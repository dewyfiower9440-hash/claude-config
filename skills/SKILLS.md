# Skill registry

Single source of truth for the skills in this environment. Update it whenever a skill is added, changed or removed.

## Installed (user scope: `~/.claude/skills/<name>/SKILL.md`)

| Skill | Purpose | Source | Added |
|---|---|---|---|
| `/plan` | Requirements → investigation → options → step plan saved to `docs/plans/YYYY-MM-DD-<slug>.md` (status/checkboxes) → approval (ExitPlanMode) → `in-progress` | own | 2026-11-05 (rev. 2026-11-06) |
| `/implement` | Run an approved plan file autonomously: test → implement → gate → self-review → tick + commit per step; stops on 4 defined exceptions; review, `status: done`, Gotchas learning loop | own | 2026-11-05 (rev. 2026-11-06) |
| `/refactor` | Behavior-preserving refactor: characterization tests, small green commits | own | 2026-11-05 |
| `/deep-review` | Parallel code-reviewer + security-auditor + test-runner, verified and ranked findings | own | 2026-11-05 |
| `/debug` | Reproduce → playbook per symptom → hypotheses → failing test → minimal fix → verify | own | 2026-11-05 |
| `/commit` | Self-review checklist, Conventional Commits on main, push + CI diagnosis | own | 2026-11-05 |
| `/predeploy` | Live-vs-candidate diff, drift check, all gates → GO/NO-GO | own | 2026-11-05 |
| `/new-project` | Scaffold Python/TS/PowerShell project with layered structure, tooling, CI | own | 2026-11-05 |

Related hooks (`~/.claude/hooks/`): `session_context.py` (SessionStart: git state + in-progress plan, compact resume), `discipline.py` (plan gate, test-weakening ask, Stop verification incl. failed runs), `notify.py` (toast, skipped while discipline blocks), `guard.py`, `format_lint.py`.

Built-in or plugin skills used as-is: `code-review`, `simplify`, `security-review`, `skill-creator` (anthropic-skills), `claude-api`, `init`.

Account-synced skills: `skills/synced/` is written by claude.ai (anthropic-skills such as docx, pdf, pptx, xlsx, docs, deep-research, browser/computer-use guides). It is gitignored and managed outside this repository; do not edit it here.

Usage evidence (transcripts 2026-07-22..2026-11-07, 34 sessions): none of the own skills above has been invoked through the Skill tool yet, and `code-reviewer` ran in one session. Judge additions and pruning from real project use, not from this baseline.

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
