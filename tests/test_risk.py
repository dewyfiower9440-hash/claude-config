"""P2-01 / P2-03: a turn's changes get a risk score that is written down, and a high score
without a plan that compares alternatives is pointed out once, at the next prompt. It is
never a reason to block.

    python -I tests/test_risk.py

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
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = HERE / "_tmp_risk"
# Under the gate every run has a folder of its own (run_all.GATE_WORK_ROOT); alone, as before.
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_risk_proj"
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
for name in ("CLAUDE_RISK_SCORE", "CLAUDE_RISK_ADVICE_AT"):
    os.environ.pop(name, None)
sys.path.insert(0, str(HOOKS))
try:
    import risk
except ImportError:  # before the module exists: every check below fails, none crashes
    risk = None

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


def call(fn: str, *args: Any) -> Any:
    return getattr(risk, fn)(*args) if hasattr(risk, fn) else None


# ---------------------------------------------------------------- pure: kinds
def kinds(*rels: str, is_test: bool = False, is_code: bool = False) -> list[Any]:
    return [call("kind", rel, is_test, is_code) for rel in rels]


check(
    "R1 files that guard the work",
    kinds(
        "hooks/discipline.py",
        ".claude/settings.json",
        "settings.json",
        ".husky/pre-commit",
        ".pre-commit-config.yaml",
        is_code=True,
    ),
    ["guard"] * 5,
)
check(
    "R1 a hooks folder deeper down (React hooks) and an editor's settings are no guards",
    [
        call("kind", "src/hooks/useAuth.ts", False, True),
        call("kind", "pkg/hooks/x.py", False, True),
        call("kind", ".vscode/settings.json", False, False),
        call("kind", "app/.claude/x.json", False, False),
    ],
    ["code", "code", "config", "config"],
)
check(
    "R2 CI definitions",
    kinds(".github/workflows/ci.yml", ".gitlab-ci.yml", "azure-pipelines.yml"),
    ["ci"] * 3,
)
check(
    "R3 schema and migrations (a .sql file is schema before it is code)",
    kinds("migrations/0001_init.py", "db/a.sql", "schema.prisma", is_code=True),
    ["schema"] * 3,
)
check(
    "R4 dependency declarations and lock files",
    kinds(
        "uv.lock",
        "pnpm-lock.yaml",
        "package-lock.json",
        "pyproject.toml",
        "sub/package.json",
        "requirements-dev.txt",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
    ),
    ["deps"] * 10,
)
check(
    "R5 tests, documents, code, configuration, anything else",
    [
        call("kind", "tests/test_a.py", True, True),
        call("kind", "README.md", False, False),
        call("kind", "docs/plans/x.md", False, False),
        call("kind", "docs/diagram.png", False, False),
        call("kind", "notes.txt", False, False),
        call("kind", "src/a.py", False, True),
        call("kind", "app.yaml", False, False),
        call("kind", ".env.example", False, False),
        call("kind", "Dockerfile", False, False),
        call("kind", "logo.png", False, False),
    ],
    [
        "test",
        "docs",
        "docs",
        "docs",
        "docs",
        "code",
        "config",
        "config",
        "config",
        "other",
    ],
)
check(
    "R6 backslashes and upper case are the same path; a guard file under tests stays a guard",
    [
        call("kind", "HOOKS\\Guard.PY", False, True),
        call("kind", ".GitHub\\Workflows\\CI.yml", False, False),
        call("kind", "UV.LOCK", False, False),
        call("kind", "hooks/tests/test_x.py", True, True),
    ],
    ["guard", "ci", "deps", "guard"],
)


# ---------------------------------------------------------------- pure: score
def score(found: list[str], lines: object = 0, repos: object = 1) -> Any:
    got = call("assess", found, lines, repos)
    return getattr(got, "score", None)


check("R7 documents only", score(["docs", "docs"]), 0)
check("R7 one code file / one guard file", (score(["code"]), score(["guard"])), (2, 5))
check(
    "R7 guard + CI + lock file: 5 + 5 + 4, and +1 for three files",
    score(["guard", "ci", "deps"]),
    15,
)
check(
    "R7 a kind counts once however many files it has",
    (score(["code", "code"]), score(["code", "test"])),
    (2, 3),
)
check(
    "R8 number of files: 2 / 3, 5 / 6, 10 / 11",
    [score(["docs"] * n) for n in (2, 3, 5, 6, 10, 11)],
    [0, 1, 1, 2, 2, 3],
)
check(
    "R8 number of lines: 49 / 50, 199 / 200, 499 / 500",
    [score(["docs"], lines=n) for n in (49, 50, 199, 200, 499, 500)],
    [0, 1, 1, 2, 2, 3],
)
check(
    "R8 every repository after the first adds 2",
    [score(["docs"], repos=n) for n in (0, 1, 2, 3)],
    [0, 0, 2, 4],
)
check(
    "R9 nothing changed, unknown kinds, odd numbers: no error",
    [
        score([]),
        score(["?"]),
        score(["code"], lines=None, repos=None),
        score(["code"], lines=-5, repos=-1),
        score(["code"], lines="many", repos=True),
    ],
    [0, 1, 2, 2, 2],
)
whole = call("assess", ["code", "code", "guard"], 60, 2)
check(
    "R9 the parts are kept: files per kind, files, lines, repositories",
    (
        getattr(whole, "kinds", None),
        getattr(whole, "files", None),
        getattr(whole, "lines", None),
        getattr(whole, "repos", None),
        getattr(whole, "score", None),
    ),
    ({"code": 2, "guard": 1}, 3, 60, 2, 2 + 5 + 1 + 1 + 2),
)

# ---------------------------------------------------------------- pure: alternatives
check(
    "R10 a heading or a table header names the comparison",
    [
        call("compares_alternatives", "# Plan\n\n## 대안 비교\n- a\n"),
        call("compares_alternatives", "## Options considered\n"),
        call(
            "compares_alternatives",
            "| 선택지 | 장점 | 단점 |\n|---|---|---|\n| A | x | y |\n",
        ),
        call("compares_alternatives", "### Trade-offs\n"),
        call("compares_alternatives", "## 옵션\n"),
        call("compares_alternatives", "## Alternatives\n"),
    ],
    [True] * 6,
)
check(
    "R10 the word in a sentence or a table's body is not a comparison; nor is no text",
    [
        call("compares_alternatives", "# Plan\n\n다른 대안은 없다고 본다.\n"),
        call("compares_alternatives", "| 항목 | 내용 |\n|---|---|\n| 대안 | 없음 |\n"),
        call("compares_alternatives", ""),
        call("compares_alternatives", "## 단계\n- [ ] 1. do it\n"),
        call("compares_alternatives", "## Adoption\n"),
        call("compares_alternatives", "## Optional steps\n"),
        call("compares_alternatives", "## Configuration options\n"),
        call("compares_alternatives", "## 비교 테스트 추가\n"),
        call("compares_alternatives", "| option | value |\n|---|---|\n| a | 1 |\n"),
    ],
    [False] * 9,
)
WITH = "## 대안\n- A\n- B\n"
WITHOUT = "## 단계\n- [ ] 1.\n"
check(
    "R11 advice: at or above the threshold and no plan that compares",
    [
        call("advice_needed", 8, 8, []),
        call("advice_needed", 7, 8, []),
        call("advice_needed", 20, 8, [WITHOUT]),
        call("advice_needed", 20, 8, [WITHOUT, WITH]),
        call("advice_needed", 0, 8, [WITHOUT]),
    ],
    [True, False, True, False, False],
)
check(
    "R12 threshold: the default unless the variable is a positive whole number",
    [call("threshold", v) for v in (None, "", "abc", "0", "-3", "2.5", "5", " 12 ")],
    [8, 8, 8, 8, 8, 8, 5, 12],
)

# ---------------------------------------------------------------- through the hooks
for d in ("src", "tests", "hooks", ".github/workflows"):
    (PROJ / d).mkdir(parents=True)
(PROJ / "src" / "a.py").write_text("a = 0\n", encoding="utf-8")
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)
NOTE = "위험 점수"
OFF = dict(ENV, CLAUDE_RISK_SCORE="0")


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


def risk_part(out: dict) -> str:
    """The risk note alone (the test-gap note of the same prompt names files on purpose)."""
    return "\n\n".join(part for part in told(out).split("\n\n") if NOTE in part)


def edit(s: str, rel: str, env: dict = ENV, lines: int = 1) -> None:
    path = PROJ / rel
    body = f"v = {uuid.uuid4().hex!r}\n" * lines
    path.write_text(body, encoding="utf-8")
    hook(
        "post",
        s,
        env,
        tool_name="Write",
        tool_input={"file_path": str(path), "content": body},
        tool_use_id=uuid.uuid4().hex,
    )


def rows(home: Path = HOME) -> list[dict]:
    f_ = home / "risk-observe.jsonl"
    if not f_.is_file():
        return []
    return [json.loads(line) for line in f_.read_text(encoding="utf-8").splitlines()]


RISKY = ("hooks/h.py", ".github/workflows/ci.yml", "uv.lock")

# switched off: the reference for what the Stop says, and nothing is written or told
ref = uuid.uuid4().hex
hook("prompt", ref, OFF, prompt="work")
for rel in RISKY:
    edit(ref, rel, OFF)
ref_stop = hook("stop", ref, OFF)
ref_next = hook("prompt", ref, OFF, "p2", prompt="next")
check(
    "R13 CLAUDE_RISK_SCORE=0: nothing observed, nothing told",
    (rows(), NOTE in told(ref_next)),
    ([], False),
)

low_ref = uuid.uuid4().hex
hook("prompt", low_ref, OFF, prompt="work")
edit(low_ref, "src/a.py", OFF, lines=60)
low_ref_stop = hook("stop", low_ref, OFF)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
edit(s, "src/a.py", lines=60)
low_stop = hook("stop", s)
check(
    "R14 one code file of 60 lines: the Stop is the same as with the score off",
    (low_stop.get("decision"), low_stop.get("reason") == low_ref_stop.get("reason")),
    (low_ref_stop.get("decision"), True),
)
check(
    "R14 ... one row: code 2 + 1 for the lines, not advised",
    [
        (
            r.get("score"),
            r.get("kinds"),
            r.get("files"),
            r.get("lines"),
            r.get("advised"),
        )
        for r in rows()
    ],
    [(3, {"code": 1}, 1, 60, False)],
)
for rel in RISKY:
    edit(s, rel)
hook("stop", s)  # the same turn stops again, now with risky files
check("R14 ... a turn is counted once, however often it stops", len(rows()), 1)
check(
    "R14 ... but the note follows what the turn has changed by its last Stop",
    NOTE in told(hook("prompt", s, prompt_id="p2", prompt="next")),
    True,
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
edit(s, "src/a.py")
hook("stop", s)
check(
    "R14 a low score: the next prompt is told nothing",
    NOTE in told(hook("prompt", s, prompt_id="p2", prompt="next")),
    False,
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
for rel in RISKY:
    edit(s, rel)
high_stop = hook("stop", s)
first = hook("prompt", s, prompt_id="p2", prompt="next")
second = hook("prompt", s, prompt_id="p3", prompt="again")
check(
    "R15 guard + CI + lock file without a plan: the Stop is the same as with the score off",
    (high_stop.get("decision"), high_stop.get("reason") == ref_stop.get("reason")),
    (ref_stop.get("decision"), True),
)
check(
    "R15 ... the next prompt is told once (the score, no file name), the one after is not",
    (
        NOTE in told(first),
        "15" in risk_part(first),
        any(
            word in risk_part(first)
            for word in ("h.py", "uv.lock", "ci.yml", "_risk_proj")
        ),
        NOTE in told(second),
    ),
    (True, True, False, False),
)
last = rows()[-1] if rows() else {}
check(
    "R15 ... the observed row has the parts and no path or file name",
    (
        last.get("score"),
        last.get("kinds"),
        last.get("files"),
        last.get("repos"),
        last.get("advised"),
        any(
            word in json.dumps(last)
            for word in ("h.py", "uv.lock", "ci.yml", "_risk_proj")
        ),
    ),
    (15, {"guard": 1, "ci": 1, "deps": 1}, 3, 1, True, False),
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
for rel in RISKY:
    edit(s, rel)
hook("stop", s)
RELAY = 'Another Claude session sent a message:\n<agent-message from="a1">\ndone\n</agent-message>'
relayed = hook("prompt", s, prompt_id="p2", prompt=RELAY)
after = hook("prompt", s, prompt_id="p3", prompt="next")
check(
    "R16 a relayed prompt does not use the note up; the user's next prompt gets it",
    (NOTE in told(relayed), NOTE in told(after)),
    (False, True),
)

HIGHER = dict(ENV, CLAUDE_RISK_ADVICE_AT="16")
s = uuid.uuid4().hex
before = len(rows())
hook("prompt", s, HIGHER, prompt="work")
for rel in RISKY:
    edit(s, rel, HIGHER)
hook("stop", s, HIGHER)
check(
    "R17 a higher threshold from the environment: observed, not advised, not told",
    (
        len(rows()) - before,
        rows()[-1].get("advised") if rows() else None,
        NOTE in told(hook("prompt", s, HIGHER, "p2", prompt="next")),
    ),
    (1, False, False),
)

HOME2 = ROOT / "home2"
(HOME2 / "risk-observe.jsonl").mkdir(parents=True)
BROKEN = dict(ENV, CLAUDE_DISCIPLINE_HOME=str(HOME2))
s = uuid.uuid4().hex
hook("prompt", s, BROKEN, prompt="work")
for rel in RISKY:
    edit(s, rel, BROKEN)
broken_stop = hook("stop", s, BROKEN)
check(
    "R18 the observation cannot be written: same Stop, and the note still comes",
    (
        broken_stop.get("decision"),
        broken_stop.get("reason") == ref_stop.get("reason"),
        NOTE in told(hook("prompt", s, BROKEN, "p2", prompt="next")),
    ),
    (ref_stop.get("decision"), True, True),
)

s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
for rel in RISKY:
    edit(s, rel)
hook("stop", s)
check(
    "R19 a note left from before the score was switched off is not told",
    NOTE in told(hook("prompt", s, OFF, "p2", prompt="next")),
    False,
)

# ---------------------------------------------------------------- the approved task's plans
import discipline as disc

PLANS = PROJ / "docs" / "plans"
PLANS.mkdir(parents=True)
(PLANS / "a.md").write_text("---\ntask: t1\n---\n## 대안\n- A\n", encoding="utf-8")
(PLANS / "b.md").write_text("---\ntask: t2\n---\n## 단계\n", encoding="utf-8")
(PLANS / "c.md").write_bytes(b"---\ntask: t1\n---\n\xff\xfe not UTF-8\n")
(PLANS / "d.txt").write_text("---\ntask: t1\n---\n## 대안\n", encoding="utf-8")
texts = getattr(disc, "_approved_plan_texts", lambda state: None)


def plans_of(approval: object) -> object:
    got = texts({"approval": approval})
    return (
        [call("compares_alternatives", text) for text in got]
        if got is not None
        else None
    )


check(
    "R20 the plan files of the approved task are found: by task, .md only, readable only",
    [
        plans_of({"base": str(PROJ), "task_id": "t1"}),
        plans_of({"base": str(PROJ), "task_id": "t2"}),
        plans_of({"base": str(PROJ), "task_id": "t3"}),
    ],
    [[True], [False], []],
)
check(
    "R20 no approval, a broken one, a base without plans: no plan, no error",
    [
        plans_of(None),
        plans_of("x"),
        plans_of({"base": 3, "task_id": "t1"}),
        plans_of({"base": str(PROJ), "task_id": None}),
        plans_of({"base": str(ROOT / "nowhere"), "task_id": "t1"}),
    ],
    [[], [], [], [], []],
)

# ---- devenv-fix-e: the note judges the plan of the approval the turn worked under, also
# when the Stop has just cleared that approval (plan closed as done), and says what is true
HELD_T1 = {"base": str(PROJ), "task_id": "t1"}  # its plan compares alternatives
HELD_T2 = {"base": str(PROJ), "task_id": "t2"}  # its plan does not


def found(state: dict, held: object) -> list:
    return [
        call("compares_alternatives", text)
        for text in disc._approved_plan_texts(state, held)
    ]


check(
    "R21 without a live approval the plans of the one held before the re-check are read; "
    "a live approval comes first",
    [
        found({"approval": None}, HELD_T1),
        found({"approval": HELD_T2}, HELD_T1),
        found({}, None),
        found({}, "x"),
    ],
    [[True], [False], [], []],
)


def noted(held: object, approval: object = None) -> str:
    state = {
        "repos": {os.path.normcase(str(PROJ)): {}},
        "turn_changes": [os.path.normcase(str(PROJ / rel)) for rel in RISKY],
        "dirty": {},
        "approval": approval,
        "turn": 99,
    }
    disc.note_risk(state, held)
    return str(state.get("risk_note"))


check(
    "R22 a high score, the plan just closed compares alternatives: no note",
    noted(HELD_T1),
    "",
)
n_plan, n_none = noted(HELD_T2), noted(None)
check(
    "R22 a plan without a comparing heading or table: the note says exactly that",
    (
        NOTE in n_plan,
        "제목이나 표로 된 대안 비교가 없습니다" in n_plan,
        "승인된 계획 없이" in n_plan,
    ),
    (True, True, False),
)
check(
    "R22 no approved plan at all: the note says so and does not speak of a plan in progress",
    (
        NOTE in n_none,
        "승인된 계획 없이" in n_none,
        "진행 중인 계획" in n_none,
        "제목이나 표로 된" in n_none,
    ),
    (True, True, False, False),
)
check(
    "R22 ... through the hook too (the session of R15 had no plan), within the length limit",
    (
        "승인된 계획 없이" in risk_part(first),
        max(len(n_plan), len(n_none)) <= disc.RISK_NOTE_CHARS,
    ),
    (True, True),
)

n_text = noted({"base": str(PROJ), "task_id": "t3"})  # approved, but no plan text found
check(
    "R22 an approval whose plan text is not found: the note says that, not 'no plan'",
    (
        NOTE in n_text,
        "계획 글을 docs/plans에서 찾지 못해" in n_text,
        "승인된 계획 없이" in n_text,
        len(n_text) <= disc.RISK_NOTE_CHARS,
    ),
    (True, True, False, True),
)

# through the Stop hook: the Stop's own re-check clears the approval (here: a step was
# added to the plan) before the note is prepared; the plan the turn worked under is
# still the one to judge by. (A plan closed as done covers its in-scope changes, so
# such a turn has nothing left to score.)
import approvals

approvals.Store(HOME, ROOT / "w" / "hwm").admin_activate(lambda code: True, "test")


def closed_turn(task: str, body: str) -> str:
    text = (
        f"---\nstatus: in-progress\ntask: {task}\n---\n\n# {task}\n\n{body}"
        "## 변경 범위\n- hooks/**\n- .github/**\n- uv.lock\n\n## 단계\n- [ ] 1. a\n"
    )
    file = PLANS / f"2026-11-16-{task}.md"
    file.write_text(text, encoding="utf-8")
    s = uuid.uuid4().hex
    hook("prompt", s, prompt="work")
    hook(
        "post",
        s,
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": text, "filePath": str(file)},
    )
    for rel in RISKY:
        edit(s, rel)
    file.write_text(text.replace("1. a", "1. a, and more"), encoding="utf-8")
    hook("stop", s)
    return risk_part(hook("prompt", s, prompt_id="p2", prompt="next"))


check(
    "R23 through the hook, the approval cleared by the Stop itself: with a comparison no note, "
    "without one the note about the plan (not 'no plan')",
    (
        closed_turn(
            "r23a", "## 대안 비교\n| 선택지 | 판단 |\n|---|---|\n| A | x |\n\n"
        ),
        "제목이나 표로 된 대안 비교가 없습니다" in closed_turn("r23b", ""),
    ),
    ("", True),
)

for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
