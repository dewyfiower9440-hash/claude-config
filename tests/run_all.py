"""Quality gate for the claude-config repository (also used by CI).

    python -I tests/run_all.py

Runs ruff (lint + format check) on hooks/ and tests/, then every hook test script.
test_guard_corpus.py runs only when this machine's transcripts exist (local-only).
Exit code 0 only when every step passed.
"""

from __future__ import annotations

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
DEFAULT_WORKERS = (
    3  # initial prior (16 GB RAM, resident collectors); re-tune from timings
)
SLOW_FIRST = [
    "test_approvals",
    "test_discipline",
    "test_discipline_d3",
    "test_guard_corpus",
]
TESTS = [
    "test_shellparse",
    "test_verification",
    "test_powershell_semantics",
    "test_ci_status",
    "test_ci_classify",
    "test_worktree",
    "test_approvals",
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
    "test_review_binding",
    "test_test_gap",
    "test_output_size",
    "test_risk",
    "test_recurrence",
    "test_format_lint",
    "test_guard_corpus",  # prints a notice and passes when no local transcripts exist
    "test_run_all",
]


def run_one(
    name: str, cmd: list[str], env: Mapping[str, str] | None = None
) -> tuple[str, int, str, float]:
    start = time.monotonic()
    p = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return name, p.returncode, p.stdout + p.stderr, time.monotonic() - start


def report(name: str, code: int, output: str, seconds: float) -> bool:
    tail = output.strip().splitlines()[-1:] or [""]
    print(
        f"{'PASS' if code == 0 else 'FAIL'} {name}: {tail[0][:120]} ({seconds:.0f}s)",
        flush=True,
    )
    if code != 0 or name in ALWAYS_SHOW:
        tail = output[-3000:]
        if code != 0:
            # The failing checks themselves, wherever they are: the tail alone can miss them.
            early = output[: len(output) - len(tail)]
            for line in [ln for ln in early.splitlines() if ln.startswith("FAIL")][:20]:
                print(line[:1000])
        print(tail)
    return code == 0


def step(name: str, cmd: list[str]) -> bool:
    return report(*run_one(name, cmd))


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
    env = {**os.environ, "TEMP": temp, "TMP": temp, "TMPDIR": temp}
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                n: pool.submit(
                    run_one, n, [sys.executable, "-I", str(tests_dir / f"{n}.py")], env
                )
                for n in order
            }
            results = {n: f.result() for n, f in futures.items()}
    finally:
        shutil.rmtree(temp, onexc=_retry_writable)
        if os.path.exists(temp):  # something still holds a file: not a test result
            print(f"NOTICE: temp folder not fully removed: {temp}", flush=True)
    ok = True
    for n in names:
        ok &= report(*results[n])
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


def main() -> None:
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
