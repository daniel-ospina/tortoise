---
title: "Namespaced Enforcement Resolution — Implementation Plan (#2030)"
type: engineering
domain: capability
doc_status: draft
created: 2026-08-30
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise enforcement seam, kind classifier
---

<!-- research-path: docs/plans/2026-08-30-namespaced-enforcement.md -->

# Namespaced Enforcement Resolution Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make `resolve_enforcement` resolve namespaced `ns:local` kinds against the declaring pack — so the classifier near-miss hook's `retry` signal fires for `agent-ops:rule` (the dead path in practice, since 78/79 index kinds are namespaced) — without changing bare-kind behavior.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Resolver-side namespace dispatch in the ONE shared seam (#1934): `resolve_enforcement` (tortoise/enforcement.py:38-75) parses `ns:local` via `rpartition(":")`, resolves the local name against `reg.get_pack(ns)` EXCLUSIVELY (namespace-SCOPED like the `KindIndex.near_misses` precedent, kind_index.py:277-301, but STRICTER — declaring pack only, NO any-namespace fallback: an unknown/malformed namespace degrades to `warn`, never raises). Bare kinds keep the cross-pack max-severity scan unchanged. The classifier near-miss hook (kind_classifier.py:282-290) then receives the correct signal; its stats dict gets an explicit `near_miss_retries: 0` init and a truncated comment is completed (hygiene only, zero behavioral change). The `create_operator` relation check (sdk.py:3898-3911) is verified bare-vs-bare — NOT a bug — documented only (pin d).

### Pattern Research

> **Findings date:** 2026-08-30

> **Gate skipped:** plan touches zero third-party dependencies — pure in-repo Python (tortoise/enforcement.py, tortoise/kind_classifier.py, tortoise/sdk.py) + stdlib; test infra (pytest/numpy) is already in-repo and untouched. No library-version / idiomatic-usage / pitfalls buckets apply (writing-plans workflow/02 skip rules).

**Library docs (preflight)** — no third-party deps in plan — skipped.

### Integration Surface Map

| Surface | Specific Surface | Data Flow | Contract | Test Layer |
|---|---|---|---|---|
| Pack registry (filesystem manifests) | `PackRegistry.get_pack(ns)` (pack_registry.py:1128) — `reg.packs.get(ns)`; packs keyed by namespace (verified: `agent-ops`, `dev`, `marketing`, `product-strategy`, `pm`; NO `core` pack) | Read | `get_pack(ns) -> PackManifest \| None`; `enforcement_for(local) -> "warn"\|"retry"\|"block"` (bare-keyed, pack_registry.py:251-260) | Unit + Integration (registry loaded from repo `packs/` in tests; conftest `_packs_env_isolation` resets `_registry`/`_PACKS_DIR`/env per test) |
| Kind index | `KindIndex.near_misses(kind)` (kind_index.py:277-301) — same-namespace-first precedent the resolver mirrors | Read | Namespaced nearMiss refs resolve same-namespace-first, then any namespace; never cross-ns when pack's own scope matches | Integration (via classifier) |
| Classifier near-miss hook | `KindClassifier.classify_items` → rerank branch → hook (kind_classifier.py:282-290) | Internal state mutation | `stats["near_miss_retries"]` incremented iff any of `[reranked, *near_misses]` resolves `retry`; never raises (fail-open) | Integration (stub encoder, controlled fixture) |
| Seam callers | `resolve_enforcement` kind arm — ONLY caller is kind_classifier.py:283 (verified); relation/chain arms: ZERO callers (verified) | Read | kind arm: namespaced → declaring pack exclusively; bare → cross-pack max-severity; `reg is None` → `warn` | Unit |
| Write path | `create_operator` relation check (sdk.py:3898-3911): `label not in declared` where `declared = {r.get("predicate") for r in reg.list_relations()}` | Read (validation) | Bare-vs-bare by design — relations declared as bare predicates; **document only, no behavior change** (pin d) | Existing TestCreateOperatorWarnNotBlock (unchanged) |

**Bug Pattern Flags:** none new — no external services, no DB, no auth, no events, no concurrency (registry memoization is reset per test by conftest `_packs_env_isolation`, conftest.py:856-882). The one latent hazard (cross-ns local-name collisions — of the 3 live collisions in the compiled kind index, only `issue` (dev:issue vs pm:issue) is a genuine cross-PACK enforcement collision; `workflow` and `deployment` are core-vocabulary index collisions (core has NO pack, so core:* kinds never participate in the enforcement scan), plus a manifest-level `requirement` equivalence (dev kindDefs `≡ product-strategy:requirement`), which is NOT a live index collision since dev:requirement is a point kind excluded from the index) is exactly what pin (b) eliminates: a stripped namespaced kind must never reach the bare cross-pack scan. Pin (b) is a FUTURE-PROOFING guarantee as much as a present hazard: today no collided bare kind declares enforcement (only agent-ops `rule` and product-strategy point kinds declare retry; none are in the collision set) — the danger is the moment any pack declares `retry` on a collided bare name (e.g. `issue`), a caller-side-strip implementation would silently mis-attribute it across packs.

### Failure Modes

| Failure scenario | Expected behavior | Test |
|---|---|---|
| Unknown namespace (`core:occurrence` — core has no pack; `no-such-ns:x`) | `warn`, NEVER `KeyError` | `test_namespaced_unknown_namespace_degrades_to_warn` |
| Malformed kinds (`agent-ops:` empty local; `a:b:c` multi-colon) | rpartition yields a resolvable/absent pack → `warn` | same test (add `agent-ops:` case) |
| Registry unavailable (`_get_registry()` → None) | `warn` (unchanged fail-open) | `test_registry_none_fails_open_to_warn` (NEW — monkeypatch `_get_registry` → None; the existing `test_warn_default` runs with a REAL registry, so it does not cover this path) |
| Hook `resolve_enforcement` raises | Fail-open — batch continues (existing `except Exception` in the hook) | `test_hook_resolve_raise_is_fail_open` (NEW — monkeypatch `resolve_enforcement` to raise inside the hook; the existing `test_rerank_failure_fail_open` exercises the `_near_miss_rerank` except path, NOT the hook's own try/except) |
| Bare kind behavior drift | `rule` → `retry`, `no-such-kind-xyz` → `warn` — byte-identical to today | existing `TestResolveEnforcement` tests |

**Tech Stack:** Python 3.12+, pytest (docker lane), uv.

---

## Approach Decision

### Picked: **A) Resolver-side namespace dispatch** (the issue's stated target)

`resolve_enforcement` is the ONE shared seam (#1934). Fixing the seam fixes the classifier hook and every future namespaced caller by construction.

**Behavioral spec of the change (kind arm only):**

```
if kind is not None:
    if ":" in kind:
        # namespaced — declaring pack EXCLUSIVELY (pin b); mirrors the
        # KindIndex.near_misses same-namespace-first precedent
        ns, _, local = kind.rpartition(":")
        pack = reg.get_pack(ns)
        if pack is None:          # unknown ns (core has no pack) → warn, never raise (pin a)
            return "warn"
        lv = pack.enforcement_for(local)
        return lv if lv in VALID_LEVELS else "warn"   # defensive guard, loop-parity
    # bare — cross-pack max-severity scan, UNCHANGED (the only path that
    # scans across packs; a stripped namespaced kind must never land here —
    # 4 verified cross-ns local-name collisions)
    for pack in reg.packs.values():
        lv = pack.enforcement_for(kind)
        if lv in VALID_LEVELS and _SEV[lv] > best_sev:
            best, best_sev = lv, _SEV[lv]
            if best == "block":
                break
    return best
```

Notes: (1) the current kind/relation/chain branch sits INSIDE the per-pack loop; the restructure hoists the branch out of the loop — same calls, same order, same semantics for relation/chain arms; (2) `best`/`best_sev` init remains for the bare path; (3) module docstring gains a namespaced-resolution line; (4) behavior flip surface is exactly one caller (verified: kind_classifier.py:283 is the only kind-arm caller) — the flip IS the fix.

**Why A over the alternatives** — outcome quality, edge-case coverage, extensibility:

1. **Correct at the seam.** The bug lives in `resolve_enforcement`, not the caller. Fixing the seam means every consumer (current classifier hook + future namespaced callers) gets correct resolution with no per-caller discipline to maintain.
2. **Pin (b) enforced by construction — for namespaced inputs.** The namespaced branch returns the declaring pack's level before the cross-pack scan is ever reachable — the cross-ns collisions (`issue` dev/pm is the genuine cross-pack one; `workflow`/`deployment` are core-vocabulary index collisions with no pack) can never be mis-attributed because a stripped name never feeds the scan. This is the FUTURE-PROOFING guarantee the caller-side-strip approach (B) lacks: the moment any pack declares `retry` on a collided bare name, B silently mis-attributes across packs while A cannot. (A genuinely BARE collided kind still max-scans across packs under both A and B — that is the pre-existing contract, explicitly kept; the only bare index kind today is `statement`.)
3. **Pin (c) is structural and STRICTER than the precedent it cites.** `get_pack(ns)` → `None` → `warn`; there is no indexing path that can raise. Malformed kinds (`agent-ops:`, `a:b:c`) degrade identically via rpartition. The resolution is namespace-SCOPED like `KindIndex.near_misses` (kind_index.py:277-301) but deliberately stricter: declaring-pack-EXCLUSIVE, no any-namespace fallback (an any-namespace fallback would leak namespaced kinds into the bare cross-pack scan — exactly the pin-b hazard).
4. **Bare path untouched.** Zero regression surface for the existing bare contract (`rule` → `retry`, max-severity across packs) — the existing TestResolveEnforcement suite is the regression guard.
5. **Minimal, durable surface.** One module restructured + hygiene + tests. No new public API to document/test/keep-in-sync (vs C), no caller-side divergence from the issue target (vs B).

**Note on the guarded manifest-layer variant (solution-verify P4):** the diverge rejection engaged only the naive strip variant (`enforcement_for('pm:issue')` on dev must never strip to 'issue'). The namespace-*guarded* variant (strip only when `ns == pack.namespace`) is outcome-equivalent to A, but A still wins: `enforcement_for`'s only caller is the seam itself, so putting namespace grammar in the seam (A) keeps the pack-local key space bare by contract AND fixes every future seam caller — the guarded variant would just relocate the same dispatch logic into each manifest method for no additional correctness.

### Rejected alternatives

**B) Caller-side strip** (hook strips `ns:` before calling): minimal diff, but (i) violates pin (b) — after stripping, the bare max-severity scan runs globally, so the cross-ns collisions become latent interference on exactly the workflow/issue/deployment kinds the pin names; (ii) leaves the seam broken for ANY future namespaced caller — the fix would be a landmine for the next consumer; (iii) diverges from the issue's explicit target (`resolve_enforcement` handles namespaced kinds). *When it WOULD have been better:* if the seam were frozen/binary-committed and the caller were the only consumer forever — i.e., a pure "make one dead path fire" hack with no extensibility requirement. Not the case here: the seam's whole purpose is shared consumption.

**C) Registry-side dispatch** (new `PackRegistry.resolve_kind_pack(kind)` primitive; `resolve_enforcement` delegates): same semantics as A, but adds a new public API that must be documented, tested, and kept in sync for a single consumer — and it would be a thin wrapper over `rpartition` + the existing `get_pack` (pack_registry.py:1128). Speculative abstraction (YAGNI); the registry already exposes exactly the primitive needed. *When it WOULD have been better:* if a second consumer needed ns→pack resolution independently of enforcement (e.g., a validation path, kind provenance queries) — then a registry primitive would be the right shared shape and A would be the duplication.

**Rejected in diverge (kept as-is):** manifest-layer fix (breaking pack-local abstraction — `enforcement_for('pm:issue')` on the dev pack must never strip to `'issue'` and mis-match; the pack-local key space is bare by contract) and index-metadata enforcement map (a second source of truth for the ladder, contradicting #1934's "ONE shared seam" principle).

---

## Tasks

### Task 1: Red tests — namespaced resolution contract (unit + integration)

**Intent:** Pin the confirmed problem as failing tests BEFORE any source change: namespaced kinds must resolve against the declaring pack; the classifier near-miss hook must record the retry signal for a rerank-chosen `agent-ops:rule`.

**Acceptance:** `tests/test_enforcement.py` and `tests/test_kind_classifier.py` contain the new cases; the discriminating assertions FAIL on current code (agent-ops:rule resolves `warn` today → both red); existing tests in both files still pass.

**Files:**
- Modify: `tests/test_enforcement.py`
- Modify: `tests/test_kind_classifier.py`
- Test: `tests/test_enforcement.py`, `tests/test_kind_classifier.py`

**Step 1: Add namespaced unit tests to `TestResolveEnforcement` (tests/test_enforcement.py:45-66)**

- `test_namespaced_agent_ops_rule_resolves_retry` — `resolve_enforcement(kind="agent-ops:rule") == "retry"` (indicator 1; RED on current code — resolves `warn`).
- `test_namespaced_unknown_namespace_degrades_to_warn` — `resolve_enforcement(kind="core:occurrence") == "warn"` (core has no pack) AND `resolve_enforcement(kind="no-such-ns:x") == "warn"` AND `resolve_enforcement(kind="agent-ops:") == "warn"` (malformed empty local — rpartition; pin a, never KeyError) AND `resolve_enforcement(kind="a:b:c") == "warn"` (multi-colon — rpartition takes the LAST colon → ns `a:b`, absent pack → warn; matches the Failure-Modes contract).
- `test_namespaced_declaring_pack_only` — `resolve_enforcement(kind="dev:issue") == "warn"` (dev declares no retry; content-level regression guard for pin b — a namespaced kind must NOT pick up a cross-pack declaration even though bare `issue` is collided across dev/pm).

  > **Note (solution-verify P2):** the real-registry `dev:issue` case alone does NOT discriminate against a buggy caller-side-strip implementation (both dev and pm resolve bare `issue` → warn today). The STRUCTURAL pin of declaring-pack-exclusive semantics is the synthetic-registry test in the next step.

- `test_namespaced_declaring_pack_exclusive_synthetic` (NEW, structural pin-b guard, **RED on current code** — asserts the FIXED behavior) — monkeypatch `tortoise.domain_loader._get_registry` to return a REGISTRY-LIKE stub with BOTH `.packs` (dict) AND `.get_pack(ns)` (the fixed code calls `reg.get_pack(ns)` BEFORE the bare loop; a plain dict would AttributeError on the green run). Exact shape: `stub = types.SimpleNamespace(packs={"a": pa, "b": pb, "c": pc}, get_pack=lambda ns: {"a": pa, "b": pb, "c": pc}.get(ns))` where `pa`/`pb`/`pc` are `PackManifest(namespace=..., name=..., version="0.1.0", tier="free", description=..., path=tmp_path, extraction={"enforcement": {"default": "warn", "kinds": {"widget": ...}, "relations": {}, "chains": {}}})` — NOTE the `extraction` dict MUST include the `"enforcement"` sub-key (enforcement_for does `self.extraction["enforcement"].get("kinds", {})`; an `extraction={"kinds": ...}` shape would KeyError). Assert: `resolve_enforcement(kind="a:widget") == "retry"` (pa kinds `{"widget": "retry"}` — declaring pack wins, NOT cross-pack max-severity); mirror `resolve_enforcement(kind="b:widget") == "warn"` (pb kinds `{"widget": "warn"}` — declaring pack's own default wins even though pack `a` has `retry`; the bare max-scan must NOT apply to namespaced kinds); guard `resolve_enforcement(kind="c:widget") == "warn"` (pc kinds `{"widget": "nuke"}` — invalid level → defensive VALID_LEVELS guard fires). This pins pin (b) + the guard independent of current manifest content. On CURRENT code `a:widget` runs the bare path → all packs miss the full-namespaced key → `warn` → RED (this is a discriminating RED, one of the strongest). The conftest `_packs_env_isolation` resets module state, never the `_get_registry` function — monkeypatch is compatible.
- `test_registry_none_fails_open_to_warn` (NEW, contract-pinning — green on current code) — monkeypatch `tortoise.domain_loader._get_registry` with `lambda: None` (a FUNCTION returning None — the from-import binds the function, so patching it to `None` directly would TypeError at call time; production's unavailable path returns None FROM the function), assert `resolve_enforcement(kind="anything") == "warn"` (fail-open contract, previously untested — the existing `test_warn_default` runs with a real registry).
- `test_only_agent_ops_rule_is_index_reachable_retry` (NEW, dormant-claim drift pin, **RED on current code** — asserts the fixed behavior) — compile the REAL spec (`compile_kind_index_spec()`), assert `{k for k in spec if resolve_enforcement(kind=k) == "retry"} == {"agent-ops:rule"}`. Pins boundary note 5 (product-strategy useCase/userJourney stay dormant because FIX P excludes point kinds from the index) against future manifest drift; fails loudly if a new index-reachable retry declaration appears.

**Step 2: Update `TestClassifierRetrySignal` (tests/test_enforcement.py:100-110)**

`test_seam_resolves_retry_for_classifier_path` currently asserts the bare form (`kind="rule"`) and CLAIMS classifier coverage it does not exercise (it only calls the seam). Change the assertion to the classifier-path form `kind="agent-ops:rule"` and rename to `test_seam_resolves_retry_for_namespaced_classifier_path`. Keep `test_retry_count_key_is_bounded_by_m3_loop` unchanged. (This renamed test is RED on current code — it asserts the namespaced resolution. It overlaps the Step-1 contract test; the distinction is intent: Step 1 pins the seam contract, this one pins the CLASSIFIER-PATH input form.)

**Step 3: Add the classifier integration test (tests/test_kind_classifier.py)**

- Extend the module-level encoder keyword tuple `_KEYWORDS = ("ticket", "code", "plan", "workflow", "occurrence", "claim")` (line 99) with `"rule"`, `"standard"`. Safety argument (verify by running the full file): existing kinds/inputs light no new dims, zero-dim cosine contributions are nil, and no existing test asserts exact stats-dict equality — per-key assertions only (verified). The shared module-scoped `classifier` fixture's index is built from FIXTURE_SPEC with `KeywordEncoder`, so vector dims grow 6→8 with no assertion impact.
- New class `TestNearMissRetrySignal`, following the `test_mutual_near_miss_tie_goes_to_llm_tail` precedent of building a local spec variant + local classifier:

```python
class TestNearMissRetrySignal:
    def test_rerank_chosen_agent_ops_rule_records_near_miss_retry(self):
        spec = dict(FIXTURE_SPEC)
        spec["agent-ops:rule"] = {
            "text": "agent-ops:rule: An operational rule",
            "section": "objects", "description": "An operational rule",
            "synonyms": [], "examples": [], "nearMisses": ["agent-ops:standard"],
        }
        spec["agent-ops:standard"] = {
            "text": "agent-ops:standard: A reusable standard",
            "section": "objects", "description": "A reusable standard",
            "synonyms": [], "examples": [], "nearMisses": [],
        }
        clf = KindClassifier(
            encoder=KeywordEncoder(),
            index=KindIndex.build(spec, encoder=KeywordEncoder(), persist=False),
            model=None, llm_tail=False,
        )
        out = clf.classify_items(_items(("entity", "the rule standard")))
        a = out["assignments"]["i0"]
        assert a["kind"] == "agent-ops:rule"      # rerank-chosen (indicator 3)
        assert a["mode"] == "rerank"
        assert out["stats"]["assigned_rerank"] == 1
        assert out["stats"].get("near_miss_retries") == 1   # seam fired
```

  Why this shape: "the rule standard" lights exactly the two new keyword dims → a tie between `agent-ops:rule` and `agent-ops:standard` (cos 1/√2 each, matching the existing one-sided test's tie geometry); one-sided nearMiss (rule declares standard) → rerank prefers the non-decoy `agent-ops:rule`; the hook then calls `resolve_enforcement("agent-ops:rule")` → must be `retry` → `near_miss_retries == 1`. Use the BARE nearMiss ref `["standard"]` (production-faithful — the real manifest declares `nearMisses: [standard]`; `KindIndex.near_misses` resolves it same-namespace-first to `agent-ops:standard` when present in the spec, exercising the resolution chain the E2E-5 fixture uses). Spec texts deliberately avoid cross-keyword substrings ("rule" not in standard's text, "standard" not in rule's text) so the tie is clean. RED on current code: the hook sees `warn` → the stat is absent → `.get()` is None → assertion fails.

**Step 4: Classifier integration test — hook fail-open (NEW, solution-verify P3)**

`test_hook_resolve_raise_is_fail_open` (in `TestNearMissRetrySignal`): monkeypatch the module the hook ACTUALLY imports from — `monkeypatch.setattr("tortoise.enforcement.resolve_enforcement", raiser)` (the hook does `from tortoise.enforcement import resolve_enforcement` INSIDE the try block at kind_classifier.py:283, so `tortoise.kind_classifier` has NO `resolve_enforcement` attribute; patching the source module works because the from-import re-executes per classify_items call). Run the same "the rule standard" batch; assert the batch does NOT abort (the item is still classified via rerank, no crash) and `assert out["stats"].get("near_miss_retries", 0) == 0` (robust to BOTH pre-init absence and post-init zero — Task 3 makes the key always present; do NOT assert the key is absent anywhere). The hook's own `except Exception: pass` at :287-290 is the fail-open contract (previously untested; the existing `test_rerank_failure_fail_open` exercises the `_near_miss_rerank` except, a different path).

**Step 5: Run targeted tests to verify RED**

```
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_enforcement.py tests/test_kind_classifier.py -v
```

Expected — **FIVE tests fail (all RED on current code, exactly the bug)**: (1) `TestResolveEnforcement::test_namespaced_agent_ops_rule_resolves_retry` (got "warn"); (2) `TestClassifierRetrySignal::test_seam_resolves_retry_for_namespaced_classifier_path` (got "warn"); (3) `TestNearMissRetrySignal::test_rerank_chosen_agent_ops_rule_records_near_miss_retry` (near_miss_retries absent); (4) `TestResolveEnforcement::test_namespaced_declaring_pack_exclusive_synthetic` (a:widget resolves "warn" — asserts the fixed behavior); (5) `TestResolveEnforcement::test_only_agent_ops_rule_is_index_reachable_retry` (empty set today). The contract-pinning tests — `test_namespaced_unknown_namespace_degrades_to_warn`, `test_namespaced_declaring_pack_only`, `test_registry_none_fails_open_to_warn` — are green on current code BY DESIGN (they pin existing behavior); do NOT "fix" them. Do NOT modify or weaken any of the five RED tests (the synthetic one is a deliberately discriminating red).

### Task 2: Green — resolver-side namespace dispatch

**Intent:** Implement the namespaced kind arm in the seam; bare path untouched.

**Acceptance:** All Task-1 red tests pass; every pre-existing test in `tests/test_enforcement.py`, `tests/test_kind_classifier.py` passes unchanged.

**Files:**
- Modify: `tortoise/enforcement.py:38-75`

**Step 1: Restructure `resolve_enforcement`'s kind arm per the behavioral spec above**

Hoist the kind/relation/chain branch out of the per-pack loop; namespaced kinds resolve against `reg.get_pack(ns)` exclusively (early return; `warn` on `None`); bare kinds keep the unchanged max-severity scan. Add the defensive `lv in VALID_LEVELS` guard on the namespaced return (loop parity). Update the module docstring's resolution-order note with one line: namespaced `ns:local` kinds resolve against the declaring pack exclusively; unknown namespaces degrade to `warn`.

**Step 2: Run targeted tests**

```
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/test_enforcement.py tests/test_kind_classifier.py -v
```

Expected: all green (Task-1 reds now pass; bare `rule` → `retry` unchanged).

### Task 3: Classifier hygiene (no behavioral change)

**Intent:** Make the retry stat's shape explicit and complete a truncated comment. The `near_miss_retries: 0` stats-init is OPTIONAL hygiene — it makes the key always present for future consumers (pin e: no consumer today). BEFORE adding it, verify NO production consumer of the classifier stats dict iterates/compares the full dict (extractor_v2 session rollup + A/B cost gate read SPECIFIC keys: `adjudication_calls`, `llm`, `classify_errors`, `embedding_errors` — grep `stats["` in extractor_v2/api to confirm; per-key `.get()` reads are safe). If any consumer does exact-dict/keys-iteration, drop the init and keep only the comment fix.

**Acceptance:** `near_miss_retries: 0` present in the stats init **iff** the Intent's grep verification confirms no exact-dict/keys-iteration consumer (expected to pass — consumers read specific keys); comment completed; full `tests/test_kind_classifier.py` green with zero behavioral diffs.

**Files:**
- Modify: `tortoise/kind_classifier.py` (stats dict init, ~line 189-200; comment at line 289)

**Step 1:** Subject to the Intent's grep verification passing (no exact-dict/keys-iteration consumer of the classifier stats dict — check BOTH `stats[` reads AND the raw-dict report embedding at extractor_v2.py:3777-78 where `classify_later` carries `s2`/`union` stats by reference; that embedding is a pass-through, so the always-present key appears in the flag-gated payload as an ADDITIVE key — consistent with the documented "additive keys only" convention at :3769, but the Task-3 change note must name it), add `"near_miss_retries": 0` to the stats dict init alongside the other counters (the hook's `.get()` fallback at line 287 becomes a no-op guard; no behavior change). If the grep finds a full-dict consumer, skip the init and keep only the comment fix.

**Step 2:** Complete the truncated exception comment at line 289 (`# the classification batch` → a full sentence, e.g., `# the classification batch must not abort — fail-open, mirroring the rerank path`).

**Step 3:** Run `tests/test_kind_classifier.py` — all green.

### Task 4: Document-only — create_operator relation check (pin d)

**Intent:** Verify-and-document that the sdk.py relation check is bare-vs-bare BY DESIGN (relations are declared as bare predicates in manifests; the write path passes bare labels) — not a bug, no behavior change.

**Acceptance:** A clarifying comment block at the relation check; zero logic changes; existing `TestCreateOperatorWarnNotBlock` green.

**Files:**
- Modify: `tortoise/sdk.py:3898-3911`

**Step 1:** Add a comment block above `declared = {r.get("predicate") for r in reg.list_relations()}` noting: predicates are declared and matched BARE by contract (manifest `relations[].predicate`); namespaced relation labels are out of scope for this check (verified bare-vs-bare, not a bug — #2030 pin d); enforcement namespacing applies to the kind arm only. Also note (epic §6 scope): the epic's §6 "undeclared relation/**kind-pair** → warn-not-block" contract has NO kind-pair leg implemented on the write path — this check covers only the relation leg; the kind-pair leg is an epic-level gap, out of #2030 scope.

**Step 2:** Run `tests/test_enforcement.py` (contains TestCreateOperatorWarnNotBlock) — green.

### Task 5: Full verification + regression sweep

**Intent:** Prove the fix in the full docker-lane suite — no collateral beyond the intended flip.

**Acceptance:** Full `tests/` green in the docker lane; targeted regression files green.

**Files:** none (verification only)

**Step 1:** Targeted regression (classifier consumers + index): `tests/test_enforcement.py tests/test_kind_classifier.py tests/test_kind_index.py tests/test_extractor_v2.py` — green.

**Step 2:** Full suite:

```
export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'
uv run pytest tests/ -v
```

Expected: green (17 embedded-only files excluded by the docker lane by design; embedded carve-out lane untouched by this change — the modified modules are all docker-lane).

**Step 3:** Confirm no test asserts exact classifier-stats equality that a new always-present `near_miss_retries` key could break (verified during exploration: classifier stats are asserted per-key only; `test_chain_enforcer.py:291` / `test_extractor_v2.py:2805` are different stat dicts).

### Task 6: Commit via commit-workflow

**Intent:** Land through the mandatory review gate.

**Acceptance:** PR merged with the code-review gate passed.

**Files:** as committed per Tasks 1-4

**Step 1:** Invoke the `commit-workflow` skill (mandatory before any commit/push/merge — AGENTS.md hard rule). Branch `feat/2030-namespaced-enforcement` already exists; PR body must flag the boundary notes below (NOT the product-strategy activation — it cannot occur; flag the silent warn→retry flip surface = exactly one caller, the census-only telemetry nature, and the dormant product-strategy declarations).

---

## Testing Strategy

- **Unit (tests/test_enforcement.py):** the seam contract — namespaced resolution (agent-ops:rule → retry), unknown/malformed ns degrade to warn (never KeyError), declaring-pack-exclusive (dev:issue → warn), bare path unchanged (rule → retry), warn defaults. Fixture/env: real registry loaded from repo `packs/` (conftest `_packs_env_isolation` resets `_registry`/`_PACKS_DIR`/`TORTOISE_PACKS_DIR` per test — deterministic regardless of ordering); agent-ops pack ships `extraction.enforcement.kinds.rule: retry` (packs/agent-ops/manifest.yaml:47).
- **Integration (tests/test_kind_classifier.py):** full near-miss hook via `KindClassifier.classify_items` — stub `KeywordEncoder` (deterministic one-hot), controlled `FIXTURE_SPEC` variant adding `agent-ops:rule` + `agent-ops:standard` (section objects), one-sided nearMiss pair, input "the rule standard", rerank-chosen `agent-ops:rule`, assert `stats["near_miss_retries"] == 1` via `.get()`. Env: docker lane URI + module fixture (real registry for the hook's `resolve_enforcement`).
- **Regression:** full `tests/` docker lane; targeted `test_kind_index.py` (near_misses untouched), `test_extractor_v2.py` (flag-gated classifier consumer), `test_enforcement.py`.
- **No pgTAP / no browser E2E:** no SQL, no UI. Issue's E2E-5 (05-plan §7) is satisfied at the code layer by the integration test; the flag-gated extractor path is covered by existing extractor tests.

## Verification Plan (test-routing)

Domain: **code** (pure Python, in-repo, zero third-party deps). Complexity: standard.
- Unit depth: full (seam contract matrix above).
- Integration depth: full (hook end-to-end with stub encoder).
- Regression: full docker-lane suite.
- Skipped: content/config/research/UX verification domains (no UI, no config-file changes, no research output); pgTAP (no SQL); browser E2E (no user journey change).
- Proof-of-work gate: `verification-before-completion` before declaring done (full suite green + targeted red/green history).

## Acceptance Criteria (mapped to issue indicators)

| Issue indicator | Evidence |
|---|---|
| 1. `resolve_enforcement(kind="agent-ops:rule") == "retry"` | `test_namespaced_agent_ops_rule_resolves_retry` green (Task 2) |
| 2. `resolve_enforcement(kind="rule") == "retry"` (bare unchanged) | existing `test_retry_for_agent_ops_rule` green, unmodified (Task 2 regression) |
| 3. Classifier near-miss involving `agent-ops:rule` records `near_miss_retries` | `TestNearMissRetrySignal::test_rerank_chosen_agent_ops_rule_records_near_miss_retry` green (Task 2) — rerank-chosen `agent-ops:rule`, `stats["near_miss_retries"] == 1`. **Caveat (devil's-advocate scenario 4):** production triggering depends on real-embedding tie geometry (rule↔standard within LAMBDA under the one-sided nearMiss) — proven under engineered stub-encoder geometry only; observable in production via the `classify_later` session-stats payload. |

Plus pinned guarantees: (a) `core:occurrence`/`no-such-ns:x`/`agent-ops:` → `warn`, never KeyError; (b) `dev:issue` → `warn` (declaring-pack-exclusive, bare scan never fed by stripped names); (d) sdk.py documented-only; (e) stats init without a consumer (hygiene).

## Runtime Prerequisites

- Docker FalkorDB up (docker lane): `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d` per AGENTS.md, then `export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
- `uv sync` (min uv 0.6.0; Python 3.12+; `.python-version`).
- Registry loads from repo `packs/` in tests (conftest clears `TORTOISE_PACKS_DIR`; `_PACKS_DIR` injection knob untouched) — no env needed beyond the URI.
- No new dependencies; no migrations; no config changes.

## Boundary Notes

1. **TORTOISE_CLASSIFY_LATER flag-gating + catalog-staleness dependency:** the production classifier path (and thus the near-miss hook) runs only under the flag (extractor_v2.py:321); the legacy pipeline never exercises the hook. This fix changes the hook's INPUT resolution, not flag behavior — no flag change. Tests exercise the hook directly via `KindClassifier.classify_items`, independent of the flag. Deployment dependency (devil's-advocate scenario 1): the "ONLY live activation" claim assumes the deployed catalog ships agent-ops with `rule: retry`; a stale/self-hosted catalog (TORTOISE_PACKS_DIR) silently lacks it — index and registry agree (both stale), indistinguishable from correctly-dormant. Mitigation is the packaged-catalog smoke surface (the real-registry tests resolve against repo `packs/`); a deployed-wheel smoke is tracked as a future hardening, out of #2030 scope.
2. **relation/chain arms callerless:** verified ZERO callers pass `relation=`/`chain_id=` today; the relation check on the write path (sdk.py) is a separate bare-predicate mechanism. Namespacing applies to the kind arm only; relation predicates are bare by manifest declaration — out of scope (Task 4 documents it).
3. **Kind equivalences not followed:** `resolve_enforcement` resolves the literal kind string; manifest `≡` equivalences / `subclassOf` / synonyms are NOT followed (a `retry` declared on a parent or equivalent kind does not transfer). Pre-existing behavior, unchanged by this fix.
4. **Retry signal is census-only — the M3 loop is NOT a near-miss consumer (CORRECTED — epic-alignment P2):** the `retry` signal is a census marker today — NO re-attempt consumer exists (`near_miss_retries` has zero production readers). The extractor's M3 loop (`_complete`, extractor_v2.py:4160+, `_COMPLETE_RETRIES=2`) bounds TRANSIENT COMPLETION retries (429/5xx/network/deadline — re-attempting the same LLM completion), NOT near-miss re-attempts. A classifier near-miss triggers no re-attempt; wiring an actual re-attempt consumer is the epic's remaining "deferred extractor-retry semantics layer" completion — OUT OF #2030 SCOPE (the issue's indicator 3 is satisfied by the census increment). `test_retry_count_key_is_bounded_by_m3_loop` (tests/test_enforcement.py) pins the same M3 attribution and is intentionally left as-is.
5. **product-strategy retry declarations stay DORMANT (CORRECTED — solution-verify P2):** product-strategy ships `retry` declarations (`useCase`/`userJourney`, kindDefs `:79`/`:90` and extraction `:179-180`) but they sit on POINT kinds, which FIX P excludes from the kind index — compiled spec verified: `product-strategy:useCase`/`product-strategy:userJourney` are NOT index kinds and no product-strategy index kind carries a retry declaration. The fix's ONLY live activation is `agent-ops:rule`. Do NOT flag product-strategy activation in the PR (it cannot occur); note instead that product-strategy's retry declarations remain dormant because their kinds are non-index point kinds.
6. **Silent behavior flip (warn → retry):** for namespaced kinds, only — the flip surface is exactly one caller (kind_classifier.py:283). Verified no other kind-arm callers exist; no caller passes a namespaced kind expecting `warn`.
7. **Plan-phase no-code boundary:** per the solution-converge phase instruction, implementation code is authored at execution (executing-plans); this plan fixes the behavioral spec and test contracts. The `### Pattern Research` gate is skipped (zero third-party deps) — findings date stamped above.

---

## Next Gates

1. **plan-review** on this document (mandatory before execution handoff).
2. **Execution handoff:** 6 tasks ≤ 8 → subagent-driven execution in this session; `executing-plans` skill.
3. **commit-workflow** at Task 6 (mandatory review gate).
