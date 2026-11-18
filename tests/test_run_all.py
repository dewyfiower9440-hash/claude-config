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
import subprocess
import sys
import tempfile
import time
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


def alive(pid: int) -> bool:
    """Whether a process with this id exists (asked of the system, not of run_all)."""
    if os.name == "nt":
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        ).stdout
        return f'"{pid}"' in listed
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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

# devenv-final 5: the project folders of the modules are this run's own too
script(
    "work_probe",
    "import os, sys\n"
    "from pathlib import Path\n"
    "root = Path(os.environ['GATE_WORK_ROOT'])\n"
    "proj = root / '_probe_proj' / '.git' / 'objects'\n"
    "proj.mkdir(parents=True)\n"
    "(proj / 'ab').write_text('x')\n"
    "os.chmod(proj / 'ab', 0o444)  # like git's object files\n"
    "inside = Path(os.environ['GATE_TMP_ROOT'])\n"
    "(inside / '_tmp_probe').mkdir()\n"
    "(inside / '_tmp_probe' / 'f').write_text('x')\n"
    "(Path(sys.argv[0]).parent / 'work.result').write_text(str(root))\n"
    "(Path(sys.argv[0]).parent / 'inside.result').write_text(str(inside))\n"
    "print('FAILURES: 0')\n",
)
ok, out = run(["work_probe"], workers=1)
seen_work = Path((TMP / "work.result").read_text())
seen_inside = Path((TMP / "inside.result").read_text())
check(
    "G-T12 a module gets a folder for its _tmp_* folders, made for this run inside "
    "tests/ under a name git ignores; gone afterwards",
    (
        seen_inside.name.startswith(f"_tmp_gate-{os.getpid()}-"),
        os.path.normcase(seen_inside.parent) == os.path.normcase(HERE),
        seen_inside.exists(),
    ),
    (True, True, False),
)
check(
    "G-T12 no module builds a _tmp folder straight under tests/ (TMP_ROOT instead)",
    [
        p.name
        for p in sorted(HERE.glob("test_*.py"))
        if p.name != Path(__file__).name
        and re.search(
            r'(HERE|\.parent) / "_tmp',
            p.read_text(encoding="utf-8").replace('ROOT.parent / "_tmp_disc_late"', ""),
        )
    ],
    [],
)
check(
    "G-T11 a module gets a work folder made for this run, under the projects folder",
    (
        ok,
        seen_work.name.startswith(f"_gate-{os.getpid()}-"),
        os.path.normcase(seen_work.parent) == os.path.normcase(run_all.WORK_PARENT),
    ),
    (True, True, True),
)
check(
    "G-T11 ... and it is gone afterwards, with what the module left (read-only too)",
    (seen_work.exists(), "NOTICE" in out),
    (False, False),
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
# devenv-ci-workers: CI runs the same gate command with fewer workers (the runner has few
# cores and no 600 s limit; time budgets in tests suffer when its modules crowd each other).
# The value is read as text here: a typo would otherwise show only on CI (workers_from_env
# exits on a bad value).
WORKFLOW = (HERE.parent / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)
ci_workers = re.findall(r'^\s*GATE_WORKERS:\s*"?([^"\s#]+)"?', WORKFLOW, re.MULTILINE)
check(
    "the CI workflow sets GATE_WORKERS once, to a number the runner accepts, not above "
    "the local default, and still runs the gate command itself",
    (
        len(ci_workers),
        [run_all.workers_from_env({"GATE_WORKERS": v}) for v in ci_workers],
        all(v.isdigit() and int(v) <= run_all.DEFAULT_WORKERS for v in ci_workers),
        "-I tests/run_all.py" in WORKFLOW,
    ),
    (1, [4], True, True),
)
reset()
script("ok_quiet", 'print("FAILURES: 0")\n')
script("ok_show", 'print("FAILURES: 0")\n')
ok, out = run(["ok_quiet", "ok_show"], workers=2)
timing = re.search(
    r"^gate: 2 modules, 0 checks, sum (\d+)s, wall (\d+)s, workers 2$",
    out,
    re.MULTILINE,
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
WORK_FOLDER = re.compile(r'"(_tmp[\w-]*)"|"(C:/dev/_[\w-]+)"|WORK_ROOT / "(_[\w-]+)"')
users: dict[str, set[str]] = {}
fixed_paths: list[str] = []
for module in sorted(HERE.glob("test_*.py")):
    if module.name == Path(__file__).name:
        continue
    for a, b, c in WORK_FOLDER.findall(module.read_text(encoding="utf-8")):
        users.setdefault((a or b or c).lower(), set()).add(module.name)
        fixed_paths += [f"{module.name}: {b}"] if b else []
# A name only (the folder a past command is judged from): never created, nothing to isolate.
fixed_paths.remove("test_guard_corpus.py: C:/dev/_nobody_s_folder")
check(
    'G-T10 no module names a fixed folder under C:/dev (WORK_ROOT / "_name" instead)',
    fixed_paths,
    [],
)
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
        "_switch_proj" in users,
        "_tmp_discipline_approvals" in users,
        "_disc_test_project" in users,
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
            r"^gate: 2 modules, 0 checks, sum \d+s, wall \d+s, workers 2$",
            out,
            re.MULTILINE,
        )
        is not None,
    ),
    (False, True),
)
# devenv-final 4: a test file that is not in TESTS never runs, and nothing says so.
check(
    "G-T8 the test files on disk are the modules the gate runs (both ways)",
    sorted(p.stem for p in HERE.glob("test_*.py")),
    sorted(run_all.TESTS),
)
# A module that starts hooks/discipline.py (as a process, or by importing it) without a
# home of its own reads and writes the real approval store under ~/.claude/discipline.
RUNS_DISCIPLINE = re.compile(
    r"""["']discipline\.py["']|^\s*import discipline\b""", re.MULTILINE
)
# The modules the pattern finds that do not run the hook, each with the reason.
NOT_RUN = {
    "test_docs_consistency": "passes the hook's path to verification.find_policy only",
    "test_usage_report": "reads one constant of the imported module",
}
found = {
    p.stem: "CLAUDE_DISCIPLINE_HOME" in text
    for p in sorted(HERE.glob("test_*.py"))
    if p.name != Path(__file__).name
    and RUNS_DISCIPLINE.search(text := p.read_text(encoding="utf-8"))
}
check(
    "G-T9 every module that runs discipline.py sets CLAUDE_DISCIPLINE_HOME",
    sorted(n for n, isolated in found.items() if not isolated and n not in NOT_RUN),
    [],
)
check(
    "G-T9 ... the exceptions are still exceptions, and the pattern still finds modules",
    (
        sorted(n for n in NOT_RUN if found.get(n) is not False),
        len(found) >= 15,
        found.get("test_ci_status"),
    ),
    ([], True, True),
)

# devenv-final 3: exit code 0 alone is not a pass. A module passes when it also ends on
# the line every module prints last, "FAILURES: 0".
reset()
script("early_exit", 'print("OK   one check")\nraise SystemExit(0)\n')
script("silent", "pass\n")
script("ok_two", 'print("OK   a")\nprint("OK   b -> 1")\nprint("FAILURES: 0")\n')
script("notice", 'print("NOTICE: nothing to run here")\nprint("FAILURES: 0")\n')
script(
    "trailing",
    'print("FAILURES: 0")\nprint("something printed after the verdict")\n',
)
ok, out = run(["early_exit"], workers=1)
check(
    "G-T1 a module that exits 0 before its last line fails the run, and says why",
    (ok, "FAIL early_exit: " in out, "FAILURES: 0" in out.split("gate:")[0]),
    (False, True, True),
)
ok, out = run(["silent"], workers=1)
check("G-T2 a module that prints nothing fails the run", ok, False)
ok, out = run(["trailing"], workers=1)
check("G-T3 a module whose last line is not the verdict fails the run", ok, False)
ok, out = run(["ok_two", "notice"], workers=2)
check(
    "G-T4 passing modules: their OK lines are added up in the gate line",
    (
        ok,
        re.search(r"^gate: 2 modules, 2 checks, sum ", out, re.MULTILINE) is not None,
    ),
    (True, True),
)
# A module that hangs is ended (its child processes too) and fails; the others still report.
script(
    "hangs",
    "import subprocess, sys, time\n"
    "from pathlib import Path\n"
    "d = Path(sys.argv[0]).parent\n"
    "child = subprocess.Popen([sys.executable, '-I', '-c',\n"
    "    'import time; time.sleep(120)'])\n"
    "(d / 'hangs.child').write_text(str(child.pid))\n"
    "time.sleep(120)\n"
    "print('FAILURES: 0')\n",
)
before_min, run_all.MIN_LIMIT_S = run_all.MIN_LIMIT_S, 3
clock = time.monotonic()
ok, out = run(["hangs", "ok_two"], workers=2)
took = time.monotonic() - clock
run_all.MIN_LIMIT_S = before_min
child_pid = int((TMP / "hangs.child").read_text())
check(
    "G-T5 a module over its time limit fails, the run does not wait for it, the other "
    "module still passes",
    (
        ok,
        took < 60,
        "FAIL hangs: " in out and "time limit" in out,
        "PASS ok_two" in out,
    ),
    (False, True, True, True),
)
check(
    "G-T5 ... and the process it started is gone too",
    alive(child_pid),
    False,
)
check(
    "G-T6 the limit is three times the measured time, never under the minimum",
    (
        run_all.time_limit("test_approvals_v2")
        == 3 * run_all.MEASURED_S["test_approvals_v2"],
        run_all.time_limit("test_shellparse"),
        run_all.MIN_LIMIT_S >= 120,
        sorted(n for n in run_all.MEASURED_S if n not in run_all.TESTS),
    ),
    (True, run_all.MIN_LIMIT_S, True, []),
)
# A character the console cannot encode (cp1252 on CI) must not end the run with a traceback.
PROBE = (
    "import sys; sys.path.insert(0, sys.argv[1]); import run_all; "
    "sys.stdout.reconfigure(encoding='cp1252'); "
    "run_all.console(); run_all.report('m', 1, 'FAIL 한글 x', 1.0)"
)
probe = subprocess.run(
    [sys.executable, "-I", "-c", PROBE, str(HERE)],
    capture_output=True,
    check=False,
)
check(
    "G-T7 output the console cannot encode is escaped, not a crash",
    (probe.returncode, b"\\ud55c" in probe.stdout, b"Traceback" in probe.stderr),
    (0, True, False),
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
