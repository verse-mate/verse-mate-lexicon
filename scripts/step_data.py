"""
Shared readers for STEPBible-Data (CC BY 4.0), used by the post-passes that
correct the generated lexicon against the original-language text:

  fix_dominant_senses.py   headline gloss = the sense a word mostly carries
  add_missing_tokens.py    content words the BSB alignment never tagged
  build_step_aliases.py    English surfaces each word is rendered with

Files download once into scripts/.step-cache/ (gitignored); pass a local
STEPBible-Data checkout with --step-dir to skip the download.
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / '.step-cache'

STEP_RAW = 'https://raw.githubusercontent.com/STEPBible/STEPBible-Data/master/'
LEXICON_FILES = [
    'Lexicons/TBESH - Translators Brief lexicon of Extended Strongs for Hebrew - STEPBible.org CC BY.txt',
    'Lexicons/TBESG - Translators Brief lexicon of Extended Strongs for Greek - STEPBible.org CC BY.txt',
]
TAGGED_FILES = [
    'Translators Amalgamated OT+NT/TAHOT Gen-Deu - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAHOT Jos-Est - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAHOT Job-Sng - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAHOT Isa-Mal - Translators Amalgamated Hebrew OT - STEPBible.org CC BY.txt',
    'Translators Amalgamated OT+NT/TAGNT Mat-Jhn - Translators Amalgamated Greek NT - STEPBible.org CC-BY.txt',
    'Translators Amalgamated OT+NT/TAGNT Act-Rev - Translators Amalgamated Greek NT - STEPBible.org CC-BY.txt',
]

# STEP book abbreviations in canonical order; index + 1 is the app's bookId.
STEP_BOOKS = (
    'Gen Exo Lev Num Deu Jos Jdg Rut 1Sa 2Sa 1Ki 2Ki 1Ch 2Ch Ezr Neh Est Job Psa '
    'Pro Ecc Sng Isa Jer Lam Ezk Dan Hos Jol Amo Oba Jon Mic Nam Hab Zep Hag Zec '
    'Mal Mat Mrk Luk Jhn Act Rom 1Co 2Co Gal Eph Php Col 1Th 2Th 1Ti 2Ti Tit Phm '
    'Heb Jas 1Pe 2Pe 1Jn 2Jn 3Jn Jud Rev'
).split()
BOOK_IDS = {abbr: i + 1 for i, abbr in enumerate(STEP_BOOKS)}

STRONGS_RE = re.compile(r'^([GH])0*(\d+)')
# English (Hebrew) reference, e.g. "Gen.31.55(32.1)#01=L". English numbering
# matches the generated chapter files.
REF_RE = re.compile(r'^(\w{3})\.(\d+)\.(\d+)(?:\([^)]*\))?#\d+=(\S*)')


def base_strongs(raw: str | None) -> str | None:
    """Canonical G####/H#### for any Strong's form (G80, H0430G, G0080)."""
    m = STRONGS_RE.match((raw or '').strip())
    return f'{m.group(1)}{m.group(2).zfill(4)}' if m else None


def step_path(step_dir: Path | None, rel: str) -> Path:
    if step_dir:
        return step_dir / rel
    dest = CACHE_DIR / Path(rel).name
    if not dest.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        url = STEP_RAW + urllib.parse.quote(rel)
        print(f'  downloading {Path(rel).name}…', file=sys.stderr)
        urllib.request.urlretrieve(url, dest)
    return dest


def clean_definition(text: str) -> str:
    """Same cleanup build.py applies to TBES definitions."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^\d+[a-z]?\)\s*', '', text)
    if len(text) > 240:
        text = text[:237].rsplit(' ', 1)[0] + '…'
    return text


def parse_lexicons(step_dir: Path | None):
    """first[base] is the row build.py kept; rows[base][ext] every listed sense."""
    first: dict[str, dict] = {}
    rows: dict[str, dict[str, dict]] = defaultdict(dict)
    for rel in LEXICON_FILES:
        with step_path(step_dir, rel).open(encoding='utf-8') as f:
            for line in f:
                p = line.rstrip('\n').split('\t')
                if len(p) < 8 or not re.match(r'^[GH]\d{4}', p[0]):
                    continue
                m = re.match(r'([GH]\d{4}[A-Za-z]?)', p[1].strip() or p[0])
                if not m:
                    continue
                row = {
                    'ext': m.group(1),
                    'lemma': p[3].split(',')[0].strip(),
                    'translit': p[4].split(',')[0].strip(),
                    'pos': p[5].strip(),
                    'gloss': p[6].strip(),
                    'definition': clean_definition(p[7]),
                }
                b = base_strongs(p[0])
                first.setdefault(b, row)
                rows[b].setdefault(row['ext'], row)
    return first, rows


def iter_tagged_words(step_dir: Path | None) -> Iterator[dict]:
    """One dict per original-language word: bookId, chapter, verse, the
    extended Strong's of the word itself (prefixes like "and/the" dropped)
    and STEP's English rendering of that word."""
    for rel in TAGGED_FILES:
        hebrew = 'TAHOT' in rel
        with step_path(step_dir, rel).open(encoding='utf-8') as f:
            for line in f:
                m = REF_RE.match(line)
                if not m or m.group(1) not in BOOK_IDS:
                    continue
                p = line.rstrip('\n').split('\t')
                if hebrew:
                    # Leningrad text and qere readings; skips extra/variant rows.
                    if len(p) < 5 or m.group(4)[:1] not in ('L', 'Q'):
                        continue
                    # Column 4 is "H9002/{H1121G}"; column 3 its English, split
                    # on the same "/" morpheme boundaries ("and/ [the] people of").
                    parts = p[4].split('/')
                    core = [i for i, s in enumerate(parts) if s.startswith('{')]
                    if not core:
                        continue
                    i = core[0]
                    ext = re.match(r'\{(H\d{4}[A-Za-z]?)', parts[i])
                    glosses = p[3].split('/')
                    english = glosses[i] if len(glosses) == len(parts) else glosses[-1]
                else:
                    if len(p) < 6 or not ('NA28' in p[5] or 'SBL' in p[5]):
                        continue
                    ext = re.match(r'(G\d{4}[A-Za-z]?)', p[3])
                    english = p[2]
                if not ext:
                    continue
                yield {
                    'book_id': BOOK_IDS[m.group(1)],
                    'chapter': int(m.group(2)),
                    'verse': int(m.group(3)),
                    'ext': ext.group(1),
                    'strongs': base_strongs(ext.group(1)),
                    'english': english.strip(),
                }
