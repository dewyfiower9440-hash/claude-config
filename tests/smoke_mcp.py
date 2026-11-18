import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

db = Path(tempfile.gettempdir()) / "dbmcp-smoke.db"
c = sqlite3.connect(db)
c.execute("create table if not exists t(a int)")
c.execute("delete from t")
c.execute("insert into t values (42)")
c.commit()
c.close()

msgs = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "query", "arguments": {"sql": "SELECT a FROM t"}},
    },
    {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "query", "arguments": {"sql": "DELETE FROM t"}},
    },
]
p = subprocess.Popen(
    [
        "uv",
        "run",
        "--project",
        "C:/dev/tools/db-mcp",
        "db-mcp",
        "sqlite",
        "--path",
        str(db),
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
)
assert p.stdin and p.stdout
# a server that never answers ends here (the read below then fails), not never
watchdog = threading.Timer(60, p.kill)
watchdog.daemon = True
watchdog.start()
for m in msgs:
    p.stdin.write(json.dumps(m) + "\n")
    p.stdin.flush()
    if "id" in m:
        resp = json.loads(p.stdout.readline())
        if m["method"] == "tools/list":
            print("tools:", [t["name"] for t in resp["result"]["tools"]])
        elif m["method"] == "tools/call":
            print(
                m["params"]["arguments"]["sql"],
                "->",
                resp["result"]["content"][0]["text"].replace("\n", " / "),
            )
        else:
            print("init:", resp["result"]["serverInfo"])
p.terminate()
sys.exit(0)
