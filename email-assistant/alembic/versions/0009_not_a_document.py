"""not_a_document extraction status

An S/MIME signature block and a tracking pixel are not documents that failed
to parse — they are plumbing the mail system attached on its own.  Counting
them as unreadable put twenty of them in front of the owner as a problem to
solve, burying the two scans that actually needed a decision.

Revision ID: 0009_notdoc
Revises: 0008_encrypted
Create Date: 2026-08-29 12:05:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_notdoc"
down_revision: str | None = "0008_encrypted"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "status IN ('extracted','empty','needs_ocr','encrypted','unsupported','failed')"
NEW = (
    "status IN ('extracted','empty','needs_ocr','encrypted','not_a_document',"
    "'unsupported','failed')"
)


def upgrade() -> None:
    op.drop_constraint("status_valid", "document_text", type_="check")
    op.create_check_constraint("status_valid", "document_text", NEW)


def downgrade() -> None:
    op.execute("UPDATE document_text SET status = 'unsupported' WHERE status = 'not_a_document'")
    op.drop_constraint("status_valid", "document_text", type_="check")
    op.create_check_constraint("status_valid", "document_text", OLD)
