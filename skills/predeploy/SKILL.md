---
name: predeploy
description: Pre-deploy verification gate. Diffs what will be deployed against what is live (or the last deployed tag), runs the full test/lint/type suite, a security audit and a code review, and gives a GO / NO-GO verdict. Use before any deploy, release, publish or copying dev files over live files. Not for an ordinary commit or push to main (/commit), nor a substitute for /deep-review during development.
argument-hint: [live path or deployed ref] [dev path or ref]
---

# Pre-deploy gate

Arguments: `$ARGUMENTS`. Do NOT deploy anything in this skill. It only produces a verdict.

## 1. Establish what is being deployed
- Determine the **live** side: a live directory, a deployed git tag/branch (e.g. `deployed`, latest `v*` tag), or a remote. Use the arguments, else the project CLAUDE.md, else ask (one question).
- Determine the **candidate** side: the working tree / `HEAD` / a dev directory.
- Working tree must be clean (`git status --porcelain`). If not, list the uncommitted files and stop: NO-GO until committed or explicitly excluded.

## 2. Diff live vs candidate
- git refs: `git diff --stat <live>..<candidate>` and `git log --oneline <live>..<candidate>`.
- directories: `git diff --no-index --stat <live_dir> <dev_dir>` (then the full diff for the files that matter).
- **Check the live side for drift**: files that changed on live but not in the candidate (hotfixes, other tools or people editing live). Every drifted file is a blocker until it is merged back or knowingly discarded by the user.
- Every changed file must map to an intended change. Flag anything unexplained.

## 3. Run the gates (in parallel where possible)
- `test-runner` agent: full test, lint and type check suite. Any failure means NO-GO.
- `security-auditor` agent: on the diff + dependencies. Any critical/high finding means NO-GO.
- `code-reviewer` agent: on `<live>..<candidate>`. Any CONFIRMED high/critical bug means NO-GO.
- Config/migrations: list any DB migrations, env var changes (compare `.env.example` with the previous version), and new services the deploy needs.

## 4. Verdict (Korean)
```
판정: GO | NO-GO
변경 요약: N files, M commits (one line each for the important ones)
드리프트: none | list
게이트: tests ✓/✗, lint ✓/✗, types ✓/✗, security ✓/✗, review ✓/✗
차단 사유: ...
배포 전 필요 작업: migrations / env / rollback plan
롤백 방법: how to restore the live version (tag or backup path)
```
GO only when every gate passed and there is no unexplained drift. Never soften a failure.
