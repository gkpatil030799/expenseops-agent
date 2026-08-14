"""add unified agent persistence foundation

Revision ID: 20260815_0026
Revises: 20260814_0025
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0026"
down_revision: str | None = "20260814_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AGENT_TENANT_TABLES = (
    "agent_conversations",
    "agent_messages",
    "agent_runs",
    "agent_tool_calls",
    "agent_action_proposals",
)


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_agent_conversations_status",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_conversations_public_id"),
    )
    op.create_index("ix_agent_conversations_workspace_id", "agent_conversations", ["workspace_id"])
    op.create_index(
        "ix_agent_conversations_owner_user_id", "agent_conversations", ["owner_user_id"]
    )
    op.create_index("ix_agent_conversations_status", "agent_conversations", ["status"])
    op.create_index(
        "ix_agent_conversations_workspace_owner_status_updated",
        "agent_conversations",
        ["workspace_id", "owner_user_id", "status", "updated_at"],
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_message_id", sa.String(64)),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("structured_response_json", sa.JSON()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_agent_messages_role",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'completed', 'failed')",
            name="ck_agent_messages_status",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_messages_public_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "client_message_id",
            name="uq_agent_messages_workspace_conversation_client",
        ),
    )
    for column in (
        "workspace_id",
        "conversation_id",
        "owner_user_id",
        "role",
        "status",
    ):
        op.create_index(f"ix_agent_messages_{column}", "agent_messages", [column])
    op.create_index(
        "ix_agent_messages_workspace_owner_conversation_created",
        "agent_messages",
        ["workspace_id", "owner_user_id", "conversation_id", "created_at"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trigger_message_id",
            sa.Integer(),
            sa.ForeignKey("agent_messages.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(128)),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("page_context_json", sa.JSON()),
        sa.Column("response_schema_version", sa.String(32)),
        sa.Column("request_id", sa.String(64)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("estimated_cost_micros", sa.Integer()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_runs_public_id"),
    )
    for column in (
        "workspace_id",
        "conversation_id",
        "owner_user_id",
        "trigger_message_id",
        "status",
        "request_id",
        "correlation_id",
    ):
        op.create_index(f"ix_agent_runs_{column}", "agent_runs", [column])
    op.create_index(
        "ix_agent_runs_workspace_owner_conversation_created",
        "agent_runs",
        ["workspace_id", "owner_user_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_workspace_status_updated",
        "agent_runs",
        ["workspace_id", "status", "updated_at"],
    )

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("operation_kind", sa.String(32), nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("result_metadata_json", sa.JSON()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation_kind IN ('read', 'write', 'external_action')",
            name="ck_agent_tool_calls_operation_kind",
        ),
        sa.CheckConstraint(
            "capability IN ('standard', 'purchasing')",
            name="ck_agent_tool_calls_capability",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'running', 'completed', 'failed', 'blocked')",
            name="ck_agent_tool_calls_status",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_tool_calls_public_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "run_id",
            "sequence",
            name="uq_agent_tool_calls_workspace_run_sequence",
        ),
    )
    for column in (
        "workspace_id",
        "run_id",
        "owner_user_id",
        "tool_name",
        "operation_kind",
        "status",
    ):
        op.create_index(f"ix_agent_tool_calls_{column}", "agent_tool_calls", [column])
    op.create_table(
        "agent_action_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "tool_call_id",
            sa.Integer(),
            sa.ForeignKey("agent_tool_calls.id", ondelete="SET NULL"),
        ),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("operation_kind", sa.String(32), nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("normalized_parameters_json", sa.JSON(), nullable=False),
        sa.Column("parameters_hash", sa.String(64), nullable=False),
        sa.Column("action_fingerprint", sa.String(64), nullable=False),
        sa.Column("preview_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "confirmed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("result_metadata_json", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("execution_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "operation_kind IN ('write', 'external_action')",
            name="ck_agent_action_proposals_operation_kind",
        ),
        sa.CheckConstraint(
            "capability IN ('standard', 'purchasing')",
            name="ck_agent_action_proposals_capability",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_confirmation', 'confirmed', 'executing', 'completed', "
            "'cancelled', 'expired', 'failed', 'ambiguous')",
            name="ck_agent_action_proposals_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_action_proposals_version"),
        sa.UniqueConstraint("public_id", name="uq_agent_action_proposals_public_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key",
            name="uq_agent_action_proposals_workspace_owner_idempotency",
        ),
    )
    for column in (
        "workspace_id",
        "owner_user_id",
        "conversation_id",
        "run_id",
        "tool_call_id",
        "tool_name",
        "operation_kind",
        "status",
        "idempotency_key",
        "confirmed_by_user_id",
        "expires_at",
    ):
        op.create_index(f"ix_agent_action_proposals_{column}", "agent_action_proposals", [column])
    op.create_index(
        "ix_agent_action_proposals_workspace_owner_status_expires",
        "agent_action_proposals",
        ["workspace_id", "owner_user_id", "status", "expires_at"],
    )
    active_states = sa.text(
        "status IN ('awaiting_confirmation', 'confirmed', 'executing')"
    )
    op.create_index(
        "uq_agent_action_proposals_active_fingerprint",
        "agent_action_proposals",
        ["workspace_id", "owner_user_id", "action_fingerprint"],
        unique=True,
        postgresql_where=active_states,
        sqlite_where=active_states,
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                CREATE FUNCTION expenseops_agent_proposal_snapshot_immutable()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF ROW(
                        NEW.workspace_id,
                        NEW.owner_user_id,
                        NEW.conversation_id,
                        NEW.tool_name,
                        NEW.tool_version,
                        NEW.operation_kind,
                        NEW.capability,
                        NEW.parameters_hash,
                        NEW.action_fingerprint,
                        NEW.idempotency_key,
                        NEW.expires_at
                    ) IS DISTINCT FROM ROW(
                        OLD.workspace_id,
                        OLD.owner_user_id,
                        OLD.conversation_id,
                        OLD.tool_name,
                        OLD.tool_version,
                        OLD.operation_kind,
                        OLD.capability,
                        OLD.parameters_hash,
                        OLD.action_fingerprint,
                        OLD.idempotency_key,
                        OLD.expires_at
                    ) OR NEW.normalized_parameters_json::jsonb
                        IS DISTINCT FROM OLD.normalized_parameters_json::jsonb
                      OR NEW.preview_json::jsonb IS DISTINCT FROM OLD.preview_json::jsonb
                    THEN
                        RAISE EXCEPTION 'agent action proposal snapshot is immutable'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        op.execute(
            sa.text(
                """
                CREATE TRIGGER trg_agent_action_proposal_snapshot_immutable
                BEFORE UPDATE ON agent_action_proposals
                FOR EACH ROW
                EXECUTE FUNCTION expenseops_agent_proposal_snapshot_immutable()
                """
            )
        )
        for table in AGENT_TENANT_TABLES:
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            op.execute(
                sa.text(
                    f'CREATE POLICY expenseops_workspace_isolation ON "{table}" '
                    "USING (current_setting('expenseops.bypass_rls', true) = 'on' OR "
                    "workspace_id = NULLIF(current_setting('expenseops.workspace_id', true), "
                    "'')::integer) "
                    "WITH CHECK (current_setting('expenseops.bypass_rls', true) = 'on' OR "
                    "workspace_id = NULLIF(current_setting('expenseops.workspace_id', true), "
                    "'')::integer)"
                )
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_agent_action_proposal_snapshot_immutable "
                "ON agent_action_proposals"
            )
        )
        op.execute(
            sa.text("DROP FUNCTION IF EXISTS expenseops_agent_proposal_snapshot_immutable()")
        )
        for table in reversed(AGENT_TENANT_TABLES):
            op.execute(
                sa.text(f'DROP POLICY IF EXISTS expenseops_workspace_isolation ON "{table}"')
            )
            op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))
    for table in reversed(AGENT_TENANT_TABLES):
        op.drop_table(table)
