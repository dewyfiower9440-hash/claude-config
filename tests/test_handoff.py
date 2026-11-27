"""P0-12: the session handoff record and its restore at the next start (python -I).

SessionEnd (`handoff.py end`) writes a small state-only record per repository; SessionStart
(`session_context.py`) leaves an open-session marker, shows the last record and reports a
marker another session left behind. A handoff never carries or restores an approval.
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

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = Path(tempfile.mkdtemp(prefix="handoff-"))
STORE = ROOT / "store"
os.environ["CLAUDE_HANDOFF_HOME"] = str(STORE)
os.environ["CLAUDE_PROJECTS_ROOT"] = str(ROOT / "no-projects")
# The SessionStart hook lists the vault's knowledge notes: never this machine's real ones.
os.environ["CLAUDE_VAULT_KNOWLEDGE"] = "0"
# SessionEnd also sweeps old session state (P0-13): keep that away from the real folder.
os.environ["CLAUDE_STATE_GC_DIR"] = str(ROOT / "state-gc")
sys.stdout.reconfigure(errors="backslashreplace")
sys.path.insert(0, str(HOOKS))
import handoff

fails = 0
LIMIT = 4000


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:110],
        "" if ok else f"(want {want!r})",
    )


# ---- the pure record
GIT = {"root": "C:/r", "branch": "main", "head": "0123456789ab", "dirty": 3}
PLAN = {"path": "docs/plans/p.md", "next_step": "- [ ] 2. do it", "open_steps": 4}
rec = handoff.build_handoff({"session_id": "s1", "reason": "other"}, GIT, PLAN, 1000.0)
check(
    "H-T1 a record holds the allow-listed fields with their values",
    rec,
    {
        "v": 1,
        "at": 1000.0,
        "session_id": "s1",
        "reason": "other",
        "git_root": "C:/r",
        "branch": "main",
        "head": "0123456789ab",
        "dirty": 3,
        "plan": "docs/plans/p.md",
        "next_step": "- [ ] 2. do it",
        "open_steps": 4,
    },
)
noisy = handoff.build_handoff(
    {
        "session_id": "s1",
        "reason": "other",
        "approval": {"task_id": "x"},
        "prompt": "secret",
    },
    {**GIT, "scope": ["**"], "token": "abc"},
    {**PLAN, "approval_id": "a"},
    1000.0,
)
check(
    "H-T2 nothing outside the allow-list is kept",
    sorted(noisy) == sorted(handoff.FIELDS),
    True,
)
check(
    "H-T2 a stored file is reduced to the allow-list when read back",
    sorted(
        handoff.clean({**rec, "approval": {"x": 1}, "scope": ["**"], "dirty": "many"})
    ),
    sorted(k for k in handoff.FIELDS if k != "dirty"),
)
long_step = handoff.build_handoff(
    {"session_id": "s", "reason": "r"},
    GIT,
    {**PLAN, "next_step": "a\nb\t" + "x" * 500},
    1.0,
)["next_step"]
check(
    "H-T3 a long, multi-line step becomes one line of at most 200",
    (len(long_step) <= 200, "\n" in long_step, long_step[:4]),
    (True, False, "a b "),
)


# ---- the hooks
def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def run(
    script: str, args: list[str], payload: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(HOOKS / script), *args],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def end(
    cwd: Path, session: str, reason: str = "prompt_input_exit"
) -> subprocess.CompletedProcess[str]:
    return run(
        "handoff.py",
        ["end"],
        {
            "hook_event_name": "SessionEnd",
            "session_id": session,
            "reason": reason,
            "cwd": str(cwd),
        },
    )


def start(cwd: Path, session: str, source: str = "startup") -> str:
    p = run(
        "session_context.py",
        [],
        {
            "hook_event_name": "SessionStart",
            "session_id": session,
            "source": source,
            "cwd": str(cwd),
        },
    )
    assert p.returncode == 0, p.stderr
    return (
        json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        if p.stdout.strip()
        else ""
    )


repo = ROOT / "repo"
(repo / "docs" / "plans").mkdir(parents=True)
git(repo, "init", "-b", "main")
(repo / "docs" / "plans" / "2026-11-15-work.md").write_text(
    "---\nstatus: in-progress\ntask: work\n---\n\n# W\n\n## 단계\n- [x] 1. first\n- [ ] 2. second thing\n- [ ] 3. third\n",
    encoding="utf-8",
)
git(repo, "add", ".")
git(repo, "commit", "-m", "feat: start")
(repo / "dirty1.txt").write_text("x", encoding="utf-8")
(repo / "dirty2.txt").write_text("x", encoding="utf-8")
# The hooks key the store by the path git reports; on CI the temp folder's own spelling
# is a short (8.3) name, so the test asks git too instead of using its own path.
TOP = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"],
    cwd=repo,
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
STORED = STORE / f"{handoff.key(TOP)}.json"
MARKER = STORE / f"{handoff.key(TOP)}.open.json"

first = start(repo, "sess-A")
check(
    "H-T6 SessionStart leaves an open-session marker",
    json.loads(MARKER.read_text(encoding="utf-8")).get("session_id"),
    "sess-A",
)
check(
    "H-T7 nothing to restore at the very first start",
    "지난 세션 종료 기록" in first,
    False,
)
p = end(repo, "sess-A")
check("H-T4 SessionEnd: exit 0 and no stdout", (p.returncode, p.stdout), (0, ""))
saved = json.loads(STORED.read_text(encoding="utf-8"))
check(
    "H-T4 ... the record names branch, changed files, plan and next step",
    (
        saved.get("branch"),
        saved.get("dirty"),
        saved.get("plan"),
        saved.get("next_step"),
        saved.get("open_steps"),
        saved.get("reason"),
    ),
    (
        "main",
        2,
        "docs/plans/2026-11-15-work.md",
        "- [ ] 2. second thing",
        2,
        "prompt_input_exit",
    ),
)
check("H-T4 ... and only allow-listed keys", set(saved) <= set(handoff.FIELDS), True)
check("H-T6 SessionEnd removes its own marker", MARKER.exists(), False)

ctx = start(repo, "sess-B")
check(
    "H-T7 the next session is shown the record",
    all(
        part in ctx
        for part in (
            "## 지난 세션 종료 기록",
            "prompt_input_exit",
            "브랜치 `main`",
            "변경 파일 2개",
            "docs/plans/2026-11-15-work.md",
            "- [ ] 2. second thing",
        )
    ),
    True,
)
check(
    "H-T7 ... told that it restores no approval",
    "승인을 복원하지 않습니다" in ctx and "task:<id>" in ctx,
    True,
)
check("H-T8 same commit: no 'changed' remark", "달라졌습니다" in ctx, False)
check(
    "H-T10 the same session again (compact): no record, no stale-session remark",
    (
        "지난 세션 종료 기록" in start(repo, "sess-B", "compact"),
        "종료 기록 없이" in start(repo, "sess-B", "resume"),
    ),
    (False, False),
)

git(repo, "add", ".")
git(repo, "commit", "-m", "feat: more")
ctx = start(repo, "sess-C")
check(
    "H-T8 a commit made since: the record says so", "커밋이 달라졌습니다" in ctx, True
)
check(
    "H-T9 a marker another session left: reported as ended without a record or running",
    "종료 기록 없이 끝났거나 아직 실행 중" in ctx,
    True,
)

# ---- nothing written where nothing should be
plain = ROOT / "plain"
plain.mkdir()
before = sorted(x.name for x in STORE.iterdir())
p1 = end(plain, "sess-X")
p2 = run("handoff.py", ["end"], "not json")
p3 = run("handoff.py", [], "{}")
check(
    "H-T5 outside a repository / bad input / no mode: exit 0, nothing written",
    (
        [p1.returncode, p2.returncode, p3.returncode],
        sorted(x.name for x in STORE.iterdir()) == before,
    ),
    ([0, 0, 0], True),
)
blocked_home = ROOT / "afile"
blocked_home.write_text("x", encoding="utf-8")
env_backup = os.environ["CLAUDE_HANDOFF_HOME"]
os.environ["CLAUDE_HANDOFF_HOME"] = str(blocked_home)
p4 = end(repo, "sess-C")
ctx_blocked = start(repo, "sess-D")
os.environ["CLAUDE_HANDOFF_HOME"] = env_backup
check("H-T5 a store that cannot be written: SessionEnd still exits 0", p4.returncode, 0)
check(
    "H-T5 ... and SessionStart still injects its usual context",
    "## git:" in ctx_blocked,
    True,
)

# ---- old, damaged and oversized records
old = dict(saved, at=time.time() - 15 * 86400)
STORED.write_text(json.dumps(old), encoding="utf-8")
MARKER.write_text(
    json.dumps({"session_id": "ancient", "at": time.time() - 15 * 86400}),
    encoding="utf-8",
)
ctx = start(repo, "sess-E")
check(
    "H-T11 a fifteen-day-old record and marker are not shown",
    ("지난 세션 종료 기록" in ctx, "종료 기록 없이" in ctx),
    (False, False),
)
for label, content in (
    ("damaged", "{not json"),
    ("another version", json.dumps(dict(saved, v=99, at=time.time()))),
    ("not an object", "[1, 2]"),
):
    STORED.write_text(content, encoding="utf-8")
    ctx = start(repo, "sess-E")
    check(
        f"H-T12 a {label} record is ignored and the start is normal",
        ("지난 세션 종료 기록" in ctx, "## git:" in ctx),
        (False, True),
    )
huge = dict(
    saved,
    at=time.time(),
    branch="b" * 5000,
    plan="p" * 5000,
    next_step="n" * 5000,
    reason="r" * 5000,
)
STORED.write_text(json.dumps(huge), encoding="utf-8")
ctx = start(repo, "sess-F")
section = (
    ctx[ctx.index("## 지난 세션 종료 기록") :]
    if "## 지난 세션 종료 기록" in ctx
    else ""
)
check(
    "H-T13 oversized values: the section stays within 600, the context within its cap",
    (
        0 < len(section.split("\n## ")[0]) <= 600,
        len(ctx) <= LIMIT,
        "## git:" in ctx,
        "[session-context]" in ctx,
    ),
    (True, True, True, True),
)

check(
    "H-T13 ... and a shown record always says it restores no approval",
    "승인을 복원하지 않습니다" in section.split("\n")[0],
    True,
)
MARKER.write_text(
    json.dumps({"session_id": "other-" + "s" * 500, "at": time.time() - 60}),
    encoding="utf-8",
)
ctx = start(repo, "sess-G")
check(
    "H-T13 with a leftover marker too: both the remark and the full heading",
    ("종료 기록 없이" in ctx, "참고용이며 승인을 복원하지 않습니다" in ctx),
    (True, True),
)

# ---- review: files that must never break a session's start or end
for label, content in (
    ("an absurd timestamp", '{"v": 1, "at": 1' + "0" * 400 + ', "branch": "main"}'),
    ("absurdly nested JSON", "[" * 100000),
):
    STORED.write_text(content, encoding="utf-8")
    MARKER.write_text(content, encoding="utf-8")
    p_start = run(
        "session_context.py",
        [],
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-H",
            "source": "startup",
            "cwd": str(repo),
        },
    )
    check(
        f"H-T15 {label} in the store: SessionStart still exits 0 with its context",
        (p_start.returncode, "## git:" in p_start.stdout),
        (0, True),
    )
    MARKER.write_text(content, encoding="utf-8")
    p_end = end(repo, "sess-H")
    check(
        f"H-T15 {label} in the store: SessionEnd exits 0, nothing on stdout",
        (p_end.returncode, p_end.stdout),
        (0, ""),
    )

git(repo, "checkout", "--detach", "HEAD")
end(repo, "sess-I")
ctx = start(repo, "sess-J")
check(
    "H-T16 a detached HEAD at both ends is not reported as a changed branch",
    ("지난 세션 종료 기록" in ctx, "브랜치가 달라졌습니다" in ctx),
    (True, False),
)
check(
    "H-T16 hashes of different lengths for one commit are the same commit",
    (
        handoff.same_commit("0123456789ab", "0123456"),
        handoff.same_commit("0123456789ab", "0123457"),
        handoff.same_commit("01", "01"),
    ),
    (True, False, False),
)

# ---- settings.json
SETTINGS = json.loads((HERE.parent / "settings.json").read_text(encoding="utf-8"))[
    "hooks"
]
check(
    "H-T14 SessionEnd runs handoff.py end",
    [
        h["command"].split(".claude/hooks/")[-1]
        for g in SETTINGS.get("SessionEnd", [])
        for h in g["hooks"]
    ],
    ["handoff.py end"],
)
check(
    "H-T14 the other hook events are all still registered",
    sorted(SETTINGS),
    sorted(
        [
            "PreToolUse",
            "PostToolUse",
            "UserPromptSubmit",
            "Stop",
            "Notification",
            "PostToolUseFailure",
            "SubagentStart",
            "SubagentStop",
            "SessionStart",
            "SessionEnd",
        ]
    ),
)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
