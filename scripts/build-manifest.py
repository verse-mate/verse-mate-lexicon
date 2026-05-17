#!/usr/bin/env python3
"""
Regenerates src/manifest.generated.ts from the JSON files in src/generated/.

Metro needs every `import()` path to be a string literal — dynamic globs
don't work — so we materialize one entry per chapter into a TypeScript
Record. Run this whenever the chapter set changes (e.g. after a
MorphGNT/BSB re-ingest).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / 'src' / 'generated'
OUT = ROOT / 'src' / 'manifest.generated.ts'

chapter_stems = sorted(
    f.stem for f in GENERATED.glob('*-*.json')
    if not f.name.startswith('_')
)

lines = [
    "// AUTO-GENERATED — do not edit. Regenerate via scripts/build-manifest.py.",
    "//",
    "// Metro static-analyses every `import()` path at bundle time, so the",
    "// keys must be string literals — that's why this file is generated",
    "// rather than constructed dynamically. Each entry is lazy: the chapter",
    "// JSON is only fetched when its loader is called.",
    "",
    "import type { GeneratedAlignment } from './internal-types';",
    "",
    "export const CHAPTER_LOADERS: Record<string, () => Promise<{ default: GeneratedAlignment }>> = {",
]
for stem in chapter_stems:
    lines.append(f"  '{stem}': () => import('./generated/{stem}.json'),")
lines.append("};")
lines.append("")

OUT.write_text('\n'.join(lines))
print(f"wrote {OUT.relative_to(ROOT)} with {len(chapter_stems)} loaders")
