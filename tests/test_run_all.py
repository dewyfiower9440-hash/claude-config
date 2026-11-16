"""run_all.py runner: modules may run in parallel, but results, failure handling and output
stay as with the sequential runner. Fake test scripts in an isolated temp folder.

    python -I tests/test_run_all.py
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_all

TMP = Path(tempfile.mkdtemp(prefix="runall-"))
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:120],
        "" if ok else f"(want {want!r})",
    )


# A fake test waits (bounded) for a sibling's marker: it can only see it if both run at once.
WAITER = """
import sys, time
from pathlib import Path
d = Path(sys.argv[0]).parent
(d / "{me}.started").touch()
deadline = time.monotonic() + {wait}
while time.monotonic() < deadline and not (d / "{other}.started").exists():
    time.sleep(0.02)
(d / "{me}.result").write_text("overlap" if (d / "{other}.started").exists() else "alone")
print("FAILURES: 0")
"""


def script(name: str, body: str) -> None:
    (TMP / f"{name}.py").write_text(body, encoding="utf-8")


def reset() -> None:
    for f in TMP.glob("*.started"):
        f.unlink()
    for f in TMP.glob("*.result"):
        f.unlink()


def run(names: list[str], workers: int) -> tuple[bool, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ok = run_all.run_tests(names, workers, TMP)
    return ok, out.getvalue()


script("wa", WAITER.format(me="wa", other="wb", wait=20))
script("wb", WAITER.format(me="wb", other="wa", wait=20))
script("ok_quiet", 'print("detail line")\nprint("FAILURES: 0")\n')
script("ok_show", 'print("measurement 42")\nprint("FAILURES: 0")\n')
script("bad", 'print("broken thing")\nprint("FAILURES: 1")\nraise SystemExit(1)\n')

ok, out = run(["wa", "wb"], workers=2)
check(
    "(c) two workers: the modules ran at the same time",
    ((TMP / "wa.result").read_text(), (TMP / "wb.result").read_text()),
    ("overlap", "overlap"),
)
check("(c) ... and both passed", ok, True)

reset()
script(
    "wa", WAITER.format(me="wa", other="wb", wait=1)
)  # bounded: alone in sequential mode
script("wb", WAITER.format(me="wb", other="wa", wait=1))
ok, out = run(["wa", "wb"], workers=1)
check(
    "(d) one worker: the modules ran one after another",
    ((TMP / "wa.result").read_text(), (TMP / "wb.result").read_text())
    in {("alone", "overlap"), ("overlap", "alone")},
    True,
)

reset()
ok, out = run(["ok_quiet", "bad", "ok_show"], workers=3)
lines = [ln for ln in out.splitlines() if re.match(r"^(PASS|FAIL) \w+: ", ln)]
check(
    "(a) results are printed in the declared order",
    [ln.split()[1].rstrip(":") for ln in lines],
    ["ok_quiet", "bad", "ok_show"],
)
check("(b) one failing module fails the run", ok, False)
check(
    "(b) the failing module is marked FAIL with its last line",
    lines[1].startswith("FAIL bad: FAILURES: 1"),
    True,
)
check("(e) a failure prints its output tail", "broken thing" in out, True)
script(
    "bad_long",
    'print("FAIL early check -> 1 (want 2)")\n'
    'for n in range(200):\n    print("OK   filler", n, "x" * 40)\n'
    'print("FAILURES: 1")\nraise SystemExit(1)\n',
)
ok, out = run(["bad_long"], workers=1)
check(
    "(e) a failing check far above the output tail is still printed",
    ("FAIL early check -> 1 (want 2)" in out, "OK   filler 0 " in out),
    (True, False),
)
check("(e) a passing module's output stays hidden", "detail line" in out, False)
check(
    "each result line carries the module's duration",
    all(ln.rstrip().endswith("s)") for ln in lines),
    True,
)

run_all.ALWAYS_SHOW = {"ok_show"}
ok, out = run(["ok_show"], workers=2)
check(
    "(e) ALWAYS_SHOW output is printed even when it passes",
    ("measurement 42" in out, ok),
    (True, True),
)

# L-T10 (P0-15): the modules get a temp folder of their own, removed after the run
script(
    "temp_probe",
    "import os, sys, tempfile\n"
    "from pathlib import Path\n"
    "seen = {k: os.environ.get(k) for k in ('TEMP', 'TMP', 'TMPDIR')}\n"
    "seen['tempfile'] = tempfile.gettempdir()\n"
    "state = Path(tempfile.gettempdir()) / 'claude-discipline'\n"
    "state.mkdir()\n"
    "(state / 'left-behind.json').write_text('{}')\n"
    "(state / 'read-only.bin').write_text('x')\n"
    "os.chmod(state / 'read-only.bin', 0o444)  # like git's object files\n"
    "(Path(sys.argv[0]).parent / 'temp.result').write_text(repr(sorted(set(seen.values()))))\n"
    "print('FAILURES: 0')\n",
)
outer = tempfile.gettempdir()
ok, out = run(["temp_probe"], workers=1)
seen_temp = ast.literal_eval((TMP / "temp.result").read_text())
check(
    "L-T10 a module sees one temp folder, made for this run under the usual one",
    (
        ok,
        len(seen_temp),
        Path(seen_temp[0]).name.startswith("claude-gate-"),
        os.path.normcase(os.path.realpath(Path(seen_temp[0]).parent))
        == os.path.normcase(os.path.realpath(outer)),
    ),
    (True, 1, True, True),
)
check(
    "L-T10 ... and it is gone afterwards, with what the module left (read-only too)",
    (Path(seen_temp[0]).exists(), tempfile.gettempdir() == outer),
    (False, True),
)

check(
    "GATE_WORKERS unset -> default",
    run_all.workers_from_env({}),
    run_all.DEFAULT_WORKERS,
)
# The number of workers and the order of the slow modules are tuned from measurements (see
# run_all.DEFAULT_WORKERS): what must hold whatever they are tuned to is checked here.
check(
    "by default the modules run in parallel",
    isinstance(run_all.DEFAULT_WORKERS, int) and run_all.DEFAULT_WORKERS >= 2,
    True,
)
check(
    "every module started first is a real module, named once",
    (
        [n for n in run_all.SLOW_FIRST if n not in run_all.TESTS],
        len(run_all.SLOW_FIRST) == len(set(run_all.SLOW_FIRST)),
        len(run_all.TESTS) == len(set(run_all.TESTS)),
    ),
    ([], True, True),
)
reset()
script("ok_quiet", 'print("FAILURES: 0")\n')
script("ok_show", 'print("FAILURES: 0")\n')
ok, out = run(["ok_quiet", "ok_show"], workers=2)
timing = re.search(
    r"^gate: 2 modules, sum (\d+)s, wall (\d+)s, workers 2$", out, re.MULTILINE
)
check(
    "the run says how long it took: modules, their sum, the wall time, the workers",
    (ok, timing is not None, out.strip().splitlines()[-1].startswith("gate: ")),
    (True, True, True),
)
# Modules run at the same time: a working folder two of them use is one they delete under
# each other (2026-11-16: test_project_switch and test_task_switch both had `_tmp_switch`).
# Every string that names such a folder counts, however the path around it is built
# (`HERE / "_tmp_x"`, `Path(__file__).resolve().parent / "_tmp_x"`, `"C:/dev/_x"`, ...).
# This file is left out: it names other modules' folders as data.
WORK_FOLDER = re.compile(r'"(_tmp[\w-]*)"|"(C:/dev/_[\w-]+)"')
users: dict[str, set[str]] = {}
for module in sorted(HERE.glob("test_*.py")):
    if module.name == Path(__file__).name:
        continue
    for a, b in WORK_FOLDER.findall(module.read_text(encoding="utf-8")):
        users.setdefault((a or b).lower(), set()).add(module.name)
check(
    "no two test modules use the same working folder",
    {name: sorted(mods) for name, mods in users.items() if len(mods) > 1},
    {},
)
check(
    "... and the folders of the modules are still found (the pattern is not stale)",
    (
        len(users) >= 50,
        "_tmp_switch" in users,
        "_tmp_task_switch" in users,
        "c:/dev/_switch_proj" in users,
        "_tmp_discipline_approvals" in users,
        "c:/dev/_disc_test_project" in users,
    ),
    (True, True, True, True, True, True),
)
reset()
script("ok_quiet", 'print("FAILURES: 0")\n')
script("bad", 'print("FAILURES: 1")\nraise SystemExit(1)\n')
ok, out = run(["ok_quiet", "bad"], workers=2)
check(
    "the time line is also the last line of a run that failed",
    (
        ok,
        re.search(
            r"^gate: 2 modules, sum \d+s, wall \d+s, workers 2$", out, re.MULTILINE
        )
        is not None,
    ),
    (False, True),
)
check(
    "GATE_WORKERS=1 -> sequential", run_all.workers_from_env({"GATE_WORKERS": "1"}), 1
)
for bad_value in (
    "0",
    "-2",
    "x",
    "",
    "²",
    "١",
):  # non-ASCII digits: isdigit() but not int()
    try:
        run_all.workers_from_env({"GATE_WORKERS": bad_value})
        got = "accepted"
    except SystemExit as e:
        got = "refused" if e.code else "exit 0"
    check(f"GATE_WORKERS={bad_value!a} is refused", got, "refused")  # cp949-safe

shutil.rmtree(TMP, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
