"""The provisioned dashboards: every SQL query runs against the real schema and returns what the panel needs."""

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mtg_market_scraper.cardmarket import CardRow, PriceRow
from mtg_market_scraper.store import insert_prices, upsert_cards
from conftest import sql

DASHBOARDS = Path(__file__).parent.parent / 'grafana' / 'dashboards'
RISING_DEFAULTS = {'window': '28', 'foil': 'false', 'min_price': '5', 'min_gain': '15'}
AS_OF = date(2026, 11, 1)


def load(name):
    return json.loads((DASHBOARDS / name).read_text())


def panel(dashboard, title):
    return next(p for p in dashboard['panels'] if p.get('title') == title)


def panel_queries(dashboard):
    """(title, rawSql) of every panel target and every query variable."""
    queries = [(p['title'], t['rawSql']) for p in dashboard['panels'] for t in p.get('targets', []) if 'rawSql' in t]
    for var in dashboard['templating']['list']:
        if var['type'] == 'query':
            query = var['query']
            queries.append((f"var {var['name']}", query['rawSql'] if isinstance(query, dict) else query))
    return queries


def sql_string(value):
    return "'" + value.replace("'", "''") + "'"


def render(query, **variables):
    """Substitute Grafana variables and macros the way Grafana would for the postgres datasource."""
    def var(match):
        name, fmt = match.group(1) or match.group(3), match.group(2)
        value = variables[name]
        return sql_string(value) if fmt == 'sqlstring' else value

    query = re.sub(r'\$__timeFilter\((\w+(?:\.\w+)?)\)', r"\1 BETWEEN '2026-01-01' AND '2026-12-31'", query)
    query = query.replace('$__timeFrom()', "'2026-01-01'").replace('$__timeTo()', "'2026-12-31'")
    return re.sub(r'\$\{(\w+)(?::(\w+))?\}|\$(\w+)', var, query)


def run_panel(session, dashboard, title, ref_id='A', **variables):
    [query] = [t['rawSql'] for t in panel(dashboard, title)['targets'] if t['refId'] == ref_id]
    cur = sql(session, render(query, **variables))
    return list(cur.keys()), cur.fetchall()


def rising_row(session, id_product, gain=0.30, price='13', jump=0.1, window=28, foil=False):
    sql(session, 
        'INSERT INTO rising (id_product, window_days, foil, as_of, price, gain_pct, slope, r2, coverage, '
        'rising_steps, jump_share) VALUES (%s, %s, %s, %s, %s, %s, 0.1, 0.9, 1.0, 3, %s)',
        (id_product, window, foil, AS_OF, Decimal(price), gain, jump),
    )


def seed_cards(session):
    upsert_cards(session, [
        CardRow(1, 'Rising Star', 6286, None, None),
        CardRow(2, 'Small Gain', 6286, None, None),
        CardRow(3, 'Too Cheap', 6286, None, None),
        CardRow(4, 'Spiky', 6286, None, None),
        CardRow(5, 'Unnamed Set', 777, None, None),
    ])
    sql(session, "UPDATE expansion SET name = 'Origins' WHERE id_expansion = 6286")


@pytest.mark.parametrize('name', ['rising.json', 'card-detail.json', 'imports.json'])
def test_queries_run_on_empty_db(session, name):
    dashboard = load(name)
    variables = {**RISING_DEFAULTS, 'search': '', 'card': '0'}
    for title, query in panel_queries(dashboard):
        sql(session, render(query, **variables)).fetchall()
    if name == 'rising.json':
        _, rows = run_panel(session, dashboard, 'Rising cards', **RISING_DEFAULTS)
        assert rows == []


def test_rising_empty_message():
    table = panel(load('rising.json'), 'Rising cards')
    assert table['fieldConfig']['defaults']['noValue'] == \
        'No rising cards: the ranking needs at least 14 days of price history.'


def test_rising_table_applies_thresholds(session):
    seed_cards(session)
    rising_row(session, 1, gain=0.30)
    rising_row(session, 2, gain=0.10)
    rising_row(session, 3, price='4')
    rising_row(session, 4, jump=0.6)
    rising_row(session, 5, gain=0.50)
    rising_row(session, 1, gain=0.30, window=14)
    rising_row(session, 1, gain=0.30, foil=True)
    columns, rows = run_panel(session, load('rising.json'), 'Rising cards', **RISING_DEFAULTS)
    by_name = [dict(zip(columns, r)) for r in rows]
    assert [r['Card'] for r in by_name] == ['Unnamed Set', 'Rising Star']
    assert [r['#'] for r in by_name] == [1, 2]
    assert by_name[0]['Expansion'] == '#777' and by_name[1]['Expansion'] == 'Origins'
    assert by_name[1]['id_product'] == '1' and by_name[1]['Gain'] == pytest.approx(0.30)
    _, count = run_panel(session, load('rising.json'), 'Rising now', **RISING_DEFAULTS)
    assert count == [(2,)]


def trend_points(session, **variables):
    """{id_product: [(time, price), ...]} from the query that draws the Trend sparklines."""
    columns, rows = run_panel(session, load('rising.json'), 'Rising cards', ref_id='B', **variables)
    assert columns == ['time', 'id_product', 'Trend']
    points = {}
    for time, id_product, value in rows:
        points.setdefault(id_product, []).append((time, value))
    return points


def test_rising_sparkline_follows_the_window(session):
    seed_cards(session)
    for i in range(60):
        day = AS_OF - timedelta(days=59 - i)
        insert_prices(session, day, [PriceRow(1, trend=Decimal(10 + i)), PriceRow(2, trend=Decimal(5))])
    rising_row(session, 1)
    rising_row(session, 2, gain=0.10)  # under the min gain: not in the table, so no line either
    points = trend_points(session, **RISING_DEFAULTS)
    assert list(points) == ['1']
    line = points['1']
    assert len(line) == 28 and [t for t, _ in line] == sorted(t for t, _ in line)
    assert line[0] == (datetime(2026, 10, 5), Decimal('42.00')) and line[-1] == (datetime(2026, 11, 1), Decimal('69.00'))


def test_rising_table_and_sparkline_join_on_the_product(session):
    seed_cards(session)
    insert_prices(session, AS_OF, [PriceRow(1, trend=Decimal(13))])
    rising_row(session, 1)
    columns, rows = run_panel(session, load('rising.json'), 'Rising cards', **RISING_DEFAULTS)
    assert 'Trend' not in columns and dict(zip(columns, rows[0]))['id_product'] == '1'
    assert list(trend_points(session, **RISING_DEFAULTS)) == ['1']
    table = panel(load('rising.json'), 'Rising cards')
    ids = [t['id'] for t in table['transformations']]
    assert ids[:2] == ['timeSeriesTable', 'joinByField']
    assert table['transformations'][1]['options']['byField'] == 'id_product'
    # Options naming the query (e.g. {'B': {...}}) leave Grafana 13 without the Trend column.
    assert table['transformations'][0]['options'] == {}
    assert table['transformations'][1]['options']['mode'] == 'outer'


def test_tracked_history_and_last_import(session):
    seed_cards(session)
    insert_prices(session, AS_OF - timedelta(days=1), [PriceRow(1, trend=Decimal(1))])
    insert_prices(session, AS_OF, [PriceRow(1, trend=Decimal(1)), PriceRow(2, trend=Decimal(1))])
    sql(session, "INSERT INTO import_run (attempt, started_at, finished_at, status) "
                 "VALUES (1, '2026-11-01 04:30+01', '2026-11-01 04:31+01', 'ok')")
    dashboard = load('rising.json')
    assert run_panel(session, dashboard, 'Cards tracked daily', **RISING_DEFAULTS)[1] == [(2,)]
    assert run_panel(session, dashboard, 'History since', **RISING_DEFAULTS)[1] == [('2026-10-31',)]
    assert run_panel(session, dashboard, 'Last import', **RISING_DEFAULTS)[1] == [('2026-11-01 04:31 · ok',)]


def test_dashboards_use_mtg_market_scraper_datasource_and_english():
    uids = set()
    for path in DASHBOARDS.glob('*.json'):
        dashboard = json.loads(path.read_text())
        uids.add(dashboard['uid'])
        for p in dashboard['panels']:
            for t in p.get('targets', []):
                assert t['datasource']['uid'] == 'mtg_market_scraper', p['title']
        for var in dashboard['templating']['list']:
            if var['type'] == 'query':
                assert var['datasource']['uid'] == 'mtg_market_scraper'
        text = path.read_text()
        assert 'Gioco' not in text and 'Finestra' not in text
    assert uids == {'rising', 'card-detail', 'imports'}


def card_options(session, search):
    dashboard = load('card-detail.json')
    [var] = [v for v in dashboard['templating']['list'] if v['name'] == 'card']
    query = var['query']['rawSql'] if isinstance(var['query'], dict) else var['query']
    return sql(session, render(query, search=search, card='')).fetchall()


def test_card_search_handles_quotes_and_percent(session):
    upsert_cards(session, [CardRow(1, "Gaea's Cradle", 10, None, None), CardRow(2, '100% Pure', None, None, None)])
    sql(session, "UPDATE expansion SET name = 'Urza''s Saga' WHERE id_expansion = 10")
    assert card_options(session, "gaea's") == [("Gaea's Cradle · Urza's Saga", 1)]
    assert card_options(session, '100%') == [('100% Pure', 2)]
    assert {v for _, v in card_options(session, '')} == {1, 2}


def test_card_detail_prices_and_changes(session):
    upsert_cards(session, [CardRow(1, 'Card', None, None, None), CardRow(2, 'Other', None, None, None)])
    for day, trend in [(AS_OF - timedelta(days=30), '8'), (AS_OF - timedelta(days=7), '10'),
                       (AS_OF - timedelta(days=1), '11'), (AS_OF, '12')]:
        insert_prices(session, day, [PriceRow(1, trend=Decimal(trend), low=Decimal('9'), trend_foil=Decimal('30'))])
        insert_prices(session, day, [PriceRow(2, trend=Decimal('99'))])
    dashboard = load('card-detail.json')
    variables = {'card': '1', 'search': ''}
    assert run_panel(session, dashboard, 'Trend', **variables)[1] == [(Decimal('12.00'),)]
    assert run_panel(session, dashboard, 'Low', **variables)[1] == [(Decimal('9.00'),)]
    assert float(run_panel(session, dashboard, 'Change 7 days', **variables)[1][0][0]) == pytest.approx(0.2)
    assert float(run_panel(session, dashboard, 'Change 30 days', **variables)[1][0][0]) == pytest.approx(0.5)
    columns, rows = run_panel(session, dashboard, 'Prices', **variables)
    assert columns[0] == 'time' and len(rows) == 4 and dict(zip(columns, rows[-1]))['Trend'] == Decimal('12.00')
    columns, rows = run_panel(session, dashboard, 'Prices (foil)', **variables)
    assert dict(zip(columns, rows[-1]))['Trend'] == Decimal('30.00')
    columns, rows = run_panel(session, dashboard, 'Latest daily changes', **variables)
    latest = dict(zip(columns, rows[0]))
    assert latest['Date'] == AS_OF and latest['Change'] == Decimal('1.00')
    assert float(latest['Change %']) == pytest.approx(1 / 11)


def test_card_detail_without_a_card_selected(session):
    dashboard = load('card-detail.json')
    for title, query in panel_queries(dashboard):
        sql(session, render(query, card='', search='')).fetchall()


def test_rising_link_targets_card_detail():
    table = panel(load('rising.json'), 'Rising cards')
    links = [prop['value'] for o in table['fieldConfig']['overrides'] for prop in o['properties'] if prop['id'] == 'links']
    url = links[0][0]['url']
    assert url.startswith('/d/card-detail/') and 'var-card=${__data.fields.id_product}' in url
    assert 'var-search=' in url and 'game' not in url


def test_duplicate_card_labels_get_the_product_id(session):
    upsert_cards(session, [CardRow(737111, 'Prize Pig', 10, None, None), CardRow(737112, 'Prize Pig', 10, None, None),
                                 CardRow(5, 'Prize Pig', 11, None, None)])
    sql(session, "UPDATE expansion SET name = 'Bloomburrow' WHERE id_expansion = 10")
    sql(session, "UPDATE expansion SET name = 'Promos' WHERE id_expansion = 11")
    assert sorted(card_options(session, 'prize pig')) == [
        ('Prize Pig · Bloomburrow · #737111', 737111),
        ('Prize Pig · Bloomburrow · #737112', 737112),
        ('Prize Pig · Promos', 5),
    ]


def test_selected_card_is_an_option_even_beyond_the_search(session):
    upsert_cards(session, [CardRow(1, 'Pikachu', None, None, None), CardRow(2, 'Raichu', None, None, None)])
    dashboard = load('card-detail.json')
    [var] = [v for v in dashboard['templating']['list'] if v['name'] == 'card']
    rows = sql(session, render(var['query']['rawSql'], search='pika', card='2')).fetchall()
    assert {v for _, v in rows} == {1, 2}


@pytest.mark.parametrize('min_price,min_gain', [('', ''), ('2,5', '10,5'), (' 5 ', '15')])
def test_rising_thresholds_tolerate_blank_and_commas(session, min_price, min_gain):
    seed_cards(session)
    rising_row(session, 1, gain=0.30)
    variables = {**RISING_DEFAULTS, 'min_price': min_price, 'min_gain': min_gain}
    dashboard = load('rising.json')
    for title, query in panel_queries(dashboard):
        sql(session, render(query, **variables)).fetchall()
    assert run_panel(session, dashboard, 'Rising now', **variables)[1] == [(1,)]


def test_changes_use_the_last_price_on_or_before(session):
    upsert_cards(session, [CardRow(1, 'Card', None, None, None)])
    for day, trend in [(AS_OF - timedelta(days=9), '10'), (AS_OF, '12')]:
        insert_prices(session, day, [PriceRow(1, trend=Decimal(trend))])
    variables = {'card': '1', 'search': ''}
    dashboard = load('card-detail.json')
    assert float(run_panel(session, dashboard, 'Change 7 days', **variables)[1][0][0]) == pytest.approx(0.2)
    assert run_panel(session, dashboard, 'Change 30 days', **variables)[1] in ([], [(None,)])


def test_window_offers_only_the_history_windows():
    [window] = [v for v in load('rising.json')['templating']['list'] if v['name'] == 'window']
    assert window['query'] == '14 days : 14,28 days : 28,56 days : 56'


def test_card_detail_has_no_dashboard_links():
    assert load('card-detail.json')['links'] == []


def test_card_detail_shows_the_scryfall_image_of_the_selected_card():
    dashboard = load('card-detail.json')
    image = panel(dashboard, 'Card')
    assert image['type'] == 'text' and image['options']['mode'] == 'html'
    assert 'https://api.scryfall.com/cards/cardmarket/${card:percentencode}?format=image' in image['options']['content']


@pytest.mark.parametrize('name', ['card-detail.json', 'imports.json'])
def test_panels_do_not_overlap(name):
    cells = set()
    for p in load(name)['panels']:
        g = p['gridPos']
        assert g['x'] + g['w'] <= 24, p['title']
        area = {(x, y) for x in range(g['x'], g['x'] + g['w']) for y in range(g['y'], g['y'] + g['h'])}
        assert not cells & area, p['title']
        cells |= area


def test_rising_sparkline_spans_missing_days():
    """A day missing for every card (MTGJSON has gaps) must not break each Trend line in two."""
    table = panel(load('rising.json'), 'Rising cards')
    [cell] = [prop['value'] for o in table['fieldConfig']['overrides'] for prop in o['properties']
              if prop['id'] == 'custom.cellOptions' and prop['value'].get('type') == 'sparkline']
    assert cell['spanNulls'] is True and cell['insertNulls'] is False


def test_card_detail_charts_span_missing_days():
    for p in load('card-detail.json')['panels']:
        if p['type'] == 'timeseries':
            assert p['fieldConfig']['defaults']['custom']['spanNulls'] is True, p['title']


def test_card_detail_price_charts_are_full_width():
    for title in ('Prices', 'Prices (foil)'):
        assert panel(load('card-detail.json'), title)['gridPos']['w'] == 24, title


def import_run(session, started, status, attempt=1, guide=None, error=None, finished=None):
    sql(session, 'INSERT INTO import_run (attempt, started_at, finished_at, guide_date, status, cards, prices, error) '
                 'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (attempt, started, finished or started + timedelta(minutes=2), guide, status,
         10 if status == 'ok' else None, 20 if status == 'ok' else None, error))


ROME_NOW = "now() AT TIME ZONE 'Europe/Rome'"


def rome_today(session):
    return sql(session, f'SELECT ({ROME_NOW})::date').scalar()


def rome_time(day, hour):
    return datetime(day.year, day.month, day.day, hour, 30, tzinfo=ZoneInfo('Europe/Rome'))


def today_status(session):
    return run_panel(session, load('imports.json'), 'Today')[1][0][0]


def test_imports_today(session):
    today = rome_today(session)
    assert today_status(session) == 'Not yet'
    import_run(session, rome_time(today - timedelta(days=1), 4), 'ok', guide=today - timedelta(days=1))
    assert today_status(session) == 'Not yet'
    import_run(session, rome_time(today, 4), 'failed', error='HTTPError: 503')
    import_run(session, rome_time(today, 4) + timedelta(minutes=15), 'failed', attempt=2, error='HTTPError: 503')
    assert today_status(session) == 'Failing · 2 failed'
    import_run(session, rome_time(today, 5), 'ok', attempt=3, guide=today)
    assert today_status(session) == 'Imported'
    mappings = panel(load('imports.json'), 'Today')['fieldConfig']['defaults']['mappings']
    patterns = [m['options']['pattern'] for m in mappings]
    assert any(re.fullmatch(p, 'Failing · 2 failed') for p in patterns)


def test_imports_stats_and_attempts(session):
    import_run(session, datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc), 'failed', error='ValueError: stale guide')
    import_run(session, datetime(2026, 11, 1, 3, 45, tzinfo=timezone.utc), 'ok', attempt=2, guide=date(2026, 10, 31))
    import_run(session, datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc), 'ok', attempt=3, guide=date(2026, 11, 1))
    dashboard = load('imports.json')
    assert run_panel(session, dashboard, 'Failed attempts')[1] == [(1,)]
    assert run_panel(session, dashboard, 'Last import')[1] == [('2026-11-01 05:02 · ok',)]
    columns, rows = run_panel(session, dashboard, 'Attempts per day')
    assert columns == ['time', 'OK', 'Failed'] and rows == [(datetime(2026, 11, 1), 2, 1)]
    columns, rows = run_panel(session, dashboard, 'Attempts')
    attempts = [dict(zip(columns, r)) for r in rows]
    assert [a['Attempt'] for a in attempts] == [3, 2, 1]
    assert [a['Note'] for a in attempts] == [None, 'late guide', None]
    assert attempts[2]['Error'] == 'ValueError: stale guide' and float(attempts[0]['Duration']) == 120


def test_imports_prices_and_missing_days(session):
    upsert_cards(session, [CardRow(1, 'Card', None, None, None), CardRow(2, 'Other', None, None, None)])
    today = rome_today(session)
    first = today - timedelta(days=5)
    for day in (first, first + timedelta(days=1), first + timedelta(days=4)):
        insert_prices(session, day, [PriceRow(1, trend=Decimal(1)), PriceRow(2, trend=Decimal(1))])
    dashboard = load('imports.json')
    _, rows = run_panel(session, dashboard, 'Cards priced per day')
    assert rows == [(datetime.combine(day, datetime.min.time()), 2)
                    for day in (first, first + timedelta(days=1), first + timedelta(days=4))]
    assert run_panel(session, dashboard, 'Latest prices')[1] == [(f'{today - timedelta(days=1)} · 1 d ago',)]
    columns, rows = run_panel(session, dashboard, 'Missing days')
    missing = [dict(zip(columns, r)) for r in rows]
    # Today is left out: before the sync runs it is not missing yet.
    assert [m['Day'] for m in missing] == [first + timedelta(days=3), first + timedelta(days=2)]
    assert missing[0]['Fill with'] == 'backfill (MTGJSON trend only)'


def test_imports_on_an_empty_db(session):
    dashboard = load('imports.json')
    assert run_panel(session, dashboard, 'Latest prices')[1] == [('—',)]
    assert run_panel(session, dashboard, 'Last import')[1] == [('never',)]
    assert run_panel(session, dashboard, 'Missing days')[1] == []
