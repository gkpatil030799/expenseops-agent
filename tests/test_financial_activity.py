from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.insights_routes import financial_activity
from app.db import Base
from app.models import (
    AuditEvent,
    ExpenseTransaction,
    FinancialOperation,
    PlaidItem,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.tenancy import TenantContext, set_session_tenant


def test_financial_activity_is_immutable_scoped_and_paginated(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'financial-activity.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="owner@example.test", display_name="Owner")
        other = User(email="other@example.test", display_name="Other")
        db.add_all([owner, other])
        db.flush()
        workspace = Workspace(name="Household", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other", created_by_user_id=other.id)
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
        item = PlaidItem(
            workspace_id=workspace.id,
            item_id="item",
            owner_user_id=owner.id,
        )
        db.add(item)
        db.flush()
        transaction = ExpenseTransaction(
            workspace_id=workspace.id,
            plaid_transaction_id="transaction",
            plaid_item_id=item.id,
            merchant_name="Aldi",
            name="Aldi",
            amount_cents=4850,
            iso_currency_code="USD",
        )
        db.add(transaction)
        db.flush()
        operation = FinancialOperation(
            workspace_id=workspace.id,
            transaction_id=transaction.id,
            actor_user_id=owner.id,
            action="splitwise_create",
            idempotency_key="key",
            state="failed",
            attempt_count=2,
            provider_object_id="splitwise-42",
            correlation_id="operation-request",
        )
        db.add(operation)
        db.flush()
        db.add_all(
            [
                AuditEvent(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    event_type="financial_operation_failed",
                    resource_type="financial_operation",
                    resource_id=str(operation.id),
                    request_id="request-42",
                    metadata_json={
                        "transaction_id": transaction.id,
                        "action": "splitwise_create",
                        "outcome": "failed",
                        "channel": "telegram",
                        "attempt": 2,
                    },
                ),
                AuditEvent(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    event_type="transaction_marked_personal",
                    resource_type="expense_transaction",
                    resource_id=str(transaction.id),
                    metadata_json={
                        "transaction_id": transaction.id,
                        "action": "mark_personal",
                        "outcome": "succeeded",
                        "channel": "dashboard",
                    },
                ),
                AuditEvent(
                    workspace_id=other_workspace.id,
                    user_id=other.id,
                    event_type="transaction_marked_personal",
                    resource_type="expense_transaction",
                    resource_id="999",
                    metadata_json={"transaction_id": 999, "action": "mark_personal"},
                ),
            ]
        )
        db.commit()
        set_session_tenant(db, TenantContext(owner.id, workspace.id))

        first_page = financial_activity(db, limit=1, offset=0)
        second_page = financial_activity(db, limit=1, offset=1)

        assert first_page.total == 2
        assert first_page.has_more is True
        assert second_page.has_more is False
        events = [*first_page.events, *second_page.events]
        failed = next(event for event in events if event.outcome == "failed")
        assert failed.actor_display_name == "Owner"
        assert failed.channel == "telegram"
        assert failed.attempt == 2
        assert failed.transaction_id == transaction.id
        assert failed.merchant_name == "Aldi"
        assert failed.amount_cents == 4850
        assert failed.provider_object_id == "splitwise-42"
        assert failed.correlation_id == "request-42"
    engine.dispose()
