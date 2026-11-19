---
name: commit
description: Self-review the working changes against a checklist, split them into logical Conventional Commits on main, and (after confirmation) push and watch CI, diagnosing failures. Use when the user says commit, save my work, push, or asks for a commit message, and also whenever Claude itself is about to run git commit (an /implement step included). Can also draft a PR when asked. Not for reviewing a change (/deep-review), for a release check (/predeploy), or for releasing this hooks repository (/release-hooks).
argument-hint: [push] [pr] [hint about the change]
---

# Commit

Arguments: `$ARGUMENTS`. Workflow: solo, directly on `main`.

## 1. Inspect
`git status`, `git diff`, `git diff --cached`, `git log --oneline -10` (to match the existing style).

## 2. Self-review checklist (read the diff, every hunk)
Stop and fix (or report) if any of these is true:
- [ ] Debug leftovers: `print(`/`console.log(`/`Write-Host` added in non-CLI code, `breakpoint()`, `debugger`, `pdb`, temporary logging.
- [ ] Commented-out code blocks, or new `TODO`/`FIXME`/`XXX` without an issue reference.
- [ ] Out-of-scope changes: files/hunks unrelated to the task, drive-by reformatting of untouched code, unexplained lockfile churn.
- [ ] Weakened safety nets: skipped/deleted tests, loosened assertions, new `noqa`/`type: ignore`/`eslint-disable`/`@ts-expect-error` without a reason.
- [ ] Missing pieces: new behavior without tests; new config without a `.env.example` entry; changed commands without a CLAUDE.md/README update.
- [ ] Error handling: swallowed exceptions, external calls without timeouts, secrets in messages/logs.
- [ ] Secrets / local-only files: `.env`, keys, `*.db`, large binaries, absolute local paths.

## 3. Gate
Run the project's fast checks (lint + format check + types + unit tests) unless they ran after the last edit in this session. Red → do not commit; report. (Only commit red code if the user explicitly says so, and say so in the message body.)

## 4. Commit
- Group into logical commits (one concern each). Stage explicit paths (`git add <files>`; `git add -p`-style splitting by staging whole files or by applying edits stepwise), never a blind `git add -A`.
- Message (English, Conventional Commits):
  ```
  <type>(<scope>)?: <imperative summary, <= 72 chars>

  <why; what changed if not obvious; wrapped at 72>
  ```
  Types: feat, fix, refactor, perf, test, docs, build, ci, chore. Breaking: `!` plus a `BREAKING CHANGE:` footer. End the message with the attribution lines from the current system reminder, if any.
- Commit via `git commit -F <file>` (write the message with the Write tool to a file outside the repository) so the newlines survive; never a shell heredoc (global rules, section 9). Show `git log --oneline -n <k>`.

## 5. Push + CI (only with `push` in the arguments, or after the user confirms)
- `git push origin main`, then `gh run list -L 1 --json databaseId,status,conclusion,workflowName` and `gh run watch <id> --exit-status`.
- **CI failed** → diagnose before touching anything: `gh run view <id> --log-failed`, then classify:
  - real failure (test/lint/types): reproduce locally with the same command, fix test-first, and make a new commit (never amend a pushed commit or force-push);
  - environment/CI-only (OS differences, path separators, missing secrets, Node/Python version, lockfile out of date, an action version): fix the workflow or config and explain;
  - flaky: rerun once with `gh run rerun <id> --failed`; if it passes, report it as flaky and propose a fix.
  Report the diagnosis in the global format.

## 6. PR (only if `pr` was given)
Create a branch, push it, and run `gh pr create` with a body of Summary / Changes / Test plan, plus the PR attribution line from the system reminder.
