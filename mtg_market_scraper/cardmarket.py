"""Download and parse Cardmarket's public price guides and product lists."""

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx

from .config import CARDMARKET_TZ

logger = logging.getLogger(__name__)

FETCH_ATTEMPTS = 3
HEADERS = {'User-Agent': 'MTGMarketScraper/0.1', 'Accept': 'application/json;q=0.9,*/*;q=0.8'}
TRANSPORT = None  # replaced in tests

PRICE_KEYS = ('trend', 'low', 'avg', 'avg1', 'avg7', 'avg30')


def fetch_bytes(url):
    """GET a file; transport errors and 5xx are retried with backoff, 4xx raise at once."""
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with httpx.Client(transport=TRANSPORT, timeout=300, follow_redirects=True, headers=HEADERS) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.content
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code >= 500
            if not retryable or attempt == FETCH_ATTEMPTS - 1:
                raise
            logger.warning('Fetching %s failed (%s), retrying', url, exc)
            time.sleep(5 * 2**attempt)


def fetch_json(url):
    return json.loads(fetch_bytes(url))


def guide_date(guide):
    created = datetime.strptime(guide['createdAt'], '%Y-%m-%dT%H:%M:%S%z')
    return created.astimezone(CARDMARKET_TZ).date()


@dataclass
class CardRow:
    id_product: int
    name: str
    id_expansion: int | None
    id_metacard: int | None
    date_added: datetime | None


@dataclass
class PriceRow:
    id_product: int
    trend: Decimal | None = None
    low: Decimal | None = None
    avg: Decimal | None = None
    avg1: Decimal | None = None
    avg7: Decimal | None = None
    avg30: Decimal | None = None
    trend_foil: Decimal | None = None
    low_foil: Decimal | None = None
    avg_foil: Decimal | None = None
    avg1_foil: Decimal | None = None
    avg7_foil: Decimal | None = None
    avg30_foil: Decimal | None = None


def to_decimal(value):
    return None if value is None else Decimal(str(value))


def parse_date_added(value):
    """Cardmarket's dateAdded; missing or unparseable values (e.g. '0000-00-00 00:00:00') become None."""
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=CARDMARKET_TZ)
    except (TypeError, ValueError):
        return None


def parse_products(products):
    return [
        CardRow(
            id_product=p['idProduct'],
            name=p['name'],
            id_expansion=p.get('idExpansion'),
            id_metacard=p.get('idMetacard'),
            date_added=parse_date_added(p.get('dateAdded')),
        )
        for p in products['products']
    ]


def parse_prices(guide, single_ids):
    """Price rows for the singles in the guide; the -foil keys go to the *_foil fields."""
    rows = []
    for entry in guide['priceGuides']:
        if entry['idProduct'] not in single_ids:
            continue
        values = {k: to_decimal(entry.get(k)) for k in PRICE_KEYS}
        values |= {f'{k}_foil': to_decimal(entry.get(k + '-foil')) for k in PRICE_KEYS}
        rows.append(PriceRow(id_product=entry['idProduct'], **values))
    return rows
