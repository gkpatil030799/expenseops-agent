from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, create_model
from sqlalchemy.orm import Session

from app.agent.contracts import (
    AGENT_CONTRACT_VERSION,
    AgentActionConfirmationBlock,
    AgentActionPreview,
    AgentCapabilities,
    AgentConversationCreate,
    AgentEmptyStateBlock,
    AgentMessageCreate,
    AgentMessageOut,
    AgentPageContext,
    AgentStructuredResponse,
    AgentSurface,
)
from app.agent.tooling import (
    AgentTool,
    AgentToolContext,
    AgentToolDispatchResult,
    AgentToolPolicyError,
    AgentToolRegistry,
    DuplicateAgentToolError,
    ToolCapability,
    ToolDisposition,
    ToolEffect,
    UnknownAgentToolError,
    UnsafeToolArgumentsError,
    UnsafeToolOutputError,
)
from app.config import Settings


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class EchoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    echoed: str


class PayloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


def _tool(
    *,
    name: str = "echo",
    effect: ToolEffect = ToolEffect.READ,
    handler=None,
    confirmation_required: bool | None = None,
    input_model: type[BaseModel] = EchoInput,
    preview_builder=None,
) -> AgentTool:
    if confirmation_required is None:
        confirmation_required = effect is not ToolEffect.READ
    if preview_builder is None and effect is not ToolEffect.READ:

        def default_preview_builder(_context, values):
            return AgentActionPreview(
                title="Review test action",
                summary=f"Apply {values.value}.",
            )

        preview_builder = default_preview_builder
    return AgentTool(
        name=name,
        description="Echo a harmless test value.",
        effect=effect,
        input_model=input_model,
        output_model=EchoOutput,
        handler=handler or (lambda _context, values: {"echoed": values.value}),
        confirmation_required=confirmation_required,
        preview_builder=preview_builder,
    )


def _context() -> AgentToolContext:
    db = Session()
    db.info.update({"workspace_id": 7, "user_id": 11})
    return AgentToolContext.from_session(db, request_id="req-1")


def _registry(
    *,
    enabled: bool = True,
    reads: bool = True,
    writes: bool = False,
    purchasing: bool = False,
) -> AgentToolRegistry:
    return AgentToolRegistry(
        Settings(
            _env_file=None,
            agent_enabled=enabled,
            agent_read_tools_enabled=reads,
            agent_write_actions_enabled=writes,
            agent_purchasing_enabled=purchasing,
        )
    )


def test_page_context_is_strict_and_cannot_select_tenant_identity():
    context = AgentPageContext.model_validate(
        {
            "schema_version": "1.0",
            "surface": "expense_insights",
            "filters": {
                "start_date": "2026-08-01",
                "end_date": "2026-08-14",
                "category": "Food & Dining",
            },
            "entity": {"kind": "transaction", "public_id": "tx_public_1"},
        }
    )

    assert context.surface is AgentSurface.EXPENSE_INSIGHTS
    assert context.filters.start_date == date(2026, 8, 1)
    assert context.model_dump(mode="json")["schema_version"] == AGENT_CONTRACT_VERSION

    with pytest.raises(ValidationError):
        AgentPageContext.model_validate(
            {
                "surface": "expense_review",
                "workspace_id": 999,
            }
        )
    with pytest.raises(ValidationError):
        AgentPageContext.model_validate(
            {
                "surface": "expense_review",
                "filters": {"user_id": 999},
            }
        )


def test_page_context_rejects_unknown_surfaces_and_reversed_dates():
    with pytest.raises(ValidationError):
        AgentPageContext.model_validate({"surface": "arbitrary_dom_panel"})
    with pytest.raises(ValidationError, match="start_date"):
        AgentPageContext.model_validate(
            {
                "surface": "expense_insights",
                "filters": {"start_date": "2026-08-14", "end_date": "2026-08-01"},
            }
        )


def test_structured_response_accepts_every_versioned_platform_neutral_block():
    now = datetime.now(UTC)
    response = AgentStructuredResponse.model_validate(
        {
            "schema_version": "1.0",
            "blocks": [
                {"type": "text", "text": "Here is what needs attention."},
                {
                    "type": "transaction_list",
                    "title": "Transactions",
                    "transactions": [
                        {
                            "public_id": "tx-1",
                            "merchant": "Coffee Shop",
                            "amount_cents": 525,
                            "currency_code": "USD",
                            "occurred_on": "2026-08-14",
                            "status": "ask_user",
                        }
                    ],
                    "total_count": 1,
                },
                {
                    "type": "spending_summary",
                    "title": "Dining",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-14",
                    "currency_code": "USD",
                    "total_cents": 12_500,
                    "previous_total_cents": 10_000,
                    "change_percent": 25,
                    "highlights": ["Dining increased."],
                },
                {
                    "type": "replenishment_summary",
                    "title": "Likely due",
                    "items": [
                        {
                            "public_id": "item-1",
                            "name": "Paper towels",
                            "confidence": 0.8,
                        }
                    ],
                },
                {
                    "type": "deal_list",
                    "title": "Relevant deals",
                    "deals": [
                        {
                            "public_id": "deal-1",
                            "merchant": "Aldi",
                            "headline": "Save 10%",
                            "score": 82,
                        }
                    ],
                    "total_count": 1,
                },
                {
                    "type": "receipt_summary",
                    "public_id": "receipt-1",
                    "merchant": "Aldi",
                    "total_cents": 4_200,
                    "currency_code": "USD",
                    "status": "confirmed",
                    "items": [{"name": "Milk", "quantity": 1, "line_total_cents": 399}],
                },
                {
                    "type": "errand_summary",
                    "title": "Errands",
                    "errands": [{"public_id": "errand-1", "title": "Aldi", "status": "open"}],
                },
                {
                    "type": "integration_status",
                    "title": "Connections",
                    "integrations": [
                        {
                            "provider": "plaid",
                            "status": "connected",
                        }
                    ],
                },
                {
                    "type": "navigation",
                    "label": "Open Insights",
                    "target_surface": "expense_insights",
                },
                {
                    "type": "action_confirmation",
                    "proposal_id": "proposal-1",
                    "proposal_version": 1,
                    "status": "awaiting_confirmation",
                    "title": "Mark as personal",
                    "summary": "Mark Coffee Shop as a personal transaction.",
                    "details": [{"label": "Amount", "value": "$5.25"}],
                    "expires_at": (now + timedelta(minutes=15)).isoformat(),
                },
                {
                    "type": "error",
                    "code": "integration_unavailable",
                    "title": "Connection unavailable",
                    "message": "Try again later.",
                    "retryable": True,
                },
                {
                    "type": "empty",
                    "title": "Nothing due",
                    "message": "No household items are predicted for this week.",
                },
            ],
        }
    )

    dumped = response.model_dump(mode="json")
    assert dumped["schema_version"] == "1.0"
    assert [block["type"] for block in dumped["blocks"]] == [
        "text",
        "transaction_list",
        "spending_summary",
        "replenishment_summary",
        "deal_list",
        "receipt_summary",
        "errand_summary",
        "integration_status",
        "navigation",
        "action_confirmation",
        "error",
        "empty",
    ]
    assert "html" not in str(dumped).casefold()


def test_v1_structured_response_still_hydrates_original_domain_bounds_and_fields():
    response = AgentStructuredResponse.model_validate(
        {
            "schema_version": "1.0",
            "blocks": [
                {
                    "type": "replenishment_summary",
                    "title": "Legacy household items",
                    "items": [
                        {"public_id": f"item-{index}", "name": f"Item {index}"}
                        for index in range(21)
                    ],
                },
                {
                    "type": "receipt_summary",
                    "public_id": "receipt-legacy",
                    "status": "confirmed",
                    "items": [{"name": f"Line {index}"} for index in range(26)],
                },
                {
                    "type": "errand_summary",
                    "title": "Legacy errands",
                    "errands": [
                        {
                            "public_id": f"errand-{index}",
                            "title": f"Errand {index}",
                            "status": "open",
                        }
                        for index in range(26)
                    ],
                },
                {
                    "type": "integration_status",
                    "title": "Legacy integration",
                    "integrations": [{"provider": "legacy_provider", "status": "connected"}],
                },
            ],
        }
    )

    dumped = response.model_dump(mode="json")
    assert dumped["blocks"][0]["total_count"] == 21
    assert dumped["blocks"][1]["total_line_count"] == 26
    assert dumped["blocks"][2]["total_count"] == 26
    assert dumped["blocks"][3]["integrations"][0]["scope"] is None


def test_structured_response_rejects_unknown_blocks_and_extra_rendering_fields():
    with pytest.raises(ValidationError):
        AgentStructuredResponse.model_validate(
            {"blocks": [{"type": "custom_html", "html": "<script>alert(1)</script>"}]}
        )
    with pytest.raises(ValidationError):
        AgentStructuredResponse.model_validate(
            {
                "blocks": [
                    {
                        "type": "text",
                        "text": "Safe content",
                        "css_selector": "#dashboard",
                    }
                ]
            }
        )


def test_action_preview_is_strict_before_a_proposal_id_exists():
    preview = AgentActionPreview(
        title="Prepare split",
        summary="Split the transaction equally.",
        details=[{"label": "Participants", "value": "Gunjan and Janhavi"}],
    )
    confirmation = AgentActionConfirmationBlock(
        **preview.model_dump(),
        proposal_id="proposal-1",
        proposal_version=2,
        status="awaiting_confirmation",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    assert confirmation.title == preview.title
    assert confirmation.proposal_version == 2
    with pytest.raises(ValidationError):
        AgentActionPreview.model_validate(
            {"title": "Prepare split", "summary": "Review it.", "workspace_id": 5}
        )


def test_capabilities_and_message_contracts_use_canonical_version_and_bounds():
    capabilities = AgentCapabilities(enabled=True, read_tools_enabled=True)
    assert capabilities.model_dump() == {
        "schema_version": "1.0",
        "enabled": True,
        "read_tools_enabled": True,
        "write_actions_enabled": False,
        "proactive_enabled": False,
        "purchasing_enabled": False,
    }
    assert AgentConversationCreate(title="Today").title == "Today"
    assert AgentMessageCreate(text="What needs attention?", client_message_id="mobile:1")
    with pytest.raises(ValidationError):
        AgentMessageCreate(text="hello", client_message_id="x" * 65)

    structured = AgentStructuredResponse(
        blocks=[AgentEmptyStateBlock(title="Done", message="All clear")]
    )
    assistant = AgentMessageOut(
        public_id="message-1",
        conversation_public_id="conversation-1",
        role="assistant",
        structured_response=structured,
        created_at=datetime.now(UTC),
    )
    assert assistant.text is None
    with pytest.raises(ValidationError, match="text or a structured response"):
        AgentMessageOut(
            public_id="message-2",
            conversation_public_id="conversation-1",
            role="assistant",
            created_at=datetime.now(UTC),
        )


def test_registry_is_an_explicit_allowlist_and_rejects_duplicates():
    registry = _registry()
    registry.register(_tool())

    assert registry.get("echo").name == "echo"
    assert registry.metadata()[0].model_dump()["effect"] == ToolEffect.READ
    with pytest.raises(DuplicateAgentToolError):
        registry.register(_tool())
    with pytest.raises(UnknownAgentToolError):
        registry.get("arbitrary_sql")


@pytest.mark.parametrize("effect", [ToolEffect.WRITE, ToolEffect.EXTERNAL_ACTION])
def test_consequential_tools_must_require_confirmation(effect: ToolEffect):
    with pytest.raises(ValueError, match="must require confirmation"):
        _tool(effect=effect, confirmation_required=False)


def test_registry_requires_extra_forbid_on_tool_schemas():
    class LooseInput(BaseModel):
        value: str

    registry = _registry()
    with pytest.raises(ValueError, match="extra='forbid'"):
        registry.register(_tool(input_model=LooseInput))


def test_registry_rejects_server_context_and_sensitive_schema_fields():
    class TenantInput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace_id: int

    class SecretInput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        api_key: str

    class SecretOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        access_token: str

    registry = _registry()
    with pytest.raises(ValueError, match="workspace_id"):
        registry.register(_tool(name="tenant_test", input_model=TenantInput))
    with pytest.raises(ValueError, match="api_key"):
        registry.register(_tool(name="secret_test", input_model=SecretInput))
    with pytest.raises(ValueError, match="access_token"):
        registry.register(
            AgentTool(
                name="secret_output_test",
                description="Invalid output schema.",
                effect=ToolEffect.READ,
                input_model=EchoInput,
                output_model=SecretOutput,
                handler=lambda _context, _values: {"access_token": "secret"},
            )
        )


@pytest.mark.parametrize(
    "field_name",
    ["apiKey", "clientSecret", "privateKey", "accessToken", "workspaceId"],
)
def test_registry_rejects_camel_case_secret_and_server_fields(field_name: str):
    unsafe_input = create_model(
        f"Unsafe{field_name}Input",
        __config__=ConfigDict(extra="forbid"),
        **{field_name: (str, ...)},
    )

    with pytest.raises(ValueError, match=field_name):
        _registry().register(_tool(name="unsafe_schema", input_model=unsafe_input))


def test_read_tools_execute_only_when_agent_and_read_flags_are_enabled():
    calls: list[tuple[int, int, str]] = []

    def handler(context: AgentToolContext, values: BaseModel):
        calls.append((context.workspace_id, context.user_id, values.value))  # type: ignore[attr-defined]
        return {"echoed": values.value}  # type: ignore[attr-defined]

    disabled_registry = _registry(enabled=False)
    disabled_registry.register(_tool(handler=handler))

    with pytest.raises(AgentToolPolicyError) as disabled:
        disabled_registry.prepare(
            "echo",
            {"value": "hello"},
            context=_context(),
        )
    assert disabled.value.code == "agent_disabled"
    reads_disabled_registry = _registry(reads=False)
    reads_disabled_registry.register(_tool(handler=handler))
    with pytest.raises(AgentToolPolicyError) as reads_disabled:
        reads_disabled_registry.prepare(
            "echo",
            {"value": "hello"},
            context=_context(),
        )
    assert reads_disabled.value.code == "read_tools_disabled"
    assert calls == []

    registry = _registry()
    registry.register(_tool(handler=handler))
    context = _context()
    prepared = registry.prepare(
        "echo",
        {"value": "hello"},
        context=context,
    )
    assert prepared.disposition is ToolDisposition.READY
    assert calls == []
    result = registry.execute_read(prepared, context=context)
    assert result.disposition is ToolDisposition.EXECUTED
    assert result.output == {"echoed": "hello"}
    assert calls == [(7, 11, "hello")]


def test_tool_context_must_still_match_the_authenticated_session():
    registry = _registry()
    registry.register(_tool())
    context = _context()
    context.db.info["user_id"] = 12

    with pytest.raises(AgentToolPolicyError) as mismatch:
        registry.prepare("echo", {"value": "hello"}, context=context)

    assert mismatch.value.code == "invalid_tool_context"


def test_read_execution_rechecks_flags_and_rejects_forged_or_tampered_dispatches():
    settings = Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
    )
    registry = AgentToolRegistry(settings)
    registry.register(_tool(input_model=PayloadInput))
    context = _context()
    prepared = registry.prepare(
        "echo",
        {"payload": {"safe": "value"}},
        context=context,
    )

    settings.agent_enabled = False
    with pytest.raises(AgentToolPolicyError) as disabled:
        registry.execute_read(prepared, context=context)
    assert disabled.value.code == "agent_disabled"

    forged = AgentToolDispatchResult(
        tool_name="echo",
        tool_version="1.0",
        effect=ToolEffect.READ,
        disposition=ToolDisposition.READY,
        normalized_arguments={"payload": {"clientSecret": "secret"}},
    )
    with pytest.raises(AgentToolPolicyError) as untrusted:
        registry.execute_read(forged, context=context)
    assert untrusted.value.code == "untrusted_tool_dispatch"

    settings.agent_enabled = True
    tampered = prepared.model_copy(
        update={"normalized_arguments": {"payload": {"workspaceId": 999}}}
    )
    with pytest.raises(AgentToolPolicyError) as altered:
        registry.execute_read(tampered, context=context)
    assert altered.value.code == "untrusted_tool_dispatch"

    context.db.info.update({"workspace_id": 8, "user_id": 12})
    other_tenant = AgentToolContext.from_session(context.db, request_id="req-2")
    with pytest.raises(AgentToolPolicyError) as rebound:
        registry.execute_read(prepared, context=other_tenant)
    assert rebound.value.code == "untrusted_tool_dispatch"


def test_purchasing_tools_have_an_independent_server_owned_flag():
    calls: list[str] = []
    tool = AgentTool(
        name="create_cart",
        description="Prepare a future cart action.",
        effect=ToolEffect.EXTERNAL_ACTION,
        capability=ToolCapability.PURCHASING,
        input_model=EchoInput,
        output_model=EchoOutput,
        handler=lambda _context, values: calls.append(values.value) or {"echoed": values.value},
        confirmation_required=True,
        preview_builder=lambda _context, values: {
            "title": "Review cart",
            "summary": f"Prepare {values.value} for purchase.",
        },
    )
    disabled = _registry(writes=True, purchasing=False)
    disabled.register(tool)
    with pytest.raises(AgentToolPolicyError) as blocked:
        disabled.prepare("create_cart", {"value": "milk"}, context=_context())
    assert blocked.value.code == "purchasing_disabled"
    assert calls == []

    enabled_settings = Settings(
        _env_file=None,
        agent_enabled=True,
        agent_read_tools_enabled=True,
        agent_write_actions_enabled=True,
        agent_purchasing_enabled=True,
    )
    enabled = AgentToolRegistry(enabled_settings)
    enabled.register(tool)
    context = _context()
    prepared = enabled.prepare("create_cart", {"value": "milk"}, context=context)
    assert prepared.disposition is ToolDisposition.PROPOSAL_REQUIRED
    assert prepared.preview.summary == "Prepare milk for purchase."
    assert calls == []

    enabled_settings.agent_purchasing_enabled = False
    with pytest.raises(AgentToolPolicyError) as disabled_after_prepare:
        enabled.validate_issued(prepared, context=context)
    assert disabled_after_prepare.value.code == "purchasing_disabled"


@pytest.mark.parametrize("effect", [ToolEffect.WRITE, ToolEffect.EXTERNAL_ACTION])
def test_consequential_dispatch_only_returns_proposal_material_and_never_calls_handler(
    effect: ToolEffect,
):
    calls: list[str] = []

    def forbidden_handler(_context: AgentToolContext, _values: BaseModel):
        calls.append("called")
        return {"echoed": "unsafe"}

    disabled_registry = _registry(writes=False)
    disabled_registry.register(
        _tool(name=f"test_{effect.value}", effect=effect, handler=forbidden_handler)
    )

    with pytest.raises(AgentToolPolicyError) as disabled:
        disabled_registry.prepare(
            f"test_{effect.value}",
            {"value": "normalized"},
            context=_context(),
        )
    assert disabled.value.code == "write_actions_disabled"

    registry = _registry(writes=True)
    registry.register(_tool(name=f"test_{effect.value}", effect=effect, handler=forbidden_handler))
    result = registry.prepare(
        f"test_{effect.value}",
        {"value": "normalized"},
        context=_context(),
    )
    assert result.disposition is ToolDisposition.PROPOSAL_REQUIRED
    assert result.normalized_arguments == {"value": "normalized"}
    assert result.output is None
    assert calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"payload": {"oauth_token": "secret"}},
        {"payload": [{"credentials": "secret"}]},
        {"payload": {"nested": {"workspace_id": 123}}},
        {"payload": {"authorization": "Bearer secret"}},
        {"payload": {"apiKey": "secret"}},
    ],
)
def test_tool_argument_sensitive_keys_are_rejected_recursively(arguments: dict[str, Any]):
    registry = _registry()
    registry.register(_tool(input_model=PayloadInput))

    with pytest.raises(UnsafeToolArgumentsError):
        registry.prepare(
            "echo",
            arguments,
            context=_context(),
        )


def test_tool_input_and_output_are_schema_validated():
    registry = _registry()
    registry.register(_tool(handler=lambda _context, _values: {"unexpected": "value"}))

    with pytest.raises(ValidationError):
        registry.prepare(
            "echo",
            {"value": 123},
            context=_context(),
        )
    context = _context()
    prepared = registry.prepare("echo", {"value": "valid"}, context=context)
    with pytest.raises(ValidationError):
        registry.execute_read(prepared, context=context)


def test_nested_sensitive_output_is_rejected_before_it_reaches_the_model():
    class NestedOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        payload: dict[str, Any]

    registry = _registry()
    registry.register(
        AgentTool(
            name="nested_output",
            description="Return a nested payload.",
            effect=ToolEffect.READ,
            input_model=EchoInput,
            output_model=NestedOutput,
            handler=lambda _context, _values: {"payload": {"api_key": "secret"}},
        )
    )

    context = _context()
    prepared = registry.prepare("nested_output", {"value": "valid"}, context=context)
    with pytest.raises(UnsafeToolOutputError):
        registry.execute_read(prepared, context=context)


def test_non_finite_tool_output_and_structured_response_are_rejected():
    class FloatOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: float

    registry = _registry()
    argument_registry = _registry()
    argument_registry.register(_tool(input_model=PayloadInput))
    with pytest.raises(UnsafeToolArgumentsError):
        argument_registry.prepare(
            "echo",
            {"payload": {"amount": float("inf")}},
            context=_context(),
        )

    registry.register(
        AgentTool(
            name="non_finite_output",
            description="Return an invalid non-finite number.",
            effect=ToolEffect.READ,
            input_model=EchoInput,
            output_model=FloatOutput,
            handler=lambda _context, _values: {"value": float("nan")},
        )
    )
    context = _context()
    prepared = registry.prepare(
        "non_finite_output",
        {"value": "valid"},
        context=context,
    )
    with pytest.raises(UnsafeToolOutputError):
        registry.execute_read(prepared, context=context)

    with pytest.raises(ValidationError):
        AgentStructuredResponse.model_validate(
            {
                "blocks": [
                    {
                        "type": "spending_summary",
                        "title": "Invalid",
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-14",
                        "currency_code": "USD",
                        "total_cents": 100,
                        "change_percent": float("nan"),
                    }
                ]
            }
        )


def test_agent_tool_context_is_server_only_and_not_part_of_model_metadata():
    registry = _registry()
    registry.register(_tool())

    context = _context()
    metadata = registry.metadata()[0].model_dump()
    assert not isinstance(context, BaseModel)
    assert "workspace_id" not in str(metadata)
    assert "user_id" not in str(metadata)
    assert "request_id" not in str(metadata)
