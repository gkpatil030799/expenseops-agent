from __future__ import annotations

from contextlib import contextmanager
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
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptParseStatus,
    TransactionStatus,
)
from app.services.item_normalization_service import normalize_merchant
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.receipt_parser_service import ParsedReceipt, ParsedReceiptItem
from app.services.receipt_transaction_reconciliation_service import (
    ReceiptTransactionMatchStatus,
    ReceiptTransactionReconciliationService,
)
from app.services.transaction_service import TransactionService


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'receipt-transaction-reconciliation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.info["workspace_id"] = 1
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class _StaticParser:
    def __init__(self, parsed: ParsedReceipt):
        self.parsed = parsed

    def parse_text(self, _text: str) -> ParsedReceipt:
        return self.parsed


class _NotificationSink:
    def notify_transaction_needs_review(self, _transaction):
        return True


def _plaid_item(db, *, workspace_id: int = 1, suffix: str = "1") -> PlaidItem:
    item = PlaidItem(
        workspace_id=workspace_id,
        item_id=f"plaid-item-{suffix}",
        access_token_encrypted="encrypted",
    )
    db.add(item)
    db.flush()
    return item


def _transaction(
    db,
    plaid_item: PlaidItem,
    *,
    external_id: str,
    merchant: str,
    amount_cents: int = 5387,
    transaction_date: date | None = date(2026, 8, 15),
    authorized_date: date | None = None,
    currency: str = "USD",
    pending: bool = False,
    status: str = TransactionStatus.ASK_USER.value,
) -> ExpenseTransaction:
    transaction = ExpenseTransaction(
        workspace_id=plaid_item.workspace_id,
        plaid_item_id=plaid_item.id,
        plaid_transaction_id=external_id,
        merchant_name=merchant,
        name=merchant,
        amount_cents=amount_cents,
        iso_currency_code=currency,
        date=transaction_date,
        authorized_date=authorized_date,
        pending=pending,
        status=status,
    )
    db.add(transaction)
    db.flush()
    return transaction


def _receipt(
    db,
    *,
    external_id: str,
    merchant: str,
    workspace_id: int = 1,
    total_cents: int = 5387,
    purchased_at: datetime = datetime(2026, 8, 15, 12, tzinfo=UTC),
    currency: str = "USD",
    transaction_id: int | None = None,
    source: str = "web",
    content_sha256: str | None = None,
) -> PurchaseReceipt:
    receipt = PurchaseReceipt(
        workspace_id=workspace_id,
        source=source,
        source_external_id=external_id,
        content_sha256=content_sha256,
        merchant_raw=merchant,
        merchant_normalized=normalize_merchant(merchant),
        purchased_at=purchased_at,
        total_cents=total_cents,
        currency=currency,
        transaction_id=transaction_id,
        parse_status=ReceiptParseStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.flush()
    return receipt


@contextmanager
def _bootstrap_unscoped(db):
    """Seed deliberately foreign fixtures without weakening runtime tenant scope."""

    workspace_id = db.info.pop("workspace_id", None)
    try:
        yield
        db.flush()
    finally:
        if workspace_id is not None:
            db.info["workspace_id"] = workspace_id


@pytest.mark.parametrize(
    ("receipt_merchant", "transaction_merchant"),
    [
        ("Costco Wholesale", "COSTCO WHSE #123"),
        ("Target", "Target Store 1843"),
        ("Olive Garden", "Olive Garden Italian Restaurant"),
        ("Best Buy", "BEST BUY #102"),
        ("Trader Joe's", "TRADER JOE’S #177"),
    ],
    ids=["grocery", "mixed-retailer", "restaurant", "retail", "trader-joes"],
)
def test_generic_merchant_matrix_auto_matches_deterministically(
    db,
    receipt_merchant,
    transaction_merchant,
):
    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id="tx-matrix",
        merchant=transaction_merchant,
    )
    receipt = _receipt(db, external_id="receipt-matrix", merchant=receipt_merchant)

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.AUTO_MATCHED
    assert decision.transaction_id == transaction.id
    assert receipt.transaction_match_status == "auto_matched"
    assert receipt.transaction_match_confidence >= 0.9
    assert receipt.transaction_match_evidence_json["reason"] == "deterministic_match"
    assert receipt.transaction_match_attempted_at is not None
    assert receipt.transaction_matched_at is not None


@pytest.mark.parametrize("amount_delta", [-2, 0, 2])
def test_amount_tolerance_includes_exact_two_cent_boundary(db, amount_delta):
    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id=f"tx-{amount_delta}",
        merchant="Trader Joe's",
        amount_cents=5387 + amount_delta,
    )
    receipt = _receipt(db, external_id=f"receipt-{amount_delta}", merchant="Trader Joe's")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.transaction_id == transaction.id


def test_amount_outside_boundary_and_non_purchase_sign_do_not_match(db):
    plaid_item = _plaid_item(db)
    _transaction(
        db,
        plaid_item,
        external_id="tx-plus-three",
        merchant="Trader Joe's",
        amount_cents=5390,
    )
    _transaction(
        db,
        plaid_item,
        external_id="tx-credit",
        merchant="Trader Joe's",
        amount_cents=-5387,
    )
    receipt = _receipt(db, external_id="receipt-no-amount", merchant="Trader Joe's")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.NO_MATCH
    assert decision.transaction_id is None


def test_authorized_date_is_a_bounded_fallback(db):
    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id="tx-authorized",
        merchant="Trader Joe's",
        transaction_date=date(2026, 8, 19),
        authorized_date=date(2026, 8, 15),
    )
    receipt = _receipt(db, external_id="receipt-authorized", merchant="Trader Joe's")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.transaction_id == transaction.id
    assert decision.evidence["candidates"][0]["date_source"] == "authorized_date"


@pytest.mark.parametrize(("date_delta", "expected"), [(2, True), (3, False)])
def test_date_tolerance_has_exact_two_day_boundary(db, date_delta, expected):
    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id=f"tx-date-{date_delta}",
        merchant="Target",
        transaction_date=date(2026, 8, 15) + timedelta(days=date_delta),
    )
    receipt = _receipt(db, external_id=f"receipt-date-{date_delta}", merchant="Target")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert (decision.transaction_id == transaction.id) is expected
    assert decision.status == (
        ReceiptTransactionMatchStatus.AUTO_MATCHED
        if expected
        else ReceiptTransactionMatchStatus.NO_MATCH
    )


def test_currency_mismatch_is_a_durable_no_match(db):
    plaid_item = _plaid_item(db)
    _transaction(
        db,
        plaid_item,
        external_id="tx-eur",
        merchant="Target",
        currency="EUR",
    )
    receipt = _receipt(db, external_id="receipt-usd", merchant="Target", currency="USD")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.NO_MATCH
    assert receipt.transaction_match_evidence_json["reason"] == "no_eligible_candidate"


def test_posted_candidate_wins_over_equivalent_pending_candidate(db):
    plaid_item = _plaid_item(db)
    _transaction(
        db,
        plaid_item,
        external_id="tx-pending",
        merchant="Target",
        pending=True,
    )
    posted = _transaction(
        db,
        plaid_item,
        external_id="tx-posted",
        merchant="Target",
        pending=False,
    )
    receipt = _receipt(db, external_id="receipt-posted", merchant="Target")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.AUTO_MATCHED
    assert decision.transaction_id == posted.id


def test_equivalent_posted_candidates_are_ambiguous_and_never_forced(db):
    plaid_item = _plaid_item(db)
    _transaction(db, plaid_item, external_id="tx-a", merchant="Target")
    _transaction(db, plaid_item, external_id="tx-b", merchant="Target")
    receipt = _receipt(db, external_id="receipt-tie", merchant="Target")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.AMBIGUOUS
    assert decision.transaction_id is None
    assert decision.evidence["reason"] == "near_tie"


def test_existing_transaction_link_collision_is_preserved_and_flagged(db):
    plaid_item = _plaid_item(db)
    transaction = _transaction(db, plaid_item, external_id="tx-linked", merchant="Target")
    first = _receipt(
        db,
        external_id="receipt-first",
        merchant="Target",
        transaction_id=transaction.id,
    )
    second = _receipt(db, external_id="receipt-second", merchant="Target")

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(second)

    assert decision.status == ReceiptTransactionMatchStatus.AMBIGUOUS
    assert decision.transaction_id is None
    assert decision.evidence["reason"] == "candidate_already_linked"
    assert first.transaction_id == transaction.id


def test_receipt_ingestion_matches_transaction_first_without_review_gate(db):
    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id="tx-first",
        merchant="Trader Joe's #177",
        amount_cents=7576,
        transaction_date=date(2026, 8, 14),
    )
    parsed = ParsedReceipt(
        merchant="Trader Joe's",
        purchased_at=datetime(2026, 8, 14, 10, tzinfo=UTC),
        subtotal_cents=7576,
        tax_cents=0,
        total_cents=7576,
        currency="USD",
        confidence=0.99,
        items=[ParsedReceiptItem(name="Organic milk", line_total_cents=699, confidence=0.99)],
    )

    receipt = ReceiptIngestionService(
        db,
        Settings(_env_file=None),
        _StaticParser(parsed),
    ).ingest_text(source="web", source_external_id="ingested", text="receipt")

    assert receipt.transaction_id == transaction.id
    assert receipt.transaction_match_status == "auto_matched"


def test_transaction_upsert_retries_receipt_that_arrived_first(db):
    plaid_item = _plaid_item(db)
    receipt = _receipt(
        db,
        external_id="receipt-before-plaid",
        merchant="Trader Joe's",
        total_cents=5387,
        purchased_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
    )
    first = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)
    assert first.status == ReceiptTransactionMatchStatus.NO_MATCH
    db.commit()

    TransactionService(
        db,
        settings=Settings(_env_file=None),
        splitwise_service=object(),
        notification_service=_NotificationSink(),
    ).upsert_transaction(
        plaid_item,
        {
            "transaction_id": "late-plaid",
            "account_id": "account-1",
            "name": "TRADER JOE’S #177",
            "merchant_name": "TRADER JOE’S #177",
            "amount": "53.87",
            "iso_currency_code": "USD",
            "date": "2026-08-15",
            "pending": False,
            "category": ["Food and Drink", "Groceries"],
        },
    )
    db.refresh(receipt)
    transaction = db.scalar(
        select(ExpenseTransaction).where(ExpenseTransaction.plaid_transaction_id == "late-plaid")
    )

    assert receipt.transaction_id == transaction.id
    assert receipt.transaction_match_status == "auto_matched"
    assert transaction.provider_category == "Food and Drink / Groceries"
    assert transaction.category == transaction.provider_category


def test_two_cross_channel_receipts_before_plaid_consolidate_duplicate_acquisitions(db):
    item = HouseholdItem(
        workspace_id=1,
        name="Organic milk",
        cadence_days=1,
        cadence_source="observed",
        cadence_confidence=0.9,
        enabled=True,
    )
    db.add(item)
    db.flush()
    prior = HouseholdItemAcquisition(
        workspace_id=1,
        household_item_id=item.id,
        acquired_at=datetime(2026, 7, 15, 10, tzinfo=UTC),
        source="manual",
        confidence=1.0,
        confirmed=True,
        user_confirmed=True,
    )
    db.add(prior)
    receipts: list[PurchaseReceipt] = []
    acquisitions: list[HouseholdItemAcquisition] = []
    for index, source in enumerate(("web", "telegram"), start=1):
        receipt = _receipt(
            db,
            external_id=f"duplicate-before-plaid-{index}",
            merchant="Trader Joe's",
            purchased_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
            source=source,
            content_sha256="a" * 64,
        )
        receipt.parse_status = ReceiptParseStatus.CONFIRMED.value
        line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="Organic milk",
            normalized_name="organic milk",
            line_total_cents=5387,
            household_item_id=item.id,
            match_status="matched",
        )
        db.add(line)
        db.flush()
        acquisition = HouseholdItemAcquisition(
            workspace_id=1,
            household_item_id=item.id,
            receipt_item_id=line.id,
            acquired_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
            source=f"receipt_{source}",
            confidence=0.99,
            confirmed=True,
            user_confirmed=True,
        )
        db.add(acquisition)
        receipts.append(receipt)
        acquisitions.append(acquisition)
    db.commit()

    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id="plaid-after-duplicate-receipts",
        merchant="TRADER JOE'S #177",
    )

    decisions = ReceiptTransactionReconciliationService(db).reconcile_for_transaction(
        transaction
    )
    db.flush()

    assert {decision.transaction_id for decision in decisions} == {transaction.id}
    assert {receipt.transaction_id for receipt in receipts} == {transaction.id}
    active_receipt_acquisitions = list(
        db.scalars(
            select(HouseholdItemAcquisition).where(
                HouseholdItemAcquisition.id.in_([row.id for row in acquisitions]),
                HouseholdItemAcquisition.voided_at.is_(None),
            )
        )
    )
    assert len(active_receipt_acquisitions) == 1
    assert sum(row.voided_at is not None for row in acquisitions) == 1
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.household_item_id == item.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    ) == 2
    assert item.cadence_source == "observed"
    assert item.cadence_days == 31


def test_artifact_consolidation_excludes_acquisition_moved_after_item_pre_read(
    db,
    monkeypatch,
):
    original_item = HouseholdItem(
        workspace_id=1,
        name="Organic milk",
        cadence_days=30,
        enabled=True,
    )
    moved_item = HouseholdItem(
        workspace_id=1,
        name="Oat milk",
        cadence_days=30,
        enabled=True,
    )
    db.add_all([original_item, moved_item])
    db.flush()
    plaid_item = _plaid_item(db)
    transaction = _transaction(
        db,
        plaid_item,
        external_id="artifact-consolidation-recheck",
        merchant="TRADER JOE'S #177",
    )
    receipts: list[PurchaseReceipt] = []
    acquisitions: list[HouseholdItemAcquisition] = []
    for index, source in enumerate(("web", "telegram"), start=1):
        receipt = _receipt(
            db,
            external_id=f"artifact-consolidation-recheck-{index}",
            merchant="Trader Joe's",
            transaction_id=transaction.id,
            source=source,
            content_sha256="c" * 64,
        )
        receipt.parse_status = ReceiptParseStatus.CONFIRMED.value
        line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="Organic milk",
            normalized_name="organic milk",
            line_total_cents=5387,
            household_item_id=original_item.id,
            match_status="matched",
        )
        db.add(line)
        db.flush()
        acquisition = HouseholdItemAcquisition(
            workspace_id=1,
            household_item_id=original_item.id,
            receipt_item_id=line.id,
            transaction_id=transaction.id,
            acquired_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
            source=f"receipt_{source}",
            confidence=0.99,
            confirmed=True,
            user_confirmed=True,
        )
        db.add(acquisition)
        receipts.append(receipt)
        acquisitions.append(acquisition)
    db.commit()

    stationary, moved = acquisitions
    original_scalars = db.scalars
    lock_trace: list[tuple[str, tuple[int, ...]]] = []

    def move_between_pre_read_and_lock(statement, *args, **kwargs):
        sql = str(statement)
        descriptions = statement.column_descriptions
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is HouseholdItem and "household_items.id" in sql:
            moved.household_item_id = moved_item.id
            db.flush()
            rows = list(original_scalars(statement, *args, **kwargs))
            lock_trace.append(("items", tuple(rows)))
            return iter(rows)
        if (
            entity is HouseholdItemAcquisition
            and "household_item_acquisitions.household_item_id IN" in sql
        ):
            rows = list(original_scalars(statement, *args, **kwargs))
            lock_trace.append(("acquisitions", tuple(row.id for row in rows)))
            return iter(rows)
        return original_scalars(statement, *args, **kwargs)

    monkeypatch.setattr(db, "scalars", move_between_pre_read_and_lock)
    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipts[-1])
    db.flush()

    assert decision.status == ReceiptTransactionMatchStatus.AUTO_MATCHED
    assert lock_trace == [
        ("items", (original_item.id,)),
        ("acquisitions", (stationary.id,)),
    ]
    assert moved.household_item_id == moved_item.id
    assert stationary.voided_at is None
    assert moved.voided_at is None


def test_equal_receipt_signatures_with_distinct_artifacts_never_collapse_purchases(db):
    item = HouseholdItem(
        workspace_id=1,
        name="Organic milk",
        cadence_days=30,
        enabled=True,
    )
    db.add(item)
    db.flush()
    acquisitions: list[HouseholdItemAcquisition] = []
    for index, source in enumerate(("web", "telegram"), start=1):
        receipt = _receipt(
            db,
            external_id=f"genuine-identical-purchase-{index}",
            merchant="Trader Joe's",
            purchased_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
            source=source,
            content_sha256=("a" if index == 1 else "b") * 64,
        )
        receipt.parse_status = ReceiptParseStatus.CONFIRMED.value
        line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="Organic milk",
            normalized_name="organic milk",
            line_total_cents=5387,
            household_item_id=item.id,
            match_status="matched",
        )
        db.add(line)
        db.flush()
        acquisition = HouseholdItemAcquisition(
            workspace_id=1,
            household_item_id=item.id,
            receipt_item_id=line.id,
            acquired_at=datetime(2026, 8, 15, 10, tzinfo=UTC),
            source=f"receipt_{source}",
            confidence=0.99,
            confirmed=True,
            user_confirmed=True,
        )
        db.add(acquisition)
        acquisitions.append(acquisition)
    db.commit()
    plaid_item = _plaid_item(db)

    first_transaction = _transaction(
        db,
        plaid_item,
        external_id="first-identical-plaid-purchase",
        merchant="TRADER JOE'S #177",
    )
    ReceiptTransactionReconciliationService(db).reconcile_for_transaction(first_transaction)
    db.flush()
    second_transaction = _transaction(
        db,
        plaid_item,
        external_id="second-identical-plaid-purchase",
        merchant="TRADER JOE'S #177",
    )
    ReceiptTransactionReconciliationService(db).reconcile_for_transaction(second_transaction)
    db.flush()

    assert sum(row.voided_at is None for row in acquisitions) == 2
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.household_item_id == item.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    ) == 2


def test_pending_to_posted_migrates_receipt_and_acquisition_atomically(db):
    plaid_item = _plaid_item(db)
    pending = _transaction(
        db,
        plaid_item,
        external_id="pending-tx",
        merchant="Target",
        pending=True,
    )
    receipt = _receipt(
        db,
        external_id="receipt-pending",
        merchant="Target",
        transaction_id=pending.id,
    )
    household_item = HouseholdItem(
        workspace_id=1,
        name="Paper towels",
        cadence_days=30,
        enabled=True,
    )
    db.add(household_item)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="Paper towels",
        normalized_name="paper towels",
        household_item_id=household_item.id,
        match_status="matched",
    )
    db.add(line)
    db.flush()
    acquisition = HouseholdItemAcquisition(
        workspace_id=1,
        household_item_id=household_item.id,
        receipt_item_id=line.id,
        transaction_id=pending.id,
        acquired_at=datetime(2026, 8, 15, tzinfo=UTC),
        source="receipt",
        confidence=0.99,
        confirmed=True,
    )
    db.add(acquisition)
    db.commit()

    TransactionService(
        db,
        settings=Settings(_env_file=None),
        splitwise_service=object(),
        notification_service=_NotificationSink(),
    ).upsert_transaction(
        plaid_item,
        {
            "transaction_id": "posted-tx",
            "pending_transaction_id": "pending-tx",
            "account_id": "account-1",
            "name": "Target Store 1843",
            "merchant_name": "Target Store 1843",
            "amount": "53.87",
            "iso_currency_code": "USD",
            "date": "2026-08-15",
            "pending": False,
        },
    )
    posted = db.scalar(
        select(ExpenseTransaction).where(ExpenseTransaction.plaid_transaction_id == "posted-tx")
    )
    db.refresh(receipt)
    db.refresh(acquisition)
    db.refresh(pending)

    assert receipt.transaction_id == posted.id
    assert acquisition.transaction_id == posted.id
    assert receipt.transaction_match_status == "auto_matched"
    assert receipt.transaction_match_evidence_json["reason"] in {
        "plaid_pending_replacement",
        "existing_link_preserved",
    }
    assert pending.replaced_by_transaction_id == posted.id
    assert pending.status == TransactionStatus.REMOVED.value


def test_pending_replacement_never_steals_an_existing_posted_receipt_link(db):
    plaid_item = _plaid_item(db)
    pending = _transaction(
        db,
        plaid_item,
        external_id="pending-collision",
        merchant="Target",
        pending=True,
    )
    posted = _transaction(
        db,
        plaid_item,
        external_id="posted-collision",
        merchant="Target",
    )
    source_receipt = _receipt(
        db,
        external_id="source-collision",
        merchant="Target",
        transaction_id=pending.id,
    )
    target_receipt = _receipt(
        db,
        external_id="target-collision",
        merchant="Target",
        transaction_id=posted.id,
    )

    result = ReceiptTransactionReconciliationService(db).migrate_pending_replacement(
        pending,
        posted,
    )

    assert result.receipt_count == 0
    assert result.ambiguous_receipt_count == 1
    assert source_receipt.transaction_id == pending.id
    assert source_receipt.transaction_match_status == "ambiguous"
    assert source_receipt.transaction_match_evidence_json["reason"] == (
        "pending_replacement_conflict"
    )
    assert target_receipt.transaction_id == posted.id


def test_removed_link_reconciles_to_unique_active_candidate_and_updates_acquisition(db):
    plaid_item = _plaid_item(db)
    removed = _transaction(
        db,
        plaid_item,
        external_id="removed",
        merchant="Target",
        status=TransactionStatus.REMOVED.value,
    )
    active = _transaction(db, plaid_item, external_id="active", merchant="Target")
    receipt = _receipt(
        db,
        external_id="receipt-removed",
        merchant="Target",
        transaction_id=removed.id,
    )
    item = HouseholdItem(workspace_id=1, name="Soap", cadence_days=30, enabled=True)
    db.add(item)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="Soap",
        normalized_name="soap",
        household_item_id=item.id,
        match_status="matched",
    )
    db.add(line)
    db.flush()
    acquisition = HouseholdItemAcquisition(
        workspace_id=1,
        household_item_id=item.id,
        receipt_item_id=line.id,
        transaction_id=removed.id,
        acquired_at=datetime(2026, 8, 15, tzinfo=UTC),
        source="receipt",
        confidence=0.99,
        confirmed=True,
    )
    db.add(acquisition)
    db.flush()

    decisions = ReceiptTransactionReconciliationService(db).reconcile_removed_transaction(removed)

    assert len(decisions) == 1
    assert receipt.transaction_id == active.id
    assert acquisition.transaction_id == active.id


def test_idempotent_rerun_preserves_existing_link_and_does_not_duplicate_learning(db):
    plaid_item = _plaid_item(db)
    transaction = _transaction(db, plaid_item, external_id="tx-idempotent", merchant="Target")
    receipt = _receipt(db, external_id="receipt-idempotent", merchant="Target")
    service = ReceiptTransactionReconciliationService(db)

    first = service.reconcile_receipt(receipt)
    first_attempted_at = receipt.transaction_match_attempted_at
    second = service.reconcile_receipt(receipt)

    assert first.transaction_id == second.transaction_id == transaction.id
    assert second.status == ReceiptTransactionMatchStatus.AUTO_MATCHED
    assert second.evidence == first.evidence
    assert receipt.transaction_match_attempted_at == first_attempted_at
    assert db.scalar(select(func.count(PurchaseReceipt.id))) == 1


def test_cross_workspace_candidate_never_matches(db):
    with _bootstrap_unscoped(db):
        other_item = _plaid_item(db, workspace_id=2, suffix="other")
        _transaction(
            db,
            other_item,
            external_id="other-workspace-tx",
            merchant="Trader Joe's",
        )
    receipt = _receipt(
        db,
        workspace_id=1,
        external_id="tenant-receipt",
        merchant="Trader Joe's",
    )

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.NO_MATCH
    assert decision.transaction_id is None


def test_cross_workspace_pending_replacement_is_a_noop(db):
    first_item = _plaid_item(db, workspace_id=1, suffix="first")
    pending = _transaction(
        db,
        first_item,
        external_id="tenant-pending",
        merchant="Target",
        pending=True,
    )
    with _bootstrap_unscoped(db):
        second_item = _plaid_item(db, workspace_id=2, suffix="second")
        posted = _transaction(
            db,
            second_item,
            external_id="tenant-posted",
            merchant="Target",
        )
    receipt = _receipt(
        db,
        workspace_id=1,
        external_id="tenant-pending-receipt",
        merchant="Target",
        transaction_id=pending.id,
    )

    result = ReceiptTransactionReconciliationService(db).migrate_pending_replacement(
        pending,
        posted,
    )

    assert result.receipt_count == 0
    assert result.acquisition_count == 0
    assert receipt.transaction_id == pending.id


def test_legacy_cross_workspace_link_is_quarantined_instead_of_preserved(db):
    with _bootstrap_unscoped(db):
        other_item = _plaid_item(db, workspace_id=2, suffix="legacy-other")
        other_transaction = _transaction(
            db,
            other_item,
            external_id="legacy-other-transaction",
            merchant="Target",
        )
    receipt = _receipt(
        db,
        workspace_id=1,
        external_id="legacy-bad-link",
        merchant="Target",
        transaction_id=other_transaction.id,
    )

    decision = ReceiptTransactionReconciliationService(db).reconcile_receipt(receipt)

    assert decision.status == ReceiptTransactionMatchStatus.AMBIGUOUS
    assert receipt.transaction_id is None
    assert decision.evidence["reason"] == "existing_link_unavailable_or_workspace_conflict"
