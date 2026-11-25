# Skill registry

Single source of truth for the skills in this environment. Update it whenever a skill is added, changed or removed.

## Installed (user scope: `~/.claude/skills/<name>/SKILL.md`)

| Skill | Purpose | Source | Added |
|---|---|---|---|
| `/plan` | Requirements → investigation → options → step plan saved to `docs/plans/YYYY-MM-DD-<slug>.md` (frontmatter `status`/`task`, checkboxes) → approval (ExitPlanMode) → `in-progress` | own | 2026-11-05 (rev. 2026-11-18) |
| `/implement` | Run an approved plan file autonomously: test → implement → gate (main session, foreground) → self-review → tick + commit per step; stops on 4 defined exceptions; review, `status: done`, Gotchas learning loop | own | 2026-11-05 (rev. 2026-11-18) |
| `/refactor` | Behavior-preserving refactor: characterization tests, small green commits | own | 2026-11-05 |
| `/deep-review` | Parallel code-reviewer + security-auditor, then the gate in the main session; verified and ranked findings | own | 2026-11-05 (rev. 2026-11-18) |
| `/release-hooks` | This repository only: work tree → gate → hook latency comparison → `merge --ff-only` into `~/.claude` → push → CI | own | 2026-11-18 |
| `/debug` | Reproduce → playbook per symptom → hypotheses → failing test → minimal fix → verify | own | 2026-11-05 |
| `/commit` | Self-review checklist, Conventional Commits on main, push + CI diagnosis | own | 2026-11-05 |
| `/predeploy` | Live-vs-candidate diff, drift check, all gates → GO/NO-GO | own | 2026-11-05 |
| `/new-project` | Scaffold Python/TS/PowerShell project with layered structure, tooling, CI | own | 2026-11-05 |
| `/distill` | What a session learned → knowledge notes in the Obsidian vault (`C:\dev\vault\notes\knowledge`): problem → cause → patch → verification, decisions with the options dropped; appends when the problem came back; `vault-sync check` and the vault's gate before the report. Only when the user runs it | own | 2026-11-25 |

Every description ends with when not to use the skill (2026-11-18): a skill that fires on the wrong request costs more than one that is missed.

Related hooks (`~/.claude/hooks/`): `session_context.py` (SessionStart: git state + in-progress plan, compact resume), `discipline.py` (plan gate, test-weakening ask, Stop verification incl. failed runs; runs the formatter of `format_lint.py` inside its post step), `handoff.py` (SessionEnd), `notify.py` (toast, skipped while discipline blocks), `guard.py`.

Built-in or plugin skills used as-is: `code-review`, `simplify`, `security-review`, `skill-creator` (anthropic-skills), `claude-api`, `init`.

Account-synced skills: `skills/synced/` is written by claude.ai (anthropic-skills such as docx, pdf, pptx, xlsx, docs, deep-research, browser/computer-use guides). It is gitignored and managed outside this repository; do not edit it here.

Usage evidence: in transcripts 2026-07-22..2026-11-07 (34 sessions) none of the own skills had been invoked through the Skill tool. In the final-check session of 2026-11-16 (a task that named no tool) `new-project`, `plan`, `implement` and `debug` were invoked unprompted (`docs/plans/archive/2026-11-16-devenv-p4.md`, progress record). In a 5-hour project session of 2026-11-19 (596 tool calls, 87 files written, 3 commits; `C:\dev\work\analysis-2026-11-19-session-2ea67f50.md`) there were 0 skill calls, 0 subagent calls and 2 MCP calls: v1.1 (`docs/plans/2026-11-19-devenv-v1-1.md`) records Skill and MCP calls in the hook state and asks for them at the moment of the act (a commit, a large output, a dependency added, a plan written by hand). `/skill-doctor` shows skills never invoked. Judge additions and pruning from real project use.

## Acquisition policy (what Claude does when a skill is missing)

1. **Notice.** A workflow has been done by hand 2+ times, or a task needs a capability no listed skill covers.
2. **Search, official first.**
   - Official Anthropic plugins/marketplaces: `claude plugin marketplace list`, then `/plugin` (browse) or the marketplace manifest in `~/.claude/plugins/marketplaces/`.
   - Otherwise draft our own skill with the `anthropic-skills:skill-creator` workflow.
   - Third-party/community skills only when nothing official fits.
3. **Review before proposing (third-party).** Read every file of the skill: SKILL.md, scripts, hooks, MCP configs. Flag any shell execution, network calls, writes outside the project, credential access, `allowed-tools` breadth, or obfuscated content. No review → no proposal.
4. **Propose** with AskUserQuestion: name, what it does, source (+ URL/commit), review result, risk. Recommended option first.
5. **Install only after approval**: own skills in `~/.claude/skills/<name>/`; plugins via `claude plugin install <name>@<marketplace>` (pin a version/commit when possible).
6. **Register** here (table row + source + date), and mention it in the turn's report.
7. **Prune.** Skills unused for a long time or superseded: propose removal; delete only on approval, then update this file.

## Candidates (proposed, not installed)

_(none yet)_
