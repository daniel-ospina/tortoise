"""#8658 — the product ask-lane hit carries the point provenance its consumers key on.

``retrieval.package_evidence_pool`` (Slice A, #2683) keys its own-source collapse
on ``quote`` / ``source_turn_id`` and #2684's span surface keys on
``span_start`` / ``span_end``. The eval lane supplies all four
(``tools/longmem_eval/ingest.py::point_props_for_hits``); the **product** read
path supplied none, because ``SearchResult`` had no such field and the point
fetch never read the columns. So on real graphs:

  * ``_is_own_source`` returned ``False`` on its FIRST line (empty ``quote``), so
    Pass 2 (a distilled point's own source chunk collapses into ONE slot) never
    fired — the point and its own source chrome both kept window slots;
  * ``_same_fact``'s same-source-turn leg (``source_turn_id``) never fired;
  * the span could not be surfaced at all (the assembly cannot emit an address
    it is never given).

What this module pins, and what makes each test FAIL:

1. ``test_product_point_hit_carries_its_provenance`` — a Point written with
   quote/``source_turn_id``/span is returned by ``tortoise_fts_query`` carrying
   those four keys with the stored values. FAILS on the pre-fix read (the hit
   dict has no such keys — the measured shape in the issue).
2. ``test_unprovenanced_hit_stays_byte_identical`` — a Point written WITHOUT
   provenance emits NONE of the keys (absent, not empty). This is the additive
   present-only contract: widening the read must not hand every legacy row four
   empty fields.
3. ``test_the_first_source_turn_is_a_turn`` — the same-source-turn leg with the
   index ``0`` (the FIRST turn, 0-based). Hermetic, no graph. FAILS on
   ``str(h.get("source_turn_id") or "")``, which read turn 0 as "no turn id".
4. ``test_the_points_own_source_chunk_now_collapses_into_one_slot`` — the
   product consequence, on hits the product path itself produced: the point and
   its own raw source chunk package into ONE slot (with the chunk kept as a
   verbatim ref) instead of two. FAILS on the pre-fix read, where Pass 2 is inert
   and both hits stand alone.

Runs on the docker lane on a dedicated per-test graph (FTS backend required;
tier-2 embedded legs skip — mirrors ``tests/test_entity_key_expansion.py``) —
but the skip is **per-test, not module-wide**: test 3 is hermetic (pure
``package_evidence_pool`` over two dicts, no graph), and it is the ONLY pin for
``_pkg_turn_key`` — half of this issue. A module-level ``pytestmark`` would have
silently skipped that half in every URI-less leg (#9543 review, non-blocking
note: made a decision rather than left an accident; the shape mirrors
``tests/test_time_aware_run_2520.py``, hermetic tests unguarded, docker legs
marked).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise.retrieval import package_evidence_pool
from tortoise.sdk import TortoiseSDK


def _falkordb_available() -> bool:
    """Probe a live FalkorDB; reads TORTOISE_DB_URI at CALL time (#221)."""
    uri = os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")
    old = os.environ.get("TORTOISE_DB_URI")
    try:
        os.environ["TORTOISE_DB_URI"] = f"{uri}_probe"
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

# Per-test, NOT module-wide: the hermetic turn-0 pin must fail closed in the
# URI-less legs instead of skipping with the docker-lane tests (see module
# docstring).
requires_falkordb = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="requires TORTOISE_DB_URI (live FalkorDB FTS lane)")


def _uri() -> str:
    return os.environ.get(
        "TORTOISE_DB_URI",  # empty-uri-default-ok: #2815 skip declared
        _live_utils.docker_uri("tortoise_test_matrix")).rstrip("/")


def _fresh_uri() -> str:
    """A dedicated per-test graph on the docker server."""
    return f"{_uri()}_{uuid.uuid4().hex[:10]}"


SESSION = "sess-8658"
QUOTE = "the tea set cost 300 dollars"
SPAN_START = 0
SPAN_END = len(QUOTE)
SOURCE_TURN_ID = 2
STATEMENT_ID = "p8658statement"
CHUNK_ID = "p8658chunk"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT: this module is about the provenance columns on a
    hit, not about which leg produced it."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    _sdk = TortoiseSDK()
    try:
        yield _sdk
    finally:
        _sdk.close()


def _seed_provenanced(sdk: TortoiseSDK) -> list[dict]:
    """The point + its own raw source chunk, both written through the product
    SDK, both returned by the product read."""
    sdk.create_point(
        "statement", QUOTE,
        id=STATEMENT_ID, session_id=SESSION, status="draft",
        quote=QUOTE, source_turn_id=SOURCE_TURN_ID,
        span_start=SPAN_START, span_end=SPAN_END)
    # The windowed verbatim transcript that turn lives in — the chunk holds the
    # quote verbatim, which is the containment leg ``_is_own_source`` reads.
    sdk.create_point(
        "session-transcript",
        f"user: {QUOTE}\nthe buyer paid cash.",
        id=CHUNK_ID, session_id=SESSION, status="draft")
    hits = sdk.tortoise_fts_query("tea set cost", limit=10)
    by_id = {h["id"]: h for h in hits}
    assert STATEMENT_ID in by_id, "the statement must be retrievable"
    return [by_id[STATEMENT_ID], by_id[CHUNK_ID]]


@requires_falkordb
def test_product_point_hit_carries_its_provenance(sdk):
    """FAILS on the pre-fix read: the hit dict keys are the measured shape in
    #8658 — no ``quote`` / ``source_turn_id`` / ``span_start`` / ``span_end``."""
    point, _chunk = _seed_provenanced(sdk)
    assert point["quote"] == QUOTE
    assert point["source_turn_id"] == SOURCE_TURN_ID
    assert point["span_start"] == SPAN_START
    assert point["span_end"] == SPAN_END


@requires_falkordb
def test_unprovenanced_hit_stays_byte_identical(sdk):
    """A legacy point (no provenance written) gains NO keys — additive and
    present-only, so the widening cannot hand every existing row four empty
    fields (which would also make every hit look provenanced downstream)."""
    sdk.create_point("statement", "the garden gate needs oiling",
                     id="p8658bare", session_id=SESSION, status="draft")
    hits = sdk.tortoise_fts_query("garden gate oiling", limit=5)
    bare = next(h for h in hits if h["id"] == "p8658bare")
    for key in ("quote", "source_turn_id", "span_start", "span_end"):
        assert key not in bare, (
            f"{key} must be ABSENT on an unprovenanced hit, not empty")


def test_the_first_source_turn_is_a_turn():
    """Turn index ``0`` is a REAL turn and must collapse like any other.

    Hermetic (no graph): two duplicate extractions of the FIRST turn of a
    session — same ``source_turn_id`` (0, 0-based per ``commit_schema`` and
    ``extractor_v2``'s ``enumerate``), same verbatim ``quote``, deliberately
    different CONTENT so the content legs cannot fire and the same-source-turn
    leg is the only one left.

    FAILS on ``str(h.get("source_turn_id") or "")``: ``0 or ""`` is ``""``, so
    the leg reads "no turn id" and the duplicate stays a second slot."""
    quote = "we paid a deposit of five hundred dollars for the venue"
    a = {"id": "p-a", "content": "the deposit was paid up front",
         "point_kind": "statement", "sessionId": SESSION,
         "quote": quote, "source_turn_id": 0}
    b = {"id": "p-b", "content": "booking the venue needed money first",
         "point_kind": "statement", "sessionId": SESSION,
         "quote": quote, "source_turn_id": 0}
    _packaged, stats = package_evidence_pool([a, b])
    assert stats["packages"] == 1, (
        f"two extractions of turn 0 are the same fact, got "
        f"{stats['packages']} packages (stats={stats})")


@requires_falkordb
def test_the_points_own_source_chunk_now_collapses_into_one_slot(sdk):
    """The product consequence (#2683 Pass 2): with the provenance on the hit,
    the distilled point and its own source chunk become ONE package (the point
    as its anchor, the chunk kept as a verbatim ref).

    FAILS on the pre-fix read — ``_is_own_source`` returns False on its first
    line (empty quote), so the two hits are two ANCHORS, both consuming window
    slots, which is exactly the flooding Slice A exists to remove
    (measured pre-fix: ``packages: 2, verbatim_refs_kept: 0``)."""
    point, chunk = _seed_provenanced(sdk)
    packaged, stats = package_evidence_pool([point, chunk])
    assert stats["packages"] == 1, (
        f"the point and its own source chunk must share one package, got "
        f"{stats['packages']} (stats={stats})")
    assert stats["verbatim_refs_kept"] == 1
    # the anchor leads its own evidence (a flat anchor-then-refs rendering)
    assert [h["id"] for h in packaged] == [STATEMENT_ID, CHUNK_ID]
