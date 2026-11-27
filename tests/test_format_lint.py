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
# c80-followup step 9 of the analysis (plan step 8): 121 edit-time refusals in session
# c80ac8d8, 112 of them F821/F401/F841 between two edits of one change. Those three are
# judged at the end of the turn (the Stop, below); everything else right after the edit.
bad = write("bad.py", "def f():\n    return undefined_name\n")
r = edit(bad)
check(
    "F-T11 an undefined name right after the edit: not reported (judged at the Stop)",
    (r.returncode, "F821" in r.stderr),
    (0, False),
)
# a problem of another rule (in ruff's default set, not fixed by --fix) right after the edit
REPEATED_KEY = 'd = {"a": 1, "a": 2}\nprint(d)\n'
repeated = write("repeated.py", REPEATED_KEY)
r = edit(repeated)
check("py repeated dict key -> exit 2", r.returncode, 2)
check("py lint feedback names the rule", "F601" in r.stderr, True)

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
    "F-T3 an unused import stays in the file (formatted); it is judged at the Stop",
    (unused.read_text(encoding="utf-8"), r.returncode, "F401" in r.stderr),
    ("x = 1\nimport os\n", 0, False),
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
r = post(write("via_post_bad.py", REPEATED_KEY), session)
check(
    "F-T9 lint problems that remain come back as before: exit 2, the rule on stderr",
    (r.returncode, "F601" in r.stderr),
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


# ---- c80-followup step 8: F821/F401/F841 at the end of the turn
def hook(mode: str, session: str, **payload: object) -> str:
    """The discipline hook in `mode`; the Stop's block reason ("" when it lets it end)."""
    done = subprocess.run(
        [sys.executable, "-I", str(DISC), mode],
        input=json.dumps({"session_id": session, "cwd": str(ROOT), **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        check=False,
        timeout=180,
    )
    if mode != "stop" or not done.stdout.strip():
        return ""
    return str(json.loads(done.stdout).get("reason") or "")


def turn_with(name: str, text: str, base: Path | None = None) -> tuple[str, Path]:
    s = uuid.uuid4().hex
    hook("prompt", s, prompt="work", prompt_id="p1")
    f = write(name, text, base)
    post(f, s)
    return s, f


UNDEFINED = "def f():\n    return undefined_name\n"
s, f = turn_with("deferred/left.py", UNDEFINED)
reason = hook("stop", s, prompt_id="p1")
check(
    "F-T12 an undefined name still there at the Stop: one block naming the rule and file:line",
    ("F821" in reason, "left.py:2" in reason.replace("\\", "/")),
    (True, True),
)
check(
    "F-T12 ... the Stop that follows the block lets the turn end",
    "F821" in hook("stop", s, prompt_id="p1", stop_hook_active=True),
    False,
)
s, f = turn_with("deferred/fixed.py", UNDEFINED)
f.write_text("undefined_name = 1\n\n\ndef f():\n    return undefined_name\n", "utf-8")
check(
    "F-T13 fixed within the turn: no block for it",
    "F821" in hook("stop", s, prompt_id="p1"),
    False,
)
s, f = turn_with("deferred/gone.py", UNDEFINED)
f.unlink()
check(
    "F-T13 deleted within the turn: skipped",
    "F821" in hook("stop", s, prompt_id="p1"),
    False,
)
s, f = turn_with("away.py", UNDEFINED, ELSEWHERE)
check(
    "F-T13 a file outside the session's repository: skipped",
    "F821" in hook("stop", s, prompt_id="p1"),
    False,
)
# (review 1) the project's own ruff settings decide: a rule it switched off blocks nothing
write("quiet/pyproject.toml", '[tool.ruff.lint]\nignore = ["F841"]\n')
s, f = turn_with("quiet/unused_var.py", "def f():\n    y = 1\n")
check(
    "F-T14 a project that ignores F841: an unused variable does not block the Stop",
    "F841" in hook("stop", s, prompt_id="p1"),
    False,
)
s, f = turn_with("deferred/unused_var.py", "def f():\n    y = 1\n")
check(
    "F-T14 ... where it is not ignored, it does",
    "F841" in hook("stop", s, prompt_id="p1"),
    True,
)
# (review 3) ruff fails on the project's settings: no block, no crash, the skip is logged
write("broken/ruff.toml", 'required-version = ">=99"\n')
s, f = turn_with("broken/left.py", UNDEFINED)
done = subprocess.run(
    [sys.executable, "-I", str(DISC), "stop"],
    input=json.dumps({"session_id": s, "cwd": str(ROOT), "prompt_id": "p1"}),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=ENV,
    check=False,
    timeout=180,
)
check(
    "F-T15 ruff fails on a project's settings: exit 0, no lint block, the skip logged",
    (
        done.returncode,
        "F821" in done.stdout,
        "deferred_lint_skipped" in done.stderr,
        "Traceback" in done.stderr,
    ),
    (0, False, True, False),
)
# (final audit 1) a project's `fix = true` must not make the Stop rewrite the file: the
# evidence of the turn is recorded before this check
write("fixing/pyproject.toml", "[tool.ruff]\nfix = true\n")
FIXABLE = "import os\n\nx = undefined_name\n"
s, f = turn_with("fixing/both.py", FIXABLE)
reason = hook("stop", s, prompt_id="p1")
check(
    "F-T16 a project with `fix = true`: the file stays as it was, and both problems are named",
    (f.read_text(encoding="utf-8"), "F401" in reason, "F821" in reason),
    (FIXABLE, True, True),
)
# (review after the c80-followup release) `--no-fix` does not undo a project's
# `fix-only = true`: ruff then fixed the file at the Stop and reported nothing
# ruff takes `fix` and `fix-only` from the settings of the folder it runs in, and the Stop
# runs it in the repository's root: F-T16's settings sit in a subfolder and never applied,
# so each case here is a repository with the setting in its root.
for setting in ("fix", "fix-only"):
    repo = BASE / f"root-{setting}"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    write("pyproject.toml", f"[tool.ruff]\n{setting} = true\n", repo)
    both = write("both.py", FIXABLE, repo)
    lines = "\n".join(fl.deferred_problems([str(both)], str(repo)))
    check(
        f"F-T19 a repository whose root says `{setting} = true`: the Stop's check leaves "
        "the file as it was and names both problems",
        (both.read_text(encoding="utf-8"), "F401" in lines, "F821" in lines),
        (FIXABLE, True, True),
    )
write("fixonly/pyproject.toml", "[tool.ruff]\nfix-only = true\n")
r = edit(write("fixonly/repeated.py", REPEATED_KEY))
check(
    "F-T20 a project with `fix-only = true`: a problem right after the edit is still "
    "reported",
    (r.returncode, "F601" in r.stderr),
    (2, True),
)
kept = write("fixing/unused.py", "x = 1\nimport os\n")
r = edit(kept)
check(
    "F-T21 a project with `fix = true`: the unused import is not removed right after "
    "the edit either",
    (kept.read_text(encoding="utf-8"), r.returncode),
    ("x = 1\nimport os\n", 0),
)
# (final review 1) the check never takes more than the hook has left
lone = write("deferred/budget.py", UNDEFINED)
check(
    "F-T17 no time left: ruff is not run, and the skip says why",
    (
        fl.deferred_problems([str(lone)], str(ROOT), budget_s=0.5),
        "time" in fl.deferred_skipped,
    ),
    ([], True),
)
# (final audit 2) a ruff that is a .cmd shim is not run with a path cmd.exe would read
SHIM = BASE / "shimbin"
write("../shimbin/ruff.cmd", f'@echo off\r\necho ran> "{BASE / "shim-ran.txt"}"\r\n')
meta_file = write("deferred/a&b.py", UNDEFINED)
saved_path = os.environ.get("PATH", "")
os.environ["PATH"] = str(SHIM)
try:
    shim_result = fl.deferred_problems([str(meta_file)], str(ROOT))
finally:
    os.environ["PATH"] = saved_path
check(
    "F-T18 a ruff.cmd shim and a path with a cmd.exe metacharacter: not run, skip recorded",
    (shim_result, (BASE / "shim-ran.txt").exists(), "cmd" in fl.deferred_skipped),
    ([], False, True),
)
s, f = turn_with("deferred/imports.py", "import os\n\nx = 1\n")
check(
    "F-T12 an unused import at the Stop: named too",
    "F401" in hook("stop", s, prompt_id="p1"),
    True,
)

shutil.rmtree(BASE, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
