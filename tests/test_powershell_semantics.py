"""D-6: pin the measured exit-status semantics of PowerShell hosts (run with python -I).

Windows PowerShell 5.1 (`powershell`) is asserted against values measured on 2026-11-06.
PowerShell 7 (`pwsh`) is measured and printed when installed (always on GitHub's
windows-latest runner) and must at least surface a failing last command; if pwsh is
missing that is printed explicitly, never silently skipped.

The safety property that matters for discipline.py is checked for both hosts: wherever a
host can exit 0 although the check failed, verification.evaluate() must not say "passed".
Note: the Claude Code PowerShell *tool* wraps scripts and reports the last native exit
code (measured separately: `cmd /c exit 3; Write-Output after` -> PostToolUseFailure);
these host-level facts are the stricter baseline.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
_spec = importlib.util.spec_from_file_location(
    "verification", HOOKS / "verification.py"
)
assert _spec and _spec.loader
verification = importlib.util.module_from_spec(_spec)
sys.modules["verification"] = verification
_spec.loader.exec_module(verification)

UNDECLARED = {"root": "", "gate": None, "ci": "undeclared", "validate": None}
# name, script, exit code measured on Windows PowerShell 5.1, is the failure masked there?
CASES = [
    ("native failure last", "cmd /c exit 3", 1, False),
    ("native failure ; then output", "cmd /c exit 3; Write-Output after", 0, True),
    ("output ; native failure last", "Write-Output before; cmd /c exit 3", 1, False),
    ("native failure piped to Out-String", "cmd /c exit 3 | Out-String", 1, False),
    (
        "explicit exit $LASTEXITCODE",
        "cmd /c exit 3; Write-Output after; exit $LASTEXITCODE",
        3,
        False,
    ),
    ("terminating throw", "throw 'boom'", 1, False),
    (
        "non-terminating Write-Error then output",
        "Write-Error 'nt'; Write-Output after",
        0,
        True,
    ),
    (
        "EAP Stop + cmdlet error",
        "$ErrorActionPreference='Stop'; Get-Item C:\\nope-xyz; Write-Output after",
        1,
        False,
    ),
]
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:90],
        "" if ok else f"(want {want!r})",
    )


def run(exe: str, script: str) -> int:
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        check=False,
    ).returncode


for host in ("powershell", "pwsh"):
    exe = shutil.which(host)
    if not exe:
        print(
            f"NOTICE: {host} is not installed here; its semantics are measured in CI (windows-latest)"
        )
        continue
    version = subprocess.run(
        [exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    print(f"== {host} {version}")
    for name, script, code_51, _masked_51 in CASES:
        code = run(exe, script)
        if host == "powershell":
            check(f"5.1 {name}: exit code", code, code_51)
        else:
            print(f"     pwsh {name}: exit {code}")
        if code == 0 and "exit 3" in script or (code == 0 and "Write-Error" in script):
            # The host hides the failure: discipline must not accept such a line as passed.
            masked = script.replace("cmd /c exit 3", "uv run pytest -q").replace(
                "Write-Error 'nt'", "uv run pytest -q"
            )
            ev = verification.evaluate(
                masked, "powershell", ".", UNDECLARED, failed=False, background=False
            )
            check(
                f"{host} {name}: a check in that position is not 'passed'",
                None if ev is None else ev["outcome"] == "passed",
                False,
            )
    if host == "pwsh":
        check(
            "pwsh native failure last: surfaces as non-zero",
            run(exe, "cmd /c exit 3") != 0,
            True,
        )

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
