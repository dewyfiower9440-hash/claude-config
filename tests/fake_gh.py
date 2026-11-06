"""Fake `gh` for discipline CI tests: answers `gh api <path>` from a JSON scenario file.

Scenario file (path in FAKE_GH_SCENARIO): {"runs": [...], "jobs": {"<run id>": [...]}, "fail": false}
Every invocation is appended to FAKE_GH_SCENARIO + ".calls" so tests can assert on them.
"""

from __future__ import annotations

import json
import os
import re
import sys

scenario_path = os.environ["FAKE_GH_SCENARIO"]
with open(scenario_path + ".calls", "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
with open(scenario_path, encoding="utf-8") as f:
    scenario = json.load(f)

if scenario.get("fail"):
    print("HTTP 502: Bad Gateway", file=sys.stderr)
    sys.exit(1)

args = sys.argv[1:]
if args[:1] != ["api"] or len(args) < 2:
    print(json.dumps({}))
    sys.exit(0)

path = args[1]
jobs = re.search(r"/actions/runs/(\d+)/jobs", path)
if jobs:
    print(json.dumps({"jobs": scenario.get("jobs", {}).get(jobs.group(1), [])}))
elif "/actions/runs" in path:
    # Like the real API: filter by the -f head_sha=... query parameter.
    params = dict(
        args[i + 1].split("=", 1) for i in range(len(args) - 1) if args[i] == "-f"
    )
    sha = params.get("head_sha")
    runs = [r for r in scenario.get("runs", []) if not sha or r.get("head_sha") == sha]
    print(json.dumps({"total_count": len(runs), "workflow_runs": runs}))
else:
    print(json.dumps({}))
