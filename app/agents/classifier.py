"""
app/agents/classifier.py
------------------------
Intent classifier for AtlasCare.

Calls Groq with the classifier prompt and returns a ClassifyResult.
Falls back to intent=UNKNOWN on any parse or API error — never raises.
"""

from __future__ import annotations

import json
import logging
import time
from urllib import response

#import google.generativeai as genai
from groq import Groq # type: ignore
#from app.services.Groq import generate
from app.config import settings
from app.schemas.agent import ClassifyResult
import opentelemetry.trace as trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Load prompt once at import time
_PROMPT_PATH = "prompts/classifier.txt"

try:
    with open(_PROMPT_PATH, "r", encoding="utf-8") as _fh:
        _PROMPT_TEMPLATE = _fh.read()
except FileNotFoundError:
    logger.error("Classifier prompt not found at %s", _PROMPT_PATH)
    _PROMPT_TEMPLATE = ""


def _get_model() -> tuple[Groq, str]:
    """Initialise and return the Groq client + model name."""
    
    client = Groq(api_key=settings.GROQ_API_KEY)
    model = "llama-3.1-8b-instant"

    return client, model


def classify(message: str, trace_id: str) -> ClassifyResult:
    """
    Classify the customer message into one of: TRACKING | COMPOUND | ESCALATION | UNKNOWN.

    Never raises — returns ClassifyResult(intent="UNKNOWN", confidence=0.0) on any failure.
    """
    t0 = time.monotonic()

    if not _PROMPT_TEMPLATE:
        logger.error("[%s] Classifier prompt template is empty", trace_id)
        return ClassifyResult(intent="UNKNOWN", confidence=0.0)

    # Use safe replacement for known placeholders to avoid interpreting
    # literal JSON examples in the prompt as format fields.
    prompt = _PROMPT_TEMPLATE.replace("{message}", message).replace("{trace_id}", trace_id)

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
        #raw = await generate(prompt, trace_id)
        logger.info("[%s] RAW JSON FROM MODEL_classifier: %s", trace_id, raw)
        logger.debug("[%s] Classifier raw response: %s", trace_id, raw)

        # Strip markdown fences if the model adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data = json.loads(raw)
        result = ClassifyResult(**data)

    except json.JSONDecodeError as exc:
        logger.warning("[%s] Classifier JSON parse error: %s", trace_id, exc)
        result = ClassifyResult(intent="UNKNOWN", confidence=0.0)

    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] Classifier error: %s", trace_id, exc)
        result = ClassifyResult(intent="UNKNOWN", confidence=0.0)

    elapsed = (time.monotonic() - t0) * 1000
    logger.info(
        "[%s] classify finished intent=%s confidence=%.2f latency=%.1fms",
        trace_id,
        result.intent,
        result.confidence,
        elapsed,
    )

    with tracer.start_as_current_span("classifier") as span:
        span.set_attribute("app.trace_id", trace_id)
        span.set_attribute("classifier.intent", result.intent)
        span.set_attribute("classifier.confidence", result.confidence)
        span.set_attribute("classifier.message_length", len(message))


    return result
