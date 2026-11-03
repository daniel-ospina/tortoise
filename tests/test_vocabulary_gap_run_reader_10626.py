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

* `result["stats"]["vocabulary_gap"]` — the reading, ALWAYS present, the shape
  `vocabulary_gap_snapshot()` reports plus `"scope": "run"`. It rides `stats`
  because the SDK's capture fold (`sdk._capture_stats_with_step_witness`) copies
  `out["stats"]` wholesale, so the reading reaches the product receipt
  (`capture_session`'s `stats`) with no second seam — the #2335 WI-1a lesson
  `error_census` taught.
* `result["warnings"]` — ONE entry naming the absorbed tokens, only when the
  reading recorded a loss. This is the run's alarm, and unlike the process log
  line it cannot be spent by an earlier run.

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
accumulator is thread-scoped, so a receipt names only its own run's tokens.

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

from tests._live_utils import _skip_unless_live_uri  # noqa: E402, RUF100
from tortoise import extractor_v2 as v2  # noqa: E402, RUF100

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
    can answer this run's S3 search."""
    _skip_unless_live_uri()
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK(graph_name=f"test_10626_{uuid.uuid4().hex[:10]}")


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


def test_a_run_with_no_fold_reports_an_empty_reading_and_no_alarm(sdk):
    """The control arm: this is what makes the reader a READER.

    A run whose boundary compared nothing reports an empty reading and emits no
    gap warning, so `with a gap` and `without` are distinguishable from the
    receipt alone."""
    out = _run(sdk, CLEAN_CLAIM, "gap-clean")

    reading = out["stats"]["vocabulary_gap"]
    assert reading["tokens"] == {}, reading
    assert reading["distinct_tokens"] == 0, reading
    assert reading["fold_decisions"] == 0, reading
    assert _gap_warnings(out) == []


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
    assert second["stats"]["vocabulary_gap"]["tokens"] == {}, (
        "a run reported a token it never absorbed — the reading is a process "
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
    conversation = [
        {"role": "user", "content": PRIOR_CLAIM},
        {"role": "assistant", "content": "noted"},
        {"role": "user", "content": GAP_CLAIM},
    ]

    out = sdk.capture_session(conversation, session_id="gap-m2")

    reading = out["stats"]["vocabulary_gap"]
    assert reading["scope"] == "run"
    assert reading["tokens"].get(UNLISTED_MARKER, 0) >= 1, (
        "the M2 lane folded a restatement across an unlisted marker and "
        f"DETACH-DELETEd the duplicate; its receipt must name the token; "
        f"reading={reading}")
    gap = _gap_warnings(out)
    assert len(gap) == 1 and repr(UNLISTED_MARKER) in gap[0], gap


def test_the_reader_does_not_change_the_fold_decision():
    """The reader reports; it must not answer #5329's open question.

    Failing closed on a shape-candidate marker is an OWNER decision (§16.4's two
    deliberately FAIL-OPEN classes make it a reopen), so the boundary must still
    PERMIT this fold while the census names what the fold absorbed. If a future
    change makes this `False`, it decided #5329 without the owner."""
    assert v2.fold_allowed(GAP_CLAIM, PRIOR_CLAIM) is True
    assert v2.vocabulary_gap_report().get(UNLISTED_MARKER, 0) >= 1
