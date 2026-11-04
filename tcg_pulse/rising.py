"""Measure how steadily each card's price rises; the Rising dashboard applies the thresholds."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, insert, select

from .config import RISING
from .models import Price, Rising


@dataclass
class RisingMetrics:
    price: Decimal
    gain_pct: float
    slope: float
    r2: float
    coverage: float
    rising_steps: int
    jump_share: float


def linear_fit(xs, ys):
    """Least-squares slope and R² of ys over xs."""
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    syy = sum((y - mean_y) ** 2 for y in ys)
    slope = sxy / sxx if sxx else 0.0
    r2 = (sxy * sxy) / (sxx * syy) if sxx and syy else 0.0
    return slope, r2


def measure(series, as_of, window_days):
    """Metrics of the window ending on as_of, or None if the card is not rising at all.

    Only the base requirements are applied here (a price on as_of above the measure floor, a first
    price in the window above the start floor, a positive gain and slope, every bucket populated and the last one above the first); user thresholds are
    applied afterwards with `passes` or as a database filter. `series` maps date -> price (None when
    missing); dates outside the window are ignored.
    """
    rules = RISING
    start = as_of - timedelta(days=window_days - 1)
    points = sorted(
        ((d - start).days, float(v)) for d, v in series.items() if v is not None and v > 0 and start <= d <= as_of
    )
    latest = series.get(as_of)
    if latest is None or latest < Decimal(rules['MEASURE_MIN_PRICE']) or len(points) < 2:
        return None

    first, last = points[0][1], points[-1][1]
    if first < float(rules['MIN_START_PRICE']):
        return None
    gain = (last - first) / first
    if gain <= 0:
        return None

    xs, ys = [p[0] for p in points], [p[1] for p in points]
    slope, r2 = linear_fit(xs, ys)
    if slope <= 0:
        return None

    buckets = defaultdict(list)
    for i, value in points:
        buckets[i * rules['BUCKETS'] // window_days].append(value)
    if len(buckets) < rules['BUCKETS']:
        return None
    averages = [sum(buckets[k]) / len(buckets[k]) for k in range(rules['BUCKETS'])]
    if averages[-1] <= averages[0]:
        return None

    max_jump = max((b - a for a, b in zip(ys, ys[1:])), default=0)
    return RisingMetrics(
        price=latest,
        gain_pct=gain,
        slope=slope,
        r2=r2,
        coverage=len(points) / window_days,
        rising_steps=sum(1 for a, b in zip(averages, averages[1:]) if b >= a),
        jump_share=max(max_jump, 0) / (last - first),
    )


def passes(metrics, thresholds):
    """Whether measured metrics meet thresholds (keys as in RISING)."""
    return (
        metrics.price >= Decimal(thresholds['MIN_PRICE'])
        and metrics.gain_pct >= thresholds['MIN_GAIN']
        and metrics.r2 >= thresholds['MIN_R2']
        and metrics.coverage >= thresholds['MIN_COVERAGE']
        and metrics.rising_steps >= thresholds['MIN_RISING_STEPS']
        and metrics.jump_share <= thresholds['MAX_JUMP_SHARE']
    )


def evaluate(series, as_of, window_days):
    """Metrics if the series is rising under the default thresholds, else None."""
    metrics = measure(series, as_of, window_days)
    return metrics if metrics and passes(metrics, RISING) else None


def card_series(session, as_of, field, days):
    """Yield (id_product, {day: price}) for the cards priced above the measure floor on as_of."""
    column = getattr(Price, field)
    floor = Decimal(RISING['MEASURE_MIN_PRICE'])
    candidates = select(Price.id_product).where(Price.day == as_of, column >= floor)
    query = (
        select(Price.id_product, Price.day, column)
        .where(Price.day.between(as_of - timedelta(days=days - 1), as_of),
               Price.id_product.in_(candidates))
        .order_by(Price.id_product, Price.day)
        .execution_options(yield_per=20000)
    )
    current, series = None, {}
    for id_product, day, value in session.execute(query):
        if id_product != current:
            if current is not None:
                yield current, series
            current, series = id_product, {}
        series[day] = value
    if current is not None:
        yield current, series


def compute_rising(session, as_of):
    """Replace the stored measures with those for as_of, across all windows, normal and foil."""
    windows = RISING['WINDOWS']
    rows = []
    for foil in (False, True):
        for id_product, series in card_series(session, as_of, 'trend_foil' if foil else 'trend', max(windows)):
            for window in windows:
                metrics = measure(series, as_of, window)
                if metrics:
                    rows.append({'id_product': id_product, 'window_days': window, 'foil': foil,
                                 'as_of': as_of, **asdict(metrics)})
    session.execute(delete(Rising))
    if rows:
        session.execute(insert(Rising), rows)
    session.commit()
    return len(rows)
