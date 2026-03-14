"""Add noHL category removal settings

Revision ID: c3a1f8e92b47
Revises: 838b25837534
Create Date: 2026-03-14 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3a1f8e92b47'
down_revision = '838b25837534'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'instance' in existing_tables:
        existing_columns = [col['name'] for col in inspector.get_columns('instance')]
        with op.batch_alter_table('instance', schema=None) as batch_op:
            if 'remove_category_on_nohl_removal' not in existing_columns:
                batch_op.add_column(sa.Column('remove_category_on_nohl_removal', sa.Boolean(), nullable=True))
            if 'nohl_removal_categories' not in existing_columns:
                batch_op.add_column(sa.Column('nohl_removal_categories', sa.Text(), nullable=True))


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'instance' in existing_tables:
        existing_columns = [col['name'] for col in inspector.get_columns('instance')]
        with op.batch_alter_table('instance', schema=None) as batch_op:
            if 'nohl_removal_categories' in existing_columns:
                batch_op.drop_column('nohl_removal_categories')
            if 'remove_category_on_nohl_removal' in existing_columns:
                batch_op.drop_column('remove_category_on_nohl_removal')
