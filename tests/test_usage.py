"""P1-07: the size of the folders that grow is measured at session end and a session start
says so when one is over its limit. Nothing is ever deleted.

    python -I tests/test_usage.py

Throw-away folders only; the real folders are neither measured nor written from here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
ROOT = Path(tempfile.mkdtemp(prefix="usage-"))
# The SessionStart hook lists the vault's knowledge notes: never this machine's real ones.
os.environ["CLAUDE_VAULT_KNOWLEDGE"] = "0"
os.environ["CLAUDE_USAGE_FILE"] = str(ROOT / "usage.json")
sys.path.insert(0, str(HOOKS))
import usage

fails = 0
NOW = 1_800_000_000.0
MB = 1_000_000


def check(name: str, got: object, want: object) -> None:
    global fails
    ok = got == want
    fails += not ok
    print(
        ("OK  " if ok else "FAIL"),
        name,
        "->",
        repr(got)[:160],
        "" if ok else f"(want {want!r})",
    )


def put(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


d = ROOT / "tree"
put(d / "a.bin", 100)
put(d / "sub" / "b.bin", 250)
put(d / "sub" / "deep" / "c.bin", 5)
outside = ROOT / "outside"
put(outside / "big.bin", 9000)
try:
    import _winapi

    _winapi.CreateJunction(str(outside), str(d / "link"))
    linked = True
except (ImportError, OSError):
    try:
        os.symlink(outside, d / "link", target_is_directory=True)
        linked = True
    except OSError:
        linked = False
        print("NOTICE: no link could be made here; U-T1 runs without one")
check(
    "U-T1 files below a folder are counted with their sizes; a link is not followed",
    usage.measure(d, 5.0),
    {"files": 3, "bytes": 355, "partial": False},
)
(ROOT / "afile").write_text("x", encoding="utf-8")
check(
    "U-T2 a missing folder / a file in its place: nothing, no error",
    (usage.measure(ROOT / "none", 5.0), usage.measure(ROOT / "afile", 5.0)),
    ({"files": 0, "bytes": 0, "partial": False},) * 2,
)
ticks = iter([0.0, 0.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0])
cut = usage.measure(d, 1.0, clock=lambda: next(ticks))
check(
    "U-T3 out of time: what was seen so far, marked partial",
    (cut["partial"], cut["files"] < 3),
    (True, True),
)


def snap(**over: object) -> dict:
    folders = {
        "projects": {"files": 100, "bytes": 400 * MB, "partial": False},
        "file_history": {"files": 2000, "bytes": 40 * MB, "partial": False},
        "state": {"files": 5, "bytes": 1000, "partial": False},
        "sessions": {"files": 30000, "bytes": 60 * MB, "partial": False},
        "observations": {"files": 6, "bytes": 2 * MB, "partial": False},
    }
    out: dict = {"v": 1, "at": NOW - 3600, "free_gb": 270.0, "folders": folders}
    for key, value in over.items():
        if key in folders:
            folders[key] = {**folders[key], **value}
        else:
            out[key] = value
    return out


check("U-T4 everything inside its limit", usage.warnings(snap(), NOW), [])
cases = {
    "projects": snap(projects={"bytes": 2500 * MB}),
    "file_history": snap(file_history={"bytes": 600 * MB}),
    "sessions": snap(sessions={"files": 150000}),
    "observations": snap(observations={"bytes": 80 * MB}),
    "free": snap(free_gb=12.5),
}
got = {name: usage.warnings(s, NOW) for name, s in cases.items()}
check(
    "U-T5 each limit that is passed gives one line",
    {k: len(v) for k, v in got.items()},
    dict.fromkeys(cases, 1),
)
check(
    "U-T5 ... with the measured value and the limit in it",
    [
        "2500" in got["projects"][0] and "2000" in got["projects"][0],
        "600" in got["file_history"][0] and "500" in got["file_history"][0],
        "150000" in got["sessions"][0] and "100000" in got["sessions"][0],
        "80" in got["observations"][0] and "50" in got["observations"][0],
        "12.5" in got["free"][0] and "20" in got["free"][0],
    ],
    [True] * 5,
)
both = usage.warnings(snap(projects={"bytes": 2500 * MB}, free_gb=3.0), NOW)
check("U-T5 two limits passed: two lines", len(both), 2)
check(
    "U-T6 an old measurement, none at all, odd values: no lines, no error",
    [
        usage.warnings(snap(at=NOW - 15 * 86400, projects={"bytes": 2500 * MB}), NOW),
        usage.warnings(None, NOW),
        usage.warnings({"at": "x", "folders": "y"}, NOW),
        usage.warnings(
            {
                "v": 1,
                "at": NOW,
                "free_gb": "low",
                "folders": {"projects": {"bytes": "big"}},
            },
            NOW,
        ),
        usage.warnings(snap(at=NOW + 99999, free_gb=1.0), NOW),
    ],
    [[], [], [], [], []],
)
partial = usage.warnings(snap(projects={"bytes": 2500 * MB, "partial": True}), NOW)
check(
    "U-T5 a partial measurement over the limit still warns (it is at least that much)",
    len(partial),
    1,
)

usage.write({**snap(), "paths": ["C:/secret"], "note": "x"})
back = usage.read()
check(
    "U-T7 what is written comes back; keys that are not allow-listed do not",
    (
        back is not None and sorted(back),
        back is not None and back["folders"]["projects"],
        back is not None and back["free_gb"],
    ),
    (
        ["at", "folders", "free_gb", "v"],
        {"files": 100, "bytes": 400 * MB, "partial": False},
        270.0,
    ),
)
Path(os.environ["CLAUDE_USAGE_FILE"]).write_text("{not json", encoding="utf-8")
check("U-T7 a damaged file reads as nothing", usage.read(), None)

huge = "9" * 400
for label, text in (
    ("a number too large for a float", '{"at": ' + huge + ', "folders": {}}'),
    (
        "Infinity",
        '{"at": 1, "free_gb": Infinity, "folders": {"projects": {"files": Infinity, "bytes": 1}}}',
    ),
    ("NaN", '{"at": NaN, "folders": {}}'),
):
    Path(os.environ["CLAUDE_USAGE_FILE"]).write_text(text, encoding="utf-8")
    try:
        result: object = (usage.read() or {}).get("folders", {})
    except (OverflowError, ValueError) as e:
        result = type(e).__name__
    check(
        f"U-T7 a record with {label}: read without error, nothing unusable kept",
        result,
        {},
    )

# every folder gets its turn: a slow first one does not leave the others unmeasured
slow_clock = iter(x * 0.3 for x in range(1000))
spread = (
    usage.snapshot(
        NOW,
        budget_s=1.0,
        clock=lambda: next(slow_clock),
        places={
            "projects": d,
            "file_history": d,
            "state": d,
            "sessions": d,
            "observations": d,
        },
    )
    if "places" in usage.snapshot.__code__.co_varnames
    else {"folders": {}}
)
check(
    "U-T3 a budget that one folder could use up alone is shared: every folder has an entry",
    sorted(spread["folders"]),
    ["file_history", "observations", "projects", "sessions", "state"],
)

# ---------------------------------------------------------------- through the hooks
CH = ROOT / "claude"
put(CH / "projects" / "p1" / "t.jsonl", 3000)
put(CH / "projects" / "p2" / "SECRET-NAME.jsonl", 2000)
put(CH / "file-history" / "h" / "f@v1", 700)
put(CH / "state" / "ledger" / "x.jsonl", 50)
sessions = ROOT / "tmp" / "claude-discipline"
put(sessions / "s1.json", 10)
put(sessions / "s2.json", 10)
obs = ROOT / "home"
put(obs / "authz-observe.jsonl", 400)
plain = ROOT / "plain"
plain.mkdir()
ENV = dict(
    os.environ,
    CLAUDE_USAGE_FILE=str(ROOT / "hook-usage.json"),
    CLAUDE_USAGE_CLAUDE_HOME=str(CH),
    CLAUDE_STATE_GC_DIR=str(sessions),
    CLAUDE_DISCIPLINE_HOME=str(obs),
    CLAUDE_HANDOFF_HOME=str(ROOT / "store"),
)


def end(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(HOOKS / "handoff.py"), "end"],
        input=json.dumps(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "u",
                "reason": "other",
                "cwd": str(plain),
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


p = end(ENV)
written = ROOT / "hook-usage.json"
rec = json.loads(written.read_text(encoding="utf-8")) if written.is_file() else {}
check(
    "U-T8 a session end measures the folders and records it; exit 0, nothing on stdout",
    (
        p.returncode,
        p.stdout,
        {
            k: (v.get("files"), v.get("bytes"))
            for k, v in (rec.get("folders") or {}).items()
        },
        isinstance(rec.get("free_gb"), float),
        abs(float(rec.get("at") or 0) - time.time()) < 120,
    ),
    (
        0,
        "",
        {
            "projects": (2, 5000),
            "file_history": (1, 700),
            "state": (1, 50),
            "sessions": (2, 20),
            "observations": (1, 400),
        },
        True,
        True,
    ),
)
check(
    "U-T11 the record holds no file name or path",
    [
        x in json.dumps(rec)
        for x in ("SECRET-NAME", "jsonl", str(ROOT.name), "claude-discipline")
    ],
    [False, False, False, False],
)
isolated = dict(ENV)
isolated.pop("CLAUDE_USAGE_CLAUDE_HOME")
isolated["CLAUDE_USAGE_FILE"] = str(ROOT / "isolated-usage.json")
end(isolated)
check(
    "U-T8 a run with an isolated handoff home and no folders named measures nothing (not the real ones)",
    (ROOT / "isolated-usage.json").exists(),
    False,
)
blocked = dict(
    ENV,
    CLAUDE_USAGE_FILE=str(ROOT / "afile" / "usage.json"),
    CLAUDE_HANDOFF_HOME=str(ROOT / "store2"),
)
repo = ROOT / "repo"
repo.mkdir()
subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
p = subprocess.run(
    [sys.executable, "-I", str(HOOKS / "handoff.py"), "end"],
    input=json.dumps(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "u",
            "reason": "other",
            "cwd": str(repo),
        }
    ),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=blocked,
    check=False,
)
check(
    "U-T9 a usage file that cannot be written: exit 0 and the handoff record is still written",
    (
        p.returncode,
        p.stdout,
        len(list((ROOT / "store2").glob("*.json"))) >= 1,
        "usage not measured" in p.stderr,
    ),
    (0, "", True, True),
)


def start(
    snapshot: dict | None, source: str = "startup", event: str | None = "SessionStart"
) -> str:
    f = ROOT / "start-usage.json"
    if snapshot is None:
        f.unlink(missing_ok=True)
    else:
        f.write_text(json.dumps(snapshot), encoding="utf-8")
    payload = {"session_id": "t", "source": source, "cwd": str(repo)}
    if event:
        payload["hook_event_name"] = event
    q = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "session_context.py")],
        input=json.dumps(payload),
        env=dict(
            ENV, CLAUDE_USAGE_FILE=str(f), CLAUDE_HANDOFF_HOME=str(ROOT / "store3")
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert q.returncode == 0, q.stderr[-300:]
    return (
        json.loads(q.stdout)["hookSpecificOutput"]["additionalContext"]
        if q.stdout.strip()
        else ""
    )


over = {**snap(projects={"bytes": 2500 * MB}), "at": time.time() - 60}
fine = {**snap(), "at": time.time() - 60}
MARK = "2500"
check(
    "U-T10 a session start says which folder is over its limit; not when all are fine",
    (MARK in start(over), MARK in start(fine), "## git" in start(fine)),
    (True, False, True),
)
check(
    "U-T10 ... not after a compaction, not without a record, not for another event",
    (
        MARK in start(over, source="compact"),
        "## git" in start(None),
        MARK in start(over, event=None),
    ),
    (False, True, False),
)
warned = start(over)
check(
    "U-T11 the warning names the kind of folder, not a path",
    (str(CH) in warned, "SECRET-NAME" in warned),
    (False, False),
)

shutil.rmtree(ROOT, ignore_errors=True)
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
