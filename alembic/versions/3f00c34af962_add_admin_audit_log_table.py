"""add admin_audit_log table

Revision ID: 3f00c34af962
Revises: a7c1e4b9d2f0
Create Date: 2026-10-30 12:30:00.000000

Additive only: creates the generic, append-only ``admin_audit_log``
table (Module 6 fix #3), distinct from Module 1's ``audit_log`` table,
which this migration does not touch.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3f00c34af962'
down_revision: Union[str, Sequence[str], None] = 'a7c1e4b9d2f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'admin_audit_log',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('actor_user_id', sa.Uuid(), nullable=False),
        sa.Column('target_user_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column(
            'details',
            sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name='fk_admin_audit_log_actor_user_id_users'),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name='fk_admin_audit_log_target_user_id_users'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('admin_audit_log')
