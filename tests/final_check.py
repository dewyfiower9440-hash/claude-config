"""Final environment check. Prints one line per check: PASS/FAIL/WARN."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
SCRATCH = Path(__file__).parent
results: list[tuple[str, str, str]] = []


def rec(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status:4} {name}" + (f" - {detail}" if detail else ""), flush=True)


def run(
    cmd: list[str] | str,
    cwd: Path | None = None,
    timeout: int = 600,
    shell: bool = False,
) -> tuple[int, str]:
    env = os.environ.copy()
    # Fresh PATH from the registry, like a new terminal would have.
    try:
        user = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "[Environment]::GetEnvironmentVariable('Path','User')+';'+"
                    "[Environment]::GetEnvironmentVariable('Path','Machine')"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if user:
            env["PATH"] = user
    except OSError:
        pass
    if isinstance(cmd, list):
        # CreateProcess searches the *parent's* PATH; resolve against the fresh PATH explicitly.
        resolved = shutil.which(cmd[0], path=env["PATH"])
        if resolved:
            cmd = [resolved, *cmd[1:]]
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=shell,
            env=env,
            check=False,
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


# ------------------------------------------------------------ 1. toolchain
TOOLS = {
    "git": ["git", "--version"],
    "gh": ["gh", "--version"],
    "node": ["node", "-v"],
    "pnpm": ["pnpm.cmd", "-v"],
    "python": ["python", "-V"],
    "uv": ["uv", "--version"],
    "ruff": ["ruff", "--version"],
    "pyright": ["pyright", "--version"],
    "docker": ["docker", "--version"],
    "claude": ["claude", "--version"],
}
for name, cmd in TOOLS.items():
    code, out = run(cmd)
    rec(
        "PASS" if code == 0 else "FAIL",
        f"tool {name}",
        out.splitlines()[0][:60] if out else "",
    )
code, out = run(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "(Get-Module -ListAvailable PSScriptAnalyzer | Select -First 1).Version.ToString();"
            "(Get-Module -ListAvailable Pester | Sort Version -Desc | Select -First 1).Version.ToString()"
        ),
    ]
)
rec(
    "PASS" if code == 0 and "6." in out else "FAIL",
    "PSScriptAnalyzer / Pester",
    out.replace("\n", " / "),
)
in_session = shutil.which("uv") is not None
rec(
    "PASS" if in_session else "WARN",
    "uv visible in this Claude session",
    "" if in_session else "Claude Code 재시작 필요 (새 PATH 미반영)",
)

# ------------------------------------------------------------ 2. settings / hooks
try:
    s = json.loads((CLAUDE / "settings.json").read_text(encoding="utf-8"))
    rec("PASS", "settings.json valid JSON")
    hooks = s.get("hooks", {})
    want = {
        "PreToolUse": ["guard.py", "discipline.py pre"],
        "PostToolUse": ["format_lint.py", "discipline.py post"],
        "UserPromptSubmit": ["notify.py start", "discipline.py prompt"],
        "Stop": ["notify.py stop", "discipline.py stop"],
        "Notification": ["notify.py attn"],
        "PostToolUseFailure": ["discipline.py fail"],
        "SessionStart": ["session_context.py", "Test-Path"],
    }
    all_cmds = [h["command"] for g in hooks.values() for x in g for h in x["hooks"]]
    py_cmds = [c for c in all_cmds if ".claude/hooks/" in c]
    bare = [c for c in py_cmds if c.startswith("python ")]
    rec(
        "PASS" if not bare else "FAIL",
        "hooks use absolute interpreter (no WindowsApps alias)",
        f"{len(bare)} bare 'python' commands" if bare else f"{len(py_cmds)} commands",
    )
    interps = {c.split(" -I ")[0] for c in py_cmds}
    rec(
        "PASS" if all(Path(i).is_file() for i in interps) else "FAIL",
        "hook interpreter exists",
        ", ".join(sorted(interps)),
    )
    ss = next(c for c in all_cmds if "Test-Path" in c)
    code, out = run(["C:/Program Files/Git/bin/bash.exe", "-c", ss])
    rec(
        "PASS" if code == 0 and not out else "FAIL",
        "SessionStart interpreter check silent when OK",
        out[:80],
    )
    code, out = run(
        [
            "C:/Program Files/Git/bin/bash.exe",
            "-c",
            ss.replace("cpython-", "cpython-missing-"),
        ]
    )
    rec(
        "PASS" if "WARNING" in out else "FAIL",
        "SessionStart interpreter check warns when missing",
    )
    for ev, names in want.items():
        cmds = " | ".join(h["command"] for x in hooks.get(ev, []) for h in x["hooks"])
        missing = [n for n in names if n not in cmds]
        rec(
            "PASS" if not missing else "FAIL",
            f"hook {ev}",
            "missing " + ", ".join(missing) if missing else "",
        )
    perms = s.get("permissions", {})
    rec(
        "PASS",
        "permissions",
        f"allow {len(perms.get('allow', []))} / ask {len(perms.get('ask', []))} / "
        f"deny {len(perms.get('deny', []))}",
    )
    for f in re.findall(r"C:/Users/user/\.claude/hooks/(\w+\.py)", json.dumps(hooks)):
        if not (CLAUDE / "hooks" / f).exists():
            rec("FAIL", f"hook file {f}", "missing")
except (OSError, ValueError) as e:
    rec("FAIL", "settings.json", str(e))

for test in ("test_guard.py", "test_discipline.py", "test_session_context.py"):
    code, out = run([sys.executable, "-I", str(SCRATCH / test)])
    last = out.splitlines()[-1] if out else ""
    rec(
        "PASS" if code in (0, None) and "FAILURES: 0" in out else "FAIL",
        f"hook tests {test}",
        last,
    )
code, out = run(["ruff", "check", str(CLAUDE / "hooks")])
rec("PASS" if code == 0 else "FAIL", "hooks lint", out.splitlines()[-1] if out else "")

# ------------------------------------------------------------ 3. CLAUDE.md / rules / skills / agents
cm = (CLAUDE / "CLAUDE.md").read_text(encoding="utf-8")
for imp in re.findall(r"^@(\S+)", cm, re.MULTILINE):
    rec("PASS" if (CLAUDE / imp).exists() else "FAIL", f"CLAUDE.md import {imp}")
rec("PASS" if len(cm) < 20000 else "WARN", "CLAUDE.md size", f"{len(cm)} chars")
imports = re.findall(r"^@(\S+)", cm, re.MULTILINE)
rec(
    "PASS" if not imports else "FAIL",
    "CLAUDE.md has no @rules imports (rules auto-load)",
    ", ".join(imports),
)
for name in ("python", "typescript", "powershell"):
    text = (CLAUDE / "rules" / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"---\npaths:\n((?:  - \"[^\"]+\"\n)+)---\n", text)
    rec(
        "PASS" if m else "FAIL",
        f"rules/{name}.md paths frontmatter",
        m.group(1).replace("\n", " ").strip() if m else "",
    )
for marker in (
    "## 2a. Learning loop",
    "## Gotchas",
    "## 2b. Context hygiene",
    "docs/plans/",
):
    rec("PASS" if marker in cm else "FAIL", f"CLAUDE.md contains {marker!r}")
for skill, marker in (
    ("plan", "status: in-progress"),
    ("implement", "Stop conditions"),
):
    t = (CLAUDE / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    rec("PASS" if marker in t else "FAIL", f"/{skill} mentions {marker!r}")
for d in sorted(
    p for p in (CLAUDE / "skills").iterdir() if p.is_dir() and p.name != "synced"
):
    sk = d / "SKILL.md"
    text = sk.read_text(encoding="utf-8") if sk.exists() else ""
    m = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    ok = bool(
        m
        and re.search(rf"^name: {re.escape(d.name)}$", m.group(1), re.MULTILINE)
        and "description:" in m.group(1)
    )
    rec("PASS" if ok else "FAIL", f"skill /{d.name}")
reg = (CLAUDE / "skills" / "SKILLS.md").read_text(encoding="utf-8")
own = {
    p.name for p in (CLAUDE / "skills").iterdir() if p.is_dir() and p.name != "synced"
}
unreg = [n for n in own if f"`/{n}`" not in reg]
rec("PASS" if not unreg else "FAIL", "SKILLS.md registry complete", ", ".join(unreg))
for a in ("code-reviewer", "test-runner", "researcher", "security-auditor"):
    f = CLAUDE / "agents" / f"{a}.md"
    ok = f.exists() and f.read_text(encoding="utf-8").startswith("---\nname: " + a)
    rec("PASS" if ok else "FAIL", f"agent {a}")

# ------------------------------------------------------------ 4. MCP / GitHub / git
code, out = run(["claude", "mcp", "list"], timeout=180)
for srv in ("github", "context7", "playwright"):
    line = next((ln for ln in out.splitlines() if ln.startswith(srv + ":")), "")
    rec(
        "PASS" if "Connected" in line else "FAIL",
        f"MCP {srv}",
        "" if line else "not registered",
    )
code, out = run([sys.executable, "-I", str(SCRATCH / "smoke_gh_mcp.py")], timeout=120)
rec(
    "PASS" if "get_me login" in out else "FAIL",
    "GitHub MCP auth (get_me)",
    out.splitlines()[-1] if out else "",
)
code, out = run(["gh", "auth", "status"])
rec(
    "PASS" if code == 0 and "Logged in" in out else "FAIL",
    "gh auth",
    "keyring" if "keyring" in out else "",
)
rec(
    "PASS" if "workflow" in out and "repo" in out else "FAIL",
    "gh token scopes repo+workflow",
)
for k in (
    "user.name",
    "user.email",
    "init.defaultBranch",
    "credential.https://github.com.helper",
):
    code, out = run(["git", "config", "--global", k])
    rec("PASS" if code == 0 and out else "FAIL", f"git {k}", out[:60])

# ------------------------------------------------------------ 5. db-mcp
code, out = run(["uv", "run", "pytest", "-q"], cwd=Path("C:/dev/tools/db-mcp"))
rec(
    "PASS" if code == 0 else "FAIL", "db-mcp tests", out.splitlines()[-1] if out else ""
)
code, out = run([sys.executable, "-I", str(SCRATCH / "smoke_mcp.py")], timeout=120)
rec(
    "PASS" if "REJECTED" in out and "42" in out else "FAIL",
    "db-mcp stdio (read + write rejected)",
)

# ------------------------------------------------------------ 6. scaffold end-to-end
root = Path(
    tempfile.mkdtemp(prefix="final-scaf-")
)  # system temp, not the tracked tests dir
gates = {
    "python": [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "pyright"],
        ["uv", "run", "pytest", "-q", "--cov"],
    ],
    "ts": [
        "pnpm lint",
        "pnpm format:check",
        "pnpm typecheck",
        "pnpm test:coverage",
        "pnpm build",
    ],
    "ps": [
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Import-Module Pester -MinimumVersion 5.0; $r = Invoke-Pester -PassThru -Output None; "
                "if ($r.FailedCount -gt 0 -or $r.PassedCount -eq 0) { exit 1 }; "
                "$a = Invoke-ScriptAnalyzer -Path . -Recurse -Settings ./PSScriptAnalyzerSettings.psd1; "
                "if (@($a).Count -gt 0) { $a | Out-String; exit 1 }"
            ),
        ]
    ],
}
for stack, cmds in gates.items():
    name = f"final-{stack}"
    code, out = run(
        [
            sys.executable,
            "-I",
            "C:/dev/tools/scaffold/new_project.py",
            name,
            "--stack",
            stack,
            "--root",
            str(root),
        ]
        + (["--db", "sqlite", "mssql"] if stack == "python" else []),
        timeout=900,
    )
    if code != 0:
        rec("FAIL", f"scaffold {stack}", out.splitlines()[-1] if out else "")
        continue
    proj = root / name
    bad = []
    for c in cmds:
        rc, o = run(c, cwd=proj, shell=isinstance(c, str), timeout=600)
        if rc != 0:
            bad.append(
                f"{c if isinstance(c, str) else ' '.join(c[:3])}: {o.splitlines()[-1] if o else rc}"
            )
    rc, log = run(["git", "log", "--oneline", "-1"], cwd=proj)
    rec(
        "PASS" if not bad else "FAIL",
        f"scaffold {stack} (gate + first commit)",
        "; ".join(bad) if bad else log[:50],
    )
    if stack == "python":
        mcp = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
        rec(
            "PASS" if set(mcp["mcpServers"]) == {"sqlite", "mssql"} else "FAIL",
            "scaffold .mcp.json (sqlite+mssql)",
        )
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ 7. hygiene
stray = [
    p
    for p in Path("C:/dev/tools/scaffold/templates").rglob("*")
    if p.name in (".ruff_cache", "__pycache__")
]
rec(
    "PASS" if not stray else "WARN",
    "templates free of caches",
    f"{len(stray)} cache dirs" if stray else "",
)
leaks = []
for p in [
    CLAUDE / "settings.json",
    HOME / ".claude.json",
    *(CLAUDE / "hooks").glob("*.py"),
    Path("C:/dev/tools/github-mcp-server/github-mcp.cmd"),
]:
    t = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    if re.search(r"gh[opsu]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}", t):
        leaks.append(p.name)
rec(
    "PASS" if not leaks else "FAIL",
    "no plaintext GitHub tokens in config",
    ", ".join(leaks),
)

fails = [r for r in results if r[0] == "FAIL"]
warns = [r for r in results if r[0] == "WARN"]
print(f"\nSUMMARY: {len(results)} checks, {len(fails)} FAIL, {len(warns)} WARN")
