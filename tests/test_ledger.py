"""P1-01: the task ledger records how far an approved task got, and nothing reads it to decide.

    python -I tests/test_ledger.py

Throw-away folders only: an isolated temp folder, approval home and ledger home, and a
repository outside %TEMP% (edits under %TEMP% are not counted by the hook).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = HERE / "_tmp_ledger"
PROJ = Path("C:/dev/_ledger_proj")
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
LHOME = ROOT / "ledger"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(ROOT / "home")
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
sys.path.insert(0, str(HOOKS))
import ledger

logging.disable(logging.CRITICAL)  # refused records are logged; expected here
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:130],
        "" if ok else f"(want {want!r})",
    )


# ---------------------------------------------------------------- pure: one project's state
D = {"C:\\r": "d1"}


def content(
    ci: str = "none", pushed: bool = False, digests: dict | None = None
) -> dict:
    digests = digests or D
    return {
        "digests": digests,
        "pushed": dict.fromkeys(digests, pushed),
        "gate": "g",
        "ci": ci,
    }


def ev(outcome: str, digests: dict | None = None) -> dict:
    return {"outcome": outcome, "at": 5.0, "digests": D if digests is None else digests}


PUSH = {"C:\\r": {"sha": "abc", "at": 10.0, "repo": "o/r"}}


def ci(status: str, sha: str = "abc", at: float = 11.0) -> dict:
    return {"C:\\r": {"sha": sha, "at": at, "status": status}}


def rs(
    c: dict, e: dict | None, pushes: dict | None = None, runs: dict | None = None
) -> str:
    return ledger.root_state(c, e, pushes or {}, runs or {})


check(
    "R-T1 no evidence / passed on other content",
    (rs(content(), None), rs(content(), ev("passed", {"C:\\r": "old"}))),
    ("IMPLEMENTED", "IMPLEMENTED"),
)
check(
    "R-T2 passed on this content, ci none",
    rs(content(), ev("passed")),
    "LOCALLY_VERIFIED",
)
check(
    "R-T3 failed / unknown / partial",
    [rs(content(), ev(o)) for o in ("failed", "unknown", "partial")],
    ["BLOCKED", "UNKNOWN", "IMPLEMENTED"],
)
req = content("required", pushed=True)
check(
    "R-T4 ci required: not pushed / pushed, CI not read / CI of another commit / read before the push",
    [
        rs(content("required"), ev("passed"), PUSH, ci("verified")),
        rs(req, ev("passed"), PUSH, {}),
        rs(req, ev("passed"), PUSH, ci("verified", sha="other")),
        rs(req, ev("passed"), PUSH, ci("verified", at=9.0)),
    ],
    ["LOCALLY_VERIFIED"] * 4,
)
check(
    "R-T5 ci required: verified / failed / unknown / pending",
    [
        rs(req, ev("passed"), PUSH, ci(s))
        for s in ("verified", "failed", "unknown", "pending")
    ],
    ["CI_VERIFIED", "BLOCKED", "LOCALLY_VERIFIED", "LOCALLY_VERIFIED"],
)
two = {"C:\\r": "d1", "C:\\r\\sub": "d2"}
two_c = content("required", pushed=True, digests=two)
two_ev = ev("passed", two)
check(
    "R-T6 two repositories: one verified -> not yet; both -> CI_VERIFIED; one failed -> BLOCKED",
    [
        rs(two_c, two_ev, PUSH, ci("verified")),
        rs(
            two_c,
            two_ev,
            {**PUSH, "C:\\r\\sub": {"sha": "s2", "at": 10.0}},
            {
                **ci("verified"),
                "C:\\r\\sub": {"sha": "s2", "at": 12.0, "status": "verified"},
            },
        ),
        rs(
            two_c,
            two_ev,
            {**PUSH, "C:\\r\\sub": {"sha": "s2", "at": 10.0}},
            {
                **ci("verified"),
                "C:\\r\\sub": {"sha": "s2", "at": 12.0, "status": "failed"},
            },
        ),
    ],
    ["LOCALLY_VERIFIED", "CI_VERIFIED", "BLOCKED"],
)
check(
    "R-T6 a timestamp that is no number: no error, and never counted as checked",
    [
        rs(req, ev("passed"), {"C:\\r": {"sha": "abc", "at": "x"}}, ci("verified")),
        rs(
            req,
            ev("passed"),
            {"C:\\r": {"sha": "abc", "at": "10"}},
            ci("verified", at=5.0),
        ),
        rs(
            req,
            ev("passed"),
            PUSH,
            {"C:\\r": {"sha": "abc", "at": None, "status": "verified"}},
        ),
    ],
    ["LOCALLY_VERIFIED"] * 3,
)
check(
    "R-T7 a task is as far as its least advanced project",
    [
        ledger.task_state(["CI_VERIFIED", "LOCALLY_VERIFIED"]),
        ledger.task_state(["LOCALLY_VERIFIED", "IMPLEMENTED", "CI_VERIFIED"]),
        ledger.task_state(["CI_VERIFIED", "UNKNOWN"]),
        ledger.task_state(["UNKNOWN", "BLOCKED"]),
        ledger.task_state(["CI_VERIFIED"]),
        ledger.task_state([]),
    ],
    ["LOCALLY_VERIFIED", "IMPLEMENTED", "UNKNOWN", "BLOCKED", "CI_VERIFIED", None],
)

# ---------------------------------------------------------------- pure: entries and the chain
SECRET = b"k" * 32


def fields(state: str, task: str = "widget", content_id: str = "c1") -> dict:
    return {
        "at": 100.0,
        "task": task,
        "approval": "ap-1",
        "state": state,
        "content": content_id,
        "roots": 1,
        "session": "s1",
    }


e1 = ledger.make_entry(None, fields("IMPLEMENTED"), SECRET)
e2 = ledger.make_entry(e1, fields("LOCALLY_VERIFIED"), SECRET)
e3 = ledger.make_entry(e2, fields("CI_VERIFIED"), SECRET)
check("R-T8 three entries verify", ledger.verify([e1, e2, e3], SECRET), None)
check(
    "R-T8 ... and the latest entry of each task is found",
    {t: e["state"] for t, e in ledger.latest([e1, e2, e3]).items()},
    {"widget": "CI_VERIFIED"},
)
check(
    "R-T9 a changed value / a removed middle entry / swapped entries / another secret are noticed",
    [
        ledger.verify([e1, {**e2, "state": "CI_VERIFIED"}, e3], SECRET) is not None,
        ledger.verify([e1, e3], SECRET) is not None,
        ledger.verify([e2, e1, e3], SECRET) is not None,
        ledger.verify([e1, e2, e3], b"x" * 32) is not None,
    ],
    [True, True, True, True],
)
check(
    "R-T9 ... and the entry is named",
    (
        "2" in str(ledger.verify([e1, {**e2, "state": "CI_VERIFIED"}, e3], SECRET)),
        "2" in str(ledger.verify([e1, e3], SECRET)),
    ),
    (True, True),
)
odd = ledger.make_entry(
    None,
    {
        **fields("IMPLEMENTED", task="two\nlines  here"),
        "scope": ["src/**"],
        "prompt": "secret text",
        "roots": "many",
        "state": "DONE",
    },
    SECRET,
)
check(
    "R-T10 only allow-listed keys of the right type; text on one line; an unknown state is no entry",
    (
        odd,
        ledger.make_entry(
            None, {**fields("IMPLEMENTED", task="two\nlines  here"), "x": 1}, SECRET
        )["task"],
    ),
    (None, "two lines here"),
)
check(
    "R-T10 ... no other key gets in",
    sorted(
        ledger.make_entry(
            None, {**fields("IMPLEMENTED"), "scope": ["a"], "prompt": "p"}, SECRET
        )
    ),
    sorted(
        [
            "v",
            "at",
            "task",
            "approval",
            "state",
            "content",
            "roots",
            "session",
            "prev",
            "mac",
        ]
    ),
)

# ---------------------------------------------------------------- where the ledger lives
_saved = {
    k: os.environ.pop(k, None) for k in ("CLAUDE_LEDGER_HOME", "CLAUDE_DISCIPLINE_HOME")
}
real_home = ledger.home()
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(ROOT / "home")
follows = ledger.home()
os.environ["CLAUDE_LEDGER_HOME"] = str(ROOT / "explicit")
explicit = ledger.home()
os.environ.pop("CLAUDE_LEDGER_HOME")
check(
    "R-T21 the real ledger by default; next to an isolated approval home; where it is told to be",
    (real_home, follows, explicit),
    (
        Path.home() / ".claude" / "state" / "ledger",
        ROOT / "home" / "ledger",
        ROOT / "explicit",
    ),
)

# ---------------------------------------------------------------- storage
os.environ["CLAUDE_LEDGER_HOME"] = str(LHOME)
REPO_A = "C:\\some\\repo"


def lines(home: Path = LHOME) -> list[dict]:
    files = sorted(home.glob("*.jsonl")) if home.is_dir() else []
    return [
        json.loads(ln)
        for f in files
        for ln in f.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


check(
    "R-T11 the same state on the same content is recorded once",
    (
        ledger.record(REPO_A, fields("IMPLEMENTED"), 1.0),
        ledger.record(REPO_A, fields("IMPLEMENTED"), 2.0),
        [e["state"] for e in lines()],
    ),
    (True, False, ["IMPLEMENTED"]),
)
ledger.record(REPO_A, fields("LOCALLY_VERIFIED"), 3.0)
ledger.record(REPO_A, fields("IMPLEMENTED", content_id="c2"), 4.0)
ledger.record(REPO_A, fields("IMPLEMENTED", task="other"), 5.0)
ledger.record(REPO_A, fields("IMPLEMENTED", content_id="c2"), 6.0)
stored = lines()
check(
    "R-T12 every change of state or content adds one entry, in order, per task",
    [(e["task"], e["state"], e["content"]) for e in stored],
    [
        ("widget", "IMPLEMENTED", "c1"),
        ("widget", "LOCALLY_VERIFIED", "c1"),
        ("widget", "IMPLEMENTED", "c2"),
        ("other", "IMPLEMENTED", "c1"),
    ],
)
check(
    "R-T12 ... the stored chain verifies with the stored secret, and reading reports no problem",
    (ledger.verify(stored, ledger.secret(LHOME, create=False)), ledger.read(REPO_A)[1]),
    (None, None),
)
check(
    "R-T12 another repository has its own file",
    (
        ledger.record("C:\\other\\repo", fields("IMPLEMENTED"), 7.0),
        len(list(LHOME.glob("*.jsonl"))),
    ),
    (True, 2),
)
target = next(
    f
    for f in LHOME.glob("*.jsonl")
    if len(f.read_text(encoding="utf-8").splitlines()) == 4
)
text = target.read_text(encoding="utf-8").splitlines()
target.write_text("\n".join([text[0], text[2], text[3]]) + "\n", encoding="utf-8")
check(
    "R-T9 a removed line in the stored file is reported by read",
    ledger.read(REPO_A)[1] is not None,
    True,
)
target.write_text("\n".join(text) + "\n", encoding="utf-8")

HOLDER = (
    "import sys, time; from pathlib import Path\n"
    f"sys.path.insert(0, {str(HOOKS)!r})\n"
    "import session_store\n"
    "with session_store.locked(Path(sys.argv[1]), 5):\n"
    "    print('held', flush=True)\n"
    "    sys.stdin.read()\n"
)
holder = subprocess.Popen(
    [sys.executable, "-I", "-c", HOLDER, str(target)],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)
holder.stdout.readline()
busy = ledger.record(REPO_A, fields("CI_VERIFIED"), 8.0)
holder.communicate("")
os.environ["CLAUDE_LEDGER_HOME"] = str(ROOT / "afile")
(ROOT / "afile").write_text("x", encoding="utf-8")
check(
    "R-T13 lock held elsewhere / a file where the folder should be: nothing recorded, no error",
    (busy, len(lines()), ledger.record(REPO_A, fields("CI_VERIFIED"), 9.0)),
    (False, 5, False),
)

os.environ["CLAUDE_LEDGER_HOME"] = str(ROOT / "ledger2")
REPO_B = "C:\\repo\\b"
ledger.record(REPO_B, fields("IMPLEMENTED"), 1.0)
bfile = next((ROOT / "ledger2").glob("*.jsonl"))
with open(bfile, "ab") as fh:
    fh.write(b'{"v":1,"task":"cut off in the mid')  # a write that was interrupted
wrote = ledger.record(REPO_B, fields("LOCALLY_VERIFIED"), 2.0)
after, problem = ledger.read(REPO_B)
check(
    "R-T18 an interrupted last line does not swallow the next entry",
    (wrote, [e.get("state") for e in after], "1 line" in str(problem)),
    (True, ["IMPLEMENTED", "LOCALLY_VERIFIED"], True),
)
REPO_C = "C:\\repo\\c"
real_max = ledger.MAX_READ_BYTES
for i in range(40):
    ledger.record(
        REPO_C, fields("IMPLEMENTED" if i % 2 else "LOCALLY_VERIFIED"), float(i)
    )
ledger.MAX_READ_BYTES = 2000  # only the tail is read from here on
tail, tail_problem = ledger.read(REPO_C)
check(
    "R-T18 a long ledger is read from its tail: fewer entries, still verified, still appendable",
    (
        0 < len(tail) < 40,
        tail_problem,
        ledger.record(REPO_C, fields("CI_VERIFIED"), 99.0),
        ledger.read(REPO_C)[1],
    ),
    (True, None, True, None),
)
ledger.MAX_READ_BYTES = real_max
check("R-T18 ... and the whole file still verifies", ledger.read(REPO_C)[1], None)
check(
    "R-T18 the default tail is small enough for a Stop (64 KB)",
    real_max <= 65536,
    True,
)
(ROOT / "ledger3").mkdir()
(ROOT / "ledger3" / ledger.SECRET_NAME).write_text("", encoding="ascii")
os.environ["CLAUDE_LEDGER_HOME"] = str(ROOT / "ledger3")
check(
    "R-T18 an empty secret file is never used and never replaced",
    (
        ledger.record(REPO_B, fields("IMPLEMENTED"), 1.0),
        (ROOT / "ledger3" / ledger.SECRET_NAME).read_text(encoding="ascii"),
    ),
    (False, ""),
)
os.environ["CLAUDE_LEDGER_HOME"] = str(ROOT / "ledger4")
(ROOT / "ledger4").mkdir()
made = ledger.secret(ROOT / "ledger4", create=True)
check(
    "R-T18 a new secret is published complete (no temp file left, same value on re-read)",
    (
        made is not None and len(made) == 32,
        sorted(x.name for x in (ROOT / "ledger4").iterdir()),
        ledger.secret(ROOT / "ledger4", create=True) == made,
    ),
    (True, [ledger.SECRET_NAME], True),
)

# ---------------------------------------------------------------- the cap: never above the Stop
sys.path.insert(0, str(HOOKS))
import discipline as disc

BASE = "c:\\dev\\proj"
cap = getattr(disc, "ledger_capped", lambda state, base: None)
clean_state = {"dirty": {}, "content_tracked_roots": [], "evidence": {}}
check(
    "R-T19 nothing else open: not capped",
    (cap(clean_state, BASE), cap({}, BASE)),
    (False, False),
)
for label, extra in (
    (
        "a repository inside the task whose content is late",
        {"content_late": ["C:\\dev\\proj"]},
    ),
    (
        "... unknown (the marker is a folder inside it)",
        {"content_unknown": ["C:\\dev\\proj\\src"]},
    ),
    ("... unknown (the marker is a folder above it)", {"content_unknown": ["C:\\dev"]}),
    ("... too large", {"content_too_large": ["C:\\dev\\proj\\vendor\\big"]}),
    ("... removed", {"content_removed": ["C:\\dev\\proj\\vendor\\gone"]}),
    (
        "a state reset that is still open",
        {"state_reset": {"at": 5.0, "why": "w", "roots": ["c:\\dev\\proj"]}},
    ),
    ("an approval warning", {"approval_warning": "something"}),
    (
        "an edit git cannot see, newer than the evidence",
        {
            "dirty": {"c:\\dev\\proj": {"at": 9.0, "files": ["x"]}},
            "evidence": {"c:\\dev\\proj": {"at": 5.0, "outcome": "passed"}},
        },
    ),
    (
        "an edit git cannot see whose evidence did not pass",
        {
            "dirty": {"c:\\dev\\proj": {"at": 4.0, "files": ["x"]}},
            "evidence": {"c:\\dev\\proj": {"at": 5.0, "outcome": "partial"}},
        },
    ),
):
    check(f"R-T19 capped by {label}", cap({**clean_state, **extra}, BASE), True)
check(
    "R-T19 not capped by the same things in another repository, or by a covered edit",
    [
        cap({**clean_state, "content_late": ["C:\\dev\\other"]}, BASE),
        cap({**clean_state, "content_removed": ["C:\\dev\\projector"]}, BASE),
        cap(
            {
                **clean_state,
                "dirty": {"c:\\dev\\proj": {"at": 4.0, "files": ["x"]}},
                "evidence": {"c:\\dev\\proj": {"at": 5.0, "outcome": "passed"}},
            },
            BASE,
        ),
        cap(
            {
                **clean_state,
                "dirty": {"c:\\dev\\proj": {"at": 9.0, "files": ["x"]}},
                "content_tracked_roots": ["c:\\dev\\proj"],
                "evidence": {"c:\\dev\\proj": {"at": 5.0, "outcome": "passed"}},
            },
            BASE,
        ),
    ],
    [False, False, False, False],
)

# ---------------------------------------------------------------- through the Stop hook
import approvals

approvals.Store(ROOT / "home", ROOT / "w" / "hwm").admin_activate(
    lambda code: True, "test"
)
(PROJ / "src").mkdir(parents=True)
(PROJ / "docs" / "plans").mkdir(parents=True)
(PROJ / ".gitignore").write_text("local_settings.py\n", encoding="utf-8")
(PROJ / "CLAUDE.md").write_text(
    "## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n", encoding="utf-8"
)
GATE = "uv run pytest -q --cov"
PLAN = (
    "---\nstatus: in-progress\ntask: widget\n---\n\n# Widget\n\n"
    "## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n"
)
PLANFILE = PROJ / "docs" / "plans" / "2026-11-15-widget.md"
PLANFILE.write_text(PLAN, encoding="utf-8")
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
HLEDGER = ROOT / "hook-ledger"
ENV = dict(
    os.environ,
    TMPDIR=str(TMPD),
    TEMP=str(TMPD),
    TMP=str(TMPD),
    CLAUDE_LEDGER_HOME=str(HLEDGER),
)


def hook(
    mode: str, s: str, env: dict | None = None, **payload: object
) -> tuple[int, dict]:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": "p", **payload}
        ),
        env=env or ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else {})


def start(approve: bool, env: dict | None = None) -> str:
    s = uuid.uuid4().hex
    hook("prompt", s, env, prompt="work on the widget")
    if approve:
        hook(
            "post",
            s,
            env,
            tool_name="ExitPlanMode",
            tool_input={},
            tool_response={"plan": PLAN, "filePath": str(PLANFILE)},
        )
    return s


def edit(s: str, name: str, body: str, env: dict | None = None) -> None:
    f = PROJ / "src" / name
    f.write_text(body, encoding="utf-8")
    hook(
        "post",
        s,
        env,
        tool_name="Write",
        tool_input={"file_path": str(f), "content": body},
        tool_use_id=uuid.uuid4().hex,
    )


def gate(s: str) -> None:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": GATE},
        "tool_use_id": uuid.uuid4().hex,
    }
    hook("pre", s, **common)
    hook("post", s, tool_response={}, **common)


s = start(approve=True)
edit(s, "a.py", "x = 1\n")
rc1, out1 = hook("stop", s)
got = lines(HLEDGER)
check(
    "R-T14 an edit under an approved task, then Stop: one IMPLEMENTED entry for the task",
    [
        (
            e.get("task"),
            e.get("state"),
            bool(e.get("approval")),
            bool(e.get("content")),
            e.get("session") == s,
        )
        for e in got
    ],
    [("widget", "IMPLEMENTED", True, True, True)],
)
gate(s)
hook("stop", s)
hook("stop", s)
got = lines(HLEDGER)
check(
    "R-T15 the gate passed, then two Stops: one LOCALLY_VERIFIED entry on the same content",
    ([e.get("state") for e in got], got[0].get("content") == got[-1].get("content")),
    (["IMPLEMENTED", "LOCALLY_VERIFIED"], True),
)
edit(s, "a.py", "x = 2\n")
hook("stop", s)
got = lines(HLEDGER)
check(
    "R-T15 an edit after the gate: back to IMPLEMENTED on new content, the chain still verifies",
    (
        [e.get("state") for e in got],
        got[-1].get("content") != got[0].get("content"),
        ledger.verify(got, ledger.secret(HLEDGER, create=False)),
    ),
    (["IMPLEMENTED", "LOCALLY_VERIFIED", "IMPLEMENTED"], True, None),
)

gate(s)
hook("stop", s)
before = [e.get("state") for e in lines(HLEDGER)]
ignored = PROJ / "src" / "local_settings.py"
ignored.write_text("k = 1\n", encoding="utf-8")
hook(
    "post",
    s,
    tool_name="Write",
    tool_input={"file_path": str(ignored), "content": "k = 1\n"},
    tool_use_id=uuid.uuid4().hex,
)
rc3, out3 = hook("stop", s)
got = lines(HLEDGER)
check(
    "R-T20 an edit git cannot see after the gate: Stop blocks, the ledger steps back with it",
    (
        before[-1],
        out3.get("decision"),
        got[-1].get("state"),
        got[-1].get("content") == got[-2].get("content"),
    ),
    ("LOCALLY_VERIFIED", "block", "IMPLEMENTED", True),
)
other_prompt = hook("stop", s, prompt_id="another-prompt")
check(
    "R-T20 a Stop of a prompt the approval does not serve records nothing",
    len(lines(HLEDGER)),
    len(got),
)

NOLEDGER = dict(ENV, CLAUDE_LEDGER_HOME=str(ROOT / "none-ledger"))
s = start(approve=False, env=NOLEDGER)
edit(s, "b.py", "y = 1\n", NOLEDGER)
hook("stop", s, NOLEDGER)
check(
    "R-T16 an edit without an approved task: nothing is recorded",
    (ROOT / "none-ledger").exists(),
    False,
)

BROKEN = dict(ENV, CLAUDE_LEDGER_HOME=str(ROOT / "afile"))
s = start(approve=True, env=BROKEN)
edit(s, "c.py", "z = 1\n", BROKEN)
rc2, out2 = hook("stop", s, BROKEN)
check(
    "R-T17 a ledger that cannot be written changes nothing about the Stop",
    (
        rc2,
        out2.get("decision"),
        out2.get("reason") == out1.get("reason"),
        bool(out1.get("reason")),
    ),
    (rc1, out1.get("decision"), True, True),
)

# ---- devenv-fix-c: a task whose plan was closed as done keeps its last verification state.
# Only the content the session finished under the approval is recorded under the task's
# name; anything changed afterwards is not.
CLOSED = ROOT / "closed-ledger"
CENV = dict(ENV, CLAUDE_LEDGER_HOME=str(CLOSED))
PLAN2 = PLAN.replace("task: widget", "task: gadget").replace("# Widget", "# Gadget")
PLAN2FILE = PROJ / "docs" / "plans" / "2026-11-16-gadget.md"


def start2(text: str = PLAN2, file: Path = PLAN2FILE, s: str | None = None) -> str:
    s = s or uuid.uuid4().hex
    file.write_text(text, encoding="utf-8")
    hook("prompt", s, CENV, prompt="work on the gadget", prompt_id="p")
    hook(
        "post",
        s,
        CENV,
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": text, "filePath": str(file)},
    )
    return s


def gate2(s: str, prompt_id: str = "p") -> None:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": GATE},
        "tool_use_id": uuid.uuid4().hex,
        "prompt_id": prompt_id,
    }
    hook("pre", s, CENV, **common)
    hook("post", s, CENV, tool_response={}, **common)


def gadget() -> list[tuple[object, object]]:
    return [
        (e.get("state"), e.get("content"))
        for e in lines(CLOSED)
        if e.get("task") == "gadget"
    ]


s = start2()
edit(s, "e.py", "e = 1\n", CENV)
PLAN2FILE.write_text(PLAN2.replace("in-progress", "done"), encoding="utf-8")
gate2(s)
hook("stop", s, CENV)
closed = gadget()
check(
    "R-T21 edit, plan closed as done, gate passed, Stop: the task ends LOCALLY_VERIFIED",
    [st for st, _ in closed][-1:],
    ["LOCALLY_VERIFIED"],
)
hook("prompt", s, CENV, prompt="thanks", prompt_id="p2")
hook("stop", s, CENV, prompt_id="p2")
check(
    "R-T22 a later prompt's Stop on the same content: nothing is added or lowered",
    gadget(),
    closed,
)
edit(s, "e.py", "e = 2\n", CENV)
hook("stop", s, CENV, prompt_id="p2")
check(
    "R-T23 a file changed after the plan was closed: not recorded under the closed task",
    gadget(),
    closed,
)
edit(s, "e.py", "e = 1\n", CENV)
gate2(s, "p2")
hook("stop", s, CENV, prompt_id="p2")
check(
    "R-T24 ... and putting the content back does not bring the task back",
    gadget(),
    closed,
)

s = start2()
edit(s, "f.py", "f = 1\n", CENV)
PLAN2FILE.write_text(
    PLAN2.replace("- src/**\n", "- src/**\n- C:/**\n"), encoding="utf-8"
)
gate2(s)
hook("stop", s, CENV)
check(
    "R-T25 an approval lost because the plan text changed (not closed): nothing recorded",
    gadget(),
    closed,
)

s = start2()
edit(s, "g.py", "g = 1\n", CENV)
PLAN2FILE.write_text(PLAN2.replace("in-progress", "done"), encoding="utf-8")
gate2(s)
hook("stop", s, CENV)
closed2 = gadget()
PLAN3 = PLAN.replace("task: widget", "task: hobbit").replace("# Widget", "# Hobbit")
PLAN3FILE = PROJ / "docs" / "plans" / "2026-11-16-hobbit.md"
PLAN3FILE.write_text(PLAN3, encoding="utf-8")
hook("prompt", s, CENV, prompt="now the hobbit", prompt_id="p3")
hook(
    "post",
    s,
    CENV,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={"plan": PLAN3, "filePath": str(PLAN3FILE)},
    prompt_id="p3",
)
hook("stop", s, CENV, prompt_id="p3")
after = json.loads(
    (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
)
check(
    "R-T26 a new approval after the close: one more closed entry before it, none after, "
    "and the closed task is forgotten",
    (len(closed2) - len(closed), gadget() == closed2, after.get("ledger_closed")),
    (1, True, None),
)

PLAN3FILE.write_text(PLAN3.replace("in-progress", "done"), encoding="utf-8")
s = start2()
edit(s, "h.py", "h = 1\n", CENV)
PLAN2FILE.write_text(PLAN2.replace("in-progress", "done"), encoding="utf-8")
gate2(s)  # the close is seen here
edit(s, "late.py", "late = 1\n", CENV)
gate2(s)
hook("stop", s, CENV)
after = json.loads(
    (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
)
check(
    "R-T27 a file edited in the same turn after the close was seen: the task is not "
    "recorded and not remembered",
    (gadget() == closed2, after.get("ledger_closed")),
    (True, None),
)

check(
    "R-T28 the cap ignores only the notice of this task's own close",
    (
        cap({"approval_warning": "plan done"}, BASE, "plan done"),
        cap({"approval_warning": "store not trusted"}, BASE, "plan done"),
        cap({"approval_warning": "plan done"}, BASE, None),
        cap({"approval_warning": "plan done"}, BASE),
    ),
    (False, True, True, True),
)
DONE_WHY = "x plan_status:done"
held_ok = {"task_id": "gadget", "base": str(PROJ)}
closed_fn = disc.closed_as_done
check(
    "R-T29 closed_as_done: another reason, a store failure over an old done reason, "
    "unknown turn coverage, an approval without task or base",
    (
        closed_fn({"approval_invalid": "revoked", "v2_block": "revoked"}, held_ok),
        closed_fn({"approval_invalid": DONE_WHY, "v2_block": "store broken"}, held_ok),
        closed_fn(
            {"approval_invalid": DONE_WHY, "v2_block": DONE_WHY, "turn_cover_off": 1},
            held_ok,
        ),
        closed_fn({"approval_invalid": DONE_WHY, "v2_block": DONE_WHY}, {"base": "b"}),
        closed_fn({"approval_invalid": DONE_WHY, "v2_block": DONE_WHY}, held_ok),
    ),
    (False, False, False, False, True),
)
for shape, held_state in (
    ("no key (state of an older version)", {"stop_prompt_id": "p"}),
    ("not a dict", {"stop_prompt_id": "p", "ledger_closed": "gadget"}),
    (
        "never pinned at the close",
        {
            "stop_prompt_id": "p",
            "ledger_closed": {"task_id": "gadget", "base": str(PROJ)},
            "content": {},
        },
    ),
):
    before_n = len(lines(CLOSED))
    disc.record_ledger({"session_id": "unit"}, held_state)
    check(
        f"R-T30 record_ledger, closed task {shape}: nothing written",
        (len(lines(CLOSED)) - before_n, held_state.get("ledger_closed")),
        (0, None)
        if isinstance(held_state.get("ledger_closed"), dict | None)
        else (0, "gadget"),
    )

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
