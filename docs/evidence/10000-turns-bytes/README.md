---
title: "#10000 — bytes per captured session in the graph, before vs after the turns moved to the record"
type: evidence
domain: platform
doc_status: final
created: 2026-10-26
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#10000, #10011, #9555, #10017, STORAGE-ARCHITECTURE.md §9.1 (D1), Postgres record, FalkorDB"
ownedBy: epistemic-team
---

# `#10000` — what a captured session costs the graph, before and after

**Measured:** 2026-10-26, on `origin/main` `fffcc7c0c`, against the parent of the squash that landed the
change (`2fa0ef54a`, the parent of `6b48f7cef` / PR #10011). Instrument:
`probe_turn_bytes.py` in this directory; raw output: `measurement-before.json`,
`measurement-after-unarmed.json`, `measurement-after-armed.json`.

**Why this file exists.** §9.1 (D1) of `docs/architecture/STORAGE-ARCHITECTURE.md` puts the transcript
outside the graph, and #10000's own acceptance list asks for *"a cost measurement taken after the change
[that] shows the drop: bytes per captured session in the graph, before vs after, on the same corpus."*
No such record existed anywhere in the tree — the issue's other acceptance bullets had homes and this one
did not. This is the home.

## The figure

Same corpus, same code path, one variable — whether the record can hold the turns.

**Corpus:** 25 sessions × 40 turns × 1,500 characters, one 384-dim `float32` vector per turn
(the production embedding width). **1,000 turns is 1,509,500 bytes of text and 1,536,000 bytes of vector**
(1,509.5 B and 1,536 B per turn — the text figure includes the `[user]` / `[assistant]` role prefix
the writer stores, 7 and 12 bytes counting each one's trailing space, so 1,500 + (7+12)/2 per turn),
and the committed `expected_rows` is `sessions × turns` = `1,000`.

| arm | what it is | bytes per captured session in the graph | ×3 runs | nodes the corpus added |
|---|---|---|---|---|
| **before** | parent commit `2fa0ef54a`, record unarmed — the pre-#10000 writer | **322,063** | 323,292 · 322,063 · 321,785 | 1,000 `:Point`, 1,000 `CONTAINS` |
| **after, unarmed** | `fffcc7c0c`, record NOT armed — the documented capability fallback | **321,175** | 321,212 · 321,033 · 321,175 | 1,000 `:Point`, 1,000 `CONTAINS` |
| **after, armed** | `fffcc7c0c`, `TORTOISE_RECORD_DSN` → a Postgres carrying the record migrations | **32,032** | 32,003 · 32,032 · 33,513 | **0 `:Point`, 0 `CONTAINS`** |

- **The drop is 290,031 bytes per captured session — 90.0%** (314.5 KiB → 31.3 KiB of graph RAM per
  session). Per turn: 8,052 → 801 bytes.
- **The control holds.** The unarmed arm is the pre-change behaviour by design, and it measures
  within **0.3%** of the parent commit (888 B/session, 0.28%) — so the delta is the change and not the
  instrument. It also means
  a deployment that never arms the record pays exactly what it paid before.
- **Only 37.8% of what is removed is payload.** The text (1,509.5 B/turn) plus its `float32` vector
  (1,536 B/turn) is 3,045.5 of the 8,052 B/turn the change takes out; the rest is graph structure — the
  node block, the attribute block, the label matrices, the `CONTAINS` edge and the HNSW entry. **That
  subtraction is the argument for placement over compression:** shrinking or truncating the stored text
  was never going to reach this number, and removing the *node* does.
- **The armed graph keeps what the ontology says it should, and no prose.** A public capture (mock
  extractor, hermetic) in the armed arm adds: `:Source` 1 (**`sum(size(content))` = 0 bytes** — the anchor
  is identity and metadata), `:Object` 1, `:Event` 2, `:Point` 1 (the extracted claim), `:Session` 1,
  `:GraphEvent` 8 — and **0 nodes carrying any turn's text**.
- **The transcript arrived somewhere instead.** The record holds **1,007 `public.point` rows** for the
  run's tenant (1,000 corpus turns + 6 public-capture turns + 1 extracted claim), 1,518,584 bytes of
  `content` — read back by `record_read.session_text`.
- **The narrative half of the bullet was already home.** `public.session_narrative` is the narrative's
  only writer (`tortoise/record_projection.py:3547`, declared node-free on purpose at `:987-995`), so
  *"turns and narrative"* has one store each; this measurement covers the turns, which are the ones that
  were in the graph.

## What remains in the graph after an armed capture — measured, not assumed

**32,032 bytes per session, and it is one node per turn that carries no text.** The armed graph holds
**:GraphEvent 1,008** (one `PointAdded` carrier per turn, plus the public capture's), `:Session` 26,
`:Source` 1, `:Point` 1 — and `sdk._capture_turn_ids` returns `[]`. Each `:GraphEvent`'s payload is
`{"id", "kind", "content_hash"}`: **the text is not in it.** ⚠️ **Two different scans are in play
here, and only one of them is this measurement.** The figure in the committed JSON
(`graphwide_text_hits_over_sessions` = 0) comes from the probe's scan over **three** properties —
`n.c`, `n.content`, `n.text` — with **25 needles** (one session prefix each, and every turn of that
session shares it, so the 1,000 turn texts are all covered: 0 hits). The **`payload`/`props`** half is
NOT in that figure; it is pinned by
`tests/test_10000_turns_record_only.py::test_an_armed_capture_puts_the_turn_text_in_no_node_at_all`,
which scans all five properties — on a 2-turn capture, not on this corpus.

So the acceptance bullet's wording — *"no per-turn node carrying the turn's text"* — is met, and the
residual is the **journal's graph mirror**, not the transcript. Whether those carriers should also leave
the graph is a placement decision of their own: it belongs with the record→cache rebuild seam
(`cache_tier`, #10070 / #10017), **not** with this bullet, and it is not claimed here.

The nodes already in live graphs are untouched by any of this and are #10017's destructive, gated sweep.

## Limits of this instrument (read before quoting a number)

- **`used_memory` from `INFO memory` is the instrument**, not `GRAPH.MEMORY USAGE`. `GRAPH.MEMORY USAGE`
  reports whole **MB** — it reads **0** for the armed corpus and **5 MB** for the unarmed one, which is a
  second witness but not a fine one — and it is blind to the HNSW `vectorset` module (#5331), which
  `used_memory` does count. Both are recorded in each JSON.
- **It includes graph structure, not just payload**, so a small corpus over-states the marginal per-turn
  cost (FalkorDB allocates in blocks). The corpus is a flag and is printed with every figure: these
  numbers are a per-session marginal **at 25 sessions × 40 turns × 1,500 characters**, not a per-node
  constant.
- **A fresh private FalkorDB container per run** (`tools/test_lane.py down` → `uri`). Runs taken on a
  container that already held other graphs read lower — **272,374** B/session was measured once in that
  state, and allocator reuse after a wipe is the only mechanism identified for the gap. Three runs per
  arm, fresh container each — and **the spread is reported as measured, not bounded**:

  | arm | the three runs | spread (max−min ÷ quoted) | max vs mean |
  |---|---|---|---|
  | before | 323,292 · 322,063 · 321,785 | **0.47%** | 0.28% |
  | after, unarmed | 321,212 · 321,033 · 321,175 | **0.06%** | 0.02% |
  | after, armed | 32,003 · 32,032 · 33,513 | **4.7%** | 3.1% |

  ⚠️ **The armed arm is the noisy one** — 4.7% run-to-run at ~32 KB, where a fixed ~1,500 B of allocator
  bookkeeping is a large fraction. That is why its figure is quoted as measured and never dressed as a
  precision bound, and why the comparison that carries the claim is the **90.0% delta**, which is three
  orders of magnitude larger than the noise on either side of it.
- **One run per arm is committed, and here is which one.** `measurement-before.json` is `before` run
  **2**, `measurement-after-unarmed.json` is run **3**, `measurement-after-armed.json` is run **2**
  (`…_r2` / `…_r3` in each `namespace`). The quoted per-arm figure in the table above **is** that
  committed run in all three arms — the middle value each time — so 6 of the 9 runs behind the spread
  column above cannot be re-read from the artifact; they are the numbers the table states.
- **The turn store is driven through its chokepoint** (`sdk._write_session_and_turns`, the ONE writer both
  capture lanes call) with synthetic vectors, so the figure is the turn store's graph cost. The `:Source`,
  `:Event` and extraction rows are present in both arms and are excluded from the delta by construction —
  the public capture that produces them runs **before** the byte baseline.

## Reproduce

```bash
cd <worktree>
eval "$(uv run python tools/test_lane.py uri)"                 # a private FalkorDB; exports TORTOISE_DB_URI
# the armed arm only — any Postgres; the migrations are applied by the same helper the record tests use
export TEST_RECORD_DB_URI=postgresql://record:record@127.0.0.1:5432/record

uv run --extra postgres python docs/evidence/10000-turns-bytes/probe_turn_bytes.py \
    --arm after-armed --namespace test_r10000_after_armed --sessions 25 --turns 40 --turn-chars 1500

uv run python tools/test_lane.py down                          # never leave the container behind
```

The `before` arm runs the same script from a worktree at `2fa0ef54a` (`origin/main` at the time of
measurement does not carry it — the script is added by the branch that adds this file).
