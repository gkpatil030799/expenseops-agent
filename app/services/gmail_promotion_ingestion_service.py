from __future__ import annotations

import base64
import hashlib
import html
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.job_tenancy import gmail_settings_for_session
from app.models import GmailSyncCheckpoint, PromotionMessage, PromotionOffer, utc_now
from app.services.gmail_client_service import GmailClient
from app.services.managed_auth_service import record_audit_once
from app.services.promotion_extraction_service import PromotionExtractionService
from app.services.promotion_ranking_service import PromotionRankingService
from app.services.promotion_trust_service import is_high_risk, safe_destination


@dataclass(frozen=True)
class PromotionSyncResult:
    scanned: int
    processed: int
    offers_created: int
    skipped: int
    recovery_sync: bool = False


class GmailPromotionIngestionService:
    def __init__(
        self, db: Session, settings: Settings | None = None, gmail: GmailClient | None = None
    ):
        self.db = db
        self.settings = gmail_settings_for_session(db, settings or get_settings())
        self.gmail = gmail or GmailClient(self.settings)

    @property
    def configured(self) -> bool:
        return bool(self.settings.promotions_enabled and self.gmail.configured)

    def sync(self, *, max_results: int | None = None) -> PromotionSyncResult:
        if not self.configured:
            raise ValueError("promotion_sync_not_configured")
        limit = min(
            max_results or self.settings.promotions_max_messages_per_sync,
            self.settings.promotions_max_messages_per_sync,
        )
        token = self.gmail.access_token()
        checkpoint = self._checkpoint()
        recovery = False
        backfill = not (checkpoint.initial_backfill_complete and checkpoint.history_id)
        next_page: str | None = None
        next_incremental_page: str | None = None
        try:
            if backfill:
                ids, newest_history, next_page = self._backfill_ids(
                    token, limit, checkpoint.backfill_page_token
                )
            else:
                ids, newest_history, next_incremental_page = self._incremental_ids(
                    token, checkpoint, limit
                )
        except Exception as exc:
            if "404" not in str(exc):
                raise
            ids, newest_history, next_page = self._backfill_ids(token, limit, None)
            backfill = True
            recovery = True
        processed = created = skipped = 0
        for message_id in ids[:limit]:
            message = self.gmail.get_message(message_id, token)
            labels = set(message.get("labelIds") or [])
            if "CATEGORY_PROMOTIONS" not in labels or labels & {"SPAM", "TRASH"}:
                skipped += 1
                continue
            did_process, count = self.process_message(message)
            processed += int(did_process)
            created += count
            skipped += int(not did_process)
            newest_history = _newer_history(newest_history, message.get("historyId"))
        if backfill:
            checkpoint.history_id = _newer_history(checkpoint.history_id, newest_history)
            checkpoint.backfill_page_token = next_page
            checkpoint.initial_backfill_complete = next_page is None
            checkpoint.incremental_page_token = None
        else:
            checkpoint.incremental_page_token = next_incremental_page
            if next_incremental_page is None:
                checkpoint.history_id = _newer_history(checkpoint.history_id, newest_history)
        checkpoint.updated_at = utc_now()
        self.db.commit()
        PromotionRankingService(self.db).rescore_all()
        return PromotionSyncResult(len(ids), processed, created, skipped, recovery)

    def process_message(self, raw: dict) -> tuple[bool, int]:
        message_id = str(raw.get("id") or "")
        if not message_id:
            return False, 0
        subject, sender, text, raw_html, received = _message_content(raw)
        digest = hashlib.sha256(f"{subject}\n{text}".encode()).hexdigest()
        message = self.db.execute(
            select(PromotionMessage).where(PromotionMessage.gmail_message_id == message_id)
        ).scalar_one_or_none()
        if message and message.content_fingerprint == digest and message.processed_at:
            return False, 0
        sender_name, sender_email = parseaddr(sender)
        sender_domain = sender_email.rpartition("@")[2].casefold() or None
        if message is None:
            message = PromotionMessage(gmail_message_id=message_id, received_at=received)
            self.db.add(message)
        message.gmail_thread_id = raw.get("threadId")
        message.gmail_history_id = str(raw.get("historyId") or "") or None
        message.sender_name = sender_name or sender_email.split("@")[0]
        message.sender_email = sender_email or None
        message.sender_domain = sender_domain
        message.subject = subject[:1000]
        message.snippet = str(raw.get("snippet") or text[:500])[:1000]
        message.gmail_label_snapshot = list(raw.get("labelIds") or [])
        message.content_fingerprint = digest
        message.updated_at = utc_now()
        self.db.flush()
        extracted = PromotionExtractionService(self.settings).extract(
            subject=subject, body=text, sender_name=message.sender_name or "", html=raw_html
        )
        if not extracted:
            message.parse_status = "no_concrete_offer"
            message.parse_confidence = 1.0
            message.processed_at = utc_now()
            self.db.commit()
            return True, 0
        created = 0
        offers_by_fingerprint: dict[str, PromotionOffer] = {}
        for value in extracted:
            value = _validated_expiry(value, received)
            merchant = _normalize_merchant(value.merchant)
            url, domain, trust = safe_destination(value.destination_url, sender_domain, merchant)
            if is_high_risk(f"{subject} {text[:4000]}"):
                trust = "suppressed"
            fingerprint = _campaign_fingerprint(merchant, value, domain, raw.get("threadId"))
            offer = offers_by_fingerprint.get(fingerprint)
            if offer is None:
                offer = self.db.execute(
                    select(PromotionOffer).where(PromotionOffer.campaign_fingerprint == fingerprint)
                ).scalar_one_or_none()
            if offer:
                sources = list(dict.fromkeys([*offer.source_message_ids, message_id]))
                offer.source_message_ids = sources
                offer.promotion_message_id = message.id
                offer.updated_at = utc_now()
                if value.expires_at:
                    offer.expires_at = value.expires_at
                    offer.expiry_precision = value.expiry_precision
                if value.promo_code:
                    offer.promo_code = value.promo_code
            else:
                offer = PromotionOffer(
                    promotion_message_id=message.id,
                    merchant_raw=value.merchant,
                    merchant_normalized=merchant,
                    primary_category=value.category,
                    offer_type=value.offer_type,
                    headline=value.headline,
                    description=f"{subject}. {message.snippet or ''}"[:2000],
                    percent_off=value.percent_off,
                    amount_off=value.amount_off,
                    currency="USD",
                    minimum_spend=value.minimum_spend,
                    promo_code=value.promo_code,
                    expires_at=value.expires_at,
                    expiry_precision=value.expiry_precision,
                    destination_url=url,
                    destination_domain=domain,
                    terms_summary=value.terms_summary,
                    confidence=value.confidence,
                    trust_status=trust,
                    status="suppressed" if trust == "suppressed" else "active",
                    campaign_fingerprint=fingerprint,
                    source_message_ids=[message_id],
                )
                self.db.add(offer)
                created += 1
            offers_by_fingerprint[fingerprint] = offer
        message.parse_status = "parsed"
        message.parse_confidence = max(v.confidence for v in extracted)
        message.processed_at = utc_now()
        if created:
            record_audit_once(
                self.db,
                event_type="first_promotion_processed",
                resource_type="promotion_offer",
            )
        self.db.commit()
        return True, created

    def _checkpoint(self) -> GmailSyncCheckpoint:
        key = f"{self.settings.gmail_user_id}:promotions"
        value = self.db.execute(
            select(GmailSyncCheckpoint).where(GmailSyncCheckpoint.account_key == key)
        ).scalar_one_or_none()
        if value is None:
            value = GmailSyncCheckpoint(account_key=key)
            self.db.add(value)
            self.db.flush()
        return value

    def _backfill_ids(
        self, token: str, limit: int, page_token: str | None
    ) -> tuple[list[str], str | None, str | None]:
        ids: list[str] = []
        page = page_token
        latest: str | None = None
        while len(ids) < limit:
            params = {
                "labelIds": "CATEGORY_PROMOTIONS",
                "q": (
                    f"newer_than:{self.settings.promotions_initial_lookback_days}d "
                    "-in:spam -in:trash"
                ),
                "maxResults": min(100, limit - len(ids)),
            }
            if page:
                params["pageToken"] = page
            data = self.gmail.request(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/{self.settings.gmail_user_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            ).json()
            ids.extend(str(v["id"]) for v in data.get("messages", []) if v.get("id"))
            page = data.get("nextPageToken")
            latest = _newer_history(latest, data.get("historyId"))
            if not page:
                break
        return ids[:limit], latest, page

    def _incremental_ids(
        self, token: str, checkpoint: GmailSyncCheckpoint, limit: int
    ) -> tuple[list[str], str | None, str | None]:
        params = {
            "startHistoryId": checkpoint.history_id,
            "labelId": "CATEGORY_PROMOTIONS",
            "historyTypes": "messageAdded",
            "maxResults": limit,
        }
        if checkpoint.incremental_page_token:
            params["pageToken"] = checkpoint.incremental_page_token
        data = self.gmail.request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/{self.settings.gmail_user_id}/history",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        ).json()
        ids = [
            str(entry["message"]["id"])
            for event in data.get("history", [])
            for entry in event.get("messagesAdded", [])
            if entry.get("message", {}).get("id")
        ]
        return (
            list(dict.fromkeys(ids))[:limit],
            str(data.get("historyId") or checkpoint.history_id),
            str(data.get("nextPageToken") or "") or None,
        )


def _message_content(message: dict) -> tuple[str, str, str, str, datetime]:
    payload = message.get("payload") or {}
    headers = {
        str(v.get("name") or "").casefold(): str(v.get("value") or "")
        for v in payload.get("headers", [])
    }
    plain: list[str] = []
    html_parts: list[str] = []

    def visit(part: dict) -> None:
        data = str((part.get("body") or {}).get("data") or "")
        mime = str(part.get("mimeType") or "")
        if data and mime in {"text/plain", "text/html"}:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
                errors="replace"
            )
            (html_parts if mime == "text/html" else plain).append(decoded)
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    raw_html = "\n".join(html_parts)
    text = "\n".join(plain) or html.unescape(re.sub(r"<[^>]+>", " ", raw_html))
    text = re.sub(r"\s+", " ", text).strip()[:30000]
    try:
        received = parsedate_to_datetime(headers.get("date", "")).astimezone(UTC)
    except (ValueError, TypeError):
        received = (
            datetime.fromtimestamp(int(message.get("internalDate", "0")) / 1000, UTC)
            if message.get("internalDate")
            else utc_now()
        )
    return headers.get("subject", ""), headers.get("from", ""), text, raw_html[:50000], received


def _normalize_merchant(value: str) -> str:
    return (
        re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]", " ", value.casefold())).strip().title()
        or "Unknown Merchant"
    )


def _validated_expiry(offer, received_at: datetime):
    if offer.expires_at and offer.expires_at.date() < received_at.date():
        return replace(offer, expires_at=None, expiry_precision="unknown")
    return offer


def _newer_history(current: str | None, candidate: object) -> str | None:
    value = str(candidate or "") or None
    if value is None:
        return current
    if current is None:
        return value
    try:
        return value if int(value) > int(current) else current
    except ValueError:
        return value


def _campaign_fingerprint(merchant: str, offer, domain: str | None, thread_id: str | None) -> str:
    del thread_id  # Campaign reminders often start new Gmail threads.
    expiry = offer.expires_at.date().isoformat() if offer.expires_at else ""
    path = (
        urlparse(offer.destination_url).path.strip("/").split("/")[0]
        if offer.destination_url
        else ""
    )
    key = "|".join(
        (
            merchant.casefold(),
            offer.offer_type,
            str(offer.percent_off or ""),
            str(offer.amount_off or ""),
            str(offer.minimum_spend or ""),
            (offer.promo_code or "").casefold(),
            expiry,
            domain or "",
            path.casefold(),
        )
    )
    return hashlib.sha256(key.encode()).hexdigest()
