"""#4861 — ``TORTOISE_EMBEDDING_MODEL_REQUIRED``, the fail-loud embedder contract.

The invariant: **a process that ARMED this flag is never handed a keyword-only
result it cannot tell apart from a healthy one.** This is its third surface — a
lane that cannot run hybrid must not run (#2985), a read that could not run its
vector leg must not be labelled hybrid (#2952), and a *process* that required
the embedder must not be handed ``None``.

⛔ THE CLAIM IS SCOPED TO ARMED PROCESSES AND TO THE READ PATH. The blanket form
("no green signal may be producible while the dense leg is absent") was FALSE:
#5275 found the flag defeated at ``tortoise_fts_query``, and the product
deliberately degrades elsewhere. The declared intentional degrades — NOT covered
by the raise and NOT defects — are:

* **Write paths** (``encode_for_store`` callers: ``tortoise/api.py``,
  ``tortoise/sdk.py``, ``tortoise/projection/entities.py``, …). Point creation
  must never depend on embeddings, so a write stores no vector and keeps going.
* **The cached-snapshot fallback tier** (``tortoise/fallback_snapshot.py``),
  which exists to serve a keyword-only result when the primary legs fail. It
  keeps its documented degrade: a text query classifies to the vector strategy,
  so an ARMED process raises at the read path's step 2 and never reaches it.
* **``fallback_tfidf``'s in-memory scorer** — same tier, but #5275 makes the
  function itself PROPAGATE the named refusal when it is armed, because its rows
  ARE a read result and ``[]`` is the quietest possible outcome. The scorer's
  own sklearn degrade (flag off) is untouched.
* **``warm_up()``**, whose NEVER-raises contract is separate (see below).

What the flag DOES guarantee, and what this module plus
``tests/test_5275_required_read_path.py`` pin: every ``None`` origin inside
``EmbeddingModel.get`` raises ``EmbedderUnavailableError`` (this module), and
``TortoiseSDK.tortoise_fts_query`` catches that class BY NAME and re-raises it
instead of laundering it into the designed-absence ``no_embedder`` degrade
(#5275).

The contract is OFF by default and reversible: unset, ``get()`` returns ``None``
exactly as before. Every test below arms the embedder's own failure state, so the
whole module is deterministic and never loads (or downloads) a model.

The load-bearing test is :func:`test_required_raises_on_negative_cache` — it is a
*removal* test: rewire that ``None`` return and it fails. That is what separates
a real gate from a decorative branch; asserting the branch merely exists does
not.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tortoise.embeddings import (
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_REVISION,
    EmbeddingModel,
)
from tortoise.exceptions import EmbedderUnavailableError

REQUIRED_ENV = "TORTOISE_EMBEDDING_MODEL_REQUIRED"


def _arm_negative_cache(monkeypatch, kind: str = "not_installed",
                        err: str = "sentence-transformers is not installed") -> None:
    """Put the singleton into the post-failure negative-cache state.

    This is the state a runner-down or missing-extra process is actually in:
    a load already failed and ``get()`` is inside the cooldown window. No model
    is loaded, so the path is reachable deterministically.
    """
    monkeypatch.setattr(EmbeddingModel, "_instance", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", kind)
    monkeypatch.setattr(EmbeddingModel, "_last_error", err)


# --------------------------------------------------------------------------
# The default is unchanged — the contract cannot drift.
# --------------------------------------------------------------------------

def test_unset_returns_none(monkeypatch):
    """UNSET → ``None``, exactly as before. Pins the off-by-default contract."""
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    _arm_negative_cache(monkeypatch)
    assert EmbeddingModel.get() is None


def test_required_is_off_by_default(monkeypatch):
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    assert EmbeddingModel._embedder_required() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_truthy_contract_is_honoured(monkeypatch, value):
    """#4097: the declared truthy contract, not hand-rolled truthiness."""
    monkeypatch.setenv(REQUIRED_ENV, value)
    _arm_negative_cache(monkeypatch)
    assert EmbeddingModel._embedder_required() is False
    assert EmbeddingModel.get() is None


# --------------------------------------------------------------------------
# The removal test — the one that proves the gate is real.
# --------------------------------------------------------------------------

def test_required_raises_on_negative_cache(monkeypatch):
    """REQUIRED + a failed load → raise, not ``None``. **Removal test.**

    Delete the ``return cls._unavailable(...)`` wiring on this path and this
    test fails — which is the point: it asserts the failure is *visible*, not
    that a branch exists.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == "not_installed"
    assert ei.value.context == "negative cache, load failed within cooldown"


def test_required_raises_on_transient_failure_exit(monkeypatch):
    """The other reachable origin: an instance exists but has no model.

    This is the "provisioning succeeded, the load still broke" case — the one
    #4123's provisioning gate cannot see, because the step passed.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    monkeypatch.setattr(EmbeddingModel, "_instance", SimpleNamespace(_model=None))
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_timeout")
    monkeypatch.setattr(EmbeddingModel, "_last_error", "timed out after 90s")
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == "load_timeout"
    assert ei.value.context == "load failed (timeout/OOM)"


# --------------------------------------------------------------------------
# Attribution — the two runs must stay distinguishable BY THE FAILURE.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["not_installed", "load_failed", "load_timeout"])
def test_failure_kind_is_carried_through(monkeypatch, kind):
    """`not_installed` (the env never had it) ≠ `load_failed`/`load_timeout`.

    A REQUIRED process does not accept "designed absence" as an excuse —
    designed for a process that did not ask for the embedder.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind=kind)
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == kind


def test_message_names_model_revision_kind_and_fix(monkeypatch):
    """The raise must be attributable and actionable without reading code."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed", err="ModuleNotFoundError: no st")
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    msg = str(ei.value)
    assert EMBEDDING_MODEL in msg
    assert EMBEDDING_MODEL_REVISION in msg
    assert "not_installed" in msg
    assert REQUIRED_ENV in msg
    assert "keyword-only" in msg
    # #5277: the remediation must NOT repeat the #2985 footgun — an explicit
    # --extra list is EXACT and silently REMOVES the ``parity`` extra.
    assert "--extra embeddings --extra parity" in msg
    assert "--all-extras" in msg
    assert "#2985" in msg
    assert "ModuleNotFoundError" in msg


def test_error_defaults_kind_when_none_recorded(monkeypatch):
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind=None)  # type: ignore[arg-type]
    with pytest.raises(EmbedderUnavailableError) as ei:
        EmbeddingModel.get()
    assert ei.value.failure_kind == "model_unavailable"


# --------------------------------------------------------------------------
# warm_up()'s NEVER-raises contract is preserved (#2952).
# --------------------------------------------------------------------------

def test_warm_up_stays_non_fatal_under_required(monkeypatch):
    """Init stays non-fatal; the *use* site is what fails loudly.

    ``warm_up`` catches ``Exception`` from ``get()``, so raising under REQUIRED
    does not turn a best-effort init probe into a crash — the two contracts do
    not collide.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="load_failed")
    assert EmbeddingModel.warm_up() is False


def test_warm_up_still_returns_false_when_not_required(monkeypatch):
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    _arm_negative_cache(monkeypatch, kind="not_installed")
    assert EmbeddingModel.warm_up() is False


# --------------------------------------------------------------------------
# #5277 item 1 — the NON-RETRIEVAL seams keep the #399/#880 TF-IDF degrade.
# --------------------------------------------------------------------------


def test_encode_degrades_to_tfidf_under_required(monkeypatch):
    """``_encode``'s documented fallback must stay reachable when armed.

    The flag exists so a *retrieval* result cannot be misread as hybrid; it
    must not turn semantic dedup (#880), pairwise similarity or cross-lens
    matching (#399) into hard failures. **Removal test**: rewire the handler
    around ``EmbeddingModel.get()`` in ``_encode`` and this raises.
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    from tortoise.embeddings import _encode

    vectors, degraded = _encode([
        "deploy the new feature to production servers tonight",
        "deploy the new feature to production servers today",
    ])
    assert degraded is True
    assert vectors.shape[0] == 2


def test_default_pairwise_similarity_degrades_under_required(monkeypatch):
    """``TortoiseSDK._default_pairwise_similarity`` is a #399 degrade surface."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="load_timeout")
    from tortoise.sdk import TortoiseSDK

    points = [
        {"id": "a", "content": "deploy the new feature to production servers tonight"},
        {"id": "b", "content": "deploy the new feature to production servers today"},
    ]
    scores = TortoiseSDK._default_pairwise_similarity(points)
    assert scores, "TF-IDF must still score the pair; a raise would have propagated"


def test_find_cross_lens_matches_degrades_under_required(monkeypatch):
    """``cross_lens.find_cross_lens_matches`` is a #399 degrade surface."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="load_failed")
    from tortoise.cross_lens import find_cross_lens_matches

    points = {
        "a": {"content": "deploy the new feature to production servers tonight",
              "lens": "src-1"},
        "b": {"content": "deploy the new feature to production servers today",
              "lens": "src-2"},
    }
    candidates = find_cross_lens_matches(points)
    assert isinstance(candidates, list)  # ran degraded, did not raise


def test_semantic_dedup_degrades_under_required(monkeypatch):
    """``_semantic_dedup``'s #880 degrade chain stays reachable when armed."""
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    from types import SimpleNamespace

    from tortoise.sdk import TortoiseSDK

    class _FakeProj:
        def __init__(self, rows):
            self.g = SimpleNamespace(
                query=lambda *a, **kw: SimpleNamespace(result_set=rows))

    sdk = object.__new__(TortoiseSDK)
    sdk._get_proj = lambda: _FakeProj(
        [("p1", "deploy the new feature to production servers tonight")])
    pairs = sdk._semantic_dedup(
        [
            ({"id": "c1",
              "content": "deploy the new feature to production servers today"}, ""),
        ],
        threshold=0.7,
        return_pairs=True,
    )
    assert pairs, "TF-IDF must still catch the near-duplicate (#880)"


# --------------------------------------------------------------------------
# #5277 item 5 — ``_last_error`` must not nest the prior message every cycle.
# --------------------------------------------------------------------------


def test_last_error_does_not_nest_under_repeated_warm_up(monkeypatch):
    """The REQUIRED raise interpolates ``_last_error``; storing its message
    re-nests the whole prior chain and grows without bound (#5277).
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_negative_cache(monkeypatch, kind="not_installed")
    lengths = []
    for cycle in range(5):
        assert EmbeddingModel.warm_up() is False
        error = EmbeddingModel._last_error or ""
        assert "last error:" not in error, (
            f"_last_error nested the prior raise at cycle {cycle}: {error!r}")
        lengths.append(len(error))
    assert max(lengths) <= 200, f"_last_error grew across cycles: {lengths}"


# --------------------------------------------------------------------------
# #5277 item 4 — the knob must be discoverable next to its siblings.
# --------------------------------------------------------------------------


def test_env_example_declares_the_required_flag():
    from pathlib import Path

    env = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert REQUIRED_ENV in env, (
        "TORTOISE_EMBEDDING_MODEL_REQUIRED is a fail-loud contract switch "
        "but is not declared in .env.example (#5277)")
    assert f"# {REQUIRED_ENV}=0" in env, "the default must be documented as OFF"
