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
git commit -am "lexicon: refresh from MorphGNT/BSB ingest"
```

Then bump the SHA in consumers' `bun.lock`:

```bash
cd verse-mate-web && bun update @versemate/lexicon
cd ../verse-mate-mobile && bun update @versemate/lexicon
```

## License

Private. Matches the consuming apps.
