---
title: "Implementation Plan #1475 — lifecycle finalize (close-on-GC): pinned-client-weakref finalizer + WeakMethod atexit seams"
type: engineering
domain: platform
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: tortoise-embedded
aboutObjects: redislite-lifecycle, embedded-reaper, fast-atexit, test-isolation
---

<!-- research-path: issue-scoping comment on #1475 (comment-5341464145) -->

## Objective

Deterministic close-on-GC for leaked (never-explicitly-closed) embedded SDK/projection objects — the #176/#1005 root cause — so the per-suite leaked-server count drops from ~200 toward <50 without touching explicit `close()`/atexit semantics.

## Why the obvious design is impossible (verified)

- `weakref.finalize(obj, cb)`: `cb` cannot dereference `obj` — it is already dead (empirically verified: weakref captured pre-`del` derefs to `None` inside the callback).
- SDK/projection/FalkorDB objects are **pinned alive until exit** by two atexit strong-refs: ours (`sdk.py:868`, `projection/__init__.py:424` bound-method registrations) and redislite's internal `client.py:448` (`atexit.register(self._cleanup)`).

## Design (the workaround)

1. **`tortoise/embedded_lifecycle.py`** — add the finalizer machinery:
   - `register_gc_close(owner, db)`: `weakref.finalize(owner, _gc_close, weakref.ref(db))`. The captured weakref is the "registry entry".
   - `_gc_close(db_ref)`: derefs the pinned client (never its own dead referent) and closes via the #1371 seam — `atexit_fast_close(getattr(db, "client", db))` if the ephemeral+flag gating holds (fast NOSAVE), else idempotent `_t_close()` (SAVE). Never raises.
2. **`tortoise/sdk.py` (line 868)** + **`tortoise/projection/__init__.py` (line 424)** — `_atexit.register(weakref.WeakMethod(self._atexit_close))`: at exit, `_atexit_close` runs only if the object is still alive. This is the pin-break that makes GC possible. Explicit close/`__exit__`/the fast-close seam are untouched.
3. **`tortoise/projection/__init__.py`** — after `self.db` is created, `register_gc_close(self, self.db)`. Single registration point covers both leaked-SDK (dies with its projection) and direct-projection leaks. Host-mode clients (no redislite pin) deref to None → safe no-op.
4. **`tortoise/__init__.py`** — **unchanged** (FalkorDB's own atexit is the exit-time net and it's pinned by redislite regardless).

## TDD tasks

1. **Red tests** in `tests/test_embedded_lifecycle.py`:
   - `test_leaked_projection_closes_on_gc` — construct `FalkorProjection`, capture `pid`, `del` + `gc.collect()`, poll pid dead (bounded).
   - `test_leaked_sdk_closes_on_gc` — `TortoiseSDK` + forced projection, same assertion (the dominant suite leak).
   - `test_explicit_close_then_gc_safe` — explicit close → `del` + `gc.collect()` → no crash, server stays dead, close-recorder counts one close.
   - `test_shared_server_survives_single_gc` — two projections on one path; GC one → server alive; GC other → dead (last-client guard).
2. **Implement** steps 1-3 above.
3. **Green:** `tests/test_embedded_lifecycle.py` + `tests/test_embedded_lifecycle_fast_close.py` + `tests/test_embedded_concurrency.py -k chaos` (+ broader lifecycle/concurrency suite if CI budget allows).

## Risks / mitigations

- **Fast NOSAVE at GC mid-suite** — only for ephemeral test-tree + `TORTOISE_FAST_ATEXIT=1` (the conftest/CI env); user-path and production servers take the normal SAVE close. Mirrors the #1371 contract exactly.
- **Shared-server kill** — inherited `_connection_count() > 1` guard in `atexit_fast_close`; regression-tested.
- **Exit-ordering (finalize vs atexit)** — both paths are idempotent + error-swallowing; whichever runs first closes, the other no-ops (the `_tortoise_fast_closed` guard short-circuits the fast path).
- **Thread-safety** — finalizer may run on the GC thread; close paths are the same idempotent ones the atexit seam uses.

## Verification

- `uv run pytest tests/test_embedded_lifecycle_fast_close.py tests/test_embedded_lifecycle.py -q -p no:cacheprovider --timeout=300`
- `uv run pytest tests/test_embedded_concurrency.py -k chaos -q -p no:cacheprovider --timeout=300`
- CI leaked-server metric (post-merge, watch indicator (b)).
