"""P1-02: a review counts for the content the reviewer saw, and only once it has ended.

    python -I tests/test_review_binding.py

A throw-away repository outside %TEMP%, an isolated temp folder and approval home. A change
that needs a review is made by editing hooks/x.py (a risky path).
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
ROOT = HERE / "_tmp_review"
PROJ = Path("C:/dev/_review_proj")
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
os.environ.pop("CLAUDE_REVIEW_GATE", None)
sys.path.insert(0, str(HOOKS))
import approvals

approvals.Store(HOME, ROOT / "w" / "hwm").admin_activate(lambda code: True, "test")
import discipline as disc

ENV = dict(os.environ, TMPDIR=str(TMPD), TEMP=str(TMPD), TMP=str(TMPD))
GATED = dict(ENV, CLAUDE_REVIEW_GATE="1")
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


review_state = getattr(disc, "review_state", lambda state, root: None)

# ---------------------------------------------------------------- pure
R = "c:\\dev\\p"
T = "c:\\dev\\p"
risky = {"files": [R + "\\hooks\\x.py"], "lines": {}, "at": 10.0}
plain = {"files": [R + "\\src\\a.py"], "lines": {}, "at": 10.0}


def st(agents: dict | None = None, entry: dict = risky, **extra: object) -> dict:
    return {
        "dirty": {R: entry},
        "content": {R: {"digests": {T: "d1"}}},
        "content_tracked_roots": [R],
        "agents": agents or {},
        "last_review": 0.0,
        **extra,
    }


def reviewer(digest: str, stopped: float | None = 2.0) -> dict:
    return {"started": 1.0, "stopped": stopped, "runs": 1, "review": {R: {T: digest}}}


check(
    "V-T1 a change no review rule applies to", review_state(st(entry=plain), R), "none"
)
check(
    "V-T2 ended review on this content / on other content / none / still running",
    [
        review_state(st({"code-reviewer:a": reviewer("d1")}), R),
        review_state(st({"code-reviewer:a": reviewer("d0")}), R),
        review_state(st(), R),
        review_state(st({"code-reviewer:a": reviewer("d1", stopped=None)}), R),
    ],
    ["current", "stale", "missing", "running"],
)
untracked = {"content": {}, "content_tracked_roots": []}
check(
    "V-T3 a project git does not track: by time, as before",
    [
        review_state(st(**untracked, last_review=11.0), R),
        review_state(st(**untracked, last_review=9.0), R),
        review_state(st({"code-reviewer:a": reviewer("d1")}, **untracked), R),
    ],
    ["current", "missing", "missing"],
)
check(
    "V-T4 several reviewers: one ended on this content is enough; an agent without a review is none",
    [
        review_state(
            st({"code-reviewer:a": reviewer("d0"), "code-reviewer:b": reviewer("d1")}),
            R,
        ),
        review_state(st({"general-purpose:g": {"started": 1.0, "stopped": 2.0}}), R),
        review_state(st({"code-reviewer:a": reviewer("d0"), "x:y": "junk"}), R),
        review_state(st(last_review=99.0), R),
    ],
    ["current", "missing", "stale", "missing"],
)

# ---- review: the gate for content-judged projects leaves the others to the time rule
os.environ["CLAUDE_REVIEW_GATE"] = "1"
decide = getattr(disc, "_review_decision", lambda state, root: "absent")
check(
    "V-T12 gate on: a content-judged project without a review is held; one judged by time is not held here",
    (
        decide(st(), R) is not None,
        decide(st(content_tracked_roots=[]), R),
        decide(st({"code-reviewer:a": reviewer("d1")}), R),
    ),
    (True, None, None),
)
os.environ.pop("CLAUDE_REVIEW_GATE")

many = {f"c:\\dev\\p{i:02}": {"files": ["f.py"], "at": float(i)} for i in range(14)}
real_digests = disc.evidence_digests
disc.evidence_digests = lambda state, root, cwd: {root: "d"}
snap = getattr(disc, "review_snapshot", lambda state, cwd: {})(
    {"dirty": many}, "c:\\dev"
)
check(
    "V-T13 more edited projects than a snapshot holds: the most recently edited ones are kept",
    sorted(snap),
    sorted(f"c:\\dev\\p{i:02}" for i in range(4, 14)),
)


def failing(state: dict, root: str, cwd: str) -> dict:
    raise ValueError("a project file that cannot be read")


disc.evidence_digests = failing
import contextlib
import io
import logging

logging.disable(logging.CRITICAL)
agent_state: dict = {"dirty": {R: risky}}
with contextlib.redirect_stdout(io.StringIO()):
    try:
        disc.on_agent_start(
            {"agent_id": "r9", "agent_type": "code-reviewer"}, agent_state
        )
        raised = None
    except ValueError as e:
        raised = str(e)
logging.disable(logging.NOTSET)
disc.evidence_digests = real_digests
check(
    "V-T14 a snapshot that cannot be taken: the reviewer still starts, with nothing recorded",
    (
        raised,
        (agent_state.get("agents") or {}).get("code-reviewer:r9", {}).get("review"),
    ),
    (None, {}),
)

# ---------------------------------------------------------------- through the hooks
(PROJ / "hooks").mkdir(parents=True)
(PROJ / "docs" / "plans").mkdir(parents=True)
(PROJ / "hooks" / "x.py").write_text("x = 0\n", encoding="utf-8")
(PROJ / "CLAUDE.md").write_text(
    "## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n", encoding="utf-8"
)
GATE = "uv run pytest -q --cov"
PLAN = (
    "---\nstatus: in-progress\ntask: widget\n---\n\n# Widget\n\n"
    "## 변경 범위\n- hooks/**\n\n## 단계\n- [ ] 1. a\n"
)
PLANFILE = PROJ / "docs" / "plans" / "2026-11-15-widget.md"
PLANFILE.write_text(PLAN, encoding="utf-8")
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
OBSERVED = HOME / "review-observe.jsonl"


def hook(mode: str, s: str, env: dict = ENV, **payload: object) -> dict:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": "p", **payload}
        ),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-300:])
    return json.loads(p.stdout) if p.stdout.strip() else {}


def state_of(s: str) -> dict:
    return json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )


def start(env: dict = ENV) -> str:
    s = uuid.uuid4().hex
    hook("prompt", s, env, prompt="work")
    return s


def edit(s: str, env: dict = ENV) -> None:
    f = PROJ / "hooks" / "x.py"
    body = f"x = {uuid.uuid4().hex!r}\n"
    f.write_text(body, encoding="utf-8")
    hook(
        "post",
        s,
        env,
        tool_name="Write",
        tool_input={"file_path": str(f), "content": body},
        tool_use_id=uuid.uuid4().hex,
    )


def gate(s: str, env: dict = ENV) -> None:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": GATE},
        "tool_use_id": uuid.uuid4().hex,
    }
    hook("pre", s, env, **common)
    hook("post", s, env, tool_response={}, **common)


def agent(
    s: str, event: str, agent_id: str, kind: str = "code-reviewer", env: dict = ENV
) -> None:
    hook(f"agent-{event}", s, env, agent_id=agent_id, agent_type=kind)


def verdict(s: str) -> object:
    state = state_of(s)
    roots = list(state.get("dirty", {}))
    return review_state(state, roots[0]) if len(roots) == 1 else roots


def rows() -> list[dict]:
    if not OBSERVED.is_file():
        return []
    return [
        json.loads(ln) for ln in OBSERVED.read_text(encoding="utf-8").splitlines() if ln
    ]


s = start()
edit(s)
agent(s, "start", "r1")
agent(s, "start", "g1", kind="general-purpose")
agent(s, "stop", "g1", kind="general-purpose")
running = None
hook("stop", s)
running = verdict(s)
agent(s, "stop", "r1")
hook("stop", s)
agents = state_of(s).get("agents", {})
check(
    "V-T5 a reviewer's start records what it saw; it counts once it has ended",
    (
        bool((agents.get("code-reviewer:r1") or {}).get("review")),
        running,
        verdict(s),
    ),
    (True, "running", "current"),
)
check(
    "V-T7 another kind of agent records nothing to review",
    "review" in (agents.get("general-purpose:g1") or {"review": 1}),
    False,
)
edit(s)
hook("stop", s)
check("V-T6 an edit after the review: the review is stale", verdict(s), "stale")

s = start()
edit(s)
gate(s)
out = hook("stop", s)
first = rows()
hook("stop", s)
check(
    "V-T8 gate off: a verified change without a review is not blocked, and is noted once",
    (out.get("decision"), [r.get("state") for r in first], len(rows())),
    (None, ["missing"], 1),
)
check(
    "V-T8 ... the note holds no path",
    all(
        str(PROJ).lower() not in json.dumps(r).lower() and "x.py" not in json.dumps(r)
        for r in first
    ),
    True,
)

s = start(GATED)
edit(s, GATED)
gate(s, GATED)
no_review = hook("stop", s, GATED)
agent(s, "start", "r2", env=GATED)
while_running = hook("stop", s, GATED)
agent(s, "stop", "r2", env=GATED)
reviewed = hook("stop", s, GATED)
edit(s, GATED)
gate(s, GATED)
after_edit = hook("stop", s, GATED)
check(
    "V-T9 gate on: no review -> blocked; reviewer running -> blocked; ended -> passes; edited again -> blocked",
    [
        (o.get("decision"), "code-reviewer" in str(o.get("reason")))
        for o in (no_review, while_running, reviewed, after_edit)
    ],
    [("block", True), ("block", True), (None, False), ("block", True)],
)

HOME2 = ROOT / "home2"
(HOME2 / "review-observe.jsonl").mkdir(parents=True)
BROKEN = dict(ENV, CLAUDE_DISCIPLINE_HOME=str(HOME2))
s = start(BROKEN)
edit(s, BROKEN)
gate(s, BROKEN)
check(
    "V-T10 a note that cannot be written changes nothing about the Stop",
    hook("stop", s, BROKEN).get("decision"),
    None,
)

s = start()
hook(
    "post",
    s,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={"plan": PLAN, "filePath": str(PLANFILE)},
)
edit(s)
gate(s)
hook("stop", s)
agent(s, "start", "r3")
agent(s, "stop", "r3")
hook("stop", s)
ledger_lines = [
    json.loads(ln)
    for f in sorted((HOME / "ledger").glob("*.jsonl"))
    for ln in f.read_text(encoding="utf-8").splitlines()
    if ln.strip()
]
check(
    "V-T11 the ledger notes the review with the state, and a new line when it changes",
    [(e.get("state"), e.get("review")) for e in ledger_lines],
    [("LOCALLY_VERIFIED", "missing"), ("LOCALLY_VERIFIED", "current")],
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
