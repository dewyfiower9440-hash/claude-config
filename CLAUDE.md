# Global working rules

## 0. Language & workspace
- Talk to me in Korean. Code, comments, identifiers, commit messages and PR text are in English.
- All development lives under `C:\dev\<project>`. Shared tools live in `C:\dev\tools` (scaffold, db-mcp, github-mcp-server). Windows native, PowerShell 5.1 + Git Bash; Docker only when Linux is required. Do not write outside `C:\dev` unless asked.
- Toolchain: Python = `uv` only; Node = `pnpm` only; PowerShell = PSScriptAnalyzer + Pester 5+. Language details live in `~/.claude/rules/*.md`; each is path-scoped (`paths:` frontmatter) and loads only when a matching file is read or edited.

## 1. How I work (habits that are not optional)
1. **Evidence, not guesses.** Read the code before making claims about it. Every claim about behavior cites `file:line`, a command output, or docs (Context7/official). Do not write APIs, flags or versions from memory when they can be checked; check them. If something is unverified, label it **미확인** explicitly.
2. **Never hide failure or incompleteness.** Do not skip, delete, weaken or `xfail` tests, loosen assertions, add lint/type suppressions, or leave `TODO`s to get green. A hook asks the user before any of these happen. Anything unfinished or failing goes in the report, prominently.
3. **No repeated attempts.** If the same approach fails twice, stop. Re-check the assumptions (right file, right env, right version, cache, build output, am I running what I think?), state what you learned, and change the approach or ask. Never loop on near-identical edits.
4. **Minimal change, stay in scope.** Change only what was asked. Refactors, renames, dependency bumps and "while I'm here" fixes are proposed, not done. Mention adjacent problems you notice; don't fix them silently.
5. **Read before writing.** Match the existing structure, naming and patterns. Search for an existing helper before writing a new one.
6. **Verify before "done".** Run the relevant tests, type check and lint, or the program itself, after the last edit, and show the result. A Stop hook blocks ending a turn with unverified code edits.

## 2. Planning (multi-file or design work)
- Triggers: the change touches **3+ files**, adds a module/dependency/schema/API, or involves a real design choice. Then use `/plan` (or plan mode): goal, findings from the code, 2-3 options with trade-offs, recommended option, step list, risks, verification per step. **Get approval before editing.** A hook stops a turn that edits more than 5 files without an approved plan.
- Plans are saved as `docs/plans/YYYY-MM-DD-<slug>.md` in the project (frontmatter `status: draft|in-progress|done`, steps as `- [ ] N. ...` checkboxes). The plan file, not the conversation, is the source of truth: after a compaction or a new session, re-read it and resume from the first unchecked step. A SessionStart hook injects the in-progress plan and git state.
- After approval, use `/implement`: it runs all steps autonomously (test → implement → verify → check the box → commit) and stops only when the same gate fails twice, reality diverges from the plan, a design decision not covered by the plan is needed, or an outward-facing action (push, deploy, publish) is next.
- Small, clearly scoped changes (1-2 files, obvious approach) proceed directly.

## 2a. Learning loop (project Gotchas)
- When a task teaches something reusable in this project (a command that fails and its working form, an environment trap, an unwritten convention), append it as one line under `## Gotchas` in the project `CLAUDE.md` (create the section if missing): `- <trap> → <what to do> (YYYY-MM-DD)`. List the added lines under 변경 in the report.
- Only facts verified in this task; no duplicates of existing lines or of what the code/README already says.
- If `## Gotchas` exceeds 40 lines, propose a cleanup (merge, delete stale lines) instead of growing it.
- If the same lesson applies to several projects, propose promoting it to `~/.claude/rules/` or this file; do not promote without approval.

## 2b. Context hygiene
- Delegate broad searches and long outputs to subagents (`Explore` for code sweeps, `test-runner` for long suites) so only the conclusion enters the main context.
- Work from the plan file for anything multi-step; keep progress in its checkboxes, not in long chat summaries.
- Keep tool output small: filter (`-q`, `| tail`, `head_limit`), do not re-read files you just edited, and end each step with a one-line status.
- Auto-memory is stored per working directory (`~/.claude/projects/<cwd-slug>/memory/`): a session started in another folder does not see it. Start sessions from the project root.

## 3. Testing policy
- **New behavior ships with tests**: happy path, boundary values, and error paths. Changed behavior updates the tests that describe it.
- **Bugs: test first.** Reproduce with a failing test, show it failing for the right reason, then fix (`/debug`).
- Coverage: aim for >= 80% line coverage on changed code (`pytest --cov`, `vitest --coverage`). Coverage is a floor, not the goal; assert on behavior, not implementation details.
- Mock only at process boundaries (network, clock, randomness, filesystem when needed, external services). Never mock the code under test or pure functions. Prefer fakes/in-memory adapters over mock call assertions.
- Tests are deterministic: no sleeps, no real network, fixed seeds/clocks, isolated temp dirs. Flaky tests are bugs.
- Test names describe behavior: `test_<unit>_<condition>_<expected>` / `it("rejects expired tokens")`.

## 4. Design principles
1. **Validate at the boundary, trust types inside.** Everything from outside (HTTP, CLI args, files, env vars, DB rows, LLM/tool output) is parsed into typed models at the edge (pydantic / zod / `[ValidateScript]`). The core receives only validated types. No `Any`/`any`, no `Optional` without a reason, no stringly-typed state: use enums/literals.
2. **Pure core, thin I/O shell.** Business logic lives in pure functions/classes with no I/O. DB, network, filesystem, clock and env access live in thin adapters behind small interfaces, injected at the composition root (`main`/app factory). The core is unit-tested without mocks; adapters get a few integration tests.
3. **Errors are explicit and meaningful.**
   - Never swallow exceptions (`except: pass`, empty `catch`). Catch only what you can handle; otherwise let it propagate.
   - Define a small domain error hierarchy per project (`AppError` → `NotFoundError`, `ValidationError`, `ExternalServiceError`, ...). Wrap low-level errors at adapter boundaries with context (`raise X(...) from e` / `new X(msg, { cause })`).
   - Messages say what failed, on which input/id, and why; never include secrets.
   - Every network/external call has an explicit timeout. Retries are explicit, bounded, with backoff + jitter, and only for idempotent operations.
4. **Structured logging & observability.**
   - No `print`/`console.log` in library/app code (CLIs may print user-facing output). Use structured logging: Python `structlog` (or stdlib `logging` with JSON), TS `pino`, PowerShell `Write-Verbose`/`Write-Information` + structured objects.
   - Log events, not sentences: `log.info("order.created", order_id=..., duration_ms=...)`. Levels: debug=diagnostics, info=business events, warning=recoverable anomalies, error=failed operation needing attention.
   - Mask secrets/PII via a logging processor/redactor. Never log tokens, passwords, connection strings or full request bodies.
   - Time the important operations (external calls, DB queries, jobs) and log `duration_ms`. Include a correlation/request id where there is a request flow.
5. **Simplicity.** The simplest design that satisfies current requirements. No speculative abstraction, plugin systems or config for hypothetical needs. Three similar lines beat a premature abstraction; extract on the third real use.
6. Configuration is loaded and validated once at startup (pydantic-settings / zod env schema). Fail fast with a clear message on bad config.

## 5. Reporting format (Korean, concise)
At the end of a task, report only:
```
결과: <one line: done / partially done / blocked>
변경: <file:line or file, one line each, what and why>
검증: <commands run + results (pass/fail counts)>
미완료·리스크: <anything unfinished, unverified (미확인), or risky; "없음" if none>
다음 단계: <only if there is a real decision for the user>
```
No preamble, no restating the request, no self-praise, no long explanations unless asked. Progress updates during long tasks are one line each.

## 6. Git
- GitHub solo workflow: commit directly to `main` in small logical Conventional Commits (`/commit`). Ask before `git push`; never force-push. Check CI after pushing.

## 7. Secrets
- Global tokens/DB passwords live in Windows Credential Manager (Python `keyring`, gh keyring). Project config goes in `.env` (gitignored); commit only `.env.example`. Never print, read back or commit secret values; hooks enforce this.

## 8. Skills & subagents
- Skill registry and acquisition policy: `~/.claude/skills/SKILLS.md`. When a task would clearly benefit from a skill that does not exist (a recurring workflow done 2+ times, or a missing capability), **propose** it: search the official Anthropic plugin marketplace first, otherwise draft our own. Third-party skills are read in full and reviewed (scripts, commands, network calls) before proposing. Install only after my approval, then update SKILLS.md.
- Subagents: `code-reviewer` (after non-trivial changes), `test-runner` (long suites), `researcher` (library/API questions; uses Context7), `security-auditor` (before deploys; auth/secrets/deps changes). `/deep-review` runs them together. `/predeploy` gates every deploy.
