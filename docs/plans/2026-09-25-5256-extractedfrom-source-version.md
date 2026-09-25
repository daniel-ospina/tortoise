# Plan — #5256: record the read version on the create-path `extractedFrom` link

**Issue:** #5256 (`complexity:complex`, Level: task, epic #5088) · **Repo:** `daniel-ospina/tortoise`
**Branch:** `feat/5256-extractedfrom-anchor` · **Base:** `origin/docs/5199-version-scope @ 52e703f89` (STACKED on PR #5207)
**Predecessor:** `docs/plans/2026-09-25-5038-source-version-anchor.md` (Task 1, branch `docs/5038-scoping`)
**Review cycle:** 7 (see §10).

---

## 1. Confirmed problem (inherited, verified on #5038)

`ONTOLOGY.md` §4.6 requires: *"Every **Point** derived from a source records **`sourceVersion`** —
the `contentHash` of the version it was read from — on its `extractedFrom` link."*

`git grep -n sourceVersion -- '*.py'` on `origin/main @ acbe80f85` → **0 matches** (a SHA snapshot;
`origin/main` has since moved, still 0). On this STACKED base, #5199/PR #5207 **does** write
`r.sourceVersion` — but on the **`references`** link, through three writers
(`edges.py::link_source_to_entity`, `::link_source_to_event`, `::link_source_to_legacy_event`,
all via `_anchor_on_create`). It is a sibling, not a substitute: it reads `s.contentHash` at link
time and records its own stale-under-in-place-rebuild limitation, and its own narrative assumes the
`extractedFrom` anchor exists.

The naive fix — stamp the edge in `_link_source` — is **silently destroyed by the next rebuild**:
`extractedFrom` ∈ `DERIVABLE_STRUCTURAL_RELS` / `STRUCTURAL_REL_LABELS` (`edges.py`) and
∈ `SUPERSEDE_STRUCTURAL_RELS` (`sdk.py`); `rebuild_all` wipes the graph; `_link_source`
re-creates the edge **bare** on the live path (`create_point`) *and* on pass-2 resurrection
(`entities.py::_upsert_point_edges`). The value must therefore **travel in the point's own
journaled snapshot**.

**Framing (from #5038's problem-verify):** *the read-version has no durable, replayable, honest
home* — not "a write is missing" (a write the next rebuild drops is not a fix), not "the read needs
an operand" (a value the model may not fabricate).

## 2. Binding constraints (X1–X7, from #5038 §2 — all seven mapped)

| # | constraint | how this plan satisfies it |
|---|---|---|
| **X1** | a transfer that carries a version is not chain-consistent (pass-2b collapses a chain onto `_final(src)`) | **moot — the transfer carries nothing** (Design D, out of scope, §9 R1) |
| **X2** | if a transfer carried a version, the live transfer writer would need the identical guard | **moot** |
| **X3** | a key in the `_emit_event` payload dict is not replayable; the consumer reads `ev["point"]` | the value is a **declared node property**, so `get_point` carries it into `ev["point"]` |
| **X4** | supersede successors emit no `PointAdded` | **moot** (no transfer version) |
| **X5** | the *current* operand depends on #5024 (unjournalled in-place source bump) | the recorded value is the journaled read version, **never** re-read at replay (§3) |
| **X6** | `batch_id = derive_batch_id(bundle)` runs **before** writes, so a server-observed version is outside the hashed bundle **iff** a fail-closed caller-supplied reject exists | the reject is added at the bundle Phase-1 validator **and** `_sanitize_props` (and every other boundary — five in all) |
| **X7** | `structural_seen` dedupe drops a differing version | **moot** |

Additional binding rules from #5256: honest-absent (never `''`); `_link_source` must be **handed**
the value and must **never** fall back to `s.contentHash`; no SDK/MCP surface change; reject on
**every** tenant write surface reaching a Point.

## 3. The seam decision (the design work of this issue)

**The version is carried by a declared node property `sourceVersionTransit` on the Point — the
`r.sourceVersion` EDGE is authoritative, this node prop is the replay transit. It is named
`...Transit` so a Point read never shadows §4.6's edge scalar with a pair-list.
It is written by its own explicit `SET` clause on both the live and replayed writers.
The edge MERGE stamps `r.sourceVersion` from the point snapshot, never from `s.contentHash`.**

### Why the node property (and not a journal-only payload key)

The issue's own mechanism line: *"the precedent to mirror is `extractedFrom` itself: a **declared
node property** with its own explicit `SET` clause"*.

| seam | verdict |
|---|---|
| **(A) declared node property, own `SET` clause** ← **chosen** | Symmetric (live CREATE-map + replayed `_upsert_point_props` clause), replayable from `ev["point"]`, **durable across re-emits** (the #5004 `embedding_verbatim` lesson: a payload-only marker was *"LOST there … so it must live on the NODE"*, `entities.py`), and the #5011 gate can **see and compare** it. |
| (B) a key in the `_emit_event` **payload dict** | Rejected by X3 — the consumer reads only `ev["point"]`. |
| (C) journal-only key in `ev["point"]` | Rejected: to keep the #5011 gate green it must be **excluded from the compared content view** (via `_NEVER_A_NODE_PROP`/`_EXCLUSION_REASONS` or `_uncarried`), so a dropped or forged transit cannot be *compared* — and it does not follow the issue's mandated node-prop precedent. *(Not claimed: that the value becomes wholly unreported — an undeclared list is surfaced in `uncarried_journal_fields`; nor that (C) loses the **edge** on a re-emit — pass-2 iterates every PointAdded and `ON CREATE` re-stamps from the original snapshot.)* |

The envelope seam (#3947's `contains_session`) was weighed and rejected: it is read from the **raw**
envelope as a per-write capture directive, not a per-point fact, and it would be a second, divergent
writer.

### Shape — per-link, so a list of `[ref, hash]` pairs, built by ONE shared helper

`ONTOLOGY.md` §4.6: *"a Point read from more than one source carries a `sourceVersion` **per
link**"*. A scalar cannot express that; a dict is not persistable (`_is_persistable_prop_value`
rejects maps — a dict would make the journal carry a key the graph cannot, and the #5011 gate would
report a divergence on **every** sourced Point). The transit is therefore a list of 2-element `[raw_ref, contentHash]` pairs (nested arrays **are** persistable). The **edge**
carries the per-link scalar `r.sourceVersion`.

**ONE builder — `_source_version_transit(versions: dict) -> list | None`** (in `edges.py`) — is used
by **both** producers; it returns `None` (key omitted entirely) when the mapping is empty, so an
un-sourced Point never carries `[]`.

**Key contract (pinned, corrected round 5):** a pair's first element is the Point's **raw
`extractedFrom` ref** — the journal-stable spelling, which is also the Point's own payload key.
`resolve_source_key(g, ref)` is still called by the live resolver, but **only to FIND the Source
node** whose `contentHash` to read; it is never the carrier's key. (An earlier version of this plan
pinned the resolved key — that was exactly backwards, and it is the round-5 P1: a value keyed by a
*resolution* is unstable, because the Source node's stored `url` can differ between live and replay
when an unjournaled stub minted by an earlier Point is absent at pass-1, so the same ref resolves to
a different key, the replay lookup misses, and the edge lands bare.) The raw ref is the one spelling
both lanes share. (`resolve_source_key` is idempotent; its adopt-on-touch write only touches the
**Source** node, never the Point — it is not a second Point write.)

### Data flow

```
LIVE  create_point(..., extractedFrom=refs)                      # FRESH-CREATE path only
        ├─ sv = resolve_source_versions(proj.g, refs)             # dict[str,str] keyed by the RAW ref
        ├─ on the CREATE map:  sourceVersionTransit = _source_version_transit(sv)  # pair-list; omitted when empty
        ├─ CREATE (n:Point {…})                                   # ONE write (#2952)
        ├─ proj._link_source(pid, refs, source_versions=sv)       # _link_source takes the DICT
        │     └─ per ref: MATCH … MERGE (n)-[r:extractedFrom]->(s)  ON CREATE SET r.sourceVersion
        └─ _emit_event("PointAdded", ..., point=self.get_point(pid))  # snapshot carries the prop

LIVE  EventAPI.add_point(..., extractedFrom=…)
        ├─ if getattr(self.projection, "g", None) is not None:
        │     _sv = <the SAME builder>(…);  if _sv is not None: p["sourceVersionTransit"] = _sv
        └─ _emit("PointAdded", point=p) → log.append + projection.apply
              ├─ pass-1 _upsert_point_props  → explicit SET n.sourceVersionTransit=$sv
              └─ pass-2 _upsert_point_edges  → _link_source(..., source_versions=dict(p["sourceVersionTransit"]))

REPLAY (rebuild_all / recover_from_log) — the SAME two writers, reading p == ev["point"]
```

**Never `s.contentHash` at replay:** pass-2 resurrection calls the same `_link_source`;
`_upsert_source`'s in-place bump is unjournalled (#5024), so reading the Source at replay would
record a version the Point was never read from — a **false current**.

**The dedup path is untouched:** `create_point(dedup=True)` returns early; the resolver runs only on
the fresh-create path, after the early return, so an idempotent re-commit cannot trip the new reject.

**Reject surfaces (all five):** `_sanitize_props` (SDK backstop — also guards `create_document` via
`_create_entity`, `update_point`, `_update_entity`), `_check_item_shape` (bundle Phase 1),
`_SERVER_MANAGED_PROPS` (MCP), `EventAPI.add_point`'s `_forged` set, and `create_source`'s own loop
— both `EventAPI.add_point` and `create_source` bypass `_sanitize_props` (the latter via
`_skip_sanitize=True`) and so need their own reject (#5004 round-7 precedent).

## 4. Task breakdown

| # | file | change |
|---|---|---|
| 1 | `tortoise/projection/edges.py` | `resolve_source_versions(g, refs)` — the **LIVE-only** resolver (keyed by the **RAW ref**; `resolve_source_key` only FINDS the node, never keys the map; skips empty/`''`/absent). It MUST normalize `refs` exactly as `_link_source` does — `refs = [refs] if isinstance(refs, str) else list(refs)` — else a scalar ref iterates **characters** and mints per-character Sources (`create_point` keeps the common single-source ref a scalar). `_source_version_transit(versions)` — the ONE pair-list builder (None when empty). `_link_source(..., source_versions: dict[str,str] \| None = None)`: the MERGE binds `r` and appends `_anchor_on_create("$v")` (reuses the #5199 helper — one source of truth for the `''`/NULL guard); `$v` is **always bound** (None when absent, for the bare callers: `create_document`, the ingest connection leg, `_link_extracted_from`, direct test calls). **Never** reads `s.contentHash`. |
| 2 | `tortoise/projection/entities.py` | `_POINT_HANDLED \| {"sourceVersionTransit"}`; an explicit conditional `SET n.sourceVersionTransit=$sv` clause in `_upsert_point_props`; `_upsert_point_edges` folds the payload to `dict(...)` and hands it to `_link_source` (a falsy/None value must never reach `dict()`). BOTH writers select the carrier through the ONE shared `_point_source_transit` (all-or-nothing `_valid_transit_pairs` — non-empty list of 2-element `[str, str]` pairs whose members are both non-empty/non-blank — **plus** the own-ref gate/filter: the carrier is written only when the Point OWNS an `extractedFrom`, and only for that Point's own raw refs, normalized exactly as `_link_source` normalizes a bare `str` as ONE ref). So a corrupt carrier contributes NO anchor on either side, **and** a hand-written/foreign payload can neither plant a stray carrier with no edge (round-5 P2) nor an anchor for a source the Point does not reference. |
| 2b | `tortoise/projection/__init__.py` | add `n.sourceVersionTransit = NULL` to the #4042 pass-1a **recreate wipe** (`embedding` / `content_hash` / `embedding_verbatim`). Without it, a delete→same-id-recreate whose new snapshot omits `extractedFrom` retains the dead incarnation's transit in the rebuilt graph while live has none — a `derived = replay(journal)` break of exactly the `embedding_verbatim` class (#5004 round-4 precedent). |
| 3 | `tortoise/sdk.py` | `_sanitize_props` rejects `sourceVersion`/`sourceVersions`/`sourceVersionTransit`; `_check_item_shape` rejects them on bundle items (Phase 1, free); `create_source` carries its own reject (it bypasses `_sanitize_props` via `_skip_sanitize=True`); `create_point` resolves the LIVE versions on the **fresh-create** path and puts the transit in the `CREATE` map (no second write — #2952). |
| 4 | `tortoise/api.py` | `EventAPI.add_point`: reject `sourceVersion`/`sourceVersions`/`sourceVersionTransit` in the existing `_forged` set; when `getattr(self.projection, "g", None) is not None`, resolve and set `p["sourceVersionTransit"]` via the shared builder — **only when the builder returns non-None** (an un-sourced Point's payload must not carry `sourceVersionTransit: null`). The `getattr` guard covers **both** `projection=None` and an `InMemoryProjection` (no `.g`). |
| 5 | `tortoise/consistency.py` | a declaration comment only: `sourceVersionTransit` is a **declared, compared** node property (`_POINT_HANDLED`), deliberately **not** `_EXCLUSION_REASONS`-excluded — excluding it would be a blind spot. |
| 6 | `tortoise/mcp_server.py` | `_SERVER_MANAGED_PROPS \| {"sourceVersion", "sourceVersions", "sourceVersionTransit"}` (#5004 convention). No surface change. *(Component addition: #5256's list omits `api.py`/`mcp_server.py`; both are required by indicator 3.)* |
| 7 | `config/ci-surfaces.yml` | register `tests/test_source_version_extractedfrom_5038.py`. |
| 8 | `tests/test_source_version_extractedfrom_5038.py` | the new suite (§6). |

`tortoise/commit_schema.py` is **not** touched. The ingest `extractedFrom` **connection** leg
(`sdk.py`, `relation: extractedFrom` → `proj._link_source(src,dst)`) is **unchanged**: it creates no
Point snapshot, so an anchor there would be a **live-only** value that dies at rebuild — the exact
class #5038 rules out (residual R3).

## 5. Integration Surface Map

| Surface | Boundary | Layer | Failure mode to test |
|---|---|---|---|
| create path (SDK) | `create_point` → `_link_source` → edge | integration (docker) | anchor not written / is `''` |
| create path (EventAPI) | `add_point` → `apply` → `_upsert_point_edges` | integration | anchor missing on the extractor lane; projection=None crash |
| journal | `ev["point"]` → pass-2 `_upsert_point_edges` | integration + round-trip | dropped by `rebuild_all`; replay reads `s.contentHash` |
| tenant write | props → `_sanitize_props` / `_check_item_shape` / `_SERVER_MANAGED_PROPS` / `add_point` | integration | a caller fabricates the anchor |
| consistency gate | `_fold_journal` vs graph (`properties(n)`) | integration | the transit trips the gate or is a blind spot |
| SDK/MCP surface | `tools/surface_manifest.py` | config | a surface change slips in |

**Journey Test Map:** no user-facing journey — the consumer is the currency read (a later issue).
Outcomes driven: (a) `r.sourceVersion` is the version read, (b) identical after `rebuild_all`,
(c) a tenant cannot forge it, (d) the node transit is the gate-visible, compared record.

### Adversarial Threat Surface

(not adversarial) — a data-recording change, not gate/enforcement code whose correctness is "an
attacker cannot make it fail open". The forge-reject is a fail-closed validation with its own tests,
but the change's correctness is not a bypass-resistance property.

## 6. Test plan — `tests/test_source_version_extractedfrom_5038.py`

Precedent: `tests/test_provenance_extractedfrom_3263.py` (DB lane, embedded-safe fixtures).

1. **round-trip parity** — Source(`h1`) + a **scalar** `extractedFrom=ref` (the common/acceptance
   form) ⇒ edge `'h1'` **and** the node transit `[[ref,'h1']]`; `rebuild_all` ⇒ both byte-identical.
   (Acceptance 1 + the gate-visible record; pins the scalar-normalization guard.)
2. **honest-absent** — Source `contentHash=''` / no Source ⇒ the edge property is absent **and** the
   node transit is absent (never `''`, never `[]`), **and** the journal payload carries no
   `sourceVersionTransit` key. (Acceptance 2, at every home.)
3. **false-current guard** — create at `h1`; advance the Source to `h2` via a **journaled**
   `create_source(contentHash='h2')`; `rebuild_all` ⇒ the edge still reads `'h1'`, not `'h2'`.
4. **per-link** — two Sources with distinct hashes ⇒ each edge carries its own; survives rebuild.
5. **URL-variant key contract (+ the round-5 P1 regression)** — (a) a Source registered canonically,
   linked via a variant ref ⇒ the edge carries the hash and survives rebuild, and the node carrier is
   keyed by the **raw** variant spelling; (b) **the round-5 P1**: a Point created BEFORE the Source is
   registered (minting an unjournaled canonical stub) followed by a Source registered under a VARIANT
   spelling ⇒ the Point's `extractedFrom.sourceVersion` is byte-identical live and after `rebuild_all`
   even though the Source NODE identity differs between the lanes (R6), and `check_consistency` stays
   healthy. **FAILS on the pre-fix parent** (replay edge bare) and if the carrier reverts to the
   resolved key.
6. **EventAPI lane** — `add_point(..., extractedFrom=…)` with a projection ⇒ anchored; survives
   rebuild.
7. **EventAPI without a Falkor graph** — `add_point(..., extractedFrom=…)` with `projection=None`
   **and** with an `InMemoryProjection` (no `.g`) must not raise (the extractor live lanes).
8. **dedup re-commit** — `create_or_update_point(..., extractedFrom=…)` twice ⇒ no raise, no
   divergence.
8b. **delete→same-id-recreate** — hard-delete a sourced Point, then create a new Point with the
    same id and **no** `extractedFrom`; `rebuild_all` ⇒ no `sourceVersionTransit` on the rebuilt **node**
    (mirrors `test_a_recreated_point_does_not_inherit_the_verbatim_marker`, #5004). ⚠️ The test
    asserts the NODE only and says why: the old incarnation's **`extractedFrom` edge** is already
    resurrected by pass 2 today (pre-existing, independent of this anchor) — see residual R5; the
    node wipe closes the part this change would otherwise introduce.
9. **reject: `create_point(**props)`** — all three names raise; nothing written.
10. **reject: `ingest` bundle** — a point item carrying any of the three names raises at Phase-1; zero mutation.
11. **reject: `create_document`** — the Document surface refuses.
12. **reject: `update_point`** — the props path refuses.
13. **reject: `EventAPI.add_point`** — the extractor seam refuses.
14. **reject: MCP boundary** — `_reject_server_managed_props({...})` returns an error.
14b. **reject: `create_source`** — the `_skip_sanitize=True` writer carries its own reject.
14c. **malformed carrier** — a hand-written `PointAdded` payload with a non-list, a dict, a
    short pair, a numeric pair, or an empty/blank hash contributes **NEITHER** the node carrier nor
    the edge version, and does not abort `rebuild_all`.
14d. **no-edge / foreign-pair carrier (round-5 P2)** — a shape-valid `sourceVersionTransit` on a
    Point with **no** `extractedFrom` writes NOTHING (no stray anchor without an edge), and a pair
    whose key is not one of the Point's own raw refs is filtered out by both writers.
15. **no scalar stray / transit⇔edge** — for a **hash-bearing** fixture created through
    `create_point`/`EventAPI.add_point`, the node transit is a list of `[ref, hash]` pairs and, at
    creation, exists **iff** the Point has an `extractedFrom` edge; no Point carries a scalar
    `sourceVersionTransit`. (Scoped twice: the honest-absent case in test 2 legitimately has an edge and
    **no** transit; and the ingest **connection** leg, R3, is out of scope.)
16. **gate comparison** — seed the graph through the **replay writer** (`rebuild_all` after the
    `create_point` that journals the carrier — NOT the `create_point` CREATE-map write alone, which
    bypasses the clause), assert the faithful fixture is healthy (`check_consistency(...)["ok"]
    is True`), **then** tamper the graph's `n.sourceVersionTransit` and assert it is reported as a
    divergence. The positive half is what pins the `_upsert_point_props` clause; the mapped mutation
    for the gate is removing `sourceVersionTransit` from `_POINT_HANDLED` (see §7 item 6).
17. **supersede boundary (4 steps)** — predecessor created against a Source(`h1`) → live
    `supersede_point` ⇒ the successor's **transferred** edge has `r.sourceVersion IS NULL` and the
    predecessor's edge is gone (its node transit unchanged) → `rebuild_all` ⇒ re-assert.

Every test names the input that makes it FAIL.

## 7. Verification plan

- Docker lane: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest <files> -v`
- Files: the new suite; `test_pointsuperseded_rebuild.py`; `test_consistency_divergence_5011.py`;
  `test_ingest_conformance.py`; `test_ingest_bundle.py`; `test_provenance_extractedfrom_3263.py`;
  `test_source_version_references_5199.py`.
- `python3 tools/surface_manifest.py check` (a CI command, not a pytest — see test plan note).
- **Mutation-verify every guard** (flip → named test RED → restore):
  1. `resolve_source_versions`' `''`/absent skip → test 2 (**this**, not the CASE, is the
     discriminating guard; the `_anchor_on_create` CASE is defence-in-depth in series);
  2. the "never `s.contentHash`" rule (make `_link_source` read the source) → test 3;
  3. each reject site → tests 9/10/11/12/13/14/14b (all five surfaces);
  4. the replay transit consumption (`_upsert_point_edges`) → test 1 (rebuild parity);
  5. the resolver key (**raw ref → `resolve_source_key`**) → test 5 (the round-5 P1 regression must go
     RED: the replay lookup misses and the edge lands bare). *(This mutant is now the DEFECT; the
     pre-round-5 row said the reverse — `resolve_source_key` → raw ref was the fix — which is no
     longer true.);
  5b. the own-ref gate/filter (`_point_source_transit`) — removing the own-ref **filter** alone → the
     own-refs P2 test; removing the gate *and* filter → the stray-carrier P2 test as well. Note the
     **gate alone is an EQUIVALENT mutant**: with no `extractedFrom` the own-ref filter's ref set is
     empty and drops every pair anyway, so only the filter-removing mutant is discriminating (the
     stray-carrier behaviour is still mutation-covered, by that mutant);
  6. **`_POINT_HANDLED` membership** (remove it) → test 16: the key then falls to `_uncarried`
     (`_UNCARRIED_LIST`) and is `skip`-ped from both sides, so the tampered mismatch becomes
     invisible — the gate's `ok` goes back to True and test 16 goes RED. (Separately: removing the
     `_upsert_point_props` clause → test 1 (rebuild parity) **and** test 16's positive half, whose
     graph is seeded through `rebuild_all`.)
  6b. the shared `_valid_transit_pairs` non-empty rule (drop it) → test 14c's
     `bad-empty-hash`/`bad-blank-hash` halves.
  7. `EventAPI.add_point`'s graph guard → test 7;
  8. the #4042 recreate wipe (`n.sourceVersionTransit = NULL`) → test 8b.

## 8. Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Ontology | medium | the anchor is canonical; no wording change |
| Architecture | high | replay determinism, the journal seam, two live producers |
| Code | high | `edges.py` + `entities.py` + `sdk.py` + `api.py` + `consistency.py` + five reject surfaces |
| Overall | **complex** | matches `complexity:complex` |

## 9. Residuals (documented, not chased)

| # | severity | residual |
|---|---|---|
| R1 | P1 | The **transfer** half (supersede/pass-2b) is out of scope — a §4.6 reopen (O1, owner-only). A superseded successor gets **no** anchor; under §4.6's *"the version it was read from"* that is honest. |
| R2 | P1 | The capture path links before `_materialize_session_source` sets the hash ⇒ **absent** (O6) — the honest value; reordering is a separate owner decision. |
| R3 | P2 | The ingest `extractedFrom` **connection** leg calls `_link_source` with **no** `source_versions`, so it produces **no anchor at all** (not an anchor that later dies). Its `extractedFrom` edge is also absent from the Point snapshot, so the edge itself dies at rebuild (pre-existing). Left bare. |
| R4 | P2 | The `references` anchor (#5199) reads `s.contentHash` at link time and records its own stale-under-in-place-rebuild limitation; unchanged here. |
| R5 | P2 | **Recast after code review — the edge resurrection is pre-existing, but the ANCHOR VALUE on it is new.** On a hard-delete→same-id-recreate, pass 2 re-creates the *old* incarnation's `extractedFrom` **edge**. That resurrection is pre-existing (the #4042 wipe clears node props, never edges) — but before this change the resurrected edge carried **no** `r.sourceVersion`; now it carries the dead incarnation's stale anchor while live has no edge at all. The **node-prop** half is closed here (the #4042 wipe + test 8b); the **edge half** is filed as a scoped follow-up (pass-2 edge-incarnation handling) and is deliberately NOT fixed in this PR. `test_recreated_point_does_not_inherit_the_node_transit` asserts the node only and says why; the follow-up must extend it to assert the edge is absent. |
| R6 | P2 | **The Source NODE IDENTITY is not lane-stable when an unjournaled stub precedes the Source record.** `create_point(..., extractedFrom=CANON)` mints a stub at `CANON`; a later `create_source(VARIANT, …)` lands on it live (node url `CANON`), but replay pass-1 builds the Source from the VARIANT-spelled record, so the same ref re-resolves to `VARIANT` and the `extractedFrom` edge lands on a *different* `:Source` node per lane. **Pre-existing** (verified on the pre-round-5 parent: live edge `CANON`, replay edge `VARIANT`), independent of the anchor; reconciling it needs the stub write journaled (or the pass order changed) — out of scope. The **anchor is now stable across it** because the carrier is keyed by the raw ref (round-5 P1), and `test_variant_ref_after_an_unjournaled_stub_keeps_the_anchor` pins the scalar across the identity difference. |

## 10. Review cycle log

**Cycle 1 — 4 verifiers (2 scope, 2 plan).** Folded: §1 corrected to `origin/main` + the #5199
sibling note; **seam switched to the declared node property** (the #5004 `embedding_verbatim`
precedent is evidence FOR it); `EventAPI.add_point` added as both a forge path and an unanchored
producer; the ingest-connection leg removed from the resolver; no `_EXCLUSION_REASONS` entry (the
prop is compared); the resolver key pinned to `resolve_source_key`; test 3 advanced via a journaled
`create_source`; the X1–X7 table completed; the `+=`-on-frozenset notation fixed;
`mcp_server.py` noted as a component addition; live-graph absence and MCP-boundary tests added;
`_link_source` reusing `ON CREATE SET`; a supersede-boundary test added.

**Cycle 2 — 2 verifiers.** Folded: `EventAPI.add_point` must guard `self.projection is not None`
(else the projection-less extractor lanes crash); test 12 restated (a node property is the design,
not a defect) and the transit⇔edge invariant pinned; ONE shared pair-list builder pinned (a dict
would trip the #5011 gate on every sourced Point); the dedup early-return injection point pinned;
mutation-verify #1 corrected (`resolve_source_versions`' skip, not the CASE, is discriminating);
mutation-verify #6 corrected (needs the node-prop assertion + a gate test — new test 16); test 13
pinned to the **transferred** edge across live + rebuild; `update_point` added to the reject reach
and its test; test 14 reframed as the CI command (not a pytest); `_upsert_point_edges` must use
`.get` and validate; `_link_source` must always bind `$v`; the `resolve_source_key` double-call and
its Source-only adopt-on-touch write clarified; the (C)-rejection's false half ("loses the edge on a
re-emit") deleted.

**Cycle 3 — 2 verifiers.** Folded: the #4042 pass-1a **recreate wipe** must clear `n.sourceVersionTransit`
(new task row 2b + test 8b); test 16 seeds the graph through the replay writer and asserts the faithful
fixture healthy **first**; mutation-verify #6 re-targeted to the `_POINT_HANDLED` membership (the
non-discriminating replay-clause mutation demoted); test 15 scoped so it no longer contradicts
test 2's honest-absent edge; the §3 data-flow `sv` type pinned (dict for `_link_source`, pair-list
for the CREATE map); R3's wording corrected ("no anchor produced at all", not "dies at rebuild");
the (C) blind-spot claim softened to "excluded from the compared content view".

**Cycle 4 — 2 verifiers.** Folded: `resolve_source_versions` **must** normalize a scalar ref
(`create_point` keeps a single ref a `str`; iterating it would mint per-character Sources) + the
scalar case is now test 1; `EventAPI.add_point` sets the transit **only when non-None** (no
`sourceVersionTransit: null`); `_upsert_point_edges` guards a falsy value before `dict(...)`; test 2 also
asserts the journal payload has no key; test 15 scoped to the two in-scope producers (the R3 connection leg excluded).

**Cycle 5 — code review (4 always-on + Architecture + Data + Config).** Folded: the generated
`docs/product/sdk-rename-table.md` was REGENERATED after the `sdk.py` insertions shifted its line
numbers (a red CI gate); the node carrier was RENAMED `sourceVersion` → `sourceVersionTransit` so a
Point read does not shadow §4.6's edge scalar with a pair-list; `_upsert_point_props` now validates
the payload shape via ONE shared all-or-nothing `_valid_transit_pairs` predicate, also used by
`_upsert_point_edges` — a per-pair filter on one side and `all()` on the other let a
partially-malformed carrier stamp the edge while writing no node record; `create_source` — the one
writer that bypasses `_sanitize_props` — got its own reject for the three names (the security
review's reproduced hole); the plan §6-17 supersede-boundary test was added (it passes today, so R1's
“the transfer carries nothing” is now pinned); the malformed-payload test now covers a carrier
WITH `extractedFrom` and asserts NEITHER writer anchors; the `EventAPI` reject message no longer
calls a provenance key an embedding field; and the ci-surfaces comments were corrected
(`edges.py` selects `sdk`, not `ep`, so the `ep` registration was dropped as unjustified; the lane is
DOCKER (the #1647 default) — a URI-less run FAILS at the session fixture, and the file is
deliberately NOT carve_out-registered).

**Cycle 6 — code review round 4 (re-review of `ffc5197f4..637bcf8ad`).** The round-3 predicate
hardening exposed an asymmetry the predicate could not fix on its own: `resolve_source_versions`
admitted a hash on truthiness (`h` is truthy for `'   '`), so a whitespace-only `contentHash` —
reachable from `create_source(url, kind, contentHash='   ')`, no validation — was written LIVE
(node carrier `[[DOC,'   ']]`, edge `r.sourceVersion='   '`, since `_anchor_on_create` nulls only
`''`) and DROPPED at replay by `_valid_transit_pairs`: `derived != replay(journal)` for a
public-API input, the exact parity class this change exists to close. **Folded:** the producer now
strip-tests (`if isinstance(h, str) and h.strip()`), so both lanes treat blank as honest-absent;
a LIVE-producer test (`test_whitespace_hash_gets_no_anchor_and_survives_rebuild`) asserts live ==
replay AND `check_consistency` healthy, so the two halves are pinned together; the
`_valid_transit_pairs` docstring no longer claims "`_source_version_transit` skips blank" (it is a
pure mapper and skips nothing — only the now-strip-testing `resolve_source_versions` does); both
call-site shape comments name the non-blank member rule (⚠️ the `_upsert_point_edges` half was
actually still missing — corrected in Cycle 7); and the §3 `EventAPI` sketch, the §8
"four reject surfaces" row and §10's lane sentence were corrected to match §2/§3/§4/§7 and the
corrected ci-surfaces comment. Also: the whitespace-KEY half was checked and is NOT reachable — a
blank `url` is refused by `create_source` (`url must be a non-empty string`), and a padded url
resolves to its non-blank padded key, so `pair[0].strip()` cannot drop a legitimate pair.

**Cycle 7 — code review round 5 (re-review of `748cd6799`).** A confirmed **P1**: a URL-variant ref
LOST the anchor across `rebuild_all` and the gate could not see it. `resolve_source_versions` keyed
its map by `resolve_source_key(g, ref)` — a **live-time resolution** — while `_link_source`
re-resolves the raw ref at replay; when an earlier Point has minted an **unjournaled** stub, the
Source node's stored `url` differs between lanes (live `CANON`, replay `VARIANT`, R6), so the replay
lookup missed and the edge landed bare (reproduced: live `'h1'`, replay `NO_EDGE`/`NULL`,
`check_consistency` green). **Folded:** (1) the carrier is now keyed by the journal-stable **raw
ref** and `resolve_source_key` is used **only to find the Source node** — so `_link_source`'s raw-ref
lookup hits on both lanes; the resolver docstring, `_link_source`'s comment/docstring and
`_source_version_transit` were all corrected (the old rationale was exactly backwards); a regression
test (`test_variant_ref_after_an_unjournaled_stub_keeps_the_anchor`) that FAILS on the pre-fix parent
pins it, and `test_url_variant_ref_uses_the_resolved_key` was **rewritten** (it registered the Source
FIRST and therefore sidestepped the bug, asserting the defect's premise) to
`test_url_variant_ref_keeps_the_anchor_across_rebuild`, which now pins the raw-ref carrier key. (2)
**P2** — the node carrier was gated on its own shape only, so a shape-valid carrier on a Point with
**no** `extractedFrom` planted a stray anchor with no edge (gate-invisible). Both writers now select
the carrier through the shared `_point_source_transit`: written only when the Point OWNS an
`extractedFrom`, and filtered to that Point's **own raw refs** (normalized as `_link_source` does);
`test_carrier_without_an_extractedfrom_is_not_written` and
`test_carrier_keeps_only_the_points_own_refs` pin the two halves. (3) **P3** — the Cycle-6 claim that
BOTH call-site shape comments name the non-blank member rule was false; the rule was added to the
`_upsert_point_edges` call-site comment, making the claim true. Mutation-verified: raw-ref key →
resolved key reddens the new P1 test (and the rewritten variant test); the own-ref filter mutant
reddens the own-refs test, and removing the gate *and* filter additionally reddens the stray-carrier
test (the gate alone is an **equivalent mutant** — the empty ref set makes the filter drop every
pair — recorded as such in §7). The replayed **Source node identity** difference (R6) is pre-existing
and left unfixed; only the anchor is now stable across it.
