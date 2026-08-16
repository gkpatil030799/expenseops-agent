"""add narrow tenant routing functions

Revision ID: 20260815_0028
Revises: 20260815_0027
Create Date: 2026-08-15

The application runtime cannot read tenant tables before it knows which
workspace to select.  These functions are the deliberately small exception:
they resolve trusted, exact external identifiers to opaque internal IDs.  The
caller must then establish that workspace and perform every domain read or
write through the normal RLS-protected tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0028"
down_revision: str | None = "20260815_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROUTING_FUNCTIONS = (
    (
        "public.expenseops_route_plaid_item(text)",
        """
        CREATE FUNCTION public.expenseops_route_plaid_item(p_item_id text)
        RETURNS TABLE (workspace_id integer, plaid_item_id integer)
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT item.workspace_id, item.id
            FROM public.plaid_items AS item
            JOIN public.workspace_memberships AS membership
              ON membership.workspace_id = item.workspace_id
             AND (
                    item.owner_user_id IS NULL
                    OR membership.user_id = item.owner_user_id
                 )
            JOIN public.users AS member_user
              ON member_user.id = membership.user_id
             AND member_user.status = 'active'
            WHERE item.item_id = $1
            ORDER BY item.id
            LIMIT 1
        $function$
        """,
    ),
    (
        "public.expenseops_route_telegram_identity(text, text)",
        """
        CREATE FUNCTION public.expenseops_route_telegram_identity(
            p_telegram_user_id text,
            p_chat_id text
        )
        RETURNS TABLE (
            workspace_id integer,
            telegram_identity_id integer,
            user_id integer
        )
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT identity.workspace_id, identity.id, identity.user_id
            FROM public.telegram_identities AS identity
            JOIN public.workspace_memberships AS membership
              ON membership.workspace_id = identity.workspace_id
             AND membership.user_id = identity.user_id
            JOIN public.users AS member_user
              ON member_user.id = membership.user_id
             AND member_user.status = 'active'
            WHERE identity.telegram_user_id = $1
              AND identity.chat_id = $2
            ORDER BY identity.id
            LIMIT 1
        $function$
        """,
    ),
    (
        "public.expenseops_route_active_telegram_identity_by_link_code(text)",
        """
        CREATE FUNCTION public.expenseops_route_active_telegram_identity_by_link_code(
            p_code_hash text
        )
        RETURNS TABLE (workspace_id integer, telegram_identity_id integer)
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT identity.workspace_id, identity.id
            FROM public.telegram_link_codes AS link
            JOIN public.workspace_memberships AS link_membership
              ON link_membership.workspace_id = link.workspace_id
             AND link_membership.user_id = link.user_id
            JOIN public.users AS link_user
              ON link_user.id = link_membership.user_id
             AND link_user.status = 'active'
            JOIN public.telegram_identities AS identity
              ON identity.user_id = link.user_id
            JOIN public.workspace_memberships AS identity_membership
              ON identity_membership.workspace_id = identity.workspace_id
             AND identity_membership.user_id = identity.user_id
            JOIN public.users AS identity_user
              ON identity_user.id = identity_membership.user_id
             AND identity_user.status = 'active'
            WHERE link.code_hash = $1
              AND identity.enabled IS TRUE
            ORDER BY identity.id
            LIMIT 1
        $function$
        """,
    ),
    (
        "public.expenseops_route_telegram_link_code(text)",
        """
        CREATE FUNCTION public.expenseops_route_telegram_link_code(p_code_hash text)
        RETURNS TABLE (
            workspace_id integer,
            telegram_link_code_id integer,
            user_id integer
        )
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT link.workspace_id, link.id, link.user_id
            FROM public.telegram_link_codes AS link
            JOIN public.workspace_memberships AS membership
              ON membership.workspace_id = link.workspace_id
             AND membership.user_id = link.user_id
            JOIN public.users AS member_user
              ON member_user.id = membership.user_id
             AND member_user.status = 'active'
            WHERE link.code_hash = $1
            ORDER BY link.id
            LIMIT 1
        $function$
        """,
    ),
    (
        "public.expenseops_route_workspace_invitation(text)",
        """
        CREATE FUNCTION public.expenseops_route_workspace_invitation(p_token_hash text)
        RETURNS TABLE (workspace_id integer, workspace_invitation_id integer)
        LANGUAGE sql
        STABLE
        STRICT
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $function$
            SELECT invitation.workspace_id, invitation.id
            FROM public.workspace_invitations AS invitation
            JOIN public.workspace_memberships AS membership
              ON membership.workspace_id = invitation.workspace_id
             AND membership.user_id = invitation.invited_by_user_id
            JOIN public.users AS member_user
              ON member_user.id = membership.user_id
             AND member_user.status = 'active'
            WHERE invitation.token_hash = $1
            ORDER BY invitation.id
            LIMIT 1
        $function$
        """,
    ),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for signature, definition in ROUTING_FUNCTIONS:
        op.execute(sa.text(definition))
        # PostgreSQL grants EXECUTE to PUBLIC by default.  Production role
        # provisioning grants these exact signatures to the runtime role only.
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for signature, _definition in reversed(ROUTING_FUNCTIONS):
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {signature}"))
