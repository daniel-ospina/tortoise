"""#13447 — a `record-restore` LANDS the vectors its rows hold (lived, not viewed).

WHAT THIS PINS. The #13220 migration header states the split plainly: the
`embedding` column closed the **INDEXABILITY** half and **not** the **RESTORE**
half — `cache_projection._props_from_row` dropped `embedding` by name for
`object`/`subject` (the schema's `props`-tail refusal), so
`python -m tortoise record-restore` materialised `:Object`/`:Subject` nodes
**SILENTLY VECTORLESS** and nothing on that path would ever put them back. That is
the failure the 2026-11-10 KEEP ruling existed to prevent, one release later.

The DB-free half of this proof lives in `tests/test_13447_derived_embedding_seam.py`
(it asserts the `vecf32($embedding)` clause on the plan). ⛔ THIS file is the other
half and it is not redundant: a plan that CONTAINS a clause proves the plan, not
that the engine stored the vector — the `vecf32` write is the seam `#4524` measured
being silently discarded on the embedded engine. So the vector is read back off the
node here.

TARGETING. The graph must be named `test_*` (`FalkorProjection`'s L2 guard refuses
a bulk wipe on anything else, #2944/C2); it skips VISIBLY when no live URI is
configured (`tests._live_utils._skip_unless_live_uri`, the #3339 reason) rather
than passing vacuously.
"""
from __future__ import annotations

import pytest

from tests._live_utils import _skip_unless_live_uri
from tortoise import cache_projection as cp
from tortoise.embeddings import EMBEDDING_DIM
from tortoise.projection import FalkorProjection

GRAPH_NAME = "test_13447_restore_vectors"

#: A 384-wide vector, the width `public.record_embedding_vector` accepts. Distinct
#: from the derived one below, so the test can tell "the record's value came back"
#: from "something re-derived a vector".
_STORED = [0.25] * EMBEDDING_DIM
_DERIVED = [0.125] * EMBEDDING_DIM


@pytest.fixture()
def encoder(monkeypatch):
    """A deterministic encoder over the ONE seam, and a count of its MODEL calls."""
    calls: list[str] = []
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda text, **kw: calls.append(text) or list(_DERIVED))
    import tortoise.embeddings as emb
    monkeypatch.setattr(emb.EmbeddingModel, "await_write_ready_reason",
                        classmethod(lambda cls, budget: None))
    return calls


def _projection() -> FalkorProjection:
    _skip_unless_live_uri()
    import os

    from tortoise.residency import clear_residency

    proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                     graph_name=GRAPH_NAME)
    proj._wipe_all_nodes(confirm_destructive=True, operation="test-setup")
    clear_residency(proj.g)
    return proj


def _view():
    """Two `public.object` rows: one carrying the record's vector, one NULL.

    Asserted on the rows the projection actually reads, so the fixture cannot
    drift from the schema the test is about.
    """
    return cp.RecordView(nodes=(
        cp.node_from_row("Object", {"id": "o-stored", "name": "Acme Corp",
                                    "object_kind": "org",
                                    "embedding": list(_STORED), "props": {}}),
        cp.node_from_row("Object", {"id": "o-null", "name": "Beta Ltd",
                                    "object_kind": "org",
                                    "embedding": None, "props": {}}),
        cp.node_from_row("Subject", {"id": "s-stored", "name": "Ada Lovelace",
                                     "subject_kind": "person",
                                     "embedding": list(_STORED), "props": {}}),
    ), relations=())


def _props(proj: FalkorProjection, label: str, key: str) -> dict:
    rows = proj.query(
        f"MATCH (n:{label} {{id: $k}}) RETURN properties(n)", k=key).result_set
    assert rows, f"no :{label} {{id: {key!r}}} in the cache"
    return dict(rows[0][0])


def test_the_restore_lands_the_vector_the_row_holds(encoder):
    """#13447's indicator (3), measured on the ENGINE.

    ⛔ The two rows answer two different questions and both matter:

    * `o-stored` / `s-stored` carry a vector in the RECORD — the restore must PUT
      IT BACK, and it must be the RECORD's value (`_STORED`, not the encoder's
      `_DERIVED`), because re-deriving would make the cache a second opinion about
      a value the record already holds. No model call may be paid for them.
    * `o-null` carries NONE — and there the restore DERIVES, through the live seam,
      because journal replay re-derives it in the fold (#7845) and a
      `record-restore` that skipped it would land a graph a `rebuild_all` of the
      same journal would not produce (the §2.0 divergence).
    """
    proj = _projection()
    view = _view()

    report = proj.rebuild_from_record(view, confirm_destructive=True)
    assert report["nodes_written"] == 3, report

    stored = _props(proj, "Object", "o-stored")
    assert stored.get("embedding") is not None, (
        "a restored :Object whose RECORD ROW HOLDS a vector landed vectorless — "
        "the RESTORE half of #13220 is still open (#13447)")
    # The engine stores a vector as `vecf32`, so compare at that width.
    assert len(stored["embedding"]) == EMBEDDING_DIM
    assert abs(float(stored["embedding"][0]) - _STORED[0]) < 1e-6
    assert abs(float(stored["embedding"][0]) - _DERIVED[0]) > 1e-6, (
        "the restore RE-DERIVED a vector for a row that already held one — the "
        "record's value is the one that must come back")

    subject = _props(proj, "Subject", "s-stored")
    assert subject.get("embedding") is not None, (
        "a restored :Subject whose record row holds a vector landed vectorless")

    derived = _props(proj, "Object", "o-null")
    assert derived.get("embedding") is not None, (
        "a row with NO stored vector restored vectorless — the rebuild must derive "
        "it, or `record-restore` and `rebuild_all` disagree (§2.0)")
    assert abs(float(derived["embedding"][0]) - _DERIVED[0]) < 1e-6

    assert encoder == ["Beta Ltd"], (
        f"the restore paid {len(encoder)} model call(s) {encoder}; exactly the ONE "
        f"row without a stored vector may be encoded (#13447)")


def test_a_refused_rebuild_pays_NO_model_call(encoder):
    """#13447 review cycle 3, issue 3 — the derived fill sits BELOW the guards.

    `fill_missing_row_vectors` is not a pure projection: for every `Object`/`Subject`
    node whose row holds no vector it pays ONE MODEL CALL. Above both destructive
    guards, a REFUSED rebuild — `confirm_destructive` not passed, or the `test_*`
    L2 guard — paid that per row and then refused, where the pre-fix path cost zero
    model calls. It also contradicted this method's own `#11747` note, which was moved
    post-write precisely so a refused rebuild would DO and ASSERT nothing.

    Non-vacuity: pre-fix this reds on `encoder == ["Beta Ltd"]` — the `o-null` row is
    encoded before the guard raises.
    """
    proj = _projection()
    view = _view()
    with pytest.raises(RuntimeError, match="confirm_destructive=True"):
        proj.rebuild_from_record(view, confirm_destructive=False)
    assert encoder == [], (
        f"a REFUSED rebuild paid {len(encoder)} model call(s) {encoder} before it "
        f"refused — the derived fill runs above the destructive guards (#13447)")
    # …and nothing was written: the refusal happens before the wipe, so the graph is
    # exactly what `_projection()` left (empty).
    assert proj.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0


def test_a_second_restore_of_the_same_view_is_byte_identical(encoder, monkeypatch):
    """§2.0's determinism, on the restore path: same view ⇒ same graph.

    This is what makes `bounded=False` on the rebuild load-bearing rather than
    stylistic: a restore that declined an encode inside a wait budget would make
    the output a function of wall-clock timing, so two restores of ONE record could
    differ (#13447's seam asks unbounded there on purpose).

    ⛔ THE DECLINE IS INJECTED FOR THE SECOND RESTORE, and that is the whole
    difference between a witness and a claim (#13447 review cycle 3, issue 5). The
    `encoder` fixture patches `await_write_ready_reason` to answer `None`, so an
    earlier cut of this test passed even with `derive_row` mutated to `bounded=True`
    — there was no decline to observe, and the assertion could not vary under the
    edit it names. A declining bound now makes the mutation visible: correct code
    never consults it (`bounded=False`), and a bounded rebuild would land `o-null`
    vectorless on the second pass, so `first != second`.
    """
    proj = _projection()
    view = _view()
    proj.rebuild_from_record(view, confirm_destructive=True)
    first = {key: _props(proj, "Object", key)
             for key in ("o-stored", "o-null")}

    import tortoise.embeddings as emb
    monkeypatch.setattr(emb.EmbeddingModel, "await_write_ready_reason",
                        classmethod(lambda cls, budget: "budget_expired"))

    proj.rebuild_from_record(view, confirm_destructive=True)
    second = {key: _props(proj, "Object", key)
              for key in ("o-stored", "o-null")}
    assert second["o-null"].get("embedding") is not None, (
        "the second restore DECLINED an encode under a wait budget — the rebuild "
        "must ask unbounded (`bounded=False`), or two restores of one record "
        "differ as a function of wall-clock timing (§2.0)")
    assert first == second
