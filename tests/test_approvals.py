"""D-7: approval persistence and integrity (run with python -I).

Uses an isolated approval home (CLAUDE_DISCIPLINE_HOME) so the real approvals are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
HOME = HERE / "_tmp_approval_home"
REPO = HERE / "_tmp_approval_repo"
shutil.rmtree(HOME, ignore_errors=True)
shutil.rmtree(REPO, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
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
    call("prompt", s, prompt_id="r1")
    return state_of(s).get("approval")


def approvals_file() -> Path:
    return HOME / "approvals.json"


# ---- location: no longer in %TEMP%
legacy = Path(tempfile.gettempdir()) / "claude-discipline" / "approvals.json"
legacy_before = legacy.read_bytes() if legacy.exists() else None
approve()
check(
    "approvals are stored under the discipline home, not %TEMP%",
    approvals_file().is_file(),
    True,
)
check("a key file was created", (HOME / "key").is_file(), True)
check(
    "the old %TEMP% approvals file is not written",
    (legacy.read_bytes() if legacy.exists() else None) == legacy_before,
    True,
)

# ---- normal resume across sessions
save_plan(PLAN.replace("- [ ] 1. a", "- [x] 1. a"))
a = rearmed()
check(
    "same task, same plan, in progress -> re-armed in a new session",
    (a or {}).get("task_id"),
    "widget",
)

# ---- tampering
records = json.loads(approvals_file().read_text(encoding="utf-8"))
original = json.dumps(records)
for rec in records.values():
    rec["scope"] = ["**"]  # widen the approved scope
approvals_file().write_text(json.dumps(records), encoding="utf-8")
check("record edited after signing (scope widened) -> not re-armed", rearmed(), None)

forged_body = PLAN.replace("task: widget", "task: forged")
save_plan(forged_body)
sys.path.insert(0, str(HOOKS))
import importlib.util

spec = importlib.util.spec_from_file_location("discipline", HOOKS / "discipline.py")
assert spec and spec.loader
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
records = json.loads(original)
records[d.plan_hash(forged_body)] = {
    "task_id": "forged",
    "plan_hash": d.plan_hash(forged_body),
    "scope": [],
    "prompt_id": "x",
    "at": time.time(),
}
approvals_file().write_text(json.dumps(records), encoding="utf-8")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="f1")
check("unsigned forged record -> not re-armed", state_of(s).get("approval"), None)
check(
    "forgery is reported, not silently ignored",
    "서명" in str(state_of(s).get("approval_warning")),
    True,
)
stop = (call("stop", s, prompt_id="f1") or {}).get("reason") or ""
check("Stop surfaces the invalid approval record once", "서명" in stop, True)

approvals_file().write_text("{ not json", encoding="utf-8")
save_plan(PLAN)
check("corrupt approvals file -> not re-armed (no crash)", rearmed(), None)

approvals_file().unlink()
check("missing approvals file -> not re-armed", rearmed(), None)

approve()
(HOME / "key").unlink()
check(
    "key removed after approval -> old records cannot verify -> not re-armed",
    rearmed(),
    None,
)

# ---- expiry (a correctly signed but old record)
approve()
records = json.loads(approvals_file().read_text(encoding="utf-8"))
key = d.load_key(create=False)
for rec in records.values():
    rec["at"] = time.time() - 31 * 86400
    rec["mac"] = d.sign_record(rec, key)
approvals_file().write_text(json.dumps(records), encoding="utf-8")
check("signed record older than 30 days -> not re-armed", rearmed(), None)

# ---- new task / changed plan
approve()
save_plan(PLAN.replace("2. b", "2. b plus scope creep"))
check("plan text changed after approval -> not re-armed", rearmed(), None)
save_plan(PLAN.replace("task: widget", "task: other"))
check("different task id -> not re-armed", rearmed(), None)
save_plan(PLAN, status="done")
check("plan done -> not re-armed", rearmed(), None)
save_plan(PLAN)
check(
    "back to the approved in-progress plan -> re-armed",
    (rearmed() or {}).get("task_id"),
    "widget",
)

# ---- protection of the approval home against tool access
guard = HOOKS / "guard.py"
home_files = [
    Path.home() / ".claude" / "discipline" / "approvals.json",
    Path.home() / ".claude" / "discipline" / "key",
]
for f in home_files:
    for tool, ti in (
        ("Read", {"file_path": str(f)}),
        ("Write", {"file_path": str(f), "content": "{}"}),
        ("Bash", {"command": f"cat {f.as_posix()}"}),
        ("PowerShell", {"command": f"Set-Content -Path '{f}' -Value '{{}}'"}),
    ):
        p = subprocess.run(
            [sys.executable, "-I", str(guard)],
            input=json.dumps({"tool_name": tool, "tool_input": ti}),
            capture_output=True,
            text=True,
            check=False,
        )
        check(f"guard blocks {tool} on {f.name}", p.returncode, 2)
settings = json.loads(
    (HERE.parent / "settings.json").read_text(
        encoding="utf-8"
    )  # the repo's settings, not $HOME's (CI)
)
deny = settings["permissions"]["deny"]
check(
    "settings deny Read of the approval home",
    "Read(~/.claude/discipline/**)" in deny,
    True,
)
check(
    "settings deny Edit of the approval home",
    "Edit(~/.claude/discipline/**)" in deny,
    True,
)

shutil.rmtree(HOME, ignore_errors=True)
shutil.rmtree(REPO, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
