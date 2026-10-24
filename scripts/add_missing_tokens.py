#!/usr/bin/env python3
"""
Add the content words the BSB alignment never tagged to the chapter files.

PROBLEM
  The BSB tables attach one English phrase to one Hebrew/Greek word. When a
  phrase spans several words, the others get no English and build.py drops
  them: in "the Israelites" (literally "sons of Israel") the English sits on
  בֵּן "son", so יִשְׂרָאֵל "Israel" is lost — 503 times. ~19,600 content words
  (4.4% of the text) never reach the app, among them forever, David, name,
  soul and heart, even where NASB/KJV print the word plainly ("children of
  Israel").

WHAT THIS SCRIPT DOES
  Compares each verse's tokens with the words of STEP's tagged OT/NT
  (TAHOT/TAGNT), by Strong's. Every content word (same part-of-speech filter
  as build.py) present in the source but not in the alignment is appended as a
  token whose surface is STEP's English rendering of it, cleaned the same way
  build_step_aliases.py cleans aliases. The renderer only underlines a surface
  it finds in the displayed text, so a rendering the translation doesn't use
  stays invisible.

  Idempotent: a re-run finds every word already aligned and adds nothing.
  Run it after build.py and before build_step_aliases.py's output is needed;
  the order between the two doesn't matter.

Usage:
  ./add_missing_tokens.py --report
  ./add_missing_tokens.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from build_step_aliases import surface
from step_data import base_strongs, iter_tagged_words, parse_lexicons

HERE = Path(__file__).resolve().parent
GENERATED = HERE.parent / 'src' / 'generated'
LEMMAS_JSON = GENERATED / '_lemmas.json'
BOOK_SLUGS_TS = HERE.parent / 'src' / 'book-slugs.ts'

# build.py's SKIP_POS_PREFIXES: function-word classes that are never tokens.
SKIP_POS_PREFIXES = (
    'G:Conj', 'G:CONJ', 'G:Prep', 'G:PREP', 'G:Art', 'G:ART', 'G:T',
    'G:Pron', 'G:PRON', 'G:P', 'G:X', 'G:R', 'G:Q', 'G:D', 'G:F',
    'G:Part', 'G:PART', 'G:PRT', 'G:INJ', 'G:Num', 'G:COND',
    'H:Conj', 'H:Prep', 'H:Art', 'H:Pron', 'H:Part', 'H:PRT',
    'H:INJ', 'H:Intj', 'H:Num', 'H:T', 'H:P', 'H:X', 'H:R', 'H:Q', 'H:D', 'H:F',
    'H:DemP',
    'A:Conj', 'A:Prep', 'A:Art', 'A:Pron', 'A:Part', 'A:PRT',
    'A:INJ', 'A:Intj', 'A:Num', 'A:DemP',
)


def is_content_pos(code: str) -> bool:
    return not any(code.strip().startswith(p) for p in SKIP_POS_PREFIXES)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--report', action='store_true', help='print counts; write nothing')
    ap.add_argument('--step-dir', type=Path, help='local STEPBible-Data checkout (skips download)')
    args = ap.parse_args()

    slugs = {int(n): s for n, s in re.findall(r"(\d+): '([^']+)'", BOOK_SLUGS_TS.read_text())}
    lemmas = json.loads(LEMMAS_JSON.read_text(encoding='utf-8'))
    slug_for: dict[str, str] = {}
    for slug, entry in lemmas.items():
        slug_for.setdefault(base_strongs(entry.get('strongs')), slug)
    first, _ = parse_lexicons(args.step_dir)
    content = {b for b, row in first.items() if is_content_pos(row['pos'])}

    source: dict[tuple[int, int], dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for w in iter_tagged_words(args.step_dir):
        source[(w['book_id'], w['chapter'])][w['verse']].append(w)

    added = Counter()
    files_changed = 0
    for (book_id, chapter), verses in sorted(source.items()):
        path = GENERATED / f'{slugs[book_id]}-{chapter}.json'
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        changed = False
        for verse, words in sorted(verses.items()):
            tokens = data['verses'].setdefault(str(verse), [])
            aligned = Counter(base_strongs(t.get('strongs')) for t in tokens)
            # Per word, the source occurrences that can become a token. A
            # rendering that is all filler ("they will be") never can, so it is
            # left out of the count too; otherwise a re-run would let it claim a
            # token added for a sibling occurrence and add that one again.
            renderable: dict[str, list[str]] = defaultdict(list)
            for w in words:
                strongs = w['strongs']
                if strongs in content and strongs in slug_for and not strongs.startswith('H9'):
                    text = surface(w['english'], keep_case=True)
                    if text:
                        renderable[strongs].append(text)
            for strongs, texts in renderable.items():
                for text in texts[aligned[strongs]:]:
                    tokens.append({'surface': text, 'lemma': slug_for[strongs], 'strongs': strongs})
                    added[strongs] += 1
                    changed = True
            if not tokens:
                del data['verses'][str(verse)]
        if changed:
            files_changed += 1
            if not args.report:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'{sum(added.values()):,} tokens added across {files_changed:,} chapter files.')
    for strongs, n in added.most_common(12):
        print(f"   {strongs} {lemmas[slug_for[strongs]].get('basicGloss')!r}: +{n}")
    if args.report:
        print('\n--report: no files written.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
