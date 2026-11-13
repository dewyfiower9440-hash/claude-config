"""run_all.py runner: modules may run in parallel, but results, failure handling and output
stay as with the sequential runner. Fake test scripts in an isolated temp folder.

    python -I tests/test_run_all.py
"""

from __future__ import annotations

import contextlib
import io
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

check(
    "GATE_WORKERS unset -> default",
    run_all.workers_from_env({}),
    run_all.DEFAULT_WORKERS,
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
