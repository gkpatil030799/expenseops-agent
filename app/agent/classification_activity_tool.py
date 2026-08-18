from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.classification_activity_schemas import ClassificationActivityOut
from app.services.classification_activity_service import (
    MAX_CLASSIFICATION_ACTIVITY_RESULTS,
    ClassificationActivityService,
)


class ClassificationActivityInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    activity_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    view: Literal[
        "summary",
        "categories",
        "new_categories",
        "matches",
        "staples",
        "cadence",
        "uncertain",
    ] = "summary"
    limit: int = Field(default=10, ge=1, le=MAX_CLASSIFICATION_ACTIVITY_RESULTS)


def register_classification_activity_tool(registry: AgentToolRegistry) -> None:
    registry.register(
        AgentTool(
            name="get_classification_activity",
            description=(
                "Read a bounded UTC-day retrospective of ExpenseOps classification decisions, "
                "category groups, newly created subcategories, receipt-to-transaction matches, "
                "newly tracked household "
                "items, cadence estimates, or uncertain outcomes. This is read-only and returns "
                "only authenticated workspace data. Use get_receipts for the latest receipt."
            ),
            effect=ToolEffect.READ,
            input_model=ClassificationActivityInput,
            output_model=ClassificationActivityOut,
            handler=_get_classification_activity,
            version="1.0",
        )
    )


def _get_classification_activity(
    context: AgentToolContext,
    values: ClassificationActivityInput,
) -> ClassificationActivityOut:
    return ClassificationActivityService(context.db).read(
        activity_date=date.fromisoformat(values.activity_date),
        view=values.view,
        limit=values.limit,
    )
