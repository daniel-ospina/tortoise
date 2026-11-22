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

    Returns ``(calls, called)``: one ``(load_timeout, thread_name)`` tuple per
    call, and an event set the instant the embedder is asked for a model.

    ``load_timeout`` is recorded because #13674 removed the pre-warm's own
    ``300.0`` literal: the two-number shape (a pre-warm override beside a much
    lower product default) described ONE load and made the timeout log name the
    wrong bound, so the pre-warm now takes ``EmbeddingModel._LOAD_TIMEOUT_S``.
    ``thread_name`` is recorded because the flag also gates ``warm_up``, whose
    call is ALSO ``background_load()`` — the thread is what pins THIS call site
    (``hosted_api._lifespan`` spawns it as ``embedding-prewarm``) rather than a
    stray engine-init warm-up.
    """
    calls: list[tuple[float | None, str]] = []
    called = threading.Event()

    def _get(_cls, load_timeout=None):
        calls.append((load_timeout, threading.current_thread().name))
        called.set()
        return object()

    monkeypatch.setattr(EmbeddingModel, "get", classmethod(_get))
    return calls, called


#: The thread ``hosted_api._lifespan`` starts for the pre-warm.
_PREWARM_THREAD = "embedding-prewarm"


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
        f"TORTOISE_EMBEDDER_WARMUP=0: calls={calls}"
    )


def test_default_warms_the_embedder(monkeypatch, embedder_get_spy):
    """An unset flag (the fail-safe default) still pre-warms the embedder."""
    calls, called = embedder_get_spy
    monkeypatch.delenv("TORTOISE_EMBEDDER_WARMUP", raising=False)

    with TestClient(ha.app):
        assert called.wait(timeout=10.0), (
            "the hosted lifespan did not pre-warm the embedder by default"
        )

    # #13674: the pre-warm no longer passes a bound of its own. It used to pass
    # ``load_timeout=300.0`` beside a 90.0 product default, so TWO declared
    # numbers described one load and the timeout log named the wrong one. What
    # this now pins is the property the old ``300.0 in calls`` assertion was
    # really about: the call above is THIS call site, and it takes the one
    # declared bound (the default) rather than a second literal that can drift.
    prewarm_calls = [t for t, name in calls if name == _PREWARM_THREAD]
    assert prewarm_calls, (
        "the hosted lifespan pre-warm did not load the embedder on its own "
        f"{_PREWARM_THREAD!r} thread: calls={calls}"
    )
    overrides = [t for t in prewarm_calls if t is not None]
    assert not overrides, (
        "the hosted pre-warm passed its own load bound "
        f"({overrides}) — #13674 left ONE declared bound, so it must take "
        f"EmbeddingModel._LOAD_TIMEOUT_S: calls={calls}"
    )


def test_a_worker_backing_skips_the_in_process_prewarm(
        monkeypatch, embedder_get_spy):
    """#14186 review F5 — a ``worker``/``external`` backing must not load it HERE.

    The pre-warm exists to make the IN-PROCESS forward pass warm. Once the seam's
    backing is ``worker`` or ``external`` nothing in this process encodes, so the
    load buys nothing and costs exactly what the migration exists to remove: 432 MB
    resident in the serving process (3.66 GB with two overlapping loads, #13674) —
    on top of the worker's own copy. Pre-fix the gate was
    ``_embedder_warmup_enabled()`` alone, so the documented migration in
    ``docs/infra-runbook.md`` §4.1.1e left two copies resident, and the runbook's
    lever table never said the pre-warm had to be disabled by hand too.

    ⛔ The boot PAGE-CACHE warm (``entrypoint.sh``, a throwaway process before
    ``exec``) is deliberately NOT gated: it is not resident, and under ``worker``
    the worker loads the same model and reads those pages warm.
    """
    calls, called = embedder_get_spy
    monkeypatch.setenv("TORTOISE_EMBED_BACKING", "worker")
    monkeypatch.delenv("TORTOISE_EMBEDDER_WARMUP", raising=False)

    with TestClient(ha.app):
        # A buggy spawn reaches the stubbed get within ms; wait so a leak cannot
        # hide behind thread scheduling.
        called.wait(timeout=0.5)

    assert calls == [], (
        "the hosted lifespan loaded the encoder in-process while the embed "
        f"seam's backing was `worker`: calls={calls}"
    )
