---
title: "fix(abuse): the Supabase abuse store's PostgREST order dialect (#4037) — Implementation Plan"
type: engineering
domain: platform
status: live
created: 2026-09-25
updated: 2026-09-25
ownedBy: organisation-design-team
subjects:
  team: organisation-design-team
doc_status: live
aboutSubjects: abuse-enforcement, test-double-fidelity
aboutObjects: tortoise/abuse.py, tests/fake_control_plane.py
---

<!-- research-path: issue #4037 scoping comment (Phase 1.5 external research) -->
<!-- design-note: the issue body's prescribed fix survived re-derivation (Approach A) -->

# fix(abuse): the Supabase abuse store's PostgREST order dialect — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the production abuse store transmit PostgREST's real `order`
grammar, and make `FakeControlPlane` stop accepting a private `-col` dialect —
so flagged orgs actually reach Stage 2 suspension, `/v1/team/alerts` is not
permanently empty, and neither can silently regress again.

**Team:** organisation-design-team
**Issue:** #4037 · **Tier: Standard** (`task-workflow-standard`)

**Architecture:** one seam, two halves. The three production call sites move to
the wire form that already works elsewhere in this repo
(`tortoise/hosted_api.py:12474` sends `order="created_at.desc"`). The test double
stops being an *inverted* emulator — today it accepts exactly the form PostgREST
rejects (`-col`) and silently no-ops exactly the form PostgREST accepts
(`col.asc`/`col.desc`, because it looks up a column literally named
`"created_at.desc"`) — and becomes an **independent** grammar oracle that raises
the same `RuntimeError(... HTTP 400)` surface the real client produces. Nothing
in `tortoise/supabase_control.py` changes: an in-process seam guard does not
change the production symptom (all three consumers catch bare `Exception`) and
would be a third grammar implementation that can false-refuse a term PostgREST
accepts.

### Pattern Research

> **Findings date:** 2026-09-25

**Library docs (preflight):** no third-party dependency is touched. The change
is a caller-supplied query-string literal plus a test double. No new import in
production; the parser is in test-land.

**Canonical grammar (external, verified against the PostgREST parser source):**
`?order=` takes a comma-separated list of
`field[.asc|.desc][.nullsfirst|.nullslast]`. `pFieldName = pQuotedValue <|>
sepByDash pIdentifier` — a leading `-` never parses (a dash is legal only
*between* identifier segments, or inside a `->`/`->>` JSON-path key); direction
precedes nulls. An invalid term raises `QueryParamError` → `PGRST100`, HTTP 400
(`src/library/PostgREST/ApiRequest/QueryParams.hs`, `Error.hs`). Independent
client confirmation: `postgrest-js` `PostgrestTransformBuilder.order()` emits
`` `${column}.${asc?'asc':'desc'}…` `` with no `-` prefix.

**Pitfall framing (external):** a hand-written test double that models a
*private* dialect is the classic mock-drift failure — it can only confirm, never
falsify (Wathan, *Preventing API Drift with Contract Tests*). The mitigation
used here is the one already established in this file for #1719:
`_assert_uuid_fidelity` (`tests/fake_control_plane.py:38`) makes the double raise
the real `RuntimeError(... HTTP 400)` surface, default-on.

---

## Tasks

### Task 1 — Move the three production call sites to the wire form

**Behaviour:** `latest_flag_at`'s newest-flag read, newest-clear read, and
`recent_alerts` must each transmit `created_at.desc`.

- `tortoise/abuse.py:344` (`SupabaseAbuseStore.latest_flag_at`, newest flag) —
  `order="-created_at"` → `order="created_at.desc"`
- `tortoise/abuse.py:354` (`SupabaseAbuseStore.latest_flag_at`, newest clear) —
  same
- `tortoise/abuse.py:445` (`SupabaseAbuseStore.recent_alerts`) — same

Nothing else in `tortoise/abuse.py` changes. `MemoryAbuseStore` sorts in Python
and is already correct — this is a hosted-only defect.

### Task 2 — Replace the fake's `-col` dialect with a PostgREST order parser

**Behaviour:** `FakeControlPlane` accepts exactly
`field[.asc|.desc][.nullsfirst|.nullslast]`, comma-separated; anything else
raises `RuntimeError("Supabase control-plane query failed ({table}): HTTP 400")`
— the same surface the real client produces for the PostgREST `PGRST100`.

- Insert two module-level helpers in `tests/fake_control_plane.py` directly after
  `_assert_uuid_fidelity` (i.e. after line 57):
  - `_parse_order(table, order) -> list[tuple[str, bool, bool | None]]` returning
    `(field, descending, nulls_first_or_None)` per term.
  - `_apply_order(rows, terms) -> list[dict]`: apply the least-significant term
    first, each a **stable** per-term sort, so a multi-term list behaves as
    PostgREST's multi-key ordering.
- Parse **before** method dispatch, next to the `_assert_uuid_fidelity` call:
  `PGRST100` is method-agnostic, exactly as 22P02 is. A falsy `order`
  (`None` or `""`) yields no terms — the real seam guards `if order:`, so
  raising on it would be a false refusal the wire never sees.
- Replace the GET-branch sort with `_apply_order`, **before** the `select`
  projection: PostgREST orders server-side before projecting, so an ordered
  column need not appear in `select`. (The old fake sorted *after* projection,
  silently no-oping `active_membership_org_ids` — `select=["org_id"]`,
  `order="created_at.asc"` — so #2001's "earliest org wins" contract was
  unverified against the double. Fixed here, since it is the same
  fake-does-not-mirror-PostgREST class and the move is two lines.)

**Deliberate design decisions (each recorded because a reader will otherwise
treat it as an oversight):**

1. **The parser is an independent oracle.** It lives in test-land and must never
   import a production order helper: a shared implementation shares its blind
   spot, which is the exact failure this issue fixes. The two halves are held in
   agreement by a test that feeds the *captured wire string* into the fake.
2. **Exception type is `RuntimeError(... HTTP 400)`, not `ValueError`.** The real
   client has no in-process order rejection — it forwards verbatim and
   PostgREST 400s — so `RuntimeError` is the faithful mirror of the real
   client's observable behaviour for `-col`. This is the `_assert_uuid_fidelity`
   precedent verbatim.
3. **Default NULL placement follows Postgres** (`asc` → NULLS LAST, `desc` →
   NULLS FIRST), overridable by an explicit `nullsfirst`/`nullslast` token.
   The old key (`value or ""`) was the **inverse** in both directions, so a
   fake-backed test could pass with production's order wrong — the same class
   of dialect divergence this issue exists to remove. Adopted after an
   empirical check: the order-touching suites (524 tests, including every
   fake-backed consumer of the nullable ordered columns `graphs.deleted_at`
   and `org_memberships.created_at`) stay green with the Postgres default, so
   the divergence was removable rather than merely documentable.
4. **The accepted grammar is a stated subset.** JSON-path (`col->>key`) and
   embedded-resource ordering raise loudly rather than being silently accepted;
   no call site uses them and the fake's rows are flat dicts.
5. **Multi-term lists are parsed and honoured**, not false-refused: PostgREST
   accepts them and "honour only the first term" is the defect shape (with
   `limit=1` it silently returns the wrong row).

### Task 3 — Tests

**`tests/test_fake_control_plane.py`** (the #1719 fidelity-lock file — its own
docstring frame is "CI green while prod 500s"):

- `test_order_legacy_dash_prefix_raises` — `order="-created_at"` →
  `RuntimeError` matching `HTTP 400`. **The sole witness** for the double: under
  the old dialect every integration test stays green, so without this assertion
  the double's regression is invisible.
- `test_order_dot_desc_sorts_newest_first` — `created_at.desc` really orders
  (the old fake silently no-op'd it).
- `test_order_bare_column_and_dot_asc_ascend` — `col` and `col.asc` agree.
- `test_order_null_placement_follows_postgres_defaults` — the explicit tokens are
  honoured, not ignored, and the bare `asc`/`desc` defaults follow Postgres
  (NULLs last on `asc`, first on `desc`).
- `test_order_multi_term_is_stable` — `a.desc,b.asc` resolves `a`-ties by `b`.
- `test_order_unparseable_term_raises` (parametrized with all six:
  `"-x"`, `"a b"`, `"a.desc.desc"`, `"a.asc.nullslast.desc"`, `"a..b"`,
  `"a.desc."`) — any permissive fallback reds.
  An **empty/absent** order is NOT in this list: it is deliberately accepted,
  because the real seam guards with `if order:` — pinned separately by
  `test_falsy_order_is_accepted_like_the_real_seam`.
- `test_order_by_a_missing_column_is_refused_like_select_and_filter` — ordering
  by an absent column is the same PostgREST 400 as the `select`/`filter` drift
  (#1001/#302), so the fake is not the one place that rejection stays invisible.
- `test_order_validation_is_method_agnostic` — PATCH with `-created_at` raises.
- `test_every_order_form_used_in_the_repo_is_accepted` — the four spellings the
  repo actually transmits (`created_at`, `created_at.asc`, `created_at.desc`,
  `deleted_at`) must all parse (false-refusal guard, explicit list rather than a
  brittle source scan).

**`tests/test_supabase_control.py::TestRealQueryParamEncoding`** (the existing
capturing-client harness, `:1497` — it has zero `order` assertions today, which
is why this class of bug had no wire-level test):

- `test_order_is_transmitted_verbatim` — `query(order="created_at.desc")` ⇒
  `seen["params"]["order"] == "created_at.desc"`.
- `test_legacy_dash_order_is_not_translated` — the seam forwards `-created_at`
  verbatim; makes the "no seam guard" decision executable rather than implicit.

**`tests/test_abuse.py`** (new class `TestAbuseOrderParam`, beside
`TestFakeTrigger` — the class that owns `test_supabase_store_over_fake`):

- `test_abuse_store_sends_the_postgrest_order_dialect` — capture the `order`
  kwarg at the store's three sites; assert each is exactly `created_at.desc`
  and that none starts with `-`. **Reds per-site** when any site is reverted.
- `test_captured_abuse_order_is_accepted_by_the_fake` — feed each captured
  string into a seeded `FakeControlPlane`; assert it does not raise **and**
  returns newest-first. This is what pins the double to the real client's wire
  form.

### Task 4 — Verification and recording

- Run the mutation proof table (below) and record the outcome in the PR body.
- Record the residuals; file the follow-up issues (Task 5).
- No `tortoise/supabase_control.py`, `tool_registry.py`, `sdk.py`, or
  `config/surface-manifest.yml` change → no MCP/SDK surface approval required.

### Task 5 — Follow-ups (file, do not absorb)

- **Fail-soft diagnostics:** `AbuseEngine._evaluate`'s `latest_flag_at` swallow
  (`tortoise/abuse.py:536-539`) sets `flagged_at = None` **with no log at all**,
  contradicting the module's documented "failures are logged and swallowed"
  contract; `/v1/team/alerts` (`hosted_api.py:7030-7033`) likewise swallows
  without a log. The error-blind seam cannot distinguish a deterministic HTTP
  400 (a bug) from a transient transport failure — which is why this defect
  stayed invisible for the interval between `4e2b9f4bb` (#983) and discovery.
- **Fake order-fidelity residual:** the fake ignores `order` on PATCH/DELETE
  (PostgREST supports `order`+`limit` on DELETE). No call site does this today.
  (The sort-after-projection gap previously listed here is **fixed in this
  PR** — see Task 2.)

---

## Testing Strategy

| Surface | Layer | Tests |
|---|---|---|
| `SupabaseAbuseStore` → `SupabaseControlPlane.query` wire param | capturing client (real `SupabaseControlPlane`, stubbed `_http`) | `test_order_is_transmitted_verbatim`, `test_legacy_dash_order_is_not_translated` |
| `SupabaseAbuseStore` call sites | capturing CP over the store | `test_abuse_store_sends_the_postgrest_order_dialect` |
| Fake dialect ↔ real wire form | cross-check (captured string → fake) | `test_captured_abuse_order_is_accepted_by_the_fake` |
| Fake grammar + semantics | unit (`tests/test_fake_control_plane.py`) | the 8 order cases above |
| Enforcement outcome (suspension) | integration (existing) | `tests/test_abuse_integration.py::TestPointBurst::test_boundary_crossing_suspends_and_403` |
| `/v1/team/alerts` | integration (existing) | `tests/test_abuse_integration.py::TestMintGateAndAlerts::test_alerts_endpoint_session_authed` |

## Verification Plan — mutation proof table

| # | Mutation | Expected | Witness |
|---|---|---|---|
| 1 | `abuse.py:344` → `-created_at` | **RED** | `test_abuse_store_sends_the_postgrest_order_dialect`; `test_boundary_crossing_suspends_and_403` |
| 2 | `abuse.py:354` → `-created_at` | **RED** | `test_abuse_store_sends_the_postgrest_order_dialect`; `test_boundary_crossing_suspends_and_403` |
| 3 | `abuse.py:445` → `-created_at` | **RED** | `test_alerts_endpoint_session_authed`; `test_per_key_velocity_notifies_once` |
| 4 | fake parser reverted to `lstrip("-")`/`startswith("-")` | **RED** | `test_order_legacy_dash_prefix_raises` + `test_order_dot_desc_sorts_newest_first` |
| 5 | production transmits `created_at.desc` | **GREEN** | `test_order_is_transmitted_verbatim` |
| 6 | unparseable term given to the fake | **RAISES** | `test_order_unparseable_term_raises` |
| 7 | every in-repo order spelling | **GREEN** | `test_every_order_form_used_in_the_repo_is_accepted` |
| 8 | seam mutated to translate/strip `-col` | **RED** | `test_legacy_dash_order_is_not_translated` |

## Acceptance Criteria

1. The three `tortoise/abuse.py` sites are `order="created_at.desc"`;
   `grep -rn '"-created_at"' tortoise/` returns nothing.
2. The fake accepts the PostgREST term grammar and raises
   `RuntimeError(... HTTP 400)` on anything else, for every method.
3. The capturing-client tests pin the transmitted `order`; a cross-check pins
   the fake's semantics to that wire string.
4. Mutation rows 1–8 hold exactly.
5. Diff is confined to `tortoise/abuse.py` (3 lines),
   `tests/fake_control_plane.py`, `tests/test_fake_control_plane.py`,
   `tests/test_supabase_control.py` (2 cases), `tests/test_abuse.py` (1 class).
   No signature, tool-registry, SDK, or manifest change.
6. No existing test regresses.
