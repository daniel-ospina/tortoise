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
