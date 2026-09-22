---
title: "Scoping: #2127 — shared patched_tortoise_sdk fixture helper + B-wave migration"
type: decisions
domain: capability
doc_status: live
created: 2026-09-02
ownedBy: epistemic-team
---

# Issue #2127 scoping — shared patched_tortoise_sdk helper + B-wave migration (foundation + wave 1)

> Scope record for issue #2127. The issue body IS the detailed scope (it descends
> from the #2090 scoping doc `docs/scoping/2026-09-01-2090-test-b-lane-scoping.md`
> follow-up section, itself triple-verified). This record VERIFIES that scope
> against origin/main code (a39eff70) and resolves the one open design decision
> (wave-1 composition of `tests/test_export_delete.py`'s #2090 machinery with the
> shared helper) with evidence. Not a re-derivation of the double diamond — a
> proportionality-scaled verification + convergence record.
>
> Tier: standard (issue label `complexity:standard`). Level: task (atomic
> deliverable: helper + wave 1 migration + child-issue filing; later waves ship
> as separate child issues).

## Confirmed problem (verified)

~24-27 test files carry the churn-shaped fixture pattern: `TortoiseSDK.__init__`
patch → temp DB + `_FALLBACK_KEEPALIVE.clear()` **without** a `TORTOISE_DB_PATH`
pin and without deterministic anchor-close at restore. This is the latent class
that produced the #2090 403s (empty respawn after daemon death) and the #2049-b
dead-socket sibling. #2090 fixed the class in 2 files; the stragglers remain,
each carrying a slightly divergent local copy of the patch/restore machinery —
the churn mechanism reproduces file-by-file whenever a new test touches the
keepalive path. Fix: ONE shared `patched_tortoise_sdk(db_path)` context manager
in a new sibling module `tests/_http_fixtures.py` + migrate files in waves.

## Baseline evidence (origin/main @ a39eff70, clean worktree — recorded BEFORE any edit)

| Gate | Command | Result |
|---|---|---|
| #2090 embedded | `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_export_delete.py tests/test_free_team_entitlement.py -q` | **58 passed** ✓ |
| #2090 docker | `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_delete.py tests/test_free_team_entitlement.py -q` | **56 passed + 2 skipped** ✓ |
| #1502 pair | `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_hosted_api.py -k "make_sdk_reuses_healthy_anchor or make_sdk_rebinds_stale_anchor" -q` | **2 passed** ✓ |
| ruff | `uv run ruff check .` | clean ✓ |

## Wave delta list (verified per-file against origin/main)

Grep primitives: `TortoiseSDK.__init__` patch present (P), `_FALLBACK_KEEPALIVE.clear()` (C),
`TORTOISE_DB_PATH` assignment/pin (Pin), deterministic anchor close at restore (Close),
seed-hold list (Hold), shared `_patch_tortoise_sdk_init` helper name vs inline `_patched`.

### Churn-shaped files (patch + clear, NO pin) — the migration targets

| File | Shape | Pin | Close | Hold | Wave | Evidence notes |
|---|---|---|---|---|---|---|
| test_action_endpoints_dual_auth.py | shared helper | ✗ | ✗ | ✗ | 1b | helper defs @47 |
| test_agent_ops_pack.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @132, 2 clear sites |
| test_auth_flip.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @68 |
| test_body_cap_sweep.py | shared helper | ✗ | ✓ | ✗ | 1b | `_patch_sdk_init` @54 (inner `_patched` @64); internal CM deterministically closes anchors @75-81 (most helper-like local shape; only the env pin is missing) |
| test_commit_endpoint.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @68 |
| test_domain_validators.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @705 |
| test_import_endpoint.py | shared helper | ✗ | ✗ | session-scoped `_SEED_PROJS` autouse | 1b | `_restore_sdk_init` @127-129 restores init + clears overrides only (NO anchor close); session-scoped `_SEED_PROJS` hold is the leak-mitigation |
| test_invites_email_http.py | shared helper | ✗ | partial | ✓ | 1b | `_REG_SDKS` hold + close in teardown |
| test_invites_http.py | shared helper | ✗ | partial | ✓ | 1b | `_REG_SDKS` hold + close in teardown |
| test_oauth_mcp.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @77 |
| ~~test_onboarding_endpoints.py~~ | **NOT wave 1b — moved to wave 2 verify/migrate (verifier P2-2, controller-confirmed)** | | | | | its `client`/`unauth_client` fixtures are ALREADY #2090-canonical (force-patch + pin + dict-sig close-at-restore @43-122); the :272/:309 inline variants are deliberate db_path PASS-THROUGH patches (do NOT force a temp DB — blind migration changes what they test). Shape-audit precondition, dedup-only migration of the 2 canonical fixtures |
| test_onboarding_health_flip.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @49 |
| test_session_extraction_modes.py | shared helper | ✗ | ✗ | ✗ | 1b | helper @160 |
| test_session_key_http.py | shared helper | ✗ | partial | ✓ | 1b | hold + close in teardown |
| test_writer_inventory.py | shared helper | ✗ | ✗ | ✗ | 1b | helper defs (the #1497 original) |
| test_pack_manifest_store.py | inline `_patched` only | ✗ | ✗ | ✗ | 1b | **patch-only, no clear** |
| test_pack_manifest_store_extraction.py | inline `_patched` only | ✗ | ✗ | ✗ | 1b | **patch-only, no clear** |
| test_sdk_props_coercion.py | inline `_patched` only | ✗ | ✗ | ✗ | 1b | **patch-only, no clear** |

### Inline-variant files (wave 2 — verify/migrate)

| File | Shape | Status |
|---|---|---|
| test_index_docs_api.py | inline `_patched` @96 | migrate (wave 2) |
| test_lme_m6_evidence.py | inline `_patched_init` @1112 | migrate (wave 2) |
| test_abuse_integration.py | inline `_patched` + own `_restore_sdk_init` @140 | migrate (wave 2) |
| test_github_connect.py | inline `_patched` @27/58 | migrate (wave 2) |
| test_github_index_lifecycle.py | inline `_patched` @91 | migrate (wave 2) |
| test_capture_session.py | inline `_patched` @1717 + own no-arg `_close_keepalive_anchors()` @1724 | **verify only** — per-site audit precondition: only ONE fixture (@1743-1773) pairs clear with pin+close; 4+ other clear sites (:2217/:2232/:2278/:2291) need pin/close audit (verifier P3-8) |
| test_dr_endpoints.py | inline `_patched_init` @53 + `_SEED_SDKS` hold | **verify only** (holds seeds) |
| test_free_team_entitlement.py | inline `_patched` @84/278 + module `_close_keepalive_anchors` | **verify only** (migrated in #2090 + later main change) |
| test_onboarding_endpoints.py | fixtures already canonical + 2 deliberate pass-through variants | **verify/migrate** (moved from wave 1b — verifier P2-2): dedup the 2 canonical fixtures onto the helper; AUDIT (do not migrate) the :272/:309 pass-through variants |
| test_pack_state.py | shared-helper name, clear-without-close | **verify/migrate** (reclassified from 'already pinned' — verifier P2-1, controller-confirmed): `registry_client` @432-447 + `supabase_client` @449-477 restore ONLY `__init__` + overrides (NO pin, NO anchor close — the #1950 clear-without-close leak shape); the file's 4 TORTOISE_DB_PATH hits are in-body monkeypatch in other tests, not the fixtures |

### Already-canonical / not targets

| File | Reason |
|---|---|
| test_hosted_api.py | #1950 canonical source of the pin + close pattern (:104-202, :144-153) — NOT a target (it is the pattern's origin) |
| ~~test_pack_state.py~~ | ~~already pinned~~ — **CORRECTED: reclassified to wave 2 verify/migrate** (its fixtures are clear-without-close; the #2090 scoping doc's 'already-pinned = 3 (hosted_api, capture_session, pack_state)' was wrong at fixture level — true count is 2 full + pack_state partial) |

### Count reconciliation

Wave 1 = 2 (export_delete, suspension_parity) · wave 1b = 14 shared-helper + 3
patch-only = 17 (onboarding_endpoints struck — moved to wave 2) · wave 2 = 5
migrate + 4 verify/migrate + pack_state = 10. Churn-action universe: 2 + 17 + 5
+ pack_state = 25 files needing action; + verify-only (capture_session,
dr_endpoints, free_team_entitlement, onboarding_endpoints) ≈ the issue's "~24".
Full patch-universe accounting: hosted_api (canonical) + 2 + 17 + 10 = 30 files
patching TortoiseSDK.__init__ — matches the origin/main grep exactly.
= 2 + 18 + 5 − overlap accounting ≈ verified (hosted_api/pack_state/capture_session
are pinned, export_delete/free_team_entitlement fixed — consistent with the #2090
scoping doc's "pattern-match 27 / already-pinned 3 / remaining ≤ 24").

## CRITICAL SUBTLETY — wave-1 composition decision (resolved WITH EVIDENCE)

**Question:** may `tests/test_export_delete.py` migrate FULLY onto the shared
helper without breaking the #2090 machinery layered on its fixtures (drift
counter `_DriftEvictionCounter`, masked-pin assert, `_SEED_SDKS` seed-hold,
`TestDriftCounterWiring`, module `_EXPECTED_DRIFT_EVICTIONS` 0-guard)?

**Decision: FULL COMPOSITION — yes.** The shared helper becomes the fixture body;
the file keeps ALL of its counter/seed-hold/wiring additions. Rationale:

1. **The file is not churn-shaped — it is the reference implementation.** Its
   local `_patch_tortoise_sdk_init` (test_export_delete.py:237-257) ALREADY
   encodes patch→pin→clear and `_restore_sdk_init` (:259-266) ALREADY encodes
   pop-env→restore-init→anchor-close→clear-overrides — byte-for-byte the
   helper's contract in non-context-manager form. Migration = replacing two
   local helpers with the shared one; the machinery composes by construction.
2. **Every pinned invariant is preservable** with an explicit ordering (traced
   against the fixture code). ⚠️ **DRAIN-LINCHPIN (verifier P1-1, accepted):**
   under counter composition the helper's `__exit__` anchor-close is a **design
   no-op** — it closes the RESTORED real dict, which is empty (helper enter
   emptied it; every in-test anchor lives in the counter). The fixture OWNS the
   deterministic close: a VERBATIM drain loop (identical to the one in both
   `TestDriftCounterWiring` teardowns, :163-170/:190-197) closes counter-held
   anchors, guarded by `assert not counter` immediately before the real-dict
   restore — omission converts into an every-test red, never a silent
   timing-dependent leak. Trace:
   - helper enter (patch → pin `TORTOISE_DB_PATH` → `_FALLBACK_KEEPALIVE.clear()`)
     runs BEFORE `_install_drift_counter()` — identical to today (clear happens
     in `_patch_tortoise_sdk_init`, then install copies the emptied real dict).
   - the 0-guard assert + warn run in the yield-`finally` with the counter still
     ENABLED (unchanged).
   - `counter.enabled = False` happens BEFORE any restore-time pop (unchanged —
     restore-time pops must never count).
   - counter-held anchors are drained + closed (uncounted) inside the fixture
     `finally`, then `ha_mod._FALLBACK_KEEPALIVE = _orig_dict` restores the real
     dict — the counting dict is never left as the module attr, and anchors are
     never dropped unclosed (the #1950 leak).
   - `_close_seed_sdks()` runs after the counter drain (anchor close) →
     deterministic last-client SAVE (pinned ordering preserved in effect: the
     helper's exit-close normally no-ops on the empty restored real dict; if an
     anchor were ever registered post-drain it would close after the seeds —
     benign, still a deterministic SHUTDOWN SAVE). Stated explicitly in the
     fixture comment.
   - the helper `__exit__` (pop env → restore `__init__` → close remaining
     anchors on the restored real dict → clear overrides) is exception-safe via
     with-statement semantics — it runs on body-failure paths too.
3. **The empirical gate arbitrates.** The #2090 gate set (58 embedded / 56+2
   docker) + #1502 pair + the counter's deterministic 0-guard are the regression
   device — if composition broke any invariant the gate reds and we fall back to
   the issue's deferral option (helper + suspension_parity; export_delete left
   as-is). **Fallback recorded and pre-agreed.**
4. **Value:** full composition proves the helper composes with the MOST complex
   existing machinery (counter + seed-hold + wiring tests) — the exact proof
   wave 1b/2 files (which are simpler: no counter, few holds) need before their
   migrations. It also deletes the file's duplicated `_close_keepalive_anchors`
   defs (:221 and :269 — the second shadows the first) and its three now-redundant
   local helpers.

**Wave-1 scope (this run):** helper module + test_export_delete full composition
+ test_suspension_parity migration. `test_suspension_parity` is the simplest
shared-helper user (patch+clear only, no pin, no anchor-close at restore, module
`_SEED_SDKS` hold retained as-is).

## Implementation plan (this run's deliverable)

### Task 1: `tests/_http_fixtures.py` — shared `patched_tortoise_sdk(db_path)`

**Intent:** single source of truth for the #1950 pin + #2090 close-at-restore
fixture pattern, replacing ~24 divergent local copies.

**Contract (per issue spec) — import purity (verifier P3-7, accepted):** the
module must import with ZERO side effects (no module-level env writes, no
`app`/keepalive mutation at import — unlike test-file idiom which sets
`TORTOISE_SECRET_PEPPER` etc. at module body). Lazy `import tortoise.hosted_api
as ha_mod` inside the contextmanager (repo helper convention,
test_hosted_api.py:106) so the module never imports hosted_api at collection
unless a migrated file actually enters the helper.

**`__enter__` close-then-clear (verifier B cycle-2 P3, accepted):** enter does
`_close_keepalive_anchors(ha_mod)` (deterministic close of any pre-existing
stray anchor from a prior file/session) BEFORE `_FALLBACK_KEEPALIVE.clear()` —
never plain clear-without-close at the one remaining enter site (the #2090
lesson applied to enter; the precedents' plain-clear-at-enter was safe only
because their teardowns drained the dict). Zero cost on the empty-dict common
path; the exception-safe close makes the cross-file pollution run (gate 6)
strictly safer.
- `__enter__`: snapshot + patch `tortoise.hosted_api.TortoiseSDK.__init__` →
  force `db_path`; set `os.environ["TORTOISE_DB_PATH"] = db_path` (the #1950 pin,
  `tests/test_hosted_api.py:174-202` rationale); `ha_mod._FALLBACK_KEEPALIVE.clear()`.
- `__exit__`: `os.environ.pop("TORTOISE_DB_PATH", None)` → restore `__init__` →
  `_close_keepalive_anchors(ha_mod)` (deterministic close,
  `tests/test_hosted_api.py:144-153`) → `app.dependency_overrides.clear()`.
- Implemented with `contextlib.contextmanager`; docstring cites precedents with
  source-pins (`#1950` hosted_api:174-202, close-at-restore hosted_api:135-153,
  mirrors note). Lint-clean (noqa where the precedents do).

**Module placement note:** `tests/_embedded.py` charter untouched (centralized
embedded construction + #1647 lane machinery). `_http_fixtures.py` is the
patch-fixture family sibling (hosted-api test doubles, beside
`tests/fake_control_plane.py`). NOT imported by conftest (conftest eagerly
imports `_embedded`; adding `_http_fixtures` would flip SHARED_MODULES tier
selection for every wave PR — the #2090 tripwire lesson). Imported on demand by
migrated test files (`from tests._http_fixtures import patched_tortoise_sdk`;
tests/ is a namespace package, same import style as `tests.fake_control_plane`).

**Acceptance:** `uv run ruff check tests/_http_fixtures.py` clean; importable
from a migrated file; docstring source-pins present.

### Task 2: `tests/test_export_delete.py` full composition

**Files:** tests/test_export_delete.py (modify).

Deltas:
- Remove local `_close_keepalive_anchors` (both duplicate defs :221/:269),
  `_patch_tortoise_sdk_init` (:237), `_restore_sdk_init` (:259) — superseded by
  the shared helper.
- Add `from tests._http_fixtures import patched_tortoise_sdk`.
- `sb_client`/`reg_client` fixture bodies: `with patched_tortoise_sdk(db_path):`
  wraps the TestClient yield; counter install after helper enter; teardown order
  per the composition trace above.
- **reg_client teardown (pinned, verbatim structure):** keep the current
  :341-355 nesting verbatim — the 0-guard assert + warn live in an INNER `try`
  whose `finally` runs (b)-(f), so an assert RED still leaves clean module
  state (enabled counter never stranded as the module attr; real dict always
  restored; seeds always closed). Inside that `finally`:
  (a) 0-guard `assert counter.drift_evictions == _EXPECTED_DRIFT_EVICTIONS` + warn
  run with the counter **ENABLED** (never move `enabled = False` before the
  assert — that would silently vacate the #2090 proof; verifier P2-3);
  (b) `counter.enabled = False` (restore-time pops must never count);
  (c) **drain loop verbatim** — multi-line copy of test_export_delete.py:164-170
  (`for _ns in list(counter):` + `dict.pop` + per-anchor try/except + `# noqa: SIM105`;
  never a compressed one-liner — E701/E702 would red the ruff gate);
  (d) `assert not counter` (drain-completeness guard — omission reds every test);
  (e) `ha_mod._FALLBACK_KEEPALIVE = _orig_dict` (the ONLY line that makes the
  helper's exit-close a true design no-op — must be unreachable-skip-free);
  (f) `_close_seed_sdks()`. The helper `__exit__` then reaps env/init/real-dict(empty)/overrides.
- **sb_client teardown (explicit — verifier P3-6):** yield-`finally` keeps
  `_close_seed_sdks()` (inert today — sb tests never seed — but uniform
  discipline); helper `__exit__` reaps env/init/anchors/overrides. Ordering note:
  sb has no counter, so the helper's exit-close IS the deterministic anchor
  close; with seeds (if ever seeded) closed in the `finally` first, the helper's
  exit-close becomes the last client — outcome-equivalent SAVE, annotated in the
  fixture comment.
- KEEP unchanged: `_SEED_SDKS`, `_close_seed_sdks`, `_computed_db_path`,
  `_paths_same`, `_DriftEvictionCounter`, `_install_drift_counter`,
  `TestDriftCounterWiring` (2 embedded_only tests), the seed-verify assert in
  `test_export_requires_owner_registry` (the #2090-doc "masked-pin" — the assert
  `_registry_count(...) == 1` at :794-795 must survive the refactor; test bodies
  are untouched), `_EXPECTED_DRIFT_EVICTIONS`, sb/reg_client public signatures +
  yields. The 0-guard's assert-before-disable ordering is pinned code-adjacent.

**Acceptance:** #2090 gate embedded 58 passed + docker 56 passed/2 skipped; #1502
pair green; ruff clean.

### Task 3: `tests/test_suspension_parity.py` migration

**Files:** tests/test_suspension_parity.py (modify).

Deltas:
- Remove local `_patch_tortoise_sdk_init` (:75) + `_restore_sdk_init` (:88).
- `sb_client`/`reg_client` bodies → `with patched_tortoise_sdk(db_path):`
  wrapping the yield; keep the file's module `_SEED_SDKS` hold + append in
  `_seed_registry` as-is (module-lifetime hold is this file's documented
  precedent).
- Note: this file's current `_restore_sdk_init` does NOT close anchors (clear
  leak pattern) — the helper adds the deterministic close + env pin (behavior
  change in the safe direction, the #1950/#2090 lesson).

**Acceptance:** `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_suspension_parity.py -q`
green embedded + docker lane; ruff clean. (File not in the #2090 gate set — run
standalone both lanes as the wave-1 exposed set.)

### Task 4: docs rows + child issues

- `docs/00_index.md` row for this scoping doc (recent-convention format).
- File child issues (issue-creation discipline) for wave 1b, wave 2, wave 3
  (tripwire: config/ allowlist data file, runtime-membership enforcement,
  warn-for-baseline channel, baseline flips to fail) — each self-contained,
  citing the #1950 pin + close-at-restore + this issue's helper as the
  migration target, with the regression gate. Keep #2127 OPEN.
- **Wave-1b child issue content (corrected):** 14 shared-helper files (mechanical
  dedup onto the helper) + the 3 patch-only files (pack_manifest_store,
  pack_manifest_store_extraction, sdk_props_coercion) marked **mechanical-only**
  — zero `_FALLBACK_KEEPALIVE` refs in all three (verifier P4-10): migration
  value ≈ 0 behavioral delta; carry the wave-3 tripwire pattern-scoping note
  (sdk_props_coercion patches `tortoise.sdk` NOT `hosted_api` — the tripwire
  pattern must discriminate hosted_api patches or the allowlist must cover
  them).
- **Wave-2 child issue content (corrected):** 5 migrate (index_docs_api,
  lme_m6_evidence, abuse_integration, github_connect, github_index_lifecycle) + 4
  verify/migrate (capture_session with per-clear-site audit, dr_endpoints,
  free_team_entitlement, onboarding_endpoints with shape audit of the :272/:309
  pass-through variants — dedup only the 2 canonical fixtures, never migrate the
  pass-through variants; the :309 q3/wizard inline patch block (test
  `test_q3_and_wizard_write_same_keys`) restores `__init__` WITHOUT closing the
  anchor its TestClient registry read creates while `TORTOISE_DB_PATH` is UNSET
  → binds the SHARED default path (#1497/#2090 shared-DB leak class) — the audit
  must name THIS block explicitly) + pack_state (registry_client + supabase_client get the
  pin + close-at-restore).

## Regression gate (this run's acceptance, BOTH lanes)

1. `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_export_delete.py tests/test_free_team_entitlement.py -q` → 58 passed (embedded)
2. `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_export_delete.py tests/test_free_team_entitlement.py -q` → 56 passed + 2 skipped (docker)
3. `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_hosted_api.py -k "make_sdk_reuses_healthy_anchor or make_sdk_rebinds_stale_anchor" -q` → 2 passed
4. Per-wave exposed set: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_suspension_parity.py -q` both lanes (26 passed embedded baseline)
5. **×10 embedded streak on tests/test_export_delete.py alone** (verifier P2-4 — the class is intermittent; a single green run cannot evidence a teardown-chain rewrite; ~50s/run) + record any failure per the test-debt gate re-run protocol
6. **Cross-file pollution run** (verifier P4): export_delete + the #1502 pair in ONE session (module-dict/env-restore discipline back-to-back)
7. `uv run ruff check .` clean
8. Zero production-code changes (diff is tests/ + docs/ only)

## Rejected alternatives (convergence record)

| Option | Verdict | Why |
|---|---|---|
| Defer test_export_delete composition to a later wave | Not chosen (this run) | Composition traced clean against all pinned invariants (above); gate arbitrates; fallback pre-agreed if red. |
| Put the helper in tests/_embedded.py | Rejected | Charter violation (issue body + #2090 scoping doc): _embedded is centralized construction + #1647 lane machinery; conftest eagerly imports it → SHARED_MODULES tier flip on every wave PR. |
| Conftest-inline allowlist for the tripwire | Rejected (moves to wave 3 child issue) | SHARED_MODULES tier flip (same reason); runtime-membership + config/ data file per #2090 scoping doc. |
| test_hosted_api.py as import source for `_close_keepalive_anchors` | Rejected (this run) | 3,300-line module import coupling; the helper carries a local verbatim copy with a source-pin mirror comment (repo convention, matches #2090's local-copy decision). |

## Wiring check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| tests/_http_fixtures.py (new module) | test infra | Task 1 + ruff + import smoke | ⚠️ planned |
| tests/test_export_delete.py fixtures | test infra | Task 2 + gate set (58/56+2) | ⚠️ planned |
| tests/test_suspension_parity.py fixtures | test infra | Task 3 + wave-1 exposed set both lanes | ⚠️ planned |
| #1502 regression pair | test infra | gate 3 | ⚠️ planned |
| docker lane immunity | CI | gate 2 | ⚠️ planned |
| ruff parity | CI lint | gate 5 (==0.16.4 pinned) | ⚠️ planned |
| wave 1b/2/3 follow-ups | test infra / config | child issues (Task 4) — #2127 stays OPEN | ⚠️ planned |
| #2128 parallel branch (registry durability) | production | zero prod changes; rebase-ready at merge | ✅ no overlap |

## Complexity

| Domain | Rating |
|---|---|
| Code (test infra) | standard |
| Config | none this run (wave 3 config/ file is a child issue) |
| Overall | standard |

## Review cycle log

### scope-verify — Cycle 1
- Verifier A (scope check): **VERIFIED — no P0/P1/P2.** P3s: body_cap_sweep Close marker/line cite; import_endpoint close overstated; onboarding_endpoints not a named-helper file. P4s: ordering-invariant wording; "masked-pin" terminology; SHARED_MODULES mechanism phrasing; #1502 gate strength (add cross-file pollution run).
- Verifier B (devil's advocate): P0=0, **P1=1**, P2=4, P3=4, P4=1.
  - P1-1 drain-linchpin: under counter composition the helper's `__exit__` anchor-close is a SILENT NO-OP on the restored empty real dict — the fixture MUST own the deterministic close via a verbatim drain loop (today the close happens implicitly via `_restore_sdk_init` closing the counter). If omitted → #1950/#2090 nondeterministic leak class returns, and a single green run cannot catch it.
  - P2-1 pack_state reclassification: `registry_client`/`supabase_client` are clear-without-close (no pin, no close) — the #2090 doc's "already-pinned = 3" was wrong at fixture level.
  - P2-2 onboarding_endpoints wave placement: client/unauth_client already canonical; :272/:309 variants are deliberate pass-through patches — not a mechanical wave-1b user.
  - P2-3 0-guard ordering vacuity: `enabled = False` before the assert would silently kill the #2090 proof.
  - P2-4 acceptance strength: single runs cannot evidence an intermittent-class teardown rewrite (×10 streak).
  - P3s: sb_client explicit body/seed-close; _http_fixtures import purity; capture_session per-site audit; module-attr stranding guard (`assert not counter`); patch-only 3-file migration value ≈ 0.
- Controller action: **ACCEPTED P1-1 + all P2s + all actionable P3s** (controller-confirmed both reclassifications against code: pack_state fixtures @432-477 restore-only-no-close; onboarding_endpoints fixtures @43-122 already pin+close, pass-through variants @272/:309). Amended the doc: drain-linchpin verbatim + `assert not counter` guard + sb_client explicit body + import purity in Task 1 acceptance + corrected wave tables (body_cap_sweep, import_endpoint, capture_session, onboarding_endpoints→wave 2, pack_state→wave 2) + ×10 embedded streak + cross-file pollution run + child-issue content corrections. Re-dispatching both verifiers (cycle 2)…

### scope-verify — Cycle 2 (on the amended scope)
- Verifier A: **no P0/P1** — P1 fix CLOSED (drain-linchpin); assert-before-disable PRESERVED; wave-table corrections code-accurate; ×10 streak + cross-file pollution run feasible. P2-1: the (a)-(f) flat list doesn't pin the exception-safe NESTING — an (a)/(d) assert RED would skip (b)-(f) (enabled counter stranded as module attr, real dict unrestored, seeds unclosed). P3s: stale 15/18 count after the onboarding_endpoints strike; masked-pin cite :552-556 is wrong (real assert :794-795); drain rendered as a ruff-violating one-liner.
- Verifier B (devil's advocate): **no P0/P1** — Q1 pre-mortem: M2/M3/M4/M5 RULED OUT structurally; M1 = the same nesting gap (F1, P2). Q2 assert placement sound on pass/failure/setup-failure paths. Q3 both orderings deterministic-SAVE-safe. Q4 reclassifications code-verified. Q5 bounded. P3s: onboarding :309 q3/wizard pass-through block leaks a SHARED-default-path anchor (restore lacks close — pre-existing, must be named in the wave-2 audit); helper `__enter__` plain clear() is the one remaining clear-without-close site (make it close-then-clear). P4: citation drift.
- Controller: incorporated ALL P2s/P3s (nesting pinned — inner-try/finally verbatim :341-355 shape; counts corrected to 14+3=17 wave 1b / 30-file full universe; masked-pin cite fixed to :794-795; drain specified as multi-line verbatim copy of :164-170 with noqa; helper `__enter__` changed to close-then-clear; onboarding :309 block named explicitly in the wave-2 child content; body_cap_sweep cite :75-81).
- **GATE RESULT: scope-verify PASSED — no P0/P1 across cycles 1-2. Scope + plan verified; proceeding to implementation (plan-verify folded into scope-verify cycle 2 — the scope doc carries the implementation plan, which both verifiers exercised in detail).**
