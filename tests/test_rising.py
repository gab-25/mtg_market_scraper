from datetime import date, timedelta
from decimal import Decimal

import pytest

from tcg_pulse.cardmarket import CardRow, PriceRow
from tcg_pulse.config import RISING
from tcg_pulse.rising import compute_rising, evaluate, measure, passes
from tcg_pulse.store import insert_prices, upsert_cards
from conftest import sql

AS_OF = date(2026, 10, 19)


def ramp(start, end, days):
    """Linear series of `days` points ending on AS_OF."""
    step = (end - start) / (days - 1)
    return {
        AS_OF - timedelta(days=days - 1 - i): Decimal(str(round(start + step * i, 2)))
        for i in range(days)
    }


def flat(value, days):
    return ramp(value, value, days)


def drop_days(series, n):
    """Remove n days from the middle of the series (never AS_OF)."""
    days = sorted(series)[1:-1:2][:n]
    return {d: v for d, v in series.items() if d not in days}


def test_steady_rise_qualifies():
    m = evaluate(ramp(10, 13, 28), AS_OF, 28)
    assert m and m.gain_pct == pytest.approx(0.30) and m.r2 > 0.99
    assert m.price == Decimal("13") and m.slope > 0


def test_price_exactly_five_qualifies():
    assert evaluate(ramp(4, 5, 28), AS_OF, 28) is not None


def test_below_floor_rejected():
    assert evaluate(ramp(3, 4.99, 28), AS_OF, 28) is None


def test_single_day_spike_rejected():
    s = flat(10, 28)
    s[AS_OF] = Decimal("14")
    assert evaluate(s, AS_OF, 28) is None


def test_small_gain_rejected():
    assert evaluate(ramp(10, 11, 28), AS_OF, 28) is None


def test_low_coverage_rejected():
    assert evaluate(drop_days(ramp(10, 13, 28), 8), AS_OF, 28) is None


def test_no_price_on_as_of_rejected():
    s = ramp(10, 13, 28)
    s[AS_OF] = None
    assert evaluate(s, AS_OF, 28) is None


def test_falling_then_recovering_rejected():
    # Down for three weeks, sharp-ish recovery in the last: buckets mostly decreasing.
    s = {**ramp(20, 12, 28)}
    for i, d in enumerate(sorted(s)[-7:]):
        s[d] = Decimal(12 + 2 * i) if i else s[d]
    assert evaluate(s, AS_OF, 28) is None


def test_nulls_ignored_and_14_day_window_works():
    s = ramp(10, 13, 14)
    s[AS_OF - timedelta(days=3)] = None
    assert evaluate(s, AS_OF, 14) is not None


def test_measure_reports_metrics_without_applying_thresholds():
    m = measure(ramp(10, 11, 28), AS_OF, 28)  # +10%: below the default 15% but still measured
    assert m.gain_pct == pytest.approx(0.10) and m.coverage == 1.0 and m.rising_steps == 3
    assert 0 < m.jump_share < 0.1
    assert not passes(m, RISING)
    assert passes(m, {**RISING, "MIN_GAIN": 0.05})


def test_measure_rejects_falling_cards_and_prices_below_measure_floor():
    assert measure(ramp(13, 10, 28), AS_OF, 28) is None
    assert measure(ramp(0.5, 0.99, 28), AS_OF, 28) is None
    assert measure(ramp(1, 2, 28), AS_OF, 28) is not None


def test_spike_is_measured_as_high_jump_share():
    s = flat(10, 28)
    s[AS_OF] = Decimal("14")
    m = measure(s, AS_OF, 28)
    assert m.jump_share == pytest.approx(1.0)
    assert passes(m, {**RISING, "MAX_JUMP_SHARE": 1.0})


def test_min_r2_threshold():
    m = measure(ramp(10, 13, 28), AS_OF, 28)
    assert passes(m, {**RISING, "MIN_R2": 0.99}) and not passes(m, {**RISING, "MIN_R2": 1.01})


def test_zero_first_price_does_not_crash():
    s = ramp(10, 13, 28)
    s[min(s)] = Decimal("0.00")
    assert evaluate(s, AS_OF, 28) is None or evaluate(s, AS_OF, 28).gain_pct > 0


def seed(session, id_product, series, field='trend'):
    upsert_cards(session, [CardRow(id_product, f'Card {id_product}', None, None, None)])
    for day, value in series.items():
        insert_prices(session, day, [PriceRow(id_product, **{field: value})])


def test_compute_rising_stores_rows_per_window(session):
    seed(session, 1, ramp(10, 13, 56))
    seed(session, 2, ramp(0.1, 0.5, 56))  # below the measure floor
    assert compute_rising(session, AS_OF) == 3
    rows = sql(session, 'SELECT id_product, window_days, foil, as_of FROM rising ORDER BY window_days').fetchall()
    assert rows == [(1, 14, False, AS_OF), (1, 28, False, AS_OF), (1, 56, False, AS_OF)]
    compute_rising(session, AS_OF)
    assert sql(session, 'SELECT count(*) FROM rising').fetchone()[0] == 3


def test_compute_rising_foil(session):
    seed(session, 1, ramp(10, 13, 56), field='trend_foil')
    compute_rising(session, AS_OF)
    assert {r[0] for r in sql(session, 'SELECT foil FROM rising').fetchall()} == {True}
