# tests/test_tripwire.py
"""E2E-6 tripwire surface (epic #1647 Task 4).

The conftest session-start tripwire (`_assert_backend_identity`) is the
session-level gate: with TORTOISE_DB_URI set, the session must be server
mode — a dormant redirect would silently run the migrated suite on embedded
and pass green. The session-level behavior (fail at session start, never
skip) is verified by SUBPROCESS pytest runs — an in-process test would be
tangled with this suite's own tripwire session (the fixture already ran at
session start; a failure can't be observed from inside the session it
governs). The probe's redirect-sensitivity is additionally pinned in-process
(test_probe_flips_with_redirect_state) via the documented exemption knob.

The helper `_tripwire_probe()` lives HERE (a test module, not conftest) so
its caller frame resolves to a non-exempt test stem — the redirect's
frame-based exemption key. Conftest imports and calls it; conftest's own
frame is irrelevant (the probe's frame is the caller's, inside this module).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tortoise.projection import FalkorProjection

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_NODEID = "tests/test_tripwire.py::test_session_target_probe"

# The child session's lane variables must never leak from the parent (a dev
# shell's TORTOISE_DB_URI / carve-out exemption would flip the child's
# expectations). The child conftest re-exports TEST_MODE / TEST_SESSION /
# JOURNAL_FILE itself; NO_REDIRECT is re-derived from the (currently empty)
# carve-out constant. TORTOISE_DB_PATH is the same lane class (a dev value
# redirects embedded/no-arg constructions to a real DB) and
# TORTOISE_EMBEDDED_AOF toggles embedded durability — both inert in the
# current child targets but popped for defense (re-review Issue 2).
_CHILD_LANE_VARS = (
    "TORTOISE_DB_URI",
    "TORTOISE_TEST_EXPECT_URI",
    "TORTOISE_TEST_ALLOW_REMOTE",
    "TORTOISE_TEST_NO_REDIRECT",
    "TORTOISE_TEST_JOURNAL_FILE",
    "TORTOISE_DB_PATH",
    "TORTOISE_EMBEDDED_AOF",
    "TORTOISE_ALLOW_NONSTANDARD_PATH",
)


def _tripwire_probe() -> FalkorProjection:
    """Probe through the redirect, not around it (cycle-5 P1-4).

    Constructs via path= with the caller frame in THIS test module —
    `_caller_test_stem()` resolves to "test_tripwire" (a non-exempt stem), so
    under TORTOISE_TEST_MODE + URI the redirect arms and `_is_embedded is
    False` IFF the redirect is armed. `from_uri()` is NOT a valid probe: it
    builds host-mode directly, bypassing the redirect — `_is_embedded` is
    False on any reachable server regardless of redirect state, so an inert
    redirect could never be detected (vacuous — the cycle-4 probe).

    The path is per-process (os.getpid()) so the embedded-lane construction
    (test_probe_flips' inert-redirect half) never races a concurrent suite's
    redislite daemon on a fixed machine-global path (re-review Issue 3).
    """
    return FalkorProjection(f"/tmp/tripwire-probe-{os.getpid()}.db")


def test_session_target_probe():
    """Trivial target for the subprocess tripwire sessions.

    The code under test in those sessions is the tripwire itself (and the
    fail-closed session gate); this test exists only so the child session
    has a test body to run. It must never construct anything — the child
    sessions' journal/backends are asserted through the session result."""
    assert True


def _docker_reachable(host: str = "localhost", port: int = 6379) -> bool:
    """True when a live FalkorDB answers a TCP connect on host:port.

    The repo's skip-guard convention (#1436) — live-FalkorDB-required tests
    SKIP with a FalkorDB-reason when the docker is absent, never error."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.fixture
def docker_up():
    if not _docker_reachable():
        pytest.skip("live FalkorDB (localhost:6379) not reachable")
    return True


def _dead_port_holder() -> tuple[int, "socket.socket"]:
    """A local port that FAILS connections deterministically.

    Bind WITHOUT listen and hold the socket for the caller's duration: the
    port is then un-stealable (no other process can bind it while we hold
    it), so the child's connect can never succeed — it is refused (RST) on
    stacks that answer, or dropped (SYN timeout, macOS) on stacks that
    don't. Either way the failure is connection-level, never a green pass
    (deep-review Issue 2 — the old bind+close had a TOCTOU window). Close
    the returned socket after the subprocess call. Returned as
    (port, socket) so the socket stays referenced (closing it on GC would
    reopen the window).
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    return s.getsockname()[1], s


def _run_session(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a subprocess pytest session against the tripwire target.

    The child loads the repo conftest (tests/conftest.py) and its
    session-start tripwire — the code under test. The parent's env is
    copied then the lane variables normalized away, so a dev shell's URI or
    carve-out exemption never leaks into the child's expectations. The
    child's own conftest re-exports the session signal + nonce + journal."""
    child_env = os.environ.copy()
    for var in _CHILD_LANE_VARS:
        child_env.pop(var, None)
    child_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", TARGET_NODEID, "-q",
         "-p", "no:cacheprovider", "--timeout=300"],
        cwd=REPO_ROOT, env=child_env,
        capture_output=True, text=True, timeout=600,
    )


def test_server_reachable_session_passes(docker_up):
    """URI set + reachable server → the tripwire passes and the child
    session runs green (rc 0, the target test passed) — the redirect is
    armed, so the probe went server-mode."""
    res = _run_session({"TORTOISE_DB_URI": "docker://:falkordb@localhost:6379"})
    assert res.returncode == 0, (
        "reachable-server session must pass\nstdout:\n" + res.stdout +
        "\nstderr:\n" + res.stderr)
    assert "1 passed" in res.stdout


def test_unreachable_server_fails_session_not_skip():
    """URI set + unreachable server → session FAILURE at start (fail-closed,
    D-4=A) — never a skip and never a green pass. The tripwire's probe
    construction cannot reach the server (server-mode health check raises),
    so the child exits non-zero with the connection failure."""
    port, held = _dead_port_holder()
    try:
        res = _run_session({"TORTOISE_DB_URI": f"docker://:falkordb@localhost:{port}"})
    finally:
        held.close()
    combined = res.stdout + res.stderr
    assert res.returncode != 0, (
        "unreachable server must fail the session, not green-pass")
    # The failure may surface as a ConnectionError / TimeoutError from the
    # eager client connect or the server-mode health-check RuntimeError —
    # either way it must be a connection-level failure, never a green pass
    # or a skip. (The held dead-port socket means the child's connect can
    # never succeed; the observed error depends on the TCP stack: refused
    # or dropped-SYN timeout.)
    assert ("ConnectionError" in combined or "health check failed" in combined
            or "Connection refused" in combined
            or "TimeoutError" in combined or "timed out" in combined), (
        "the session failure must be a server-connection failure\n" + combined)
    assert "1 passed" not in combined, "no test may pass on a dead server"
    assert "1 skipped" not in combined, "the tripwire must fail, never skip"


def test_non_loopback_uri_fails_session():
    """A remote/shared TORTOISE_DB_URI fails at session start, before ANY
    test writes — the tripwire's locality gate (D-4/P0-2). The refusal
    happens before the probe constructs, so zero graphs are created."""
    res = _run_session({
        "TORTOISE_DB_URI": "docker://:pw@db.internal.example.com:6379"})
    combined = res.stdout + res.stderr
    assert res.returncode != 0, (
        "non-loopback URI must fail the session, not green-pass")
    assert "not loopback" in combined, (
        "the failure must be the locality refusal\n" + combined)
    assert "1 passed" not in combined, "no test may run before the refusal"


def test_expect_uri_without_uri_fails_session():
    """TORTOISE_TEST_EXPECT_URI=1 with no TORTOISE_DB_URI fails the session
    at start — closing the vacuous-pass hole: a CI docker-half job whose URI
    was dropped would otherwise green-pass on the carve-out shape."""
    res = _run_session({"TORTOISE_TEST_EXPECT_URI": "1"})
    combined = res.stdout + res.stderr
    assert res.returncode != 0, (
        "EXPECT_URI without a URI must fail the session, not green-pass")
    assert "TORTOISE_TEST_EXPECT_URI=1 but TORTOISE_DB_URI unset" in combined, (
        "the failure must be the EXPECT_URI enforcement\n" + combined)
    assert "1 passed" not in combined


def test_expect_uri_unsupported_scheme_fails_session():
    """EXPECT_URI=1 with a set-but-unsupported-scheme URI fails the session
    too (VGATE P2-2) — postgres:// or a bare path would otherwise run the
    embedded lane and green-pass on the wrong backend."""
    res = _run_session({
        "TORTOISE_TEST_EXPECT_URI": "1",
        "TORTOISE_DB_URI": "postgres://:pw@db.internal.example.com:5432/x"})
    combined = res.stdout + res.stderr
    assert res.returncode != 0, (
        "EXPECT_URI with an unsupported-scheme URI must fail the session")
    assert "not a supported connection URI" in combined, (
        "the failure must be the EXPECT_URI scheme enforcement\n" + combined)
    assert "1 passed" not in combined


def test_expect_uri_set_session_passes(docker_up):
    """EXPECT_URI=1 + URI set + reachable server → green session (the CI
    docker-half shape)."""
    res = _run_session({
        "TORTOISE_TEST_EXPECT_URI": "1",
        "TORTOISE_DB_URI": "docker://:falkordb@localhost:6379"})
    assert res.returncode == 0, (
        "EXPECT_URI + reachable server session must pass\nstdout:\n" +
        res.stdout + "\nstderr:\n" + res.stderr)
    assert "1 passed" in res.stdout


def test_probe_flips_with_redirect_state(monkeypatch, docker_up):
    """Cycle-5 P1-4: the probe is SENSITIVE to the redirect — same
    construction, redirect armed → server; same construction with the
    documented exemption knob set → embedded. Exercises the REAL inert path
    (the state the tripwire must detect) without monkeypatching the
    redirect's internals."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    probe = _tripwire_probe()
    try:
        assert probe._is_embedded is False  # redirect armed → server mode
    finally:
        # Delete the probe graph ONLY when it is UNJOURNALED. On the
        # URI-unset parent lane the journal env is never exported (conftest
        # exports it at import only under a URI), so the mint would linger
        # until a later docker session's leftover sweep (VGATE P2-1). On
        # the URI-set lane the mint IS journaled and the session-end sweep
        # drops it; deleting it here would leave a journal entry for a
        # missing graph — keep-on-partial retention on servers that raise
        # on missing-graph ops (deep-review Issue 3).
        if not probe._is_embedded \
                and not os.environ.get("TORTOISE_TEST_JOURNAL_FILE"):
            try:  # noqa: SIM105
                probe.db.select_graph(probe.graph_name).delete()
            except Exception:
                pass
        probe.close()
    # inert-redirect state via the REAL exemption path: the caller stem is
    # listed → the redirect does not fire → the probe stays embedded — the
    # exact state the session-start tripwire must fail on
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "test_tripwire")
    probe2 = _tripwire_probe()
    try:
        assert probe2._is_embedded is True
    finally:
        probe2.close()
