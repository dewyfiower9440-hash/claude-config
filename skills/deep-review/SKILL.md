---
name: deep-review
description: Deep multi-agent review of a change set - runs code-reviewer and security-auditor in parallel and then the project's gate in the main session, cross-verifies every finding against the code, deduplicates, ranks by severity and proposes concrete fixes. Use when the user asks for a thorough/deep review, before merging or deploying significant work, or after a large /implement. Not for a small diff (use the code-reviewer agent alone) and not a way to run the gate (the main session runs it).
argument-hint: [git range | path | "staged"] [--fix]
---

# Deep review

Target: `$ARGUMENTS` (default: uncommitted changes plus commits not yet pushed: `git diff @{upstream}...HEAD` + working tree; if there is no upstream, `HEAD~1..HEAD` + the working tree).

## 1. Scope
- Resolve the exact range and list the files with `git diff --stat`. Read the project CLAUDE.md and the plan (if any) so the reviewers know the intent.
- Write a 3-5 line "intent summary" of what the change is supposed to do. All agents get it.

## 2. Parallel review (one message, two Agent calls), then the gate
- `code-reviewer`: correctness, edge cases, error handling, concurrency, design/scope, test quality. Include the intent summary and the range.
- `security-auditor`: the range + dependency changes + CI/workflow changes.
- After both have reported, the main session runs the project's declared `gate` itself: verbatim, from the project root, in the foreground. A subagent's test run is not evidence (global rules 1.6); use `test-runner` only to diagnose a long failing suite.

## 3. Verify every finding (do not forward unverified claims)
For each finding, open the code yourself and decide:
- **CONFIRMED**: you can show the failure (a failing test, a command output, or an unambiguous code path with concrete inputs).
- **PLAUSIBLE**: a reasonable risk that you could not demonstrate.
- **REJECTED**: wrong or not applicable. Drop it, with a one-line reason in the appendix.
Merge duplicates across agents. For CONFIRMED correctness bugs, write the failing test that proves it when that is cheap.

## 4. Report (Korean)
```
판정: ship | fix first | rework
품질 게이트: lint ✓/✗ · types ✓/✗ · tests x/y · coverage(changed) n%
발견 사항 (심각도순):
  [critical|high|medium|low] [CONFIRMED|PLAUSIBLE] file:line — 문제 — 실패 시나리오 — 수정안
테스트 공백: behaviors in the change that no test covers
부록: 기각된 항목 (한 줄 사유)
```

## 5. Fix (only with `--fix` or after the user says so)
- Fix CONFIRMED findings in severity order, test first for bugs, one commit per fix, quality gate after each. Re-run `code-reviewer` on the fix commits. PLAUSIBLE items go back to the user as a list.
