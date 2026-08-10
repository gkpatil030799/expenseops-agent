from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

from app.config import Settings, get_settings


@dataclass(frozen=True)
class ParsedReceiptItem:
    name: str
    quantity: float | None = None
    unit: str | None = None
    unit_price_cents: int | None = None
    line_total_cents: int | None = None
    brand: str | None = None
    category: str | None = None
    confidence: float = 0.0
    is_household_purchase: bool = True


@dataclass(frozen=True)
class ParsedReceipt:
    merchant: str | None
    purchased_at: datetime | None
    subtotal_cents: int | None
    tax_cents: int | None
    total_cents: int | None
    currency: str = "USD"
    confidence: float = 0.0
    items: list[ParsedReceiptItem] = field(default_factory=list)


class ReceiptParserError(RuntimeError):
    pass


class ReceiptParser(Protocol):
    def parse_attachment(self, content: bytes, mime_type: str, filename: str) -> ParsedReceipt: ...

    def parse_text(self, text: str) -> ParsedReceipt: ...


class UnavailableReceiptParser:
    def parse_attachment(self, content: bytes, mime_type: str, filename: str) -> ParsedReceipt:
        raise ReceiptParserError("receipt_parser_not_configured")

    def parse_text(self, text: str) -> ParsedReceipt:
        raise ReceiptParserError("receipt_parser_not_configured")


class OpenAIReceiptParser:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ):
        self.settings = settings or get_settings()
        self.client = client

    def parse_attachment(self, content: bytes, mime_type: str, filename: str) -> ParsedReceipt:
        encoded = base64.b64encode(content).decode("ascii")
        if mime_type == "application/pdf":
            source = {
                "type": "input_file",
                "filename": filename or "receipt.pdf",
                "file_data": f"data:{mime_type};base64,{encoded}",
            }
        elif mime_type.startswith("image/"):
            source = {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{encoded}",
                "detail": "high",
            }
        else:
            raise ReceiptParserError("unsupported_receipt_type")
        return self._request([source, {"type": "input_text", "text": _PROMPT}])

    def parse_text(self, text: str) -> ParsedReceipt:
        if not text.strip():
            raise ReceiptParserError("empty_receipt_text")
        return self._request(
            [{"type": "input_text", "text": f"{_PROMPT}\n\nReceipt text:\n{text[:30000]}"}]
        )

    def _request(self, content: list[dict]) -> ParsedReceipt:
        if not self.settings.openai_api_key:
            raise ReceiptParserError("openai_not_configured")
        payload = {
            "model": self.settings.receipt_parser_model,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "receipt",
                    "strict": True,
                    "schema": _SCHEMA,
                }
            },
        }
        try:
            if self.client:
                response = self.client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                    json=payload,
                )
            else:
                with httpx.Client(timeout=45.0) as client:
                    response = client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                        json=payload,
                    )
            response.raise_for_status()
            body = response.json()
            output_text = body.get("output_text") or _extract_output_text(body)
            return _from_json(json.loads(output_text))
        except ReceiptParserError:
            raise
        except Exception as exc:
            raise ReceiptParserError("receipt_parse_failed") from exc


def build_receipt_parser(settings: Settings | None = None) -> ReceiptParser:
    config = settings or get_settings()
    if config.receipt_parser_provider == "openai":
        return OpenAIReceiptParser(config)
    return UnavailableReceiptParser()


def _extract_output_text(body: dict) -> str:
    for output in body.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "output_text":
                return str(content.get("text") or "")
    raise ReceiptParserError("receipt_parse_empty_response")


def _from_json(value: dict) -> ParsedReceipt:
    purchased_at = None
    if value.get("purchased_at"):
        try:
            purchased_at = datetime.fromisoformat(str(value["purchased_at"]).replace("Z", "+00:00"))
        except ValueError:
            purchased_at = None
    return ParsedReceipt(
        merchant=value.get("merchant"),
        purchased_at=purchased_at,
        subtotal_cents=value.get("subtotal_cents"),
        tax_cents=value.get("tax_cents"),
        total_cents=value.get("total_cents"),
        currency=value.get("currency") or "USD",
        confidence=float(value.get("confidence") or 0.0),
        items=[ParsedReceiptItem(**item) for item in value.get("items", [])],
    )


_PROMPT = """Extract only actual purchased line items from this receipt/order confirmation.
Ignore advertisements, recommendations, payment card numbers, loyalty identifiers,
and unrelated email text.
Use integer cents for monetary values. Return null when a field is not visible; never invent it.
Mark non-household/service/refund lines with is_household_purchase=false. Confidence is 0..1."""

_NULLABLE_INTEGER = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_NULLABLE_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "merchant",
        "purchased_at",
        "subtotal_cents",
        "tax_cents",
        "total_cents",
        "currency",
        "confidence",
        "items",
    ],
    "properties": {
        "merchant": _NULLABLE_STRING,
        "purchased_at": _NULLABLE_STRING,
        "subtotal_cents": _NULLABLE_INTEGER,
        "tax_cents": _NULLABLE_INTEGER,
        "total_cents": _NULLABLE_INTEGER,
        "currency": {"type": "string"},
        "confidence": {"type": "number"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "quantity",
                    "unit",
                    "unit_price_cents",
                    "line_total_cents",
                    "brand",
                    "category",
                    "confidence",
                    "is_household_purchase",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "quantity": _NULLABLE_NUMBER,
                    "unit": _NULLABLE_STRING,
                    "unit_price_cents": _NULLABLE_INTEGER,
                    "line_total_cents": _NULLABLE_INTEGER,
                    "brand": _NULLABLE_STRING,
                    "category": _NULLABLE_STRING,
                    "confidence": {"type": "number"},
                    "is_household_purchase": {"type": "boolean"},
                },
            },
        },
    },
}
