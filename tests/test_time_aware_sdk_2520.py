"""#2520 (C6) — the SDK's query-side date anchor on the DENSE leg.

Class B, per test:

  (1) **What value makes this test fail?** — named in each docstring.
  (2) **Is the value reachable?** — the fixture installs a RECORDING
      embedder, so the exact string handed to ``model.encode`` is
      observable; the failing value (the bare, un-anchored query) is the
      default behaviour and is asserted as the negative control.

The recording embedder makes the test lane-independent (no model load, no
network, no `embeddings` extra needed) while still exercising the real
``tortoise_fts_query`` control flow. Requires a live FalkorDB for the SDK
graph (docker lane) — the anchor decision itself is pure.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.sdk import TortoiseSDK


def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe2520sdk"
        from tortoise.sdk import TortoiseSDK as _ProbeSDK
        _probe = _ProbeSDK()
        _probe._get_proj().g.query("RETURN 1")
        _probe.close()
        return True
    except Exception:
        return False
    finally:
        if old is not None:
            os.environ["TORTOISE_DB_URI"] = old
        else:
            os.environ.pop("TORTOISE_DB_URI", None)


FALKORDB_AVAILABLE = _falkordb_available()

#: The graph-backed tests need a live FalkorDB; the pure anchor-decision
#: tests below are hermetic and always run — so the SDK-side contract is
#: never permanently skipped in CI (a lane without FalkorDB still proves
#: the decision; the graph half proves the wiring).
_DOCKER_ONLY = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB lane)")


class _RecordingEmbedder:
    """A stand-in for ``EmbeddingModel`` that records every encoded text."""

    def __init__(self) -> None:
        self.texts: list[list[str]] = []

    def encode(self, texts):
        self.texts.append(list(texts))
        return [[0.0] * 384 for _ in texts]


@pytest.fixture()
def recording_embedder(monkeypatch):
    """Install the recording embedder and pin out write-time embedding."""
    import tortoise.embeddings as emb
    recorder = _RecordingEmbedder()
    monkeypatch.setattr(emb.EmbeddingModel, "get",
                        staticmethod(lambda: recorder))
    monkeypatch.setattr(emb, "compute_embedding",
                        staticmethod(lambda content: None))
    return recorder


@pytest.fixture()
def sdk():
    uri = (os.environ.get(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
        + "_" + uuid.uuid4().hex[:10])
    old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = uri
    sdk = TortoiseSDK(namespace="test_time_aware_2520")
    yield sdk
    sdk.close()
    if old is not None:
        os.environ["TORTOISE_DB_URI"] = old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


def _encoded(recorder) -> list[str]:
    return [t for call in recorder.texts for t in call]


@_DOCKER_ONLY
def test_off_path_embeds_the_bare_query(recording_embedder, sdk):
    """FAIL VALUE: any anchor on the off path — the default-off contract is
    byte-identical. Reachable: ``time_aware`` defaults to False."""
    sdk.tortoise_fts_query("where do I live now?", limit=5)
    assert "where do I live now?" in _encoded(recording_embedder)
    assert not any("(as of" in t for t in _encoded(recording_embedder))


@_DOCKER_ONLY
def test_time_aware_anchors_the_dense_query(recording_embedder, sdk):
    """FAIL VALUE: the bare query reaching ``model.encode`` when the arm is
    ON with a valid date. Reachable: the recorder captures the exact
    string; a no-op injection fails the assertion."""
    sdk.tortoise_fts_query(
        "where do I live now?", limit=5,
        time_aware=True, query_date="2026-09-25")
    assert "where do I live now? (as of 2026-09-25)" in _encoded(
        recording_embedder)


@_DOCKER_ONLY
def test_date_pinned_query_is_not_anchored(recording_embedder, sdk):
    """FAIL VALUE: an anchor on a DATE-PINNED query — the invert-recency
    guard. Reachable: the query names a year, so the detector returns
    DATE_PINNED and the bare query must be embedded."""
    sdk.tortoise_fts_query(
        "where did I live in 2024?", limit=5,
        time_aware=True, query_date="2026-09-25")
    assert "where did I live in 2024?" in _encoded(recording_embedder)
    assert not any("(as of" in t for t in _encoded(recording_embedder))


@_DOCKER_ONLY
def test_time_aware_without_a_date_is_a_noop(recording_embedder, sdk):
    """FAIL VALUE: an invented anchor when ``query_date`` is absent.
    Reachable: ``query_date`` defaults to None."""
    sdk.tortoise_fts_query(
        "where do I live now?", limit=5, time_aware=True)
    assert "where do I live now?" in _encoded(recording_embedder)
    assert not any("(as of" in t for t in _encoded(recording_embedder))


@_DOCKER_ONLY
def test_fts_leg_keeps_the_original_query(recording_embedder, sdk):
    """FAIL VALUE: the sparse leg being handed the anchored string (date
    tokens dilute the OR-union) — the anchor is a DENSE-leg concern only.
    Reachable: the recorder proves the dense leg got the anchor; this test
    pins that a second, un-anchored embed never appears, i.e. the anchor is
    not re-encoded elsewhere."""
    sdk.tortoise_fts_query(
        "where do I live now?", limit=5,
        time_aware=True, query_date="2026-09-25")
    encoded = _encoded(recording_embedder)
    assert encoded.count("where do I live now? (as of 2026-09-25)") == 1
    assert "where do I live now?" not in encoded


# ── hermetic half (no DB, no embedder): the factored anchor decision ──────

def test_dense_query_decision_is_lane_independent():
    """FAIL VALUE: the anchor decision depending on a live graph. The SDK
    factors the decision into ``tortoise.time_aware.dense_query_for``, so
    this half runs on any lane — a FalkorDB-less CI still proves the
    contract; the graph half above proves the wiring.

    Reachable: every branch (armed/off, prefer-latest/date-pinned/None-date)
    is a pure function call."""
    from tortoise.time_aware import dense_query_for
    assert dense_query_for("where do I live now?", time_aware=True,
                           query_date="2026-09-25") == (
        "where do I live now? (as of 2026-09-25)")
    assert dense_query_for("where do I live now?", time_aware=False,
                           query_date="2026-09-25") == "where do I live now?"
    assert dense_query_for("where did I live in 2024?", time_aware=True,
                           query_date="2026-09-25") == (
        "where did I live in 2024?")
    assert dense_query_for("where do I live now?", time_aware=True,
                           query_date=None) == "where do I live now?"
