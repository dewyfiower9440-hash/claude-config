"""P0-13: old session state files are removed, everything else is left (python -I).

Runs in throw-away folders only; the real state folder is never swept from here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = Path(tempfile.mkdtemp(prefix="stategc-"))
sys.stdout.reconfigure(errors="backslashreplace")
sys.path.insert(0, str(HOOKS))
import state_gc

fails = 0
DAY = 86400.0
NOW = 2_000_000_000.0
TTL = 14 * DAY
OLD, RECENT = NOW - 15 * DAY, NOW - 1 * DAY


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:110],
        "" if ok else f"(want {want!r})",
    )


def stale(entries: list[tuple[str, float]]) -> list[str]:
    return state_gc.stale_entries(entries, NOW, TTL)


check(
    "G-T1 an old state file goes with its lock",
    stale([("a1.json", OLD), ("a1.lock", RECENT)]),
    ["a1.json", "a1.lock"],
)
check(
    "G-T2 a recent state file keeps its old lock",
    stale([("b2.json", RECENT), ("b2.lock", OLD)]),
    [],
)
check(
    "G-T3 a lock without a state file: removed when old, kept when recent",
    stale([("c3.lock", OLD), ("d4.lock", RECENT)]),
    ["c3.lock"],
)
check(
    "G-T4 recovery copies and other names are never candidates",
    stale(
        [
            ("e5.json.corrupt-20261101T010101-a601d4", OLD),
            ("e5.json.recovered-20261101T010101-9e2743", OLD),
            ("e5.json.123.tmp", OLD),
            ("e5.txt", OLD),
            (".hidden.json", OLD),
            ("with space.json", OLD),
            ("e5.JSON", OLD),
            ("..json", OLD),
            ("e5.json", RECENT),
        ]
    ),
    [],
)
check(
    "G-T5 exactly the TTL stays, one second more goes",
    stale([("f6.json", NOW - TTL), ("g7.json", NOW - TTL - 1)]),
    ["g7.json"],
)


def make(folder: Path, name: str, mtime: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text("{}", encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


d = ROOT / "d1"
for name, mtime in (
    ("old1.json", OLD),
    ("old1.lock", OLD),
    ("new1.json", RECENT),
    ("new1.lock", OLD),
    ("old2.json.recovered-20261101T010101-9e2743", OLD),
    ("orphan.lock", OLD),
):
    make(d, name, mtime)
make(d / "sub", "old3.json", OLD)
check("G-T6 sweep removes the stale files", state_gc.sweep(d, NOW, TTL), (3, 0))
check(
    "G-T6 ... and leaves the rest, the folder and what is inside it",
    sorted(p.relative_to(d).as_posix() for p in d.rglob("*")),
    [
        "new1.json",
        "new1.lock",
        "old2.json.recovered-20261101T010101-9e2743",
        "sub",
        "sub/old3.json",
    ],
)

d = ROOT / "d2"
for i in range(10):
    make(d, f"s{i:02}.json", OLD)
first = state_gc.sweep(d, NOW, TTL, limit=4)
second = state_gc.sweep(d, NOW, TTL, limit=100)
check(
    "G-T7 the limit bounds one run and the next run continues",
    (first, second, list(d.iterdir())),
    ((4, 0), (6, 0), []),
)
check(
    "G-T7 no time left: nothing is removed",
    (make(d, "late.json", OLD).name, state_gc.sweep(d, NOW, TTL, budget_s=-1.0)),
    ("late.json", (0, 0)),
)

d = ROOT / "d3"
busy = make(d, "busy.json", OLD)
make(d, "free.json", OLD)
if os.name == "nt":
    with open(busy, "r+b"):  # an open file cannot be removed on Windows
        result = state_gc.sweep(d, NOW, TTL)
    check(
        "G-T8 a file in use is counted as failed and the sweep goes on",
        (result, busy.exists(), (d / "free.json").exists()),
        ((1, 1), True, False),
    )
else:
    print("NOTICE: G-T8 needs Windows file locking; skipped on this platform")

afile = ROOT / "afile"
afile.write_text("x", encoding="utf-8")
check(
    "G-T9 a missing folder and a file in its place: nothing, no error",
    (state_gc.sweep(ROOT / "none", NOW, TTL), state_gc.sweep(afile, NOW, TTL)),
    ((0, 0), (0, 0)),
)

for raw, want in (
    (None, 35),
    ("", 35),
    ("7", 7),
    (" 30 ", 30),
    ("0", 35),
    ("-3", 35),
    ("abc", 35),
    ("1.5", 35),
    ("99999", 365),
    ("²", 35),
):
    check(f"G-T10 TTL value {raw!r}", state_gc.ttl_days(raw), want)

# ---- which folder: only an absolute path whose last part is claude-discipline
saved = {k: os.environ.get(k) for k in ("CLAUDE_STATE_GC_DIR", "TEMP")}
good = ROOT / "d5" / "claude-discipline"
for raw, want in (
    ("rel", None),
    ("claude-discipline", None),
    (str(ROOT / "d5"), None),
    (str(ROOT / "d5" / "claude-discipline" / "sub"), None),
    (str(good), good),
):
    os.environ["CLAUDE_STATE_GC_DIR"] = raw
    check(f"G-T13 folder setting {raw[-30:]!r}", state_gc.default_dir(), want)
os.environ["CLAUDE_STATE_GC_DIR"] = "rel"
check(
    "G-T13 a refused folder setting sweeps nothing", state_gc.sweep_default(NOW), (0, 0)
)
for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

d = ROOT / "d6"
make(d, "pair.lock", OLD)
make(d, "pair.json", RECENT)
ticks = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])
check(
    "G-T14 time runs out while listing: nothing is removed (half a list could split a pair)",
    (
        state_gc.sweep(d, NOW, TTL, budget_s=1.0, clock=lambda: next(ticks)),
        sorted(x.name for x in d.iterdir()),
    ),
    ((0, 0), ["pair.json", "pair.lock"]),
)

# ---- through the SessionEnd hook
d = ROOT / "d4" / "claude-discipline"
real_now = time.time()
make(d, "gone.json", real_now - 40 * DAY)
make(d, "gone.lock", real_now - 40 * DAY)
make(d, "mid.json", real_now - 20 * DAY)
make(d, "kept.json", real_now - 2 * DAY)
make(d, "gone.json.corrupt-20261101T010101-a601d4", real_now - 40 * DAY)
wrong = ROOT / "project"
make(wrong, "package.json", real_now - 400 * DAY)
make(wrong, "uv.lock", real_now - 400 * DAY)
plain = ROOT / "plain"
plain.mkdir()
env = dict(
    os.environ, CLAUDE_STATE_GC_DIR=str(d), CLAUDE_HANDOFF_HOME=str(ROOT / "store")
)
env.pop("CLAUDE_STATE_TTL_DAYS", None)
p = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "handoff.py"), "end"],
    input=json.dumps(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "g",
            "reason": "other",
            "cwd": str(plain),
        }
    ),
    capture_output=True,
    text=True,
    encoding="utf-8",
    env=env,
    check=False,
)
check("G-T11 SessionEnd: exit 0, nothing on stdout", (p.returncode, p.stdout), (0, ""))
check(
    "G-T11/12 ... the sweep ran (even outside a repository): old pair gone, the rest kept",
    sorted(x.name for x in d.iterdir()),
    ["gone.json.corrupt-20261101T010101-a601d4", "kept.json", "mid.json"],
)
env["CLAUDE_STATE_TTL_DAYS"] = "1"
subprocess.run(
    [sys.executable, "-I", str(HOOKS / "handoff.py"), "end"],
    input="not json",
    capture_output=True,
    text=True,
    env=env,
    check=False,
)
check(
    "G-T11 a bad payload still sweeps, with the configured TTL",
    sorted(x.name for x in d.iterdir()),
    ["gone.json.corrupt-20261101T010101-a601d4"],
)

env["CLAUDE_STATE_GC_DIR"] = str(wrong)
env["CLAUDE_STATE_TTL_DAYS"] = "1"
p = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "handoff.py"), "end"],
    input="{}",
    capture_output=True,
    text=True,
    env=env,
    cwd=wrong,
    check=False,
)
check(
    "G-T13 SessionEnd with a folder of another name: its files stay",
    (p.returncode, sorted(x.name for x in wrong.iterdir())),
    (0, ["package.json", "uv.lock"]),
)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
