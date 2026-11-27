---
name: watch-job
description: Start a long measurement, collection, download, backtest or batch job in the background and let the session wake itself when it ends - a finish condition that covers every terminal state, a background wait or a Monitor, a check-back with /loop for jobs that outlive a monitor - then report the result without being asked. Use when a job will run for more than about 10 minutes, or when the user would otherwise have to ask "is it done?". Not for the project's gate or a reviewer run (the gate runs in the foreground, a subagent notifies by itself), and not for CI after a push (/commit and /implement watch it).
argument-hint: <the command or job, and what "done" means>
---

# Watch a long job

Job: `$ARGUMENTS`

In session c80ac8d8 the user asked "확인해봐" about 14 times while long measurements ran unwatched. A job that runs longer than about 10 minutes (an initial threshold; re-tune it from the session monitor's `long_checks` and `run_minutes` in its report, `C:\dev\work\monitor`) is started so that the session learns of its end by itself.

## 1. Before starting
- Say in one line what the job does, how long it should take (from an earlier run, the input size, or "unknown"), and where its output goes (a log file, an output file, a row count).
- Write down the **finish condition** and the **failure signatures**: the process exits (and its exit code), the output file appears, a final log line. Also: `Traceback`, `Error`, `FAILED`, `Killed`, a non-zero exit, no new log line for a period that is clearly too long. Silence is not success: a watch that only matches the success line stays quiet through a crash.

## 2. Start it and arm the watch (pick one)
- **One notice when it ends** (the usual case): run the job itself with Bash `run_in_background`. Its completion notice arrives on its own, with the exit code. Send its output to a log file outside the repository (the job's own folder when that is no repository, else the scratchpad): an untracked file inside it still changes what the verification evidence and `watch:` globs see.
- **One notice for a condition of a job that is already running**: Bash `run_in_background` with a loop that also ends when the process is gone or the log has stopped moving, so a silent death wakes the session too. Anchor the success match on the job's real last line, not a word that progress lines contain:
  `until grep -qE "^<real last line>|Traceback|Error|Killed" run.log 2>/dev/null || ! kill -0 "$PID" 2>/dev/null || [ $(( $(date +%s) - $(stat -c %Y run.log 2>/dev/null || date +%s) )) -gt 1800 ]; do sleep 30; done`
  (set `PID` first and check it: with an empty `$PID` the `kill -0` test fails at once and the wait ends as if the job had; a log that does not exist yet counts as just written; 1800 s without a new log line is an initial limit). `$PID` is the `$!` of a job started from the same Git Bash: `kill -0` sees MSYS process ids only. For a job started elsewhere (PowerShell, a scheduled task), test the Windows id instead: `tasklist //FI "PID eq $PID" | grep -q " $PID "`.
- **One notice per step or progress line**: the Monitor tool with a command that prints only the lines you would act on (progress and every failure signature, `grep --line-buffered`) and exits at the end. A monitor expires after its `timeout_ms` (at most 30 minutes): re-arm it on expiry while the job runs.
- **A job longer than a monitor or a session turn** (hours): `/loop` without an interval, checking the finish condition each wake-up and stopping the loop once it holds.

## 3. While it runs
Go on with other work that does not depend on the result. Do not poll by hand and do not sleep in the foreground. When the user asks, say it is still running and what the last progress line was.

## 4. When it ends
- Read the exit code and the end of the log (`tail`, `grep`), not the whole output.
- Report in one or two lines: finished or failed, the run time, where the result is, and the numbers it produced. A number that decides something (accept, reject, pass) goes to `result-verifier` for an independent recount before it is reported as a verdict (global rules, section 8).
- A failure is reported with its first error line and the log path. Rerun only after the cause is known (global rules 1.3).
