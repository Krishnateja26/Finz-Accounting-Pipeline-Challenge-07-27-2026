"""
Gemini fallback classifier.

Called ONLY when `classify_deterministic` returns None. Gemini is
constrained to a strict JSON schema and the actual chart of accounts, and
its answer is validated before it is trusted. Gemini recommends; it never
decides whether a transaction gets posted to QuickBooks -- that is always a
human (review_status must become APPROVED) or a high-confidence deterministic
rule (see classification.py policy in the review API).
"""
import json
import logging

import httpx

from app.config import get_settings
from app.models.enums import TransactionType
from app.services.classification import CHART_OF_ACCOUNTS

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = {t.value for t in TransactionType} - {TransactionType.UNKNOWN.value}

_SYSTEM_PROMPT = """You are an accounting classification assistant for a US home-services company
using cash-basis accounting. You will be given one bank transaction description and its signed
amount in USD (negative = money out, positive = money in). Classify it using ONLY the accounts in
this chart of accounts:

{chart}

Respond with ONLY a JSON object (no markdown fences, no prose) matching exactly this schema:
{{
  "transaction_type": one of {types},
  "counterparty": "<short business or person name, or null>",
  "account_number": "<one of the account numbers above>",
  "confidence": <float 0-1>,
  "explanation": "<one sentence>"
}}

Never invent an account number that is not in the chart of accounts above. Never change the
amount or date. If you are not reasonably confident, set confidence below 0.6."""


class GeminiClassificationError(Exception):
    pass


def _build_prompt(description: str, amount_cents: int) -> str:
    chart_lines = "\n".join(f"{num}: {name}" for num, name in CHART_OF_ACCOUNTS.items())
    system = _SYSTEM_PROMPT.format(chart=chart_lines, types=sorted(_ALLOWED_TYPES))
    return f"{system}\n\nDescription: {description}\nAmount (cents): {amount_cents}"


async def classify_with_gemini(description: str, amount_cents: int) -> dict:
    """Calls Gemini and returns a validated classification dict, or raises
    GeminiClassificationError if the response is missing, malformed, or
    references an account/type outside our chart -- in which case the
    caller must fall back to manual review rather than trusting the model.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiClassificationError("GEMINI_API_KEY is not configured")

    prompt = _build_prompt(description, amount_cents)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers={"x-goog-api-key": settings.gemini_api_key})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise GeminiClassificationError(f"Gemini request failed: {exc}") from exc

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise GeminiClassificationError(f"Could not parse Gemini response: {exc}") from exc

    return _validate(parsed)


def _validate(parsed: dict) -> dict:
    account_number = parsed.get("account_number")
    transaction_type = parsed.get("transaction_type")
    confidence = parsed.get("confidence")

    if account_number not in CHART_OF_ACCOUNTS:
        raise GeminiClassificationError(f"Gemini returned an unknown account number: {account_number}")
    if transaction_type not in _ALLOWED_TYPES:
        raise GeminiClassificationError(f"Gemini returned an unknown transaction type: {transaction_type}")
    if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
        raise GeminiClassificationError(f"Gemini returned an invalid confidence: {confidence}")

    return {
        "transaction_type": transaction_type,
        "counterparty": parsed.get("counterparty"),
        "account_number": account_number,
        "confidence": float(confidence),
        "explanation": parsed.get("explanation") or "Gemini classification",
        "source": "gemini",
    }
