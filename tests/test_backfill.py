import csv
import gzip
import io
import json
import lzma
from datetime import date, timedelta
from decimal import Decimal

import pytest

from mtg_market_scraper.backfill import backfill, history_rows, read_identifiers
from mtg_market_scraper.cardmarket import CardRow, PriceRow
from mtg_market_scraper.config import MTGJSON_IDENTIFIER_URLS, MTGJSON_PRICES_URL
from mtg_market_scraper.store import insert_prices, upsert_cards
from conftest import sql

TODAY = date(2026, 11, 4)


def identifiers_csv(rows):
    """rows: (uuid, mcmId) -> gzipped CSV bytes with MTGJSON's columns (only the ones read here)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['uuid', 'scryfallId', 'mcmId', 'mcmMetaId'])
    for uuid, mcm_id in rows:
        writer.writerow([uuid, 'x', mcm_id, ''])
    return gzip.compress(out.getvalue().encode())


def all_prices(cards):
    """cards: uuid -> {finish: {day: value}} -> xz-compressed AllPrices.json bytes."""
    data = {uuid: {'paper': {
        'cardmarket': {'buylist': {}, 'retail': finishes, 'currency': 'EUR'},
        'tcgplayer': {'buylist': {}, 'retail': {'normal': {'2026-11-01': 999}}, 'currency': 'USD'},
    }} for uuid, finishes in cards.items()}
    return lzma.compress(json.dumps({'meta': {'date': '2026-11-03'}, 'data': data}).encode())


def ramp(start, days, step):
    """{day: price} over `days` days ending yesterday, rising by `step` a day."""
    return {str(TODAY - timedelta(days=days - i)): start + i * step for i in range(days)}


class FakeMtgjson:
    def __init__(self, identifiers, prices):
        self.files = {
            MTGJSON_IDENTIFIER_URLS[0]: identifiers_csv(identifiers),
            MTGJSON_IDENTIFIER_URLS[1]: identifiers_csv([]),
            MTGJSON_PRICES_URL: all_prices(prices),
        }
        self.calls = []

    def __call__(self, url, path):
        self.calls.append(url)
        path.write_bytes(self.files[url])


def test_read_identifiers(tmp_path):
    path = tmp_path / 'ids.csv.gz'
    path.write_bytes(identifiers_csv([('u1', '10'), ('u2', '')]))
    assert read_identifiers([path]) == {'u1': 10}


def test_history_rows_keeps_cardmarket_finishes_of_wanted_products_and_days(tmp_path):
    path = tmp_path / 'AllPrices.json.xz'
    path.write_bytes(all_prices({
        'u1': {'normal': {'2026-11-01': 1.5, '2026-11-02': 1.6}, 'foil': {'2026-11-01': 3.0},
               'etched': {'2026-11-01': 4.0}, 'other': {'2026-11-01': 9.0}},
        'u2': {'normal': {'2026-11-01': 7.0}},  # product not wanted
        'u3': {'normal': {'2026-11-01': 7.0}},  # no mcmId
    }))
    rows = list(history_rows(path, {'u1': 10, 'u2': 20}, {10}, lambda day: day != '2026-11-02'))
    assert sorted(rows) == [(10, '2026-11-01', False, 0, 1.5), (10, '2026-11-01', True, 0, 3.0),
                            (10, '2026-11-01', True, 1, 4.0)]


def test_backfill_fills_missing_days_and_computes_rising(session):
    upsert_cards(session, [CardRow(1, 'Rising', None, None, None), CardRow(2, 'Etched', None, None, None)])
    imported_day = TODAY - timedelta(days=2)
    insert_prices(session, imported_day, [PriceRow(1, trend=Decimal('99'), low=Decimal('98'))])
    session.commit()
    fake = FakeMtgjson(
        [('u1', '1'), ('u1-foil', '1'), ('u2', '2'), ('u9', '9')],
        {
            'u1': {'normal': {**ramp(10, 56, 0.1), str(TODAY): 50.0}},  # today is left to the sync
            'u1-foil': {'foil': {str(TODAY - timedelta(days=1)): 30.0}},  # another uuid, same product
            'u2': {'etched': {str(TODAY - timedelta(days=1)): 5.0}},
            'u9': {'normal': ramp(10, 3, 1)},  # not a known card
        })

    inserted = backfill(session, TODAY, fetch=fake)

    assert inserted == 56  # card 1 on 55 days (one already imported) + card 2 on one day
    assert sql(session, 'SELECT count(*) FROM price WHERE day = %s', (TODAY,)).scalar() == 0
    assert sql(session, 'SELECT count(*) FROM price WHERE id_product = 9').scalar() == 0
    assert sql(session, 'SELECT trend, low FROM price WHERE day = %s', (imported_day,)).fetchone() == (
        Decimal('99.00'), Decimal('98.00'))
    yesterday = sql(session, 'SELECT id_product, trend, trend_foil, low FROM price WHERE day = %s ORDER BY 1',
                    (TODAY - timedelta(days=1),)).fetchall()
    assert yesterday == [(1, Decimal('15.50'), Decimal('30.00'), None), (2, None, Decimal('5.00'), None)]
    assert sql(session, 'SELECT count(*) FROM rising WHERE id_product = 1 AND NOT foil').scalar() > 0

    assert backfill(session, TODAY, fetch=fake) == 0


def test_backfill_prefers_foil_over_etched(session):
    upsert_cards(session, [CardRow(1, 'Card', None, None, None)])
    session.commit()
    day = str(TODAY - timedelta(days=1))
    fake = FakeMtgjson([('a', '1'), ('b', '1')], {'a': {'etched': {day: 8.0}}, 'b': {'foil': {day: 6.0}}})
    backfill(session, TODAY, fetch=fake)
    assert sql(session, 'SELECT trend_foil FROM price').scalar() == Decimal('6.00')


def test_backfill_needs_a_sync_first(session):
    with pytest.raises(RuntimeError, match='run the sync once'):
        backfill(session, TODAY, fetch=FakeMtgjson([], {}))
