from datetime import date
from decimal import Decimal

import httpx
import pytest

from mtg_market_scraper import cardmarket
from mtg_market_scraper.cardmarket import guide_date, parse_prices, parse_products
from mtg_market_scraper.config import CARDMARKET_TZ


def guide(*entries, created='2026-11-01T02:47:00+0100'):
    return {'version': 1, 'createdAt': created, 'priceGuides': list(entries)}


def test_guide_date_converts_to_berlin():
    assert guide_date(guide(created='2026-11-01T02:47:00+0100')) == date(2026, 11, 1)
    assert guide_date(guide(created='2026-10-31T23:30:00+0000')) == date(2026, 11, 1)


def test_parse_prices_reads_foil_suffix():
    rows = parse_prices(guide({'idProduct': 1, 'trend': 0.07, 'trend-foil': 0.38, 'low-foil': None}), {1})
    assert rows[0].trend == Decimal('0.07')
    assert rows[0].trend_foil == Decimal('0.38')
    assert rows[0].low_foil is None


def test_parse_prices_skips_products_not_in_singles():
    rows = parse_prices(guide({'idProduct': 5, 'trend': 1}, {'idProduct': 6, 'trend': 2}), {5})
    assert [r.id_product for r in rows] == [5]


def test_parse_products():
    cards = parse_products({'products': [
        {'idProduct': 690368, 'name': 'Sheoldred, the Apocalypse', 'idCategory': 1, 'idExpansion': 5229,
         'idMetacard': 415369, 'dateAdded': '2022-12-28 22:11:43'},
        {'idProduct': 7, 'name': 'Bare'},
    ]})
    sheoldred, bare = cards
    assert (sheoldred.id_product, sheoldred.name, sheoldred.id_expansion, sheoldred.id_metacard) == (
        690368, 'Sheoldred, the Apocalypse', 5229, 415369)
    assert sheoldred.date_added.tzinfo == CARDMARKET_TZ and sheoldred.date_added.hour == 22
    assert bare.id_expansion is None and bare.date_added is None


def test_fetch_json_retries_5xx_not_4xx(monkeypatch):
    monkeypatch.setattr(cardmarket.time, 'sleep', lambda s: None)
    calls = []

    def handler(responses):
        def handle(request):
            calls.append(request.url)
            return responses.pop(0)
        return handle

    monkeypatch.setattr(cardmarket, 'TRANSPORT', httpx.MockTransport(handler([httpx.Response(503), httpx.Response(200, json={'ok': 1})])))
    assert cardmarket.fetch_json('https://example.test/a.json') == {'ok': 1}
    assert len(calls) == 2

    calls.clear()
    monkeypatch.setattr(cardmarket, 'TRANSPORT', httpx.MockTransport(handler([httpx.Response(404)])))
    with pytest.raises(httpx.HTTPStatusError):
        cardmarket.fetch_json('https://example.test/b.json')
    assert len(calls) == 1


def test_parse_products_treats_zero_date_as_missing():
    [card] = parse_products({'products': [{'idProduct': 1, 'name': 'Island', 'dateAdded': '0000-00-00 00:00:00'}]})
    assert card.date_added is None
