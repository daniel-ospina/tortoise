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

from tortoise.projection import FalkorProjection

_HAS_FALKOR: bool | None = None


def has_falkor() -> bool:
    """Runtime probe: is embedded FalkorDBLite usable on this machine?

    Mirrors the historical per-file probes (issue #82 — redislite interprets
    some paths as hostnames → idna UnicodeEncodeError). Centralized so
    converted files do not construct a raw client just to probe.
    """
    global _HAS_FALKOR
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
    """
    if not has_falkor():
        yield None
        return
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    proj = FalkorProjection(db_path, graph_name="test")
    yield proj
    proj.close()
