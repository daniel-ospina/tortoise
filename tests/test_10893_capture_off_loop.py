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

import pytest

from tests.test_hosted_api import TEST_ORG_ID, TEST_TEAM  # noqa: F401  (fixture side effects)
from tests.test_hosted_api import client as client
from tortoise import record_projection as rp
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


async def _charge_one_schema_read() -> None:
    """A `record_projection` schema read ON a running loop — in BOTH shapes.

    The census charges a leg only when `_on_loop()` is true, so the only way to
    prove its second surface intercepts is to call a reader from inside a
    coroutine. `asyncio.run(...)` around this gives exactly that: a real loop,
    one DIRECT read, and one read made from INSIDE a charged SDK leg — the shape
    a capture actually uses, and the one a SHARED re-entry guard swallows.

    The scratch leg is installed on `TortoiseSDK` by the caller (so the census's
    own wrap loop instruments it) and deleted afterwards.
    """
    rp.table_columns()
    TortoiseSDK._census_scratch_read()  # type: ignore[attr-defined]


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
#: before the fix; `_get_registry` 16-36 ms on this tree), while the largest
#: UNDECLARED leg on the path (`_emit_event`, inside the provenance stamp) reads
#: 0.51-0.92 ms and 1.20 ms under coverage — so a 1 ms bar put that leg 0.1 ms
#: from reding a gate whose own docstring argues against timing sensitivity.
#: 5 ms leaves ~4x margin over the measured overhead band and is still ~110x
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
    # The two provenance stamps in `_capture_session_impl`
    # (`_stamp_capture_provenance` and the `_capture_stamp_targets` call beside
    # it — cited by NAME, not line: this PR's own tail rewrite moves every
    # `hosted_api.py` line below it) go through this ONE retry wrapper —
    # but the stamp is JOURNAL-FIRST, so it is TWO synchronous legs per stamp on
    # the loop and the census observes BOTH: this wrapper, and the journal
    # `_emit_event` that precedes it (`sdk.py:11590` — re-derived after the
    # `origin/main` `e0accb8fe6` merge moved it +31 from `11559`). Measured on FOUR
    # consecutive runs of this test on this tree: `_emit_event` 0.51 / 0.56 /
    # 0.83 / 0.92 ms and this wrapper 0.99–3.62 ms per row. ⛔ An earlier cut of
    # this comment said "the census sees a single leg and not two": the second
    # leg was there and sat under the bar, i.e. the figure was stated wider than
    # the measurement (#14086 review, P2). Neither leg is listed separately —
    # this entry is the declaration for the stamp, and both legs are the same
    # owned work.
    # ⛔ THE PREMISE THIS PARAGRAPH USED TO STATE IS WITHDRAWN, SO IT IS GONE.
    # It read: "`_stamp_capture_provenance` is JOURNAL-FIRST (#9350) … so moving
    # it to another thread changes which thread OWNS that lane, which is a
    # behaviour change, not a move of WHERE the same callable runs. It needs its
    # own measurement (#14104)." That reasoning is false and #14104's closing
    # comment withdraws it in full: the lane identity is a `ContextVar`
    # (`_CACHE_WRITE_AUTHORIZED`), opened inside the stamp's OWN body, and
    # `_run_off_loop` confines contextvars by design (`_submit_off_loop` →
    # `confine_context`, deliberately not a bare `run_in_executor`) — so
    # relocating these changes WHERE they run and nothing else. Keeping both
    # paragraphs in one comment block asserted P and ¬P about the same move, which
    # is why the refuted one is replaced rather than annotated (#14086 round-2
    # review, R2-2).
    # ⚠️ This entry is keyed on a GENERIC retry wrapper — measured 36 call sites
    # in `tortoise/sdk.py` (34 besides the two provenance stamps; the only other
    # references in `tortoise/` are prose, in `exceptions.py` and `retry.py`), so
    # it exempts any FUTURE on-loop use of it at any cost, and the size of that
    # exemption is the number that sizes the residual. An earlier cut of this
    # comment said "~9 other call sites", understating it ~4× (#14086 review,
    # P2).
    # ⛔ ITS OWNER IS AN OPEN ISSUE: #14112 off-loads the four stamp sites as ONE
    # closure and then DELETES this entry — deleting it is what
    # closes the generic-wrapper hole above. ⛔ An earlier cut pointed at #14104,
    # which is CLOSED (NOT_PLANNED); an entry whose owner cannot act is a
    # permanent unowned exemption, the one thing this table exists to prevent, and
    # this file's contract says every entry NAMES the issue that owns removing it
    # (#14086 round-2 review, R2-1).
    "_graph_write_with_retry": "#14112 (the capture's four provenance stamp sites, still "
    "inline — the issue lists them; no line number here, because this table's "
    "figures are the ones that rot)",
    # NOT observed on the measured path (the checkpoint that reached it is now
    # off-loaded). Listed because the census drives ONE branch of this handler
    # and the completion gate is reached from others; an entry here is a
    # declaration about ownership, not a claim that the leg runs today.
    # ⚠️ AND, LIKE THE `_graph_write_with_retry` ROW ABOVE, ITS SIZE IS THE
    # PROBLEM: the verdict is keyed on the METHOD NAME alone, so this line
    # exempts EVERY on-loop `sdk._get_proj()` on the capture path, at any cost,
    # whatever its caller — and `_get_proj` is not a capture-specific leg
    # (measured on this tree with `git grep -c '_get_proj('`: 395 lines under
    # `tortoise/`, 394 besides the one `def`; 207 in `tortoise/sdk.py`, where it
    # is defined, i.e. 206 besides that `def` — and the sibling row's 36 for
    # `_graph_write_with_retry(` is likewise its `sdk.py` lines minus its `def`
    # (37 with it), so compare 206 with 36 and not with the def-included 207),
    # it is the cold
    # projection attach. `#4451` owns the completion-gate leg named here and
    # NOT that population; a future on-loop `_get_proj` elsewhere joins the
    # exemption silently and is then reported as `#4451`'s. Declared rather
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
    """THE CENSUS: every sync `TortoiseSDK` method, plus the record-projection
    schema readers (#4625), observed on the loop.

    The invariant test above pins one leg. This one pins the CLASS: it wraps all
    381 synchronous SDK methods (guarding against nested re-entry, which would
    otherwise charge a whole call tree to its outer method) and asserts that the
    set of them observed running on the loop is the declared residual — each
    entry naming its owning issue. It also wraps the three `record_projection`
    schema readers as their own legs (`rp.table_columns`,
    `rp.refused_props_names`, `rp.jsonb_columns`), because they are reached from
    INSIDE an SDK leg and a file read there is otherwise invisible to this
    census — see `saved_rp`, which states both why they get no exemption and
    that the capture this test drives does not reach them (so the surface is
    proven live by a direct on-loop call, not by the capture).

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
    #: A SECOND re-entry guard, for the record-projection readers (#4625). They
    #: are LEAVES of the SDK legs that call them (`_emit_event` →
    #: `_write_record_projection` → `replay_plan` → `table_columns()`), so a
    #: SHARED guard makes the reader probes take the re-entry early-return and
    #: record NOTHING on the only path that matters — the cost stays absorbed in
    #: the `_emit_event` row, which is the blindness this surface exists to end.
    #: ⛔ MEASURED on a first cut that shared `depth`: reached from inside a
    #: charged SDK leg → `rows == ['_emit_event']`; a direct on-loop call →
    #: `rows == ['rp.table_columns']`. The self-check below therefore drives BOTH
    #: shapes and counts them, because a direct call alone cannot see that bug.
    rp_depth = threading.local()
    saved: dict[str, object] = {}
    #: ⛔ THE SECOND SURFACE THIS CENSUS CHARGES (#4625). Wrapping the SDK alone
    #: made the record-projection schema readers INVISIBLE as legs: they are
    #: reached from inside an SDK leg (`_emit_event` → `_write_record_projection`
    #: → `replay_plan`), so their cost was folded into that SDK row — and a
    #: 20-file `Path.read_text` sweep inside `replay_plan` cannot be told apart
    #: from the emit that contains it. #4625 is exactly that shape in production
    #: (the loop blocked 52.406 s, the captured leaf a migration `read_text`).
    #: Charged by NAME, on the module every caller looks them up on —
    #: `record_projection`'s own sites, `cache_projection`
    #: (`from .record_projection import table_columns`, resolved at call time) and
    #: `outside_view` (`_rp.table_columns()`) — so patching here intercepts every
    #: production caller.
    #: ⛔ THEY CARRY THEIR OWN GUARD (`rp_depth`), not the SDK's: sharing it makes
    #: every read reached from inside an SDK leg record nothing, which is the
    #: shape a first cut shipped and this lane's VGATE reproduced.
    #: ⚠️ WHAT THE CAPTURE THIS TEST DRIVES ACTUALLY REACHES, measured rather
    #: than assumed: nothing on this surface. The record write is not armed here
    #: (the only SDK legs observed are `_emit_event`, `_graph_write_with_retry`,
    #: `_get_registry` and `_probe_embedded_busy`), so wrapping the readers alone
    #: would leave a SILENT instrument — which is why the wrap is proven live by
    #: the self-check below instead of by this capture.
    #: ⛔ SO THIS SURFACE IS NOT #4625's FALSIFIER, and must not be read as one.
    #: The falsifier is `tests/test_4625_schema_reads_off_write_path.py` (executed:
    #: restoring the per-consult fingerprint reds it with 360 `stat`/`exists`
    #: operations for three records; restoring the TTL reds it with 720). What
    #: this surface adds is that a schema read a capture DOES reach on the loop is
    #: priced instead of absorbed into `_emit_event`'s row — the blindness #14112
    #: names, one module over.
    #: ⛔ AND THERE IS NO EXEMPTION FOR THESE READS, deliberately: after #4625
    #: the warm path performs NO filesystem operation, so they price at ~0 and
    #: need no row. A row keyed on a reader name would be the generic-wrapper
    #: hole (#14112) one module over — it would exempt any FUTURE on-loop cost
    #: added inside them — so this surface only makes the cost VISIBLE.
    saved_rp: dict[str, object] = {}
    #: A SCRATCH sync method, so the census's OWN wrap loop instruments it and
    #: the self-check can drive the NESTED shape (a schema read made from inside
    #: a charged SDK leg). Deleted in the `finally` below.
    _SCRATCH = "_census_scratch_read"

    def _wrap(name: str, fn, guard: threading.local | None = None):
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
        guard = depth if guard is None else guard

        def _probe(*a, **kw):
            # Re-entry guard: a sync SDK method calling another one must be
            # charged to the OUTER method, or the census reports the whole call
            # tree as one leg per level.
            if getattr(guard, "n", 0):
                return fn(*a, **kw)
            if not _on_loop():
                return fn(*a, **kw)
            guard.n = 1
            t0 = time.monotonic()
            err = None
            try:
                return fn(*a, **kw)
            except BaseException as exc:  # recorded, then re-raised unchanged
                err = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                guard.n = 0
                rows.append({"method": name,
                             "ms": (time.monotonic() - t0) * 1000.0,
                             "thread": threading.current_thread().name,
                             "err": err})

        _probe.__name__ = getattr(fn, "__name__", name)
        _probe.__doc__ = getattr(fn, "__doc__", None)
        return _probe

    # ⛔ WARM THE READERS BEFORE PROBING (#4625). The FIRST consult in a process
    # RESOLVES the schema — a read of every registered migration, measured at
    # ~10.5 ms on this tree (`rp.table_columns`, cold) — and that one-off would
    # otherwise be charged to this capture and red the bar on whichever shard
    # runs this test first, i.e. a gate that is red only on the first run and
    # green on every later one. The one-off is real, but it is
    # `tests/test_4625_schema_reads_off_write_path.py`'s subject (its cold
    # assertion SEES the read and pins that it is once, not per record); what
    # this census prices is the per-request path, which is now zero.
    rp.table_columns()
    rp.refused_props_names()
    rp.jsonb_columns()

    # ⛔ THE INSTALLATION IS INSIDE THE `try` THAT RESTORES IT. The check below
    # RAISES on purpose — a `staticmethod` installed as an instance method is the
    # one defect it exists to make loud — and a raise between `setattr` and this
    # `try` left up to 381 probe-wrapped methods installed on `TortoiseSDK` for
    # every later test in the shard, so the guard fired into a cross-file cascade
    # that no longer named its cause. Registering each wrap in `saved` before it
    # is installed, and wrapping the whole loop in the same `try/finally`, is what
    # makes the restore unconditional (#14086 review F1).
    def _census_scratch_read(*_a, **_kw):
        """A charged SDK leg whose BODY reads the schema — the nested shape.

        Installed on `TortoiseSDK` BEFORE the wrap loop, so the census's own loop
        instruments it like any other sync method; the self-check then calls it
        on the loop and the reader it reaches must still be charged (#4625).
        """
        return rp.table_columns()

    setattr(TortoiseSDK, _SCRATCH, _census_scratch_read)
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

        # ⛔ THE RECORD-PROJECTION READERS, charged as their own legs (#4625),
        # under their OWN re-entry guard: sharing the SDK's `depth` makes a read
        # reached from INSIDE an SDK leg record nothing, which is the only path a
        # capture reaches them by (see `rp_depth`).
        for name in ("table_columns", "refused_props_names", "jsonb_columns"):
            raw = getattr(rp, name)
            saved_rp[name] = raw
            setattr(rp, name, _wrap(f"rp.{name}", raw, guard=rp_depth))

        # ⛔ THE INSTRUMENT CHECKS ITSELF, because the capture below does NOT
        # reach this surface (see `saved_rp`). It drives BOTH shapes and counts
        # them: a DIRECT on-loop call, and one made from inside a CHARGED SDK leg
        # — the shape a shared guard silently swallows, and the only shape a
        # capture ever uses. One call would pass over that bug.
        asyncio.run(_charge_one_schema_read())
        charged = sum(1 for row in rows if row["method"] == "rp.table_columns")
        assert charged == 2, (
            f"the record-projection reader probe charged {charged} of 2 calls — "
            "the DIRECT one and the one made INSIDE a charged SDK leg. A surface "
            "that sees only the direct call is blind on the only path a capture "
            "reaches it, and that blindness is what this surface exists to end "
            "(#4625): the reader probes must carry their OWN re-entry guard, not "
            "the SDK's.")

        r = client.post("/v1/sessions", json={"conversation": _CONV})
    finally:
        for name, original in saved.items():
            setattr(TortoiseSDK, name, original)
        for name, original in saved_rp.items():
            setattr(rp, name, original)
        # …and the scratch leg does not outlive the test (it is not in `saved`
        # when the wrap loop never reached it, e.g. a raise before it).
        if _SCRATCH in vars(TortoiseSDK):
            delattr(TortoiseSDK, _SCRATCH)

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

    # ⛔ AND NO SEPARATE LIVE-BAR FOR THE SECOND SURFACE (#4625 review — a dead
    # assertion was DELETED here). A pin `assert "rp.table_columns" in totals`
    # used to sit at this point, and it could never fail: `totals` is summed over
    # the same `rows` the self-check above already counted, and `charged == 2`
    # requires two `rp.table_columns` rows to exist in it. No mutation could red
    # it without reddening that earlier assertion first, so it read as extra
    # coverage while adding none (mutation contract rule 3). The live-bar for
    # this surface IS the self-check above — both shapes, asserted by count — and
    # the capture path cannot carry a second one today, because it does not reach
    # these readers at all (`saved_rp` / the surface comment above say why).

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
    (`_emit_event` at 1.2 ms comes back as unreported-but-blocking) AND the
    census row above, whose verdict is `_undeclared_blocking` — that row is the
    one the change exists for, since it flipped at 1 ms under coverage.
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
