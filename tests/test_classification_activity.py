from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import get_args, get_type_hints

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.replenishment_routes import classification_activity
from app.classification_activity_schemas import ClassificationActivityOut
from app.db import Base
from app.models import (
    ClassificationConcept,
    ClassificationConceptAlias,
    ClassificationDecisionRecord,
    ExpenseTransaction,
    HouseholdItem,
    PlaidItem,
    PurchaseReceipt,
    PurchaseReceiptItem,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.classification_activity_service import ClassificationActivityService
from app.tenancy import TenantContext, set_session_tenant


def test_daily_classification_activity_is_bounded_scoped_and_truthfully_grouped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'classification-activity.db'}")
    Base.metadata.create_all(engine)
    target_date = date(2026, 8, 17)
    observed_at = datetime(2026, 8, 17, 12, tzinfo=UTC)
    with Session(engine) as db:
        owner = User(email="activity-owner@example.test", display_name="Activity owner")
        outsider = User(email="activity-outsider@example.test", display_name="Outsider")
        db.add_all([owner, outsider])
        db.flush()
        workspace = Workspace(name="Activity household", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other household", created_by_user_id=outsider.id)
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
                    user_id=outsider.id,
                    role="owner",
                    is_default=True,
                ),
            ]
        )
        plaid_item = PlaidItem(
            workspace_id=workspace.id,
            item_id="activity-bank",
            owner_user_id=owner.id,
        )
        other_plaid_item = PlaidItem(
            workspace_id=other_workspace.id,
            item_id="other-activity-bank",
            owner_user_id=outsider.id,
        )
        db.add_all([plaid_item, other_plaid_item])
        db.flush()
        transaction = ExpenseTransaction(
            workspace_id=workspace.id,
            plaid_transaction_id="activity-transaction",
            plaid_item_id=plaid_item.id,
            merchant_name="Synthetic Cafe",
            name="Synthetic Cafe",
            amount_cents=1_200,
            iso_currency_code="USD",
            date=target_date,
        )
        uncertain_transaction = ExpenseTransaction(
            workspace_id=workspace.id,
            plaid_transaction_id="uncertain-transaction",
            plaid_item_id=plaid_item.id,
            merchant_name="Unknown seller",
            name="Unknown seller",
            amount_cents=2_000,
            iso_currency_code="USD",
            date=target_date,
        )
        other_transaction = ExpenseTransaction(
            workspace_id=other_workspace.id,
            plaid_transaction_id="private-other-transaction",
            plaid_item_id=other_plaid_item.id,
            merchant_name="Private other merchant",
            name="Private other merchant",
            amount_cents=99_999,
            iso_currency_code="USD",
            date=target_date,
        )
        db.add_all([transaction, uncertain_transaction, other_transaction])
        db.flush()
        staple = HouseholdItem(
            workspace_id=workspace.id,
            name="Paper towels",
            cadence_days=None,
            cadence_source="learning",
            canonical_key="paper towels",
            spending_parent_category="household_home",
            replenishment_eligibility="replenishable",
            classification_confidence=0.98,
            cadence_confidence=0.0,
            created_at=observed_at,
        )
        cadence_item = HouseholdItem(
            workspace_id=workspace.id,
            name="Laundry detergent",
            cadence_days=30,
            cadence_source="observed",
            cadence_min_days=27,
            cadence_max_days=33,
            cadence_confidence=0.82,
            canonical_key="laundry detergent",
            spending_parent_category="household_home",
            replenishment_eligibility="replenishable",
            classification_confidence=0.98,
            cadence_estimated_at=observed_at + timedelta(minutes=5),
            created_at=observed_at - timedelta(days=20),
        )
        db.add_all([staple, cadence_item])
        db.flush()
        matched_receipt = PurchaseReceipt(
            workspace_id=workspace.id,
            source="web",
            source_external_id="daily-matched-receipt",
            merchant_raw="Household Store",
            total_cents=3_500,
            currency="USD",
            parse_status="confirmed",
            transaction_id=transaction.id,
            transaction_match_status="auto_matched",
            transaction_match_confidence=0.97,
            transaction_match_evidence_json={"secret_candidate_account_id": "do-not-project"},
            transaction_match_attempted_at=observed_at + timedelta(minutes=2),
            transaction_matched_at=observed_at + timedelta(minutes=2),
        )
        ambiguous_receipt = PurchaseReceipt(
            workspace_id=workspace.id,
            source="web",
            source_external_id="daily-ambiguous-receipt",
            merchant_raw="Possible Store",
            total_cents=2_000,
            currency="USD",
            parse_status="confirmed",
            transaction_match_status="ambiguous",
            transaction_match_confidence=0.76,
            transaction_match_evidence_json={"candidate_ids": [999_999]},
            transaction_match_attempted_at=observed_at + timedelta(minutes=3),
        )
        db.add_all([matched_receipt, ambiguous_receipt])
        db.flush()
        receipt_line = PurchaseReceiptItem(
            receipt_id=matched_receipt.id,
            raw_name="PAPER TOWELS",
            normalized_name="paper towels",
            household_item_id=staple.id,
        )
        db.add(receipt_line)
        db.flush()
        records = [
            _decision(
                workspace.id,
                "transaction",
                transaction.id,
                observed_at,
                parent="food_dining",
                activity="coffee_beverage",
                replenishment="not_replenishable",
                confidence=0.98,
                band="high",
                provenance=["deterministic_taxonomy_rule"],
            ),
            _decision(
                workspace.id,
                "transaction",
                uncertain_transaction.id,
                observed_at + timedelta(minutes=1),
                parent="other_uncertain",
                activity="uncertain",
                replenishment="uncertain",
                confidence=0.0,
                band="low",
                authority="fallback",
                provenance=["no_supported_rule"],
            ),
            _decision(
                workspace.id,
                "receipt_line",
                receipt_line.id,
                observed_at + timedelta(minutes=2),
                parent="household_home",
                activity="household_consumable",
                replenishment="replenishable",
                confidence=0.98,
                band="high",
                household_item_id=staple.id,
                created_household_item=True,
                created_subcategory=True,
                provenance=["deterministic_taxonomy_rule"],
            ),
            _decision(
                other_workspace.id,
                "transaction",
                other_transaction.id,
                observed_at,
                parent="travel",
                activity="travel",
                replenishment="not_replenishable",
                confidence=0.99,
                band="high",
                provenance=["private_other_workspace"],
            ),
            _decision(
                workspace.id,
                "transaction",
                transaction.id,
                datetime(2026, 8, 18, 0, tzinfo=UTC),
                version=2,
                parent="food_dining",
                activity="coffee_beverage",
                replenishment="not_replenishable",
                confidence=0.98,
                band="high",
                provenance=["next_day_boundary"],
            ),
        ]
        db.add_all(records)
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))

        result = ClassificationActivityService(db).read(
            activity_date=target_date,
            view="summary",
            limit=10,
        )

        assert result.counts.model_dump() == {
            "transactions": 1,
            "receipt_items": 1,
            "categories": 2,
            "new_categories": 1,
            "receipt_matches": 2,
            "new_household_items": 1,
            "cadence_updates": 1,
            "uncertain": 2,
        }
        assert [row.merchant for row in result.transactions] == ["Unknown seller"]
        assert result.receipt_items[0].name == "PAPER TOWELS"
        assert result.receipt_items[0].household_item_name == "Paper towels"
        assert result.new_categories[0].subcategory == "Synthetic category"
        assert result.new_household_items[0].name == "Paper towels"
        assert result.cadence_updates[0].cadence_min_days == 27
        assert {row.status for row in result.receipt_matches} == {"auto_matched", "ambiguous"}
        assert {row.kind for row in result.uncertain} == {"transaction", "receipt_match"}
        assert result.truncated_sections == []
        serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        assert "Private other merchant" not in serialized
        assert "private_other_workspace" not in serialized
        assert "do-not-project" not in serialized
        assert "999999" not in serialized
        assert "next_day_boundary" not in serialized

        categories = classification_activity(
            db,
            activity_date=target_date,
            view="categories",
            limit=10,
        )
        assert categories.categories
        assert categories.transactions == []
        assert categories.receipt_items == []
        assert categories.counts.transactions == 1

        created_categories = classification_activity(
            db,
            activity_date=target_date,
            view="new_categories",
            limit=10,
        )
        assert [row.subcategory for row in created_categories.new_categories] == [
            "Synthetic category"
        ]
        assert created_categories.categories == []
        assert created_categories.counts.new_categories == 1

        bounded = ClassificationActivityService(db).read(
            activity_date=target_date,
            view="summary",
            limit=1,
        )
        assert "transactions" not in bounded.truncated_sections
        assert "receipt_matches" in bounded.truncated_sections
        assert "uncertain" in bounded.truncated_sections

        matched_receipt.parse_status = "ignored"
        db.commit()
        ignored_activity = ClassificationActivityService(db).read(
            activity_date=target_date,
            view="summary",
            limit=10,
        )
        assert ignored_activity.receipt_items[0].source_available is False

        matched_receipt.parse_status = "failed"
        db.commit()
        failed_activity = ClassificationActivityService(db).read(
            activity_date=target_date,
            view="summary",
            limit=10,
        )
        assert failed_activity.receipt_items[0].source_available is False
    engine.dispose()


def test_range_activity_uses_local_boundaries_and_projects_candidates_and_aliases(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'classification-range.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="range-owner@example.test", display_name="Range owner")
        outsider = User(email="range-outsider@example.test", display_name="Range outsider")
        db.add_all([owner, outsider])
        db.flush()
        workspace = Workspace(name="Range household", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other range household", created_by_user_id=outsider.id)
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
                    user_id=outsider.id,
                    role="owner",
                    is_default=True,
                ),
            ]
        )
        learning_item = HouseholdItem(
            workspace_id=workspace.id,
            name="Paper towels",
            cadence_days=None,
            cadence_source="learning",
            canonical_key="paper towels",
            spending_parent_category="household_home",
            replenishment_eligibility="replenishable",
            classification_confidence=0.98,
            cadence_confidence=0.0,
            created_at=datetime(2026, 8, 18, 6, 30, tzinfo=UTC),
        )
        db.add(learning_item)
        db.flush()
        receipt = PurchaseReceipt(
            workspace_id=workspace.id,
            source="web",
            source_external_id="range-receipt",
            merchant_raw="Household Store",
            total_cents=6_000,
            currency="USD",
            parse_status="confirmed",
        )
        db.add(receipt)
        db.flush()
        learning_line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="PAPER TOWELS",
            normalized_name="paper towels",
            household_item_id=learning_item.id,
        )
        candidate_line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="AIR FILTER 20X20",
            normalized_name="air filter 20x20",
        )
        outside_line = PurchaseReceiptItem(
            receipt_id=receipt.id,
            raw_name="OUTSIDE LOCAL DAY",
            normalized_name="outside local day",
        )
        db.add_all([learning_line, candidate_line, outside_line])
        db.flush()

        concept = ClassificationConcept(
            workspace_id=workspace.id,
            parent_category="household_home",
            name="Paper towels",
            normalized_name="paper towels",
            item_activity_type="household_consumable",
            replenishment_eligibility="replenishable",
            source="deterministic_exact",
            confidence=0.98,
            created_at=datetime(2026, 8, 18, 6, 20, tzinfo=UTC),
        )
        private_concept = ClassificationConcept(
            workspace_id=other_workspace.id,
            parent_category="household_home",
            name="Private product",
            normalized_name="private product",
            item_activity_type="household_consumable",
            replenishment_eligibility="replenishable",
            source="deterministic_exact",
            confidence=0.99,
            created_at=datetime(2026, 8, 18, 6, 20, tzinfo=UTC),
        )
        db.add_all([concept, private_concept])
        db.flush()
        db.add_all(
            [
                ClassificationConceptAlias(
                    workspace_id=workspace.id,
                    concept_id=concept.id,
                    merchant_normalized="household store",
                    raw_pattern="PAPER TOWELS 6=12 ROLLS",
                    normalized_alias="paper towels 6 12 rolls",
                    confidence=0.97,
                    source="confirmed_alias",
                    created_at=datetime(2026, 8, 18, 6, 45, tzinfo=UTC),
                    voided_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
                ),
                ClassificationConceptAlias(
                    workspace_id=other_workspace.id,
                    concept_id=private_concept.id,
                    merchant_normalized="private store",
                    raw_pattern="PRIVATE PRODUCT",
                    normalized_alias="private product",
                    confidence=0.99,
                    source="confirmed_alias",
                    created_at=datetime(2026, 8, 18, 6, 46, tzinfo=UTC),
                ),
            ]
        )
        db.add_all(
            [
                _decision(
                    workspace.id,
                    "receipt_line",
                    learning_line.id,
                    datetime(2026, 8, 18, 6, 30, tzinfo=UTC),
                    parent="household_home",
                    activity="household_consumable",
                    replenishment="replenishable",
                    confidence=0.98,
                    band="high",
                    provenance=["deterministic_taxonomy_rule"],
                    household_item_id=learning_item.id,
                    created_household_item=True,
                ),
                _decision(
                    workspace.id,
                    "receipt_line",
                    candidate_line.id,
                    datetime(2026, 8, 18, 6, 31, tzinfo=UTC),
                    parent="household_home",
                    activity="household_consumable",
                    replenishment="potentially_replenishable",
                    confidence=0.72,
                    band="medium",
                    provenance=["model_evidence"],
                ),
                _decision(
                    workspace.id,
                    "receipt_line",
                    outside_line.id,
                    datetime(2026, 8, 18, 7, 0, tzinfo=UTC),
                    parent="household_home",
                    activity="household_consumable",
                    replenishment="replenishable",
                    confidence=0.95,
                    band="high",
                    provenance=["boundary_fixture"],
                ),
            ]
        )
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))

        result = ClassificationActivityService(db).read_range(
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 17),
            timezone="America/Phoenix",
            view="summary",
            limit=10,
        )

        assert result.schema_version == "1.1"
        assert result.timezone == "America/Phoenix"
        assert result.counts.staple_candidates == 2
        assert result.counts.aliases == 1
        assert [row.name for row in result.staple_candidates] == [
            "AIR FILTER 20X20",
            "PAPER TOWELS",
        ]
        assert [row.learning_state for row in result.staple_candidates] == [
            "candidate",
            "learning",
        ]
        assert result.staple_candidates[1].created_household_item is True
        assert result.staple_candidates[1].household_item_name == "Paper towels"
        assert len(result.aliases) == 1
        assert result.aliases[0].raw_pattern == "PAPER TOWELS 6=12 ROLLS"
        assert result.aliases[0].active is False
        serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        assert "PRIVATE PRODUCT" not in serialized
        assert "OUTSIDE LOCAL DAY" not in serialized

        candidate_view = ClassificationActivityService(db).read_range(
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 17),
            timezone="America/Phoenix",
            view="staple_candidates",
            limit=1,
        )
        assert len(candidate_view.staple_candidates) == 1
        assert candidate_view.aliases == []
        assert candidate_view.truncated_sections == ["staple_candidates"]

        alias_view = ClassificationActivityService(db).read_range(
            start_date=date(2026, 8, 17),
            end_date=date(2026, 8, 17),
            timezone="America/Phoenix",
            view="aliases",
            limit=10,
        )
        assert len(alias_view.aliases) == 1
        assert alias_view.staple_candidates == []

        legacy = ClassificationActivityService(db).read(
            activity_date=date(2026, 8, 17),
            view="summary",
            limit=10,
        )
        assert legacy.schema_version == "1.0"
        assert "staple_candidates" not in legacy.model_dump(mode="json")
        assert "aliases" not in legacy.model_dump(mode="json")
    engine.dispose()


def test_classification_activity_contract_rejects_unreconciled_category_counts() -> None:
    payload = {
        "view": "categories",
        "activity_date": "2026-08-17",
        "as_of": "2026-08-17T12:00:00Z",
        "counts": {
            "transactions": 1,
            "receipt_items": 0,
            "categories": 1,
            "new_categories": 0,
            "receipt_matches": 0,
            "new_household_items": 0,
            "cadence_updates": 0,
            "uncertain": 0,
        },
        "categories": [
            {
                "parent_category": "food_dining",
                "transaction_count": 1,
                "receipt_item_count": 0,
                "total_count": 2,
            }
        ],
    }
    try:
        ClassificationActivityOut.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("unreconciled category totals must fail closed")


def test_classification_activity_route_exposes_new_categories_view() -> None:
    view_annotation = get_type_hints(classification_activity)["view"]
    assert "new_categories" in get_args(view_annotation)


def _decision(
    workspace_id: int,
    source_type: str,
    source_entity_id: int,
    created_at: datetime,
    *,
    version: int = 1,
    parent: str,
    activity: str,
    replenishment: str,
    confidence: float,
    band: str,
    provenance: list[str],
    authority: str = "deterministic_exact",
    household_item_id: int | None = None,
    created_household_item: bool = False,
    created_subcategory: bool = False,
) -> ClassificationDecisionRecord:
    return ClassificationDecisionRecord(
        workspace_id=workspace_id,
        source_type=source_type,
        source_entity_id=source_entity_id,
        version=version,
        spending_parent_category=parent,
        subcategory_name="Synthetic category",
        concept_name="Synthetic concept",
        item_activity_type=activity,
        replenishment_eligibility=replenishment,
        confidence=confidence,
        confidence_band=band,
        authority=authority,
        provenance_json=provenance,
        decision_state="final",
        finalized_at=created_at,
        household_item_id=household_item_id,
        created_subcategory=created_subcategory,
        created_concept=False,
        created_household_item=created_household_item,
        created_at=created_at,
    )
