"""#3301 — the four search legs must apply the canonical OBJECT vocabulary.

The defect (reproduced on the base commit): the four retrieval legs
(FTS, vector-index, vector-brute-force, structural) applied their
terminal-status predicate ONLY when the queried label was ``Point`` — so
``entity_type="object"`` returned EVERY Object, including
``superseded`` / ``deprecated`` / ``archived`` / ``retracted`` rows, while
``recall_state`` and the Object status vocabulary treat those as
non-current.

These are BEHAVIOUR tests: each leg asserts the ACTUAL returned set of
Object ids, never that a predicate was called. The expected sets are
hard-coded literals (never imported from the code under test), and the
positive control (a ``live`` Object) is in every set so a filter that
hides everything cannot pass vacuously.

The Object family is deliberately NOT the Point family: an Object has no
``outdated`` concept (no Object writer sets it), so the legs must NOT AND
the Point-only ``outdated=true`` conjunct — ``obj-flag`` (status ``live``,
``outdated=true``) pins that.
"""
from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.embeddings import EMBEDDING_DIM
from tortoise.search_engine import (
    MECHANISM_INDEX,
    MECHANISM_SCAN_FALLBACK,
    VECTOR_MECHANISM_KEY,
    reset_circuit_breakers,
    run_fts_query,
    run_structural_query,
    run_vector_query,
)

# The canonical OBJECT vocabulary, hard-coded (an expectation imported from
# the code under test is not an expectation).
CANONICAL_OBJECT_TERMINAL = frozenset(
    {"superseded", "deprecated", "archived", "retracted"})
#: the ids that MUST be VISIBLE on every default leg (status `live`), plus
#: the live-Object positive control carrying the Point-only `outdated` flag.
VISIBLE_IDS = frozenset({"obj-live", "obj-flag"})
#: the ids that MUST be HIDDEN on every default leg.
HIDDEN_IDS = frozenset(f"obj-{s}" for s in sorted(CANONICAL_OBJECT_TERMINAL))
ALL_IDS = VISIBLE_IDS | HIDDEN_IDS

_VEC = [1.0] + [0.0] * (EMBEDDING_DIM - 1)


# ── FalkorDB availability (the Object FTS/vector indexes need a server) ────

def _probe(candidates):
    env_uri = os.environ.get("TORTOISE_DB_URI")
    for uri in candidates:
        if not uri:
            continue
        proj = None
        try:
            from tortoise.projection import FalkorProjection
            proj = FalkorProjection.from_uri(uri)
            proj.g.query("RETURN 1")
            return True, uri
        except Exception:
            if uri == env_uri:
                break
            continue
        finally:
            if proj is not None:
                try:  # noqa: SIM105
                    proj.close()
                except Exception:
                    pass
    return False, None


FALKORDB_AVAILABLE, _WORKING_URI = _probe([
    os.environ.get("TORTOISE_DB_URI"),
    "docker://:falkordb@localhost:6379/tortoise_test_3301",
    "docker://:@localhost:16379/tortoise_test_3301",
])

_docker_only = pytest.mark.skipif(
    not FALKORDB_AVAILABLE,
    reason="live FalkorDB (with Object FTS/vector indexes) not available")


def _current_uri() -> str:
    return (os.environ.get("TORTOISE_DB_URI")
            or (_WORKING_URI or "docker://localhost:6379/tortoise_test_3301"))


@pytest.fixture()
def graph():
    """Fresh graph: Object FTS + vector indexes, one Object per status, plus
    the live ``outdated=true`` positive control."""
    reset_circuit_breakers()
    from tortoise.projection import FalkorProjection
    gname = f"test_object_search_vis_{uuid.uuid4().hex[:10]}"
    proj = FalkorProjection.from_uri(_current_uri(), graph_name=gname)
    g = proj.g
    g.query("MATCH (n) DETACH DELETE n")
    # Object FTS index (name) — production creates this for 'Object'.
    with contextlib.suppress(Exception):
        g.query("CALL db.idx.fulltext.createNodeIndex('Object', 'name')")
    # Object vector index — production creates one for Point only, so this is
    # the leg's index path; a failure degrades to brute-force, which is fine.
    with contextlib.suppress(Exception):
        g.query(
            "CREATE VECTOR INDEX FOR (o:Object) ON (o.embedding) "
            f"OPTIONS {{dimension: {EMBEDDING_DIM}, "
            "similarityFunction: 'cosine'}}")
    for st in [*sorted(CANONICAL_OBJECT_TERMINAL), "live"]:
        g.query(
            "CREATE (o:Object {id:$id, name:$name, status:$st, "
            "objectKind:'thing', embedding:vecf32($vec)})",
            params={"id": f"obj-{st}", "name": f"widget {st}",
                    "st": st, "vec": _VEC})
    # no Object writer sets `outdated`; a legacy/direct one must stay visible.
    g.query(
        "CREATE (o:Object {id:'obj-flag', name:'widget flagged', "
        "status:'live', outdated:true, objectKind:'thing', "
        "embedding:vecf32($vec)})", params={"vec": _VEC})
    yield g
    reset_circuit_breakers()
    proj.close()


def _ids(rows) -> set:
    return {str(r[0]) for r in rows}


# ── One test per leg: the ACTUAL returned set ─────────────────────────────

@_docker_only
def test_fts_leg_returns_only_visible_objects(graph):
    assert _ids(run_fts_query(
        graph, "widget", entity_type="object", limit=50)) == set(VISIBLE_IDS)


@_docker_only
def test_vector_index_leg_returns_only_visible_objects(graph):
    trace: list = []
    rows = run_vector_query(
        graph, _VEC, entity_type="object", is_embedded=False,
        limit=50, leg_trace=trace)
    assert _ids(rows) == set(VISIBLE_IDS)
    # `is_embedded=False` must ATTEMPT the index: both the index path and a
    # failed-attempt fallback mean the docker branch ran; the plain `scan`
    # mechanism would mean the embedded path ran instead (`scan_fallback` is
    # legitimate here — the fixture's index creation is best-effort).
    mechanisms = {e.get(VECTOR_MECHANISM_KEY) for e in trace}
    assert mechanisms <= {MECHANISM_INDEX, MECHANISM_SCAN_FALLBACK}, (
        f"is_embedded=False must attempt the vector index: {mechanisms}")


@_docker_only
def test_vector_bruteforce_leg_returns_only_visible_objects(graph):
    assert _ids(run_vector_query(
        graph, _VEC, entity_type="object", is_embedded=True,
        limit=50)) == set(VISIBLE_IDS)


@_docker_only
def test_structural_leg_returns_only_visible_objects(graph):
    assert _ids(run_structural_query(
        graph, "thing", entity_type="object", limit=50)) == set(VISIBLE_IDS)


@_docker_only
def test_no_leg_over_hides_the_live_positive_controls(graph):
    """The fix must not hide a legitimately visible Object: every leg still
    returns BOTH live controls (and the Point-only ``outdated`` flag does not
    hide ``obj-flag``)."""
    legs = {
        "fts": run_fts_query(graph, "widget", entity_type="object", limit=50),
        "vector_index": run_vector_query(
            graph, _VEC, entity_type="object", is_embedded=False, limit=50),
        "vector_bruteforce": run_vector_query(
            graph, _VEC, entity_type="object", is_embedded=True, limit=50),
        "structural": run_structural_query(
            graph, "thing", entity_type="object", limit=50),
    }
    for leg, rows in legs.items():
        assert _ids(rows) == set(VISIBLE_IDS), leg


@_docker_only
@pytest.mark.parametrize("leg", [
    "fts", "vector_index", "vector_bruteforce", "structural"])
def test_audit_opt_out_still_sees_the_hidden_objects(graph, leg):
    """``excluded_statuses=()`` (the audit/history opt-in) must still return
    the terminal Objects on EVERY leg — the exclusion is a per-leg default, not
    a data filter, and each leg short-circuits on its OWN guard (a single-leg
    test cannot catch one leg regressing)."""
    if leg == "fts":
        rows = run_fts_query(graph, "widget", entity_type="object", limit=50,
                             excluded_statuses=())
    elif leg == "vector_index":
        rows = run_vector_query(graph, _VEC, entity_type="object",
                                is_embedded=False, limit=50,
                                excluded_statuses=())
    elif leg == "vector_bruteforce":
        rows = run_vector_query(graph, _VEC, entity_type="object",
                                is_embedded=True, limit=50,
                                excluded_statuses=())
    else:
        rows = run_structural_query(graph, "thing", entity_type="object",
                                    limit=50, excluded_statuses=())
    assert _ids(rows) == set(ALL_IDS), leg


# ── The extractor PRIOR leg opts back into terminal Objects ───────────────

def test_extractor_prior_leg_opts_into_terminal_objects():
    """#3301: the S3 object/subject prior leg must pass include_terminal=True
    (commit_ops.apply_supersessions' documented entity-terminal idempotency
    branch is reachable through it), while the Point leg keeps the default —
    terminal Points stay out of capture priors."""
    from tortoise.extractor_v2 import _fts_rows

    class _RecordingSDK:
        def __init__(self):
            self.calls = []

        def tortoise_fts_query(self, query, *, entity_type, limit=3,
                               include_terminal=False):
            self.calls.append((entity_type, include_terminal))
            return []

    sdk = _RecordingSDK()
    _fts_rows(sdk, "object", "q", limit=3)
    _fts_rows(sdk, "subject", "q", limit=3)
    _fts_rows(sdk, "point", "q", limit=3)
    assert sdk.calls == [("object", True), ("subject", True),
                         ("point", False)]


# ── The vocabulary has ONE declaration ───────────────────────────────────

def test_object_vocabulary_is_declared_once_and_is_the_canonical_set():
    from tortoise import commit_ops
    from tortoise.search_engine import _status_vocab_for

    assert commit_ops.OBJECT_TERMINAL_STATUSES == CANONICAL_OBJECT_TERMINAL
    # the recall alias is the SAME object, never a second literal
    assert (commit_ops._RECALL_OBJECT_EXCLUDED_STATUS
            is commit_ops.OBJECT_TERMINAL_STATUSES)
    # the family rule lives in ONE place and yields this canonical set for
    # Objects, with the Point-only `outdated` flag OFF.
    vocab, include_outdated_flag = _status_vocab_for("Object")
    assert vocab is commit_ops.OBJECT_TERMINAL_STATUSES
    assert include_outdated_flag is False


def test_points_keep_the_point_vocabulary_and_flag():
    """The Object lane must not change the Point lane: Points still get the
    canonical POINT vocabulary AND the legacy `outdated` flag conjunct."""
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES
    from tortoise.search_engine import _status_vocab_for

    vocab, include_outdated_flag = _status_vocab_for("Point")
    assert vocab is TERMINAL_EXCLUDED_STATUSES
    assert include_outdated_flag is True
