#!/usr/bin/env python3
"""
Merge reviewed enrichment (enrich.py's schema) into `_lemmas.json` by Strong's.

enrich.py generates pronunciation / semanticRange / related / notes through the
API. When an entry is written or corrected by hand instead (see
scripts/data/reenrich_displaced_senses.json), this applies it: every entry whose
Strong's matches gets those four fields replaced. Fields not in the input are
left alone, and re-running is a no-op.

Usage:
  ./apply_enrichment.py data/reenrich_displaced_senses.json
  ./apply_enrichment.py data/reenrich_displaced_senses.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from step_data import base_strongs

HERE = Path(__file__).resolve().parent
LEMMAS_JSON = HERE.parent / 'src' / 'generated' / '_lemmas.json'
FIELDS = ('pronunciation', 'semanticRange', 'related', 'notes')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', type=Path, help='JSON keyed by Strong\'s (G####/H####)')
    ap.add_argument('--dry-run', action='store_true', help='print changes; do not write')
    args = ap.parse_args()

    enrichment = json.loads(args.input.read_text(encoding='utf-8'))
    enrichment.pop('_meta', None)
    lemmas = json.loads(LEMMAS_JSON.read_text(encoding='utf-8'))

    changed = 0
    for slug, entry in lemmas.items():
        new = enrichment.get(base_strongs(entry.get('strongs')))
        if not new:
            continue
        diff = [f for f in FIELDS if f in new and entry.get(f) != new[f]]
        if diff:
            print(f"{entry['strongs']} {slug}: {', '.join(diff)}")
            for f in diff:
                entry[f] = new[f]
            changed += 1
    print(f'{changed} entries updated.')
    if changed and not args.dry_run:
        LEMMAS_JSON.write_text(json.dumps(lemmas, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Now run: bun run build:light')
    return 0


if __name__ == '__main__':
    sys.exit(main())
