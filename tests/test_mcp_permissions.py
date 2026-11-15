"""P4-07: every MCP tool that is not marked read-only by its server needs a confirmation.

    python -I tests/test_mcp_permissions.py

Reads settings.json and the recorded tool lists (tests/mcp_tools.json); no server is
started and nothing is called. The record is refreshed with tests/mcp_inventory.py after a
server update.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import mcp_inventory as inv
except ImportError:  # before the module exists: every check below fails, none crashes
    inv = None

fails = 0

# Playwright tools its server marks as not read-only that stay allowed without a
# confirmation (the user's decision of 2026-11-16): they open, show or close a page but do
# not act on its content. A page that is opened still sends a request to its address and
# brings the page's text into the conversation. Adding a name here is a permission change:
# it needs the user's approval, like the settings themselves.
OPENS_OR_SHOWS = {
    "playwright": {
        "browser_navigate",
        "browser_navigate_back",
        "browser_tabs",
        "browser_resize",
        "browser_close",
    }
}


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:300],
        "" if ok else f"(want {want!r})",
    )


def target(rule: object) -> Any:
    return inv.rule_target(rule) if hasattr(inv, "rule_target") else "missing"


def found(
    permissions: dict[str, Any], tolerated: dict[str, set[str]] | None = None
) -> Any:
    if not hasattr(inv, "problems"):
        return "missing"
    return inv.problems(permissions, INVENTORY, tolerated or {})


# ---------------------------------------------------------------- pure
check(
    "M1 a rule's target: whole server, one tool, a wildcard, not an MCP rule",
    [
        target("mcp__context7"),
        target("mcp__github__push_files"),
        target("mcp__playwright__*"),
        target("mcp__claude_ai_Google_Drive__share_file"),
        target("Bash(git push:*)"),
        target("mcp__"),
        target(7),
    ],
    [
        ("context7", None),
        ("github", "push_files"),
        ("playwright", None),
        ("claude_ai_Google_Drive", "share_file"),
        None,
        None,
        None,
    ],
)
INVENTORY = {
    "servers": {
        "srv": {
            "tools": {
                "look": {"readOnly": True, "destructive": None},
                "change": {"readOnly": False, "destructive": None},
                "wipe": {"readOnly": False, "destructive": True},
                "unmarked": {"readOnly": None, "destructive": None},
            }
        }
    }
}
check(
    "M2 write tools (and tools without a mark) need ask or deny",
    [
        found({"ask": ["mcp__srv__change"], "deny": ["mcp__srv__wipe"]}),
        found(
            {
                "ask": ["mcp__srv__change", "mcp__srv__unmarked"],
                "deny": ["mcp__srv__wipe"],
            }
        ),
        found({"ask": ["mcp__srv"]}),
        found({"deny": ["mcp__srv__*"]}),
        found({}),
    ],
    [
        ["srv: write tool `unmarked` is neither under ask nor deny"],
        [],
        [],
        [],
        [
            "srv: write tool `change` is neither under ask nor deny",
            "srv: write tool `unmarked` is neither under ask nor deny",
            "srv: write tool `wipe` is neither under ask nor deny",
        ],
    ],
)
COVERED = {"ask": ["mcp__srv__change", "mcp__srv__unmarked", "mcp__srv__wipe"]}
check(
    "M3 a write tool kept allowed on purpose is named; allowing another one is a problem",
    [
        found({"ask": ["mcp__srv__change", "mcp__srv__wipe"]}, {"srv": {"unmarked"}}),
        found(
            {
                "ask": ["mcp__srv__change", "mcp__srv__wipe"],
                "allow": ["mcp__srv__unmarked"],
            },
            {"srv": {"unmarked"}},
        ),
        found({**COVERED, "allow": ["mcp__srv__look"]}),
        found({**COVERED, "allow": ["mcp__srv__change"]}),
    ],
    [[], [], [], ["srv: write tool `change` is allowed by a rule of its own"]],
)
check(
    "M4 only context7 may be allowed as a whole; a deny may name anything",
    [
        found({**COVERED, "allow": ["mcp__srv"]}),
        found({**COVERED, "allow": ["mcp__context7"]}),
        found({**COVERED, "allow": ["mcp__playwright"]}),
        found({**COVERED, "allow": ["mcp__playwright__*"]}),
        found({**COVERED, "deny": ["mcp__srv__gone", "mcp__other__x"]}),
    ],
    [
        ["srv: the whole server is allowed"],
        [],
        ["playwright: the whole server is allowed"],
        ["playwright: the whole server is allowed"],
        [],
    ],
)
check(
    "M4 a tool of a server that is not recorded cannot be judged: allowing it is a problem",
    [
        found({**COVERED, "allow": ["mcp__context7__query-docs"]}),
        found({**COVERED, "allow": ["mcp__claude_ai_Gmail__send_email"]}),
        found(
            {**COVERED, "ask": [*COVERED["ask"], "mcp__claude_ai_Gmail__send_email"]}
        ),
    ],
    [
        [
            "context7: allow rule for `query-docs`, a tool of a server that is not recorded"
        ],
        [
            "claude_ai_Gmail: allow rule for `send_email`, a tool of a server that is not recorded"
        ],
        [],
    ],
)
check(
    "M4 a rule that ends in two underscores names nothing; a rule listed twice",
    [
        target("mcp__github__"),
        found({**COVERED, "ask": [*COVERED["ask"], "mcp__srv__"]}),
        found({**COVERED, "deny": ["mcp__srv__wipe", "mcp__srv__wipe"]}),
        found({"ask": [*COVERED["ask"], "mcp__srv__change"]}),
    ],
    [
        ("github", ""),
        ["srv: ask rule `mcp__srv__` names no tool"],
        ["srv: deny rule for `wipe` is listed twice"],
        ["srv: ask rule for `change` is listed twice"],
    ],
)
check(
    "M5 an ask or allow rule for a tool the server does not have is stale",
    [
        found({**COVERED, "ask": [*COVERED["ask"], "mcp__srv__gone"]}),
        found({**COVERED, "allow": ["mcp__srv__gone"]}),
    ],
    [
        ["srv: ask rule names `gone`, which the server does not have"],
        ["srv: allow rule names `gone`, which the server does not have"],
    ],
)
check(
    "M5 odd input: no error",
    [
        found({"ask": "x", "allow": None, "deny": [3, None]}) != [],
        inv.problems({}, {}, {}) if hasattr(inv, "problems") else None,
    ],
    [True, []],
)

# ---------------------------------------------------------------- the real files
RECORD = HERE / "mcp_tools.json"
SETTINGS = HERE.parent / "settings.json"
INVENTORY = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.is_file() else {}
permissions = json.loads(SETTINGS.read_text(encoding="utf-8")).get("permissions", {})
servers = INVENTORY.get("servers", {})
check(
    "M6 the record: two servers, their tools, marks that are true, false or absent",
    (
        sorted(servers),
        {name: len(entry.get("tools", {})) for name, entry in servers.items()},
        all(
            set(hints) == {"readOnly", "destructive"}
            and all(v in (True, False, None) for v in hints.values())
            for entry in servers.values()
            for hints in entry.get("tools", {}).values()
        ),
    ),
    (["github", "playwright"], {"github": 48, "playwright": 26}, True),
)
check(
    "M7 settings.json: every write tool of the recorded servers is confirmed, no stale rule",
    found(permissions, OPENS_OR_SHOWS),
    [],
)
check(
    "M7 ... the tools kept allowed on purpose exist and are not read-only (else the list is stale)",
    sorted(
        name
        for server, names in OPENS_OR_SHOWS.items()
        for name in names
        if servers.get(server, {}).get("tools", {}).get(name, {}).get("readOnly")
        is not False
    ),
    [],
)
allowed = {r for r in permissions.get("allow", []) if isinstance(r, str)}
check(
    "M7 ... each of them is allowed by a rule of its own, and no server but context7 as a whole",
    (
        sorted(
            f"{server}:{name}"
            for server, names in OPENS_OR_SHOWS.items()
            for name in names
            if f"mcp__{server}__{name}" not in allowed
        ),
        sorted(r for r in allowed if r.startswith("mcp__") and r.count("__") == 1),
    ),
    ([], ["mcp__context7"]),
)
check(
    "M7 ... page-acting tools are confirmed: hover and click among them",
    [
        f"mcp__playwright__browser_{n}" in permissions.get("ask", [])
        for n in ("hover", "click", "evaluate")
    ],
    [True, True, True],
)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
