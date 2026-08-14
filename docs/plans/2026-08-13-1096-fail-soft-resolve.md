---
title: "Implementation Plan #1096 — Fail-soft resolve_api_key when additive teams columns missing"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-hosted
aboutObjects: auth, schema-drift, fail-soft, resolve-api-key
created: 2026-08-13
---

<!-- research-path: standalone (issue #1096 — scoping comment; pattern from #1001 systemic-fix section) -->

# #1096 — Fail-soft resolve_api_key when additive teams columns missing

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** `resolve_api_key` (and the other teams reads in the auth seam) must survive a schema that is missing the additive `suspended_at`/`flagged_at` (migration 0015) and `deleted_at`/`grace_hours` (20260813000001) columns — a missing additive column must degrade to safe defaults (un-suspended/un-flagged), never take down all auth across REST + MCP. (Defense-in-depth behind the #1095 deploy gate; accepted-risk boundary: degradation-duration fail-open on suspension enforcement — mint grant pinned, invite flows 500→working and `claim_status` `{"claimable": False}`→real determination, both policy-unchanged.)

**Team:** epistemic-team
**Role:** (session)

**Architecture:** The teams read keeps its SINGLE round-trip in the healthy path (combined select = base + additive) and degrades only on failure: when the query raises (a missing additive column → PostgREST 400, body discarded by the error-blind seam), retry once with the drift-safe base set (migration 0006 columns only for `resolve_api_key`; `team_by_id`'s retry intentionally retains the #302 `deleted_at`/`grace_hours` columns so a deletion-drifted schema fails closed) and pad the additive fields to `None` (un-suspended / un-flagged safe defaults, pre-0015 behavior). **Only suspension/staging state (0015 `suspended_at`/`flagged_at`) fails soft.** The #302 soft-delete columns (`deleted_at`/`grace_hours`, 20260813000001) stay fail-closed in `team_by_id`: a deletion kill-switch must fail the guard closed, never open it (a schema missing them cannot have soft-deleted rows, and the write path is equally drifted — so fail-closed is both safe and correct there). The base-only retry remains fail-closed (its failure propagates — a broken teams table must never authenticate). The degrade is logged at WARNING so drift stays diagnosable (#1001 post-mortem: "schema drift is undiagnosable"). Enforcement semantics are untouched whenever the additive columns ARE readable (REST 403 / MCP -32006 still fire via `suspended_at` in the returned dict). This is defense-in-depth: the deploy gate (#1095) is the primary fix; this keeps auth resilient if drift still ships.

**Accepted risk (explicit):** when the 0015 additive columns are unreadable for any reason — drift, or a transient base-ok/additive-fail error — a durably-suspended team degrades to un-suspended for the **entire degradation duration**, and on MCP up to **+60s after recovery** (TeamResolutionMiddleware's resolution TTL — REST resolves fresh per request and 403s immediately; the drift-degrade sets no suspension signal, so the MCP cache holds the un-suspended entry until TTL). Worse, the degrade **actively clears the in-process suspension signal**: both enforcement seams self-heal a fresh un-suspended resolution (`if is_suspended_signal(team_id): clear_suspended(team_id)`), so the first drifted request tears down the one local enforcement cell that still works, for the whole degradation duration (the scoping verifier's multi-replica cell concern — its closure is the deferred `revoked_at` stamping, item ④). During the window, session-key mint (hosted_api ~5425, `suspended_at` 403 gate) is **newly permitted** for a durably-suspended team (a capability grant, not just continued auth); the invite flows (mint + accept) restore from fail-closed 500 to **working** — policy-unchanged (invites were never suspension-gated in any mode; only the drift 500 blocked them) and consistent with this slice's fail-soft intent, so not pinned separately; and the #1148 key-login gate (`_check_dashboard_key_login`, management endpoints create_api_key/revoke_api_key/backups_restore/billing via `get_current_team_session`) degrades to the safe default ALLOWED under drift — `resolve_api_key` normalizes the additive `dashboard_key_login` None-pad to True (the column is NOT NULL DEFAULT true; a schema missing 20260813000005 behaves as pre-#1148, where key-login was always allowed). Note: under additive drift the STORED False is NOT readable (the additive set is excluded from the base retry — the degrade loses all additive state), so a team that disabled key-login has the gate re-opened for the degradation duration — the same fail-open class as the suspension degrade, accepted-by-scope. Pinned at the seam level by `test_resolve_api_key_additive_columns_missing_fail_soft` (True assertion), `test_resolve_api_key_dashboard_key_login_only_drift`, and `test_resolve_api_key_stored_false_drift_fail_open` (False→True documented). The SESSION branch (`_session_user_team`) routes through the same fail-soft seam (degrade, never 500). (Merge note: `dashboard_key_login` was added to `_TEAM_ADDITIVE_SELECT` during the post-plan rebase with main, which shipped #1148; this decision extends the plan's fail-soft principle to the new additive class.)

Likewise `claim_status` (~5210, claimability probe) restores from fail-closed `{"claimable": False}` to a real determination under drift — policy-unchanged (no suspension gate; the claim RPC is drift-independent) and read-only, so not pinned; and `team_info`'s status chip (GET /v1/team, ~1945) silently flips `"flagged"`→`"active"` for the degradation window — display-only, the same fail-soft direction as the un-suspended default, so not pinned. **`claim_team` (POST /v1/claim) is the one durable-write opening: it is suspension-gated via the same `_get_current_team_supabase` auth dependency, so under 0015 drift a durably-suspended ANON team can execute the claim (`claim_membership` RPC — permanent owner-identity link + `teams.email` overwrite, NOT undone by drift recovery) — pinned by `test_claim_allowed_under_0015_drift_then_blocked_after_recovery` (Task 4 Step 5).** This fail-open window is accepted-by-scope; see the `Deferred:` block below for the closure that would eliminate it and why it is not in this slice. The #302 deletion kill-switch is NOT part of this window: the 410 fires in healthy and 0015-drift modes; under 20260813000001 drift `team_by_id` raises and the consumer returns 500 (fail-closed — the guard never opens, never 2xx).

### Pattern Research

> **Findings date:** 2026-08-13

**Library docs (preflight)** — no third-party deps in plan — skipped.

**Library version & API surface** — skipped: plan touches zero third-party libraries. Only in-repo seam (httpx PostgREST client, already used 2+ places) and in-repo FakeControlPlane.

**Idiomatic usage patterns** — skipped: follows the existing in-repo best-effort pattern (`update_last_used` try/except in `tortoise/supabase_control.py`, `derived_tier` fail-open in `tortoise/quota.py:137`, `_registry_abuse_write`, `abuse._team_email` in `tortoise/abuse.py`).

**Library/framework pitfalls** — skipped: no third-party dep; the PostgREST seam behavior is pinned by the fake mirroring the seam's HTTP 400 (the #1001 failure shape; the body code is per the PostgREST error reference v12 — PGRST204, "column specified in the `columns` query parameter is not found" — but is NOT observable in-process: the seam raises RuntimeError and **discards the error body**, so a fail-soft predicate cannot discriminate error codes at this layer — documented decision below).

> Gate skipped: plan touches zero third-party deps (research-protocol skip rule).

### Integration Surface Map

| Surface | Layer | Notes |
|---|---|---|
| `resolve_api_key` step 3 → teams combined select | unit (FakeControlPlane) | Healthy path ONE round-trip (2 queries/auth total). Failure → base-only retry → fail-soft. Base-only retry failure → RuntimeError propagates (fail-closed, correct). Direct consumers: `_get_current_team_supabase` (~1049, resolve at ~1062 — indexes `team_id`/`tier` directly, reads `suspended_at` via `.get()` for the 403 gate) feeding REST `get_current_team` dispatcher (~910); `_session_user_team` (~1110, session-auth management branch — teams read routed through the fail-soft seam post-test-review, degrades never-500) + `team_info` (GET /v1/team, ~1921 — reads `flagged_at` via `.get()` for the status chip, display-only), `claim_team` (POST /v1/claim, ~5128/5177 — **suspension-gated WRITE** via the same auth dependency; under 0015 drift the gate opens and a durably-suspended anon team can claim (permanent identity link + `teams.email` overwrite, survives drift recovery) — pinned, see Accepted risk), MCP `TeamResolutionMiddleware` (mcp_auth ~166 — reads via `.get()`), `claim_status` (~5210 — indexes `team_id` directly; read-only probe; under 0015 drift it restores from fail-closed `{"claimable": False}` to a real determination, policy-unchanged). The always-present contract keys (`team_id`/`tier`/`key_id`) are never affected by the fail-soft — only the additive fields pad to `None`. |
| `resolve_api_key` degrade path | unit (FakeControlPlane drift mode) | New `_teams_row_fail_soft` helper; degraded ⇒ `suspended_at`/`flagged_at`/`email` keys present with `None` + WARNING log. |
| `team_by_id` → teams select | unit | Same combined-with-fallback; **fail-soft additive set = 0015 `suspended_at`/`flagged_at` only** — the #302 `deleted_at`/`grace_hours` stay in the base select so the drift-safe retry fails CLOSED on 20260813000001 drift (deletion kill-switch never opens). |
| REST enforcement (hosted_api 403 SUSPENDED) | integration (existing `test_abuse_integration.py`) | Reads `suspended_at` from resolved dict via `.get()` — unchanged; no edits needed. |
| MCP enforcement (`mcp_auth.py` -32006) | integration (existing) | Reads `suspended_at` from resolved dict via `.get()` — unchanged; no edits needed. |
| FakeControlPlane | test infra | New `missing_columns` drift mode mirrors the real seam (raises RuntimeError on select of a drifted column, body discarded — same as production). |
| Remaining `teams` reads — **explicit verdicts** | — | `abuse.SupabaseAbuseStore._team_field` (`suspended_at`/`flagged_at`/`email` selects): no live call sites for `team_suspended`/`team_flagged_at`; `team_email` callers go through the already best-effort `_team_email` wrapper (abuse.py:132) → **out of scope**, unchanged. `hosted_api._iter_registered_teams` (~line 141, retention-sweep enumerator) filters `deleted_at IS NULL`: under 20260813000001 drift the query raises but the function's `except Exception: return []` swallows it → **silently skips all teams** (no log — a genuine #1001 diagnosability gap); out of scope (not auth), flagged for the escalation decomposition. `hosted_api._purge_deleted_teams` (~line 4731, the actual #302 hard-purge sweep) selects `grace_hours`/`deleted_at` + filters `deleted_at lte cutoff`: under 20260813000001 drift its query raises → outer `except` logs WARNING `"deleted-team purge sweep failed"` and aborts the sweep (diagnosable, fail-closed-ish maintenance no-op); out of scope (not auth), unchanged, flagged for the escalation decomposition. `team_by_id` consumers: `invitation_accept` (supabase_control.py:713 — reads `deleted_at` → 410 kill-switch; works under 0015 drift, 500 under 20260813000001 drift via the shared seam; pinned by `test_invitation_accept_410_under_0015_drift`); `export_team`/`delete_team`/`list_my_teams`/`create_graph`/`list_graphs` route via `_team_node` (~3723 — Supabase mode → `team_by_id`, registry mode → FalkorDB Team node); session-key mint (~5425) and `invite_to_team` (~4046) call `team_by_id` directly (Supabase) or read `tier`/`suspended_at` via their own Cypher (registry). Mint: `suspended_at` 403 gate reads None under 0015 drift → mint newly permitted during the window (see Accepted risk; pinned); 500 under 20260813000001 drift via the shared seam — fail-closed, no key minted. `export_team` (~4494) — the 410 consumer (410 preserved healthy+0015-drift, 500 under 20260813000001 drift); `delete_team` (~4608) — reads `deleted_at` but answers an already-scheduled delete with an idempotent 200 replay (no 410; unchanged under 0015 drift — `deleted_at` survives the base retry, pinned by `test_team_by_id_deleted_at_survives_0015_drift`; 500 under 20260813000001 drift); `list_my_teams` (~3786) — reads name/tier only, never `deleted_at`, no deletion gate in any mode (under 0015 drift restores 500→working, policy-unchanged — same class as the invite flows; 500 under 20260813000001 drift — pre-existing behavior, unchanged by this slice); `create_graph` (~3928) — tier/limits gate via `_team_limits_from_node` (under 0015 drift restores 500→working, policy-unchanged; unchanged under 20260813000001 drift — 500, fail-closed via the shared seam) / `list_graphs` (~3948) — existence/404 check only, no tier/limits gate (drift behavior identical to create_graph); `invite_to_team` — tier gate only, never suspension-gated (under 0015 drift restores 500→working — tier gate intact, **no new exposure**; 500 under 20260813000001 drift via the shared seam, fail-closed). All other `teams` reads are base-only (0006) selects → **drift-safe**, unchanged: `quota.resolve_team_limits`, `team_email`, `github_credentials`, `team_onboarding_state`, `team_by_name`, `team_by_email`, `graph_metadata`, `backup_sweep.py` (:156/:205), `team_id_for_stripe_customer` (supabase_control.py:1348), `team_tier` read, `hosted_api` health probe (:834). |

### Failure Modes

- Drifted schema (missing 0015 columns — the #1001 case) → **Expected:** combined teams read raises → base-only retry succeeds → team resolves un-suspended/un-flagged (`suspended_at`/`flagged_at` = `None`); auth keeps working; WARNING logged per degrade → **Test:** `test_resolve_api_key_additive_columns_missing_fail_soft` (incl. caplog warning assertion)
- Drifted schema (missing 20260813000005 `dashboard_key_login` — the #1148 additive class) → **Expected:** key-login gate degrades to safe default ALLOWED (`resolve_api_key` normalizes None→True; a team that set False keeps it False); pinned by `test_resolve_api_key_dashboard_key_login_only_drift` → **Test:** `test_resolve_api_key_dashboard_key_login_only_drift` + `test_resolve_api_key_additive_columns_missing_fail_soft` (True assertion)
- Drifted schema (missing 20260813000001 columns — the #302 soft-delete columns) → **Expected:** `team_by_id` FAILS CLOSED (RuntimeError propagates from the base retry — the deletion kill-switch guard must never open; `invitation_accept`/`export_team` 410 semantics preserved) → **Test:** `test_team_by_id_deletion_columns_drift_fails_closed`
- Columns present, team suspended → **Expected:** `suspended_at` carried in resolved dict; REST 403 / MCP -32006 unchanged → **Test:** `test_resolve_api_key_carries_suspension_state` + existing `test_abuse_integration.py` 403/MCP tests
- Base columns missing (table broken / corrupt, distinct from a full outage) → **Expected:** base-only retry also raises → RuntimeError propagates (fail-closed, outcome unchanged), but the failure path now costs a second teams query + a second WARNING per request for the entire degradation (acknowledged trade-off for the single-round-trip healthy path; bounded only by the deferred seam error-discrimination closure, item ①) → **Test:** `test_team_by_id_base_read_fails_closed` + existing resolve fail-closed tests
- Non-drift additive-read failure (transient) → **Expected:** one-request degrade to un-suspended on REST (up to +60s on MCP per the resolution TTL — see Accepted risk; behavior is identical to the drift case) — accepted-by-scope; the WARNING log is the tripwire → **Test:** (documented, not asserted at consumer level; the seam degrade path is pinned by `test_resolve_api_key_additive_columns_missing_fail_soft`)

### Deferred (GOOD > EASY resolution)

`Deferred: fail-soft boundary treats ANY additive-read failure as column absence (fail-open on suspension enforcement for the degradation duration) — Good alternative: (a) seam error discrimination (preserve the PostgREST body / its error code in the raised RuntimeError so only genuine column-absence degrades, re-raise on transport/5xx) + (b) revocation closure (`abuse_suspend` also stamps `api_keys.revoked_at` — a 0007 base column already read fail-closed in resolve step 1 — making suspension enforcement drift-independent). Cost: (a) ripples into the fail-closed contract at the 3 HTTP-409 sites (seam redesign, scoping item ①); (b) changes suspension reversibility (unsuspend restores access only after re-minting keys — a product-behavior decision). Rationale: user selected the issue-scope-only path (Option 1 — the issue body sanctions the un-suspended safe-default trade-off; #1095 deploy gate is the primary fix); both alternatives are tracked in the scoping comment's project-workflow escalation (child items ① ④ ⑤).`

**Tech Stack:** Python 3.12, PostgREST (Supabase service role), pytest, in-repo FakeControlPlane.

---

## Task 1: FakeControlPlane drift mode

**Intent:** Simulate the #1001 schema-drift failure (PostgREST 400 on a select of a column the table doesn't have) so the fail-soft tests exercise the real failure shape — the fake mirrors the seam's behavior of raising RuntimeError with the body detail discarded.

**Acceptance:** `FakeControlPlane(missing_columns={"teams": {"suspended_at"}})` raises `RuntimeError` (message `Supabase control-plane query failed (teams): HTTP 400`) for a GET whose select includes a drifted column, and behaves exactly as today with the default (no drift). All existing tests still pass.

**Files:**
- Modify: `tests/fake_control_plane.py` (constructor + `query` GET branch), `tests/test_supabase_control.py` (new `test_fake_filter_column_drift_raises` pin, Step 3)

**Step 1:** Extend `FakeControlPlane.__init__` to accept `missing_columns: dict[str, set[str]] | None = None` and store it (default `None`).

**Step 2:** In `query()` GET branch (after the `select` projection line), if `self.missing_columns` marks any column in `select` **or any filter column** for `table`, raise (filter-column drift mirrors the real seam: the #302 sweeps filter on `deleted_at`, and PostgREST 400s on a filter of an absent column just as on a select):

```python
if self.missing_columns and table in self.missing_columns:
    drifted = self.missing_columns[table]
    if (select and drifted & set(select)) or (
            filters and drifted & {col for col, _, _ in filters}):
        # Mirrors the #1001 failure: PostgREST HTTP 400 for an absent
        # column (PGRST204 per the error reference); the real seam discards
        # the body, so only HTTP 400 surfaces.
        raise RuntimeError(
            f"Supabase control-plane query failed ({table}): HTTP 400")
```

**Step 3:** Run the existing suite subset + pin the filter-drift branch. **This branch + pin are out-of-slice fake-fidelity scaffolding, not a #1096 auth-seam pin** — no #1096 test filters on a drifted column; they exist so a future escalation child (sweeps/health drift tests) does not silently pass where prod raises (PostgREST genuinely 400s on a filter of an absent column). Placement: `tests/test_supabase_control.py`.
`./.venv/bin/python -m pytest tests/test_supabase_control.py tests/test_abuse.py -q` → all pass.

```python
def test_fake_filter_column_drift_raises():
    """Fake fidelity (scaffolding for the escalation decomposition):
    PostgREST 400s on a FILTER of an absent column just as on a select —
    the #302 sweeps filter deleted_at."""
    fake = FakeControlPlane(
        {"teams": [dict(FREE_TEAM)]},
        missing_columns={"teams": {"deleted_at"}})
    with pytest.raises(RuntimeError):
        fake.query("teams", select=["id"],
                   filters=[("deleted_at", "is", None)])
```

## Task 2: resolve_api_key fail-soft (combined select + base-only fallback)

**Intent:** The auth hot path must not die when 0015's additive columns are absent — and must not pay an extra round-trip in the healthy path. One combined select (base+additive); on failure, retry the drift-safe base set.

**Acceptance:** With drift on `suspended_at`/`flagged_at` (but `email` present — it is a 0006 base column), `resolve_api_key` returns the team dict with `suspended_at`/`flagged_at`/`email` keys present (`None`/real value) and correct identity + quota fields; with no drift, the query count is unchanged (2 queries/auth: api_keys + teams) and behavior is byte-identical to today.

**Files:**
- Modify: `tortoise/supabase_control.py` (`_QUOTA_SELECT` split → `_TEAM_BASE_SELECT` + `_TEAM_ADDITIVE_SELECT`; new `_teams_row_fail_soft`; `resolve_api_key` step 3)

**Step 1 (constants):** Replace `_QUOTA_SELECT` with:

```python
# Base teams columns (migration 0006 — the core teams table; drift-safe).
# email is 0006, NOT 0015 — it rides the base read.
_TEAM_BASE_SELECT = [
    "id", "name", "tier", "max_users", "max_graphs", "graph_size_cap",
    "ops_allowance", "email",
]
# Additive teams columns, separately migrated (#308 / migration 0015).
# A schema one migration behind raises PostgREST 400 on these; the auth
# seam must degrade to safe defaults (un-suspended/un-flagged), never
# take down all auth (#1096, defense-in-depth behind the #1095 deploy gate).
_TEAM_ADDITIVE_SELECT = ["suspended_at", "flagged_at"]

# Combined quota read (primary query) — the healthy path stays ONE round-trip.
_QUOTA_SELECT = _TEAM_BASE_SELECT + _TEAM_ADDITIVE_SELECT
```

**Step 2 (helper):** Add next to `resolve_api_key`:

```python
def _teams_row_fail_soft(cp, team_id: str, *, select: list[str],
                         additive: list[str]) -> dict | None:
    """Teams row with fail-soft additive columns (#1096).

    Primary query selects base+additive (one round-trip). When it raises —
    a missing additive column → PostgREST HTTP 400 (PGRST204 per the
    error reference; the error-blind seam discards the body) — retry with the drift-safe base set and pad the
    additive fields to None (un-suspended/un-flagged, pre-0015 behavior).
    A failure of the base-only retry PROPAGATES (fail-closed: a broken
    teams table, or a missing non-additive column — e.g. team_by_id's
    #302 deleted_at/grace_hours stay in THAT call site's base set — must
    never authenticate or open a kill-switch guard). Logged at WARNING so
    drift stays diagnosable (#1001 post-mortem). Accepted-by-scope: a
    non-drift failure of the combined read degrades suspension enforcement
    for the degrade duration (one auth; up to +60s on MCP) — the closure
    (error discrimination + revoked_at stamping) is deferred to the #1096
    escalation decomposition.
    """
    additive_defaults = {f: None for f in additive}
    try:
        rows = cp.query("teams", select=select, filters=[("id", "eq", team_id)])
    except Exception as e:
        base = [c for c in select if c not in additive_defaults]
        _logger.warning(
            "teams read failed for %s (select=%s) — retrying base-only "
            "select %s; a missing additive column (0015) degrades, a "
            "missing base/deletion column fails closed (%s)",
            team_id, select, base, e)
        try:
            rows = cp.query("teams", select=base, filters=[("id", "eq", team_id)])
        except Exception as e2:
            _logger.warning(
                "teams base-only read failed for %s (select=%s) — "
                "fail-closed (missing base column or control-plane "
                "outage): %s",
                team_id, base, e2)
            raise
    if not rows:
        return None
    return {**additive_defaults, **rows[0]}
```

(Trade-off note: a non-drift teams failure now costs a second query + second WARNING per request — the price of keeping the healthy path at one round-trip; gating the retry to genuine column absence is the deferred seam error-discrimination item ①.)

**Step 3 (resolve step 3):** Replace the teams query block with:

```python
    team_row = _teams_row_fail_soft(
        cp, team_id, select=_QUOTA_SELECT, additive=_TEAM_ADDITIVE_SELECT)
    if team_row is None:
        # Key's team vanished — fail closed (401), never authenticate.
        return None
```

**Step 3b (docstring contracts):** Update `resolve_api_key`'s docstring — the current "Fail-closed: a control-plane error raises (RuntimeError) — it never returns None (401)" is now stale. New contract text:

```
    Fail-closed: a control-plane error raises (RuntimeError) — it never
    returns None (401) and never falls back to the registry. EXCEPTION
    (#1096): a failure of the additive teams read (0015 suspended_at/
    flagged_at — separately-migrated columns) degrades to safe defaults
    (un-suspended/un-flagged) and is logged at WARNING; the drift-safe
    base read still raises on failure (a broken teams table never
    authenticates).
```

Also qualify the seam-level fail-closed docstrings that the new behavior contradicts:
- Module docstring of `tortoise/supabase_control.py` (the "Fail-closed contract (backup-seam P1-3 pattern): every query error raises ``RuntimeError`` — auth never falls back to the registry and never authenticates on error. ``update_last_used`` is the one best-effort exception" paragraph) → append the #1096 exception: additive-teams-read failures (0015) degrade to safe defaults at WARNING; base/deletion reads still raise.
- `_get_current_team_supabase` (hosted_api.py ~1056): "never 200" claim → append "EXCEPTION (#1096): an additive-teams-read failure (0015) degrades to a 200 with safe defaults (un-suspended/un-flagged), logged at WARNING".
- `SupabaseControlPlane` class docstring: qualify the fail-closed sentence as **seam-level** behavior (the resolve caller may intentionally swallow an additive-read failure per #1096).

All are docstring-only edits.

(The dict contract is unchanged: `suspended_at`/`flagged_at`/`email` keys are always present — `None` when degraded. Healthy-path query count stays 2/auth — `test_session_key_resolves_via_api_keys`'s `query_count == 2` assertion at line 132 does NOT change.)

**Step 4:** Run `./.venv/bin/python -m pytest tests/test_supabase_control.py -q` → green (no existing test changes).

## Task 3: team_by_id fail-soft (suspension columns only)

**Intent:** `team_by_id` survives 0015 drift (the auth-relevant additive set) while keeping the #302 deletion kill-switch fail-closed: only suspension/staging state fails soft; `deleted_at`/`grace_hours` stay in the critical path so a drifted 20260813000001 schema fails the delete-sensitive paths CLOSED (500), never opening the guard. The `deleted_at` consumers: `invitation_accept` (410) and `export_team` (410 via `_team_node` → `team_by_id` in Supabase mode) are pinned — `invitation_accept` at the consumer level, `export_team`'s shared seam at the `team_by_id` level; `delete_team` reads `deleted_at` but replays idempotently (200), and `list_my_teams` never reads it — both 500 under 20260813000001 drift via the shared seam.

**Acceptance:** With 0015 drift (`suspended_at`/`flagged_at` missing), `team_by_id` returns the team with those keys `None`; with 20260813000001 drift it raises RuntimeError (fail-closed); no drift → identical to today.

**Files:**
- Modify: `tortoise/supabase_control.py` (`team_by_id`)

**Step 1:** Rewrite `team_by_id` (additive set = 0015 columns only; `deleted_at`/`grace_hours` stay in the select so the base retry keeps them — their absence fails the retry closed):

```python
def team_by_id(cp, team_id: str) -> dict | None:
    """Team row (registry-properties-shaped dict) or None.

    Suspension/staging columns (0015) read fail-soft (#1096): a schema
    missing them returns the row with safe None defaults, never raises.
    The #302 soft-delete columns (20260813000001 deleted_at/grace_hours)
    are NOT additive-fail-soft: the deletion kill-switch guard must fail
    closed, never open (a schema missing them cannot have soft-deleted
    rows — the write path is equally drifted — so fail-closed is safe).
    """
    return _teams_row_fail_soft(
        cp, team_id,
        select=["id", "name", "tier", "email", "graph_name", "max_users",
                "max_teams", "max_graphs", "ops_allowance", "graph_size_cap",
                "backup_enabled", "backup_latest_at", "backup_restored_at",
                "created_at", "deleted_at", "grace_hours"]
            + _TEAM_ADDITIVE_SELECT,
        additive=_TEAM_ADDITIVE_SELECT)
```

**Step 2:** Run `./.venv/bin/python -m pytest tests/test_supabase_control.py -q` → green.

## Task 4: drift + enforcement + fail-closed tests

**Intent:** Pin the O/I/T targets — (1) missing additive columns ⇒ team resolves (not RuntimeError); (2) columns present ⇒ behavior unchanged (suspension state carried, enforcement intact); plus the two contract boundaries the degrade introduces: base-read fail-closed and drift diagnosability.

**Acceptance:** New tests below pass; existing `test_abuse_integration.py` 403/MCP tests still pass (proves no enforcement regression). **Stated decision:** the MCP +60s cache-hold consequence is **documented only, intentionally unpinned** — its mechanism (middleware 60s TTL + signal-based invalidation) is pre-existing unchanged code whose drift-affected inputs (resolve dict + signal set) ARE pinned (seam tests + REST signal-teardown); the +60s hold is arithmetic on that, and a middleware-level time-travel test would verify unchanged cache semantics (see the MCP note below). The other three accepted-risk consequences (mint grant, claim-write, REST signal-teardown) are pinned.

**Files:**
- Modify: `tests/test_supabase_control.py` (new `TestResolveApiKeyFailSoft` + `TestTeamByID` classes), `tests/test_abuse_integration.py` (mint-under-drift + signal-teardown pins), `tests/test_claim_endpoints.py` (claim-write pin)

**Step 1 (fail-soft + diagnosability, resolve):**

```python
class TestResolveApiKeyFailSoft:
    def test_resolve_api_key_additive_columns_missing_fail_soft(self, fake, caplog):
        """#1096: teams missing 0015 additive columns (the #1001 drift) →
        resolve returns the team, NOT RuntimeError; additive fields default
        to None (un-suspended/un-flagged, pre-0015 behavior); the degrade
        is logged (drift stays diagnosable)."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["team_id"] == "team-free-001"
        assert team["tier"] == "free"
        assert team["max_points"] == 10000
        assert team["suspended_at"] is None
        assert team["flagged_at"] is None
        assert team["email"] is None  # 0006 base column — still read, row has none
        assert any("additive" in r.message for r in caplog.records)

    def test_resolve_api_key_carries_suspension_state(self, fake):
        """O/I/T target 2: with the columns PRESENT, suspension state still
        resolves (enforcement is unchanged — REST 403 / MCP -32006 consume
        this field)."""
        fake.seed("api_keys", [_key_row()])
        fake.tables["teams"][0]["suspended_at"] = \
            datetime.now(timezone.utc).isoformat()
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["suspended_at"] is not None

    def test_resolve_api_key_degrade_then_recover(self, fake):
        """#1096: degrade-then-recover — the helper is stateless; after the
        additive columns become readable again, enforcement resumes (a
        future latch/cache in the degrade path must not stick)."""
        fake.seed("api_keys", [_key_row()])
        fake.tables["teams"][0]["suspended_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        assert resolve_api_key(fake, TOKEN)["suspended_at"] is None  # degraded
        fake.missing_columns = None
        assert resolve_api_key(fake, TOKEN)["suspended_at"] is not None  # recovered

    def test_resolve_api_key_missing_team_under_drift_returns_none(self, fake):
        """#1096: drift + absent team — the base retry returns [] → None
        (401), never a raise (fail-closed on not-found, fail-soft on drift)."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("api_keys", [_key_row(team_id="team-gone")])
        assert resolve_api_key(fake, TOKEN) is None

    def test_long_lived_key_resolves_under_0015_drift(self, fake):
        """#1096: the team_memberships (long-lived key) branch drifts the
        same way — the shared _teams_row_fail_soft teams read degrades
        identically; the membership query itself is drift-scoped."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("team_memberships", [_membership_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["team_id"] == "team-free-001"
        assert team["suspended_at"] is None
        assert team["flagged_at"] is None

    def test_resolve_api_key_base_column_drift_fails_closed(self, fake, caplog):
        """#1096: a drifted BASE column (0006) fails the auth hot path
        CLOSED — combined read raises → base retry also raises (ops_allowance
        stays in the base set) → RuntimeError + the fatal-path WARNING."""
        fake.missing_columns = {"teams": {"ops_allowance"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            with pytest.raises(RuntimeError):
                resolve_api_key(fake, TOKEN)
        assert any("base-only read failed" in r.message for r in caplog.records)
```

**Step 2 (drift + fail-closed, team_by_id — own class, not part of `TestResolveApiKeyFailSoft`):**

```python
class TestTeamByID:
    def test_team_by_id_additive_columns_missing_fail_soft(self, fake):
        """#1096: team_by_id survives 0015 drift (suspension/staging
        columns) — returns the row with None defaults, no raise."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        team = team_by_id(fake, "team-free-001")
        assert team is not None
        assert team["name"] == "Free Team"
        assert team["suspended_at"] is None
        assert team["flagged_at"] is None
        assert team["deleted_at"] is None  # base column read via retry

    def test_team_by_id_deleted_at_survives_0015_drift(self, fake):
        """#1096: the deletion kill-switch must survive 0015 drift — a SET
        deleted_at (soft-deleted team) is carried by the base retry, so
        invitation_accept/export_team 410 guards keep firing."""
        fake.tables["teams"][0]["deleted_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        team = team_by_id(fake, "team-free-001")
        assert team is not None
        assert team["deleted_at"] is not None

    def test_team_by_id_deletion_columns_drift_fails_closed(self, caplog):
        """#1096: the #302 soft-delete columns are NOT fail-soft — a schema
        missing deleted_at/grace_hours fails the deletion guard CLOSED
        (RuntimeError propagates), never opening the 410 kill-switch; the
        fatal-path WARNING (the drift-diagnosability tripwire) fires and
        names the retry select."""
        fake = FakeControlPlane(
            {"api_keys": [], "team_memberships": [], "teams": [dict(FREE_TEAM)]},
            missing_columns={"teams": {"deleted_at", "grace_hours"}})
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            with pytest.raises(RuntimeError):
                team_by_id(fake, "team-free-001")
        assert any("base-only read failed" in r.message for r in caplog.records)

    def test_team_by_id_base_read_fails_closed(self):
        """#1096: the base-only retry must NOT swallow a real outage — a
        broken teams read still propagates RuntimeError (fail-closed)."""
        with pytest.raises(RuntimeError):
            team_by_id(ErrorControlPlane(), "team-free-001")

    def test_invitation_accept_410_under_0015_drift(self, fake):
        """#1096: the deletion kill-switch fires at the CONSUMER level under
        0015 drift — a soft-deleted team still 410s invitation_accept (the
        deleted_at-carried test proves the mechanism; this proves the
        contract the Architecture section claims)."""
        inv = invitation_mint(fake, "team-free-001", "bob@example.com",
                              "member", "owner-1")
        fake.tables["teams"][0]["deleted_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        with pytest.raises(InvitationError) as ei:
            invitation_accept(fake, inv["token"], "user-2")
        assert ei.value.status == 410
```

**Step 3 (consumer-level accepted-risk pin, test_abuse_integration.py — place in `TestMintGateAndAlerts`):** The mint gate is the headline accepted-risk behavior — under 0015 drift a durably-suspended team can mint. Pin BOTH directions (drift fail-open + recovery restores 403), mirroring the existing `test_mint_rejected_while_suspended` pattern (`env` fixture, TestClient, dependency_overrides):

```python
    def test_mint_allowed_under_0015_drift_then_blocked_after_recovery(self, env):
        """#1096 accepted-risk pin: under 0015 drift the mint gate's
        suspended_at reads None → a durably-suspended team CAN mint; after
        recovery the 403 returns (the fail-open window closes)."""
        from tortoise.hosted_api import get_current_user
        fake = env["fake"]
        fake.seed("team_memberships", [{
            "user_id": "user-1", "team_id": TEAM, "role": "owner",
            "status": "active", "team_name": "abuse-team"}])
        # env fixture pre-seeds 2 provisioned keys at the free-tier cap
        # (max_api_keys=2). The DRIFT-phase mint has the suspension gate
        # bypassed (degrade) and would 402 at the cap before creating a
        # key — clear them so the 200 assertion holds. The recovery phase
        # 403s at the suspension gate before any cap check.
        # (precedent: test_auth_flip test_mint_resolves_then_revoked_rejected).
        fake.tables["api_keys"] = []
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        env["app"].dependency_overrides[get_current_user] = \
            lambda: {"user_id": "user-1"}
        try:
            with TestClient(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "team_id": TEAM})
                assert r.status_code == 200  # fail-open during drift (accepted)
            fake.missing_columns = None  # drift resolved
            with TestClient(env["app"]) as tc:
                r = tc.post("/v1/session/key",
                            json={"purpose": "recovery", "team_id": TEAM})
                assert r.status_code == 403  # enforcement restored
        finally:
            env["app"].dependency_overrides.clear()
```

Run: `./.venv/bin/python -m pytest tests/test_supabase_control.py tests/test_abuse_integration.py -q` → all green (incl. the 403/MCP enforcement tests).

**Step 4 (signal-teardown pin, test_abuse_integration.py — place in `TestRestSuspension`):** The accepted-risk worst case is a NEW interaction introduced by this slice: a drifted resolution reads `suspended_at=None` and the pre-existing self-heal tears down the in-process suspension signal while the DURABLE stamp stays set. Pin it (before #1096 a `None` resolution only came from a genuine `abuse_unsuspend`):

```python
    def test_drift_resolution_clears_suspension_signal(self, env):
        """#1096 accepted-risk pin: under 0015 drift a fresh resolution reads
        suspended_at=None and the self-heal clears the in-process signal —
        the only local enforcement cell is torn down while the durable
        suspended_at stays stamped (the worst-case window mechanism)."""
        fake = env["fake"]
        fake.rpc("abuse_suspend", {"p_team_id": TEAM})
        abuse.mark_suspended(TEAM)
        assert abuse.is_suspended_signal(TEAM)
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        with TestClient(env["app"]) as tc:
            r = tc.get("/v1/team", headers=_auth())
        assert r.status_code == 200  # degraded (accepted-by-scope)
        assert not abuse.is_suspended_signal(TEAM)  # self-heal tore it down
        assert fake.tables["teams"][0]["suspended_at"] is not None  # durable stays
```

**Step 5 (claim WRITE pin, tests/test_claim_endpoints.py — place in `TestClaimEndpoint`):** `claim_team` is suspension-gated via the same auth dependency as mint, but the claim is a DURABLE write (owner identity link + email overwrite, survives drift recovery) — pin both the healthy-mode gate and the drift fail-open, using the file's `_claim_env`/`client`/`_provision_anon`/`_patch_verify` fixtures:

```python
    def test_claim_allowed_under_0015_drift_then_blocked_after_recovery(self, client, fake, monkeypatch):
        """#1096 accepted-risk pin: claim is suspension-gated via
        _get_current_team_supabase — under 0015 drift a durably-suspended
        anon team can execute the durable claim write (permanent identity
        link + email overwrite); in healthy mode the 403 SUSPENDED gate
        fires (the pin's premise)."""
        _patch_verify(monkeypatch, _jwt("user-1", providers=["github"]))
        # Drift phase: suspended anon team claims successfully (fail-open).
        key, team_id = _provision_anon(client, fake)
        fake.rpc("abuse_suspend", {"p_team_id": team_id})
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        assert client.post("/v1/claim", json={"api_key": key}).status_code == 200
        fake.missing_columns = None  # drift resolved — enforcement must resume
        # Healthy phase: a FRESH suspended anon team is 403-blocked at the gate.
        key2, team_id2 = _provision_anon(client, fake)
        fake.rpc("abuse_suspend", {"p_team_id": team_id2})
        r = client.post("/v1/claim", json={"api_key": key2})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "SUSPENDED"
```

Run: `./.venv/bin/python -m pytest tests/test_supabase_control.py tests/test_abuse_integration.py tests/test_claim_endpoints.py -q` → all green.

**MCP +60s cache-hold claim — explicitly documented, not consumer-asserted:** the `TeamResolutionMiddleware` 60s TTL arithmetic is **pre-existing and unchanged** — this slice adds no drift-specific branch to the middleware. The drift-affected inputs to the cache (`resolve_api_key`'s returned dict + the suspension signal set) are pinned at seam/REST level (`test_resolve_api_key_additive_columns_missing_fail_soft`, `degrade_then_recover`, `test_drift_resolution_clears_suspension_signal`); the cache faithfully caches whatever resolve returns, so the MCP +60s hold is a documented consequence of pinned behaviors, not new untested code — an MCP-transport drift test would re-verify unchanged cache arithmetic for marginal value against the plan's 15-test suite (6 resolve + 5 team_by_id + mint + signal-teardown + claim-write + fake-filter-drift pins).

## Task 5: full suite + commit handoff

**Intent:** Prove no regression beyond the seam and hand off for the commit gate.

**Acceptance:** Hermetic suite green; diff contains only the planned files.

**Files:**
- Run: full suite

**Step 1:** `./.venv/bin/python -m pytest tests/ -q` → green (hermetic suite, FalkorDBLite).

**Step 2:** Confirm `git status` shows only the planned files (`tortoise/supabase_control.py`, `tortoise/hosted_api.py` — docstring-only edits per Task 2 Step 3b, `tests/fake_control_plane.py`, `tests/test_supabase_control.py`, `tests/test_abuse_integration.py` — mint-under-drift (Step 3) + signal-teardown (Step 4) tests, `tests/test_claim_endpoints.py` — claim-write pin (Step 5), this plan doc).

**Step 3:** Invoke `commit-workflow` (mandatory gate before any commit; creates PR, code-review gate).

<!-- plan-review: cycles=27, status=clean, version=2.3.0 -->
<!-- cycle log: 27 cycles, ~45 issues found+fixed (P1×6: helper {} contract KeyError, fail-open window understated+revocation closure dropped, mint test 402-at-cap arithmetic, claim pin drift-not-reset, docstring contracts stale; P2×~39: hot-path query cost → combined-select-with-fallback redesign, deleted_at/grace_hours fail-closed split, surface-map completeness (list_my_teams/delete_team/create_graph/list_graphs/invite_to_team/claim_team/claim_status/team_info/team_id_for_stripe_customer/derived_tier attributions), purge-sweep behavior corrections, warning-message accuracy, caplog pins, degrade-then-recover + membership-path + base-column-fail-closed + filter-drift tests, MCP +60s documented-only stated decision, PGRST204 verified correct vs v12 error reference). Qwen gate skipped: provider API unavailable (401). Final verification (Phase 5): NO ISSUES FOUND. -->
