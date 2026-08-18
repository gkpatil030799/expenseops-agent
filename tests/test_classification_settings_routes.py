from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.api.classification_routes import (
    ClassificationConceptMerge,
    ClassificationConceptRename,
    ClassificationCorrection,
    ClassificationSettingsUpdate,
    classification_settings,
    correct_receipt_line_classification,
    correct_transaction_classification,
    list_classification_concepts,
    merge_classification_concepts,
    rename_classification_concept,
    update_classification_settings,
)
from app.api.deps import get_current_workspace_owner
from app.db import Base
from app.models import (
    ClassificationConcept,
    ClassificationSettings,
    ExpenseTransaction,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.tenancy import TenantContext, set_session_tenant


def test_owner_can_toggle_workspace_classification_but_global_off_stays_effectively_off(
    tmp_path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'classification-settings.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        "app.api.classification_routes.get_settings",
        lambda: SimpleNamespace(autonomous_classification_enabled=False),
    )
    with Session(engine) as db:
        owner = User(email="classification-owner@example.test", display_name="Owner")
        other_owner = User(email="classification-other@example.test", display_name="Other")
        db.add_all([owner, other_owner])
        db.flush()
        workspace = Workspace(name="Classification home", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other home", created_by_user_id=other_owner.id)
        db.add_all([workspace, other_workspace])
        db.flush()
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=owner.id,
            role="owner",
            is_default=True,
        )
        db.add_all(
            [
                membership,
                WorkspaceMembership(
                    workspace_id=other_workspace.id,
                    user_id=other_owner.id,
                    role="owner",
                    is_default=True,
                ),
                ClassificationSettings(
                    workspace_id=other_workspace.id,
                    autonomous_enabled=False,
                ),
            ]
        )
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))

        initial = classification_settings(db, membership)
        assert initial.autonomous_enabled is False
        assert initial.global_rollout_enabled is False
        assert initial.effective_autonomous_enabled is False

        updated = update_classification_settings(
            ClassificationSettingsUpdate(autonomous_enabled=True),
            db,
            owner,
            workspace,
            membership,
        )
        assert updated.autonomous_enabled is True
        assert updated.effective_autonomous_enabled is False
        own_row = db.scalar(
            select(ClassificationSettings).where(
                ClassificationSettings.workspace_id == workspace.id
            )
        )
        assert own_row is not None and own_row.autonomous_enabled is True
        other_enabled = db.execute(
            text(
                "SELECT autonomous_enabled FROM classification_settings "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": other_workspace.id},
        ).scalar_one()
        assert other_enabled == 0
    engine.dispose()


def test_classification_settings_reject_unknown_fields_and_non_owner(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        ClassificationSettingsUpdate.model_validate(
            {"autonomous_enabled": True, "workspace_id": 99}
        )

    membership = SimpleNamespace(role="member")
    monkeypatch.setattr(
        "app.api.deps.require_membership",
        lambda _db, _user_id, _workspace_id: membership,
    )
    with pytest.raises(HTTPException) as error:
        get_current_workspace_owner(
            SimpleNamespace(),
            SimpleNamespace(id=7),
            SimpleNamespace(id=3),
        )
    assert error.value.status_code == 403


def test_correction_contract_is_strict_and_cross_workspace_ids_fail_closed(tmp_path) -> None:
    with pytest.raises(ValidationError):
        ClassificationCorrection.model_validate(
            {
                "spending_parent_category": "food_dining",
                "item_activity_type": "grocery",
                "replenishment_eligibility": "replenishable",
                "workspace_id": 99,
            }
        )

    engine = create_engine(f"sqlite:///{tmp_path / 'classification-corrections.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="correction-owner@example.test", display_name="Owner")
        other = User(email="correction-other@example.test", display_name="Other")
        db.add_all([owner, other])
        db.flush()
        workspace = Workspace(name="Owner workspace", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other workspace", created_by_user_id=other.id)
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
                    user_id=other.id,
                    role="owner",
                    is_default=True,
                ),
            ]
        )
        other_plaid = PlaidItem(
            workspace_id=other_workspace.id,
            item_id="other-classification-plaid",
            owner_user_id=other.id,
        )
        db.add(other_plaid)
        db.flush()
        other_transaction = ExpenseTransaction(
            workspace_id=other_workspace.id,
            plaid_transaction_id="other-classification-transaction",
            plaid_item_id=other_plaid.id,
            merchant_name="Other merchant",
            name="Other merchant",
            amount_cents=1_000,
            iso_currency_code="USD",
            date=date(2026, 8, 17),
        )
        other_receipt = PurchaseReceipt(
            workspace_id=other_workspace.id,
            source="web",
            source_external_id="other-classification-receipt",
            parse_status="needs_review",
        )
        db.add_all([other_transaction, other_receipt])
        db.flush()
        other_line = PurchaseReceiptItem(
            receipt_id=other_receipt.id,
            raw_name="OTHER ITEM",
            normalized_name="other item",
        )
        db.add(other_line)
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))
        payload = ClassificationCorrection(
            spending_parent_category="food_dining",
            item_activity_type="grocery",
            replenishment_eligibility="replenishable",
            subcategory_name="Groceries",
            canonical_concept="Milk",
        )

        with pytest.raises(HTTPException) as receipt_error:
            correct_receipt_line_classification(
                other_line.id,
                payload,
                db,
                owner,
                workspace,
            )
        assert receipt_error.value.status_code == 404
        with pytest.raises(HTTPException) as transaction_error:
            correct_transaction_classification(
                other_transaction.id,
                payload,
                db,
                owner,
                workspace,
            )
        assert transaction_error.value.status_code == 404
    engine.dispose()


def test_member_can_correct_owned_receipt_and_transaction_and_clear_optional_taxonomy(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'classification-correction-success.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="classification-member@example.test", display_name="Member")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Correction workspace", created_by_user_id=user.id)
        db.add(workspace)
        db.flush()
        db.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role="member",
                is_default=True,
            )
        )
        plaid = PlaidItem(
            workspace_id=workspace.id,
            item_id="classification-correction-plaid",
            owner_user_id=user.id,
        )
        receipt = PurchaseReceipt(
            workspace_id=workspace.id,
            owner_user_id=user.id,
            source="web",
            source_external_id="classification-correction-receipt",
            merchant_raw="Example Cafe",
            parse_status="needs_review",
        )
        db.add_all([plaid, receipt])
        db.flush()
        transaction = ExpenseTransaction(
            workspace_id=workspace.id,
            plaid_transaction_id="classification-correction-transaction",
            plaid_item_id=plaid.id,
            merchant_name="Example Cafe",
            name="Example Cafe",
            amount_cents=1_000,
            iso_currency_code="USD",
            date=date(2026, 8, 17),
        )
        line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="CAFE ITEM",
            normalized_name="cafe item",
        )
        db.add_all([transaction, line])
        db.commit()
        set_session_tenant(db, TenantContext(user.id, workspace.id))

        payload = ClassificationCorrection(
            spending_parent_category="other_uncertain",
            item_activity_type="uncertain",
            replenishment_eligibility="uncertain",
            subcategory_name=None,
            canonical_concept=None,
        )
        receipt_result = correct_receipt_line_classification(
            line.id,
            payload,
            db,
            user,
            workspace,
        )
        transaction_result = correct_transaction_classification(
            transaction.id,
            payload,
            db,
            user,
            workspace,
        )

        assert receipt_result.applied is True
        assert transaction_result.applied is True
        db.refresh(line)
        db.refresh(transaction)
        assert line.spending_parent_category == "other_uncertain"
        assert line.classification_subcategory_name is None
        assert line.classification_concept_name is None
        assert line.classification_authority == "user_correction"
        assert transaction.spending_parent_category == "other_uncertain"
        assert transaction.classification_subcategory_name is None
        assert transaction.classification_concept_name is None
        assert transaction.classification_authority == "user_correction"
    engine.dispose()


def test_owner_can_list_rename_and_merge_compatible_concepts_with_strict_contracts(
    tmp_path,
) -> None:
    with pytest.raises(ValidationError):
        ClassificationConceptRename.model_validate({"name": "Coffee", "workspace_id": 9})
    with pytest.raises(ValidationError):
        ClassificationConceptMerge.model_validate(
            {"target_concept_id": 2, "merge_household_history": True}
        )
    with pytest.raises(ValidationError):
        ClassificationConceptMerge.model_validate({"target_concept_id": "2"})

    engine = create_engine(f"sqlite:///{tmp_path / 'classification-concept-routes.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="concept-owner@example.test", display_name="Concept Owner")
        db.add(owner)
        db.flush()
        workspace = Workspace(name="Concept workspace", created_by_user_id=owner.id)
        db.add(workspace)
        db.flush()
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=owner.id,
            role="owner",
            is_default=True,
        )
        source = ClassificationConcept(
            workspace_id=workspace.id,
            parent_category="food_dining",
            name="Cafe beverage",
            normalized_name="cafe beverage",
            item_activity_type="coffee_beverage",
            replenishment_eligibility="not_replenishable",
            source="user_correction",
            confidence=1.0,
        )
        target = ClassificationConcept(
            workspace_id=workspace.id,
            parent_category="food_dining",
            name="Coffee beverage",
            normalized_name="coffee beverage",
            item_activity_type="coffee_beverage",
            replenishment_eligibility="not_replenishable",
            source="user_correction",
            confidence=1.0,
        )
        db.add_all([membership, source, target])
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))

        listed = list_classification_concepts(db, membership, limit=100)
        assert [concept.name for concept in listed.concepts] == [
            "Cafe beverage",
            "Coffee beverage",
        ]
        assert all(concept.can_merge_as_source for concept in listed.concepts)

        renamed = rename_classification_concept(
            source.id,
            ClassificationConceptRename(name="Cafe drink"),
            db,
            membership,
        )
        assert renamed.applied is True
        assert renamed.household_items_merged is False

        merged = merge_classification_concepts(
            source.id,
            ClassificationConceptMerge(target_concept_id=target.id),
            db,
            membership,
        )
        assert merged.applied is True
        assert merged.source_concept_id == source.id
        assert merged.target_concept_id == target.id
        assert merged.target_name == "Coffee beverage"
        assert merged.household_items_merged is False

        with pytest.raises(HTTPException) as conflict:
            merge_classification_concepts(
                target.id,
                ClassificationConceptMerge(target_concept_id=target.id),
                db,
                membership,
            )
        assert conflict.value.status_code == 409
    engine.dispose()
