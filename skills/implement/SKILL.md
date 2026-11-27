---
name: implement
description: Execute an approved plan autonomously to the end - for each step write/adjust tests first, implement minimally, run the quality gate, self-review, tick the plan checkbox and commit - stopping only on defined exceptions; then review, close the plan and record lessons. Use after /plan is approved, when the user says to implement/build an agreed design, or to resume ("continue") an in-progress plan. Not when there is no approved plan file (run /plan first) or for a one-file change with an obvious approach.
argument-hint: [plan file or "continue"] [step number]
---

# Implement

Arguments: `$ARGUMENTS`

## 0. Load the plan
- The plan file is the source of truth: the one given (`docs/plans/...md`), or for "continue"/no argument the newest `docs/plans/*.md` with `status: in-progress`. If there is none and the change is non-trivial, run `/plan` first.
- Resume from the first unchecked step (`- [ ]`). After a compaction or a new session, re-read the plan file and `git log --oneline -10` before doing anything; do not trust memory of earlier steps.
- Check that the working tree is clean (`git status --porcelain`); if it is not and the changes are not explained by the plan's progress, ask what to do with them.
- Read the task's last recorded verification state: `python -I ~/.claude/hooks/ledger.py show --task <the plan's task id>` (in Git Bash: PowerShell does not expand `~`; run from the project; exit code 1 means no entry among the recent records, 3 that the ledger is not intact). It is a note on where the last session stopped, nothing more: when it disagrees with the checkboxes (all steps ticked but `BLOCKED` or `UNKNOWN`, or a warning that the ledger is not intact), say so in the report and start with the quality gate. Never skip a gate, a review or the plan approval because the ledger says `LOCALLY_VERIFIED` or `CI_VERIFIED`: the hooks judge by content, not by the ledger.

## 1. Run every step autonomously
Proceed through all remaining steps without asking between them. For each step:
1. **Tests first.** Add or adjust tests that express the step's acceptance criteria (happy path, boundaries, error paths). Run them and confirm the new ones fail for the expected reason (or are already-green characterization tests for a refactor step).
2. **Implement minimally** until they pass. Follow the language rules and design principles (boundary validation, pure core, explicit errors, structured logs). No scope creep; note extra ideas for the end. A bug that shows up while implementing (wrong output, a crash, a regression the step did not mean to cause) goes through Skill(debug): a failing test that reproduces it first, then the fix.
3. **Quality gate:** the project's declared `gate` (its CLAUDE.md `## Verification`), run by the main session itself: verbatim, from the project root, in the foreground, as the whole command line (no pipe, `;`, redirect-and-echo or background: the hook records those as unknown). Inside a multi-step section, the tests of the modules the step touches are enough between gates; the plan says where the full gate runs. The `test-runner` agent is for diagnosis only (a long failing suite): its report is not evidence and never replaces the gate.
4. **Self-review the diff** (`git diff`): debug prints, commented-out code, leftover TODOs, unrelated changes, missing error handling/timeouts, secrets, names. Fix before committing.
5. **Tick and commit.** Change the step's `- [ ]` to `- [x]` in the plan file, append one line to `## 진행 기록` (`YYYY-MM-DD 단계 N: <what> — <deviation or "계획대로">`), and commit code + plan together as one Conventional Commit by invoking the commit skill (Skill(commit), with the step's paths in its arguments), not with a direct `git commit`. Do not push unless the approved plan's text quotes the user's decision that this task's pushes may go ahead (global rules, section 6); then follow CI to its end without waiting for it: start `gh run watch <id> --exit-status` in the background and go on with the next step. When its completion notice arrives, record the verdict with a foreground `gh run view <id> --json status,conclusion`. On a red CI, stop the current step and fix CI first. The last push's CI has ended green before the plan is closed.
6. **One-line progress report:** `단계 N/M 완료 — <what>, 테스트 <x passed>, 커밋 <sha>`.

### Stop conditions (the only reasons to pause and ask)
Stop, leave the step unchecked, write the reason under `## 진행 기록`, and report to the user when:
1. **The same gate fails twice** for the same step (global rule 1.3): state what you learned and the options.
2. **Reality diverges from the plan**: unexpected coupling, a missing API, a step much larger than planned, an assumption in the plan turns out false, or a file outside `## 변경 범위` must change (update the plan and get it re-approved; the discipline hook counts out-of-scope files against the 5-file guard).
3. **A design decision the plan does not cover** is needed (new dependency, schema/API shape, behavior choice the user would care about).
4. **An outward-facing action** is next: `git push` (unless the approved plan quotes the push decision, see step 5), deploy, publish, sending anything to an external service, or anything destructive/irreversible.
Anything else (a lint fix, a missing import, a test fixture) is handled without asking. Never improvise a different design silently.

## 2. Finish
- Run the full quality gate once more from a clean state (the main session, in the foreground, as above).
- Run the `code-reviewer` agent on the whole range (`git diff <base>..HEAD`); also `security-auditor` before the plan is closed if auth, secrets, dependencies or CI changed, or the change adds or alters network calls, the handling of external input (HTTP, files, CLI args, data from other processes), or scheduled jobs and scripts that start subprocesses. Fix CONFIRMED findings (each as its own commit), and list the PLAUSIBLE ones for the user.
- Update docs touched by the change (README, CLAUDE.md commands, `.env.example`).
- Set the plan frontmatter to `status: done` and commit it.
- **Learning loop** (global CLAUDE.md 2a): add reusable lessons from this run (failed commands and their working form, environment traps, unwritten conventions) as one line each under `## Gotchas` in the project CLAUDE.md; propose promotion to global rules if a lesson applies across projects.
- Final report in the global reporting format, including the commit list, the Gotchas lines added, and any deviations from the plan.
