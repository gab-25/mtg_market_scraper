"""Command line: `python -m tcg_pulse sync` (the daily run) and `python -m tcg_pulse reimport`."""

import argparse
import logging
import sys
from datetime import date, datetime

from .config import CARDMARKET_TZ, load_settings
from .db import connect, migrate
from .sync import reimport, sync


def main(argv=None):
    parser = argparse.ArgumentParser(prog='tcg_pulse')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('sync', help="import today's Magic prices, retrying failures")
    again = commands.add_parser('reimport', help='import an archived day again')
    again.add_argument('--date', required=True, type=date.fromisoformat)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s', stream=sys.stdout)
    settings = load_settings()
    session = connect(settings.database_url)
    migrate(session)

    if args.command == 'sync':
        ok = sync(session, datetime.now(CARDMARKET_TZ).date(), settings.data_dir)
        sys.exit(0 if ok else 1)
    result = reimport(session, args.date, settings.data_dir)
    logging.info('Reimported %d prices for %s', result.prices, result.day)


if __name__ == '__main__':
    main()
