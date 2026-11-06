import json
import subprocess

p = subprocess.Popen(
    ["cmd", "/c", r"C:\dev\tools\github-mcp-server\github-mcp.cmd"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    encoding="utf-8",
)
assert p.stdin and p.stdout


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


send(
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
assert tools
names = {t["name"] for t in tools["result"]["tools"]}
print("tool count:", len(names))
EXCLUDED = {"delete_repository", "fork_repository", "create_repository"}
leaked = sorted(EXCLUDED & names)
print("excluded tools absent:", not leaked, leaked or "")
print("write tools still available (gated by ask):", "push_files" in names)
me = send(
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_me", "arguments": {}},
    }
)
assert me
text = me["result"]["content"][0]["text"]
print("get_me login:", json.loads(text).get("login"))
p.terminate()
