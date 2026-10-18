"""#3065 — every product FalkorDB client carries a BOUNDED socket read.

Follow-up to #2969. ``falkordb.FalkorDB.__init__`` defaults
``socket_timeout=None`` and passes it to ``redis.Redis`` explicitly, which
OVERRIDES redis-py's own ``DEFAULT_SOCKET_TIMEOUT`` — so a raw client built
without the kwarg is not "5s by default", it is **unbounded**. A server-side
stall (the #2969 active-defrag loop, #2838's MERGE lock) then parks the caller
in ``recv()`` forever: the process is alive, 0% CPU, no error and no log. The
raw clients in the package that carried no bound (``session_indexer``'s
graph-entity lookup) or knob-blind literals (``backup``'s BGSAVE, the ``doctor``
probe) each re-derived their own socket policy, so ``TORTOISE_DB_SOCKET_TIMEOUT``
/ ``TORTOISE_DB_SOCKET_CONNECT_TIMEOUT`` reached the projection's client only.

This file pins the remedy: ONE resolution point
(``projection._bounded_server_socket_kwargs``), every product construction site
routed through it, and — the systemic half — an AST guard so the NEXT raw client
cannot be added without a read bound.

#8118 extends that guard over the TOOL surfaces (``tools/``, ``graph-scripts/``,
``apps/``), where the identical defect survived because #3065's scan scope was
deliberately the product package: nine more unbounded constructions, and four of
them were not named in the issue body until the tree was re-measured. The scan
roots are PINNED, so widening the guard is a visible edit rather than a quiet
side effect.

Tests:
  1. Resolver semantics (bounded default, per-lane knobs, the explicit
     ``none`` opt-out preserved).
  2. Wiring: the indexer, BGSAVE and doctor-probe clients actually RECEIVE the
     bound, and follow the knobs.
  3. The bound is real: a resolved read timeout interrupts a silent peer's
     stalled read, and an ABSENT one parks the caller (the #2969 shape).
  4. The AST guard + its anti-vacuity inventory.

Measured before the fix (pinned by hand, on the pinned falkordb build):
``session_indexer``'s client had ``socket_timeout=None`` AND
``socket_connect_timeout=None``; the resolved policy is ``10.0`` / ``2.0``.
"""

from __future__ import annotations

import ast
import contextlib
import socket
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PKG = REPO_ROOT / "tortoise"

# The construction sites in the product package, pinned explicitly. A NEW site
# reds this guard twice over: its file is absent from the map, or its count
# differs from the pinned number. That is the point — an inventory that can
# drift silently is an inventory that has already drifted (#1349/#3616 class).
_EXPECTED_SITES = {
    "tortoise/__main__.py": 1,           # the doctor graph probe
    "tortoise/backup.py": 1,             # BGSAVE
    "tortoise/projection/__init__.py": 2,  # embedded + server branches
    "tortoise/session_indexer.py": 1,    # graph-entity keyword lookup
    "tortoise/acl_graph_users.py": 1,    # the ACL admin client
    "tortoise/hosted_backup.py": 1,      # the restore-swap copy client
}

#: Sites whose read bound lives OUTSIDE the constructor call, each pinned to the
#: test that FAILS if the bound is removed. An entry here is a CLAIM, not a
#: waiver: #3065's defect was an unbounded ``recv()``, so a site may only be
#: listed alongside something that proves its bound exists. The inventory above
#: still pins these files, so a SECOND client in one of them reds on the count.
_BOUND_LIVES_ELSEWHERE = {
    # ``redis.Redis(connection_pool=ConnectionPool(**kwargs))``, built with
    # ``kwargs["socket_timeout"] = _restore_swap_timeout_s()`` (#3813) and NO
    # retry. The bound belongs to the POOL, so the call node cannot show it.
    "tortoise/hosted_backup.py":
        "tests/test_dr_endpoints.py::TestRestoreSwapReadBound",
    # #8118: the viz server's boot client is ``FalkorDB(**kwargs)`` where
    # ``kwargs`` comes from ``apps/graph-viz/server/connection.py::
    # build_connection_kwargs()`` — a pure env→kwargs builder with its own unit
    # test, deliberately bounded THERE (the builder is the seam a test can
    # observe without booting a connection). The proving test lives in this
    # file so an exemption can never point at the very builder it is excusing.
    "apps/graph-viz/server/main.py":
        "tests/test_bounded_raw_clients_3065.py"
        "::test_the_graph_viz_kwargs_builder_carries_the_bounded_read",
}


# ═══════════════════════════════════════════════════════════════════════════
# 1. The one resolution point
# ═══════════════════════════════════════════════════════════════════════════

def _socket_pair(kwargs: dict) -> dict:
    """The resolver's SOCKET half, so the pinned pairs below stay about the
    bound while the retry policy is pinned separately (#2969)."""
    return {k: v for k, v in kwargs.items() if k != "retry"}


def test_the_resolver_defaults_to_a_bounded_read_and_connect():
    """Both keys must be PRESENT and positive.

    ``falkordb`` defaults ``socket_timeout=None`` and hands it to redis-py
    explicitly, so an ABSENT key is the unbounded bug — this asserts the key
    exists and is a positive number, not merely "not None by accident".
    """
    from tortoise.projection import (
        _DB_CONNECT_TIMEOUT_DEFAULT,
        _DB_SOCKET_TIMEOUT_DEFAULT,
        _bounded_server_socket_kwargs,
    )

    kwargs = _bounded_server_socket_kwargs()
    assert _socket_pair(kwargs) == {
        "socket_connect_timeout": _DB_CONNECT_TIMEOUT_DEFAULT,
        "socket_timeout": _DB_SOCKET_TIMEOUT_DEFAULT,
    }
    assert kwargs["socket_timeout"] > 0, "an absent/None read bound IS the #3065 hang"
    assert kwargs["socket_connect_timeout"] > 0
    # #2850's budget rule, restated here because a raw client is where it is
    # easiest to lose: the connect bound must beat a 5s health-check budget.
    assert kwargs["socket_connect_timeout"] < 5.0


def test_the_resolver_follows_the_per_lane_knobs(monkeypatch):
    """The #2969 knobs are the reason the resolver exists — they must WIN."""
    from tortoise.projection import _bounded_server_socket_kwargs

    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "3")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "0.5")

    assert _socket_pair(_bounded_server_socket_kwargs()) == {
        "socket_connect_timeout": 0.5,
        "socket_timeout": 3.0,
    }


def test_the_resolver_keeps_the_explicit_unbounded_opt_out(monkeypatch):
    """``none`` is the DOCUMENTED escape hatch — unifying the sites must not
    quietly turn an operator's explicit opt-out into a bound, nor a bound into
    an opt-out."""
    from tortoise.projection import _bounded_server_socket_kwargs

    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "none")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "off")

    assert _socket_pair(_bounded_server_socket_kwargs()) == {
        "socket_connect_timeout": None,
        "socket_timeout": None,
    }
    # #2969: the retry policy is INDEPENDENT of the bound's opt-out. Disabling
    # the bound must not also change the multiplier, or the two knobs would
    # interact in a way no call site can see. ZERO retries is the policy: the
    # bound is the wait (``_no_server_retry``), whether or not it is set.
    assert _bounded_server_socket_kwargs()["retry"]._retries == 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. Wiring — the bound reaches each product client
# ═══════════════════════════════════════════════════════════════════════════

def _recording_falkordb(captured: list[dict], error: Exception | None = None):
    """A ``falkordb.FalkorDB`` stand-in recording its ctor kwargs.

    Returns a CLASS (not a Mock) so ``cypher_guard`` takes its real subclassing
    path and the kwargs recorded are the ones the vendor constructor receives.
    """

    class _Result:
        result_set = (("SiblingParser",), ("UnrelatedThing",))

    class _Graph:
        def query(self, cypher, *args, **kwargs):
            if error is not None:
                raise error
            return _Result()

    class _Connection:
        """The vendor client's command channel (``.connection``)."""

        def __init__(self, commands: list):
            self.commands = commands

        def execute_command(self, *args, **kwargs):
            self.commands.append(args[0])

    class _Recorder:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.commands: list = []
            self.connection = _Connection(self.commands)
            captured.append(kwargs)

        def select_graph(self, name):
            return _Graph()

    return _Recorder


def _indexer_kwargs(monkeypatch) -> dict:
    """Run one real ``_graph_entity_keywords`` pass; return the client kwargs."""
    import falkordb

    from tortoise import session_indexer as si

    captured: list[dict] = []
    # The client is a process-wide cache — without this reset the previous
    # test's handle is reused and nothing is constructed (a vacuous pass).
    monkeypatch.setattr(si, "_graph_db", None)
    monkeypatch.setattr(si, "_graph_warned", False)
    monkeypatch.setattr(falkordb, "FalkorDB", _recording_falkordb(captured))
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@db.example.com:6379/tortoise")

    assert si._graph_entity_keywords("SiblingParser is discussed here") == [
        "SiblingParser"]
    assert len(captured) == 1, "exactly one client must be constructed"
    return captured[0]


def test_session_indexer_client_carries_the_bounded_read(monkeypatch):
    """#3065: this site passed NO bound — ``socket_timeout=None`` on the wire."""
    kwargs = _indexer_kwargs(monkeypatch)

    assert kwargs["socket_timeout"] is not None, (
        "an absent read bound is the unbounded recv() #3065 exists to kill")
    assert kwargs["socket_timeout"] > 0
    assert kwargs["socket_connect_timeout"] is not None
    assert kwargs["socket_connect_timeout"] > 0


def test_session_indexer_client_follows_the_knobs(monkeypatch):
    """The point of the shared resolver: the product knob reaches THIS client."""
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "3")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "0.5")

    kwargs = _indexer_kwargs(monkeypatch)

    assert kwargs["socket_timeout"] == 3.0
    assert kwargs["socket_connect_timeout"] == 0.5


def _bgsave_client_kwargs(monkeypatch, captured: list[dict]) -> dict:
    from tortoise.backup import _bgsave

    monkeypatch.setattr("falkordb.FalkorDB", _recording_falkordb(captured))

    assert _bgsave("docker://:pw@snapshot.example.com:6379/tortoise") == "ok"
    assert len(captured) == 1
    return captured[0]


def test_bgsave_client_carries_the_bounded_read(monkeypatch):
    """#3065: this site's 5s/10s literals were knob-BLIND, not absent.

    Bounded already — so this test is about the SECOND half of the defect: an
    operator's ``TORTOISE_DB_SOCKET_TIMEOUT`` has to reach the snapshot path
    too, or the product has one knob per client.
    """
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "7")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "1")

    captured: list[dict] = []
    kwargs = _bgsave_client_kwargs(monkeypatch, captured)

    assert kwargs["socket_timeout"] == 7.0, "the knob must reach BGSAVE"
    assert kwargs["socket_connect_timeout"] == 1.0


def test_bgsave_still_dials_the_uri_and_still_issues_the_bgsave(monkeypatch):
    """The recorder is exercised for real, so the kwargs assertions above
    cannot pass against a client the BGSAVE path never actually used.
    """
    captured: list[dict] = []
    _bgsave_client_kwargs(monkeypatch, captured)

    assert captured[0]["host"] == "snapshot.example.com"
    assert captured[0]["port"] == 6379


def test_doctor_probe_client_carries_the_bounded_read(monkeypatch, capsys):
    """#3065: the probe's literals were knob-blind too.

    ``doctor`` is the tool an operator reaches for WHEN the graph is wedged, so
    it is the worst place to park on a bound nobody configured.
    """
    import argparse

    from tortoise.__main__ import _cmd_doctor

    captured: list[dict] = []
    monkeypatch.setattr("falkordb.FalkorDB", _recording_falkordb(captured))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "4")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "1")

    _cmd_doctor(argparse.Namespace(
        cmd="doctor", db="docker://:pw@probe.example.com:6379/tortoise",
        path=None))
    capsys.readouterr()

    assert captured, "the doctor graph probe must construct a client"
    assert captured[0]["socket_timeout"] == 4.0
    assert captured[0]["socket_connect_timeout"] == 1.0


# ═══════════════════════════════════════════════════════════════════════════
# 3. The bound is REAL — it interrupts a stalled read
# ═══════════════════════════════════════════════════════════════════════════

def _silent_peer() -> tuple[socket.socket, list, int]:
    """A listening socket that accepts and then says nothing (#2969's shape)."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    held: list = []

    def accept_and_say_nothing():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            held.append(conn)   # kept open, never written to

    threading.Thread(target=accept_and_say_nothing, daemon=True).start()
    return server, held, server.getsockname()[1]


def test_the_resolved_read_bound_interrupts_a_stalled_read(monkeypatch):
    """The end-to-end proof, on a real socket: with the resolved bound the read
    raises ``TimeoutError`` AT the bound; without one it never returns.

    This is the measurement #3065's evidence rests on — a client that is only
    *configured* with a bound proves nothing until a silent peer is timed
    against it.
    """
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "1")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "2")
    from tortoise.projection import _bounded_server_socket_kwargs

    server, held, port = _silent_peer()
    try:
        # The resolver already pins `Retry(NoBackoff(), 0)` (#2969), which IS
        # the policy this test wants: ZERO retries, so the caller waits exactly
        # one `socket_timeout` and this stays a measurement of the SOCKET BOUND
        # rather than of a multiplier. `retry` is popped from the spread and
        # passed explicitly ONCE — not to override a policy, but because
        # spreading the dict AND passing `retry=` would raise `TypeError: got
        # multiple values for keyword argument 'retry'`. Passing it explicitly
        # keeps the subject bound-only: if the resolver's retry policy ever
        # changes, this test still measures one bound.
        kwargs = {k: v for k, v in _bounded_server_socket_kwargs().items()
                  if k != "retry"}
        client = redis.Redis(host="127.0.0.1", port=port,
                             retry=Retry(NoBackoff(), 0),
                             **kwargs)
        started = time.monotonic()
        with pytest.raises(redis.exceptions.TimeoutError):
            client.execute_command("PING")
        elapsed = time.monotonic() - started
        # Both ends: a fast-fail would be << the bound (something other than
        # the read timed out), and a hang would exceed it.
        assert 0.5 <= elapsed < 3.0, f"bound not consumed as a bound: {elapsed:.2f}s"
    finally:
        for conn in held:
            conn.close()
        server.close()


def test_an_absent_read_bound_parks_the_caller_in_recv():
    """The falsification half: ``socket_timeout=None`` — what the indexer passed
    before this fix — does NOT return, which is why the defect was invisible.

    Bounded to a 0.6s observation so the test itself cannot hang; the peer can
    never answer, so "still running" is the only possible outcome.
    """
    import redis

    server, held, port = _silent_peer()
    try:
        client = redis.Redis(host="127.0.0.1", port=port,
                             socket_timeout=None, socket_connect_timeout=2)
        done = threading.Event()

        def _read_forever():
            # The read is EXPECTED never to return; whatever the eventual
            # cleanup raises (the peer closing) is not the assertion's subject.
            with contextlib.suppress(Exception):
                client.execute_command("PING")
            done.set()

        threading.Thread(target=_read_forever, daemon=True).start()
        assert not done.wait(0.6), (
            "an unbounded read returned against a silent peer — the harness "
            "cannot distinguish bounded from unbounded")
    finally:
        for conn in held:
            conn.close()
        server.close()


# ═══════════════════════════════════════════════════════════════════════════
# 4. The systemic half — no product client may skip the bound
# ═══════════════════════════════════════════════════════════════════════════

#: The ONE declaration of which callables are client constructions. The #3065
#: read-bound scan and the #6882 guard-seam scan both derive from it, so a name
#: added for one verdict cannot drift out of the other.
_GUARD_SEAM = "guarded_client"
_FALKORDB_CLIENT_NAMES = frozenset({"FalkorDB", _GUARD_SEAM})

#: Class/function names that reach the socket. Kept in one place so the
#: predicate, the alias scan and the negative controls cannot drift apart.
_CLIENT_NAMES = _FALKORDB_CLIENT_NAMES | {"Redis", "StrictRedis", "from_url"}

#: Modules whose ``from … import Redis as R`` makes a construction take a name
#: the predicate would otherwise never see. ``tortoise`` is included because it is
#: the real home of the guard seam (``from tortoise.cypher_guard import
#: guarded_client as G``) — without it that alias is unresolvable and the control
#: for it passes vacuously.
_CLIENT_MODULES = {"redis", "falkordb", "tortoise"}


#: The production constructors that MUST route through the #3595 guard seam
#: (#6882). Pinned explicitly: a new guarded site, or a revert to a raw
#: ``FalkorDB(...)``, changes this map or adds an offender.
_EXPECTED_GUARD_SEAM_SITES = {
    "tortoise/__main__.py": 1,             # the doctor graph probe
    "tortoise/backup.py": 1,               # BGSAVE
    "tortoise/projection/__init__.py": 2,  # embedded + server branches
    "tortoise/session_indexer.py": 1,      # graph-entity keyword lookup
}


def _client_aliases(tree: ast.AST) -> dict[str, str]:
    """Local name → the client name it aliases through an ``as`` import.

    A name-based predicate is blind to ``from redis import Redis as R`` — so
    ``R(host=…)`` reached the socket and matched nothing, neither offender nor
    inventory. MEASURED on this branch. The alias map is collected per module
    and threaded into the predicate so an import cannot hide a client.

    A MAP, not a set: the guard-seam verdict (#6882) must know WHICH name an
    alias resolves to (``guarded_client`` or a raw ``FalkorDB``), and a bare set
    cannot answer that.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if (node.module or "").split(".")[0] not in _CLIENT_MODULES:
            continue
        for a in node.names:
            if a.name in _CLIENT_NAMES and a.asname:
                aliases[a.asname] = a.name
    return aliases


def _client_verb(node: ast.Call, aliases: dict[str, str] | None = None) -> str | None:
    """The recognised client name *node* constructs, else ``None``.

    The ONE predicate behind both systemic guards: it resolves attribute forms
    (``falkordb.FalkorDB``), plain names, and ``as`` aliases, so
    :func:`_constructs_a_client` (has a read bound) and the guard-seam scan
    (#6882: did NOT skip the guard seam) can never disagree about what counts as
    a construction.
    """
    func = node.func
    if isinstance(func, ast.Name):
        if aliases and func.id in aliases:
            return aliases[func.id]
        return func.id if func.id in _CLIENT_NAMES else None
    if isinstance(func, ast.Attribute):
        return func.attr if func.attr in _CLIENT_NAMES else None
    return None


def _constructs_a_client(node: ast.Call, aliases: dict[str, str] | None = None) -> bool:
    """True for a raw client construction, whatever its class is called.

    Three class shapes reach the same socket: the falkordb wrapper
    (``FalkorDB(...)``), the package's guard seam (``guarded_client(...)``),
    and a BARE redis client (``redis.Redis(...)`` / ``redis.StrictRedis(...)``
    / ``redis.from_url(...)``) — plus an ``as``-aliased import of any of them.
    Recognising only the first two is a blind spot that was MEASURED, not
    assumed: a bare unbounded ``redis.Redis`` added to ``tortoise/`` left this
    guard green, because no predicate matched it and the inventory therefore
    never changed (#3065 follow-up).

    COVERAGE, stated so this guard does not over-claim: it recognises those
    call shapes and aliases. A construction reached through indirection this
    scan cannot follow — a factory that returns a client, a name bound at
    runtime — is NOT counted, and is what the `_EXPECTED_SITES` inventory's
    explicit-edit requirement is there to make someone look at.
    """
    return _client_verb(node, aliases) is not None


def _carries_a_read_bound(node: ast.Call) -> bool:
    """A bound is present as ``socket_timeout=…`` or as a ``**`` spread of the
    shared resolver (``**_bounded_server_socket_kwargs()``).

    ``socket_timeout=None`` is NOT a bound — it IS the #3065 bug written down.
    ``falkordb.FalkorDB.__init__`` defaults it to ``None`` and hands that to
    redis-py explicitly, so an explicit ``None`` is the same unbounded
    ``recv()`` that a missing kwarg is, and accepting it would let the exact
    forbidden shape ship green. Only the LITERAL is rejected: a name may
    resolve to a real number at runtime, and this is a static scan.
    """
    for kw in node.keywords:
        if kw.arg == "socket_timeout":
            return not (isinstance(kw.value, ast.Constant)
                        and kw.value.value is None)
        if kw.arg is None and "socket_kwargs" in ast.unparse(kw.value):
            return True
    return False


def test_every_product_client_is_constructed_with_a_read_bound():
    """#3065's systemic pin: a product client cannot ship without a read bound.

    The defect was not "two call sites forgot a kwarg" — it was that each site
    re-derived its own socket policy, so adding a site silently added a hole.
    This guard closes that class for the construction shapes it recognises
    (see ``_constructs_a_client`` for what those are, and what it does NOT
    follow): a recognised construction in ``tortoise/`` carries a
    ``socket_timeout`` or the shared resolver, and the site inventory is pinned
    so a new one must be considered deliberately.
    """
    found: dict[str, int] = {}
    offenders: list[str] = []
    for path in sorted(PRODUCT_PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        aliases = _client_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _constructs_a_client(node, aliases):
                continue
            found[rel] = found.get(rel, 0) + 1
            if (not _carries_a_read_bound(node)
                    and rel not in _BOUND_LIVES_ELSEWHERE):
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "FalkorDB client(s) constructed with no read bound — an unbounded "
        "recv() that a stalled server parks forever (#3065). Pass "
        "**_bounded_server_socket_kwargs() from tortoise.projection, or an "
        f"explicit socket_timeout: {offenders}")

    # Anti-vacuity: a scan that found nothing would pass the assert above
    # while guarding nothing (the #7053 class), and a NEW site must be an
    # explicit inventory edit rather than a silent addition.
    assert found == _EXPECTED_SITES, (
        "the client-construction inventory changed — a new raw client must be "
        "listed (and carry a bound) rather than silently joining the package: "
        f"found={found} expected={_EXPECTED_SITES}")


def test_every_product_falkordb_client_goes_through_the_guard_seam():
    """#6882: a raw ``FalkorDB(...)`` in the package reds — the wiring is pinned.

    The defect this prevents is MEASURED, not hypothetical: reverting
    ``guarded_client(FalkorDB, …)`` to a raw ``FalkorDB(…)`` at BOTH projection
    sites left the whole #3595 guard suite green (34 tests collected, 0 red),
    silently unguarding ``proj.db.select_graph(...)`` — the dominant path (SDK
    registry handles, ``hosted_api``, ``backup_sweep``, ``session_indexer``, the
    doctor probe). The read-bound scan above cannot catch it:
    :func:`_constructs_a_client` treats ``guarded_client(…)`` and ``FalkorDB(…)``
    as the SAME construction (both must carry a bound), so the seam itself was
    never asserted.
    """
    seam: dict[str, int] = {}
    raw: list[str] = []
    for path in sorted(PRODUCT_PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _client_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            verb = _client_verb(node, aliases)
            if verb not in _FALKORDB_CLIENT_NAMES:
                continue
            if verb == _GUARD_SEAM:
                seam[rel] = seam.get(rel, 0) + 1
            else:
                raw.append(f"{rel}:{node.lineno}")

    assert not raw, (
        "FalkorDB client(s) constructed WITHOUT the #3595 guard seam — a raw "
        "FalkorDB(...) unguards every handle it yields (proj.db.select_graph, "
        "hosted_api, backup_sweep, session_indexer, the doctor probe) while the "
        "guard suite stays green. Build it with `guarded_client(FalkorDB, …)` "
        f"from tortoise.cypher_guard: {raw}")

    # Anti-vacuity + explicit inventory: a scan that found nothing would pass
    # the assert above while guarding nothing, and a NEW seam site must be a
    # deliberate inventory edit rather than a silent addition.
    assert seam == _EXPECTED_GUARD_SEAM_SITES, (
        "the guard-seam inventory changed — a new construction must be listed "
        f"(and routed through the seam): found={seam} "
        f"expected={_EXPECTED_GUARD_SEAM_SITES}")

    # Parity pin against the #3065 inventory: every seam site is also a
    # recognised construction site, so one edit cannot satisfy one scan while
    # silently skipping the other.
    for rel, count in _EXPECTED_GUARD_SEAM_SITES.items():
        assert _EXPECTED_SITES.get(rel, 0) >= count, (
            f"{rel} carries a guard-seam construction but is not counted in "
            "_EXPECTED_SITES — the two inventories have drifted")


def test_the_guard_itself_reads_the_shapes_it_claims_to():
    """Negative controls for the AST guard, asserted directly.

    Without these, a guard that recognises nothing passes the scan above for
    the wrong reason.
    """
    def _only_call(src: str) -> ast.Call:
        stmt = ast.parse(src).body[0]
        assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call), src
        return stmt.value

    assert _constructs_a_client(_only_call("FalkorDB(host='h', port=1)"))
    assert _constructs_a_client(_only_call("guarded_client(FalkorDB, host='h')"))
    assert _constructs_a_client(_only_call("falkordb.FalkorDB(host='h')"))
    assert not _constructs_a_client(_only_call("some_other(host='h')"))

    # The blind spot this guard used to have: a BARE redis client reached the
    # same socket and matched nothing, so an unbounded one shipped unseen.
    assert _constructs_a_client(_only_call("redis.Redis(host='h', port=1)"))
    assert _constructs_a_client(_only_call("Redis(host='h', port=1)"))
    assert _constructs_a_client(_only_call("redis.StrictRedis(host='h')"))
    assert _constructs_a_client(_only_call("redis.from_url('redis://h')"))

    # …and an `as` alias is the same blind spot wearing a different name.
    assert not _constructs_a_client(_only_call("R(host='h', port=1)"))
    assert _constructs_a_client(
        _only_call("R(host='h', port=1)"), {"R": "Redis"})
    assert _client_aliases(ast.parse("from redis import Redis as R")) == {"R": "Redis"}
    assert _client_aliases(ast.parse("import redis")) == {}
    assert _client_aliases(ast.parse("from something_else import Redis as R")) == {}

    # The verdict predicate behind BOTH systemic guards (#6882).
    assert _client_verb(_only_call("FalkorDB(host='h')")) == "FalkorDB"
    assert _client_verb(
        _only_call("guarded_client(FalkorDB, host='h')")) == _GUARD_SEAM
    assert _client_verb(_only_call("falkordb.FalkorDB(host='h')")) == "FalkorDB"
    assert _client_verb(_only_call("some_other(host='h')")) is None
    # An `as` alias resolves to the name it aliases — including the seam's real
    # home module, whose exclusion would make this control vacuous.
    assert _client_verb(
        _only_call("F(host='h')"),
        _client_aliases(ast.parse("from falkordb import FalkorDB as F"))) == "FalkorDB"
    assert _client_verb(
        _only_call("G(host='h')"),
        _client_aliases(ast.parse(
            "from tortoise.cypher_guard import guarded_client as G"))) == _GUARD_SEAM

    assert not _carries_a_read_bound(_only_call("guarded_client(FalkorDB, host='h')"))
    assert not _carries_a_read_bound(_only_call("redis.Redis(host='h', port=1)"))
    assert _carries_a_read_bound(
        _only_call("guarded_client(FalkorDB, host='h', socket_timeout=10)"))
    assert _carries_a_read_bound(
        _only_call("guarded_client(FalkorDB, **_bounded_server_socket_kwargs())"))
    # `socket_timeout=None` is the #3065 bug written down, not a bound.
    assert not _carries_a_read_bound(
        _only_call("redis.Redis(host='h', port=1, socket_timeout=None)"))
    # …but a NAME is not statically resolvable, so it must stay accepted.
    assert _carries_a_read_bound(
        _only_call("redis.Redis(host='h', port=1, socket_timeout=cfg.read_to)"))


def test_every_bound_elsewhere_claim_names_a_live_test():
    """An exemption may not outlive the test that justifies it.

    ``_BOUND_LIVES_ELSEWHERE`` is the one place where a construction with no
    visible bound is allowed through, so the proof it carries has to exist:
    otherwise the entry rots into a silent hole of exactly the shape #3065 was.
    
    The proving test may be written either way this repo writes tests — a
    ``class`` (``::TestX``) or a bare ``def test_…`` (#8118: the graph-viz
    builder's proof is a function) — so both spellings are accepted and neither
    lowers the bar: the symbol has to be DEFINED in the named file.
    """
    for rel, node_id in _BOUND_LIVES_ELSEWHERE.items():
        path = REPO_ROOT / rel
        assert path.exists(), f"exempt site is gone: {rel}"
        assert "::" in node_id, ("an exemption must name its proving test, "
                                 f"not a bare file: {node_id!r}")
        test_file, _, test_name = node_id.partition("::")
        # ``Class::test`` and ``test`` are both valid node ids; the SYMBOL is
        # the last segment either way.
        symbol = test_name.rpartition("::")[2]
        assert symbol, f"no test named for {rel}"
        src = (REPO_ROOT / test_file).read_text(encoding="utf-8")
        assert f"class {symbol}" in src or f"def {symbol}" in src, (
            f"the test proving {rel}'s bound is elsewhere no longer exists: "
            f"{node_id}")
        assert "socket_timeout" in src, (
            f"{test_file} no longer mentions socket_timeout — the bound that "
            f"justifies exempting {rel} may be gone")


# ═══════════════════════════════════════════════════════════════════════════
# 5. #8118 — the same class OUTSIDE the product package
# ═══════════════════════════════════════════════════════════════════════════

#: The roots #8118 closes the scan over. #3065's scope was deliberately
#: ``tortoise/`` only; the identical defect survived in the tool surfaces beside
#: it, which is what an unscanned root looks like: five sites the issue body
#: listed, and FOUR more the re-measurement found (``tools/ask_shape_rate.py``,
#: ``tools/graph_write_repair.py``, ``graph-scripts/smoke_test.py`` — and the
#: graph-viz boot, which is unbounded through an env→kwargs builder rather than
#: at the constructor). MEASURED on ``origin/main`` @ ``43e5529a3``: 15
#: recognized constructions, 9 of them carrying no read bound.
TOOL_SURFACE_ROOTS = ("tools", "graph-scripts", "apps")

#: Every recognized construction in those roots, pinned per file. A NEW site
#: reds this guard twice over (absent from the map, or a changed count), which is
#: the point — a silently growing inventory is an inventory that already drifted.
#: ``tortoise/`` keeps its own map above; this one is separate on purpose, so a
#: change of SCOPE is a visible edit to this file rather than a wider loop.
_EXPECTED_TOOL_SITES = {
    "apps/graph-viz/server/main.py": 1,          # the viz boot client (bound via its builder)
    "graph-scripts/audit_graph.py": 1,           # the shallow audit's shared client
    "graph-scripts/audit_graph_deep.py": 1,      # the deep audit's shared client
    "graph-scripts/context_removal_audit.py": 1,  # pre-existing 5s/120s literals
    "graph-scripts/merge_endometriosis.py": 1,   # the one-off data merge
    "graph-scripts/parity_sample.py": 1,         # pre-existing 5s/120s literals
    "graph-scripts/pre_migration_snapshot.py": 2,  # two pre-existing bounded sites
    "graph-scripts/smoke_test.py": 1,            # the server-mode liveness client
    "tools/ask_shape_rate.py": 1,                # the scratch-graph GRAPH.DELETE
    "tools/edge_census.py": 2,                   # GRAPH.LIST + the measurement handle
    "tools/graph_write_repair.py": 2,            # per-DB scan client + the repair client
    "tools/longmem_eval/server_posture.py": 1,   # pre-existing, already resolver-bound
}


def test_every_tool_surface_client_is_constructed_with_a_read_bound():
    """#8118's systemic pin: the bound is not a product-package privilege.

    ``tools/edge_census.py`` is the sharpest case — its contract is "read a live
    production graph and change nothing", so an unbounded ``recv()`` against a
    stalled engine (the #2969 defrag loop, #2838's MERGE lock) is precisely the
    outcome a caller cannot tolerate: the process stays alive at 0% CPU, prints
    nothing, and exits nothing. ``apps/graph-viz/server/main.py`` is the same
    shape on a user-facing boot path, retried 30 times.

    Scope: ``tools/``, ``graph-scripts/``, ``apps/``. ``tests/`` and any
    ``tests/`` subdirectory are excluded — test-owned fixtures are not product or
    tool surfaces (and ``apps/graph-viz/tests/`` would otherwise be scanned by
    virtue of its parent). ``battery/``, ``benchmarks/``, ``services/``,
    ``integrations/``, ``client/`` and ``spike/`` were measured on
    ``origin/main`` @ ``43e5529a3`` and carried ZERO unbounded constructions, so
    they are not scanned here; adding a root is a deliberate edit to
    ``TOOL_SURFACE_ROOTS``.
    """
    found: dict[str, int] = {}
    offenders: list[str] = []
    for root in TOOL_SURFACE_ROOTS:
        root_path = REPO_ROOT / root
        assert root_path.is_dir(), f"scan root is gone: {root}"
        for path in sorted(root_path.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            aliases = _client_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _constructs_a_client(
                        node, aliases):
                    continue
                found[rel] = found.get(rel, 0) + 1
                if (not _carries_a_read_bound(node)
                        and rel not in _BOUND_LIVES_ELSEWHERE):
                    offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "raw FalkorDB/redis client(s) in the tool surfaces constructed with no "
        "read bound — ``falkordb`` defaults ``socket_timeout=None`` and hands it "
        "to redis-py explicitly, so the read is UNBOUNDED and a stalled server "
        "parks the process in recv() forever (#8118, same defect as #3065). Pass "
        "**_bounded_server_socket_kwargs() from tortoise.projection, or an "
        f"explicit socket_timeout: {offenders}")

    # Anti-vacuity + explicit inventory (#7053/#1349 class): a scan that found
    # nothing would pass the assert above while guarding nothing.
    assert found == _EXPECTED_TOOL_SITES, (
        "the tool-surface client inventory changed — a new raw client must be "
        "listed here (and carry a bound) rather than silently joining the tool "
        f"surfaces: found={found} expected={_EXPECTED_TOOL_SITES}")


def _graph_viz_connection_module(monkeypatch):
    """Load ``apps/graph-viz/server/connection.py`` WITHOUT importing main.py.

    ``main.py`` connects at import time, so the builder is loaded by path — the
    same seam its own unit test uses.
    """
    import importlib.util

    path = REPO_ROOT / "apps" / "graph-viz" / "server" / "connection.py"
    spec = importlib.util.spec_from_file_location("gviz_connection_8118", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME",
                 "FALKORDB_PASSWORD", "FALKORDB_SSL",
                 "TORTOISE_DB_SOCKET_TIMEOUT",
                 "TORTOISE_DB_SOCKET_CONNECT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    return module


def test_the_graph_viz_kwargs_builder_carries_the_bounded_read(monkeypatch):
    """#8118: the exemption behind ``apps/graph-viz/server/main.py``'s proof.

    ``main.py`` builds its boot client as ``FalkorDB(**build_connection_kwargs())``,
    so the bound cannot appear at the call node — the deliberate placement is the
    env→kwargs BUILDER, which is the seam a test can observe without booting a
    connection. This pins that the builder actually supplies it, so the exemption
    is a claim with a live test behind it rather than a waiver.
    """
    mod = _graph_viz_connection_module(monkeypatch)
    kwargs = mod.build_connection_kwargs()

    assert kwargs["socket_timeout"] is not None, (
        "the viz boot client would carry socket_timeout=None — the unbounded "
        "recv() #8118 exists to kill, on a path that retries it 30 times")
    assert kwargs["socket_timeout"] > 0
    assert kwargs["socket_connect_timeout"] is not None
    assert kwargs["socket_connect_timeout"] > 0
    # The endpoint contract is untouched: the bound is not a re-plumbing.
    assert kwargs["host"] == "localhost"
    assert kwargs["port"] == 16379


def test_the_graph_viz_builder_takes_its_bound_from_the_product_resolver(monkeypatch):
    """The operator knobs must reach the viz server, not a second policy.

    A builder that hard-codes its own seconds is the exact drift #8118 is about
    — bounded, but blind to ``TORTOISE_DB_SOCKET_TIMEOUT`` — so the knob winning
    is what proves the resolver (and not a literal) is the source.
    """
    mod = _graph_viz_connection_module(monkeypatch)
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", "3")
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "0.5")

    kwargs = mod.build_connection_kwargs()

    assert kwargs["socket_timeout"] == 3.0
    assert kwargs["socket_connect_timeout"] == 0.5


def test_graph_viz_fallback_matches_the_product_defaults(monkeypatch):
    """The standalone-deploy fallback is pinned EQUAL to the product defaults.

    The viz server ships its own ``requirements.txt`` with no ``tortoise``, so the
    resolver cannot be a hard import and a fallback must exist. An unpinned
    fallback would be a second policy — the defect class this whole file is about
    — so a change to either side without the other is this red test.
    """
    from tortoise.projection import (
        _DB_CONNECT_TIMEOUT_DEFAULT,
        _DB_SOCKET_TIMEOUT_DEFAULT,
    )

    mod = _graph_viz_connection_module(monkeypatch)
    assert mod.FALLBACK_SOCKET_CONNECT_TIMEOUT == _DB_CONNECT_TIMEOUT_DEFAULT
    assert mod.FALLBACK_SOCKET_TIMEOUT == _DB_SOCKET_TIMEOUT_DEFAULT
