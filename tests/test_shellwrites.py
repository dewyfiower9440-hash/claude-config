"""P0-07: write targets read from a shell command before it runs (run with python -I).

`shellwrites.write_targets` is best effort: it names the files a Bash or PowerShell command
visibly writes (redirections, a small table of programs and cmdlets) and counts the writes
whose target it cannot tell. The hook judges those targets like an Edit: observed in the
default warn mode, denied under enforce.
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
HOME, WITNESS = HERE / "_tmp_shellw_home", HERE / "_tmp_shellw_w"
REPO = HERE / "_tmp_shellw_repo"
for _p in (HOME, WITNESS, REPO):
    shutil.rmtree(_p, ignore_errors=True)
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(WITNESS / "hwm")
os.environ.pop("CLAUDE_AUTHZ_MODE", None)
sys.stdout.reconfigure(errors="backslashreplace")
sys.path.insert(0, str(HOOKS))
import approvals as ap
from shellwrites import write_targets

fails = 0
CWD = "C:\\work\\proj"


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


def targets(
    command: str, dialect: str = "bash", cwd: str = CWD
) -> tuple[list[str], int]:
    paths, unresolved = write_targets(command, dialect, cwd)
    return [p.replace("\\", "/") for p in paths], unresolved


P = "C:/work/proj"
HOMEDIR = os.path.expanduser("~").replace("\\", "/")
TEMPDIR = tempfile.gettempdir().replace("\\", "/")
for name, command, dialect, want in (
    ("W-T1 >", "echo hi > out.txt", "bash", ([f"{P}/out.txt"], 0)),
    ("W-T1 >> with a quoted path", 'cat a >> "b c.txt"', "bash", ([f"{P}/b c.txt"], 0)),
    ("W-T1 2>", "make 2> err.log", "bash", ([f"{P}/err.log"], 0)),
    (
        "W-T1 &> and &>>",
        "a &> o1.txt; b &>> o2.txt",
        "bash",
        ([f"{P}/o1.txt", f"{P}/o2.txt"], 0),
    ),
    (
        "W-T1 >| and two redirections",
        "c >| f; d 1> g 2> h",
        "bash",
        ([f"{P}/f", f"{P}/g", f"{P}/h"], 0),
    ),
    (
        "W-T1 heredoc body is data",
        "cat <<EOF > o.txt\nx > y\nEOF",
        "bash",
        ([f"{P}/o.txt"], 0),
    ),
    (
        "W-T1 PowerShell > and *>",
        "a > o.txt; b *> all.txt",
        "powershell",
        ([f"{P}/o.txt", f"{P}/all.txt"], 0),
    ),
    ("W-T2 /dev/null and 2>&1", "a > /dev/null 2>&1; b 2>/dev/null", "bash", ([], 0)),
    ("W-T2 $null and NUL", "a > $null; b 2>$null; c > NUL", "powershell", ([], 0)),
    ("W-T3 tee", "a | tee -a t1 t2", "bash", ([f"{P}/t1", f"{P}/t2"], 0)),
    (
        "W-T3 sed -i",
        "sed -i s/a/b/ f1.py f2.py",
        "bash",
        ([f"{P}/f1.py", f"{P}/f2.py"], 0),
    ),
    ("W-T3 sed -i.bak -e", "sed -i.bak -e s/a/b/ f1", "bash", ([f"{P}/f1"], 0)),
    ("W-T3 sed without -i only reads", "sed s/a/b/ f1", "bash", ([], 0)),
    ("W-T3 cp writes the destination", "cp -r a b", "bash", ([f"{P}/b"], 0)),
    ("W-T3 mv changes both", "mv a b", "bash", ([f"{P}/a", f"{P}/b"], 0)),
    (
        "W-T3 rm and touch",
        "rm -rf d1 f2; touch n1",
        "bash",
        ([f"{P}/d1", f"{P}/f2", f"{P}/n1"], 0),
    ),
    ("W-T3 dd of=", "dd if=a of=b bs=1", "bash", ([f"{P}/b"], 0)),
    ("W-T3 truncate -s", "truncate -s 0 big.log", "bash", ([f"{P}/big.log"], 0)),
    ("W-T3 behind a wrapper", "sudo env X=1 tee out", "bash", ([f"{P}/out"], 0)),
    (
        "W-T4 Set-Content -Path",
        'Set-Content -Path "a b.txt" -Value x',
        "powershell",
        ([f"{P}/a b.txt"], 0),
    ),
    (
        "W-T4 Set-Content positional",
        "Set-Content a.txt x",
        "powershell",
        ([f"{P}/a.txt"], 0),
    ),
    (
        "W-T4 Out-File and Add-Content",
        "x | Out-File -FilePath o.txt; ac l.txt y",
        "powershell",
        ([f"{P}/o.txt", f"{P}/l.txt"], 0),
    ),
    ("W-T4 New-Item", "ni -ItemType File z.txt", "powershell", ([f"{P}/z.txt"], 0)),
    (
        "W-T4 Remove-Item list",
        "Remove-Item -Recurse -Force d1, d2",
        "powershell",
        ([f"{P}/d1", f"{P}/d2"], 0),
    ),
    (
        "W-T4 Copy-Item destination",
        "Copy-Item a.txt -Destination b.txt",
        "powershell",
        ([f"{P}/b.txt"], 0),
    ),
    ("W-T4 Copy-Item positional", "cp a.txt b.txt", "powershell", ([f"{P}/b.txt"], 0)),
    (
        "W-T4 Move-Item both",
        "Move-Item a.txt b.txt",
        "powershell",
        ([f"{P}/a.txt", f"{P}/b.txt"], 0),
    ),
    (
        "W-T4 cmdlet names in any case",
        "set-content A.TXT x",
        "powershell",
        ([f"{P}/A.TXT"], 0),
    ),
    ("W-T5 after cd", "cd sub && echo x > f", "bash", ([f"{P}/sub/f"], 0)),
    (
        "W-T5 Git Bash drive path",
        "echo x > /c/dev/x.txt",
        "bash",
        (["C:/dev/x.txt"], 0),
    ),
    ("W-T5 home", "echo x > ~/x.txt", "bash", ([f"{HOMEDIR}/x.txt"], 0)),
    (
        "W-T5 absolute Windows path",
        "Set-Content C:\\dev\\y.txt x",
        "powershell",
        (["C:/dev/y.txt"], 0),
    ),
    ("W-T6 variable target", "echo x > $OUT/f", "bash", ([], 1)),
    ("W-T6 command substitution", "echo x > $(date).txt", "bash", ([], 1)),
    ("W-T6 glob target", "rm *.py", "bash", ([], 1)),
    (
        "W-T6 PowerShell variable",
        "Set-Content $p x; a > $env:TEMP\\x.txt",
        "powershell",
        ([], 2),
    ),
    ("W-T6 unbalanced quote", "echo 'x > f", "bash", ([], 1)),
    ("W-T7 reads only", "cat a; git status; ls > /dev/null", "bash", ([], 0)),
    ("W-T7 the gate", "python -I tests/run_all.py", "bash", ([], 0)),
    (
        "W-T7 a script's own writes are not visible",
        "python x.py --out o.txt",
        "bash",
        ([], 0),
    ),
    (
        "W-T7 PowerShell reads only",
        "Get-Content a.txt | Select-Object -First 3",
        "powershell",
        ([], 0),
    ),
    # review: Git Bash special paths, lists, PowerShell forms, options, compound commands
    (
        "W-T15 /tmp is the temp folder",
        "echo x > /tmp/x.txt",
        "bash",
        ([f"{TEMPDIR}/x.txt"], 0),
    ),
    ("W-T15 /dev/* is no file", "a > /dev/fd/2; b 2> /dev/stderr", "bash", ([], 0)),
    ("W-T15 >& file", "a >& both.log; b 2>&1", "bash", ([f"{P}/both.log"], 0)),
    (
        "W-T16 PowerShell 2> and 2>>",
        "a 2> err.txt; b 2>> e2.txt",
        "powershell",
        ([f"{P}/err.txt", f"{P}/e2.txt"], 0),
    ),
    (
        "W-T16 comma list without spaces",
        "Remove-Item src\\a,..\\other.py",
        "powershell",
        ([f"{P}/src/a", "C:/work/other.py"], 0),
    ),
    (
        "W-T16 -Path with a comma list",
        "Remove-Item -Path a, b -Force",
        "powershell",
        ([f"{P}/a", f"{P}/b"], 0),
    ),
    (
        "W-T16 -Path:value",
        "Set-Content -Path:a.txt x",
        "powershell",
        ([f"{P}/a.txt"], 0),
    ),
    (
        "W-T16 -Dest prefix names the destination",
        "Copy-Item -Dest b.txt a.txt",
        "powershell",
        ([f"{P}/b.txt"], 0),
    ),
    (
        "W-T16 Copy-Item -Path src dst",
        "Copy-Item -Path a.txt b.txt",
        "powershell",
        ([f"{P}/b.txt"], 0),
    ),
    (
        "W-T16 a switch does not eat the path",
        "Set-Content -NoNewline a.txt x",
        "powershell",
        ([f"{P}/a.txt"], 0),
    ),
    ("W-T17 cp -t dir", "cp -t dir a b", "bash", ([f"{P}/dir"], 0)),
    (
        "W-T17 mv --target-directory=",
        "mv --target-directory=dir a",
        "bash",
        ([f"{P}/a", f"{P}/dir"], 0),
    ),
    (
        "W-T17 touch -d value",
        "touch -d 2020-01-01 f; touch -r ref g",
        "bash",
        ([f"{P}/f", f"{P}/g"], 0),
    ),
    ("W-T17 rm --", "rm -- -weird", "bash", ([f"{P}/-weird"], 0)),
    ("W-T17 install -d", "install -d d1 d2", "bash", ([f"{P}/d1", f"{P}/d2"], 0)),
    ("W-T18 for/do", "for f in a b; do rm g; done", "bash", ([f"{P}/g"], 0)),
    (
        "W-T18 if/then and braces",
        "if true; then touch f; fi; { rm x; }",
        "bash",
        ([f"{P}/f", f"{P}/x"], 0),
    ),
    ("W-T18 timeout", "timeout 5 rm f", "bash", ([f"{P}/f"], 0)),
    (
        "W-T19 no false target",
        'git commit -m "a > b"; echo "x -> y"; [ 1 -gt 0 ]',
        "bash",
        ([], 0),
    ),
    (
        "W-T19 PowerShell comparison",
        "if (1 -gt 0) { 'x' } ; a 2>&1",
        "powershell",
        ([], 0),
    ),
):
    check(name, targets(command, dialect), want)

# ---- the hook itself
(REPO / "docs" / "plans").mkdir(parents=True)
(REPO / "src" / "old").mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=REPO, check=True)
ap.Store(HOME, WITNESS / "hwm").admin_activate(lambda code: True, "test")
PLAN = (
    "---\nstatus: in-progress\ntask: shellwt\n---\n\n# T\n\n"
    "## 변경 범위\n- src/**\n\n## 단계\n- [ ] 1. a\n"
)
PLAN_FILE = REPO / "docs" / "plans" / "2026-11-15-shellwt.md"
PLAN_FILE.write_bytes(PLAN.encode("utf-8"))
OBSERVE = HOME / "authz-observe.jsonl"
_n = 0


def call(mode: str, session: str, authz: str | None, **payload: object) -> dict | None:
    env = dict(os.environ)
    if authz is not None:
        env["CLAUDE_AUTHZ_MODE"] = authz
    payload.setdefault("cwd", str(REPO))
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps({"session_id": session, **payload}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def session(authz: str | None, approve: bool = False) -> tuple[str, str]:
    global _n
    _n += 1
    s, p = uuid.uuid4().hex, f"w{_n}"
    call("prompt", s, authz, prompt_id=p, prompt="do it")
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


def shell(s: str, p: str, authz: str | None, command: str, tool: str = "Bash") -> dict:
    out = call(
        "pre", s, authz, prompt_id=p, tool_name=tool, tool_input={"command": command}
    )
    return (out or {}).get("hookSpecificOutput") or {}


def decision(
    s: str, p: str, authz: str | None, command: str, tool: str = "Bash"
) -> str:
    return shell(s, p, authz, command, tool).get("permissionDecision") or "allow"


def observed() -> list[dict]:
    if not OBSERVE.is_file():
        return []
    return [
        json.loads(x) for x in OBSERVE.read_text(encoding="utf-8").splitlines() if x
    ]


E = "enforce"
s, p = session(None)
check(
    "W-T8 warn, no approval: the command runs",
    decision(s, p, None, "echo x > src/a.py"),
    "allow",
)
check(
    "W-T8 ... and one observation line names the tool",
    [(r.get("reason"), r.get("tool"), r.get("ext")) for r in observed()],
    [("no_approval", "Bash", ".py")],
)
check(
    "W-T8 warn: a read-only command adds no line",
    (decision(s, p, None, "git status"), len(observed())),
    ("allow", 1),
)

s, p = session(E)
w9 = shell(s, p, E, "echo x > src/a.py")
check(
    "W-T9 enforce, no approval: denied, the reason names the file",
    (w9.get("permissionDecision"), "a.py" in str(w9.get("permissionDecisionReason"))),
    ("deny", True),
)
check(
    "W-T9 enforce: PowerShell as well",
    decision(s, p, E, "Set-Content src\\a.py x", "PowerShell"),
    "deny",
)
check(
    "W-T11 enforce, no approval: read-only commands and the gate still run",
    [
        decision(s, p, E, c)
        for c in (
            "git status",
            "cat src/a.py",
            "python -I tests/run_all.py",
            "ls > /dev/null",
        )
    ],
    ["allow"] * 4,
)
check(
    "W-T13 enforce: a script's own writes are not seen (documented limit)",
    decision(s, p, E, "python x.py"),
    "allow",
)

s, p = session(E, approve=True)
scratch = (Path(tempfile.gettempdir()) / "shellw-never-written.txt").as_posix()
check(
    "W-T10 enforce, approved: in scope / out of scope / %TEMP%",
    [
        decision(s, p, E, c)
        for c in ("echo x > src/a.py", "echo x > other.py", f"echo x > {scratch}")
    ],
    ["allow", "deny", "allow"],
)
check(
    "W-T10 enforce, approved: one target outside is enough to deny",
    decision(s, p, E, "cp src/a.py other.py && touch src/b.py"),
    "deny",
)
check(
    "W-T12 enforce, approved: removing a folder inside the scope",
    decision(s, p, E, "rm -r src/old"),
    "allow",
)
check(
    "W-T12 ... but not the repository's own folder outside it",
    decision(s, p, E, "rm -r docs"),
    "deny",
)
n_before = len(observed())
check(
    "W-T6 enforce: an unresolvable target is not a denial",
    decision(s, p, E, "echo x > $OUT/f"),
    "allow",
)
check(
    "W-T6 ... it is observed as unresolved",
    [r.get("reason") for r in observed()[n_before:]],
    ["unresolved"],
)

many = "rm " + " ".join(f"f{i}" for i in range(260))
many_paths, many_unresolved = write_targets(many, "bash", CWD)
check(
    "W-T20 more targets than the cap: the rest is unresolved",
    (len(many_paths), many_unresolved),
    (200, 60),
)

s, p = session(None, approve=True)
n_before = len(observed())
check(
    "W-T21 warn, approved: in scope adds nothing, out of scope and unresolved are observed",
    (
        [
            decision(s, p, None, c)
            for c in ("echo x > src/a.py", "echo x > other.py", "echo x > $OUT/f")
        ],
        [r.get("reason") for r in observed()[n_before:]],
    ),
    (["allow"] * 3, ["out_of_scope", "unresolved"]),
)
s, p = session(E, approve=True)
check(
    "W-T22 enforce, approved: a shell write to the approved plan document is allowed",
    decision(s, p, E, f"echo x >> {PLAN_FILE.as_posix()}"),
    "allow",
)

# ---- settings.json: the discipline hook also sees notebook edits
SETTINGS = json.loads((HERE.parent / "settings.json").read_text(encoding="utf-8"))


def matchers(event: str, script: str) -> list[str]:
    return [
        g.get("matcher", "")
        for g in SETTINGS["hooks"][event]
        if any(script in h.get("command", "") for h in g.get("hooks", []))
    ]


check(
    "W-T14 the discipline pre/post matchers include NotebookEdit",
    (
        matchers("PreToolUse", "discipline.py pre"),
        matchers("PostToolUse", "discipline.py post"),
    ),
    (
        ["Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell"],
        ["Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell|Agent|Task|ExitPlanMode"],
    ),
)
check(
    "W-T14 the other matchers are unchanged",
    (matchers("PreToolUse", "guard.py"), matchers("PostToolUse", "format_lint.py")),
    (
        ["Bash|PowerShell|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob"],
        ["Edit|Write|MultiEdit"],
    ),
)

for _p in (HOME, WITNESS, REPO):
    shutil.rmtree(_p, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
