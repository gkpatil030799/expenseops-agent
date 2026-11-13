from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import Base
from app.jobs import classification_finalizer as classification_finalizer_job
from app.models import (
    ClassificationActivityType,
    ClassificationAuthority,
    ClassificationDecisionRecord,
    ClassificationDecisionState,
    ClassificationSettings,
    DataConsent,
    ExpenseTransaction,
    HouseholdItemAcquisition,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentEligibility,
    SpendingParentCategory,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.services.autonomous_classification_service import AutonomousClassificationService
from app.services.classification_backfill_service import ClassificationBackfillService
from app.services.classification_finalizer_service import (
    FINALIZER_JOB_NAME,
    ClassificationFinalizerResult,
    ClassificationFinalizerService,
    run_finalizer_for_workspace,
)
from app.services.classification_model_service import (
    ClassificationModelError,
    ClassificationModelSuggestion,
)
from app.services.classification_taxonomy_service import (
    ClassificationSourceType,
    ClassificationTaxonomyService,
    build_classification_decision,
)
from app.services.job_lease_service import acquire_job_lease
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.receipt_parser_service import ParsedReceipt, ParsedReceiptItem
from app.services.transaction_service import TransactionService
from app.tenancy import TenantContext, set_session_tenant
from scripts import backfill_autonomous_classification


@pytest.fixture
def classification_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'classification-jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        users = [
            User(email="jobs-a@example.test", display_name="Jobs A"),
            User(email="jobs-b@example.test", display_name="Jobs B"),
        ]
        db.add_all(users)
        db.flush()
        workspaces = [
            Workspace(name="Jobs A", created_by_user_id=users[0].id),
            Workspace(name="Jobs B", created_by_user_id=users[1].id),
        ]
        db.add_all(workspaces)
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="owner",
                    is_default=True,
                )
                for user, workspace in zip(users, workspaces, strict=True)
            ]
        )
        plaid_items = [
            PlaidItem(
                workspace_id=workspace.id,
                item_id=f"jobs-plaid-{workspace.id}",
                owner_user_id=user.id,
            )
            for user, workspace in zip(users, workspaces, strict=True)
        ]
        db.add_all(plaid_items)
        db.add_all(
            [
                ClassificationSettings(
                    workspace_id=workspace.id,
                    autonomous_enabled=True,
                )
                for workspace in workspaces
            ]
        )
        db.commit()
        set_session_tenant(db, TenantContext(users[0].id, workspaces[0].id))
        yield (
            db,
            factory,
            {
                "user": users[0],
                "workspace": workspaces[0],
                "plaid": plaid_items[0],
                "other_user": users[1],
                "other_workspace": workspaces[1],
                "other_plaid": plaid_items[1],
            },
        )
    engine.dispose()


def _settings(**updates) -> Settings:
    return Settings(
        autonomous_classification_enabled=True,
        classification_finalizer_batch_size=20,
        classification_backfill_batch_size=20,
        **updates,
    )


def test_backfill_cli_requires_an_explicit_workspace() -> None:
    parser = backfill_autonomous_classification._argument_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--dry-run"])

    args = parser.parse_args(["--workspace-id", "7", "--dry-run"])
    assert args.workspace_id == 7
    assert args.dry_run is True


class _StaticReceiptParser:
    def parse_text(self, _text: str) -> ParsedReceipt:
        return ParsedReceipt(
            merchant="Neighborhood Market",
            purchased_at=datetime(2026, 8, 17, 12, tzinfo=UTC),
            subtotal_cents=1_000,
            tax_cents=0,
            total_cents=1_000,
            confidence=0.99,
            items=[
                ParsedReceiptItem(
                    name="TOILET PAPER",
                    line_total_cents=1_000,
                    confidence=0.99,
                )
            ],
        )


class _ConcurrentReceiptParser(_StaticReceiptParser):
    def __init__(self, barrier: Barrier) -> None:
        self.barrier = barrier

    def parse_text(self, text: str) -> ParsedReceipt:
        self.barrier.wait(timeout=5)
        return super().parse_text(text)


def _transaction(
    db: Session,
    values: dict,
    suffix: str,
    *,
    workspace: str = "workspace",
    plaid: str = "plaid",
    merchant: str = "Independent Service",
    amount_cents: int = 1_000,
) -> ExpenseTransaction:
    value = ExpenseTransaction(
        workspace_id=values[workspace].id,
        plaid_transaction_id=f"classification-job-{suffix}",
        plaid_item_id=values[plaid].id,
        merchant_name=merchant,
        name=merchant,
        amount_cents=amount_cents,
        iso_currency_code="USD",
        date=date(2026, 8, 17),
    )
    db.add(value)
    db.flush()
    return value


def test_two_workers_same_artifact_without_plaid_create_one_logical_purchase(
    classification_db,
) -> None:
    db, factory, values = classification_db
    barrier = Barrier(2)
    parser = _ConcurrentReceiptParser(barrier)
    settings = _settings()

    def ingest(source: str, external_id: str) -> int:
        with factory() as worker:
            set_session_tenant(
                worker,
                TenantContext(values["user"].id, values["workspace"].id),
            )
            receipt = ReceiptIngestionService(
                worker,
                settings,
                parser,
                owner_user_id=values["user"].id,
            ).ingest_text(
                source=source,
                source_external_id=external_id,
                text="the exact same receipt artifact",
            )
            return receipt.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipt_ids = list(
            pool.map(
                lambda values: ingest(*values),
                (("gmail", "gmail-race"), ("telegram", "telegram-race")),
            )
        )

    db.expire_all()
    assert len(set(receipt_ids)) == 1
    assert db.scalar(select(func.count(PurchaseReceipt.id))) == 1
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.voided_at.is_(None)
        )
    ) == 1


def _make_due_provisional(db: Session, transaction: ExpenseTransaction) -> None:
    decision = build_classification_decision(
        source_type=ClassificationSourceType.TRANSACTION,
        source_entity_id=transaction.id,
        spending_parent_category=SpendingParentCategory.SERVICES,
        subcategory_name="Local services",
        item_activity_type=ClassificationActivityType.SERVICE,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
        confidence=0.72,
        authority=ClassificationAuthority.MODEL_EVIDENCE,
        provenance_codes=("semantic_match",),
    )
    ClassificationTaxonomyService(db).apply_decision(decision, commit=False)
    transaction.classification_auto_finalize_at = utc_now() - timedelta(minutes=1)
    db.flush()


def test_finalizer_is_bounded_finalizes_due_and_preserves_user_correction(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    due = _transaction(db, values, "due")
    future = _transaction(db, values, "future")
    corrected = _transaction(db, values, "corrected")
    _make_due_provisional(db, due)
    _make_due_provisional(db, future)
    future.classification_auto_finalize_at = utc_now() + timedelta(hours=1)
    correction = build_classification_decision(
        source_type=ClassificationSourceType.TRANSACTION,
        source_entity_id=corrected.id,
        spending_parent_category=SpendingParentCategory.TRANSPORTATION,
        subcategory_name="Transit",
        item_activity_type=ClassificationActivityType.TRANSPORTATION,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
        confidence=1.0,
        authority=ClassificationAuthority.USER_CORRECTION,
        provenance_codes=("user_correction",),
    )
    ClassificationTaxonomyService(db).apply_decision(correction, commit=False)
    db.commit()

    result = ClassificationFinalizerService(db, _settings()).run(
        batch_size=1,
        use_model=False,
    )

    db.refresh(due)
    db.refresh(future)
    db.refresh(corrected)
    assert result.due == 1
    assert result.transactions_finalized == 1
    assert result.deterministic_fallbacks == 1
    assert due.classification_decision_state == ClassificationDecisionState.FINAL.value
    assert "grace_period_elapsed" in due.classification_provenance_json
    assert future.classification_decision_state == ClassificationDecisionState.PROVISIONAL.value
    assert corrected.classification_decision_state == ClassificationDecisionState.CORRECTED.value
    assert corrected.classification_authority == ClassificationAuthority.USER_CORRECTION.value


def test_finalizer_does_not_double_count_a_row_due_for_two_reasons_at_once(
    classification_db,
) -> None:
    """A row that is simultaneously grace-period-due (auto_finalize_at) and
    retry-due (classification_retry_at) must only consume one slot of the
    batch -- otherwise it silently crowds out a different, genuinely due row
    from the same bounded run.
    """

    db, _factory, values = classification_db
    both_reasons = _transaction(db, values, "both-reasons")
    single_reason = _transaction(db, values, "single-reason")
    _make_due_provisional(db, both_reasons)
    _make_due_provisional(db, single_reason)
    both_reasons.classification_retry_at = utc_now() - timedelta(minutes=5)
    single_reason.classification_auto_finalize_at = utc_now() - timedelta(seconds=30)
    db.commit()

    result = ClassificationFinalizerService(db, _settings()).run(
        batch_size=2,
        use_model=False,
    )

    db.refresh(both_reasons)
    db.refresh(single_reason)
    # Before the fix, both_reasons's auto_finalize-due and retry-due entries
    # each consumed a slot of the batch_size=2 limit before de-duplication,
    # so single_reason was silently crowded out of this run even though
    # there was room for two genuinely distinct due rows.
    assert result.due == 2
    assert result.transactions_finalized + result.transactions_recovered == 2
    assert both_reasons.classification_retry_at is None
    assert single_reason.classification_decision_state == ClassificationDecisionState.FINAL.value


def test_finalizer_claims_and_finalizes_due_receipt_lines(classification_db) -> None:
    db, _factory, values = classification_db
    receipt = PurchaseReceipt(
        workspace_id=values["workspace"].id,
        source="web",
        source_external_id="provisional-line-receipt",
        merchant_raw="Neighborhood Shop",
        total_cents=500,
        currency="USD",
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="MYSTERY ITEM",
        normalized_name="mystery item",
        line_total_cents=500,
    )
    db.add(line)
    db.flush()
    decision = build_classification_decision(
        source_type=ClassificationSourceType.RECEIPT_LINE,
        source_entity_id=line.id,
        spending_parent_category=SpendingParentCategory.LIFESTYLE_SHOPPING,
        subcategory_name="General merchandise",
        item_activity_type=ClassificationActivityType.ONE_TIME_PURCHASE,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
        confidence=0.7,
        authority=ClassificationAuthority.MODEL_EVIDENCE,
        provenance_codes=("semantic_match",),
    )
    ClassificationTaxonomyService(db).apply_decision(decision, commit=False)
    line.classification_auto_finalize_at = utc_now() - timedelta(minutes=1)
    db.commit()

    result = ClassificationFinalizerService(db, _settings()).run(use_model=False)

    db.refresh(line)
    assert result.receipt_lines_finalized == 1
    assert line.classification_decision_state == ClassificationDecisionState.FINAL.value
    assert "grace_period_elapsed" in line.classification_provenance_json


def test_receipt_finalization_rolls_back_when_linked_transaction_recompute_fails(
    classification_db,
    monkeypatch,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(db, values, "linked-recompute-retry")
    matched_at = utc_now()
    receipt = PurchaseReceipt(
        workspace_id=values["workspace"].id,
        source="web",
        source_external_id="linked-recompute-receipt",
        merchant_raw="Neighborhood Shop",
        total_cents=500,
        currency="USD",
        parse_status="needs_review",
        transaction_id=transaction.id,
        transaction_match_status="auto_matched",
        transaction_match_confidence=0.99,
        transaction_match_evidence_json={"reason": "test_exact_match"},
        transaction_match_attempted_at=matched_at,
        transaction_matched_at=matched_at,
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="MYSTERY ITEM",
        normalized_name="mystery item",
        line_total_cents=500,
    )
    db.add(line)
    db.flush()
    decision = build_classification_decision(
        source_type=ClassificationSourceType.RECEIPT_LINE,
        source_entity_id=line.id,
        spending_parent_category=SpendingParentCategory.LIFESTYLE_SHOPPING,
        subcategory_name="General merchandise",
        item_activity_type=ClassificationActivityType.ONE_TIME_PURCHASE,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
        confidence=0.7,
        authority=ClassificationAuthority.MODEL_EVIDENCE,
        provenance_codes=("semantic_match",),
    )
    ClassificationTaxonomyService(db).apply_decision(decision, commit=False)
    line.classification_auto_finalize_at = utc_now() - timedelta(minutes=1)
    db.commit()
    original = AutonomousClassificationService.recompute_transaction_from_receipt_state

    def fail_recompute(*_args, **_kwargs):
        raise RuntimeError("synthetic linked recompute failure")

    monkeypatch.setattr(
        AutonomousClassificationService,
        "recompute_transaction_from_receipt_state",
        fail_recompute,
    )
    failed = ClassificationFinalizerService(db, _settings()).run(use_model=False)
    db.refresh(line)

    assert failed.failures == 1
    assert failed.receipt_lines_finalized == 0
    assert line.classification_decision_state == ClassificationDecisionState.PROVISIONAL.value

    monkeypatch.setattr(
        AutonomousClassificationService,
        "recompute_transaction_from_receipt_state",
        original,
    )
    recovered = ClassificationFinalizerService(db, _settings()).run(use_model=False)
    db.refresh(line)

    assert recovered.failures == 0
    assert recovered.receipt_lines_finalized == 1
    assert line.classification_decision_state == ClassificationDecisionState.FINAL.value


def test_live_ingestion_classification_failure_is_retried_by_finalizer(
    classification_db,
    monkeypatch,
) -> None:
    db, _factory, values = classification_db
    settings = _settings()
    original = ClassificationTaxonomyService.apply_decision
    attempts = 0

    def fail_first_apply(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("synthetic first apply failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        ClassificationTaxonomyService,
        "apply_decision",
        fail_first_apply,
    )
    receipt = ReceiptIngestionService(
        db,
        settings,
        _StaticReceiptParser(),
        owner_user_id=values["user"].id,
    ).ingest_text(
        source="web",
        source_external_id="live-classification-retry",
        text="durable live receipt",
    )
    line = receipt.items[0]
    assert line.classification_applied_at is None
    assert line.classification_retry_at is not None
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == 0
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 0

    first_retry = ClassificationFinalizerService(db, settings).run(use_model=False)
    db.refresh(line)
    second_retry = ClassificationFinalizerService(db, settings).run(use_model=False)

    assert first_retry.receipt_lines_recovered == 1
    assert line.classification_applied_at is not None
    assert line.classification_retry_at is None
    assert line.classification_authority != ClassificationAuthority.USER_CORRECTION.value
    assert second_retry.receipt_lines_recovered == 0
    assert attempts == 2
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == 1
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.voided_at.is_(None)
        )
    ) == 1


def test_swallowed_receipt_learning_failure_rolls_back_projection_and_recovers_once(
    classification_db,
    monkeypatch,
) -> None:
    db, _factory, values = classification_db
    settings = _settings()
    original = AutonomousClassificationService._apply_receipt_learning
    attempts = 0

    def fail_first_learning(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("synthetic receipt learning failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        AutonomousClassificationService,
        "_apply_receipt_learning",
        fail_first_learning,
    )
    receipt = ReceiptIngestionService(
        db,
        settings,
        _StaticReceiptParser(),
        owner_user_id=values["user"].id,
    ).ingest_text(
        source="web",
        source_external_id="live-learning-retry",
        text="durable learning receipt",
    )
    line = receipt.items[0]

    assert line.classification_applied_at is None
    assert line.classification_retry_at is not None
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == 0
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 0

    first_retry = ClassificationFinalizerService(db, settings).run(use_model=False)
    db.refresh(line)
    second_retry = ClassificationFinalizerService(db, settings).run(use_model=False)

    assert first_retry.receipt_lines_recovered == 1
    assert second_retry.receipt_lines_recovered == 0
    assert line.classification_retry_at is None
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == 1
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.voided_at.is_(None)
        )
    ) == 1


def test_restore_learning_failure_is_repaired_without_reclassifying_user_facts(
    classification_db,
    monkeypatch,
) -> None:
    db, _factory, values = classification_db
    settings = _settings()
    ingestion = ReceiptIngestionService(
        db,
        settings,
        _StaticReceiptParser(),
        owner_user_id=values["user"].id,
    )
    receipt = ingestion.ingest_text(
        source="web",
        source_external_id="restore-learning-retry",
        text="restorable learning receipt",
    )
    line = receipt.items[0]
    original_version = line.classification_version
    ingestion.ignore(receipt.id)

    original = AutonomousClassificationService._apply_receipt_learning
    attempts = 0

    def fail_first_learning(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("synthetic restore learning failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        AutonomousClassificationService,
        "_apply_receipt_learning",
        fail_first_learning,
    )
    restored = ingestion.restore(receipt.id)
    line = restored.items[0]

    assert line.classification_applied_at is not None
    assert line.classification_version == original_version
    assert line.classification_retry_at is not None
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.voided_at.is_(None)
        )
    ) == 0

    workspace_settings = db.scalar(
        select(ClassificationSettings).where(
            ClassificationSettings.workspace_id == values["workspace"].id
        )
    )
    assert workspace_settings is not None
    workspace_settings.autonomous_enabled = False
    db.commit()
    ingestion._classify_receipt_nonblocking(restored)
    db.commit()
    db.refresh(line)
    assert line.classification_retry_at is not None
    workspace_settings.autonomous_enabled = True
    db.commit()

    first_retry = ClassificationFinalizerService(db, settings).run(use_model=False)
    db.refresh(line)
    second_retry = ClassificationFinalizerService(db, settings).run(use_model=False)

    assert first_retry.receipt_lines_recovered == 1
    assert second_retry.receipt_lines_recovered == 0
    assert line.classification_version == original_version
    assert line.classification_retry_at is None
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.voided_at.is_(None)
        )
    ) == 1


def test_plaid_upsert_classification_failure_is_retried_by_finalizer(
    classification_db,
    monkeypatch,
) -> None:
    db, _factory, values = classification_db
    settings = _settings()
    original = ClassificationTaxonomyService.apply_decision
    attempts = 0

    def fail_first_apply(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("synthetic first transaction apply failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        ClassificationTaxonomyService,
        "apply_decision",
        fail_first_apply,
    )
    created = TransactionService(
        db,
        settings=settings,
        splitwise_service=object(),
        notification_service=object(),
    ).upsert_transaction(
        values["plaid"],
        {
            "transaction_id": "classification-job-live-transaction-retry",
            "account_id": "account-1",
            "name": "Independent Service",
            "merchant_name": "Independent Service",
            "amount": "10.00",
            "iso_currency_code": "USD",
            "date": "2026-08-17",
            "pending": True,
            "category": ["Service"],
        },
    )
    transaction = db.scalar(
        select(ExpenseTransaction).where(
            ExpenseTransaction.plaid_transaction_id == "classification-job-live-transaction-retry"
        )
    )

    assert created is True
    assert transaction is not None
    assert transaction.classification_applied_at is None
    assert transaction.classification_retry_at is not None

    first_retry = ClassificationFinalizerService(db, settings).run(use_model=False)
    db.refresh(transaction)
    second_retry = ClassificationFinalizerService(db, settings).run(use_model=False)

    assert first_retry.transactions_recovered == 1
    assert transaction.classification_applied_at is not None
    assert transaction.classification_retry_at is None
    assert transaction.classification_authority != ClassificationAuthority.USER_CORRECTION.value
    assert second_retry.transactions_recovered == 0
    assert attempts == 2


def test_modified_final_transaction_failure_retries_fresh_provider_facts(
    classification_db,
    monkeypatch,
) -> None:
    db, _factory, values = classification_db
    settings = _settings()
    transaction = _transaction(
        db,
        values,
        "modified-final-retry",
        merchant="Starbucks coffee",
    )
    service = object.__new__(TransactionService)
    service.db = db
    service.settings = settings
    service._classify_transaction_nonblocking(transaction)
    db.commit()
    db.refresh(transaction)
    original_version = transaction.classification_version
    assert transaction.spending_parent_category == SpendingParentCategory.FOOD_DINING.value

    transaction.merchant_name = "Laundry detergent"
    transaction.name = "Laundry detergent"
    original = ClassificationTaxonomyService.apply_decision
    attempts = 0

    def fail_first_apply(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SQLAlchemyError("synthetic modified transaction apply failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        ClassificationTaxonomyService,
        "apply_decision",
        fail_first_apply,
    )
    service._classify_transaction_nonblocking(transaction)
    db.commit()
    db.refresh(transaction)

    assert transaction.classification_version == original_version
    assert transaction.spending_parent_category == SpendingParentCategory.FOOD_DINING.value
    assert transaction.classification_retry_at is not None

    workspace_settings = db.scalar(
        select(ClassificationSettings).where(
            ClassificationSettings.workspace_id == values["workspace"].id
        )
    )
    assert workspace_settings is not None
    workspace_settings.autonomous_enabled = False
    db.commit()
    service._classify_transaction_nonblocking(transaction)
    db.commit()
    db.refresh(transaction)
    assert transaction.classification_retry_at is not None
    workspace_settings.autonomous_enabled = True
    db.commit()

    first_retry = ClassificationFinalizerService(db, settings).run(use_model=False)
    db.refresh(transaction)
    second_retry = ClassificationFinalizerService(db, settings).run(use_model=False)

    assert first_retry.transactions_recovered == 1
    assert second_retry.transactions_recovered == 0
    assert transaction.classification_retry_at is None
    assert transaction.classification_version == original_version + 1
    assert transaction.spending_parent_category == SpendingParentCategory.HOUSEHOLD_HOME.value
    assert attempts == 2


class _OutageModel:
    def __init__(self) -> None:
        self.calls = 0

    def classify(self, _candidates):
        self.calls += 1
        raise ClassificationModelError("classification_provider_unavailable", retryable=True)


def _food_model_suggestion(source_entity_id: int) -> ClassificationModelSuggestion:
    return ClassificationModelSuggestion(
        decision=build_classification_decision(
            source_type=ClassificationSourceType.TRANSACTION,
            source_entity_id=source_entity_id,
            spending_parent_category=SpendingParentCategory.FOOD_DINING,
            subcategory_name="Restaurants",
            item_activity_type=ClassificationActivityType.RESTAURANT_MEAL,
            replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
            confidence=0.95,
            authority=ClassificationAuthority.MODEL_EVIDENCE,
            provenance_codes=("model_semantic_classification",),
        ),
        cadence_min_days=None,
        cadence_max_days=None,
    )


def test_model_requires_consent_and_outage_uses_deterministic_fallback(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    no_consent = _transaction(db, values, "no-consent")
    _make_due_provisional(db, no_consent)
    db.commit()
    model = _OutageModel()
    settings = _settings(openai_api_key="test-key")

    first = ClassificationFinalizerService(db, settings, model).run()

    assert first.model_candidates == 0
    assert model.calls == 0
    db.add(
        DataConsent(
            workspace_id=values["workspace"].id,
            user_id=values["user"].id,
            purpose="model_transaction_classification",
            granted=True,
            policy_version="test",
        )
    )
    with_consent = _transaction(db, values, "with-consent")
    _make_due_provisional(db, with_consent)
    db.commit()

    second = ClassificationFinalizerService(db, settings, model).run()

    db.refresh(with_consent)
    assert second.model_candidates == 1
    assert second.model_calls == 1
    assert second.model_failure_code == "classification_provider_unavailable"
    assert second.deterministic_fallbacks == 1
    assert model.calls == 1
    assert with_consent.classification_decision_state == ClassificationDecisionState.FINAL.value


def test_model_consent_requires_an_active_current_workspace_member(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    consent = DataConsent(
        workspace_id=values["workspace"].id,
        user_id=values["user"].id,
        purpose="model_transaction_classification",
        granted=True,
        policy_version="test",
    )
    db.add(consent)
    db.commit()
    settings = _settings(openai_api_key="test-key")

    finalizer = ClassificationFinalizerService(db, settings)
    backfill = ClassificationBackfillService(db, settings)
    assert (
        finalizer._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is True
    )
    assert (
        backfill._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is True
    )

    values["user"].status = "suspended"
    db.commit()
    assert (
        finalizer._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is False
    )
    assert (
        backfill._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is False
    )

    values["user"].status = "active"
    consent.revoked_at = utc_now()
    db.commit()
    assert (
        finalizer._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is False
    )
    assert (
        backfill._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is False
    )

    consent.revoked_at = None
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == values["workspace"].id,
            WorkspaceMembership.user_id == values["user"].id,
        )
    )
    assert membership is not None
    db.delete(membership)
    db.commit()
    assert (
        finalizer._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is False
    )
    assert (
        backfill._consent_granted(
            "model_transaction_classification",
            user_id=values["user"].id,
        )
        is False
    )


def test_another_workspace_member_cannot_consent_for_the_transaction_owner(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    other_member = User(email="jobs-member@example.test", display_name="Other member")
    db.add(other_member)
    db.flush()
    db.add_all(
        [
            WorkspaceMembership(
                workspace_id=values["workspace"].id,
                user_id=other_member.id,
                role="member",
            ),
            DataConsent(
                workspace_id=values["workspace"].id,
                user_id=other_member.id,
                purpose="model_transaction_classification",
                granted=True,
                policy_version="test",
            ),
        ]
    )
    transaction = _transaction(db, values, "non-owner-consent")
    _make_due_provisional(db, transaction)
    db.commit()
    model = _OutageModel()

    result = ClassificationFinalizerService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run()

    assert result.model_candidates == 0
    assert result.model_calls == 0
    assert model.calls == 0


def test_user_correction_during_model_planning_wins_before_finalizer_apply(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(db, values, "correction-during-model")
    _make_due_provisional(db, transaction)
    db.add(
        DataConsent(
            workspace_id=values["workspace"].id,
            user_id=values["user"].id,
            purpose="model_transaction_classification",
            granted=True,
            policy_version="test",
        )
    )
    db.commit()

    class CorrectingModel:
        calls = 0
        last_observation = None

        def classify(self, _candidates):
            self.calls += 1
            AutonomousClassificationService(db, _settings()).correct_transaction(
                transaction.id,
                spending_parent_category=SpendingParentCategory.TRANSPORTATION,
                subcategory_name="Public transit",
                item_activity_type=ClassificationActivityType.TRANSPORTATION,
                replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
                commit=False,
            )
            return []

    model = CorrectingModel()
    result = ClassificationFinalizerService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run()

    db.refresh(transaction)
    assert model.calls == 1
    assert result.transactions_finalized == 0
    assert result.skipped == 1
    assert transaction.classification_decision_state == "corrected"
    assert transaction.classification_authority == "user_correction"


def test_workspace_disable_during_model_planning_discards_finalizer_work(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(db, values, "disable-during-model")
    _make_due_provisional(db, transaction)
    db.add(
        DataConsent(
            workspace_id=values["workspace"].id,
            user_id=values["user"].id,
            purpose="model_transaction_classification",
            granted=True,
            policy_version="test",
        )
    )
    db.commit()

    class DisablingModel:
        calls = 0
        last_observation = None

        def classify(self, _candidates):
            self.calls += 1
            workspace_settings = ClassificationTaxonomyService(db).get_settings()
            assert workspace_settings is not None
            workspace_settings.autonomous_enabled = False
            db.flush()
            return [_food_model_suggestion(transaction.id)]

    model = DisablingModel()
    result = ClassificationFinalizerService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run()

    db.refresh(transaction)
    assert model.calls == 1
    assert result.enabled is False
    assert result.transactions_finalized == 0
    assert result.skipped == 1
    assert transaction.spending_parent_category == SpendingParentCategory.SERVICES.value
    assert transaction.classification_decision_state == "provisional"


def test_consent_revoke_during_model_planning_forces_finalizer_fallback(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(db, values, "revoke-during-model")
    _make_due_provisional(db, transaction)
    consent = DataConsent(
        workspace_id=values["workspace"].id,
        user_id=values["user"].id,
        purpose="model_transaction_classification",
        granted=True,
        policy_version="test",
    )
    db.add(consent)
    db.commit()

    class RevokingModel:
        calls = 0
        last_observation = None

        def classify(self, _candidates):
            self.calls += 1
            consent.revoked_at = utc_now()
            db.flush()
            return [_food_model_suggestion(transaction.id)]

    model = RevokingModel()
    result = ClassificationFinalizerService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run()

    db.refresh(transaction)
    assert model.calls == 1
    assert result.transactions_finalized == 1
    assert result.deterministic_fallbacks == 1
    assert transaction.spending_parent_category == SpendingParentCategory.SERVICES.value
    assert transaction.classification_decision_state == "final"
    assert transaction.classification_authority == ClassificationAuthority.MODEL_EVIDENCE.value


def test_global_and_workspace_kill_switches_leave_provisional_state_untouched(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(db, values, "kill-switch")
    _make_due_provisional(db, transaction)
    workspace_settings = ClassificationTaxonomyService(db).get_settings()
    assert workspace_settings is not None
    workspace_settings.autonomous_enabled = False
    db.commit()

    workspace_disabled = ClassificationFinalizerService(db, _settings()).run()

    db.refresh(transaction)
    assert workspace_disabled.enabled is False
    assert (
        transaction.classification_decision_state == ClassificationDecisionState.PROVISIONAL.value
    )
    workspace_settings.autonomous_enabled = True
    db.commit()

    globally_disabled = ClassificationFinalizerService(
        db,
        _settings().model_copy(update={"autonomous_classification_enabled": False}),
    ).run()

    db.refresh(transaction)
    assert globally_disabled.enabled is False
    assert (
        transaction.classification_decision_state == ClassificationDecisionState.PROVISIONAL.value
    )


def test_finalizer_never_claims_another_workspace_row(classification_db) -> None:
    db, factory, values = classification_db
    own = _transaction(db, values, "own-scope")
    _make_due_provisional(db, own)
    db.commit()
    with factory() as other_db:
        set_session_tenant(
            other_db,
            TenantContext(
                values["other_user"].id,
                values["other_workspace"].id,
            ),
        )
        other = _transaction(
            other_db,
            values,
            "other-scope",
            workspace="other_workspace",
            plaid="other_plaid",
        )
        _make_due_provisional(other_db, other)
        other_id = other.id
        other_db.commit()

    result = ClassificationFinalizerService(db, _settings()).run(use_model=False)

    db.refresh(own)
    assert result.transactions_finalized == 1
    assert own.classification_decision_state == ClassificationDecisionState.FINAL.value
    with factory() as other_db:
        set_session_tenant(
            other_db,
            TenantContext(
                values["other_user"].id,
                values["other_workspace"].id,
            ),
        )
        other = other_db.get(ExpenseTransaction, other_id)
        assert other is not None
        assert other.classification_decision_state == ClassificationDecisionState.PROVISIONAL.value


def _historical_receipt_and_transaction(db: Session, values: dict):
    transaction = _transaction(
        db,
        values,
        "historical",
        merchant="Trader Joe's #177",
        amount_cents=1_000,
    )
    receipt = PurchaseReceipt(
        workspace_id=values["workspace"].id,
        source="web",
        source_external_id="historical-receipt",
        merchant_raw="Trader Joe's",
        merchant_normalized="trader joes",
        purchased_at=datetime(2026, 8, 17, 12, tzinfo=UTC),
        subtotal_cents=1_000,
        total_cents=1_000,
        currency="USD",
        line_items_complete=True,
        arithmetic_status="verified",
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="TOILET PAPER",
        normalized_name="toilet paper",
        line_total_cents=1_000,
    )
    db.add(line)
    db.flush()
    return transaction, receipt, line


def test_backfill_dry_run_is_read_only_then_checkpointed_page_is_idempotent_and_scoped(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction, receipt, line = _historical_receipt_and_transaction(db, values)
    settings_row = ClassificationTaxonomyService(db).get_settings()
    assert settings_row is not None
    db.commit()

    preview = ClassificationBackfillService(db, _settings()).run_page(dry_run=True)

    assert preview.receipt_match_scanned == 1
    assert preview.receipt_items_scanned == 1
    assert preview.transactions_scanned == 1
    db.refresh(settings_row)
    db.refresh(receipt)
    db.refresh(line)
    assert settings_row.receipt_match_backfill_cursor == 0
    assert settings_row.receipt_item_backfill_cursor == 0
    assert settings_row.transaction_backfill_cursor == 0
    assert receipt.transaction_match_status == "not_attempted"
    assert line.classification_applied_at is None

    result = ClassificationBackfillService(db, _settings()).run_page()

    db.refresh(settings_row)
    db.refresh(transaction)
    db.refresh(receipt)
    db.refresh(line)
    assert result.receipt_auto_matches == 1
    assert result.receipt_items_classified >= 1
    assert result.transactions_classified >= 1
    assert receipt.transaction_id == transaction.id
    assert line.classification_applied_at is not None
    assert transaction.classification_applied_at is not None
    assert settings_row.receipt_match_backfill_cursor == receipt.id
    assert settings_row.receipt_item_backfill_cursor == line.id
    assert settings_row.transaction_backfill_cursor == transaction.id
    ledger_count = db.scalar(select(func.count(ClassificationDecisionRecord.id)))

    repeated = ClassificationBackfillService(db, _settings()).run_page()

    assert repeated.receipt_match_scanned == 0
    assert repeated.receipt_items_scanned == 0
    assert repeated.transactions_scanned == 0
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == ledger_count


def test_backfill_never_learns_from_an_ignored_receipt(classification_db) -> None:
    db, _factory, values = classification_db
    transaction, receipt, line = _historical_receipt_and_transaction(db, values)
    receipt.parse_status = "ignored"
    ClassificationTaxonomyService(db).get_settings()
    db.commit()

    result = ClassificationBackfillService(db, _settings()).run_page()

    db.refresh(receipt)
    db.refresh(line)
    db.refresh(transaction)
    assert result.receipt_match_scanned == 0
    assert result.receipt_items_scanned == 0
    assert receipt.transaction_match_status == "not_attempted"
    assert line.classification_applied_at is None
    assert transaction.classification_applied_at is not None


def test_backfill_failure_rolls_back_cursor_and_page_work(classification_db, monkeypatch) -> None:
    db, _factory, values = classification_db
    _transaction_value, receipt, line = _historical_receipt_and_transaction(db, values)
    settings_row = ClassificationTaxonomyService(db).get_settings()
    db.commit()

    def fail_classification(*_args, **_kwargs):
        raise RuntimeError("synthetic classification failure")

    monkeypatch.setattr(
        "app.services.classification_backfill_service."
        "AutonomousClassificationService.classify_receipt",
        fail_classification,
    )
    with pytest.raises(RuntimeError, match="synthetic classification failure"):
        ClassificationBackfillService(db, _settings()).run_page()
    db.rollback()

    db.refresh(settings_row)
    db.refresh(receipt)
    db.refresh(line)
    assert settings_row.receipt_match_backfill_cursor == 0
    assert settings_row.receipt_item_backfill_cursor == 0
    assert settings_row.transaction_backfill_cursor == 0
    assert receipt.transaction_match_status == "not_attempted"
    assert line.classification_applied_at is None


def test_historical_model_batch_is_explicit_consent_gated_and_outage_safe(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(
        db,
        values,
        "historical-model",
        merchant="ZXQ Mystery Vendor",
    )
    db.add(
        DataConsent(
            workspace_id=values["workspace"].id,
            user_id=values["user"].id,
            purpose="model_transaction_classification",
            granted=True,
            policy_version="test",
        )
    )
    ClassificationTaxonomyService(db).get_settings()
    db.commit()
    model = _OutageModel()

    result = ClassificationBackfillService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run_page(use_model=True)

    db.refresh(transaction)
    assert result.model_candidates == 1
    assert result.model_calls == 1
    assert result.model_failure_code == "classification_provider_unavailable"
    assert model.calls == 1
    assert transaction.classification_applied_at is not None
    assert (
        transaction.classification_decision_state == ClassificationDecisionState.PROVISIONAL.value
    )
    assert transaction.classification_auto_finalize_at is not None


def test_workspace_disable_during_backfill_model_planning_aborts_page(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(
        db,
        values,
        "backfill-disable-during-model",
        merchant="ZXQ Mystery Vendor",
    )
    db.add(
        DataConsent(
            workspace_id=values["workspace"].id,
            user_id=values["user"].id,
            purpose="model_transaction_classification",
            granted=True,
            policy_version="test",
        )
    )
    settings_row = ClassificationTaxonomyService(db).get_settings()
    assert settings_row is not None
    db.commit()

    class DisablingModel:
        calls = 0
        last_observation = None

        def classify(self, _candidates):
            self.calls += 1
            settings_row.autonomous_enabled = False
            db.flush()
            return [_food_model_suggestion(transaction.id)]

    model = DisablingModel()
    result = ClassificationBackfillService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run_page(use_model=True)

    db.refresh(transaction)
    db.refresh(settings_row)
    assert model.calls == 1
    assert result.enabled is False
    assert transaction.classification_applied_at is None
    assert settings_row.transaction_backfill_cursor == 0


def test_consent_revoke_during_backfill_model_planning_discards_suggestion(
    classification_db,
) -> None:
    db, _factory, values = classification_db
    transaction = _transaction(
        db,
        values,
        "backfill-revoke-during-model",
        merchant="ZXQ Mystery Vendor",
    )
    consent = DataConsent(
        workspace_id=values["workspace"].id,
        user_id=values["user"].id,
        purpose="model_transaction_classification",
        granted=True,
        policy_version="test",
    )
    db.add(consent)
    ClassificationTaxonomyService(db).get_settings()
    db.commit()

    class RevokingModel:
        calls = 0
        last_observation = None

        def classify(self, _candidates):
            self.calls += 1
            consent.revoked_at = utc_now()
            db.flush()
            return [_food_model_suggestion(transaction.id)]

    model = RevokingModel()
    result = ClassificationBackfillService(
        db,
        _settings(openai_api_key="test-key"),
        model,
    ).run_page(use_model=True)

    db.refresh(transaction)
    assert model.calls == 1
    assert result.transactions_classified == 1
    assert transaction.spending_parent_category != SpendingParentCategory.FOOD_DINING.value
    assert transaction.classification_authority != ClassificationAuthority.MODEL_EVIDENCE.value


def test_workspace_runner_commits_lease_before_finalizer_work(
    classification_db,
    monkeypatch,
) -> None:
    db, factory, values = classification_db
    observed = {"blocked": False}

    def inspect_lease(_service, **_kwargs):
        with factory() as concurrent:
            set_session_tenant(
                concurrent,
                TenantContext(values["user"].id, values["workspace"].id),
            )
            observed["blocked"] = (
                acquire_job_lease(
                    concurrent,
                    workspace_id=values["workspace"].id,
                    job_name=FINALIZER_JOB_NAME,
                )
                is None
            )
        return ClassificationFinalizerResult(
            values["workspace"].id,
            True,
            True,
        )

    monkeypatch.setattr(ClassificationFinalizerService, "run", inspect_lease)

    result = run_finalizer_for_workspace(
        db,
        workspace_id=values["workspace"].id,
        settings=_settings(),
    )

    assert result.lease_acquired is True
    assert observed["blocked"] is True


def test_finalizer_forever_mode_runs_bounded_poll_loop(monkeypatch) -> None:
    calls: list[tuple[int | None, bool]] = []

    monkeypatch.setattr(
        classification_finalizer_job,
        "get_settings",
        lambda: type("WorkerSettings", (), {"classification_finalizer_poll_seconds": 300})(),
    )
    monkeypatch.setattr(
        classification_finalizer_job,
        "run",
        lambda *, batch_size, use_model: calls.append((batch_size, use_model)),
    )

    def stop_after_first_poll(seconds: int) -> None:
        assert seconds == 31
        raise RuntimeError("stop-after-first-poll")

    monkeypatch.setattr(classification_finalizer_job.time, "sleep", stop_after_first_poll)

    with pytest.raises(RuntimeError, match="stop-after-first-poll"):
        classification_finalizer_job.run_forever(
            batch_size=7,
            use_model=False,
            poll_seconds=31,
        )
    assert calls == [(7, False)]


def test_finalizer_forever_mode_recovers_after_failed_iteration(monkeypatch) -> None:
    calls = 0
    sleeps = 0

    monkeypatch.setattr(
        classification_finalizer_job,
        "get_settings",
        lambda: type("WorkerSettings", (), {"classification_finalizer_poll_seconds": 300})(),
    )

    def run_iteration(*, batch_size, use_model):
        nonlocal calls
        assert batch_size == 5
        assert use_model is True
        calls += 1
        if calls == 1:
            raise RuntimeError("one-workspace-failed")
        return {"workspaces": 1}

    def stop_after_recovery(seconds: int) -> None:
        nonlocal sleeps
        assert seconds == 31
        sleeps += 1
        if sleeps == 2:
            raise RuntimeError("stop-after-recovery")

    monkeypatch.setattr(classification_finalizer_job, "run", run_iteration)
    monkeypatch.setattr(classification_finalizer_job.time, "sleep", stop_after_recovery)

    with pytest.raises(RuntimeError, match="stop-after-recovery"):
        classification_finalizer_job.run_forever(
            batch_size=5,
            use_model=True,
            poll_seconds=31,
        )

    assert calls == 2
    assert sleeps == 2

@pytest.mark.parametrize("poll_seconds", [29, 3601])
def test_finalizer_forever_mode_rejects_unsafe_poll_interval(
    monkeypatch,
    poll_seconds: int,
) -> None:
    monkeypatch.setattr(
        classification_finalizer_job,
        "get_settings",
        lambda: type("WorkerSettings", (), {"classification_finalizer_poll_seconds": 300})(),
    )

    with pytest.raises(ValueError, match="poll interval is out of bounds"):
        classification_finalizer_job.run_forever(poll_seconds=poll_seconds)
