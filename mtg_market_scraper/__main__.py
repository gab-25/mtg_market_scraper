"""Command line: `python -m mtg_market_scraper sync` (the daily run), `reimport` and `backfill`."""

import argparse
import logging
import sys
from datetime import date, datetime

from .backfill import backfill
from .config import CARDMARKET_TZ, load_settings
from .db import connect, migrate
from .sync import reimport, sync


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mtg_market_scraper')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('sync', help="import today's Magic prices, retrying failures")
    again = commands.add_parser('reimport', help='import an archived day again')
    again.add_argument('--date', required=True, type=date.fromisoformat)
    commands.add_parser('backfill', help="load the last ~90 days of trend prices from MTGJSON (once, after the first sync)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', stream=sys.stdout)
    settings = load_settings()
    session = connect(settings.database_url)
    migrate(session)

    if args.command == 'sync':
        ok = sync(session, datetime.now(CARDMARKET_TZ).date(), settings.data_dir)
        sys.exit(0 if ok else 1)
    if args.command == 'backfill':
        inserted = backfill(session, datetime.now(CARDMARKET_TZ).date())
        logging.info('Backfilled %d prices', inserted)
        return
    result = reimport(session, args.date, settings.data_dir)
    logging.info('Reimported %d prices for %s', result.prices, result.day)


if __name__ == '__main__':
    main()
