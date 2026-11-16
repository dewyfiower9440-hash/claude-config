"""P4-01: a read-only report of what is actually used (skills, subagents, MCP servers, from
the session transcripts) and of what the hooks' observation files hold. Names and numbers
only.

    python -I tests/test_usage_report.py

Example transcripts and observation rows in a temp folder; the real folders are not read.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = HERE / "_tmp_usage_report"
shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True)
sys.path.insert(0, str(HOOKS))
try:
    import usage_report as ur
except ImportError:  # before the module exists: every check below fails, none crashes
    ur = None

fails = 0
DAY = 86400.0
NOW = time.time()
SECRET = "ZQXJ-prompt-marker"


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:150],
        "" if ok else f"(want {want!r})",
    )


def call(fn: str, *args: Any) -> Any:
    return getattr(ur, fn)(*args) if hasattr(ur, fn) else None


def use(name: str, **given: object) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": SECRET},
                    {
                        "type": "tool_use",
                        "name": name,
                        "input": {**given, "prompt": SECRET},
                    },
                ]
            },
        }
    )


SESSION = [
    use("Skill", skill="implement"),
    use("Skill", skill="implement"),
    use("Skill", skill="claude-api"),
    use("Agent", subagent_type="code-reviewer"),
    use("Task", subagent_type="code-reviewer"),
    use("Agent"),
    use("mcp__context7__query-docs"),
    use("mcp__context7__resolve-library-id"),
    use("mcp__github__list_issues"),
    use("Bash", command="echo " + SECRET),
    use("Edit"),
    json.dumps({"type": "user", "message": {"content": "plain text " + SECRET}}),
]
# a line is parsed only when it can hold a call (it has the text "tool_use"); the two
# broken ones below do, an ordinary broken line is not looked at and not counted
ODD = [
    'not json "tool_use"',
    json.dumps([1, "tool_use"]),
    "not json either",
    json.dumps(
        {
            "message": {
                "content": [{"type": "tool_use"}, "x", {"type": "tool_use", "name": 7}]
            }
        }
    ),
    json.dumps(
        {"message": {"content": [{"type": "tool_use", "name": "Skill", "input": "x"}]}}
    ),
    use("Skill", skill="bad name with spaces " + SECRET),
    use("Agent", subagent_type="x" * 200),
    "",
]


def counted(lines: list[str]) -> Any:
    got = call("count_calls", lines)
    if got is None:
        return None
    return (
        dict(got.skills),
        dict(got.agents),
        dict(got.servers),
        dict(got.mcp_tools),
        dict(got.tools),
        got.tool_uses,
        got.bad,
    )


# ---------------------------------------------------------------- pure: calls
check(
    "U1 skills, subagents, MCP servers and tools, other tools: counted by name",
    counted(SESSION),
    (
        {"implement": 2, "claude-api": 1},
        {"code-reviewer": 2, "(default)": 1},
        {"context7": 2, "github": 1},
        {
            "context7:query-docs": 1,
            "context7:resolve-library-id": 1,
            "github:list_issues": 1,
        },
        {"Skill": 3, "Agent": 2, "Task": 1, "Bash": 1, "Edit": 1},
        11,
        0,
    ),
)
odd = counted(ODD)
check(
    "U2 broken lines and odd blocks are skipped and counted; odd names become (other)",
    (odd[0], odd[1], odd[5], odd[6]) if odd else None,
    ({"(other)": 2}, {"(other)": 1}, 3, 2),
)
check("U2 nothing at all", counted([]), ({}, {}, {}, {}, {}, 0, 0))

# ---------------------------------------------------------------- pure: numbers
check(
    "U3 percentile (nearest rank): none, one value, known values",
    [
        call("percentile", [], 50),
        call("percentile", [7], 90),
        call("percentile", [1, 2, 3, 4], 50),
        call("percentile", [4, 1, 3, 2], 50),
        call("percentile", list(range(1, 11)), 90),
        call("percentile", list(range(1, 11)), 100),
    ],
    [None, 7, 2, 2, 9, 10],
)


def summary(kind: str, rows: list[Any], since: float = 0.0) -> str:
    got = call("summarize_rows", kind, rows, since)
    return " | ".join(got) if got is not None else ""


RISK = [
    {
        "at": NOW,
        "score": 2,
        "kinds": {"code": 1},
        "files": 1,
        "lines": 0,
        "repos": 1,
        "advised": False,
    },
    {
        "at": NOW,
        "score": 15,
        "kinds": {"guard": 1, "ci": 1, "deps": 1},
        "files": 3,
        "lines": 0,
        "repos": 1,
        "advised": True,
    },
    {
        "at": NOW,
        "score": 9,
        "kinds": {"code": 2, "guard": 1},
        "files": 3,
        "lines": 80,
        "repos": 1,
        "advised": False,
    },
    {
        "at": NOW - 40 * DAY,
        "score": 99,
        "kinds": {"code": 1},
        "files": 1,
        "lines": 0,
        "repos": 1,
        "advised": True,
    },
    {"at": "soon", "score": 1},
    "x",
]
risk = summary("risk", RISK, NOW - 30 * DAY)
check(
    "U4 risk: rows in the period, the score's spread, at or above the threshold, advised, kinds",
    [
        w in risk
        for w in (
            "rows 3",
            "skipped 2",
            "min 2",
            "median 9",
            "p90 15",
            "max 15",
            "at or above 8: 2",
            "advised 1",
            "code 3",
            "guard 2",
        )
    ],
    [True] * 10,
)
check("U4 ... a row before the period is not in it", "99" in risk, False)
check(
    "U5 test-gap, review, output-size, failures",
    [
        summary("test-gap", [{"at": NOW, "files": 2}, {"at": NOW, "files": 1}]),
        summary(
            "review",
            [
                {"at": NOW, "state": "missing", "rules": 1, "gate": False},
                {"at": NOW, "state": "missing", "rules": 0, "gate": True},
                {"at": NOW, "state": "stale", "rules": 2, "gate": False},
            ],
        ),
        summary(
            "output-size",
            [
                {
                    "at": NOW,
                    "chars": 9000,
                    "test": True,
                    "failed": False,
                    "who": "main",
                },
                {
                    "at": NOW,
                    "chars": 3000,
                    "test": True,
                    "failed": False,
                    "who": "main",
                },
                {
                    "at": NOW,
                    "chars": 20000,
                    "test": False,
                    "failed": True,
                    "who": "subagent",
                },
            ],
        ),
        summary(
            "failures",
            [
                {"at": NOW, "source": "gate", "kind": "encoding", "session": "a"},
                {"at": NOW, "source": "gate", "kind": "encoding", "session": "b"},
                {"at": NOW, "source": "ci", "kind": "timeout", "session": "a"},
            ],
        ),
    ],
    [
        "rows 2 (skipped 0): files 3",
        "rows 3 (skipped 0): missing 2, stale 1; gate on 1",
        "rows 3 (skipped 0): test runs 2; main 2, subagent 1; chars median 9000, max 20000; at or above 8000: 2",
        "rows 3 (skipped 0), sessions 2: gate/encoding 2, ci/timeout 1",
    ],
)
BIG = 1e308
check(
    "U5 numbers too large to print or to add up: shown as ?, no error",
    [
        summary("test-gap", [{"at": NOW, "files": BIG}, {"at": NOW, "files": BIG}]),
        "max ?" in summary("risk", [{"at": NOW, "score": BIG, "kinds": {"code": BIG}}]),
        "max ?"
        in summary("output-size", [{"at": NOW, "chars": 10**400, "who": "main"}]),
    ],
    ["rows 2 (skipped 0): files ?", True, True],
)
if ur is not None and hasattr(ur, "read_rows"):
    long_file = ROOT / "long.jsonl"
    long_file.write_text(
        "".join(json.dumps({"at": NOW, "files": i}) + "\n" for i in range(200)),
        encoding="utf-8",
        newline="\n",
    )
    limit = ur.MAX_READ_BYTES
    ur.MAX_READ_BYTES = 1000
    got_rows = ur.read_rows(long_file)
    ur.MAX_READ_BYTES = limit
    whole_rows = ur.read_rows(long_file)
    long_file.unlink()
    check(
        "U5 a file longer than the limit: its end only, whole rows, and that it was cut is said",
        (
            isinstance(got_rows, tuple) and got_rows[1],
            isinstance(got_rows, tuple) and 0 < len(got_rows[0]) < 200,
            isinstance(got_rows, tuple)
            and all(isinstance(r, dict) for r in got_rows[0]),
            isinstance(got_rows, tuple) and got_rows[0][-1].get("files"),
            isinstance(whole_rows, tuple) and (len(whole_rows[0]), whole_rows[1]),
        ),
        (True, True, True, 199, (200, False)),
    )
check(
    "U5 no rows; a kind that is not known",
    (summary("risk", []), call("summarize_rows", "nonsense", [], 0.0)),
    ("rows 0 (skipped 0)", None),
)
import discipline as disc

# a statement between the two imports: `discipline` is sorted as first-party only where
# ~/.claude/discipline/ exists, so one block with both is "unsorted" either here or on CI
OUTPUT_DEFAULT = disc.OUTPUT_ADVICE_DEFAULT
import risk as risk_module

check(
    "U6 the thresholds the report names are the hooks' own",
    (getattr(ur, "RISK_AT", None), getattr(ur, "OUTPUT_AT", None)),
    (risk_module.ADVICE_AT, OUTPUT_DEFAULT),
)

# ---------------------------------------------------------------- the commands
PROJECTS = ROOT / "projects"
CONFIG = ROOT / "config"
for rel in ("skills/plan", "skills/implement", "skills/synced/x", "agents"):
    (CONFIG / rel).mkdir(parents=True)
for rel in (
    "skills/plan/SKILL.md",
    "skills/implement/SKILL.md",
    "skills/synced/x/SKILL.md",
    "agents/code-reviewer.md",
    "agents/test-runner.md",
):
    (CONFIG / rel).write_text("---\nname: x\n---\n", encoding="utf-8")
(CONFIG / "skills" / "SKILLS.md").write_text("# registry\n", encoding="utf-8")


def session(folder: str, name: str, lines: list[str], days_ago: float = 0.0) -> None:
    path = PROJECTS / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    at = NOW - days_ago * DAY
    os.utime(path, (at, at))


REPEATED = json.dumps(
    {
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_same",
                    "name": "Agent",
                    "input": {"subagent_type": "researcher"},
                }
            ]
        }
    }
)
QUOTED = json.dumps(
    {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "plan"},
                        }
                    ],
                },
                {
                    "type": "text",
                    "text": '{"type":"tool_use","name":"Skill","input":{"skill":"plan"}}',
                },
            ]
        },
    }
)
session("C--dev-a", "s1.jsonl", [*SESSION, REPEATED])
# a resumed session carries the earlier blocks again: the same block id is one call
session(
    "C--dev-a",
    "s2.jsonl",
    [use("Bash"), use("Skill", skill="implement"), REPEATED, QUOTED],
)
session("C--dev-a", "old.jsonl", [use("Skill", skill="plan")] * 5, days_ago=90)
session("C--dev-quant-research", "q.jsonl", [use("Skill", skill="debug")] * 9)
session("C--dev-qr-backup", "q.jsonl", [use("Skill", skill="debug")] * 9)
# names that only look like the out-of-scope ones are in scope
session("C--dev-quantity-tool", "a.jsonl", [use("Skill", skill="commit")])
session("C--dev-qr-code-gen", "a.jsonl", [use("Skill", skill="commit")])
session("C--dev-a/sub", "agent.jsonl", [use("Skill", skill="predeploy")] * 9)
# what a subagent of session s1 called is in a folder of its own
session(
    "C--dev-a/s1/subagents",
    "agent-1.jsonl",
    [use("mcp__context7__query-docs")] * 4 + [use("Skill", skill="claude-api")],
)
session(
    "C--dev-a/old/subagents",
    "agent-9.jsonl",
    [use("mcp__github__list_issues")] * 7,
    days_ago=90,
)
(PROJECTS / "C--dev-a" / "notes.txt").write_text("x", encoding="utf-8")


def digest() -> str:
    h = hashlib.sha256()
    for f in sorted(ROOT.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
            h.update(str(f.stat().st_mtime_ns).encode())
    return h.hexdigest()


def run(*args: str) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "usage_report.py"), *args],
        env=dict(
            os.environ, USERPROFILE=str(ROOT / "nohome"), HOME=str(ROOT / "nohome")
        ),
        capture_output=True,
        check=False,
    )
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


def lines_of(out: str) -> list[str]:
    return [" ".join(line.split()) for line in out.splitlines()]


before = digest()
rc, out = run("calls", "--projects", str(PROJECTS), "--config", str(CONFIG))
shown = lines_of(out)
check(
    "U7 calls: the recent sessions of folders in scope; the two out-of-scope folders are skipped",
    (
        rc,
        any(line.startswith("sessions 4 ") for line in shown),
        "skipped (out of scope): 2" in out,
    ),
    (0, True, True),
)
check(
    "U7 ... names with their counts; installed skills and subagents that were never called show 0",
    [
        line in shown
        for line in (
            "implement 3",
            "claude-api 1",
            "commit 2",
            "plan 0",
            "code-reviewer 2",
            "researcher 1",
            "test-runner 0",
            "context7 2",
            "github 1",
        )
    ],
    [True] * 9,
)
check(
    "U7 ... not the old session, not a stray subfolder, not account-synced skills, no prompt text",
    (
        "predeploy" in out,
        "debug" in out,
        "synced" in out or " x 0" in out,
        SECRET in out,
        "plan 5" in out,
    ),
    (False, False, False, False, False),
)
check(
    "U7 ... no folder or file name of the transcripts",
    any(
        text in out
        for text in ("C--dev", "s1.jsonl", "agent-1", str(PROJECTS), "_tmp_usage")
    ),
    False,
)
check(
    "U7 ... how much was read, and the block that came twice (a resumed session) once",
    ("tool_use blocks 16" in out, "lines read 19" in out, "repeated blocks 1" in out),
    (True, True, True),
)
inside = (
    out.split("[mcp servers, inside subagents]")[-1]
    if "inside subagents]" in out
    else ""
)
check(
    "U7 ... what subagents called is counted apart, from the subagent transcripts of those sessions",
    (
        "subagent transcripts 1" in out,
        lines_of(inside)[1:2],
        "claude-api 1" in lines_of(out.split("[skills, inside subagents]")[-1])[:3],
        "github 7" in out,
    ),
    (True, ["context7 4"], True, False),
)
rc, out = run(
    "calls",
    "--projects",
    str(PROJECTS),
    "--config",
    str(CONFIG),
    "--since",
    "2000-01-01",
)
check(
    "U8 --since: the older session counts too",
    (rc, "plan 5" in lines_of(out)),
    (0, True),
)
rc, out = run("calls", "--projects", str(ROOT / "nowhere"), "--config", str(CONFIG))
check(
    "U8 no transcripts folder: nothing counted, installed names still listed, exit code 0",
    (
        rc,
        any(line.startswith("sessions 0 ") for line in lines_of(out)),
        "plan 0" in lines_of(out),
    ),
    (0, True, True),
)

check("U8 the calls runs wrote and touched nothing", digest() == before, True)

HOME = ROOT / "home"
FAILURES = ROOT / "failures"
HOME.mkdir()
FAILURES.mkdir()


def rows_file(path: Path, rows: list[Any]) -> None:
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in rows) + "broken\n", encoding="utf-8"
    )


rows_file(HOME / "risk-observe.jsonl", RISK[:4])
rows_file(HOME / "test-gap-observe.jsonl", [{"at": NOW, "files": 2}])
rows_file(
    FAILURES / "abc.jsonl",
    [{"at": NOW, "source": "gate", "kind": "encoding", "session": "a"}],
)
rows_file(
    FAILURES / "def.jsonl",
    [{"at": NOW, "source": "ci", "kind": "timeout", "session": "b"}],
)
before = digest()
rc, out = run("observe", "--home", str(HOME), "--failures", str(FAILURES))
check(
    "U9 observe: one summary per kind; a missing file is said to be missing; broken lines counted",
    (
        rc,
        "[risk] rows 3 (skipped 1)" in out,
        "[test-gap] rows 1 (skipped 1): files 2" in out,
        "[review] no file" in out,
        "[output-size] no file" in out,
        "[failures] rows 2 (skipped 2), sessions 2" in out,
    ),
    (0, True, True, True, True, True),
)
rc, out = run(
    "observe", "--home", str(ROOT / "nowhere"), "--failures", str(ROOT / "nowhere2")
)
check(
    "U9 no folders at all: every kind missing, exit code 0",
    (rc, out.count("no file")),
    (0, 5),
)
check("U10 nothing was written or touched", digest() == before, True)
rc, out = run(
    "calls",
    "--projects",
    str(PROJECTS),
    "--config",
    str(CONFIG),
    "--since",
    "1970-01-01",
)
check(
    "U11 a date at the edge of what the clock knows: no traceback",
    "Traceback" in out,
    False,
)
check(
    "U11 no command, a date that is none, an unknown command: exit code 2",
    [run()[0], run("calls", "--since", "yesterday")[0], run("erase")[0]],
    [2, 2, 2],
)

# ---------------------------------------------------------------- commands (P5-02)
FORMS = [
    ("bash", "uv run pytest -q --cov", ["uv run pytest"]),
    ("bash", "uv run --project C:/dev/tools/db-mcp db-mcp sqlite", ["uv run <other>"]),
    (
        "bash",
        "uv run --no-project --python 3.13 --with pytest pytest tests -q",
        ["uv run pytest"],
    ),
    ("bash", "uv sync --locked && uv run ruff check .", ["uv sync", "uv run ruff"]),
    ("bash", "uv run python -c 'print(1)'", ["uv run python"]),
    ("bash", "uv run ./scripts/x.py", ["uv run <other>"]),
    ("bash", "uv run --refresh-package secrettoken123 pytest", ["uv run pytest"]),
    ("bash", "uv run --unknown-option myhost.corp python x", ["uv run <other>"]),
    ("bash", "uv run -w requests python -c 1", ["uv run python"]),
    ("bash", "uv --directory C:/x run pytest", ["uv <other>"]),
    ("bash", "pnpm --filter sk-ABCDEF123456 build", ["pnpm <other>"]),
    ("bash", "uvx --from evil ruff check", ["uvx <other>"]),
    ("bash", "uv run", ["uv run <none>"]),
    ("bash", "node scripts/build.js --watch", ["node <script>"]),
    ("bash", "node -e 'console.log(1)'", ["node <inline>"]),
    ("bash", "pnpm exec tsc && pnpm dlx create-x", ["pnpm exec", "pnpm dlx"]),
    ("bash", "uvx ruff check . ; npx tsc --noEmit", ["uvx ruff", "npx tsc"]),
    ("powershell", "uv run pyright; pnpm test", ["uv run pyright", "pnpm test"]),
    ("bash", "git status && ls", []),
    ("bash", "echo 'unterminated", []),
    ("bash", "pnpm $(cat secret.txt)", ["pnpm <other>"]),
]
forms_of = getattr(ur, "command_forms", lambda command, dialect: None)
check(
    "U12 command_forms: the program and its sub-command or tool, nothing more",
    [
        (command, forms_of(command, dialect))
        for dialect, command, want in FORMS
        if forms_of(command, dialect) != want
    ],
    [],
)
FPROJ = ROOT / "forms-projects"
(FPROJ / "C--dev-app").mkdir(parents=True)
(FPROJ / "C--dev-quant-research").mkdir(parents=True)


def shell_row(block_id: str, tool: str, command: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": block_id,
                        "name": tool,
                        "input": {"command": command},
                    }
                ]
            },
        }
    )


(FPROJ / "C--dev-app" / "s1.jsonl").write_text(
    "\n".join(
        [
            shell_row("a1", "Bash", "uv run pytest -q C:/dev/app/tests/secret_case.py"),
            shell_row("a1", "Bash", "uv run pytest -q C:/dev/app/tests/secret_case.py"),
            shell_row(
                "a2", "Bash", "uv run pytest && node tools/gen.js --token abc123"
            ),
            shell_row("a3", "PowerShell", "pnpm exec vitest"),
            "not json",
            json.dumps({"type": "user", "message": {"content": "uv run python"}}),
        ]
    )
    + "\n",
    encoding="utf-8",
)
(FPROJ / "C--dev-quant-research" / "s2.jsonl").write_text(
    shell_row("q1", "Bash", "uv run python model.py") + "\n", encoding="utf-8"
)
before = digest()
rc, out = run("commands", "--projects", str(FPROJ), "--since", "1970-01-02")
shown = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
check(
    "U13 commands: forms counted once per block, out-of-scope folders skipped, and no "
    "argument, path or prompt text in the output",
    (
        rc,
        "     2  Bash: uv run pytest" in shown,
        "     1  Bash: node <script>" in shown,
        "     1  PowerShell: pnpm exec" in shown,
        "uv run python" in shown,
        any(word in shown for word in ("secret_case", "abc123", "gen.js", "model.py")),
        "1 out-of-scope folders skipped" in shown,
    ),
    (0, True, True, True, False, False, True),
)
check("U13 the commands run wrote and touched nothing", digest() == before, True)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
