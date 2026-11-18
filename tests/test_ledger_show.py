"""P2-06: `ledger.py show` prints how far a task got, for a person or `/implement` to read.
It only reads; what it prints is a record, never a reason to skip a gate.

    python -I tests/test_ledger_show.py

An isolated ledger home and approval home, a throw-away repository outside %TEMP%.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_ledger_show"
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_ledgershow_proj"
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
LHOME = ROOT / "ledger"
AHOME = ROOT / "home"
AHOME.mkdir(parents=True)
os.environ["CLAUDE_LEDGER_HOME"] = str(LHOME)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(AHOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
sys.path.insert(0, str(HOOKS))
import ledger

logging.disable(logging.CRITICAL)
fails = 0
DAY = 86400.0
NOW = 1_800_000_000.0
REMINDER = "승인이나 검증의 근거가 아닙니다"
WARNING = "온전하지 않습니다"


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


def show(entries: list[Any], problem: str | None, task: str | None) -> list[str]:
    fn = getattr(ledger, "show_lines", None)
    return list(fn(entries, problem, task, NOW)) if fn else []


def entry(task: str, state: str, days_ago: float, **more: object) -> dict[str, Any]:
    return {
        "task": task,
        "state": state,
        "at": NOW - days_ago * DAY,
        "content": "c1",
        **more,
    }


# ---------------------------------------------------------------- pure
E = [
    entry("widget", "IMPLEMENTED", 5),
    entry("other", "CI_VERIFIED", 4, review="current"),
    entry("widget", "LOCALLY_VERIFIED", 2.5, review="stale", content="abc123"),
]
one = show(E, None, "widget")
check(
    "S1 one task: its last entry (state, review, how long ago, content), then the reminder",
    (
        len(one),
        all(
            w in (one or [""])[0]
            for w in ("widget", "LOCALLY_VERIFIED", "stale", "2일 전", "abc123")
        ),
        "IMPLEMENTED" in (one or [""])[0],
        REMINDER in (one or [""])[-1],
    ),
    (2, True, False, True),
)
none = show(E, None, "missing")
check(
    "S2 a task without an entry: said so, the reminder still comes",
    ("기록 없음" in (none or [""])[0], REMINDER in (none or [""])[-1], len(none)),
    (True, True, 2),
)
many = [entry(f"t{i:02d}", "IMPLEMENTED", 30 - i) for i in range(14)]
listed = show(many, None, None)
check(
    "S3 no task named: the last entry of each task, newest first, ten at most",
    (
        len(listed),
        (listed or [""])[0].split(":")[0],
        (listed[9] if len(listed) > 9 else "").split(":")[0],
        REMINDER in (listed or [""])[-1],
    ),
    (11, "t13", "t04", True),
)
warned = show(E, "entry 2: content does not match its signature", "widget")
check(
    "S4 a ledger that is not intact: a warning first, with what is wrong",
    (
        WARNING in (warned or [""])[0],
        "entry 2" in (warned or [""])[0],
        len(warned),
        REMINDER in (warned or [""])[-1],
    ),
    (True, True, 3, True),
)
odd = show(
    [
        {"task": "widget", "state": "IMPLEMENTED"},
        {"task": 7, "state": "x", "at": NOW},
        "not an entry",
        {"task": "late", "state": "BLOCKED", "at": "soon", "review": 3},
    ],
    None,
    None,
)
check(
    "S5 entries without a time, with odd values, things that are no entries: no error",
    (
        len(odd),
        any("widget" in line for line in odd),
        any("late" in line for line in odd),
    ),
    (3, True, True),
)
check(
    "S5 an empty ledger",
    (len(show([], None, None)), "기록 없음" in (show([], None, None) or [""])[0]),
    (2, True),
)

HUGE = 10**400
wild = show(
    [
        {"task": "huge", "state": "CI_VERIFIED", "at": HUGE},
        {"task": "nan", "state": "CI_VERIFIED", "at": float("nan")},
        {"task": "inf", "state": "CI_VERIFIED", "at": float("inf")},
        {"task": "neg", "state": "CI_VERIFIED", "at": -5},
        {"task": "zero", "state": "CI_VERIFIED", "at": 0},
        {"task": "future", "state": "CI_VERIFIED", "at": NOW + 10 * DAY},
        {
            "task": "esc\x1b[1A\x1b[2K\u202e\x00",
            "state": "S\nT",
            "at": NOW,
            "content": "x" * 5000,
        },
    ],
    None,
    None,
)
check(
    "S5 times that are huge, not numbers, negative or in the future; control characters: no error",
    (
        len(wild),
        wild[0].split(":")[0] if wild else None,
        any("미래" in line for line in wild if line.startswith("future")),
        any(ch in "".join(wild) for ch in "\x1b\x00\u202e\n"),
        max((len(line) for line in wild), default=0) < 600,
    ),
    (8, "future", True, False, True),
)
check(
    "S5 a task's last entry is the last in the file, whatever its time says",
    show(
        [entry("widget", "CI_VERIFIED", 1), entry("widget", "BLOCKED", 9)],
        None,
        "widget",
    )[0].split(",")[0],
    "widget: BLOCKED (리뷰 ?)",
)
check(
    "S5 a task whose name starts like the 'no entry' line is still an entry",
    getattr(ledger, "pick", lambda e, t: None)(
        [entry("기록 없음", "IMPLEMENTED", 1)], None
    )
    is not None
    and len(
        getattr(ledger, "pick", lambda e, t: [])(
            [entry("기록 없음", "IMPLEMENTED", 1)], None
        )
    ),
    1,
)

# ---------------------------------------------------------------- the command
PROJ.mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
# outside every repository (ROOT lies inside this one)
PLAIN = Path(tempfile.mkdtemp(prefix="ledger-show-"))


def fields(
    task: str, state: str, content: str = "c1", review: str = "none"
) -> dict[str, Any]:
    return {
        "task": task,
        "approval": "a1",
        "state": state,
        "content": content,
        "roots": 1,
        "session": "s1",
        "review": review,
    }


def run(*args: str, cwd: Path = PROJ) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "ledger.py"), *args],
        cwd=cwd,
        env=dict(os.environ),
        capture_output=True,
        check=False,
    )
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(folder.rglob("*")) if folder.exists() else []:
        h.update(str(f.relative_to(folder)).encode())
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()


rc, out = run("show")
check(
    "S6 no ledger at all: nothing to show (exit code 1), and none is created",
    (rc, "기록 없음" in out, LHOME.exists()),
    (1, True, False),
)
ledger.record(str(PROJ), fields("widget", "IMPLEMENTED"), NOW - 3 * DAY)
ledger.record(
    str(PROJ), fields("widget", "LOCALLY_VERIFIED", review="current"), NOW - 2 * DAY
)
ledger.record(str(PROJ), fields("gadget", "BLOCKED", content="c9"), NOW - DAY)
before = digest(LHOME)
rc, out = run("show", "--task", "widget")
check(
    "S7 a task of the repository the command runs in: its last state, exit code 0",
    (
        rc,
        "widget: LOCALLY_VERIFIED" in out,
        "current" in out,
        "gadget" in out,
        REMINDER in out,
    ),
    (0, True, True, False, True),
)
rc, out = run("show", "--root", str(PROJ), cwd=PLAIN)
check(
    "S7 --root, no task: every task of that repository",
    (rc, "widget: LOCALLY_VERIFIED" in out, "gadget: BLOCKED" in out),
    (0, True, True),
)
check(
    "S8 a task without an entry: exit code 1; no repository, no such folder, no command: 2",
    [
        run("show", "--task", "missing")[0],
        run("show", cwd=PLAIN)[0],
        run("show", "--root", str(ROOT / "nowhere"))[0],
        run()[0],
        run("erase")[0],
    ],
    [1, 2, 2, 2, 2],
)
check(
    "S9 reading changed nothing: the ledger folder is the same, the approval home is empty",
    (digest(LHOME) == before, list(AHOME.iterdir())),
    (True, []),
)
target = next(LHOME.glob("*.jsonl"))
text = target.read_text(encoding="utf-8")
target.write_text(
    text.replace("LOCALLY_VERIFIED", "CI_VERIFIED"), encoding="utf-8", newline="\n"
)
rc, out = run("show", "--task", "widget")
check(
    "S10 an entry was edited: shown with the warning first, exit code 3",
    (rc, out.lstrip().startswith("경고"), WARNING in out, "widget: CI_VERIFIED" in out),
    (3, True, True, True),
)

target.write_text(text + "not an entry\n", encoding="utf-8", newline="\n")
rc, out = run("show", "--task", "widget")
check(
    "S10 a line that is no entry: the warning, exit code 3",
    (rc, WARNING in out, "widget: LOCALLY_VERIFIED" in out),
    (3, True, True),
)
target.write_text(
    text + '{"task": "forged", "state": "CI_VERIFIED", "at": ' + str(HUGE) + "}\n",
    encoding="utf-8",
    newline="\n",
)
rc, out = run("show")
check(
    "S10 a forged entry with a huge time: no traceback, the warning, exit code 3",
    (rc, "Traceback" in out, WARNING in out),
    (3, False, True),
)
target.write_text(text, encoding="utf-8", newline="\n")
(LHOME / "mac-secret").unlink()
rc, out = run("show", "--task", "widget")
rc_none, _ = run("show", "--task", "missing")
check(
    "S11 the secret is gone: the warning, exit code 3 (also for a task without an entry), no new secret",
    (rc, WARNING in out, rc_none, (LHOME / "mac-secret").exists()),
    (3, True, 3, False),
)

for p in (ROOT, PROJ, PLAIN):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
