---
name: new-project
description: Scaffold a new project under C:\dev with Python (uv), TypeScript (pnpm) or PowerShell, including lint/format/type/test tooling, GitHub Actions CI, CLAUDE.md, .env.example and optional SQLite/MSSQL MCP. Use when the user wants to start, create or set up a new project, app, tool, script or repo. Not for adding a module or tooling to a project that already exists.
argument-hint: <name> [python|ts|ps] [sqlite] [mssql]
---

# New project

Arguments: `$ARGUMENTS`

1. Work out the name, stack (`python`, `ts`, `ps`) and DBs (`sqlite`, `mssql`, none) from the arguments and the request. If the stack is truly ambiguous, ask with AskUserQuestion (one question). Name: letters, digits, `.`, `_`, `-`; kebab-case preferred.
2. Run:
   ```
   python -I C:/dev/tools/scaffold/new_project.py <name> --stack <stack> [--db sqlite mssql]
   ```
   It creates `C:\dev\<name>`, installs dev dependencies, writes configs, CI and CLAUDE.md, and runs `git init -b main` with an initial commit when a git identity is set.
3. Verify the scaffold actually works and show the results:
   - python: `uv run pytest -q`, `uv run ruff check .`, `uv run pyright`
   - ts: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`
   - ps: `Import-Module Pester -MinimumVersion 5.0; Invoke-Pester -Output Detailed`
4. GitHub: ask whether to create the remote. If yes: `gh repo create <name> --private --source . --remote origin --push` (this pushes, so confirm first).
5. If `mssql` was chosen: tell the user to store the connection string once, in a separate terminal window (it prompts with hidden input, so it does not work through `!`; the value goes to Credential Manager):
   `uv run --project C:/dev/tools/db-mcp db-mcp set-mssql <profile>` (profile = name in snake_case, as written in the project's CLAUDE.md).
   The project `.mcp.json` is read-only for both servers (the scaffold leaves an empty `data/app.db` so the read-only SQLite server can start); a project that needs the SQLite server to write adds `--allow-write` to its args itself, and says so in its CLAUDE.md.
6. Remind the user that a new Claude Code session in `C:\dev\<name>` picks up the project `.mcp.json` (approve it on first launch).
7. Then customize CLAUDE.md for the real purpose of the project once the user describes it.
