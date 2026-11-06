"""Quality gate for the claude-config repository (also used by CI).

    python -I tests/run_all.py

Runs ruff (lint + format check) on hooks/ and tests/, then every hook test script.
test_guard_corpus.py runs only when this machine's transcripts exist (local-only).
Exit code 0 only when every step passed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    "test_shellparse",
    "test_verification",
    "test_powershell_semantics",
    "test_ci_status",
    "test_guard",
    "test_discipline",
    "test_session_context",
    "test_format_lint",
    "test_guard_corpus",  # prints a notice and passes when no local transcripts exist
]


def step(name: str, cmd: list[str]) -> bool:
    p = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
    print(
        f"{'PASS' if p.returncode == 0 else 'FAIL'} {name}: {tail[0][:120]}", flush=True
    )
    if p.returncode != 0:
        print((p.stdout + p.stderr)[-3000:])
    return p.returncode == 0


def main() -> None:
    ruff = shutil.which("ruff")
    if not ruff:
        sys.exit("ruff is not on PATH")
    ok = step("ruff check", [ruff, "check", "hooks", "tests"])
    ok &= step("ruff format --check", [ruff, "format", "--check", "hooks", "tests"])
    for t in TESTS:
        ok &= step(t, [sys.executable, "-I", str(ROOT / "tests" / f"{t}.py")])
    print("GATE", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
