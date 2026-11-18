---
name: refactor
description: Behavior-preserving refactoring - pin current behavior with characterization tests, then change structure in small verified steps (each green and committed) with no functional change. Use when the user asks to refactor, clean up, restructure, extract, rename, split modules, remove duplication or improve design without changing behavior. Not when behavior is meant to change (bug fix: /debug; new behavior: /plan).
argument-hint: <target code and goal>
---

# Safe refactor

Target / goal: `$ARGUMENTS`

The rule: **behavior does not change.** If a behavior change is wanted, it is a separate, explicit step that the user approves.

## 1. Scope & goal
- State the structural goal (e.g. "separate I/O from pricing logic", "remove 3 duplicated parsers") and what is out of scope.
- Identify the public surface that must not change: function signatures, CLI flags, HTTP/API contracts, file formats, log event names, DB schema. If the refactor must change one of these, stop and get approval.
- If more than 3 files are involved, write a short step plan and get approval (`/plan` light) before editing.

## 2. Pin the behavior
- Check the existing coverage of the target (`pytest --cov` / `vitest --coverage` / Pester). Where it is weak, add **characterization tests** that capture the current outputs, including edge cases and error behavior (even odd current behavior: pin it and note it, don't fix it).
- All tests green **before** the first structural change. Commit the tests separately (`test: characterize ...`).

## 3. Small steps
- One mechanical transformation per step: extract function, inline, rename, move, introduce parameter/interface, split module, replace conditional with polymorphism, and so on.
- After each step: full quality gate green → commit (`refactor: ...`). Never batch several risky moves into one commit.
- Do not change tests and production code in the same step, except for mechanical renames/moves that the tests must follow.
- Keep each step reversible (`git revert` of a single commit).

## 4. Verify equivalence
- Full test suite, type check and lint green; the coverage of the target did not drop.
- Where practical, compare before/after on real inputs: same outputs for a sample of inputs, the same CLI output, an unchanged API snapshot.
- `code-reviewer` agent on the full range, told explicitly to look for **unintended behavior changes**.

## 5. Report
- Global reporting format, plus: the structural before→after in 2-3 lines, the list of commits, any pinned odd behavior worth fixing later (as proposals).
