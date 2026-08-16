from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.logging_config import configure_logging, log_event
from app.models import GmailAccount, OAuthState, PlaidItem, SplitwiseIntegration
from app.security import rotate_secret
from app.tenancy import clear_session_tenant, set_trusted_workspace, workspace_ids

logger = logging.getLogger(__name__)


def main() -> int:
    settings = get_settings()
    settings.validate_worker_runtime()
    configure_logging(settings)
    counts: dict[str, int] = {}
    try:
        with SessionLocal() as db:
            clear_session_tenant(db)
            counts["oauth_states"] = _rotate_column(db, OAuthState, "payload_encrypted")
            for name in ("gmail_accounts", "splitwise_integrations", "plaid_items"):
                counts[name] = 0
            for workspace_id in workspace_ids(db):
                set_trusted_workspace(db, workspace_id)
                counts["gmail_accounts"] += _rotate_column(
                    db, GmailAccount, "refresh_token_encrypted"
                )
                counts["splitwise_integrations"] += _rotate_column(
                    db, SplitwiseIntegration, "credentials_encrypted"
                )
                counts["plaid_items"] += _rotate_column(db, PlaidItem, "access_token_encrypted")
            db.commit()
        log_event(logger, "encryption_key_rotation_completed", **counts)
        return 0
    except Exception as exc:
        log_event(
            logger,
            "encryption_key_rotation_failed",
            level=logging.ERROR,
            error_class=type(exc).__name__,
        )
        return 1


def _rotate_column(db, model, attribute: str) -> int:
    count = 0
    for row in db.scalars(select(model)):
        value = getattr(row, attribute)
        if value:
            updated = rotate_secret(value)
            if updated != value:
                setattr(row, attribute, updated)
                count += 1
    return count


if __name__ == "__main__":
    raise SystemExit(main())
