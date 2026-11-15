"""What is actually used, and what the hooks have observed (P4-01). Read-only.

    python -I hooks/usage_report.py calls   [--since YYYY-MM-DD] [--projects DIR] [--config DIR]
    python -I hooks/usage_report.py observe [--since YYYY-MM-DD] [--home DIR] [--failures DIR]

`calls` reads the session transcripts (`~/.claude/projects/<folder>/*.jsonl`) once and counts
the Skill, subagent and MCP calls by name. Installed skills and subagents that were never
called are listed with 0, so that "not used" shows. What subagents called themselves is in
transcripts of their own (`<folder>/<session>/subagents/*.jsonl`) and is counted apart. Only
the name of a tool and the name of the skill or subagent it was given are looked at: no
prompt, command, path or tool input is read into the report, and no folder or file name is
printed. The project folders of the out-of-scope repositories (quant-research, qr-data,
qr-backup) are skipped.

`--since` selects sessions by when their transcript last changed: a long session that is
still in use is counted from its start. A resumed session carries earlier blocks again;
a block id is counted once and the repeats are reported.

`observe` sums up the observation files the hooks append to in the approval home (risk
score, test gap, review, output size) and the failure kinds under `state/failures`. The
approval home is not read by Claude's tools: the user runs `observe` in their own terminal
(as with authz_report.py, which covers the fifth file, authz-observe.jsonl).

Counts say whether something is used, not whether it is any good. The transcript format is
not a documented contract: "lines read" and "tool_use blocks" are printed so that a format
change (blocks no longer found) can be told from "nothing was called".

Exit codes: 0 done (also when there is nothing to count), 2 bad input. Not a hook.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RISK_AT = 8  # hooks/risk.py ADVICE_AT
OUTPUT_AT = 8000  # hooks/discipline.py OUTPUT_ADVICE_DEFAULT
DEFAULT_DAYS = 30
MAX_READ_BYTES = 4_194_304  # of one observation file, from its end
PRINTABLE_BELOW = 1e15  # a larger number in a record is damage, not a count
# the projects folders of C:\dev\quant-research, qr-data and qr-backup (and below them)
OUT_OF_SCOPE = re.compile(
    r"(^|-)(quant-research|qr-data|qr-backup)(-|$)", re.IGNORECASE
)
NAME = re.compile(r"[A-Za-z0-9:_.-]{1,60}")
OTHER = "(other)"
OBSERVED = ("risk", "test-gap", "review", "output-size")
KINDS = (*OBSERVED, "failures")


@dataclass(slots=True)
class Calls:
    skills: Counter[str] = field(default_factory=Counter)
    agents: Counter[str] = field(default_factory=Counter)
    servers: Counter[str] = field(default_factory=Counter)
    mcp_tools: Counter[str] = field(default_factory=Counter)
    tools: Counter[str] = field(default_factory=Counter)
    lines: int = 0
    tool_uses: int = 0
    repeated: int = 0  # blocks whose id was counted before (a resumed session)
    bad: int = 0  # lines with the text "tool_use" that are no JSON object

    def add(self, other: Calls) -> None:
        for name in ("skills", "agents", "servers", "mcp_tools", "tools"):
            getattr(self, name).update(getattr(other, name))
        self.lines += other.lines
        self.tool_uses += other.tool_uses
        self.repeated += other.repeated
        self.bad += other.bad


def clean_name(value: object) -> str:
    """A name that came out of a conversation, kept only when it looks like a name."""
    return value if isinstance(value, str) and NAME.fullmatch(value) else OTHER


def count_calls(lines: Iterable[str], seen: set[str] | None = None) -> Calls:
    """The tool calls of one transcript, by name. `seen` holds the block ids counted so
    far in this run: a block with one of them is a repeat, not a call."""
    found = Calls()
    for line in lines:
        if not line.strip():
            continue
        found.lines += 1
        if '"tool_use"' not in line:
            continue  # most lines; also keeps prompts and results from being parsed at all
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            found.bad += 1
            continue
        if not isinstance(row, dict):
            found.bad += 1
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        # only the blocks of the message itself: a tool_use quoted in a result is no call
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            block_id = block.get("id")
            if seen is not None and isinstance(block_id, str) and block_id:
                if block_id in seen:
                    found.repeated += 1
                    continue
                seen.add(block_id)
            found.tool_uses += 1
            given = block.get("input")
            given = given if isinstance(given, dict) else {}
            if name.startswith("mcp__"):
                parts = name.split("__")
                server = clean_name(parts[1]) if len(parts) > 2 else OTHER
                found.servers[server] += 1
                found.mcp_tools[f"{server}:{clean_name(parts[-1])}"] += 1
                continue
            found.tools[clean_name(name)] += 1
            if name == "Skill":
                found.skills[clean_name(given.get("skill"))] += 1
            elif name in ("Agent", "Task"):
                kind = given.get("subagent_type")
                found.agents[
                    "(default)" if kind in (None, "") else clean_name(kind)
                ] += 1
    return found


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile by nearest rank (a value that occurs); None without values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(q / 100 * len(ordered)), 1)
    return ordered[min(rank, len(ordered)) - 1]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _whole(value: float | None) -> str:
    if value is None or not math.isfinite(value) or abs(value) >= PRINTABLE_BELOW:
        return "?"
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def _by_count(counts: Counter[str]) -> str:
    return ", ".join(
        f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )


def summarize_rows(kind: str, rows: Sequence[Any], since: float) -> list[str] | None:
    """One line for the rows of one observation kind from `since` on; None for a kind this
    report does not know. A row without a usable time is counted as skipped."""
    if kind not in KINDS:
        return None
    kept: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        at = _number(row.get("at")) if isinstance(row, dict) else None
        if at is None:
            skipped += 1
        elif at >= since:
            kept.append(row)
    head = f"rows {len(kept)} (skipped {skipped})"
    if not kept:
        return [head]
    if kind == "risk":
        scores = [s for s in (_number(r.get("score")) for r in kept) if s is not None]
        per_kind: Counter[str] = Counter()
        for r in kept:
            kinds = r.get("kinds")
            for name, n in (kinds if isinstance(kinds, dict) else {}).items():
                count = _number(n)
                if count is not None and 0 <= count < PRINTABLE_BELOW:
                    per_kind[clean_name(name)] += int(count)
        return [
            (
                f"{head}: score min {_whole(min(scores, default=None))}, "
                f"median {_whole(percentile(scores, 50))}, p90 {_whole(percentile(scores, 90))}, "
                f"max {_whole(max(scores, default=None))}; "
                f"at or above {RISK_AT}: {sum(1 for s in scores if s >= RISK_AT)}; "
                f"advised {sum(1 for r in kept if r.get('advised') is True)}; "
                f"files by kind {_by_count(per_kind)}"
            )
        ]
    if kind == "test-gap":
        return [
            f"{head}: files {_whole(sum(_number(r.get('files')) or 0 for r in kept))}"
        ]
    if kind == "review":
        states = Counter(clean_name(r.get("state")) for r in kept)
        return [
            f"{head}: {_by_count(states)}; gate on {sum(1 for r in kept if r.get('gate') is True)}"
        ]
    if kind == "output-size":
        chars = [c for c in (_number(r.get("chars")) for r in kept) if c is not None]
        who = Counter("main" if r.get("who") == "main" else "subagent" for r in kept)
        return [
            (
                f"{head}: test runs {sum(1 for r in kept if r.get('test') is True)}; "
                f"main {who['main']}, subagent {who['subagent']}; "
                f"chars median {_whole(percentile(chars, 50))}, max {_whole(max(chars, default=None))}; "
                f"at or above {OUTPUT_AT}: {sum(1 for c in chars if c >= OUTPUT_AT)}"
            )
        ]
    pairs = Counter(
        f"{clean_name(r.get('source'))}/{clean_name(r.get('kind'))}" for r in kept
    )
    sessions = {r.get("session") for r in kept if isinstance(r.get("session"), str)}
    return [f"{head}, sessions {len(sessions)}: {_by_count(pairs)}"]


def read_rows(path: Path) -> tuple[list[Any], bool]:
    """(the lines at the end of an observation file, parsed; whether the file is longer
    than what was read). A line that is no JSON comes back as None (it is then counted as
    skipped). Raises OSError when the file is missing or cannot be read."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(size - MAX_READ_BYTES, 0)
        if start:
            f.seek(start - 1)
            whole_first_line = f.read(1) == b"\n"  # the read now stands at `start`
        else:
            f.seek(0)
            whole_first_line = True
        raw = f.read(MAX_READ_BYTES).decode("utf-8", errors="replace")
    lines = raw.splitlines()[0 if whole_first_line else 1 :]
    rows: list[Any] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, RecursionError):
            rows.append(None)
    return rows, start > 0


def installed(config: Path) -> tuple[list[str], list[str]]:
    """(own skills, subagents) defined under the configuration folder; `skills/synced` is
    the account's, not ours."""
    skills = sorted(
        p.parent.name
        for p in config.glob("skills/*/SKILL.md")
        if p.parent.name != "synced"
    )
    agents = sorted(p.stem for p in config.glob("agents/*.md"))
    return skills, agents


def _table(title: str, counts: Counter[str], always: Sequence[str] = ()) -> list[str]:
    merged = Counter({name: 0 for name in always})
    merged.update(counts)
    rows = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    return (
        [f"[{title}]", *(f"  {name} {n}" for name, n in rows)]
        if rows
        else [f"[{title}]", "  (none)"]
    )


def _count_file(path: Path, seen: set[str]) -> Calls | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return count_calls(f, seen)
    except OSError:
        return None


def calls_report(projects: Path, config: Path, since: float) -> list[str]:
    total, inside = Calls(), Calls()
    seen: set[str] = set()
    sessions = with_skill = with_agent = with_mcp = skipped = unreadable = sub_files = 0
    try:
        folders = sorted(p for p in projects.iterdir() if p.is_dir())
        missing = False
    except OSError:
        folders, missing = [], True
    for folder in folders:
        if OUT_OF_SCOPE.search(folder.name):
            skipped += 1
            continue
        try:
            recent = sorted(
                p
                for p in folder.glob("*.jsonl")
                if p.is_file() and p.stat().st_mtime >= since
            )
        except OSError:
            unreadable += 1
            continue
        for path in recent:
            one = _count_file(path, seen)
            if one is None:
                unreadable += 1
                continue
            sessions += 1
            with_skill += bool(one.skills)
            with_agent += bool(one.agents)
            with_mcp += bool(one.servers)
            total.add(one)
            # what the session's subagents called themselves
            try:
                own = sorted((folder / path.stem / "subagents").glob("*.jsonl"))
            except OSError:
                own = []
            for sub in own:
                counted = _count_file(sub, seen) if sub.is_file() else None
                if counted is None:
                    unreadable += 1
                    continue
                sub_files += 1
                inside.add(counted)
    skills, agents = installed(config)
    return [
        f"sessions changed since {time.strftime('%Y-%m-%d', time.localtime(since))}"
        + (" (no transcripts folder was found)" if missing else ""),
        (
            f"sessions {sessions} (with a Skill call {with_skill}, a subagent call {with_agent}, "
            f"an MCP call {with_mcp})"
        ),
        (
            f"lines read {total.lines}, tool_use blocks {total.tool_uses}, repeated blocks "
            f"{total.repeated + inside.repeated}, candidate lines that are no JSON "
            f"{total.bad + inside.bad}, files that could not be read {unreadable}"
        ),
        f"project folders skipped (out of scope): {skipped}",
        *_table("skills", total.skills, skills),
        *_table("subagents", total.agents, agents),
        *_table("mcp servers", total.servers),
        *_table("mcp tools", total.mcp_tools),
        *_table("tools", total.tools),
        f"subagent transcripts {sub_files} (lines read {inside.lines}, tool_use blocks {inside.tool_uses})",
        *_table("skills, inside subagents", inside.skills),
        *_table("mcp servers, inside subagents", inside.servers),
        *_table("mcp tools, inside subagents", inside.mcp_tools),
    ]


def _one_kind(kind: str, rows: Sequence[Any], cut: bool, since: float) -> str:
    line = (summarize_rows(kind, rows, since) or ["?"])[0]
    note = (
        f" (only the last {MAX_READ_BYTES // 1_048_576} MB of a longer file were read)"
        if cut
        else ""
    )
    return f"[{kind}] {line}{note}"


def observe_report(home: Path, failures: Path, since: float) -> list[str]:
    out = [f"rows since {time.strftime('%Y-%m-%d', time.localtime(since))}"]
    for kind in OBSERVED:
        try:
            rows, cut = read_rows(home / f"{kind}-observe.jsonl")
        except FileNotFoundError:
            out.append(f"[{kind}] no file")
            continue
        except OSError as e:
            out.append(f"[{kind}] cannot be read ({type(e).__name__})")
            continue
        out.append(_one_kind(kind, rows, cut, since))
    try:
        files = sorted(failures.glob("*.jsonl")) if failures.is_dir() else []
    except OSError:
        files = []
    if not files:
        out.append("[failures] no file")
        return out
    every: list[Any] = []
    any_cut = False
    for path in files:
        try:
            rows, cut = read_rows(path)
        except OSError:
            every.append(None)
            continue
        every.extend(rows)
        any_cut = any_cut or cut
    out.append(_one_kind("failures", every, any_cut, since))
    return out


def _day(text: str) -> float:
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d"))
    except (ValueError, OverflowError, OSError) as e:
        raise argparse.ArgumentTypeError(
            "a date as YYYY-MM-DD that this system's clock knows"
        ) from e


def _home_dir() -> Path | None:
    try:
        return Path.home()
    except RuntimeError:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        prog="usage_report", description="Usage and observation counts."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    calls = commands.add_parser(
        "calls", help="skill, subagent and MCP calls in the transcripts"
    )
    calls.add_argument("--since", type=_day)
    calls.add_argument("--projects", type=Path)
    calls.add_argument(
        "--config", type=Path, default=Path(__file__).resolve().parent.parent
    )
    observe = commands.add_parser(
        "observe", help="what the hooks' observation files hold"
    )
    observe.add_argument("--since", type=_day)
    observe.add_argument("--home", type=Path)
    observe.add_argument("--failures", type=Path)
    args = parser.parse_args(argv)
    since = args.since if args.since is not None else time.time() - DEFAULT_DAYS * 86400
    user = _home_dir()
    if args.command == "calls":
        projects = args.projects or (user / ".claude" / "projects" if user else None)
        if projects is None:
            print("usage_report: no home folder; give --projects", file=sys.stderr)
            return 2
        lines = calls_report(projects, args.config, since)
    else:
        isolated = os.environ.get("CLAUDE_DISCIPLINE_HOME")
        base = (
            Path(isolated)
            if isolated
            else (user / ".claude" / "discipline" if user else None)
        )
        home = args.home or base
        failures = args.failures or (
            Path(isolated) / "failures"
            if isolated
            else (user / ".claude" / "state" / "failures" if user else None)
        )
        if home is None or failures is None:
            print(
                "usage_report: no home folder; give --home and --failures",
                file=sys.stderr,
            )
            return 2
        lines = observe_report(home, failures, since)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
