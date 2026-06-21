"""
app/agents/planner.py
---------------------
Tool-call planner for AtlasCare.

Calls Groq with the planner prompt and returns a ToolCallPlan.
Falls back to an empty plan on any parse or API error — never raises.
"""

from __future__ import annotations

import json
import logging
import time

#import google.generativeai as genai
from groq import Groq # type: ignore
#from app.services.Groq import generate
from app.config import settings
from app.schemas.agent import ToolCallPlan, ToolCallStep
from opentelemetry import trace

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)

_PROMPT_PATH = "prompts/planner.txt"

try:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as _fh:
        _PROMPT_TEMPLATE = _fh.read()
except FileNotFoundError:
    logger.error("Planner prompt not found at %s", _PROMPT_PATH)
    _PROMPT_TEMPLATE = ""


def _get_model() -> tuple[Groq, str]:
    """Initialise and return the Groq client + model name."""
    
    client = Groq(api_key=settings.GROQ_API_KEY)
    model = "llama-3.1-8b-instant"

    return client, model


def _validate_steps(steps: list[dict]) -> list[ToolCallStep]:
    """
    Validate and coerce raw step dicts into ToolCallStep objects.
    Drops any step with an invalid tool name or missing required args.
    Logs a warning for each dropped step.
    """
    valid: list[ToolCallStep] = []
    for raw_step in steps:
        try:
            step = ToolCallStep(**raw_step)
            # Extra guard: kb_search must never have empty tags
            if step.tool == "kb_search" and not step.args.get("tags"):
                logger.warning("Planner emitted kb_search with empty tags — dropping step")
                continue
            valid.append(step)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dropping invalid planner step %s: %s", raw_step, exc)
    return valid



def plan(
    message: str,
    intent: str,
    session_context: dict,
    trace_id: str,
) -> ToolCallPlan:
    """
    Produce an ordered list of tool calls to resolve the customer request.

    Never raises — returns ToolCallPlan(steps=[]) on any failure.
    """
    t0 = time.monotonic()
    # with tracer.start_as_current_span("planner") as span:
    #     span.set_attribute("app.trace_id", trace_id)
    #     span.set_attribute("planner.intent", intent)
    #     span.set_attribute("planner.message_length", len(message))

    if not _PROMPT_TEMPLATE:
        logger.error("[%s] Planner prompt template is empty", trace_id)
        return ToolCallPlan(steps=[])

    # Use safe replacement for known placeholders so literal JSON examples
    # in the prompt don't get treated as format fields by str.format.
    prompt = (
        _PROMPT_TEMPLATE
        .replace("{message}", message)
        .replace("{intent}", intent)
        .replace("{session_context}", json.dumps(session_context))
        .replace("{trace_id}", trace_id)
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
        temperature=0.0,
        max_tokens=500,
        )
        raw = response.choices[0].message.content.strip()
        logger.info("[%s] RAW JSON FROM MODEL planner: %s", trace_id, raw)
        logger.debug("[%s] Planner raw response: %s", trace_id, raw)

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # if not raw.startswith("{"):
        #     raw = _extract_json_object(raw)

        data = json.loads(raw)
        steps = _validate_steps(data.get("steps", []))
        result = ToolCallPlan(steps=steps)

    except json.JSONDecodeError as exc:
        logger.warning("[%s] Planner JSON parse error: %s", trace_id, exc)
        result = ToolCallPlan(steps=[])

    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] Planner error: %s", trace_id, exc)
        result = ToolCallPlan(steps=[])
    

    elapsed = (time.monotonic() - t0) * 1000
    logger.info(
        "[%s] plan finished steps=%d latency=%.1fms",
        trace_id,
        len(result.steps),
        elapsed,
    )

    with tracer.start_as_current_span("planner") as span:
        span.set_attribute("app.trace_id", trace_id)
        span.set_attribute("planner.steps", len(result.steps))
        span.set_attribute("planner.message_length", len(message))
    
    return result
