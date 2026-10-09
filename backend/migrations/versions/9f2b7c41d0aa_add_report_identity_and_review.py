"""add report identity and review state

Reports become attributable (Neon Auth user id/email/name) and reviewable
(status, note, reviewer) so the reporter can see and withdraw their own
submissions and an admin can accept or reject them.

Revision ID: 9f2b7c41d0aa
Revises: 18aa3c193394
Create Date: 2026-10-09 12:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f2b7c41d0aa'
down_revision: str | None = '18aa3c193394'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows predate sign-in: they keep their text but have no reporter
    # (NULL user_*), and are treated as already-visible "pending" reports.
    with op.batch_alter_table("user_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("user_email", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("user_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("company_name", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("risk_level", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("risk_score", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.create_index(batch_op.f("ix_user_reports_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_user_reports_user_email"), ["user_email"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_user_reports_company_name"), ["company_name"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_user_reports_status"), ["status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("user_reports", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_reports_status"))
        batch_op.drop_index(batch_op.f("ix_user_reports_company_name"))
        batch_op.drop_index(batch_op.f("ix_user_reports_user_email"))
        batch_op.drop_index(batch_op.f("ix_user_reports_user_id"))
        batch_op.drop_column("updated_at")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_note")
        batch_op.drop_column("status")
        batch_op.drop_column("risk_score")
        batch_op.drop_column("risk_level")
        batch_op.drop_column("company_name")
        batch_op.drop_column("user_name")
        batch_op.drop_column("user_email")
        batch_op.drop_column("user_id")
