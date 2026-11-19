"""P1-04: the size of shell output is measured; a large test run in the main conversation
is answered once per turn with a hint to delegate. Never a reason to block.

    python -I tests/test_output_size.py

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
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_outsize"
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_outsize_proj"
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
os.environ.pop("CLAUDE_OUTPUT_ADVICE_CHARS", None)
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


chars = getattr(disc, "response_chars", lambda r: None)
is_test = getattr(disc, "is_test_run", lambda c, d, g: None)
threshold = getattr(disc, "advice_threshold", lambda raw: None)
GATE = "python -I tests/run_all.py"

check(
    "O-T1 the text of a result: dict / text / nothing / a list",
    [
        chars({"stdout": "a" * 10, "stderr": "b" * 5, "interrupted": False, "code": 3}),
        chars("c" * 7),
        chars(None),
        chars(["x" * 100]),
    ],
    [15, 7, 0, 0],
)
check(
    "O-T2 runners and the declared gate are test runs; other commands are not",
    [
        is_test("uv run pytest -q", "bash", None),
        is_test("ruff check .", "bash", None),
        is_test(f"cd repo && {GATE}", "bash", GATE),
        is_test("git status", "bash", GATE),
        is_test("echo pytest", "bash", None),
        is_test("echo 'unbalanced", "bash", None),
        is_test("Invoke-Pester -Output Detailed", "powershell", None),
        is_test(None, "bash", None),
    ],
    [True, True, True, False, False, False, True, False],
)
check(
    "O-T3 the threshold: default, a value, too small, no number, too large",
    [
        threshold(None),
        threshold("4000"),
        threshold("500"),
        threshold("abc"),
        threshold("99999999"),
    ],
    [8000, 4000, 8000, 8000, 8000],
)

# ---------------------------------------------------------------- through the hooks
PROJ.mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
OBSERVED = HOME / "output-size-observe.jsonl"
HINT = "test-runner"
EXPLORE = "Explore"
PERSISTED = (
    "<persisted-output>Output too large (40000 chars). Saved to C:/tmp/x.txt"
    "</persisted-output>"
)
SECRET_TEXT = "ZQXJ-output-marker"


def hook(
    mode: str, s: str, env: dict = ENV, prompt_id: str = "p1", **payload: object
) -> tuple[int, dict]:
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
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else {})


def shell(
    s: str,
    command: str,
    size: int,
    mode: str = "post",
    env: dict = ENV,
    prompt_id: str = "p1",
    persisted: bool = False,
    **who: object,
) -> tuple[int, str]:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": uuid.uuid4().hex,
        **who,
    }
    hook("pre", s, env, prompt_id, **common)
    text = (SECRET_TEXT + "x" * size)[:size]
    if persisted:
        text = (PERSISTED + "x" * size)[:size]
    rc, out = hook(
        mode, s, env, prompt_id, tool_response={"stdout": text, "stderr": ""}, **common
    )
    return rc, str(out.get("hookSpecificOutput", {}).get("additionalContext") or "")


def rows(home: Path = HOME) -> list[dict]:
    f = home / "output-size-observe.jsonl"
    if not f.is_file():
        return []
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln]


TEST = "uv run pytest -q"
s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
rc, said = shell(s, TEST, 9000)
check(
    "O-T4 a large test run: a hint with the size, one row observed",
    (rc, HINT in said, "9000" in said or "9,000" in said, len(rows())),
    (0, True, True, 1),
)
rc, said = shell(s, TEST, 9000)
check(
    "O-T5 again in the same turn: no second hint, a second row",
    (HINT in said, len(rows())),
    (False, 2),
)
hook("prompt", s, prompt_id="p2", prompt="next")
rc, said = shell(s, TEST, 9000, prompt_id="p2")
check("O-T6 in the next turn: the hint again", HINT in said, True)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
before = len(rows())
rc, said = shell(s, TEST, 3000)
check(
    "O-T7 a test run below the threshold: no hint, observed as a test run",
    (
        HINT in said,
        len(rows()) - before,
        rows()[-1].get("test"),
        rows()[-1].get("chars"),
    ),
    (False, 1, True, 3000),
)
rc, said = shell(s, "git log --stat", 9000)
check(
    "O-T8 a large output of something that is no test run (v1.1): the Explore hint, observed as no test",
    (
        HINT in said,
        EXPLORE in said,
        "9000" in said,
        rows()[-1].get("test"),
        rows()[-1].get("who"),
    ),
    (False, True, True, False, "main"),
)
rc, said = shell(s, "cat docs/plans/big.md", 9000)
check("O-T8b a second large output in the same turn: no second hint", said, "")
hook("prompt", s, prompt_id="p3", prompt="next")
rc, said = shell(s, TEST, 9000, prompt_id="p3")
rc, said = shell(s, "cat docs/plans/big.md", 9000, prompt_id="p3")
check(
    "O-T8c one output hint per turn: after the test-runner hint, no Explore hint",
    (EXPLORE in said, said),
    (False, ""),
)
hook("prompt", s, prompt_id="p4", prompt="next")
rc, said = shell(s, "find . -name '*.py'", 300, prompt_id="p4", persisted=True)
check(
    "O-T8d a small result that says the output was persisted to a file: the Explore hint, a row",
    (
        EXPLORE in said,
        "persisted" in said.lower() or "파일" in said,
        rows()[-1].get("chars"),
    ),
    (True, True, 300),
)
hook("prompt", s, prompt_id="p5", prompt="next")
rc, said = shell(
    s, "find . -name '*.py'", 9000, prompt_id="p5", agent_id="e1", agent_type="Explore"
)
check("O-T8e a large output inside a subagent: no hint", said, "")
before = len(rows())
rc, said = shell(s, TEST, 500)
check(
    "O-T9 a small output: nothing said, nothing observed",
    (said, len(rows()) - before),
    ("", 0),
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
rc, said = shell(s, TEST, 9000, agent_id="t1", agent_type="test-runner")
check(
    "O-T10 a large test run inside a subagent: no hint (it is delegated), observed as subagent",
    (HINT in said, rows()[-1].get("who"), rows()[-1].get("test")),
    (False, "subagent", True),
)
rc, said = shell(s, TEST, 9000, mode="fail")
check(
    "O-T11 a failed test run: nothing printed, observed as failed",
    (rc, said, rows()[-1].get("failed")),
    (0, "", True),
)
before = len(rows())
failure = {
    "tool_name": "Bash",
    "tool_input": {"command": TEST},
    "tool_use_id": uuid.uuid4().hex,
    "error": "E" * 5000,
    "is_interrupt": False,
}
hook("pre", s, **failure)
rc, out = hook("fail", s, **failure)
check(
    "O-T11 a failure event that carries its text as `error` (no tool_response) is measured too",
    (rc, out, len(rows()) - before, rows()[-1].get("chars"), rows()[-1].get("failed")),
    (0, {}, 1, 5000, True),
)
everything = json.dumps(rows())
check(
    "O-T12 the observation holds no command, output or path",
    [x in everything for x in ("pytest", SECRET_TEXT, "_outsize_proj", "git log")],
    [False, False, False, False],
)
check(
    "O-T12 ... only these keys",
    sorted({k for r in rows() for k in r}),
    ["at", "chars", "failed", "test", "who"],
)
HOME2 = ROOT / "home2"
(HOME2 / "output-size-observe.jsonl").mkdir(parents=True)
BROKEN = dict(ENV, CLAUDE_DISCIPLINE_HOME=str(HOME2))
s = uuid.uuid4().hex
hook("prompt", s, BROKEN, prompt="work")
rc, said = shell(s, TEST, 9000, env=BROKEN)
check(
    "O-T12 an observation that cannot be written: the hint still comes, exit code 0",
    (rc, HINT in said),
    (0, True),
)
LOW = dict(ENV, CLAUDE_OUTPUT_ADVICE_CHARS="2500")
s = uuid.uuid4().hex
hook("prompt", s, LOW, prompt="work")
rc, said = shell(s, TEST, 3000, env=LOW)
check("O-T3 a configured threshold is used by the hook", HINT in said, True)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
