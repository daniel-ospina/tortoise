---
title: "#2781 — cross-pack references: an absent pack must not drop its referrers"
type: engineering
domain: platform
doc_status: implemented
created: 2026-10-12
subjects.team: epistemic-team
aboutObjects: tortoise-pack-registry, tortoise-pack-manifest, tortoise-tenant-packs
aboutSubjects: tortoise-cross-pack-reference, tortoise-pack-dependency
---

# #2781 — cross-pack references

**Issue:** #2781 (`complexity:complex`, `P1-severe`) · **Branch:** `feat/2781-tm`
**Related:** #2725 (the venture/HR split that surfaced this), #2728/#2714 (per-graph pack
selection), #2714 (hosted extractor vocabulary = the *installed* set)

## The defect (re-measured at `a91333d02`)

A domain pack could not point at a kind owned by another pack without paying for it twice.

1. **Self-hosted — an absent pack took its referrers down with it.** An unresolvable cross-pack
   reference *isolated* the referring pack (`pack_registry.load_all` drops every namespace in
   `registry.errors`), and the fixpoint re-validated against the surviving set, so the isolation
   cascaded. Measured: dropping `project-management` (namespace `pm`) loaded **3 of 6** packs
   (`agent-ops`, `marketing`, `venture`) — `dev` and `product-strategy` went with it. Dropping
   `product-strategy` did the same to `dev` and `marketing`.
2. **`depends_on` was dead config** — parsed at `pack_registry.PackManifest` and written by every
   manifest as `[]`, with zero consumers repo-wide.
3. **Hosted — no cross-pack reference could ever install.** `validate_manifest` validated each
   upload in a temp registry containing exactly that one manifest, so every `ns:kind` reference
   (including one to the starter catalog) failed. Reproduced in
   `TestCrossPackInstall2781::test_upload_pair_second_pack_referencing_the_first_201`.

## Decision — `depends_on` is a consumed declaration; a missing peer is not a broken manifest

The two questions the issue left open are answered by the migration of the retired decision
register (#2826 row B8: OPEN-ENGINEERING, recommendation *fail-closed declarative resolution*), and
by the issue's own indicators — which are more specific than the recommendation and win where they
disagree:

| | Ruling |
|---|---|
| Bucket | `registry.errors` = the manifest is **unusable** (schema / typo) → isolated. `registry.unresolved_refs` = **a missing peer** → the pack loads, the reference is reported. |
| Clause 1 | A namespaced ref whose **namespace is not loaded** is a missing pack, never a typo: the namespace does not resolve *at all*, so it cannot be a misspelling inside a present pack. |
| Clause 2 | Any unresolvable ref (bare included) is a missing peer when the declaring pack has a `depends_on` entry that is not loaded. A bare name carries no namespace, so without that declaration there is no evidence of absence → it stays a **typo** and the pack is isolated. |
| `depends_on` | Consumed at registry load (an absent declared dependency is reported against the declaring pack, by name) **and** enforced fail-closed at hosted install. |
| Self-hosted vs hosted | Different questions. Registry load is **availability-preserving** (one absent pack must not take down the catalog); a hosted **install** is **fail-closed** (`validate_manifest` refuses, naming the missing pack). |

**Why clause 2 is not leniency.** It is the only rule that lets a *bare* reference be distinguished
from a typo, and the declaration is what supplies the evidence. It converted the `product-strategy`
cascade (bare `nearMisses` in `dev`/`marketing`) into two reported gaps, which clause 1 alone could
not do.

**Cost, stated.** A pack with a declared-but-absent dependency and a typo'd **bare** reference
reports rather than isolates it. Clause 1 keeps the exact-name case fatal, and every tolerance is
reported (never silent), but the combination is a deliberate trade — recorded here, not discovered
later.

## Change

| File | Change |
|---|---|
| `tortoise/pack_registry.py` | `unresolved_refs` bucket; the two clauses (`_missing_pack_explanation`); `_report_unresolved` routing all five ref sites (relations / `equivalentTo` / v3.1 slots / `nearMisses` / chains); `depends_on` declared-dependency pass + shape guard; a warn-once "loaded with unresolved references" line. |
| `tortoise/pack_manifest_store.py` | `validate_manifest(..., installed_manifests=…)` seeds the starter catalog (resolved **by declared namespace**, not by directory name — `packs/project-management/` declares `pm`) plus the tenant's own manifests; unresolved refs refuse the install. `validate_manifest_for_tenant` / `upsert_tenant_manifest(..., extra_manifests=…)` are the single entry point for all three install routes. |
| `tortoise/hosted_api.py` | Upload pre-check uses the tenant-aware validator (a pre-check that cannot see the tenant's packs would refuse the manifests it exists to admit); the restore path passes its siblings so a cross-referencing `pack_config` validates as the SET it will become, not in install order. |
| `packs/{dev,marketing,product-strategy}/manifest.yaml` | The cross-pack references these manifests actually make are now **declared** (`dev → product-strategy, pm`; `marketing → product-strategy`; `product-strategy → dev`). |
| `packs/_template/manifest.yaml` | Documents the consumed semantics of `depends_on`. |

Not changed: the MCP tool registry and the public `TortoiseSDK` surface (no surface change is
needed or approved).

## Verification

```bash
.venv/bin/python tools/test_lane.py uri    # private lane instance (#5084)
export TORTOISE_DB_URI='docker://:@127.0.0.1:<port>/tortoise_test_matrix'
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
  tests/test_pack_registry_gaps.py tests/test_pack_kinds.py \
  tests/test_pack_manifest_store.py tests/test_pack_extraction_slots_v31.py \
  tests/test_pack_manifest_store_extraction.py tests/test_pack_state.py \
  tests/test_export_pack_config.py tests/test_rebuild_config_preservation.py \
  tests/test_pack_content_v3.py tests/test_venture_pack.py \
  tests/test_pack_shipping.py tests/test_domain_loader_adapter.py \
  tests/test_pack_cli.py tests/test_agent_ops_pack.py tests/test_pack_shipping_wheel.py \
  -q -p no:cacheprovider
# 594 passed, 1 skipped
```

The regression the issue names is pinned on the REAL catalog
(`test_dropping_one_catalog_pack_does_not_drop_its_referrers`, parametrized over
`project-management` and `product-strategy`), and the hosted indicator end-to-end
(`test_upload_pair_second_pack_referencing_the_first_201`).
