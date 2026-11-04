from tcg_pulse import expansions
from tcg_pulse.expansions import derive_names, refresh_expansions
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
    ))
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
