<!-- research-path: the issue comment carrying `<!-- research: v1 -->` (to be posted on #10180 with this plan) -->
<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->

# #10180 — the verb that MOVES the default graph, and the lanes that must follow it

**Level:** task · **complexity:** standard · **class:** code
**Branch:** `feat/10180-promote-default-graph` · **Path:** `decided-ruling` (decision
comment on #10180, `issuecomment-6449884575`: "Build #10180 as the selection verb
only, plus one line of visibility")

**Paths:** `supabase/migrations/20261102000001_graphs_promote_default.sql`,
`supabase/tests/20261102000001_graphs_promote_default.sql`, `supabase/tests/pglite/validate.mjs`,
`tortoise/supabase_control.py`, `tortoise/hosted_api.py`, `tortoise/mcp_auth.py`,
`tests/fake_control_plane.py`, `tests/test_10180_promote_default_graph.py`,
`docs/authorization-architecture.md`, `docs/registry-graph-schema.md`,
`docs/ops/multi-graph-migration-runbook.md`

## 1. The problem, stated as a falsifiable claim

A team can create graphs and mint a key bound to one, but **nothing can move the
default**: every writer of `kind='default'` in `public.graphs` is a *creation*
path (`ensure_default_graph_row`, `set_graph_name`'s no-active-row insert, the
`20261028000001` backfill), and no surface updates it. Measured at this head:
`git show origin/main:tortoise/hosted_api.py | grep -c promote` → **0**. Moving a
team onto a graph it outgrew therefore requires a hand-edited production row —
unversioned, untested, and invisible to the readers that derive the default from
it. That is the step #10175's switchover needs.

**Falsifier for the design chosen below:** an organization that has never
promoted whose data access moves. The whole change is built so that cannot
happen (see §3); if any test showed a never-promoted org opening a different
graph, this design is wrong and the whole class conversion must be withdrawn.

## 2. Why "flip the row" is not enough — and what that costs

"The default graph" is two facts (`docs/ops/multi-graph-migration-runbook.md` §6):
the `kind='default'` row (the RECORD tenant) and `organizations.graph_name` (the
DATA-PLANE namespace an org-wide credential opens). A promotion that moved only
the row would file record rows under the new graph while the points went to the
old one — the `derived != measured` class #10025 exists to remove. So the verb
moves BOTH, in one transaction.

That in turn means every lane that opens "the org's default graph" with
`_make_sdk(namespace=org_id)` (the derivation `org_{org_id}`) must follow the
pointer, or a promotion leaves the dream pass, the event sweep, the pack catalog,
the quota counter and the seed/indexing runners writing the DEMOTED graph while
the record names the promoted one. The inherited (uncommitted) attempt converted
`_data_sdk` alone; a fresh-context review confirmed eleven lanes left behind,
five of which bind the record tenant from the row and write to the derivation —
the split, in the other direction.

## 3. The design decision, and the contradiction test

**The flip is gated on the DATA, not on the lane.** One pure function,
`supabase_control.moved_default_namespace(org_id, namespace)`, returns a
namespace only when it is a **per-graph namespace of that org**
(`org_{org_id}_{gid}` — the shape `insert_graph`/`_provision_graph` mint, which
only a promotion can put in `organizations.graph_name`), and only in hosted mode.
Everything else returns `None` and the caller takes the historical open:
untouched `org_{org_id}`, legacy `team_{org_id}`, registry `org_{name}`.

* **Contradiction test, run first.** The obvious implementation — "the row is the
  single authority", option 1 in the runbook — would change the org-wide
  resolution *unconditionally* and so reverse **#2023's recorded pin** (the
  selfhost `org_{name}` lane, and pre-#1903 hosted rows, must not have their data
  access moved). That is a reopen, not a lane's call, and it was never answered
  on the issue. This design does **not** reverse it: nothing moves for any org
  until an operator explicitly promotes one, and the registry lane never flips.
  Option 1 remains available as a separate, unblocked decision; it is simply no
  longer a precondition for the verb.
* **`OVERRIDES:`** none — no recorded decision is departed from.

## 4. Wiring (every consumer of what this touches)

| what | where | what it does there |
|---|---|---|
| `public.graphs` (`kind`, `status`) + `uq_graphs_org_default_active` | `20261028000001` | the one-live-default invariant the move must not violate |
| `organizations.graph_name` | `_graph_namespace_for` → every resolved credential | the org-wide namespace; the promotion moves it |
| `default_graph_id_for` | `record_writer.record_tenant_for` | the RECORD tenant — moves with the row, no change needed |
| `_data_sdk` / `_data_graph_name` | REST data plane + admission gate | open the moved namespace when it moved |
| `mcp_auth._get_org_sdk` | MCP data plane | same, via `moved_default_namespace` |
| `_sweep_events`, `_dream_worker._open_sdk`, `_dispatch_webhooks_sync`, `org_info`, `list_packs`, `upload_pack_manifest`, `_org_proj`, `_open_org_graph_sdk`, `_graph_available`, `_get_onboarding_projection`, `patch_onboarding_state`, `_run_indexing`, `_relink_sessions_after_index`, `_run_docs_indexing`, `_run_onboarding_seed`, `_run_starter_seed`, `_seed_demo_graph`, `_seed_demo_sync`, `import_org` | `hosted_api` | all now open through `_org_sdk` |
| `quota._count_resource` points/sessions/documents | `_check_org_limit` | gets the default-graph SDK explicitly (money gate) |
| `graph_metadata`, `backup_sweep.enumerate_org_graphs` | dashboard + backups | read `graph_name`, which the promotion moves — consistent by construction |
| provisioning/creation sites (`provision_tenant`, `register_user`, `_eager_provision_org_graph`, `_drop_org_graph_impl`, `_open_org_graph_sdk`'s canonical branch) | `hosted_api` | stay historical; reasons recorded in the inventory test |

## 5. Implementation steps, each with the check that proves it

1. **Migration** `20261102000001_graphs_promote_default.sql` — renumbered off
   `20261030000001` (taken by `oauth_consent_tickets` on `origin/main`).
   `promote_default_graph(p_org_id, p_graph_id)`: org row `FOR UPDATE`, target
   row `FOR UPDATE`, refusals (unknown/foreign, trash, non-active), demote →
   promote → move `graph_name`, **post-condition** (exactly one ACTIVE default and
   it is the target, else RAISE → rollback), idempotent arm with the §3 drift
   repair. → `supabase/tests/20261102000001_*.sql` (pgTAP: move, invariant,
   idempotence + repair, four refusals, rollback drill, second promotion) run by
   `node supabase/tests/pglite/validate.mjs` — **PASSED**.
2. **Control-plane seam** `supabase_control.promote_default_graph` + named error
   mapping (404/409, `PROMOTE_CONFLICT` for the post-condition) and the pure
   `moved_default_namespace`. → Python tests.
3. **The REST verb** `POST /v1/graphs/{graph_id}/promote` (REST-only: no MCP tool,
   no public SDK method — #4282), auth mirroring the neighbouring PATCH/DELETE,
   `default` sentinel, audit event, explicit in-flight semantics in the response.
   → endpoint tests (200/404/409/403, single RPC, no table PATCH).
4. **The class**: `_org_sdk` + conversion of the lanes in §4, `mcp_auth`, quota.
   → `test_every_org_wide_opener_is_converted_or_accounted_for` (AST inventory),
   `test_org_sdk_is_byte_identical_for_an_unpromoted_org`,
   `test_the_quota_gate_counts_the_promoted_graph`,
   `test_the_mcp_lane_follows_a_promoted_default`.
5. **One line of provenance**: `resp["graph"] = {id, namespace}` on the capture
   receipt, additive. → receipt tests.
6. **Docs**: the two "the default is immutable" claims + a runbook §6 subsection
   recording what landed and why it is not option 1.

## 6. Rounds

**Rounds:** 1 (verifiers: 1, class: code) — plus the code-review panel at the PR
head, which is the attestation gate.

### Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

* R1 A never-promoted org's data access moves (the #2023 regression) → `test_the_gate_leaves_every_unmoved_namespace_alone` (5 shapes) + `test_org_sdk_is_byte_identical_for_an_unpromoted_org` + `test_the_mcp_lane_is_unchanged_for_an_unpromoted_org`; mutation-proved.
* R2 A promotion leaves the org with zero (or two) live defaults → pgTAP invariant + rollback drill + the SQL post-condition/row-lock pin + `test_promotion_never_leaves_two_defaults` + `test_soft_delete_cannot_tombstone_a_graph_that_became_the_default`.
* R3 A lane keeps reading/writing the demoted graph after a promotion (record/data split, money gate, retention) → the AST inventory test + the per-lane resolver tests; mutation-proved by reverting one site. The org HARD DELETE's single-graph drop is a pre-existing class (it drops the default's storage and leaves the rest), filed as **#10450** and recorded in the inventory with that reason.
* R4 The receipt shape changes for existing consumers → additive-only assertions in the receipt test.
* R5 A migration version collides with `origin/main` (append-only, aborts `db push`) → renumbered to `20261102000001`; `supabase/migrations` has no duplicate prefix; the PGlite harness applies every migration first.
* R6 The promote endpoint is reachable by a data-plane key, or leaks existence across orgs → 403/404 tests.
* R7 The verb makes a previously-impossible interleaving reachable: a DELETE tombstoning the DEFAULT row (nothing could change `kind` before). → `soft_delete_graph`'s tombstone PATCH now carries the `kind != 'default'` predicate and returns its rowcount, and the endpoint re-reads to answer 403 rather than 404; mutation-proved.

**Out of scope — must NOT be raised** (note, file, do not chase):

* pre-existing defects outside the diff · style/prose preference with no behaviour change
* architectures the plan did not propose (option 1's unconditional authority; per-key rebinding, #10362; cross-graph merged retrieval)
* incidental test/fixture shape · the private-lane container's ACL configuration (the 5 `test_pack_selection_by_graph::TestCreateGraphWithPacks` failures reproduce on clean `origin/main` with the same container)
* the org HARD DELETE's single-graph drop — pre-existing, filed as #10450

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.
