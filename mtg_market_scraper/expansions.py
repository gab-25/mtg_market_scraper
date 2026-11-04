"""Expansion names, from MTGJSON's sets and from the names of Cardmarket's sealed products.

Cardmarket's singles list carries only an expansion id. MTGJSON's sets give Cardmarket's own name for
most real sets (mcmId -> mcmName, and mcmIdExtras -> "<name>: Extras"). For the rest (promos, Secret
Lair, ...), sealed products of the same expansion are named after it ("Bloomburrow Play Booster Box",
"Foundations Prerelease Pack"), so stripping the product part gives a name. Precedence: overrides
(expansion_overrides.json, by hand) over MTGJSON over the sealed products.
"""

import csv
import gzip
import io
import json
import re
from collections import Counter, defaultdict
from importlib.resources import files

from sqlalchemy.dialects.postgresql import insert

from .cardmarket import fetch_bytes
from .config import MTGJSON_SETS_URL
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


SET_COLUMNS = ('mcmId', 'mcmIdExtras', 'mcmName')


def read_sets(raw):
    """MTGJSON's gzipped sets CSV -> one {mcmId, mcmIdExtras, mcmName} dict per set (strings, maybe empty)."""
    rows = csv.DictReader(io.StringIO(gzip.decompress(raw).decode()))
    return [{col: row.get(col) or '' for col in SET_COLUMNS} for row in rows]


def fetch_sets():
    return read_sets(fetch_bytes(MTGJSON_SETS_URL))


def mtgjson_names(sets):
    """Map idExpansion -> Cardmarket's name, for the sets MTGJSON links to Cardmarket."""
    names = {}
    for s in sets:
        if not (s['mcmId'] and s['mcmName']):
            continue
        names[int(s['mcmId'])] = s['mcmName']
        if s['mcmIdExtras']:
            names[int(s['mcmIdExtras'])] = f"{s['mcmName']}: Extras"
    return names


def load_overrides():
    data = json.loads((files('mtg_market_scraper') / 'expansion_overrides.json').read_text())
    return {int(k): v for k, v in data.items()}


def refresh_expansions(session, nonsingles, sets):
    """Store the names (overrides, then MTGJSON, then sealed products); returns how many were named."""
    names = derive_names(nonsingles) | mtgjson_names(sets) | load_overrides()
    if names:
        stmt = insert(Expansion)
        session.execute(
            stmt.on_conflict_do_update(index_elements=[Expansion.id_expansion],
                                       set_={'name': stmt.excluded.name}),
            [{'id_expansion': exp, 'name': name} for exp, name in names.items()],
        )
    session.commit()
    return len(names)
