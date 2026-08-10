from __future__ import annotations

import re
from urllib.parse import urlparse

HIGH_RISK = (
    "gambling",
    "casino",
    "payday loan",
    "debt relief",
    "adult",
    "weapon",
    "firearm",
    "nicotine",
    "vape",
    "recreational drug",
)


def safe_destination(
    url: str | None, sender_domain: str | None, merchant: str
) -> tuple[str | None, str | None, str]:
    if not url:
        return None, None, "review"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None, None, "suppressed"
    domain = parsed.hostname.casefold().removeprefix("www.")
    sender = (sender_domain or "").casefold().removeprefix("www.")
    merchant_tokens = {
        token for token in re.findall(r"[a-z0-9]+", merchant.casefold()) if len(token) > 3
    }
    matching = sender and (
        domain == sender or domain.endswith("." + sender) or sender.endswith("." + domain)
    )
    merchant_match = any(token in domain for token in merchant_tokens)
    return url, domain, "trusted" if matching or merchant_match else "review"


def is_high_risk(text: str) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in HIGH_RISK)
