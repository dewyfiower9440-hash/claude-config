"""P1-03: a turn that changed implementation files but no test file is pointed out once,
at the next prompt. It is never a reason to block.

    python -I tests/test_test_gap.py

A throw-away repository outside %TEMP%, an isolated temp folder and approval home.
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
HOOKS = HERE.parent / "hooks"
ROOT = HERE / "_tmp_testgap"
PROJ = Path("C:/dev/_testgap_proj")
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
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


gap = getattr(disc, "test_gap", lambda changes, session_files, tops: None)

# ---------------------------------------------------------------- pure
T = "C:\\dev\\p"
tops = [T]


def f(*rel: str, top: str = T) -> list[str]:
    return [os.path.join(top, r.replace("/", os.sep)) for r in rel]


check(
    "G2-T1 implementation files only",
    gap(f("src/a.py", "src/b.ts"), [], tops),
    f("src/a.py", "src/b.ts"),
)
check(
    "G2-T2 a test file changed in the same turn",
    [
        gap(f("src/a.py", "tests/test_a.py"), [], tops),
        gap(f("src/a.ts", "src/a.test.ts"), [], tops),
        gap(f("src/a.py", "src/test_a.py"), [], tops),
    ],
    [[], [], []],
)
check(
    "G2-T3 the test file was edited earlier in this session",
    gap(f("src/a.py"), f("tests/test_a.py"), tops),
    [],
)
check(
    "G2-T4 documents and configuration only",
    gap(f("README.md", "settings.json", "pyproject.toml", "docs/plans/x.md"), [], tops),
    [],
)
check("G2-T5 test files only", gap(f("tests/test_a.py"), [], tops), [])
under_tests = "C:\\work\\tests\\proj"
check(
    "G2-T6 a repository below a folder named tests: its src file is no test",
    gap(f("src/a.py", top=under_tests), [], [under_tests]),
    f("src/a.py", top=under_tests),
)
check(
    "G2-T7 paths outside every repository, nothing, odd values: no error",
    [
        gap(["D:\\elsewhere\\a.py"], [], tops),
        gap([], [], tops),
        gap(None, None, None),
        gap([1, None, "C:\\dev\\p\\src\\a.py"], [2], [3, T]),
    ],
    [["D:\\elsewhere\\a.py"], [], [], f("src/a.py")],
)

OTHER = "C:\\dev\\q"
check(
    "G2-T12 a test edited in another repository does not cover this one",
    [
        gap(f("src/a.py"), f("tests/test_b.py", top=OTHER), [T, OTHER]),
        gap(f("src/a.py") + f("tests/test_b.py", top=OTHER), [], [T, OTHER]),
        gap(
            f("src/a.py") + f("src/b.py", "tests/test_b.py", top=OTHER), [], [T, OTHER]
        ),
    ],
    [f("src/a.py"), f("src/a.py"), f("src/a.py")],
)
INNER = "C:\\dev\\mono\\tests\\proj"
check(
    "G2-T13 a repository nested below another one's tests folder is judged by itself",
    [
        gap(f("src/a.py", top=INNER), [], ["C:\\dev\\mono", INNER]),
        gap(f("src/a.py", top=INNER), [], [INNER, "C:\\dev\\mono"]),
    ],
    [f("src/a.py", top=INNER)] * 2,
)
check(
    "G2-T14 a name that only ends in 'test', and a test-named document, are no tests",
    [
        gap(f("src/a.py", "src/latest.py"), [], tops),
        gap(f("src/a.py", "docs/latest.md", "docs/plans/test_x.md"), [], tops),
        gap(f("src/contest.ts"), [], tops),
    ],
    [f("src/a.py", "src/latest.py"), f("src/a.py"), f("src/contest.ts")],
)

# ---------------------------------------------------------------- through the hooks
for d in ("src", "tests"):
    (PROJ / d).mkdir(parents=True)
(PROJ / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
(PROJ / "tests" / "test_a.py").write_text("t = 0\n", encoding="utf-8")
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
OBSERVED = HOME / "test-gap-observe.jsonl"
NOTE = "테스트 파일"


def hook(
    mode: str, s: str, env: dict = ENV, prompt_id: str = "p1", **payload: object
) -> dict:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": prompt_id, **payload}
        ),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert p.returncode == 0, (mode, p.returncode, p.stderr[-300:])
    return json.loads(p.stdout) if p.stdout.strip() else {}


def told(out: dict) -> str:
    return str(out.get("hookSpecificOutput", {}).get("additionalContext") or "")


def edit(s: str, rel: str, env: dict = ENV, prompt_id: str = "p1") -> None:
    path = PROJ / rel
    body = f"v = {uuid.uuid4().hex!r}\n"
    path.write_text(body, encoding="utf-8")
    hook(
        "post",
        s,
        env,
        prompt_id,
        tool_name="Write",
        tool_input={"file_path": str(path), "content": body},
        tool_use_id=uuid.uuid4().hex,
    )


def rows(home: Path = HOME) -> int:
    f_ = home / "test-gap-observe.jsonl"
    return len(f_.read_text(encoding="utf-8").splitlines()) if f_.is_file() else 0


# a reference session without any gap: what the Stop says for an unverified edit
ref = uuid.uuid4().hex
hook("prompt", ref, prompt="work")
edit(ref, "src/a.py")
edit(ref, "tests/test_a.py")
ref_stop = hook("stop", ref)
ref_next = hook("prompt", ref, prompt_id="p2", prompt="next")
check(
    "G2-T9 implementation and test changed together: no note, nothing observed",
    (NOTE in told(ref_next), rows()),
    (False, 0),
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
edit(s, "src/a.py")
gap_stop = hook("stop", s)
first = hook("prompt", s, prompt_id="p2", prompt="next")
second = hook("prompt", s, prompt_id="p3", prompt="again")
check(
    "G2-T8 implementation only: the Stop is the same as without a gap, one row is observed",
    (
        gap_stop.get("decision"),
        gap_stop.get("reason") == ref_stop.get("reason"),
        rows(),
    ),
    (ref_stop.get("decision"), True, 1),
)
check(
    "G2-T8 ... the next prompt is told once (file name, no full path), the one after is not",
    (
        NOTE in told(first),
        "a.py" in told(first),
        str(PROJ).lower() in told(first).lower(),
        NOTE in told(second),
    ),
    (True, True, False, False),
)
row = (
    json.loads(OBSERVED.read_text(encoding="utf-8").splitlines()[0])
    if OBSERVED.is_file()
    else {}
)
check(
    "G2-T8 ... the observed row counts files and holds no path",
    (row.get("files"), "a.py" in json.dumps(row)),
    (1, False),
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
edit(s, "src/a.py")
hook("stop", s)
RELAY = 'Another Claude session sent a message:\n<agent-message from="a1">\ndone\n</agent-message>'
relayed = hook("prompt", s, prompt_id="p2", prompt=RELAY)
after = hook("prompt", s, prompt_id="p3", prompt="next")
check(
    "G2-T10 a relayed prompt does not use the note up; the user's next prompt gets it",
    (NOTE in told(relayed), NOTE in told(after)),
    (False, True),
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
edit(s, "src/a.py")
hook("stop", s)  # blocked (unverified): the turn goes on
edit(s, "tests/test_a.py")
hook("stop", s, stop_hook_active=True)
check(
    "G2-T15 a test added after the first Stop of the turn withdraws the note",
    NOTE in told(hook("prompt", s, prompt_id="p2", prompt="next")),
    False,
)

HOME2 = ROOT / "home2"
(HOME2 / "test-gap-observe.jsonl").mkdir(parents=True)
BROKEN = dict(ENV, CLAUDE_DISCIPLINE_HOME=str(HOME2))
s = uuid.uuid4().hex
hook("prompt", s, BROKEN, prompt="work")
edit(s, "src/a.py", BROKEN)
broken_stop = hook("stop", s, BROKEN)
check(
    "G2-T11 the observation cannot be written: same Stop, and the note still comes",
    (
        broken_stop.get("decision"),
        NOTE in told(hook("prompt", s, BROKEN, "p2", prompt="next")),
    ),
    (ref_stop.get("decision"), True),
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
