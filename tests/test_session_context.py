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


def run_hook(cwd: Path, source: str = "startup") -> str:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps({"session_id": "t", "source": source, "cwd": str(cwd)}),
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
check("only one plan shown", "2026-11-01-active.md" in ctx, False)

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
check("non-git dir -> nothing", run_hook(nogit), "")

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
