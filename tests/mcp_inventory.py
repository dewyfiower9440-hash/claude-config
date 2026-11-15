"""The tools of the local MCP servers as the servers describe them, and the check that the
permission rules of settings.json cover them (P4-07).

    python -I tests/mcp_inventory.py            -> the inventory as JSON (stdout)
    python -I tests/mcp_inventory.py --check    -> compare the servers with tests/mcp_tools.json

Run it after a server is updated. It sends `tools/list` only: no tool is called, nothing is
changed on GitHub or in a browser. Not part of the gate (the servers are not on CI); the
gate's tests/test_mcp_permissions.py works from the recorded tests/mcp_tools.json and
`problems()` below.

A tool counts as a read when the server marks it `readOnlyHint: true`. That is the
server's own claim; every other tool is treated as a write.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RECORD = HERE / "mcp_tools.json"
TOOLS_ROOT = Path("C:/dev/tools")
SERVERS = {
    "github": TOOLS_ROOT / "github-mcp-server" / "github-mcp.cmd",
    "playwright": TOOLS_ROOT / "playwright-mcp" / "playwright-mcp.cmd",
}
LIST_TIMEOUT_S = 60
# The servers allowed as a whole. Adding one is a permission change (it needs the user's
# approval): a server allowed as a whole lets every tool it gets later run unconfirmed.
SERVER_WIDE_ALLOW = ("context7",)


def rule_target(rule: object) -> tuple[str, str | None] | None:
    """(server, tool or None for the whole server) of an MCP permission rule; None for a
    rule that is not about MCP."""
    if not isinstance(rule, str) or not rule.startswith("mcp__"):
        return None
    server, _sep, tool = rule[len("mcp__") :].partition("__")
    if not server:
        return None
    if not _sep or tool == "*":
        return server, None  # `mcp__server` and `mcp__server__*`: the whole server
    return server, tool  # "" for a rule that ends in `__` and so names nothing


def _targets(rules: object) -> list[tuple[str, str | None]]:
    found = [rule_target(rule) for rule in (rules if isinstance(rules, list) else [])]
    return [target for target in found if target is not None]


def problems(
    permissions: dict[str, Any],
    inventory: dict[str, Any],
    tolerated: dict[str, set[str]],
) -> list[str]:
    """What is wrong between the permission rules and the recorded tools (empty: nothing).

    - a tool the server does not mark read-only must be under `ask` or `deny`, or be named
      in `tolerated[server]` (kept allowed on purpose, with the reason next to that list)
    - a rule that allows a single tool may only name a read-only or a tolerated tool
    - a server may be allowed as a whole only if it is one of SERVER_WIDE_ALLOW
    - an `ask` or `allow` rule must name a tool the server has (a `deny` may name any)
    - a rule may not end in `__` (it names nothing) or be listed twice
    - a tool of a server that is not in the record cannot be judged: allowing it by a rule
      of its own is reported (asking for or denying it is always fine)

    Servers that are in neither the record nor the rules are not looked at.
    """
    allow, ask, deny = (_targets(permissions.get(k)) for k in ("allow", "ask", "deny"))
    out: list[str] = []
    for kind, rules in (("allow", allow), ("ask", ask), ("deny", deny)):
        seen: set[tuple[str, str | None]] = set()
        for server, tool in rules:
            if tool == "":
                out.append(f"{server}: {kind} rule `mcp__{server}__` names no tool")
            elif (server, tool) in seen:
                out.append(
                    f"{server}: {kind} rule for `{tool or 'the whole server'}` is listed twice"
                )
            seen.add((server, tool))
    servers = inventory.get("servers") if isinstance(inventory, dict) else None
    servers = servers if isinstance(servers, dict) else {}
    for server, tool in allow:
        if tool and server not in servers:
            out.append(
                f"{server}: allow rule for `{tool}`, a tool of a server that is not recorded"
            )
    for server, entry in sorted(servers.items()):
        tools = entry.get("tools") if isinstance(entry, dict) else None
        tools = tools if isinstance(tools, dict) else {}
        whole = any(s == server and tool is None for s, tool in (*ask, *deny))
        confirmed = {tool for s, tool in (*ask, *deny) if s == server and tool}
        for name, hints in sorted(tools.items()):
            read_only = isinstance(hints, dict) and hints.get("readOnly") is True
            if read_only:
                continue
            if (
                not whole
                and name not in confirmed
                and name not in tolerated.get(server, set())
            ):
                out.append(
                    f"{server}: write tool `{name}` is neither under ask nor deny"
                )
            if (server, name) in allow and name not in tolerated.get(server, set()):
                out.append(
                    f"{server}: write tool `{name}` is allowed by a rule of its own"
                )
        for kind, rules in (("ask", ask), ("allow", allow)):
            for s, tool in rules:
                if s == server and tool and tool not in tools:
                    out.append(
                        f"{server}: {kind} rule names `{tool}`, which the server does not have"
                    )
    for server, tool in allow:
        if tool is None and server not in SERVER_WIDE_ALLOW:
            out.append(f"{server}: the whole server is allowed")
    return out


def _list_tools(launcher: Path) -> dict[str, dict[str, object]]:
    """The server's answer to `tools/list`. Raises RuntimeError when the server does not
    start, does not answer within LIST_TIMEOUT_S, or answers with an error."""
    import queue
    import threading

    try:
        p = subprocess.Popen(
            ["cmd", "/c", str(launcher)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        raise RuntimeError(
            f"{launcher.name} could not be started ({type(e).__name__})"
        ) from e
    assert p.stdin and p.stdout
    lines: queue.Queue[str | None] = queue.Queue()

    def pump(stream: Any) -> None:
        for line in stream:
            lines.put(line)
        lines.put(None)  # the server closed its output

    threading.Thread(target=pump, args=(p.stdout,), daemon=True).start()

    def send(message: dict[str, object]) -> dict[str, Any] | None:
        assert p.stdin
        p.stdin.write(json.dumps(message) + "\n")
        p.stdin.flush()
        if "id" not in message:
            return None
        deadline = time.monotonic() + LIST_TIMEOUT_S
        while True:
            try:
                line = lines.get(timeout=max(deadline - time.monotonic(), 0.01))
            except queue.Empty:
                raise RuntimeError(
                    f"no answer from {launcher.name} in {LIST_TIMEOUT_S}s"
                ) from None
            if line is None:
                raise RuntimeError(f"{launcher.name} ended without an answer")
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # a banner or a warning on stdout, not a message
            if isinstance(reply, dict) and reply.get("id") == message["id"]:
                return reply

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "inventory", "version": "0"},
                },
            }
        )
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        reply = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    except OSError as e:
        raise RuntimeError(
            f"{launcher.name} stopped taking input ({type(e).__name__})"
        ) from e
    finally:
        # `cmd /c` started the server as its child: end the whole tree, not only cmd
        subprocess.run(
            ["taskkill", "/PID", str(p.pid), "/T", "/F"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    listed = (
        (reply or {}).get("result", {}).get("tools")
        if isinstance(reply, dict)
        else None
    )
    tools = [
        tool
        for tool in (listed if isinstance(listed, list) else [])
        if isinstance(tool, dict) and tool.get("name")
    ]
    if not tools:  # an error reply, or a server without tools: nothing to record
        raise RuntimeError(f"{launcher.name} did not list its tools")
    return {
        str(tool["name"]): {
            "readOnly": (tool.get("annotations") or {}).get("readOnlyHint"),
            "destructive": (tool.get("annotations") or {}).get("destructiveHint"),
        }
        for tool in sorted(tools, key=lambda item: str(item["name"]))
    }


def _version(server: str) -> str:
    try:
        if server == "github":
            text = (TOOLS_ROOT / "github-mcp-server" / "VERSION").read_text(
                encoding="utf-8"
            )
            line = next(
                (ln for ln in text.splitlines() if ln.startswith("Version:")), ""
            )
            return line.partition(":")[2].strip() or "?"
        package = (
            TOOLS_ROOT
            / "playwright-mcp"
            / "node_modules"
            / "@playwright"
            / "mcp"
            / "package.json"
        )
        return str(json.loads(package.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "?"


def read_servers() -> dict[str, Any]:
    return {
        "read_on": time.strftime("%Y-%m-%d"),
        "how": "tools/list of the local servers; readOnly and destructive are the servers' own hints",
        "servers": {
            name: {"version": _version(name), "tools": _list_tools(launcher)}
            for name, launcher in SERVERS.items()
        },
    }


def main(argv: list[str]) -> int:
    try:
        now = read_servers()
    except RuntimeError as e:
        print(f"mcp_inventory: {e}", file=sys.stderr)
        return 2
    if "--check" not in argv:
        sys.stdout.write(json.dumps(now, indent=1, sort_keys=True) + "\n")
        return 0
    recorded = json.loads(RECORD.read_text(encoding="utf-8"))
    different = 0
    for server, entry in now["servers"].items():
        old = (recorded.get("servers") or {}).get(server, {}).get("tools", {})
        new = entry["tools"]
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                different += 1
                print(
                    f"{server}: {name}: recorded {old.get(name)}, server says {new.get(name)}"
                )
    print(f"{different} difference(s) between the servers and {RECORD.name}")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
