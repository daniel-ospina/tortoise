"""Centralized session-shared embedded construction (#1012).

All `FalkorProjection(` / `FalkorDB(` construction for converted test files
lives HERE so those files contain zero raw constructions — one session-scoped
projection (ONE redislite server) serves the whole suite instead of one
server per test (the recurring #1005 leak driver).

Hermeticity comes from per-test `wipe()`, not per-test fresh paths — the
same pattern the shared_embedded_db users (test_ranking.py,
test_session_semantic_search.py) already follow.

Not converted here (deliberately): raw-layer tests whose construction IS the
test input (guard, hard-reject, reaper, chaos, lifecycle-close, config path
resolution, migrations, backup restore, flip-gate script integration) — see
RAW_EMBEDDED_ALLOWLIST in test_embedded_lifecycle.py.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from tortoise.config import is_db_uri
from tortoise.projection import FalkorProjection

# Epic #1647 (plan-review P0-1): in-repo carve-out exemption list — the
# caller TEST-MODULE stems exempted from the URI-aware redirect via
# TORTOISE_TEST_NO_REDIRECT (conftest exports it from this constant so the
# product-side redirect reads the list without importing tests/). Exemption
# keys on the CALLER test module (frame-identified by _caller_test_stem),
# NEVER on the DB-file basename. The list is wired in Task 5; empty in P1
# (URI unset everywhere → the redirect is dormant).
TEST_NO_REDIRECT_STEMS: tuple[str, ...] = ()

_HAS_FALKOR: bool | None = None


def _uri_set_supported() -> bool:
    """Epic #1647: True when a supported TORTOISE_DB_URI is set.

    The seam-fixture URI branch predicate (plan-review P2-16): under a
    supported URI the redirect flips embedded constructions to the server,
    so the fixtures construct server-mode directly and has_falkor()
    short-circuits (the embedded probe would redirect, mint a test graph on
    the server, and misreport backend availability).
    """
    return is_db_uri(os.environ.get("TORTOISE_DB_URI"))


def has_falkor() -> bool:
    """Runtime probe: is embedded FalkorDBLite usable on this machine?

    Mirrors the historical per-file probes (issue #82 — redislite interprets
    some paths as hostnames → idna UnicodeEncodeError). Centralized so
    converted files do not construct a raw client just to probe.

    Epic #1647 (P2-16): under a supported TORTOISE_DB_URI the probe is
    SKIPPED and True is returned immediately — the probe would construct a
    projection, which would redirect (calling test frame + URI + TEST_MODE)
    and mint a derived test_<stem>_<hash> graph on the server while
    misreporting backend availability. Under URI the server IS the backend,
    so skip_if_no_falkor() returns False and migrated files never
    vacuous-return.
    """
    global _HAS_FALKOR
    if _uri_set_supported():
        return True
    if _HAS_FALKOR is None:
        try:
            from redislite.falkordb_client import FalkorDB  # noqa: F401
            db_path = os.path.join(
                tempfile.mkdtemp(prefix="tortoise_probe_"), "probe.db")
            proj = FalkorProjection(db_path, graph_name="test")
            proj.close()
            _HAS_FALKOR = True
        except Exception:
            _HAS_FALKOR = False
    return _HAS_FALKOR


def skip_if_no_falkor() -> bool:
    """True when embedded FalkorDBLite is unavailable (callers return early,
    preserving the historical vacuous-pass behavior)."""
    return not has_falkor()


def wipe(proj) -> None:
    """Wipe every graph in the shared embedded DB (hermeticity per test).

    EMBEDDED-ONLY: refuses to run against a server-mode (Docker/cloud)
    projection — this helper targets the session-shared TEST server, never a
    live registry. The shared DB serves multiple graphs
    (registry_control_plane, team_*), not just the projection's default, so
    ALL graphs are cleared. _GuardedGraph's test-graph assertion is NOT
    consulted here (raw db handle; and it returns early for embedded mode) —
    the embedded guard below is the actual protection.
    """
    if not getattr(proj, "_is_embedded", False):
        raise RuntimeError(
            "wipe() is for the session-shared EMBEDDED test server only — "
            "refusing to wipe a server-mode projection"
        )
    try:
        graphs = list(proj.db.list_graphs())
    except Exception:
        graphs = []  # list_graphs unknown on this backend — fall back below
    if not graphs:
        # list_graphs failure would silently narrow the wipe; the embedded
        # backend enumerates graphs reliably, so fall back to the projection
        # default and let the next test's exact-set assertions surface any
        # leak loudly.
        graphs = [getattr(proj, "_graph_name", "test")]
    for g in graphs:
        try:  # noqa: SIM105
            proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        except Exception:
            pass


@pytest.fixture(scope="session")
def shared_proj():
    """One session-scoped embedded projection (#1012).

    Replaces per-test `FalkorProjection(fresh-tmp-path)` construction in
    converted files: ONE redislite server for the whole session instead of
    one per test. Yields None when embedded mode is unavailable so callers
    keep the historical skip semantics (`if shared_proj is None: return`).

    Epic #1647 (D-1=A): URI-aware seam — when a supported TORTOISE_DB_URI is
    set, construct server-mode via from_uri with a guard-passing shared-tier
    graph name (test_suite_<uuid>) instead; the URI branch runs BEFORE
    has_falkor() (P2-16: the probe would redirect and mint a server graph).
    Unset → today's embedded construction unchanged (P1 zero-change).
    """
    if _uri_set_supported():
        proj = FalkorProjection.from_uri(
            os.environ["TORTOISE_DB_URI"],
            graph_name=f"test_suite_{os.urandom(4).hex()}")
        yield proj
        proj.close()
        return
    if not has_falkor():
        yield None
        return
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    proj = FalkorProjection(db_path, graph_name="test")
    yield proj
    proj.close()
