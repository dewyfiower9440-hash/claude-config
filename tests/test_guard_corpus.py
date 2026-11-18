"""LOCAL-ONLY regression: replay every real Bash/PowerShell command from this machine's
Claude Code transcripts through the parsed guard and the legacy whole-string guard.

Requirement: the parsed guard never blocks a command the legacy guard allowed.
Commands the legacy guard blocked but the parsed guard allows are printed for review.
Transcript content is read in place and never written anywhere. Not run in CI
(the transcripts exist only on this machine); exits 0 with a notice if none are found.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
TRANSCRIPTS = Path.home() / ".claude" / "projects" / "C--"
# New blocks that were reviewed and are intended (prefix of the command -> reason).
REVIEWED_NEW_BLOCKS_EXACT = {
    # 2026-11-12: blocked only by its last segment `env | grep -oE '^(ANTHROPIC|CLAUDE)[A-Z_]*'`
    # (each segment checked alone; pinned in test_guard.py). Exact match, so another command
    # sharing its harmless prefix is never excused.
    "claude --version 2>&1; claude auth status --json 2>/dev/null | python -c \"import json,sys;d=json.load(sys.stdin);print({k:d.get(k) for k in ('loggedIn','authMethod','apiProvider','subscriptionType','planTier')})\"; env | grep -oE '^(ANTHROPIC|CLAUDE)[A-Z_]*' | sort -u": "bare `env` piped to grep in a later segment streams every environment value; legacy only caught `env` at line end",
}
NEUTRAL_CWD = "C:/dev/_nobody_s_folder"
PROTECTED_MARKS = (
    "protected folder of ~/.claude",
    "walks through the protected folders",
)
REVIEWED_NEW_BLOCKS = {
    "env | cut -d= -f1": "bare `env` streams every environment value into the pipe; legacy only caught `env` at line end",
}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def blocked(fn: Callable[..., object], *args: str) -> bool:
    try:
        fn(*args)
    except SystemExit as e:
        return e.code == 2
    return False


def commands() -> list[tuple[str, str]]:
    seen: dict[tuple[str, str], None] = {}
    for path in sorted(TRANSCRIPTS.glob("*.jsonl")):
        for line in path.open(encoding="utf-8"):
            if '"tool_use"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for b in (d.get("message") or {}).get("content") or []:
                if (
                    isinstance(b, dict)
                    and b.get("type") == "tool_use"
                    and b.get("name") in ("Bash", "PowerShell")
                ):
                    cmd = (b.get("input") or {}).get("command") or ""
                    if cmd:
                        seen[(b["name"], cmd)] = None
    return list(seen)


def main() -> None:
    if not TRANSCRIPTS.is_dir():
        print("NOTICE: no local transcripts; corpus regression not run")
        print("FAILURES: 0")  # the line the gate reads as this module's verdict
        return
    sys.path.insert(0, str(HOOKS))
    guard = load("guard")
    corpus = commands()
    import contextlib
    import io

    new_blocks: list[str] = []
    relaxed: list[str] = []
    protected: list[str] = []
    # The transcripts do not say where a command ran. Judged from a folder that is nobody's
    # (not this repository: every `grep -r x .` of the past would count as a sweep of
    # ~/.claude), so only commands that name ~/.claude themselves meet the rules below.
    guard._ctx["cwd"] = NEUTRAL_CWD
    for tool, cmd in corpus:
        dialect = "powershell" if tool == "PowerShell" else "bash"
        guard._asks.clear()
        said = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()):
            old = blocked(guard.legacy_check, cmd)
        with contextlib.redirect_stderr(said):
            new = blocked(guard.check_command, cmd, dialect)
        if new and not old and any(mark in said.getvalue() for mark in PROTECTED_MARKS):
            # devenv-fix-m (2026-11-18): reading ~/.claude/discipline or ~/.claude/state
            # through the shell, or sweeping all of ~/.claude, is blocked on purpose. These
            # are past commands that did so; tests/test_guard.py (M-T5, M-T6) holds the rule.
            protected.append(f"{tool}: {cmd[:160]!r}")
        elif new and not old:
            reviewed = REVIEWED_NEW_BLOCKS_EXACT.get(cmd) or next(
                (r for p, r in REVIEWED_NEW_BLOCKS.items() if cmd.startswith(p)), None
            )
            if reviewed:
                print(f"  REVIEWED NEW BLOCK ({reviewed}): {cmd[:60]!r}")
            else:
                new_blocks.append(f"{tool}: {cmd[:160]!r}")
        elif old and not new:
            relaxed.append(f"{tool}: {cmd[:160]!r}")
    print(f"corpus commands: {len(corpus)}")
    print(f"newly blocked (must be 0): {len(new_blocks)}")
    for c in new_blocks[:20]:
        print("  NEW BLOCK", c)
    print(f"blocked by the protected-folder rules, on purpose: {len(protected)}")
    for c in protected[:12]:
        print("  PROTECTED", c)
    print(f"no longer blocked (review): {len(relaxed)}")
    for c in relaxed[:40]:
        print("  RELAXED", c)
    print("FAILURES:", len(new_blocks))
    sys.exit(1 if new_blocks else 0)


if __name__ == "__main__":
    main()
