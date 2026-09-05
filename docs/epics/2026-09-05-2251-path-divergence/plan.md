---
title: "Issue #2251 — Embedded registry path/namespace divergence: implementation plan"
type: engineering
domain: platform
doc_status: draft
created: 2026-09-05
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Issue #2251 — Embedded registry path/namespace divergence: implementation plan (A2 amended)

> Scope doc for issue #2251. All line numbers against head `4380ff68` (worktree
> `.worktrees/fix/2251-embedded-path-divergence`). Problem + solution diamonds
> verified: 2× problem-verify cycles clean (no P0/P1 after 2 controller fixes),
> 2× solution-verify clean (no P0/P1), second-model coherence clean, Phase 7
> parallel review (codebase + devil's advocate) conditional-GO with P2s —
> all P2/P3/P4 findings incorporated below.

## Confirmed Problem (v2 — controller fixes applied)

Two hosted control-plane reads in `hosted_api.py` construct bare, unanchored
SDKs whose connection parameters diverge from every registry writer in the same
process:

- **(a) Namespace divergence (mode-independent, primary):** `_iter_registered_teams`
  (hosted_api.py:294-338) builds `TortoiseSDK()` with no namespace (line 329) →
  `_get_registry()` derives graph `"control_plane"` (sdk.py:1558, ns="" branch),
  while every Team writer uses `namespace="registry"` → `registry_control_plane`
  (sdk.py:1556; writers at 1084/1093, 4172/4177, `_registry_sdk()` 16325). The
  registry-mode retention sweep therefore enumerates a graph **no current writer
  targets** — always-empty in ANY non-Supabase deployment (URI or embedded).
- **(b) Path divergence (embedded-only):** BOTH `_iter_registered_teams` AND
  `_graph_has_team_namespace` (13815-13842, `TortoiseSDK(namespace="registry")`
  at 13835) resolve the embedded path via `config.resolve_db_path()` →
  `~/.tortoise/tortoise.db` when `TORTOISE_DB_PATH` is unset, while the anchored
  writers (`_make_sdk` 180-187, `_registry_anchor` 245-248) resolve
  `/data/tortoise.db` with a tempdir fallback. The onboarding existence check
  drifts; the read path auto-materializes a stray `~/.tortoise/tortoise.db`.

Empirically verified (throwaway probes): site 1 broken in all 4 env combos
(embedded/URI × path set/unset); site 2 broken in the embedded + path-unset
case; the fix-shape (`_make_sdk(namespace="registry")`) repairs both in all
combos. URI-mode bug (a) confirmed live (same server, different graph).
Supabase-mode hosted production is unaffected via the early return (308-313).

**Blast radius split:** path half = URI-less env-unset runs only; namespace
half = every non-Supabase hosted_api deployment (dev, docker-lane CI,
registry-mode on-prem). Supabase mode (hosted prod) unaffected via control-plane
seam.

## Solution — A2: embedded-path resolver extraction + seam routing

Chosen over A1 (seam-only: leaves the duplicated `/data` policy class and the
`~/.tortoise` bare-construction trap) and A3 (site-2 explicit URI/embedded
branch: adds a second mode-dispatch surface, forfeits the anchored daemon, and
re-introduces per-request daemon spawn/close churn for a non-load-bearing
no-anchor contract). Verified: `_make_sdk` URI branch (174-176) is
construction-args byte-identical to today's bare constructions, so URI mode is
preserved; the `"registry"` anchor key is shared with `_registry_anchor()`
writers so reads and writes view the same anchored server; routing site 2
through `_make_sdk` preserves pin-4 (never constructs the `team_{tid}`
projection — `list_graphs()` is the probe) and fixes a latent exception-path
SDK leak (`sdk.close()` at 13837 is skipped when `list_graphs()` raises).

### Implementation steps (`tortoise/hosted_api.py` only; no other product files)

1. **Add `_resolve_embedded_db_path()`** (module-private, immediately above
   `_make_sdk` ~:157). Semantics = the current inline prologue, byte-preserving:
   - `db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")` —
     **get-with-default, NOT `or`** (an empty-string `TORTOISE_DB_PATH` must
     keep falling through `os.makedirs(os.path.dirname(""))` → `FileNotFoundError`
     → tempdir, matching today; do not collapse `""` → `/data`).
   - `os.makedirs(os.path.dirname(db_path), exist_ok=True)`; on `OSError`
     (unwritable/unmountable `/data`, dirname-is-a-file, empty-string env) →
     `os.path.join(tempfile.gettempdir(), "tortoise.db")`.
   - Docstring: server-process path policy (`TORTOISE_DB_PATH` → `/data` →
     tempdir); note this mirrors `selfhost_api._resolve_embedded_db_path` (:97,
     "mirroring hosted_api._make_sdk" — same-name mirror convention, no
     cross-import; future consolidation target); **anti-bare-construction
     warning**: bare `TortoiseSDK()` resolves `~/.tortoise` via
     `config.resolve_db_path()` — hosted request/sweep paths must use the seam.
   - No `abspath`/`expanduser` normalization (match current seams; the
     divergence fix is consistency with writers, not new normalization).
2. **`_make_sdk` (:176-183)** → `db_path = _resolve_embedded_db_path()`.
   **`_registry_anchor` (:243-248)** → same. Anchor/fresh-handle machinery,
   `_KEEPALIVE_LOCK`/`_anchor_usable` logic, and docstrings otherwise untouched.
3. **Site 1 — `_iter_registered_teams`:**
   - `sdk = TortoiseSDK()` (:329) → `sdk = _make_sdk(namespace="registry")`,
     **kept inside the existing outer `try`** (never-raise `except → []`
     contract preserved; `_make_sdk` can raise `EmbeddedStoreBusyError` when the
     DB is cross-process-held).
   - Supabase early-return (:308-313) **byte-untouched** (post-#669
     no-registry-recreate).
   - Rewrite the #2179 comment block (:316-328) → #2251 resolution note (bare
     construction read ns-less `control_plane` on `~/.tortoise` while writers
     target `registry_control_plane` on the anchored store; `_make_sdk`
     fixes both halves; fresh handle per call fine for a boot+hourly best-effort
     sweep; keep the "if a to_thread shape is introduced, route through
     `_registry_anchor()` first" forward note).
   - Docstring (:295-303): drop the stale "and boot reconcile" claim (only
     caller is `_sweep_events` at :485); registry mode reads `registry_control_plane`
     Team nodes via `_make_sdk(namespace="registry")`.
   - Remove the dead local `from tortoise.sdk import TortoiseSDK` (:314) —
     module-level import exists at :69.
4. **Site 2 — `_graph_has_team_namespace`:**
   - Replace the bare construction (:13835-13838) with the seam route:
     ```python
     try:
         sdk = _make_sdk(namespace="registry")
         try:
             graphs = sdk._get_proj().db.list_graphs() or []
         finally:
             sdk.close()
         return graph_name in graphs
     except Exception:
         return True  # graph-up-unknown fail-open (unchanged)
     ```
     **The `_make_sdk` call sits INSIDE the outer except-catching `try`**
     (empirically proven: without this, `EmbeddedStoreBusyError` on a
     cross-process-held DB escapes → 500s onboarding GET/PATCH). Inner
     `try/finally sdk.close()` fixes today's exception-path leak.
   - URI branch of `_make_sdk` returns `TortoiseSDK(namespace="registry")` —
     construction-args byte-identical to today (no anchor, no keepalive, no
     `_get_registry()` → cannot recreate the deleted `registry_control_plane`
     in Supabase mode; `list_graphs()` is server-wide and never mints a
     `team_{tid}` graph — pin-4 preserved).
   - Rewrite the #2179 comment block (:13822-13834) → #2251 resolution note;
     fix the stale "WITHOUT constructing the projection" docstring (the body
     does construct the *registry* projection; what it must never construct is
     the `team_{tid}` projection).
   - Remove the dead local `from tortoise.sdk import TortoiseSDK` (:13826).
   - Reword the stale falsy-team_id guard comment (:333-334) whose rationale
     ("namespace=None would sweep the default/shared graph") changes post-fix
     (falsy id now fails the namespace charset regex at construction instead;
     the filter itself stays — it is still correct).

### Testing strategy (all additions in `tests/test_hosted_api.py`)

Lane mechanics: `test_hosted_api.py` is docker-lane (`slow_files` +
`api` surface; NOT in the carve-out). New tests are **markerless** (an
`embedded_only` marker would skip them in the docker lane AND exclude them from
the D14 `-k` subset — the #2188 dead-test trap). Env-control per test via the
proven `test_register_journals_minted_team_graph` (:2172-2208) shape
(delenv/setenv + monkeypatch). Tests that mint the `"registry"` anchor must
close+clear `_FALLBACK_KEEPALIVE` in a `finally` (fixture hygiene).

1. **`test_iter_registered_teams_reads_registry_control_plane`** (graph-name
   leg; discriminates even under the pinned harness): `monkeypatch
   tortoise.supabase_control.is_supabase_enabled → False` (force registry
   branch), delenv `TORTOISE_DB_URI`, setenv `TORTOISE_DB_PATH=tmp`; seed a
   Team via the writer seam
   `_make_sdk(namespace="registry")._get_registry().query(CREATE …)`; assert
   `_iter_registered_teams()` returns the team. **Pre-fix FAILS** (queries
   `control_plane`); post-fix passes. Also seed a falsy-id Team row → assert
   it is skipped (site-1 guard).
2. **`test_graph_has_team_namespace_probes_writer_db`** (path leg,
   isolated-env): delenv `TORTOISE_DB_URI` + `TORTOISE_DB_PATH`; redirect
   `HOME` to a tempdir (hermetic `~/.tortoise`); install a **record-only**
   `TortoiseSDK.__init__` spy capturing `(db_path, namespace)` (records, does
   NOT delegate to the real `__init__` — avoids opening real DBs); clear
   `_FALLBACK_KEEPALIVE`; call `_make_sdk(namespace="registry")` (writer
   shape) + `_iter_registered_teams()` + `_graph_has_team_namespace("t1")`;
   assert (a) all recorded `db_path` values are **equal**, (b) none is under
   `$HOME/.tortoise`, (c) all `namespace == "registry"`. **Pre-fix FAILS** via
   (a)+(c): the record-only spy never delegates to the real `__init__`, so pre-fix
   bare sites record `db_path=None` (their `~/.tortoise` resolution happens inside
   the never-run real ctor) vs the writer's real path, plus `namespace=None` vs
   `"registry"`. (b) is a post-fix regression guard, not a discriminator.
   **Self-check (fail-closed): assert ≥1 recorded construction has
   `db_path is not None`** (proves the embedded leg ran — otherwise a leaked
   `TORTOISE_DB_URI` makes everything record None and (a)/(c) vacuous-pass).
   Environment-agnostic:
   never assert the exact resolved path (`/data` vs tempdir varies by host);
   reset `tempfile.tempdir = None` after redirecting `TMPDIR` if asserting the
   fallback path.
3. **`test_supabase_mode_never_constructs_registry_sdk`** (invariant guard,
   NOT a pre-fix discriminator): `TORTOISE_CONTROL_PLANE=supabase` + fake
   creds + monkeypatched `get_control_plane → FakeControlPlane` seeded with a
   team (pattern :1149-1167); record-only spy; assert `_iter_registered_teams()`
   returns the fake's rows AND zero SDK constructions recorded (registry never
   touched — #669). Guard label: passes pre-fix too (early return already
   exists); it pins the invariant against regression.
4. **Site-2 URI-mode parity** (cheap): with `TORTOISE_DB_URI` set (not
   delenv'd), record-only spy; assert `_graph_has_team_namespace` constructs
   exactly one SDK with `(db_path=None, namespace="registry")` and does NOT
   populate `_FALLBACK_KEEPALIVE` — pins "URI construction semantics unchanged".
5. **Resolver unit tests** (4): unset env + monkeypatched `os.makedirs` no-op →
   `/data/tortoise.db`; env honored → returned **verbatim** (the resolver does
   NOT abs-ify — raw env value, intended divergence from `config.resolve_db_path`);
   dirname-is-a-file → tempdir fallback; `TORTOISE_DB_PATH=""` → tempdir (pins
   the deliberate `""` corner that diverges from `resolve_db_path`'s
   `""`→`~/.tortoise`).
   Also: site-2 exception-path — monkeypatch `list_graphs` to raise + assert
   `close()` was invoked via a spy on a REAL SDK (needs real construction, not
   the record-only spy) (pins the finally-leak fix).
   Hygiene note: test 1's `TORTOISE_DB_PATH` must use an absolute tempdir path
   (not bare `"tmp"`) to avoid repo-root artifacts under pre-fix red runs
   (`resolve_db_path` abs-ifies relative paths).

### Acceptance criteria (restated per incorporated review P2s)

- (a) Embedded (URI-less, `TORTOISE_DB_PATH` unset): registry writes + sweep
  enumeration + existence reads hit the **same** DB file. → tests 1-2.
- (b) Both sites ride the anchored registry seam (`_make_sdk(namespace="registry")`),
  no bare `TortoiseSDK()` remains in `hosted_api.py` outside the factories
  (grep-clean). → tests 1-2 + inspection.
- (c) **URI construction semantics unchanged** — no URI connection
  parameter/writer/Supabase-read change; bug (a) is mode-independent, so the
  sweep/check **graph target** correction (`control_plane` →
  `registry_control_plane`) is an explicit intentional fix, restated as such in
  the issue body (original "URI mode unaffected" was wrong for site 1 in
  registry mode). → test 4 + docker-lane runs.
- (d) Supabase mode never constructs the registry (post-#669). → test 3.
- (e) Registry-mode production behavior flips are named as intended in the
  issue body: (i) the event-retention sweep becomes live for the first time in
  registry-mode embedded AND URI deployments (boot + hourly per-team purge;
  startup latency now scales with registered-team count); (ii) onboarding
  `_graph_has_team_namespace` re-enables the node-aware FLOW graph leg in
  embedded-default deployments (previously always-False on the wrong DB).
- (f) Path policy is a single named function with corner tests. → resolver tests.
- (g) #2179 comment blocks + stale docstrings rewritten; no "Do NOT fix…"
  guidance left pointing at a resolved divergence.

### Verification plan (pre-merge)

1. `uv run ruff check tortoise/hosted_api.py tests/test_hosted_api.py`.
2. Embedded run of the new tests (delenv'd, no URI needed).
3. **Docker-lane run** (the audit the reviewers require, MEASURED not assumed):
   full `test_hosted_api.py` + the unpatched-URI booters
   (`test_onboarding_state_split.py`, `test_dr_endpoints.py`,
   `test_agent_signup*.py`, `test_email_signup.py`, `test_dashboard_login.py`,
   `test_billing.py`) on a **pre-populated matrix** (run a live signup file
   first so the shared registry has accumulated teams), watching for: boot
   latency inflation, graph-inventory assertion breaks, resurrection of dropped
   team graphs. Decide: if per-boot sweep cost is unacceptable → gate the boot
   sweep or add a probe-before-purge as a **scoped follow-up** (not in this PR
   unless measurement shows breakage).
4. `_close_keepalive_anchors` hygiene on all new anchor-minting tests.
5. If the measurement shows the sweep flip breaks an existing suite, that
   finding goes back into this plan BEFORE merge (not after).

### What the fix must NOT do

1. No registry construction in Supabase mode (site-1 early return untouched;
   site-2 URI branch never anchors / never calls `_get_registry`).
2. No URI connection-string/writer changes.
3. No onboarding consumer behavior flips: `_get_onboarding_projection`
   resolution order, `_graph_has_team_namespace` `except → True` fail-open,
   and the #1997 accept-and-drop gate all stay — only the probed DB is
   corrected. `_ACCEPT_AND_DROP` unchanged.
4. No read-path writes to the `team_{tid}` graph (pin-4): `list_graphs()` is
   the probe; the seam's eager anchor connect touches only the writers' registry
   main graph (a graph that already exists post-provision). (The onboarding
   FLOW-leg re-enable in embedded-default deployments IS an intended
   consumer-observable flip per acceptance (e)(ii) — the "no flips" constraint
   means no UNINTENDED flips.)
5. No factory contract changes (`_make_sdk` returns fresh; `_registry_anchor`
   returns the anchor; `"registry"` key + `_registry_sdk` unchanged).
6. No out-of-scope path changes: CLI/graph-scripts bare constructions and
   `config.resolve_db_path()`'s `~/.tortoise` default remain correct for
   offline/dev surfaces.
7. No scope creep into `quota.py`/`metering.py`/`selfhost_api.py` inline `/data`
   copies (filed as class-hygiene follow-up context; the two fixed sites +
   resolver extraction remove the recurrence vector inside hosted_api).

## Rejected Alternatives

- **A1 (seam routing only):** would be better under a razor-thin review budget
  with an immediate follow-up hardening issue; rejected because it leaves the
  duplicated `/data` policy (6 lines × 2 factories) and the bare-construction
  trap that caused this bug class.
- **A3 (split responsibility: path-only site 2):** would be better only if
  site-2's no-anchor request-path contract were load-bearing (production
  Supabase is URI mode → never anchors anyway; embedded registry mode anchors at
  team-create before any GET). Rejected: second mode-dispatch surface, loses
  the anchored daemon, per-request open/close churn.
- **Option 1 (unify canonical default to `~/.tortoise` or `/data`):** fixes
  NEITHER site-1 defect (path-unified sweep still reads `control_plane`); a
  naive flip would break `/data` volume persistence (selfhost pins env;
  fly.toml mounts /data) or silently relocate dev/CLI DBs. The canonical
  question is answered as "server processes use the anchored seam; `~/.tortoise`
  stays the CLI/dev default".
- **Routing site 2 through `_registry_sdk()`:** rejected — its eager connect +
  `PROBE_RETRY_DELAY` retry-sleep is wrong for a best-effort read that must fail
  open fast.
- **Routing site 1 through `_registry_anchor()`:** rejected — fresh-handle
  semantics match every sibling registry reader; keep the anchor handle off the
  sweep's read path (forward note retained for a future to_thread shape).

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `_iter_registered_teams` consumers (`_sweep_events` 485, boot 496 + hourly 503) | code | site-1 fix + docker-lane audit | ✅ |
| `_graph_has_team_namespace` consumers (`_get_onboarding_projection` 13871, #1997 gate 14089) | code | site-2 fix + onboarding suites | ✅ |
| `_make_sdk`/`_registry_anchor` factories + `_FALLBACK_KEEPALIVE["registry"]` | code | resolver extraction + anchor hygiene | ✅ |
| Supabase no-registry-recreate (#669) | invariant | early return untouched + test 3 | ✅ |
| Docker-lane boot-sweep flip (shared matrix registry) | test-infra | measured pre-merge audit; scoped follow-up if breakage | ⚠️ measured |
| `_http_fixtures`/`test_hosted_api` client fixtures | test-infra | no fixture edits; new tests self-clean anchors | ✅ |
| `test_billing.py:927-961` monkeypatch | test-infra | module-attr patch — routing-immune | ✅ |
| `config/ci-surfaces.yml` lanes | test-infra | no new files, no marker changes | ✅ |
| Legacy ns-less `control_plane` rows (pre-#139 data, mcp stdio) | data | census note; no migration (writers all ns="registry" today) | ✅ documented |
| Docs (`docs/00_index.md`, `docs/event-catalog.md`, .env.example) | docs | no falsified statements; issue-body amendment records intended flips | ✅ |

## Issue body amendment (to post with the scoping comment)

Amend #2251 with: (1) the namespace-divergence discovery (site 1 is
mode-independent — original "URI mode unaffected" was wrong for registry-mode
site 1); (2) restated acceptance (URI construction semantics unchanged; sweep
graph-target correction intentional; production behavior flips named — sweep
becomes live, onboarding graph leg re-enables in embedded default); (3) the
docker-lane boot-sweep audit as a pre-merge measurement; (4) follow-up context:
`quota.py`/`metering.py`/`selfhost_api.py` inline `/data` copies + selfhost
health probes (`selfhost.py:196/223`) as the remaining class-hygiene surface.
