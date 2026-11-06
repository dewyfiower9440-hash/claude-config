"""PostToolUse: auto-format and lint the file Claude just edited.

Formatting is applied silently. Remaining lint problems are fed back to Claude
(exit 2 + stderr) so it fixes them in the same turn.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT = 60


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def project_root(path: Path, markers: tuple[str, ...]) -> Path:
    for parent in [path.parent, *path.parents]:
        if any((parent / m).exists() for m in markers):
            return parent
    return path.parent


def local_bin(root: Path, name: str) -> str | None:
    for cand in (
        root / "node_modules" / ".bin" / f"{name}.cmd",
        root / "node_modules" / ".bin" / name,
    ):
        if cand.exists():
            return str(cand)
    return None


def python_file(path: Path) -> list[str]:
    ruff = shutil.which("ruff")
    if not ruff:
        return []
    root = project_root(path, ("pyproject.toml", "ruff.toml", ".git"))
    run([ruff, "check", "--fix", "--quiet", str(path)], root)
    run([ruff, "format", str(path)], root)
    res = run([ruff, "check", "--output-format", "concise", str(path)], root)
    if res and res.returncode != 0 and res.stdout.strip():
        return [f"ruff:\n{res.stdout.strip()}"]
    return []


def ts_file(path: Path) -> list[str]:
    root = project_root(path, ("package.json",))
    problems: list[str] = []
    prettier = local_bin(root, "prettier")
    if prettier:
        run([prettier, "--write", "--log-level", "warn", str(path)], root)
    eslint = local_bin(root, "eslint")
    if eslint and path.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        run([eslint, "--fix", str(path)], root)
        res = run([eslint, "--format", "unix", str(path)], root)
        if res and res.returncode == 1 and res.stdout.strip():
            problems.append(f"eslint:\n{res.stdout.strip()}")
    return problems


def ps_file(path: Path) -> list[str]:
    quoted = str(path).replace("'", "''")
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f"$r = Invoke-ScriptAnalyzer -Path '{quoted}' -Severity Error,Warning; "
        "$r | ForEach-Object { '{0}:{1} [{2}] {3}' -f $_.Line,$_.Column,$_.RuleName,$_.Message }"
    )
    res = run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], path.parent
    )
    if res and res.stdout.strip():
        return [f"PSScriptAnalyzer ({path.name}):\n{res.stdout.strip()}"]
    return []


HANDLERS = {
    ".py": python_file,
    ".pyi": python_file,
    ".ts": ts_file,
    ".tsx": ts_file,
    ".js": ts_file,
    ".jsx": ts_file,
    ".mjs": ts_file,
    ".cjs": ts_file,
    ".json": ts_file,
    ".css": ts_file,
    ".md": ts_file,
    ".yml": ts_file,
    ".yaml": ts_file,
    ".ps1": ps_file,
    ".psm1": ps_file,
}


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, OSError):  # malformed payload: skip formatting
        sys.exit(0)
    ti = data.get("tool_input", {}) or {}
    path_str = ti.get("file_path") or ti.get("notebook_path")
    if not path_str:
        sys.exit(0)
    path = Path(path_str)
    handler = HANDLERS.get(path.suffix.lower())
    if not handler or not path.exists():
        sys.exit(0)
    problems = handler(path)
    if problems:
        print(
            "Lint problems remain after auto-fix; fix them:\n" + "\n\n".join(problems),
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
