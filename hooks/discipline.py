"""Engineering-discipline hooks (one script, several events).

  discipline.py pre     PreToolUse  Edit|Write|MultiEdit|Bash|PowerShell
                        - test weakening / suppression  -> "ask" the user with the reason
                        - >MAX_FILES files edited this turn without an approved plan -> "deny" (plan first)
  discipline.py post    PostToolUse Edit|Write|MultiEdit|Bash|PowerShell|Agent|Task|ExitPlanMode
                        - records code edits, successful verification commands, plan approval
  discipline.py fail    PostToolUseFailure Bash|PowerShell|Agent|Task
                        - records failed verification commands (an attempt, with last_verify_ok=False)
  discipline.py prompt  UserPromptSubmit - starts a new turn (resets the per-turn file count)
  discipline.py stop    Stop - blocks ending the turn when code was edited after the last verification,
                        or once per failure when the last verification failed (report it honestly)

State: %TEMP%/claude-discipline/<session_id>.json
notify.py imports session_key/load_state/stop_block_reason so it never toasts on a blocked Stop.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# python -I drops the script dir; verification.py and shellparse.py live next to this file
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


def temp_root() -> Path:
    """Same lookup order as tempfile.gettempdir(), without its ~90 ms import."""
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value)
    import tempfile  # fallback only; slow import

    return Path(tempfile.gettempdir())


MAX_FILES_WITHOUT_PLAN = int(os.environ.get("CLAUDE_MAX_FILES_WITHOUT_PLAN", "5"))
STATE_DIR = temp_root() / "claude-discipline"

CODE_EXT = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ps1",
    ".psm1",
    ".sh",
    ".sql",
    ".go",
    ".rs",
    ".cs",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".cpp",
    ".h",
}
# Files that never count toward the per-turn limit or the verification requirement.
IGNORED_PATH = re.compile(
    r"([\\/]AppData[\\/]Local[\\/]Temp[\\/])|([\\/]\.claude[\\/](projects|plans|todos)[\\/])|([\\/]memory[\\/][^\\/]+\.md$)",
    re.IGNORECASE,
)
TEST_PATH = re.compile(
    r"(^|[\\/])(tests?|__tests__|spec)([\\/])|(_test|\.test|\.spec|Tests?)\.[a-z0-9]+$|(^|[\\/])test_[^\\/]+$",
    re.IGNORECASE,
)

# Verification is judged by hooks/verification.py (declared project gate, real runners,
# attributable exit status). Agents' own claims never count: a subagent's Bash runs reach
# these hooks directly (with agent_id), and code-reviewer is recorded only as a review.
REVIEW_AGENTS = {"code-reviewer"}

# (label, regex) - counted in old vs new content; an increase is "weakening".
SUPPRESSIONS = [
    ("type: ignore", r"#\s*type:\s*ignore"),
    ("pyright: ignore", r"#\s*pyright:\s*ignore"),
    ("noqa", r"#\s*noqa\b"),
    ("pragma: no cover", r"#\s*pragma:\s*no\s*cover"),
    ("eslint-disable", r"eslint-disable"),
    ("@ts-ignore", r"@ts-ignore"),
    ("@ts-expect-error", r"@ts-expect-error"),
    ("@ts-nocheck", r"@ts-nocheck"),
    (
        "SuppressMessage",
        r"SuppressMessageAttribute|\[Diagnostics\.CodeAnalysis\.SuppressMessage",
    ),
]
TEST_SKIPS = [
    (
        "pytest skip/xfail",
        r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\(",
    ),
    ("unittest skip", r"@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b"),
    (
        "js skip/todo",
        r"\b(it|test|describe|suite)\.(skip|todo)\b|\b(xit|xtest|xdescribe)\(",
    ),
    ("js only (focus)", r"\b(it|test|describe|suite)\.only\b|\b(fit|fdescribe)\("),
    ("Pester skip", r"\s-Skip\b|Set-ItResult\s+-(Skipped|Inconclusive|Pending)"),
]
ASSERTIONS = r"\bassert\b|\bexpect\(|\bShould\s+-|\bassert[A-Z]\w*\(|\.toBe|\.toEqual|\.toThrow|pytest\.raises"
DELETE_CMD = re.compile(r"\b(rm|del|erase|Remove-Item|ri|git\s+rm)\b", re.IGNORECASE)


# ---------------------------------------------------------------- state
def default_state() -> dict[str, Any]:
    return {
        "turn": 0,
        "turn_files": [],
        "last_code_edit": 0.0,
        # project root ("" = no declared policy) -> {"at", "gate", "ci", "validate"} of the last code edit
        "dirty": {},
        # project root -> {"at", "outcome", "kind", "cmd", "why"} of the last verification evidence
        "evidence": {},
        # "<root>|<evidence at>" -> turn in which Stop was blocked for that failed/unknown result
        "notified": {},
        "last_review": 0.0,
        # plan approval: {task_id, plan_hash, scope, prompt_id[, rearmed_from]} or None
        "approval": None,
    }


def session_key(data: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(data.get("session_id", "default")))


def load_state(session: str) -> dict[str, Any]:
    STATE_DIR.mkdir(exist_ok=True)
    p = STATE_DIR / f"{session}.json"
    try:
        saved = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or corrupt state file: start from defaults
        saved = {}
    state = default_state()
    if isinstance(saved, dict):
        state.update(saved)
    return state


def save_state(session: str, state: dict[str, Any]) -> None:
    (STATE_DIR / f"{session}.json").write_text(json.dumps(state), encoding="utf-8")


# ---------------------------------------------------------------- helpers
def edit_pairs(tool: str, ti: dict[str, Any]) -> tuple[str, str]:
    """Return (old_text, new_text) for an edit tool call."""
    if tool == "Edit":
        return ti.get("old_string", ""), ti.get("new_string", "")
    if tool == "MultiEdit":
        edits = ti.get("edits", []) or []
        return "\n".join(e.get("old_string", "") for e in edits), "\n".join(
            e.get("new_string", "") for e in edits
        )
    if tool == "Write":
        old = ""
        try:
            old = Path(ti.get("file_path", "")).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            pass
        return old, ti.get("content", "")
    return "", ""


def count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE | re.MULTILINE))


def weakening_findings(path: str, old: str, new: str) -> list[str]:
    found: list[str] = []
    for label, pat in SUPPRESSIONS:
        delta = count(pat, new) - count(pat, old)
        if delta > 0:
            found.append(f"+{delta} `{label}` suppression")
    if TEST_PATH.search(path):
        for label, pat in TEST_SKIPS:
            delta = count(pat, new) - count(pat, old)
            if delta > 0:
                found.append(f"+{delta} {label}")
        removed = count(ASSERTIONS, old) - count(ASSERTIONS, new)
        if old and removed > 0:
            found.append(f"-{removed} assertion(s) removed from a test file")
    return found


def deleted_test_files(cmd: str) -> list[str]:
    if not DELETE_CMD.search(cmd):
        return []
    return [
        t
        for t in re.split(r"[\s'\"]+", cmd)
        if t and TEST_PATH.search(t) and "." in Path(t).name
    ]


def emit_pre(decision: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


# ---------------------------------------------------------------- events
def on_pre(data: dict[str, Any], state: dict[str, Any]) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if tool in ("Bash", "PowerShell"):
        tests = deleted_test_files(ti.get("command", ""))
        if tests:
            emit_pre(
                "ask",
                f"[discipline] 테스트 파일 삭제 감지: {', '.join(tests)}. 의도한 삭제인지 확인해 주세요.",
            )
        return

    path = ti.get("file_path", "")
    if not path or IGNORED_PATH.search(path):
        return

    # 1) Large change without an approved plan covering it.
    approval = active_approval(state, data.get("prompt_id"))
    counted = [f for f in state["turn_files"] if not in_approved_scope(approval, f)]
    if (
        path not in state["turn_files"]
        and not in_approved_scope(approval, path)
        and len(counted) >= MAX_FILES_WITHOUT_PLAN
    ):
        scope_note = (
            f" 승인된 계획(task: {approval.get('task_id')})의 변경 범위 밖 파일입니다."
            if approval
            else ""
        )
        emit_pre(
            "deny",
            f"[discipline] 이번 턴에 승인 범위 밖 파일을 이미 {len(counted)}개 수정했습니다(한도 {MAX_FILES_WITHOUT_PLAN}).{scope_note} "
            "더 진행하기 전에 /plan 으로 계획을 세우거나 갱신하고 ExitPlanMode 로 사용자 승인을 받으세요.",
        )

    # 2) Test weakening / suppressions.
    old, new = edit_pairs(tool, ti)
    findings = weakening_findings(path, old, new)
    if findings:
        emit_pre(
            "ask",
            f"[discipline] 안전장치 약화 감지 ({Path(path).name}): "
            + "; ".join(findings)
            + ". 사유가 정당한지 확인 후 승인해 주세요 (Claude는 사유를 코드 주석에 남겨야 합니다).",
        )


def on_post(data: dict[str, Any], state: dict[str, Any]) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    now = time.time()
    if tool in ("Edit", "Write", "MultiEdit"):
        path = ti.get("file_path", "")
        if path and not IGNORED_PATH.search(path):
            if path not in state["turn_files"]:
                state["turn_files"].append(path)
            if Path(path).suffix.lower() in CODE_EXT:
                state["last_code_edit"] = now
                mark_dirty(state, path, now)
    elif tool in ("Bash", "PowerShell"):
        record_evidence(data, state, failed=False)
    elif tool in ("Agent", "Task"):
        if ti.get("subagent_type") in REVIEW_AGENTS:
            state["last_review"] = now  # a review, not verification
    elif tool == "ExitPlanMode":
        # PostToolUse only fires once the user approved the plan.
        record_approval(data, state)


# ---------------------------------------------------------------- plan approval scope
APPROVALS_FILE = STATE_DIR / "approvals.json"
MAX_APPROVALS = 50
SCOPE_HEADINGS = r"(변경 범위|Scope|Change scope)"


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


def plan_task(text: str) -> str | None:
    m = re.search(r"^task:\s*(\S+)\s*$", text, re.MULTILINE)
    return m.group(1) if m else None


def plan_scope(text: str, base: str) -> list[str]:
    """Glob patterns under `## 변경 범위`, made absolute against `base` (the approving cwd)."""
    m = re.search(
        rf"^##\s*{SCOPE_HEADINGS}\b[^\n]*\n(.*?)(?=^##\s|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    globs: list[str] = []
    for line in m.group(2).splitlines():
        item = re.match(r"^\s*[-*]\s+`?([^`\s(]+)`?", line)
        if item:
            g = item.group(1)
            absolute = re.match(r"^[A-Za-z]:[\\/]", g) or g.startswith(("/", "~"))
            g = os.path.expanduser(g) if g.startswith("~") else g
            globs.append(_norm(g if absolute else os.path.join(base, g)))
    return globs


def _norm(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/").lower()


def _load_approvals() -> dict[str, Any]:
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or corrupt: no persisted approvals
        return {}
    return data if isinstance(data, dict) else {}


def record_approval(data: dict[str, Any], state: dict[str, Any]) -> None:
    response = data.get("tool_response")
    response = response if isinstance(response, dict) else {}
    text = response.get("plan") if isinstance(response.get("plan"), str) else None
    if text is None and isinstance(response.get("filePath"), str):
        try:
            text = Path(response["filePath"]).read_text(encoding="utf-8")
        except OSError:  # plan file gone: approval stays prompt-only
            text = None
    cwd = str(data.get("cwd") or os.getcwd())
    record = {
        "task_id": plan_task(text) if text else None,
        "plan_hash": plan_hash(text) if text else None,
        "scope": plan_scope(text, cwd) if text else [],
        "prompt_id": data.get("prompt_id"),
        "session_id": data.get("session_id"),
        "at": time.time(),
    }
    state["approval"] = record
    if record["task_id"] and record["plan_hash"]:
        approvals = _load_approvals()
        approvals[record["plan_hash"]] = record
        newest = sorted(approvals.items(), key=lambda kv: float(kv[1].get("at", 0)))[
            -MAX_APPROVALS:
        ]
        APPROVALS_FILE.write_text(json.dumps(dict(newest)), encoding="utf-8")


def rearm_approval(cwd: str) -> dict[str, Any] | None:
    """An approval survives into a new prompt or session only for the same, still in-progress plan."""
    approvals = _load_approvals()
    if not approvals:
        return None
    d = os.path.abspath(cwd)
    for _ in range(6):
        plans_dir = Path(d) / "docs" / "plans"
        if plans_dir.is_dir():
            for plan in sorted(plans_dir.glob("*.md")):
                try:
                    text = plan.read_text(encoding="utf-8")
                except OSError:  # unreadable plan: skip it
                    continue
                fm = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
                in_progress = bool(
                    fm
                    and re.search(
                        r"^status:\s*in-progress\s*$", fm.group(1), re.MULTILINE
                    )
                )
                unchecked = re.search(r"^\s*[-*]\s\[ \]", text, re.MULTILINE)
                if not (in_progress and unchecked):
                    continue
                record = approvals.get(plan_hash(text))
                if (
                    record
                    and record.get("task_id")
                    and record.get("task_id") == plan_task(text)
                ):
                    return {**record, "rearmed_from": str(plan)}
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def active_approval(state: dict[str, Any], prompt_id: object) -> dict[str, Any] | None:
    approval = state.get("approval")
    if not isinstance(approval, dict):
        return None
    if approval.get("rearmed_from") or approval.get("prompt_id") == prompt_id:
        return approval
    return None  # approved in an earlier prompt and not re-armed for this one


def in_approved_scope(approval: dict[str, Any] | None, path: str) -> bool:
    import fnmatch

    if approval is None:
        return False
    scope = approval.get("scope") or []
    if not scope:
        return not approval.get(
            "rearmed_from"
        )  # unscoped approval covers only its own prompt
    target = _norm(path)
    if "/docs/plans/" in target:
        return True  # keeping the plan file up to date is part of the approved work
    return any(fnmatch.fnmatch(target, g) or target == g for g in scope)


MAX_DIRTY_FILES = 200


def mark_dirty(state: dict[str, Any], path: str, at: float) -> None:
    """Group an edited code file under its project's verification policy (nearest declaration)."""
    from verification import find_policy

    policy = find_policy(path)
    entry = state["dirty"].setdefault(policy["root"], {"files": []})
    entry.update(
        {
            "at": max(at, float(entry.get("at", 0))),
            "gate": policy["gate"],
            "ci": policy["ci"],
            "validate": policy["validate"],
        }
    )
    files = entry.setdefault("files", [])
    if path not in files and len(files) < MAX_DIRTY_FILES:
        files.append(path)


def reresolve_dirty(state: dict[str, Any]) -> None:
    """Policies can be declared or changed after an edit: regroup edited files by their current policy."""
    old = state["dirty"]
    state["dirty"] = {}
    for entry in old.values():
        files = entry.get("files") or []
        if not files:
            continue  # state written by an older hook version: nothing to regroup
        for f in files:
            mark_dirty(state, f, float(entry.get("at", 0)))


def record_evidence(
    data: dict[str, Any], state: dict[str, Any], *, failed: bool
) -> None:
    """Attach what this shell command proves (if anything) to each project with unverified edits."""
    from verification import Policy, evaluate

    reresolve_dirty(state)

    ti = data.get("tool_input", {}) or {}
    response = data.get("tool_response")
    background = bool(ti.get("run_in_background")) or (
        isinstance(response, dict) and bool(response.get("backgroundTaskId"))
    )
    is_ps = data.get("tool_name") == "PowerShell"
    cwd = str(data.get("cwd") or os.getcwd())
    now = time.time()
    for root, entry in state["dirty"].items():
        policy: Policy = {
            "root": root,
            "gate": entry.get("gate"),
            "ci": entry.get("ci", "undeclared"),
            "validate": entry.get("validate"),
        }
        ev = evaluate(
            str(ti.get("command", "")),
            "powershell" if is_ps else "bash",
            cwd,
            policy,
            failed=failed,
            background=background,
        )
        if ev is None:
            continue
        old = state["evidence"].get(root) or {}
        still_valid_pass = old.get("outcome") == "passed" and float(
            old.get("at", 0)
        ) >= float(entry.get("at", 0))
        if still_valid_pass and ev["outcome"] in ("partial", "unknown"):
            continue  # a later side run does not revoke a passed gate for unchanged code
        state["evidence"][root] = {**ev, "at": now}
    record_ci_activity(str(ti.get("command", "")), is_ps, cwd, state, failed=failed)


def record_ci_activity(
    command: str, is_ps: bool, cwd: str, state: dict[str, Any], *, failed: bool
) -> None:
    """`git push` records what was pushed; `gh run watch|view|list` re-reads CI from the API."""
    from shellparse import ParseError, parse, program_and_args
    from verification import cwd_at

    try:
        cmds = [
            c for c in parse(command, "powershell" if is_ps else "bash") if not c.nested
        ]
    except ParseError:
        return
    now = time.time()
    for i, cmd in enumerate(cmds):
        program, args = program_and_args(cmd)
        if (
            program == "git"
            and "push" in args[:3]
            and not failed
            and not {"--dry-run", "-n"} & set(args)
        ):
            from ci_status import push_info

            info = push_info(cwd_at(cmds, i, cwd))
            if info:
                state.setdefault("pushes", {})[info["toplevel"]] = {**info, "at": now}
                state.setdefault("ci", {}).pop(info["toplevel"], None)
        elif (
            program == "gh"
            and args[:1] == ["run"]
            and args[1:2]
            and args[1] in ("watch", "view", "list")
        ):
            from ci_status import fetch_ci

            for top, push in state.get("pushes", {}).items():
                state.setdefault("ci", {})[top] = {**fetch_ci(push), "at": now}


def on_fail(data: dict[str, Any], state: dict[str, Any]) -> None:
    """PostToolUseFailure: a failed check is still evidence (of failure)."""
    if data.get("tool_name") in ("Bash", "PowerShell"):
        record_evidence(data, state, failed=True)


def on_prompt(data: dict[str, Any], state: dict[str, Any]) -> None:
    state["turn"] = int(state.get("turn", 0)) + 1
    state["turn_files"] = []
    # A new prompt never inherits the previous approval; only the same in-progress plan re-arms it.
    state["approval"] = rearm_approval(str(data.get("cwd") or os.getcwd()))


REASON_UNVERIFIED = (
    "[discipline] 마지막 코드 수정 이후 검증(테스트/타입체크/린트/실행)을 실행하지 않았습니다. "
    "프로젝트의 품질 게이트를 실행하고 결과를 보고하세요. "
    "검증이 불가능한 변경이면 그 이유를 '미확인'으로 명시하세요."
)
REASON_FAILED = (
    "[discipline] 마지막 검증이 실패한 상태로 종료하려 합니다. "
    "고칠 수 있으면 고치고 다시 검증하세요. 아니면 실패한 명령, 실패 내용, 원인 추정을 보고의 "
    "'미완료·리스크'에 그대로 적고 종료하세요(이미 그렇게 보고했다면 바로 종료해도 됩니다)."
)
REASON_UNKNOWN = (
    "[discipline] 마지막 검증의 결과를 확인할 수 없습니다({why}). 종료 코드가 가려지면 실패해도 성공처럼 "
    "보입니다. 검증 명령을 단독으로(또는 &&로만 이어서) 포그라운드에서 다시 실행하거나, 결과를 확인할 수 "
    "없었다는 사실을 '미완료·리스크'에 적고 종료하세요."
)
REASON_NOT_PUSHED = (
    "[discipline] {root}의 검증 정책은 `ci: required`인데, 이번 수정분이 push되지 않아 CI 검증이 없습니다"
    "(현재 상태: Locally Verified). push가 사용자 승인 대기라면 보고에 'CI 미검증(push 전)'으로 명시하고 종료하세요."
)
REASON_CI_UNCHECKED = (
    "[discipline] {ref} push 이후 CI 결과를 확인하지 않았습니다. `gh run watch <run id> --exit-status`로 "
    "확인하세요. 판정은 명령 출력이 아니라 GitHub API의 repository·branch·workflow·headSha·job 결과로 합니다."
)
REASON_CI_FAILED = (
    "[discipline] {ref}의 CI가 실패했습니다({detail}). 고친 뒤 다시 push해 확인하거나, 실패한 run과 원인을 "
    "'미완료·리스크'에 그대로 보고하세요."
)
REASON_CI_UNKNOWN = (
    "[discipline] {ref}의 CI 결과를 확정할 수 없습니다({detail}). 실행이 끝난 뒤 다시 확인하거나 "
    "'CI 미확인'으로 보고하세요."
)
REASON_GATE_NOT_RUN = (
    "[discipline] {root}에 선언된 품질 게이트(`{gate}`)가 마지막 수정 이후 통과하지 않았습니다"
    "(다른 테스트 명령만 실행됨). 게이트를 그대로 실행하거나, 실행하지 못한 이유를 '미확인'으로 보고하세요."
)


def _stop_decision(state: dict[str, Any]) -> tuple[str, str | None] | None:
    """(reason, notify key) for the first project whose edits lack passing evidence."""
    for root, entry in sorted(state.get("dirty", {}).items()):
        edited_at = float(entry.get("at", 0))
        if edited_at <= 0:
            continue
        where = f" (프로젝트: {root})" if root else ""
        ev = state.get("evidence", {}).get(root) or {}
        ev_at = float(ev.get("at", 0))
        if ev_at < edited_at:
            return REASON_UNVERIFIED + where, None
        outcome = ev.get("outcome")
        if outcome == "partial":
            return REASON_GATE_NOT_RUN.format(root=root, gate=entry.get("gate")), None
        if outcome in ("failed", "unknown"):
            key = f"{root}|{ev_at}"
            if _not_yet_notified(state, key):
                reason = (
                    REASON_FAILED
                    if outcome == "failed"
                    else REASON_UNKNOWN.format(why=ev.get("why", ""))
                )
                return reason + where, key
            continue
        if entry.get("ci") == "required":
            decision = _ci_decision(state, root, edited_at)
            if decision:
                return decision
    return None


def _not_yet_notified(state: dict[str, Any], key: str) -> bool:
    # Block once per result; again only within the same turn (the parallel notify hook
    # may read the state after discipline wrote the flag).
    turn = state.get("notified", {}).get(key)
    return turn is None or turn == state.get("turn")


def _inside(path: str, root: str) -> bool:
    p, r = (
        os.path.normcase(os.path.normpath(path)),
        os.path.normcase(os.path.normpath(root)),
    )
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _ci_decision(
    state: dict[str, Any], root: str, edited_at: float
) -> tuple[str, str] | None:
    """CI half of the completion check for a project whose policy says `ci: required`."""
    pushes = [
        (top, p)
        for top, p in state.get("pushes", {}).items()
        if _inside(root, top)
        and float(p.get("at", 0)) >= edited_at
        and not any(_inside(u, root) for u in p.get("uncommitted", []))
    ]
    if not pushes:
        key = f"{root}|nopush|{edited_at}"
        return (
            (REASON_NOT_PUSHED.format(root=root), key)
            if _not_yet_notified(state, key)
            else None
        )
    top, push = max(pushes, key=lambda tp: float(tp[1].get("at", 0)))
    ci = state.get("ci", {}).get(top) or {}
    ref = f"{push.get('repo')}@{str(push.get('sha', ''))[:7]}"
    if float(ci.get("at", 0)) < float(push.get("at", 0)) or ci.get("sha") != push.get(
        "sha"
    ):
        key = f"{root}|unchecked|{push.get('sha')}"
        return (
            (REASON_CI_UNCHECKED.format(ref=ref), key)
            if _not_yet_notified(state, key)
            else None
        )
    status = ci.get("status")
    if status == "verified":
        return None
    key = f"{root}|ci|{push.get('sha')}|{status}|{ci.get('why')}"
    if not _not_yet_notified(state, key):
        return None
    runs = ", ".join(
        f"run {r.get('id')} {r.get('conclusion') or r.get('status')}"
        for r in ci.get("runs", [])
    )
    detail = "; ".join(x for x in (runs, str(ci.get("why") or "")) if x)
    template = REASON_CI_FAILED if status == "failed" else REASON_CI_UNKNOWN
    return template.format(ref=ref, detail=detail), key


def stop_block_reason(state: dict[str, Any], stop_hook_active: bool) -> str | None:
    """Pure decision shared with notify.py: why this Stop is blocked, or None."""
    if stop_hook_active:
        return None  # already blocked once this turn; let Claude stop
    decision = _stop_decision(state)
    return decision[0] if decision else None


def on_stop(data: dict[str, Any], state: dict[str, Any]) -> None:
    if data.get("stop_hook_active"):
        return
    decision = _stop_decision(state)
    if decision is None:
        return
    reason, notify_key = decision
    if notify_key:
        state.setdefault("notified", {})[notify_key] = state.get("turn")
    print(json.dumps({"decision": "block", "reason": reason}))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (
        ValueError,
        OSError,
    ):  # malformed/empty payload: never block on hook input problems
        sys.exit(0)
    session = session_key(data)
    state = load_state(session)
    handler = {
        "pre": on_pre,
        "post": on_post,
        "fail": on_fail,
        "prompt": on_prompt,
        "stop": on_stop,
    }.get(mode)
    if handler is None:
        sys.exit(0)
    try:
        handler(data, state)
    finally:
        save_state(session, state)


if __name__ == "__main__":
    main()
