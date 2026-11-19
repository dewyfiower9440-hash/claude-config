"""v1.1 step 2: a `git commit` of a change that needs an independent review and has none
is refused once (CLAUDE_COMMIT_REVIEW=block, the default), with the reviewer and the
/commit skill named; the second attempt passes. `notify` says it after the commit instead;
`off` does nothing.

    python -I tests/test_commit_review.py

A throw-away repository outside %TEMP%, an isolated temp folder and approval home. A change
that needs a review is made by editing hooks/x.py (a risky path), as test_review_binding.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_commit_review"
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_commit_review_proj"
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
os.environ.pop("CLAUDE_COMMIT_REVIEW", None)
os.environ.pop("CLAUDE_REVIEW_GATE", None)
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
commits = getattr(disc, "commit_commands", lambda command, dialect, cwd: None)
mode_of = getattr(disc, "commit_review_mode", lambda raw: None)


def norm(paths: object) -> object:
    if not isinstance(paths, list):
        return paths
    return [os.path.normcase(os.path.normpath(str(p))) for p in paths]


HERE_CWD = "C:/dev"
SUB = os.path.normcase(os.path.normpath("C:/dev/sub"))
TOP = os.path.normcase(os.path.normpath(HERE_CWD))
check(
    "C-T1 git commit is seen as git reads it; other git commands and words are not",
    [
        norm(commits('git commit -m "x"', "bash", HERE_CWD)),
        norm(commits("git -C sub commit -q", "bash", HERE_CWD)),
        norm(commits("cd sub && git add -A && git commit -F msg", "bash", HERE_CWD)),
        commits("git status && git stash push -m commit", "bash", HERE_CWD),
        commits("echo git commit", "bash", HERE_CWD),
        commits("git commit --dry-run", "bash", HERE_CWD),
        commits("git log --grep commit", "bash", HERE_CWD),
        norm(commits("git commit -m 'x'; git push", "powershell", HERE_CWD)),
        commits("echo 'unbalanced", "bash", HERE_CWD),
        commits(None, "bash", HERE_CWD),
    ],
    [[TOP], [SUB], [SUB], [], [], [], [], [TOP], [], []],
)
check(
    "C-T2 the mode: unset and junk are block; notify and off are themselves",
    [mode_of(None), mode_of("notify"), mode_of("OFF"), mode_of("x"), mode_of("")],
    ["block", "notify", "off", "block", "block"],
)

# ---------------------------------------------------------------- through the hooks
(PROJ / "hooks").mkdir(parents=True)
(PROJ / "src").mkdir(parents=True)
(PROJ / "hooks" / "x.py").write_text("x = 0\n", encoding="utf-8")
(PROJ / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
(PROJ / "CLAUDE.md").write_text(
    "## Verification\n- gate: `uv run pytest -q --cov`\n- ci: none\n", encoding="utf-8"
)
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
subprocess.run(["git", "add", "-A"], cwd=PROJ, check=True)
subprocess.run(
    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
    cwd=PROJ,
    check=True,
)
COMMIT = 'git commit -m "x"'


def hook(mode: str, s: str, env: dict = ENV, **payload: object) -> dict:
    p = inproc.run(
        HOOKS / "discipline.py",
        [mode],
        json.dumps({"session_id": s, "cwd": str(PROJ), "prompt_id": "p", **payload}),
        env=env,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-300:])
    return json.loads(p.stdout) if p.stdout.strip() else {}


def start(env: dict = ENV) -> str:
    s = uuid.uuid4().hex
    hook("prompt", s, env, prompt="work")
    return s


def edit(s: str, rel: str = "hooks/x.py", env: dict = ENV) -> None:
    f = PROJ / rel
    body = f"x = {uuid.uuid4().hex!r}\n"
    f.write_text(body, encoding="utf-8")
    hook(
        "post",
        s,
        env,
        tool_name="Write",
        tool_input={"file_path": str(f), "content": body},
        tool_use_id=uuid.uuid4().hex,
    )


def agent(s: str, event: str, agent_id: str, kind: str = "code-reviewer") -> None:
    hook(f"agent-{event}", s, ENV, agent_id=agent_id, agent_type=kind)


def shell(
    s: str, command: str = COMMIT, env: dict = ENV, tool: str = "Bash", **who: object
) -> tuple[str, str, str]:
    """(pre decision, pre reason, post additionalContext) of one shell call."""
    common = {
        "tool_name": tool,
        "tool_input": {"command": command},
        "tool_use_id": uuid.uuid4().hex,
        **who,
    }
    pre = hook("pre", s, env, **common).get("hookSpecificOutput", {})
    post = hook("post", s, env, tool_response={"stdout": "", "stderr": ""}, **common)
    return (
        str(pre.get("permissionDecision") or ""),
        str(pre.get("permissionDecisionReason") or ""),
        str(post.get("hookSpecificOutput", {}).get("additionalContext") or ""),
    )


def skill(s: str, name: str) -> None:
    hook(
        "post",
        s,
        ENV,
        tool_name="Skill",
        tool_input={"skill": name},
        tool_use_id=uuid.uuid4().hex,
        tool_response={},
    )


s = start()
edit(s)
decision, reason, said = shell(s)
check(
    "C-T3 a risky change without a review: the commit is refused once, naming the reviewer and /commit",
    (decision, "code-reviewer" in reason, "/commit" in reason, "한 번" in reason, said),
    ("deny", True, True, True, ""),
)
decision, reason, said = shell(s)
check("C-T4 the second attempt passes", (decision, reason, said), ("", "", ""))
decision, reason, said = shell(s, "git status")
check("C-T5 a git command that is no commit is not checked", decision, "")

s = start()
edit(s)
agent(s, "start", "r1")
agent(s, "stop", "r1")
decision, reason, said = shell(s)
check(
    "C-T6 reviewed (the reviewer ended on this content): the commit passes",
    (decision, said),
    ("", ""),
)
edit(s)
decision, reason, said = shell(s)
check(
    "C-T7 edited again after the review: stale, refused once",
    (decision, "stale" in reason or "이후" in reason),
    ("deny", True),
)

s = start()
edit(s)
agent(s, "start", "r2")
decision, reason, said = shell(s)
check(
    "C-T8 a reviewer that has not ended: refused once",
    (decision, "running" in reason or "끝나" in reason),
    ("deny", True),
)

s = start()
edit(s, "src/a.py")
decision, reason, said = shell(s)
check(
    "C-T9 a small change no review rule applies to: no check",
    (decision, said),
    ("", ""),
)

s = start()
edit(s)
decision, reason, said = shell(s, agent_id="g1", agent_type="general-purpose")
check("C-T10 a subagent's commit is not checked", (decision, said), ("", ""))

s = start()
edit(s)
decision, reason, said = shell(s, env=dict(ENV, CLAUDE_COMMIT_REVIEW="off"))
check("C-T11 off: nothing", (decision, said), ("", ""))

s = start()
edit(s)
decision, reason, said = shell(s, env=dict(ENV, CLAUDE_COMMIT_REVIEW="notify"))
check(
    "C-T12 notify: the commit passes and the note comes after it",
    (decision, "code-reviewer" in said, "/commit" in said),
    ("", True, True),
)
decision, reason, said = shell(s, env=dict(ENV, CLAUDE_COMMIT_REVIEW="notify"))
check("C-T13 notify: said once per content", said, "")
decision, reason, said = shell(
    s, "git status", env=dict(ENV, CLAUDE_COMMIT_REVIEW="notify")
)
check("C-T13 ... a note is never carried to another command", said, "")

s = start()
edit(s)
skill(s, "commit")
decision, reason, said = shell(s)
check(
    "C-T14 inside the /commit skill (recorded this turn): a note instead of a refusal, without the /commit hint",
    (decision, "code-reviewer" in said, "/commit" in said),
    ("", True, False),
)

s = start()
edit(s)
decision, reason, said = shell(s, "git commit -m 'x'", tool="PowerShell")
check("C-T15 PowerShell: the same check", decision, "deny")

print(f"\nFAILURES: {fails}")
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
sys.exit(1 if fails else 0)
