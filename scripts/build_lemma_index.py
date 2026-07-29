#!/usr/bin/env python3
"""Emit a small "light" lexicon for chapter load, leaving the prose behind a lazy lookup.

Why
---
`_lemmas.json` is 18.7MB across 18,100 entries, and `loadAlignmentFor` awaits ALL of it on the first
chapter a reader opens. On a phone that is a single ~2s block of the JS thread, measured across four
independent captures in verse-mate-mobile (worst JS block 1991ms / 1946ms / 2163ms / 2207ms). It is
deferred past first paint so startup is unaffected, but any swipe or tab switch landing inside that
window freezes outright — while everything else being optimised in that reader is tens of milliseconds.

Almost none of that weight is needed to render a chapter. Measured by field:

    notes            5.54 MB
    related          4.51 MB
    semanticRange    2.09 MB     <- 12.1MB of 18.7MB is popover content
    lemma            0.32 MB
    pos              0.21 MB
    basicGloss       0.19 MB
    translit         0.18 MB
    strongs          0.13 MB
    loaded           0.09 MB

Chapter load needs: whether a lemma has an entry (the renderer's `if (!entry) continue` gates the
underline), its `strongs` (homograph disambiguation), and `translit` + `basicGloss` + `loaded`, which
the mobile renderer reads at render time for accessibility labels and the context-sensitive marker.
`notes`, `related` and `semanticRange` are only read when a reader TAPS a word.

Layout
------
Columnar, not row-oriented, because the field NAMES are what dominate a small projection: repeating
six keys 18,100 times costs more than the values. `pos` is dictionary-encoded (110 distinct values
across 18,100 entries).

    row-oriented (6 fields)   2.48 MB
    columnar                  1.42 MB
    columnar + pos vocab      1.24 MB   <- 15x smaller than the full file

Every field kept here is one of `LexEntry`'s REQUIRED fields (plus `loaded`), which is deliberate: a
light entry then satisfies `LexEntry` structurally, so consumers need no type changes and only the
popover — which reads the optional prose fields — has to opt into the lazy full lookup.

Scope
-----
Only the GENERATED lexicon. `src/lemmas.ts` (HAND_LEXICON, ~144 hand-curated entries) is a static
TypeScript import that is already bundled and cheap, so the loader merges it at runtime — mirroring
`getMergedLexicon`'s `{...generated, ...HAND_LEXICON}` precedence exactly. Baking it in here would mean
a Python script parsing TypeScript, and two places to keep in step.

Usage
-----
    python3 scripts/build_lemma_index.py            # write it
    python3 scripts/build_lemma_index.py --verify   # fail if stale (for CI)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "src" / "generated" / "_lemmas.json"
LIGHT = ROOT / "src" / "generated" / "_lemmas-light.json"

# `LexEntry`'s required fields, plus `loaded` (the UI's context-sensitive marker). Anything added here
# must stay cheap — the whole point is that this file is parsed on a phone's JS thread.
FIELDS = ["lemma", "translit", "strongs", "pos", "basicGloss"]
# Dictionary-encoded: 110 distinct values over 18,100 entries.
VOCAB_FIELDS = {"pos"}


def build(entries: dict) -> dict:
    """Columnar light lexicon. Column order matches `slugs`, index for index."""
    # ORIGINAL key order, not sorted. `strongsToSlug` is first-writer-wins, so the order decides which
    # slug a colliding Strong's number resolves to — and the full path walks `Object.entries` of the
    # merged lexicon, i.e. this file's order. Sorting here would silently pick a different sense for
    # every homograph (the exact bug the per-token Strong's work existed to fix).
    slugs = [k for k, v in entries.items() if isinstance(v, dict)]
    out: dict[str, object] = {"slugs": slugs}

    for field in FIELDS:
        values = [entries[s].get(field) for s in slugs]
        if field in VOCAB_FIELDS:
            vocab = sorted({v for v in values if isinstance(v, str)})
            lookup = {v: i for i, v in enumerate(vocab)}
            out[f"{field}Vocab"] = vocab
            # -1 for absent, so the loader can tell "no value" from "index 0".
            out[field] = [lookup.get(v, -1) if isinstance(v, str) else -1 for v in values]
        else:
            out[field] = values

    # `loaded` is a boolean flag on a minority of entries; a list of indices is far smaller than
    # 18,100 booleans, and the loader turns it back into a flag.
    out["loadedIdx"] = [i for i, s in enumerate(slugs) if entries[s].get("loaded") is True]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="exit non-zero if the committed light lexicon does not match _lemmas.json",
    )
    args = parser.parse_args()

    if not FULL.exists():
        print(f"missing {FULL}", file=sys.stderr)
        return 1

    light = build(json.loads(FULL.read_text()))

    if args.verify:
        if not LIGHT.exists():
            print(
                f"{LIGHT.name} is missing — run: python3 scripts/build_lemma_index.py",
                file=sys.stderr,
            )
            return 1
        if json.loads(LIGHT.read_text()) != light:
            print(
                f"{LIGHT.name} is STALE — it no longer matches _lemmas.json. "
                f"Run: python3 scripts/build_lemma_index.py",
                file=sys.stderr,
            )
            return 1
        print(f"{LIGHT.name} matches _lemmas.json ({len(light['slugs'])} entries)")
        return 0

    # MULTI-LINE, and that is not a style choice — a single-line file BREAKS THE APP.
    #
    # The first version wrote this with `separators=(",", ":")`, producing 1.15MB on one line. It is
    # valid JSON (Python parses it, `json.loads` round-trips), but on device every `import()` of it
    # rejected with:
    #
    #     SyntaxError: 13786:43:non-terminated string
    #
    # …five times, once per chapter load, leaving `alignment` null — no underlines, and a perf capture
    # that looked like a triumph because the work it was supposed to measure never ran. Note the
    # reported position: line 13786 in a file that has two lines. That mismatch is the tell that Hermes
    # is not parsing the bytes written here but a transform of them.
    #
    # `_lemmas.json` (18.7MB, far larger) has always worked, and the only structural difference is that
    # it is pretty-printed across 454,260 lines. Rewriting this file multi-line made the error disappear
    # and alignment resolve, verified on a real device. The exact mechanism inside Metro/Hermes is NOT
    # identified — what is established is the boundary, so this follows the format that is known to work
    # rather than the one that is 9% smaller.
    #
    # `indent=0` puts each array element on its own line: 110,906 lines, 1.26MB. The 0.11MB it costs
    # over compact separators buys a file that loads at all.
    LIGHT.write_text(
        json.dumps(light, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
    )

    # Fail loudly if anyone "optimises" the line count away again. A silent revert here does not look
    # like a crash: it looks like the lexicon quietly not loading, which measured as a 9x improvement.
    line_count = LIGHT.read_text(encoding="utf-8").count("\n")
    if line_count < 1000:
        raise SystemExit(
            f"{LIGHT.name} was written as {line_count} lines. A single-line file of this size fails to "
            f"parse on device (SyntaxError: non-terminated string) and silently disables the lexicon. "
            f"Keep it multi-line."
        )

    full_mb = os.path.getsize(FULL) / 1e6
    light_mb = os.path.getsize(LIGHT) / 1e6
    print(
        f"wrote {LIGHT.relative_to(ROOT)}: {len(light['slugs'])} entries, "
        f"{light_mb:.2f}MB vs {full_mb:.1f}MB full ({full_mb / light_mb:.0f}x smaller)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
