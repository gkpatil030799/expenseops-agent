from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.classification_activity_schemas import ClassificationActivityOut
from app.config import get_settings
from app.models import (
    HouseholdItem,
    HouseholdItemAcquisition,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ReplenishmentModelVersion,
    ReplenishmentPrediction,
    utc_now,
)
from app.replenishment_schemas import (
    AcquisitionCorrectionRequest,
    FeedbackRequest,
    GmailSyncRequest,
    ReceiptBatchDecisionRequest,
    ReceiptLineMatchRequest,
    ReceiptLineNewHouseholdItemRequest,
    ReceiptOut,
    WeeklyRunRequest,
)
from app.services.acquisition_service import AcquisitionService
from app.services.classification_activity_service import (
    MAX_CLASSIFICATION_ACTIVITY_RESULTS,
    ClassificationActivityService,
)
from app.services.gmail_receipt_service import GmailReceiptService
from app.services.receipt_ingestion_service import ReceiptIngestionService
from app.services.replenishment_model_service import ReplenishmentModelService
from app.services.replenishment_prediction_service import TrainingDatasetService
from app.services.weekly_replenishment_service import WeeklyReplenishmentService

router = APIRouter(prefix="/api/replenishment", tags=["replenishment-learning"])


@router.get("/classification-activity", response_model=ClassificationActivityOut)
def classification_activity(
    db: DbSession,
    activity_date: date,
    view: Literal[
        "summary",
        "categories",
        "new_categories",
        "matches",
        "staples",
        "cadence",
        "uncertain",
    ] = "summary",
    limit: int = Query(default=10, ge=1, le=MAX_CLASSIFICATION_ACTIVITY_RESULTS),
) -> ClassificationActivityOut:
    return ClassificationActivityService(db).read(
        activity_date=activity_date,
        view=view,
        limit=limit,
    )


@router.get("/summary")
def summary(db: DbSession) -> dict:
    now = utc_now()
    latest_predictions = list(
        db.execute(
            select(ReplenishmentPrediction)
            .options(selectinload(ReplenishmentPrediction.household_item))
            .order_by(
                ReplenishmentPrediction.generated_at.desc(), ReplenishmentPrediction.id.desc()
            )
        ).scalars()
    )
    unique: dict[int, ReplenishmentPrediction] = {}
    for prediction in latest_predictions:
        unique.setdefault(prediction.household_item_id, prediction)
    this_week = [
        _prediction_dict(prediction)
        for prediction in unique.values()
        if _aware(prediction.predicted_need_at) <= _aware(now) + timedelta(days=7)
    ]
    recent = list(
        db.execute(
            select(PurchaseReceipt)
            .options(
                selectinload(PurchaseReceipt.items).selectinload(
                    PurchaseReceiptItem.household_item
                ),
                selectinload(PurchaseReceipt.items).selectinload(PurchaseReceiptItem.acquisition),
            )
            .order_by(PurchaseReceipt.created_at.desc())
            .limit(8)
        ).scalars()
    )
    completed_errors = list(
        db.execute(
            select(ReplenishmentPrediction.error_days).where(
                ReplenishmentPrediction.error_days.is_not(None)
            )
        ).scalars()
    )
    active_model = (
        db.execute(
            select(ReplenishmentModelVersion)
            .where(ReplenishmentModelVersion.status == "active")
            .order_by(ReplenishmentModelVersion.trained_at.desc())
        )
        .scalars()
        .first()
    )
    latest_model = (
        db.execute(
            select(ReplenishmentModelVersion).order_by(
                ReplenishmentModelVersion.trained_at.desc(),
                ReplenishmentModelVersion.id.desc(),
            )
        )
        .scalars()
        .first()
    )
    latest_prediction = latest_predictions[0] if latest_predictions else None
    training_rows = TrainingDatasetService(db).rows()
    model_metrics = active_model.metrics_json if active_model else {}
    return {
        "this_week": sorted(this_week, key=lambda value: value["predicted_need_at"]),
        "learning": {
            "confirmed_acquisitions": db.scalar(
                select(func.count(HouseholdItemAcquisition.id)).where(
                    HouseholdItemAcquisition.confirmed.is_(True),
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
            )
            or 0,
            "items_with_history": db.scalar(
                select(func.count(func.distinct(HouseholdItemAcquisition.household_item_id))).where(
                    HouseholdItemAcquisition.confirmed.is_(True),
                    HouseholdItemAcquisition.voided_at.is_(None),
                )
            )
            or 0,
            "active_model": active_model.version if active_model else None,
        },
        "recent_receipts": [_receipt_dict(receipt) for receipt in recent],
        "accuracy": {
            "evaluated_predictions": len(completed_errors),
            "mae_days": (
                round(sum(completed_errors) / len(completed_errors), 2)
                if completed_errors
                else None
            ),
            "current_prediction_method": (latest_prediction.method if latest_prediction else None),
            "training_observations": len(training_rows),
            "validation_observations": model_metrics.get("validation_rows", 0),
            "validation_method": model_metrics.get("validation_method"),
            "baseline_mae_days": model_metrics.get("baseline_mae_days"),
            "active_model_mae_days": model_metrics.get("candidate_mae_days"),
            "improvement_pct": model_metrics.get("improvement_pct"),
            "confidence_level": (
                model_metrics.get("data_confidence_level")
                if active_model
                else (latest_prediction.confidence_level if latest_prediction else "insufficient")
            ),
            "latest_model_status": latest_model.status if latest_model else None,
            "decision_reason": (
                (latest_model.metrics_json or {}).get("decision_reason") if latest_model else None
            ),
        },
    }


@router.get("/receipts")
def list_receipts(
    db: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    bucket: Literal["all", "active", "history"] = "all",
    include_meta: bool = False,
) -> list[dict] | dict:
    stmt = select(PurchaseReceipt)
    if bucket == "active":
        stmt = stmt.where(PurchaseReceipt.parse_status.in_(["needs_review", "failed"]))
    elif bucket == "history":
        stmt = stmt.where(PurchaseReceipt.parse_status.in_(["confirmed", "ignored"]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    receipts = db.execute(
        stmt.options(
            selectinload(PurchaseReceipt.items).selectinload(PurchaseReceiptItem.household_item),
            selectinload(PurchaseReceipt.items).selectinload(PurchaseReceiptItem.acquisition),
        )
        .order_by(PurchaseReceipt.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars()
    items = [_receipt_dict(receipt) for receipt in receipts]
    if not include_meta:
        return items
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }


@router.post("/receipts/upload", response_model=ReceiptOut)
def upload_receipt_photo(db: DbSession, file: UploadFile = File(...)) -> dict:
    # This handler includes synchronous image validation and provider I/O, so a
    # normal def keeps it on FastAPI's worker thread instead of blocking the
    # application event loop.
    content = file.file.read(get_settings().receipt_max_attachment_bytes + 1)
    file.file.close()
    try:
        receipt = ReceiptIngestionService(db).ingest_attachment(
            source="web",
            source_external_id=f"upload-{uuid4()}",
            content=content,
            mime_type=str(file.content_type or "application/octet-stream"),
            filename=str(file.filename or "receipt"),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403, detail="Receipt upload is no longer authorized."
        ) from exc
    return _receipt_dict(receipt)


@router.post("/receipts/{receipt_id}/confirm", response_model=ReceiptOut)
def confirm_receipt(receipt_id: int, db: DbSession) -> dict:
    try:
        return _receipt_dict(ReceiptIngestionService(db).confirm(receipt_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/receipts/{receipt_id}/decisions", response_model=ReceiptOut)
def submit_receipt_decisions(
    receipt_id: int,
    payload: ReceiptBatchDecisionRequest,
    db: DbSession,
) -> dict:
    try:
        receipt = ReceiptIngestionService(db).apply_decisions(
            receipt_id,
            decisions=[decision.model_dump() for decision in payload.decisions],
            expected_updated_at=payload.expected_updated_at,
            confirm=payload.finalize == "confirm",
            acknowledge_undecided=payload.acknowledge_undecided,
        )
        return _receipt_dict(receipt)
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if detail == "receipt_changed_refresh_required" else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/receipts/{receipt_id}/ignore", response_model=ReceiptOut)
def ignore_receipt(receipt_id: int, db: DbSession) -> dict:
    try:
        return _receipt_dict(ReceiptIngestionService(db).ignore(receipt_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/receipts/{receipt_id}/restore", response_model=ReceiptOut)
def restore_receipt(receipt_id: int, db: DbSession) -> dict:
    try:
        return _receipt_dict(ReceiptIngestionService(db).restore(receipt_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/receipts/{receipt_id}/items/{line_id}")
def update_receipt_line(
    receipt_id: int, line_id: int, payload: ReceiptLineMatchRequest, db: DbSession
) -> dict:
    try:
        line = ReceiptIngestionService(db).update_line_match(
            receipt_id, line_id, payload.household_item_id, rejected=payload.rejected
        )
        return {
            "id": line.id,
            "match_status": line.match_status,
            "household_item_id": line.household_item_id,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/receipts/{receipt_id}/items/{line_id}/track")
def track_receipt_line_as_household_item(
    receipt_id: int,
    line_id: int,
    payload: ReceiptLineNewHouseholdItemRequest,
    db: DbSession,
) -> dict:
    try:
        item, line = ReceiptIngestionService(db).track_line_as_new_household_item(
            receipt_id,
            line_id,
            name=payload.name,
            cadence_days=payload.cadence_days,
            replenishment_mode=payload.replenishment_mode,
        )
        return {
            "household_item": {"id": item.id, "name": item.name},
            "receipt_line": {
                "id": line.id,
                "match_status": line.match_status,
                "household_item_id": line.household_item_id,
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/items/{item_id}/feedback")
def record_feedback(item_id: int, payload: FeedbackRequest, db: DbSession) -> dict:
    item = db.get(HouseholdItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Household item not found.")
    event = AcquisitionService(db).feedback(
        item,
        payload.feedback_type,
        prediction_id=payload.prediction_id,
        metadata=payload.metadata,
    )
    return {"id": event.id, "feedback_type": event.feedback_type}


@router.post("/acquisitions/{acquisition_id}/undo")
def undo_acquisition(acquisition_id: int, db: DbSession) -> dict:
    try:
        event = AcquisitionService(db).undo(acquisition_id)
        return {"id": event.id, "voided_at": event.voided_at}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/acquisitions/{acquisition_id}/correct")
def correct_acquisition(
    acquisition_id: int, payload: AcquisitionCorrectionRequest, db: DbSession
) -> dict:
    item = db.get(HouseholdItem, payload.household_item_id) if payload.household_item_id else None
    if payload.household_item_id and item is None:
        raise HTTPException(status_code=404, detail="Household item not found.")
    try:
        event = AcquisitionService(db).correct(
            acquisition_id,
            household_item=item,
            acquired_at=payload.acquired_at,
            quantity=payload.quantity,
            unit=payload.unit,
        )
        return {"id": event.id, "household_item_id": event.household_item_id}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/weekly-run")
def run_weekly(payload: WeeklyRunRequest, db: DbSession) -> dict:
    run = WeeklyReplenishmentService(db).run(trigger="manual", run_key=payload.run_key)
    return _run_dict(run)


@router.post("/models/{model_id}/rollback")
def rollback_model(model_id: int, db: DbSession) -> dict:
    try:
        model = ReplenishmentModelService(db).rollback_to(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": model.id,
        "version": model.version,
        "status": model.status,
        "reason": model.metrics_json.get("activation_reason"),
    }


@router.get("/models")
def list_model_versions(db: DbSession, limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    models = db.execute(
        select(ReplenishmentModelVersion)
        .order_by(
            ReplenishmentModelVersion.trained_at.desc(),
            ReplenishmentModelVersion.id.desc(),
        )
        .limit(limit)
    ).scalars()
    return [
        {
            "id": model.id,
            "version": model.version,
            "status": model.status,
            "trained_at": model.trained_at,
            "training_rows": model.training_rows,
            "metrics": model.metrics_json,
        }
        for model in models
    ]


@router.post("/gmail/sync")
def sync_gmail(payload: GmailSyncRequest, db: DbSession) -> dict:
    try:
        result = GmailReceiptService(db).sync(max_results=payload.max_results)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"scanned": result.scanned, "ingested": result.ingested, "skipped": result.skipped}


@router.get("/gmail/status")
def gmail_sync_status(db: DbSession) -> dict:
    return GmailReceiptService(db).status()


def _receipt_dict(receipt: PurchaseReceipt) -> dict:
    tracked = sum(1 for line in receipt.items if line.household_item_id is not None)
    ignored = sum(
        1
        for line in receipt.items
        if line.household_item_id is None and line.match_status in {"rejected", "irrelevant"}
    )
    total = len(receipt.items)
    parse_quality, quality_message = _receipt_quality(receipt)
    return {
        "id": receipt.id,
        "source": receipt.source,
        "merchant": receipt.merchant_raw,
        "purchased_at": receipt.purchased_at,
        "subtotal_cents": receipt.subtotal_cents,
        "tax_cents": receipt.tax_cents,
        "tip_cents": receipt.tip_cents,
        "discount_cents": receipt.discount_cents,
        "total_cents": receipt.total_cents,
        "currency": receipt.currency,
        "line_items_complete": receipt.line_items_complete,
        "quality_warnings": list(receipt.quality_warnings_json or []),
        "arithmetic_status": receipt.arithmetic_status,
        "parse_status": receipt.parse_status,
        "parse_quality": parse_quality,
        "quality_message": quality_message,
        "parse_confidence": receipt.parse_confidence,
        "failure_code": receipt.failure_code,
        "transaction_id": receipt.transaction_id,
        "transaction_match_status": receipt.transaction_match_status,
        "transaction_match_confidence": receipt.transaction_match_confidence,
        "transaction_match_attempted_at": receipt.transaction_match_attempted_at,
        "transaction_matched_at": receipt.transaction_matched_at,
        "created_at": receipt.created_at,
        "updated_at": receipt.updated_at,
        "decision_summary": {
            "tracked": tracked,
            "ignored": ignored,
            "undecided": max(0, total - tracked - ignored),
            "total": total,
        },
        "items": [
            {
                "id": line.id,
                "raw_name": line.raw_name,
                "normalized_name": line.normalized_name,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price_cents": line.unit_price_cents,
                "line_total_cents": line.line_total_cents,
                "brand": line.brand,
                "household_item_id": line.household_item_id,
                "household_item_name": line.household_item.name if line.household_item else None,
                "acquisition_id": line.acquisition.id if line.acquisition else None,
                "match_status": line.match_status,
                "match_confidence": line.match_confidence,
                "classification": line.classification,
                "classification_confidence": line.classification_confidence,
                "canonical_name": line.canonical_name,
                "spending_parent_category": line.spending_parent_category,
                "classification_subcategory_name": line.classification_subcategory_name,
                "classification_concept_name": line.classification_concept_name,
                "item_activity_type": line.item_activity_type,
                "replenishment_eligibility": line.replenishment_eligibility,
                "classification_confidence_band": line.classification_confidence_band,
                "classification_authority": line.classification_authority,
                "classification_decision_state": line.classification_decision_state,
                "classification_applied_at": line.classification_applied_at,
                "classification_finalized_at": line.classification_finalized_at,
                "classification_corrected_at": line.classification_corrected_at,
                "classification_version": line.classification_version,
            }
            for line in receipt.items
        ],
    }


def _receipt_quality(receipt: PurchaseReceipt) -> tuple[str, str | None]:
    if receipt.parse_status == "failed":
        messages = {
            "receipt_non_receipt": "This image does not look like a receipt.",
            "receipt_attachment_too_large": "This file is too large. Upload a photo under 10 MB.",
            "unsupported_receipt_type": "Upload a JPEG, PNG, WebP, or PDF receipt.",
            "receipt_media_type_mismatch": "The file type does not match its contents.",
            "receipt_provider_timeout": "Receipt processing timed out. Try again.",
            "receipt_provider_rate_limited": "Receipt processing is busy right now. Try again.",
            "receipt_provider_unavailable": "Receipt processing is temporarily unavailable.",
        }
        return (
            "failed",
            messages.get(
                receipt.failure_code,
                "I couldn't reliably read this receipt. "
                "Try a clearer photo with the full receipt visible.",
            ),
        )
    messages = {
        "receipt_partial_extraction": "I read part of this receipt. Check the extracted items.",
        "receipt_total_uncertain": "I found useful details but could not reliably read the total.",
        "receipt_arithmetic_mismatch": (
            "The extracted amounts do not fully add up. Review before confirming."
        ),
        "receipt_transaction_match_ambiguous": "More than one transaction may match this receipt.",
    }
    if receipt.failure_code in messages:
        return "partial", messages[receipt.failure_code]
    return "complete", None


def _prediction_dict(prediction: ReplenishmentPrediction) -> dict:
    explanation = _prediction_explanation(prediction)
    return {
        "id": prediction.id,
        "household_item_id": prediction.household_item_id,
        "household_item_name": prediction.household_item.name,
        "predicted_need_at": prediction.predicted_need_at,
        "predicted_days_remaining": prediction.predicted_days_remaining,
        "due_score": prediction.due_score,
        "confidence": prediction.confidence,
        "confidence_level": prediction.confidence_level,
        "method": prediction.method,
        "explanation": explanation,
        "evidence_label": (
            "Early estimate — limited history"
            if prediction.confidence_level in {"insufficient", "low"}
            else None
        ),
    }


def _run_dict(run) -> dict:
    return {
        "id": run.id,
        "run_key": run.run_key,
        "status": run.status,
        "dataset_size": run.dataset_size,
        "metrics": run.metrics_json,
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _prediction_explanation(prediction: ReplenishmentPrediction) -> str:
    snapshot = prediction.feature_snapshot or {}
    history = int(snapshot.get("history_count", 0))
    last_acquired = prediction.household_item.last_acquired_at
    days_since = (
        max(0, int((_aware(prediction.generated_at) - _aware(last_acquired)).days))
        if last_acquired
        else None
    )
    suffix = f" Last purchased {days_since} days ago." if days_since is not None else ""
    if prediction.method.startswith("ml_ridge"):
        return f"Based on {history} previous purchases and recent purchase intervals.{suffix}"
    if prediction.method.startswith("adaptive"):
        typical = max(
            1,
            round(
                (_aware(prediction.predicted_need_at) - _aware(last_acquired)).total_seconds()
                / 86_400
            )
            if last_acquired
            else round(float(snapshot.get("weighted_interval", 1))),
        )
        return f"Usually restocked every ~{typical} days.{suffix}"
    cadence = round(float(snapshot.get("cadence_days", 0)))
    return f"Scheduled every {cadence} days.{suffix}"
