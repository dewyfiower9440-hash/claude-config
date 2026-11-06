"""Scenario tests for ~/.claude/hooks/discipline.py (run with python -I)."""

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "hooks" / "discipline.py")
TMP = Path(tempfile.mkdtemp(prefix="disc-"))
fails = 0


def call(mode: str, session: str, **payload: object) -> dict | None:
    p = subprocess.run(
        [sys.executable, "-I", HOOK, mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def decision(out: dict | None) -> str | None:
    if out is None:
        return None
    if "hookSpecificOutput" in out:
        return out["hookSpecificOutput"]["permissionDecision"]
    return out.get("decision")


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(("OK  " if ok else "FAIL"), name, "->", got, "" if ok else f"(want {want})")


def edit(session: str, path: str, old: str, new: str, mode: str = "pre") -> dict | None:
    return call(
        mode,
        session,
        tool_name="Edit",
        tool_input={"file_path": path, "old_string": old, "new_string": new},
    )


# Paths outside %TEMP% so they are not ignored.
PROJ = "C:/dev/_disc_test_project"

# ---- weakening
s = uuid.uuid4().hex
check(
    "plain edit allowed", decision(edit(s, f"{PROJ}/src/a.py", "x = 1", "x = 2")), None
)
check(
    "noqa added -> ask",
    decision(edit(s, f"{PROJ}/src/a.py", "import os", "import os  # noqa: F401")),
    "ask",
)
check(
    "type: ignore added -> ask",
    decision(edit(s, f"{PROJ}/src/a.py", "y = f()", "y = f()  # type: ignore")),
    "ask",
)
check(
    "existing noqa kept -> allowed",
    decision(edit(s, f"{PROJ}/src/a.py", "a  # noqa\nb", "a  # noqa\nc")),
    None,
)
check(
    "pytest skip in test -> ask",
    decision(
        edit(
            s,
            f"{PROJ}/tests/test_a.py",
            "def test_x():",
            "@pytest.mark.skip\ndef test_x():",
        )
    ),
    "ask",
)
check(
    "assert removed in test -> ask",
    decision(edit(s, f"{PROJ}/tests/test_a.py", "    assert f() == 1\n", "    f()\n")),
    "ask",
)
check(
    "assert changed (same count) -> allowed",
    decision(edit(s, f"{PROJ}/tests/test_a.py", "assert f() == 1", "assert f() == 2")),
    None,
)
check(
    "it.only in ts test -> ask",
    decision(
        edit(s, f"{PROJ}/src/a.test.ts", 'it("x", () => {', 'it.only("x", () => {')
    ),
    "ask",
)
check(
    "eslint-disable -> ask",
    decision(
        edit(
            s,
            f"{PROJ}/src/a.ts",
            "const a = b;",
            "// eslint-disable-next-line\nconst a = b;",
        )
    ),
    "ask",
)
check(
    "Pester -Skip -> ask",
    decision(
        edit(s, f"{PROJ}/tests/X.Tests.ps1", "It 'works' {", "It 'works' -Skip {")
    ),
    "ask",
)
check(
    "rm test file -> ask",
    decision(
        call("pre", s, tool_name="Bash", tool_input={"command": "rm tests/test_a.py"})
    ),
    "ask",
)
check(
    "rm build dir -> allowed",
    decision(call("pre", s, tool_name="Bash", tool_input={"command": "rm -rf dist"})),
    None,
)

# ---- large change
s = uuid.uuid4().hex
call("prompt", s)
for i in range(5):
    edit(s, f"{PROJ}/src/f{i}.py", "a", "b", mode="post")
check(
    "6th distinct file without plan -> deny",
    decision(edit(s, f"{PROJ}/src/f5.py", "a", "b")),
    "deny",
)
check(
    "re-editing a known file -> allowed",
    decision(edit(s, f"{PROJ}/src/f0.py", "a", "b")),
    None,
)
check(
    "temp file not counted -> allowed",
    decision(edit(s, str(TMP / "scratch.py"), "a", "b")),
    None,
)
call("post", s, tool_name="ExitPlanMode", tool_input={})
check(
    "after plan approval -> allowed",
    decision(edit(s, f"{PROJ}/src/f5.py", "a", "b")),
    None,
)
s2 = uuid.uuid4().hex
for i in range(5):
    edit(s2, f"{PROJ}/src/g{i}.py", "a", "b", mode="post")
call("prompt", s2)
check(
    "new turn resets count -> allowed",
    decision(edit(s2, f"{PROJ}/src/g9.py", "a", "b")),
    None,
)

# ---- stop verification
s = uuid.uuid4().hex
check("stop with no edits -> allowed", decision(call("stop", s)), None)
edit(s, f"{PROJ}/src/a.py", "a", "b", mode="post")
check("stop after code edit, no verify -> block", decision(call("stop", s)), "block")
check(
    "stop_hook_active -> allowed",
    decision(call("stop", s, stop_hook_active=True)),
    None,
)
call("post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q"})
check("stop after pytest -> allowed", decision(call("stop", s)), None)
edit(s, f"{PROJ}/README.md", "a", "b", mode="post")
check("doc-only edit after verify -> allowed", decision(call("stop", s)), None)
edit(s, f"{PROJ}/src/b.ts", "a", "b", mode="post")
call(
    "post",
    s,
    tool_name="Agent",
    tool_input={"subagent_type": "test-runner", "prompt": "run"},
)
check("test-runner agent counts as verify", decision(call("stop", s)), None)
edit(s, f"{PROJ}/src/c.ps1", "a", "b", mode="post")
call("post", s, tool_name="PowerShell", tool_input={"command": "Get-ChildItem"})
check("non-verify command does not count -> block", decision(call("stop", s)), "block")


# ---- verification failures (PostToolUseFailure)
def reason(out: dict | None) -> str:
    return (out or {}).get("reason", "")


def state_of(session: str) -> dict:
    return json.loads(
        (
            Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
        ).read_text(encoding="utf-8")
    )


s = uuid.uuid4().hex
call("prompt", s)
edit(s, f"{PROJ}/src/a.py", "a", "b", mode="post")
call(
    "fail",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q"},
    error="Exit code 1",
)
st = state_of(s)
check("failed pytest recorded as verify attempt", st["last_verify"] > 0, True)
check("failed pytest -> last_verify_ok False", st["last_verify_ok"], False)
out = call("stop", s)
check("stop after failed verify -> block", decision(out), "block")
check("failed-verify block says report failure", "실패" in reason(out), True)
check(
    "failed-verify reason differs from unverified",
    "실행하지 않았습니다" in reason(out),
    False,
)
check(
    "failed verify, second stop (stop_hook_active) -> allowed",
    decision(call("stop", s, stop_hook_active=True)),
    None,
)
call("prompt", s)
check("same failure, next turn -> not blocked again", decision(call("stop", s)), None)
call(
    "fail",
    s,
    tool_name="Bash",
    tool_input={"command": "ls missing-dir"},
    error="Exit code 2",
)
check(
    "failed non-verify command ignored",
    state_of(s)["last_verify"] == st["last_verify"],
    True,
)
call("post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q"})
check("passing verify -> last_verify_ok True", state_of(s)["last_verify_ok"], True)
check("stop after passing verify -> allowed", decision(call("stop", s)), None)
edit(s, f"{PROJ}/src/a.py", "b", "c", mode="post")
call(
    "fail",
    s,
    tool_name="Agent",
    tool_input={"subagent_type": "test-runner"},
    error="boom",
)
check("new failure via test-runner agent -> block", decision(call("stop", s)), "block")
edit(s, f"{PROJ}/src/a.py", "c", "d", mode="post")
out = call("stop", s)
check(
    "edit after failed verify -> unverified reason",
    "실행하지 않았습니다" in reason(out),
    True,
)

s = uuid.uuid4().hex
call(
    "fail",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q"},
    error="Exit code 1",
)
check("failed verify without code edits -> allowed", decision(call("stop", s)), None)

# ---- notify: no toast when discipline will block, toast on the final stop
sys.path.insert(0, str(Path(HOOK).parent))
import discipline
import notify

blocking = {"last_code_edit": 2.0, "last_verify": 1.0}
check(
    "pure stop_block_reason: unverified edit -> reason",
    discipline.stop_block_reason(blocking, stop_hook_active=False) is not None,
    True,
)
check(
    "pure stop_block_reason: stop_hook_active -> None",
    discipline.stop_block_reason(blocking, stop_hook_active=True),
    None,
)
check(
    "notify: blocked stop -> no toast",
    notify.should_toast_on_stop(block_reason="x", elapsed=999.0),
    False,
)
check(
    "notify: long final stop -> toast",
    notify.should_toast_on_stop(block_reason=None, elapsed=999.0),
    True,
)
check(
    "notify: short turn -> no toast",
    notify.should_toast_on_stop(block_reason=None, elapsed=1.0),
    False,
)
check(
    "notify: unknown start -> no toast",
    notify.should_toast_on_stop(block_reason=None, elapsed=None),
    False,
)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
