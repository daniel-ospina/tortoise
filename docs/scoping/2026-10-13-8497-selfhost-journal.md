---
title: "#8497 — self-hosted has no record: scope + plan"
type: scoping
domain: platform
doc_status: implemented
created: 2026-10-13
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise
related:
  - issue: 8497
    note: "self-hosted is journal-less by construction — the scope this doc resolves."
  - issue: 7879
    note: "Parent epic (the two-store record/cache split)."
  - issue: 7951
    note: "§17.1 write-path chokepoint — inert on self-host until the journal exists; the CLI-lane residual is recorded there."
  - issue: 4240
    note: "The hosted-lane precedent: wired the per-graph JSONL journal."
  - issue: 2881
    note: "Recorded decision: the journal is NEVER the durability mechanism. Its collision is CLOSED by scoping (§14.1 O1) — no reopen."
---

# #8497 — the two-store split is hosted-only

## Confirmed problem

**On a shipped self-hosted deployment the derived graph is the only copy.** Not because the
record/cache split was decided differently for self-host, but because a **wiring gap** leaves
self-host **journal-less by construction**: `config._resolve_event_log_path` returns `None` when
`TORTOISE_EVENT_LOG_BASE_DIR` is unset, nothing in the self-host deployment sets it, and the
self-host **REST door never passes a journal even when it is set** — so `_journal_backed` stays
`False`, `derived = replay(journal)` is false, `tortoise rebuild --dir` has nothing to replay, and
the §17.1 single-writer guard is inert.

`docs/architecture/STORAGE-ARCHITECTURE.md:365` names this exact state for the *hosted* path —
*"a wiring gap, not a design gap"* — and `#4240` closed it for hosted. Self-host is the same gap,
never closed for the other deployment.

**Not a design question, and no owner decision is owed for this half.** The owner settled the
scope on the issue (third and final comment, 2026-10-13):

| property | mechanism (decided) | self-host |
|---|---|---|
| **REBUILDABILITY** — `derived = replay(journal)` | **the journal** (`#4240`) | ❌ absent |
| **DURABILITY** — authority in a store-managed artifact, restorable from backup | **the store's backup** (`#2881`, unchanged) — Postgres (§2.0) | ❌ absent (parked) |

`#2881` is **CLOSED by scoping**, not overridden (§14.1 **O1**, owner 2026-09-24): *DURABILITY =
the store's backup; REBUILDABILITY = the journal.* No reopen. **The owner's ruling on this issue:
implement the journal (rebuildability) half now — required by the invariant, mirrors `#4240`,
contravenes nothing — and leave the Postgres-record half as an open product question.**

⚠️ **Stated plainly, because it is easy to overclaim:** arming the journal buys **rebuildability,
not durability**, and it buys rebuildability **of what the journal holds** — a partial one. Three
residuals are declared, not fixed, and none of them changes the ledger:

1. **The journal is not the durability mechanism** (`#2881`, §14.1 O1). `docs/durability-posture.md`
   is the one durability authority and its self-hosted row is unchanged: on-box AOF, **no off-box
   copy** (`#2880` open), **not drilled**.
2. **The dual-write hazard is unchanged.** The JSONL is written outside the store's transaction, so
   arming it closes the invariant *as far as hosted closes it today* — no further (the issue's own
   comment says so; `#5089`/§3 mechanism (1) still records the atomic-write gap).
3. **Journal completeness is unchanged** — raw-Cypher writes ride no journal record at all
   (`#4641`/`#4653` are the named instance: config/onboarding/watermark classes are carried by the
   pre-wipe sidecar precisely *because* `rebuild_all`'s replay cannot reproduce them). Self-host
   inherits exactly the completeness hosted has; this change neither improves nor worsens it.

### Re-measurement on the current head (each claim checked before changing anything)

| # | Issue's claim | Verdict |
|---|---|---|
| 1 | `_resolve_event_log_path` returns `None` when the base dir is unset | ✅ holds — **stale location**: it now lives in `tortoise/config.py:91` (env read `:124`) and is re-exported by `hosted_api` (`#7951` review P1: importing `hosted_api` costs ~1 s and its `sys.modules` presence is a deployment discriminator). |
| 2 | `fly.toml:221` sets `TORTOISE_EVENT_LOG_BASE_DIR`; `docker-compose.yml` does not | ✅ both hold. **Incomplete**: `entrypoint.sh:292` *also* defaults it (`/data/events`) for the hosted image, and `:350-356` **fails the boot** on an unwritable dir. The hosted default is two-fold; self-host had neither half. |
| 3 | `selfhost.py` / `selfhost_api.py` gate only on `TORTOISE_DB_URI` | ✅ holds in substance. |
| 4 | `_journal_backed` is armed by `sdk._get_proj` → `_bind_journal` | ✅ holds (`sdk.py:5131`, `projection/__init__.py:6655`). |
| 5 | *(new)* the MCP door already threads the journal on the org-scoped self-host lane | ✅ `mcp_auth._get_org_sdk` passes `event_log_path=_journal_path_for(...)`; its docstring already says the self-host lane *"is armed whenever the journal base dir is set"*. The **base dir** is the only missing switch there. |
| 6 | *(new, the deeper half)* the **REST door is journal-less even with the base dir set** | ✅ `selfhost_api._sdk()` built `TortoiseSDK(...)` with **no** `event_log_path`. Setting the env var today would arm the MCP door and leave `/v1/*` writing an unrecorded graph — a partial journal looks like a record and is not one. |
| 7 | *(new)* no `/data/events`, no `/data` volume, no probe in the self-host image | ✅ `Dockerfile.selfhost` created `/data` only and set no journal env; `docker-compose.yml` mounted only `falkordb-data` (the sidecar's). A journal written there would be lost with the container. |

## Decision — the seam

**Chosen: a *default journal base dir for self-host* (owned by the self-host deployment), threaded
through the SAME single derivation — never a second derivation, and never a second binding.**
Three coupled pieces:

1. **`config.ensure_selfhost_event_log()`** — resolves `TORTOISE_EVENT_LOG_BASE_DIR` (explicit wins,
   returned unchanged) else the default `<data_root>/events` **only when `data_root` is a
   persistent MOUNT** (`_persistent_dir` → `os.path.ismount` **plus** the mount table in
   `/proc/self/mountinfo`, never `isdir`); creates the dir and
   probes it writable; sets the env var on success, so **every lane that already calls
   `_resolve_event_log_path`** (the MCP door today, the REST door after step 2) picks it up from
   the one derivation. On failure it logs a **loud typed ERROR** and leaves the env **unset**
   (journal-less, honestly declared) — it never raises, never exits, and never writes a journal to a
   fallback location.
2. **`selfhost_api.selfhost_journal_path()`** — the ONE wrapper for the self-host REST door: arms the deployment default, then derives through
   `_resolve_event_log_path(namespace=SELFHOST_ORG_ID, graph_name=None)` (never a literal) and is passed into **all three** `TortoiseSDK(...)`
   constructions — the keepalive anchor *and* both per-request SDKs (`.py:249`, `:261`, `:265`), plus the two read-only health probes in
   `selfhost.py`. The anchor is read-only today, but arming it too removes a
   "same server, two guard states" divergence for free; deciding it by reasoning about which constructions currently write is exactly how a
   door gets forgotten.
2b. **The MCP door arms itself** — `mcp_auth._get_org_sdk` calls `ensure_selfhost_event_log()` when `org_id == SELFHOST_ORG_ID`. This matters
   because `tortoise serve --http` is a documented self-host daemon that serves `org_selfhost` **without importing `tortoise.selfhost`** — the
   module that arms the docker daemon — so the MCP door was a *third* unarmed surface.
3. **The deployment declares it** — `docker-compose.yml` gains `TORTOISE_EVENT_LOG_BASE_DIR` plus a
   **named volume** on `/data` (so the record survives container recreation); `Dockerfile.selfhost`
   creates `/data/events`.

**Why this seam, and not the alternatives.**

| Alternative | Why rejected |
|---|---|
| **Derive the journal inside `TortoiseSDK.__init__` when the env is set** | Attractive (it would cover the CLI lane at once) but it globally changes SDK construction: every `TortoiseSDK(namespace=…)` in the 4,498-function suite would become journal-backed and §17.1-guarded wherever the var happens to be set, turning existing direct-fold fixtures red. The codebase already chose explicit threading (`_make_sdk`, `mcp_auth`) — a global derivation is a bigger decision than this issue and is not needed to close it. |
| **A second self-host-specific resolver** (compute the path in `selfhost.py`) | Exactly the #4240 review's divergence class: two derivations of `(namespace, graph_name) → journal` drift, and the journal gets keyed to a graph the SDK never opens. |
| **Default the base dir unconditionally in Python** | Dev/CI/test lanes would probe on every import and stop being byte-identical — a silent breach of the *"embedded / dev / CI stay journal-less"* boundary this issue explicitly preserves. Gating on a real mount keeps those lanes untouched. |
| **Gate the default on `/data` EXISTING** | The image creates `/data` at BUILD time, so every container from it has a writable `/data` on the **ephemeral** layer — an existence gate arms a journal that dies with the container. That is the "partial record" state again, on a path the design does not control. A mount — `ismount` or an entry in the mount table — is the honest signal (and is what makes the compose volume load-bearing). |
| **Fail the boot on an unwritable journal** (the `entrypoint.sh` precedent) | A managed Fly deploy may fail; the dispatch brief for this lane requires that a self-hosted install must **not** break because a directory is missing or unwritable. The hosted lane's precedent does not transfer. |
| **Refuse the write surface when no journal can be armed** | The strongest honesty option (no request is served that cannot be recorded), but it makes an unwritable optional volume fatal for a self-hosted install — the same "must not break" constraint, moved from boot to request time. **Kept as a residual question**, not silently chosen: the deployment degrades to journal-less with a loud ERROR rather than refusing service. |
| **Ship Postgres to self-host now** | Product scope, not architecture — the owner parked it on the issue. Out of scope here. |

### Failure modes and their dispositions

| Failure | Disposition |
|---|---|
| No persistent mount, root absent (bare daemon, dev, CI) | No default, no probe, no log — journal-less, byte-identical. Explicit env still honoured. |
| Root EXISTS but is not a detected persistent mount (ephemeral `docker run`, a same-filesystem bind mount) | No default; **one WARNING** naming the root, then journal-less. Silence here is indistinguishable from a correctly journal-less dev lane (#8526 P2/P3-2). |
| `<data_root>/events` missing | Created (`makedirs(exist_ok=True)`). |
| `<data_root>/events` unwritable (default path) | **Loud typed ERROR**, env left unset ⇒ the instance is journal-less **and says so**. Never a silent durability-shaped claim; §17.1 legitimately has nothing to enforce. |
| Operator sets the env explicitly to an unwritable dir | Honoured (their choice, the pre-existing contract), plus a boot ERROR. Appends stay fail-soft: ERROR + `tortoise_journal_write_failures_total` + capture-receipt warning (`sdk.py::_emit_event`) — the same accepted residual the hosted lane documents. |
| Journal dir on an unmounted `/data` in compose | Closed by the compose volume added here. |
| The embedded engine inside the shipped container | **Armed** when `/data` is a mount, even though the store is the eval engine. The `OVERRIDES:` boundary on the issue is about *lanes where no durability is claimed* (dev/CI/test), not about which store engine a SHIPPED deployment chose; a container on a mounted volume is the deployment. |

### Adversarial threat surface

**Declared — this is enforcement code** (§17.1 is a fail-closed write gate; `#8497` is class A: an
**inert enforcer** whose condition could never fire on a shipped deployment).

In scope:

| Threat class | Adversarial input | Required behaviour | Pinned by |
|---|---|---|---|
| T1 — default silently not applied | self-hosted-shaped config (mounted data root, base dir unset) | a **real** journal path resolves for the self-host graph and `_journal_backed is True` | `test_default_arms_a_real_journal_when_the_data_root_is_a_mount` |
| T2 — REST door bypasses the record | base dir set, write via `selfhost_api._sdk()` | SDK carries `event_log_path`; the write lands a record; a direct fold **raises** `DirectCacheWriteRefused` | `test_rest_door_carries_the_journal_and_arms_the_guard` |
| T3 — a second derivation invents its own key | `namespace="selfhost"` | the door's journal path is byte-identical to `config._resolve_event_log_path`'s — one derivation | `test_default_arms_…` (path equality) |
| T4 — silent claim on an unwritable default | `/data` mounted, `<data_root>/events` unwritable | journal **not** armed + ERROR logged; the instance does not pretend to record | `test_unwritable_default_refuses_loudly_and_arms_nothing` |
| T5 — armed but NON-PERSISTENT | `/data` exists but is not a mount (ephemeral container layer) | no journal armed (a mount, not existence, is the signal); the declined default is logged once + the operator's explicit env remains the override | `test_no_persistent_mount_arms_nothing`, `test_an_existing_non_mount_root_says_it_armed_nothing` |
| T6 — a third unarmed door | a new `TortoiseSDK(...)` added anywhere in the self-host surface, or `event_log_path=None` | the inventory guard REDs (keyword **value** checked, all three door modules scanned) — the #8497 defect *was* one door silently omitting the argument | `test_every_selfhost_sdk_construction_carries_the_journal`, `test_inventory_reds_on_a_none_valued_journal_argument` |
| T7 — a self-host daemon that never imports `tortoise.selfhost` | `tortoise serve --http` (MCP door only) | the MCP door arms the journal itself (`mcp_auth._get_org_sdk`) | `test_mcp_selfhost_lane_arms_the_journal` |
| T8 — the persistence gate regresses to existence | a plain dir where a mount is required, and a same-filesystem bind mount | the real `_persistent_dir` is pinned (`ismount` **and** the mount table, not `isdir`) | `test_persistent_dir_is_a_mount_not_a_directory`, `test_persistent_dir_sees_a_same_filesystem_bind_mount` |

Out of scope (declared, not chased): the Postgres record half (owner-parked); `#2881` (closed);
the dual-write hazard and journal completeness (residuals 2 and 3 above — unchanged, not worsened);
off-box copy (`#2880`); **the CLI lane** — see the residual below.

### Residual enumerated, not silently absorbed — the CLI write surfaces

Measured on the head: `tortoise/__main__.py` constructs `TortoiseSDK(...)` in **21** places and
passes `event_log_path` in **zero** of them (lines 458, 949, 955, 8234, 8241, 9537, 9569, 9622,
9801, 9803, 9927, …). That is **not** the same defect as the REST door: those constructions carry
**no namespace**, and `_resolve_event_log_path` returns `None` for the unnamed default graph and for
`namespace="registry"` **by construction, on both deployments** — so the CLI's no-namespace SDK has
no journal *by design*, not by omission. The self-host consequence is still concrete: the daemon
serves `org_selfhost` while a CLI ingest opens the URI's own graph (`tortoise`), so an operator's
CLI ingest writes a graph that is neither journaled nor the one the agent reads. This is a
**pre-existing, deployment-symmetric** gap, out of `#8497`'s scoped half; the evidence is recorded
on `#7951` (the door-completeness root) rather than filed as a peer.

### Dispositions for the rest of the issue's consequence table

| Issue item | Disposition |
|---|---|
| `#7952` completeness marker / `#7953` residency router "derived from a store with no truth behind it" | **Addressed at the root** by this change: the truth (journal) now exists on self-host, so those markers are no longer derived from a store with nothing behind it. No code change here. |
| `#7950` — extend the capstone's S1–S6 evidence to the self-host lane | **Explicitly NOT done here.** The owner's final comment narrowed `#8497` to the journal half and parked Postgres; the capstone's self-host half still needs the record (it is a *two-store* capstone) and remains owed. Named so a lane reading this scope does not think it was covered. |
| Postgres record on self-host | **Parked by the owner** (product scope). |

## Plan (implemented)

### Task 1 — the arming seam (config) ✅

`ensure_selfhost_event_log()` + `_persistent_dir()` + `_SELFHOST_DATA_ROOT` in `tortoise/config.py`
(`:147`–`:225`). **Acceptance met:** arms `<data_root>/events` when the root is a mount and the env
is unset; honours an explicit env; returns `None` + logs ERROR when the default cannot be written;
makes no filesystem change when the root is not a persistent mount.

### Task 2 — the REST door carries the journal ✅

`selfhost_api._selfhost_journal_path()` (`:200`) + `event_log_path=` on all three constructions
(`:249`, `:261`, `:265`). **Acceptance met:** the resulting projection has `_journal_backed is True`
and a real REST write lands a `PointAdded` record.

### Task 3 — the deployment declares it ✅

`docker-compose.yml` (env + `tortoise-data` volume on `/data`), `Dockerfile.selfhost`
(`mkdir -p /data/events`), `README.md` + `docs/quickstart-selfhosted.md` env tables.

### Task 4 — tests, failing before the fix ✅

`tests/test_selfhost_rest.py::TestSelfHostJournal8497` — the T1–T8 cases. **No new file**, so no
`tests/_embedded.py` / `tests/test_markers.py` registration is owed. The file is registered under
`api`; it is **DUAL-REGISTERED under `core`** by this change because the seam it pins lives in
`tortoise/config.py`, which matches no `SOURCE_PATTERNS` entry and falls through to `core` (a named
match REPLACES that fallback) — an `api`-only entry leaves the guard asleep on a `config.py`-only
change (#8526 review P2-2; the earlier "already `core`-registered" claim here was wrong).

## Rejected alternatives

Recorded above (the seam table). The issue body's own recommendation — *"a default
`TORTOISE_EVENT_LOG_BASE_DIR` in `docker-compose.yml`/`selfhost.py`"* — survives, but as a
hypothesis it was **insufficient on its own**: the re-measurement found the REST door reports `None`
even with the var set, and an existence gate would arm an ephemeral journal. Neither of those was
named in the body.

## Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `config._resolve_event_log_path` (the one derivation) | config | reused unchanged | ✅ |
| MCP door (`mcp_auth._get_org_sdk`) | API | already threads it; armed by the base dir | ✅ |
| REST door (`selfhost_api._sdk`) | API | Task 2 | ✅ |
| MCP door on `tortoise serve --http` | API | `mcp_auth._get_org_sdk` arms it (T7) | ✅ |
| Read-only health probes (`selfhost.py`) | runtime | Task 2 (`selfhost_journal_path`) | ✅ |
| Daemon startup (`selfhost.py`) | runtime | Task 1 (import-time arm) | ✅ |
| Compose topology + image | infra | Task 3 | ✅ |
| Env-table docs | docs | Task 3 | ✅ |
| CLI write verbs (`__main__.py`) | API | **not covered — pre-existing, symmetric with hosted; evidence on `#7951`** | ⚠️ declared |
| Durable off-box copy (`#2880`) | infra | **not covered — existing open issue** | ⚠️ declared |
| Postgres record | infra | **parked by the owner** | ⚠️ declared |
| Capstone `#7950` self-host evidence | verification | **owed, not covered here** | ⚠️ declared |

## Verify cycle log

### #8526 review — Cycle 1 (fix round)

Reviewer findings at `0e3cc5ba7` (deduplicated across the review's three comments):

- **P1 — the `docs` check is RED**: 9 markdown-lint findings in this file. A new file's findings can
  never be in the baseline snapshot, and the job fails closed by design. **Fixed in place** (blank
  lines around the `###` headings and their lists, and a language on the fenced evidence block); the
  snapshot is NOT regenerated, per the job's own rule.
- **P2 — the explicit branch never consulted the memo**: `docker-compose.yml` sets
  `TORTOISE_EVENT_LOG_BASE_DIR`, so every `/v1/*` request, MCP tool call and readiness probe re-ran
  `makedirs` + `mkstemp` + `unlink` on the journal volume. **Fixed**: `_SELFHOST_JOURNAL_PROBED`
  keys the probe by value, so it runs once per base dir per process.
- **P2/P3 — the guard read the keyword, not its value, and scanned one module**:
  `event_log_path=None` passed it, and `selfhost.py` / `mcp_auth.py` were unscanned. **Fixed**:
  `_journal_inventory` scans all three door modules and treats a `None` literal as unarmed.
- **P2/P3 — `ismount` misses a same-filesystem bind mount, silently**: `mount --bind` on the same
  partition returns False, and the declined branch logged nothing. **Fixed**: the gate also reads
  the mount table (`/proc/self/mountinfo`), and an existing-but-not-persistent root logs one WARNING.
- **P2 — the `api`-only CI registration**: the `config.py` pins were ungated on a `config.py`-only
  change. **Fixed**: dual-registered under `core` in `config/ci-surfaces.yml`.
- **CI (`test (h)`)**: `tests/test_probe_reset_inflight_4608.py`'s fake SDK factory predated this
  change's `event_log_path=` argument on `selfhost._probe_sdk`, so the probe raised before it reached
  its query. **Fixed in the fake** (`**_kwargs`), not by dropping the arming.
- **P3 — a memo hit disagreed with the derivation**: with the var cleared after the arm (a test's
  monkeypatch teardown, an ops harness), the cached verdict returned the armed base while
  `_resolve_event_log_path` — the ONE derivation every door reads — returned `None`, so the door
  built a journal-less SDK behind an "armed" claim. **Fixed**: a positive memo hit re-establishes
  the env var the arm wrote; `test_a_cached_arm_re_establishes_the_env_the_derivation_reads` pins
  it (fails with `KeyError: 'TORTOISE_EVENT_LOG_BASE_DIR'` before the fix).

Re-verified at this head with the commands in the Evidence block below.

### scope-verify — Cycle 1

- Verifier: 1 × (standard tier). Findings: **0 P0, 2 P1, 5 P2, 1 P3.**
  - P1-a (`/data` EXISTS is a false persistence signal — the image bakes `/data`, so an existence
    gate arms an ephemeral journal). **Fixed**: the gate is now `os.path.ismount` via
    `_persistent_dir`, T5 added, and `test_no_persistent_mount_arms_nothing` pins it.
  - P1-b (CLI write surfaces unenumerated). **Addressed by enumeration + evidence, not by code**:
    the CLI's no-namespace constructions are outside the derivation *by construction on both
    deployments*; recorded on `#7951` as the door-completeness root (no peer filed).
  - P2s **incorporated**: T5/T6 added; the "must not break" attribution corrected to the dispatch
    brief (the issue does not say it) and the refuse-the-write-surface alternative is now evaluated
    and kept as an explicit residual; the journal-completeness residual (`#4641`/`#4653`) and the
    `#7950`/`#7952`/`#7953` dispositions added; line citations corrected; the three (not two)
    `TortoiseSDK` constructions in `_sdk()` stated with the anchor deliberately included.
  - P3 **fixed**: real line numbers cited.
- Controller action: fixed P1-a, P1-b (by scoping + evidence), incorporated all P2s/P3.
- Re-dispatch: none — no P0/P1 remains after the fixes.
- Contradiction test: the verifier confirmed it is respected — the change is scoped to
  REBUILDABILITY, leaves DURABILITY = the store's backup (`#2881`, §14.1 O1) and the
  `durability-posture.md` self-hosted row untouched, and the only departure (the embedded/dev/CI
  boundary) carries the `OVERRIDES:` marker on the issue itself.

### plan-verify — Cycle 1

- Reviewer: 1 × (fresh context). Findings: **0 P0, 2 P1, 3 P2, 4 P3.**
  - P1-a (`tortoise serve --http` never imports `tortoise.selfhost`, so the MCP door stayed unarmed on a documented self-host daemon).
    **Fixed**: `mcp_auth._get_org_sdk` arms the default for `SELFHOST_ORG_ID`; T7 pins it; the README claim is now true.
  - P1-b (every arming test fakes `_persistent_dir`, so the change's central re-measured decision — *mount, not existence* — was unpinned).
    **Fixed**: `test_persistent_dir_is_a_mount_not_a_directory` pins the real function (T8).
  - P2s **fixed**: the health-probe SDKs now carry the journal; the inventory guard matches `ast.Attribute` too and its docstring names its
    escape surface (an aliased import) instead of over-claiming completeness; the probe is a UNIQUE `mkstemp` name (two concurrent armers
    could otherwise have one `unlink` read a writable journal as unwritable) and the verdict is memoized per data root, so the lazy
    per-request path is a dict lookup; the explicit env is probed too, so the plan's promised boot ERROR is real.
    *(Fix round, below: the memo now covers the explicit branch too, the inventory spans all three door modules and reads the
    keyword's value, and the persistence gate consults the mount table.)*
  - P3s **fixed**: `SELFHOST_ORG_ID` replaces the `"selfhost"` literal in the REST wrapper; the quickstart restore sentence was re-written
    (the earlier edit had mangled its punctuation); line citations updated.
- Controller action: fixed both P1s and every P2/P3. Re-dispatch: none — no P0/P1 remains.
- The reviewer independently reproduced the pre-fix RED with a single atomic stash/pop (source stashed, tests kept → 8 failed for the stated
  reasons).

### Evidence (exact commands)

```bash
env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m pytest tests/test_selfhost_rest.py -q -p no:cacheprovider -k "8497 or Journal"
# pre-fix (source reverted to origin/main, tests kept): 14 failed, 1 passed
# fix-round pins only (source reverted to the PR head 0e3cc5ba7): 6 failed, 9 passed
# post-fix:                                                      15 passed

# CI fix for `test (h)`: the fake SDK factory must track the real call shape.
# With the test file at the old head (no `**_kwargs`) and the source fixed:
... pytest tests/test_probe_reset_inflight_4608.py -q   # 1 failed, 2 passed
# with the fix:                                         # 3 passed

... pytest tests/test_selfhost_rest.py tests/test_selfhost.py tests/test_onboarding_selfhost_w12.py \
  tests/test_selfhost_volunteer_context.py tests/test_selfhost_health_probe_executor.py \
  tests/test_uri_env_mutations_declared.py tests/test_markers.py tests/test_durability_posture.py \
  tests/test_7951_write_doors.py tests/test_7951_enforce_atomic_write.py tests/test_config.py \
  tests/test_ci_selection.py tests/test_docs_lint_baseline.py tests/test_docs_lint_gate.py \
  tests/test_probe_reset_inflight_4608.py -q                # 538 passed, 1 skipped

... pytest tests/test_mcp_server.py tests/test_mcp_server_auth_modes.py \
  tests/test_mcp_route_challenge.py -q                      # 166 passed, 16 skipped

.venv/bin/python -m ruff check tortoise/config.py tortoise/selfhost.py tortoise/selfhost_api.py \
  tortoise/mcp_auth.py tests/test_selfhost_rest.py tests/test_probe_reset_inflight_4608.py
# All checks passed!

echo "tortoise/config.py" | .venv/bin/python tools/ci_selection.py --changed-files - --event pull_request
# surfaces=['core'], test_files includes test_selfhost_rest.py  (the P2-2 dual registration)
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python tools/ci_selection.py --integrity
# ✅ integrity: all test files classified; slow_files consistent; shards consistent

npx --yes markdownlint-cli2 "docs/scoping/2026-10-13-8497-selfhost-journal.md"  # 0 issues
```
