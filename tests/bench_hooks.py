"""P0-01 baseline: hook latency, session-context injection and the approval attack/normal corpus.

Not part of the gate (run_all.py): it records measurements, it does not assert.
Everything runs against throw-away repositories under tests/_tmp_bench and an isolated approval
home (CLAUDE_DISCIPLINE_HOME); the real approvals, state of real projects and notify toasts are
never touched.

  python -I tests/bench_hooks.py <label>     -> tests/_baseline/2026-11-11-<label>.json
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
# Outside every git repository (under ~/.claude the fixtures would resolve to this repo) and
# outside %TEMP% (discipline.IGNORED_PATH does not count edits there).
TMP = Path("C:/dev/_bench-hooks-tmp")
HOME = TMP / "approval_home"
OUT_DIR = HERE / "_baseline"
RUNS = 20

shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
sys.path.insert(0, str(HOOKS))
import discipline as d

SESSIONS: list[str] = []


def git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def hook(
    script: str, args: list[str], payload: dict[str, Any]
) -> tuple[float, str, int]:
    t0 = time.perf_counter()
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / script), *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return (time.perf_counter() - t0) * 1000, p.stdout, p.returncode


def session() -> str:
    s = "bench-" + uuid.uuid4().hex
    SESSIONS.append(s)
    return s


def disc(mode: str, s: str, **payload: Any) -> str:
    _ms, out, rc = hook("discipline.py", [mode], {"session_id": s, **payload})
    if rc != 0:
        raise RuntimeError(f"discipline {mode} exited {rc}")
    return out


def denied(out: str) -> bool:
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    except (ValueError, KeyError, TypeError):
        return False


def approval_of(s: str) -> dict[str, Any] | None:
    f = Path(tempfile.gettempdir()) / "claude-discipline" / f"{s}.json"
    state = json.loads(f.read_text(encoding="utf-8"))
    a = state.get("approval")
    return a if isinstance(a, dict) else None


# ---------------------------------------------------------------- fixture repositories
REPO = TMP / "dev" / "proj"  # a project
SUB = REPO / "vendor" / "nested"  # its own git root inside the project
OTHER = TMP / "dev" / "other"  # another project
NONGIT = TMP / "root"  # like C:\ (not a repository)
DEVDIR = TMP / "dev"  # like C:\dev (holds projects, not a repository)
for r in (REPO, SUB, OTHER):
    git_init(r)
NONGIT.mkdir()
(DEVDIR / "scratch").mkdir()
(REPO / "src").mkdir()
PLANS = REPO / "docs" / "plans"
PLANS.mkdir(parents=True)


def plan_text(task: str, status: str = "in-progress") -> str:
    return (
        f"---\nstatus: {status}\ntask: {task}\n---\n\n# {task}\n\n"
        "## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n- [ ] 2. b\n"
    )


def write_plan(name: str, task: str, status: str = "in-progress") -> Path:
    p = PLANS / name
    p.write_text(plan_text(task, status), encoding="utf-8")
    return p


def approve(task: str, cwd: Path = REPO) -> None:
    s = session()
    disc("prompt", s, prompt_id="ap", cwd=str(cwd), prompt=f"approve {task}")
    disc(
        "post",
        s,
        prompt_id="ap",
        cwd=str(cwd),
        tool_name="ExitPlanMode",
        tool_input={},
        tool_response={"plan": plan_text(task)},
    )


def rearmed(prompt: str, cwd: Path = REPO) -> str | None:
    s = session()
    disc("prompt", s, prompt_id="p1", cwd=str(cwd), prompt=prompt)
    a = approval_of(s)
    return str(a.get("task_id")) if a else None


# ---------------------------------------------------------------- 1. latency
def latency() -> dict[str, dict[str, float]]:
    s = session()
    edit = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(REPO / "src" / "a.py"),
            "old_string": "a",
            "new_string": "b",
        },
    }
    (REPO / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (REPO / "notes.md").write_text("# n\n", encoding="utf-8")
    cases: dict[str, tuple[str, list[str], dict[str, Any]]] = {
        "guard(Bash)": (
            "guard.py",
            [],
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
        ),
        "discipline pre(Edit)": (
            "discipline.py",
            ["pre"],
            {"session_id": s, "cwd": str(REPO), **edit},
        ),
        "discipline post(Edit)": (
            "discipline.py",
            ["post"],
            {"session_id": s, "cwd": str(REPO), **edit},
        ),
        "discipline prompt": (
            "discipline.py",
            ["prompt"],
            {"session_id": s, "cwd": str(REPO), "prompt": "hi", "prompt_id": "l"},
        ),
        "discipline stop": (
            "discipline.py",
            ["stop"],
            {"session_id": s, "cwd": str(REPO), "stop_hook_active": True},
        ),
        "session_context(startup)": (
            "session_context.py",
            [],
            {"session_id": s, "cwd": str(REPO), "source": "startup"},
        ),
        "format_lint(.md)": (
            "format_lint.py",
            [],
            {
                "tool_name": "Edit",
                "cwd": str(REPO),
                "tool_input": {"file_path": str(REPO / "notes.md")},
            },
        ),
        "format_lint(.py)": (
            "format_lint.py",
            [],
            {"cwd": str(REPO), **edit},
        ),
    }
    result: dict[str, dict[str, float]] = {}
    for name, (script, args, payload) in cases.items():
        times = sorted(hook(script, args, payload)[0] for _ in range(RUNS))
        result[name] = {
            "p50_ms": round(statistics.median(times), 1),
            "p95_ms": round(times[int(0.95 * (len(times) - 1))], 1),
            "max_ms": round(times[-1], 1),
        }
    return result


# ---------------------------------------------------------------- 2. sessions A-G
def sessions() -> dict[str, dict[str, Any]]:
    write_plan("2026-11-01-alpha.md", "alpha")
    write_plan("2026-11-02-widget.md", "widget")
    write_plan("2026-11-03-parked.md", "parked", status="blocked")
    scenarios = {
        "A cwd like C:\\ (not a repo)": (NONGIT, "startup"),
        "B cwd like C:\\dev (holds repos)": (DEVDIR, "startup"),
        "C project root": (REPO, "startup"),
        "D project subdirectory": (REPO / "src", "startup"),
        "E resume": (REPO, "resume"),
        "F compact": (REPO, "compact"),
        "G non-repo folder under C:\\dev": (DEVDIR / "scratch", "startup"),
    }
    out: dict[str, dict[str, Any]] = {}
    for name, (cwd, source) in scenarios.items():
        _ms, text, rc = hook(
            "session_context.py",
            [],
            {"session_id": session(), "cwd": str(cwd), "source": source},
        )
        ctx = ""
        if text.strip():
            try:
                ctx = json.loads(text)["hookSpecificOutput"]["additionalContext"]
            except (ValueError, KeyError, TypeError):
                ctx = text
        shown = [
            n
            for n in ("alpha", "widget", "parked")
            if f"2026-11-0{'123'[['alpha', 'widget', 'parked'].index(n)]}-{n}.md" in ctx
        ]
        out[name] = {
            "exit": rc,
            "injected_chars": len(ctx),
            "context_head": ctx.splitlines()[0][:80] if ctx else "",
            "plans_shown": shown,
            "plans_in_repo": ["alpha", "widget", "parked(blocked)"],
        }
    return out


# ---------------------------------------------------------------- 3. approval corpus
def corpus() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def row(
        kind: str, cid: str, what: str, got: object, want: object, owner: str
    ) -> None:
        rows.append(
            {
                "kind": kind,
                "id": cid,
                "case": what,
                "got": got,
                "want": want,
                "pass": got == want,
                "owner": owner,
            }
        )

    for p in PLANS.glob("*.md"):
        p.unlink()
    write_plan("2026-11-01-alpha.md", "alpha")
    write_plan("2026-11-02-widget.md", "widget")
    approve("alpha")
    approve("widget")

    row(
        "attack",
        "A1",
        "no task named in the prompt -> no re-arm",
        rearmed("continue"),
        None,
        "P0-03",
    )
    row(
        "attack",
        "A2",
        "prompt names another task -> no re-arm",
        rearmed("task:other please"),
        None,
        "P0-03",
    )
    row(
        "attack",
        "A3",
        "nested git root under the project -> no inherited approval",
        rearmed("task:widget", cwd=SUB),
        None,
        "P0-03",
    )
    row(
        "attack",
        "A4",
        "two active approved plans, prompt names widget -> widget",
        rearmed("task:widget"),
        "widget",
        "P0-03",
    )
    other_plan = str(OTHER / "docs" / "plans" / "x.md")
    rec = {"scope": [d._norm(str(REPO / "src/**"))], "rearmed_from": "x"}
    row(
        "attack",
        "A5",
        "other repo's docs/plans file inside an approval scope -> refused",
        d.in_approved_scope(rec, other_plan),
        False,
        "P0-05",
    )
    mixed = {"scope": [d._norm(str(REPO / "src/**")), d._norm(str(TMP / "rawdata/**"))]}
    row(
        "attack",
        "A6",
        "data path approved through a code scope -> refused",
        d.in_approved_scope(mixed, str(TMP / "rawdata" / "raw" / "x.jsonl")),
        False,
        "P0-05",
    )

    s = session()
    disc("prompt", s, prompt_id="u1", cwd=str(OTHER), prompt="edit things")
    allowed = 0
    for i in range(7):
        f = str(OTHER / f"f{i}.py")
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": f, "old_string": "a", "new_string": "b"},
        }
        if denied(disc("pre", s, prompt_id="u1", cwd=str(OTHER), **payload)):
            break
        allowed += 1
        disc("post", s, prompt_id="u1", cwd=str(OTHER), **payload)
    row(
        "attack",
        "A7",
        "Edits allowed in one prompt without any approval (policy B: 0)",
        allowed,
        0,
        "P0-06",
    )
    s = session()
    disc("prompt", s, prompt_id="u2", cwd=str(OTHER), prompt="x")
    out = disc(
        "pre",
        s,
        prompt_id="u2",
        cwd=str(OTHER),
        tool_name="Bash",
        tool_input={"command": "echo x > src/a.py"},
    )
    row(
        "attack",
        "A8",
        "unapproved Bash redirect write -> denied before it runs",
        denied(out),
        True,
        "P0-07",
    )
    # Isolated from A1-A4: only the widget plan exists, so the result is about blocked alone.
    alpha = PLANS / "2026-11-01-alpha.md"
    alpha.unlink()
    widget = PLANS / "2026-11-02-widget.md"
    widget.write_text(plan_text("widget", "blocked"), encoding="utf-8")
    widget.write_text(plan_text("widget", "in-progress"), encoding="utf-8")
    row(
        "attack",
        "A9",
        "plan parked as blocked then flipped back -> old approval revoked",
        rearmed("task:widget"),
        None,
        "P0-04",
    )

    # Normal cases run with both approved plans active, so selection is exercised.
    write_plan("2026-11-01-alpha.md", "alpha")
    write_plan("2026-11-02-widget.md", "widget")
    row(
        "normal",
        "N1",
        "prompt names the approved task -> re-armed",
        rearmed("task:widget 계속"),
        "widget",
        "P0-03",
    )
    row(
        "normal",
        "N2",
        "prompt names the plan file -> re-armed",
        rearmed("/implement docs/plans/2026-11-02-widget.md"),
        "widget",
        "P0-03",
    )
    row(
        "normal",
        "N3",
        "project subdirectory, task named -> re-armed",
        rearmed("task:widget", cwd=REPO / "src"),
        "widget",
        "P0-03",
    )
    s = session()
    disc("prompt", s, prompt_id="w1", cwd=str(REPO), prompt="task:widget")
    in_scope_denied = False
    for i in range(7):
        f = str(REPO / "src" / f"s{i}.py")
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": f, "old_string": "a", "new_string": "b"},
        }
        in_scope_denied |= denied(
            disc("pre", s, prompt_id="w1", cwd=str(REPO), **payload)
        )
        disc("post", s, prompt_id="w1", cwd=str(REPO), **payload)
    row(
        "normal",
        "N4",
        "7 in-scope edits under a re-armed approval -> none denied",
        in_scope_denied,
        False,
        "P0-03",
    )
    return rows


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=HERE.parent,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--", "hooks"],
            cwd=HERE.parent,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )
    result = {
        "label": label,
        "commit": commit,
        "hooks_dirty": dirty,
        "python": sys.version.split()[0],
        "runs_per_hook": RUNS,
        "latency": latency(),
        "sessions": sessions(),
        "corpus": corpus(),
    }
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"2026-11-11-{label}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, v in result["latency"].items():
        print(f"{name:28} p50 {v['p50_ms']:7.1f}  p95 {v['p95_ms']:7.1f}")
    for name, v in result["sessions"].items():
        print(
            f"{name:34} chars {v['injected_chars']:5}  head {v['context_head']!r}  plans {v['plans_shown']}"
        )
    for r in result["corpus"]:
        print(
            f"{r['id']:3} {'PASS' if r['pass'] else 'FAIL'} [{r['owner']}] {r['case']} -> {r['got']!r}"
        )
    print("saved", out)
    for s in SESSIONS:
        (Path(tempfile.gettempdir()) / "claude-discipline" / f"{s}.json").unlink(
            missing_ok=True
        )
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
