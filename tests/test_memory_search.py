"""P2-05: a read-only command that finds memory files by words.

    python -I tests/test_memory_search.py

Example memories in a temp folder; the real memory folders are never read (the command's
home folder is pointed at the temp folder).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
# Under the gate every run has its own folder for these (run_all.GATE_TMP_ROOT, inside
# tests/ as before); alone, tests/ itself.
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_memory_search"
shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True)
sys.path.insert(0, str(HOOKS))
try:
    import memory_search as ms
except ImportError:  # before the module exists: every check below fails, none crashes
    ms = None

fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:150],
        "" if ok else f"(want {want!r})",
    )


def call(fn: str, *args: Any) -> Any:
    return getattr(ms, fn)(*args) if hasattr(ms, fn) else None


# ---------------------------------------------------------------- pure: folder names
check(
    "M1 the memory folder's name for a working folder",
    [
        call("slug", "C:\\"),
        call("slug", "C:\\Users\\user\\.claude"),
        call("slug", "C:\\dev\\_eval-dryrun\\runs\\t0-A-1"),
        call("slug", "C:\\work\\보고서 2026"),
    ],
    [
        "C--",
        "C--Users-user--claude",
        "C--dev--eval-dryrun-runs-t0-A-1",
        "C--work-----2026",  # one dash per character (for non-ASCII: the rule as designed)
    ],
)

# ---------------------------------------------------------------- pure: reading a memory
FULL = (
    "---\n"
    "name: ci-fix-through\n"
    'description: "After an approved push, fix CI failures to green: no pause"\n'
    "metadata:\n"
    "  node_type: memory\n"
    "  type: feedback\n"
    "---\n"
    "\n"
    "First line of the body.\n"
    "**Why:** the user said so.\n"
)


def fields(text: str) -> Any:
    m = call("parse", text)
    return None if m is None else (m.name, m.description, m.kind, m.body.strip())


check(
    "M2 frontmatter: name, quoted description, the type under metadata, the body",
    fields(FULL),
    (
        "ci-fix-through",
        "After an approved push, fix CI failures to green: no pause",
        "feedback",
        "First line of the body.\n**Why:** the user said so.",
    ),
)
check(
    "M2 no frontmatter, one that never closes, nothing: the text is all body",
    [
        fields("just a note\n"),
        fields("---\nname: x\nno end\n"),
        fields(""),
    ],
    [
        ("", "", "", "just a note"),
        ("", "", "", "---\nname: x\nno end"),
        ("", "", "", ""),
    ],
)
check(
    "M2 a type at the top level, single quotes, CRLF line ends",
    fields(
        "---\r\nname: a\r\ndescription: 'it''s here'\r\ntype: project\r\n---\r\nbody\r\n"
    ),
    ("a", "it's here", "project", "body"),
)


# ---------------------------------------------------------------- pure: matching
def hit(text: str, *words: str) -> Any:
    m = call("parse", text)
    h = call("match", m, list(words)) if m is not None else None
    return None if h is None else (h.score, list(h.lines))


BODY = "---\nname: heredoc-rule\ndescription: never patch through a heredoc\n---\nUse Write.\nA heredoc mangles backslashes.\n한글 줄도 있다.\n"
check(
    "M3 one word: found in the name (3), the description (2) or the body (1), the best one",
    [
        hit(BODY, "heredoc"),
        hit(BODY, "patch"),
        hit(BODY, "mangles"),
        hit(BODY, "nothing"),
    ],
    [
        (3, ["A heredoc mangles backslashes."]),
        (2, []),
        (1, ["A heredoc mangles backslashes."]),
        None,
    ],
)
check(
    "M3 several words must all be there; upper case and Korean words match",
    [
        hit(BODY, "HEREDOC", "write"),
        hit(BODY, "heredoc", "missing"),
        hit(BODY, "한글"),
        hit(BODY),
        hit(BODY, "", "  "),
    ],
    [
        (4, ["Use Write.", "A heredoc mangles backslashes."]),
        None,
        (1, ["한글 줄도 있다."]),
        None,
        None,
    ],
)
LONG = "---\nname: n\n---\n" + "".join(
    f"needle line {i} " + "x" * 300 + "\n" for i in range(6)
)
long_hit = hit(LONG, "needle")
check(
    "M3 at most three matching lines (whole: they are cut after masking, when shown)",
    (
        len(long_hit[1]) if long_hit else None,
        min(len(line) for line in long_hit[1]) > 300 if long_hit else None,
        long_hit[1][0][:13] if long_hit else None,
    ),
    (3, True, "needle line 0"),
)
shown = getattr(ms, "shown", lambda text: None)
SPLIT = "zebra " + "y" * 139 + " password=SECRETVALUE123"
check(
    "M3 shown text: masked first, then cut to 160 characters, control characters removed",
    (
        "SECRE" in (shown(SPLIT) or "SECRE"),
        len(shown("x" * 500) or ""),
        shown("a\x1b[31mb\x07c\td"),
    ),
    (False, 160, "a[31mbc\td"),
)
check(
    "M3 ... direction marks and zero-width characters too (a memory must show what it holds)",
    shown("safe" + chr(0x202E) + "txt." + chr(0x200B) + "exe" + chr(0x2066) + "!"),
    "safetxt.exe!",
)
check(
    "M2 a block description; a type only at the top level or under metadata",
    [
        fields(
            "---\nname: b\ndescription: >\n  first part\n  second zebra part\ntype: user\n---\nx\n"
        ),
        fields(
            "---\nname: c\ndescription: |\n  type: fake\n  more\nmetadata:\n  type: project\n---\nx\n"
        ),
        fields("---\nname: d\nother:\n  type: fake\n  name: wrong\n---\nx\n"),
    ],
    [
        ("b", "first part second zebra part", "user", "x"),
        ("c", "type: fake more", "project", "x"),
        ("d", "", "", "x"),
    ],
)


def order(found: list[tuple[str, int]], limit: int) -> object:
    if not hasattr(ms, "rank"):
        return None
    items = [
        ms.Found(Path(name), ms.Memory("", "", "", ""), ms.Hit(score, ()))
        for name, score in found
    ]
    return [f.path.name for f in ms.rank(items, limit)]


SCORED = [("b.md", 1), ("c.md", 3), ("a.md", 1), ("d.md", 2)]
check(
    "M3 order: best score first, the same score by file name; the limit",
    [order(SCORED, 10), order(SCORED, 2), order([], 5)],
    [["c.md", "d.md", "a.md", "b.md"], ["c.md", "d.md"], []],
)

# ---------------------------------------------------------------- the command
HOME = ROOT / "home"
WORK = ROOT / "work" / "proj"
WORK.mkdir(parents=True)
SLUG = call("slug", str(WORK)) or "none"
MEM = HOME / ".claude" / "projects" / SLUG / "memory"
ELSE = HOME / ".claude" / "projects" / "C--else" / "memory"
EXTRA = ROOT / "extra"
for d in (MEM, ELSE, EXTRA):
    d.mkdir(parents=True)
TOKEN = "ghp_" + "A1b2C3d4" * 4


def write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8", newline="\n")


write(
    MEM,
    "alpha.md",
    "---\nname: alpha-widget\ndescription: about the widget\nmetadata:\n  type: project\n---\nThe widget ships in June.\n",
)
write(
    MEM,
    "beta.md",
    "---\nname: beta\ndescription: other things\nmetadata:\n  type: feedback\n---\nMentions the widget once.\n",
)
write(
    MEM,
    "gamma.md",
    f"---\nname: gamma\ndescription: a credential note\n---\nwidget token {TOKEN} was rotated\n",
)
write(MEM, "MEMORY.md", "- [alpha](alpha.md) — widget index line\n")
write(MEM, "big.md", "widget " * 60_000)
(MEM / "latin.md").write_bytes(b"widget caf\xe9\n")
write(MEM, "notes.txt", "widget in a text file\n")
write(
    ELSE,
    "delta.md",
    "---\nname: delta\ndescription: elsewhere\n---\nA widget in another project.\n",
)
write(
    EXTRA, "eps.md", "---\nname: eps\ndescription: extra folder\n---\nwidget here too\n"
)


def digest() -> str:
    h = hashlib.sha256()
    for f in sorted(ROOT.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def run(*args: str, cwd: Path = WORK) -> tuple[int, str]:
    env = dict(os.environ, USERPROFILE=str(HOME), HOME=str(HOME))
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "memory_search.py"), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


before = digest()
rc, out = run("widget")
order = [out.find(name) for name in ("alpha.md", "beta.md", "gamma.md")]
check(
    "M4 the working folder's memories: found, best first (name before body, then by file name)",
    (rc, all(i >= 0 for i in order), order == sorted(order)),
    (0, True, True),
)
check(
    "M4 ... each hit shows its kind, name, description and the matching line",
    all(
        text in out
        for text in (
            "[project] alpha-widget",
            "about the widget",
            "The widget ships in June.",
        )
    ),
    True,
)
check(
    "M4 ... the index, other file types, too large and non-UTF-8 files are left out and counted",
    (
        "index line" in out,
        "text file" in out,
        "another project" in out,
        "3개 일치" in out,
        "2개 건너뜀" in out,
    ),
    (False, False, False, True, True),
)
check(
    "M4 ... a token in a matching line is masked",
    (TOKEN in out, "rotated" in out),
    (False, True),
)
check("M5 no memory has every word: exit code 1", run("widget", "zzz-not-there")[0], 1)
check(
    "M5 no word, a folder that does not exist, a limit that is no number: exit code 2",
    [
        run()[0],
        run("  ")[0],
        run("widget", "--dir", str(ROOT / "nowhere"))[0],
        run("widget", "--limit", "many")[0],
    ],
    [2, 2, 2, 2],
)
rc, out = run("widget", cwd=ROOT)
check(
    "M5 a working folder without a memory folder: said so, exit code 2",
    (rc, "메모리 폴더가 없습니다" in out),
    (2, True),
)
rc, out = run("widget", "--dir", str(EXTRA), "--dir", str(ELSE))
check(
    "M6 --dir, more than once: only those folders",
    (rc, "eps.md" in out, "delta.md" in out, "alpha.md" in out),
    (0, True, True, False),
)
rc, out = run("widget", "--all")
check(
    "M6 --all: every project's memory folder",
    (rc, "alpha.md" in out, "delta.md" in out, "eps.md" in out),
    (0, True, True, False),
)
rc, out = run("widget", "--all", "--limit", "1")
check(
    "M6 --limit: the best hits only, and the count says how many there were",
    (rc, "alpha.md" in out, "beta.md" in out, "4개 일치" in out),
    (0, True, False, True),
)
check("M7 nothing was written anywhere", digest() == before, True)

ODD = ROOT / "odd"
ODD.mkdir()
(ODD / "folder.md").mkdir()
(ODD / "bom.md").write_bytes(
    "\ufeff---\nname: bom-note\ndescription: with a mark\n---\nzebra\n".encode()
)
write(
    ODD,
    "kind.md",
    "---\nname: k\nmetadata:\n  type: api_key=ZZZZYYYYXXXXWWWW\n---\nzebra\n",
)
write(ODD, "cut.md", "---\nname: cut\n---\n" + SPLIT + "\n")
write(
    ODD, "wide.md", "---\nname: wide\ndescription: " + "zebra " * 400 + "\n---\nbody\n"
)
write(ODD, "empty.md", "")
write(ODD, "rule.md", "zebra\n\n---\n\nmore\n")
rc, out = run("zebra", "--dir", str(ODD), "--limit", "50")
check(
    "M8 odd files: no traceback; a BOM file is read; an unknown type is shown as ?",
    (
        rc,
        "Traceback" in out,
        "[?] bom-note — with a mark" in out,
        "ZZZZYYYY" in out,
        "[?] k" in out,
    ),
    (0, False, True, False, True),
)
check(
    "M8 ... a secret at the cut is masked whole; no shown line is longer than its limit",
    ("SECRE" in out, max(len(line) for line in out.splitlines()) <= 2 * 160 + 40),
    (False, True),
)
check(
    "M8 words with regex characters, a huge limit: no traceback",
    [
        (
            run("a.*(", "--dir", str(ODD))[0],
            "Traceback" in run("a.*(", "--dir", str(ODD))[1],
        ),
        (run("zebra", "--dir", str(ODD), "--limit", "999999999")[0]),
    ],
    [(1, False), 0],
)
check(
    "M8 a limit below 1, an empty --dir, --dir together with --all: exit code 2",
    [
        run("zebra", "--dir", str(ODD), "--limit", "0")[0],
        run("zebra", "--dir", str(ODD), "--limit", "-3")[0],
        run("zebra", "--dir", "")[0],
        run("zebra", "--dir", str(ODD), "--all")[0],
    ],
    [2, 2, 2, 2],
)


def run_without_home(*args: str) -> tuple[int, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in ("USERPROFILE", "HOME", "HOMEPATH", "HOMEDRIVE")
    }
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "memory_search.py"), *args],
        cwd=WORK,
        env=env,
        capture_output=True,
        check=False,
    )
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")


rc_dir, out_dir = run_without_home("zebra", "--dir", str(ODD))
rc_own, out_own = run_without_home("zebra")
check(
    "M9 no home folder: --dir still works; without it the command says so (exit code 2)",
    (rc_dir, "Traceback" in out_dir, rc_own, "Traceback" in out_own),
    (0, False, 2, False),
)
MANY = ROOT / "many"
MANY.mkdir()
CAP = getattr(ms, "MAX_FILES_PER_DIR", 0)
if ms is not None:
    ms.MAX_FILES_PER_DIR = 3
    for i in range(5):
        write(MANY, f"m{i}.md", "zebra\n")
    got = ms.search([MANY], ["zebra"])
    ms.MAX_FILES_PER_DIR = CAP
    check(
        "M9 files beyond the per-folder limit are counted as skipped, not dropped silently",
        (len(got[0]), got[1], got[2]),
        (3, 3, 2),
    )

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
