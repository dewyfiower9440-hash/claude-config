"""Plan approval instances (v2): a pure lifecycle core and a small signed file store.

Authority comes only from an ACTIVE, signed approval instance in the store. An instance moves
one way: ACTIVE -> REVOKED | EXPIRED; resuming work always issues a new approval_id. A store
that is missing records grants nothing, so loss or corruption fails closed.

v2 is dormant until the admin `activate` command (interactive TTY + one-time code) creates
the `v2-active` marker; until then discipline.py keeps its v1 behaviour and never writes here.

    python -I hooks/approvals.py activate|reset [--home DIR] [--witness FILE]
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import random
import re
import secrets
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

log = logging.getLogger("approvals")  # stderr only; never secrets
V = 2
ACTIVE, REVOKED, EXPIRED = "ACTIVE", "REVOKED", "EXPIRED"
MAX_AGE_S = 30 * 86400
LOCK_BUDGET_S = 2.0  # initial prior; re-tune from measured contention
MAX_RECORDS = 200
MAX_FILE_BYTES = (
    4_000_000  # a store of MAX_RECORDS is ~200 KB; anything larger is not ours
)
STORE_NAME = "approvals.v2.json"
LOCK_NAME = "approvals.v2.lock"
HWM_NAME = "hwm"
MARKER_NAME = "v2-active"
KEY_NAME = "key"
V1_NAME = "approvals.json"
V2_FILES = (
    MARKER_NAME,
    STORE_NAME,
    HWM_NAME,
)  # discipline.V2_FILES must match (tested)
RECORD_FIELDS = (
    "approval_id",
    "task_id",
    "git_root",
    "plan_path",
    "core_hash",
    "scope",
    "issued_at",
    "expires_at",
    "state",
    "state_reason",
    "state_at",
    "supersedes",
    "superseded_by",
    "session_id",
    "prompt_id",
)


# ---------------------------------------------------------------- errors
class ApprovalError(Exception):
    """Base error of the approval store."""


class IllegalTransition(ApprovalError):
    """An approval state change other than ACTIVE -> REVOKED | EXPIRED."""


class StoreError(ApprovalError):
    """The store cannot be trusted or written: every approval fails closed."""


class StoreCorrupt(StoreError):
    """Unparseable store or witness, a bad signature, or a missing key."""


class StoreRollback(StoreCorrupt):
    """The store is older than a high-water witness (a restored snapshot)."""


class StoreBusy(StoreError):
    """The store lock could not be taken within the budget."""


class StoreWriteFailed(StoreError):
    """Writing the store or a witness failed."""


class AdminRefused(ApprovalError):
    """An admin command was not confirmed interactively."""


# ---------------------------------------------------------------- pure core: plans
FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
STATUS_LINE = re.compile(r"^status\s*:", re.IGNORECASE)
PROGRESS = re.compile(
    r"^##\s*(진행 기록|Progress)\b.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)
CHECKED = re.compile(r"^(\s*[-*]\s)\[[xX]\]", re.MULTILINE)


def _split(text: str) -> tuple[str, str]:
    text = text.replace("\r\n", "\n").removeprefix("﻿")
    m = FRONTMATTER.match(text)
    return (m.group(1), text[m.end() :]) if m else ("", text)


def plan_status(text: str) -> str | None:
    """The plan's status: the one `status:` line of its frontmatter. Two status lines say
    nothing (the last one used to win: `status: done` could be overruled by a line added
    under it)."""
    fm, _body = _split(text)
    found = re.findall(r"^status\s*:\s*['\"]?([A-Za-z_-]+)['\"]?\s*$", fm, re.MULTILINE)
    lines = [ln for ln in fm.splitlines() if STATUS_LINE.match(ln.strip())]
    return found[0].lower() if len(found) == 1 and len(lines) == 1 else None


def plan_task(text: str) -> str | None:
    """The plan's task id: the `task:` line of its frontmatter, else the one in the plan's
    head (before its first `## ` heading: where the earlier plan template put it). A
    `task:` line further down (a quoted example, another plan pasted in) names nothing,
    and the frontmatter's wins over any other."""
    fm, body = _split(text)
    head = re.split(r"^##\s", body, maxsplit=1, flags=re.MULTILINE)[0]
    for part in (fm, head):
        m = re.search(r"^task:\s*(\S+)\s*$", part, re.MULTILINE)
        if m:
            return m.group(1)
    return None


def normalize_core(text: str) -> str:
    """What an approval covers: every frontmatter key except `status`, and the whole body
    except checkbox state and the progress-log section."""
    fm, body = _split(text)
    keys = [
        ln.rstrip()
        for ln in fm.splitlines()
        if ln.strip() and not STATUS_LINE.match(ln.strip())
    ]
    body = PROGRESS.sub("", CHECKED.sub(r"\1[ ]", body))
    lines = [ln.rstrip() for ln in body.strip().splitlines()]
    return "---\n" + "\n".join(keys) + "\n---\n" + "\n".join(lines)


def core_hash(text: str) -> str:
    return hashlib.sha256(normalize_core(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- pure core: records
def _canon(obj: object) -> bytes:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def mac(key: bytes, kind: str, body: object) -> str:
    return hmac.new(
        key, _canon({"v": V, "kind": kind, "body": body}), hashlib.sha256
    ).hexdigest()


def sign_record(rec: dict[str, Any], key: bytes) -> dict[str, Any]:
    body = {k: rec.get(k) for k in RECORD_FIELDS}
    return {**body, "mac": mac(key, "approval", body)}


def record_mac_ok(rec: dict[str, Any], key: bytes) -> bool:
    body = {k: rec.get(k) for k in RECORD_FIELDS}
    return hmac.compare_digest(str(rec.get("mac") or ""), mac(key, "approval", body))


def new_record(
    *,
    task_id: str,
    git_root: str,
    plan_path: str | None,
    core: str,
    scope: list[str],
    session_id: str | None,
    prompt_id: str | None,
    now: float,
) -> dict[str, Any]:
    return {
        "approval_id": secrets.token_hex(16),
        "task_id": task_id,
        "git_root": git_root,
        "plan_path": plan_path,
        "core_hash": core,
        "scope": list(scope),
        "issued_at": now,
        "expires_at": now + MAX_AGE_S,
        "state": ACTIVE,
        "state_reason": None,
        "state_at": now,
        "supersedes": None,
        "superseded_by": None,
        "session_id": session_id,
        "prompt_id": prompt_id,
    }


def transition(
    rec: dict[str, Any], state: str, reason: str, now: float
) -> dict[str, Any]:
    if rec.get("state") != ACTIVE or state not in (REVOKED, EXPIRED):
        raise IllegalTransition(
            f"approval {rec.get('approval_id')}: {rec.get('state')} -> {state} is not allowed"
        )
    return {**rec, "state": state, "state_reason": reason, "state_at": now}


def effective_state(rec: dict[str, Any], now: float) -> str:
    if rec.get("state") == ACTIVE and now >= float(rec.get("expires_at") or 0):
        return EXPIRED
    return str(rec.get("state"))


def issue(
    records: dict[str, dict[str, Any]], new: dict[str, Any], now: float
) -> dict[str, dict[str, Any]]:
    """Add `new`; every earlier ACTIVE approval of the same task and root is superseded."""
    out = dict(records)
    for rid, rec in records.items():
        if (
            rec.get("state") == ACTIVE
            and rec.get("task_id") == new["task_id"]
            and rec.get("git_root") == new["git_root"]
        ):
            out[rid] = {
                **transition(rec, REVOKED, "superseded", now),
                "superseded_by": new["approval_id"],
            }
            new = {**new, "supersedes": rid}
    out[new["approval_id"]] = new
    return out


def revoke(
    records: dict[str, dict[str, Any]], ids: list[str], reason: str, now: float
) -> dict[str, dict[str, Any]]:
    out = dict(records)
    for rid in ids:
        rec = out.get(rid)
        if rec and rec.get("state") == ACTIVE:
            out[rid] = transition(rec, REVOKED, reason, now)
    return out


class Verdict(NamedTuple):  # NamedTuple: dataclasses+inspect cost ~35 ms per hook run
    """Why an approval does not authorize now (`reason` None = it does), and whether the
    observed cause must be recorded permanently (`revoke`)."""

    reason: str | None
    revoke: bool = False


def check(
    rec: dict[str, Any] | None, *, root: str, plan_text: str | None, now: float
) -> Verdict:
    if rec is None:
        return Verdict("승인 기록이 저장소에 없음")
    state = effective_state(rec, now)
    if state == EXPIRED and rec.get("state") == ACTIVE:
        return Verdict("승인 만료", revoke=True)
    if state != ACTIVE:
        return Verdict(f"승인 {state}({rec.get('state_reason')})")
    if os.path.normcase(str(rec.get("git_root") or "")) != os.path.normcase(root):
        return Verdict("승인 당시와 다른 git 루트")
    if plan_text is None:
        return Verdict("plan_missing", revoke=True)
    if plan_task(plan_text) != rec.get("task_id"):
        return Verdict("plan_task_changed", revoke=True)
    status = plan_status(plan_text)
    if status != "in-progress":
        return Verdict(f"plan_status:{status}", revoke=True)
    if core_hash(plan_text) != rec.get("core_hash"):
        return Verdict("plan_changed", revoke=True)
    return Verdict(None)


def trim(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep at most MAX_RECORDS: ACTIVE ones first, then the newest by state change."""
    if len(records) <= MAX_RECORDS:
        return records
    ranked = sorted(
        records.items(),
        key=lambda kv: (
            kv[1].get("state") == ACTIVE,
            float(kv[1].get("state_at") or 0),
        ),
    )
    return dict(ranked[-MAX_RECORDS:])


# ---------------------------------------------------------------- store adapter
class Snapshot(NamedTuple):
    gen: int
    records: dict[str, dict[str, Any]]


def default_home() -> Path:
    return Path(
        os.environ.get("CLAUDE_DISCIPLINE_HOME")
        or (Path.home() / ".claude" / "discipline")
    )


def default_witness() -> Path:
    env = os.environ.get("CLAUDE_DISCIPLINE_WITNESS")
    if env:
        return Path(env)
    home = os.environ.get("CLAUDE_DISCIPLINE_HOME")
    if home:  # an isolated home (tests) never shares the real witness
        return Path(home + "-witness") / HWM_NAME
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "claude-discipline" / HWM_NAME


def is_active(home: Path, witness: Path | None = None) -> bool:
    """v2 once activated: the marker, or any v2 file (deleting the marker alone never falls
    back to v1), or the witness kept outside the home (emptying the home does not either)."""
    return any((home / n).exists() for n in V2_FILES) or bool(
        witness and witness.exists()
    )


READ_ATTEMPTS = 4  # Windows: the file is being replaced by another process right now
MIN_KEY_BYTES = 16  # the store writes 32; a shorter "key" would sign anything


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path, what: str) -> Any:
    """Parsed JSON; FileNotFoundError passes through, anything else unreadable is corrupt.
    A file that cannot be opened for a moment (PermissionError while a writer replaces it)
    is read again a few times before that."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise StoreCorrupt(f"{what} 크기 초과: {path.name}")
        for attempt in range(READ_ATTEMPTS):
            try:
                return json.loads(_read_text(path))
            except PermissionError:
                if attempt == READ_ATTEMPTS - 1:
                    raise
                time.sleep(0.02 + random.random() * 0.03)
    except FileNotFoundError:
        raise
    except (OSError, ValueError, RecursionError) as e:
        raise StoreCorrupt(f"{what} 손상: {path.name} ({type(e).__name__})") from e
    raise StoreCorrupt(
        f"{what} 손상: {path.name}"
    )  # not reached: the loop returns or raises


def _try_lock(fd: int) -> bool:
    """Non-blocking exclusive OS lock; released by the OS if the holder dies."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:  # held by another process (or not lockable): not acquired
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


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(3):  # Windows: a reader holding the target briefly
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
        raise StoreWriteFailed(f"{path.name}: {e}") from e


class Store:
    def __init__(
        self,
        home: Path,
        witness: Path,
        *,
        lock_budget_s: float = LOCK_BUDGET_S,
        on_busy: Callable[[], None] | None = None,
    ) -> None:
        self.home = home
        self.path = home / STORE_NAME
        self.lock_path = home / LOCK_NAME
        self.witnesses = (home / HWM_NAME, witness)
        self.key_path = home / KEY_NAME
        self.lock_budget_s = lock_budget_s
        self.on_busy = on_busy

    # -- key and witnesses
    def _key(self) -> bytes | None:
        try:
            key = bytes.fromhex(self.key_path.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            raise StoreCorrupt(f"서명 키를 읽을 수 없음: {e}") from e
        if len(key) < MIN_KEY_BYTES:
            raise StoreCorrupt(f"서명 키가 너무 짧음({len(key)}바이트)")
        return key

    def _witness_gen(self, path: Path, key: bytes | None) -> int:
        try:
            raw = _read_json(path, "고수위 증인")
        except FileNotFoundError:
            return 0
        if key is None:
            raise StoreCorrupt("증인은 있는데 서명 키가 없음")
        gen = raw.get("gen") if isinstance(raw, dict) else None
        if not isinstance(gen, int) or not hmac.compare_digest(
            str(raw.get("mac") or ""), mac(key, "hwm", {"gen": gen})
        ):
            raise StoreCorrupt(f"고수위 증인 서명 불일치: {path.name}")
        return gen

    # -- read
    def load(self, seen_gen: int = 0) -> Snapshot:
        key = self._key()
        high = max([seen_gen] + [self._witness_gen(w, key) for w in self.witnesses])
        try:
            raw = _read_json(self.path, "저장소")
        except FileNotFoundError:
            if high > 0:
                raise StoreRollback(
                    f"저장소가 없는데 증인 gen={high} (삭제·롤백)"
                ) from None
            return Snapshot(0, {})
        if key is None:
            raise StoreCorrupt("저장소는 있는데 서명 키가 없음")
        if not isinstance(raw, dict) or raw.get("v") != V:
            raise StoreCorrupt("저장소 형식 오류")
        gen, records = raw.get("gen"), raw.get("records")
        if not isinstance(gen, int) or not isinstance(records, dict):
            raise StoreCorrupt("저장소 형식 오류")
        if not hmac.compare_digest(
            str(raw.get("mac") or ""),
            mac(key, "store", {"gen": gen, "records": records}),
        ):
            raise StoreCorrupt("저장소 서명 불일치")
        for rid, rec in records.items():
            if (
                not isinstance(rec, dict)
                or rec.get("approval_id") != rid
                or not record_mac_ok(rec, key)
            ):
                raise StoreCorrupt(f"승인 레코드 서명 불일치: {rid}")
        if gen < high:
            raise StoreRollback(f"저장소 gen={gen} < 증인 gen={high} (스냅샷 복원)")
        return Snapshot(gen, records)

    # -- write
    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Exclusive OS lock on a persistent lock file: a dead holder releases it, so there is
        no stale-lock recovery (and no recovery race)."""
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            raise StoreWriteFailed(f"잠금 파일을 열 수 없음: {e}") from e
        try:
            deadline = time.monotonic() + self.lock_budget_s
            while not _try_lock(fd):
                if self.on_busy:
                    self.on_busy()
                if time.monotonic() >= deadline:
                    raise StoreBusy(
                        f"승인 저장소 잠금을 {self.lock_budget_s}s 안에 얻지 못함"
                    )
                time.sleep(0.02 + random.random() * 0.03)
            try:
                yield
            finally:
                try:
                    _unlock(fd)
                except (
                    OSError
                ):  # the write already happened; os.close below releases it
                    log.warning("approvals.unlock_failed", exc_info=True)
        finally:
            os.close(fd)  # closing also releases the lock

    def _write(self, gen: int, records: dict[str, dict[str, Any]], key: bytes) -> None:
        body = {"gen": gen, "records": records}
        _atomic_write(
            self.path, _canon({"v": V, **body, "mac": mac(key, "store", body)})
        )
        for w in self.witnesses:  # after the store: a witness never exceeds it
            _atomic_write(w, _canon({"gen": gen, "mac": mac(key, "hwm", {"gen": gen})}))

    def update(
        self,
        fn: Callable[[dict[str, dict[str, Any]]], dict[str, dict[str, Any]]],
        seen_gen: int = 0,
    ) -> Snapshot:
        with self._lock():
            snap = self.load(seen_gen)
            key = self._key()
            if (
                key is None
            ):  # load() succeeded, so this is a fresh store: create its key
                key = secrets.token_bytes(32)
                _atomic_write(self.key_path, key.hex().encode("utf-8"))
            records = {
                rid: sign_record(rec, key)
                for rid, rec in trim(fn(dict(snap.records))).items()
            }
            gen = snap.gen + 1
            self._write(gen, records, key)
            return Snapshot(gen, records)

    # -- admin (interactive confirmation only)
    def _raw_gen(self, path: Path) -> int:
        try:
            raw = _read_json(path, "gen")
        except (FileNotFoundError, StoreCorrupt):  # unreadable: contributes nothing
            return 0
        gen = raw.get("gen") if isinstance(raw, dict) else None
        return gen if isinstance(gen, int) else 0

    def _fresh_start(self, stamp: str) -> int:
        gen = 1 + max(self._raw_gen(p) for p in (self.path, *self.witnesses))
        for p in (self.path, *self.witnesses):
            if p.exists():
                try:
                    os.replace(p, p.with_name(f"{p.name}.corrupt-{stamp}"))
                except OSError as e:
                    raise StoreWriteFailed(f"격리 실패: {p.name}: {e}") from e
        key = secrets.token_bytes(32)
        _atomic_write(self.key_path, key.hex().encode("utf-8"))
        self._write(gen, {}, key)
        return gen

    def admin_activate(self, confirm: Callable[[str], bool], stamp: str) -> int:
        if is_active(self.home):
            raise AdminRefused("이미 활성화됨")
        if not confirm(secrets.token_hex(3)):
            raise AdminRefused("확인되지 않음")
        with self._lock():
            v1 = self.home / V1_NAME
            if v1.exists():
                try:
                    os.replace(v1, self.home / f"approvals.v1.retired-{stamp}.json")
                except OSError as e:
                    raise StoreWriteFailed(f"v1 퇴역 실패: {e}") from e
            gen = self._fresh_start(stamp)  # new key: no v1 record verifies again
            _atomic_write(self.home / MARKER_NAME, f"activated {stamp}\n".encode())
        return gen

    def admin_reset(self, confirm: Callable[[str], bool], stamp: str) -> int:
        """Quarantine the store and witnesses, rotate the key, start empty. Grants nothing."""
        if not confirm(secrets.token_hex(3)):
            raise AdminRefused("확인되지 않음")
        with self._lock():
            return self._fresh_start(stamp)


def tty_confirm(code: str) -> bool:
    """True only when a person at an interactive terminal types the one-time code back.
    Not a security boundary against code running as the same user (it could call the Store
    methods directly); it keeps tool shells, relayed text and prompts from triggering it."""
    if not (_is_console(sys.stdin) and _is_console(sys.stdout)):
        return False
    print(f"확인 코드: {code}")
    try:
        typed = input("위 코드를 그대로 입력하면 진행합니다: ")
    except EOFError:  # no person typing: refuse
        return False
    return typed.strip() == code


def _is_console(stream: Any) -> bool:
    """isatty() is True for the NUL device on Windows; require a real console handle."""
    if not stream.isatty():
        return False
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt

    mode = ctypes.c_uint32()
    handle = msvcrt.get_osfhandle(stream.fileno())
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def main(argv: list[str]) -> int:
    import argparse

    if isinstance(
        sys.stdout, io.TextIOWrapper
    ):  # cp949 pipes/consoles: messages are Korean
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="approval store admin (interactive only)"
    )
    parser.add_argument("command", choices=("activate", "reset"))
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--witness", type=Path, default=None)
    args = parser.parse_args(argv)
    store = Store(args.home or default_home(), args.witness or default_witness())
    stamp = time.strftime("%Y%m%dT%H%M%S")
    action = store.admin_activate if args.command == "activate" else store.admin_reset
    try:
        gen = action(tty_confirm, stamp)
    except AdminRefused as e:
        print(
            f"거부: {e} (대화형 터미널에서 직접 실행하고 확인 코드를 입력해야 합니다)"
        )
        return 2
    except StoreError as e:
        print(f"실패: {e}")
        return 1
    print(f"완료: gen={gen}. 승인은 발급되지 않았습니다. 계획마다 다시 승인받으세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
