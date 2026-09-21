<!-- issue-scoping: v5.1 double diamond + verify — daniel-ospina/tortoise#3914 -->
<!-- plan-review: cycles=0 (see §7 — the scope artifact IS the issue's own Fix section + this plan) -->

# Ask-lane seeders emit the CAPTURE shape — Implementation Plan

> **For Pi:** this plan is already executed (PR #4011); it records the design the diff implements.

**Issue:** [#3914](https://github.com/daniel-ospina/tortoise/issues/3914) — Level: `task`, complexity: `standard`
**Branch:** `fix/3914-ask-seeders-capture-shape` · **PR:** #4011
**Scope artifact:** issue #3914 body (§Fix) + this plan's §2–§4
**Supersedes:** the per-seeder fix strategy implied by #3914's "Fix" section; §3 D1 argues the single-store variant.

---

## 1. Problem (confirmed)

Four ask-lane seeders manufacture the session/turn graph the product read path then consumes, and all
four write a shape the capture path cannot produce: plain `statement` Points carrying `p.sessionId` /
`p.eventId` **props**, with **no `(:Session)` node** and **no `(:Session)-[:CONTAINS]->(:Point)` edge**.

The shipping point fetch resolves a hit's identity as `coalesce(p.sessionId prop, min(CONTAINS edge
ids))` — **the prop first** (`tortoise/sdk.py`, the batch fetch behind `tortoise_fts_query` /
`/v1/search`). A fixture that writes the prop and no edge therefore reports the identity as present on
a graph where the CONTAINS read was never exercised: the read can break entirely and the suite stays
byte-green. That is not hypothetical — it is the mutation #3888 recorded
(`retrieved_session_ids == []`), and all four committed transcripts quoted the forged provenance
(`[session sess-0]` / `sess-1` / `sess-2`). The second harm, the one that outlives the fixture, is that
a seeder teaches every downstream consumer the shape it writes.

Reproduced in-repo rather than taken from the issue: `rg` over `create_point` / `CONTAINS` /
`sessionId` / `eventId` / `is_episodic` / `pointKind` and the `_seed*` helpers classifies 12 helpers —
4 forged (the four above), 7 real (`ask_spotcheck._seed_memory`, `ask_recall_bench._seed_question`,
`test_ask_retrieval_levers._seed`, `longmem_eval.ingest.ingest_haystack`, `longmem_eval.ingest_v2`,
`test_d3_session_identity._seed_captured_session`, the eval-lane inline seeders in
`test_graph_integrity_gate.py` / `test_per_session_census.py` / `test_hosted_api.py`), 1 **new
instance not in the issue's inventory** (`tests/_assembly_graph.py::build_base_graph`).

## 2. Design decisions (locked)

| # | Decision | Rationale |
|---|---|---|
| D1 | **One shared writer**, `tools/ask_spotcheck.py::seed_capture_turn_store`, instead of fixing four seeders independently | #3911 had already made `ask_spotcheck._seed_memory` the third hand-copy of capture's turn loop (`tortoise/sdk.py` / `tortoise/hosted_api.py` NOTEs, #3551). Four parallel fixes would be four more copies; one writer makes the shape un-driftable and is the pattern #3911's own review endorsed |
| D2 | `tests/test_ask_regression_llm.py` **imports** the generator's `_seed` rather than mirroring it | The two already had to agree byte-for-byte; sharing the function makes that structural instead of aspirational |
| D3 | `_seed_event_graph` keeps the **extracted-claim** shape (it needs `p.eventId` for the `:Event` join, its actual subject) and gains the CONTAINS edge capture writes for extracted claims too | Cycle-2 review: capture CONTAINS-wires *extracted* Points as well (the extraction loops), so a claims-only fixture with no edge was an **under**-approximation. The claim "identity rides the edge on TURN points only" was false and is corrected |
| D4 | The Session node carries capture's trio (`created_at` coalesced, `turn_count`, `is_episodic=true`), via one `merge_capture_session` helper | Cycle-1/2 review: a bare-id Session is a node shape **no** product writer produces, and consumers reading `s.turn_count` / `s.is_episodic` see `None` — so no fixture seeded without them can guard those surfaces |
| D5 | `_assembly_graph.py` **left as-is**, declared in its module docstring | Its goldens assert identity **absence** (`[session ?]`) and the fetch deliberately does not read its snake `session_id` prop, so it is inert for the false-PASS class; wiring a Session would move policy-governed goldens — #3804's call, not a fixture edit |
| D6 | Transcript seed keys `kind` / `tags` are **dropped**, not carried | Capture's turn store takes neither, and a `kind` knob would let a seed silently opt out of the turn shape — the defect class this plan closes. No committed fixture used either key |
| D7 | Goldens are **regenerated**, and the resulting shift is reported | The goldens were generated on the forged graph; a shift is information, not a failure to preserve |

## 3. Integration Surface Map

| Surface | Kind | Touched by this change? | Guard |
|---|---|---|---|
| `tortoise/sdk.py` capture turn loop | source of truth (read only) | **comment-only** (drift NOTE symbol) | parity asserted in `test_ask_seed_shape.py` |
| `tortoise/hosted_api.py` capture turn loop | source of truth (read only) | **comment-only** (drift NOTE symbol) | same |
| `tortoise/sdk.py` point fetch (`tortoise_fts_query`) | reader under test | no | `test_ask_seed_shape.py` (wire `sessionId`), `test_ask_api.py` (`retrieved_session_ids`) |
| `tortoise/retrieval.py::hit_session_id` / `_render_block` | reader under test | no | rendered `[session <sid>]` / `[session ?]` |
| `tortoise/sdk.py::annotate_ask_hits` | reader under test | no | `test_ask_sdk.py` (Event join + speaker) |
| `tools/ask_spotcheck.py` (seeder) | writer | yes | `test_ask_seed_shape.py`, `test_ask_spotcheck_seed_shape.py` |
| `tools/gen_ask_transcripts.py` (seeder + generator) | writer | yes | `test_ask_seed_shape.py`, `test_ask_regression_llm.py` |
| `tests/fixtures/ask_llm_transcripts/*.json` | committed goldens | yes (regenerated) | byte-equality in `test_ask_regression_llm.py` |
| `tools/ci_selection.py` / `config/ci-surfaces.yml` | selection manifest | yes | `tests/test_ci_selection.py` |
| MCP / public SDK surface | frozen (#3863) | **no** | — |

## 4. Tasks

1. Extract `seed_capture_turn_store` + `merge_capture_session` from `_seed_memory`; make `_seed_memory` delegate.
2. Route `tools/gen_ask_transcripts._seed` and `tests/test_ask_api._seed_point` through it; make `tests/test_ask_regression_llm._seed` import the generator's.
3. Give `tests/test_ask_sdk._seed_event_graph` capture's extracted-claim shape (eventId + speaker + Session + CONTAINS) and remove the forged `sessionId` branch.
4. Regenerate the four transcripts; verify stability across two runs.
5. Guards asserting the **resolved graph**: new `tests/test_ask_seed_shape.py`; readbacks in `test_ask_api.py`; ignore-the-key pin in `test_ask_sdk.py`.
6. Mutation-prove each guard (A–G); restore each file byte-identically.
7. Register `tools/gen_ask_transcripts.py` (SOURCE_PATTERNS + TOOL_CARVEOUTS) and `tests/test_ask_seed_shape.py` (ci-surfaces); companion pin in `test_ci_selection.py`.
8. Declare `tests/_assembly_graph.py` inert in its module docstring; report it as new information.

## 5. Verification

| Check | Result |
|---|---|
| `tests/test_ask_seed_shape.py test_ask_sdk.py test_ask_api.py test_ask_regression_llm.py test_ci_selection.py` | 183 passed, 1 skipped |
| `tests/test_ask_spotcheck_seed_shape.py` / `tests/test_d3_session_identity.py` | 2 / 20 passed |
| `tools/ci_selection.py --integrity` | exit 0 |
| `ruff check` + `py_compile` on changed `.py` | clean |
| Mutation A (turn-store CONTAINS edge removed) | 4 failed, 5 passed, 1 skipped → restored 9 passed, 1 skipped |
| Mutation B (`_seed_point` back to the forged shape) | 2 failed, 28 passed → restored 30 passed |
| Mutation C (forged `sessionId` branch re-added) | 1 failed, 36 passed → restored 37 passed |
| Mutation D (`pointKind='event'` → `'statement'`) | 2 failed, 1 passed → restored 3 passed |
| Mutation E (`_seed_event_graph` CONTAINS edge removed) | 1 failed, 36 passed → restored |
| Mutation F (bare Session in `_seed_event_graph`) | 1 failed, 36 passed → restored |
| Mutation G (bare Session in `merge_capture_session`) | 2 failed, 1 passed → restored 3 passed |

## 6. Review Cycle Log

| Cycle | Findings | Disposition |
|---|---|---|
| 1 | P2: `_seed_event_graph` had no Session/CONTAINS and its guard pinned the omission + false "TURN points only" claim; P2: bare-id Session in `seed_capture_turn_store` | fixed in `b66751e57` |
| 2 | P2: cycle 1 had introduced a bare Session in `_seed_event_graph` and left the Session side unpinned; P2: drift NOTEs named the moved `_seed_memory` symbol | fixed in `5796ec47a`, plus the Session-props pin in `test_ask_seed_shape.py` |
| 3 | PR-body staleness only (claims not refreshed after cycle 2) | body rewritten; no code finding |

## 7. Reported shift (expected, not preserved)

The four goldens are regenerated. Beyond provenance the rendered evidence loses `(session date …)` —
capture's turn Points carry no `eventId`, so the `:Event` date join reaches no turn — and gains
capture's `[user] ` role bracket. Second, larger consequence: capture's turn store writes **no
`embedding`**, so these Points carry none and the dense leg contributes nothing — `gold-verbatim-commit`
drops from 3 retrieved hits to 1. The fixture is now emission-independent for these turns (it was
env-dependent before: the committed goldens could only be reproduced with the embeddings extra
installed) but no longer exercises the dense leg.

## 8. Follow-ups filed / carried

- `tests/_assembly_graph.py` session-less Points — inert, declared, governed by **#3804**.
- The dense-leg loss for the transcript lane is stated in the PR body; a decision to re-seed the lane
  with an embedding backfill (so it exercises the dense leg again) is **not** taken here.
- #3551 (collapse the three capture turn-store copies onto one primitive) is unblocked in part: the ask
  lane now has **one** copy instead of three, so #3551 has two live writers plus one seeder to merge.
