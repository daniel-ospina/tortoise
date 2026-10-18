"""Live-FalkorDB test utilities (#942).

Plain module on purpose: a function defined in conftest.py is auto-collected
as a pytest FIXTURE and cannot be called directly. This module is not
auto-loaded, so _skip_unless_live_uri stays a plain function importable by
test_event_store.py and test_embedded_concurrency.py.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re

logger = logging.getLogger(__name__)

# #3339: the ONE live-URI skip reason. tools/skip-guard.py keys the
# intentional URI-gate family on this reason's PREFIX, and since #3592 the
# guard's verdict is LANE-AWARE: the family is exempt on a lane that declared
# itself URI-less (or declared nothing) and is a VIOLATION on a lane that
# promised the URI. Anything that needs to skip on a missing TORTOISE_DB_URI
# must still use this constant (directly or via _skip_unless_live_uri below) —
# a hand-written reason is how test_scan_eval_graph_live_smoke redded
# `test (a)` for every PR whose selection landed in the tier-2 URI-less lane.
# An UNDECLARED lane (the carve-out job, a local run) is the case where the
# prefix is still load-bearing, so routing through this constant remains
# required everywhere; the lane awareness only stops a reason from excusing a
# skip the lane had promised to run.
LIVE_URI_SKIP_REASON = (
    "requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job "
    "test-concurrency-falkor)"
)

# ── #6673: the docker-lane service ports are DYNAMIC (not 6379/16379) ──────
#
# The CI services lanes publish their falkordb containers with
# `docker run -p 127.0.0.1:0:6379` and read the assigned host port back, instead
# of the fixed `6379:6379` / `16379:6379` pair. WHY (#6673): every
# self-hosted runner on this host shares ONE Docker daemon, so a fixed host
# port is a single global namespace — when two services jobs overlapped, the
# second container's `docker start` died with "Bind for 0.0.0.0:6379 failed:
# port is already allocated", the RUNNER aborted in its
# `initialize_containers` pre-step, and the job reported a FALSE RED in ~9s
# with zero tests executed (measured: 6 executions across `test (b)`,
# `test-track-b`, `test-concurrency-falkor`). An ephemeral port cannot
# collide, so N runners can run services jobs concurrently.
#
# The provision step (`.github/actions/falkordb-provision`) writes the two
# assigned ports into $GITHUB_ENV. DEFAULTS ARE THE HISTORICAL LITERALS, so
# a local run, a URI-less run, and the GitHub-hosted lanes behave exactly as
# before — this seam is opt-in by env.
#
# This is a TEST-ONLY seam and deliberately NOT the product's
# FALKORDB_HOST/FALKORDB_PORT pair: those are read by
# tortoise/__main__.py::resolve_db_path (default 16379) to resolve a DB
# target when no URI is given, so overloading them would change what
# `tortoise doctor` etc. connect to. FALKORDB_PORT is honoured here only as
# the LEGACY fallback, which is where test_ingest.py / test_projection.py
# already read it from (their pre-#6673 behaviour, preserved).
# FALKORDB_HOST is not read at all — see service_host().
_PORT_ENV = "TORTOISE_TEST_DOCKER_PORT"
_LEGACY_PORT_ENV = "TORTOISE_TEST_LEGACY_PORT"
_HOST_ENV = "TORTOISE_TEST_DOCKER_HOST"


def docker_port() -> int:
    """The passworded docker-lane service's host port (default 6379)."""
    return int(os.environ.get(_PORT_ENV) or "6379")


def legacy_port() -> int:
    """The passwordless legacy service's host port (default 16379).

    Resolution order mirrors the pre-#6673 probes in test_ingest.py /
    test_projection.py. Read at CALL time so a monkeypatched FALKORDB_PORT
    keeps working.
    """
    return int(
        os.environ.get(_LEGACY_PORT_ENV)
        or os.environ.get("FALKORDB_PORT")
        or "16379"
    )


def service_host() -> str:
    """The host the published service ports are reachable on.

    The #6673 var wins; otherwise ``localhost`` — the historical literal every
    client in this lane was written against, and what the remaining
    ``SimpleNamespace(_host="localhost", …)`` fakes model.

    The product's ``FALKORDB_HOST`` is deliberately NOT consulted. Honouring it
    here made the probe follow the override while those clients did not: the
    probe would pass, the docker leg would be selected, and the client would
    dial a dead localhost — the split-brain this seam exists to remove, moved
    to the host axis. A test-only var cannot be half-threaded that way because
    nothing assumes its default.
    """
    return os.environ.get(_HOST_ENV) or "localhost"


# Import-time snapshots — for the module-level URI constants that were
# literals before #6673 (the workflow writes the env before pytest starts, so
# a module-level read is the same value a call-time read would return).
DOCKER_PORT = docker_port()
LEGACY_PORT = legacy_port()
SERVICE_HOST = service_host()


def docker_base_uri(*, password: str | None = "falkordb",
                    host: str | None = None) -> str:
    """The passworded docker-lane URI WITHOUT a graph path.

    The raw-client / `graph_name=`-argument sites spell the URI this way
    (`docker://:falkordb@localhost:6379` + a separate graph name), so the
    graph-less form needs its own accessor.

    `password` covers the three distinct STRING shapes the suite ships:
    `"falkordb"` → `docker://:falkordb@h:p` (the docker lane), `""` →
    `docker://:@h:p` (empty userinfo, the legacy spelling) and `None` → NO
    userinfo at all (`docker://h:p`, how test_integration_search and
    test_hnsw_vector_index spell their local fallback). These parse alike but
    are different strings, so a site that compares them keeps comparing like
    with like.
    """
    userinfo = "" if password is None else f":{password}@"
    return f"docker://{userinfo}{host or service_host()}:{docker_port()}"


def legacy_base_uri(*, password: str | None = "",
                    host: str | None = None) -> str:
    """The passwordless legacy-lane URI WITHOUT a graph path."""
    userinfo = "" if password is None else f":{password}@"
    return f"docker://{userinfo}{host or service_host()}:{legacy_port()}"


def docker_uri(graph: str, *, password: str | None = "falkordb",
               host: str | None = None) -> str:
    """A passworded docker-lane URI for `graph` at the live service port."""
    return f"{docker_base_uri(password=password, host=host)}/{graph}"


def legacy_uri(graph: str, *, password: str | None = "",
               host: str | None = None) -> str:
    """A passwordless legacy-lane URI for `graph` at the live service port."""
    return f"{legacy_base_uri(password=password, host=host)}/{graph}"


def tcp_reachable(port: int, *, host: str | None = None,
                  timeout: float = 1.0) -> bool:
    """Plain TCP-connect probe (the shape every live probe in the suite uses)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host or service_host(), port)) == 0
    except OSError:  # pragma: no cover - connect_ex returns, never raises
        return False
    finally:
        s.close()


def docker_reachable(*, timeout: float = 1.0) -> bool:
    """True when the provisioned passworded service accepts connections."""
    return tcp_reachable(docker_port(), timeout=timeout)


def legacy_reachable(*, timeout: float = 1.0) -> bool:
    """True when the provisioned passwordless legacy service accepts connections."""
    return tcp_reachable(legacy_port(), timeout=timeout)


def _skip_unless_live_uri():
    """Skip a docker:// live-FalkorDB test when no URI is configured (#942).

    Divergence from _skip_if_no_falkor (probe-based, test_projection.py):
    these tests REQUIRE the real server that CI's test-concurrency-falkor job
    provides (TORTOISE_DB_URI set); in every other surface they must skip
    VISIBLY (pytest.skip), never early-return-green — the vacuity pattern
    #942 exists to kill.
    """
    import pytest

    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip(LIVE_URI_SKIP_REASON)


@contextlib.contextmanager
def mask_embedder():
    """Install the no-embedder double over BOTH embedder seams; restore on exit.

    The ONE owner of the mask (#8057). Every docker-lane module in the assembly
    family pins the dense/vector legs OUT — the evidence path there is
    deterministic text, no model, no network — and used to hand-roll the same
    two ``monkeypatch.setattr`` calls in an autouse ``_no_embedder`` fixture.
    A FIXTURE CANNOT REACH A MODULE-LEVEL PROBE: the availability gate runs at
    module IMPORT, before any fixture, so the probe kept driving
    ``create_point`` with the REAL seam. That path derives an embedding
    (``encode_for_store`` -> ``EmbeddingModel.get``), and a cold
    sentence-transformers/torch load is measured at 50-205s (#6960) — paid at
    import, on a lane that only ever needed the FULLTEXT round-trip. Owning the
    mask in one place is the point: the probe and the fixtures cannot drift
    into disagreeing about which seams are pinned.

    Both seams, because either one alone still loads: ``encode_for_store`` calls
    ``compute_embedding`` (module global, the seam the write paths use) and
    ``_encode`` calls ``EmbeddingModel.get`` (the singleton loader). Both
    doubles absorb any signature (``*args, **kwargs``): the write paths call
    ``compute_embedding`` with exactly one positional argument and
    ``EmbeddingModel.get`` with none, but the warm-up leg passes
    ``load_timeout=`` — a narrow double that raised on the wider call would be
    swallowed by the write path's own ``except Exception`` and degrade to the
    silent no-vector path #4280 is about, i.e. the mask would introduce the
    failure it exists to avoid.

    Restores BOTH attributes in a ``finally``, so an exception in the masked
    block cannot leave the process pinned to the double. The saved values are
    read out of ``__dict__``, not via ``getattr``: ``EmbeddingModel.get`` is a
    ``classmethod``, so ``getattr`` hands back a NEW bound-method object on every
    access and restoring that would replace the descriptor with a plain method —
    a functional-looking but structurally degraded seam (subclass override and
    ``get.__func__`` both stop working) for every test after the mask.
    """
    from tortoise import embeddings as _emb

    _cls = _emb.EmbeddingModel
    _saved_compute = _emb.__dict__["compute_embedding"]
    _saved_get = _cls.__dict__["get"]
    _emb.compute_embedding = staticmethod(lambda *a, **k: None)
    _cls.get = staticmethod(lambda *a, **k: None)
    try:
        yield
    finally:
        _emb.compute_embedding = _saved_compute
        _cls.get = _saved_get


# ── #8746: DELETE a per-run test graph, do not merely empty it ─────────────
#
# The prefix gate is the CANONICAL fail-closed set, `_SERVER_WIPE_PREFIXES` in
# `tests/_embedded.py` — IMPORTED, never restated (see `drop_test_graph`).
# Fail-closed: a name outside it is never deleted by this helper, so a fixture
# that resolved a PRODUCT/TENANT (`team_`/`org_`) graph by mistake cannot
# destroy it. That set is the GRAPH.LIST family, which is exactly this helper's
# input shape (a NAME, no journal attribution). The second arm excludes the
# SHARED URI-default graph, which is INSIDE that family but belongs to the whole
# job. CITE SYMBOLS, never line numbers: a line pointer re-stales.
#
# #8746 review round 2 (P3): the tolerated refusals/failures are ACCUMULATED, not
# only logged. `drop_test_graph` is log-and-continue by policy (a teardown must
# not red a suite), but a `logger.warning` from the TEARDOWN OF A PASSING TEST is
# DISCARDED by pytest capture (#7735 / #1103, measured 0 occurrences under the CI
# flags), so a systematic refusal would be invisible — and the per-run leak this
# helper removes would come back unobserved, which is the very defect #8746's own
# test docstring names. `tests/conftest.py::_redislite_hygiene` — the
# lane-INDEPENDENT finalizer, since `_server_graph_hygiene` is URI-gated while
# `live_proj` skips on a TCP probe and therefore also runs URI-less — hands this
# record to `tests/_tmpdir_hygiene.py::write_tolerated_cleanup_report` as an
# EXTRA SECTION, so it rides the `tempdir-hygiene-end.json` artifact that every
# suite-session job ALREADY dumps (pinned by
# `tests/test_tmpdir_hygiene.py::test_suite_jobs_surface_the_tolerated_leak`).
# A second artifact file nothing reads is not an observability channel, and
# adding a dump step is a workflow change (#8746 review r3 P2).
GRAPH_DELETE_EVENTS: list[dict[str, object]] = []

# The MINT SHAPE this tree produces: a per-run graph name ends in a random hex
# suffix, minted as ``f"test_…_{os.urandom(4).hex()}"`` (8) — or, at the site
# #8936 adopts next, ``f"test_subj1423_{uuid.uuid4().hex[:10]}"`` (10). So the
# floor is 8 hex characters, not exactly 8: the width is a CHOICE (wide enough
# that no fixed spelling reaches it), not a measurement of every mint. The
# prefix family is NOT sufficient on its own — it also contains SHARED,
# fixed-name fixture graphs (``tortoise_test_integration_search``,
# ``tortoise_test_fts125``, ``tortoise_test_3301``, ``tortoise_test_proj125``),
# and those belong to the whole job or to another suite. See the third arm in
# `drop_test_graph`.
_PER_RUN_GRAPH_NAME_RE = re.compile(r"^[a-z0-9_]*_[0-9a-f]{8,}$")


def graph_delete_events() -> list[dict[str, object]]:
    """The tolerated ``drop_test_graph`` refusals/failures so far this session."""
    return [dict(entry) for entry in GRAPH_DELETE_EVENTS]


def _restore_graph_delete_events(snapshot: list[dict[str, object]]) -> None:
    """Reset the record to ``snapshot`` — so a test that drives a refusal can
    keep the session artifact honest (the #7735 ``_discard_tolerated_leaks``
    precedent: without it, a synthetic name forks into every CI run's record)."""
    GRAPH_DELETE_EVENTS[:] = [dict(entry) for entry in snapshot]


def _record_delete_event(name: object, event: str, reason: str) -> None:
    GRAPH_DELETE_EVENTS.append({"name": str(name), "event": event,
                                "reason": reason})


def drop_test_graph(proj, *, name: str | None = None) -> bool:
    """DETACH-free ``GRAPH.DELETE`` of a per-run test graph (#8746).

    Why this exists
    ---------------
    Per-run graphs minted as ``f"test_x_{os.urandom(4).hex()}"`` used to be
    emptied in FIXTURE SETUP (``MATCH (n) DETACH DELETE n`` before the
    ``yield``) and closed at teardown — ``close()`` was the WHOLE teardown, so
    the CONTENTS went and the GRAPH OBJECT was never deleted at all, still
    counted by the server's ``CACHE_SIZE``, which is the documented trigger for
    ``graph was deleted or replaced while the query was running`` (#7357).
    #8746 measured 481-586 such leftovers on one shared instance (44% of every
    graph on it).

    The session sweep (``tests/conftest.py::_server_graph_hygiene`` /
    ``tests/_embedded.py::_sweep_drop``) reaps the JOURNALED graphs, and
    ``FalkorProjection.from_uri`` journals in a test frame — so the graphs no
    longer LEAK past a session. But the sweep only runs at session END, so a
    session that uses a per-run fixture hundreds of times still holds hundreds
    of empty graphs for its whole lifetime: the same cache-churn pressure, just
    bounded by the session. Deleting at teardown removes it at the source and
    makes the leak impossible even when the journal is unavailable.

    Why ``detach=False``
    -------------------
    ``GRAPH.DELETE`` removes the graph wholesale — the DETACH is redundant, and
    on a graph that exists with no replayable record it is the *materialising*
    half of the AOF-poisoning sequence in ``tortoise/graph_delete_guard.py``
    (#2961): an implicit create emits no ``GRAPH.EFFECT``, so a later
    ``GRAPH.DELETE`` replays against a key that was never created and the
    server never reaches a serving state again. The presence gate inside
    :func:`~tortoise.graph_delete_guard.safe_graph_delete` is what makes the
    remaining case safe: an ABSENT graph transmits nothing at all.

    Returns True when the graph was present and was deleted, False when it was
    already absent or the name was refused. Never raises: a failed teardown
    must not red a suite, and the session sweep remains the backstop (this is
    the same log-and-continue policy as ``_sweep_drop``).

    The prefix gate imports the canonical ``_SERVER_WIPE_PREFIXES`` rather than
    restating it. A NAMED local copy would sit outside every ``#3634`` drift
    guard (``tests/_embedded.py``'s ONE declaration + ``_DIVERGENCE_REGISTER``,
    ``tests/test_graph_name_ownership.py``'s object-identity tie and its
    named-constant scanner), so if the canonical set were ever NARROWED (as it
    deliberately was in ``#7795``) this helper would keep ``GRAPH.DELETE``-ing a
    family the canonical GRAPH.LIST set no longer authorises — fail-OPEN on an
    irreversible delete. Function-local because this module is deliberately
    light (no ``pytest`` / ``tortoise.projection`` at module scope); the import
    resolves the LIVE module attribute on every call, so it is the canonical
    set of the moment that the gate consults.

    Three arms, all fail-closed (#8746 review round 2 P3, round 5 P2). The
    prefix arm alone is not enough on the axis that matters:

    1. the canonical PREFIX family;
    2. the shared URI-default graph (e.g. ``tortoise_test_matrix``), which is
       INSIDE the family but belongs to the whole job — exactly as every other
       TARGETED delete path in this tree refuses it
       (``tests/_embedded.py::_wipe_or``'s ``scope.discard(default)``,
       ``wipe_server``'s per-test ``g == default_graph`` arm); only the
       session-end sweep owns it;
    3. the per-run MINT SHAPE (``_PER_RUN_GRAPH_NAME_RE``: a ``_<8+ lowercase
       hex>`` suffix). Arm 2 alone was
       inert on the URI-less lane — ``_uri_default_graph_name()`` returns
       ``None`` when ``TORTOISE_DB_URI`` is unset, and ``live_proj`` runs there
       too (it skips on a TCP probe, not on the URI) — while the family also
       holds SHARED fixed-name graphs on the connected server
       (``tortoise_test_integration_search``, ``tortoise_test_fts125``,
       ``tortoise_test_3301``, …), none of them per-run. A fixed/shared name is
       now REFUSED AND RECORDED rather than deleted; the cost is an observable
       non-delete, the alternative is an irreversible delete of another suite's
       graph. The width floor is 8, not exactly 8: this tree mints 8
       (``os.urandom(4).hex()``) and 10 (``uuid4().hex[:10]``) hex characters,
       and the arm must not refuse a mint it was written to protect — a refusal
       here is a silent leak, the defect #8746 exists to remove.

    Residual, stated honestly: the shape arm has no PROVENANCE. It admits any
    family name ending in ``_<8+ lowercase hex>``, so it cannot tell this run's
    mint from another run's — it only excludes the fixed/shared spellings. The
    real callers are what make it exact: every one in this tree mints a random
    hex suffix and passes THAT name (see
    ``tests/test_projection.py::live_proj`` — ``test_proj125_<8 hex>`` is a
    minted per-run name, not a shared one — and
    ``test_add_document_journals_the_anchor_and_replays_identically``).
    """
    gname = name if name is not None else getattr(proj, "graph_name", None)
    # The gate is inside a try of its OWN, because the "never raises" contract
    # on this helper covers the gate too: every caller reaches here from a
    # fixture ``finally:``, so a raise would convert a PASSING test into a
    # teardown ERROR. The gate has a real raise vector —
    # ``_uri_default_graph_name()`` parses ``TORTOISE_DB_URI`` with ``urlparse``,
    # which raises ``ValueError`` on a malformed value (a bracketed host such as
    # ``docker://[::1``) — and the answer decides whether an irreversible
    # GRAPH.DELETE runs. A gate failure establishes no reason to delete, so it
    # fails CLOSED: nothing is transmitted, and the refusal is RECORDED (not
    # only logged) so a systematic gate failure cannot hide in a passing
    # test's discarded teardown output.
    try:
        from tests._embedded import (
            _SERVER_WIPE_PREFIXES,
            _uri_default_graph_name,
        )

        reason = None
        if not isinstance(gname, str) or not gname.startswith(_SERVER_WIPE_PREFIXES):
            reason = (f"not a test-owned graph name (prefix gate: "
                      f"{_SERVER_WIPE_PREFIXES})")
        elif gname == _uri_default_graph_name():
            reason = ("the SHARED URI-default graph — the whole job writes to it; "
                      "only the session-end sweep may drop it")
        elif not _PER_RUN_GRAPH_NAME_RE.match(gname):
            reason = ("not a per-run mint shape (_<8+ hex> suffix) — the family "
                      "also holds SHARED fixed-name fixture graphs, and this helper "
                      "may delete only the graph its own run minted")
        if reason is not None:
            logger.warning(
                "[#8746] refusing to GRAPH.DELETE %r — %s; left untouched",
                gname, reason)
            _record_delete_event(gname, "refused", reason)
            return False
    except Exception as exc:  # the gate must not raise either
        logger.warning(
            "[#8746] the delete gate itself raised (%r) on %r — nothing was "
            "transmitted; left for the session sweep", exc, gname)
        _record_delete_event(gname, "failed", f"gate: {exc!r}")
        return False
    try:
        from tortoise.graph_delete_guard import graph_exists, safe_graph_delete

        if safe_graph_delete(proj.db, gname, detach=False, drop=True):
            return True
        # A False return is AMBIGUOUS by the guard's OWN contract — "already
        # absent" or "a lock refusal, nothing transmitted" — and the caller is
        # the one that must re-check presence (`tests/_embedded.py`'s
        # `_drop_one_graph` and `_sweep_proj` are the precedents). Without the
        # re-check a lock refusal is a SILENT leak, and it is the LIKELIEST
        # refusal on a shared instance (a peer holds
        # `tortoise:graph-delete-lock`): one empty graph per run left in
        # `CACHE_SIZE` until the session sweep, i.e. exactly the #7357 churn
        # this helper exists to remove. Cost is one GRAPH.LIST on a path that
        # already did one (#8746 review r3 P3).
        if graph_exists(proj.db, gname):
            _record_delete_event(
                gname, "refused",
                "the guard refused (cross-process delete lock unavailable) and "
                "the graph is still present")
        return False
    except Exception as exc:  # hygiene never fails the suite
        logger.warning(
            "[#8746] GRAPH.DELETE of %r failed (%r) — left for the session "
            "sweep, which owns every journaled graph", gname, exc)
        _record_delete_event(gname, "failed", repr(exc))
        return False
