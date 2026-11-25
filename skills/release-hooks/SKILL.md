---
name: release-hooks
description: Release a change of this configuration repository (~/.claude - hooks, settings.json, skills, agents, rules) the safe way - from the work tree, through the gate and a hook latency comparison, by a fast-forward merge into main, then push and CI. Use when hooks or settings were changed in C:\dev\_wt\claude-config and should go live. Not for any other project (use /commit and /predeploy there), and never for editing files under ~/.claude directly.
argument-hint: [branch in the work tree, default: its current branch]
---

# Release hooks

The live hooks run from the working tree of `~/.claude`. A broken guard or pre hook refuses every tool call (`onFailure: "block"`), so nothing is edited there: `~/.claude` changes only by a fast-forward merge of a commit that passed the gate in the work tree `C:\dev\_wt\claude-config` (CLAUDE.md `## Release`).

## 1. Where things stand
- Run from the work tree root. `git status --short` must be clean (commit first, `/commit` conventions); `git -C ~/.claude status --short` must show no change under `hooks/` or `settings.json` (if it does, stop and report: somebody edited the live copy).
- `git log --oneline main..HEAD`: the commits that will go live. No commits: nothing to release.

## 2. Gate (the main session, in the foreground)
- `python -I tests/run_all.py`, verbatim, as the whole command line. Pass = exit 0 and `GATE PASSED`; note the `gate:` line (modules, checks, wall time).
- A failure stops the release. Fix it in the work tree (test first), commit, and start again from step 1. The same failure twice: stop and report (global rules 1.3).

## 3. Hook latency (only when a file under `hooks/` changed)
- Run `tests/bench_hooks.py <label>` with the interpreter the hooks run on (the one `settings.json` names, not whatever `python` is first on PATH), then run the gate again before committing the new baseline (the file changes the content the evidence is bound to).
- Compare p50 per hook with the newest file in `tests/_baseline/`. A hook more than 20 % slower without a stated reason stops the release. The approval corpus must show the same cases passing as before (two cases, A7 and A8, pass only under enforce).

## 4. Merge, push, CI
- `git -C ~/.claude merge --ff-only <branch>`. Not a fast-forward: stop, never force; rebase the work tree branch onto `main`, run the gate again.
- When you can, merge after the user's next own prompt, not in the turn that made the commits (a subagent's hand-back or a relayed result continues the turn; a rebase or an amend gives old commits a new time). The discipline hook does not count what git brings in as edits, except commits whose committer time falls in the current turn (`edit; git commit` must not slip through). A merge of such commits is therefore reported at the Stop as files changed in `~/.claude` without an approved plan there (the plan's approval is bound to the work tree, another git root): with no approval active when more than five files came in, with one active from the first file (observed 2026-11-25, 11 files). The Stop is blocked once with that list and the next Stop passes; no edit, commit or push is refused. The hook's text says to revert or reduce the change: for a release the user asked for, that is the user's decision, so do not revert on your own, and say in the report which files they are and that they came in by the release merge.
- Right after the merge, run a few ordinary commands (`git status`, a Read, an Edit in a scratch folder): the live hooks must not block them.
- Push needs the user's go-ahead unless this task's pushes were approved (global rules, section 6): `git -C ~/.claude push origin main`. Never force-push.
- Follow the CI run of the pushed commit to its end (`gh run watch <id> --exit-status`) and read the result from the API. A job that never started (no steps, an annotation about billing or limits) is "unknown", not a failure of the change: report it, do not rerun in a loop.

## 5. If the live hooks lock the session
- In your own terminal (not through Claude): `git -C ~/.claude checkout <last good commit> -- hooks settings.json`, then fix in the work tree and release again.

## 6. Report (Korean, the global format)
- State reached (Locally Verified / CI Verified run id / Unknown), the `gate:` line, the latency table when measured, the merged range, and anything left open.
