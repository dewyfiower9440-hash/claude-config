---
name: debug
description: Disciplined debugging protocol - reproduce, gather evidence, form ranked hypotheses, confirm the root cause, write a failing regression test, fix minimally, verify - with symptom-specific playbooks (crash, wrong output, performance, flaky, environment) and Python/Node/PowerShell debugger recipes. Use for bugs, crashes, exceptions, wrong output, slowness, flaky tests, regressions or "it doesn't work" reports. Not for adding a feature or for a structural clean-up with no wrong behaviour (/plan, /refactor).
argument-hint: <symptom or error>
---

# Debug protocol

Symptom: `$ARGUMENTS`

Do not edit production code until step 5. Give a one-line progress update in Korean after each phase.

## 1. Reproduce
- Find the smallest exact command/input that shows the bug. Record **expected vs actual** and the full error/stack trace.
- Pin the environment: versions (`uv run python -V`, `node -v`, `$PSVersionTable`), lockfile state, config/env vars (names only, never secret values), the OS and shell actually used, the cwd.
- Cannot reproduce? Collect facts and say so. Do not guess-fix a bug you cannot see.

## 2. Classify and use the playbook

**Crash / exception**
- Read the *whole* traceback bottom-up; find the first frame in our code. Inspect the actual values there: debugger, or temporary structured logging.
- Check the error-path assumptions: `None`/`undefined`, an empty collection, encoding (cp949 vs utf-8 on Windows), path separators, permissions, a missing env var.

**Wrong output**
- Find the earliest point where the data is wrong: bisect the pipeline by logging/asserting intermediate values (input → parse → transform → output).
- Compare with a known-good input/version. `git bisect run <test-cmd>` when there is a good commit.

**Performance / hang**
- Measure first, don't guess: Python `uv run python -m cProfile -o out.prof ...` / `py-spy dump --pid` (hang) / `py-spy record`; Node `node --cpu-prof`, `--inspect`; PowerShell `Measure-Command`, `Trace-Command`.
- Look for N+1 queries, sync I/O in async code, missing timeouts (a hang usually means a missing timeout), lock contention, unbounded retries, accidentally quadratic loops.

**Flaky / intermittent**
- Run it repeatedly to get a failure rate: `uv run pytest -x --count=50` (pytest-repeat) / `pnpm exec vitest run --repeat 50` / a loop in PowerShell. Randomize the order (`-p random_order`) to expose shared state.
- Usual causes: time/timezone, ordering of sets/dicts/async completion, shared global state, real network/filesystem, test pollution, race conditions, port reuse.

**Environment / "works on my machine"**
- Diff the environments: lockfile vs installed (`uv pip list`, `pnpm ls`), PATH and which binary actually runs (`Get-Command x -All`, `where.exe`), env vars, cwd, BOM/encoding, CRLF/LF, cached build output (`dist/`, `__pycache__`, `.venv` built for another Python).

## 3. Hypotheses
- List 2-4 candidate root causes ranked by likelihood, each with the cheapest test that would confirm or kill it. Run the most discriminating cheap test first.
- Distinguish the root cause from the symptom: keep asking "why" until the answer is a code/design fact. Two wrong hypotheses in a row → re-check your assumptions (right file? right env? stale build? am I running what I think?).

## 4. Regression test
- A test at the lowest sensible level that reproduces the bug. Run it and **show it failing for the right reason**.
- If the layer the bug lives in has no test harness (for example a page script in a project whose tests only reach the API): try to reproduce it with a test at the nearest layer that has one. If that cannot show the bug, write the manual reproduction steps and the expected result in the report and state `회귀 테스트 없음` under 미완료·리스크. Adding a test framework is a proposal, not part of the fix.

## 5. Fix
- The minimal change that fixes the root cause, not the symptom. No unrelated refactors (propose them). `Grep` for the same faulty pattern elsewhere and list the sibling sites. Fix them only if they are clearly the same bug and in scope; otherwise propose.

## 6. Verify
- The new test passes; the full suite, lint and types are clean (`test-runner` for long suites); the original reproduction now gives the expected result; temporary logging/breakpoints are removed (`git diff` check).

## 7. Report (global format) + root cause in 1-2 sentences, the regression test name, sibling risks.

## Debugger recipes
- **Python:** `breakpoint()` (pdb) → `uv run pytest -x --pdb` drops into post-mortem; `uv run python -X faulthandler` for hard crashes; `PYTHONASYNCIODEBUG=1` for asyncio issues. Logging: `structlog` with `log.debug("x.state", **locals_subset)`.
- **Node/TS:** `node --inspect-brk` + `chrome://inspect`; `pnpm exec vitest --inspect-brk --no-file-parallelism`; `NODE_DEBUG=http,net` for networking; `--trace-warnings`, `--unhandled-rejections=strict`.
- **PowerShell:** `Set-PSBreakpoint -Script x.ps1 -Line N`, `Set-PSDebug -Trace 1` (or `-Strict`), `$Error[0] | Format-List * -Force`, `$_.ScriptStackTrace` in `catch`, `Trace-Command -Name ParameterBinding -Expression {...} -PSHost`.
