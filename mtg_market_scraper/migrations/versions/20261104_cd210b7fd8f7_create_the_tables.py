"""Create the tables

Revision ID: cd210b7fd8f7
Revises: 
Create Date: 2026-11-04 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = 'cd210b7fd8f7'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS timescaledb')
    op.create_table('card',
    sa.Column('id_product', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('id_expansion', sa.Integer(), nullable=True),
    sa.Column('id_metacard', sa.Integer(), nullable=True),
    sa.Column('date_added', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id_product')
    )
    op.create_table('expansion',
    sa.Column('id_expansion', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id_expansion')
    )
    op.create_table('import_run',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('guide_date', sa.Date(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('cards', sa.Integer(), nullable=True),
    sa.Column('prices', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('ok', 'failed')", name='import_run_status_check'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('import_run_started', 'import_run', ['started_at'], unique=False)
    op.create_table('price',
    sa.Column('id_product', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('trend', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('low', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg1', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg7', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg30', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('trend_foil', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('low_foil', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg_foil', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg1_foil', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg7_foil', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('avg30_foil', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.PrimaryKeyConstraint('id_product', 'day')
    )
    # One row per card per day: partition the prices by day.
    op.execute("SELECT create_hypertable('price', by_range('day'), create_default_indexes => FALSE)")
    op.create_index('price_day_idx', 'price', [sa.text('day DESC')], unique=False)
    op.create_table('rising',
    sa.Column('id_product', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('window_days', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('foil', sa.Boolean(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('gain_pct', sa.Double(), nullable=False),
    sa.Column('slope', sa.Double(), nullable=False),
    sa.Column('r2', sa.Double(), nullable=False),
    sa.Column('coverage', sa.Double(), nullable=False),
    sa.Column('rising_steps', sa.Integer(), nullable=False),
    sa.Column('jump_share', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('id_product', 'window_days', 'foil')
    )


def downgrade():
    op.drop_table('rising')
    op.drop_table('price')
    op.drop_index('import_run_started', table_name='import_run')
    op.drop_table('import_run')
    op.drop_table('expansion')
    op.drop_table('card')
