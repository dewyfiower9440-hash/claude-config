"""P0-10: a session that moves into another repository is told about it once (python -I).

At a user prompt, when the working directory's repository is one this session has not seen,
the discipline hook injects that project's plan summary and says that approvals from the
previous project do not apply. The approval decision itself is unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
HOME = HERE / "_tmp_switch_home"
ROOT = HERE / "_tmp_switch"
for _p in (HOME, ROOT):
    shutil.rmtree(_p, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(HOME) + "-w"
os.environ.pop("CLAUDE_AUTHZ_MODE", None)
sys.stdout.reconfigure(errors="backslashreplace")
sys.path.insert(0, str(HOOKS))
import discipline as d

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


def plan(status: str, task: str, extra: str = "") -> str:
    return (
        f"---\nstatus: {status}\ntask: {task}\n{extra}---\n\n# P\n\n"
        "## 변경 범위\n- src/**\n\n## 단계\n- [x] 1. a\n- [ ] 2. b\n"
    )


def repo(name: str, plans: dict[str, str]) -> Path:
    r = ROOT / name
    (r / "docs" / "plans").mkdir(parents=True)
    (r / "src").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    for fname, text in plans.items():
        (r / "docs" / "plans" / fname).write_bytes(text.encode("utf-8"))
    return r


A = repo("a", {"2026-11-01-alpha.md": plan("in-progress", "alpha")})
B = repo(
    "b",
    {
        "2026-11-02-beta.md": plan("in-progress", "beta"),
        "2026-11-03-stuck.md": plan(
            "blocked", "stuck", "blocked_reason: waiting for a key\n"
        ),
        "2026-11-04-long.md": plan("in-progress", "L" * 3000),
    },
)
os.utime(B / "docs" / "plans" / "2026-11-02-beta.md", (2_000_000_100, 2_000_000_100))
C = repo("c", {})
# Outside every repository: ROOT itself sits inside this checkout, the temp folder does not.
PLAIN = Path(tempfile.mkdtemp(prefix="switch-plain-"))
_n = 0


def call(
    mode: str, session: str, authz: str | None = None, **payload: object
) -> dict | None:
    env = dict(os.environ)
    if authz:
        env["CLAUDE_AUTHZ_MODE"] = authz
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def prompt(session: str, cwd: Path, text: str = "go on") -> str:
    """The context the prompt hook injects ("" when it injects nothing)."""
    global _n
    _n += 1
    out = call("prompt", session, prompt_id=f"x{_n}", prompt=text, cwd=str(cwd))
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""


def state_of(session: str) -> dict:
    return json.loads(
        (
            Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
        ).read_text(encoding="utf-8")
    )


s = uuid.uuid4().hex
check("X-T1 the session's first prompt injects nothing", prompt(s, A), "")
note = prompt(s, B)
check(
    "X-T2 a new repository: its path, the approval notice and its main plan",
    (
        os.path.normcase(str(B)) in os.path.normcase(note.replace("/", "\\")),
        "승인은 이 저장소에 적용되지 않습니다" in note,
        "docs/plans/2026-11-02-beta.md" in note,
        "task:<id>" in note,
    ),
    (True, True, True, True),
)
check(
    "X-T7 the other plans are summarised and the notice stays short",
    (
        "2026-11-03-stuck.md: waiting for a key" in note,
        "2026-11-04-long.md" in note,
        "L" * 41 in note,
        len(note) <= 1500,
    ),
    (True, True, False, True),
)
check("X-T3 the same repository again: nothing", prompt(s, B), "")
check("X-T3 back in the first repository: nothing", prompt(s, A), "")
check("X-T4 a folder that is no repository: nothing", prompt(s, PLAIN), "")
check(
    "X-T4 ... and a repository seen for the first time after it is announced",
    (
        "저장소로 바뀌었습니다" in (c_note := prompt(s, C)),
        "진행 중이거나 막힌 계획이 없습니다" in c_note,
    ),
    (True, True),
)

s = uuid.uuid4().hex
prompt(s, A)
RELAY = 'Another Claude session sent a message:\n<agent-message from="x">\ndone\n</agent-message>'
check(
    "X-T5 a relayed message arriving in a new repository: nothing",
    prompt(s, B, RELAY),
    "",
)
check(
    "X-T5 ... the next user prompt there is announced",
    "저장소로 바뀌었습니다" in prompt(s, B),
    True,
)

s = uuid.uuid4().hex
prompt(s, A, "plan it")
call(
    "post",
    s,
    prompt_id=f"x{_n}",
    cwd=str(A),
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={
        "plan": plan("in-progress", "alpha"),
        "filePath": str(A / "docs" / "plans" / "2026-11-01-alpha.md"),
    },
)
check(
    "X-T6 an approval exists in the first repository",
    (state_of(s).get("approval") or {}).get("task_id"),
    "alpha",
)
note = prompt(s, B)
check(
    "X-T6 moving on: announced, and the session holds no approval there",
    ("저장소로 바뀌었습니다" in note, state_of(s).get("approval")),
    (True, None),
)
denied = call(
    "pre",
    s,
    "enforce",
    prompt_id=f"x{_n}",
    cwd=str(B),
    tool_name="Edit",
    tool_input={
        "file_path": str(B / "src" / "x.py"),
        "old_string": "a",
        "new_string": "b",
    },
)
check(
    "X-T6 ... an edit in the new repository is not covered by the old approval",
    ((denied or {}).get("hookSpecificOutput") or {}).get("permissionDecision"),
    "deny",
)

s = uuid.uuid4().hex
prompt(s, A)
both = prompt(s, B, "ack-missing C:/no/such/repo")
check(
    "X-T9 together with an ack-missing answer: one output carrying both",
    ("저장소로 바뀌었습니다" in both, "ack-missing" in both),
    (True, True),
)

# ---- the pure notice
check(
    "X-T8 no plans at all",
    "진행 중이거나 막힌 계획이 없습니다" in d.switch_notice("C:/r", None, []),
    True,
)
only_main = d.switch_notice("C:/r", "docs/plans/p.md", [])
check(
    "X-T8 a main plan only",
    ("`docs/plans/p.md`" in only_main, "계획이 없습니다" in only_main),
    (True, False),
)
many = d.switch_notice(
    "C:/r", "docs/plans/p.md", [f"- note {i} " + "n" * 200 for i in range(40)]
)
check(
    "X-T8 many notes: capped at 1500 characters",
    (len(many) <= 1500, "- note 0" in many),
    (True, True),
)
check(
    "X-T8 plans could not be read: no claim that there are none",
    "계획이 없습니다" in d.switch_notice("C:/r", None, [], plans_read=False),
    False,
)

for _p in (HOME, ROOT, PLAIN, Path(str(HOME) + "-w")):
    shutil.rmtree(_p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
