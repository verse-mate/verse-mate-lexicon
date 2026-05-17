// Shapes that match the generated JSON exactly, used internally by the
// loader to type-check the manifest. Public consumers should use the
// merged shapes from `./types` (ChapterAlignment, LexEntry, AlignedToken).

import type { LemmaKey, LexEntry } from './types';

export interface GeneratedAlignment {
  bookId: number;
  book: string;
  chapter: number;
  version: string;
  verses: Record<string, { surface: string; lemma: string }[]>;
  themeLemmas?: string[];
}

export type GeneratedLexicon = Record<LemmaKey, LexEntry>;

/**
 * Per-occurrence contextual glosses for loaded NT lemmas. Keyed by
 * `<book-slug>:<chapter>:<verse>:<lemma>`. When a token has an entry
 * here, the popover's "In this verse" section appears.
 */
export type ContextualGlosses = Record<string, string>;
