"""P0-05: what an approved plan's scope covers (run with python -I).

Path classes (plan / code / data / external), the docs/plans exception limited to the
approving repository, data paths that need their own block, and label lines in the scope
list. Mostly direct calls; two cases run the hook itself. Everything lives in throw-away
folders outside %TEMP% and in an isolated approval home.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
HOME, WITNESS = HERE / "_tmp_scope_home", HERE / "_tmp_scope_w"
# No folder above ROOT holds a .git entry, so ROOT/data is certainly outside any repository.
ROOT = Path("C:/dev/_scope_t")
A, B, DATA = ROOT / "a", ROOT / "b", ROOT / "data"


def rm_tree(path: Path) -> None:
    """Remove a tree that may hold a junction (unlink it first: never follow it)."""
    for link in (path / "a" / "docs" / "plans" / "link", path / "data" / "link"):
        if os.path.lexists(link):
            os.rmdir(link)
    shutil.rmtree(path, ignore_errors=True)


for _p in (HOME, WITNESS):
    shutil.rmtree(_p, ignore_errors=True)
rm_tree(ROOT)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(WITNESS / "hwm")
sys.stdout.reconfigure(errors="backslashreplace")
sys.path.insert(0, str(HOOKS))
import approvals as ap

import discipline as d

fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:100],
        "" if ok else f"(want {want!r})",
    )


for repo in (A, B):
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "src").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
(DATA / "raw").mkdir(parents=True)
LINK = A / "docs" / "plans" / "link"
subprocess.run(
    ["cmd", "/c", "mklink", "/J", str(LINK), str(B / "docs" / "plans")],
    check=True,
    capture_output=True,
)

BASE = os.path.normcase(str(A))
SRC = d._norm(str(A / "src/**"))
WIDE = d._norm(str(ROOT / "**"))
DATA_FILE = str(DATA / "raw" / "x.jsonl")


def approval(scope: list[str], base: str | None = BASE) -> dict:
    rec = {"v": 2, "approval_id": "t", "scope": scope, "rearmed_from": "x"}
    if base is not None:
        rec["base"] = base
    return rec


def covered(scope: list[str], path: object, base: str | None = BASE) -> bool:
    return d.in_approved_scope(approval(scope, base), str(path))


# ---- the docs/plans exception belongs to the approving repository only
check(
    "P-T1 the approving repo's plan file", covered([SRC], A / "docs/plans/x.md"), True
)
check(
    "P-T2 another repo's plan file (N1)", covered([SRC], B / "docs/plans/x.md"), False
)
check(
    "P-T3 a link in the plans folder that leads into another repo",
    covered([SRC], LINK / "x.md"),
    False,
)
check(
    "P-T4 the same plan file in another letter case",
    covered([SRC], str(A).upper() + "\\DOCS\\PLANS\\x.md"),
    True,
)
check(
    "P-T4 dot-dot back into the approving repo's plans",
    covered([SRC], str(A / "src" / ".." / "docs" / "plans" / "x.md")),
    True,
)
check(
    "P-T4 dot-dot out into another repo's plans",
    covered(
        [SRC], str(A / "docs" / "plans" / ".." / ".." / ".." / "b/docs/plans/x.md")
    ),
    False,
)
check(
    "P-T11 a record without a base gets no plans exception",
    covered([SRC], A / "docs/plans/x.md", base=None),
    False,
)

# ---- code and other repositories: the scope patterns decide, as before
check("P-T5 a file inside the scope", covered([SRC], A / "src/w.py"), True)
check("P-T5 a file outside the scope", covered([SRC], A / "other.py"), False)
check(
    "P-T9 another repo's code under a wide pattern (unchanged)",
    covered([WIDE], B / "src/y.py"),
    True,
)

# ---- data: outside every repository, needs its own block
check("P-T6 a wide code pattern does not cover data", covered([WIDE], DATA_FILE), False)
DATA_GLOB = "data:" + d._norm(str(DATA / "**"))
check("P-T7 the data block covers it", covered([WIDE, DATA_GLOB], DATA_FILE), True)
check(
    "P-T7 ... and only what its pattern names",
    covered([SRC, DATA_GLOB], str(ROOT / "elsewhere" / "y.bin")),
    False,
)
check(
    "P-T8 a data pattern never covers code",
    covered(["data:" + WIDE], A / "src/w.py"),
    False,
)
check(
    "P-T8 ... nor another repo's plan file",
    covered(["data:" + WIDE], B / "docs/plans/x.md"),
    False,
)

# ---- plan_scope: the data block and label lines
PLAN = (
    "---\nstatus: in-progress\ntask: scopet\n---\n\n# T\n\n"
    "## 변경 범위\n"
    "- src/**\n"
    "- `tests/a.py` (설명: 등록)\n"
    "- C:/x/**\n"
    "- 범위 밖: 다른 파일\n"
    "- 하지 않는 일: hook 수정\n"
    "- Makefile (note: x)\n"
    "- Dockerfile - note: x\n\n"
    "## 데이터 변경 범위\n"
    f"- {DATA.as_posix()}/**\n"
    "- 주의 사항: 백업 먼저\n\n"
    "## 단계\n- [ ] 1. a\n"
)
check(
    "P-T10 scope items: paths kept, label lines dropped, data block prefixed",
    d.plan_scope(PLAN, str(A)),
    [
        d._norm(str(A / "src/**")),
        d._norm(str(A / "tests/a.py")),
        d._norm("C:/x/**"),
        d._norm(str(A / "Makefile")),
        d._norm(str(A / "Dockerfile")),
        DATA_GLOB,
    ],
)
check(
    "P-T10 a plan without a data block has no data entries",
    [
        g
        for g in d.plan_scope(PLAN.split("## 데이터")[0], str(A))
        if g.startswith("data:")
    ],
    [],
)

# ---- path_class: the pure classification
ROOT_N = d._norm(str(A))
for name, path, in_repo, want in (
    ("a plan file", A / "docs/plans/x.md", True, "plan"),
    ("a nested plan file", A / "docs/plans/sub/x.md", True, "plan"),
    ("the plans folder itself", A / "docs/plans", True, "code"),
    ("a code file", A / "src/w.py", True, "code"),
    ("the base itself", A, True, "code"),
    (
        "a sibling that only shares the prefix",
        str(A) + "x/docs/plans/x.md",
        True,
        "external",
    ),
    ("another repo", B / "src/y.py", True, "external"),
    ("outside every repo", DATA / "raw/x.jsonl", False, "data"),
):
    check(f"P-T12 {name}", d.path_class(str(path), ROOT_N, in_repo), want)


for name, path, want in (
    ("a file in a base that is no repository", DATA / "raw/x.jsonl", "data"),
    ("the plans folder of such a base", DATA / "docs/plans/x.md", "plan"),
):
    check(f"P-T12 {name}", d.path_class(str(path), d._norm(str(DATA)), False), want)

# ---- review: a base that is no repository, links between data folders, UNC, headings
NOGIT = os.path.normcase(str(DATA))
check(
    "P-T15 a session approved in a folder that is no repository: code patterns cover nothing",
    covered([d._norm(str(DATA / "**"))], DATA_FILE, base=NOGIT),
    False,
)
check(
    "P-T15 ... its data block does",
    covered([d._norm(str(DATA / "**")), DATA_GLOB], DATA_FILE, base=NOGIT),
    True,
)
OTHER_DATA = ROOT / "data2"
(OTHER_DATA / "raw").mkdir(parents=True)
DATA_LINK = DATA / "link"
subprocess.run(
    ["cmd", "/c", "mklink", "/J", str(DATA_LINK), str(OTHER_DATA)],
    check=True,
    capture_output=True,
)
check(
    "P-T16 a link inside the approved data folder that leads to another data folder",
    covered([SRC, DATA_GLOB], DATA_LINK / "raw" / "y.bin"),
    False,
)
UNC_FILE = "\\\\nohost.invalid\\share\\raw\\x.jsonl"
check(
    "P-T17 a UNC path is not covered by a code pattern",
    covered(["//nohost.invalid/share/**", d._norm("//**"), "/**"], UNC_FILE),
    False,
)
check(
    "P-T17 ... only by the data block",
    covered([SRC, "data://nohost.invalid/share/**"], UNC_FILE),
    True,
)
check(
    "P-T17 the long-path spelling of the approving repo's plan file",
    covered([SRC], "\\\\?\\" + str(A / "docs" / "plans" / "x.md")),
    True,
)
check(
    "P-T17 the long-path spelling of a data file is still data",
    covered([WIDE], "\\\\?\\" + DATA_FILE),
    False,
)
check(
    "P-T18 a scope entry that is not text is ignored",
    (covered([7, SRC], A / "src/w.py"), covered([7], A / "src/w.py")),
    (True, False),
)
ENGLISH = "# T\n\n## Data Scope\n- " + DATA.as_posix() + "/**\n\n## Scope\n- src/**\n"
check(
    "P-T19 English headings, data block first: each item lands on its own side",
    d.plan_scope(ENGLISH, str(A)),
    [SRC, DATA_GLOB],
)


# ---- the hook itself: a v2 approval in A, then B's plan file
def call(mode: str, session: str, **payload: object) -> dict | None:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def state_of(session: str) -> dict:
    return json.loads(
        (
            Path(tempfile.gettempdir()) / "claude-discipline" / f"{session}.json"
        ).read_text(encoding="utf-8")
    )


ap.Store(HOME, WITNESS / "hwm").admin_activate(lambda code: True, "test")
E2E_PLAN = (
    "---\nstatus: in-progress\ntask: scopet\n---\n\n# T\n\n"
    "## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n"
)
PLAN_FILE = A / "docs" / "plans" / "2026-11-14-scopet.md"
PLAN_FILE.write_bytes(E2E_PLAN.encode("utf-8"))
FOREIGN_PLAN = B / "docs" / "plans" / "x.md"
s, p = uuid.uuid4().hex, "sc1"
call("prompt", s, prompt_id=p, prompt="plan it", cwd=str(A))
call(
    "post",
    s,
    prompt_id=p,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={"plan": E2E_PLAN, "filePath": str(PLAN_FILE)},
    cwd=str(A),
)
check(
    "P-T13 a v2 approval is issued for repo a",
    (state_of(s).get("approval") or {}).get("task_id"),
    "scopet",
)
for i in range(5):
    call(
        "post",
        s,
        prompt_id=p,
        tool_name="Edit",
        tool_input={"file_path": str(A / f"o{i}.txt")},
        cwd=str(A),
    )


def pre_decision(path: Path) -> str:
    out = call(
        "pre",
        s,
        prompt_id=p,
        tool_name="Edit",
        tool_input={"file_path": str(path), "old_string": "a", "new_string": "b"},
        cwd=str(A),
    )
    return (out or {}).get("hookSpecificOutput", {}).get(
        "permissionDecision"
    ) or "allow"


check(
    "P-T13 the approving repo's plan file is still allowed",
    pre_decision(PLAN_FILE),
    "allow",
)
check(
    "P-T13 an in-scope file is still allowed", pre_decision(A / "src" / "w.py"), "allow"
)
check(
    "P-T13 another repo's plan file as the sixth out-of-scope file: denied",
    pre_decision(FOREIGN_PLAN),
    "deny",
)

s, p = uuid.uuid4().hex, "sc2"
call("prompt", s, prompt_id=p, prompt="plan it", cwd=str(A))
call(
    "post",
    s,
    prompt_id=p,
    tool_name="ExitPlanMode",
    tool_input={},
    tool_response={"plan": E2E_PLAN, "filePath": str(PLAN_FILE)},
    cwd=str(A),
)
pre_decision(FOREIGN_PLAN)  # first of this turn: the hook sees repo b before the write
FOREIGN_PLAN.write_text("# changed by the test\n", encoding="utf-8")
call(
    "post",
    s,
    prompt_id=p,
    tool_name="Write",
    tool_input={"file_path": str(FOREIGN_PLAN)},
    cwd=str(A),
)
stop = str((call("stop", s, prompt_id=p, cwd=str(A)) or {}).get("reason"))
check(
    "P-T14 Stop reports the other repo's plan file as outside the approved scope",
    ("변경 범위 밖" in stop, FOREIGN_PLAN.name in stop),
    (True, True),
)

rm_tree(ROOT)
for _p in (HOME, WITNESS):
    shutil.rmtree(_p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
