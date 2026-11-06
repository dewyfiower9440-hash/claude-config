"""Windows toast notifications.

Usage (hook command argument):
  notify.py start   -> UserPromptSubmit: remember when the turn started
  notify.py stop    -> Stop: toast only if the turn ran longer than the threshold and
                       discipline.py will not block this Stop (Stop hooks run in parallel,
                       so the same pure decision is evaluated here from the shared state)
  notify.py attn    -> Notification: toast immediately (permission prompt / idle input)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType

LONG_TURN_SECONDS = int(os.environ.get("CLAUDE_NOTIFY_MIN_SECONDS", "60"))


def load_discipline() -> ModuleType:
    """Import the sibling discipline.py (python -I drops the script dir from sys.path)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import discipline

    return discipline


def should_toast_on_stop(block_reason: str | None, elapsed: float | None) -> bool:
    """Toast only when the turn really ends (not blocked) and it ran long enough."""
    if block_reason is not None:
        return False  # discipline makes Claude continue; the final Stop will toast
    return elapsed is not None and elapsed >= LONG_TURN_SECONDS


def toast(title: str, body: str) -> None:
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("'", "''")[:200]
        )

    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual></toast>')
$appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""
    import subprocess  # lazy: only toasts need it

    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            ps,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "attn"
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, OSError):  # malformed payload: notify with defaults
        data = {}
    discipline = load_discipline()
    session = str(data.get("session_id", "default"))
    project = Path(data.get("cwd") or os.getcwd()).name or "Claude"
    state_dir = discipline.temp_root() / "claude-notify"
    state_dir.mkdir(exist_ok=True)
    stamp = state_dir / f"{session}.start"

    if mode == "start":
        stamp.write_text(str(time.time()))
    elif mode == "stop":
        reason = discipline.stop_block_reason(
            discipline.load_state(discipline.session_key(data)),
            bool(data.get("stop_hook_active")),
        )
        try:
            elapsed: float | None = time.time() - float(stamp.read_text())
        except (OSError, ValueError):  # no start stamp: turn length unknown
            elapsed = None
        if should_toast_on_stop(reason, elapsed) and elapsed is not None:
            toast(
                f"Claude Code - {project}",
                f"작업 완료 ({int(elapsed // 60)}분 {int(elapsed % 60)}초)",
            )
    else:
        toast(
            f"Claude Code - {project}",
            data.get("message") or "입력이나 승인이 필요합니다",
        )


if __name__ == "__main__":
    main()
