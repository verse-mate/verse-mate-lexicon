# @versemate/lexicon

Chapter-aligned Greek/Hebrew lexicon for VerseMate. Maps English surface
forms in each Bible chapter to their underlying lemma + per-occurrence
contextual gloss, with a shared lemma table for the dictionary card.

Shared by **verse-mate-web** (Vite SPA) and **verse-mate-mobile** (Expo
React Native). Both consume it lazily — only the chapters a user opens
get fetched.

## Usage

```ts
import { loadAlignmentFor, type ChapterAlignment } from '@versemate/lexicon';

const alignment = await loadAlignmentFor(43, 3); // John 3
if (alignment) {
  // alignment.verses[16] → [{ surface: "loved", lemma: "agapao", contextual: "..." }, ...]
  // alignment.lexicon["agapao"] → { lemma: "ἀγαπάω", translit: "agapaō", basicGloss: "...", ... }
}
```

## Coverage

- Hand-curated: James 1–5 (rich semantic-range, theologically-loaded
  word notes pastor-reviewed before merge)
- Generated: every NT and OT chapter (1,189 total) — auto-aligned via
  the MorphGNT + BSB ingest pipeline, merged with hand-curated lemmas
  where they overlap

`themeLemmas` per chapter — the renderer uses these to give chapter-
central words a more prominent visual treatment.

## Bundler notes

- **Metro** (React Native): the chapter manifest in
  `src/manifest.generated.ts` is 1,189 literal `import()` paths. Metro
  static-analyses them at build time and produces one chunk per chapter.
  Each chapter is fetched on first access; the `_lemmas.json` global
  table (~16 MB raw, ~5 MB gzipped) is fetched once on the first
  generated-chapter open.
- **Vite** (web): same `import()` syntax. Vite's dev server hot-modules
  any change in `src/`.

### The light lexicon (`lite: true`)

`loadAlignmentFor` awaits the whole 18.7 MB `_lemmas.json` on the FIRST chapter a reader opens,
because it needs one field from it — `strongs` — to disambiguate homographs. On a phone that is a
single **~2 s block of the JS thread**, measured across four independent captures in
verse-mate-mobile (worst JS block 1991 / 1946 / 2163 / 2207 ms). It lands after first paint, so
startup is fine, but any swipe or tab switch inside that window freezes outright.

Almost none of that weight is needed to render a chapter. By field:

| field | size | needed to render? |
|---|---|---|
| `notes` | 5.54 MB | no — popover only |
| `related` | 4.51 MB | no — popover only |
| `semanticRange` | 2.09 MB | no — popover only |
| `lemma` / `pos` / `basicGloss` / `translit` / `strongs` / `loaded` | ~1.1 MB total | yes |

So there is a second, small file:

```bash
bun run build:light     # regenerate src/generated/_lemmas-light.json
bun run verify:light    # fail if it is stale (run this after any _lemmas.json regen)
```

```ts
// chapter load — never touches the 18.7 MB file
const alignment = await loadAlignmentFor(bookId, chapter, { lite: true });

// a reader taps a word — pay for the prose then
const entry = await lookupLemma(slug);
```

`lite` is **opt-in**, so existing callers are unaffected. The light file is columnar with a
dictionary-encoded `pos`, because on a projection this small the repeated field *names* cost more than
the values (2.48 MB row-oriented vs **1.15 MB**, 16x smaller than the full file). Every field in it is
one of `LexEntry`'s *required* fields, so a light entry satisfies `LexEntry` structurally and consumers
need no type changes — only whatever renders definitions has to call `lookupLemma`. The ~144
hand-curated `HAND_LEXICON` entries are merged in full on both paths, so theologically loaded words
keep their prose either way.

**`_lemmas-light.json` is generated — never edit it by hand, and re-run `build:light` whenever
`_lemmas.json` changes.** `verify:light` exists so a stale index fails loudly instead of silently
serving wrong Strong's numbers.

## Authoring pipeline

The generated chapter files and `_lemmas.json` / `_contextual.json` are
produced by `verse-mate-web/scripts/lexicon-ingest/build.py` (MorphGNT
+ BSB alignment for NT, BHS + ESV alignment for OT). After regen:

```bash
cd verse-mate-web
python scripts/lexicon-ingest/build.py
# script writes to verse-mate-lexicon/src/generated/
cd ../verse-mate-lexicon
node scripts/build-manifest.mjs  # regenerates src/manifest.generated.ts
python3 scripts/fix_dominant_senses.py   # headline gloss = most-used sense, not STEP's first row
python3 scripts/apply_enrichment.py scripts/data/reenrich_displaced_senses.json
python3 scripts/add_missing_tokens.py    # content words the BSB alignment never tagged
python3 scripts/build_step_aliases.py    # English renderings per Strong's, from STEP
python3 scripts/prune_aliases.py         # drop aliases that name another word in the verse
bun run build:light && bun run verify:parity
git commit -am "lexicon: refresh from MorphGNT/BSB ingest"
```

Each post-pass is idempotent (a re-run changes nothing) and reads
STEPBible's tagged OT/NT (CC BY), cached in `scripts/.step-cache/`:

- `fix_dominant_senses.py` only rewrites glosses that are still build.py's
  first-row default, so it never overrides a hand edit.
- `add_missing_tokens.py` appends a token for each content word the BSB
  tables left without English (e.g. Israel in "the Israelites"), using STEP's
  rendering as its surface.
- `build_step_aliases.py` adds the English each word is rendered with across
  the OT/NT to its alias list.
- `prune_aliases.py` runs last. It trims trailing prepositions ("said to" →
  "said") and drops aliases that STEP shows mostly belong to another word in
  the same verse (H3068 "LORD" claiming "god", H0259 "one" claiming "a").

### Aliases

`_aliases.json` is keyed by canonical Strong's (`G####` / `H####`), and the
loader looks each token's aliases up by its own Strong's, skipping any alias
that is another token's own surface in the same verse. It used to be keyed
by lemma slug, which collapses homographs, so one word could pick up another's
English surfaces (רוּחַ "Spirit" matching "smell", עַ֫יִן "eye" matching "no").
`generate_aliases.py` and `kjv_aliases.py` read and write Strong's keys.

`generate_aliases.py` asks an LLM (OpenAI by default; `OPEN_AI_KEY` or
`OPENAI_API_KEY`) for the wording modern translations use, e.g. NASB's
"deportation" for μετοικεσία where BSB prints "exile". It appends to each
word's existing list rather than replacing it, and records finished words in
`scripts/.generate_aliases.done` so an interrupted run resumes:

```bash
pip install openai
python3 scripts/generate_aliases.py --top 50     # trial; check usage
python3 scripts/generate_aliases.py --aligned    # every Strong's in the text
python3 scripts/kjv_aliases.py && python3 scripts/build_step_aliases.py
python3 scripts/prune_aliases.py
```

Then bump the SHA in consumers' `bun.lock`:

```bash
cd verse-mate-web && bun update @versemate/lexicon
cd ../verse-mate-mobile && bun update @versemate/lexicon
```

## License

Private. Matches the consuming apps.
