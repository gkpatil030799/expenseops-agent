from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.job_tenancy import gmail_settings_for_session
from app.models import (
    GmailAccount,
    GmailSyncCheckpoint,
    PurchaseReceipt,
    User,
    WorkspaceMembership,
    utc_now,
)
from app.services.data_lifecycle_service import gmail_consent_granted
from app.services.gmail_client_service import GmailClient
from app.services.receipt_ingestion_service import ReceiptIngestionService


@dataclass(frozen=True)
class GmailSyncResult:
    scanned: int
    ingested: int
    skipped: int
    receipts: list[PurchaseReceipt]


@dataclass(frozen=True)
class GmailReceiptAttachment:
    part_id: str
    filename: str
    mime_type: str
    size: int
    attachment_id: str | None
    inline_data: str | None


class GmailReceiptService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ):
        self.db = db
        self.settings = gmail_settings_for_session(db, settings or get_settings())
        self.client = client
        self.gmail = GmailClient(self.settings, client)

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.gmail_receipt_sync_enabled
            and self.settings.gmail_client_id
            and self.settings.gmail_client_secret
            and self.settings.gmail_refresh_token
            and gmail_consent_granted(self.db, "gmail_receipts")
            and (
                self.settings.receipt_parser_provider != "openai"
                or gmail_consent_granted(self.db, "model_receipt_processing")
            )
        )

    def sync(self, *, max_results: int = 25) -> GmailSyncResult:
        if not self.configured:
            raise ValueError("gmail_receipt_sync_not_configured")
        token = self.gmail.access_token()
        headers = {"Authorization": f"Bearer {token}"}
        checkpoint = self.db.scalar(
            select(GmailSyncCheckpoint).where(
                GmailSyncCheckpoint.account_key == self._checkpoint_key
            )
        )
        if checkpoint is None:
            checkpoint = GmailSyncCheckpoint(account_key=self._checkpoint_key)
            self.db.add(checkpoint)
            self.db.flush()
        params = {"q": self.settings.gmail_receipt_query, "maxResults": max_results}
        if not checkpoint.initial_backfill_complete and checkpoint.backfill_page_token:
            params["pageToken"] = checkpoint.backfill_page_token
        response = self._request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/{self.settings.gmail_user_id}/messages",
            headers=headers,
            params=params,
        ).json()
        messages = response.get("messages", [])
        ingested: list[PurchaseReceipt] = []
        skipped = 0
        for summary in messages:
            message_id = str(summary.get("id") or "")
            if not message_id:
                skipped += 1
                continue
            if self.db.scalar(
                select(PurchaseReceipt.id).where(
                    PurchaseReceipt.source == "gmail",
                    PurchaseReceipt.source_external_id == message_id,
                )
            ):
                skipped += 1
                continue
            message = self.gmail.get_message(message_id, token)
            subject, sender, body = _message_text(message)
            if not _looks_like_receipt(subject, sender, body):
                skipped += 1
                continue
            attachments = _message_receipt_attachments(message)
            selected = attachments[0] if attachments else None
            ingestion = ReceiptIngestionService(
                self.db,
                self.settings,
                owner_user_id=self._active_account_owner_user_id,
            )
            if selected is not None and not _looks_like_structured_receipt(body):
                content = _attachment_content(selected, message_id, token, self.gmail)
                receipt = ingestion.ingest_attachment(
                    source="gmail",
                    source_external_id=message_id,
                    content=content,
                    mime_type=selected.mime_type,
                    filename=selected.filename,
                    auto_confirm_high_confidence=True,
                )
            else:
                receipt = ingestion.ingest_text(
                    source="gmail",
                    source_external_id=message_id,
                    text=f"Subject: {subject}\nFrom: {sender}\n\n{body}",
                    auto_confirm_high_confidence=True,
                )
            ingested.append(receipt)
        checkpoint.backfill_page_token = response.get("nextPageToken")
        checkpoint.initial_backfill_complete = checkpoint.backfill_page_token is None
        checkpoint.updated_at = utc_now()
        self.db.commit()
        return GmailSyncResult(
            scanned=len(messages),
            ingested=len(ingested),
            skipped=skipped,
            receipts=ingested,
        )

    def status(self) -> dict:
        checkpoint = self.db.scalar(
            select(GmailSyncCheckpoint).where(
                GmailSyncCheckpoint.account_key == self._checkpoint_key
            )
        )
        latest_receipt_at = self.db.scalar(
            select(PurchaseReceipt.created_at)
            .where(PurchaseReceipt.source == "gmail")
            .order_by(PurchaseReceipt.created_at.desc())
            .limit(1)
        )
        return {
            "configured": self.configured,
            "last_successful_sync_at": checkpoint.updated_at if checkpoint else None,
            "latest_receipt_at": latest_receipt_at,
        }

    @property
    def _checkpoint_key(self) -> str:
        return f"{self.settings.gmail_user_id}:receipts"

    @property
    def _active_account_owner_user_id(self) -> int | None:
        workspace_id = self.db.info.get("workspace_id")
        if not isinstance(workspace_id, int):
            return None
        return self.db.scalar(
            select(GmailAccount.user_id)
            .join(User, User.id == GmailAccount.user_id)
            .join(
                WorkspaceMembership,
                (WorkspaceMembership.workspace_id == GmailAccount.workspace_id)
                & (WorkspaceMembership.user_id == GmailAccount.user_id),
            )
            .where(
                GmailAccount.workspace_id == workspace_id,
                GmailAccount.google_user_id == self.settings.gmail_user_id,
                GmailAccount.enabled.is_(True),
                User.status == "active",
            )
            .limit(1)
        )

    def _access_token(self) -> str:
        return self.gmail.access_token()

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        return self.gmail.request(method, url, **kwargs)


def _message_text(message: dict) -> tuple[str, str, str]:
    payload = message.get("payload") or {}
    headers = {
        str(header.get("name") or "").casefold(): str(header.get("value") or "")
        for header in payload.get("headers", [])
    }
    parts: list[str] = []

    def visit(part: dict) -> None:
        mime = str(part.get("mimeType") or "")
        data = str((part.get("body") or {}).get("data") or "")
        if data and mime in {"text/plain", "text/html"}:
            try:
                decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
                    "utf-8", errors="replace"
                )
            except ValueError:
                return
            if mime == "text/html":
                decoded = html.unescape(re.sub(r"<[^>]+>", " ", decoded))
            parts.append(re.sub(r"\s+", " ", decoded).strip())
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    return headers.get("subject", ""), headers.get("from", ""), "\n".join(parts)[:30000]


def _looks_like_receipt(subject: str, sender: str, body: str) -> bool:
    combined = f"{subject} {body[:5000]}".casefold()
    purchase_signals = ("receipt", "order total", "your order", "purchase", "subtotal")
    marketing_signals = ("unsubscribe", "sale ends", "shop now", "weekly ad")
    has_purchase = any(signal in combined for signal in purchase_signals)
    only_marketing = any(signal in combined for signal in marketing_signals) and not any(
        signal in combined for signal in ("order number", "total", "payment")
    )
    return bool(sender.strip() and has_purchase and not only_marketing)


def _looks_like_structured_receipt(body: str) -> bool:
    compact = re.sub(r"\s+", " ", body).casefold()
    has_total_label = bool(re.search(r"\b(?:order total|grand total|subtotal|total)\b", compact))
    has_money = bool(re.search(r"(?:\$|usd\s*)\d{1,7}(?:[.,]\d{2})", compact))
    return len(compact) >= 80 and has_total_label and has_money


def _message_receipt_attachments(message: dict) -> list[GmailReceiptAttachment]:
    attachments: list[GmailReceiptAttachment] = []

    def visit(part: dict) -> None:
        mime_type = str(part.get("mimeType") or "").casefold()
        filename = str(part.get("filename") or "").strip()
        body = part.get("body") or {}
        attachment_id = str(body.get("attachmentId") or "").strip() or None
        inline_data = str(body.get("data") or "").strip() or None
        supported = mime_type in {
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/octet-stream",
        }
        if supported and (attachment_id or inline_data):
            attachments.append(
                GmailReceiptAttachment(
                    part_id=str(part.get("partId") or ""),
                    filename=filename or _attachment_filename(mime_type),
                    mime_type=mime_type,
                    size=max(0, int(body.get("size") or 0)),
                    attachment_id=attachment_id,
                    inline_data=inline_data,
                )
            )
        for child in part.get("parts", []):
            if isinstance(child, dict):
                visit(child)

    payload = message.get("payload") or {}
    if isinstance(payload, dict):
        visit(payload)
    return sorted(
        attachments,
        key=lambda item: (
            item.mime_type.startswith("image/"),
            item.size,
            item.part_id,
        ),
        reverse=True,
    )


def _attachment_content(
    attachment: GmailReceiptAttachment,
    message_id: str,
    token: str,
    gmail: GmailClient,
) -> bytes:
    if attachment.inline_data:
        try:
            return base64.urlsafe_b64decode(
                attachment.inline_data + "=" * (-len(attachment.inline_data) % 4)
            )
        except ValueError as exc:
            raise ValueError("gmail_attachment_invalid") from exc
    if attachment.attachment_id:
        return gmail.get_attachment(message_id, attachment.attachment_id, token)
    raise ValueError("gmail_attachment_empty")


def _attachment_filename(mime_type: str) -> str:
    extension = {
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime_type, "bin")
    return f"gmail-receipt.{extension}"
