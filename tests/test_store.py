from datetime import date, datetime, timezone
from decimal import Decimal

from mtg_market_scraper.cardmarket import CardRow, PriceRow
from mtg_market_scraper.store import imported, insert_prices, log_run, upsert_cards
from conftest import sql

DAY = date(2026, 11, 1)


def card(id_product, name='Card', expansion=7):
    return CardRow(id_product=id_product, name=name, id_expansion=expansion, id_metacard=None, date_added=None)


def test_upsert_cards_creates_expansions_and_updates_names(session):
    upsert_cards(session, [card(1, 'Old')])
    upsert_cards(session, [card(1, 'New')])
    assert sql(session, 'SELECT id_product, name FROM card').fetchall() == [(1, 'New')]
    assert sql(session, 'SELECT id_expansion, name FROM expansion').fetchall() == [(7, None)]


def test_insert_prices_is_idempotent(session):
    upsert_cards(session, [card(1), card(2)])
    rows = [PriceRow(1, trend=Decimal('1.50')), PriceRow(2, trend_foil=Decimal('9'))]
    assert insert_prices(session, DAY, rows) == 2
    assert insert_prices(session, DAY, rows) == 0
    assert sql(session, 'SELECT count(*) FROM price').fetchone()[0] == 2
    assert sql(session, 'SELECT trend FROM price WHERE id_product = 1').fetchone()[0] == Decimal('1.50')


def test_imported_needs_an_ok_run_for_that_day(session):
    started = datetime.now(timezone.utc)
    assert not imported(session, DAY)
    log_run(session, 1, started, 'failed', error='boom')
    assert not imported(session, DAY)
    log_run(session, 2, started, 'ok', guide_date=DAY, cards=1, prices=1)
    assert imported(session, DAY)
    assert not imported(session, date(2026, 11, 2))


def test_log_run_survives_rollback(session):
    log_run(session, 1, datetime.now(timezone.utc), 'failed', error='boom')
    session.rollback()
    assert sql(session, 'SELECT status, error, finished_at IS NOT NULL FROM import_run').fetchall() == [('failed', 'boom', True)]
