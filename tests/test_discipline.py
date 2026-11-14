"""Scenario tests for ~/.claude/hooks/discipline.py (run with python -I)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "hooks" / "discipline.py")
# Plan approvals made by these tests go to an isolated store, never the real one.
APPROVAL_HOME = Path(__file__).resolve().parent / "_tmp_discipline_approvals"
shutil.rmtree(APPROVAL_HOME, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(APPROVAL_HOME)
TMP = Path(tempfile.mkdtemp(prefix="disc-"))
fails = 0


def call(mode: str, session: str, **payload: object) -> dict | None:
    if (
        mode in ("post", "fail")
        and payload.get("tool_name") in ("Bash", "PowerShell")
        and "tool_use_id" not in payload
    ):
        # As in the real harness, every shell PostToolUse(Failure) follows its PreToolUse
        # with the same tool_use_id. Tests that pass a tool_use_id drive PreToolUse themselves.
        payload["tool_use_id"] = uuid.uuid4().hex
        call("pre", session, **payload)
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


# ---- D-2: approval bound to task_id + plan hash + approved scope
def d2_edit(
    session: str, path: str, prompt_id: str, cwd: str, mode: str = "pre"
) -> dict | None:
    return call(
        mode,
        session,
        tool_name="Edit",
        tool_input={"file_path": path, "old_string": "a", "new_string": "b"},
        prompt_id=prompt_id,
        cwd=cwd,
    )


def d2_prompt(session: str, prompt_id: str, cwd: str) -> None:
    # Re-arming needs the prompt to name the task (P0-03); every D-2 prompt names it, so the
    # negative cases below are refused for their own reason, not for a missing task.
    call(
        "prompt",
        session,
        prompt_id=prompt_id,
        prompt="task:widget-api continue",
        cwd=cwd,
    )


def approve(session: str, plan: str, prompt_id: str, cwd: str) -> None:
    call(
        "post",
        session,
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": plan, "filePath": "C:/x/plans/p.md", "isAgent": False},
        prompt_id=prompt_id,
        cwd=cwd,
    )


D2 = Path(__file__).resolve().parent / "_tmp_d2"
shutil.rmtree(D2, ignore_errors=True)
(D2 / "docs" / "plans").mkdir(parents=True)
# Its own git root: approvals re-arm only from the cwd's git toplevel, and without this the
# toplevel would be the enclosing ~/.claude repository.
subprocess.run(["git", "init", "-q"], cwd=D2, check=True)
REPO2 = str(D2)
PLAN_BODY = (
    "# Widget\n\ntask: widget-api\n\n## 목표\nShip it.\n\n## 변경 범위\n- src/**\n- tests/**\n\n"
    "## 단계\n- [ ] 1. first\n- [ ] 2. second\n"
)
PLAN_FILE = D2 / "docs" / "plans" / "2026-11-06-widget.md"


def save_plan(body: str, status: str = "in-progress") -> None:
    PLAN_FILE.write_text(f"---\nstatus: {status}\n---\n\n{body}", encoding="utf-8")


def fill_outside(session: str, prompt_id: str, n: int = 5) -> None:
    for i in range(n):
        d2_edit(session, f"{REPO2}/other/o{i}.py", prompt_id, REPO2, mode="post")


s = uuid.uuid4().hex
d2_prompt(s, "p1", REPO2)
approve(s, PLAN_BODY, "p1", REPO2)
for i in range(7):
    d2_edit(s, f"{REPO2}/src/m{i}.py", "p1", REPO2, mode="post")
check(
    "D-2: same prompt, files in approved scope are not counted -> allowed",
    decision(d2_edit(s, f"{REPO2}/src/m9.py", "p1", REPO2)),
    None,
)
fill_outside(s, "p1")
check(
    "D-2: same prompt, 6th file outside the approved scope -> deny",
    decision(d2_edit(s, f"{REPO2}/other/o9.py", "p1", REPO2)),
    "deny",
)

s = uuid.uuid4().hex
d2_prompt(s, "q1", REPO2)
approve(s, PLAN_BODY, "q1", REPO2)
d2_prompt(s, "q2", REPO2)  # no in-progress plan file exists yet
fill_outside(s, "q2", 0)
for i in range(5):
    d2_edit(s, f"{REPO2}/src/n{i}.py", "q2", REPO2, mode="post")
check(
    "D-2: next prompt without an in-progress plan -> approval not reused -> deny",
    decision(d2_edit(s, f"{REPO2}/src/n9.py", "q2", REPO2)),
    "deny",
)

save_plan(
    PLAN_BODY.replace("- [ ] 1. first", "- [x] 1. first")
)  # same plan, a step ticked
s = uuid.uuid4().hex
d2_prompt(s, "r1", REPO2)
approve(s, PLAN_BODY, "r1", REPO2)
d2_prompt(s, "r2", REPO2)
for i in range(6):
    d2_edit(s, f"{REPO2}/src/k{i}.py", "r2", REPO2, mode="post")
check(
    "D-2: next prompt, same in-progress plan (checkbox ticked) -> re-armed -> allowed",
    decision(d2_edit(s, f"{REPO2}/src/k9.py", "r2", REPO2)),
    None,
)
fill_outside(s, "r2")
check(
    "D-2: re-armed approval still counts files outside scope -> deny",
    decision(d2_edit(s, f"{REPO2}/other/z.py", "r2", REPO2)),
    "deny",
)

s_resume = (
    uuid.uuid4().hex
)  # brand-new session id (e.g. after /clear), same plan on disk
d2_prompt(s_resume, "u1", REPO2)
for i in range(6):
    d2_edit(s_resume, f"{REPO2}/tests/t{i}.py", "u1", REPO2, mode="post")
check(
    "D-2: new session resumes the same in-progress plan -> allowed",
    decision(d2_edit(s_resume, f"{REPO2}/tests/t9.py", "u1", REPO2)),
    None,
)

save_plan(
    PLAN_BODY.replace("2. second", "2. second, plus a new endpoint")
)  # plan edited after approval
s = uuid.uuid4().hex
d2_prompt(s, "v1", REPO2)
for i in range(5):
    d2_edit(s, f"{REPO2}/src/e{i}.py", "v1", REPO2, mode="post")
check(
    "D-2: plan steps changed after approval -> not re-armed -> deny",
    decision(d2_edit(s, f"{REPO2}/src/e9.py", "v1", REPO2)),
    "deny",
)

save_plan(PLAN_BODY, status="done")
s = uuid.uuid4().hex
d2_prompt(s, "w1", REPO2)
for i in range(5):
    d2_edit(s, f"{REPO2}/src/d{i}.py", "w1", REPO2, mode="post")
check(
    "D-2: plan marked done -> not re-armed -> deny",
    decision(d2_edit(s, f"{REPO2}/src/d9.py", "w1", REPO2)),
    "deny",
)

OTHER = PLAN_BODY.replace("task: widget-api", "task: billing").replace(
    "Ship it.", "Bill it."
)
save_plan(OTHER)  # a different task's plan is in progress; only widget-api was approved
s = uuid.uuid4().hex
d2_prompt(s, "x1", REPO2)
for i in range(5):
    d2_edit(s, f"{REPO2}/src/b{i}.py", "x1", REPO2, mode="post")
check(
    "D-2: approval of one plan never re-arms another task -> deny",
    decision(d2_edit(s, f"{REPO2}/src/b9.py", "x1", REPO2)),
    "deny",
)

s = uuid.uuid4().hex
d2_prompt(s, "y1", REPO2)
call(
    "post",
    s,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={},
    prompt_id="y1",
    cwd=REPO2,
)
for i in range(6):
    d2_edit(s, f"{REPO2}/any/a{i}.py", "y1", REPO2, mode="post")
check(
    "D-2: approval without plan text -> valid for that prompt only (allowed now)",
    decision(d2_edit(s, f"{REPO2}/any/a9.py", "y1", REPO2)),
    None,
)
d2_prompt(s, "y2", REPO2)
for i in range(5):
    d2_edit(s, f"{REPO2}/any/c{i}.py", "y2", REPO2, mode="post")
check(
    "D-2: ... and not reused on the next prompt -> deny",
    decision(d2_edit(s, f"{REPO2}/any/c9.py", "y2", REPO2)),
    "deny",
)
shutil.rmtree(D2, ignore_errors=True)

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
check(
    "test-runner agent claim alone is not evidence -> block",
    decision(call("stop", s)),
    "block",
)
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q"},
    agent_id="a1",
    agent_type="test-runner",
)
check(
    "subagent's own pytest run is evidence -> allowed", decision(call("stop", s)), None
)
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


def evidence(session: str, root: str = "") -> dict:
    """Verification evidence for a project root ('' = no declared policy)."""
    return state_of(session).get("evidence", {}).get(root, {})


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
st = evidence(s)
check("failed pytest recorded as verify attempt", st.get("at", 0) > 0, True)
check("failed pytest -> outcome failed", st.get("outcome"), "failed")
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
    evidence(s).get("at") == st.get("at"),
    True,
)
call("post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q"})
check("passing verify -> outcome passed", evidence(s).get("outcome"), "passed")
check("stop after passing verify -> allowed", decision(call("stop", s)), None)
edit(s, f"{PROJ}/src/a.py", "b", "c", mode="post")
call(
    "fail",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q"},
    error="Exit code 1",
    agent_id="a1",
    agent_type="test-runner",
)
check("new failure inside a subagent -> block", decision(call("stop", s)), "block")
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


# ---- D-1a: results must be attributable, reviews are not verification
def fresh(path: str = f"{PROJ}/src/a.py") -> str:
    sid = uuid.uuid4().hex
    call("prompt", sid)
    edit(sid, path, "a", "b", mode="post")
    return sid


s = fresh()
call("post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q | tail -3"})
out = call("stop", s)
check("pytest piped to tail -> block (result unknown)", decision(out), "block")
check("unknown reason names the cause", "확인할 수 없습니다" in reason(out), True)
check(
    "unknown, stop_hook_active -> allowed",
    decision(call("stop", s, stop_hook_active=True)),
    None,
)
call("prompt", s)
check(
    "same unknown result, next turn -> not blocked again",
    decision(call("stop", s)),
    None,
)

s = fresh()
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "set -o pipefail; uv run pytest -q | tail -3"},
)
check(
    "pipefail makes the piped result attributable -> allowed",
    decision(call("stop", s)),
    None,
)

s = fresh()
call("post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q; echo done"})
check("pytest followed by ; -> block (masked)", decision(call("stop", s)), "block")

s = fresh()
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "cd x && uv run pytest -q && echo ok"},
)
check("&& chain keeps the exit status -> allowed", decision(call("stop", s)), None)

s = fresh()
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q", "run_in_background": True},
    tool_response={"stdout": "", "stderr": "", "backgroundTaskId": "b1"},
)
check("backgrounded pytest -> block (not finished)", decision(call("stop", s)), "block")

s = fresh()
call("post", s, tool_name="Bash", tool_input={"command": "python -I scratch_probe.py"})
check(
    "arbitrary python script is not verification -> block",
    decision(call("stop", s)),
    "block",
)

s = fresh()
call(
    "post",
    s,
    tool_name="Agent",
    tool_input={"subagent_type": "code-reviewer", "prompt": "review"},
)
check(
    "code-reviewer alone is not verification -> block",
    decision(call("stop", s)),
    "block",
)
check("code-reviewer recorded as review", state_of(s).get("last_review", 0) > 0, True)

# ---- D-1a / FC-2: a declared project gate must be the thing that passed
ROOT = Path(__file__).resolve().parent / "_tmp_disc_project"
(ROOT / "src").mkdir(parents=True, exist_ok=True)
(ROOT / "CLAUDE.md").write_text(
    "# demo\n\n## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n\n## Gotchas\n",
    encoding="utf-8",
)
R = str(ROOT)
s = fresh(f"{R}/src/m.py")
call("post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q"}, cwd=R)
out = call("stop", s)
check("FC-2: tests pass but declared gate not run -> block", decision(out), "block")
check("FC-2 reason names the declared gate", "pytest -q --cov" in reason(out), True)
call(
    "post", s, tool_name="Bash", tool_input={"command": "uv run pytest -q --cov"}, cwd=R
)
check(
    "declared gate passed in project root -> allowed", decision(call("stop", s)), None
)

s = fresh(f"{R}/src/m.py")
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
    cwd=str(ROOT.parent),
)
check(
    "gate run in another directory does not count -> block",
    decision(call("stop", s)),
    "block",
)
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": f'cd "{R}" && uv run pytest -q --cov'},
    cwd=str(ROOT.parent),
)
check("cd into the project then gate -> allowed", decision(call("stop", s)), None)

LATE = ROOT.parent / "_tmp_disc_late"
(LATE / "src").mkdir(parents=True, exist_ok=True)
s = fresh(f"{LATE}/src/m.py")  # edited while the project has no policy yet
(LATE / "CLAUDE.md").write_text(
    "## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n", encoding="utf-8"
)
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
    cwd=str(LATE),
)
check(
    "policy declared after the edit is picked up -> allowed",
    decision(call("stop", s)),
    None,
)
check(
    "edit is regrouped under the nearer new policy",
    list(state_of(s)["dirty"]),
    [str(LATE)],
)
shutil.rmtree(LATE, ignore_errors=True)

s = fresh(f"{R}/src/m.py")
call(
    "fail",
    s,
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
    cwd=R,
    error="Exit code 1",
)
out = call("stop", s)
check("declared gate failed -> block with failure reason", "실패" in reason(out), True)


# ---- E-1: independent-review switch (off by default; A/B arm B turns it on)
def gate_passed(session: str) -> None:
    call(
        "post",
        session,
        tool_name="Bash",
        tool_input={"command": "uv run pytest -q --cov"},
        cwd=R,
    )


def edits(session: str, paths: list[str], new: str = "b") -> None:
    for p in paths:
        call(
            "post",
            session,
            tool_name="Edit",
            tool_input={"file_path": p, "old_string": "a", "new_string": new},
        )


os.environ["CLAUDE_REVIEW_GATE"] = "1"
s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/src/a.py", f"{R}/src/b.py", f"{R}/src/c.py"])
gate_passed(s)
out = call("stop", s)
check("E-1 on: 3 code files, gate passed, no review -> block", decision(out), "block")
check("E-1 reason asks for code-reviewer", "code-reviewer" in reason(out), True)
call(
    "post",
    s,
    tool_name="Agent",
    tool_input={"subagent_type": "code-reviewer", "prompt": "review"},
)
check("E-1 on: after code-reviewer -> allowed", decision(call("stop", s)), None)

s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/src/a.py"])
gate_passed(s)
check("E-1 on: one small file -> no review needed", decision(call("stop", s)), None)

s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/hooks/x.py"])
gate_passed(s)
check(
    "E-1 on: risky path (hooks/) -> review needed", decision(call("stop", s)), "block"
)

s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/src/a.py", f"{R}/tests/test_a.py"])
gate_passed(s)
check(
    "E-1 on: tests and implementation changed together -> review needed",
    decision(call("stop", s)),
    "block",
)

s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/src/big.py"], new="\n".join(f"line {i}" for i in range(160)))
gate_passed(s)
check("E-1 on: 150+ changed lines -> review needed", decision(call("stop", s)), "block")

s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/src/a.py", f"{R}/src/b.py", f"{R}/src/c.py"])
call(
    "post",
    s,
    tool_name="Agent",
    tool_input={"subagent_type": "code-reviewer", "prompt": "review"},
)
out = call("stop", s)
check(
    "E-1 on: review is never completion evidence (gate not run) -> unverified",
    "실행하지 않았습니다" in reason(out),
    True,
)

del os.environ["CLAUDE_REVIEW_GATE"]
s = uuid.uuid4().hex
call("prompt", s)
edits(s, [f"{R}/src/a.py", f"{R}/src/b.py", f"{R}/src/c.py"])
gate_passed(s)
check("E-1 off (default): no review requirement", decision(call("stop", s)), None)

shutil.rmtree(ROOT, ignore_errors=True)

# ---- notify: no toast when discipline will block, toast on the final stop
sys.path.insert(0, str(Path(HOOK).parent))
# importlib, not import statements: ruff classifies `discipline` as first-party only where a
# `discipline/` folder exists (the approval home in ~/.claude), so the sorted order differed
# between ~/.claude and a clean checkout (CI).
import importlib

notify = importlib.import_module("notify")
discipline = importlib.import_module("discipline")

blocking = {
    "dirty": {"": {"at": 2.0, "gate": None, "ci": "undeclared"}},
    "evidence": {},
}
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

# ---- P0-04b: path classes. A git worktree under %TEMP% is code like any repository; only a
# non-git temp folder (scratch) stays out of scope. Links never move a path into or out of it.
PB = Path("C:/dev/_disc_p004b")  # outside %TEMP%


def rm_tree(p: Path) -> None:
    """Remove a test tree, including git's read-only object files (Windows)."""
    import stat

    def writable(
        func: Callable[[str], object], target: str, _exc: BaseException
    ) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if p.exists():
        shutil.rmtree(p, onexc=writable)


rm_tree(PB)
REPO_A = PB / "repoA"
REPO_A.mkdir(parents=True)
for args in (
    ["init", "-q"],
    ["config", "user.email", "t@t"],
    ["config", "user.name", "t"],
):
    subprocess.run(["git", *args], cwd=REPO_A, check=True)
(REPO_A / "README").write_text("x\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=REPO_A, check=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=REPO_A, check=True)
WT_B = TMP / "wtB"
subprocess.run(
    ["git", "worktree", "add", "-q", "--detach", str(WT_B)], cwd=REPO_A, check=True
)
PLAIN_C = TMP / "plainC"
PLAIN_C.mkdir()
LINK = TMP / "link-to-repoA"
subprocess.run(
    ["cmd", "/c", "mklink", "/J", str(LINK), str(REPO_A)],
    capture_output=True,
    check=True,
)


def classes_run(root: Path, sixth: str) -> tuple[str | None, str | None]:
    """(6th unapproved code file, first-edit assertion removal) for a fresh session in `root`."""
    s = uuid.uuid4().hex
    call("prompt", s, prompt_id="c1", prompt="small change", cwd=str(root))
    for i in range(5):
        call(
            "post",
            s,
            prompt_id="c1",
            cwd=str(root),
            tool_name="Edit",
            tool_input={"file_path": str(root / "src" / f"f{i}.py")},
        )
    six = decision(
        call(
            "pre",
            s,
            prompt_id="c1",
            cwd=str(root),
            tool_name="Edit",
            tool_input={"file_path": sixth, "old_string": "a", "new_string": "b"},
        )
    )
    w = uuid.uuid4().hex
    call("prompt", w, prompt_id="c2", prompt="small change", cwd=str(root))
    weak = decision(
        call(
            "pre",
            w,
            prompt_id="c2",
            cwd=str(root),
            tool_name="Edit",
            tool_input={
                "file_path": str(root / "tests" / "test_x.py"),
                "old_string": "    assert f() == 1\n",
                "new_string": "    pass\n",
            },
        )
    )
    return six, weak


check(
    "R10 normal git repo: 6th unapproved file denied, assertion removal asks",
    classes_run(REPO_A, str(REPO_A / "src" / "f5.py")),
    ("deny", "ask"),
)
check(
    "R11 git worktree under %TEMP%: same policy as R10",
    classes_run(WT_B, str(WT_B / "src" / "f5.py")),
    ("deny", "ask"),
)
check(
    "R12 non-git temp folder: still out of scope (scratch)",
    classes_run(PLAIN_C, str(PLAIN_C / "src" / "f5.py")),
    (None, None),
)
check(
    "R14 junction under %TEMP% into a repository: not a way out",
    classes_run(LINK, str(LINK / "src" / "f5.py")),
    ("deny", "ask"),
)
check(
    "R14 worktree: upper-case path, a relative path and a file in a new folder are still protected",
    (
        classes_run(WT_B, str(WT_B / "src" / "f5.py").upper())[0],
        classes_run(WT_B, "src/rel.py")[0],
        classes_run(WT_B, str(WT_B / "new" / "deeper" / "n.py"))[0],
    ),
    ("deny", "deny", "deny"),
)

# ---- P0-04b: a tracked repository that disappeared (deleted worktree) is reported once as
# unverifiable; its baseline is kept (compared again if it comes back), never counted verified.
WT_D = TMP / "wtD"
subprocess.run(
    ["git", "worktree", "add", "-q", "--detach", str(WT_D)], cwd=REPO_A, check=True
)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="d1", prompt="work", cwd=str(WT_D))
(WT_D / "src").mkdir()
call(
    "post",
    s,
    prompt_id="d1",
    cwd=str(WT_D),
    tool_name="Edit",
    tool_input={"file_path": str(WT_D / "src" / "a.py")},
)
(WT_D / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
subprocess.run(
    ["git", "worktree", "remove", "--force", str(WT_D)], cwd=REPO_A, check=True
)
first = call("stop", s, prompt_id="d1", cwd=str(TMP))
check(
    "R17 deleted worktree: Stop reports it as removed and unverified",
    (
        "삭제" in str((first or {}).get("reason")),
        "미확인" in str((first or {}).get("reason")),
    ),
    (True, True),
)
call("prompt", s, prompt_id="d2", prompt="next", cwd=str(TMP))
again = call("stop", s, prompt_id="d2", cwd=str(TMP))
check(
    "R17/T11 ... while its edit is unverified, the missing repository is reported every turn",
    "사라졌습니다" in str((again or {}).get("reason")),
    True,
)


def state_file(session: str) -> dict:
    return json.loads(
        (
            Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
        ).read_text(encoding="utf-8")
    )


check(
    "R17 the deleted repository's baseline is kept, not dropped (it may come back)",
    any(
        os.path.normcase(str(WT_D)) == os.path.normcase(k)
        for k in state_file(s).get("repos_removed", {})
    ),
    True,
)

# ---- P0-04b review: rename away and back must not reset the baseline (hiding unverified edits)
REPO_R = PB / "repoR"
REPO_R.mkdir()
for args in (
    ["init", "-q"],
    ["config", "user.email", "t@t"],
    ["config", "user.name", "t"],
):
    subprocess.run(["git", *args], cwd=REPO_R, check=True)
(REPO_R / "a.py").write_text("x = 1\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=REPO_R, check=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=REPO_R, check=True)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="m1", prompt="work", cwd=str(REPO_R))
edit_in = {
    "file_path": str(REPO_R / "a.py"),
    "old_string": "x = 1",
    "new_string": "x = 2",
}
call("pre", s, prompt_id="m1", cwd=str(REPO_R), tool_name="Edit", tool_input=edit_in)
(REPO_R / "a.py").write_text("x = 2\n", encoding="utf-8")
call("post", s, prompt_id="m1", cwd=str(REPO_R), tool_name="Edit", tool_input=edit_in)
check(
    "R18 edited, nothing verified -> Stop blocks",
    decision(call("stop", s, prompt_id="m1", cwd=str(REPO_R))),
    "block",
)
os.rename(REPO_R, PB / "repoR-moved")
moved = call("stop", s, prompt_id="m1", cwd=str(PB))
check(
    "R18 repository moved away -> missing-repository notice",
    "사라졌습니다" in str((moved or {}).get("reason")),
    True,
)
os.rename(PB / "repoR-moved", REPO_R)
call("prompt", s, prompt_id="m2", prompt="continue", cwd=str(REPO_R))
check(
    "R18 moved back: the original baseline is restored and the unverified edit still blocks",
    decision(call("stop", s, prompt_id="m2", cwd=str(REPO_R))),
    "block",
)
back = call("stop", s, prompt_id="m2", cwd=str(REPO_R))
check(
    "R18 ... and the block is about missing verification (not some other reason)",
    "검증(테스트/타입체크/린트/실행)을 실행하지 않았습니다"
    in str((back or {}).get("reason")),
    True,
)

# ---- P0-04b review: a repository moved to a NEW path keeps its baseline (worktree move);
# shell-only changes made before the move must still demand verification there.
REPO_M = PB / "repoM"
REPO_M.mkdir()
for args in (
    ["init", "-q"],
    ["config", "user.email", "t@t"],
    ["config", "user.name", "t"],
):
    subprocess.run(["git", *args], cwd=REPO_M, check=True)
(REPO_M / "m.py").write_text("x = 1\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=REPO_M, check=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=REPO_M, check=True)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="v1", prompt="work", cwd=str(REPO_M))
sh = {"command": "python gen.py"}
call(
    "pre",
    s,
    prompt_id="v1",
    cwd=str(REPO_M),
    tool_name="Bash",
    tool_input=sh,
    tool_use_id="sh1",
)
(REPO_M / "m.py").write_text("x = 2\n", encoding="utf-8")  # a shell-only change
call(
    "post",
    s,
    prompt_id="v1",
    cwd=str(REPO_M),
    tool_name="Bash",
    tool_input=sh,
    tool_use_id="sh1",
    tool_response={},
)
MOVED = PB / "repoM-new"
os.rename(REPO_M, MOVED)
call("stop", s, prompt_id="v1", cwd=str(PB))  # removal notice
for i in (2, 3):
    call("prompt", s, prompt_id=f"v{i}", prompt="continue there", cwd=str(MOVED))
    call(
        "pre",
        s,
        prompt_id=f"v{i}",
        cwd=str(MOVED),
        tool_name="Bash",
        tool_input=sh,
        tool_use_id=f"sh{i}",
    )
    call(
        "post",
        s,
        prompt_id=f"v{i}",
        cwd=str(MOVED),
        tool_name="Bash",
        tool_input=sh,
        tool_use_id=f"sh{i}",
        tool_response={},
    )
    check(
        f"moved repository, turn {i}: the old path's unverified shell change still blocks Stop",
        "사라졌습니다" in reason(call("stop", s, prompt_id=f"v{i}", cwd=str(MOVED))),
        True,
    )

# ---- R19: a sibling worktree at the same HEAD must not take the moved one's baseline away;
# when the original path comes back, its unverified edit is still not taken as verified.
SIB_A, SIB_B = PB / "sibA", PB / "sibB"
for w in (SIB_A, SIB_B):
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(w)], cwd=REPO_A, check=True
    )
s = uuid.uuid4().hex
call("prompt", s, prompt_id="w1", prompt="work", cwd=str(SIB_A))
sib_edit = {"file_path": str(SIB_A / "r.py"), "content": "x = 2\n"}  # a code file
call("pre", s, prompt_id="w1", cwd=str(SIB_A), tool_name="Write", tool_input=sib_edit)
(SIB_A / "r.py").write_text("x = 2\n", encoding="utf-8")
call("post", s, prompt_id="w1", cwd=str(SIB_A), tool_name="Write", tool_input=sib_edit)
check(
    "R19 control: the unverified code edit blocks before anything moves",
    decision(call("stop", s, prompt_id="w1", cwd=str(SIB_A))),
    "block",
)
os.rename(SIB_A, PB / "sibA.bak")
call("stop", s, prompt_id="w1", cwd=str(PB))  # removal notice for sibA
call("prompt", s, prompt_id="w2", prompt="other worktree", cwd=str(SIB_B))
call("stop", s, prompt_id="w2", cwd=str(SIB_B))
os.rename(PB / "sibA.bak", SIB_A)
sib_stops = []
for i in (3, 4):
    call("prompt", s, prompt_id=f"w{i}", prompt="back", cwd=str(SIB_A))
    sib_stops.append(
        "검증(테스트/타입체크/린트/실행)을 실행하지 않았습니다"
        in reason(call("stop", s, prompt_id=f"w{i}", cwd=str(SIB_A)))
    )
check(
    "R19 original worktree back after a sibling took a baseline: blocked as unverified",
    sib_stops,
    [True, True],
)

# ---- review #2: a removed repository that never comes back must not make unrelated new
# repositories "late" (blocked with a wrong reason)
REPO_U = PB / "repoU"
REPO_U.mkdir()
for args in (
    ["init", "-q"],
    ["config", "user.email", "t@t"],
    ["config", "user.name", "t"],
):
    subprocess.run(["git", *args], cwd=REPO_U, check=True)
(REPO_U / "u.txt").write_text("u\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=REPO_U, check=True)
subprocess.run(["git", "commit", "-qm", "u"], cwd=REPO_U, check=True)
WT_GONE = PB / "gone"
subprocess.run(
    ["git", "worktree", "add", "-q", "--detach", str(WT_GONE)], cwd=REPO_A, check=True
)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="u1", prompt="look", cwd=str(WT_GONE))
subprocess.run(
    ["git", "worktree", "remove", "--force", str(WT_GONE)], cwd=REPO_A, check=True
)
check(
    "review #2 control: a repository only used as cwd and removed outside any tool is a normal deletion",
    reason(call("stop", s, prompt_id="u1", cwd=str(PB))),
    "",
)
call("prompt", s, prompt_id="u2", prompt="unrelated repository", cwd=str(REPO_U))
check(
    "review #2: an unrelated, unchanged repository is not blocked as late",
    decision(call("stop", s, prompt_id="u2", cwd=str(REPO_U))),
    None,
)

# ---- D3 (final): no identity guessing. A missing repository keeps its baseline (by path);
# it is cleared only when proven clean (clean at the last Stop and untouched since), otherwise
# reported every turn; a repository at a returned path is compared with that path's baseline.
GONE = "사라졌습니다"
UNVERIFIED = "검증(테스트/타입체크/린트/실행)을 실행하지 않았습니다"
POLICY = "## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n"


def d3_repo(name: str, policy: bool = False) -> Path:
    r = PB / name
    r.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=r, check=True)
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    if policy:
        (r / "CLAUDE.md").write_text(POLICY, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True)
    return r


def d3_edit(s: str, p: str, repo: Path) -> None:
    w = {"file_path": str(repo / "a.py"), "content": "x = 2\n"}
    call("pre", s, prompt_id=p, cwd=str(repo), tool_name="Write", tool_input=w)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    call("post", s, prompt_id=p, cwd=str(repo), tool_name="Write", tool_input=w)


def d3_stop(s: str, p: str, cwd: Path) -> str:
    return reason(call("stop", s, prompt_id=p, cwd=str(cwd)))


# T4: two renames, then back to the original path
A4 = d3_repo("t4")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="a1", prompt="work", cwd=str(A4))
d3_edit(s, "a1", A4)
os.rename(A4, PB / "t4-x")
r1 = d3_stop(s, "a1", PB)
call("prompt", s, prompt_id="a2", prompt="go on", cwd=str(PB))
os.rename(PB / "t4-x", PB / "t4-y")
r2 = d3_stop(s, "a2", PB)
os.rename(PB / "t4-y", A4)
call("prompt", s, prompt_id="a3", prompt="back", cwd=str(A4))
r3 = d3_stop(s, "a3", A4)
check(
    "T4 renamed twice: reported missing each turn, then unverified at the original path",
    (GONE in r1, GONE in r2, UNVERIFIED in r3),
    (True, True, True),
)

# T5: edit, rename away and back within one turn
A5 = d3_repo("t5")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="b1", prompt="work", cwd=str(A5))
d3_edit(s, "b1", A5)
os.rename(A5, PB / "t5-x")
os.rename(PB / "t5-x", A5)
check(
    "T5 same-turn rename and restore: the edit is still unverified",
    UNVERIFIED in d3_stop(s, "b1", A5),
    True,
)

# T7: an unverified repository is deleted and a different repository appears at its path
A7 = d3_repo("t7")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="c1", prompt="work", cwd=str(A7))
d3_edit(s, "c1", A7)
rm_tree(A7)
r1 = d3_stop(s, "c1", PB)
A7.mkdir()
subprocess.run(["git", "init", "-q"], cwd=A7, check=True)
(A7 / "other.py").write_text("y = 1\n", encoding="utf-8")
call("prompt", s, prompt_id="c2", prompt="new repo here", cwd=str(A7))
r2 = d3_stop(s, "c2", A7)
check(
    "T7 another repository at the same path inherits nothing verified (blocked as unverified)",
    (GONE in r1, UNVERIFIED in r2),
    (True, True),
)
check(
    "T7 ... and no passed evidence exists for it",
    any(
        v.get("outcome") == "passed" for v in state_file(s).get("evidence", {}).values()
    ),
    False,
)

# T8: git metadata of a tracked repository is damaged
A8 = d3_repo("t8")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="e1", prompt="work", cwd=str(A8))
d3_edit(s, "e1", A8)
(A8 / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
check(
    "T8 damaged .git: Stop reports the state as unknown, never verified",
    "확인할 수 없습니다" in d3_stop(s, "e1", A8),
    True,
)

# T9: a verified repository that is deleted is a normal deletion: no block for it
A9 = d3_repo("t9", policy=True)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f1", prompt="work", cwd=str(A9))
d3_edit(s, "f1", A9)
call(
    "post",
    s,
    prompt_id="f1",
    cwd=str(A9),
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
)
check(
    "T9 control: edit verified by the declared gate -> Stop passes",
    d3_stop(s, "f1", A9),
    "",
)
rm_tree(A9)
call("prompt", s, prompt_id="f2", prompt="next", cwd=str(PB))
check("T9 the verified repository's deletion does not block", d3_stop(s, "f2", PB), "")

# A-T1 (review #1): a shell command run from ANOTHER cwd edits a tracked repository by absolute
# path and moves it in the same command. Recorded before the shell runs: every tracked
# repository may have been changed, so the missing one is not a normal deletion.
AT1 = d3_repo("at1")
OTHER_CWD = d3_repo("at1-other")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="h1", prompt="look", cwd=str(AT1))
check("A-T1 control: tracked and clean at the first Stop", d3_stop(s, "h1", AT1), "")
call("prompt", s, prompt_id="h2", prompt="elsewhere", cwd=str(OTHER_CWD))
sh = {
    "command": f'sed -i s/1/2/ "{AT1.as_posix()}/a.py" && mv "{AT1.as_posix()}" "{AT1.as_posix()}-moved"'
}
call(
    "pre",
    s,
    prompt_id="h2",
    cwd=str(OTHER_CWD),
    tool_name="Bash",
    tool_input=sh,
    tool_use_id="h2b",
)
(AT1 / "a.py").write_text("x = 2\n", encoding="utf-8")
os.rename(AT1, PB / "at1-moved")
call(
    "post",
    s,
    prompt_id="h2",
    cwd=str(OTHER_CWD),
    tool_name="Bash",
    tool_input=sh,
    tool_use_id="h2b",
    tool_response={},
)
check(
    "A-T1 edited by absolute path and moved by one shell command: reported missing",
    GONE in d3_stop(s, "h2", OTHER_CWD),
    True,
)
call("prompt", s, prompt_id="h3", prompt="next", cwd=str(PB / "at1-moved"))
check(
    "A-T1 ... still reported on the next turn (never passes)",
    GONE in d3_stop(s, "h3", PB / "at1-moved"),
    True,
)

# A-T9: a clean worktree removed by a shell command is a KNOWN conservative false positive until
# P0-07 can tell which paths a command writes: the shell may have changed it, so it is reported.
AT9 = d3_repo("at9")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="i1", prompt="look", cwd=str(AT9))
check("A-T9 control: clean at the first Stop", d3_stop(s, "i1", AT9), "")
call("prompt", s, prompt_id="i2", prompt="remove it", cwd=str(PB))
rm_cmd = {"command": f'rm -rf "{AT9.as_posix()}"'}
call(
    "pre",
    s,
    prompt_id="i2",
    cwd=str(PB),
    tool_name="Bash",
    tool_input=rm_cmd,
    tool_use_id="i2b",
)
rm_tree(AT9)
call(
    "post",
    s,
    prompt_id="i2",
    cwd=str(PB),
    tool_name="Bash",
    tool_input=rm_cmd,
    tool_use_id="i2b",
    tool_response={},
)
check(
    "A-T9 known false positive (P0-07): a shell-removed clean repository is reported missing",
    GONE in d3_stop(s, "i2", PB),
    True,
)

# A-T11: a repository proven clean and deleted without any tool leaves no baseline behind,
# so a new repository created at the same path starts fresh (nothing stale applied to it).
AT11 = d3_repo("at11")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="j1", prompt="look", cwd=str(AT11))
check("A-T11 control: clean at the first Stop", d3_stop(s, "j1", AT11), "")
rm_tree(AT11)
call("prompt", s, prompt_id="j2", prompt="next", cwd=str(PB))
check("A-T11 clean deletion: no block", d3_stop(s, "j2", PB), "")
d3_repo(
    "at11"
)  # a different repository at the same path (different content, new commit)
(AT11 / "b.py").write_text("y = 1\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=AT11, check=True)
subprocess.run(["git", "commit", "-qm", "other"], cwd=AT11, check=True)
call("prompt", s, prompt_id="j3", prompt="new repository", cwd=str(AT11))
check(
    "A-T11 new repository at a cleanly deleted path starts fresh (no stale baseline)",
    d3_stop(s, "j3", AT11),
    "",
)
check(
    "A-T11 ... and the deleted one left nothing in the session state",
    os.path.normcase(str(AT11))
    in {os.path.normcase(k) for k in state_file(s).get("repos_removed", {})},
    False,
)

# T10 / T11 / T12: unverified repository deleted; reported every turn, also after a resume
A10 = d3_repo("t10")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="g1", prompt="work", cwd=str(A10))
d3_edit(s, "g1", A10)
rm_tree(A10)
seen = [GONE in d3_stop(s, "g1", PB)]
for i in (2, 3):
    call("prompt", s, prompt_id=f"g{i}", prompt="next", cwd=str(PB))
    seen.append(GONE in d3_stop(s, f"g{i}", PB))
check("T10/T11 unverified deletion: reported on every turn", seen, [True, True, True])
call(
    "prompt", s, prompt_id="g4", prompt="resumed later", cwd=str(PB)
)  # a resumed session: same id
check(
    "T12 after a resume (same session state) it is still reported",
    GONE in d3_stop(s, "g4", PB),
    True,
)


# ---- P0-04b revision (D3a, F1): a missing repository's path is taken back only when a git
# toplevel is there again; a plain folder or another repository's subfolder is not it.
def tracked(session: str, key: str, path: Path) -> bool:
    return os.path.normcase(str(path)) in {
        os.path.normcase(k) for k in state_file(session).get(key, {})
    }


BT1 = d3_repo("bt1")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="k1", prompt="work", cwd=str(BT1))
d3_edit(s, "k1", BT1)
rm_tree(BT1)
BT1.mkdir()  # same path, no repository
check(
    "B-T1 a plain folder at a missing repository's path: still reported missing",
    GONE in d3_stop(s, "k1", PB),
    True,
)
check(
    "B-T1 ... and it is not tracked as a repository again",
    (tracked(s, "repos", BT1), tracked(s, "repos_removed", BT1)),
    (False, True),
)

BT2_OUT = d3_repo("bt2")
BT2 = BT2_OUT / "inner"
BT2.mkdir()
for args in (
    ["init", "-q"],
    ["config", "user.email", "t@t"],
    ["config", "user.name", "t"],
):
    subprocess.run(["git", *args], cwd=BT2, check=True)
(BT2 / "a.py").write_text("x = 1\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=BT2, check=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=BT2, check=True)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="k2", prompt="work", cwd=str(BT2))
d3_edit(s, "k2", BT2)
rm_tree(BT2)
BT2.mkdir()  # now only a subfolder of the outer repository
check(
    "B-T2 the path became another repository's subfolder: still reported missing",
    GONE in d3_stop(s, "k2", PB),
    True,
)
check(
    "B-T2 ... and it is not tracked as a repository again",
    (tracked(s, "repos", BT2), tracked(s, "repos_removed", BT2)),
    (False, True),
)

BT3 = d3_repo("bt3")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="k3", prompt="work", cwd=str(BT3))
d3_edit(s, "k3", BT3)
os.rename(BT3, PB / "bt3-x")
gone3 = GONE in d3_stop(s, "k3", PB)
os.rename(PB / "bt3-x", BT3)
call("prompt", s, prompt_id="k4", prompt="back", cwd=str(BT3))
check(
    "B-T3 the repository itself back at its path: original baseline, edit still unverified",
    (gone3, UNVERIFIED in d3_stop(s, "k4", BT3), tracked(s, "repos", BT3)),
    (True, True, True),
)

BT4 = d3_repo("bt4")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="k5", prompt="look", cwd=str(BT4))
check("B-T4 control: clean at the first Stop", d3_stop(s, "k5", BT4), "")
rm_tree(BT4)
call("prompt", s, prompt_id="k6", prompt="next", cwd=str(PB))
check("B-T4 control: clean deletion, no block", d3_stop(s, "k6", PB), "")
BT4.mkdir()  # a plain folder where the repository was
bt4_write = {"file_path": str(BT4 / "a.py"), "content": "x = 3\n"}
call("pre", s, prompt_id="k6", cwd=str(BT4), tool_name="Write", tool_input=bt4_write)
check(
    "B-T4 an edit under the old path does not revive the repository from a stale cache",
    tracked(s, "repos", BT4),
    False,
)

# ---- P0-04b revision (D3b, F2/F3): a background shell may write after the Stop, and a shell
# event seen without its PreToolUse touched the repository all the same.
BT5 = d3_repo("bt5")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="l1", prompt="start a watcher", cwd=str(BT5))
call(
    "post",
    s,
    prompt_id="l1",
    cwd=str(BT5),
    tool_name="Bash",
    tool_input={"command": "python watch.py", "run_in_background": True},
    tool_response={"backgroundTaskId": "bg1"},
)
check("B-T5 control: clean at the Stop of that turn", d3_stop(s, "l1", BT5), "")
call("prompt", s, prompt_id="l2", prompt="next", cwd=str(PB))
rm_tree(BT5)
check(
    "B-T5 a repository a background shell ran in, missing a turn later: reported, not forgotten",
    GONE in d3_stop(s, "l2", PB),
    True,
)

BT6 = d3_repo("bt6")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="l3", prompt="look", cwd=str(BT6))
check("B-T6 control: clean at the first Stop", d3_stop(s, "l3", BT6), "")
call("prompt", s, prompt_id="l4", prompt="next", cwd=str(PB))
rm_tree(BT6)
check(
    "B-T6 used only as cwd and removed outside any tool: a normal deletion",
    d3_stop(s, "l4", PB),
    "",
)

for bt_name, bt_mode in (("bt7", "fail"), ("bt8", "post")):
    BTX = d3_repo(bt_name)
    s = uuid.uuid4().hex
    call("prompt", s, prompt_id="n1", prompt="look", cwd=str(BTX))
    first_stop = d3_stop(s, "n1", BTX)
    call("prompt", s, prompt_id="n2", prompt="next", cwd=str(PB))
    call(  # its PreToolUse never arrived (tool_use_id given: `call` does not send one)
        bt_mode,
        s,
        prompt_id="n2",
        cwd=str(BTX),
        tool_name="Bash",
        tool_input={"command": "python build.py"},
        tool_use_id=f"{bt_name}-only",
        tool_response={},
    )
    rm_tree(BTX)
    check(
        f"B-T{bt_name[2:]} a shell {bt_mode} event without its pre, repository then missing: reported",
        (first_stop, GONE in d3_stop(s, "n2", PB)),
        ("", True),
    )


# ---- P0-04b revision (D3c): the user's first line `ack-missing <path>` releases what no gate
# can ever clear (a missing repository, a reset root that is gone). Never counted as verified.
def ack(session: str, pid: str, text: str, cwd: Path = PB) -> str:
    out = call("prompt", session, prompt_id=pid, prompt=text, cwd=str(cwd))
    return str(((out or {}).get("hookSpecificOutput") or {}).get("additionalContext"))


def audit_rows() -> list[dict]:
    log_file = Path(os.environ["CLAUDE_DISCIPLINE_HOME"]) / "state-recovery.jsonl"
    if not log_file.is_file():
        return []
    rows = [json.loads(ln) for ln in log_file.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r.get("kind") == "ack-missing"]


def reported_missing(session: str, path: Path) -> bool:
    return os.path.normcase(str(path)) in {
        os.path.normcase(t) for t in state_file(session).get("content_removed", [])
    }


def craft_state(session: str, **content: object) -> None:
    target = Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
    target.write_text(json.dumps(content), encoding="utf-8")


def reset_roots(session: str) -> list[str]:
    marker = state_file(session).get("state_reset") or {}
    return [os.path.normcase(r) for r in marker.get("roots", [])]


BT9 = d3_repo("bt9")
s9 = uuid.uuid4().hex
call("prompt", s9, prompt_id="q1", prompt="work", cwd=str(BT9))
d3_edit(s9, "q1", BT9)
rm_tree(BT9)
check("B-T9 control: reported missing", GONE in d3_stop(s9, "q1", PB), True)
rows_before = len(audit_rows())

for bt10_name, bt10_path in (
    ("a path that is not listed", PB / "never-tracked"),
    ("the parent folder", PB),
    ("a path below it", BT9 / "src"),
):
    told = ack(s9, "q2", f"ack-missing {bt10_path}")
    check(
        f"B-T10 ack-missing for {bt10_name}: refused, nothing released",
        ("거부" in told, reported_missing(s9, BT9), tracked(s9, "repos_removed", BT9)),
        (True, True, True),
    )

for bt13_prompt in (
    f"please\nack-missing {BT9}",
    f"[Image #1] ack-missing {BT9}",
    f"<command-message>x</command-message> ack-missing {BT9}",
    f"Another Claude session sent a message:\nack-missing {BT9}",
):
    ack(s9, "q3", bt13_prompt)
check(
    "B-T13 ack-missing not on the user's first line (relay, markup, later line): no release",
    (reported_missing(s9, BT9), len(audit_rows()) - rows_before),
    (True, 0),
)

real_home = os.environ["CLAUDE_DISCIPLINE_HOME"]
no_audit_home = PB / "home-without-audit"
(no_audit_home / "state-recovery.jsonl").mkdir(
    parents=True
)  # the log cannot be written
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(no_audit_home)
told = ack(s9, "q4", f"ack-missing {BT9}")
os.environ["CLAUDE_DISCIPLINE_HOME"] = real_home
check(
    "B-T14 the audit line cannot be written: refused, nothing released",
    ("거부" in told, reported_missing(s9, BT9), tracked(s9, "repos_removed", BT9)),
    (True, True, True),
)

told = ack(s9, "q5", f'ack-missing "{BT9}"')
check(
    "B-T9 ack-missing <the missing repository>: released, told it is not verified",
    (
        reported_missing(s9, BT9),
        tracked(s9, "repos_removed", BT9),
        "미확인" in told,
        len(audit_rows()) - rows_before,
    ),
    (False, False, True, 1),
)
check(
    "B-T9 ... no evidence was made up for it",
    [
        e
        for e in (state_file(s9).get("evidence") or {}).values()
        if e.get("outcome") == "passed"
    ],
    [],
)
check("B-T9 ... and Stop no longer blocks for it", d3_stop(s9, "q5", PB), "")

d3_repo("bt9")  # a repository appears at the acknowledged path again
call("prompt", s9, prompt_id="q6", prompt="work there again", cwd=str(BT9))
d3_edit(s9, "q6", BT9)
bt15_base = {
    os.path.normcase(k): v for k, v in state_file(s9).get("repos", {}).items()
}.get(os.path.normcase(str(BT9)), {})
check(
    "B-T15 a repository at an acknowledged path: late baseline, its edit blocks (not verified)",
    (bt15_base.get("late"), decision(call("stop", s9, prompt_id="q6", cwd=str(BT9)))),
    (True, "block"),
)

BT11 = d3_repo("bt11", policy=True)
BT12 = PB / "bt12-gone"  # a reset root that no longer exists
s = uuid.uuid4().hex
craft_state(
    s,
    state_reset={
        "at": 1.0,
        "why": "crafted for the test",
        "roots": [str(BT11), str(BT12)],
    },
)
told = ack(s, "r1", f"ack-missing {BT11}", cwd=BT11)
check(
    "B-T11 ack-missing for an existing root with a declared gate: refused (its gate decides)",
    ("거부" in told, sorted(reset_roots(s))),
    (True, sorted(os.path.normcase(str(p)) for p in (BT11, BT12))),
)
told = ack(s, "r2", f"ack-missing {BT12}", cwd=BT11)
check(
    "B-T12 ack-missing for a reset root that is gone: only that root is released",
    ("미확인" in told, reset_roots(s)),
    (True, [os.path.normcase(str(BT11))]),
)

BT16 = d3_repo("bt16")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="t1", prompt="look", cwd=str(BT16))
check("B-T16 control: clean at the first Stop", d3_stop(s, "t1", BT16), "")
call("prompt", s, prompt_id="t2", prompt="remove it", cwd=str(PB))
call(
    "post",
    s,
    prompt_id="t2",
    cwd=str(PB),
    tool_name="Bash",
    tool_input={"command": f'rm -rf "{BT16.as_posix()}"'},
    tool_response={},
)
rm_tree(BT16)
bt16_reported = GONE in d3_stop(s, "t2", PB)
ack(s, "t3", f"ack-missing {BT16}")
check(
    "B-T16 the known false positive (shell-removed clean repository) is released by ack-missing",
    (bt16_reported, d3_stop(s, "t3", PB)),
    (True, ""),
)


# ---- P0-04b revision, review findings (2026-11-13)
def repo_base(session: str, path: Path) -> dict:
    return {
        os.path.normcase(k): v for k, v in state_file(session).get("repos", {}).items()
    }.get(os.path.normcase(str(path)), {})


# #1: a repository first seen by a shell post/fail was snapshotted AFTER the command ran.
BT17 = d3_repo("bt17")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="v1", prompt="build", cwd=str(PB))
(BT17 / "a.py").write_text("x = 9\n", encoding="utf-8")  # what the command did
call(
    "post",
    s,
    prompt_id="v1",
    cwd=str(BT17),
    tool_name="Bash",
    tool_input={"command": "python build.py"},
    tool_use_id="bt17-only",
    tool_response={},
)
check(
    "B-T17 a baseline first taken after a shell command ran: late, reported as unknown",
    (repo_base(s, BT17).get("late"), "미확인" in d3_stop(s, "v1", PB)),
    (True, True),
)

# #4: once a background shell runs, a repository tracked later may be written by it too.
BT18X = d3_repo("bt18x")
BT18C = d3_repo("bt18c")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="w1", prompt="start a watcher", cwd=str(BT18X))
call(
    "post",
    s,
    prompt_id="w1",
    cwd=str(BT18X),
    tool_name="Bash",
    tool_input={"command": "python watch.py", "run_in_background": True},
    tool_response={"backgroundTaskId": "bg2"},
)
bt18_stops = [d3_stop(s, "w1", BT18X)]
call("prompt", s, prompt_id="w2", prompt="look elsewhere", cwd=str(BT18C))
bt18_stops.append(d3_stop(s, "w2", BT18C))
call("prompt", s, prompt_id="w3", prompt="next", cwd=str(PB))
rm_tree(BT18C)
check(
    "B-T18 a repository tracked after a background shell started, then missing: reported",
    (bt18_stops, GONE in d3_stop(s, "w3", PB)),
    (["", ""], True),
)

# #2/#3: ack-missing drops only the edit records of files that went with the path.
BT19 = d3_repo("bt19")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="x1", prompt="work", cwd=str(BT19))
d3_edit(s, "x1", BT19)
bt19_b = {"file_path": str(BT19 / "b.py"), "content": "y = 1\n"}
call("post", s, prompt_id="x1", cwd=str(BT19), tool_name="Write", tool_input=bt19_b)
rm_tree(BT19)
BT19.mkdir()
(BT19 / "b.py").write_text("y = 1\n", encoding="utf-8")  # this edited file still exists
bt19_reported = GONE in d3_stop(s, "x1", PB)
ack(s, "x2", f"ack-missing {BT19}")
check(
    "B-T19 after ack-missing an edited file that still exists keeps demanding verification",
    (bt19_reported, reported_missing(s, BT19), UNVERIFIED in d3_stop(s, "x2", PB)),
    (True, False, True),
)

BT19X = PB / "bt19-crafted"  # never existed
other_root = str(PB / "bt19-other-project")
capped_root = str(PB / "bt19-capped-project")
s = uuid.uuid4().hex
craft_state(
    s,
    content_removed=[str(BT19X)],
    repos_removed={str(BT19X): {}},
    dirty={
        other_root: {"at": 5.0, "files": [], "gate": None},
        capped_root: {
            "at": 6.0,
            "gate": None,
            "files": [str(BT19X / f"f{i}.py") for i in range(200)],
        },
    },
)
ack(s, "x3", f"ack-missing {BT19X}")
bt19_dirty = state_file(s).get("dirty", {})
check(
    "B-T19 ack-missing leaves unrelated edit records and a capped (maybe incomplete) list alone",
    (
        reported_missing(s, BT19X),
        other_root in bt19_dirty,
        bt19_dirty.get(capped_root, {}).get("at"),
    ),
    (False, True, 6.0),
)

# #3: a missing-repository entry that is also a gated, existing reset root.
BT20 = PB / "bt20"
BT20.mkdir()
(BT20 / "CLAUDE.md").write_text(POLICY, encoding="utf-8")
s = uuid.uuid4().hex
craft_state(
    s,
    content_removed=[str(BT20)],
    repos_removed={str(BT20): {}},
    state_reset={"at": 1.0, "why": "crafted for the test", "roots": [str(BT20)]},
)
ack(s, "y1", f"ack-missing {BT20}")
check(
    "B-T20 missing entry released, the same path's gated reset root stays (its gate decides)",
    (reported_missing(s, BT20), reset_roots(s)),
    (False, [os.path.normcase(str(BT20))]),
)

# #3: a slash command's args block is the user's own line too (as for recover-state).
BT21 = PB / "bt21-gone"
s = uuid.uuid4().hex
craft_state(s, content_removed=[str(BT21)], repos_removed={str(BT21): {}})
ack(
    s,
    "y2",
    f"<command-name>/ack</command-name>\n<command-args>ack-missing {BT21}</command-args>",
)
check(
    "B-T21 ack-missing given as a slash command's args: accepted",
    reported_missing(s, BT21),
    False,
)

# #5: a repository that is back at its path is cleared by verification, not by ack-missing.
BT22 = d3_repo("bt22")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="z1", prompt="work", cwd=str(BT22))
d3_edit(s, "z1", BT22)
os.rename(BT22, PB / "bt22-x")
bt22_reported = GONE in d3_stop(s, "z1", PB)
os.rename(PB / "bt22-x", BT22)
told = ack(s, "z2", f"ack-missing {BT22}")
check(
    "B-T22 ack-missing for a repository that came back: refused, its unverified edit still blocks",
    (bt22_reported, "거부" in told, UNVERIFIED in d3_stop(s, "z2", BT22)),
    (True, True, True),
)

# #6: malformed lists are refused before the audit line, never half applied.
BT23 = PB / "bt23-gone"
s = uuid.uuid4().hex
craft_state(s, content_removed={str(BT23): 1}, repos_removed={str(BT23): {}})
rows_before = len(audit_rows())
told = ack(s, "z3", f"ack-missing {BT23}")
check(
    "B-T23 ack-missing on a malformed state: refused, no audit line, nothing released",
    (
        "거부" in told,
        len(audit_rows()) - rows_before,
        tracked(s, "repos_removed", BT23),
    ),
    (True, 0, True),
)

# #7: recover-state keeps what the two new records protect.
BT24_BG = PB / "bt24-background"
BT24_ACK = PB / "bt24-acked"
s = uuid.uuid4().hex
craft_state(
    s,
    background_tops=[str(BT24_BG)],
    acked_missing=[
        {"path": str(BT24_ACK), "at": 1.0, "prompt_id": "old"},
        {"path": 7, "at": "x"},
        "junk",
    ],
)
call("prompt", s, prompt_id="z4", prompt="recover-state", cwd=str(PB))
check(
    "B-T24 recover-state: background places become marker roots, well-formed acks are kept",
    (
        os.path.normcase(str(BT24_BG)) in reset_roots(s),
        [row.get("path") for row in state_file(s).get("acked_missing", [])],
    ),
    (True, [str(BT24_ACK)]),
)

# ---- P0-04b revision, second review (2026-11-13)
# L1: a capped edit list may hold edits it does not show; two releases must not empty it away.
BT25A = PB / "bt25-gone-a"  # never existed
BT25B = PB / "bt25-gone-b"
bt25_root = str(PB / "bt25-capped-project")
s = uuid.uuid4().hex
craft_state(
    s,
    content_removed=[str(BT25A), str(BT25B)],
    repos_removed={str(BT25A): {}, str(BT25B): {}},
    dirty={
        bt25_root: {
            "at": 6.0,
            "gate": None,
            "files": [str(BT25A / f"f{i}.py") for i in range(100)]
            + [str(BT25B / f"f{i}.py") for i in range(100)],
        }
    },
)
ack(s, "c1", f"ack-missing {BT25A}")
ack(s, "c2", f"ack-missing {BT25B}")
check(
    "B-T25 two ack-missing over a capped edit list: both released, the capped record stays",
    (
        reported_missing(s, BT25A),
        reported_missing(s, BT25B),
        state_file(s).get("dirty", {}).get(bt25_root, {}).get("at"),
    ),
    (False, False, 6.0),
)

# L4: a repository that comes back while a background shell runs is never "untouched" either.
BT26X = d3_repo("bt26x")
BT26Y = d3_repo("bt26y")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="e1", prompt="build", cwd=str(BT26Y))
call(
    "post",
    s,
    prompt_id="e1",
    cwd=str(BT26Y),
    tool_name="Bash",
    tool_input={"command": "python build.py"},
    tool_response={},
)
os.rename(BT26Y, PB / "bt26y-away")
bt26_gone = GONE in d3_stop(s, "e1", PB)
call("prompt", s, prompt_id="e2", prompt="start a watcher", cwd=str(BT26X))
call(
    "post",
    s,
    prompt_id="e2",
    cwd=str(BT26X),
    tool_name="Bash",
    tool_input={"command": "python watch.py", "run_in_background": True},
    tool_response={"backgroundTaskId": "bg3"},
)
os.rename(PB / "bt26y-away", BT26Y)
bt26_back = d3_stop(s, "e2", BT26X)
call("prompt", s, prompt_id="e3", prompt="next", cwd=str(PB))
rm_tree(BT26Y)
check(
    "B-T26 a repository restored after a background shell started, then missing: reported",
    (bt26_gone, bt26_back, GONE in d3_stop(s, "e3", PB)),
    (True, "", True),
)

# L3: a repository that comes back in a shell post is compared with its ORIGINAL baseline,
# which was taken before the command: not late.
BT27 = d3_repo("bt27")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="h1", prompt="work", cwd=str(BT27))
d3_edit(s, "h1", BT27)
os.rename(BT27, PB / "bt27-away")
bt27_gone = GONE in d3_stop(s, "h1", PB)
os.rename(PB / "bt27-away", BT27)
call("prompt", s, prompt_id="h2", prompt="next", cwd=str(PB))
call(
    "post",
    s,
    prompt_id="h2",
    cwd=str(BT27),
    tool_name="Bash",
    tool_input={"command": "python build.py"},
    tool_use_id="bt27-only",
    tool_response={},
)
check(
    "B-T27 a missing repository restored by a shell post keeps its baseline: not marked late",
    (
        bt27_gone,
        tracked(s, "repos", BT27),
        bool(repo_base(s, BT27).get("late")),
        UNVERIFIED in d3_stop(s, "h2", BT27),
    ),
    (True, True, False, True),
)

# L2/L4: recover-state survives unusable ack times and keeps the background record itself.
BT28_BG = PB / "bt28-background"
BT28_ACK = PB / "bt28-acked"
s = uuid.uuid4().hex
craft_state(
    s,
    background_tops=[str(BT28_BG), 7],
    acked_missing=[
        {"path": str(PB / "bt28-huge"), "at": 10**400},
        {"path": str(PB / "bt28-nan"), "at": float("nan")},
        {"path": str(BT28_ACK), "at": 2.0},
    ],
)
call("prompt", s, prompt_id="j1", prompt="recover-state", cwd=str(PB))
check(
    "B-T28 recover-state: unusable ack times dropped (no failure), background record carried",
    (
        state_file(s).get("recovered") is not None,
        [row.get("path") for row in state_file(s).get("acked_missing", [])],
        state_file(s).get("background_tops"),
    ),
    (True, [str(BT28_ACK)], [str(BT28_BG)]),
)

# L6: a state written before the directory cache was cleaned can still point at a missing
# repository's path: restored only if git says a toplevel is there now.
BT29 = PB / "bt29"
(BT29 / ".git").mkdir(
    parents=True
)  # passes the cheap presence test, is not a repository
s = uuid.uuid4().hex
craft_state(
    s,
    dir_tops={str(BT29): str(BT29)},
    content_removed=[str(BT29)],
    repos_removed={str(BT29): {}},
)
bt29_w = {"file_path": str(BT29 / "a.py"), "content": "x = 2\n"}
call("pre", s, prompt_id="m1", cwd=str(BT29), tool_name="Write", tool_input=bt29_w)
check(
    "B-T29 a cached top that is no git toplevel: not restored, still reported missing",
    (
        tracked(s, "repos", BT29),
        tracked(s, "repos_removed", BT29),
        reported_missing(s, BT29),
    ),
    (False, True, True),
)


# ---- F4a: who left a record (main or a subagent) is written next to it and shown in the
# reasons. It never changes a decision.
def owners_of(session: str, path: Path) -> list[str]:
    return {
        os.path.normcase(k): v for k, v in state_file(session).get("owners", {}).items()
    }.get(os.path.normcase(str(path)), [])


def agent_shell(session: str, pid: str, repo: Path, **who: object) -> None:
    call(
        "post",
        session,
        prompt_id=pid,
        cwd=str(repo),
        tool_name="Bash",
        tool_input={"command": "python build.py"},
        tool_response={},
        **who,
    )


def agent_write(session: str, pid: str, repo: Path, name: str, **who: object) -> str:
    w = {"file_path": str(repo / name), "content": "x = 2\n"}
    out = call(
        "pre",
        session,
        prompt_id=pid,
        cwd=str(repo),
        tool_name="Write",
        tool_input=w,
        **who,
    )
    (repo / name).write_text("x = 2\n", encoding="utf-8")
    call(
        "post",
        session,
        prompt_id=pid,
        cwd=str(repo),
        tool_name="Write",
        tool_input=w,
        **who,
    )
    return str(decision(out))


REVIEWER = {"agent_id": "a7", "agent_type": "code-reviewer"}
OWNER_NOTE = "주체"

FT1 = d3_repo("ft1")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f1", prompt="work", cwd=str(FT1))
d3_edit(s, "f1", FT1)
ft1_reason = d3_stop(s, "f1", FT1)
check(
    "F-T1 main only: the repository's owner is main, no subagent named in the reason",
    (owners_of(s, FT1), UNVERIFIED in ft1_reason, OWNER_NOTE in ft1_reason),
    (["main"], True, False),
)

FT2 = d3_repo("ft2")  # a subagent's scratch repository (the p3parent case)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f2", prompt="review", cwd=str(PB))
agent_shell(s, "f2", FT2, **REVIEWER)
rm_tree(FT2)
ft2_reason = d3_stop(s, "f2", PB)
check(
    "F-T2 a repository a subagent ran a shell in, then missing: reported, naming the subagent",
    (GONE in ft2_reason, "code-reviewer:a7" in ft2_reason),
    (True, True),
)
ack(s, "f3", f"ack-missing {FT2}")
check(
    "F-T3 ack-missing: released, the audit line carries the owners, the owner record is dropped",
    (
        reported_missing(s, FT2),
        (audit_rows()[-1:] or [{}])[0].get("owners"),
        owners_of(s, FT2),
    ),
    (False, ["code-reviewer:a7"], []),
)

FT4 = d3_repo("ft4")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f4", prompt="implement", cwd=str(FT4))
agent_write(s, "f4", FT4, "a.py", agent_id="b1", agent_type="general-purpose")
ft4_reason = d3_stop(s, "f4", FT4)
check(
    "F-T4 a subagent's unverified edit: Stop blocked as before, naming the subagent",
    (UNVERIFIED in ft4_reason, "general-purpose:b1" in ft4_reason),
    (True, True),
)

FT5 = d3_repo("ft5", policy=True)
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f5", prompt="implement", cwd=str(FT5))
agent_write(s, "f5", FT5, "a.py", agent_id="t1", agent_type="test-runner")
call(
    "post",
    s,
    prompt_id="f5",
    cwd=str(FT5),
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
    tool_response={},
    agent_id="t1",
    agent_type="test-runner",
)
check(
    "F-T5 a subagent's passing gate: accepted as before, the evidence says who ran it",
    (
        d3_stop(s, "f5", FT5),
        [e.get("by") for e in state_file(s).get("evidence", {}).values()],
    ),
    ("", ["test-runner:t1"]),
)

FT6 = d3_repo("ft6")
FT7 = d3_repo("ft7")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f6", prompt="work", cwd=str(FT7))
d3_edit(s, "f6", FT6)
agent_shell(s, "f6", FT6, **REVIEWER)
agent_shell(s, "f6", FT6, **REVIEWER)
d3_edit(s, "f6", FT6)
check(
    "F-T6 main and a subagent in one repository: both recorded once",
    owners_of(s, FT6),
    ["main", "code-reviewer:a7"],
)
check(
    "F-T7 a repository the subagent's shell did not run in: not attributed to it",
    owners_of(s, FT7),
    [],
)

FT8 = d3_repo("ft8")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f8", prompt="work", cwd=str(PB))
for odd in (7, {"a": 1}, "x" * 200, "a\nb\x07c"):
    agent_shell(s, "f8", FT8, agent_id=odd, agent_type="t\ty")
ft8 = owners_of(s, FT8) or ["<none>"]
check(
    "F-T8 odd agent ids: no failure, labels bounded and free of control characters",
    (
        ft8[0],
        len(ft8),
        max(len(label) for label in ft8) <= 80,
        any(ch < " " for label in ft8 for ch in label),
        ft8[-1],
    ),
    ("agent:?", 3, True, False, "ty:abc"),
)

FT9 = d3_repo("ft9")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f9", prompt="work", cwd=str(PB))
for n in range(10):
    agent_shell(s, "f9", FT9, agent_id=f"n{n}", agent_type="w")
check(
    "F-T9 more than eight owners of one repository: the latest eight are kept",
    owners_of(s, FT9),
    [f"w:n{n}" for n in range(2, 10)],
)


def ft10_decisions(tag: str, **who: object) -> list[object]:
    """One scenario per rule; the decisions must not depend on who acted."""
    got: list[object] = []
    edited = d3_repo(f"ft10-{tag}-edit")
    session = uuid.uuid4().hex
    call("prompt", session, prompt_id="g1", prompt="work", cwd=str(edited))
    agent_write(session, "g1", edited, "a.py", **who)
    got.append(decision(call("stop", session, prompt_id="g1", cwd=str(edited))))
    call(
        "fail",
        session,
        prompt_id="g1",
        cwd=str(edited),
        tool_name="Bash",
        tool_input={"command": "uv run pytest -q"},
        error="Exit code 1",
        **who,
    )
    call("prompt", session, prompt_id="g2", prompt="next", cwd=str(edited))
    got.append(decision(call("stop", session, prompt_id="g2", cwd=str(edited))))
    gone = d3_repo(f"ft10-{tag}-gone")
    session = uuid.uuid4().hex
    call("prompt", session, prompt_id="g3", prompt="work", cwd=str(PB))
    agent_shell(session, "g3", gone, **who)
    rm_tree(gone)
    got.append(decision(call("stop", session, prompt_id="g3", cwd=str(PB))))
    many = d3_repo(f"ft10-{tag}-many")
    session = uuid.uuid4().hex
    call("prompt", session, prompt_id="g4", prompt="work", cwd=str(many))
    got += [agent_write(session, "g4", many, f"m{n}.py", **who) for n in range(7)]
    return got


ft10_main = ft10_decisions("main")
check(
    "F-T10 the same scenarios acted by main and by a subagent: identical decisions",
    (
        ft10_decisions("agent", **REVIEWER) == ft10_main,
        "block" in ft10_main,
        "deny" in ft10_main,
    ),
    (True, True, True),
)

# ---- P0-04b review: git failures and caches must not make a %TEMP% repository look like scratch
sys.path.insert(0, str(Path(HOOK).parent))
import discipline as _d

check(
    "D2 a UNC path under %TEMP% is never scratch (git cannot judge it)",
    _d.ignored_path(
        {}, "\\\\localhost\\C$\\Users\\u\\AppData\\Local\\Temp\\nothere\\a.py", "C:\\"
    ),
    False,
)
_real = _d.os.path.realpath


def _no_io(p: str) -> str:  # a stalled share: realpath must not even be reached
    raise AssertionError(f"realpath called for {p}")


_d.os.path.realpath = _no_io
try:
    check(
        "D2 UNC and non-ignorable paths are decided before any link resolution (no share I/O)",
        (
            _d.ignored_path(
                {}, "\\\\deadhost\\share\\AppData\\Local\\Temp\\a.py", "C:\\"
            ),
            _d.ignored_path({}, "C:/dev/project/src/a.py", "C:\\"),
        ),
        (False, False),
    )
finally:
    _d.os.path.realpath = _real
check(
    "D2 a %TEMP% path that cannot be inspected (embedded NUL) is not taken as scratch",
    _d.ignored_path({}, str(TMP / "plainC" / "a\0b" / "x.py"), "C:\\"),
    False,
)
WT_X = TMP / "wtX"
subprocess.run(
    ["git", "worktree", "add", "-q", "--detach", str(WT_X)], cwd=REPO_A, check=True
)
with open(WT_X / ".git", "r+", encoding="utf-8") as f:  # git hides it: "w" is refused
    f.write("gitdir: nowhere\n")  # corrupt worktree metadata
    f.truncate()
check(
    "D2 corrupt .git in a %TEMP% worktree: still protected",
    classes_run(WT_X, str(WT_X / "src" / "f5.py")),
    ("deny", "ask"),
)
LATE = TMP / "late-init"
LATE.mkdir()
s = uuid.uuid4().hex
call("prompt", s, prompt_id="g1", prompt="small change", cwd=str(LATE))
weak = {
    "file_path": str(LATE / "tests" / "test_x.py"),
    "old_string": "    assert f() == 1\n",
    "new_string": "    pass\n",
}
check(
    "D2 before git init: a plain %TEMP% folder is scratch",
    decision(
        call("pre", s, prompt_id="g1", cwd=str(LATE), tool_name="Edit", tool_input=weak)
    ),
    None,
)
subprocess.run(["git", "init", "-q"], cwd=LATE, check=True)
check(
    "D2 after git init in the same session: protected (no stale cache)",
    decision(
        call("pre", s, prompt_id="g1", cwd=str(LATE), tool_name="Edit", tool_input=weak)
    ),
    "ask",
)
shutil.rmtree(WT_X, ignore_errors=True)
subprocess.run(["git", "worktree", "prune"], cwd=REPO_A, check=False)
subprocess.run(
    ["cmd", "/c", "rmdir", str(LINK)], capture_output=True, check=False
)  # junction only
subprocess.run(
    ["git", "worktree", "remove", "--force", str(WT_B)], cwd=REPO_A, check=False
)
rm_tree(PB)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
