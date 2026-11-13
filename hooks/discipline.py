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
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

_T0 = time.monotonic()  # earliest point of this hook run: every budget counts from here

# stderr only (a successful hook's stderr is not shown as a result); never secrets.
log = logging.getLogger("discipline")

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
# Files that never count toward the per-turn limit or the verification requirement: Claude's
# own transcripts/plans/todos/memory, and scratch files under %TEMP% (see ignored_path: a git
# working tree under %TEMP%, such as a scratch worktree, is code like any other repository).
IGNORED_PATH = re.compile(
    r"([\\/]AppData[\\/]Local[\\/]Temp[\\/])|([\\/]\.claude[\\/](projects|plans|todos)[\\/])|([\\/]memory[\\/][^\\/]+\.md$)",
    re.IGNORECASE,
)
CLAUDE_OWN_PATH = re.compile(
    r"([\\/]\.claude[\\/](projects|plans|todos)[\\/])|([\\/]memory[\\/][^\\/]+\.md$)",
    re.IGNORECASE,
)


def absolute(path: str, cwd: str) -> str:
    """A tool path as the tool resolves it (relative to the session's cwd)."""
    return os.path.abspath(path if os.path.isabs(path) else os.path.join(cwd, path))


def is_unc(path: str) -> bool:
    return os.path.splitdrive(path)[0].startswith(("\\\\", "//"))


def may_be_repository(path: str) -> bool:
    """True unless it is certain that no git working tree contains `path`: any `.git` entry
    (directory, worktree file, even corrupt) in it or an ancestor, a UNC path (git refuses or
    misjudges those), or a folder that cannot be inspected. Never asks git, so a git error is
    never read as "not a repository", and nothing is cached (a later `git init` counts)."""
    if is_unc(path):
        return True
    d = path
    while True:
        try:
            os.lstat(os.path.join(d, ".git"))
            return True  # any entry named .git: possibly a repository
        except (FileNotFoundError, NotADirectoryError):
            pass  # certainly no .git here
        except (
            OSError,
            ValueError,
        ):  # cannot be inspected (access, NUL, ...): not certain
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def ignored_path(state: dict[str, Any], path: str, cwd: str) -> bool:
    """Out of scope only when both the path as given and its real (link-resolved) path are
    ignorable, and a %TEMP% path is certainly not inside a git working tree (a scratch
    worktree is code like any repository)."""
    given = absolute(path, cwd)
    if is_unc(given) or not (
        CLAUDE_OWN_PATH.search(given) or IGNORED_PATH.search(given)
    ):
        return False  # decided without any I/O (a stalled share must not time the hook out)
    for p in (given, os.path.realpath(given)):
        if CLAUDE_OWN_PATH.search(p):
            continue
        if not IGNORED_PATH.search(p):
            return False  # a link must not move a protected file out of scope
        if may_be_repository(p):
            return False
    return True


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
    import session_store

    session_store.save(STATE_DIR / f"{session}.json", state)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def state_reset_active(state: dict[str, Any]) -> str | None:
    """Why the session's tracking state is untrusted (damaged or missed events), until the
    declared gate of every root in the marker's scope passed after that moment; None once
    cleared or if never reset. An empty scope (nothing gated to wait for) stays active
    until on_stop has reported it once. Never raises on malformed values."""
    reset = state.get("state_reset")
    if reset is None:
        return None
    if not isinstance(reset, dict):
        return "세션 추적 상태 표식 손상"
    since = _number(reset.get("at"))
    if since is None:
        return "세션 추적 상태 표식 손상(시각)"
    roots = reset.get("roots")
    evidence = state.get("evidence")
    # Compared case-insensitively on Windows: an unresolvable place is recorded normcased,
    # and once its CLAUDE.md is repaired its gate evidence is keyed by the policy root.
    by_root = {
        os.path.normcase(str(k)): v
        for k, v in (evidence if isinstance(evidence, dict) else {}).items()
    }

    def gate_passed_since(root: object) -> bool:
        ev = by_root.get(os.path.normcase(root)) if isinstance(root, str) else None
        if not isinstance(ev, dict):
            return False
        at = _number(ev.get("at"))
        return (
            ev.get("kind") == "gate"
            and ev.get("outcome") == "passed"
            and at is not None
            and at > since
        )

    if isinstance(roots, list) and roots and all(map(gate_passed_since, roots)):
        state.pop("state_reset", None)
        return None
    return str(reset.get("why") or "원인 미상")


def reset_scope(saved: dict[str, Any] | None, cwd: str) -> tuple[list[str], list[str]]:
    """(roots, problems): roots with a declared gate whose content a reset makes unverified
    (those the readable previous state knew - dirty, touched repositories - and the policy
    root of this event). A place whose policy cannot be read is itself a root (no evidence
    is ever recorded for it, so the marker stays until the user resolves it)."""
    from verification import find_policy

    roots: set[str] = set()
    problems: list[str] = []
    if saved:
        dirty = saved.get("dirty")
        for root, entry in (dirty if isinstance(dirty, dict) else {}).items():
            if root and isinstance(entry, dict) and entry.get("gate"):
                roots.add(str(root))
        tops = saved.get("touched_tops")
        places = [
            t for t in (tops if isinstance(tops, list) else []) if isinstance(t, str)
        ]
    else:
        places = []
    for place in [*places, cwd]:
        try:
            policy = find_policy(os.path.join(place, "_"))
        except (OSError, ValueError) as e:  # e.g. a CLAUDE.md that is not UTF-8
            roots.add(unresolved_root(place))
            problems.append(f"범위 확인 실패({type(e).__name__}): {place}")
            continue
        if policy["root"] and policy["gate"]:
            roots.add(policy["root"])
    return sorted(roots), problems


def unresolved_root(place: str) -> str:
    return os.path.normcase(os.path.abspath(place))


def mark_reset(
    state: dict[str, Any], saved: dict[str, Any] | None, why: list[str], cwd: str
) -> None:
    """Record (or extend) the reset marker. It is written before the scope is computed, with
    the event's place as an unresolvable root, so a failure while computing it still leaves
    a marker that blocks (fail closed) instead of none."""
    previous_roots: set[str] = set()
    previous_why = ""
    if state_reset_active(state) is not None:  # an earlier marker that is still open
        previous = state.get("state_reset") or {}
        previous_roots = {r for r in previous.get("roots") or [] if isinstance(r, str)}
        previous_why = str(previous.get("why") or "")

    def record(roots: set[str], reasons: list[str]) -> None:
        state["state_reset"] = {
            "at": time.time(),
            # newest first, so truncation drops the oldest reasons
            "why": "; ".join(w for w in [*reasons, previous_why] if w)[:300],
            "roots": sorted(roots | previous_roots),
        }

    record({unresolved_root(cwd)}, [*why, "범위 계산 전"])
    roots, problems = reset_scope(saved, cwd)
    record(set(roots), [*why, *problems])


def state_shape_problem(saved: dict[str, Any]) -> str | None:
    """The first key whose value has the wrong type for the code that reads it, or None."""
    for key, default in default_state().items():
        if key not in saved:
            continue
        value = saved[key]
        if default is None:
            ok = value is None or isinstance(value, dict)
        elif isinstance(default, int | float):
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        else:
            ok = isinstance(value, type(default))
        if not ok:
            return key
    for key in ("seen_events", "touched_tops", "background_tops"):
        value = saved.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return key
    reset = saved.get("state_reset")
    if reset is not None and (
        not isinstance(reset, dict)
        or _number(reset.get("at")) is None
        or not isinstance(reset.get("roots", []), list)
    ):
        return "state_reset"
    return None


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
    """Write the decision and flush it now: a piped stdout is block-buffered, and a flush
    failing at interpreter exit would end with status 120 (non-blocking). A refusal that
    cannot be delivered becomes exit 2 (blocking) with the reason on stderr."""
    try:
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
        sys.stdout.flush()
    except (OSError, ValueError) as e:
        if decision != "allow":
            # Raised, not exited: the locked run still saves its state (finally), and the
            # hook boundary then ends with exit 2 (fail_closed -> hard_block).
            raise OutputFailed(f"PreToolUse 판정 출력 실패: {e}") from e
        log.exception("discipline.pre_output_failed")
    sys.exit(0)


class OutputFailed(Exception):
    """A blocking decision could not be delivered on stdout."""


def emit_stop_block(reason: str) -> None:
    try:
        print(json.dumps({"decision": "block", "reason": reason}))
        sys.stdout.flush()  # now, not at interpreter exit (a failure there is status 120)
    except (OSError, ValueError) as e:
        raise OutputFailed(f"Stop 판정 출력 실패: {e}") from e


# ---------------------------------------------------------------- events
def shell_in_background(data: dict[str, Any]) -> bool:
    """The shell command was sent to the background (asked for, or reported by the tool)."""
    ti = data.get("tool_input", {}) or {}
    response = data.get("tool_response")
    return bool(ti.get("run_in_background")) or (
        isinstance(response, dict) and bool(response.get("backgroundTaskId"))
    )


def note_shell_touch(
    data: dict[str, Any], state: dict[str, Any], *, after: bool = False
) -> None:
    """Record which repositories a shell command may change. Called for its PreToolUse and
    again (`after`) for its PostToolUse(Failure), so a command whose PreToolUse was never
    recorded is not taken as having touched nothing. Repeating it changes nothing."""
    ti = data.get("tool_input", {}) or {}
    cwd = str(data.get("cwd") or os.getcwd())
    command = str(ti.get("command", ""))
    shell_tops = state.setdefault("turn_shell_tops", [])
    touched = state.setdefault("touched_tops", [])
    # A repository that comes back here is compared with its original baseline (taken before
    # the command), so it counts as known too.
    known = set(state.get("repos", {})) | set(state.get("repos_removed", {}))
    for d in shell_targets(command, data.get("tool_name") == "PowerShell", cwd):
        top = track_repo(state, d)
        if top and top not in shell_tops:
            shell_tops.append(top)  # Bash edits there are visible only via content maps
        if top and top not in touched:
            touched.append(top)  # may change it before the next Stop
        if after and top and top not in known and top in state.get("repos", {}):
            # First seen only now, so its baseline already holds whatever the command wrote:
            # late, never "unchanged".
            state["repos"][top]["late"] = True
    # A shell can write any path (absolute paths, git -C, scripts), so every tracked repository
    # may change — never a "normal deletion" later. Conservative until P0-07 can tell which
    # paths a command writes.
    for top in state.get("repos", {}):
        if top not in touched:
            touched.append(top)
    if shell_in_background(data):
        # It may keep writing after this turn's Stop, and no hook says when it ends: these
        # repositories are never "untouched" again in this session.
        late_writers = state.setdefault("background_tops", [])
        for top in touched:
            if top not in late_writers:
                late_writers.append(top)


def on_pre(data: dict[str, Any], state: dict[str, Any]) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    cwd = str(data.get("cwd") or os.getcwd())

    # D-5: snapshot any repository this tool is about to touch, before it runs.
    if tool in ("Bash", "PowerShell"):
        command = str(ti.get("command", ""))
        note_shell_touch(data, state)
        # A check is evidence only for the content it ran on: remember the content before it
        # runs, so `gate && sed ...` (content changed during the command) is not bound to the
        # post-edit content.
        tops = check_repos(state, command, tool == "PowerShell", cwd)
        if tops is not None and data.get("tool_use_id"):
            pre = state.setdefault("pre_check", {})
            pre[str(data["tool_use_id"])] = repo_snapshots(state, tops)
            for stale in list(pre)[:-MAX_PRE_CHECKS]:
                pre.pop(stale, None)
    elif ti.get("file_path") and not ignored_path(state, str(ti["file_path"]), cwd):
        top = track_repo(state, absolute(str(ti["file_path"]), cwd))
        if top and top not in state.setdefault("touched_tops", []):
            state["touched_tops"].append(top)  # may change it before the next Stop

    if tool in ("Bash", "PowerShell"):
        tests = deleted_test_files(ti.get("command", ""))
        if tests:
            emit_pre(
                "ask",
                f"[discipline] 테스트 파일 삭제 감지: {', '.join(tests)}. 의도한 삭제인지 확인해 주세요.",
            )
        return

    path = ti.get("file_path", "")
    if not path or ignored_path(state, path, cwd):
        return
    path = absolute(path, cwd)
    # A reset marker does not deny writes: edits after it are tracked by the new state, and
    # Stop stays blocked until the gates in its scope pass, so a red gate remains fixable.

    # 0) v2: the approval (and the store itself) is verified now, before this write.
    if v2_active():
        try:
            denial = v2_write_check(state, cwd, path)
        except Exception as e:  # hook boundary: refuse instead of crashing (fail-open)
            log.exception("approvals.v2_hook_failure")
            denial = v2_failure(e)
        if denial:
            emit_pre("deny", denial)

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
            "더 진행하기 전에 /plan 으로 계획을 세우거나 갱신하고 ExitPlanMode 로 사용자 승인을 받으세요. "
            "이미 승인된 진행 중 계획을 이어가는 것이라면, 사용자가 프롬프트 첫 줄에 task:<id>를 적어야 재무장됩니다.",
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
        cwd = str(data.get("cwd") or os.getcwd())
        if path and not ignored_path(state, path, cwd):
            path = absolute(path, cwd)
            if path not in state["turn_files"]:
                state["turn_files"].append(path)
            top = track_repo(state, path)
            if top and top not in state.setdefault("touched_tops", []):
                state["touched_tops"].append(top)  # also when no PreToolUse was seen
            if Path(path).suffix.lower() in CODE_EXT:
                state["last_code_edit"] = now
                mark_dirty(state, path, now, changed_lines(tool, ti))
    elif tool in ("Bash", "PowerShell"):
        note_shell_touch(data, state, after=True)  # also when no PreToolUse was seen
        record_evidence(data, state, failed=False)
        if v2_active():  # a shell may have changed a plan: record what is seen now
            try:
                v2_write_check(state, str(data.get("cwd") or os.getcwd()))
            except Exception as e:  # hook boundary: the approval is lost, not ignored
                log.exception("approvals.v2_hook_failure")
                state["approval"] = None
                state["approval_warning"] = v2_failure(e)
    elif tool in ("Agent", "Task"):
        if ti.get("subagent_type") in REVIEW_AGENTS:
            state["last_review"] = now  # a review, not verification
    elif tool == "ExitPlanMode":
        # PostToolUse only fires once the user approved the plan.
        record_approval(data, state)


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


def load_key(*, create: bool) -> bytes | None:
    """HMAC key for approval records; created on first approval, never shown to tools (guard/deny)."""
    try:
        return bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())
    except (
        OSError,
        ValueError,
    ):  # missing or unreadable key: records cannot be verified
        if not create:
            return None
    APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    KEY_FILE.write_text(key.hex(), encoding="utf-8")
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


def record_approval(data: dict[str, Any], state: dict[str, Any]) -> None:
    import worktree

    response = data.get("tool_response")
    response = response if isinstance(response, dict) else {}
    text = response.get("plan") if isinstance(response.get("plan"), str) else None
    if text is None and isinstance(response.get("filePath"), str):
        try:
            text = Path(response["filePath"]).read_text(encoding="utf-8")
        except (OSError, ValueError):  # gone or not UTF-8: approval stays prompt-only
            text = None
    cwd = str(data.get("cwd") or os.getcwd())
    if v2_active() and text and plan_task(text):
        try:
            v2_record_approval(data, state, text, response, cwd)
        except Exception as e:  # hook boundary: no approval rather than a crash
            log.exception("approvals.v2_hook_failure")
            state["approval"] = None
            state["approval_warning"] = v2_failure(e)
        return
    record = {
        "task_id": plan_task(text) if text else None,
        "plan_hash": plan_hash(text) if text else None,
        "scope": plan_scope(text, cwd) if text else [],
        "prompt_id": data.get("prompt_id"),
        "session_id": data.get("session_id"),
        # The project the approval was given in: its git toplevel (so approving from a
        # subdirectory still covers the project's docs/plans), else the working directory.
        "base": os.path.normcase(worktree.toplevel(cwd) or os.path.abspath(cwd)),
        "at": time.time(),
    }
    state["approval"] = record
    if record["task_id"] and record["plan_hash"]:
        key = load_key(create=True)
        approvals, _problem = (
            _load_approvals()
        )  # a corrupt file is replaced by valid records
        approvals[record["plan_hash"]] = {**record, "mac": sign_record(record, key)}
        newest = sorted(approvals.items(), key=lambda kv: float(kv[1].get("at", 0)))[
            -MAX_APPROVALS:
        ]
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        APPROVALS_FILE.write_text(json.dumps(dict(newest)), encoding="utf-8")


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
    prompt's naming text names by task id or by plan file."""
    import worktree

    naming = naming_text(prompt)
    tasks = prompt_tasks(naming)
    if not tasks and not PLAN_REF.search(naming):
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
        if (task and task.lower() in tasks) or names_plan(
            naming, plan, root, os.path.abspath(cwd)
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


# ---------------------------------------------------------------- P0-04: v2 approval instances
# Dormant until the admin `activate` command (approvals.py, interactive TTY only) creates
# APPROVALS_HOME/v2-active; until then everything above (v1) runs unchanged and nothing is
# written by v2. Under v2 the session keeps only a pointer (approval_id); every protected
# write re-reads and verifies the store and the plan file.
REVOKE_CMD = re.compile(
    r"^\s*revoke\s+task(?:_id)?\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9_.-]*)",
    re.IGNORECASE | re.ASCII,
)
NOT_GLOBAL = "전역 취소 미완료"
REASON_V2_STORE = (
    "[discipline] 승인 저장소를 신뢰할 수 없어 모든 보호 대상 쓰기와 승인 발급을 차단합니다({why}). "
    "복구는 사용자가 대화형 터미널에서 `python -I ~/.claude/hooks/approvals.py reset`을 직접 실행해야 하며"
    "(권한은 발급되지 않음), 그 뒤 계획마다 다시 승인받아야 합니다. Claude는 이를 대신 실행할 수 없습니다."
)
REASON_V2_INVALID = (
    "[discipline] 승인(task: {task})이 더 이상 유효하지 않아 쓰기를 차단합니다: {why}. "
    "계속하려면 계획을 다시 제시해 ExitPlanMode로 새 승인을 받으세요(과거 승인은 되살아나지 않습니다)."
)


# == approvals.V2_FILES (test_approvals checks it): the marker, or any v2 file, means v2, so
# deleting the marker alone never falls back to v1.
V2_FILES = ("v2-active", "approvals.v2.json", "hwm")


def v2_active() -> bool:
    # Stats only: importing approvals on every hook call costs ~50-100 ms while v2 is dormant.
    return any((APPROVALS_HOME / n).exists() for n in V2_FILES)


def v2_failure(e: BaseException) -> str:
    """An unexpected failure in a v2 check, at the hook boundary: it must refuse, never let
    the tool proceed (a crashing hook is non-blocking in Claude Code)."""
    return REASON_V2_STORE.format(why=f"검사 중 오류 {type(e).__name__}: {e}")


def _v2_store() -> Any:
    import approvals

    return approvals.Store(APPROVALS_HOME, approvals.default_witness())


def _seen(state: dict[str, Any]) -> int:
    return int(state.get("approvals_seen_gen") or 0)


def _v2_load(state: dict[str, Any]) -> Any:
    snap = _v2_store().load(
        _seen(state)
    )  # this session's memory is a rollback witness too
    state["approvals_seen_gen"] = max(_seen(state), snap.gen)
    return snap


def _v2_update(state: dict[str, Any], fn: Any) -> Any:
    snap = _v2_store().update(fn, _seen(state))
    state["approvals_seen_gen"] = snap.gen
    return snap


def _v2_root(cwd: str) -> str:
    import worktree

    return os.path.normcase(worktree.toplevel(cwd) or os.path.abspath(cwd))


def _v2_find_plan(root: str, task: object, core: object) -> str | None:
    """The one plan file in root/docs/plans with this task and core hash (else None)."""
    import approvals

    plans_dir = Path(root) / "docs" / "plans"
    hits: list[Path] = []
    for plan in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        try:
            text = plan.read_text(encoding="utf-8")
        except (
            OSError,
            ValueError,
        ):  # unreadable or not UTF-8: cannot be the approved one
            continue
        if plan_task(text) == task and approvals.core_hash(text) == core:
            hits.append(plan)
    return os.path.normcase(os.path.relpath(hits[0], root)) if len(hits) == 1 else None


UNBOUND = "승인된 계획 파일이 아직 docs/plans에 없음(계획 파일을 먼저 기록하세요)"


def _v2_read_plan(
    rec: dict[str, Any] | None, root: str
) -> tuple[str | None, str | None]:
    """(text, None); (None, None) when the bound plan file is gone (-> revoke); (None, why)
    when it cannot be judged now (unbound, locked, not UTF-8): refused, never revoked."""
    rel = (rec or {}).get("plan_path")
    if not rel:
        return None, UNBOUND
    try:
        return (Path(root) / str(rel)).read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"계획 파일을 지금 읽을 수 없음({type(e).__name__})"


def v2_observe(state: dict[str, Any], root: str) -> None:
    """Record permanently what is seen now: ACTIVE approvals of `root` whose plan left
    in-progress, changed its core or disappeared are revoked; unbound plan paths are bound.
    Raises StoreError when the store cannot be trusted. When the write fails, the causes stay
    pending (this session refuses them) and the failure is reported as not global."""
    import approvals as ap

    now = time.time()
    snap = _v2_load(state)
    pending: dict[str, str] = dict(state.get("pending_revocations") or {})
    binds: dict[str, str] = {}
    for rid, rec in snap.records.items():
        if rec.get("state") != ap.ACTIVE or rec.get("git_root") != root:
            continue
        if not rec.get("plan_path"):
            rel = _v2_find_plan(root, rec.get("task_id"), rec.get("core_hash"))
            if rel:
                binds[rid] = rel
            continue
        text, unreadable = _v2_read_plan(rec, root)
        if unreadable:
            continue  # cannot be judged now: refused at the write, never revoked for it
        verdict = ap.check(rec, root=root, plan_text=text, now=now)
        if verdict.revoke and verdict.reason:
            pending[rid] = verdict.reason
    pending = {
        rid: why
        for rid, why in pending.items()
        if (snap.records.get(rid) or {}).get("state") == ap.ACTIVE
    }
    if not pending and not binds:
        state["pending_revocations"] = {}
        return

    def apply(recs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out = dict(recs)
        for rid, rel in binds.items():
            if rid in out and not out[rid].get("plan_path"):
                out[rid] = {**out[rid], "plan_path": rel}
        for rid, why in pending.items():
            out = ap.revoke(out, [rid], why, now)
        return out

    try:
        _v2_update(state, apply)
    except ap.StoreError as e:
        state["pending_revocations"] = pending
        if pending:
            state["approval_warning"] = (
                f"승인 폐기를 저장하지 못했습니다({e}) — {NOT_GLOBAL}: 이 세션에서만 차단되며 "
                "다른 세션에는 반영되지 않았을 수 있습니다"
            )
        return
    state["pending_revocations"] = {}


def v2_record_approval(
    data: dict[str, Any],
    state: dict[str, Any],
    text: str,
    response: dict[str, Any],
    cwd: str,
) -> None:
    """ExitPlanMode: issue a new approval instance; nothing is granted unless it is stored."""
    import approvals as ap

    now = time.time()
    root = _v2_root(cwd)
    task = plan_task(text) or ""
    core = ap.core_hash(text)
    plan_path = None
    fp = response.get("filePath")
    if isinstance(fp, str) and _inside(fp, os.path.join(root, "docs", "plans")):
        try:
            same = ap.core_hash(Path(fp).read_text(encoding="utf-8")) == core
        except (OSError, ValueError):  # not written yet / unreadable: bound later
            same = False
        if same:
            plan_path = os.path.normcase(os.path.relpath(fp, root))
    plan_path = plan_path or _v2_find_plan(root, task, core)
    rec = ap.new_record(
        task_id=task,
        git_root=root,
        plan_path=plan_path,
        core=core,
        scope=plan_scope(text, cwd),
        session_id=data.get("session_id"),
        prompt_id=data.get("prompt_id"),
        now=now,
    )
    state["approval"] = None
    try:
        _v2_update(state, lambda recs: ap.issue(recs, rec, now))
    except ap.StoreError as e:
        state["approval_warning"] = (
            f"승인을 저장하지 못해 발급하지 않았습니다({e}). 쓰기는 차단됩니다. "
            "저장소 문제를 해결한 뒤 계획을 다시 승인받으세요"
        )
        return
    state["approval"] = {
        "v": 2,
        "approval_id": rec["approval_id"],
        "task_id": task,
        "scope": rec["scope"],
        "prompt_id": data.get("prompt_id"),
        "base": root,
    }


def v2_revoke_task(state: dict[str, Any], root: str, task: str) -> str | None:
    """`revoke task:<id>` (first line): revoke this root's ACTIVE approvals of the task."""
    import approvals as ap

    now = time.time()
    ids: list[str] = []
    try:
        snap = _v2_load(state)
        ids = [
            rid
            for rid, r in snap.records.items()
            if r.get("state") == ap.ACTIVE
            and str(r.get("task_id") or "").lower() == task.lower()
            and r.get("git_root") == root
        ]
        if not ids:
            return f"{NOTE} 취소할 ACTIVE 승인이 없습니다(task:{task})"
        _v2_update(state, lambda recs: ap.revoke(recs, ids, "user_revoked", now))
    except ap.StoreError as e:
        state.setdefault("pending_revocations", {}).update(
            {rid: "user_revoked" for rid in ids}
        )
        return (
            f"승인 취소를 저장하지 못했습니다({e}) — {NOT_GLOBAL}: 이 세션에서만 차단되며 "
            "다른 세션에는 반영되지 않았을 수 있습니다"
        )
    return None


def v2_rearm(
    cwd: str, prompt: str, state: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """v2 counterpart of rearm_approval: the named plan's ACTIVE instance, verified now."""
    import approvals as ap

    revoke_cmd = REVOKE_CMD.match(naming_text(prompt))
    if revoke_cmd:
        return None, v2_revoke_task(state, _v2_root(cwd), revoke_cmd.group(1))
    root, named = named_plans(cwd, prompt)
    if not named:
        return None, None
    try:
        v2_observe(state, root)
        snap = _v2_load(state)
    except ap.StoreError as e:
        return None, f"승인 저장소를 신뢰할 수 없음({e})"
    now = time.time()
    pending = state.get("pending_revocations") or {}
    found: list[dict[str, Any]] = []
    problems: list[str] = []
    for plan, text, task in named:
        rel = os.path.normcase(os.path.relpath(plan, root))
        recs = [
            r
            for r in snap.records.values()
            if r.get("state") == ap.ACTIVE
            and r.get("task_id") == task
            and r.get("git_root") == root
            and r.get("plan_path") == rel
        ]
        if not recs:
            problems.append(f"유효한 v2 승인 없음: {plan.name}")
        for rec in recs:
            why = (
                f"폐기 대기({NOT_GLOBAL})"
                if rec["approval_id"] in pending
                else ap.check(rec, root=root, plan_text=text, now=now).reason
            )
            if why:
                problems.append(f"{why}: {plan.name}")
            else:
                found.append(rec)
    if len(found) > 1:
        names = ", ".join(str(f.get("task_id")) for f in found)
        return None, f"{AMBIGUOUS}({names}) — task:<id> 하나만 지정 필요"
    if not found:
        return None, "; ".join(problems) or None
    rec = found[0]
    pointer = {
        "v": 2,
        "approval_id": rec["approval_id"],
        "task_id": rec.get("task_id"),
        "scope": list(rec.get("scope") or []),
        "base": root,
        "rearmed_from": str(Path(root) / str(rec.get("plan_path"))),
    }
    return pointer, (f"{NOTE} {'; '.join(problems)}" if problems else None)


def v2_write_check(
    state: dict[str, Any], cwd: str, path: str | None = None
) -> str | None:
    """Denial reason for a protected write under v2, else None. The store is re-read and
    verified every time; a cached ACTIVE pointer alone never authorizes anything. Only a
    lost approval (revoked, expired, superseded, missing) sticks for the rest of the prompt;
    a plan that cannot be judged right now refuses this write only."""
    import approvals as ap

    ptr = state.get("approval")
    has_ptr = isinstance(ptr, dict) and bool(ptr.get("approval_id"))
    root = str(ptr.get("base")) if has_ptr and isinstance(ptr, dict) else _v2_root(cwd)
    try:
        v2_observe(state, root)
        snap = _v2_load(state)
    except ap.StoreError as e:
        state["v2_block"] = str(e)
        return REASON_V2_STORE.format(why=e)
    if not (has_ptr and isinstance(ptr, dict)):
        return None  # no approval: the five-file allowance applies (P0-06)
    rid = str(ptr["approval_id"])
    why = state.get("approval_invalid")
    if not why and rid in (state.get("pending_revocations") or {}):
        why = f"폐기 대기({NOT_GLOBAL})"
    if not why:
        rec = snap.records.get(rid)
        text, unreadable = _v2_read_plan(rec, root)
        if rec and rec.get("state") == ap.ACTIVE and unreadable:
            plans = os.path.normcase(os.path.join(root, "docs", "plans"))
            if unreadable == UNBOUND and path and _inside(path, plans):
                return None  # writing the approved plan file itself is what binds it
            state["v2_block"] = unreadable
            return REASON_V2_INVALID.format(task=ptr.get("task_id"), why=unreadable)
        verdict = ap.check(rec, root=root, plan_text=text, now=time.time())
        if verdict.reason is None and rec:
            ptr["scope"] = list(
                rec.get("scope") or []
            )  # the stored scope, not the cache
            return None
        why = verdict.reason
    state["approval_invalid"] = why
    state["v2_block"] = why
    return REASON_V2_INVALID.format(task=ptr.get("task_id"), why=why)


MAX_DIRTY_FILES = 200


def mark_dirty(state: dict[str, Any], path: str, at: float, lines: int = 0) -> None:
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
    changed = entry.setdefault("lines", {})
    changed[path] = int(changed.get(path, 0)) + lines


def reresolve_dirty(state: dict[str, Any]) -> None:
    """Policies can be declared or changed after an edit: regroup edited files by their current policy."""
    old = state["dirty"]
    state["dirty"] = {}
    for entry in old.values():
        files = entry.get("files") or []
        if not files:
            continue  # state written by an older hook version: nothing to regroup
        for f in files:
            mark_dirty(
                state,
                f,
                float(entry.get("at", 0)),
                int((entry.get("lines") or {}).get(f, 0)),
            )


def changed_lines(tool: str, ti: dict[str, Any]) -> int:
    """Approximate size of one edit (used only for the review-needed rule)."""
    if tool == "Write":
        return len(str(ti.get("content", "")).splitlines())
    pairs = ti.get("edits") if tool == "MultiEdit" else [ti]
    total = 0
    for e in pairs or []:
        if isinstance(e, dict):
            total += max(
                len(str(e.get("old_string", "")).splitlines()),
                len(str(e.get("new_string", "")).splitlines()),
            )
    return total


# ---------------------------------------------------------------- E-1 independent review (A/B switch)
RISKY_PATH = re.compile(
    r"[\\/](hooks|migrations|scaffold)[\\/]|[\\/]\.github[\\/]workflows[\\/]|settings\.json$|auth|secret|credential",
    re.IGNORECASE,
)
REVIEW_MIN_FILES = 3
REVIEW_MIN_LINES = 150


def review_triggers(entry: dict[str, Any], root: str = "") -> list[str]:
    """Why this project's change needs an independent review (empty = it does not)."""
    files = [str(f) for f in entry.get("files") or []]
    why: list[str] = []
    if len(files) >= REVIEW_MIN_FILES:
        why.append(f"코드 파일 {len(files)}개")
    lines = sum(int(n) for n in (entry.get("lines") or {}).values())
    if lines >= REVIEW_MIN_LINES:
        why.append(f"변경 {lines}줄")
    # Classify by the path inside the project: a project under some ".../tests/..." folder
    # must not make every file look like a test or a risky path.
    rel = [os.path.relpath(f, root) if root and _inside(f, root) else f for f in files]
    risky = [r for r in rel if RISKY_PATH.search(os.sep + r)]
    if risky:
        why.append(f"위험 경로 {Path(risky[0]).name}")
    tests = [r for r in rel if TEST_PATH.search(r)]
    if tests and len(tests) < len(files):
        why.append("테스트와 구현 동시 변경")
    return why


def record_evidence(
    data: dict[str, Any], state: dict[str, Any], *, failed: bool
) -> None:
    """Attach what this shell command proves (if anything) to each project with unverified edits."""
    from verification import Policy, evaluate

    reresolve_dirty(state)

    ti = data.get("tool_input", {}) or {}
    background = shell_in_background(data)
    is_ps = data.get("tool_name") == "PowerShell"
    cwd = str(data.get("cwd") or os.getcwd())
    now = time.time()
    from verification import find_policy

    command = str(ti.get("command", ""))
    targets = shell_targets(command, is_ps, cwd)
    pre = state.get("pre_check", {}).pop(str(data.get("tool_use_id") or ""), None)
    # No pre-run record (pre hook killed, entry evicted, no tool_use_id) means the content the
    # check ran on is unknown: such a run cannot be a pass.
    changed_during = (
        "검사 전 내용 기록이 없음(검사한 내용을 확인할 수 없음)"
        if not isinstance(pre, dict)
        else changed_while_running(state, pre)
    )
    policies: dict[str, Policy] = {}
    for root, entry in state["dirty"].items():
        policies[root] = {
            "root": root,
            "gate": entry.get("gate"),
            "ci": entry.get("ci", "undeclared"),
            "validate": entry.get("validate"),
        }
    # D-5: projects changed outside the Edit tool have no dirty entry; the directories this
    # command ran in decide which project's evidence it can be.
    for d in targets:
        found = find_policy(os.path.join(d, "_"))
        policies.setdefault(found["root"], found)
    for root, policy in policies.items():
        entry = state["dirty"].get(root, {})
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
        if ev["outcome"] == "passed" and changed_during:
            ev = {
                **ev,
                "outcome": "unknown",
                "why": changed_during,
            }
        old = state["evidence"].get(root) or {}
        still_valid_pass = old.get("outcome") == "passed" and float(
            old.get("at", 0)
        ) >= float(entry.get("at", 0))
        if still_valid_pass and ev["outcome"] in ("partial", "unknown"):
            continue  # a later side run does not revoke a passed gate for unchanged code
        record: dict[str, Any] = {**ev, "at": now}
        if ev["outcome"] == "passed":
            record["digests"] = evidence_digests(state, root, targets[-1])
        state["evidence"][root] = record
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
        note_shell_touch(data, state, after=True)  # also when no PreToolUse was seen
        record_evidence(data, state, failed=True)


def relay_shaped(prompt: str) -> bool:
    """A prompt that looks like a relayed agent message or task notification. UserPromptSubmit
    also runs for those (documented) and no hook field tells them from typed text, so this may
    only make the hook stricter: such a prompt keeps the turn's counted files and baselines,
    and never keeps or grants an approval."""
    first = next((ln for ln in prompt.splitlines() if ln.strip()), "").strip().lower()
    return first.startswith(RELAY_HEADERS) or first.startswith("<task-notification>")


RELAY_NOTE = (
    f"{NOTE} 중계 메시지(subagent 결과·알림)로는 승인이 유지되지 않습니다 — "
    "작업을 이어가려면 사용자가 프롬프트 첫 줄에 task:<id>를 적어 재개해야 합니다"
)


def on_prompt(data: dict[str, Any], state: dict[str, Any]) -> None:
    state["turn"] = int(state.get("turn", 0)) + 1
    prompt = data.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    cwd = str(data.get("cwd") or os.getcwd())
    relay = relay_shaped(prompt)
    if not relay:  # a relay continues the user's turn: its counts and baselines stay
        state["turn_files"] = []
        state["turn_shell_tops"] = []
    # A new prompt never inherits the previous approval; only a prompt that names the same
    # in-progress plan's task (or plan file) re-arms it.
    state["approval_invalid"] = None
    state["v2_block"] = None
    if relay:
        had = bool(state.get("approval"))
        state["approval"] = None
        state["approval_warning"] = RELAY_NOTE if had else None
    elif v2_active():
        try:
            state["approval"], state["approval_warning"] = v2_rearm(cwd, prompt, state)
        except Exception as e:  # hook boundary: nothing re-armed rather than a crash
            log.exception("approvals.v2_hook_failure")
            state["approval"], state["approval_warning"] = None, v2_failure(e)
    else:
        state["approval"], state["approval_warning"] = rearm_approval(cwd, prompt)
    state["prompt_id"] = data.get("prompt_id")
    if state["approval"]:
        # Bound to this prompt: if a later prompt's hook never runs (killed, timed out), the
        # stale approval does not carry over to it.
        state["approval"]["prompt_id"] = data.get("prompt_id")
    told = ack_missing(data, state)  # the user's `ack-missing <path>`, if this is one
    if told:
        emit_prompt_context(told)
    track_repo(state, str(data.get("cwd") or os.getcwd()))
    if not relay:
        refresh_turn_baselines(state)


# ---------------------------------------------------------------- D-5 content tracking
# Every git repository the session touches gets a content map ({path: blob}) as baseline
# when it is first seen (before the tool that touches it runs) and a per-turn baseline at
# each prompt. Stop compares them with the current contents, so edits made by any tool
# (Edit, sed, scripts) are seen, commits are not mistaken for edits, and verification
# evidence is bound to the content hash it was produced on.
MAX_DIR_CACHE = 200
MAX_PRE_CHECKS = 20
GIT_BUDGET_S = {"pre": 20.0, "prompt": 20.0, "post": 45.0, "fail": 45.0, "stop": 45.0}
# Mirrors the discipline hook timeouts in settings.json (a test fails when they drift). The
# lock wait and the git budget both come out of this one deadline, so the hook decides
# (closed) before Claude Code kills it.
HOOK_TIMEOUT_S = {"pre": 30.0, "prompt": 30.0, "post": 60.0, "fail": 60.0, "stop": 60.0}
HOOK_MARGIN_S = 5.0  # initial prior: interpreter start, handler work, save


def hook_time_left(mode: str, elapsed: float) -> float:
    return HOOK_TIMEOUT_S[mode] - HOOK_MARGIN_S - elapsed


def lock_budget_s(mode: str, elapsed: float, configured: float) -> float:
    """Wait for the session lock at most half of what is left, so git keeps the rest."""
    return max(0.0, min(configured, hook_time_left(mode, elapsed) / 2))


def git_budget_s(mode: str, elapsed: float, configured: float) -> float:
    return max(0.0, min(configured, hook_time_left(mode, elapsed)))


def _watch_globs(top: str) -> list[str]:
    from verification import find_policy
    from worktree import rel_prefix

    policy = find_policy(os.path.join(top, "_"))
    prefix = rel_prefix(top, policy["root"]) if policy["root"] else None
    if prefix is None:
        return []
    return [f"{prefix}/{g}" if prefix else g for g in policy.get("watch", [])]


def is_repo_root(top: str) -> bool:
    """A git toplevel is at `top` right now. Asked from git, never from the directory cache: a
    plain folder or another repository's subfolder at a missing repository's path is not it.
    No answer in time counts as no."""
    import worktree

    if not os.path.isdir(top):
        return False
    found = worktree.toplevel(top)
    return bool(found) and os.path.normcase(os.path.abspath(found)) == os.path.normcase(
        os.path.abspath(top)
    )


def repo_present(top: str) -> bool:
    """Cheap presence test (no git call): the folder is there and still carries its `.git`
    entry. A folder left or re-created without one is not the repository that was tracked."""
    return os.path.isdir(top) and os.path.exists(os.path.join(top, ".git"))


def forget_dir_tops(state: dict[str, Any], top: str) -> None:
    """Drop cached directory -> toplevel answers about `top`: after a repository went missing or
    came back, the next lookup there asks git again."""
    cache = state.get("dir_tops")
    if not isinstance(cache, dict):
        return
    for probe in [p for p, t in cache.items() if t == top or _inside(p, top)]:
        cache.pop(probe, None)


def restore_repo(state: dict[str, Any], top: str) -> None:
    """A path whose repository had gone missing has a repository again: it is compared with that
    path's original baselines (no identity guessing), so nothing unverified is forgotten and
    nothing there is taken as already verified."""
    state.setdefault("repos", {})[top] = state["repos_removed"].pop(top)
    if top in state.get("content_removed", []):
        state["content_removed"].remove(top)
    forget_dir_tops(state, top)
    late_writers = state.get("background_tops")
    if isinstance(late_writers, list) and late_writers and top not in late_writers:
        # A background shell started while it was away may write here too.
        late_writers.append(top)


def track_repo(state: dict[str, Any], path: str) -> str | None:
    """Git toplevel containing `path`; a newly seen repository is snapshotted right away."""
    import worktree

    p = os.path.abspath(path)
    repos = state.setdefault("repos", {})
    # Resolve per directory with git, never by path prefix: a repository nested inside
    # another repository's folder (ignored subrepo, submodule) has its own toplevel.
    probe = p if os.path.isdir(p) else os.path.dirname(p)
    dir_tops = state.setdefault("dir_tops", {})
    cached = probe in dir_tops
    if cached and dir_tops[probe] and not repo_present(dir_tops[probe]):
        dir_tops.pop(probe)  # that repository is gone: ask git what is here now
        cached = False
    if cached:
        top = dir_tops[probe]
    else:
        top = worktree.toplevel(p)
        if worktree.budget_exhausted:
            missed = state.setdefault("missed_dirs", [])
            if probe not in missed and len(missed) < MAX_DIR_CACHE:
                missed.append(
                    probe
                )  # this directory's repository went unobserved for a while
        elif len(dir_tops) < MAX_DIR_CACHE:
            dir_tops[probe] = top
    if not top:
        return None
    if top not in repos and top in state.get("repos_removed", {}):
        # A repository that was missing: never a fresh baseline. An answer git gave just now is
        # a toplevel; a cached one (a state saved before missing repositories' cache entries
        # were dropped) is asked again, and a path that is no toplevel stays missing.
        if cached and not is_repo_root(top):
            dir_tops.pop(probe, None)
            return None
        restore_repo(state, top)
    if top not in repos or repos[top].get("session") is None:
        # New repository, or its first snapshot failed (timeout/budget): (re)take the baseline.
        # A baseline taken after a failed attempt may already contain unverified edits, so it
        # is marked late: its changes are reported unknown, never taken as "unchanged".
        # So is a repository at a path the user acknowledged as missing: what was there was
        # never verified, and nothing says this one is another.
        late = (
            top in repos
            or any(_inside(d, top) for d in state.get("missed_dirs", []))
            or acked_before(state, top)
        )
        snap = worktree.content_map(top, _watch_globs(top))
        repos[top] = {
            "session": snap,
            "turn": snap,
            "turn_head": worktree.head(top),
            "turn_at": time.time(),
            "late": late,
        }
        late_writers = state.get("background_tops")
        if isinstance(late_writers, list) and late_writers and top not in late_writers:
            # A background shell started earlier may write here too (absolute paths): this
            # repository is never "untouched" for the rest of the session either.
            late_writers.append(top)
    return top


def refresh_turn_baselines(state: dict[str, Any]) -> None:
    import worktree

    for top, base in state.get("repos", {}).items():
        base["turn"] = worktree.content_map(top, _watch_globs(top))
        base["turn_head"] = worktree.head(top)
        base["turn_at"] = time.time()


def repo_snapshots(
    state: dict[str, Any], tops: list[str]
) -> dict[str, dict[str, str] | None]:
    """Relevant file contents of each repository before a check runs (None: unavailable)."""
    import worktree

    out: dict[str, dict[str, str] | None] = {}
    for top in tops:
        snap = worktree.content_map(top, _watch_globs(top))
        tracked = worktree.tracked_paths(top) if snap is not None else None
        out[top] = (
            None
            if snap is None or tracked is None
            else {
                p: h
                for p, h in snap.items()
                if worktree.relevant(p)
                and (p in tracked or p.lower().endswith(worktree.SOURCE_EXT))
            }
        )
    return out


def changed_while_running(state: dict[str, Any], pre: dict[str, Any]) -> str:
    """Why the content a check ran on differs from the content now ("" if it does not).
    Files the check itself created (reports, coverage) do not count; edits and deletions of
    files that existed before it ran do."""
    import worktree

    for top, before in sorted(pre.items()):
        now = (
            worktree.content_map(top, _watch_globs(top)) if before is not None else None
        )
        if before is None or now is None:
            return "검사 전후 내용을 확인할 수 없음(git 조회 실패)"
        if any(now.get(p) != h for p, h in before.items()):
            return "검사 실행 중 파일이 바뀜(검사한 내용과 현재 내용이 다를 수 있음)"
    return ""


def check_repos(
    state: dict[str, Any], command: str, is_ps: bool, cwd: str
) -> list[str] | None:
    """Repositories whose content a check command could be evidence for (None if not a check)."""
    from shellparse import Dialect
    from verification import Policy, evaluate, find_policy

    reresolve_dirty(state)  # the same policy grouping record_evidence() will use
    targets = shell_targets(command, is_ps, cwd)
    policies: list[Policy] = [find_policy(os.path.join(d, "_")) for d in targets]
    for r, e in state.get("dirty", {}).items():
        policies.append(
            {
                "root": r,
                "gate": e.get("gate"),
                "ci": e.get("ci", "undeclared"),
                "validate": e.get("validate"),
            }
        )
    dialect: Dialect = "powershell" if is_ps else "bash"
    if not any(
        evaluate(command, dialect, cwd, p, failed=False, background=False)
        for p in policies
    ):
        return None
    tops = {track_repo(state, d) for d in targets} | {
        track_repo(state, p["root"]) for p in policies if p["root"]
    }
    return sorted(t for t in tops if t)


def shell_targets(command: str, is_ps: bool, cwd: str) -> list[str]:
    """Directories a shell command works in, in order: the hook cwd, then every `cd` target."""
    from shellparse import ParseError, parse, program_and_args
    from verification import cwd_at

    try:
        cmds = [
            c for c in parse(command, "powershell" if is_ps else "bash") if not c.nested
        ]
    except ParseError:
        return [cwd]
    dirs = [cwd]
    for i, c in enumerate(cmds):
        program, args = program_and_args(c)
        if program in ("cd", "set-location", "sl", "pushd", "chdir") and args:
            target = cwd_at(cmds, i + 1, cwd)
            if target not in dirs:
                dirs.append(target)
    return dirs


def refresh_content(state: dict[str, Any]) -> None:
    """Stop-time comparison of current contents with the session/turn baselines."""
    import worktree
    from verification import find_policy

    content: dict[str, Any] = {}
    unknown: list[str] = []
    too_large: list[str] = []
    late: list[str] = []
    turn_changes: set[str] = set()
    visible: set[str] = set()
    kept = state.setdefault("repos_removed", {})
    for top in [t for t in kept if is_repo_root(t)]:
        restore_repo(state, top)  # it came back: compare with the original baseline
    touched = set(state.get("touched_tops", [])) | set(state.get("background_tops", []))
    status = state.setdefault("repo_state", {})
    for top in [t for t in state.get("repos", {}) if not repo_present(t)]:
        forget_dir_tops(state, top)
        # A missing repository (deleted, moved, renamed) cannot be compared now. Its baseline
        # is kept by path (a repository appearing there again is compared with it, never taken
        # as verified). Without guessing where it went: proven clean (clean at the last Stop
        # and untouched since) is a normal deletion; anything else is reported every turn.
        base = state["repos"].pop(top)
        if status.get(top, "clean") == "clean" and top not in touched:
            status.pop(
                top, None
            )  # proven clean: nothing kept for a new repository there
            continue
        kept[top] = base
        if top not in state.setdefault("content_removed", []):
            state["content_removed"].append(top)
    for top, base in state.get("repos", {}).items():
        now = worktree.content_map(top, _watch_globs(top))
        if now is None or base.get("session") is None:
            (too_large if worktree.last_error == "too_large" else unknown).append(top)
            continue
        if base.get("late"):
            late.append(
                top
            )  # baseline may include unverified edits: timestamps decide there
        else:
            visible |= {
                os.path.normcase(os.path.join(top, p))
                for p in set(now) | set(base["session"])
            }
        turn_base = base.get("turn") or base["session"]
        moved = worktree.changed(turn_base, now)
        head_then, head_now = base.get("turn_head"), worktree.head(top)
        if head_then and head_now and head_then != head_now:
            # checkout/pull/reset moved HEAD: files whose old and new content equal the old and
            # new commits came from git, not from an edit made in this turn.
            old_tree, new_tree = (
                worktree.tree_map(top, head_then),
                worktree.tree_map(top, head_now),
            )
            # Commits made during this turn (`edit; git commit`) are this turn's own edits, so
            # their paths are never excluded.
            own = worktree.paths_committed_since(
                top, head_then, head_now, float(base.get("turn_at") or 0)
            )
            if old_tree is not None and new_tree is not None and own is not None:
                moved = {
                    p
                    for p in moved
                    if p in own
                    or not (
                        turn_base.get(p) == old_tree.get(p)
                        and now.get(p) == new_tree.get(p)
                    )
                }
        turn_changes |= {os.path.join(top, p) for p in moved}
        changed_roots: dict[str, Any] = {}
        for p in worktree.changed(base["session"], now):
            if worktree.relevant(p):
                policy = find_policy(os.path.join(top, p))
                changed_roots.setdefault(policy["root"], policy)
        push = state.get("pushes", {}).get(top)
        pushed_tree = worktree.tree_map(top, push["sha"]) if push else None
        for root, policy in changed_roots.items():
            prefix = (worktree.rel_prefix(top, root) or "") if root else ""
            entry = content.setdefault(
                root,
                {
                    "digests": {},
                    "pushed": {},
                    "gate": policy["gate"],
                    "ci": policy["ci"],
                },
            )
            entry["digests"][top] = worktree.digest(now, prefix)
            entry["pushed"][top] = (
                pushed_tree is not None
                and worktree.digest(pushed_tree, prefix) == entry["digests"][top]
            )
    state["content"] = content
    state["content_unknown"] = unknown
    state["content_too_large"] = too_large
    state["content_late"] = late
    # What each present repository is at this Stop (used if it goes missing later): clean =
    # no change since the session baseline, or every change covered by passed evidence.
    for top in state.get("repos", {}):
        if top in unknown or top in too_large or top in late:
            status[top] = "unverified"
            continue
        verified = True
        for root, entry in content.items():
            if top in entry.get("digests", {}):
                ev = state.get("evidence", {}).get(root) or {}
                verified &= ev.get("outcome") == "passed" and all(
                    ev.get("digests", {}).get(t) == d
                    for t, d in entry["digests"].items()
                )
        status[top] = "clean" if verified else "unverified"
    state["touched_tops"] = []
    state["turn_changes"] = sorted(turn_changes)
    # Edit-tracked roots whose files are all visible to git: content decides, not timestamps.
    state["content_tracked_roots"] = [
        root
        for root, entry in state.get("dirty", {}).items()
        if entry.get("files")
        and all(os.path.normcase(os.path.abspath(f)) in visible for f in entry["files"])
    ]


def evidence_digests(state: dict[str, Any], root: str, cwd: str) -> dict[str, str]:
    """Content hashes a passed check was run on (bound into the evidence)."""
    import worktree

    own = track_repo(state, root or cwd)
    if not own:
        return {}
    # The project root's own repository plus every tracked repository nested inside the root
    # (same keys refresh_content() produces), so a nested repo never leaves evidence stale.
    tops = {own} | (
        {t for t in state.get("repos", {}) if _inside(t, root)} if root else set()
    )
    out: dict[str, str] = {}
    for top in sorted(tops):
        snap = worktree.content_map(top, _watch_globs(top))
        if snap is None:
            return {}  # partial evidence is no evidence
        prefix = (worktree.rel_prefix(top, root) or "") if root else ""
        out[top] = worktree.digest(snap, prefix)
    return out


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
REASON_APPROVAL_INVALID = (
    "[discipline] 저장된 계획 승인 기록을 신뢰할 수 없어 자동 재승인하지 않았습니다({why}). "
    "이 작업을 계속하려면 계획을 다시 제시해 사용자 승인을 받으세요. 이 사실을 보고에 적으세요."
)
REASON_APPROVAL_AMBIGUOUS = (
    "[discipline] 프롬프트가 승인된 진행 중 계획 여러 개를 가리켜 어느 것도 재무장하지 않았습니다({why}). "
    "승인 기록은 유효합니다. 프롬프트 첫 줄에 task:<id> 하나만 적어 다시 요청해 달라고 사용자에게 알리세요."
)
REASON_RELAY_DROPPED = "[discipline] {why}. 지금은 승인된 작업이 없습니다. 이 사실과 재개 방법을 사용자에게 알리세요."
REASON_STATE_RESET = (
    "[discipline] 세션 추적 상태 손상 또는 누락({why}): 그 이전의 변경은 미확인입니다. "
    "해당 프로젝트들의 품질 게이트가 이 시점 이후 통과할 때까지 완료(Stop)를 턴마다 한 번 막습니다. "
    "보고에 '미확인'으로 적으세요. 범위 안의 경로가 사라졌거나 게이트가 없어 통과시킬 수 없다면, 사용자가 프롬프트 "
    "첫 줄에 ack-missing <경로> 를 입력해 그 경로만 확인으로 해제할 수 있습니다(검증으로 치지 않습니다)."
)
REASON_HOOK_ERROR = (
    "[discipline] 추적 hook 처리 중 오류({why}). 이번 이벤트를 신뢰할 수 있게 기록하지 못해 안전하게 막습니다. "
    "다음 실행에서 '세션 추적 상태 누락'으로 표시되며, 그 전의 변경은 미확인입니다. 이 사실을 사용자에게 알리세요. "
    "같은 오류가 반복되면 사용자가 프롬프트 첫 줄에 recover-state 를 입력해 복구할 수 있습니다(원본 보존, 미검증 표식 유지, "
    "승인은 task:<id>로 다시 재무장). 상태 파일을 직접 지우거나 고치지 마세요."
)
REASON_STATE_BUSY = (
    "[discipline] 세션 상태 잠금을 얻지 못했습니다({why}). 이번 판정을 기록할 수 없어 안전하게 막습니다. "
    "다른 hook(서브에이전트 포함)이 끝난 뒤 다시 시도하세요."
)
REASON_REMOVED = (
    "[discipline] 추적하던 저장소 {top}가 사라졌습니다(삭제·이동·이름 변경). 그 안에 검증되지 않은 "
    "변경이 있었거나 확인할 수 없어 '미확인'입니다. 그 경로로 되돌려 게이트를 통과시키기 전까지 매 턴 "
    "보고됩니다(다른 경로의 저장소로 이어 받지 않습니다). 보고에 '미확인'으로 적으세요. "
    "되돌릴 수 없다면 사용자가 프롬프트 첫 줄에 ack-missing {top} 을 입력해 확인으로 해제할 수 있습니다"
    "(검증으로 치지 않습니다). 이 방법을 사용자에게 알리세요."
)
REASON_APPROVAL_NOTE = (
    "[discipline] {why}. 지정한 승인 작업은 계속할 수 있습니다. "
    "거부된 쪽이 의도한 계획이었다면 그 사실을 사용자에게 알리세요."
)
REASON_STALE = (
    "[discipline] {root}: 마지막 검증 이후 파일 내용이 바뀌었습니다(Bash·스크립트 수정 포함, 내용 해시 기준). "
    "검증 증거가 무효이니 게이트를 다시 실행하세요."
)
REASON_OUT_OF_SCOPE = (
    "[discipline] 이번 턴의 변경 중 {n}개가 승인된 계획(task: {task})의 변경 범위 밖입니다: {files}. "
    "되돌리거나 계획을 갱신해 다시 승인받으세요."
)
REASON_BULK = (
    "[discipline] 승인된 계획 없이 이번 턴에 {n}개 파일이 바뀌었습니다(Bash 등 도구 밖 수정 포함, 한도 {limit}): "
    "{files}. /plan 으로 계획을 세워 승인받거나 변경을 줄이세요."
)
REASON_CONTENT_UNKNOWN = (
    "[discipline] {top}의 변경 상태를 확인할 수 없습니다(git 조회 실패 또는 파일 수 초과). "
    "변경이 검증됐다는 근거가 없으므로 '미확인'으로 보고하세요."
)
REASON_TOO_LARGE_NOTE = " (파일 수가 내용 추적 한도 5,000개를 넘습니다. Bash·스크립트로 한 변경은 감지할 수 없으므로, 이 저장소에서 셸 명령을 쓴 턴은 매번 '미확인'입니다.)"
REASON_LATE_NOTE = " (git 조회가 시간 안에 끝나지 않아 이 저장소의 기준선을 늦게 잡았습니다. 그 사이 변경은 검증 여부를 알 수 없습니다.)"
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
REASON_REVIEW = (
    "[discipline] {root}의 변경이 독립 리뷰 기준({why})에 해당합니다(CLAUDE_REVIEW_GATE=1). 기존 `code-reviewer` "
    "에이전트로 리뷰하고, CONFIRMED 지적은 테스트로 재현해 고친 뒤 게이트를 다시 실행하세요. "
    "리뷰 통과 자체는 완료 근거가 아닙니다."
)
REASON_GATE_NOT_RUN = (
    "[discipline] {root}에 선언된 품질 게이트(`{gate}`)가 마지막 수정 이후 통과하지 않았습니다"
    "(다른 테스트 명령만 실행됨). 게이트를 그대로 실행하거나, 실행하지 못한 이유를 '미확인'으로 보고하세요."
)


def _stop_decision(state: dict[str, Any]) -> tuple[str, str | None] | None:
    """(reason, notify key): a reset marker (once per turn until its scope's gates pass)
    together with the first other problem, so the one block of the turn shows both."""
    damaged = state_reset_active(state)
    other = _first_stop_problem(state)
    if damaged:
        reset = REASON_STATE_RESET.format(why=damaged)
        if other:
            return f"{reset}\n\n{other[0]}", other[1]
        return reset, None
    return other


def _first_stop_problem(state: dict[str, Any]) -> tuple[str, str | None] | None:
    """(reason, notify key) for the first problem: scope, unreadable state, content, then timestamps."""
    warning = state.get("approval_warning")
    if warning:
        key = f"approval-warning|{state.get('turn')}|{warning}"
        if _not_yet_notified(state, key):
            template = (
                REASON_APPROVAL_AMBIGUOUS
                if str(warning).startswith(AMBIGUOUS)
                else REASON_RELAY_DROPPED
                if warning == RELAY_NOTE  # nothing approved continues after a relay
                else REASON_APPROVAL_NOTE
                if str(warning).startswith(NOTE)
                else REASON_APPROVAL_INVALID
            )
            return template.format(why=warning), key
    decision = _scope_decision(state)
    if decision:
        return decision
    for top in state.get("content_too_large", []):
        # Once per session, and again in every turn that ran shell commands there: that turn's
        # edits cannot be seen, so it must never end as verified.
        key = f"content-too-large|{top}"
        if top in state.get("turn_shell_tops", []):
            key += f"|{state.get('turn')}"
        if _not_yet_notified(state, key):
            return REASON_CONTENT_UNKNOWN.format(top=top) + REASON_TOO_LARGE_NOTE, key
    for top in state.get("content_removed", []):
        # Every turn while it is missing and not proven clean: its changes were never verified.
        key = f"content-removed|{top}|{state.get('turn')}"
        if _not_yet_notified(state, key):
            return REASON_REMOVED.format(top=top), key
    for top in state.get("content_late", []):
        key = f"content-late|{top}"
        if _not_yet_notified(state, key):
            return REASON_CONTENT_UNKNOWN.format(top=top) + REASON_LATE_NOTE, key
    for top in state.get("content_unknown", []):
        key = f"content-unknown|{top}|{state.get('turn')}"
        if _not_yet_notified(state, key):
            return REASON_CONTENT_UNKNOWN.format(top=top), key
    for root, entry in sorted(state.get("content", {}).items()):
        decision = _content_decision(state, root, entry)
        if decision:
            return decision
    tracked = set(state.get("content_tracked_roots", []))
    for root, entry in sorted(state.get("dirty", {}).items()):
        edited_at = float(entry.get("at", 0))
        if edited_at <= 0 or root in tracked:
            continue  # content-tracked roots were decided above (or did not change at all)
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
        if os.environ.get("CLAUDE_REVIEW_GATE") == "1":
            why = review_triggers(entry, root)
            key = f"{root}|review|{edited_at}"
            if (
                why
                and float(state.get("last_review", 0)) < edited_at
                and _not_yet_notified(state, key)
            ):
                return REASON_REVIEW.format(
                    root=root or "이 프로젝트", why=", ".join(why)
                ), key
        if entry.get("ci") == "required":
            decision = _ci_decision(state, root, edited_at)
            if decision:
                return decision
    return None


def _scope_decision(state: dict[str, Any]) -> tuple[str, str] | None:
    """Changes made this turn by any tool must fit the approved plan's scope (or the 5-file limit)."""
    changes = state.get("turn_changes") or []
    if not changes:
        return None
    prompt_id = state.get("stop_prompt_id")
    approval = active_approval(state, prompt_id)
    if approval:
        outside = [f for f in changes if not in_approved_scope(approval, f)]
        key = f"scope|{prompt_id}|{len(outside)}"
        if outside and _not_yet_notified(state, key):
            shown = ", ".join(Path(f).name for f in outside[:5])
            return REASON_OUT_OF_SCOPE.format(
                task=approval.get("task_id"), n=len(outside), files=shown
            ), key
        return None
    key = f"bulk|{prompt_id}|{len(changes)}"
    if len(changes) > MAX_FILES_WITHOUT_PLAN and _not_yet_notified(state, key):
        shown = ", ".join(Path(f).name for f in changes[:5])
        return REASON_BULK.format(
            n=len(changes), limit=MAX_FILES_WITHOUT_PLAN, files=shown
        ), key
    return None


def _content_decision(
    state: dict[str, Any], root: str, c: dict[str, Any]
) -> tuple[str, str | None] | None:
    """A project whose file contents changed this session: evidence must match the current content."""
    where = f" (프로젝트: {root})" if root else ""
    ev = state.get("evidence", {}).get(root) or {}
    outcome = ev.get("outcome")
    digests: dict[str, str] = c.get("digests", {})
    if not (
        outcome == "passed"
        and all(ev.get("digests", {}).get(t) == d for t, d in digests.items())
    ):
        if outcome in ("failed", "unknown"):
            key = f"{root}|{float(ev.get('at', 0))}"
            if not _not_yet_notified(state, key):
                return None  # this failed/unknown result was already reported once
            reason = (
                REASON_FAILED
                if outcome == "failed"
                else REASON_UNKNOWN.format(why=ev.get("why", ""))
            )
            return reason + where, key
        if outcome == "partial":
            return REASON_GATE_NOT_RUN.format(root=root, gate=c.get("gate")), None
        if outcome == "passed":
            return REASON_STALE.format(root=root or "이 프로젝트"), None
        return REASON_UNVERIFIED + where, None
    if c.get("ci") != "required":
        return None
    ref_sha = "|".join(sorted(digests.values()))
    if not all(c.get("pushed", {}).get(t) for t in digests):
        key = f"{root}|nopush|{ref_sha}"
        return (
            (REASON_NOT_PUSHED.format(root=root), key)
            if _not_yet_notified(state, key)
            else None
        )
    for top in digests:
        push = state.get("pushes", {}).get(top) or {}
        ci = state.get("ci", {}).get(top) or {}
        ref = f"{push.get('repo')}@{str(push.get('sha', ''))[:7]}"
        if ci.get("sha") != push.get("sha") or float(ci.get("at", 0)) < float(
            push.get("at", 0)
        ):
            key = f"{root}|unchecked|{push.get('sha')}"
            return (
                (REASON_CI_UNCHECKED.format(ref=ref), key)
                if _not_yet_notified(state, key)
                else None
            )
        if ci.get("status") != "verified":
            key = f"{root}|ci|{push.get('sha')}|{ci.get('status')}|{ci.get('why')}"
            if not _not_yet_notified(state, key):
                return None
            runs = ", ".join(
                f"run {r.get('id')} {r.get('conclusion') or r.get('status')}"
                for r in ci.get("runs", [])
            )
            detail = "; ".join(x for x in (runs, str(ci.get("why") or "")) if x)
            template = (
                REASON_CI_FAILED if ci.get("status") == "failed" else REASON_CI_UNKNOWN
            )
            return template.format(ref=ref, detail=detail), key
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
    import worktree

    state["stop_prompt_id"] = data.get("prompt_id") or state.get("prompt_id")
    if v2_active():
        try:
            refused = v2_write_check(state, str(data.get("cwd") or os.getcwd()))
        except Exception as e:  # hook boundary: block with the failure, never crash
            log.exception("approvals.v2_hook_failure")
            refused, state["v2_block"] = v2_failure(e), v2_failure(e)
        if refused:
            # A revoked/invalid approval (or an untrusted store) covers nothing this turn did.
            state["approval"] = None
            state["approval_warning"] = state.get("approval_warning") or state.get(
                "v2_block"
            )
    track_repo(state, str(data.get("cwd") or os.getcwd()))
    refresh_content(state)
    if worktree.budget_exhausted:
        # git could not answer in time: what changed is unknown, which never counts as unchanged.
        marker = str(data.get("cwd") or os.getcwd())
        if marker not in state["content_unknown"]:
            state["content_unknown"].append(marker)
    damaged = state_reset_active(state)
    decision = _stop_decision(state)
    if decision is None:
        return
    reason, notify_key = decision
    if notify_key:
        state.setdefault("notified", {})[notify_key] = state.get("turn")
    reset = state.get("state_reset")
    if (
        damaged is not None
        and reason.startswith(REASON_STATE_RESET.format(why=damaged))
        and isinstance(reset, dict)
        and not reset.get("roots")
    ):
        state.pop(
            "state_reset", None
        )  # no gate to wait for: reported once, as '미확인'
    emit_stop_block(reason)


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
        run_hook(mode, data, handler, STATE_DIR / f"{session}.json")
    except Exception:  # outermost boundary: even the failure handling failed
        log.exception("discipline.hook_boundary_failure")
        hard_block(mode, data, REASON_HOOK_ERROR.format(why="오류 처리 중 오류"))


def run_hook(mode: str, data: dict[str, Any], handler: Any, path: Path) -> None:
    import session_store

    try:
        configured = float(
            os.environ.get("CLAUDE_DISCIPLINE_LOCK_BUDGET_S")
            or session_store.LOCK_BUDGET_S
        )
    except ValueError:  # malformed override: use the default
        configured = session_store.LOCK_BUDGET_S
    lock_budget = lock_budget_s(mode, time.monotonic() - _T0, configured)
    try:
        # One exclusive lock per session for the whole run: main and subagent hooks of the
        # session are serialized, so no update is lost (S).
        with session_store.locked(path, lock_budget):
            if recovery_requested(mode, data):
                recover_locked(data, path)
            else:
                run_locked(mode, data, handler, path)
    except session_store.StateBusy as e:
        # Nothing can be recorded: refuse what needs the state, remember that it was missed.
        note_safely(path, f"{mode}: {e}")
        fail_closed(mode, data, REASON_STATE_BUSY.format(why=e))
    except (
        Exception
    ) as e:  # hook boundary: an exit 1 would let a pre through (fail-open)
        log.exception("discipline.hook_failure")
        why = f"{type(e).__name__}: {e}"[:200]
        # What was known is saved (run_locked's finally); this event may be half applied.
        note_safely(path, f"{mode}: 처리 중 오류({why})")
        fail_closed(mode, data, REASON_HOOK_ERROR.format(why=why))


def note_safely(path: Path, why: str) -> None:
    """Remember a missed event; never raises (it runs on the failure path)."""
    try:
        import session_store

        session_store.note_missed(path, why)
    except Exception:  # the refusal that follows must still happen
        log.exception("discipline.note_missed_failed")


def blocks(mode: str, data: dict[str, Any]) -> bool:
    return mode == "pre" or (mode == "stop" and not data.get("stop_hook_active"))


def fail_closed(mode: str, data: dict[str, Any], reason: str) -> None:
    """Refuse what needs a trustworthy state: the write (pre) and the end of the turn (stop).
    If the refusal cannot be written, exit 2 (blocking for both events per the hook docs)."""
    if not blocks(mode, data):
        return
    try:
        if mode == "pre":
            emit_pre("deny", reason)
        emit_stop_block(reason)
    except Exception:  # SystemExit from emit_pre passes through untouched
        log.exception("discipline.refusal_output_failed")
        hard_block(mode, data, reason)


def hard_block(mode: str, data: dict[str, Any], reason: str) -> None:
    """Last resort: exit 2 with the reason on stderr for pre/stop, without flushing stdout
    (a failing flush at interpreter exit would turn the status into 120: non-blocking)."""
    if not blocks(mode, data):
        sys.exit(0)
    try:
        sys.stderr.write(reason + "\n")
        sys.stderr.flush()
    except (
        OSError,
        ValueError,
    ):  # nothing can be reported; the exit status still blocks
        log.warning("discipline.hard_block_stderr_failed")
    os._exit(2)


# ---------------------------------------------------------------- user state recovery
RECOVER_CMD = "recover-state"
REASON_RECOVERED = (
    "[discipline] 사용자 요청(recover-state)으로 세션 추적 상태를 복구했습니다. 원본은 {kept}로 보존했고 "
    "감사 기록은 {audit}에 있습니다. 미검증 범위 {roots}는 '세션 추적 상태 손상 또는 누락' 표식으로 남아, 그 게이트가 "
    "통과할 때까지 완료(Stop)를 막습니다. 승인은 0입니다. 작업을 이어가려면 사용자가 프롬프트 첫 줄에 task:<id>를 적어 "
    "재무장해야 합니다. 이 사실을 사용자에게 알리세요."
)
REASON_RECOVERY_FAILED = (
    "[discipline] 세션 추적 상태 복구(recover-state) 실패: {why}. 상태는 바뀌지 않았고 보호 대상 쓰기 차단도 그대로입니다. "
    "상태 파일을 직접 지우거나 고치지 말고 이 사실을 사용자에게 알리세요."
)


def recovery_requested(mode: str, data: dict[str, Any]) -> bool:
    """Only the user's own first line (never a relay, markup or a later line) asks for it."""
    prompt = data.get("prompt")
    if mode != "prompt" or not isinstance(prompt, str) or relay_shaped(prompt):
        return False
    return naming_text(prompt).strip().lower() == RECOVER_CMD


def audit_recovery(row: dict[str, Any]) -> bool:
    """Append one line to the recovery audit log (outside %TEMP%); False if it failed."""
    try:
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": time.time(), **row}, ensure_ascii=False) + "\n"
        with open(RECOVERY_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except (OSError, ValueError, TypeError):
        log.exception("discipline.recovery_audit_failed")
        return False
    return True


# ---------------------------------------------------------------- user acknowledgement
# `ack-missing <path>` on the user's own first line releases what no gate can ever clear: a
# tracked repository that is gone, or a reset-marker root that is gone or has no gate. It
# grants nothing and verifies nothing (no evidence is written); a repository that appears at
# that path later starts with a late baseline.
ACK_CMD = re.compile(r"^ack-missing\s+(\S.*)$", re.IGNORECASE)
MAX_ACKED = 50
REASON_ACKED = (
    "[discipline] 사용자 확인(ack-missing)으로 {path}의 미확인 표식을 해제했습니다({what}). 검증된 것이 아닙니다: "
    "그 안의 변경은 보고에 '미확인'으로 남기세요. 감사 기록은 {audit}에 있습니다."
)
REASON_ACK_REFUSED = "[discipline] ack-missing 거부: {why}. 상태는 바뀌지 않았습니다. 이 사실을 사용자에게 알리세요."


def ack_requested(data: dict[str, Any]) -> str | None:
    """The path named by `ack-missing <path>`: only on the user's own first line (never a
    relay, markup or a later line)."""
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or relay_shaped(prompt):
        return None
    asked = ACK_CMD.match(naming_text(prompt).strip())
    if not asked:
        return None
    return asked.group(1).strip().strip("\"'").strip() or None


def same_place(a: object, b: str) -> bool:
    return isinstance(a, str) and os.path.normcase(
        os.path.abspath(a)
    ) == os.path.normcase(os.path.abspath(b))


def gate_can_clear(root: str) -> bool:
    """The root exists and declares a gate, so passing that gate is how it is cleared."""
    from verification import find_policy

    if not os.path.isdir(root):
        return False
    try:
        policy = find_policy(os.path.join(root, "_"))
    except (OSError, ValueError):  # unreadable policy: no gate will ever be recorded
        return False
    return bool(policy["gate"]) and same_place(policy["root"], root)


def acked_before(state: dict[str, Any], top: str) -> bool:
    acked = state.get("acked_missing")
    return any(
        isinstance(row, dict) and same_place(row.get("path"), top)
        for row in (acked if isinstance(acked, list) else [])
    )


def ack_missing(data: dict[str, Any], state: dict[str, Any]) -> str | None:
    """Apply the user's `ack-missing <path>`; the text to tell Claude, or None if not asked."""
    asked = ack_requested(data)
    if asked is None:
        return None
    target = absolute(asked, str(data.get("cwd") or os.getcwd()))
    # Everything below edits these records in place: a malformed one is refused here, before
    # the audit line, so a release is never half applied.
    for key, kind in (
        ("content_removed", list),
        ("repos_removed", dict),
        ("repo_state", dict),
        ("dirty", dict),
        ("acked_missing", list),
    ):
        if key in state and not isinstance(state[key], kind):
            return REASON_ACK_REFUSED.format(
                why=f"세션 추적 상태의 {key} 형식이 올바르지 않습니다(recover-state로 먼저 복구)"
            )
    removed = [t for t in state.get("content_removed", []) if same_place(t, target)]
    kept = [t for t in state.get("repos_removed", {}) if same_place(t, target)]
    marker = state.get("state_reset") if state_reset_active(state) else None
    roots = marker.get("roots") if isinstance(marker, dict) else None
    in_marker = [r for r in (roots or []) if same_place(r, target)]
    if not (removed or kept or in_marker):
        return REASON_ACK_REFUSED.format(
            why=f"{target}는 사라진 저장소나 미확인 표식 범위로 기록된 경로가 아닙니다(정확한 경로만 받습니다)"
        )
    if in_marker and gate_can_clear(target):
        if not (removed or kept):
            return REASON_ACK_REFUSED.format(
                why=f"{target}는 존재하고 게이트가 선언돼 있어 게이트 통과로만 해제됩니다"
            )
        in_marker = []  # the missing-repository entry goes, the gated root stays
    if (removed or kept) and is_repo_root(target):
        return REASON_ACK_REFUSED.format(
            why=f"{target}에 저장소가 다시 있습니다. 원래 기준선과 비교해 검증(게이트)으로 해제됩니다"
        )
    what = [
        name
        for name, found in (
            ("content_removed", removed),
            ("repos_removed", kept),
            ("state_reset.roots", in_marker),
        )
        if found
    ]
    gone_files = [
        f
        for entry in state.get("dirty", {}).values()
        for f in (entry.get("files") or [] if isinstance(entry, dict) else [])
        if isinstance(f, str) and _inside(f, target) and not os.path.exists(f)
    ]
    if not audit_recovery(
        {
            "kind": "ack-missing",
            "session": data.get("session_id"),
            "prompt_id": data.get("prompt_id"),
            "path": target,
            "released": what,
            "edited_files_gone": len(gone_files),
        }
    ):
        return REASON_ACK_REFUSED.format(
            why="감사 기록을 남기지 못했습니다(감사 없는 해제 금지)"
        )
    for t in removed:
        state["content_removed"].remove(t)
    for t in kept:
        state["repos_removed"].pop(t, None)
        state.get("repo_state", {}).pop(t, None)
        forget_dir_tops(state, t)
    if in_marker and isinstance(marker, dict):
        marker["roots"] = [r for r in roots or [] if not same_place(r, target)]
    # Edits recorded for files that went with it can never be verified: they are part of what
    # the user acknowledged. Files that still exist keep demanding verification.
    # Only entries that listed such a file change. An entry whose list was cut at the limit may
    # hold edits it does not list: it is left whole (shrinking it would hide that it was cut,
    # and a later release could then drop it), so only its gate clears it.
    for root in list(state.get("dirty", {})):
        entry = state["dirty"][root]
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, list) or not any(f in gone_files for f in files):
            continue
        if len(files) >= MAX_DIRTY_FILES:
            continue
        entry["files"] = [f for f in files if f not in gone_files]
        if isinstance(entry.get("lines"), dict):
            for f in gone_files:
                entry["lines"].pop(f, None)
        if not entry["files"]:
            state["dirty"].pop(root)
    acked = state.setdefault("acked_missing", [])
    acked.append(
        {"path": target, "at": time.time(), "prompt_id": data.get("prompt_id")}
    )
    del acked[:-MAX_ACKED]
    return REASON_ACKED.format(
        path=target, what=", ".join(what), audit=RECOVERY_AUDIT_FILE
    )


RECOVERY_PLACE_LISTS = (
    "background_tops",
    "touched_tops",
    "missed_dirs",
    "turn_shell_tops",
    "content_removed",
    "content_unknown",
    "content_too_large",
    "content_late",
)
RECOVERY_PLACE_MAPS = ("dirty", "content", "repos", "repo_state", "repos_removed")


def recovery_roots(saved: object, cwd: str) -> tuple[set[str], list[str]]:
    """Every place the old state knew, as a root of the new marker. Only path strings are
    read (list items and dict keys); no structured value is trusted or carried, so nothing
    malformed can survive a recovery. A place with a gated policy becomes that policy root
    (its gate must pass again on the current content); any other place - no gate, removed,
    unreadable policy - stays as an unresolvable root, which only the user can release."""
    from verification import find_policy

    if not isinstance(saved, dict):
        return {unresolved_root(cwd)}, [
            "원본을 읽을 수 없음(작업 위치를 미확인 범위로 둠)"
        ]
    places: set[str] = set()
    for key in RECOVERY_PLACE_LISTS:
        value = saved.get(key)
        if isinstance(value, list):
            places |= {v for v in value if isinstance(v, str) and v}
    for key in RECOVERY_PLACE_MAPS:
        value = saved.get(key)
        if isinstance(value, dict):
            places |= {k for k in value if isinstance(k, str) and k}
            if "" in value:  # edits outside any declared policy
                places.add(cwd)
    roots: set[str] = set()
    reset = saved.get("state_reset")
    if isinstance(reset, dict) and isinstance(reset.get("roots"), list):
        roots |= {r for r in reset["roots"] if isinstance(r, str) and r}
    problems: list[str] = []
    for place in sorted(places):
        try:
            policy = find_policy(os.path.join(place, "_"))
        except (OSError, ValueError) as e:
            roots.add(unresolved_root(place))
            problems.append(f"범위 확인 실패({type(e).__name__}): {place}")
            continue
        if policy["root"] and policy["gate"] and os.path.isdir(place):
            roots.add(policy["root"])
        else:
            roots.add(unresolved_root(place))
    scope, scope_problems = reset_scope(None, cwd)  # this event's own project
    return roots | set(scope), problems + scope_problems


def finite_time(value: object) -> float | None:
    """A JSON number usable as a time, or None (also for NaN, infinities and integers too
    large for a float)."""
    import math

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def recovery_carry(saved: object) -> dict[str, Any]:
    """The flat session-only safety records, each taken only when fully well-formed:
    revocations the store could not record yet and the approval-store rollback witness."""
    if not isinstance(saved, dict):
        return {}
    carry: dict[str, Any] = {}
    pending = saved.get("pending_revocations")
    if isinstance(pending, dict):
        kept = {
            k: v
            for k, v in pending.items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if kept:
            carry["pending_revocations"] = kept
    gen = saved.get("approvals_seen_gen")
    if isinstance(gen, int) and not isinstance(gen, bool) and gen > 0:
        carry["approvals_seen_gen"] = gen
    # Paths the user acknowledged as missing: a repository appearing there later still gets a
    # late baseline. Rebuilt from well-formed rows only (path and time, nothing else).
    acked = saved.get("acked_missing")
    rows = [
        {"path": row["path"], "at": at}
        for row in (acked if isinstance(acked, list) else [])
        if isinstance(row, dict)
        and isinstance(row.get("path"), str)
        and row["path"]
        and (at := finite_time(row.get("at"))) is not None
    ]
    if rows:
        carry["acked_missing"] = rows[-MAX_ACKED:]
    # Repositories a background shell may still write to: no hook says when it ends, so the
    # record outlives a recovery (path strings only).
    late_writers = saved.get("background_tops")
    places = [
        t
        for t in (late_writers if isinstance(late_writers, list) else [])
        if isinstance(t, str) and t
    ]
    if places:
        carry["background_tops"] = places
    return carry


def recover_locked(data: dict[str, Any], path: Path) -> None:
    """The user's `recover-state`: keep a copy of the original, then replace the state
    atomically with defaults plus a reset marker over every place the old state knew.
    Nothing is granted, each step is audited, and the original stays in place until the
    new state is saved, so any failure leaves the state exactly as it was."""
    import hashlib

    import session_store

    cwd = str(data.get("cwd") or os.getcwd())
    try:
        raw: bytes | None = path.read_bytes()
    except FileNotFoundError:
        raw = None
    sha = hashlib.sha256(raw).hexdigest() if raw is not None else ""
    row = {
        "session": path.stem,
        "prompt_id": data.get("prompt_id"),
        "cwd": cwd,
        "sha256": sha,
        "size": len(raw) if raw is not None else 0,
    }
    if not audit_recovery({**row, "event": "started"}):
        emit_prompt_context(REASON_RECOVERY_FAILED.format(why="감사 기록을 쓸 수 없음"))
        return
    kept = session_store.keep_copy(path, raw, "recovered") if raw is not None else None
    if raw is not None and kept is None:
        audit_recovery({**row, "event": "failed", "why": "원본 사본을 만들 수 없음"})
        emit_prompt_context(
            REASON_RECOVERY_FAILED.format(why="원본 사본을 만들 수 없음")
        )
        return
    kept_name = kept.name if kept else "원본 없음"
    try:
        state, roots = recovered_state(data, raw, kept_name, sha, cwd)
        session_store.save(path, state)
    except Exception as e:  # the original is still in place; the boundary fails closed
        audit_recovery(
            {
                **row,
                "event": "failed",
                "why": f"{type(e).__name__}: {e}"[:200],
                "preserved": kept_name,
            }
        )
        if isinstance(e, session_store.StateWriteFailed):
            emit_prompt_context(REASON_RECOVERY_FAILED.format(why=f"저장 실패: {e}"))
            return
        raise
    audit_recovery(
        {**row, "event": "done", "preserved": kept_name, "roots": sorted(roots)}
    )
    emit_prompt_context(
        REASON_RECOVERED.format(
            kept=kept_name, audit=RECOVERY_AUDIT_FILE, roots=sorted(roots) or "없음"
        )
    )


def recovered_state(
    data: dict[str, Any], raw: bytes | None, kept_name: str, sha: str, cwd: str
) -> tuple[dict[str, Any], set[str]]:
    """The state that replaces the old one. Missed-event notes are left for the next run,
    which merges them into this marker."""
    try:
        saved: object = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except (ValueError, RecursionError):
        saved = None
    roots, problems = recovery_roots(saved, cwd)
    state = default_state()
    state.update(recovery_carry(saved))
    state["turn"] = 1
    state["prompt_id"] = data.get("prompt_id")
    state["recovered"] = {"at": time.time(), "preserved": kept_name, "sha256": sha}
    why = [
        f"사용자 복구(recover-state): 보존 {kept_name}, sha256 {sha[:12] or '-'}",
        *problems,
    ]
    state["state_reset"] = {
        "at": time.time(),
        "why": "; ".join(why)[:300],
        "roots": sorted(roots),
    }
    return state, roots


def emit_prompt_context(text: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": text,
                }
            }  # ASCII escapes: a piped stdout on Windows is not UTF-8
        )
    )


SEEN_EVENTS = 200


def run_locked(mode: str, data: dict[str, Any], handler: Any, path: Path) -> None:
    """Load (damage is preserved and turns into a reset marker), handle once per event, save
    atomically (a failed save is remembered as a missed event)."""
    import session_store
    import worktree

    state = default_state()
    why: list[str] = []
    try:
        saved = session_store.load(path)
    except session_store.StateCorrupt as e:
        saved = None
        why.append(f"{e}, 보존: {e.preserved.name if e.preserved else '이동 실패'}")
    problem = state_shape_problem(saved) if saved else None
    if problem:  # well-formed JSON the code cannot use: as damaged as unreadable JSON
        kept = session_store.preserve(path)
        saved = None
        why.append(
            f"세션 추적 상태 손상(형식: {problem}), 보존: {kept.name if kept else '이동 실패'}"
        )
    if saved:
        state.update(saved)
    # From here on every step runs under the save below: an exception anywhere (notes
    # already taken, marker half computed, handler) still persists what is known, and the
    # loaded state is never replaced by defaults.
    try:
        why += session_store.take_missed(path)
        if why:
            mark_reset(state, saved, why, str(data.get("cwd") or os.getcwd()))
        # Every git call of this run shares one budget, cut to what is left before the
        # hook's deadline after the lock wait, so the hook decides (closed) instead of being
        # killed.
        budget = os.environ.get("CLAUDE_DISCIPLINE_GIT_BUDGET_S")
        try:
            seconds = float(budget) if budget else GIT_BUDGET_S[mode]
        except ValueError:  # malformed override: use the default
            seconds = GIT_BUDGET_S[mode]
        worktree.set_budget(git_budget_s(mode, time.monotonic() - _T0, seconds))
        event = f"{mode}|{data.get('tool_use_id')}" if data.get("tool_use_id") else None
        seen: list[str] = state.setdefault("seen_events", [])
        if event and mode in ("post", "fail") and event in seen:
            return  # the same tool event delivered again: already applied
        if event:
            seen.append(event)
            del seen[:-SEEN_EVENTS]
        handler(data, state)
    finally:
        try:
            session_store.save(path, state)
        except session_store.StateWriteFailed as e:
            note_safely(path, f"{mode}: {e}")


if __name__ == "__main__":
    main()
