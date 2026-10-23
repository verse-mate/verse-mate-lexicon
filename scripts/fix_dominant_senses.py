#!/usr/bin/env python3
"""
Post-process `_lemmas.json` so each card's headline gloss is the sense the
word actually carries in scripture, not the first sense STEP happens to list.

PROBLEM
  STEP's brief lexicons (TBESH / TBESG) list several "extended Strong's"
  rows per Strong's number: the common sense, rarer senses, and proper names
  the word appears inside. verse-mate-web's `build.py` keys on the base
  4-digit number and keeps the FIRST row it meets, so for ~100 lemmas the
  card shows a sense used in a handful of verses, or a place name:

    H1004 בַּ֫יִת  "place"              — house, 2,049×
    H5892 עִיר    "excitement"         — city, 1,089×
    H6965 קוּם    "(Leb)-kamai"        — to arise, 628×
    H7462 רָעָה   "House of Shepherds" — to shepherd (Ps 23:1), 177×
    H1984 הָלַל   "to shine"           — to praise, 165×

  `notes` / `semanticRange` were later rewritten by enrich.py and mostly
  describe the right word; `basicGloss` (the headline) never was.

WHAT THIS SCRIPT DOES
  Counts how often each extended Strong's is used, word by word, in STEP's
  tagged Hebrew OT (TAHOT) and Greek NT (TAGNT). For every Strong's whose
  current `basicGloss` is still the first-listed row's gloss, and whose
  first-listed row covers under 20% of its occurrences, it replaces the
  headline with the most-used row's gloss. When that row is a different
  Hebrew/Greek word (a proper name, a homonym), `lemma`, `translit` and
  `pos` move with it and the stale `pronunciation` is dropped.

  An entry whose `basicGloss` was hand-edited (no longer the first-listed
  gloss) is never touched, so the script is idempotent and safe to re-run
  after every `build.py` regeneration.

  Afterwards run `bun run build:light` so `_lemmas-light.json` (what
  mobile renders from) carries the same glosses.

Usage:
  ./fix_dominant_senses.py                     # apply + write
  ./fix_dominant_senses.py --dry-run           # print changes; no write
  ./fix_dominant_senses.py --step-dir PATH     # use a local STEPBible-Data checkout
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEMMAS_JSON = HERE.parent / 'src' / 'generated' / '_lemmas.json'
CACHE_DIR = HERE / '.step-cache'

STEP_RAW = 'https://raw.githubusercontent.com/STEPBible/STEPBible-Data/master/'
LEXICON_FILES = [
    'Lexicons/TBESH - Translators Brief lexicon of Extended Strongs for Hebrew - STEPBible.org CC BY.txt',
    'Lexicons/TBESG - Translators Brief lexicon of Extended Strongs for Greek - STEPBible.org CC BY.txt',
]
TAGGED_FILES = [
    'Translators Amalgamated OT+NT/TAHOT Gen-Deu - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAHOT Jos-Est - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAHOT Job-Sng - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAHOT Isa-Mal - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAGNT Mat-Jhn - Translators Amalgamated Greek NT - STEPBible.org CC-BY.txt',
    'Translators Amalgamated OT+NT/TAGNT Act-Rev - Translators Amalgamated Greek NT - STEPBible.org CC-BY.txt',
]

# A first-listed row is "the wrong sense" only when it covers less than this
# share of the word's occurrences. Below MIN_OCCURRENCES there is too little
# usage to say which sense dominates.
FIRST_SHARE_MAX = 0.2
MIN_OCCURRENCES = 3

# The first-listed gloss is a fine synonym of the dominant one; leave as is.
KEEP: frozenset[str] = frozenset({
    'H0758',  # Aram / Syria
    'H1166',  # Married / rule: to marry
    'H2011',  # (Topheth of) Hinnom
    'H2048',  # to mock / to deceive
    'H4024',  # tower / Migdol
    'H4118',  # quick / quickly
    'H4938',  # support / staff
    'H5553',  # rock / crag
    'H5713',  # witness / testimony
    'H5920',  # Most High / height
    'H6767',  # buzzing / banging
    'H7722',  # ravage / devastation
    'H8433',  # rebuke / argument
    'H8649',  # treachery / deceitfulness
})

# STEP glosses read "root: sense". The root half is the headline, except where
# the root reads wrong for how the word is used.
GLOSS_OVERRIDES: dict[str, str] = {
    'H1984': 'to praise',    # "to boast: praise" — Psalms' hallel
    'H2490': 'to profane',   # "to profane/begin: profane"
    'H7462': 'to shepherd',  # "to pasture" — Ps 23:1 "The LORD is my shepherd"
    'H7704': 'field',        # "land: country" — שָׂדֶה, field/open country
}

# Entries whose enriched prose (notes / semanticRange / related) describes the
# displaced word, not the one now on the card. Their prose falls back to STEP's
# dictionary definition until enrich.py re-runs them.
RESET_PROSE: frozenset[str] = frozenset({
    'H0328',  # sorcerers' muttering → "softly" (אַט)
    'H1933',  # to fall → to be (הָוָה)
    'H3733',  # camel saddle → ram (כַּר)
    'H6030',  # to dwell → to answer (עָנָה)
    'H7356',  # womb → compassion (רַחֲמִים)
    'H7462',  # place name Beth-eked → to shepherd (רָעָה)
    'H8302',  # lance → armor (שִׁרְיוֹן)
})

# STEP POS code → the display POS the lexicon uses (subset of build.py's map).
POS_MAP = {
    'H:V': 'Verb', 'A:V': 'Verb', 'G:V': 'Verb',
    'H:N': 'Noun', 'H:N-M': 'Noun (masc.)', 'H:N-F': 'Noun (fem.)',
    'G:N': 'Noun', 'G:N-M': 'Noun (masc.)', 'G:N-F': 'Noun (fem.)', 'G:N-N': 'Noun (neut.)',
    'H:A': 'Adjective', 'G:A': 'Adjective',
    'H:Adv': 'Adverb', 'G:ADV': 'Adverb',
    'N:N': 'Proper noun', 'N:N-M-P': 'Proper noun (person)', 'N:N-F-P': 'Proper noun (person)',
    'N:N--T': 'Proper noun (place/thing)', 'N:N--L': 'Proper noun (place/thing)',
}

STRONGS_RE = re.compile(r'^([GH])0*(\d+)')


def base_strongs(raw: str) -> str | None:
    m = STRONGS_RE.match((raw or '').strip())
    return f'{m.group(1)}{m.group(2).zfill(4)}' if m else None


def step_path(step_dir: Path | None, rel: str) -> Path:
    if step_dir:
        return step_dir / rel
    dest = CACHE_DIR / Path(rel).name
    if not dest.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        url = STEP_RAW + urllib.parse.quote(rel)
        print(f'  downloading {Path(rel).name}…', file=sys.stderr)
        urllib.request.urlretrieve(url, dest)
    return dest


def clean_definition(text: str) -> str:
    """Same cleanup build.py applies to TBES definitions."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^\d+[a-z]?\)\s*', '', text)
    if len(text) > 240:
        text = text[:237].rsplit(' ', 1)[0] + '…'
    return text


def parse_lexicons(step_dir: Path | None):
    """first[base] is the row build.py kept; rows[base][ext] every listed sense."""
    first: dict[str, dict] = {}
    rows: dict[str, dict[str, dict]] = defaultdict(dict)
    for rel in LEXICON_FILES:
        with step_path(step_dir, rel).open(encoding='utf-8') as f:
            for line in f:
                p = line.rstrip('\n').split('\t')
                if len(p) < 8 or not re.match(r'^[GH]\d{4}', p[0]):
                    continue
                m = re.match(r'([GH]\d{4}[A-Za-z]?)', p[1].strip() or p[0])
                if not m:
                    continue
                row = {
                    'ext': m.group(1),
                    'lemma': p[3].split(',')[0].strip(),
                    'translit': p[4].split(',')[0].strip(),
                    'pos': p[5].strip(),
                    'gloss': p[6].strip(),
                    'definition': clean_definition(p[7]),
                }
                b = base_strongs(p[0])
                first.setdefault(b, row)
                rows[b].setdefault(row['ext'], row)
    return first, rows


def count_usage(step_dir: Path | None) -> Counter:
    """Occurrences of each extended Strong's, one per word in the source text."""
    use: Counter = Counter()
    for rel in TAGGED_FILES:
        hebrew = 'TAHOT' in rel
        with step_path(step_dir, rel).open(encoding='utf-8') as f:
            for line in f:
                if not re.match(r'^\w{3}\.\d+\.\d+', line):
                    continue
                p = line.rstrip('\n').split('\t')
                if hebrew:
                    # Column 4 is "H9003/{H7225G}": prefixes outside braces, the word inside.
                    core = re.findall(r'\{(H\d{4}[A-Za-z]?)\}', p[4]) if len(p) > 4 else []
                    if core:
                        use[core[0]] += 1
                elif len(p) > 5 and ('NA28' in p[5] or 'SBL' in p[5]):
                    m = re.match(r'(G\d{4}[A-Za-z]?)', p[3])
                    if m:
                        use[m.group(1)] += 1
    return use


def headline(gloss: str) -> str:
    return gloss.split(':')[0].strip()


def plan_changes(lemmas: dict, first: dict, rows: dict, use: Counter) -> list[dict]:
    by_strongs: dict[str, list[str]] = defaultdict(list)
    for slug, entry in lemmas.items():
        b = base_strongs(entry.get('strongs', ''))
        if b:
            by_strongs[b].append(slug)

    changes = []
    for b, kept in first.items():
        if b in KEEP:
            continue
        senses = rows[b]
        total = sum(use[ext] for ext in senses)
        if total < MIN_OCCURRENCES:
            continue
        dominant = max(senses.values(), key=lambda r: use[r['ext']])
        if dominant['ext'] == kept['ext'] or use[kept['ext']] / total >= FIRST_SHARE_MAX:
            continue
        new_gloss = GLOSS_OVERRIDES.get(b, headline(dominant['gloss']))
        if new_gloss.lower() == headline(kept['gloss']).lower():
            continue
        for slug in by_strongs.get(b, []):
            entry = lemmas[slug]
            # Only replace the build.py default; a hand-edited gloss wins.
            if entry.get('basicGloss') != kept['gloss']:
                continue
            changes.append({
                'slug': slug,
                'strongs': b,
                'occurrences': total,
                'old_gloss': entry.get('basicGloss'),
                'new_gloss': new_gloss,
                'other_word': kept['lemma'] != dominant['lemma'],
                'dominant': dominant,
            })
    changes.sort(key=lambda c: -c['occurrences'])
    return changes


def apply(lemmas: dict, change: dict) -> None:
    entry = lemmas[change['slug']]
    dominant = change['dominant']
    entry['basicGloss'] = change['new_gloss']
    if change['other_word']:
        entry['lemma'] = dominant['lemma']
        entry['translit'] = dominant['translit']
        entry['pos'] = POS_MAP.get(dominant['pos'], dominant['pos'])
        entry.pop('pronunciation', None)
    if change['strongs'] in RESET_PROSE:
        entry['notes'] = dominant['definition']
        for field in ('semanticRange', 'related', 'pronunciation'):
            entry.pop(field, None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='print changes; do not write')
    ap.add_argument('--step-dir', type=Path, help='local STEPBible-Data checkout (skips download)')
    args = ap.parse_args()

    lemmas = json.loads(LEMMAS_JSON.read_text(encoding='utf-8'))
    first, rows = parse_lexicons(args.step_dir)
    use = count_usage(args.step_dir)
    changes = plan_changes(lemmas, first, rows, use)

    for c in changes:
        tag = ' [other word]' if c['other_word'] else ''
        tag += ' [prose reset]' if c['strongs'] in RESET_PROSE else ''
        print(f"{c['strongs']} {c['slug']:<18} {c['occurrences']:>5}×  "
              f"{c['old_gloss']!r} → {c['new_gloss']!r}{tag}")
    print(f'{len(changes)} entries, {sum(c["occurrences"] for c in changes):,} occurrences.')

    if args.dry_run or not changes:
        return 0
    for c in changes:
        apply(lemmas, c)
    LEMMAS_JSON.write_text(json.dumps(lemmas, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {LEMMAS_JSON.relative_to(HERE.parent)}. Now run: bun run build:light')
    return 0


if __name__ == '__main__':
    sys.exit(main())
