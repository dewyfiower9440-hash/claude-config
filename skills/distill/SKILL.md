---
name: distill
description: Turn what this session learned into knowledge notes in the Obsidian vault (C:\dev\vault\notes\knowledge) - problems with their cause, patch and verification, and decisions with the options dropped - appending to an existing note when the same problem came back. Use when the user says distill, 정리, "남겨 둬", or asks to record what was learned before closing a task. With an argument, distills that topic from past session notes instead. Not for summarising a conversation (the session note already holds it), for project Gotchas (those go in the project's CLAUDE.md), or for anything that is not verified.
argument-hint: [topic to distill from past session notes; empty = this session]
disable-model-invocation: true
---

# Distill

Arguments: `$ARGUMENTS`. Session: `${CLAUDE_SESSION_ID}`.

The vault is `C:\dev\vault\notes`. `sessions/` and `env/` are generated: never edit them.
Hand-written notes live in `knowledge/`; that folder is the only place this skill writes.
Invoking this skill is the user's instruction to write these notes, and nothing else outside
the session's project. The hooks make no exception for it: see section 4 for what they say.

## 1. What is worth a note
- From this session (or, with an argument, from the session notes that match it: search
  `C:\dev\vault\notes\sessions` for the topic's error text, file names and task ids; hand a
  wide search to the `Explore` agent).
- A **problem**: something went wrong, its cause was found, and a patch or a workaround was
  verified. A **decision**: a real choice between options, with what was dropped and why.
- Only what is verified, with its evidence (`file:line`, a commit, a command and its
  result). A guess is written as a guess ("추정") or left out. No summary of the work, no
  praise, nothing the code or its README already says.
- At most three notes. Nothing reusable: write nothing and say so.

## 2. Find the note it belongs to
- Search `C:\dev\vault\notes\knowledge` for the error text, the file names and the key
  terms (Grep; file names there are short Korean sentences).
- The same problem is already there: append under `## 다시 나왔을 때` (problem) or
  `## 다시 볼 조건` (decision), with today's date, what was different this time, and the
  session link. Do not rewrite what the note already says.
- Otherwise a new note from `templates/problem.md` or `templates/decision.md`: copy the
  template, fill `{{title}}`, and `{{date}}` with the day the problem was found or the
  decision made (`YYYY-MM-DD`; not today when the note is about an earlier day), set
  `area`, keep the section headings and delete the `<!-- ... -->` hints. `status` of a
  problem: `open` until its patch is verified, then `resolved`; a decision keeps `decided`.
  File name: a short Korean sentence that says the problem
  (`커밋 리뷰 차단이 재시도로 우회됨.md`), no date in the name.

## 3. Write it
- Korean prose, short. 증상 with the error text as it was (a `text` code block); 원인 with
  the evidence; 패치 with the commit or the plan file; 검증 with the command and its result.
- Link the session note: its name is `<the day the session started>-<first 8 characters of
  the session id>` under `C:\dev\vault\notes\sessions\YYYY\MM\`. Find it, do not build it:
  Glob `C:/dev/vault/notes/sessions/**/*-<first 8>.md` and write the name found as
  `[[YYYY-MM-DD-xxxxxxxx]]`. Nothing found (a session younger than 10 minutes, or
  `${CLAUDE_SESSION_ID}` printed above as a literal placeholder): leave the link out and
  say so in the report. Do not guess the session from the newest transcript: several
  sessions share a project folder.
- Never a secret value, not as an example either: write `[가림]` in its place. No HTML, no
  `![...]` embeds, no links other than `http(s)` and `[[...]]`.

## 4. Check, in this order
- `uv run --project C:/dev/vault vault-sync check <each file written>`, each file by its
  absolute path (a relative path is refused as "not a file of the vault" from any other
  folder): exit 0. A `path:line` is reported: mask or remove that line and run again (until
  it passes, the vault commits nothing).
- The vault's gate, from `C:\dev\vault`, in the foreground, as the whole command line (its
  CLAUDE.md `## Verification` has the line; a note is tracked content there, so the gate's
  evidence is stale after writing one). Then go back to the session's own folder.
- What the hooks say about these notes, and what to do: with an approved plan held for the
  session's project they are outside its scope, and the Stop reports them once; without a
  plan nothing is said for up to five files. Say in the report that they are `/distill`
  notes and do not revert them. Under `CLAUDE_AUTHZ_MODE=enforce` the write itself is
  refused: do not work around it, tell the user that the notes need a session started in
  `C:\dev\vault` with a plan there.

## 5. Report (Korean)
- Each note written or appended to, with its path and one line on what it says; what was
  left out and why (not verified, already recorded); the check and gate results.
