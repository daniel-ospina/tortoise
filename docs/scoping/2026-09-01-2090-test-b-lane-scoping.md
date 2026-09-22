# Scoping: #2090 — test (b) lane intermittent 403s in export/delete (embedded-lane data loss)

> Pipeline: task-workflow-standard → issue-scoping v5.1 double diamond.
> Issue: https://github.com/daniel-ospina/tortoise/issues/2090
> Branch: fix/2090-ci-lane-pollution. Date: 2026-09-01.
> Status: problem diamond VERIFIED (2 verifier cycles); solution diamond pending.

## Amended Confirmed Problem (post problem-verify cycle 1)

The intermittent 403-on-export/delete CI failures (issue #2090) are **within-test
embedded-lane data loss** — the same test-isolation family documented in-repo by
`f69c775d`, #1497, #1502, #1607 — **not** runner DB pollution / quota-class. The
issue's original framing is refuted (see Rejected Framings).

### Reconciled mechanism (hybrid — hypothesis to be confirmed by one fix-phase experiment)

1. **Deterministic enabler — anchor path-drift churn.** The test fixture patches
   `TortoiseSDK.__init__` to bind to a per-test temp DB
   (`tests/test_export_delete.py:66-82`) but never sets `TORTOISE_DB_PATH`.
   `_make_sdk`/`_registry_anchor` (`tortoise/hosted_api.py:131-215`) compute the
   path from env (`/data/tortoise.db`, falling back to `/tmp/tortoise.db`) which
   **never equals the fixture tempdir** → `_anchor_usable`
   (`hosted_api.py:96-127`) reports path drift on **every** call → the #1607
   keepalive anchor is evicted+closed+recreated per call → **0/1-client windows**
   on the fixture's redislite daemon. (This churn is a direct consequence of the
   #1502 fix, commit `9de006a1`: the rebind check that cured cross-test staleness
   makes the anchor unusable per-call in test env.)
2. **Nondeterministic trigger — dropped-SDK GC-NOSAVE in a window.**
   `_seed_registry` (`tests/test_export_delete.py:195-214`) drops its SDK
   (returns `None`, never explicitly closed). When the dropped SDK is collected
   at a moment when it holds the **sole server-side connection** — the killing
   window is the 0-other-client instant (the seed is the only `CLIENT LIST`
   entry at finalizer time; count>1 degrades to disconnect-only, verified
   `_gc_close` semantics; two entries arise from two live SDKs, e.g. anchor +
   fresh request SDK, never one churned SDK) — the #1475 close-on-GC finalizer
   (`tortoise/embedded_lifecycle.py:204-282`, enabled by
   `TORTOISE_FAST_ATEXIT=1` at `tests/conftest.py:42`) fires
   `SHUTDOWN NOSAVE` → daemon dies without persisting (registry writes live only
   in daemon memory — no RDB is written).
   > **Trigger timing — open sub-question (resolved by the fix-phase
   > experiment, see Fix-phase experiment):** verifiers disagree on the
   > collection timing of the dropped projection — (A) refcount-deterministic
   > at scope exit vs (B) cycle-pinned (`proj ↔ _GuardedGraph`), so
   > cyclic-GC-timed. A also proposes an alternative trigger: the enter-time
   > anchor's eager `_get_proj()` (best-effort, exception swallowed) may
   > intermittently fail to connect on a loaded runner → the seed is last
   > client at scope exit → deterministic NOSAVE. Both are sub-hypotheses of
   > "collection landing when the dropped SDK holds the last server-side
   > connection(s)". The family and both fix families are unaffected.
3. **Killing event — empty respawn → authz-403.** Dead daemon →
   `_registry_anchor()` respawns an **empty** DB at the same path →
   `_require_owner` (`hosted_api.py:6732-6799`, authz-first by design, PR #873)
   returns **403 "Requires owner role in team"** before the 410/200
   short-circuits (`export_team` def at :7840, `delete_team` def at :8909;
   short-circuit raises ~:7852/:8956).

**Neither trigger alone is sufficient:** explicit `close()` does **not** NOSAVE
(daemons survive 0-client windows); the seed SDK is not last-client when the
lifespan-pinned anchor holds (registry-mode fixtures pin an anchor via
`_purge_deleted_teams` at TestClient enter, `hosted_api.py:426-432`). The family
is a **racy GC-NOSAVE landing in an eviction-created window**.

**Mode qualification:** the anchor-pin premise holds for registry-mode fixtures;
Supabase-mode fixtures pin no anchor (boot purge early-returns). The trigger
depends on fixture mode.

### Symptom family (both classes must be covered by fix acceptance)

| Symptom | Mechanism | Where observed |
|---|---|---|
| 403 "Requires owner role in team" (expected 200/410/202) | empty respawn after daemon death (clean socket teardown — Linux CI) | #2090 on #1937/#2049/#2054 |
| `redis.socket ConnectionError` / 500-class | stale unix socket after NOSAVE death (different teardown timing — macOS local / #2049-b) | #2049-b `test_free_team_entitlement` sibling; pre-#1502 class |

### Blast radius (re-verified cycle 2 — the "19" correction was itself wrong)

- **26-27 test files** match the pattern definition (`TortoiseSDK.__init__`
  patch + `_FALLBACK_KEEPALIVE.clear()`); 26 by grep intersection, 27
  including `test_body_cap_sweep.py`'s variant. "19" counted only files using
  the shared `_patch_tortoise_sdk_init` helper; the other ~8 use inline
  `_patched_init` variants (test_index_docs_api, test_lme_m6_evidence,
  test_abuse_integration, test_github_connect, test_github_index_lifecycle,
  test_capture_session, test_dr_endpoints, test_free_team_entitlement).
  **Fix family (b)'s churn coverage claim is 26-27 files, not 19.**
- **True dropped-seed victim:** `test_export_delete._seed_registry` (+ the
  `TestPurge` class, `tests/test_export_delete.py:633-671`, which seeds without
  holding and calls `ha_mod._purge_deleted_teams()` directly; + `_registry_count`
  drops SDKs after every read).
- **Already-fixed precedents to replicate (in-repo prior art):**
  `test_suspension_parity` `_SEED_SDKS` (comment documents exactly this
  mechanism + "flaky registry-mode 403s"), `test_dr_endpoints` `_SEED_SDKS`
  (commit `f985f6c1`, #1579/#1587), #1556 (`test_invites_http`), #1612,
  `f69c775d` (`_seed_graph` returns sdk, "caller keeps this alive until the
  export reads the graph"), and — for fix family (b1) specifically —
  **#1950 `test_hosted_api.py` `client` fixture (:174-202)** which pins
  `TORTOISE_DB_PATH` to the fixture temp DB: "so _make_sdk's keepalive anchor
  path matches and the anchor is REUSED instead of evicted + closed on every
  registry access (each eviction shut the redislite daemon down mid-test,
  losing the consent seed … → 403)". #1950 also documents the companion:
  clearing alone leaked anchors — deterministic anchor-close at restore is
  required (see :135). Coverage claim: pattern-match = 27 files; already-
  pinned = 3 (test_hosted_api, test_capture_session, test_pack_state);
  remaining churn-affected ≤ 24.
- **Churn/dead-socket class in other files** (e.g. `test_free_team_entitlement`)
  is fixable only via the path-alignment/churn fix — seed-holding alone does not
  cover it.

### Exposed set for regression gate

Success-asserting no-SDK-hold seeds (can fail, must be in the gate):
`test_export_deleted_team_410_registry` (:554), `test_delete_idempotent_registry`
(:595), `TestPurge` registry tests (:634-671).
Masked test (asserts 403, passes regardless — must be pinned to assert the seed
landed): `test_export_requires_owner_registry` (:528).
**Second symptom class (dead-socket ConnectionError/500) — add one cross-file
churn representative to the gate for fix family (b):** `test_free_team_entitlement`
(the #2049-b sibling) or `test_suspension_parity` (as mechanism control); its
success-assertions must survive the path-alignment fix. The gate runs on BOTH
lanes: embedded (carve-out) + docker (`TORTOISE_DB_URI` set, immunity check).

### Fix-phase experiment (redesigned per verifier — discriminates the trigger)

The cycle-1 design ("kill the anchor between seed and request") was wrong: the
seed is already collected by then (harmlessly), so it cannot discriminate the
GC-NOSAVE trigger. Deterministic construction instead:
1. Anchor held (lifespan pin) → seed dropped → drift-evict → `gc.collect()`
   between evict-close and new-anchor connect — **construction only; outcome
   feeds the step-3 discriminator (do NOT assert a kill here: under
   refcount timing the seed is collected at drop while the anchor still
   holds → disconnect-only → data survives, which is itself informative).**
2. Control: `gc.collect()` while the anchor is held → daemon survives, seed
   data intact.
3. Discriminator for the trigger sub-question: force the enter-pin to fail
   (anchor ABSENT at seed-collection time) → deterministic NOSAVE at scope
   exit (supports timing-A) vs data surviving (supports timing-B/cycle-pinned).
   **Pin the sequence: assert survival immediately after scope exit (NO
   gc.collect), then gc.collect() and re-assert.**
4. Instrumentation: spy on `_gc_close` logging (connection count, action taken)
   and/or a churn counter asserting ZERO `_anchor_usable`-driven evictions
   during the gate (after fix b1).
Read `falkordblite` client source for the `CLIENT LIST` count semantics before
interpreting results (per-fix-phase note).
**EXECUTED 2026-09-01 (spike, deleted before merge):** standalone
`TORTOISE_FAST_ATEXIT=1` run (URI unset). Result: **data survives in BOTH
shapes** — anchor-held p1=1 p2=1, anchor-absent p1=1 p2=1. Narrowing: the
close-on-GC NOSAVE does NOT fire from a plain dropped seed at scope exit under
this probe; the 403 requires the **eviction-churn-created 0-other-client
window** (which the b1 pin eliminates) — GC timing per se is not sufficient.
This CONFIRMS the hybrid synthesis (churn windows = necessary condition) and
that the fix is trigger-independent. (macOS teardown semantics often leave the
daemon alive post-NOSAVE — the Linux-CI empty respawn additionally needs the
window, consistent.)

### Fix families (both candidates; decision in solution phase)

- **(a) Hold/return ALL dropped seed SDKs** — the established in-repo pattern
  (f69c775d). Covers `_seed_registry`, `_registry_count`, and TestPurge's direct
  seeds (all call sites must hold or the label is narrower than the required
  fix). Cheap, precedented. Does NOT cover the churn/dead-socket class in
  other files.
- **(b) Align the fixture DB path with `_make_sdk`'s computed path** — kills
  the **churn enabler**, covering the broader 26-27-file churn/dead-socket
  class. TWO variants, separated by risk:
  - **(b1) Fixture-side (PRIMARY, low risk):** set `TORTOISE_DB_PATH` to the
    fixture tempdir in the shared patch helper (and/or a conftest autouse
    fixture) so `_anchor_usable` passes and the #1607 keepalive anchor holds
    process-lifetime. Resolves the root cause **while preserving #1502
    semantics** — per-test tempdirs still drift cross-test, so cross-test
    eviction still fires; only the within-test false positive disappears.
  - **(b2) Production-side (HIGH RISK — treat as anti-pattern documentation;
    do NOT burn a design cycle):** changing
    `_anchor_usable` to validate against the anchor's own created path
    (record `created_path` at creation) instead of the env-computed path. ⚠️
    A naive "compare the anchor's path to its own binding" is a tautology that
    would neuter the #1502 stale-rebind guard (commit `9de006a1`: "the anchor
    keeps serving the PREVIOUS test's graph — dead socket when the daemon
    dies, stale rows while it lingers", with 2 dedicated regression unit
    tests `test_make_sdk_reuses_healthy_anchor` /
    `test_make_sdk_rebinds_stale_anchor`, tests/test_hosted_api.py:3068/3096).
    The stale-rebind test simulates drift by changing `TORTOISE_DB_PATH`;
    the CI patched-shape drifts by BINDING change with CONSTANT env — a
    recorded-arg comparison would deem the cross-test anchor "usable" and
    re-serve the previous test's graph (the #1502 class). **Gate for any b2:
    (i) #1502 stale-rebind regression tests stay green; (ii) new
    "patched-but-alive path" test pinning current churn behavior;
    (iii) explicit invariant: cross-test eviction must still fire when the
    binding drifts while TORTOISE_DB_PATH stays constant (patched-shape
    case); (iv) compare against the anchor's RECORDED creation path — never
    a self-comparison, never env-computed alone.** Expected outcome: b2 fails
    gate (i) in the fixture env → b1 is the only verified root-cause path.

### Falsification check

Definition proven wrong by: a docker-lane (URI set) failure of these tests;
quota-flavored 403 detail; seed surviving AND still 403; failures persisting
after both fix families. **Cheap temporal falsifier:** a run BEFORE 2026-08-19
(#1475 close-on-GC landed, `5e0c7d30`) showing the 403 class would refute the
hybrid. **Temporal corroboration:** the day-cluster (2026-09-01) is explained by
#1475 landing 08-19 + the seed-hold prior-art campaign 08-20→08-23
(f69c775d, #1556/#1588/#1579/#1587, #1612) fixing the identical mechanism
file-by-file — test_export_delete was the straggler. The ~43-min failing-run
runtime is the known slow embedded lane (churn + close-on-GC + exit-time NOSAVE
batch), not a quota/health signal.

### Confidence

- Family level: **90** (three independent verification modes: code reads, git
  history, reproduction).
- Trigger stage: **70** — hybrid hypothesis; the two candidate collection
  timings (refcount-at-scope-exit vs cycle-pinned cyclic-GC, plus the
  enter-pin-best-effort-failure variant) remain OPEN until the redesigned
  fix-phase experiment (deterministic evict-window construction + control +
  enter-pin-failure discriminator, see Fix-phase experiment) discriminates
  them.

## Rejected Framings

| Framing | Verdict | Why |
|---|---|---|
| Runner DB pollution / quota-class (original issue) | Refuted | No quota check exists on the authz path (403 detail = "Requires owner role in team"); cross-run reuse mechanically impossible (fresh VM + fresh container per job, no volumes, per-test tempdirs); `SWEEP_TEAM_STRAYS` confirms dedicated fresh-per-job docker lane |
| Lane misattribution ("test (b)" specific) | Correct but descriptive | LPT puts test_export_delete in half b for #1937/#2049/#2054, but #2054 also failed in half (a) — the class is embedded-lane-wide (tier-2 URI-less shape `CARVE_OUT=1`, `python-ci.yml:430-445`), not half-specific. Docker lane immune (host-mode finalizers no-op + session-nonce graph redirect) |
| Unhealthy runner / HF network / openrouter 401 | Red herrings | `HF_HUB_OFFLINE=1` is intentional + continue-on-error; 401/network noise is unrelated to the authz-403; no runner-health signal participates in the reproduced mechanism |

## External Research (Phase 1.5)

### Axis Research
> **Trigger assessment:** axes low (UX=low, Ontology=low, Architecture=medium).
> Architecture fires on the `_anchor_usable`/keepalive change surface, but the
> fix pattern is **in-repo precedent** (f69c775d, #1497, #1502, #1607, #1556,
> #1579/#1587, #1612 — all documented in code comments, verified) and redislite
> lifecycle semantics are not an externally-documented pattern. No third-party
> deps; no novel pattern. External research not demonstrated — skipped per
> activation rule (codebase-first precedent scan found 3+ examples).

## Wiring Check (Phase 6 — preliminary)

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `tests/test_export_delete.py` `_seed_registry` | test helper | fix family (a) | ⚠️ pending solution |
| fixture path vs `TORTOISE_DB_PATH` | test infra / env | fix family (b) | ⚠️ pending solution |
| `tortoise/hosted_api.py` `_anchor_usable`/`_make_sdk`/`_registry_anchor` | production keepalive core | fix family (b1) primary; (b2) ONLY gated on #1502 regression tests | ⚠️ pending solution |
| embedded lane CI shape (tier-2 CARVE_OUT) | CI config | verification target (no CI change expected unless docker-lane fix needed) | ⚠️ pending solution |
| docker lane (`TORTOISE_DB_URI` set) | CI config | must remain immune (regression gate runs both lanes) | ⚠️ pending solution |
| 26-27 patch-fixture test files | test infra | churn fix (b1) covers; seed-hold (a) is file-local to test_export_delete | ⚠️ pending solution |
| cross-file churn representative (test_free_team_entitlement / test_suspension_parity) | test infra | regression-gate member for fix (b) | ⚠️ pending solution |

## Review Cycle Log

### problem-verify — Cycle 1
- Verifier A: P0=0, P1=2, P2=3, P3=1, P4=1 — P1-1: "observed set = exposed set"
  overclaim (3 no-SDK-hold tests + TestPurge; requires_owner is masked);
  P1-2: trigger disagreement merged, not reconciled (need hybrid synthesis).
- Verifier B: P0=0, P1=0, P2=5, P3=2 — supplied the synthesis (explicit close ≠
  NOSAVE; seed GCs at count ≥ 2 in pinned-anchor trace; hybrid = churn windows +
  GC-NOSAVE), corrected blast radius (19 files), flagged missed fix family (b),
  widened symptom family (ConnectionError sibling), prior art citations.
- Controller action: FIXED both P1s — amended the mechanism to the hybrid
  synthesis, corrected blast radius, added fix family (b), widened symptoms,
  qualified fixture mode, added prior art. Incorporated P2s (exposed-set
  classification, TestPurge, repro-protocol note, `_anchor_usable` line refs).
### problem-verify — Cycle 2
- Verifier A: P0=0, P1=0, P2=3, P3=2 — APPROVED. P2-1: trigger-timing
  sub-claim "cyclic GC inside an eviction window" contradicted by probes
  (refcount-at-scope-exit vs cycle-pinned — recorded as open sub-question;
  alternative trigger: enter-pin `_get_proj()` best-effort failure on loaded
  runners → seed last-client at scope exit). P2-2: blast radius 19 is an
  undercount vs the doc's own pattern definition. P2-3: regression gate
  missing the second symptom class (ConnectionError/500).
- Verifier B: P0=0, P1=1, P2=3, P3=2, P4=1 — mechanism code-verified at every
  link; no additional kill path. P1: production-side (b) variant is a
  tautology that would neuter the #1502 guard (9de006a1) — separated into
  (b1) fixture-side primary / (b2) gated production-side.
- Controller action: FIXED the P1 (b1/b2 split with #1502 gate); folded in all
  P2s (blast radius re-verified at 26-27; cross-file churn representative
  added to the gate; day-cluster/43-min disposed via #1475 timeline;
  experiment redesigned to a deterministic construction with control +
  trigger discriminator + instrumentation); P3s (line refs export_team:7840 /
  delete_team:8909; family (a) widened to ALL dropped seeds incl. TestPurge +
  _registry_count; falkordblite `CLIENT LIST` source cited).
### problem-verify — Cycle 3 (FINAL)
- Verifier A: P0=0, P1=0, P2=2, P3=3, P4=1 — APPROVED, ready for solution
  diamond. P2s: missing #1950 precedent (now added — proves fix (b1) in-repo);
  0/1/2-client parenthetical contradicted verified `_gc_close` count>1
  semantics (corrected: killing window = 0-other-client instant).
- Verifier B: P0=0, P1=0, P2=2, P3=1 — APPROVED. Confirmed the cycle-2 P1
  gate is functional (grep of 9de006a1 yields exactly the two #1502 regression
  tests; recorded-self-path semantics would fail `test_make_sdk_rebinds_stale_anchor`).
  P2s: stale confidence-section experiment reference (fixed); b2 gate needs
  the constant-env drift invariant (added: cross-test eviction must still fire
  when binding drifts while TORTOISE_DB_PATH stays constant; b2 expected to
  fail gate (i) → treated as anti-pattern documentation).
- Controller action: incorporated all P2s/P3s (0/1/2-client → 0-other-client
  killing instant; #1950 precedent + coverage split 27/3/≤24; line refs
  7840/8909; confidence text; experiment steps construction-only + pinned
  sequence; inline-variant list swap test_agent_ops_pack → test_github_connect;
  TestPurge ref 633-671).
- **GATE RESULT: problem-verify PASSED — no P0/P1 across cycles 1-3.**
  (Trigger timing sub-question deliberately OPEN with discriminator.)

---

# Solution Diamond (Phase 4/5 + solution-verify cycles)

## Chosen approach (solution-converge, amended by solution-verify cycle 1)

**Approach A (test-file-local, precedented) + one cross-file churn representative.**
Files touched: `tests/test_export_delete.py` + `tests/test_free_team_entitlement.py`
(client :59-80, reg_client :244-270). **No production code, no CI config changes.**
(Framing label corrected: NOT "test_export_delete only" — it is "test_export_delete
+ one cross-file churn representative", the gate-mandated second symptom class.)

Rationale (quality-over-convenience): (b1) kills the deterministic churn enabler;
seed-hold (family a) neutralizes the GC-NOSAVE trigger INCLUDING the
enter-pin-best-effort-failure variant; restore-close fixes the #1950-documented
leak. Both halves are in-repo prior art (#1950 pin verbatim; _SEED_SDKS /
f69c775d). B-now rejected as premature (B wave 1 ≡ A content; helper extraction
for 2 call sites before the 24-file migration reveals heterogeneous shapes); the
follow-up IS B. C1/b2 rejected (anti-pattern, would neuter #1502 guard); C2
filed as optional defense-in-depth follow-up.

**Escalation trigger (amended — triage before HALT; structurally post-merge):**
any churn-class flake (403-after-seed or dead-socket) observed in a file OTHER
than the two migrated ones → TRIAGE FIRST: (a) confirm the churn signature (403
detail "Requires owner role in team" + empty-respawn behavior, or the
dead-socket ConnectionError class); (b) ATTRIBUTION: show the flake is NOT
caused by this merge's own changes (fix touches tests only, but the seed-hold
resource shift can mimic a churn signature in other files by slowing runs —
check peak RSS/daemon counts); (c) only then pull the systemic B migration
forward as wave 0; a different-root-cause failure does NOT expand scope. ⚠️ The
trigger can only FIRE post-merge (the ×10 gate runs only the 2 migrated files;
pre-merge tier-2 legs select only changed files) — so specify a minimum
post-merge embedded full-matrix observation window at close-out (the B-pull
decision input). HALT = human-input checkpoint per human-input-framework
(pause, surface, resume on decision) — distinct from commit-workflow's
post-PR escalation gate.
**Lint tripwire (P2, devil's advocate → P1 fix at re-review → MOVED to follow-up,
final decision):** the tripwire does NOT ship in this fix. ⚠️ Rationale (final
verifier, P1-A): any implementation requires editing `tests/conftest.py`, which
∈ `SHARED_MODULES` (`tools/ci_selection.py`) → the fix PR would flip to
`full=true` (docker matrix) — breaking acceptance (i) (tier-2 embedded streak)
and removing the migrated files' only CI embedded coverage (they are not in the
carve-out leg; post-merge pushes are docker). It moves to follow-up 1 wave 3
with: (a) ship-time baseline allowlist of the ~27 pre-existing unpinned modules
(24 churn-shaped + 3 patch-only no-clear: pack_manifest_store,
pack_manifest_store_extraction, sdk_props_coercion; re-grep at ship time with
the exact pattern `patch(TortoiseSDK.__init__) ∧ ¬TORTOISE_DB_PATH-pin`);
(b) **runtime allowlist membership as the PRIMARY new-violation mechanism
(pattern ∧ ∉ allowlist → fail) — NOT git-diff** (all CI test jobs use shallow
fetch-depth:1; a git error at collection would blanket-red the suite; git-diff
is at most a local refinement with warn-only degradation); (c) warn
(non-blocking) for baseline files via a visible channel (pytest warning summary
or dedicated log line — CI output is buffered to /tmp/pytest.log); (d) the
allowlist lives in a `config/` data file (config changes select core surface,
tier-2, `full=false` — a conftest-inline allowlist would flip every B-wave PR
to full-matrix); (e) baseline flips to fail at wave 3. This PR instead relies on
the deterministic counter + masked pin + success-asserts (acceptance 5).

## Implementation plan (amended)

**Step 0 — Fix-phase experiment (trigger discriminator; spike file deleted before
merge).** ⚠️ **LANE CORRECTION (P2, review gates): the spike MUST run on the
EMBEDDED carve-out shape — `TORTOISE_TEST_CARVE_OUT=1`, URI unset — NOT with
`TORTOISE_DB_URI` set.** Under a supported URI the epic #1647 projection
redirect flips explicit-path constructions to server mode (frame-gated), so the
constructions never hit the embedded close-on-GC path and the NOSAVE-vs-survive
discriminator becomes vacuous. Docker is only for Step 5's lane-immunity check.
Read `falkordblite`/`redislite` client source for `CLIENT LIST` count semantics
FIRST. Run the scoping doc's redesigned construction verbatim: anchor
held → seed dropped → drift-evict → `gc.collect()` between evict-close and
new-anchor connect (construction only — NO kill assert: refcount-timing outcome
is itself informative); control (`gc.collect()` with anchor held → survives);
enter-pin-failure discriminator (anchor absent at seed-collection → NOSAVE at
scope exit vs data survives); pinned assert sequence: survival immediately after
scope exit (NO gc.collect), then gc.collect() + re-assert; `_gc_close` spy
(count, action) + eviction counter. Also unit-check the counter's
`_db_path`-vs-computed classification on one constructed drift pair vs one
probe-failure pair BEFORE the machinery is committed. Record the trigger
sub-question resolution in §Fix-phase experiment. Prereqs: carve-out shape
(CARVE_OUT=1, URI unset) + `uv sync`.

**Step 1 — RED (instrumentation + masked pin, no fix yet).**
- Eviction counter in `reg_client`: install AFTER `_patch_tortoise_sdk_init`
  (which clears). **⚠️ P1 fix (cycle 2) — counter mechanics (FINAL):**
  - Use a `dict`-subclass replacement of `ha_mod._FALLBACK_KEEPALIVE`
    overriding `pop()` (dict instance methods cannot be monkeypatched in
    place; all 6 production refs are module-global lookups → they see the
    replacement; `clear()` clears in place BEFORE install, so the counting
    dict starts empty; no stray refs to the original dict).
  - **Discriminator reads `value._db_path`, NOT `proj._path`:** both eviction
    sites call `anchor.close()` BEFORE pop (hosted_api.py:160-162, :203-205)
    and `close()` nulls `self._proj` (sdk.py:6713) — at pop time
    `proj._path` is None in ALL eviction cases. `_db_path` survives close()
    (set in __init__ sdk.py:1110-1130; equals the path `_proj` was built
    from). Classify at pop: replicate `_make_sdk`'s env-path computation
    (`TORTOISE_DB_PATH` else `/data/tortoise.db`, with the makedirs-OSError
    fallback to `tempfile.gettempdir()/tortoise.db`, and `:memory:`
    special-case as same-path); count ONLY when `value._db_path !=
    computed_path` (path-drift); equal → probe-failure/benign → do NOT
    count; no `_db_path` → do NOT count. Post-Step-2 the env is pinned so
    the computed path is deterministic (== db_path) — the committed 0-guard
    can only see probe-failure pops, which the discriminator excludes.
  - **Unwrap mechanics (P2 fix): disable-flag, never swap-then-close.** Keep
    the counting dict installed as the module attr; set `enabled=False`;
    let `_restore_sdk_init`'s `_close_keepalive_anchors` pop+close the REAL
    anchors (pops uncounted — restore-time pops must never count, else every
    test reds); then restore the original dict. (Swap-before-close strands
    the anchors in the counting dict → the #1950 leak this fix is built on.)
  - **Exact fixture sequence (pinned):** install-after-patch → `with
    TestClient` → `yield` → **assert against a phase-toggle constant
    `_EXPECTED_DRIFT_EVICTIONS` with EXPLICIT operator semantics: RED (G1)
    asserts `evictions >= _EXPECTED_DRIFT_EVICTIONS` (constant 1 — tests
    routinely accumulate 2+ drift-pops: enter purge → `_require_owner` →
    export/`_make_sdk` → `_registry_count`; `==` would spuriously red G1);
    GREEN (from Step 2 on) asserts `evictions == 0`** (a module constant,
    not an inline edit, so the toggle can't be missed at Step 2/3; the
    counter object itself named `_DRIFT_EVICTION_COUNT`) → `enabled=False`
    → `_restore_sdk_init` (closes anchors, pops uncounted) → restore
    original dict — all inside the fixture's existing try/finally, in that
    exact order (teardown-assert failure attribution: a body failure +
    teardown error reports together during RED — acceptable, but the
    counter-disable/restore MUST run in `finally` so the counting dict
    never stays as the module attr).
  - Annotate: embedded-lane signal; docker lane is structural immunity (URI
    branch never touches the keepalive), not a measured 0.
  - **⚠️ Warn-only probe-failure counter (P2, devil's advocate):** alongside
    the gating 0-guard, add a NON-gating count of probe-failure pops (path
    equal but evicted anyway — the enter-pin `_get_proj()`-failure class,
    which the discriminator deliberately excludes). Report it (warn/log) in
    the streak runs so the enter-pin churn rate on real runners is OBSERVED,
    not asserted away — acceptance-5's "zero path-drift evictions" is NOT
    "no churn" and must not overclaim.
- Masked-pin: `test_export_requires_owner_registry` (:526-531) gains
  `assert _registry_count(db_path, "Team", "reg-team-1") == 1` after seed.
  **⚠️ ordering dependency:** `_registry_count` constructs+drops an SDK — until
  Step 3's hold lands, the assert's own dropped SDK is the drop class; move the
  assert to AFTER Step 3 (or add the `_SEED_SDKS` hold in Step 1 alongside) so
  RED failures are attributable to the counter, not the assert's own SDK.
- Gate G1 (RED): counter ≥1 deterministically on the embedded carve-out lane.

**Step 2 — GREEN (b1 pin + restore-close).**
- `sb_client` + `reg_client`: `os.environ["TORTOISE_DB_PATH"] = db_path` right
  after `_patch_tortoise_sdk_init(db_path)` — verbatim #1950 pin
  (tests/test_hosted_api.py:174-202 rationale) + #1950's companion close-at-
  restore lesson (:135-141 — clear-without-close leaked anchors). Pop-then-close
  order in restore; both fixtures share ONE restore so neither leaks env.
- `_restore_sdk_init`: `os.environ.pop("TORTOISE_DB_PATH", None)` →
  restore `__init__` → **`_close_keepalive_anchors(ha_mod)`** (deterministic
  close per anchor — SHUTDOWN SAVE, not GC-timed) → clear `dependency_overrides`.
- **`_close_keepalive_anchors` source (P2 fix + P3 review refinement):** it does
  NOT exist in production; defined only in tests/test_hosted_api.py:144
  (`(ha_mod)` signature) and tests/test_capture_session.py:1724 (no-arg —
  divergent). **Use a LOCAL verbatim copy with a source-pin comment
  (`# mirrors tests/test_hosted_api.py:144 — keep in sync`) in BOTH files
  (Step 2 AND Step 4 must pick the SAME mechanism)** — matches the repo's
  dominant self-contained-fixture-helper convention and avoids the
  first-ever cross-test import of the 3,300-line test_hosted_api module.
  True single-source-of-truth dedup happens in follow-up B wave 1.
- Document (one line each, P3): supabase-mode no-breakage (authz is
  control-plane-only, no SDK; boot purge early-returns so no anchor at enter);
  #1502 tests unaffected (they monkeypatch TORTOISE_DB_PATH in-test, overriding
  the pin; pin popped at teardown so nothing leaks cross-file).
- Gate G2: fresh-context verifier on the fixture change (#1502/#1950 invariants).

**Step 3 — seed-hold (family a).**
- Module `_SEED_SDKS: list[TortoiseSDK] = []` (test_suspension_parity:156
  precedent, typed); `_seed_registry` (:195-214) and `_registry_count` (:219-230)
  append their SDKs (append BEFORE the writes/return — precedent
  suspension_parity:163); keep `-> None` signatures; all 9 _seed_registry call
  sites + _registry_count sites inherit the hold. Verify inline seeds already
  hold locally (test_export_uses_stored_graph_name :534, cascade :563, revoke
  :604, purge :662 — no change needed).
- **⚠️ Close scope (P2, devil's advocate): FUNCTION-scoped close, not
  session-scoped.** A module list held to session end keeps ~18 held SDK
  CLIENTS alive (10 `_seed_registry` + 8 `_registry_count` — one redislite
  SERVER per test tempdir shared by all its SDKs), peaking at ~20-31 servers
  (each a real redis-server fork, ~5-15 MB RSS + FD) — a NEW resource-pressure
  profile that could induce the one external-death class the fix cannot prevent
  (daemon OOM/pressure-kill → identical empty-respawn 403). Close each test's
  seeds at that test's teardown (per-test tempdirs make cross-test reads
  impossible): `_close_seed_sdks()` (while _SEED_SDKS: pop().close()) in the
  fixtures' `finally` — **pinned position: AFTER `_restore_sdk_init` in the
  same `finally`** (final seed close = deterministic last-client SAVE shutdown;
  runs on body-failure paths; seeds are never dict members so no counter
  interference in either order) — collapses peak daemons to ~2 concurrent
  (shared_proj session server + current test's server).
  **Measurement (P2 fix, re-review): pin the mechanism** — best-effort
  NEVER-FAIL fixture-level sampler in the 2 files (peak RSS via
  resource/psutil if importable, else skip silently) + live-server count at
  session end; record in the streak evidence note. Also measure the runtime
  delta of the added per-test server shutdown (SAVE + pid-exit poll, typical
  ≤1s, worst-case ~10s per `_cleanup` wait loop) against the pre-fix baseline
  on the ×10 streak.
- Now the masked-pin assert from Step 1 lands (its SDK is held).
- Gate G3: full gate green ×10 embedded (stability evidence).

**Step 4 — cross-file churn representative (`tests/test_free_team_entitlement.py`).**
- `client` (:59-80, supabase) + `reg_client` (:244-270, registry): same b1 pin +
  close-at-restore migration (they currently clear-without-close — the leak
  pattern).
- **Coverage statement (P3):** the env pin is process-wide and covers TestPurge's
  direct `ha_mod._purge_deleted_teams()` calls (no TestClient involvement); the
  enter-time lifespan purge runs after the pin so the enter anchor is created
  pinned.
- Gate G4: verifier on the representative migration; gate set green both lanes.

**Step 5 — full regression gate, both lanes.**
- Embedded (carve-out): `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_export_delete.py tests/test_free_team_entitlement.py`
- Docker (immunity): `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest ...`
  — ⚠️ annotation (CORRECTED per review gates — P2): docker-lane runs of these
  two files DO exercise the URI/server lane of the DATA PLANE via the
  projection redirect (neither file is in TEST_NO_REDIRECT_STEMS;
  projection/__init__.py:568-650 flips explicit-path constructions to derived
  server graphs under URI+TEST_MODE) — so the docker gate IS real URI-path
  data-plane coverage. The keepalive-ANCHOR branch never engages (URI branch
  early-returns) — the counter's docker 0 is NOT anchor coverage. Full-matrix
  URI evidence still per Step 6 (ii).
- Also append the #1502 regression pair (P3, codebase review — they never run
  in CI: embedded_only + slow_files + not in carve-out) to the embedded gate
  AND acceptance 3:
  `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_hosted_api.py -k "make_sdk_reuses_healthy_anchor or make_sdk_rebinds_stale_anchor"`
  — makes G2 machine-checkable (zero-production-change makes this low-risk).
- ×10 repeated embedded runs for stability; record runner IDs + peak RSS +
  live-server count each run (P2 — makes the resource hypothesis falsifiable;
  runner IDs needed for external-death attribution).
- Gate G5: all green, ×10 embedded, both lanes; zero churn-class flakes.

**Step 6 — CI streak + follow-ups.**
- **⚠️ P1 fix — acceptance re-scope:** the fix PR (test files + docs) selects
  tier-2 `full=false` (tools/ci_selection.py; tests/test_export_delete.py is
  api-surface, config/ci-surfaces.yml:58) → the PR gets ONLY tier-2 embedded
  runs; full-matrix docker runs happen on post-merge main pushes + the nightly
  schedule. Amended acceptance:
  (i) **10 consecutive green TIER-2 EMBEDDED runs on the PR** (the actual
  failing lane);
  (ii) docker-lane evidence = the merge's own post-merge full run + observed
  consecutive full-matrix runs on subsequent main pushes/nightlies (state count
  + observation window at close-out);
  (iii) no `workflow_dispatch` added (plan excludes CI changes).
- Streak reset semantics (P3): any failure resets to 0; known-unrelated lanes
  (HF/OpenRouter continue-on-error noise — disposed in the problem diamond)
  excluded with evidence note. Whole-run green counts; an UNRELATED flaky test
  in another tier-2 file resets the streak — at close-out use an evidence-based
  re-run protocol (flaky = passes on ≥1 of ≤2 isolated re-runs → WARN + record,
  continue — test-debt gate semantics). Per-PR concurrency cancels in-flight
  runs (python-ci.yml:104-110 concurrency group) — space pushes or use UI
  re-runs (which don't trip cancel-in-progress) so runs are consecutive; a
  cancelled run is not a failure and does not reset the streak.
- **⚠️ Streak classification (P2, devil's advocate): the
  masked-assert-then-403 class (seed verified present via the masked-pin
  assert, then 403 later in the SAME test) = EXTERNAL-DEATH falsifier (the
  fix's one uncovered class — daemon killed by resource pressure, not
  GC-NOSAVE), NOT a reset-to-0 flake.** Post-fix, a masked seed-assert failure
  during ×10/streak → triage per escalation attribution (peak RSS/daemon
  counts) before classifying; assert-PASS + later 403 = external-death
  falsifier → record + escalate per the trigger, and C2 durability + resource
  controls become P0. Classify explicitly in the streak protocol.
- Gate G6: commit-workflow review gate + streak evidence.

## Acceptance criteria (amended — tied to O/I/T)

1. Root-cause writeup: scoping doc problem diamond VERIFIED (3 cycles) +
   fix-phase experiment results recorded (trigger sub-question resolved/narrowed).
2. Lane determinism: 10 consecutive green tier-2 embedded CI runs on the fix
   branch; docker-lane evidence per Step 6 (ii).
3. Suite integrity: tests/test_export_delete.py 31/31 on embedded carve-out AND
   docker lanes; gate set (exposed :554/:595/TestPurge + masked :528 +
   representative test_free_team_entitlement) green both lanes ×10 embedded.
4. Isolation preserved: no new shared state; per-test tempdirs + per-fixture
   pins; cross-test eviction still fires; #1502 semantics untouched (zero
   production code changed).
5. Root-cause fix evidence: zero mid-test path-drift anchor evictions in
   registry-mode fixtures (the deterministic guard).
6. Follow-ups filed (post-merge, after the full-matrix evidence the waves
   cite — acceptance 6 verified at close-out+1; or pre-file as drafts with
   merge-blocking noted).

Spike file (Step 0): place under `spike/2090_trigger_discriminator.py`
(tracked dir exists; `spike/` ∈ NON_PYTHON_PREFIXES so it can't influence PR
selection); deletion lands in the FINAL commit (or keep untracked so it never
ships). HALT (escalation) = human-input checkpoint per
human-input-framework (pause, surface, resume on decision) — distinct from
commit-workflow's post-PR escalation gate. Streak flaky boundary cites the
test-debt gate: flaky = passes on ≥1 of ≤2 isolated re-runs → WARN + record,
no block; UI re-run is the consecutive-run mechanism (doesn't trip
cancel-in-progress, python-ci.yml:104-110).
**Docs:** add a row to `docs/00_index.md` for this scoping doc in the fix PR
(following the recent-convention row format: `Test (b) lane 403/export-delete
scoping (#2090)` → file).
**Enter-pin-failure rationale (CORRECTED, P3 devil's advocate):** the fix
works for the enter-pin-`_get_proj()`-failure variant via **path-aligned reads
+ seed-hold**, NOT anchor reuse — under that variant the enter anchor has
`_proj=None` → `_anchor_usable` False → evict+recreate every call (the pin
cannot fix a `proj is None` failure); seed-hold keeps the data alive and the
pin keeps reads on the same daemon. The residual per-call churn in that
variant is invisible to the 0-guard (probe-failure pops excluded) — observed
by the warn-only counter instead.
**a-only tradeoff (P3 second-model):** family (a) alone (seed-hold) would stop
the bleeding deterministically (held seeds never fire the finalizer) but
forfeits acceptance-5's deterministic zero-eviction proof and defers the
second symptom class — b1+counter is the minimal construction converting a
racy-flake fix into a deterministically verifiable one.
**Exposed set (widened, P3 devil's advocate):** `test_delete_cascade_registry`
(:561) and `test_delete_revokes_key_auth_registry` (:604) assert 202/401 after
seeds and are equally exposed (harmless — the gate runs the whole file).
**Temporal falsifier — EXECUTED (P3 devil's advocate):** #1475 close-on-GC
landed 2026-08-19 10:53 (5e0c7d30). Failing PRs' CI runs all postdate it:
#1937 (created 08-28, merged 09-01 — CI 08-28→09-01), #2049 (08-30), #2054
(08-30). NO pre-08-19 403 exists → hybrid NOT falsified; consistent.

## Follow-up issues to file (not absorbed)

1. **Systemic churn-class migration (B waves)** — shared `patched_tortoise_sdk(db_path)`
   context manager in **`tests/_http_fixtures.py` (a NEW sibling module — NOT
   `tests/_embedded.py`, whose charter is centralized embedded CONSTRUCTION +
   the #1647 lane machinery; conftest eagerly imports it in every session,
   and the patch-fixture family belongs beside `tests/fake_control_plane.py`
   as hosted-api test doubles)**; migrate in waves with PER-WAVE ISSUES: wave 1 =
   shared-helper users = test_export_delete + test_suspension_parity, PLUS
   **wave 1b: the remaining named-helper files (action_endpoints_dual_auth,
   agent_ops_pack, auth_flip, body_cap_sweep, commit_endpoint,
   domain_validators, import_endpoint, invites_email_http, invites_http,
   oauth_mcp, onboarding_endpoints, onboarding_health_flip,
   session_extraction_modes, session_key_http, writer_inventory) + the 3
   patch-only no-clear files (pack_manifest_store,
   pack_manifest_store_extraction, sdk_props_coercion — visible via the
   tripwire baseline allowlist)** (test_free_team_entitlement
   is an INLINE variant, NOT a shared-helper user: it defines `_patched` at
   :64-66/:252-254), wave 2 = the ~8 inline variants
   (test_index_docs_api, test_lme_m6_evidence, test_abuse_integration,
   test_github_connect, test_github_index_lifecycle, test_capture_session,
   test_dr_endpoints, test_free_team_entitlement — note capture_session already
   pins, dr_endpoints already holds _SEED_SDKS), wave 3 = verification + the
   **lint tripwire** (moved here from THIS fix — see Lint tripwire paragraph:
   config/ data file allowlist, runtime-membership enforcement, warn-for-
   baseline via visible channel; baseline flips to fail at wave 3). Each wave issue
   cites the #1950 verbatim pin + close-at-restore companion so it is
   self-contained. Regression gate = #2090 gate + per-wave exposed sets
   on both lanes.
2. **C2 durability hardening (optional)** — SAVE/BGSAVE cascade through
   `_registry_anchor` so embedded daemon death loses no registry mutations.
   Promote if production durability evidence (not test flake) emerges; otherwise
   close with measured evidence.
3. **Not filed:** C1/b2 path-keyed liveness — anti-pattern (would neuter #1502
   guard); documented in this scoping doc; no design cycle.

## solution-verify — Cycle 1 log
- Verifier A: P0=0, P1=0, P2=4, P3=5 — VERIFIED. P2s: escalation trigger for
  B-pull-forward; _close_keepalive_anchors source; masked-pin/seed-hold
  ordering; follow-up wave boundaries.
- Verifier B: P0=0, P1=2, P2=2, P3=4, P4=1. P1-1: eviction counter counts
  restore-time pops (fails 100% of runs) unless unwrap order specified — FIXED
  (counter unwrapped before restore; assert at yield-resume; dict-subclass;
  path-drift-only counting). P1-2: "10 full-matrix green runs" unachievable
  (PR selects tier-2 only) — FIXED (tier-2 streak + post-merge docker evidence).
  P2s: _SEED_SDKS unbounded leak (added session-close fixture); committed
  0-eviction assert probe-failure flake surface (path-drift-only counting).
- Controller action: FIXED both P1s; folded all P2s/P3s (label correction,
  TestPurge coverage statement, supabase-mode no-breakage, gates enumeration,
  streak semantics, docker annotation, spike prereqs, precedent citation).
- Re-dispatching both verifiers (cycle 2)…

### solution-verify — Cycle 2
- Verifier A: P0=0, P1=1, P3=2 — NOT APPROVED as written. P1 (converge-quality):
  the path-drift discriminator is mechanically broken — both eviction sites
  close() BEFORE pop (hosted_api.py:160-162/:203-205) and close() nulls `_proj`
  (sdk.py:6713) → at pop time `proj._path` is None in ALL cases; the
  discriminator must snapshot the path at get()-time or use `_db_path` (which
  survives close; sdk.py:1110-1130). P1-2 (acceptance re-scope) verified
  RESOLVED (test_export_delete is api-surface config/ci-surfaces.yml:58 → tier-2
  full=false; full-matrix only post-merge push/schedule).
- Verifier B: P0=0, P1=1, P2=2, P3=2 — NOT execution-ready. Same P1
  (discriminator: use `_db_path` which survives close, or hook `_anchor_usable`
  to tag the drift reason; RED determinism confirmed — every reg_client test
  does ≥1 registry op, counter ≥1 pre-fix deterministic; install must precede
  TestClient enter). P2-1: unwrap ambiguity — swap-before-close strands anchors
  in the counting dict (the #1950 leak) → disable-flag, never swap-then-close.
  P2-2: escalation trigger needs triage before HALT (confirm churn signature;
  different-root-cause flake must not expand scope). P3s: streak whole-run
  green (unrelated flaky test resets — evidence-based re-run protocol);
  per-PR concurrency cancels in-flight runs (spacing pushes; cancelled run
  ≠ failure).
- Controller action: FIXED the P1 (discriminator reads `value._db_path`
  surviving close, replicating _make_sdk's env-path computation incl. /tmp
  fallback + :memory: special-case; count only when _db_path ≠ computed_path;
  no _db_path → don't count); FOLDED P2s (disable-flag unwrap with exact
  pinned fixture sequence install→TestClient→yield→assert→enabled=False→
  restore; escalation triage → HALT only on confirmed churn signature = human
  gate); FOLDED P3s (streak re-run protocol; concurrency note).
- Re-dispatching both verifiers (cycle 3 — final)…

### solution-verify — Cycle 3 (FINAL)
- Verifier A: P0=0, P1=0, P2=0, P3=1, P4=2 — APPROVED, execution-ready.
  `_db_path` discriminator verified end-to-end (init precedence sdk.py:1108-1131,
  close-survival, both eviction sites, env-path replication exact incl. OSError
  fallback + :memory: guard at hosted_api.py:121); disable-flag unwrap leak-free;
  URI-mode residual edge none (URI branch never touches keepalive).
- Verifier B: P0=0, P1=0, P2=2, P3=5, P4=2 — APPROVED. P1 CONFIRMED FIXED;
  `_db_path` can never be None for dict members (only embedded-branch anchors
  enter the dict); RED determinism holds (every reg_client test → registry op
  → ≥1 drift-pop pre-pin). P2s: docker-gate framing annotation (fixture-forced
  embedded on docker — URI evidence = post-merge full matrix) — FOLDED;
  phase-toggle constant for the counter assert — FOLDED. P3s: spike placement,
  follow-up timing (post-merge), streak flaky boundary (test-debt gate
  citation), HALT = human-input checkpoint distinction, discriminator abspath
  helper, TestPurge "no TestClient involvement" rewording — FOLDED into the
  plan.
- **GATE RESULT: solution-verify PASSED — no P0/P1 across cycles 1-3.**
  Plan execution-ready (executing-plans → commit-workflow).

## Phase 5.6 — Second-Model Coherence Check (DONE — deepseek-v4-pro)
No P0/P1 — cross-diamond coherence strong. P2: counter assert operator
(`>=` for RED, `== 0` for GREEN — fixed in Step 1). P3s: a-only tradeoff
sentence (added); docker acceptance unmeasurable on PR (accepted — docker
evidence post-merge per Step 6-ii); 10× streak thin (ranked: acceptance-5
counter = primary determinism evidence, streak = corroboration); P4s: refuted
framing softened; experiment coupling wording; import coupling (resolved by
local-copy decision).

## Phase 7 — Parallel Review Gates (DONE — 3 agents + second-model; epic
alignment skipped — standalone)
All agents: **no P0/P1**. Codebase & Docs: P2-1 docker-gate annotation
corrected (projection redirect → real URI data-plane coverage; keepalive
anchor branch never engages); P3s: follow-up wave double-count (fixed),
_SEED_SDKS ~17 not 60-100, docs/00_index.md row (added), #1502 pair added to
embedded gate, counter teardown attribution (pinned). UX Patterns (adapted):
P2-1 follow-up home = tests/_http_fixtures.py (fixed); P3s: local copy vs
import (local copy chosen, same mechanism in both files), keep the counter
(pin replicated computation + spike unit-check), spike lane correction (fixed:
embedded carve-out, not docker). Devil's Advocate: P2-1 function-scoped seed
close (fixed); P2-2 escalation trigger post-merge-only + attribution/resource
checks + lint tripwire INTO this fix (fixed); P2-3 masked-assert-then-403 =
external-death falsifier classification (fixed); P2-4 warn-only probe-failure
counter (fixed); P3s: temporal falsifier EXECUTED (holds — all failing PRs'
CI postdates #1475 08-19), enter-pin rationale corrected, docker gate restated,
exposed set widened.

## Phase 8 — Finalize (re-review cycle + plan comment pending)

### Phase 7 — Re-review cycle (2 agents)
- Agent 1 (mechanism/codebase): conditional PASS — 1 new P1 (lint tripwire scope:
  blanket tripwire reds ~25 pre-existing unpinned files), 2 P2s (wave
  decomposition leaves ~15-17 named-helper files unwaved; #1502 pair claimed in
  gate but absent from gate text), P3s (runner IDs, masked classification
  operationalization, seed-close placement, daemon-count arithmetic).
- Agent 2 (devil's advocate): 1 P1 (same tripwire scope) + 1 P2 (measurement
  mechanism for peak RSS/live-server unspecified — attribution depends on it),
  P3s (seed-close pinned after restore; supabase asymmetry rationale;
  skip+teardown interplay; #2054 merged 08-31 not 08-30; "daemons survive
  0-client windows" misdescribes redislite last-client-close SAVE shutdown).
- Controller: FIXED the P1 (tripwire scope clause) → final verifier found 2 NEW
  P1s (conftest hook flips PR to full-matrix via SHARED_MODULES; git-diff NEW-
  module detection inoperable in shallow CI) → MOVED the tripwire to follow-up 1
  wave 3 (runtime-membership enforcement, config/ allowlist file, warn channel);
  FOLDED P2s (measurement pinned: best-effort never-fail sampler + server count
  at session end + runtime-delta measurement; wave 1b enumeration; #1502 pair in
  gate + acceptance 3; masked-assert-then-403 classification operationalized;
  _close_seed_sdks after _restore_sdk_init; supabase reg_client-only rationale
  documented).
- **GATE RESULT: all review gates PASSED — no unresolved P0/P1. Plan
  execution-ready.**

## Phase 8 — Finalize

### Confirmed Problem
Within-test embedded-lane data loss → intermittent 403s in tests/test_export_delete.py
registry tests on the CI tier-2 URI-less lane. Mechanism: fixture patch + no
TORTOISE_DB_PATH pin → keepalive anchor evicted per call (0-other-client
windows) + dropped-seed GC-NOSAVE → redislite daemon death → empty respawn →
_require_owner 403 before 410/200. Docker lane immune. Issue's "runner DB
pollution / quota-class" framing refuted (no quota check; fresh containers;
per-test tempdirs). Family confidence 90; trigger timing (refcount-vs-cyclic-GC)
open at 70 — resolved by the Step-0 experiment.

### Verification Gates
- problem-verify: 3 cycles, clean (P1s fixed in cycle 1; P2s incorporated).
- solution-verify: 3 cycles, clean (P1s fixed in cycles 1-2; final cycle
  approved).
- Phase 5.6 second-model coherence: clean (no P0/P1).
- Phase 7 parallel review gates (3 agents + re-review cycle): no unresolved
  P0/P1.

### Plan
See Implementation plan (Steps 0-6, gates G1-G6) above. Files: 2 test files +
docs. No production/CI changes.

### Clarifications
None — no questions qualified (problem well-specified by issue + verified root
cause).

### External Research (Phase 1.5 artifact)
### Axis Research
> **Trigger assessment:** axes low (UX=low, Ontology=low, Architecture=medium).
> Architecture fires on the _anchor_usable/keepalive change surface, but the fix
> pattern is in-repo precedent (f69c775d, #1497, #1502, #1607, #1556,
> #1579/#1587, #1612, #1950 — all documented in code comments, verified);
> redislite lifecycle semantics are not an externally-documented pattern; no
> third-party deps. Justified-skip per activation rule (codebase-first precedent
> scan found 3+ examples).

### Integration Docs
No new deps (falkordblite 0.10.0 vendored; redislite client semantics per
`.venv/lib/python3.12/site-packages/redislite/client.py:183-200` CLIENT LIST).

### Rejected Alternatives
- Runner DB pollution / quota (original issue) — refuted (no quota code;
  fresh VM + fresh container per job; per-test tempdirs). Pollution intuition
  survives only as within-run path-drift (the confirmed mechanism).
- Lane misattribution "test (b)" — embedded-lane-wide (tier-2 URI-less shape),
  not half-specific (#2054 also failed in half a).
- Fresh DB per run / runner health checks (issue's proposed fix 1+2) — no-ops
  (already fresh) / red herrings (no health signal in the mechanism).
- Approach B (systemic shared helper) now — premature (B wave 1 ≡ A content;
  follow-up IS B; escalation trigger pulls it forward on evidence).
- Approach C1/b2 (production keepalive path-keyed liveness) — anti-pattern
  (would neuter #1502 guard).
- Approach C2 (durability cascade) as primary — defense-in-depth only; filed
  as optional follow-up.

### Wiring Check
| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| test_export_delete fixtures (sb/reg_client) | test infra | b1 pin + restore-close (Step 2) | ✅ planned |
| _seed_registry/_registry_count/TestPurge seeds | test infra | seed-hold + function-scoped close (Step 3) | ✅ planned |
| masked test :528 | test infra | masked-pin assert (Step 3) | ✅ planned |
| cross-file representative test_free_team_entitlement | test infra | b1 migration (Step 4) | ✅ planned |
| eviction-counter 0-guard | test infra | dict-subclass + _db_path discriminator (Step 1) | ✅ planned |
| docker lane | CI config | immunity gate (Step 5); post-merge full-matrix (Step 6-ii) | ✅ planned |
| #1502 regression pair | test infra | appended to embedded gate (Step 5) | ✅ planned |
| 24 remaining churn files | test infra | follow-up 1 (B waves) | ✅ filed as follow-up |
| embedded keepalive durability | production | follow-up 2 (C2, optional) | ✅ filed as follow-up |

### Review Cycle Log
See problem-verify cycles 1-3, solution-verify cycles 1-3, Phase 5.6, Phase 7
(+ re-review) above.

### Complexity
| Domain | Rating |
|---|---|
| Config (CI/infra) | standard |
| Code (test infra) | standard |
| Overall | standard |
