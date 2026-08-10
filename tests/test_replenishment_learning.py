from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.replenishment_routes import summary
from app.config import Settings
from app.db import Base
from app.models import (
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    PlaidItem,
    PurchaseReceipt,
    ReplenishmentFeedback,
    ReplenishmentModelVersion,
    ReplenishmentPrediction,
)
from app.services.acquisition_service import AcquisitionService
from app.services.gmail_receipt_service import GmailReceiptService, _looks_like_receipt
from app.services.item_normalization_service import (
    ItemNormalizationService,
    normalize_item_name,
)
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.receipt_parser_service import (
    OpenAIReceiptParser,
    ParsedReceipt,
    ParsedReceiptItem,
)
from app.services.replenishment_model_service import ReplenishmentModelService
from app.services.replenishment_prediction_service import (
    ReplenishmentPredictionService,
    TrainingDatasetService,
    TrainingRow,
    adaptive_interval,
)
from app.services.replenishment_service import ReplenishmentService
from app.services.weekly_replenishment_service import WeeklyReplenishmentService


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'learning.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class StaticParser:
    def __init__(self, parsed: ParsedReceipt):
        self.parsed = parsed
        self.calls = 0

    def parse_attachment(self, content: bytes, mime_type: str, filename: str) -> ParsedReceipt:
        self.calls += 1
        return self.parsed

    def parse_text(self, text: str) -> ParsedReceipt:
        self.calls += 1
        return self.parsed


def item(db, name="Paper towels", cadence=30) -> HouseholdItem:
    value = HouseholdItem(name=name, cadence_days=cadence, enabled=True)
    db.add(value)
    db.commit()
    db.refresh(value)
    return value


def parsed_receipt(
    *,
    name: str = "Paper towels",
    confidence: float = 0.99,
    total: int = 8742,
    quantity: float | None = 12,
) -> ParsedReceipt:
    return ParsedReceipt(
        merchant="Costco Wholesale",
        purchased_at=datetime(2026, 8, 9, tzinfo=UTC),
        subtotal_cents=8000,
        tax_cents=742,
        total_cents=total,
        confidence=confidence,
        items=[
            ParsedReceiptItem(
                name=name,
                quantity=quantity,
                unit="roll",
                line_total_cents=2499,
                confidence=confidence,
            )
        ],
    )


def settings(**overrides) -> Settings:
    values = {
        "receipt_auto_match_confidence": 0.9,
        "receipt_possible_match_confidence": 0.65,
        "replenishment_ml_min_rows": 10,
        "replenishment_ml_min_validation_rows": 3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def add_history(db, household_item: HouseholdItem, days: list[int]) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    service = AcquisitionService(db)
    elapsed = 0
    service.record(household_item, acquired_at=start, source="test")
    for interval in days:
        elapsed += interval
        service.record(household_item, acquired_at=start + timedelta(days=elapsed), source="test")


def test_telegram_receipt_ingestion_parses_matches_and_preserves_raw_name(db):
    tracked = item(db)
    parser = StaticParser(parsed_receipt(name="KIRKLAND PAPER TOWELS 12RL"))
    service = ReceiptIngestionService(db, settings(), parser)

    receipt = service.ingest_attachment(
        source="telegram",
        source_external_id="message-1",
        content=b"image bytes",
        mime_type="image/jpeg",
        filename="receipt.jpg",
    )

    assert receipt.source == "telegram"
    assert receipt.items[0].raw_name == "KIRKLAND PAPER TOWELS 12RL"
    assert receipt.items[0].household_item_id == tracked.id
    assert receipt.items[0].normalized_name != receipt.items[0].raw_name
    assert not hasattr(receipt, "original_content")


def test_receipt_parser_uses_structured_vision_output():
    response_body = {
        "output_text": (
            '{"merchant":"Costco","purchased_at":"2026-08-09T10:00:00Z",'
            '"subtotal_cents":1000,"tax_cents":80,"total_cents":1080,'
            '"currency":"USD","confidence":0.98,"items":[{"name":"Eggs",'
            '"quantity":12,"unit":"count","unit_price_cents":null,'
            '"line_total_cents":1080,"brand":null,"category":"grocery",'
            '"confidence":0.99,"is_household_purchase":true}]}'
        )
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert b"input_image" in request.content
        return httpx.Response(200, json=response_body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    parser = OpenAIReceiptParser(
        settings(receipt_parser_provider="openai", openai_api_key="test-key"), client
    )
    result = parser.parse_attachment(b"private image", "image/jpeg", "receipt.jpg")
    assert result.items[0].name == "Eggs"
    assert result.items[0].quantity == 12


def test_line_item_normalization_and_saved_alias_are_reused(db):
    tracked = item(db, "Laundry detergent")
    normalizer = ItemNormalizationService(db)
    assert normalize_item_name("TIDE PODS 42 CT") == "tide pods"
    normalizer.learn_alias(tracked, "TIDE PODS 42 CT", merchant="Target")
    db.commit()
    match = normalizer.match("TIDE PODS 42 CT", "Target")
    assert match.household_item.id == tracked.id
    assert match.source == "alias"


def test_receipt_confirmation_creates_immutable_quantity_aware_acquisition(db):
    tracked = item(db)
    service = ReceiptIngestionService(db, settings(), StaticParser(parsed_receipt()))
    receipt = service.ingest_text(source="gmail", source_external_id="gmail-1", text="receipt one")
    service.confirm(receipt.id)
    acquisition = db.scalar(select(HouseholdItemAcquisition))
    assert acquisition.household_item_id == tracked.id
    assert acquisition.quantity == 12
    assert acquisition.unit == "roll"
    assert acquisition.source == "receipt_gmail"


def test_low_confidence_or_unmatched_lines_do_not_contaminate_history(db):
    item(db)
    service = ReceiptIngestionService(
        db, settings(), StaticParser(parsed_receipt(name="unrelated shirt", confidence=0.4))
    )
    receipt = service.ingest_text(source="telegram", source_external_id="low-1", text="unclear")
    service.confirm(receipt.id)
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 0


def test_unmatched_receipt_line_can_start_tracking_a_new_household_item(db):
    service = ReceiptIngestionService(
        db, settings(), StaticParser(parsed_receipt(name="BRITANNIA TOASTED JUJI RUSK"))
    )
    receipt = service.ingest_text(
        source="telegram", source_external_id="new-staple-1", text="receipt"
    )
    line = receipt.items[0]
    assert line.household_item_id is None

    tracked, updated_line = service.track_line_as_new_household_item(
        receipt.id,
        line.id,
        name="Toasted rusk",
        cadence_days=21,
        replenishment_mode="either",
    )

    assert tracked.name == "Toasted rusk"
    assert tracked.cadence_days == 21
    assert updated_line.household_item_id == tracked.id
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 0

    service.confirm(receipt.id)
    acquisition = db.scalar(select(HouseholdItemAcquisition))
    assert acquisition.household_item_id == tracked.id
    assert acquisition.user_confirmed is True


def test_duplicate_external_id_is_idempotent_and_skips_reparse(db):
    item(db)
    parser = StaticParser(parsed_receipt())
    service = ReceiptIngestionService(db, settings(), parser)
    first = service.ingest_text(source="gmail", source_external_id="m-1", text="same")
    second = service.ingest_text(source="gmail", source_external_id="m-1", text="changed")
    assert first.id == second.id
    assert parser.calls == 1


def test_duplicate_receipt_hash_across_sources_is_deduplicated(db):
    item(db)
    parser = StaticParser(parsed_receipt())
    service = ReceiptIngestionService(db, settings(), parser)
    first = service.ingest_attachment(
        source="telegram",
        source_external_id="tg-1",
        content=b"same receipt",
        mime_type="image/jpeg",
        filename="a.jpg",
    )
    second = service.ingest_attachment(
        source="gmail",
        source_external_id="gm-1",
        content=b"same receipt",
        mime_type="image/jpeg",
        filename="b.jpg",
    )
    assert first.id == second.id


def test_plaid_reconciliation_links_receipt_but_plaid_alone_creates_no_product(db):
    tracked = item(db)
    plaid_item = PlaidItem(item_id="plaid-item", access_token_encrypted="encrypted")
    db.add(plaid_item)
    db.flush()
    transaction = ExpenseTransaction(
        plaid_transaction_id="tx-1",
        plaid_item_id=plaid_item.id,
        merchant_name="COSTCO WHSE #123",
        name="COSTCO",
        amount_cents=8742,
        date=date(2026, 8, 9),
    )
    db.add(transaction)
    db.commit()
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 0

    service = ReceiptIngestionService(db, settings(), StaticParser(parsed_receipt()))
    receipt = service.ingest_text(
        source="gmail", source_external_id="reconciled", text="costco order"
    )
    assert receipt.transaction_id == transaction.id
    service.confirm(receipt.id)
    assert db.scalar(select(HouseholdItemAcquisition)).household_item_id == tracked.id


def test_telegram_gmail_plaid_representations_create_one_acquisition(db):
    item(db)
    plaid_item = PlaidItem(item_id="plaid-item-2", access_token_encrypted="encrypted")
    db.add(plaid_item)
    db.flush()
    db.add(
        ExpenseTransaction(
            plaid_transaction_id="tx-2",
            plaid_item_id=plaid_item.id,
            merchant_name="Costco",
            name="Costco",
            amount_cents=8742,
            date=date(2026, 8, 9),
        )
    )
    db.commit()
    parser = StaticParser(parsed_receipt())
    service = ReceiptIngestionService(db, settings(), parser)
    telegram = service.ingest_text(
        source="telegram", source_external_id="tg-dedupe", text="telegram representation"
    )
    gmail = service.ingest_text(
        source="gmail", source_external_id="gm-dedupe", text="gmail representation"
    )
    service.confirm(telegram.id)
    service.confirm(gmail.id)
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 1


def test_still_have_is_feedback_not_purchase_and_delays_baseline(db):
    tracked = item(db, cadence=10)
    acquisition = AcquisitionService(db)
    last = datetime(2026, 8, 1, tzinfo=UTC)
    acquisition.record(tracked, acquired_at=last, source="manual")
    before = ReplenishmentPredictionService(db).predict_item(
        tracked, now=datetime(2026, 8, 5, tzinfo=UTC)
    )
    ReplenishmentService(db, SimpleNamespace(household_snooze_days=7)).still_have(
        tracked.id, now=datetime(2026, 8, 5, tzinfo=UTC)
    )
    after = ReplenishmentPredictionService(db).predict_item(
        tracked, now=datetime(2026, 8, 5, tzinfo=UTC)
    )
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 1
    assert db.scalar(select(func.count(ReplenishmentFeedback.id))) == 1
    assert after.predicted_need_at > before.predicted_need_at


def test_predictions_are_persisted_and_closed_against_actual_purchase(db):
    tracked = item(db, cadence=7)
    AcquisitionService(db).record(
        tracked, acquired_at=datetime(2026, 8, 1, tzinfo=UTC), source="manual"
    )
    prediction = ReplenishmentPredictionService(db).predict_item(
        tracked, now=datetime(2026, 8, 2, tzinfo=UTC)
    )
    AcquisitionService(db).record(
        tracked, acquired_at=datetime(2026, 8, 10, tzinfo=UTC), source="manual"
    )
    db.refresh(prediction)
    assert prediction.actual_next_acquisition_at is not None
    assert prediction.error_days == 2


def test_adaptive_cadence_uses_robust_recent_intervals():
    tracked = SimpleNamespace(cadence_days=30)
    assert adaptive_interval(tracked, [37, 35, 36]) == pytest.approx(36.0)
    assert adaptive_interval(tracked, []) == 30


def test_training_rows_use_only_information_available_at_observation_time(db):
    tracked = item(db, cadence=10)
    add_history(db, tracked, [10, 11, 12])
    future = datetime(2027, 1, 1, tzinfo=UTC)
    AcquisitionService(db).feedback(tracked, "still_have", occurred_at=future)
    rows = TrainingDatasetService(db).rows()
    assert len(rows) == 2
    assert rows[0].features[6] == 0
    assert rows[0].target_days == 11


def test_sparse_data_uses_statistical_fallback_without_model(db):
    tracked = item(db, cadence=14)
    add_history(db, tracked, [14])
    model = ReplenishmentModelService(db, settings(replenishment_ml_min_rows=20))
    assert model.train_and_evaluate() is None
    prediction = ReplenishmentPredictionService(db).predict_item(tracked)
    assert prediction.method in {"adaptive_median", "configured_cadence"}


def test_candidate_training_records_baseline_comparison_and_promotion(db, monkeypatch):
    rows = [
        TrainingRow(
            1,
            datetime(2026, 1, index + 1, tzinfo=UTC),
            [float(index)] * 10,
            float(index),
            100.0,
            100.0,
        )
        for index in range(1, 21)
    ]
    monkeypatch.setattr(TrainingDatasetService, "rows", lambda self: rows)
    monkeypatch.setattr(
        "app.services.replenishment_model_service._fit_ridge",
        lambda training: {
            "means": [0] * 10,
            "scales": [1] * 10,
            "weights": [1] + [0] * 9,
            "intercept": 0,
        },
    )
    model = ReplenishmentModelService(db, settings()).train_and_evaluate()
    assert model.status == "active"
    assert model.metrics_json["model_mae_days"] < model.metrics_json["baseline_mae_days"]


def test_candidate_that_does_not_beat_baseline_is_rejected(db, monkeypatch):
    rows = [
        TrainingRow(1, datetime(2026, 1, index + 1, tzinfo=UTC), [0.0] * 10, 5.0, 5.0)
        for index in range(1, 21)
    ]
    monkeypatch.setattr(TrainingDatasetService, "rows", lambda self: rows)
    model = ReplenishmentModelService(db, settings()).train_and_evaluate()
    assert model.status == "rejected"
    assert model.metrics_json["promoted"] is False


def test_failed_training_preserves_active_model(db, monkeypatch):
    active = ReplenishmentModelVersion(
        version="champion",
        algorithm="ridge",
        status="active",
        training_rows=20,
        metrics_json={},
        artifact_json={},
    )
    db.add(active)
    db.commit()
    monkeypatch.setattr(
        TrainingDatasetService, "rows", lambda self: (_ for _ in ()).throw(RuntimeError("bad"))
    )
    with pytest.raises(RuntimeError):
        ReplenishmentModelService(db, settings()).train_and_evaluate()
    db.refresh(active)
    assert active.status == "active"


def test_weekly_retraining_is_idempotent_and_logs_dataset_and_metrics(db):
    tracked = item(db)
    add_history(db, tracked, [30, 31])
    service = WeeklyReplenishmentService(db)
    first = service.run(run_key="week-1", trigger="manual")
    second = service.run(run_key="week-1", trigger="manual")
    assert first.id == second.id
    assert first.status == "completed"
    assert first.dataset_size == 1
    assert "predictions_generated" in first.metrics_json


def test_user_correction_repairs_and_undo_removes_training_evidence(db):
    paper = item(db, "Paper towels")
    eggs = item(db, "Eggs", 7)
    service = AcquisitionService(db)
    original = service.record(paper, source="manual", quantity=12)
    corrected = service.correct(original.id, household_item=eggs, quantity=24, unit="count")
    db.refresh(original)
    assert original.voided_at is not None
    assert corrected.household_item_id == eggs.id
    service.undo(corrected.id)
    assert (
        db.scalar(
            select(func.count(HouseholdItemAcquisition.id)).where(
                HouseholdItemAcquisition.voided_at.is_(None)
            )
        )
        == 0
    )


def test_gmail_marketing_filter_rejects_ads_and_accepts_real_receipts():
    assert not _looks_like_receipt(
        "Weekly sale", "store@example.com", "Shop now. Weekly ad. Unsubscribe"
    )
    assert _looks_like_receipt(
        "Your receipt", "orders@example.com", "Order number 12. Subtotal $10. Total $11"
    )


def test_gmail_sync_is_narrow_and_message_id_idempotent(db):
    encoded = base64.urlsafe_b64encode(b"Order number 12. Subtotal $10. Total $11. Receipt").decode(
        "ascii"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token"})
        if request.url.path.endswith("/messages"):
            assert "receipt" in request.url.params["q"].casefold()
            return httpx.Response(200, json={"messages": [{"id": "gmail-message-1"}]})
        return httpx.Response(
            200,
            json={
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Your receipt"},
                        {"name": "From", "value": "orders@example.com"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": encoded},
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    config = settings(
        gmail_receipt_sync_enabled=True,
        gmail_client_id="id",
        gmail_client_secret="secret",
        gmail_refresh_token="refresh",
    )
    service = GmailReceiptService(db, config, client)
    service.sync()
    service.sync()
    assert db.scalar(select(func.count(PurchaseReceipt.id))) == 1


def test_prediction_history_keeps_multiple_runs_instead_of_overwriting(db):
    tracked = item(db, cadence=7)
    AcquisitionService(db).record(
        tracked, acquired_at=datetime(2026, 8, 1, tzinfo=UTC), source="manual"
    )
    prediction = ReplenishmentPredictionService(db)
    prediction.predict_item(tracked, now=datetime(2026, 8, 2, tzinfo=UTC))
    prediction.predict_item(tracked, now=datetime(2026, 8, 3, tzinfo=UTC))
    assert db.scalar(select(func.count(ReplenishmentPrediction.id))) == 2


def test_learning_summary_exposes_weekly_needs_and_accuracy_without_raw_content(db):
    tracked = item(db, cadence=7)
    AcquisitionService(db).record(
        tracked, acquired_at=datetime(2026, 8, 1, tzinfo=UTC), source="manual"
    )
    ReplenishmentPredictionService(db).predict_item(tracked, now=datetime(2026, 8, 7, tzinfo=UTC))
    result = summary(db)
    assert result["learning"]["confirmed_acquisitions"] == 1
    assert result["accuracy"]["evaluated_predictions"] == 0
    assert "email_content" not in result
