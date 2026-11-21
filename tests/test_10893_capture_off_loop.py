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
from tests.test_hosted_api import client as client
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
    # where that promise is visible.
    # ⛔ NO FIGURE HERE. This table is about OWNERSHIP — which issue removes the
    # leg — and a millisecond reading copied into it is a SECOND carrier of a
    # number the docstring already states and nothing re-derives; that is how
    # this entry came to say "2 calls, 81.5 ms" while the census docstring below
    # said "4 on-loop calls, 81.5 ms" for the same commits (#14086 cycle-4
    # review, F2). State a magnitude once, or not at all.
    "_get_registry": "#4451 (metering registry read inside `record_write_ops`)",
    # The two provenance stamps in `_capture_session_impl`
    # (`_stamp_capture_provenance` at `hosted_api.py` ~21182 and
    # `_capture_stamp_targets` at ~21209) go through this ONE retry wrapper, so
    # the census sees a single leg and not two: measured 2.50 ms + 4.11 ms on the
    # loop (a throwaway spy keyed on the retry's own `what=` argument).
    # ⛔ NOT a same-cycle off-load: `_stamp_capture_provenance` is JOURNAL-FIRST
    # (#9350) — one `PointRevised` per minted id, then the stamp inside its own
    # `_live_write_scope` lane — so moving it to another thread changes which
    # thread OWNS that lane, which is a behaviour change, not a move of WHERE the
    # same callable runs. It needs its own measurement (#14104).
    # ⚠️ This entry is keyed on a GENERIC retry wrapper with ~9 other call sites,
    # so it exempts any FUTURE on-loop use of it at any cost. #14104 is CLOSED
    # (NOT_PLANNED, 2026-11-21) and its closing comment WITHDRAWS the reason it
    # recorded for leaving the stamps where they are: the write scope is a
    # `ContextVar` and `_run_off_loop` confines context, so relocating them
    # changes WHERE they run and nothing else. ⛔ So this entry is NOT a permanent
    # exemption — it is the declaration that these two legs still run here, and
    # the owner whose work removes them is the stamp off-load (#9350's
    # journal-first ordering is what the move must preserve), not #14104.
    "_graph_write_with_retry": "the capture's two provenance stamps — removal is the off-load "
    "of `_stamp_capture_provenance` / `_capture_stamp_targets` (#14104's own closing "
    "comment withdraws the reason it was left)",
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

    Which entries the census Actually OBSERVES on the path this test drives is
    not restated here as a count. The declared set below is about OWNERSHIP
    (which issue removes the leg); the only membership this file asserts is
    `_get_registry` — the instrument-liveness row — plus the negative rule that
    no UNDECLARED leg may appear at all. ⛔ An earlier cut of this paragraph
    said "ONE on-loop leg, `_get_registry`", and the table two screens up
    contradicted it in the same file ("measured 2.50 ms + 4.11 ms on the loop"
    for `_graph_write_with_retry`) — a figure stated wider than the measurement
    is the same defect this cycle's reviews kept correcting, so the number is
    gone rather than re-copied (#14086 review F3).

    ⛔ Its COST is a reading, not a pin, and that distinction is deliberate:
    nothing here asserts a millisecond figure, because a wall-clock number
    beside an assertion re-stales against the run it came from — on a loaded
    runner it MOVES, and the review that closed this cycle read 68 ms for the
    same leg where the author's box read 81 ms for it. (An earlier cut of this
    docstring and of `_ON_LOOP_RESIDUAL` stated the same measurement twice with
    different call counts, which is what a duplicated figure does.) What IS
    pinned is membership: `_get_registry` must appear in `totals` (the
    instrument-liveness assert below), and no UNDECLARED leg may appear at all.
    """
    rows: list[dict] = []
    depth = threading.local()
    saved: dict[str, object] = {}

    def _wrap(name: str, fn):
        # ⛔ `*a, **kw` and NO named `self`: the probe is installed back under
        # the SAME descriptor kind it was read from (plain / `staticmethod` /
        # `classmethod`), so the descriptor machinery binds exactly the first
        # argument the original got. Naming `self` here is what silently broke
        # all 15 `staticmethod`s on the class — the probe became an INSTANCE
        # method, so `sdk._helper(x)` was invoked as `fn(instance, x)` and raised
        # `… got multiple values for argument 'point_kind'` INSIDE the capture
        # this census measures, leaving every leg downstream of it uncharged
        # (#14086 cycle-4 review, F1 — measured on `_dedup_match_clauses`,
        # `sdk.py:32725`, on the point-creation path).
        def _probe(*a, **kw):
            # Re-entry guard: a sync SDK method calling another one must be
            # charged to the OUTER method, or the census reports the whole call
            # tree as one leg per level.
            if getattr(depth, "n", 0):
                return fn(*a, **kw)
            if not _on_loop():
                return fn(*a, **kw)
            depth.n = 1
            t0 = time.monotonic()
            err = None
            try:
                return fn(*a, **kw)
            except BaseException as exc:  # recorded, then re-raised unchanged
                err = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                depth.n = 0
                rows.append({"method": name,
                             "ms": (time.monotonic() - t0) * 1000.0,
                             "thread": threading.current_thread().name,
                             "err": err})

        _probe.__name__ = getattr(fn, "__name__", name)
        _probe.__doc__ = getattr(fn, "__doc__", None)
        return _probe

    # ⛔ THE INSTALLATION IS INSIDE THE `try` THAT RESTORES IT. The check below
    # RAISES on purpose — a `staticmethod` installed as an instance method is the
    # one defect it exists to make loud — and a raise between `setattr` and this
    # `try` left up to 381 probe-wrapped methods installed on `TortoiseSDK` for
    # every later test in the shard, so the guard fired into a cross-file cascade
    # that no longer named its cause. Registering each wrap in `saved` before it
    # is installed, and wrapping the whole loop in the same `try/finally`, is what
    # makes the restore unconditional (#14086 review F1).
    try:
        for name, attr in list(vars(TortoiseSDK).items()):
            if name.startswith("__"):
                continue
            if isinstance(attr, staticmethod):
                kind: type | None = staticmethod
            elif isinstance(attr, classmethod):
                kind = classmethod
            elif inspect.isfunction(attr):
                kind = None
            else:
                continue
            raw = attr.__func__ if kind is not None else attr
            if not inspect.isfunction(raw) or not _is_sync_callable(raw):
                continue
            saved[name] = attr
            probe = _wrap(name, raw)
            installed = probe if kind is None else kind(probe)
            setattr(TortoiseSDK, name, installed)
            # ⛔ THE INSTRUMENT CHECKS ITS OWN INSTALLATION. Re-installing under the
            # WRONG descriptor kind is the F1 defect, and it is INVISIBLE from the
            # measurement side: a `staticmethod` that becomes an instance method
            # raises off the loop (on `capture-extract_*`), where this census does
            # not record rows at all — so the capture is quietly broken and the
            # census reads clean. Measured: reverting this line to
            # `setattr(TortoiseSDK, name, probe)` and running the test still gives
            # `2 passed`. A structural check is therefore the only falsifier that
            # bites, and it costs nothing (#14086 cycle-5 review, F1'):
            if kind is not None and not isinstance(vars(TortoiseSDK)[name], kind):
                raise AssertionError(
                    f"the census changed {name!r}'s descriptor kind: it is a "
                    f"{kind.__name__} on TortoiseSDK and the probe installed a "
                    f"{type(vars(TortoiseSDK)[name]).__name__}. A `staticmethod` "
                    "installed as an instance method receives `self` as its first "
                    "argument and raises inside the capture being measured — off "
                    "the loop, where this census records nothing, so the run "
                    "would look clean over a broken capture.")

        r = client.post("/v1/sessions", json={"conversation": _CONV})
    finally:
        for name, original in saved.items():
            setattr(TortoiseSDK, name, original)

    assert r.status_code == 200, r.text[:300]
    assert saved, "no synchronous TortoiseSDK methods were instrumented"

    # ⛔ A LEG THAT RAISED LEAVES EVERY LATER LEG UNCHARGED. This is the second
    # half of F1: a broken probe (the `staticmethod` bug above) raised inside the
    # capture, the handler caught/absorbed it, and the census still reported a
    # clean bill because `rows` simply stopped growing at that point. So any
    # exception observed in a probed leg is a FAILURE of the instrument's
    # coverage, whatever the SDK's own fail-open arm then did with it.
    raised = [f"{r['method']}: {r['err']}" for r in rows if r["err"]]
    assert not raised, (
        "a synchronous SDK leg RAISED while the census was measuring it, so "
        "every leg after it on the same path went uncharged and this census is "
        "not evidence about the class — a `TypeError` here is usually the "
        "probe's own signature rather than the SDK's:\n  " + "\n  ".join(raised))

    totals: dict[str, float] = {}
    for row in rows:
        totals[row["method"]] = totals.get(row["method"], 0.0) + row["ms"]

    # ⛔ THE INSTRUMENT MUST BE PROVEN LIVE BEFORE ITS SILENCE MEANS ANYTHING.
    # `saved` only says some methods were WRAPPED. With `rows == []` every
    # assertion below passes vacuously (`totals == {}` -> `blocking == {}` ->
    # `undeclared == {}`) — so a wrapper that stops INTERCEPTING the capture's
    # SDK path (methods relocated to a base class, generated on a subclass,
    # `vars(TortoiseSDK)` no longer holding them) would read as a clean census,
    # which is precisely the failure this census exists to detect. `_get_registry`
    # is the leg the docstring measures on every instrumented run; if #4451
    # removes it, this assertion moves with it.
    assert "_get_registry" in totals, (
        "the census observed no residual leg — the instrumentation did not "
        "intercept the capture's SDK path, so this run proves nothing about "
        f"what is left on the loop (methods wrapped: {len(saved)}; "
        f"methods observed: {sorted(totals) or 'none'})")

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
