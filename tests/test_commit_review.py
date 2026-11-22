"""v1.1 step 2: a `git commit` of a change that needs an independent review and has none
is refused (CLAUDE_COMMIT_REVIEW=block, the default), with the reviewer and the /commit
skill named. devenv-v1-2 step 2: it is refused on every attempt; only a commit message with
a `Review-Skipped: <reason>` trailer passes, and the skip is said after it. `notify` says it
after the commit instead; `off` does nothing.

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
HOME_DIR = os.path.normcase(os.path.normpath(os.path.expanduser("~/proj")))
check(
    "C-T1b (review finding 1) `git -C` takes a Git Bash path, ~ and an absolute path as the shell does",
    [
        norm(commits("git -C /c/dev/x commit -m y", "bash", HERE_CWD)),
        norm(commits("git -C ~/proj commit -m y", "bash", HERE_CWD)),
        norm(commits("git -C D:/w commit -m y", "bash", HERE_CWD)),
    ],
    [
        [os.path.normcase(os.path.normpath("C:/dev/x"))],
        [HOME_DIR],
        [os.path.normcase(os.path.normpath("D:/w"))],
    ],
)
check(
    "C-T2 the mode: unset and junk are block; notify and off are themselves",
    [mode_of(None), mode_of("notify"), mode_of("OFF"), mode_of("x"), mode_of("")],
    ["block", "notify", "off", "block", "block"],
)

# devenv-v1-2 step 2: the commit message, as the command line gives it
calls = getattr(disc, "commit_calls", lambda command, dialect, cwd: [])
skipped = getattr(disc, "review_skipped_reason", lambda text: "missing")


def msgs(command: str, dialect: str = "bash") -> list[tuple[str | None, str | None]]:
    return [(c.message, c.message_file) for c in calls(command, dialect, HERE_CWD)]


MSG_FILE = os.path.normcase(os.path.normpath("C:/dev/sub/msg.txt"))
check(
    "C-T16 the message from -m (repeated, joined), --message=, -am, -F <file>, -F - with a heredoc or a pipe",
    [
        msgs('git commit -m "a" -m "Review-Skipped: r"'),
        msgs("git commit --message=a"),
        msgs("git commit -am 'a b'"),
        msgs("git commit -ma"),
        [
            (m, os.path.normcase(os.path.normpath(f)) if f else f)
            for m, f in msgs("cd sub && git commit -F msg.txt")
        ],
        msgs("git commit -F - <<'EOF'\nsubj\n\nReview-Skipped: hurry\nEOF\n"),
        msgs("printf 'subj' | git commit -F -"),
        msgs("@'\nsubj\n\nReview-Skipped: ps\n'@ | git commit -F -", "powershell"),
        msgs("git commit -q"),
    ],
    [
        [("a\n\nReview-Skipped: r", None)],
        [("a", None)],
        [("a b", None)],
        [("a", None)],
        [(None, MSG_FILE)],
        [("subj\n\nReview-Skipped: hurry", None)],
        [("subj", None)],
        [("subj\n\nReview-Skipped: ps", None)],
        [(None, None)],
    ],
)
check(
    "C-T16b (review 2) options as git reads them: clusters stop at a letter that takes a value, other values are skipped, --trailer counts",
    [
        [
            (m, os.path.normcase(os.path.normpath(f)) if f else f)
            for m, f in msgs('git commit -Fm "Review-Skipped: x"')
        ],
        msgs("git commit -mF x"),
        msgs('git commit -Cm "Review-Skipped: x"'),
        msgs('git commit --author "-mReview-Skipped: x" -m y'),
        msgs('git commit -t "-mReview-Skipped: x" -m y'),
        msgs('git commit --trailer "Review-Skipped: x" -m y'),
        msgs("git commit --trailer=Review-Skipped:x -m y"),
        msgs("git commit -sm y"),
        msgs("git commit -uno -m y"),
        msgs('git commit --auth "-mReview-Skipped: x" -m y'),
        msgs('git commit --mess "Review-Skipped: x"'),
        msgs('git commit --trailer "Review-Skipped=x" -m y'),
        msgs("git commit -m --dry-run"),
        msgs("git commit --dry -m y"),
    ],
    [
        [(None, os.path.normcase(os.path.normpath("C:/dev/m")))],
        [("F", None)],
        [(None, None)],
        [("y", None)],
        [("y", None)],
        [("Review-Skipped: x\n\ny", None)],
        [("Review-Skipped: x\n\ny", None)],
        [("y", None)],
        [("y", None)],
        [("y", None)],
        [("Review-Skipped: x", None)],
        [("Review-Skipped: x\n\ny", None)],
        [("--dry-run", None)],
        [],
    ],
)
check(
    "C-T16e (re-review 2) a `-F` file the same line writes first: the message is unknown",
    msgs("printf 'feat: y' > msg.txt && git commit -F msg.txt"),
    [(None, None)],
)
check(
    "C-T16c (review 4a, 5) a pipe: a heredoc into cat counts; a command whose output is unknown gives no message",
    [
        msgs("cat <<'EOF' | git commit -F -\nsubj\n\nReview-Skipped: y\nEOF\n"),
        msgs('grep "Review-Skipped: x" f | git commit -F -'),
        msgs("echo -e 'subj' | git commit -F -"),
        msgs("printf 'Review-Skipped: %s' '' | git commit -F -"),
        msgs("printf 'feat' 'Review-Skipped: x' | git commit -F -"),
        msgs("git commit --dry-run --no-dry-run -m feat"),
    ],
    [
        [("subj\n\nReview-Skipped: y", None)],
        [(None, None)],
        [("subj", None)],
        [(None, None)],
        [(None, None)],
        [("feat", None)],
    ],
)
check(
    "C-T16d `git -C sub commit -F rel` reads the file where git runs",
    [
        (m, os.path.normcase(os.path.normpath(str(f))))
        for m, f in msgs("git -C sub commit -F msg.txt")
    ],
    [(None, MSG_FILE)],
)
check(
    "C-T17 the trailer: a reason after `Review-Skipped:` on its own line; none, empty or mid-line is no trailer",
    [
        skipped("subj\n\nReview-Skipped: docs only"),
        skipped("subj\n\nreview-skipped:  typo fix "),
        skipped("subj\n\nReview-Skipped:"),
        skipped("subj mentions Review-Skipped: x"),
        skipped("subj"),
        skipped(None),
    ],
    ["docs only", "typo fix", None, None, None, None],
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
    "C-T3 a risky change without a review: the commit is refused, naming the reviewer, /commit and the trailer",
    (
        decision,
        "code-reviewer" in reason,
        "/commit" in reason,
        "Review-Skipped:" in reason,
        "다시 실행하면 통과" in reason,
        said,
    ),
    ("deny", True, True, True, False, ""),
)
decision, reason, said = shell(s)
check(
    "C-T4 (devenv-v1-2) the same commit again is refused again",
    (decision, "Review-Skipped:" in reason, said),
    ("deny", True, ""),
)
decision, reason, said = shell(s, 'git commit -m "x" -m "Review-Skipped: docs only"')
check(
    "C-T4b with a `Review-Skipped:` trailer it passes, and the skip is said after it",
    (decision, "docs only" in said, "리뷰 생략" in said),
    ("", True, True),
)
decision, reason, said = shell(s, "git commit -m 'x' -m 'Review-Skipped:'")
check("C-T4c an empty reason is no trailer: refused", decision, "deny")
decision, reason, said = shell(
    s, "git commit -F - <<'EOF'\nx\n\nReview-Skipped: heredoc\nEOF\n"
)
check(
    "C-T4d the trailer in a `-F -` heredoc: passes",
    (decision, "heredoc" in said),
    ("", True),
)
(PROJ / "msg.txt").write_text("x\n\nReview-Skipped: from file\n", encoding="utf-8")
decision, reason, said = shell(s, "git commit -F msg.txt")
check(
    "C-T4e the trailer in a `-F <file>`: passes",
    (decision, "from file" in said),
    ("", True),
)
(PROJ / "msg.txt").unlink()
decision, reason, said = shell(s, "git commit -F msg.txt")
check("C-T4f a `-F <file>` that cannot be read: refused", decision, "deny")
decision, reason, said = shell(
    s, 'git commit -m a -m "Review-Skipped: x" && git commit -m b'
)
check(
    "C-T4g two commits on one line: the one without a trailer is refused",
    decision,
    "deny",
)
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
    "C-T7 edited again after the review: stale, refused",
    (decision, "stale" in reason or "이후" in reason),
    ("deny", True),
)

s = start()
edit(s)
agent(s, "start", "r2")
decision, reason, said = shell(s)
check(
    "C-T8 a reviewer that has not ended: refused",
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
    "C-T14 (devenv-v1-2, user decision) inside the /commit skill: refused too, without the /commit hint",
    (decision, "code-reviewer" in reason, "/commit" in reason, said),
    ("deny", True, False, ""),
)
decision, reason, said = shell(s, 'git commit -m x -m "Review-Skipped: in skill"')
check(
    "C-T14b inside the skill, a trailer passes as anywhere",
    (decision, "in skill" in said),
    ("", True),
)
s = start()
edit(s)
skill(s, "commit")
decision, reason, said = shell(s, env=dict(ENV, CLAUDE_COMMIT_REVIEW="notify"))
check(
    "C-T14c notify inside the skill: a note without the /commit hint",
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
