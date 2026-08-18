from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.classification_activity_schemas import (
    MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS,
    ClassificationActivityRangeOut,
    ClassificationActivityRangeView,
)
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

    activity_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    view: ClassificationActivityRangeView = "summary"
    limit: int = Field(default=10, ge=1, le=MAX_CLASSIFICATION_ACTIVITY_RESULTS)

    @model_validator(mode="after")
    def validate_range(self) -> ClassificationActivityInput:
        legacy = self.activity_date is not None
        bounded_range = self.start_date is not None and self.end_date is not None
        if legacy == bounded_range or (self.start_date is None) != (self.end_date is None):
            raise ValueError("provide activity_date or one complete start_date/end_date range")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if legacy:
            return self
        start = date.fromisoformat(self.start_date or "")
        end = date.fromisoformat(self.end_date or "")
        if start > end:
            raise ValueError("start_date must not be after end_date")
        if (end - start).days >= MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS:
            raise ValueError(
                f"classification activity range cannot exceed "
                f"{MAX_CLASSIFICATION_ACTIVITY_RANGE_DAYS} days"
            )
        return self


def register_classification_activity_tool(registry: AgentToolRegistry) -> None:
    registry.register(
        AgentTool(
            name="get_classification_activity",
            description=(
                "Use for a bounded local-date retrospective of what ExpenseOps categorized or "
                "learned: decisions, categories, aliases, receipt matches, newly tracked items, "
                "recent staple candidates, cadence, or uncertainty. Do not use for what is due "
                "now or replenishment history; use household replenishment for those questions. "
                "Use receipts for the latest receipt. Authenticated workspace data only."
            ),
            effect=ToolEffect.READ,
            input_model=ClassificationActivityInput,
            output_model=ClassificationActivityRangeOut,
            handler=_get_classification_activity,
            version="1.1",
        )
    )


def _get_classification_activity(
    context: AgentToolContext,
    values: ClassificationActivityInput,
) -> ClassificationActivityRangeOut:
    start_date = date.fromisoformat(values.start_date or values.activity_date or "")
    end_date = date.fromisoformat(values.end_date or values.activity_date or "")
    return ClassificationActivityService(context.db).read_range(
        start_date=start_date,
        end_date=end_date,
        timezone=values.timezone,
        view=values.view,
        limit=values.limit,
    )
