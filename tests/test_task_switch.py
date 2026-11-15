"""P1-01b: a new approval in the middle of a turn does not turn the previous task's own,
in-scope edits into "out of scope" - and cannot be used to hide any other edit.

    python -I tests/test_task_switch.py

A throw-away repository outside %TEMP% (edits under %TEMP% are not counted), an isolated
temp folder and approval home. Plan alpha covers src/**, plan beta covers lib/**.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = HERE / "_tmp_switch"
PROJ = Path("C:/dev/_switch_proj")
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(ROOT / "home")
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
sys.path.insert(0, str(HOOKS))
import approvals

approvals.Store(ROOT / "home", ROOT / "w" / "hwm").admin_activate(
    lambda code: True, "test"
)
ENV = dict(os.environ, TMPDIR=str(TMPD), TEMP=str(TMPD), TMP=str(TMPD))
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:150],
        "" if ok else f"(want {want!r})",
    )


def plan(task: str, scope: str, status: str = "in-progress") -> str:
    return (
        f"---\nstatus: {status}\ntask: {task}\n---\n\n# {task}\n\n"
        f"## 변경 범위\n- {scope}\n\n## 단계\n- [ ] 1. a\n"
    )


PLANS = PROJ / "docs" / "plans"
PLANS.mkdir(parents=True)
for d in ("src", "lib", "other"):
    (PROJ / d).mkdir()
    (PROJ / d / "keep.txt").write_text("k\n", encoding="utf-8")
FILE = {"alpha": PLANS / "2026-11-15-alpha.md", "beta": PLANS / "2026-11-15-beta.md"}
TEXT = {"alpha": plan("alpha", "src/**"), "beta": plan("beta", "lib/**")}
for task, f in FILE.items():
    f.write_text(TEXT[task], encoding="utf-8")
(PROJ / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)


def hook(mode: str, s: str, prompt_id: str = "p1", **payload: object) -> dict:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": prompt_id, **payload}
        ),
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-300:])
    return json.loads(p.stdout) if p.stdout.strip() else {}


def start(prompt: str = "work") -> str:
    s = uuid.uuid4().hex
    hook("prompt", s, prompt=prompt)
    return s


def approve(s: str, task: str, prompt_id: str = "p1") -> None:
    hook(
        "post",
        s,
        prompt_id,
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": TEXT[task], "filePath": str(FILE[task])},
    )


def edit(s: str, rel: str, prompt_id: str = "p1") -> None:
    f = PROJ / rel
    body = f"v = {uuid.uuid4().hex!r}\n"
    f.write_text(body, encoding="utf-8")
    hook(
        "post",
        s,
        prompt_id,
        tool_name="Write",
        tool_input={"file_path": str(f), "content": body},
        tool_use_id=uuid.uuid4().hex,
    )


def stop(s: str, prompt_id: str = "p1") -> tuple[list[str], str]:
    """(names of the files this turn is held to have changed, the Stop's reason)."""
    out = hook("stop", s, prompt_id)
    state = json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )
    names = sorted(Path(f).name for f in state.get("turn_changes", []))
    return names, str(out.get("reason") or "")


def may_write(s: str, rel: str, prompt_id: str = "p1") -> bool:
    out = hook(
        "pre",
        s,
        prompt_id,
        tool_name="Write",
        tool_input={"file_path": str(PROJ / rel), "content": "x\n"},
    )
    return out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def task_of(s: str) -> object:
    state = json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )
    return (state.get("approval") or {}).get("task_id")


def close_alpha() -> None:
    FILE["alpha"].write_text(plan("alpha", "src/**", "done"), encoding="utf-8")


def reopen_alpha() -> None:
    FILE["alpha"].write_text(TEXT["alpha"], encoding="utf-8")


OUT = "변경 범위 밖"

s1 = start()
approve(s1, "alpha")
edit(s1, "src/a.py")
approve(s1, "beta")
edit(s1, "lib/b.py")
names, why = stop(s1)
check(
    "B-T1 alpha's in-scope edit, then beta approved and worked on: nothing is out of scope",
    (names, OUT in why),
    (["b.py"], False),
)

s = start()
edit(s, "src/x.py")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T2 an edit before any approval is not covered by the approval that follows",
    (names, OUT in why),
    (["x.py"], True),
)

s = start()
approve(s, "alpha")
edit(s, "other/y.py")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T3 an edit outside alpha's scope stays reported after the switch",
    (names, OUT in why),
    (["y.py"], True),
)

s = start()
approve(s, "alpha")
edit(s, "src/a.py")
approve(s, "beta")
edit(s, "src/a.py")
names, why = stop(s)
check(
    "B-T4 the same file edited again after the switch is reported",
    (names, OUT in why),
    (["a.py"], True),
)

s = start()
approve(s, "alpha")
(PROJ / "src" / "a.py").unlink()
approve(s, "beta")
names, why = stop(s)
check(
    "B-T6 a file removed under alpha stays covered",
    (names, OUT in why),
    ([], False),
)
(PROJ / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")

hook("prompt", s1, "p2", prompt="task:beta 계속")
edit(s1, "src/a.py", "p2")
names, why = stop(s1, "p2")
check(
    "B-T7 the next turn starts with nothing covered: an edit outside beta is reported",
    (names, OUT in why),
    (["a.py"], True),
)

s = start()
approve(s, "alpha")
edit(s, "src/a.py")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T8 covered means in scope, not verified: the Stop still asks for verification",
    (names, OUT in why, "검증" in why),
    ([], False, True),
)

s = start()
approve(s, "alpha")
edit(s, "src/a.py")
edit(s, "other/y.py")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T9 of two edits under alpha only the out-of-scope one is reported",
    (names, OUT in why, "1개" in why),
    (["y.py"], True, True),
)

s = start()
edit(s, "src/x.py")
approve(s, "alpha")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T10 an edit made before alpha was approved is not covered by passing through alpha",
    (names, OUT in why),
    (["x.py"], True),
)

s = start()
edit(s, "src/x.py")
approve(s, "alpha")
edit(s, "src/a.py")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T10 ... while what was edited under alpha is",
    (names, OUT in why),
    (["x.py"], True),
)

# The usual hand-over: alpha's plan is closed (status: done), then beta is approved.
for label, with_shell in (
    ("a shell command in between", True),
    ("nothing in between", False),
):
    reopen_alpha()
    s = start()
    approve(s, "alpha")
    edit(s, "src/a.py")
    close_alpha()
    if with_shell:
        shell = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_use_id": uuid.uuid4().hex,
        }
        hook("pre", s, **shell)
        hook("post", s, tool_response={}, **shell)
    approve(s, "beta")
    allowed = may_write(s, "lib/b.py")
    edit(s, "lib/b.py")
    names, why = stop(s)
    check(
        f"B-T11 alpha closed as done, then beta approved ({label}): beta works, alpha's edit is covered",
        (allowed, "a.py" in names, "b.py" in names, OUT in why, task_of(s)),
        (True, False, True, False, "beta"),
    )

reopen_alpha()
s = start()
approve(s, "alpha")
edit(s, "src/a.py")
close_alpha()
edit(s, "other/y.py")
approve(s, "beta")
names, why = stop(s)
check(
    "B-T11 ... an out-of-scope edit is still reported after alpha was closed",
    ("a.py" in names, "y.py" in names, OUT in why),
    (False, True, True),
)

reopen_alpha()
s = start()
approve(s, "alpha")
edit(s, "src/a.py")
FILE["alpha"].write_text(plan("alpha", "src/**", "blocked"), encoding="utf-8")
approve(s, "beta")
allowed = may_write(s, "lib/b.py")
names, why = stop(s)
check(
    "B-T5 alpha parked (blocked) before the switch: its edit is not covered, beta still works",
    ("a.py" in names, allowed, task_of(s)),
    (True, True, "beta"),
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
