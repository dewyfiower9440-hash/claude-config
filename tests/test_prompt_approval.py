"""What a prompt does to the approval a session holds (devenv-fix-h, -i, -j, -j2): it never
ends silently, a subagent's hand-back is recognised by the session's own record of that
subagent, and a prompt hook that fails drops the approval and says so.

    python -I tests/test_prompt_approval.py

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

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = (
    TMP_ROOT / "_tmp_prompt_approval"
)  # its own: test_project_switch has `_tmp_switch`
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_prompt_proj"
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
    p = inproc.run(
        HOOKS / "discipline.py",
        [mode],
        json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": prompt_id, **payload}
        ),
        env=ENV,
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


# Split from tests/test_task_switch.py (2026-11-18, devenv-fix-l): the cases of the prompt
# hook's approval continuity, unchanged. The lines above are that module's prelude with its
# two work folders renamed (both modules run at the same time); the definition below is the
# one this half used from the other half's body.
LOST = "<task-notification>\n<task-id>zz9unknown</task-id>\n</task-notification>"


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
                "waited",  # devenv-fix-j2: numbers and a flag, still no text
                "stop_seen",
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
    "H-T7 any other text under the prompt id in progress is a relay that keeps nothing, "
    "and that is said",
    mid_work("some other text", "p1"),
    (None, True, 1),
)

# ---- devenv-fix-i: a hand-back is recognised by the id of a subagent this session started
# and saw end (SubagentStart, then SubagentStop, then the prompt), whether it arrives
# mid-work or as a prompt of its own (observed 2026-11-17: the prompt hook is handed the bare
# tag and a new prompt id then). The prompt id and the shape of the text only assist.
HEADER = "Another Claude session sent a message:\n"
FOREIGN = ROOT / "foreign"
FOREIGN.mkdir()
subprocess.run(["git", "init", "-q"], cwd=FOREIGN, check=True)


def handed(
    prompt: str,
    prompt_id: str = "p9",
    *,
    started: bool = True,
    stopped: bool = True,
    approved: bool = True,
    closed: bool = False,
    cwd: Path = PROJ,
) -> tuple[object, bool, int]:
    """Approve alpha (unless not `approved`), edit one file, record the start and the stop
    of the subagent ag1 as told, then hand the prompt hook `prompt` under `prompt_id`:
    (task still approved, loss said, files still counted)."""
    s = start()
    if approved:
        approve(s, "alpha")
        edit(s, "src/hb.py")
    if started:
        hook("agent-start", s, "p1", **AGENT)
    if stopped:
        hook("agent-stop", s, "p1", **AGENT)
    if closed:
        close_alpha()
    try:
        out = hook("prompt", s, prompt_id, prompt=prompt, cwd=str(cwd))
    finally:
        reopen_alpha()
    state = json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )
    TOLD.append(said(out))
    return task_of(s), LOST_WORDS in said(out), len(state.get("turn_files") or [])


TOLD: list[str] = []  # what each `handed` prompt was told, in order
NOT_HANDBACK = "subagent의 결과 보고가 아님"
check(
    "I-T1 the hand-back of a subagent this session started and saw end, arriving as a "
    "prompt of its own (new prompt id, bare tag): the approval is kept, nothing is said, "
    "the turn's counts stay",
    handed(handback("ag1")),
    ("alpha", False, 1),
)
check(
    "I-T2 the same text while that subagent has not ended (no SubagentStop recorded): a "
    "user prompt, the approval ends, said at once",
    (handed(handback("ag1"), stopped=False), NOT_HANDBACK in TOLD[-1]),
    ((None, True, 0), True),
)
check(
    "I-T3 an id this session never started: a user prompt, the approval ends, said at once",
    (
        handed(handback("zz9")),
        NOT_HANDBACK in TOLD[-1],
        handed(handback("ag1"), started=False, stopped=False),
        NOT_HANDBACK in TOLD[-1],
    ),
    ((None, True, 0), True, (None, True, 0), True),
)
check(
    "I-T4 mid-work (the prompt id in progress) the rule is the same: only a subagent that "
    "ended keeps the approval",
    (handed(handback("ag1"), "p1"), handed(handback("ag1"), "p1", stopped=False)),
    (("alpha", False, 1), (None, True, 1)),
)
check(
    "I-T5 with the relay header line too: kept after the stop, not before it",
    (
        handed(HEADER + handback("ag1")),
        handed(HEADER + handback("ag1"), stopped=False),
        handed(HEADER + handback("zz9")),
    ),
    (("alpha", False, 1), (None, True, 1), (None, True, 1)),
)
check(
    "I-T6 the conditions for keeping are as before: not in another git root, not a plan "
    "closed meanwhile (told as finished, not as lost), and a hand-back never creates an "
    "approval",
    (
        handed(handback("ag1"), cwd=FOREIGN)[0],
        handed(handback("ag1"), closed=True)[:2],
        "done으로 닫혀" in TOLD[-1],
        handed(handback("ag1"), approved=False),
        TOLD[-1],
    ),
    (None, (None, False), True, (None, False, 0), ""),
)
check(
    "I-T7 the tag counts only alone on the first line: after other text, with text on its "
    "line, or with an id of other characters it is an ordinary prompt",
    (
        handed("see this\n" + handback("ag1")),
        handed(handback("ag1").replace(">\n", "> and more\n", 1)),
        handed(handback("ag 1")),
    ),
    ((None, True, 0), (None, True, 0), (None, True, 0)),
)

# ---- devenv-fix-i: entries an earlier version wrote into prompt_seen (they had a `head`
# field with the masked start of the text) lose every field that is not a shape
s = start()
state_file = TMPD / "claude-discipline" / f"{s}.json"
state = json.loads(state_file.read_text(encoding="utf-8"))
state["prompt_seen"] = [
    {
        "at": 1.0,
        "keys": ["prompt"],
        "prompt_id": "old",
        "chars": 9,
        "kind": "user",
        "before": None,
        "after": None,
        "head": ["leftover words of a prompt"],
    },
    "not an entry",
]
state_file.write_text(json.dumps(state), encoding="utf-8")
hook("prompt", s, "p2", prompt="next prompt")
state_text = state_file.read_text(encoding="utf-8")
seen = json.loads(state_text).get("prompt_seen", [])
check(
    "I-T9 prompt_seen keeps only the fields of a shape: an old entry's text is gone with "
    "the next prompt, the entry itself stays, and what is not an entry is dropped",
    (
        "leftover" in state_text,
        '"head"' in state_text,
        [e.get("prompt_id") for e in seen if isinstance(e, dict)][-2:],
        all(isinstance(e, dict) for e in seen),
    ),
    (False, False, ["old", "p2"], True),
)

# ---- devenv-fix-j: a prompt hook that ends in an exception decided nothing, so the approval
# it should have decided on goes (fail-closed) and the prompt is told at once. The failure is
# brought about by a state the shape check accepts and on_prompt cannot use (`turn` is
# infinite: int() raises OverflowError on its first line).
FAILED_WORDS = "프롬프트 hook 처리 중 오류"


def state_of(s: str) -> dict:
    return json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )


def set_turn(s: str, value: float) -> None:
    f = TMPD / "claude-discipline" / f"{s}.json"
    state = json.loads(f.read_text(encoding="utf-8"))
    state["turn"] = value
    f.write_text(json.dumps(state), encoding="utf-8")


def failing_prompt(prompt_id: str, approved: bool = True) -> tuple[object, ...]:
    s = start()
    if approved:
        approve(s, "alpha")
        edit(s, "src/jf.py")
    in_force = bool(disc_h.active_approval(state_of(s), "p1"))
    set_turn(s, float("inf"))
    out = hook("prompt", s, prompt_id, prompt="task:alpha\ngo on")
    told = said(out)
    after = state_of(s)
    # the state is still one on_prompt cannot use: the Stop of that turn must block too
    broken_stop = hook("stop", s, prompt_id).get("decision")
    set_turn(s, 1)
    stopped = hook("stop", s, prompt_id)
    why = str(stopped.get("reason"))
    return (
        in_force,
        (after.get("approval") or {}).get("task_id"),
        # what a write under that prompt id would be decided on: no approval
        disc_h.active_approval(after, prompt_id),
        FAILED_WORDS in told,
        "task:alpha" in told,
        "폐기했습니다" in told,
        after.get("prompt_id") == prompt_id,
        len(after.get("turn_files") or []),
        broken_stop,
        stopped.get("decision"),
        "처리 중 오류" in why,
        ("폐기함" in why, "task:<id>" in why, "다시 제시" in why),
    )


check(
    "J-T1 a prompt of its own whose hook fails: the approval is dropped, the prompt is told "
    "at once with the task and how to go on, the failed prompt's id is recorded; J-T4 the "
    "Stop after it is blocked (also while the state is still unusable) and its reason names "
    "the dropped approval and asks for the task line, not for a new plan",
    failing_prompt("p2"),
    (
        True,
        None,
        None,
        True,
        True,
        True,
        True,
        1,
        "block",
        "block",
        True,
        (True, True, False),
    ),
)
check(
    "J-T2 the same under the prompt id in progress (a message delivered mid-work), where "
    "the approval left behind was still in force: a write under that id finds no approval",
    failing_prompt("p1"),
    (
        True,
        None,
        None,
        True,
        True,
        True,
        True,
        1,
        "block",
        "block",
        True,
        (True, True, False),
    ),
)
check(
    "J-T3 without an approval: none appears, the prompt is told of the failure only; the "
    "first Stop is blocked for the missed event, and with nothing changed and nothing "
    "dropped the one after it has nothing left to say",
    failing_prompt("p2", approved=False),
    (
        False,
        None,
        None,
        True,
        False,
        False,
        True,
        0,
        "block",
        None,
        False,
        (False, False, False),
    ),
)

kept_closed = {"task_id": "alpha", "approval_id": "a1", "content": "c1"}
unit = {
    "approval": {"task_id": "alpha", "approval_id": "a1", "prompt_id": "p1"},
    "prompt_id": "p1",
    "turn_files": ["x.py"],
    "ledger_closed": dict(kept_closed),
}
dropped = disc_h.prompt_failed(unit, {"prompt_id": "p7"}, "OverflowError: x")
check(
    "J-T5 prompt_failed: the approval and the prompt id change, the turn's records and the "
    "closed task's memory stay; it returns the task it dropped, and nothing for a state "
    "without an approval or one it cannot read",
    (
        dropped,
        unit["approval"],
        "폐기" in str(unit.get("approval_warning")),
        unit["prompt_id"],
        unit["turn_files"],
        unit["ledger_closed"] == kept_closed,
        disc_h.prompt_failed({}, {}, "x"),
        disc_h.prompt_failed({"approval": "not a dict"}, {"prompt_id": 3}, "x"),
    ),
    ("alpha", None, True, "p7", ["x.py"], True, None, None),
)


# ---- devenv-fix-j2: the stop record of a subagent and its hand-back prompt come from two hook
# processes started at almost the same time, in either order (observed 2026-11-17: the stop
# record 0.150 s and 0.900 s before the prompt, then 0.97 s after it). The prompt hook waits
# for the stop record of a subagent this session started, outside the session lock, up to a
# limit; the rule itself ("only after it ended") is unchanged.
def hook_with(env: dict, mode: str, s: str, prompt_id: str, **payload: object) -> dict:
    p = inproc.run(
        HOOKS / "discipline.py",
        [mode],
        json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": prompt_id, **payload}
        ),
        env=env,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-300:])
    return json.loads(p.stdout) if p.stdout.strip() else {}


def no_stop(
    prompt: str, started: bool = True, stopped: bool = False
) -> tuple[object, ...]:
    s = start()
    approve(s, "alpha")
    if started:
        hook("agent-start", s, "p1", **AGENT)
    if stopped:
        hook("agent-stop", s, "p1", **AGENT)
    brief = dict(ENV, CLAUDE_DISCIPLINE_HANDBACK_WAIT_S="0.4")
    out = hook_with(brief, "prompt", s, "p9", prompt=prompt)
    seen = state_of(s).get("prompt_seen", [{}])[-1]
    waited = seen.get("waited")
    return (
        task_of(s),
        NOT_HANDBACK in said(out),
        seen.get("stop_seen"),
        "none" if waited is None else 0.3 <= waited < 5,
    )


check(
    "J2-T2 the stop record does not arrive within the limit: handled as a user prompt as "
    "before, told at once with the reason, the wait is recorded",
    no_stop(handback("ag1")),
    (None, True, False, True),
)
check(
    "J2-T3 nothing is waited for when the subagent has already ended, when the id is not "
    "one this session started, or for ordinary text; the decisions are as before",
    (
        no_stop(handback("ag1"), stopped=True),
        no_stop(handback("zz9")),
        no_stop(handback("ag1"), started=False),
        no_stop("just some text"),
    ),
    (
        ("alpha", False, None, "none"),
        (None, True, None, "none"),
        (None, True, None, "none"),
        (None, False, None, "none"),
    ),
)


def unit_wait(
    states: list[object], limit: object = 3.0, prompt: str = ""
) -> tuple[object, ...]:
    """await_agent_stop with a fake clock: each sleep advances it, each read takes the next
    of `states` (the last one repeats). (result, number of reads, seconds slept)."""
    now = [0.0]
    reads = [0]

    def read(_path: object) -> object:
        reads[0] += 1
        state = states[min(reads[0], len(states)) - 1]
        if isinstance(state, Exception):
            raise state
        return state

    def sleep(seconds: float) -> None:
        now[0] += seconds

    got = disc_h.await_agent_stop(
        {"prompt": prompt or handback("ag1")},
        Path("unused"),
        read=read,
        clock=lambda: now[0],
        sleep=sleep,
        limit=limit,
    )
    return got, reads[0], round(now[0], 3)


RUNNING = {
    "agents": {"code-reviewer:ag1": {"started": 5.0, "stopped": None, "runs": 1}}
}
ENDED = {"agents": {"code-reviewer:ag1": {"started": 5.0, "stopped": 6.0, "runs": 1}}}
RESUMED = {
    "agents": {"code-reviewer:ag1": {"started": 7.0, "stopped": None, "runs": 2}}
}
check(
    "J2-T4 await_agent_stop: found at the first look after a pause, at the third, never "
    "(the limit); no wait without a state, with one that cannot be read or is no object, "
    "for an id not started here, for one already ended, for a prompt without the tag; a "
    "read that fails while waiting counts as 'not yet'; a limit of zero or below looks no "
    "further; a subagent that was resumed (its end cleared again) is waited for like a "
    "running one",
    (
        unit_wait([RUNNING, ENDED]),
        unit_wait([RUNNING, RUNNING, RUNNING, ENDED]),
        unit_wait([RUNNING], limit=0.35),
        unit_wait([None]),
        unit_wait([OSError("busy")]),
        unit_wait([["not", "an", "object"]]),
        unit_wait(
            [{"agents": {"code-reviewer:other": {"started": 5.0, "stopped": None}}}]
        ),
        unit_wait([ENDED]),
        unit_wait([RUNNING], prompt="no tag here"),
        unit_wait([RUNNING, OSError("busy"), ENDED]),
        unit_wait([RUNNING], limit=0.0),
        unit_wait([RUNNING], limit=-1.0),
        unit_wait([RESUMED, RESUMED], limit=0.2),
    ),
    (
        ({"waited": 0.1, "stop_seen": True}, 2, 0.1),
        ({"waited": 0.3, "stop_seen": True}, 4, 0.3),
        ({"waited": 0.35, "stop_seen": False}, 5, 0.35),
        (None, 1, 0.0),
        (None, 1, 0.0),
        (None, 1, 0.0),
        (None, 1, 0.0),
        (None, 1, 0.0),
        (None, 0, 0.0),
        ({"waited": 0.2, "stop_seen": True}, 3, 0.2),
        ({"waited": 0.0, "stop_seen": False}, 1, 0.0),
        ({"waited": 0.0, "stop_seen": False}, 1, 0.0),
        ({"waited": 0.2, "stop_seen": False}, 3, 0.2),
    ),
)
check(
    "J2-T4 the limit from the environment: a number of seconds, anything else (not a "
    "number, negative, not finite) is the default",
    [
        disc_h.handback_wait_limit({"CLAUDE_DISCIPLINE_HANDBACK_WAIT_S": raw})
        for raw in ("0.4", "0", "-1", "abc", "nan", "inf", "")
    ]
    + [disc_h.handback_wait_limit({})],
    [0.4, 0.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0],
)
s = start()
state_file = TMPD / "claude-discipline" / f"{s}.json"
state = json.loads(state_file.read_text(encoding="utf-8"))
state["prompt_seen"] = [
    {
        "at": 1.0,
        "keys": ["prompt"],
        "prompt_id": "old",
        "chars": 9,
        "sha": "abc",
        "lines": ["text"],
        "kind": "user",
        "before": None,
        "after": None,
    },
]
state_file.write_text(json.dumps(state), encoding="utf-8")
hook("prompt", s, "p2", prompt="next prompt")
seen = state_of(s).get("prompt_seen", [])
check(
    "J2-T6 an entry written before the two fields existed stays as it is; a new entry "
    "carries them (None when nothing was waited for)",
    (
        sorted(seen[0]) == sorted(state["prompt_seen"][0]),
        ("waited" in seen[-1], seen[-1].get("waited"), seen[-1].get("stop_seen")),
    ),
    (True, (True, None, None)),
)

# The failures above happen on on_prompt's first line. run_hook takes the handler as an
# argument, so the other places are reached in this process without a switch in the hook:
# before the handler, after the handler decided, after it had something to tell, and with a
# state that cannot be read or saved.
import contextlib
import io

import session_store
import worktree


def in_process(
    *,
    handler: object = None,
    broken: tuple[object, str] | None = None,
    prompt_id: str = "p1",
) -> tuple[object, ...]:
    """Approve alpha in a session, then run the prompt hook in this process with `handler`
    (default: the real one) while `broken` (module, name) raises: (task approved on disk
    afterwards, what the prompt was told as (failure, dropped, not recorded, the handler's
    own text), number of JSON objects printed)."""
    s = start()
    approve(s, "alpha")
    path = TMPD / "claude-discipline" / f"{s}.json"
    data = {
        "session_id": s,
        "cwd": str(PROJ),
        "prompt_id": prompt_id,
        "prompt": "task:alpha\ngo on",
    }

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("broken on purpose")

    original = getattr(broken[0], broken[1]) if broken else None
    disc_h._prompt_failure.clear()
    disc_h._prompt_pending.clear()
    disc_h._prompt_told = False
    out = io.StringIO()
    # the failures are made on purpose: their logged tracebacks would be this module's last
    # output lines, which the gate shows as its result
    import logging  # the hook itself imports it only when it logs (lazylog)

    logging.disable(logging.CRITICAL)
    try:
        if broken:
            setattr(broken[0], broken[1], boom)
        with contextlib.redirect_stdout(out):
            disc_h.run_hook("prompt", data, handler or disc_h.on_prompt, path)
    finally:
        logging.disable(logging.NOTSET)
        if broken:
            setattr(broken[0], broken[1], original)
    printed = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    told = "\n".join(said(json.loads(ln)) for ln in printed)
    return (
        (state_of(s).get("approval") or {}).get("task_id"),
        (
            FAILED_WORDS in told,
            "폐기했습니다" in told,
            "기록하지 못했습니다" in told,
            "handler text" in told,
        ),
        len(printed),
    )


def decides_then_fails(data: dict, state: dict) -> None:
    state["approval"] = {"task_id": "alpha", "approval_id": "x", "prompt_id": "p1"}
    disc_h._prompt_pending.append("handler text")
    raise RuntimeError("after the decision")


check(
    "J-T7 a failure before the handler runs (the git budget of the run): the approval is "
    "dropped all the same and the prompt is told",
    in_process(broken=(worktree, "set_budget")),
    (None, (True, True, False, False), 1),
)
check(
    "J-T8 a failure after the handler decided to keep the approval and had its own text to "
    "tell: the approval goes, and one JSON object carries both texts",
    in_process(handler=decides_then_fails),
    (None, (True, True, False, True), 1),
)
check(
    "J-T9 the state cannot be saved: the prompt is told that dropping the approval was not "
    "recorded, not that it was dropped",
    in_process(broken=(session_store, "save")),
    ("alpha", (True, False, True, False), 1),
)
check(
    "J-T10 the state cannot be read: nothing is written over it, and the prompt is told "
    "the same",
    in_process(broken=(session_store, "load")),
    ("alpha", (True, False, True, False), 1),
)
check(
    "J-T11 a prompt hook that does not fail prints what the handler had to tell, once",
    in_process(handler=lambda d, st: disc_h._prompt_pending.append("handler text")),
    ("alpha", (False, False, False, True), 1),
)


def late_stop() -> tuple[object, ...]:
    """The hand-back prompt is being handled and the stop record is not there yet: the real
    SubagentStop hook runs (as its own process) between the prompt hook's first and second
    look at the state. The order does not depend on how fast processes start."""
    s = start()
    approve(s, "alpha")
    hook("agent-start", s, "p1", **AGENT)
    path = TMPD / "claude-discipline" / f"{s}.json"
    looks = [0]
    stop_ok: list[bool] = []

    def read(state_path: Path) -> object:
        looks[0] += 1
        if looks[0] == 2:
            # J2-T5: the waiting prompt hook holds no lock, so this hook can record the end
            hook("agent-stop", s, "p1", **AGENT)
            stop_ok.append(disc_h.finished_agent(state_of(s), "ag1"))
        return disc_h.peek_state(state_path)

    real_wait, real_t0 = disc_h.await_agent_stop, disc_h._T0
    disc_h._prompt_failure.clear()
    disc_h._prompt_pending.clear()
    disc_h._handback_wait.clear()
    disc_h._prompt_told = False
    out = io.StringIO()
    try:
        disc_h._T0 = disc_h.time.monotonic()  # this run starts now, like a hook process
        disc_h.await_agent_stop = lambda data, state_path: real_wait(
            data, state_path, read=read, limit=3.0
        )
        with contextlib.redirect_stdout(out):
            disc_h.run_hook(
                "prompt",
                {
                    "session_id": s,
                    "cwd": str(PROJ),
                    "prompt_id": "p9",
                    "prompt": handback("ag1"),
                },
                disc_h.on_prompt,
                path,
            )
    finally:
        disc_h.await_agent_stop, disc_h._T0 = real_wait, real_t0
        disc_h._handback_wait.clear()
    printed = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    seen = state_of(s).get("prompt_seen", [{}])[-1]
    waited = seen.get("waited")
    return (
        task_of(s),
        any(LOST_WORDS in said(json.loads(ln)) for ln in printed),
        seen.get("kind"),
        seen.get("stop_seen"),
        isinstance(waited, float) and 0 < waited < 3.5,
        looks[0],
        stop_ok,
    )


check(
    "J2-T1 the stop record arrives after the hand-back prompt hook has started: the hook "
    "waits for it (second look), the approval is kept, the wait is recorded; J2-T5 the "
    "SubagentStop hook records the end while the prompt hook waits (no lock is held)",
    late_stop(),
    ("alpha", False, "relay", True, True, 2, [True]),
)


# ---- devenv-final 23a: the hand-back of a subagent this session launched with the Agent
# tool keeps the approval even when no SubagentStart/SubagentStop was recorded for it
# (observed 2026-11-18 with a web-fetch agent: its report ended the approval mid-task). The
# hook's own record of the launch is the Agent tool's PostToolUse, which names the agent.
def launched(response: object, tool: str = "Agent", prompt_id: str = "p1") -> str:
    s = start()
    approve(s, "alpha")
    hook(
        "post",
        s,
        prompt_id,
        tool_name=tool,
        tool_input={"subagent_type": "web-fetch", "prompt": "read a page"},
        tool_response=response,
        tool_use_id=uuid.uuid4().hex,
    )
    return s


RESULT_TEXT = (
    "the report\nagentId: af00d1 (use SendMessage with to: 'af00d1' to continue)"
)
for label, response in (
    ("an agentId field", {"agentId": "af00d1", "content": []}),
    ("an agent_id field", {"agent_id": "af00d1"}),
    ("the id in the result text", {"content": [{"type": "text", "text": RESULT_TEXT}]}),
    ("the result as plain text", RESULT_TEXT),
):
    s = launched(response)
    out = hook("prompt", s, "p1", prompt=handback("af00d1"))
    check(
        f"A-T1 the launch recorded from {label}: its hand-back keeps the approval, silently",
        (task_of(s), LOST_WORDS in said(out)),
        ("alpha", False),
    )
s = launched({"agentId": "af00d1"}, tool="Task")
check(
    "A-T1 ... the same under the tool's older name, and with the relay header line",
    (
        LOST_WORDS
        in said(
            hook(
                "prompt",
                s,
                "p1",
                prompt="Another Claude session sent a message while you were working:\n"
                + handback("af00d1"),
            )
        ),
        task_of(s),
    ),
    (False, "alpha"),
)
s = launched({"agentId": "af00d1"})
out = hook("prompt", s, "p1", prompt=handback("someone-else"))
check(
    "A-T2 a hand-back naming an agent this session did not launch still ends the approval",
    (task_of(s), LOST_WORDS in said(out)),
    (None, True),
)
s = launched({"note": "no id in it"})
out = hook("prompt", s, "p1", prompt=handback("af00d1"))
check(
    "A-T2 ... and so does one whose launch recorded no id",
    (task_of(s), LOST_WORDS in said(out)),
    (None, True),
)
s = start()
hook(
    "post",
    s,
    "p1",
    tool_name="Agent",
    tool_input={"subagent_type": "web-fetch"},
    tool_response={"agentId": "af00d1"},
    tool_use_id=uuid.uuid4().hex,
)
hook("prompt", s, "p1", prompt=handback("af00d1"))
check("A-T3 a hand-back never creates an approval", task_of(s), None)
# devenv-final 27 (measured with `claude -p` and a probe hook, 2026-11-18): the Agent tool's
# PostToolUse carries {"agentId", "status": "async_launched", ...}; a subagent launched in
# the background reports with a task notification whose id is that agentId; a Bash command
# that ran into its timeout arrives as PostToolUseFailure ("Command timed out"), never as
# a PostToolUse.
MEASURED = {
    "isAsync": True,
    "status": "async_launched",
    "agentId": "abba910d67cf82ff9",
    "description": "d",
    "prompt": "p",
    "outputFile": "o",
    "canReadOutputFile": True,
    "canContinueAgent": True,
}
s = launched(MEASURED)
notification = (
    "<task-notification>\n<task-id>abba910d67cf82ff9</task-id>\n"
    "<status>completed</status>\n</task-notification>"
)
out = hook("prompt", s, "p9", prompt=notification)
check(
    "A-T5 the measured shape: the launch is recorded, and the completion notification of "
    "that agent (a new prompt id) keeps the approval",
    (task_of(s), LOST_WORDS in said(out)),
    ("alpha", False),
)
s = launched(MEASURED)
out = hook(
    "prompt",
    s,
    "p9",
    prompt=notification.replace("abba910d67cf82ff9", "0000000000000000f"),
)
check(
    "A-T5 ... a notification with another id does not",
    (task_of(s), LOST_WORDS in said(out)),
    (None, True),
)
s = launched(MEASURED)
out = hook(
    "prompt",
    s,
    "p9",
    prompt=notification + "\ntask:alpha\nrecover-state",
)
check(
    "A-T6 a first-line command inside a relayed prompt is not the user's: nothing is "
    "recovered or re-armed by it (the approval is kept only because the agent is this "
    "session's)",
    ("recover" in said(out).lower(), task_of(s)),
    (False, "alpha"),
)
check(
    "A-T4 the ids are kept as a bounded list of plain ids",
    (
        disc_h.agent_ids({"agentId": "ok_1-2.x"}),
        disc_h.agent_ids({"agentId": "bad id!"}),
        disc_h.agent_ids(["not", "a", "dict"]),
        disc_h.agent_ids(None),
        disc_h.MAX_LAUNCHED_AGENTS >= 20,
    ),
    (["ok_1-2.x"], [], [], [], True),
)

# ---- lost-approval-block (2026-11-25): once an approval the session held is lost at a
# prompt, edits in that repository are refused until an approval stands again. Observed
# 2026-11-24: the loss was said, the session took the word "승인" in the user's message for
# an approval and edited six files; under warn the hook let five of them through.
NO_RECORD = "승인 기록 없음: 계획 승인 다시 받기"


def pre(
    s: str, prompt_id: str, env: dict[str, str] | None = None, **payload: object
) -> dict:
    p = inproc.run(
        HOOKS / "discipline.py",
        ["pre"],
        json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": prompt_id, **payload}
        ),
        env=env or ENV,
    )
    assert p.returncode == 0, ("pre", p.returncode, p.stderr[-300:])
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    return out.get("hookSpecificOutput", {})


def write_refusal(
    s: str, target: Path, prompt_id: str, env: dict[str, str] | None = None
) -> str:
    """Why a Write of that file is refused ("" when it is not)."""
    out = pre(
        s,
        prompt_id,
        env,
        tool_name="Write",
        tool_input={"file_path": str(target), "content": "x\n"},
    )
    return (
        str(out.get("permissionDecisionReason") or "")
        if out.get("permissionDecision") == "deny"
        else ""
    )


def shell_refusal(s: str, command: str, prompt_id: str) -> str:
    out = pre(s, prompt_id, tool_name="Bash", tool_input={"command": command})
    return (
        str(out.get("permissionDecisionReason") or "")
        if out.get("permissionDecision") == "deny"
        else ""
    )


def lose(prompt: str, prompt_id: str = "p2") -> str:
    """A session that was approved for alpha and then got this prompt."""
    s = start()
    approve(s, "alpha")
    hook("prompt", s, prompt_id, prompt=prompt)
    return s


for words in (
    "승인",
    "7단계까지 계속 진행해 승인",
    "approved, go on",
    "yes, I approve task alpha",
):
    s = lose(words)
    why = write_refusal(s, PROJ / "src" / "a.py", "p2")
    check(
        f"L-T1 a user prompt that says {words!r} without naming the task on its first line "
        "re-arms nothing, and the first edit after it is refused with the reason and the task",
        (task_of(s), NO_RECORD in why, "task:alpha" in why),
        (None, True, True),
    )
s = lose("승인")
check(
    "L-T2 the refusal covers a file outside the lost plan's scope too, and a new file",
    (
        NO_RECORD in write_refusal(s, PROJ / "other" / "keep.txt", "p2"),
        NO_RECORD in write_refusal(s, PROJ / "src" / "brand_new.py", "p2"),
    ),
    (True, True),
)
check(
    "L-T3 ... but not a file the hook never counts (under the temp folder)",
    write_refusal(s, TMPD / "scratch.txt", "p2"),
    "",
)
check(
    "L-T4 ... and a shell command whose write targets can be read is refused the same way",
    (
        NO_RECORD in shell_refusal(s, "sed -i s/a/b/ src/a.py", "p2"),
        NO_RECORD in shell_refusal(s, "echo x > src/out.txt", "p2"),
        shell_refusal(s, "git status --short", "p2"),
    ),
    (True, True, ""),
)
hook("prompt", s, "p3", prompt="and one more thing")
hook("prompt", s, "p4", prompt="please")
check(
    "L-T5 the refusal lasts over later prompts that name no task",
    NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p4"),
    True,
)
hook("prompt", s, "p5", prompt="task:alpha\ncontinue")
check(
    "L-T6 a prompt whose first line names the task re-arms the approval: the edit goes through",
    (task_of(s), write_refusal(s, PROJ / "src" / "a.py", "p5")),
    ("alpha", ""),
)
hook("prompt", s, "p6", prompt="thanks")
check(
    "L-T7 ... and losing it again refuses again",
    NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p6"),
    True,
)
s = lose("승인")
approve(s, "beta", "p2")
check(
    "L-T8 a new plan approved with ExitPlanMode lifts the refusal (the approval is beta's now)",
    (task_of(s), write_refusal(s, PROJ / "lib" / "b.py", "p2")),
    ("beta", ""),
)
s = start()
hook("prompt", s, "p2", prompt="승인")
check(
    "L-T9 a session that never held an approval is not refused: warn's allowance is as before",
    (
        write_refusal(s, PROJ / "src" / "a.py", "p2"),
        write_refusal(s, PROJ / "other" / "keep.txt", "p2"),
    ),
    ("", ""),
)
hook("prompt", (s := start()), "p2", prompt="approved, go on")
OBSERVED = ROOT / "home" / "authz-observe.jsonl"
seen_before = (
    len(OBSERVED.read_text(encoding="utf-8").splitlines()) if OBSERVED.exists() else 0
)
allowed = []
for n in range(7):
    target = PROJ / "other" / f"n{n}.txt"
    allowed.append(write_refusal(s, target, "p2") == "")
    if allowed[-1]:
        target.write_text("x\n", encoding="utf-8")
        hook(
            "post",
            s,
            "p2",
            tool_name="Write",
            tool_input={"file_path": str(target), "content": "x\n"},
            tool_use_id=uuid.uuid4().hex,
        )
sixth = write_refusal(s, PROJ / "other" / "n5.txt", "p2")
seen_after = (
    len(OBSERVED.read_text(encoding="utf-8").splitlines()) if OBSERVED.exists() else 0
)
check(
    "L-T9b ... in full: five files without a plan go through, the sixth is refused with the "
    "old wording (not the lost-approval one), and each allowed edit left an observation",
    (allowed[:6], NO_RECORD in sixth, "한도" in sixth, seen_after - seen_before >= 5),
    ([True, True, True, True, True, False], False, True, True),
)

# ---- what ends the refusal, and what does not (second design, after the reviews of
# 2026-11-25: the session remembers the last approval it held; no plan file is read)
s = start()
approve(s, "alpha")
edit(s, "src/done.py")
close_alpha()
try:
    stop(s)  # the hook sees the plan closed as done while the approval stands
    hook("prompt", s, "p2", prompt="next thing")
    closed_then = write_refusal(s, PROJ / "src" / "a.py", "p2")
    held_then = state_of(s).get("approval_held")
finally:
    reopen_alpha()
check(
    "L-T10 a plan closed as done while its approval stood ended it in the ordinary way: "
    "nothing is remembered and nothing is refused afterwards",
    (closed_then, held_then),
    ("", None),
)


def after_loss(change) -> tuple[bool, bool]:
    """Whether a write is still refused, right away and at a later prompt, after the session
    did `change` to its plan file once the approval was lost."""
    s = lose("승인")
    original = FILE["alpha"].read_text(encoding="utf-8")
    try:
        change()
        now = NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p2")
        hook("prompt", s, "p3", prompt="go on")
        later = NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p3")
    finally:
        for stray in PLANS.glob("moved-*.md"):
            stray.unlink()
        FILE["alpha"].write_text(original, encoding="utf-8")
    return now, later


check(
    "L-T11 what the session does to its plan file after the loss does not lift the refusal: "
    "closing it as done, parking it as blocked, renaming it, deleting it, breaking its "
    "status line, making it unreadable",
    [
        after_loss(close_alpha),
        after_loss(
            lambda: FILE["alpha"].write_text(
                plan("alpha", "src/**", "blocked"), encoding="utf-8"
            )
        ),
        after_loss(lambda: FILE["alpha"].rename(PLANS / "moved-alpha.md")),
        after_loss(lambda: FILE["alpha"].unlink()),
        after_loss(
            lambda: FILE["alpha"].write_text(
                TEXT["alpha"].replace(
                    "status: in-progress", "status: in-progress\nstatus: done"
                ),
                encoding="utf-8",
            )
        ),
        after_loss(lambda: FILE["alpha"].write_bytes(b"\xff\xfe\x00\xd8")),
    ],
    [(True, True)] * 6,
)
s = lose(LOST, "r1")
check(
    "L-T12 an approval lost to a notification this session did not start is refused the same way",
    NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "r1"),
    True,
)
s = lose("승인")
check(
    "L-T13 CLAUDE_LOST_APPROVAL=notify (the user's switch, read from the hook's own "
    "environment): the loss is only said; any other value refuses",
    (
        write_refusal(
            s, PROJ / "src" / "a.py", "p2", dict(ENV, CLAUDE_LOST_APPROVAL="notify")
        ),
        NO_RECORD
        in write_refusal(
            s, PROJ / "src" / "a.py", "p2", dict(ENV, CLAUDE_LOST_APPROVAL="off")
        ),
        NO_RECORD
        in shell_refusal(s, "CLAUDE_LOST_APPROVAL=notify sed -i s/a/b/ src/a.py", "p2"),
    ),
    ("", True, True),
)
s = start()
approve(s, "alpha")
out = hook("prompt", s, "p2", prompt="please also fix the other thing")
s2 = start()
approve(s2, "alpha")
p = inproc.run(
    HOOKS / "discipline.py",
    ["prompt"],
    json.dumps(
        {"session_id": s2, "cwd": str(PROJ), "prompt_id": "p2", "prompt": "please"}
    ),
    env=dict(ENV, CLAUDE_LOST_APPROVAL="notify"),
)
quiet = json.loads(p.stdout) if p.stdout.strip() else {}
why = write_refusal(lose("승인"), PROJ / "src" / "a.py", "p2")
check(
    "L-T14 the note at the loss says that edits are refused (not so under notify), and "
    "neither it nor the refusal tells the session the name of the switch",
    (
        "거부" in said(out),
        "task:alpha" in said(out),
        "거부" in said(quiet),
        LOST_WORDS in said(quiet),
        "CLAUDE_LOST_APPROVAL" in said(out) + why,
        "revoke task:alpha" in why,
    ),
    (True, True, False, True, False, True),
)

# ---- the ways an approval goes without a prompt saying so (review 2026-11-25, findings 1, 2)
s = start()
approve(s, "alpha")
edit(s, "src/pc.py")
FILE["alpha"].write_text(
    TEXT["alpha"].replace("- src/**", "- src/**\n- other/**"), encoding="utf-8"
)
try:
    stop(s)  # the plan's scope changed after the approval: the Stop drops it
    dropped = task_of(s)
    hook("prompt", s, "p2", prompt="approved")
    after_stop = write_refusal(s, PROJ / "src" / "a.py", "p2")
finally:
    reopen_alpha()
check(
    "L-T15 an approval dropped at a Stop (the plan changed after it) is waited for like one "
    "lost at a prompt: the next prompt's edit is refused",
    (dropped, NO_RECORD in after_stop, "task:alpha" in after_stop),
    (None, True, True),
)
s = start()
approve(s, "alpha")
set_turn(s, float("inf"))
hook(
    "prompt", s, "p2", prompt="task:alpha\ngo on"
)  # the prompt hook fails: approval dropped
set_turn(s, 1)
failed_now = write_refusal(s, PROJ / "src" / "a.py", "p2")
hook("prompt", s, "p3", prompt="approved")
failed_later = write_refusal(s, PROJ / "src" / "a.py", "p3")
check(
    "L-T16 an approval dropped because the prompt hook failed: refused in that prompt and "
    "in the next one",
    (NO_RECORD in failed_now, NO_RECORD in failed_later),
    (True, True),
)

# ---- task ids the prompt parser cannot spell (finding 3 / audit 4)
ODD = {
    "feat/x": "2026-11-25-odd1.md",
    "v1.": "2026-11-25-odd2.md",
    "승인차단": "2026-11-25-odd3.md",
}
odd_results = []
for odd_task, name in ODD.items():
    FILE[odd_task] = PLANS / name
    TEXT[odd_task] = plan(odd_task, "src/**")
    FILE[odd_task].write_text(TEXT[odd_task], encoding="utf-8")
    try:
        s = start()
        approve(s, odd_task)
        held = task_of(s)
        hook("prompt", s, "p2", prompt="승인")
        odd_results.append(
            held != odd_task
            or NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p2")
        )
    finally:
        FILE[odd_task].unlink()
check(
    "L-T17 a task id with a slash, a trailing dot or Korean letters: if it was approved at "
    "all, its loss is waited for like any other",
    odd_results,
    [True, True, True],
)

# ---- other spellings of a path in the repository (audit 2)
import _winapi
import ctypes

s = lose("승인")
link = ROOT / "link-to-proj"
_winapi.CreateJunction(str(PROJ), str(link))
buffer = ctypes.create_unicode_buffer(600)
got = ctypes.windll.kernel32.GetShortPathNameW(
    str(PROJ / "src" / "brand_new_long_name.py"), buffer, 600
)
(PROJ / "src" / "brand_new_long_name.py").write_text("x\n", encoding="utf-8")
got = ctypes.windll.kernel32.GetShortPathNameW(
    str(PROJ / "src" / "brand_new_long_name.py"), buffer, 600
)
short = Path(buffer.value) if got else PROJ / "src" / "brand_new_long_name.py"
check(
    "L-T18 the repository reached another way is the repository: through a junction, by "
    "another letter case, with forward slashes, with `..`, and by an 8.3 short name where "
    "the volume has them",
    (
        NO_RECORD in write_refusal(s, link / "src" / "a.py", "p2"),
        NO_RECORD in write_refusal(s, Path(str(PROJ / "src" / "a.py").upper()), "p2"),
        NO_RECORD in write_refusal(s, Path((PROJ / "src" / "a.py").as_posix()), "p2"),
        NO_RECORD in write_refusal(s, PROJ / "other" / ".." / "src" / "a.py", "p2"),
        NO_RECORD in write_refusal(s, short, "p2"),
    ),
    (True, True, True, True, True),
)
print("     (L-T18 short name tried:", short.name, ")")
elsewhere = ROOT / "another-repo"
(elsewhere / "src").mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=elsewhere, check=True)
check(
    "L-T19 a file in another repository is not this approval's to refuse",
    write_refusal(s, elsewhere / "src" / "x.py", "p2"),
    "",
)

# ---- shell commands (finding 4 / audit 3)
check(
    "L-T20 while an approval is waited for, a shell write whose target cannot be read is "
    "refused too; commands that write nothing the check sees are not",
    (
        NO_RECORD in shell_refusal(s, "F=src/x.py; echo x > $F", "p2"),
        NO_RECORD in shell_refusal(s, 'echo x > "$TARGET"', "p2"),
        shell_refusal(s, "git status --short", "p2"),
        shell_refusal(s, "python -I tests/run_all.py", "p2"),
        shell_refusal(s, "grep -n foo src/a.py | head -5", "p2"),
    ),
    (True, True, "", "", ""),
)
fresh = start()
check(
    "L-T21 ... and without an approval ever held, the unreadable target is only observed, as before",
    shell_refusal(fresh, "F=src/x.py; echo x > $F", "p1"),
    "",
)

# ---- the user's own ways out
s = lose("승인")
hook("prompt", s, "p3", prompt="revoke task:alpha")
check(
    "L-T22 `revoke task:<id>` on the first line lays the task down: nothing is waited for",
    (state_of(s).get("approval_held"), write_refusal(s, PROJ / "src" / "a.py", "p3")),
    (None, ""),
)
s = lose("승인")
hook("prompt", s, "p3", prompt="revoke task:beta")
check(
    "L-T23 ... revoking another task does not",
    NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p3"),
    True,
)
approve(s, "alpha", "p3")  # the plan presented again and approved with ExitPlanMode
check(
    "L-T24 the plan approved again at a later prompt: edits go through, the plan file too",
    (
        write_refusal(s, PROJ / "src" / "a.py", "p3"),
        write_refusal(s, FILE["alpha"], "p3"),
    ),
    ("", ""),
)


# ---- what names a task (user decision 2026-11-25: the first line must start with it)
def rearmed_by(prompt: str) -> object:
    s = start()
    approve(s, "alpha")
    hook("prompt", s, "p2", prompt=prompt)
    return task_of(s)


def slash(args: str) -> str:
    return (
        "<command-message>implement</command-message>\n<command-name>/implement</command-name>\n"
        f"<command-args>{args}</command-args>"
    )


check(
    "L-T25 a first line that starts with the task continues the approval, with words after "
    "it or without, after blank lines too; so do a slash command's arguments (task or plan file)",
    [
        rearmed_by("task:alpha"),
        rearmed_by("task:alpha 이어서 진행해"),
        rearmed_by("task: alpha\ncontinue"),
        rearmed_by("\n\n  task:alpha go on"),
        rearmed_by("TASK_ID=alpha continue"),
        rearmed_by("`task:alpha` continue"),
        rearmed_by(slash("task:alpha")),
        rearmed_by(slash("docs/plans/2026-11-15-alpha.md")),
    ],
    ["alpha"] * 8,
)
check(
    "L-T26 a first line that only mentions the task, or names the plan file, does not; nor "
    "does a second line, pasted text or a quoted reviewer",
    [
        rearmed_by("do NOT go on with task:alpha yet"),
        rearmed_by("the reviewer said: task:alpha approved"),
        rearmed_by("please continue task:alpha"),
        rearmed_by("docs/plans/2026-11-15-alpha.md"),
        rearmed_by("continue 2026-11-15-alpha.md"),
        rearmed_by("go on\ntask:alpha"),
        rearmed_by("<pasted_content>\ntask:alpha\n</pasted_content>"),
        rearmed_by("승인 task:alpha"),
    ],
    [None] * 8,
)
check(
    "L-T27 the first-line commands still work on a line of their own",
    (
        disc_h.REVOKE_CMD.match(disc_h.naming_text("revoke task:alpha")) is not None,
        disc_h.naming_text("recover-state").strip().lower(),
    ),
    (True, "recover-state"),
)

# ---- second review (2026-11-25): the ordinary end of a task must not lock the next prompt,
# whichever hook is the first to see the closed plan
DONE_TEXT = plan("alpha", "src/**", "done")


def close_with_write(s: str) -> None:
    FILE["alpha"].write_text(DONE_TEXT, encoding="utf-8")
    hook(
        "post",
        s,
        "p1",
        tool_name="Write",
        tool_input={"file_path": str(FILE["alpha"]), "content": DONE_TEXT},
        tool_use_id=uuid.uuid4().hex,
    )


def bash_post(s: str, command: str) -> None:
    hook(
        "post",
        s,
        "p1",
        tool_name="Bash",
        tool_input={"command": command},
        tool_response={"stdout": ""},
        tool_use_id=uuid.uuid4().hex,
    )


def after_closing(*steps) -> tuple[str, object]:
    """An approved task closed as done in the turn, then `steps`, then the next prompt: the
    refusal of an unrelated edit there ("" = none), and what is still remembered."""
    s = start()
    approve(s, "alpha")
    edit(s, "src/work.py")
    try:
        for step in steps:
            step(s)
        hook("prompt", s, "p2", prompt="an unrelated small thing")
        return write_refusal(s, PROJ / "other" / "keep.txt", "p2"), state_of(s).get(
            "approval_held"
        )
    finally:
        reopen_alpha()


check(
    "L-T28 a plan closed as done while approved does not lock the next prompt: closed with "
    "Write then a shell command then Stop (close, commit, stop); closed then another edit "
    "then Stop; closed by a shell command then Stop; closed and interrupted before any Stop; "
    "closed and stopped at once",
    [
        after_closing(
            close_with_write, lambda s: bash_post(s, "git status --short"), stop
        ),
        after_closing(close_with_write, lambda s: may_write(s, "src/a.py"), stop),
        after_closing(
            lambda s: FILE["alpha"].write_text(DONE_TEXT, encoding="utf-8"),
            lambda s: bash_post(
                s, "sed -i s/in-progress/done/ docs/plans/2026-11-15-alpha.md"
            ),
            stop,
        ),
        after_closing(close_with_write),
        after_closing(close_with_write, stop),
    ],
    [("", None)] * 5,
)
s = lose("승인")
hook("prompt", s, "p3", prompt="recover-state")
check(
    "L-T29 `recover-state` is for a damaged state, not a way to lay a task down: the held "
    "approval is still waited for after it",
    NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "p3"),
    True,
)
check(
    "L-T30 a first line that merely starts with a slash is not a command: a comment, a path, "
    "a lone slash name nothing; a command word does",
    [
        rearmed_by("//comment task:alpha"),
        rearmed_by("// reviewer said task:alpha approved"),
        rearmed_by("/tmp/log.txt mentions task:alpha, ignore it"),
        rearmed_by("/ 2026-11-15-alpha.md"),
        rearmed_by("/implement task:alpha"),
        rearmed_by("/implement docs/plans/2026-11-15-alpha.md"),
        rearmed_by("/my-plugin:implement task:alpha"),
    ],
    [None, None, None, None, "alpha", "alpha", "alpha"],
)
s = lose("승인")
check(
    "L-T31 device and share spellings of a path in the repository are the repository too",
    (
        NO_RECORD
        in write_refusal(s, Path("\\\\?\\" + str(PROJ / "src" / "a.py")), "p2"),
        NO_RECORD
        in write_refusal(s, Path("\\\\.\\" + str(PROJ / "src" / "a.py")), "p2"),
        NO_RECORD
        in write_refusal(
            s,
            Path("\\\\localhost\\" + str(PROJ / "src" / "a.py").replace(":", "$", 1)),
            "p2",
        ),
    ),
    (True, True, True),
)
s = start()
approve(s, "alpha")
check(
    "L-T32 while the approval stands, a tool call that carries another prompt id or none (a "
    "subagent's) is not taken for a lost approval",
    (
        NO_RECORD in write_refusal(s, PROJ / "src" / "a.py", "sub-agent-prompt"),
        NO_RECORD
        in str(
            pre(
                s,
                None,
                tool_name="Write",
                tool_input={"file_path": str(PROJ / "src" / "a.py"), "content": "x"},
            )
        ),
    ),
    (False, False),
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
