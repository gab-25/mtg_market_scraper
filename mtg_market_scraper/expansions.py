"""Expansion names, derived from the names of Cardmarket's sealed products.

Cardmarket's singles list carries only an expansion id. Sealed products of the same expansion are
named after it ("Bloomburrow Play Booster Box", "Foundations Prerelease Pack"), so stripping the product
part gives the expansion name. expansion_overrides.json corrects names by hand and always wins.
"""

import json
import re
from collections import Counter, defaultdict
from importlib.resources import files

from sqlalchemy.dialects.postgresql import insert

from .models import Expansion

# Trailing words that name the product, not the expansion ("Draft Booster Box", "Prerelease Pack").
PRODUCT_WORDS = {
    'booster', 'boosters', 'box', 'boxes', 'case', 'display', 'draft', 'set', 'collector', 'play',
    'sleeved', 'bundle', 'pack', 'packs', 'dash', 'tin', 'vault', 'deck', 'theme', 'fat', 'kit',
    'prerelease', 'event',
}
PARENTHETICAL_RE = re.compile(r'\(([^)]*)\)')
PACKAGING_RE = re.compile(r'^\d+x\b', re.IGNORECASE)


def is_packaging(note):
    """"12x Booster Box" describes the package; "Non-English" or "Pre-Errata" tells editions apart."""
    words = note.lower().split()
    return bool(PACKAGING_RE.match(note.strip())) or all(w in PRODUCT_WORDS for w in words)


def candidate(name):
    """Drop the trailing product words and packaging notes, keep edition notes:
    "Innistrad: Crimson Vow Draft Booster Box" -> "Innistrad: Crimson Vow",
    "Foundations Prerelease Pack" -> "Foundations",
    "Dominaria Booster (Non-English)" -> "Dominaria (Non-English)".
    Empty when nothing but product words is left."""
    notes = [n.strip() for n in PARENTHETICAL_RE.findall(name) if not is_packaging(n)]
    words = PARENTHETICAL_RE.sub(' ', name).split()
    while words and words[-1].lower().rstrip(':-') in PRODUCT_WORDS:
        words.pop()
    base = ' '.join(words).rstrip(' :-')
    return ' '.join([base, *(f'({n})' for n in notes)]) if base else ''


def derive_names(nonsingles):
    """Map idExpansion -> name, for expansions that have at least one sealed product."""
    preferred, other = defaultdict(list), defaultdict(list)
    for product in nonsingles['products']:
        exp = product.get('idExpansion')
        name = candidate(product.get('name') or '')
        if not exp or not name:
            continue
        category = product.get('categoryName') or ''
        target = preferred if ('Booster' in category or 'Display' in category) else other
        target[exp].append(name)
    names = {}
    for exp in preferred.keys() | other.keys():
        counts = Counter(preferred.get(exp) or other[exp])
        names[exp] = min(counts, key=lambda n: (-counts[n], len(n), n))
    return names


def load_overrides():
    data = json.loads((files('mtg_market_scraper') / 'expansion_overrides.json').read_text())
    return {int(k): v for k, v in data.items()}


def refresh_expansions(session, nonsingles):
    """Store derived names (overrides win) for the expansions; returns how many were named."""
    names = derive_names(nonsingles) | load_overrides()
    if names:
        stmt = insert(Expansion)
        session.execute(
            stmt.on_conflict_do_update(index_elements=[Expansion.id_expansion],
                                       set_={'name': stmt.excluded.name}),
            [{'id_expansion': exp, 'name': name} for exp, name in names.items()],
        )
    session.commit()
    return len(names)
