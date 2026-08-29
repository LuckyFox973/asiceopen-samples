"""encrypted extraction status

A password-protected PDF is not a parser failure — it is a readable document
waiting for a password.  Recording it as "failed" put it in the same bucket as
a corrupt file, which tells the owner to investigate something that only needs
a password they already have.

Revision ID: 0008_encrypted
Revises: 0007_pkce
Create Date: 2026-08-29 11:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_encrypted"
down_revision: str | None = "0007_pkce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "status IN ('extracted','empty','needs_ocr','unsupported','failed')"
NEW = "status IN ('extracted','empty','needs_ocr','encrypted','unsupported','failed')"


def upgrade() -> None:
    op.drop_constraint("status_valid", "document_text", type_="check")
    op.create_check_constraint("status_valid", "document_text", NEW)


def downgrade() -> None:
    # Rows carrying the new value would violate the old constraint; they are
    # re-parsed on the next run, so recording them as failures is honest.
    op.execute("UPDATE document_text SET status = 'failed' WHERE status = 'encrypted'")
    op.drop_constraint("status_valid", "document_text", type_="check")
    op.create_check_constraint("status_valid", "document_text", OLD)
