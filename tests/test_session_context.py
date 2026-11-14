"""Scenario tests for ~/.claude/hooks/session_context.py (run with python -I)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session_context.py"
ROOT = Path(tempfile.mkdtemp(prefix="sessctx-"))
LIMIT = 4000
fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:80],
        "" if ok else f"(want {want!r})",
    )


def run_hook(cwd: Path, source: str = "startup", projects: Path | None = None) -> str:
    # The projects root is always a throw-away folder here: the real one is never listed.
    env = dict(os.environ, CLAUDE_PROJECTS_ROOT=str(projects or ROOT / "no-projects"))
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps({"session_id": "t", "source": source, "cwd": str(cwd)}),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return ""
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart", out
    return hso["additionalContext"]


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


PLAN = """---
status: {status}
---

# {title}

## 목표
{goal}

## 단계
- [x] 1. first step done
- [ ] 2. second step pending
"""

# ---- repo with an in-progress plan, a newer draft plan, and commits
repo = ROOT / "repo"
repo.mkdir()
git(repo, "init", "-b", "main")
write(repo / "README.md", "hi\n")
git(repo, "add", ".")
git(repo, "commit", "-m", "feat: initial commit")
active = repo / "docs" / "plans" / "2026-11-01-active.md"
write(
    active,
    PLAN.format(status="in-progress", title="Active", goal="Ship the widget API."),
)
draft = repo / "docs" / "plans" / "2026-11-05-draft.md"
write(draft, PLAN.format(status="draft", title="Draft", goal="Not started."))
old_done = repo / "docs" / "plans" / "2026-10-01-done.md"
write(old_done, PLAN.format(status="done", title="Done", goal="Old."))
git(repo, "add", ".")
git(repo, "commit", "-m", "docs: add plans")
write(repo / "dirty.txt", "x\n")

ctx = run_hook(repo)
check("git repo -> context emitted", bool(ctx), True)
check("branch shown", "main" in ctx, True)
check("recent commit shown", "docs: add plans" in ctx, True)
check("dirty file in status", "dirty.txt" in ctx, True)
check("in-progress plan path shown", "2026-11-01-active.md" in ctx, True)
check("plan goal shown", "Ship the widget API." in ctx, True)
check("unchecked step shown", "- [ ] 2. second step pending" in ctx, True)
check("checked step shown", "- [x] 1. first step done" in ctx, True)
check("draft plan ignored", "2026-11-05-draft.md" in ctx, False)
check("done plan ignored", "2026-10-01-done.md" in ctx, False)
check("startup -> no compact resume instruction", "compact" in ctx.lower(), False)

ctx_c = run_hook(repo, source="compact")
check(
    "compact -> resume instruction",
    "compact" in ctx_c.lower() and "다시 읽" in ctx_c,
    True,
)

# ---- newest in-progress plan wins
newer = repo / "docs" / "plans" / "2026-11-04-newer.md"
write(newer, PLAN.format(status="in-progress", title="Newer", goal="Newer goal."))
os.utime(newer, (2_000_000_000, 2_000_000_000))
ctx = run_hook(repo)
check("newest in-progress plan chosen", "2026-11-04-newer.md" in ctx, True)
# P0-09: the other in-progress plan is no longer hidden; it gets one line, not a checklist.
check(
    "C-T1 the other in-progress plan is listed with its open steps",
    "- docs/plans/2026-11-01-active.md (task: -, 미체크 1개)" in ctx,
    True,
)
check(
    "C-T1 ... once, under its own heading, with how to pick one",
    (
        ctx.count("2026-11-01-active.md"),
        "## 다른 진행 중 계획" in ctx,
        "task:<id>" in ctx,
    ),
    (1, True, True),
)
check(
    "C-T1 the main plan keeps its goal and checklist",
    "Newer goal." in ctx and "- [ ] 2. second step pending" in ctx,
    True,
)
check(
    "C-T10 draft and done plans still appear nowhere",
    "draft.md" in ctx or "done.md" in ctx,
    False,
)

# ---- size cap with a huge checklist and many dirty files
big = repo / "docs" / "plans" / "2026-11-06-big.md"
write(
    big,
    PLAN.format(status="in-progress", title="Big", goal="Big goal.")
    + "".join(f"- [ ] {i}. step {'x' * 60}\n" for i in range(3, 400)),
)
os.utime(big, (2_100_000_000, 2_100_000_000))
for i in range(30):
    write(repo / f"untracked_{i:02}.txt", "x\n")
ctx = run_hook(repo, source="compact")
check("context capped at 4000 chars", len(ctx) <= LIMIT, True)
check("capped context keeps resume instruction", "다시 읽" in ctx, True)
status_lines = [ln for ln in ctx.splitlines() if "untracked_" in ln]
check("status limited to 20 lines", len(status_lines) <= 20, True)

# ---- repo without plans
plain = ROOT / "plain"
plain.mkdir()
git(plain, "init", "-b", "dev")
write(plain / "a.txt", "a\n")
git(plain, "add", ".")
git(plain, "commit", "-m", "chore: first")
ctx = run_hook(plain)
check("no plans -> git info only", "dev" in ctx and "chore: first" in ctx, True)
check("no plans -> no plan section", "docs/plans" in ctx, False)

# ---- repo without commits
empty = ROOT / "empty"
empty.mkdir()
git(empty, "init", "-b", "main")
check("repo without commits -> no crash", isinstance(run_hook(empty), str), True)

# ---- not a git repository
nogit = ROOT / "nogit"
nogit.mkdir()
write(
    nogit / "docs" / "plans" / "2026-11-01-x.md",
    PLAN.format(status="in-progress", title="X", goal="G"),
)
check("C-T8 outside a repository, no projects root -> nothing", run_hook(nogit), "")


# ---- P0-09: blocked plans, mismatch warning, list limits
def plan_text(
    status: str, extra: str = "", steps: str = "- [x] 1. a\n- [ ] 2. b\n- [ ] 3. c\n"
) -> str:
    return f"---\nstatus: {status}\n{extra}---\n\n# P\n\n## 목표\nG\n\n## 단계\n{steps}"


r2 = ROOT / "repo2"
r2.mkdir()
git(r2, "init", "-b", "main")
write(r2 / "README.md", "hi\n")
git(r2, "add", ".")
git(r2, "commit", "-m", "feat: initial commit")
plans2 = r2 / "docs" / "plans"
write(
    plans2 / "2026-11-02-stuck.md",
    plan_text(
        "blocked", "task: stuck\nblocked_reason: waiting for the vendor API key\n"
    ),
)
ctx = run_hook(r2)
check(
    "C-T3 only a blocked plan: listed, and no main-plan section",
    ("## 막힌 계획" in ctx, "## 진행 중인 계획" in ctx),
    (True, False),
)
check(
    "C-T2 a blocked plan shows path, reason and open steps",
    "- docs/plans/2026-11-02-stuck.md: waiting for the vendor API key (미체크 2개)"
    in ctx,
    True,
)
check("C-T2 ... says it grants nothing", "승인 권한 없음" in ctx, True)
check("C-T2 ... and its checklist is not injected", "- [ ] 2. b" in ctx, False)

write(plans2 / "2026-11-03-main.md", plan_text("in-progress", "task: mainp\n"))
os.utime(plans2 / "2026-11-03-main.md", (2_000_000_100, 2_000_000_100))
write(
    plans2 / "2026-11-03-alldone.md",
    plan_text("in-progress", "task: alld\n", "- [x] 1. a\n- [X] 2. b\n"),
)
ctx = run_hook(r2)
check(
    "C-T4 in progress with nothing unchecked: a mismatch warning",
    "경고" in ctx
    and "2026-11-03-alldone.md" in ctx
    and "미체크 단계가 없습니다" in ctx,
    True,
)
check(
    "C-T4 the main plan with open steps gets no warning",
    ctx.count("미체크 단계가 없습니다"),
    1,
)
check("C-T1 the other plan's task id is shown", "(task: alld, 미체크 0개)" in ctx, True)

long_reason = "x" * 150 + " second\tpart " + "y" * 150
write(
    plans2 / "2026-11-04-long.md",
    plan_text("blocked", f"blocked_reason: '{long_reason}'\n"),
)
ctx = run_hook(r2)
line = next(ln for ln in ctx.splitlines() if "2026-11-04-long.md" in ln)
check(
    "C-T6 a long reason is cut to one line of at most 120 characters",
    (len(line.split(": ", 1)[1].rsplit(" (미체크", 1)[0]) <= 120, "\t" in line),
    (True, False),
)

for i in range(12):
    write(
        plans2 / f"2026-11-05-many{i:02}.md",
        plan_text("blocked" if i % 2 else "in-progress", f"task: m{i}\n"),
    )
ctx = run_hook(r2)
listed = [ln for ln in ctx.splitlines() if ln.startswith("- docs/plans/")]
check(
    "C-T5 many plans: eight lines, the rest counted, within the size cap",
    (len(listed), "개 더)" in ctx, len(ctx) <= LIMIT),
    (8, True, True),
)

# ---- review: nothing in a plan file can push out the header, git state or main plan
r3 = ROOT / "repo3"
r3.mkdir()
git(r3, "init", "-b", "main")
write(r3 / "README.md", "hi\n")
git(r3, "add", ".")
git(r3, "commit", "-m", "feat: initial commit")
plans3 = r3 / "docs" / "plans"
write(plans3 / "2026-11-03-main.md", plan_text("in-progress", "task: mainp\n"))
os.utime(plans3 / "2026-11-03-main.md", (2_000_000_100, 2_000_000_100))
write(
    plans3 / "2026-11-02-huge.md",
    plan_text("in-progress", "task: " + "T" * 5000 + "\n"),
)
for i in range(12):
    write(
        plans3 / f"2026-11-01-b{i:02}.md",
        plan_text("blocked", "blocked_reason: " + "r" * 300 + "\n"),
    )
for i in range(25):
    write(r3 / ("dirty-" + "d" * 100 + f"-{i:02}.txt"), "x\n")
ctx = run_hook(r3, source="compact")
check(
    "C-T11 a huge task id and many notes: header, resume instruction, git and main plan survive",
    (
        len(ctx) <= LIMIT,
        "다시 읽" in ctx,
        "## git:" in ctx,
        "## 진행 중인 계획: `docs/plans/2026-11-03-main.md`" in ctx,
        "T" * 41 in ctx,
    ),
    (True, True, True, True, False),
)
write(
    plans3 / "2026-11-04-crlf.md",
    plan_text("blocked", "blocked_reason: 'waiting: on x'\n").replace("\n", "\r\n"),
)
r4 = ROOT / "repo4"
r4.mkdir()
git(r4, "init", "-b", "main")
write(
    r4 / "docs" / "plans" / "2026-11-04-crlf.md",
    plan_text("blocked", "blocked_reason: 'waiting: on x'\n"),
)
p4 = r4 / "docs" / "plans" / "2026-11-04-crlf.md"
p4.write_bytes(p4.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
write(
    r4 / "docs" / "plans" / "2026-11-05-case.md",
    plan_text("In-Progress", "task: cased\n"),
)
write(
    r4 / "docs" / "plans" / "2026-11-06-twice.md",
    plan_text("blocked", "status: in-progress\ntask: twice\n"),
)
ctx = run_hook(r4)
check(
    "C-T12 a CRLF plan: the quoted reason is unquoted",
    "2026-11-04-crlf.md: waiting: on x (미체크" in ctx,
    True,
)
check(
    "C-T12 status in another letter case is the main plan, not also an 'other' one",
    (
        "## 진행 중인 계획: `docs/plans/2026-11-05-case.md`" in ctx,
        "## 다른 진행 중 계획" in ctx,
    ),
    (True, False),
)
check(
    "C-T12 two status lines: the first decides, the plan appears once",
    (ctx.count("2026-11-06-twice.md"), "2026-11-06-twice.md: 사유 없음" in ctx),
    (1, True),
)
broken = ROOT / "brokenrepo"
(broken / ".git").mkdir(parents=True)  # a .git entry git cannot read
check(
    "C-T13 a folder with a .git entry git cannot read: not announced as outside a repository",
    run_hook(broken, projects=ROOT / "repo"),
    "",
)

# ---- P0-09: started outside every repository
projects = ROOT / "projects"
for name in ("beta", "alpha", "gamma"):
    (projects / name / ".git").mkdir(parents=True)
for name in ("plainfolder", "_tmp", ".hidden"):
    (projects / name).mkdir()
(projects / "_tmp" / ".git").mkdir()
(projects / ".hidden" / ".git").mkdir()
(projects / "afile.txt").write_text("x", encoding="utf-8")
ctx = run_hook(nogit, projects=projects)
check(
    "C-T7 outside a repository: the repositories under the projects root, by name",
    [ln for ln in ctx.splitlines() if ln.startswith("- ")],
    ["- alpha", "- beta", "- gamma"],
)
check(
    "C-T7 ... with the advice to start inside a project, and no plan content",
    ("프로젝트 폴더에서 세션을 시작" in ctx, "2026-11-01-x.md" in ctx),
    (True, False),
)
for i in range(32):
    (projects / f"p{i:02}" / ".git").mkdir(parents=True)
ctx = run_hook(nogit, projects=projects)
check(
    "C-T9 many repositories: thirty names and the rest counted",
    (len([ln for ln in ctx.splitlines() if ln.startswith("- ")]), "(+5개 더)" in ctx),
    (30, True),
)
check(
    "C-T7 inside a repository the projects list is not injected",
    "- alpha" in run_hook(repo, projects=projects),
    False,
)

# ---- malformed payload never fails
p = subprocess.run(
    [sys.executable, "-I", str(HOOK)],
    input="not json",
    capture_output=True,
    text=True,
    check=False,
)
check(
    "malformed payload -> exit 0, no output", (p.returncode, p.stdout.strip()), (0, "")
)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
