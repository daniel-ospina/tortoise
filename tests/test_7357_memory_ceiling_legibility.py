"""#7357 — a memory-ceiling refusal says so, instead of reading as a broken write.

The issue is about a memory failure that MISDESCRIBES itself. Its first shape was
an uncapped container SIGKILLed under host pressure, whose death reached the
caller as a bare transport error; #8954 and #9352 made that legible. #9204 then
capped every container this repo starts and #9506 gave the lane's own instance a
Redis-side ``--maxmemory`` BELOW the cap with ``noeviction``, so an overshoot is
an ANSWERED refusal instead of a kill — which created the third shape of the same
defect:

    redis.exceptions.OutOfMemoryError:
        command not allowed when used memory > 'maxmemory'.

MEASURED on this checkout 2026-10-21 against a lane container
(``CONFIG SET maxmemory 1mb`` + ``noeviction``): both a raw ``graph.query`` and
the same call through ``guarded_client`` raised exactly that sentence, unchanged —
no cause, no next action, no owner of the limit, and nothing in the tree that
said "memory" (the issue's own grep for a ceiling diagnosis was empty).

What these tests pin, in order:

* the family predicate, and that it is EXCLUSIVE of the instance-outage family
  (an answered refusal is not a dead box, and a dead box is not an answer);
* the note's content — capacity refusal, nothing applied, data intact, and the
  real remedy rather than an invitation to retry (a deterministic refusal
  is not a transient), and that it conforms to the repo's recorded #2981 ruling
  for the same refusal instead of advising a destructive one;
* the contract: driver sentence at position 0, exception TYPE unchanged, one
  suffix at most;
* the WIRING, driven through a real ``guarded_client`` and through the duck-typed
  proxy, so deleting the seam reddens these rather than merely the helper.
"""
from __future__ import annotations

import pytest

redis_exc = pytest.importorskip("redis.exceptions")

from tortoise import cypher_guard  # noqa: E402
from tortoise.retry import (  # noqa: E402
    _LEGIBLE_OPENER,
    explain_instance_unavailable,
    explain_memory_ceiling,
    is_instance_unavailable,
    is_memory_ceiling_refusal,
    retryable_aborted_write,
    retryable_transient,
)
from tortoise.sdk import _classify_db_failure  # noqa: E402

#: The engine's own sentence, verbatim, as the vendor parser hands it over.
CEILING_SENTENCE = "command not allowed when used memory > 'maxmemory'."
WRITE_STATEMENT = "CREATE (n:Point {id:$id}) RETURN n.id"


def _ceiling_error() -> BaseException:
    """The exception the pinned driver mints for the engine's ``-OOM`` reply."""
    return redis_exc.OutOfMemoryError(CEILING_SENTENCE)


# ── the family predicate ────────────────────────────────────────────────────
def test_the_measured_sentence_is_the_memory_ceiling_family():
    assert is_memory_ceiling_refusal(_ceiling_error()) is True


def test_the_family_is_decided_by_class_even_without_the_measured_text():
    """A rebuilt exception (no wire text) is still the family, by type."""
    assert is_memory_ceiling_refusal(redis_exc.OutOfMemoryError("")) is True


def test_a_plain_response_error_carrying_the_engine_sentence_is_the_family():
    """The text arm: a `ResponseError` that did not pass the parser's table."""
    exc = redis_exc.ResponseError(CEILING_SENTENCE)
    assert is_memory_ceiling_refusal(exc) is True


def test_an_unrelated_refusal_is_not_the_family():
    """`maxmemory` alone is not the discriminator — a caller's own message can
    carry it (`CONFIG GET maxmemory`, a docstring, a config dump)."""
    assert is_memory_ceiling_refusal(
        redis_exc.ResponseError("ERR unknown command 'GRAPH.FROB'")) is False
    assert is_memory_ceiling_refusal(
        redis_exc.ResponseError("maxmemory is set to 1610612736")) is False


def test_a_transport_failure_is_not_the_family_and_vice_versa():
    """EXCLUSIVITY: the two notes must never be applied to one another's case.

    A ceiling refusal is an ANSWERED reply; an instance outage is the absence of
    one. Confusing them sends a reader after a container restart that never
    happened, or retries a capacity refusal forever.
    """
    transport = redis_exc.TimeoutError("Timeout reading from socket")
    assert is_memory_ceiling_refusal(transport) is False
    assert is_instance_unavailable(transport) is True
    assert is_instance_unavailable(_ceiling_error()) is False
    assert explain_memory_ceiling(transport) is None
    assert explain_instance_unavailable(
        _ceiling_error(), mutating=True) is None


def test_the_nearest_neighbour_is_not_the_family_either():
    """`LOADING` is the other ANSWERED refusal, and it sits one step away.

    Both are replies rather than outages, so a reloading instance must not be
    told it "reached a memory limit" — the remediation (free memory, raise the
    ceiling) would turn a knob that changes nothing. MEASURED on the pinned
    redis (7.4.1): `BusyLoadingError` is NOT a `ResponseError` — it arrives as
    `network:BusyLoadingError` under the CONNECTION family, which is why
    `is_instance_unavailable` needs its order-critical early return for it. So
    the two predicates partition it deliberately: an outage (per that family) and
    not a ceiling refusal. Both halves are pinned here, because a future redis
    that reparented it would break the partition silently.
    """
    loading = redis_exc.BusyLoadingError(
        "LOADING Redis is loading the dataset in memory")
    assert not isinstance(loading, redis_exc.ResponseError), (
        "the partition below is pinned on the measured inheritance")
    assert is_memory_ceiling_refusal(loading) is False
    assert explain_memory_ceiling(loading) is None
    assert is_instance_unavailable(loading) is True, (
        "LOADING is the outage family's answered member (#9352)")


# ── the note's content ──────────────────────────────────────────────────────
def test_the_note_names_the_mechanism_and_refuses_the_data_loss_reading():
    text = explain_memory_ceiling(_ceiling_error())
    assert text is not None
    # The driver sentence stays at position 0 (every text match reads there).
    assert text.startswith(CEILING_SENTENCE)
    assert _LEGIBLE_OPENER in text
    lowered = text.lower()
    # What happened …
    assert "memory-ceiling" in lowered
    assert "capacity" in lowered
    # … what it is NOT …
    assert "graph was deleted or replaced" in lowered
    assert "not evidence that the graph or its data was lost" in lowered
    assert "not a dead or restarting instance" in lowered
    # … and what was (not) applied.
    assert "nothing was applied" in lowered


def test_the_note_does_not_invite_a_retry_and_names_the_real_remedy():
    """A deterministic refusal is NOT a transient: the next action is memory."""
    text = (explain_memory_ceiling(_ceiling_error()) or "").lower()
    assert "re-issuing it now will be refused the same way" in text
    assert "raise the ceiling" in text
    # The kernel cap is the BACKSTOP; raising it does not move the refusal, so
    # the note must not send the reader to the wrong knob alone.
    assert "falkordb_container_memory" in text
    assert "backstop" in text
    # It must never be told "retrying is safe" the way the READ arm of the
    # transport note is (#9352): that arm's licence does not extend here.
    assert "re-issuing this statement is safe" not in text


def test_the_note_never_names_deleting_graphs_as_the_remedy():
    """P2 (#9532 review): the remedy must agree with the repo's #2981 ruling.

    This seam runs on EVERY instance the SDK builds a client for — shared and
    production included — not just a lane's own container, so "free memory by
    deleting graphs this instance no longer needs" is advice the caller cannot
    act on safely: `GRAPH.DELETE` is irreversible and a name you cannot
    attribute is not yours to delete. `_write_refusal_message` (#2981) already
    records that ruling for this exact refusal, and two messages for one
    refusal must not advise opposite actions.

    NON-VACUOUS: the pre-fix note said "free memory (delete graphs this
    instance no longer needs)", so the first assertion fails on it.
    """
    text = (explain_memory_ceiling(_ceiling_error()) or "").lower()
    assert "delete graphs this instance no longer needs" not in text, (
        "the note must not offer graph deletion as the remedy: it is emitted "
        "on shared and production instances too (see #2981)")
    # Deletion may only appear as a PROHIBITION, and it must carry #2981's
    # constraint verbatim rather than loosening it into a paraphrase.
    assert "do not hand-pick names from graph.list to free memory" in text
    assert "graph.delete cannot be undone" in text
    assert "not yours to delete" in text
    assert "never delete the production graph `tortoise`" in text
    assert "tortoise_restored*" in text
    assert "do not flushall" in text
    assert "#2981" in text, (
        "the note must point at the policy it is conforming to, or the next "
        "lane re-decides it")


def test_the_denyoom_claim_is_scoped_to_the_verb_that_was_measured():
    """P3 (#9532 review): the note must not out-claim its own measurement.

    The PR measured a refused `GRAPH.QUERY` WRITE (a `CREATE` left the graph at
    0 nodes). That every `GRAPH.*` verb carries `denyoom` — reads such as
    `GRAPH.RO_QUERY`/`GRAPH.LIST` included — is NOT established by it, so the
    note names the measured verb and the load-bearing half ("rejected BEFORE it
    ran") rather than the absolute.

    NON-VACUOUS: the pre-fix note said "a `denyoom` command (every `GRAPH.*`
    verb)", so the second assertion fails on it.
    """
    text = explain_memory_ceiling(_ceiling_error()) or ""
    lowered = text.lower()
    assert "every `graph.*` verb" not in lowered
    assert "graph.query" in lowered, (
        "the scoped claim names the verb that was actually measured")
    assert "rejected before it" in lowered


def test_the_note_is_idempotent_and_keeps_the_exception_type():
    first = explain_memory_ceiling(_ceiling_error())
    assert first is not None
    exc = redis_exc.OutOfMemoryError(first)
    assert explain_memory_ceiling(exc) is None, (
        "a second suffix on an already-explained message is the #7357 "
        "double-note defect")
    # Type is load-bearing: `_classify_db_failure` buckets BY TYPE, so a wrapper
    # (or a rewritten args[0] on a different class) would silently re-bucket it.
    assert isinstance(exc, redis_exc.OutOfMemoryError)
    assert _classify_db_failure(exc) == "db"


def test_a_ceiling_refusal_is_never_a_retryable_write():
    """Retrying is the one action the note forbids — the predicates must agree.

    Both the transient predicate and the write-arm classifier are checked,
    because either answering True would re-issue a statement the engine has just
    refused, and the note above would then be contradicting the code.
    """
    exc = _ceiling_error()
    assert retryable_transient(exc) is False
    assert retryable_aborted_write(exc, graph_exists=lambda: True) is False


# ── the wiring, through a real guarded client ───────────────────────────────
class _CeilingConnection:
    """Vendor-shaped connection (redis-py): the public verb IS the dispatcher.

    ``Redis.execute_command`` is only a façade — ``return
    self._execute_command(*args, **options)`` — so this is the shape
    ``_guard_connection`` rebinds BOTH names of, and the shape a guarded command
    therefore crosses on its way to the socket.
    """

    def __init__(self, error):
        self.error = error
        self.execute_command = self._execute_command
        self.commands: list = []

    def _execute_command(self, *args, **kwargs):
        self.commands.append(args)
        # A FRESH instance per command, as a real driver does: the note mutates
        # the exception in place, so a shared instance would carry it forward.
        raise type(self.error)(*self.error.args)


class _CeilingGraph:
    """Vendor-shaped graph handle whose every command reaches *client*."""

    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.execute_command = client.execute_command

    def query(self, q, params=None, timeout=None):
        return self._query(q, params=params, timeout=timeout)

    def ro_query(self, q, params=None, timeout=None):
        return self._query(q, params=params, timeout=timeout, read_only=True)

    def _query(self, q, params=None, timeout=None, read_only=False):
        return self.execute_command(
            "GRAPH.RO_QUERY" if read_only else "GRAPH.QUERY", self.name, q)


class _CeilingClient:
    """Vendor-shaped client answering every command with the engine's refusal."""

    def __init__(self, error=None):
        self.error = error if error is not None else _ceiling_error()
        self.connection = _CeilingConnection(self.error)
        self.execute_command = self._execute_command
        self.commands: list = []

    def _execute_command(self, *args, **kwargs):
        # As redis-py does: the façade delegates to the connection's channel, so
        # a handle command really crosses TWO appliers — `_forward_query` on the
        # guarded handle proxy, then the connection's `_GuardedExecuteCommand`
        # (rebound in place by `_guard_connection`). A façade that raised
        # directly would cross only the first, which is the vacuous fixture
        # the #9532 review caught.
        self.commands.append(args)
        return self.connection.execute_command(*args, **kwargs)

    def select_graph(self, name):
        return _CeilingGraph(self, name)


def _guarded(error=None):
    client = cypher_guard.guarded_client(lambda **kw: _CeilingClient(error))
    graph = client.select_graph("tortoise")
    return client, graph


def test_a_guarded_handle_query_carries_the_ceiling_note():
    _client, graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.query(WRITE_STATEMENT)
    message = str(caught.value)
    assert message.startswith(CEILING_SENTENCE), (
        "the driver sentence must stay at position 0: a retry predicate and "
        "every caller text match reads from there")
    assert _LEGIBLE_OPENER in message
    assert "memory-ceiling" in message.lower()


def test_a_guarded_read_gets_the_same_note_and_no_write_ambiguity():
    """A refusal is a refusal: the note does not split read/write.

    The transport arm says "OUTCOME IS UNKNOWN, read back first" for a mutating
    statement because the REPLY was lost. Here the reply arrived, so the outcome
    is known for both shapes — and claiming ambiguity would send the caller to
    read back a statement that provably did not run.
    """
    _client, graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.ro_query("MATCH (n) RETURN count(n)")
    message = str(caught.value)
    assert "memory-ceiling" in message.lower()
    assert "OUTCOME IS UNKNOWN" not in message


def test_the_client_command_channel_carries_the_note_too():
    """The second channel (a raw `execute_command`) is the same seam."""
    client, _graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        client.execute_command("GRAPH.QUERY", "tortoise", WRITE_STATEMENT)
    assert "memory-ceiling" in str(caught.value).lower()


def test_a_duck_typed_handle_proxy_carries_the_note():
    """The proxy dispatches straight to its wrapped handle, so it needs its own
    application (the class it claims to cover must not overstate)."""

    class _Stub:
        name = "tortoise"

        def query(self, q, params=None, timeout=None):
            raise _ceiling_error()

    proxy = cypher_guard._guard_handle(_Stub())
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        proxy.query(WRITE_STATEMENT)
    assert "memory-ceiling" in str(caught.value).lower()


def test_the_note_is_applied_once_across_two_nested_channels(monkeypatch):
    """A handle verb and the connection channel both wrap the same failure.

    The fixture must make the delegations REAL, or this test proves nothing
    (#9532 review): `_GuardedClientProxy` guards the connection IN PLACE and
    `_guard_connection` rebinds both of its command entries, but a client whose
    `_execute_command` raised directly would cross ONE applier — and then
    `count == 1` would hold with no opener guard at all. So `_CeilingClient`
    delegates to `self.connection` exactly as `redis.Redis` does, and the
    applier count is ASSERTED (>= 2) rather than assumed: the chain is
    `_forward_query` (the handle proxy) then `_GuardedExecuteCommand` (the
    connection), two chances to append one suffix.

    The opener is the shared idempotency marker; without it the note grows a
    second suffix per layer — the #7357 double-note defect. NON-VACUOUS:
    delete `if _LEGIBLE_OPENER in text` from `explain_memory_ceiling` and the
    `count == 1` assertion below reddens (the applier count stays at 2).
    """
    appliers: list = []
    real_applier = cypher_guard._note_instance_outage

    def counting_applier(exc, *, mutating):
        appliers.append(mutating)
        real_applier(exc, mutating=mutating)

    # Both call sites (`_forward_query`, `_explain_instance_unavailable`) look
    # the applier up on the MODULE, so this counts every layer in the chain.
    monkeypatch.setattr(cypher_guard, "_note_instance_outage", counting_applier)

    _client, graph = _guarded()
    with pytest.raises(redis_exc.OutOfMemoryError) as caught:
        graph.query(WRITE_STATEMENT)
    message = str(caught.value)
    assert len(appliers) >= 2, (
        "the fixture stopped crossing two appliers, so the single-suffix "
        f"assertion below is vacuous (appliers={len(appliers)}) — the client "
        "must delegate to its guarded connection as redis.Redis does")
    assert message.startswith(CEILING_SENTENCE)
    assert message.count(_LEGIBLE_OPENER) == 1, message
    assert "memory-ceiling" in message.lower()
