"""#11642 — the Postgres dense leg, and the erasure residual scan.

Two subjects, and they are one file because they are one DECISION's two halves:
the move puts an ANN leg in Postgres (`tortoise/record_vector.py`) and therefore
puts a copy of every vector in two stores, which is why the erasure attestation
had to grow a second store (`tortoise/embedding_residual_scan.py`).

⛔ NOTHING HERE NEEDS A LIVE POSTGRES. That is deliberate rather than convenient:
the repo's record legs that DO need one (`tests/test_record_serve_live.py`) skip
in every CI leg today, so a pin written only there would be green because it
never ran. What is pinnable without a database is the part that has been wrong
before: which labels are served, what an unserved label does, what a failure
does, when the leg is armed, and the difference between "the store holds
nothing" and "nobody looked".
"""
from __future__ import annotations

import pytest

from tortoise import record_vector
from tortoise.embedding_residual_scan import (
    CLEAN,
    EXIT_CLEAN,
    EXIT_RESIDUE,
    EXIT_UNAVAILABLE,
    RESIDUE,
    UNAVAILABLE,
    ResidualScan,
)


# ── the served set ──────────────────────────────────────────────────────────

def test_only_the_labels_whose_vector_the_record_carries_are_served():
    """`object`/`subject` are NOT served, and that is the stated decision.

    Their vector is derived inside the graph fold from `name` (#7845) and rides
    no journal record, so `public.object`/`public.subject` have no column to
    read — see `20261109000001_record_embedding_vector.sql`'s closing block for
    both rejected candidates. A future lane that "adds" them here without a
    column would make the leg query a column that does not exist.
    """
    assert set(record_vector.LABEL_SERVED) == {
        "point", "operator", "source", "document", "event"}
    assert "object" not in record_vector.LABEL_SERVED
    assert "subject" not in record_vector.LABEL_SERVED


def test_every_served_label_is_a_real_entity_type():
    """The map must not invent a vocabulary the runner would reject.

    `run_vector_query` validates `entity_type` against
    `tortoise.security.VALID_ENTITY_TYPES` before it does anything, so a key
    here that is not in that set can never be reached — a dead entry that reads
    as coverage.
    """
    from tortoise.security import VALID_ENTITY_TYPES

    assert set(record_vector.LABEL_SERVED) <= set(VALID_ENTITY_TYPES)


def test_the_id_column_matches_the_runners_id_field():
    """#448's three-way `id_field` derivation, restated in SQL.

    `source`/`document` answer the canonical key `url`, `event` answers
    `event_id`, everything else `id`. A divergence here would return a node key
    the caller cannot join back to the graph — a silent empty dense leg.
    """
    assert record_vector.LABEL_SERVED["source"][1] == "url"
    assert record_vector.LABEL_SERVED["document"][1] == "url"
    assert record_vector.LABEL_SERVED["event"][1] == "event_id"
    assert record_vector.LABEL_SERVED["point"][1] == "id"
    assert record_vector.LABEL_SERVED["operator"][1] == "id"


def test_operator_and_document_are_the_two_discriminated_rows():
    """One table can hold two served entities; the filter is what separates them.

    Operators are Points (#172) and a Document IS a Source (D10, §4.4), so an
    unfiltered read of either table would fuse two entity types into one leg.
    `point`/`source` must therefore carry NO filter while their twins do.
    """
    assert record_vector.LABEL_SERVED["point"][2] == ""
    assert record_vector.LABEL_SERVED["source"][2] == "document_kind IS NULL"
    assert "is_operator" in record_vector.LABEL_SERVED["operator"][2]
    assert "IS NOT NULL" in record_vector.LABEL_SERVED["document"][2]


def test_the_scan_cannot_return_a_short_answer_silently():
    """The measured pgvector hazard, pinned as behaviour.

    On pgvector 0.8.7 with the DEFAULT `hnsw.iterative_scan = off`, a filtered
    ANN over a 20,000-row corpus returned **0 rows** where 152 matched
    (`LIMIT 500`); `strict_order` returned 150. The leg therefore sets the scan
    explicitly on every read — and this constant is what the SQL uses, so a
    "cleanup" that drops the SET re-opens the hole.
    """
    assert record_vector.HNSW_ITERATIVE_SCAN == "strict_order"


# ── arming ─────────────────────────────────────────────────────────────────

def test_no_tenant_means_no_searcher(monkeypatch):
    """The scope comes from the ONE declaration both credential lanes install.

    An unarmed request must produce no searcher at all — not a searcher that
    guesses. RLS renders an unscoped read as ZERO ROWS, so a guess would present
    a total loss of the dense leg as "no matches".
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.setattr("tortoise.record_writer.record_tenant", lambda: None,
                        raising=False)
    assert record_vector.searcher() is None


def test_a_half_tenant_is_not_a_tenant():
    """`(None, g)`, `(o, "")`, `"og"`, `(o,)` are all refused, never half-used.

    A mis-scoped read under RLS returns ZERO rows with no error, so the one
    input that produces a silent empty dense leg is a tenant the record cannot
    be scoped to — it must be refused HERE, before any SQL runs.
    """
    import tortoise.record_writer as rw

    original = rw.record_tenant
    try:
        for bad in ((None, "g"), ("", "g"), ("o", None), ("o", ""), "og", ("o",),
                    ("o", "g", "extra"), 3, None):
            rw.record_tenant = lambda b=bad: b
            assert record_vector._resolve_scope() is None, bad
        rw.record_tenant = lambda: ("o", "g")
        resolved = record_vector._resolve_scope()
        assert resolved is not None
        assert (resolved.org_id, resolved.graph_id) == ("o", "g")
    finally:
        rw.record_tenant = original


def test_an_unarmed_box_reports_unarmed_rather_than_raising():
    """`armed()` is a fact, not an exception — the SDK calls it per read."""
    assert record_vector.armed("") is False


# ── the residual scan's verdict algebra ────────────────────────────────────

def test_clean_requires_BOTH_stores_to_have_been_read():
    """The whole point: a one-store scan is never CLEAN.

    `None` is "nobody looked", never zero — the distinction the tool exists to
    keep, and the one a `count()`-shaped API loses by default.
    """
    partial = ResidualScan(org_id="o", graph_id="g", record_vectors=0,
                           graph_vectors=None,
                           refusals=["graph: no handle supplied"])
    assert partial.verdict == UNAVAILABLE
    assert partial.exit_code == EXIT_UNAVAILABLE

    both = ResidualScan(org_id="o", graph_id="g", record_vectors=0,
                        graph_vectors=0)
    assert both.verdict == CLEAN
    assert both.exit_code == EXIT_CLEAN


@pytest.mark.parametrize("record,graph", [(3, 0), (0, 3), (2, 2)])
def test_residue_in_EITHER_store_is_a_finding(record, graph):
    """Erased means erased in both. A leftover in one store is not "mostly fine".

    This is the assertion that would have caught the reason the scan exists: a
    delete that reached the graph and not the record (or the reverse) leaves a
    vector readable by exactly the leg the customer cannot see.
    """
    outcome = ResidualScan(org_id="o", graph_id="g", record_vectors=record,
                           graph_vectors=graph)
    assert outcome.verdict == RESIDUE
    assert outcome.exit_code == EXIT_RESIDUE


def test_a_refusal_outranks_a_count_even_a_zero_one():
    """A store that could not be read may not contribute a clean zero.

    A record that refuses while the graph reads 0 must NOT be RESIDUE and must
    not be CLEAN — it is no verdict, because the unread store is exactly where
    the residue would be.
    """
    outcome = ResidualScan(org_id="o", graph_id="g", record_vectors=None,
                           graph_vectors=0,
                           refusals=["record: could not be opened"])
    assert outcome.verdict == UNAVAILABLE


def test_the_outcome_names_the_scope_it_measured():
    """A CLEAN about the wrong scope is a true statement about nothing asked.

    The dict carries the scope and what "scope" MEANS in each store, so a reader
    quoting a CLEAN cannot mistake a graph-name scope for an org-wide one.
    """
    payload = ResidualScan(org_id="o", graph_id="g", record_vectors=0,
                           graph_vectors=0).as_dict()
    assert payload["org_id"] == "o" and payload["graph_id"] == "g"
    assert "org_id+graph_id" in payload["scope"]
    assert "graph name" in payload["scope"]


def test_the_record_half_counts_both_the_payload_and_the_indexed_projection():
    """A residual can survive in `embedding` OR in `embedding_vec`.

    The indexed projection is GENERATED from the payload, so today they cannot
    disagree — but an erasure that nulls one and not the other is precisely the
    shape a `delete`-based attestation misses, and counting only the payload
    would report it clean.
    """
    import inspect

    src = inspect.getsource(
        __import__("tortoise.embedding_residual_scan", fromlist=["x"])._count_record)
    assert "embedding IS NOT NULL" in src
    assert "embedding_vec IS NOT NULL" in src
