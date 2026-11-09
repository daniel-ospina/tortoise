#!/usr/bin/env python3
"""Generate `tortoise/_placement_table.json` — the placement table as package data.

WHY THIS EXISTS (#10663). The cache projection reads the record's property
namespace from the field-level placement table, whose one declaration is prose:
`docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md`. The
DEPLOYED IMAGE ships no `docs/` tree (`Dockerfile.hosted` COPYes `tortoise/`,
`packs/`, `tests/sample_transcript.txt`, `product/pricing.json` and
`supabase/migrations/`), so `record-reconcile` and `record-restore` could not run
in production at all — they refused with *"the placement table is not on disk at
/app/docs/…"*, wrapped as *"the record is unreadable"*, exit 2.

A contract a production verb reads is a RUNTIME DEPENDENCY, and a runtime
dependency must live in the package. The doc stays the ONE declaration; this
tool derives the carrier from it — it does not restate it, so the two cannot
disagree except by an edit that skipped this step, and
`tests/test_10663_placement_table_packaged.py` makes exactly that edit RED.

USAGE
    uv run python tools/gen_placement_table.py           # write/refresh
    uv run python tools/gen_placement_table.py --check   # exit 1 if stale

WHAT the file holds: the parsed `(table, property)` rows and provenance (the
source path, its sha256, the row COUNT, and the generator version it came
through — the last two are ENFORCED by the reader, not decorative; the sha256
is NOT, because an image has no doc to compare it against, and saying so keeps
the next reader from assuming the carrier is more validated than it is). No
`ADDITIONAL_PRIMARY_COLUMNS` — those are schema-spelled decisions appended by
`cache_projection`, and a carrier that invented them would be a second
declaration of the schema.
"""

from __future__ import annotations

import sys

# The #5128 interpreter guard MUST precede every other import: a module-level
# 3.12-only import raises ImportError on an older interpreter BEFORE a later
# guard could run, and the refusal then looks like a broken tool rather than a
# floor. Print-then-`SystemExit(int)` is the shape the #5128 census's detector
# accepts (`tests/test_entry_point_python_guard.py`), and exit 2 is reserved
# here for "cannot answer" — 1 already means STALE under `--check`, and a
# caller must never read an interpreter refusal as "regenerate the table".
# Mirrors `tools/test_design_map_check.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/gen_placement_table.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/gen_placement_table.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tortoise.cache_projection import (  # noqa: E402
    PACKAGED_PLACEMENT_TABLE,
    PLACEMENT_DOC,
    PLACEMENT_TABLE_GENERATOR,
    parse_placement_rows,
)

#: The generator's own version — the ONE value, owned by the package (#10663
#: review, P2). Bumping it is what makes a stale carrier legible as stale rather
#: than merely different: `_read_packaged_placement_rows` REFUSES any other value
#: in `_generated_by`, so "bumped the shape and forgot to regenerate" fails
#: closed at runtime instead of being silently accepted as authoritative. The
#: local alias keeps the name this tool's own prose uses.
GENERATOR = PLACEMENT_TABLE_GENERATOR


def build() -> str:
    """The carrier's exact text, derived from :data:`PLACEMENT_DOC`.

    ONE ROW PER LINE: a placement change then reads as a one-row diff, and a
    reviewer can see which field moved rather than a reflowed blob.
    """
    raw = PLACEMENT_DOC.read_bytes()
    rows = parse_placement_rows(raw.decode("utf-8"))
    meta = {
        "_generated_by": GENERATOR,
        "_source": str(PLACEMENT_DOC.relative_to(REPO_ROOT)),
        "_source_sha256": hashlib.sha256(raw).hexdigest(),
        # The row COUNT, so the reader can refuse a truncated carrier. Without
        # it, a carrier holding the first N rows parses cleanly and is accepted
        # as the WHOLE namespace — the silent-partial form of the same fault the
        # empty check closes (#10663 review, P2).
        "_row_count": len(rows),
        "_note": (
            "GENERATED — do not hand-edit. Regenerate with "
            "`uv run python tools/gen_placement_table.py`; the doc named in "
            "_source is the one declaration (#10663)."
        ),
    }
    lines = ["{"]
    for key, value in meta.items():
        lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append(' "rows": [')
    lines.append(",\n".join(f"  {json.dumps(list(row))}" for row in rows))
    lines.append(" ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the carrier is missing or stale")
    args = ap.parse_args(argv)

    text = build()
    current = (PACKAGED_PLACEMENT_TABLE.read_text(encoding="utf-8")
               if PACKAGED_PLACEMENT_TABLE.exists() else None)
    if args.check:
        if current == text:
            print(f"up to date: {PACKAGED_PLACEMENT_TABLE}")
            return 0
        print(f"STALE: {PACKAGED_PLACEMENT_TABLE} does not match "
              f"{PLACEMENT_DOC} — run `uv run python tools/gen_placement_table.py`",
              file=sys.stderr)
        return 1
    PACKAGED_PLACEMENT_TABLE.write_text(text, encoding="utf-8")
    print(f"wrote {PACKAGED_PLACEMENT_TABLE} "
          f"({len(json.loads(text)['rows'])} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
