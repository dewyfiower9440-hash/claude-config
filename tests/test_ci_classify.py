"""P1-05: a failed CI run is given a kind (lint, test, encoding, environment, timeout,
other) from the failed step and the end of its log. A hint: it never changes the verdict.

    python -I tests/test_ci_classify.py

No network: the GitHub calls of ci_status are replaced in-process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
import ci_status as ci

fails = 0


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


classify = getattr(ci, "classify_failure", lambda step, conclusion, log: None)
GATE_STEP = "Quality gate (same command as the local gate in CLAUDE.md)"

# Failure texts this repository's CI really produced (see CLAUDE.md Gotchas).
ENCODING = (
    "PASS test_discipline_d3: FAILURES: 0 (159s)\n"
    "FAIL test_session_store: UnicodeEncodeError: 'charmap' codec can't encode characters "
    "in position 2-3: character maps to <undefined> (33s)\n"
    'File "C:\\hostedtoolcache\\windows\\Python\\3.13\\Lib\\encodings\\cp1252.py", line 19\n'
    "GATE FAILED\n"
)
LINT = (
    "FAIL ruff check: Found 1 error. (0s)\n"
    "I001 [*] Import block is un-sorted or un-formatted\n"
    " --> tests\\test_scope.py:20:1\n"
    "PASS test_shellparse: FAILURES: 0 (0s)\n"
    "GATE FAILED\n"
)
TEST = (
    "PASS ruff check: All checks passed! (0s)\n"
    "FAIL test_handoff: FAILURES: 2 (6s)\n"
    "FAIL H-T3 the record is found again -> None (want {'branch': 'main'})\n"
    "FAILURES: 2\n"
    "GATE FAILED\n"
)

check(
    "F-T1 the console encoding failure",
    classify(GATE_STEP, "failure", ENCODING),
    "encoding",
)
check("F-T2 the import order failure", classify(GATE_STEP, "failure", LINT), "lint")
check("F-T3 a failing check", classify(GATE_STEP, "failure", TEST), "test")
check(
    "F-T4 a missing module / a missing tool",
    [
        classify(
            GATE_STEP, "failure", "ModuleNotFoundError: No module named 'worktree'\n"
        ),
        classify(GATE_STEP, "failure", "ruff is not on PATH\n"),
        classify(
            "Run tests",
            "failure",
            "'pytest' is not recognized as an internal or external command\n",
        ),
        classify("Run tests", "failure", "bash: uv: command not found\n"),
    ],
    ["environment"] * 4,
)
check(
    "F-T5 a timeout by conclusion / by the runner's message",
    [
        classify(GATE_STEP, "timed_out", TEST),
        classify(
            GATE_STEP,
            "failure",
            "The job running on runner GitHub Actions 3 has exceeded the maximum execution "
            "time of 360 minutes.\n",
        ),
    ],
    ["timeout", "timeout"],
)
check(
    "F-T6 no clue in the log: a setup step is environment, anything else is other",
    [
        classify("Install uv", "failure", "Process completed with exit code 1.\n"),
        classify("Run actions/checkout@v5", "failure", ""),
        classify(GATE_STEP, "failure", "Process completed with exit code 1.\n"),
    ],
    ["environment", "environment", "other"],
)
check(
    "F-T7 nothing to go by: other, and no error",
    [
        classify("", "", ""),
        classify(None, None, None),
        classify(3, ["x"], {"log": 1}),
    ],
    ["other", "other", "other"],
)
check(
    "F-T8 order: an encoding error inside a failing test is encoding; lint beside FAIL lines is lint",
    [
        classify(GATE_STEP, "failure", TEST + ENCODING),
        classify(GATE_STEP, "failure", LINT + "FAIL test_x: FAILURES: 0\n"),
        classify(GATE_STEP, "failure", "3 failed, 10 passed in 2.1s\n"),
        classify(GATE_STEP, "failure", "E   AssertionError: assert 1 == 2\n"),
    ],
    ["encoding", "lint", "test", "test"],
)

# ---------------------------------------------------------------- the adapter
PUSH: Any = {
    "repo": "o/r",
    "branch": "main",
    "sha": "abc123",
    "toplevel": "C:\\nowhere",
    "uncommitted": [],
}
WORKFLOW = ".github/workflows/ci.yml"
SECRET = "ghp_" + "A1b2C3d4" * 4


def run(conclusion: str, status: str = "completed") -> dict:
    return {
        "id": 77,
        "path": WORKFLOW,
        "event": "push",
        "head_sha": "abc123",
        "head_branch": "main",
        "repository": {"full_name": "o/r"},
        "status": status,
        "conclusion": conclusion,
        "run_attempt": 1,
    }


JOBS = {
    "jobs": [
        {
            "id": 9001,
            "name": "hooks",
            "conclusion": "failure",
            "steps": [
                {"name": "Run actions/checkout@v5", "conclusion": "success"},
                {"name": GATE_STEP, "conclusion": "failure"},
                {"name": "Post Run actions/checkout@v5", "conclusion": "success"},
            ],
        }
    ]
}
calls: list[str] = []


def fake_api(listing: dict, jobs: dict | None, log: str | None) -> None:
    calls.clear()

    def gh_json(path: str, **params: str) -> dict | None:
        calls.append(path)
        return jobs if path.endswith("/jobs") else listing

    def failed_log(repo: str, run_id: int) -> str | None:
        calls.append(f"log of run {run_id} in {repo}")
        return log

    ci._gh_json = gh_json
    ci._failed_log = failed_log
    ci.required_workflows = lambda toplevel, sha: [WORKFLOW]


# devenv-final 26: workflow files that cannot be read make the whole answer unknown (they
# used to count as "no workflow is required"), and so does a lookup with no time left.
fake_api({"workflow_runs": [run("success")]}, JOBS, "")
ci.required_workflows = lambda toplevel, sha: None
unreadable = ci.fetch_ci(PUSH)
ci.required_workflows = lambda toplevel, sha: [WORKFLOW]
check(
    "F-T20 the commit's workflow files cannot be read: unknown, whatever the runs say",
    (unreadable.get("status"), "workflow" in str(unreadable.get("why"))),
    ("unknown", True),
)
check(
    "F-T20 the lookups of one check share a time budget; a spent one runs nothing",
    (
        ci.FETCH_BUDGET_S < 60,
        setattr(ci, "_deadline", 0.0),
        ci._run(["git", "--version"], None, 5),
        setattr(ci, "_deadline", None),
        ci._run(["git", "--version"], None, 5) is not None,
    ),
    (True, None, None, None, True),
)

fake_api({"workflow_runs": [run("failure")]}, JOBS, "token " + SECRET + "\n" + ENCODING)
ev = ci.fetch_ci(PUSH)
check(
    "F-T9 a failed run: still failed, with its kind and the failed step in the reason",
    (
        ev.get("status"),
        ev.get("kind"),
        "인코딩" in str(ev.get("why")),
        "Quality gate" in str(ev.get("why")),
        "log of run 77 in o/r" in calls,
    ),
    ("failed", "encoding", True, True, True),
)
check(
    "F-T12 nothing of the log text is kept in the result",
    (SECRET in repr(ev), "charmap" in repr(ev), "hostedtoolcache" in repr(ev)),
    (False, False, False),
)


def as_gh_prints(log: str) -> str:
    """The shape `gh run view --log-failed` gives: job, step and a timestamp before each line."""
    return "".join(
        f"hooks\t{GATE_STEP}\t2026-11-15T03:38:31.4363681Z {line}\n"
        for line in log.splitlines()
    )


prefixed = []
for log in (
    TEST,
    LINT,
    ENCODING,
    "FAIL H-T3 x -> None (want 1)",
    "I001 [*] Import block is un-sorted",
):
    fake_api({"workflow_runs": [run("failure")]}, JOBS, as_gh_prints(log))
    prefixed.append(ci.fetch_ci(PUSH).get("kind"))
check(
    "F-T9 the log as gh prints it (job, step and time before each line) is classified the same",
    prefixed,
    ["test", "lint", "encoding", "test", "lint"],
)

fake_api({"workflow_runs": [run("failure")]}, None, ENCODING)
no_jobs = ci.fetch_ci(PUSH)
fake_api({"workflow_runs": [run("failure")]}, JOBS, None)
no_log = ci.fetch_ci(PUSH)
setup_jobs = {
    "jobs": [
        {
            "id": 9002,
            "name": "hooks",
            "conclusion": "failure",
            "steps": [{"name": "Install uv", "conclusion": "failure"}],
        }
    ]
}
fake_api({"workflow_runs": [run("failure")]}, setup_jobs, None)
setup = ci.fetch_ci(PUSH)
check(
    "F-T10 jobs or log unavailable: the verdict stays, the kind falls back, no error",
    [(e.get("status"), e.get("kind")) for e in (no_jobs, no_log, setup)],
    [("failed", "other"), ("failed", "other"), ("failed", "environment")],
)
ok_jobs = {"jobs": [{"id": 1, "name": "hooks", "conclusion": "success", "steps": []}]}
fake_api({"workflow_runs": [run("success")]}, ok_jobs, ENCODING)
passed = ci.fetch_ci(PUSH)
passed_calls = list(calls)
fake_api({"workflow_runs": [run("", status="in_progress")]}, JOBS, ENCODING)
pending = ci.fetch_ci(PUSH)
check(
    "F-T11 a passed or a pending run: no kind, and no log is fetched",
    (
        passed.get("status"),
        "kind" in passed,
        any(p.startswith("log of") for p in passed_calls),
        pending.get("status"),
        "kind" in pending,
        any(p.startswith("log of") for p in calls),
    ),
    ("verified", False, False, "pending", False, False),
)
fake_api(
    {"workflow_runs": [run("failure")]}, {"jobs": "not a list"}, 12345
)  # odd answers
odd = ci.fetch_ci(PUSH)
check(
    "F-T10 answers of the wrong shape: still failed, kind other",
    (odd.get("status"), odd.get("kind")),
    ("failed", "other"),
)

fail_fast = {
    "jobs": [
        {
            "id": 1,
            "name": "matrix (a)",
            "conclusion": "cancelled",
            "steps": [{"name": "Install deps", "conclusion": "cancelled"}],
        },
        {
            "id": 2,
            "name": "matrix (b)",
            "conclusion": "failure",
            "steps": [
                {"name": "Install deps", "conclusion": "success"},
                {"name": "Gate", "conclusion": "failure"},
            ],
        },
    ]
}
fake_api(
    {"workflow_runs": [run("failure")]},
    fail_fast,
    "Process completed with exit code 1.",
)
picked = ci.fetch_ci(PUSH)
check(
    "F-T13 a cancelled job listed before the failed one: the failed job's step is the one named",
    (
        picked.get("kind"),
        "Gate" in str(picked.get("why")),
        "Install deps" in str(picked.get("why")),
    ),
    ("other", True, False),
)
job_level = {"jobs": [{"id": 3, "name": "hooks", "conclusion": "failure", "steps": []}]}
fake_api({"workflow_runs": [run("success")]}, job_level, TEST)
quiet = ci.fetch_ci(PUSH)
check(
    "F-T13 failed at job level only (the run itself says success): no kind is made up",
    (quiet.get("status"), "kind" in quiet, "원인 분류" in str(quiet.get("why"))),
    ("failed", False, False),
)
check(
    "F-T4 PowerShell 5.1's wording of a missing command / a step that merely contains 'install'",
    [
        classify(
            "Run tests",
            "failure",
            "pytest : The term 'pytest' is not recognized as the name of a cmdlet, function\n",
        ),
        classify("Run tests (reinstall check)", "failure", ""),
        classify("Uninstall old tools", "failure", ""),
    ],
    ["environment", "other", "other"],
)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
