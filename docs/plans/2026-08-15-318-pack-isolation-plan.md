# Plan: Multi-Tenant Pack Isolation (hosted)

> **Issue:** #318 · **Status:** planned (decisions locked 2026-08-15) · **Scoping:** approach A approved; owner decisions in issue comment 5287515660

## Goal

Implement the approved Approach A from the scoping: shared pack catalog + per-tenant `PackInstall` activation records in each tenant's graph (no file copies), idempotent activation at all provisioning sites, read-only introspection, backfill for existing tenants.

## Design decisions (locked 2026-08-15)

| # | Question | Decision |
|---|---|---|
| D1 | Pack model | Fixed default set for all tenants now; selection UI later (post-enterprise tier) |
| D2 | "Copies starter packs" | Activation records, not file copies (industry pattern: shared catalog + per-tenant install-state; shared resources read-only for tenants) |
| D3 | Enterprise governance | Defer until `tier: enterprise` exists (today a validation error) |
| D4 | Introspection surface | Minimal read-only REST `GET /v1/packs` + MCP `packs_list` - in scope |
| D5 | Existing-tenant backfill | Idempotent operator script re-runs activation per existing team; handles `team_{name}` vs `team_{id}` naming via recorded `graph_name` |
| D6 | Existence masking | Empty result (no error) when nothing to see; errors only for auth failures - matches #969 masking pattern |

Note (owner 2026-08-15): no legacy customers pre-launch (0 customers), so backfill is belt-and-suspenders - still included for correctness. Builders' own packs belong to #557 (sub-tenancy), sequenced after this.

## Tasks

1. **pack_registry helper** - shared-catalog read + per-tenant activation record model (`PackInstall`).
2. **pack_state.py** - per-tenant activation state (graph-native, provenance-marked), idempotent additive MERGE.
3. **Provisioning hooks** - activate defaults at all 3 sites: `/internal/provision` (hosted_api.py:636), `provision_team` RPC (supabase_control.py:1114), self-service key provisioning (hosted_api.py:2004).
4. **Introspection surface** - read-only `GET /v1/packs` + MCP `packs_list` (auth-only, fail-closed; D6 masking).
5. **Backfill script** - idempotent activation per existing team (graph-scripts/), `--dry-run` first.
6. **Env + verification** - staging test: cross-tenant isolation (tenant A cannot see tenant B packs), idempotency re-run, masking.

## Acceptance (from scoping AC1-AC6)

- Default packs auto-active per new tenant at signup/provision.
- Activation idempotent (re-run no-ops).
- `GET /v1/packs` + `packs_list` read-only; empty-masked per D6.
- Cross-tenant isolation verified in staging (O/I/T test).
- No file copies; no shared-mutation possible (read-only for tenants).
- Backfill handles legacy naming inconsistency; dry-run default.

## Non-goals

- Builders' own packs -> #557 (sub-tenancy epic, sequenced after this).
- Custom-pack authoring -> #1154 (must close before that slice).
- Enterprise governance/selection UI -> deferred (D3).
- Per-tenant pack copies (F1) -> rejected.

## Test plan

- Integration surfaces: 3 provisioning sites, `GET /v1/packs`, `packs_list` MCP, idempotency, cross-tenant isolation test, masking test.
- Regression: existing provisioning tests stay green.
