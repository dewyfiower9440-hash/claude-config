"""Failure kinds that come back (P2-04).

Every failed gate run and failed CI run leaves one small row in a file of its repository:
when, where it was seen (gate or ci), the kind `ci_status.classify_failure` gave it, and a
short hash of the session. No path, no command, no output. At a session start the rows of
the last weeks are read; a trap-like kind (encoding, environment, timeout) seen in several
sessions, and again lately, is named as a candidate for the project's Gotchas.

A note for a person to act on, nothing more: no CLAUDE.md is read or written here, and the
rows decide nothing else. Test, lint and other failures are counted but never pointed
out: they are ordinary work, not a trap. The numbers below are first guesses.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KINDS = ("timeout", "encoding", "environment", "lint", "test", "other")
SOURCES = ("gate", "ci")
TRAP_KINDS = ("encoding", "environment", "timeout")
WINDOW_S = 14 * 86400.0  # rows older than this are not counted
FRESH_S = 3 * 86400.0  # nothing this recent: the matter is not pointed out any more
MIN_SESSIONS = 2
MAX_READ_BYTES = 65_536
SESSION_CHARS = 12
LINE = (
    "Gotchas 후보: 이 저장소에서 '{label}' 실패가 최근 14일에 세션 {sessions}개에서 "
    "났습니다(게이트 {gate}, CI {ci}). 원인이 같으면 프로젝트 CLAUDE.md의 Gotchas에 한 줄을 "
    "제안하세요. (자동으로 쓰지 않습니다.)"
)
LABEL = {"encoding": "인코딩", "environment": "환경·설치", "timeout": "시간 초과"}


@dataclass(frozen=True, slots=True)
class Recurrence:
    kind: str
    sessions: int
    gate: int  # rows from gate runs
    ci: int  # rows from CI runs


def valid(row: object) -> bool:
    """A row as `record` writes it; anything else in the file is skipped."""
    if not isinstance(row, dict):
        return False
    at = row.get("at")
    return (
        isinstance(at, (int, float))
        and not isinstance(at, bool)
        and row.get("source") in SOURCES
        and row.get("kind") in KINDS
        and isinstance(row.get("session"), str)
    )


def recurring(rows: list[Any], now: float) -> list[Recurrence]:
    """The trap-like kinds that failed in at least MIN_SESSIONS sessions during the window
    and at least once in the last FRESH_S seconds, in the order of TRAP_KINDS."""
    recent = [r for r in rows if valid(r) and now - WINDOW_S <= r["at"] <= now]
    out: list[Recurrence] = []
    for kind in TRAP_KINDS:
        mine = [r for r in recent if r["kind"] == kind]
        sessions = {r["session"] for r in mine}
        if len(sessions) < MIN_SESSIONS or max(r["at"] for r in mine) < now - FRESH_S:
            continue
        gate = sum(1 for r in mine if r["source"] == "gate")
        out.append(Recurrence(kind, len(sessions), gate, len(mine) - gate))
    return out


def lines(found: list[Recurrence]) -> list[str]:
    """One note line per recurring kind."""
    return [
        LINE.format(label=LABEL[r.kind], sessions=r.sessions, gate=r.gate, ci=r.ci)
        for r in found
    ]


def home() -> Path:
    """The folder of the rows. A run with an isolated approval home (a test, a benchmark)
    keeps them there: its failures are not the user's."""
    isolated = os.environ.get("CLAUDE_DISCIPLINE_HOME")
    if isolated:
        return Path(isolated) / "failures"
    return Path.home() / ".claude" / "state" / "failures"


def repo_file(root: str) -> Path:
    norm = os.path.normcase(os.path.normpath(root))
    return home() / f"{hashlib.sha256(norm.encode('utf-8')).hexdigest()[:24]}.jsonl"


def record(root: str, source: str, kind: str, session_id: str, now: float) -> bool:
    """Append one row for the repository at `root`; False when the source or kind is not
    one this module knows (nothing is written then). Raises OSError when the file cannot
    be written: the caller decides what an unwritten row means."""
    if source not in SOURCES or kind not in KINDS:
        return False
    row = {
        "at": now,
        "source": source,
        "kind": kind,
        "session": hashlib.sha256(session_id.encode("utf-8")).hexdigest()[
            :SESSION_CHARS
        ],
    }
    path = repo_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=True) + "\n")
    return True


def read(root: str) -> list[dict[str, Any]]:
    """The valid rows at the end of the repository's file (the last MAX_READ_BYTES); an
    empty list when there is no file. Raises OSError when it exists and cannot be read."""
    path = repo_file(root)
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(size - MAX_READ_BYTES, 0))
            raw = f.read()
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    # when the read started inside the file, its first line is the tail of a row: dropped
    for line in raw.decode("utf-8", errors="replace").splitlines()[
        1 if size > MAX_READ_BYTES else 0 :
    ]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if valid(row):
            rows.append(row)
    return rows
