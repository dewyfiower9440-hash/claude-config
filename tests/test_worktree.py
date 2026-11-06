"""D-5: content-based change detection (run with python -I).

Real temporary git repository; files are changed on disk the way Bash would change them,
and the hook only sees a Bash PostToolUse with the command text (as in the real harness).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "discipline.py"
WORK = HERE / "_tmp_wt"
REPO = WORK / "repo"
fails = 0


def remove_tree(path: Path) -> None:
    """rmtree that also removes git's read-only object files on Windows."""
    import os
    import stat

    def clear_readonly(
        func: Callable[[str], object], target: str, _exc: BaseException
    ) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onexc=clear_readonly)


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:120],
        "" if ok else f"(want {want!r})",
    )


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def call(mode: str, session: str, **payload: object) -> dict | None:
    payload.setdefault("cwd", str(REPO))
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK), mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def stop(session: str) -> str | None:
    return (call("stop", session) or {}).get("reason")


def bash(
    session: str, command: str, *, failed: bool = False, prompt_id: str = "p1"
) -> None:
    if failed:
        call(
            "fail",
            session,
            tool_name="Bash",
            tool_input={"command": command},
            error="Exit code 1",
            prompt_id=prompt_id,
        )
    else:
        call(
            "post",
            session,
            tool_name="Bash",
            tool_input={"command": command},
            prompt_id=prompt_id,
        )


def write(rel: str, text: str) -> None:
    p = REPO / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def new_session(prompt_id: str = "p1") -> str:
    s = uuid.uuid4().hex
    call("prompt", s, prompt_id=prompt_id)
    return s


GATE = "uv run pytest -q"

remove_tree(WORK)
REPO.mkdir(parents=True)
write(
    "CLAUDE.md",
    f"## Verification\n- gate: `{GATE}`\n- ci: none\n- watch: `config/*.local.toml`\n",
)
write(".gitignore", "build/\nconfig/*.local.toml\n")
write("src/a.py", "x = 1\n")
write("README.md", "demo\n")
git("init", "-q", "-b", "main")
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "init")

# ---- the three reproduced holes
s = new_session()
write("src/a.py", "x = 2\n")
call(
    "post",
    s,
    tool_name="Edit",
    tool_input={
        "file_path": str(REPO / "src" / "a.py"),
        "old_string": "1",
        "new_string": "2",
    },
    prompt_id="p1",
)
bash(s, GATE)
write("src/a.py", "x = 3\n")
bash(s, "sed -i 's/2/3/' src/a.py")
r = stop(s)
check("A: Bash edit after the gate invalidates it -> block", r is not None, True)
check(
    "A: reason says files changed after verification",
    r is not None and "검증 이후" in r,
    True,
)

s = new_session()
write("src/b.py", "y = 1\n")
bash(s, "printf 'y = 1' > src/b.py")
check("B: Bash-only code change without a gate -> block", stop(s) is not None, True)
bash(s, GATE)
check("B: gate after the Bash change -> allowed", stop(s), None)

s = new_session()
write("src/c.py", "z = 1\n")
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "c")
bash(s, "git add -A && git commit -m c")
check(
    "C: Bash change + commit, no gate -> block (commit is not verification)",
    stop(s) is not None,
    True,
)

# ---- content semantics
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "baseline")
s = new_session()
write("src/a.py", "x = 10\n")
bash(s, "python tools/rewrite.py")
bash(s, GATE)
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "after gate")
bash(s, "git commit -am done")
check("commit after verification keeps it valid (content unchanged)", stop(s), None)
write("src/a.py", "x = 11\n")
bash(s, "sed -i s/10/11/ src/a.py")
check("later Bash edit -> block", stop(s) is not None, True)
write("src/a.py", "x = 10\n")
bash(s, "git checkout -- src/a.py")
check("reverting to the verified content -> allowed again", stop(s), None)
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "x10")

s = new_session()
write("README.md", "docs only change\n")
bash(s, "echo docs >> README.md")
check("docs-only change needs no verification", stop(s), None)

s = new_session()
write("build/out.py", "generated = 1\n")
bash(s, "python -m build")
check("ignored file change is not a code change", stop(s), None)

s = new_session()
write("config/dev.local.toml", "debug = true\n")
bash(s, "echo debug > config/dev.local.toml")
check("ignored file matching a declared watch glob -> block", stop(s) is not None, True)
bash(s, GATE)
check("... then the gate -> allowed", stop(s), None)

s = new_session()
write("src/new_mod.py", "n = 1\n")
bash(s, "touch src/new_mod.py")
check("new untracked code file -> block", stop(s) is not None, True)
bash(s, GATE + " | tail -3")
check("masked gate run does not verify it -> block", stop(s) is not None, True)
(REPO / "src" / "new_mod.py").unlink()
bash(s, "rm src/new_mod.py")
check("deleting it again restores the baseline -> allowed", stop(s), None)

# ---- change scope (turn level, Bash included)
PLAN = "# P\n\ntask: scoped\n\n## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. x\n"
s = new_session("q1")
call(
    "post",
    s,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={"plan": PLAN},
    prompt_id="q1",
)
write("src/a.py", "x = 20\n")
write("other/out_of_scope.py", "o = 1\n")
bash(s, "python gen.py", prompt_id="q1")
bash(s, GATE, prompt_id="q1")
r = stop(s)
check(
    "approved scope: Bash change outside the scope -> block",
    r is not None and "out_of_scope.py" in r,
    True,
)
(REPO / "other" / "out_of_scope.py").unlink()
bash(s, "rm other/out_of_scope.py", prompt_id="q1")
r = stop(s)
check(
    "deleting a verified file is a content change -> re-verify needed",
    r is not None and "검증 이후" in r,
    True,
)
bash(s, GATE, prompt_id="q1")
check("approved scope: only in-scope changes, gate re-run -> allowed", stop(s), None)
git("add", "-A")
git("commit", "-q", "--allow-empty", "-m", "x20")

s = new_session("r1")
for i in range(6):
    write(f"src/gen_{i}.py", f"g = {i}\n")
bash(s, "python generate_many.py", prompt_id="r1")
bash(s, GATE, prompt_id="r1")
r = stop(s)
check(
    "no approval: 6 files changed via Bash in one turn -> block",
    r is not None and "6" in r,
    True,
)

# ---- nested repository: a repo inside another repo's folder must get its own snapshot
OUTER = WORK / "outer"
INNER = OUTER / "inner"
INNER.mkdir(parents=True)
(OUTER / "o.py").write_text("o = 1\n", encoding="utf-8")
(OUTER / ".gitignore").write_text("inner/\n", encoding="utf-8")
for d in (OUTER, INNER):
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=d, check=True, capture_output=True
    )
(INNER / "CLAUDE.md").write_text(
    f"## Verification\n- gate: `{GATE}`\n- ci: none\n", encoding="utf-8"
)
(INNER / "i.py").write_text("i = 1\n", encoding="utf-8")
for d in (OUTER, INNER):
    subprocess.run(["git", "add", "-A"], cwd=d, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "-m",
            "i",
        ],
        cwd=d,
        check=True,
        capture_output=True,
    )
s = uuid.uuid4().hex
call(
    "prompt", s, prompt_id="n1", cwd=str(OUTER)
)  # the session starts in the outer repo
call(
    "pre",
    s,
    tool_name="Bash",
    tool_input={"command": "cd inner && sed -i s/1/2/ i.py"},
    prompt_id="n1",
    cwd=str(OUTER),
)
(INNER / "i.py").write_text("i = 2\n", encoding="utf-8")
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "cd inner && sed -i s/1/2/ i.py"},
    prompt_id="n1",
    cwd=str(OUTER),
)
r = (call("stop", s, prompt_id="n1", cwd=str(OUTER)) or {}).get("reason")
check(
    "nested repo: Bash edit in the inner repo is detected -> block", r is not None, True
)

remove_tree(WORK)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
