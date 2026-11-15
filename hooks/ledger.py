"""Task ledger (P1-01): how far each approved task got, kept per repository across sessions.

discipline.py (Stop) derives a task's verification state from the evidence it already holds
and appends one line here whenever that state, or the content it was reached on, changes:

    IMPLEMENTED -> LOCALLY_VERIFIED -> CI_VERIFIED      (CLAUDE.md, section 1.6)
    BLOCKED (a gate or CI run failed), UNKNOWN (a result that could not be read)

The ledger is a record only. Nothing reads it to allow, approve or verify anything; an entry
holds allow-listed keys (FIELDS), never a scope, prompt text or file content.

Each line carries the hash of the line before it and an HMAC, so a changed, removed or
reordered line in the middle is noticed. What this does not give: the secret is a file of
the same user account, so it shows damage and naive edits, not a deliberate forgery; and
lines cut off the end leave a chain that still verifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

log = logging.getLogger("ledger")  # stderr only; never entry contents
V = 1
BLOCKED, UNKNOWN = "BLOCKED", "UNKNOWN"
IMPLEMENTED, LOCALLY_VERIFIED, CI_VERIFIED = (
    "IMPLEMENTED",
    "LOCALLY_VERIFIED",
    "CI_VERIFIED",
)
ORDER = (
    BLOCKED,
    UNKNOWN,
    IMPLEMENTED,
    LOCALLY_VERIFIED,
    CI_VERIFIED,
)  # least advanced first
TEXT_CHARS = 120
# A longer file is read from its tail: a Stop needs the last entry and the task's own
# last one. If that one is further back, the same state is recorded once more.
MAX_READ_BYTES = 65_536
LOCK_BUDGET_S = 0.5
SECRET_NAME = "mac-secret"
# name -> type of every key an entry may hold besides `prev` and `mac`
FIELDS: dict[str, type] = {
    "v": int,
    "at": float,
    "task": str,
    "approval": str,
    "state": str,
    "content": str,
    "roots": int,
    "session": str,
    "review": str,  # P1-02: none | current | running | stale | missing (a note, no gate)
}


# ---------------------------------------------------------------- pure: states
def _num(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def root_state(
    content: dict[str, Any],
    evidence: dict[str, Any] | None,
    pushes: dict[str, Any],
    ci: dict[str, Any],
) -> str:
    """The state of one project whose content changed, by the rules of discipline's
    completion check (_content_decision), without its once-per-turn notification logic."""
    evidence = evidence or {}
    outcome = evidence.get("outcome")
    digests: dict[str, str] = content.get("digests") or {}
    seen: dict[str, str] = evidence.get("digests") or {}
    if not (outcome == "passed" and all(seen.get(t) == d for t, d in digests.items())):
        if outcome == "failed":
            return BLOCKED
        return UNKNOWN if outcome == "unknown" else IMPLEMENTED
    pushed = content.get("pushed") or {}
    if content.get("ci") != "required" or not all(pushed.get(t) for t in digests):
        return LOCALLY_VERIFIED
    state = CI_VERIFIED
    for top in digests:
        push, run = pushes.get(top) or {}, ci.get(top) or {}
        pushed_at, read_at = _num(push.get("at")), _num(run.get("at"))
        if (
            run.get("sha") != push.get("sha")
            or pushed_at is None
            or read_at is None
            or read_at < pushed_at
        ):
            # this push's CI result has not been read (a time that is no number included)
            state = LOCALLY_VERIFIED
        elif run.get("status") == "failed":
            return BLOCKED
        elif run.get("status") != "verified":
            state = LOCALLY_VERIFIED  # pending or unreadable: not verified yet
    return state


def task_state(states: list[str]) -> str | None:
    """A task is as far as its least advanced project; None without any project."""
    known = [s for s in states if s in ORDER]
    return min(known, key=ORDER.index) if known else None


def content_id(digests: dict[str, str]) -> str:
    """One short value for a set of repository content hashes."""
    joined = "\n".join(f"{top}={digest}" for top, digest in sorted(digests.items()))
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()[:16]


# ---------------------------------------------------------------- pure: entries
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
            except OverflowError:
                continue
        if isinstance(value, bool) or not isinstance(value, kind):
            continue
        if kind is str:
            value = " ".join(value.split())[:TEXT_CHARS]
            if not value:
                continue
        out[key] = value
    return out


def _canonical(entry: dict[str, Any]) -> bytes:
    return json.dumps(
        entry, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def _line_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(entry)).hexdigest()[:32]


def _mac(body: dict[str, Any], secret: bytes) -> str:
    return hmac.new(secret, _canonical(body), hashlib.sha256).hexdigest()


def make_entry(
    prev: dict[str, Any] | None, fields: dict[str, Any], secret: bytes
) -> dict[str, Any] | None:
    """The next entry after `prev`, or None when `fields` name no task or no known state."""
    body = clean({**fields, "v": V})
    if body.get("state") not in ORDER or "task" not in body or "at" not in body:
        return None
    body["prev"] = _line_hash(prev) if prev else ""
    return {**body, "mac": _mac(body, secret)}


def verify(
    entries: list[dict[str, Any]], secret: bytes, anchored: bool = True
) -> str | None:
    """None when every entry is intact and follows the one before it; else what is wrong
    with which entry (counted from 1). `anchored=False`: the first entry is not the first
    of the ledger (a tail was read), so what precedes it is not checked."""
    prev: dict[str, Any] | None = None
    for i, entry in enumerate(entries, 1):
        body = {k: v for k, v in entry.items() if k != "mac"}
        mac = entry.get("mac")
        if not isinstance(mac, str) or not hmac.compare_digest(mac, _mac(body, secret)):
            return f"entry {i}: content does not match its signature"
        expected = _line_hash(prev) if prev else ""
        if (prev or anchored) and entry.get("prev") != expected:
            return f"entry {i}: does not follow the entry before it"
        prev = entry
    return None


def latest(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last entry of each task."""
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry.get("task"), str):
            out[entry["task"]] = entry
    return out


# ---------------------------------------------------------------- storage
def home() -> Path:
    """The ledger folder. A run with an isolated approval home (a test, a benchmark) keeps
    its ledger there too: its tasks are not the user's and must not reach the real ledger."""
    custom = os.environ.get("CLAUDE_LEDGER_HOME")
    if custom:
        return Path(custom)
    isolated = os.environ.get("CLAUDE_DISCIPLINE_HOME")
    if isolated:
        return Path(isolated) / "ledger"
    return Path.home() / ".claude" / "state" / "ledger"


def repo_file(root: str) -> Path:
    norm = os.path.normcase(os.path.normpath(root))
    return home() / f"{hashlib.sha256(norm.encode('utf-8')).hexdigest()[:24]}.jsonl"


def secret(folder: Path, create: bool) -> bytes | None:
    """The HMAC secret of this ledger home (made on first use when `create`)."""
    path = folder / SECRET_NAME
    for _attempt in range(2):
        try:
            value = bytes.fromhex(path.read_text(encoding="ascii").strip())
            return value if len(value) >= 16 else None
        except FileNotFoundError:
            if not create:
                return None
        except ValueError:  # not hex: unusable, and never silently replaced
            return None
        # Published complete or not at all: written beside it, then linked into place
        # (a link never replaces an existing file, so the first writer wins).
        tmp = folder / f"{SECRET_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, secrets.token_hex(32).encode("ascii"))
            finally:
                os.close(fd)
            try:
                os.link(tmp, path)
            except FileExistsError:  # another process published first: read that one
                pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:  # left behind: harmless, it is not the secret in use
                pass
    return None


def _load(path: Path) -> tuple[list[dict[str, Any]], bool, int]:
    """(entries, whether the file was read from its start, lines that are not entries)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            whole = size <= MAX_READ_BYTES
            if not whole:
                f.seek(size - MAX_READ_BYTES)
                f.readline()  # the cut line
            raw = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return [], True, 0
    entries: list[dict[str, Any]] = []
    bad = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            bad += 1
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        else:
            bad += 1
    return entries, whole, bad


def read(root: str) -> tuple[list[dict[str, Any]], str | None]:
    """(entries of the repository's ledger, what is wrong with it or None). Never raises
    for a missing or unreadable ledger."""
    path = repo_file(root)
    try:
        entries, whole, bad = _load(path)
        key = secret(path.parent, create=False)
    except OSError as e:
        return [], f"ledger not readable ({type(e).__name__})"
    if not entries and not bad:
        return [], None
    if key is None:
        return entries, "ledger secret missing or unusable"
    if bad:
        return entries, f"{bad} line(s) are not entries"
    return entries, verify(entries, key, anchored=whole)


def record(root: str, fields: dict[str, Any], now: float) -> bool:
    """Append an entry for the task in `fields` unless its last entry already has this
    state on this content. False when nothing was written (no change, lock busy, storage
    unusable); never raises for those."""
    import session_store

    path = repo_file(root)
    try:
        with session_store.locked(path, LOCK_BUDGET_S):
            key = secret(path.parent, create=True)
            if key is None:
                log.warning("ledger.secret_unusable")
                return False
            entries, _whole, _bad = _load(path)
            entry = make_entry(
                entries[-1] if entries else None, {**fields, "at": float(now)}, key
            )
            if entry is None:
                return False
            last = latest(entries).get(entry["task"])
            if last and all(
                last.get(k) == entry.get(k)
                for k in ("state", "content", "approval", "review")
            ):
                return False
            with open(path, "ab+") as f:
                # an interrupted write may have left a line without its end: start a new one
                if f.seek(0, os.SEEK_END) > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
                f.write(_canonical(entry) + b"\n")
            return True
    except (OSError, session_store.StateError) as e:
        # the ledger is a record, not a gate: the caller's work goes on without it
        log.warning("ledger.not_recorded (%s)", type(e).__name__)
        return False
