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
ROOT = HERE / "_tmp_task_switch"  # its own: test_project_switch has `_tmp_switch`
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

# ---- devenv-fix-g: what was changed inside the scope while the approval was valid is
# recorded as covered at that moment, so losing the approval later (here: a relayed prompt
# the session did not start) does not turn that work into unapproved or out-of-scope work.
LOST = "<task-notification>\n<task-id>zz9unknown</task-id>\n</task-notification>"
NO_PLAN = "승인된 계획 없이"


def lose(s: str, prompt_id: str = "r1") -> None:
    hook("prompt", s, prompt_id, prompt=LOST)


def edit_at(s: str, rel: str, prompt_id: str) -> None:
    edit(s, rel, prompt_id)


def open_after(s: str, prompt_id: str = "r1") -> tuple[list[str], str]:
    """After a Stop: (the listed changes the Stop still asks about, the Stop's reason). A
    change made inside the scope of an approval valid at that moment stays listed (the risk
    score and the test-gap note still see it) but is answered."""
    _listed, why = stop(s, prompt_id)
    state = json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )
    answered = set(state.get("turn_answered") or [])
    return sorted(
        Path(f).name for f in state.get("turn_changes", []) if f not in answered
    ), why


reopen_alpha()


s = start()
approve(s, "alpha")
for n in range(6):
    edit(s, f"src/g{n}.py")
lose(s)
names, why = open_after(s)
check(
    "G-T1 six in-scope edits under a valid approval, the approval lost afterwards: "
    "none of them is counted as unapproved",
    (task_of(s), names, NO_PLAN in why),
    (None, [], False),
)
edit_at(s, "src/g0.py", "r1")
edit_at(s, "src/late.py", "r1")
names, why = open_after(s)
check(
    "G-T2 after the loss: the same file edited again and a new file are both counted",
    names,
    ["g0.py", "late.py"],
)

s = start()
approve(s, "alpha")
edit(s, "src/h.py")
lose(s)
approve(s, "beta", "r1")
edit_at(s, "lib/h2.py", "r1")
names, why = open_after(s)
check(
    "G-T3 alpha's work, alpha lost, beta approved and worked on in the same turn: "
    "alpha's file is not out of beta's scope",
    (names, OUT in why),
    ([], False),  # h.py was done under alpha, h2.py under beta: neither is asked about
)

s = start()
approve(s, "alpha")
edit(s, "other/z.py")
edit(s, "src/in.py")
lose(s)
names, why = open_after(s)
check(
    "G-T4 an edit outside the scope is not covered, the one inside is",
    names,
    ["z.py"],
)

s = start()
approve(s, "alpha")
FILE["alpha"].write_text(TEXT["alpha"].replace("1. a", "1. a, and b"), encoding="utf-8")
edit(s, "src/k.py")
lose(s)
names, why = open_after(s)
check(
    "G-T5 an edit made after the plan text changed (approval no longer valid): not covered",
    "k.py" in names,
    True,
)
FILE["alpha"].unlink()
s = start()
approve(s, "alpha")  # approved, plan file not saved: unbound
edit(s, "src/u.py")
lose(s)
names, why = open_after(s)
check(
    "G-T5 an edit under an approval whose plan file is not saved yet: not covered",
    "u.py" in names,
    True,
)
FILE["alpha"].write_text(TEXT["alpha"], encoding="utf-8")


def write_call(s: str, rel: str, tool_use_id: str, pre: bool) -> None:
    """One Write as the harness sends it: PreToolUse (optional), the write, PostToolUse."""
    f = PROJ / rel
    ti = {"file_path": str(f), "content": f"v = {uuid.uuid4().hex!r}\n"}
    if pre:
        hook("pre", s, "p1", tool_name="Write", tool_input=ti, tool_use_id=tool_use_id)
    f.write_text(str(ti["content"]), encoding="utf-8")
    hook("post", s, "p1", tool_name="Write", tool_input=ti, tool_use_id=tool_use_id)


def shell_call(s: str, command: str) -> None:
    hook(
        "post",
        s,
        "p1",
        tool_name="Bash",
        tool_input={"command": command},
        tool_response={"stdout": ""},
        tool_use_id=uuid.uuid4().hex,
    )


s = start()
approve(s, "alpha")
f1 = PROJ / "src" / "r1.py"
ti1 = {"file_path": str(f1), "content": "r = 1\n"}
hook("pre", s, "p1", tool_name="Write", tool_input=ti1, tool_use_id="call-A")
f1.write_text("r = 1\n", encoding="utf-8")
FILE["alpha"].write_text(TEXT["alpha"].replace("1. a", "1. a, and c"), encoding="utf-8")
hook("post", s, "p1", tool_name="Write", tool_input=ti1, tool_use_id="call-A")
write_call(s, "src/r2.py", "call-C", pre=False)  # no PreToolUse of this call was seen
lose(s)
names, why = open_after(s)
check(
    "G-T7 the check PreToolUse made is reused for the same tool call only: the call checked "
    "before the plan changed is covered, another call is checked again and is not",
    ("r1.py" in names, "r2.py" in names),
    (False, True),
)
FILE["alpha"].write_text(TEXT["alpha"], encoding="utf-8")


def cover_state(s: str) -> tuple[object, list[str]]:
    state = json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )
    return (state.get("cover_last") or {}).get("at"), sorted(
        Path(f).name for f in state.get("turn_done_under") or {}
    )


s = start()
approve(s, "alpha")
edit(s, "src/first.py")
at0, done0 = cover_state(s)
(PROJ / "src" / "sh.py").write_text(
    "sh = 1\n", encoding="utf-8"
)  # by no tool the hook saw
shell_call(s, "git status")
at1, done1 = cover_state(s)
shell_call(s, "python gen.py")
at2, done2 = cover_state(s)
check(
    "G-T8 after a read-only shell command the repository is not looked at; after one that "
    "may write it is, and what changed inside the scope is recorded",
    (done0, at1 == at0, done1, at2 != at1, done2),
    (["first.py"], True, ["first.py"], True, ["first.py", "sh.py"]),
)
lose(s)
check(
    "G-T8 ... and a change a read-only command was skipped over is still recorded before a "
    "relayed prompt decides about the approval",
    "sh.py" in open_after(s)[0],
    False,
)

# ---- devenv-fix-h: what the prompt hook was handed is kept for diagnosis (never to decide),
# as a shape only: nothing of the prompt's text reaches the state file.
SECRET_LINE = (
    "please use ghp_deadbeef12Zq9XyLmNoPqRsTuVwXyZaBcDeFgHi and password=hunter2 for "
    "order 12345678, id 3f2a9c1e-77aa-4b1c-9d2e-aabbccddeeff"
)
s = start()
approve(s, "alpha")
hook("prompt", s, "px", prompt="task:alpha\ncontinue")
for n in range(14):
    hook("prompt", s, f"py{n}", prompt="task:alpha\ncontinue")
seen = json.loads(
    (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
).get("prompt_seen", [])
s2 = start()
approve(s2, "alpha")
hook(
    "prompt", s2, "pz", prompt=f"\n{SECRET_LINE}\nsecond line\nthird line with the body"
)
state_text = (TMPD / "claude-discipline" / f"{s2}.json").read_text(encoding="utf-8")
first = json.loads(state_text).get("prompt_seen", [{}])[-1]
check(
    "H-T6 prompt_seen keeps the last 12 prompts as shapes: key names, prompt id, length, a "
    "short hash, the kind of the first two lines, the classification, the task before and "
    "after",
    (
        len(seen),
        seen[-1].get("lines") if seen else None,
        (seen[-1].get("before"), seen[-1].get("after")) if seen else None,
        sorted(first),
        "prompt" in first.get("keys", []) and "session_id" in first.get("keys", []),
        first.get("lines"),
        (first.get("kind"), first.get("before"), first.get("after")),
        (first.get("chars"), len(str(first.get("sha")))),
    ),
    (
        12,
        ["task-line", "text"],
        ("alpha", "alpha"),
        sorted(
            [
                "at",
                "keys",
                "prompt_id",
                "chars",
                "sha",
                "lines",
                "kind",
                "before",
                "after",
            ]
        ),
        True,
        ["text", "text"],
        ("user", "alpha", None),
        (len(f"\n{SECRET_LINE}\nsecond line\nthird line with the body"), 12),
    ),
)
check(
    "H-T6 nothing of the prompt's text is in the state file: no token, no password, no "
    "number, no id, no word of any line",
    sorted(
        word
        for word in (
            "ghp_",
            "deadbeef",
            "Zq9XyLm",
            "hunter2",
            "hunte",
            "password",
            "12345678",
            "3f2a9c1e",
            "please use",
            "second line",
            "third line",
        )
        if word in state_text
    ),
    [],
)
import discipline as disc_h

check(
    "H-T6 line_kind: a fixed label per kind of first line",
    [
        disc_h.line_kind(line)
        for line in (
            "",
            "Another Claude session sent a message:",
            "[SYSTEM NOTIFICATION - NOT USER INPUT]",
            "<task-notification>",
            "<task-id>bq4w1kuxu</task-id>",
            '<agent-message from="a6dc0c74b132b9ed9">',
            "task:devenv-fix-h",
            "/plan something",
            '<system-reminder id="55bb185844a4ba65">',
            '<pasted_content id="204c">',
            "[Image: x]",
            "결정: 1번으로 진행",
        )
    ],
    [
        "none",
        "relay-header",
        "system-notification",
        "task-notification",
        "task-id",
        "agent-message",
        "task-line",
        "slash",
        "tag:system-reminder",
        "tag:pasted_content",
        "bracket",
        "text",
    ],
)

# ---- devenv-fix-h: an approval never ends silently at a prompt, and a result delivered
# while the session is working (same prompt id, bare `<agent-message>` first line) is told
# from a prompt the user typed.
LOST_WORDS = "끊겼습니다"


def said(out: dict) -> str:
    return str(out.get("hookSpecificOutput", {}).get("additionalContext") or "")


def handback(agent_id: str) -> str:
    return (
        f'<agent-message from="{agent_id}">\n'
        "[Subagent hand-back] The text below is the final report of a subagent\n"
        "  done\n</agent-message>"
    )


s = start()
approve(s, "alpha")
out = hook("prompt", s, "p2", prompt="please also fix the other thing")
check(
    "H-T1 a user prompt that names no task while a task is approved: said at once, with "
    "the task id and how to go on; the approval ends as before",
    (LOST_WORDS in said(out), "task:alpha" in said(out), task_of(s)),
    (True, True, None),
)
check(
    "H-T5 the next prompt after that loss says nothing again",
    LOST_WORDS in said(hook("prompt", s, "p3", prompt="and one more thing")),
    False,
)
s = start()
approve(s, "alpha")
out = hook("prompt", s, "p2", prompt="task:alpha\ncontinue")
check(
    "H-T2 a user prompt whose first line names the task: kept, nothing said",
    (LOST_WORDS in said(out), task_of(s)),
    (False, "alpha"),
)
s = start()
approve(s, "alpha")
out = hook("prompt", s, "r1", prompt=LOST)
check(
    "H-T3 a notification this session did not start: the loss is said in that prompt",
    (LOST_WORDS in said(out), "task:alpha" in said(out), task_of(s)),
    (True, True, None),
)
s = start()
quiet = [
    LOST_WORDS in said(hook("prompt", s, "p2", prompt="no task was ever approved"))
]
s = start()
approve(s, "alpha")
hook(
    "post",
    s,
    "p1",
    tool_name="Bash",
    tool_input={"command": "uv run serve", "run_in_background": True},
    tool_response={"stdout": "", "backgroundTaskId": "bgh1"},
    tool_use_id=uuid.uuid4().hex,
)
own = "<task-notification>\n<task-id>bgh1</task-id>\n<status>completed</status>\n</task-notification>"
quiet.append(LOST_WORDS in said(hook("prompt", s, "p1", prompt=own)))
check(
    "H-T4 nothing is said to a session without an approval, nor when the approval is kept",
    (quiet, task_of(s)),
    ([False, False], "alpha"),
)

AGENT = {"agent_id": "ag1", "agent_type": "code-reviewer"}


def mid_work(prompt: str, prompt_id: str) -> tuple[object, bool, int]:
    """Approve alpha, edit one file, start and stop the subagent ag1, then hand the prompt
    hook `prompt` under `prompt_id`: (task still approved, loss said, files still counted)."""
    s = start()
    approve(s, "alpha")
    edit(s, "src/mw.py")
    hook("agent-start", s, "p1", **AGENT)
    hook("agent-stop", s, "p1", **AGENT)
    out = hook("prompt", s, prompt_id, prompt=prompt)
    state = json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )
    return task_of(s), LOST_WORDS in said(out), len(state.get("turn_files") or [])


check(
    "H-T7 a hand-back delivered mid-work (the prompt id of the prompt in progress, the bare "
    "tag first): from a subagent this session started the approval is kept and the turn "
    "goes on; from an unknown sender it ends, and that is said",
    (mid_work(handback("ag1"), "p1"), mid_work(handback("zz9"), "p1")),
    (("alpha", False, 1), (None, True, 1)),
)
MID = "작업 중에 도착한 메시지로는"


def mid_work_reason(prompt: str) -> tuple[object, bool, bool]:
    s = start()
    approve(s, "alpha")
    out = hook("prompt", s, "p1", prompt=prompt)  # the id of the prompt in progress
    return task_of(s), MID in said(out), "task:alpha" in said(out)


check(
    "H-T7 text under the prompt id in progress cannot continue the approval, even when it "
    "names the task: the loss is said with the reason that fits (ask in a new prompt)",
    (mid_work_reason("task:alpha\nplease go on"), mid_work_reason("also fix x")),
    ((None, True, True), (None, True, True)),
)
check(
    "H-T7 the same text as a prompt of its own (a new prompt id: typed or pasted by the "
    "user) keeps nothing, and any other text under the prompt id in progress is a relay "
    "that keeps nothing either; both are said",
    (mid_work(handback("ag1"), "p9"), mid_work("some other text", "p1")),
    ((None, True, 0), (None, True, 1)),
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
