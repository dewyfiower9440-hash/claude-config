"""Small shell parser shared by guard.py and discipline.py.

Splits a Bash or PowerShell command into simple commands with quotes removed and
records the operator that follows each one. Commands inside $( ), backticks,
subshells and PowerShell script blocks are returned too (nested=True); heredoc and
here-string bodies are kept as data. This is not a full shell: no expansion, no
aliases. Unbalanced quoting raises ParseError so callers can fall back to
conservative whole-string checks.
"""

from __future__ import annotations

import ntpath
import re
from typing import Literal

# No dataclasses/pathlib here: this module is imported by every PreToolUse hook call and
# those imports cost ~60 ms (dataclasses pulls in inspect).

Dialect = Literal["bash", "powershell"]


class SimpleCommand:
    __slots__ = ("heredoc", "nested", "op_after", "words")

    def __init__(
        self,
        words: list[str] | None = None,
        heredoc: str | None = None,
        op_after: str | None = None,
        nested: bool = False,
    ) -> None:
        self.words: list[str] = words if words is not None else []
        self.heredoc = heredoc
        self.op_after = op_after  # operator that follows; None if last in its list
        self.nested = nested  # inside $( ), backticks, ( ) or { }

    def __repr__(self) -> str:
        return f"SimpleCommand({self.words!r}, heredoc={self.heredoc!r}, op_after={self.op_after!r}, nested={self.nested})"


# Words that only wrap the real program: `sudo git push`, `env X=1 git push`, ...
WRAPPERS = {
    "sudo",
    "command",
    "exec",
    "nohup",
    "time",
    "env",
    "nice",
    "xargs",
    "builtin",
}
EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")


class ParseError(ValueError):
    """The command could not be tokenized (unbalanced quotes, brackets or substitutions)."""


def parse(command: str, dialect: Dialect = "bash") -> list[SimpleCommand]:
    parser = _Bash(command) if dialect == "bash" else _PowerShell(command)
    return parser.run()


def program_and_args(cmd: SimpleCommand) -> tuple[str, list[str]]:
    """Normalized program name (lower-case basename, no extension) and its arguments."""
    words = list(cmd.words)
    while words:
        head = words[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head):  # VAR=value prefix
            words.pop(0)
        elif _basename(head) in WRAPPERS and len(words) > 1:
            words.pop(0)
            while words and words[0].startswith(
                "-"
            ):  # wrapper options such as `sudo -u x` are rare; skip flags
                words.pop(0)
        else:
            break
    if not words:
        return "", []
    return _basename(words[0]), words[1:]


def _basename(word: str) -> str:
    name = ntpath.basename(word.replace("/", "\\").rstrip("\\")).lower()
    for suffix in EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


class _Parser:
    def __init__(self, src: str) -> None:
        self.s = src
        self.i = 0
        self.commands: list[SimpleCommand] = []
        self._cur = SimpleCommand()
        self._word: list[str] | None = None

    def run(self) -> list[SimpleCommand]:
        self._list(nested=False, closer=None)
        return self.commands

    # word / command bookkeeping (state is saved and restored around nested lists)
    def _add(self, text: str) -> None:
        if self._word is None:
            self._word = []
        self._word.append(text)

    def _end_word(self) -> None:
        if self._word is not None:
            self._cur.words.append("".join(self._word))
            self._word = None

    def _end_cmd(self, op: str | None) -> None:
        self._end_word()
        if self._cur.words or self._cur.heredoc is not None:
            self._cur.op_after = op
            self.commands.append(self._cur)
        elif op in ("|", "&&", "||") and self.commands:
            self.commands[
                -1
            ].op_after = op  # `(a) | b`: the subshell's last command feeds the pipe
        self._cur = SimpleCommand(nested=self._cur.nested)

    def _nested_list(self, closer: str) -> None:
        saved_cur, saved_word = self._cur, self._word
        self._cur, self._word = SimpleCommand(nested=True), None
        self._list(nested=True, closer=closer)
        self._cur, self._word = saved_cur, saved_word

    def _list(self, nested: bool, closer: str | None) -> None:
        raise NotImplementedError


class _Bash(_Parser):
    def __init__(self, src: str) -> None:
        super().__init__(src)
        self._heredocs: list[tuple[SimpleCommand, str, bool]] = []

    def _list(self, nested: bool, closer: str | None) -> None:
        s = self.s
        self._cur = SimpleCommand(nested=nested)
        while self.i < len(s):
            c = s[self.i]
            nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
            if c in " \t\r":
                self._end_word()
                self.i += 1
            elif c == "\n":
                self._end_cmd("\n")
                self.i += 1
                self._read_heredoc_bodies()
            elif c == "\\":
                if nxt == "\n":
                    self.i += 2  # line continuation
                else:
                    self._add(nxt)
                    self.i += 2
            elif c == "'":
                end = s.find("'", self.i + 1)
                if end < 0:
                    raise ParseError("unterminated single quote")
                self._add(s[self.i + 1 : end])
                self.i = end + 1
            elif c == '"':
                self._double_quoted()
            elif c == "`":
                self._backticks()
            elif c == "$" and nxt == "(":
                self._dollar_paren()
            elif c == "#" and self._word is None:
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            elif c == "<" and s.startswith("<<<", self.i):
                self._end_word()
                self._cur.words.append("<<<")
                self.i += 3
            elif c == "<" and nxt == "<":
                self._heredoc_start()
            elif c in "<>":
                self._end_word()
                j = self.i + 1
                if c == ">" and j < len(s) and s[j] in ">&|":
                    j += 1
                self._cur.words.append(s[self.i : j])
                self.i = j
            elif c == "&":
                if nxt == "&":
                    self._end_cmd("&&")
                    self.i += 2
                elif nxt == ">":
                    self._end_word()
                    self._cur.words.append("&>")
                    self.i += 2
                else:
                    self._end_cmd("&")
                    self.i += 1
            elif c == "|":
                if nxt == "|":
                    self._end_cmd("||")
                    self.i += 2
                else:
                    self._end_cmd("|")
                    self.i += 2 if nxt == "&" else 1
            elif c == ";":
                self._end_cmd(";")
                self.i += 2 if nxt == ";" else 1
            elif c == "(" and self._word is None and not self._cur.words:
                self.i += 1
                self._nested_list(")")
            elif c == ")":
                if closer != ")":
                    raise ParseError("unexpected )")
                self._end_cmd(None)
                self.i += 1
                return
            else:
                self._add(c)
                self.i += 1
        if closer:
            raise ParseError(f"missing {closer}")
        self._end_cmd(None)

    def _double_quoted(self) -> None:
        s = self.s
        self.i += 1
        self._add("")  # "" is still a word
        while True:
            if self.i >= len(s):
                raise ParseError("unterminated double quote")
            c = s[self.i]
            nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
            if c == '"':
                self.i += 1
                return
            if c == "\\" and nxt in '"\\$`\n':
                if nxt != "\n":
                    self._add(nxt)
                self.i += 2
            elif c == "$" and nxt == "(":
                self._dollar_paren()
            elif c == "`":
                self._backticks()
            else:
                self._add(c)
                self.i += 1

    def _dollar_paren(self) -> None:
        s = self.s
        start = self.i
        if s.startswith("$((", self.i):  # arithmetic expansion: not a command
            end = s.find("))", self.i + 3)
            if end < 0:
                raise ParseError("unterminated $((")
            self._add(s[start : end + 2])
            self.i = end + 2
            return
        self.i += 2
        self._nested_list(")")
        self._add(s[start : self.i])

    def _backticks(self) -> None:
        s = self.s
        end = self.i + 1
        while end < len(s) and s[end] != "`":
            end += 2 if s[end] == "\\" else 1
        if end >= len(s):
            raise ParseError("unterminated backtick")
        inner = s[self.i + 1 : end]
        for cmd in _Bash(inner).run():
            cmd.nested = True
            self.commands.append(cmd)
        self._add(s[self.i : end + 1])
        self.i = end + 1

    def _heredoc_start(self) -> None:
        s = self.s
        self._end_word()
        self.i += 2
        strip_tabs = self.i < len(s) and s[self.i] == "-"
        if strip_tabs:
            self.i += 1
        while self.i < len(s) and s[self.i] in " \t":
            self.i += 1
        m = re.match(r"""'([^']*)'|"([^"]*)"|\\?([^\s;&|<>()]+)""", s[self.i :])
        if not m:
            raise ParseError("heredoc without delimiter")
        delim = next(g for g in m.groups() if g is not None)
        self.i += m.end()
        self._heredocs.append((self._cur, delim, strip_tabs))

    def _read_heredoc_bodies(self) -> None:
        s = self.s
        for cmd, delim, strip_tabs in self._heredocs:
            lines: list[str] = []
            while self.i < len(s):
                end = s.find("\n", self.i)
                line = s[self.i :] if end < 0 else s[self.i : end]
                self.i = len(s) if end < 0 else end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delim:
                    break
                lines.append(line.lstrip("\t") if strip_tabs else line)
            body = "\n".join(lines)
            cmd.heredoc = body if cmd.heredoc is None else cmd.heredoc + "\n" + body
        self._heredocs.clear()


class _PowerShell(_Parser):
    def _list(self, nested: bool, closer: str | None) -> None:
        s = self.s
        self._cur = SimpleCommand(nested=nested)
        while self.i < len(s):
            c = s[self.i]
            nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
            if c in " \t\r":
                self._end_word()
                self.i += 1
            elif c in "\n;":
                self._end_cmd("\n" if c == "\n" else ";")
                self.i += 1
            elif c == "`":
                if nxt == "\n":
                    self.i += 2  # line continuation
                else:
                    self._add(nxt)
                    self.i += 2
            elif c == "'":
                self._single_quoted()
            elif c == '"':
                self._double_quoted()
            elif c == "@" and nxt in ("'", '"') and self._here_string():
                pass  # _here_string consumed it
            elif c == "<" and nxt == "#":
                end = s.find("#>", self.i + 2)
                if end < 0:
                    raise ParseError("unterminated block comment")
                self.i = end + 2
            elif c == "#" and self._word is None:
                end = s.find("\n", self.i)
                self.i = len(s) if end < 0 else end
            elif c == "|":
                self._end_cmd("||" if nxt == "|" else "|")
                self.i += 2 if nxt == "|" else 1
            elif c == "&":
                if nxt == "&":
                    self._end_cmd("&&")
                    self.i += 2
                elif self._word is None and not self._cur.words:
                    self.i += 1  # call operator: `& 'C:\x.exe' args`
                else:
                    self._end_cmd("&")
                    self.i += 1
            elif c == "$" and nxt == "{":
                end = s.find("}", self.i)
                if end < 0:
                    raise ParseError("unterminated ${")
                self._add(s[self.i : end + 1])
                self.i = end + 1
            elif c == "$" and nxt == "(":
                start = self.i
                self.i += 2
                self._nested_list(")")
                self._add(s[start : self.i])
            elif c in "({" or (c == "@" and nxt in "({"):
                self._end_word()
                if c == "@":
                    self.i += 1
                    c = s[self.i]
                self.i += 1
                self._nested_list(")" if c == "(" else "}")
            elif c in ")}":
                if closer != c:
                    raise ParseError(f"unexpected {c}")
                self._end_cmd(None)
                self.i += 1
                return
            else:
                self._add(c)
                self.i += 1
        if closer:
            raise ParseError(f"missing {closer}")
        self._end_cmd(None)

    def _single_quoted(self) -> None:
        s = self.s
        self.i += 1
        self._add("")
        while True:
            if self.i >= len(s):
                raise ParseError("unterminated single quote")
            if s[self.i] == "'":
                if s.startswith("''", self.i):
                    self._add("'")
                    self.i += 2
                    continue
                self.i += 1
                return
            self._add(s[self.i])
            self.i += 1

    def _double_quoted(self) -> None:
        s = self.s
        self.i += 1
        self._add("")
        while True:
            if self.i >= len(s):
                raise ParseError("unterminated double quote")
            c = s[self.i]
            nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
            if c == '"':
                if nxt == '"':
                    self._add('"')
                    self.i += 2
                    continue
                self.i += 1
                return
            if c == "`":
                self._add(nxt)
                self.i += 2
            elif c == "$" and nxt == "(":
                start = self.i
                self.i += 2
                self._nested_list(")")
                self._add(s[start : self.i])
            else:
                self._add(c)
                self.i += 1

    def _here_string(self) -> bool:
        """@'...'@ / @"..."@: the opener must end its line and the closer must start a line."""
        s = self.s
        quote = s[self.i + 1]
        line_end = s.find("\n", self.i + 2)
        if line_end < 0 or s[self.i + 2 : line_end].strip():
            return False
        terminator = "\n" + quote + "@"
        end = s.find(terminator, line_end)
        if end < 0:
            raise ParseError("unterminated here-string")
        self._add(s[line_end + 1 : end])
        self.i = end + len(terminator)
        return True
