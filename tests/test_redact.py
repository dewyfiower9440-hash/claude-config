"""P1-09: token-shaped text is masked before it is written to the handoff record or the
task ledger, and before either reaches a session's context.

    python -I tests/test_redact.py

Every "token" here is assembled from pieces and has only the shape of one. Throw-away
folders; the real handoff store and ledger are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = Path(tempfile.mkdtemp(prefix="redact-"))
os.environ["CLAUDE_HANDOFF_HOME"] = str(ROOT / "store")
os.environ["CLAUDE_LEDGER_HOME"] = str(ROOT / "ledger")
os.environ["CLAUDE_STATE_GC_DIR"] = str(ROOT / "gc" / "claude-discipline")
sys.path.insert(0, str(HOOKS))
import handoff
import ledger
import session_context as sc

try:
    import redact as redact_module

    redact = redact_module.redact
    MASK = redact_module.MASK
except ImportError:
    redact = lambda text: text
    MASK = "[가림]"

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


BODY = "A1b2C3d4" * 5  # 40 characters with letters and digits


def fake(prefix: str, body: str = BODY) -> str:
    return prefix + body


def gone(text: str, *secrets: str) -> tuple[bool, bool]:
    """(no secret left in the masked text, the mask is there)."""
    out = redact(text)
    return not any(s in out for s in secrets), MASK in out


GH = [fake("gh" + kind + "_") for kind in "pousr"] + [
    fake("github" + "_pat_", "11AB" * 12)
]
check(
    "D-T1 GitHub tokens of every prefix",
    [gone(f"push with {t} now", t) for t in GH],
    [(True, True)] * len(GH),
)
OTHERS = [
    fake("sk" + "-"),
    fake("sk" + "-ant-api03-"),
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ASIA" + "ABCDEFGHIJKLMNOP",
    "xox" + "b-" + "1234567890-abcdefABCDEF123456",
    ".".join(
        (
            "eyJ" + "hbGciOiJIUzI1NiJ9",
            "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0",
            "SflKxwRJSMeKKF2QT4fwpM",
        )
    ),
]
check(
    "D-T2 API keys, cloud key ids, chat tokens, JWTs",
    [gone(f"x {t} y", t) for t in OTHERS],
    [(True, True)] * len(OTHERS),
)
check(
    "D-T2 the first line of a private key",
    gone("-----BEGIN " + "RSA PRIVATE KEY-----", "PRIVATE KEY"),
    (True, True),
)
VALUE = "Zx9" + "Qw8Er7Ty6"
pairs = {
    f"password={VALUE}": "password=",
    f"TOKEN: {VALUE}": "TOKEN:",
    f'api_key = "{VALUE}"': "api_key",
    f"Authorization: Bearer {VALUE}{VALUE}": "Authorization",
    f"client_secret={VALUE}": "client_secret=",
    '{"password": "' + VALUE + '"}': '"password"',
    '{"access_token":"' + VALUE + '"}': "access_token",
    f"SECRET_KEY={VALUE}": "SECRET_KEY=",
    f"DB_PASS={VALUE}": "DB_PASS=",
    f"private_key={VALUE}": "private_key=",
    f"passphrase: {VALUE}": "passphrase:",
    f"Authorization: Basic {VALUE}{VALUE}==": "Authorization",
}
check(
    "D-T3 name=value pairs: the value goes, the name stays",
    [(VALUE not in redact(text), keep in redact(text)) for text, keep in pairs.items()],
    [(True, True)] * len(pairs),
)
MORE = [
    "glpat" + "-" + "AbCdEf123456GhIjKl78",
    "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
    "npm" + "_" + "A1b2C3d4" * 4 + "A1b2",
    "hf" + "_" + "A1b2C3d4" * 4,
    "sk" + "_live_" + "A1b2C3d4" * 3,
    "pypi" + "-AgEIcHlwaS5vcmc" + "A1b2C3d4" * 4,
]
check(
    "D-T2 more token prefixes (GitLab, Google, npm, Hugging Face, Stripe, PyPI)",
    [gone(f"x {t} y", t) for t in MORE],
    [(True, True)] * len(MORE),
)
check(
    "D-T2 a PGP private key block's first line",
    gone("-----BEGIN PGP " + "PRIVATE KEY BLOCK-----", "PRIVATE KEY"),
    (True, True),
)
piece = ("gh" + "p_" + BODY)[:14]
check(
    "D-T9 the front piece of a cut token (a known prefix and a few characters) is masked too",
    [gone(f"task:deploy-{piece}", piece), gone(("xox" + "b-12345"), "b-12345")],
    [(True, True), (True, True)],
)
KEPT = [
    "token_budget: 12345678",
    "password_reset_flow: implemented",
    "pwd: C:\\dev\\project",
    "PWD=/home/user/project",
    "run pwd and check the output",
    "ghp is a prefix, gh pr list is a command",
]
check(
    "D-T5 names that only contain a secret word, and pwd as a folder, are left",
    [redact(x) for x in KEPT],
    KEPT,
)
tricky = 'token=":sk-@:b'
check(
    "D-T6 masking twice equals masking once also where one mask creates another match",
    (
        redact(redact(tricky)) == redact(tricky),
        redact(redact("https://u:p@h/x")) == redact("https://u:p@h/x"),
    ),
    (True, True),
)
url = f"https://deploy:{VALUE}@example.com/repo.git"
check(
    "D-T4 a password inside a URL: only the password goes",
    (
        VALUE not in redact(url),
        "deploy" in redact(url),
        "example.com/repo.git" in redact(url),
    ),
    (True, True, True),
)
PLAIN = [
    "a1b2c3d4e5f6",
    "0123456789abcdef0123456789abcdef01234567",
    "2f7312b3-4c52-40e8-a873-3dbc0bfab65a",
    "C:\\Users\\user\\.claude\\hooks\\discipline.py",
    "토큰 검증 테스트를 추가하고 게이트를 실행한다",
    "rotate the token and the password before the release",
    "pwd=1",
    "docs/plans/2026-11-15-devenv-p1-09.md",
    "feature/task-ledger",
    "skip-list and sk-1 are not keys",
]
check(
    "D-T5 ordinary text, hashes, ids and paths are left as they are",
    [redact(x) for x in PLAIN],
    PLAIN,
)
once = redact(f"use {GH[0]} then password={VALUE}")
check(
    "D-T6 masking again changes nothing; non-text and empty text pass through",
    (redact(once) == once, redact(None), redact(42), redact("")),
    (True, None, 42, ""),
)
sentence = redact(f"1. log in with {GH[0]} and {OTHERS[0]}, then run the gate")
check(
    "D-T7 two tokens in a sentence: both go, the sentence stays",
    (
        GH[0] in sentence or OTHERS[0] in sentence,
        sentence.count(MASK),
        sentence.startswith("1. log in with "),
        sentence.endswith(", then run the gate"),
    ),
    (False, 2, True, True),
)

# ---------------------------------------------------------------- the handoff record
record = handoff.clean(
    {
        "v": 1,
        "at": 5.0,
        "next_step": f"- [ ] 3. push with {GH[0]} and check CI",
        "branch": GH[1],
        "plan": "docs/plans/x.md",
        "head": "a1b2c3d4e5f6",
    }
)
check(
    "D-T8 a handoff record: tokens in the next step and the branch are masked, the rest stays",
    (
        GH[0] in json.dumps(record) or GH[1] in json.dumps(record),
        "push with" in record.get("next_step", "")
        and "check CI" in record.get("next_step", ""),
        record.get("head"),
        record.get("plan"),
    ),
    (False, True, "a1b2c3d4e5f6", "docs/plans/x.md"),
)
limit = handoff.STEP_CHARS
straddle = handoff.clean(
    {"v": 1, "at": 5.0, "next_step": "x" * (limit - 12) + " " + GH[0] + " tail"}
)
check(
    "D-T9 a token cut by the length limit leaves no piece behind",
    (
        "gh" + "p_" in straddle.get("next_step", ""),
        BODY[:8] in straddle.get("next_step", ""),
    ),
    (False, False),
)

REPO = ROOT / "repo"
(REPO / "docs" / "plans").mkdir(parents=True)
(REPO / "docs" / "plans" / "2026-11-15-widget.md").write_text(
    "---\nstatus: in-progress\ntask: widget\n---\n\n# Widget\n\n## 단계\n"
    f"- [ ] 1. authenticate with {GH[2]} and push\n",
    encoding="utf-8",
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


git("init", "-q")
git("add", "-A")
git("commit", "-q", "-m", "init")
git("checkout", "-q", "-b", "fix-" + GH[3])
TOP = git("rev-parse", "--show-toplevel")
p = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "handoff.py"), "end"],
    input=json.dumps(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "r",
            "reason": "other",
            "cwd": str(REPO),
        }
    ),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    check=False,
)
stored = "".join(f.read_text(encoding="utf-8") for f in (ROOT / "store").glob("*.json"))
check(
    "D-T8 a session end in a repository whose plan and branch hold tokens: none in the stored record",
    (
        p.returncode,
        GH[2] in stored,
        GH[3] in stored,
        "authenticate with" in stored,
        MASK in json.loads(stored or "{}").get("next_step", ""),
    ),
    (0, False, False, True, True),
)

old = ROOT / "store" / f"{handoff.key(TOP)}.json"
old.write_text(
    json.dumps(
        {
            "v": 1,
            "at": time.time() - 60,
            "session_id": "earlier",
            "reason": "other",
            "git_root": TOP,
            "branch": "main",
            "head": "a1b2c3d4e5f6",
            "dirty": 0,
            "plan": "docs/plans/2026-11-15-widget.md",
            "next_step": f"- [ ] 1. use {GH[4]} here",
            "open_steps": 1,
        }
    ),
    encoding="utf-8",
)
q = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "session_context.py")],
    input=json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": "next",
            "source": "startup",
            "cwd": str(REPO),
        }
    ),
    env=dict(os.environ, CLAUDE_PROJECTS_ROOT=str(ROOT / "none")),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    check=False,
)
ctx = (
    json.loads(q.stdout)["hookSpecificOutput"]["additionalContext"]
    if q.stdout.strip()
    else ""
)
check(
    "D-T10 a record written before masking existed: its token does not reach the next session's context",
    (q.returncode, GH[4] in ctx, f"use {MASK} here" in ctx),
    (0, False, True),
)
check(
    "D-T10 ... nor does the token in the plan's own checklist line or the branch name",
    (GH[2] in ctx, GH[3] in ctx),
    (False, False),
)

long_task = "a" * 25 + "-" + GH[0]
(REPO / "docs" / "plans" / "2026-11-16-long.md").write_text(
    f"---\nstatus: in-progress\ntask: {long_task}\n---\n\n## 목표\n"
    + "g" * (sc.GOAL_CHARS - 10)
    + f" {GH[1]}\n\n## 단계\n- [ ] 1. a\n",
    encoding="utf-8",
)
q = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "session_context.py")],
    input=json.dumps({"session_id": "next", "source": "startup", "cwd": str(REPO)}),
    env=dict(os.environ, CLAUDE_PROJECTS_ROOT=str(ROOT / "none")),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    check=False,
)
ctx = (
    json.loads(q.stdout)["hookSpecificOutput"]["additionalContext"]
    if q.stdout.strip()
    else ""
)
check(
    "D-T9 a token cut by the context's own limits (task id, goal) leaves no piece in the context",
    (q.returncode, "gh" + "p_" in ctx, "gh" + "o_" in ctx, len(ctx) <= sc.MAX_CHARS),
    (0, False, False, True),
)
huge = handoff.clean({"v": 1, "at": 5.0, "next_step": "a-" * 100_000 + GH[0]})
check(
    "D-T9 an absurdly long step is bounded before it is masked (and stays within its limit)",
    len(huge.get("next_step", "")) <= handoff.STEP_CHARS,
    True,
)

# ---------------------------------------------------------------- the ledger
wrote = ledger.record(
    "C:\\some\\repo",
    {
        "task": "deploy-" + GH[0],
        "approval": "a1",
        "state": "IMPLEMENTED",
        "content": "0123456789abcdef",
        "roots": 1,
        "session": "2f7312b3-4c52-40e8-a873-3dbc0bfab65a",
        "review": "none",
    },
    1.0,
)
entries, problem = ledger.read("C:\\some\\repo")
check(
    "D-T11 a ledger entry: the token in the task is masked, hashes and ids stay, the chain verifies",
    (
        wrote,
        GH[0] in json.dumps(entries),
        entries[0].get("content") if entries else None,
        entries[0].get("session") if entries else None,
        problem,
    ),
    (True, False, "0123456789abcdef", "2f7312b3-4c52-40e8-a873-3dbc0bfab65a", None),
)
summary = sc.PlanSummary("docs/plans/x.md", "in-progress", "t-" + GH[1][:30], 1, "")
shown = sc.ledger_notes(
    [summary],
    {
        summary.task: {
            "task": summary.task,
            "state": "CI_VERIFIED",
            "at": 5.0,
            "review": GH[0],
        }
    },
    None,
    0,
    None,
)
check(
    "D-T12 an older ledger line or plan with a token: the context lines hold none",
    ("gh" + "o_" in "".join(shown), GH[0] in "".join(shown)),
    (False, False),
)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
