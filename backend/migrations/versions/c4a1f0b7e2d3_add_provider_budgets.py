"""add provider budgets

Persisted, deployment-wide readings of the free allowances a run spends (Groq
from its response headers, Brave from the extension). Global by design: one row
per provider window, no user key, and durable so the panel's budget bar survives
a host restart instead of blanking out.

Revision ID: c4a1f0b7e2d3
Revises: 9f2b7c41d0aa
Create Date: 2026-10-12 10:31:07.114855

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a1f0b7e2d3'
down_revision: str | None = '9f2b7c41d0aa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'provider_budgets',
        sa.Column('budget_key', sa.String(length=64), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        # The refill window ("minute" | "day" | "month"). Named `period` because
        # `window` is a reserved word in PostgreSQL.
        sa.Column('period', sa.String(length=16), nullable=False),
        sa.Column('limit_value', sa.Integer(), nullable=True),
        sa.Column('remaining', sa.Integer(), nullable=True),
        sa.Column('reset_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('budget_key'),
    )
    with op.batch_alter_table('provider_budgets', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_provider_budgets_provider'), ['provider'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('provider_budgets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_provider_budgets_provider'))

    op.drop_table('provider_budgets')
