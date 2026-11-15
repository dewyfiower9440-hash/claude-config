"""Disk use of the folders that grow (P1-07): measured at session end, warned about at start.

handoff.py (SessionEnd) calls `snapshot` and `write`; session_context.py (SessionStart)
calls `read` and `warnings`. Only numbers are kept: how many files and bytes each kind of
folder holds and how much disk is free. No file name, no path, no content. Nothing here
deletes anything: what removes old data is described in hooks/retention.md.

The limits are initial priors (re-tune them from what the record shows over time).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

V = 1
BUDGET_S = 1.0  # all folders together, at session end
MAX_AGE_S = 14 * 86400  # an older measurement says nothing about now
MB = 1_000_000
# what each limit is compared with: (folder, field, divisor, limit, unit, label)
LIMITS: tuple[tuple[str, str, int, float, str, str], ...] = (
    ("projects", "bytes", MB, 2000, "MB", "대화 기록(projects)"),
    ("file_history", "bytes", MB, 500, "MB", "파일 이력(file-history)"),
    ("sessions", "files", 1, 100_000, "개", "세션 상태 파일"),
    ("observations", "bytes", MB, 50, "MB", "승인 저장소 폴더(hook 관측 기록 포함)"),
)
FREE_GB_LIMIT = 20.0
# measured in this order: the two that grow without a ceiling first
FOLDERS = ("sessions", "observations", "projects", "file_history", "state")


# ---------------------------------------------------------------- pure
def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            number = float(value)
        except OverflowError:  # an absurdly large integer: not a measurement
            return None
        return number if math.isfinite(number) else None
    return None


def clean(raw: object) -> dict[str, Any] | None:
    """A snapshot reduced to its allow-listed shape, or None when it is not one."""
    if not isinstance(raw, dict) or _number(raw.get("at")) is None:
        return None
    folders: dict[str, dict[str, Any]] = {}
    given = raw.get("folders")
    for name in FOLDERS:
        entry = given.get(name) if isinstance(given, dict) else None
        if not isinstance(entry, dict):
            continue
        files, size = _number(entry.get("files")), _number(entry.get("bytes"))
        if files is None or size is None:
            continue
        folders[name] = {
            "files": int(files),
            "bytes": int(size),
            "partial": entry.get("partial") is True,
        }
    return {
        "v": V,
        "at": float(raw["at"]),
        "free_gb": _number(raw.get("free_gb")),
        "folders": folders,
    }


def warnings(snapshot: object, now: float) -> list[str]:
    """One line for every limit the last measurement is over; none when the measurement
    is missing, unreadable, older than MAX_AGE_S or from the future."""
    snap = clean(snapshot)
    if snap is None or not 0 <= now - snap["at"] <= MAX_AGE_S:
        return []
    out: list[str] = []
    for name, field, divisor, limit, unit, label in LIMITS:
        entry = snap["folders"].get(name)
        if not entry:
            continue
        value = entry[field] / divisor
        if value > limit:
            more = (
                "(측정이 시간 안에 끝나지 않아 그 이상입니다) "
                if entry["partial"]
                else ""
            )
            out.append(
                f"경고: {label}이(가) {value:.0f}{unit}입니다. {more}"
                f"기준({limit:.0f}{unit})을 넘었습니다. 자동으로 지우지 않습니다. "
                "`hooks/retention.md`를 보세요."
            )
    free = snap["free_gb"]
    if free is not None and free < FREE_GB_LIMIT:
        out.append(
            f"경고: 디스크 여유 공간이 {free:.1f}GB로 기준({FREE_GB_LIMIT:.0f}GB)보다 적습니다."
        )
    return out


# ---------------------------------------------------------------- I/O
def measure(
    folder: Path, budget_s: float, clock: Callable[[], float] = time.monotonic
) -> dict[str, Any]:
    """Files and bytes below `folder` (links and junctions are not followed). Out of time:
    what was counted so far, marked partial. A folder that is not there counts as empty."""
    started = clock()
    files = size = 0
    partial = False
    stack = [str(folder)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if clock() - started > budget_s:
                        partial = True
                        stack.clear()
                        break
                    try:
                        if entry.is_symlink() or entry.is_junction():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:  # vanished or unreadable meanwhile: not counted
                        continue
        except OSError:  # missing, not a folder, not listable
            continue
    return {"files": files, "bytes": size, "partial": partial}


def enabled() -> bool:
    """Whether a session end measures at all. A run with an isolated handoff home (a test)
    does so only when it also names the folders: it must not walk the user's own."""
    return bool(os.environ.get("CLAUDE_USAGE_CLAUDE_HOME")) or not os.environ.get(
        "CLAUDE_HANDOFF_HOME"
    )


def targets() -> dict[str, Path | None]:
    import state_gc

    claude = Path(os.environ.get("CLAUDE_USAGE_CLAUDE_HOME") or Path.home() / ".claude")
    observations = os.environ.get("CLAUDE_DISCIPLINE_HOME")
    return {
        "projects": claude / "projects",
        "file_history": claude / "file-history",
        "state": claude / "state",
        "sessions": state_gc.default_dir(),
        "observations": Path(observations) if observations else claude / "discipline",
    }


def snapshot(
    now: float,
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
    places: dict[str, Path | None] | None = None,
) -> dict[str, Any]:
    """Every folder gets an equal share of what is left of the budget, so one large
    folder ends as `partial` instead of leaving the ones after it unmeasured."""
    started = clock()
    folders: dict[str, Any] = {}
    places = targets() if places is None else places
    todo = [name for name in FOLDERS if places.get(name) is not None]
    for i, name in enumerate(todo):
        left = budget_s - (clock() - started)
        share = max(left, 0.0) / (len(todo) - i)
        place = places[name]
        if place is not None:
            folders[name] = measure(place, share, clock)
    try:
        free_gb: float | None = round(shutil.disk_usage(Path.home()).free / 1e9, 1)
    except OSError:
        free_gb = None
    return {"v": V, "at": float(now), "free_gb": free_gb, "folders": folders}


def usage_file() -> Path:
    custom = os.environ.get("CLAUDE_USAGE_FILE")
    return Path(custom) if custom else Path.home() / ".claude" / "state" / "usage.json"


def write(snap: object) -> None:
    data = clean(snap)
    if data is None:
        return
    path = usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp, path)


def read() -> dict[str, Any] | None:
    try:
        return clean(json.loads(usage_file().read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError, OverflowError):
        return None  # missing, unreadable or damaged: there is no measurement
