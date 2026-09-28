"""#3595 — refuse unsupported Cypher operators (``=~``) before dispatch.

FalkorDB implements no Cypher regex-match operator. ``=~`` does not raise: it
prints ``FalkorDB does not currently support =~`` IN PLACE OF RESULTS and the
surrounding query returns an EMPTY result set — a confident false negative
that is indistinguishable from "no matches". Two agents in one session read
that as truth: enumerating legacy ``obj-<26hex>`` ids across every graph with
``=~ '^[a-z]{2,3}-[0-9a-f]{26}$'`` returned 0 everywhere, where the supported
``STARTS WITH`` found 5 nodes in 2 graphs.

This is a LANDMINE, not a live bug: no shipped Cypher uses ``=~`` (the ``=~``
in ``sdk._DIGEST_STRUCTURE_RE`` is a PYTHON character class and never reaches
this module).

**THE SEAM.** The guard cannot live at query CALL SITES. This repo reaches a
FalkorDB handle through many paths: ``proj.g`` (the projection's write handle),
the ~142 ``reg.query(...)`` sites behind ``TortoiseSDK._get_registry``, the
direct ``select_graph(...).query(...)`` sites in ``hosted_api`` / ``backup_sweep``
/ ``hosted_backup`` / ``navigation`` / ``memory_orchestrator`` /
``session_indexer``, and helpers that accept a raw handle
(``hosted_backup.restore_graph``, ``pack_state._target_graph``). Only a guard on
the HANDLE ITSELF covers all of them, so this module wraps the vendor CLIENT:
its ``select_graph`` returns a guarded ``Graph`` subclass that overrides EVERY
query entry point.

**SCOPE — what this covers, and what it does not.** Inside the ``tortoise``
package every FalkorDB client is constructed through :func:`guarded_client`
(the projection's embedded and Docker clients, the BGSAVE helper, the session
indexer, the doctor probe), so every handle those clients yield — including the
``select_graph(...).query(...)`` sites and the SDK registry handles — is
guarded. Code OUTSIDE the package that builds a raw vendor client is NOT
covered: ``graph-scripts/*.py`` and ``apps/graph-viz/server/main.py`` construct
``falkordb.FalkorDB`` directly. Those are operator scripts, not product
surfaces; a guard there would need the same ``guarded_client`` call.

There is deliberately NO ``__getattr__`` escape hatch for query entry points on
the guarded graph. An un-overridden ``ro_query`` / ``explain`` / ``profile`` is
exactly how the operator would escape — the failure mode the previous
call-site-only guard had.

**The scan** is a single left-to-right pass that keeps state, so ``=~`` is
reported only where it is CODE. Inside a quoted literal (``RETURN 'a =~ b'``) it
is DATA; inside a backtick-quoted identifier (``MATCH (n:`a=~b`)``) it is a
NAME; inside a Cypher comment (``// =~``, ``/* =~ */``) it is prose. None of
those may trip the guard — a false POSITIVE would block a legitimate query,
which is its own class of harm even though it is loud rather than silent.
"""
from __future__ import annotations

from tortoise.exceptions import UnsupportedCypherOperatorError

#: Marker set on the generated classes so the factories are idempotent —
#: wrapping an already-guarded client/handle again is a no-op, never a second
#: subclass layer.
_GUARDED_GRAPH_MARKER = "_tortoise_cypher_guarded_graph"
_GUARDED_CLIENT_MARKER = "_tortoise_cypher_guarded_client"


def _unsupported_cypher_operator(cypher: str) -> str | None:
    """Return the first unsupported Cypher operator in *cypher*, else None.

    Only the Cypher regex-match operator ``=~`` is currently known. The scan
    skips quoted string literals, backtick-quoted identifiers and Cypher
    comments, so only an OPERATOR — never data, a name, nor prose — is
    reported. Python-side regexes never pass through this function (#3595).
    """
    if not isinstance(cypher, str) or "=~" not in cypher:
        return None
    i, n = 0, len(cypher)
    while i < n:
        c = cypher[i]
        if c in ("'", '"', "`"):
            i = _skip_cypher_quoted(cypher, i, c)
        elif c == "/" and i + 1 < n and cypher[i + 1] == "/":
            nl = cypher.find(chr(10), i + 2)
            i = n if nl == -1 else nl + 1
        elif c == "/" and i + 1 < n and cypher[i + 1] == "*":
            end = cypher.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif c == "=" and i + 1 < n and cypher[i + 1] == "~":
            return "=~"
        else:
            i += 1
    return None


def _skip_cypher_quoted(text: str, start: int, quote: str) -> int:
    """Return the index just past the quoted region opening at *start*.

    Used for both string literals (``'`` / ``"``) and backtick-quoted
    identifiers (``\\```). Backslash escapes are honoured (``'a\\'b'`` is one
    literal). An unterminated region consumes the remainder — the query is
    malformed, and the guard must not invent an operator from the swallowed
    bytes.
    """
    i, n = start + 1, len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return n


def _guard_unsupported_cypher(cypher: str) -> None:
    """Raise :class:`UnsupportedCypherOperatorError` if *cypher* uses ``=~``.

    The single place the operator check is TURNED INTO a refusal — every guarded
    entry point and ``projection._GuardedGraph.query`` call this, so the
    decision has exactly one home.
    """
    op = _unsupported_cypher_operator(cypher)
    if op is not None:
        raise UnsupportedCypherOperatorError(op, cypher)


class _UnsupportedOperatorGuardedQueries:
    """Mixin refusing unsupported Cypher operators on a vendor ``Graph``.

    Mixed into a subclass of the vendor graph class (see
    :func:`guarded_graph_class`). Every method that can put a query string on
    the wire is overridden, so no un-guarded entry point is left reachable
    through the MRO:

    * ``query`` / ``ro_query`` / ``_query`` — the three query verbs (the
      vendor's ``query``/``ro_query`` both delegate to ``_query``, which is
      itself a public-enough entry point to override);
    * ``profile`` / ``explain`` — accept arbitrary Cypher and issue
      ``GRAPH.PROFILE`` / ``GRAPH.EXPLAIN`` DIRECTLY, they do NOT route through
      ``_query``, so they need their own override;
    * ``copy`` — returns a new handle, re-wrapped so the returned handle is
      guarded too.
    """

    def query(self, q, params=None, timeout=None):
        _guard_unsupported_cypher(q)
        return super().query(q, params=params, timeout=timeout)

    def ro_query(self, q, params=None, timeout=None):
        _guard_unsupported_cypher(q)
        return super().ro_query(q, params=params, timeout=timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        _guard_unsupported_cypher(q)
        return super()._query(
            q, params=params, timeout=timeout, read_only=read_only
        )

    def profile(self, query, params=None):
        _guard_unsupported_cypher(query)
        return super().profile(query, params=params)

    def explain(self, query, params=None):
        _guard_unsupported_cypher(query)
        return super().explain(query, params=params)

    def copy(self, clone):
        super().copy(clone)
        return type(self)(self.client, clone)


#: One guarded subclass per vendor graph class — cached so ``isinstance`` and
#: class identity are stable across calls.
_GUARDED_GRAPH_CLASSES: dict[type, type] = {}
_GUARDED_CLIENT_CLASSES: dict[type, type] = {}


def guarded_graph_class(base_graph: type) -> type:
    """Return a subclass of *base_graph* with the operator guard (cached)."""
    cached = _GUARDED_GRAPH_CLASSES.get(base_graph)
    if cached is not None:
        return cached
    if getattr(base_graph, _GUARDED_GRAPH_MARKER, False):
        _GUARDED_GRAPH_CLASSES[base_graph] = base_graph
        return base_graph
    cls = type(
        f"Guarded{base_graph.__name__}",
        (_UnsupportedOperatorGuardedQueries, base_graph),
        {_GUARDED_GRAPH_MARKER: True},
    )
    _GUARDED_GRAPH_CLASSES[base_graph] = cls
    return cls


def guarded_client_class(base_client: type) -> type:
    """Return a subclass of *base_client* whose ``select_graph`` is guarded.

    The vendor classes (``falkordb.FalkorDB`` for the server lane, the
    redislite subclass for embedded) both pick their graph class inside
    ``select_graph``; wrapping the raw handle's exact class here means the
    guard follows whatever the vendor returns, with no hardcoded graph class.
    """

    cached = _GUARDED_CLIENT_CLASSES.get(base_client)
    if cached is not None:
        return cached
    if getattr(base_client, _GUARDED_CLIENT_MARKER, False):
        _GUARDED_CLIENT_CLASSES[base_client] = base_client
        return base_client

    class _GuardedClient(base_client):
        """Vendor FalkorDB client whose every graph handle is guarded."""

        def select_graph(self, graph_id):
            handle = super().select_graph(graph_id)
            graph_cls = guarded_graph_class(type(handle))
            if type(handle) is graph_cls:
                return handle
            # Rebuild through the vendor's own ``(client, name)`` contract —
            # both families (falkordb.Graph and redislite.Graph) take exactly
            # those two arguments, and ``handle.client`` is what the vendor
            # itself passes to ``Graph`` (the Redis connection for embedded,
            # the client for server).
            return graph_cls(handle.client, handle.name)

    _GuardedClient.__name__ = f"Guarded{base_client.__name__}"
    _GuardedClient.__qualname__ = _GuardedClient.__name__
    setattr(_GuardedClient, _GUARDED_CLIENT_MARKER, True)
    _GUARDED_CLIENT_CLASSES[base_client] = _GuardedClient
    return _GuardedClient


def guarded_client(base_client: type, *args, **kwargs):
    """Construct a guarded instance of *base_client* (the ONE construction seam).

    Every FalkorDB client the ``tortoise`` package builds — the projection's
    embedded and Docker clients, the BGSAVE helper, the session indexer, the
    doctor probe — goes through here, so every handle they yield is guarded
    regardless of which call site queries it. A raw vendor client built
    *outside* the package (``graph-scripts/``, ``apps/graph-viz/``) is not
    covered; see the module docstring for that scope boundary.
    """
    return guarded_client_class(base_client)(*args, **kwargs)
