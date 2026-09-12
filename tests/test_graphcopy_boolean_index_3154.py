"""Hermetic regression for #3154 — ``GRAPH.COPY`` silently dropped the
``false`` posting entries of a boolean RANGE index, so ``n.is_operator =
false`` read ZERO rows on copies of a graph whose index set carried the
boolean index in its SDK-created position (the pre-#3154 shape), while
``NOT n.is_operator``, ``n.is_operator = true`` and ``typeof()`` all stayed
correct (the DATA was intact — the index was corrupt).

Why it matters: EP anchor selection (`dream.py`), the calibration gate
(`calibrate_summary`) and dedup all read the ``= false`` form, so a restored
graph came back with belief propagation, calibration and dedup silently
disabled — a fail-open state with no error anywhere.

Docker/server lane only: the corruption is a FalkorDB ``GRAPH.COPY``
behaviour and needs a real server. The file skips cleanly when no
non-embedded FalkorDB is reachable (mirrors tests/test_indexes.py's #522
non-embedded gate).
"""
from __future__ import annotations

import os
import uuid

import pytest

# ── Non-embedded (docker/server) gate ────────────────────────────────────
FALKORDB_AVAILABLE = False
_WORKING_URI: str | None = None


def _probe_falkordb(candidates: list[str | None]) -> tuple[bool, str | None]:
    """Probe candidate URIs for a live non-embedded FalkorDB."""
    _env_uri = os.environ.get("TORTOISE_DB_URI")
    for _uri in candidates:
        if not _uri:
            continue
        _proj = None
        try:
            from tortoise.projection import FalkorProjection
            _proj = FalkorProjection.from_uri(_uri)
            _proj.g.query("RETURN 1")
            return True, _uri
        except Exception:
            if _uri == _env_uri and _uri:
                break  # env-specified DB unreachable — don't fall through
            continue
        finally:
            if _proj is not None:
                try:  # noqa: SIM105
                    _proj.close()
                except Exception:
                    pass
    return False, None


FALKORDB_AVAILABLE, _WORKING_URI = _probe_falkordb([
    os.environ.get("TORTOISE_DB_URI"),
    "docker://:falkordb@localhost:6379/tortoise_test_graphcopy3154",
    "docker://:@localhost:16379/tortoise_test_graphcopy3154",
])

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="FalkorDB not available")

_N_FALSE = 10
_N_TRUE = 5


def _uri() -> str:
    return os.environ.get("TORTOISE_DB_URI") or (_WORKING_URI or "")


def _name(stem: str) -> str:
    return f"test_graphcopy3154_{stem}_{uuid.uuid4().hex[:8]}"


def _seed_points(g, *, n_false: int = _N_FALSE,
                 n_true: int = _N_TRUE) -> None:
    """Seed non-operator points carrying EP/calibration/dedup state, plus
    operator points whose ``is_operator = true`` lookups must keep working."""
    for i in range(n_false):
        g.query(
            "CREATE (:Point {id:$id, pointKind:'statement', is_operator:false, "
            "lastDreamedAt:1.0, ep_alpha:1.5, posterior_alpha:1.5, "
            "baseline_set:false, content_hash:$h})",
            params={"id": f"f{i}", "h": f"hash_f{i}"},
        )
    for i in range(n_true):
        g.query(
            "CREATE (:Point {id:$id, pointKind:'statement', is_operator:true, "
            "lastDreamedAt:2.0, ep_alpha:2.5, posterior_alpha:2.5, "
            "baseline_set:true, content_hash:$h})",
            params={"id": f"t{i}", "h": f"hash_t{i}"},
        )


def _predicate_counts(g) -> dict[str, int]:
    def _n(q: str) -> int:
        return int(g.query(q).result_set[0][0])
    return {
        "eq_false": _n("MATCH (n:Point) WHERE n.is_operator = false "
                       "RETURN count(n)"),
        "not": _n("MATCH (n:Point) WHERE NOT n.is_operator RETURN count(n)"),
        "eq_true": _n("MATCH (n:Point) WHERE n.is_operator = true "
                      "RETURN count(n)"),
    }


def _belief_state(g) -> dict[str, float]:
    """EP + calibration + dedup state as read through the `= false` predicate
    — the exact surface #3154 silently zeroed."""
    row = g.query(
        "MATCH (n:Point) WHERE n.is_operator = false "
        "RETURN sum(n.ep_alpha), "
        "sum(CASE WHEN n.baseline_set = false THEN 1 ELSE 0 END), "
        "count(DISTINCT n.content_hash)"
    ).result_set[0]
    return {
        "ep_alpha_sum": float(row[0] or 0.0),
        "uncalibrated": int(row[1] or 0),
        "dedup_hashes": int(row[2] or 0),
    }


def _assert_healthy(g, *, tag: str) -> None:
    counts = _predicate_counts(g)
    assert counts["eq_false"] == _N_FALSE, (
        f"[{tag}] #3154: `is_operator = false` dropped to {counts['eq_false']} "
        f"of {_N_FALSE} — a boolean index corrupts this predicate "
        f"(counts={counts})"
    )
    assert counts["not"] == _N_FALSE, f"[{tag}] NOT drifted: {counts}"
    assert counts["eq_true"] == _N_TRUE, f"[{tag}] = true drifted: {counts}"


@pytest.fixture
def db():
    """A FalkorDB connection handle + cleanup for every graph a test names."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(_uri())
    created: list[str] = []
    try:
        yield proj.db, created
    finally:
        for graph_name in created:
            try:  # noqa: SIM105
                proj.db.select_graph(graph_name).delete()
            except Exception:
                pass
        proj.close()


def test_graph_copy_preserves_boolean_false_entries(db):
    """#3154 regression: a direct GRAPH.COPY of a SDK-shaped graph must keep
    boolean `false` entries queryable and round-trip the belief/dedup state."""
    from tortoise.projection import FalkorProjection
    graph_db, created = db
    src_name, dst_name = _name("copy_src"), _name("copy_dst")
    created += [src_name, dst_name]

    proj = FalkorProjection.from_uri(_uri(), graph_name=src_name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    _seed_points(proj.g)

    _assert_healthy(proj.g, tag="source")
    src_belief = _belief_state(proj.g)
    assert src_belief == {"ep_alpha_sum": 15.0, "uncalibrated": 10,
                          "dedup_hashes": 10}

    proj.g.copy(dst_name)

    dst_g = graph_db.select_graph(dst_name)
    _assert_healthy(dst_g, tag="after GRAPH.COPY")
    assert _belief_state(dst_g) == src_belief, (
        "#3154: belief/calibration/dedup state did not round-trip the copy"
    )

    # Durability: the destination must stay healthy across a projection
    # reopen (the restore/import path calls _ensure_indexes right after the
    # swap, and a fresh boolean index on a copy destination is corrupt too
    # (drop+recreate does not heal it) — so the index must not come back).
    proj2 = FalkorProjection.from_uri(_uri(), graph_name=dst_name)
    proj2._ensure_indexes()
    _assert_healthy(proj2.g, tag="after _ensure_indexes reopen")
    assert _belief_state(proj2.g) == src_belief
    proj2.close()
    proj.close()


def test_restore_swap_preserves_boolean_false_entries(db):
    """The real restore path (dump → temp → verify → delete live →
    GRAPH.COPY temp→live) must not silently degrade the boolean predicate.

    The temp graph is built by ``restore_graph`` from the logical dump and so
    carries NO index schema — the swap therefore cannot carry a boolean index
    today. This test pins that end-to-end invariant; the compensating audit
    (``_audit_copied_boolean_indexes``) is exercised directly by the other
    tests in this file.
    """
    from tortoise.hosted_backup import _restore_into_temp_verify_swap, dump_graph
    from tortoise.projection import FalkorProjection

    graph_db, created = db
    src_name, live_name = _name("restore_src"), _name("restore_live")
    created += [src_name, live_name]

    proj = FalkorProjection.from_uri(_uri(), graph_name=src_name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    _seed_points(proj.g)
    src_belief = _belief_state(proj.g)
    payload = dump_graph(proj.g, src_name)

    _restore_into_temp_verify_swap(graph_db, payload, live_name=live_name)

    live_g = graph_db.select_graph(live_name)
    _assert_healthy(live_g, tag="after restore swap")
    assert _belief_state(live_g) == src_belief, (
        "#3154: restore swapped in a graph whose belief/calibration state "
        "reads as empty through the `= false` predicate"
    )

    # The post-swap index rebuild (hosted_api._rebuild_import_indexes) must
    # not reinstate a corrupt boolean index either.
    proj2 = FalkorProjection.from_uri(_uri(), graph_name=live_name)
    proj2._ensure_indexes()
    _assert_healthy(proj2.g, tag="after post-restore index rebuild")
    proj2.close()
    proj.close()


def test_audit_helper_repairs_a_corrupted_copy(db):
    """The copy-path compensation: `_audit_copied_boolean_indexes` detects the
    corruption a GRAPH.COPY introduced, drops the boolean index (presence-
    based), and reports the repair loudly — and reports a graph that carries
    no boolean index as clean."""
    from tortoise.hosted_backup import _audit_copied_boolean_indexes
    from tortoise.projection import FalkorProjection

    graph_db, created = db
    src_name, dst_name = _name("audit_src"), _name("audit_dst")
    created += [src_name, dst_name]

    proj = FalkorProjection.from_uri(_uri(), graph_name=src_name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    _seed_points(proj.g)
    # Reproduce the legacy shape that carries the hazard: a persisted
    # single-property boolean index (the SDK no longer creates one — #3154).
    proj.g.query("CREATE INDEX FOR (n:Point) ON (n.is_operator)")
    _assert_healthy(proj.g, tag="source with legacy boolean index")

    proj.g.copy(dst_name)
    dst_g = graph_db.select_graph(dst_name)
    corrupt = _predicate_counts(dst_g)
    if corrupt["eq_false"] == corrupt["not"]:
        pytest.skip(
            "this FalkorDB build no longer corrupts the boolean index on "
            "GRAPH.COPY — the compensation is not exercised here"
        )
    assert corrupt["eq_false"] == 0 and corrupt["not"] == _N_FALSE, corrupt

    repaired = _audit_copied_boolean_indexes(
        dst_g, graph_name=dst_name, stage="test copy")

    assert repaired is True, "the corrupt boolean index must be reported"
    _assert_healthy(dst_g, tag="after audit repair")
    assert _belief_state(dst_g) == _belief_state(proj.g)

    # A graph that carries no boolean index is the clean case — the second
    # call on the just-repaired copy finds nothing to drop.
    assert _audit_copied_boolean_indexes(
        dst_g, graph_name=dst_name, stage="test clean-idempotent",
    ) is False
    proj.close()


def test_audit_drops_boolean_index_when_counts_agree(db):
    """Presence-based repair (#3154 review P1): a copy whose counts happen to
    AGREE — the source had no `false` rows at copy time — still carries a
    poisoned index, and the NEXT non-operator write silently reads 0 through
    `is_operator = false`. The audit must drop by index presence, not by a
    count mismatch."""
    from tortoise.hosted_backup import _audit_copied_boolean_indexes
    from tortoise.projection import FalkorProjection

    graph_db, created = db
    src_name, dst_name = _name("zerofloor_src"), _name("zerofloor_dst")
    created += [src_name, dst_name]

    proj = FalkorProjection.from_uri(_uri(), graph_name=src_name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    for i in range(5):  # operators only → zero `false` rows on the source
        proj.g.query(
            "CREATE (:Point {id:$id, pointKind:'statement', is_operator:true, "
            "lastDreamedAt:2.0})",
            params={"id": f"t{i}"},
        )
    proj.g.query("CREATE INDEX FOR (n:Point) ON (n.is_operator)")
    proj.g.copy(dst_name)

    dst_g = graph_db.select_graph(dst_name)
    # Counts agree — a count-based check would leave the index in place.
    assert _predicate_counts(dst_g) == {"eq_false": 0, "not": 0, "eq_true": 5}

    assert _audit_copied_boolean_indexes(
        dst_g, graph_name=dst_name, stage="zero-false copy") is True

    # The poison is gone: a NEW non-operator write is visible through the
    # load-bearing `= false` predicate.
    dst_g.query(
        "CREATE (:Point {id:'newf', pointKind:'statement', is_operator:false, "
        "lastDreamedAt:3.0})")
    counts = _predicate_counts(dst_g)
    assert counts["eq_false"] == 1, (
        "#3154: a non-operator write after a copy read as 0 through "
        f"`is_operator = false` (counts={counts})"
    )
    assert counts["not"] == 1
    proj.close()


def test_ensure_indexes_never_indexes_is_operator(db):
    """The durable half of the fix: `_ensure_indexes` indexes lastDreamedAt
    (staleness ordering) and never `is_operator`, on any backend."""
    from tortoise.projection import FalkorProjection

    _graph_db, created = db
    name = _name("policy")
    created.append(name)

    proj = FalkorProjection.from_uri(_uri(), graph_name=name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj._ensure_indexes()
    _seed_points(proj.g)
    _assert_healthy(proj.g, tag="freshly indexed graph")

    rows = proj.g.query("CALL db.indexes()").result_set
    point_fields: set[str] = set()
    for row in rows:
        if row[0] == "Point":
            point_fields.update(str(f) for f in (row[1] or ()))
    assert "is_operator" not in point_fields, (
        f"#3154: _ensure_indexes must not index the boolean property on the "
        f"docker/server lane (GRAPH.COPY can corrupt it): "
        f"{sorted(point_fields)}"
    )
    assert "lastDreamedAt" in point_fields, (
        f"the staleness ordering index must survive: {sorted(point_fields)}"
    )
    proj.close()
