"""#10626 — the #5329 vocabulary-gap census gets a RUN-LEVEL READER.

WHAT THIS PINS
--------------
`#10475` landed a census for the boundary's vocabulary gap: when a fold decision
absorbs a one-sided token that reached no identity dimension, `_boundary` counts
it (`extractor_v2._VocabularyGapCensus`) and emits one `WARNING` the first time
it sees that token. Invariant **I2**
(`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md:927`) is explicit about what
reporting means —

    "HOLD, or degrade **loudly into the run's census**. Silent absence is never
    an outcome."

— and until this change the census was counted and never reported anywhere a run
could see it: the `WARNING` is a PROCESS log line, capped at
`_VOCABULARY_GAP_LOG_CAP` distinct tokens per process, so once the 32nd token
arrives the alarm is spent for the life of the process while the token set that
answers *which* markers we are losing stays in memory behind a function nobody
calls. `vocabulary_gap_report()` / `vocabulary_gap_snapshot()` had ZERO
production call sites.

THE READER
----------
`extract_session_v2` — the entry `sdk`, `hosted_api`, `ingest_v2` and
`run_v2_pipeline` all route through — now installs a RUN-scoped accumulator for
the duration of the run (`_run_scoped_vocabulary_gap`) and appends its reading to
the run's OWN receipt:

* `result["stats"]["vocabulary_gap"]` — the reading, present WHEN THE RUN
  RECORDED A LOSS (a token sample, a pair-read residual, or oversize tokens) and
  ABSENT otherwise, the shape `vocabulary_gap_snapshot()` reports plus
  `"scope": "run"`. It rides `stats` because the SDK's capture fold
  (`sdk._capture_stats_with_step_witness`) copies `out["stats"]` wholesale, so
  the reading reaches the product receipt (`capture_session`'s `stats`) with no
  second seam — the #2335 WI-1a lesson `error_census` taught.
* `result["warnings"]` — ONE entry naming the absorbed tokens, under the SAME
  predicate: this is the run's alarm, and unlike the process log line it cannot
  be spent by an earlier run.

⛔ ABSENT-WHEN-NO-DATA IS PART OF THE CONTRACT, not a detail. `stats` is passed
through verbatim by the hosted receipt (`hosted_api` → `"stats": meta.get("stats")
or {}`) and read by the cost seam, and the M2 lane documents a genuine zero-call
path as a clean `{}`. An always-present reading made that `{}` impossible on M2,
which reds `tests/test_capture_cost_measurement.py::
test_emit_call_site_writes_no_cost_row_when_props_is_none` — the arm
`test_a_zero_fold_m2_capture_leaves_stats_untouched` here pins the same fact
hermetically.

The SAME reader is wired onto the opt-in M2 capture lane
(`sdk.TortoiseSDK._extract_session_llm`), because that lane does not route
through the v2 entry and its in-capture fold is a `DETACH DELETE` of the
duplicate node — the most destructive fold in the product. Covering only the v2
lane would have left the deleting lane silent. The M2 arm is hermetic (in-capture
dedup needs no S3 search), so it runs on any lane; the v2 arm needs a real
searchable graph.

⚠️ **The fold decision is UNCHANGED, and that is deliberate.** Whether a
shape-candidate marker should fail CLOSED is #5329's owner-gated question —
`§16.4`'s two deliberately FAIL-OPEN classes make that a REOPEN, not a guard
tweak — so a reader that quietly implemented it would be deciding it. The last
test here pins that the reader did not: the boundary still PERMITS the fold while
the receipt now names what it absorbed.

WHY A RUN-SCOPED ACCUMULATOR AND NOT A SNAPSHOT DELTA: a before/after delta over
the process-global census is a SUPERSET of the run's own folds (a concurrent
capture may have folded inside the window). A superset is the safe direction for
a COUNT, but not for token NAMES — a hosted process serves many tenants, so a
receipt echoing a concurrent tenant's token would be a cross-tenant leak. The
accumulator is scoped to the run's own EXECUTION CONTEXT (a `ContextVar`, not a
thread-local), so a receipt names only its own run's tokens AND a fold that runs
on a worker carrying that context still lands on this run's reading
(`test_a_fold_on_a_context_carried_worker_lands_on_the_run_reading`).

RUNNING IT: the S3 leg must be a REAL, searchable backend (extractor_v2 skips
the search on an embedded FalkorDBLite store), so these tests take the docker
lane and skip VISIBLY elsewhere.
"""
from __future__ import annotations

import ast
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils  # noqa: E402, RUF100
from tests._live_utils import _skip_unless_live_uri  # noqa: E402, RUF100
from tortoise import extractor_v2 as v2  # noqa: E402, RUF100
from tortoise.search_engine import reset_circuit_breakers  # noqa: E402, RUF100

#: #5329's own exemplar: a negator that appears in NO boundary vocabulary, so the
#: boundary reads the pair as a one-sided broadening and PERMITS the fold.
UNLISTED_MARKER = "hardly"

#: The claim the seeded prior carries, and the claim that folds onto it.
PRIOR_CLAIM = "we ship on fridays"
GAP_CLAIM = f"we {UNLISTED_MARKER} ship on fridays"

#: A claim with no prior anywhere, so the run's boundary compares nothing and the
#: reading is genuinely empty (the control arm).
CLEAN_CLAIM = "the quarterly audit completed on its own schedule"


class _ScriptedModel:
    """The minimum adapter the v2 pipeline needs: a story, an S4 review, an S2 map.

    Same shape the extractor suite uses (`tests/test_extractor_v2.py`'s
    ``_Model``): the pipeline dispatches on the SYSTEM prompt, so the S2 answer —
    the only one whose content this file cares about — is the default branch.
    """

    last_finish_reason = "stop"

    def __init__(self, claim: str) -> None:
        self.claim = claim
        self.calls = 0

    def complete(self, *, system: str, user: str, max_tokens=None) -> str:
        self.calls += 1
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        if "GAP REVIEWER" in system:
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": []}')
        return json.dumps({
            "entities": [], "events": [], "operators": [],
            "points": [{"content": self.claim, "pointKind": "statement"}],
        })


@pytest.fixture(autouse=True)
def _fresh_census():
    """Each test measures its own window — the process census is global."""
    v2.reset_vocabulary_gap_census()
    yield
    v2.reset_vocabulary_gap_census()


@pytest.fixture()
def sdk():
    """A live, SEARCHABLE graph — its own graph name, so no peer test's priors
    can answer this run's S3 search.

    Teardown follows the file family's convention (`tests/test_source_fts.py`):
    delete the per-run graph through `tests._live_utils.drop_test_graph`, then
    close. A bare `close()` leaves the GRAPH OBJECT on the shared docker
    instance, still counted against the server's `CACHE_SIZE` — the #8746/#7357
    cache-eviction churn that helper exists to remove — and an unclosed client
    leaks a redis connection per test.
    """
    _skip_unless_live_uri()
    from tortoise.sdk import TortoiseSDK

    reset_circuit_breakers()
    name = f"test_10626_{uuid.uuid4().hex[:10]}"
    s = TortoiseSDK(graph_name=name)
    try:
        yield s
    finally:
        proj = getattr(s, "_proj", None)
        if proj is not None:
            # log-and-continue by contract (#8746): a teardown hiccup must not
            # red a passing test, and the session sweep is the backstop.
            _live_utils.drop_test_graph(proj)
        reset_circuit_breakers()
        s.close()


def _run(sdk, claim: str, session_id: str) -> dict:
    """One REAL v2 run through the production entry point."""
    conversation = [{"role": "user", "content": claim},
                    {"role": "assistant", "content": "noted"}]
    return v2.extract_session_v2(_ScriptedModel(claim), conversation,
                                 sdk=sdk, session_id=session_id)


def _gap_warnings(out: dict) -> list[str]:
    return [w for w in (out.get("warnings") or [])
            if "boundary vocabulary gap" in w]


# ── the reading reaches the run's own receipt ───────────────────────────────


def test_a_run_that_absorbs_an_unlisted_marker_reports_it_in_its_own_receipt(sdk):
    """The seam. Before this change the run's receipt carried NO trace of the
    census — the gap existed only as a process log line and an in-memory dict
    nobody read (measured on #10626: `stats` had no census key, `warnings` only
    the unrelated source-turn note)."""
    sdk.create_point("statement", PRIOR_CLAIM)

    out = _run(sdk, GAP_CLAIM, "gap-1")

    reading = out["stats"]["vocabulary_gap"]
    assert reading["scope"] == "run", (
        "the reading must be labelled as THIS run's, not a process window")
    assert reading["fold_decisions"] >= 1, (
        "the run's boundary compared the pair and permitted a fold, so the "
        "reading must have counted that decision")
    assert reading["tokens"].get(UNLISTED_MARKER, 0) >= 1, (
        f"{UNLISTED_MARKER!r} appears in no boundary vocabulary and was "
        f"absorbed by this run's fold decision, so the run's census must name "
        f"it; reading={reading}")

    gap = _gap_warnings(out)
    assert len(gap) == 1, f"expected ONE run-level alarm, got {gap}"
    assert repr(UNLISTED_MARKER) in gap[0], (
        "the run-level alarm must NAME the token — a bare count is not "
        f"actionable; got {gap[0]!r}")


def test_a_run_with_no_fold_reports_nothing_and_no_alarm(sdk):
    """The control arm: this is what makes the reader a READER — and it pins
    ABSENT-WHEN-NO-DATA.

    A run whose boundary compared nothing carries NO `vocabulary_gap` key and
    emits no gap warning, so `with a gap` and `without` are distinguishable from
    the receipt alone — and the key's absence is what keeps the M2 lane's
    documented `stats == {}` zero-call contract intact."""
    out = _run(sdk, CLEAN_CLAIM, "gap-clean")

    assert "vocabulary_gap" not in (out.get("stats") or {}), (
        "a run that recorded no loss must not carry a fabricated (empty) "
        f"reading; stats={out.get('stats')}")
    assert _gap_warnings(out) == []
    # The process census is the thing that DID see nothing — so the absence
    # above is the run scope being quiet, not the boundary failing to run.
    assert v2.vocabulary_gap_snapshot()["fold_decisions"] == 0, (
        v2.vocabulary_gap_snapshot())


def test_the_reading_is_this_run_not_the_process_lifetime(sdk):
    """A process-global counter cannot answer "what did THIS run absorb" — the
    second run must not inherit the first run's tokens."""
    sdk.create_point("statement", PRIOR_CLAIM)
    first = _run(sdk, GAP_CLAIM, "gap-a")
    assert first["stats"]["vocabulary_gap"]["tokens"], first["stats"]

    # The process census still holds the first run's token — i.e. the second run
    # is NOT reading an empty census, only an empty WINDOW.
    assert v2.vocabulary_gap_report().get(UNLISTED_MARKER, 0) >= 1

    second = _run(sdk, CLEAN_CLAIM, "gap-b")
    assert "vocabulary_gap" not in (second.get("stats") or {}), (
        "a run reported a reading it never earned — the reading is a process "
        "lifetime total, not this run's")
    assert _gap_warnings(second) == []


def test_a_peer_fold_outside_the_run_window_cannot_earn_it_a_reading():
    """This is what separates a RUN-SCOPED accumulator from a SNAPSHOT DELTA.

    `test_the_reading_is_this_run_not_the_process_lifetime` above only reds a
    reader that reports an UNDELTED process total: its second run's window
    contains no fold at all, so a `before/after` delta reader also reports
    nothing there and passes identically (review P2). A delta reader is the
    cheap wrong answer — it needs no propagation, no `ContextVar`, and no
    per-run accumulator, and it *is* wrong: any fold landing on a bare worker
    between the two snapshots is attributed to whatever run is open.

    So this arm folds from a context that does NOT carry the run's scope, INSIDE
    the window, and requires the receipt to stay empty. A delta reader inherits
    the token and reds here; the run-scoped accumulator does not see it and
    stays quiet.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> dict:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # No `contextvars.copy_context()` — this worker is a PEER of the
            # run, exactly like the bare arm in the propagation test above.
            pool.submit(v2.fold_allowed, GAP_CLAIM, PRIOR_CLAIM).result()
        return {"stats": {}, "warnings": []}

    out = v2._run_scoped_vocabulary_gap(_run)()

    # The fold really happened, and the PROCESS census really saw it — so the
    # silence below is the run scope, not a fold that never ran.
    assert v2.vocabulary_gap_report().get(UNLISTED_MARKER, 0) >= 1, (
        "fixture: the bare-worker fold must reach the process census, otherwise "
        "this arm proves nothing")
    assert "vocabulary_gap" not in (out.get("stats") or {}), (
        "a peer's fold outside the run window was attributed to this run — "
        "that is a snapshot DELTA, not a run-scoped accumulator; "
        f"stats={out.get('stats')}")
    assert _gap_warnings(out) == []


def test_the_per_process_log_cap_cannot_spend_the_run_level_record(sdk, monkeypatch):
    """The defect #10626 names: after 32 distinct tokens the WARNING is spent for
    the life of the process. The run-level reading must survive that."""
    monkeypatch.setattr(v2, "_VOCABULARY_GAP_LOG_CAP", 0)
    sdk.create_point("statement", PRIOR_CLAIM)

    out = _run(sdk, GAP_CLAIM, "gap-capped")

    reading = out["stats"]["vocabulary_gap"]
    assert reading["tokens"].get(UNLISTED_MARKER, 0) >= 1, (
        "with the process log cap exhausted the run's own record must still "
        f"name the token; reading={reading}")
    assert repr(UNLISTED_MARKER) in _gap_warnings(out)[0]


# ── and it decides nothing (#5329 stays owner-gated) ───────────────────────

def test_the_m2_capture_lane_reports_the_gap_on_its_own_receipt(tmp_path, monkeypatch):
    """The OTHER capture lane, and the more destructive one.

    `TORTOISE_SESSION_EXTRACTOR=m2` does not route through `extract_session_v2`:
    it folds in-capture through `dedup_classify.rephrase_hit`, and the SDK then
    DETACH-DELETEs the folded node. Hermetic — in-capture dedup needs no S3
    search — so this arm runs on every lane, embedded included.
    """
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "m2.db"))
    try:
        conversation = [
            {"role": "user", "content": PRIOR_CLAIM},
            {"role": "assistant", "content": "noted"},
            {"role": "user", "content": GAP_CLAIM},
        ]

        out = sdk.capture_session(conversation, session_id="gap-m2")
    finally:
        sdk.close()

    reading = out["stats"]["vocabulary_gap"]
    assert reading["scope"] == "run"
    assert reading["tokens"].get(UNLISTED_MARKER, 0) >= 1, (
        "the M2 lane folded a restatement across an unlisted marker and "
        f"DETACH-DELETEd the duplicate; its receipt must name the token; "
        f"reading={reading}")
    gap = _gap_warnings(out)
    assert len(gap) == 1 and repr(UNLISTED_MARKER) in gap[0], gap


def test_a_zero_fold_m2_capture_leaves_stats_untouched(tmp_path, monkeypatch):
    """ISSUE 1 (review round 4): the reader must not make `stats` non-empty on
    EVERY M2 capture.

    `sdk._extract_session_llm` documents `stats == {}` on a genuine zero-call
    path, the hosted receipt passes `stats` through verbatim
    (`"stats": meta.get("stats") or {}`) and
    `tests/test_capture_cost_measurement.py::
    test_emit_call_site_writes_no_cost_row_when_props_is_none` asserts it — so an
    always-present reading reds a real, CI-selected test. Here the capture folds
    NOTHING (a claim with no prior and no unlisted marker), so the reader must
    leave `stats` exactly as the lane built it.
    """
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "m2-clean.db"))
    try:
        out = sdk.capture_session(
            [{"role": "user", "content": CLEAN_CLAIM},
             {"role": "assistant", "content": "noted"}],
            session_id="gap-m2-clean")
    finally:
        sdk.close()

    assert "vocabulary_gap" not in out["stats"], (
        "a run that folded nothing must not carry a fabricated reading — the "
        "M2 lane's zero-call contract is a clean stats dict; "
        f"stats={out['stats']}")
    assert _gap_warnings(out) == [], out.get("warnings")


def test_the_reader_does_not_change_the_fold_decision():
    """The reader reports; it must not answer #5329's open question.

    Failing closed on a shape-candidate marker is an OWNER decision (§16.4's two
    deliberately FAIL-OPEN classes make it a reopen), so the boundary must still
    PERMIT this fold while the census names what the fold absorbed. If a future
    change makes this `False`, it decided #5329 without the owner."""
    assert v2.fold_allowed(GAP_CLAIM, PRIOR_CLAIM) is True
    assert v2.vocabulary_gap_report().get(UNLISTED_MARKER, 0) >= 1


# ── the scope PROPAGATES, it does not merely isolate (review round 4, obs. A) ──

def test_a_fold_on_a_context_carried_worker_lands_on_the_run_reading():
    """Observation A: the run scope must follow the run, not just exclude peers.

    The census's own docstring says the boundary runs on extractor worker threads
    (`_VocabularyGapCensus`: "Thread-safe (the boundary runs on extractor worker
    threads)"), so a `threading.local` scope would drop a fold that happened on a
    worker: the fold happens, no counter sees it, and the run's receipt reports an
    EMPTY reading — the "silent absence" invariant I2 forbids. The scope is a
    `ContextVar` — the idiom the stdlib recommends for state that must not bleed
    across concurrent code, and the one this repo already PROPAGATES into its
    capture pool (`hosted_api._run_off_loop` copies the caller's contextvars into
    the worker, #2185) — so a worker CARRYING the run's context lands on the
    run's reading.

    The second fold pins the honest BOUNDARY of that guarantee: a worker whose
    context was never copied sees nothing, and must not be attributed to the run.
    """
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> dict:
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            carried = pool.submit(ctx.run, v2.fold_allowed,
                                  GAP_CLAIM, PRIOR_CLAIM).result()
        with ThreadPoolExecutor(max_workers=1) as pool:
            bare = pool.submit(v2.fold_allowed,
                               GAP_CLAIM, PRIOR_CLAIM).result()
        return {"stats": {}, "warnings": [], "carried": carried, "bare": bare}

    out = v2._run_scoped_vocabulary_gap(_run)()

    assert out["carried"] is True and out["bare"] is True
    reading = out["stats"]["vocabulary_gap"]
    assert reading["tokens"].get(UNLISTED_MARKER, 0) >= 1, (
        "a fold executed by a worker carrying the run's context was not recorded "
        f"on the run's reading; reading={reading}")
    assert reading["fold_decisions"] == 1, (
        "exactly the context-carried fold belongs to this run — a worker whose "
        f"context was never copied must not be attributed to it; reading={reading}")


# ── the alarm NAMES the shape it recorded (review round 5, non-blocking) ──────
#
# `_vocabulary_gap_recorded_a_loss` attaches the reading on THREE shapes, and
# only the first is an UNNAMED token: the token sample; the PAIR-READ residual (a
# token a CLOSED connective/state list does name, so the guard did not fire on
# this pair); and oversize tokens (counted, never retained). The alarm's trailing
# sentence described all three as having "reached NO identity dimension", which is
# FALSE for the residual, and it enumerated only the first two — so an
# oversize-only loss rendered as "this run's folds absorbed ." Both strings ship
# on a CUSTOMER-VISIBLE receipt field (`stats` / `warnings`) on every capture
# lane, so the alarm must name the shape it actually recorded.


def _run_recording(*absorbed_tokens: str) -> dict:
    """One wrapped run whose boundary records exactly ``absorbed_tokens``."""
    def _run() -> dict:
        for token in absorbed_tokens:
            v2._record_vocabulary_gap([token])
        return {"stats": {}, "warnings": []}

    return v2._run_scoped_vocabulary_gap(_run)()


def test_an_oversize_only_loss_alarms_in_a_sentence_and_names_its_shape():
    """Reachable: `_recorded_a_loss` fires on `oversize_observations` ALONE.

    Pre-fix this produced the literal sentence "this run's folds absorbed .",
    with the oversize count nowhered in the alarm (it was only on the reading).
    """
    oversize = "z" * (v2._VOCABULARY_GAP_MAX_TOKEN + 1)

    out = _run_recording(oversize)

    reading = out["stats"]["vocabulary_gap"]
    assert reading["oversize_observations"] == 1, reading
    assert reading["tokens"] == {} and reading["pair_read_residual"] == 0, reading

    warning = _gap_warnings(out)[0]
    assert "absorbed ." not in warning, f"empty enumeration: {warning!r}"
    assert "absorbed ;" not in warning, f"dangling separator: {warning!r}"
    assert "1 oversize token(s)" in warning, (
        f"the alarm must NAME the oversize shape it recorded; got {warning!r}")


def test_a_pair_read_only_loss_does_not_claim_an_unnamed_marker_was_lost():
    """`because` IS named — by a closed connective list (`_PAIR_READ_BUCKETS`).

    The flat trailing sentence told the reader the opposite ("Each reached NO
    identity dimension"), which is the residual's whole reason for being kept out
    of `tokens` (`_pair_read_bucket`'s docstring)."""
    out = _run_recording("because")

    reading = out["stats"]["vocabulary_gap"]
    assert reading["tokens"] == {} and reading["pair_read_residual"] == 1, reading

    warning = _gap_warnings(out)[0]
    assert "absorbed ." not in warning, f"empty enumeration: {warning!r}"
    assert "1 named-but-unread pair-read token(s)" in warning, warning
    assert "NO identity dimension" not in warning, (
        "a NAMED connective must not be reported as an unnamed marker that "
        f"reached no identity dimension; got {warning!r}")
    assert "NAMED by a closed list" in warning, (
        f"the alarm must say the residual IS named; got {warning!r}")


def test_a_mixed_loss_scopes_the_unnamed_claim_to_the_unlisted_sample():
    """Both shapes at once: the "no identity dimension" claim belongs to the
    unlisted sample, so it must not blanket the named residual."""
    out = _run_recording(UNLISTED_MARKER, "because")

    reading = out["stats"]["vocabulary_gap"]
    assert reading["tokens"] == {UNLISTED_MARKER: 1}, reading
    assert reading["pair_read_residual"] == 1, reading

    warning = _gap_warnings(out)[0]
    assert repr(UNLISTED_MARKER) in warning, warning
    assert "1 named-but-unread pair-read token(s)" in warning, warning
    assert warning.index("NO identity dimension") < warning.index(
        "named-but-unread"), (
        f"the unnamed claim must stay with the unlisted sample; got {warning!r}")


def test_the_alarm_is_never_malformed_even_for_an_empty_reading():
    """Defensive: `_attach` never calls this on a zero reading, but a caller that
    did would otherwise ship "this run's folds absorbed ." to a receipt."""
    warning = v2._vocabulary_gap_run_warning(
        v2._VocabularyGapRunAccumulator().reading())
    assert "absorbed ." not in warning, warning
    assert "absorbed ;" not in warning, warning


def test_a_truncated_sample_is_named_in_the_alarm():
    """Review P2: past the SAMPLE cap the receipt must say its list is incomplete.

    `_VOCABULARY_GAP_CAP` bounds the retained SAMPLE, not the token SIZE (that
    is `_VOCABULARY_GAP_MAX_TOKEN`, a separate clause), and `_truncated` only
    increments once the sample is full — so a truncated reading always still
    carries a non-empty sample and the clause is purely ADDITIVE. Silent, a run
    that absorbed 5,000 distinct tokens shipped "512 unlisted token(s)" with no
    sign the rest were dropped, on a customer-visible receipt field.

    Driven END TO END through the real accumulator rather than a hand-built
    dict, so it also pins that `_truncated` reaches the alarm at all: without
    the clause the assertion below has nothing to match (the pre-fix warning
    names the sample and stops).
    """
    overflow = 3
    acc = v2._VocabularyGapRunAccumulator()
    # Distinct, short, bucket-free tokens: no pair-read bucket and no oversize
    # cap, so the ONLY loss this reading records is the sample cap itself.
    acc.absorb(f"tok{i}" for i in range(v2._VOCABULARY_GAP_CAP + overflow))

    reading = acc.reading()
    assert reading["distinct_tokens"] == v2._VOCABULARY_GAP_CAP, reading["cap"]
    assert reading["truncated_observations"] == overflow, reading["truncated_observations"]
    assert reading["pair_read_residual"] == 0, reading["pair_read_residual"]
    assert reading["oversize_observations"] == 0, reading["oversize_observations"]

    warning = v2._vocabulary_gap_run_warning(reading)
    assert f"{overflow} further observation(s)" in warning, warning
    assert f"the {v2._VOCABULARY_GAP_CAP}-distinct-token sample cap" in warning, warning
    assert "not complete" in warning, warning
    # Additive, not displacing: the unlisted-sample clause the predicate fires
    # on is still there, so the reader gets BOTH facts from one alarm.
    assert "unlisted one-sided token(s)" in warning, warning


# ── round 7, P2 (1): the accumulator is SHARED across context-carrying threads ─
#
# `_VocabularyGapRunAccumulator`'s docstring used to justify having no lock with
# "run-confined by construction". Those halves disagree: `copy_context()` copies
# the MAPPING, not the values, so every context-carrying thread holds the SAME
# accumulator object — which is why
# `test_a_fold_on_a_context_carried_worker_lands_on_the_run_reading` has a second
# thread writing into it at all. `absorb` is then an unlocked read-modify-write on
# shared state, exactly what `_VocabularyGapCensus._lock` exists to prevent on the
# process census it mirrors. The arm below pins the property the lock is for, by
# MEASURING overlap rather than by asserting an internal attribute exists.


def test_an_absorb_cannot_interleave_with_another_absorb():
    """DETERMINISTIC pin of the property the missing lock broke: mutual exclusion.

    The docstring used to justify having no lock with "run-confined by
    construction" — but `copy_context()` copies the MAPPING, not the values, so
    every context-carrying thread holds the SAME accumulator object (that sharing
    is the whole point of the scope, and it is why
    `test_a_fold_on_a_context_carried_worker_lands_on_the_run_reading` has a
    second thread writing into it). Two `absorb` calls can therefore be in flight
    at once, and unlocked `+=` loses increments / over-admits past the cap.

    Measured by OVERLAP, not by inspecting internals: the first fold's token
    iterable parks between two tokens and waits for a second fold to finish. With
    the lock, the second fold cannot enter while the first is inside `absorb`, so
    the wait times out; without it, the second fold completes inside the first —
    which is exactly the interleaving that corrupts the counts.
    """
    import threading

    acc = v2._VocabularyGapRunAccumulator()
    parked = threading.Event()
    second_done = threading.Event()
    overlapped: list[bool] = []

    def _tokens():
        yield UNLISTED_MARKER
        # Park INSIDE absorb(), and tell the main thread we are here so the peer
        # cannot win the race by folding before we ever entered.
        parked.set()
        overlapped.append(second_done.wait(0.50))
        yield "also-unlisted"

    def _second_fold() -> None:
        acc.absorb([UNLISTED_MARKER])
        second_done.set()

    first = threading.Thread(target=acc.absorb, args=(_tokens(),), daemon=True)
    first.start()
    assert parked.wait(5), "the first fold never entered `absorb`"
    try:
        second = threading.Thread(target=_second_fold)
        second.start()
        second.join(5)
    finally:
        first.join(5)

    assert overlapped == [False], (
        "a second `absorb` ran to completion while the first was still inside "
        "`absorb` — the run accumulator has no mutual exclusion, so concurrent "
        "context-carrying folds lose increments and can over-admit past "
        f"_VOCABULARY_GAP_CAP (observed overlap={overlapped})")
    reading = acc.reading()
    assert reading["fold_decisions"] == 2, reading
    assert reading["tokens"] == {UNLISTED_MARKER: 2, "also-unlisted": 1}, reading


# ── round 7, P2 (2): the post-run attach sits INSIDE the module's suppress ─────

def test_a_raising_attach_never_turns_a_committed_run_into_a_raised_one(monkeypatch):
    """Observability may degrade to UNATTACHED — it may not decide the capture.

    This wrapper runs AFTER the run has committed its points, so an exception out
    of the attach would leave this function and `sdk` would report `ok: False` on
    a capture that SUCCEEDED. The two existing census writers are wrapped in
    `contextlib.suppress(Exception)` for exactly this reason; the new sibling path
    was not. A raising receipt-field builder stands in for any raise here.
    """
    def _boom(_reading):
        raise RuntimeError("receipt alarm builder blew up")

    monkeypatch.setattr(v2, "_vocabulary_gap_run_warning", _boom)

    def _run() -> dict:
        v2._record_vocabulary_gap([UNLISTED_MARKER])
        return {"stats": {}, "warnings": []}

    out = v2._run_scoped_vocabulary_gap(_run)()

    assert out == {"stats": {}, "warnings": []}, (
        "a raising attach must leave the receipt EXACTLY as the lane built it "
        f"(built before written) and must not escape; got {out!r}")
    # The loss is not silent: the process census still holds it.
    assert v2.vocabulary_gap_report().get(UNLISTED_MARKER, 0) >= 1


def test_the_reader_never_replaces_a_lanes_own_receipt_fields():
    """A container of the wrong type is the LANE's value, not this reader's to
    overwrite.

    `_attach` used to replace a non-`dict` `stats` (and a non-`list` `warnings`)
    with a fresh container, silently discarding whatever the lane had put there.
    The reader owns only its own key/entry: here `stats` is the lane's string, so
    it must survive untouched while the alarm still lands on `warnings` (the
    reading stays loud even where it cannot attach), and the control shows the
    reverse direction — a dict `stats` takes the reading while the lane's tuple
    `warnings` survives.
    """
    def _foreign_stats() -> dict:
        v2._record_vocabulary_gap([UNLISTED_MARKER])
        return {"stats": "the lane's own shape", "warnings": []}

    receipt = v2._run_scoped_vocabulary_gap(_foreign_stats)()
    assert receipt["stats"] == "the lane's own shape", (
        "the reader CLOBBERED a receipt field the lane owns; "
        f"stats={receipt['stats']!r}")
    assert len(_gap_warnings(receipt)) == 1, (
        "the reading cannot attach to a foreign `stats`, so the alarm must still "
        f"land on `warnings` rather than the loss going silent; {receipt!r}")

    def _foreign_warnings() -> dict:
        v2._record_vocabulary_gap([UNLISTED_MARKER])
        return {"stats": {}, "warnings": ("the lane's own tuple",)}

    receipt = v2._run_scoped_vocabulary_gap(_foreign_warnings)()
    assert receipt["warnings"] == ("the lane's own tuple",), (
        "the reader CLOBBERED a receipt field the lane owns; "
        f"warnings={receipt['warnings']!r}")
    assert receipt["stats"]["vocabulary_gap"]["tokens"].get(UNLISTED_MARKER) == 1, (
        "the reading must still land on a dict `stats`; "
        f"stats={receipt['stats']!r}")

# ── the wiring is LAZY, and laziness is a PIN, not prose (review note 2) ─────
# `tortoise/sdk.py` installs the M2 capture reader as a ONE-SHOT lazy entry: the
# first call pays the `extractor_v2` import, rebinds the class attribute, and
# every later call goes straight there. The reason is MEASURED, not stylistic —
# binding the real wrapper at module scope made `import tortoise.sdk` eagerly
# import `tortoise.extractor_v2` (`value_gate`/`vet_gate`/`near_dup_merge`/
# `failure_policy` with it), contradicting this repo's documented cold-start
# policy for that module (#10626 review P2).
#
# That fix rested on a sentence in a comment, and nothing redded if the import
# moved back out to module scope (review note 2 at #10790). A `sys.modules`
# assertion cannot pin it here: this file imports `extractor_v2` at its own top,
# and a shared pytest session may have imported it long before, so an
# "is it in sys.modules" reading is a measurement of the SESSION, not of `sdk`.
# The SOURCE is the durable carrier of the claim, so the pin reads the AST:
# `sdk.py` must carry no module-level `extractor_v2` import, and the import must
# live inside the nested lazy entry the wiring installs.

SDK_SOURCE = Path(__file__).resolve().parent.parent / "tortoise" / "sdk.py"

#: The wiring entry point in `sdk.py` — one module-level CALL, never a
#: module-level import.
WIRING_NAME = "_wire_vocabulary_gap_run_reader_10626"


def _mentions_extractor_v2(node: ast.AST) -> bool:
    return "extractor_v2" in ast.unparse(node)


def test_the_vocabulary_gap_reader_is_wired_LAZILY_at_module_scope():
    """The measured cold-start fix, pinned at the mechanism it rests on.

    Reds if the `extractor_v2` import is hoisted back to module scope — the
    regression that costs `import tortoise.sdk` the whole extractor stack — and
    independently if the module-level wiring CALL is dropped, which would leave
    the M2 lane's receipt unwrapped and the reader silently absent.
    """
    tree = ast.parse(SDK_SOURCE.read_text(encoding="utf-8"))

    eager = [
        ast.unparse(n) for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom)) and _mentions_extractor_v2(n)
    ]
    assert not eager, (
        "tortoise/sdk.py imports `extractor_v2` at MODULE scope, so "
        "`import tortoise.sdk` pays the extractor stack again — the measured "
        "#10626 cold-start regression. Keep the import inside the lazy entry: "
        f"{eager}")

    wiring = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == WIRING_NAME), None)
    assert wiring is not None, (
        f"`{WIRING_NAME}` is gone from tortoise/sdk.py — the M2 capture lane "
        "would then run UNWRAPPED, losing the #10626 reading on the one lane "
        "that DETACH-DELETEs a duplicate")

    nested = [
        ast.unparse(n) for n in ast.walk(wiring)
        if isinstance(n, (ast.Import, ast.ImportFrom)) and _mentions_extractor_v2(n)
    ]
    assert nested, (
        f"`{WIRING_NAME}` no longer imports `extractor_v2` inside its lazy "
        "entry — the bound name the wrapper rebinds to must come from a call-"
        "time import, or the module-scope parsimony is a fiction")

    installed = [
        n for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == WIRING_NAME
    ]
    assert installed, (
        f"tortoise/sdk.py defines `{WIRING_NAME}` but never CALLS it at module "
        "scope, so the wrapper is never installed")
