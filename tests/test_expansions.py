import gzip

from mtg_market_scraper import expansions
from mtg_market_scraper.expansions import derive_names, mtgjson_names, read_sets, refresh_expansions
from conftest import sql


def sealed(*items):
    """items: (idExpansion, name, categoryName)"""
    return {'products': [
        {'idProduct': i, 'name': name, 'categoryName': category, 'idExpansion': exp}
        for i, (exp, name, category) in enumerate(items)
    ]}


def test_names_from_boosters():
    names = derive_names(sealed(
        (5800, 'Bloomburrow Play Booster Box', 'Magic Display'),
        (5800, 'Bloomburrow Play Booster', 'Magic Booster'),
        (5800, 'Bloomburrow Commander Deck', 'Magic Preconstructed Decks'),
        (5900, 'Foundations Prerelease Pack', 'Magic Prerelease'),
        (5900, 'Foundations Collector Booster', 'Magic Booster'),
    ))
    assert names == {5800: 'Bloomburrow', 5900: 'Foundations'}


def test_booster_box_case_with_parenthetical():
    names = derive_names(sealed(
        (5800, 'Bloomburrow Case (6x Play Booster Box)', 'Magic Display'),
        (5800, 'Bloomburrow Play Booster', 'Magic Booster'),
    ))
    assert names == {5800: 'Bloomburrow'}


def test_falls_back_to_other_categories():
    assert derive_names(sealed((1, 'Foundations Prerelease Pack', 'Magic Prerelease'))) == {1: 'Foundations'}


def test_expansion_without_products_absent():
    assert 999 not in derive_names(sealed((1, 'Bloomburrow Play Booster', 'Magic Booster')))


def test_overrides_win_and_unnamed_stays_null(session, monkeypatch):
    monkeypatch.setattr(expansions, 'load_overrides', lambda: {5800: 'Bloomburrow (Fixed)'})
    sql(session, "INSERT INTO expansion (id_expansion) VALUES (5800), (42)")
    count = refresh_expansions(session, sealed(
        (5800, 'Bloomburrow Play Booster', 'Magic Booster'),
        (5900, 'Foundations Play Booster', 'Magic Booster'),
    ), [])
    rows = dict(sql(session, "SELECT id_expansion, name FROM expansion").fetchall())
    assert rows == {5800: 'Bloomburrow (Fixed)', 5900: 'Foundations', 42: None}
    assert count == 2


def test_load_overrides_reads_repo_file():
    assert isinstance(expansions.load_overrides(), dict)


def test_names_after_colon_are_kept():
    names = derive_names(sealed(
        (1, 'Innistrad: Crimson Vow Draft Booster Box', 'Magic Display'),
        (1, 'Innistrad: Crimson Vow Set Booster', 'Magic Booster'),
        (2, 'Innistrad: Midnight Hunt Draft Booster', 'Magic Booster'),
        (3, 'From the Vault: Dragons', 'MtG Set'),
        (4, 'Challenger Deck 2022: Gruul Stompy', 'Magic Preconstructed Decks'),
    ))
    assert names[1] == 'Innistrad: Crimson Vow'
    assert names[2] == 'Innistrad: Midnight Hunt'
    assert names[3] == 'From the Vault: Dragons'
    assert names[4] == 'Challenger Deck 2022: Gruul Stompy'


def test_edition_markers_in_parentheses_are_kept():
    names = derive_names(sealed(
        (10, 'Dominaria Booster Box Case (6x Booster Box)', 'Magic Display'),
        (10, 'Dominaria Booster', 'Magic Booster'),
        (11, 'Dominaria Booster Box Case (6x Booster Box) (Non-English)', 'Magic Display'),
        (11, 'Dominaria Booster (Non-English)', 'Magic Booster'),
        (12, 'Dominaria Draft Booster (Pre-Errata)', 'Magic Booster'),
    ))
    assert names == {10: 'Dominaria', 11: 'Dominaria (Non-English)', 12: 'Dominaria (Pre-Errata)'}


def mtgjson_set(mcm_id, name, extras=''):
    return {'mcmId': str(mcm_id), 'mcmIdExtras': str(extras), 'mcmName': name}


def test_read_sets_keeps_the_cardmarket_columns():
    raw = gzip.compress(
        b'code,name,mcmId,mcmName,mcmIdExtras\n'
        b'BLB,Bloomburrow,5658,Bloomburrow,5659\n'
        b'PBLB,Bloomburrow Promos,,,\n')
    assert read_sets(raw) == [mtgjson_set(5658, 'Bloomburrow', 5659), mtgjson_set('', '')]


def test_mtgjson_names_include_the_extras_expansion():
    names = mtgjson_names([
        mtgjson_set(5658, 'Bloomburrow', 5659),
        mtgjson_set(74, 'Tenth Edition'),
        mtgjson_set('', ''),
    ])
    assert names == {5658: 'Bloomburrow', 5659: 'Bloomburrow: Extras', 74: 'Tenth Edition'}


def test_mtgjson_beats_derived_and_overrides_beat_mtgjson(session, monkeypatch):
    monkeypatch.setattr(expansions, 'load_overrides', lambda: {3: 'Hand-fixed'})
    refresh_expansions(
        session,
        sealed((1, 'Bloomburrow Play Booster', 'Magic Booster'), (2, 'Foundations Play Booster', 'Magic Booster')),
        [mtgjson_set(1, 'Bloomburrow (MTGJSON)'), mtgjson_set(3, 'Promos')],
    )
    rows = dict(sql(session, "SELECT id_expansion, name FROM expansion").fetchall())
    assert rows == {1: 'Bloomburrow (MTGJSON)', 2: 'Foundations', 3: 'Hand-fixed'}
