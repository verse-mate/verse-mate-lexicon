/**
 * @versemate/lexicon — public entry point.
 *
 * Provides chapter-aligned Greek/Hebrew lexicon data for the VerseMate
 * Bible reader. Consumed by verse-mate-web (Vite) and verse-mate-mobile
 * (Expo / React Native). Both bundlers can statically resolve the
 * generated chapter manifest in `manifest.generated.ts`.
 *
 * Public API:
 *   - `loadAlignmentFor(bookId, chapter)`: async, returns the merged
 *     ChapterAlignment (hand-curated overrides win over generated).
 *   - `getBookSlug(bookId)`: helper for callers that need the slug
 *     directly (e.g. building a contextual cache key).
 *   - Type re-exports.
 *
 * Bundler notes:
 *   - Metro: `manifest.generated.ts` is 1,189 literal `import()` paths,
 *     which Metro static-analyses at build time. Each chapter is its
 *     own code-split chunk.
 *   - Vite: `import()` works the same way — Vite is happy with literal
 *     paths and lazily fetches each chapter.
 *
 * The `_lemmas.json` (16 MB) and `_contextual.json` files are also
 * `import()`-ed lazily and cached at the module level so the user pays
 * the cost at most once per session.
 */

import { CHAPTER_LOADERS } from './manifest.generated';
import { JAMES_1_ALIGNMENT } from './james-1';
import { JAMES_2_ALIGNMENT } from './james-2';
import { JAMES_3_ALIGNMENT } from './james-3';
import { JAMES_4_ALIGNMENT } from './james-4';
import { JAMES_5_ALIGNMENT } from './james-5';
import { LEXICON as HAND_LEXICON } from './lemmas';
import { getBookSlug } from './book-slugs';
import type {
  ChapterAlignment,
  LemmaKey,
  LexEntry,
} from './types';
import type {
  ContextualGlosses,
  GeneratedAlignment,
  GeneratedLexicon,
} from './internal-types';

// Hand-curated James alignments take precedence over generated when both
// exist. Keyed by `${bookId}:${chapter}`.
const HAND_ALIGNMENTS: Record<string, ChapterAlignment> = {
  '59:1': JAMES_1_ALIGNMENT,
  '59:2': JAMES_2_ALIGNMENT,
  '59:3': JAMES_3_ALIGNMENT,
  '59:4': JAMES_4_ALIGNMENT,
  '59:5': JAMES_5_ALIGNMENT,
};

let generatedLexiconPromise: Promise<GeneratedLexicon> | null = null;
function loadGeneratedLexicon(): Promise<GeneratedLexicon> {
  if (!generatedLexiconPromise) {
    generatedLexiconPromise = import('./generated/_lemmas.json').then(
      (m) => m.default as GeneratedLexicon,
    );
  }
  return generatedLexiconPromise;
}

let contextualPromise: Promise<ContextualGlosses> | null = null;
function loadContextual(): Promise<ContextualGlosses> {
  if (!contextualPromise) {
    contextualPromise = import('./generated/_contextual.json').then((m) => {
      // The _meta key is informational only — strip it so it can never
      // accidentally match a lemma lookup.
      const { _meta, ...rest } = m.default as ContextualGlosses & {
        _meta?: unknown;
      };
      void _meta;
      return rest as ContextualGlosses;
    });
  }
  return contextualPromise;
}

// Cache: alignments by `${bookId}:${chapter}`. Hand-curated chapters are
// seeded at module init so they hit immediately without an await.
const alignmentCache: Map<string, ChapterAlignment> = new Map(
  Object.entries(HAND_ALIGNMENTS),
);

/**
 * Async lookup. Hand-curated chapters return synchronously (via a
 * resolved promise). Generated chapters lazy-load their per-chapter
 * JSON + (on first hit) the shared lemmas file, then merge with
 * hand-curated overrides so theologically loaded words keep their
 * richer entries.
 *
 * Returns `null` if the book/chapter has no alignment data.
 */
export async function loadAlignmentFor(
  bookId: number,
  chapter: number,
): Promise<ChapterAlignment | null> {
  const key = `${bookId}:${chapter}`;
  const cached = alignmentCache.get(key);
  if (cached) return cached;

  const slug = getBookSlug(bookId);
  if (!slug) return null;
  const loader = CHAPTER_LOADERS[`${slug}-${chapter}`];
  if (!loader) return null;

  const [chapterMod, generatedLexicon, contextual] = await Promise.all([
    loader(),
    loadGeneratedLexicon(),
    loadContextual(),
  ]);
  const raw: GeneratedAlignment = chapterMod.default;

  // Hand-curated lemma entries win on collision — preserves the rich
  // James-1 contextual glosses + semantic ranges for words like λόγος,
  // ὑπομονή.
  const mergedLexicon: Record<LemmaKey, LexEntry> = {
    ...generatedLexicon,
    ...HAND_LEXICON,
  };

  // Inject per-occurrence contextual glosses for loaded lemmas.
  const mergedVerses: ChapterAlignment['verses'] = {};
  for (const [verseStr, tokens] of Object.entries(raw.verses)) {
    mergedVerses[Number(verseStr)] = (
      tokens as { surface: string; lemma: string }[]
    ).map((t) => {
      const k = `${slug}:${raw.chapter}:${verseStr}:${t.lemma}`;
      return contextual[k] ? { ...t, contextual: contextual[k] } : t;
    });
  }

  const alignment: ChapterAlignment = {
    bookId: raw.bookId,
    book: raw.book,
    chapter: raw.chapter,
    version: raw.version,
    verses: mergedVerses,
    lexicon: mergedLexicon,
    themeLemmas: raw.themeLemmas,
  };
  alignmentCache.set(key, alignment);
  return alignment;
}

export { getBookSlug, BOOK_SLUGS } from './book-slugs';
export type {
  ChapterAlignment,
  LexEntry,
  AlignedToken,
  LemmaKey,
  RelatedWord,
} from './types';
