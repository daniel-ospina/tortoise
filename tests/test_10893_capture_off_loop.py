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
checkpoint) was off-loaded in the SAME cycle, by the commit before this one.
⛔ NO COUNT OF THE RESIDUAL'S LEGS IS STATED HERE — the counts that do appear below are
facts about the CODE (how many entries the table holds, how many legs one stamp
is, how many call sites a wrapper has), not readings of what the loop carried on
some run. (The `81 ms in 4 calls` figure three lines up is a reading of ONE run and
labelled as the before/after measurement it belongs to; what this disclaimer
refuses to state is any count of the residual's LEGS.) An earlier cut said "that is why the residual below is a single leg and
not six", which was a reading of the observed set — the thing that re-stales
against the run it came from, and that this file elsewhere refuses to state
(#14086 round-2 review, R2-3; round-3 review, R3-3).

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

import httpx
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


def _undeclared_blocking(totals: dict[str, float]) -> dict[str, float]:
    """The census's verdict: legs at/above the bar that no issue owns.

    Factored out of the test body so the THRESHOLD's semantics are pinnable
    WITHOUT a wall clock — see
    `test_the_census_bar_reports_magnitude_and_not_probe_overhead`, which pins
    both ends of the bar on synthetic totals.
    """
    return {m: ms for m, ms in totals.items()
            if ms >= _ON_LOOP_BAR_MS and m not in _ON_LOOP_RESIDUAL}


#: The "this is WORK, not probe overhead" bar, in ms of TOTAL on-loop time for
#: one capture request. ⛔ Deliberately a MAGNITUDE and not the noise floor: the
#: legs that matter here are tens to hundreds of milliseconds (the mint 552 ms
#: before the fix; `_get_registry` 16-36 ms on this tree), while the leg that
#: MOTIVATED the bar — `_emit_event` inside the provenance stamp, then the
#: largest UNDECLARED leg on the path — measured 0.51-0.92 ms and 1.20 ms under
#: coverage, so a 1 ms bar put it 0.1 ms from reding a gate whose own docstring
#: argues against timing sensitivity. ⛔ THAT LEG IS NO LONGER ON THE PATH: the
#: stamp chain is off the loop since #13722/#14112, and the census cannot
#: observe it at any bar (measured: with the bar back at 1.0 only the synthetic
#: assertion below reds). The figure is kept as the bar's REASON, not as a
#: current reading — the margin is sized on the probe-overhead band, which is
#: why it still stands. 5 ms leaves ~4x margin over that band and is still ~110x
#: below the 552 ms the mint cost, i.e. every leg worth declaring is caught
#: (#14086 review, P2).
_ON_LOOP_BAR_MS = 5.0

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
    # NOT observed on the measured path (the checkpoint that reached it is now
    # off-loaded). Listed because the census drives ONE branch of this handler
    # and the completion gate is reached from others; an entry here is a
    # declaration about ownership, not a claim that the leg runs today.
    # ⚠️ ITS SIZE IS THE PROBLEM: the verdict is keyed on the METHOD NAME alone, so this line
    # exempts EVERY on-loop `sdk._get_proj()` on the capture path, at any cost,
    # whatever its caller — and `_get_proj` is not a capture-specific leg
    # (measured on this tree with `git grep -c '_get_proj('`: 395 lines under
    # `tortoise/`, 394 besides the one `def`; 207 in `tortoise/sdk.py`, where it
    # is defined, i.e. 206 besides that `def`). It is the cold projection
    # attach, not a capture-only door. `#4451` owns the completion-gate leg
    # named here and NOT that population; a future on-loop `_get_proj`
    # elsewhere joins the exemption silently and is then reported as `#4451`'s.
    # Declared rather
    # than narrowed because narrowing needs a call-site label the census does
    # not carry; whoever owns `#4451` should prefer the label over the name
    # (#14086 review, P3).
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
    bar is `_ON_LOOP_BAR_MS` of TOTAL on-loop time for the request — two orders
    of magnitude below the 552 ms the mint cost, and set with margin over the
    probe's OWN overhead rather than at the noise floor. ⛔ It used to be 1 ms,
    and the largest UNDECLARED leg on this path (`_emit_event`, 0.51-0.92 ms
    measured here) sat 0.1-0.5 ms inside that: under coverage the same leg
    measured 1.20 ms and flipped this test to FAILED on a leg the table
    deliberately does not require — a false block in a gate whose own docstring
    argues against timing sensitivity (#14086 review, P2). The bar is about
    MAGNITUDE: a leg that matters is tens to hundreds of ms (the mint 552 ms,
    `_get_registry` 16-36 ms here), so 5 ms still catches every leg worth
    declaring while no longer reding on the probe's own cost.

    Which entries the census Actually OBSERVES on the path this test drives is
    not restated here as a count. The declared set below is about OWNERSHIP
    (which issue removes the leg); the only membership this file asserts is
    `_get_registry` — the instrument-liveness row — plus the negative rule that
    no UNDECLARED leg may appear at all. ⛔ An earlier cut of this paragraph
    said "ONE on-loop leg, `_get_registry`", and the table above contradicted
    it in the same file, with a figure stated wider than the measurement — the
    defect this cycle's reviews kept correcting, so the number is gone rather
    than re-copied (#14086 review F3). ⛔ The row that carried that figure
    (`_graph_write_with_retry`) was DELETED by the change this file now pins
    (#13722/#14112), so no prose here points at it as though it still exists.

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
        # all 16 `staticmethod`s on the class — measured: `TortoiseSDK` carries
        # 16 `staticmethod`s and all 16 are sync, so all 16 were wrapped (an
        # earlier cut of this comment said 15, and the number is the exposure)
        # — the probe became an INSTANCE method, so `sdk._helper(x)` was invoked
        # as `fn(instance, x)` and raised `… got multiple values for argument
        # 'point_kind'` INSIDE the capture this census measures, leaving every
        # leg downstream of it uncharged (#14086 cycle-4 review, F1 — measured
        # on `_dedup_match_clauses`, `sdk.py:32756` (+31 from `32725` after the same
        # merge), on the point-creation
        # path).
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
    # ⛔ WHAT THIS ROW CAN AND CANNOT SEE. `_probe` appends a row only when the
    # leg ran ON the loop, so this catches a real raise from an ON-LOOP leg
    # (executed: a plugin wrapping `TortoiseSDK._get_registry` to raise reds it,
    # with the exception text in the message). It does NOT catch the probe's own
    # signature breaking a leg that runs OFF the loop — no row is written there.
    # Measured over all 16 `staticmethod`s: the only two the capture reaches
    # (`_dedup_match_clauses`, `_validate_kind`) run on `capture-extract_0`, so
    # the F1 `TypeError` produced no row and no `err` here at all. That class is
    # the structural descriptor-kind guard above — a SEPARATE check that has to
    # exist precisely because this row cannot see it (#14086 review, P2).
    assert not raised, (
        "a synchronous SDK leg RAISED while the census was measuring it, so "
        "every leg after it on the same path went uncharged and this census is "
        "not evidence about the class:\n  " + "\n  ".join(raised))

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

    # A leg at or above `_ON_LOOP_BAR_MS` of TOTAL on-loop time is the "this is
    # work, not probe overhead" bar (see the docstring for why it is a magnitude
    # and not the noise floor). `_undeclared_blocking` IS the verdict, and it is
    # a module-level function so the threshold can be pinned without a clock.
    undeclared = _undeclared_blocking(totals)
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


def test_the_census_bar_reports_magnitude_and_not_probe_overhead():
    """The bar is a MAGNITUDE, pinned here WITHOUT a wall clock.

    `_ON_LOOP_BAR_MS` used to be 1 ms, which put the largest UNDECLARED leg on
    this path (`_emit_event`, inside the provenance stamp) 0.1-0.5 ms inside it:
    measured here at 0.51-0.92 ms, and at 1.20 ms under coverage, where this test
    went FAILED on a leg the residual deliberately does not require. Loosening a
    bar must not become a blind spot, so both ends are pinned on synthetic
    totals: the measured probe-overhead band is NOT reported, and a leg of the
    size the census exists to catch still is.

    Verified non-vacuous: restoring the bar to 1.0 reds the FIRST assertion
    (`_emit_event` at 1.2 ms comes back as unreported-but-blocking). ⛔ It does
    NOT any longer red the census row above, and this docstring used to claim it
    did: that row flipped at 1 ms only because the stamp's `_emit_event` was ON
    the loop, and #13722/#14112 moved it off — so the real census can no longer
    observe it at any bar (measured on this tree: bar=1.0 → `1 failed, 1 passed`
    across these two tests). The synthetic assertion beside the measured band is
    what remains as the bar's falsifier.
    """
    # The measured overhead band, including the coverage run that flipped the
    # real census — noise, not work.
    assert _undeclared_blocking({"_emit_event": 1.2}) == {}
    # The bar itself is inclusive-and-reporting: anything AT it is reported.
    assert _undeclared_blocking({"a_leg_at_the_bar": _ON_LOOP_BAR_MS}) != {}
    # The mint's own measured cost before the fix, and a leg no one has named.
    assert set(_undeclared_blocking({"_create_event": 552.0})) == {"_create_event"}
    assert set(_undeclared_blocking({"some_new_sync_write": 40.0})) == {
        "some_new_sync_write"}
    # …and a DECLARED leg is never reported, whatever it costs: the table is an
    # ownership declaration, not a budget.
    assert _undeclared_blocking({"_get_registry": 16.29}) == {}


# ══ #13722: the STAMP CHAIN's write, and the loop it used to park ═══════════
# The census above measures COST (ms of on-loop time, against a bar). The test
# below measures the same invariant the other way round — the loop's own
# liveness WHILE the write is in flight — because #13722's symptom was not a
# slow leg but a parked loop: `/health` `loop_blocks.max_age_s` 53.041 s against
# an in-flight population of ~30 records. `_graph_write_with_retry`'s residual
# row above was deleted by the change this pins (its owner was #14112), so this
# test is now the row's replacement: the stamp half of `_capture_session_impl`
# is submitted as ONE closure through `_run_off_loop(_CAPTURE_EXECUTOR, …)`.
# ⛔ MEASURED, so nobody re-derives it — AND MEASURED BOTH WAYS, which is the
# point: with the `await` reverted to an inline call the census above is
# BORDERLINE. The deleted row's per-record cost sits AT the 5 ms TOTAL bar
# (`_ON_LOOP_BAR_MS`), so the same mutation reads `_graph_write_with_retry`
# 5.6 ms -> RED on a loaded box and under the bar -> `2 passed` on two quieter
# runs of this same tree. A verdict that flips with box load is not a falsifier:
# the assertions below are the whole falsifier, which is why the pool identity
# among them is asserted and not just recorded. (Stated as a range on purpose —
# an earlier cut of this comment said the census "cannot see this regression at
# any bar", which a 5.6 ms run falsifies; a single green run is not a range.)
#
# MEASURED, pre-fix vs post-fix, same fixture: pre-fix the ticker completes 0
# ticks inside the blocking window and `/health` answers only after `exited`;
# post-fix the write is off-loop, tens of ticks land inside it, and `/health` is
# answered there. ⛔ NO WALL-CLOCK NUMBER IS ASSERTED — the tick count and the
# in-window probe are the signals with teeth, as in
# `tests/test_graph_write_loop_responsiveness.py`, whose harness this mirrors.
#
# ⛔ WHAT THIS DOES **NOT** CLAIM. Not that fewer DB round trips happen, not that
# statements are batched, and not that per-record wall time improved: the change
# moves WHERE the same callable runs and nothing else (batching is #13722's
# second half, and it can reorder a load-bearing stamp chain — a data defect,
# not a perf win).
#: The blocked window, 2.0 s for the reason `tests/test_graph_write_loop_responsiveness.py`
#: uses the same value: a 1.0 s window was measured FLAKY on a loaded box (one run in
#: four red on the tick count / the in-window probe, the identical selection green on
#: the next two runs). Widening the window is the fix that does not weaken the bound —
#: the bar (10 ticks) is unchanged, so a loop that is truly blocked still yields 0.
_STAMP_STALL_S = 2.0
_STAMP_TICK_S = 0.02
_STAMP_MIN_TICKS_IN_STALL = 10


def _install_blocking_stamp_write(monkeypatch, state: dict) -> None:
    """Block the stamp chain's write, recording where it ran and its window.

    The seam is ``TortoiseSDK._emit_event`` — the ONE journal door the stamp
    opens per id (`_stamp_capture_provenance`'s comprehension) and the door
    whose live arm reaches `record_writer.write_live_record`. Blocking THERE
    stands in for a slow write: on a configured record the same call carries
    the 7 blocking DB round trips, and in this fixture (no record armed) it
    carries the journal append, so the stand-in is faithful either way.

    Keyed on ``type_ == "PointRevised"`` because that is the type the stamp
    emits; blocking every emit would also block legs this test does not touch
    and would let it pass for the wrong reason. Blocks ONCE: the window under
    measurement is the first write's, and every further id would only add
    another ``_STAMP_STALL_S`` to the run.
    """
    real = TortoiseSDK._emit_event

    def _blocking(self, type_, payload=None, **kwargs):
        if type_ == "PointRevised" and "entered" not in state:
            # AGGREGATED, never last-call-wins: a later off-loop call must not
            # erase an earlier on-loop one (the census's own rule above).
            state["ran_on_loop"] = state.get("ran_on_loop", False) or _on_loop()
            state["thread"] = threading.current_thread().name
            state["entered"] = time.perf_counter()
            try:
                time.sleep(_STAMP_STALL_S)  # stand-in for the blocking write
            finally:
                state["exited"] = time.perf_counter()
        return real(self, type_, payload, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "_emit_event", _blocking)


async def _drive_blocked_stamp_write(state: dict):
    """Fire a capture concurrently with a ticker, then probe /health mid-write.

    Ordering is what makes the measurement sound: the ticker starts first and
    is given two periods so it is mid-sleep when the write blocks; the capture
    is launched and awaited only far enough to see it enter the write; /health
    is requested while the write is parked, and ``write_in_flight`` is sampled
    BEFORE that request is issued.
    """
    from tortoise.hosted_api import app

    ticks: list[float] = []
    stop = {"done": False}

    async def _ticker():
        while not stop["done"]:
            ticks.append(time.perf_counter())
            await asyncio.sleep(_STAMP_TICK_S)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as ac:
        tick = asyncio.create_task(_ticker())
        await asyncio.sleep(_STAMP_TICK_S * 2)  # ticker mid-sleep before it
        capture = asyncio.create_task(
            ac.post("/v1/sessions", json={"conversation": _CONV}))
        for _ in range(2000):  # let the capture reach the blocking write
            if "entered" in state:
                break
            await asyncio.sleep(_STAMP_TICK_S / 2)
        assert "entered" in state, (
            "the stamp chain's write was never reached — the capture failed "
            "before it stamped anything, so this run proves nothing about the "
            "loop")
        write_in_flight = not capture.done()
        health = await ac.get("/health")
        health_done = time.perf_counter()
        response = await capture
        stop["done"] = True
        await tick
    return ticks, health, health_done, response, write_in_flight


def test_the_stamp_chain_record_write_does_not_block_the_event_loop(
        client, llm_mock, monkeypatch):
    """#13722 / #14112: the stamp's blocking write must not park the loop."""
    state: dict = {}
    _install_blocking_stamp_write(monkeypatch, state)

    ticks, health, health_done, response, write_in_flight = asyncio.run(
        _drive_blocked_stamp_write(state))

    assert state.get("ran_on_loop") is False, (
        "the stamp chain's write ran ON the event loop (thread "
        f"{state.get('thread')!r}) — every `PointRevised` it journals is 7 "
        "blocking DB round trips through `record_writer.write_live_record`, so "
        "the single uvicorn worker's loop is parked for the whole write and "
        "every concurrent request accrues it as `pre_handler` (#13722's 53 s "
        "`loop_blocks` episode)")
    assert str(state.get("thread", "")).startswith("capture-extract"), (
        f"the stamp chain's write ran on {state.get('thread')!r}, not the "
        "dedicated capture pool — `asyncio.to_thread` would put it on the "
        "loop's SHARED default executor, the starvation path #3060 recorded "
        "and `_submit_off_loop` exists to avoid. It confines contextvars "
        "either way, so the POOL is the whole difference: "
        "`_run_off_loop(_CAPTURE_EXECUTOR, ...)` is the seam (#13722/#14112)")

    entered, exited = state["entered"], state["exited"]
    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= _STAMP_MIN_TICKS_IN_STALL, (
        f"the event loop completed only {len(in_stall)} tick(s) during a "
        f"{_STAMP_STALL_S:.1f}s blocked stamp write (need "
        f"{_STAMP_MIN_TICKS_IN_STALL}) — the write is blocking the loop, so "
        "every concurrent request waits for it (#13722)")

    # ⛔ INSTRUMENT INTEGRITY, not behaviour: this asserts the PROBE, and it is
    # kept because it is REACHABLE — it guards a real vacuum. A probe that missed
    # the route (a 404 from a moved path, a malformed body) is served by the
    # router with no I/O, so it is as fast as the real in-memory `/health` and
    # would sail through the timing row below and be read as a request the loop
    # served. What is NOT asserted is the status VALUE (`ok`/`degraded`):
    # `/health` derives it from in-memory state (#2850), so no mutation of this
    # diff can move it — that row was dead weight and was deleted (review F2,
    # PR #14520). ⚠️ The sibling `tests/test_graph_write_loop_responsiveness.py`
    # keeps the status-value row instead, on the rationale that "asserting 200
    # alone could not fail"; that divergence is deliberate here, and the 200 row
    # demonstrably reds on a 404.
    assert health.status_code == 200, (
        "the /health probe never reached the handler "
        f"({health.status_code} {health.text[:200]!r}), so the in-window timing "
        "asserted below measures nothing (#13722)")
    assert health_done < exited, (
        "/health was NOT served while the stamp write was blocked (answered "
        f"{(health_done - exited) * 1000:.0f} ms AFTER the block released) — a "
        "concurrent request queued behind the write instead of being served, "
        "which is the process-wide stall this off-load exists to prevent "
        "(#13722)")
    assert write_in_flight, (
        "the capture had already returned before /health was issued, so this "
        "run proves nothing about a request served DURING the blocking window "
        "(#13722)")

    assert response.status_code == 200, (
        f"the capture did not complete after the off-loaded stamp write "
        f"({response.status_code}: {response.text[:200]})")
