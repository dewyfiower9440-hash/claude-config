"""Verification evidence for discipline.py: project policy lookup and result attribution.

A project declares its policy in the nearest CLAUDE.md (walking up from an edited file)
that has a `## Verification` section:

    ## Verification
    - gate: `uv run ruff check . && uv run pyright && uv run pytest -q --cov`
    - ci: required          # required | none
    - validate: `python -I scripts/smoke.py`   # optional, real-environment check

Without a declared policy only real test/lint/type runners count (never arbitrary
scripts). A run counts as *passed* only when its exit status is attributable: the
hook saw success (PostToolUse, not a failure event), it did not go to the background,
and nothing after it in the command line can mask its status (`|`, `;`, `||`, `&`),
unless `set -o pipefail` / `set -e` make that safe.
"""

from __future__ import annotations

import os
import re
from typing import NotRequired, TypedDict

from shellparse import Dialect, ParseError, SimpleCommand, parse, program_and_args


class Policy(TypedDict):
    root: str  # directory of the CLAUDE.md that declares it; "" when undeclared
    gate: str | None
    ci: str  # "required" | "none" | "undeclared"
    validate: str | None
    # ignored files (globs relative to root) whose changes still matter, e.g. a local config
    watch: NotRequired[list[str]]


class Evidence(TypedDict):
    outcome: str  # "passed" | "failed" | "unknown" | "partial"
    kind: str  # "gate" | "runner" | "validate"
    cmd: str
    why: str


UNDECLARED: Policy = {"root": "", "gate": None, "ci": "undeclared", "validate": None}
# The heading is `## Verification` and nothing else: "## Verification notes" (or a history
# section quoting an old gate) declares no policy.
SECTION = re.compile(
    r"^##[ \t]+Verification[ \t]*\n(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)
FIELD = re.compile(
    r"^\s*(?:[-*]\s+)?(gate|ci|validate|watch)\s*:\s*(.+?)\s*$", re.IGNORECASE
)

RUNNERS = {
    "pytest", "pyright", "mypy", "vitest", "jest", "mocha", "tsc", "eslint",
    "invoke-pester", "invoke-scriptanalyzer",
}  # fmt: skip
# A build is not a check (it says nothing about tests, lint or types).
PACKAGE_SCRIPTS = re.compile(r"^(test|lint|typecheck|check)(:[\w-]+)?$")
# Options with which a runner checks nothing (it prints something and exits 0) or reports
# success whatever it found.
NOT_A_RUN = {
    "--version", "--help", "-h", "--collect-only", "--co", "--setup-plan", "--fixtures",
    "--markers", "--exit-zero", "--showconfig", "--init", "--print-config", "--listtests",
}  # fmt: skip


# ---------------------------------------------------------------- policy (reads files)
def parse_policy(text: str, root: str) -> Policy | None:
    m = SECTION.search(text)
    if not m:
        return None
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        fm = FIELD.match(line)
        if fm:
            value = fm.group(2).split("  #")[0].strip()
            fields[fm.group(1).lower()] = value.strip("`").strip()
    ci_word = (
        fields.get("ci", "none").split()[0].lower() if fields.get("ci") else "none"
    )
    ci = (
        ci_word if ci_word in ("required", "none") else "required"
    )  # unknown value: be strict
    watch = [
        g.strip("`")
        for g in re.split(r"[,\s]+", fields.get("watch", ""))
        if g.strip("`")
    ]
    return {
        "root": root,
        "gate": fields.get("gate"),
        "ci": ci,
        "validate": fields.get("validate"),
        "watch": watch,
    }


def find_policy(file_path: str) -> Policy:
    d = os.path.dirname(os.path.abspath(file_path))
    while True:
        claude_md = os.path.join(d, "CLAUDE.md")
        if os.path.isfile(claude_md):
            try:
                with open(claude_md, encoding="utf-8") as f:
                    policy = parse_policy(f.read(), d)
            except OSError:  # unreadable CLAUDE.md: keep walking up
                policy = None
            if policy:
                return policy
        parent = os.path.dirname(d)
        if parent == d:
            return UNDECLARED
        d = parent


# ---------------------------------------------------------------- attribution (pure)
def silent_on_failure(cmd: SimpleCommand) -> bool:
    """PowerShell cmdlet runners whose failures do not reach the exit status (measured on 5.1:
    the PowerShell tool reports success after failing Pester tests unless -CI/-EnableExit is set,
    and Invoke-ScriptAnalyzer findings never fail without -EnableExit)."""
    program, args = program_and_args(cmd)
    low = {a.lower() for a in args}
    if program == "invoke-pester":
        return not low & {"-ci", "-enableexit"}
    if program == "invoke-scriptanalyzer":
        return "-enableexit" not in low
    return False


def is_runner(cmd: SimpleCommand) -> bool:
    program, args = program_and_args(cmd)
    low = [a.lower() for a in args]
    if NOT_A_RUN & set(low):
        return False
    if program in RUNNERS:
        return True
    if program == "ruff":
        return low[:1] == ["check"] or (low[:1] == ["format"] and "--check" in low)
    if program == "prettier":
        return "--check" in low
    if program in ("uv", "uvx"):
        rest = low[low.index("run") + 1 :] if "run" in low else low
        words = [
            w
            for i, w in enumerate(rest)
            if not w.startswith("-") and (i == 0 or not _takes_value(rest[i - 1]))
        ]
        return bool(words) and is_runner(SimpleCommand(words=words))
    if program in ("python", "python3", "py"):
        return (
            "-m" in low
            and low.index("-m") + 1 < len(low)
            and low[low.index("-m") + 1] in RUNNERS
        )
    if program in ("pnpm", "npm", "yarn"):
        rest = low[1:] if low[:1] == ["run"] else low
        return bool(rest) and bool(PACKAGE_SCRIPTS.match(rest[0]))
    if program in ("cargo", "go", "dotnet"):
        return bool(low) and low[0] in ("test", "check", "clippy", "vet")
    return False


def _takes_value(flag: str) -> bool:
    return flag in (
        "--with",
        "--python",
        "--project",
        "--directory",
        "-p",
        "--group",
        "--extra",
    )


def _errexit_pipefail(cmds: list[SimpleCommand]) -> tuple[bool, bool]:
    errexit = pipefail = False
    for c in cmds:
        program, args = program_and_args(c)
        if program != "set":
            continue
        for i, a in enumerate(args):
            if re.fullmatch(r"-[a-zA-Z]*e[a-zA-Z]*", a):
                errexit = True
            if a == "-o" and i + 1 < len(args):
                errexit |= args[i + 1] == "errexit"
                pipefail |= args[i + 1] == "pipefail"
            if re.fullmatch(r"-[a-zA-Z]*o", a) and i + 1 < len(args):
                pipefail |= args[i + 1] == "pipefail"
    return errexit, pipefail


def attributable(cmds: list[SimpleCommand], start: int) -> bool:
    """True when a failure of cmds[start] reaches the command line's exit status."""
    errexit, pipefail = _errexit_pipefail(cmds[:start])
    ok_ops = (
        {"&&"} | ({"|"} if pipefail else set()) | ({";", "\n"} if errexit else set())
    )
    for c in cmds[start:-1]:
        if c.op_after not in ok_ops:
            return False
    return True


def _find_sequence(
    cmds: list[SimpleCommand], target: list[SimpleCommand]
) -> int | None:
    want = [t.words for t in target]
    for k in range(len(cmds) - len(want) + 1):
        if [c.words for c in cmds[k : k + len(want)]] == want:
            return k
    return None


def cwd_at(cmds: list[SimpleCommand], index: int, cwd: str) -> str:
    """Working directory in effect at cmds[index], following `cd` / `Set-Location`."""
    for c in cmds[:index]:
        program, args = program_and_args(c)
        if program in ("cd", "set-location", "sl", "pushd", "chdir") and args:
            cwd = _resolve(cwd, args[-1])
    return cwd


def _resolve(cwd: str, target: str) -> str:
    home = os.path.expanduser("~")
    target = re.sub(
        r"^(\$HOME|\$env:USERPROFILE|~)(?=$|[\\/])",
        lambda _m: home,
        target,
        flags=re.IGNORECASE,
    )
    msys = re.match(r"^/([a-zA-Z])(/.*)?$", target)  # Git Bash /c/dev -> C:/dev
    if msys:
        target = f"{msys.group(1).upper()}:{msys.group(2) or '/'}"
    return os.path.normpath(os.path.join(cwd, target))


def _inside(path: str, root: str) -> bool:
    p, r = (
        os.path.normcase(os.path.normpath(path)),
        os.path.normcase(os.path.normpath(root)),
    )
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


CD = ("cd", "set-location", "sl", "pushd", "chdir")
LITERAL_PATH = re.compile(r"^(?:~|\$HOME)?[^$`%*?~]*$")


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )


def gate_form(
    cmds: list[SimpleCommand],
    start: int,
    length: int,
    cwd: str,
    root: str,
    declared: str,
) -> str | None:
    """Why this command line is not a run of the declared check that can be trusted (None
    when it is). The whole line must be the check, or one literal `cd <root> &&` and the
    check, run at the policy's root: anything before it can decide what the words of the
    check start (PATH, a function, a trap) or where, and anything after it, or an operator
    other than `&&` inside it, can decide the exit status instead of the check."""
    how = f"run the gate as declared and nothing else: `{declared}` at {root}, or `cd {root} && {declared}`"
    before, after = cmds[:start], cmds[start + length :]
    place = cwd
    if before:
        program, args = program_and_args(before[0])
        literal = (
            len(before) == 1
            and before[0].op_after == "&&"
            and program in CD
            and len(args) == 1
            and bool(LITERAL_PATH.match(args[0]))
        )
        if not literal:
            return f"something runs before the gate ({how})"
        place = _resolve(cwd, args[0])
    if after:
        return f"something runs after the gate ({how})"
    if cmds[-1].op_after:
        return f"the gate does not end the command line ({how})"
    if any(c.op_after != "&&" for c in cmds[start:-1]):
        return "the declared gate joins its stages with something other than &&: a failing stage does not fail it"
    if not _same(place, root):
        return f"not run at the policy's root ({how})"
    return None


def evaluate(
    command: str,
    dialect: Dialect,
    cwd: str,
    policy: Policy,
    *,
    failed: bool,
    background: bool,
) -> Evidence | None:
    """Evidence this command line provides for `policy`'s project, or None if it is not a check."""
    try:
        cmds = [c for c in parse(command, dialect) if not c.nested]
    except ParseError:
        gate = policy["gate"]
        if gate and gate in command:
            return {
                "outcome": "unknown",
                "kind": "gate",
                "cmd": command[:200],
                "why": "unparseable command line",
            }
        return None

    def verdict(
        kind: str, index: int, start: int | None = None, form: str | None = None
    ) -> Evidence:
        silent = [
            c
            for c in cmds[index if start is None else start : index + 1]
            if silent_on_failure(c)
        ]
        if silent and not failed:
            return {
                "outcome": "unknown",
                "kind": kind,
                "cmd": command[:200],
                "why": f"{silent[0].words[0]} reports failures without a failing exit status (add -CI / -EnableExit)",
            }
        if failed:
            return {
                "outcome": "failed",
                "kind": kind,
                "cmd": command[:200],
                "why": "exit status non-zero",
            }
        if background:
            return {
                "outcome": "unknown",
                "kind": kind,
                "cmd": command[:200],
                "why": "ran in the background",
            }
        if form:
            return {
                "outcome": "unknown",
                "kind": kind,
                "cmd": command[:200],
                "why": form,
            }
        if not attributable(cmds, index):
            return {
                "outcome": "unknown",
                "kind": kind,
                "cmd": command[:200],
                "why": "exit status masked by | ; || or &",
            }
        return {"outcome": "passed", "kind": kind, "cmd": command[:200], "why": ""}

    root = policy["root"]
    for kind, declared in (("gate", policy["gate"]), ("validate", policy["validate"])):
        if not declared:
            continue
        try:
            target = [c for c in parse(declared, dialect) if not c.nested]
        except ParseError:
            continue
        k = _find_sequence(cmds, target)
        if k is not None and _inside(cwd_at(cmds, k, cwd), root):
            form = gate_form(cmds, k, len(target), cwd, root, declared)
            return verdict(kind, k + len(target) - 1, start=k, form=form)

    for i, c in enumerate(cmds):
        if is_runner(c) and (not root or _inside(cwd_at(cmds, i, cwd), root)):
            if policy["gate"]:
                # A test command ran, but not the declared gate (e.g. pytest without --cov).
                return {
                    "outcome": "partial",
                    "kind": "runner",
                    "cmd": command[:200],
                    "why": "declared gate not run",
                }
            return verdict("runner", i)
    return None
