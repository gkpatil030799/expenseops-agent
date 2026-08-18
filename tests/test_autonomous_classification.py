from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import (
    ClassificationActivityType,
    ClassificationConceptAlias,
    ClassificationDecisionRecord,
    ClassificationSettings,
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    HouseholdItemAlias,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentEligibility,
    SpendingParentCategory,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.autonomous_classification_service import (
    AutonomousClassificationService,
)
from app.services.classification_taxonomy_service import ClassificationTaxonomyError
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.replenishment_service import ReplenishmentService
from app.tenancy import TenantContext, set_session_tenant


@pytest.fixture
def classification_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'autonomous-classification.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="autonomous@example.test", display_name="Autonomous")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Autonomous", created_by_user_id=user.id)
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
        plaid = PlaidItem(
            workspace_id=workspace.id,
            item_id="autonomous-plaid",
            owner_user_id=user.id,
        )
        db.add(plaid)
        db.commit()
        set_session_tenant(
            db,
            TenantContext(user_id=user.id, workspace_id=workspace.id),
        )
        db.add(
            ClassificationSettings(
                workspace_id=workspace.id,
                autonomous_enabled=True,
            )
        )
        db.commit()
        yield db, user, workspace, plaid
    engine.dispose()


def _enabled_settings() -> Settings:
    return Settings(
        autonomous_classification_enabled=True,
        autonomous_category_creation_enabled=True,
        autonomous_cadence_estimation_enabled=True,
    )


def _receipt(
    db: Session,
    *,
    name: str,
    classification: str,
    confidence: float,
    category: str | None = None,
    canonical_name: str | None = None,
    complete: bool = True,
    arithmetic_status: str = "verified",
) -> tuple[PurchaseReceipt, PurchaseReceiptItem]:
    receipt = PurchaseReceipt(
        source="test",
        source_external_id=f"receipt-{name}",
        merchant_raw="Example Market",
        merchant_normalized="example market",
        purchased_at=datetime(2026, 8, 17, 12, tzinfo=UTC),
        total_cents=599,
        currency="USD",
        line_items_complete=complete,
        arithmetic_status=arithmetic_status,
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name=name,
        normalized_name=name.casefold(),
        line_total_cents=599,
        category=category,
        classification=classification,
        classification_confidence=confidence,
        canonical_name=canonical_name,
        match_status="unmatched",
    )
    db.add(line)
    db.commit()
    return receipt, line


def test_safe_receipt_line_progresses_without_confirmation_and_is_idempotent(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="ORGANIC EGGS",
        classification="perishable_grocery",
        confidence=0.98,
        category="Groceries",
        canonical_name="Eggs",
    )
    service = AutonomousClassificationService(db, _enabled_settings())

    first = service.classify_receipt(receipt)
    second = service.classify_receipt(receipt)

    db.refresh(line)
    item = db.get(HouseholdItem, line.household_item_id)
    acquisition = db.scalar(
        select(HouseholdItemAcquisition).where(
            HouseholdItemAcquisition.receipt_item_id == line.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    )
    assert first.receipt_items_categorized == 1
    assert first.household_items_auto_created == 1
    assert first.acquisitions_auto_recorded == 1
    assert second.acquisitions_auto_recorded == 0
    assert line.spending_parent_category == "food_dining"
    assert line.replenishment_eligibility == "replenishable"
    assert line.classification_decision_state == "final"
    assert item is not None
    assert item.cadence_source == "category_prior"
    assert item.cadence_days is not None
    assert acquisition is not None and acquisition.user_confirmed is False
    assert receipt.parse_status == "needs_review"
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == 1
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 1


def test_receipt_parser_cannot_create_a_merchant_named_subcategory(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="LATTE",
        classification="dining_or_experience",
        confidence=0.99,
        category="Starbucks Coffee",
    )
    receipt.merchant_raw = "Starbucks"
    receipt.merchant_normalized = "starbucks"
    db.commit()

    AutonomousClassificationService(db, _enabled_settings()).classify_receipt(receipt)

    db.refresh(line)
    assert line.classification_subcategory_name == "Coffee"
    assert line.classification_subcategory_name != "Starbucks Coffee"


def test_dining_is_classified_but_never_becomes_replenishment(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="PANEER TIKKA RESTAURANT",
        classification="dining_or_experience",
        confidence=0.99,
        category="Restaurants",
    )

    result = AutonomousClassificationService(db, _enabled_settings()).classify_receipt(
        receipt
    )

    db.refresh(line)
    assert result.receipt_items_categorized == 1
    assert result.household_items_auto_created == 0
    assert result.acquisitions_auto_recorded == 0
    assert line.item_activity_type == "restaurant_meal"
    assert line.replenishment_eligibility == "not_replenishable"
    assert line.household_item_id is None


def test_medium_evidence_is_provisional_and_does_not_create_taxonomy_or_staple(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="SPECIAL CLEANING POD",
        classification="replenishable_household",
        confidence=0.72,
        category="Special cleaning",
        canonical_name="Special cleaning pod",
    )

    result = AutonomousClassificationService(db, _enabled_settings()).classify_receipt(
        receipt
    )

    db.refresh(line)
    assert result.classifications_provisional == 1
    assert result.categories_auto_created == 0
    assert result.concepts_auto_created == 0
    assert result.household_items_auto_created == 0
    assert result.acquisitions_auto_recorded == 0
    assert line.classification_decision_state == "provisional"
    assert line.classification_auto_finalize_at is not None
    assert line.household_item_id is None


def test_incomplete_or_unreconciled_receipt_never_records_acquisition(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="TOILET PAPER",
        classification="replenishable_household",
        confidence=0.99,
        canonical_name="Toilet paper",
        complete=False,
        arithmetic_status="mismatch",
    )

    result = AutonomousClassificationService(db, _enabled_settings()).classify_receipt(
        receipt
    )

    db.refresh(line)
    assert result.household_items_auto_created == 0
    assert result.acquisitions_auto_recorded == 0
    assert line.household_item_id is None
    assert db.scalar(select(func.count(HouseholdItemAcquisition.id))) == 0


def test_transaction_sign_and_canonical_consumer_projection_are_separate(
    classification_db,
) -> None:
    db, _user, _workspace, plaid = classification_db
    purchase = ExpenseTransaction(
        plaid_item_id=plaid.id,
        plaid_transaction_id="coffee-purchase",
        name="Coffee Shop",
        merchant_name="Coffee Shop",
        amount_cents=650,
        date=date(2026, 8, 17),
        category="Food and Drink / Coffee Shop",
        provider_category="Food and Drink / Coffee Shop",
    )
    credit = ExpenseTransaction(
        plaid_item_id=plaid.id,
        plaid_transaction_id="coffee-credit",
        name="Coffee Shop refund",
        merchant_name="Coffee Shop",
        amount_cents=-650,
        date=date(2026, 8, 17),
        category="Food and Drink / Coffee Shop",
        provider_category="Food and Drink / Coffee Shop",
    )
    db.add_all([purchase, credit])
    db.commit()
    service = AutonomousClassificationService(db, _enabled_settings())

    service.classify_transaction(purchase)
    service.classify_transaction(credit)

    db.refresh(purchase)
    db.refresh(credit)
    assert purchase.spending_parent_category == "food_dining"
    assert purchase.classification_activity_type == "coffee_beverage"
    assert credit.spending_parent_category == "fees_taxes_discounts"
    assert credit.classification_activity_type == "refund"
    assert credit.replenishment_eligibility == "not_replenishable"


def test_global_and_workspace_kill_switches_stop_all_application(
    classification_db,
) -> None:
    db, _user, _workspace, plaid = classification_db
    tx = ExpenseTransaction(
        plaid_item_id=plaid.id,
        plaid_transaction_id="kill-switch",
        name="Starbucks coffee",
        merchant_name="Starbucks coffee",
        amount_cents=500,
        date=date(2026, 8, 17),
    )
    db.add(tx)
    db.commit()

    global_off = AutonomousClassificationService(
        db, Settings(autonomous_classification_enabled=False)
    ).classify_transaction(tx)
    assert global_off.skipped == 1
    assert tx.classification_applied_at is None

    workspace_settings = db.scalar(select(ClassificationSettings))
    assert workspace_settings is not None
    workspace_settings.autonomous_enabled = False
    db.commit()
    workspace_off = AutonomousClassificationService(
        db, _enabled_settings()
    ).classify_transaction(tx)
    assert workspace_off.skipped == 1
    assert tx.classification_applied_at is None


def test_due_provisional_finalization_preserves_user_corrections(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="SPECIAL CLEANING POD",
        classification="replenishable_household",
        confidence=0.72,
        category="Special cleaning",
        canonical_name="Special cleaning pod",
    )
    service = AutonomousClassificationService(db, _enabled_settings())
    service.classify_receipt(receipt)
    db.refresh(line)
    line.classification_auto_finalize_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    result = service.finalize_decision(line)

    db.refresh(line)
    assert result.classifications_auto_finalized == 1
    assert line.classification_decision_state == "final"
    assert line.classification_auto_finalize_at is None
    assert line.household_item_id is None
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == 2


def test_user_correction_repairs_then_undoes_autonomous_learning(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="ORGANIC EGGS",
        classification="perishable_grocery",
        confidence=0.98,
        category="Groceries",
        canonical_name="Eggs",
    )
    service = AutonomousClassificationService(db, _enabled_settings())
    service.classify_receipt(receipt)
    db.refresh(line)
    original_item = db.get(HouseholdItem, line.household_item_id)
    original_acquisition = db.scalar(
        select(HouseholdItemAcquisition).where(
            HouseholdItemAcquisition.receipt_item_id == line.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    )
    assert original_item is not None and original_acquisition is not None

    repaired = service.correct_receipt_line(
        line.id,
        spending_parent_category=SpendingParentCategory.HOUSEHOLD_HOME,
        subcategory_name="Dishwashing supplies",
        canonical_concept="Dish soap",
        item_activity_type=ClassificationActivityType.HOUSEHOLD_CONSUMABLE,
        replenishment_eligibility=ReplenishmentEligibility.REPLENISHABLE,
    )

    db.refresh(line)
    db.refresh(original_item)
    db.refresh(original_acquisition)
    repaired_item = db.get(HouseholdItem, line.household_item_id)
    repaired_acquisition = db.scalar(
        select(HouseholdItemAcquisition).where(
            HouseholdItemAcquisition.receipt_item_id == line.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    )
    assert repaired.version == 2
    assert line.classification_decision_state == "corrected"
    assert line.classification_authority == "user_correction"
    assert original_acquisition.voided_at is not None
    assert original_item.enabled is False
    assert repaired_item is not None and repaired_item.name == "Dish soap"
    assert repaired_item.cadence_source == "category_prior"
    assert repaired_acquisition is not None
    assert repaired_acquisition.user_confirmed is True
    assert repaired_acquisition.supersedes_acquisition_id == original_acquisition.id

    removed = service.correct_receipt_line(
        line.id,
        spending_parent_category=SpendingParentCategory.LIFESTYLE_SHOPPING,
        subcategory_name="General merchandise",
        canonical_concept=None,
        item_activity_type=ClassificationActivityType.ONE_TIME_PURCHASE,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
    )

    db.refresh(line)
    db.refresh(repaired_item)
    db.refresh(repaired_acquisition)
    assert removed.version == 3
    assert line.household_item_id is None
    assert line.match_status == "irrelevant"
    assert repaired_acquisition.voided_at is not None
    assert repaired_item.enabled is False
    assert (
        db.scalar(
            select(func.count(HouseholdItemAcquisition.id)).where(
                HouseholdItemAcquisition.receipt_item_id == line.id,
                HouseholdItemAcquisition.voided_at.is_(None),
            )
        )
        == 0
    )


def test_transaction_user_correction_cannot_be_overwritten_by_automation(
    classification_db,
) -> None:
    db, _user, _workspace, plaid = classification_db
    transaction = ExpenseTransaction(
        plaid_item_id=plaid.id,
        plaid_transaction_id="transaction-correction",
        name="Ambiguous Store",
        merchant_name="Ambiguous Store",
        amount_cents=1_500,
        date=date(2026, 8, 17),
    )
    db.add(transaction)
    db.commit()
    service = AutonomousClassificationService(db, _enabled_settings())
    service.classify_transaction(transaction)

    corrected = service.correct_transaction(
        transaction.id,
        spending_parent_category=SpendingParentCategory.TRANSPORTATION,
        subcategory_name="Public transit",
        canonical_concept=None,
        item_activity_type=ClassificationActivityType.TRANSPORTATION,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
    )
    replay = service.classify_transaction(transaction)

    db.refresh(transaction)
    assert corrected.version == 2
    assert replay.skipped == 1
    assert transaction.spending_parent_category == "transportation"
    assert transaction.classification_authority == "user_correction"
    assert transaction.classification_decision_state == "corrected"


def test_transaction_user_correction_teaches_future_same_merchant_classification(
    classification_db,
) -> None:
    db, _user, _workspace, plaid = classification_db
    corrected_transaction = ExpenseTransaction(
        plaid_item_id=plaid.id,
        plaid_transaction_id="merchant-correction-source",
        name="Starbucks 1234",
        merchant_name="Starbucks",
        amount_cents=650,
        date=date(2026, 8, 17),
    )
    db.add(corrected_transaction)
    db.commit()
    service = AutonomousClassificationService(db, _enabled_settings())
    service.classify_transaction(corrected_transaction)

    service.correct_transaction(
        corrected_transaction.id,
        spending_parent_category=SpendingParentCategory.FOOD_DINING,
        subcategory_name="Coffee shops",
        canonical_concept="Coffee shop purchase",
        item_activity_type=ClassificationActivityType.COFFEE_BEVERAGE,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
    )
    future = ExpenseTransaction(
        plaid_item_id=plaid.id,
        plaid_transaction_id="merchant-correction-future",
        name="Starbucks Store 9876",
        merchant_name="Starbucks",
        amount_cents=725,
        date=date(2026, 8, 18),
    )
    db.add(future)
    db.commit()

    result = service.classify_transaction(future)

    db.refresh(future)
    assert result.transactions_categorized == 1
    assert future.spending_parent_category == "food_dining"
    assert future.classification_subcategory_name == "Coffee shops"
    assert future.classification_concept_name == "Coffee shop purchase"
    assert future.classification_authority == "confirmed_alias"


def test_ignoring_receipt_reverts_only_unconfirmed_autonomous_learning(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="ORGANIC EGGS",
        classification="perishable_grocery",
        confidence=0.98,
        category="Groceries",
        canonical_name="Eggs",
    )
    autonomous = AutonomousClassificationService(db, _enabled_settings())
    autonomous.classify_receipt(receipt)
    db.refresh(line)
    item = db.get(HouseholdItem, line.household_item_id)
    acquisition = db.scalar(
        select(HouseholdItemAcquisition).where(
            HouseholdItemAcquisition.receipt_item_id == line.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    )
    assert item is not None and acquisition is not None

    ignored = ReceiptIngestionService(
        db,
        _enabled_settings(),
        parser=object(),
    ).ignore(receipt.id)

    db.refresh(line)
    db.refresh(item)
    db.refresh(acquisition)
    assert ignored.parse_status == "ignored"
    assert acquisition.voided_at is not None
    assert line.household_item_id is None
    assert line.match_status == "rejected"
    assert item.enabled is False
    assert db.scalar(
        select(func.count(HouseholdItemAlias.id)).where(
            HouseholdItemAlias.household_item_id == item.id,
            HouseholdItemAlias.voided_at.is_(None),
        )
    ) == 0
    assert db.scalar(
        select(func.count(ClassificationConceptAlias.id)).where(
            ClassificationConceptAlias.workspace_id == receipt.workspace_id,
            ClassificationConceptAlias.voided_at.is_(None),
        )
    ) == 0
    replay = autonomous.classify_receipt(ignored)
    assert replay.receipt_items_categorized == 0
    assert replay.skipped == 1


def test_ignored_receipt_correction_is_blocked_until_restore(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="ORGANIC EGGS",
        classification="perishable_grocery",
        confidence=0.98,
        category="Groceries",
        canonical_name="Eggs",
    )
    settings = _enabled_settings()
    autonomous = AutonomousClassificationService(db, settings)
    autonomous.classify_receipt(receipt)
    ingestion = ReceiptIngestionService(db, settings, parser=object())
    ingestion.ignore(receipt.id)

    with pytest.raises(
        ClassificationTaxonomyError,
        match="must be restored before correction",
    ):
        autonomous.correct_receipt_line(
            line.id,
            spending_parent_category=SpendingParentCategory.FOOD_DINING,
            subcategory_name="Coffee shops",
            canonical_concept="Coffee purchase",
            item_activity_type=ClassificationActivityType.COFFEE_BEVERAGE,
            replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
        )
    assert db.scalar(
        select(func.count(HouseholdItemAcquisition.id)).where(
            HouseholdItemAcquisition.receipt_item_id == line.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    ) == 0

    restored = ingestion.restore(receipt.id)
    corrected = autonomous.correct_receipt_line(
        line.id,
        spending_parent_category=SpendingParentCategory.FOOD_DINING,
        subcategory_name="Coffee shops",
        canonical_concept="Coffee purchase",
        item_activity_type=ClassificationActivityType.COFFEE_BEVERAGE,
        replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
    )

    assert restored.parse_status == "needs_review"
    assert corrected.version == 2
    db.refresh(line)
    assert line.classification_decision_state == "corrected"
    assert line.household_item_id is None

    receipt.parse_status = "failed"
    db.commit()
    with pytest.raises(
        ClassificationTaxonomyError,
        match="must be restored before correction",
    ):
        autonomous.correct_receipt_line(
            line.id,
            spending_parent_category=SpendingParentCategory.FOOD_DINING,
            subcategory_name="Restaurants",
            canonical_concept=None,
            item_activity_type=ClassificationActivityType.RESTAURANT_MEAL,
            replenishment_eligibility=ReplenishmentEligibility.NOT_REPLENISHABLE,
        )


def test_explicit_confirmation_upgrades_autonomous_learning_before_ignore(
    classification_db,
) -> None:
    db, _user, _workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="ORGANIC EGGS",
        classification="perishable_grocery",
        confidence=0.98,
        category="Groceries",
        canonical_name="Eggs",
    )
    settings = _enabled_settings()
    AutonomousClassificationService(db, settings).classify_receipt(receipt)
    ingestion = ReceiptIngestionService(db, settings, parser=object())
    ingestion.confirm(receipt.id)
    acquisition = db.scalar(
        select(HouseholdItemAcquisition).where(
            HouseholdItemAcquisition.receipt_item_id == line.id,
            HouseholdItemAcquisition.voided_at.is_(None),
        )
    )
    assert acquisition is not None
    assert acquisition.user_confirmed is True
    assert acquisition.source == "receipt_test"

    ingestion.ignore(receipt.id)

    db.refresh(line)
    db.refresh(acquisition)
    assert acquisition.voided_at is None
    assert line.household_item_id == acquisition.household_item_id
    alias = db.scalar(
        select(HouseholdItemAlias).where(
            HouseholdItemAlias.household_item_id == acquisition.household_item_id,
            HouseholdItemAlias.voided_at.is_(None),
        )
    )
    assert alias is not None and alias.source == "confirmed_receipt"
    classification_alias = db.scalar(
        select(ClassificationConceptAlias).where(
            ClassificationConceptAlias.workspace_id == receipt.workspace_id,
            ClassificationConceptAlias.voided_at.is_(None),
        )
    )
    assert classification_alias is not None
    assert classification_alias.source == "confirmed_alias"


def test_deleting_a_classified_household_item_soft_disables_it_and_preserves_audit(
    classification_db,
) -> None:
    db, _user, workspace, _plaid = classification_db
    receipt, line = _receipt(
        db,
        name="ORGANIC EGGS",
        classification="perishable_grocery",
        confidence=0.98,
        category="Groceries",
        canonical_name="Eggs",
    )
    AutonomousClassificationService(db, _enabled_settings()).classify_receipt(receipt)
    db.refresh(line)
    item_id = line.household_item_id
    assert item_id is not None

    service = ReplenishmentService(db)
    service.delete_item(item_id)

    preserved = db.get(HouseholdItem, item_id)
    assert preserved is not None and preserved.enabled is False
    assert item_id not in {item.id for item in service.list_items()}
    assert db.scalar(
        select(func.count(ClassificationDecisionRecord.id)).where(
            ClassificationDecisionRecord.workspace_id == workspace.id,
            ClassificationDecisionRecord.household_item_id == item_id,
        )
    ) == 1
