#!/usr/bin/env python3
"""
Post-process `_lemmas.json` to fix wrong-card slug collisions.

PROBLEM
  The original build.py iterates TBESG/TBESH lexicons and the first Strong's
  to slugify into a given key wins the bare slug. Whatever its part of speech.

  This caused user-visible bugs:
    - γῆ "earth" (Noun G1093) lost to γέ "indeed" (Particle G1065) →
      tapping "earth" in James 1:10 showed a particle card.
    - μήν "month" (Noun G3376) lost to μέν (Particle G3303) →
      tapping "month" in James 4:13 showed a particle card.
    - 35 other particle/preposition/interjection/pronoun entries hold
      bare slugs they shouldn't, because their content-word collider
      slugged identically.

OBSERVATION
  build.py's `is_content_pos` filter rejects Particles, Prepositions,
  Conjunctions, Pronouns, Interjections, and similar function-word
  POS classes when scanning BSB tokens. Their contribution to the slug's
  ntFrequency / otFrequency counter is provably 0.

  So if the current bare-slug entry is in a filtered POS class AND has
  non-zero ntFrequency/otFrequency, those tags MUST have come from the
  displaced content-word collider. Promoting the content-word collider
  to the bare slug is therefore safe — it can't strand any tagged token.

WHAT THIS SCRIPT DOES
  For each slug-collision group where the bare entry is a non-content POS:
    1. Pick the best content-word sibling via POS priority:
       Noun > Verb > Adjective > Proper-noun > Adverb.
    2. Move the bare entry's data to `<base>_<bare_strongs>` (preserving
       the displaced entry under its disambiguating suffix). Frequency
       is reset to 0 since it never tagged any tokens.
    3. Move the chosen content-word into the bare slug, inheriting the
       bare's BSB frequency (which was always its frequency anyway).
    4. Strip enriched fields (semanticRange, related, pronunciation,
       loaded) from the new bare entry — those belonged to the prior
       occupant. enrich.py can re-run to repopulate.

  Then writes back to `_lemmas.json`. Prints a swap-by-swap report.

  This is a one-time data fix. The structural fix (per-Strong's freq
  tracking in build.py) is queued separately.

Usage:
  ./fix_slug_collisions.py                  # apply + write
  ./fix_slug_collisions.py --dry-run        # print swaps; no write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEMMAS_JSON = HERE.parent / 'src' / 'generated' / '_lemmas.json'

# Slugs that disambiguate via the gap-fill suffix end in `_g\d+` (Greek)
# or `_h\d+` (Hebrew), optionally with a letter for homonyms (h1254a).
SUFFIX_RE = re.compile(r'_[gh]\d+[a-z]?$')

# POS strings that build.py's `is_content_pos` would reject — entries
# with these POS values cannot have contributed to any slug's BSB freq.
NON_CONTENT_POS_KEYWORDS = (
    'particle', 'interjection', 'preposition', 'conjunction',
    'pronoun', 'numeral',
)
NON_CONTENT_POS_PREFIXES = (
    # Greek raw POS codes
    'g:cond', 'g:prt', 'g:conj', 'g:prep', 'g:art', 'g:t',
    'g:pron', 'g:part', 'g:inj', 'g:num',
    # Hebrew raw POS codes
    'h:conj', 'h:prep', 'h:art', 'h:pron', 'h:part', 'h:prt',
    'h:inj', 'h:intj', 'h:num',
    'h:dem', 'h:rel', 'h:perp', 'h:intg', 'h:neg', 'h:cond',
    # Aramaic raw POS codes
    'a:conj', 'a:prep', 'a:art', 'a:pron', 'a:part', 'a:dem',
    'a:perp', 'a:cond', 'a:neg', 'a:intg',
)

# Enriched fields stripped on swap — these were attached to the prior
# bare occupant by enrich.py and belong to a different lemma after swap.
ENRICHED_FIELDS = ('semanticRange', 'related', 'pronunciation', 'loaded')


def is_non_content_pos(pos: str) -> bool:
    """True when this POS class would be skipped by build.py's
    `is_content_pos` filter (and so cannot have contributed to BSB
    token frequency for any slug). Mirrors the SKIP_POS_PREFIXES set
    in verse-mate-web/scripts/lexicon-ingest/build.py.

    Empty/unknown POS strings are treated as content (matches build.py
    default — "Unknown codes default to True so we don't silently drop
    new content classes")."""
    p = (pos or '').lower().strip()
    if not p:
        return False
    if any(kw in p for kw in NON_CONTENT_POS_KEYWORDS):
        return True
    return any(p.startswith(pref) for pref in NON_CONTENT_POS_PREFIXES)


# POS strings we're willing to promote as the bare-slug winner. Any
# winner outside this set (suffixes, raw POS codes like Pp3f / G:I,
# punctuation markers) means the collision group has no clean content
# word to surface — skip the swap and leave the current bare alone.
PROMOTABLE_POS_KEYWORDS = (
    'noun', 'verb', 'adjective', 'adverb',
)


def is_promotable_content_pos(pos: str) -> bool:
    """A stricter check than `not is_non_content_pos`. Rejects raw
    POS codes (Pp3f, Ps3m, G:I, H:Intg), Suffix/Prefix markers, empty
    strings, and anything else that wouldn't make a quality card."""
    p = (pos or '').strip()
    if not p:
        return False
    p_lower = p.lower()
    if 'suffix' in p_lower or 'prefix' in p_lower or 'punct' in p_lower:
        return False
    if is_non_content_pos(p):
        return False
    # Must be one of the recognized content-word strings (handles
    # "Noun (masc.)", "Verb", "Adjective", "Adverb", "Proper noun (person)").
    # Note: "pronoun" contains "noun" but is already filtered above.
    if 'proper noun' in p_lower:
        return True
    return any(kw in p_lower for kw in PROMOTABLE_POS_KEYWORDS)


def pos_priority(pos: str) -> int:
    """Slug-collision tiebreaker rank — lower wins. Order matters:
    'verb' in 'adverb', 'noun' in 'pronoun'/'proper noun'."""
    p = (pos or '').lower()
    if 'pronoun' in p:      return 5
    if 'proper noun' in p:  return 3
    if 'noun' in p:         return 0
    if 'adverb' in p:       return 4
    if 'verb' in p:         return 1
    if 'adjective' in p:    return 2
    if 'preposition' in p:  return 6
    if 'conjunction' in p:  return 7
    return 9


def base_slug(slug: str) -> str:
    """Strip the gap-fill disambiguator suffix to get the bare slug
    a collision group shares. `ge_g1065` → `ge`, `taam_h2938` → `taam`."""
    return SUFFIX_RE.sub('', slug)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Print swaps but do not write the file.')
    args = parser.parse_args()

    if not LEMMAS_JSON.exists():
        print(f'ERROR: {LEMMAS_JSON} not found.', file=sys.stderr)
        return 1
    lem: dict[str, dict] = json.loads(LEMMAS_JSON.read_text())

    # Build collision groups
    groups: dict[str, list[str]] = {}
    for slug in lem:
        groups.setdefault(base_slug(slug), []).append(slug)

    swaps = []  # (base, displaced_slug, bare_lemma, winner_slug, winner_lemma)
    skipped_existing = 0

    for base, members in sorted(groups.items()):
        if len(members) < 2 or base not in lem:
            continue
        bare = lem[base]
        if not is_non_content_pos(bare.get('pos')):
            continue
        # Pick best content-word sibling. The promotable check is stricter
        # than just "not non-content" — it rejects raw POS codes (Pp3f,
        # G:I, ...), Suffix markers, etc. Those exist in the lexicon but
        # don't make good card content.
        candidates = []
        for s in members:
            if s == base:
                continue
            e = lem[s]
            if not is_promotable_content_pos(e.get('pos')):
                continue
            candidates.append((pos_priority(e.get('pos')), e.get('strongs', ''), s))
        if not candidates:
            continue
        candidates.sort()
        _, _, winner_slug = candidates[0]
        winner = lem[winner_slug]

        bare_strongs = (bare.get('strongs') or '').lower()
        if not bare_strongs:
            continue
        displaced_slug = f'{base}_{bare_strongs}'
        if displaced_slug in lem:
            # Already taken by a homonym disambiguator — skip to be safe.
            skipped_existing += 1
            continue

        # --- Perform the swap ---
        # 1. Move bare's data to `<base>_<bare_strongs>`, with zeroed freq
        #    (non-content POS never tagged any BSB token, so the freq the
        #    bare slug carried is the winner's).
        displaced = dict(bare)
        for fk in ('ntFrequency', 'otFrequency'):
            if fk in displaced:
                displaced[fk] = 0
        lem[displaced_slug] = displaced

        # 2. Promote winner: take its data, attach bare's freq, strip
        #    enriched fields (they belonged to the prior bare occupant).
        promoted = dict(winner)
        for k in ENRICHED_FIELDS:
            promoted.pop(k, None)
        # Carry the bare's freq forward — those tags were always the winner's.
        if 'ntFrequency' in bare:
            promoted['ntFrequency'] = bare['ntFrequency']
        if 'otFrequency' in bare:
            promoted['otFrequency'] = bare['otFrequency']
        # Notes from the bare entry belonged to the displaced lemma; reset
        # to whatever the winner had (gap-fill copies the raw TBE notes).
        # (winner's notes is already in `promoted` via the dict(winner) copy.)
        lem[base] = promoted

        # 3. Delete the winner's now-redundant suffixed slot.
        del lem[winner_slug]

        swaps.append((base, displaced_slug, bare.get('lemma', '?'),
                      bare.get('pos', '?'), winner_slug,
                      winner.get('lemma', '?'), winner.get('pos', '?')))

    # Report
    print(f'Slug-collision swaps applied: {len(swaps)}')
    if skipped_existing:
        print(f'(Skipped {skipped_existing} groups where the displaced slug was already taken.)')
    print()
    for base, disp, b_lemma, b_pos, w_slug, w_lemma, w_pos in swaps:
        print(f'  {base!r:18s}  {b_lemma!r:12s} ({b_pos!r:30s}) → {w_lemma!r:12s} ({w_pos!r:25s})')
        print(f'                      (displaced bare → {disp!r}, dropped sibling slug {w_slug!r})')

    if args.dry_run:
        print('\n[dry-run] No changes written.')
        return 0

    # Write back. Round-trip JSON to keep formatting consistent.
    LEMMAS_JSON.write_text(json.dumps(lem, ensure_ascii=False, indent=2))
    print(f'\nWrote {LEMMAS_JSON}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
