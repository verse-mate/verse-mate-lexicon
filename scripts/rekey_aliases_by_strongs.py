#!/usr/bin/env python3
"""
One-time migration: re-key `_aliases.json` from lemma slug to Strong's number.

PROBLEM
  Aliases were generated per lemma SLUG (generate_aliases.py asked Claude
  about whichever entry held that slug at the time). Slugs collapse
  homographs, and fix_slug_collisions.py later moved different words onto
  22 of those slugs, so a token's `lemma` slug can name a different word
  than the one its aliases were written for:

    ben   aliases written for בֵּין "between"  — tokens are בֵּן "son"
    ayin  aliases written for אַיִן "nothing"  — tokens are עַ֫יִן "eye"
                                                (so a "no" in the verse
                                                opened the "eye" card)
    ruach aliases written for רוּחַ "to smell" — tokens are רוּחַ "Spirit"

  ~28,500 aligned tokens (9.4%) picked up another word's surfaces.

WHAT THIS SCRIPT DOES
  Maps every alias list to the Strong's number of the entry it was
  generated FOR — read from `_lemmas.json` as of the commit the aliases
  were generated against (8b85467, before the slug-collision swaps) — and
  rewrites `_aliases.json` keyed by canonical `G####` / `H####`. The loader
  then looks aliases up by each token's own Strong's, so a word only ever
  gets surfaces written for it.

  Lists that land on the same Strong's are unioned (order preserved).

Usage (from the lexicon repo root; needs git history):
  python3 scripts/rekey_aliases_by_strongs.py --report   # preview, write nothing
  python3 scripts/rekey_aliases_by_strongs.py
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ALIASES_JSON = ROOT / 'src' / 'generated' / '_aliases.json'
LEMMAS_JSON = ROOT / 'src' / 'generated' / '_lemmas.json'

# `_lemmas.json` as the alias generator saw it (aliases landed in e763fbf..cb24aeb,
# none of which touched _lemmas.json; 4120dbe then swapped slugs).
GENERATED_AGAINST = '8b85467'

STRONGS_RE = re.compile(r'^([GH])0*(\d+)')


def normalize_strongs(raw: str | None) -> str | None:
    m = STRONGS_RE.match((raw or '').strip())
    return f'{m.group(1)}{m.group(2).zfill(4)}' if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description='Re-key _aliases.json by Strong\'s number.')
    ap.add_argument('--report', action='store_true', help='print changes, write nothing')
    args = ap.parse_args()

    aliases = json.loads(ALIASES_JSON.read_text(encoding='utf-8'))
    meta = aliases.pop('_meta', {})
    if all(STRONGS_RE.match(k) for k in aliases):
        print('_aliases.json is already keyed by Strong\'s; nothing to do.')
        return 0

    then = json.loads(subprocess.check_output(
        ['git', 'show', f'{GENERATED_AGAINST}:src/generated/_lemmas.json'], cwd=ROOT))
    now = json.loads(LEMMAS_JSON.read_text(encoding='utf-8'))

    out: dict[str, list[str]] = {}
    moved, dropped = [], []
    for slug, surfaces in aliases.items():
        strongs = normalize_strongs((then.get(slug) or {}).get('strongs'))
        if not strongs:
            dropped.append(slug)
            continue
        if normalize_strongs((now.get(slug) or {}).get('strongs')) != strongs:
            moved.append((slug, strongs, normalize_strongs((now.get(slug) or {}).get('strongs'))))
        merged = out.setdefault(strongs, [])
        seen = {s.lower() for s in merged}
        merged.extend(s for s in surfaces if s.lower() not in seen)

    print(f'{len(aliases)} slug lists → {len(out)} Strong\'s lists.')
    print(f'{len(moved)} lists now follow their own word instead of the slug\'s current occupant:')
    for slug, was, is_now in moved:
        print(f'   {slug:10s} {was} (aliases) — slug now holds {is_now}')
    if dropped:
        print(f'{len(dropped)} lists had no Strong\'s and were dropped: {dropped}')
    if args.report:
        print('\n--report: no file written.')
        return 0

    meta['schema'] = "Record<strongs, string[]> — canonical G####/H####"
    meta['description'] = (
        "Cross-translation English surface aliases keyed by Strong's number. "
        "Each list holds distinct English surfaces (lowercase, no leading article) "
        "used across KJV, NASB, ESV, NIV, BSB, NLT, ASV, NRSV for that word. The "
        "loader unions a token's list with its BSB surface so the renderer's "
        "substring scan matches whichever translation is displayed."
    )
    ordered = {'_meta': meta, **dict(sorted(out.items()))}
    ALIASES_JSON.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nwrote {ALIASES_JSON.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
