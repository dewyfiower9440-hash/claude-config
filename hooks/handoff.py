"""Session handoff (P0-12): what a session was doing when it ended, for the next one.

`python -I handoff.py end` runs on SessionEnd: it writes a small JSON record for the
repository the session was in (branch, commit, number of changed files, the in-progress
plan and its next open step). session_context.py (SessionStart) leaves an "open session"
marker next to it and, at the next start, shows the record and any marker another session
left behind (a session that ended without a record, or is still running).

A handoff carries state only. It never holds an approval, a scope, prompt text or file
content, and nothing reads it to grant anything: the keys are an allow-list (FIELDS).
SessionEnd cannot block and has little time, so every git call is bounded, every failure
ends with exit code 0, and a missing record is an expected case, not an error.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

V = 1
MAX_AGE_S = 14 * 86400
GIT_TIMEOUT_S = 1
END_BUDGET_S = 3.0  # all git calls of one SessionEnd together (the hook's limit is 5)
# SessionEnd hooks share a 1.5 s default budget (stretched to the hook's own timeout), so
# the plan lookup opens only the most recently changed plan files.
END_PLANS_READ = 5
SWEEP_RESERVE_S = 1.5  # the state sweep starts only this long before END_BUDGET_S
USAGE_START_S = 2.0  # the usage measurement (up to 1 s) starts only before this
STEP_CHARS = 200
VALUE_CHARS = 160  # any other text value when shown
SECTION_CHARS = 600
# name -> type of every key a record may hold; anything else is dropped on write and read
FIELDS: dict[str, type] = {
    "v": int,
    "at": float,
    "session_id": str,
    "reason": str,
    "git_root": str,
    "branch": str,
    "head": str,
    "dirty": int,
    "plan": str,
    "next_step": str,
    "open_steps": int,
}


# ---------------------------------------------------------------- pure core
def one_line(text: object, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


def build_handoff(
    payload: dict[str, Any],
    git: dict[str, Any],
    plan: dict[str, Any] | None,
    now: float,
) -> dict[str, Any]:
    """The record for a session that ends now. Only allow-listed keys, each of its type;
    a value that is missing or of another type is left out."""
    raw: dict[str, Any] = {
        "v": V,
        "at": float(now),
        "session_id": payload.get("session_id"),
        "reason": payload.get("reason"),
        "git_root": git.get("root"),
        "branch": git.get("branch"),
        "head": git.get("head"),
        "dirty": git.get("dirty"),
    }
    if plan:
        raw.update(
            plan=plan.get("path"),
            next_step=plan.get("next_step"),
            open_steps=plan.get("open_steps"),
        )
    return clean(raw)


def masked(text: str) -> str:
    """`text` with token-shaped parts masked (P1-09, hooks/redact.py)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    from redact import redact

    return str(redact(text))


def clean(raw: object) -> dict[str, Any]:
    """`raw` reduced to the allow-list: known keys of the right type, text on one line."""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, kind in FIELDS.items():
        value = raw.get(key)
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            try:
                value = float(value)
            except OverflowError:  # an absurd number is no timestamp
                continue
        if isinstance(value, bool) or not isinstance(value, kind):
            continue
        if kind is str:
            # masked before it is cut: a token cut in two would no longer look like one
            limit = STEP_CHARS if key == "next_step" else VALUE_CHARS * 2
            # bounded first (with room for a token that straddles the limit to stay whole)
            value = masked(" ".join(value[: limit * 20].split())[: limit + 512])[:limit]
            if not value:
                continue
        out[key] = value
    return out


def stamp(at: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
    except (OverflowError, OSError, ValueError):
        return "?"


def same_commit(a: str, b: str) -> bool:
    """Two abbreviated hashes of one commit (compared over the shorter one's length)."""
    n = min(len(a), len(b))
    return n >= 4 and a[:n] == b[:n]


def restore_lines(
    record: dict[str, Any] | None,
    other_open: dict[str, Any] | None,
    branch: str,
    head: str,
) -> list[str]:
    """The lines a new session is shown: a marker another session left, then the last
    session's record compared with the repository as it is now. At most SECTION_CHARS.
    The record's heading itself says that it restores no approval, so the record is never
    shown without that; the optional detail lines are dropped first when space is short."""
    lines: list[str] = []
    if other_open:
        lines.append(
            f"참고: 이전 세션({stamp(float(other_open.get('at', 0)))} 시작)이 종료 기록 없이 "
            "끝났거나 아직 실행 중입니다. 하던 작업은 git 상태와 계획 파일로 다시 확인하세요."
        )
    if not record:
        return lines
    heading = (
        f"## 지난 세션 종료 기록 ({stamp(record['at'])}, 사유: "
        f"{one_line(record.get('reason', '?'), 30)}) - 참고용이며 승인을 복원하지 않습니다"
    )
    then = (
        f"그때: 브랜치 `{one_line(record.get('branch', '?'), 40)}`, "
        f"커밋 {one_line(record.get('head', '?'), 12)}, 변경 파일 {record.get('dirty', '?')}개"
    )
    changed = []
    # A detached HEAD is spelled differently by the two hooks: not a branch to compare.
    named = record.get("branch") not in (None, "HEAD") and not branch.startswith("HEAD")
    if named and record["branch"] != branch:
        changed.append("브랜치가 달라졌습니다")
    if record.get("head") and head and not same_commit(record["head"], head):
        changed.append("커밋이 달라졌습니다")
    then += f" -> 지금은 {', '.join(changed)}" if changed else ""
    optional = []
    if record.get("plan"):
        optional.append(
            f"계획: `{one_line(record['plan'], 100)}` (미체크 {record.get('open_steps', '?')}개)"
        )
    if record.get("next_step"):
        optional.append(f"다음 단계였던 것: {one_line(record['next_step'], 120)}")
    optional.append(
        "기준은 현재 git 상태와 계획 파일입니다. 작업을 이어가려면 사용자가 task:<id>를 "
        "지정하거나 새로 승인해야 합니다."
    )
    room = SECTION_CHARS - sum(len(x) + 1 for x in lines)
    block = [heading, then]
    if sum(len(x) + 1 for x in block) > room:
        return lines  # not even the heading fits: no record rather than half of one
    room -= sum(len(x) + 1 for x in block)
    for line in optional:
        if len(line) + 1 <= room:
            block.append(line)
            room -= len(line) + 1
    return lines + block


# ---------------------------------------------------------------- storage
def home() -> Path:
    custom = os.environ.get("CLAUDE_HANDOFF_HOME")
    return Path(custom) if custom else Path.home() / ".claude" / "state" / "handoff"


def key(root: str) -> str:
    norm = os.path.normcase(os.path.normpath(root))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        # missing, unreadable or damaged (also absurdly nested): there is no record
        return None


def write_handoff(root: str, record: dict[str, Any]) -> None:
    _write_json(home() / f"{key(root)}.json", clean(record))


def read_handoff(root: str, now: float) -> dict[str, Any] | None:
    """The stored record if it is this version's, complete enough and not too old."""
    record = clean(_read_json(home() / f"{key(root)}.json"))
    if record.get("v") != V or "at" not in record:
        return None
    if not 0 <= now - record["at"] <= MAX_AGE_S:
        return None
    return record


def mark_open(root: str, session_id: str, now: float) -> None:
    _write_json(
        home() / f"{key(root)}.open.json", {"session_id": session_id, "at": float(now)}
    )


def other_open(root: str, session_id: str, now: float) -> dict[str, Any] | None:
    """The marker another session left for this repository (a stale one is removed)."""
    path = home() / f"{key(root)}.open.json"
    marker = _read_json(path)
    if not isinstance(marker, dict):
        return None
    at = marker.get("at")
    try:
        at = (
            float(at)
            if isinstance(at, int | float) and not isinstance(at, bool)
            else None
        )
    except OverflowError:
        at = None
    if at is None or now - at > MAX_AGE_S:
        try:
            path.unlink()
        except OSError:  # cannot be removed: ignored, it is only a hint
            pass
        return None
    if marker.get("session_id") == session_id:
        return None
    return {"session_id": one_line(marker.get("session_id", ""), 80), "at": at}


def clear_open(root: str, session_id: str) -> None:
    path = home() / f"{key(root)}.open.json"
    marker = _read_json(path)
    if isinstance(marker, dict) and marker.get("session_id") == session_id:
        try:
            path.unlink()
        except OSError:  # left behind: the next start reports it as possibly running
            pass


# ---------------------------------------------------------------- SessionEnd
_deadline = float("inf")


def _git(cwd: str, *args: str) -> str | None:
    left = _deadline - time.monotonic()
    if left <= 0.05:
        return None  # out of time: the field is left out
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(GIT_TIMEOUT_S, left),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):  # git missing or slow: field left out
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def read_plan(root: str) -> dict[str, Any] | None:
    """Path, next open step and open-step count of the repository's main plan."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session_context

    # SessionEnd hooks share 1.5 s: only the few most recently changed plans are opened
    main, _summaries = session_context.read_plans(Path(root), limit=END_PLANS_READ)
    if not main:
        return None
    open_steps = [s for s in main.checklist if session_context.UNCHECKED.match(s)]
    return {
        "path": main.path,
        "next_step": open_steps[0] if open_steps else None,
        "open_steps": len(open_steps),
    }


def end(data: dict[str, Any], now: float) -> None:
    global _deadline
    _deadline = time.monotonic() + END_BUDGET_S
    cwd = str(data.get("cwd") or "")
    root = _git(cwd, "rev-parse", "--show-toplevel") if cwd else None
    if not root:
        return  # not in a repository (or git did not answer): nothing to hand over
    status = _git(root, "status", "--porcelain")
    git = {
        "root": root,
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "head": (_git(root, "rev-parse", "HEAD") or "")[:12] or None,
        "dirty": len(status.splitlines()) if status is not None else None,
    }
    try:
        plan = read_plan(root) if time.monotonic() < _deadline else None
    except (
        OSError,
        ValueError,
        ImportError,
        AttributeError,
        TypeError,
        RecursionError,
    ) as e:
        # the plan is optional context; the record is still written
        print(f"handoff: plan not read ({type(e).__name__})", file=sys.stderr)
        plan = None
    write_handoff(root, build_handoff(data, git, plan, now))
    if isinstance(data.get("session_id"), str):
        clear_open(root, data["session_id"])


def main(argv: list[str]) -> int:
    if argv[:1] != ["end"]:
        print("usage: handoff.py end  (SessionEnd payload on stdin)", file=sys.stderr)
        return 0
    started = time.monotonic()
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if isinstance(data, dict):
            end(data, time.time())
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ImportError,
        RecursionError,
        OverflowError,
        subprocess.SubprocessError,
    ) as e:
        # SessionEnd cannot block: never fail the session's end
        print(f"handoff: not written ({type(e).__name__})", file=sys.stderr)
    try:  # P0-13: once per session end, drop state files of long-silent sessions
        if time.monotonic() - started < END_BUDGET_S - SWEEP_RESERVE_S:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import state_gc

            state_gc.sweep_default(time.time())
    except (OSError, ValueError, ImportError, OverflowError) as e:
        print(f"handoff: state sweep skipped ({type(e).__name__})", file=sys.stderr)
    try:  # P1-07: last, and only with time to spare: how much the growing folders hold
        if time.monotonic() - started < USAGE_START_S:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import usage

            if usage.enabled():
                usage.write(usage.snapshot(time.time()))
    except (OSError, ValueError, ImportError, OverflowError, TypeError) as e:
        print(f"handoff: usage not measured ({type(e).__name__})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
