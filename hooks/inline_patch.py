"""devenv-v1-2 step 3: inline patches, found from the command text alone (no I/O).

An inline patch is a shell command that writes a file from code or text given on the
command line, with a backslash in that text: `python -c` code or a python heredoc that
writes (`write_text`, `write_bytes`, `.write(`, `open(..., 'w'|'a'|'x'|'+')`, or its
standard output redirected to a file), and `cat > f <<EOF` / `tee f <<EOF`. The shell and
Python string escapes turn such backslashes into other bytes (`\\r` in
`C:\\dev\\qr-data\\run` became a CR on 2026-11-20; global rules section 9). A commit message
heredoc, a heredoc that is only printed, `echo`, python that writes nothing (also to
stdout/stderr, `2>/dev/null`, `> $null`), `python -m` and a python script file are not
patches. A command that cannot be parsed gives nothing: the check never guesses.

Not seen (accepted, measured later): output piped on to a writer (`| tee f`, `| Out-File`),
a heredoc piped into python (`cat <<EOF | python -`), code inside `bash -c "..."`, `sed -i`
and `perl -pi` (their backslashes are regular expressions).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal, NamedTuple

from shellparse import ParseError, SimpleCommand, parse, program_and_args
from shellwrites import GLUED_REDIRECT, NOT_A_FILE, POSIX_DEVICES, REDIRECT

Kind = Literal["python-c", "python-heredoc", "shell-heredoc"]


class Finding(NamedTuple):
    kind: Kind
    program: str


PYTHON = re.compile(r"python(\d+(\.\d+)*)?w?|py")
# Short options of python that take a value (the rest of the cluster, or the next word).
PYTHON_VALUED = frozenset("WX")
MODE = r"['\"][rbt]*[wax+][rwabxt+]*['\"]"
ARGS = r"(?:[^()]|\([^()]*\))*"  # may span lines: open(\n    p,\n    "w",\n)
WRITE_SIGNAL = re.compile(
    r"\.write_text\s*\(|\.write_bytes\s*\("
    # sys.stdout.write and os.write(1, ...) print, they write no file
    r"|(?<!stdout)(?<!stderr)(?<!\bos)\.write\s*\("
    # builtin open(path, mode): the mode is a later argument (`open('tab')` reads); the
    # arguments may hold one level of calls (`open(os.path.join(a, b), 'w')`) but not run
    # past open's own closing parenthesis (`open('f').read().replace('\\', 'x')`)
    rf"|(?<![.\w])open\s*\({ARGS}?,\s*(?:mode\s*=\s*)?{MODE}"
    # Path.open(mode), io.open(path, mode)
    rf"|\.open\s*\((?:{ARGS}?,)?\s*(?:mode\s*=\s*)?{MODE}"
)


def inline_patches(command: object, dialect: str) -> list[Finding]:
    """The inline patches of a shell command (see the module docstring)."""
    if not isinstance(command, str):
        return []
    try:
        cmds = parse(command, "powershell" if dialect == "powershell" else "bash")
    except ParseError:
        return []
    out: list[Finding] = []
    for cmd in cmds:
        found = _patch_of(cmd)
        if found is not None:
            out.append(found)
    return out


def _patch_of(cmd: SimpleCommand) -> Finding | None:
    plain, to_file = _stdout_to_file(cmd.words)
    program, args = program_and_args(SimpleCommand(plain))
    if program == "uv":
        program, args = _uv_run_program(args)
    if PYTHON.fullmatch(program):
        return _python_patch(program, args, cmd.heredoc, to_file)
    if program in ("cat", "tee") and cmd.heredoc is not None and "\\" in cmd.heredoc:
        writes = to_file or (
            program == "tee"
            and any(not a.startswith("-") and _is_file(a) for a in args)
        )
        return Finding("shell-heredoc", program) if writes else None
    return None


def _is_file(target: str) -> bool:
    return target.lower() not in NOT_A_FILE and not target.startswith(POSIX_DEVICES)


def _stdout_to_file(words: list[str]) -> tuple[list[str], bool]:
    """(the words without redirections, whether standard output goes to a file). A
    redirection of another descriptor (`2> err`, `2>&1`) or to a device (`/dev/null`,
    `NUL`, `$null`) is no file written from the command's output."""
    plain: list[str] = []
    to_file = False
    i = 0
    while i < len(words):
        w = words[i]
        glued = GLUED_REDIRECT.match(w) if not REDIRECT.fullmatch(w) else None
        if REDIRECT.fullmatch(w) or glued or w == ">&":
            fd = w[0] if w[0] in "0123456789&*" else "1"
            if fd == "1" and plain and re.fullmatch(r"\d", plain[-1]):
                # shellparse splits `2>` into `2` `>`: read as the descriptor (`2 > f`,
                # where the 2 is an argument and stdout goes to f, is missed: rare)
                fd = plain.pop()
            if glued:
                target = glued.group("target")
            else:
                i += 1
                while i < len(words) and REDIRECT.fullmatch(words[i]):
                    i += 1
                target = words[i] if i < len(words) else ""
            duplicate = w == ">&" and re.fullmatch(r"\d|-", target)
            if fd in ("1", "&", "*") and not duplicate and target and _is_file(target):
                to_file = True
        else:
            plain.append(w)
        i += 1
    return plain, to_file


def _uv_run_program(args: list[str]) -> tuple[str, list[str]]:
    """The program `uv [options] run [options] <program> ...` starts, and its arguments.
    An option may take the next word as its value; a word after an option that is not
    python is taken for such a value (uv has many valued options), so this never names a
    program it is not sure of."""
    rest = _after_options(args, lambda w: w == "run")
    if not rest or rest[0] != "run":
        return "", []
    rest = _after_options(rest[1:], lambda w: bool(PYTHON.fullmatch(_basename(w))))
    if not rest:
        return "", []
    return program_and_args(SimpleCommand(rest))


def _after_options(words: list[str], accept: Callable[[str], bool]) -> list[str]:
    """`words` from the first non-option word, where a non-option word right after an
    option is skipped as that option's possible value unless `accept` takes it."""
    after_option = False
    for i, w in enumerate(words):
        if w == "--":
            return words[i + 1 :]
        if w.startswith("-") and len(w) > 1:
            after_option = "=" not in w
            continue
        if accept(w):
            return words[i:]
        if not after_option:
            return words[i:]
        after_option = False
    return []


def _basename(word: str) -> str:
    return program_and_args(SimpleCommand([word]))[0]


def _python_patch(
    program: str, args: list[str], heredoc: str | None, to_file: bool
) -> Finding | None:
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-" or not a.startswith("-") or a == "--":
            # `python -` reads the heredoc; `python script.py` runs a file: not inline
            if a == "-" and heredoc is not None:
                return _if_writes("python-heredoc", program, heredoc, to_file)
            return None
        if a.startswith("--"):  # --version, --help ...: nothing runs inline
            i += 1
            continue
        for j, letter in enumerate(a[1:], start=1):
            if letter == "m":
                return None  # `python -m module ...`: the rest is the module's
            if letter == "c":
                code = a[j + 1 :] or (args[i + 1] if i + 1 < len(args) else "")
                return _if_writes("python-c", program, code, to_file)
            if letter in PYTHON_VALUED:
                if not a[j + 1 :]:
                    i += 1  # the value is the next word
                break
        i += 1
    if heredoc is not None:  # `python <<EOF`
        return _if_writes("python-heredoc", program, heredoc, to_file)
    return None


def _if_writes(kind: Kind, program: str, code: str, to_file: bool) -> Finding | None:
    if "\\" not in code:
        return None
    if to_file or WRITE_SIGNAL.search(code):
        return Finding(kind, program)
    return None
