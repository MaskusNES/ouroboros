"""Weekly AI apps digest — найти самые трендовые AI-приложения для ПК и iPhone
и отправить дайджест владельцу.

Tools:
  ai_digest_run    — собрать и отправить дайджест прямо сейчас
  ai_digest_check  — проверить и при необходимости запустить еженедельный дайджест
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.utils import utc_now_iso

log = logging.getLogger(__name__)

_STATE_FILE = "state/ai_digest_state.json"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state(ctx: ToolContext) -> dict:
    path = ctx.drive_root / _STATE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(ctx: ToolContext, state: dict) -> None:
    path = ctx.drive_root / _STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core digest builder
# ---------------------------------------------------------------------------

def _build_digest(ctx: ToolContext) -> str:
    """Search for trending AI apps and build a digest message."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "⚠️ OPENAI_API_KEY не задан — web_search недоступен."

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        websearch_model = os.environ.get("OUROBOROS_WEBSEARCH_MODEL", "gpt-5")

        def search(query: str) -> str:
            resp = client.responses.create(
                model=websearch_model,
                tools=[{"type": "web_search"}],
                tool_choice="auto",
                input=query,
            )
            d = resp.model_dump()
            text = ""
            for item in d.get("output", []) or []:
                if item.get("type") == "message":
                    for block in item.get("content", []) or []:
                        if block.get("type") in ("output_text", "text"):
                            text += block.get("text", "")
            return text or "(нет результатов)"

        # Two targeted searches — economy mode (max 2 searches per policy)
        iphone_results = search(
            "trending AI apps iPhone App Store 2026 top new artificial intelligence productivity"
        )
        pc_results = search(
            "trending AI apps Windows Mac desktop 2026 top new artificial intelligence productivity"
        )

        # Build digest
        now = datetime.now(tz=timezone.utc)
        week_str = now.strftime("%-d %B %Y")

        digest = f"""📱💻 **AI-приложения недели** (неделя {week_str})

---

**📱 iPhone (App Store)**

{iphone_results[:1200]}

---

**💻 ПК (Windows / Mac)**

{pc_results[:1200]}

---
_Дайджест формируется автоматически раз в неделю_"""

        return digest

    except Exception as e:
        return f"⚠️ Ошибка при формировании дайджеста: {e}"


# ---------------------------------------------------------------------------
# Tool: ai_digest_run
# ---------------------------------------------------------------------------

def _ai_digest_run(ctx: ToolContext) -> str:
    """Собрать и отправить дайджест трендовых AI-приложений прямо сейчас."""
    digest = _build_digest(ctx)

    # Send to owner
    if ctx.current_chat_id:
        ctx.pending_events.append({
            "type": "send_message",
            "chat_id": ctx.current_chat_id,
            "text": digest,
            "format": "markdown",
            "is_progress": False,
            "ts": utc_now_iso(),
        })

    # Save last run time
    state = _load_state(ctx)
    state["last_run_utc"] = utc_now_iso()
    _save_state(ctx, state)

    return f"Дайджест отправлен. Длина: {len(digest)} символов."


# ---------------------------------------------------------------------------
# Tool: ai_digest_check (consciousness_only)
# ---------------------------------------------------------------------------

def _ai_digest_check(ctx: ToolContext) -> str:
    """Проверить: пора ли запускать еженедельный дайджест AI-приложений.
    Запускает дайджест если прошло >= 7 дней с последнего запуска.
    Вызывается из фонового сознания.
    """
    state = _load_state(ctx)
    last_run_str = state.get("last_run_utc", "")

    now = datetime.now(tz=timezone.utc)

    if last_run_str:
        try:
            last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            delta = now - last_run
            if delta.total_seconds() < 7 * 24 * 3600:
                days_left = 7 - delta.days
                return f"Дайджест не нужен — последний был {delta.days}д назад. Следующий через ~{days_left}д."
        except Exception:
            pass

    # Time to run!
    log.info("AI digest: 7 days passed, running weekly digest")
    result = _ai_digest_run(ctx)
    return f"Еженедельный дайджест запущен: {result}"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "ai_digest_run",
            {
                "name": "ai_digest_run",
                "description": (
                    "Собрать и немедленно отправить владельцу дайджест самых трендовых "
                    "AI-приложений для iPhone и ПК за текущую неделю. "
                    "Использует 2 web_search запроса."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            _ai_digest_run,
        ),
        ToolEntry(
            "ai_digest_check",
            {
                "name": "ai_digest_check",
                "description": (
                    "Проверить и при необходимости запустить еженедельный дайджест AI-приложений. "
                    "Запускает дайджест если прошло >= 7 дней с последнего запуска. "
                    "Можно вызвать вручную для немедленной проверки."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            _ai_digest_check,
        ),
    ]
