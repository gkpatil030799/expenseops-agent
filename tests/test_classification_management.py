from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.api.classification_routes import (
    ClassificationSubcategoryMerge,
    ClassificationSubcategoryRename,
    HouseholdItemMerge,
    HouseholdItemMergeUndo,
)
from app.db import Base
from app.models import (
    AuditEvent,
    ClassificationAuthority,
    ClassificationConcept,
    ClassificationDecisionRecord,
    ClassificationSettings,
    ClassificationSubcategory,
    Errand,
    ErrandHouseholdItem,
    ErrandPlan,
    ErrandPlanStop,
    ErrandPlanStopHouseholdItem,
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    HouseholdItemAlias,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentPrediction,
    SpendingParentCategory,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.classification_taxonomy_service import (
    ClassificationCollisionError,
    ClassificationSourceType,
    ClassificationTaxonomyError,
    ClassificationTaxonomyService,
    classify_known_text,
)
from app.services.household_item_merge_service import HouseholdItemMergeService
from app.tenancy import TenantContext, clear_session_tenant, set_session_tenant


def test_management_contracts_forbid_tenant_and_unbounded_fields() -> None:
    with pytest.raises(ValidationError):
        HouseholdItemMerge.model_validate({"target_household_item_id": 2, "workspace_id": 99})
    with pytest.raises(ValidationError):
        HouseholdItemMergeUndo.model_validate({"merge_event_id": 1, "force": True})
    with pytest.raises(ValidationError):
        ClassificationSubcategoryMerge.model_validate({"target_subcategory_id": "2"})
    with pytest.raises(ValidationError):
        ClassificationSubcategoryRename.model_validate({"name": "x" * 129})


@pytest.fixture
def management_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'classification-management.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="management-owner@example.test", display_name="Owner")
        other_owner = User(email="management-other@example.test", display_name="Other")
        db.add_all([owner, other_owner])
        db.flush()
        workspace = Workspace(name="Managed home", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other home", created_by_user_id=other_owner.id)
        db.add_all([workspace, other_workspace])
        db.flush()
        db.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    role="owner",
                    is_default=True,
                ),
                WorkspaceMembership(
                    workspace_id=other_workspace.id,
                    user_id=other_owner.id,
                    role="owner",
                    is_default=True,
                ),
                ClassificationSettings(workspace_id=workspace.id, autonomous_enabled=True),
            ]
        )
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))
        yield db, workspace, other_workspace
    engine.dispose()


def test_subcategory_rename_and_merge_repair_current_projections_and_append_ledger(
    management_db,
) -> None:
    db, workspace, _other_workspace = management_db
    receipt = PurchaseReceipt(
        workspace_id=workspace.id,
        source="test",
        source_external_id="subcategory-receipt",
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="TOILET PAPER",
        normalized_name="toilet paper",
    )
    db.add(line)
    db.commit()
    service = ClassificationTaxonomyService(db)
    applied = service.apply_decision(
        classify_known_text(
            source_type=ClassificationSourceType.RECEIPT_LINE,
            source_entity_id=line.id,
            text=line.raw_name,
        ),
        create_household_item=False,
    )
    source = db.get(ClassificationSubcategory, applied.subcategory_id)
    target = service.ensure_subcategory(
        parent=SpendingParentCategory.HOUSEHOLD_HOME,
        name="Home paper supplies",
        confidence=1.0,
        source=ClassificationAuthority.USER_CORRECTION,
    )
    db.commit()

    renamed = service.rename_subcategory(source.id, name="Paper essentials")

    assert renamed.applied is True
    assert line.classification_subcategory_name == "Paper essentials"
    assert line.classification_version == 2
    assert (
        db.scalar(
            select(func.count(ClassificationDecisionRecord.id)).where(
                ClassificationDecisionRecord.source_type == "receipt_line",
                ClassificationDecisionRecord.source_entity_id == line.id,
            )
        )
        == 2
    )

    merged = service.merge_subcategories(
        source.id,
        target_subcategory_id=target.id,
    )

    assert merged.applied is True
    assert source.merged_into_id == target.id
    assert line.classification_subcategory_id == target.id
    assert line.classification_subcategory_name == target.name
    assert line.classification_version == 3
    records = list(
        db.scalars(
            select(ClassificationDecisionRecord)
            .where(ClassificationDecisionRecord.source_entity_id == line.id)
            .order_by(ClassificationDecisionRecord.version)
        )
    )
    assert [record.subcategory_name for record in records] == [
        "Paper goods",
        "Paper essentials",
        "Home paper supplies",
    ]
    assert [record.provenance_json for record in records[-2:]] == [
        ["subcategory_rename"],
        ["subcategory_merge"],
    ]


def test_subcategory_merge_rejects_incompatible_cross_tenant_and_cycles(
    management_db,
) -> None:
    db, workspace, other_workspace = management_db
    own_a = ClassificationSubcategory(
        workspace_id=workspace.id,
        parent_category="food_dining",
        name="Cafe",
        normalized_name="cafe",
        source="user_correction",
        confidence=1.0,
    )
    own_b = ClassificationSubcategory(
        workspace_id=workspace.id,
        parent_category="household_home",
        name="Home",
        normalized_name="home",
        source="user_correction",
        confidence=1.0,
    )
    foreign = ClassificationSubcategory(
        workspace_id=other_workspace.id,
        parent_category="food_dining",
        name="Foreign cafe",
        normalized_name="foreign cafe",
        source="user_correction",
        confidence=1.0,
    )
    db.add_all([own_a, own_b])
    db.commit()
    clear_session_tenant(db)
    db.add(foreign)
    db.commit()
    set_session_tenant(db, TenantContext(workspace.created_by_user_id, workspace.id))
    service = ClassificationTaxonomyService(db)

    with pytest.raises(ClassificationCollisionError, match="parent categories"):
        service.merge_subcategories(own_a.id, target_subcategory_id=own_b.id)
    db.rollback()
    with pytest.raises(ClassificationTaxonomyError, match="not found"):
        service.merge_subcategories(own_a.id, target_subcategory_id=foreign.id)
    db.rollback()
    own_b.parent_category = "food_dining"
    db.commit()
    own_b.merged_into_id = own_a.id
    db.commit()
    with pytest.raises(ClassificationCollisionError, match="cycle"):
        service.merge_subcategories(own_a.id, target_subcategory_id=own_b.id)


def test_household_item_merge_and_undo_preserve_facts_and_repair_dependents(
    management_db,
) -> None:
    db, workspace, _other_workspace = management_db
    receipt = PurchaseReceipt(
        workspace_id=workspace.id,
        source="test",
        source_external_id="household-merge-receipt",
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="TOILET PAPER",
        normalized_name="toilet paper",
    )
    db.add(line)
    db.commit()
    taxonomy = ClassificationTaxonomyService(db)
    applied = taxonomy.apply_decision(
        classify_known_text(
            source_type=ClassificationSourceType.RECEIPT_LINE,
            source_entity_id=line.id,
            text=line.raw_name,
        ),
        raw_alias=line.raw_name,
        create_household_item=True,
    )
    source = db.get(HouseholdItem, applied.household_item_id)
    target = HouseholdItem(
        workspace_id=workspace.id,
        name="Bathroom tissue",
        canonical_key="bathroom tissue canonical",
        classification_concept_id=source.classification_concept_id,
        spending_parent_category=source.spending_parent_category,
        replenishment_eligibility=source.replenishment_eligibility,
        classification_confidence=1.0,
        classification_provenance_json=["user_correction"],
        cadence_days=None,
        cadence_source="learning",
        cadence_confidence=0.0,
        enabled=True,
    )
    db.add(target)
    db.flush()
    duplicate_target_alias = HouseholdItemAlias(
        household_item_id=target.id,
        merchant_normalized="",
        raw_pattern="TOILET PAPER",
        normalized_alias="toilet paper",
        confidence=1.0,
        source="user",
    )
    unique_source_alias = HouseholdItemAlias(
        household_item_id=source.id,
        merchant_normalized="store",
        raw_pattern="BATH TISSUE",
        normalized_alias="bath tissue",
        confidence=1.0,
        source="user",
    )
    start = datetime(2026, 6, 1, 12, tzinfo=UTC)
    source_acquisitions = [
        HouseholdItemAcquisition(
            workspace_id=workspace.id,
            household_item_id=source.id,
            acquired_at=start,
            source="receipt",
            confidence=1.0,
            confirmed=True,
            logical_purchase_key="merge-source-one",
        ),
        HouseholdItemAcquisition(
            workspace_id=workspace.id,
            household_item_id=source.id,
            acquired_at=start + timedelta(days=30),
            source="receipt",
            confidence=1.0,
            confirmed=True,
            logical_purchase_key="merge-source-two",
        ),
    ]
    target_acquisition = HouseholdItemAcquisition(
        workspace_id=workspace.id,
        household_item_id=target.id,
        acquired_at=start + timedelta(days=60),
        source="manual",
        confidence=1.0,
        confirmed=True,
        logical_purchase_key="merge-target-one",
    )
    prediction = ReplenishmentPrediction(
        workspace_id=workspace.id,
        household_item_id=source.id,
        generated_at=start + timedelta(days=31),
        predicted_need_at=start + timedelta(days=60),
        predicted_days_remaining=29,
        due_score=0.2,
        method="deterministic",
        confidence=0.7,
        confidence_level="medium",
        feature_snapshot={},
    )
    errand = Errand(
        workspace_id=workspace.id,
        title="Buy paper",
        errand_type="purchase",
    )
    plan = ErrandPlan(workspace_id=workspace.id, estimated_stop_minutes=0)
    db.add_all(
        [
            duplicate_target_alias,
            unique_source_alias,
            *source_acquisitions,
            target_acquisition,
            prediction,
            errand,
            plan,
        ]
    )
    db.flush()
    stop = ErrandPlanStop(plan_id=plan.id, stop_order=1, place_name="Store")
    db.add(stop)
    db.flush()
    db.add_all(
        [
            ErrandHouseholdItem(errand_id=errand.id, household_item_id=source.id),
            ErrandHouseholdItem(errand_id=errand.id, household_item_id=target.id),
            ErrandPlanStopHouseholdItem(
                stop_id=stop.id,
                household_item_id=source.id,
                reason="source",
            ),
        ]
    )
    db.commit()
    original_decision_count = db.scalar(select(func.count(ClassificationDecisionRecord.id)))

    merged = HouseholdItemMergeService(db).merge(source.id, target_item_id=target.id)

    assert source.enabled is False
    assert source.merged_into_id == target.id
    assert source.merged_at is not None
    assert line.household_item_id == target.id
    assert all(value.household_item_id == target.id for value in source_acquisitions)
    assert unique_source_alias.household_item_id == target.id
    assert db.scalar(select(func.count(ClassificationDecisionRecord.id))) == (
        original_decision_count + 1
    )
    assert (
        db.scalar(
            select(func.count(ReplenishmentPrediction.id)).where(
                ReplenishmentPrediction.household_item_id == source.id
            )
        )
        == 0
    )
    assert merged.predictions_invalidated == 1
    assert target.last_acquired_at == target_acquisition.acquired_at
    assert target.cadence_days == 30
    assert (
        db.scalar(
            select(func.count(ErrandHouseholdItem.id)).where(
                ErrandHouseholdItem.errand_id == errand.id
            )
        )
        == 1
    )
    assert (
        db.get(AuditEvent, merged.merge_event_id).metadata_json["provider_records_changed"] is False
    )

    undone = HouseholdItemMergeService(db).undo(
        source.id,
        merge_event_id=merged.merge_event_id,
    )

    assert undone.reverted is True
    assert source.enabled is True
    assert source.merged_into_id is None
    assert line.household_item_id == source.id
    assert all(value.household_item_id == source.id for value in source_acquisitions)
    assert unique_source_alias.household_item_id == source.id
    assert (
        db.scalar(
            select(func.count(ErrandHouseholdItem.id)).where(
                ErrandHouseholdItem.errand_id == errand.id
            )
        )
        == 2
    )
    assert (
        db.scalar(
            select(func.count(ErrandPlanStopHouseholdItem.id)).where(
                ErrandPlanStopHouseholdItem.stop_id == stop.id
            )
        )
        == 1
    )
    assert db.scalar(
        select(ErrandPlanStopHouseholdItem.reason).where(
            ErrandPlanStopHouseholdItem.stop_id == stop.id,
            ErrandPlanStopHouseholdItem.household_item_id == source.id,
        )
    ) == "source"
    assert source.cadence_days == 30
    assert target.last_acquired_at == target_acquisition.acquired_at


def test_household_item_merge_fails_closed_for_cross_tenant_or_incompatible_items(
    management_db,
) -> None:
    db, workspace, other_workspace = management_db
    source = HouseholdItem(
        workspace_id=workspace.id,
        name="Milk",
        cadence_days=7,
        cadence_source="configured",
        cadence_confidence=1.0,
        cadence_min_days=7,
        cadence_max_days=7,
        spending_parent_category="food_dining",
        replenishment_eligibility="replenishable",
        enabled=True,
    )
    incompatible = HouseholdItem(
        workspace_id=workspace.id,
        name="Coffee visit",
        cadence_days=7,
        cadence_source="configured",
        cadence_confidence=1.0,
        cadence_min_days=7,
        cadence_max_days=7,
        spending_parent_category="food_dining",
        replenishment_eligibility="not_replenishable",
        enabled=True,
    )
    foreign = HouseholdItem(
        workspace_id=other_workspace.id,
        name="Foreign milk",
        cadence_days=7,
        cadence_source="configured",
        cadence_confidence=1.0,
        cadence_min_days=7,
        cadence_max_days=7,
        spending_parent_category="food_dining",
        replenishment_eligibility="replenishable",
        enabled=True,
    )
    db.add_all([source, incompatible])
    db.commit()
    clear_session_tenant(db)
    db.add(foreign)
    db.commit()
    set_session_tenant(db, TenantContext(workspace.created_by_user_id, workspace.id))
    service = HouseholdItemMergeService(db)

    with pytest.raises(ClassificationCollisionError, match="itself"):
        service.merge(source.id, target_item_id=source.id)
    db.rollback()
    with pytest.raises(ClassificationCollisionError, match="incompatible"):
        service.merge(source.id, target_item_id=incompatible.id)
    db.rollback()
    incompatible.replenishment_eligibility = source.replenishment_eligibility
    incompatible.enabled = False
    db.commit()
    with pytest.raises(ClassificationCollisionError, match="target household item is not active"):
        service.merge(source.id, target_item_id=incompatible.id)
    db.rollback()
    with pytest.raises(ClassificationTaxonomyError, match="not found"):
        service.merge(source.id, target_item_id=foreign.id)


def test_distinct_household_concepts_require_taxonomy_merge_before_history_merge(
    management_db,
) -> None:
    db, workspace, _other_workspace = management_db
    receipt = PurchaseReceipt(
        workspace_id=workspace.id,
        source="test",
        source_external_id="distinct-concept-receipt",
        parse_status="needs_review",
    )
    db.add(receipt)
    db.flush()
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="TOILET PAPER",
        normalized_name="toilet paper",
    )
    db.add(line)
    db.commit()
    taxonomy = ClassificationTaxonomyService(db)
    application = taxonomy.apply_decision(
        classify_known_text(
            source_type=ClassificationSourceType.RECEIPT_LINE,
            source_entity_id=line.id,
            text=line.raw_name,
        ),
        raw_alias=line.raw_name,
        create_household_item=True,
    )
    source = db.get(HouseholdItem, application.household_item_id)
    source_concept = db.get(ClassificationConcept, application.concept_id)
    target_concept = ClassificationConcept(
        workspace_id=workspace.id,
        parent_category=source_concept.parent_category,
        subcategory_id=source_concept.subcategory_id,
        name="Bathroom tissue",
        normalized_name="bathroom tissue",
        item_activity_type=source_concept.item_activity_type,
        replenishment_eligibility=source_concept.replenishment_eligibility,
        source="user_correction",
        confidence=1.0,
    )
    db.add(target_concept)
    db.flush()
    target = HouseholdItem(
        workspace_id=workspace.id,
        name="Bathroom tissue",
        canonical_key="bathroom tissue",
        classification_concept_id=target_concept.id,
        spending_parent_category=source.spending_parent_category,
        replenishment_eligibility=source.replenishment_eligibility,
        classification_confidence=1.0,
        classification_provenance_json=["user_correction"],
        cadence_days=None,
        cadence_source="learning",
        cadence_confidence=0.0,
        enabled=True,
    )
    db.add(target)
    db.commit()

    with pytest.raises(ClassificationCollisionError, match="different concepts"):
        HouseholdItemMergeService(db).merge(source.id, target_item_id=target.id)
    db.rollback()

    taxonomy.merge_concepts(source_concept.id, target_concept_id=target_concept.id)
    assert source.classification_concept_id == target_concept.id
    assert taxonomy.resolve_alias("TOILET PAPER").id == target_concept.id
    future_decision = classify_known_text(
        source_type=ClassificationSourceType.RECEIPT_LINE,
        source_entity_id=line.id,
        text=line.raw_name,
    )
    with pytest.raises(ClassificationCollisionError, match="multiple active"):
        taxonomy.ensure_household_item(future_decision, target_concept)

    merged = HouseholdItemMergeService(db).merge(source.id, target_item_id=target.id)

    assert merged.applied is True
    assert source.enabled is False
    assert target.enabled is True
    assert (
        db.scalar(
            select(func.count(HouseholdItem.id)).where(
                HouseholdItem.workspace_id == workspace.id,
                HouseholdItem.classification_concept_id == target_concept.id,
                HouseholdItem.enabled.is_(True),
            )
        )
        == 1
    )
    assert taxonomy.ensure_household_item(future_decision, target_concept).id == target.id


def test_taxonomy_mutations_follow_transaction_then_receipt_line_lock_order(
    management_db,
) -> None:
    db, workspace, _other_workspace = management_db
    owner_id = workspace.created_by_user_id
    plaid = PlaidItem(
        workspace_id=workspace.id,
        item_id="taxonomy-lock-order-plaid",
        owner_user_id=owner_id,
    )
    receipt = PurchaseReceipt(
        workspace_id=workspace.id,
        source="test",
        source_external_id="taxonomy-lock-order-receipt",
        parse_status="needs_review",
    )
    db.add_all([plaid, receipt])
    db.flush()
    transaction = ExpenseTransaction(
        workspace_id=workspace.id,
        plaid_transaction_id="taxonomy-lock-order-transaction",
        plaid_item_id=plaid.id,
        merchant_name="Toilet paper",
        name="Toilet paper",
        amount_cents=1_000,
        date=datetime(2026, 8, 17, tzinfo=UTC).date(),
    )
    line = PurchaseReceiptItem(
        receipt_id=receipt.id,
        raw_name="TOILET PAPER",
        normalized_name="toilet paper",
    )
    db.add_all([transaction, line])
    db.commit()
    taxonomy = ClassificationTaxonomyService(db)
    line_application = taxonomy.apply_decision(
        classify_known_text(
            source_type=ClassificationSourceType.RECEIPT_LINE,
            source_entity_id=line.id,
            text=line.raw_name,
        ),
        create_household_item=False,
    )
    transaction_application = taxonomy.apply_decision(
        classify_known_text(
            source_type=ClassificationSourceType.TRANSACTION,
            source_entity_id=transaction.id,
            text=transaction.name,
        )
    )
    assert transaction_application.concept_id == line_application.concept_id

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement.casefold())

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        taxonomy._locked_concept_projections(line_application.concept_id)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)

    transaction_select = next(
        index
        for index, statement in enumerate(statements)
        if "from expense_transactions" in statement
    )
    receipt_select = next(
        index
        for index, statement in enumerate(statements)
        if "from purchase_receipt_items" in statement
    )
    assert transaction_select < receipt_select
