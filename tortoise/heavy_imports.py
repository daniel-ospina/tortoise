"""The ONE lock that serializes the heavy optional-dependency imports (#5718).

Why this module exists
----------------------
``torch`` (via ``sentence_transformers`` → ``transformers``) and a cold
``sklearn``/``scipy`` import must never overlap in one process. scipy's
array-API dispatch decides which namespace an array belongs to by peeking
straight at ``sys.modules`` and then doing an UNGUARDED ``getattr``
(``scipy/_external/array_api_compat/common/_helpers.py::_issubclass_fast``)::

    mod = sys.modules["torch"]            # present the instant torch's import begins
    parent_cls = getattr(mod, "Tensor")   # AttributeError while torch is mid-import

A cold ``sklearn.feature_extraction.text`` import pulls ``scipy.stats`` in, and
scipy.stats exercises that dispatch while it builds itself — so a cold sklearn
import that overlaps a torch import raises::

    AttributeError: partially initialized module 'torch' has no attribute
    'Tensor' (most likely due to a circular import)

That is a race, not a broken install — but it poisons the sklearn/scipy import
for the whole process, and the sparse fallback's broad ``except Exception`` then
swallows it and returns no hits (measured on the #5718 Python-CI shard, where the
shard's import order started an embedder load before the oracle's TF-IDF
assertion ran).

The race is SYMMETRIC — whichever of the two families is mid-import and whichever
is cold, the pair must not overlap. ONE lock, taken by *every* heavy importer on
BOTH sides, closes it by construction: whichever import runs first, the other
waits and never observes a half-built module. A one-sided guard (a ``torch``-only
tripwire, or a ``sklearn``-only one) cannot see the mirror case, which is why the
rule below covers both families with a single mechanism.

The rule, machine-checked
-------------------------
Every ``torch``/``sentence_transformers``/``transformers``/``scipy``/``sklearn``
import in the ``tortoise`` package must live inside one of the helpers below —
i.e. inside a ``with _HEAVY_IMPORT_LOCK:`` block *in this module*.
``tests/test_heavy_import_guard.py`` parses the package and fails naming any bare
import, so a new torch/scipy/sklearn import cannot re-open the window silently.
Call one of these helpers; never import those modules directly in library code.

Properties the guard depends on (do not weaken)
-----------------------------------------------
* the lock is held ONLY across the ``import`` statement — never across model
  construction, encoding or search, which would serialize the hot path;
* it is an ``RLock`` (re-entrant) so a helper may be called from code that
  already holds it;
* no helper calls another helper (no nesting, no lock-order questions);
* nothing acquires ``_HEAVY_IMPORT_LOCK`` while holding a higher-level lock, and
  no helper calls into user code, so there is no lock-order inversion.

Why a leaf module rather than ``tortoise/embeddings.py``
--------------------------------------------------------
``embeddings`` is imported by the projection package
(``tortoise/projection/grounding.py``) and by ``rerank``; putting the lock there
invites the very import cycle this module cures — a cycle in the lock's own
import graph would re-introduce a partially-initialized module, i.e. the disease
instead of the cure. This module imports nothing but ``threading``, so it can
never take part in a cycle.
"""
from __future__ import annotations

import threading

_HEAVY_IMPORT_LOCK = threading.RLock()


def import_tfidf_vectorizer():
    """Return sklearn's ``TfidfVectorizer``, imported under the heavy-import lock.

    Raises ``ImportError`` when the ``[embeddings]`` extra is absent, exactly as
    the bare ``from sklearn...`` did.
    """
    with _HEAVY_IMPORT_LOCK:
        from sklearn.feature_extraction.text import TfidfVectorizer  # lazy: [embeddings] extra
    return TfidfVectorizer


def import_sentence_transformer():
    """Return sentence-transformers' ``SentenceTransformer`` under the lock.

    Only the import is serialized — torch is fully initialized once
    ``sentence_transformers`` finishes importing — so the (slow) model
    construction stays outside the lock and never blocks the sparse fallback.
    """
    with _HEAVY_IMPORT_LOCK:
        from sentence_transformers import SentenceTransformer
    return SentenceTransformer


def import_cross_encoder():
    """Return sentence-transformers' ``CrossEncoder`` under the lock.

    The ask-lane reranker (``tortoise/rerank.py``) is a second torch importer
    (``sentence_transformers`` → ``transformers`` → ``torch``). It shares this
    lock for the same reason as the embedder load.
    """
    with _HEAVY_IMPORT_LOCK:
        from sentence_transformers import CrossEncoder
    return CrossEncoder


def import_torch():
    """Return the ``torch`` module, imported under the heavy-import lock.

    ``import torch`` is itself a torch-family importer, so it obeys the same rule
    as the two sentence-transformers entry points. In the ask lane torch is
    already warm when this runs, so the lock is uncontended; routing it through
    here keeps the invariant *total* rather than carving out an exception the
    guard would have to name.
    """
    with _HEAVY_IMPORT_LOCK:
        import torch
    return torch


def import_scipy_special():
    """Return the ``scipy.special`` module, imported under the heavy-import lock."""
    with _HEAVY_IMPORT_LOCK:
        from scipy import special
    return special


def import_scipy_sparse():
    """Return ``(scipy.sparse, scipy.sparse.linalg)``, imported under the lock."""
    with _HEAVY_IMPORT_LOCK:
        from scipy import sparse
        from scipy.sparse import linalg as sparse_linalg
    return sparse, sparse_linalg
