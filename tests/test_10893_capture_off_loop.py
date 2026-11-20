"""#10893 (second cycle) — the capture handler's SYNCHRONOUS SDK writes must not
run on the event loop.

THE DEFECT THIS PINS
--------------------
The first cycle (`#14032`) took ONE on-loop leg out of the capture/health path
and search still measured 8–17 s, with `pre_handler` ~6 s: a `/v1/search` whose
own legs were inside budget waited six seconds before its handler ran.

`/health`'s `loop_blocks` names the mechanism, from the loop thread's own stack
(innermost LAST), on production:

    hosted_api.py:20741 in _capture_session_impl
      -> sdk._create_event -> create_entity -> derived_embedding.wrapper
      -> _emit_event -> live_write.write -> _write_record_projection
      -> record_writer.write_live_record -> write_records -> write
      -> record_projection.replay -> _apply_tenant_scope

`loop_age_s` 5.5 s, `capture.in_flight` pinned at 8/8 — so the single uvicorn
worker's loop is parked for seconds at a time and there is always a capture in
flight to park it again. That is the whole of `pre_handler`.

`_create_event` is a SYNCHRONOUS SDK write, and on the hosted deployment every
SDK entity write ends in `record_projection.replay` — a blocking round trip per
statement, on the calling thread. Its two neighbours in the same handler
(`_write_session_and_turns`, `_materialize_session_source`) were already
off-loaded; the mint was the member of the class that was missed.

MEASURED (2-turn capture, every `TortoiseSDK` sync method instrumented, local
FalkorDB, no record armed — so this UNDER-states production, where the same call
also replays the journal):

    before:  640 ms of on-loop synchronous SDK work, of which
             `_create_event` was 552 ms — 86 %; next largest leg 26 ms
    after:    81 ms in 4 calls, and `_create_event` is not on the loop at all

The handler's post-write tail (`link_session_entities`, `_session_alive`,
`_update_onboarding_state`, `_sweep_orphaned_writes`, the capture-disclosed
checkpoint) was off-loaded in the SAME cycle, by the commit before this one —
that is why the residual below is a single leg and not six.

WHY THE CENSUS TEST EXISTS AS WELL AS THE INVARIANT ONE
------------------------------------------------------
The invariant test pins the leg that blocked the loop. It cannot see the NEXT
one: the handler's remaining synchronous legs are each tens of milliseconds
today and were invisible while the mint was seconds. The census walks EVERY
synchronous `TortoiseSDK` method, so a leg that returns to the loop fails here
even though nobody wrote a test naming it — which is exactly how the mint
survived five years of one-test-per-leg coverage.

The census is an ALLOW-LIST, not a budget, because a timing budget on a shared
CI runner is flaky in the direction that hides regressions. Every entry names
the issue that owns it.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time

import pytest

from tests.test_hosted_api import TEST_ORG_ID, TEST_TEAM  # noqa: F401  (fixture side effects)
from tests.test_hosted_api import client as client  # noqa: F401
from tortoise.sdk import TortoiseSDK

_CONV = [
    {"role": "user",
     "content": "The database schema needs normalization before the release."},
    {"role": "assistant",
     "content": "Agreed — I will write the migration this week."},
]

#: A worker thread has no running event loop, so this is the discriminator the
#: whole file uses (the same one `test_capture_loop_responsiveness.py` uses).
def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _is_sync_callable(raw) -> bool:
    return not (inspect.iscoroutinefunction(raw)
                or inspect.isasyncgenfunction(raw))


#: Synchronous SDK legs that still run on the loop during a capture, each with
#: the issue that owns removing it. An allow-list rather than a time budget: a
#: wall-clock budget on a shared runner is flaky in the direction that HIDES a
#: regression, and the point of this table is that a new leg cannot join it
#: without a lane deciding to.
#:
#: The whole table is bookkeeping on a path that has already committed the
#: capture — none of it can refuse anything, which is why it is the residual and
#: not the fix.
_ON_LOOP_RESIDUAL: dict[str, str] = {
    # `_record_write_op` -> `metering.record_write_ops` (a blocking MERGE +
    # MATCH in registry mode, a blocking control-plane RPC in Supabase mode).
    # Its docstring records the off-load as tracked by #4451; this census is
    # where that promise is visible. Measured after this cycle's two commits:
    # 2 calls, 81.5 ms — the whole of what is left on the loop.
    "_get_registry": "#4451 (metering registry read inside `record_write_ops`)",
    # NOT observed on the measured path (the checkpoint that reached it is now
    # off-loaded). Listed because the census drives ONE branch of this handler
    # and the completion gate is reached from others; an entry here is a
    # declaration about ownership, not a claim that the leg runs today.
    "_get_proj": "#4451 (onboarding completion gate)",
}


@pytest.fixture()
def llm_mock(monkeypatch):
    """A provider key present, so the capture reaches every write leg."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def test_capture_event_mint_runs_off_the_event_loop(client, llm_mock,
                                                   monkeypatch):
    """The MEASURED leg: `sdk._create_event` must not run on the loop.

    Non-vacuity is asserted first: a path that stops calling the mint leaves
    ``seen`` empty and fails on "never ran" rather than passing silently.
    """
    seen: dict[str, object] = {}

    #: `_create_event` is ALSO the extraction lane's writer, and extraction is
    #: already off-loop — so the observation is keyed on the MINT, which is the
    #: call in the handler body. Recording the last call instead would report
    #: whatever the extractor wrote last, and pass for the wrong reason.
    def _fake_create_event(self, name, eventKind, **kw):
        if eventKind == "sessionCaptured":
            seen["ran_on_loop"] = _on_loop()
            seen["thread"] = threading.current_thread().name
            seen["name"] = name
            seen["eventKind"] = eventKind
        return {"id": "ev_10893_test", "eventId": "ev_10893_test"}

    monkeypatch.setattr(TortoiseSDK, "_create_event", _fake_create_event)
    r = client.post("/v1/sessions", json={"conversation": _CONV})

    assert "ran_on_loop" in seen, (
        f"the session Event mint never ran ({r.status_code}: {r.text[:300]}) — "
        "this run proves nothing")
    assert seen["ran_on_loop"] is False, (
        f"`sdk._create_event` ran ON the event loop (thread "
        f"{seen['thread']!r}) — it is a synchronous SDK write that ends in "
        "`record_projection.replay`, so it parks the single uvicorn worker's "
        "loop for seconds and every concurrent `/v1/search` accrues that time "
        "as `pre_handler` (#10893: measured 5.5 s with capture.in_flight 8/8)")
    assert str(seen["thread"]).startswith("capture-extract"), (
        f"the session Event mint ran on {seen['thread']!r}, not the dedicated "
        "capture pool — a long or stalled write there would occupy the SHARED "
        "default executor's workers and starve every other `to_thread` caller "
        "(#3060); `_run_off_loop(_CAPTURE_EXECUTOR, ...)` is the seam the two "
        "calls beside it already use")
    assert str(seen["name"]).startswith("session_"), (
        f"the mint was handed {seen['name']!r} — the off-load must change WHERE "
        "the write runs, never WHAT it writes (#4014/#1727 byte-parity with "
        "`sdk.capture_session`)")
    assert r.status_code == 200, r.text[:300]


def test_no_sync_sdk_leg_runs_on_the_event_loop_during_a_capture(
        client, llm_mock):
    """THE CENSUS: every sync `TortoiseSDK` method, observed on the loop.

    The invariant test above pins one leg. This one pins the CLASS: it wraps all
    381 synchronous SDK methods (guarding against nested re-entry, which would
    otherwise charge a whole call tree to its outer method) and asserts that the
    set of them observed running on the loop is the declared residual — each
    entry naming its owning issue.

    A leg that costs effectively nothing on the loop (a flag read, an
    environment probe) is not a blocker and is not required to be listed; the
    bar is 1 ms of TOTAL on-loop time for the request, two orders of magnitude
    below the 552 ms the mint cost.

    Measured on the instrumented tree after this cycle's two commits: 4 on-loop
    calls, 81.5 ms total, of which the metering registry read is 81.47 ms.
    """
    rows: list[dict] = []
    depth = threading.local()
    saved: dict[str, object] = {}

    def _wrap(name: str, fn):
        def _probe(self, *a, **kw):
            # Re-entry guard: a sync SDK method calling another one must be
            # charged to the OUTER method, or the census reports the whole call
            # tree as one leg per level.
            if getattr(depth, "n", 0):
                return fn(self, *a, **kw)
            if not _on_loop():
                return fn(self, *a, **kw)
            depth.n = 1
            t0 = time.monotonic()
            try:
                return fn(self, *a, **kw)
            finally:
                depth.n = 0
                rows.append({"method": name,
                             "ms": (time.monotonic() - t0) * 1000.0,
                             "thread": threading.current_thread().name})

        _probe.__name__ = getattr(fn, "__name__", name)
        _probe.__doc__ = getattr(fn, "__doc__", None)
        return _probe

    for name, attr in list(vars(TortoiseSDK).items()):
        if name.startswith("__"):
            continue
        raw = getattr(attr, "__func__", attr)
        if not inspect.isfunction(raw) or not _is_sync_callable(raw):
            continue
        saved[name] = vars(TortoiseSDK)[name]
        setattr(TortoiseSDK, name, _wrap(name, raw))

    try:
        r = client.post("/v1/sessions", json={"conversation": _CONV})
    finally:
        for name, original in saved.items():
            setattr(TortoiseSDK, name, original)

    assert r.status_code == 200, r.text[:300]
    assert saved, "no synchronous TortoiseSDK methods were instrumented"

    totals: dict[str, float] = {}
    for row in rows:
        totals[row["method"]] = totals.get(row["method"], 0.0) + row["ms"]

    # 1 ms of TOTAL on-loop time is the "not a blocker" bar (see the docstring).
    blocking = {m: ms for m, ms in totals.items() if ms >= 1.0}
    undeclared = {m: ms for m, ms in blocking.items()
                  if m not in _ON_LOOP_RESIDUAL}
    assert not undeclared, (
        "synchronous SDK legs ran ON the event loop during a capture and are "
        f"not in the declared residual: "
        f"{ {m: round(ms, 1) for m, ms in undeclared.items()} }. The loop is "
        "the single uvicorn worker's, so each of these is `pre_handler` for "
        "every concurrent `/v1/search` (#10893). Off-load it with "
        "`await _run_off_loop(_CAPTURE_EXECUTOR, ...)` — the seam the writes "
        "around it already use — or, if it must stay, add it to "
        "`_ON_LOOP_RESIDUAL` with the issue that owns removing it. "
        f"Observed totals (ms): { {k: round(v, 1) for k, v in totals.items()} }")
