"""Removal of session state files nobody has used for a while (P0-13).

discipline.py keeps `<session>.json` and `<session>.lock` per session in the temp folder's
`claude-discipline` and never deleted them. `sweep` removes those whose state file has not
been written for TTL days (the hook rewrites it on every run, so an old modification time
means the session has been silent that long). It is called once per session end by
handoff.py, bounded in files and time.

Only names of exactly the forms `<id>.json` and `<id>.lock` are ever removed. Recovery
copies (`*.json.corrupt-*`, `*.json.recovered-*`), anything else, folders and links are
left alone. A lock in use cannot be removed on Windows; that failure is counted and skipped.

A session that wakes up after its state was removed starts like a new session: what it had
recorded as unverified is gone and Stop no longer asks for it. The default TTL is therefore
longer than the time a transcript can still be resumed (cleanupPeriodDays, 30 by default);
a shorter CLAUDE_STATE_TTL_DAYS is the user's explicit choice of that loss.

The folder is only ever an absolute path whose last part is `claude-discipline`: with any
other setting nothing is swept, because `package.json` or `uv.lock` have the same name form.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path

DEFAULT_TTL_DAYS = 35  # above the transcript retention (30 days by default)
MAX_TTL_DAYS = 365
LIMIT = 2000
BUDGET_S = 1.0  # listing and removing together
FOLDER_NAME = "claude-discipline"
NAME = re.compile(r"^([A-Za-z0-9_-]+)\.(json|lock)$", re.ASCII)


# ---------------------------------------------------------------- pure core
def ttl_days(raw: object) -> int:
    """The TTL in days from CLAUDE_STATE_TTL_DAYS: a whole number from 1 to MAX_TTL_DAYS,
    anything else is the default."""
    if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
        days = int(raw.strip())
        if days >= 1:
            return min(days, MAX_TTL_DAYS)
    return DEFAULT_TTL_DAYS


def stale_entries(
    entries: list[tuple[str, float]], now: float, ttl_s: float
) -> list[str]:
    """Names to remove among (name, modification time) pairs: a state file older than the
    TTL with its lock, and a lock without a state file that is itself older. A lock whose
    state file stays, stays."""
    known: dict[str, dict[str, float]] = {}
    for name, mtime in entries:
        m = NAME.match(name)
        if m:
            known.setdefault(m.group(1), {})[m.group(2)] = mtime
    out: list[str] = []
    for session in sorted(known):
        files = known[session]
        decider = files.get("json", files.get("lock", now))
        if now - decider > ttl_s:
            out += [f"{session}.{ext}" for ext in ("json", "lock") if ext in files]
    return out


# ---------------------------------------------------------------- I/O
def default_dir() -> Path | None:
    """Where discipline.py keeps its state (same lookup as its temp_root), or the folder
    CLAUDE_STATE_GC_DIR names. None when that setting is not an absolute path ending in
    FOLDER_NAME: a mistyped folder must not lose its `*.json` and `*.lock` files."""
    custom = os.environ.get("CLAUDE_STATE_GC_DIR")
    if custom:
        path = Path(custom)
        return path if path.is_absolute() and path.name == FOLDER_NAME else None
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value) / FOLDER_NAME
    import tempfile

    return Path(tempfile.gettempdir()) / FOLDER_NAME


def sweep(
    folder: Path,
    now: float,
    ttl_s: float,
    limit: int = LIMIT,
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, int]:
    """(files removed, files that could not be removed). Never raises for a missing or
    unreadable folder; stops at `limit` removals or after `budget_s` seconds. When the time
    runs out while the folder is still being listed, nothing is removed: in half a list a
    lock could look like one without a state file."""
    started = clock()
    entries: list[tuple[str, float]] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if clock() - started > budget_s:
                    return 0, 0
                try:
                    if entry.is_file(follow_symlinks=False):
                        entries.append(
                            (entry.name, entry.stat(follow_symlinks=False).st_mtime)
                        )
                except OSError:  # vanished or unreadable meanwhile: not a candidate
                    continue
    except OSError:  # no such folder, not a folder, not listable: nothing to do
        return 0, 0
    removed = failed = 0
    listed = dict(entries)
    for name in stale_entries(entries, now, ttl_s):
        if removed >= limit or clock() - started > budget_s:
            break
        target = os.path.join(folder, name)
        try:
            # A session that woke up since the folder was listed has written its state
            # again: what is removed is what is still as old as it was when listed.
            if os.stat(target).st_mtime > listed.get(name, 0.0):
                continue
            os.unlink(target)
            removed += 1
        except FileNotFoundError:  # another sweep took it
            continue
        except OSError:  # in use (a lock held right now) or not permitted
            failed += 1
    return removed, failed


# devenv-final 14: discipline.py leaves `<session>` in this folder when a session gets its
# first state, outside the temp folder: a state file that is missing while its marker is
# there was deleted, and the session's unverified edits must not vanish with it.
MARKER = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)


def alive_dir(env: Mapping[str, str] | None = None) -> Path:
    """The marker folder, under the approval home (so a test's isolated home isolates the
    markers too)."""
    home = (os.environ if env is None else env).get("CLAUDE_DISCIPLINE_HOME")
    return (Path(home) if home else Path.home() / ".claude" / "discipline") / "alive"


def sweep_markers(
    markers: Path,
    folder: Path,
    now: float,
    ttl_s: float,
    limit: int = LIMIT,
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Markers removed: those older than the TTL whose state file in `folder` is gone (the
    sweep above removed it, or the session never came back). A marker whose state file is
    still there stays whatever its age. Never raises for a missing folder."""
    started = clock()
    removed = 0
    try:
        with os.scandir(markers) as it:
            for entry in it:
                if removed >= limit or clock() - started > budget_s:
                    break
                try:
                    if (
                        not MARKER.match(entry.name)
                        or not entry.is_file(follow_symlinks=False)
                        or now - entry.stat(follow_symlinks=False).st_mtime <= ttl_s
                        or os.path.lexists(os.path.join(folder, entry.name + ".json"))
                    ):
                        continue
                    os.unlink(entry.path)
                    removed += 1
                except OSError:  # vanished, in use or not permitted: left for next time
                    continue
    except OSError:  # no such folder: nothing to do
        return removed
    return removed


def sweep_default(now: float) -> tuple[int, int]:
    folder = default_dir()
    if folder is None:
        return 0, 0
    days = ttl_days(os.environ.get("CLAUDE_STATE_TTL_DAYS"))
    result = sweep(folder, now, days * 86400.0)
    sweep_markers(alive_dir(), folder, now, days * 86400.0)
    sweep_stamps(folder.parent / NOTIFY_FOLDER, now, days * 86400.0)
    return result


# notify.py leaves `<session>.start` per session in the temp folder's `claude-notify` (when
# the turn began) and never removed them.
NOTIFY_FOLDER = "claude-notify"
STAMP = re.compile(r"^[A-Za-z0-9_-]+\.start$", re.ASCII)


def sweep_stamps(
    folder: Path,
    now: float,
    ttl_s: float,
    limit: int = LIMIT,
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Turn-start stamps older than the TTL, removed. Only names of the form
    `<id>.start` in a folder named NOTIFY_FOLDER; never raises for a missing folder."""
    if folder.name != NOTIFY_FOLDER:
        return 0
    started = clock()
    removed = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if removed >= limit or clock() - started > budget_s:
                    break
                try:
                    if (
                        STAMP.match(entry.name)
                        and entry.is_file(follow_symlinks=False)
                        and now - entry.stat(follow_symlinks=False).st_mtime > ttl_s
                    ):
                        os.unlink(entry.path)
                        removed += 1
                except OSError:  # vanished or in use: left for next time
                    continue
    except OSError:  # no such folder: nothing to do
        return removed
    return removed
