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

VERIFY_CMD = re.compile(
    r"\b(pytest|vitest|jest|mocha|tsc|pyright|mypy|ruff|eslint|prettier|Invoke-Pester|Invoke-ScriptAnalyzer|"
    r"cargo\s+(test|check|clippy)|go\s+(test|vet|build)|dotnet\s+(test|build)|"
    r"(pnpm|npm|yarn)\s+(run\s+)?(test|lint|typecheck|check|build|test:\w+)|"
    r"uv\s+run|node\s+\S+\.(m?js|cjs)|python3?\s+\S+\.py|tsx\s+\S+|make\s+(test|check))\b",
    re.IGNORECASE,
)
VERIFY_AGENTS = {"test-runner", "code-reviewer"}

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
        "last_verify": 0.0,
        "last_verify_ok": True,
        "fail_notified": None,  # {"verify": <last_verify>, "turn": <turn>} once blocked for it
        "plan_approved": False,
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

    # 1) Large change without an approved plan.
    files = set(state["turn_files"])
    if (
        path not in files
        and not state.get("plan_approved")
        and len(files) >= MAX_FILES_WITHOUT_PLAN
    ):
        emit_pre(
            "deny",
            f"[discipline] 이번 턴에 이미 {len(files)}개 파일을 수정했습니다(한도 {MAX_FILES_WITHOUT_PLAN}). "
            "더 진행하기 전에 /plan 으로 계획을 세우고 ExitPlanMode 로 사용자 승인을 받으세요.",
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
    elif is_verification(tool, ti):
        state["last_verify"] = now
        state["last_verify_ok"] = True
    elif tool == "ExitPlanMode":
        state["plan_approved"] = (
            True  # PostToolUse only fires once the user approved the plan
        )


def is_verification(tool: str, ti: dict[str, Any]) -> bool:
    if tool in ("Bash", "PowerShell"):
        return bool(VERIFY_CMD.search(ti.get("command", "")))
    if tool in ("Agent", "Task"):
        return ti.get("subagent_type") in VERIFY_AGENTS
    return False


def on_fail(data: dict[str, Any], state: dict[str, Any]) -> None:
    """PostToolUseFailure: a failed test/lint run is still a verification attempt."""
    if is_verification(data.get("tool_name", ""), data.get("tool_input", {}) or {}):
        state["last_verify"] = time.time()
        state["last_verify_ok"] = False


def on_prompt(_data: dict[str, Any], state: dict[str, Any]) -> None:
    state["turn"] = int(state.get("turn", 0)) + 1
    state["turn_files"] = []


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


def failure_pending(state: dict[str, Any]) -> bool:
    """Last verification (after the last code edit) failed and was not yet flagged in an earlier turn."""
    last_verify = float(state.get("last_verify", 0))
    last_edit = float(state.get("last_code_edit", 0))
    if last_edit <= 0 or last_verify < last_edit or state.get("last_verify_ok", True):
        return False
    notified = state.get("fail_notified")
    if not isinstance(notified, dict) or notified.get("verify") != last_verify:
        return True
    # Already flagged for this failure: block again only within the same turn (the
    # parallel notify hook may read the state after discipline wrote the flag).
    return notified.get("turn") == state.get("turn")


def stop_block_reason(state: dict[str, Any], stop_hook_active: bool) -> str | None:
    """Pure decision shared with notify.py: why this Stop is blocked, or None."""
    if stop_hook_active:
        return None  # already blocked once this turn; let Claude stop
    if float(state.get("last_code_edit", 0)) > float(state.get("last_verify", 0)):
        return REASON_UNVERIFIED
    if failure_pending(state):
        return REASON_FAILED
    return None


def on_stop(data: dict[str, Any], state: dict[str, Any]) -> None:
    reason = stop_block_reason(state, bool(data.get("stop_hook_active")))
    if reason is None:
        return
    if reason == REASON_FAILED:
        state["fail_notified"] = {
            "verify": state["last_verify"],
            "turn": state.get("turn"),
        }
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
