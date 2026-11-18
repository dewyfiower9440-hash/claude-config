"""Find memory files by words (P2-05). Read-only: nothing is written, moved or indexed.

    python -I hooks/memory_search.py WORD [WORD ...] [--dir DIR ... | --all] [--limit N]

Auto-memory is kept per working folder (`~/.claude/projects/<folder name>/memory/`), so a
session started elsewhere does not see it. This looks through the memory files of the
current working folder (default), of the folders given with `--dir`, or of every project
(`--all`), and prints the memories that hold every word (in the name, the description or
the body; upper and lower case are the same), best first.

The folder name is the working folder's path with every character that is not an ASCII
letter or digit replaced by a dash. That rule was read off existing folders, not from
documentation; when no such folder exists the command says so (use `--dir` then).

What is printed is masked first (`redact.redact`), then freed of control characters, then
cut: a secret that straddles the cut would otherwise leave its front piece behind.

Exit codes: 0 something found, 1 nothing found, 2 bad input. Not a hook: nothing runs this
by itself.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

INDEX_NAME = "memory.md"  # the index of a memory folder (compared without case)
KINDS = ("user", "feedback", "project", "reference")
MAX_FILE_BYTES = 262_144
MAX_FILES_PER_DIR = 2000
MAX_LINES = 3
SHOWN_CHARS = 160
DEFAULT_LIMIT = 10
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_NOT_PLAIN = re.compile(r"[^A-Za-z0-9]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x9b]")
_BLOCK = re.compile(
    r"[|>][+-]?"
)  # a YAML block scalar: the value is on the lines below


@dataclass(frozen=True, slots=True)
class Memory:
    name: str
    description: str
    kind: str  # whatever the file's `type` says ("" when it says nothing)
    body: str


@dataclass(frozen=True, slots=True)
class Hit:
    score: int
    lines: tuple[
        str, ...
    ]  # the first lines of the body that hold one of the words, whole


@dataclass(frozen=True, slots=True)
class Found:
    path: Path
    memory: Memory
    hit: Hit


def slug(cwd: str) -> str:
    """The name of the projects folder that belongs to a working folder."""
    return _NOT_PLAIN.sub("-", cwd)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse(text: str) -> Memory:
    """A memory file's name, description, type and body. Without a frontmatter that opens
    and closes, the whole text is the body.

    Not a YAML parser: `name` and `description` are read at the top level, `type` at the
    top level or under `metadata:`, and a block value (`|`, `>`) is the indented lines
    below its key joined by spaces."""
    found = _FRONTMATTER.match(text)
    if not found:
        return Memory("", "", "", text)
    values: dict[str, str] = {}
    top = ""  # the top-level key the current indented lines belong to
    block: list[str] | None = None  # the lines of a block value being collected
    for line in found.group(1).splitlines():
        indented = line[:1] in (" ", "\t")
        if indented and block is not None:
            block.append(line.strip())
            continue
        key, colon, value = line.partition(":")
        key = key.strip()
        if not colon or not key:
            continue
        if not indented:
            if block is not None:
                values.setdefault(top, " ".join(part for part in block if part))
            top, block = key, None
            if _BLOCK.fullmatch(value.strip()):
                block = []
            elif key in ("name", "description", "type"):
                values.setdefault(key, _unquote(value))
        elif key == "type" and top == "metadata":
            values.setdefault("type", _unquote(value))
    if block is not None:
        values.setdefault(top, " ".join(part for part in block if part))
    return Memory(
        values.get("name", ""),
        values.get("description", ""),
        values.get("type", ""),
        text[found.end() :],
    )


def match(memory: Memory, words: Sequence[str]) -> Hit | None:
    """The memory's score for the words, or None unless it holds every one of them. A word
    counts 3 in the name, 2 in the description, 1 in the body (its best place only)."""
    wanted = [w.strip().casefold() for w in words if w.strip()]
    if not wanted:
        return None
    name, description, body = (
        memory.name.casefold(),
        memory.description.casefold(),
        memory.body.casefold(),
    )
    score = 0
    for word in wanted:
        points = (
            3
            if word in name
            else 2
            if word in description
            else 1
            if word in body
            else 0
        )
        if not points:
            return None
        score += points
    lines = [
        line.strip()
        for line in memory.body.splitlines()
        if any(word in line.casefold() for word in wanted)
    ]
    return Hit(score, tuple(lines[:MAX_LINES]))


def rank(found: Sequence[Found], limit: int) -> list[Found]:
    """Best score first, then by file name and path; at most `limit`."""
    ordered = sorted(found, key=lambda f: (-f.hit.score, f.path.name, str(f.path)))
    return ordered[: max(limit, 0)]


def search(
    folders: Sequence[Path], words: Sequence[str]
) -> tuple[list[Found], int, int]:
    """(memories that match, files looked at, files skipped: too large, unreadable, not
    UTF-8, or beyond the per-folder limit)."""
    found: list[Found] = []
    looked = skipped = 0
    for folder in folders:
        try:
            files = sorted(
                p
                for p in folder.glob("*.md")
                if p.name.lower() != INDEX_NAME and p.is_file()
            )
        except OSError:  # the folder went away or cannot be listed
            skipped += 1
            continue
        skipped += max(len(files) - MAX_FILES_PER_DIR, 0)
        for path in files[:MAX_FILES_PER_DIR]:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, ValueError):  # unreadable or not UTF-8: counted, not fatal
                skipped += 1
                continue
            looked += 1
            memory = parse(text)
            hit = match(memory, words)
            if hit is not None:
                found.append(Found(path, memory, hit))
    return found, looked, skipped


def shown(text: str) -> str:
    """Text as it is printed: masked, then without control characters, then cut."""
    # `python -I` leaves the script's folder off the import path
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import unicodedata

    from redact import redact

    plain = _CONTROL.sub("", redact(text))
    # format characters too (direction marks, zero-width characters): a memory written
    # with them could show another text than it holds
    return "".join(ch for ch in plain if unicodedata.category(ch) != "Cf")[:SHOWN_CHARS]


def render(found: Sequence[Found], total: int, looked: int, skipped: int) -> str:
    """What the command prints for the hits to show."""
    out: list[str] = []
    for item in found:
        memory = item.memory
        about = f" — {shown(memory.description)}" if memory.description else ""
        kind = memory.kind if memory.kind in KINDS else "?"
        out.append(_CONTROL.sub("", str(item.path.parent / shown(item.path.name))))
        out.append(f"  [{kind}] {shown(memory.name or item.path.stem)}{about}")
        out.extend(f"    {shown(line)}" for line in item.hit.lines)
    out.append(f"{total}개 일치(파일 {looked}개를 봄, {skipped}개 건너뜀)")
    return "\n".join(out) + "\n"


def _folders(args: argparse.Namespace) -> tuple[list[Path], str | None]:
    """(folders to search, what is wrong with the request or None)."""
    if args.dir:
        for given in args.dir:
            if not given.strip() or not Path(given).is_dir():
                return [], f"폴더가 없습니다: {given!r}"
        return [Path(given) for given in args.dir], None
    try:
        projects = Path.home() / ".claude" / "projects"
        own = projects / slug(str(Path.cwd())) / "memory"
    except (RuntimeError, OSError):
        return [], "홈 폴더나 작업 폴더를 알 수 없습니다 (--dir로 폴더를 주세요)"
    if args.all:
        folders = sorted(p for p in projects.glob("*/memory") if p.is_dir())
        return folders, (None if folders else f"메모리 폴더가 없습니다: {projects}")
    if not own.is_dir():
        return (
            [],
            f"이 작업 폴더의 메모리 폴더가 없습니다: {own} (--dir 또는 --all을 쓰세요)",
        )
    return [own], None


def _at_least_one(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    sys.dont_write_bytecode = (
        True  # read-only: no cache file for the mask module either
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="memory_search", description="Find memory files by words (read-only)."
    )
    parser.add_argument("words", nargs="*")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--dir", action="append", help="a memory folder (repeatable)")
    where.add_argument(
        "--all", action="store_true", help="every project's memory folder"
    )
    parser.add_argument("--limit", type=_at_least_one, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    words = [w for w in args.words if w.strip()]
    if not words:
        print("memory_search: 찾을 낱말을 하나 이상 주세요", file=sys.stderr)
        return 2
    folders, problem = _folders(args)
    if problem:
        print(f"memory_search: {problem}", file=sys.stderr)
        return 2
    found, looked, skipped = search(folders, words)
    sys.stdout.write(render(rank(found, args.limit), len(found), looked, skipped))
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
