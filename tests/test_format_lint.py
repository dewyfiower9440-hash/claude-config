"""Scenario tests for hooks/format_lint.py (run with python -I). Needs ruff and PSScriptAnalyzer."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "format_lint.py"
BASE = Path(tempfile.mkdtemp(prefix="fmtlint-"))
ROOT = BASE / "repo"  # the session's repository
ELSEWHERE = BASE / "elsewhere"  # a folder that is not part of it
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


def run(payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,  # exit 2 = lint problems fed back to Claude, asserted below
        timeout=120,
    )


def edit(path: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run(
        json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(path)},
                "cwd": str(cwd or ROOT),
            }
        )
    )


def write(name: str, text: str, base: Path | None = None) -> Path:
    p = (base or ROOT) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


ROOT.mkdir()
ELSEWHERE.mkdir()
subprocess.run(["git", "init", "-q"], cwd=ROOT, check=True)

# ---- python
bad = write("bad.py", "def f():\n    return undefined_name\n")
r = edit(bad)
check("py undefined name -> exit 2", r.returncode, 2)
check("py lint feedback names the rule", "F821" in r.stderr, True)

ugly = write("ugly.py", "x=1\ny  =  2\n")
r = edit(ugly)
check("py fixable only -> exit 0", r.returncode, 0)
check("py formatted", ugly.read_text(encoding="utf-8"), "x = 1\ny = 2\n")
check(
    "F-T6 a file that was rewritten is said so in one line on stdout",
    ("ugly.py" in r.stdout, len(r.stdout.strip().splitlines())),
    (True, 1),
)
check("F-T6 ... and a file left as it was says nothing", edit(ugly).stdout.strip(), "")

# devenv-final 25 (changed expectation: the import used to be removed, exit 0). An unused
# import is not deleted behind the session's back: the next edit usually uses it. It is
# reported instead.
unused = write("unused.py", "x=1\nimport os\n")
r = edit(unused)
check(
    "F-T3 an unused import stays in the file (formatted) and is reported",
    (unused.read_text(encoding="utf-8"), r.returncode, "F401" in r.stderr),
    ("x = 1\nimport os\n", 2, True),
)

# ---- inputs the hook must ignore
check("unsupported extension -> 0", edit(write("notes.txt", "x=1\n")).returncode, 0)
check("missing file -> 0", edit(ROOT / "nope.py").returncode, 0)
check(
    "no file_path -> 0",
    run(json.dumps({"tool_name": "Edit", "tool_input": {}})).returncode,
    0,
)
check("malformed payload -> 0", run("not json").returncode, 0)

# ---- devenv-final 25: only the session's repository, never its plans
outside = write("other.py", "x=1\n", ELSEWHERE)
r = edit(outside)
check(
    "F-T1 a file outside the session's repository is left as it is",
    (outside.read_text(encoding="utf-8"), r.returncode, r.stdout.strip()),
    ("x=1\n", 0, ""),
)
r = edit(ugly := write("again.py", "x=1\n"), cwd=ELSEWHERE)
check(
    "F-T1 ... and so is any file when the session is in no repository",
    (ugly.read_text(encoding="utf-8"), r.returncode),
    ("x=1\n", 0),
)
plan_code = write("docs/plans/snippet.py", "x=1\n")
plan_text = write(
    "docs/plans/2026-11-18-x.md", "---\nstatus: draft\n---\n# P\n\n\n\ntext\n"
)
check(
    "F-T2 nothing under docs/plans is rewritten (an approved plan stays the approved text)",
    (
        edit(plan_code).returncode,
        plan_code.read_text(encoding="utf-8"),
        edit(plan_text).returncode,
        plan_text.read_text(encoding="utf-8"),
    ),
    (0, "x=1\n", 0, "---\nstatus: draft\n---\n# P\n\n\n\ntext\n"),
)
# A program planted in the session's folder is not the formatter.
MARK = BASE / "planted-ran.txt"
write("ruff.cmd", f'@echo off\r\necho ran> "{MARK}"\r\nexit /b 0\r\n')
write("ruff.bat", f'@echo off\r\necho ran> "{MARK}"\r\nexit /b 0\r\n')
planted_target = write("pkg/mod.py", "x=1\n")
write("pkg/ruff.cmd", f'@echo off\r\necho ran> "{MARK}"\r\nexit /b 0\r\n')
r = edit(planted_target)
check(
    "F-T5 a ruff.cmd in the session's folder or next to the file is not run; the real "
    "one from PATH formats the file",
    (MARK.exists(), planted_target.read_text(encoding="utf-8")),
    (False, "x = 1\n"),
)

# ---- powershell
warn = write("Warn.ps1", "function Get-Thing { gci }\n")
r = edit(warn)
check("ps1 analyzer warning -> exit 2", r.returncode, 2)
check("ps1 feedback names the rule", "PSAvoidUsingCmdletAliases" in r.stderr, True)
clean = write(
    "Clean.ps1",
    "function Get-Thing {\n    [CmdletBinding()]\n    param()\n    Get-ChildItem\n}\n",
)
check("clean ps1 -> 0", edit(clean).returncode, 0)
odd = write("we[i]rd name.ps1", "function Get-Thing { gci }\n")
r = edit(odd)
check(
    "F-T4 a file whose name holds wildcard characters is analysed as that file",
    (r.returncode, "PSAvoidUsingCmdletAliases" in r.stderr),
    (2, True),
)
# ---- a .cmd shim is run through cmd.exe: not with a path it would read as commands
sys.path.insert(0, str(HOOK.parent))
import format_lint as fl

web = ROOT / "web"
(web / "node_modules" / ".bin").mkdir(parents=True)
write("web/package.json", "{}\n")
RAN = BASE / "prettier-ran.txt"
write("web/node_modules/.bin/prettier.cmd", f'@echo off\r\necho ran> "{RAN}"\r\n')
plain = write("web/ok.ts", "const a=1\n")
fl.ts_file(plain, ROOT)
ran_plain = RAN.exists()
RAN.unlink(missing_ok=True)
meta = write("web/a&b.ts", "const a=1\n")
fl.ts_file(meta, ROOT)
check(
    "F-T7 the project's own shim runs for a plain path, not for one with a cmd.exe "
    "metacharacter",
    (ran_plain, RAN.exists()),
    (True, False),
)
check(
    "F-T8 the run's tools share one time budget; a spent one runs nothing",
    (
        fl.RUN_BUDGET_S <= 60,
        setattr(fl, "_deadline", 0.0),
        fl.run(["cmd", "/c", "echo"], ROOT),
    ),
    (True, None, None),
)

# ---- the formatter runs inside the discipline post hook, before that hook reads the file
import os
import uuid

DISC = HOOK.parent / "discipline.py"
TMPD = BASE / "tmp"
TMPD.mkdir()
ENV = dict(
    os.environ,
    TEMP=str(TMPD),
    TMP=str(TMPD),
    TMPDIR=str(TMPD),
    CLAUDE_DISCIPLINE_HOME=str(BASE / "home"),
    CLAUDE_DISCIPLINE_WITNESS=str(BASE / "w" / "hwm"),
)


def post(path: Path, session: str) -> subprocess.CompletedProcess[str]:
    payload = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path), "old_string": "a", "new_string": "b"},
        "tool_use_id": uuid.uuid4().hex,
        "cwd": str(ROOT),
    }
    return subprocess.run(
        [sys.executable, "-I", str(DISC), "post"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        check=False,
        timeout=180,
    )


session = uuid.uuid4().hex
via_post = write("via_post.py", "x=1\ny  =  2\n")
r = post(via_post, session)
state = json.loads((TMPD / "claude-discipline" / f"{session}.json").read_text("utf-8"))
check(
    "F-T9 the post hook formats the edited file itself, says so, and exits 0",
    (via_post.read_text(encoding="utf-8"), "via_post.py" in r.stdout, r.returncode),
    ("x = 1\ny = 2\n", True, 0),
)
check(
    "F-T9 ... what it prints on stdout is one JSON hook output (a plain line would break "
    "every reader of the post hook's output)",
    json.loads(r.stdout).get("hookSpecificOutput", {}).get("hookEventName"),
    "PostToolUse",
)
check(
    "F-T9 ... and still records the edit (the formatter did not replace the hook's work)",
    any("via_post.py" in str(f) for f in state.get("turn_files", [])),
    True,
)
r = post(write("via_post_bad.py", "def f():\n    return undefined_name\n"), session)
check(
    "F-T9 lint problems that remain come back as before: exit 2, the rule on stderr",
    (r.returncode, "F821" in r.stderr),
    (2, True),
)
check(
    "F-T9 a file the formatter does not touch (outside the repository): exit 0, recorded",
    post(outside, session).returncode,
    0,
)

# devenv-final 42 (found by the end-to-end trial): a test written before its module exists
# imports a module ruff cannot find yet, and ruff sorts such an import as third-party.
# The hook moved it, and the project's own gate then failed (I001) once the module was
# there. Import order is judged by the gate, where the tree is complete: the hook neither
# rewrites it nor reports it.
PROJ = ROOT / "proj"
write(
    "proj/pyproject.toml",
    '[tool.ruff]\nsrc = ["src"]\n[tool.ruff.lint]\nselect = ["E", "F", "I"]\n',
)
write("proj/src/pkg/__init__.py", "")
write("proj/src/pkg/errors.py", "class E(Exception):\n    pass\n")
FIRST = "import os\n\nfrom pkg.errors import E\nfrom pkg.notyet import f\n\nprint(os, E, f)\n"
early = write("proj/tests/test_new.py", FIRST)
r = edit(early, cwd=PROJ)
check(
    "F-T10 an import of a module that does not exist yet stays where it was written, and "
    "nothing is reported about import order",
    (early.read_text(encoding="utf-8"), r.returncode, "I001" in r.stderr),
    (FIRST, 0, False),
)
late = write(
    "proj/tests/test_mixed.py", "import sys\nimport os\nx=1\nprint(os, sys, x)\n"
)
r = edit(late, cwd=PROJ)
check(
    "F-T10 ... imports that are out of order are left for the gate too (the rest of the "
    "file is still formatted)",
    (late.read_text(encoding="utf-8"), r.returncode),
    ("import sys\nimport os\n\nx = 1\nprint(os, sys, x)\n", 0),
)

shutil.rmtree(BASE, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
