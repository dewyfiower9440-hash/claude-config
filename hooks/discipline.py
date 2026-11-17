"""Engineering-discipline hooks (one script, several events).

  discipline.py pre     PreToolUse  Edit|Write|MultiEdit|Bash|PowerShell
                        - test weakening / suppression  -> "ask" the user with the reason
                        - >MAX_FILES files edited this turn without an approved plan -> "deny" (plan first)
  discipline.py post    PostToolUse Edit|Write|MultiEdit|Bash|PowerShell|Agent|Task|ExitPlanMode
                        - records code edits, successful verification commands, plan approval
  discipline.py fail    PostToolUseFailure Bash|PowerShell|Agent|Task
                        - records failed verification commands (an attempt, with last_verify_ok=False)
  discipline.py prompt  UserPromptSubmit - starts a new turn (resets the per-turn file count)
  discipline.py stop    Stop - blocks ending the turn when code was edited after the last verification,
                        or once per failure when the last verification failed (report it honestly)

State: %TEMP%/claude-discipline/<session_id>.json
notify.py imports session_key/load_state/stop_block_reason so it never toasts on a blocked Stop.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

_T0 = time.monotonic()  # earliest point of this hook run: every budget counts from here

# stderr only (a successful hook's stderr is not shown as a result); never secrets.
log = logging.getLogger("discipline")

# python -I drops the script dir; verification.py and shellparse.py live next to this file
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


def temp_root() -> Path:
    """Same lookup order as tempfile.gettempdir(), without its ~90 ms import."""
    for var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value)
    import tempfile  # fallback only; slow import

    return Path(tempfile.gettempdir())


MAX_FILES_WITHOUT_PLAN = int(os.environ.get("CLAUDE_MAX_FILES_WITHOUT_PLAN", "5"))


def authz_mode(raw: object) -> str:
    """ "enforce" only when asked for exactly; anything else is the observing "warn"."""
    return (
        "enforce"
        if isinstance(raw, str) and raw.strip().lower() == "enforce"
        else "warn"
    )


# P0-06: under "enforce" no file outside an approved plan's scope may be edited (not even
# one). "warn", the default while the rule is being observed, keeps the five-file allowance
# and records every edit that enforce would have denied.
AUTHZ_MODE = authz_mode(os.environ.get("CLAUDE_AUTHZ_MODE"))


def authz_verdict(
    covered: bool, counted_before: bool, counted: int, limit: int, mode: str
) -> str:
    """ "allow", "deny" or "would_deny" (allowed now, denied once enforced) for one edit.
    `covered`: inside an approved scope. `counted_before`: the file already counts toward
    this turn's allowance. `counted`: files outside the scope edited this turn so far."""
    if covered:
        return "allow"
    if mode == "enforce":
        return "deny"
    return "deny" if not counted_before and counted >= limit else "would_deny"


STATE_DIR = temp_root() / "claude-discipline"

CODE_EXT = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ps1",
    ".psm1",
    ".sh",
    ".sql",
    ".go",
    ".rs",
    ".cs",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".cpp",
    ".h",
}
# Files that never count toward the per-turn limit or the verification requirement: Claude's
# own transcripts/plans/todos/memory, and scratch files under %TEMP% (see ignored_path: a git
# working tree under %TEMP%, such as a scratch worktree, is code like any other repository).
IGNORED_PATH = re.compile(
    r"([\\/]AppData[\\/]Local[\\/]Temp[\\/])|([\\/]\.claude[\\/](projects|plans|todos)[\\/])|([\\/]memory[\\/][^\\/]+\.md$)",
    re.IGNORECASE,
)
CLAUDE_OWN_PATH = re.compile(
    r"([\\/]\.claude[\\/](projects|plans|todos)[\\/])|([\\/]memory[\\/][^\\/]+\.md$)",
    re.IGNORECASE,
)


def absolute(path: str, cwd: str) -> str:
    """A tool path as the tool resolves it (relative to the session's cwd)."""
    return os.path.abspath(path if os.path.isabs(path) else os.path.join(cwd, path))


def edit_target(tool_input: dict[str, Any]) -> str:
    """The file an editing tool writes: `file_path`, or `notebook_path` (NotebookEdit)."""
    for key in ("file_path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def is_unc(path: str) -> bool:
    return os.path.splitdrive(path)[0].startswith(("\\\\", "//"))


def may_be_repository(path: str) -> bool:
    """True unless it is certain that no git working tree contains `path`: any `.git` entry
    (directory, worktree file, even corrupt) in it or an ancestor, a UNC path (git refuses or
    misjudges those), or a folder that cannot be inspected. Never asks git, so a git error is
    never read as "not a repository", and nothing is cached (a later `git init` counts)."""
    if is_unc(path):
        return True
    d = path
    while True:
        try:
            os.lstat(os.path.join(d, ".git"))
            return True  # any entry named .git: possibly a repository
        except (FileNotFoundError, NotADirectoryError):
            pass  # certainly no .git here
        except (
            OSError,
            ValueError,
        ):  # cannot be inspected (access, NUL, ...): not certain
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def ignored_path(state: dict[str, Any], path: str, cwd: str) -> bool:
    """Out of scope only when both the path as given and its real (link-resolved) path are
    ignorable, and a %TEMP% path is certainly not inside a git working tree (a scratch
    worktree is code like any repository)."""
    given = absolute(path, cwd)
    if is_unc(given) or not (
        CLAUDE_OWN_PATH.search(given) or IGNORED_PATH.search(given)
    ):
        return False  # decided without any I/O (a stalled share must not time the hook out)
    for p in (given, os.path.realpath(given)):
        if CLAUDE_OWN_PATH.search(p):
            continue
        if not IGNORED_PATH.search(p):
            return False  # a link must not move a protected file out of scope
        if may_be_repository(p):
            return False
    return True


TEST_PATH = re.compile(
    r"(^|[\\/])(tests?|__tests__|spec)([\\/])|(_test|\.test|\.spec|Tests?)\.[a-z0-9]+$|(^|[\\/])test_[^\\/]+$",
    re.IGNORECASE,
)

# Verification is judged by hooks/verification.py (declared project gate, real runners,
# attributable exit status). Agents' own claims never count: a subagent's Bash runs reach
# these hooks directly (with agent_id), and code-reviewer is recorded only as a review.
REVIEW_AGENTS = {"code-reviewer"}

# (label, regex) - counted in old vs new content; an increase is "weakening".
SUPPRESSIONS = [
    ("type: ignore", r"#\s*type:\s*ignore"),
    ("pyright: ignore", r"#\s*pyright:\s*ignore"),
    ("noqa", r"#\s*noqa\b"),
    ("pragma: no cover", r"#\s*pragma:\s*no\s*cover"),
    ("eslint-disable", r"eslint-disable"),
    ("@ts-ignore", r"@ts-ignore"),
    ("@ts-expect-error", r"@ts-expect-error"),
    ("@ts-nocheck", r"@ts-nocheck"),
    (
        "SuppressMessage",
        r"SuppressMessageAttribute|\[Diagnostics\.CodeAnalysis\.SuppressMessage",
    ),
]
TEST_SKIPS = [
    (
        "pytest skip/xfail",
        r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\(",
    ),
    ("unittest skip", r"@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b"),
    (
        "js skip/todo",
        r"\b(it|test|describe|suite)\.(skip|todo)\b|\b(xit|xtest|xdescribe)\(",
    ),
    ("js only (focus)", r"\b(it|test|describe|suite)\.only\b|\b(fit|fdescribe)\("),
    ("Pester skip", r"\s-Skip\b|Set-ItResult\s+-(Skipped|Inconclusive|Pending)"),
]
ASSERTIONS = r"\bassert\b|\bexpect\(|\bShould\s+-|\bassert[A-Z]\w*\(|\.toBe|\.toEqual|\.toThrow|pytest\.raises"
DELETE_CMD = re.compile(r"\b(rm|del|erase|Remove-Item|ri|git\s+rm)\b", re.IGNORECASE)


# ---------------------------------------------------------------- state
def default_state() -> dict[str, Any]:
    return {
        "turn": 0,
        "turn_files": [],
        "last_code_edit": 0.0,
        # project root ("" = no declared policy) -> {"at", "gate", "ci", "validate"} of the last code edit
        "dirty": {},
        # project root -> {"at", "outcome", "kind", "cmd", "why"} of the last verification evidence
        "evidence": {},
        # "<root>|<evidence at>" -> turn in which Stop was blocked for that failed/unknown result
        "notified": {},
        "last_review": 0.0,
        # plan approval: {task_id, plan_hash, scope, prompt_id[, rearmed_from]} or None
        "approval": None,
        # the task whose plan this session closed as done: {task_id, approval_id, base,
        # note[, content]}; the ledger keeps recording it while its content stays the same
        "ledger_closed": None,
        # repository top -> who acted on it ("main" or "<agent_type>:<agent_id>"); labels only
        "owners": {},
        # actor label -> {started, stopped, runs}: subagents seen starting (SubagentStart)
        # and ending (SubagentStop); shown in reasons only
        "agents": {},
        # ids of the background shell commands this session started, as PostToolUse saw
        # them (newest last, at most MAX_BACKGROUND_TASKS); see v2_keep_on_relay
        "background_tasks": [],
        # repositories already described to this session; seeded (1) by its first user
        # prompt, which records where the session is without announcing it
        "context_roots": [],
        "context_seeded": 0,
        # 1 once this session's earlier-format missed-event notes were looked for (P0-15)
        "notes_checked": 0,
        # path -> content hash of this turn's changes that an approval valid at the time
        # covered when another approval took its place (P1-01b); emptied at every prompt
        "turn_covered": {},
        # path -> content hash of what the turn had already changed when the current
        # approval was recorded (those are not that approval's changes)
        "turn_approval_from": {},
        # "<root>|<content id>" of changes already noted as lacking a current review
        "review_observed": [],
        # P1-03: what the next prompt is told about a turn without any test change, and
        # the turn that was last counted in the observation file
        "test_gap_note": "",
        "test_gap_turn": 0,
        # P2-01: what the next prompt is told about a high-risk turn without a plan that
        # compares alternatives, and the turn that was last scored in the observation file
        "risk_note": "",
        "risk_turn": 0,
        # P2-04: "<repository>|<gate or ci>|<kind>" of the failures this session has
        # already counted in the repository's recurrence file
        "failures_noted": [],
        "output_advice_turn": 0,  # P1-04: the turn that already got the delegation hint
        "turn_cover_off": 0,  # 1: not known what an approval started from; cover nothing
        # the last cover_valid: {at, n, why}; for diagnosis, never read to decide
        "cover_last": None,
        # path -> content hash of what was changed inside the scope of an approval valid at
        # that moment (cover_valid), and the listed changes of the last Stop it answers
        "turn_done_under": {},
        "turn_answered": [],
        # {id, approval}: the tool call that was just checked against the approval (by
        # PreToolUse for an Edit/Write, by PostToolUse for a shell command); cover_valid
        # need not check the same call again
        "pre_valid": None,
        # the last prompts as the hook was handed them (note_prompt_seen): diagnosis only
        "prompt_seen": [],
    }


def session_key(data: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(data.get("session_id", "default")))


def load_state(session: str) -> dict[str, Any]:
    STATE_DIR.mkdir(exist_ok=True)
    p = STATE_DIR / f"{session}.json"
    try:
        saved = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or corrupt state file: start from defaults
        saved = {}
    state = default_state()
    if isinstance(saved, dict):
        state.update(saved)
    return state


def save_state(session: str, state: dict[str, Any]) -> None:
    import session_store

    session_store.save(STATE_DIR / f"{session}.json", state)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def state_reset_active(state: dict[str, Any]) -> str | None:
    """Why the session's tracking state is untrusted (damaged or missed events), until the
    declared gate of every root in the marker's scope passed after that moment; None once
    cleared or if never reset. An empty scope (nothing gated to wait for) stays active
    until on_stop has reported it once. Never raises on malformed values."""
    reset = state.get("state_reset")
    if reset is None:
        return None
    if not isinstance(reset, dict):
        return "세션 추적 상태 표식 손상"
    since = _number(reset.get("at"))
    if since is None:
        return "세션 추적 상태 표식 손상(시각)"
    roots = reset.get("roots")
    evidence = state.get("evidence")
    # Compared case-insensitively on Windows: an unresolvable place is recorded normcased,
    # and once its CLAUDE.md is repaired its gate evidence is keyed by the policy root.
    by_root = {
        os.path.normcase(str(k)): v
        for k, v in (evidence if isinstance(evidence, dict) else {}).items()
    }

    def gate_passed_since(root: object) -> bool:
        ev = by_root.get(os.path.normcase(root)) if isinstance(root, str) else None
        if not isinstance(ev, dict):
            return False
        at = _number(ev.get("at"))
        return (
            ev.get("kind") == "gate"
            and ev.get("outcome") == "passed"
            and at is not None
            and at > since
        )

    if isinstance(roots, list) and roots and all(map(gate_passed_since, roots)):
        state.pop("state_reset", None)
        return None
    return str(reset.get("why") or "원인 미상")


def reset_scope(saved: dict[str, Any] | None, cwd: str) -> tuple[list[str], list[str]]:
    """(roots, problems): roots with a declared gate whose content a reset makes unverified
    (those the readable previous state knew - dirty, touched repositories - and the policy
    root of this event). A place whose policy cannot be read is itself a root (no evidence
    is ever recorded for it, so the marker stays until the user resolves it)."""
    from verification import find_policy

    roots: set[str] = set()
    problems: list[str] = []
    if saved:
        dirty = saved.get("dirty")
        for root, entry in (dirty if isinstance(dirty, dict) else {}).items():
            if root and isinstance(entry, dict) and entry.get("gate"):
                roots.add(str(root))
        tops = saved.get("touched_tops")
        places = [
            t for t in (tops if isinstance(tops, list) else []) if isinstance(t, str)
        ]
    else:
        places = []
    for place in [*places, cwd]:
        try:
            policy = find_policy(os.path.join(place, "_"))
        except (OSError, ValueError) as e:  # e.g. a CLAUDE.md that is not UTF-8
            roots.add(unresolved_root(place))
            problems.append(f"범위 확인 실패({type(e).__name__}): {place}")
            continue
        if policy["root"] and policy["gate"]:
            roots.add(policy["root"])
    return sorted(roots), problems


def unresolved_root(place: str) -> str:
    return os.path.normcase(os.path.abspath(place))


def mark_reset(
    state: dict[str, Any], saved: dict[str, Any] | None, why: list[str], cwd: str
) -> None:
    """Record (or extend) the reset marker. It is written before the scope is computed, with
    the event's place as an unresolvable root, so a failure while computing it still leaves
    a marker that blocks (fail closed) instead of none."""
    previous_roots: set[str] = set()
    previous_why = ""
    if state_reset_active(state) is not None:  # an earlier marker that is still open
        previous = state.get("state_reset") or {}
        previous_roots = {r for r in previous.get("roots") or [] if isinstance(r, str)}
        previous_why = str(previous.get("why") or "")

    def record(roots: set[str], reasons: list[str]) -> None:
        state["state_reset"] = {
            "at": time.time(),
            # newest first, so truncation drops the oldest reasons
            "why": "; ".join(w for w in [*reasons, previous_why] if w)[:300],
            "roots": sorted(roots | previous_roots),
        }

    record({unresolved_root(cwd)}, [*why, "범위 계산 전"])
    roots, problems = reset_scope(saved, cwd)
    record(set(roots), [*why, *problems])


def state_shape_problem(saved: dict[str, Any]) -> str | None:
    """The first key whose value has the wrong type for the code that reads it, or None."""
    for key, default in default_state().items():
        if key not in saved:
            continue
        value = saved[key]
        if default is None:
            ok = value is None or isinstance(value, dict)
        elif isinstance(default, int | float):
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        else:
            ok = isinstance(value, type(default))
        if not ok:
            return key
    for key in ("seen_events", "touched_tops", "background_tops"):
        value = saved.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return key
    reset = saved.get("state_reset")
    if reset is not None and (
        not isinstance(reset, dict)
        or _number(reset.get("at")) is None
        or not isinstance(reset.get("roots", []), list)
    ):
        return "state_reset"
    return None


# ---------------------------------------------------------------- helpers
def edit_pairs(tool: str, ti: dict[str, Any]) -> tuple[str, str]:
    """Return (old_text, new_text) for an edit tool call."""
    if tool == "Edit":
        return ti.get("old_string", ""), ti.get("new_string", "")
    if tool == "MultiEdit":
        edits = ti.get("edits", []) or []
        return "\n".join(e.get("old_string", "") for e in edits), "\n".join(
            e.get("new_string", "") for e in edits
        )
    if tool == "Write":
        old = ""
        try:
            old = Path(ti.get("file_path", "")).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            pass
        return old, ti.get("content", "")
    return "", ""


def count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE | re.MULTILINE))


def weakening_findings(path: str, old: str, new: str) -> list[str]:
    found: list[str] = []
    for label, pat in SUPPRESSIONS:
        delta = count(pat, new) - count(pat, old)
        if delta > 0:
            found.append(f"+{delta} `{label}` suppression")
    if TEST_PATH.search(path):
        for label, pat in TEST_SKIPS:
            delta = count(pat, new) - count(pat, old)
            if delta > 0:
                found.append(f"+{delta} {label}")
        removed = count(ASSERTIONS, old) - count(ASSERTIONS, new)
        if old and removed > 0:
            found.append(f"-{removed} assertion(s) removed from a test file")
    return found


def deleted_test_files(cmd: str) -> list[str]:
    if not DELETE_CMD.search(cmd):
        return []
    return [
        t
        for t in re.split(r"[\s'\"]+", cmd)
        if t and TEST_PATH.search(t) and "." in Path(t).name
    ]


def emit_pre(decision: str, reason: str) -> None:
    """Write the decision and flush it now: a piped stdout is block-buffered, and a flush
    failing at interpreter exit would end with status 120 (non-blocking). A refusal that
    cannot be delivered becomes exit 2 (blocking) with the reason on stderr."""
    try:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision,
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        sys.stdout.flush()
    except (OSError, ValueError) as e:
        if decision != "allow":
            # Raised, not exited: the locked run still saves its state (finally), and the
            # hook boundary then ends with exit 2 (fail_closed -> hard_block).
            raise OutputFailed(f"PreToolUse 판정 출력 실패: {e}") from e
        log.exception("discipline.pre_output_failed")
    sys.exit(0)


class OutputFailed(Exception):
    """A blocking decision could not be delivered on stdout."""


def emit_stop_block(reason: str) -> None:
    try:
        print(json.dumps({"decision": "block", "reason": reason}))
        sys.stdout.flush()  # now, not at interpreter exit (a failure there is status 120)
    except (OSError, ValueError) as e:
        raise OutputFailed(f"Stop 판정 출력 실패: {e}") from e


# ---------------------------------------------------------------- events
def shell_in_background(data: dict[str, Any]) -> bool:
    """The shell command was sent to the background (asked for, or reported by the tool)."""
    ti = data.get("tool_input", {}) or {}
    response = data.get("tool_response")
    return bool(ti.get("run_in_background")) or (
        isinstance(response, dict) and bool(response.get("backgroundTaskId"))
    )


MAX_ACTOR = 80
MAX_OWNERS = 8
_ACTOR_NOISE = re.compile(r"[^A-Za-z0-9_.-]")


def actor(data: dict[str, Any]) -> str:
    """Who this hook event belongs to: "main", or "<agent_type>:<agent_id>" inside a subagent.
    A label for reasons and audit lines only: no decision reads it, so a made-up value cannot
    release anything."""
    agent = data.get("agent_id")
    if agent is None or agent == "":
        return "main"
    if not isinstance(agent, str):
        return "agent:?"
    kind = data.get("agent_type")
    # Plain name characters only: the label is shown inside block reasons, where anything
    # else (spaces, prose, markup) could be read as part of the message.
    kind = _ACTOR_NOISE.sub("", kind) if isinstance(kind, str) else ""
    return f"{kind or 'agent'}:{_ACTOR_NOISE.sub('', agent) or '?'}"[:MAX_ACTOR]


def event_label(mode: str, data: dict[str, Any]) -> str:
    """The hook mode, with the subagent it ran in (for missed-event notes)."""
    who = actor(data)
    return mode if who == "main" else f"{mode}[{who}]"


def add_actors(seen: list[Any], names: object) -> None:
    """Move the labels among `names` to the end of `seen` (most recent last), keeping the
    latest MAX_OWNERS."""
    for who in names if isinstance(names, list | tuple) else ():
        if isinstance(who, str) and who:
            if who in seen:
                seen.remove(who)
            seen.append(who)
    del seen[:-MAX_OWNERS]


def note_owner(state: dict[str, Any], top: str, who: str) -> None:
    """Remember that `who` acted on the repository at `top` (edited it, ran a shell in it).
    A full table drops its oldest entries: the repository being acted on now is the one a
    reason is most likely to name."""
    owners = state.setdefault("owners", {})
    while top not in owners and len(owners) >= MAX_DIR_CACHE:
        owners.pop(next(iter(owners)))
    seen = owners.get(top)
    if not isinstance(seen, list):
        seen = owners[top] = []
    add_actors(seen, [who])


def owner_note(names: object, agents: object = None) -> str:
    """Reason suffix naming who left a record; empty unless a subagent is among them.
    A subagent whose end was reported (`agents`, see on_agent_stop) is marked as ended."""
    labels = [
        n for n in (names if isinstance(names, list) else []) if isinstance(n, str)
    ]
    if not any(n != "main" for n in labels):
        return ""
    known = agents if isinstance(agents, dict) else {}
    shown = [
        f"{n}(종료됨)"
        if isinstance(known.get(n), dict) and known[n].get("stopped")
        else n
        for n in labels
    ]
    return f" (이 기록을 남긴 주체: {', '.join(shown)})"


MAX_AGENTS = 50
AGENT_START_NOTE = (
    "[discipline] This subagent's tool calls are recorded in the main session's "
    "verification tracking. Create scratch repositories and temporary files under %TEMP% "
    "only. Do not delete or move a repository that existed before you started."
)


def agent_key(data: dict[str, Any]) -> str | None:
    """The actor label of a lifecycle event, or None when it names no usable agent. An id the
    label cannot carry exactly (characters dropped, or cut at the length limit) is not
    tracked: two such agents would share one label, and the end of one would show the other
    as ended."""
    agent = data.get("agent_id")
    if not isinstance(agent, str) or not agent or _ACTOR_NOISE.search(agent):
        return None
    who = actor(data)
    return who if len(who) < MAX_ACTOR else None


def on_agent_start(data: dict[str, Any], state: dict[str, Any]) -> None:
    """SubagentStart (spawned or resumed): remember it as running and tell it the tracking
    rules. Recorded for the reasons; the one thing a decision reads is what a review agent
    saw when it started (`review`, P1-02), and only the review gate reads that."""
    who = agent_key(data)
    if who is None:
        return
    agents = state.setdefault("agents", {})
    entry = agents.get(who)
    if not isinstance(entry, dict):
        entry = {"runs": 0}
    runs = entry.get("runs")
    agents.pop(who, None)
    while len(agents) >= MAX_AGENTS:
        ended = [
            k for k, v in agents.items() if isinstance(v, dict) and v.get("stopped")
        ]
        agents.pop(ended[0] if ended else next(iter(agents)))
    agents[who] = {
        "started": time.time(),
        "stopped": None,
        "runs": (runs if isinstance(runs, int) else 0) + 1,
    }
    if data.get("agent_type") in REVIEW_AGENTS:
        # P1-02: the review counts for this content only, and only once the agent ended
        try:
            agents[who]["review"] = review_snapshot(
                state, str(data.get("cwd") or os.getcwd())
            )
        except (OSError, ValueError):
            # no snapshot means "no review recorded": the safe side, and the agent starts
            log.exception("discipline.review_snapshot_failed")
            agents[who]["review"] = {}
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStart",
                    "additionalContext": AGENT_START_NOTE,
                }
            }
        )
    )


def on_agent_stop(data: dict[str, Any], state: dict[str, Any]) -> None:
    """SubagentStop: note the end of a subagent that was seen starting. Never prints: an
    output here could keep the subagent running. Agents never seen starting (internal ones)
    are ignored."""
    who = agent_key(data)
    entry = state.get("agents", {}).get(who) if who else None
    if isinstance(entry, dict):
        entry["stopped"] = time.time()


def note_shell_touch(
    data: dict[str, Any], state: dict[str, Any], *, after: bool = False
) -> None:
    """Record which repositories a shell command may change. Called for its PreToolUse and
    again (`after`) for its PostToolUse(Failure), so a command whose PreToolUse was never
    recorded is not taken as having touched nothing. Repeating it changes nothing."""
    ti = data.get("tool_input", {}) or {}
    cwd = str(data.get("cwd") or os.getcwd())
    command = str(ti.get("command", ""))
    shell_tops = state.setdefault("turn_shell_tops", [])
    touched = state.setdefault("touched_tops", [])
    # A repository that comes back here is compared with its original baseline (taken before
    # the command), so it counts as known too.
    known = set(state.get("repos", {})) | set(state.get("repos_removed", {}))
    for d in shell_targets(command, data.get("tool_name") == "PowerShell", cwd):
        top = track_repo(state, d)
        if top and top not in shell_tops:
            shell_tops.append(top)  # Bash edits there are visible only via content maps
        if top and top not in touched:
            touched.append(top)  # may change it before the next Stop
        if top:
            note_owner(state, top, actor(data))  # only where the command ran
        if after and top and top not in known and top in state.get("repos", {}):
            # First seen only now, so its baseline already holds whatever the command wrote:
            # late, never "unchanged".
            state["repos"][top]["late"] = True
    # A shell can write any path (absolute paths, git -C, scripts), so every tracked repository
    # may change — never a "normal deletion" later. Conservative until P0-07 can tell which
    # paths a command writes.
    for top in state.get("repos", {}):
        if top not in touched:
            touched.append(top)
    if shell_in_background(data):
        # It may keep writing after this turn's Stop, and no hook says when it ends: these
        # repositories are never "untouched" again in this session.
        late_writers = state.setdefault("background_tops", [])
        for top in touched:
            if top not in late_writers:
                late_writers.append(top)


def shell_write_denial(
    data: dict[str, Any], state: dict[str, Any], command: str, cwd: str
) -> str | None:
    """P0-07: judge the files a shell command visibly writes like an Edit (best effort, see
    shellwrites.py for what is not seen). Warn mode only records the targets enforce would
    refuse; enforce returns the refusal. A failure here refuses under enforce and changes
    nothing under warn."""
    tool = data.get("tool_name", "")
    try:
        from shellwrites import write_targets

        paths, unresolved = write_targets(
            command, "powershell" if tool == "PowerShell" else "bash", cwd
        )
        paths = [f for f in paths if not ignored_path(state, f, cwd)]
        if not paths and not unresolved:
            return None
        if AUTHZ_MODE == "enforce" and v2_active():
            for f in paths:  # per target: the plan file itself has its own rule
                refused = v2_write_check(state, cwd, f)
                if refused:
                    return refused
        approval = active_approval(state, data.get("prompt_id"))
        base = approval.get("base") if approval else None
        outside = [
            f
            for f in paths
            # an existing folder stands for the files in it (`rm -r src`)
            if not in_approved_scope(
                approval,
                os.path.join(f, "_") if not is_unc(f) and os.path.isdir(f) else f,
            )
        ]
        reason = "out_of_scope" if approval else "no_approval"
        if unresolved:  # a write this check could not judge: counted in both modes
            observe_authz("unresolved", cwd, tool, base)
        if AUTHZ_MODE != "enforce":
            for f in outside:
                observe_authz(reason, f, tool, base)
            return None
        if not outside:
            return None
        why = (
            f"승인된 계획(task: {approval.get('task_id')})의 변경 범위 밖"
            if approval
            else "승인된 계획 없음"
        )
        shown = ", ".join(Path(f).name for f in outside[:5])
        return REASON_AUTHZ.format(why=f"{why}, 셸 명령의 쓰기 대상: {shown}")
    except (
        Exception
    ) as e:  # hook boundary: enforce refuses, warn keeps today's behaviour
        log.exception("discipline.shell_write_check_failed")
        if AUTHZ_MODE == "enforce":
            return REASON_AUTHZ.format(
                why=f"셸 쓰기 대상 판정 중 오류 {type(e).__name__}"
            )
        return None


def on_pre(data: dict[str, Any], state: dict[str, Any]) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    cwd = str(data.get("cwd") or os.getcwd())

    # D-5: snapshot any repository this tool is about to touch, before it runs.
    if tool in ("Bash", "PowerShell"):
        command = str(ti.get("command", ""))
        note_shell_touch(data, state)
        # A check is evidence only for the content it ran on: remember the content before it
        # runs, so `gate && sed ...` (content changed during the command) is not bound to the
        # post-edit content.
        tops = check_repos(state, command, tool == "PowerShell", cwd)
        if tops is not None and data.get("tool_use_id"):
            pre = state.setdefault("pre_check", {})
            pre[str(data["tool_use_id"])] = repo_snapshots(state, tops)
            for stale in list(pre)[:-MAX_PRE_CHECKS]:
                pre.pop(stale, None)
    elif edit_target(ti) and not ignored_path(state, edit_target(ti), cwd):
        top = track_repo(state, absolute(edit_target(ti), cwd))
        if top and top not in state.setdefault("touched_tops", []):
            state["touched_tops"].append(top)  # may change it before the next Stop
        if top:
            note_owner(state, top, actor(data))

    if tool in ("Bash", "PowerShell"):
        refusal = shell_write_denial(data, state, str(ti.get("command", "")), cwd)
        if refusal:
            emit_pre("deny", refusal)
        tests = deleted_test_files(ti.get("command", ""))
        if tests:
            emit_pre(
                "ask",
                f"[discipline] 테스트 파일 삭제 감지: {', '.join(tests)}. 의도한 삭제인지 확인해 주세요.",
            )
        return

    path = edit_target(ti)
    if not path or ignored_path(state, path, cwd):
        return
    path = absolute(path, cwd)
    # A reset marker does not deny writes: edits after it are tracked by the new state, and
    # Stop stays blocked until the gates in its scope pass, so a red gate remains fixable.

    # 0) v2: the approval (and the store itself) is verified now, before this write.
    if v2_active():
        try:
            denial = v2_write_check(state, cwd, path)
        except Exception as e:  # hook boundary: refuse instead of crashing (fail-open)
            log.exception("approvals.v2_hook_failure")
            denial = v2_failure(e)
        if denial:
            emit_pre("deny", denial)
        checked = state.get("approval")
        state["pre_valid"] = (
            {"id": data.get("tool_use_id"), "approval": checked.get("approval_id")}
            if isinstance(checked, dict)
            and checked.get("approval_id")
            and data.get("tool_use_id")
            and not _passed_unbound
            else None
        )

    # 1) Large change without an approved plan covering it.
    approval = active_approval(state, data.get("prompt_id"))
    counted = [f for f in state["turn_files"] if not in_approved_scope(approval, f)]
    verdict = authz_verdict(
        in_approved_scope(approval, path),
        path in state["turn_files"],
        len(counted),
        MAX_FILES_WITHOUT_PLAN,
        AUTHZ_MODE,
    )
    why = (
        f"승인된 계획(task: {approval.get('task_id')})의 변경 범위 밖"
        if approval
        else "승인된 계획 없음"
    )
    if verdict == "would_deny":
        observe_authz(
            "out_of_scope" if approval else "no_approval",
            path,
            tool,
            approval.get("base") if approval else None,
        )
    if verdict == "deny" and AUTHZ_MODE == "enforce":
        emit_pre("deny", REASON_AUTHZ.format(why=why))
    if verdict == "deny":
        scope_note = (
            f" 승인된 계획(task: {approval.get('task_id')})의 변경 범위 밖 파일입니다."
            if approval
            else ""
        )
        emit_pre(
            "deny",
            f"[discipline] 이번 턴에 승인 범위 밖 파일을 이미 {len(counted)}개 수정했습니다(한도 {MAX_FILES_WITHOUT_PLAN}).{scope_note} "
            "더 진행하기 전에 /plan 으로 계획을 세우거나 갱신하고 ExitPlanMode 로 사용자 승인을 받으세요. "
            "이미 승인된 진행 중 계획을 이어가는 것이라면, 사용자가 프롬프트 첫 줄에 task:<id>를 적어야 재무장됩니다.",
        )

    # 2) Test weakening / suppressions.
    old, new = edit_pairs(tool, ti)
    findings = weakening_findings(path, old, new)
    if findings:
        emit_pre(
            "ask",
            f"[discipline] 안전장치 약화 감지 ({Path(path).name}): "
            + "; ".join(findings)
            + ". 사유가 정당한지 확인 후 승인해 주세요 (Claude는 사유를 코드 주석에 남겨야 합니다).",
        )


OUTPUT_OBSERVE_MIN = 2000  # smaller outputs are not worth a row
OUTPUT_ADVICE_DEFAULT = 8000  # initial prior; re-tune from output-size-observe.jsonl
OUTPUT_ADVICE_NOTE = (
    "[discipline] 방금 테스트·검사 명령의 출력이 {n}자였습니다(기준 {limit}자). 긴 출력은 "
    "`test-runner` subagent에 맡기면 요약만 돌아옵니다. 직접 실행할 때는 조용한 옵션(-q 등)을 "
    "쓰세요. (알림이며 차단이 아닙니다.)"
)


def response_chars(response: object) -> int:
    """How much text a shell tool result carries."""
    if isinstance(response, str):
        return len(response)
    if isinstance(response, dict):
        return sum(len(v) for v in response.values() if isinstance(v, str))
    return 0


def advice_threshold(raw: object) -> int:
    """CLAUDE_OUTPUT_ADVICE_CHARS: a whole number from 1000 to 1000000, else the default."""
    if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
        value = int(raw.strip())
        if 1000 <= value <= 1_000_000:
            return value
    return OUTPUT_ADVICE_DEFAULT


def is_test_run(command: object, dialect: str, gate: object) -> bool:
    """Whether a shell command runs tests or checks: it contains the project's declared
    gate, or one of its commands is a real runner. A command that cannot be parsed is not."""
    if not isinstance(command, str):
        return False
    if isinstance(gate, str) and gate.strip() and gate.strip() in command:
        return True
    from shellparse import ParseError, parse
    from verification import is_runner

    try:
        return any(
            is_runner(cmd)
            for cmd in parse(
                command, "powershell" if dialect == "powershell" else "bash"
            )
        )
    except ParseError:
        return False


def note_output_size(
    data: dict[str, Any], state: dict[str, Any], *, failed: bool
) -> None:
    """P1-04: count large shell outputs (no command, no content, no path) and, for a large
    test run made by the main conversation, say once per turn that it can be delegated.
    A note only; a failure here changes nothing else the hook does."""
    try:
        # a failure event may carry its text as `error` instead of a tool_response
        size = response_chars(data.get("tool_response")) or response_chars(
            data.get("error") if failed else None
        )
        if size < OUTPUT_OBSERVE_MIN:
            return
        from verification import find_policy

        cwd = str(data.get("cwd") or os.getcwd())
        dialect = "powershell" if data.get("tool_name") == "PowerShell" else "bash"
        test = is_test_run(
            (data.get("tool_input") or {}).get("command"),
            dialect,
            find_policy(os.path.join(cwd, "_")).get("gate"),
        )
        main = actor(data) == "main"
        limit = advice_threshold(os.environ.get("CLAUDE_OUTPUT_ADVICE_CHARS"))
        if (
            test
            and main
            and not failed
            and size >= limit
            and state.get("output_advice_turn") != state.get("turn")
        ):
            state["output_advice_turn"] = state.get("turn") or 0
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": OUTPUT_ADVICE_NOTE.format(
                                n=size, limit=limit
                            ),
                        }
                    }
                )
            )
        row = {
            "at": time.time(),
            "chars": size,
            "test": test,
            "failed": failed,
            "who": "main" if main else "subagent",
        }
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        with open(
            APPROVALS_HOME / "output-size-observe.jsonl", "a", encoding="utf-8"
        ) as f:
            f.write(json.dumps(row) + "\n")
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        # RecursionError: the parser on an absurdly nested command
        log.exception("discipline.output_size_failed")


def turn_changed_now(state: dict[str, Any]) -> dict[str, str] | None:
    """normcased path -> content hash now ("" when it is no regular file now) of everything
    that differs from the turn baselines; None when a repository cannot be read."""
    import worktree

    out: dict[str, str] = {}
    for top, base in state.get("repos", {}).items():
        turn_base = base.get("turn") or base.get("session")
        now = worktree.content_map(top, _watch_globs(top))
        if turn_base is None or now is None:
            return None
        for rel in worktree.changed(turn_base, now):
            out[os.path.normcase(os.path.join(top, rel))] = now.get(rel) or ""
    return out


def cover_changes(
    state: dict[str, Any],
    approval: dict[str, Any],
    changed: dict[str, str] | None = None,
    into: str = "turn_covered",
) -> None:
    """Remember which of this turn's changes `approval` covers, with their content as it is
    now: the Stop of this turn does not hold them against the scope of a later approval
    (P1-01b). Covered is only what is inside the approval's scope and changed after the
    approval began (not what was already like this when it was recorded); a file that
    changes again later no longer matches. Nothing is covered when git cannot answer or
    when it is not known what the turn looked like when the approval began."""
    if state.get("turn_cover_off"):
        return
    changed = turn_changed_now(state) if changed is None else changed
    if changed is None:
        return
    began = state.get("turn_approval_from") or {}
    covered = state.setdefault(into, {})
    for full, digest in changed.items():
        if began.get(full) == digest:
            continue  # as it was when this approval began: not made under it
        if not digest and os.path.lexists(full):
            continue  # there, but no regular file: a later change could not be told apart
        if in_approved_scope(approval, full):
            covered[full] = digest


READ_ONLY_PROGRAMS = {
    "cd",
    "pwd",
    "ls",
    "dir",
    "cat",
    "type",
    "head",
    "tail",
    "wc",
    "grep",
    "egrep",
    "fgrep",
    "findstr",
    "which",
    "where",
    "echo",
    "printf",
    "true",
    "test",
    "sleep",
    "ping",
    "date",
    "cut",
    "get-content",
    "gc",
    "get-childitem",
    "gci",
    "get-item",
    "test-path",
    "select-string",
    "sls",
    "get-command",
    "get-date",
    "measure-object",
    "select-object",
    "sort-object",
    "format-table",
    "out-string",
    "write-output",
}
READ_ONLY_GIT = {
    "status",
    "log",
    "diff",
    "show",
    "rev-parse",
    "ls-files",
    "describe",
    "blame",
    "check-ignore",
    "cat-file",
    "shortlog",
    "rev-list",
}
READ_ONLY_GH = {
    ("run", "view"),
    ("run", "list"),
    ("run", "watch"),
    ("pr", "view"),
    ("pr", "list"),
    ("issue", "view"),
    ("issue", "list"),
    ("repo", "view"),
}


def shell_reads_only(command: str, dialect: str, cwd: str) -> bool:
    """True only when every part of a shell command is on a short list of programs that
    change no file, nothing is redirected and nothing is fed to an interpreter. Anything
    that does not parse, is not on the list or looks unusual is False: the caller then
    looks at the repository (fail-closed)."""
    from shellparse import ParseError, parse, program_and_args
    from shellwrites import write_targets

    try:
        cmds = parse(command, dialect)
        if not cmds or write_targets(command, dialect, cwd) != ([], 0):
            return False
        for cmd in cmds:
            program, args = program_and_args(cmd)
            if cmd.heredoc is not None or any(
                re.search(r"[<>`]|\$\(", a) for a in args
            ):
                return False
            subs = [a.lower() for a in args if not a.startswith("-")]
            if program == "git":
                if any(a in ("-c", "-C") or a.startswith("--") for a in args[:1]):
                    return False
                if any(a == "-o" or a.startswith("--output") for a in args):
                    return False  # `git diff --output=file` writes
                if subs[:1] and subs[0] in READ_ONLY_GIT:
                    continue
                return False
            if program == "gh":
                if tuple(subs[:2]) in READ_ONLY_GH:
                    continue
                return False
            if program not in READ_ONLY_PROGRAMS:
                return False
        return True
    except (
        ParseError,
        ValueError,
        TypeError,
        AttributeError,
        IndexError,
        RecursionError,
    ):
        return False


def _one_change(state: dict[str, Any], path: str) -> dict[str, str] | None:
    """{path: content hash now} for one file, spelled and hashed as turn_changed_now does it
    ("" when it is no regular file now); None when it lies in no tracked repository or git
    does not answer (the caller then looks at the whole turn)."""
    import subprocess

    tops = [top for top in (state.get("repos") or {}) if _inside(path, top)]
    if not tops:
        return None
    top = max(tops, key=len)
    rel = os.path.relpath(path, top).replace(os.sep, "/")
    full = os.path.normcase(os.path.join(top, rel))
    if not os.path.isfile(path):
        return {full: ""}
    try:
        done = subprocess.run(
            ["git", "hash-object", "--", rel],
            cwd=top,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    digest = done.stdout.strip() if done.returncode == 0 else ""
    return {full: digest} if digest else None


def cover_valid(
    data: dict[str, Any],
    state: dict[str, Any],
    held: object = None,
    only: str | None = None,
) -> None:
    """Record, now, which of this turn's changes the approval in force covers (cover_changes):
    called after every write, before a Stop re-checks the approval and before a relayed
    prompt decides whether it survives. What was done inside the scope while the approval
    was valid then stays covered whatever happens to the approval later; a file changed
    again afterwards no longer matches its recorded content. Nothing is covered unless the
    approval is valid at this very moment (the same check every write goes through, run on
    a copy of the state so that it decides nothing here). `held` names the approval when the
    prompt binding cannot (a relayed prompt has a new prompt id). `only` is the one file an
    Edit/Write just wrote: only that file is hashed. The approval is not checked again when
    it was already checked for this very tool call (same tool_use_id, same approval: by
    PreToolUse for an Edit/Write, by this PostToolUse for a shell command); any other call
    is checked now. `cover_last` keeps the outcome for diagnosis only."""
    n, why = 0, "ok"
    try:
        ptr = (
            held
            if isinstance(held, dict)
            else active_approval(state, data.get("prompt_id") or state.get("prompt_id"))
        )
        if not ptr:
            why = "no-approval"
        elif not v2_active():
            why = "v1"
        elif state.get("turn_cover_off"):
            why = "cover-off"
        else:
            # its own record: `turn_covered` takes a file out of the turn's change list
            # (the work of an approval that was replaced); this one only answers the Stop's
            # "was this approved" for a change that stays listed
            covered = state.setdefault("turn_done_under", {})  # shared with the copy
            probe = dict(state)
            pre = state.get("pre_valid")
            checked_before = (
                isinstance(pre, dict)
                and bool(pre.get("id"))
                and pre.get("id") == data.get("tool_use_id")
                and pre.get("approval") == ptr.get("approval_id")
            )
            if not checked_before and v2_write_check(
                probe, str(data.get("cwd") or os.getcwd())
            ):
                why = "not-valid"
            else:
                changed = _one_change(state, only) if only else None
                if changed is None:
                    # the whole turn is looked at: the check made for one tool call does
                    # not speak for everything else that changed, so check now
                    if checked_before and v2_write_check(
                        probe, str(data.get("cwd") or os.getcwd())
                    ):
                        changed, why = {}, "not-valid"
                    else:
                        changed = turn_changed_now(state)
                if why == "not-valid":
                    pass
                elif changed is None:
                    why = "unknown"
                else:
                    cover_changes(state, ptr, changed, into="turn_done_under")
                    n = len(covered)
    except Exception:  # hook boundary: nothing covered rather than a crash
        log.exception("discipline.cover_failed")
        why = "error"
    state["cover_last"] = {"at": time.time(), "n": n, "why": why}


def keep_covered_changes(
    data: dict[str, Any], state: dict[str, Any]
) -> dict[str, str] | None:
    """Called when a new approval is about to replace the current one in the middle of a
    turn. Returns what the turn has changed so far (None if unknown), which is also what
    the new approval starts from. The validity check runs on a copy of the state's top
    level: what it decides about the outgoing approval must not stick to the new one."""
    changed = turn_changed_now(state)
    prompt_id = data.get("prompt_id")
    if changed is None or not active_approval(state, prompt_id):
        return changed
    state.setdefault("turn_covered", {})  # shared with the copy below
    probe = dict(state)
    if v2_active():
        try:
            # a plan closed as done covers its changes inside this call (see there)
            refused = v2_write_check(probe, str(data.get("cwd") or os.getcwd()))
        except Exception:  # hook boundary: an unreadable store covers nothing
            log.exception("approvals.v2_hook_failure")
            return changed
        if refused:
            return changed
    previous = active_approval(probe, prompt_id)
    if previous:
        cover_changes(state, previous, changed)
    return changed


def on_post(data: dict[str, Any], state: dict[str, Any]) -> None:
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    now = time.time()
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = edit_target(ti)
        cwd = str(data.get("cwd") or os.getcwd())
        if path and not ignored_path(state, path, cwd):
            path = absolute(path, cwd)
            if path not in state["turn_files"]:
                state["turn_files"].append(path)
            top = track_repo(state, path)
            if top and top not in state.setdefault("touched_tops", []):
                state["touched_tops"].append(top)  # also when no PreToolUse was seen
            if top:
                note_owner(state, top, actor(data))
            if state.get("test_gap_note") and is_test_file(
                path, list(state.get("repos") or {})
            ):
                # a test written after the Stop that prepared the note: withdrawn
                state["test_gap_note"] = ""
            if Path(path).suffix.lower() in CODE_EXT:
                state["last_code_edit"] = now
                mark_dirty(state, path, now, changed_lines(tool, ti), by=[actor(data)])
    elif tool in ("Bash", "PowerShell"):
        note_shell_touch(data, state, after=True)  # also when no PreToolUse was seen
        record_evidence(data, state, failed=False)
        note_background_task(data, state)
        if v2_active():  # a shell may have changed a plan: record what is seen now
            state["pre_valid"] = None
            try:
                refused_now = v2_write_check(state, str(data.get("cwd") or os.getcwd()))
                checked = state.get("approval")
                if (
                    refused_now is None
                    and isinstance(checked, dict)
                    and checked.get("approval_id")
                    and data.get("tool_use_id")
                ):
                    # this very tool call was just checked: cover_valid below need not
                    state["pre_valid"] = {
                        "id": data.get("tool_use_id"),
                        "approval": checked.get("approval_id"),
                    }
            except Exception as e:  # hook boundary: the approval is lost, not ignored
                log.exception("approvals.v2_hook_failure")
                state["approval"] = None
                state["approval_warning"] = v2_failure(e)
        note_output_size(data, state, failed=False)
    elif tool in ("Agent", "Task"):
        if ti.get("subagent_type") in REVIEW_AGENTS:
            state["last_review"] = now  # a review, not verification
    elif tool == "ExitPlanMode":
        # PostToolUse only fires once the user approved the plan.
        changed = keep_covered_changes(data, state)
        record_approval(data, state)
        state["turn_approval_from"] = changed or {}
        if changed is None:
            state["turn_cover_off"] = 1
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        target = edit_target(ti)
        here = str(data.get("cwd") or os.getcwd())
        cover_valid(data, state, only=absolute(target, here) if target else None)
    elif tool in ("Bash", "PowerShell") and not shell_reads_only(
        str(ti.get("command") or ""),
        "powershell" if tool == "PowerShell" else "bash",
        str(data.get("cwd") or os.getcwd()),
    ):
        # a command that may have written: look at the whole turn (a read-only one cannot
        # have changed what the last look recorded)
        cover_valid(data, state)
    closing = state.get("ledger_closed")
    if isinstance(closing, dict) and closing.get("pin"):
        # This tool call closed the plan as done: write the task's state to the ledger now,
        # on the content of this moment, before the Stop takes the approval away.
        try:
            state["stop_prompt_id"] = data.get("prompt_id") or state.get("prompt_id")
            track_repo(state, str(data.get("cwd") or os.getcwd()))
            refresh_content(state)
            record_ledger(data, state)
        except (
            OSError,
            ValueError,
            TypeError,
            AttributeError,
            ImportError,
            RecursionError,
            KeyError,
        ):
            log.exception("discipline.ledger_failed")
        finally:
            # one try: a close whose record failed is not retried on every later tool call
            # (without a pinned content the Stop forgets it, see record_ledger)
            left = state.get("ledger_closed")
            if isinstance(left, dict):
                left.pop("pin", None)


# ---------------------------------------------------------------- plan approval scope
# Approvals outlive %TEMP% cleanup (Storage Sense) and reboots; records are HMAC-signed with a
# per-machine key so an edited or injected record never re-arms anything. Tools are kept away
# from this folder by guard.py and the settings deny rules.
APPROVALS_HOME = Path(
    os.environ.get("CLAUDE_DISCIPLINE_HOME") or (Path.home() / ".claude" / "discipline")
)
APPROVALS_FILE = APPROVALS_HOME / "approvals.json"
KEY_FILE = APPROVALS_HOME / "key"
RECOVERY_AUDIT_FILE = APPROVALS_HOME / "state-recovery.jsonl"
AUTHZ_OBSERVE_FILE = APPROVALS_HOME / "authz-observe.jsonl"
REASON_AUTHZ = (
    "[discipline] 승인된 계획의 범위 안에 있지 않은 파일은 수정할 수 없습니다({why}). "
    "/plan 으로 계획을 세우거나 갱신해 ExitPlanMode 로 사용자 승인을 받으세요. "
    "이미 승인된 진행 중 계획을 이어가는 것이라면, 사용자가 프롬프트 첫 줄에 task:<id>를 적어야 재개됩니다."
)


def observe_authz(reason: str, path: str, tool: object, base: object = None) -> None:
    """One line for an edit that enforce mode would have denied (hooks/authz_report.py
    counts them). No path and no content: only what kind of file it was. Observation must
    never stop the edit, so a failure is logged and nothing else happens."""
    try:
        if is_unc(path):  # never resolved: a stalled share must not time the hook out
            kind = "data"
        else:
            real = os.path.realpath(path)
            in_repo = may_be_repository(real)
            if isinstance(base, str) and base:
                kind = path_class(real, os.path.realpath(base), in_repo)
            else:  # no approval: there is no approving repository to compare with
                kind = "code" if in_repo else "data"
        row = {
            "at": time.time(),
            "reason": reason,
            "class": kind,
            "ext": Path(path).suffix.lower()[:16],
            "tool": tool if isinstance(tool, str) else "",
        }
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        with open(AUTHZ_OBSERVE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except (OSError, ValueError, TypeError):
        log.exception("discipline.authz_observe_failed")


SIGNED_FIELDS = (
    "task_id",
    "plan_hash",
    "scope",
    "prompt_id",
    "session_id",
    "base",
    "at",
)
APPROVAL_MAX_AGE_S = 30 * 86400
MAX_APPROVALS = 50
SCOPE_HEADINGS = r"(변경 범위|Scope|Change scope)"
# P0-05: paths outside every git repository (raw data, backups) are covered only by their
# own block; its patterns are stored in the same scope list with this prefix.
DATA_SCOPE_HEADINGS = r"(데이터 변경 범위|Data [Ss]cope|Data [Cc]hange [Ss]cope)"
DATA_SCOPE = "data:"
# A list item that is a label, not a path ("- 범위 밖: ..."): a first word that is not
# plain ASCII and has no path character, further words, then a colon before any
# parenthesis. `- Makefile (note: x)` and a backticked first word stay paths.
SCOPE_LABEL = re.compile(r"^\s*[-*]\s+(?=\S*[^\x00-\x7f])[^`\s:/\\*.]+\s+[^:(\n]*:")


def normalize_plan(text: str) -> str:
    """Plan body without frontmatter, checkbox state or progress log: what the user approved."""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    text = re.sub(r"^(\s*[-*]\s)\[[xX]\]", r"\1[ ]", text, flags=re.MULTILINE)
    text = re.sub(
        r"^##\s*(진행 기록|Progress)\b.*?(?=^##\s|\Z)",
        "",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def plan_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(normalize_plan(text).encode("utf-8")).hexdigest()


def plan_task(text: str) -> str | None:
    m = re.search(r"^task:\s*(\S+)\s*$", text, re.MULTILINE)
    return m.group(1) if m else None


def _scope_items(text: str, headings: str, base: str) -> list[str]:
    """Glob patterns listed under the `## <heading>` block, absolute against `base`."""
    m = re.search(
        rf"^##\s*{headings}\b[^\n]*\n(.*?)(?=^##\s|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    globs: list[str] = []
    for line in m.group(2).splitlines():
        if SCOPE_LABEL.match(line):
            continue  # "- 범위 밖: ..." explains the list; it names no path
        item = re.match(r"^\s*[-*]\s+`?([^`\s(]+)`?", line)
        if item:
            g = item.group(1)
            absolute = re.match(r"^[A-Za-z]:[\\/]", g) or g.startswith(("/", "~"))
            g = os.path.expanduser(g) if g.startswith("~") else g
            globs.append(_norm(g if absolute else os.path.join(base, g)))
    return globs


def plan_scope(text: str, base: str) -> list[str]:
    """What the plan may change, as absolute glob patterns (against `base`, the approving
    cwd): the `## 변경 범위` items, then the `## 데이터 변경 범위` items marked `data:`. One
    list, so the signed record keeps its shape (see in_approved_scope for how they differ)."""
    return _scope_items(text, SCOPE_HEADINGS, base) + [
        DATA_SCOPE + g for g in _scope_items(text, DATA_SCOPE_HEADINGS, base)
    ]


def _norm(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/").lower()


def load_key(*, create: bool) -> bytes | None:
    """HMAC key for approval records; created on first approval, never shown to tools (guard/deny)."""
    try:
        return bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())
    except (
        OSError,
        ValueError,
    ):  # missing or unreadable key: records cannot be verified
        if not create:
            return None
    APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    KEY_FILE.write_text(key.hex(), encoding="utf-8")
    return key


def sign_record(record: dict[str, Any], key: bytes | None) -> str:
    import hashlib
    import hmac

    if key is None:
        return ""
    body = {k: record.get(k) for k in SIGNED_FIELDS}
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def record_is_valid(record: dict[str, Any], key: bytes | None) -> str | None:
    """None if the record is authentic and fresh, else why it must not re-arm anything."""
    import hmac

    if key is None:
        return "서명 키 없음"
    if "base" not in record:
        return "이전 형식의 승인 기록(프로젝트 경로 없음) — 다시 승인 필요"
    mac = str(record.get("mac") or "")
    if not mac or not hmac.compare_digest(mac, sign_record(record, key)):
        return "서명 불일치(기록이 위조되거나 수정됨)"
    if time.time() - float(record.get("at") or 0) > APPROVAL_MAX_AGE_S:
        return "승인 기록 만료(30일 초과)"
    return None


def _load_approvals() -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError):  # corrupt or unreadable: nothing is trusted
        return {}, "승인 기록 파일 손상"
    return (data, None) if isinstance(data, dict) else ({}, "승인 기록 파일 손상")


def record_approval(data: dict[str, Any], state: dict[str, Any]) -> None:
    import worktree

    response = data.get("tool_response")
    response = response if isinstance(response, dict) else {}
    text = response.get("plan") if isinstance(response.get("plan"), str) else None
    if text is None and isinstance(response.get("filePath"), str):
        try:
            text = Path(response["filePath"]).read_text(encoding="utf-8")
        except (OSError, ValueError):  # gone or not UTF-8: approval stays prompt-only
            text = None
    cwd = str(data.get("cwd") or os.getcwd())
    if v2_active() and text and plan_task(text):
        try:
            v2_record_approval(data, state, text, response, cwd)
        except Exception as e:  # hook boundary: no approval rather than a crash
            log.exception("approvals.v2_hook_failure")
            state["approval"] = None
            state["approval_warning"] = v2_failure(e)
        return
    record = {
        "task_id": plan_task(text) if text else None,
        "plan_hash": plan_hash(text) if text else None,
        "scope": plan_scope(text, cwd) if text else [],
        "prompt_id": data.get("prompt_id"),
        "session_id": data.get("session_id"),
        # The project the approval was given in: its git toplevel (so approving from a
        # subdirectory still covers the project's docs/plans), else the working directory.
        "base": os.path.normcase(worktree.toplevel(cwd) or os.path.abspath(cwd)),
        "at": time.time(),
    }
    state["approval"] = record
    if record["task_id"] and record["plan_hash"]:
        key = load_key(create=True)
        approvals, _problem = (
            _load_approvals()
        )  # a corrupt file is replaced by valid records
        approvals[record["plan_hash"]] = {**record, "mac": sign_record(record, key)}
        newest = sorted(approvals.items(), key=lambda kv: float(kv[1].get("at", 0)))[
            -MAX_APPROVALS:
        ]
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        APPROVALS_FILE.write_text(json.dumps(dict(newest)), encoding="utf-8")


# `task:<id>`, `task_id: <id>`, `task=<id>` (optionally in backticks). ASCII ids only, so a
# Korean particle right after the id (`task:widget을`) is not part of it.
TASK_REF = re.compile(
    r"\btask(?:_id)?\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9_.-]*)", re.IGNORECASE | re.ASCII
)
# ASCII path characters only: `@docs/...` (file mention) and `계획docs/...` still yield the path,
# and a sentence period after `.md` is not part of it. Paths with spaces are not recognised.
PLAN_REF = re.compile(
    r"[A-Za-z0-9_.:/\\~-]+\.md(?![A-Za-z0-9_-])", re.IGNORECASE | re.ASCII
)
COMMAND_HEADER = re.compile(r"\A\s*<command-(?:message|name)>")
# The args block counts only directly after the header tags, never later in an expanded body.
COMMAND_ARGS = re.compile(
    r"\A\s*(?:<command-(message|name)>.*?</command-\1>\s*)+<command-args>(.*?)</command-args>",
    re.DOTALL,
)
RELAY_HEADERS = ("another claude session", "[system notification")
AMBIGUOUS = "재무장 후보가 여러 개"
NOTE = "참고:"  # a warning that informs (another named plan was refused) without demanding anything


def naming_text(prompt: str) -> str:
    """The part of a prompt allowed to name a task. A slash command (the prompt starts with the
    harness's <command-...> header) names it in the <command-args> block right after the header
    only; any other prompt in its first non-empty line only, and not at all when that line is
    markup, a harness note in brackets (`[Image: ...]`, `[Usage limit ...]`) or a relay header.
    Pasted documents and relayed text below the first line never re-arm an approval."""
    if COMMAND_HEADER.match(prompt):
        args = COMMAND_ARGS.match(prompt)
        return args.group(2) if args else ""
    first = next((line for line in prompt.splitlines() if line.strip()), "").strip()
    if first.startswith(("<", "[")) or first.lower().startswith(RELAY_HEADERS):
        return ""
    return first


def prompt_tasks(text: str) -> set[str]:
    """Task ids named explicitly; only these may re-arm an earlier approval."""
    return {m.group(1).rstrip(".").lower() for m in TASK_REF.finditer(text)}


def names_plan(text: str, plan: Path, root: str, cwd: str) -> bool:
    """True when `text` names this plan file: by its exact file name, or by a path that
    resolves (relative to `cwd` or the git `root`) to it. A same-named file in another project
    does not count."""
    want = os.path.normcase(str(plan))
    for ref in PLAN_REF.findall(text):
        if "/" in ref or "\\" in ref:
            bases = [""] if os.path.isabs(ref) else [cwd, root]
            if any(
                os.path.normcase(os.path.abspath(os.path.join(b, ref))) == want
                for b in bases
            ):
                return True
        elif ref.lower() == plan.name.lower():
            return True
    return False


def rearm_approval(
    cwd: str, prompt: str = ""
) -> tuple[dict[str, Any] | None, str | None]:
    """(approval, warning): an approval survives into a new prompt or session only when the
    prompt's first line (or slash-command arguments) names its task (`task:<id>`) or its plan
    file, the plan is still in progress in the git root the prompt runs in (never a parent or
    another project), and the record is authentic (HMAC) and fresh. Two named plans that could
    both re-arm -> none (ambiguous)."""
    root, named = named_plans(cwd, prompt)
    if not named:
        return None, None  # nothing named, or the named work has no plan here
    approvals, problem = _load_approvals()
    if not approvals:
        return None, problem
    key = load_key(create=False)
    found: list[dict[str, Any]] = []
    for plan, text, task in named:
        fm = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
        in_progress = bool(
            fm and re.search(r"^status:\s*in-progress\s*$", fm.group(1), re.MULTILINE)
        )
        unchecked = re.search(r"^\s*[-*]\s\[ \]", text, re.MULTILINE)
        if not (in_progress and unchecked):
            continue
        record = approvals.get(plan_hash(text))
        if not (
            isinstance(record, dict)
            and record.get("task_id")
            and record.get("task_id") == task
        ):
            continue
        invalid = record_is_valid(record, key)
        if not invalid and os.path.normcase(str(record.get("base") or "")) != root:
            invalid = "승인 당시와 다른 git 루트(프로젝트)"
        if invalid:
            # Keep looking (another plan may hold a valid approval) but report the first.
            problem = problem or f"{invalid}: {plan.name}"
            continue
        found.append({**record, "rearmed_from": str(plan)})
    if len(found) > 1:
        names = ", ".join(str(f.get("task_id")) for f in found)
        return None, f"{AMBIGUOUS}({names}) — task:<id> 하나만 지정 필요"
    if (
        found
    ):  # another named plan's record was refused: say so, without demanding anything
        return found[0], (f"{NOTE} {problem}" if problem else None)
    return None, problem


def named_plans(
    cwd: str, prompt: str
) -> tuple[str, list[tuple[Path, str, str | None]]]:
    """(git root, [(plan file, text, task)]) of the plans in this root's docs/plans that the
    prompt's naming text names by task id or by plan file."""
    import worktree

    naming = naming_text(prompt)
    tasks = prompt_tasks(naming)
    if not tasks and not PLAN_REF.search(naming):
        return "", []  # nothing named: no git call needed
    root = os.path.normcase(worktree.toplevel(cwd) or os.path.abspath(cwd))
    plans_dir = Path(root) / "docs" / "plans"
    named: list[tuple[Path, str, str | None]] = []
    for plan in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        try:
            text = plan.read_text(encoding="utf-8")
        except (OSError, ValueError):  # unreadable or not UTF-8: skip it
            continue
        task = plan_task(text)
        if (task and task.lower() in tasks) or names_plan(
            naming, plan, root, os.path.abspath(cwd)
        ):
            named.append((plan, text, task))
    return root, named


def active_approval(state: dict[str, Any], prompt_id: object) -> dict[str, Any] | None:
    approval = state.get("approval")
    if not isinstance(approval, dict):
        return None
    if (
        approval.get("prompt_id") == prompt_id
    ):  # re-armed approvals carry the prompt they serve
        return approval
    return None  # approved in an earlier prompt and not re-armed for this one


def path_class(real_path: str, base: str, in_repo: bool) -> str:
    """What kind of path an approval given in `base` (a git root) is asked to cover:
    "plan" (inside base/docs/plans), "code" (elsewhere inside base), "external" (outside
    base, inside or possibly inside another repository) or "data" (outside base and
    certainly in no repository: nothing tracks its changes)."""
    target, root = _norm(real_path), _norm(base)
    if target.startswith(root + "/docs/plans/"):
        return "plan"
    if (
        not in_repo
    ):  # also inside a base that is no repository (a session started there)
        return "data"
    inside = target == root or target.startswith(root + "/")
    return "code" if inside else "external"


def plain_path(path: str) -> str:
    r"""A Windows path without the `\\?\` long-path prefix (`\\?\UNC\server` -> `\\server`),
    so the same file is classified the same way however it was spelled."""
    if path.startswith(("\\\\?\\UNC\\", "//?/UNC/")):
        return "\\\\" + path[8:]
    if path.startswith(("\\\\?\\", "//?/")):
        return path[4:]
    return path


def in_approved_scope(approval: dict[str, Any] | None, path: str) -> bool:
    import fnmatch

    if approval is None:
        return False
    scope = approval.get("scope") or []
    if not scope:
        return not approval.get(
            "rearmed_from"
        )  # unscoped approval covers only its own prompt
    path = plain_path(path)
    patterns = [g for g in scope if isinstance(g, str)]
    data = [g[len(DATA_SCOPE) :] for g in patterns if g.startswith(DATA_SCOPE)]
    if is_unc(path):
        # Never resolved (a stalled share must not time the hook out) and never known to be
        # tracked by a repository: only the data block can cover it.
        target = _norm(path)
        return any(fnmatch.fnmatch(target, g) or target == g for g in data)
    # Plans and data are judged where the path really is: a link must not move another
    # repository's plans folder, or another data folder, under what was approved.
    real = os.path.realpath(path)
    in_repo = may_be_repository(real)
    base = approval.get("base")
    if isinstance(base, str) and base:
        kind = path_class(real, os.path.realpath(plain_path(base)), in_repo)
    else:  # a record without its repository: no plans exception
        kind = "external" if in_repo else "data"
    if kind == "plan":
        return True  # keeping the plan file up to date is part of the approved work
    if kind == "data":  # only the data block covers it, never a code pattern
        target = _norm(real)
        return any(fnmatch.fnmatch(target, g) or target == g for g in data)
    # Code (here or in another repository) is matched as the tool named it, as before.
    target = _norm(path)
    return any(
        fnmatch.fnmatch(target, g) or target == g
        for g in patterns
        if not g.startswith(DATA_SCOPE)
    )


# ---------------------------------------------------------------- P0-04: v2 approval instances
# Dormant until the admin `activate` command (approvals.py, interactive TTY only) creates
# APPROVALS_HOME/v2-active; until then everything above (v1) runs unchanged and nothing is
# written by v2. Under v2 the session keeps only a pointer (approval_id); every protected
# write re-reads and verifies the store and the plan file.
REVOKE_CMD = re.compile(
    r"^\s*revoke\s+task(?:_id)?\s*[:=]\s*`?([A-Za-z0-9][A-Za-z0-9_.-]*)",
    re.IGNORECASE | re.ASCII,
)
NOT_GLOBAL = "전역 취소 미완료"
REASON_V2_STORE = (
    "[discipline] 승인 저장소를 신뢰할 수 없어 모든 보호 대상 쓰기와 승인 발급을 차단합니다({why}). "
    "복구는 사용자가 대화형 터미널에서 `python -I ~/.claude/hooks/approvals.py reset`을 직접 실행해야 하며"
    "(권한은 발급되지 않음), 그 뒤 계획마다 다시 승인받아야 합니다. Claude는 이를 대신 실행할 수 없습니다."
)
REASON_V2_INVALID = (
    "[discipline] 승인(task: {task})이 더 이상 유효하지 않아 쓰기를 차단합니다: {why}. "
    "계속하려면 계획을 다시 제시해 ExitPlanMode로 새 승인을 받으세요(과거 승인은 되살아나지 않습니다)."
)


# == approvals.V2_FILES (test_approvals_v2 checks it): the marker, or any v2 file, means v2, so
# deleting the marker alone never falls back to v1.
V2_FILES = ("v2-active", "approvals.v2.json", "hwm")


def v2_active() -> bool:
    # Stats only: importing approvals on every hook call costs ~50-100 ms while v2 is dormant.
    return any((APPROVALS_HOME / n).exists() for n in V2_FILES)


def v2_failure(e: BaseException) -> str:
    """An unexpected failure in a v2 check, at the hook boundary: it must refuse, never let
    the tool proceed (a crashing hook is non-blocking in Claude Code)."""
    return REASON_V2_STORE.format(why=f"검사 중 오류 {type(e).__name__}: {e}")


def _v2_store() -> Any:
    import approvals

    return approvals.Store(APPROVALS_HOME, approvals.default_witness())


def _seen(state: dict[str, Any]) -> int:
    return int(state.get("approvals_seen_gen") or 0)


# The store as this run read it. One hook run is one process and lasts a few hundred ms;
# reading and verifying the store once per run instead of at every use costs nothing in
# guarantees: a revocation by another session that lands during the run was already able to
# land just after the last read. run_locked starts every run without it.
_v2_snap: Any = None


def _v2_forget() -> None:
    global _v2_snap
    _v2_snap = None


def _v2_load(state: dict[str, Any]) -> Any:
    global _v2_snap
    if _v2_snap is None:
        # this session's memory is a rollback witness too
        _v2_snap = _v2_store().load(_seen(state))
    state["approvals_seen_gen"] = max(_seen(state), _v2_snap.gen)
    return _v2_snap


def _v2_update(state: dict[str, Any], fn: Any) -> Any:
    global _v2_snap
    _v2_snap = (
        None  # if the change fails, what was read before it is not trusted either
    )
    snap = _v2_store().update(fn, _seen(state))
    state["approvals_seen_gen"] = snap.gen
    _v2_snap = snap
    return snap


def _v2_root(cwd: str) -> str:
    import worktree

    return os.path.normcase(worktree.toplevel(cwd) or os.path.abspath(cwd))


def _v2_find_plan(root: str, task: object, core: object) -> str | None:
    """The one plan file in root/docs/plans with this task and core hash (else None)."""
    import approvals

    plans_dir = Path(root) / "docs" / "plans"
    hits: list[Path] = []
    for plan in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        try:
            text = plan.read_text(encoding="utf-8")
        except (
            OSError,
            ValueError,
        ):  # unreadable or not UTF-8: cannot be the approved one
            continue
        if plan_task(text) == task and approvals.core_hash(text) == core:
            hits.append(plan)
    return os.path.normcase(os.path.relpath(hits[0], root)) if len(hits) == 1 else None


UNBOUND = "승인된 계획 파일이 아직 docs/plans에 없음(계획 파일을 먼저 기록하세요)"
UNBOUND_DIFFERS = (
    "승인된 글과 다른 계획 파일({names}): frontmatter에는 status만 더하고 본문은 "
    "승인된 그대로 저장하세요"
)
UNBOUND_TWICE = (
    "승인된 글이 계획 파일 여러 개에 있음({names}): docs/plans에 하나만 남기세요"
)
MAX_NAMED_PLANS = 3


def _v2_unbound_why(root: str, task: object, core: object) -> str:
    """Why an approval has no plan file yet, for the refusal's text only (the verdict does
    not depend on it). Among the files of this task in docs/plans: several holding the
    approved text, or some holding another text, are named (file names only); with no
    file of this task, the plain 'not saved yet'."""
    import approvals

    plans_dir = Path(root) / "docs" / "plans"
    same: list[str] = []
    other: list[str] = []
    for plan in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        try:
            text = plan.read_text(encoding="utf-8")
        except (OSError, ValueError):  # unreadable or not UTF-8: says nothing
            continue
        if task and plan_task(text) == task:
            (same if approvals.core_hash(text) == core else other).append(plan.name)
    names = same if len(same) > 1 else other
    if not names:
        return UNBOUND
    listed = ", ".join(names[:MAX_NAMED_PLANS])
    if len(names) > MAX_NAMED_PLANS:
        listed += f" 외 {len(names) - MAX_NAMED_PLANS}개"
    return (UNBOUND_TWICE if names is same else UNBOUND_DIFFERS).format(names=listed)


def _v2_read_plan(
    rec: dict[str, Any] | None, root: str
) -> tuple[str | None, str | None]:
    """(text, None); (None, None) when the bound plan file is gone (-> revoke); (None, why)
    when it cannot be judged now (unbound, locked, not UTF-8): refused, never revoked."""
    rel = (rec or {}).get("plan_path")
    if not rel:
        return None, UNBOUND
    try:
        return (Path(root) / str(rel)).read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"계획 파일을 지금 읽을 수 없음({type(e).__name__})"


def v2_observe(state: dict[str, Any], root: str) -> None:
    """Record permanently what is seen now: ACTIVE approvals of `root` whose plan left
    in-progress, changed its core or disappeared are revoked; unbound plan paths are bound.
    Raises StoreError when the store cannot be trusted. When the write fails, the causes stay
    pending (this session refuses them) and the failure is reported as not global."""
    import approvals as ap

    now = time.time()
    snap = _v2_load(state)
    pending: dict[str, str] = dict(state.get("pending_revocations") or {})
    binds: dict[str, str] = {}
    for rid, rec in snap.records.items():
        if rec.get("state") != ap.ACTIVE or rec.get("git_root") != root:
            continue
        if not rec.get("plan_path"):
            rel = _v2_find_plan(root, rec.get("task_id"), rec.get("core_hash"))
            if rel:
                binds[rid] = rel
            continue
        text, unreadable = _v2_read_plan(rec, root)
        if unreadable:
            continue  # cannot be judged now: refused at the write, never revoked for it
        verdict = ap.check(rec, root=root, plan_text=text, now=now)
        if verdict.revoke and verdict.reason:
            pending[rid] = verdict.reason
    pending = {
        rid: why
        for rid, why in pending.items()
        if (snap.records.get(rid) or {}).get("state") == ap.ACTIVE
    }
    if not pending and not binds:
        state["pending_revocations"] = {}
        return

    def apply(recs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out = dict(recs)
        for rid, rel in binds.items():
            if rid in out and not out[rid].get("plan_path"):
                out[rid] = {**out[rid], "plan_path": rel}
        for rid, why in pending.items():
            out = ap.revoke(out, [rid], why, now)
        return out

    try:
        _v2_update(state, apply)
    except ap.StoreError as e:
        state["pending_revocations"] = pending
        if pending:
            state["approval_warning"] = (
                f"승인 폐기를 저장하지 못했습니다({e}) — {NOT_GLOBAL}: 이 세션에서만 차단되며 "
                "다른 세션에는 반영되지 않았을 수 있습니다"
            )
        return
    state["pending_revocations"] = {}


def v2_record_approval(
    data: dict[str, Any],
    state: dict[str, Any],
    text: str,
    response: dict[str, Any],
    cwd: str,
) -> None:
    """ExitPlanMode: issue a new approval instance; nothing is granted unless it is stored."""
    import approvals as ap

    now = time.time()
    root = _v2_root(cwd)
    task = plan_task(text) or ""
    core = ap.core_hash(text)
    plan_path = None
    fp = response.get("filePath")
    if isinstance(fp, str) and _inside(fp, os.path.join(root, "docs", "plans")):
        try:
            same = ap.core_hash(Path(fp).read_text(encoding="utf-8")) == core
        except (OSError, ValueError):  # not written yet / unreadable: bound later
            same = False
        if same:
            plan_path = os.path.normcase(os.path.relpath(fp, root))
    plan_path = plan_path or _v2_find_plan(root, task, core)
    rec = ap.new_record(
        task_id=task,
        git_root=root,
        plan_path=plan_path,
        core=core,
        scope=plan_scope(text, cwd),
        session_id=data.get("session_id"),
        prompt_id=data.get("prompt_id"),
        now=now,
    )
    state["approval"] = None
    try:
        _v2_update(state, lambda recs: ap.issue(recs, rec, now))
    except ap.StoreError as e:
        state["approval_warning"] = (
            f"승인을 저장하지 못해 발급하지 않았습니다({e}). 쓰기는 차단됩니다. "
            "저장소 문제를 해결한 뒤 계획을 다시 승인받으세요"
        )
        return
    # A stored, fresh user approval: what was decided about the approval it replaces
    # (lost for the rest of the prompt) is not a verdict on this one.
    state["approval_invalid"] = None
    state["v2_block"] = None
    state["approval"] = {
        "v": 2,
        "approval_id": rec["approval_id"],
        "task_id": task,
        "scope": rec["scope"],
        "prompt_id": data.get("prompt_id"),
        "base": root,
    }


def v2_revoke_task(state: dict[str, Any], root: str, task: str) -> str | None:
    """`revoke task:<id>` (first line): revoke this root's ACTIVE approvals of the task."""
    import approvals as ap

    now = time.time()
    ids: list[str] = []
    try:
        snap = _v2_load(state)
        ids = [
            rid
            for rid, r in snap.records.items()
            if r.get("state") == ap.ACTIVE
            and str(r.get("task_id") or "").lower() == task.lower()
            and r.get("git_root") == root
        ]
        if not ids:
            return f"{NOTE} 취소할 ACTIVE 승인이 없습니다(task:{task})"
        _v2_update(state, lambda recs: ap.revoke(recs, ids, "user_revoked", now))
    except ap.StoreError as e:
        state.setdefault("pending_revocations", {}).update(
            {rid: "user_revoked" for rid in ids}
        )
        return (
            f"승인 취소를 저장하지 못했습니다({e}) — {NOT_GLOBAL}: 이 세션에서만 차단되며 "
            "다른 세션에는 반영되지 않았을 수 있습니다"
        )
    return None


def v2_rearm(
    cwd: str, prompt: str, state: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """v2 counterpart of rearm_approval: the named plan's ACTIVE instance, verified now."""
    import approvals as ap

    revoke_cmd = REVOKE_CMD.match(naming_text(prompt))
    if revoke_cmd:
        return None, v2_revoke_task(state, _v2_root(cwd), revoke_cmd.group(1))
    root, named = named_plans(cwd, prompt)
    if not named:
        return None, None
    try:
        v2_observe(state, root)
        snap = _v2_load(state)
    except ap.StoreError as e:
        return None, f"승인 저장소를 신뢰할 수 없음({e})"
    now = time.time()
    pending = state.get("pending_revocations") or {}
    found: list[dict[str, Any]] = []
    problems: list[str] = []
    for plan, text, task in named:
        rel = os.path.normcase(os.path.relpath(plan, root))
        recs = [
            r
            for r in snap.records.values()
            if r.get("state") == ap.ACTIVE
            and r.get("task_id") == task
            and r.get("git_root") == root
            and r.get("plan_path") == rel
        ]
        if not recs:
            problems.append(f"유효한 v2 승인 없음: {plan.name}")
        for rec in recs:
            why = (
                f"폐기 대기({NOT_GLOBAL})"
                if rec["approval_id"] in pending
                else ap.check(rec, root=root, plan_text=text, now=now).reason
            )
            if why:
                problems.append(f"{why}: {plan.name}")
            else:
                found.append(rec)
    if len(found) > 1:
        names = ", ".join(str(f.get("task_id")) for f in found)
        return None, f"{AMBIGUOUS}({names}) — task:<id> 하나만 지정 필요"
    if not found:
        return None, "; ".join(problems) or None
    rec = found[0]
    pointer = {
        "v": 2,
        "approval_id": rec["approval_id"],
        "task_id": rec.get("task_id"),
        "scope": list(rec.get("scope") or []),
        "base": root,
        "rearmed_from": str(Path(root) / str(rec.get("plan_path"))),
    }
    return pointer, (f"{NOTE} {'; '.join(problems)}" if problems else None)


# Whether the last v2_write_check of this hook run refused only because the ACTIVE
# approval has no plan file bound yet (never kept in the session state: one event, one
# process). Unbound is not invalid: saving the approved plan binds it.
_refused_unbound = False
# ... and whether it let a write into docs/plans through for such an approval: that pass
# says nothing about the approval being valid for other files.
_passed_unbound = False


def v2_write_check(
    state: dict[str, Any], cwd: str, path: str | None = None
) -> str | None:
    """Denial reason for a protected write under v2, else None. The store is read and
    verified in every hook run (once per run, see _v2_load); a cached ACTIVE pointer alone
    never authorizes anything. Only a
    lost approval (revoked, expired, superseded, missing) sticks for the rest of the prompt;
    a plan that cannot be judged right now refuses this write only."""
    import approvals as ap

    global _refused_unbound, _passed_unbound
    _refused_unbound = _passed_unbound = False
    ptr = state.get("approval")
    has_ptr = isinstance(ptr, dict) and bool(ptr.get("approval_id"))
    root = str(ptr.get("base")) if has_ptr and isinstance(ptr, dict) else _v2_root(cwd)
    try:
        v2_observe(state, root)
        snap = _v2_load(state)
    except ap.StoreError as e:
        state["v2_block"] = str(e)
        return REASON_V2_STORE.format(why=e)
    if not (has_ptr and isinstance(ptr, dict)):
        return None  # no approval: the five-file allowance applies (P0-06)
    rid = str(ptr["approval_id"])
    why = state.get("approval_invalid")
    if not why and rid in (state.get("pending_revocations") or {}):
        why = f"폐기 대기({NOT_GLOBAL})"
    if not why:
        rec = snap.records.get(rid)
        text, unreadable = _v2_read_plan(rec, root)
        if rec and rec.get("state") == ap.ACTIVE and unreadable:
            plans = os.path.normcase(os.path.join(root, "docs", "plans"))
            if unreadable == UNBOUND and path and _inside(path, plans):
                _passed_unbound = True
                return None  # writing the approved plan file itself is what binds it
            if unreadable == UNBOUND:
                _refused_unbound = True
                unreadable = _v2_unbound_why(
                    root, rec.get("task_id"), rec.get("core_hash")
                )
            state["v2_block"] = unreadable
            return REASON_V2_INVALID.format(task=ptr.get("task_id"), why=unreadable)
        verdict = ap.check(rec, root=root, plan_text=text, now=time.time())
        if verdict.reason is None and rec:
            ptr["scope"] = list(
                rec.get("scope") or []
            )  # the stored scope, not the cache
            return None
        why = verdict.reason
    if not state.get("approval_invalid") and "plan_status:done" in str(why):
        # The plan was closed as done: the ordinary end of a task. What it changed inside
        # its scope stays covered for this turn's Stop; any other loss covers nothing.
        cover_changes(state, ptr)
        known = state.get("ledger_closed")
        if (
            ptr.get("task_id")
            and ptr.get("base")
            and not (
                isinstance(known, dict)
                and known.get("approval_id") == ptr.get("approval_id")
            )
        ):
            # ... and the ledger records the task's last state before the approval goes
            # (on_post / on_stop write it, pinned to the content of this moment). A record
            # only: nothing is approved by it.
            state["ledger_closed"] = {
                "task_id": ptr.get("task_id"),
                "approval_id": ptr.get("approval_id"),
                "base": ptr.get("base"),
                "note": why,
                "pin": True,
            }
    state["approval_invalid"] = why
    state["v2_block"] = why
    return REASON_V2_INVALID.format(task=ptr.get("task_id"), why=why)


MAX_DIRTY_FILES = 200


def mark_dirty(
    state: dict[str, Any], path: str, at: float, lines: int = 0, by: object = None
) -> None:
    """Group an edited code file under its project's verification policy (nearest declaration).
    `by` lists who made the edit (labels for the reasons, see `actor`)."""
    from verification import find_policy

    policy = find_policy(path)
    entry = state["dirty"].setdefault(policy["root"], {"files": []})
    entry.update(
        {
            "at": max(at, float(entry.get("at", 0))),
            "gate": policy["gate"],
            "ci": policy["ci"],
            "validate": policy["validate"],
        }
    )
    files = entry.setdefault("files", [])
    if path not in files and len(files) < MAX_DIRTY_FILES:
        files.append(path)
    changed = entry.setdefault("lines", {})
    changed[path] = int(changed.get(path, 0)) + lines
    if by:
        seen = entry.get("by")
        if not isinstance(seen, list):
            seen = entry["by"] = []
        add_actors(seen, by)


def reresolve_dirty(state: dict[str, Any]) -> None:
    """Policies can be declared or changed after an edit: regroup edited files by their current policy."""
    old = state["dirty"]
    state["dirty"] = {}
    for entry in old.values():
        files = entry.get("files") or []
        if not files:
            continue  # state written by an older hook version: nothing to regroup
        for f in files:
            mark_dirty(
                state,
                f,
                float(entry.get("at", 0)),
                int((entry.get("lines") or {}).get(f, 0)),
                by=entry.get("by"),
            )


def changed_lines(tool: str, ti: dict[str, Any]) -> int:
    """Approximate size of one edit (used only for the review-needed rule)."""
    if tool == "Write":
        return len(str(ti.get("content", "")).splitlines())
    pairs = ti.get("edits") if tool == "MultiEdit" else [ti]
    total = 0
    for e in pairs or []:
        if isinstance(e, dict):
            total += max(
                len(str(e.get("old_string", "")).splitlines()),
                len(str(e.get("new_string", "")).splitlines()),
            )
    return total


# ---------------------------------------------------------------- E-1 independent review (A/B switch)
RISKY_PATH = re.compile(
    r"[\\/](hooks|migrations|scaffold)[\\/]|[\\/]\.github[\\/]workflows[\\/]|settings\.json$|auth|secret|credential",
    re.IGNORECASE,
)
REVIEW_MIN_FILES = 3
REVIEW_MIN_LINES = 150


def review_triggers(entry: dict[str, Any], root: str = "") -> list[str]:
    """Why this project's change needs an independent review (empty = it does not)."""
    files = [str(f) for f in entry.get("files") or []]
    why: list[str] = []
    if len(files) >= REVIEW_MIN_FILES:
        why.append(f"코드 파일 {len(files)}개")
    lines = sum(int(n) for n in (entry.get("lines") or {}).values())
    if lines >= REVIEW_MIN_LINES:
        why.append(f"변경 {lines}줄")
    # Classify by the path inside the project: a project under some ".../tests/..." folder
    # must not make every file look like a test or a risky path.
    rel = [os.path.relpath(f, root) if root and _inside(f, root) else f for f in files]
    risky = [r for r in rel if RISKY_PATH.search(os.sep + r)]
    if risky:
        why.append(f"위험 경로 {Path(risky[0]).name}")
    tests = [r for r in rel if TEST_PATH.search(r)]
    if tests and len(tests) < len(files):
        why.append("테스트와 구현 동시 변경")
    return why


def record_evidence(
    data: dict[str, Any], state: dict[str, Any], *, failed: bool
) -> None:
    """Attach what this shell command proves (if anything) to each project with unverified edits."""
    from verification import Policy, evaluate

    reresolve_dirty(state)

    ti = data.get("tool_input", {}) or {}
    background = shell_in_background(data)
    is_ps = data.get("tool_name") == "PowerShell"
    cwd = str(data.get("cwd") or os.getcwd())
    now = time.time()
    from verification import find_policy

    command = str(ti.get("command", ""))
    targets = shell_targets(command, is_ps, cwd)
    pre = state.get("pre_check", {}).pop(str(data.get("tool_use_id") or ""), None)
    # No pre-run record (pre hook killed, entry evicted, no tool_use_id) means the content the
    # check ran on is unknown: such a run cannot be a pass.
    changed_during = (
        "검사 전 내용 기록이 없음(검사한 내용을 확인할 수 없음)"
        if not isinstance(pre, dict)
        else changed_while_running(state, pre)
    )
    policies: dict[str, Policy] = {}
    for root, entry in state["dirty"].items():
        policies[root] = {
            "root": root,
            "gate": entry.get("gate"),
            "ci": entry.get("ci", "undeclared"),
            "validate": entry.get("validate"),
        }
    # D-5: projects changed outside the Edit tool have no dirty entry; the directories this
    # command ran in decide which project's evidence it can be.
    for d in targets:
        found = find_policy(os.path.join(d, "_"))
        policies.setdefault(found["root"], found)
    failed_gates: list[str] = []
    for root, policy in policies.items():
        entry = state["dirty"].get(root, {})
        ev = evaluate(
            str(ti.get("command", "")),
            "powershell" if is_ps else "bash",
            cwd,
            policy,
            failed=failed,
            background=background,
        )
        if ev is None:
            continue
        if ev["outcome"] == "passed" and changed_during:
            ev = {
                **ev,
                "outcome": "unknown",
                "why": changed_during,
            }
        old = state["evidence"].get(root) or {}
        still_valid_pass = old.get("outcome") == "passed" and float(
            old.get("at", 0)
        ) >= float(entry.get("at", 0))
        if still_valid_pass and ev["outcome"] in ("partial", "unknown"):
            continue  # a later side run does not revoke a passed gate for unchanged code
        record: dict[str, Any] = {**ev, "at": now, "by": actor(data)}
        if ev["outcome"] == "passed":
            record["digests"] = evidence_digests(state, root, targets[-1])
        state["evidence"][root] = record
        if ev["outcome"] == "failed" and ev.get("kind") == "gate":
            failed_gates.append(root)
    gate_root = innermost_root(failed_gates)
    if gate_root is not None:
        note_gate_failure(data, state, gate_root, failed=failed)
    record_ci_activity(str(ti.get("command", "")), is_ps, cwd, state, failed=failed)
    # here too, not only at the Stop: a CI failure that is fixed within the turn counts
    note_ci_failures(state, data.get("session_id"))


def record_ci_activity(
    command: str, is_ps: bool, cwd: str, state: dict[str, Any], *, failed: bool
) -> None:
    """`git push` records what was pushed; `gh run watch|view|list` re-reads CI from the API."""
    from shellparse import ParseError, parse, program_and_args
    from verification import cwd_at

    try:
        cmds = [
            c for c in parse(command, "powershell" if is_ps else "bash") if not c.nested
        ]
    except ParseError:
        return
    now = time.time()
    for i, cmd in enumerate(cmds):
        program, args = program_and_args(cmd)
        if (
            program == "git"
            and "push" in args[:3]
            and not failed
            and not {"--dry-run", "-n"} & set(args)
        ):
            from ci_status import push_info

            info = push_info(cwd_at(cmds, i, cwd))
            if info:
                state.setdefault("pushes", {})[info["toplevel"]] = {**info, "at": now}
                state.setdefault("ci", {}).pop(info["toplevel"], None)
        elif (
            program == "gh"
            and args[:1] == ["run"]
            and args[1:2]
            and args[1] in ("watch", "view", "list")
        ):
            from ci_status import fetch_ci

            for top, push in state.get("pushes", {}).items():
                state.setdefault("ci", {})[top] = {**fetch_ci(push), "at": now}


FAILURES_NOTED_MAX = 60
FAILURE_TAIL_CHARS = 20_000  # the end of a gate's output is where it failed


def note_failure(
    state: dict[str, Any], root: str, source: str, kind: object, session_id: object
) -> None:
    """P2-04: count a failure's kind once per session in the recurrence file of the
    repository that holds `root` (see recurrence.py). A record for a later note; it
    changes nothing a hook decides. CLAUDE_RECURRENCE=0 switches it off."""
    if os.environ.get("CLAUDE_RECURRENCE") == "0":
        return
    try:
        import recurrence

        if (
            not isinstance(session_id, str)
            or not session_id
            or source not in recurrence.SOURCES
            or kind not in recurrence.KINDS
        ):
            return
        tops = [t for t in (state.get("repos") or {}) if isinstance(t, str)]
        top = _repo_place(root, tops)[0]
        if not top:
            # not a tracked repository (tracking ran out of time, say): ask git once, so
            # that the row lands under the key the session start reads with
            import worktree

            top = os.path.normcase(worktree.toplevel(root) or root)
        noted = state.setdefault("failures_noted", [])
        key = f"{top}|{source}|{kind}"
        if (
            not isinstance(noted, list)
            or key in noted
            or len(noted)
            >= FAILURES_NOTED_MAX  # "once per session" can no longer be kept
        ):
            return
        recurrence.record(top, source, str(kind), session_id, time.time())
        noted.append(key)
    except Exception:  # hook boundary: a record, nothing depends on it
        log.exception("discipline.recurrence_failed")


def innermost_root(roots: list[str]) -> str | None:
    """The deepest of the project roots one failed gate run was recorded for (an outer
    project that declares the same gate is not where it failed)."""
    return max(roots, key=lambda r: len(os.path.normpath(r)), default=None)


def note_gate_failure(
    data: dict[str, Any], state: dict[str, Any], root: str, *, failed: bool
) -> None:
    """A gate run that failed: its kind, from the end of its output (used for the
    classification only, never stored), goes to the recurrence file."""
    if os.environ.get("CLAUDE_RECURRENCE") == "0":
        return
    try:
        from ci_status import classify_failure

        parts: list[str] = []
        for source in (
            data.get("tool_response"),
            data.get("error") if failed else None,
        ):
            if isinstance(source, str):
                parts.append(source)
            elif isinstance(source, dict):
                parts.extend(v for v in source.values() if isinstance(v, str))
        tail = "\n".join(parts)[-FAILURE_TAIL_CHARS:]
        kind = classify_failure("", "failure", tail)
    except Exception:  # hook boundary: a record, nothing depends on it
        log.exception("discipline.recurrence_failed")
        return
    note_failure(state, root, "gate", kind, data.get("session_id"))


def note_ci_failures(state: dict[str, Any], session_id: object) -> None:
    """Stop: every repository whose CI is known to have failed, with the kind the CI check
    gave it, is counted (once per session and kind)."""
    known = state.get("ci")
    for top, ci in (known if isinstance(known, dict) else {}).items():
        if (
            isinstance(top, str)
            and isinstance(ci, dict)
            and ci.get("status") == "failed"
        ):
            note_failure(state, top, "ci", ci.get("kind"), session_id)


def on_fail(data: dict[str, Any], state: dict[str, Any]) -> None:
    """PostToolUseFailure: a failed check is still evidence (of failure)."""
    if data.get("tool_name") in ("Bash", "PowerShell"):
        note_shell_touch(data, state, after=True)  # also when no PreToolUse was seen
        record_evidence(data, state, failed=True)
        note_background_task(data, state)
        note_output_size(data, state, failed=True)


MAX_BACKGROUND_TASKS = 50
TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
TASK_ID_LINE = re.compile(r"<task-id>([A-Za-z0-9_-]{1,64})</task-id>", re.ASCII)


def note_background_task(data: dict[str, Any], state: dict[str, Any]) -> None:
    """Remember the id of a background shell command this session started: the
    `backgroundTaskId` of a Bash/PowerShell result. Only the hook's own view of the start
    counts later (v2_keep_on_relay); the id is never taken from a prompt. A command a
    subagent of this session put in the background counts too (the session started it).
    A failure event that carries no `tool_response` records nothing."""
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return
    response = data.get("tool_response")
    task_id = response.get("backgroundTaskId") if isinstance(response, dict) else None
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        return
    known = state.get("background_tasks")
    tasks = [t for t in known if t != task_id] if isinstance(known, list) else []
    state["background_tasks"] = [*tasks, task_id][-MAX_BACKGROUND_TASKS:]


def relay_task_id(prompt: str) -> str | None:
    """The task id of a completion notification: `<task-notification>` alone on the first
    non-blank line and `<task-id>ID</task-id>` alone on the very next one. Any other shape
    gives None."""
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if len(lines) < 2 or lines[0] != "<task-notification>":
        return None
    found = TASK_ID_LINE.fullmatch(lines[1])
    return found.group(1) if found else None


def started_here(state: dict[str, Any], prompt: str) -> bool:
    """True when the relayed prompt reports on something this session itself started, by
    the hook's own record: the hand-back of a subagent it saw start and end (SubagentStart,
    then SubagentStop), or the completion notification of a subagent or of a background
    shell command (PostToolUse)."""
    agent_id = relay_agent_id(prompt)
    if agent_id is not None:
        return finished_agent(state, agent_id)
    task_id = relay_task_id(prompt)
    if task_id is None:
        return False
    tasks = state.get("background_tasks")
    return (isinstance(tasks, list) and task_id in tasks) or started_agent(
        state, task_id
    )


def relay_shaped(prompt: str) -> bool:
    """A prompt that looks like a relayed agent message or task notification. UserPromptSubmit
    also runs for those (documented) and no hook field tells them from typed text, so this may
    only make the hook stricter: such a prompt keeps the turn's counted files and baselines,
    and by its shape alone never keeps or grants an approval (what keeps one is this
    session's own record of having started the thing reported on, see started_here)."""
    first = next((ln for ln in prompt.splitlines() if ln.strip()), "").strip().lower()
    return first.startswith(RELAY_HEADERS) or first.startswith("<task-notification>")


RELAY_NOTE = (
    f"{NOTE} 중계 메시지(subagent 결과·알림)로는 승인이 유지되지 않습니다 — "
    "작업을 이어가려면 사용자가 프롬프트 첫 줄에 task:<id>를 적어 재개해야 합니다"
)
AGENT_HANDBACK = re.compile(r'<agent-message from="([A-Za-z0-9_.-]+)">', re.ASCII)


def relay_agent_id(prompt: str) -> str | None:
    """The sender id a prompt gives as that of a subagent hand-back: `<agent-message
    from="ID">` alone on the first non-blank line, or on the line right after the relay
    header. The prompt hook is handed the tag without the header line, mid-work and as a
    prompt of its own alike (observed 2026-11-17). This only reads the id: whether it is a
    hand-back is decided by this session's own record of that subagent (finished_agent),
    never by the text. Any other shape gives None."""
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    if lines and lines[0].lower().startswith(RELAY_HEADERS[0]):
        lines = lines[1:]
    found = AGENT_HANDBACK.fullmatch(lines[0]) if lines else None
    return found.group(1) if found else None


def started_agent(state: dict[str, Any], agent_id: str) -> bool:
    """True when SubagentStart recorded an agent with exactly this id in this session
    (labels are `<agent_type>:<agent_id>`, see `agent_key`)."""
    agents = state.get("agents")
    if not isinstance(agents, dict):
        return False
    return any(
        isinstance(label, str) and label.rpartition(":")[2] == agent_id
        for label in agents
    )


def finished_agent(state: dict[str, Any], agent_id: str) -> bool:
    """True when this session recorded a subagent with exactly this id starting and, after
    that, ending (SubagentStart sets `stopped` to None again when one is resumed): only
    then can a prompt be that subagent's hand-back."""
    agents = state.get("agents")
    if not isinstance(agents, dict):
        return False

    def ended(entry: object) -> bool:
        if not isinstance(entry, dict):
            return False
        began, stopped = entry.get("started"), entry.get("stopped")
        return (
            isinstance(began, float)
            and isinstance(stopped, float)
            and math.isfinite(stopped)
            and stopped >= began > 0
        )

    return any(
        isinstance(label, str) and label.rpartition(":")[2] == agent_id and ended(entry)
        for label, entry in agents.items()
    )


HANDBACK_WAIT_S = (
    3.0  # observed 2026-11-17: a stop record up to 0.97 s after its hand-back
)
HANDBACK_POLL_S = 0.1
# What this run waited for a stop record (await_agent_stop), kept in prompt_seen so that the
# limit can be tuned from what was measured.
_handback_wait: dict[str, Any] = {}


def handback_wait_limit(env: Any) -> float:
    """How long a hand-back prompt may wait for its subagent's stop record, in seconds:
    CLAUDE_DISCIPLINE_HANDBACK_WAIT_S when it is a number that is finite and not negative,
    else the default."""
    raw = env.get("CLAUDE_DISCIPLINE_HANDBACK_WAIT_S")
    try:
        seconds = float(raw) if raw else HANDBACK_WAIT_S
    except ValueError:  # malformed override: use the default
        return HANDBACK_WAIT_S
    return seconds if math.isfinite(seconds) and seconds >= 0 else HANDBACK_WAIT_S


def peek_state(path: Path) -> dict[str, Any] | None:
    """The saved session state read without the session lock and without touching the file
    (session_store.load moves an unreadable one aside: that needs the lock). None when it
    is missing, being replaced right now, or not an object. For looking only: nothing read
    this way is decided on."""
    import session_store

    try:
        if path.stat().st_size > session_store.MAX_FILE_BYTES:
            return None  # the store refuses it too; and a long read would be in its way
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def running_agent(state: dict[str, Any], agent_id: str) -> bool:
    """True when this session recorded a subagent with exactly this id starting and has no
    end recorded for it yet."""
    agents = state.get("agents")
    if not isinstance(agents, dict) or finished_agent(state, agent_id):
        return False
    return any(
        isinstance(label, str)
        and label.rpartition(":")[2] == agent_id
        and isinstance(entry, dict)
        and isinstance(entry.get("started"), float)
        for label, entry in agents.items()
    )


def await_agent_stop(
    data: dict[str, Any],
    path: Path,
    *,
    read: Any = peek_state,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
    limit: float | None = None,
) -> dict[str, Any] | None:
    """Before the session lock is taken: when the prompt gives the id of a subagent this
    session started and no end is recorded for it yet, wait for that record (SubagentStop
    runs in another process and needs the lock itself; the two arrive in either order),
    looking every HANDBACK_POLL_S up to the limit. Returns how long it waited and whether
    the record came, None when there was nothing to wait for. It decides nothing: on_prompt
    reads the state again under the lock. Never raises."""
    try:
        prompt = data.get("prompt")
        sender = relay_agent_id(prompt) if isinstance(prompt, str) else None
        if sender is None:
            return None
        try:
            state = read(path)
        except OSError:  # cannot be looked at: nothing is known to wait for
            return None
        if not isinstance(state, dict) or not running_agent(state, sender):
            return None
        if limit is None:
            # never more than half of what the hook has left: the lock and git need the rest
            limit = min(
                handback_wait_limit(os.environ),
                hook_time_left("prompt", time.monotonic() - _T0) / 2,
            )
        began = clock()
        seen = False
        while (left := limit - (clock() - began)) > 1e-9:
            sleep(min(HANDBACK_POLL_S, left))
            try:
                state = read(path)
            except OSError:  # being replaced right now: not yet
                continue
            if isinstance(state, dict) and finished_agent(state, sender):
                seen = True
                break
        return {"waited": round(clock() - began, 3), "stop_seen": seen}
    except Exception:  # hook boundary: a wait that fails is no wait
        log.exception("discipline.handback_wait_failed")
        return None


def v2_keep_on_relay(
    state: dict[str, Any], cwd: str, prompt: str
) -> tuple[dict[str, Any] | None, str | None]:
    """(pointer, warning) for a relayed prompt under v2 (R1). The pointer survives only a
    report on something this session started (see started_here: a subagent's hand-back, or
    the completion notification of a subagent or a background shell command), in the
    approval's own git root, and only if the approval verifies right now or is merely not
    bound to its plan file yet (the notification came between the approval and the save;
    until it is bound it approves nothing outside docs/plans). Every later write is
    verified again. Anything else drops it as before. A relay never creates an approval:
    no pointer in, no pointer out."""
    ptr = state.get("approval")
    dropped = (None, RELAY_NOTE if ptr else None)
    if not (isinstance(ptr, dict) and ptr.get("v") == 2 and ptr.get("approval_id")):
        return dropped
    if not started_here(state, prompt):
        return dropped
    if _v2_root(cwd) != str(ptr.get("base")):
        return dropped
    refused = v2_write_check(state, cwd)
    if refused is None or _refused_unbound:
        # An approval without a scope covers only the prompt it was given in (see
        # in_approved_scope); a relay must not stretch it over further prompts.
        if refused is not None:
            state["v2_block"] = None  # not a loss: the plan file can still be saved
        return (ptr, None) if ptr.get("scope") else dropped
    why = state.get("v2_block")
    if refused == REASON_V2_STORE.format(why=why):
        return None, f"승인 저장소를 신뢰할 수 없음({why})"
    return None, f"중계 뒤 승인을 유지하지 못함: {why}"


MAX_CONTEXT_ROOTS = 20
SWITCH_NOTICE_CHARS = 1500
SWITCH_NOTE_CHARS = 240


def switch_notice(
    root: str, main_path: str | None, notes: list[str], plans_read: bool = True
) -> str:
    """What a session is told when its working directory moves into a repository it has
    not seen (P0-10): where it is now, that earlier approvals do not apply there, and that
    project's open plans. Information only; no decision reads it."""
    lines = [
        f"[discipline] 작업 폴더가 이 세션에서 처음 보는 저장소로 바뀌었습니다: `{root}`",
        (
            "이전 프로젝트에서 받은 계획 승인은 이 저장소에 적용되지 않습니다. 이 저장소의 계획을 "
            "이어가려면 사용자가 프롬프트 첫 줄에 task:<id>를 적거나, 새 계획을 승인받아야 합니다."
        ),
    ]
    if main_path:
        lines.append(f"진행 중인 계획: `{main_path}` (계속하기 전에 다시 읽으세요)")
    elif plans_read and not notes:
        lines.append("이 저장소의 docs/plans에는 진행 중이거나 막힌 계획이 없습니다.")
    lines += [n[:SWITCH_NOTE_CHARS] for n in notes]
    return "\n".join(lines)[:SWITCH_NOTICE_CHARS].rstrip()


def new_context_root(state: dict[str, Any], top: str | None) -> bool:
    """True when the repository `top` must be announced now. The first user prompt of a
    session only records where it is (SessionStart already described that place); after
    that, each repository is announced the first time a prompt arrives in it."""
    seen = state.setdefault("context_roots", [])
    if not state.get("context_seeded"):
        state["context_seeded"] = 1
        if top and top not in seen:
            seen.append(top)
        return False
    if not top or top in seen:
        return False
    seen.append(top)
    del seen[:-MAX_CONTEXT_ROOTS]
    return True


def project_switch_context(top: str) -> str:
    """The notice for `top`, with its plans as the SessionStart hook would summarise them.
    A failure to read them shortens the notice; it never stops the prompt."""
    try:
        import session_context

        main, summaries = session_context.read_plans(Path(top))
        main_path = main.path if main else None
        return switch_notice(
            top, main_path, session_context.plan_notes(summaries, main_path)
        )
    except Exception:  # hook boundary: the notice is information, not a gate
        log.exception("discipline.switch_context_failed")
        return switch_notice(top, None, [], plans_read=False)


LOST_NOTE = (
    "[discipline] 승인(task: {task})이 이 프롬프트에서 끊겼습니다({why}). 계속하려면 "
    "사용자에게 프롬프트 첫 줄 task:{task} 를 요청하세요. 그 전에는 범위 안의 파일도 "
    "고치지 마세요."
)
CLOSED_NOTE = (
    "[discipline] 작업(task: {task})의 계획이 done으로 닫혀 승인이 끝났습니다. 이어서 할 "
    "일이 있으면 새 계획을 세워 승인받으세요."
)


def already_closed(state: dict[str, Any], held: object) -> bool:
    """True when `held` is the approval of the task this session has just closed as done
    (remembered for the ledger): an approval that ended in the ordinary way."""
    closed = state.get("ledger_closed")
    return (
        isinstance(held, dict)
        and isinstance(closed, dict)
        and bool(closed.get("approval_id"))
        and closed.get("approval_id") == held.get("approval_id")
    )


def approval_lost_note(
    state: dict[str, Any],
    task_before: object,
    relay: bool,
    mid_work: bool = False,
    not_handback: bool = False,
) -> str | None:
    """What to say, in this very prompt, when the session held an approval coming in and
    does not hold it (or holds another task's) going out. The decision is made elsewhere;
    this only makes sure it is never silent. None when nothing was lost. `mid_work`: the
    prompt carried the id of the prompt in progress without looking like a relay (a message
    that arrived while the session was working), which cannot continue an approval.
    `not_handback`: the prompt gave a sender id that is not a subagent this session saw
    start and end."""
    if not isinstance(task_before, str) or not task_before:
        return None
    after = state.get("approval")
    task_after = after.get("task_id") if isinstance(after, dict) else None
    if task_after == task_before:
        return None
    warning = state.get("approval_warning")
    ended = " ".join(
        str(x)
        for x in (warning, state.get("v2_block"), state.get("approval_invalid"))
        if x
    )
    closed = state.get("ledger_closed")
    if (
        isinstance(closed, dict) and closed.get("task_id") == task_before
    ) or "plan_status:done" in ended:
        return CLOSED_NOTE.format(
            task=task_before
        )  # the ordinary end: nothing to resume
    if isinstance(task_after, str) and task_after:
        why = f"프롬프트가 다른 작업(task: {task_after})을 이름함"
    elif isinstance(warning, str) and warning and warning != RELAY_NOTE:
        why = warning[:160]
    elif not_handback:
        why = (
            "이 세션이 띄워 끝난 것으로 기록된 subagent의 결과 보고가 아님(없는 id이거나 "
            "아직 끝나지 않음)"
        )
    elif mid_work:
        why = (
            "작업 중에 도착한 메시지로는 승인을 이을 수 없음: 이 턴이 끝난 뒤 새 프롬프트의 "
            "첫 줄에 적어야 함"
        )
    elif relay:
        why = "이 세션이 시작하지 않은 알림이거나 승인을 유지할 조건이 맞지 않음"
    else:
        why = "프롬프트가 작업을 이름하지 않음"
    return LOST_NOTE.format(task=task_before, why=why)


MAX_PROMPTS_SEEN = 12
_TAG_LINE = re.compile(r"<([a-z][a-z_-]{0,30})[\s>]", re.ASCII)


def line_kind(line: str) -> str:
    """What kind of line a prompt starts with, as one of a few fixed labels. The label is
    all that is kept of the line: nothing of its text."""
    if not line:
        return "none"
    low = line.lower()
    if low.startswith(RELAY_HEADERS[0]):
        return "relay-header"
    if low.startswith(RELAY_HEADERS[1]):
        return "system-notification"
    if line == "<task-notification>":
        return "task-notification"
    if TASK_ID_LINE.fullmatch(line):
        return "task-id"
    if AGENT_HANDBACK.fullmatch(line):
        return "agent-message"
    if re.match(r"task:\s*\S", line):
        return "task-line"
    if line.startswith("/"):
        return "slash"
    tag = _TAG_LINE.match(line)
    if tag:
        return f"tag:{tag.group(1)}"
    return "bracket" if line.startswith("[") else "text"


def note_prompt_seen(
    data: dict[str, Any], state: dict[str, Any], kind: str, before: object
) -> None:
    """Diagnosis only (never read to decide): the shape of what the prompt hook was handed.
    The names of the payload's keys, the prompt id, the length of the text, a short hash of
    it (to tell a re-sent prompt), the kind of its first two non-blank lines (line_kind),
    how the hook classified it, which task was approved before and after, and what the run
    waited for a subagent's stop record. Nothing of the prompt's text is kept: telling
    prompts apart needs their shape only."""
    try:
        import hashlib

        prompt = data.get("prompt")
        text = prompt if isinstance(prompt, str) else ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:2]
        after = state.get("approval")
        entry = {
            "at": time.time(),
            "keys": sorted(str(k)[:40] for k in data)[:30],
            "prompt_id": str(data.get("prompt_id"))[:64],
            "chars": len(text),
            "sha": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12],
            "lines": [line_kind(ln) for ln in lines],
            "kind": kind,
            "before": before,
            "after": after.get("task_id") if isinstance(after, dict) else None,
            # how long this prompt waited for a subagent's stop record, and whether it came
            "waited": _handback_wait.get("waited"),
            "stop_seen": _handback_wait.get("stop_seen"),
        }
        seen = state.get("prompt_seen")
        # entries an earlier version wrote may hold the start of a prompt's text
        kept = [
            {k: v for k, v in old.items() if k in entry}
            for old in (seen if isinstance(seen, list) else [])
            if isinstance(old, dict)
        ]
        state["prompt_seen"] = [*kept, entry][-MAX_PROMPTS_SEEN:]
    except Exception:  # hook boundary: a diagnosis that fails changes nothing
        log.exception("discipline.prompt_seen_failed")


def on_prompt(data: dict[str, Any], state: dict[str, Any]) -> None:
    state["turn"] = int(state.get("turn", 0)) + 1
    prompt = data.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    cwd = str(data.get("cwd") or os.getcwd())
    # Structure before shape: a prompt that carries the id of the prompt in progress was not
    # typed as a new prompt by the user (a result or notification delivered mid-work keeps
    # that id; observed 2026-11-17). It is handled as a relay whatever its text looks like.
    continues = bool(data.get("prompt_id")) and data.get("prompt_id") == state.get(
        "prompt_id"
    )
    # The hand-back of a subagent this session started and saw end is a relay too, by that
    # record: it arrives as a prompt of its own with a new prompt id (observed 2026-11-17).
    sender = relay_agent_id(prompt)
    handback = sender is not None and finished_agent(state, sender)
    relay = relay_shaped(prompt) or continues or handback
    held_before = state.get("approval")
    task_before = held_before.get("task_id") if isinstance(held_before, dict) else None
    if not relay:  # a relay continues the user's turn: its counts and baselines stay
        state["turn_files"] = []
        state["turn_shell_tops"] = []
    # A new prompt never inherits the previous approval; only a prompt that names the same
    # in-progress plan's task (or plan file) re-arms it.
    state["approval_invalid"] = None
    state["v2_block"] = None
    if relay and v2_active():
        had = bool(state.get("approval"))
        # what was done under the approval so far is recorded before the relay decides
        cover_valid(data, state, state.get("approval"))
        try:
            state["approval"], state["approval_warning"] = v2_keep_on_relay(
                state, cwd, prompt
            )
        except Exception as e:  # hook boundary: the approval is dropped, never a crash
            log.exception("approvals.v2_hook_failure")
            state["approval"] = None
            state["approval_warning"] = v2_failure(e) if had else None
    elif relay:
        had = bool(state.get("approval"))
        state["approval"] = None
        state["approval_warning"] = RELAY_NOTE if had else None
    elif v2_active():
        try:
            state["approval"], state["approval_warning"] = v2_rearm(cwd, prompt, state)
        except Exception as e:  # hook boundary: nothing re-armed rather than a crash
            log.exception("approvals.v2_hook_failure")
            state["approval"], state["approval_warning"] = None, v2_failure(e)
    else:
        state["approval"], state["approval_warning"] = rearm_approval(cwd, prompt)
    if not state["approval"] and already_closed(state, held_before):
        # the plan of that task had been closed as done: its approval ended there, so this
        # prompt lost nothing and leaves no warning behind
        state["approval_warning"] = None
    state["prompt_id"] = data.get("prompt_id")
    if state["approval"]:
        # Bound to this prompt: if a later prompt's hook never runs (killed, timed out), the
        # stale approval does not carry over to it.
        state["approval"]["prompt_id"] = data.get("prompt_id")
    note_prompt_seen(data, state, "relay" if relay else "user", task_before)
    told = ack_missing(data, state)  # the user's `ack-missing <path>`, if this is one
    lost = approval_lost_note(
        state,
        task_before,
        relay,
        continues and not relay_shaped(prompt) and sender is None,
        sender is not None and not handback,
    )
    if lost:
        told = "\n\n".join(x for x in (told, lost) if x)
    if not relay and state.get("test_gap_note"):
        # P1-03: said once, to the user's own next prompt (a relayed one leaves it)
        told = "\n\n".join(x for x in (told, str(state["test_gap_note"])) if x)
        state["test_gap_note"] = ""
    if (
        not relay
        and state.get("risk_note")
        and os.environ.get("CLAUDE_RISK_SCORE") != "0"
    ):
        # P2-01: the same way, once (a note left from before the score was switched off
        # is not told)
        told = "\n\n".join(x for x in (told, str(state["risk_note"])) if x)
        state["risk_note"] = ""
    try:
        top = track_repo(state, str(data.get("cwd") or os.getcwd()))
        if not relay and new_context_root(state, top) and top:
            told = "\n\n".join(x for x in (told, project_switch_context(top)) if x)
    finally:
        # One JSON object per hook run, printed by run_hook: an ack-missing answer already
        # recorded in the state is delivered even if tracking the repository fails, and a
        # failure after this point is told in the same object.
        if told:
            _prompt_pending.append(told)
    if not relay:
        refresh_turn_baselines(state)
        state["turn_covered"] = {}
        state["turn_done_under"] = {}
        state["turn_approval_from"] = {}
        state["turn_cover_off"] = 0


# ---------------------------------------------------------------- D-5 content tracking
# Every git repository the session touches gets a content map ({path: blob}) as baseline
# when it is first seen (before the tool that touches it runs) and a per-turn baseline at
# each prompt. Stop compares them with the current contents, so edits made by any tool
# (Edit, sed, scripts) are seen, commits are not mistaken for edits, and verification
# evidence is bound to the content hash it was produced on.
MAX_DIR_CACHE = 200
MAX_PRE_CHECKS = 20
GIT_BUDGET_S = {
    "pre": 20.0,
    "prompt": 20.0,
    "post": 45.0,
    "fail": 45.0,
    "stop": 45.0,
    "agent-start": 20.0,
    "agent-stop": 20.0,
}
# Mirrors the discipline hook timeouts in settings.json (a test fails when they drift). The
# lock wait and the git budget both come out of this one deadline, so the hook decides
# (closed) before Claude Code kills it.
HOOK_TIMEOUT_S = {
    "pre": 30.0,
    "prompt": 30.0,
    "post": 60.0,
    "fail": 60.0,
    "stop": 60.0,
    "agent-start": 30.0,
    "agent-stop": 30.0,
}
# Subagent lifecycle events carry nothing to verify: one that cannot take the session lock
# is dropped, not remembered as a missed event (that would mark the session unverified).
# One that fails after the lock is handled like any other event: it may already have taken
# other events' missed notes, which must not be lost.
LIFECYCLE_MODES = ("agent-start", "agent-stop")
HOOK_MARGIN_S = 5.0  # initial prior: interpreter start, handler work, save


def hook_time_left(mode: str, elapsed: float) -> float:
    return HOOK_TIMEOUT_S[mode] - HOOK_MARGIN_S - elapsed


def lock_budget_s(mode: str, elapsed: float, configured: float) -> float:
    """Wait for the session lock at most half of what is left, so git keeps the rest."""
    return max(0.0, min(configured, hook_time_left(mode, elapsed) / 2))


def git_budget_s(mode: str, elapsed: float, configured: float) -> float:
    return max(0.0, min(configured, hook_time_left(mode, elapsed)))


def _watch_globs(top: str) -> list[str]:
    from verification import find_policy
    from worktree import rel_prefix

    policy = find_policy(os.path.join(top, "_"))
    prefix = rel_prefix(top, policy["root"]) if policy["root"] else None
    if prefix is None:
        return []
    return [f"{prefix}/{g}" if prefix else g for g in policy.get("watch", [])]


def is_repo_root(top: str) -> bool:
    """A git toplevel is at `top` right now. Asked from git, never from the directory cache: a
    plain folder or another repository's subfolder at a missing repository's path is not it.
    No answer in time counts as no."""
    import worktree

    if not os.path.isdir(top):
        return False
    found = worktree.toplevel(top)
    return bool(found) and os.path.normcase(os.path.abspath(found)) == os.path.normcase(
        os.path.abspath(top)
    )


def repo_present(top: str) -> bool:
    """Cheap presence test (no git call): the folder is there and still carries its `.git`
    entry. A folder left or re-created without one is not the repository that was tracked."""
    return os.path.isdir(top) and os.path.exists(os.path.join(top, ".git"))


def forget_dir_tops(state: dict[str, Any], top: str) -> None:
    """Drop cached directory -> toplevel answers about `top`: after a repository went missing or
    came back, the next lookup there asks git again."""
    cache = state.get("dir_tops")
    if not isinstance(cache, dict):
        return
    for probe in [p for p, t in cache.items() if t == top or _inside(p, top)]:
        cache.pop(probe, None)


def restore_repo(state: dict[str, Any], top: str) -> None:
    """A path whose repository had gone missing has a repository again: it is compared with that
    path's original baselines (no identity guessing), so nothing unverified is forgotten and
    nothing there is taken as already verified."""
    state.setdefault("repos", {})[top] = state["repos_removed"].pop(top)
    if top in state.get("content_removed", []):
        state["content_removed"].remove(top)
    forget_dir_tops(state, top)
    late_writers = state.get("background_tops")
    if isinstance(late_writers, list) and late_writers and top not in late_writers:
        # A background shell started while it was away may write here too.
        late_writers.append(top)


def track_repo(state: dict[str, Any], path: str) -> str | None:
    """Git toplevel containing `path`; a newly seen repository is snapshotted right away."""
    import worktree

    p = os.path.abspath(path)
    repos = state.setdefault("repos", {})
    # Resolve per directory with git, never by path prefix: a repository nested inside
    # another repository's folder (ignored subrepo, submodule) has its own toplevel.
    probe = p if os.path.isdir(p) else os.path.dirname(p)
    dir_tops = state.setdefault("dir_tops", {})
    cached = probe in dir_tops
    if cached and dir_tops[probe] and not repo_present(dir_tops[probe]):
        dir_tops.pop(probe)  # that repository is gone: ask git what is here now
        cached = False
    if cached:
        top = dir_tops[probe]
    else:
        top = worktree.toplevel(p)
        if worktree.budget_exhausted:
            missed = state.setdefault("missed_dirs", [])
            if probe not in missed and len(missed) < MAX_DIR_CACHE:
                missed.append(
                    probe
                )  # this directory's repository went unobserved for a while
        elif len(dir_tops) < MAX_DIR_CACHE:
            dir_tops[probe] = top
    if not top:
        return None
    if top not in repos and top in state.get("repos_removed", {}):
        # A repository that was missing: never a fresh baseline. An answer git gave just now is
        # a toplevel; a cached one (a state saved before missing repositories' cache entries
        # were dropped) is asked again, and a path that is no toplevel stays missing.
        if cached and not is_repo_root(top):
            dir_tops.pop(probe, None)
            return None
        restore_repo(state, top)
    if top not in repos or repos[top].get("session") is None:
        # New repository, or its first snapshot failed (timeout/budget): (re)take the baseline.
        # A baseline taken after a failed attempt may already contain unverified edits, so it
        # is marked late: its changes are reported unknown, never taken as "unchanged".
        # So is a repository at a path the user acknowledged as missing: what was there was
        # never verified, and nothing says this one is another.
        late = (
            top in repos
            or any(_inside(d, top) for d in state.get("missed_dirs", []))
            or acked_before(state, top)
        )
        snap = worktree.content_map(top, _watch_globs(top))
        repos[top] = {
            "session": snap,
            "turn": snap,
            "turn_head": worktree.head(top),
            "turn_at": time.time(),
            "late": late,
        }
        late_writers = state.get("background_tops")
        if isinstance(late_writers, list) and late_writers and top not in late_writers:
            # A background shell started earlier may write here too (absolute paths): this
            # repository is never "untouched" for the rest of the session either.
            late_writers.append(top)
    return top


def refresh_turn_baselines(state: dict[str, Any]) -> None:
    import worktree

    for top, base in state.get("repos", {}).items():
        base["turn"] = worktree.content_map(top, _watch_globs(top))
        base["turn_head"] = worktree.head(top)
        base["turn_at"] = time.time()


def repo_snapshots(
    state: dict[str, Any], tops: list[str]
) -> dict[str, dict[str, str] | None]:
    """Relevant file contents of each repository before a check runs (None: unavailable)."""
    import worktree

    out: dict[str, dict[str, str] | None] = {}
    for top in tops:
        snap = worktree.content_map(top, _watch_globs(top))
        tracked = worktree.tracked_paths(top) if snap is not None else None
        out[top] = (
            None
            if snap is None or tracked is None
            else {
                p: h
                for p, h in snap.items()
                if worktree.relevant(p)
                and (p in tracked or p.lower().endswith(worktree.SOURCE_EXT))
            }
        )
    return out


def changed_while_running(state: dict[str, Any], pre: dict[str, Any]) -> str:
    """Why the content a check ran on differs from the content now ("" if it does not).
    Files the check itself created (reports, coverage) do not count; edits and deletions of
    files that existed before it ran do."""
    import worktree

    for top, before in sorted(pre.items()):
        now = (
            worktree.content_map(top, _watch_globs(top)) if before is not None else None
        )
        if before is None or now is None:
            return "검사 전후 내용을 확인할 수 없음(git 조회 실패)"
        if any(now.get(p) != h for p, h in before.items()):
            return "검사 실행 중 파일이 바뀜(검사한 내용과 현재 내용이 다를 수 있음)"
    return ""


def check_repos(
    state: dict[str, Any], command: str, is_ps: bool, cwd: str
) -> list[str] | None:
    """Repositories whose content a check command could be evidence for (None if not a check)."""
    from shellparse import Dialect
    from verification import Policy, evaluate, find_policy

    reresolve_dirty(state)  # the same policy grouping record_evidence() will use
    targets = shell_targets(command, is_ps, cwd)
    policies: list[Policy] = [find_policy(os.path.join(d, "_")) for d in targets]
    for r, e in state.get("dirty", {}).items():
        policies.append(
            {
                "root": r,
                "gate": e.get("gate"),
                "ci": e.get("ci", "undeclared"),
                "validate": e.get("validate"),
            }
        )
    dialect: Dialect = "powershell" if is_ps else "bash"
    if not any(
        evaluate(command, dialect, cwd, p, failed=False, background=False)
        for p in policies
    ):
        return None
    tops = {track_repo(state, d) for d in targets} | {
        track_repo(state, p["root"]) for p in policies if p["root"]
    }
    return sorted(t for t in tops if t)


def shell_targets(command: str, is_ps: bool, cwd: str) -> list[str]:
    """Directories a shell command works in, in order: the hook cwd, then every `cd` target."""
    from shellparse import ParseError, parse, program_and_args
    from verification import cwd_at

    try:
        cmds = [
            c for c in parse(command, "powershell" if is_ps else "bash") if not c.nested
        ]
    except ParseError:
        return [cwd]
    dirs = [cwd]
    for i, c in enumerate(cmds):
        program, args = program_and_args(c)
        if program in ("cd", "set-location", "sl", "pushd", "chdir") and args:
            target = cwd_at(cmds, i + 1, cwd)
            if target not in dirs:
                dirs.append(target)
    return dirs


def refresh_content(state: dict[str, Any]) -> None:
    """Stop-time comparison of current contents with the session/turn baselines."""
    import worktree
    from verification import find_policy

    content: dict[str, Any] = {}
    unknown: list[str] = []
    too_large: list[str] = []
    late: list[str] = []
    turn_changes: set[str] = set()
    turn_answered: set[str] = set()
    done_under = state.get("turn_done_under")
    done_under = done_under if isinstance(done_under, dict) else {}
    visible: set[str] = set()
    kept = state.setdefault("repos_removed", {})
    for top in [t for t in kept if is_repo_root(t)]:
        restore_repo(state, top)  # it came back: compare with the original baseline
    touched = set(state.get("touched_tops", [])) | set(state.get("background_tops", []))
    status = state.setdefault("repo_state", {})
    for top in [t for t in state.get("repos", {}) if not repo_present(t)]:
        forget_dir_tops(state, top)
        # A missing repository (deleted, moved, renamed) cannot be compared now. Its baseline
        # is kept by path (a repository appearing there again is compared with it, never taken
        # as verified). Without guessing where it went: proven clean (clean at the last Stop
        # and untouched since) is a normal deletion; anything else is reported every turn.
        base = state["repos"].pop(top)
        if status.get(top, "clean") == "clean" and top not in touched:
            status.pop(
                top, None
            )  # proven clean: nothing kept for a new repository there
            state.get("owners", {}).pop(top, None)
            continue
        kept[top] = base
        if top not in state.setdefault("content_removed", []):
            state["content_removed"].append(top)
    for top, base in state.get("repos", {}).items():
        now = worktree.content_map(top, _watch_globs(top))
        if now is None or base.get("session") is None:
            (too_large if worktree.last_error == "too_large" else unknown).append(top)
            continue
        if base.get("late"):
            late.append(
                top
            )  # baseline may include unverified edits: timestamps decide there
        else:
            visible |= {
                os.path.normcase(os.path.join(top, p))
                for p in set(now) | set(base["session"])
            }
        turn_base = base.get("turn") or base["session"]
        moved = worktree.changed(turn_base, now)
        head_then, head_now = base.get("turn_head"), worktree.head(top)
        if head_then and head_now and head_then != head_now:
            # checkout/pull/reset moved HEAD: files whose old and new content equal the old and
            # new commits came from git, not from an edit made in this turn.
            old_tree, new_tree = (
                worktree.tree_map(top, head_then),
                worktree.tree_map(top, head_now),
            )
            # Commits made during this turn (`edit; git commit`) are this turn's own edits, so
            # their paths are never excluded.
            own = worktree.paths_committed_since(
                top, head_then, head_now, float(base.get("turn_at") or 0)
            )
            if old_tree is not None and new_tree is not None and own is not None:
                moved = {
                    p
                    for p in moved
                    if p in own
                    or not (
                        turn_base.get(p) == old_tree.get(p)
                        and now.get(p) == new_tree.get(p)
                    )
                }
        covered = state.get("turn_covered") or {}
        if covered:
            # covered by the approval that was valid when another replaced it, and not
            # touched since (same content): not this approval's to answer for
            moved = {
                p
                for p in moved
                if covered.get(os.path.normcase(os.path.join(top, p)))
                != (now.get(p) or "")
            }
        turn_changes |= {os.path.join(top, p) for p in moved}
        turn_answered |= {
            os.path.join(top, p)
            for p in moved
            if done_under.get(os.path.normcase(os.path.join(top, p)), None)
            == (now.get(p) or "")
        }
        changed_roots: dict[str, Any] = {}
        for p in worktree.changed(base["session"], now):
            if worktree.relevant(p):
                policy = find_policy(os.path.join(top, p))
                changed_roots.setdefault(policy["root"], policy)
        push = state.get("pushes", {}).get(top)
        pushed_tree = worktree.tree_map(top, push["sha"]) if push else None
        for root, policy in changed_roots.items():
            prefix = (worktree.rel_prefix(top, root) or "") if root else ""
            entry = content.setdefault(
                root,
                {
                    "digests": {},
                    "pushed": {},
                    "gate": policy["gate"],
                    "ci": policy["ci"],
                },
            )
            entry["digests"][top] = worktree.digest(now, prefix)
            entry["pushed"][top] = (
                pushed_tree is not None
                and worktree.digest(pushed_tree, prefix) == entry["digests"][top]
            )
    state["content"] = content
    state["content_unknown"] = unknown
    state["content_too_large"] = too_large
    state["content_late"] = late
    # What each present repository is at this Stop (used if it goes missing later): clean =
    # no change since the session baseline, or every change covered by passed evidence.
    for top in state.get("repos", {}):
        if top in unknown or top in too_large or top in late:
            status[top] = "unverified"
            continue
        verified = True
        for root, entry in content.items():
            if top in entry.get("digests", {}):
                ev = state.get("evidence", {}).get(root) or {}
                verified &= ev.get("outcome") == "passed" and all(
                    ev.get("digests", {}).get(t) == d
                    for t, d in entry["digests"].items()
                )
        status[top] = "clean" if verified else "unverified"
    state["touched_tops"] = []
    state["turn_changes"] = sorted(turn_changes)
    # listed changes that were made inside the scope of an approval valid at that moment and
    # not touched since (cover_valid): the scope decision does not ask about them again
    state["turn_answered"] = sorted(turn_answered)
    # Edit-tracked roots whose files are all visible to git: content decides, not timestamps.
    state["content_tracked_roots"] = [
        root
        for root, entry in state.get("dirty", {}).items()
        if entry.get("files")
        and all(os.path.normcase(os.path.abspath(f)) in visible for f in entry["files"])
    ]


def evidence_digests(state: dict[str, Any], root: str, cwd: str) -> dict[str, str]:
    """Content hashes a passed check was run on (bound into the evidence)."""
    import worktree

    own = track_repo(state, root or cwd)
    if not own:
        return {}
    # The project root's own repository plus every tracked repository nested inside the root
    # (same keys refresh_content() produces), so a nested repo never leaves evidence stale.
    tops = {own} | (
        {t for t in state.get("repos", {}) if _inside(t, root)} if root else set()
    )
    out: dict[str, str] = {}
    for top in sorted(tops):
        snap = worktree.content_map(top, _watch_globs(top))
        if snap is None:
            return {}  # partial evidence is no evidence
        prefix = (worktree.rel_prefix(top, root) or "") if root else ""
        out[top] = worktree.digest(snap, prefix)
    return out


REASON_UNVERIFIED = (
    "[discipline] 마지막 코드 수정 이후 검증(테스트/타입체크/린트/실행)을 실행하지 않았습니다. "
    "프로젝트의 품질 게이트를 실행하고 결과를 보고하세요. "
    "검증이 불가능한 변경이면 그 이유를 '미확인'으로 명시하세요."
)
REASON_FAILED = (
    "[discipline] 마지막 검증이 실패한 상태로 종료하려 합니다. "
    "고칠 수 있으면 고치고 다시 검증하세요. 아니면 실패한 명령, 실패 내용, 원인 추정을 보고의 "
    "'미완료·리스크'에 그대로 적고 종료하세요(이미 그렇게 보고했다면 바로 종료해도 됩니다)."
)
REASON_UNKNOWN = (
    "[discipline] 마지막 검증의 결과를 확인할 수 없습니다({why}). 종료 코드가 가려지면 실패해도 성공처럼 "
    "보입니다. 검증 명령을 단독으로(또는 &&로만 이어서) 포그라운드에서 다시 실행하거나, 결과를 확인할 수 "
    "없었다는 사실을 '미완료·리스크'에 적고 종료하세요."
)
REASON_APPROVAL_INVALID = (
    "[discipline] 저장된 계획 승인 기록을 신뢰할 수 없어 자동 재승인하지 않았습니다({why}). "
    "이 작업을 계속하려면 계획을 다시 제시해 사용자 승인을 받으세요. 이 사실을 보고에 적으세요."
)
REASON_PROMPT_FAILED = (
    "[discipline] {why}. 그 프롬프트가 승인을 이을 수 있는지 판정하지 못했습니다. 계속하려면 "
    "사용자에게 프롬프트 첫 줄 task:<id> 를 요청하세요. 이 사실을 보고에 적으세요."
)
REASON_APPROVAL_AMBIGUOUS = (
    "[discipline] 프롬프트가 승인된 진행 중 계획 여러 개를 가리켜 어느 것도 재무장하지 않았습니다({why}). "
    "승인 기록은 유효합니다. 프롬프트 첫 줄에 task:<id> 하나만 적어 다시 요청해 달라고 사용자에게 알리세요."
)
REASON_RELAY_DROPPED = "[discipline] {why}. 지금은 승인된 작업이 없습니다. 이 사실과 재개 방법을 사용자에게 알리세요."
REASON_STATE_RESET = (
    "[discipline] 세션 추적 상태 손상 또는 누락({why}): 그 이전의 변경은 미확인입니다. "
    "해당 프로젝트들의 품질 게이트가 이 시점 이후 통과할 때까지 완료(Stop)를 턴마다 한 번 막습니다. "
    "보고에 '미확인'으로 적으세요. 범위 안의 경로가 사라졌거나 게이트가 없어 통과시킬 수 없다면, 사용자가 프롬프트 "
    "첫 줄에 ack-missing <경로> 를 입력해 그 경로만 확인으로 해제할 수 있습니다(검증으로 치지 않습니다)."
)
REASON_HOOK_ERROR = (
    "[discipline] 추적 hook 처리 중 오류({why}). 이번 이벤트를 신뢰할 수 있게 기록하지 못해 안전하게 막습니다. "
    "다음 실행에서 '세션 추적 상태 누락'으로 표시되며, 그 전의 변경은 미확인입니다. 이 사실을 사용자에게 알리세요. "
    "같은 오류가 반복되면 사용자가 프롬프트 첫 줄에 recover-state 를 입력해 복구할 수 있습니다(원본 보존, 미검증 표식 유지, "
    "승인은 task:<id>로 다시 재무장). 상태 파일을 직접 지우거나 고치지 마세요."
)
REASON_STATE_BUSY = (
    "[discipline] 세션 상태 잠금을 얻지 못했습니다({why}). 이번 판정을 기록할 수 없어 안전하게 막습니다. "
    "다른 hook(서브에이전트 포함)이 끝난 뒤 다시 시도하세요."
)
REASON_REMOVED = (
    "[discipline] 추적하던 저장소 {top}가 사라졌습니다(삭제·이동·이름 변경). 그 안에 검증되지 않은 "
    "변경이 있었거나 확인할 수 없어 '미확인'입니다. 그 경로로 되돌려 게이트를 통과시키기 전까지 매 턴 "
    "보고됩니다(다른 경로의 저장소로 이어 받지 않습니다). 보고에 '미확인'으로 적으세요. "
    "되돌릴 수 없다면 사용자가 프롬프트 첫 줄에 ack-missing {top} 을 입력해 확인으로 해제할 수 있습니다"
    "(검증으로 치지 않습니다). 이 방법을 사용자에게 알리세요."
)
REASON_APPROVAL_NOTE = (
    "[discipline] {why}. 지정한 승인 작업은 계속할 수 있습니다. "
    "거부된 쪽이 의도한 계획이었다면 그 사실을 사용자에게 알리세요."
)
REASON_STALE = (
    "[discipline] {root}: 마지막 검증 이후 파일 내용이 바뀌었습니다(Bash·스크립트 수정 포함, 내용 해시 기준). "
    "검증 증거가 무효이니 게이트를 다시 실행하세요."
)
REASON_OUT_OF_SCOPE = (
    "[discipline] 이번 턴의 변경 중 {n}개가 승인된 계획(task: {task})의 변경 범위 밖입니다: {files}. "
    "되돌리거나 계획을 갱신해 다시 승인받으세요."
)
REASON_BULK = (
    "[discipline] 승인된 계획 없이 이번 턴에 {n}개 파일이 바뀌었습니다(Bash 등 도구 밖 수정 포함, 한도 {limit}): "
    "{files}. /plan 으로 계획을 세워 승인받거나 변경을 줄이세요."
)
REASON_CONTENT_UNKNOWN = (
    "[discipline] {top}의 변경 상태를 확인할 수 없습니다(git 조회 실패 또는 파일 수 초과). "
    "변경이 검증됐다는 근거가 없으므로 '미확인'으로 보고하세요."
)
REASON_TOO_LARGE_NOTE = " (파일 수가 내용 추적 한도 5,000개를 넘습니다. Bash·스크립트로 한 변경은 감지할 수 없으므로, 이 저장소에서 셸 명령을 쓴 턴은 매번 '미확인'입니다.)"
REASON_LATE_NOTE = " (git 조회가 시간 안에 끝나지 않아 이 저장소의 기준선을 늦게 잡았습니다. 그 사이 변경은 검증 여부를 알 수 없습니다.)"
REASON_NOT_PUSHED = (
    "[discipline] {root}의 검증 정책은 `ci: required`인데, 이번 수정분이 push되지 않아 CI 검증이 없습니다"
    "(현재 상태: Locally Verified). push가 사용자 승인 대기라면 보고에 'CI 미검증(push 전)'으로 명시하고 종료하세요."
)
REASON_CI_UNCHECKED = (
    "[discipline] {ref} push 이후 CI 결과를 확인하지 않았습니다. `gh run watch <run id> --exit-status`로 "
    "확인하세요. 판정은 명령 출력이 아니라 GitHub API의 repository·branch·workflow·headSha·job 결과로 합니다."
)
REASON_CI_FAILED = (
    "[discipline] {ref}의 CI가 실패했습니다({detail}). 고친 뒤 다시 push해 확인하거나, 실패한 run과 원인을 "
    "'미완료·리스크'에 그대로 보고하세요."
)
REASON_CI_UNKNOWN = (
    "[discipline] {ref}의 CI 결과를 확정할 수 없습니다({detail}). 실행이 끝난 뒤 다시 확인하거나 "
    "'CI 미확인'으로 보고하세요."
)
REASON_REVIEW = (
    "[discipline] {root}의 변경이 독립 리뷰 기준({why})에 해당합니다(CLAUDE_REVIEW_GATE=1). 기존 `code-reviewer` "
    "에이전트로 리뷰하고, CONFIRMED 지적은 테스트로 재현해 고친 뒤 게이트를 다시 실행하세요. "
    "리뷰 통과 자체는 완료 근거가 아닙니다."
)
REASON_GATE_NOT_RUN = (
    "[discipline] {root}에 선언된 품질 게이트(`{gate}`)가 마지막 수정 이후 통과하지 않았습니다"
    "(다른 테스트 명령만 실행됨). 게이트를 그대로 실행하거나, 실행하지 못한 이유를 '미확인'으로 보고하세요."
)


def _stop_decision(state: dict[str, Any]) -> tuple[str, str | None] | None:
    """(reason, notify key): a reset marker (once per turn until its scope's gates pass)
    together with the first other problem, so the one block of the turn shows both."""
    damaged = state_reset_active(state)
    other = _first_stop_problem(state)
    if damaged:
        reset = REASON_STATE_RESET.format(why=damaged)
        if other:
            return f"{reset}\n\n{other[0]}", other[1]
        return reset, None
    return other


def _first_stop_problem(state: dict[str, Any]) -> tuple[str, str | None] | None:
    """(reason, notify key) for the first problem: scope, unreadable state, content, then timestamps."""
    warning = state.get("approval_warning")
    if warning:
        key = f"approval-warning|{state.get('turn')}|{warning}"
        if _not_yet_notified(state, key):
            template = (
                REASON_APPROVAL_AMBIGUOUS
                if str(warning).startswith(AMBIGUOUS)
                else REASON_RELAY_DROPPED
                if warning == RELAY_NOTE  # nothing approved continues after a relay
                else REASON_APPROVAL_NOTE
                if str(warning).startswith(NOTE)
                else REASON_PROMPT_FAILED
                if str(warning).startswith(PROMPT_FAILED_PREFIX)
                else REASON_APPROVAL_INVALID
            )
            return template.format(why=warning), key
    decision = _scope_decision(state)
    if decision:
        return decision
    for top in state.get("content_too_large", []):
        # Once per session, and again in every turn that ran shell commands there: that turn's
        # edits cannot be seen, so it must never end as verified.
        key = f"content-too-large|{top}"
        if top in state.get("turn_shell_tops", []):
            key += f"|{state.get('turn')}"
        if _not_yet_notified(state, key):
            return REASON_CONTENT_UNKNOWN.format(top=top) + REASON_TOO_LARGE_NOTE, key
    for top in state.get("content_removed", []):
        # Every turn while it is missing and not proven clean: its changes were never verified.
        key = f"content-removed|{top}|{state.get('turn')}"
        if _not_yet_notified(state, key):
            return REASON_REMOVED.format(top=top) + owner_note(
                (state.get("owners") or {}).get(top), state.get("agents")
            ), key
    for top in state.get("content_late", []):
        key = f"content-late|{top}"
        if _not_yet_notified(state, key):
            return REASON_CONTENT_UNKNOWN.format(top=top) + REASON_LATE_NOTE, key
    for top in state.get("content_unknown", []):
        key = f"content-unknown|{top}|{state.get('turn')}"
        if _not_yet_notified(state, key):
            return REASON_CONTENT_UNKNOWN.format(top=top), key
    for root, entry in sorted(state.get("content", {}).items()):
        decision = _content_decision(state, root, entry)
        if decision:
            return decision
    tracked = set(state.get("content_tracked_roots", []))
    for root, entry in sorted(state.get("dirty", {}).items()):
        edited_at = float(entry.get("at", 0))
        if edited_at <= 0 or root in tracked:
            continue  # content-tracked roots were decided above (or did not change at all)
        where = f" (프로젝트: {root})" if root else ""
        ev = state.get("evidence", {}).get(root) or {}
        ev_at = float(ev.get("at", 0))
        if ev_at < edited_at:
            return REASON_UNVERIFIED + where + owner_note(
                entry.get("by"), state.get("agents")
            ), None
        outcome = ev.get("outcome")
        if outcome == "partial":
            return REASON_GATE_NOT_RUN.format(root=root, gate=entry.get("gate")), None
        if outcome in ("failed", "unknown"):
            key = f"{root}|{ev_at}"
            if _not_yet_notified(state, key):
                reason = (
                    REASON_FAILED
                    if outcome == "failed"
                    else REASON_UNKNOWN.format(why=ev.get("why", ""))
                )
                return reason + where, key
            continue
        if os.environ.get("CLAUDE_REVIEW_GATE") == "1":
            why = review_triggers(entry, root)
            key = f"{root}|review|{edited_at}"
            if (
                why
                and float(state.get("last_review", 0)) < edited_at
                and _not_yet_notified(state, key)
            ):
                return REASON_REVIEW.format(
                    root=root or "이 프로젝트", why=", ".join(why)
                ), key
        if entry.get("ci") == "required":
            decision = _ci_decision(state, root, edited_at)
            if decision:
                return decision
    return None


def _scope_decision(state: dict[str, Any]) -> tuple[str, str] | None:
    """Changes made this turn by any tool must fit the approved plan's scope (or the 5-file limit)."""
    answered = set(state.get("turn_answered") or [])
    changes = [f for f in state.get("turn_changes") or [] if f not in answered]
    if not changes:
        return None
    prompt_id = state.get("stop_prompt_id")
    approval = active_approval(state, prompt_id)
    if approval:
        outside = [f for f in changes if not in_approved_scope(approval, f)]
        key = f"scope|{prompt_id}|{len(outside)}"
        if outside and _not_yet_notified(state, key):
            shown = ", ".join(Path(f).name for f in outside[:5])
            return REASON_OUT_OF_SCOPE.format(
                task=approval.get("task_id"), n=len(outside), files=shown
            ), key
        return None
    key = f"bulk|{prompt_id}|{len(changes)}"
    limit = 0 if AUTHZ_MODE == "enforce" else MAX_FILES_WITHOUT_PLAN
    if len(changes) > limit and _not_yet_notified(state, key):
        shown = ", ".join(Path(f).name for f in changes[:5])
        return REASON_BULK.format(n=len(changes), limit=limit, files=shown), key
    return None


def _content_decision(
    state: dict[str, Any], root: str, c: dict[str, Any]
) -> tuple[str, str | None] | None:
    """A project whose file contents changed this session: evidence must match the current content."""
    where = f" (프로젝트: {root})" if root else ""
    edited_by = owner_note(
        (state.get("dirty", {}).get(root) or {}).get("by"), state.get("agents")
    )
    ev = state.get("evidence", {}).get(root) or {}
    outcome = ev.get("outcome")
    digests: dict[str, str] = c.get("digests", {})
    if not (
        outcome == "passed"
        and all(ev.get("digests", {}).get(t) == d for t, d in digests.items())
    ):
        if outcome in ("failed", "unknown"):
            key = f"{root}|{float(ev.get('at', 0))}"
            if not _not_yet_notified(state, key):
                return None  # this failed/unknown result was already reported once
            reason = (
                REASON_FAILED
                if outcome == "failed"
                else REASON_UNKNOWN.format(why=ev.get("why", ""))
            )
            return reason + where, key
        if outcome == "partial":
            return REASON_GATE_NOT_RUN.format(root=root, gate=c.get("gate")), None
        if outcome == "passed":
            return REASON_STALE.format(root=root or "이 프로젝트"), None
        return REASON_UNVERIFIED + where + edited_by, None
    review = _review_decision(state, root)
    if review:
        return review
    if c.get("ci") != "required":
        return None
    ref_sha = "|".join(sorted(digests.values()))
    if not all(c.get("pushed", {}).get(t) for t in digests):
        key = f"{root}|nopush|{ref_sha}"
        return (
            (REASON_NOT_PUSHED.format(root=root), key)
            if _not_yet_notified(state, key)
            else None
        )
    for top in digests:
        push = state.get("pushes", {}).get(top) or {}
        ci = state.get("ci", {}).get(top) or {}
        ref = f"{push.get('repo')}@{str(push.get('sha', ''))[:7]}"
        if ci.get("sha") != push.get("sha") or float(ci.get("at", 0)) < float(
            push.get("at", 0)
        ):
            key = f"{root}|unchecked|{push.get('sha')}"
            return (
                (REASON_CI_UNCHECKED.format(ref=ref), key)
                if _not_yet_notified(state, key)
                else None
            )
        if ci.get("status") != "verified":
            key = f"{root}|ci|{push.get('sha')}|{ci.get('status')}|{ci.get('why')}"
            if not _not_yet_notified(state, key):
                return None
            runs = ", ".join(
                f"run {r.get('id')} {r.get('conclusion') or r.get('status')}"
                for r in ci.get("runs", [])
            )
            detail = "; ".join(x for x in (runs, str(ci.get("why") or "")) if x)
            template = (
                REASON_CI_FAILED if ci.get("status") == "failed" else REASON_CI_UNKNOWN
            )
            return template.format(ref=ref, detail=detail), key
    return None


def _not_yet_notified(state: dict[str, Any], key: str) -> bool:
    # Block once per result; again only within the same turn (the parallel notify hook
    # may read the state after discipline wrote the flag).
    turn = state.get("notified", {}).get(key)
    return turn is None or turn == state.get("turn")


def _inside(path: str, root: str) -> bool:
    p, r = (
        os.path.normcase(os.path.normpath(path)),
        os.path.normcase(os.path.normpath(root)),
    )
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _ci_decision(
    state: dict[str, Any], root: str, edited_at: float
) -> tuple[str, str] | None:
    """CI half of the completion check for a project whose policy says `ci: required`."""
    pushes = [
        (top, p)
        for top, p in state.get("pushes", {}).items()
        if _inside(root, top)
        and float(p.get("at", 0)) >= edited_at
        and not any(_inside(u, root) for u in p.get("uncommitted", []))
    ]
    if not pushes:
        key = f"{root}|nopush|{edited_at}"
        return (
            (REASON_NOT_PUSHED.format(root=root), key)
            if _not_yet_notified(state, key)
            else None
        )
    top, push = max(pushes, key=lambda tp: float(tp[1].get("at", 0)))
    ci = state.get("ci", {}).get(top) or {}
    ref = f"{push.get('repo')}@{str(push.get('sha', ''))[:7]}"
    if float(ci.get("at", 0)) < float(push.get("at", 0)) or ci.get("sha") != push.get(
        "sha"
    ):
        key = f"{root}|unchecked|{push.get('sha')}"
        return (
            (REASON_CI_UNCHECKED.format(ref=ref), key)
            if _not_yet_notified(state, key)
            else None
        )
    status = ci.get("status")
    if status == "verified":
        return None
    key = f"{root}|ci|{push.get('sha')}|{status}|{ci.get('why')}"
    if not _not_yet_notified(state, key):
        return None
    runs = ", ".join(
        f"run {r.get('id')} {r.get('conclusion') or r.get('status')}"
        for r in ci.get("runs", [])
    )
    detail = "; ".join(x for x in (runs, str(ci.get("why") or "")) if x)
    template = REASON_CI_FAILED if status == "failed" else REASON_CI_UNKNOWN
    return template.format(ref=ref, detail=detail), key


def stop_block_reason(state: dict[str, Any], stop_hook_active: bool) -> str | None:
    """Pure decision shared with notify.py: why this Stop is blocked, or None."""
    if stop_hook_active:
        return None  # already blocked once this turn; let Claude stop
    decision = _stop_decision(state)
    return decision[0] if decision else None


REVIEW_ORDER = ("missing", "stale", "running", "current", "none")  # worst first
REVIEW_OBSERVE_FILE = APPROVALS_HOME / "review-observe.jsonl"
MAX_REVIEW_OBSERVED = 50
MAX_REVIEW_ROOTS = 10


def review_state(state: dict[str, Any], root: str) -> str:
    """Whether the change in project `root` has had its independent review (P1-02):
    "none" (no review rule applies), "current" (a reviewer that has ended saw exactly this
    content), "running" (one that saw it has not ended), "stale" (reviewed, changed since),
    "missing". A project whose content git does not track is judged by time, as before."""
    entry = (state.get("dirty") or {}).get(root)
    if not isinstance(entry, dict) or not review_triggers(entry, root):
        return "none"
    content = (state.get("content") or {}).get(root)
    digests = content.get("digests") if isinstance(content, dict) else None
    if not digests or root not in (state.get("content_tracked_roots") or []):
        reviewed_at = _number(state.get("last_review")) or 0.0
        return (
            "current" if reviewed_at >= (_number(entry.get("at")) or 0.0) else "missing"
        )
    found = "missing"
    for agent in (state.get("agents") or {}).values():
        seen = agent.get("review") if isinstance(agent, dict) else None
        snap = seen.get(root) if isinstance(seen, dict) else None
        if not isinstance(snap, dict):
            continue
        if all(snap.get(top) == digest for top, digest in digests.items()):
            if agent.get("stopped"):
                return "current"
            found = "running"
        elif found == "missing":
            found = "stale"
    return found


def review_snapshot(state: dict[str, Any], cwd: str) -> dict[str, dict[str, str]]:
    """What a reviewer that starts now sees: the content hashes of the edited projects."""
    out: dict[str, dict[str, str]] = {}
    dirty = state.get("dirty") or {}
    newest = sorted(
        dirty,
        key=lambda r: _number((dirty.get(r) or {}).get("at")) or 0.0,
        reverse=True,
    )
    for root in newest[:MAX_REVIEW_ROOTS]:
        digests = evidence_digests(state, root, cwd)
        if digests:
            out[root] = digests
    return out


def _review_decision(state: dict[str, Any], root: str) -> tuple[str, str] | None:
    """The review gate (CLAUDE_REVIEW_GATE=1) for a project judged by content."""
    if os.environ.get("CLAUDE_REVIEW_GATE") != "1":
        return None
    if root not in (state.get("content_tracked_roots") or []):
        return None  # judged by time: _first_stop_problem asks for that review, once
    if review_state(state, root) in ("none", "current"):
        return None
    content = (state.get("content") or {}).get(root) or {}
    key = f"{root}|review|{'|'.join(sorted((content.get('digests') or {}).values()))}"
    if not _not_yet_notified(state, key):
        return None
    entry = (state.get("dirty") or {}).get(root) or {}
    return REASON_REVIEW.format(
        root=root or "이 프로젝트", why=", ".join(review_triggers(entry, root))
    ), key


def observe_reviews(state: dict[str, Any]) -> None:
    """One line per verified change that lacks a current review (what the review gate
    would hold, were it on): for judging the gate before it is made the default. No path
    and no content; never changes the Stop, so a failure is logged and nothing else."""
    try:
        seen: list[str] = state.setdefault("review_observed", [])
        for root, content in sorted((state.get("content") or {}).items()):
            digests = content.get("digests") or {}
            ev = (state.get("evidence") or {}).get(root) or {}
            if ev.get("outcome") != "passed" or not all(
                (ev.get("digests") or {}).get(t) == d for t, d in digests.items()
            ):
                continue  # not verified yet: the review question comes after that
            verdict = review_state(state, root)
            if verdict in ("none", "current"):
                continue
            key = f"{root}|{'|'.join(sorted(digests.values()))}|{verdict}"
            if key in seen:
                continue
            seen.append(key)
            del seen[:-MAX_REVIEW_OBSERVED]
            entry = (state.get("dirty") or {}).get(root) or {}
            row = {
                "at": time.time(),
                "state": verdict,
                "rules": len(review_triggers(entry, root)),
                "gate": os.environ.get("CLAUDE_REVIEW_GATE") == "1",
            }
            APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
            with open(REVIEW_OBSERVE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except (OSError, ValueError, TypeError, AttributeError):
        log.exception("discipline.review_observe_failed")


TEST_GAP_OBSERVE_FILE = APPROVALS_HOME / "test-gap-observe.jsonl"
TEST_GAP_NOTE = (
    "[discipline] 지난 턴에 구현 파일 {n}개({files})가 바뀌었고 테스트 파일은 바뀌지 않았습니다. "
    "테스트가 필요한 변경이면 추가하고, 필요 없으면 보고에 그 이유를 적으세요. "
    "(알림이며 차단이 아닙니다.)"
)
TEST_GAP_NOTE_CHARS = 300


def _repo_place(path: str, tops: list[str]) -> tuple[str, str]:
    """(the innermost repository of `tops` that holds `path` or "", the path inside it)."""
    holding = [top for top in tops if _inside(path, top)]
    if not holding:
        return "", path
    top = max(holding, key=len)
    return os.path.normcase(top), os.path.relpath(path, top)


def is_test_file(path: str, tops: list[str]) -> bool:
    """A code file at a test path, judged by its place inside its own repository (a
    repository below some `tests` folder is not all tests; `latest.md` is no test)."""
    if Path(path).suffix.lower() not in CODE_EXT:
        return False
    return bool(
        TEST_NAME.search(Path(path).name) or TEST_DIR.search(_repo_place(path, tops)[1])
    )


TEST_DIR = re.compile(r"(^|[\\/])(tests?|__tests__|spec)[\\/]", re.IGNORECASE)
TEST_NAME = re.compile(
    r"^test_.+|.+(_test|\.test|\.spec)\.[a-z0-9]+$|.+[a-z0-9]Tests?\.[a-z0-9]+$"
)


def test_gap(turn_changes: object, session_files: object, tops: object) -> list[str]:
    """The implementation files a turn changed without any test file to go with them
    (P1-03): code files among `turn_changes` whose repository saw no test file changed in
    the turn or edited earlier in the session. Coarse on purpose (a refactoring is flagged
    too, one touched test file covers its whole repository): it is a note, never a reason
    to block."""
    roots = [t for t in (tops if isinstance(tops, list) else []) if isinstance(t, str)]

    def texts(value: object) -> list[str]:
        return [
            v for v in (value if isinstance(value, list) else []) if isinstance(v, str)
        ]

    changed = texts(turn_changes)
    tested = {
        _repo_place(path, roots)[0]
        for path in changed + texts(session_files)
        if is_test_file(path, roots)
    }
    return [
        path
        for path in changed
        if Path(path).suffix.lower() in CODE_EXT
        and not is_test_file(path, roots)
        and _repo_place(path, roots)[0] not in tested
    ]


def note_test_gap(state: dict[str, Any]) -> None:
    """Stop: prepare the note for the next prompt and count the turn once in the
    observation file (no path, no content). Never changes the Stop."""
    try:
        edited = [
            path
            for entry in (state.get("dirty") or {}).values()
            if isinstance(entry, dict)
            for path in entry.get("files") or []
        ]
        missing = test_gap(
            state.get("turn_changes"), edited, list(state.get("repos") or {})
        )
        if not missing:
            state["test_gap_note"] = ""
            return
        names = ", ".join(Path(path).name for path in missing[:3])
        if len(missing) > 3:
            names += " 등"
        state["test_gap_note"] = TEST_GAP_NOTE.format(n=len(missing), files=names)[
            :TEST_GAP_NOTE_CHARS
        ]
        if state.get("test_gap_turn") == state.get("turn"):
            return  # this turn is already counted (a Stop can run more than once)
        state["test_gap_turn"] = state.get("turn") or 0
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        with open(TEST_GAP_OBSERVE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "files": len(missing)}) + "\n")
    except (OSError, ValueError, TypeError, AttributeError):
        log.exception("discipline.test_gap_failed")


RISK_OBSERVE_FILE = APPROVALS_HOME / "risk-observe.jsonl"
RISK_NOTE = (
    "[discipline] 지난 턴의 변경은 위험 점수 {score}입니다({parts}; 안내 기준 {at}). "
    "승인된 계획에 제목이나 표로 된 대안 비교가 없습니다. 되돌리기 어려운 변경이면 "
    "대안 2~3개와 trade-off를 계획에 적고 진행하세요. (알림이며 차단이 아닙니다.)"
)
RISK_NOTE_NO_PLAN = (
    "[discipline] 지난 턴의 변경은 위험 점수 {score}입니다({parts}; 안내 기준 {at}). "
    "승인된 계획 없이 한 변경입니다. 되돌리기 어려운 변경이면 /plan으로 대안 2~3개와 "
    "trade-off를 적고 승인받으세요. (알림이며 차단이 아닙니다.)"
)
RISK_NOTE_NO_TEXT = (
    "[discipline] 지난 턴의 변경은 위험 점수 {score}입니다({parts}; 안내 기준 {at}). "
    "승인된 작업의 계획 글을 docs/plans에서 찾지 못해 대안 비교를 확인하지 못했습니다. "
    "되돌리기 어려운 변경이면 대안 2~3개와 trade-off를 계획에 적고 진행하세요. "
    "(알림이며 차단이 아닙니다.)"
)
RISK_NOTE_CHARS = 300


def _approved_plan_texts(state: dict[str, Any], held: object = None) -> list[str]:
    """The plan files of the task the current approval is for (read at a Stop, only when a
    score is high). Reading them gives nothing but the answer to "does one compare
    alternatives". `held` is the approval the turn worked under as the Stop found it,
    before its re-check: the Stop of the turn that closes a plan as done has cleared the
    approval by now, and its plan is still the one to judge by."""
    approval = state.get("approval")
    if not isinstance(approval, dict):
        approval = held
    if not isinstance(approval, dict):
        return []
    base, task = approval.get("base"), approval.get("task_id")
    if not isinstance(base, str) or not isinstance(task, str):
        return []
    plans_dir = Path(base) / "docs" / "plans"
    texts: list[str] = []
    for plan in sorted(plans_dir.glob("*.md")) if plans_dir.is_dir() else []:
        try:
            text = plan.read_text(encoding="utf-8")
        except (OSError, ValueError):  # unreadable or not UTF-8: not a plan to judge by
            continue
        if plan_task(text) == task:
            texts.append(text)
    return texts


def note_risk(state: dict[str, Any], held: object = None) -> None:
    """Stop: score what the turn changed (P2-01), count it once in the observation file
    (numbers and kinds, no path) and prepare the note for the next prompt when the score is
    high and no plan of the approved task compares alternatives (P2-03). Never changes the
    Stop. CLAUDE_RISK_SCORE=0 switches all of it off.

    The line count is coarse: lines the Edit/Write tools changed during the session in the
    turn's code files (CODE_EXT); nothing for other files (workflows, lock files, settings,
    documents) and nothing for shell edits. A Stop that was blocked is not scored again
    when the turn goes on, and a note is not withdrawn when a plan gets its comparison
    afterwards."""
    if os.environ.get("CLAUDE_RISK_SCORE") == "0":
        return
    try:
        import risk

        tops = [t for t in (state.get("repos") or {}) if isinstance(t, str)]
        changed = [p for p in (state.get("turn_changes") or []) if isinstance(p, str)]
        if not changed:
            state["risk_note"] = ""
            return
        edited: dict[str, int] = {}
        for entry in (state.get("dirty") or {}).values():
            counts = entry.get("lines") if isinstance(entry, dict) else None
            for path, n in (counts if isinstance(counts, dict) else {}).items():
                if isinstance(path, str) and isinstance(n, int):
                    edited[_norm(path)] = n
        places = [_repo_place(path, tops) for path in changed]
        found = risk.assess(
            [
                risk.kind(
                    rel
                    if top
                    else Path(path).name,  # outside every repository: its name
                    is_test_file(path, tops),
                    Path(path).suffix.lower() in CODE_EXT,
                )
                for path, (top, rel) in zip(changed, places, strict=True)
            ],
            sum(edited.get(_norm(path), 0) for path in changed),
            len({top for top, _rel in places}),
        )
        at = risk.threshold(os.environ.get("CLAUDE_RISK_ADVICE_AT"))
        # plan files are read only for a score that is high enough to ask
        plans = (
            _approved_plan_texts(state, held)
            if risk.advice_needed(found.score, at, [])
            else []
        )
        advised = risk.advice_needed(found.score, at, plans)
        parts = ", ".join(f"{k} {n}" for k, n in sorted(found.kinds.items()))
        approved = isinstance(state.get("approval"), dict) or isinstance(held, dict)
        wording = (
            RISK_NOTE if plans else RISK_NOTE_NO_TEXT if approved else RISK_NOTE_NO_PLAN
        )
        state["risk_note"] = (
            wording.format(score=found.score, parts=parts, at=at)[:RISK_NOTE_CHARS]
            if advised
            else ""
        )
        if state.get("risk_turn") == state.get("turn"):
            return  # this turn is already counted
        state["risk_turn"] = state.get("turn") or 0
        row = {
            "at": time.time(),
            "score": found.score,
            "kinds": found.kinds,
            "files": found.files,
            "lines": found.lines,
            "repos": found.repos,
            "advised": advised,
        }
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        with open(RISK_OBSERVE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:  # hook boundary: an observation, the Stop is decided without it
        log.exception("discipline.risk_failed")


def ledger_capped(state: dict[str, Any], base: str, closed_note: object = None) -> bool:
    """Whether something besides the per-project content check keeps a Stop from accepting
    the task under `base` as verified: the ledger must never say more than the Stop does.
    The review gate is not part of this: a missing review is noted in the entry's own
    `review` field, next to the verification state it does not change."""

    def touches(place: object) -> bool:
        return isinstance(place, str) and (_inside(place, base) or _inside(base, place))

    warning = state.get("approval_warning")
    # closed_note: the notice that this very task's plan was closed as done (the ordinary
    # end of a task) is not a reason to doubt its verification; any other warning is
    if state_reset_active(state) is not None or (warning and warning != closed_note):
        return True
    for key in (
        "content_unknown",
        "content_too_large",
        "content_late",
        "content_removed",
    ):
        if any(touches(place) for place in state.get(key) or []):
            return True
    tracked = set(state.get("content_tracked_roots") or [])
    for root, entry in (state.get("dirty") or {}).items():
        edited_at = _number(entry.get("at")) or 0.0
        if edited_at <= 0 or root in tracked or not touches(root):
            continue  # decided by content (root_state), or not this task's
        ev = (state.get("evidence") or {}).get(root) or {}
        if (_number(ev.get("at")) or 0.0) < edited_at or ev.get("outcome") != "passed":
            return True
    return False


def record_ledger(data: dict[str, Any], state: dict[str, Any]) -> None:
    """P1-01: note in the repository's task ledger how far the approved task is, from what
    refresh_content just computed. A record only: nothing here or elsewhere reads it to
    decide, and a ledger that cannot be written changes nothing about this Stop."""
    # the approval this prompt works under (one of an earlier prompt names no task now)
    live = active_approval(state, state.get("stop_prompt_id"))
    held = state.get("ledger_closed")
    held = held if isinstance(held, dict) else None
    if live and held and held.get("approval_id") == live.get("approval_id"):
        live = None  # this very task was just closed as done: recorded as closed below
    elif live and held and held.get("task_id") == live.get("task_id"):
        # the same task approved anew (its plan was opened again): it is live, not closed,
        # and two approvals of one task would write over each other at every Stop
        state["ledger_closed"] = held = None
    if held is not None:
        # The task this session closed as done, while the content it finished on is still
        # what the repository holds: its verification may still advance (a CI result read
        # after the close), also when another task has been approved meanwhile.
        if not held.pop("pin", False) and not held.get("content"):
            # its content was never pinned at the close: what the repository holds now can
            # no longer be told from later work
            state["ledger_closed"] = None
        else:
            _record_task(data, state, held, held)
    if live:
        _record_task(data, state, live, None)


def _record_task(
    data: dict[str, Any],
    state: dict[str, Any],
    ptr: dict[str, Any],
    closed: dict[str, Any] | None,
) -> None:
    """One ledger entry for the task of `ptr`: a live approval, or (`closed`, the same
    object) the remembered task this session closed as done. A closed task is recorded
    only on the content pinned at its close; any other content ends its memory."""
    if not ptr.get("task_id") or not ptr.get("base"):
        return
    import ledger

    base = str(ptr["base"])
    states: list[str] = []
    reviews: list[str] = []
    digests: dict[str, str] = {}
    for root, entry in sorted(state.get("content", {}).items()):
        tops = entry.get("digests") or {}
        if not any(_inside(top, base) for top in tops):
            continue  # another repository's project: not this task's
        states.append(
            ledger.root_state(
                entry,
                state.get("evidence", {}).get(root),
                state.get("pushes", {}),
                state.get("ci", {}),
            )
        )
        reviews.append(review_state(state, root))
        # by root and repository: two policy roots of one repository have one digest each
        digests.update({f"{root}|{top}": digest for top, digest in tops.items()})
    reached = ledger.task_state(states)
    content = ledger.content_id(digests)
    if closed is not None:
        if reached is None or closed.get("content") not in (None, content):
            state["ledger_closed"] = None  # changed since the close: not this task
            return
        closed["content"] = content
    if reached is None:
        return  # nothing of this task changed in this session
    if reached in (ledger.LOCALLY_VERIFIED, ledger.CI_VERIFIED) and ledger_capped(
        state, base, closed.get("note") if closed else None
    ):
        reached = ledger.IMPLEMENTED
    ledger.record(
        base,
        {
            "task": str(ptr["task_id"]),
            "approval": str(ptr.get("approval_id") or ""),
            "state": reached,
            "content": content,
            "roots": len(states),
            "session": str(data.get("session_id") or ""),
            "review": min(reviews, key=REVIEW_ORDER.index),
        },
        time.time(),
    )


def on_stop(data: dict[str, Any], state: dict[str, Any]) -> None:
    if data.get("stop_hook_active"):
        return
    import worktree

    state["stop_prompt_id"] = data.get("prompt_id") or state.get("prompt_id")
    worked_under = active_approval(state, state["stop_prompt_id"])
    cover_valid(data, state, worked_under)
    if v2_active():
        try:
            refused = v2_write_check(state, str(data.get("cwd") or os.getcwd()))
        except Exception as e:  # hook boundary: block with the failure, never crash
            log.exception("approvals.v2_hook_failure")
            refused, state["v2_block"] = v2_failure(e), v2_failure(e)
        if refused:
            # A revoked/invalid approval (or an untrusted store) covers nothing this turn did.
            state["approval"] = None
            state["approval_warning"] = state.get("approval_warning") or state.get(
                "v2_block"
            )
    track_repo(state, str(data.get("cwd") or os.getcwd()))
    refresh_content(state)
    if worktree.budget_exhausted:
        # git could not answer in time: what changed is unknown, which never counts as unchanged.
        marker = str(data.get("cwd") or os.getcwd())
        if marker not in state["content_unknown"]:
            state["content_unknown"].append(marker)
    try:
        record_ledger(data, state)
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        ImportError,
        RecursionError,
    ):
        # a record, not a gate: this Stop is decided the same without it
        log.exception("discipline.ledger_failed")
    observe_reviews(state)
    note_test_gap(state)
    note_risk(state, worked_under)
    note_ci_failures(state, data.get("session_id"))
    damaged = state_reset_active(state)
    decision = _stop_decision(state)
    if decision is None:
        return
    reason, notify_key = decision
    if notify_key:
        state.setdefault("notified", {})[notify_key] = state.get("turn")
    reset = state.get("state_reset")
    if (
        damaged is not None
        and reason.startswith(REASON_STATE_RESET.format(why=damaged))
        and isinstance(reset, dict)
        and not reset.get("roots")
    ):
        state.pop(
            "state_reset", None
        )  # no gate to wait for: reported once, as '미확인'
    emit_stop_block(reason)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (
        ValueError,
        OSError,
    ):  # malformed/empty payload: never block on hook input problems
        sys.exit(0)
    session = session_key(data)
    handler = {
        "pre": on_pre,
        "post": on_post,
        "fail": on_fail,
        "prompt": on_prompt,
        "stop": on_stop,
        "agent-start": on_agent_start,
        "agent-stop": on_agent_stop,
    }.get(mode)
    if handler is None:
        sys.exit(0)
    try:
        run_hook(mode, data, handler, STATE_DIR / f"{session}.json")
    except Exception:  # outermost boundary: even the failure handling failed
        log.exception("discipline.hook_boundary_failure")
        hard_block(mode, data, REASON_HOOK_ERROR.format(why="오류 처리 중 오류"))


PROMPT_FAILED_PREFIX = "프롬프트 hook 처리 중 오류로 승인을 폐기함"
PROMPT_FAILED_WARNING = PROMPT_FAILED_PREFIX + "({why})"
PROMPT_FAILED_UNSAVED = (
    " 승인 폐기를 기록하지 못했습니다(세션 상태를 읽거나 쓰지 못함). 승인 상태를 신뢰할 수 "
    "없으니, 편집하기 전에 사용자에게 프롬프트 첫 줄 task:<id> 를 요청하세요."
)
PROMPT_FAILED_NOTE = (
    "[discipline] 프롬프트 hook 처리 중 오류({why}). 이 프롬프트가 승인을 이을 수 있는지 판정하지 "
    "못했습니다.{dropped} 편집하기 전에 이 사실을 사용자에게 알리세요."
)
PROMPT_FAILED_DROPPED = (
    " 승인(task: {task})을 폐기했습니다. 계속하려면 사용자에게 프롬프트 첫 줄 task:{task} 를 "
    "요청하세요. 그 전에는 범위 안의 파일도 고치지 마세요."
)
PROMPT_BUSY_NOTE = (
    "[discipline] 이 프롬프트를 hook이 처리하지 못했습니다(세션 상태 잠금: {why}). 승인 상태를 "
    "확인할 수 없으니, 편집하기 전에 사용자에게 프롬프트 첫 줄 task:<id> 를 요청하세요."
)
# What the failure path of this run tells the prompt: set by note_prompt_failure ("task":
# the task it dropped, "" for none) and by run_locked ("unsaved": the state with the dropped
# approval did not reach the disk), read by run_hook.
_prompt_failure: dict[str, str] = {}
_prompt_pending: list[str] = []  # what on_prompt has to tell: printed once, by run_hook
_prompt_told = (
    False  # emit_prompt_context ran: a prompt hook's output is one JSON object
)


def prompt_failed(state: dict[str, Any], data: dict[str, Any], why: str) -> str | None:
    """A prompt handler ended in an exception: it decided nothing, so the approval it should
    have decided on does not stay (fail-closed), and the prompt it failed on becomes the
    prompt in progress (a later one is not read as its continuation). Everything else is
    left as it is. Returns the task whose approval was dropped, or None."""
    held = state.get("approval")
    task = held.get("task_id") if isinstance(held, dict) else None
    state["approval"] = None
    state["prompt_id"] = data.get("prompt_id")
    if held:
        state["approval_warning"] = PROMPT_FAILED_WARNING.format(why=why)
    return task if isinstance(task, str) and task else None


def note_prompt_failure(state: dict[str, Any], data: dict[str, Any], why: str) -> None:
    """prompt_failed on the failure path: never raises (the handler's own exception is the
    one to report), and keeps what run_hook tells the prompt."""
    try:
        _prompt_failure["task"] = prompt_failed(state, data, why) or ""
    except Exception:  # hook boundary: the failure being handled is reported, not this
        log.exception("discipline.prompt_failed_failed")
        state["approval"] = None
        _prompt_failure["task"] = ""


def tell_prompt_failure(text: str) -> None:
    """Tell the failed prompt, once, together with what on_prompt already had to tell, and
    never raise on the failure path."""
    if _prompt_told:
        return
    try:
        emit_prompt_context("\n\n".join([*_prompt_pending, text]))
    except Exception:  # hook boundary: nothing more can be said
        log.exception("discipline.prompt_failure_output_failed")


def prompt_failure_text(why: str) -> str:
    """What a prompt whose hook ended in an exception is told: whether an approval was
    dropped, or that dropping it could not be recorded."""
    task = _prompt_failure.get("task")
    if "task" not in _prompt_failure or _prompt_failure.get("unsaved"):
        dropped = (
            PROMPT_FAILED_UNSAVED  # failed before the state was read, or not saved
        )
    elif task:
        dropped = PROMPT_FAILED_DROPPED.format(task=task)
    else:
        dropped = ""
    return PROMPT_FAILED_NOTE.format(why=why, dropped=dropped)


def run_hook(mode: str, data: dict[str, Any], handler: Any, path: Path) -> None:
    import session_store

    try:
        configured = float(
            os.environ.get("CLAUDE_DISCIPLINE_LOCK_BUDGET_S")
            or session_store.LOCK_BUDGET_S
        )
    except ValueError:  # malformed override: use the default
        configured = session_store.LOCK_BUDGET_S
    if mode == "prompt":
        # outside the lock: the stop record this may wait for is written under it
        _handback_wait.update(await_agent_stop(data, path) or {})
    lock_budget = lock_budget_s(mode, time.monotonic() - _T0, configured)
    try:
        # One exclusive lock per session for the whole run: main and subagent hooks of the
        # session are serialized, so no update is lost (S).
        with session_store.locked(path, lock_budget):
            if recovery_requested(mode, data):
                recover_locked(data, path)
            else:
                run_locked(mode, data, handler, path)
        if _prompt_pending and not _prompt_told:
            emit_prompt_context("\n\n".join(_prompt_pending))
    except session_store.StateBusy as e:
        if mode in LIFECYCLE_MODES:
            # No message text: it is not ASCII, and stderr here is not UTF-8.
            log.warning("discipline.lifecycle_event_dropped mode=%s", mode)
            return
        # Nothing can be recorded: refuse what needs the state, remember that it was missed.
        note_safely(path, f"{event_label(mode, data)}: {e}")
        if mode == "prompt":
            # a prompt cannot be refused and its approval cannot be changed without the
            # state: it is at least not silent
            tell_prompt_failure(PROMPT_BUSY_NOTE.format(why=str(e)[:200]))
        fail_closed(mode, data, REASON_STATE_BUSY.format(why=e))
    except (
        Exception
    ) as e:  # hook boundary: an exit 1 would let a pre through (fail-open)
        log.exception("discipline.hook_failure")
        why = f"{type(e).__name__}: {e}"[:200]
        # What was known is saved (run_locked's finally); this event may be half applied.
        note_safely(path, f"{event_label(mode, data)}: 처리 중 오류({why})")
        if mode == "prompt":
            tell_prompt_failure(prompt_failure_text(why))
        fail_closed(mode, data, REASON_HOOK_ERROR.format(why=why))


def note_safely(path: Path, why: str) -> None:
    """Remember a missed event; never raises (it runs on the failure path)."""
    try:
        import session_store

        session_store.note_missed(path, why)
    except Exception:  # the refusal that follows must still happen
        log.exception("discipline.note_missed_failed")


def blocks(mode: str, data: dict[str, Any]) -> bool:
    return mode == "pre" or (mode == "stop" and not data.get("stop_hook_active"))


def fail_closed(mode: str, data: dict[str, Any], reason: str) -> None:
    """Refuse what needs a trustworthy state: the write (pre) and the end of the turn (stop).
    If the refusal cannot be written, exit 2 (blocking for both events per the hook docs)."""
    if not blocks(mode, data):
        return
    try:
        if mode == "pre":
            emit_pre("deny", reason)
        emit_stop_block(reason)
    except Exception:  # SystemExit from emit_pre passes through untouched
        log.exception("discipline.refusal_output_failed")
        hard_block(mode, data, reason)


def hard_block(mode: str, data: dict[str, Any], reason: str) -> None:
    """Last resort: exit 2 with the reason on stderr for pre/stop, without flushing stdout
    (a failing flush at interpreter exit would turn the status into 120: non-blocking)."""
    if not blocks(mode, data):
        sys.exit(0)
    try:
        sys.stderr.write(reason + "\n")
        sys.stderr.flush()
    except (
        OSError,
        ValueError,
    ):  # nothing can be reported; the exit status still blocks
        log.warning("discipline.hard_block_stderr_failed")
    os._exit(2)


# ---------------------------------------------------------------- user state recovery
RECOVER_CMD = "recover-state"
REASON_RECOVERED = (
    "[discipline] 사용자 요청(recover-state)으로 세션 추적 상태를 복구했습니다. 원본은 {kept}로 보존했고 "
    "감사 기록은 {audit}에 있습니다. 미검증 범위 {roots}는 '세션 추적 상태 손상 또는 누락' 표식으로 남아, 그 게이트가 "
    "통과할 때까지 완료(Stop)를 막습니다. 승인은 0입니다. 작업을 이어가려면 사용자가 프롬프트 첫 줄에 task:<id>를 적어 "
    "재무장해야 합니다. 이 사실을 사용자에게 알리세요."
)
REASON_RECOVERY_FAILED = (
    "[discipline] 세션 추적 상태 복구(recover-state) 실패: {why}. 상태는 바뀌지 않았고 보호 대상 쓰기 차단도 그대로입니다. "
    "상태 파일을 직접 지우거나 고치지 말고 이 사실을 사용자에게 알리세요."
)


def recovery_requested(mode: str, data: dict[str, Any]) -> bool:
    """Only the user's own first line (never a relay, markup or a later line) asks for it."""
    prompt = data.get("prompt")
    if mode != "prompt" or not isinstance(prompt, str) or relay_shaped(prompt):
        return False
    return naming_text(prompt).strip().lower() == RECOVER_CMD


def audit_recovery(row: dict[str, Any]) -> bool:
    """Append one line to the recovery audit log (outside %TEMP%); False if it failed."""
    try:
        APPROVALS_HOME.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"at": time.time(), **row}, ensure_ascii=False) + "\n"
        with open(RECOVERY_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except (OSError, ValueError, TypeError):
        log.exception("discipline.recovery_audit_failed")
        return False
    return True


# ---------------------------------------------------------------- user acknowledgement
# `ack-missing <path>` on the user's own first line releases what no gate can ever clear: a
# tracked repository that is gone, or a reset-marker root that is gone or has no gate. It
# grants nothing and verifies nothing (no evidence is written); a repository that appears at
# that path later starts with a late baseline.
ACK_CMD = re.compile(r"^ack-missing\s+(\S.*)$", re.IGNORECASE)
MAX_ACKED = 50
REASON_ACKED = (
    "[discipline] 사용자 확인(ack-missing)으로 {path}의 미확인 표식을 해제했습니다({what}). 검증된 것이 아닙니다: "
    "그 안의 변경은 보고에 '미확인'으로 남기세요. 감사 기록은 {audit}에 있습니다."
)
REASON_ACK_REFUSED = "[discipline] ack-missing 거부: {why}. 상태는 바뀌지 않았습니다. 이 사실을 사용자에게 알리세요."


def ack_requested(data: dict[str, Any]) -> str | None:
    """The path named by `ack-missing <path>`: only on the user's own first line (never a
    relay, markup or a later line)."""
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or relay_shaped(prompt):
        return None
    asked = ACK_CMD.match(naming_text(prompt).strip())
    if not asked:
        return None
    return asked.group(1).strip().strip("\"'").strip() or None


def same_place(a: object, b: str) -> bool:
    return isinstance(a, str) and os.path.normcase(
        os.path.abspath(a)
    ) == os.path.normcase(os.path.abspath(b))


def gate_can_clear(root: str) -> bool:
    """The root exists and declares a gate, so passing that gate is how it is cleared."""
    from verification import find_policy

    if not os.path.isdir(root):
        return False
    try:
        policy = find_policy(os.path.join(root, "_"))
    except (OSError, ValueError):  # unreadable policy: no gate will ever be recorded
        return False
    return bool(policy["gate"]) and same_place(policy["root"], root)


def acked_before(state: dict[str, Any], top: str) -> bool:
    acked = state.get("acked_missing")
    return any(
        isinstance(row, dict) and same_place(row.get("path"), top)
        for row in (acked if isinstance(acked, list) else [])
    )


def ack_missing(data: dict[str, Any], state: dict[str, Any]) -> str | None:
    """Apply the user's `ack-missing <path>`; the text to tell Claude, or None if not asked."""
    asked = ack_requested(data)
    if asked is None:
        return None
    target = absolute(asked, str(data.get("cwd") or os.getcwd()))
    # Everything below edits these records in place: a malformed one is refused here, before
    # the audit line, so a release is never half applied.
    for key, kind in (
        ("content_removed", list),
        ("repos_removed", dict),
        ("repo_state", dict),
        ("dirty", dict),
        ("acked_missing", list),
    ):
        if key in state and not isinstance(state[key], kind):
            return REASON_ACK_REFUSED.format(
                why=f"세션 추적 상태의 {key} 형식이 올바르지 않습니다(recover-state로 먼저 복구)"
            )
    removed = [t for t in state.get("content_removed", []) if same_place(t, target)]
    kept = [t for t in state.get("repos_removed", {}) if same_place(t, target)]
    marker = state.get("state_reset") if state_reset_active(state) else None
    roots = marker.get("roots") if isinstance(marker, dict) else None
    in_marker = [r for r in (roots or []) if same_place(r, target)]
    if not (removed or kept or in_marker):
        return REASON_ACK_REFUSED.format(
            why=f"{target}는 사라진 저장소나 미확인 표식 범위로 기록된 경로가 아닙니다(정확한 경로만 받습니다)"
        )
    if in_marker and gate_can_clear(target):
        if not (removed or kept):
            return REASON_ACK_REFUSED.format(
                why=f"{target}는 존재하고 게이트가 선언돼 있어 게이트 통과로만 해제됩니다"
            )
        in_marker = []  # the missing-repository entry goes, the gated root stays
    if (removed or kept) and is_repo_root(target):
        return REASON_ACK_REFUSED.format(
            why=f"{target}에 저장소가 다시 있습니다. 원래 기준선과 비교해 검증(게이트)으로 해제됩니다"
        )
    what = [
        name
        for name, found in (
            ("content_removed", removed),
            ("repos_removed", kept),
            ("state_reset.roots", in_marker),
        )
        if found
    ]
    gone_files = [
        f
        for entry in state.get("dirty", {}).values()
        for f in (entry.get("files") or [] if isinstance(entry, dict) else [])
        if isinstance(f, str) and _inside(f, target) and not os.path.exists(f)
    ]
    if not audit_recovery(
        {
            "kind": "ack-missing",
            "session": data.get("session_id"),
            "prompt_id": data.get("prompt_id"),
            "path": target,
            "released": what,
            "owners": [
                who
                for t, names in (state.get("owners") or {}).items()
                if same_place(t, target) and isinstance(names, list)
                for who in names
                if isinstance(who, str)
            ],
            "edited_files_gone": len(gone_files),
        }
    ):
        return REASON_ACK_REFUSED.format(
            why="감사 기록을 남기지 못했습니다(감사 없는 해제 금지)"
        )
    for t in removed:
        state["content_removed"].remove(t)
    for t in kept:
        state["repos_removed"].pop(t, None)
        state.get("repo_state", {}).pop(t, None)
        forget_dir_tops(state, t)
    owners = state.get("owners")
    # Only with the repository's own records: releasing a marker root alone leaves a
    # repository that may still be tracked, and its owners with it.
    if isinstance(owners, dict) and (removed or kept):
        for t in [t for t in owners if same_place(t, target)]:
            owners.pop(t)
    if in_marker and isinstance(marker, dict):
        marker["roots"] = [r for r in roots or [] if not same_place(r, target)]
    # Edits recorded for files that went with it can never be verified: they are part of what
    # the user acknowledged. Files that still exist keep demanding verification.
    # Only entries that listed such a file change. An entry whose list was cut at the limit may
    # hold edits it does not list: it is left whole (shrinking it would hide that it was cut,
    # and a later release could then drop it), so only its gate clears it.
    for root in list(state.get("dirty", {})):
        entry = state["dirty"][root]
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, list) or not any(f in gone_files for f in files):
            continue
        if len(files) >= MAX_DIRTY_FILES:
            continue
        entry["files"] = [f for f in files if f not in gone_files]
        if isinstance(entry.get("lines"), dict):
            for f in gone_files:
                entry["lines"].pop(f, None)
        if not entry["files"]:
            state["dirty"].pop(root)
    acked = state.setdefault("acked_missing", [])
    acked.append(
        {"path": target, "at": time.time(), "prompt_id": data.get("prompt_id")}
    )
    del acked[:-MAX_ACKED]
    return REASON_ACKED.format(
        path=target, what=", ".join(what), audit=RECOVERY_AUDIT_FILE
    )


RECOVERY_PLACE_LISTS = (
    "background_tops",
    "touched_tops",
    "missed_dirs",
    "turn_shell_tops",
    "content_removed",
    "content_unknown",
    "content_too_large",
    "content_late",
)
RECOVERY_PLACE_MAPS = ("dirty", "content", "repos", "repo_state", "repos_removed")


def recovery_roots(saved: object, cwd: str) -> tuple[set[str], list[str]]:
    """Every place the old state knew, as a root of the new marker. Only path strings are
    read (list items and dict keys); no structured value is trusted or carried, so nothing
    malformed can survive a recovery. A place with a gated policy becomes that policy root
    (its gate must pass again on the current content); any other place - no gate, removed,
    unreadable policy - stays as an unresolvable root, which only the user can release."""
    from verification import find_policy

    if not isinstance(saved, dict):
        return {unresolved_root(cwd)}, [
            "원본을 읽을 수 없음(작업 위치를 미확인 범위로 둠)"
        ]
    places: set[str] = set()
    for key in RECOVERY_PLACE_LISTS:
        value = saved.get(key)
        if isinstance(value, list):
            places |= {v for v in value if isinstance(v, str) and v}
    for key in RECOVERY_PLACE_MAPS:
        value = saved.get(key)
        if isinstance(value, dict):
            places |= {k for k in value if isinstance(k, str) and k}
            if "" in value:  # edits outside any declared policy
                places.add(cwd)
    roots: set[str] = set()
    reset = saved.get("state_reset")
    if isinstance(reset, dict) and isinstance(reset.get("roots"), list):
        roots |= {r for r in reset["roots"] if isinstance(r, str) and r}
    problems: list[str] = []
    for place in sorted(places):
        try:
            policy = find_policy(os.path.join(place, "_"))
        except (OSError, ValueError) as e:
            roots.add(unresolved_root(place))
            problems.append(f"범위 확인 실패({type(e).__name__}): {place}")
            continue
        if policy["root"] and policy["gate"] and os.path.isdir(place):
            roots.add(policy["root"])
        else:
            roots.add(unresolved_root(place))
    scope, scope_problems = reset_scope(None, cwd)  # this event's own project
    return roots | set(scope), problems + scope_problems


def finite_time(value: object) -> float | None:
    """A JSON number usable as a time, or None (also for NaN, infinities and integers too
    large for a float)."""
    import math

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def recovery_carry(saved: object) -> dict[str, Any]:
    """The flat session-only safety records, each taken only when fully well-formed:
    revocations the store could not record yet and the approval-store rollback witness."""
    if not isinstance(saved, dict):
        return {}
    carry: dict[str, Any] = {}
    pending = saved.get("pending_revocations")
    if isinstance(pending, dict):
        kept = {
            k: v
            for k, v in pending.items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if kept:
            carry["pending_revocations"] = kept
    gen = saved.get("approvals_seen_gen")
    if isinstance(gen, int) and not isinstance(gen, bool) and gen > 0:
        carry["approvals_seen_gen"] = gen
    # Paths the user acknowledged as missing: a repository appearing there later still gets a
    # late baseline. Rebuilt from well-formed rows only (path and time, nothing else).
    acked = saved.get("acked_missing")
    rows = [
        {"path": row["path"], "at": at}
        for row in (acked if isinstance(acked, list) else [])
        if isinstance(row, dict)
        and isinstance(row.get("path"), str)
        and row["path"]
        and (at := finite_time(row.get("at"))) is not None
    ]
    if rows:
        carry["acked_missing"] = rows[-MAX_ACKED:]
    # Repositories a background shell may still write to: no hook says when it ends, so the
    # record outlives a recovery (path strings only).
    late_writers = saved.get("background_tops")
    places = [
        t
        for t in (late_writers if isinstance(late_writers, list) else [])
        if isinstance(t, str) and t
    ]
    if places:
        carry["background_tops"] = places
    return carry


def recover_locked(data: dict[str, Any], path: Path) -> None:
    """The user's `recover-state`: keep a copy of the original, then replace the state
    atomically with defaults plus a reset marker over every place the old state knew.
    Nothing is granted, each step is audited, and the original stays in place until the
    new state is saved, so any failure leaves the state exactly as it was."""
    import hashlib

    import session_store

    cwd = str(data.get("cwd") or os.getcwd())
    try:
        raw: bytes | None = path.read_bytes()
    except FileNotFoundError:
        raw = None
    sha = hashlib.sha256(raw).hexdigest() if raw is not None else ""
    row = {
        "session": path.stem,
        "prompt_id": data.get("prompt_id"),
        "cwd": cwd,
        "sha256": sha,
        "size": len(raw) if raw is not None else 0,
    }
    if not audit_recovery({**row, "event": "started"}):
        emit_prompt_context(REASON_RECOVERY_FAILED.format(why="감사 기록을 쓸 수 없음"))
        return
    kept = session_store.keep_copy(path, raw, "recovered") if raw is not None else None
    if raw is not None and kept is None:
        audit_recovery({**row, "event": "failed", "why": "원본 사본을 만들 수 없음"})
        emit_prompt_context(
            REASON_RECOVERY_FAILED.format(why="원본 사본을 만들 수 없음")
        )
        return
    kept_name = kept.name if kept else "원본 없음"
    try:
        state, roots = recovered_state(data, raw, kept_name, sha, cwd)
        session_store.save(path, state)
    except Exception as e:  # the original is still in place; the boundary fails closed
        audit_recovery(
            {
                **row,
                "event": "failed",
                "why": f"{type(e).__name__}: {e}"[:200],
                "preserved": kept_name,
            }
        )
        if isinstance(e, session_store.StateWriteFailed):
            emit_prompt_context(REASON_RECOVERY_FAILED.format(why=f"저장 실패: {e}"))
            return
        raise
    audit_recovery(
        {**row, "event": "done", "preserved": kept_name, "roots": sorted(roots)}
    )
    emit_prompt_context(
        REASON_RECOVERED.format(
            kept=kept_name, audit=RECOVERY_AUDIT_FILE, roots=sorted(roots) or "없음"
        )
    )


def recovered_state(
    data: dict[str, Any], raw: bytes | None, kept_name: str, sha: str, cwd: str
) -> tuple[dict[str, Any], set[str]]:
    """The state that replaces the old one. Missed-event notes are left for the next run,
    which merges them into this marker."""
    try:
        saved: object = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except (ValueError, RecursionError):
        saved = None
    roots, problems = recovery_roots(saved, cwd)
    state = default_state()
    state.update(recovery_carry(saved))
    state["turn"] = 1
    state["prompt_id"] = data.get("prompt_id")
    state["recovered"] = {"at": time.time(), "preserved": kept_name, "sha256": sha}
    why = [
        f"사용자 복구(recover-state): 보존 {kept_name}, sha256 {sha[:12] or '-'}",
        *problems,
    ]
    state["state_reset"] = {
        "at": time.time(),
        "why": "; ".join(why)[:300],
        "roots": sorted(roots),
    }
    return state, roots


def emit_prompt_context(text: str) -> None:
    global _prompt_told
    _prompt_told = True
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": text,
                }
            }  # ASCII escapes: a piped stdout on Windows is not UTF-8
        )
    )


SEEN_EVENTS = 200


def run_locked(mode: str, data: dict[str, Any], handler: Any, path: Path) -> None:
    """Load (damage is preserved and turns into a reset marker), handle once per event, save
    atomically (a failed save is remembered as a missed event)."""
    import session_store
    import worktree

    _v2_forget()
    state = default_state()
    why: list[str] = []
    try:
        try:
            saved = session_store.load(path)
        except session_store.StateCorrupt as e:
            saved = None
            why.append(f"{e}, 보존: {e.preserved.name if e.preserved else '이동 실패'}")
        problem = state_shape_problem(saved) if saved else None
        if (
            problem
        ):  # well-formed JSON the code cannot use: as damaged as unreadable JSON
            kept = session_store.preserve(path)
            saved = None
            why.append(
                f"세션 추적 상태 손상(형식: {problem}), 보존: {kept.name if kept else '이동 실패'}"
            )
        if saved:
            state.update(saved)
    except Exception:
        # the state was not read: nothing is saved over it, so nothing can be dropped in it
        if mode == "prompt":
            _prompt_failure["unsaved"] = "1"
        raise
    # From here on every step runs under the save below: an exception anywhere (notes
    # already taken, marker half computed, handler) still persists what is known, and the
    # loaded state is never replaced by defaults.
    try:
        # The earlier note format is looked for once per session: it lists the whole
        # state folder (tens of thousands of entries), which every run used to pay for.
        # A look that found anything (also a note it could not read or remove yet) is
        # repeated by the next run: "read again next time" holds for the earlier format too.
        checked = bool(state.get("notes_checked"))
        taken = session_store.take_missed(path, legacy=not checked)
        why += taken
        state["notes_checked"] = 1 if checked or not taken else 0
        if why:
            mark_reset(state, saved, why, str(data.get("cwd") or os.getcwd()))
        # Every git call of this run shares one budget, cut to what is left before the
        # hook's deadline after the lock wait, so the hook decides (closed) instead of being
        # killed.
        budget = os.environ.get("CLAUDE_DISCIPLINE_GIT_BUDGET_S")
        try:
            seconds = float(budget) if budget else GIT_BUDGET_S[mode]
        except ValueError:  # malformed override: use the default
            seconds = GIT_BUDGET_S[mode]
        worktree.set_budget(git_budget_s(mode, time.monotonic() - _T0, seconds))
        event = f"{mode}|{data.get('tool_use_id')}" if data.get("tool_use_id") else None
        seen: list[str] = state.setdefault("seen_events", [])
        if event and mode in ("post", "fail") and event in seen:
            return  # the same tool event delivered again: already applied
        if event:
            seen.append(event)
            del seen[:-SEEN_EVENTS]
        handler(data, state)
    except Exception as e:
        # Anywhere in a prompt hook's run (the notes, the marker, the budget, the handler):
        # it did not decide whether the approval continues, so the approval goes.
        if mode == "prompt":
            note_prompt_failure(state, data, f"{type(e).__name__}: {e}"[:200])
        raise
    finally:
        try:
            session_store.save(path, state)
        except session_store.StateWriteFailed as e:
            if mode == "prompt":
                _prompt_failure["unsaved"] = "1"
            note_safely(path, f"{event_label(mode, data)}: {e}")


if __name__ == "__main__":
    main()
