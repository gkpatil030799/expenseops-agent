from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agent.contracts import AgentAttentionSummaryBlock
from app.agent.read_tools import build_read_tool_registry
from app.agent.tooling import AgentToolContext
from app.attention_schemas import AttentionPreferencePatch
from app.config import Settings
from app.db import Base
from app.models import (
    AgentActionProposal,
    ExpenseTransaction,
    FinancialOperation,
    OutboxEvent,
    PlaidItem,
    ProactiveAttentionDelivery,
    ProactiveAttentionPreference,
    TelegramIdentity,
    TransactionStatus,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.services.proactive_attention_service import (
    ProactiveAttentionDisabledError,
    ProactiveAttentionService,
)


@pytest.fixture
def attention_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'attention.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine)
    owner = User(email="attention@example.test", display_name="Attention owner")
    other = User(email="other-attention@example.test", display_name="Other owner")
    db.add_all([owner, other])
    db.flush()
    workspace = Workspace(name="Attention", created_by_user_id=owner.id)
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
    db.commit()
    db.info.update(workspace_id=workspace.id, user_id=owner.id)
    try:
        yield db, owner, other, workspace, other_workspace
    finally:
        db.close()
        engine.dispose()


def _settings(*, proactive: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=True,
        agent_proactive_enabled=proactive,
        agent_purchasing_enabled=False,
    )


class _FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.variant = 0

    def prepare(self, tool_name: str, arguments: dict, *, context: AgentToolContext):
        assert context.workspace_id > 0
        assert context.user_id > 0
        return SimpleNamespace(tool_name=tool_name, arguments=dict(arguments))

    def execute_read(self, prepared, *, context: AgentToolContext):
        self.calls.append((prepared.tool_name, prepared.arguments))
        output = _outputs(self.variant)[prepared.tool_name]
        return SimpleNamespace(
            tool_version="1.0",
            normalized_arguments=prepared.arguments,
            output=output,
        )


class _Telegram:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None]] = []

    def send_message(self, message: str, *, chat_id: str | None = None) -> bool:
        self.messages.append((message, chat_id))
        return True


def _outputs(variant: int) -> dict[str, dict]:
    return {
        "search_transactions": {
            "transactions": [],
            "total_count": 2 + variant,
            "result_limit": 25,
            "truncated": False,
        },
        "get_receipts": {
            "view": "needs_review",
            "receipts": [],
            "receipt": None,
            "total_count": 1,
            "truncated": False,
        },
        "get_integration_status": {
            "integrations": [
                {
                    "provider": "gmail",
                    "status": "attention_required",
                    "scope": "workspace",
                }
            ]
        },
        "get_household_replenishment": {
            "items": [{"name": "Detergent", "due_state": "likely_due"}],
            "item": None,
            "truncated": False,
        },
        "get_relevant_deals": {
            "deals": [
                {
                    "merchant": "<script>Target</script>",
                    "relevant_to_need": True,
                    "expires_on": "2026-08-20",
                }
            ],
            "truncated": False,
        },
        "get_errands_and_plan": {
            "errands": [
                {
                    "title": "Pick up detergent",
                    "status": "open",
                    "priority": "high",
                    "due_date": "2026-08-17",
                }
            ],
            "plan": None,
            "truncated": False,
        },
    }


def _patch_registry(monkeypatch, registry: _FakeRegistry) -> None:
    monkeypatch.setattr(
        "app.services.proactive_attention_service.build_read_tool_registry",
        lambda _settings: registry,
    )


def test_flag_false_prevents_evaluation_and_delivery(attention_db, monkeypatch) -> None:
    db, *_ = attention_db
    registry = _FakeRegistry()
    telegram = _Telegram()
    _patch_registry(monkeypatch, registry)
    service = ProactiveAttentionService(db, _settings(proactive=False), telegram)

    with pytest.raises(ProactiveAttentionDisabledError):
        service.build_center()
    with pytest.raises(ProactiveAttentionDisabledError):
        service.deliver_telegram_digest()

    assert registry.calls == []
    assert telegram.messages == []
    assert db.scalar(select(func.count(ProactiveAttentionPreference.id))) == 0


def test_center_composes_all_selected_canonical_domains_without_provider_or_writes(
    attention_db,
    monkeypatch,
) -> None:
    db, *_ = attention_db
    registry = _FakeRegistry()
    _patch_registry(monkeypatch, registry)
    service = ProactiveAttentionService(db, _settings())

    response, preferences = service.build_center(now=datetime(2026, 8, 17, 12, tzinfo=UTC))

    assert response is not None
    block = next(
        value for value in response.blocks if isinstance(value, AgentAttentionSummaryBlock)
    )
    assert block.status == "complete"
    assert block.checked_domains == [
        "transactions",
        "replenishment",
        "receipts",
        "deals",
        "errands",
        "integrations",
    ]
    assert {item.priority for item in block.items} == {
        "action_required",
        "time_sensitive",
        "useful_to_know",
    }
    assert len(registry.calls) == len(preferences.categories_json) == 6
    assert all(call[0] != "get_spending_insights" for call in registry.calls)
    assert db.scalar(select(func.count(AgentActionProposal.id))) == 0
    assert db.scalar(select(func.count(FinancialOperation.id))) == 0
    assert db.scalar(select(func.count(OutboxEvent.id))) == 0


def test_preferences_are_user_scoped_and_corrupt_categories_fail_closed(
    attention_db,
    monkeypatch,
) -> None:
    db, owner, other, workspace, _other_workspace = attention_db
    registry = _FakeRegistry()
    _patch_registry(monkeypatch, registry)
    service = ProactiveAttentionService(db, _settings())
    first = service.update_preferences(
        AttentionPreferencePatch(categories=["receipts", "replenishment"])
    )

    db.info.update(workspace_id=workspace.id, user_id=other.id)
    second = service.preferences()
    assert second.user_id == other.id
    assert second.categories_json != first.categories_json

    second.categories_json = ["receipts", "unknown"]
    db.commit()
    with pytest.raises(ValueError, match="Stored attention categories"):
        service.build_center()
    assert registry.calls == []

    rows = list(
        db.scalars(
            select(ProactiveAttentionPreference).order_by(ProactiveAttentionPreference.user_id)
        )
    )
    assert {row.user_id for row in rows} == {owner.id, other.id}


def test_digest_honors_mode_quiet_hours_dedupe_cooldown_and_daily_cap(
    attention_db,
    monkeypatch,
) -> None:
    db, owner, *_ = attention_db
    registry = _FakeRegistry()
    telegram = _Telegram()
    _patch_registry(monkeypatch, registry)
    service = ProactiveAttentionService(db, _settings(), telegram)
    db.add(
        TelegramIdentity(
            workspace_id=db.info["workspace_id"],
            user_id=owner.id,
            telegram_user_id="attention-telegram-user",
            chat_id="attention-private-chat",
        )
    )
    db.commit()
    service.update_preferences(
        AttentionPreferencePatch(
            telegram_enabled=True,
            delivery_mode="immediate",
            timezone="UTC",
            quiet_start_hour=22,
            quiet_end_hour=7,
            cooldown_minutes=60,
            max_alerts_per_day=2,
        )
    )
    midday = datetime(2026, 8, 17, 12, tzinfo=UTC)

    assert service.deliver_telegram_digest(now=midday)["status"] == "skipped_delivery_mode"
    service.update_preferences(AttentionPreferencePatch(delivery_mode="digest"))
    assert (
        service.deliver_telegram_digest(now=datetime(2026, 8, 17, 23, tzinfo=UTC))["status"]
        == "skipped_quiet_hours"
    )
    assert service.deliver_telegram_digest(now=midday)["status"] == "sent"
    assert service.deliver_telegram_digest(now=midday)["status"] == "skipped_duplicate"

    registry.variant = 1
    assert (
        service.deliver_telegram_digest(now=midday + timedelta(minutes=30))["status"]
        == "skipped_cooldown"
    )
    assert service.deliver_telegram_digest(now=midday + timedelta(minutes=61))["status"] == "sent"
    registry.variant = 2
    assert (
        service.deliver_telegram_digest(now=midday + timedelta(minutes=122))["status"]
        == "skipped_daily_cap"
    )

    assert len(telegram.messages) == 2
    assert "<script>" not in telegram.messages[0][0]
    assert "&lt;script&gt;Target&lt;/script&gt;" in telegram.messages[0][0]
    assert {chat_id for _message, chat_id in telegram.messages} == {"attention-private-chat"}
    assert db.scalar(select(func.count(ProactiveAttentionDelivery.id))) == 2
    assert set(db.scalars(select(ProactiveAttentionDelivery.status))) == {"sent"}


def test_digest_never_falls_back_to_an_unowned_or_process_level_chat(
    attention_db,
    monkeypatch,
) -> None:
    db, _owner, other, workspace, _other_workspace = attention_db
    registry = _FakeRegistry()
    telegram = _Telegram()
    _patch_registry(monkeypatch, registry)
    service = ProactiveAttentionService(db, _settings(), telegram)
    service.update_preferences(AttentionPreferencePatch(telegram_enabled=True))

    assert service.deliver_telegram_digest()["status"] == "skipped_channel_unavailable"
    db.add(
        TelegramIdentity(
            workspace_id=workspace.id,
            user_id=other.id,
            telegram_user_id="other-user-telegram",
            chat_id="other-user-private-chat",
        )
    )
    db.commit()
    assert service.deliver_telegram_digest()["status"] == "skipped_channel_unavailable"

    assert telegram.messages == []
    assert registry.calls == []


def test_ambiguous_telegram_outcome_is_claimed_once_and_never_blindly_retried(
    attention_db,
    monkeypatch,
) -> None:
    db, owner, *_ = attention_db
    registry = _FakeRegistry()

    class AmbiguousTelegram(_Telegram):
        def send_message(self, message: str, *, chat_id: str | None = None) -> bool:
            self.messages.append((message, chat_id))
            raise RuntimeError("provider timeout after possible send")

    telegram = AmbiguousTelegram()
    _patch_registry(monkeypatch, registry)
    db.add(
        TelegramIdentity(
            workspace_id=db.info["workspace_id"],
            user_id=owner.id,
            telegram_user_id="ambiguous-telegram-user",
            chat_id="ambiguous-private-chat",
        )
    )
    db.commit()
    service = ProactiveAttentionService(db, _settings(), telegram)
    service.update_preferences(AttentionPreferencePatch(telegram_enabled=True))
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="provider timeout"):
        service.deliver_telegram_digest(now=now)
    assert service.deliver_telegram_digest(now=now)["status"] == "skipped_duplicate"

    delivery = db.scalar(select(ProactiveAttentionDelivery))
    assert delivery is not None
    assert delivery.status == "ambiguous"
    assert delivery.delivered_at is None
    assert len(telegram.messages) == 1


def test_transaction_attention_scope_includes_recovery_states_and_excludes_other_tenants(
    attention_db,
) -> None:
    db, _owner, _other, workspace, other_workspace = attention_db
    item = PlaidItem(
        workspace_id=workspace.id,
        item_id="attention-plaid",
        institution_name="Bank",
    )
    db.add(item)
    db.flush()
    statuses = [
        TransactionStatus.ASK_USER.value,
        TransactionStatus.POST_AMBIGUOUS.value,
        TransactionStatus.UNDO_AMBIGUOUS.value,
        TransactionStatus.RECONCILIATION_REQUIRED.value,
        TransactionStatus.ERROR.value,
        TransactionStatus.PERSONAL.value,
    ]
    for index, status in enumerate(statuses):
        db.add(
            ExpenseTransaction(
                workspace_id=workspace.id,
                plaid_transaction_id=f"attention-{index}",
                plaid_item_id=item.id,
                name=f"Transaction {index}",
                merchant_name=f"Merchant {index}",
                amount_cents=100 + index,
                date=date(2026, 8, 17),
                pending=False,
                status=status,
            )
        )
    db.commit()
    db.info.update(workspace_id=other_workspace.id)
    other_item = PlaidItem(
        workspace_id=other_workspace.id,
        item_id="other-attention-plaid",
        institution_name="Other bank",
    )
    db.add(other_item)
    db.flush()
    db.add(
        ExpenseTransaction(
            workspace_id=other_workspace.id,
            plaid_transaction_id="cross-tenant-attention",
            plaid_item_id=other_item.id,
            name="Private outsider transaction",
            amount_cents=999_999,
            date=date(2026, 8, 17),
            pending=False,
            status=TransactionStatus.ERROR.value,
        )
    )
    db.commit()
    db.info.update(workspace_id=workspace.id)

    registry = build_read_tool_registry(_settings())
    context = AgentToolContext.from_session(db, request_id="attention-scope")
    prepared = registry.prepare(
        "search_transactions",
        {"review_type": "attention", "include_pending": False, "limit": 25},
        context=context,
    )
    result = registry.execute_read(prepared, context=context)

    assert registry.get("search_transactions").version == "1.1"
    assert result.output is not None
    assert result.output["total_count"] == 5
    assert {row["status"] for row in result.output["transactions"]} == set(statuses[:-1])
    assert "Private outsider transaction" not in str(result.output)
