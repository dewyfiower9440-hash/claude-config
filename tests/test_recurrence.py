"""P2-04: the kind of a failed gate or CI run is counted per repository, and a trap-like
kind (encoding, environment, timeout) that came back in several sessions is pointed out at
the next session start as a Gotchas candidate. Nothing is written to any CLAUDE.md.

    python -I tests/test_recurrence.py

Throw-away repositories outside %TEMP%, an isolated temp folder and approval home.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = HERE / "_tmp_recurrence"
PROJ = Path("C:/dev/_recur_proj")
OTHER = Path("C:/dev/_recur_other")
for p in (ROOT, PROJ, OTHER):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
os.environ.pop("CLAUDE_RECURRENCE", None)
sys.path.insert(0, str(HOOKS))
try:
    import recurrence
except ImportError:  # before the module exists: every check below fails, none crashes
    recurrence = None

ENV = dict(os.environ, TMPDIR=str(TMPD), TEMP=str(TMPD), TMP=str(TMPD))
OFF = dict(ENV, CLAUDE_RECURRENCE="0")
fails = 0
DAY = 86400.0
NOW = 1_800_000_000.0


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


def call(fn: str, *args: Any) -> Any:
    return getattr(recurrence, fn)(*args) if hasattr(recurrence, fn) else None


def row(kind: str, session: str, days_ago: float = 0.5, source: str = "gate") -> dict:
    return {
        "at": NOW - days_ago * DAY,
        "source": source,
        "kind": kind,
        "session": session,
    }


def found(rows: list[Any]) -> Any:
    got = call("recurring", rows, NOW)
    if got is None:
        return None
    return [(r.kind, r.sessions, r.gate, r.ci) for r in got]


# ---------------------------------------------------------------- pure
check(
    "C1 one session, however often: nothing recurs",
    found([row("encoding", "s1"), row("encoding", "s1"), row("encoding", "s1")]),
    [],
)
check(
    "C2 two sessions: it recurs (sessions, gate rows, CI rows)",
    found(
        [
            row("encoding", "s1"),
            row("encoding", "s2", source="ci"),
            row("encoding", "s2"),
        ]
    ),
    [("encoding", 2, 2, 1)],
)
check(
    "C3 a row older than 14 days does not count",
    [
        found([row("timeout", "s1", 13.9), row("timeout", "s2", 0.1)]),
        found([row("timeout", "s1", 14.1), row("timeout", "s2", 0.1)]),
        found([row("timeout", "s1", 14), row("timeout", "s2", 0)]),
    ],
    [[("timeout", 2, 2, 0)], [], [("timeout", 2, 2, 0)]],
)
check(
    "C4 nothing in the last 3 days: it is not pointed out any more",
    [
        found([row("environment", "s1", 5), row("environment", "s2", 2.9)]),
        found([row("environment", "s1", 5), row("environment", "s2", 3.1)]),
        found([row("environment", "s1", 5), row("environment", "s2", 3)]),
    ],
    [[("environment", 2, 2, 0)], [], [("environment", 2, 2, 0)]],
)
check(
    "C5 test, lint and other failures are ordinary work: never pointed out",
    found([row(k, s) for k in ("test", "lint", "other") for s in ("s1", "s2", "s3")]),
    [],
)
check(
    "C6 each kind by itself, in a fixed order",
    found(
        [
            row("timeout", "s1"),
            row("encoding", "s1"),
            row("encoding", "s2"),
            row("timeout", "s3"),
            row("environment", "s1"),
        ]
    ),
    [("encoding", 2, 2, 0), ("timeout", 2, 2, 0)],
)
check(
    "C7 rows from the future, broken rows, odd values: left out, no error",
    found(
        [
            row("encoding", "s1"),
            {"at": NOW + DAY, "source": "gate", "kind": "encoding", "session": "s2"},
            {"at": "now", "source": "gate", "kind": "encoding", "session": "s3"},
            {"at": NOW, "source": "shell", "kind": "encoding", "session": "s4"},
            {"at": NOW, "source": "gate", "kind": "weird", "session": "s5"},
            {"at": NOW, "source": "gate", "kind": "encoding", "session": 7},
            {"at": True, "source": "gate", "kind": "encoding", "session": "s8"},
            "x",
            None,
        ]
    ),
    [],
)
check("C7 nothing at all", (found([]), call("lines", [])), ([], []))
told = call(
    "lines",
    call(
        "recurring",
        [row("encoding", "sess-one"), row("encoding", "sess-two", source="ci")],
        NOW,
    )
    or [],
)
check(
    "C8 the line: a candidate, the kind's name, the counts; no session id",
    (
        len(told or []),
        all(
            w in (told or [""])[0]
            for w in ("Gotchas 후보", "인코딩", "세션 2개", "게이트 1", "CI 1")
        ),
        "sess-" in "".join(told or []),
    ),
    (1, True, False),
)

# ---------------------------------------------------------------- the files
SID = "session-" + uuid.uuid4().hex
A, B = str(PROJ), str(OTHER)
check(
    "C9 nothing recorded yet: an empty list",
    call("read", A),
    [],
)
call("record", A, "gate", "encoding", SID, NOW)
call("record", A, "ci", "timeout", SID, NOW + 1)
call("record", B, "gate", "environment", SID, NOW)
got = call("read", A) or []
check(
    "C9 recorded and read back, one file per repository",
    (
        [(r.get("source"), r.get("kind"), r.get("at")) for r in got],
        [(r.get("kind")) for r in call("read", B) or []],
    ),
    ([("gate", "encoding", NOW), ("ci", "timeout", NOW + 1)], ["environment"]),
)
FILES = HOME / "failures"
stored = (
    "".join(f.read_text(encoding="utf-8") for f in FILES.glob("*"))
    if FILES.is_dir()
    else ""
)
check(
    "C9 the session id is stored as a short hash; no path is stored",
    (
        SID in stored,
        "_recur_" in stored,
        len(str(got[0].get("session"))) if got else None,
        (got[0].get("session") == got[1].get("session")) if len(got) > 1 else None,
    ),
    (False, False, 12, True),
)
if hasattr(recurrence, "repo_file"):
    with open(recurrence.repo_file(A), "a", encoding="utf-8") as fh:
        fh.write('not json\n{"at": 1}\n[]\n')
call("record", A, "gate", "lint", "another", NOW + 2)
check(
    "C10 broken lines are skipped, the rows around them are kept",
    [r.get("kind") for r in call("read", A) or []],
    ["encoding", "timeout", "lint"],
)
check(
    "C10 a kind or source that is not known is not recorded",
    (
        call("record", A, "gate", "weird", SID, NOW),
        call("record", A, "shell", "encoding", SID, NOW),
        len(call("read", A) or []),
    ),
    (False, False, 3),
)
shutil.rmtree(FILES, ignore_errors=True)
if hasattr(recurrence, "repo_file"):
    # a file longer than what is read: only whole rows from its end come back
    FILES.mkdir(parents=True)
    filler = json.dumps(row("lint", "old")) + "\n"
    with open(recurrence.repo_file(A), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(filler * (70_000 // len(filler) + 1))
    call("record", A, "ci", "encoding", "last", NOW)
    tail = call("read", A) or []
    check(
        "C10 a long file: the end is read, every row whole, the newest row last",
        (
            0 < len(tail) < 70_000 // len(filler),
            {r.get("kind") for r in tail[:-1]},
            tail[-1].get("kind") if tail else None,
        ),
        (True, {"lint"}, "encoding"),
    )
    shutil.rmtree(FILES, ignore_errors=True)

# ---------------------------------------------------------------- through the hooks
GATE = "python check.py"
for d in (PROJ, OTHER):
    d.mkdir(parents=True)
    (d / "CLAUDE.md").write_text(
        f"# p\n\n## Verification\n- gate: `{GATE}`\n- ci: none\n", encoding="utf-8"
    )
    (d / "check.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
ENCODING = "Traceback\nUnicodeEncodeError: 'charmap' codec can't encode character"
TESTFAIL = "FAIL T1 something\nFAILURES: 2"


def hook(
    mode: str, s: str, env: dict = ENV, cwd: Path = PROJ, **payload: object
) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps(
            {"session_id": s, "cwd": str(cwd), "prompt_id": "p1", **payload}
        ),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return p.returncode, p.stdout.strip()


def shell(
    s: str,
    command: str,
    text: str,
    failed: bool = True,
    env: dict = ENV,
    cwd: Path = PROJ,
) -> tuple[int, str]:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": uuid.uuid4().hex,
    }
    hook("pre", s, env, cwd, **common)
    if failed:
        return hook("fail", s, env, cwd, error=text, **common)
    return hook(
        "post", s, env, cwd, tool_response={"stdout": text, "stderr": ""}, **common
    )


def stored_rows(root: Path, home: Path = HOME) -> list[tuple[Any, Any]]:
    folder = home / "failures"
    out: list[tuple[Any, Any]] = []
    name = (
        Path(recurrence.repo_file(str(root))).name
        if hasattr(recurrence, "repo_file")
        else ""
    )
    target = folder / name
    if name and target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out.append((r.get("source"), r.get("kind")))
    return out


def start(root: Path, env: dict = ENV, source: str = "startup") -> str:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "session_context.py")],
        input=json.dumps(
            {
                "session_id": uuid.uuid4().hex,
                "source": source,
                "cwd": str(root),
                "hook_event_name": "SessionStart",
            }
        ),
        env=dict(env, CLAUDE_PROJECTS_ROOT=str(ROOT / "none")),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert p.returncode == 0, p.stderr[-300:]
    if not p.stdout.strip():
        return ""
    return str(json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"])


CANDIDATE = "Gotchas 후보"

off = uuid.uuid4().hex
hook("prompt", off, OFF, prompt="work")
off_fail = shell(off, GATE, ENCODING, env=OFF)
off_stop = hook("stop", off, OFF)
check("C11 CLAUDE_RECURRENCE=0: a failed gate records nothing", stored_rows(PROJ), [])

s1 = uuid.uuid4().hex
hook("prompt", s1, prompt="work")
first = shell(s1, GATE, ENCODING)
shell(s1, GATE, ENCODING)
check(
    "C12 the gate fails with an encoding error, twice in one session: one row",
    stored_rows(PROJ),
    [("gate", "encoding")],
)
check(
    "C12 ... and what the hook answers is the same as with the record switched off",
    (first, hook("stop", s1)),
    (off_fail, off_stop),
)
shell(s1, GATE, TESTFAIL)
check(
    "C13 the same session, now failing tests: counted as its own kind",
    stored_rows(PROJ),
    [("gate", "encoding"), ("gate", "test")],
)
before = stored_rows(PROJ)
shell(s1, GATE, "ok", failed=False)
shell(s1, "python other.py", ENCODING)
shell(s1, "git log --oneline", ENCODING)
check(
    "C14 a passing gate and a failing command that is no gate: no row",
    stored_rows(PROJ),
    before,
)
check(
    "C15 one session only: the next session start says nothing",
    CANDIDATE in start(PROJ),
    False,
)

s2 = uuid.uuid4().hex
hook("prompt", s2, prompt="work")
shell(s2, GATE, ENCODING)
said = start(PROJ)
check(
    "C16 a second session fails the same way: the next start names the candidate once",
    (
        said.count(CANDIDATE),
        "인코딩" in said,
        "세션 2개" in said,
        s1 in said or s2 in said,
    ),
    (1, True, True, False),
)
check(
    "C16 ... not in another repository, not after a compaction, not when switched off",
    (
        CANDIDATE in start(OTHER),
        CANDIDATE in start(PROJ, source="compact"),
        CANDIDATE in start(PROJ, OFF),
    ),
    (False, False, False),
)

HOME2 = ROOT / "home2"
(HOME2 / "failures").parent.mkdir(parents=True)
(HOME2 / "failures").write_text("a file where the folder should be", encoding="utf-8")
BROKEN = dict(ENV, CLAUDE_DISCIPLINE_HOME=str(HOME2))
s3 = uuid.uuid4().hex
hook("prompt", s3, BROKEN, prompt="work")
check(
    "C17 the record cannot be written: same answers, the start still works",
    (
        shell(s3, GATE, ENCODING, env=BROKEN),
        hook("stop", s3, BROKEN),
        CANDIDATE in start(PROJ, BROKEN),
    ),
    (off_fail, off_stop, False),
)
HOME3 = ROOT / "home3"
if hasattr(recurrence, "repo_file"):
    # a folder where the repository's file should be: reading fails (not "no file")
    (HOME3 / "failures" / Path(recurrence.repo_file(str(PROJ))).name).mkdir(
        parents=True
    )
check(
    "C17 the record cannot be read: the start works and says nothing of it",
    CANDIDATE in start(PROJ, dict(ENV, CLAUDE_DISCIPLINE_HOME=str(HOME3))),
    False,
)

# ---------------------------------------------------------------- CI and the once-per-session rule
import discipline as disc

note = getattr(disc, "note_failure", None)
shutil.rmtree(HOME / "failures", ignore_errors=True)
state: dict[str, Any] = {"repos": {str(PROJ): {}}}
if note:
    note(state, str(PROJ / "sub"), "ci", "timeout", "sess-a")
    note(state, str(PROJ), "ci", "timeout", "sess-a")
    note(state, str(PROJ), "ci", "encoding", "sess-a")
    note(state, str(PROJ), "ci", "nonsense", "sess-a")
    note(state, str(PROJ), "ci", None, "sess-a")
    note(state, str(PROJ), "ci", "timeout", None)
check(
    "C18 a folder inside the repository counts for the repository; once per kind and session",
    stored_rows(PROJ),
    [("ci", "timeout"), ("ci", "encoding")],
)
full: dict[str, Any] = {
    "repos": {str(OTHER): {}},
    "failures_noted": [f"k{i}" for i in range(getattr(disc, "FAILURES_NOTED_MAX", 0))],
}
if note:
    note(full, str(OTHER), "ci", "timeout", "sess-c")
    note(full, str(OTHER), "ci", "timeout", "sess-c")
check(
    "C18 a session that has counted its limit of failures counts no more",
    stored_rows(OTHER),
    [],
)
nested: dict[str, Any] = {"repos": {str(PROJ): {}, str(PROJ / "inner"): {}}}
innermost = getattr(disc, "innermost_root", lambda roots: None)
check(
    "C18 a failed gate is counted for the innermost of the projects it failed for",
    [
        innermost([str(PROJ), str(PROJ / "inner")]),
        innermost([str(PROJ / "inner"), str(PROJ)]),
        innermost([str(PROJ)]),
        innermost([]),
    ],
    [str(PROJ / "inner"), str(PROJ / "inner"), str(PROJ), None],
)
ci_note = getattr(disc, "note_ci_failures", None)
state = {
    "repos": {str(OTHER): {}},
    "ci": {
        str(OTHER): {"status": "failed", "kind": "environment"},
        str(PROJ): {"status": "verified", "kind": "timeout"},
        "C:\\x": {"status": "failed"},
        "C:\\y": "broken",
    },
}
if ci_note:
    ci_note(state, "sess-b")
    ci_note(state, "sess-b")
check(
    "C19 at a Stop: a failed CI run with a kind is counted once; other states are not",
    (stored_rows(OTHER), stored_rows(PROJ)),
    ([("ci", "environment")], [("ci", "timeout"), ("ci", "encoding")]),
)

for p in (ROOT, PROJ, OTHER):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
