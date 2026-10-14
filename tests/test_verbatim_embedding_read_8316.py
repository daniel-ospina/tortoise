"""#8316 — a VERBATIM (caller-supplied) embedding must not poison the dense read.

THE DEFECT
----------
``create_point(content, embedding=[…])``, ``update_point(id, embedding=[…])``
and ``update_entity(id, embedding=[…])`` all store the caller's vector
**VERBATIM** as a plain Cypher ``List`` — that is PR #3018's recorded decision
(the TYPE CHANGE GUARD: a caller-owned float64 must not be silently narrowed
through ``vecf32``), reaffirmed by #5004 and pinned by
``test_update_point_derived_embedding_4520.py::test_a_caller_supplied_vector_stays_verbatim``.
``typeof(n.embedding)`` therefore reads ``List`` after ONE such call, and the
dense read's per-row expression ``vec.euclideanDistance(n.embedding, _qv)``
raises::

    Type mismatch: expected Null or Vectorf32 but was List

for the WHOLE ``MATCH`` — so ONE caller-vectored node degraded the ENTIRE dense
leg, every other node included, to FTS-only recall. ``run_vector_query``
swallows the raise as ``query_failed`` and returns ``[]`` (measured on the
embedded lane AND on the docker lane).

WHY THE FIX IS AT THE READ BOUNDARY, NOT THE WRITE BOUNDARY
-----------------------------------------------------------
Routing the caller's vector through the shared write seam
(``tortoise/vector_write.py``) would narrow a caller-owned float64 to float32 —
a REVERSAL of the recorded PR #3018 decision, not a fix; its route is the
#5046/#5048 reopen, never a quiet re-route. Both shapes the write side
legitimately produces are therefore normalised at the READ boundary instead, in
ONE place: ``vector_write.stored_embedding_vector_expr`` — the expression the
dense read uses for a stored embedding. The stored value is untouched.

RED before / GREEN after
------------------------
``test_a_verbatim_embedding_does_not_poison_the_dense_leg`` fails on the
pre-fix tree (``run_vector_query`` returns ``[]`` — the whole ``MATCH`` raised,
leg-trace reason ``query_failed``) and passes once the read normalises the
shape.

*Mutations that re-redden it* (each must be reverted to pass):

* restore ``<alias>.embedding`` as the distance argument in
  ``search_engine.run_vector_query`` — the ``MATCH`` raises and the dense leg
  returns ``[]``; or
* "simplify" ``stored_embedding_vector_expr`` to
  ``CASE WHEN typeof(…) = 'Vectorf32' THEN … ELSE vecf32(…) END`` — both engines
  evaluate BOTH ``CASE`` arms, so every already-typed row raises
  (``… but was Vectorf32``). ``test_the_typed_shape_is_still_readable`` pins
  that, so the obvious simplification reddens here rather than in production.

Hermetic: the embedder is stubbed, the store is a per-test embedded file. No
provider keys, no network.

Run (embedded lane)::

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \\
        uv run pytest tests/test_verbatim_embedding_read_8316.py -q
"""
from __future__ import annotations

import math
from unittest import mock

import pytest

from tortoise.embeddings import EMBEDDING_DIM
from tortoise.sdk import TortoiseSDK

# The store is a real embedded file (the defect reproduces there first), so the
# module is module-level `embedded_only` and its stem MUST be registered in all
# THREE pinned homes: config/ci-surfaces.yml `carve_out:`, tests/_embedded.py
# TEST_NO_REDIRECT_STEMS, and the `expected` frozenset in
# tests/test_markers.py::test_no_redirect_stems_registry_exact (the third leg of
# the three-way mirror — the #4520/#4521/#7852 shape). Without the registration a
# URI-set selection collects the file and SKIPS every case via the marker hook —
# a permanently green, permanently unexecuted gate (the #4047 shape); with the
# stem missing from the mirror, test_markers.py reds instead.
pytestmark = pytest.mark.embedded_only

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384

#: A float64 value that does NOT round-trip through float32 — so the stored
#: value proves the caller's precision was preserved (the recorded decision).
_CALLER_VALUE = 0.1234567890123


def _vector(seed: float) -> list[float]:
    """A smooth, embedder-shaped vector (no axis a synthetic probe could hit)."""
    return [math.sin(i * 0.017) * 0.4 + seed for i in range(DIM)]


class _StubEmbedder:
    """A TOTAL (never-raising) embedder double.

    Totality is load-bearing, not politeness: ``create_point`` calls
    ``encode_for_store`` on EVERY construction — even when the caller passes
    ``embedding=`` explicitly — inside a bare ``except Exception: pass``. A
    dict-style stub that raised ``KeyError`` for an unmapped text would silently
    drive the encoder-FAILURE branch on those cases (so "no encoder needed" was
    in fact encoding and failing), and a future change that stopped swallowing
    the error would turn them into errors instead of clean assertions. An
    unmapped text therefore gets a deterministic vector, keeping every case on
    the encoder-SUCCESS path.
    """

    def __init__(self, mapping: dict[str, list[float]] | None = None) -> None:
        self._mapping = dict(mapping or {})
        self.calls: list[str] = []

    def __call__(self, text: str, *_a, **_k) -> list[float]:
        self.calls.append(text)
        return list(self._mapping.get(text) or _vector(len(text) % 7 * 0.01))


def _typeof(sdk: TortoiseSDK, pid: str) -> str | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN typeof(n.embedding)",
        params={"id": pid}).result_set
    return rows[0][0] if rows else None


def _stored(sdk: TortoiseSDK, pid: str) -> list[float] | None:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.embedding",
        params={"id": pid}).result_set
    return None if not rows or rows[0][0] is None else list(rows[0][0])


def _dense(sdk: TortoiseSDK, query: list[float]) -> tuple[dict[str, float], str | None]:
    """The shared dense read, with its leg-trace reason (#8316's own symptom).

    Returns ``({hit id: leg score}, reason)`` — the SCORE matters, not just
    membership: a same-width but WRONG vector still yields ``reason =='ok'``
    and still returns the id, so only the score shows the caller's value
    survived the read. Pre-fix the whole ``MATCH`` raises inside
    ``run_vector_query``, which records ``query_failed`` and returns ``[]``.

    The strategy circuit breaker is reset first: three consecutive failures
    open it (30 s cooldown, process-global), and a later test would then record
    ``breaker_open`` instead of the real ``query_failed`` — a green/red for the
    wrong reason.
    """
    from tortoise.search_engine import reset_circuit_breakers, run_vector_query
    reset_circuit_breakers()
    trace: list[dict] = []
    hits = dict(run_vector_query(
        sdk._get_proj().g, query, is_embedded=True, leg_trace=trace))
    return hits, (trace[-1].get("reason") if trace else None)


# ── acceptance: one verbatim node must not poison the whole dense leg ────────

def test_a_verbatim_embedding_does_not_poison_the_dense_leg(tmp_path):
    """RED before: the whole ``MATCH`` raises and NO Point is returned."""
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "verbatim_read.db"))
        try:
            typed_id = sdk.create_point("statement", "alpha text")["id"]
            assert _typeof(sdk, typed_id) == "Vectorf32", (
                "premise: create_point stores a typed vector")

            # The vector VARIES per component (a uniform vector cannot tell a
            # component-wise read from a replicated/permuted one). NOTE: this
            # test does NOT assert the caller's float64 precision — a narrowed
            # store would score 1.0 here exactly as the verbatim one does; that
            # pin is `test_the_caller_value_is_still_stored_verbatim` below.
            verbatim_id = sdk.create_point("statement", "beta text")["id"]
            caller_vec = [_CALLER_VALUE + 0.01 * i for i in range(DIM)]
            sdk.update_point(verbatim_id, embedding=caller_vec)  # THE write
            assert _typeof(sdk, verbatim_id) == "List", (
                "premise: a caller-supplied embedding is stored VERBATIM "
                "(PR #3018) — this test is about the READ, not the write")

            # (1) the OTHER Point's dense read must survive a neighbour's shape
            hits, reason = _dense(sdk, _vector(0.0))
            assert reason == "ok", (
                f"the dense leg recorded {reason!r} instead of 'ok' — ONE "
                "verbatim (`List`) embedding makes `vec.euclideanDistance` "
                "raise for the WHOLE MATCH ('Type mismatch: expected Null or "
                "Vectorf32 but was List'), degrading every Point to FTS-only "
                "recall (#8316)")
            assert typed_id in hits, (
                "the typed Point vanished from the dense leg because a "
                "neighbour holds a verbatim List embedding (#8316)")

            # (2) the verbatim node is itself dense-readable — and the read
            # compared the STORED value, not merely "a vector". Membership
            # alone is blind here: ANY same-width vector keeps reason 'ok' and
            # keeps the row, so a mutation that read some other or constant
            # vector (or that stopped distinguishing the rows) would stay
            # green. The discriminating pair below is the value-preservation
            # half for the plain-List shape (#8316).
            far_id = sdk.create_point(
                "statement", "gamma text",
                embedding=[-(_CALLER_VALUE + 0.01 * i) for i in range(DIM)])["id"]
            hits, reason = _dense(sdk, caller_vec)
            assert reason == "ok", reason
            assert verbatim_id in hits, (
                "the caller-vectored Point is missing from its own dense read — "
                "the read boundary did not normalise the stored shape (#8316)")
            # A self-query scores ``1.0 / (1.0 + euclideanDistance)`` ≈ 1.0;
            # the distant verbatim row must score far lower.
            assert hits[verbatim_id] == pytest.approx(1.0, abs=1e-3), (
                "a self-query on the caller's own vector did not score ~1.0 "
                f"({hits[verbatim_id]!r}) — the read did not return the "
                "caller's stored value (#8316)")
            assert hits[far_id] < hits[verbatim_id] - 0.5, (
                "the dense read did not discriminate the two stored vectors "
                f"({hits[verbatim_id]!r} vs {hits[far_id]!r}) — the rows are "
                "scored against something other than their own embedding "
                "(#8316)")
        finally:
            sdk.close()


def test_the_caller_value_is_still_stored_verbatim(tmp_path):
    """The fix must not narrow the caller's value (the recorded decision).

    A read-side fix touches no write, so the exact float64 must still be on the
    node and ``typeof`` must still read ``List``. If this ever reads
    ``Vectorf32``/``0.12345679…``, the fix was implemented at the WRITE boundary
    — a reversal of PR #3018 that belongs to the #5046/#5048 reopen.
    """
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "verbatim_value.db"))
        try:
            pid = sdk.create_point("statement", "alpha text")["id"]
            sdk.update_point(pid, embedding=[_CALLER_VALUE] * DIM)
            assert _typeof(sdk, pid) == "List"
            stored = _stored(sdk, pid)
            assert stored is not None and stored[0] == _CALLER_VALUE, (
                "the caller's float64 was silently narrowed to float32 — the "
                "recorded verbatim decision (PR #3018) was reversed")
        finally:
            sdk.close()


def test_the_typed_shape_is_still_readable(tmp_path):
    """An already-typed row must pass through the normaliser UNCHANGED.

    ``vecf32()`` REJECTS an already-typed vector ("expected List or Null but
    was Vectorf32"), and both engines evaluate BOTH arms of a ``CASE`` — so a
    "simplification" to ``CASE WHEN typeof(…)=… THEN … ELSE vecf32(…) END``
    raises on every normal row. This is what reddens that edit — and the pin is
    BEHAVIOURAL, never an assertion on the generated Cypher text: the query
    below runs the real expression against a typed row, which is exactly the
    arm a ``CASE`` would evaluate eagerly.
    """
    from tortoise.vector_write import stored_embedding_vector_expr

    expr = stored_embedding_vector_expr("n")
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "typed_read.db"))
        try:
            pid = sdk.create_point("statement", "alpha text")["id"]
            assert _typeof(sdk, pid) == "Vectorf32", "premise: typed vector"
            rows = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) WITH vecf32($q) AS _qv, n "
                f"RETURN vec.euclideanDistance({expr}, _qv)",
                params={"id": pid, "q": _vector(0.0)}).result_set
            assert rows and rows[0][0] == pytest.approx(0.0, abs=1e-6), (
                "the normaliser did not return the typed vector unchanged: "
                f"{rows!r} (#8316)")
        finally:
            sdk.close()


# ── every writer on the verbatim branch is covered by the ONE read seam ──────

def test_create_point_verbatim_embedding_is_dense_readable(tmp_path):
    """``create_point(embedding=…)`` reaches the same branch — and the seam."""
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "create_verbatim.db"))
        try:
            caller_vec = [_CALLER_VALUE] * DIM
            pid = sdk.create_point(
                "statement", "caller vector wins over the encoder",
                embedding=caller_vec)["id"]
            assert _typeof(sdk, pid) == "List", (
                "premise: create_point stores a caller embedding VERBATIM")
            hits, reason = _dense(sdk, caller_vec)
            assert reason == "ok", reason
            assert pid in hits, (
                "a create_point caller-vectored Point is unreadable by the "
                "dense leg (#8316)")
        finally:
            sdk.close()


def test_update_entity_point_branch_verbatim_embedding_is_dense_readable(tmp_path):
    """``update_entity(id, embedding=…)`` — the other reported reachable surface."""
    embedder = _StubEmbedder({"alpha text": _vector(0.0)})
    with mock.patch(_EMBED_PATCH, embedder):
        sdk = TortoiseSDK(str(tmp_path / "entity_verbatim.db"))
        try:
            pid = sdk.create_point("statement", "alpha text")["id"]
            caller_vec = [_CALLER_VALUE] * DIM
            sdk.update_entity(pid, embedding=caller_vec)
            assert _typeof(sdk, pid) == "List", (
                "premise: update_entity's Point branch stores verbatim")
            hits, reason = _dense(sdk, caller_vec)
            assert reason == "ok", reason
            assert pid in hits, (
                "an update_entity caller-vectored Point is unreadable by the "
                "dense leg (#8316)")
        finally:
            sdk.close()


# ── the seam's own residual: what it does NOT normalise, pinned so the claim
#    in its docstring and in the leg_trace comment stays verified ─────────────
#
# The NULL/absent case is deliberately NOT pinned here: the read filters it with
# `WHERE n.embedding IS NOT NULL` BEFORE the seam is evaluated, and a NULL
# distance would be dropped by `WHERE distance IS NOT NULL` anyway — so any
# assertion about it would pass for reasons unrelated to the seam (verified: it
# survives reverting the read seam, and even deleting the null row). The
# leg-level empty/null coverage lives in
# `test_search_engine.py::test_vector_trace_no_embeddings_zero_row_guard`.

def test_a_non_numeric_embedding_still_records_query_failed(tmp_path):
    """The residual #8430 owns: a ``List`` the cast cannot READ still aborts.

    The seam normalises a right-width ``List`` of NUMBERS. A ``List`` of
    strings passes the ``typeof(_v) = 'List'`` filter and then raises inside
    ``vecf32`` ("vectorf32 expects an array of numbers"), which takes the whole
    ``MATCH`` down exactly as the type mismatch did before #8316 — so this is
    #8430's class, not a shape #8316 claims to normalise, and pinning it keeps
    that boundary honest (the WIDTH half of it is pinned by
    ``test_search_engine.py::test_vector_trace_wrong_dim_node_is_query_failed``).
    """
    with mock.patch(_EMBED_PATCH, _StubEmbedder()):
        sdk = TortoiseSDK(str(tmp_path / "non_numeric.db"))
        try:
            healthy = sdk.create_point("statement", "healthy")["id"]
            assert _typeof(sdk, healthy) == "Vectorf32", (
                "premise: the neighbour is a normal typed row")
            sdk._get_proj().g.query(
                "CREATE (p:Point {id:'junk', content:'board game', "
                "pointKind:'statement', embedding:$v})",
                params={"v": ["x"] * DIM})
            _hits, reason = _dense(sdk, [0.1] * DIM)
            assert reason == "query_failed", (
                "expected the unreadable value to abort the MATCH (the #8430 "
                f"residual named by the leg_trace comment) — got {reason!r}. "
                "If this now records 'ok', the #8430 class was closed and "
                "this test plus the leg_trace comment it pins must be "
                "UPDATED, not deleted.")
            # The whole-MATCH claim is carried by the leg REASON above: every
            # failure branch of run_vector_query records `query_failed` and
            # returns [] (so `hits == []` here would be entailed by the reason,
            # not independent evidence of it).
        finally:
            sdk.close()
