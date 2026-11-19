"""Write targets read from a shell command before it runs (P0-07, best effort).

`write_targets` names the files a Bash or PowerShell command visibly writes, so the
discipline hook can judge them like an Edit. It reads only what the command line says:

  seen      redirections (`>`, `>>`, `>|`, `2>`, `&>`, `*>`, `>& file`), and a small table
            of programs and cmdlets (tee, sed -i, cp, mv, install, ln, rm, touch, truncate,
            dd of=; Set-Content, Add-Content, Clear-Content, Out-File, Tee-Object,
            Export-Csv, New-Item, Remove-Item, Copy-Item, Move-Item, Rename-Item and common
            aliases), also after `do`/`then`/`else`/`{`/`!`/`timeout N`
  counted   a write whose target cannot be told (a variable, a substitution, a glob, a
            command that does not parse, more targets than MAX_TARGETS): returned as
            `unresolved`, never guessed
  NOT seen  whatever a script or program writes on its own (`python x.py`, node, git, build
            tools), code passed in a heredoc or `-c`/`-Command` string, aliases and
            functions defined elsewhere, paths built at run time, a directory change that
            does not last (`(cd sub; ...)`, `Push-Location`): later targets are then
            resolved against the wrong folder

So this is not a boundary. What it misses is left to the Stop-time content check (inside
git repositories) and to file permissions (outside them). Quotes are already removed when
words are inspected, so a literal ">" argument (`grep '>' f`) reads as a redirection: more
targets, never fewer.
"""

from __future__ import annotations

import re

from shellparse import Dialect, ParseError, SimpleCommand, parse, program_and_args

# verification and tempfile are imported inside write_targets: both are slow to import and
# most commands write nothing.

MAX_TARGETS = 200
REDIRECT = re.compile(
    r"(?:\d|\*|&)?>{1,2}\|?"
)  # '>', '>>', '>|', '2>', '&>', '*>>', ...
GLUED_REDIRECT = re.compile(r"^(?:\d|\*|&)?>{1,2}\|?(?P<target>[^>&|].*)$")
NOT_A_FILE = {"nul", "$null", "-"}
POSIX_DEVICES = ("/dev/", "/proc/", "/sys/")
UNRESOLVABLE = re.compile(r"[$`*?%]")

# program -> how its plain arguments name what it writes
ALL_POSITIONAL = {"tee", "rm", "mv", "rmdir", "unlink"}
LAST_POSITIONAL = {"cp", "install", "ln"}
TARGET_DIR = ("-t", "--target-directory")
PS_PATH_PARAMS = ("-path", "-literalpath", "-filepath", "-pspath")
PS_DESTINATION = "-destination"
PS_VALUE_PARAMS = (  # parameters whose next word is a value, not a path to write
    "-value",
    "-itemtype",
    "-type",
    "-encoding",
    "-filter",
    "-include",
    "-exclude",
    "-inputobject",
    "-width",
    "-delimiter",
    "-newname",
    "-name",
    "-target",
    "-stream",
    "-credential",
    "-erroraction",
    "-variable",
)
PS_WRITE_FIRST = {  # cmdlets (and aliases) that write the path they are given
    "set-content",
    "add-content",
    "ac",
    "clear-content",
    "clc",
    "out-file",
    "tee-object",
    "export-csv",
    "epcsv",
    "new-item",
    "ni",
    "rename-item",
    "ren",
    "rni",
}
PS_WRITE_ALL = {"remove-item", "ri", "del", "erase", "rd"}  # every listed path
PS_COPY = {"copy-item", "copy", "cpi"}  # writes the destination
PS_MOVE = {"move-item", "move", "mi"}  # changes source and destination
PS_ONLY_ALIASES = {
    "rm": "remove-item",
    "rmdir": "remove-item",
    "cp": "copy-item",
    "mv": "move-item",
    "tee": "tee-object",
    "mkdir": "new-item",  # a function over New-Item -ItemType Directory
    "md": "new-item",
}


def _split_redirections(words: list[str]) -> tuple[list[str], list[str]]:
    """(plain words, redirection targets) of one simple command."""
    plain: list[str] = []
    found: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if REDIRECT.fullmatch(w):
            if w[0] == ">" and plain and re.fullmatch(r"\d", plain[-1]):
                plain.pop()  # the descriptor of `2 > file`
            i += 1
            while i < len(words) and REDIRECT.fullmatch(words[i]):
                i += 1  # `&>` `>` (the parser's spelling of `&>>`)
            if i < len(words):
                found.append(words[i])
        elif w in (">&", "<&"):
            if plain and re.fullmatch(r"\d", plain[-1]):
                plain.pop()
            i += 1
            # `2>&1` copies a descriptor; `>& file` (bash) writes the file
            if w == ">&" and i < len(words) and not re.fullmatch(r"\d|-", words[i]):
                found.append(words[i])
        else:
            glued = GLUED_REDIRECT.match(w)
            if glued:
                found.append(glued.group("target"))
            else:
                plain.append(w)
        i += 1
    return plain, found


def _positionals(args: list[str], valued: set[str]) -> list[str]:
    """Arguments that are not options; an option in `valued` also consumes its value, and
    everything after `--` is positional."""
    out: list[str] = []
    skip = False
    for i, a in enumerate(args):
        if a == "--":
            return out + args[i + 1 :]
        if skip:
            skip = False
        elif a.startswith("-") and len(a) > 1:
            skip = a in valued
        else:
            out.append(a)
    return out


def _target_dir(args: list[str]) -> str | None:
    """The value of `-t DIR` / `--target-directory[=]DIR` (cp, mv, install, ln)."""
    for i, a in enumerate(args):
        if a in TARGET_DIR:
            return args[i + 1] if i + 1 < len(args) else None
        if a.startswith("--target-directory="):
            return a.split("=", 1)[1]
    return None


def _unix_targets(program: str, args: list[str]) -> list[str]:
    if program in ("mv", *LAST_POSITIONAL):
        into = _target_dir(args)
        named = _positionals(args, {*TARGET_DIR, "-m", "-o", "-g", "-S"})
        if program == "install" and "-d" in args:
            return named  # install -d makes every listed directory
        if into is not None:
            return ([*named, into]) if program == "mv" else [into]
        if program == "mv":
            return named
        return named[-1:] if len(named) >= 2 else []
    if program in ALL_POSITIONAL:
        return _positionals(args, set())
    if program == "touch":
        return _positionals(args, {"-d", "-r", "-t", "--date", "--reference"})
    if program == "mkdir":  # guard-b15 6: a folder made is a write (`mkdir -m 755 d`)
        return _positionals(args, {"-m", "--mode", "-Z", "--context"})
    if program == "truncate":
        return _positionals(args, {"-s", "--size", "-r", "--reference"})
    if program == "dd":
        return [a[3:] for a in args if a.startswith("of=") and len(a) > 3]
    if program == "sed" and any(
        a.startswith("--in-place") or re.fullmatch(r"-[a-zA-Z]*i.*", a) for a in args
    ):
        scripted = any(
            a in ("-e", "-f") or a.startswith(("--expression", "--file")) for a in args
        )
        named = _positionals(args, {"-e", "-f"})
        return named if scripted else named[1:]
    return []


def _ps_param(word: str) -> str | None:
    """ "path", "destination", "value" or "switch" for a `-Parameter` word (PowerShell
    accepts any unambiguous prefix); None when the word is not a parameter."""
    if not (word.startswith("-") and len(word) > 1) or re.fullmatch(r"-\d.*", word):
        return None
    low = word.lower()
    kinds = (
        {"path" for p in PS_PATH_PARAMS if p.startswith(low)}
        | ({"destination"} if PS_DESTINATION.startswith(low) else set())
        | {"value" for p in PS_VALUE_PARAMS if p.startswith(low)}
    )
    return kinds.pop() if len(kinds) == 1 else "switch"


def _powershell_targets(cmdlet: str, args: list[str]) -> list[str]:
    named: list[str] = []
    destination: list[str] = []
    plain: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        head, colon, attached = a.partition(":")
        kind = _ps_param(head if colon and head.startswith("-") else a)
        if kind is None:
            plain.append(a)
        elif kind != "switch":
            # the value: attached (`-Path:x`), else the following words of a comma list
            values = [attached] if colon and head.startswith("-") and attached else []
            if not values:
                while i + 1 < len(args):
                    i += 1
                    values.append(args[i])
                    if not args[i].endswith(","):
                        break
            if kind == "path":
                named += values
            elif kind == "destination":
                destination += values
        i += 1

    def listed(words: list[str]) -> list[str]:
        return [p for w in words for p in w.split(",") if p]

    named, destination, plain = listed(named), listed(destination), listed(plain)
    if cmdlet in PS_WRITE_ALL:
        return named + plain
    if cmdlet in PS_WRITE_FIRST:
        return named or plain[:1]
    # Copy-Item / Move-Item: the source is -Path or the first plain word; the destination
    # is -Destination or the next plain word after the source.
    source = named or plain[:1]
    rest = plain if named else plain[1:]
    target = destination or rest[:1]
    if cmdlet in PS_COPY:
        return target
    if cmdlet in PS_MOVE:
        return source + target
    return []


def _command_targets(cmd: SimpleCommand, dialect: Dialect) -> list[str]:
    plain, found = _split_redirections(cmd.words)
    # `do rm x`, `then touch f`, `{ rm x; }`, `timeout 5 rm x`: program_and_args sees through
    program, args = program_and_args(SimpleCommand(words=plain))
    if dialect == "powershell":
        program = PS_ONLY_ALIASES.get(program, program)
    if program in PS_WRITE_FIRST | PS_WRITE_ALL | PS_COPY | PS_MOVE:
        return found + _powershell_targets(program, args)
    return found + _unix_targets(program, args)


# The temp folder by the variable each shell gives it (guard-b15 6: a read-only subagent
# told to write under the temp folder wrote `$TEMP/x` and was refused for naming no target).
TEMP_FORMS = {
    "bash": re.compile(
        r"^(\$TMPDIR|\$TEMP|\$TMP|\$\{TMPDIR\}|\$\{TEMP\}|\$\{TMP\})(?=$|[\\/])"
    ),
    "powershell": re.compile(
        r"^(\$env:TEMP|\$env:TMP|\$\{env:TEMP\}|\$\{env:TMP\})(?=$|[\\/])",
        re.IGNORECASE,
    ),
}


def _posix_special(target: str, dialect: Dialect) -> str | None:
    """Paths that are not what they look like on Windows: "" for a Git Bash device (nothing
    to judge), the real temp folder for /tmp and for the shell's temp variable, None for an
    ordinary path."""
    form = TEMP_FORMS[dialect].match(target)
    if form:
        import tempfile

        return tempfile.gettempdir() + target[form.end() :]
    if dialect != "bash":
        return None
    low = target.lower()
    if low.startswith(POSIX_DEVICES) or low in ("/dev", "/proc"):
        return ""
    if low == "/tmp" or low.startswith("/tmp/"):
        import tempfile

        return tempfile.gettempdir() + target[4:]
    return None


def write_targets(command: str, dialect: Dialect, cwd: str) -> tuple[list[str], int]:
    """(absolute paths the command visibly writes, number of writes with an unknown target).
    See the module docstring for what is and is not seen."""
    try:
        cmds = parse(command, dialect)
    except ParseError:
        return [], 1
    paths: list[str] = []
    seen: set[str] = set()
    unresolved = 0
    for i, cmd in enumerate(cmds):
        raw = _command_targets(cmd, dialect)
        if not raw:
            continue
        from verification import _resolve, cwd_at

        here = cwd_at(cmds, i, cwd)
        for target in raw:
            if target.lower() in NOT_A_FILE:
                continue
            special = _posix_special(target, dialect)
            if special == "":
                continue
            text = special if special is not None else target
            if UNRESOLVABLE.search(text) or len(paths) >= MAX_TARGETS:
                unresolved += 1
                continue
            resolved = _resolve(here, text)
            if resolved not in seen:
                seen.add(resolved)
                paths.append(resolved)
    return paths, unresolved
