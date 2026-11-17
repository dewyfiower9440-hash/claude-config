"""D-7: approval persistence and integrity (run with python -I).

Uses an isolated approval home (CLAUDE_DISCIPLINE_HOME) so the real approvals are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
HOME = HERE / "_tmp_approval2_home"
REPO = HERE / "_tmp_approval2_repo"
shutil.rmtree(HOME, ignore_errors=True)
shutil.rmtree(REPO, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
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
        repr(got)[:100],
        "" if ok else f"(want {want!r})",
    )


def call(mode: str, session: str, **payload: object) -> dict | None:
    payload.setdefault("cwd", str(REPO))
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def state_of(session: str) -> dict:
    return json.loads(
        (
            Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
        ).read_text(encoding="utf-8")
    )


PLAN = "# Widget\n\ntask: widget\n\n## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n- [ ] 2. b\n"
(REPO / "docs" / "plans").mkdir(parents=True)
# Its own git repository, so the project an approval is bound to is REPO (its toplevel).
subprocess.run(["git", "init", "-q"], cwd=REPO, check=True)
PLAN_FILE = REPO / "docs" / "plans" / "2026-11-06-widget.md"


def save_plan(body: str, status: str = "in-progress") -> None:
    PLAN_FILE.write_text(f"---\nstatus: {status}\n---\n\n{body}", encoding="utf-8")


def approve(body: str = PLAN) -> None:
    s = uuid.uuid4().hex
    call("prompt", s, prompt_id="a1")
    call(
        "post",
        s,
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": body},
        prompt_id="a1",
    )


def rearmed() -> dict | None:
    s = uuid.uuid4().hex  # a brand-new session, e.g. after a reboot
    call("prompt", s, prompt_id="r1", prompt="task:widget")
    return state_of(s).get("approval")


def approvals_file() -> Path:
    return HOME / "approvals.json"


# Split from tests/test_approvals.py (2026-11-17, devenv-gate-split): the cases of the v2
# approval store, unchanged. The lines above are that module's prelude with its two work
# folders renamed (both modules run at the same time); the four definitions below are the
# ones this half used from the other half's body.
RELAY = 'Another Claude session sent a message:\n<agent-message from="x">done</agent-message>'

NOTIF = "<task-notification>\n<task-id>x</task-id>\n<status>completed</status>\n</task-notification>"

KAGENT = {"agent_id": "ka1", "agent_type": "code-reviewer"}


def handback(agent_id: str, body: str = "done") -> str:
    return (
        "Another Claude session sent a message:\n"
        f'<agent-message from="{agent_id}">\n{body}\n</agent-message>'
    )


# ================================================================ P0-04: v2 approval instances
# Isolated homes/witnesses; the `v2-active` marker exists only inside these test homes.
sys.path.insert(0, str(HOOKS))
import approvals as ap

# Write decisions ignore %TEMP% paths (discipline IGNORED_PATH): a checkout under %TEMP%
# (a scratch worktree) points CLAUDE_TEST_TMP at a folder outside it.
TROOT = Path(os.environ.get("CLAUDE_TEST_TMP") or HERE)
TROOT.mkdir(parents=True, exist_ok=True)
V2HOME, V2W = TROOT / "_tmp_approval_v2home", TROOT / "_tmp_approval_v2w"
V2REPO = TROOT / "_tmp_approval_v2repo"
for p in (V2HOME, V2W, V2REPO):
    shutil.rmtree(p, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(V2HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(V2W / "hwm")
(V2REPO / "docs" / "plans").mkdir(parents=True)
(V2REPO / "src").mkdir()
subprocess.run(["git", "init", "-q"], cwd=V2REPO, check=True)
V2PLAN = (
    "---\nstatus: in-progress\ntask: widget\n---\n\n# Widget\n\n## 보안 조건\n- keep data\n\n"
    "## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n- [ ] 2. b\n\n## 진행 기록\n- start\n"
)
V2FILE = V2REPO / "docs" / "plans" / "2026-11-12-widget.md"
V2ALPHA = V2REPO / "docs" / "plans" / "2026-11-12-alpha.md"
TARGET = V2REPO / "src" / "w.py"
_pid = 0


def pid() -> str:
    global _pid
    _pid += 1
    return f"v2p{_pid}"


def v2_save(text: str = V2PLAN) -> None:
    V2FILE.write_bytes(text.encode("utf-8"))


def v2_approve(text: str = V2PLAN) -> tuple[str, str]:
    """A user approval (ExitPlanMode) in a fresh session: (session, prompt_id)."""
    s, p = uuid.uuid4().hex, pid()
    call("prompt", s, prompt_id=p, prompt="plan the widget", cwd=str(V2REPO))
    call(
        "post",
        s,
        prompt_id=p,
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": text, "filePath": str(V2FILE)},
        cwd=str(V2REPO),
    )
    return s, p


def v2_prompt(prompt: str, s: str | None = None, cwd: Path = V2REPO) -> tuple[str, str]:
    s, p = s or uuid.uuid4().hex, pid()
    call("prompt", s, prompt_id=p, prompt=prompt, cwd=str(cwd))
    return s, p


def edit_decision(s: str, p: str, path: Path = TARGET) -> str:
    """'allow' only via a live approval: five out-of-scope files were already edited this turn,
    so the five-file allowance cannot be what lets an in-scope edit through."""
    for i in range(5):
        other = V2REPO / f"o{i}.txt"
        call(
            "post",
            s,
            prompt_id=p,
            tool_name="Edit",
            tool_input={"file_path": str(other)},
            cwd=str(V2REPO),
        )
    out = call(
        "pre",
        s,
        prompt_id=p,
        tool_name="Edit",
        tool_input={"file_path": str(path), "old_string": "a", "new_string": "b"},
        cwd=str(V2REPO),
    )
    return out["hookSpecificOutput"]["permissionDecision"] if out else "allow"


def raw_pre(s: str, p: str, path: Path = TARGET) -> dict | None:
    return call(
        "pre",
        s,
        prompt_id=p,
        tool_name="Edit",
        tool_input={"file_path": str(path), "old_string": "a", "new_string": "b"},
        cwd=str(V2REPO),
    )


def v2_store() -> ap.Store:
    return ap.Store(V2HOME, V2W / "hwm")


sys.path.insert(0, str(HOOKS))
import discipline as _disc

check(
    "discipline's v2 detection uses the same files as approvals",
    _disc.V2_FILES,
    ap.V2_FILES,
)


# ---- T28 dormant: no marker -> v1 behaviour, no v2 files anywhere
v2_save()
s, p = v2_approve()
s2, p2 = v2_prompt("task:widget 계속")
check(
    "T28 dormant: v1 approval re-armed as before",
    (state_of(s2).get("approval") or {}).get("task_id"),
    "widget",
)
check(
    "T28 dormant: no v2 store, witness or marker written",
    [
        x.exists()
        for x in (
            V2HOME / ap.STORE_NAME,
            V2HOME / ap.HWM_NAME,
            V2W / "hwm",
            V2HOME / ap.MARKER_NAME,
        )
    ],
    [False, False, False, False],
)

# ---- activate v2 in the isolated home (admin path, confirmed in-test)
v2_store().admin_activate(lambda code: True, "test")
check("v2 marker exists only in the test home", ap.is_active(V2HOME), True)

# ---- T13 v1 approvals grant nothing under v2
s, p = v2_prompt("task:widget 계속")
check("T13 v1 approval is not re-armed under v2", state_of(s).get("approval"), None)
check("T13 edit denied without a v2 approval", edit_decision(s, p), "deny")

# ---- L-T9 (P0-15): one hook run reads and verifies the store once
_loads: list[int] = []
_real_load = ap.Store.load


def _counting_load(self: ap.Store, seen_gen: int = 0) -> ap.Snapshot:
    _loads.append(seen_gen)
    return _real_load(self, seen_gen)


ap.Store.load = _counting_load
_forget = getattr(_disc, "_v2_forget", lambda: None)
_forget()
_st: dict = {}
_a, _b = _disc._v2_load(_st), _disc._v2_load(_st)
check(
    "L-T9 two reads in one run: the store is loaded once",
    (len(_loads), _a is _b, _st.get("approvals_seen_gen") == _a.gen),
    (1, True, True),
)
_c = _disc._v2_update(_st, lambda recs: recs)
_n = len(_loads)
_d = _disc._v2_load(_st)
check(
    "L-T9 after a change by this run: what the change wrote, without another load",
    (_d is _c, _d.gen, len(_loads) - _n),
    (True, _a.gen + 1, 0),
)


def _refuse(recs: dict) -> dict:
    raise ap.StoreError("refused for the test")


try:
    _disc._v2_update(_st, _refuse)
except ap.StoreError:
    pass
_n = len(_loads)
_e = _disc._v2_load(_st)
check(
    "L-T9 after a change that failed: read again, not what was kept",
    (len(_loads) - _n, _e.gen),
    (1, _c.gen),
)
_forget()
_n = len(_loads)
_disc._v2_load(_st)
check("L-T9 a new run starts without what the last one read", len(_loads) - _n, 1)
ap.Store.load = _real_load
_forget()

# ---- normal approval, resume, progress updates
s1, p1 = v2_approve()
a1 = state_of(s1).get("approval") or {}
check("v2 approval issued with an approval_id", bool(a1.get("approval_id")), True)
check(
    "v2 approved edit allowed in the approving prompt", edit_decision(s1, p1), "allow"
)
s, p = v2_prompt("task:widget 계속")
check(
    "v2 resume in a new session (task named) -> same approval",
    (state_of(s).get("approval") or {}).get("approval_id"),
    a1.get("approval_id"),
)
check("v2 resumed edit allowed", edit_decision(s, p), "allow")
for name, text in (
    ("checkbox checked", V2PLAN.replace("- [ ] 1. a", "- [x] 1. a")),
    ("all checked", V2PLAN.replace("- [ ]", "- [x]")),
    ("unchecked again", V2PLAN.replace("- [ ] 1. a", "- [ ] 1. a")),
    ("progress log appended", V2PLAN + "- step 1 done\n"),
    ("CRLF", V2PLAN.replace("\n", "\r\n")),
):
    v2_save(text)
    s, p = v2_prompt("task:widget")
    check(f"T12 {name}: approval kept and edit allowed", edit_decision(s, p), "allow")
v2_save()

# ---- T22 (5) command-args must follow the command header directly; (3) note on other named plan
s, p = v2_prompt(
    "<command-message>implement</command-message>\n<command-name>/implement</command-name>\n<command-args>task:widget</command-args>"
)
check(
    "T22 /implement with task in its args re-arms",
    (state_of(s).get("approval") or {}).get("task_id"),
    "widget",
)
s, p = v2_prompt(
    "<command-name>/x</command-name>\nexpanded body\n<command-args>task:widget</command-args>"
)
check(
    "T22 command-args not right after the header -> not re-armed",
    state_of(s).get("approval"),
    None,
)
V2ALPHA.write_text(V2PLAN.replace("task: widget", "task: alpha"), encoding="utf-8")
s, p = v2_prompt("task:widget task:alpha")
st = state_of(s)
check(
    "T22 widget re-armed although alpha has no approval",
    (st.get("approval") or {}).get("task_id"),
    "widget",
)
check(
    "T22 the problem with alpha is reported as a note",
    "alpha" in str(st.get("approval_warning")),
    True,
)
V2ALPHA.unlink()

# ---- T5 / T6 other task, other root
s, p = v2_prompt("task:gadget")
check(
    "T5 a task without approval -> nothing re-armed", state_of(s).get("approval"), None
)
V2OTHER = TROOT / "_tmp_approval_v2other"
shutil.rmtree(V2OTHER, ignore_errors=True)
(V2OTHER / "docs" / "plans").mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=V2OTHER, check=True)
(V2OTHER / "docs" / "plans" / V2FILE.name).write_text(V2PLAN, encoding="utf-8")
s, p = v2_prompt("task:widget", cwd=V2OTHER)
check(
    "T6 same plan in another git root -> not re-armed",
    state_of(s).get("approval"),
    None,
)
shutil.rmtree(V2OTHER, ignore_errors=True)

# ---- T15 / T3 revocation in one session reaches a cached approval in another
sB, pB = v2_prompt("task:widget")
check(
    "T15 session B holds the approval",
    bool((state_of(sB).get("approval") or {}).get("approval_id")),
    True,
)
sA, pA = v2_prompt("revoke task:widget")
recs = v2_store().load().records
check(
    "T3 revoke persisted (REVOKED, user_revoked)",
    recs[a1["approval_id"]]["state_reason"],
    "user_revoked",
)
out = raw_pre(sB, pB)
check(
    "T15 B's cached approval is denied at the next write",
    (out or {}).get("hookSpecificOutput", {}).get("permissionDecision"),
    "deny",
)
s, p = v2_prompt("task:widget")
check(
    "T3 revoked approval is not re-armed in a new session",
    state_of(s).get("approval"),
    None,
)

# ---- T7 re-approval issues a new id; the old one stays revoked
s7, p7 = v2_approve()
a7 = state_of(s7).get("approval") or {}
check(
    "T7 re-approval: new approval_id",
    bool(a7.get("approval_id")) and a7.get("approval_id") != a1.get("approval_id"),
    True,
)
check(
    "T7 old id stays REVOKED",
    v2_store().load().records[a1["approval_id"]]["state"],
    "REVOKED",
)
check("T7 new approval allows edits", edit_decision(s7, p7), "allow")

# ---- P0-04b under v2: a relay never keeps the pointer; a revoked approval stays refused
sv, pv = v2_approve()
check(
    "v2 relay: approval pointer present before the relay",
    bool((state_of(sv).get("approval") or {}).get("approval_id")),
    True,
)
call("prompt", sv, prompt_id=pid(), prompt=RELAY, cwd=str(V2REPO))
check(
    "v2 relay: the pointer is dropped by the relay", state_of(sv).get("approval"), None
)
sr, pr = v2_approve()
v2_prompt("revoke task:widget")  # another session revokes it
pr2 = pid()
call("prompt", sr, prompt_id=pr2, prompt=NOTIF, cwd=str(V2REPO))
check(
    "R7 revoked, then a task notification: the write is refused",
    edit_decision(sr, pr2),
    "deny",
)
sr2, pr3 = v2_prompt("task:widget", s=sr)  # a real user prompt after the relay
check(
    "R7 ... and naming the task afterwards cannot revive the revoked approval",
    state_of(sr2).get("approval"),
    None,
)
v2_save()

# ---- R1 (devenv-r1): the hand-back of a subagent this session started keeps the pointer;
# every other relay drops it as before, and each write is still verified against the store.
V2FOREIGN = TROOT / "_tmp_approval_v2foreign"
shutil.rmtree(V2FOREIGN, ignore_errors=True)
V2FOREIGN.mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=V2FOREIGN, check=True)


def k_session() -> tuple[str, str]:
    """A fresh approved session that started (and ended) the subagent `ka1`."""
    s, p = v2_approve()
    call("agent-start", s, cwd=str(V2REPO), **KAGENT)
    call("agent-stop", s, cwd=str(V2REPO), **KAGENT)
    return s, p


def k_relay(s: str, prompt: str, cwd: Path = V2REPO) -> tuple[dict, str]:
    p = pid()
    call("prompt", s, prompt_id=p, prompt=prompt, cwd=str(cwd))
    return state_of(s), p


sk, _ = k_session()
k_before = (state_of(sk).get("approval") or {}).get("approval_id")
st, pk = k_relay(sk, handback("ka1"))
check(
    "K-T1 a recorded agent's hand-back keeps the same approval, bound to the relay prompt",
    (
        bool(k_before),
        (st.get("approval") or {}).get("approval_id") == k_before,
        (st.get("approval") or {}).get("prompt_id") == pk,
        st.get("approval_warning"),
    ),
    (True, True, True, None),
)
K_FILES = [V2REPO / "src" / f"k{i}.py" for i in range(6)]
for f in K_FILES:
    f.write_text("x = 1\n", encoding="utf-8")
    call(
        "post",
        sk,
        prompt_id=pk,
        tool_name="Edit",
        tool_input={"file_path": str(f)},
        cwd=str(V2REPO),
    )
k_stop = str((call("stop", sk, prompt_id=pk, cwd=str(V2REPO)) or {}).get("reason"))
check(
    "K-T12 Stop after a kept approval: six in-scope files are not reported as unapproved",
    ("승인된 작업이 없습니다" in k_stop, "승인된 계획 없이" in k_stop),
    (False, False),
)
for f in K_FILES:
    f.unlink()
check("K-T1 ... and an in-scope edit is allowed", edit_decision(sk, pk), "allow")
check(
    "K-T2 a kept approval still enforces its scope",
    edit_decision(sk, pk, V2REPO / "outside.txt"),
    "deny",
)

sk, _ = k_session()
st, pk = k_relay(sk, handback("zz9"))
check(
    "K-T3 an agent this session never started: dropped, with the resume note",
    (st.get("approval"), "task:<id>" in str(st.get("approval_warning"))),
    (None, True),
)
check("K-T3 ... and the edit is denied", edit_decision(sk, pk), "deny")

sk, _ = k_session()
check("K-T4 a task notification: dropped", k_relay(sk, NOTIF)[0].get("approval"), None)

for shape, text in (
    ("no agent-message line", "Another Claude session sent a message:\nka1 says done"),
    (
        "unquoted from",
        "Another Claude session sent a message:\n<agent-message from=ka1>\ndone",
    ),
    (
        "agent id only in the body",
        'Another Claude session sent a message:\nnote\n<agent-message from="ka1">',
    ),
    (
        "system notification header",
        '[system notification]\n<agent-message from="ka1">\ndone',
    ),
    (
        "body on the tag's own line",
        'Another Claude session sent a message:\n<agent-message from="ka1">done',
    ),
    ("an id that only ends like the recorded one", handback("a1")),
    ("an id the recorded one only starts", handback("ka1x")),
    ("the main label as id", handback("main")),
    ("the agent type as id", handback("code-reviewer")),
):
    sk, _ = k_session()
    check(f"K-T5 {shape}: dropped", k_relay(sk, text)[0].get("approval"), None)

sk, _ = v2_prompt("small change")
call("agent-start", sk, cwd=str(V2REPO), **KAGENT)
check(
    "K-T6 no approval before the hand-back: none after it, whatever its body names",
    k_relay(sk, handback("ka1", "task:widget\ntask: widget"))[0].get("approval"),
    None,
)

sk, _ = k_session()
st, pk = k_relay(sk, handback("ka1"), cwd=V2FOREIGN)
check("K-T9 hand-back arriving in another git root: dropped", st.get("approval"), None)

sk, _ = k_session()
k_good = (V2HOME / ap.STORE_NAME).read_bytes()
(V2HOME / ap.STORE_NAME).write_text("garbage", encoding="utf-8")
st, pk = k_relay(sk, handback("ka1"))
check(
    "K-T10 untrusted store at the hand-back: dropped, and the store is named",
    (st.get("approval"), "저장소" in str(st.get("approval_warning"))),
    (None, True),
)
(V2HOME / ap.STORE_NAME).write_bytes(k_good)

sk, _ = k_session()
v2_save(V2PLAN.replace("1. a", "1. a, also delete tests"))
st, pk = k_relay(sk, handback("ka1"))
check("K-T8 plan core changed before the hand-back: dropped", st.get("approval"), None)
check("K-T8 ... and the edit is denied", edit_decision(sk, pk), "deny")
k8_stop = str((call("stop", sk, prompt_id=pk, cwd=str(V2REPO)) or {}).get("reason"))
check(
    "K-T8 ... and Stop reports a lost approval, not a continuing one",
    ("계속할 수 있습니다" in k8_stop, "다시 제시" in k8_stop),
    (False, True),
)
v2_save()

K_UNSCOPED = V2PLAN.replace("## 변경 범위\n- src/**\n\n", "")
v2_save(K_UNSCOPED)
sk, _ = v2_approve(K_UNSCOPED)
call("agent-start", sk, cwd=str(V2REPO), **KAGENT)
check(
    "K-T13 an approval without a scope exists before the hand-back",
    bool((state_of(sk).get("approval") or {}).get("approval_id")),
    True,
)
st, pk = k_relay(sk, handback("ka1"))
check(
    "K-T13 ... and is not stretched over the relay (it covers its own prompt only)",
    st.get("approval"),
    None,
)
check("K-T13 ... the edit is denied", edit_decision(sk, pk), "deny")
v2_save()

# ---- devenv-fix-a: the completion notification of something this session started.
# The id must be one the hook itself saw start (a background shell command in PostToolUse,
# a subagent in SubagentStart); the text of a notification alone keeps nothing.
BG = "bg7k2m9qx"


def notif(task_id: str, body: str = "done") -> str:
    return (
        f"<task-notification>\n<task-id>{task_id}</task-id>\n"
        f"<status>completed</status>\n<summary>{body}</summary>\n</task-notification>"
    )


def bg_start(s: str, p: str, task_id: object = BG, tool: str = "Bash") -> None:
    call(
        "post",
        s,
        prompt_id=p,
        tool_name=tool,
        tool_input={"command": "uv run serve", "run_in_background": True},
        tool_response={"stdout": "", "stderr": "", "backgroundTaskId": task_id},
        cwd=str(V2REPO),
    )


def b_session() -> tuple[str, str]:
    """A fresh approved session that started the background command `BG`."""
    s, p = v2_approve()
    bg_start(s, p)
    return s, p


sb, pb0 = v2_approve()
for f in K_FILES:
    f.write_text("x = 1\n", encoding="utf-8")
    call(
        "post",
        sb,
        prompt_id=pb0,
        tool_name="Edit",
        tool_input={"file_path": str(f)},
        cwd=str(V2REPO),
    )
bg_start(sb, pb0)
b_before = (state_of(sb).get("approval") or {}).get("approval_id")
st, pb = k_relay(sb, notif(BG))
check(
    "K-T14 the notification of a background command this session started keeps the "
    "approval, bound to the notification prompt",
    (
        bool(b_before),
        (st.get("approval") or {}).get("approval_id") == b_before,
        (st.get("approval") or {}).get("prompt_id") == pb,
        st.get("approval_warning"),
    ),
    (True, True, True, None),
)
check(
    "K-T15 after six edits, a background command and its notification: the next "
    "in-scope edit is not refused",
    raw_pre(sb, pb),
    None,
)
b_stop = str((call("stop", sb, prompt_id=pb, cwd=str(V2REPO)) or {}).get("reason"))
check(
    "K-T15 ... and Stop does not report unapproved work",
    ("승인된 작업이 없습니다" in b_stop, "승인된 계획 없이" in b_stop),
    (False, False),
)
for f in K_FILES:
    f.unlink()
check("K-T14 ... an in-scope edit is allowed", edit_decision(sb, pb), "allow")
check(
    "K-T14 ... and the kept approval still enforces its scope",
    edit_decision(sb, pb, V2REPO / "outside.txt"),
    "deny",
)

sk, _ = k_session()
st, pk = k_relay(sk, notif("ka1"))
check(
    "K-T16 the completion notification of a subagent this session started: kept",
    bool((st.get("approval") or {}).get("approval_id")),
    True,
)

so, po = v2_prompt("unrelated work")
bg_start(so, po, "other9zz1")
sb, _ = b_session()
st, pb = k_relay(sb, notif("other9zz1"))
check(
    "K-T17 the id of a background task another session started: dropped, with the note",
    (st.get("approval"), "task:<id>" in str(st.get("approval_warning"))),
    (None, True),
)
check("K-T17 ... and the edit is denied", edit_decision(sb, pb), "deny")

for shape, text in (
    (
        "id line is not the second line",
        f"<task-notification>\n<status>completed</status>\n<task-id>{BG}</task-id>",
    ),
    ("text before the notification", "note\n" + notif(BG)),
    (
        "wrapped in pasted content",
        f'<pasted_content id="z">\n{notif(BG)}\n</pasted_content>',
    ),
    (
        "spaces inside the id tag",
        f"<task-notification>\n<task-id> {BG} </task-id>\n</task-notification>",
    ),
    (
        "more text on the id line",
        f"<task-notification>\n<task-id>{BG}</task-id> ok\n</task-notification>",
    ),
    (
        "all on one line",
        f"<task-notification><task-id>{BG}</task-id></task-notification>",
    ),
    (
        "text on the opening line",
        f"<task-notification> x\n<task-id>{BG}</task-id>\n</task-notification>",
    ),
):
    sb, _ = b_session()
    check(f"K-T18 {shape}: dropped", k_relay(sb, text)[0].get("approval"), None)

sb, pb = v2_prompt("small change")
bg_start(sb, pb)
check(
    "K-T19 no approval before the notification: none after it, whatever its body names",
    k_relay(sb, notif(BG, "task:widget\ntask: widget"))[0].get("approval"),
    None,
)

sb, _ = b_session()
check(
    "K-T20 notification arriving in another git root: dropped",
    k_relay(sb, notif(BG), cwd=V2FOREIGN)[0].get("approval"),
    None,
)

sb, _ = b_session()
v2_save(V2PLAN.replace("1. a", "1. a, also delete tests"))
st, pb = k_relay(sb, notif(BG))
check(
    "K-T20 plan core changed before the notification: dropped", st.get("approval"), None
)
check("K-T20 ... and the edit is denied", edit_decision(sb, pb), "deny")
v2_save()

v2_save(K_UNSCOPED)
sb, pb = v2_approve(K_UNSCOPED)
bg_start(sb, pb)
check(
    "K-T20 an approval without a scope is not stretched over the notification",
    k_relay(sb, notif(BG))[0].get("approval"),
    None,
)
v2_save()

for shape, text, want in (
    ("the real shape", notif(BG), BG),
    ("blank lines before it", "\n\n" + notif(BG), BG),
    ("an id with a bracket", notif("ab<c"), None),
    ("an id with a space", notif("ab c"), None),
    ("an empty id", notif(""), None),
    ("an id longer than 64", notif("a" * 65), None),
    ("a hand-back, not a notification", handback(BG), None),
    ("only the opening line", "<task-notification>", None),
    ("empty prompt", "", None),
):
    check(f"K-T18 relay_task_id, {shape}", _disc.relay_task_id(text), want)

b_state: dict = {}
for tool, response in (
    ("Bash", {"backgroundTaskId": 123}),
    ("Bash", {"backgroundTaskId": "ab c"}),
    ("Bash", {"backgroundTaskId": "x" * 65}),
    ("Bash", {"stdout": "backgroundTaskId"}),
    ("Bash", "backgroundTaskId"),
    ("Bash", None),
    ("Write", {"backgroundTaskId": BG}),
):
    _disc.note_background_task({"tool_name": tool, "tool_response": response}, b_state)
check("K-T21 ids that are not recorded", b_state.get("background_tasks") or [], [])
for i in range(51):
    _disc.note_background_task(
        {"tool_name": "PowerShell", "tool_response": {"backgroundTaskId": f"t{i}"}},
        b_state,
    )
    _disc.note_background_task(
        {"tool_name": "Bash", "tool_response": {"backgroundTaskId": f"t{i}"}}, b_state
    )
check(
    "K-T21 at most 50 ids are kept, the newest ones, each once",
    (
        len(b_state["background_tasks"]),
        b_state["background_tasks"][0],
        b_state["background_tasks"][-1],
    ),
    (50, "t1", "t50"),
)

b_sub: dict = {}
_disc.note_background_task(
    {
        "tool_name": "Bash",
        "agent_id": "ka1",
        "agent_type": "code-reviewer",
        "tool_response": {"backgroundTaskId": BG},
    },
    b_sub,
)
_disc.note_background_task({"tool_name": "Bash", "error": f"failed {BG}"}, b_sub)
check(
    "K-T21 a subagent's background command counts; a failure without a response adds none",
    b_sub.get("background_tasks"),
    [BG],
)
check(
    "K-T18 started_here: a recorded id, CRLF line ends",
    _disc.started_here(b_sub, notif(BG).replace("\n", "\r\n")),
    True,
)
for shape, held in (
    ("a string holding the id", BG),
    ("a dict keyed by the id", {BG: 1}),
    ("missing", None),
):
    check(
        f"K-T18 started_here: stored ids are not a list ({shape}) -> not started here",
        _disc.started_here({"background_tasks": held}, notif(BG)),
        False,
    )


# ---- devenv-fix-a2: an approval whose plan file is not saved yet (unbound) is not lost to
# a notification that arrives before the save. Unbound is not invalid: saving binds it.
def a_id(st: dict) -> object:
    return (st.get("approval") or {}).get("approval_id")


V2FILE.unlink()
su, pu0 = v2_approve()  # approved; docs/plans holds no file of this task yet
bg_start(su, pu0)
u_before = a_id(state_of(su))
st, pu = k_relay(su, notif(BG))
check(
    "K-T22 a notification before the plan file is saved: the approval is kept",
    (bool(u_before), a_id(st) == u_before, st.get("approval_warning")),
    (True, True, None),
)
v2_save()
u_stop = str((call("stop", su, prompt_id=pu, cwd=str(V2REPO)) or {}).get("reason"))
check(
    "K-T22 ... the plan saved afterwards: Stop reports no lost approval",
    ("다시 제시" in u_stop, "승인된 작업이 없습니다" in u_stop, a_id(state_of(su))),
    (False, False, u_before),
)
check("K-T22 ... and an in-scope edit is allowed", edit_decision(su, pu), "allow")

V2FILE.unlink()
su, pu0 = v2_approve()
bg_start(su, pu0)
k_relay(su, notif(BG))
st, pu = k_relay(su, notif(BG))  # a second notification, still before the save
u_pre = (raw_pre(su, pu) or {}).get("hookSpecificOutput", {})
check(
    "K-T23 kept across two notifications, still unbound: an edit outside docs/plans is "
    "refused, and the reason says the plan file is missing",
    (
        bool(a_id(st)),
        st.get("approval_warning"),
        u_pre.get("permissionDecision"),
        "아직 docs/plans에 없음" in str(u_pre.get("permissionDecisionReason")),
    ),
    (True, None, "deny", True),
)
u_stop = str((call("stop", su, prompt_id=pu, cwd=str(V2REPO)) or {}).get("reason"))
check(
    "K-T23 kept but never saved: Stop says the plan file is missing and drops it",
    (bool(a_id(st)), "아직 docs/plans에 없음" in u_stop, state_of(su).get("approval")),
    (True, True, None),
)

v2_save(V2PLAN.replace("task: widget\n", "task: widget\ncreated: 2026-11-16\n"))
su, pu0 = v2_approve()
bg_start(su, pu0)
st, pu = k_relay(su, notif(BG))
check(
    "K-T24 a file of the task with another text (still unbound): kept without a warning, "
    "it can be fixed",
    (bool(a_id(st)), st.get("approval_warning"), st.get("v2_block")),
    (True, None, None),
)
V2FILE.unlink()

su, pu0 = v2_approve()
bg_start(su, pu0)
check(
    "K-T25 unbound, a notification with an unknown id: dropped",
    k_relay(su, notif("zz9unknown"))[0].get("approval"),
    None,
)
su, pu0 = v2_approve()
bg_start(su, pu0)
check(
    "K-T25 unbound, the notification arrives in another git root: dropped",
    k_relay(su, notif(BG), cwd=V2FOREIGN)[0].get("approval"),
    None,
)
su, pu0 = v2_approve(K_UNSCOPED)
bg_start(su, pu0)
check(
    "K-T25 unbound and without a scope: dropped",
    k_relay(su, notif(BG))[0].get("approval"),
    None,
)
v2_save()

sb7, _ = b_session()
sk, _ = k_session()
v2_prompt("revoke task:widget")  # another session revokes it
check(
    "K-T20 revoked before the notification: dropped",
    k_relay(sb7, notif(BG))[0].get("approval"),
    None,
)
st, pk = k_relay(sk, handback("ka1"))
check("K-T7 revoked before the hand-back: dropped", st.get("approval"), None)
check("K-T7 ... the edit is denied", edit_decision(sk, pk), "deny")
check(
    "K-T7 ... and naming the task afterwards cannot revive it",
    state_of(v2_prompt("task:widget", s=sk)[0]).get("approval"),
    None,
)
shutil.rmtree(V2FOREIGN, ignore_errors=True)
v2_save()

# ---- devenv-fix-b: an approval whose plan file is not bound yet says why. A file of the
# same task with another text is named (the skill once told sessions to add a key); the
# verdict is the same refusal either way.
U_SAVED_WRONG = V2PLAN.replace("task: widget\n", "task: widget\ncreated: 2026-11-16\n")
U_OTHER = V2REPO / "docs" / "plans" / "2026-11-12-gadget.md"
U_OTHER.write_text(V2PLAN.replace("task: widget", "task: gadget"), encoding="utf-8")


def u_reason(s: str, p: str) -> str:
    out = raw_pre(s, p) or {}
    return str(out.get("hookSpecificOutput", {}).get("permissionDecisionReason"))


v2_save(U_SAVED_WRONG)
su, pu = v2_approve()
u_why = u_reason(su, pu)
check(
    "U-T1 saved with an extra frontmatter key: refused, the file is named and the rule said",
    (V2FILE.name in u_why, "status만" in u_why, "아직 docs/plans에 없음" in u_why),
    (True, True, False),
)
check(
    "U-T1 ... and neither the other task's file nor any plan text is in the message",
    (U_OTHER.name in u_why, "created" in u_why, "keep data" in u_why),
    (False, False, False),
)
v2_save()
check(
    "U-T2 saved as approved afterwards: the edit is allowed",
    edit_decision(su, pu),
    "allow",
)
V2FILE.unlink()
su, pu = v2_approve()
u_why = u_reason(su, pu)
check(
    "U-T3 no file of this task (only another task's): refused with the old wording",
    ("아직 docs/plans에 없음" in u_why, U_OTHER.name in u_why, "status만" in u_why),
    (True, False, False),
)
U_OTHER.unlink()
v2_save()
U_ROOT = TROOT / "_tmp_approval_unbound"
shutil.rmtree(U_ROOT, ignore_errors=True)
(U_ROOT / "docs" / "plans").mkdir(parents=True)
U_CORE = ap.core_hash(V2PLAN)
check(
    "U-T4 wording, no plan folder content: not saved yet",
    _disc._v2_unbound_why(str(U_ROOT), "widget", U_CORE),
    _disc.UNBOUND,
)
for n in range(5):
    (U_ROOT / "docs" / "plans" / f"d{n}.md").write_text(
        U_SAVED_WRONG.replace("2026-11-16", f"2026-11-1{n}"), encoding="utf-8"
    )
(U_ROOT / "docs" / "plans" / "same1.md").write_text(V2PLAN, encoding="utf-8")
(U_ROOT / "docs" / "plans" / "bin.md").write_bytes(b"\xff\xfe task: widget")
check(
    "U-T4 five files with another text (and one with the approved text): three named, "
    "the rest counted",
    _disc._v2_unbound_why(str(U_ROOT), "widget", U_CORE),
    _disc.UNBOUND_DIFFERS.format(names="d0.md, d1.md, d2.md 외 2개"),
)
(U_ROOT / "docs" / "plans" / "same2.md").write_text(
    V2PLAN.replace("in-progress", "done"), encoding="utf-8"
)
check(
    "U-T4 the approved text in two files: those two are named, not the differing ones",
    _disc._v2_unbound_why(str(U_ROOT), "widget", U_CORE),
    _disc.UNBOUND_TWICE.format(names="same1.md, same2.md"),
)
check(
    "U-T4 no task: nothing is named",
    _disc._v2_unbound_why(str(U_ROOT), None, U_CORE),
    _disc.UNBOUND,
)
shutil.rmtree(U_ROOT, ignore_errors=True)

# ---- T1 / T2 / T11 / T23 / T24: plan changes revoke, restoring them does not revive
for name, change in (
    ("T1 blocked", V2PLAN.replace("in-progress", "blocked")),
    ("T2 done", V2PLAN.replace("in-progress", "done")),
    ("T11 core step changed", V2PLAN.replace("1. a", "1. a, also delete tests")),
    ("T23 scope extended", V2PLAN.replace("- src/**\n", "- src/**\n- C:/**\n")),
    ("T24 task id changed", V2PLAN.replace("task: widget", "task: gadget")),
    ("T25 security constraint removed", V2PLAN.replace("- keep data\n", "")),
):
    v2_save()
    s, p = v2_approve()
    aid = (state_of(s).get("approval") or {}).get("approval_id")
    v2_save(change)
    sx, px = v2_prompt("task:widget")  # observes the change
    check(f"{name}: not re-armed while changed", state_of(sx).get("approval"), None)
    check(
        f"{name}: write by the approving session denied",
        (raw_pre(s, p) or {}).get("hookSpecificOutput", {}).get("permissionDecision"),
        "deny",
    )
    v2_save()  # restore the approved text / in-progress
    sy, py = v2_prompt("task:widget")
    check(
        f"{name}: restored plan does not revive the approval",
        state_of(sy).get("approval"),
        None,
    )
    check(
        f"{name}: approval REVOKED in the store",
        v2_store().load().records[aid]["state"],
        "REVOKED",
    )

# ---- T20 revocation that cannot be persisted: this session blocked, reported as not global
v2_save()
s, p = v2_approve()
aid = (state_of(s).get("approval") or {}).get("approval_id")
os.chmod(V2HOME / ap.STORE_NAME, stat.S_IREAD)
sr, pr = v2_prompt("revoke task:widget")
check(
    "T20 failed revocation reported as not global",
    "전역 취소 미완료" in str(state_of(sr).get("approval_warning")),
    True,
)
check(
    "T20 the store still says ACTIVE (not globally revoked)",
    v2_store().load().records[aid]["state"],
    "ACTIVE",
)
os.chmod(V2HOME / ap.STORE_NAME, stat.S_IREAD | stat.S_IWRITE)

# ---- T21 / T9 corrupt store: every protected write and issuance refused
good = (V2HOME / ap.STORE_NAME).read_bytes()
(V2HOME / ap.STORE_NAME).write_text("garbage", encoding="utf-8")
out = raw_pre(s, p)
check(
    "T21 corrupt store: write denied",
    (out or {}).get("hookSpecificOutput", {}).get("permissionDecision"),
    "deny",
)
s9, p9 = v2_prompt("make a small change")
out = raw_pre(s9, p9)
check(
    "T9 corrupt store: even the five-file allowance is denied",
    (out or {}).get("hookSpecificOutput", {}).get("permissionDecision"),
    "deny",
)
s9b, _ = v2_approve()
check(
    "T9 corrupt store: ExitPlanMode issues nothing", state_of(s9b).get("approval"), None
)
check(
    "T9 issuance failure is reported",
    "승인" in str(state_of(s9b).get("approval_warning")),
    True,
)
v2_prompt("discipline: reset-approvals")
check(
    "T27 the prompt text resets nothing",
    (V2HOME / ap.STORE_NAME).read_text(encoding="utf-8"),
    "garbage",
)

# ---- T14 rollback of the store snapshot
(V2HOME / ap.STORE_NAME).write_bytes(good)
v2_prompt("revoke task:widget")  # gen advances
(V2HOME / ap.STORE_NAME).write_bytes(
    good
)  # restore the older snapshot where it was ACTIVE
s14, p14 = v2_prompt("task:widget")
check("T14 restored snapshot: nothing re-armed", state_of(s14).get("approval"), None)
check(
    "T14 restored snapshot: write denied",
    (raw_pre(s14, p14) or {}).get("hookSpecificOutput", {}).get("permissionDecision"),
    "deny",
)

# ---- T10 admin reset grants nothing; only a new approval works
v2_store().admin_reset(lambda code: True, "test")
s, p = v2_prompt("task:widget")
check("T10 after reset: nothing re-armed", state_of(s).get("approval"), None)
check("T10 after reset: edit denied", edit_decision(s, p), "deny")
s, p = v2_approve()
check("T10 after reset: a new approval works", edit_decision(s, p), "allow")


def decision(out: dict | None) -> str:
    return (out or {}).get("hookSpecificOutput", {}).get(
        "permissionDecision"
    ) or "allow"


# ---- review: plan-mode approval (plan file outside the root, docs/plans copy written later)
V2FILE.unlink()
PM = TROOT / "_tmp_approval_planmode" / "cached-plan.md"
PM.parent.mkdir(parents=True, exist_ok=True)
PM.write_text(V2PLAN.replace("in-progress", "draft"), encoding="utf-8")
s, p = uuid.uuid4().hex, pid()
call("prompt", s, prompt_id=p, prompt="plan it", cwd=str(V2REPO))
call(
    "post",
    s,
    prompt_id=p,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={"plan": PM.read_text(encoding="utf-8"), "filePath": str(PM)},
    cwd=str(V2REPO),
)
check(
    "plan mode: approval issued although the plan file is not in docs/plans yet",
    bool((state_of(s).get("approval") or {}).get("approval_id")),
    True,
)
check(
    "plan mode: writing the plan file itself is allowed before binding",
    decision(raw_pre(s, p, V2FILE)),
    "allow",
)
check("plan mode: other writes wait for the plan file", decision(raw_pre(s, p)), "deny")
v2_save()  # the approved text, now in-progress, in docs/plans
check(
    "plan mode: once the plan file exists, the same prompt may write in scope",
    edit_decision(s, p),
    "allow",
)
shutil.rmtree(PM.parent, ignore_errors=True)

# ---- review: a plan that cannot be read now refuses without crashing or revoking
V2FILE.write_bytes(V2FILE.read_bytes() + b"\xff\xfe")
check(
    "non-UTF-8 plan: write denied (hook exits 0 with deny, no crash)",
    decision(raw_pre(s, p)),
    "deny",
)
stop_out = call("stop", s, prompt_id=p, cwd=str(V2REPO))
check(
    "non-UTF-8 plan: Stop runs and blocks for the unreadable plan (no crash)",
    (
        (stop_out or {}).get("decision"),
        "계획 파일을 지금 읽을 수 없음" in str((stop_out or {}).get("reason")),
    ),
    ("block", True),
)
v2_save()
s, p = v2_prompt("task:widget")
check(
    "non-UTF-8 plan was not revoked: re-armed after the file is fixed",
    edit_decision(s, p),
    "allow",
)

# ---- review: a store that crashes the JSON parser denies (no RecursionError crash)
good = (V2HOME / ap.STORE_NAME).read_bytes()
(V2HOME / ap.STORE_NAME).write_text("[" * 200_000, encoding="utf-8")
check(
    "deeply nested store: write denied, hook exits 0", decision(raw_pre(s, p)), "deny"
)
(V2HOME / ap.STORE_NAME).write_bytes(good)

# ---- review: deleting the marker alone does not fall back to v1
(V2HOME / ap.MARKER_NAME).unlink()
s, p = v2_prompt("task:widget")
check(
    "marker deleted, store present: still v2 (pointer is a v2 approval)",
    (state_of(s).get("approval") or {}).get("v"),
    2,
)
(V2HOME / ap.MARKER_NAME).write_text("test", encoding="utf-8")

# ---- T13 code rollback to the v1 path: activation retired v1 and rotated the key
V1HOME = TROOT / "_tmp_approval_v1home"
shutil.rmtree(V1HOME, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(V1HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(V1HOME) + "-w/hwm"
v2_save()
v2_approve()  # dormant: a v1 record
s, p = v2_prompt("task:widget")
check(
    "T13 control: v1 record re-arms before activation",
    (state_of(s).get("approval") or {}).get("task_id"),
    "widget",
)
ap.Store(V1HOME, Path(str(V1HOME) + "-w") / "hwm").admin_activate(
    lambda code: True, "t"
)
(V1HOME / ap.MARKER_NAME).unlink()  # code/marker rolled back: the v1 path runs again
s, p = v2_prompt("task:widget")
check(
    "T13 v1 path after activation: old approval not re-armed",
    state_of(s).get("approval"),
    None,
)
os.replace(V1HOME / "approvals.v1.retired-t.json", V1HOME / "approvals.json")
s, p = v2_prompt("task:widget")
check(
    "T13 retired v1 file restored: still not re-armed (key rotated)",
    state_of(s).get("approval"),
    None,
)
base = subprocess.run(
    ["git", "rev-parse", "--verify", "-q", "devenv-baseline-2026-11-11^{commit}"],
    cwd=HERE,
    capture_output=True,
    text=True,
    check=False,
)
if (
    base.returncode == 0
):  # local only: the tag is never pushed, CI checkouts have no tags
    OLD = HERE / "_tmp_approval_oldhooks"
    shutil.rmtree(OLD, ignore_errors=True)
    OLD.mkdir()
    names = subprocess.run(
        ["git", "ls-tree", "--name-only", "devenv-baseline-2026-11-11", "hooks/"],
        cwd=HERE.parent,  # the pathspec is relative to the cwd
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for n in names:
        if n.endswith(".py"):
            blob = subprocess.run(
                ["git", "show", f"devenv-baseline-2026-11-11:{n}"],
                cwd=HERE,
                capture_output=True,
                check=True,
            ).stdout
            (OLD / Path(n).name).write_bytes(blob)
    so = uuid.uuid4().hex
    old_run = subprocess.run(
        [sys.executable, "-I", str(OLD / "discipline.py"), "prompt"],
        input=json.dumps(
            {
                "session_id": so,
                "cwd": str(V2REPO),
                "prompt": "task:widget",
                "prompt_id": "old",
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    check(
        "T13 baseline-tag v1 code runs",
        (old_run.returncode, old_run.stderr[-300:]),
        (0, ""),
    )
    check(
        "T13 baseline-tag v1 code after activation: nothing re-armed",
        state_of(so).get("approval"),
        None,
    )
    shutil.rmtree(OLD, ignore_errors=True)
else:
    print(
        "NOTICE: baseline tag absent (CI); the v1-path rollback checks above still ran"
    )

for p_ in (V2HOME, V2W, V2REPO, V1HOME, Path(str(V1HOME) + "-w")):
    shutil.rmtree(p_, ignore_errors=True)
shutil.rmtree(HOME, ignore_errors=True)
shutil.rmtree(REPO, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
