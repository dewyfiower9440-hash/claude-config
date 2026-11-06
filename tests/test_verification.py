"""Unit tests for hooks/verification.py (run with python -I)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
_spec = importlib.util.spec_from_file_location(
    "verification", HOOKS / "verification.py"
)
assert _spec and _spec.loader
v = importlib.util.module_from_spec(_spec)
sys.modules["verification"] = v
_spec.loader.exec_module(v)
sp = sys.modules["shellparse"]

fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:100],
        "" if ok else f"(want {want!r})",
    )


ROOT = "C:/proj"
DECLARED = {
    "root": ROOT,
    "gate": "uv run ruff check . && uv run pytest -q --cov",
    "ci": "required",
    "validate": None,
}
UNDECLARED = {"root": "", "gate": None, "ci": "undeclared", "validate": None}


def outcome(
    cmd: str,
    policy: dict = UNDECLARED,
    *,
    cwd: str = ROOT,
    dialect: str = "bash",
    failed: bool = False,
    bg: bool = False,
) -> str | None:
    ev = v.evaluate(cmd, dialect, cwd, policy, failed=failed, background=bg)
    return None if ev is None else ev["outcome"]


# ---- policy parsing
text = "# p\n\n## Verification\n- gate: `uv run pytest -q`\n- ci: required  # pushes need green CI\n\n## Gotchas\n- gate: not this\n"
pol = v.parse_policy(text, "C:/p")
check("policy: gate from backticks", pol["gate"], "uv run pytest -q")
check("policy: ci with trailing comment", pol["ci"], "required")
check("policy: lines outside the section ignored", pol["validate"], None)
check(
    "policy: no section -> None",
    v.parse_policy("# p\n## Commands\n- gate: x\n", "C:/p"),
    None,
)
check(
    "policy: unknown ci value is strict",
    v.parse_policy("## Verification\nci: maybe\n", "C:/p")["ci"],
    "required",
)
check(
    "policy: ci defaults to none",
    v.parse_policy("## Verification\ngate: make test\n", "C:/p")["ci"],
    "none",
)

tmp = Path(__file__).resolve().parent / "_tmp_verification"
(tmp / "pkg" / "sub").mkdir(parents=True, exist_ok=True)
(tmp / "CLAUDE.md").write_text(
    "## Verification\n- gate: `make check`\n", encoding="utf-8"
)
(tmp / "pkg" / "CLAUDE.md").write_text(
    "# pkg notes without a policy\n", encoding="utf-8"
)
found = v.find_policy(str(tmp / "pkg" / "sub" / "x.py"))
check(
    "find_policy: skips CLAUDE.md without a section",
    (found["root"] == str(tmp), found["gate"]),
    (True, "make check"),
)
shutil.rmtree(tmp, ignore_errors=True)

# ---- attribution
gate = DECLARED["gate"]
check("gate exact -> passed", outcome(gate, DECLARED), "passed")
check("gate then && echo -> passed", outcome(gate + " && echo ok", DECLARED), "passed")
check("gate | tail -> unknown", outcome(gate + " | tail -3", DECLARED), "unknown")
check("gate ; echo -> unknown", outcome(gate + "; echo done", DECLARED), "unknown")
check(
    "set -e makes ; safe",
    outcome("set -e; " + gate + "; echo done", DECLARED),
    "passed",
)
check(
    "set -euo pipefail makes | safe",
    outcome("set -euo pipefail; " + gate + " | tail -3", DECLARED),
    "passed",
)
check("gate || true -> unknown", outcome(gate + " || true", DECLARED), "unknown")
check("gate failed event -> failed", outcome(gate, DECLARED, failed=True), "failed")
check("gate in background -> unknown", outcome(gate, DECLARED, bg=True), "unknown")
check(
    "gate from another dir -> not evidence",
    outcome(gate, DECLARED, cwd="C:/other"),
    None,
)
check(
    "cd into root then gate -> passed",
    outcome(f"cd {ROOT} && " + gate, DECLARED, cwd="C:/other"),
    "passed",
)
check(
    "Git Bash /c/ path cd -> passed",
    outcome("cd /c/proj && " + gate, DECLARED, cwd="C:/other"),
    "passed",
)
check("subset of gate -> partial", outcome("uv run pytest -q", DECLARED), "partial")
check("non-check command -> None", outcome("git status", DECLARED), None)
check(
    "unparseable line containing gate -> unknown",
    outcome('echo "x && ' + gate, DECLARED),
    "unknown",
)

# ---- runners when no policy is declared
check("pytest -> passed", outcome("uv run pytest -q"), "passed")
check("uvx ruff check -> passed", outcome("uvx ruff check ."), "passed")
check(
    "uv run --with pytest pytest -> passed",
    outcome("uv run --no-project --with pytest pytest x -q"),
    "passed",
)
check("python -m pytest -> passed", outcome("python -m pytest"), "passed")
check("pnpm test:coverage -> passed", outcome("pnpm test:coverage"), "passed")
check("ruff format without --check -> None", outcome("ruff format ."), None)
check("python script -> None", outcome("python -I scratch.py"), None)
check("node script -> None", outcome("node build.js"), None)
check("uv run arbitrary script -> None", outcome("uv run python x.py"), None)
check(
    "PowerShell Invoke-Pester after ; is still last -> passed",
    outcome("Import-Module Pester; Invoke-Pester -CI", dialect="powershell"),
    "passed",
)
check(
    "PowerShell Invoke-Pester alone -> passed",
    outcome("Invoke-Pester -CI", dialect="powershell"),
    "passed",
)

# ---- a PowerShell project gate run from the PowerShell tool
PS_GATE = 'powershell -NoProfile -Command "Invoke-ScriptAnalyzer -Path . -Recurse -EnableExit; Invoke-Pester -CI"'
PS_POLICY = {"root": ROOT, "gate": PS_GATE, "ci": "none", "validate": None}
check(
    "PS gate via PowerShell tool -> passed",
    outcome(PS_GATE, PS_POLICY, dialect="powershell"),
    "passed",
)
PS_GATE2 = "Invoke-ScriptAnalyzer -Path .\\src -EnableExit"
check(
    "PS gate with backslash path parsed in the command's dialect -> passed",
    outcome(
        PS_GATE2,
        {"root": ROOT, "gate": PS_GATE2, "ci": "none", "validate": None},
        dialect="powershell",
    ),
    "passed",
)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
