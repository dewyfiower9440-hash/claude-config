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

GIT_TIMEOUT_S = 10
MAX_FILES = 5000
DOC_EXT = (".md", ".txt", ".rst", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf")


def _git(cwd: str, *args: str, stdin: str | None = None) -> str | None:
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):  # git missing or hung: no snapshot
        return None
    return p.stdout if p.returncode == 0 else None


def toplevel(path: str) -> str | None:
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while d and not os.path.isdir(d):  # a file about to be created in a new folder
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    out = _git(d, "rev-parse", "--show-toplevel") if d else None
    return os.path.normpath(out.strip()) if out else None


def content_map(top: str, watch: list[str] | None = None) -> dict[str, str] | None:
    index = _git(top, "ls-files", "-s", "-z")
    status = _git(top, "status", "--porcelain=v1", "-z", "-uall")
    if index is None or status is None:
        return None
    files: dict[str, str] = {}
    for entry in index.split("\0"):
        if "\t" in entry:
            meta, path = entry.split("\t", 1)
            parts = meta.split()
            if len(parts) == 3 and parts[2] == "0":
                files[path] = parts[1]
    touched: list[str] = []
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
    return None if len(files) > MAX_FILES else files


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
def relevant(path: str) -> bool:
    return not path.lower().endswith(DOC_EXT)


def under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix.rstrip("/") + "/")


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
