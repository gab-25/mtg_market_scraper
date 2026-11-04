"""Engine, sessions and Alembic migrations."""

from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def engine_url(url):
    """postgres:// and postgresql:// URLs use the psycopg 3 driver."""
    for scheme in ('postgres://', 'postgresql://'):
        if url.startswith(scheme):
            return 'postgresql+psycopg://' + url[len(scheme):]
    return url


def connect(url):
    """A session on its own connection; the caller commits."""
    return Session(create_engine(engine_url(url)))


def alembic_config():
    config = Config()
    config.set_main_option('script_location', str(files('mtg_market_scraper') / 'migrations'))
    return config


def migrate(session):
    """Bring the schema, and the read-only grafana_reader role, up to date (idempotent)."""
    config = alembic_config()
    config.attributes['connection'] = session.connection()
    command.upgrade(config, 'head')
    session.commit()

