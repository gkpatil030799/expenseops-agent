from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.config import Settings
from app.services.integration_status_service import (
    INTEGRATION_PROVIDERS,
    MAX_INTEGRATION_STATUSES,
    IntegrationProvider,
    IntegrationScope,
    IntegrationState,
    IntegrationStatusService,
)


class IntegrationStatusToolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class IntegrationStatusToolInput(IntegrationStatusToolModel):
    providers: list[IntegrationProvider] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_INTEGRATION_STATUSES,
    )

    @field_validator("providers")
    @classmethod
    def require_unique_providers(
        cls,
        value: list[IntegrationProvider] | None,
    ) -> list[IntegrationProvider] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("providers must be unique")
        return value


class IntegrationStatusToolItem(IntegrationStatusToolModel):
    provider: IntegrationProvider
    label: str = Field(min_length=1, max_length=64)
    scope: IntegrationScope
    status: IntegrationState
    message: str = Field(min_length=1, max_length=500)
    last_successful_sync_at: datetime | None = None


class IntegrationStatusToolOutput(IntegrationStatusToolModel):
    integrations: list[IntegrationStatusToolItem] = Field(
        min_length=1,
        max_length=MAX_INTEGRATION_STATUSES,
    )


def build_integration_status_tool(settings: Settings) -> AgentTool:
    def handle(
        context: AgentToolContext,
        values: IntegrationStatusToolInput,
    ) -> dict:
        statuses = IntegrationStatusService(context.db, settings).get_statuses(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            providers=values.providers,
        )
        return {
            "integrations": [
                {
                    "provider": status.provider,
                    "label": status.label,
                    "scope": status.scope,
                    "status": status.status,
                    "message": status.message,
                    "last_successful_sync_at": status.last_successful_sync_at,
                }
                for status in statuses
            ]
        }

    return AgentTool(
        name="get_integration_status",
        description=(
            "Return safe canonical connection and readiness states for the authenticated "
            "ExpenseOps user's personal, workspace, and application integrations. Use for "
            "attention questions only when connection health is directly relevant."
        ),
        effect=ToolEffect.READ,
        input_model=IntegrationStatusToolInput,
        output_model=IntegrationStatusToolOutput,
        handler=handle,
    )


def register_integration_read_tool(
    registry: AgentToolRegistry,
    settings: Settings,
) -> None:
    registry.register(build_integration_status_tool(settings))


__all__ = [
    "INTEGRATION_PROVIDERS",
    "IntegrationStatusToolInput",
    "IntegrationStatusToolItem",
    "IntegrationStatusToolOutput",
    "build_integration_status_tool",
    "register_integration_read_tool",
]
