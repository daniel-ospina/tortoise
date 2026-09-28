---
title: "Bound the FalkorDB graph leak (#3634) — Implementation Plan"
type: engineering
domain: platform
status: live
created: 2026-09-24
updated: 2026-09-24
ownedBy: organisation-design-team
subjects:
  team: organisation-design-team
doc_status: live
aboutSubjects: test-infrastructure, graph ownership
aboutObjects: tests/_embedded.py, tests/conftest.py, tortoise/sdk.py, tortoise/projection/__init__.py
---

<!-- research-path: issue #3634 scoping comments (Phase 1.5 external research) -->
<!-- plan-review: cycles=3 status=converged reviewers=1,2,5 final-verification=run(7 residuals, all applied) tier=standard date=2026-09-24 | cycle1=2xP0+17xP1 cycle2=4xP0+11xP1 cycle3=0xP0+1xP1+7xP2 | escalate: convergence exit at the Low-Medium cap — no issue left unresolved, but the last application was not re-reviewed -->

# Bound the FalkorDB graph leak — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Stop the test lane accumulating FalkorDB graphs until the container hits `maxmemory`, by making the ownership record authoritative, completing the epic's own CI-3 fix, and making a broken backend report its real cause.

**Team:** organisation-design-team
**Issue:** #3634 · **Epic:** `docs/epics/2026-08-24-test-db-migration/plan.md`

> **Tier: Standard.** Steps below specify **behaviour, the real seam, and the file** — not invented helper names. Every symbol named here was verified to exist; where a test needs a fixture, the plan names the **existing** one to follow. Do not introduce a helper the plan does not name — if a seam is missing, stop and report it rather than inventing one.

**Architecture:** Five seams, each fail-closed, ordered so shared vocabulary is declared **before** anything consumes it. Task 1 declares the graph-name ownership contract the repo currently expresses in five uncoordinated copies, and adds a test that fails on an unregistered **named** prefix constant (scope stated at Task 1 Step 1 — anonymous literals are not scanned). Task 2 fixes the mint that produced the epic's own CI-3 leak. Task 3 reclaims the residue no default path can reach — behind an explicit opt-in, and **only** the residue, so it does not become a third copy of `wipe_server`. Task 4 attributes a broken backend to its real cause. Task 5 turns the E2E-7 warning into a session-scoped gate that measures what it claims and can actually fail.

### Pattern Research

> **Findings date:** 2026-09-24

**Library docs (preflight)**

> Gate skipped: the plan touches **zero third-party dependencies**. Every call site it changes goes through an in-repo wrapper — `FalkorProjection` / `probe.db.list_graphs()` / `_drop_one_graph` (`tests/_embedded.py`) for FalkorDB access, and `_journal_append_product` (`tortoise/projection/__init__.py`) for journalling. No new FalkorDB, `falkordb`, or `redis` API surface; no new third-party import.

> Bucket *Library version & API surface* skipped: no library version is in play — the changed code calls wrappers that already exist at 2+ call sites.
> Bucket *Idiomatic usage patterns* skipped: no new usage pattern; the changes are a string derivation, a set predicate, and a gate modelled on an existing one.
> Bucket *Library/framework pitfalls* skipped: the FalkorDB semantics the plan depends on (`GRAPH.LIST`/`GRAPH.DELETE`/`GRAPH.QUERY` auto-creates, the `noeviction` failure mode, graphs carrying no creation timestamp) were triangulated in `issue-scoping` Phase 1.5 and are recorded on #3634. Re-firing would re-research a settled question.

**Prior research consumed (issue-scoping Phase 1.5, on #3634):**
- Canonical: ephemeral-per-run is the default SOTA (Testcontainers/Ryuk deletes when the test process stops checking in). Our substrate is the **persistent** variant, so it inherits obligations Ryuk does not — completeness and unconditionality.
- Competitor variance: Kubernetes/OpenShift reap by `ownerReferences` + a TTL controller, never by name shape.
- Known pitfall: a name prefix is **not** proof of ownership, and prefix/pattern deletion is non-atomic in Redis-family stores. The journal is the correct shape; the defect is a prefix gate outvoting it.

**Contradiction test (run first, per component):** D-4=A and #7795/#1884 hold that only the ownership record may authorise a delete. Tasks 1/2/5 move *toward* that; Task 3 uses the exact opt-in mechanism #1884 installed. **Four recorded departures**, each carrying an `OVERRIDES:` line **on issue #3634** (per AGENTS.md, the artifact a lane actually reads) and indexed in the epic changelog: (a) the residue sweep itself — a journal-blind, name-shape-authorised delete (Task 3 Step 8, CI-7); (b) the new opt-in env read, against `_KNOWN_NARROW_READS`'s "the ledger can only shrink" invariant (Task 3 Step 8, CI-8); (c) the E2E-7 gate's predicate (Task 5 Step 7, CI-9); (d) that gate's scoping against the log-and-continue policy (Task 5 Step 7, CI-10). `noeviction` is untouched and `allkeys-*` is never adopted.

### Integration Surface Map

Layer vocabulary: **unit** = `_FakeDb`/`_FakeProj` or `object.__new__` (the `tests/test_wipe_server.py` / `tests/test_projection_maxmemory_message.py` model); **integration** = live FalkorDB on `localhost:6379` (the `uri_env`/`server_proj` fixtures); **session-e2e** = subprocess `pytest` (the `tests/test_tripwire.py` model — this repo has **no Playwright**); **pgTAP-equivalent = N/A** (no SQL — the DB-logic layer is FalkorDB Cypher, pinned by live-docker integration); **AST pin** = the `tests/test_write_ahead_mint.py` model (`ast.walk` + a `_MINT_SITES`-style registry that fails loudly when the set changes).

| # | Surface | Type | Test layer | Contract | Key failure modes | Bug pattern | Backed by |
|---|---|---|---|---|---|---|---|
| 1 | SDK registry-name derivation — `tortoise/sdk.py::_get_registry` | State / naming contract | **Unit** | Test-derived ⇒ name matches the sweep prefixes; prepend **only when absent**; `elif ns`/`else` branches unchanged | (a) prefix absent ⇒ unowned cohort (Cause B); (b) double-prefix ⇒ breaks `test_derived_names.py:166`; (c) prefix on a shared branch ⇒ **AC2** | Conditional guard, fail-open | Task 2 |
| 2 | Duplicated derivation — `graph-scripts/backfill_invite_ghost_members.py::_registry_graph_name(base)` | Contract / consumer | **Unit** | Must agree with the SDK | Stale name printed after Task 2 | Duplicated-derivation drift | Task 2 Step 5 (deleted, not tested around) |
| 3 | Journal **append** — `_journal_append_product`, `_journal_append` | State mutation | **Unit** | `_TEST_SESSION_ACTIVE` ∧ URI-gated; OSError raises (#3214) | Silent no-op ⇒ minted-but-unowned graph | Fail-open, idempotency | **Unchanged** — `tests/test_write_ahead_mint.py` (existing) |
| 4 | Journal **read/remove/cursor** — `_read_journal_file`, `_read_journal`, `_remove_journal_file` | State mutation / fs | **Unit** | Tolerant parser; `remove iff not failed` | Journal removed while an owned drop failed | Cleanup/idempotency | **Unchanged** — `tests/test_wipe_server.py::test_sweep_dedupes_journal_entries`, `tests/test_write_ahead_mint.py::test_sweep_drop_converges_on_a_never_created_name` (existing) |
| 5 | Markers + pid liveness — `active_suite_markers`, `_stale_sweep` | Liveness | **Unit** + session-e2e | Live **iff** the marker parses as live | Live session misread as dead ⇒ graphs swept (data loss) | Race, fail-open | **Unchanged** — covered by the #3074 tests; no new test claimed |
| 6 | **Own/stale sweep** — `_session_end_own_sweep`/`_sweep_drop`/`_drop_one_graph` | DB / external | **Unit** + **Integration** | Input is the **journal**, gated by the ownership predicate; URI-default skipped | (a) owned name preserved ⇒ leak (LC1); (b) wrong prefix matches a shared registry ⇒ **AC2** | Destructive-by-default, fail-open | Task 1, Task 5 |
| 7 | **Global / last-suite-standing sweep** — `wipe_server`, `_leftover_sweep` | DB / external | **Unit** + **Integration** | `_SERVER_WIPE_PREFIXES`, narrower than the journal set; live peers re-read per graph | Peer's live graph detached (#3074) | Race, destructive-by-default | Task 1 (behaviour unchanged) |
| 8 | **Opt-in env gates** — `_team_sweep_allowed` + the new legacy gate | Config / guard | **Unit** (env matrix) | Exact `== "1"`; fail-closed; refusal logged | Non-`"1"` enables an irreversible delete; OFF path deletes something new (**AC3**) | Fail-open, conditional guard | Task 3 |
| 9 | **Env-vocabulary ledger** — `tests/test_env_truthy.py::_KNOWN_NARROW_READS` | Guard contract | **Unit** | A new `== "1"` read needs a ledger entry **with a reason** | New read without an entry ⇒ guard reds | Conditional guard | Task 3 Step 6 |
| 10 | **pytest session lifecycle** — `_server_graph_hygiene`, `atexit` | Event / state | **Unit** + **AST pin** + session-e2e | Teardown once; name set captured **before** the sweep | Capture after the sweep ⇒ **vacuous gate** | Idempotency, fail-open | Task 5 |
| 11 | **E2E-7 gate** — `_SERVER_SWEEP_GRAPH_LIST_CONSTANT`, `conftest.py`'s `_server_graph_hygiene` (bound check + teardown capture) | Guard / assertion | **Unit** + session-e2e | `set(owned ∧ journalled ∧ ¬default) ∩ GRAPH.LIST == ∅` | Today: WARNING-only. Hazards: the assert under the broad `except` ⇒ **vacuous**; an owned-set from the raw journal ⇒ **false-positive** on a preserved shared registry | Fail-open, conditional guard | Task 5 |
| 12 | **Backend attribution** — `_write_refusal_message`, `_WRITE_REFUSAL_MARKERS`, `_auto_health_recover` | Failure classification | **Unit** (`object.__new__` — the classifier *is* the unit) | Each cause maps to its **own** remedy; genuine corruption still reaches rebuild advice | A backend cause routed into the maxmemory message ⇒ misattribution; a widened substring list swallows corruption | Silent function skips, conditional guard | Task 4 |
| 13 | **Concurrency** — peer suites on one shared server, the mint→journal window | Concurrent access | **Integration** (deterministic monkeypatched interleavings) | No atomic cross-channel delete (#3214, filed) | Peer's live graph deleted by a `scope=None` sweep | Race | **Unchanged** — `tests/test_wipe_server.py::test_wipe_server_toctou_*`; residual #3214 |

**Bug Pattern Flags** — *Race:* 5, 7, 13. *Cleanup/idempotency:* 3, 4, 6, 10. *Fail-open:* 1, 3, 6, 8, 10, 11, 12 — **both sides of every gate asserted**; a gate that silently does not apply is this issue's whole defect class. *Destructive-by-default:* 6, 7, 8 — the shared registries present in every fixture and asserted **survived**. *Conditional guard:* 1, 8, 9, 10, 11, 12 — boundary values required. *Added:* duplicated-derivation drift (s2) and duplicated vocabulary (Task 1's register). *Not present:* SQL business logic, N+1, stale closures.

**Untestable properties — do not invent tests for these:**
1. **"This session's sweep removed all owned names" as an in-process property.** `_server_graph_hygiene` is `scope="session", autouse=True`, so it has run before any test body; its failure cannot be observed from inside the session it governs (`tests/test_tripwire.py:6-9`). → Extract the predicate as a named helper and unit-test it; AST-pin the capture-before-sweep ordering; treat session-level observation as **not covered** rather than pretending otherwise.
2. **The whole-server count's correctness** — it *cannot* distinguish our residue from foreign graphs. That is the departure's justification. → Test only the predicate and the arithmetic.
3. **P3's "1,608 → 0"** — the `:6379` lane was restored on a **fresh volume (0 graphs)**; the census is historical. → Test the **mechanism** on fixtures on a live server; keep the census as out-of-band evidence.
4. **A real concurrent-suite interleaving** — non-deterministic by construction. → Deterministic monkeypatched interleavings + the filed #3214 residual.

### Verification Plan

**Domain:** `code` only. **Complexity:** UX=n/a, Architecture=standard(→medium), Ontology=low.

| # | Destination | Depth | Reason |
|---|---|---|---|
| 1 | `test-writing` (unit) | **full** | Criteria that fail at a single seam: the ownership register (T1), the mint derivation (T2), the exact-`"1"` gate (T3), the env ledger (T3), the extracted E2E-7 predicate (T5), the P4 classifier (T4). |
| 2 | `test-integration` (live FalkorDB) | **full** | Architecture=medium. Mocks cannot prove `GRAPH.DELETE` removes a name from the real `GRAPH.LIST` nor that the shared registries survive (AC2). |
| 3 | `test-review` | **full** | Mandatory gate for the test files created/edited. |
| 4 | Session-lifecycle | **best-effort** | AC4's gate is **not** in-process observable (Untestable #1). The AST pin is the real guarantee; the subprocess leg is worthwhile only if `tests/test_tripwire.py`'s `_run_session` can be given a seeding path — if it cannot, record AC4 as **AST-pinned, not session-observed** rather than shipping an unrunnable test. |

**Skipped:** `test-e2e`/Playwright (no browser); `ux-*` (no UI); `content-*`; `research-verification` (Phase 1.5 complete); `config-validation` (the new env read is code, guarded by the ledger + a CI set-site pin); pgTAP (no SQL); architectural-soundness review (Architecture=medium, not high).

**Tech Stack:** Python 3.12, pytest, FalkorDB (via the in-repo `FalkorProjection` wrapper), Docker-compose test lane.

---

## Acceptance Criteria

> **⛔ Every task that creates a new test file must also register it.** `tests/test_ci_selection.py` fails on an unclassified test file. Run `python3 tools/ci_selection.py --register --surface core` (the canonical tool — do not hand-edit the list) and confirm `python3 tools/ci_selection.py --integrity` exits 0. Discovered during Task 1, which hit it with `tests/test_graph_name_ownership.py`; Tasks 3 and 5 create new files too.

1. One **declared** graph-name ownership contract in `tests/_embedded.py`. **Declared surface** = `tests/_embedded.py`, `tortoise/sdk.py`, `tortoise/projection/__init__.py`. Every **named** prefix constant on that surface is registered **by reference** (a test asserts the ties and fails on an unregistered constant). Anonymous prefix *literals* — and named constants in any file outside the three — are **out of the scanner's scope**; they stay governed by the DIVERGENCE comment at the declaration.
2. A test-derived registry name carries the approved prefix, **prepending only when absent**; `tests/test_derived_names.py` stays green; `registry_tortoise` and `registry_control_plane` are **never** dropped (fail-closed regression test).
3. Legacy reclamation is opt-in by exact `"1"`; **off** ⇒ no default path deletes anything new, enforced by an **AST pin** (not prose); **on** ⇒ only the declared residue is reclaimed.
4. No **owned** journalled name survives its sweep — `set(owned ∧ journalled ∧ ¬default) ∩ GRAPH.LIST == ∅`. **Reach: last-suite-standing only** (the gate sits under `if not others`), and the AST-pinned capture-before-sweep ordering is the guarantee — not an in-process assertion.
5. A broken backend reports its **own** cause (`maxmemory` vs `LOADING` vs `fork`/errno-17), never `python -m tortoise rebuild`.
6. All **four** recorded departures carry an `OVERRIDES:` line **on #3634** and a row in the epic changelog.

---

### Task 1: Declare the graph-name ownership contract

**Intent:** The test-prefix vocabulary is copied in five uncoordinated places, which is why a name could be journalled, refused, and its record discarded. Declare it once and make an unregistered **named** constant fail the suite.
**Acceptance:** The sets are declared together with their rationale; `_SERVER_WIPE_PREFIXES` and `_PRODUCT_GRAPH_PREFIXES` are tied to the declaration **by reference**; `wipe_server`'s and `_sweep_drop`'s behaviour are unchanged (existing tests green); the residue predicate is **deny-safe** (a `tortoise_restored_*` name and a non-`str` are never residue).
**Files:**
- Modify: `tests/_embedded.py` (the divergence register ~653-663; `wipe_server`'s literal ~825)
- Test: `tests/test_graph_name_ownership.py` (new)

**Step 1: Write the failing test** — assert the **ties**, not labels the snippet itself invents, and scan for a new copy:

```python
from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent     # the tests/test_write_ahead_mint.py pattern

from tests._embedded import (                     # noqa: E402
    _DIVERGENCE_REGISTER, _LEGACY_RESIDUE_PREFIXES, _PRODUCT_GRAPH_PREFIXES,
    _SERVER_WIPE_PREFIXES, _SWEEP_OWNED_PREFIXES, is_legacy_residue,
)


def test_the_register_is_tied_to_the_real_symbols():
    for name, symbol in (
        ("_SWEEP_OWNED_PREFIXES", _SWEEP_OWNED_PREFIXES),
        ("_SERVER_WIPE_PREFIXES", _SERVER_WIPE_PREFIXES),
        ("_PRODUCT_GRAPH_PREFIXES", _PRODUCT_GRAPH_PREFIXES),
        ("_LEGACY_RESIDUE_PREFIXES", _LEGACY_RESIDUE_PREFIXES),
    ):
        assert _DIVERGENCE_REGISTER[name]["set"] is symbol, name
    for name, entry in _DIVERGENCE_REGISTER.items():
        assert entry["reason"].strip(), f"{name} registered without a reason"


def test_the_wipe_literal_is_a_subset_of_the_journal_set():
    assert set(_SERVER_WIPE_PREFIXES) < set(_SWEEP_OWNED_PREFIXES)


def test_residue_is_disjoint_from_every_owned_family():
    """Overlap would make the residue pass a third copy of wipe_server."""
    assert not set(_LEGACY_RESIDUE_PREFIXES) & set(_SWEEP_OWNED_PREFIXES)


def test_the_residue_predicate_is_deny_safe():
    for name in ("registry_tortoise", "registry_control_plane", "test_a",
                 "tortoise_test_matrix", "tortoise_restored_20260101",
                 "org_x", "team_y", "totally_unrelated"):
        assert not is_legacy_residue(name, default_graph="tortoise_test_matrix"), name
    assert not is_legacy_residue(None, default_graph=None)     # non-str must not raise


def test_a_sixth_copy_of_the_vocabulary_fails_here():
    """AC1's enforcement, SCOPE: NAMED prefix constants on the declared surface.
    Anonymous literals (`startswith(("test_", ...))` in tortoise/sdk.py,
    tests/test_derived_names.py, tests/test_pre_migration_safety.py) are NOT
    covered — they remain governed by the DIVERGENCE comment at the
    declaration. Do not claim a guarantee broader than this scanner."""
    seen = set()
    for rel in ("tests/_embedded.py", "tortoise/sdk.py", "tortoise/projection/__init__.py"):
        src = (REPO / rel).read_text()
        for m in re.finditer(r"^(_?[A-Z_]*PREFIXES)\s*[:=]", src, re.M):
            seen.add(m.group(1))
    unregistered = {n for n in seen if n not in _DIVERGENCE_REGISTER}
    assert unregistered == set(), f"prefix constant(s) not in the register: {unregistered}"
```

Run this once after Step 3 and confirm `seen` matches exactly the four registered names before committing — if the scanner finds a fifth, register it rather than widening the regex.

**Step 2:** Run `uv run pytest tests/test_graph_name_ownership.py -v` → FAIL (ImportError).
**Step 3: Implement** in `tests/_embedded.py`, keeping the existing DIVERGENCE comment and extending it. Exact contents:

```python
# ── Graph-name ownership vocabulary (#3634) ────────────────────────────────
# ONE declaration. Each set states its INPUT, because that is what makes it
# safe: a prefix is not ownership; a journal record is.
_SWEEP_OWNED_PREFIXES = ("test_", "tortoise_test", "team_", "org_")
#   input: the JOURNAL (an ownership record). May include product families.
_SERVER_WIPE_PREFIXES = ("test_", "tortoise_test")
#   input: GRAPH.LIST (no attribution). Deliberately a SUBSET (#7795).
_LEGACY_RESIDUE_PREFIXES = (
    "registry_test_",   # 723 in the #3634 census — the epic CI-3 cohort
    "v10fix_", "ttm_",  # 10 + 9
    "review_rw_probe", "askshape_", "legbudget_", "tt4524_probe", "probe_d10_",
)
#   input: GRAPH.LIST, OPT-IN ONLY. Names no default path can reach. Every
#   entry is verified present in the #3634 census; exact stems are preferred
#   over broad ones so a future name cannot be swept by accident.
_DIVERGENCE_REGISTER = {
    "_SWEEP_OWNED_PREFIXES": {"set": _SWEEP_OWNED_PREFIXES,
                              "reason": "the canonical journal-ownership declaration (#7795)."},
    "_SERVER_WIPE_PREFIXES": {"set": _SERVER_WIPE_PREFIXES,
                              "reason": "#7795 fail-closed: GRAPH.LIST has no attribution."},
    "_PRODUCT_GRAPH_PREFIXES": {"set": _PRODUCT_GRAPH_PREFIXES,
                                "reason": "real tenant graphs; opt-in via _sweep_team_strays."},
    "_LEGACY_RESIDUE_PREFIXES": {"set": _LEGACY_RESIDUE_PREFIXES,
                                 "reason": "#3634 journal-blind residue; opt-in only."},
}


def owns_by_ownership_record(name: str) -> bool:
    """The JOURNAL path's predicate — the only one that may authorise a delete
    from an ownership record."""
    return isinstance(name, str) and name.startswith(_SWEEP_OWNED_PREFIXES)


def is_legacy_residue(name: str, *, default_graph: str | None) -> bool:
    """The GRAPH.LIST residue predicate. Opt-in only. Deny-safe: a non-str, the
    URI default, anything the ownership record covers, and any `tortoise_restored*`
    snapshot (which `_guard_destructive` treats as production) are all refused."""
    if not isinstance(name, str):
        return False
    if name.startswith("tortoise_restored"):
        return False
    if default_graph is not None and name == default_graph:
        return False
    if owns_by_ownership_record(name):
        return False
    return name.startswith(_LEGACY_RESIDUE_PREFIXES)
```

`_PRODUCT_GRAPH_PREFIXES` is declared **below** this block today (its module-level declaration in `tests/_embedded.py`); **move it above the register** so the dict can reference it at import time by object identity (`is`), which is what the tie test asserts.

**Rewire the consumers — do not leave a copy behind.** Three concrete edits, all in `tests/_embedded.py`:
- `wipe_server`'s anonymous literal (`startswith(("test_", "tortoise_test"))` at ~`:825`) → `startswith(_SERVER_WIPE_PREFIXES)`.
- `_sweep_drop`'s gate (~`:1049`) → `owns_by_ownership_record(g)`.
- Extend the existing DIVERGENCE comment so its "this literal" reference names `_SERVER_WIPE_PREFIXES` rather than a bare tuple (the register tie cannot detect a re-inlined literal — that is why the comment must name the symbol).

Verify no copy remains: the diff for this task must show that literal appearing **once**.
**Step 4:** `uv run pytest tests/test_graph_name_ownership.py tests/test_wipe_server.py tests/test_markers.py tests/test_derived_names.py tests/test_env_truthy.py -v` → PASS.
**Step 5: Commit.**

### Task 2: The registry control-plane name carries the approved prefix (epic CI-3 half 2)

**Intent:** A test-derived registry name must match the reaper filter that already exists, so it is dropped by its own session's sweep instead of leaking forever.
**Acceptance:** A test-derived registry name matches `_SERVER_WIPE_PREFIXES`; an already-compliant name is unchanged; the `elif ns`/`else` branches are unchanged; `tests/test_derived_names.py` stays green.
**Files:**
- Modify: `tortoise/sdk.py::_get_registry` (~2638-2644)
- Modify: `graph-scripts/backfill_invite_ghost_members.py` (~52-59, 89)
- Test: `tests/test_derived_names.py`

**Step 1: Write the failing test.** The **real seam** (verified): `_get_registry` derives from `getattr(self._get_proj(), "graph_name", None)`, so mutate the cached projection — there is no `_proj_override`:

```python
def _registry_name_for(tmp_path, ns, graph_name):
    """Drive the REAL derivation: _get_registry reads proj.graph_name."""
    sdk = TortoiseSDK(str(tmp_path / "x.db"), namespace=ns)
    try:
        if graph_name is not None:
            sdk._get_proj().graph_name = graph_name   # the redirect does this in a test session
        return sdk._get_registry()._name
    finally:
        sdk.close()


def test_test_derived_registry_name_carries_an_approved_prefix(tmp_path):
    name = _registry_name_for(tmp_path, "registry", "test_docs_api_abc123def456")
    assert name.startswith(("test_", "tortoise_test_")), name


def test_already_compliant_registry_name_is_not_double_prefixed(tmp_path):
    name = _registry_name_for(tmp_path, "test-hosted", "test_hosted_tortoise")
    assert name.startswith("test_hosted_")
    assert not name.startswith("test_test_hosted_")


def test_shared_registry_name_is_never_prefixed(tmp_path):
    """AC2's fail-closed direction. `namespace="registry"` makes `_get_proj`'s
    `namespace == "registry"` branch force graph_name="registry_tortoise"
    BEFORE our override, so this is the real shared-name path — do not also
    assert a separate `graph_name=None` leg, which is byte-identical to it."""
    assert _registry_name_for(tmp_path, "registry", None) == "registry_control_plane"
    assert _registry_name_for(tmp_path, "registry", "registry_tortoise") == "registry_control_plane"
```

**Step 2:** `uv run pytest tests/test_derived_names.py -k registry -v` → FAIL on the first test.
**Step 3: Implement** — restructure so the prefix is applied **only inside the test-derived branch** (this *replaces* the existing `if`/`elif`/`else` chain; there is no separate "add a guard after the branch" step):

```python
            if graph_name and graph_name.startswith(("tortoise_test_", "test_")):
                registry_name = f"{ns}_{graph_name}_control_plane" if ns else f"{graph_name}_control_plane"
                # #3634 / epic CI-3 half 2: WITHOUT this the derived name matches
                # NEITHER ownership set, so every mint leaks one server graph
                # (E2E-7). Prepend only when absent — `test-hosted` derivatives
                # already comply, and double-prefixing breaks
                # tests/test_derived_names.py.
                if not registry_name.startswith(("test_", "tortoise_test_")):
                    registry_name = f"test_{registry_name}"
            elif ns:
                registry_name = f"{ns}_control_plane"      # shared — NEVER prefixed
            else:
                registry_name = "control_plane"            # shared — NEVER prefixed
```

**Step 4:** `uv run pytest tests/test_derived_names.py -v` → PASS, including the pre-existing `test_hosted_` assertion.
**Step 5: Delete the duplicated derivation.** `_registry_graph_name(base)` returns `registry_{base}_control_plane` and would print a stale name after Step 3. **Delete the helper** and read the name from the SDK in the print path (DRY — do not test a second copy into agreement). If the script must stay standalone, it may not reconstruct the name at all; print the base graph and instruct the operator to re-run. Also update the `clear_max_sessions_4010.py::test_guard` docstring's "the resolved name is never test-prefixed" invariant to say it holds only on the no-`path=` CLI path.
**Step 6:** `uv run pytest tests/test_wipe_server.py tests/test_write_ahead_mint.py tests/test_markers.py -v` → PASS.
**Step 7: Commit.**

### Task 3: Opt-in reclamation of the journal-blind residue

**Intent:** 723 legacy `registry_test_*` graphs and ~31 eval graphs have no journal left, so only a prefix-scoped sweep can reach them — and #7795/#1884 forbid that by default.
**Acceptance:** Gate unset/`0`/any non-`"1"` ⇒ `deleted == []`. Gate exactly `"1"` ⇒ exactly the declared residue; the shared registries, the URI default, `tortoise_restored_*`, a `None`, `org_*`/`team_*` and `test_`/`tortoise_test_` names are untouched. The function has **no** default call site, pinned by an AST walk.
**Files:**
- Modify: `tests/_embedded.py` (new `_legacy_sweep_allowed` + `_sweep_legacy_strays`, modelled on `_team_sweep_allowed` ~1131-1157 and `_sweep_team_strays` ~1142)
- Modify: `tests/test_env_truthy.py` (`_KNOWN_NARROW_READS` entry + amend its "can only shrink" docstring)
- Modify: `config/ci-surfaces.yml` (register any **new** test file — see the note below)
- Test: `tests/test_wipe_server.py` (gate matrix + live leg); `tests/test_graph_name_ownership.py` (AST pin)

**Step 1: Write the failing tests.** `_FakeDb`'s first parameter is `fail_delete`, **not** the graph list (`tests/test_wipe_server.py:82-89`); set `db.graphs` and pass `_FakeProj(db)` positionally (the pattern at `:960`):

```python
@pytest.mark.parametrize("value,expected", [
    (None, False), ("", False), ("0", False), ("true", False),
    ("yes", False), ("1 ", False), ("01", False), ("1", True),
])
def test_legacy_sweep_gate_is_narrow_by_design(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("TORTOISE_TEST_SWEEP_LEGACY", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", value)
    assert _legacy_sweep_allowed() is expected


_RESIDUE_FIXTURE = [
    "test_a", "tortoise_test_b", "registry_test_c_control_plane",
    "v10fix_c1", "ttm_a1", "review_rw_probe", "askshape_b6", "legbudget_25979_txrx",
    "registry_tortoise", "registry_control_plane", "tortoise_test_matrix",
    "tortoise_restored_20260101", "org_x", "team_y", "totally_unrelated", "t",
]


def test_legacy_sweep_off_deletes_nothing(monkeypatch):
    monkeypatch.delenv("TORTOISE_TEST_SWEEP_LEGACY", raising=False)
    db = _FakeDb(); db.graphs = list(_RESIDUE_FIXTURE)
    _sweep_legacy_strays(_FakeProj(db), default_graph="tortoise_test_matrix")
    assert db.detached == []                                        # AC3


def test_legacy_sweep_on_reclaims_only_the_residue(monkeypatch):
    monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", "1")
    db = _FakeDb(); db.graphs = list(_RESIDUE_FIXTURE)
    _sweep_legacy_strays(_FakeProj(db), default_graph="tortoise_test_matrix")
    assert set(db.detached) == {
        "registry_test_c_control_plane", "v10fix_c1", "ttm_a1",
        "review_rw_probe", "askshape_b6", "legbudget_25979_txrx",
    }
    for protected in ("registry_tortoise", "registry_control_plane",
                      "tortoise_test_matrix", "tortoise_restored_20260101",
                      "org_x", "team_y", "test_a", "tortoise_test_b",
                      "totally_unrelated", "t"):
        assert protected not in db.detached
```

Verify expected set membership against the declared prefixes before committing; the fixture is written so the assertion is derivable, not guessed.

**Step 2:** FAIL (`_legacy_sweep_allowed` undefined).
**Step 3: Implement** — signature **`_sweep_legacy_strays(proj, *, default_graph: str | None)`** (explicit, so tests and code agree; `_sweep_team_strays(proj, uri)` takes a URI, this one takes the default-graph name because it is called manually). Copy `_team_sweep_allowed`'s exact-`"1"` + log-the-refusal structure. The predicate is `is_legacy_residue(name, default_graph=default_graph)` — **do not re-list prefixes here.** Document at the definition that it must never be called from a default teardown path.
**Step 4:** Green on the unit tests.
**Step 5: AST pin for AC3** — a real walk, modelled on `tests/test_write_ahead_mint.py` (`ast.parse` + a `_MINT_SITES`-style registry that fails loudly when the set changes). Recover the enclosing definition with a **recursive walk carrying the function name** (the model's `_collect_sites` shape) rather than a parent map:

```python
_DEFAULT_PATH_FILES = ("tests/_embedded.py", "tests/conftest.py",
                       "tests/test_tripwire.py")
# Expected call sites: NONE. The guard fails loudly if this set changes
# (the tests/test_write_ahead_mint.py `_MINT_SITES` contract).
_LEGACY_CALL_SITES: set[tuple[str, int]] = set()


def _calls_in(path):
    """(enclosing_def_name, node) for every Call to the sweep, via ast.walk.
    ast.walk parents are absent, so recover the enclosing def with a RECURSIVE walk that carries the function name (the test_write_ahead_mint.py `_collect_sites` shape) rather than a parent map.
    ...


def test_legacy_sweep_has_no_default_call_site():
    found = {(rel, node.lineno) for rel in _DEFAULT_PATH_FILES
             for _def_name, node in _calls_in(REPO / rel)}
    assert found == _LEGACY_CALL_SITES, f"wired into a default path: {sorted(found)}"
```

Implement `_calls_in` with a recursive `ast` walk that carries the enclosing definition's name (the model's `_collect_sites` shape) — a bare `ast.Call` has no parent link, so an "enclosing def" cannot be recovered from `ast.walk` alone. Assert on `(file, lineno)`. **State the scanner's limits in the test docstring:** it covers the three files listed; a default call site in any other module is not caught (widen the tuple deliberately if that changes).
**Step 6: Ledger + CI set-site** — add the read to `_KNOWN_NARROW_READS` with its reason, **and** amend that docstring's "the ledger can only shrink" invariant in the same commit (it now grows by one deliberate, `OVERRIDES:`-marked departure). Add a CI pin asserting the opt-in appears nowhere in `python-ci.yml`, modelled on the existing env-gated-iff-URI test in `tests/test_ci_selection.py` (locate the exact model before writing). Run `uv run pytest tests/test_env_truthy.py tests/test_ci_selection.py -v` → PASS.
**Step 7: Live integration leg** — add `test_legacy_sweep_reclaims_on_live_server(uri_env)` to `tests/test_wipe_server.py`: create a `registry_test_<uuid>_control_plane` and a `registry_tortoise`-style shared name on the real server, set the gate, run the sweep, and assert from `GRAPH.LIST` that the first is gone and the second survives. Select it by exact nodeid:

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
  uv run pytest "tests/test_wipe_server.py::test_legacy_sweep_reclaims_on_live_server" -v
```

**Step 8: Record the departure on its proper home** — an `OVERRIDES:` comment on issue #3634 for **both** Task 3 departures: (i) the residue sweep itself (a journal-blind, name-shape-authorised delete, permitted only because it is opt-in and disjoint from every owned family), and (ii) the new opt-in env read, which grows a ledger the guard documents as able to *only shrink*. Add the matching row for **each** to the epic changelog table (`docs/epics/2026-08-24-test-db-migration/plan.md`), and add the new sweep to the `_embedded.py` register **by symbol**, per the register's own instruction.
**Step 9: Commit.**

### Task 4: Attribute a broken backend to its real cause (P4)

**Intent:** A `maxmemory` refusal, a `LOADING` reply, and a fork/errno-17 failure must each report **their own** cause. Routing all three into the maxmemory message would be the misattribution this task exists to remove.
**Acceptance:** Each cause yields a message naming **that** cause; none yields the rebuild command; genuine corruption still reaches the rebuild advice.
**Files:**
- Modify: `tortoise/projection/__init__.py` (add `_BACKEND_FAILURE_MARKERS` ~1222 and `_BACKEND_FAILURE_REMEDIES` ~1270, and add `_backend_failure_message` ~2875; `_WRITE_REFUSAL_MARKERS` ~1198 and `_write_refusal_message` ~2845 are **NOT** modified). Fork detection **delegates** to `fork_slot.is_fork_refusal` — the ONE canonical classifier, which owns the marker vocabulary (`could not fork`, `can't fork for module`, `cannot fork for module`) and walks the `__cause__`/`__context__` chain; the table must NOT restate fork markers, or the vocabulary re-forks and `could not fork` goes unrecognised again.
- Test: `tests/test_projection_maxmemory_message.py`

**Step 1: Write the failing test** — the file **already has the model**: `_projection(probe_error=...)` built with `object.__new__`, and it asserts on what `_auto_health_recover` **raises** via `pytest.raises`. Reuse it; do not invent a helper:

```python
@pytest.mark.parametrize("cause,needle", [
    (_MAXMEMORY_ERROR, "maxmemory"),                              # the existing constant
    ("LOADING Redis is loading the dataset in memory", "loading"),
    # The engine's REAL wordings, never an invented stem: `GRAPH.COPY failed,
    # could not fork` is FalkorDB's reply (cmd_copy.c); errno 17 surfaces as
    # redis's own `Can't fork for module: File exists` (module.c, EEXIST).
    # Both route through `fork_slot.is_fork_refusal` — the shipped test pins both.
    ("GRAPH.COPY failed, could not fork", "fork"),
    ("Can't fork for module: File exists", "fork"),
])
def test_each_backend_cause_names_itself(monkeypatch, cause, needle):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    proj = _projection(probe_error=RuntimeError(cause))
    with pytest.raises(RuntimeError) as err:
        proj._auto_health_recover()
    assert needle in str(err.value).lower()
    assert "python -m tortoise rebuild" not in str(err.value)
```

The corruption direction is **already pinned** by `test_genuine_corruption_still_advises_rebuild` — do not duplicate it; run it to confirm it stays green.
**Step 2:** FAIL for the two new causes.
**Step 3: Implement** — the current markers are `("used memory >", "oom command not allowed", "out of memory")`; the two new causes match none, so they currently reach the rebuild advice. Add **cause-specific** detection and a **cause-specific** message body — do **not** add a bare `"loading"` to the shared substring tuple, which would swallow unrelated text. A small ordered `(marker, cause_key)` table plus a per-cause remedy is the shape; the maxmemory branch keeps its existing message verbatim.
**Step 4:** `uv run pytest tests/test_projection_maxmemory_message.py -v` → PASS, including the two pre-existing pins.
**Step 5:** `uv run pytest tests/test_embedded_lifecycle.py -v` → PASS.
**Step 6: Commit.** (`_probe_ok` already retains the reason at `~2735` — it is **not** a modify target.)

### Task 5: A session-scoped E2E-7 gate that can fail (P5)

**Intent:** Make accumulation loud on the property actually claimed — this session's **owned** journalled names are gone — without false-positiving on a preserved name and without being swallowed by the surrounding handler.
**Acceptance:** The gate raises iff an owned journalled name survives **and the sweep reported no failure**; it never raises on a preserved-but-journalled shared registry, the URI default, or after a transient delete error; the name set is captured **before** the sweep; the assert is not inside the broad `except`; the whole-server count stays a logged warning and `server-hygiene-end.json` is still written.
**Files:**
- Modify: `tests/_embedded.py` (add `_owned_survivors` and `_live_graph_names`)
- Modify: `tests/conftest.py` (imports ~700-707; capture ~757; gate ~781-808)
- Modify: `docs/epics/2026-08-24-test-db-migration/plan.md` (changelog table)
- Modify: `config/ci-surfaces.yml` (register the new test file — see the note below)
- Test: `tests/test_server_hygiene_gate.py` (new)

**Step 1: Write the failing test** — the ownership filter is **inside** the helper, and the fixture includes the false-positive case:

```python
def test_owned_survivors_applies_the_ownership_predicate():
    """A preserved-but-journalled shared registry must NOT be a survivor."""
    journal = {"test_a", "registry_test_b_control_plane",
               "registry_control_plane", "registry_tortoise", "tortoise_test_matrix"}
    live = {"test_a", "registry_control_plane", "registry_tortoise", "tortoise_test_matrix"}
    assert _owned_survivors(journal, live, "tortoise_test_matrix") == {"test_a"}


def test_owned_survivors_ignores_foreign_graphs():
    assert _owned_survivors({"test_a"}, {"org_x", "t"}, None) == set()
```

**Step 2:** FAIL.
**Step 3: Implement** — in `tests/_embedded.py`:

```python
def _live_graph_names(uri: str) -> set[str]:
    """The live server's graph names. Mirrors the existing probe idiom
    (conftest.py's `with _sweep_proj(uri) as probe: probe.db.list_graphs()`)."""
    with _sweep_proj(uri) as probe:
        return set(probe.db.list_graphs() or [])


def _owned_survivors(journal_names, live_names, default_graph) -> set[str]:
    """Owned ∧ journalled ∧ ¬default ∧ still live. Mirrors _sweep_drop's skips:
    only a name the OWNERSHIP RECORD authorises is ever a leak."""
    owned = {n for n in journal_names if owns_by_ownership_record(n)}
    owned.discard(default_graph)
    return owned & set(live_names)
```

In `tests/conftest.py`: add `_live_graph_names`, `_owned_survivors`, `_uri_default_graph_name` to the existing import block; keep `journal_size = len(_read_journal())` for the bound and add `journal_names = set(_read_journal())` alongside (the journal can carry duplicate lines — do not silently change the bound's arithmetic); then place the assert **after** the `try/except` at `:781-808`, still inside the `if not others ...` guard:

```python
    # ── E2E-7 gate (#3634 Task 5). Lives under the `if not others` guard above
    # (last-suite-standing only — do NOT widen that), and MUST sit outside the
    # broad `except Exception`, or AssertionError is swallowed and the gate is
    # vacuous. Short-circuit on `error` too: a sweep that RAISED sets
    # own={"error": ...} with no `failed` key, so `not own.get("failed")` alone
    # would run the gate over names a dead sweep left and red the suite
    # (violating cycle-8 P2-3).
    if not own.get("skipped") and not own.get("failed") and not own.get("error"):
        survivors = _owned_survivors(journal_names, _live_graph_names(uri),
                                     _uri_default_graph_name())
        if survivors:
            raise AssertionError(
                f"E2E-7: {len(survivors)} owned journalled graph(s) survived the "
                f"sweep: {sorted(survivors)}")
```

Keep the whole-server count as a `print(... WARNING ...)` and **retain** the `server-hygiene-end.json` write.
**Step 4:** Green on the unit tests.
**Step 5: AST pin** the capture-before-sweep ordering in `tests/test_server_hygiene_gate.py` with a real `ast.walk` of the `_server_graph_hygiene` body: the **`journal_names = set(_read_journal())` assignment's** `_read_journal()` call must lexically precede the `_session_end_own_sweep` call. The pin targets the GATE'S OWN capture, not merely any `_read_journal()` call — the whole-server bound's `journal_size = len(_read_journal())` also calls it, and matching on that read let the gate's capture move after the sweep and still pass.
**Step 6: Session leg + reach.** The gate sits under `if not others` in `_server_graph_hygiene`, so it is **last-suite-standing only**; record that as the reach. DO NOT claim in-process observability (Untestable #1). If `tests/test_tripwire.py`'s `_run_session` cannot be given a seeding path (it pops `TORTOISE_TEST_JOURNAL_FILE`), record AC4 as **AST-pinned, not session-observed** and skip the subprocess test rather than shipping an unrunnable one.
**Step 7: Record both departures** — `OVERRIDES:` comments **on issue #3634**:

1. the gate's predicate: *the plan's `GRAPH.LIST count < journal_size + 20` assert as the gate* — a whole-server count cannot distinguish this session's residue from pre-existing/foreign graphs;
2. the gate's interaction with the log-and-continue policy (epic cycle-8 P2-3/P2-4): the gate is scoped to `not own.get("failed")` so a transient delete error keeps the journal and does **not** red the suite; note the residual (the UNJOURNALLED mint — a graph materialized with no ownership record at all (LC4/LC7, #5048) is outside the owned ∧ journalled set by construction — plus the concurrent-suite window: under `if not others` the gate never runs while another suite is active). A *silent-success* drop is NOT a residual: the capture is pre-sweep and the post-sweep live probe never consults `own["dropped"]`, so the surviving owned name raises.

Add the matching rows to the epic changelog table (`docs/epics/2026-08-24-test-db-migration/plan.md`, the CI-Fix Changelog / cycle-8 table).
**Step 8: Commit.**

### Task 6: File the LC4/LC7 follow-up

**Intent:** The scoping lists constructor-time / staging write-ahead journalling (LC4/LC7) as in-scope-and-pending; it appears in no task. Per the deferral rule it needs a real re-check mechanism, not silent rot.
**Acceptance:** A follow-up issue exists naming the sites and a trigger; the plan's Out-of-scope section names it.
**Files:** a new GitHub issue; `docs/plans/2026-09-24-3634-graph-leak-reaping.md` (Out of scope)

**Step 1:** File the issue covering `tortoise/hosted_backup.py`'s restore-staging mint, `tortoise/sdk.py`'s org/team mint, and `tortoise/projection/__init__.py`'s two `_journal_append_product` call sites (the redirect append and `from_uri`'s) — post-mint journalling leaves a crash window in which a graph exists with no ownership record. Name the trigger and the dependency on #3634.
**Step 2:** Reference it in Out of scope with the trigger.
**Step 3: Commit.**

### Task 7: Verify and hand off evidence

**Intent:** Prove the change is bounded and crosses no recorded decision beyond the **four** marked departures.
**Acceptance:** Gate-off lane green; the mechanism demonstrated on live fixtures; AC1–AC6 each have a named test or a recorded AST pin; no `allkeys-*` and no `wipe_server` literal widening in the diff.
**Files:** `docs/plans/2026-09-24-3634-graph-leak-reaping.md` (add the `status: live` field at the top, per the sibling plans in `docs/plans/`), `docs/epics/2026-08-24-test-db-migration/plan.md` (the changelog rows from Task 3 Step 8 and Task 5 Step 7)

**Step 1:** Full docker lane — `uv run pytest tests/ -v` with `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
**Step 2:** Demonstrate the **mechanism** on live fixtures (Task 3 Step 7). The 1,745-name census is **historical evidence**, recorded with the archived tarball sha256 `32efc318…` — there is no live 1,608-graph subject (the `:6379` lane was restored on a fresh volume). Do not claim to reclaim it here.
**Step 3:** Confirm the guardrails held:

```bash
git diff "$(git merge-base origin/main HEAD)..HEAD" -- tests/_embedded.py | grep -n 'allkeys' || echo "no allkeys — OK"
grep -c 'startswith(("test_", "tortoise_test"))' tests/_embedded.py   # must print 0 — the literal now has one home
```
**Step 4:** Commit; run `commit-workflow`.

---

## Risks

| Risk | Mitigation |
|---|---|
| Task 2 changes a persistent graph name | Scoped to the test-derived branch only; the `elif ns`/`else` branches have an explicit regression test; the tests name the **real** `registry_control_plane` / `registry_tortoise` values rather than assuming them. |
| Task 3's prefix delete hits a real graph | Reachable only via exact `"1"`; `is_legacy_residue` is deny-safe (non-`str`, `tortoise_restored*`, the URI default, and every owned family refused); AST-pinned to have **no** default call site; the live test asserts a shared registry survives. |
| Task 5's assert reds CI on a preserved name or a blip | Owned set computed by `owns_by_ownership_record` inside the helper; the fixture includes the preserved-but-journalled case; the assert sits outside the broad `except` and is scoped to `not own.get("failed")`. |
| Vocabulary re-fragments | Task 1 ties the four constants **by reference**, rewires the three consumers, and scans for an unregistered **named** constant on the declared surface. |
| Consumers of the renamed registry graph | Enumerated: the only reconstruction is `graph-scripts/backfill_invite_ghost_members.py` (Task 2 Step 5 **deletes** it). `clear_max_sessions_4010.py::test_guard` documents the invariant "the resolved name is never test-prefixed"; it constructs `TortoiseSDK(namespace="registry")` **without an explicit path**, so the redirect never fires and the invariant holds for the CLI path — but it becomes conditionally false for any in-test construction with an explicit `path=`. **Task 2 Step 5 updates that docstring.** |

## Out of scope

Unjournalled legacy `org_*` (104) and the graph named `t` — reachable only via the existing `_sweep_team_strays` opt-in (note: *journalled* `org_*`/`team_*` **are** owned and dropped by the default journal path; only the journal-blind residue needs the gate). LC5 (the `from_uri`/`host=` redirect gap). The atomicity residual #3214. The product storage architecture #4333.

**LC4/LC7 — constructor-time / staging write-ahead journalling: tracked in #5048** (the original #5187 was consolidated into it and is now closed). A graph can be materialized before any ownership record exists — the restore staging graphs in `tortoise/hosted_backup.py` (journalled nowhere in that file) and `FalkorProjection.__init__`'s `_ensure_indexes()` in `tortoise/projection/__init__.py` when a direct construction bypasses the `from_uri`/redirect append. A crash in the window leaves a graph invisible to every journalled sweep, including the new E2E-7 gate, which asserts on **owned ∧ journalled** survivors (so an unjournalled graph is outside its owned set by construction). Distinct from #3634: #3634 owns graphs that *had* a record and drifted out of the sweep. **Trigger:** an AST trip extending `tests/test_write_ahead_mint.py`'s `_MINT_SITES` guard to the constructor/staging materialization sites, so a new unowned mint reddens CI instead of rotting; dated re-check at the next #4333 touch or the next quarterly infra-stability pass.

**Backfill-guard URI-path gate: #5188, fixed in PR #5222.** `graph-scripts/backfill_invite_ghost_members.py` gated the destructive run on the URI-path graph name while the writes land on the SDK-resolved registry graph (`registry_control_plane`), so a test-prefixed `--uri` proceeded without `--yes` for a write to a shared non-test graph. The guard now takes `test_guard(reg.name, args.yes)`, mirroring `clear_max_sessions_4010.py`'s `test_guard(target, args.yes)` on the SDK-resolved name. **Regression test:** `tests/test_backfill_ghost_members_guard.py` (a test-prefixed URI path does not auto-approve a non-test resolved name; a genuinely test-prefixed resolved name still does). #5188 stays open as the tracker until this lands.
