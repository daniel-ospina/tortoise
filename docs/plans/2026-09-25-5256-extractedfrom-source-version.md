# Plan — #5256: record the read version on the create-path `extractedFrom` link

**Issue:** #5256 (`complexity:complex`, Level: task, epic #5088) · **Repo:** `daniel-ospina/tortoise`
**Branch:** `feat/5256-extractedfrom-anchor` · **Base:** `origin/docs/5199-version-scope @ 52e703f89` (STACKED on PR #5207)
**Predecessor:** `docs/plans/2026-09-25-5038-source-version-anchor.md` (Task 1, branch `docs/5038-scoping`)
**Review cycle:** 3 (see §10).

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
| **X6** | `batch_id = derive_batch_id(bundle)` runs **before** writes, so a server-observed version is outside the hashed bundle **iff** a fail-closed caller-supplied reject exists | the reject is added at the bundle Phase-1 validator **and** `_sanitize_props` (and the two other boundaries) |
| **X7** | `structural_seen` dedupe drops a differing version | **moot** |

Additional binding rules from #5256: honest-absent (never `''`); `_link_source` must be **handed**
the value and must **never** fall back to `s.contentHash`; no SDK/MCP surface change; reject on
**every** tenant write surface reaching a Point.

## 3. The seam decision (the design work of this issue)

**The version is a declared node property `sourceVersion` on the Point — edge-authoritative,
prop-as-transit — written by its own explicit `SET` clause on both the live and replayed writers.
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
report a divergence on **every** sourced Point). The transit is therefore a list of 2-element
`[resolve_source_key(g, ref), contentHash]` pairs (nested arrays **are** persistable). The **edge**
carries the per-link scalar `r.sourceVersion`.

**ONE builder — `_source_version_transit(versions: dict) -> list | None`** (in `edges.py`) — is used
by **both** producers; it returns `None` (key omitted entirely) when the mapping is empty, so an
un-sourced Point never carries `[]`.

**Key contract (pinned):** a pair's first element is exactly `_mint_source_stub`'s return value —
`resolve_source_key(g, ref)`, the node's stored `url` (NOT `normalize_source_url`). The live resolver
and `_link_source` call the **same** function, so a URL variant cannot silently produce an absent
anchor. (`resolve_source_key` is idempotent; its adopt-on-touch write only touches the **Source**
node, never the Point — it is not a second Point write.)

### Data flow

```
LIVE  create_point(..., extractedFrom=refs)                      # FRESH-CREATE path only
        ├─ sv = resolve_source_versions(proj.g, refs)             # dict[str,str]
        ├─ on the CREATE map:  sourceVersion = _source_version_transit(sv)  # pair-list; omitted when empty
        ├─ CREATE (n:Point {…})                                   # ONE write (#2952)
        ├─ proj._link_source(pid, refs, source_versions=sv)       # _link_source takes the DICT
        │     └─ per ref: MATCH … MERGE (n)-[r:extractedFrom]->(s)  ON CREATE SET r.sourceVersion
        └─ _emit_event("PointAdded", ..., point=self.get_point(pid))  # snapshot carries the prop

LIVE  EventAPI.add_point(..., extractedFrom=…)
        ├─ if self.projection is not None:  p["sourceVersion"] = <the SAME builder>(…)
        └─ _emit("PointAdded", point=p) → log.append + projection.apply
              ├─ pass-1 _upsert_point_props  → explicit SET n.sourceVersion=$sv
              └─ pass-2 _upsert_point_edges  → _link_source(..., source_versions=dict(p["sourceVersion"]))

REPLAY (rebuild_all / recover_from_log) — the SAME two writers, reading p == ev["point"]
```

**Never `s.contentHash` at replay:** pass-2 resurrection calls the same `_link_source`;
`_upsert_source`'s in-place bump is unjournalled (#5024), so reading the Source at replay would
record a version the Point was never read from — a **false current**.

**The dedup path is untouched:** `create_point(dedup=True)` returns early; the resolver runs only on
the fresh-create path, after the early return, so an idempotent re-commit cannot trip the new reject.

**Reject surfaces (all four):** `_sanitize_props` (SDK backstop — also guards `create_document` via
`_create_entity`, `update_point`, `_update_entity`), `_check_item_shape` (bundle Phase 1),
`_SERVER_MANAGED_PROPS` (MCP), and `EventAPI.add_point`'s `_forged` set (the one producer that
bypasses `_sanitize_props`, #5004 round-7 precedent).

## 4. Task breakdown

| # | file | change |
|---|---|---|
| 1 | `tortoise/projection/edges.py` | `resolve_source_versions(g, refs)` — the **LIVE-only** resolver (keyed by `resolve_source_key`, skips empty/`''`/absent). It MUST normalize `refs` exactly as `_link_source` does — `refs = [refs] if isinstance(refs, str) else list(refs)` — else a scalar ref iterates **characters** and mints per-character Sources (`create_point` keeps the common single-source ref a scalar). `_source_version_transit(versions)` — the ONE pair-list builder (None when empty). `_link_source(..., source_versions: dict[str,str] \| None = None)`: the MERGE binds `r` and appends `_anchor_on_create("$v")` (reuses the #5199 helper — one source of truth for the `''`/NULL guard); `$v` is **always bound** (None when absent, for the bare callers: `create_document`, the ingest connection leg, `_link_extracted_from`, direct test calls). **Never** reads `s.contentHash`. |
| 2 | `tortoise/projection/entities.py` | `_POINT_HANDLED \| {"sourceVersion"}`; an explicit conditional `SET n.sourceVersion=$sv` clause in `_upsert_point_props` (only when the payload carries a non-empty list of pairs); `_upsert_point_edges` uses `p.get("sourceVersion")` and, **only when it is a non-empty list of 2-element sequences**, hands `dict(...)` to `_link_source` (a falsy/None value must never reach `dict()`). |
| 2b | `tortoise/projection/__init__.py` | add `n.sourceVersion = NULL` to the #4042 pass-1a **recreate wipe** (`embedding` / `content_hash` / `embedding_verbatim`). Without it, a delete→same-id-recreate whose new snapshot omits `extractedFrom` retains the dead incarnation's transit in the rebuilt graph while live has none — a `derived = replay(journal)` break of exactly the `embedding_verbatim` class (#5004 round-4 precedent). |
| 3 | `tortoise/sdk.py` | `_sanitize_props` rejects `sourceVersion`/`sourceVersions`; `_check_item_shape` rejects them on bundle items (Phase 1, free); `create_point` resolves the LIVE versions on the **fresh-create** path and puts the transit in the `CREATE` map (no second write — #2952). |
| 4 | `tortoise/api.py` | `EventAPI.add_point`: reject `sourceVersion`/`sourceVersions` in the existing `_forged` set; when `getattr(self.projection, "g", None) is not None`, resolve and set `p["sourceVersion"]` via the shared builder — **only when the builder returns non-None** (an un-sourced Point's payload must not carry `sourceVersion: null`). The `getattr` guard covers **both** `projection=None` and an `InMemoryProjection` (no `.g`). |
| 5 | `tortoise/consistency.py` | a declaration comment only: `sourceVersion` is a **declared, compared** node property (`_POINT_HANDLED`), deliberately **not** `_EXCLUSION_REASONS`-excluded — excluding it would be a blind spot. |
| 6 | `tortoise/mcp_server.py` | `_SERVER_MANAGED_PROPS \| {"sourceVersion", "sourceVersions"}` (#5004 convention). No surface change. *(Component addition: #5256's list omits `api.py`/`mcp_server.py`; both are required by indicator 3.)* |
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
   `sourceVersion` key. (Acceptance 2, at every home.)
3. **false-current guard** — create at `h1`; advance the Source to `h2` via a **journaled**
   `create_source(contentHash='h2')`; `rebuild_all` ⇒ the edge still reads `'h1'`, not `'h2'`.
4. **per-link** — two Sources with distinct hashes ⇒ each edge carries its own; survives rebuild.
5. **URL-variant key contract** — a Source registered canonically, linked via a variant ref ⇒ the
   edge carries the hash and survives rebuild.
6. **EventAPI lane** — `add_point(..., extractedFrom=…)` with a projection ⇒ anchored; survives
   rebuild.
7. **EventAPI without a Falkor graph** — `add_point(..., extractedFrom=…)` with `projection=None`
   **and** with an `InMemoryProjection` (no `.g`) must not raise (the extractor live lanes).
8. **dedup re-commit** — `create_or_update_point(..., extractedFrom=…)` twice ⇒ no raise, no
   divergence.
8b. **delete→same-id-recreate** — hard-delete a sourced Point, then create a new Point with the
    same id and **no** `extractedFrom`; `rebuild_all` ⇒ no `sourceVersion` on the rebuilt **node**
    (mirrors `test_a_recreated_point_does_not_inherit_the_verbatim_marker`, #5004). ⚠️ The test
    asserts the NODE only and says why: the old incarnation's **`extractedFrom` edge** is already
    resurrected by pass 2 today (pre-existing, independent of this anchor) — see residual R5; the
    node wipe closes the part this change would otherwise introduce.
9. **reject: `create_point(**props)`** — both keys raise; nothing written.
10. **reject: `ingest` bundle** — a point item carrying the key raises at Phase-1; zero mutation.
11. **reject: `create_document`** — the Document surface refuses.
12. **reject: `update_point`** — the props path refuses.
13. **reject: `EventAPI.add_point`** — the extractor seam refuses.
14. **reject: MCP boundary** — `_reject_server_managed_props({...})` returns an error.
15. **no scalar stray / transit⇔edge** — for a **hash-bearing** fixture created through
    `create_point`/`EventAPI.add_point`, the node transit is a list of `[ref, hash]` pairs and, at
    creation, exists **iff** the Point has an `extractedFrom` edge; no Point carries a scalar
    `sourceVersion`. (Scoped twice: the honest-absent case in test 2 legitimately has an edge and
    **no** transit; and the ingest **connection** leg, R3, is out of scope.)
16. **gate comparison** — seed the graph through the **replay writer** (`rebuild_all`/`apply`, as
    `test_consistency_divergence_5011.py`'s `_seed` does — NOT `create_point`, whose CREATE-map
    write bypasses the clause), assert the faithful fixture is healthy (`check_consistency(...)["ok"]
    is True`), **then** tamper the graph's `n.sourceVersion` and assert it is reported as a
    divergence. The positive half is what pins the clause; the mapped mutation is removing
    `sourceVersion` from `_POINT_HANDLED` (see §7 item 6).
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
  3. each reject site → tests 9/10/11/12/13/14;
  4. the replay transit consumption (`_upsert_point_edges`) → test 1 (rebuild parity);
  5. the resolver key (`resolve_source_key` → raw ref) → test 5;
  6. **`_POINT_HANDLED` membership** (remove it) → test 16: the key then falls to `_uncarried`
     (`_UNCARRIED_LIST`) and is `skip`-ped from both sides, so the tampered mismatch becomes
     invisible — the gate's `ok` goes back to True and test 16 goes RED. (Separately: removing the
     `_upsert_point_props` clause → test 1 / test 16's positive half.)
  7. `EventAPI.add_point`'s graph guard → test 7;
  8. the #4042 recreate wipe (`n.sourceVersion = NULL`) → test 8b.

## 8. Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Ontology | medium | the anchor is canonical; no wording change |
| Architecture | high | replay determinism, the journal seam, two live producers |
| Code | high | `edges.py` + `entities.py` + `sdk.py` + `api.py` + `consistency.py` + four reject surfaces |
| Overall | **complex** | matches `complexity:complex` |

## 9. Residuals (documented, not chased)

| # | severity | residual |
|---|---|---|
| R1 | P1 | The **transfer** half (supersede/pass-2b) is out of scope — a §4.6 reopen (O1, owner-only). A superseded successor gets **no** anchor; under §4.6's *"the version it was read from"* that is honest. |
| R2 | P1 | The capture path links before `_materialize_session_source` sets the hash ⇒ **absent** (O6) — the honest value; reordering is a separate owner decision. |
| R3 | P2 | The ingest `extractedFrom` **connection** leg calls `_link_source` with **no** `source_versions`, so it produces **no anchor at all** (not an anchor that later dies). Its `extractedFrom` edge is also absent from the Point snapshot, so the edge itself dies at rebuild (pre-existing). Left bare. |
| R4 | P2 | The `references` anchor (#5199) reads `s.contentHash` at link time and records its own stale-under-in-place-rebuild limitation; unchanged here. |
| R5 | P2 | **Pre-existing, not introduced:** on a hard-delete→same-id-recreate, pass 2 re-creates the *old* incarnation's `extractedFrom` **edge** (which then carries its old `r.sourceVersion`) even though the live graph has none — the same class of gap `n.extractedFrom` itself already has (the recreate wipe @4042 clears node props, never edges). This change closes the **node-prop** half (the #4042 wipe) and records the edge half here; fixing edge-incarnation handling in pass 2 is a separate defect, out of scope. |

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

**Cycle 3 — 2 verifiers.** Folded: the #4042 pass-1a **recreate wipe** must clear `n.sourceVersion`
(new task row 2b + test 8b); test 16 must seed through the replay writer and assert the faithful
fixture healthy **first**; mutation-verify #6 re-targeted to the `_POINT_HANDLED` membership (the
non-discriminating replay-clause mutation demoted); test 15 scoped so it no longer contradicts
test 2's honest-absent edge; the §3 data-flow `sv` type pinned (dict for `_link_source`, pair-list
for the CREATE map); R3's wording corrected ("no anchor produced at all", not "dies at rebuild");
the (C) blind-spot claim softened to "excluded from the compared content view".

**Cycle 4 — 2 verifiers.** Folded: `resolve_source_versions` **must** normalize a scalar ref
(`create_point` keeps a single ref a `str`; iterating it would mint per-character Sources) + the
scalar case is now test 1; `EventAPI.add_point` sets the transit **only when non-None** (no
`sourceVersion: null`); `_upsert_point_edges` guards a falsy value before `dict(...)`; test 2 also
asserts the journal payload has no key; test 15 scoped to the two in-scope producers (the R3
connection leg excluded); the pre-existing recreate **edge** resurrection recorded as residual R5
(test 8b asserts the node only, and says why); §3's shape wording aligned with the pinned
`resolve_source_key` key contract.
