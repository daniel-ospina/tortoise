# Implementation Plan — #1933: agent-ops rules-with-why starter pack

> Level: task · Complexity: standard · Skill: task-workflow-standard
> Scoping: `docs/scoping/2026-08-30-1933-agent-ops-pack-scoping.md` (verified:
> 2 scope verifiers; all findings incorporated — P0 commit-Layer-1 event-vocab
> fix, P1 chain-completeness trigger refinement, P2s test-design details).
> Prior research: epic `docs/epics/2026-08-28-1891-expansion-packs/05-plan.md`
> §3 sketch / §4 / §7 E2E-4/5 / §8 R7-R8; test-design #1898 surface 11.

## Design decisions (locked)

1. **Pack content** — the epic §3 sketch with TWO verified amendments:
   (a) **NO `subclassOf`** (plan-verify P0-1, verified empirically: the shared
   validator requires PascalCase parents in CANONICAL_OBJECT_KINDS — both
   `{rule: standard}` and `{rule: Standard}` fail; test_pack_registry_gaps.py
   pins lowercase→error, so the validator is not changed; the semantic
   "rule is a kind of standard" rides `kindDefs.rule.nearMisses: [standard]`
   instead — noted in the PR); (b) everything else per the sketch: namespace
   `agent-ops`, name "Agent Operations", version 0.1.0, tier free.
   `ontology:` extends core; objectKinds `[rule]`; pointKinds `[rationale]`;
   eventKinds `[ruleRevised]`; relation `groundedIn` (predicate camelCase,
   mechanism IMPL, fromKind `agent-ops:rule`, toKind `agent-ops:rationale`,
   extractable true); chain `ruleLifecycle` steps `[rule, rationale,
   ruleRevised]` enforcement warn; `memory_granularity` (durable: rule text +
   situation + reasoning; ephemeral: mechanics/logistics). `extraction:`
   active true, sourceTypes `[conversation]`, enforcement.kinds `{rule:
   retry}`. Schema note: `memory_granularity` lives under `ontology:` (the 4
   shipped packs' placement; value_extractor.py reads
   `ontology.memory_granularity`).
2. **Starter set** — `DEFAULT_STARTER_PACKS` gains `agent-ops` (5 total).
   CI smoke bound `len(DEFAULT_STARTER_PACKS)` self-adjusts (verified sites:
   .github/workflows/publish-selfhost.yml:46, tests/test_pack_shipping_wheel.py:129).
3. **Extractor visibility** — `PACK_NS` (extractor_v2.py:108) gains
   `"agent-ops:"` so the S2/S4 prompt vocabulary carries the pack kinds;
   `_PACK_TRIGGERS` gains an agent-ops entry (compact-mode consistency).
   Entity/event write gates (FIX M/A) already read the registry — no change.
4. **Commit Layer-1 event vocab** (P0) — `commit_schema.py` `Vocab`/
   `compile_vocab` gains `event_kinds` (bare + `ns:kind` from each pack's
   eventKinds, mirroring the pointKind pattern); `validate_layer1`'s event
   check drives from `vocab.event_kinds` instead of the hardcoded set.
   Additive-only (core kinds remain accepted; the `"banana"` rejection test
   stays green).
5. **Chain-completeness warning** (E2E-4 negative a) — new
   `validate_chain_completeness(embed_list, master)` in extractor_v2: for
   each PACK-DECLARED chain whose id is NOT in the canonical hardcoded
   `CHAINS` dict (the exclusion references `CHAINS` directly — plan-verify
   P2-4; productDelivery/epicToCode/campaignToChannel have established
   enforcement and must not change), compute the lowest step index with an
   emitted item (entity kinds + point kinds + event kinds, bare-form
   matching); if the NEXT step has no emitted item → note {chain, finding,
   missing step, action: warned} + a warning string appended to
   `result["warnings"]`. **Zero emitted items for any step → no-op (no
   note, no warning)** — plan-verify P2-3, so unrelated sessions never
   flood warnings. A ruleRevised-only embed (step 2 emitted, no next step)
   never warns — accepted and documented (a revision without its rule is
   out of this slice's chain semantics). Severity from the manifest chain
   enforcement (`warn`). ruleLifecycle: rule emitted + rationale absent →
   warn (ruleRevised presence does NOT satisfy the missing rationale).
   **Wire-in (plan-verify P2-1):** the chain-enforcement block runs before
   `result` exists — collect the notes into a local list there, then merge
   `result["chain_completeness"]` + warnings AFTER `result` exists (at the
   `result["chain_enforcer"]` assignment ~:3664 AND in the S5-failure
   branch ~:3660 so the failure path also carries the notes).
6. **Rationale pointKind through extraction** — the point write gate keeps
   FIX P (repair pack point kinds → statement). The rationale lands as a
   statement Point carrying the why-content; the `rationale` kind is the
   pack semantic and is SDK-writable (`create_point(kind="rationale")`).
   **Mock embed-list contract (plan-verify P1-1):** the happy-path mock
   MUST emit the rationale point with `pointKind: "agent-ops:rationale"`
   (the pre-repair form) so the completeness check sees step 1 — the
   committed node then carries `statement` (the post-repair form).
7. **Test graph shape (happy path)** — rule :Object {objectKind
   agent-ops:rule}; rationale :Point (statement) aboutObject → rule;
   groundedIn IMPL : rationale -[:IMPL]-> :Event {eventKind: ruleRevised}
   (bare — FIX-A stripping); provenance: rationale -[:extractedFrom]->
   Source {url: "session.md"}; :Session CONTAINS points. No Document-hop
   assertion (v2 payload carries no documents key).
8. **Negative-b (supersede failure)** — re-propagation fails via
   monkeypatched `sdk.dream` raising; contested check on the RAW EP surface
   `sdk._get_ep().get_contested_claims()` filtered to the new rule's id
   (Beta(1,1) fallback variance 1/12 ≈ 0.083 > 0.04). Control: successful
   dream → id absent.

## Tasks

### T1 — `packs/agent-ops/manifest.yaml` (new)
Per D1. Validate: `PackRegistry(default_packs_dir()).load_all()` → 5 packs,
`registry.errors` empty for agent-ops; `enforcement_for("rule") == "retry"`;
`relation_is_extractable(groundedIn)` true; `domain_chain_spec("agent-ops")
["ruleLifecycle"]["enforcement"] == "warn"`.

### T2 — `tortoise/pack_state.py`
`DEFAULT_STARTER_PACKS = ("dev", "marketing", "product-strategy", "pm", "agent-ops")`
(comment updated). R8 convergence is automatic (idempotent additive MERGE).

### T3 — `tortoise/extractor_v2.py`
- `PACK_NS` += `"agent-ops:"`.
- `_PACK_TRIGGERS` += `"agent-ops:"` with LOW-NOISE triggers (plan-verify
  P2-2: avoid the bare "rule"/"rules" substrings that match "scheduled",
  "unstructured", … — use distinctive phrases like "standard operating",
  "protocol", "token acknowledgement", "destructive action").
- New `validate_chain_completeness(embed_list, master=None) -> list[dict]`
  (D5) + wire into `extract_session_v2` per D5's wire-in contract (local
  list at the chain-enforcement block; merge into `result` after it
  exists, incl. the S5-failure branch).

### T4 — `tortoise/commit_schema.py` (P0 fix)
- `Vocab` gains `event_kinds: frozenset[str]`; `compile_vocab` compiles
  bare + `ns:kind` forms of each pack's `event_kinds` (union with the
  canonical `EVENT_KINDS`); `validate_layer1`'s event-kind check uses
  `vocab.event_kinds`. `Vocab.__contains__` extended to include
  `event_kinds` (plan-verify P2-1 — keeps the helper's contract honest).
- Keep the hardcoded `EVENT_KINDS` constant as the canonical-core base.

### T5 — Fixtures (new `tests/fixtures/expansion-epic/`)
- `rules_with_why.txt` — a session where the agent states a rule WITH
  reasoning ("destructive actions require a verbal token acknowledgement
  because a prior incident of unacknowledged destructive action caused
  rollback loss…").
- `rules_no_reasoning.txt` — the SAME rule stated WITHOUT reasoning.
- `near_miss.txt` — content confusable between `agent-ops:rule` and core
  `standard` (a "standard operating procedure" phrasing; #1934 consumer).

### T6 — `tests/test_agent_ops_pack.py` (new; core surface)
Offline MockModel (existing precedent), docker lane. Helpers: fixture→EDU
conversion, a 3-response MockModel (S1 story / S2 embed list / S4 same list
no-op merge), commit via the test_commit_endpoint client pattern
(patched TortoiseSDK init → temp DB).
- `test_happy_path_mining_mints_rule_rationale_impl`: mock embed list =
  rule entity (agent-ops:rule) + ruleRevised event + rationale point with
  **pointKind "agent-ops:rationale"** (pre-repair; committed as statement)
  + IMPL rationale→event. Post the extractor payload UNCHANGED (the
  extractor already finalizes `client_commit_id` via compute_client_commit_id
  — plan-verify P2-2: do not mutate hashed fields session_id/points/
  entities/operators/summary/story_arc/events/supersessions between
  extraction and post). Commit → assert :Object {objectKind
  agent-ops:rule}, rationale :Point + aboutObject → rule, IMPL edge to
  :Event {eventKind ruleRevised}, extractedFrom → Source(session.md),
  Session CONTAINS rationale, no ruleLifecycle completeness warning.
- `test_no_reasoning_variant_no_rationale_and_chain_warning`: mock =
  rule entity only → assert no rationale point, chain-completeness warning
  naming ruleLifecycle/rationale.
- `test_r7_memory_granularity_reaches_value_brief`:
  `compile_value_brief()["memory_granularity"]["agent-ops"]` contains
  "Durable"/"rule text"; `build_master_list()["memory_granularity"]`
  mirrors it.
- `test_enforcement_rule_retry_resolution`: registry-level
  `enforcement_for("rule") == "retry"` (E2E-5 forward contract).

### T7 — `tests/test_agent_ops_supersede.py` (new; core surface)
SDK-level (no HTTP):
- `test_supersede_ep_cascade_retains_argument_tree`: rule point
  (statement) + rationale point (kind "rationale") + IMPL
  (direction="unidirectional") + baselines (rationale Beta(8,2), rule
  Beta(2,6)) → dream → record pre-supersede confidence → create NEW rule
  point → supersede_point → assert old superseded + CORRECTS edge; IMPL
  transferred to new rule; rationale points retained (count unchanged);
  dream → new rule confidence moved off baseline (EP cascade).
- `test_supersede_failure_path_contested_claims` (negative b): same setup,
  supersede, monkeypatch `sdk.dream` to raise (unreachable graph) →
  `sdk._get_ep().get_contested_claims()` includes the NEW rule id with
  variance > 0.04; then un-patch, dream succeeds, `get_contested_claims`
  does NOT include the new rule id (control).

### T8 — `tests/test_pack_state.py` (add upgrade-convergence)
`test_old_4pack_tenant_converges_to_agent_ops_after_upgrade`:
`monkeypatch.delenv("TORTOISE_STARTER_PACKS")`; ensure with the old 4-name
starter → 4 installs; ensure with the new default → 5 installs incl.
agent-ops, no duplicates (idempotent MERGE). Plus assert
`DEFAULT_STARTER_PACKS` length 5 and `_expected_defaults()` includes
agent-ops (the existing helper self-adapts — check it still passes).

### T9 — `config/ci-surfaces.yml`
Register `test_agent_ops_pack.py` + `test_agent_ops_supersede.py` under
`core:` (alphabetical). Run `python3 tools/ci_selection.py --integrity`.

## Verification
1. Docker lane: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`
   → run test_agent_ops_pack.py, test_agent_ops_supersede.py,
   test_pack_state.py, test_commit_schema.py, test_commit_endpoint.py,
   test_extractor_v2.py, test_value_extractor.py, test_pack_shipping*.py
   (regression around the P0/T3 changes).
2. `python3 tools/ci_selection.py --integrity` clean (new files registered).
3. commit-workflow skill (mandatory) → PR with pre-existing-CI-failures note
   if main's known failures surface.
