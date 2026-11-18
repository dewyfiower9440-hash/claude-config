"""S (session state integrity): concurrent hooks, crashes while saving, corrupt state, lock
contention, duplicate events. Isolated TMPDIR / approval home; nothing real is touched.

    python -I tests/test_session_store.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

sys.stdout.reconfigure(
    errors="backslashreplace"
)  # Korean values on a cp1252 console (CI)
HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "discipline.py"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_sstore"
TMPD = ROOT / "tmp"
STATE = TMPD / "claude-discipline"
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_sstore_proj"  # outside %TEMP%: edits there are counted
PROJ3 = WORK_ROOT / "_sstore_proj3"  # a second project with its own gate
NOGATE = WORK_ROOT / "_sstore_nogate"  # no CLAUDE.md: no declared gate
for p in (ROOT, PROJ, PROJ3, NOGATE):
    shutil.rmtree(p, ignore_errors=True)
TMPD.mkdir(parents=True)
(PROJ / "src").mkdir(parents=True)
(PROJ / "CLAUDE.md").write_text(
    "## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n", encoding="utf-8"
)
(PROJ3 / "src").mkdir(parents=True)
(PROJ3 / "CLAUDE.md").write_text(
    "## Verification\n- gate: `uv run pytest -x`\n- ci: none\n", encoding="utf-8"
)
(NOGATE / "src").mkdir(parents=True)
GATE, GATE3 = "uv run pytest -q --cov", "uv run pytest -x"
ENV = dict(
    os.environ,
    TMPDIR=str(TMPD),
    TEMP=str(TMPD),
    TMP=str(TMPD),
    CLAUDE_DISCIPLINE_HOME=str(ROOT / "home"),
    CLAUDE_DISCIPLINE_WITNESS=str(ROOT / "w" / "hwm"),
)
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:140],
        "" if ok else f"(want {want!r})",
    )


def hook(mode: str, s: str, env: dict | None = None, **payload: object) -> dict | None:
    p = inproc.run(
        HOOK,
        [mode],
        json.dumps({"session_id": s, "cwd": str(PROJ), **payload}),
        env=env or ENV,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-400:])
    return json.loads(p.stdout) if p.stdout.strip() else None


def state(s: str) -> dict:
    return json.loads((STATE / f"{s}.json").read_text(encoding="utf-8"))


def decision(out: dict | None) -> str | None:
    if not out:
        return None
    return out.get("hookSpecificOutput", {}).get("permissionDecision") or out.get(
        "decision"
    )


def reason(out: dict | None) -> str:
    out = out or {}
    return str(
        out.get("hookSpecificOutput", {}).get("permissionDecisionReason")
        or out.get("reason")
        or ""
    )


def edit_post(
    s: str, name: str, tool_use_id: str | None = None, lines: int = 1
) -> None:
    payload: dict[str, object] = {
        "prompt_id": "p",
        "tool_name": "Write",
        "tool_input": {"file_path": str(PROJ / "src" / name), "content": "x\n" * lines},
    }
    if tool_use_id:
        payload["tool_use_id"] = tool_use_id
    hook("post", s, **payload)


def hold_lock(s: str) -> int:
    """Take the session lock from this test process (another hook holding it)."""
    import msvcrt

    STATE.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE / f"{s}.lock", os.O_RDWR | os.O_CREAT)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    return fd


def release_lock(fd: int) -> None:
    import msvcrt

    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)


def edit_payload(s: str, name: str) -> str:
    return json.dumps(
        {
            "session_id": s,
            "cwd": str(PROJ),
            "prompt_id": "p",
            "tool_name": "Write",
            "tool_input": {"file_path": str(PROJ / "src" / name), "content": "x\n"},
        }
    )


# ---- S-T1 concurrent hooks of one session: no lost update
lost = 0
for _round in range(5):
    s = uuid.uuid4().hex
    hook("prompt", s, prompt_id="p", prompt="work")
    fd = hold_lock(
        s
    )  # both writers queue behind this holder (no effect without locking)
    procs = [
        subprocess.Popen(
            [sys.executable, "-I", str(HOOK), "post"],
            stdin=subprocess.PIPE,
            env=ENV,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for _ in range(2)
    ]
    for proc, name in zip(procs, ("a.py", "b.py"), strict=True):
        assert proc.stdin is not None
        proc.stdin.write(edit_payload(s, name))
        proc.stdin.close()
    time.sleep(
        0.8
    )  # let both reach the lock; a shorter wait only weakens the red phase
    release_lock(fd)
    for proc in procs:
        proc.wait(timeout=60)
    names = sorted(Path(f).name for f in state(s).get("turn_files", []))
    lost += names != ["a.py", "b.py"]
check("S-T1 two hooks of one session at once: no lost update (5 rounds)", lost, 0)

# ---- S-T2 killed while persisting: the previous state stays readable and intact
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "keep.py")
before = (STATE / f"{s}.json").read_text(encoding="utf-8")
CRASH = r"""
import io, os, sys, json
from pathlib import Path
sys.argv = [sys.argv[1], "post"]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
sys.stdin = io.TextIOWrapper(io.BytesIO(os.environ["PAYLOAD"].encode()), encoding="utf-8")
real = Path.write_text
def half(self, data, *a, **k):  # a non-atomic writer dies halfway through
    if self.suffix == ".json" and "claude-discipline" in str(self):
        with open(self, "w", encoding="utf-8") as f:
            f.write(data[: len(data) // 2]); f.flush()
        os._exit(9)
    return real(self, data, *a, **k)
Path.write_text = half
os.replace = lambda *a, **k: os._exit(9)  # an atomic writer dies before the replace
import runpy
runpy.run_path(sys.argv[0], run_name="__main__")
"""
crash_env = dict(ENV, PAYLOAD=edit_payload(s, "lost.py"))
r = subprocess.run(
    [sys.executable, "-I", "-c", CRASH, str(HOOK)],
    env=crash_env,
    capture_output=True,
    text=True,
    check=False,
)
after = (STATE / f"{s}.json").read_text(encoding="utf-8")
try:
    json.loads(after)
    readable = True
except ValueError:
    readable = False
check(
    "S-T2 hook killed while saving (exit 9): previous state intact and readable",
    (r.returncode, readable, after == before),
    (9, True, True),
)

# ---- S-T3 corrupt state: preserved, writes denied, Stop blocked, with the exact reason
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
(STATE / f"{s}.json").write_text("{not json", encoding="utf-8")
out = hook(
    "pre",
    s,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "c.py"), "content": "x"},
)
check(
    "S-T3' corrupt state: the write is not denied for the damage (a red gate stays fixable)",
    (decision(out) == "deny", "손상" in reason(out)),
    (False, False),
)
check(
    "S-T3 the corrupt file is preserved for inspection",
    len(list(STATE.glob(f"{s}.json.corrupt-*"))) >= 1,
    True,
)
out = hook("stop", s, prompt_id="p")
check(
    "S-T3 Stop blocks: changes before the damage are unverified",
    (decision(out), "손상" in reason(out)),
    ("block", True),
)

# ---- S-T4 recovery: the declared gate passing after the damage clears it (as in the
# harness, the PreToolUse of the same tool_use_id comes first)
hook(
    "pre",
    s,
    prompt_id="p",
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
    tool_use_id="g1",
)
hook(
    "post",
    s,
    prompt_id="p",
    tool_name="Bash",
    tool_input={"command": "uv run pytest -q --cov"},
    tool_use_id="g1",
    tool_response={},
)
out = hook("stop", s, prompt_id="p")
check(
    "S-T4 gate passed after the damage: the damage notice is cleared",
    "손상" in reason(out),
    False,
)

# ---- S-T5 lock held past the budget: fail closed
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
fd = hold_lock(s)
short = dict(ENV, CLAUDE_DISCIPLINE_LOCK_BUDGET_S="0.5")
out_pre = hook(
    "pre",
    s,
    env=short,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "d.py"), "content": "x"},
)
out_stop = hook("stop", s, env=short, prompt_id="p")
release_lock(fd)
check(
    "S-T5 session lock busy: write denied, Stop blocked (no fail-open)",
    (decision(out_pre), decision(out_stop)),
    ("deny", "block"),
)
# devenv-fix-j: the prompt hook cannot block, but it is no longer silent when it could not
# run: the prompt is told that the approval state is unknown
s2 = uuid.uuid4().hex
hook("prompt", s2, prompt_id="p", prompt="work")
fd2 = hold_lock(s2)
out_prompt = hook("prompt", s2, env=short, prompt_id="p2", prompt="next") or {}
release_lock(fd2)
told_busy = str(out_prompt.get("hookSpecificOutput", {}).get("additionalContext") or "")
check(
    "J-T6 session lock busy at a prompt: the prompt is told the hook could not handle it "
    "and to ask for the task line before editing",
    ("잠금" in told_busy, "task:<id>" in told_busy),
    (True, True),
)
check(
    "S-T5 ... reason names the lock",
    ("잠금" in reason(out_pre), "잠금" in reason(out_stop)),
    (True, True),
)

# ---- S-T6 the same event delivered twice changes the state once
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "dup.py", tool_use_id="same-1", lines=3)
edit_post(s, "dup.py", tool_use_id="same-1", lines=3)
lines = {
    Path(k).name: v
    for e in state(s).get("dirty", {}).values()
    for k, v in (e.get("lines") or {}).items()
}
check(
    "S-T6 duplicate PostToolUse (same tool_use_id): counted once",
    lines.get("dup.py"),
    3,
)

# ---- S-T7 a lock holder that died does not block the next hook
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
DIE = (
    "import msvcrt, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
    "msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)\n"
    "os._exit(0)\n"
)
subprocess.run([sys.executable, "-I", "-c", DIE, str(STATE / f"{s}.lock")], check=True)
edit_post(s, "after.py")
check(
    "S-T7 holder died with the lock: the next hook proceeds",
    "after.py" in {Path(f).name for f in state(s).get("turn_files", [])},
    True,
)

sys.path.insert(0, str(HOOK.parent))
import importlib

discipline = importlib.import_module("discipline")
session_store = importlib.import_module("session_store")
find_policy = importlib.import_module("verification").find_policy


def bash(
    s: str, command: str, tid: str, cwd: Path = PROJ, failed: bool = False
) -> None:
    """A shell check as the harness delivers it: PreToolUse, then Post/PostFailure."""
    common: dict[str, object] = {
        "prompt_id": "p",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": tid,
        "cwd": str(cwd),
    }
    hook("pre", s, **common)
    hook("fail" if failed else "post", s, tool_response={}, **common)


def corrupt(s: str, cwd: Path = PROJ, text: str = "{not json") -> None:
    (STATE / f"{s}.json").write_text(text, encoding="utf-8")
    hook("prompt", s, prompt_id="p", prompt="next", cwd=str(cwd))


def blocked_for_reset(s: str) -> bool:
    out = hook("stop", s, prompt_id="p")
    return decision(out) == "block" and "손상 또는 누락" in reason(out)


def write_in(s: str, proj: Path, name: str) -> None:
    hook(
        "post",
        s,
        prompt_id="p",
        tool_name="Write",
        tool_input={"file_path": str(proj / "src" / name), "content": "x\n"},
        cwd=str(proj),
    )


# ---- S-T9 only the declared gate of a root in the reset scope clears the marker
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "t9.py")
corrupt(s)
bash(s, "pytest -q", "r1", cwd=NOGATE)  # a plain runner elsewhere: passed, kind runner
bash(s, GATE3, "r2", cwd=PROJ3)  # another project's gate
check(
    "S-T9 unrelated runner / out-of-scope gate pass: marker kept, Stop blocks",
    blocked_for_reset(s),
    True,
)
bash(s, GATE, "r3")
check("S-T9 ... the scope root's gate passes: cleared", blocked_for_reset(s), False)

# ---- S-T10 two roots in scope (missed event, state readable): both gates are needed
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
write_in(s, PROJ, "a.py")
write_in(s, PROJ3, "b.py")
session_store.note_missed(STATE / f"{s}.json", "post: test")
hook("prompt", s, prompt_id="p", prompt="next")
check(
    "S-T10 marker scope = both gated roots",
    sorted(state(s).get("state_reset", {}).get("roots") or []),
    sorted(find_policy(str(p / "_"))["root"] for p in (PROJ, PROJ3)),
)
bash(s, GATE, "g1")
check("S-T10 one of two gates passed: kept", blocked_for_reset(s), True)
bash(s, GATE3, "g2", cwd=PROJ3)
check("S-T10 ... both passed: cleared", blocked_for_reset(s), False)

# ---- S-T10b nothing gated in scope: reported on one Stop, then cleared
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work", cwd=str(NOGATE))
corrupt(s, cwd=NOGATE)
check("S-T10b no gated root: the first Stop reports it", blocked_for_reset(s), True)
check("S-T10b ... the next Stop does not repeat it", blocked_for_reset(s), False)

# ---- S-T11 one hook deadline: lock wait + git budget fit inside the hook timeout
over = []
for mode, timeout in discipline.HOOK_TIMEOUT_S.items():
    for elapsed in (0.0, 1.0, 12.0, 24.0, 40.0):
        lb = discipline.lock_budget_s(mode, elapsed, 20.0)
        gb = discipline.git_budget_s(mode, elapsed + lb, 45.0)
        if (
            lb < 0
            or gb < 0
            or (
                elapsed + lb + gb > timeout - discipline.HOOK_MARGIN_S
                and elapsed < timeout - discipline.HOOK_MARGIN_S
            )
        ):
            over.append((mode, elapsed, lb, gb))
check("S-T11 lock + git budget <= timeout - margin (every mode)", over, [])
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
fd = hold_lock(s)
t0 = time.monotonic()
out = hook(
    "pre",
    s,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "late.py"), "content": "x"},
)
took = time.monotonic() - t0
release_lock(fd)
check(
    "S-T11 lock held throughout (default budgets): pre decides (deny) before its 30 s timeout",
    (decision(out), took < discipline.HOOK_TIMEOUT_S["pre"] - 1),
    ("deny", True),
)

# ---- S-T12 HOOK_TIMEOUT_S mirrors the discipline hook timeouts in settings.json
settings = json.loads((HERE.parent / "settings.json").read_text(encoding="utf-8"))
declared: dict[str, float] = {}
for groups in settings.get("hooks", {}).values():
    for group in groups:
        for h in group.get("hooks", []):
            words = str(h.get("command", "")).split()
            if any(w.endswith("discipline.py") for w in words[:-1]):
                declared[words[-1]] = float(h.get("timeout"))
# devenv-final 25 (changed expectation: post was 60 in both): the post hook runs the
# formatter first for an edit, so settings.json gives it the hook's own time plus the
# formatter's allowance.
check(
    "S-T12 settings.json discipline timeouts == HOOK_TIMEOUT_S (post: plus the formatter's)",
    declared,
    {
        **discipline.HOOK_TIMEOUT_S,
        "post": discipline.HOOK_TIMEOUT_S["post"] + discipline.FORMAT_ALLOWANCE_S,
    },
)

# ---- S-T13 a note written while notes are being taken is never lost
NOTES = 300
target = ROOT / "notes" / "n.json"
target.parent.mkdir(parents=True)
WRITER = (
    "import sys; from pathlib import Path\n"
    f"sys.path.insert(0, {str(HOOK.parent)!r})\n"
    "import session_store\n"
    f"for i in range({NOTES}): session_store.note_missed(Path(sys.argv[1]), f'n{{i}}')\n"
)
writer = subprocess.Popen([sys.executable, "-I", "-c", WRITER, str(target)])
taken: list[str] = []
while writer.poll() is None:
    taken += session_store.take_missed(target)
taken += session_store.take_missed(target)
got = [t for t in taken if t.startswith("n")]
check(
    "S-T13 concurrent note_missed/take_missed: every note taken exactly once",
    (writer.returncode, len(got), len(set(got))),
    (0, NOTES, NOTES),
)

# ---- L (P0-15): notes live in a per-session folder; the crowded state folder is not listed
LT = ROOT / "lt" / "claude-discipline"
LT.mkdir(parents=True)


def old_note(target: Path, suffix: str, *whys: str) -> Path:
    """A note in the earlier format: a file next to the state file."""
    f = target.with_name(f"{target.stem}.missed{suffix}")
    f.write_text(
        "".join(json.dumps({"at": 1.0, "why": w}) + "\n" for w in whys),
        encoding="utf-8",
    )
    return f


t1 = LT / "sess1.json"
session_store.note_missed(t1, "first")
session_store.note_missed(t1, "second")
notes1 = LT / "sess1.notes"
check(
    "L-T1 notes are files inside <session>.notes, nothing appears beside the state file",
    (
        sorted(x.name for x in LT.iterdir()),
        notes1.is_dir() and [x.name.startswith("missed.") for x in notes1.iterdir()],
    ),
    (["sess1.notes"], [True, True]),
)
# devenv-final 26: read and kept until the state that holds them is saved
t_keep = LT / "keep1.json"
session_store.note_missed(t_keep, "kept reason")
first = session_store.take_missed(t_keep, keep=True)
still = len(list((LT / "keep1.notes").iterdir()))
again = session_store.take_missed(t_keep, keep=True)  # a run whose save failed
session_store.forget_taken()
check(
    "L-T8 take(keep): the note stays until forget_taken; a run whose save failed leaves "
    "it for the next run, which reads it again",
    (first, still, again, list((LT / "keep1.notes").iterdir())),
    (["kept reason"], 1, ["kept reason"], []),
)
shutil.rmtree(LT / "keep1.notes", ignore_errors=True)
held = [0]
real_replace = os.replace


def busy_replace(src: str, dst: str) -> None:
    if str(dst).endswith("busy.json") and held[0] > 0:
        held[0] -= 1
        raise PermissionError(13, "held by a reader")
    real_replace(src, dst)


os.replace = busy_replace
try:
    held[0] = 12  # more than the three quick tries of before
    session_store.save(LT / "busy.json", {"a": 1})
    saved_after = json.loads((LT / "busy.json").read_text(encoding="utf-8"))
    budget, session_store.SAVE_BUDGET_S = session_store.SAVE_BUDGET_S, 0.05
    held[0] = 10_000
    try:
        session_store.save(LT / "busy.json", {"a": 2})
        gave_up = "saved"
    except session_store.StateWriteFailed:
        gave_up = "StateWriteFailed"
    session_store.SAVE_BUDGET_S = budget
finally:
    os.replace = real_replace
check(
    "L-T9 a state file held open for a moment is saved once it is free; one held for good "
    "fails after the budget, with nothing left behind",
    (
        saved_after,
        gave_up,
        json.loads((LT / "busy.json").read_text(encoding="utf-8")),
        [x.name for x in LT.iterdir() if x.name.endswith(".tmp")],
    ),
    ({"a": 1}, "StateWriteFailed", {"a": 1}, []),
)
(LT / "busy.json").unlink()
check(
    "L-T2 take: both reasons in order, the files are removed, nothing the second time",
    (
        session_store.take_missed(t1),
        list(notes1.iterdir()),
        session_store.take_missed(t1),
    ),
    (["first", "second"], [], []),
)

t2 = LT / "sess2.json"
old_note(t2, ".5.1.aa", "old-a")
old_note(t2, "", "old-b", "old-c")
check(
    "L-T3 earlier-format notes are read and removed when asked for",
    (
        session_store.take_missed(t2, legacy=True),
        sorted(x.name for x in LT.iterdir() if x.name.startswith("sess2")),
    ),
    (["old-b", "old-c", "old-a"], []),
)
kept = old_note(t2, ".6.1.bb", "old-d")
check(
    "L-T4 ... and neither read nor touched otherwise",
    (session_store.take_missed(t2), kept.exists()),
    ([], True),
)

listed: list[str] = []
real_names = getattr(session_store, "_names", None)


def spy_names(folder: Path) -> list[str]:
    listed.append(Path(folder).name)
    return real_names(folder)


session_store._names = spy_names
session_store.take_missed(t2)
plain = list(listed)
session_store.take_missed(t2, legacy=True)
session_store._names = real_names
check(
    "L-T7 a plain take lists only the session's notes folder, never the state folder",
    (plain, sorted(listed[len(plain) :])),
    (["sess2.notes"], ["claude-discipline", "sess2.notes"]),
)

t3 = LT / "sess3.json"
(LT / "sess3.notes").write_text("not a folder", encoding="utf-8")
logging.disable(
    logging.CRITICAL
)  # the refused note is logged; keep the output readable
session_store.note_missed(t3, "cannot be written")
logging.disable(logging.NOTSET)
check(
    "L-T8 a file in the notes folder's place: reported, no exception",
    session_store.take_missed(t3),
    ["놓친 이벤트 기록 목록을 읽을 수 없음"],
)
t4 = LT / "sess4.json"
(LT / "sess4.notes" / "missed.1.1.dir").mkdir(parents=True)
check(
    "L-T8 a note that cannot be read: reported, no exception",
    session_store.take_missed(t4),
    ["놓친 이벤트 기록을 읽을 수 없음"],
)

s = uuid.uuid4().hex
old_note(STATE / f"{s}.json", ".7.1.cc", "post: legacy-note")
hook("prompt", s, prompt_id="p", prompt="work")
check(
    "L-T5 a session not checked yet: its earlier-format note is taken",
    (
        "legacy-note" in str(state(s).get("state_reset", {}).get("why")),
        [x.name for x in STATE.iterdir() if x.name.startswith(f"{s}.missed")],
    ),
    (True, []),
)
check(
    "L-T5 ... a run that found something looks once more before the session is marked",
    (
        state(s).get("notes_checked"),
        hook("prompt", s, prompt_id="p1b", prompt="work") and None,
        state(s).get("notes_checked"),
    ),
    (0, None, 1),
)
late = old_note(STATE / f"{s}.json", ".8.1.dd", "post: late-legacy-note")
hook("prompt", s, prompt_id="p2", prompt="next")
check(
    "L-T6 a checked session does not look at earlier-format notes again",
    (
        "late-legacy-note" in str(state(s).get("state_reset", {}).get("why")),
        late.exists(),
    ),
    (False, True),
)
session_store.note_missed(STATE / f"{s}.json", "post: new-format-note")
hook("prompt", s, prompt_id="p3", prompt="again")
check(
    "L-T6 ... while a new-format note is still taken",
    "new-format-note" in str(state(s).get("state_reset", {}).get("why")),
    True,
)

if os.name == "nt":
    # review: an earlier-format note that cannot be removed yet is read again next run,
    # as its message says, also when it was found by the once-per-session look
    s = uuid.uuid4().hex
    stuck = old_note(STATE / f"{s}.json", ".7.2.ff", "post: stuck-legacy-note")
    with open(stuck, "rb"):  # an open file cannot be removed on Windows
        hook("prompt", s, prompt_id="p", prompt="work")
    first = (
        "다음 실행에서 다시 읽음" in str(state(s).get("state_reset", {}).get("why")),
        stuck.exists(),
        state(s).get("notes_checked"),
    )
    hook("prompt", s, prompt_id="p2", prompt="next")
    check(
        "L-T5b a note that could not be removed: reported, kept, and taken by the next run",
        (first, stuck.exists()),
        ((True, True, 0), False),
    )
else:
    print("NOTICE: L-T5b needs Windows file locking; skipped on this platform")

# ---- S-T14 a busy post/fail/prompt leaves a note that becomes a marker next run
short = dict(ENV, CLAUDE_DISCIPLINE_LOCK_BUDGET_S="0.3")
marked = {}
for mode in ("post", "fail", "prompt"):
    s = uuid.uuid4().hex
    hook("prompt", s, prompt_id="p", prompt="work")
    fd = hold_lock(s)
    hook(
        mode,
        s,
        env=short,
        prompt_id="p",
        prompt="x",
        tool_name="Write",
        tool_input={"file_path": str(PROJ / "src" / "busy.py"), "content": "x"},
        tool_response={},
    )
    release_lock(fd)
    hook("prompt", s, prompt_id="p", prompt="next")
    marked[mode] = mode in str(state(s).get("state_reset", {}).get("why"))
check(
    "S-T14 busy post/fail/prompt -> note -> marker on the next run",
    marked,
    {"post": True, "fail": True, "prompt": True},
)

# ---- S-T15 a failed save leaves a note that becomes a marker next run
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
SAVEFAIL = r"""
import io, os, sys
sys.argv = [sys.argv[1], "post"]
sys.path.insert(0, os.path.dirname(sys.argv[0]))
sys.stdin = io.TextIOWrapper(io.BytesIO(os.environ["PAYLOAD"].encode()), encoding="utf-8")
def no_fsync(fd):
    raise OSError(28, "No space left on device")
os.fsync = no_fsync
import runpy
runpy.run_path(sys.argv[0], run_name="__main__")
"""
r = subprocess.run(
    [sys.executable, "-I", "-c", SAVEFAIL, str(HOOK)],
    env=dict(ENV, PAYLOAD=edit_payload(s, "nospace.py")),
    capture_output=True,
    text=True,
    check=False,
)
hook("prompt", s, prompt_id="p", prompt="next")
check(
    "S-T15 save failed (exit 0) -> marker on the next run naming the failure",
    (r.returncode, "저장 실패" in str(state(s).get("state_reset", {}).get("why"))),
    (0, True),
)

# ---- S-T5' after the busy lock is released: marker visible, cleared by the gate
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "t5.py")
fd = hold_lock(s)
hook(
    "pre",
    s,
    env=short,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "t5.py"), "content": "x"},
)
release_lock(fd)
hook("prompt", s, prompt_id="p", prompt="next")
check("S-T5' released: the missed pre shows as a marker", blocked_for_reset(s), True)
bash(s, GATE, "g5")
check("S-T5' ... gate passed: cleared", blocked_for_reset(s), False)

# ---- F-T11 a missed subagent event: the marker names the subagent, and blocks as any other
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "f11.py")
fd = hold_lock(s)
hook(
    "pre",
    s,
    env=short,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "f11.py"), "content": "x"},
    agent_id="a7",
    agent_type="code-reviewer",
)
release_lock(fd)
hook("prompt", s, prompt_id="p", prompt="next")
check(
    "F-T11 missed subagent event: the marker names it and blocks",
    (
        "code-reviewer:a7" in str(state(s).get("state_reset", {}).get("why")),
        blocked_for_reset(s),
    ),
    (True, True),
)

# ---- G-T9 a lifecycle event that cannot take the lock: dropped, the session is not marked
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
fd = hold_lock(s)
gt9_outs = [
    hook(mode, s, env=short, agent_id="a7", agent_type="code-reviewer")
    for mode in ("agent-start", "agent-stop")
]
release_lock(fd)
hook("prompt", s, prompt_id="p", prompt="next")
check(
    "G-T9 missed lifecycle events: no output, no marker (they carry nothing to verify)",
    (gt9_outs, blocked_for_reset(s)),
    ([None, None], False),
)

# ---- G-T11 a lifecycle event that fails after taking the missed notes: they are noted again,
# so the next event widens the marker to every known root (as for any other event)
gt11 = STATE / f"{uuid.uuid4().hex}.json"
gt11.write_text(json.dumps({"touched_tops": [str(PROJ)]}), encoding="utf-8")
session_store.note_missed(gt11, "an earlier missed event")
gt11_data = {
    "session_id": gt11.stem,
    "cwd": str(NOGATE),
    "agent_id": "a7",
    "agent_type": "code-reviewer",
}


def _scope_fails(saved: object, cwd: str) -> tuple[list[str], list[str]]:
    raise RuntimeError("scope failure")


_real_scope = discipline.reset_scope
discipline.reset_scope = _scope_fails
logging.disable(logging.CRITICAL)  # the injected failure's traceback is not test output
try:
    discipline.run_hook("agent-stop", gt11_data, discipline.on_agent_stop, gt11)
finally:
    logging.disable(logging.NOTSET)
    discipline.reset_scope = _real_scope
discipline.run_hook("agent-stop", gt11_data, discipline.on_agent_stop, gt11)
check(
    "G-T11 lifecycle event failing mid-way: the marker still reaches the known gated root",
    os.path.normcase(str(PROJ))
    in {
        os.path.normcase(str(r))
        for r in json.loads(gt11.read_text(encoding="utf-8"))
        .get("state_reset", {})
        .get("roots", [])
    },
    True,
)

# ---- G-T12 agent-stop on an unreadable state: as any event (exit 0, preserved, marker), silent
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
(STATE / f"{s}.json").write_text("{not json", encoding="utf-8")
gt12_out = hook("agent-stop", s, agent_id="a7", agent_type="code-reviewer")
check(
    "G-T12 agent-stop on a damaged state: no output, preserved, marker",
    (
        gt12_out,
        len(list(STATE.glob(f"{s}.json.corrupt-*"))),
        "손상" in str(state(s).get("state_reset", {}).get("why")),
    ),
    (None, 1, True),
)

# ---- G-T13 the unverified-edit reason marks a subagent whose end was reported
s = uuid.uuid4().hex
gt13_who = {"agent_id": "a7", "agent_type": "code-reviewer"}
hook("prompt", s, prompt_id="p", prompt="work")
hook("agent-start", s, **gt13_who)
hook(
    "post",
    s,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "g13.py"), "content": "x\n"},
    **gt13_who,
)
hook("agent-stop", s, **gt13_who)
gt13_out = hook("stop", s, prompt_id="p")
check(
    "G-T13 unverified edit by an ended subagent: blocks, the reason says it ended",
    (decision(gt13_out), "code-reviewer:a7(종료됨)" in reason(gt13_out)),
    ("block", True),
)

# ---- G-T14 an agent id that the label cannot carry exactly is not tracked: two such ids
# would share one label, and one's end would mark the other as ended
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
gt14_outs = [
    hook("agent-start", s, agent_id=odd, agent_type="w")
    for odd in ("한글", "a b", "x" * 100)
]
check(
    "G-T14 agent ids changed by the label rules: not recorded, no start note",
    (gt14_outs, state(s).get("agents", {})),
    ([None, None, None], {}),
)

# ---- S-T16 well-formed JSON with wrong value types: no crash, preserved, marker
shapes = {
    "seen_events": json.dumps({"seen_events": "x"}),
    "state_reset.at": json.dumps({"state_reset": {"at": "abc", "why": "w"}}),
    "evidence": json.dumps({"evidence": []}),
    "background_tops": json.dumps({"background_tops": "C:/x"}),
    "owners": json.dumps({"owners": ["main"]}),
    "agents": json.dumps({"agents": ["x"]}),
}
result = {}
for label, text in shapes.items():
    s = uuid.uuid4().hex
    hook("prompt", s, prompt_id="p", prompt="work")
    (STATE / f"{s}.json").write_text(text, encoding="utf-8")
    edit_post(s, "shape.py", tool_use_id="t16")  # hook() asserts exit 0
    result[label] = (
        len(list(STATE.glob(f"{s}.json.corrupt-*"))) == 1,
        "손상" in str(state(s).get("state_reset", {}).get("why")),
    )
check(
    "S-T16 wrong value types: exit 0, preserved, marker",
    result,
    dict.fromkeys(shapes, (True, True)),
)

# ---- S-T17 while marked: edits allowed, Stop blocks once per turn, gate clears it
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
corrupt(s)
out = hook(
    "pre",
    s,
    prompt_id="p",
    tool_name="Write",
    tool_input={"file_path": str(PROJ / "src" / "fix.py"), "content": "x"},
)
edit_post(s, "fix.py")
first = hook("stop", s, prompt_id="p")
again = hook("stop", s, prompt_id="p", stop_hook_active=True)
check(
    "S-T17 marked: Edit not denied, Stop blocks, the re-stop of the turn passes",
    ("손상" in reason(out), decision(first), decision(again)),
    (False, "block", None),
)
bash(s, GATE, "g17")
check("S-T17 ... gate passed after the fix: cleared", blocked_for_reset(s), False)

BAD = WORK_ROOT / "_sstore_badutf"  # its CLAUDE.md is not UTF-8
shutil.rmtree(BAD, ignore_errors=True)
(BAD / "src").mkdir(parents=True)
(BAD / "CLAUDE.md").write_bytes(b"## Verification\n- gate: `\xff\xfe broken`\n")

WRAP = r"""
import io, os, sys
mode, hook = sys.argv[2], sys.argv[1]
sys.argv = [hook, mode]
sys.path.insert(0, os.path.dirname(hook))
sys.stdin = io.TextIOWrapper(io.BytesIO(os.environ["PAYLOAD"].encode()), encoding="utf-8")
def boom(*a, **k):
    raise RuntimeError("injected " + os.environ["BREAK"])
import verification, worktree, session_store
if os.environ["BREAK"] == "find_policy":
    verification.find_policy = boom
else:  # toplevel (on first sight of a directory) or set_budget (every run)
    setattr(worktree, os.environ["BREAK"], boom)
note_exc = os.environ.get("NOTE_EXC")
if note_exc:  # recording the failure fails too
    def bad_note(*a, **k):
        raise {"ValueError": ValueError, "RuntimeError": RuntimeError, "OSError": OSError}[note_exc]("injected note failure")
    session_store.note_missed = bad_note
if os.environ.get("STDOUT_BROKEN"):  # the decision itself cannot be written
    class Broken(io.TextIOBase):
        def write(self, s):
            raise OSError(32, "broken pipe (injected)")
    sys.stdout = Broken()
if os.environ.get("SAVE_BROKEN"):  # the state file cannot be replaced
    def no_save(path, state):
        raise session_store.StateWriteFailed("세션 상태 저장 실패: injected")
    session_store.save = no_save
if os.environ.get("STDOUT_FLUSH_BROKEN"):  # writes are buffered, the flush fails (pipe)
    class Buffered(io.TextIOBase):
        def write(self, s):
            return len(s)
        def flush(self):
            raise OSError(32, "broken pipe on flush (injected)")
    sys.stdout = Buffered()
import runpy
runpy.run_path(hook, run_name="__main__")
"""


def broken_hook(
    mode: str, s: str, what: str, extra: dict[str, str] | None = None, **payload: object
) -> tuple[int, dict | None]:
    """Run one hook with `what` (find_policy | toplevel) raising inside the hook process."""
    r = subprocess.run(
        [sys.executable, "-I", "-c", WRAP, str(HOOK), mode],
        env=dict(
            ENV,
            BREAK=what,
            PAYLOAD=json.dumps({"session_id": s, "cwd": str(PROJ), **payload}),
            **(extra or {}),
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # OS error text on stderr may be in the ANSI code page
        check=False,
    )
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else None)


def write_payload(name: str, sub: str) -> dict[str, object]:
    return {
        "prompt_id": "p",
        "tool_name": "Write",
        "tool_input": {"file_path": str(PROJ / "src" / sub / name), "content": "x\n"},
    }


# ---- S-T18 a non-UTF-8 CLAUDE.md where the reset scope is computed: nothing is lost
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "keep18.py")
session_store.note_missed(STATE / f"{s}.json", "post: t18-note")
hook("prompt", s, prompt_id="p", prompt="next", cwd=str(BAD))  # hook() asserts exit 0
st = state(s)
reset = st.get("state_reset") or {}
check(
    "S-T18 bad UTF-8 CLAUDE.md: exit 0, note kept in the marker, the place is in scope",
    (
        "t18-note" in str(reset.get("why")),
        os.path.normcase(str(BAD))
        in [os.path.normcase(r) for r in reset.get("roots") or []],
        # a prompt starts a new turn (turn_files empties); the unverified edit stays dirty
        find_policy(str(PROJ / "_"))["root"] in st.get("dirty", {}),
    ),
    (True, True, True),
)
check("S-T18 ... Stop blocks", blocked_for_reset(s), True)

# ---- S-T19 an exception while the reset scope is computed: state and marker survive
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "keep19.py")
session_store.note_missed(STATE / f"{s}.json", "post: t19-note")
rc, _out = broken_hook("post", s, "find_policy", **write_payload("x.py", "n19"))
st = state(s)
check(
    "S-T19 find_policy raises during the reset: exit 0, earlier state kept",
    (rc, "keep19.py" in {Path(f).name for f in st.get("turn_files", [])}),
    (0, True),
)
check("S-T19 ... the next Stop blocks for the reset", blocked_for_reset(s), True)

# ---- S-T20 a handler exception: post keeps the state and leaves a note, pre denies,
# Stop blocks
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
edit_post(s, "keep20.py")
rc_post, _out = broken_hook("post", s, "toplevel", **write_payload("x.py", "n20a"))
kept = "keep20.py" in {Path(f).name for f in state(s).get("turn_files", [])}
rc_pre, out_pre = broken_hook("pre", s, "toplevel", **write_payload("x.py", "n20b"))
rc_stop, out_stop = broken_hook("stop", s, "toplevel", prompt_id="p")
check(
    "S-T20 handler raises: exit 0 everywhere, state kept, pre deny, Stop block",
    (rc_post, kept, rc_pre, decision(out_pre), rc_stop, decision(out_stop)),
    (0, True, 0, "deny", 0, "block"),
)
check(
    "S-T20 ... the reason names the failure",
    "RuntimeError" in reason(out_pre),
    True,
)
hook("prompt", s, prompt_id="p", prompt="next")
check(
    "S-T20 ... the failed post becomes a marker on the next run",
    "post" in str(state(s).get("state_reset", {}).get("why")),
    True,
)

# ---- S-T21 a reset marker does not hide another Stop reason
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
(STATE / f"{s}.json").write_text(
    json.dumps(
        {
            "turn": 1,
            "approval_warning": "t21-approval-warning",
            "state_reset": {"at": 1.0, "why": "t21-reset", "roots": ["C:\\nowhere"]},
        }
    ),
    encoding="utf-8",
)
out = hook("stop", s, prompt_id="p")
check(
    "S-T21 marker + approval warning: one Stop shows both",
    (decision(out), "t21-reset" in reason(out), "t21-approval-warning" in reason(out)),
    ("block", True, True),
)

# ---- S-T22 a new reset after the previous scope already passed: only the new scope
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work", cwd=str(NOGATE))
old_root = find_policy(str(PROJ / "_"))["root"]
(STATE / f"{s}.json").write_text(
    json.dumps(
        {
            "turn": 1,
            "state_reset": {"at": 1.0, "why": "t22-old", "roots": [old_root]},
            "evidence": {
                old_root: {"kind": "gate", "outcome": "passed", "at": 2.0, "cmd": GATE}
            },
        }
    ),
    encoding="utf-8",
)
session_store.note_missed(STATE / f"{s}.json", "post: t22-new")
hook("prompt", s, prompt_id="p", prompt="next", cwd=str(NOGATE))
reset = state(s).get("state_reset") or {}
check(
    "S-T22 previous scope passed: new marker has only the new scope, newest reason first",
    (reset.get("roots"), str(reset.get("why")).startswith("post: t22-new")),
    ([], True),
)

AUDIT = ROOT / "home" / "state-recovery.jsonl"
FIX = WORK_ROOT / "_sstore_fix"  # CLAUDE.md broken first, repaired later
GONE = WORK_ROOT / "_sstore_gone"  # a repository that no longer exists
for p in (FIX, GONE):
    shutil.rmtree(p, ignore_errors=True)
(FIX / "src").mkdir(parents=True)
(FIX / "CLAUDE.md").write_bytes(b"## Verification\n- gate: `\xff broken`\n")
GATE4 = "uv run pytest -k fix"


def pre_write(s: str, sub: str, env: dict | None = None) -> dict | None:
    return hook(
        "pre",
        s,
        env=env,
        prompt_id="p",
        tool_name="Write",
        tool_input={"file_path": str(PROJ / "src" / sub / "w.py"), "content": "x"},
    )


def audit(s: str) -> list[str]:
    if not AUDIT.exists():
        return []
    rows = [json.loads(ln) for ln in AUDIT.read_text(encoding="utf-8").splitlines()]
    return [str(r.get("event")) for r in rows if r.get("session") == s]


def broken_state(s: str) -> bytes:
    """A real state-dependent failure: dir_tops is not a dict, track_repo raises on it."""
    hook("prompt", s, prompt_id="p", prompt="work")
    edit_post(s, "keep24.py")
    st = state(s)
    st["dir_tops"] = "x"
    raw = json.dumps(st).encode("utf-8")
    (STATE / f"{s}.json").write_bytes(raw)
    return raw


# ---- S-T23 a healthy state: a protected write is allowed (no decision, exit 0)
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
check("S-T23 healthy state: pre(Write) allowed", decision(pre_write(s, "n23")), None)

# ---- S-T24 a repeated, state-dependent exception: every protected write is denied,
# and the reason names the user's recovery command
s24 = uuid.uuid4().hex
broken_state(s24)
first = pre_write(s24, "n24a")
second = pre_write(s24, "n24b")
check(
    "S-T24 repeated exception: pre denied twice, reason offers recover-state",
    (decision(first), decision(second), "recover-state" in reason(second)),
    ("deny", "deny", True),
)

# ---- S-T25 recording the failure fails too (ValueError / RuntimeError / OSError):
# pre is still refused (deny or exit 2), Stop still blocks (block or exit 2)
outcomes = {}
for exc in ("ValueError", "RuntimeError", "OSError"):
    s = uuid.uuid4().hex
    hook("prompt", s, prompt_id="p", prompt="work")
    rc_p, out_p = broken_hook(
        "pre", s, "toplevel", {"NOTE_EXC": exc}, **write_payload("x.py", f"n25{exc}")
    )
    rc_s, out_s = broken_hook("stop", s, "set_budget", {"NOTE_EXC": exc}, prompt_id="p")
    outcomes[exc] = (
        (rc_p == 0 and decision(out_p) == "deny") or rc_p == 2,
        (rc_s == 0 and decision(out_s) == "block") or rc_s == 2,
    )
check(
    "S-T25 note_missed raises inside the failure path: no exit 1 (pre refused, Stop blocked)",
    outcomes,
    dict.fromkeys(("ValueError", "RuntimeError", "OSError"), (True, True)),
)

# ---- S-T26 the refusal itself cannot be written: exit 2 (blocking per the hook docs)
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
rc_p, _o = broken_hook(
    "pre", s, "toplevel", {"STDOUT_BROKEN": "1"}, **write_payload("x.py", "n26")
)
rc_s, _o = broken_hook("stop", s, "set_budget", {"STDOUT_BROKEN": "1"}, prompt_id="p")
check("S-T26 decision output fails: pre exit 2, stop exit 2", (rc_p, rc_s), (2, 2))

# ---- S-T28 recovery cannot move the original aside: nothing changes, still refused
s28 = uuid.uuid4().hex
raw28 = broken_state(s28)
with open(STATE / f"{s28}.json", "rb"):  # held open: the move (os.replace) fails
    hook("prompt", s28, prompt_id="p", prompt="recover-state")
check(
    "S-T28 recovery move fails: audited as failed, state unchanged, pre still denied",
    (
        audit(s28)[-1:] == ["failed"],
        (STATE / f"{s28}.json").read_bytes() == raw28,
        decision(pre_write(s28, "n28")),
    ),
    (True, True, "deny"),
)

# ---- S-T27 the user recovers the S-T24 session: original preserved byte for byte,
# audited, unverified roots kept as a marker, no approval, writes allowed, Stop blocked
# until the gate passes
before27 = (STATE / f"{s24}.json").read_bytes()  # as left by the denied S-T24 runs
hook("prompt", s24, prompt_id="p", prompt="  Recover-State ")
kept = list(STATE.glob(f"{s24}.json.recovered-*"))
st = state(s24)
proj_root = find_policy(str(PROJ / "_"))["root"]
check(
    "S-T27 recover-state: preserved copy == original, audit started+done, marker keeps the dirty root, approval 0",
    (
        len(kept) == 1 and kept[0].read_bytes() == before27,
        audit(s24)[-2:] == ["started", "done"],
        proj_root in (st.get("state_reset") or {}).get("roots", []),
        st.get("approval"),
        st.get("dir_tops") == "x",
    ),
    (True, True, True, None, False),
)
check(
    "S-T27 ... after recovery: pre allowed, Stop blocked for the reset",
    (decision(pre_write(s24, "n27")), blocked_for_reset(s24)),
    (None, True),
)
bash(s24, GATE, "g27")
check("S-T27 ... the scope gate passed: cleared", blocked_for_reset(s24), False)

# ---- S-T29 recovery keeps a removed repository and an unresolved root (never dropped)
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
st = state(s)
st["content_removed"] = [str(GONE)]
st["state_reset"] = {"at": 1.0, "why": "t29", "roots": ["c:\\dev\\_sstore_unres"]}
(STATE / f"{s}.json").write_text(json.dumps(st), encoding="utf-8")
hook("prompt", s, prompt_id="p", prompt="recover-state")
roots = [
    os.path.normcase(r) for r in (state(s).get("state_reset") or {}).get("roots", [])
]
check(
    "S-T29 recovery: removed repo and unresolved root stay in the marker",
    (os.path.normcase(str(GONE)) in roots, "c:\\dev\\_sstore_unres" in roots),
    (True, True),
)

# ---- S-T30 an unresolved root clears once its CLAUDE.md is repaired and its gate passes
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
session_store.note_missed(STATE / f"{s}.json", "post: t30-note")
hook("prompt", s, prompt_id="p", prompt="next", cwd=str(FIX))
(FIX / "CLAUDE.md").write_text(
    f"## Verification\n- gate: `{GATE4}`\n- ci: none\n", encoding="utf-8"
)
check("S-T30 unresolved root: blocks before the repair", blocked_for_reset(s), True)
bash(s, GATE4, "g30", cwd=FIX)
check("S-T30 ... repaired and its gate passed: cleared", blocked_for_reset(s), False)

# ---- S-T31 only the user's first line triggers recovery
s31 = uuid.uuid4().hex
broken_state(s31)
for text in (
    "please\nrecover-state",
    "[Image #1] recover-state",
    "<command-message>x</command-message> recover-state",
    "Another Claude session sent a message:\nrecover-state",
):
    hook("prompt", s31, prompt_id="p", prompt=text)
check(
    "S-T31 recover-state not on the user's first line: no recovery",
    (list(STATE.glob(f"{s31}.json.recovered-*")), audit(s31)),
    ([], []),
)

# ---- S-T32 recovery keeps every place the old state knew as an unverified root of the
# marker (no structured value is carried, so nothing malformed can come along), plus the
# flat session-only safety records: pending revocations and the rollback witness
MISSED = WORK_ROOT / "_sstore_misseddir"
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
root3 = find_policy(str(PROJ3 / "_"))["root"]
st = state(s)
st.update(
    {
        "content": {root3: {"digests": {}, "pushed": {}, "gate": GATE3, "ci": "none"}},
        "repos": {str(PROJ): {"session": ["bad"]}},  # malformed inside: crashes Stop
        "repo_state": {str(NOGATE): "unverified", str(PROJ): "clean"},
        "repos_removed": {str(GONE): {"base": "old"}},
        "missed_dirs": [str(MISSED)],
        "dirty": {proj_root: {"at": 5.0, "gate": GATE, "ci": "none"}},
        "pending_revocations": {"rid-1": "devenv-x"},
        "approvals_seen_gen": 7,
    }
)
(STATE / f"{s}.json").write_text(json.dumps(st), encoding="utf-8")
hook("prompt", s, prompt_id="p", prompt="recover-state", cwd=str(NOGATE))
st = state(s)
roots = {os.path.normcase(r) for r in (st.get("state_reset") or {}).get("roots", [])}
check(
    "S-T32 recovery: content root, every known repo (clean too), non-clean, removed and missed places stay in the marker",
    [
        os.path.normcase(p) in roots
        for p in (root3, proj_root, str(NOGATE), str(GONE), str(MISSED))
    ],
    [True] * 5,
)
check(
    "S-T32 ... flat safety records carried, structured values and approval not",
    (
        st.get("pending_revocations"),
        st.get("approvals_seen_gen"),
        st.get("repos"),
        st.get("approval"),
    ),
    ({"rid-1": "devenv-x"}, 7, None, None),
)
out = hook("stop", s, prompt_id="p", cwd=str(NOGATE))
check(
    "S-T32 ... the malformed value is gone: Stop blocks for the reset, not for a hook error",
    (decision(out), "손상 또는 누락" in reason(out), "처리 중 오류" in reason(out)),
    ("block", True, False),
)
hook("prompt", s, prompt_id="p", prompt="next", cwd=str(NOGATE))
check(
    "S-T32 ... and it stays blocked next turn (unresolved roots are not dropped)",
    blocked_for_reset(s),
    True,
)

# ---- S-T35 the new state cannot be saved during recovery: the original is untouched
s35 = uuid.uuid4().hex
hook("prompt", s35, prompt_id="p", prompt="work")
edit_post(s35, "keep35.py")
before35 = (STATE / f"{s35}.json").read_bytes()
rc, _o = broken_hook(
    "prompt",
    s35,
    "no_such_attr",
    {"SAVE_BROKEN": "1"},
    prompt_id="p",
    prompt="recover-state",
)
check(
    "S-T35 recovery save fails: exit 0, original byte for byte in place, audited failed",
    (rc, (STATE / f"{s35}.json").read_bytes() == before35, audit(s35)[-1:]),
    (0, True, ["failed"]),
)

# ---- S-T33 the deny is buffered and the flush fails (a broken pipe): still blocking
s = uuid.uuid4().hex
hook("prompt", s, prompt_id="p", prompt="work")
rc_p, _o = broken_hook(
    "pre", s, "toplevel", {"STDOUT_FLUSH_BROKEN": "1"}, **write_payload("x.py", "n33")
)
check("S-T33 deny cannot be flushed: pre exit 2 (not 120)", rc_p, 2)
edit_post(s, "unverified33.py")  # an ordinary reason for Stop to block
rc_s, _o = broken_hook(
    "stop", s, "no_such_attr", {"STDOUT_FLUSH_BROKEN": "1"}, prompt_id="p"
)
check("S-T33 ... an ordinary Stop block cannot be flushed: exit 2", rc_s, 2)

# ---- S-T34 recovery fails after the original was moved aside: it is put back, audited
s34 = uuid.uuid4().hex
hook("prompt", s34, prompt_id="p", prompt="work")
before34 = (STATE / f"{s34}.json").read_bytes()
rc, _o = broken_hook(
    "prompt", s34, "find_policy", prompt_id="p", prompt="recover-state"
)
check(
    "S-T34 failure after the move: original restored byte for byte, audited failed, exit 0",
    (
        rc,
        (STATE / f"{s34}.json").exists()
        and (STATE / f"{s34}.json").read_bytes() == before34,
        audit(s34)[-1:],
    ),
    (0, True, ["failed"]),
)

for p in (ROOT, PROJ, PROJ3, NOGATE, BAD, FIX, GONE):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
