# Scoping — #1933: agent-ops rules-with-why starter pack (pack + fixtures + integration tests)

> Double-diamond scoping, proportional to a well-specified standard task
> (epic #1891 slice; the epic plan §3 sketch, §4 data model, §7 E2E-4, and
> test-design #1898 surface 11 pin the targets). Complexity: **standard**.
> Prior research: epic `05-plan.md` (§3 manifest sketch, §4 data model, §7
> E2E-4/5), test-design #1898 surface 11, issue body O/I/T + verification
> checklist. No external gate queries needed (all axes low — no novel
> pattern, no new deps; the schema is the shipped-pack precedent).

## Phase 1/2 — Problem diamond (proportional)

### Confirmed problem

The agent-ops rules-with-why pattern (the 2026-08-28 sales conversation's
exact use case: agents store rules + the why, and rewrite rules with
reasoning intact) has **no shipped pack**. The core ontology has the
primitives (`standard`, `statement`, IMPL), but nothing operationalizes the
pattern for extraction: no `rule` kind, no `rationale` point kind, no
`groundedIn` relation, no `ruleLifecycle` chain, no memory-granularity
guidance, and no default activation. Concretely, on current main:

- `packs/` ships 4 packs (dev, marketing, product-strategy, pm);
  `DEFAULT_STARTER_PACKS = ("dev", "marketing", "product-strategy", "pm")`
  (pack_state.py:31).
- The extractor's master list hardcodes `PACK_NS =
  ("product-strategy:", "dev:", "marketing:", "pm:")` (extractor_v2.py:108)
  — a 5th pack namespace would be invisible to the S2/S4 prompt vocabulary.
- No `packs/agent-ops/` manifest exists; no `tests/fixtures/expansion-epic/`
  fixtures exist (E2E-4/5 depend on them).
- No integration coverage for a rules-with-why mining → supersede lifecycle.

**Falsification check:** if the pack + starter-set change were already done,
`PackRegistry(default_packs_dir()).load_all()` would report 5 packs incl.
`agent-ops`, `DEFAULT_STARTER_PACKS` would be length 5, and
`compile_value_brief()["memory_granularity"]` would contain an `agent-ops`
key. All three fail on current code. Confidence: **95** (the epic plan +
issue + merged #1929 packaging foundation pin the targets; residual 5% =
schema-validation details of the manifest, resolved empirically during
implementation).

### Rejected problem framings

- *"The extraction pipeline needs a new rules-with-why mining mode"* —
  rejected: the v2 5-stage pipeline (S1→S5, extractor_v2) already mints
  pack kinds via the S2/S4 embed list + `execute_embed` gates (FIX M
  object kinds, FIX A event kinds). The pack declares the vocabulary; the
  mock-model integration tests drive the existing pipeline deterministically.
- *"The point write gate must be extended so pack point kinds (e.g.
  agent-ops:rationale) survive extraction"* — rejected: FIX P (issue #1695
  task) pins that pack point kinds repair to `statement` at the write gate
  (`test_point_kind_report_agrees_with_point_gate` asserts
  `dev:requirement → statement`). The rationale point is minted as a
  statement Point carrying the why-content; the `rationale` pointKind is
  writable via the SDK/`create_point` (known_kinds("pointKind") includes
  pack point kinds) — the supersede test uses that surface.
- *"Chain-completeness enforcement belongs to the commit path domain
  validators"* — rejected after research: `_infer_payload_domain`
  (commit_schema.py) scores payload **pointKinds** only; the no-reasoning
  variant has zero agent-ops points (the rationale was never minted), so
  domain inference returns None and payload-local validators never run. The
  completeness warning must be produced at **extraction level** (on the
  embed list, pre-repair, where the rationale pointKind is still visible).

## Phase 4/5 — Solution diamond

### Alternative A — Ship the pack + starter-set + fixtures + offline integration tests (RECOMMENDED)

Exactly per the epic plan §3 sketch and the issue's targets:

1. `packs/agent-ops/manifest.yaml` — namespace `agent-ops`, name "Agent
   Operations", version 0.1.0, tier free; `ontology: extends core,
   objectKinds [rule], subclassOf {rule: standard}, pointKinds [rationale],
   eventKinds [ruleRevised]`, relation `groundedIn` (rule -IMPL-> rationale,
   extractable: true), chain `ruleLifecycle` [rule, rationale, ruleRevised]
   enforcement warn, `memory_granularity` (durable: rule text + situation +
   reasoning; ephemeral: mechanics/logistics), `extraction: active,
   sourceTypes [conversation], enforcement.kinds {rule: retry}` (the E2E-5
   fixture relies on the resolution `PackManifest.enforcement_for("rule")
   == "retry"`).
2. `tortoise/pack_state.py` — `DEFAULT_STARTER_PACKS` gains `agent-ops`
   (defaults become 5; the CI smoke bound `len(DEFAULT_STARTER_PACKS)`
   self-adjusts — verified bound sites: publish-selfhost.yml:46,
   test_pack_shipping_wheel.py:129).
3. `tortoise/extractor_v2.py` — `PACK_NS` gains `"agent-ops:"` (the master
   list's pack_kinds feed the S2/S4 prompts) + `_PACK_TRIGGERS` entry
   (compact-mode consistency). FIX M/A gates already read the registry, so
   `agent-ops:rule` entities and `agent-ops:ruleRevised` events survive
   `execute_embed` without further changes.
4. Chain-completeness warning (E2E-4 negative a): a small deterministic
   `validate_chain_completeness(embed_list)` in extractor_v2, wired into
   `extract_session_v2`'s chain-enforcement block, driven by the
   **pack-declared** chains (from PackRegistry) EXCLUDING the canonical
   hardcoded CHAINS (productDelivery/epicToCode/campaignToChannel — their
   enforcement semantics are established via the graph/payload validators and
   must not change). Trigger (scope-verify P1-1 refinement) = find the LOWEST
   step index with an emitted item; if the NEXT step (index+1) has NO emitted
   item → warn, naming the missing step (ruleLifecycle: rule emitted +
   rationale absent → warn even when a ruleRevised event is present — a
   rewrite without the why is still incomplete). Severity from the chain's
   manifest enforcement (warn).
4b. **Commit Layer-1 event-vocabulary fix (P0-1, confirmed by both
   verifiers):** `tortoise/commit_schema.py` Layer-1 validates payload events
   against a HARDCODED `EVENT_KINDS` set (line ~706) — a mined `ruleRevised`
   event (bare form, per execute_embed's FIX-A stripping) would 422. Amend
   `Vocab`/`compile_vocab` to compile pack `event_kinds` bare + `ns:kind`
   (mirroring the existing pointKind pattern at commit_schema.py:126-152)
   and drive the Layer-1 event check from it. Additive-only; the only
   existing rejection test uses `"banana"` (test_commit_schema.py
   test_events_bad_kind_rejected) — unaffected.
5. Fixtures `tests/fixtures/expansion-epic/rules_with_why.txt`,
   `rules_no_reasoning.txt`, `near_miss.txt` per the plan §7.
5b. `tortoise/commit_schema.py`: pack-aware Layer-1 event vocabulary (the
   P0 fix — compile_vocab gains `event_kinds`; the line-706 check drives
   from it).
6. Integration tests (offline MockModel, docker lane):
   - Happy-path mining (extract_session_v2 → /v1/sessions/commit via the
     test_commit_endpoint client pattern) mints rule Object
     (objectKind agent-ops:rule — namespaced, the entity lane keeps it
     verbatim) + rationale Point (pointKind statement, aboutObject → rule)
     + groundedIn IMPL edge (rationale -IMPL-> :Event with eventKind
     `ruleRevised` — BARE, per execute_embed's FIX-A stripping; P1-2) +
     session linkage: rationale point -[:extractedFrom]-> Source
     {url: 'session.md'} + :Session CONTAINS the points + the ruleRevised
     :Event aboutObject → rule Object (P2-2: assert the actually-minted
     shape — the v2 payload carries no documents key, so no Document hop).
   - No-reasoning variant: rationale NOT minted + chain-completeness
     warning (ruleLifecycle).
   - Supersede (SDK-level): rule point (kind statement) + rationale point
     (kind `rationale` — BARE, the known_kinds('pointKind') form; P2-6) +
     IMPL operator (direction="unidirectional" — the groundedIn/extraction
     convention; P2-4) + strong baselines (rationale Beta(8,2)) + dream →
     supersede → EP cascade (new rule confidence moves, rationale points
     retained, IMPL transferred) + negative b: the re-propagation FAILS
     (monkeypatched sdk.dream raising — the unreachable-graph mechanism;
     P2-1) → `sdk._get_ep().get_contested_claims()` (the RAW EP surface —
     the SDK's own contested-claims review deliberately does NOT flag
     unmeasured priors; P1-3) surfaces the NEW RULE'S point id with
     variance > 0.04 (filtered to that id — Beta(1,1) fallback = 1/12 ≈
     0.083); control: after a successful dream the new rule's id is NOT
     in the contested set. Avoid sdk.get_confidence() pre-dream (its
     lazy-consistency would dream the dirty roots and wipe the negative-b
     state).
   - R7 extractor test: `compile_value_brief()` (and `build_master_list`)
     contains the agent-ops memory_granularity text (keep the assertion on
     compile_value_brief / the verbose render — the compact render's
     granularity subsection is hardcoded to dev/product-strategy triggers).
   - R8 upgrade-convergence test: old 4-pack tenant → `ensure_tenant_packs`
     with the new default converges to 5 (agent-ops active, idempotent);
     `monkeypatch.delenv("TORTOISE_STARTER_PACKS")` so the env override
     cannot poison the default (P2-7).
   - Enforcement-resolution check (not behavior — #1934 forward):
     `PackManifest.enforcement_for("rule") == "retry"` (the E2E-5
     fixture's dependency; the retry WIRING is #1934's).
   - `near_miss.txt` is created for #1934 (E2E-5) consumption — no test in
     this slice consumes it (P2-3 forward-contract note).
7. Register the new test file(s) in `config/ci-surfaces.yml` (core surface —
   the --integrity drift gate).

### Alternative B — Point-gate extension (rationale survives as its own kind)

Extend `execute_embed`'s point gate so pack point kinds pass through
unrepaired. Rejected: breaks the pinned FIX P contract
(`test_point_kind_report_agrees_with_point_gate`), touches the classify-
later A/B surface (#1695) that another epic is actively measuring, and is
not needed by any issue assertion (the rationale rides as a statement Point;
the pack's pointKind is reachable via the SDK).

### Alternative C — Commit-path domain-validator completeness warning

Register a payload_local validator for agent-ops + extend
`_infer_payload_domain` to score entity kinds. Rejected: the rationale is
repaired to `statement` before the payload exists, so a payload-level
validator cannot deterministically find the rationale; the extraction-level
check (A4) sees the pre-repair embed list.

## Axis Research (issue #231 D11)

### `### Axis Research` — light pass (justified)

| Axis | Rating | Basis |
|------|--------|-------|
| Ontology | standard | The manifest schema is the 4 shipped packs' precedent (packs/dev/manifest.yaml); validation rules read directly from pack_registry.py (kindDefs/chains/relations/extraction sections) |
| Architecture | standard | DEFAULT_STARTER_PACKS consumer list verified: pack_state.py + graph-scripts/backfill_pack_installs.py + tests (test_pack_state, test_pack_shipping, test_pack_shipping_wheel) + .github/workflows/publish-selfhost.yml — all derive from the constant or the catalog, no hardcoded 4-count outside tests |
| Extraction | standard | v2 gates (FIX M/A) read the registry, not the hardcoded master; only PACK_NS + _PACK_TRIGGERS need the agent-ops entry |

### `### Integration Docs` — justified skip

No new third-party dependencies. No new external APIs. The only "integration"
is with the existing pack registry (in-repo), the v2 extractor (in-repo),
and the commit endpoint (in-repo, test pattern at tests/test_commit_endpoint.py).

### Verification checklist (from the issue)

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| 11 Agent-ops pack | integration (offline mock models) + ontology validation | manifest valid; mining mints rule+rationale+IMPL; no-reasoning variant warns; supersede re-propagates EP |
| 2 Packaging interaction | CI smoke (coordinated) | starter-set count becomes 5 on built artifacts (bound = len(DEFAULT_STARTER_PACKS)) |

### Complexity ratings

| Domain | Rating | Rationale |
|--------|--------|-----------|
| Ontology | standard | New pack kinds/relations/chain + starter-set change |
| Architecture | standard | DEFAULT_STARTER_PACKS + convergence behavior + the commit Layer-1 event-vocab consumer (commit_schema.py) |

## Phase 8 — Implementation checklist (summary)

1. `packs/agent-ops/manifest.yaml` (validate via `PackRegistry.load_all()`).
2. `tortoise/pack_state.py`: DEFAULT_STARTER_PACKS + "agent-ops".
3. `tortoise/extractor_v2.py`: PACK_NS + _PACK_TRIGGERS + 
   `validate_chain_completeness` wiring.
4. Fixtures x3.
5. Tests: `tests/test_agent_ops_pack.py` (extraction happy-path +
   no-reasoning + R7) and `tests/test_agent_ops_supersede.py` (supersede EP
   cascade + negative b) + `tests/test_pack_state.py` upgrade-convergence
   addition (or a dedicated test in the new file).
6. `config/ci-surfaces.yml` registration + `ci_selection.py --integrity`.
7. Run slice tests on the docker lane (TORTOISE_DB_URI).
