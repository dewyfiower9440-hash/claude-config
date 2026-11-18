"""CI verification for discipline.py.

After a `git push`, the pushed commit counts as CI Verified only when, for that exact
repository / branch / head SHA, every push-triggered workflow in the commit has a
completed `push` run with conclusion `success` and every job of that run succeeded.
The judgment (`ci_verdict`) is pure; git and gh access live in the adapters below it.
Any adapter failure yields status "unknown", never success.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, NotRequired, TypedDict

GH_TIMEOUT_S = 15
LOG_TIMEOUT_S = 10
LOG_TAIL_CHARS = 20_000  # the end of a job log is where it failed
STEP_CHARS = 80
GIT_TIMEOUT_S = 5


class PushInfo(TypedDict):
    repo: str  # owner/name
    branch: str
    sha: str
    toplevel: str
    uncommitted: list[
        str
    ]  # absolute paths with changes not contained in the pushed commit


class RunResult(TypedDict):
    id: int
    path: str
    status: str
    conclusion: str


class CiEvidence(TypedDict):
    status: str  # "verified" | "failed" | "pending" | "unknown"
    repo: str
    branch: str
    sha: str
    runs: list[RunResult]
    why: str
    kind: NotRequired[
        str
    ]  # of a failed run: a hint (classify_failure), never the verdict


# ---------------------------------------------------------------- pure
def triggers_push(workflow_yaml: str) -> bool:
    """Whether a workflow's `on:` includes push (inline list/scalar or block mapping/list)."""
    lines = workflow_yaml.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"""^["']?on["']?\s*:\s*(.*)$""", line)
        if not m:
            continue
        inline = m.group(1).split("#")[0].strip()
        if inline:
            return bool(re.search(r"\bpush\b", inline))
        for nxt in lines[i + 1 :]:
            if nxt.strip() and not nxt[0].isspace():
                break  # next top-level key
            if re.match(r"^\s+(-\s*)?push\b", nxt):
                return True
        return False
    return False


def ci_verdict(
    push: PushInfo,
    required: list[str],
    runs: list[dict[str, Any]],
    jobs: dict[int, list[dict[str, Any]] | None],
) -> CiEvidence:
    results: list[RunResult] = []
    if not required:
        return _evidence(
            push, "unknown", results, "no push-triggered workflow in the pushed commit"
        )
    worst = "verified"
    why: list[str] = []
    for path in required:
        candidates = [
            r
            for r in runs
            if r.get("path") == path
            and r.get("event") == "push"
            and r.get("head_sha") == push["sha"]
            and r.get("head_branch") == push["branch"]
            and str((r.get("repository") or {}).get("full_name", "")).lower()
            == push["repo"].lower()
        ]
        if not candidates:
            worst = _worse(worst, "pending")
            why.append(f"{path}: no push run for this commit yet")
            continue
        run = max(
            candidates,
            key=lambda r: (int(r.get("run_attempt") or 1), int(r.get("id") or 0)),
        )
        rid = int(run.get("id") or 0)
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        results.append(
            {"id": rid, "path": path, "status": status, "conclusion": conclusion}
        )
        if status != "completed":
            worst = _worse(worst, "pending")
            why.append(f"{path}: run {rid} {status}")
        elif conclusion != "success":
            worst = _worse(worst, "failed")
            why.append(f"{path}: run {rid} {conclusion}")
        else:
            run_jobs = jobs.get(rid)
            if run_jobs is None:
                worst = _worse(worst, "unknown")
                why.append(f"{path}: run {rid} jobs unavailable")
            elif not run_jobs:
                worst = _worse(worst, "unknown")
                why.append(f"{path}: run {rid} has no jobs")
            else:
                for job in run_jobs:
                    jc = str(job.get("conclusion") or "")
                    if jc == "success":
                        continue
                    worst = _worse(
                        worst,
                        "failed"
                        if jc in ("failure", "cancelled", "timed_out")
                        else "unknown",
                    )
                    why.append(
                        f"{path}: run {rid} job {job.get('name')} {jc or 'unfinished'}"
                    )
    return _evidence(push, worst, results, "; ".join(why))


def _evidence(
    push: PushInfo, status: str, runs: list[RunResult], why: str
) -> CiEvidence:
    return {
        "status": status,
        "repo": push["repo"],
        "branch": push["branch"],
        "sha": push["sha"],
        "runs": runs,
        "why": why,
    }


KIND_LABEL = {
    "timeout": "시간 초과",
    "encoding": "인코딩",
    "environment": "환경·설치",
    "lint": "린트·형식",
    "test": "테스트 실패",
    "other": "기타",
}
_TIMEOUT = re.compile(
    r"exceeded the maximum execution time|TimeoutExpired", re.IGNORECASE
)
_ENCODING = re.compile(
    r"Unicode(En|De)codeError|codec can't (en|de)code", re.IGNORECASE
)
_ENVIRONMENT = re.compile(
    r"ModuleNotFoundError|No module named|is not on PATH|command not found"
    r"|is not recognized as (an? |the )(internal|name)",
    re.IGNORECASE,
)
_LINT = re.compile(
    r"FAIL ruff|Would reformat|would be reformatted|^[A-Z]{1,4}\d{3,4} \[\*?\]"
    r"|^Found \d+ errors?\b",
    re.MULTILINE,
)
_TEST = re.compile(
    r"^FAIL |FAILURES: [1-9]|AssertionError|\b[1-9]\d* failed\b", re.MULTILINE
)
_SETUP_STEP = re.compile(r"\b(install|set ?up|checkout|cache|restore)\b", re.IGNORECASE)


def classify_failure(step: object, conclusion: object, log_tail: object) -> str:
    """What kind of failure a CI job ended with, from the failed step's name and the end
    of its log: timeout, encoding, environment, lint, test or other (first match in that
    order; a failing setup step without any clue in the log is environment). A hint for
    the first look, shaped by the failures this repository's CI has had; it never decides
    whether the run counts."""
    log = log_tail if isinstance(log_tail, str) else ""
    if conclusion == "timed_out" or _TIMEOUT.search(log):
        return "timeout"
    for kind, pattern in (
        ("encoding", _ENCODING),
        ("environment", _ENVIRONMENT),
        ("lint", _LINT),
        ("test", _TEST),
    ):
        if pattern.search(log):
            return kind
    if isinstance(step, str) and _SETUP_STEP.search(step):
        return "environment"
    return "other"


def _worse(a: str, b: str) -> str:
    order = ["verified", "pending", "unknown", "failed"]
    return a if order.index(a) >= order.index(b) else b


def github_repo(remote_url: str) -> str | None:
    m = re.search(
        r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url.strip()
    )
    return f"{m.group(1)}/{m.group(2)}" if m else None


# ---------------------------------------------------------------- adapters (git, gh)
# devenv-final 26: one CI lookup makes several gh calls of up to GH_TIMEOUT_S each. They
# share what is left of the hook's time, so the hook answers ("unknown") instead of being
# ended by its timeout in the middle of them.
FETCH_BUDGET_S = 40.0
_deadline: float | None = None


def _run(cmd: list[str], cwd: str | None, timeout: float) -> str | None:
    if _deadline is not None:
        timeout = min(timeout, _deadline - time.monotonic())
        if timeout <= 0:
            return None  # no time left: no answer, never a late one
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):  # tool missing or hung: no evidence
        return None
    return p.stdout if p.returncode == 0 else None


def _git(cwd: str, *args: str) -> str | None:
    out = _run(["git", "-c", "core.quotepath=false", *args], cwd, GIT_TIMEOUT_S)
    return None if out is None else out.strip()


def push_info(cwd: str) -> PushInfo | None:
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    sha = _git(cwd, "rev-parse", "HEAD")
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    url = _git(cwd, "remote", "get-url", "origin")
    status = _git(cwd, "status", "--porcelain")
    if not (toplevel and sha and branch and url) or status is None:
        return None
    repo = github_repo(url)
    if not repo:
        return None
    top = os.path.normpath(toplevel)
    dirty = [
        os.path.normpath(os.path.join(top, line[3:].split(" -> ")[-1].strip('"')))
        for line in status.splitlines()
        if len(line) > 3
    ]
    return {
        "repo": repo,
        "branch": branch,
        "sha": sha,
        "toplevel": top,
        "uncommitted": dirty,
    }


def required_workflows(toplevel: str, sha: str) -> list[str] | None:
    """The workflow files of commit `sha` that a push triggers; None when git could not
    list or read them (a workflow that cannot be read is not "not required": with none
    required, any green run would verify the push)."""
    listing = _git(toplevel, "ls-tree", "--name-only", sha, ".github/workflows/")
    if listing is None:
        return None
    paths = [p for p in listing.splitlines() if p.endswith((".yml", ".yaml"))]
    required: list[str] = []
    for p in paths:
        text = _git(toplevel, "show", f"{sha}:{p}")
        if text is None:
            return None
        if triggers_push(text):
            required.append(p)
    return required


def _gh_json(path: str, **params: str) -> dict[str, Any] | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    # Query parameters go through -f (GET) rather than "?a=b&c=d": no shell ever sees an `&`.
    fields = [arg for k, v in params.items() for arg in ("-f", f"{k}={v}")]
    out = _run([gh, "api", path, "-X", "GET", *fields], None, GH_TIMEOUT_S)
    if out is None:
        return None
    try:
        data = json.loads(out)
    except ValueError:  # non-JSON output: treat as unavailable
        return None
    return data if isinstance(data, dict) else None


def _failed_log(repo: str, run_id: int) -> str | None:
    """The log of a run's failed steps. Through `gh run view --log-failed`: the job-log
    API answer holds terminal escape sequences, which `gh api` refuses to print."""
    gh = shutil.which("gh")
    if not gh:
        return None
    return _run(
        [gh, "run", "view", str(run_id), "--repo", repo, "--log-failed"],
        None,
        LOG_TIMEOUT_S,
    )


_LOG_PREFIX = re.compile(
    r"^(?:[^\t\n]*\t){0,2}\d{4}-\d\d-\d\dT[\d:.]+Z ?", re.MULTILINE
)


def _plain_lines(log: str) -> str:
    """Log lines without what gh puts in front of them (job, step, timestamp)."""
    return _LOG_PREFIX.sub("", log)


def _not_the_failure(item: Any) -> bool:
    return item.get("conclusion") not in ("failure", "timed_out")


def failure_note(repo: str, run_id: int) -> tuple[str, str]:
    """(kind, name of the failed step) of a failed run. The log is used for the
    classification only: nothing of it is returned, stored or logged. Whatever cannot be
    fetched is done without (kind "other" at worst)."""
    data = _gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    candidates = [
        j
        for j in (jobs if isinstance(jobs, list) else [])
        if isinstance(j, dict) and j.get("conclusion") not in ("success", "skipped")
    ]
    # the job (and step) that failed or timed out, before one that was merely cancelled
    for job in sorted(candidates, key=_not_the_failure):
        steps = [
            s
            for s in (job.get("steps") if isinstance(job.get("steps"), list) else [])
            if isinstance(s, dict)
            and s.get("conclusion") not in ("success", "skipped", None)
        ]
        failed = min(steps, key=_not_the_failure, default={})
        step = " ".join(str(failed.get("name") or "").split())[:STEP_CHARS]
        log = _failed_log(repo, run_id)
        tail = _plain_lines(log[-LOG_TAIL_CHARS:]) if isinstance(log, str) else ""
        return classify_failure(
            step, failed.get("conclusion") or job.get("conclusion"), tail
        ), step
    return "other", ""


def fetch_ci(push: PushInfo, budget_s: float = FETCH_BUDGET_S) -> CiEvidence:
    global _deadline
    _deadline = time.monotonic() + budget_s
    try:
        return _fetch_ci(push)
    finally:
        _deadline = None


def _unknown(push: PushInfo, why: str) -> CiEvidence:
    return {
        "status": "unknown",
        "repo": push["repo"],
        "branch": push["branch"],
        "sha": push["sha"],
        "runs": [],
        "why": why,
    }


def _fetch_ci(push: PushInfo) -> CiEvidence:
    required = required_workflows(push["toplevel"], push["sha"])
    if required is None:
        return _unknown(push, "the commit's workflow files could not be read")
    listing = _gh_json(
        f"repos/{push['repo']}/actions/runs", head_sha=push["sha"], per_page="100"
    )
    if listing is None:
        return _unknown(push, "GitHub API unavailable")
    runs = [r for r in listing.get("workflow_runs", []) if isinstance(r, dict)]
    jobs: dict[int, list[dict[str, Any]] | None] = {}
    for r in runs:
        if (
            r.get("path") in required
            and r.get("status") == "completed"
            and r.get("conclusion") == "success"
        ):
            rid = int(r.get("id") or 0)
            data = _gh_json(f"repos/{push['repo']}/actions/runs/{rid}/jobs")
            jobs[rid] = (
                None
                if data is None
                else [j for j in data.get("jobs", []) if isinstance(j, dict)]
            )
    evidence = ci_verdict(push, required, runs, jobs)
    if evidence["status"] == "failed":
        not_passed = [
            r
            for r in evidence["runs"]
            if r["status"] == "completed" and r["conclusion"] != "success"
        ]
        failed_run = min(not_passed, key=_not_the_failure, default=None)
        if (
            failed_run
        ):  # a failure seen at job level only is left without a made-up kind
            kind, step = failure_note(push["repo"], failed_run["id"])
            evidence["kind"] = kind
            where = f" (실패 단계: {step})" if step else ""
            evidence["why"] += f"; 원인 분류: {KIND_LABEL[kind]}{where}"
    return evidence
