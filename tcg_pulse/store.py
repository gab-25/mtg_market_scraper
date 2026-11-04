"""Write cards, daily prices and import runs. Callers own the transaction, except log_run."""

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import column, delete, exists, func, select, table, text
from sqlalchemy.dialects.postgresql import insert

from .models import Card, Expansion, ImportRun, Price

BATCH = 5000


def batches(rows):
    for start in range(0, len(rows), BATCH):
        yield rows[start:start + BATCH]


def upsert_cards(session, cards, update=True):
    """Insert the cards, updating existing ones unless update is False; unknown expansions get a
    row with no name."""
    expansions = sorted({c.id_expansion for c in cards if c.id_expansion is not None})
    if expansions:
        session.execute(
            insert(Expansion).on_conflict_do_nothing(),
            [{'id_expansion': e} for e in expansions],
        )
    rows = list({c.id_product: asdict(c) for c in cards}.values())
    stmt = insert(Card)
    if update:
        stmt = stmt.on_conflict_do_update(
            index_elements=[Card.id_product],
            set_={col: stmt.excluded[col] for col in ('name', 'id_expansion', 'id_metacard', 'date_added')},
        )
    else:
        stmt = stmt.on_conflict_do_nothing()
    for batch in batches(rows):
        session.execute(stmt, batch)
    return len(cards)


PRICE_COLUMNS = [c.name for c in Price.__table__.columns]
price_in = table('price_in', *(column(name) for name in PRICE_COLUMNS))


def insert_prices(session, day, prices):
    """Store the day's prices; rows already present are left alone. Returns the rows inserted.

    ~125k rows a day: they are streamed with COPY into a temporary table on the session's own
    connection (same transaction), then moved into price with one INSERT ... ON CONFLICT DO NOTHING.
    Row-by-row inserts through the ORM took 25-30 s instead of a few."""
    session.execute(text(
        'CREATE TEMP TABLE IF NOT EXISTS price_in (LIKE price INCLUDING DEFAULTS) ON COMMIT DELETE ROWS'))
    session.execute(text('TRUNCATE price_in'))
    driver = session.connection().connection.driver_connection
    with driver.cursor() as cursor, cursor.copy(f'COPY price_in ({", ".join(PRICE_COLUMNS)}) FROM STDIN') as copy:
        for p in prices:
            row = {'day': day, **asdict(p)}
            copy.write_row([row[name] for name in PRICE_COLUMNS])
    # Counted with RETURNING inside the statement: the row count of an INSERT ... SELECT into the
    # hypertable is not reported (-1).
    inserted = (
        insert(Price).from_select(PRICE_COLUMNS, select(price_in)).on_conflict_do_nothing()
        .returning(Price.id_product).cte('inserted')
    )
    return session.scalar(select(func.count()).select_from(inserted))


def delete_prices(session, day):
    session.execute(delete(Price).where(Price.day == day))


def imported(session, day):
    """Whether a sync finished the whole import (prices, rising, names) of that day."""
    return session.scalar(select(exists().where(
        ImportRun.guide_date == day, ImportRun.status == 'ok')))


def log_run(session, attempt, started_at, status, guide_date=None, cards=None, prices=None, error=None):
    """Record one attempt and commit it, so it stays even when the import itself was rolled back."""
    session.add(ImportRun(
        attempt=attempt, started_at=started_at, finished_at=datetime.now(timezone.utc),
        guide_date=guide_date, status=status, cards=cards, prices=prices, error=error,
    ))
    session.commit()
