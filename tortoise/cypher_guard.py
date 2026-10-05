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

**The second invariant (#4647/#7174) — the store's numeric domain.** FalkorDB
stores an integer as INT64 and a number as a double, so a param outside those
domains is SILENTLY ALTERED (``SET n.v = $v`` reports success and stores
``9223372036854775807`` for ``2**70``). The predicate lives in
:mod:`tortoise.numeric_domain`; this seam applies it to every param map on every
guarded handle, so a writer is covered without anyone remembering to add a call
site. It is a PARAM boundary — what it excludes is listed once, in
:func:`_guard_numeric_params` — and the journal WRITE path is exempt
(:func:`tolerating_altered_numbers`: ``apply``, ``rebuild``, ``rebuild_all``,
``recover_from_log``, ``backup.restore``). Applying a journal record whose number
the store cannot hold is a divergence for ``check_consistency`` to report, never
a crash (#5011), so every whole-journal replay engine runs with the refusal
suspended; a caller-driven write at the param boundary is still refused.

**The scan** is a single left-to-right pass that keeps state, so ``=~`` is
reported only where it is CODE. Inside a quoted literal (``RETURN 'a =~ b'``) it
is DATA; inside a backtick-quoted identifier (``MATCH (n:`a=~b`)``) it is a
NAME; inside a Cypher comment (``// =~``, ``/* =~ */``) it is prose. None of
those may trip the guard — a false POSITIVE would block a legitimate query,
which is its own class of harm even though it is loud rather than silent.
"""
from __future__ import annotations

import contextvars
import functools
from contextlib import contextmanager

from tortoise.exceptions import (
    UnrepresentableNumberError,
    UnsupportedCypherOperatorError,
)
from tortoise.numeric_domain import numeric_alteration_reason

#: Marker set on the generated classes so the factories are idempotent —
#: wrapping an already-guarded client/handle again is a no-op, never a second
#: subclass layer. Read through :func:`_carries_marker`, never a bare
#: ``getattr`` (a permissive ``__getattr__`` object answers every name).
_GUARDED_GRAPH_MARKER = "_tortoise_cypher_guarded_graph"

# #6072: the guard builds a SUBCLASS of the vendor graph class so that no
# un-guarded query verb is reachable through the MRO (that isolation is
# deliberate — see _UnsupportedOperatorGuardedQueries — and must NOT be traded
# away for an instance-attribute patch, which `del handle.query` would undo).
#
# The cost of that isolation is that `type(handle)` is no longer the vendor
# class, and any consumer keying on the concrete class changes behaviour
# (tests/test_hosted_api.py monkeypatches `query` at vendor-class level and
# dispatches via `_orig_query[type(self)]` -> KeyError -> the required
# python-ci-gate leg fails).
#
# The remedy is to make the vendor class REACHABLE AS A PUBLISHED CONTRACT
# rather than inferred: the generated class carries its base here, and
# `unguarded_graph_class()` reads it. The guard keeps MRO-level isolation and
# consumers stop guessing at identity.
_GUARDED_GRAPH_BASE_MARKER = "_tortoise_cypher_unguarded_base"
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
            # A ``//`` comment ends at the first newline of EITHER convention:
            # terminating on ``\n`` alone let a bare ``\r`` (classic-Mac
            # ending) swallow the remainder, so a real ``=~`` after it was
            # never reported (#3595 review).
            stop = min(
                [j for j in (cypher.find("\n", i + 2), cypher.find("\r", i + 2))
                 if j != -1], default=-1)
            i = n if stop == -1 else stop + 1
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


def _guard_numeric_params(params) -> None:
    """#7174: refuse a param the store would silently ALTER, on any handle.

    The predicate is ``tortoise.numeric_domain``'s — the same one the SDK's
    #4647 call sites use, so the early refusal and this one cannot drift.

    Only a ``dict`` is a param map: that is what the vendor's
    ``_build_params_header`` accepts, so any other shape is the vendor's own
    error and is forwarded rather than refused by a guess.

    **The journal WRITE path is exempt — see :func:`tolerating_altered_numbers`.**
    A caller can pass a string instead of the number, so a caller-driven write at
    the param boundary is refused; a journal RECORD cannot be re-authored at replay
    time, and #5011 decided that applying one whose number the store cannot hold is
    a DIVERGENCE for ``check_consistency`` to report, never a crash. ``apply``, the
    rebuilds and ``recover_from_log`` therefore run with the refusal suspended —
    including ``apply``'s live callers, a residual stated in full on the
    decorator's own docstring.

    NOT covered here, deliberately: a numeric LITERAL in the statement text
    (``{v: 1180591620717411303424}`` with no ``params``) and a raw vendor client
    the package did not build are separate mechanisms with their own homes — see
    the SCOPE paragraph above and issue #7174, which also carries the analysis of
    a number already serialized into a STRING param (a JSON payload).
    """
    if not isinstance(params, dict):
        return
    if _TOLERATE_ALTERED_NUMBERS.get():
        return
    for key, value in params.items():
        reason = numeric_alteration_reason(key, value)
        if reason:
            raise UnrepresentableNumberError(reason)


def _guard_query(cypher: str, params=None) -> None:
    """Both pre-dispatch refusals in one call: unsupported operator, then domain."""
    _guard_unsupported_cypher(cypher)
    _guard_numeric_params(params)


#: #7174/#5011: set while a REPLAY is writing. A journal is a FILE — a record
#: written by an older revision, or hand-edited, can carry a number the store
#: cannot hold — and #5011 decided that replaying one must NOT crash the run:
#: the store clamps it and ``check_consistency`` reports the divergence. A
#: caller-driven write is refused instead, because the caller has a faithful
#: representation available (a string) and can choose it. Per-context (never a
#: module global), so a tolerance in one thread or task cannot leak into another.
_TOLERATE_ALTERED_NUMBERS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tortoise_tolerate_altered_numbers", default=False
)


@contextmanager
def tolerating_altered_numbers():
    """Suspend the numeric-domain refusal for a REPLAY write (#5011)."""
    token = _TOLERATE_ALTERED_NUMBERS.set(True)
    try:
        yield
    finally:
        _TOLERATE_ALTERED_NUMBERS.reset(token)


def tolerates_altered_numbers(fn):
    """Decorator form of :func:`tolerating_altered_numbers` — the journal WRITE path.

    Applied to ``FalkorProjection.apply`` #5011, ``FalkorProjection.rebuild``,
    ``FalkorProjection.rebuild_all``, ``consistency.recover_from_log`` and
    ``backup.restore`` — every whole-journal replay engine in the package.

    **Why ``apply`` is in that set.** #5011 decided that a journal record carrying
    a number the store cannot hold is a DIVERGENCE for ``check_consistency`` to
    report, never a crash, and its reachability story is a record applied to the
    projection (``test_an_over_range_int_is_a_divergence_not_a_crash`` seeds
    through ``apply``): the store clamps and the check reports it. Refusing there
    would reverse that decision, so ``apply`` keeps it.

    **The residual, stated rather than hidden.** ``apply`` is ALSO a live-write
    primitive for the paths that bypass the SDK's sanitize boundary:
    ``api.EventAPI._emit``, the connectors and the indexers call it directly and
    carry no ``_reject_unrepresentable_number`` guard, so a caller's out-of-domain
    value on THOSE paths is clamped rather than refused. (The SDK's entity path is
    not part of this residual — ``_create_entity`` runs ``_sanitize_props``,
    which refuses at sdk.py:2607, so the early guard still fires there; only
    ``sdk.py:20941``'s ``_skip_sanitize`` branch reaches ``apply`` unguarded, and
    its ``create_source`` caller guards the assembled event itself.) Closing the
    remaining paths would refuse a live write where #5011 expects the value to
    land and diverge, so it is a DECISION, not an oversight; evidence on #7174.
    """

    @functools.wraps(fn)
    def _replay_entry(*args, **kwargs):
        with tolerating_altered_numbers():
            return fn(*args, **kwargs)

    return _replay_entry


def _as_text(value):
    """Decode a bytes command/payload so the scan can see its text, else return it.

    redis-py accepts the BYTES form of ``execute_command``, and the scanner
    reports ``None`` for anything that is not ``str`` — so without this a bytes
    payload would pass the guard untouched (#3595 review).
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return value


def _guard_execute_command(args) -> None:
    """Refuse a ``=~`` in a raw ``execute_command`` payload.

    The vendor binds ``Graph.execute_command = client.execute_command`` as an
    INSTANCE attribute, so it is NOT shadowed by a class-level method; the
    guarded seams re-bind it to :func:`_guarded_execute_command` instead.

    Only the four ``GRAPH.*`` query verbs are inspected: their Cypher argument
    is positional (``(cmd, graph_name, cypher, ...)``). Every other command
    (``GRAPH.DELETE``, ``GRAPH.COPY``, ``GRAPH.CONFIG``, ``INFO``, ...) and
    every non-``GRAPH`` command is forwarded untouched.

    #7174 — deliberately NO numeric check here, and the reason is MEASURED, not
    assumed: a raw ``GRAPH.QUERY`` has no positional params argument. The pinned
    vendor (``falkordb`` 1.6.2, ``graph.py::_query``) builds its param header INTO
    the statement (``self._build_params_header(params) + q``) and sends
    ``[cmd, name, query, "--compact"]``, appending ``["timeout", <int>]`` only
    when a timeout was given — so ``args[3]`` is a flag such as ``--compact``,
    never a JSON payload. Params on this channel are
    therefore inlined as TEXT (``CYPHER k=<v>``), which is a statement-literal
    channel, not a param map: covered on the ``params=`` path by
    :func:`_guard_numeric_params` before the vendor serializes them, and left
    uncovered as a literal (see its docstring).
    """
    if len(args) >= 3:
        verb = _as_text(args[0])
        statement = _as_text(args[2])
        if (
            isinstance(verb, str)
            and verb.upper() in _GRAPH_QUERY_COMMANDS
            and isinstance(statement, str)
        ):
            _guard_unsupported_cypher(statement)


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
        _guard_query(q, params)
        return super().query(q, params=params, timeout=timeout)

    def ro_query(self, q, params=None, timeout=None):
        _guard_query(q, params)
        return super().ro_query(q, params=params, timeout=timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        _guard_query(q, params)
        return super()._query(
            q, params=params, timeout=timeout, read_only=read_only
        )

    def profile(self, query, params=None):
        _guard_query(query, params)
        return super().profile(query, params=params)

    def explain(self, query, params=None):
        _guard_query(query, params)
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
        _guard_query(q, params)
        return _forward_query(self._handle.query, q, params, timeout)

    def ro_query(self, q, params=None, timeout=None):
        _guard_query(q, params)
        return _forward_query(self._handle.ro_query, q, params, timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        _guard_query(q, params)
        extra = {"read_only": True} if read_only else {}
        return _forward_query(
            self._handle._query, q, params, timeout, **extra
        )

    def profile(self, query, params=None):
        _guard_query(query, params)
        return _forward_query(self._handle.profile, query, params)

    def explain(self, query, params=None):
        _guard_query(query, params)
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

    __slots__ = ("__weakref__", "_client", "execute_command")

    def __init__(self, client):
        self._client = client
        # Guard the client's own command channel too: it is an instance
        # attribute on the vendor client, so ``__getattr__`` would otherwise
        # hand back the unguarded bound method (#3595 review).
        raw = getattr(client, "execute_command", None)
        if raw is not None:
            self.execute_command = _guarded_execute_command(raw)

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
    # #7174: defence in depth — the proxy's verbs already guard, so this is
    # normally a second (cheap) pass over the same map. It exists so that a verb
    # added to THIS proxy later cannot forward params unguarded by forgetting
    # `_guard_query`. (The vendor-subclass mixin does not route through here — it
    # calls `super()` directly, so its verbs must keep guarding themselves.)
    _guard_numeric_params(params)
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
        {_GUARDED_GRAPH_MARKER: True,
         _GUARDED_GRAPH_BASE_MARKER: base_graph},
    )
    _GUARDED_GRAPH_CLASSES[base_graph] = cls
    return cls


def unguarded_graph_class(graph_cls: type) -> type:
    """The VENDOR graph class a guarded class was built from (#6072).

    The guard subclasses the vendor graph class so no un-guarded query verb is
    reachable through the MRO. That isolation means ``type(handle)`` is a
    generated subclass, so any consumer keying on the concrete class breaks
    (measured: ``tests/test_hosted_api.py`` monkeypatches ``query`` at
    vendor-class level and dispatches via ``_orig_query[type(self)]``, raising
    ``KeyError`` and failing the REQUIRED ``python-ci-gate`` leg).

    Consumers that need the vendor class must ask for it here rather than infer
    it from ``type()`` — this is the supported contract, and it stays correct if
    the guard ever changes how it isolates the query verbs.

    A class that was never guarded is returned unchanged, so callers need no
    special case.
    """
    return getattr(graph_cls, _GUARDED_GRAPH_BASE_MARKER, graph_cls)


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

    class _GuardedClient(base_client):  # type: ignore[valid-type]  # a class object, not a type alias (#5414)
        """Vendor FalkorDB client whose every graph handle is guarded."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # The vendor binds ``self.execute_command`` as an INSTANCE attribute
            # (see :func:`_guard_execute_command`), so the client's OWN command
            # channel carried Cypher unrefused while every handle verb was
            # guarded — the same shadowed bypass this module closes at handle
            # level, one level up (#3595 review). Reading it back through
            # ``self`` finds the instance attribute when the vendor set one and
            # the class method otherwise. A duck-typed base that has no command
            # channel at all is left alone.
            raw = getattr(self, "execute_command", None)
            if raw is not None:
                self.execute_command = _guarded_execute_command(raw)

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
