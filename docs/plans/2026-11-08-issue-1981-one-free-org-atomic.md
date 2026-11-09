<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/1981 (comment carrying `<!-- research: v1 -->`) -->
<!-- plan-review: cycles=0, status=not-run-in-lane (this lane has no reviewer; the fresh-context review is the harness PR review at the pushed head) -->

# #1981 — the one-free-org entitlement, atomic in the write's own transaction

**Issue:** [#1981](https://github.com/daniel-ospina/tortoise/issues/1981) · **Team:** organisation-design-team
**Complexity:** standard · **Predecessors:** #1877 → #2789 → #2810 → #1954 (this is #1954's residual)

## 1. Confirmed problem

`_assert_free_org_allowance` (`tortoise/hosted_api.py:24582`) is a **read-then-write**: it reads
`owned_free_org_ids` and then the caller provisions. #1954 closed the window with a per-user
`asyncio.Lock` (`_org_create_lock`, `tortoise/hosted_api.py:24422`) — which is **per process**.
Every control-plane statement is its own PostgREST request = its own transaction, so no
application-level lock can span the read and the write, and a second uvicorn worker (or a second
app instance on the same Supabase project) has its own lock dict. Two workers can each read
"owns 0 free orgs" and each provision → two free orgs for one person. The single-worker assumption
is documented (`docs/capacity.md:106`, "`Dockerfile.hosted` runs a single `uvicorn` worker"), which
is exactly why the gap is invisible until a deployment runs N workers / N replicas.

**Falsifier** (the observation that would overturn the chosen approach): if a partial unique index
could express the predicate, or if a stored-column denormalisation were acceptable, the create-path
re-check would be the wrong seam. Both fail — see §2.

## 2. Alternatives considered

| # | Alternative | Why it loses |
|---|---|---|
| A | **Partial unique index** on `org_memberships` (the mechanism the issue names first) | The rule's inputs live on TWO tables: it counts `org_memberships` rows but decides on `organizations.subscription_status`. A PostgreSQL index predicate cannot reference another table. |
| B | **Denormalise the rule onto a column** ("claim the free slot") + unique index on it | Contradicts a settled product decision: `create A → buy B → cancel B` legitimately ends with two free orgs (`docs/scoping/2026-09-10-2789-one-free-org.md` §2, pinned by `TestCancellationLifecycleKnownHole`). A hard unique index would make `customer.subscription.deleted` FAIL on that transition. |
| C | **Cross-process lock only** (advisory lock, no re-check) | A lock without an in-transaction re-read is not a gate; a writer that does not take the lock (or a key-derivation drift, #1879's finding) silently loses mutual exclusion. |
| D | **Atomic conditional provision — chosen** | The check moves INSIDE `provision_team`, which already is that one transaction: `pg_advisory_xact_lock` serialises the user's concurrent provisions across processes, then the SAME predicate the Python gate reads is re-evaluated inside the transaction, and a refusal aborts before any row is written. Transaction-scoped lock → released at COMMIT/ROLLBACK, nothing to leak or steal. |

## 3. Integration surface map

| Surface | Layer | Evidence |
|---|---|---|
| `provision_team` RPC decision matrix | SQL (PGlite harness) | `supabase/tests/0010_provisioning_rpcs.sql` §8 |
| Refusal → structured 402, create-org lane | HTTP (FastAPI TestClient) | `TestAtomicProvisionBackstop::test_rpc_refusal_surfaces_the_structured_402` |
| Refusal → structured 402, onboarding lane | HTTP | `…::test_onboarding_lane_maps_the_refusal_to_the_same_402` |
| Stranded graph namespace on refusal | unit | `…::test_rpc_refusal_drops_the_eagerly_materialized_graph` |
| Reclaim is gated on PROOF the provision did not land (§4) | unit | `TestNamespaceReclaimAttribution::test_only_a_server_REJECTION_proves_nothing_landed` (4xx yes · 5xx/transport no) · `…::test_an_ambiguous_rpc_failure_does_not_drop_the_graph` · `…::test_a_duplicate_name_409_drops_the_graph` · register lane both ways: `…::test_a_provision_refusal_still_reclaims_in_the_register_lane` / `…::test_a_post_provision_failure_does_not_reclaim_the_committed_org` |
| Non-vacuity (a 500 stays a 500) | HTTP | `…::test_unrelated_rpc_failure_is_not_laundered_into_a_402` |
| SQL↔Python marker lockstep | static (file read) | `…::test_the_marker_is_the_one_the_migration_raises` |
| Existing single-process burst behaviour | unchanged | `TestConcurrentTeamCreationTOCTOU` (still green) |

## 4. Wiring

⛔ **Cites are SYMBOLS, not `file:line` offsets, on purpose.** This table carried line numbers and three of them were stale at the head that shipped them (a 24–34 line miss in a 45k-line file), which is exactly how the next lane lands on the wrong function. A symbol survives the next edit; an offset does not.

| Call site | What it does after this change |
|---|---|
| `tortoise/supabase_control.py` `owned_free_org_ids` | Unchanged; **its predicate is now duplicated inside the RPC**, and the pair is pinned by `test_the_sql_predicate_is_the_python_predicate` on BOTH sides of the claim: the exclusion set is BUILT FROM `_BILLING_ACTIVE_STATUSES` / `_PENDING_PAYMENT_STATUS` (a constant change in either direction reddens it), and the NEGATIVE space is pinned too — the gate may reference only the columns the Python twin reads, and its WHERE clause is a golden text — so an ADDED condition (`o.tier`, `o.deleted_at`, `m.status <> 'deleted'`) reddens rather than silently changing what counts as free. Any future edit must move both. |
| `tortoise/hosted_api.py` `_create_org_supabase_lane` | Maps the RPC marker onto the same structured 402 and reclaims the eagerly materialized graph namespace — **gated on attribution** (`_rpc_failure_proves_nothing_landed`): only a server REJECTION (the 402 marker, or a 4xx) proves nothing committed, so a transport failure **or a 5xx** (a gateway 502/504 can be generated after a COMMIT) never `GRAPH.DELETE`s a live org's namespace. The drop is bounded and cannot fail the refusal. |
| `tortoise/hosted_api.py` `_create_onboarding_org_lane` | Maps the marker onto the same 402 instead of its generic 400. Passes `p_user_id=owner_user_id` and `p_tier='free'`, so it is a user-path lane and is pinned as one. |
| `tortoise/hosted_api.py` `_provision_new_org_from_checkout` | ⛔ **NOT "unaffected by construction"** — that was an assumption, not an invariant. It now REFUSES a free or absent tier loudly: defaulting a missing tier to `'free'` made the gate engage and refuse a provision the customer had already paid for, which the webhook re-raises as a 500 Stripe redelivers forever. The session-creation route rejects `free`/`anon` before checkout, so reaching here with one means the metadata was lost. |
| `/v1/register`, `/v1/agent/signup` (`provision_org`, `provision_org_with_token`) | The entitlement gate does not fire (the identity path passes `p_user_id = NULL`). The namespace reclaim in `register_user` is **gated on attribution AND scoped to the provision step** — it used to delete on ANY exception, and it used to run the gate against the exception of a POST-provision step (`set_dashboard_key_login`), so a live account whose login flag failed lost its graph. It also runs through the shared `_drop_org_graph` off the event loop now. |
| `tortoise/hosted_api.py` `_create_org_registry_lane` | **Unchanged, and the window is NOT closed here.** Registry mode writes into the graph, where Postgres constraints do not exist, so #1954's read-then-write persists on this lane under N workers / N replicas (the lane's own docstring says the in-process lock "is not bulletproof" there). A graph-level backstop is its own change — filed as **#11603**, recorded as a residual in the migration header. |
| claim lanes (`claim_membership` RPC) | **Unchanged** — the other writer that can create an owned free org. Recorded as a residual in the migration header; it is a distinct RPC and a distinct derivation site. Filed as **#11579**. |

## 5. Implementation steps (all landed in this PR)

1. Migration `20261108000001_one_free_org_atomic_provision.sql` — advisory lock + in-transaction
   re-check inside `provision_team`, free-tier + user-path only; signature unchanged so
   `CREATE OR REPLACE` preserves the ACL. → `supabase/tests/0010_provisioning_rpcs.sql` §8.
2. `_ONE_FREE_ORG_RPC_MARKER` + `_one_free_org_blocker_from_rpc` in `hosted_api`. →
   `test_marker_absent_yields_no_blocker`, `test_the_marker_is_the_one_the_migration_raises`.
3. Create-org lane mapping (+ graph drop, **gated on attribution** — see §4). → the 402 and
   graph-drop tests, in BOTH directions (`test_a_duplicate_name_409_drops_the_graph`,
   `test_an_ambiguous_rpc_failure_does_not_drop_the_graph`).
4. Onboarding lane mapping. → `test_onboarding_lane_maps_the_refusal_to_the_same_402`.

## 6. Review Scope Contract

**Class:** gate-enforcement (a correctness guard over a write path)

**Risk surface** — what this change can break, each with the check that would catch it:

- **R1** the backstop refuses a create the product rule ALLOWS (fails closed on legitimate use) →
  caught by the SQL §8 matrix: paid tier, identity path, self-replay, collaborator-only,
  paid/`pending_payment`/`past_due`/`trialing` orgs, and the `canceled` acceptance all assert
  **allowed**; predicate parity with `owned_free_org_ids` is asserted by
  `test_the_sql_predicate_is_the_python_predicate` (a separate test, because the SQL §8 matrix
  pins today's statuses and cannot see a change to the Python constants).
- **R2** the refusal reaches a client as a 500/400 instead of the structured 402 → caught by
  `TestAtomicProvisionBackstop` on both Supabase lanes.
- **R3** a non-entitlement RPC failure is laundered into a 402 (telling an uncapped user they are
  capped) → caught by `test_unrelated_rpc_failure_is_not_laundered_into_a_402`.
- **R4** SQL and Python drift apart on the marker → caught by
  `test_the_marker_is_the_one_the_migration_raises`.
- **R5** the refusal strands a materialized graph namespace → caught by
  `test_rpc_refusal_drops_the_eagerly_materialized_graph`. Its mirror pins the destructive direction,
  which is the more expensive mistake: `test_an_ambiguous_rpc_failure_does_not_drop_the_graph`
  (5xx/transport ⇒ no drop), `test_a_post_provision_failure_does_not_reclaim_the_committed_org`
  (a post-provision step's failure is not evidence about the provision), and
  `test_only_a_server_REJECTION_proves_nothing_landed` (the predicate's whole truth table).
- **R6** the ACL is lost because the function was redefined → `CREATE OR REPLACE` with an unchanged
  signature preserves grants (no `DROP FUNCTION`); the PGlite harness runs the ACL/RLS section of
  the 0010 suite against the new body.

**Threat classes** (gate-enforcement):

- `T1` a *paid* provision being refused (the #2789 exit closed by accident) → §8c asserts the paid
  exit lands.
- `T2` the marker matching a non-entitlement failure → non-vacuity test above.
- `T3` the lock not taken by a second writer (`claim_membership`) → **out of scope here**, stated in
  the migration header and filed (#11579); it is not a bypass of this diff, it is a different writer.
- `T4` the backstop being read as covering the **registry/graph** lane, which it cannot → **out of
  scope here**, stated in the migration header and filed (#11603). That lane keeps #1954's window
  under N workers, and closing it needs a graph-level mechanism, not this one.

**Out of scope — must NOT be raised:** the `claim_membership` multi-worker window (different RPC,
its own derivation site — #11579), whether the registry/graph lane keeps #1954's window (it does:
no Postgres there to constrain it, so it is **not** closed by this PR and is filed as #11603 —
do not re-derive it here, and do not fix it here), and whether the
`create A → buy B → cancel B` hole should be closed (a settled product decision, #2789 §2).

**Acceptance:** every R<n> has the named evidence green at the pushed head; `T3` is recorded, not
chased.
