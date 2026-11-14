"""Counts from the P0-06 observation log (run by the user: `python -I hooks/authz_report.py`).

discipline.py appends one line to `authz-observe.jsonl` in the approval home for every edit
that enforce mode would have denied. This prints how many there were, by day, reason, path
class, extension and tool - counts only, so it can be pasted into a report. The approval
home is not readable by Claude's tools; the user runs this in their own terminal.

The log undercounts: in warn mode an edit refused by the five-file rule is not logged,
although enforce would refuse it too. Read the totals as a lower bound.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

FIELDS = ("reason", "class", "ext", "tool")


def default_file() -> Path:
    home = os.environ.get("CLAUDE_DISCIPLINE_HOME")
    base = Path(home) if home else Path.home() / ".claude" / "discipline"
    return base / "authz-observe.jsonl"


def summarize(lines: list[str]) -> tuple[dict[str, Counter[str]], int, int]:
    """(counts per field plus "day", rows counted, lines skipped as unreadable)."""
    counts: dict[str, Counter[str]] = {k: Counter() for k in ("day", *FIELDS)}
    total = skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            at = float(row["at"])
            if not math.isfinite(at):
                raise ValueError("not a time")
            day = time.strftime("%Y-%m-%d", time.localtime(at))
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            skipped += 1  # not a row this tool wrote (or a damaged one)
            continue
        total += 1
        counts["day"][day] += 1
        for k in FIELDS:
            counts[k][str(row.get(k) or "(none)")] += 1
    return counts, total, skipped


def main(argv: list[str]) -> int:
    # Values come from file names; a console that cannot encode one must not end the run.
    sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="authz observation counts")
    parser.add_argument("--file", type=Path, default=None)
    args = parser.parse_args(argv)
    path = args.file or default_file()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as e:
        print(f"cannot read the observation log: {type(e).__name__}", file=sys.stderr)
        return 1
    counts, total, skipped = summarize(lines)
    print(f"total {total}")
    print(f"skipped {skipped}")
    for field in ("day", *FIELDS):
        print(f"[{field}]")
        for name, n in sorted(counts[field].items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
