# tests/test_loopback_predicate_single_source.py
"""Epic #1647 cycle-7 P1-3: tortoise.config.is_loopback_uri is the SINGLE
shared loopback predicate (created in Task 1) and projection._is_loopback_host
DELEGATES to the same LOOPBACK_HOSTS constant. Both modules must resolve the
SAME host set — a divergence would let the redirect accept a host the Task 4
tripwire refuses (or vice versa), breaking the fail-before-first-write chain.

Sibling coverage lives inside tests/test_redirect_seam.py
(test_loopback_predicate_single_source); this dedicated file keeps the pin
independently runnable (single-source checks are cheap and standalone)."""
from tortoise.config import LOOPBACK_HOSTS, is_loopback_uri
from tortoise.projection import _is_loopback_host


def test_loopback_predicate_single_source():
    for host in ("localhost", "127.0.0.1", "::1"):
        assert host in LOOPBACK_HOSTS
        assert _is_loopback_host(host) is True
        # Divergence note (epic #1647 Task 1 impl): a BARE IPv6 literal
        # (docker://:pw@::1:6379) does not parse — urlparse yields hostname
        # None (RFC 3986 requires brackets), so the URI is built with the
        # bracketed [::1] form to actually exercise the ::1 hostname.
        _host_form = f"[{host}]" if ":" in host else host
        assert is_loopback_uri(f"docker://:pw@{_host_form}:6379") is True
    for host in ("db.internal.example.com", "falkor.prod.internal"):
        assert _is_loopback_host(host) is False
        assert is_loopback_uri(f"docker://:pw@{host}:6379") is False
    # Cycle-8 P2-1: HOSTLESS URIs — the single predicate must refuse them the
    # SAME way in both modules. The redirect previously accepted
    # `hostname or "localhost"` while is_loopback_uri refused (absent
    # hostname → not loopback) — a divergence that let the redirect mint
    # graphs while the session-start tripwire said non-loopback.
    assert is_loopback_uri("docker://:pw@:6379") is False, \
        "hostless URI is not loopback (absent hostname, fail-closed)"
    assert _is_loopback_host(None) is False, \
        "None host is not loopback — the redirect's hostless path must refuse"
    assert is_loopback_uri("docker://:falkordb@localhost:6379") is True
