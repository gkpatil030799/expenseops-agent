from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import (
    DataConsent,
    GmailAccount,
    PurchaseReceipt,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services import gmail_receipt_service
from app.services.gmail_receipt_service import GmailReceiptService
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.receipt_parser_service import ParsedReceipt, ParsedReceiptItem
from app.tenancy import TenantContext, set_session_tenant


class _ProbeParser:
    def __init__(self) -> None:
        self.calls = 0

    def parse_artifact(self, _artifact):
        self.calls += 1
        return ParsedReceipt(
            merchant="Target",
            purchased_at=datetime(2026, 8, 17, 12, tzinfo=UTC),
            subtotal_cents=500,
            tax_cents=0,
            total_cents=500,
            confidence=0.99,
            items=[
                ParsedReceiptItem(
                    name="Dish soap",
                    line_total_cents=500,
                    confidence=0.99,
                )
            ],
        )

    def parse_text(self, _text: str):
        return self.parse_artifact(None)


def _image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (640, 960), "white").save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture
def scoped_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'day16-receipt-privacy.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(email="receipt-owner@example.test", display_name="Receipt owner")
    db.add(user)
    db.flush()
    workspace = Workspace(name="Receipt workspace", created_by_user_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
            is_default=True,
        )
    )
    db.commit()
    set_session_tenant(db, TenantContext(user.id, workspace.id))
    try:
        yield db, user, workspace
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("source", ["web", "telegram"])
def test_direct_image_model_path_requires_owner_scoped_consent_before_provider(
    scoped_db,
    monkeypatch,
    source: str,
) -> None:
    db, user, workspace = scoped_db
    parser = _ProbeParser()
    monkeypatch.setattr(
        "app.services.receipt_ingestion_service.build_receipt_parser",
        lambda _settings: parser,
    )
    settings = Settings(
        _env_file=None,
        openai_api_key="synthetic",
        receipt_parser_provider="openai",
    )
    service = ReceiptIngestionService(db, settings)

    with pytest.raises(PermissionError, match="model_receipt_processing_consent_required"):
        service.ingest_attachment(
            source=source,
            source_external_id=f"{source}-without-consent",
            content=_image(),
            mime_type="image/jpeg",
            filename="receipt.jpg",
        )
    assert parser.calls == 0
    assert db.scalar(select(func.count(PurchaseReceipt.id))) == 0

    db.add(
        DataConsent(
            workspace_id=workspace.id,
            user_id=user.id,
            purpose="model_receipt_processing",
            granted=True,
            policy_version="test",
        )
    )
    db.commit()
    receipt = service.ingest_attachment(
        source=source,
        source_external_id=f"{source}-with-consent",
        content=_image(),
        mime_type="image/jpeg",
        filename="receipt.jpg",
    )

    assert parser.calls == 1
    assert receipt.owner_user_id == user.id


def test_gmail_account_owner_is_the_receipt_model_consent_subject(
    scoped_db,
    monkeypatch,
) -> None:
    db, user, workspace = scoped_db
    db.add(
        GmailAccount(
            workspace_id=workspace.id,
            user_id=user.id,
            google_user_id="owner@gmail.test",
            refresh_token_encrypted="unused-in-this-test",
            enabled=True,
        )
    )
    db.commit()
    settings = Settings(
        _env_file=None,
        gmail_user_id="owner@gmail.test",
        gmail_receipt_sync_enabled=True,
    )
    monkeypatch.setattr(
        gmail_receipt_service,
        "gmail_settings_for_session",
        lambda _db, configured: configured,
    )

    service = GmailReceiptService(db, settings)

    assert service._active_account_owner_user_id == user.id


def test_text_receipt_model_path_rechecks_consent_before_provider(
    scoped_db,
    monkeypatch,
) -> None:
    db, _user, _workspace = scoped_db
    parser = _ProbeParser()
    monkeypatch.setattr(
        "app.services.receipt_ingestion_service.build_receipt_parser",
        lambda _settings: parser,
    )
    service = ReceiptIngestionService(
        db,
        Settings(
            _env_file=None,
            openai_api_key="synthetic",
            receipt_parser_provider="openai",
        ),
    )

    with pytest.raises(PermissionError, match="model_receipt_processing_consent_required"):
        service.ingest_text(
            source="gmail",
            source_external_id="gmail-text-without-consent",
            text="Receipt email body with private line items",
        )

    assert parser.calls == 0
