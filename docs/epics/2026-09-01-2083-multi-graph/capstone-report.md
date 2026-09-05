# Capstone verification report — epic #2083 multi-graph tenancy

**Status: PASS** · **Commit verified on:** `b2508bb8` (docs-merge head; children C1–C8 shipped `2110`–`2117`, all closed) · **Date:** 2026-09-04/05 · **Mode:** docker-lane FalkorDB (`docker://:falkordb@localhost:6379`, container up) + committed-dist dashboard (two-server wrangler harness) · **Capstone:** #2118.

## Scope

The epic's 12 detailed E2E scenarios (plan §7) executed against the shipped
state through their assigned test layers — no mocks for the control/data
plane (live FalkorDB); the dashboard UI ran against the committed dist with
a layered session harness (the repo's dashboard-e2e pattern — the browser
cannot hold credentials, #2246 ADR-010).

## Suite results (docker lane, individually run — cross-file isolation)

| E2E | Suite(s) run at b2508bb8 | Result |
|---|---|---|
| E2E-1 provision + per-graph key | test_control_plane.py, test_acl_graph_users.py (ACL user permission set), writer_inventory provisioning | ✅ 49+14+67 |
| E2E-2 cross-graph isolation | test_tenancy_spine.py (ACL-OFF spine) + test_acl_graph_users.py (ACL-ON NOPERM) + test_mcp_http.py (MCP surface) | ✅ 7+14+53 |
| E2E-3 tier gate 402/409 | test_control_plane.py, test_hosted_api.py (E2E-3 402-first/solo-409 pins), test_supabase_control.py (supabase lane) | ✅ 49+54+163 |
| E2E-4 one-level-deep | test_hosted_api.py (deleg=0 negatives), writer_inventory | ✅ (in 54/67) |
| E2E-5 existing-team migration | test_tenancy_spine.py (legacy_full_access resolution), writer_inventory | ✅ |
| E2E-6 delivery-shape tenancy | test_delivery_tenancy.py (16: context/sessions per graph, recording override, vanish fail-closed) + test_capture_session.py (REST+MCP session capture) | ✅ 16+113 |
| E2E-7 quota + revocation | test_hosted_api.py (409 + X-Graph-Quota; rollback), test_suspension_parity.py | ✅ |
| E2E-8 graph lifecycle | test_hosted_api.py (delete/tombstone/cascade/name-reuse/no-orphan), test_control_plane.py | ✅ |
| E2E-9 key scopes + legacy | test_hosted_api.py (scope matrix pins), test_writer_inventory.py | ✅ |
| E2E-10 suspended team | test_suspension_parity.py (29: both planes 403, appeals open) | ✅ |
| E2E-11 concurrent provisioning | test_hosted_api.py TestProvisioningConcurrency (no oversubscription) | ✅ |
| E2E-12 key mgmt + dashboard create | test_writer_inventory.py + dashboard e2e (below) | ✅ |
| Migration/reversibility (surface 13) | PGlite validate.mjs incl. the C8 rollback drill (apply→rollback→re-apply) | ✅ |

**Dashboard clickthrough (E2E-12 UI half):** `tests/e2e/test_graphs_management.py` 9/9 + `test_keys_table_mixed.py` 4/4 (13/13) at b2508bb8 on the two-server committed-dist harness — meter shapes, reveal-once modal, per-graph key panel mint/revoke, delete arm-confirm, free/anon 🔒 lock, 409 inline errors, zero key-authed requests.

## Clickthrough screenshots (captured on the committed dist)

| Frame | What it verifies |
|---|---|
| [02-graphs-tab.png](capstone-shots/02-graphs-tab.png) | Graphs tab: default-first rows (badge, no actions), custom row Keys/Delete, meter "2 graphs · ∞ cap" |
| [04-reveal-modal.png](capstone-shots/04-reveal-modal.png) | One-time reveal modal — "shown once", key plaintext, Copy & done / I saved it (no show-key route) |
| [05-key-panel.png](capstone-shots/05-key-panel.png) | Per-graph key panel "Keys for prod": list (prefix/created/status), Revoke, mint form |
| [06-mint-reveal.png](capstone-shots/06-mint-reveal.png) | Panel mint → same reveal modal (plaintext once) |
| [07-delete-armed.png](capstone-shots/07-delete-armed.png) | Delete arm-confirm on a custom row (default row has no Delete) |
| [08-free-locked.png](capstone-shots/08-free-locked.png) | Free tier: 🔒 locked create + upgrade CTA |

## Data-state verification

- **No cross-graph writes:** tenancy_spine + acl_graph_users assert zero
  cross-graph reads/writes across REST/MCP/SDK surfaces (app-layer spine
  with ACL OFF; NOPERM with ACL ON) — live FalkorDB.
- **No orphan artifacts:** E2E-8 pins assert the deleted graph's keys are
  revoked + its ACL user dropped; control_plane asserts no orphan from
  rejected concurrent mints.
- **Quota accounting:** X-Graph-Quota on cap; per-team lock count-then-
  insert (no oversubscription, E2E-11); default occupies slot 1.
- **Audit trail:** key create/revoke/graph lifecycle events asserted by the
  suite legs (hosted_api audit pins).

## Residuals (filed/accepted, not blockers)

- R2 FalkorDB GRAPH.LIST name leak (#2652 upstream-open) — mitigated by
  denying GRAPH.LIST/KEYS/SCAN on ACL users + app-layer as authoritative
  (plan R2 stance; NOT triggered at runtime — version-dependent).
- Per-graph sweep enumeration (backup/event-retention) = C5 handoff
  residual; R13 periodic audit owns retention amplification.
- MCP `_get_team_sdk` vanish race = accepted residual (honest error).
- Dashboard key panel mints the graphs:read+write pair only (scope-aware
  mint UI = documented future).

## Verdict

**PASS** — all E2E-1..12 verified through their assigned layers at
`b2508bb8`; dashboard clickthrough captured (screenshots above); cross-graph
isolation violations = 0; no orphan artifacts in the exercised paths;
quota accounting + audit trail present. Epic #2083 is complete.
