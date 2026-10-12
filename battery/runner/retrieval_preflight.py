"""Fail-closed retrieval preflight (#2985) — a degraded retrieval is never a
product measurement.

WHY THIS EXISTS
---------------
The product's retrieval is HYBRID: ``tortoise_fts_query`` fuses FTS (sparse) +
vector (dense) + structural legs via RRF. The dense leg is optional at runtime —
``tortoise/embeddings.py::EmbeddingModel.get()`` returns ``None`` when the
``embeddings`` extra (``sentence-transformers``) is not installed, the model
fails to load, or the product's 60s negative cooldown is active. When it does,
the vector strategy is simply never submitted and retrieval silently degrades to
KEYWORD-ONLY (``leg_trace`` records ``no_embedder``).

That degrade is fine for the PRODUCT (point creation and search must never
depend on embeddings). It is NOT fine for a measurement lane: a local venv
without the ``embeddings`` extra silently ran FTS-only for several runs, and
the numbers were published as product measurements. Measured impact: recall@20
0.80 FTS-only vs **1.00** hybrid on ``factconsolidation_sh_6k`` (0.44 vs 0.61 on
``mh_6k``). The product's own CI installs ``-e '.[test,embeddings]'``
(``.github/workflows/python-ci.yml``), so the extra is part of the canonical
surface; the hosted image pre-downloads and pre-warms ``BAAI/bge-small-en-v1.5``.

WHAT THIS MODULE IS
-------------------
One shared, fail-closed preflight for the REAL lanes that read through
retrieval. A mock/hermetic lane has no retrieval engine and is exempt by
construction (it never calls this).

``hybrid_retrieval_available()`` is the labelling primitive;
``require_hybrid_retrieval()`` is the gate — it raises
:class:`HybridRetrievalUnavailable` with an actionable message naming the fix.

WHO CALLS IT, AND WHEN (the claim above is exact, not aspirational)
-------------------------------------------------------------------
* The **A4 arm's real runner** (``battery/runner/run.py``) calls
  ``require_hybrid_retrieval()`` at arm-init, BEFORE setup/ingest. It is an
  AVAILABILITY gate: it proves the embedder can load, and refuses the arm
  when it cannot. It also arms the arm's observed-leg refusal
  (``A4TortoiseArm.require_observed_hybrid``), which catches a leg that
  fails at QUERY time (``encode_failed`` / ``breaker_open``) — a condition
  this preflight cannot see.
* The **parity Tortoise lane**
  (``battery/parity/mabench_tortoise.run_cr_tortoise_lane``) does NOT call
  ``require_hybrid_retrieval()``. It ingests the pinned pool FIRST, then
  gates AFTER ingest on the OBSERVED per-leg trace
  (``retrieval_capability_gate`` → ``_capability_refusal``). That gate is
  trace-based rather than availability-based because the lane's own
  ``recall`` already emits the trace, so there is no reason to probe the
  embedder separately.

``merge_leg_trace`` below is the one interpreter of the product's trace
shape, shared by both lanes so they record identically.

RETRY SEMANTICS (deliberate)
----------------------------
This module caches NOTHING — not even the positive result. Every call asks the
product's own singleton, which already owns the retry policy: a 60s negative
cooldown after a failed load, then a fresh attempt on the next ``get()``. A
second, stickier negative cache here would hide a later-fixed environment (the
exact failure mode #2985 is about), and a positive cache would hide a
subsequently-broken one.

Import is lazy/guarded: this module is importable without ``sentence-transformers``
installed (the probe reports unavailability instead of raising ImportError).
"""
from __future__ import annotations

import contextlib

__all__ = [
    "HybridRetrievalUnavailable",
    "VectorOverwriteDiscarded",
    "hybrid_retrieval_available",
    "merge_leg_trace",
    "require_hybrid_retrieval",
    "require_vector_overwrite_integrity",
    "vector_overwrite_available",
]

#: The remediation named on every refusal. The `uv sync` warning is load-bearing:
#: an explicit ``--extra`` list is EXACT — ``uv sync --extra embeddings`` SILENTLY
#: REMOVES the ``parity`` extra (pyarrow), which is how a run broke mid-investigation
#: (#2985). Name every extra the lane needs in ONE command, or use ``--all-extras``.
_FIX = (
    "install the `embeddings` extra — `uv sync --extra embeddings --extra "
    "parity` (uv sync with explicit --extra flags is EXACT and REMOVES any "
    "extra you leave out, so name every one the lane needs; `--all-extras` "
    "also works), or `pip install -e '.[embeddings,parity]'`. Verify with: "
    "python -c \"from tortoise.embeddings import EmbeddingModel; "
    "print(EmbeddingModel.get() is not None)\""
)


class HybridRetrievalUnavailable(RuntimeError):
    """The dense retrieval leg cannot run here — hybrid retrieval is off.

    Raised by :func:`require_hybrid_retrieval` so a real lane fails closed
    instead of silently measuring keyword-only retrieval.
    """


class VectorOverwriteDiscarded(HybridRetrievalUnavailable):
    """The store SILENTLY DISCARDS a small-Δ ``vecf32`` overwrite (#4520).

    A subclass of :class:`HybridRetrievalUnavailable` on purpose: it is a
    *dense-leg* refusal, so the runners' existing
    ``except HybridRetrievalUnavailable`` arm-skip handler records it as a
    retrieval refusal with no new failure path, and a caller may still catch
    the subtype specifically when it wants to distinguish the cause.
    """


#: A scratch label — never a product label, so the probe node can never be
#: confused with, or ranked as, real content. The discard is label- and
#: index-independent (measured), so an unindexed scratch label detects it.
_VECTOR_PROBE_LABEL = "_TortoiseVectorProbe"
#: The probe writes `base`, then overwrites ONE component by this much. Real
#: embedder output never moves a component by ~1.0 (the embedded engine's
#: landing threshold), so this is the realistic case — and a one-hot probe
#: (Δ = 1.0) LANDS even on the defective engine and would hide the defect.
_VECTOR_PROBE_DELTA = 0.02
_VECTOR_PROBE_DIM = 384


def _probe_vector(seed: float) -> list[float]:
    """Deterministic, realistic-looking vector: small components in ±0.05."""
    return [seed + 0.0001 * i for i in range(_VECTOR_PROBE_DIM)]


def _probe_one_write(graph, label: str, probe_id: str,
                     vector: list[float], *, seam: bool) -> list[float] | None:
    """Overwrite the probe node's vector and return what the store READ back.

    ``seam=True`` writes through the product's clear-then-set seam (the write
    path the product actually uses); ``seam=False`` issues the RAW overwrite
    (the engine-level canary, reported as a diagnostic but never gated on).
    """
    if seam:
        # The product's write path — the only path a store must support.
        from tortoise.vector_write import (
            clear_then_set_prefix,
            set_embedding_clause,
        )
        query = (f"MATCH (n:{label} {{id:$id}}) " + clear_then_set_prefix("n")
                 + "SET " + set_embedding_clause("n"))
    else:
        query = f"MATCH (n:{label} {{id:$id}}) SET n.embedding = vecf32($embedding)"
    graph.query(query, params={"id": probe_id, "embedding": vector})
    rows = graph.query(
        f"MATCH (n:{label} {{id:$id}}) RETURN n.embedding",
        params={"id": probe_id}).result_set
    return list(rows[0][0]) if rows and rows[0][0] is not None else None


def _landed(got: list[float] | None, want: list[float]) -> bool:
    return got is not None and abs(got[0] - want[0]) <= _VECTOR_PROBE_DELTA / 2


def vector_overwrite_available(graph) -> tuple[bool, str | None]:
    """Probe one store's VECTOR WRITE PATH for the #4520 silent discard.

    Writes a scratch vector, OVERWRITES it with a realistic small-Δ vector
    **through the product's clear-then-set seam** (``tortoise/vector_write.py``),
    reads it back and compares the **VALUE** — a ``typeof`` check passes while
    the value is stale, which is the whole trap. The scratch node is deleted in
    a ``finally``, so a successful probe leaves no residue.

    ``graph`` is any handle with ``.query(cypher, params=…)``
    (``FalkorProjection.g`` / a ``_GuardedGraph``). Returns ``(available,
    reason)``; any failure (a raising query, an unreadable vector) is
    UNAVAILABLE — the fail-closed direction of the embedder probe.

    WHY THE SEAM, NOT THE RAW ENGINE: the writers #4520 migrated route through
    the seam, and the seam is what makes the embedded engine land the overwrite.
    Gating on the RAW overwrite would refuse the embedded store *even after the
    fix* — and the embedded store is the a4 real lane's default target — so it
    would deterministically skip the treatment arm. The probe therefore answers
    the PRODUCT question (does a small-Δ overwrite through our own write path
    land?) and the raw-engine result is reported as a diagnostic on failure.
    """
    import uuid
    probe_id = f"probe-{uuid.uuid4().hex}"
    base = _probe_vector(-0.02)
    moved = list(base)
    moved[0] += _VECTOR_PROBE_DELTA
    label = _VECTOR_PROBE_LABEL
    try:
        graph.query(
            f"CREATE (n:{label} {{id:$id}}) SET n.embedding = vecf32($embedding)",
            params={"id": probe_id, "embedding": base})
        got = _probe_one_write(graph, label, probe_id, moved, seam=True)
        # Diagnostic only (never gated): does the RAW engine overwrite land?
        raw_discards = None
        if not _landed(got, moved):
            try:
                graph.query(
                    f"MATCH (n:{label} {{id:$id}}) "
                    "SET n.embedding = vecf32($embedding)",
                    params={"id": probe_id, "embedding": base})
                raw = _probe_one_write(graph, label, probe_id, moved, seam=False)
                raw_discards = not _landed(raw, moved)
            except Exception:
                raw_discards = None
    except Exception as e:
        return False, f"the vector-write probe could not run ({type(e).__name__}: {e})"
    finally:
        with contextlib.suppress(Exception):  # cleanup is best-effort
            graph.query(f"MATCH (n:{label} {{id:$id}}) DELETE n",
                        params={"id": probe_id})
    if _landed(got, moved):
        return True, None
    detail = ("the RAW engine overwrite ALSO discards it" if raw_discards
              else "the raw engine overwrite lands, so the seam itself is not "
                   "taking effect on this store" if raw_discards is False
              else "the raw engine check was inconclusive")
    return False, (
        f"a small-Δ ({_VECTOR_PROBE_DELTA}/component) vector OVERWRITE through "
        f"the product's clear-then-set write seam did not land — the store "
        f"returned the PRE-write value; the type was still Vectorf32, so a "
        f"typeof check would have passed ({detail})")


def require_vector_overwrite_integrity(graph) -> None:
    """Fail closed unless ``graph``'s store lands a small-Δ vector overwrite.

    Call at real-lane start-up (before ingest) on the store the lane will
    measure. Raises :class:`VectorOverwriteDiscarded` — a
    :class:`HybridRetrievalUnavailable` subclass the runners already handle —
    when the store silently keeps the stale vector. This is the fail-loud half
    of #4520: the shared write seam (``tortoise/vector_write.py``) makes the
    write land, and this probe validates that the SEAM lands on this store.

    It CANNOT see a writer that bypasses the seam — the probe itself writes
    through the seam, so a bypass is invisible to it. Writer routing is a static
    property of the call sites, guarded for the ENUMERATED set by
    ``tests/test_vector_write_seam_4520.py``'s inventory net (which pins the
    writers this change migrated — it is not a proof about unenumerated
    writers).
    """
    available, reason = vector_overwrite_available(graph)
    if not available:
        raise VectorOverwriteDiscarded(
            f"the vector leg is UNTRUSTWORTHY on this store ({reason}). On the "
            f"embedded engine (falkordblite/redislite) a `vecf32` overwrite of "
            f"an existing vector is silently discarded unless a component moves "
            f"by ~1.0, and real embedder output never does — so dense retrieval "
            f"can answer from a vector that no longer describes the stored "
            f"content (#4520). Fix: route the writer through "
            f"`tortoise/vector_write.py` (clear the property in the same "
            f"operation as the write), or run the lane against a server "
            f"FalkorDB (TORTOISE_DB_URI=docker://...) where the overwrite lands.")


def _probe() -> tuple[bool, str | None]:
    """Probe the product's embedder singleton. Returns ``(available, reason)``.

    No caching of any kind (see module docstring): the product's
    ``EmbeddingModel.get()`` owns the retry/cooldown policy. The import is
    lazy so this module loads without ``sentence-transformers``.
    """
    try:
        from tortoise.embeddings import EmbeddingModel
    except Exception as e:  # noqa: BLE001, RUF100 — import guard, not a metric
        return False, (
            f"tortoise.embeddings is not importable "
            f"({type(e).__name__}: {e})")
    try:
        model = EmbeddingModel.get()
    except Exception as e:  # noqa: BLE001, RUF100 — fail closed, keep the reason
        return False, f"EmbeddingModel.get() raised ({type(e).__name__}: {e})"
    if model is None:
        return False, (
            "EmbeddingModel.get() returned None — sentence-transformers is not "
            "installed, the model failed to load, or the product's 60s "
            "negative-load cooldown is active (it retries on the next get())")
    return True, None


def hybrid_retrieval_available() -> bool:
    """True when a real retrieval call would submit the dense leg too.

    Labelling primitive for artifacts (``retrieval_degraded`` /
    ``retrieval_legs``): never gate on this directly — use
    :func:`require_hybrid_retrieval` so the failure is loud.
    """
    return _probe()[0]


def require_hybrid_retrieval() -> None:
    """Fail closed unless the product's embedder is available.

    Call this at the top of every REAL lane that reads through retrieval
    BEFORE any ingest or question — unless the lane has its own
    trace-based gate (the parity Tortoise lane gates AFTER ingest on the
    OBSERVED leg trace; see the module docstring). Raises
    :class:`HybridRetrievalUnavailable` with a message that names the
    ``embeddings`` extra and how to verify the fix — a lane that cannot
    run hybrid must not run at all, because its number would be a
    keyword-only number wearing the product's label.

    The STORE is a SEPARATE gate: a lane that will ingest calls
    :func:`require_vector_overwrite_integrity` on the store handle once it
    exists (the runner has no store at this point), so a store that silently
    discards a small-Δ vector overwrite (#4520) is refused before ingest.
    """
    available, reason = _probe()
    if not available:
        raise HybridRetrievalUnavailable(
            f"hybrid retrieval is UNAVAILABLE ({reason}). A real retrieval "
            f"lane would silently run KEYWORD-ONLY (FTS) retrieval and "
            f"understate recall (measured 0.80 FTS-only vs 1.00 hybrid on "
            f"factconsolidation_sh_6k). Fix: {_FIX}")


def merge_leg_trace(legs: list[str], trace: list[dict] | None) -> bool:
    """Fold one product ``leg_trace`` into ``legs``; return any-degraded.

    The single interpreter of the R2 #1541 trace shape
    ({"leg", "ran", "degraded", "reason", "count"}) shared by the parity
    Tortoise lane and the a4 arm, so both record retrieval conditions
    identically. ``legs`` is mutated in place with the UNION of observed leg
    names (order of first observation). Defensive by design: an unexpected
    entry shape is ignored rather than crashing a lane — the preflight, not
    this helper, is the fail-closed gate.
    """
    degraded = False
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        leg = entry.get("leg")
        if isinstance(leg, str) and leg and leg not in legs:
            legs.append(leg)
        if entry.get("degraded"):
            degraded = True
    return degraded
