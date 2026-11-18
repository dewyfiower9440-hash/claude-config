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
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
APPROVAL_HOME = TMP_ROOT / "_tmp_discipline_approvals"
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
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = (WORK_ROOT / "_disc_test_project").as_posix()

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


D2 = TMP_ROOT / "_tmp_d2"
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
ROOT = TMP_ROOT / "_tmp_disc_project"
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
PB = WORK_ROOT / "_disc_p004b"  # outside %TEMP%


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
        # realpath on both sides: %TEMP% may be an 8.3 short path (RUNNER~1 on CI) while the
        # hook records the long form git reports
        os.path.normcase(os.path.realpath(WT_D))
        == os.path.normcase(os.path.realpath(k))
        for k in state_file(s).get("repos_removed", {})
    )
    # on a mismatch, show what was recorded instead of a bare False
    or (str(WT_D), sorted(state_file(s).get("repos_removed", {}))),
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
# devenv-final 13: Claude's own folders are the ones under the home's .claude, not any
# folder of a project that happens to be named like them.
_HOME = _d.os.path.expanduser("~")
check(
    "K-T5 a project's own .claude/plans, .claude/todos or memory folder holds code like "
    "any other folder",
    [
        _d.ignored_path({}, p, "C:\\")
        for p in (
            "C:/dev/proj/.claude/plans/tool.py",
            "C:/dev/proj/.claude/todos/x.json",
            "C:/dev/proj/.claude/projects/a/b.py",
            "C:/dev/proj/src/memory/notes.md",
            "C:/dev/proj/memory/schema.md",
        )
    ],
    [False] * 5,
)
check(
    "K-T5 ... the home's are still out of scope (plans, todos, transcripts, memory)",
    [
        _d.ignored_path({}, _d.os.path.join(_HOME, ".claude", *parts), "C:\\")
        for parts in (
            ("plans", "x.md"),
            ("todos", "a.json"),
            ("projects", "C--dev-x", "s.jsonl"),
            ("projects", "C--dev-x", "memory", "note.md"),
        )
    ],
    [True] * 4,
)
check(
    "K-T5 ... and the rest of the home's .claude (hooks, settings) is code",
    [
        _d.ignored_path({}, _d.os.path.join(_HOME, ".claude", *parts), "C:\\")
        for parts in (("hooks", "guard.py"), ("settings.json",), ("memory", "x.md"))
    ],
    [False] * 3,
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
