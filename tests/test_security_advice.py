"""devenv-v1-2 step 7: security-auditor was never called in session 2ea67f50, also when
`uv add cryptography` brought in order signing. The main conversation's dependency add, or
its edit of a file whose name says auth/crypt/secret/sign/token/key, in a session without
a security-auditor run, gets one hint per session. A note only.

    python -I tests/test_security_advice.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or HERE)
ROOT = TMP_ROOT / "_tmp_security_advice"
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_security_advice_proj"
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
PROJ.mkdir(parents=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(ROOT / "home")
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
sys.path.insert(0, str(HOOKS))
import discipline as disc

ENV = dict(os.environ, TMPDIR=str(TMPD), TEMP=str(TMPD), TMP=str(TMPD))
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:150],
        "" if ok else f"(want {want!r})",
    )


# ---------------------------------------------------------------- pure
triggers = getattr(disc, "security_triggers", lambda tool, ti, root=None: None)
check(
    "S-T1 a dependency add is a trigger; a lock-file install or other commands are not",
    [
        triggers("Bash", {"command": "uv add cryptography"}),
        triggers("PowerShell", {"command": "pnpm add jose"}),
        triggers("Bash", {"command": "npm install"}),
        triggers("Bash", {"command": "git status"}),
    ],
    [["의존성 추가(uv add)"], ["의존성 추가(pnpm add)"], [], []],
)
check(
    "S-T2 a file named for auth/crypt/secret/sign/token/key is a trigger, as a word of its name",
    [
        triggers("Edit", {"file_path": "C:/p/src/auth.py"}),
        triggers("Write", {"file_path": "C:/p/src/order_signing.py"}),
        triggers("Edit", {"file_path": "C:/p/src/api_key.py"}),
        triggers("Edit", {"file_path": "C:/p/src/crypto_util.py"}),
        triggers("Edit", {"file_path": "C:/p/src/token_store.ts"}),
        triggers("Edit", {"file_path": "C:/p/secrets/config.py"}, "C:/p"),
    ],
    [
        ["보안 관련 파일(auth.py)"],
        ["보안 관련 파일(order_signing.py)"],
        ["보안 관련 파일(api_key.py)"],
        ["보안 관련 파일(crypto_util.py)"],
        ["보안 관련 파일(token_store.ts)"],
        ["보안 관련 파일(config.py)"],
    ],
)
check(
    "S-T3 not a trigger: a test file, a word that only contains the letters "
    "(tokenizer, design, monkey), an ordinary file",
    [
        triggers("Edit", {"file_path": "C:/p/tests/test_auth.py"}),
        triggers("Edit", {"file_path": "C:/p/src/test_tokenizer.py"}),
        triggers("Edit", {"file_path": "C:/p/src/tokenizer.py"}),
        triggers("Edit", {"file_path": "C:/p/src/design.py"}),
        triggers("Edit", {"file_path": "C:/p/src/monkey.py"}),
        triggers("Edit", {"file_path": "C:/p/src/orders.py"}),
        triggers("Read", {"file_path": "C:/p/src/auth.py"}),
    ],
    [[], [], [], [], [], [], []],
)
check(
    "S-T3c (review 1, 2) only the path inside the repository counts: its folder name, a user "
    "folder and a plan file are no trigger; a relative path keeps its first folder",
    [
        triggers(
            "Edit",
            {"file_path": "C:/dev/auth-service/README.md"},
            "C:/dev/auth-service",
        ),
        triggers(
            "Edit",
            {"file_path": "C:/dev/auth-service/src/orders.py"},
            "C:/dev/auth-service",
        ),
        triggers("Edit", {"file_path": "C:/Users/author/proj/x.py"}, None),
        triggers(
            "Edit", {"file_path": "C:/p/docs/plans/2026-11-20-signing.md"}, "C:/p"
        ),
        triggers(
            "Edit",
            {"file_path": "C:/dev/auth-service/src/auth.py"},
            "C:/dev/auth-service",
        ),
        triggers("Edit", {"file_path": "C:/dev/x/auth/login.py"}, "C:/dev/x"),
        triggers("Edit", {"file_path": "auth/login.py"}, None),
        triggers("Edit", {"file_path": "C:/elsewhere/api_key.txt"}, None),
    ],
    [
        [],
        [],
        [],
        [],
        ["보안 관련 파일(auth.py)"],
        ["보안 관련 파일(login.py)"],
        ["보안 관련 파일(login.py)"],
        ["보안 관련 파일(api_key.txt)"],
    ],
)
# A recorded false positive: `author.py` has no security meaning but starts with `auth`.
check(
    "S-T3b known false positive kept visible: author.py",
    triggers("Edit", {"file_path": "C:/p/src/author.py"}),
    ["보안 관련 파일(author.py)"],
)


# ---------------------------------------------------------------- through the hooks
def hook(mode: str, s: str, **payload: object) -> dict:
    p = inproc.run(
        HOOKS / "discipline.py",
        [mode],
        json.dumps({"session_id": s, "cwd": str(PROJ), "prompt_id": "p", **payload}),
        env=ENV,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-300:])
    return json.loads(p.stdout) if p.stdout.strip() else {}


def said(out: dict) -> str:
    return str(out.get("hookSpecificOutput", {}).get("additionalContext") or "")


def shell(s: str, command: str, **who: object) -> str:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": uuid.uuid4().hex,
        **who,
    }
    hook("pre", s, **common)
    return said(hook("post", s, tool_response={"stdout": "", "stderr": ""}, **common))


def edit(s: str, rel: str, **who: object) -> str:
    f = PROJ / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x = 1\n", encoding="utf-8")
    common = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(f), "content": "x = 1\n"},
        "tool_use_id": uuid.uuid4().hex,
        **who,
    }
    hook("pre", s, **common)
    return said(hook("post", s, tool_response={}, **common))


def start() -> str:
    s = uuid.uuid4().hex
    hook("prompt", s, prompt="work")
    return s


AUDITOR = "security-auditor"
s = start()
first = shell(s, "uv add cryptography")
check(
    "S-T4 a dependency add without an audit: the hint names security-auditor and the trigger",
    (AUDITOR in first, "uv add" in first, "알림" in first),
    (True, True, True),
)
check(
    "S-T5 the second time in the session: no hint",
    AUDITOR in edit(s, "src/auth.py"),
    False,
)

s = start()
check(
    "S-T6 an edit of a security file: the hint", AUDITOR in edit(s, "src/auth.py"), True
)

s = start()
hook(
    "post",
    s,
    tool_name="Agent",
    tool_input={"subagent_type": AUDITOR, "prompt": "audit"},
    tool_use_id=uuid.uuid4().hex,
    tool_response={},
)
check(
    "S-T7 after a security-auditor run: no hint",
    AUDITOR in shell(s, "uv add jose"),
    False,
)

s = start()
hook("agent-start", s, agent_id="a1", agent_type=AUDITOR)
check(
    "S-T7b a security-auditor seen starting counts too",
    AUDITOR in edit(s, "src/auth.py"),
    False,
)

s = start()
check(
    "S-T8 a subagent's add or edit: nothing",
    (
        shell(s, "uv add x", agent_id="g1", agent_type="general-purpose"),
        edit(s, "src/auth.py", agent_id="g1", agent_type="general-purpose"),
    ),
    ("", ""),
)
check(
    "S-T8b ... and the main conversation still gets it once",
    AUDITOR in shell(s, "uv add x"),
    True,
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
