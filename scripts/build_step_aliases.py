#!/usr/bin/env python3
"""
Add the English surfaces each word is actually rendered with to `_aliases.json`.

PROBLEM
  A token underlines only when its BSB surface, or one of its aliases, appears
  in the displayed translation. generate_aliases.py covers ~2,000 words from an
  LLM's recall, so most words have no aliases and drop their underline whenever
  NASB/KJV/ESV word them differently from the BSB ("brethren" vs "brothers").

WHAT THIS SCRIPT DOES
  STEP's tagged OT/NT (TAHOT/TAGNT) give an English rendering for every
  original-language word. This collects them per Strong's number, cleans them
  (drops "[the]"/"<obj.>" fillers, leading pronouns/auxiliaries, trailing "of"),
  keeps those used at least MIN_COUNT times, and unions them into each word's
  alias list after the existing entries. Function-word surfaces ("him", "of")
  are never added: they would underline the wrong span.

  Additive and idempotent: existing aliases are kept, re-running adds nothing.

Usage:
  ./build_step_aliases.py --report     # print a sample; write nothing
  ./build_step_aliases.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from step_data import iter_tagged_words

HERE = Path(__file__).resolve().parent
ALIASES_JSON = HERE.parent / 'src' / 'generated' / '_aliases.json'

MIN_COUNT = 2      # a rendering used once is too often a paraphrase
MAX_PER_WORD = 20  # most-used first
MAX_WORDS = 4      # longer renderings are clauses, not surfaces

# Leading words STEP folds into a rendering from the verb's person/tense or a
# fused article; stripped so "they were fruitful" yields "fruitful".
LEADING = frozenset(
    'a an the he she it they we you i his her its their our your my '
    'was were is are be been being will shall would should may might must '
    'has have had do does did let and but so then not no o'.split()
)
TRAILING = frozenset('of to for from with in on at by'.split())
# Never an alias: these match too much unrelated text.
STOP = LEADING | TRAILING | frozenset(
    'him them me us who whom which what that this these those there here '
    'all as if or when than up out into upon over under one'.split()
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def surface(english: str, keep_case: bool = False) -> str | None:
    """The surface one rendering contributes, e.g. "and/ [the] people of" →
    "people"; "they were fruitful" → "fruitful". None when nothing is left
    but filler, or the rendering is a clause rather than a word or phrase.
    Lowercase unless keep_case (tokens keep "Israel"; aliases are lowercase)."""
    text = re.sub(r'\[[^\]]*\]|<[^>]*>|\{[^}]*\}|\([^)]*\)', ' ', english)
    words = WORD_RE.findall(text if keep_case else text.lower())
    while words and (words[0].lower() in LEADING or words[0].lower() in TRAILING):
        words = words[1:]
    while words and words[-1].lower() in TRAILING:
        words = words[:-1]
    if not words or len(words) > MAX_WORDS or all(w.lower() in STOP for w in words):
        return None
    s = ' '.join(words)
    return s if len(s) > 1 else None


def collect(step_dir: Path | None) -> dict[str, list[str]]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for w in iter_tagged_words(step_dir):
        s = surface(w['english'])
        if s:
            counts[w['strongs']][s] += 1
    return {
        strongs: [s for s, n in c.most_common() if n >= MIN_COUNT][:MAX_PER_WORD]
        for strongs, c in counts.items()
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--report', action='store_true', help='print a sample; write nothing')
    ap.add_argument('--step-dir', type=Path, help='local STEPBible-Data checkout (skips download)')
    args = ap.parse_args()

    aliases = json.loads(ALIASES_JSON.read_text(encoding='utf-8'))
    meta = aliases.pop('_meta', {})
    step = collect(args.step_dir)

    words_touched = added = 0
    for strongs, found in step.items():
        current = aliases.get(strongs, [])
        seen = {s.lower() for s in current}
        new = [s for s in found if s not in seen]
        if new:
            aliases[strongs] = current + new
            words_touched += 1
            added += len(new)
    print(f'{words_touched:,} words gained {added:,} surfaces '
          f'({len(aliases):,} words now have aliases).')
    for probe in ('G0080', 'H1121', 'H5869', 'H7307', 'H3478', 'H7462'):
        print(f'   {probe}: {aliases.get(probe, [])[:10]}')
    if args.report or not added:
        if args.report:
            print('\n--report: no file written.')
        return 0

    meta['generated'] = (
        'generate_aliases.py (LLM, top ~2,000 words), kjv_aliases.py (KJV archaic '
        'forms), build_step_aliases.py (STEP TAHOT/TAGNT English renderings)'
    )
    ordered = {'_meta': meta, **dict(sorted(aliases.items()))}
    ALIASES_JSON.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nwrote {ALIASES_JSON.relative_to(HERE.parent)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
