"""#8486 finding 3 — pins for `tools/longmem_eval/ingest_profile.py`.

The profiler is a DIAGNOSTIC, so its only correctness claims are about its
CALIBRATION: the synthetic haystack must be the real split's shape, or the
per-question projection it prints describes a workload nobody runs. These
tests are import-light (no lane, no DB, no embedder) and pin exactly that.

Run::

    uv run pytest tests/test_longmem_ingest_profile.py -q
"""
from __future__ import annotations

import math
import os

from tools.longmem_eval import ingest_profile as prof
from tools.longmem_eval.ingest import DEFAULT_CHUNK_TURNS, _session_chunks


def test_the_headline_shape_matches_the_measured_dataset_scale():
    """⛔ The calibration claim, pinned.

    `haystack_overlap.CENSUS` measured the 500-Q S split at 371,095 encode
    units = 742 points/question. The profiler's headline shape (25 sessions ×
    20 turns at ``chunk_turns=2``) must produce that many points, or the
    ``s/question`` projection it prints is about a different workload.
    """
    sessions, turns = prof.SCALING_SHAPES[-1]
    q = prof.synthetic_question("cal", sessions, turns,
                                prof.HEADLINE_TURN_CHARS)
    n_turns = sum(len(s) for s in q["haystack_sessions"])
    n_chunks = sum(len(list(_session_chunks(s, DEFAULT_CHUNK_TURNS)))
                   for s in q["haystack_sessions"])
    points = n_turns + n_chunks
    assert n_turns == 500
    assert points == 742 + 8  # 750 — within 1.1% of the measured 742
    assert abs(points - prof.DATASET_POINTS_PER_QUESTION) / \
        prof.DATASET_POINTS_PER_QUESTION < 0.02


def test_the_chunk_leg_is_one_per_chunk_turns_window():
    """The profile's cost is proportional to (turns + chunks); if the chunk
    leg silently disappeared (a `chunk_turns` default change), the projection
    would under-report by a third."""
    sessions, turns = 10, 20
    q = prof.synthetic_question("chunks", sessions, turns, 200)
    expected = sessions * math.ceil(turns / DEFAULT_CHUNK_TURNS)
    got = sum(len(list(_session_chunks(s, DEFAULT_CHUNK_TURNS)))
              for s in q["haystack_sessions"])
    assert got == expected == 100


def test_the_named_db_wins_over_an_ambient_uri(monkeypatch):
    """``--db`` must WIN over an exported ``TORTOISE_DB_URI`` — pinned.

    The round-2 review recorded this as a P3: ``_sdk`` was changed from
    ``os.environ.setdefault`` to an assignment so an ambient variable can no
    longer redirect the profiler's five throwaway graphs to a server the
    operator did not name, but nothing reddened if it were reverted.

    ``tools/test_lane.py`` exports exactly that variable on a smoke lane, so
    ``setdefault`` would silently measure a DIFFERENT server while
    ``report["db"]`` still said True. The SDK is stubbed: this pins the env
    contract, not a connection.

    Non-vacuity: with ``setdefault`` restored, the stub observes the ambient
    URI and the first assertion reds.
    """
    seen: dict[str, str | None] = {}

    class _StubSDK:
        def __init__(self, *_a, **_kw):
            seen["uri"] = os.environ.get("TORTOISE_DB_URI")

    monkeypatch.setattr(prof, "TortoiseSDK", _StubSDK)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@ambient:6379/other")

    _, cleanup = prof._sdk(db_uri="docker://:pw@named:6379/want",
                           namespace="profile-test")
    assert seen["uri"] == "docker://:pw@named:6379/want", (
        "an ambient TORTOISE_DB_URI must not outrank the --db it was given")
    cleanup()
    assert os.environ["TORTOISE_DB_URI"] == "docker://:pw@ambient:6379/other", (
        "cleanup must restore the ambient value it borrowed the variable from")


def test_the_evidence_turn_is_marked_once_per_question():
    """Turn/evidence denominators the ingest reports must be derivable from
    the fixture, so a shape change cannot quietly alter what it measures."""
    q = prof.synthetic_question("evi", 3, 4, 200)
    marked = sum(1 for s in q["haystack_sessions"]
                 for t in s if t["has_answer"])
    assert marked == 1


def test_every_documented_turn_length_is_built():
    """The two measured turn lengths (900 = the real ~230 tokens/turn, 200 =
    the short shape that makes the embed term's length sensitivity visible)
    must actually differ in what they build."""
    short = prof.synthetic_question("s", 1, 2, prof.SHORT_TURN_CHARS)
    long = prof.synthetic_question("l", 1, 2, prof.HEADLINE_TURN_CHARS)
    assert len(short["haystack_sessions"][0][0]["content"]) == \
        prof.SHORT_TURN_CHARS
    assert len(long["haystack_sessions"][0][0]["content"]) == \
        prof.HEADLINE_TURN_CHARS
