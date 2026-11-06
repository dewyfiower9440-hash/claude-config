"""Scenario tests for hooks/format_lint.py (run with python -I). Needs ruff and PSScriptAnalyzer."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "format_lint.py"
ROOT = Path(tempfile.mkdtemp(prefix="fmtlint-"))
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:80],
        "" if ok else f"(want {want!r})",
    )


def run(payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,  # exit 2 = lint problems fed back to Claude, asserted below
        timeout=120,
    )


def edit(path: Path) -> subprocess.CompletedProcess[str]:
    return run(
        json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(path)}})
    )


def write(name: str, text: str) -> Path:
    p = ROOT / name
    p.write_text(text, encoding="utf-8")
    return p


# ---- python
bad = write("bad.py", "def f():\n    return undefined_name\n")
r = edit(bad)
check("py undefined name -> exit 2", r.returncode, 2)
check("py lint feedback names the rule", "F821" in r.stderr, True)

ugly = write("ugly.py", "x=1\nimport os\n")
r = edit(ugly)
check("py fixable only -> exit 0", r.returncode, 0)
check("py auto-fixed and formatted", ugly.read_text(encoding="utf-8"), "x = 1\n")

# ---- inputs the hook must ignore
check("unsupported extension -> 0", edit(write("notes.txt", "x=1\n")).returncode, 0)
check("missing file -> 0", edit(ROOT / "nope.py").returncode, 0)
check(
    "no file_path -> 0",
    run(json.dumps({"tool_name": "Edit", "tool_input": {}})).returncode,
    0,
)
check("malformed payload -> 0", run("not json").returncode, 0)

# ---- powershell
warn = write("Warn.ps1", "function Get-Thing { gci }\n")
r = edit(warn)
check("ps1 analyzer warning -> exit 2", r.returncode, 2)
check("ps1 feedback names the rule", "PSAvoidUsingCmdletAliases" in r.stderr, True)
clean = write(
    "Clean.ps1",
    "function Get-Thing {\n    [CmdletBinding()]\n    param()\n    Get-ChildItem\n}\n",
)
check("clean ps1 -> 0", edit(clean).returncode, 0)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
