"""Backfill: the last ~90 days of trend prices from MTGJSON, for the days never imported from Cardmarket.

Cardmarket only publishes today's price guide. MTGJSON keeps the last 90 days of it: its Cardmarket retail
price of a day is that day's price guide trend ("normal") and trend-foil ("foil", and "etched" for etched
foils), keyed by MTGJSON's card uuid. Its identifiers files map each uuid to Cardmarket's idProduct (mcmId).
Only trend and trend_foil are filled; low and the averages stay empty on those days.
"""

import csv
import gzip
import logging
import lzma
import tempfile
from pathlib import Path

import httpx
import ijson
from sqlalchemy import func, select, text

from .cardmarket import HEADERS
from .config import MTGJSON_IDENTIFIER_URLS, MTGJSON_PRICES_URL
from .models import Card, Price
from .rising import compute_rising

logger = logging.getLogger(__name__)

# Where a product has both, a foil price wins over an etched one for trend_foil.
FINISHES = {'normal': (False, 0), 'foil': (True, 0), 'etched': (True, 1)}


def download(url, path):
    """Stream a (large) file to disk."""
    with httpx.Client(timeout=600, follow_redirects=True, headers=HEADERS) as client:
        with client.stream('GET', url) as response:
            response.raise_for_status()
            with open(path, 'wb') as out:
                for chunk in response.iter_bytes(1 << 20):
                    out.write(chunk)


def read_identifiers(paths):
    """Map MTGJSON uuid -> Cardmarket idProduct, from MTGJSON's gzipped identifiers CSV files."""
    mcm = {}
    for path in paths:
        with gzip.open(path, 'rt', newline='') as f:
            for row in csv.DictReader(f):
                if row.get('mcmId'):
                    mcm[row['uuid']] = int(row['mcmId'])
    return mcm


def history_rows(prices_path, mcm, wanted_products, wanted_day):
    """Yield (id_product, day, foil, rank, value) from MTGJSON's AllPrices.json.xz, streamed.

    Only products in wanted_products and days for which wanted_day(day) is true are kept."""
    with lzma.open(prices_path, 'rb') as f:
        for uuid, providers in ijson.kvitems(f, 'data', use_float=True):
            id_product = mcm.get(uuid)
            if id_product not in wanted_products:
                continue
            retail = ((providers.get('paper') or {}).get('cardmarket') or {}).get('retail') or {}
            for finish, series in retail.items():
                if finish not in FINISHES:
                    continue
                foil, rank = FINISHES[finish]
                for day, value in series.items():
                    if value is not None and wanted_day(day):
                        yield id_product, day, foil, rank, value


def store_history(session, rows):
    """Insert one price row per product and day, with trend and trend_foil; returns the rows inserted."""
    session.execute(text(
        'CREATE TEMP TABLE history_in (id_product int, day date, foil bool, rank int, value numeric(10, 2)) '
        'ON COMMIT DROP'))
    driver = session.connection().connection.driver_connection
    with driver.cursor() as cursor, cursor.copy('COPY history_in FROM STDIN') as copy:
        for id_product, day, foil, rank, value in rows:
            copy.write_row((id_product, day, foil, rank, f'{value:.2f}'))
    inserted = session.scalar(text("""
        WITH inserted AS (
            INSERT INTO price (id_product, day, trend, trend_foil)
            SELECT id_product, day,
                   (array_agg(value ORDER BY rank, value) FILTER (WHERE NOT foil))[1],
                   (array_agg(value ORDER BY rank, value) FILTER (WHERE foil))[1]
            FROM history_in
            GROUP BY id_product, day
            ON CONFLICT DO NOTHING
            RETURNING 1
        )
        SELECT count(*) FROM inserted
    """))
    session.commit()
    session.execute(text('ANALYZE price'))
    session.commit()
    return inserted


def backfill(session, today, fetch=download):
    """Fill the days before today that have no prices yet, then recompute rising. Returns the rows inserted.

    Needs the cards of at least one Cardmarket import: prices are only stored for known singles. Today is
    left to the daily sync, so that its Cardmarket prices are never shadowed by a trend-only row."""
    products = set(session.scalars(select(Card.id_product)))
    if not products:
        raise RuntimeError('No cards yet: run the sync once before the backfill')
    stored = {str(d) for d in session.scalars(select(Price.day).distinct())}
    with tempfile.TemporaryDirectory() as workdir:
        identifiers = []
        for url in MTGJSON_IDENTIFIER_URLS:
            path = Path(workdir) / url.rsplit('/', 1)[-1]
            logger.info('Downloading %s', url)
            fetch(url, path)
            identifiers.append(path)
        prices = Path(workdir) / 'AllPrices.json.xz'
        logger.info('Downloading %s', MTGJSON_PRICES_URL)
        fetch(MTGJSON_PRICES_URL, prices)
        mcm = read_identifiers(identifiers)
        rows = history_rows(prices, mcm, products, lambda day: day < str(today) and day not in stored)
        inserted = store_history(session, rows)
    latest = session.scalar(select(func.max(Price.day)))
    if inserted and latest is not None:
        compute_rising(session, latest)
    return inserted
