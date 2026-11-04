"""The database tables, as SQLAlchemy models. Changing them needs an Alembic migration."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Numeric, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Money = Numeric(10, 2)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Expansion(Base):
    __tablename__ = 'expansion'

    id_expansion: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(Text)


class Card(Base):
    __tablename__ = 'card'

    id_product: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(Text)
    id_expansion: Mapped[int | None]
    id_metacard: Mapped[int | None]
    date_added: Mapped[datetime | None]


class Price(Base):
    """One card's prices on one day; a TimescaleDB hypertable partitioned on day."""

    __tablename__ = 'price'
    __table_args__ = (Index('price_day_idx', text('day DESC')),)

    id_product: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    day: Mapped[date] = mapped_column(primary_key=True)
    trend: Mapped[Decimal | None] = mapped_column(Money)
    low: Mapped[Decimal | None] = mapped_column(Money)
    avg: Mapped[Decimal | None] = mapped_column(Money)
    avg1: Mapped[Decimal | None] = mapped_column(Money)
    avg7: Mapped[Decimal | None] = mapped_column(Money)
    avg30: Mapped[Decimal | None] = mapped_column(Money)
    trend_foil: Mapped[Decimal | None] = mapped_column(Money)
    low_foil: Mapped[Decimal | None] = mapped_column(Money)
    avg_foil: Mapped[Decimal | None] = mapped_column(Money)
    avg1_foil: Mapped[Decimal | None] = mapped_column(Money)
    avg7_foil: Mapped[Decimal | None] = mapped_column(Money)
    avg30_foil: Mapped[Decimal | None] = mapped_column(Money)


class Rising(Base):
    """The latest rising measures of the cards; thresholds are applied by the dashboard."""

    __tablename__ = 'rising'

    id_product: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    window_days: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    foil: Mapped[bool] = mapped_column(primary_key=True)
    as_of: Mapped[date]
    price: Mapped[Decimal] = mapped_column(Money)
    gain_pct: Mapped[float]
    slope: Mapped[float]
    r2: Mapped[float]
    coverage: Mapped[float]
    rising_steps: Mapped[int]
    jump_share: Mapped[float]


class ImportRun(Base):
    """One attempt at importing a day."""

    __tablename__ = 'import_run'
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'failed')", name='import_run_status_check'),
        Index('import_run_started', 'started_at'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attempt: Mapped[int]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    guide_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(Text)
    cards: Mapped[int | None]
    prices: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
