"""#4861 — ``TORTOISE_EMBEDDING_MODEL_REQUIRED``, the fail-loud embedder contract.

The invariant: **no green signal may be producible while the dense leg is
absent.** This is its third surface — a lane that cannot run hybrid must not run
(#2985), a read that could not run its vector leg must not be labelled hybrid
(#2952), and a *process* that required the embedder must not be handed ``None``.

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
    assert "extra embeddings" in msg
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
