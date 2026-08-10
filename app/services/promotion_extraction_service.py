from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape

import httpx

from app.config import Settings, get_settings

CATEGORIES = (
    "Groceries",
    "Household",
    "Clothing",
    "Beauty",
    "Dining",
    "Travel",
    "Electronics",
    "Health and Fitness",
    "Entertainment",
    "Home",
    "Other",
)


@dataclass(frozen=True)
class ExtractedOffer:
    merchant: str
    headline: str
    offer_type: str
    category: str = "Other"
    percent_off: float | None = None
    amount_off: float | None = None
    minimum_spend: float | None = None
    promo_code: str | None = None
    expires_at: datetime | None = None
    expiry_precision: str = "unknown"
    destination_url: str | None = None
    terms_summary: str | None = None
    confidence: float = 0.75


class PromotionExtractionService:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.client = client

    def extract(
        self, *, subject: str, body: str, sender_name: str, html: str = ""
    ) -> list[ExtractedOffer]:
        structured = self._structured(html, sender_name)
        if structured:
            return structured
        text = unescape(re.sub(r"\s+", " ", f"{subject}\n{body}"))
        deterministic = self._deterministic(text, sender_name)
        if deterministic:
            return deterministic
        if self.settings.promotions_llm_fallback_enabled and self.settings.openai_api_key:
            return self._llm(text[: self.settings.promotions_max_llm_body_chars], sender_name)
        return []

    def _llm(self, text: str, merchant: str) -> list[ExtractedOffer]:
        payload = {
            "model": self.settings.openai_model,
            "input": (
                "Extract only concrete promotions with a measurable benefit. "
                "Return no offers for vague marketing. Never invent terms or expiry.\n\n"
                f"Sender: {merchant}\nEmail: {text}"
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "promotion_offers",
                    "strict": True,
                    "schema": _LLM_SCHEMA,
                }
            },
        }
        try:
            if self.client:
                response = self.client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                    json=payload,
                    timeout=30.0,
                )
            else:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                        json=payload,
                    )
            response.raise_for_status()
            body = response.json()
            output = body.get("output_text") or _output_text(body)
            values = json.loads(output).get("offers", [])
            return [_from_llm(value, merchant) for value in values if value.get("is_concrete")]
        except Exception:
            return []

    def _structured(self, html: str, merchant: str) -> list[ExtractedOffer]:
        offers: list[ExtractedOffer] = []
        for raw in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.I | re.S,
        ):
            try:
                data = json.loads(unescape(raw))
            except (ValueError, TypeError):
                continue
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict) or str(node.get("@type", "")).casefold() not in {
                    "discountoffer",
                    "offer",
                }:
                    continue
                description = str(node.get("description") or node.get("name") or "").strip()
                parsed = self._deterministic(description, merchant)
                if parsed:
                    value = parsed[0]
                    offers.append(
                        ExtractedOffer(
                            **{
                                **value.__dict__,
                                "destination_url": node.get("url") or value.destination_url,
                                "confidence": 0.95,
                            }
                        )
                    )
        return offers

    def _deterministic(self, text: str, merchant: str) -> list[ExtractedOffer]:
        lowered = text.casefold()
        percent = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*%\s*off\b", text, re.I)
        amount = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)\s*off\b", text, re.I)
        bogo = re.search(r"\b(?:bogo|buy\s+one\s+get\s+one)\b", text, re.I)
        free_item = re.search(r"\bfree\s+(?:entr[eé]e|item|gift|shipping)\b", text, re.I)
        if not any((percent, amount, bogo, free_item)):
            return []
        minimum = re.search(
            r"(?:\$\s*\d+(?:\.\d{1,2})?\s*off\s*(?:a|your)?\s*)\$\s*(\d+(?:\.\d{1,2})?)|(?:minimum|spend)\s*\$\s*(\d+(?:\.\d{1,2})?)",
            text,
            re.I,
        )
        code = re.search(
            r"(?:code|promo(?:tional)?\s+code)\s*[:\-]?\s*([A-Z0-9][A-Z0-9_-]{2,20})", text, re.I
        )
        url = re.search(r"https?://[^\s<>\"']+", text, re.I)
        expiry, precision = _expiry(text)
        offer_type = (
            "percent_off"
            if percent
            else "amount_off"
            if amount
            else "bogo"
            if bogo
            else "free_item"
        )
        matched = percent or amount or bogo or free_item
        headline = matched.group(0) if matched else text[:120]
        return [
            ExtractedOffer(
                merchant=merchant or "Unknown merchant",
                headline=headline.strip().capitalize(),
                offer_type=offer_type,
                category=_category(lowered),
                percent_off=float(percent.group(1)) if percent else None,
                amount_off=float(amount.group(1)) if amount else None,
                minimum_spend=float(next(group for group in minimum.groups() if group))
                if minimum
                else None,
                promo_code=code.group(1) if code else None,
                expires_at=expiry,
                expiry_precision=precision,
                destination_url=url.group(0).rstrip(".,)") if url else None,
                terms_summary=(
                    f"Minimum purchase ${next(group for group in minimum.groups() if group)}"
                    if minimum
                    else None
                ),
                confidence=0.88,
            )
        ]


def _category(text: str) -> str:
    rules = {
        "Household": ("laundry", "detergent", "paper towel", "cleaning"),
        "Groceries": ("grocery", "food", "aldi", "produce"),
        "Clothing": ("clothing", "apparel", "styles", "shirt", "jeans"),
        "Dining": ("restaurant", "entrée", "entree", "dining", "chipotle"),
        "Beauty": ("beauty", "makeup", "salon", "skincare"),
        "Electronics": ("electronics", "television", "laptop", "phone"),
        "Travel": ("flight", "hotel", "travel"),
        "Home": ("furniture", "home improvement"),
        "Health and Fitness": ("fitness", "gym", "vitamin"),
        "Entertainment": ("movie", "streaming", "concert"),
    }
    return next(
        (category for category, words in rules.items() if any(word in text for word in words)),
        "Other",
    )


def _expiry(text: str) -> tuple[datetime | None, str]:
    match = re.search(r"(?:expires?|ends?)\s+(?:on\s+)?(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I)
    if not match:
        return None, "unknown"
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            value = datetime.strptime(match.group(1), fmt).replace(
                tzinfo=UTC, hour=23, minute=59, second=59
            )
            return value, "date"
        except ValueError:
            pass
    return None, "unknown"


def _output_text(body: dict) -> str:
    for output in body.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text":
                return str(content.get("text") or "")
    return '{"offers":[]}'


def _from_llm(value: dict, default_merchant: str) -> ExtractedOffer:
    expires_at = None
    if value.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(str(value["expires_at"]).replace("Z", "+00:00"))
        except ValueError:
            pass
    category = value.get("category") if value.get("category") in CATEGORIES else "Other"
    return ExtractedOffer(
        merchant=value.get("merchant") or default_merchant or "Unknown merchant",
        headline=value.get("headline") or "Promotion",
        offer_type=value.get("offer_type") or "other",
        category=category,
        percent_off=value.get("percent_off"),
        amount_off=value.get("amount_off"),
        minimum_spend=value.get("minimum_spend"),
        promo_code=value.get("promo_code"),
        expires_at=expires_at,
        expiry_precision=value.get("expiry_precision") or "unknown",
        destination_url=value.get("destination_url"),
        terms_summary=value.get("terms_summary"),
        confidence=float(value.get("confidence") or 0),
    )


_NULLABLE_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_LLM_FIELDS = {
    "is_concrete": {"type": "boolean"},
    "merchant": _NULLABLE_STRING,
    "headline": _NULLABLE_STRING,
    "offer_type": {"type": "string"},
    "category": {"type": "string"},
    "percent_off": _NULLABLE_NUMBER,
    "amount_off": _NULLABLE_NUMBER,
    "minimum_spend": _NULLABLE_NUMBER,
    "promo_code": _NULLABLE_STRING,
    "expires_at": _NULLABLE_STRING,
    "expiry_precision": {"type": "string"},
    "destination_url": _NULLABLE_STRING,
    "terms_summary": _NULLABLE_STRING,
    "confidence": {"type": "number"},
}
_LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["offers"],
    "properties": {
        "offers": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_LLM_FIELDS),
                "properties": _LLM_FIELDS,
            },
        }
    },
}
