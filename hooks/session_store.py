"""Per-session discipline state file: one exclusive OS lock for a whole hook run, atomic
replace on save, and damage that is preserved and reported instead of silently reset.

Every hook process of a session (main Claude and its subagents share the session_id) takes
the same lock, so runs are serialized and no update is lost. The OS releases the lock when a
holder dies. Standard library only (hooks run with python -I on every tool call).
"""

from __future__ import annotations

import json
import logging
import os
import random
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger("session_store")  # stderr only; never state contents
LOCK_BUDGET_S = (
    20.0  # initial prior: below the 30 s pre/prompt hook timeout in settings.json
)
MAX_FILE_BYTES = 8_000_000


class StateError(Exception):
    """The session state cannot be used safely; callers fail closed."""


class StateBusy(StateError):
    """Another hook of this session held the lock past the budget."""


class StateCorrupt(StateError):
    """The state file was unreadable; it has been preserved under `preserved`."""

    def __init__(self, message: str, preserved: Path | None) -> None:
        super().__init__(message)
        self.preserved = preserved


class StateWriteFailed(StateError):
    """The new state could not be written; the previous file is unchanged."""


def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:  # held by another process: not acquired
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # held by another process: not acquired
        return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def locked(path: Path, budget_s: float = LOCK_BUDGET_S) -> Iterator[None]:
    """Hold `<path stem>.lock` exclusively for the block (raises StateBusy past the budget)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise StateBusy(f"상태 잠금 파일을 열 수 없음: {e}") from e
    try:
        deadline = time.monotonic() + budget_s
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise StateBusy(f"세션 상태 잠금을 {budget_s:g}s 안에 얻지 못함")
            time.sleep(0.02 + random.random() * 0.03)
        try:
            yield
        finally:
            try:
                _unlock(fd)
            except OSError:  # the run already finished; os.close below releases it
                log.warning("session_store.unlock_failed", exc_info=True)
    finally:
        os.close(fd)


def load(path: Path) -> dict[str, Any] | None:
    """The saved state, None if there is none yet. An unreadable file is moved aside (kept
    for inspection) and reported as StateCorrupt; it is never silently replaced."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("state file too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("state is not an object")
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, RecursionError) as e:
        raise StateCorrupt(
            f"세션 추적 상태 손상({type(e).__name__})", preserve(path)
        ) from e


def preserve(path: Path, label: str = "corrupt") -> Path | None:
    """Move a state file aside for inspection (`<name>.<label>-<ts>-<rand>`); None if it
    could not be moved."""
    kept = path.with_name(
        f"{path.name}.{label}-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    )
    try:
        os.replace(path, kept)
    except OSError:  # could not move it: report it in place
        return None
    return kept


def keep_copy(path: Path, raw: bytes, label: str) -> Path | None:
    """Write `raw` (the state file's bytes) next to it as `<name>.<label>-<ts>-<rand>`,
    durably; the original is not touched. None if the copy could not be written."""
    kept = path.with_name(
        f"{path.name}.{label}-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    )
    try:
        fd = os.open(kept, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        log.warning("session_store.keep_copy_failed", exc_info=True)
        return None
    return kept


def save(path: Path, state: dict[str, Any]) -> None:
    """Write the whole state atomically (temp file, fsync, replace)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(state))
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(
            3
        ):  # Windows: a reader (notify) may hold the target briefly
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.02 + random.random() * 0.03)
    except OSError as e:
        try:
            tmp.unlink()
        except (
            OSError
        ):  # the temp file may not exist; the write failure is what is raised
            pass
        raise StateWriteFailed(f"세션 상태 저장 실패: {e}") from e


def note_missed(path: Path, why: str) -> None:
    """Record, without the lock, that an event could not be recorded; the next locked run
    turns it into a reset marker. One file per note, published complete by a rename, so a
    concurrent take never sees a partial note and never deletes one it has not read."""
    name = f"{time.time_ns()}.{os.getpid()}.{secrets.token_hex(4)}"
    tmp = path.with_name(f"{path.stem}.note-tmp.{name}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            line = json.dumps({"at": time.time(), "why": str(why)}) + "\n"
            os.write(fd, line.encode("utf-8", errors="replace"))
        finally:
            os.close(fd)
        os.replace(tmp, path.with_name(f"{path.stem}.missed.{name}"))
    except (OSError, ValueError):  # nothing more can be recorded; caller fails closed
        log.warning("session_store.note_missed_failed", exc_info=True)


def take_missed(path: Path) -> list[str]:
    """Reasons of events missed since the last locked run (call only while locked). Each
    note is removed only after it was read; one that cannot be removed yet is read again
    next time and still counts now (as a generic reason), so nothing is lost."""
    try:
        # `<stem>.missed` is the earlier single-file format; read it the same way.
        notes = sorted(path.parent.glob(f"{path.stem}.missed*"))
    except OSError:
        return ["놓친 이벤트 기록 목록을 읽을 수 없음"]
    out: list[str] = []
    for p in notes:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            continue
        except OSError:
            out.append("놓친 이벤트 기록을 읽을 수 없음")
            continue
        try:
            p.unlink()
        except FileNotFoundError:
            pass  # already gone: what was read above is still reported
        except OSError:  # kept: its reasons are reported when it can be removed
            log.warning("session_store.take_missed_unlink_failed", exc_info=True)
            out.append("놓친 이벤트 기록을 아직 지울 수 없음(다음 실행에서 다시 읽음)")
            continue
        for ln in lines or [""]:
            try:
                out.append(str(json.loads(ln).get("why")))
            except (ValueError, AttributeError):
                out.append("손상된 놓친 이벤트 기록")
    return out
