# Scoping — #1367: surface supersede/NAND structure in reader context

**Date:** 2026-08-18 · **Issue:** #1367 · **Complexity:** standard (agreed)
**Team:** epistemic-team · **Parent:** #1144 (retrieval optimization loop)

## Problem

LongMemEval baseline 66.2% (2026-08-17). The knowledge-update (66.7%) and
multi-session update-tracking (44.6%) categories fail because the reader
can't tell which statement replaced which. The GRAPH knows (supersede_point /
CORRECTS edges / NAND operators), but the flat context rendered by
`tools/longmem_eval/retrieve.py::render_context` shows only hit content +
session dates.

## Machinery (already merged — #1353)

`tortoise_fts_query` results carry promoted D8 fields (additive keys, emitted
only when known):
- `status` — live/superseded/deprecated/retracted/draft
- `superseded_by` — `{id, content_snippet, created_at}` of the newest
  superseding claim (incoming CORRECTS)
- `supersedes` — `[{id, content_snippet, created_at}]` of replaced claims
  (outgoing CORRECTS)

`content_snippet` = `(content or "")[:120]` (search_engine
`fetch_point_epistemic_state`). This issue REUSES these fields — no second
supersede-detection path.

## Decision: annotation placement

**`retrieve.py` only** (reader/retrieve surface). The ingest path is NOT
touched (owned by the LME v2-ingest worktree — #1369/#1394 extractor
supersession wiring).

1. **`retrieve_for_question`** — extract the annotation loop into a pure
   helper `_annotate_hits(hits, props, dates)` that passes the promoted
   `superseded_by` / `supersedes` through from each raw search hit
   (additive keys; absent → `None` / `[]` → no markers, byte-identical
   rendering). `render_context` stays a pure string builder shared with the
   token estimator (no SDK access, no extra graph queries).
2. **`render_context`** — emit per-hit markers before the content:
   - superseded hit: `[SUPERSEDED BY: <newest superseding content_snippet>]`
   - superseding hit: `[SUPERSEDES: <replaced snippet> ; <replaced snippet>]`
   Both can appear on one hit (a chain mid-point). Marker text uses the
   promoted `content_snippet` (120 chars — for LME's short turns this is the
   full claim; `expand_relationships` full-content fetch is a documented
   follow-up if real claims truncate).

### Embedded-mode reality check (probed 2026-08-18)

On embedded FalkorDBLite (CI), `degradation_chain` returns `None` (no
fulltext index — even with `_elevated_timeout_ms=10s`), so hits come from
the TF-IDF snapshot fallback which does NOT decorate. ⇒ the annotation is a
no-op in embedded CI and fires on the Docker/HNSW path where the real
500-question eval (#1144 re-run) runs. No regression: undecorated hits
render byte-identically to today.

## Test plan (tests/test_longmem_runner.py — already registered in
config/ci-surfaces.yml, no new file ⇒ no ci-surfaces change)

1. `test_render_context_annotates_superseded_and_superseding_hits` — unit:
   decorated hits → expected marker text + superseding claim content present.
2. `test_render_context_supersede_markers_absent_without_promoted_fields` —
   backward compat: no keys / empty values → byte-identical output.
3. `test_annotate_hits_passes_through_supersession_state` — raw hit with D8
   fields → annotated hit carries them; raw hit without → None/[].
4. `test_retrieve_for_question_surfaces_supersession_annotation` —
   integration: build real supersession via `sdk.supersede_point` (CORRECTS
   edge), run `retrieve_for_question` (hits carry the keys), and pipe a hit
   decorated by the PRODUCTION `fetch_point_epistemic_state` through
   `_annotate_hits` → `render_context` asserting the marker (simulates the
   Docker path with real machinery, zero network).

## Out of scope

- Ingest-side supersession creation (v2-ingest worktree owns it).
- `expand_relationships` full-content enrichment (snippet suffices for LME;
  follow-up if measurement shows truncation).
- NAND-operator surfacing (no NAND edges in the LME eval graph; CORRECTS is
  the supersede channel #1353 uses).
