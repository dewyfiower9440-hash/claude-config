"""approvals.py (P0-04): lifecycle core, signed store, rollback/corruption handling, locking,
concurrency and the admin path. Isolated temp homes and test keys only.

    python -I tests/test_approvals_store.py
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import approvals as ap

# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
TMP = TMP_ROOT / "_tmp_store"
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir()
# Check names and values hold Korean text; a console that cannot encode it (cp1252 on
# CI) must not end the run.
sys.stdout.reconfigure(errors="backslashreplace")
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:110],
        "" if ok else f"(want {want!r})",
    )


def raises(fn: Callable[[], object], exc: type[BaseException]) -> str:
    """Name of the expected exception raised by fn; any other exception propagates."""
    try:
        fn()
    except exc as e:
        return type(e).__name__
    return "no exception"


_n = 0


def fresh() -> ap.Store:
    global _n
    _n += 1
    home = TMP / f"home{_n}"
    return ap.Store(home, TMP / f"w{_n}" / "hwm", lock_budget_s=0.3)


PLAN = (
    "---\nstatus: in-progress\ntask: widget\n---\n\n# Widget\n\n"
    "## 보안 조건\n- never touch qr-data\n\n## 변경 범위\n- src/**\n\n"
    "## 단계\n- [ ] 1. a\n- [ ] 2. b\n\n## 진행 기록\n- started\n"
)
NOW = 1_800_000_000.0
ROOT = os.path.normcase("C:/repo")


def rec_for(text: str = PLAN, **kw: object) -> dict:
    base: dict[str, Any] = {
        "task_id": "widget",
        "git_root": ROOT,
        "plan_path": "docs/plans/w.md",
        "core": ap.core_hash(text),
        "scope": ["c:/repo/src/**"],
        "session_id": "s",
        "prompt_id": "p",
        "now": NOW,
    }
    base.update(kw)
    return ap.new_record(**base)


# ---- S3/C: what the approval covers (core_hash)
h = ap.core_hash(PLAN)
for name, text in (
    ("checkbox checked", PLAN.replace("- [ ] 1. a", "- [x] 1. a")),
    ("all boxes checked", PLAN.replace("- [ ]", "- [x]")),
    (
        "box unchecked again",
        PLAN.replace("- [ ]", "- [x]").replace("- [x] 2", "- [ ] 2"),
    ),
    ("progress log appended", PLAN + "- step 1 done\n"),
    ("CRLF line endings", PLAN.replace("\n", "\r\n")),
    ("trailing spaces", PLAN.replace("# Widget", "# Widget   ")),
    (
        "status changed (lifecycle, judged separately)",
        PLAN.replace("in-progress", "blocked"),
    ),
):
    check(f"T12 core_hash unchanged: {name}", ap.core_hash(text), h)
for name, text in (
    (
        "T23 scope extended",
        PLAN.replace("- src/**\n", "- src/**\n- C:/dev/qr-data/**\n"),
    ),
    ("T24 task id changed (frontmatter)", PLAN.replace("task: widget", "task: gadget")),
    ("T25 security constraint deleted", PLAN.replace("- never touch qr-data\n", "")),
    (
        "T26 frontmatter authority key added",
        PLAN.replace("task: widget\n", "task: widget\ndata: allowed\n"),
    ),
    (
        "T26 frontmatter key changed",
        PLAN.replace("---\nstatus", "---\nowner: me\nstatus").replace(
            "owner: me", "owner: you"
        ),
    ),
    ("step wording changed", PLAN.replace("1. a", "1. a and delete tests")),
    ("new step added", PLAN.replace("- [ ] 2. b\n", "- [ ] 2. b\n- [ ] 3. c\n")),
    ("scope section smuggled after progress", PLAN + "## 변경 범위\n- C:/**\n"),
):
    check(f"core_hash changes: {name}", ap.core_hash(text) != h, True)

# ---- lifecycle core
r = rec_for()
check(
    "new record is ACTIVE with 30-day expiry",
    (r["state"], r["expires_at"] - NOW),
    ("ACTIVE", 30 * 86400),
)
check("approval ids are unique", rec_for()["approval_id"] != r["approval_id"], True)
rv = ap.transition(r, ap.REVOKED, "user_revoked", NOW)
check("ACTIVE -> REVOKED", rv["state"], "REVOKED")
check(
    "REVOKED -> ACTIVE is illegal",
    raises(lambda: ap.transition(rv, ap.ACTIVE, "x", NOW), ap.IllegalTransition),
    "IllegalTransition",
)
check(
    "REVOKED -> EXPIRED is illegal",
    raises(lambda: ap.transition(rv, ap.EXPIRED, "x", NOW), ap.IllegalTransition),
    "IllegalTransition",
)
check("check: valid", ap.check(r, root=ROOT, plan_text=PLAN, now=NOW), ap.Verdict(None))
check(
    "T4 check: expired -> revoke",
    ap.check(r, root=ROOT, plan_text=PLAN, now=NOW + 30 * 86400),
    ap.Verdict("승인 만료", revoke=True),
)
check(
    "T5 check: other task in the plan file",
    ap.check(
        r, root=ROOT, plan_text=PLAN.replace("task: widget", "task: alpha"), now=NOW
    ).reason,
    "plan_task_changed",
)
check(
    "T6 check: other git root",
    ap.check(r, root=os.path.normcase("C:/other"), plan_text=PLAN, now=NOW).reason,
    "승인 당시와 다른 git 루트",
)
check(
    "T1 check: blocked plan -> revoke",
    ap.check(r, root=ROOT, plan_text=PLAN.replace("in-progress", "blocked"), now=NOW),
    ap.Verdict("plan_status:blocked", revoke=True),
)
check(
    "T2 check: done plan -> revoke",
    ap.check(
        r, root=ROOT, plan_text=PLAN.replace("in-progress", "done"), now=NOW
    ).revoke,
    True,
)
check(
    "T11 check: core changed -> revoke",
    ap.check(r, root=ROOT, plan_text=PLAN.replace("1. a", "1. z"), now=NOW),
    ap.Verdict("plan_changed", revoke=True),
)
# ---- v1.1 step 6: a record that keeps the hash of each part says which part changed
parts_of = getattr(ap, "part_hashes", lambda text: {})
changed = getattr(ap, "changed_parts", lambda old, text: None)
ph = parts_of(PLAN)
check(
    "P-T1 the parts: frontmatter, scope, steps, the rest",
    sorted(ph),
    sorted(["frontmatter", "변경 범위", "단계", "그 밖"]),
)
check(
    "P-T2 what changes nothing in the core changes no part",
    [
        changed(ph, PLAN.replace("- [ ] 1. a", "- [x] 1. a")),
        changed(ph, PLAN + "- step 1 done\n"),
        changed(ph, PLAN.replace("in-progress", "done")),
        changed(ph, PLAN.replace("\n", "\r\n")),
    ],
    [[], [], [], []],
)
check(
    "P-T3 the changed part is named: scope / steps / frontmatter / the rest / several",
    [
        changed(ph, PLAN.replace("- src/**\n", "- src/**\n- C:/dev/qr-data/**\n")),
        changed(ph, PLAN.replace("- [ ] 2. b\n", "- [ ] 2. b\n- [ ] 3. c\n")),
        changed(ph, PLAN.replace("task: widget\n", "task: widget\ndata: allowed\n")),
        changed(ph, PLAN.replace("- never touch qr-data\n", "")),
        changed(ph, PLAN.replace("1. a", "1. z").replace("- src/**", "- lib/**")),
        changed(ph, PLAN + "## 데이터 변경 범위\n- C:/data/**\n"),
    ],
    [
        ["변경 범위"],
        ["단계"],
        ["frontmatter"],
        ["그 밖"],
        ["변경 범위", "단계"],
        ["변경 범위"],
    ],
)
check(
    "P-T4 no part hashes (an older record) or junk: nothing named",
    [changed(None, PLAN), changed("x", PLAN), changed({}, PLAN)],
    [[], [], []],
)
rp = rec_for(part_hashes=ph)
check(
    "P-T5 check: a record with part hashes names the changed part in its reason",
    [
        ap.check(
            rp,
            root=ROOT,
            plan_text=PLAN.replace("- src/**\n", "- src/**\n- C:/x/**\n"),
            now=NOW,
        ),
        ap.check(rp, root=ROOT, plan_text=PLAN.replace("1. a", "1. z"), now=NOW),
        ap.check(rp, root=ROOT, plan_text=PLAN, now=NOW),
        ap.check(r, root=ROOT, plan_text=PLAN.replace("1. a", "1. z"), now=NOW),
    ],
    [
        ap.Verdict("plan_changed(변경 범위)", revoke=True),
        ap.Verdict("plan_changed(단계)", revoke=True),
        ap.Verdict(None),
        ap.Verdict("plan_changed", revoke=True),
    ],
)
KEYP = b"k" * 32
signed_p = ap.sign_record(rp, KEYP)
signed_old = ap.sign_record(r, KEYP)
check(
    "P-T6 the part hashes are signed with the record; a record without them verifies as before",
    (
        signed_p.get("part_hashes") == ph,
        ap.record_mac_ok(signed_p, KEYP),
        ap.record_mac_ok({**signed_p, "part_hashes": {}}, KEYP),
        "part_hashes" in signed_old,
        ap.record_mac_ok(signed_old, KEYP),
    ),
    (True, True, False, False, True),
)
check(
    "check: plan file missing -> revoke",
    ap.check(r, root=ROOT, plan_text=None, now=NOW).revoke,
    True,
)
check(
    "check: revoked record never authorizes",
    ap.check(rv, root=ROOT, plan_text=PLAN, now=NOW).reason is not None,
    True,
)
r2 = rec_for()
recs = ap.issue({r["approval_id"]: r}, r2, NOW)
check(
    "T7 issue supersedes the earlier ACTIVE approval of the task",
    (
        recs[r["approval_id"]]["state"],
        recs[r["approval_id"]]["superseded_by"],
        recs[r2["approval_id"]]["supersedes"],
    ),
    ("REVOKED", r2["approval_id"], r["approval_id"]),
)
check(
    "T7 exactly one ACTIVE after re-approval",
    sum(x["state"] == "ACTIVE" for x in recs.values()),
    1,
)

# ---- store: fresh, write, read
s = fresh()
check(
    "fresh store: gen 0, no records (grants nothing)",
    (s.load().gen, s.load().records),
    (0, {}),
)
snap = s.update(lambda rs: ap.issue(rs, r, NOW))
check(
    "update writes gen 1 with the record",
    (snap.gen, list(snap.records)),
    (1, [r["approval_id"]]),
)
check(
    "load verifies and returns it",
    s.load().records[r["approval_id"]]["state"],
    "ACTIVE",
)
check(
    "witnesses W1 and W2 hold gen 1",
    [json.loads(w.read_text())["gen"] for w in s.witnesses],
    [1, 1],
)

# ---- T8 forged / replayed records
good = s.path.read_text(encoding="utf-8")
s.update(lambda rs: ap.revoke(rs, [r["approval_id"]], "user_revoked", NOW))
raw = json.loads(s.path.read_text(encoding="utf-8"))
raw["records"][r["approval_id"]]["state"] = "ACTIVE"  # flip back without the key
s.path.write_text(json.dumps(raw), encoding="utf-8")
check(
    "T8 revoked record flipped to ACTIVE -> corrupt",
    raises(s.load, ap.StoreCorrupt),
    "StoreCorrupt",
)
raw = json.loads(good)  # the old ACTIVE copy (gen 1) spliced into the current file
cur = json.loads(s.path.read_text(encoding="utf-8"))
cur["records"] = raw["records"]
s.path.write_text(json.dumps(cur), encoding="utf-8")
check(
    "T8 old ACTIVE record re-inserted -> corrupt",
    raises(s.load, ap.StoreCorrupt),
    "StoreCorrupt",
)

# ---- T14 rollback detection (snapshot restore)
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
snapshot = s.path.read_bytes()
s.update(lambda rs: ap.revoke(rs, [r["approval_id"]], "user_revoked", NOW))
s.path.write_bytes(snapshot)  # restore the gen-1 store where the approval was ACTIVE
check(
    "T14 restored older store -> rollback",
    raises(s.load, ap.StoreRollback),
    "StoreRollback",
)
s.witnesses[0].unlink()  # attacker also removes W1: W2 alone still detects it
check(
    "T14 only W2 left -> still rollback",
    raises(s.load, ap.StoreRollback),
    "StoreRollback",
)
s.witnesses[1].unlink()
check(
    "T14 both witnesses gone, session saw gen 2 -> rollback",
    raises(lambda: s.load(seen_gen=2), ap.StoreRollback),
    "StoreRollback",
)
check(
    "T14 limit: all witnesses and session memory gone -> undetectable (documented)",
    s.load(seen_gen=0).gen,
    1,
)
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
s.path.unlink()
check(
    "T14 store deleted while witnesses remain -> rollback",
    raises(s.load, ap.StoreRollback),
    "StoreRollback",
)

# ---- T9 corruption: every approval fails closed, issuance refused too
for name, mutate in (
    ("garbage", lambda st: st.path.write_text("not json", encoding="utf-8")),
    (
        "half-written JSON",
        lambda st: st.path.write_bytes(
            st.path.read_bytes()[: len(st.path.read_bytes()) // 2]
        ),
    ),
    (
        "store MAC",
        lambda st: st.path.write_text(
            st.path.read_text(encoding="utf-8").replace('"gen":1', '"gen":7'),
            encoding="utf-8",
        ),
    ),
    (
        "record MAC",
        lambda st: st.path.write_text(
            st.path.read_text(encoding="utf-8").replace("src/**", "**"),
            encoding="utf-8",
        ),
    ),
    ("key missing", lambda st: st.key_path.unlink()),
    ("witness garbage", lambda st: st.witnesses[1].write_text("x", encoding="utf-8")),
):
    st = fresh()
    st.update(lambda rs: ap.issue(rs, rec_for(), NOW))
    mutate(st)
    check(
        f"T9 {name}: load fails closed",
        raises(st.load, ap.StoreCorrupt),
        "StoreCorrupt",
    )
    check(
        f"T9 {name}: issuance refused",
        raises(
            lambda st=st: st.update(lambda rs: ap.issue(rs, rec_for(), NOW)),
            ap.StoreError,
        )
        in ("StoreCorrupt", "StoreRollback"),
        True,
    )

# ---- T18 crash mid-write: leftover temp files never replace the store
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
before = s.path.read_bytes()
# A real update() whose process dies inside _atomic_write, after the temp file is written
# and fsynced but before os.replace (the replace is the process boundary that is cut).
CRASH = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import approvals as ap
from pathlib import Path
st = ap.Store(Path(sys.argv[2]), Path(sys.argv[3]))
os.replace = lambda *a, **k: os._exit(9)
rec = ap.new_record(task_id="crash", git_root="c:\\repo", plan_path=None, core="h", scope=[], session_id=None, prompt_id=None, now=1.0)
st.update(lambda rs: ap.issue(rs, rec, 1.0))
"""
crash = subprocess.run(
    [sys.executable, "-I", "-c", CRASH, str(HOOKS), str(s.home), str(s.witnesses[1])],
    check=False,
)
check(
    "T18 writer killed inside _atomic_write before replace (exit 9)",
    crash.returncode,
    9,
)
check(
    "T18 store intact and loadable after the crash (temp file ignored)",
    (s.path.read_bytes() == before, s.load().gen, any(s.home.glob("*.tmp"))),
    (True, 1, True),
)
check("T18 lock released by the dead writer", s.update(lambda rs: rs).gen, 2)

# ---- T19 locks: a dead holder's OS lock is released; a live holder past the budget refuses
s = fresh()
HOLD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import approvals as ap
from pathlib import Path
st = ap.Store(Path(sys.argv[2]), Path(sys.argv[3]))
cm = st._lock()
cm.__enter__()
os._exit(0)  # dies while holding the lock (no cleanup runs)
"""
held = subprocess.run(
    [sys.executable, "-I", "-c", HOLD, str(HOOKS), str(s.home), str(s.witnesses[1])],
    check=False,
)
check(
    "T19 holder died with the lock: next writer proceeds",
    (held.returncode, s.update(lambda rs: ap.issue(rs, rec_for(), NOW)).gen),
    (0, 1),
)
other = ap.Store(s.home, s.witnesses[1], lock_budget_s=0.2)
with s._lock():  # a live holder (another handle) for longer than the budget
    check(
        "T19 live lock past the budget -> StoreBusy",
        raises(lambda: other.update(lambda rs: rs), ap.StoreBusy),
        "StoreBusy",
    )
check("T19 nothing written while busy", s.load().gen, 1)

# ---- review findings: unreadable inputs and failing writes are store errors, never crashes
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
s.path.write_text("[" * 200_000, encoding="utf-8")  # RecursionError in json
check(
    "deeply nested store -> StoreCorrupt (not RecursionError)",
    raises(s.load, ap.StoreCorrupt),
    "StoreCorrupt",
)
s.path.write_bytes(b"\xff\xfe" * 10)
check(
    "non-UTF-8 store -> StoreCorrupt", raises(s.load, ap.StoreCorrupt), "StoreCorrupt"
)
with open(s.path, "wb") as f:
    f.truncate(ap.MAX_FILE_BYTES + 1)
check(
    "oversized store -> StoreCorrupt", raises(s.load, ap.StoreCorrupt), "StoreCorrupt"
)
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
w2 = s.witnesses[1]
w2.unlink()
w2.parent.rmdir()
w2.parent.write_text("a file where the witness folder must be", encoding="utf-8")
check(
    "W2 parent is a file -> StoreWriteFailed (not a raw OSError)",
    raises(lambda: s.update(lambda rs: rs), ap.StoreWriteFailed),
    "StoreWriteFailed",
)
w2.parent.unlink()
check(
    "BOM before frontmatter: status still read",
    ap.plan_status("\ufeff" + PLAN),
    "in-progress",
)
check("BOM does not change core_hash", ap.core_hash("\ufeff" + PLAN), h)
s = fresh()
s.update(lambda rs: rs)
check(
    "v2 stays active without the marker once a store exists", ap.is_active(s.home), True
)
check("an empty home is not active", ap.is_active(TMP / "never-created"), False)
nul = subprocess.run(
    [
        sys.executable,
        "-I",
        "-c",
        (
            "import sys;sys.path.insert(0,sys.argv[1]);import approvals as ap;"
            "r=(sys.stdin.isatty(),sys.stdout.isatty(),ap.tty_confirm('abc'));"
            "open(sys.argv[2],'w').write(repr(r))"
        ),
        str(HOOKS),
        str(TMP / "nul_result.txt"),
    ],
    stdin=subprocess.DEVNULL,  # stdin and stdout both NUL: isatty() is True for both
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
)
nul_result = (TMP / "nul_result.txt").read_text(encoding="utf-8")
check(
    "tty_confirm refuses NUL on stdin and stdout although isatty() is True for both",
    (nul.returncode, nul_result),
    (0, "(True, True, False)" if os.name == "nt" else nul_result),
)

# ---- T20/T21 write failure: StoreWriteFailed, previous store kept
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
os.chmod(s.path, stat.S_IREAD)  # readable, but replacing it fails (Windows read-only)
check(
    "T20 write failure surfaces as StoreWriteFailed",
    raises(
        lambda: s.update(
            lambda rs: ap.revoke(rs, [r["approval_id"]], "user_revoked", NOW)
        ),
        ap.StoreWriteFailed,
    ),
    "StoreWriteFailed",
)
check(
    "T20 failed revocation left the previous store (still ACTIVE: not globally revoked)",
    s.load().records[r["approval_id"]]["state"],
    "ACTIVE",
)
os.chmod(s.path, stat.S_IREAD | stat.S_IWRITE)

# ---- T16 / T17 concurrency between processes (lock held by the test, then released)
HELPER = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import approvals as ap
from pathlib import Path
home, witness, mode, arg, flag = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5], Path(sys.argv[6])
st = ap.Store(home, witness, lock_budget_s=20.0, on_busy=lambda: flag.touch())
now = 1800000000.0
if mode == "issue":
    rec = ap.new_record(task_id=arg, git_root="c:\\repo", plan_path=None, core="h", scope=[], session_id=None, prompt_id=None, now=now)
    st.update(lambda rs: ap.issue(rs, rec, now))
else:
    st.update(lambda rs: ap.revoke(rs, [arg], "user_revoked", now))
"""


def race(st: ap.Store, jobs: list[tuple[str, str]]) -> list[int]:
    hold = st._lock()
    hold.__enter__()  # every writer must queue behind this holder
    flags = [st.home / f"busy{i}" for i in range(len(jobs))]
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-c",
                HELPER,
                str(HOOKS),
                str(st.home),
                str(st.witnesses[1]),
                m,
                a,
                str(f),
            ]
        )
        for (m, a), f in zip(jobs, flags, strict=True)
    ]
    deadline = time.monotonic() + 30
    while not all(f.exists() for f in flags) and time.monotonic() < deadline:
        time.sleep(
            0.02
        )  # wait for an observable signal: every writer is blocked on the lock
    hold.__exit__(None, None, None)
    return [p.wait(timeout=60) for p in procs]


s = fresh()
codes = race(s, [("issue", "alpha"), ("issue", "beta")])
snap = s.load()
check("T16 both writers exit 0", codes, [0, 0])
check(
    "T16 no lost update: both approvals stored",
    sorted(x["task_id"] for x in snap.records.values()),
    ["alpha", "beta"],
)
check("T16 gen advanced once per write", snap.gen, 2)

s = fresh()
s.update(lambda rs: ap.issue(rs, rec_for(git_root="c:\\repo"), NOW))
old_id = next(iter(s.load().records))
codes = race(s, [("issue", "widget"), ("revoke", old_id)])
snap = s.load()
states = sorted(x["state"] for x in snap.records.values())
check("T17 re-approval and revocation at once: both exit 0", codes, [0, 0])
check(
    "T17 exactly one ACTIVE, the old one REVOKED",
    (states, snap.records[old_id]["state"]),
    (["ACTIVE", "REVOKED"], "REVOKED"),
)

# ---- T10 / T27 admin path: interactive confirmation only, grants nothing
s = fresh()
s.update(lambda rs: ap.issue(rs, r, NOW))
s.path.write_text("garbage", encoding="utf-8")
before_key = s.key_path.read_text(encoding="utf-8")
check(
    "T27 admin reset not confirmed -> refused",
    raises(lambda: s.admin_reset(lambda code: False, "t"), ap.AdminRefused),
    "AdminRefused",
)
check(
    "T27 refused reset changed nothing",
    (s.path.read_text(encoding="utf-8"), s.key_path.read_text(encoding="utf-8")),
    ("garbage", before_key),
)
cli = subprocess.run(
    [
        sys.executable,
        "-I",
        str(HOOKS / "approvals.py"),
        "reset",
        "--home",
        str(s.home),
        "--witness",
        str(s.witnesses[1]),
    ],
    stdin=subprocess.DEVNULL,
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=False,
)
check("T27 CLI without a TTY (as a tool shell runs it) -> exit 2", cli.returncode, 2)
check("T27 CLI refusal changed nothing", s.path.read_text(encoding="utf-8"), "garbage")
gen = s.admin_reset(lambda code: True, "t")
snap = s.load()
check(
    "T10 reset: empty store, gen above every witness",
    (snap.records, gen > 1),
    ({}, True),
)
check(
    "T10 reset rotated the key",
    s.key_path.read_text(encoding="utf-8") != before_key,
    True,
)
check(
    "T10 reset quarantined the corrupt store",
    (s.home / f"{ap.STORE_NAME}.corrupt-t").read_text(encoding="utf-8"),
    "garbage",
)
check(
    "T10 after reset only a new approval is stored",
    s.update(lambda rs: ap.issue(rs, rec_for(), NOW)).gen,
    gen + 1,
)

# ---- T13 activation retires v1 and rotates the key (v1 code: see test_approvals.py)
s = fresh()
s.home.mkdir(parents=True)
(s.home / "approvals.json").write_text('{"h": {"task_id": "widget"}}', encoding="utf-8")
(s.home / "key").write_text("00" * 32, encoding="utf-8")
check(
    "T13 activation not confirmed -> refused, v1 untouched",
    (
        raises(lambda: s.admin_activate(lambda c: False, "t"), ap.AdminRefused),
        (s.home / "approvals.json").exists(),
    ),
    ("AdminRefused", True),
)
s.admin_activate(lambda c: True, "t")
check(
    "T13 activate: v1 retired, key rotated, empty v2 store, marker",
    (
        (s.home / "approvals.json").exists(),
        (s.home / "approvals.v1.retired-t.json").exists(),
        s.key_path.read_text(encoding="utf-8") != "00" * 32,
        s.load().records,
        ap.is_active(s.home),
    ),
    (False, True, True, {}, True),
)
check(
    "T13 activating twice is refused",
    raises(lambda: s.admin_activate(lambda c: True, "t2"), ap.AdminRefused),
    "AdminRefused",
)

# ---- devenv-final 23: the key and the read path
s = fresh()
s.update(lambda recs: ap.issue(recs, rec_for(), time.time()))
good_key = s.key_path.read_text(encoding="utf-8")
s.key_path.write_text("abcd", encoding="utf-8")
check(
    "S-T1 a key file holding a short key is not a key (a 2-byte key signs anything)",
    raises(s.load, ap.StoreCorrupt),
    "StoreCorrupt",
)
s.key_path.write_text(good_key, encoding="utf-8")
check(
    "S-T1 ... the key written by the store itself is long enough",
    len(s.load().records),
    1,
)

real_read = ap._read_text
denied = [0]


def flaky(path: Path) -> str:
    # Windows: a reader meets the instant another process replaces the file
    if path == s.path and denied[0] > 0:
        denied[0] -= 1
        raise PermissionError(13, "Access is denied")
    return real_read(path)


ap._read_text = flaky
try:
    denied[0] = 2
    check(
        "S-T2 a store that cannot be opened for a moment is read again, not called corrupt",
        (len(s.load().records), denied[0]),
        (1, 0),
    )
    denied[0] = 50
    check(
        "S-T2 ... one that stays unreadable is corrupt (bounded retries)",
        (raises(s.load, ap.StoreCorrupt), denied[0] >= 40),
        ("StoreCorrupt", True),
    )
finally:
    ap._read_text = real_read

shutil.rmtree(TMP, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
