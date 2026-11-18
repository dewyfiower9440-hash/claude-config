"""Auto-format and lint the file Claude just edited (PostToolUse).

Formatting is applied silently (one line on stdout says that the file changed). Remaining
lint problems are fed back to Claude (exit 2 + stderr) so it fixes them in the same turn.

`process(data)` does the work and never exits: discipline.py calls it in-process for an
edit, before it reads the file's content (two hooks of the same event run in parallel, so a
formatter of its own process changed the file while the post hook was hashing it). Run as a
script it behaves as before.

Only files of the session's repository (the git root above the payload's `cwd`) are
touched, and never its plan files: an approved plan must stay the approved text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT = 60  # one tool call
RUN_BUDGET_S = (
    60.0  # all tool calls of one run together (the hook's own limit is higher)
)
# Characters cmd.exe gives a meaning to. A `.cmd` shim is run through cmd.exe, which reads
# its own path and every argument: a file named `a&calc.ts` must not become a command.
CMD_META = re.compile(r'[&|<>^%!"`;,=()\n\r]')
# Fixes ruff must not apply on its own: an import or a variable that is unused right now is
# usually used by the next edit of the same change.
UNFIXABLE = "F401,F841"
NO_FORMAT = ("docs/plans/",)  # repository-relative prefixes that are never rewritten

_deadline: float | None = None


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """One tool call within what is left of the run's budget (None: not run or failed)."""
    timeout: float = TIMEOUT
    if _deadline is not None:
        timeout = min(timeout, _deadline - time.monotonic())
        if timeout <= 0:
            return None
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
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


def repository_root(cwd: Path) -> Path | None:
    """The folder above (or at) `cwd` that holds a `.git` entry: the session's repository."""
    try:
        for folder in (cwd, *cwd.parents):
            if (folder / ".git").exists():
                return folder
    except OSError:  # cannot be inspected: no repository to format in
        return None
    return None


def inside(path: Path, root: Path) -> str | None:
    """`path` relative to `root` (posix, lower case), judged where both really are; None
    when it is not inside."""
    try:
        real, base = os.path.realpath(path), os.path.realpath(root)
    except (OSError, ValueError):
        return None
    real_n, base_n = os.path.normcase(real), os.path.normcase(base)
    if not real_n.startswith(base_n.rstrip("\\/") + os.sep):
        return None
    return os.path.relpath(real_n, base_n).replace("\\", "/")


def which_outside(name: str, *folders: Path) -> str | None:
    """Absolute path of program `name` from PATH, never from `folders` or below them (the
    edited file's folder, the session's folder): a `ruff.cmd` dropped next to the code is
    not the formatter. shutil.which would look in the current folder first on Windows."""
    avoid = [os.path.normcase(os.path.realpath(f)) for f in folders]
    suffixes = (
        [
            e.lower()
            for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
            if e
        ]
        if os.name == "nt"
        else [""]
    )
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue  # "" and "." mean the current folder
        folder = os.path.normcase(os.path.realpath(entry))
        if any(
            folder == a or folder.startswith(a.rstrip("\\/") + os.sep) for a in avoid
        ):
            continue
        for suffix in suffixes:
            candidate = os.path.join(entry, name + suffix)
            if os.path.isfile(candidate):
                return candidate
    return None


def local_bin(root: Path, name: str) -> str | None:
    for cand in (
        root / "node_modules" / ".bin" / f"{name}.cmd",
        root / "node_modules" / ".bin" / name,
    ):
        if cand.exists():
            return str(cand)
    return None


def python_file(path: Path, cwd: Path) -> list[str]:
    ruff = which_outside("ruff", path.parent, cwd)
    if not ruff:
        return []
    root = project_root(path, ("pyproject.toml", "ruff.toml", ".git"))
    run([ruff, "check", "--fix", "--unfixable", UNFIXABLE, "--quiet", str(path)], root)
    run([ruff, "format", str(path)], root)
    res = run([ruff, "check", "--output-format", "concise", str(path)], root)
    if res and res.returncode != 0 and res.stdout.strip():
        return [f"ruff:\n{res.stdout.strip()}"]
    return []


def ts_file(path: Path, cwd: Path) -> list[str]:
    root = project_root(path, ("package.json",))
    problems: list[str] = []
    prettier = local_bin(root, "prettier")
    eslint = local_bin(root, "eslint")
    shims = [
        s for s in (prettier, eslint) if s and s.lower().endswith((".cmd", ".bat"))
    ]
    if shims and any(CMD_META.search(text) for text in (*shims, str(path))):
        return []  # see CMD_META: not run at all rather than run through cmd.exe
    if prettier:
        run([prettier, "--write", "--log-level", "warn", str(path)], root)
    if eslint and path.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        run([eslint, "--fix", str(path)], root)
        res = run([eslint, "--format", "unix", str(path)], root)
        if res and res.returncode == 1 and res.stdout.strip():
            problems.append(f"eslint:\n{res.stdout.strip()}")
    return problems


def ps_file(path: Path, cwd: Path) -> list[str]:
    powershell = which_outside("powershell", path.parent, cwd)
    if not powershell:
        return []
    quoted = str(path).replace("'", "''")
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        # -Path takes a wildcard pattern: `[`, `]`, `*`, `?` in a file name are escaped
        f"$p = [WildcardPattern]::Escape('{quoted}'); "
        "$r = Invoke-ScriptAnalyzer -Path $p -Severity Error,Warning; "
        "$r | ForEach-Object { '{0}:{1} [{2}] {3}' -f $_.Line,$_.Column,$_.RuleName,$_.Message }"
    )
    res = run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script], path.parent
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


def _content(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def process(data: object) -> tuple[bool, list[str]]:
    """(the file was changed, lint problems that remain) for one PostToolUse payload.
    Nothing happens for a file outside the session's repository, a plan file, an unknown
    suffix or a missing file."""
    global _deadline
    if not isinstance(data, dict):
        return False, []
    ti = data.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    path_str = ti.get("file_path") or ti.get("notebook_path")
    cwd_str = data.get("cwd")
    if not isinstance(path_str, str) or not path_str:
        return False, []
    cwd = Path(cwd_str) if isinstance(cwd_str, str) and cwd_str else Path.cwd()
    path = Path(path_str) if os.path.isabs(path_str) else cwd / path_str
    handler = HANDLERS.get(path.suffix.lower())
    if not handler or not path.is_file():
        return False, []
    root = repository_root(cwd)
    relative = inside(path, root) if root else None
    if relative is None or relative.startswith(NO_FORMAT):
        return False, []
    _deadline = time.monotonic() + RUN_BUDGET_S
    before = _content(path)
    problems = handler(path, cwd)
    return _content(path) != before, problems


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, OSError):  # malformed payload: skip formatting
        sys.exit(0)
    changed, problems = process(data)
    report(changed, problems, data)
    sys.exit(2 if problems else 0)


def changed_note(data: object) -> str:
    """The one line that says the edited file was rewritten."""
    ti = data.get("tool_input") if isinstance(data, dict) else None
    name = (
        Path(str((ti or {}).get("file_path") or "")).name
        if isinstance(ti, dict)
        else ""
    )
    return f"format_lint: {name} was reformatted (read it again before editing it)"


def report(
    changed: bool, problems: list[str], data: object, as_json: bool = False
) -> None:
    """What a run tells: one line when the file was rewritten (as the hook output of a
    PostToolUse hook when `as_json`: a hook's stdout is one JSON object or none), and the
    remaining problems on stderr."""
    if changed and as_json:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": changed_note(data),
                    }
                }
            )
        )
    elif changed:
        print(changed_note(data))
    if problems:
        print(
            "Lint problems remain after auto-fix; fix them:\n" + "\n\n".join(problems),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
