#!/usr/bin/env python3
"""
Drop aliases that mostly name a different word in the verse.

PROBLEM
  An alias underlines whatever matching text it finds in a verse where its
  word occurs. Some aliases are another word's English: STEP's idiom
  fragments ("give into the hand" left H5414 "give" with "hand"), LLM
  recall ("LORD" with "god", "one" with "a"), copulas on "to be". In "the
  LORD God", H3068's "god" alias claims the "God" that is H0430, and the
  card shows the wrong word.

WHAT THIS SCRIPT DOES
  First a trailing preposition is trimmed off every alias ("said to" →
  "said", "slept with" → "slept"), as build_step_aliases.py does for STEP's
  renderings. The preposition is its own word in the verse, and the longer
  alias wins the substring scan, so "said to" on "to speak" would claim
  both the "said" of "to say" and the "to" after it.

  Then, for every verse a word occurs in, STEP's tagged OT/NT (TAHOT/TAGNT) gives
  the English of each original-language word. An alias counts as the word's
  own in that verse when its words all appear in the word's own rendering,
  and as another word's when they all appear in another word's rendering
  instead. An alias whose matches go to other words at least MIN_OTHER
  times, and to its own word less than MAX_OWN_SHARE of the time, is
  dropped. Aliases STEP says nothing about either way stay.

  Run it last, after generate_aliases.py, kjv_aliases.py and
  build_step_aliases.py, since each of them only adds. Idempotent.

Usage:
  ./prune_aliases.py --report
  ./prune_aliases.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from build_step_aliases import TRAILING
from step_data import iter_tagged_words

HERE = Path(__file__).resolve().parent
ALIASES_JSON = HERE.parent / 'src' / 'generated' / '_aliases.json'

MIN_OTHER = 3
MAX_OWN_SHARE = 0.2
WORD_RE = re.compile(r"[a-z’']+")


def words(text: str) -> frozenset[str]:
    return frozenset(WORD_RE.findall(re.sub(r'[\[\]<>]', '', text.lower())))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--report', action='store_true', help='print what would be dropped; write nothing')
    ap.add_argument('--step-dir', type=Path, help='local STEPBible-Data checkout (skips download)')
    args = ap.parse_args()

    data = json.loads(ALIASES_JSON.read_text(encoding='utf-8'))

    trimmed = 0
    for strongs, aliases in data.items():
        if strongs == '_meta':
            continue
        out: list[str] = []
        for alias in aliases:
            parts = alias.split()
            while len(parts) > 1 and parts[-1] in TRAILING:
                parts.pop()
            s = ' '.join(parts)
            trimmed += s != alias
            if s not in out:
                out.append(s)
        data[strongs] = out
    print(f'{trimmed:,} aliases trimmed of a trailing preposition.')

    # verse -> Strong's -> words of its English in that verse
    verses: dict[tuple, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for w in iter_tagged_words(args.step_dir):
        verses[(w['book_id'], w['chapter'], w['verse'])][w['strongs']] |= words(w['english'])

    own: Counter = Counter()
    other: Counter = Counter()
    for renderings in verses.values():
        for strongs, mine in renderings.items():
            for alias in data.get(strongs, ()):
                aw = words(alias)
                if not aw:
                    continue
                if aw <= mine:
                    own[strongs, alias] += 1
                elif any(aw <= ws for s, ws in renderings.items() if s != strongs):
                    other[strongs, alias] += 1

    dropped = {key: (own[key], n) for key, n in other.items()
               if n >= MIN_OTHER and own[key] / (own[key] + n) < MAX_OWN_SHARE}
    for strongs, alias in dropped:
        data[strongs] = [a for a in data[strongs] if a != alias]

    print(f'{len(dropped):,} aliases dropped across '
          f'{len({s for s, _ in dropped}):,} words.')
    for (strongs, alias), (o, n) in sorted(dropped.items(), key=lambda kv: -kv[1][1])[:25]:
        print(f'   {strongs} {alias!r}: own {o}, other words {n}')
    if args.report:
        print('\n--report: no files written.')
    else:
        if isinstance(data.get('_meta'), dict):
            data['_meta']['generated'] = (
                'generate_aliases.py (LLM, every aligned word), kjv_aliases.py (KJV '
                'archaic forms), build_step_aliases.py (STEP TAHOT/TAGNT English '
                'renderings), pruned by prune_aliases.py'
            )
        ALIASES_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
