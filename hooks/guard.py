"""PreToolUse guard: blocks destructive shell commands and access to secrets, keeps the
read-only subagents read-only, keeps the approval folders from being read through the shell,
and asks before a command ends processes.

Exit code 2 blocks the tool call; stderr is shown to Claude as the reason. A question to the
user is a PreToolUse permissionDecision of "ask" on stdout.
"""

from __future__ import annotations

import itertools
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
    # discipline's signed plan approvals and their HMAC key: hooks only, never tools
    r"(^|[\\/])\.claude[\\/]discipline([\\/]|$)",
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


def ask(reason: str) -> None:
    """Hand the decision to the user (PreToolUse permissionDecision "ask")."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": f"guard hook: {reason}. Confirm that this "
                    "is what you want.",
                }
            }
        )
    )
    sys.exit(0)


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


def staged_secrets(cwd: str | None = None) -> list[str]:
    """Staged files that look like secrets, in the repository the commit runs in (`cwd`:
    the session's folder after the command's own `cd`s, not the hook process's folder)."""
    import subprocess  # lazy: only `git commit` checks need it (~20 ms import)

    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=cwd if cwd and os.path.isdir(cwd) else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [f for f in out.stdout.splitlines() if is_secret_path(f)]


def _git_folder(args: list[str], cwd: str) -> str:
    """Where a git command runs: `cwd`, or what its `-C <path>` options lead to."""
    from verification import _resolve

    folder = cwd
    for k, a in enumerate(args[:-1]):
        if a == "-C":
            folder = _resolve(folder, args[k + 1])
    return folder


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


# git queries that can name a file but never print its content: sub-command -> (flags
# allowed as they are, prefixes of `--flag=value` forms, flags that take the next word).
# An allow-list: any other flag (-p, -S, -G, -L, --cc, --word-diff, --output, ...) keeps
# the ordinary secret-file check.
GIT_NAME_QUERIES: dict[str, tuple[set[str], tuple[str, ...], set[str]]] = {
    "log": (
        {
            "--all",
            "--oneline",
            "--follow",
            "--name-only",
            "--name-status",
            "--stat",
            "--reverse",
            "--no-merges",
            "--full-history",
        },
        (
            "--diff-filter=",
            "--format=",
            "--pretty=",
            "--date=",
            "--since=",
            "--until=",
            "--after=",
            "--before=",
            "--max-count=",
        ),
        {"-n"},
    ),
    "ls-files": (
        {
            "--error-unmatch",
            "-c",
            "--cached",
            "-o",
            "--others",
            "-i",
            "--ignored",
            "--exclude-standard",
            "-z",
        },
        (),
        set(),
    ),
    "check-ignore": (
        {"-v", "--verbose", "-q", "--quiet", "-n", "--non-matching", "--no-index"},
        (),
        set(),
    ),
}


def git_names_only(cmd: object, args: list[str]) -> bool:
    """True for one plain `git log` / `git ls-files` / `git check-ignore` whose arguments
    may name a secret file without the command being able to print it: git called directly
    (no VAR=value prefix, no wrapper), no global option but `-C <dir>`, only allow-listed
    flags, nothing substituted or redirected, not nested, and its output not piped on."""
    words = getattr(cmd, "words", None) or []
    if (
        getattr(cmd, "nested", True)
        or getattr(cmd, "heredoc", None) is not None
        or getattr(cmd, "op_after", None) in ("|", "|&")
        or len(words) != len(args) + 1
        or any(re.search(r"[<>$`]", a) or a in REDIRECTS for a in args)
    ):
        return False
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] != "-C" or i + 1 >= len(args):
            return False
        i += 2
    sub = args[i:]
    if not sub or sub[0] not in GIT_NAME_QUERIES:
        return False
    exact, prefixes, valued = GIT_NAME_QUERIES[sub[0]]
    j = 1
    while j < len(sub):
        a = sub[j]
        if a == "--":
            # only paths follow; a `rev:path` word there is still not a plain name
            return not any(":" in rest for rest in sub[j + 1 :])
        if not a.startswith("-") and ":" in a:
            return False  # `rev:path`: left to the ordinary check
        if a.startswith("-"):
            if a in valued and sub[0] == "log":
                if j + 1 >= len(sub) or not sub[j + 1].isdigit():
                    return False
                j += 1
            elif not (
                a in exact
                or a.startswith(prefixes)
                or (sub[0] == "log" and re.fullmatch(r"-\d+", a))
            ):
                return False
        j += 1
    return True


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
    words = [program, *(_basename(a) for a in args)]
    if (
        (program == "gh" and lowered[:2] == ["auth", "token"])
        # `gh auth status --show-token` / `-t` prints the token like `gh auth token`
        or (
            program == "gh"
            and lowered[:2] == ["auth", "status"]
            and any(a in ("--show-token", "-t") for a in lowered)
        )
        # `git credential fill|get` asks the helper for a stored password and prints it
        or (
            program == "git"
            and git_subcommand(args)[:1] == ["credential"]
            and any(a in ("fill", "get") for a in git_subcommand(args)[1:2])
        )
        or (program == "cmdkey" and "/list" in lowered)
        # `keyring get`, also as `python -m keyring get`, `uv run keyring get`
        or any(
            a == "keyring" and words[k + 1] == "get" for k, a in enumerate(words[:-1])
        )
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


# ---------------------------------------------------------------- devenv-fix-m
# Who runs the tool call and where. A subagent's tool calls reach this hook with its
# agent_type (the hooks of settings.json run for subagents as for the main session).
_ctx: dict[str, str | None] = {"agent": None, "cwd": None}
_asks: list[str] = []  # questions for the user; a block anywhere in the command wins

# Subagents whose definitions say "read-only". Their definitions have no edit tool; this
# makes the shell follow (2026-11-18: a reviewer killed every grep on the machine by name).
READ_ONLY_AGENTS = {"code-reviewer", "security-auditor", "test-runner"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_ONLY_REASON = (
    "the read-only subagent ({agent}) may not {what}; say so in your report and leave it "
    "to the main session"
)
KILLERS = {"kill", "pkill", "killall", "taskkill", "tskill", "stop-process", "spps"}
GIT_WRITES = {
    "add", "am", "apply", "bisect", "checkout", "cherry-pick", "clean", "clone", "commit",
    "fetch", "gc", "init", "merge", "mv", "pull", "push", "rebase", "reset", "restore",
    "revert", "rm", "switch", "update-index", "update-ref",
}  # fmt: skip
GIT_READ_FORMS = {  # subcommands that write unless used in one of these read forms
    "branch": {"", "-a", "-r", "-v", "-vv", "-l", "--list", "--all", "--remotes",
               "--show-current", "--contains", "--merged", "--no-merged"},
    "stash": {"list", "show"},
    "tag": {"", "-l", "--list", "-n", "--contains"},
    "worktree": {"list"},
    "remote": {"", "-v", "show", "get-url"},
    "config": {"--get", "--get-all", "--list", "-l", "--show-origin"},
    "notes": {"", "list", "show"},
}  # fmt: skip


def read_only_agent() -> str | None:
    agent = _ctx["agent"]
    return agent if agent in READ_ONLY_AGENTS else None


def ends_processes(program: str, args: list[str]) -> bool:
    if program in KILLERS:
        return True
    low = [a.lower() for a in args]
    return (
        program == "wmic"
        and "process" in low
        and any(a in ("delete", "terminate") or "terminate" in a for a in low)
    )


def git_writes(sub: list[str]) -> bool:
    """True when the git subcommand (as git_subcommand gives it) changes the repository."""
    if not sub:
        return False
    head, rest = sub[0], sub[1:]
    if head in GIT_WRITES:
        return True
    forms = GIT_READ_FORMS.get(head)
    if forms is None:
        return False
    first = rest[0] if rest else ""
    if first not in forms:
        return True
    # `git branch --list x` reads; `git branch -l` followed by nothing else reads too
    return head == "branch" and any(
        a in ("-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy")
        for a in rest
    )


# The folders of the approval store and of the session records. settings.json denies the
# Read tool for one of them; a shell command reads them all the same.
HOME_DIR = os.path.expanduser("~")
CLAUDE_HOME = os.path.join(HOME_DIR, ".claude")


def _temp_root() -> str:
    """The temp folder as discipline.py finds it (its temp_root), without importing it."""
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value and os.path.isdir(value):
            return value
    import tempfile  # fallback only; slow import

    return tempfile.gettempdir()


# The approval store, the hooks' records, and the session state the discipline hook keeps
# under the temp folder (what is unverified, which approval is active: devenv-final 14).
PROTECTED = [
    *(os.path.join(CLAUDE_HOME, name) for name in ("discipline", "state")),
    os.path.join(_temp_root(), "claude-discipline"),
]
PROTECTED_REASON = (
    "{path} is inside a protected folder of ~/.claude (discipline, state) or the hooks' "
    "session state under the temp folder: it is not read or written through a tool"
)
SWEEP_REASON = (
    "a recursive command starting at {root} walks through the protected folders of "
    "~/.claude (discipline, state) or the hooks' session state under the temp folder: "
    "start from a subfolder (hooks, tests, docs, ...) or use `git grep`"
)
_HOME_FORM = re.compile(
    r"^(~|\$HOME|\$\{HOME\}|%USERPROFILE%|\$env:USERPROFILE|\$\{env:USERPROFILE\}|\$env:HOME)"
    r"(?=$|[\\/])",
    re.IGNORECASE,
)
# The names a shell gives the temp folder (Git Bash's /tmp is the Windows temp folder).
# Without them the session state under it was protected under its literal path only.
_TEMP_FORM = re.compile(
    r"^(/tmp|\$TMPDIR|\$TEMP|\$TMP|\$\{TMPDIR\}|\$\{TEMP\}|\$\{TMP\}|%TEMP%|%TMP%"
    r"|\$env:TEMP|\$env:TMP|\$\{env:TEMP\}|\$\{env:TMP\})(?=$|[\\/])",
    re.IGNORECASE,
)
# The state folder by name, in a word whose start cannot be told (another variable).
_STATE_NAME = re.compile(r"(^|[\\/])claude-discipline($|[\\/])", re.IGNORECASE)
# guard-b15: the profile folders a shell names by variable (`$env:APPDATA\..\..\.claude`),
# resolved from the environment like the home forms above.
_ENV_FORM = re.compile(
    r"^(?:\$env:|\$\{env:|\$\{|\$|%)(APPDATA|LOCALAPPDATA)[}%]?(?=$|[\\/])",
    re.IGNORECASE,
)


def _env_value(m: re.Match[str]) -> str:
    value = os.environ.get(m.group(1).upper())
    return value if value else m.group(0)  # unset: left as it is (then not a path)


# guard-b15 2: Bash expands `{a,b}` before anything else, so one word can name several
# paths (`~/.claude/{discipline,x}/key`). The innermost brace with a comma first; a brace
# without a comma is literal, as in Bash.
BRACE_LIMIT = 64


def _first_brace(word: str) -> tuple[int, list[int], int] | None:
    """(start, top-level comma positions, end) of the leftmost `{a,b}` in `word` that has a
    comma at its own level, braces inside it left as they are; None when there is none."""
    start = word.find("{")
    while start != -1:
        depth, commas = 0, []
        for i in range(start, len(word)):
            c = word[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    if commas:
                        return start, commas, i
                    break
            elif c == "," and depth == 1:
                commas.append(i)
        start = word.find("{", start + 1)
    return None


def brace_alternatives(word: str, limit: int = BRACE_LIMIT) -> list[str]:
    """The words Bash makes of `word` by brace expansion (the word itself when it has no
    `{a,b}`), at most `limit` of them: the leftmost brace first, each alternative then
    expanded in turn (`{a,b{1,2}}` -> a, b1, b2)."""
    found = _first_brace(word)
    if found is None:
        return [word]
    start, commas, end = found
    out: list[str] = []
    bounds = [start, *commas, end]
    for a, b in itertools.pairwise(bounds):
        rest = word[:start] + word[a + 1 : b] + word[end + 1 :]
        for expanded in brace_alternatives(rest, limit):
            out.append(expanded)
            if len(out) >= limit:
                return out
    return out


def _words(arg: str, dialect: Dialect) -> list[str]:
    """What the shell makes of one argument before the program sees it."""
    return brace_alternatives(arg) if dialect == "bash" and "{" in arg else [arg]


# Programs whose plain-word arguments are files or folders. For any other program a word
# without a path separator (`state` inside a line of Python, a branch name, a subcommand) is
# not taken for a path: only words that look like one are.
FILE_PROGRAMS = {
    "cat", "ls", "dir", "type", "head", "tail", "less", "more", "wc", "cp", "mv", "rm",
    "rmdir", "find", "tree", "du", "tar", "zip", "7z", "7za", "stat", "file", "sort", "uniq",
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr", "sed", "awk", "cut", "xxd",
    "strings", "od", "nl", "tac", "diff", "cmp", "xcopy", "robocopy", "start", "explorer",
    "notepad", "code", "get-content", "gc", "get-childitem", "gci", "get-item", "gi",
    "copy-item", "cpi", "copy", "move-item", "remove-item", "select-string", "sls",
    "test-path", "resolve-path", "get-filehash", "import-csv", "compress-archive",
    "cd", "pushd", "chdir", "set-location", "sl",
}  # fmt: skip


def as_path(word: str, cwd: str, bare: bool = True) -> str | None:
    """The absolute path a command word names, or None when it names none that can be told
    (a flag, a variable, a URL). A glob counts as the folder it starts in. With `bare`
    false, a word that does not look like a path (no separator, no home form, not `.` or
    `..`) is not one."""
    from verification import _resolve

    text = word.strip("'\"")
    if not text or text.startswith("-") or "://" in text:
        return None
    looks = bool(_HOME_FORM.match(text)) or "/" in text or "\\" in text
    if not bare and not looks and text not in (".", ".."):
        return None
    text = _HOME_FORM.sub(lambda _m: HOME_DIR, text)
    text = _TEMP_FORM.sub(lambda _m: _temp_root(), text)
    text = _ENV_FORM.sub(_env_value, text)
    cut = re.search(r"[*?\[]", text)
    if cut:
        text = text[: cut.start()]
        text = text[: max(text.rfind("/"), text.rfind("\\")) + 1] or "."
    if re.search(r"[$%`]", text):
        return None
    try:
        return _resolve(cwd, text)
    except (ValueError, OSError):
        return None


def in_protected(path: str) -> bool:
    from verification import _inside

    return any(_inside(path, root) for root in PROTECTED)


def above_protected(path: str) -> bool:
    """True for a folder that holds a protected folder somewhere below it: ~/.claude and
    every folder above it, the temp folder (guard-b15: the session state is under it) and
    every folder above that."""
    from verification import _inside

    return any(_inside(root, path) for root in PROTECTED)


def glob_reaches_protected(word: str, cwd: str, walks: bool = True) -> bool | None:
    """For a word with a pattern in it (`*.py`, `s*`, `~/.claude/*`): whether the pattern
    can match the way down to a protected folder. None when the word has no pattern.
    `walks`: the word is where a command that walks a tree starts, so matching the first
    folder on the way is enough. Otherwise the word names files, and every part of the
    pattern must match the folder at its depth: `disc*/key` reaches ~/.claude/discipline
    from ~/.claude, while `/*/*.jsonl` (a file two levels below the root) does not reach
    C:\\Users\\<name>\\.claude\\state."""
    import fnmatch

    from verification import _inside, _resolve

    text = word.strip("'\"").rstrip(",")
    cut = re.search(r"[*?\[]", text)
    if not cut:
        return None
    split = max(text.rfind("/", 0, cut.start()), text.rfind("\\", 0, cut.start())) + 1
    folder = as_path(text[:split] or ".", cwd)
    parts = [p for p in re.split(r"[\\/]", text[split:]) if p]
    pattern = parts[0] if parts else "*"
    if folder is None:
        return False
    start = _resolve(folder, ".")
    if in_protected(start):
        return True  # the pattern starts inside a protected folder
    for root in PROTECTED:
        if not _inside(root, start) or os.path.normcase(root) == os.path.normcase(
            start
        ):
            continue  # this protected folder is not below the folder the pattern starts in
        down = [d.lower() for d in re.split(r"[\\/]", os.path.relpath(root, start))]
        if walks:
            if fnmatch.fnmatch(down[0], pattern.lower()):
                return True
            continue
        if "**" in parts[: len(down)]:
            return True  # any depth
        if len(parts) >= len(down) and all(
            fnmatch.fnmatch(d, p.lower()) for d, p in zip(down, parts, strict=False)
        ):
            return True
    return False


# devenv-final 19: a pattern is not a name, so `cat .en*` passed the name rules. Names a
# pattern is tried against (the secret files the rules above know by name); a pattern
# counts only when its literal characters make up a real part of the name it matches, so
# `*`, `*.json` or `s*` are not taken for an attempt on every secret there is.
SECRET_NAMES = (
    ".env", ".env.local", ".env.production", ".env.development", ".credentials.json",
    ".git-credentials", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "secrets.json", "secret.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    "credentials", "Migration_Secrets",
)  # fmt: skip
GLOB_MIN_LITERALS = 3
# Share of the name the pattern spells out: `.en*` (3 of 4) and `id_*` (3 of 6) count,
# `*.json` (5 of the 12 of secrets.json) does not. A pattern that starts with a dot is
# rarer and counts sooner (`.git-c*`), but `.git*` is not an attempt on .git-credentials.
GLOB_MIN_SHARE = 0.5
GLOB_MIN_SHARE_DOT = 0.3


def glob_reaches_secret(word: str) -> str | None:
    """The secret file name a pattern word (`.en*`, `./.e[n]v`, `id_*`) can match, or None.
    As in a shell, a pattern that does not start with a dot does not match a dot file."""
    import fnmatch

    text = word.strip("'\"").rstrip(",")
    if not re.search(r"[*?\[]", text):
        return None
    for segment in re.split(r"[\\/]+", text):
        if not re.search(r"[*?\[]", segment):
            continue
        literals = len(re.sub(r"\[[^\]]*\]|[*?]", "", segment))
        if literals < GLOB_MIN_LITERALS:
            continue
        for name in SECRET_NAMES:
            if name.startswith(".") and not segment.startswith("."):
                continue
            share = GLOB_MIN_SHARE_DOT if name.startswith(".") else GLOB_MIN_SHARE
            if (
                literals >= share * len(name)
                and fnmatch.fnmatchcase(name.lower(), segment.lower())
                and not any(re.search(p, name, re.IGNORECASE) for p in SECRET_ALLOW)
            ):
                return name
    return None


SCRIPT_PROGRAMS = {"sed", "awk", "gawk", "perl"}  # their first plain word is a script


def script_indexes(program: str, args: list[str]) -> set[int]:
    """The arguments of sed, awk and the like that are program text, not files."""
    if program not in SCRIPT_PROGRAMS:
        return set()
    out = {
        k + 1 for k, a in enumerate(args) if a in ("-e", "--expression", "-f", "--file")
    }
    if not out:
        first = next((k for k, a in enumerate(args) if not a.startswith("-")), None)
        if first is not None:
            out.add(first)
    # whatever has braces or statement ends in it is program text wherever it stands
    # (`awk -F : '/x/{getline}'`)
    return out | {k for k, a in enumerate(args) if re.search(r"[{};]", a)}


def _flag_letters(args: list[str]) -> str:
    return "".join(a[1:] for a in args if re.fullmatch(r"-[A-Za-z]+", a))


def _ps_switch(args: list[str], name: str) -> bool:
    """A PowerShell switch given in full or by an unambiguous-looking prefix (-Recurse, -r)."""
    return any(
        len(a) >= 2 and a.startswith("-") and name.startswith(a[1:].lower())
        for a in args
    )


PS_VALUED = {"-filter", "-include", "-exclude", "-depth", "-pattern", "-destination",
             "-name", "-attributes", "-encoding"}  # fmt: skip
SWEEP_ALWAYS = {"rg", "find", "tree", "du", "fd", "fdfind", "ag", "ack"}
ARCHIVERS = {"tar", "zip", "7z", "7za", "compress-archive", "xcopy", "robocopy"}


def sweep_starts(program: str, args: list[str], dialect: Dialect) -> list[str] | None:
    """Where a command that walks a folder tree starts: the words naming its start folders,
    [] for "the current folder", None when the command does not walk a tree."""
    letters = _flag_letters(args)
    ps = dialect == "powershell"
    skip: set[int] = set()
    for k, a in enumerate(args):
        low = a.lower()
        if low in VALUE_FLAGS or low in PATTERN_FLAGS or (ps and low in PS_VALUED):
            skip.add(k + 1)
    skip |= {k - 1 for k in search_patterns([program, *args]) if k >= 1}
    # `/s` is a switch for the Windows programs (and for everything in PowerShell); in a
    # Bash line `/tmp` or `/etc` is a path (guard-b15: `grep -r x /tmp` named no start).
    switches = ps or program in ("findstr", "xcopy", "robocopy", "dir", "rd", "rmdir")
    words = [
        a
        for k, a in enumerate(args)
        if k not in skip
        and not a.startswith("-")
        and not (switches and re.fullmatch(r"/[A-Za-z?]+", a))
    ]
    default_here = True
    if program in ("grep", "egrep", "fgrep"):
        walks = (
            "r" in letters
            or "R" in letters
            or any(a in ("--recursive", "--dereference-recursive") for a in args)
        )
    elif program in SWEEP_ALWAYS:
        walks = True
        if program == "find":  # roots come before the first expression
            cut = next(
                (k for k, a in enumerate(args) if a.startswith(("-", "(", "!"))),
                len(args),
            )
            words = [a for a in args[:cut]]
            # `find X -maxdepth 1` lists X's entries like `ls X`: it walks nothing below
            if any(
                a == "-maxdepth" and args[k + 1 : k + 2] in (["0"], ["1"])
                for k, a in enumerate(args)
            ):
                return None
        elif program in ("fd", "fdfind"):
            words = words[1:]  # the first word is the pattern
    elif program in ("ls", "dir", "gci", "get-childitem"):
        walks = (
            _ps_switch(args, "recurse")
            if ps
            else "R" in letters
            or "--recursive" in args
            or any(a.lower() == "/s" for a in args)
        )
    elif program == "findstr":
        walks = any(a.lower() == "/s" for a in args)
    elif program in ("cp", "copy-item", "cpi", "copy"):
        walks = (
            _ps_switch(args, "recurse")
            if ps
            else any(c in letters for c in "rRa")
            or "--recursive" in args
            or "--archive" in args
        )
        words, default_here = words[:1] if ps else words[:-1], False
    elif program in ARCHIVERS:
        walks, default_here = True, False
        if program in ("xcopy", "robocopy"):
            words = words[:1]
    else:
        return None
    if not walks:
        return None
    return words if words or not default_here else []


# devenv-final 18: what a recursive delete names is resolved like any path (`~/`, `~/*`,
# `$HOME/`, `/c/Users/x`, a `cd` before it) instead of being matched as text.
PROJECTS_ROOT = os.path.normpath(os.environ.get("CLAUDE_PROJECTS_ROOT") or "C:/dev")
DELETE_REASON = (
    "recursive delete of {path}: the home folder, the projects folder, a drive root, or a "
    "folder above them"
)


def _delete_targets(program: str, args: list[str], dialect: Dialect) -> list[str]:
    """The words a recursive delete names ([] when the command is not one)."""
    low = [a.lower() for a in args]
    if program == "cmd" and "/c" in low:  # cmd /c rd /s /q X
        rest = args[low.index("/c") + 1 :]
        return _delete_targets(_basename(rest[0]), rest[1:], dialect) if rest else []
    if program in ("rd", "rmdir") and "/s" in low:
        return [a for a in args if not re.fullmatch(r"/[A-Za-z]", a)]
    if dialect == "powershell" and program in (
        "remove-item", "ri", "rm", "rmdir", "rd", "del", "erase",
    ):  # fmt: skip
        if not _ps_switch(args, "recurse"):
            return []
        from shellwrites import _powershell_targets

        return _powershell_targets("remove-item", args)
    if program == "rm":
        letters = _flag_letters(args)
        if "r" in letters or "R" in letters or "--recursive" in args:
            from shellwrites import _positionals

            return _positionals(args, set())
    return []


def check_recursive_delete(
    program: str, args: list[str], dialect: Dialect, cwd: str
) -> None:
    from verification import _inside, _resolve

    for word in (
        w
        for target in _delete_targets(program, args, dialect)
        for w in _words(target, dialect)
    ):
        text = re.sub(r"[\\/]\*+$", "", word.strip("'\"")) or word  # X/* empties X
        text = _HOME_FORM.sub(lambda _m: HOME_DIR, text)
        if re.search(r"[$%`*?\[]", text):
            continue  # cannot be told (the text rules above still judge `$HOME`, `~`)
        try:
            path = _resolve(cwd, text)
        except (ValueError, OSError):
            continue
        if any(
            _inside(keep, path) for keep in (HOME_DIR, PROJECTS_ROOT)
        ) or re.fullmatch(r"[A-Za-z]:[\\/]*", path):
            block(DELETE_REASON.format(path=word))


def check_confirmations(program: str, args: list[str]) -> None:
    """Commands that throw work away or act on every match: the user's to confirm."""
    if program == "find":
        acts = [
            a for a in args if a in ("-delete", "-exec", "-execdir", "-ok", "-okdir")
        ]
        if acts:
            _asks.append(f"`find {acts[0]}` acts on every file it matches")
        return
    if program != "git":
        return
    sub = git_subcommand(args)
    verb, rest = (sub[0] if sub else ""), sub[1:]
    everything = any(a in (".", ":/", "*") for a in rest)
    if verb == "switch" and any(
        a in ("--discard-changes", "-f", "--force") for a in rest
    ):
        _asks.append("`git switch --discard-changes` throws away uncommitted changes")
    elif verb == "checkout" and everything:
        _asks.append("`git checkout -- .` throws away every uncommitted change")
    elif (
        verb == "restore"
        and everything
        and not ("--staged" in rest and "--worktree" not in rest and "-W" not in rest)
    ):
        _asks.append("`git restore .` throws away every uncommitted change")
    elif verb == "stash" and rest[:1] in (["drop"], ["clear"]):
        _asks.append(f"`git stash {rest[0]}` deletes stashed work")
    elif verb == "push" and (
        any(a in ("--mirror", "--delete", "-d") for a in rest)
        or any(re.fullmatch(r":[^:\s]+", a) for a in rest)
    ):
        _asks.append("this `git push` deletes or overwrites remote branches")


def check_isolation(
    program: str, args: list[str], dialect: Dialect, cwd: str, data: set[int]
) -> None:
    """The rules of devenv-fix-m for one simple command at `cwd`; `data` are the indexes of
    arguments that are message text or search patterns, not paths."""
    agent = read_only_agent()
    if ends_processes(program, args):
        if agent:
            block(READ_ONLY_REASON.format(agent=agent, what="end processes"))
        _asks.append(f"`{program}` ends processes")
    if agent and program == "git" and git_writes(git_subcommand(args)):
        block(READ_ONLY_REASON.format(agent=agent, what="change a git repository"))
    data = data | script_indexes(program, args)
    for k, arg in enumerate(args):
        if k in data:
            continue
        for tok in (
            t for w in _words(arg, dialect) for t in re.split(r"[\s'\"<>|;&()=,]+", w)
        ):
            path = as_path(tok, cwd, program in FILE_PROGRAMS) if tok else None
            if path and in_protected(path):
                block(PROTECTED_REASON.format(path=tok))
            # a variable the guard cannot expand, in front of the state folder's name
            if (
                tok
                and path is None
                and re.search(r"[$%`]", tok)
                and _STATE_NAME.search(tok)
            ):
                block(PROTECTED_REASON.format(path=tok))
            # a pattern that can match a protected folder, or a secret file by name
            pathlike = program in FILE_PROGRAMS or "/" in tok or "\\" in tok
            if tok and pathlike and glob_reaches_protected(tok, cwd, walks=False):
                block(PROTECTED_REASON.format(path=tok))
            hit = glob_reaches_secret(tok) if tok and pathlike else None
            if hit:
                block(
                    f"command touches a secret file (the pattern {tok} matches {hit})"
                )
    check_recursive_delete(program, args, dialect, cwd)
    check_confirmations(program, args)
    starts = sweep_starts(program, args, dialect)
    if starts is None:
        return
    if dialect == "powershell":
        # a word that is all pattern (`*vm*bundle*`) is a filter there, not a start folder
        named = [
            w for w in starts if not re.fullmatch(r"[^\\/]*[*?][^\\/]*", w.rstrip(","))
        ]
        if starts and not named:
            return
        starts = named
    for word in starts or ["."]:
        reaches = glob_reaches_protected(word, cwd)
        if reaches is False:
            continue  # a pattern that matches neither folder nor the way to them
        path = as_path(word, cwd)
        if path and above_protected(path):
            block(SWEEP_REASON.format(root=word if starts else "the current folder"))


def check_read_only_writes(command: str, dialect: Dialect, cwd: str) -> None:
    """A read-only subagent writes nothing a command line shows, outside the temp folder."""
    agent = read_only_agent()
    if not agent:
        return
    import tempfile

    from shellwrites import write_targets
    from verification import _inside

    targets, unknown = write_targets(command, dialect, cwd)
    temp = tempfile.gettempdir()
    outside = [t for t in targets if not _inside(t, temp)]
    if outside:
        block(
            READ_ONLY_REASON.format(
                agent=agent, what=f"write files ({os.path.basename(outside[0])})"
            )
        )
    if unknown:
        block(
            READ_ONLY_REASON.format(
                agent=agent, what="write to a target it does not name"
            )
        )


def check_parsed(
    command: str, dialect: Dialect, depth: int = 0, cwd: str | None = None
) -> None:
    from shellparse import ParseError, parse, program_and_args
    from verification import cwd_at

    if depth > MAX_DEPTH:
        block("too deeply nested shell command")
    base = cwd or _ctx["cwd"] or os.getcwd()
    try:
        commands = parse(command, dialect)
    except ParseError:
        legacy_check(command)
        if read_only_agent():
            block(
                READ_ONLY_REASON.format(
                    agent=read_only_agent(), what="run a command that cannot be read"
                )
            )
        return
    check_read_only_writes(command, dialect, base)
    pipeline: list[str] = []
    piped_heredocs: list[str] = []
    for index, cmd in enumerate(commands):
        here = cwd_at(commands, index, base)
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
                check_parsed(body, "powershell" if ps else "bash", depth + 1, here)
            pipeline = []
            piped_heredocs = []

        for script, sub_dialect in nested_scripts(program, args):
            check_parsed(script, sub_dialect, depth + 1, here)
        if cmd.heredoc is not None and program in INTERPRETERS:
            if program in POSIX_SHELLS:
                check_parsed(cmd.heredoc, "bash", depth + 1, here)
            elif program in PS_HOSTS:
                check_parsed(cmd.heredoc, "powershell", depth + 1, here)
            else:
                hit = tokens_look_secret(cmd.heredoc)
                if hit:
                    block(f"command touches a secret file ({hit})")

        data = message_value_indexes(program, args)
        data |= {k - 1 for k in search_patterns([program, *args]) if k >= 1}
        if program == "git" and git_names_only(cmd, args):
            data = set(range(len(args)))  # names a file, cannot print it
        check_isolation(program, args, dialect, here, data)
        for k, arg in enumerate(args):
            if k in data:
                continue
            for tok in (
                t
                for w in _words(arg, dialect)
                for t in re.split(r"[\s'\"<>|;&()=,]+", w)
            ):
                if tok and is_secret_path(tok):
                    block(f"command touches a secret file ({tok})")
                # git's `<revision>:<path>` and `-L<range>:<path>` name a file too
                path = tok.rpartition(":")[2] if program == "git" else ""
                if path and path != tok and is_secret_path(path):
                    block(f"command touches a secret file ({path})")
        if is_secret_path(cmd.words[0]) if cmd.words else False:
            block(f"command touches a secret file ({cmd.words[0]})")

        if program == "git":
            sub = git_subcommand(args)
            if sub[:1] == ["add"] and ("-f" in sub or "--force" in sub):
                block("git add --force can stage ignored secret files")
            if sub[:1] == ["commit"]:
                leaked = staged_secrets(_git_folder(args, here))
                if leaked:
                    block(
                        f"staged files look like secrets: {', '.join(leaked)} (unstage them)"
                    )


# devenv-final 11b: the `## Verification` section of a CLAUDE.md declares the gate that
# decides what counts as verified. Weakening it (or adding one in a folder below the real
# one) would turn any command into "the gate", so a change of it is the user's to confirm.
POLICY_FILE = "claude.md"
POLICY_REASON = (
    "this changes the `## Verification` section of {path} (the gate that decides what "
    "counts as verified)"
)
POLICY_SHELL_REASON = (
    "this command writes {path}, which can hold the `## Verification` section (the gate "
    "that decides what counts as verified)"
)


def _policy_section(text: str) -> str:
    from verification import SECTION

    found = SECTION.search(text.replace("\r\n", "\n"))
    return found.group(0).strip() if found else ""


def changes_policy(tool: str, ti: dict[str, object], full: str) -> bool:
    """True when this Edit/Write/MultiEdit would leave the file's Verification section
    different from what it is now (added, removed or changed)."""
    if os.path.basename(full).lower() != POLICY_FILE:
        return False
    try:
        with open(full, encoding="utf-8", errors="replace", newline="") as f:
            before = f.read().replace("\r\n", "\n")
    except OSError:  # no such file yet (or unreadable): whatever it gets is new
        before = ""
    if tool == "Write":
        after = str(ti.get("content") or "")
    else:
        listed = ti.get("edits")
        edits = listed if tool == "MultiEdit" and isinstance(listed, list) else [ti]
        after = before
        for e in edits:
            if not isinstance(e, dict):
                continue
            old, new = str(e.get("old_string") or ""), str(e.get("new_string") or "")
            if not old or old not in after:
                continue  # this edit will not apply
            after = (
                after.replace(old, new)
                if e.get("replace_all")
                else after.replace(old, new, 1)
            )
    return _policy_section(before) != _policy_section(after)


# devenv-final 20: the hooks, settings and agent definitions under ~/.claude are what runs
# in every session. They are changed in the work tree and reach ~/.claude by a merge of a
# commit that passed the gate (CLAUDE.md, Release); a change made in place is the user's
# to confirm.
LIVE_FOLDERS = ("hooks", "agents")
LIVE_FILES = ("settings.json",)
LIVE_REASON = (
    "this changes {path} in place: the live hooks, settings and agents of ~/.claude are "
    "changed in the work tree and merged after the gate (CLAUDE.md, Release)"
)


def is_live_config(full: str) -> bool:
    from verification import _inside

    return any(
        _inside(full, os.path.join(CLAUDE_HOME, name)) for name in LIVE_FOLDERS
    ) or any(
        os.path.normcase(os.path.normpath(full))
        == os.path.normcase(os.path.join(CLAUDE_HOME, name))
        for name in LIVE_FILES
    )


def shell_guarded_writes(command: str, dialect: Dialect, cwd: str) -> list[str]:
    """Reasons to ask about what a command line visibly writes (best effort, see
    shellwrites): a CLAUDE.md, or the live configuration under ~/.claude."""
    low = command.lower()
    if POLICY_FILE not in low and ".claude" not in low:
        return []
    from shellwrites import write_targets

    targets, _unknown = write_targets(command, dialect, cwd)
    return [
        POLICY_SHELL_REASON.format(path=t)
        for t in targets
        if os.path.basename(t).lower() == POLICY_FILE
    ] + [LIVE_REASON.format(path=t) for t in targets if is_live_config(t)]


# What the protected folders hold, by name form: a Grep `glob` without a folder part
# (`*.json`) matches at any depth, so it reaches them when it can match one of these.
PROTECTED_NAME_FORMS = ("x.json", "x.jsonl", "x.key", "x.lock", "x.log", "x.txt", "x")


def tool_glob_reaches_protected(where: str, pattern: str) -> bool:
    """Whether a Grep `glob` (or a Glob `pattern`) applied at `where`, a folder above the
    protected ones, can match files in them. `hooks/**` cannot; `state/**`, `st*/x` can; a
    pattern starting with `**`, or without a folder part, can when it can match a file
    name found there."""
    import fnmatch

    text = pattern.strip().lstrip("!").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    deep = text.startswith("**/") or text == "**"
    while text.startswith("**/"):
        text = text[3:]
    if text in ("", "**"):
        return True
    if "/" not in text:
        return any(fnmatch.fnmatch(name, text.lower()) for name in PROTECTED_NAME_FORMS)
    if deep:
        return True  # `**/x/y`: any folder, the protected ones among them
    return bool(glob_reaches_protected(os.path.join(where, text), where))


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
    agent = data.get("agent_type")
    _ctx["agent"] = agent if isinstance(agent, str) and data.get("agent_id") else None
    cwd = data.get("cwd")
    _ctx["cwd"] = cwd if isinstance(cwd, str) and cwd else os.getcwd()

    if tool in EDIT_TOOLS and read_only_agent():
        block(READ_ONLY_REASON.format(agent=read_only_agent(), what="edit files"))
    if tool in ("Bash", "PowerShell"):
        dialect: Dialect = "powershell" if tool == "PowerShell" else "bash"
        check_command(ti.get("command", ""), dialect)
        _asks.extend(
            shell_guarded_writes(str(ti.get("command", "")), dialect, str(_ctx["cwd"]))
        )
        if _asks:
            ask("; ".join(dict.fromkeys(_asks)))
    elif tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if path and is_secret_path(path):
            block(f"access to secret file {path}")
        full = as_path(str(path), str(_ctx["cwd"])) if path else None
        if full and in_protected(full):
            block(PROTECTED_REASON.format(path=path))
        if full and tool != "Read" and changes_policy(tool, ti, full):
            ask(POLICY_REASON.format(path=path))
        if full and tool != "Read" and is_live_config(full):
            ask(LIVE_REASON.format(path=path))
    elif tool in ("Grep", "Glob"):
        # Grep's "pattern" is a regex over content, not a path; only Glob's pattern is a path.
        keys = ("path", "pattern") if tool == "Glob" else ("path", "glob")
        target = " ".join(str(ti.get(k, "")) for k in keys)
        hit = tokens_look_secret(target)
        if hit:
            block(f"search targets a secret file ({hit})")
        where = as_path(str(ti.get("path") or "."), str(_ctx["cwd"]))
        if where and in_protected(where):
            block(PROTECTED_REASON.format(path=ti.get("path")))
        above = bool(where) and above_protected(str(where))
        if above and not ti.get("glob") and tool == "Grep":
            # a content search over all of ~/.claude or above it reads the protected folders
            block(SWEEP_REASON.format(root=ti.get("path") or "the current folder"))
        reaches = (
            above
            and tool == "Grep"
            and bool(ti.get("glob"))
            and tool_glob_reaches_protected(str(where), str(ti.get("glob")))
        )
        if reaches:  # ... and so does one whose `glob` can match files in them
            block(SWEEP_REASON.format(root=f"{ti.get('path') or '.'} ({ti['glob']})"))
        if above and tool == "Glob":
            # a pattern that names a protected folder (`discipline/**`, `st*/**`); a plain
            # `**/*.py` lists names everywhere and stays allowed, as before
            pattern = str(ti.get("pattern") or "").replace("\\", "/")
            if not pattern.startswith("**") and glob_reaches_protected(
                os.path.join(str(where), pattern), str(where)
            ):
                block(PROTECTED_REASON.format(path=pattern))
            literal = as_path(os.path.join(str(where), pattern), str(where))
            if literal and in_protected(literal):
                block(PROTECTED_REASON.format(path=pattern))
    sys.exit(0)


def run() -> None:
    """devenv-final 20: fail closed. An error inside the guard used to end it with exit
    code 1, which the harness reads as "no objection": the command the guard could not
    judge then ran. Now such a call is refused (exit 2) and says so."""
    try:
        main()
    except Exception as e:  # the hook's outermost boundary
        import logging

        logging.getLogger("guard").exception("guard.hook_boundary_failure")
        print(
            f"BLOCKED by guard hook: the guard itself failed ({type(e).__name__}) and "
            "could not judge this call. Tell the user; if every call is refused, the "
            "way out is in ~/.claude/CLAUDE.md (Release).",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    run()
