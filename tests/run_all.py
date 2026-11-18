"""Quality gate for the claude-config repository (also used by CI).

    python -I tests/run_all.py

Runs ruff (lint + format check) on hooks/ and tests/, then every hook test script.
test_guard_corpus.py runs only when this machine's transcripts exist (local-only).
Exit code 0 only when every step passed.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Modules are independent (own temp folders, uuid sessions, env-set approval homes) and are
# run in parallel; their results are still printed in TESTS order. GATE_WORKERS=1: sequential.
# Measured 2026-11-16: 37 modules whose times add up to 1,833-2,139 s. With 3 workers that
# is 611 s at best, past the 600 s a foreground run may take (a run that goes over is moved
# to the background and is then no evidence). With 4 it is 458-535 s at best. The last line
# of a run gives the sum and the wall time of the modules (the two ruff steps before them,
# a few seconds, are not in it): re-tune when the wall time nears 600 s again.
# Measured 2026-11-17: the sum had grown to 2,059 s and 4 workers took 549-566 s. The wall
# time followed the sum divided by the workers, not the longest module, so 6 workers (16
# logical CPUs here) took 470 s with a sum of 2,336 s, and that run ended on test_approvals
# alone (469 s): it was split in two (test_approvals, test_approvals_v2).
# Measured 2026-11-18: a day of added tests brought the sum to 2,719 s and 6 workers to
# 545 s. test_task_switch was split (test_task_switch, test_prompt_approval) and the start
# order renewed: 475 s with 6 workers (sum 2,739 s), 422 s with 8 (sum 3,121 s, no failure).
# With 8 the run ends on the longest module (test_approvals_v2, 421 s): more workers will
# not shorten it further; fewer or faster cases will. CI sets its own number (ci.yml).
DEFAULT_WORKERS = 8
# Longest first (measured 2026-11-18 with 6 workers: 379, 314, 292, 255, 202, 186, 174, 139,
# 127, 130, 125 s in the order below), so that the end of a run is not one worker finishing
# a long module alone (2026-11-17: with test_task_switch missing from this list it started
# at about 300 s and the run took 471 s instead of about 400 s). A module that grows past
# 90 s belongs here.
SLOW_FIRST = [
    "test_approvals_v2",
    "test_discipline_d3",
    "test_discipline",
    "test_ledger",
    "test_prompt_approval",
    "test_guard_corpus",
    "test_worktree",
    "test_session_store",
    "test_task_switch",
    "test_approvals",
    "test_ci_status",
]
TESTS = [
    "test_shellparse",
    "test_verification",
    "test_powershell_semantics",
    "test_ci_status",
    "test_ci_classify",
    "test_worktree",
    "test_approvals",
    "test_approvals_v2",
    "test_approvals_store",
    "test_scope",
    "test_authz",
    "test_shellwrites",
    "test_guard",
    "test_discipline",
    "test_discipline_d3",
    "test_session_store",
    "test_session_context",
    "test_ledger_context",
    "test_project_switch",
    "test_handoff",
    "test_state_gc",
    "test_usage",
    "test_redact",
    "test_ledger",
    "test_task_switch",
    "test_prompt_approval",
    "test_policy_change",
    "test_review_binding",
    "test_test_gap",
    "test_output_size",
    "test_risk",
    "test_recurrence",
    "test_memory_search",
    "test_ledger_show",
    "test_usage_report",
    "test_mcp_permissions",
    "test_docs_consistency",
    "test_format_lint",
    "test_guard_corpus",  # prints a notice and passes when no local transcripts exist
    "test_run_all",
]


# A module that hangs would otherwise hold the whole run (and CI) until someone ends it. Its
# limit is three times what it took when measured, never under MIN_LIMIT_S. Measured
# 2026-11-18 with 8 workers (seconds; the modules not listed took under 40 s; the CI runner
# with 4 workers took no longer per module, run 44224210470). Re-measure with the list above.
MIN_LIMIT_S = 120.0
LIMIT_FACTOR = 3
MEASURED_S = {
    "test_approvals_v2": 465,
    "test_discipline_d3": 403,
    "test_discipline": 365,
    "test_ledger": 335,
    "test_prompt_approval": 261,
    "test_worktree": 218,
    "test_guard_corpus": 195,
    "test_session_store": 170,
    "test_approvals": 169,
    "test_task_switch": 164,
    "test_ci_status": 154,
    "test_guard": 79,
    "test_authz": 73,
    "test_risk": 58,
    "test_review_binding": 49,
}
WORK_PARENT = Path(
    "C:/dev"
)  # where a run's `_gate-<pid>-*` folder is made (see run_tests)
VERDICT = "FAILURES: 0"  # the line every module prints last when all its checks passed
TIMED_OUT = -9  # run_one's exit code for a module ended at its time limit
KILL_WAIT_S = 15


def time_limit(name: str) -> float:
    return max(MIN_LIMIT_S, LIMIT_FACTOR * MEASURED_S.get(name, 0))


def end_tree(p: subprocess.Popen[str]) -> None:
    """End `p` and what it started (a test module starts hooks, git, shells)."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                capture_output=True,
                timeout=KILL_WAIT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):  # then at least the module itself
            p.kill()
    else:
        p.kill()


def run_one(
    name: str,
    cmd: list[str],
    env: Mapping[str, str] | None = None,
    limit: float | None = None,
) -> tuple[str, int, str, float]:
    start = time.monotonic()
    p = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, err = p.communicate(timeout=limit)
        code = p.returncode
    except subprocess.TimeoutExpired:
        end_tree(p)
        try:
            out, err = p.communicate(timeout=KILL_WAIT_S)
        except subprocess.TimeoutExpired:  # a survivor still holds the pipes
            p.kill()
            out, err = "", ""
        code = TIMED_OUT
        err += f"\nTIMEOUT: over its time limit of {limit:.0f}s; the process tree was ended"
    return name, code, out + err, time.monotonic() - start


def verdict(code: int, output: str) -> str | None:
    """Why this result is not a pass (None when it is). The exit code alone is not enough:
    a module that exits 0 before its checks ran, or prints nothing, has verified nothing."""
    if code == TIMED_OUT:
        return "ended at its time limit"
    if code != 0:
        return f"exit code {code}"
    last = output.strip().splitlines()[-1:] or [""]
    if last[0].strip() != VERDICT:
        return f"exit code 0 but the last line is not {VERDICT!r}"
    return None


def checks(output: str) -> int:
    """The checks a module reported as passed (its `OK ...` lines)."""
    return sum(1 for ln in output.splitlines() if ln.startswith("OK "))


def report(
    name: str, code: int, output: str, seconds: float, plain: bool = False
) -> bool:
    """Print one result line (and the output of a failure). `plain`: a tool, not a test
    module; its exit code is its verdict."""
    why = (
        (None if code == 0 else f"exit code {code}") if plain else verdict(code, output)
    )
    tail = output.strip().splitlines()[-1:] or [""]
    print(
        f"{'FAIL' if why else 'PASS'} {name}: {tail[0][:120]} ({seconds:.0f}s)",
        flush=True,
    )
    if why or name in ALWAYS_SHOW:
        tail = output[-3000:]
        if why:
            print(f"  not a pass: {why}")
            # The failing checks themselves, wherever they are: the tail alone can miss them.
            early = output[: len(output) - len(tail)]
            for line in [ln for ln in early.splitlines() if ln.startswith("FAIL")][:20]:
                print(line[:1000])
        print(tail)
    return why is None


def step(name: str, cmd: list[str]) -> bool:
    return report(*run_one(name, cmd), plain=True)


def _retry_writable(func: Callable[[str], object], path: str, _exc: object) -> None:
    """rmtree could not remove `path`: git writes its object files read-only and Windows
    refuses to delete those. Anything still refused is left (and reported by the caller)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:  # in use or not permitted: the rest of the tree is still removed
        return


def run_tests(names: list[str], workers: int, tests_dir: Path = ROOT / "tests") -> bool:
    """Run every module (slow ones submitted first), report in the declared order.

    The modules share a temp folder made for this run and removed after it: the hooks they
    start keep one state file per session under the temp folder, and a gate run starts
    thousands of sessions (they used to pile up in the real one, tens of thousands)."""
    rank = {n: i for i, n in enumerate(SLOW_FIRST)}
    order = sorted(names, key=lambda n: rank.get(n, len(SLOW_FIRST)))
    temp = tempfile.mkdtemp(prefix="claude-gate-")
    # The project folders the modules build (outside %TEMP%, where the hooks count edits)
    # also belong to this run alone: two gates at once do not delete each other's, and
    # nothing is left under the projects folder (devenv-final 5).
    WORK_PARENT.mkdir(parents=True, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f"_gate-{os.getpid()}-", dir=WORK_PARENT)
    # ... and so do the `_tmp_*` folders they build inside tests/ (inside this repository
    # on purpose: some cases rely on its CLAUDE.md being above them; ignored by git).
    inside = tempfile.mkdtemp(prefix=f"_tmp_gate-{os.getpid()}-", dir=ROOT / "tests")
    env = {
        **os.environ,
        "TEMP": temp,
        "TMP": temp,
        "TMPDIR": temp,
        "GATE_WORK_ROOT": work,
        "GATE_TMP_ROOT": inside,
    }
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                n: pool.submit(
                    run_one,
                    n,
                    [sys.executable, "-I", str(tests_dir / f"{n}.py")],
                    env,
                    time_limit(n),
                )
                for n in order
            }
            results = {n: f.result() for n, f in futures.items()}
    finally:
        for folder, what in ((temp, "temp"), (work, "work"), (inside, "tests")):
            shutil.rmtree(folder, onexc=_retry_writable)
            if os.path.exists(
                folder
            ):  # something still holds a file: not a test result
                print(f"NOTICE: {what} folder not fully removed: {folder}", flush=True)
    wall = time.monotonic() - started
    ok = True
    for n in names:
        ok &= report(*results[n])
    # how close the run is to the time a foreground run may take (see DEFAULT_WORKERS)
    total = sum(seconds for _name, _code, _output, seconds in results.values())
    passed = sum(checks(output) for _name, _code, output, _seconds in results.values())
    print(
        f"gate: {len(names)} modules, {passed} checks, sum {total:.0f}s, "
        f"wall {wall:.0f}s, workers {workers}",
        flush=True,
    )
    return ok


def workers_from_env(env: Mapping[str, str]) -> int:
    raw = env.get("GATE_WORKERS")
    if raw is None:
        return DEFAULT_WORKERS
    if not (raw.isascii() and raw.isdigit()) or int(raw) < 1:  # "²".isdigit() is True
        sys.exit(f"GATE_WORKERS must be a positive integer, got {raw!r}")
    return int(raw)


# Measurement output worth keeping in CI logs even when the test passes (pwsh 7 values).
ALWAYS_SHOW = {"test_powershell_semantics"}


def console() -> None:
    """A module's output can hold characters the console cannot encode (Korean check names
    on cp1252 in CI): escape them instead of ending the run with UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def main() -> None:
    console()
    ruff = shutil.which("ruff")
    if not ruff:
        sys.exit("ruff is not on PATH")
    ok = step("ruff check", [ruff, "check", "hooks", "tests"])
    ok &= step("ruff format --check", [ruff, "format", "--check", "hooks", "tests"])
    ok &= run_tests(TESTS, workers_from_env(os.environ))
    print("GATE", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
