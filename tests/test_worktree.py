"""D-5: content-based change detection (run with python -I).

Real temporary git repository; files are changed on disk the way Bash would change them,
and the hook only sees a Bash PostToolUse with the command text (as in the real harness).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "discipline.py"
# Plan approvals made by these tests go to an isolated store, never the real one.
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(TMP_ROOT / "_tmp_worktree_approvals")
WORK = TMP_ROOT / "_tmp_wt"
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
    if (
        mode in ("post", "fail")
        and payload.get("tool_name") in ("Bash", "PowerShell")
        and "tool_use_id" not in payload
    ):
        # As in the real harness, every shell PostToolUse(Failure) follows its PreToolUse.
        # Tests that pass a tool_use_id drive PreToolUse themselves.
        payload["tool_use_id"] = uuid.uuid4().hex
        call("pre", session, **payload)
    p = inproc.run(HOOK, [mode], json.dumps({"session_id": session, **payload}))
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

# ---- review finding 5: nested repo inside a policy root must not make evidence permanently stale
(OUTER / "CLAUDE.md").write_text(
    f"## Verification\n- gate: `{GATE}`\n- ci: none\n", encoding="utf-8"
)
(INNER / "CLAUDE.md").unlink()
s = uuid.uuid4().hex
call("prompt", s, prompt_id="m1", cwd=str(OUTER))
call(
    "pre",
    s,
    tool_name="Bash",
    tool_input={"command": "cd inner && sed -i s/2/3/ i.py"},
    prompt_id="m1",
    cwd=str(OUTER),
)
(INNER / "i.py").write_text("i = 3\n", encoding="utf-8")
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "cd inner && sed -i s/2/3/ i.py"},
    prompt_id="m1",
    cwd=str(OUTER),
)
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": GATE},
    prompt_id="m1",
    cwd=str(OUTER),
)
check(
    "nested repo under the policy root: gate from the root covers it -> allowed",
    (call("stop", s, prompt_id="m1", cwd=str(OUTER)) or {}).get("reason"),
    None,
)

# ---- review finding 1: files changed by the same command after the check
remove_tree(REPO)
REPO.mkdir(parents=True)
write("CLAUDE.md", f"## Verification\n- gate: `{GATE}`\n- ci: none\n")
write("src/a.py", "x = 1\n")
write("requirements.txt", "requests==2.0\n")
git("init", "-q", "-b", "main")
git("add", "-A")
git("commit", "-q", "-m", "init")
s = new_session()
write("src/a.py", "x = 5\n")
bash(s, "sed -i s/1/5/ src/a.py")
chained = GATE + " && sed -i 's/5/6/' src/a.py"
call(
    "pre",
    s,
    tool_name="Bash",
    tool_input={"command": chained},
    tool_use_id="t9",
    prompt_id="p1",
)
write("src/a.py", "x = 6\n")  # the check ran on x = 5; the same command then changed it
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": chained},
    tool_use_id="t9",
    prompt_id="p1",
)
check(
    "gate && edit: content changed while the check command ran -> block",
    stop(s) is not None,
    True,
)
call(
    "pre",
    s,
    tool_name="Bash",
    tool_input={"command": GATE},
    tool_use_id="t10",
    prompt_id="p1",
)
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": GATE},
    tool_use_id="t10",
    prompt_id="p1",
)
check(
    "plain gate with pre/post snapshots, nothing changed meanwhile -> allowed",
    stop(s),
    None,
)

# ---- review finding 6: non-doc .txt files are code-relevant; README is not
write("requirements.txt", "requests==9.9\n")
bash(s, "sed -i s/2.0/9.9/ requirements.txt")
check("requirements.txt changed after the gate -> block", stop(s) is not None, True)
bash(s, GATE)
write("README.md", "only docs\n")
bash(s, "echo docs > README.md")
check("README change after the gate still needs nothing", stop(s), None)
git("add", "-A")
git("commit", "-q", "-m", "reqs")

# ---- review finding 4: switching branches is not an unapproved bulk edit
git("checkout", "-q", "-b", "feature")
for i in range(7):
    write(f"src/feat_{i}.py", f"f = {i}\n")
git("add", "-A")
os.environ["GIT_COMMITTER_DATE"] = "2026-01-01T00:00:00"  # made before this session
git("commit", "-q", "-m", "feature files")
del os.environ["GIT_COMMITTER_DATE"]
git("checkout", "-q", "main")
s = new_session("b1")
git("checkout", "-q", "feature")
bash(s, "git checkout feature", prompt_id="b1")
bash(s, GATE, prompt_id="b1")
check("git checkout bringing in 7 files is not counted as a bulk edit", stop(s), None)

# ---- second review, finding 2: committing this turn's own edits does not hide them
s = new_session("b2")
for i in range(7):
    write(f"src/own_{i}.py", f"o = {i}\n")
bash(s, "for i in 0 1 2 3 4 5 6; do echo o > src/own_$i.py; done", prompt_id="b2")
git("add", "-A")
git("commit", "-q", "-m", "own files")
bash(s, "git add -A && git commit -m own", prompt_id="b2")
bash(s, GATE, prompt_id="b2")
r = (call("stop", s, prompt_id="b2") or {}).get("reason") or ""
check(
    "7 files edited and committed in the same turn -> still a bulk edit", "7" in r, True
)

# ---- review findings 2 and 3: git time budget exhausted -> unknown (never allowed), then recovers
os.environ["CLAUDE_DISCIPLINE_GIT_BUDGET_S"] = "0"
s = uuid.uuid4().hex
call("prompt", s, prompt_id="z1")  # snapshot impossible within budget
write("src/a.py", "x = 77\n")
bash(s, "sed -i s/6/77/ src/a.py", prompt_id="z1")
r = (call("stop", s, prompt_id="z1") or {}).get("reason")
check(
    "no time left for git at Stop -> block as unknown (fail closed)",
    r is not None and "확인할 수 없습니다" in r,
    True,
)
edit_payload = {
    "tool_name": "Edit",
    "tool_input": {
        "file_path": str(REPO / "src" / "a.py"),
        "old_string": "77",
        "new_string": "78",
    },
}
call("pre", s, prompt_id="z1", **edit_payload)
write("src/a.py", "x = 78\n")
call("post", s, prompt_id="z1", **edit_payload)
del os.environ["CLAUDE_DISCIPLINE_GIT_BUDGET_S"]
call("prompt", s, prompt_id="z2")
r = (call("stop", s, prompt_id="z2") or {}).get("reason")
check(
    "second review, finding 1: git back, edits made while it was unavailable, no gate -> block",
    r is not None,
    True,
)
call("prompt", s, prompt_id="z3")
bash(s, GATE, prompt_id="z3")
check(
    "a failed first snapshot is retried later (no permanent unknown)",
    (call("stop", s, prompt_id="z3") or {}).get("reason"),
    None,
)

# ---- second review, finding 3: a check with no pre-run record is not a pass
s = new_session("m1")
write("src/a.py", "x = 79\n")
bash(s, "sed -i s/78/79/ src/a.py", prompt_id="m1")
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": GATE},
    tool_use_id="never-seen-by-pre",
    prompt_id="m1",
)
check(
    "gate with no PreToolUse record -> not a pass -> block", stop(s) is not None, True
)

# ---- second review, finding 4: output files a gate writes are not edits
s = new_session("m2")
bash(s, GATE, prompt_id="m2")
for n in range(2):  # created by the first run, rewritten by the second
    call(
        "pre",
        s,
        tool_name="Bash",
        tool_input={"command": GATE},
        tool_use_id=f"cov{n}",
        prompt_id="m2",
    )
    write("coverage.xml", f"<coverage run='{n}'/>\n")
    call(
        "post",
        s,
        tool_name="Bash",
        tool_input={"command": GATE},
        tool_use_id=f"cov{n}",
        prompt_id="m2",
    )
    check(
        f"gate writing an untracked report (run {n + 1}) -> still Locally Verified",
        stop(s),
        None,
    )
(REPO / "coverage.xml").unlink()

# ---- review finding 3: an oversized repository blocks once per session, not every turn
BIG = WORK / "big"
(BIG / "src").mkdir(parents=True)
subprocess.run(["git", "init", "-q", "-b", "main"], cwd=BIG, check=True)
for i in range(5001):  # one more than worktree.MAX_FILES
    (BIG / "src" / f"f{i}.py").write_bytes(b"")
s = uuid.uuid4().hex
call("prompt", s, prompt_id="b1", cwd=str(BIG))
first = (call("stop", s, prompt_id="b1", cwd=str(BIG)) or {}).get("reason") or ""
check(
    "repository over the file limit -> blocked as unknown",
    "확인할 수 없습니다" in first,
    True,
)
call("prompt", s, prompt_id="b2", cwd=str(BIG))
check(
    "over the file limit -> not blocked again in a later turn",
    (call("stop", s, prompt_id="b2", cwd=str(BIG)) or {}).get("reason"),
    None,
)
# A turn that runs shell commands there (edits invisible) must never end as verified.
call("prompt", s, prompt_id="b3", cwd=str(BIG))
(BIG / "src" / "f0.py").write_text("x = 1\n", encoding="utf-8")
call(
    "post",
    s,
    tool_name="Bash",
    tool_input={"command": "echo x = 1 > src/f0.py"},
    prompt_id="b3",
    cwd=str(BIG),
)
r = (call("stop", s, prompt_id="b3", cwd=str(BIG)) or {}).get("reason") or ""
check(
    "over the file limit, Bash used this turn -> blocked as unknown again",
    "확인할 수 없습니다" in r,
    True,
)

# ---- devenv-final 13: what the content digest must not miss
sys.path.insert(0, str(HOOK.parent))
import worktree as wt

RELEVANT = {
    # a folder named docs inside the source tree is code
    "src/docs/handler.py": True,
    "app/docs/api.ts": True,
    "packages/x/docs/guide.md": True,
    # source below the top-level docs folder (sphinx conf, a build script) is code
    "docs/conf.py": True,
    "docs/build.ps1": True,
    # a module named like a document is code
    "readme.py": True,
    "src/license.ts": True,
    "authors.json": True,
    "tools/changelog.toml": True,
    # documents and media stay out
    "docs/guide.md": False,
    "docs/plans/2026-11-18-x.md": False,
    "README.md": False,
    "LICENSE": False,
    "sub/CHANGELOG.txt": False,
    "NOTICE.rst": False,
    "logo.png": False,
}
check(
    "K-T1 relevant(): named like a document only without a source suffix; docs/ only at "
    "the top and never for source",
    {p: wt.relevant(p) for p in RELEVANT},
    RELEVANT,
)
BITS = WORK / "bits"
(BITS / "src").mkdir(parents=True)


def bgit(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=BITS,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


bgit("init", "-q")
for name in ("a.py", "b.py", "c.py"):
    (BITS / "src" / name).write_text("x = 0\n", encoding="utf-8")
bgit("add", "-A")
bgit("commit", "-q", "-m", "base")
base = wt.digest(wt.content_map(str(BITS)) or {})
bgit("update-index", "--assume-unchanged", "src/a.py")
(BITS / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
after_a = wt.digest(wt.content_map(str(BITS)) or {})
bgit("update-index", "--skip-worktree", "src/b.py")
(BITS / "src" / "b.py").write_text("x = 2\n", encoding="utf-8")
after_b = wt.digest(wt.content_map(str(BITS)) or {})
check(
    "K-T2 a change git is told not to look at (assume-unchanged, skip-worktree) is seen",
    (base != after_a, after_a != after_b, bgit("status", "--porcelain")),
    (True, True, ""),
)
(BITS / "src" / "b.py").unlink()
check(
    "K-T2 ... and so is its deletion",
    "src/b.py" in (wt.content_map(str(BITS)) or {"src/b.py": ""}),
    False,
)
# A file that is excluded before it exists is never seen by git: what can be seen is that
# the list of hidden names changed, and a digest taken before that no longer holds.
before_hiding = wt.digest(wt.content_map(str(BITS)) or {})
(BITS / ".git" / "info").mkdir(exist_ok=True)
with open(BITS / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
    f.write("\nsrc/hidden.py\n")
hiding = wt.digest(wt.content_map(str(BITS)) or {})
(BITS / "src" / "hidden.py").write_text("h = 1\n", encoding="utf-8")
check(
    "K-T3 a name added to .git/info/exclude changes the digest; the file it hides "
    "afterwards is the limit of what git shows",
    (before_hiding != hiding, hiding == wt.digest(wt.content_map(str(BITS)) or {})),
    (True, True),
)
LINKED = WORK / "bits-linked"
bgit("worktree", "add", "-q", "--detach", str(LINKED))
check(
    "K-T3 ... also read from a linked work tree (the exclude file is the repository's)",
    wt.exclude_file(str(LINKED)) == wt.exclude_file(str(BITS))
    and os.path.isfile(wt.exclude_file(str(LINKED)) or ""),
    True,
)
INNER = WORK / "inner-src"
INNER.mkdir()
subprocess.run(["git", "init", "-q"], cwd=INNER, check=True)
(INNER / "m.py").write_text("m = 0\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=INNER, check=True)
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
        "m",
    ],
    cwd=INNER,
    check=True,
)
bgit("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(INNER), "vendor")
bgit("commit", "-q", "-m", "submodule")
clean = wt.content_map(str(BITS))
(BITS / "vendor" / "m.py").write_text("m = 1\n", encoding="utf-8")
dirty = wt.content_map(str(BITS))
check(
    "K-T4 a clean submodule is one entry of the map; a dirty one makes the content unknown",
    (clean is not None and "vendor" in clean, dirty, wt.last_error),
    (True, None, "unavailable"),
)

# ---- devenv-final 30: the work tree of a folder is found without a git call, and is the
# one git names
import tempfile

FIND = WORK / "find"
(FIND / "repo" / "a" / "b").mkdir(parents=True)
(FIND / "plain" / "x").mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=FIND / "repo", check=True)
subprocess.run(
    ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
     "--allow-empty", "-m", "c"],
    cwd=FIND / "repo",
    check=True,
)  # fmt: skip
subprocess.run(
    ["git", "worktree", "add", "-q", "--detach", str(FIND / "linked")],
    cwd=FIND / "repo",
    check=True,
)
OUTSIDE_ALL = Path(tempfile.mkdtemp(prefix="wt-norepo-"))


def git_top(folder: Path) -> str | None:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,  # not a repository: no output, which is the answer
    ).stdout.strip()
    return os.path.normcase(os.path.normpath(out)) if out else None


def found_top(path: Path) -> str | None:
    top = wt.toplevel(str(path))
    return os.path.normcase(top) if top else None


check(
    "K-T6 toplevel(): the same answer as git for a folder deep in a repository, a file "
    "that does not exist yet, a linked work tree, and a folder in no repository",
    (
        found_top(FIND / "repo" / "a" / "b") == git_top(FIND / "repo" / "a" / "b"),
        found_top(FIND / "repo" / "a" / "new" / "f.py") == git_top(FIND / "repo" / "a"),
        found_top(FIND / "linked") == git_top(FIND / "linked"),
        found_top(FIND / "linked") != found_top(FIND / "repo"),
        found_top(OUTSIDE_ALL),
    ),
    (True, True, True, True, None),
)
check(
    "K-T6 ... and it is decided without git except where the folders cannot tell (inside "
    "a .git folder, or with GIT_DIR set)",
    (
        isinstance(wt.find_toplevel(str(FIND / "repo" / "a")), str),
        wt.find_toplevel(str(OUTSIDE_ALL)),
        wt.find_toplevel(str(FIND / "repo" / ".git" / "refs")),
    ),
    (True, None, False),
)
(FIND / "bogus" / ".git").mkdir(parents=True)  # a folder named .git, no repository
(FIND / "bogusfile").mkdir()
(FIND / "bogusfile" / ".git").write_text("not a pointer\n", encoding="utf-8")
check(
    "K-T6 ... a .git entry that is no repository (no HEAD, no gitdir line): asked of git, "
    "whose answer toplevel() gives",
    (
        wt.find_toplevel(str(FIND / "bogus")),
        wt.find_toplevel(str(FIND / "bogusfile")),
        found_top(FIND / "bogus") == git_top(FIND / "bogus"),
    ),
    (False, False, True),
)
os.environ["GIT_DIR"] = str(FIND / "repo" / ".git")
check(
    "K-T6 ... GIT_DIR set: asked of git", wt.find_toplevel(str(FIND / "plain")), False
)
del os.environ["GIT_DIR"]
shutil.rmtree(OUTSIDE_ALL, ignore_errors=True)

remove_tree(WORK)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
