"""S (session state integrity): concurrent hooks, crashes while saving, corrupt state, lock
contention, duplicate events. Isolated TMPDIR / approval home; nothing real is touched.

    python -I tests/test_session_store.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "discipline.py"
ROOT = HERE / "_tmp_sstore"
TMPD = ROOT / "tmp"
STATE = TMPD / "claude-discipline"
PROJ = Path("C:/dev/_sstore_proj")  # outside %TEMP%: edits there are counted
PROJ3 = Path("C:/dev/_sstore_proj3")  # a second project with its own gate
NOGATE = Path("C:/dev/_sstore_nogate")  # no CLAUDE.md: no declared gate
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
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK), mode],
        input=json.dumps({"session_id": s, "cwd": str(PROJ), **payload}),
        env=env or ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
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
check(
    "S-T12 settings.json discipline timeouts == HOOK_TIMEOUT_S",
    declared,
    discipline.HOOK_TIMEOUT_S,
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

# ---- S-T16 well-formed JSON with wrong value types: no crash, preserved, marker
shapes = {
    "seen_events": json.dumps({"seen_events": "x"}),
    "state_reset.at": json.dumps({"state_reset": {"at": "abc", "why": "w"}}),
    "evidence": json.dumps({"evidence": []}),
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

for p in (ROOT, PROJ, PROJ3, NOGATE):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
