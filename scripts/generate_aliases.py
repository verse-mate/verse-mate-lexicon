#!/usr/bin/env python3
"""
Generate cross-translation English surface aliases for the top-N lemmas.

For each lemma we already have basic data for (in src/generated/_lemmas.json),
ask Claude Haiku to enumerate the English surfaces that major English Bibles
(KJV, NASB, ESV, NIV, BSB, NLT, ASV, NRSV) use to render that Greek/Hebrew
lemma. Aggregate to src/generated/_aliases.json keyed by lemma slug.

The renderer (in src/index.ts) unions these aliases with the BSB surface
from each generated alignment token. So a token like {surface: "perseverance",
lemma: "hupomone"} gets its surface expanded to ["perseverance", "endurance",
"steadfastness", "patience", "longsuffering"] — the substring scan then
matches whichever the served translation happens to use.

Usage:
  export ANTHROPIC_API_KEY=...
  ./generate_aliases.py --top 2000          # top by combined NT+OT freq
  ./generate_aliases.py --all               # every lemma in _lemmas.json
  ./generate_aliases.py --lemmas hupomone,doulos,agape   # specific slugs
  ./generate_aliases.py --dry-run --top 3   # preview prompts, no API call

Tuning:
  --model claude-sonnet-4-5-20250929        # Sonnet (~5x cost, slower)
  --concurrency 8                            # parallel workers (default 8)
  --no-skip-existing                         # re-pay for already-aliased lemmas

Cost / time targets (Haiku 4.5, 8 parallel):
  --top 500   ~$0.50    ~8 min
  --top 2000  ~$2       ~30 min
  --all       ~$10      ~2 hr
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATED_DIR = HERE.parent / 'src' / 'generated'
LEMMAS_JSON = GENERATED_DIR / '_lemmas.json'
ALIASES_JSON = GENERATED_DIR / '_aliases.json'

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
MAX_TOKENS = 512
DEFAULT_CONCURRENCY = 8
CHECKPOINT_EVERY = 50

SYSTEM_PROMPT = """You are a biblical-language scholar enumerating the English surface forms used by major English Bibles to render a given Greek or Hebrew lemma.

You will receive a lemma's basic data. Your job: list every distinct English word or short phrase that any of these published translations actually uses for this lemma in canonical scripture:

  KJV (1611, the only "thou/thee/begat/longsuffering" archaic variant)
  NASB (1995 / 2020 revisions)
  ESV (2001)
  NIV (1984 / 2011)
  BSB (Berean Standard Bible)
  NLT (New Living Translation)
  ASV (American Standard Version)
  NRSV (New Revised Standard Version)

Constraints:

1. Each entry is a SINGLE English surface (a word or short multi-word phrase) — lowercase, no leading article ("the", "a", "an"). Strip surrounding punctuation. Use the EXACT word a published translation prints, not a synonym.

2. Cover ALL the major grammatical forms used in published translations: noun → verb form distinctions ("endurance" / "endure"), singular → plural ("trial" / "trials"), participial forms ("enduring"), and any compound spellings (with-hyphen, without-hyphen).

3. Include KJV-only archaic surfaces where they exist (longsuffering, divers, charity-for-agape, quicken, comely, lasciviousness, behooved, sundry, verily-but only if used as the rendered surface for THIS lemma).

4. NEVER invent surfaces a translation doesn't actually use. If unsure, leave it out — false positives clutter the UI more than missing entries.

5. NEVER include articles, prepositions, conjunctions, pronouns as standalone entries (those are filtered at a different layer).

Return ONLY a JSON object with key "aliases" mapping to a string array. No surrounding prose. No code fences. Example:

{"aliases": ["perseverance", "endurance", "steadfastness", "patience", "longsuffering", "enduring"]}

If the lemma is a proper name (Paul, Jesus, Christ, Moses, Jerusalem), include only the spelling variants actually used (e.g. ["jesus"] not synonyms). If a lemma has only one universal English surface across all translations, return a single-element array."""


def build_user_prompt(slug: str, entry: dict) -> str:
    freq = entry.get('ntFrequency') or entry.get('otFrequency') or 0
    return f"""Lemma: {entry['lemma']}
Translit: {entry['translit']}
Strong's: {entry['strongs']}
POS: {entry['pos']}
Basic gloss: {entry['basicGloss']}
Frequency: {freq}× in {'NT' if entry.get('ntFrequency') else 'OT'}
Slug: {slug}

List every distinct English surface used for this lemma across KJV/NASB/ESV/NIV/BSB/NLT/ASV/NRSV in published scripture. Return JSON now."""


async def fetch_aliases(client, slug: str, entry: dict,
                        sem: asyncio.Semaphore, model: str
                        ) -> tuple[str, list[str] | None, str | None]:
    async with sem:
        try:
            msg = await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': build_user_prompt(slug, entry)}],
            )
            text = msg.content[0].text.strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            data = json.loads(text)
            aliases = data.get('aliases', [])
            # Sanitize: lowercase, strip whitespace + leading "the/a/an"
            clean = []
            seen = set()
            for a in aliases:
                if not isinstance(a, str):
                    continue
                a = a.strip().lower()
                a = re.sub(r'^(?:the |a |an )', '', a)
                a = a.strip(' .,;:!?"\'')
                if a and a not in seen:
                    seen.add(a)
                    clean.append(a)
            return slug, clean, None
        except Exception as e:
            return slug, None, str(e)


async def run_generation(targets: list[str], lemmas: dict, aliases_out: dict,
                         client, model: str, concurrency: int) -> tuple[int, int]:
    sem = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(fetch_aliases(client, slug, lemmas[slug], sem, model))
        for slug in targets
    ]
    ok = 0
    fail = 0
    done = 0
    start = time.time()
    for fut in asyncio.as_completed(tasks):
        slug, aliases, err = await fut
        done += 1
        if aliases:
            aliases_out[slug] = aliases
            ok += 1
        else:
            fail += 1
            if fail <= 3 or fail % 50 == 0:
                print(f'  WARN: {slug} failed: {err}', file=sys.stderr)
        if done % 20 == 0 or done == 1 or done == len(targets):
            elapsed = time.time() - start
            rate = done / max(elapsed, 1)
            eta = (len(targets) - done) / max(rate, 0.1) / 60
            print(f'[{done}/{len(targets)}] ok={ok} fail={fail} '
                  f'rate={rate:.1f}/s ~{eta:.0f} min left')
        if done % CHECKPOINT_EVERY == 0:
            _write(aliases_out)
    _write(aliases_out)
    return ok, fail


def _write(aliases_out: dict) -> None:
    """Persist atomically — copy _meta header from any existing file, merge
    in the freshly-generated entries, and write back."""
    existing_meta = {}
    if ALIASES_JSON.exists():
        try:
            existing = json.loads(ALIASES_JSON.read_text())
            if '_meta' in existing:
                existing_meta = {'_meta': existing['_meta']}
        except Exception:
            pass
    out = {**existing_meta, **{k: v for k, v in aliases_out.items() if k != '_meta'}}
    ALIASES_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--all', action='store_true', help='Every lemma in _lemmas.json')
    g.add_argument('--top', type=int, help='Top-N by combined NT+OT frequency')
    g.add_argument('--lemmas', help='Comma-separated lemma slugs')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print prompts; no API; no write')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument('--no-skip-existing', action='store_true',
                        help='Re-generate even for slugs already in _aliases.json')
    args = parser.parse_args()

    if not LEMMAS_JSON.exists():
        print(f'ERROR: {LEMMAS_JSON} not found.', file=sys.stderr)
        return 1
    lemmas = json.loads(LEMMAS_JSON.read_text())
    aliases_out = {}
    if ALIASES_JSON.exists():
        existing = json.loads(ALIASES_JSON.read_text())
        aliases_out = {k: v for k, v in existing.items() if k != '_meta'}

    # Pick targets.
    if args.lemmas:
        targets = [s.strip() for s in args.lemmas.split(',') if s.strip()]
    elif args.top:
        def freq(e):
            return (e.get('ntFrequency') or 0) + (e.get('otFrequency') or 0)
        ranked = sorted(lemmas.items(), key=lambda kv: -freq(kv[1]))
        targets = [slug for slug, _ in ranked[:args.top]]
    else:
        targets = list(lemmas.keys())

    targets = [t for t in targets if t in lemmas]
    if not args.no_skip_existing:
        before = len(targets)
        targets = [t for t in targets if t not in aliases_out]
        print(f'Skipping {before - len(targets)} already-aliased slugs.')

    print(f'Will generate aliases for {len(targets):,} lemmas '
          f'(model={args.model}, concurrency={args.concurrency}).')

    if args.dry_run:
        for slug in targets[:5]:
            print('\n--- DRY RUN ---')
            print(f'slug: {slug}')
            print(build_user_prompt(slug, lemmas[slug]))
        print(f'\n(dry-run printed first 5 of {len(targets):,} prompts)')
        return 0
    if not targets:
        print('Nothing to generate.')
        return 0

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print('ERROR: ANTHROPIC_API_KEY not set.', file=sys.stderr)
        return 1
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        print('ERROR: pip install anthropic', file=sys.stderr)
        return 1
    client = AsyncAnthropic()

    ok, fail = asyncio.run(run_generation(
        targets, lemmas, aliases_out, client, args.model, args.concurrency))
    print(f'\nDone. Generated: {ok}, failed: {fail}.')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
