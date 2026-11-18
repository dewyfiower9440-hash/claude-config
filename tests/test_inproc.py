"""tests/inproc.py: a script run inside the test process gives what the same script gives
as a process, and leaves the test process as it was (run with python -I)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

sys.stdout.reconfigure(errors="backslashreplace")
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:160],
        "" if ok else f"(want {want!r})",
    )


WORK = Path(tempfile.mkdtemp(prefix="inproc-"))
BIN = WORK / "bin"
BIN.mkdir()
ELSEWHERE = WORK / "elsewhere"
ELSEWHERE.mkdir()
# A sibling with module-level state: a new process starts it at 0 every time.
(BIN / "counter.py").write_text("n = 0\n", encoding="utf-8")
PROBE_LINES = (
    "import json, os, sys, tempfile",
    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))",
    "import counter",
    "counter.n += 1",
    "data = json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))",
    "sys.stdout.reconfigure(encoding='utf-8')",
    "print(json.dumps({'argv': sys.argv[1:], 'name': __name__,",
    "    'file': os.path.basename(__file__), 'marker': os.environ.get('MARKER'),",
    "    'cwd': os.path.basename(os.getcwd()), 'n': counter.n,",
    "    'temp': os.path.basename(tempfile.gettempdir()), 'said': data.get('say')},",
    "    ensure_ascii=False))",
    "sys.stderr.write('note\\n')",
    "how = data.get('end')",
    "if how == 'hard':",
    "    os._exit(2)",
    "if how == 'text':",
    "    sys.exit('refused')",
    "if how == 'code':",
    "    sys.exit(3)",
    "os.environ['LEAK'] = '1'",
    "os.chdir(os.path.dirname(os.path.abspath(__file__)))",
    "",
)
(BIN / "probe.py").write_text("\n".join(PROBE_LINES), encoding="utf-8")
PROBE = BIN / "probe.py"
# TMPDIR too: tempfile asks it first, and the gate sets all three for its modules
ENV = dict(
    os.environ,
    MARKER="m1",
    TMPDIR=str(ELSEWHERE),
    TEMP=str(ELSEWHERE),
    TMP=str(ELSEWHERE),
)


def both(end: str | None = None, say: str = "x") -> tuple[inproc.Result, inproc.Result]:
    payload = json.dumps({"end": end, "say": say})
    return (
        inproc.run(PROBE, ["a", "b"], payload, env=ENV, cwd=str(ELSEWHERE), real=True),
        inproc.run(PROBE, ["a", "b"], payload, env=ENV, cwd=str(ELSEWHERE), real=False),
    )


real, inside = both()
check(
    "N-T1 a plain run: the same status, output and error text as the process",
    (inside, json.loads(inside.stdout)),
    (
        real,
        {
            "argv": ["a", "b"],
            "name": "__main__",
            "file": "probe.py",
            "marker": "m1",
            "cwd": "elsewhere",
            "n": 1,
            "temp": "elsewhere",
            "said": "x",
        },
    ),
)
real, inside = both()
check(
    "N-T2 again: module-level state of a sibling starts anew, as in a new process",
    (json.loads(inside.stdout)["n"], inside == real),
    (1, True),
)
real, inside = both(say="한글 ✓")
check(
    "N-T3 text outside ASCII goes in and comes out the same",
    (json.loads(inside.stdout)["said"], inside == real),
    ("한글 ✓", True),
)
for end, code in (("hard", 2), ("code", 3), ("text", 1)):
    real, inside = both(end)
    check(
        f"N-T4 the script ends itself ({end}): status {code}, like the process",
        (
            inside.returncode,
            inside.returncode == real.returncode,
            "refused" in inside.stderr,
        ),
        (code, True, end == "text"),
    )
before = (os.getcwd(), os.environ.get("LEAK"), os.environ.get("MARKER"), sys.argv[:])
kept_stdout, kept_exit, kept_temp = sys.stdout, os._exit, tempfile.gettempdir()
both()
check(
    "N-T5 afterwards the test process is as it was: folder, environment, argv, streams, "
    "os._exit, the temp folder, and no sibling left imported",
    (
        (os.getcwd(), os.environ.get("LEAK"), os.environ.get("MARKER"), sys.argv[:]),
        sys.stdout is kept_stdout,
        os._exit is kept_exit,
        tempfile.gettempdir() == kept_temp,
        "counter" in sys.modules,
    ),
    (before, True, True, True, False),
)
sys.path.insert(0, str(BIN))
import counter  # the test's own copy of a sibling, as a test module imports a hook

counter.n = 41
both()
check(
    "N-T6 a sibling the test itself imported is the test's again afterwards, untouched",
    (sys.modules.get("counter") is counter, counter.n),
    (True, 41),
)
(BIN / "boom.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
try:
    inproc.run(BIN / "boom.py", [], "", real=False)
    raised = "nothing"
except RuntimeError as e:
    raised = str(e)
check(
    "N-T7 an exception the script does not catch reaches the test (never a silent pass), "
    "and the process is restored all the same",
    (raised, sys.stdout is kept_stdout, os._exit is kept_exit),
    ("boom", True, True),
)
(BIN / "first.py").write_text("print('ran')\n", encoding="utf-8")
count = dict(inproc.counts)
inproc.run(BIN / "first.py", ["x"], "")
inproc.run(BIN / "first.py", ["x"], "")
inproc.run(BIN / "first.py", ["y"], "")
check(
    "N-T8 unasked, the first run of each (script, arguments) pair is a real process and "
    "the later ones are not",
    (
        inproc.counts["real"] - count["real"],
        inproc.counts["inproc"] - count["inproc"],
    ),
    (2, 1),
)
out = inproc.run_real(
    Path(__file__).resolve().parent / "inproc_all_real.py",
    [str(BIN / "first.py")],
    "",
    env=dict(os.environ, GATE_REAL_HOOKS="1"),
)
check(
    "N-T9 GATE_REAL_HOOKS=1: every run is a real process",
    (out.returncode, out.stdout.strip()),
    (0, "real 3 inproc 0"),
)
shutil.rmtree(WORK, ignore_errors=True)
print("FAILURES:", fails)
