#!/usr/bin/env python3
"""Prove the light path resolves every lemma and every Strong's number exactly like the full path.

Why this exists
---------------
`{ lite: true }` swaps the data source under the renderer, so the two paths must agree on two things or
readers silently get different results:

1. **Which lemmas have an entry.** The renderer's `if (!entry) continue` gates the underline, so a slug
   missing from the light path loses its underline.
2. **Which slug a Strong's number resolves to.** `strongsToSlug` is FIRST-WRITER-WINS over the merged
   lexicon's key order, and that decides which SENSE a homograph shows (Hebrew אֵת obj-marker H0853 vs
   plowshare H0855, עֵת "time" H6256 — all slugify to "et"). Iterate in a different order and every
   homograph quietly resolves to a different definition.

Point 2 is not hypothetical: the first version of the generator sorted the slugs, which would have
diverged from `_lemmas.json`'s own order and broken exactly the homograph disambiguation that the
per-token Strong's work was built to fix.

This replicates BOTH implementations in Python — the TypeScript `getStrongsToSlug(getMergedLexicon(...))`
and `lightStrongsToSlug(...)` — against the real data, and diffs them.

    python3 scripts/verify_light_parity.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "src" / "generated" / "_lemmas.json"
LIGHT = ROOT / "src" / "generated" / "_lemmas-light.json"
HAND_TS = ROOT / "src" / "lemmas.ts"


def normalize_strongs(value) -> str | None:
    """Mirror of `normalizeStrongs` in src/index.ts: canonical G####/H#### form."""
    if not isinstance(value, str):
        return None
    # EXACTLY the TS regex — /^([GH])(\d+)([A-Za-z]?)$/ on the trimmed string, digits padded to 4.
    # A looser version here would be symmetric across both maps and so would not break the diff, but it
    # would inflate the reported count with keys the real implementation rejects.
    m = re.fullmatch(r"([GH])(\d+)([A-Za-z]?)", value.strip())
    if not m:
        return None
    return f"{m.group(1)}{m.group(2).zfill(4)}"


def hand_strongs() -> dict[str, str]:
    """Slug -> Strong's for HAND_LEXICON, scraped from the TS source.

    Deliberately a scrape and deliberately shallow: this only needs each hand entry's slug and its
    `strongs`, and a real TS parse would be a dependency for no extra confidence. If the scrape finds
    nothing the check fails loudly rather than silently comparing without the overrides.
    """
    src = HAND_TS.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    # Entries look like:  slug: { ... strongs: 'G3056', ... }
    for m in re.finditer(r"^\s{2}([A-Za-z0-9_]+):\s*\{", src, re.MULTILINE):
        slug = m.group(1)
        tail = src[m.end() : m.end() + 4000]
        s = re.search(r"strongs:\s*'([^']+)'", tail)
        if s:
            out[slug] = s.group(1)
    return out


def full_path_map(full: dict, hand: dict[str, str]) -> dict[str, str]:
    """getStrongsToSlug(getMergedLexicon(generated)) — first-writer-wins over the SPREAD's key order.

    A spread does not move an existing key, so an overridden slug keeps its position in the generated
    order and contributes the HAND value there; hand-only slugs are appended after.
    """
    merged_order = list(full.keys()) + [k for k in hand if k not in full]
    out: dict[str, str] = {}
    for slug in merged_order:
        raw = hand.get(slug, (full.get(slug) or {}).get("strongs"))
        key = normalize_strongs(raw)
        if key and key not in out:
            out[key] = slug
    return out


def light_path_map(light: dict, hand: dict[str, str]) -> dict[str, str]:
    """lightStrongsToSlug(...) — the columnar implementation in src/index.ts."""
    slugs = light["slugs"]
    strongs = light["strongs"]
    present = set(slugs)
    out: dict[str, str] = {}
    for i, slug in enumerate(slugs):
        key = normalize_strongs(hand.get(slug, strongs[i]))
        if key and key not in out:
            out[key] = slug
    for slug, raw in hand.items():
        if slug in present:
            continue
        key = normalize_strongs(raw)
        if key and key not in out:
            out[key] = slug
    return out


def main() -> int:
    full = json.loads(FULL.read_text(encoding="utf-8"))
    light = json.loads(LIGHT.read_text(encoding="utf-8"))
    hand = hand_strongs()
    if not hand:
        print("FAIL: scraped 0 HAND_LEXICON entries from src/lemmas.ts", file=sys.stderr)
        return 1
    print(f"HAND_LEXICON entries with a Strong's: {len(hand)}")

    failures = 0

    # 1. entry existence — a slug missing here loses its underline
    full_slugs = {k for k, v in full.items() if isinstance(v, dict)}
    light_slugs = set(light["slugs"])
    missing = full_slugs - light_slugs
    extra = light_slugs - full_slugs
    if missing or extra:
        failures += 1
        print(f"FAIL entry existence: {len(missing)} missing, {len(extra)} extra")
        print("   e.g. missing:", sorted(missing)[:5])
    else:
        print(f"OK   entry existence: {len(light_slugs)} slugs match exactly")

    # 2. column order — decides homograph resolution
    if [k for k, v in full.items() if isinstance(v, dict)] != light["slugs"]:
        failures += 1
        print("FAIL column order differs from _lemmas.json (homographs would resolve differently)")
    else:
        print("OK   column order identical to _lemmas.json")

    # 3. the Strong's map itself
    a = full_path_map(full, hand)
    b = light_path_map(light, hand)
    if a != b:
        failures += 1
        diff = [k for k in set(a) | set(b) if a.get(k) != b.get(k)]
        print(f"FAIL strongsToSlug differs on {len(diff)} Strong's numbers")
        for k in sorted(diff)[:10]:
            print(f"   {k}: full={a.get(k)!r} light={b.get(k)!r}")
    else:
        print(f"OK   strongsToSlug identical across both paths ({len(a)} Strong's numbers)")

    # 4. the fields the renderer reads at render time
    bad = []
    cols = {f: light[f] for f in ("lemma", "translit", "strongs", "basicGloss")}
    pos_vocab = light["posVocab"]
    for i, slug in enumerate(light["slugs"]):
        src = full[slug]
        for f, col in cols.items():
            if (src.get(f) or "") != (col[i] or ""):
                bad.append((slug, f))
                break
        else:
            pi = light["pos"][i]
            if (src.get("pos") or "") != (pos_vocab[pi] if pi >= 0 else ""):
                bad.append((slug, "pos"))
    if bad:
        failures += 1
        print(f"FAIL {len(bad)} entries have a field mismatch, e.g. {bad[:5]}")
    else:
        print("OK   lemma/translit/strongs/pos/basicGloss identical for all 18,100 entries")

    # 5. the `loaded` flag (drives the context-sensitive marker)
    loaded_light = {light["slugs"][i] for i in light["loadedIdx"]}
    loaded_full = {k for k, v in full.items() if isinstance(v, dict) and v.get("loaded") is True}
    if loaded_light != loaded_full:
        failures += 1
        print(f"FAIL `loaded` differs: {len(loaded_full ^ loaded_light)} slugs")
    else:
        print(f"OK   `loaded` flag identical ({len(loaded_full)} entries)")

    print("\nPARITY OK" if not failures else f"\n{failures} PARITY FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
