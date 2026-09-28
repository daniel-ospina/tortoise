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
covered. That set is ILLUSTRATIVE, not exhaustive — e.g. ``graph-scripts/*.py``,
``apps/graph-viz/server/main.py``, ``docs/runbook/m2-d3-gold-rank-docker.py``
(``db.select_graph(name).delete()``) and ``tests/`` all construct a vendor
client directly, and any other raw construction is equally uncovered; a guard
there would need the same :func:`guarded_client` call.

There is deliberately NO ``__getattr__`` escape hatch for query entry points on
the guarded graph. An un-overridden ``ro_query`` / ``explain`` / ``profile`` is
exactly how the operator would escape — the failure mode the previous
call-site-only guard had. ``execute_command`` is a special case (the vendor
binds it as an INSTANCE attribute, so a class-level override is shadowed); it is
intercepted explicitly — see :func:`_guarded_execute_command`.

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
#: subclass layer. Read through :func:`_carries_marker`, never a bare
#: ``getattr`` (a permissive ``__getattr__`` object answers every name).
_GUARDED_GRAPH_MARKER = "_tortoise_cypher_guarded_graph"
_GUARDED_CLIENT_MARKER = "_tortoise_cypher_guarded_client"

#: The Redis commands that carry a Cypher statement on a graph handle. Used by
#: the ``execute_command`` interception — the vendor binds
#: ``Graph.execute_command`` to the raw client method as an INSTANCE attribute,
#: so a plain class-level override would be shadowed.
_GRAPH_QUERY_COMMANDS = frozenset(
    {"GRAPH.QUERY", "GRAPH.RO_QUERY", "GRAPH.PROFILE", "GRAPH.EXPLAIN"}
)


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

    String literals (``'`` / ``"``) and backtick-quoted identifiers both end at
    their own quote, but they do NOT share an escape rule:

    * in a STRING literal, openCypher escapes the quote with a backslash
      (``'a\\'b'``) and also accepts a DOUBLED quote (``'a''b'``);
    * in a BACKTICK identifier, there is NO backslash escape — the only way to
      write a backtick inside one is to DOUBLE it (``a``b``). Honouring ``\\``
      there (the pre-fix behaviour) let ``\\``` swallow the closing backtick, so
      a real ``=~`` after it was read as NAME content and the guard MISSED it —
      the exact silent false negative this module exists to stop.

    An unterminated region consumes the remainder — the query is malformed, and
    the guard must not invent an operator from the swallowed bytes.
    """
    i, n = start + 1, len(text)
    if quote == "`":
        while i < n:
            if text[i] == "`":
                if i + 1 < n and text[i + 1] == "`":
                    i += 2
                    continue
                return i + 1
            i += 1
        return n

    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            if i + 1 < n and text[i + 1] == quote:
                i += 2
                continue
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


def _guard_execute_command(args) -> None:
    """Refuse a ``=~`` in a raw ``execute_command`` payload.

    The vendor binds ``Graph.execute_command = client.execute_command`` as an
    INSTANCE attribute, so it is NOT shadowed by a class-level method; the
    guarded seams re-bind it to :func:`_guarded_execute_command` instead.

    Only the four ``GRAPH.*`` query verbs are inspected: their Cypher argument
    is positional (``(cmd, graph_name, cypher, ...)``). Every other command
    (``GRAPH.DELETE``, ``GRAPH.COPY``, ``GRAPH.CONFIG``, ``INFO``, ...) and
    every non-``GRAPH`` command is forwarded untouched.
    """
    if (
        len(args) >= 3
        and isinstance(args[0], str)
        and args[0].upper() in _GRAPH_QUERY_COMMANDS
        and isinstance(args[2], str)
    ):
        _guard_unsupported_cypher(args[2])


def _guarded_execute_command(raw):
    """Wrap a bound ``execute_command`` with :func:`_guard_execute_command`."""

    def _execute_command(*args, **kwargs):
        _guard_execute_command(args)
        return raw(*args, **kwargs)

    return _execute_command


class _UnsupportedOperatorGuardedQueries:
    """Mixin refusing unsupported Cypher operators on a vendor ``Graph``.

    Mixed into a subclass of the vendor graph class (see
    :func:`guarded_graph_class`). Every method that can put a Cypher statement
    on the wire is overridden, so no un-guarded QUERY entry point is left
    reachable through the MRO:

    * ``query`` / ``ro_query`` / ``_query`` — the three query verbs (the
      vendor's ``query``/``ro_query`` both delegate to ``_query``, which is
      itself a public-enough entry point to override);
    * ``profile`` / ``explain`` — accept arbitrary Cypher and issue
      ``GRAPH.PROFILE`` / ``GRAPH.EXPLAIN`` DIRECTLY, they do NOT route through
      ``_query``, so they need their own override;
    * ``execute_command`` — re-bound in :meth:`__init__` (the vendor sets it as
      an INSTANCE attribute, so a method override would lose to it);
    * ``copy`` — returns a new handle, re-wrapped so the returned handle is
      guarded too.
    """

    def __init__(self, client, name):
        # The vendor ``Graph.__init__`` assigns
        # ``self.execute_command = client.execute_command`` — an instance
        # attribute that SHADOWS any method we define on this class. Re-bind it
        # AFTER the vendor assignment so the raw command channel is guarded too.
        super().__init__(client, name)
        self.execute_command = _guarded_execute_command(client.execute_command)

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
        # The vendor ``copy`` returns a RAW vendor handle; re-wrap it so the
        # clone is guarded regardless of the concrete graph class.
        return _guard_handle(super().copy(clone))


class _GuardedHandleProxy:
    """Delegating proxy guarding a handle whose class cannot be subclassed.

    Returned by :func:`_guard_handle` for a DUCK-TYPED handle — a test stub
    exposing only ``query``, a ``Mock``, or any handle that is not a real
    vendor graph. The vendor graph classes are reconstructed as a guarded
    SUBCLASS instead (so their ``isinstance``/class identity is preserved);
    this proxy exists only for handles that contract does not fit. Calling
    ``graph_cls(handle.client, handle.name)`` on such a handle was the round-2
    P1 regression (``AttributeError: '_GraphStub' object has no attribute
    'client'``), which redded ``tests/test_redirect_seam.py``.

    Every query entry point is guarded; ``copy`` re-wraps so a clone stays
    guarded; any other attribute (``name``, ``delete``, ``schema``, ...) is
    forwarded verbatim. Optional query kwargs are forwarded ONLY when supplied,
    so a narrow duck-typed ``query(self, q)`` stays callable.
    """

    __slots__ = ("__weakref__", "_handle")

    def __init__(self, handle):
        self._handle = handle

    def query(self, q, params=None, timeout=None):
        _guard_unsupported_cypher(q)
        return _forward_query(self._handle.query, q, params, timeout)

    def ro_query(self, q, params=None, timeout=None):
        _guard_unsupported_cypher(q)
        return _forward_query(self._handle.ro_query, q, params, timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        _guard_unsupported_cypher(q)
        extra = {"read_only": True} if read_only else {}
        return _forward_query(
            self._handle._query, q, params, timeout, **extra
        )

    def profile(self, query, params=None):
        _guard_unsupported_cypher(query)
        return _forward_query(self._handle.profile, query, params)

    def explain(self, query, params=None):
        _guard_unsupported_cypher(query)
        return _forward_query(self._handle.explain, query, params)

    def execute_command(self, *args, **kwargs):
        _guard_execute_command(args)
        return self._handle.execute_command(*args, **kwargs)

    def copy(self, clone):
        return _guard_handle(self._handle.copy(clone))

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _GuardedClientProxy:
    """Delegating proxy guarding a client INSTANCE that cannot be subclassed.

    Used only when the client construction seam is handed a duck-typed
    FACTORY rather than a class (a ``Mock`` installed by a test, or any
    callable constructor). Class-level subclassing is impossible there, so the
    constructed instance is wrapped and its ``select_graph`` guarded —
    returning the factory untouched would silently disable the guard for a
    permissive object, the hole round 2 flagged.
    """

    __slots__ = ("__weakref__", "_client")

    def __init__(self, client):
        self._client = client

    def select_graph(self, graph_id):
        return _guard_handle(self._client.select_graph(graph_id))

    def __getattr__(self, name):
        return getattr(self._client, name)


def _forward_query(method, q, params=None, timeout=None, **extra):
    """Call *method* forwarding only the optional kwargs actually supplied.

    A real vendor ``query``/``ro_query``/``_query`` accepts ``params`` /
    ``timeout`` / ``read_only``, but a duck-typed stub may declare only ``q``.
    Omitting unset kwargs keeps both call conventions working.
    """
    kwargs = dict(extra)
    if params is not None:
        kwargs["params"] = params
    if timeout is not None:
        kwargs["timeout"] = timeout
    return method(q, **kwargs)


#: One guarded subclass per vendor graph class — cached so ``isinstance`` and
#: class identity are stable across calls.
_GUARDED_GRAPH_CLASSES: dict[type, type] = {}
_GUARDED_CLIENT_CLASSES: dict[type, type] = {}

#: Lazily-resolved real vendor graph base class (``falkordb.Graph``; the
#: embedded ``redislite.Graph`` subclasses it). ``None`` until first use.
_VENDOR_GRAPH_BASE: type | None = None


def _carries_marker(obj, marker: str) -> bool:
    """True only when *marker* is DEFINED on the class or one of its bases.

    ``getattr(obj, marker, False)`` is not a safe test: any object with a
    permissive ``__getattr__`` (a ``Mock``, a proxy) answers with a truthy
    value and would silently skip the guard. Reading ``vars()`` of each class
    in the MRO cannot be forged that way.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    return any(marker in vars(base) for base in getattr(cls, "__mro__", ()))


def _is_vendor_graph(handle) -> bool:
    """Whether *handle* is a real vendor graph, not a duck-typed stub.

    Only a real vendor graph carries the ``(client, name)`` constructor
    contract the guarded-subclass rebuild needs. A duck-typed handle (a test
    stub exposing only ``query``, a ``Mock``) does not, and is wrapped in
    :class:`_GuardedHandleProxy` instead.
    """
    global _VENDOR_GRAPH_BASE
    if _VENDOR_GRAPH_BASE is None:
        try:
            from falkordb import Graph as _VendorGraph
        except Exception:  # pragma: no cover - falkordb is a hard dependency
            return False
        _VENDOR_GRAPH_BASE = _VendorGraph
    return isinstance(handle, _VENDOR_GRAPH_BASE)


def _guard_handle(handle):
    """Return *handle* guarded, whatever shape it is.

    * already-guarded → returned unchanged (idempotent);
    * a real vendor graph → rebuilt as the guarded SUBCLASS, so class identity
      and ``isinstance`` survive;
    * anything else (duck-typed stub, ``Mock``) → wrapped in a delegating
      proxy, because the vendor ``(client, name)`` rebuild contract does not
      apply and forcing it is the round-2 P1 ``AttributeError``.
    """
    graph_cls = guarded_graph_class(type(handle))
    if type(handle) is graph_cls:
        return handle
    if _is_vendor_graph(handle):
        return graph_cls(handle.client, handle.name)
    return _GuardedHandleProxy(handle)


def guarded_graph_class(base_graph: type) -> type:
    """Return a subclass of *base_graph* with the operator guard (cached)."""
    cached = _GUARDED_GRAPH_CLASSES.get(base_graph)
    if cached is not None:
        return cached
    if _carries_marker(base_graph, _GUARDED_GRAPH_MARKER):
        _GUARDED_GRAPH_CLASSES[base_graph] = base_graph
        return base_graph
    cls = type(
        f"Guarded{base_graph.__name__}",
        (_UnsupportedOperatorGuardedQueries, base_graph),
        {_GUARDED_GRAPH_MARKER: True},
    )
    _GUARDED_GRAPH_CLASSES[base_graph] = cls
    return cls


def guarded_client_class(base_client):
    """Return a guarded form of *base_client* (cached).

    The vendor classes (``falkordb.FalkorDB`` for the server lane, the
    redislite subclass for embedded) both pick their graph class inside
    ``select_graph``; wrapping the raw handle's exact class here means the
    guard follows whatever the vendor returns, with no hardcoded graph class.

    A real class is subclassed. A non-class factory (a ``Mock``, a duck-typed
    constructor) cannot be subclassed, so a factory wrapping the constructed
    instance is returned instead — never the object untouched.
    """
    cached = _GUARDED_CLIENT_CLASSES.get(base_client)
    if cached is not None:
        return cached
    if _carries_marker(base_client, _GUARDED_CLIENT_MARKER):
        _GUARDED_CLIENT_CLASSES[base_client] = base_client
        return base_client
    if not isinstance(base_client, type):
        def _guarded_client_factory(*args, **kwargs):
            return _GuardedClientProxy(base_client(*args, **kwargs))

        _guarded_client_factory.__name__ = getattr(
            base_client, "__name__", "guarded_client")
        _GUARDED_CLIENT_CLASSES[base_client] = _guarded_client_factory
        return _guarded_client_factory

    class _GuardedClient(base_client):
        """Vendor FalkorDB client whose every graph handle is guarded."""

        def select_graph(self, graph_id):
            return _guard_handle(super().select_graph(graph_id))

    _GuardedClient.__name__ = f"Guarded{base_client.__name__}"
    _GuardedClient.__qualname__ = _GuardedClient.__name__
    setattr(_GuardedClient, _GUARDED_CLIENT_MARKER, True)
    _GUARDED_CLIENT_CLASSES[base_client] = _GuardedClient
    return _GuardedClient


def guarded_client(base_client, *args, **kwargs):
    """Construct a guarded instance of *base_client* (the ONE construction seam).

    Every FalkorDB client the ``tortoise`` package builds — the projection's
    embedded and Docker clients, the BGSAVE helper, the session indexer, the
    doctor probe — goes through here, so every handle they yield is guarded
    regardless of which call site queries it. A raw vendor client built
    *outside* the package (e.g. ``graph-scripts/``, ``apps/graph-viz/``,
    ``docs/runbook/``, ``tests/``) is not covered; see the module docstring for
    that scope boundary.
    """
    return guarded_client_class(base_client)(*args, **kwargs)
