"""v1.1 step 1: the hook records which skills and MCP servers the session used, so a later
check can tell "not used" from "not seen". Never a reason to block.

    python -I tests/test_usage_state.py

A throw-away repository outside %TEMP%, an isolated temp folder and approval home.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
TMP_ROOT = Path(os.environ.get("GATE_TMP_ROOT") or Path(__file__).resolve().parent)
ROOT = TMP_ROOT / "_tmp_usage_state"
WORK_ROOT = Path(os.environ.get("GATE_WORK_ROOT", "C:/dev"))
PROJ = WORK_ROOT / "_usage_state_proj"
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
TMPD = ROOT / "tmp"
TMPD.mkdir(parents=True)
HOME = ROOT / "home"
os.environ["CLAUDE_DISCIPLINE_HOME"] = str(HOME)
os.environ["CLAUDE_DISCIPLINE_WITNESS"] = str(ROOT / "w" / "hwm")
sys.path.insert(0, str(HOOKS))
import discipline as disc

ENV = dict(os.environ, TMPDIR=str(TMPD), TEMP=str(TMPD), TMP=str(TMPD))
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


PROJ.mkdir(parents=True)
subprocess.run(["git", "init", "-q"], cwd=PROJ, check=True)


def hook(
    mode: str, s: str, env: dict = ENV, prompt_id: str = "p1", **payload: object
) -> tuple[int, dict]:
    p = subprocess.run(
        [sys.executable, "-I", str(HOOKS / "discipline.py"), mode],
        input=json.dumps(
            {"session_id": s, "cwd": str(PROJ), "prompt_id": prompt_id, **payload}
        ),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else {})


def state_of(s: str) -> dict:
    return json.loads(
        (TMPD / "claude-discipline" / f"{s}.json").read_text(encoding="utf-8")
    )


def post(s: str, tool: str, prompt_id: str = "p1", **ti: object) -> tuple[int, dict]:
    return hook(
        "post",
        s,
        prompt_id=prompt_id,
        tool_name=tool,
        tool_input=ti,
        tool_use_id=uuid.uuid4().hex,
        tool_response={"ok": True},
    )


# ---------------------------------------------------------------- the state's shape
check(
    "U-T1 the default state has the usage keys",
    (disc.default_state().get("skills"), disc.default_state().get("mcp")),
    ([], {}),
)
check(
    "U-T2 the matcher names the Skill tool and MCP tools",
    getattr(disc, "USAGE_TOOLS_MATCHER", None),
    "Skill|mcp__.*",
)

# ---------------------------------------------------------------- skills
s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
rc, out = post(s, "Skill", skill="commit", args="")
st = state_of(s)
check(
    "U-T3 a Skill call: recorded with its name and turn, nothing printed",
    (rc, out, [(r.get("skill"), r.get("turn")) for r in st.get("skills", [])]),
    (0, {}, [("commit", 1)]),
)
check(
    "U-T3 ... the record carries a time",
    isinstance(st["skills"][0].get("at"), int | float) and st["skills"][0]["at"] > 0,
    True,
)
hook("prompt", s, prompt_id="p2", prompt="next")
post(s, "Skill", prompt_id="p2", skill="plan")
check(
    "U-T4 the next turn's skill is appended, oldest first",
    [(r["skill"], r["turn"]) for r in state_of(s)["skills"]],
    [("commit", 1), ("plan", 2)],
)
post(s, "Skill", prompt_id="p2", skill="a b\n<x>" + "y" * 100)
check(
    "U-T5 a skill name is kept to plain name characters and a bounded length",
    state_of(s)["skills"][-1]["skill"],
    ("abxy" + "y" * 100)[: disc.MAX_SKILL_NAME],
)
post(s, "Skill", prompt_id="p2", args="only args")
check(
    "U-T6 a Skill call without a name records nothing",
    len(state_of(s)["skills"]),
    3,
)
for i in range(disc.MAX_SKILLS + 5):
    post(s, "Skill", prompt_id="p2", skill=f"s{i}")
kept = state_of(s)["skills"]
check(
    "U-T7 the list is capped; the newest stay",
    (len(kept), kept[-1]["skill"]),
    (disc.MAX_SKILLS, f"s{disc.MAX_SKILLS + 4}"),
)

# ---------------------------------------------------------------- MCP servers
s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
rc, out = post(s, "mcp__context7__query-docs", libraryId="/x/y", query="q")
st = state_of(s)
check(
    "U-T8 an MCP tool: counted under its server, nothing printed",
    (rc, out, st.get("mcp", {}).get("context7", {}).get("count")),
    (0, {}, 1),
)
check(
    "U-T8 ... with the turn of the last call, no tool name and no arguments",
    (
        st["mcp"]["context7"].get("last"),
        "query-docs" in json.dumps(st["mcp"]),
        "/x/y" in json.dumps(st),
    ),
    (1, False, False),
)
post(s, "mcp__context7__resolve-library-id", libraryName="z")
post(s, "mcp__github__get_me")
st = state_of(s)
check(
    "U-T9 a second call of the server adds to its count; another server gets its own row",
    (st["mcp"]["context7"]["count"], st["mcp"]["github"]["count"]),
    (2, 1),
)
post(s, "mcp__" + "a" * 200 + "__t")
post(s, "mcp__weird name!__t")
post(s, "mcp__")
check(
    "U-T10 a server name is kept to plain characters and a bounded length; an empty one is dropped",
    sorted(state_of(s)["mcp"]),
    sorted(["context7", "github", "a" * disc.MAX_MCP_SERVER_NAME, "weirdname"]),
)
for i in range(disc.MAX_MCP_SERVERS + 3):
    post(s, f"mcp__srv{i}__t")
check(
    "U-T11 the number of servers is capped",
    len(state_of(s)["mcp"]) <= disc.MAX_MCP_SERVERS,
    True,
)

# ---------------------------------------------------------------- other tools
s = uuid.uuid4().hex
hook("prompt", s, prompt="work")
post(s, "Bash", command="echo skill mcp__x__y")
post(s, "Agent", subagent_type="Explore", prompt="look")
post(s, "Read", file_path=str(PROJ / "a.md"))
st = state_of(s)
check(
    "U-T12 other tools record no skill and no MCP use",
    (st.get("skills"), st.get("mcp")),
    ([], {}),
)
check(
    "U-T13 a saved state missing the usage keys is still well-shaped; a wrong type is named",
    (
        disc.state_shape_problem({"turn": 1}),
        disc.state_shape_problem({"skills": {}}),
        disc.state_shape_problem({"mcp": []}),
    ),
    (None, "skills", "mcp"),
)

# ---------------------------------------------------------------- settings.json wiring
settings = json.loads((HERE.parent / "settings.json").read_text(encoding="utf-8"))
post_matchers = [h.get("matcher", "") for h in settings["hooks"]["PostToolUse"]]
check(
    "U-T14 settings.json: the discipline post hook also matches Skill and MCP tools",
    any(
        "Skill" in m.split("|") and "mcp__.*" in m.split("|") and "Bash" in m.split("|")
        for m in post_matchers
    ),
    True,
)

print(f"\nFAILURES: {fails}")
for p in (ROOT, PROJ):
    shutil.rmtree(p, ignore_errors=True)
sys.exit(1 if fails else 0)
