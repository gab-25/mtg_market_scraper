import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mtg_market_scraper.db import engine_url, migrate

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL', 'postgresql://mtg_market_scraper:mtg_market_scraper@localhost:5432/mtg_market_scraper')


@pytest.fixture(scope='session')
def engine():
    engine = create_engine(engine_url(TEST_DATABASE_URL))
    # Roles are server-wide: make sure grafana_reader exists so every test schema gets its grants.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_reader') THEN "
            "CREATE ROLE grafana_reader LOGIN PASSWORD 'test'; END IF; END $$")
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """A session whose search_path is a fresh, throwaway schema, migrated with Alembic."""
    schema = f't_{uuid.uuid4().hex}'
    connection = engine.connect()
    connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
    connection.exec_driver_sql(f'SET search_path TO {schema}, public')
    connection.commit()
    session = Session(bind=connection)
    migrate(session)
    try:
        yield session
    finally:
        session.close()
        connection.rollback()
        connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        connection.commit()
        connection.close()


def sql(session, query, params=None):
    """Run plain SQL (psycopg %s placeholders) on the session's connection."""
    if params is None:  # the driver still parses placeholders: keep a literal % as is
        query, params = query.replace('%', '%%'), ()
    return session.connection().exec_driver_sql(query, params)
