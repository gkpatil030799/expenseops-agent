from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.agent.tooling import AgentTool, AgentToolContext, AgentToolRegistry, ToolEffect
from app.models import (
    ExpenseTransaction,
    HouseholdItem,
    HouseholdItemAcquisition,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReceiptParseStatus,
    ReplenishmentPrediction,
    TransactionStatus,
    utc_now,
)
from app.services.replenishment_service import ReplenishmentService, due_score, due_state

MAX_HOUSEHOLD_RESULTS = 20
MAX_ACQUISITION_RESULTS = 20
MAX_RECEIPT_RESULTS = 20
MAX_RECEIPT_LINE_RESULTS = 25
MAX_RECEIPT_DATE_RANGE_DAYS = 730
MAX_DATABASE_IDENTIFIER = 2_147_483_647

_CONFIDENCE_LEVELS = frozenset({"insufficient", "low", "medium", "high"})
_IGNORED_LINE_STATUSES = frozenset({"irrelevant", "rejected"})
_NEEDS_REVIEW_RECEIPT_STATUSES = (
    ReceiptParseStatus.NEEDS_REVIEW.value,
    ReceiptParseStatus.FAILED.value,
)


class HouseholdReceiptToolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class HouseholdReplenishmentInput(HouseholdReceiptToolModel):
    view: Literal["due", "learning", "item_history"] = "due"
    household_item_id: int | None = Field(
        default=None,
        ge=1,
        le=MAX_DATABASE_IDENTIFIER,
    )
    query: str | None = Field(default=None, min_length=1, max_length=255)
    horizon_days: int = Field(default=7, ge=0, le=90)
    limit: int = Field(default=10, ge=1, le=MAX_HOUSEHOLD_RESULTS)

    @model_validator(mode="after")
    def validate_view(self) -> HouseholdReplenishmentInput:
        if self.view == "item_history":
            selectors = int(self.household_item_id is not None) + int(self.query is not None)
            if selectors != 1:
                raise ValueError("item_history requires exactly one household_item_id or query")
        elif self.household_item_id is not None:
            raise ValueError("household_item_id is only supported for item_history")
        return self


class HouseholdReplenishmentItem(HouseholdReceiptToolModel):
    public_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    quantity: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=64)
    due_state: Literal["likely_due", "probably_due", "not_due", "learning"]
    predicted_due_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    confidence_level: Literal["insufficient", "low", "medium", "high"]
    evidence_basis: Literal["configured_cadence", "purchase_pattern", "validated_model"]
    reason: str = Field(min_length=1, max_length=500)
    last_acquired_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    confirmed_acquisition_count: int = Field(ge=0)
    snoozed: bool


class HouseholdAcquisitionItem(HouseholdReceiptToolModel):
    acquired_at: date
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=64)
    merchant: str | None = Field(default=None, max_length=255)
    evidence_type: Literal["manual", "receipt", "transaction", "imported", "correction"]


class HouseholdLearningSummary(HouseholdReceiptToolModel):
    confirmed_acquisition_count: int = Field(ge=0)
    items_with_history: int = Field(ge=0)
    items_with_predictions: int = Field(ge=0)


class HouseholdReplenishmentOutput(HouseholdReceiptToolModel):
    view: Literal["due", "learning", "item_history"]
    as_of: datetime
    items: list[HouseholdReplenishmentItem] = Field(max_length=MAX_HOUSEHOLD_RESULTS)
    item: HouseholdReplenishmentItem | None = None
    acquisitions: list[HouseholdAcquisitionItem] = Field(max_length=MAX_ACQUISITION_RESULTS)
    learning: HouseholdLearningSummary | None = None
    total_count: int = Field(ge=0)
    result_limit: int = Field(ge=1, le=MAX_HOUSEHOLD_RESULTS)
    truncated: bool

    @model_validator(mode="after")
    def validate_view_payload(self) -> HouseholdReplenishmentOutput:
        if self.view == "item_history":
            if self.item is None or self.items or self.learning is not None:
                raise ValueError("item_history requires one item and acquisition rows only")
        elif self.item is not None or self.acquisitions:
            raise ValueError("due and learning views cannot contain item history fields")
        if self.view == "learning" and self.learning is None:
            raise ValueError("learning view requires a learning summary")
        if self.view == "due" and self.learning is not None:
            raise ValueError("due view cannot contain a learning summary")
        return self


class ReceiptsInput(HouseholdReceiptToolModel):
    view: Literal["recent", "needs_review", "detail"] = "recent"
    receipt_id: int | None = Field(default=None, ge=1, le=MAX_DATABASE_IDENTIFIER)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    ingested_start_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    ingested_end_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    limit: int = Field(default=10, ge=1, le=MAX_RECEIPT_RESULTS)
    line_limit: int = Field(default=25, ge=1, le=MAX_RECEIPT_LINE_RESULTS)

    @model_validator(mode="after")
    def validate_view(self) -> ReceiptsInput:
        start = date.fromisoformat(self.ingested_start_date) if self.ingested_start_date else None
        end = date.fromisoformat(self.ingested_end_date) if self.ingested_end_date else None
        if start and end:
            if start > end:
                raise ValueError("ingested_start_date must not be after ingested_end_date")
            if (end - start).days > MAX_RECEIPT_DATE_RANGE_DAYS:
                raise ValueError("receipt date range must be two years or less")
        if self.view == "detail":
            if self.receipt_id is None:
                raise ValueError("receipt_id is required for detail")
            if self.merchant or start or end:
                raise ValueError("detail cannot be combined with receipt search filters")
        elif self.receipt_id is not None:
            raise ValueError("receipt_id is only supported for detail")
        return self


class ReceiptSummaryItem(HouseholdReceiptToolModel):
    public_id: str = Field(min_length=1, max_length=128)
    merchant: str | None = Field(default=None, min_length=1, max_length=255)
    purchased_at: datetime | None = None
    ingested_at: datetime
    total_cents: int | None = None
    currency_code: str = Field(min_length=3, max_length=8, pattern=r"^[A-Z]{3,8}$")
    status: Literal["pending", "parsed", "needs_review", "confirmed", "ignored", "failed"]
    matched_line_count: int = Field(ge=0)
    ignored_line_count: int = Field(ge=0)
    unmatched_line_count: int = Field(ge=0)
    total_line_count: int = Field(ge=0)
    transaction_linked: bool
    confirmed_household_item_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_RECEIPT_LINE_RESULTS,
    )
    confirmed_household_item_ids_truncated: bool = False


class ReceiptLineItem(HouseholdReceiptToolModel):
    name: str = Field(min_length=1, max_length=500)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=64)
    line_total_cents: int | None = None
    match_status: Literal["matched", "possible", "unmatched", "ignored"]
    household_item_name: str | None = Field(default=None, min_length=1, max_length=255)
    household_item_public_id: str | None = Field(default=None, min_length=1, max_length=128)
    confirmed_acquisition: bool


class ReceiptDetailItem(ReceiptSummaryItem):
    lines: list[ReceiptLineItem] = Field(max_length=MAX_RECEIPT_LINE_RESULTS)


class ReceiptsOutput(HouseholdReceiptToolModel):
    view: Literal["recent", "needs_review", "detail"]
    receipts: list[ReceiptSummaryItem] = Field(max_length=MAX_RECEIPT_RESULTS)
    receipt: ReceiptDetailItem | None = None
    total_count: int = Field(ge=0)
    result_limit: int = Field(ge=1, le=MAX_RECEIPT_LINE_RESULTS)
    truncated: bool

    @model_validator(mode="after")
    def validate_view_payload(self) -> ReceiptsOutput:
        if self.view == "detail":
            if self.receipt is None or self.receipts:
                raise ValueError("detail view requires exactly one receipt detail")
        elif self.receipt is not None:
            raise ValueError("receipt lists cannot contain a receipt detail")
        return self


def register_household_receipt_tools(registry: AgentToolRegistry) -> None:
    registry.register(
        AgentTool(
            name="get_household_replenishment",
            description=(
                "Read bounded household due estimates, safe replenishment-learning summaries, "
                "or confirmed acquisition history from the authenticated ExpenseOps workspace. "
                "Pair with deals for a due-item offer question, errands for exact stored links, "
                "or receipts only when confirmed acquisition evidence is relevant."
            ),
            effect=ToolEffect.READ,
            input_model=HouseholdReplenishmentInput,
            output_model=HouseholdReplenishmentOutput,
            handler=_get_household_replenishment,
        )
    )
    registry.register(
        AgentTool(
            name="get_receipts",
            description=(
                "Read bounded recent receipts, receipts needing review, or one safe receipt "
                "detail from the authenticated ExpenseOps workspace. Recent and needs-review "
                "list rows include bounded tenant-verified confirmed household-item links. "
                "Use recent when one question combines review status with recent confirmed "
                "acquisitions; pair with replenishment only for exact ID-linked questions."
            ),
            effect=ToolEffect.READ,
            input_model=ReceiptsInput,
            output_model=ReceiptsOutput,
            handler=_get_receipts,
            version="1.1",
        )
    )


def _get_household_replenishment(
    context: AgentToolContext,
    values: HouseholdReplenishmentInput,
) -> dict:
    now = utc_now()
    if values.view == "item_history":
        return _household_item_history(context, values, now=now)

    criteria = [
        HouseholdItem.workspace_id == context.workspace_id,
        HouseholdItem.enabled.is_(True),
    ]
    if values.query:
        query = f"%{_escape_like(values.query.casefold())}%"
        criteria.append(func.lower(HouseholdItem.name).like(query, escape="\\"))
    candidates = list(
        context.db.scalars(
            select(HouseholdItem)
            .where(*criteria)
            .order_by(func.lower(HouseholdItem.name), HouseholdItem.id)
        )
    )
    predictions = _latest_predictions(
        context.db,
        workspace_id=context.workspace_id,
        item_ids=[item.id for item in candidates],
    )

    if values.view == "due":
        service = ReplenishmentService(context.db)
        due_items: list[tuple[HouseholdItem, ReplenishmentPrediction | None]] = []
        horizon = _aware(now) + timedelta(days=values.horizon_days)
        for item in candidates:
            if item.snoozed_until is not None and _aware(item.snoozed_until) > _aware(now):
                continue
            prediction = _current_prediction(predictions.get(item.id), item)
            if prediction is not None:
                if _aware(prediction.predicted_need_at) <= horizon:
                    due_items.append((item, prediction))
            elif service.should_surface(item, now=now):
                due_items.append((item, None))
        due_items.sort(
            key=lambda value: (
                _projected_due_at(value[0], value[1]),
                value[0].name.casefold(),
                value[0].id,
            )
        )
        total_count = len(due_items)
        selected = due_items[: values.limit]
        counts = _confirmed_acquisition_counts(
            context.db,
            workspace_id=context.workspace_id,
            item_ids=[item.id for item, _prediction in selected],
        )
        return {
            "view": values.view,
            "as_of": now,
            "items": [
                _household_item_dict(item, prediction, counts.get(item.id, 0), now=now)
                for item, prediction in selected
            ],
            "item": None,
            "acquisitions": [],
            "learning": None,
            "total_count": total_count,
            "result_limit": values.limit,
            "truncated": total_count > len(selected),
        }

    learning_items: list[tuple[HouseholdItem, ReplenishmentPrediction | None]] = []
    for item in candidates:
        prediction = _current_prediction(predictions.get(item.id), item)
        if prediction is None or _confidence_level(prediction) == "insufficient":
            learning_items.append((item, prediction))
    total_count = len(learning_items)
    selected_items = learning_items[: values.limit]
    counts = _confirmed_acquisition_counts(
        context.db,
        workspace_id=context.workspace_id,
        item_ids=[item.id for item, _prediction in selected_items],
    )
    return {
        "view": values.view,
        "as_of": now,
        "items": [
            _household_item_dict(
                item,
                prediction,
                counts.get(item.id, 0),
                now=now,
            )
            for item, prediction in selected_items
        ],
        "item": None,
        "acquisitions": [],
        "learning": _learning_summary(context),
        "total_count": total_count,
        "result_limit": values.limit,
        "truncated": total_count > len(selected_items),
    }


def _household_item_history(
    context: AgentToolContext,
    values: HouseholdReplenishmentInput,
    *,
    now: datetime,
) -> dict:
    if values.household_item_id is not None:
        item = context.db.scalar(
            select(HouseholdItem).where(
                HouseholdItem.workspace_id == context.workspace_id,
                HouseholdItem.id == values.household_item_id,
            )
        )
    else:
        item = _resolve_household_item_query(
            context.db,
            workspace_id=context.workspace_id,
            query=values.query or "",
        )
    if item is None:
        raise ValueError("Household item not found.")
    history_criteria = (
        HouseholdItemAcquisition.workspace_id == context.workspace_id,
        HouseholdItemAcquisition.household_item_id == item.id,
        HouseholdItemAcquisition.confirmed.is_(True),
        HouseholdItemAcquisition.voided_at.is_(None),
    )
    total_count = int(
        context.db.scalar(select(func.count(HouseholdItemAcquisition.id)).where(*history_criteria))
        or 0
    )
    rows = list(
        context.db.scalars(
            select(HouseholdItemAcquisition)
            .where(*history_criteria)
            .order_by(
                HouseholdItemAcquisition.acquired_at.desc(),
                HouseholdItemAcquisition.id.desc(),
            )
            .limit(values.limit)
        )
    )
    prediction = _latest_predictions(
        context.db,
        workspace_id=context.workspace_id,
        item_ids=[item.id],
    ).get(item.id)
    return {
        "view": values.view,
        "as_of": now,
        "items": [],
        "item": _household_item_dict(
            item,
            _current_prediction(prediction, item),
            total_count,
            now=now,
        ),
        "acquisitions": [
            {
                "acquired_at": _aware(row.acquired_at).date(),
                "quantity": row.quantity,
                "unit": _clean_optional(row.unit),
                "merchant": _clean_optional(row.merchant_normalized),
                "evidence_type": _safe_evidence_type(row.source),
            }
            for row in rows
        ],
        "learning": None,
        "total_count": total_count,
        "result_limit": values.limit,
        "truncated": total_count > len(rows),
    }


def _resolve_household_item_query(
    db: Session,
    *,
    workspace_id: int,
    query: str,
) -> HouseholdItem | None:
    normalized = query.casefold()
    exact = list(
        db.scalars(
            select(HouseholdItem)
            .where(
                HouseholdItem.workspace_id == workspace_id,
                func.lower(HouseholdItem.name) == normalized,
            )
            .order_by(HouseholdItem.id)
            .limit(2)
        )
    )
    matches = exact
    if not matches:
        pattern = f"%{_escape_like(normalized)}%"
        matches = list(
            db.scalars(
                select(HouseholdItem)
                .where(
                    HouseholdItem.workspace_id == workspace_id,
                    func.lower(HouseholdItem.name).like(pattern, escape="\\"),
                )
                .order_by(func.lower(HouseholdItem.name), HouseholdItem.id)
                .limit(2)
            )
        )
    if len(matches) > 1:
        raise ValueError("Household item query matched more than one tracked item.")
    return matches[0] if matches else None


def _get_receipts(context: AgentToolContext, values: ReceiptsInput) -> dict:
    if values.view == "detail":
        return _receipt_detail(context, values)

    criteria = [PurchaseReceipt.workspace_id == context.workspace_id]
    if values.view == "needs_review":
        criteria.append(PurchaseReceipt.parse_status.in_(_NEEDS_REVIEW_RECEIPT_STATUSES))
    if values.merchant:
        merchant_query = f"%{_escape_like(values.merchant.casefold())}%"
        criteria.append(
            or_(
                func.lower(func.coalesce(PurchaseReceipt.merchant_raw, "")).like(
                    merchant_query,
                    escape="\\",
                ),
                func.lower(func.coalesce(PurchaseReceipt.merchant_normalized, "")).like(
                    merchant_query,
                    escape="\\",
                ),
            )
        )
    if values.ingested_start_date:
        start = datetime.combine(date.fromisoformat(values.ingested_start_date), time.min, UTC)
        criteria.append(PurchaseReceipt.created_at >= start)
    if values.ingested_end_date:
        end = datetime.combine(date.fromisoformat(values.ingested_end_date), time.max, UTC)
        criteria.append(PurchaseReceipt.created_at <= end)

    total_count = int(
        context.db.scalar(select(func.count(PurchaseReceipt.id)).where(*criteria)) or 0
    )
    receipts = list(
        context.db.scalars(
            select(PurchaseReceipt)
            .where(*criteria)
            .order_by(PurchaseReceipt.created_at.desc(), PurchaseReceipt.id.desc())
            .limit(values.limit)
        )
    )
    receipt_ids = [receipt.id for receipt in receipts]
    counts = _receipt_line_counts(
        context.db,
        workspace_id=context.workspace_id,
        receipt_ids=receipt_ids,
    )
    safe_transactions = _safe_transaction_ids(
        context.db,
        workspace_id=context.workspace_id,
        transaction_ids=[
            receipt.transaction_id for receipt in receipts if receipt.transaction_id is not None
        ],
    )
    confirmed_household_links = _confirmed_receipt_household_item_ids(
        context.db,
        workspace_id=context.workspace_id,
        receipt_ids=receipt_ids,
    )
    return {
        "view": values.view,
        "receipts": [
            _receipt_summary_dict(
                receipt,
                counts.get(receipt.id, (0, 0, 0, 0)),
                safe_transactions=safe_transactions,
                confirmed_household_links=confirmed_household_links.get(receipt.id, ([], False)),
            )
            for receipt in receipts
        ],
        "receipt": None,
        "total_count": total_count,
        "result_limit": values.limit,
        "truncated": total_count > len(receipts),
    }


def _receipt_detail(context: AgentToolContext, values: ReceiptsInput) -> dict:
    receipt = context.db.scalar(
        select(PurchaseReceipt).where(
            PurchaseReceipt.workspace_id == context.workspace_id,
            PurchaseReceipt.id == values.receipt_id,
        )
    )
    if receipt is None:
        raise ValueError("Receipt not found.")

    line_scope = (
        PurchaseReceipt.workspace_id == context.workspace_id,
        PurchaseReceipt.id == receipt.id,
        PurchaseReceiptItem.receipt_id == PurchaseReceipt.id,
    )
    total_line_count = int(
        context.db.scalar(
            select(func.count(PurchaseReceiptItem.id))
            .select_from(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(*line_scope)
        )
        or 0
    )
    lines = list(
        context.db.scalars(
            select(PurchaseReceiptItem)
            .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
            .where(*line_scope)
            .order_by(PurchaseReceiptItem.id)
            .limit(values.line_limit)
        )
    )
    household_names = _safe_household_names(
        context.db,
        workspace_id=context.workspace_id,
        item_ids=[line.household_item_id for line in lines if line.household_item_id is not None],
    )
    confirmed_line_ids = _confirmed_receipt_line_ids(
        context.db,
        workspace_id=context.workspace_id,
        line_ids=[line.id for line in lines],
    )
    counts = _receipt_line_counts(
        context.db,
        workspace_id=context.workspace_id,
        receipt_ids=[receipt.id],
    ).get(receipt.id, (0, 0, 0, total_line_count))
    safe_transactions = _safe_transaction_ids(
        context.db,
        workspace_id=context.workspace_id,
        transaction_ids=[receipt.transaction_id] if receipt.transaction_id is not None else [],
    )
    confirmed_household_links = _confirmed_receipt_household_item_ids(
        context.db,
        workspace_id=context.workspace_id,
        receipt_ids=[receipt.id],
    )
    detail = {
        **_receipt_summary_dict(
            receipt,
            counts,
            safe_transactions=safe_transactions,
            confirmed_household_links=confirmed_household_links.get(receipt.id, ([], False)),
        ),
        "lines": [
            {
                "name": line.raw_name,
                "quantity": line.quantity,
                "unit": _clean_optional(line.unit),
                "line_total_cents": line.line_total_cents,
                "match_status": _safe_match_status(line, household_names),
                "household_item_name": household_names.get(line.household_item_id),
                "household_item_public_id": (
                    str(line.household_item_id)
                    if line.household_item_id in household_names
                    else None
                ),
                "confirmed_acquisition": line.id in confirmed_line_ids,
            }
            for line in lines
        ],
    }
    return {
        "view": values.view,
        "receipts": [],
        "receipt": detail,
        "total_count": total_line_count,
        "result_limit": values.line_limit,
        "truncated": total_line_count > len(lines),
    }


def _latest_predictions(
    db: Session,
    *,
    workspace_id: int,
    item_ids: list[int],
) -> dict[int, ReplenishmentPrediction]:
    if not item_ids:
        return {}
    ranked = (
        select(
            ReplenishmentPrediction.id.label("prediction_id"),
            func.row_number()
            .over(
                partition_by=ReplenishmentPrediction.household_item_id,
                order_by=(
                    ReplenishmentPrediction.generated_at.desc(),
                    ReplenishmentPrediction.id.desc(),
                ),
            )
            .label("position"),
        )
        .where(
            ReplenishmentPrediction.workspace_id == workspace_id,
            ReplenishmentPrediction.household_item_id.in_(item_ids),
        )
        .subquery()
    )
    rows = db.scalars(
        select(ReplenishmentPrediction)
        .join(ranked, ranked.c.prediction_id == ReplenishmentPrediction.id)
        .where(
            ReplenishmentPrediction.workspace_id == workspace_id,
            ranked.c.position == 1,
        )
    )
    return {row.household_item_id: row for row in rows}


def _current_prediction(
    prediction: ReplenishmentPrediction | None,
    item: HouseholdItem,
) -> ReplenishmentPrediction | None:
    if prediction is None or prediction.actual_next_acquisition_at is not None:
        return None
    if item.last_acquired_at is not None and _aware(item.last_acquired_at) > _aware(
        prediction.generated_at
    ):
        return None
    return prediction


def _confirmed_acquisition_counts(
    db: Session,
    *,
    workspace_id: int,
    item_ids: list[int],
) -> dict[int, int]:
    if not item_ids:
        return {}
    rows = db.execute(
        select(
            HouseholdItemAcquisition.household_item_id,
            func.count(HouseholdItemAcquisition.id),
        )
        .where(
            HouseholdItemAcquisition.workspace_id == workspace_id,
            HouseholdItemAcquisition.household_item_id.in_(item_ids),
            HouseholdItemAcquisition.confirmed.is_(True),
            HouseholdItemAcquisition.voided_at.is_(None),
        )
        .group_by(HouseholdItemAcquisition.household_item_id)
    )
    return {item_id: int(count) for item_id, count in rows}


def _learning_summary(context: AgentToolContext) -> dict:
    acquisition_scope = (
        HouseholdItemAcquisition.workspace_id == context.workspace_id,
        HouseholdItemAcquisition.confirmed.is_(True),
        HouseholdItemAcquisition.voided_at.is_(None),
    )
    return {
        "confirmed_acquisition_count": int(
            context.db.scalar(
                select(func.count(HouseholdItemAcquisition.id)).where(*acquisition_scope)
            )
            or 0
        ),
        "items_with_history": int(
            context.db.scalar(
                select(func.count(func.distinct(HouseholdItemAcquisition.household_item_id))).where(
                    *acquisition_scope
                )
            )
            or 0
        ),
        "items_with_predictions": int(
            context.db.scalar(
                select(func.count(func.distinct(ReplenishmentPrediction.household_item_id))).where(
                    ReplenishmentPrediction.workspace_id == context.workspace_id
                )
            )
            or 0
        ),
    }


def _household_item_dict(
    item: HouseholdItem,
    prediction: ReplenishmentPrediction | None,
    acquisition_count: int,
    *,
    now: datetime,
) -> dict:
    basis = _evidence_basis(prediction)
    if prediction is not None:
        canonical_due_state = due_state(float(prediction.due_score), has_history=True)
    else:
        score = due_score(item.last_acquired_at, item.cadence_days, now=now)
        canonical_due_state = due_state(score, has_history=item.last_acquired_at is not None)
    return {
        "public_id": str(item.id),
        "name": item.name,
        "quantity": _clean_optional(item.quantity),
        "unit": _clean_optional(item.unit),
        "due_state": "learning" if canonical_due_state == "unknown" else canonical_due_state,
        "predicted_due_on": _projected_due_at(item, prediction).date().isoformat()
        if item.last_acquired_at is not None or prediction is not None
        else None,
        "confidence_level": _confidence_level(prediction),
        "evidence_basis": basis,
        "reason": _prediction_reason(item, prediction, acquisition_count),
        "last_acquired_on": _aware(item.last_acquired_at).date().isoformat()
        if item.last_acquired_at
        else None,
        "confirmed_acquisition_count": acquisition_count,
        "snoozed": item.snoozed_until is not None and _aware(item.snoozed_until) > _aware(now),
    }


def _projected_due_at(
    item: HouseholdItem,
    prediction: ReplenishmentPrediction | None,
) -> datetime:
    if prediction is not None:
        return _aware(prediction.predicted_need_at)
    if item.last_acquired_at is not None:
        return _aware(item.last_acquired_at) + timedelta(days=item.cadence_days)
    return datetime.max.replace(tzinfo=UTC)


def _evidence_basis(
    prediction: ReplenishmentPrediction | None,
) -> Literal["configured_cadence", "purchase_pattern", "validated_model"]:
    if prediction is None:
        return "configured_cadence"
    if prediction.method.startswith("ml_ridge"):
        return "validated_model"
    if prediction.method.startswith("adaptive"):
        return "purchase_pattern"
    return "configured_cadence"


def _confidence_level(
    prediction: ReplenishmentPrediction | None,
) -> Literal["insufficient", "low", "medium", "high"]:
    if prediction is None or prediction.confidence_level not in _CONFIDENCE_LEVELS:
        return "insufficient"
    return prediction.confidence_level  # type: ignore[return-value]


def _prediction_reason(
    item: HouseholdItem,
    prediction: ReplenishmentPrediction | None,
    acquisition_count: int,
) -> str:
    basis = _evidence_basis(prediction)
    if basis == "validated_model":
        reason = (
            f"Based on validated timing patterns and {acquisition_count} confirmed acquisitions."
        )
    elif basis == "purchase_pattern":
        reason = f"Based on recent timing across {acquisition_count} confirmed acquisitions."
    else:
        reason = f"Based on the configured {item.cadence_days}-day cadence."
    if item.last_acquired_at is not None:
        acquired_on = _aware(item.last_acquired_at).date().isoformat()
        reason += f" Last recorded purchase date: {acquired_on}."
    return reason


def _receipt_line_counts(
    db: Session,
    *,
    workspace_id: int,
    receipt_ids: list[int],
) -> dict[int, tuple[int, int, int, int]]:
    if not receipt_ids:
        return {}
    safe_item = aliased(HouseholdItem)
    tracked = func.sum(case((safe_item.id.is_not(None), 1), else_=0))
    ignored = func.sum(
        case(
            (
                and_(
                    safe_item.id.is_(None),
                    PurchaseReceiptItem.match_status.in_(_IGNORED_LINE_STATUSES),
                ),
                1,
            ),
            else_=0,
        )
    )
    rows = db.execute(
        select(
            PurchaseReceiptItem.receipt_id,
            tracked,
            ignored,
            func.count(PurchaseReceiptItem.id),
        )
        .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
        .outerjoin(
            safe_item,
            and_(
                safe_item.id == PurchaseReceiptItem.household_item_id,
                safe_item.workspace_id == workspace_id,
            ),
        )
        .where(
            PurchaseReceipt.workspace_id == workspace_id,
            PurchaseReceipt.id.in_(receipt_ids),
        )
        .group_by(PurchaseReceiptItem.receipt_id)
    )
    result = {}
    for receipt_id, tracked_count, ignored_count, total_count in rows:
        tracked_value = int(tracked_count or 0)
        ignored_value = int(ignored_count or 0)
        total_value = int(total_count or 0)
        result[receipt_id] = (
            tracked_value,
            ignored_value,
            max(0, total_value - tracked_value - ignored_value),
            total_value,
        )
    return result


def _safe_transaction_ids(
    db: Session,
    *,
    workspace_id: int,
    transaction_ids: list[int],
) -> set[int]:
    if not transaction_ids:
        return set()
    return set(
        db.scalars(
            select(ExpenseTransaction.id).where(
                ExpenseTransaction.workspace_id == workspace_id,
                ExpenseTransaction.id.in_(transaction_ids),
                ExpenseTransaction.status != TransactionStatus.REMOVED.value,
            )
        )
    )


def _safe_household_names(
    db: Session,
    *,
    workspace_id: int,
    item_ids: list[int],
) -> dict[int, str]:
    if not item_ids:
        return {}
    rows = db.execute(
        select(HouseholdItem.id, HouseholdItem.name).where(
            HouseholdItem.workspace_id == workspace_id,
            HouseholdItem.id.in_(item_ids),
        )
    )
    return {item_id: name for item_id, name in rows}


def _confirmed_receipt_line_ids(
    db: Session,
    *,
    workspace_id: int,
    line_ids: list[int],
) -> set[int]:
    if not line_ids:
        return set()
    return set(
        db.scalars(
            select(HouseholdItemAcquisition.receipt_item_id).where(
                HouseholdItemAcquisition.workspace_id == workspace_id,
                HouseholdItemAcquisition.receipt_item_id.in_(line_ids),
                HouseholdItemAcquisition.confirmed.is_(True),
                HouseholdItemAcquisition.voided_at.is_(None),
            )
        )
    )


def _confirmed_receipt_household_item_ids(
    db: Session,
    *,
    workspace_id: int,
    receipt_ids: list[int],
) -> dict[int, tuple[list[int], bool]]:
    """Return bounded, tenant-verified receipt-to-item acquisition links."""

    if not receipt_ids:
        return {}
    pairs = (
        select(
            PurchaseReceiptItem.receipt_id.label("receipt_id"),
            HouseholdItemAcquisition.household_item_id.label("household_item_id"),
        )
        .join(
            HouseholdItemAcquisition,
            HouseholdItemAcquisition.receipt_item_id == PurchaseReceiptItem.id,
        )
        .join(PurchaseReceipt, PurchaseReceipt.id == PurchaseReceiptItem.receipt_id)
        .join(HouseholdItem, HouseholdItem.id == HouseholdItemAcquisition.household_item_id)
        .where(
            PurchaseReceipt.workspace_id == workspace_id,
            PurchaseReceipt.id.in_(receipt_ids),
            HouseholdItemAcquisition.workspace_id == workspace_id,
            HouseholdItemAcquisition.confirmed.is_(True),
            HouseholdItemAcquisition.voided_at.is_(None),
            HouseholdItem.workspace_id == workspace_id,
        )
        .distinct()
        .subquery()
    )
    ranked = select(
        pairs.c.receipt_id,
        pairs.c.household_item_id,
        func.row_number()
        .over(
            partition_by=pairs.c.receipt_id,
            order_by=pairs.c.household_item_id,
        )
        .label("position"),
    ).subquery()
    rows = db.execute(
        select(ranked.c.receipt_id, ranked.c.household_item_id, ranked.c.position)
        .where(ranked.c.position <= MAX_RECEIPT_LINE_RESULTS + 1)
        .order_by(ranked.c.receipt_id, ranked.c.position)
    )
    grouped: dict[int, list[int]] = {}
    truncated: set[int] = set()
    for receipt_id, item_id, position in rows:
        if int(position) > MAX_RECEIPT_LINE_RESULTS:
            truncated.add(int(receipt_id))
            continue
        grouped.setdefault(int(receipt_id), []).append(int(item_id))
    return {
        receipt_id: (item_ids, receipt_id in truncated) for receipt_id, item_ids in grouped.items()
    }


def _receipt_summary_dict(
    receipt: PurchaseReceipt,
    counts: tuple[int, int, int, int],
    *,
    safe_transactions: set[int],
    confirmed_household_links: tuple[list[int], bool] | None = None,
) -> dict:
    matched, ignored, unmatched, total = counts
    confirmed_household_item_ids, links_truncated = confirmed_household_links or ([], False)
    return {
        "public_id": str(receipt.id),
        "merchant": _clean_optional(receipt.merchant_raw),
        "purchased_at": receipt.purchased_at,
        "ingested_at": receipt.created_at,
        "total_cents": receipt.total_cents,
        "currency_code": (receipt.currency or "USD").upper(),
        "status": receipt.parse_status,
        "matched_line_count": matched,
        "ignored_line_count": ignored,
        "unmatched_line_count": unmatched,
        "total_line_count": total,
        "transaction_linked": receipt.transaction_id in safe_transactions,
        "confirmed_household_item_ids": [str(item_id) for item_id in confirmed_household_item_ids],
        "confirmed_household_item_ids_truncated": links_truncated,
    }


def _safe_match_status(
    line: PurchaseReceiptItem,
    household_names: dict[int, str],
) -> Literal["matched", "possible", "unmatched", "ignored"]:
    if line.match_status in _IGNORED_LINE_STATUSES:
        return "ignored"
    if line.household_item_id not in household_names:
        return "unmatched"
    if line.match_status == "possible":
        return "possible"
    return "matched"


def _safe_evidence_type(
    source: str,
) -> Literal["manual", "receipt", "transaction", "imported", "correction"]:
    if source.startswith("receipt_"):
        return "receipt"
    if source == "transaction_confirmed":
        return "transaction"
    if source == "imported":
        return "imported"
    if source == "correction":
        return "correction"
    return "manual"


def _clean_optional(value: str | None) -> str | None:
    clean = value.strip() if value else ""
    return clean or None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
