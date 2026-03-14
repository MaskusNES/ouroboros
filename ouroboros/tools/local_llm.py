"""Local LLM tools: local_llm_query, local_llm_status."""

from __future__ import annotations

import json
import logging
from typing import List

import requests

from ouroboros.llm import _ollama_base_url, get_local_models, local_llm_available
from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


def _local_llm_query(
    ctx: ToolContext,
    prompt: str,
    model: str = "deepseek-r1:14b",
    max_tokens: int = 2048,
) -> str:
    try:
        url = _ollama_base_url() + "/api/generate"
        resp = requests.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        return f"local_llm_query error: {type(e).__name__}: {e}"


def _local_llm_status(ctx: ToolContext) -> str:
    try:
        available = local_llm_available()
        models = get_local_models() if available else []
        return json.dumps({"available": available, "models": models, "endpoint": _ollama_base_url()})
    except Exception as e:
        return f"local_llm_status error: {type(e).__name__}: {e}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("local_llm_query", {
            "name": "local_llm_query",
            "description": (
                "Query the local Ollama LLM (DeepSeek R1:14b on RTX 5070 Ti via VPN). "
                "Use for summarization, analysis, draft writing — saves paid API budget. "
                "NOT suitable for tool calls or complex reasoning chains."
            ),
            "parameters": {"type": "object", "properties": {
                "prompt": {"type": "string", "description": "The prompt/question to send to the local LLM"},
                "model": {"type": "string", "default": "deepseek-r1:14b", "description": "Model name to use"},
                "max_tokens": {"type": "integer", "default": 2048, "description": "Max tokens in response"},
            }, "required": ["prompt"]},
        }, _local_llm_query),
        ToolEntry("local_llm_status", {
            "name": "local_llm_status",
            "description": "Check if local Ollama instance is available and list loaded models.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _local_llm_status),
    ]
