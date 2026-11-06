"""SessionStart hook: inject git state and the in-progress plan so work resumes after
startup / resume / clear / compact.

Output (stdout JSON): hookSpecificOutput.additionalContext, at most MAX_CHARS characters.
Outside a git repository, or on any malformed input, it prints nothing.

Plan files: <repo>/docs/plans/*.md with frontmatter `status: in-progress`; the most
recently modified one is shown (path, goal section, checklist).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 4000
STATUS_LINES = 20
COMMITS = 5
GIT_TIMEOUT_S = 5
GOAL_CHARS = 600

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", re.DOTALL)
IN_PROGRESS = re.compile(r"^status:\s*['\"]?in-progress['\"]?\s*$", re.MULTILINE)
GOAL_HEADING = re.compile(r"^#{1,4}\s*(목표|goal)\b.*$", re.IGNORECASE | re.MULTILINE)
CHECKBOX = re.compile(r"^\s*[-*]\s\[[ xX]\]\s.*$", re.MULTILINE)
UNCHECKED = re.compile(r"^\s*[-*]\s\[ \]")


# ---------------------------------------------------------------- pure core
@dataclass(frozen=True, slots=True)
class GitInfo:
    branch: str
    status: tuple[str, ...]
    commits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanInfo:
    path: str
    goal: str
    checklist: tuple[str, ...]


def parse_plan(path: str, text: str) -> PlanInfo | None:
    """Return the plan if its frontmatter says `status: in-progress`, else None."""
    fm = FRONTMATTER.match(text)
    if not fm or not IN_PROGRESS.search(fm.group(1)):
        return None
    body = text[fm.end() :]
    goal = ""
    heading = GOAL_HEADING.search(body)
    if heading:
        rest = body[heading.end() :]
        nxt = re.search(r"^#", rest, re.MULTILINE)
        goal = rest[: nxt.start() if nxt else len(rest)].strip()[:GOAL_CHARS]
    steps = tuple(m.group(0).strip() for m in CHECKBOX.finditer(body))
    return PlanInfo(path=path, goal=goal, checklist=steps)


def fit_checklist(steps: tuple[str, ...], budget: int) -> list[str]:
    """Fit steps into `budget` chars: drop checked steps first, then trailing ones."""
    if sum(len(s) + 1 for s in steps) <= budget:
        return list(steps)
    open_steps = [s for s in steps if UNCHECKED.match(s)]
    done = len(steps) - len(open_steps)
    out = [f"(완료된 단계 {done}개 생략)"] if done else []
    used = sum(len(s) + 1 for s in out)
    for i, step in enumerate(open_steps):
        more = f"… (남은 단계 {len(open_steps) - i}개 더 있음, 계획 파일 참조)"
        if used + len(step) + 1 + len(more) + 1 > budget:
            out.append(more)
            break
        out.append(step)
        used += len(step) + 1
    return out


def build_context(source: str, git: GitInfo, plan: PlanInfo | None) -> str:
    head: list[str] = ["[session-context] 자동 주입된 작업 상태 (SessionStart hook)"]
    if source == "compact" and plan:
        head.append(
            f"컨텍스트가 compact 되었습니다. 계속하기 전에 계획 파일 `{plan.path}`을 다시 읽고, "
            "체크리스트의 첫 미완료 단계부터 재개하세요. 완료한 단계는 체크박스를 갱신하세요."
        )
    elif source == "compact":
        head.append(
            "컨텍스트가 compact 되었습니다. 진행 중이던 작업은 아래 git 상태와 "
            "대화 요약을 기준으로 다시 확인한 뒤 재개하세요(파일을 다시 읽고 판단)."
        )

    status = list(git.status[:STATUS_LINES])
    if len(git.status) > STATUS_LINES:
        status.append(f"… (+{len(git.status) - STATUS_LINES}개 더)")
    lines = [f"## git: 브랜치 `{git.branch}`"]
    lines += (
        ["변경 파일(git status --short):", *status] if status else ["작업 트리 깨끗함"]
    )
    if git.commits:
        lines += ["최근 커밋:", *git.commits]

    plan_lines: list[str] = []
    if plan:
        plan_lines = [f"## 진행 중인 계획: `{plan.path}`"]
        if plan.goal:
            plan_lines += ["목표:", plan.goal]

    fixed = "\n".join([*head, "", *lines, "", *plan_lines])
    if plan and plan.checklist:
        budget = MAX_CHARS - len(fixed) - len("\n체크리스트:\n")
        fixed += "\n체크리스트:\n" + "\n".join(fit_checklist(plan.checklist, budget))
    return fixed[:MAX_CHARS].rstrip()


# ---------------------------------------------------------------- I/O shell
def git(cwd: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):  # git missing or hung: no context
        return None
    return p.stdout if p.returncode == 0 else None


def read_git(cwd: Path) -> tuple[Path, GitInfo] | None:
    top = git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return None
    root = Path(top.strip())
    status = (git(root, "status", "--short", "--branch") or "").splitlines()
    branch = "?"
    if status and status[0].startswith("## "):
        branch = status.pop(0)[3:].split("...")[0].replace("No commits yet on ", "")
    log = git(root, "log", "--oneline", f"-{COMMITS}") or ""
    return root, GitInfo(
        branch=branch, status=tuple(status), commits=tuple(log.splitlines())
    )


def read_plan(root: Path) -> PlanInfo | None:
    plans_dir = root / "docs" / "plans"
    if not plans_dir.is_dir():
        return None
    candidates = sorted(
        plans_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # unreadable plan file: try the next one
            continue
        plan = parse_plan(path.relative_to(root).as_posix(), text)
        if plan:
            return plan
    return None


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, OSError):  # malformed payload: inject nothing
        return
    if not isinstance(data, dict) or not data.get("cwd"):
        return
    found = read_git(Path(str(data["cwd"])))
    if not found:
        return
    root, info = found
    context = build_context(str(data.get("source", "")), info, read_plan(root))
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
