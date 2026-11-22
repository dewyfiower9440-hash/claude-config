"""devenv-v1-2 step 3: an inline patch is a shell command that writes a file from code or
text given on the command line (`python -c`, a python heredoc, `cat > f <<EOF`, `tee f
<<EOF`) with a backslash in it; the shell or Python turns those into other bytes (a `\\r`
in a Windows path became a CR on 2026-11-20). `inline_patches` finds them from the command
text alone.

    python -I tests/test_inline_patch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import inline_patch as ip

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


def kinds(command: str, dialect: str = "bash") -> list[str]:
    return [f.kind for f in ip.inline_patches(command, dialect)]


# The 2026-11-20 incident, as the session ran it (a Windows path inside a triple-quoted
# Python string, written with write_text).
INCIDENT = (
    'python -c "\n'
    "from pathlib import Path\n"
    "p = Path('tools/register_paper_task.ps1')\n"
    "s = p.read_text(encoding='utf-8')\n"
    "s = s.replace('X', '''$Run = 'C:\\dev\\qr-data\\run' ''')\n"
    "p.write_text(s, encoding='utf-8')\n"
    '"'
)
check("IP-1 the 2026-11-20 incident is found", kinds(INCIDENT), ["python-c"])
check(
    "IP-2 `uv run python -I -c` and `py -3 -c` are python too",
    [
        kinds(r"uv run python -I -c " + "\"open('a.txt','w').write('C:\\\\x')\""),
        kinds(r"uv run --with x python -c " + "\"Path('a').write_text('a\\\\b')\""),
        kinds(r"py -3 -c " + "\"Path('a').write_bytes(b'\\\\x00')\""),
        kinds(r"python3 -c " + "\"open('a', mode='a').write('\\\\n')\""),
    ],
    [["python-c"], ["python-c"], ["python-c"], ["python-c"]],
)
check(
    "IP-3 a python heredoc that writes with a backslash is found",
    kinds("python - <<'EOF'\nopen('f','w').write('a\\\\nb')\nEOF\n"),
    ["python-heredoc"],
)
check(
    "IP-4 `cat > f <<EOF` / `cat >> f` / `tee f` with a backslash are found; without one they are not",
    [
        kinds("cat >> f.txt <<'EOF'\nC:\\\\dev\\\\x\nEOF\n"),
        kinds("cat > f.txt <<'EOF'\na\\\\b\nEOF\n"),
        kinds("tee -a f.txt <<'EOF'\na\\\\b\nEOF\n"),
        kinds("cat >> f.txt <<'EOF'\nplain text\nEOF\n"),
        kinds("tee f.txt <<'EOF'\nplain\nEOF\n"),
    ],
    [["shell-heredoc"], ["shell-heredoc"], ["shell-heredoc"], [], []],
)
check(
    "IP-5 not a patch: a commit message heredoc, a heredoc only printed, echo, read-only python, a script file",
    [
        kinds("git commit -F - <<'EOF'\nfix C:\\\\dev path\nEOF\n"),
        kinds("cat <<'EOF'\na\\\\b\nEOF\n"),
        kinds("echo 'a\\\\b' > f.txt"),
        kinds(r"python -I -c " + "\"import re; print(re.findall(r'\\\\d+', 'a1'))\""),
        kinds("python tool.py <<'EOF'\nopen('f','w').write('a\\\\b')\nEOF\n"),
        kinds(r"python -c " + "\"open('f','w').write('plain')\""),
        kinds("python -m tool -c \"open('f','w').write('a\\\\b')\""),
    ],
    [[], [], [], [], [], [], []],
)
check(
    "IP-6 python whose output is redirected to a file, with a backslash in the code, is a patch",
    kinds(r"python -c " + "\"print('C:\\\\dev')\"" + " > out.txt"),
    ["python-c"],
)
check(
    "IP-7 a command that cannot be parsed, and a non-string, give nothing",
    [kinds("python -c 'unbalanced"), ip.inline_patches(None, "bash")],
    [[], []],
)
check(
    "IP-8 PowerShell: python -c is checked the same way",
    kinds(r"python -c " + "\"open('f','w').write('C:\\\\x')\"", "powershell"),
    ["python-c"],
)
READ_ONLY = r"python -c " + "\"import re; print(re.findall(r'\\\\d+', 'a1'))\""
check(
    "IP-10 (review 1) a redirect of stderr or to a device writes no file: not a patch",
    [
        kinds(READ_ONLY + " 2>/dev/null"),
        kinds(READ_ONLY + " > /dev/null"),
        kinds(READ_ONLY + " 2> err.txt"),
        kinds(READ_ONLY + " > NUL", "powershell"),
        kinds(READ_ONLY + " > $null", "powershell"),
        kinds(READ_ONLY + " 2>&1"),
        kinds("cat <<'EOF' 2>/dev/null\na\\\\b\nEOF\n"),
        kinds("tee /dev/null <<'EOF'\na\\\\b\nEOF\n"),
    ],
    [[], [], [], [], [], [], [], []],
)
check(
    "IP-10b stdout to a file is: `>`, `1>`, `&>`, `>>`, glued `>out`",
    [
        kinds(READ_ONLY + " > out.txt"),
        kinds(READ_ONLY + " 1> out.txt"),
        kinds(READ_ONLY + " &> out.txt"),
        kinds(READ_ONLY + " >> out.txt"),
        kinds(READ_ONLY + " >out.txt"),
    ],
    [["python-c"]] * 5,
)
check(
    "IP-11 (review 2) sys.stdout.write / sys.stderr.write print, they write no file",
    [
        kinds(r"python -c " + "\"import sys; sys.stdout.write('\\\\d')\""),
        kinds(r"python -c " + "\"import sys; sys.stderr.write('a\\\\b')\""),
    ],
    [[], []],
)
check(
    "IP-12 (review 3) uv: valued options before python, options before run, `--`",
    [
        kinds("uv run -w x python -c \"open('f','w').write('a\\\\b')\""),
        kinds("uv run --with-editable . python -c \"open('f','w').write('a\\\\b')\""),
        kinds("uv run --only-group dev python -c \"open('f','w').write('a\\\\b')\""),
        kinds("uv --directory x run python -c \"open('f','w').write('a\\\\b')\""),
        kinds("uv run -- python -c \"open('f','w').write('a\\\\b')\""),
        kinds("uv run --no-sync python -I -c \"open('f','w').write('a\\\\b')\""),
        kinds("uv run tool.py <<'EOF'\nopen('f','w').write('a\\\\b')\nEOF\n"),
        kinds("uv run -m tool -c \"open('f','w').write('a\\\\b')\""),
    ],
    [["python-c"]] * 6 + [[], []],
)
check(
    "IP-13 (review 4) python option clusters and names: -Ic, -c<code>, -X utf8, python3.12",
    [
        kinds("python -Ic \"open('f','w').write('a\\\\b')\""),
        kinds("python -c\"open('f','w').write('a\\\\b')\""),
        kinds("python -X utf8 -c \"open('f','w').write('a\\\\b')\""),
        kinds("python3.12 -c \"open('f','w').write('a\\\\b')\""),
        kinds("python -W ignore tool.py"),
    ],
    [["python-c"]] * 4 + [[]],
)
check(
    "IP-14 (review 5) open: a mode after a nested call, keyword mode, r+, Path.open; reading is not writing",
    [
        kinds(
            "python -c \"import os; f=open(os.path.join('a','b'), 'w'); print('\\\\d', file=f)\""
        ),
        kinds("python -c \"open(p, mode='w', encoding='utf-8').write('\\\\d')\""),
        kinds("python -c \"f=open(p, 'r+'); print('\\\\d', file=f)\""),
        kinds(
            "python -c \"from pathlib import Path; f=Path('x').open('w'); print('\\\\d', file=f)\""
        ),
        kinds("python -c \"print(open('tab').read().count('\\\\t'))\""),
    ],
    [["python-c"]] * 4 + [[]],
)
check(
    "IP-15 a nested command and two heredocs on one line are each checked",
    [
        kinds("echo $(python -c \"open('f','w').write('a\\\\b')\")"),
        kinds("cat <<'A' > a.txt; python - <<'B'\na\\\\b\nA\nprint(1)\nB\n"),
    ],
    [["python-c"], ["shell-heredoc"]],
)

check(
    "IP-16 (re-review) reading past open's parenthesis and os.write are no write; a file object's write is",
    [
        kinds("python -c \"print(open('f').read().replace('\\\\', 'x'))\""),
        kinds("python -c \"import os; os.write(1, b'\\\\n')\""),
        kinds("python -c \"f = get(); f.write('\\\\n')\""),
    ],
    [[], [], ["python-c"]],
)

f = ip.inline_patches(INCIDENT, "bash")
check(
    "IP-9 a finding names the program",
    [(x.kind, x.program) for x in f],
    [("python-c", "python")],
)

print(f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
