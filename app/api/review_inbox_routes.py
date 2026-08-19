from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, CurrentWorkspace, DbSession
from app.review_inbox_schemas import (
    ReviewInboxBadgeOut,
    ReviewInboxPageOut,
    ReviewItemSeenOut,
)
from app.services.review_inbox_service import ReviewInboxError, ReviewInboxService

router = APIRouter(prefix="/api/review-inbox", tags=["review-inbox"])


@router.get("", response_model=ReviewInboxPageOut)
def list_review_inbox(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReviewInboxPageOut:
    page = ReviewInboxService(db).list_open(limit=limit, offset=offset)
    return ReviewInboxPageOut(
        items=page.items,
        total_open=page.total_open,
        unread_count=page.unread_count,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/badge", response_model=ReviewInboxBadgeOut)
def review_inbox_badge(
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> ReviewInboxBadgeOut:
    page = ReviewInboxService(db).list_open(limit=1)
    return ReviewInboxBadgeOut(open_count=page.total_open, unread_count=page.unread_count)


@router.post("/{public_id}/seen", response_model=ReviewItemSeenOut)
def mark_review_item_seen(
    public_id: str,
    db: DbSession,
    _user: CurrentUser,
    _workspace: CurrentWorkspace,
) -> ReviewItemSeenOut:
    try:
        item = ReviewInboxService(db).mark_seen(public_id)
    except ReviewInboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if item.seen_at is None:  # pragma: no cover - guarded by service
        raise HTTPException(status_code=500, detail="Review item was not marked seen.")
    return ReviewItemSeenOut(public_id=item.public_id, seen_at=item.seen_at)
