import importlib.util
import uuid
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from conftest import sql
from mtg_market_scraper.db import migrate
from mtg_market_scraper.models import Base


def test_schema_creates_tables(session):
    rows = sql(session, 
        'SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()'
    ).fetchall()
    assert {'expansion', 'card', 'price', 'rising', 'import_run'} <= {r[0] for r in rows}


def test_price_is_hypertable(session):
    row = sql(session, 
        "SELECT 1 FROM timescaledb_information.hypertables "
        "WHERE hypertable_schema = current_schema() AND hypertable_name = 'price'"
    ).fetchone()
    assert row is not None


def test_migrate_is_idempotent(session):
    sql(session, "INSERT INTO card (id_product, name) VALUES (1, 'Island')")
    session.commit()
    migrate(session)
    assert sql(session, 'SELECT name FROM card').fetchall() == [('Island',)]


def test_models_match_migrations(session):
    """A model changed without a migration shows up here (run `alembic revision --autogenerate`)."""
    context = MigrationContext.configure(session.connection(), opts={'compare_type': True})
    assert compare_metadata(context, Base.metadata) == []


def reader_migration():
    [path] = Path(__file__).parent.parent.glob('mtg_market_scraper/migrations/versions/*_let_grafana_reader_read_the_tables.py')
    spec = importlib.util.spec_from_file_location('reader_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def can_select(session, table):
    return sql(session, f"SELECT has_table_privilege('grafana_reader', current_schema() || '.{table}', 'SELECT')").scalar()


def test_reader_can_read_every_table(session):
    for table in ('expansion', 'card', 'price', 'rising', 'import_run'):
        assert can_select(session, table), table
    assert not sql(session, "SELECT has_table_privilege('grafana_reader', current_schema() || '.price', 'INSERT')").scalar()


def test_reader_can_read_tables_added_later(session):
    sql(session, 'CREATE TABLE added_later (id int)')
    assert can_select(session, 'added_later')


def test_reader_role_is_created_with_the_password(session):
    name = f'reader_{uuid.uuid4().hex[:8]}'
    try:
        reader_migration().ensure_role(session.connection(), name, "pa'ss")
        row = sql(session, 'SELECT rolcanlogin, rolsuper FROM pg_roles WHERE rolname = %s', (name,)).fetchone()
        assert row == (True, False)
    finally:
        session.rollback()


def test_reader_role_is_not_created_without_a_password(session):
    name = f'reader_{uuid.uuid4().hex[:8]}'
    reader_migration().ensure_role(session.connection(), name, None)
    assert sql(session, 'SELECT count(*) FROM pg_roles WHERE rolname = %s', (name,)).scalar() == 0


def test_downgrade_takes_the_grants_back(session):
    from alembic import command
    from mtg_market_scraper.db import alembic_config
    config = alembic_config()
    config.attributes['connection'] = session.connection()
    command.downgrade(config, 'cd210b7fd8f7')
    assert not can_select(session, 'price')
    assert sql(session, "SELECT count(*) FROM pg_roles WHERE rolname = 'grafana_reader'").scalar() == 1

