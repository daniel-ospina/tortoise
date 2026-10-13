"""#7357 — the canonical-store guard: a test session must not target the map store.

Two layers are pinned here, because a predicate that nothing calls is not a
guard:

* the PREDICATE (`tests/_canonical_store_guard.py`) — which targets are the
  canonical instance, and which are the sanctioned test lanes;
* the WIRING — a real subprocess pytest session with a canonical
  ``TORTOISE_DB_URI`` must FAIL AT SESSION START with the guard's message,
  before the #6073 DB probe is reached. That wiring is what the four measured
  recurrences needed: the test run that minted graphs on the canonical instance
  was a plain `pytest` invocation with no guard on the URI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._canonical_store_guard import (
    ALLOW_CANONICAL_ENV,
    CANONICAL_DB_PORT,
    canonical_store_refusal,
    is_canonical_store_uri,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Pop these from the child's environment so the PARENT session's lane state
#: (and this file's own escape-hatch tests) can never leak into a child that is
#: being used to prove the guard fires. `TORTOISE_TEST_ALLOW_CANONICAL` is the
#: load-bearing one: an exported opt-in in the parent shell would silently
#: disarm the child.
_CHILD_LANE_VARS = (
    "TORTOISE_DB_URI",
    "TORTOISE_TEST_ALLOW_CANONICAL",
    "TORTOISE_TEST_ALLOW_REMOTE",
)


# ── the predicate: canonical targets are refused ──────────────────────────


@pytest.mark.parametrize(
    "uri",
    [
        # The measured churn source: the documented map URI, verbatim.
        "docker://:@127.0.0.1:16379/tortoise",
        # A credentialed spelling and another graph name — the harm is the
        # INSTANCE (mints on it), not the graph a test happens to name.
        "docker://:hunter2@127.0.0.1:16379/tortoise_test_matrix",
        "docker://:falkordb@localhost:16379/tortoise",
        "redis://:pw@127.0.0.1:16379/anything",
        # Portless: `FalkorProjection.from_uri` resolves this to 16379, so it IS a
        # canonical target rather than an ambiguous one.
        "docker://:@127.0.0.1/tortoise",
        "docker://:@localhost/graph",
    ],
)
def test_the_canonical_store_is_identified(uri):
    assert is_canonical_store_uri(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        # Every sanctioned test lane.
        "docker://:falkordb@localhost:6379/tortoise_test_matrix",  # compose sidecar
        "docker://:falkordb@localhost:16390/tortoise_test_matrix",  # test_lane.py
        "docker://:@localhost:16412/x",  # #6673 ephemeral
        # The embedded/carve-out lane: no URI at all.
        "",
        None,
        # Path-style values are not server URIs.
        "/tmp/tortoise.db",
        # A REMOTE host on this port number is another machine's database —
        # refusing it would punish a deliberate TORTOISE_TEST_ALLOW_REMOTE=1
        # session for a coincidence.
        "docker://:pw@db.internal:16379/tortoise",
        # Malformed / non-numeric port: the #6073 probe owns that diagnosis.
        "docker://:@127.0.0.1:notaport/g",
    ],
)
def test_sanctioned_targets_are_not_identified(uri):
    assert is_canonical_store_uri(uri) is False


# ── the refusal message ───────────────────────────────────────────────────


def test_refusal_names_the_evidence_and_the_way_out(monkeypatch):
    monkeypatch.delenv(ALLOW_CANONICAL_ENV, raising=False)
    msg = canonical_store_refusal("docker://:@127.0.0.1:16379/tortoise")
    assert msg is not None
    assert f"127.0.0.1:{CANONICAL_DB_PORT}" in msg  # names the target
    assert "CACHE_SIZE" in msg  # names the mechanism
    assert "#7357" in msg
    assert "THIS IS NOT YOUR DIFF" in msg
    assert "tools/test_lane.py" in msg  # the private-lane way out
    assert "tortoise_test_matrix" in msg  # the sanctioned test graph
    assert ALLOW_CANONICAL_ENV in msg  # the explicit escape


def test_refusal_never_echoes_the_credential(monkeypatch):
    monkeypatch.delenv(ALLOW_CANONICAL_ENV, raising=False)
    msg = canonical_store_refusal("docker://:topsecret@127.0.0.1:16379/tortoise")
    assert msg is not None
    assert "topsecret" not in msg


def test_the_explicit_opt_in_disarms_the_guard(monkeypatch):
    uri = "docker://:@127.0.0.1:16379/tortoise"
    monkeypatch.delenv(ALLOW_CANONICAL_ENV, raising=False)
    assert canonical_store_refusal(uri) is not None
    for spelling in ("1", "true", "yes", "on"):
        monkeypatch.setenv(ALLOW_CANONICAL_ENV, spelling)
        assert canonical_store_refusal(uri) is None, spelling
    # A blank/garbage value is NOT an opt-in (#4097: blank is unset).
    monkeypatch.setenv(ALLOW_CANONICAL_ENV, "")
    assert canonical_store_refusal(uri) is not None


def test_non_canonical_targets_have_no_refusal(monkeypatch):
    monkeypatch.setenv(ALLOW_CANONICAL_ENV, "1")  # must not even matter
    assert canonical_store_refusal("") is None
    assert canonical_store_refusal("docker://:falkordb@localhost:6379/x") is None


# ── the wiring: a real session fails at start, before the DB probe ────────


def _run_session(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a subprocess pytest session against the guard's target.

    The child loads the repo conftest — the code under test. The parent's lane
    variables are normalized away first (see `_CHILD_LANE_VARS`), so the child's
    verdict is about the URI it was handed and nothing else.
    """
    child_env = os.environ.copy()
    for var in _CHILD_LANE_VARS:
        child_env.pop(var, None)
    child_env.update(env)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_canonical_store_guard.py::test_the_canonical_store_is_identified",
            "-q",
            "-p",
            "no:cacheprovider",
            "--no-header",
        ],
        cwd=REPO_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_session_start_refuses_the_canonical_store():
    """A real pytest session with the map URI FAILS on the guard, not on the DB.

    The failure must be the GUARD's, not the #6073 probe's and not a green
    pass: if this session ran, it would mint graphs on the canonical instance.
    """
    res = _run_session({"TORTOISE_DB_URI": "docker://:@127.0.0.1:16379/tortoise"})
    combined = res.stdout + res.stderr
    assert res.returncode != 0, (
        "a session pointed at the canonical store must fail, not green-pass\n" + combined
    )
    assert "CANONICAL Tortoise store" in combined, combined
    assert "#7357" in combined, combined
    # IMPORT-time, not a fixture: the refusal must precede COLLECTION, because
    # the session's own tripwire mints a `test_tripwire_probe_*` graph when the
    # fixtures run — that mint is part of the harm (measured 2026-10-13: 8 new
    # `test_*` graphs on the canonical instance in 150 s, two of them
    # `test_tripwire_probe_*`, with no lane doing anything but running pytest).
    assert "tests/conftest.py" in combined, combined
    # The #6073 wedge message must NOT be what fired: the guard runs before the
    # probe, so a canonical target never even PINGs the map store.
    assert "did not answer a PING" not in combined, combined
    assert "1 passed" not in combined, combined
