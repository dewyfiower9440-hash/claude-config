"""Unit tests for hooks/shellparse.py (run with python -I)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "shellparse", Path(__file__).resolve().parent.parent / "hooks" / "shellparse.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["shellparse"] = _mod  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(_mod)
ParseError = _mod.ParseError
SimpleCommand = _mod.SimpleCommand
parse = _mod.parse
program_and_args = _mod.program_and_args

fails = 0


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:110],
        "" if ok else f"(want {want!r})",
    )


def words(cmds: list) -> list[list[str]]:
    return [c.words for c in cmds]


def ops(cmds: list) -> list[str | None]:
    return [c.op_after for c in cmds]


def raises(name: str, src: str, dialect: str = "bash") -> None:
    try:
        parse(src, dialect)
    except ParseError:
        check(name, "ParseError", "ParseError")
        return
    check(name, "parsed", "ParseError")


# ------------------------------------------------------------------ bash
c = parse("echo a && echo b")
check("bash: && splits", words(c), [["echo", "a"], ["echo", "b"]])
check("bash: && operator recorded", ops(c), ["&&", None])

c = parse('git commit -m "fix: x; y && z" && git push')
check(
    "bash: operators inside double quotes are data",
    words(c)[0],
    ["git", "commit", "-m", "fix: x; y && z"],
)
check("bash: second command after &&", words(c)[1], ["git", "push"])

check("bash: pipe", ops(parse("uv run pytest -q | tail -3")), ["|", None])
check("bash: ; || & operators", ops(parse("a; b || c & d")), [";", "||", "&", None])
check("bash: newline separates", ops(parse("a\nb")), ["\n", None])

c = parse("echo 'a \"b\" $(c) `d`'")
check("bash: single quotes are literal", words(c), [["echo", 'a "b" $(c) `d`']])

c = parse('echo "x $(git push -f) y"')
check(
    "bash: $() inside double quotes runs",
    [x.words for x in c if x.nested],
    [["git", "push", "-f"]],
)
c = parse("echo $(rm -rf /)")
check("bash: unquoted $() runs", [x.words for x in c if x.nested], [["rm", "-rf", "/"]])
c = parse("echo `whoami`")
check("bash: backticks run", [x.words for x in c if x.nested], [["whoami"]])
check(
    "bash: arithmetic is not a command",
    words(parse("echo $((1+2))")),
    [["echo", "$((1+2))"]],
)

check("bash: backslash escapes space", words(parse("echo a\\ b")), [["echo", "a b"]])
check(
    "bash: escaped quote in double quotes",
    words(parse('echo "a\\"b"')),
    [["echo", 'a"b']],
)
check("bash: line continuation", words(parse("echo a \\\nb")), [["echo", "a", "b"]])
check("bash: comment ignored", words(parse("echo a # rm -rf /")), [["echo", "a"]])
check("bash: hash inside word kept", words(parse("echo a#b")), [["echo", "a#b"]])

c = parse("cat > notes.md <<'EOF'\nnever commit .env\nEOF\necho done")
check("bash: heredoc words", words(c), [["cat", ">", "notes.md"], ["echo", "done"]])
check("bash: heredoc body attached", c[0].heredoc, "never commit .env")
c = parse("cat <<-EOF | bash\n\trm -rf /\n\tEOF\n")
check(
    "bash: <<- strips tabs and keeps pipe",
    (c[0].heredoc, ops(c)),
    ("rm -rf /", ["|", "\n"]),
)

c = parse("(cd x && make) | tee log")
check(
    "bash: subshell commands are nested",
    [(x.words, x.nested) for x in c][:2],
    [(["cd", "x"], True), (["make"], True)],
)
check("bash: command after subshell", c[-1].words, ["tee", "log"])
check(
    "bash: redirections are words",
    words(parse("cmd 2>&1 >out.txt <in.txt")),
    [["cmd", "2", ">&", "1", ">", "out.txt", "<", "in.txt"]],
)
check("bash: &> redirection", words(parse("a &>/dev/null")), [["a", "&>", "/dev/null"]])

raises("bash: unterminated single quote", "echo 'abc")
raises("bash: unterminated double quote", 'echo "abc')
raises("bash: unterminated $(", "echo $(abc")
raises("bash: stray )", "echo a)")

# ------------------------------------------------------------------ powershell
c = parse("Get-ChildItem; Remove-Item x", "powershell")
check("ps: ; splits", words(c), [["Get-ChildItem"], ["Remove-Item", "x"]])
c = parse(
    "'a','b' | ForEach-Object { Unregister-ScheduledTask -TaskName $_ -Confirm:$false }",
    "powershell",
)
check(
    "ps: script block commands are nested",
    [x.words for x in c if x.nested],
    [["Unregister-ScheduledTask", "-TaskName", "$_", "-Confirm:$false"]],
)
check(
    "ps: '' escape",
    words(parse("Write-Output 'it''s'", "powershell")),
    [["Write-Output", "it's"]],
)
check(
    "ps: backtick escape",
    words(parse('Write-Output "a`"b"', "powershell")),
    [["Write-Output", 'a"b']],
)
c = parse("$m = @'\nline .env\n'@; git commit -m $m", "powershell")
check("ps: here-string is one word", c[0].words, ["$m", "=", "line .env"])
c = parse('Write-Output "$(Remove-Item -Recurse C:\\)"', "powershell")
check(
    "ps: $() subexpression runs",
    [x.words for x in c if x.nested],
    [["Remove-Item", "-Recurse", "C:\\"]],
)
check(
    "ps: call operator dropped",
    words(parse("& 'C:\\Program Files\\Git\\bin\\git.exe' push -f", "powershell")),
    [["C:\\Program Files\\Git\\bin\\git.exe", "push", "-f"]],
)
check(
    "ps: backslashes preserved",
    words(parse("Remove-Item .\\dist", "powershell")),
    [["Remove-Item", ".\\dist"]],
)
check(
    "ps: comment ignored",
    words(parse("Get-Date # Remove-Item C:\\", "powershell")),
    [["Get-Date"]],
)
check(
    "ps: ${env:X} is a word",
    words(parse("echo ${env:PATH}", "powershell")),
    [["echo", "${env:PATH}"]],
)
check(
    "ps: format operator stays in its command",
    words(parse("\"'{0:N1}' -f 1\"", "powershell")),
    [["'{0:N1}' -f 1"]],
)
raises("ps: unterminated quote", "Write-Output 'abc", "powershell")
raises("ps: unterminated block", "ForEach-Object { a", "powershell")

# ------------------------------------------------------------------ program_and_args
check(
    "program: exe path",
    program_and_args(parse('"C:\\x\\git.exe" push')[0]),
    ("git", ["push"]),
)
check(
    "program: env + sudo",
    program_and_args(parse("FOO=1 sudo git push -f")[0]),
    ("git", ["push", "-f"]),
)
check(
    "program: unix path",
    program_and_args(parse("/usr/bin/git push")[0]),
    ("git", ["push"]),
)
check(
    "program: lower-cased",
    program_and_args(parse("Remove-Item x", "powershell")[0]),
    ("remove-item", ["x"]),
)
check("program: assignment only", program_and_args(parse("X=1")[0]), ("", []))

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
