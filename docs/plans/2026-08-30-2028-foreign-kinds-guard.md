---
title: "Plan — #2028 fix inert _check_foreign_kinds guard (pre-v1.1 foreign-kind detection)"
type: engineering
domain: capability
doc_status: live
created: 2026-08-30
subjects.team: epistemic-team
ownedBy: epistemic-team
---

<!-- research-path: issue #2028 (post-epic bug review, 2026-08-30); epic #1891 slice #1936 regression; scoping comment 2026-08-30 (v3.1 final) -->

# Issue #2028 — Fix the inert `_check_foreign_kinds` guard

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the pre-v1.1 foreign-kind guard actually fire — `_check_foreign_kinds` must scan `node["props"]` (the real dump shape) instead of dead top-level keys, and must run pre-restore so a foreign-kind artifact 422s with the live graph untouched and the ledger unstamped.

**Team:** epistemic-team
**Role:** product-implementer

**Architecture:** Two-part fix in `tortoise/hosted_api.py`. (1) `_check_foreign_kinds` reads `node.get("props") or {}` for the full kind-key set (`_KIND_PROP_KEYS` = pointKind/objectKind/eventKind/documentKind/subjectKind/sourceKind + compat `kind`/`actionKind`), known-set = DEFAULT_STARTER_PACKS ∪ shared-catalog pack_summaries ∪ dump-native PackManifest namespaces ∪ the artifact's OWN declared `pack_config` namespaces (self-contained vocab + manifest-upsert-established vocab), `core` excluded. (2) Hoist the guard into the existing restore try-block — run UNCONDITIONALLY before `_restore_into_temp_verify_swap`, wrapped in `asyncio.to_thread` — so the existing `except (ValueError, KeyError) → _quarantine_import → 422` handler fires with zero side effects; every legitimate artifact passes (its namespaces are absorbed), only genuinely undeclared foreign vocabulary 422s (fail-loudly, never silent partial). Distinct quarantine reason per artifact class: pre-v1.1 (no pack_config), v1.1 empty/malformed packs, v1.1 partial coverage. Retries converge (no `last_import_sha256` stamp on rejection). `_apply_import_pack_config` keeps its pc-None call as idempotent defense-in-depth. No changes to `dump_graph`, `DUMP_FORMAT`, `restore_graph`, `pack_state`, or the hub.

### Pattern Research

> **Findings date:** 2026-08-30

**Library docs (preflight)** — no third-party deps in plan (pure internal Python in `tortoise/hosted_api.py` + tests).

> Gate skipped: plan touches zero third-party deps. In-repo precedent for the kind vocabulary (sdk.py:9661 kind_field), dump shape (hosted_backup.py dump_graph), known-set (pack_registry.pack_summaries, pack_state.DEFAULT_STARTER_PACKS), and the fail-closed import chain (existing ValueError → quarantine → 422 handlers). No external knowledge needed.

### Integration Surface Map

| Surface | Boundary | Test Layer | Failure mode covered |
|---|---|---|---|
| `_check_foreign_kinds` (guard read path + known-set) | payload dump nodes `{dump_id, labels, props}` | unit (test_export_pack_config.py) | foreign ns raises; bare/starter/core pass; non-string value passes; missing props; non-dict node/props; ≤5 listing |
| Dump-native PackManifest absorption | labels/props of dump nodes | unit | self-contained manifest passes; mismatched namespace still raises |
| Real `dump_graph` output | sdk graph → dump | unit (integration w/ embedded FalkorDB) | real-shape foreign kind raises; clean graph passes |
| Import endpoint 422 + quarantine | hosted import API | endpoint (test_import_endpoint.py) | pre-v1.1 foreign kind → 422 + quarantine audit + live graph untouched |
| Post-v1.1 gate (`pack_config` present) | import API | endpoint | post-v1.1 custom-pack artifact still imports 200 (guard gated off; manifest upsert) |
| Registry known-set | shared catalog (packs dir) | n/a — unchanged logic | determinism verified: tenant-ops ∉ catalog; starter namespaces present |

### Verification Plan

- **Domain:** code (hosted import path). **Tier:** standard.
- **Unit + integration:** `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_pack_config.py tests/test_import_endpoint.py -q`
- **Not applicable:** UX (no UI), content, config, research domains.
- **CI note:** python-ci-gate has known unrelated failures (tracked #1970) — document in the PR body if hit; admin-exception merge path.

---

### Task 1: Fix the guard read path + known-set (TDD)

**Intent:** Make `_check_foreign_kinds` read kinds from `node["props"]` (the real dump shape) so it can actually detect foreign pack vocabulary; keep the known-set correct (starter ∪ catalog ∪ dump-native PackManifest) so there are no false positives.

**Acceptance:** `_check_foreign_kinds` raises ValueError ("predates pack-config") for props-nested foreign kinds; passes for bare/starter/core kinds, non-string values, missing/non-dict props, and self-contained PackManifest dumps; lists ≤5 foreign kinds.

**Files:**
- Modify: `tortoise/hosted_api.py` (`_check_foreign_kinds` ~7872, add module-level `_KIND_PROP_KEYS`)
- Test: `tests/test_export_pack_config.py` (TestForeignKindsGuard)

**Step 1: Write the failing tests (rewrite existing 2 + add new cases)**

Rewrite `TestForeignKindsGuard` to the REAL dump shape and add the matrix from scope v3.1:

```python
class TestForeignKindsGuard:
    def test_pre_v1_1_with_foreign_kinds_raises(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["Point"],
                              "props": {"objectKind": "tenant-ops:contract"}}]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)

    def test_pre_v1_1_clean_payload_passes(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            {"dump_id": 1, "labels": ["Point"], "props": {"pointKind": "statement"}},
            {"dump_id": 2, "labels": ["Event"], "props": {"eventKind": "dev:review"}},
            {"dump_id": 3, "labels": ["Point"], "props": {"kind": "core:meeting"}},
            {"dump_id": 4, "labels": ["Point"]},  # absent props — robustness
        ]}
        _check_foreign_kinds(payload)  # dev is a starter, core excluded, bare passes

    @pytest.mark.parametrize("key", ["pointKind", "objectKind", "eventKind",
                                     "documentKind", "subjectKind", "sourceKind",
                                     "actionKind", "kind"])
    def test_extended_keys_foreign_raises(self, key):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["X"],
                              "props": {key: "tenant-ops:thing"}}]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)

    @pytest.mark.parametrize("key,value", [
        ("pointKind", "statement"), ("objectKind", "dev:epic"),
        ("eventKind", "pm:cardCreated"), ("kind", "core:meeting")])
    def test_extended_keys_clean_passes(self, key, value):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["X"],
                              "props": {key: value}}]}
        _check_foreign_kinds(payload)

    def test_non_string_kind_value_passes(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["X"],
                              "props": {"pointKind": 42}}]}
        _check_foreign_kinds(payload)  # isinstance guard — no crash

    def test_non_dict_props_and_nodes_skipped(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            "junk",
            {"dump_id": 2, "labels": ["X"], "props": []},
            # PackManifest-labeled node with non-dict props must not
            # AttributeError in the absorption loop either
            {"dump_id": 3, "labels": ["PackManifest"], "props": []},
        ]}
        _check_foreign_kinds(payload)  # no AttributeError → 500

    def test_many_foreign_kinds_lists_only_five(self):
        from tortoise.hosted_api import _check_foreign_kinds
        foreign = [f"ns{i}:kind{j}" for i in range(6) for j in range(2)]
        payload = {"nodes": [{"dump_id": i, "labels": ["X"], "props": {"objectKind": k}}
                             for i, k in enumerate(foreign)]}
        with pytest.raises(ValueError) as ei:
            _check_foreign_kinds(payload)
        # message lists exactly 5 (sorted(foreign)[:5])
        assert "ns0:kind0" in str(ei.value) and "ns5:kind1" not in str(ei.value)

    def test_self_contained_packmanifest_passes(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            {"dump_id": 1, "labels": ["PackManifest"],
             "props": {"namespace": "tenant-ops", "yaml": "namespace: tenant-ops"}},
            {"dump_id": 2, "labels": ["Object"],
             "props": {"objectKind": "tenant-ops:contract"}},
        ]}
        _check_foreign_kinds(payload)  # manifest in dump = self-contained vocab

    def test_packmanifest_mismatch_still_raises(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            {"dump_id": 1, "labels": ["PackManifest"],
             "props": {"namespace": "other-ns"}},
            {"dump_id": 2, "labels": ["Object"],
             "props": {"objectKind": "tenant-ops:contract"}},
        ]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)
```

**Step 2: Run — expect FAIL (current guard reads top-level keys; props-nested kinds invisible)**

Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_pack_config.py::TestForeignKindsGuard -q`
Expected: `test_pre_v1_1_with_foreign_kinds_raises` FAILS (guard does not raise).

**Step 3: Implement**

In `tortoise/hosted_api.py`, above `_check_foreign_kinds`:

```python
# Kind-carrying prop keys on dump nodes (sdk.py:9661 kind_field — the 6 live
# writer keys) + `kind` (extractor_v2 legacy-compat) + `actionKind` (pack-declared
# bucket, no current node carrier — future-proof). `op_type` is deliberately NOT
# here: operator types are a fixed non-namespaced set (IMPL/NAND/MITIGATES).
_KIND_PROP_KEYS = ("pointKind", "objectKind", "eventKind", "documentKind",
                   "subjectKind", "sourceKind", "actionKind", "kind")
```

Rewrite `_check_foreign_kinds`:

```python
def _check_foreign_kinds(payload: dict) -> None:
    """Loud-mismatch guard for pre-v1.1 artifacts (no pack_config).

    Scans the dump's node kinds for ``ns:kind`` values where ns is neither a
    starter namespace, a shared-catalog pack, nor a namespace carried by the
    dump's OWN PackManifest nodes (self-contained vocabulary — a restored
    manifest makes the vocabulary live, so it must not be rejected). Dump
    nodes are ``{dump_id, labels, props}`` with kinds inside ``props``
    (hosted_backup.dump_graph); a namespaced kind from an unknown pack would
    be silently dropped on import → raise ValueError (→ 422 quarantine).

    Documented boundaries: (a) only NAMESPACED foreign kinds are detectable —
    legacy un-namespaced pack kinds are indistinguishable from core vocab and
    require re-export via migrate_kinds; (b) known-set membership is catalog
    presence, not activation — non-starter CATALOG-pack kinds in a pre-v1.1
    artifact still lose activation (no manifest in the artifact); (c) a None
    registry collapses known to starter + PackManifest (fail-closed bias);
    (d) nested-dict kinds inside props are not scanned (FalkorDB props are
    scalar/array in real dumps).
    """
    from tortoise.domain_loader import _get_registry
    from tortoise.pack_state import DEFAULT_STARTER_PACKS

    reg = _get_registry()
    known = set(DEFAULT_STARTER_PACKS)
    if reg is not None:
        known |= set(reg.pack_summaries())
    for node in payload.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        labels = node.get("labels") or []
        if isinstance(labels, list) and "PackManifest" in labels:
            props = node.get("props") or {}
            if isinstance(props, dict):
                ns = props.get("namespace")
                if isinstance(ns, str) and ns:
                    known.add(ns)
    foreign: set[str] = set()
    for node in payload.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        props = node.get("props") or {}
        if not isinstance(props, dict):
            continue
        for key in _KIND_PROP_KEYS:
            kind = props.get(key)
            if isinstance(kind, str) and ":" in kind:
                ns = kind.split(":", 1)[0]
                if ns not in known and ns != "core":
                    foreign.add(kind)
    if foreign:
        raise ValueError(
            "artifact predates pack-config (v1.1) but references unknown pack "
            f"kinds {sorted(foreign)[:5]} — the vocabulary would be lost; "
            "re-export with a newer tortoise version")
```

**Step 4: Run — expect PASS**

Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_pack_config.py::TestForeignKindsGuard -q`
Expected: PASS (all rewritten + new cases).

**Step 5: Commit**

```bash
git add tortoise/hosted_api.py tests/test_export_pack_config.py
git commit -m "fix(hosted): scan props for foreign kinds in pre-v1.1 import guard (#2028)"
```

---

### Task 2: Real-dump_graph integration test (TDD)

**Intent:** Prove the guard works against ACTUAL `dump_graph` output — the exact shape the bug shipped against — not just hand-built payloads (issue Indicator 3).

**Acceptance:** `_check_foreign_kinds(dump_graph(...))` raises for a graph containing a `tenant-ops:contract` object; passes for a core-only graph.

**Files:**
- Test: `tests/test_export_pack_config.py` (add to TestForeignKindsGuard)

**Step 1: Write the failing test**

```python
    def test_real_dump_graph_foreign_kind_raises(self, sdk):
        from tortoise.hosted_api import _check_foreign_kinds
        from tortoise.hosted_backup import dump_graph
        # NO _seed_packs here: a genuine pre-v1.1 artifact predates PackManifest
        # storage (#1935) so its dump carries NO manifest node — tenant-ops is
        # foreign to both the shared catalog and the dump itself → must raise.
        # (A dump that DOES carry its own PackManifest is self-contained and
        # covered by test_self_contained_packmanifest_passes above — seeding
        # one here would mask the foreign kind and wrongly pass.)
        sdk.create_object("Contract 1", objectKind="tenant-ops:contract")
        dump = dump_graph(sdk._get_proj().g, graph_name="t")
        dump.pop("pack_config", None)  # pre-v1.1 artifact (no pack_config)
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(dump)

    def test_real_dump_graph_clean_graph_passes(self, sdk):
        from tortoise.hosted_api import _check_foreign_kinds
        from tortoise.hosted_backup import dump_graph
        sdk.create_point("statement", content="A claim")
        dump = dump_graph(sdk._get_proj().g, graph_name="t")
        dump.pop("pack_config", None)
        _check_foreign_kinds(dump)
```

**Step 2: Run — the tests PASS immediately (Task 1 already fixed the guard)**

Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_pack_config.py::TestForeignKindsGuard::test_real_dump_graph_foreign_kind_raises tests/test_export_pack_config.py::TestForeignKindsGuard::test_real_dump_graph_clean_graph_passes -q`
Expected: PASS — Task 1's rewritten guard already scans `node["props"]`, so the real-dump tests are green immediately. This task is a REGRESSION LOCK on the real dump shape (the exact shape the bug shipped against), not a fresh red-green cycle.

**Step 3: No production change** (Task 1's implementation covers it) — verify both real-dump tests pass.

**Step 4: Run — expect PASS**

Run: both real-dump tests. Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_export_pack_config.py
git commit -m "test(hosted): guard against real dump_graph output (#2028)"
```

---

### Task 3: Hoist the guard pre-restore in the import flow (TDD)

**Intent:** A foreign-kind artifact must 422 with the live graph untouched and `last_import_sha256` unstamped (retries converge) — the fail-closed contract the post-swap placement violates (issue Indicators 1-2).

**Acceptance:** Import endpoint returns 422 + `quarantined_import` audit for a pre-v1.1 foreign-kind artifact with live-graph ids unchanged; a post-v1.1 artifact with the same kinds still imports 200.

**Files:**
- Modify: `tortoise/hosted_api.py` (import flow ~8009)
- Test: `tests/test_import_endpoint.py` (TestImportValidationFailClosed + TestImportHappyPath)

**Step 1: Write the failing endpoint tests**

```python
    def test_import_pre_v1_1_foreign_kind_422(self, sb_client, as_user,
                                              capture_audit):
        """Pre-v1.1 artifact (no pack_config) with a namespaced custom-pack
        kind → guard fires PRE-restore → 422 + quarantine; nothing lands in
        the live graph, last_import_sha256 unstamped (retries converge).

        No _seed_live_graph — no additional long-held holder on the file
        (the anchor+SDK pair is empirically safe: same pattern as the unskipped
        json-body-form / fresh-team tests)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["pointKind"] = "tenant-ops:contract"
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 422, r.text
        assert any(e["operation"] == "quarantined_import" for e in capture_audit)
        assert _counts(db_path)["ids"] == []  # nothing landed (pre-restore)
        # ledger NOT stamped → re-import of the same artifact re-validates
        # (quarantine stamps a separate key; last_import_sha256 is untouched)
        assert not fake.tables["teams"][0].get("last_import_sha256")

    def test_import_post_v1_1_custom_pack_passthrough_200(self, sb_client,
                                                          as_user):
        """Post-v1.1 artifact (pack_config with packs present) with a custom-
        pack kind → guard gated off; restore + manifest upsert succeed →
        200. Locks the gate: WITHOUT packs the same kinds 422 (see
        test_import_pre_v1_1_foreign_kind_422 and
        test_import_v1_1_empty_packs_foreign_kind_422); WITH them they import.

        No _seed_live_graph — no additional long-held holder (same pattern as
        the unskipped json-body-form / fresh-team tests)."""
        tc, fake, db_path = sb_client
        _seed_team(fake)
        as_user()
        key = os.urandom(32)
        payload = _build_payload(n_points=1, n_edges=0)
        payload["nodes"][0]["props"]["objectKind"] = "tenant-ops:contract"
        payload["pack_config"] = {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": CUSTOM_MANIFEST}]}
        artifact = _build_artifact(payload, key)
        r = _post_import(tc, artifact, key)
        assert r.status_code == 200, r.text
        assert r.json()["imported"] is True
        assert _counts(db_path)["ids"] == ["pt-0"]  # restore+swap landed
```

(`CUSTOM_MANIFEST` imported or defined in test_import_endpoint.py — copy the small manifest constant from test_export_pack_config.py.)

**Step 2: Run — expect FAIL (post-Task-1 red: graph pollution + ledger stamp, not a 200)**

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_import_endpoint.py::TestImportValidationFailClosed::test_import_pre_v1_1_foreign_kind_422 -q`
(Module is embedded-lane by design — its autouse fixture pops `TORTOISE_DB_URI` mid-session, so the docker URI is inert for this file. But the epic #1647 P4 SESSION gate still requires a URI unless the carve-out flag is set — hence `TORTOISE_TEST_CARVE_OUT=1` on every test_import_endpoint.py run.)
Expected: FAIL — after Task 1 the (post-swap) pc-None guard call already 422s + quarantines, so the first two assertions pass, but the foreign node HAS landed (restore+swap ran before the post-swap guard) and `last_import_sha256` IS stamped → the `ids == []` and ledger-unstamped assertions fail. Red proves the hoist (Task 3) is required for the empty-graph/ledger acceptance.

**Step 3: Implement the hoist**

In `tortoise/hosted_api.py`, inside the existing `try:` block that wraps `_restore_into_temp_verify_swap` (after the in-lock idempotency short-circuit and the 413 size-cap, immediately before the restore call):

```python
            try:
                # #2028: run the foreign-kind guard BEFORE any restore/swap so
                # a rejection 422s with the live graph untouched and
                # last_import_sha256 unstamped (retries converge). The guard
                # absorbs the artifact's own pack_config declared namespaces
                # AND dump-native PackManifest namespaces into its known-set,
                # so every legitimate artifact (pre-v1.1 core/starter, v1.1
                # with covering packs, self-contained manifests) passes and
                # only genuinely undeclared foreign vocabulary 422s —
                # fail-loudly, never silent partial.
                await asyncio.to_thread(_check_foreign_kinds, parsed["payload"])
                result = await asyncio.to_thread(
                    _restore_into_temp_verify_swap,
                    sdk._get_proj().db, parsed["payload"],
                    live_name=graph_name,
                )
            except RestoreVerificationError as e:
                ...
```

**Step 4: Run — expect PASS (both tests)**

Run: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_import_endpoint.py -q`
Expected: PASS — new 422 test passes (empty team graph, ledger unstamped); passthrough 200 passes (`["pt-0"]`); no existing endpoint test regresses (existing payloads use bare `claim` kinds → guard passes). Note both new tests are intentionally NOT `@_import_deep` — no `_seed_live_graph`, so no single-writer collision (same pattern as the unskipped json-body-form / fresh-team tests).

**Step 5: Full suite for both files**

Run: `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_pack_config.py -q && TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_import_endpoint.py -q`
Expected: PASS.

**Ordering/precedence notes (documented, by design):** the 413 node-count cap runs before the lock and the guard — a dual-fault payload (foreign kind + oversize) returns 413, not 422 (defensible: size rejection first). Envelope/tamper failures 422 before the guard. The `_apply_import_pack_config` pc-None guard call is retained as defense-in-depth but is redundant-by-construction post-hoist: same payload, same sticky process-global registry — it can never fire for a payload the hoist passed.

**Follow-up note (pre-existing, OUT of #2028 scope):** malformed-but-truthy `pack_config` shapes (`{"packs": [42]}` → AttributeError in `_apply_import_pack_config`'s `pack.get` post-swap; non-list packs) 500 after the swap/ledger stamp — pre-existing (#1936), unreachable from any exporter; the unconditional guard narrows (does not widen) the exposure window by 422ing foreign-kind payloads pre-restore. Filed as #2040; not fixed here. **Boundary:** the backup-restore endpoint (`restore_backup`, hosted_backup.py — same `dump_graph` shape, no guard/pack-config application) has the same pre-v1.1 silent-drop class but is a separate surface, out of #2028 scope — noted in PR #2041 follow-ups.

**Step 6: Commit**

```bash
git add tortoise/hosted_api.py tests/test_import_endpoint.py
git commit -m "fix(hosted): run foreign-kind guard pre-restore on import (#2028)"
```

---

### Task 4: Full verification + commit-workflow

**Intent:** Prove the fix end-to-end and ship through the mandatory review gates.

**Acceptance:** Both test files pass on the docker lane; commit-workflow completes; PR created with #1970 CI note if hit.

**Step 1: Run the full target suite**

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_pack_config.py -v
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_import_endpoint.py -v   # embedded lane by design
```

**Step 2: Broader regression sweep (cheap, same lane):** export/backup surfaces that consume the same dump shape:
`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_hosted_backup.py tests/test_export_cli.py -q`

**Step 3: Commit + PR via commit-workflow skill** (MANDATORY — pre-flight, code-review gate, auto-merge). Note #1970 python-ci-gate failures in the PR body if hit.

---
<!-- plan-review: status=clean, cycles=3, verifiers=2 parallel per cycle (task-workflow-standard PLAN-VERIFY gate); no P0/P1 remaining; 3 P2 narrative fixes incorporated (cycle 3) -->
