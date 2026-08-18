from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.models import (
    ClassificationActivityType,
    ClassificationAuthority,
    ClassificationDecisionRecord,
    ClassificationSettings,
    ExpenseTransaction,
    PlaidItem,
    ReplenishmentEligibility,
    SpendingParentCategory,
    User,
    Workspace,
    WorkspaceMembership,
    utc_now,
)
from app.services.classification_finalizer_service import ClassificationFinalizerService
from app.services.classification_taxonomy_service import (
    ClassificationSourceType,
    ClassificationTaxonomyService,
    build_classification_decision,
)
from app.tenancy import TenantContext, set_session_tenant


def test_repeated_finalizer_delivery_is_idempotent(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'day16-finalizer-retry.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="finalizer-retry@example.test", display_name="Finalizer Retry")
        db.add(user)
        db.flush()
        workspace = Workspace(name="Finalizer Retry", created_by_user_id=user.id)
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
            item_id="day16-finalizer-retry",
            owner_user_id=user.id,
        )
        db.add(plaid)
        db.add(
            ClassificationSettings(
                workspace_id=workspace.id,
                autonomous_enabled=True,
            )
        )
        db.flush()
        set_session_tenant(db, TenantContext(user.id, workspace.id))
        transaction = ExpenseTransaction(
            workspace_id=workspace.id,
            plaid_item_id=plaid.id,
            plaid_transaction_id="day16-finalizer-retry-transaction",
            merchant_name="Independent Service",
            name="Independent Service",
            amount_cents=1_000,
            iso_currency_code="USD",
            date=date(2026, 8, 17),
        )
        db.add(transaction)
        db.flush()
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
        db.commit()
        settings = Settings(
            _env_file=None,
            autonomous_classification_enabled=True,
            classification_finalizer_batch_size=10,
        )

        first = ClassificationFinalizerService(db, settings).run(use_model=False)
        count_after_first = db.scalar(select(func.count(ClassificationDecisionRecord.id)))
        second = ClassificationFinalizerService(db, settings).run(use_model=False)
        count_after_second = db.scalar(select(func.count(ClassificationDecisionRecord.id)))

        db.refresh(transaction)
        assert first.transactions_finalized == 1
        assert second.due == 0
        assert second.transactions_finalized == 0
        assert count_after_first == 2
        assert count_after_second == count_after_first
        assert transaction.classification_decision_state == "final"
        assert transaction.classification_auto_finalize_at is None
    engine.dispose()
