"""Add orphaned files support

Revision ID: 838b25837534
Revises: 
Create Date: 2026-01-07 22:23:21.273655

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '838b25837534'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Check if this is a new database or an existing one
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'instance' not in existing_tables:
        # New database - create all tables
        op.create_table('instance',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('host', sa.String(length=200), nullable=False),
            sa.Column('username', sa.String(length=100), nullable=True),
            sa.Column('password', sa.String(length=100), nullable=True),
            sa.Column('qbt_download_dir', sa.String(length=500), nullable=True),
            sa.Column('mapped_download_dir', sa.String(length=500), nullable=True),
            sa.Column('tag_nohardlinks', sa.Boolean(), nullable=True),
            sa.Column('pause_cross_seeded_torrents', sa.Boolean(), nullable=True),
            sa.Column('tag_unregistered_torrents', sa.Boolean(), nullable=True),
            sa.Column('orphaned_scan_enabled', sa.Boolean(), nullable=True),
            sa.Column('orphaned_min_age_days', sa.Integer(), nullable=True),
            sa.Column('orphaned_ignore_patterns', sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name')
        )
        op.create_table('rule',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('condition_type', sa.String(length=50), nullable=False),
            sa.Column('condition_value', sa.String(length=255), nullable=False),
            sa.Column('share_limit_ratio', sa.Float(), nullable=True),
            sa.Column('share_limit_time', sa.Integer(), nullable=True),
            sa.Column('max_upload_speed', sa.Integer(), nullable=True),
            sa.Column('max_download_speed', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_table('telegram_message',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('timestamp', sa.DateTime(), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_table('action_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('timestamp', sa.DateTime(), nullable=False),
            sa.Column('instance_id', sa.Integer(), nullable=False),
            sa.Column('action', sa.String(length=255), nullable=False),
            sa.Column('details', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['instance_id'], ['instance.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_table('instance_rules',
            sa.Column('instance_id', sa.Integer(), nullable=False),
            sa.Column('rule_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['instance_id'], ['instance.id'], ),
            sa.ForeignKeyConstraint(['rule_id'], ['rule.id'], ),
            sa.PrimaryKeyConstraint('instance_id', 'rule_id')
        )
    else:
        # Existing database - add new columns to instance table
        existing_columns = [col['name'] for col in inspector.get_columns('instance')]
        
        with op.batch_alter_table('instance', schema=None) as batch_op:
            if 'orphaned_scan_enabled' not in existing_columns:
                batch_op.add_column(sa.Column('orphaned_scan_enabled', sa.Boolean(), nullable=True))
            if 'orphaned_min_age_days' not in existing_columns:
                batch_op.add_column(sa.Column('orphaned_min_age_days', sa.Integer(), nullable=True))
            if 'orphaned_ignore_patterns' not in existing_columns:
                batch_op.add_column(sa.Column('orphaned_ignore_patterns', sa.Text(), nullable=True))
    
    # Create orphaned_file table if it doesn't exist
    if 'orphaned_file' not in existing_tables:
        op.create_table('orphaned_file',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('timestamp', sa.DateTime(), nullable=False),
            sa.Column('instance_id', sa.Integer(), nullable=False),
            sa.Column('file_path', sa.Text(), nullable=False),
            sa.Column('file_size', sa.BigInteger(), nullable=True),
            sa.Column('file_mtime', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['instance_id'], ['instance.id'], ),
            sa.PrimaryKeyConstraint('id')
        )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'orphaned_file' in existing_tables:
        op.drop_table('orphaned_file')
    
    if 'instance' in existing_tables:
        existing_columns = [col['name'] for col in inspector.get_columns('instance')]
        with op.batch_alter_table('instance', schema=None) as batch_op:
            if 'orphaned_ignore_patterns' in existing_columns:
                batch_op.drop_column('orphaned_ignore_patterns')
            if 'orphaned_min_age_days' in existing_columns:
                batch_op.drop_column('orphaned_min_age_days')
            if 'orphaned_scan_enabled' in existing_columns:
                batch_op.drop_column('orphaned_scan_enabled')
