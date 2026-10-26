#!/usr/bin/env python3
"""
Generate cross-translation English surface aliases for lemmas.

For each lemma we already have basic data for (in src/generated/_lemmas.json),
ask an LLM (OpenAI by default, or Anthropic) to enumerate the English surfaces
that major English Bibles (KJV, NASB, ESV, NIV, BSB, NLT, ASV, NRSV) use to
render that Greek/Hebrew lemma. Merge into src/generated/_aliases.json, keyed
by canonical Strong's
(G####/H####) — not slug, since slugs collapse homographs and the loader
looks aliases up by each token's own Strong's.

The renderer (in src/index.ts) unions these aliases with the BSB surface
from each generated alignment token. So a token like {surface: "perseverance",
lemma: "hupomone"} gets its surface expanded to ["perseverance", "endurance",
"steadfastness", "patience", "longsuffering"] — the substring scan then
matches whichever the served translation happens to use.

New aliases are MERGED into a word's existing list (appended, deduplicated),
never replacing it: the list also holds KJV and STEP renderings written by
kjv_aliases.py and build_step_aliases.py.

Usage:
  pip install openai                        # or: pip install anthropic
  export OPEN_AI_KEY=...                    # or OPENAI_API_KEY
  ./generate_aliases.py --aligned           # every Strong's in the chapter files
  ./generate_aliases.py --top 2000          # top by combined NT+OT freq
  ./generate_aliases.py --all               # every lemma in _lemmas.json
  ./generate_aliases.py --lemmas hupomone,doulos,agape   # specific slugs
  ./generate_aliases.py --dry-run --top 3   # preview prompts, no API call

Tuning:
  --provider anthropic                       # ANTHROPIC_API_KEY, Claude Haiku
  --model gpt-5                              # default gpt-5-mini (openai)
  --reasoning-effort low                     # OpenAI reasoning models only
  --concurrency 8                            # parallel workers (default 8)
  --no-skip-existing                         # redo words already generated

Resume: every word that comes back is recorded in scripts/.generate_aliases.done
(gitignored), and later runs skip those words. An interrupted run picks up
where it stopped; failed words are not recorded, so a re-run retries them.
Delete the file (or pass --no-skip-existing) to regenerate from scratch.

Cost: run a small batch first (--top 50), read its usage in the provider
dashboard and scale by the target count the script prints.
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

DONE_FILE = HERE / '.generate_aliases.done'

DEFAULT_MODELS = {
    'openai': 'gpt-5-mini',
    'anthropic': 'claude-haiku-4-5-20251001',
}
# Env vars checked for each provider's key, in order. OPEN_AI_KEY is the
# backend's name for it.
API_KEY_ENV = {
    'openai': ('OPEN_AI_KEY', 'OPENAI_API_KEY'),
    'anthropic': ('ANTHROPIC_API_KEY',),
}

STRONGS_RE = re.compile(r'^([GH])0*(\d+)')


def strongs_key(entry: dict) -> str | None:
    """Canonical G####/H#### — the key _aliases.json is stored under."""
    m = STRONGS_RE.match((entry.get('strongs') or '').strip())
    return f'{m.group(1)}{m.group(2).zfill(4)}' if m else None


MAX_TOKENS = 512
# OpenAI reasoning models spend completion tokens on reasoning before the
# answer, so they need more headroom than the ~100-token JSON reply.
OPENAI_MAX_COMPLETION_TOKENS = 4000
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


def parse_aliases(text: str) -> list[str]:
    """The model's JSON reply → clean aliases: lowercase, no leading article,
    no surrounding punctuation, deduplicated."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    aliases = json.loads(text).get('aliases', [])
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
    return clean


def merge_aliases(existing: list[str], new: list[str]) -> list[str]:
    """Existing order first, then new entries not already present."""
    seen = {a.lower() for a in existing}
    out = list(existing)
    for a in new:
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


async def complete(client, provider: str, model: str, prompt: str,
                   reasoning_effort: str | None) -> str:
    if provider == 'openai':
        kwargs = {}
        if reasoning_effort:
            kwargs['reasoning_effort'] = reasoning_effort
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ],
            response_format={'type': 'json_object'},
            max_completion_tokens=OPENAI_MAX_COMPLETION_TOKENS,
            **kwargs,
        )
        return resp.choices[0].message.content or ''
    msg = await client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return msg.content[0].text


async def fetch_aliases(client, provider: str, slug: str, entry: dict,
                        sem: asyncio.Semaphore, model: str,
                        reasoning_effort: str | None
                        ) -> tuple[str, list[str] | None, str | None]:
    async with sem:
        try:
            text = await complete(client, provider, model,
                                  build_user_prompt(slug, entry), reasoning_effort)
            return slug, parse_aliases(text), None
        except Exception as e:
            return slug, None, str(e)


async def run_generation(targets: list[str], lemmas: dict, aliases_out: dict,
                         done_keys: set[str], client, provider: str, model: str,
                         concurrency: int, reasoning_effort: str | None
                         ) -> tuple[int, int]:
    sem = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(fetch_aliases(client, provider, slug, lemmas[slug],
                                          sem, model, reasoning_effort))
        for slug in targets
    ]
    ok = 0
    fail = 0
    done = 0
    start = time.time()
    for fut in asyncio.as_completed(tasks):
        slug, aliases, err = await fut
        done += 1
        key = strongs_key(lemmas[slug])
        if aliases and key:
            aliases_out[key] = merge_aliases(aliases_out.get(key, []), aliases)
            done_keys.add(key)
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
            _write(aliases_out, done_keys)
    _write(aliases_out, done_keys)
    return ok, fail


def aligned_strongs() -> set[str]:
    """Every Strong's number a token in the chapter files carries."""
    found = set()
    for path in GENERATED_DIR.glob('[!_]*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        for tokens in data.get('verses', {}).values():
            for t in tokens:
                key = strongs_key(t)
                if key:
                    found.add(key)
    return found


def _read_done() -> set[str]:
    if not DONE_FILE.exists():
        return set()
    return {line.strip() for line in DONE_FILE.read_text().splitlines() if line.strip()}


def _write(aliases_out: dict, done_keys: set[str]) -> None:
    """Persist — copy _meta header from any existing file, merge in the
    freshly-generated entries, and write back. Then record finished words."""
    existing_meta = {}
    if ALIASES_JSON.exists():
        try:
            existing = json.loads(ALIASES_JSON.read_text())
            if '_meta' in existing:
                existing_meta = {'_meta': existing['_meta']}
        except Exception:
            pass
    out = {**existing_meta, **{k: v for k, v in aliases_out.items() if k != '_meta'}}
    tmp = ALIASES_JSON.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    tmp.replace(ALIASES_JSON)
    DONE_FILE.write_text(''.join(f'{k}\n' for k in sorted(done_keys)))


def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--aligned', action='store_true',
                   help="Every Strong's number that appears in the chapter files")
    g.add_argument('--all', action='store_true', help='Every lemma in _lemmas.json')
    g.add_argument('--top', type=int, help='Top-N by combined NT+OT frequency')
    g.add_argument('--lemmas', help='Comma-separated lemma slugs')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print prompts; no API; no write')
    parser.add_argument('--provider', choices=sorted(DEFAULT_MODELS), default='openai')
    parser.add_argument('--model', help='default: gpt-5-mini (openai), '
                        'claude-haiku-4-5 (anthropic)')
    parser.add_argument('--reasoning-effort',
                        help='OpenAI reasoning models only, e.g. minimal/low/medium')
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument('--no-skip-existing', action='store_true',
                        help=f'Redo words already listed in {DONE_FILE.name}')
    args = parser.parse_args()
    model = args.model or DEFAULT_MODELS[args.provider]

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
    elif args.aligned:
        # One slug per Strong's: aliases are keyed by Strong's, so homograph
        # slugs sharing a number would only pay twice for the same list.
        wanted = aligned_strongs()
        by_key: dict[str, str] = {}
        for slug, entry in lemmas.items():
            key = strongs_key(entry)
            if key in wanted:
                by_key.setdefault(key, slug)
        targets = list(by_key.values())
    elif args.top:
        def freq(e):
            return (e.get('ntFrequency') or 0) + (e.get('otFrequency') or 0)
        ranked = sorted(lemmas.items(), key=lambda kv: -freq(kv[1]))
        targets = [slug for slug, _ in ranked[:args.top]]
    else:
        targets = list(lemmas.keys())

    targets = [t for t in targets if t in lemmas and strongs_key(lemmas[t])]
    done_keys = set() if args.no_skip_existing else _read_done()
    if done_keys:
        before = len(targets)
        targets = [t for t in targets if strongs_key(lemmas[t]) not in done_keys]
        print(f'Skipping {before - len(targets)} words already generated '
              f'({DONE_FILE.name}).')

    print(f'Will generate aliases for {len(targets):,} lemmas '
          f'(provider={args.provider}, model={model}, '
          f'concurrency={args.concurrency}).')

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

    env_names = API_KEY_ENV[args.provider]
    api_key = next((os.environ[n] for n in env_names if os.environ.get(n)), None)
    if not api_key:
        print(f'ERROR: set {" or ".join(env_names)}.', file=sys.stderr)
        return 1
    try:
        if args.provider == 'openai':
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, max_retries=5)
        else:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key, max_retries=5)
    except ImportError:
        print(f'ERROR: pip install {args.provider}', file=sys.stderr)
        return 1

    ok, fail = asyncio.run(run_generation(
        targets, lemmas, aliases_out, done_keys, client, args.provider, model,
        args.concurrency, args.reasoning_effort))
    print(f'\nDone. Generated: {ok}, failed: {fail}.')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
