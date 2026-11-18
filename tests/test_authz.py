"""P0-06: no edit without an approved plan (run with python -I).

Two modes: `enforce` (an edit outside an approved scope is denied from the first file) and
the default `warn` (the five-file rule as before, plus one observation line for every edit
that enforce would have denied). Isolated approval home, throw-away repository.
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

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
HOME, WITNESS = TMP_ROOT / "_tmp_authz_home", TMP_ROOT / "_tmp_authz_w"
REPO = TMP_ROOT / "_tmp_authz_repo"
for _p in (HOME, WITNESS, REPO):
    shutil.rmtree(_p, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(WITNESS / "hwm")
os.environ.pop("CLAUDE_AUTHZ_MODE", None)
sys.stdout.reconfigure(errors="backslashreplace")
sys.path.insert(0, str(HOOKS))
import approvals as ap

# Kept apart from the import above (isort classifies `discipline` differently on CI).
fails = 0
import discipline as d

OBSERVE = HOME / "authz-observe.jsonl"


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


# ---- the pure verdict
for name, args, want in (
    ("inside the approved scope, warn", (True, False, 9, 5, "warn"), "allow"),
    ("inside the approved scope, enforce", (True, False, 9, 5, "enforce"), "allow"),
    ("warn, first file outside", (False, False, 0, 5, "warn"), "would_deny"),
    ("warn, fifth file outside", (False, False, 4, 5, "warn"), "would_deny"),
    ("warn, sixth file outside", (False, False, 5, 5, "warn"), "deny"),
    ("warn, a counted file again", (False, True, 5, 5, "warn"), "would_deny"),
    ("enforce, first file outside", (False, False, 0, 5, "enforce"), "deny"),
    ("enforce, a counted file again", (False, True, 1, 5, "enforce"), "deny"),
):
    check(f"Z-T1 {name}", d.authz_verdict(*args), want)
for raw, want in (
    (None, "warn"),
    ("", "warn"),
    ("warn", "warn"),
    ("off", "warn"),
    ("ENFORCE ", "enforce"),
    ("enforce", "enforce"),
    ("enforced", "warn"),
):
    check(f"Z-T2 mode value {raw!r}", d.authz_mode(raw), want)

# ---- the hook itself
(REPO / "docs" / "plans").mkdir(parents=True)
(REPO / "src").mkdir()
subprocess.run(["git", "init", "-q"], cwd=REPO, check=True)
ap.Store(HOME, WITNESS / "hwm").admin_activate(lambda code: True, "test")
PLAN = (
    "---\nstatus: in-progress\ntask: authzt\n---\n\n# T\n\n"
    "## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n"
)
PLAN_FILE = REPO / "docs" / "plans" / "2026-11-15-authzt.md"
PLAN_FILE.write_bytes(PLAN.encode("utf-8"))
_n = 0


def call(mode: str, session: str, authz: str | None, **payload: object) -> dict | None:
    env = dict(os.environ)
    if authz is not None:
        env["CLAUDE_AUTHZ_MODE"] = authz
    payload.setdefault("cwd", str(REPO))
    p = inproc.run(
        HOOKS / "discipline.py",
        [mode],
        json.dumps({"session_id": session, **payload}),
        env=env,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def session(
    authz: str | None, prompt: str = "do it", approve: bool = False
) -> tuple[str, str]:
    global _n
    _n += 1
    s, p = uuid.uuid4().hex, f"z{_n}"
    call("prompt", s, authz, prompt_id=p, prompt=prompt)
    if approve:
        call(
            "post",
            s,
            authz,
            prompt_id=p,
            tool_name="ExitPlanMode",
            tool_input={},
            tool_response={"plan": PLAN, "filePath": str(PLAN_FILE)},
        )
    return s, p


def pre(s: str, p: str, authz: str | None, path: object, tool: str = "Edit") -> dict:
    out = call(
        "pre",
        s,
        authz,
        prompt_id=p,
        tool_name=tool,
        tool_input={"file_path": str(path), "old_string": "a", "new_string": "b"},
    )
    return (out or {}).get("hookSpecificOutput") or {}


def decision(s: str, p: str, authz: str | None, path: object) -> str:
    return pre(s, p, authz, path).get("permissionDecision") or "allow"


def edited(s: str, p: str, authz: str | None, path: object) -> None:
    call(
        "post",
        s,
        authz,
        prompt_id=p,
        tool_name="Edit",
        tool_input={"file_path": str(path)},
    )


def observed() -> list[dict]:
    if not OBSERVE.is_file():
        return []
    return [
        json.loads(ln)
        for ln in OBSERVE.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


E = "enforce"
s, p = session(E)
first = pre(s, p, E, REPO / "src" / "w.py")
check(
    "Z-T3 enforce, no approval: the first edit is denied and says how to get approval",
    (
        first.get("permissionDecision"),
        "ExitPlanMode" in str(first.get("permissionDecisionReason")),
    ),
    ("deny", True),
)
check(
    "Z-T3 ... a Write as well",
    pre(s, p, E, REPO / "n.py", "Write").get("permissionDecision"),
    "deny",
)
check("Z-T3 enforce writes no observation line", observed(), [])

s, p = session(E, approve=True)
check(
    "Z-T4 an approval was issued",
    (
        d_state := json.loads(
            (Path(tempfile.gettempdir()) / "claude-discipline" / f"{s}.json").read_text(
                encoding="utf-8"
            )
        )
    )
    .get("approval", {})
    .get("task_id"),
    "authzt",
)
z4 = []
for i in range(8):
    f = REPO / "src" / f"f{i % 4}.py"
    z4.append(decision(s, p, E, f))
    edited(s, p, E, f)
check("Z-T4 enforce, approved: eight in-scope edits all allowed", z4, ["allow"] * 8)
check(
    "Z-T5 enforce, approved: the first out-of-scope file is denied",
    decision(s, p, E, REPO / "other.py"),
    "deny",
)
check(
    "Z-T6 enforce, approved: the plan document is allowed",
    decision(s, p, E, PLAN_FILE),
    "allow",
)
OWN_PLAN = Path.home() / ".claude" / "plans" / "authz-test-never-written.md"
SCRATCH = Path(tempfile.gettempdir()) / "authz-test-never-written" / "x.py"
s0, p0 = session(E)
check(
    "Z-T8 enforce: Claude's own plan file and a %TEMP% scratch file are not judged",
    (decision(s0, p0, E, OWN_PLAN), decision(s0, p0, E, SCRATCH)),
    ("allow", "allow"),
)

s7, p7 = session(E, prompt="task:someothertask 계속")
check(
    "Z-T7 enforce: naming another task grants nothing",
    decision(s7, p7, E, REPO / "src" / "w.py"),
    "deny",
)
PLAN_FILE.write_bytes(PLAN.replace("1. a", "1. a, and more").encode("utf-8"))
check(
    "Z-T7 enforce: the plan body changed after approval",
    decision(s, p, E, REPO / "src" / "f0.py"),
    "deny",
)
PLAN_FILE.write_bytes(PLAN.encode("utf-8"))
s, p = session(E, approve=True)
check(
    "Z-T7 a fresh approval works again",
    decision(s, p, E, REPO / "src" / "f0.py"),
    "allow",
)
session(E, prompt="revoke task:authzt")
check(
    "Z-T7 enforce: revoked from another session",
    decision(s, p, E, REPO / "src" / "f0.py"),
    "deny",
)

s, p = session(E)
(REPO / "src" / "stop.py").write_text("x = 1\n", encoding="utf-8")
edited(s, p, E, REPO / "src" / "stop.py")
z14 = str((call("stop", s, E, prompt_id=p) or {}).get("reason"))
check(
    "Z-T14 enforce: Stop reports one file changed without an approved plan",
    "승인된 계획 없이" in z14,
    True,
)
(REPO / "src" / "stop.py").unlink()

# ---- warn (the default): behaviour unchanged, observation lines added
W = None  # no CLAUDE_AUTHZ_MODE at all
s, p = session(W)
z9 = []
for i in range(5):
    f = REPO / f"w{i}.py"
    z9.append(decision(s, p, W, f))
    edited(s, p, W, f)
check("Z-T9 warn, no approval: five files allowed", z9, ["allow"] * 5)
rows = observed()
check(
    "Z-T9 ... and five observation lines, reason no_approval",
    [r.get("reason") for r in rows],
    ["no_approval"] * 5,
)
check(
    "Z-T10 warn: the sixth file is denied as before",
    decision(s, p, W, REPO / "w5.py"),
    "deny",
)
check("Z-T10 ... and adds no observation line", len(observed()), 5)
check(
    "Z-T12 an observation line holds only its named fields",
    (sorted(rows[0]), rows[0].get("class"), rows[0].get("ext"), rows[0].get("tool")),
    (["at", "class", "ext", "reason", "tool"], "code", ".py", "Edit"),
)
check(
    "Z-T12 ... and no path or file name",
    any("w0" in json.dumps(r) or "_tmp_authz" in json.dumps(r) for r in rows),
    False,
)

s, p = session(W, approve=True)
check(
    "Z-T11 warn, approved: an in-scope edit adds no line",
    (decision(s, p, W, REPO / "src" / "a.py"), len(observed())),
    ("allow", 5),
)
check(
    "Z-T11 warn, approved: an out-of-scope edit is allowed",
    decision(s, p, W, REPO / "README.md"),
    "allow",
)
check(
    "Z-T11 ... and observed as out_of_scope",
    [(r.get("reason"), r.get("ext")) for r in observed()[5:]],
    [("out_of_scope", ".md")],
)

s, p = session(W, approve=True)
for i in range(5):
    edited(s, p, W, REPO / f"x{i}.txt")
z16 = pre(s, p, W, REPO / "x5.txt")
check(
    "Z-T16 warn, approved: the sixth out-of-scope file is denied with the old wording",
    (
        z16.get("permissionDecision"),
        "변경 범위 밖 파일" in str(z16.get("permissionDecisionReason")),
        "한도 5" in str(z16.get("permissionDecisionReason")),
    ),
    ("deny", True, True),
)
s, p = session(W)
for name in ("s1.py", "s2.py"):
    (REPO / name).write_text("x = 1\n", encoding="utf-8")
    edited(s, p, W, REPO / name)
z17 = str((call("stop", s, W, prompt_id=p) or {}).get("reason"))
check(
    "Z-T17 warn: Stop does not report two files changed without a plan",
    "승인된 계획 없이" in z17,
    False,
)
for name in ("s1.py", "s2.py"):
    (REPO / name).unlink()


def notebook(s: str, p: str, authz: str | None, path: object) -> str:
    out = call(
        "pre",
        s,
        authz,
        prompt_id=p,
        tool_name="NotebookEdit",
        tool_input={"notebook_path": str(path), "new_source": "x"},
    )
    return ((out or {}).get("hookSpecificOutput") or {}).get(
        "permissionDecision"
    ) or "allow"


s, p = session(E)
check(
    "Z-T18 enforce: a notebook edit without approval is denied too",
    notebook(s, p, E, REPO / "n.ipynb"),
    "deny",
)
s, p = session(E, approve=True)
check(
    "Z-T18 enforce, approved: an in-scope notebook is allowed, another is not",
    (notebook(s, p, E, REPO / "src" / "n.ipynb"), notebook(s, p, E, REPO / "n.ipynb")),
    ("allow", "deny"),
)
s, p = session(W)
n_before = len(observed())
UNC = "\\\\nohost.invalid\\share\\x.py"
check(
    "Z-T19 warn: a UNC path is observed as data without being resolved",
    (decision(s, p, W, UNC), [r.get("class") for r in observed()[n_before:]]),
    ("allow", ["data"]),
)

OBSERVE.unlink()
OBSERVE.mkdir()  # the log cannot be written
s, p = session(W)
check(
    "Z-T13 warn: an unwritable observation log does not stop the edit",
    decision(s, p, W, REPO / "q.py"),
    "allow",
)
OBSERVE.rmdir()

# ---- the report tool: counts only
LOG = HOME / "report-input.jsonl"
LOG.write_text(
    "\n".join(
        json.dumps(r)
        for r in (
            {
                "at": 1794700000,
                "reason": "no_approval",
                "class": "code",
                "ext": ".py",
                "tool": "Edit",
            },
            {
                "at": 1794700100,
                "reason": "no_approval",
                "class": "code",
                "ext": ".py",
                "tool": "Write",
            },
            {
                "at": 1794800000,
                "reason": "out_of_scope",
                "class": "data",
                "ext": ".jsonl",
                "tool": "Edit",
            },
        )
    )
    + "\nnot json\n"
    + '{"at": -1e30, "reason": "x"}\n{"at": NaN}\n[1, 2]\n{"at": 1e20}\n',
    encoding="utf-8",
)
rep = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "authz_report.py"), "--file", str(LOG)],
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=False,
)
check("Z-T15 the report runs", (rep.returncode, rep.stderr), (0, ""))
check(
    "Z-T15 ... and prints totals by reason, class and extension",
    all(
        part in rep.stdout
        for part in (
            "total 3",
            "no_approval 2",
            "out_of_scope 1",
            "code 2",
            "data 1",
            ".py 2",
            "skipped 5",
        )
    ),
    True,
)
missing = subprocess.run(
    [
        sys.executable,
        "-I",
        str(HOOKS / "authz_report.py"),
        "--file",
        str(HOME / "none.jsonl"),
    ],
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=False,
)
check(
    "Z-T15 no log yet: says so, exit 0",
    (missing.returncode, "total 0" in missing.stdout),
    (0, True),
)

for _p in (HOME, WITNESS, REPO):
    shutil.rmtree(_p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
