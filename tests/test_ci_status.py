"""D-1b: CI verification tests (run with python -I).

Uses a real temporary git repository (no network) and a fake `gh` placed first on PATH.
Covers the CI half of FC-1 (local gate green, CI not confirmed / failing) and FC-3
(a run that is not the pushed commit's push-triggered workflow must not count).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "discipline.py"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
WORK = TMP_ROOT / "_tmp_ci"
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


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def remove_tree(path: Path) -> None:
    """rmtree that also removes git's read-only object files (Windows refuses those)."""

    def writable_then_retry(
        func: Callable[[str], object], p: str, exc: BaseException
    ) -> None:
        if not isinstance(exc, PermissionError):
            raise exc
        os.chmod(p, 0o700)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=writable_then_retry)


# ---- fixture: repo with a declared policy (ci: required) and two push workflows
remove_tree(WORK)
REPO = WORK / "repo"
(REPO / ".github" / "workflows").mkdir(parents=True)
(REPO / "src").mkdir()
(REPO / "CLAUDE.md").write_text(
    "# demo\n\n## Verification\n- gate: `uv run pytest -q`\n- ci: required\n",
    encoding="utf-8",
)
(REPO / ".github" / "workflows" / "ci.yml").write_text(
    "name: ci\non: [push, pull_request]\njobs: {}\n", encoding="utf-8"
)
(REPO / ".github" / "workflows" / "lint.yml").write_text(
    "name: lint\non:\n  push:\n    branches: [main]\njobs: {}\n", encoding="utf-8"
)
(REPO / ".github" / "workflows" / "nightly.yml").write_text(
    "name: nightly\non:\n  schedule:\n    - cron: '0 0 * * *'\njobs: {}\n",
    encoding="utf-8",
)
(REPO / "src" / "m.py").write_text("x = 1\n", encoding="utf-8")
git(REPO, "init", "-q", "-b", "main")
git(REPO, "remote", "add", "origin", "https://github.com/acme/demo.git")
git(REPO, "add", "-A")
git(REPO, "commit", "-q", "-m", "init")
SHA = git(REPO, "rev-parse", "HEAD")

FAKE_BIN = WORK / "bin"
FAKE_BIN.mkdir()
(FAKE_BIN / "gh.cmd").write_text(
    f'@echo off\r\n"{sys.executable}" -I "{HERE / "fake_gh.py"}" %*\r\n',
    encoding="utf-8",
)
SCENARIO = WORK / "scenario.json"
# The hook's approval store and its witness: this run's own, never the real ~/.claude ones.
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(WORK / "home")
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(WORK / "w" / "hwm")
os.environ["FAKE_GH_SCENARIO"] = str(SCENARIO)
os.environ["PATH"] = str(FAKE_BIN) + os.pathsep + os.environ["PATH"]


def scenario(
    runs: list[dict], jobs: dict[str, list[dict]] | None = None, fail: bool = False
) -> None:
    SCENARIO.write_text(
        json.dumps({"runs": runs, "jobs": jobs or {}, "fail": fail}), encoding="utf-8"
    )


def run(
    rid: int,
    path: str,
    *,
    conclusion: str = "success",
    status: str = "completed",
    event: str = "push",
    sha: str | None = None,
    branch: str = "main",
    repo: str = "acme/demo",
) -> dict:
    return {
        "id": rid,
        "name": Path(path).stem,
        "path": path,
        "event": event,
        "head_branch": branch,
        "head_sha": sha
        or SHA,  # default: the commit made by the latest locally_verified()
        "status": status,
        "conclusion": conclusion,
        "run_attempt": 1,
        "repository": {"full_name": repo},
    }


GREEN_JOBS = {
    "1": [{"name": "test", "conclusion": "success"}],
    "2": [{"name": "lint", "conclusion": "success"}],
}


def call(mode: str, session: str, **payload: object) -> dict | None:
    if (
        mode in ("post", "fail")
        and payload.get("tool_name") in ("Bash", "PowerShell")
        and "tool_use_id" not in payload
    ):
        # As in the real harness, every shell PostToolUse(Failure) follows its PreToolUse.
        payload["tool_use_id"] = uuid.uuid4().hex
        call("pre", session, **payload)
    p = inproc.run(HOOK, [mode], json.dumps({"session_id": session, **payload}))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def bash(
    session: str, command: str, *, failed: bool = False, cwd: Path | None = None
) -> None:
    if failed:
        call(
            "fail",
            session,
            tool_name="Bash",
            tool_input={"command": command},
            cwd=str(cwd or REPO),
            error="Exit code 1",
        )
    else:
        call(
            "post",
            session,
            tool_name="Bash",
            tool_input={"command": command},
            cwd=str(cwd or REPO),
        )


def stop(session: str, **kw: object) -> str | None:
    out = call("stop", session, **kw)
    return (out or {}).get("reason")


def state(session: str) -> dict:
    return json.loads(
        (
            Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
        ).read_text(encoding="utf-8")
    )


EDITS = [0]


def locally_verified() -> str:
    """A real edit on disk, committed (so a push contains it), then the declared gate passes."""
    global SHA
    s = uuid.uuid4().hex
    call("prompt", s, cwd=str(REPO))
    EDITS[0] += 1
    (REPO / "src" / "m.py").write_text(f"x = {EDITS[0] + 100}\n", encoding="utf-8")
    call(
        "post",
        s,
        tool_name="Edit",
        tool_input={
            "file_path": str(REPO / "src" / "m.py"),
            "old_string": "1",
            "new_string": "2",
        },
        cwd=str(REPO),
    )
    git(REPO, "add", "-A")
    git(REPO, "commit", "-q", "-m", f"edit {EDITS[0]}")
    SHA = git(REPO, "rev-parse", "HEAD")
    bash(s, "uv run pytest -q")
    return s


# ---- FC-1: local gate green is not enough when ci: required
s = locally_verified()
r = stop(s)
check("FC-1: gate passed, not pushed -> block", r is not None and "CI" in r, True)
check(
    "FC-1: not-pushed block happens once (stop_hook_active)",
    stop(s, stop_hook_active=True),
    None,
)
call("prompt", s)
check("FC-1: not-pushed block not repeated next turn", stop(s), None)

s = locally_verified()
scenario(
    [run(1, ".github/workflows/ci.yml"), run(2, ".github/workflows/lint.yml")],
    GREEN_JOBS,
)
bash(s, "git push origin main")
r = stop(s)
check("pushed but CI never checked -> block", r is not None and "CI" in r, True)
bash(s, "gh run watch 1 --exit-status")
check("all required push workflows green for the pushed SHA -> allowed", stop(s), None)
ci = next(iter(state(s)["ci"].values()))
check(
    "CI evidence records repo/branch/sha",
    (ci["repo"], ci["branch"], ci["sha"]),
    ("acme/demo", "main", SHA),
)
check(
    "CI evidence records run ids and conclusions",
    sorted((x["id"], x["conclusion"]) for x in ci["runs"]),
    [(1, "success"), (2, "success")],
)
check(
    "schedule-only workflow is not required",
    sorted(x["path"] for x in ci["runs"]),
    [".github/workflows/ci.yml", ".github/workflows/lint.yml"],
)

s = locally_verified()
scenario(
    [
        run(1, ".github/workflows/ci.yml", conclusion="failure"),
        run(2, ".github/workflows/lint.yml"),
    ],
    GREEN_JOBS,
)
bash(s, "git push origin main")
bash(s, "gh run watch 1 --exit-status", failed=True)
r = stop(s)
check("FC-1: CI run failed -> block", r is not None and "실패" in r, True)
check("CI failure reason cites the run id", r is not None and "1" in r, True)

s = locally_verified()
scenario([run(1, ".github/workflows/ci.yml")], GREEN_JOBS)
bash(s, "git push origin main")
bash(s, "gh run view 1")
check("one of two required workflows missing -> block", stop(s) is not None, True)

s = locally_verified()
scenario(
    [
        run(1, ".github/workflows/ci.yml", status="in_progress", conclusion=""),
        run(2, ".github/workflows/lint.yml"),
    ],
    GREEN_JOBS,
)
bash(s, "git push origin main")
bash(s, "gh run list")
check("required run still in progress -> block", stop(s) is not None, True)

# c80-followup step 1: /commit and /implement watch CI in the background and record the
# verdict with a short foreground `gh run view` once the watch has ended.
s = locally_verified()
scenario(
    [
        run(1, ".github/workflows/ci.yml", status="in_progress", conclusion=""),
        run(2, ".github/workflows/lint.yml"),
    ],
    GREEN_JOBS,
)
bash(s, "git push origin main")
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "gh run watch 1 --exit-status", "run_in_background": True},
    tool_response={"backgroundTaskId": "bg1", "stdout": "", "stderr": ""},
    cwd=str(REPO),
)
check(
    "a background watch started while CI runs: still pending at the Stop -> block",
    stop(s) is not None,
    True,
)
call("prompt", s, cwd=str(REPO))
scenario(
    [run(1, ".github/workflows/ci.yml"), run(2, ".github/workflows/lint.yml")],
    GREEN_JOBS,
)
bash(s, "gh run view 1 --json status,conclusion")
check(
    "the foreground `gh run view` after the watch ended records the green verdict -> allowed",
    stop(s),
    None,
)

s = locally_verified()
scenario(
    [run(1, ".github/workflows/ci.yml"), run(2, ".github/workflows/lint.yml")],
    {
        "1": [
            {"name": "test", "conclusion": "success"},
            {"name": "e2e", "conclusion": "skipped"},
        ],
        "2": GREEN_JOBS["2"],
    },
)
bash(s, "git push origin main")
bash(s, "gh run watch 1 --exit-status")
check("a skipped job means not all jobs passed -> block", stop(s) is not None, True)

# ---- FC-3: runs that are not this push's workflow runs must not count
s = locally_verified()
scenario(
    [
        run(9, "dynamic/dependabot/dependabot-updates", event="dynamic"),
        run(1, ".github/workflows/ci.yml", event="pull_request"),
        run(2, ".github/workflows/lint.yml", branch="dependabot/x"),
    ],
    {"9": [{"name": "x", "conclusion": "success"}], **GREEN_JOBS},
)
bash(s, "git push origin main")
bash(s, "gh run watch 9 --exit-status")
check(
    "FC-3: Dependabot/PR/other-branch runs do not verify the push -> block",
    stop(s) is not None,
    True,
)

s = locally_verified()
scenario(
    [
        run(1, ".github/workflows/ci.yml", repo="evil/fork"),
        run(2, ".github/workflows/lint.yml"),
    ],
    GREEN_JOBS,
)
bash(s, "git push origin main")
bash(s, "gh run watch 1 --exit-status")
check("FC-3: run from another repository -> block", stop(s) is not None, True)

s = locally_verified()
scenario(
    [
        run(1, ".github/workflows/ci.yml", sha="0" * 40),
        run(2, ".github/workflows/lint.yml"),
    ],
    GREEN_JOBS,
)
bash(s, "git push origin main")
bash(s, "gh run watch 1 --exit-status")
check("FC-3: run for another commit -> block", stop(s) is not None, True)

s = locally_verified()
scenario([], fail=True)
bash(s, "git push origin main")
bash(s, "gh run watch 1 --exit-status")
r = stop(s)
check(
    "gh API failure -> block as unknown, not success", r is not None and "CI" in r, True
)

# ---- a push that does not contain the edit
s = locally_verified()
scenario(
    [run(1, ".github/workflows/ci.yml"), run(2, ".github/workflows/lint.yml")],
    GREEN_JOBS,
)
(REPO / "src" / "m.py").write_text(
    "x = 3\n", encoding="utf-8"
)  # uncommitted change in the project
bash(s, "git push origin main")
bash(s, "gh run watch 1 --exit-status")
check(
    "push with uncommitted project changes does not verify them -> block",
    stop(s) is not None,
    True,
)
git(REPO, "checkout", "--", "src/m.py")

# ---- devenv-final 26: a push is git's subcommand `push`, not the word among its arguments
s = locally_verified()
bash(s, "git stash push -m wip")
bash(s, "git log --oneline push-notes")
check(
    "`git stash push` and a ref named like it are no push of the branch",
    state(s).get("pushes") or {},
    {},
)
bash(s, "git -c core.quotepath=false push origin main")
check(
    "`git -c x=y push` is one",
    [p.get("sha") for p in (state(s).get("pushes") or {}).values()],
    [SHA],
)

# ---- devenv-v1-2 step 1: `git -C <dir> push` records the repository git pushes from,
# not the session folder's (a work tree on another branch used to be recorded)
OTHER = WORK / "wt"
OTHER.mkdir()
git(OTHER, "init", "-q", "-b", "devenv-final")
git(OTHER, "remote", "add", "origin", "https://github.com/acme/other.git")
(OTHER / "f.txt").write_text("x\n", encoding="utf-8")
git(OTHER, "add", "-A")
git(OTHER, "commit", "-q", "-m", "init")
TOP = os.path.normpath(git(REPO, "rev-parse", "--show-toplevel"))
OTHER_TOP = os.path.normpath(git(OTHER, "rev-parse", "--show-toplevel"))


def pushed(session: str) -> dict[str, tuple[str, str]]:
    return {
        top: (str(p.get("repo")), str(p.get("branch")))
        for top, p in (state(session).get("pushes") or {}).items()
    }


s = locally_verified()
bash(s, f'git -C "{REPO}" push origin main', cwd=OTHER)
check(
    "`git -C <repo> push` from a work tree on another branch -> that repo's branch",
    pushed(s),
    {TOP: ("acme/demo", "main")},
)

s = locally_verified()
msys = "/" + str(REPO)[0].lower() + str(REPO)[2:].replace("\\", "/")
bash(s, f"git -C {msys} push origin main", cwd=OTHER)
check(
    "`git -C /c/...` (Git Bash path) -> resolved",
    pushed(s),
    {TOP: ("acme/demo", "main")},
)

s = locally_verified()
bash(s, "git -C ../repo push origin main", cwd=OTHER)
check(
    "`git -C ../repo` (relative) -> resolved", pushed(s), {TOP: ("acme/demo", "main")}
)

s = locally_verified()
bash(s, f'cd "{REPO}" && git push origin main', cwd=OTHER)
check("`cd <repo> && git push` -> as before", pushed(s), {TOP: ("acme/demo", "main")})

s = locally_verified()
bash(s, "git push origin devenv-final", cwd=OTHER)
check(
    "plain `git push` in the session folder -> that folder's branch",
    pushed(s),
    {OTHER_TOP: ("acme/other", "devenv-final")},
)

# ---- ci: none needs no CI
(REPO / "CLAUDE.md").write_text(
    "# demo\n\n## Verification\n- gate: `uv run pytest -q`\n- ci: none\n",
    encoding="utf-8",
)
s = locally_verified()
check("ci: none -> locally verified is enough", stop(s), None)

remove_tree(WORK)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
