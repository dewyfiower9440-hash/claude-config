"""Run a hook script inside the test process, as `python -I <script> <args>` would run it.

A hook is a fresh process on every event; the test modules started thousands of them, and
most of a gate run was process start-up. `run()` runs the script as `__main__` (runpy)
with its own stdin, stdout, stderr, argv, environment and working folder, and with the
hooks' sibling modules imported anew, so module-level state is what a new process has.

What it does not reproduce: a process being ended from outside, two hooks at the same
time, the console encoding, a handle left open at exit, a hook that dies of an exception
it does not catch (here the exception reaches the test and ends it, loudly). So the
first run of every (script, arguments) pair of a test module is a real process (the same
call, end to end), and GATE_REAL_HOOKS=1 makes every run one. Cases about the things
above ask for `real=True` themselves.
"""

from __future__ import annotations

import io
import os
import runpy
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple, NoReturn

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
ALWAYS_REAL = os.environ.get("GATE_REAL_HOOKS") == "1"


class Result(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


_seen: set[tuple[str, tuple[str, ...]]] = set()
counts = {"real": 0, "inproc": 0}  # for the module's own report


def _siblings(folder: Path) -> set[str]:
    """The modules a script of `folder` imports from next to itself."""
    return {p.stem for p in folder.glob("*.py")}


def _text(stream: io.TextIOWrapper, raw: io.BytesIO) -> str:
    stream.flush()
    return raw.getvalue().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _hard_exit(status: int) -> NoReturn:
    """os._exit inside the test process: the status as given, no clean-up of the hook's."""
    raise SystemExit(status)


def run_real(
    script: Path,
    args: list[str],
    stdin: str,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> Result:
    p = subprocess.run(
        [sys.executable, "-I", str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=None if env is None else dict(env),
        cwd=cwd,
        check=False,
    )
    counts["real"] += 1
    return Result(p.returncode, p.stdout, p.stderr)


def run(
    script: Path,
    args: list[str],
    stdin: str,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    real: bool | None = None,
) -> Result:
    key = (str(script), tuple(args))
    first = key not in _seen
    _seen.add(key)
    if real or ALWAYS_REAL or (real is None and first):
        return run_real(script, args, stdin, env, cwd)
    counts["inproc"] += 1
    path = str(script)
    names = _siblings(script.parent)
    kept_modules = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    kept_argv, kept_path = sys.argv, list(sys.path)
    kept_streams = (sys.stdin, sys.stdout, sys.stderr)
    kept_environ, kept_folder = os.environ.copy(), os.getcwd()
    kept_exit, kept_tempdir = os._exit, tempfile.tempdir
    out_raw, err_raw = io.BytesIO(), io.BytesIO()
    # wrappers over bytes: hooks read sys.stdin.buffer and call sys.stdout.reconfigure
    out = io.TextIOWrapper(out_raw, encoding="utf-8", newline="")
    err = io.TextIOWrapper(err_raw, encoding="utf-8", newline="")
    rc = 0
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        if cwd is not None:
            os.chdir(cwd)
        tempfile.tempdir = None  # looked up again from this run's environment
        sys.argv = [path, *args]
        sys.stdin = io.TextIOWrapper(
            io.BytesIO(stdin.encode("utf-8")), encoding="utf-8", newline=""
        )
        sys.stdout, sys.stderr = out, err
        os._exit = _hard_exit
        # by name, from the script's folder: the import system's cached bytecode is used
        # (run_path compiled the source on every run, which cost as much as a process)
        sys.path.insert(0, str(script.parent))
        try:
            runpy.run_module(script.stem, run_name="__main__", alter_sys=True)
        except SystemExit as e:
            if e.code is None:
                rc = 0
            elif isinstance(e.code, int):
                rc = e.code
            else:
                err.write(f"{e.code}\n")
                rc = 1
    finally:
        sys.argv = kept_argv
        sys.path[:] = kept_path
        sys.stdin, sys.stdout, sys.stderr = kept_streams
        os.environ.clear()
        os.environ.update(kept_environ)
        os.chdir(kept_folder)
        os._exit = kept_exit
        tempfile.tempdir = kept_tempdir
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(kept_modules)
    return Result(rc, _text(out, out_raw), _text(err, err_raw))
