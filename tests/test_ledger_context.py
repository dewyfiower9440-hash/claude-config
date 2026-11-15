"""P1-06: a session start shows, for each plan in progress, how far the task ledger says
it got, and what looks different now. Shown only: it restores nothing.

    python -I tests/test_ledger_context.py

Throw-away repository and ledger home; the real ledger is never read or written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = Path(tempfile.mkdtemp(prefix="ledgerctx-"))
LHOME = ROOT / "ledger"
os.environ["CLAUDE_LEDGER_HOME"] = str(LHOME)
sys.path.insert(0, str(HOOKS))
import ledger
import session_context as sc

fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:160],
        "" if ok else f"(want {want!r})",
    )


notes = getattr(
    sc, "ledger_notes", lambda summaries, latest, problem, dirty, head_at: None
)
AT = 1_800_000_000.0


def plan(task: str, status: str = "in-progress") -> sc.PlanSummary:
    return sc.PlanSummary(f"docs/plans/{task or 'x'}.md", status, task, 2, "")


def entry(state: str, review: str = "current", at: object = AT) -> dict:
    return {
        "task": "widget",
        "state": state,
        "at": at,
        "review": review,
        "content": "c",
    }


def lines(*args: object) -> list[str]:
    out = notes(*args)
    return out if isinstance(out, list) else ["<no list>"]


one = lines([plan("widget")], {"widget": entry("LOCALLY_VERIFIED")}, None, 0, AT - 60)
check(
    "X2-T1 one plan in progress with a verified entry: a heading and one line, nothing flagged",
    (
        len(one),
        "widget" in one[-1],
        "로컬 검증" in one[-1],
        "current" in one[-1],
        "달라졌을" in one[-1] or "커밋" in one[-1],
    ),
    (2, True, True, True, False),
)
check(
    "X2-T10 the heading says it restores neither approval nor verification",
    ("복원하지 않" in one[0], "승인" in one[0], "검증" in one[0]),
    (True, True, True),
)
changed = lines([plan("widget")], {"widget": entry("CI_VERIFIED")}, None, 3, AT - 60)
check(
    "X2-T2 a verified state while the work tree has changes: said, with the count",
    ("CI 검증" in changed[-1], "달라졌을" in changed[-1], "3" in changed[-1]),
    (True, True, True),
)
later = lines(
    [plan("widget")], {"widget": entry("LOCALLY_VERIFIED")}, None, 0, AT + 600
)
check("X2-T3 a commit after the entry: said", "커밋" in later[-1], True)
unverified = lines(
    [plan("widget")], {"widget": entry("IMPLEMENTED")}, None, 3, AT + 600
)
check(
    "X2-T4 an unverified state: nothing to contradict, nothing flagged",
    (
        "미검증" in unverified[-1],
        "달라졌을" in unverified[-1],
        "커밋" in unverified[-1],
    ),
    (True, False, False),
)
missing = lines([plan("gadget")], {"widget": entry("CI_VERIFIED")}, None, 0, None)
check(
    "X2-T5 a plan whose task the ledger does not know",
    (
        "gadget" in missing[-1],
        "기록 없음" in missing[-1],
        "CI 검증" in "".join(missing),
    ),
    (True, True, False),
)
broken = lines(
    [plan("widget")],
    {"widget": entry("CI_VERIFIED")},
    "entry 2: content does not match",
    0,
    None,
)
check(
    "X2-T6 a ledger with a problem: said before the task lines",
    ("entry 2" in broken[1], "widget" in broken[-1], len(broken)),
    (True, True, 3),
)
check(
    "X2-T7 nothing in progress / only parked plans / a plan without a task: no lines",
    [
        lines([], {"widget": entry("CI_VERIFIED")}, None, 0, None),
        lines(
            [plan("widget", "blocked")], {"widget": entry("CI_VERIFIED")}, None, 0, None
        ),
        lines([plan("")], {}, None, 0, None),
        lines([], {}, "entry 1: broken", 0, None),
    ],
    [[], [], [], []],
)
six = lines([plan(f"t{i}") for i in range(6)], {}, None, 0, None)
check(
    "X2-T8 six plans: four are shown, every line within the note limit",
    (len(six), all(len(x) <= sc.NOTE_LINE_CHARS for x in six)),
    (5, True),
)
long_task = lines([plan("w" * 500)], {}, None, 0, None)
check(
    "X2-T8 a very long task id is cut",
    all(len(x) <= sc.NOTE_LINE_CHARS for x in long_task),
    True,
)
odd = [
    lines(
        [plan("widget")],
        {"widget": entry("LOCALLY_VERIFIED", at="yesterday")},
        None,
        0,
        AT,
    ),
    lines([plan("widget")], {"widget": entry("DONE", review=7)}, None, "many", "now"),
    lines([plan("widget")], {"widget": "not a dict"}, None, 0, None),
    lines([plan("widget")], None, None, None, None),
]
check(
    "X2-T9 odd values: no error, a line for the task every time",
    [len(x) for x in odd],
    [2, 2, 2, 2],
)

long_id = "t" * 60
cut = sc.summarize_plan(
    "docs/plans/long.md",
    f"---\nstatus: in-progress\ntask: {long_id}\n---\n- [ ] 1. a\n",
)
found = lines(
    [cut], {long_id: {**entry("CI_VERIFIED"), "task": long_id}}, None, 0, None
)
check(
    "X2-T5 a task id longer than a summary keeps is still matched with its ledger entry",
    (cut is not None and len(cut.task) < len(long_id), "CI 검증" in found[-1]),
    (True, True),
)

# ---------------------------------------------------------------- through the hook
REPO = ROOT / "repo"
(REPO / "docs" / "plans").mkdir(parents=True)
(REPO / "docs" / "plans" / "2026-11-15-widget.md").write_text(
    "---\nstatus: in-progress\ntask: widget\n---\n\n# Widget\n\n## 단계\n- [ ] 1. a\n",
    encoding="utf-8",
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


git("init", "-q")
git("add", "-A")
git("commit", "-q", "-m", "init")
TOP = git("rev-parse", "--show-toplevel")


def hook(env_extra: dict[str, str] | None = None) -> tuple[int, str]:
    env = dict(os.environ, CLAUDE_PROJECTS_ROOT=str(ROOT / "none"), **(env_extra or {}))
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "session_context.py")],
        input=json.dumps({"session_id": "t", "source": "startup", "cwd": str(REPO)}),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if not p.stdout.strip():
        return p.returncode, ""
    return p.returncode, json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


rc, empty_ctx = hook()
check(
    "X2-T12 no ledger yet: the start works, the plan is listed as without an entry",
    (rc, "widget" in empty_ctx, "기록 없음" in empty_ctx),
    (0, True, True),
)
ledger.record(
    TOP,
    {
        "task": "widget",
        "approval": "a1",
        "state": "LOCALLY_VERIFIED",
        "content": "c1",
        "roots": 1,
        "session": "s1",
        "review": "missing",
    },
    time.time() - 3600,
)
rc, ctx = hook()
check(
    "X2-T11 a ledger entry for the plan's task is shown at the start",
    (rc, "로컬 검증" in ctx, "missing" in ctx, "복원하지 않" in ctx),
    (0, True, True, True),
)
check(
    "X2-T11 ... the entry is an hour old, the commit is not: pointed out; inside the size limit",
    ("기록 뒤에 커밋이 있음" in ctx, len(ctx) <= sc.MAX_CHARS),
    (True, True),
)
(REPO / "more.txt").write_text("x\n", encoding="utf-8")
git("add", "-A")
git("commit", "-q", "-m", "after the entry")
rc, ctx = hook()
check(
    "X2-T11 ... a commit made after the entry is pointed out",
    "기록 뒤에 커밋이 있음" in ctx,
    True,
)
# what was shown before the ledger existed must not be pushed out by it
for i in range(5):
    (REPO / "docs" / "plans" / f"2026-11-1{i}-other{i}.md").write_text(
        f"---\nstatus: in-progress\ntask: other{i}\n---\n\n# o\n\n- [ ] 1. a\n",
        encoding="utf-8",
    )
for i in range(2):
    (REPO / "docs" / "plans" / f"2026-11-0{i}-parked{i}.md").write_text(
        f"---\nstatus: blocked\ntask: parked{i}\nblocked_reason: {'waiting ' * 12}\n---\n\n- [ ] 1. a\n",
        encoding="utf-8",
    )
rc, crowded = hook()
check(
    "X2-T8 many plans: the list of other plans and the parked plans come first and stay whole",
    (
        crowded.count("\n- docs/plans/"),
        "막힌 계획" in crowded,
        0 <= crowded.find("막힌 계획") < crowded.find("작업 원장")
        if "작업 원장" in crowded
        else True,
    ),
    (7, True, True),
)
for f in (REPO / "docs" / "plans").glob("2026-11-??-*"):
    if "widget" not in f.name:
        f.unlink()
target = next(LHOME.glob("*.jsonl"))
good = target.read_text(encoding="utf-8")
target.write_text(good.replace("LOCALLY_VERIFIED", "CI_VERIFIED"), encoding="utf-8")
rc, tampered = hook()
check(
    "X2-T12 an edited ledger line: the start works and says the ledger has a problem",
    (rc, "원장" in tampered and "문제" in tampered, "## git" in tampered),
    (0, True, True),
)
(ROOT / "afile").write_text("x", encoding="utf-8")
rc, nohome = hook({"CLAUDE_LEDGER_HOME": str(ROOT / "afile")})
check(
    "X2-T12 a file where the ledger folder should be: the start works as without a ledger",
    (rc, "## git" in nohome, "task:widget: 원장 기록 없음" in nohome),
    (0, True, True),
)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
