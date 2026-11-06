---
name: plan
description: Design and plan a non-trivial change before writing code - clarify requirements, investigate the codebase, compare 2-3 approaches, and produce an approved step-by-step plan with verification criteria. Use for features, multi-file changes (3+ files), new modules/dependencies/schemas/APIs, migrations, or whenever there is a real design choice. Required before editing more than 5 files in a turn.
argument-hint: <what to build or change>
---

# Plan

Request: `$ARGUMENTS`

No production edits in this skill. Read, search and run read-only commands only.

## 1. Requirements
- Restate the goal in 1-2 sentences, plus explicit **non-goals**.
- List the acceptance criteria as testable statements ("given X, when Y, then Z").
- If a requirement is ambiguous **and** the answer changes the design, ask with AskUserQuestion, one question at a time with a recommended option. Otherwise pick the conventional default and record it under Assumptions.

## 2. Investigate (evidence, not guesses)
- Find the entry points, the modules involved, existing patterns/helpers to reuse, the tests that cover the area, and the conventions in the project CLAUDE.md.
- For unfamiliar libraries/APIs, use the `researcher` agent (Context7) and do not rely on memory.
- Record findings with `file:line` references.

## 3. Options
- 2-3 genuinely different approaches (skip this if only one is reasonable, and say why). For each: sketch, files touched, pros, cons, risk, rough size.
- Evaluate against the global design principles: boundary validation, pure core/thin I/O, explicit errors, observability, simplicity.
- Recommend one, with the deciding reason.

## 4. Plan
Always save the plan to `docs/plans/YYYY-MM-DD-<slug>.md` in the project (today's date, short kebab-case slug). The plan file is the source of truth that survives compaction and new sessions; the SessionStart hook injects the newest `in-progress` plan automatically. Shape (Korean prose, identifiers in English):
```markdown
---
status: draft
created: YYYY-MM-DD
---

# <title>

task: <slug, same as the file name's slug>

## 목표
<1-3 sentences>. 비목표: <...>

## 변경 범위
- <glob, absolute or relative to the project root, e.g. src/billing/**>
- <one per line; files outside it stay subject to the 5-file guard>

## 가정
## 선택한 접근
<approach + deciding reason>

## 단계
- [ ] 1. <what> — 파일: <files> — 테스트: <tests> — 검증: <command + expected result>
- [ ] 2. ...

## 리스크와 대응
<incl. rollback for data/schema changes>

## 영향 범위
<public API / config / migrations / docs to update>

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
```
- Each step is independently verifiable and committable (ideally < ~200 changed lines); one checkbox per step, numbered.
- Order the steps so the system stays working after each one (expand, then migrate, then contract for breaking changes).
- `## 목표` and the checkbox format are parsed by `~/.claude/hooks/session_context.py`; `task:` and `## 변경 범위` are parsed by `discipline.py`. Keep them. Relative scope globs resolve against the session's working directory at approval: start the session in the project root, or write absolute globs.

## 5. Approval
- Present the plan with **ExitPlanMode** (the user's approval gate). The approval is bound to `task:` + the plan text + `## 변경 범위`: it lifts the >5-file guard only for files inside that scope, and only for the approving prompt.
- On approval, save the approved text **verbatim** as the body of `docs/plans/<file>.md` with frontmatter `status: in-progress` (only one in-progress plan per project; set any older one to `done` or back to `draft` after asking). Later prompts and new sessions re-arm the approval only while that file stays in progress with unchecked steps and its text (ignoring checkboxes and `## 진행 기록`) is unchanged; changing steps or scope needs a new approval. Then continue with `/implement docs/plans/<file>.md`.
