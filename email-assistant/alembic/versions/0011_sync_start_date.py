"""Remember the start date a full pass walked from.

Moving a mailbox's start date earlier used to change nothing: the engine saw a
completed initial sync and went straight to the history cursor, so the older
mail the operator had just asked for was never fetched and nothing said so.

Recording the date the pass actually covered turns that into a decision the
engine can make.  Existing rows are backfilled with the account's current start
date — that is exactly what the completed pass walked — so upgrading does not
provoke a re-walk.

Revision ID: 0011_syncstart
Revises: 0010_filing
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011_syncstart"
down_revision: str | None = "0010_filing"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("sync_state", sa.Column("initial_sync_start_date", sa.Date(), nullable=True))
    # Only a completed pass has covered anything; a half-finished one keeps its
    # page token and NULL, and the engine treats that as "walk it again".
    #
    # A mailbox without its own start date walked from the configured default,
    # so that is the truthful value to record.  Reading it here is the last
    # moment it is knowable: the operator changes it immediately afterwards,
    # and then nothing remembers what the finished pass actually covered.
    from app.core.config import get_settings

    op.execute(
        sa.text(
            """
            UPDATE sync_state AS s
               SET initial_sync_start_date = COALESCE(a.sync_start_date, :fallback)
              FROM mailbox_account AS a
             WHERE a.id = s.account_id
               AND s.initial_sync_completed_at IS NOT NULL
            """
        ).bindparams(fallback=get_settings().sync_start_date)
    )


def downgrade() -> None:
    op.drop_column("sync_state", "initial_sync_start_date")
