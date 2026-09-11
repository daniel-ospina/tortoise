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
One shared, fail-closed preflight that every REAL lane reading through
retrieval calls before it ingests or asks anything. A mock/hermetic lane has no
retrieval engine and is exempt by construction (it never calls this).

``hybrid_retrieval_available()`` is the labelling primitive;
``require_hybrid_retrieval()`` is the gate — it raises
:class:`HybridRetrievalUnavailable` with an actionable message naming the fix.

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

__all__ = [
    "HybridRetrievalUnavailable",
    "hybrid_retrieval_available",
    "merge_leg_trace",
    "require_hybrid_retrieval",
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

    Call this at the top of every REAL lane that reads through retrieval,
    BEFORE any ingest or question. Raises :class:`HybridRetrievalUnavailable`
    with a message that names the ``embeddings`` extra and how to verify the
    fix — a lane that cannot run hybrid must not run at all, because its
    number would be a keyword-only number wearing the product's label.
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
