"""Working-tree content snapshots for discipline.py (D-5).

A content map is {repo-relative posix path: git blob sha} for every tracked or
untracked-but-not-ignored file as it is on disk now (plus ignored files matching
declared `watch` globs). Commits do not change it, so committing or pushing is never
mistaken for verification or for an edit; any change to file contents, whichever tool
made it (Edit, sed, a script), is visible. Unavailable or oversized repositories give
None, which callers treat as "unknown", never as unchanged.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time

GIT_TIMEOUT_S = 10
MAX_FILES = 5000
# Not code: documentation and media. Other .md/.txt (requirements.txt, skills/*.md) count.
NON_CODE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".ico")
NON_CODE_NAMES = ("readme", "changelog", "license", "notice", "authors")

# One time budget for every git call of a hook run, so the hook finishes (and decides,
# failing closed) before Claude Code's hook timeout would kill it.
_deadline: float | None = None
last_error = (
    ""  # "too_large" | "unavailable" for the last content_map() that returned None
)


budget_exhausted = False  # some git call got no answer in time: results are incomplete


def set_budget(seconds: float | None) -> None:
    global _deadline, budget_exhausted
    _deadline = None if seconds is None else time.monotonic() + seconds
    budget_exhausted = False


def _git(cwd: str, *args: str, stdin: str | None = None) -> str | None:
    global budget_exhausted
    timeout: float = GIT_TIMEOUT_S
    if _deadline is not None:
        remaining = _deadline - time.monotonic()
        if remaining <= 0:
            budget_exhausted = True
            return None  # budget exhausted: no answer rather than a late one
        timeout = min(timeout, remaining)
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:  # git hung: no snapshot, and say so
        budget_exhausted = True
        return None
    except OSError:  # git missing: no snapshot
        return None
    return p.stdout if p.returncode == 0 else None


def head(top: str) -> str | None:
    out = _git(top, "rev-parse", "--verify", "-q", "HEAD")
    return out.strip() if out else None


def find_toplevel(folder: str) -> str | None | bool:
    """The work tree `folder` is in, found without a git call: the nearest folder at or
    above it that holds a `.git` entry (a folder, or the file of a linked work tree or
    submodule), as git names it (links resolved). None: no such folder. False: it cannot
    be told this way (the folder is inside a `.git` folder, a GIT_DIR is set, the path
    cannot be read): ask git."""
    if os.environ.get("GIT_DIR") or os.environ.get("GIT_WORK_TREE"):
        return False
    try:
        d = os.path.realpath(folder)
        parts = {p.lower() for p in d.replace("\\", "/").split("/")}
        if ".git" in parts:
            return False
        while True:
            dot_git = os.path.join(d, ".git")
            if os.path.isdir(dot_git):
                # a folder named .git that is no repository (no HEAD): git decides
                if os.path.isfile(os.path.join(dot_git, "HEAD")):
                    return os.path.normpath(d)
                return False
            if os.path.lexists(dot_git):
                with open(dot_git, encoding="utf-8", errors="replace") as f:
                    if f.read(8) == "gitdir: ":
                        return os.path.normpath(d)
                return False
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent
    except (OSError, ValueError):
        return False


def toplevel(path: str) -> str | None:
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):  # a file about to be created in a new folder
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    if not d:
        return None
    # devenv-final 30: most hook runs asked git only for this, at about 60 ms a call
    found = find_toplevel(d)
    if found is not False:
        return found if isinstance(found, str) else None
    out = _git(d, "rev-parse", "--show-toplevel")
    return os.path.normpath(out.strip()) if out else None


EXCLUDE_KEY = ".git/info/exclude"  # the map's entry for the repository's exclude file
GITLINK = "160000"  # the index mode of a submodule


def exclude_file(top: str) -> str | None:
    """Path of the repository's info/exclude as seen from the work tree `top` (a linked
    work tree and a submodule keep it in the repository they belong to), without a git
    call; None when it cannot be told."""
    dot_git = os.path.join(top, ".git")
    if os.path.isdir(dot_git):
        return os.path.normpath(os.path.join(dot_git, "info", "exclude"))
    try:
        with open(dot_git, encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
        if not first.startswith("gitdir:"):
            return None
        git_dir = os.path.normpath(os.path.join(top, first[len("gitdir:") :].strip()))
        common = git_dir
        pointer = os.path.join(git_dir, "commondir")
        if os.path.isfile(pointer):
            with open(pointer, encoding="utf-8", errors="replace") as f:
                common = os.path.normpath(os.path.join(git_dir, f.read().strip()))
    except OSError:  # no .git entry, or unreadable
        return None
    return os.path.normpath(os.path.join(common, "info", "exclude"))


def _exclude_hash(top: str) -> str:
    """A hash of the names the repository hides without a .gitignore ("" when it has no
    such file): adding one changes every digest taken before."""
    path = exclude_file(top)
    try:
        with open(path or "", "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def content_map(top: str, watch: list[str] | None = None) -> dict[str, str] | None:
    global last_error
    last_error = "unavailable"  # until the map is complete
    index = _git(top, "ls-files", "-s", "-v", "-z")
    status = _git(top, "status", "--porcelain=v1", "-z", "-uall")
    if index is None or status is None:
        return None
    files: dict[str, str] = {}
    touched: list[str] = []
    gitlinks: set[str] = set()
    for entry in index.split("\0"):
        if "\t" in entry:
            meta, path = entry.split("\t", 1)
            parts = meta.split()  # <tag> <mode> <object> <stage>
            if len(parts) == 4 and parts[3] == "0":
                files[path] = parts[2]
                if parts[1] == GITLINK:
                    gitlinks.add(path)
                elif parts[0].islower() or parts[0] == "S":
                    # assume-unchanged / skip-worktree: `git status` does not look at
                    # this file, so its content is hashed here like a changed one's.
                    touched.append(path)
    records = status.split("\0")
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if len(rec) < 4:
            continue
        code, path = rec[:2], rec[3:]
        if code[0] in "RC":
            i += 1  # the next record is the rename/copy source
        touched.append(path)
    if watch:
        ignored = _git(
            top, "ls-files", "-o", "-i", "--exclude-standard", "-z", "--", *watch
        )
        if ignored is None:
            return None
        touched += [p for p in ignored.split("\0") if p]
    if gitlinks & set(touched):
        # A submodule with changes of its own (git lists it as modified): what is in it
        # is not in this map, so the content is unknown rather than "one changed entry".
        return None
    present = [p for p in touched if os.path.isfile(os.path.join(top, p))]
    for p in touched:
        if p not in present:
            files.pop(p, None)  # deleted in the working tree
    if present:
        hashes = _git(
            top, "hash-object", "--stdin-paths", stdin="\n".join(present) + "\n"
        )
        if hashes is None:
            return None
        for p, h in zip(present, hashes.split(), strict=False):
            files[p] = h
    if len(files) > MAX_FILES:
        last_error = "too_large"
        return None
    hidden = _exclude_hash(top)
    if hidden:
        files[EXCLUDE_KEY] = hidden
    last_error = ""
    return files


# Untracked files with these suffixes are source a check may run on; other untracked files
# (coverage.xml, .coverage, reports) are treated as check output, not as edits.
SOURCE_EXT = (
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ps1",
    ".psm1",
    ".psd1",
    ".sh",
    ".sql",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".rs",
    ".go",
    ".java",
    ".cs",
)


def tracked_paths(top: str) -> set[str] | None:
    out = _git(top, "ls-files", "-z")
    return None if out is None else {p for p in out.split("\0") if p}


def paths_committed_since(
    top: str, old: str, new: str, since: float
) -> set[str] | None:
    """Paths changed by commits in old..new whose committer time is at or after `since`
    (commits made now, as opposed to ones a checkout/pull brought in). None if unknown."""
    out = _git(top, "log", "--format=@%ct", "--name-only", "-z", f"{old}..{new}")
    if out is None:
        return None
    paths: set[str] = set()
    recent = False
    for token in out.replace("\n", "\0").split("\0"):
        if token.startswith("@") and token[1:].isdigit():
            recent = int(token[1:]) >= int(since) - 1  # second resolution of %ct
        elif token and recent:
            paths.add(token)
    return paths


def tree_map(top: str, rev: str) -> dict[str, str] | None:
    out = _git(top, "ls-tree", "-r", "-z", rev)
    if out is None:
        return None
    files: dict[str, str] = {}
    for entry in out.split("\0"):
        if "\t" in entry:
            meta, path = entry.split("\t", 1)
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                files[path] = parts[2]
    return files


# ---------------------------------------------------------------- pure helpers
DOC_EXT = ("", ".md", ".txt", ".rst", ".adoc")


def relevant(path: str) -> bool:
    """Whether a change to `path` can change behaviour. What cannot: media, files named
    like a document (README, LICENSE, ...) that are documents by their suffix too, and the
    repository's top-level docs/ folder except for source in it (docs/conf.py). A folder
    named docs deeper in the tree (src/docs/handler.py) is code like any other."""
    low = path.lower().replace("\\", "/")
    name = low.rsplit("/", 1)[-1]
    stem, dot, suffix = name.partition(".")
    if low.endswith(NON_CODE_EXT):
        return False
    if low.startswith("docs/") and not low.endswith(SOURCE_EXT):
        return False
    return not (stem in NON_CODE_NAMES and (dot + suffix) in DOC_EXT)


def under(path: str, prefix: str) -> bool:
    # the repository's exclude file belongs to every project in it
    return (
        not prefix
        or path == EXCLUDE_KEY
        or path == prefix
        or path.startswith(prefix.rstrip("/") + "/")
    )


def digest(files: dict[str, str], prefix: str = "") -> str:
    items = sorted((p, h) for p, h in files.items() if under(p, prefix) and relevant(p))
    return hashlib.sha256(
        "\n".join(f"{p}\t{h}" for p, h in items).encode("utf-8")
    ).hexdigest()


def changed(before: dict[str, str], after: dict[str, str]) -> set[str]:
    keys = set(before) | set(after)
    return {p for p in keys if before.get(p) != after.get(p)}


def rel_prefix(top: str, root: str) -> str | None:
    """Repo-relative posix prefix of `root`, or None if root is outside the repository."""
    t, r = (
        os.path.normcase(os.path.normpath(top)),
        os.path.normcase(os.path.normpath(root)),
    )
    if r == t:
        return ""
    if not r.startswith(t.rstrip("\\/") + os.sep):
        return None
    return os.path.relpath(os.path.normpath(root), os.path.normpath(top)).replace(
        "\\", "/"
    )
