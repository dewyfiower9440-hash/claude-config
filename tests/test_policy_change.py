"""devenv-final 11c: a gate declaration that changes during a session.

    python -I tests/test_policy_change.py

The gate a session is judged by is the one it found when it first acted in a project. When
the declaration changes afterwards (the gate line edited, or a CLAUDE.md with its own gate
created below the real one), the new gate alone is not a pass: the old one must pass on the
same content too. Throw-away repositories outside %TEMP%, an isolated temp folder and
approval home; the hook is run as a process, as the harness runs it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "discipline.py"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_policy_change"
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
BASE = WORK_ROOT / "_policy_change_proj"
for p in (ROOT, BASE):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(ROOT / "home")
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
ENV = dict(os.environ, TMPDIR=str(TMPD), TEMP=str(TMPD), TMP=str(TMPD))
OLD, WEAK = "uv run pytest -q --cov", "true"
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


def call(mode: str, session: str, **payload: object) -> dict | None:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK), mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=ENV,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def write(session: str, path: Path, text: str) -> None:
    """One Write tool call as the harness reports it: pre, the file changes, post."""
    ti = {"file_path": str(path), "content": text}
    use = uuid.uuid4().hex
    call(
        "pre",
        session,
        tool_name="Write",
        tool_input=ti,
        tool_use_id=use,
        cwd=str(path.parent),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    call(
        "post",
        session,
        tool_name="Write",
        tool_input=ti,
        tool_use_id=use,
        cwd=str(path.parent),
    )


def bash(session: str, command: str, cwd: Path) -> None:
    """A shell command that succeeded (the hook only reads the event; nothing is run)."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": uuid.uuid4().hex,
        "cwd": str(cwd),
    }
    call("pre", session, **payload)
    call("post", session, **payload)


def stop(session: str) -> str | None:
    return (call("stop", session) or {}).get("reason")


def project(name: str, gate: str = OLD) -> Path:
    proj = BASE / name
    (proj / "src").mkdir(parents=True)
    (proj / "sub").mkdir()
    (proj / "CLAUDE.md").write_text(policy(gate), encoding="utf-8")
    (proj / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
    (proj / "sub" / "b.py").write_text("b = 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    return proj


def policy(gate: str) -> str:
    return f"# p\n\n## Verification\n- gate: `{gate}`\n- ci: none\n"


def begin(proj: Path) -> str:
    session = uuid.uuid4().hex
    call("prompt", session, cwd=str(proj), prompt="work")
    return session


# ---- nothing changes: the declared gate is enough (the baseline of every case below)
P = project("same")
s = begin(P)
write(s, P / "src" / "a.py", "a = 1\n")
check("C-T1 an edit without a gate run blocks", stop(s) is not None, True)
bash(s, OLD, P)
check("C-T1 the declared gate, run at the root, verifies it", stop(s), None)

# ---- the gate line is weakened in the session
P = project("weakened")
s = begin(P)
write(s, P / "src" / "a.py", "a = 1\n")
write(s, P / "CLAUDE.md", policy(WEAK))
bash(s, WEAK, P)
reason = stop(s) or ""
check(
    "C-T2 the weakened gate alone does not verify; the reason names the old gate",
    (bool(reason), OLD in reason),
    (True, True),
)
bash(s, OLD, P)
check("C-T2 the old gate passing on the same content completes it", stop(s), None)
write(s, P / "src" / "a.py", "a = 2\n")
bash(s, WEAK, P)
check(
    "C-T2 after another edit both are needed again (the old pass was for other content)",
    stop(s) is not None,
    True,
)
bash(s, OLD, P)
bash(s, WEAK, P)
check("C-T2 ... in either order", stop(s), None)

# ---- a CLAUDE.md with its own gate is created below the real one
P = project("nested")
s = begin(P)
write(s, P / "sub" / "b.py", "b = 1\n")
write(s, P / "sub" / "CLAUDE.md", policy(WEAK))
write(s, P / "sub" / "b.py", "b = 2\n")
bash(s, WEAK, P / "sub")
reason = stop(s) or ""
check(
    "C-T3 the new declaration's gate alone does not verify the files it took over",
    (bool(reason), OLD in reason),
    (True, True),
)
bash(s, OLD, P)
check("C-T3 the enclosing project's gate, run at its root, completes it", stop(s), None)

# ---- a legitimate change (a stricter gate): the same rule, and it can be met
P = project("stricter", gate="uv run pytest -q")
s = begin(P)
write(s, P / "src" / "a.py", "a = 1\n")
write(s, P / "CLAUDE.md", policy(OLD))
bash(s, "uv run pytest -q", P)
bash(s, OLD, P)
check("C-T4 old gate then new gate on the same content: verified", stop(s), None)

# ---- what is not a change
P = project("preexisting")
(P / "sub" / "CLAUDE.md").write_text(policy(WEAK), encoding="utf-8")
s = begin(P)
bash(s, "git status", P)  # the session acts in the enclosing project first
write(s, P / "sub" / "b.py", "b = 1\n")
bash(s, WEAK, P / "sub")
check(
    "C-T5 a nested declaration that was there before the session is judged by its own gate",
    stop(s),
    None,
)
P = project("declared-later")
(P / "CLAUDE.md").unlink()
s = begin(P)
write(s, P / "src" / "a.py", "a = 1\n")
write(s, P / "CLAUDE.md", policy(OLD))
bash(s, OLD, P)
check("C-T6 a first declaration (none before) is not a changed one", stop(s), None)
s2 = begin(BASE / "weakened")
write(s2, BASE / "weakened" / "src" / "a.py", "a = 3\n")
bash(s2, WEAK, BASE / "weakened")
check(
    "C-T7 a new session takes the declaration as it finds it (the user saw the change)",
    stop(s2),
    None,
)


# ---- devenv-final 12: a failed or unknown result is reported once, for the content it
# was about. Changing the code afterwards without running the gate again is a new
# unverified state, not "already reported".
def failed_bash(session: str, command: str, cwd: Path) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": uuid.uuid4().hex,
        "cwd": str(cwd),
    }
    call("pre", session, **payload)
    call("fail", session, error="Exit code 1", **payload)


for label, run_gate in (
    ("failed", lambda s, p: failed_bash(s, OLD, p)),
    ("unknown", lambda s, p: bash(s, OLD + " | tail -3", p)),
):
    P = project(f"stale-{label}")
    s = begin(P)
    write(s, P / "src" / "a.py", "a = 1\n")
    run_gate(s, P)
    check(f"D-T1 a {label} gate blocks the stop", stop(s) is not None, True)
    call("prompt", s, cwd=str(P), prompt="go on")
    check(
        "D-T1 ... once: reported, nothing changed, the next stop is quiet",
        stop(s),
        None,
    )
    call("prompt", s, cwd=str(P), prompt="fix it")
    (P / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")  # by a script, no Edit
    bash(s, "python fix.py", P)
    check(
        f"D-T2 the code changed after the reported {label} result, no new run: blocked again",
        stop(s) is not None,
        True,
    )
    call("prompt", s, cwd=str(P), prompt="again")
    check(
        "D-T2 ... and at every stop until the gate runs on this content",
        stop(s) is not None,
        True,
    )
    bash(s, OLD, P)
    check("D-T2 ... the gate passing on the new content ends it", stop(s), None)

for p in (ROOT, BASE):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
