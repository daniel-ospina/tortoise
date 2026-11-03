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
