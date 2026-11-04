"""Cardmarket URLs for Magic: The Gathering, rising thresholds and runtime settings."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Cardmarket (Berlin) dates its files in its own local time: a guide's day and "today" are taken in it.
CARDMARKET_TZ = ZoneInfo('Europe/Berlin')

# Cardmarket's public files for Magic: The Gathering (its game id is 1).
PRICE_GUIDE_URL = 'https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json'
SINGLES_URL = 'https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_1.json'
NONSINGLES_URL = 'https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_1.json'

# MTGJSON keeps the last 90 days of Cardmarket's price guide trend; its identifiers map cards to idProduct.
MTGJSON_PRICES_URL = 'https://mtgjson.com/api/v5/AllPrices.json.xz'
MTGJSON_IDENTIFIER_URLS = (
    'https://mtgjson.com/api/v5/csv/cardIdentifiers.csv.gz',
    'https://mtgjson.com/api/v5/csv/tokenIdentifiers.csv.gz',
)


# Rising measures: a card is measured when priced >= MEASURE_MIN_PRICE; the other values are the
# default thresholds the Rising dashboard applies.
RISING = {
    'MEASURE_MIN_PRICE': '1.00',
    'MIN_PRICE': '5.00',
    'WINDOWS': [14, 28, 56],
    'MIN_GAIN': 0.15,
    'MIN_R2': 0.0,
    'MIN_COVERAGE': 0.8,
    'BUCKETS': 4,
    'MIN_RISING_STEPS': 2,
    'MAX_JUMP_SHARE': 0.5,
}

# A sync tries at most ATTEMPTS times, RETRY_SECONDS apart.
ATTEMPTS = 4
RETRY_SECONDS = 900


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path


def load_settings():
    return Settings(database_url=os.environ['DATABASE_URL'], data_dir=Path(os.environ.get('DATA_DIR', 'data')))
