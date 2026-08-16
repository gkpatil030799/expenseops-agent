from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.agent.integration_read_tool import register_integration_read_tool
from app.agent.tooling import (
    AgentToolContext,
    AgentToolRegistry,
    ToolDisposition,
    ToolEffect,
    UnsafeToolArgumentsError,
)
from app.config import Settings
from app.db import Base
from app.models import (
    DataConsent,
    GmailAccount,
    GmailSyncCheckpoint,
    PlaidItem,
    SplitwiseIntegration,
    TelegramIdentity,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.tenancy import TenantContext, set_session_tenant


@pytest.fixture
def integration_tool_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-integration-status.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        owner = User(email="status-owner@example.test", display_name="Status owner")
        member = User(email="status-member@example.test", display_name="Status member")
        outsider = User(email="status-outsider@example.test", display_name="Status outsider")
        db.add_all([owner, member, outsider])
        db.flush()
        workspace = Workspace(name="Status workspace", created_by_user_id=owner.id)
        other_workspace = Workspace(name="Other status workspace", created_by_user_id=outsider.id)
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
                    workspace_id=workspace.id,
                    user_id=member.id,
                    role="member",
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
        db.commit()
        contexts = {
            "owner": TenantContext(owner.id, workspace.id),
            "member": TenantContext(member.id, workspace.id),
            "outsider": TenantContext(outsider.id, other_workspace.id),
        }

    try:
        yield factory, engine, contexts
    finally:
        engine.dispose()


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "agent_enabled": True,
        "agent_read_tools_enabled": True,
        "agent_write_actions_enabled": False,
        "agent_proactive_enabled": False,
        "agent_purchasing_enabled": False,
        "plaid_client_id": "configured-plaid-client",
        "plaid_secret": "configured-plaid-secret",
        "gmail_client_id": "configured-gmail-client",
        "gmail_client_secret": "configured-gmail-secret",
        "splitwise_consumer_key": "configured-splitwise-client",
        "splitwise_consumer_secret": "configured-splitwise-secret",
        "telegram_bot_token": "configured-telegram-token",
        "household_routing_provider": "google_maps",
        "google_maps_api_key": "configured-maps-key",
        "openai_api_key": "configured-openai-key",
    }
    values.update(overrides)
    return Settings(**values)


def _registry(settings: Settings) -> AgentToolRegistry:
    registry = AgentToolRegistry(settings)
    register_integration_read_tool(registry, settings)
    return registry


def _scoped(factory: sessionmaker, context: TenantContext) -> Session:
    db = factory()
    set_session_tenant(db, context)
    return db


def _execute(registry: AgentToolRegistry, db: Session, arguments: dict) -> dict:
    context = AgentToolContext.from_session(db, request_id="integration-status-test")
    prepared = registry.prepare("get_integration_status", arguments, context=context)
    assert prepared.disposition is ToolDisposition.READY
    executed = registry.execute_read(prepared, context=context)
    assert executed.disposition is ToolDisposition.EXECUTED
    assert executed.output is not None
    return executed.output


def _seed_connected_integrations(db: Session, context: TenantContext) -> datetime:
    synced_at = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
    suffix = f"{context.workspace_id}-{context.user_id}"
    db.add_all(
        [
            PlaidItem(
                workspace_id=context.workspace_id,
                item_id=f"provider-item-secret-{suffix}",
                owner_user_id=context.user_id,
                ownership_verified_at=synced_at,
                access_token_encrypted="encrypted-plaid-credential",
                institution_name="Private bank name",
            ),
            GmailAccount(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                google_user_id=f"private-gmail-{suffix}@example.test",
                refresh_token_encrypted="encrypted-gmail-credential",
            ),
            GmailSyncCheckpoint(
                workspace_id=context.workspace_id,
                account_key=f"private-gmail-{suffix}@example.test:receipts",
                history_id="private-history-id",
                updated_at=synced_at,
            ),
            SplitwiseIntegration(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                credentials_encrypted="encrypted-splitwise-credential",
                splitwise_user_id="private-splitwise-id",
                display_name="Private Splitwise name",
                email="private-splitwise@example.test",
                verified_at=synced_at,
            ),
            TelegramIdentity(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                telegram_user_id=f"private-telegram-id-{suffix}",
                chat_id=f"private-chat-id-{suffix}",
            ),
        ]
    )
    db.commit()
    return synced_at


def test_tool_metadata_and_exact_safe_output_shape(
    integration_tool_database,
    monkeypatch,
):
    factory, engine, contexts = integration_tool_database
    settings = _settings()
    registry = _registry(settings)
    metadata = registry.metadata()
    assert len(metadata) == 1
    assert metadata[0].name == "get_integration_status"
    assert metadata[0].effect is ToolEffect.READ
    assert metadata[0].confirmation_required is False

    def forbid_provider_call(*_args, **_kwargs):
        raise AssertionError("integration status must not contact a provider")

    monkeypatch.setattr("httpx.Client.request", forbid_provider_call)
    monkeypatch.setattr("requests.sessions.Session.request", forbid_provider_call)

    with _scoped(factory, contexts["owner"]) as db:
        synced_at = _seed_connected_integrations(db, contexts["owner"])
        statements: list[str] = []

        def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            output = _execute(registry, db, {})
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)

        assert [item["provider"] for item in output["integrations"]] == [
            "plaid",
            "gmail",
            "splitwise",
            "telegram",
            "google_maps",
            "openai",
        ]
        assert [item["scope"] for item in output["integrations"]] == [
            "workspace",
            "workspace",
            "personal",
            "personal",
            "application",
            "application",
        ]
        assert [item["status"] for item in output["integrations"]] == [
            "connected",
            "connected",
            "connected",
            "connected",
            "ready",
            "ready",
        ]
        gmail = output["integrations"][1]
        assert gmail["last_successful_sync_at"] == synced_at.isoformat().replace("+00:00", "Z")
        assert set(gmail) == {
            "provider",
            "label",
            "scope",
            "status",
            "message",
            "last_successful_sync_at",
        }
        assert statements
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert not db.new
        assert not db.dirty
        assert not db.deleted

    serialized = json.dumps(output, sort_keys=True)
    for private_value in (
        "provider-item-secret-1-1",
        "encrypted-plaid-credential",
        "Private bank name",
        "private-gmail-1-1@example.test",
        "encrypted-gmail-credential",
        "private-history-id",
        "encrypted-splitwise-credential",
        "private-splitwise-id",
        "Private Splitwise name",
        "private-splitwise@example.test",
        "private-telegram-id-1-1",
        "private-chat-id-1-1",
        settings.plaid_secret,
        settings.gmail_client_secret,
        settings.splitwise_consumer_secret,
        settings.telegram_bot_token,
        settings.google_maps_api_key,
        settings.openai_api_key,
    ):
        assert private_value not in serialized


def test_provider_filter_is_closed_bounded_unique_and_has_no_tenant_arguments(
    integration_tool_database,
):
    factory, _engine, contexts = integration_tool_database
    settings = _settings()
    registry = _registry(settings)
    with _scoped(factory, contexts["owner"]) as db:
        output = _execute(registry, db, {"providers": ["gmail", "google_maps"]})
        assert [item["provider"] for item in output["integrations"]] == [
            "gmail",
            "google_maps",
        ]

        context = AgentToolContext.from_session(db)
        for providers in ([], ["gmail", "gmail"], ["not_a_provider"]):
            with pytest.raises(ValidationError):
                registry.prepare(
                    "get_integration_status",
                    {"providers": providers},
                    context=context,
                )
        with pytest.raises(ValidationError):
            registry.prepare(
                "get_integration_status",
                {"providers": ["gmail"] * 7},
                context=context,
            )
        with pytest.raises(UnsafeToolArgumentsError):
            registry.prepare(
                "get_integration_status",
                {"workspace_id": contexts["outsider"].workspace_id},
                context=context,
            )


def test_workspace_status_is_shared_but_personal_status_does_not_leak_between_members(
    integration_tool_database,
):
    factory, _engine, contexts = integration_tool_database
    settings = _settings()
    registry = _registry(settings)
    with _scoped(factory, contexts["owner"]) as db:
        _seed_connected_integrations(db, contexts["owner"])

    with _scoped(factory, contexts["outsider"]) as db:
        _seed_connected_integrations(db, contexts["outsider"])

    with _scoped(factory, contexts["member"]) as db:
        output = _execute(
            registry,
            db,
            {"providers": ["plaid", "gmail", "splitwise", "telegram"]},
        )

    statuses = {item["provider"]: item["status"] for item in output["integrations"]}
    assert statuses == {
        "plaid": "connected",
        "gmail": "connected",
        "splitwise": "disconnected",
        "telegram": "disconnected",
    }
    serialized = json.dumps(output, sort_keys=True)
    assert "status-owner@example.test" not in serialized
    assert "status-outsider@example.test" not in serialized
    assert "private" not in serialized.casefold()


def test_empty_and_misconfigured_environment_uses_truthful_non_connected_states(
    integration_tool_database,
):
    factory, _engine, contexts = integration_tool_database
    settings = _settings(
        plaid_client_id="",
        plaid_secret="",
        gmail_client_id="",
        gmail_client_secret="",
        splitwise_consumer_key="",
        splitwise_consumer_secret="",
        telegram_bot_token="",
        household_routing_provider="fallback",
        household_place_search_provider="fallback",
        google_maps_api_key="",
        openai_api_key="",
    )
    with _scoped(factory, contexts["owner"]) as db:
        output = _execute(_registry(settings), db, {})

    assert {item["provider"]: item["status"] for item in output["integrations"]} == {
        "plaid": "unavailable",
        "gmail": "unavailable",
        "splitwise": "unavailable",
        "telegram": "unavailable",
        "google_maps": "disabled",
        "openai": "attention_required",
    }


def test_disabled_and_unverified_records_are_not_reported_as_connected(
    integration_tool_database,
):
    factory, _engine, contexts = integration_tool_database
    context = contexts["owner"]
    with _scoped(factory, context) as db:
        db.add_all(
            [
                PlaidItem(
                    workspace_id=context.workspace_id,
                    item_id="disabled-item",
                    owner_user_id=context.user_id,
                    ownership_verified_at=datetime.now(UTC),
                    enabled=False,
                ),
                GmailAccount(
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                    google_user_id="disabled@example.test",
                    refresh_token_encrypted="encrypted",
                    enabled=False,
                ),
                SplitwiseIntegration(
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                    credentials_encrypted="encrypted",
                    enabled=True,
                    verified_at=None,
                ),
                TelegramIdentity(
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                    telegram_user_id="disabled-telegram",
                    chat_id="disabled-chat",
                    enabled=False,
                ),
            ]
        )
        db.commit()
        output = _execute(_registry(_settings()), db, {})

    assert {item["provider"]: item["status"] for item in output["integrations"]} == {
        "plaid": "disabled",
        "gmail": "disabled",
        "splitwise": "attention_required",
        "telegram": "disabled",
        "google_maps": "ready",
        "openai": "ready",
    }


def test_gmail_reports_required_consent_attention_and_last_successful_sync(
    integration_tool_database,
):
    factory, _engine, contexts = integration_tool_database
    context = contexts["owner"]
    settings = _settings(
        gmail_receipt_sync_enabled=True,
        promotions_enabled=True,
        receipt_parser_provider="openai",
        promotions_llm_fallback_enabled=True,
    )
    registry = _registry(settings)
    with _scoped(factory, context) as db:
        synced_at = _seed_connected_integrations(db, context)
        before = _execute(registry, db, {"providers": ["gmail"]})["integrations"][0]
        assert before["status"] == "attention_required"
        assert before["last_successful_sync_at"] == synced_at.isoformat().replace("+00:00", "Z")

        now = datetime.now(UTC)
        for purpose in (
            "gmail_receipts",
            "gmail_promotions",
            "model_receipt_processing",
        ):
            db.add(
                DataConsent(
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                    purpose=purpose,
                    granted=True,
                    granted_at=now,
                )
            )
        db.commit()
        after = _execute(registry, db, {"providers": ["gmail"]})["integrations"][0]

    assert after["status"] == "connected"
    assert after["message"] == "The workspace Gmail connection is connected."
    assert after["last_successful_sync_at"] == before["last_successful_sync_at"]
