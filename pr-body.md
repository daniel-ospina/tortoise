## Summary
Closes #1475 — deterministic close-on-GC finalizer for leaked FalkorDB/TortoiseSDK objects (the #176/#1005 lifecycle root cause, cut from the ~200-server/suite leak surface). Works around the documented weakref.finalize dead-referent constraint: the finalizer is attached to the OWNER and derefs a plain weakref to the DB client, which redislite's own atexit registration keeps alive as the liveness anchor. Explicit close()/__exit__ and the #1371 fast-atexit are unchanged.

## Verification
- tests/test_embedded_lifecycle_fast_close.py + test_embedded_lifecycle.py: 18 passed
- VGATE: finalizer registry logic audited (no dead-referent deref, last-client semantics, idempotent with explicit close + atexit, never raises in GC context)

Closes #1475
