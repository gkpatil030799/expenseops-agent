from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.models import (
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    HouseholdItemAlias,
    PlaidItem,
    ReplenishmentModelVersion,
)
from app.services.acquisition_service import AcquisitionService
from app.services.quantity_normalization_service import normalize_quantity
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.receipt_parser_service import ParsedReceipt, ParsedReceiptItem
from app.services.replenishment_model_service import ReplenishmentModelService
from app.services.replenishment_prediction_service import (
    ReplenishmentPredictionService,
    TrainingDatasetService,
    TrainingRow,
)
from app.services.training_eligibility_service import training_eligibility


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reliability.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def config(**overrides) -> Settings:
    values = {
        "receipt_auto_match_confidence": 0.9,
        "receipt_possible_match_confidence": 0.65,
        "replenishment_ml_min_rows": 10,
        "replenishment_ml_min_validation_rows": 3,
        "replenishment_walk_forward_min_rows": 60,
        "replenishment_model_min_mae_improvement_pct": 10,
        "replenishment_model_min_mae_improvement_days": 1,
        "replenishment_max_feedback_cadence_adjustment_pct": 25,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def household_item(db, name: str = "Paper towels", cadence: int = 30) -> HouseholdItem:
    value = HouseholdItem(name=name, cadence_days=cadence, enabled=True)
    db.add(value)
    db.commit()
    db.refresh(value)
    return value


def rows(
    count: int,
    *,
    target: float,
    adaptive: float,
    configured: float,
    width: int = 4,
) -> list[TrainingRow]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        TrainingRow(
            household_item_id=1,
            observed_at=start + timedelta(days=index),
            features=[configured] + [0.0] * (width - 1),
            target_days=target,
            baseline_days=adaptive,
            configured_baseline_days=configured,
        )
        for index in range(count)
    ]


def constant_artifact(value: float, width: int = 4) -> dict:
    return {
        "means": [0.0] * width,
        "scales": [1.0] * width,
        "weights": [0.0] * width,
        "intercept": value,
    }


def patch_training(monkeypatch, training_rows: list[TrainingRow], prediction: float) -> None:
    monkeypatch.setattr(TrainingDatasetService, "rows", lambda self: training_rows)
    monkeypatch.setattr(
        "app.services.replenishment_model_service._fit_ridge",
        lambda selected: constant_artifact(prediction, len(selected[0].features)),
    )


def test_tiny_absolute_mae_improvement_does_not_promote(db, monkeypatch):
    patch_training(monkeypatch, rows(20, target=10, adaptive=11, configured=12), 10.5)
    model = ReplenishmentModelService(db, config()).train_and_evaluate()
    assert model.status == "rejected"
    assert "below the required 1.00 days" in model.metrics_json["decision_reason"]


def test_meaningful_mae_improvement_can_promote(db, monkeypatch):
    patch_training(monkeypatch, rows(20, target=10, adaptive=14, configured=15), 10)
    model = ReplenishmentModelService(db, config()).train_and_evaluate()
    assert model.status == "active"
    assert model.metrics_json["improvement_days"] == 4
    assert model.metrics_json["improvement_pct"] == 100


def test_candidate_is_compared_to_strongest_baseline(db, monkeypatch):
    patch_training(monkeypatch, rows(20, target=10, adaptive=12, configured=20), 11.5)
    model = ReplenishmentModelService(db, config()).train_and_evaluate()
    assert model.status == "rejected"
    assert model.metrics_json["best_baseline"] == "adaptive_cadence"
    assert model.metrics_json["baseline_maes"]["configured_cadence"] == 10
    assert model.metrics_json["baseline_maes"]["adaptive_cadence"] == 2


def test_active_model_is_strongest_baseline_and_survives_rejection(db, monkeypatch):
    active = ReplenishmentModelVersion(
        version="champion",
        algorithm="ridge",
        status="active",
        training_rows=20,
        metrics_json={},
        artifact_json=constant_artifact(10.2),
    )
    db.add(active)
    db.commit()
    patch_training(monkeypatch, rows(20, target=10, adaptive=14, configured=15), 10.1)
    candidate = ReplenishmentModelService(db, config()).train_and_evaluate()
    db.refresh(active)
    assert candidate.status == "rejected"
    assert candidate.metrics_json["best_baseline"] == "active_model"
    assert active.status == "active"


def test_small_dataset_uses_chronological_holdout(db, monkeypatch):
    patch_training(monkeypatch, rows(20, target=10, adaptive=14, configured=15), 10)
    model = ReplenishmentModelService(db, config()).train_and_evaluate()
    assert model.metrics_json["validation_method"] == "chronological_holdout"
    assert model.metrics_json["validation_rows"] == 4
    assert model.metrics_json["data_confidence_level"] == "low"


def test_large_dataset_uses_expanding_walk_forward(db, monkeypatch):
    patch_training(monkeypatch, rows(60, target=10, adaptive=14, configured=15), 10)
    model = ReplenishmentModelService(db, config()).train_and_evaluate()
    assert model.metrics_json["validation_method"] == "expanding_walk_forward"
    assert model.metrics_json["validation_folds"] == 30
    assert model.metrics_json["data_confidence_level"] == "medium"


def test_historical_features_exclude_future_quantity_and_feedback(db):
    tracked = household_item(db, cadence=10)
    service = AcquisitionService(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    service.record(tracked, acquired_at=start, quantity=1, unit="gallon", source="manual")
    service.record(
        tracked, acquired_at=start + timedelta(days=10), quantity=1, unit="gallon", source="manual"
    )
    service.record(
        tracked, acquired_at=start + timedelta(days=20), quantity=2, unit="gallon", source="manual"
    )
    service.feedback(tracked, "still_have", occurred_at=start + timedelta(days=30))
    generated = TrainingDatasetService(db, config()).rows()
    assert generated[0].features[1] == 2
    assert generated[0].features[5] == 128
    assert generated[0].features[7] == 0


def test_later_cadence_edit_does_not_leak_into_historical_rows(db):
    tracked = household_item(db, cadence=10)
    service = AcquisitionService(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for offset in (0, 10, 20):
        service.record(tracked, acquired_at=start + timedelta(days=offset), source="manual")
    tracked.cadence_days = 45
    db.commit()
    generated = TrainingDatasetService(db, config()).rows()
    assert generated[0].features[0] == 10
    assert generated[0].configured_baseline_days == 10


def test_quantity_normalization_converts_only_known_units():
    eggs = normalize_quantity(24, "count", source_confidence=0.95)
    milk = normalize_quantity(2, "gallons", source_confidence=0.95)
    assert (eggs.quantity, eggs.unit, eggs.package_size) == (24, "count", 24)
    assert (milk.quantity, milk.unit) == (256, "fluid_ounce")


def test_ambiguous_or_uncertain_quantity_remains_unknown():
    ambiguous = normalize_quantity(2, "pack", source_confidence=0.99)
    uncertain = normalize_quantity(12, "roll", source_confidence=0.6)
    assert ambiguous.quantity is None
    assert uncertain.quantity is None


def test_low_confidence_receipt_is_excluded_from_training(db):
    tracked = household_item(db)
    event = AcquisitionService(db).record(
        tracked,
        source="receipt_telegram",
        confidence=0.4,
        confirmed=True,
        user_confirmed=True,
    )
    result = training_eligibility(event, config())
    assert result.eligible is False
    assert result.reason == "confidence_below_training_threshold"


def test_confirmed_medium_confidence_receipt_is_training_eligible(db):
    tracked = household_item(db)
    event = AcquisitionService(db).record(
        tracked,
        source="receipt_telegram",
        confidence=0.75,
        confirmed=True,
        user_confirmed=True,
    )
    result = training_eligibility(event, config())
    assert result.eligible is True
    assert result.reason == "user_confirmed_medium_confidence"


class Parser:
    def __init__(self, parsed: ParsedReceipt):
        self.parsed = parsed

    def parse_text(self, text: str) -> ParsedReceipt:
        return self.parsed

    def parse_attachment(self, content: bytes, mime_type: str, filename: str) -> ParsedReceipt:
        return self.parsed


def receipt_parser(name: str = "Toilet paper") -> Parser:
    return Parser(
        ParsedReceipt(
            merchant="Costco",
            purchased_at=datetime(2026, 8, 9, tzinfo=UTC),
            subtotal_cents=2000,
            tax_cents=0,
            total_cents=2000,
            confidence=0.99,
            items=[
                ParsedReceiptItem(
                    name=name,
                    quantity=12,
                    unit="roll",
                    confidence=0.99,
                )
            ],
        )
    )


def test_receipt_correction_moves_evidence_and_soft_invalidates_alias(db):
    wrong = household_item(db, "Toilet paper")
    correct = household_item(db, "Paper towels")
    ingestion = ReceiptIngestionService(db, config(), receipt_parser())
    receipt = ingestion.ingest_text(
        source="telegram", source_external_id="correction", text="receipt"
    )
    ingestion.confirm(receipt.id)
    original = db.scalar(select(HouseholdItemAcquisition))
    ingestion.update_line_match(receipt.id, receipt.items[0].id, correct.id)
    acquisitions = list(
        db.execute(select(HouseholdItemAcquisition).order_by(HouseholdItemAcquisition.id)).scalars()
    )
    aliases = list(db.execute(select(HouseholdItemAlias)).scalars())
    assert original.household_item_id == wrong.id
    assert acquisitions[0].voided_at is not None
    assert acquisitions[1].household_item_id == correct.id
    assert acquisitions[1].supersedes_acquisition_id == acquisitions[0].id
    assert any(alias.household_item_id == wrong.id and alias.voided_at for alias in aliases)
    assert any(alias.household_item_id == correct.id and not alias.voided_at for alias in aliases)


def test_acquisition_undo_removes_event_from_dataset_and_prediction_outcomes(db):
    tracked = household_item(db, cadence=10)
    service = AcquisitionService(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    first = service.record(tracked, acquired_at=start, source="manual")
    prediction = ReplenishmentPredictionService(db, config()).predict_item(
        tracked, now=start + timedelta(days=1)
    )
    second = service.record(tracked, acquired_at=start + timedelta(days=10), source="manual")
    service.record(tracked, acquired_at=start + timedelta(days=20), source="manual")
    db.refresh(prediction)
    assert prediction.actual_next_acquisition_at == second.acquired_at
    service.undo(second.id)
    db.refresh(prediction)
    assert prediction.actual_next_acquisition_at.date() == (start + timedelta(days=20)).date()
    assert first.voided_at is None


@pytest.mark.parametrize("plaid_first", [True, False])
def test_ingestion_order_cannot_duplicate_logical_acquisition(db, plaid_first):
    household_item(db)
    parser = Parser(
        ParsedReceipt(
            merchant="Costco",
            purchased_at=datetime(2026, 8, 9, tzinfo=UTC),
            subtotal_cents=8742,
            tax_cents=0,
            total_cents=8742,
            confidence=0.99,
            items=[ParsedReceiptItem(name="Paper towels", confidence=0.99)],
        )
    )

    def add_plaid() -> None:
        plaid = PlaidItem(item_id=f"plaid-{plaid_first}", access_token_encrypted="encrypted")
        db.add(plaid)
        db.flush()
        db.add(
            ExpenseTransaction(
                plaid_transaction_id=f"tx-{plaid_first}",
                plaid_item_id=plaid.id,
                merchant_name="Costco",
                name="Costco",
                amount_cents=8742,
                date=date(2026, 8, 9),
            )
        )
        db.commit()

    if plaid_first:
        add_plaid()
    service = ReceiptIngestionService(db, config(), parser)
    first = service.ingest_text(
        source="gmail", source_external_id="gmail-order", text="gmail receipt"
    )
    service.confirm(first.id)
    if not plaid_first:
        add_plaid()
    second = service.ingest_text(
        source="telegram", source_external_id="telegram-order", text="telegram receipt"
    )
    service.confirm(second.id)
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 1


def test_prediction_uses_next_valid_acquisition_once_and_rebuilds_after_undo(db):
    tracked = household_item(db, cadence=7)
    service = AcquisitionService(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    service.record(tracked, acquired_at=start, source="manual")
    prediction = ReplenishmentPredictionService(db, config()).predict_item(
        tracked, now=start + timedelta(days=2)
    )
    next_valid = service.record(tracked, acquired_at=start + timedelta(days=8), source="manual")
    service.record(
        tracked,
        acquired_at=start + timedelta(days=9),
        source="receipt_gmail",
        confidence=0.2,
        confirmed=True,
    )
    later = service.record(tracked, acquired_at=start + timedelta(days=15), source="manual")
    db.refresh(prediction)
    assert prediction.actual_next_acquisition_at == next_valid.acquired_at
    service.undo(next_valid.id)
    db.refresh(prediction)
    assert prediction.actual_next_acquisition_at == later.acquired_at


@pytest.mark.parametrize(
    "artifact",
    [
        {"broken": True},
        constant_artifact(float("nan"), 11),
        constant_artifact(10_000, 11),
    ],
)
def test_invalid_or_extreme_active_model_falls_back_safely(db, artifact):
    tracked = household_item(db, cadence=10)
    AcquisitionService(db).record(
        tracked, acquired_at=datetime(2026, 1, 1, tzinfo=UTC), source="manual"
    )
    db.add(
        ReplenishmentModelVersion(
            version="bad-model",
            algorithm="ridge",
            status="active",
            training_rows=100,
            metrics_json={},
            artifact_json=artifact,
        )
    )
    db.commit()
    prediction = ReplenishmentPredictionService(db, config()).predict_item(
        tracked, now=datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert prediction.model_version_id is None
    assert prediction.method == "configured_cadence"


def test_still_have_adjustment_is_bounded(db):
    tracked = household_item(db, cadence=40)
    service = AcquisitionService(db)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    service.record(tracked, acquired_at=start, source="manual")
    for day in range(2, 20):
        service.feedback(tracked, "still_have", occurred_at=start + timedelta(days=day))
    prediction = ReplenishmentPredictionService(db, config()).predict_item(
        tracked, now=start + timedelta(days=20)
    )
    predicted_interval = (prediction.predicted_need_at - start.replace(tzinfo=None)).days
    assert predicted_interval <= 50


def test_promotion_audit_trail_contains_metrics_and_reason(db, monkeypatch):
    patch_training(monkeypatch, rows(20, target=10, adaptive=14, configured=15), 10)
    model = ReplenishmentModelService(db, config()).train_and_evaluate()
    assert model.metrics_json["decision_reason"].startswith("Candidate MAE")
    assert model.metrics_json["baseline_maes"]["adaptive_cadence"] == 4
    assert model.metrics_json["candidate_mae_days"] == 0


def test_manual_model_rollback_preserves_versions_and_one_active(db):
    previous = ReplenishmentModelVersion(
        version="previous",
        algorithm="ridge",
        status="superseded",
        training_rows=60,
        metrics_json={},
        artifact_json=constant_artifact(10),
    )
    current = ReplenishmentModelVersion(
        version="current",
        algorithm="ridge",
        status="active",
        training_rows=80,
        metrics_json={},
        artifact_json=constant_artifact(11),
    )
    db.add_all([previous, current])
    db.commit()
    activated = ReplenishmentModelService(db, config()).rollback_to(previous.id)
    db.refresh(current)
    assert activated.status == "active"
    assert current.status == "rolled_back"
    assert (
        db.scalar(
            select(func.count(ReplenishmentModelVersion.id)).where(
                ReplenishmentModelVersion.status == "active"
            )
        )
        == 1
    )
