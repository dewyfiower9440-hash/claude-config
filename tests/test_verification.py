"""Unit tests for hooks/verification.py (run with python -I)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
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

# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
tmp = TMP_ROOT / "_tmp_verification"
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
# devenv-final 10: the three forms below counted as passed before; a declared gate now
# counts only alone (V-T1). `set -e` / pipefail still make a runner's status reach the end
# where no gate is declared.
check(
    "gate then && echo -> unknown", outcome(gate + " && echo ok", DECLARED), "unknown"
)
check("gate | tail -> unknown", outcome(gate + " | tail -3", DECLARED), "unknown")
check("gate ; echo -> unknown", outcome(gate + "; echo done", DECLARED), "unknown")
check(
    "set -e does not make a declared gate with ; after it count",
    outcome("set -e; " + gate + "; echo done", DECLARED),
    "unknown",
)
check(
    "set -euo pipefail does not make a declared gate with | after it count",
    outcome("set -euo pipefail; " + gate + " | tail -3", DECLARED),
    "unknown",
)
check(
    "undeclared: set -e makes ; safe for a runner",
    outcome("set -e; uv run pytest -q; echo done"),
    "passed",
)
check(
    "undeclared: set -euo pipefail makes | safe for a runner",
    outcome("set -euo pipefail; uv run pytest -q | tail -3"),
    "passed",
)
check(
    "undeclared: without them ; and | mask a runner",
    (outcome("uv run pytest -q; echo done"), outcome("uv run pytest -q | tail -3")),
    ("unknown", "unknown"),
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

# ---- devenv-final 9/10: a declared gate counts only when the whole command line is the
# gate, or a literal `cd <policy root> &&` and the gate. Each form below ran "the gate"
# and reported success without the gate having decided the exit status.
BYPASSES = [
    ("true || gate", "true || " + gate, ROOT),
    ("exit 0; gate", "exit 0; " + gate, ROOT),
    ("gate &", gate + " &", ROOT),
    ("trap ... EXIT; gate", "trap 'exit 0' EXIT; " + gate, ROOT),
    ("a function named like a tool, then gate", "uv() { true; }; " + gate, ROOT),
    ("export PATH=... && gate", "export PATH=/tmp/fake:$PATH && " + gate, ROOT),
    ('cd "$VAR" && gate', 'cd "$VAR" && ' + gate, ROOT),
    ("cd sub && gate", "cd sub && " + gate, ROOT),
    ("gate from a folder below the root", gate, ROOT + "/pkg/sub"),
    ("gate && echo", gate + " && echo ok", ROOT),
    ("set -e; gate; echo", "set -e; " + gate + "; echo done", ROOT),
    ("set -euo pipefail; gate | tail", "set -euo pipefail; " + gate + " | tail", ROOT),
    ("cd root; gate (not &&)", f"cd {ROOT}; " + gate, "C:/other"),
    ("cd root && cd root && gate", f"cd {ROOT} && cd {ROOT} && " + gate, "C:/other"),
    ("cd -P root && gate", f"cd -P {ROOT} && " + gate, "C:/other"),
]
for label, line, where in BYPASSES:
    check(f"V-T1 {label} -> unknown", outcome(line, DECLARED, cwd=where), "unknown")
# Found by the closing review: something in brackets before the gate was not seen as
# "something before the gate". In PowerShell `( )` and `@( )` run in the session's own
# scope, and `&{ }` can still set a global function or an environment variable.
for dialect, lines in (
    (
        "powershell",
        (
            '($env:PATH = "C:\\fake;$env:PATH"); ' + gate,
            "(Set-Alias uv hostname -Scope Global); " + gate,
            "@(Set-Alias -Scope Global uv hostname); " + gate,
            "&{ function global:uv { } }; " + gate,
            "(cd pkg); " + gate,
        ),
    ),
    ("bash", ("(export PATH=/tmp/fake:$PATH); " + gate, gate + "; (exit 0)")),
):
    for line in lines:
        check(
            f"V-T1 [{dialect}] a bracketed command beside the gate -> unknown: {line[:40]}",
            outcome(line, DECLARED, dialect=dialect),
            "unknown",
        )
check(
    "V-T1 ... the gate alone still passes in PowerShell",
    outcome(gate, DECLARED, dialect="powershell"),
    "passed",
)
check(
    "V-T1 ... and a failed event is still a failure, whatever the form",
    outcome("true || " + gate, DECLARED, failed=True),
    "failed",
)
ev = v.evaluate(
    gate + " && echo ok", "bash", ROOT, DECLARED, failed=False, background=False
)
check(
    "V-T1 the reason says what to run instead",
    ev is not None and gate in ev["why"] and "cd " in ev["why"],
    True,
)
for label, declared in (
    ("stages joined by ;", "uv run ruff check .; uv run pytest -q"),
    ("stages joined by ||", "uv run ruff check . || uv run pytest -q"),
    ("a stage piped", "uv run pytest -q | tee log.txt"),
):
    staged = {"root": ROOT, "gate": declared, "ci": "none", "validate": None}
    check(
        f"V-T2 a declared gate with {label}, run as declared -> unknown",
        outcome(declared, staged),
        "unknown",
    )
check(
    "V-T3 the two forms that count: the gate alone at the root; a literal cd to the root",
    (
        outcome(gate, DECLARED),
        outcome(f"cd {ROOT} && " + gate, DECLARED, cwd="C:/other"),
        outcome('cd "C:/proj" && ' + gate, DECLARED, cwd=ROOT + "/pkg"),
        outcome("cd /c/proj && " + gate, DECLARED, cwd="C:/other"),
    ),
    ("passed", "passed", "passed", "passed"),
)
check(
    "V-T3 a heading that only starts with Verification declares nothing",
    (
        v.parse_policy("## Verification notes\n- gate: `true`\n", "C:/p"),
        v.parse_policy("## Verification history\n- gate: `true`\n", "C:/p"),
        v.parse_policy("##  Verification  \n- gate: `make t`\n", "C:/p")["gate"],
    ),
    (None, None, "make t"),
)
# devenv-final 11a: two gate lines (a weaker one slipped in under the real one) declare
# nothing that can pass.
TWO = "## Verification\n- gate: `uv run pytest -q --cov`\n- gate: `true`\n- ci: required\n"
two = v.parse_policy(TWO, ROOT)
check(
    "V-T5 two gate lines: no gate, the policy says why, ci stays",
    (two["gate"], bool(two.get("invalid")), two["ci"]),
    (None, True, "required"),
)
check(
    "V-T5 ... neither declared line, nor a runner, passes there; other commands are no check",
    (
        outcome("true", two),
        outcome("uv run pytest -q --cov", two),
        outcome("uv run pytest -q", two),
        outcome("git status", two),
        outcome("uv run pytest -q", two, cwd="C:/other"),
    ),
    ("unknown", "unknown", "unknown", None, None),
)
check(
    "V-T5 one gate line (and a gate: line outside the section) is a valid policy",
    v.parse_policy(text, "C:/p").get("invalid"),
    None,
)
# Without a declared policy only a command that runs checks counts as a runner.
for line in (
    "pytest --version",
    "uv run pytest --collect-only -q",
    "pytest --co",
    "python -m pytest --help",
    "ruff check --exit-zero .",
    "uvx ruff check . --exit-zero",
    "pnpm build",
    "npm run build",
    "cargo build",
    "tsc --showConfig",
    "eslint --print-config x.js",
):
    check(f"V-T4 {line} is not a check -> None", outcome(line), None)
check(
    "V-T4 ... the runs themselves still count",
    (
        outcome("pytest -q"),
        outcome("ruff check ."),
        outcome("pnpm test"),
        outcome("cargo test"),
    ),
    ("passed", "passed", "passed", "passed"),
)

# devenv-final 17: the parser's keywords and wrappers are seen through, so what stands
# behind them is judged as the command it is, and a negated or conditional run is no pass.
check(
    "V-T6 a runner behind `!` or in a condition proves nothing; behind timeout it is a run",
    (
        outcome("! uv run pytest -q"),
        outcome("if uv run pytest -q; then echo ok; fi"),
        outcome("timeout 600 uv run pytest -q"),
        outcome("for t in a b; do uv run pytest $t; done"),
    ),
    (None, None, "passed", "unknown"),
)
check(
    "V-T6 a declared gate behind a wrapper or keyword is not the declared gate (its words "
    "differ): a test run that is not the gate, never a pass",
    (
        outcome("timeout 600 " + gate, DECLARED),
        outcome("! " + gate, DECLARED),
        outcome("time " + gate, DECLARED),
    ),
    ("partial", "partial", "partial"),
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

# ---- D-6: PowerShell cmdlet runners report failure only with exit switches (measured)
check(
    "Invoke-Pester without -CI -> not passed",
    outcome("Invoke-Pester", dialect="powershell"),
    "unknown",
)
check(
    "Invoke-Pester -Output Detailed (no -CI) -> not passed",
    outcome("Invoke-Pester -Output Detailed", dialect="powershell"),
    "unknown",
)
check(
    "Invoke-Pester -CI -> passed",
    outcome("Invoke-Pester -CI", dialect="powershell"),
    "passed",
)
check(
    "Invoke-Pester -EnableExit -> passed",
    outcome("Invoke-Pester -EnableExit", dialect="powershell"),
    "passed",
)
check(
    "Invoke-ScriptAnalyzer without -EnableExit -> not passed",
    outcome("Invoke-ScriptAnalyzer -Path . -Recurse", dialect="powershell"),
    "unknown",
)
check(
    "Invoke-ScriptAnalyzer -EnableExit -> passed",
    outcome("Invoke-ScriptAnalyzer -Path . -EnableExit", dialect="powershell"),
    "passed",
)
check(
    "EAP Stop does not make a later statement safe -> unknown",
    outcome(
        "$ErrorActionPreference = 'Stop'; uv run pytest -q; Write-Output done",
        dialect="powershell",
    ),
    "unknown",
)
check(
    "PS native runner then native command -> unknown",
    outcome("uv run pytest -q; cmd /c exit 0", dialect="powershell"),
    "unknown",
)

# guard-b15 1: Git Bash's /tmp is the Windows temp folder, for cwd tracking too
check(
    "_resolve: /tmp/x is under the temp folder",
    v._resolve("C:/x", "/tmp/a/b"),
    os.path.normpath(os.path.join(tempfile.gettempdir(), "a", "b")),
)
check(
    "_resolve: /tmp alone is the temp folder",
    v._resolve("C:/x", "/tmp"),
    os.path.normpath(tempfile.gettempdir()),
)
check(
    "_resolve: /tmpx is not the temp folder",
    v._resolve("C:/x", "/tmpx"),
    os.path.normpath("C:/tmpx"),
)
# guard-b15 5: a pattern that the shell expands to one folder is followed
_pat = Path(tempfile.mkdtemp(prefix="verification-cd-"))
(_pat / "only-one").mkdir()
(_pat / "two-a").mkdir()
(_pat / "two-b").mkdir()
(_pat / "two-c.txt").write_text("", encoding="utf-8")
check(
    "_resolve: a pattern matching one folder goes there",
    v._resolve(str(_pat), "only-*/sub"),
    os.path.normpath(str(_pat / "only-one" / "sub")),
)
check(
    "_resolve: a pattern matching two folders stays a pattern",
    v._resolve(str(_pat), "two-*"),
    os.path.normpath(str(_pat / "two-*")),
)
check(
    "_resolve: a pattern matching only a file stays a pattern",
    v._resolve(str(_pat), "two-c.*"),
    os.path.normpath(str(_pat / "two-c.*")),
)
shutil.rmtree(_pat, ignore_errors=True)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
