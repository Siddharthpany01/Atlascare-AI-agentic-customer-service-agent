"""
app/agents/synthesizer.py
--------------------------
Response synthesizer for AtlasCare.

Formats tool results from the executor into a JSON summary, injects them into
the synthesizer prompt, calls Groq, and returns the final
customer-facing reply string.

Never raises — returns a safe fallback message on any error.
"""

from __future__ import annotations

import json
import logging
import time

#import google.generativeai as genai
from groq import Groq #type: ignore
from app.config import settings
from app.schemas.trace import ToolCallRecord
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_PROMPT_PATH = "prompts/synthesizer.txt"
_FALLBACK_REPLY = (
    "I'm sorry, I encountered an issue preparing your response. "
    "Please try again or contact support if this persists."
)

try:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as _fh:
        _PROMPT_TEMPLATE = _fh.read()
except FileNotFoundError:
    logger.error("Synthesizer prompt not found at %s", _PROMPT_PATH)
    _PROMPT_TEMPLATE = ""


def _get_model() -> tuple[Groq, str]:
    """Initialise and return the Groq client + model name."""
    
    client = Groq(api_key=settings.GROQ_API_KEY)
    model = "llama-3.1-8b-instant"

    return client, model


def _build_tool_results_summary(records: list[ToolCallRecord]) -> str:
    """
    Convert executor records into a JSON string for the synthesizer prompt.
    Includes the full ToolResult data if attached by the executor.
    """
    summaries = []
    for record in records:
        tool_result = record.__dict__.get("_tool_result")
        entry: dict = {
            "seq": record.seq,
            "tool": record.tool,
            "args": record.args,
            "status": record.status,
        }
        if tool_result is not None:
            entry["data"] = tool_result.data
            entry["error_code"] = tool_result.error_code
            entry["error_message"] = tool_result.error_message
        summaries.append(entry)
    return json.dumps(summaries, indent=2, ensure_ascii=False)


def synthesize(
    message: str,
    tool_results: list[ToolCallRecord],
    trace_id: str,
) -> str:
    """
    Compose a customer-facing reply from the tool results.

    Never raises — returns _FALLBACK_REPLY on any error.
    """
    t0 = time.monotonic()

    if not _PROMPT_TEMPLATE:
        logger.error("[%s] Synthesizer prompt template is empty", trace_id)
        return _FALLBACK_REPLY

    tool_results_json = _build_tool_results_summary(tool_results)

    prompt = _PROMPT_TEMPLATE.format(
        message=message,
        tool_results=tool_results_json,
    )

    try:
        client, model = _get_model()
        response = client.chat.completions.create(
        model=model,
         messages=[
        {
            "role": "user",
            "content": prompt,
        }
        ],
        temperature=0.4,
        max_tokens=500,
        )
        reply = response.choices[0].message.content.strip()
        logger.info("[%s] RAW JSON FROM MODEL synthesizer: %s", trace_id, reply)        
        #reply = await generate(prompt, trace_id, temperature=0.3)

    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] Synthesizer error: %s", trace_id, exc)
        reply = _FALLBACK_REPLY
    
    with tracer.start_as_current_span("synthesizer") as span:
        span.set_attribute("app.trace_id", trace_id)
        span.set_attribute("synthesizer.message_length", len(message))

    elapsed = (time.monotonic() - t0) * 1000
    logger.info("[%s] synthesize finished latency=%.1fms", trace_id, elapsed)
    return reply
