"""Part of the discipline hook (moved out of discipline.py, devenv-final 40):
what a plan file says (its task, its scope, its hash), which plan a prompt names,
and the first approval store's records. Nothing here needs the session state or the
rest of the hook; discipline.py imports back, under the same names, the ones it or
another module uses through it (26 of the 38).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- plan approval scope
# Approvals outlive %TEMP% cleanup (Storage Sense) and reboots; records are HMAC-signed with a
# per-machine key so an edited or injected record never re-arms anything. Tools are kept away
# from this folder by guard.py and the settings deny rules.
APPROVALS_HOME = Path(
    os.environ.get("CLAUDE_DISCIPLINE_HOME") or (Path.home() / ".claude" / "discipline")
)
APPROVALS_FILE = APPROVALS_HOME / "approvals.json"
KEY_FILE = APPROVALS_HOME / "key"
RECOVERY_AUDIT_FILE = APPROVALS_HOME / "state-recovery.jsonl"
AUTHZ_OBSERVE_FILE = APPROVALS_HOME / "authz-observe.jsonl"
REASON_AUTHZ = (
    "[discipline] 승인된 계획의 범위 안에 있지 않은 파일은 수정할 수 없습니다({why}). "
    "/plan 으로 계획을 세우거나 갱신해 ExitPlanMode 로 사용자 승인을 받으세요. "
    "이미 승인된 진행 중 계획을 이어가는 것이라면, 사용자가 프롬프트 첫 줄에 task:<id>를 적어야 재개됩니다."
)


SIGNED_FIELDS = (
    "task_id",
    "plan_hash",
    "scope",
    "prompt_id",
    "session_id",
    "base",
    "at",
)
APPROVAL_MAX_AGE_S = 30 * 86400
MAX_APPROVALS = 50
SCOPE_HEADINGS = r"(변경 범위|Scope|Change scope)"
# P0-05: paths outside every git repository (raw data, backups) are covered only by their
# own block; its patterns are stored in the same scope list with this prefix.
DATA_SCOPE_HEADINGS = r"(데이터 변경 범위|Data [Ss]cope|Data [Cc]hange [Ss]cope)"
DATA_SCOPE = "data:"
# A list item that is a label, not a path ("- 범위 밖: ..."): a first word that is not
# plain ASCII and has no path character, further words, then a colon before any
# parenthesis. `- Makefile (note: x)` and a backticked first word stay paths.
SCOPE_LABEL = re.compile(r"^\s*[-*]\s+(?=\S*[^\x00-\x7f])[^`\s:/\\*.]+\s+[^:(\n]*:")


def normalize_plan(text: str) -> str:
    """Plan body without frontmatter, checkbox state or progress log: what the user approved."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    text = re.sub(r"^(\s*[-*]\s)\[[xX]\]", r"\1[ ]", text, flags=re.MULTILINE)
    text = re.sub(
        r"^##\s*(진행 기록|Progress)\b.*?(?=^##\s|\Z)",
        "",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def plan_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(normalize_plan(text).encode("utf-8")).hexdigest()


PLAN_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def plan_task(text: str) -> str | None:
    """The task id of a plan: the `task:` line of its frontmatter (== approvals.plan_task,
    kept here so that a hook run that needs only this does not import approvals), else
    the one in the plan's head, before its first `## ` heading (where the earlier plan
    template put it). A `task:` line further down names nothing."""
    normal = text.replace("\r\n", "\n").removeprefix("﻿")
    front = PLAN_FRONTMATTER.match(normal)
    body = normal[front.end() :] if front else normal
    head = re.split(r"^##\s", body, maxsplit=1, flags=re.MULTILINE)[0]
    for part in (front.group(1) if front else "", head):
        m = re.search(r"^task:\s*(\S+)\s*$", part, re.MULTILINE)
        if m:
            return m.group(1)
    return None


def _norm(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/").lower()


def load_key(*, create: bool) -> bytes | None:
    """HMAC key for approval records; created on first approval, never shown to tools (guard/deny)."""
    try:
        return bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        if not create:
            return None
    except (OSError, ValueError):
        # guard-b15 7: a key that is there but cannot be read (locked, garbage) is not
        # replaced: a new key would make every record signed so far unverifiable
        return None
    APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    # written whole or not at all, and only if no key appeared meanwhile: the full text
    # goes to a temp file, which becomes the key by a hard link (atomic, and refused when
    # the name is taken). approvals._atomic_write is not imported here so that a hook run
    # that needs only this module stays light.
    tmp = KEY_FILE.with_name(f"{KEY_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(key.hex(), encoding="utf-8")
    try:
        os.link(tmp, KEY_FILE)
    except FileExistsError:
        tmp.unlink(missing_ok=True)
        return load_key(create=False)  # another run made the key first: use that one
    except OSError:
        os.replace(tmp, KEY_FILE)  # a file system without hard links
        return key
    tmp.unlink(missing_ok=True)
    return key


def sign_record(record: dict[str, Any], key: bytes | None) -> str:
    import hashlib
    import hmac

    if key is None:
        return ""
    body = {k: record.get(k) for k in SIGNED_FIELDS}
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def record_is_valid(record: dict[str, Any], key: bytes | None) -> str | None:
    """None if the record is authentic and fresh, else why it must not re-arm anything."""
    import hmac

    if key is None:
        return "서명 키 없음"
    if "base" not in record:
        return "이전 형식의 승인 기록(프로젝트 경로 없음) — 다시 승인 필요"
    mac = str(record.get("mac") or "")
    if not mac or not hmac.compare_digest(mac, sign_record(record, key)):
        return "서명 불일치(기록이 위조되거나 수정됨)"
    if time.time() - float(record.get("at") or 0) > APPROVAL_MAX_AGE_S:
        return "승인 기록 만료(30일 초과)"
    return None


def _load_approvals() -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError):  # corrupt or unreadable: nothing is trusted
        return {}, "승인 기록 파일 손상"
    return (data, None) if isinstance(data, dict) else ({}, "승인 기록 파일 손상")


NO_TASK_WARNING = (
    "승인된 계획의 frontmatter에 task: 줄이 없어 승인으로 기록하지 않았습니다. "
    "frontmatter에 `task: <id>`를 넣어 계획을 다시 승인받으세요"
)


# `task:<id>`, `task_id: <id>`, `task=<id>` (optionally in backticks). ASCII ids only, so a
# Korean particle right after the id (`task:widget을`) is not part of it.
TASK_REF = re.compile(
    r"\btask(?:_id)?\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9_.-]*)", re.IGNORECASE | re.ASCII
)
# ASCII path characters only: `@docs/...` (file mention) and `계획docs/...` still yield the path,
# and a sentence period after `.md` is not part of it. Paths with spaces are not recognised.
PLAN_REF = re.compile(
    r"[A-Za-z0-9_.:/\\~-]+\.md(?![A-Za-z0-9_-])", re.IGNORECASE | re.ASCII
)
COMMAND_HEADER = re.compile(r"\A\s*<command-(?:message|name)>")
# A slash command as typed: a slash, a command word (a plugin's may hold `:`), then a blank
# or the end. A comment (`//...`), a path (`/tmp/x.md ...`) or a lone slash is not one.
TYPED_COMMAND = re.compile(r"/[A-Za-z][A-Za-z0-9_:-]*(?:\s|\Z)", re.ASCII)
# The args block counts only directly after the header tags, never later in an expanded body.
COMMAND_ARGS = re.compile(
    r"\A\s*(?:<command-(message|name)>.*?</command-\1>\s*)+<command-args>(.*?)</command-args>",
    re.DOTALL,
)
RELAY_HEADERS = ("another claude session", "[system notification")
AMBIGUOUS = "재무장 후보가 여러 개"
NOTE = "참고:"  # a warning that informs (another named plan was refused) without demanding anything


def naming_text(prompt: str) -> str:
    """The part of a prompt allowed to name a task. A slash command (the prompt starts with the
    harness's <command-...> header) names it in the <command-args> block right after the header
    only; any other prompt in its first non-empty line only, and not at all when that line is
    markup, a harness note in brackets (`[Image: ...]`, `[Usage limit ...]`) or a relay header.
    Pasted documents and relayed text below the first line never re-arm an approval."""
    if COMMAND_HEADER.match(prompt):
        args = COMMAND_ARGS.match(prompt)
        return args.group(2) if args else ""
    first = next((line for line in prompt.splitlines() if line.strip()), "").strip()
    if first.startswith(("<", "[")) or first.lower().startswith(RELAY_HEADERS):
        return ""
    return first


def prompt_tasks(text: str) -> set[str]:
    """Task ids named explicitly; only these may re-arm an earlier approval."""
    return {m.group(1).rstrip(".").lower() for m in TASK_REF.finditer(text)}


def leading_task(text: str) -> set[str]:
    """The task id a line starts with (`task:<id>`, words may follow), as a set of none or
    one. A line that only mentions a task ("do NOT go on with task:x yet", "the reviewer
    said: task:x approved") names none: since 2026-11-25 a prompt the user typed continues
    an approval only by starting with the task."""
    start = text.lstrip(" \t﻿\u200b").removeprefix("`")
    m = TASK_REF.match(start)
    return {m.group(1).rstrip(".").lower()} if m else set()


def names_plan(text: str, plan: Path, root: str, cwd: str) -> bool:
    """True when `text` names this plan file: by its exact file name, or by a path that
    resolves (relative to `cwd` or the git `root`) to it. A same-named file in another project
    does not count."""
    want = os.path.normcase(str(plan))
    for ref in PLAN_REF.findall(text):
        if "/" in ref or "\\" in ref:
            bases = [""] if os.path.isabs(ref) else [cwd, root]
            if any(
                os.path.normcase(os.path.abspath(os.path.join(b, ref))) == want
                for b in bases
            ):
                return True
        elif ref.lower() == plan.name.lower():
            return True
    return False


def rearm_approval(
    cwd: str, prompt: str = ""
) -> tuple[dict[str, Any] | None, str | None]:
    """(approval, warning): an approval survives into a new prompt or session only when the
    prompt's first line (or slash-command arguments) names its task (`task:<id>`) or its plan
    file, the plan is still in progress in the git root the prompt runs in (never a parent or
    another project), and the record is authentic (HMAC) and fresh. Two named plans that could
    both re-arm -> none (ambiguous)."""
    root, named = named_plans(cwd, prompt)
    if not named:
        return None, None  # nothing named, or the named work has no plan here
    approvals, problem = _load_approvals()
    if not approvals:
        return None, problem
    key = load_key(create=False)
    found: list[dict[str, Any]] = []
    for plan, text, task in named:
        fm = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
        in_progress = bool(
            fm and re.search(r"^status:\s*in-progress\s*$", fm.group(1), re.MULTILINE)
        )
        unchecked = re.search(r"^\s*[-*]\s\[ \]", text, re.MULTILINE)
        if not (in_progress and unchecked):
            continue
        record = approvals.get(plan_hash(text))
        if not (
            isinstance(record, dict)
            and record.get("task_id")
            and record.get("task_id") == task
        ):
            continue
        invalid = record_is_valid(record, key)
        if not invalid and os.path.normcase(str(record.get("base") or "")) != root:
            invalid = "승인 당시와 다른 git 루트(프로젝트)"
        if invalid:
            # Keep looking (another plan may hold a valid approval) but report the first.
            problem = problem or f"{invalid}: {plan.name}"
            continue
        found.append({**record, "rearmed_from": str(plan)})
    if len(found) > 1:
        names = ", ".join(str(f.get("task_id")) for f in found)
        return None, f"{AMBIGUOUS}({names}) — task:<id> 하나만 지정 필요"
    if (
        found
    ):  # another named plan's record was refused: say so, without demanding anything
        return found[0], (f"{NOTE} {problem}" if problem else None)
    return None, problem


def named_plans(
    cwd: str, prompt: str
) -> tuple[str, list[tuple[Path, str, str | None]]]:
    """(git root, [(plan file, text, task)]) of the plans in this root's docs/plans that the
    prompt names. A slash command names them in its arguments, by task id or by plan file
    (`/implement docs/plans/x.md`); any other prompt only by starting its first line with
    `task:<id>`: a task mentioned later in the line, or a plan file's name, names nothing."""
    import worktree

    naming = naming_text(prompt)
    # a slash command: expanded by the harness (the <command-...> header) or as typed
    command = bool(COMMAND_HEADER.match(prompt) or TYPED_COMMAND.match(naming))
    # A line that starts with a task names every task on it (two of them stay ambiguous, as
    # before); a line that does not start with one names none.
    tasks = prompt_tasks(naming) if command or leading_task(naming) else set()
    if not tasks and not (command and PLAN_REF.search(naming)):
        return "", []  # nothing named: no git call needed
    root = os.path.normcase(worktree.toplevel(cwd) or os.path.abspath(cwd))
    plans_dir = Path(root) / "docs" / "plans"
    named: list[tuple[Path, str, str | None]] = []
    for plan in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        try:
            text = plan.read_text(encoding="utf-8")
        except (OSError, ValueError):  # unreadable or not UTF-8: skip it
            continue
        task = plan_task(text)
        if (task and task.lower() in tasks) or (
            command and names_plan(naming, plan, root, os.path.abspath(cwd))
        ):
            named.append((plan, text, task))
    return root, named


def active_approval(state: dict[str, Any], prompt_id: object) -> dict[str, Any] | None:
    approval = state.get("approval")
    if not isinstance(approval, dict):
        return None
    if (
        approval.get("prompt_id") == prompt_id
    ):  # re-armed approvals carry the prompt they serve
        return approval
    return None  # approved in an earlier prompt and not re-armed for this one


def path_class(real_path: str, base: str, in_repo: bool) -> str:
    """What kind of path an approval given in `base` (a git root) is asked to cover:
    "plan" (inside base/docs/plans), "code" (elsewhere inside base), "external" (outside
    base, inside or possibly inside another repository) or "data" (outside base and
    certainly in no repository: nothing tracks its changes)."""
    target, root = _norm(real_path), _norm(base)
    if target.startswith(root + "/docs/plans/"):
        return "plan"
    if (
        not in_repo
    ):  # also inside a base that is no repository (a session started there)
        return "data"
    inside = target == root or target.startswith(root + "/")
    return "code" if inside else "external"


def plain_path(path: str) -> str:
    r"""A Windows path without the `\\?\` long-path prefix (`\\?\UNC\server` -> `\\server`),
    so the same file is classified the same way however it was spelled."""
    if path.startswith(("\\\\?\\UNC\\", "//?/UNC/")):
        return "\\\\" + path[8:]
    if path.startswith(("\\\\?\\", "//?/")):
        return path[4:]
    return path
