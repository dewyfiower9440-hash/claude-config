"""Scenario tests for ~/.claude/hooks/discipline.py (run with python -I)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "hooks" / "discipline.py")
# Plan approvals made by these tests go to an isolated store, never the real one.
APPROVAL_HOME = Path(__file__).resolve().parent / "_tmp_discipline_approvals"
shutil.rmtree(APPROVAL_HOME, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(APPROVAL_HOME)
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
    call("prompt", session, prompt_id=prompt_id, prompt="continue", cwd=cwd)


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
import discipline
import notify

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

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
