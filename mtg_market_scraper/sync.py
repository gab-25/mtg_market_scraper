"""One daily sync: Magic's price guide into the database, with retries and a file archive."""

import gzip
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, text

from .cardmarket import fetch_json, guide_date, parse_prices, parse_products
from .config import ATTEMPTS, NONSINGLES_URL, PRICE_GUIDE_URL, RETRY_SECONDS, SINGLES_URL
from .expansions import refresh_expansions
from .rising import compute_rising
from .models import Price
from .store import delete_prices, imported, insert_prices, log_run, upsert_cards

logger = logging.getLogger(__name__)


class GuideNotReady(Exception):
    """Today's price guide has not been published yet."""


@dataclass
class ImportResult:
    day: date
    cards: int
    prices: int


def archive_path(data_dir, day, kind):
    return Path(data_dir) / f'{day:%Y-%m-%d}' / f'{kind}.json.gz'


def archive(data_dir, day, kind, payload):
    path = archive_path(data_dir, day, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
    return path


def read_archive(data_dir, day, kind):
    return json.loads(gzip.decompress(archive_path(data_dir, day, kind).read_bytes()))


def store(session, guide, products, replace=False, update_cards=True):
    """Write one day's cards and prices in a single transaction; replace drops that day's prices first."""
    day = guide_date(guide)
    cards = parse_products(products)
    prices = parse_prices(guide, {c.id_product for c in cards})
    upsert_cards(session, cards, update=update_cards)
    if replace:
        delete_prices(session, day)
    inserted = insert_prices(session, day, prices)
    session.commit()
    # Fresh statistics: without them the rising query that follows can pick a plan that never ends.
    session.execute(text('ANALYZE price'))
    session.commit()
    return ImportResult(day=day, cards=len(cards), prices=inserted)


def refresh_names(session, day, data_dir, load):
    """Best effort: a failure is logged and never fails the import."""
    try:
        nonsingles = load()
        archive(data_dir, day, 'products_nonsingles', nonsingles)
        refresh_expansions(session, nonsingles)
    except Exception:
        session.rollback()
        logger.exception('Refreshing expansion names failed')


def import_day(session, today, data_dir, fetch=fetch_json):
    guide = fetch(PRICE_GUIDE_URL)
    if guide_date(guide) != today:
        raise GuideNotReady(f'price guide is dated {guide_date(guide)}, expected {today}')
    products = fetch(SINGLES_URL)
    archive(data_dir, today, 'price_guide', guide)
    archive(data_dir, today, 'products_singles', products)
    result = store(session, guide, products)
    compute_rising(session, today)
    refresh_names(session, today, data_dir, lambda: fetch(NONSINGLES_URL))
    return result


def sync(session, today, data_dir, fetch=fetch_json, sleep=time.sleep):
    """Import today's prices unless already fully imported. True when today is done."""
    done = imported(session, today)
    session.commit()
    if done:
        return True
    for attempt in range(1, ATTEMPTS + 1):
        started = datetime.now(timezone.utc)
        try:
            result = import_day(session, today, data_dir, fetch)
        except Exception as exc:
            session.rollback()
            logger.warning('Attempt %d/%d failed: %s', attempt, ATTEMPTS, exc)
            log_run(session, attempt, started, 'failed', error=f'{type(exc).__name__}: {exc}')
        else:
            logger.info('%d cards, %d prices for %s', result.cards, result.prices, result.day)
            log_run(session, attempt, started, 'ok', guide_date=result.day, cards=result.cards, prices=result.prices)
            return True
        if attempt < ATTEMPTS:
            sleep(RETRY_SECONDS)
    logger.error('Giving up on %s', today)
    return False


def reimport(session, day, data_dir):
    """Import an archived day again, replacing that day's prices, without downloading anything.

    Card names are only updated, and rising only recomputed, when the day is the latest one stored."""
    latest = session.scalar(select(func.max(Price.day)))
    is_latest = latest is None or day >= latest
    result = store(session, read_archive(data_dir, day, 'price_guide'),
                   read_archive(data_dir, day, 'products_singles'), replace=True, update_cards=is_latest)
    if is_latest:
        compute_rising(session, day)
    if is_latest and archive_path(data_dir, day, 'products_nonsingles').exists():
        refresh_names(session, day, data_dir, lambda: read_archive(data_dir, day, 'products_nonsingles'))
    return result
