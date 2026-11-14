"""SessionStart hook: inject git state and the in-progress plan so work resumes after
startup / resume / clear / compact.

Output (stdout JSON): hookSpecificOutput.additionalContext, at most MAX_CHARS characters.
Outside a git repository, or on any malformed input, it prints nothing.

Plan files: <repo>/docs/plans/*.md with frontmatter `status: in-progress`; the most
recently modified one is shown (path, goal section, checklist).
"""

from __future__ import annotations

import json
import os
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
# P0-09: every in-progress and blocked plan is mentioned, and a session started outside
# every repository is told which projects exist.
STATUS = re.compile(r"^status:\s*['\"]?([A-Za-z_-]+)['\"]?\s*$", re.MULTILINE)
TASK = re.compile(r"^task:\s*(\S+)\s*$", re.MULTILINE)
BLOCKED_REASON = re.compile(r"^blocked_reason:\s*(.*)$", re.MULTILINE)
LISTED_PLANS = 8
REASON_CHARS = 120
TASK_CHARS = 40
NOTE_LINE_CHARS = 240  # one note line: a path, a task id or reason, a count
NOTES_CHARS = 1200  # all notes together; the rest of the cap is the main plan's
PLAN_READ_CHARS = 262144  # a plan's frontmatter and steps are near its top
LISTED_PROJECTS = 30
DEFAULT_PROJECTS_ROOT = "C:/dev"
HEADER = "[session-context] 자동 주입된 작업 상태 (SessionStart hook)"


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


@dataclass(frozen=True, slots=True)
class PlanSummary:
    path: str
    status: str  # "in-progress" or "blocked"
    task: str
    unchecked: int
    reason: str  # blocked_reason, one line


def summarize_plan(path: str, text: str) -> PlanSummary | None:
    """One line's worth of a plan that is in progress or blocked; None for any other."""
    fm = FRONTMATTER.match(text)
    status = STATUS.search(fm.group(1)) if fm else None
    if (
        not fm
        or not status
        or status.group(1).lower() not in ("in-progress", "blocked")
    ):
        return None
    head, body = fm.group(1), text[fm.end() :]
    task = TASK.search(head)
    reason = BLOCKED_REASON.search(head)
    steps = [m.group(0) for m in CHECKBOX.finditer(body)]
    return PlanSummary(
        path=path,
        status=status.group(1).lower(),
        task=task.group(1)[:TASK_CHARS] if task else "-",
        unchecked=sum(1 for s in steps if UNCHECKED.match(s)),
        reason=" ".join(reason.group(1).strip().strip("'\"").split())[:REASON_CHARS]
        if reason
        else "",
    )


def plan_notes(summaries: tuple[PlanSummary, ...], main_path: str | None) -> list[str]:
    """Lines about the plans the main section does not show: other in-progress plans,
    blocked plans (shown only, they grant nothing) and plans whose state looks wrong."""
    others = [s for s in summaries if s.status == "in-progress" and s.path != main_path]
    blocked = [s for s in summaries if s.status == "blocked"]
    room = LISTED_PLANS
    out: list[str] = []

    def listed(rows: list[str]) -> list[str]:
        nonlocal room
        shown, room = rows[:room], max(0, room - len(rows))
        hidden = len(rows) - len(shown)
        return shown + ([f"… (+{hidden}개 더)"] if hidden else [])

    if others:
        out += [
            f"## 다른 진행 중 계획 ({len(others)}개)",
            "이어갈 작업은 사용자가 프롬프트 첫 줄에 task:<id>로 지정합니다.",
            *listed(
                [f"- {s.path} (task: {s.task}, 미체크 {s.unchecked}개)" for s in others]
            ),
        ]
    if blocked:
        out += [
            f"## 막힌 계획 ({len(blocked)}개, status: blocked, 표시만 하며 승인 권한 없음)",
            *listed(
                [
                    f"- {s.path}: {s.reason or '사유 없음'} (미체크 {s.unchecked}개)"
                    for s in blocked
                ]
            ),
        ]
    finished = [s for s in summaries if s.status == "in-progress" and s.unchecked == 0]
    for s in finished[:3]:
        out.append(
            f"경고: `{s.path}`는 진행 중인데 미체크 단계가 없습니다(완료 표시가 빠졌을 수 있음)."
        )
    return out


def build_outside(cwd: str, root: str, names: list[str]) -> str:
    """Context for a session started outside every repository: the projects that exist."""
    shown = names[:LISTED_PROJECTS]
    lines = [
        HEADER,
        (
            f"git 저장소 밖에서 시작했습니다(`{cwd}`). 프로젝트 폴더에서 세션을 시작해야 "
            "그 프로젝트의 계획과 메모리가 적용됩니다."
        ),
        "",
        f"## 프로젝트 (`{root}`, git 저장소 {len(names)}개)",
        *[f"- {n}" for n in shown],
    ]
    if len(names) > len(shown):
        lines.append(f"… (+{len(names) - len(shown)}개 더)")
    return "\n".join(lines)[:MAX_CHARS].rstrip()


def parse_plan(path: str, text: str) -> PlanInfo | None:
    """Return the plan if its frontmatter says `status: in-progress`, else None."""
    fm = FRONTMATTER.match(text)
    status = STATUS.search(fm.group(1)) if fm else None
    # The first status line decides, as in summarize_plan: one plan, one state.
    if not fm or not status or status.group(1).lower() != "in-progress":
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


def build_context(
    source: str, git: GitInfo, plan: PlanInfo | None, notes: tuple[str, ...] = ()
) -> str:
    head: list[str] = [HEADER]
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
    # Notes are bounded twice (each line, and all together) and never take more than what
    # the header, git state and plan heading leave: those always survive.
    room = min(NOTES_CHARS, max(0, MAX_CHARS - len(fixed) - 2))
    kept: list[str] = []
    for note in notes:
        note = note[:NOTE_LINE_CHARS]
        if len(note) + 1 > room:
            break
        kept.append(note)
        room -= len(note) + 1
    tail = ("\n\n" + "\n".join(kept)) if kept else ""
    if plan and plan.checklist:
        budget = MAX_CHARS - len(fixed) - len(tail) - len("\n체크리스트:\n")
        fixed += "\n체크리스트:\n" + "\n".join(fit_checklist(plan.checklist, budget))
    return (fixed[: MAX_CHARS - len(tail)].rstrip() + tail)[:MAX_CHARS].rstrip()


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


def read_plans(root: Path) -> tuple[PlanInfo | None, tuple[PlanSummary, ...]]:
    """(the most recently changed in-progress plan, every in-progress or blocked plan)."""
    plans_dir = root / "docs" / "plans"
    if not plans_dir.is_dir():
        return None, ()
    main: PlanInfo | None = None
    summaries: list[PlanSummary] = []
    dated: list[tuple[float, Path]] = []
    for path in plans_dir.glob("*.md"):
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:  # removed meanwhile, or a dangling link: not a plan to show
            continue
    for _mtime, path in sorted(dated, key=lambda d: d[0], reverse=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(PLAN_READ_CHARS)
        except OSError:  # unreadable plan file: try the next one
            continue
        rel = path.relative_to(root).as_posix()
        summary = summarize_plan(rel, text)
        if summary:
            summaries.append(summary)
        main = main or parse_plan(rel, text)
    return main, tuple(summaries)


def in_work_tree(cwd: Path) -> bool:
    """True when `cwd` or a folder above it holds a `.git` entry (or that cannot be told):
    then "outside every repository" would be a wrong thing to announce."""
    try:
        return any((d / ".git").exists() for d in (cwd, *cwd.parents))
    except OSError:
        return True


def list_projects(root: Path) -> list[str] | None:
    """Names of the folders directly under `root` that hold a `.git` entry (nothing inside
    them is read); None when the root cannot be listed."""
    try:
        entries = sorted(root.iterdir(), key=lambda e: e.name.lower())
    except OSError:  # no such folder, or not listable: nothing to say
        return None
    names: list[str] = []
    for entry in entries:
        if entry.name.startswith(("_", ".")):
            continue
        try:
            if entry.is_dir() and (entry / ".git").exists():
                names.append(entry.name)
        except OSError:  # one unreadable folder does not hide the others
            continue
    return names


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, OSError):  # malformed payload: inject nothing
        return
    if not isinstance(data, dict) or not data.get("cwd"):
        return
    cwd = Path(str(data["cwd"]))
    found = read_git(cwd)
    if found:
        root, info = found
        plan, summaries = read_plans(root)
        notes = tuple(plan_notes(summaries, plan.path if plan else None))
        context = build_context(str(data.get("source", "")), info, plan, notes)
    elif in_work_tree(cwd):
        return  # a repository git could not read (missing, slow, refused): say nothing
    else:
        projects_root = os.environ.get("CLAUDE_PROJECTS_ROOT") or DEFAULT_PROJECTS_ROOT
        names = list_projects(Path(projects_root))
        if not names:
            return
        context = build_outside(str(cwd), projects_root, names)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
