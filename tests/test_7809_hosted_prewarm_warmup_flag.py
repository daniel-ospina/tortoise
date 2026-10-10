"""#7809 — the hosted lifespan pre-warm honours ``TORTOISE_EMBEDDER_WARMUP``.

``tests/conftest.py::_disable_embedder_autowarmup`` sets
``TORTOISE_EMBEDDER_WARMUP=0`` for the whole suite, but
``tortoise/hosted_api.py::_lifespan`` spawned its ``embedding-prewarm``
thread unconditionally: every ``with TestClient(ha.app)`` started a REAL
embedder load in a daemon thread that outlived its test and leaked into the
next one (real wall-clock cost + shared state — #4387 retries the blocked HF
HEAD requests ~5x with backoff).

These tests drive the REAL lifespan and pin the two halves of the contract:
the flag skips the load, and the fail-safe default still warms.
"""
from __future__ import annotations

import os
import threading

# hosted_api's auth module needs the pepper at import time (same preamble as
# tests/test_hosted_api.py).
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha
from tortoise.embeddings import EmbeddingModel


@pytest.fixture
def embedder_get_spy(monkeypatch):
    """Replace ``EmbeddingModel.get`` with a recorder + first-call signal.

    Returns ``(calls, called)``: the recorded ``load_timeout`` args and an
    event set the instant the embedder is asked for a model.
    """
    calls: list[float | None] = []
    called = threading.Event()

    def _get(_cls, load_timeout=None):
        calls.append(load_timeout)
        called.set()
        return object()

    monkeypatch.setattr(EmbeddingModel, "get", classmethod(_get))
    return calls, called


def test_flag_zero_skips_the_hosted_prewarm(monkeypatch, embedder_get_spy):
    """``TORTOISE_EMBEDDER_WARMUP=0`` → the lifespan starts no embedder load."""
    calls, called = embedder_get_spy
    monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")

    with TestClient(ha.app):
        # A buggy spawn reaches the stubbed get within ms; wait so a leak
        # cannot hide behind thread scheduling.
        called.wait(timeout=0.5)

    assert calls == [], (
        "the hosted lifespan loaded the embedder despite "
        f"TORTOISE_EMBEDDER_WARMUP=0: load_timeout={calls}"
    )


def test_default_warms_the_embedder(monkeypatch, embedder_get_spy):
    """An unset flag (the fail-safe default) still pre-warms the embedder."""
    calls, called = embedder_get_spy
    monkeypatch.delenv("TORTOISE_EMBEDDER_WARMUP", raising=False)

    with TestClient(ha.app):
        assert called.wait(timeout=10.0), (
            "the hosted lifespan did not pre-warm the embedder by default"
        )

    # 300.0 is the hosted pre-warm's dedicated cold-start window (#545);
    # asserting it pins THIS call site, not a stray engine-init warm-up.
    assert 300.0 in calls, f"hosted pre-warm did not load: calls={calls}"
