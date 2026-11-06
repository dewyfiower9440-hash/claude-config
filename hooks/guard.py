"""PreToolUse guard: blocks destructive shell commands and access to secrets.

Exit code 2 blocks the tool call; stderr is shown to Claude as the reason.
"""

from __future__ import annotations

import json
import re
import sys

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


def check_command(cmd: str) -> None:
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
        check_command(ti.get("command", ""))
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
