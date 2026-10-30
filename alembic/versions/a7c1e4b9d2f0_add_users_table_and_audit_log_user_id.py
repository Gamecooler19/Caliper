"""add users table and audit_log.user_id

Revision ID: a7c1e4b9d2f0
Revises: d3870a79a78a
Create Date: 2026-10-30 12:00:00.000000

Additive only: creates ``users`` and adds a nullable ``audit_log.user_id``
foreign key. Existing audit rows keep user_id NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c1e4b9d2f0'
down_revision: Union[str, Sequence[str], None] = 'd3870a79a78a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.Enum('admin', 'analyst', 'auditor', name='user_role'), nullable=False),
    sa.Column('totp_secret_encrypted', sa.Text(), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('email = lower(email)', name='ck_users_email_lowercase'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.add_column('audit_log', sa.Column('user_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_audit_log_user_id_users', 'audit_log', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_audit_log_user_id_users', 'audit_log', type_='foreignkey')
    op.drop_column('audit_log', 'user_id')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    sa.Enum(name='user_role').drop(op.get_bind(), checkfirst=True)
