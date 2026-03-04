"""Persistent reminders — survive restarts by storing in Drive/state/reminders.json.

Tools:
  reminder_set     — create a reminder (fires at a specific UTC time)
  reminder_list    — list pending reminders
  reminder_delete  — cancel a reminder by ID
  reminder_check   — check due reminders and fire them (called by background consciousness)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import utc_now_iso

log = logging.getLogger(__name__)

_REMINDERS_FILE = "state/reminders.json"
_LOCK_FILE = "state/reminders.lock"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load(ctx: ToolContext) -> List[Dict]:
    path = ctx.drive_root / _REMINDERS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to load reminders: %s", e)
        return []


def _save(ctx: ToolContext, reminders: List[Dict]) -> None:
    path = ctx.drive_root / _REMINDERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(reminders, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse ISO 8601 UTC timestamp, return aware datetime or None."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _reminder_set(
    ctx: ToolContext,
    fire_at_utc: str,
    text: str,
    id: str = "",
    repeat_daily: bool = False,
) -> str:
    """Schedule a reminder that fires at a specific UTC time.

    Args:
        fire_at_utc:   ISO 8601 UTC datetime, e.g. "2026-02-28T19:00:00Z"
        text:          Message to send to the owner when the reminder fires.
        id:            Optional stable ID (useful for overwriting existing reminders).
                       If omitted, a random ID is generated.
        repeat_daily:  If True, the reminder is automatically rescheduled every
                       24 hours after it fires.
    """
    dt = _parse_iso(fire_at_utc)
    if dt is None:
        return f"⚠️ Cannot parse fire_at_utc='{fire_at_utc}'. Use ISO 8601 UTC, e.g. '2026-02-28T19:00:00Z'."

    now = datetime.now(tz=timezone.utc)
    if dt <= now:
        return f"⚠️ fire_at_utc '{fire_at_utc}' is in the past (now={utc_now_iso()}). Reminder not saved."

    rid = id.strip() or uuid.uuid4().hex[:8]
    reminders = _load(ctx)

    # Overwrite if same ID exists
    reminders = [r for r in reminders if r.get("id") != rid]
    reminders.append({
        "id": rid,
        "fire_at_utc": dt.isoformat(),
        "text": text,
        "repeat_daily": repeat_daily,
        "created_at": utc_now_iso(),
    })
    _save(ctx, reminders)

    delta_sec = int((dt - now).total_seconds())
    delta_human = _fmt_delta(delta_sec)
    return f"OK: reminder '{rid}' saved — fires in {delta_human} (at {dt.strftime('%Y-%m-%d %H:%M UTC')})."


def _reminder_list(ctx: ToolContext) -> str:
    """List all pending reminders."""
    reminders = _load(ctx)
    if not reminders:
        return "No pending reminders."
    now = datetime.now(tz=timezone.utc)
    lines = []
    for r in sorted(reminders, key=lambda x: x.get("fire_at_utc", "")):
        dt = _parse_iso(r.get("fire_at_utc", ""))
        if dt:
            delta = int((dt - now).total_seconds())
            when = f"in {_fmt_delta(delta)}" if delta > 0 else "OVERDUE"
        else:
            when = "unknown time"
        lines.append(
            f"• [{r['id']}] {when} ({r.get('fire_at_utc', '?')[:16]}): {r.get('text', '')[:80]}"
        )
    return "\n".join(lines)


def _reminder_delete(ctx: ToolContext, id: str) -> str:
    """Cancel a reminder by its ID."""
    reminders = _load(ctx)
    before = len(reminders)
    reminders = [r for r in reminders if r.get("id") != id]
    if len(reminders) == before:
        return f"⚠️ Reminder '{id}' not found."
    _save(ctx, reminders)
    return f"OK: reminder '{id}' deleted."


def _reminder_check(ctx: ToolContext) -> str:
    """Check for due reminders and fire them. Intended for background consciousness.

    Returns a summary of what fired (or 'nothing due' if none).
    Fired reminders are removed from storage.
    """
    lock_path = ctx.drive_root / _LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_timeout_sec = 3.0
    lock_stale_sec = 15.0
    lock_sleep_sec = 0.05

    lock_fd = None
    lock_acquired = False
    try:
        start = time.time()
        while time.time() - start < lock_timeout_sec:
            try:
                lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                lock_acquired = True
                break
            except FileExistsError:
                try:
                    stat = lock_path.stat()
                    if time.time() - stat.st_mtime > lock_stale_sec:
                        lock_path.unlink()
                        continue
                except Exception:
                    pass
                time.sleep(lock_sleep_sec)
            except Exception:
                log.debug("Failed to acquire reminders lock", exc_info=True)
                break

        if not lock_acquired:
            return "Skipped: another worker is checking reminders."

        reminders = _load(ctx)
        if not reminders:
            return "No pending reminders."

        now = datetime.now(tz=timezone.utc)
        due = [r for r in reminders if _is_due(r, now)]
        remaining = [r for r in reminders if not _is_due(r, now)]

        if not due:
            # Report next upcoming reminder for context
            upcoming = sorted(reminders, key=lambda x: x.get("fire_at_utc", ""))
            next_r = upcoming[0]
            dt = _parse_iso(next_r.get("fire_at_utc", ""))
            delta = int((dt - now).total_seconds()) if dt else -1
            return f"Nothing due. Next: [{next_r['id']}] in {_fmt_delta(delta)}: {next_r.get('text','')[:60]}"

        # Fire each due reminder
        fired = []
        for r in due:
            text = r.get("text", "(no text)")
            if ctx.current_chat_id:
                ctx.pending_events.append({
                    "type": "send_message",
                    "chat_id": ctx.current_chat_id,
                    "text": f"⏰ Напоминание: {text}",
                    "format": "markdown",
                    "is_progress": False,
                    "ts": utc_now_iso(),
                })
            fired.append(f"[{r['id']}] {text[:80]}")

        # After firing, reschedule daily reminders
        for r in due:
            if r.get("repeat_daily"):
                orig_dt = _parse_iso(r.get("fire_at_utc", ""))
                if orig_dt:
                    from datetime import timedelta
                    next_dt = orig_dt + timedelta(days=1)
                    remaining.append({
                        "id": r["id"],
                        "fire_at_utc": next_dt.isoformat(),
                        "text": r.get("text", ""),
                        "repeat_daily": True,
                        "created_at": utc_now_iso(),
                    })

        _save(ctx, remaining)
        fired_str = "\n".join(fired)
        return f"Fired {len(due)} reminder(s):\n{fired_str}"

    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception:
                pass
        if lock_acquired:
            try:
                lock_path.unlink()
            except Exception:
                pass


def _is_due(r: Dict, now: datetime) -> bool:
    dt = _parse_iso(r.get("fire_at_utc", ""))
    return dt is not None and dt <= now


def _fmt_delta(seconds: int) -> str:
    """Human-readable duration."""
    if seconds < 0:
        return "overdue"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m = seconds // 60
        return f"{m}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "reminder_set",
            {
                "name": "reminder_set",
                "description": (
                    "Schedule a persistent reminder that survives restarts. "
                    "Fires at a specific UTC time and sends a message to the owner. "
                    "Stored in Drive — not in memory. "
                    "Supports repeat_daily to automatically reschedule every 24 hours."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fire_at_utc": {
                            "type": "string",
                            "description": "ISO 8601 UTC datetime, e.g. '2026-02-28T19:00:00Z'",
                        },
                        "text": {
                            "type": "string",
                            "description": "Message to send to the owner when reminder fires.",
                        },
                        "id": {
                            "type": "string",
                            "description": "Optional stable ID (to overwrite an existing reminder). Omit for random.",
                        },
                        "repeat_daily": {
                            "type": "boolean",
                            "description": "If true, automatically reschedule this reminder every 24 hours after it fires.",
                        },
                    },
                    "required": ["fire_at_utc", "text"],
                },
            },
            _reminder_set,
        ),
        ToolEntry(
            "reminder_list",
            {
                "name": "reminder_list",
                "description": "List all pending persistent reminders.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            _reminder_list,
        ),
        ToolEntry(
            "reminder_delete",
            {
                "name": "reminder_delete",
                "description": "Cancel a pending reminder by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Reminder ID to cancel"},
                    },
                    "required": ["id"],
                },
            },
            _reminder_delete,
        ),
        ToolEntry(
            "reminder_check",
            {
                "name": "reminder_check",
                "description": (
                    "Check for due reminders and fire them (send to owner). "
                    "Call this from background consciousness on each wakeup. "
                    "Returns a summary of what fired."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            _reminder_check,
        ),
    ]
