"""add promotion intelligence

Revision ID: 20260809_0010
Revises: 20260809_0009
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0010"
down_revision: str | None = "20260809_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "promotion_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gmail_message_id", sa.String(255), nullable=False),
        sa.Column("gmail_thread_id", sa.String(255)),
        sa.Column("gmail_history_id", sa.String(255)),
        sa.Column("sender_name", sa.String(255)),
        sa.Column("sender_email", sa.String(320)),
        sa.Column("sender_domain", sa.String(255)),
        sa.Column("subject", sa.String(1000), nullable=False),
        sa.Column("snippet", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gmail_label_snapshot", sa.JSON(), nullable=False),
        sa.Column("parse_status", sa.String(32), nullable=False),
        sa.Column("parse_confidence", sa.Float()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("content_fingerprint", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("gmail_message_id", name="uq_promotion_message_gmail_id"),
    )
    for name, cols in (
        ("gmail_message_id", ["gmail_message_id"]),
        ("sender_domain", ["sender_domain"]),
        ("received_at", ["received_at"]),
        ("parse_status", ["parse_status"]),
    ):
        op.create_index(f"ix_promotion_messages_{name}", "promotion_messages", cols)

    op.create_table(
        "promotion_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "promotion_message_id",
            sa.Integer(),
            sa.ForeignKey("promotion_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant_raw", sa.String(255), nullable=False),
        sa.Column("merchant_normalized", sa.String(255), nullable=False),
        sa.Column("primary_category", sa.String(64), nullable=False),
        sa.Column("secondary_categories", sa.JSON(), nullable=False),
        sa.Column("offer_type", sa.String(32), nullable=False),
        sa.Column("headline", sa.String(1000), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("percent_off", sa.Float()),
        sa.Column("amount_off", sa.Float()),
        sa.Column("currency", sa.String(8)),
        sa.Column("minimum_spend", sa.Float()),
        sa.Column("original_price", sa.Float()),
        sa.Column("discounted_price", sa.Float()),
        sa.Column("promo_code", sa.String(128)),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("expiry_precision", sa.String(32), nullable=False),
        sa.Column("destination_url", sa.Text()),
        sa.Column("destination_domain", sa.String(255)),
        sa.Column("terms_summary", sa.Text()),
        sa.Column("loyalty_required", sa.Boolean()),
        sa.Column("new_customer_only", sa.Boolean()),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("trust_status", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("campaign_fingerprint", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("score_breakdown_json", sa.JSON(), nullable=False),
        sa.Column("saved", sa.Boolean(), nullable=False),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_fingerprint", name="uq_promotion_campaign_fingerprint"),
    )
    for name, cols in (
        ("promotion_message_id", ["promotion_message_id"]),
        ("merchant_normalized", ["merchant_normalized"]),
        ("primary_category", ["primary_category"]),
        ("expires_at", ["expires_at"]),
        ("trust_status", ["trust_status"]),
        ("status", ["status"]),
        ("campaign_fingerprint", ["campaign_fingerprint"]),
        ("score", ["score"]),
        ("saved", ["saved"]),
    ):
        op.create_index(f"ix_promotion_offers_{name}", "promotion_offers", cols)

    op.create_table(
        "promotion_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "promotion_offer_id",
            sa.Integer(),
            sa.ForeignKey("promotion_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("feedback_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_promotion_feedback_promotion_offer_id", "promotion_feedback", ["promotion_offer_id"]
    )
    op.create_index("ix_promotion_feedback_feedback_type", "promotion_feedback", ["feedback_type"])
    op.create_table(
        "promotion_digest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offers_considered", sa.Integer(), nullable=False),
        sa.Column("offers_included", sa.Integer(), nullable=False),
        sa.Column("delivery_channel", sa.String(32), nullable=False),
        sa.Column("delivery_status", sa.String(32), nullable=False),
        sa.Column("campaign_fingerprints", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_promotion_digest_runs_delivery_status", "promotion_digest_runs", ["delivery_status"]
    )
    op.create_table(
        "promotion_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("preferred_categories", sa.JSON(), nullable=False),
        sa.Column("muted_categories", sa.JSON(), nullable=False),
        sa.Column("preferred_merchants", sa.JSON(), nullable=False),
        sa.Column("muted_merchants", sa.JSON(), nullable=False),
        sa.Column("minimum_score", sa.Float(), nullable=False),
        sa.Column("maximum_deals_per_digest", sa.Integer(), nullable=False),
        sa.Column("digest_cadence", sa.String(32), nullable=False),
        sa.Column("digest_time", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("include_minimum_spend", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "gmail_sync_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(255), nullable=False),
        sa.Column("history_id", sa.String(255)),
        sa.Column("initial_backfill_complete", sa.Boolean(), nullable=False),
        sa.Column("watch_expiration_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_key", name="uq_gmail_sync_checkpoint_account"),
    )
    op.create_index(
        "ix_gmail_sync_checkpoints_account_key", "gmail_sync_checkpoints", ["account_key"]
    )


def downgrade() -> None:
    op.drop_table("gmail_sync_checkpoints")
    op.drop_table("promotion_settings")
    op.drop_table("promotion_digest_runs")
    op.drop_table("promotion_feedback")
    op.drop_table("promotion_offers")
    op.drop_table("promotion_messages")
