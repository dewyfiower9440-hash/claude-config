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
from typing import Any, TypedDict

GH_TIMEOUT_S = 15
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


def _worse(a: str, b: str) -> str:
    order = ["verified", "pending", "unknown", "failed"]
    return a if order.index(a) >= order.index(b) else b


def github_repo(remote_url: str) -> str | None:
    m = re.search(
        r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url.strip()
    )
    return f"{m.group(1)}/{m.group(2)}" if m else None


# ---------------------------------------------------------------- adapters (git, gh)
def _run(cmd: list[str], cwd: str | None, timeout: int) -> str | None:
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


def required_workflows(toplevel: str, sha: str) -> list[str]:
    listing = _git(toplevel, "ls-tree", "--name-only", sha, ".github/workflows/")
    paths = [p for p in (listing or "").splitlines() if p.endswith((".yml", ".yaml"))]
    return [
        p for p in paths if triggers_push(_git(toplevel, "show", f"{sha}:{p}") or "")
    ]


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


def fetch_ci(push: PushInfo) -> CiEvidence:
    required = required_workflows(push["toplevel"], push["sha"])
    listing = _gh_json(
        f"repos/{push['repo']}/actions/runs", head_sha=push["sha"], per_page="100"
    )
    if listing is None:
        return {
            "status": "unknown",
            "repo": push["repo"],
            "branch": push["branch"],
            "sha": push["sha"],
            "runs": [],
            "why": "GitHub API unavailable",
        }
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
    return ci_verdict(push, required, runs, jobs)
