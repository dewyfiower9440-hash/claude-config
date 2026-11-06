"""PreToolUse guard: blocks destructive shell commands and access to secrets.

Exit code 2 blocks the tool call; stderr is shown to Claude as the reason.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Literal

# python -I drops the script dir; os.path instead of pathlib keeps this hook's startup fast
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

Dialect = Literal["bash", "powershell"]

# ---------------------------------------------------------------- secrets
SECRET_PATH_PATTERNS = [
    r"(^|[\\/])\.env(\.[^\\/]*)?$",  # .env, .env.local ... (examples allowed below)
    r"(^|[\\/])\.credentials\.json$",
    r"(^|[\\/])Migration_Secrets([\\/]|$)",
    r"\.(pem|key|pfx|p12|ppk|kdbx)$",
    r"(^|[\\/])id_(rsa|ed25519|ecdsa|dsa)$",
    r"(^|[\\/])\.ssh[\\/]",
    r"(^|[\\/])\.aws[\\/]credentials$",
    r"(^|[\\/])\.git-credentials$",
    r"(^|[\\/])\.netrc$",
    r"(^|[\\/])secrets?\.(json|ya?ml|toml)$",
]
SECRET_ALLOW = [r"\.env\.(example|sample|template)$", r"\.pub$"]

# Commands that dump secret material even without touching a file path.
SECRET_COMMANDS = [
    r"\bgh\s+auth\s+token\b",
    r"\bcmdkey\s+/list\b",
    r"Get-StoredCredential",
    r"GetNetworkCredential\(\)\.Password",
    r"\bkeyring\s+get\b",
    r"\bprintenv\b|\benv\s*$|Get-ChildItem\s+env:|\bgci\s+env:|\bls\s+env:",
]

# ---------------------------------------------------------------- destructive
DANGEROUS = [
    (
        r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+(/|~|\$HOME|/c/?|[A-Za-z]:[\\/]?)(\s|$|\*)",
        "recursive delete of a root or home directory",
    ),
    (r"\brm\s+-[a-zA-Z]*\s+.*--no-preserve-root", "rm --no-preserve-root"),
    (
        r"Remove-Item\b.*-Recurse.*\s['\"]?([A-Za-z]:[\\/]?|~|\$env:USERPROFILE|\$HOME)['\"]?(\s|$)",
        "recursive delete of a drive root or home directory",
    ),
    (
        r"Remove-Item\b.*\s['\"]?([A-Za-z]:[\\/]?|~|\$env:USERPROFILE|\$HOME)['\"]?\s.*-Recurse",
        "recursive delete of a drive root or home directory",
    ),
    (
        r"\b(rd|rmdir)\s+/s\b.*\s[A-Za-z]:[\\/]?(\s|$)",
        "recursive delete of a drive root",
    ),
    (r"\bdel\s+/[sqf].*\s[A-Za-z]:[\\/]\*?(\s|$)", "mass delete on a drive root"),
    (r"\bformat(\.com)?\s+[A-Za-z]:", "disk format"),
    (
        r"\b(Format-Volume|Clear-Disk|Initialize-Disk|Remove-Partition|Set-Partition)\b",
        "disk/partition operation",
    ),
    (r"\bdiskpart\b", "diskpart"),
    (r"\bmkfs(\.\w+)?\b|\bdd\s+.*of=/dev/", "raw disk write"),
    (r"\bgit\s+push\b.*(--force\b|-f\b|--force-with-lease\b|\s\+\w)", "force push"),
    (
        r"\bgit\s+reset\s+--hard\b",
        "git reset --hard (discards work; ask the user first)",
    ),
    (
        r"\bgit\s+clean\s+-[a-zA-Z]*f",
        "git clean -f (deletes untracked files; ask the user first)",
    ),
    (r"\bgit\s+filter-(branch|repo)\b", "history rewrite"),
    (r"\b(shutdown|Stop-Computer|Restart-Computer)\b", "shutdown/restart"),
    (
        r"\bbcdedit\b|\breg\s+delete\s+HKLM\b|Remove-Item\s+.*HKLM:",
        "boot/registry system change",
    ),
    (r"\bvssadmin\s+delete\b|\bwbadmin\s+delete\b", "backup/shadow copy deletion"),
    (r"\bcipher\s+/w\b|\bsdelete\b", "secure wipe"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (
        r"\b(curl|wget|iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b[^|]*\|\s*(ba|z)?sh\b",
        "pipe from internet to shell",
    ),
    (
        r"\b(iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b.*\|\s*(iex|Invoke-Expression)\b",
        "pipe from internet to iex",
    ),
    (
        r"\bdocker\s+system\s+prune\b.*(-a|--all).*--volumes|\bdocker\s+volume\s+prune\b",
        "docker volume wipe",
    ),
    (r"\bDROP\s+(DATABASE|SCHEMA)\b", "DROP DATABASE/SCHEMA"),
]


def block(reason: str) -> None:
    print(
        f"BLOCKED by guard hook: {reason}. If this is really intended, ask the user to run it themselves.",
        file=sys.stderr,
    )
    sys.exit(2)


def is_secret_path(path: str) -> bool:
    p = path.replace("\\", "/")
    if any(re.search(a, p, re.IGNORECASE) for a in SECRET_ALLOW):
        return False
    return any(re.search(s, p, re.IGNORECASE) for s in SECRET_PATH_PATTERNS)


# Search commands: their pattern argument is a content regex, not a file, so a pattern such
# as '.env' must not count as touching a secret file. Every other argument is still checked.
SEARCH_HEADS = {
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "findstr",
    "select-string",
    "sls",
}
PATTERN_FLAGS = {"-e", "--regexp", "-pattern"}
VALUE_FLAGS = {  # flags whose next token is a value, never the positional pattern
    "-A", "-B", "-C", "-m", "--max-count", "-g", "--glob", "-t", "--type", "-T",
    "--type-not", "-f", "--file", "-j", "--threads",
    "-path", "-literalpath", "-include", "-exclude", "-encoding", "-context",
}  # fmt: skip


def search_patterns(tokens: list[str]) -> set[int]:
    """Indexes of the pattern arguments when `tokens` is a grep-like command, else empty."""
    if not tokens:
        return set()
    head = tokens[0].lower().removesuffix(".exe")
    start = 1
    if head == "git" and len(tokens) > 1 and tokens[1] == "grep":
        start = 2
    elif head not in SEARCH_HEADS:
        return set()
    ps = head in ("select-string", "sls")
    found: set[int] = set()
    positional_open = True
    expect: str | None = None
    for i in range(start, len(tokens)):
        tok = tokens[i]
        if expect == "pattern":
            found.add(i)
            expect = None
        elif expect == "value":
            expect = None
        elif tok == "--":
            break  # everything after is a file operand
        elif tok.startswith("-") and len(tok) > 1:
            name, eq, _ = tok.partition("=")
            key = name.lower() if ps or name.startswith("--") else name
            if key in PATTERN_FLAGS or (ps and key == "-pattern"):
                positional_open = False
                if eq:
                    found.add(i)  # --regexp=PATTERN in one token
                else:
                    expect = "pattern"
            elif key in VALUE_FLAGS and not eq:
                expect = "value"
        elif positional_open:
            found.add(i)
            positional_open = False
    return found


def split_segment(segment: str) -> list[str]:
    import shlex  # lazy: only shell commands need it

    try:
        return shlex.split(
            segment, posix=False
        )  # posix=False keeps Windows backslashes
    except ValueError:  # unbalanced quotes: fall back to checking the raw text
        return [segment]


def tokens_look_secret(command: str) -> str | None:
    for segment in re.split(r"\|\|?|&&?|;|\n", command):
        tokens = split_segment(segment)
        skip = search_patterns(tokens)
        checked = " ".join(t for i, t in enumerate(tokens) if i not in skip)
        for tok in re.split(r"[\s'\"<>|;&()=,]+", checked):
            if tok and is_secret_path(tok):
                return tok
    return None


def staged_secrets() -> list[str]:
    import subprocess  # lazy: only `git commit` checks need it (~20 ms import)

    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [f for f in out.stdout.splitlines() if is_secret_path(f)]


def legacy_check(cmd: str) -> None:
    """Whole-string checks; used only when the command cannot be parsed (conservative)."""
    for pattern, reason in DANGEROUS:
        if re.search(pattern, cmd, re.IGNORECASE):
            block(reason)
    for pattern in SECRET_COMMANDS:
        if re.search(pattern, cmd, re.IGNORECASE):
            block("command would print secrets or credentials")
    hit = tokens_look_secret(cmd)
    if hit:
        block(f"command touches a secret file ({hit})")
    if re.search(r"\bgit\s+add\b.*(\s-f\b|--force)", cmd):
        block("git add --force can stage ignored secret files")
    if re.search(r"\bgit\s+commit\b", cmd):
        leaked = staged_secrets()
        if leaked:
            block(f"staged files look like secrets: {', '.join(leaked)} (unstage them)")


# ---------------------------------------------------------------- parsed checks
# Rules matched (re.match, anchored) against one simple command rendered as
# "<program> <args...>" after quote removal, so text inside another command's
# arguments (a commit message, an echo) can no longer trigger them.
COMMAND_RULES = [
    (
        r"rm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+(--\s+)?(/|~|\$HOME|/c/?|[A-Za-z]:[\\/]?)(\s|$|\*)",
        "recursive delete of a root or home directory",
    ),
    (r"rm\s+.*--no-preserve-root", "rm --no-preserve-root"),
    (
        r"(remove-item|ri)\b.*-recurse.*\s([A-Za-z]:[\\/]?|~|\$env:USERPROFILE|\$HOME)(\s|$)",
        "recursive delete of a drive root or home directory",
    ),
    (
        r"(remove-item|ri)\b.*\s([A-Za-z]:[\\/]?|~|\$env:USERPROFILE|\$HOME)\s.*-recurse",
        "recursive delete of a drive root or home directory",
    ),
    (r"(rd|rmdir)\s+/s\b.*\s[A-Za-z]:[\\/]?(\s|$)", "recursive delete of a drive root"),
    (r"(del|erase)\s+/[sqf].*\s[A-Za-z]:[\\/]\*?(\s|$)", "mass delete on a drive root"),
    (r"format\s+[A-Za-z]:", "disk format"),
    (
        r"(format-volume|clear-disk|initialize-disk|remove-partition|set-partition)\b",
        "disk/partition operation",
    ),
    (r"diskpart\b", "diskpart"),
    (r"mkfs(\.\w+)?\b|dd\s+.*of=/dev/", "raw disk write"),
    (r"git\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s\+\S)", "force push"),
    (
        r"git\s+reset\b.*\s--hard\b",
        "git reset --hard (discards work; ask the user first)",
    ),
    (
        r"git\s+clean\b.*\s-[a-zA-Z]*f",
        "git clean -f (deletes untracked files; ask the user first)",
    ),
    (r"git\s+filter-(branch|repo)\b", "history rewrite"),
    (r"(shutdown|stop-computer|restart-computer)\b", "shutdown/restart"),
    (
        r"bcdedit\b|reg\s+delete\s+HKLM\b|(remove-item|ri)\s+.*HKLM:",
        "boot/registry system change",
    ),
    (r"(vssadmin|wbadmin)\s+delete\b", "backup/shadow copy deletion"),
    (r"cipher\s+/w\b|sdelete\b", "secure wipe"),
    (
        r"docker\s+system\s+prune\b.*(-a|--all).*--volumes|docker\s+volume\s+prune\b",
        "docker volume wipe",
    ),
]
# Still matched against the whole raw text: SQL and .NET calls live inside arguments.
LINE_RULES = [
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\bDROP\s+(DATABASE|SCHEMA)\b", "DROP DATABASE/SCHEMA"),
    (
        r"Get-StoredCredential|GetNetworkCredential\(\)\.Password",
        "command would print secrets or credentials",
    ),
]
DOWNLOADERS = {"curl", "wget", "iwr", "invoke-webrequest", "irm", "invoke-restmethod"}
POSIX_SHELLS = {"sh", "bash", "zsh", "dash"}
PS_HOSTS = {"powershell", "pwsh"}
INTERPRETERS = (
    POSIX_SHELLS | PS_HOSTS | {"python", "python3", "py", "node", "ruby", "perl", "cmd"}
)
DATA_ARG_PROGRAMS = {
    "echo",
    "printf",
    "write-output",
    "write-host",
    "write-information",
    "write-verbose",
}
REDIRECTS = {">", ">>", ">|", ">&", "&>", "<"}
GIT_GLOBAL_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
}
MAX_DEPTH = 4


def git_subcommand(args: list[str]) -> list[str]:
    """Drop git's global options (`git -C repo push` -> `push`)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_GLOBAL_WITH_VALUE else 1
    return args[i:]


def message_value_indexes(program: str, args: list[str]) -> set[int]:
    """Argument indexes that are free text (commit messages, PR bodies, echo output)."""
    if program in DATA_ARG_PROGRAMS:
        return {
            i
            for i, a in enumerate(args)
            if a not in REDIRECTS and (i == 0 or args[i - 1] not in REDIRECTS)
        }
    data: set[int] = set()
    if program == "git":
        sub = git_subcommand(args)
        offset = len(args) - len(sub)
        if sub[:1] in (["commit"], ["tag"]):
            for j in range(1, len(sub)):
                a = sub[j]
                if a == "--":
                    break
                if a in ("-m", "--message") or (
                    re.fullmatch(r"-[a-zA-Z]*m", a) and not a.startswith("--")
                ):
                    data.add(offset + j + 1)
                elif a.startswith("--message=") or (re.fullmatch(r"-m.+", a)):
                    data.add(offset + j)
    elif program == "gh" and args[:1] and args[0] in ("pr", "issue", "release"):
        for j, a in enumerate(args):
            if a in ("--title", "-t", "--body", "-b", "--notes", "-n"):
                data.add(j + 1)
            elif re.match(r"--(title|body|notes)=", a):
                data.add(j)
    return data


def nested_scripts(program: str, args: list[str]) -> list[tuple[str, Dialect]]:
    """Command strings another shell will execute: `bash -c X`, `powershell -Command X`, ..."""
    import base64

    if program in POSIX_SHELLS:
        for j, a in enumerate(args):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a) and j + 1 < len(args):
                return [(args[j + 1], "bash")]
        return []
    if program in PS_HOSTS:
        for j, a in enumerate(args):
            low = a.lower()
            if low in ("-command", "-c", "-com", "-comm", "-comma", "-comman"):
                return [(" ".join(args[j + 1 :]), "powershell")]
            if low in ("-encodedcommand", "-enc", "-e", "-ec") and j + 1 < len(args):
                try:
                    return [
                        (
                            base64.b64decode(args[j + 1]).decode("utf-16-le"),
                            "powershell",
                        )
                    ]
                except ValueError:  # undecodable payload: treat as hostile
                    block("unreadable -EncodedCommand payload")
            if not a.startswith("-"):
                return [
                    (" ".join(args[j:]), "powershell")
                ]  # `powershell "Remove-Item ..."`
        return []
    if program == "cmd":
        for j, a in enumerate(args):
            if a.lower() in ("/c", "/k"):
                return [(" ".join(args[j + 1 :]), "bash")]
        return []
    if program == "eval":
        return [(" ".join(args), "bash")]
    if program in ("iex", "invoke-expression") and args:
        return [(" ".join(args), "powershell")]
    return []


def check_secret_command(program: str, args: list[str], first_word: str) -> None:
    lowered = [a.lower() for a in args]
    if (
        (program == "gh" and lowered[:2] == ["auth", "token"])
        or (program == "cmdkey" and "/list" in lowered)
        or (program == "keyring" and lowered[:1] == ["get"])
        or program == "printenv"
        or (program in ("env", "") and _basename(first_word) == "env")
        or (
            program in ("get-childitem", "gci", "ls", "dir")
            and any(a.startswith("env:") for a in lowered)
        )
    ):
        block("command would print secrets or credentials")


def _basename(word: str) -> str:
    from shellparse import SimpleCommand, program_and_args

    return program_and_args(SimpleCommand(words=[word]))[0]


def check_parsed(command: str, dialect: Dialect, depth: int = 0) -> None:
    from shellparse import ParseError, parse, program_and_args

    if depth > MAX_DEPTH:
        block("too deeply nested shell command")
    try:
        commands = parse(command, dialect)
    except ParseError:
        legacy_check(command)
        return
    pipeline: list[str] = []
    piped_heredocs: list[str] = []
    for cmd in commands:
        program, args = program_and_args(cmd)
        canonical = " ".join(
            [program, *(git_subcommand(args) if program == "git" else args)]
        )
        for pattern, reason in COMMAND_RULES:
            if re.match(pattern, canonical, re.IGNORECASE):
                block(reason)
        check_secret_command(program, args, cmd.words[0] if cmd.words else "")

        pipeline.append(program)
        if cmd.heredoc is not None and cmd.op_after == "|":
            piped_heredocs.append(
                cmd.heredoc
            )  # `cat <<EOF | bash`: the body is executed
        if cmd.op_after != "|":
            shells_downstream = [
                p
                for p in pipeline[1:]
                if p in POSIX_SHELLS | PS_HOSTS | {"iex", "invoke-expression"}
            ]
            if any(p in DOWNLOADERS for p in pipeline) and shells_downstream:
                block("pipe from internet to a shell")
            for body in piped_heredocs if shells_downstream else []:
                ps = shells_downstream[0] in PS_HOSTS | {"iex", "invoke-expression"}
                check_parsed(body, "powershell" if ps else "bash", depth + 1)
            pipeline = []
            piped_heredocs = []

        for script, sub_dialect in nested_scripts(program, args):
            check_parsed(script, sub_dialect, depth + 1)
        if cmd.heredoc is not None and program in INTERPRETERS:
            if program in POSIX_SHELLS:
                check_parsed(cmd.heredoc, "bash", depth + 1)
            elif program in PS_HOSTS:
                check_parsed(cmd.heredoc, "powershell", depth + 1)
            else:
                hit = tokens_look_secret(cmd.heredoc)
                if hit:
                    block(f"command touches a secret file ({hit})")

        data = message_value_indexes(program, args)
        data |= {k - 1 for k in search_patterns([program, *args]) if k >= 1}
        for k, arg in enumerate(args):
            if k in data:
                continue
            for tok in re.split(r"[\s'\"<>|;&()=,]+", arg):
                if tok and is_secret_path(tok):
                    block(f"command touches a secret file ({tok})")
        if is_secret_path(cmd.words[0]) if cmd.words else False:
            block(f"command touches a secret file ({cmd.words[0]})")

        if program == "git":
            sub = git_subcommand(args)
            if sub[:1] == ["add"] and ("-f" in sub or "--force" in sub):
                block("git add --force can stage ignored secret files")
            if sub[:1] == ["commit"]:
                leaked = staged_secrets()
                if leaked:
                    block(
                        f"staged files look like secrets: {', '.join(leaked)} (unstage them)"
                    )


def check_command(cmd: str, dialect: Dialect = "bash") -> None:
    for pattern, reason in LINE_RULES:
        if re.search(pattern, cmd, re.IGNORECASE):
            block(reason)
    check_parsed(cmd, dialect)


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (
        ValueError,
        OSError,
    ):  # malformed payload: never block on hook input problems
        sys.exit(0)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if tool in ("Bash", "PowerShell"):
        check_command(
            ti.get("command", ""), "powershell" if tool == "PowerShell" else "bash"
        )
    elif tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if path and is_secret_path(path):
            block(f"access to secret file {path}")
    elif tool in ("Grep", "Glob"):
        # Grep's "pattern" is a regex over content, not a path; only Glob's pattern is a path.
        keys = ("path", "pattern") if tool == "Glob" else ("path", "glob")
        target = " ".join(str(ti.get(k, "")) for k in keys)
        hit = tokens_look_secret(target)
        if hit:
            block(f"search targets a secret file ({hit})")
    sys.exit(0)


if __name__ == "__main__":
    main()
