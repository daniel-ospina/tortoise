"""#7357 — the instance going away says so, instead of reading as a broken graph.

The issue's symptom block lists TWO errors, and only one of them had been made
legible:

* ``ResponseError: graph was deleted or replaced while the query was running``
  — explained by ``retry.explain_graph_abort`` on the write retry's exhaustion
  arm (#8954);
* ``TimeoutError: Timeout reading from socket`` (and, same family,
  ``ConnectionError: Connection closed by server.``) — the INSTANCE quitting
  mid-request, which still arrived as the bare driver sentence on every verb,
  reads as well as writes, through every handle.

The issue's own 2026-10-13 measurement is what this file pins: a bare
transport error died *inside a test run* and was indistinguishable from the
test's own defect, costing a full diagnostic cycle. The message must name the
mechanism (the instance stopped answering: a restart/reload/OOM-kill, or a
client timeout), must NOT read as graph damage, and — because a lost reply is
not a lost write (#7405) — must never invite a blind re-issue of a statement
that can mutate.

Wiring is tested where the repo's seam is: ``cypher_guard``'s guarded command
channel — the ONE channel every GUARDED FalkorDB client's command passes through
(handle verbs, the client's own ``execute_command`` and the connection's) — plus
the duck-typed handle proxy, which dispatches straight to its wrapped handle and
therefore carries its own application of the same note. The tests below drive a
real ``guarded_client`` so removing the wiring — not merely the helper — reddens
them.
"""
from __future__ import annotations

import pytest

redis_exc = pytest.importorskip("redis.exceptions")

from tortoise import cypher_guard  # noqa: E402
from tortoise.retry import (  # noqa: E402
    explain_instance_unavailable,
    is_dead_reader,
    is_instance_unavailable,
    retryable_transient,
)
from tortoise.sdk import _classify_db_failure  # noqa: E402

READ_STATEMENT = "MATCH (n:Point {id:$id}) RETURN n.id"
WRITE_STATEMENT = "CREATE (n:Point {id:$id}) RETURN n.id"

#: The two sentences the issue measured, plus the handshake a restarted
#: FalkorDB answers with while it reloads its RDB.
SOCKET_TIMEOUT = "Timeout reading from socket"
CONNECTION_CLOSED = "Connection closed by server."
LOADING = "LOADING Redis is loading the dataset in memory"


class _DeadGraph:
    """Vendor-shaped graph handle whose every query reaches *client*."""

    def __init__(self, client, name):
        self.client = client
        self.name = name
        # The vendor binds this as an INSTANCE attribute; the guarded client has
        # already rebound it, which is the wiring under test.
        self.execute_command = client.execute_command

    def query(self, q, params=None, timeout=None):
        return self._query(q, params=params, timeout=timeout)

    def ro_query(self, q, params=None, timeout=None):
        return self._query(q, params=params, timeout=timeout, read_only=True)

    def _query(self, q, params=None, timeout=None, read_only=False):
        return self.execute_command(
            "GRAPH.RO_QUERY" if read_only else "GRAPH.QUERY", self.name, q)


class _DeadClient:
    """Vendor-shaped client whose every command raises ``cls.error``."""

    error: BaseException = redis_exc.TimeoutError(SOCKET_TIMEOUT)

    def __init__(self):
        self.connection = None
        self.execute_command = self._execute

    def _execute(self, *args, **kwargs):
        # A FRESH instance per command, as a real driver does: the channel's note
        # is idempotent per exception, so a shared instance would swallow it.
        raise type(self.error)(*self.error.args)

    def select_graph(self, name):
        return _DeadGraph(self, name)


def _dead_client(error: BaseException):
    """A guarded client whose commands fail with *error* (one class per error)."""
    base = type("DeadClient", (_DeadClient,), {"error": error})
    return cypher_guard.guarded_client(base)


def _raise_from_handle(error, statement, *, read_only=False):
    """Run *statement* through the guarded client's HANDLE path, return the error."""
    client = _dead_client(error)
    graph = client.select_graph("tortoise")
    with pytest.raises(type(error)) as caught:
        if read_only:
            graph.ro_query(statement)
        else:
            graph.query(statement)
    return caught.value


# ── the note itself ────────────────────────────────────────────────────────


def test_a_socket_timeout_on_a_read_names_the_instance_and_calls_a_reissue_safe():
    """The issue's first measured error, on the read shape a probe actually used."""
    exc = _raise_from_handle(redis_exc.TimeoutError(SOCKET_TIMEOUT), READ_STATEMENT)

    message = str(exc)
    # The driver sentence stays at position 0: every caller that matches on it
    # (and every retry predicate) reads from there.
    assert message.startswith(SOCKET_TIMEOUT)
    assert "quit answering in the middle of this request" in message
    assert "container restart/reload or an OOM-kill" in message
    # The misdescription this issue is titled after is REFUSED, not repeated.
    assert "NOT the engine's 'graph was deleted or replaced' abort" in message
    assert "not evidence that the graph or its data was lost" in message
    # A read cannot have changed anything, so the next action is stated.
    assert "re-issuing this statement is safe" in message


def test_a_socket_timeout_on_a_write_leaves_the_outcome_unknown_and_never_calls_it_safe():
    """A lost reply is not a lost write (#7405): the note must not invite a retry.

    The ISSUE's own suggested wording was "retry; your data is intact". For a
    mutating statement that is a duplication hazard — ``create_point`` issues a
    bare ``CREATE`` with no uniqueness constraint — so the note says UNKNOWN and
    tells the caller to read back first.
    """
    exc = _raise_from_handle(redis_exc.TimeoutError(SOCKET_TIMEOUT), WRITE_STATEMENT)

    message = str(exc)
    assert message.startswith(SOCKET_TIMEOUT)
    assert "OUTCOME IS UNKNOWN" in message
    assert "read back what you wrote before re-issuing" in message
    assert "can duplicate it" in message
    # The read-arm reassurance must not leak into the write arm.
    assert "re-issuing this statement is safe" not in message


def test_a_write_is_classified_as_a_write_even_when_it_is_a_merge_rather_than_a_create():
    """One classifier decides, so the arm follows ``_statement_writes`` alone."""
    exc = _raise_from_handle(
        redis_exc.ConnectionError(CONNECTION_CLOSED),
        "MERGE (p:Point {id:$id}) SET p.content = $content",
    )
    assert "OUTCOME IS UNKNOWN" in str(exc)


def test_a_command_with_no_statement_is_left_unknown_rather_than_called_safe():
    client = _dead_client(redis_exc.TimeoutError(SOCKET_TIMEOUT))
    with pytest.raises(redis_exc.TimeoutError) as caught:
        client.execute_command("PING")

    message = str(caught.value)
    assert message.startswith(SOCKET_TIMEOUT)
    assert "Whether the command took effect is UNKNOWN" in message
    assert "re-issuing this statement is safe" not in message


def test_a_destructive_command_with_no_statement_is_never_called_safe():
    client = _dead_client(redis_exc.ConnectionError(CONNECTION_CLOSED))
    with pytest.raises(redis_exc.ConnectionError) as caught:
        client.execute_command("GRAPH.DELETE", "tortoise")

    message = str(caught.value)
    assert "OUTCOME IS UNKNOWN" in message
    assert "re-issuing this statement is safe" not in message


def test_a_write_through_the_read_only_verb_still_says_the_outcome_is_unknown():
    """``ro_query``'s refusal of a write is ENGINE POLICY, not a property of the verb.

    v4.20.6 answers ``ResponseError("graph.RO_QUERY is to be executed only on
    read-only queries")`` and applies nothing, so treating the verb as
    non-executing was true THERE. It is unmeasured on falkordb 6.x (production)
    and on the embedded engine, and the projection records the opposite reading
    for the same verb — "The 'read-only' verb is mutating-CAPABLE … a statement
    handed to it is dispatched the same way". The asymmetry decides the arm: a
    wrong "safe to re-issue" duplicates a bare non-idempotent ``CREATE`` (the
    ``retryable_aborted_write`` class) while a wrong "OUTCOME UNKNOWN" only asks
    for a read-back on the engine that refused.

    P2 (review cycle 4) — the arm used to be ``False`` and this test pinned it.
    """
    exc = _raise_from_handle(
        redis_exc.TimeoutError(SOCKET_TIMEOUT), WRITE_STATEMENT, read_only=True)
    message = str(exc)
    assert "OUTCOME IS UNKNOWN" in message
    assert "re-issuing this statement is safe" not in message

    # …and the safe arm SURVIVES for the traffic that actually uses the verb.
    read = _raise_from_handle(
        redis_exc.TimeoutError(SOCKET_TIMEOUT), READ_STATEMENT, read_only=True)
    assert "re-issuing this statement is safe" in str(read)
    assert "OUTCOME IS UNKNOWN" not in str(read)
    # The channel-level classifier agrees with the handle-level one.
    assert cypher_guard._command_mutating(
        ("GRAPH.RO_QUERY", "proj.g", WRITE_STATEMENT)) is True
    assert cypher_guard._command_mutating(
        ("GRAPH.RO_QUERY", "proj.g", READ_STATEMENT)) is False


def test_a_restart_handshake_says_the_instance_is_loading_and_safe_to_reissue():
    """``LOADING`` IS-A ``ConnectionError`` on the pinned redis — same family.

    It is the one family member the instance ANSWERED, so it must not borrow the
    "quit answering" wording: the engine rejected the command before running it,
    which makes a re-issue safe for a READ and a WRITE alike.
    """
    exc = _raise_from_handle(redis_exc.BusyLoadingError(LOADING), WRITE_STATEMENT)

    message = str(exc)
    assert message.startswith(LOADING)
    assert "it is restarting and loading its dataset from disk" in message
    assert "rejected BEFORE it ran" in message
    assert "re-issuing it once the load completes is safe" in message
    # The transport arm's claims would both be false here.
    assert "quit answering in the middle of this request" not in message
    assert "OUTCOME IS UNKNOWN" not in message


def test_a_credential_refusal_is_never_reported_as_a_dead_instance():
    """P1 (review): ``AuthenticationError``/``AuthorizationError``/
    ``MaxConnectionsError``/``ExternalAuthProviderError`` all SUBCLASS
    ``ConnectionError`` on the pinned redis, so a plain family test would tell an
    operator with a WRONGPASS or an ACL ``NOPERM`` that their box had restarted —
    and call a permanent error safe to re-issue. The repo records the same trap
    in ``graph_key_restore._instrument_failures`` (#8827).
    """
    for error in (
        redis_exc.AuthenticationError("WRONGPASS invalid username-password pair"),
        redis_exc.AuthenticationError("invalid password"),
        redis_exc.AuthorizationError(
            "NOPERM this user has no permissions to run the 'graph.query' command"),
        redis_exc.MaxConnectionsError("Too many connections"),
        redis_exc.ExternalAuthProviderError("problem with LDAP service"),
    ):
        exc = _raise_from_handle(error, WRITE_STATEMENT)
        assert str(exc) == str(error), type(error).__name__
        assert "what this means" not in str(exc), type(error).__name__
        assert is_instance_unavailable(exc) is False, type(error).__name__


def test_a_server_refusal_the_parser_maps_into_the_transport_family_is_excluded():
    """P2 (review): the SERVER's own ``-ERR`` answers are not a dead box.

    ``redis``'s reply parser maps some answered refusals into
    ``ConnectionError`` — including the PLAIN class — and redis-py strips the
    leading ``ERR``, so the exception must be excluded by what the parser table
    says, not by the class alone. Read the literals FROM the vendor table so this
    test pins the pin rather than restating strings from memory.
    """
    from redis._parsers.base import BaseParser

    err_table = BaseParser.EXCEPTION_CLASSES["ERR"]
    served = {
        text: cls for text, cls in err_table.items()
        if isinstance(cls, type) and issubclass(cls, redis_exc.ConnectionError)
        and not issubclass(cls, redis_exc.BusyLoadingError)
    }
    assert served, "the parser no longer maps any answered refusal into ConnectionError"
    for text, cls in served.items():
        # Built exactly as the parser builds it: redis-py strips the ``ERR ``.
        exc = cls(text)
        assert is_instance_unavailable(exc) is False, (text, cls.__name__)
        assert explain_instance_unavailable(exc, mutating=False) is None


def test_an_explain_plan_is_never_told_its_write_may_have_landed():
    """P2 (review): ``GRAPH.EXPLAIN`` only PLANS, so nothing can have applied.

    The projection records the distinction (``GRAPH.PROFILE`` EXECUTES,
    ``GRAPH.EXPLAIN`` does not), and a ``CREATE`` sent to ``explain`` was being
    told its write's OUTCOME IS UNKNOWN — a false statement about a
    non-executing verb.
    """
    assert cypher_guard._command_mutating(
        ("GRAPH.EXPLAIN", "tortoise", WRITE_STATEMENT)) is False
    assert cypher_guard._command_mutating(
        ("GRAPH.PROFILE", "tortoise", WRITE_STATEMENT)) is True

    client = _dead_client(redis_exc.TimeoutError(SOCKET_TIMEOUT))
    with pytest.raises(redis_exc.TimeoutError) as caught:
        client.execute_command("GRAPH.EXPLAIN", "tortoise", WRITE_STATEMENT)
    assert "re-issuing this statement is safe" in str(caught.value)
    assert "OUTCOME IS UNKNOWN" not in str(caught.value)


def test_the_proxy_verbs_agree_with_the_command_channel():
    """The proxy has no command tuple, so its arm must match ``_command_mutating``.

    ``explain`` only PLANS (nothing can have applied, whatever the text), while
    ``ro_query`` takes its arm from the TEXT — a write there is not provably
    unapplied (see the engine-policy test above) and a read keeps the safe arm.
    """

    class _StubHandle:
        name = "stub"
        client = None

        def query(self, q, params=None, timeout=None):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

        def ro_query(self, q, params=None, timeout=None):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

        def _query(self, q, params=None, timeout=None, read_only=False):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

        def explain(self, q, params=None):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

    handle = cypher_guard._guard_handle(_StubHandle())

    # EXPLAIN plans only → safe even for a write's text.
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.explain(WRITE_STATEMENT)
    assert "re-issuing this statement is safe" in str(caught.value)
    assert "OUTCOME IS UNKNOWN" not in str(caught.value)

    # RO_QUERY is classified by its text, both ways.
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.ro_query(WRITE_STATEMENT)
    assert "OUTCOME IS UNKNOWN" in str(caught.value)
    assert "re-issuing this statement is safe" not in str(caught.value)

    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.ro_query(READ_STATEMENT)
    assert "re-issuing this statement is safe" in str(caught.value)

    # ``_query(read_only=True)`` picks the GRAPH.RO_QUERY VERB, it does not make
    # the statement a read — so it must not restore the hardcoded ``False`` this
    # round removed. Pinned separately from ``ro_query``: they are different
    # methods and a revert of one leaves the other green (measured, #9358 review
    # P2 — the ``_query`` half had no pin at all).
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle._query(WRITE_STATEMENT, read_only=True)
    assert "OUTCOME IS UNKNOWN" in str(caught.value)
    assert "re-issuing this statement is safe" not in str(caught.value)

    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle._query(READ_STATEMENT, read_only=True)
    assert "re-issuing this statement is safe" in str(caught.value)

    # …while a write through the EXECUTING verb still gets the loud arm.
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.query(WRITE_STATEMENT)
    assert "OUTCOME IS UNKNOWN" in str(caught.value)


def test_the_proxy_explain_arm_follows_the_policy_set_not_a_literal(monkeypatch):
    """``explain``'s ``False`` must be DERIVED from ``_NON_EXECUTING_COMMANDS``.

    The proxy USED TO pass ``False`` for that verb while the command channel
    derived it from the set — a literal there is a second encoding of one policy,
    the drift ``_destructive_commands`` rules out for the same reason. If a
    later lane does what the ⛔ block on that set invites and measures
    ``GRAPH.RO_QUERY``/``EXPLAIN`` on another engine, the set changes and the
    proxy must move with it. Pinned by REMOVING the verb from the set: the
    proxy's arm must then follow the TEXT (the loud arm for a write), which a
    hardcoded ``False`` cannot do.
    """

    class _StubHandle:
        name = "stub"
        client = None

        def explain(self, q, params=None):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

    handle = cypher_guard._guard_handle(_StubHandle())
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.explain(WRITE_STATEMENT)
    assert "re-issuing this statement is safe" in str(caught.value)

    monkeypatch.setattr(cypher_guard, "_NON_EXECUTING_COMMANDS", frozenset())
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.explain(WRITE_STATEMENT)
    assert "OUTCOME IS UNKNOWN" in str(caught.value)
    assert "re-issuing this statement is safe" not in str(caught.value)

    # The command channel agrees with the proxy under the same policy change.
    assert cypher_guard._command_mutating(
        ("GRAPH.EXPLAIN", "tortoise", WRITE_STATEMENT)) is True


def test_a_classifier_that_raises_hedges_instead_of_failing_the_query(monkeypatch):
    """The classifier is TOTAL, because the proxy evaluates it BEFORE its belt.

    The proxy computes ``mutating=_statement_mutating(q)`` as an ARGUMENT, so it
    runs outside ``_forward_query``'s ``except``: a raise there would fail the
    caller's query outright — never dispatching it — instead of hedging the
    note. The command channel's derivation got the same belt in the merged PR
    (``_explain_instance_unavailable``); the proxy has no single call site to
    wrap, so the belt belongs in the classifier. #9358 review P2.
    """
    from tortoise import projection

    monkeypatch.setattr(projection, "_statement_writes",
                        lambda statement: (_ for _ in ()).throw(RuntimeError("classifier blew up")))

    assert cypher_guard._statement_mutating(WRITE_STATEMENT) is None

    class _StubHandle:
        name = "stub"
        client = None

        def query(self, q, params=None, timeout=None):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

    handle = cypher_guard._guard_handle(_StubHandle())
    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.query(WRITE_STATEMENT)
    # The hedge: described, told the outcome is not known — not raised past the
    # caller, and never the "safe to re-issue" claim it cannot support.
    message = str(caught.value)
    assert "Whether the command took effect is UNKNOWN" in message
    assert "re-issuing this statement is safe" not in message


# ── what must NOT be rewritten ─────────────────────────────────────────────


def test_the_graph_swap_abort_is_left_to_explain_graph_abort():
    """#8954 owns that family; two notes on one error would contradict each other."""
    exc = _raise_from_handle(
        redis_exc.ResponseError(
            "graph was deleted or replaced while the query was running, aborting"),
        WRITE_STATEMENT,
    )
    assert str(exc) == (
        "graph was deleted or replaced while the query was running, aborting")


def test_a_persistence_refusal_is_not_reported_as_a_dead_instance():
    """``MISCONF`` is retryable, but it is the ENGINE refusing — not the box dying."""
    exc = _raise_from_handle(
        redis_exc.ResponseError("MISCONF Redis is configured to save RDB snapshots"),
        WRITE_STATEMENT,
    )
    assert "what this means" not in str(exc)
    assert is_instance_unavailable(exc) is False
    # …while it stays retryable on the transport predicate, whose job is different.
    assert retryable_transient(exc) is True


def test_a_deterministic_engine_error_is_not_explained_away():
    exc = _raise_from_handle(
        redis_exc.ResponseError("WRONGTYPE Operation against a key"), WRITE_STATEMENT)
    assert str(exc) == "WRONGTYPE Operation against a key"


def test_a_non_transport_error_is_not_explained_by_the_helper():
    assert explain_instance_unavailable(
        ValueError("not a db error"), mutating=True) is None


def test_a_read_racing_a_parser_teardown_is_a_transport_outcome():
    """#6977: a torn-down parser socket IS the transport going away.

    ``SocketBuffer.close()`` closes the buffer and then clears the reference, so
    a read landing in either window raises a plain ``ValueError`` /
    ``AttributeError``. Neither carries a transport class, so before #6977 the
    classifier answered False and the sweep folded the teardown into a per-graph
    DATA error — the ~20% false red on the #2823 P0 guard.

    The shapes are raised through the REAL guarded handle here, because that is
    where the difference bites: the guard appends its diagnosis IN PLACE, and a
    classifier that compares the whole message answers False on the production
    path while passing every synthetic test. Measured, and the reason the arm
    matches the message HEAD.
    """
    teardown = (
        ValueError("I/O operation on closed file."),
        AttributeError("'NoneType' object has no attribute 'readline'"),
        AttributeError("'NoneType' object has no attribute 'read'"),
    )
    for error in teardown:
        exc = _raise_from_handle(error, READ_STATEMENT)
        # The guard annotated it (the note is what the caller sees)…
        assert "CONNECTION-LIFETIME" in str(exc), str(exc)
        assert "graph was deleted or replaced' abort" in str(exc)
        # …and the classification SURVIVES the annotation.
        assert is_dead_reader(exc) is True, repr(error)
        assert is_instance_unavailable(exc) is True, repr(error)


#: Message text is the ONLY discriminator this arm has, so its narrowness is the
#: contract: a different ``ValueError``/``AttributeError`` — including one about
#: a closed file that is not the vendor's string — must stay outside the family.
def test_only_the_vendor_teardown_strings_are_admitted():
    for exc in (
        ValueError("I/O operation on closed file"),  # no trailing period
        ValueError("I/O operation on a closed file."),
        AttributeError("'NoneType' object has no attribute 'encode'"),
        ValueError("not a db error"),
    ):
        assert is_dead_reader(exc) is False, repr(exc)
        assert is_instance_unavailable(exc) is False, repr(exc)


def test_a_bare_local_timeout_is_not_claimed_to_be_an_instance_outage():
    """``retryable_transient`` refuses a cause-less builtin timeout — so do we."""
    assert is_instance_unavailable(TimeoutError("deadline exceeded")) is False


def test_a_masked_teardown_is_admitted_through_its_cause_chain():
    """#6977: the chain walk admits the shape #3813 recorded.

    ``hosted_backup._is_client_read_timeout`` records a ``ValueError: I/O
    operation on closed file`` chained behind the originating
    ``redis.exceptions.TimeoutError``. Walking the chain through
    ``retryable_transient`` (the module's ONE transport classifier) admits it
    without a second predicate; the message text alone would not.
    """
    timeout = redis_exc.TimeoutError(SOCKET_TIMEOUT)
    try:
        raise ValueError("a masked shape the text arm does not know") from timeout
    except ValueError as exc:
        assert is_dead_reader(exc) is False          # not a vendor string
        assert is_instance_unavailable(exc) is True  # …but its CAUSE is transport


def test_an_answered_refusal_in_the_chain_does_not_join_the_family():
    """#6977 review: the answered-refusal exclusion is per-EXCEPTION, not per-family.

    The vendor parser maps ``-ERR max number of clients reached`` to the plain
    ``ConnectionError`` and ``WRONGPASS`` to ``AuthenticationError``. Those must
    not be smuggled in by a chain either: a data failure whose ``__context__``
    happens to be a pool-exhaustion refusal is still a data failure, and calling
    it ``transport`` sends an operator to the wrong surface (#8827's trap).
    """
    for refusal in (
        redis_exc.ConnectionError("max number of clients reached"),
        redis_exc.AuthenticationError("WRONGPASS invalid username-password pair"),
        redis_exc.ResponseError("MISCONF Redis is configured to save RDB snapshots"),
    ):
        # Each is excluded on its own…
        assert is_instance_unavailable(refusal) is False, repr(refusal)
        # …and stays excluded when it is merely the CAUSE of another error.
        try:
            raise ValueError("bad row shape") from refusal
        except ValueError as exc:
            assert is_instance_unavailable(exc) is False, repr(refusal)


def test_a_self_referential_chain_terminates():
    """The walk is identity-deduplicated, so a cycle cannot hang a red run."""
    exc = ValueError("bad row shape")
    exc.__cause__ = exc
    assert is_instance_unavailable(exc) is False


def test_an_implicit_context_chain_is_admitted_too():
    """#6977: the shape the chain walk exists for is the IMPLICIT one.

    ``hosted_backup._is_client_read_timeout`` records the masked teardown as an
    implicit ``__context__`` (redis-py raises during the ``except`` body, with no
    ``from``), so the ``__context__`` half of the walk is the half production
    travels — it must be pinned, not just the explicit ``from`` half.
    """
    try:
        try:
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)
        except redis_exc.TimeoutError:
            # Implicit context ON PURPOSE: this is the shape production produces
            # (redis-py raises during the ``except`` body, with no ``from``).
            raise ValueError(  # noqa: B904
                "a masked shape the text arm does not know")
    except ValueError as exc:
        assert exc.__cause__ is None and exc.__context__ is not None
        assert is_instance_unavailable(exc) is True


def test_a_suppressed_context_is_not_followed():
    """``raise ... from None`` DETACHES the chain on purpose — honour it.

    Python sets ``__suppress_context__`` and the author's intent is that the
    detached error is not the explanation. Following it anyway relabels a data
    failure as an instance outage — the very misdirection this module exists to
    prevent.
    """
    try:
        try:
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)
        except redis_exc.TimeoutError:
            raise ValueError("bad row shape") from None
    except ValueError as exc:
        assert exc.__suppress_context__ is True
        assert is_instance_unavailable(exc) is False


def test_a_loading_link_stays_an_instance_event_when_chained():
    """LOADING is the one ANSWER this module treats as an instance event.

    ``BusyLoadingError`` subclasses ``ConnectionError`` on the pin, so the walk
    would admit it via ``retryable_transient`` anyway — this pins that the
    answered-refusal exclusion deliberately does not take it away, chained or
    not (the command was rejected BEFORE it ran, so nothing was applied).
    """
    try:
        raise ValueError("a masked shape") from redis_exc.BusyLoadingError(
            "LOADING Redis is loading the dataset in memory")
    except ValueError as exc:
        assert is_instance_unavailable(exc) is True


# ── contract the callers depend on ─────────────────────────────────────────


def test_the_explained_error_still_buckets_as_a_db_outage():
    """The note must not change the error's CLASS: ``_classify_db_failure`` is
    what the eval/reporting lanes bucket on, and a wrapper type would misbucket."""
    exc = _raise_from_handle(redis_exc.TimeoutError(SOCKET_TIMEOUT), READ_STATEMENT)
    assert isinstance(exc, redis_exc.TimeoutError)
    assert _classify_db_failure(exc) == "db"
    assert retryable_transient(exc) is True


def test_the_note_is_appended_at_most_once():
    """Two nested channels must not grow two suffixes."""
    exc = redis_exc.TimeoutError(SOCKET_TIMEOUT)
    first = explain_instance_unavailable(exc, mutating=False)
    assert first is not None
    exc.args = (first,)
    assert explain_instance_unavailable(exc, mutating=False) is None
    assert str(exc).count("what this means") == 1


def test_nested_channels_append_exactly_one_note():
    """The real redis-py chain, not a single-layer stub.

    ``Redis.execute_command`` is a façade over ``_execute_command`` and BOTH are
    rebound by ``_guard_connection``; a handle command therefore crosses the
    graph's ``execute_command``, the client's and the connection's — three
    wrappers for one failure. The opener exists for exactly this, so the test
    exercises the delegation rather than calling the helper twice.
    """

    class _Conn:
        def __init__(self):
            self.execute_command = self._execute_command

        def _execute_command(self, *args, **kwargs):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

    class _Client:
        def __init__(self):
            self.connection = _Conn()
            self.execute_command = self._execute_command

        def _execute_command(self, *args, **kwargs):
            # As redis-py does: the façade delegates to the connection's channel.
            return self.connection.execute_command(*args, **kwargs)

        def select_graph(self, name):
            return _DeadGraph(self, name)

    client = cypher_guard.guarded_client(_Client)
    graph = client.select_graph("tortoise")
    with pytest.raises(redis_exc.TimeoutError) as caught:
        graph.query(WRITE_STATEMENT)

    message = str(caught.value)
    assert message.startswith(SOCKET_TIMEOUT)
    assert message.count("what this means") == 1
    assert "OUTCOME IS UNKNOWN" in message


def test_the_duck_typed_handle_proxy_is_covered():
    """P2 (review): the proxy dispatches straight to its handle — no note otherwise."""

    class _StubHandle:
        name = "stub"
        client = None

        def query(self, q, params=None, timeout=None):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

        ro_query = query

        def execute_command(self, *args, **kwargs):
            raise redis_exc.TimeoutError(SOCKET_TIMEOUT)

    handle = cypher_guard._guard_handle(_StubHandle())
    assert type(handle).__name__ == "_GuardedHandleProxy"

    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.query(WRITE_STATEMENT)
    assert "OUTCOME IS UNKNOWN" in str(caught.value)

    with pytest.raises(redis_exc.TimeoutError) as caught:
        handle.execute_command("GRAPH.QUERY", "stub", READ_STATEMENT)
    assert str(caught.value).startswith(SOCKET_TIMEOUT)
    assert "re-issuing this statement is safe" in str(caught.value)


def test_an_already_explained_graph_abort_is_not_given_a_second_suffix():
    """The shared opener is the idempotency marker for BOTH notes."""
    from tortoise.retry import explain_graph_abort

    explained = explain_graph_abort(
        redis_exc.ResponseError(
            "graph was deleted or replaced while the query was running, aborting"),
        graph_present=True,
        attempts=4,
    )
    assert explained is not None
    exc = redis_exc.TimeoutError(SOCKET_TIMEOUT)
    exc.args = (explained,)
    assert explain_instance_unavailable(exc, mutating=True) is None


# ── the wiring, not just the helper ────────────────────────────────────────


def test_the_raw_command_channel_is_covered():
    """``client.execute_command`` is its own door (vendor instance attribute)."""
    client = _dead_client(redis_exc.TimeoutError(SOCKET_TIMEOUT))
    with pytest.raises(redis_exc.TimeoutError) as caught:
        client.execute_command("GRAPH.QUERY", "tortoise", READ_STATEMENT)
    assert "quit answering in the middle of this request" in str(caught.value)


def test_the_connection_command_channel_is_covered():
    """``client.connection`` is the second raw channel (#6882) — same wrapper."""
    class _Conn:
        def __init__(self, error):
            self.error = error
            self.execute_command = self._execute

        def _execute(self, *args, **kwargs):
            raise type(self.error)(*self.error.args)

    error = redis_exc.TimeoutError(SOCKET_TIMEOUT)
    conn = _Conn(error)

    class _ClientWithConn(_DeadClient):
        def __init__(self):
            super().__init__()
            self.connection = conn

    client = cypher_guard.guarded_client(_ClientWithConn)
    with pytest.raises(redis_exc.TimeoutError) as caught:
        client.connection.execute_command("GRAPH.QUERY", "tortoise", WRITE_STATEMENT)
    assert "OUTCOME IS UNKNOWN" in str(caught.value)


def test_the_destructive_set_is_read_from_the_projection_not_copied():
    """P2 (review): one list, one owner — a private copy is drift.

    ``projection._DESTRUCTIVE_HANDLE_COMMANDS`` is the repo's single answer to
    "what destroys a graph handle"; ``_HANDLE_MUTATING_VERBS`` states the rule
    ("ONE list … so 'what is a mutating verb' cannot drift between the two
    readers").
    """
    from tortoise.projection import _DESTRUCTIVE_HANDLE_COMMANDS

    assert cypher_guard._destructive_commands() is _DESTRUCTIVE_HANDLE_COMMANDS
    for verb in _DESTRUCTIVE_HANDLE_COMMANDS:
        assert cypher_guard._command_mutating((verb, "tortoise")) is True, verb


def test_a_key_space_command_lands_on_the_hedge_not_the_mutation_arm():
    """The destructive arm is narrow ON PURPOSE — read the docstring's SCOPE note.

    The projection's REFUSAL rule is the namespace (a graph IS a Redis key), but
    reusing it here would classify the read-only probes this channel really
    sends — ``MODULE LIST`` on every client construction, ``INFO``, ``EXISTS`` —
    as mutations and tell a caller to "read back what you wrote" about a read.
    A key-space command therefore gets the ``None`` hedge, which still warns that
    anything mutating must be read back. Pinned so the choice is deliberate.
    """
    for verb in ("DEL", "UNLINK", "RENAME", "EXPIRE", "MODULE", "INFO"):
        assert cypher_guard._command_mutating((verb, "tortoise")) is None, verb


def test_loading_is_admitted_before_the_answered_refusal_checks():
    """P2 (review): the LOADING early return is ORDER-CRITICAL.

    The vendor parser maps ``LOADING`` in its ``EXCEPTION_CLASSES``, so the
    answered-refusal check WOULD exclude it — which would silently delete the
    LOADING arm and every test pinning it. Both halves are asserted so a
    reordering fails here with the reason attached.
    """
    from tortoise.retry import _answered_by_server

    exc = redis_exc.BusyLoadingError(LOADING)
    assert _answered_by_server(exc) is True, "the parser no longer maps LOADING"
    assert is_instance_unavailable(exc) is True, (
        "the LOADING family was dropped — the early return must precede the "
        "answered-refusal checks in is_instance_unavailable")


def test_the_helper_is_the_only_source_of_the_wording():
    """The wiring must not inline its own text — one home or they will drift."""
    from pathlib import Path

    source = Path(cypher_guard.__file__).read_text(encoding="utf-8")
    assert "quit answering in the middle of this request" not in source
    assert "explain_instance_unavailable" in source


# ── the applier's own contract: it must never REPLACE the error ─────────────
#
# ``_note_instance_outage``'s docstring promises it "never raises", because it
# runs inside an ``except`` block on the way to re-raising the ORIGINAL error:
# a raise from the note would replace the exception being explained with the
# applier's own — the exact swap the note exists to prevent. That promise is
# ENFORCED by a belt around the body (and around the arm's derivation, which
# reads caller-supplied Cypher), so these three tests pin the belt rather than
# the promise: each one arms a failure INSIDE the note path and asserts the
# caller still sees its own error.


class _ReadOnlyArgsTimeout(redis_exc.TimeoutError):
    """A transport error whose ``args`` cannot be reassigned (#9352 review P2).

    ``args`` is writable on every built-in exception, so the honest way to arm
    the failure the contract has to survive is to make ONE exception type that
    refuses the write — ``exc.args = (…)`` is the note's last statement, and on
    a type like this it raises ``AttributeError`` from inside the applier.
    """

    def __setattr__(self, name, value):
        # ONLY ``args``: ``redis``'s own ``__init__`` assigns ``error_type``
        # through ``__setattr__``, so a blanket refusal would fail at raise time
        # and the test would prove nothing about the applier.
        if name == "args":
            raise AttributeError("args is read-only on this exception")
        super().__setattr__(name, value)


def test_a_note_that_cannot_be_written_never_replaces_the_error_it_explains():
    """The append itself fails → the ORIGINAL error still surfaces, unannotated."""
    exc = _raise_from_handle(_ReadOnlyArgsTimeout(SOCKET_TIMEOUT), READ_STATEMENT)

    # Not AttributeError, not a chained wrapper: the caller's own error.
    assert type(exc) is _ReadOnlyArgsTimeout
    assert exc.args == (SOCKET_TIMEOUT,), (
        "the message was rewritten or replaced — the note must be all-or-nothing")
    assert cypher_guard.explain_instance_unavailable(exc) is not None, (
        "precondition: this family IS explainable — otherwise the belt was never reached")
    assert "quit answering in the middle of this request" not in str(exc)


def test_the_applier_swallows_a_failing_helper(monkeypatch):
    """The belt is around the applier's own body, not only around its callers."""
    monkeypatch.setattr(
        cypher_guard, "explain_instance_unavailable",
        lambda exc, mutating=None: (_ for _ in ()).throw(RuntimeError("boom")))

    exc = redis_exc.TimeoutError(SOCKET_TIMEOUT)
    exc.args = (SOCKET_TIMEOUT,)
    # Must not raise, and must not touch the exception.
    assert cypher_guard._note_instance_outage(exc, mutating=False) is None
    assert exc.args == (SOCKET_TIMEOUT,)


def test_a_failing_arm_derivation_lands_on_the_hedge_and_keeps_the_error(monkeypatch):
    """The DERIVATION reads caller data, so it is inside the same belt.

    ``_command_mutating`` walks the wire args, the projection's destructive set
    and the write regexes over the caller's Cypher. A raise from any of those
    happens on the same error path, so without its own guard it would replace
    the caller's transport error with a classifier's exception. It is answered
    with the ``None`` hedge instead — "outcome UNKNOWN, read back first" — which
    is the conservative arm the docstring promises for an unclassifiable command.
    """
    def _boom(args):
        raise RuntimeError("classifier blew up")

    monkeypatch.setattr(cypher_guard, "_command_mutating", _boom)

    client = _dead_client(redis_exc.TimeoutError(SOCKET_TIMEOUT))
    with pytest.raises(redis_exc.TimeoutError) as caught:
        client.execute_command("GRAPH.QUERY", "tortoise", WRITE_STATEMENT)

    message = str(caught.value)
    assert message.startswith(SOCKET_TIMEOUT)
    assert "Whether the command took effect is UNKNOWN" in message
    # The mutation arm must NOT be guessed either way from a classifier failure.
    assert "OUTCOME IS UNKNOWN" not in message
    assert "re-issuing this statement is safe" not in message
