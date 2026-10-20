"""#9209 — §17.1's FOURTH door: the write handle's NON-CYPHER verbs.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 makes the graph a DERIVED
CACHE of the journal, and #7951 armed a refusal at two of its doors — the fold
(`FalkorProjection.apply`) and the public raw-query lane
(`FalkorProjection.query`). Both of those doors read a Cypher **statement**,
and a whole-graph destruction carries none, so `proj.g` still wiped an armed
projection two ways:

* ``proj.g.delete()`` — the vendor's ``GRAPH.DELETE``, reached through
  ``_GuardedGraph.__getattr__``, which forwards every attribute the wrapper
  does not itself define. No verb-level guard can see a forwarded attribute *by
  construction*.
* ``proj.g.execute_command("GRAPH.DELETE", <graph>)`` — the raw command
  channel, which ``cypher_guard._guard_execute_command`` forwards untouched
  because it classifies only the Cypher argument of the four ``GRAPH.*`` QUERY
  verbs.

MEASURED on `main` before this suite existed (embedded, journal-backed — the
same shape the issue reports from `feat/9124-tm`):

    proj.g.query("MATCH (n) RETURN count(n)")                 -> 2
    proj.g.execute_command("GRAPH.DELETE", proj._graph_name)  -> 'OK'
    proj.g.query("MATCH (n) RETURN count(n)")                 -> 0
    proj.g.delete()                                           -> 'OK'

while the module comment stated the handle's escapes as three *statement*
escapes. The wipe is unrepairable in the direction it ran: `rebuild_all` can
only replay records, and "somebody deleted the graph" is not one.

WHAT THIS SUITE PINS — the wrong behaviour is IMPOSSIBLE, not merely that a new
path runs. Every refusal case asserts the CACHE IS STILL THERE afterwards
(count unchanged), because a guard that raised *after* the wipe would leave
exactly the corruption it claims to prevent, with a traceback on top.

WHAT IT DOES NOT CLAIM. Not every non-Cypher mutation is refused — only the one
that destroys this cache. `GRAPH.COPY` writes this cache's contents into
ANOTHER graph and removes nothing here, so it must stay allowed: a rule that
refused it would be refusing an operation the cache never lost anything to, and
`tests/test_graphcopy_boolean_index_3154.py` legitimately copies through this
handle. That asymmetry is the rule, and
`test_an_unrelated_non_cypher_verb_...` is what keeps it honest. The
Cypher-bearing raw command
(``execute_command("GRAPH.QUERY", <g>, "MATCH (n) DETACH DELETE n")``) is NOT
closed here — it needs statement recovery, which is #9124's door.

⛔ THE VERB LIST IS NOT THE RULE EITHER (review round 6), and this suite was
written wrongly once because of it: a FalkorDB graph IS a Redis key, so
``execute_command("DEL", <own graph>)`` — and ``UNLINK``, ``RENAME``,
``EXPIRE 0`` — wiped an armed cache while the enumerated list named only
``GRAPH.DELETE`` and the flushes. MEASURED, on a real journal-backed SDK at
the head that shipped the list:

    proj.g.execute_command("DEL", proj._graph_name)        -> 'OK', 2 -> 0
    proj.g.execute_command("UNLINK", proj._graph_name)     -> 1,    2 -> 0
    proj.g.execute_command("RENAME", proj._graph_name, x)  -> True, cache moved
    proj.g.execute_command("EXPIRE", proj._graph_name, 0)  -> 1,    2 -> 0

So the armed handle enforces a NAMESPACE boundary instead: outside the
``GRAPH.`` family it refuses every command except the read-only probes the repo
actually sends (``_ARMED_READ_ONLY_COMMANDS``), because anything else that can
reach the key space can remove the cache. The tests below pin BOTH halves —
the refusal, and that a read still passes (an over-refusal here would silently
drop a legitimately retried write: `tortoise/sdk.py::_graph_presence` probes
``EXISTS`` and answers "do not retry" when it raises).
"""
from __future__ import annotations

import contextlib
import inspect

import pytest

from tortoise.projection import (
    DirectCacheWriteRefused,
    FalkorProjection,
    _GuardedGraph,
    _handle_command_text,
    _handle_command_words,
    _replay_write_scope,
)

# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK whose projection is ARMED (`event_log_path` configured).

    Lane-agnostic in NAME only — the explicit `db_path` forces the EMBEDDED
    branch in every lane (`TortoiseSDK.__init__` takes the URI branch only when
    `db_uri and db_path is None`), so no case here can reach the shared docker
    server even when `TORTOISE_DB_URI` is configured (#5084). The fixture
    ASSERTS that above, so if the branch ever inverts the lane hazard is a red
    test rather than a silent wipe of every concurrent lane's graphs.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "noncypher.db"), event_log_path=str(journal))
    assert sdk._get_proj()._is_embedded, (
        "this suite must run on the EMBEDDED lane: an explicit `db_path` always "
        "wins over `TORTOISE_DB_URI`, and a case that reached the shared docker "
        "server would make this suite's failure mode a real wipe of every other "
        "lane's graphs (#5084)"
    )
    sdk.create_entity("subject", "armed-subject", description="a real node")
    try:
        yield sdk
    finally:
        sdk.close()


def _count(proj, graph=None) -> int:
    handle = proj if graph is None else graph
    return int(handle.query("MATCH (n) RETURN count(n)").result_set[0][0])


def _drop_graph(proj, name: str) -> None:
    """Remove a graph this suite minted, through the CLIENT channel.

    The deliberate, visible channel for a drop (`proj.db`), not the write handle
    whose graph/key doors are §17.1-guarded. Best-effort by design: a teardown
    must never turn a passing assertion into an error, but it must also not
    leave a graph behind per run on any lane (#5084) — `GRAPH.COPY` mints its
    destination directly, so the session journal never replays it and the #8746
    teardown's per-run name shape does not match a `_9209_copy` suffix.
    """
    with contextlib.suppress(Exception):
        proj.db.select_graph(name).delete()


class _RecordingGraph:
    """Minimal vendor-shaped handle: records the calls it receives.

    Used only where the assertion is about a decision made BEFORE dispatch
    (the L2 / declared-scope cases), so nothing here reaches a server.
    """

    def __init__(self):
        self.calls: list[tuple] = []

    def delete(self, *args, **kwargs):
        self.calls.append(("delete", args, kwargs))
        return "OK"

    def execute_command(self, *args, **kwargs):
        self.calls.append(("execute_command", args, kwargs))
        return "OK"

    def call_procedure(self, *args, **kwargs):
        self.calls.append(("call_procedure", args, kwargs))
        return "OK"

    def query(self, cypher, params=None, timeout=None):  # pragma: no cover
        raise AssertionError("no query may run in these cases")


class _NamedRecordingGraph(_RecordingGraph):
    """A recording handle that carries the vendor's ``name`` attribute.

    `delete()` passes the handle's OWN graph name as the target, because the
    wrapper is also built in-tree over a graph that is not the projection's
    (`tortoise/hosted_api.py`), so "the projection's name" is not always "the
    graph this verb removes".
    """

    def __init__(self, name: str):
        super().__init__()
        self.name = name


class _EncodeLiar(str):
    """A ``str`` whose ``encode()`` puts a DIFFERENT command on the wire.

    The pinned client serializes ``args[0].encode()``, so this is what the server
    sees; ``str`` content says ``GRAPH.COPY`` (review round 9).
    """

    def encode(self, *args, **kwargs):
        return b"GRAPH.DELETE prod_tortoise"


class _SplitLiar(bytes):
    """A ``bytes`` whose ``split()`` puts a DIFFERENT command on the wire.

    The pinned client splits ``args[0]`` itself, so an overridden ``split`` is
    what decides the command the server parses (review round 9).
    """

    def split(self, *args, **kwargs):
        return [b"GRAPH.DELETE", b"prod_tortoise"]


class _FormatLiar:
    """An object whose ``__format__`` writes Cypher into ``CALL {procedure}``.

    The vendor builds the CALL text with an f-string, so ``__format__``/``__str__``
    decides what the server parses (review round 10).
    """

    def __format__(self, spec):
        return "db.labels() MATCH (n) DETACH DELETE n //"

    def __str__(self):
        return "db.labels"


def _armed_projection(*, graph_name: str, embedded: bool):
    """An ARMED projection with no DB — the guard must fire before any I/O.

    `_journal_backed` is the arming (`FalkorProjection._bind_journal`); it is a
    class-level default of `False`, so setting it per instance arms this object
    exactly as a configured journal does.
    """
    proj = object.__new__(FalkorProjection)
    proj._journal_backed = True
    proj._is_embedded = embedded
    proj._graph_name = graph_name
    proj._path = None
    proj.g = _NamedRecordingGraph(graph_name)
    return proj


# ── the command channel: GRAPH.DELETE ─────────────────────────────────────

@pytest.mark.parametrize("verb", ["GRAPH.DELETE", "graph.delete", b"GRAPH.DELETE",
                                  memoryview(b"GRAPH.DELETE"),
                                  memoryview(b"graph.delete")])
def test_GRAPH_DELETE_on_the_command_channel_is_refused_on_an_armed_projection(
        sdk_with_journal, verb):
    """RED before #9209 (the wipe LANDED — count went to 0), GREEN after.

    Three spellings, because the classifier must not be one literal: redis-py
    accepts the BYTES form (the #3595 review finding) and Redis itself is
    case-insensitive, so a str-only or case-sensitive match would leave the
    door open two ways while appearing closed.

    The two ``memoryview`` cases are the same door one decoder branch over, and
    they were MEASURED open at the head before this pin: the client's
    ``Encoder.encode`` passes a ``memoryview`` through UNCHANGED, so the buffer
    reached the wire as ``GRAPH.DELETE`` — while a decoder that knew only
    ``bytes``/``bytearray`` returned it untouched and the wrapper forwarded the
    command. RED there (`DID NOT RAISE`, cache at 0 nodes).

    MUTATION: drop the `_DESTRUCTIVE_HANDLE_COMMANDS` branch from
    `_GuardedGraph.execute_command` and this test fails at `pytest.raises`
    (DID NOT RAISE), with the node count at 0.
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed, (
        "the fixture must ARM the guard (`event_log_path` configured) — an "
        "unarmed projection is outside §17.1 and this test would be vacuous"
    )
    before = _count(proj)
    assert before > 0, "the armed cache must hold something a wipe could destroy"

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.g.execute_command(verb, proj._graph_name)

    assert "17.1" in str(exc.value), (
        "the refusal must cite the rule it enforces so the next writer can read "
        f"why; message={str(exc.value)!r}"
    )
    assert _count(proj) == before, (
        "the refusal must be PRE-DISPATCH: an armed projection whose wipe raises "
        "AFTER deleting the cache is worse than no guard at all"
    )


# ── the server-wide commands: FLUSHALL / FLUSHDB ─────────────────────────


@pytest.mark.parametrize(
    "args",
    [("FLUSHALL",), ("flushall",), (b"FLUSHALL",), ("FLUSHALL", "ASYNC"),
     ("FLUSHALL ASYNC",), (b"flushall async",), ("FLUSHDB",), ("flushdb", "ASYNC")],
)
def test_a_server_wide_flush_is_refused_on_an_armed_projection(args):
    """Review finding, now a pin: a flush carries no statement and no graph name.

    ``_guard_execute_command`` forwards every non-``GRAPH`` command untouched,
    so ``proj.g.execute_command("FLUSHALL")`` destroyed an armed cache — the
    same class as #9209 one level up (it takes EVERY graph on the server, not
    just this one). The module's own maxmemory message already told operators
    "do NOT FLUSHALL"; this is that advice enforced at the handle.

    The joined-string spellings are included because the client SPLITS the
    first argument at pack time (`redis.connection.*Serializer.pack`), so
    ``"FLUSHALL ASYNC"`` is the same command as ``("FLUSHALL", "ASYNC")`` — a
    classifier that reads one token would close the pair spelling only.

    ⛔ PINNED ON A RECORDING HANDLE, not a live SDK, and that is a review fix
    (#5084): the assertion's FAILURE mode is a dispatched ``FLUSHALL``, so a
    live case whose fixture ever reached the shared docker server would wipe
    every concurrent lane's graphs BEFORE the red test could report anything.
    ``calls == []`` proves the refusal is pre-dispatch, which is strictly
    stronger than "the cache still had its nodes". The live-handle pin on
    ``GRAPH.DELETE`` above keeps the real SDK/arming path covered (that one can
    only remove the graph it minted itself).

    MUTATION: drop the ``_SERVER_WIDE_COMMANDS`` branch from
    `_refuse_unauthorized_graph_wipe` and this fails with ``calls`` holding the
    flush.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command(*args)

    assert proj.g.calls == [], (
        f"the flush reached the vendor handle despite the refusal; "
        f"calls={proj.g.calls!r}"
    )


def test_a_server_wide_flush_is_refused_even_inside_a_declared_scope():
    """A declaration says "this WRITE is a projection of the journal".

    It cannot say that about a flush: ``FLUSHALL`` also removes graphs this
    journal never wrote and no replay can restore — and L2 (the disposability
    check) reads THIS projection's name, so it can speak for exactly one graph
    while the command takes all of them. Review round 2 measured that gap: with
    a ``test_*``-named (or embedded) armed projection, an in-scope flush passed.

    MUTATION: delete the ``_SERVER_WIDE_COMMANDS`` branch from
    `_refuse_unauthorized_graph_wipe` and this fails — the in-scope flush is
    dispatched instead of raising.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="EVERY graph"
    ):
        guarded.execute_command("FLUSHALL")
    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="EVERY graph"
    ):
        guarded.execute_command("FLUSHDB", "ASYNC")

    assert proj.g.calls == [], (
        f"a server-wide flush reached the vendor handle; calls={proj.g.calls!r}"
    )


# ── the client splits the first argument: the joined spelling ─────────────


@pytest.mark.parametrize("sep", [" ", "  ", "\t"])
def test_a_joined_graph_delete_is_refused_like_the_two_argument_form(
    sdk_with_journal, sep
):
    """Review finding, now a pin: the CLIENT splits the first argument.

    Both serializers in the pinned ``redis`` do ``args[0].encode().split()``
    before packing (their own comment: a caller may put "1 or more literal
    arguments in the command name, e.g. 'CONFIG GET'"), so
    ``execute_command("GRAPH.DELETE <graph>")`` and
    ``execute_command("GRAPH.DELETE", "<graph>")`` arrive byte-identical. A
    classifier that reads ``args[0]`` as ONE TOKEN therefore closes one spelling
    and leaves the other open — the same spelling-shaped coverage defect #9209
    exists to end. Whitespace variants are included because the splitter is
    ``str.split()``, not a space-only split.

    MUTATION: take the verb from ``args[0]`` verbatim instead of from
    ``_handle_command_words`` and this fails with DID NOT RAISE, cache at 0.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    assert before > 0

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(f"GRAPH.DELETE{sep}{proj._graph_name}")

    assert _count(proj) == before, (
        "the joined-spelling wipe reached the server despite the refusal"
    )


# ── the forwarded attribute: proj.g.delete() ──────────────────────────────


def test_the_handle_delete_verb_is_refused_and_lands_nothing(sdk_with_journal):
    """RED before #9209 (`proj.g.delete()` returned 'OK' and emptied the graph).

    This is the shape no query guard can reach: `__getattr__` forwarded the
    vendor's `delete` verb straight through. Pinned behaviourally on the REAL
    vendor handle, so an implementation that merely renames the hole (defines
    `delete` but forgets to call the decision) fails here.

    MUTATION: delete the `delete` method from `_GuardedGraph` (back to a
    forwarded attribute) and this test fails — the assertion is on the node
    count, which reaches 0.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    assert before > 0

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.delete()

    assert _count(proj) == before, (
        "the whole-graph delete reached the vendor handle despite the refusal"
    )


def test_the_same_decision_covers_both_spellings():
    """One decision, two spellings — pinned structurally, not by prose.

    The command channel and the forwarded attribute must consult the SAME
    refusal (`_refuse_unauthorized_graph_wipe`). Two copies of the rule is how
    one of them silently drifts open the next time the other is narrowed; the
    repo's #3595 convention is one shared decision per class.
    """
    assert hasattr(_GuardedGraph, "delete"), (
        "`delete` must be DEFINED on the wrapper — a forwarded attribute is "
        "unreachable by any verb-level guard by construction (#9209)"
    )
    assert hasattr(_GuardedGraph, "_refuse_unauthorized_graph_wipe")
    for fn in (_GuardedGraph.delete, _GuardedGraph.execute_command):
        src = inspect.getsource(fn)
        assert "_refuse_unauthorized_graph_wipe" in src, (
            f"{fn.__qualname__} does not consult the shared wipe refusal"
        )


# ── the precise half: an UNRELATED non-Cypher verb ────────────────────────

def test_an_unrelated_non_cypher_verb_leaves_the_armed_cache_intact(
        sdk_with_journal):
    """A non-Cypher mutation that destroys nothing must not be refused, and must
    not disturb an armed cache.

    Three assertions, each load-bearing:
    1. the verb was NOT refused — so the guard refuses destruction, not every
       non-Cypher mutation (a blacklist would fail right here);
    2. the COPY really ran (the destination holds the same nodes) — otherwise
       "the cache is intact" would be satisfied by a no-op and prove nothing;
    3. the arming survived (a following drive-by `GRAPH.DELETE` is still
       refused) — a guard that "cleared" its own state on an unrelated verb
       would re-open §17.1 for the rest of the process.

    MUTATION: make `_refuse_unauthorized_graph_wipe` / `_refuse_key_space_handle_
    command` refuse every non-Cypher verb and (1) fails; make them clear/disarm
    anything on the way through and (3) fails.

    The destination is dropped in a `finally` (#9209 review): `GRAPH.COPY`
    mints it directly, so the session journal never replays it and the #8746
    teardown's per-run name shape does not match a `_9209_copy` suffix — on a
    shared server that would be one leftover graph per run.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    dst = proj._graph_name + "_9209_copy"

    try:
        proj.g.copy(dst)  # must NOT be refused: it removes nothing from this cache

        assert _count(proj) == before, "an unrelated non-Cypher verb changed the cache"
        assert _count(proj, proj.db.select_graph(dst)) == before, (
            "GRAPH.COPY did not actually run — the 'cache intact' assertion below "
            "would be vacuous"
        )
        with pytest.raises(DirectCacheWriteRefused):
            proj.g.delete()
        assert _count(proj) == before, (
            "the arming did not survive an unrelated non-Cypher verb"
        )
    finally:
        _drop_graph(proj, dst)


# ── the key-space door: the cache IS a Redis key (review round 6) ────────

#: Every one of these DESTROYS the graph's key — and with it the armed cache —
#: while carrying no Cypher statement and, in the first two, no graph argument
#: that any Cypher-reading guard could see. Measured wiping a real armed cache
#: at the head that enumerated only GRAPH.DELETE and the flushes.
_KEY_SPACE_WIPES = [
    ("DEL",),
    ("UNLINK",),
    ("RENAME", "<other>"),
    ("EXPIRE", 0),
    ("PEXPIRE", 0),
]


@pytest.mark.parametrize("tail", _KEY_SPACE_WIPES,
                         ids=[t[0] for t in _KEY_SPACE_WIPES])
def test_a_key_space_command_on_the_graph_key_is_refused(
        sdk_with_journal, tail):
    """RED at the previous head: the cache went to 0 with no error at all.

    The graph's derived cache is stored under the graph's OWN Redis key, so
    ``DEL`` / ``UNLINK`` / ``RENAME`` / ``EXPIRE 0`` are the same whole-graph
    wipe as ``GRAPH.DELETE`` — with no statement for either Cypher door to read
    and, for the first two, no graph name in a position the classifier looked
    at. That is why the fix is a NAMESPACE boundary and not four more entries in
    a verb list: the list was incomplete at every round that used one.

    PRE-DISPATCH, as everywhere in this suite: the count is read AFTER the
    refusal, so a guard that raised on top of a completed wipe fails here.

    MUTATION: return early from `_refuse_key_space_handle_command` and this
    fails with DID NOT RAISE and the node count at 0 (verified against the
    head before this test existed).
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed, "the fixture must ARM the guard"
    before = _count(proj)
    assert before > 0, "the armed cache must hold something a wipe could destroy"

    args = [tail[0], proj._graph_name]
    for extra in tail[1:]:
        args.append(proj._graph_name + "_9209_dst" if extra == "<other>" else extra)

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.g.execute_command(*args)

    assert "17.1" in str(exc.value), (
        f"the refusal must cite the rule it enforces; message={str(exc.value)!r}"
    )
    assert _count(proj) == before, (
        f"`{tail[0]}` was refused AFTER it destroyed the cache — an armed "
        "projection whose wipe raises on top of a completed deletion is worse "
        "than no guard at all"
    )


@pytest.mark.parametrize("verb", [b"DEL", "del", "DEL", memoryview(b"DEL")])
def test_the_key_space_refusal_is_not_spelling_shaped(sdk_with_journal, verb):
    """Bytes and case, because the client accepts both and Redis is
    case-insensitive — the same two spellings the GRAPH.DELETE cases pin.

    ``memoryview`` joined them in review: the client's ``Encoder.encode`` passes
    it through UNCHANGED, so the buffer reaches the wire as ``DEL`` while a
    decoder that only knew ``bytes``/``bytearray`` forwarded the command.
    RED at the head before this case existed.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(verb, proj._graph_name)
    assert _count(proj) == before


def test_an_unreadable_procedure_payload_is_refused():
    """Review round 10 (P1): ``call_procedure`` must not read past what it sends.

    The vendor formats the CALLER's object into ``CALL {procedure}(…)``, so a
    ``str`` SUBCLASS or a ``__format__``-carrying object decides the statement on
    the wire. MEASURED at the head before this pin: a ``__format__`` that emits
    ``MATCH (n) DETACH DELETE n`` was DISPATCHED on a non-test graph while the
    same wipe as an exact ``str`` was refused by L2 — i.e. the guard judged a
    statement the server never saw.

    MUTATION: restore `if statement is not None and _is_bulk_wipe(...)` and these
    payloads are forwarded (`calls` non-empty).
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    for payload in (_FormatLiar(), _EncodeLiar("db.labels"),
                    _SplitLiar(b"db.labels")):
        with pytest.raises(DirectCacheWriteRefused):
            guarded.call_procedure(payload, read_only=False)

    # An unreadable EMIT name is the same surface, one argument over.
    with pytest.raises(DirectCacheWriteRefused):
        guarded.call_procedure("db.labels", emit=[_EncodeLiar("node")])

    assert proj.g.calls == [], (
        f"an unreadable procedure payload was dispatched: {proj.g.calls!r}"
    )

    # The readable half: the statement path still classifies the exact text, so
    # L2 refuses the real wipe (the guard did not become a wall).
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.call_procedure("db.labels() MATCH (n) DETACH DELETE n //", False)
    assert proj.g.calls == [], f"the wipe was dispatched: {proj.g.calls!r}"


def test_an_unreadable_head_is_refused_on_an_unarmed_server_projection():
    """Review round 10 (P2): the unreadable-payload rule follows L2, not the journal.

    Gating it on ``_journal_backed`` alone left an unarmed SERVER projection
    forwarding a head it could not read — the same asymmetry one layer over
    (MEASURED: ``'DEL'`` + a str-subclass argument was dispatched, so even
    ``delete()``'s new name rule could be bypassed by making the TARGET
    unreadable). An unarmed EMBEDDED projection keeps the pass-through, which is
    the exemption L2 itself makes.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    proj._journal_backed = False
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command(_SplitLiar(b"DEL"), "prod_tortoise")
    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command("DEL", _EncodeLiar("prod_tortoise"))
    with pytest.raises(DirectCacheWriteRefused):
        guarded.call_procedure(_FormatLiar(), read_only=False)
    assert proj.g.calls == [], (
        f"an unreadable payload was dispatched on an unarmed server graph: "
        f"{proj.g.calls!r}"
    )

    # The embedded exemption is L2's, not a new hole: still pass-through.
    embedded = _armed_projection(graph_name="prod_tortoise", embedded=True)
    embedded._journal_backed = False
    handle = _NamedRecordingGraph("prod_tortoise")
    assert _GuardedGraph(handle, embedded).execute_command(
        _SplitLiar(b"COPY"), "x") == "OK"
    assert _GuardedGraph(handle, embedded).call_procedure(
        _FormatLiar(), read_only=False) == "OK"
    assert [c[0] for c in handle.calls] == ["execute_command", "call_procedure"], (
        "an unarmed EMBEDDED projection is the pre-#9209 pass-through (L2's own "
        "embedded exemption) — pinning it so a later edit cannot widen or "
        f"silently narrow it; calls={handle.calls!r}"
    )


def test_the_shared_decoder_sees_a_memoryview_payload():
    """The THIRD encoding spelling, pinned where the decision actually reads it.

    Review measured a P0 on the head before this: ``memoryview(b"GRAPH.DELETE")``
    was ALLOWED on an armed projection and reached the server as the refused
    command, because ``_handle_command_text`` only decoded ``bytes`` /
    ``bytearray`` and answered ``None`` for a buffer the client forwards
    verbatim. The same gap existed in the shared decoder
    ``cypher_guard._as_text``, which is what the #3595 ``=~`` scan reads — so
    both are pinned here, at the decoder, in addition to the end-to-end cases
    above.

    MUTATION: drop ``memoryview`` from either ``isinstance`` tuple and this
    fails (`None` / the raw buffer instead of the command text).
    """
    from tortoise.cypher_guard import _as_text

    for payload in (memoryview(b"GRAPH.DELETE"),
                    memoryview(bytearray(b"GRAPH.DELETE"))):
        assert _handle_command_text(payload) == "GRAPH.DELETE", (
            f"{payload!r} must decode — the client passes it to the wire unchanged"
        )
        assert _as_text(payload) == "GRAPH.DELETE", (
            "the shared #3595 decoder must see it too, or the operator guard is "
            "bypassed one encoding over"
        )
        assert _handle_command_words((payload, "g")) == ("GRAPH.DELETE", ("g",))

    # ⛔ THE BUFFER, NOT `bytes(value)` — a review round 8 measurement: a
    # `bytes` SUBCLASS whose `__bytes__` lies read as `GRAPH.COPY` here while the
    # client put `GRAPH.DELETE` on the wire (and the armed cache went to 0).
    # `_handle_command_text` reads the buffer AND refuses a subclass outright
    # (its `encode()`/`split()` decide the wire, so no read of it can be trusted);
    # `_as_text` reads the buffer, because the #3595 lane still gains the true
    # characters from it.
    class _LyingBytes(bytes):
        def __bytes__(self):  # the whole point is that it lies
            return b"GRAPH.COPY"

    class _LyingByteArray(bytearray):
        def __bytes__(self):
            return b"GRAPH.COPY"

    lying = _LyingBytes(b"GRAPH.DELETE")
    assert bytes(lying) == b"GRAPH.COPY", (
        "non-vacuity: the subclass must actually lie for this pin to mean anything"
    )
    assert _handle_command_text(lying) is None, (
        "a text/bytes SUBCLASS is UNCLASSIFIABLE — its encode()/split() decide "
        "what the client sends, so the handle must refuse it rather than read it"
    )
    assert _handle_command_words((lying, "g"))[0] is None
    assert _as_text(lying) == "GRAPH.DELETE", (
        "the shared #3595 decoder must read the BUFFER (not __bytes__), or the "
        "operator scan is steered by the subclass"
    )
    assert _handle_command_text(_LyingByteArray(b"DEL")) is None
    assert _as_text(_LyingByteArray(b"DEL")) == "DEL"

    # The over-refusal direction, stated so a later "fix" does not remove it:
    # the client does NOT split a memoryview, so this payload would be ONE token
    # (`unknown command`) on the wire — refusing it here is fail-closed, not a
    # misclassification.
    assert _handle_command_words((memoryview(b"DEL x"),)) == ("DEL", ("x",))


def test_a_lying_bytes_subclass_cannot_smuggle_a_wipe_past_the_handle():
    """Review round 8 (P0), end to end: the classifier must read the BUFFER.

    ``bytes(subclass)`` calls the subclass's ``__bytes__``; the client sends the
    buffer. So a lying subclass made the guard classify ``GRAPH.COPY`` (inside
    the ``GRAPH.`` family, so allowed) while the wire carried ``GRAPH.DELETE`` —
    MEASURED wiping an armed projection. RED at the head before this pin.

    MUTATION: decode with ``bytes(value)`` instead of ``memoryview(value)`` and
    this fails with DID NOT RAISE.
    """
    class _LyingBytes(bytes):
        def __bytes__(self):
            return b"GRAPH.COPY"

    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    # NO declared scope: the point is the VERB the classifier reads. Pre-fix the
    # subclass classified as `GRAPH.COPY` (allowed), so the wipe was dispatched;
    # decoding the buffer classifies `GRAPH.DELETE`, which no undeclared scope
    # authorizes.
    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command(_LyingBytes(b"GRAPH.DELETE"), "test_9209")

    assert proj.g.calls == [], (
        f"the wipe was dispatched despite the refusal: {proj.g.calls!r}"
    )


@pytest.mark.parametrize("payload_factory", [
    lambda: _EncodeLiar("GRAPH.COPY"),
    lambda: _SplitLiar(b"GRAPH.COPY"),
], ids=["str-encode-liar", "bytes-split-liar"])
def test_a_subclass_that_lies_about_its_wire_form_is_refused(payload_factory):
    """Review round 9 (P1): the client serializes the CALLER's object.

    ``redis`` calls ``args[0].encode()`` for a ``str`` and ``args[0].split()`` for
    a ``bytes`` — both overridable, so a subclass put ``GRAPH.DELETE`` on the wire
    while every base-class read of it said ``GRAPH.COPY`` (MEASURED at the head
    before this pin: the command was dispatched). No read can be proven to match
    such a payload, so an armed handle REFUSES an unclassifiable one instead of
    forwarding it.

    MUTATION: let a subclass through `_handle_command_text` (or drop the
    `_command_payload_is_unreadable` check from `execute_command`) and this fails
    with DID NOT RAISE.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(DirectCacheWriteRefused):
        guarded.execute_command(payload_factory(), "test_9209")

    assert proj.g.calls == [], (
        f"an unreadable payload was dispatched: {proj.g.calls!r}"
    )


def test_a_handle_without_a_name_is_refused_not_judged_by_the_projection():
    """Review round 9 (P3): L2 must not fall back to the PROJECTION's name.

    `delete()` names the graph it removes. A handle that exposes no ``name``
    cannot be identified at all, so judging ``proj._graph_name`` would approve a
    delete of a graph nothing looked at — MEASURED approving a nameless handle's
    delete because the projection happened to be named ``test_…``.
    """
    class _Nameless:
        def __init__(self):
            self.calls = []

        def delete(self, *args, **kwargs):
            self.calls.append(("delete", args, kwargs))
            return "OK"

    proj = _armed_projection(graph_name="test_9209", embedded=False)
    nameless = _Nameless()

    with pytest.raises(RuntimeError, match="no `name`"):
        _GuardedGraph(nameless, proj).delete()
    assert nameless.calls == [], f"a nameless delete dispatched: {nameless.calls!r}"


def test_a_joined_key_space_spelling_is_refused_too(sdk_with_journal):
    """The client splits the first argument, so ``"DEL <graph>"`` in one string
    is the same command as two arguments (`_handle_command_words`).
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(f"DEL {proj._graph_name}")
    assert _count(proj) == before


def test_a_key_space_command_is_refused_on_NAMESPACE_not_on_a_list(
        sdk_with_journal):
    """The rule's own evidence: a verb named NOWHERE in the module is refused.

    ``SET``/``RESTORE``/``SWAPDB`` are not in any list (and adding them would
    only move the incompleteness), yet each can overwrite, restore over, or
    swap away the key the cache lives under. The guard must refuse them because
    they leave the ``GRAPH.`` namespace, not because someone enumerated them.

    ``EVAL`` is here for the SCRIPTING channel a review named: it can issue
    ``redis.call('FLUSHALL')`` / ``redis.call('GRAPH.DELETE', KEYS[1])``
    SERVER-side, so no client-side per-verb enumeration could ever cover it —
    the namespace boundary does, because a script command leaves the family.

    MUTATION: replace the namespace check with a verb allow-list of the currently
    enumerated commands and this fails (``SET`` would be dispatched).
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    for args in (("SET", proj._graph_name, "{} "), ("RESTORE", proj._graph_name, 0, "x"),
                 ("SWAPDB", 0, 1),
                 ("EVAL", "return redis.call('FLUSHALL')", 0),
                 ("EVALSHA", "deadbeef", 1, proj._graph_name)):
        with pytest.raises(DirectCacheWriteRefused) as exc:
            proj.g.execute_command(*args)
        assert "GRAPH." in str(exc.value), (
            "the refusal must name the boundary it enforces so the next writer "
            f"can see why; message={str(exc.value)!r}"
        )
    assert _count(proj) == before


def test_a_read_only_probe_still_passes_on_an_armed_projection(sdk_with_journal):
    """The other half of the rule: over-refusal here would be SILENT.

    `tortoise/sdk.py::_graph_presence` probes ``proj.g.execute_command("EXISTS",
    <graph>)`` on its retry path and treats a refusal as "do not retry" — so a
    guard that refused the read would not fail loudly, it would quietly drop a
    legitimately retried write (#7685). Both spellings the SDK uses are pinned.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)

    assert proj.g.execute_command("EXISTS", proj._graph_name), (
        "EXISTS must still answer on an armed projection — the SDK's retry gate "
        "reads it"
    )
    assert proj.g.execute_command(b"TYPE", proj._graph_name) is not None
    assert _count(proj) == before


def test_a_graph_namespace_command_that_destroys_nothing_still_passes(
        sdk_with_journal):
    """The precise half of the NAMESPACE rule, on the command channel.

    ``GRAPH.COPY`` is inside the family and removes nothing from this cache, so
    the boundary must not touch it — the same asymmetry
    `test_an_unrelated_non_cypher_verb_...` pins for the forwarded attribute.

    MUTATION: make `_refuse_key_space_handle_command` refuse the whole non-empty
    command space instead of leaving the ``GRAPH.`` family alone and this fails.
    """
    proj = sdk_with_journal._get_proj()
    before = _count(proj)
    dst = proj._graph_name + "_9209_ns_copy"

    try:
        proj.g.execute_command("GRAPH.COPY", proj._graph_name, dst)

        assert _count(proj) == before, "GRAPH.COPY changed this cache"
        assert _count(proj, proj.db.select_graph(dst)) == before, (
            "GRAPH.COPY did not actually run — 'cache intact' would be vacuous"
        )
    finally:
        _drop_graph(proj, dst)


# ── the declaration: a scope authorizes the WRITE, never the GRAPH ────────

def test_a_declared_scope_still_refuses_a_non_test_server_graph():
    """L2 applies INSIDE the declaration (#2944's polarity, one door over).

    `prod_tortoise` is the name the existing wipe guard treats as real-looking:
    a declared write scope says "this write is sanctioned", never "this graph is
    disposable". Without this, the new door would be strictly weaker than the
    old ones — the first place a whole-graph wipe on production could hide.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope():
        with pytest.raises(RuntimeError, match="non-test graph"):
            guarded.delete()
        with pytest.raises(RuntimeError, match="non-test graph"):
            guarded.execute_command("GRAPH.DELETE", "prod_tortoise")

    assert proj.g.calls == [], (
        f"a wipe reached the vendor handle despite L2; calls={proj.g.calls!r}"
    )


def test_a_destructive_command_naming_another_graph_is_refused():
    """Review finding, now a pin: L2 reads THIS projection's graph name.

    So inside a declared scope, an armed projection named ``test_9209``
    dispatched ``execute_command("GRAPH.DELETE", "prod_tortoise")`` — the L2
    name check passed because it validated the projection's ``test_*`` name
    while the command addressed a production graph on the same server. The
    handle addresses exactly one graph (its own cache); a wipe of any other
    graph is refused here rather than judged by a name it does not own.

    MUTATION: drop the target comparison from
    `_refuse_unauthorized_graph_wipe` and the in-scope half passes the wipe
    through to the vendor handle.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(DirectCacheWriteRefused, match="not 'prod_tortoise'"):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")
    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="not 'prod_tortoise'"
    ):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")

    assert proj.g.calls == [], (
        f"a foreign-graph wipe reached the vendor handle; calls={proj.g.calls!r}"
    )


def test_a_bulk_wipe_hidden_in_a_procedure_call_is_refused():
    """Round 3 finding, now a pin: `call_procedure` pastes caller text into CYPHER.

    The vendor builds ``CALL <procedure>(<args>)`` and sends it through
    ``_query``, so ``proj.g.call_procedure`` was a forwarded attribute that no
    verb-level guard could see — MEASURED before the fix, on an armed
    projection: ``call_procedure("db.labels() MATCH (n) DETACH DELETE n //",
    False)`` emptied the graph while ``proj.g.query`` refused the same DETACH
    DELETE on the same non-test graph. `_GuardedGraph.call_procedure` is now
    defined and runs the statement through the same L2 classifier `query` uses.

    MUTATION: remove `call_procedure` from `_GuardedGraph` (back to a forwarded
    attribute) and this fails — the vendor call is dispatched instead of raising.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.call_procedure(
            "db.labels() MATCH (n) DETACH DELETE n //", read_only=False
        )

    assert proj.g.calls == [], f"the wipe was dispatched: {proj.g.calls!r}"


def test_arguments_cannot_exempt_or_invent_a_wipe():
    """The classified statement must be the one the VENDOR sends.

    The vendor turns ``args`` into ``$paramN`` placeholders and passes the
    values as ``params``, so an argument VALUE is never in the wire statement.
    Rendering the values into the classified text instead — the first version of
    this guard did — is wrong in BOTH directions, and a verifier measured both:
    an arg carrying a scoping construct made `_is_bulk_wipe` read False while the
    vendor still sent the wipe, and a read-only call whose ARG merely contained
    ``DETACH DELETE`` was refused. The wipe is in the ``procedure`` text, so that
    is what must be classified.
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    # (a) an argument whose text LOOKS scoping must not exempt the real wipe
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.call_procedure(
            "db.labels() MATCH (n) DETACH DELETE n //",
            read_only=False,
            args=["\nMATCH (n:Foo) RETURN n"],
        )
    assert proj.g.calls == [], f"the wipe was dispatched: {proj.g.calls!r}"

    # (b) an argument VALUE containing the delete verb must not over-refuse a
    proj.g.calls = []
    proj.g.call_procedure = lambda *a, **k: "OK"
    assert guarded.call_procedure(
        "db.idx.fulltext.queryNodes",
        read_only=True,
        args=["index_name", "MATCH (n) DETACH DELETE n"],
    ) == "OK", "a read-only call was refused for text in an ARGUMENT value"


def test_a_read_only_procedure_call_still_passes(sdk_with_journal):
    """The no-over-refusal half: L2 must not become a wall around procedures.

    ``db.labels()`` is a read. A guard that refused procedure calls wholesale
    would pass the wipe test above and break every diagnostic that uses one.
    """
    proj = sdk_with_journal._get_proj()

    labels = proj.g.call_procedure("db.labels", read_only=True).result_set

    assert labels, "the procedure call returned nothing — it did not reach the graph"
    assert ["Subject"] in labels


def test_delete_judges_the_handles_own_graph_not_the_projections():
    """Review finding, now a pin: `delete()` must name the graph it REMOVES.

    The wrapper is built in-tree over a graph that is not the projection's own
    — `tortoise/hosted_api.py` does `_GuardedGraph(proj.db.select_graph(name),
    proj)` — so judging a `delete()` by `_proj._graph_name` would authorize it
    against a name it does not delete, the same defect round 1 fixed for the
    command channel, one spelling over.

    MUTATION: pass `target=None` from `delete()` (i.e. judge only the
    projection) and the foreign-handle half of this test fails — the in-scope
    delete reaches the vendor handle.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)

    foreign = _NamedRecordingGraph("team_other")
    with _replay_write_scope(), pytest.raises(
        DirectCacheWriteRefused, match="not 'team_other'"
    ):
        _GuardedGraph(foreign, proj).delete()
    assert foreign.calls == [], f"a foreign delete dispatched: {foreign.calls!r}"

    own = _NamedRecordingGraph("test_9209")
    with _replay_write_scope():
        assert _GuardedGraph(own, proj).delete() == "OK"
    assert [c[0] for c in own.calls] == ["delete"], (
        "the declared lane must still reach a disposable graph named like the "
        "projection's"
    )


def test_a_declared_scope_reaches_a_disposable_graph():
    """The declared lane is not dead — otherwise the guard would be a wall.

    This proves the refusal is scoped to `_journal_backed and no scope`, not to
    "delete is always refused". It deliberately uses an `embedded=False`
    projection with a `test_`-named graph, so L2's NAME branch actually runs:
    the embedded exemption returns from `_assert_test_graph` early and would
    keep this case green even if that branch were inverted.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    guarded = _GuardedGraph(proj.g, proj)

    with _replay_write_scope():
        assert guarded.delete() == "OK"
    assert ("delete", (), {}) in proj.g.calls
    assert proj._graph_name.startswith("test_"), (
        "this case must exercise L2's NAME branch (embedded=False), so the "
        "permitted path is proven with the disposability check actually "
        "reading a name — an embedded projection returns from L2 early and "
        "would keep the suite green if that branch inverted"
    )


def test_an_unarmed_projection_still_applies_the_name_rule():
    """L2 is a NAME rule, not an §17.1 concept — so it needs no journal.

    🔴 THE MEASURED DEFECT THIS PIN REPLACES: the whole refusal used to return
    early when ``_journal_backed`` was False, so an UNARMED projection
    dispatched ``proj.g.delete()`` of a non-test SERVER graph while
    ``proj.g.query("MATCH (n) DETACH DELETE n")`` raised ``non-test graph`` on
    the same handle, the same graph and the same state — the asymmetry #9209
    exists to close, and a contract violation of this wrapper's own docstring
    (L2 "is unconditional — there is deliberately NO bypass attribute").

    WHAT IS STILL JOURNAL-SPECIFIC: the DECLARATION check (a scope authorizes
    "this write is a projection of the journal") and the foreign-target check,
    which asks whether a declared lane may touch a graph this projection does
    not own. The server-wide refusal is NOT journal-specific either (a flush on
    an unarmed ``test_*``-named SERVER projection was measured dispatching), and
    neither is the name rule applied to a KEY-SPACE command that names this
    graph — both are pinned below.

    MUTATION: restore the early ``return`` on an unarmed projection and the
    `pytest.raises` below fail (the wipe is dispatched).
    """
    proj = _armed_projection(graph_name="prod_tortoise", embedded=False)
    proj._journal_backed = False
    guarded = _GuardedGraph(proj.g, proj)

    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.delete()
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command("GRAPH.DELETE", "prod_tortoise")
    # The KEY-SPACE spelling of the same wipe, on the same projection: refusing
    # `delete()` while dispatching `DEL <own graph>` is the same asymmetry one
    # spelling over (review round 8 measured it).
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command("DEL", "prod_tortoise")
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.execute_command(f"UNLINK {proj._graph_name}")
    # A server-wide flush needs no name rule to be refused: no disposability
    # check can speak for every graph on the server, armed or not.
    with pytest.raises(DirectCacheWriteRefused, match="EVERY graph"):
        guarded.execute_command("FLUSHALL")
    assert proj.g.calls == [], (
        f"an unarmed projection wiped a non-test server graph: {proj.g.calls!r}"
    )

    # The other half, so the guard is not a wall on an unarmed projection:
    # a read-only probe still answers (`sdk._graph_presence` reads EXISTS and
    # treats a refusal as "do not retry", #7685) ...
    assert guarded.execute_command("EXISTS", "prod_tortoise") == "OK"
    # ... and a key-space command naming a DISPOSABLE graph is dispatched.
    disposable = _armed_projection(graph_name="test_9209_disposable",
                                   embedded=False)
    disposable._journal_backed = False
    handle = _NamedRecordingGraph("test_9209_disposable")
    assert _GuardedGraph(handle, disposable).delete() == "OK"
    assert _GuardedGraph(handle, disposable).execute_command(
        "DEL", "test_9209_disposable") == "OK"
    assert [c[0] for c in handle.calls] == ["delete", "execute_command"], (
        "an unarmed projection must still be able to drop a disposable graph"
    )

    # The statement path keeps the polarity it always had (`query`'s): L2
    # applies armed or not, and `call_procedure` reconstructs a statement.
    with pytest.raises(RuntimeError) as exc:
        guarded.call_procedure("db.labels() MATCH (n) DETACH DELETE n //", False)
    assert "non-test graph" in str(exc.value), (
        "call_procedure must apply L2's name check exactly as `query` does — "
        f"message={str(exc.value)!r}"
    )


def test_an_unarmed_foreign_handle_is_judged_by_the_graph_it_REMOVES():
    """Review round 8 (P3): L2 must judge the TARGET, not the projection's name.

    The wrapper is built in-tree over a graph that is not the projection's
    (`tortoise/hosted_api.py`), so judging by `_proj._graph_name` approved a
    foreign handle's delete of `prod_tenant` purely because the projection
    happened to be named `test_…`. MEASURED on the unarmed path at the head
    before this pin: `OK [('delete', ())]`.

    MUTATION: pass `graph_name=None` (judge the projection) and the refusal
    below disappears.
    """
    proj = _armed_projection(graph_name="test_9209", embedded=False)
    proj._journal_backed = False
    foreign = _NamedRecordingGraph("prod_tenant")

    with pytest.raises(RuntimeError, match="prod_tenant"):
        _GuardedGraph(foreign, proj).delete()
    assert foreign.calls == [], (
        f"a foreign handle wiped a non-test graph: {foreign.calls!r}"
    )


# ── the drift guard: every forwarded verb is classified ───────────────────

#: Non-Cypher verbs the handle FORWARDS to the vendor, each classified by what a
#: call can do to THIS cache. A new vendor verb — or a new wrapper method —
#: fails `test_every_handle_verb_is_classified` until it is declared here, which
#: is the whole point: the #9209 hole was a verb nobody had enumerated.
#:
#: ⛔ `name`, `client` and `schema` are deliberately ABSENT: they are not verbs
#: (`name` is a str, `schema` is the client-side schema cache, `client` is a
#: handle), so the enumeration below does not see them. That is not an oversight
#: being papered over — `client` is escape (3) of the module's SCOPE block by
#: another door (`proj.g.client.execute_command(...)` reaches the same guarded
#: CLIENT, which carries the #3595 operator guard and not §17.1's), and it is
#: named there.
_NON_DESTRUCTIVE_FORWARDED = {
    "copy": "writes this cache INTO another graph; removes nothing here",
    "slowlog": "reads the server's slowlog",
    "slowlog_reset": "clears the server's slowlog, not the cache",
    "list_indices": "reads index DDL",
    "list_constraints": "reads constraint DDL",
}
# Index/constraint DDL: mutates SCHEMA, never corpus nodes, so a call cannot
# destroy the cache an unrelated path armed. The op sets are the vendor's own
# (indices: range/fulltext/vector; constraints: unique/mandatory) — pinned by
# the reverse assertion below, which rejects a ledger entry naming a verb the
# handle does not have.
for _kind in ("node", "edge"):
    for _op in ("range", "fulltext", "vector"):
        _NON_DESTRUCTIVE_FORWARDED[f"create_{_kind}_{_op}_index"] = "schema DDL"
        _NON_DESTRUCTIVE_FORWARDED[f"drop_{_kind}_{_op}_index"] = "schema DDL"
    for _op in ("unique", "mandatory"):
        _NON_DESTRUCTIVE_FORWARDED[f"create_{_kind}_{_op}_constraint"] = "schema DDL"
        _NON_DESTRUCTIVE_FORWARDED[f"drop_{_kind}_{_op}_constraint"] = "schema DDL"

#: Verbs the wrapper defines itself. `delete` is here BECAUSE OF #9209 — it is
#: the destructive one, and defining it is what closes the forwarding hole —
#: and `call_procedure` joined it in round 3: the vendor pastes the caller's
#: procedure string into CYPHER position, so a forwarded attribute was a write
#: channel no verb-level guard could see.
_GUARDED_ON_THE_WRAPPER = frozenset(
    {"query", "ro_query", "_query", "profile", "explain", "execute_command",
     "delete", "call_procedure"}
)

#: Statement-bearing channels that are NOT this suite's: they carry Cypher, so
#: §17.1's statement recovery (#9124) owns them. Declared, not silently ignored.
#: EMPTY since #9209's round 3 — `call_procedure` was the only entry and it is
#: now DEFINED on the wrapper (its reconstructed statement goes through L2), so
#: it moved to `_GUARDED_ON_THE_WRAPPER`. Kept as a named bucket so the next
#: statement-bearing forward has a place to be declared instead of forgotten.
_STATEMENT_BEARING_NOT_CLOSED_HERE: dict[str, str] = {}


def test_every_handle_verb_is_classified(sdk_with_journal):
    """Enumerate the handle's verbs and require each to be guarded or declared.

    The #9209 defect was not "one bad call": it was that a verb DESTROYING the
    cache had never been enumerated, so no guard could be pointed at it. This
    test is the executable form of that enumeration, taken from the handle a
    caller actually holds.

    WHAT IT SEES, stated exactly: every PUBLIC CALLABLE reachable by attribute —
    class-level verbs (``dir(type(handle))``) UNION per-instance bindings
    (``vars(handle)``), because the pinned vendor binds
    ``self.execute_command = client.execute_command`` in ``Graph.__init__`` and a
    class-only enumeration cannot see it. Non-callable attributes are handles
    or data, not verbs: ``name`` is a string, ``schema`` is the client-side
    schema cache (``GraphSchema.clear`` only empties that object, ``refresh``
    reads), and ``client`` is escape (3) of the module's SCOPE block by another
    door. None of the three issues a destructive command.

    WHAT IT CANNOT SEE: a verb reached through a returned object
    (``handle.copy(x).delete()``), a dunder, or ``getattr`` by computed name.
    It is a drift guard over the verb surface, not a completeness proof.
    """
    proj = sdk_with_journal._get_proj()
    handle = proj.g
    inner = handle._g
    wrapper_defined = set()
    for klass in type(handle).__mro__:
        wrapper_defined |= set(klass.__dict__)

    names = {n for n in dir(type(inner)) if not n.startswith("_")}
    names |= {n for n in vars(inner) if not n.startswith("_")}
    public = {n for n in names if callable(getattr(inner, n, None))}
    declared = set(_NON_DESTRUCTIVE_FORWARDED) | set(_STATEMENT_BEARING_NOT_CLOSED_HERE)

    unclassified = public - wrapper_defined - declared
    assert not unclassified, (
        "the write handle forwards these verbs unclassified — decide for each "
        f"whether a call can destroy the cache: {sorted(unclassified)}"
    )
    stale = declared - public
    assert not stale, (
        f"the ledger declares verbs the handle does not have: {sorted(stale)}"
    )
    assert "delete" in wrapper_defined, (
        "`delete` (GRAPH.DELETE) must be DEFINED on the wrapper, not forwarded: "
        "a forwarded attribute is exactly the #9209 hole"
    )
    assert "execute_command" in wrapper_defined, (
        "the raw command channel must be the WRAPPER's (it carries the #9209 "
        "refusal and the #3595 guards), not the vendor's instance binding"
    )
    # Both directions: nothing declared as guarded may actually be forwarded,
    # or the ledger would describe a wrapper that is not the one shipping.
    for name in _GUARDED_ON_THE_WRAPPER:
        assert name in wrapper_defined, f"{name} is declared guarded but is not defined"
