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
        return
    sys.path.insert(0, str(HOOKS))
    guard = load("guard")
    corpus = commands()
    import contextlib
    import io

    new_blocks: list[str] = []
    relaxed: list[str] = []
    for tool, cmd in corpus:
        dialect = "powershell" if tool == "PowerShell" else "bash"
        with contextlib.redirect_stderr(io.StringIO()):
            old = blocked(guard.legacy_check, cmd)
            new = blocked(guard.check_command, cmd, dialect)
        if new and not old:
            reviewed = next(
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
    print(f"no longer blocked (review): {len(relaxed)}")
    for c in relaxed[:40]:
        print("  RELAXED", c)
    print("FAILURES:", len(new_blocks))
    sys.exit(1 if new_blocks else 0)


if __name__ == "__main__":
    main()
