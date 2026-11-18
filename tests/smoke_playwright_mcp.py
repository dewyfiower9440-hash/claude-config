"""Smoke test for the pinned Playwright MCP launcher (stdio): version and gated tools."""

import json
import subprocess
import sys
import threading
from pathlib import Path

WAIT_S = 120  # the first start may download the pinned package
LAUNCHER = r"C:\dev\tools\playwright-mcp\playwright-mcp.cmd"
PINNED = "0.0.81"
GATED = {"browser_run_code_unsafe", "browser_file_upload"}

p = subprocess.Popen(
    ["cmd", "/c", LAUNCHER],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    encoding="utf-8",
)
assert p.stdin and p.stdout
# a server that never answers ends here (the read below then fails), not never
watchdog = threading.Timer(WAIT_S, p.kill)
watchdog.daemon = True
watchdog.start()


def send(m: dict) -> dict | None:
    assert p.stdin and p.stdout
    p.stdin.write(json.dumps(m) + "\n")
    p.stdin.flush()
    if "id" not in m:
        return None
    while True:
        r = json.loads(p.stdout.readline())
        if r.get("id") == m["id"]:
            return r


init = send(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        },
    }
)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
p.terminate()
assert init and tools
# serverInfo.version reports the bundled Playwright build, not the @playwright/mcp package.
pkg = Path(LAUNCHER).parent / "node_modules" / "@playwright" / "mcp" / "package.json"
version = json.loads(pkg.read_text(encoding="utf-8"))["version"]
names = {t["name"] for t in tools["result"]["tools"]}
print(
    "server:",
    init["result"]["serverInfo"].get("name"),
    init["result"]["serverInfo"].get("version"),
)
print("package version:", version, "pinned ok:", version == PINNED)
print("tool count:", len(names))
missing = sorted(GATED - names)
print("gated tools present:", not missing, missing or "")
sys.exit(0 if version == PINNED and not missing else 1)
