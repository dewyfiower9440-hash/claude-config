"""A coarse risk score for what one turn changed (P2-01), and whether a plan compares
alternatives (P2-03). Pure functions: no file, no environment, no clock.

The score is written down by the Stop hook and may lead to one note at the next prompt. It
never blocks and decides nothing else. Weights, size steps and the threshold are first
guesses: they are to be set again from what the observation file shows.

    score = sum of the weights of the kinds of file that changed (each kind once)
          + a step for the number of files, a step for the number of lines
          + 2 for every repository after the first
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

ADVICE_AT = 8
WEIGHTS = {
    "guard": 5,  # automation that guards the work: hooks, agent settings, pre-commit
    "ci": 5,
    "schema": 5,
    "deps": 4,
    "code": 2,
    "config": 2,
    "test": 1,
    "other": 1,
    "docs": 0,
}
FILE_STEPS = (3, 6, 11)  # from this many files on: +1, +2, +3
LINE_STEPS = (50, 200, 500)
PER_EXTRA_REPO = 2

_GUARD_DIRS = {"hooks", ".claude", ".husky"}
_CI_NAMES = {".gitlab-ci.yml", "azure-pipelines.yml"}
_DEPS_NAMES = {
    "uv.lock",
    "pnpm-lock.yaml",
    "package-lock.json",
    "pyproject.toml",
    "package.json",
    "cargo.toml",
    "cargo.lock",
    "go.mod",
    "go.sum",
}
_DOC_EXT = {".md", ".rst", ".txt"}
_CONFIG_EXT = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg"}
_CONFIG_NAMES = {".env.example", "dockerfile"}
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# Whole words only, and no bare "option" or "비교": "Adoption", "Optional steps",
# "Configuration options" and "비교 테스트 추가" are headings of plans that compare nothing.
_COMPARES = re.compile(
    r"대안|옵션|선택지|\balternatives?\b|\boptions\s+considered\b|\btrade-?offs?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Risk:
    score: int
    kinds: dict[str, int]  # files per kind
    files: int
    lines: int
    repos: int


def kind(rel: str, is_test: bool, is_code: bool) -> str:
    """The kind of a changed file, from its path inside its repository. The first that
    fits: guard, ci, schema, deps, test, docs, code, config, other."""
    path = PurePosixPath(rel.replace("\\", "/").lower())
    dirs, name, ext = set(path.parts[:-1]), path.name, path.suffix
    top = path.parts[0] if len(path.parts) > 1 else ""
    # a guard by its place at the top of the repository: `src/hooks/` holds ordinary code,
    # and only the repository's own or the agent's `settings.json` is one
    if (
        top in _GUARD_DIRS
        or name == ".pre-commit-config.yaml"
        or (name == "settings.json" and top in ("", ".claude"))
    ):
        return "guard"
    if ".github/workflows/" in f"{path.as_posix()}" or name in _CI_NAMES:
        return "ci"
    if "migrations" in dirs or ext == ".sql" or name.startswith("schema."):
        return "schema"
    if name in _DEPS_NAMES or (name.startswith("requirements") and ext == ".txt"):
        return "deps"
    if is_test:
        return "test"
    if ext in _DOC_EXT or path.parts[:1] == ("docs",):
        return "docs"
    if is_code:
        return "code"
    if ext in _CONFIG_EXT or name in _CONFIG_NAMES:
        return "config"
    return "other"


def _count(value: object) -> int:
    """A count from the hook's state: a whole number of 0 or more, else 0."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else 0
    )


def _step(n: int, steps: Sequence[int]) -> int:
    return sum(1 for at in steps if n >= at)


def assess(kinds: Sequence[str], lines: object, repos: object) -> Risk:
    """The score of one turn: `kinds` has one entry per changed file (see `kind`), `lines`
    is the number of changed lines as far as it is known, `repos` the number of
    repositories the files are in. An unknown kind weighs like "other"."""
    per_kind: dict[str, int] = {}
    for k in kinds:
        per_kind[k] = per_kind.get(k, 0) + 1
    line_count, repo_count = _count(lines), _count(repos)
    score = (
        sum(WEIGHTS.get(k, WEIGHTS["other"]) for k in per_kind)
        + _step(len(kinds), FILE_STEPS)
        + _step(line_count, LINE_STEPS)
        + PER_EXTRA_REPO * max(repo_count - 1, 0)
    )
    return Risk(score, per_kind, len(kinds), line_count, repo_count)


def compares_alternatives(plan_text: str) -> bool:
    """Whether a plan has a part that compares alternatives: a heading or the header row of
    a table that names one (대안, 옵션, 선택지, alternatives, options considered, trade-offs).
    A word search, nothing more: it tells a plan that has such a part from one that has not."""
    lines = plan_text.splitlines()
    for i, line in enumerate(lines):
        heading = _HEADING.match(line)
        if heading and _COMPARES.search(heading.group(1)):
            return True
        is_header = (
            _TABLE_ROW.match(line) is not None
            and i + 1 < len(lines)
            and _TABLE_RULE.match(lines[i + 1]) is not None
        )
        if is_header and _COMPARES.search(line):
            return True
    return False


def advice_needed(score: int, at: int, plan_texts: Sequence[str]) -> bool:
    """A score at or above the threshold with no plan in hand that compares alternatives."""
    return score >= at and not any(compares_alternatives(text) for text in plan_texts)


def threshold(env_value: str | None) -> int:
    """The advice threshold: CLAUDE_RISK_ADVICE_AT when it is a positive whole number."""
    text = (env_value or "").strip()
    return (
        int(text) if text.isascii() and text.isdigit() and int(text) > 0 else ADVICE_AT
    )
