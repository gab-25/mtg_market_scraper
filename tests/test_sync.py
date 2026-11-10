import gzip
import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from mtg_market_scraper import __main__ as cli
from mtg_market_scraper.config import NONSINGLES_URL, PRICE_GUIDE_URL, SINGLES_URL
from mtg_market_scraper.sync import archive, reimport, sync
from conftest import sql

TODAY = date(2026, 11, 1)
TODAY_STAMP = '2026-11-01T02:47:00+0100'
YESTERDAY = TODAY - timedelta(days=1)
YESTERDAY_STAMP = '2026-10-31T02:47:00+0100'


class FakeCardmarket:
    """Serves two singles and one sealed product; scripted guide dates and failures per URL."""

    def __init__(self):
        self.calls = []
        self.stamps = []  # createdAt values of the price guide, served in order (then today)
        self.errors = {}  # url -> exception raised every time

    def __call__(self, url):
        self.calls.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url == PRICE_GUIDE_URL:
            stamp = self.stamps.pop(0) if self.stamps else TODAY_STAMP
            return {'createdAt': stamp, 'priceGuides': [
                {'idProduct': 1, 'trend': 1.5, 'trend-foil': 3},
                {'idProduct': 2, 'trend': 2.5},
                {'idProduct': 99, 'trend': 50},  # sealed product, not a single
            ]}
        if url == SINGLES_URL:
            return {'products': [
                {'idProduct': 1, 'name': 'One', 'idExpansion': 10},
                {'idProduct': 2, 'name': 'Two', 'idExpansion': 10},
            ]}
        if url == NONSINGLES_URL:
            return {'products': [
                {'idProduct': 99, 'name': 'Bloomburrow Play Booster', 'categoryName': 'Magic Booster', 'idExpansion': 10}]}
        raise AssertionError(f'unexpected url {url}')


@pytest.fixture
def fake():
    return FakeCardmarket()


class FakeMtgjson:
    """Serves MTGJSON's sets, as read_sets returns them; fails when error is set."""

    def __init__(self):
        self.calls = 0
        self.error = None
        self.sets = [{'mcmId': '10', 'mcmIdExtras': '11', 'mcmName': 'Bloomburrow (MTGJSON)'}]

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.sets


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    """The repo's expansion_overrides.json would add its own names to every sync."""
    from mtg_market_scraper import expansions
    monkeypatch.setattr(expansions, 'load_overrides', dict)


@pytest.fixture
def mtgjson():
    return FakeMtgjson()


@pytest.fixture
def sleeps():
    return []


def run(session, tmp_path, fake, sleeps, mtgjson=None):
    return sync(session, TODAY, tmp_path, fetch=fake, sleep=sleeps.append, fetch_sets=mtgjson or FakeMtgjson())


def names(session):
    return dict(sql(session, 'SELECT id_expansion, name FROM expansion').fetchall())


def runs(session):
    return sql(session, 'SELECT status FROM import_run ORDER BY id').fetchall()


def count_prices(session):
    return sql(session, 'SELECT count(*) FROM price').fetchone()[0]


def test_sync_imports_and_archives(session, tmp_path, fake, sleeps):
    assert run(session, tmp_path, fake, sleeps) is True
    assert sleeps == []
    assert sorted(p.name for p in (tmp_path / '2026-11-01').iterdir()) == [
        'mtgjson_sets.json.gz', 'price_guide.json.gz', 'products_nonsingles.json.gz', 'products_singles.json.gz']
    assert runs(session) == [('ok',)]
    assert count_prices(session) == 2
    assert sql(session, 'SELECT trend_foil FROM price WHERE id_product = 1').fetchone()[0] == 3
    assert names(session) == {10: 'Bloomburrow (MTGJSON)', 11: 'Bloomburrow (MTGJSON): Extras'}
    archived = json.loads(gzip.decompress((tmp_path / '2026-11-01' / 'price_guide.json.gz').read_bytes()))
    assert archived['createdAt'] == TODAY_STAMP


def test_guide_not_ready_counts_as_attempt(session, tmp_path, fake, sleeps):
    yesterday = FakeCardmarket()
    yesterday.stamps = [YESTERDAY_STAMP]
    sync(session, YESTERDAY, tmp_path, fetch=yesterday, sleep=sleeps.append, fetch_sets=FakeMtgjson())
    fake.stamps = [YESTERDAY_STAMP, YESTERDAY_STAMP, TODAY_STAMP]
    assert run(session, tmp_path, fake, sleeps) is True
    assert sleeps == [900, 900]
    assert runs(session) == [('ok',), ('failed',), ('failed',), ('ok',)]


def test_late_guide_of_a_missing_day_is_imported(session, tmp_path, fake, sleeps):
    """Yesterday's guide, published too late for yesterday's sync, is still imported under its own day."""
    fake.stamps = [YESTERDAY_STAMP, YESTERDAY_STAMP, TODAY_STAMP]
    assert run(session, tmp_path, fake, sleeps) is True
    assert sleeps == [900, 900]
    assert sql(session, 'SELECT status, guide_date FROM import_run ORDER BY id').fetchall() == [
        ('ok', YESTERDAY), ('failed', None), ('ok', TODAY)]
    assert sql(session, 'SELECT day, count(*) FROM price GROUP BY day ORDER BY day').fetchall() == [
        (YESTERDAY, 2), (TODAY, 2)]
    assert (tmp_path / '2026-10-31' / 'price_guide.json.gz').exists()


def test_late_guide_alone_does_not_finish_today(session, tmp_path, fake, sleeps):
    fake.stamps = [YESTERDAY_STAMP] * 4
    assert run(session, tmp_path, fake, sleeps) is False
    assert sql(session, 'SELECT status, guide_date FROM import_run ORDER BY id').fetchall() == [
        ('ok', YESTERDAY), ('failed', None), ('failed', None), ('failed', None)]


def test_late_guide_older_than_stored_prices_keeps_current_names_and_rising(session, tmp_path, fake, sleeps,
                                                                            monkeypatch):
    """A day older than the latest prices stored (e.g. backfilled) must not roll names or rising back."""
    sql(session, "INSERT INTO card (id_product, name) VALUES (1, 'Current')")
    sql(session, "INSERT INTO price (day, id_product, trend) VALUES (%s, 1, 1.0)", (TODAY,))
    session.commit()
    fake.stamps = [YESTERDAY_STAMP] * 4
    calls = []
    from mtg_market_scraper import sync as sync_module
    monkeypatch.setattr(sync_module, 'compute_rising', lambda session, as_of: calls.append(as_of))
    run(session, tmp_path, fake, sleeps)
    assert calls == []
    assert sql(session, 'SELECT name FROM card WHERE id_product = 1').fetchone()[0] == 'Current'
    assert sql(session, 'SELECT count(*) FROM price WHERE day = %s', (YESTERDAY,)).fetchone()[0] == 2


def test_failing_import_gets_four_attempts(session, tmp_path, fake, sleeps):
    fake.errors[PRICE_GUIDE_URL] = httpx.ConnectError('network down')
    assert run(session, tmp_path, fake, sleeps) is False
    assert sleeps == [900, 900, 900]
    rows = sql(session, 'SELECT attempt, status, error FROM import_run ORDER BY id').fetchall()
    assert [(a, s) for a, s, _ in rows] == [(1, 'failed'), (2, 'failed'), (3, 'failed'), (4, 'failed')]
    assert all('network down' in e for _, _, e in rows)
    assert count_prices(session) == 0


def test_rerun_same_day_is_noop(session, tmp_path, fake, sleeps):
    run(session, tmp_path, fake, sleeps)
    fake.calls.clear()
    assert run(session, tmp_path, fake, sleeps) is True
    assert fake.calls == []
    assert count_prices(session) == 2


def test_expansion_failure_does_not_fail_import(session, tmp_path, fake, sleeps):
    fake.errors[NONSINGLES_URL] = httpx.ConnectError('nope')
    assert run(session, tmp_path, fake, sleeps) is True
    assert runs(session) == [('ok',)]
    assert count_prices(session) == 2


def test_mtgjson_failure_leaves_names_alone(session, tmp_path, fake, sleeps, mtgjson):
    sql(session, "INSERT INTO expansion (id_expansion, name) VALUES (10, 'Kept')")
    session.commit()
    mtgjson.error = httpx.ConnectError('nope')
    assert run(session, tmp_path, fake, sleeps, mtgjson) is True
    assert runs(session) == [('ok',)]
    assert names(session) == {10: 'Kept'}


def test_reimport_names_from_the_archive(session, tmp_path, fake, sleeps, mtgjson):
    run(session, tmp_path, fake, sleeps, mtgjson)
    sql(session, 'UPDATE expansion SET name = NULL')
    session.commit()
    reimport(session, TODAY, tmp_path)
    assert mtgjson.calls == 1
    assert names(session)[10] == 'Bloomburrow (MTGJSON)'


def test_reimport_without_archived_sets_leaves_names_alone(session, tmp_path, fake, sleeps):
    run(session, tmp_path, fake, sleeps)
    (tmp_path / '2026-11-01' / 'mtgjson_sets.json.gz').unlink()
    sql(session, "UPDATE expansion SET name = 'Kept'")
    session.commit()
    reimport(session, TODAY, tmp_path)
    assert set(names(session).values()) == {'Kept'}


def test_reimport_from_archive(session, tmp_path, fake, sleeps):
    run(session, tmp_path, fake, sleeps)
    sql(session, 'DELETE FROM price')
    session.commit()
    fake.calls.clear()
    result = reimport(session, TODAY, tmp_path)
    assert result.prices == 2 and fake.calls == []
    assert count_prices(session) == 2


def test_cli_exit_code(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setattr(cli, 'connect', lambda url: None)
    monkeypatch.setattr(cli, 'migrate', lambda session: None)
    monkeypatch.setattr(cli, 'sync', lambda *a, **k: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(['sync'])
    assert exc.value.code == 1


def test_rerun_retries_a_day_whose_rising_failed(session, tmp_path, fake, sleeps, monkeypatch):
    from mtg_market_scraper import sync as sync_module
    real = sync_module.compute_rising

    def broken(session, as_of):
        raise RuntimeError('statement timeout')

    monkeypatch.setattr(sync_module, 'compute_rising', broken)
    assert run(session, tmp_path, fake, sleeps) is False
    assert runs(session) == [('failed',)] * 4

    monkeypatch.setattr(sync_module, 'compute_rising', real)
    assert run(session, tmp_path, fake, sleeps) is True
    assert runs(session)[-1] == ('ok',)
    assert count_prices(session) == 2


def test_reimport_replaces_wrong_prices(session, tmp_path, fake, sleeps):
    run(session, tmp_path, fake, sleeps)
    sql(session, 'UPDATE price SET trend = 999')
    session.commit()
    reimport(session, TODAY, tmp_path)
    assert sql(session, 'SELECT trend FROM price WHERE id_product = 1').fetchone()[0] == Decimal('1.50')


def test_reimport_of_an_older_day_keeps_current_card_names(session, tmp_path, fake, sleeps):
    run(session, tmp_path, fake, sleeps)
    older = TODAY - timedelta(days=3)
    for kind in ('price_guide', 'products_singles'):
        payload = json.loads(gzip.decompress((tmp_path / '2026-11-01' / f'{kind}.json.gz').read_bytes()))
        if kind == 'price_guide':
            payload['createdAt'] = '2026-10-29T02:47:00+0100'
        else:
            payload['products'][0]['name'] = 'Old name'
        archive(tmp_path, older, kind, payload)
    reimport(session, older, tmp_path)
    assert sql(session, 'SELECT name FROM card WHERE id_product = 1').fetchone()[0] == 'One'
    assert sql(session, 'SELECT count(*) FROM price WHERE day = %s', (older,)).fetchone()[0] == 2
