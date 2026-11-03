"""Bounded retry with a transport-only predicate — the #1806 resilience
primitive, now a PRODUCT module (product inversion,
fix/invert-retrieval-to-product).

Moved from ``tools/longmem_eval/errors.py`` (where the eval's ingest write
path consumed it) so the product owns the capability: ``retryable_transient``
is the pinned transport-class predicate, ``retryable_aborted_write`` is the
narrower write-path predicate (the SDK's direct graph writes, #7405), and
``call_with_predicate`` is the bounded jittered retry loop. The eval harness
re-exports ``retryable_transient`` / ``call_with_predicate`` /
``WriteStageRetriesExhausted`` unchanged from ``tools/longmem_eval/errors.py``
and keeps its own run knobs (``INGEST_WRITE_RETRIES`` etc.) eval-side.

⚠️ PARTIALLY WIRED (#7405): Wired:

  * ``create_point``'s direct writes — the ``CREATE (:Point)``, the
    ``TAGGED`` edges (``_sync_tags``) and the ``extractedFrom`` edge
    (``_link_source``);
  * ``_advance_ep_version``'s ``MERGE (:EpMeta)``;
  * ``_mark_dirty`` — the ``ep_dirty``/``ep_dirty_at`` persist SET and the
    terminal ``ep_dirty`` clear SET (both absolute SETs under the statement's
    own WHERE);
  * ``delete_point`` — the ``DETACH DELETE`` and the orphan-``:Tag`` GC;
  * ``promote_point`` — the CAS ``status draft → live`` SET;
  * ``create_operator`` — the ``CREATE (:Point)`` node, the per-input
    typed-edge + ``INPUT`` mirror statement, and the never-set-status source
    promote SET;
  * ``mitigate_operator`` — the mitigation ``CREATE (:Point)`` and the
    ``IMPL``/``mitigated_by`` edge pair;
  * ``create_entity``'s document ``extractedFrom`` leg and
    ``create_document``'s ``extractedFrom`` leg (both ``_link_source``);
  * ``update_point`` — the three property/promote ``SET`` arms plus the
    per-key ``SET`` loop (the two ``:Point:Object``/``:Point`` status promote
    arms carry their guard inside the statement; the ``:Object`` plain arm
    carries a relative ``n.version = coalesce(n.version, 0) + 1`` beside the
    plain ``SET n += $props``, safe because an abort means the statement did
    NOT land — not because the write is absolute), the derived-embedding
    clear+set, and the ``TAGGED`` edges it shares with ``create_point``
    (``_sync_tags``). ``annotate_operator`` is covered transitively — it
    delegates here;
  * ``invalidate_point`` — the terminal stamp ``SET`` and the ``CORRECTS``
    ``MERGE``;
  * ``retract_point`` — the CAS ``status → retracted`` ``SET`` (its
    ``_terminal_excluded`` guard is inside the statement);
  * the capture lane's graph writes, all in ``tortoise/sdk.py`` — the
    ``:Session`` MERGE (``_write_session_and_turns``), the batched
    ``UNWIND $turns`` turn store and the stale-turn ``DETACH DELETE`` sweep
    (``_write_capture_turns``), and the capture lane's
    follow-up provenance/state stamps — the minted-point ``eventId`` stamp,
    the operator ``eventId`` stamps, the ``retry_failed_capture`` provenance
    heal, the ``entity_links_*`` counters, and the
    ``capture_ok``/``capture_extractor`` SET (the SDK ``capture_session``
    entry point that ran these was deleted by #4282; the hosted lane still
    runs them);
  * ``_post_commit`` — the ``POST /v1/sessions/commit`` transmission — on
    ``retryable_transient`` (the transport-class predicate; it is not a graph
    write, so the graph-state-gated ``retryable_aborted_write`` does not
    apply). Re-issuing is safe by construction: the same deterministic
    ``client_commit_id`` rides every attempt and the server serializes on it
    atomically (``CommitRecordStore.acquire``).

⛔ NOT covered — the ``proj.apply`` FOLDS a live write reaches
(``_apply_and_journal``, ``_create_entity``, ``_doc_write``): a fold is a
multi-statement sequence over an open event-type space, so a mid-fold abort
leaves the earlier statements landed and re-issuing the whole fold is a
RE-APPLICATION, not a re-issue — ``retryable_aborted_write``'s "the statement
did not land" guarantee is per-statement and does not extend to the enclosing
fold. Also unwired: ``supersede_point`` (its statements are individually
idempotent — ``MERGE`` / DELETE by ``ID(r)`` / ``SET r += $attrs`` — but the
multi-statement transfer is deliberately left to a separate increment),
``_apply_source_inheritance``'s revert ``REMOVE n.ep_alpha, …`` plus the
baseline ``SET`` that path inherits through (``set_point_baseline``'s
``SET n.ep_alpha = $a, …``), and the remaining direct ``proj.g.query`` write
sites across ``tortoise/``.
``merge_points`` is not a direct graph write at all: it is an ``EventAPI``
journal emit (``tortoise/api.py``) whose graph effect is the ``proj.apply``
fold above. A write abort at any of these still raises (or is downgraded by the
caller's own ``except``) exactly as before; those paths' robustness today
remains idempotent-MERGE + server-side dedup.

Re-issuing is only safe for a statement whose abort means it DID NOT LAND.
That is what ``retryable_aborted_write`` proves and what makes it admissible
on a bare ``CREATE``; the transport-class ``retryable_transient`` does NOT
prove it (a lost reply is not a lost write) and must never be pointed at a
non-idempotent statement — see that predicate's docstring.

LEGIBILITY (#7357): the graph-abort family's engine sentence
(``graph was deleted or replaced …``) reads as corruption, and the measured
outages behind it are usually a graph SWAP the retry could not outlast. On
exhaustion the SDK appends :func:`explain_graph_abort`'s diagnosis to the SAME
exception (type and the anchored engine sentence both preserved), branched on
the same ``EXISTS`` presence the retry trusts — a present key says the
statement did not land (though a rebuild may have REPLACED the contents, and
presence is not writability), so a re-issue cannot duplicate; an absent key is
a deletion OR a rebuild still inside its delete window, and is never told
"retry".

The SAME issue names a second, disjoint family: the instance stops answering
underneath the client (a container restart, an RDB reload, an OOM-kill) and
surfaces as a bare transport error — ``TimeoutError("Timeout reading from
socket")``, ``ConnectionError("Connection closed by server.")``, or a
``LOADING`` handshake. Those are not graph aborts (the engine never replied),
so :func:`explain_graph_abort` deliberately leaves them alone; they get their
own diagnosis from :func:`explain_instance_unavailable`, which states the
mechanism and, because it cannot know whether a statement landed, branches on
whether the statement can MUTATE rather than on any graph probe — EXCEPT for
the ``LOADING`` handshake, which ignores ``mutating`` because the instance
replied and rejected the command before it ran, so nothing was applied either
way.
"""
from __future__ import annotations

import errno
import logging
import random
import re
import socket
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Network errno set the retry predicate trusts as transport evidence on a
#: bare ``OSError`` — a deterministic-bug FileNotFoundError/ENOENT or
#: PermissionError/EACCES is never retried.
_NETWORK_ERRNOS = frozenset({
    errno.ECONNRESET, errno.ETIMEDOUT, errno.EHOSTUNREACH,
    errno.ENETUNREACH, errno.EPIPE, errno.ECONNREFUSED,
    errno.ECONNABORTED, errno.ENETDOWN,
})

#: MISCONF / AOF-fsync / disk-full write refusals redis surfaces as
#: ``ResponseError`` — retried (bounded) because the recovery under disk
#: pressure IS the retry; unrelated ResponseErrors (WRONGTYPE, ...) are not.
_MISCONF_RE = re.compile(r"MISCONF|Can't persist")

#: Write refusals where the engine ABORTED the statement, so the outcome is
#: definitively *did not land* (#7405) AND the graph's own state is irrelevant
#: to the decision — the same graph is still there when the retry is issued:
#:   - a concurrent writer holds the write lock, reported on the constraint
#:     path: ``Write query aborted: another write is in progress``;
#:   - a concurrent writer holds the slot, reported on the ``GRAPH.QUERY``
#:     write path: ``ERR another write is in progress, retry the query``.
#:     This is a SEPARATE engine code path from the constraint-path abort
#:     above (``src/graph_core.rs::execute_query_write``, raised before the
#:     slot is claimed), and on the **v6 Rust core** it is the refusal the
#:     SDK's own wrapped statements actually receive — ``_advance_ep_version``'s
#:     ``MERGE`` and ``create_point``'s bare ``CREATE`` both take the write
#:     path, so without this clause a contended ``create_point`` write is
#:     raised instead of retried (the #7405 loss).
#: Both are v6 literals; the engine's own concurrency test documents the
#: message as retryable and the message itself instructs a retry.
#:
#: ANCHORED TO THE START OF THE MESSAGE (``\A``), deliberately: a bare
#: alternation over ``re.search`` matches ANY message merely CONTAINING the
#: phrases (measured: three crafted non-abort diagnostics all returned True),
#: and a LINE-start anchor is not enough either — a parse error ECHOES the
#: offending CYPHER, so user source containing the phrase would authorize a
#: re-issue. Because the predicate gates a re-issued bare ``CREATE`` —
#: non-idempotent, no uniqueness constraint on ``Point.id`` — a false positive
#: IS the duplicate-point failure #7405's fix exists to prevent: under-matching
#: (a loud miss) is the safe direction. Measured live on v6 (ver 60001), a parse
#: error collapses newlines to SPACES in its ``errCtx:`` echo, so the phrase
#: lands mid-message, never at position 0 (``_abort_arm`` normalizes the ``ERR``
#: prefix so one anchored pattern serves both the wire and the stripped form).
_WRITE_LOCK_RE = re.compile(
    r"\A\s*(?:(?:write query )?aborted:\s*another write is in progress"
    r"|another write is in progress, retry the query)",
    re.IGNORECASE)

#: The graph-deleted-or-replaced abort family — the ONE class whose retry
#: decision depends on the graph KEY's presence, not on the error text (#7685).
#: Two engine cores, three measured literals, and on v6 a single sentence for two
#: different realities:
#:
#:   - **v6 core** (``MODULE LIST`` ver **60001**, Rust): one sentence, because
#:     the engine cannot tell the caller which happened — ``graph was deleted or
#:     replaced while the query was running, aborting``;
#:   - **C core** (``MODULE LIST`` ver **42004** = the ``docker-compose.yml``-
#:     pinned ``falkordb-server:v4.20.4``, and **42006** = v4.20.6, the image the
#:     CI docker lane provisions): two literals, measured live on the same race —
#:     ``Encountered different graph value when opened key <name>`` when the key
#:     had been replaced, ``Encountered an empty key when opened key <name>``
#:     when it was absent.
#:
#: All three mean the statement did NOT land in the graph the caller is writing
#: to, so re-issuing cannot duplicate. But **neither core's text by itself
#: authorizes a retry**: FalkorDB auto-creates a graph on write, so a re-issued
#: bare ``CREATE`` would succeed into a fresh EMPTY graph and the caller would be
#: told SUCCESS while the graph it wrote against is gone (#6666 / #7685(A)).
#:
#: The discriminator is the graph key's presence at classification time —
#: ``EXISTS <name>`` is 0 after a plain deletion and 1 after a rebuild's recreate
#: (measured on both cores). It is **presence, not identity**: a rebuild whose
#: recreate has landed reads 1 and retries (correct — the caller's write belongs
#: in the rebuilt graph); a rebuild still inside its delete window, or a plain
#: deletion, reads 0 and is refused LOUD. A rebuild that recreated the key and
#: then failed mid-replay is therefore still retried into the new graph — that is
#: the REBUILD's outcome, not the retry's, and separating the two fully would
#: need an engine-level identity token (out of scope, #7685's escalation line).
#:
#: All three literals are ANCHORED TO THE START OF THE MESSAGE (``\A``): they
#: are whole engine messages, so a diagnostic that merely quotes one must not
#: match — and a LINE-start anchor is not enough, because a parse error echoes the
#: offending source (which can contain the sentence) inside ``errCtx:``. Measured
#: live on v6 (ver 60001): that echo collapses newlines to SPACES, so the sentence
#: lands mid-message and cannot match; ``\A`` makes that structural rather than
#: dependent on the engine's newline handling. A false positive re-issues a bare,
#: non-idempotent ``CREATE``, so under-matching is the safe direction.
_GRAPH_ABORT_RE = re.compile(
    r"\A\s*graph was deleted or replaced while the query was running, aborting"
    r"|\A\s*encountered an empty key when opened key "
    r"|\A\s*encountered different graph value when opened key ",
    re.IGNORECASE)

#: The RESP simple-error code. ``redis-py`` strips a leading ``ERR `` from wire
#: errors (measured), but a directly constructed ``ResponseError("ERR …")`` — or
#: a transport that does not strip it — keeps it; normalizing once lets the
#: anchored patterns above serve both forms.
_ERR_PREFIX_RE = re.compile(r"\AERR\s+", re.IGNORECASE)

#: The retry arms, named so ONE classifier decides which one an exception takes.
_ARM_STATE_INDEPENDENT = "state_independent"
_ARM_GRAPH_ABORT = "graph_abort"


def _abort_arm(exc: BaseException) -> str | None:
    """Classify *exc* into its retry arm, or ``None`` when it is not retryable.

    THE single classifier: both :func:`retryable_aborted_write` and
    :func:`graph_abort_family` read this, so the arm that AUTHORIZES a retry and
    the arm that DECIDES whether it needs graph state are always the same one.
    Two independent regex tests disagreed on a message carrying both an
    unconditional clause and a graph-abort clause: the predicate short-circuited to
    retryable as state-independent while a graph-abort-only family test then gated
    it on ``EXISTS``, refusing a legitimate contended/persistence write whenever
    the key was missing (#7685 review round 2).

    Precedence: the ``\\A``-ANCHORED arm is decided FIRST. A match at the message
    start is the strongest signal available — the message IS that abort — while
    ``_MISCONF_RE`` is an unanchored search. On a message that carries both, the
    unanchored MISCONF must therefore NOT win: that would send a genuine
    graph-abort to the state-independent arm, which retries with no ``EXISTS``
    probe at all — straight into an auto-created EMPTY graph, the #6666 silent
    success this predicate exists to prevent. With the anchored arm first, such a
    message is gated, and a message merely MENTIONING persistence is unaffected.
    """
    import redis.exceptions as _re

    if not isinstance(exc, _re.ResponseError):
        return None
    text = _ERR_PREFIX_RE.sub("", str(exc).strip(), count=1)
    if _GRAPH_ABORT_RE.search(text):
        return _ARM_GRAPH_ABORT
    if _MISCONF_RE.search(text) or _WRITE_LOCK_RE.search(text):
        return _ARM_STATE_INDEPENDENT
    return None


def retryable_aborted_write(
    exc: BaseException,
    *,
    graph_exists: Callable[[], bool] | None = None,
) -> bool:
    """Retryable ONLY for write refusals whose outcome is definitively *did not land*.

    **The graph-abort family is refused unless the caller supplies graph state**
    (``graph_exists``); an omitted probe is a refusal, never a guess (see below).

    Deliberately narrower than :func:`retryable_transient`, and it exists because
    that predicate is NOT safe for a NON-IDEMPOTENT statement. ``create_point``
    issues a bare ``CREATE`` with a client-minted id and there is **no uniqueness
    constraint on ``Point.id``** (a plain index only, so a duplicate is accepted);
    the eval lane documents the same hazard independently — *"create_point uses
    CREATE (not MERGE), so re-running ingest over the same fresh graph would
    duplicate points"* (``tools/longmem_eval/ingest.py``, ``_point_exists``).

    ``retryable_transient`` also returns True for redis ``TimeoutError`` /
    ``ConnectionError``, where the server may have APPLIED the write and only the
    reply was lost — re-issuing those on a bare ``CREATE`` can mint two points
    with one id, and ``get_point`` would silently return one of them, breaking
    ``derived == replay(journal)``. Both arms below instead mean the engine
    ABORTED the statement, so nothing was applied and re-issuing cannot duplicate:

    - a concurrent writer held the write slot (#7405) — the engine's own
      *aborting* refusal, reported on two code paths (the constraint path's
      ``Write query aborted: …`` and the ``GRAPH.QUERY`` write path's
      ``another write is in progress, retry the query``), both with the same
      *did not land* semantics (see ``_WRITE_LOCK_RE``);
    - persistence refused the write (``MISCONF`` / ``Can't persist``) — a write
      refusal, not a completed write.

    **The graph-deleted-or-replaced family needs the caller's state (#7685).**
    The engine's abort text (``_GRAPH_ABORT_RE``) admits BOTH a legitimate
    rebuild and a plain deletion, and on the v6 core it is literally the same
    sentence. Retrying a deletion re-issues the ``CREATE`` into a graph FalkorDB
    auto-created EMPTY, so the caller is told SUCCESS while its data is gone
    (#6666 class). The predicate therefore refuses this family unless
    *graph_exists* answers, and the answer is False:

    - ``graph_exists`` — a zero-argument callable answering *"is the graph key
      present right now?"* (the SDK passes a raw ``EXISTS <name>`` probe).
      ``True`` (a rebuild's recreate has landed, or the key was never gone) →
      retry; ``False`` (a plain deletion, or a rebuild still inside its delete
      window) → refuse, so the engine's abort surfaces LOUD.
    - **Omitted, or the probe raises → refuse.** A retry this predicate cannot
      authorize is never guessed: a missed retry surfaces the engine's error,
      while a false retry reports success against a graph the caller never wrote
      to. (The SDK additionally re-probes immediately before each re-issue, so a
      deletion that lands during the backoff — after this predicate already
      answered — is still refused; see ``TortoiseSDK._graph_write_with_retry``.)

    **What each arm rests on.** The state-independent arms rest on engine
    source/semantics (no test pins it — see PR #7615's verification table): the
    write-slot refusal is raised from ``src/graph_core.rs::execute_query_write``
    *before the slot is claimed*, and ``MISCONF`` is a pre-execution command
    rejection. The graph-abort family's v6 form rests on
    ``WriteAbort::GraphUnregistered``, whose registration test runs BEFORE
    mutation under one continuous GIL hold and whose engine test says it
    *"aborted before mutating"*. The two **C-core literals are
    measurement-backed, not source-cited**: their abort-before-mutate basis is a
    live one-application measurement (``{1 retry, 1_000_001 nodes}`` on ver
    42004, and the same ``when opened key`` open-time wording), not an engine
    test read. Do not copy the pattern onto a fourth literal without that
    evidence.

    This predicate is the SDK write path's own gate; it is intentionally NOT
    :func:`retryable_transient`, which stays the transport-class predicate the
    eval's OUTER phase loops use (see the layering note there).
    """
    arm = _abort_arm(exc)
    if arm is None:
        return False
    if arm == _ARM_STATE_INDEPENDENT:
        return True
    # The graph-abort family. The text cannot distinguish a rebuild from a
    # deletion, so the STATE decides — and a retry we cannot authorize is
    # refused (loud), never guessed.
    if graph_exists is None:
        return False
    try:
        return bool(graph_exists())
    except Exception:  # noqa: BLE001, RUF100 — a failed probe is not a rebuild
        logger.warning(
            "graph-existence probe failed; refusing to retry the aborted write "
            "(a silent success is worse than a loud miss)", exc_info=True)
        return False


def graph_abort_family(exc: BaseException) -> bool:
    """True when *exc* is the graph-deleted-or-replaced abort family.

    That family's retry decision depends on the graph's state, so a caller that
    re-issues the write must consult ``graph_exists`` for it — and ONLY for it.
    The write-slot / ``MISCONF`` arms are state-independent: they must keep
    retrying even when the graph key is absent, because their refusal has nothing
    to do with the graph's presence. (#7685 review round 2: the SDK's
    re-issue-time probe was applied to every arm, which turned a contended or
    persistence-refused write into a hard failure whenever the key was missing.)

    Reads :func:`_abort_arm`, the same classifier the predicate uses, so the
    authorization and the gating can never disagree. On a message that carries
    BOTH clauses the ``\\A``-anchored graph-abort arm wins when it LEADS the
    message — and the family then reports True — because that anchored match
    means the message IS that abort; the unanchored MISCONF arm is the
    fallback for a message that merely mentions persistence.
    """
    return _abort_arm(exc) == _ARM_GRAPH_ABORT


#: The stable opener of the legibility suffix :func:`explain_graph_abort`
#: appends. It doubles as the IDEMPOTENCY marker: a message that already
#: carries it has been explained once and is never explained again (two nested
#: retry layers must not grow two suffixes).
_LEGIBLE_OPENER = " — what this means: "


def explain_graph_abort(
    exc: BaseException,
    *,
    graph_present: bool | None,
    attempts: int = 1,
) -> str | None:
    """Return *exc*'s message with a caller-legible explanation appended (#7357).

    **The defect.** The engine reports a graph SWAP with
    ``graph was deleted or replaced while the query was running, aborting`` —
    the ``#7685`` family, whose three measured literals are in
    ``_GRAPH_ABORT_RE``. Read raw, that sentence names two causes (deletion,
    replacement), asserts the graph was destroyed, and offers no next action,
    so an ordinary failed write reads as CORRUPTION or DATA LOSS. Measured
    cost (#7357, 2026-10-15 comment): a lane asked the owner to authorise a
    graph REBUILD on the strength of "the graph is write-broken", on a graph
    whose data was intact and whose write path worked.

    **What this adds, and what it deliberately does not.** The wording states
    the mechanism's CLASS (the instance swapped the graph under the running
    query: a restart/reload, a rebuild, or the engine evicting the graph from
    its cache), the outcome of THIS statement (it did not land; the engine
    aborts before mutating), and the next action — and it branches on the same
    ``EXISTS`` discriminator the retry itself trusts, because the sentence
    alone cannot tell a replacement from a deletion:

    - ``graph_present is True`` — the key is still there, so the graph was
      not left DELETED and a retry reaches the caller's graph rather than a
      freshly auto-created EMPTY one; a re-issue is therefore safe from
      DUPLICATION. It does NOT say the contents survived: a rebuild REPLACES
      them, which is one of the mechanisms the same sentence names, so the
      note tells the caller to verify rather than assume. Nor does presence
      imply WRITABILITY: a graph that refuses every write (the measured
      write-locked state ``tools/graph_write_repair.py`` documents — key
      present, reads fine, every write refused) still refuses the retry.
      This is the case the raw sentence misdescribes, and the one the measured
      outages hit.
    - ``graph_present is False`` — the key is gone. Never told "retry": a
      re-issue lands in a graph FalkorDB auto-creates EMPTY, the #6666
      silent-success class. The note does NOT settle WHY it is gone: an
      absence is a plain deletion OR a rebuild still inside its delete window
      (``retryable_aborted_write``'s own wording), and the probe cannot tell
      those apart — so it names both and gives an action that fits both,
      instead of asserting a deletion the evidence does not establish.
    - ``graph_present is None`` — the presence probe could not answer, so the
      message says the state is UNKNOWN rather than guessing either way. It
      names ALL THREE reasons the answer can be missing — an unresponsive
      instance, an unresolved graph name and a missing projection
      (``_graph_presence``) — instead of picking one, because naming only the
      instance sends the reader to check a healthy server for a wiring gap it
      cannot see.

    It does NOT say which of restart / rebuild / eviction happened: the engine
    exposes no identity token for the graph, the same limitation ``#7685``
    escalated. It also cannot make the write succeed — it makes the refusal
    legible. **Every clause is scoped to what ``EXISTS`` proves**: presence is
    not identity (see ``_graph_presence``) and it is not integrity either, so
    the note never reassures a caller about its DATA — that overclaim is the
    mirror of the defect this function exists to fix (#7357).

    **Contract preserved.** The returned message KEEPS the engine sentence at
    position 0, so the ``\\A``-anchored ``_GRAPH_ABORT_RE`` still classifies it
    (a caller that re-classifies the explained error — the SDK does on every
    retry — sees the same arm) and no caller's ``match=`` breaks. Returns
    ``None`` when *exc* is not in this family or was already explained, so this
    is also the allow-list check: a deterministic ``ResponseError``
    (``WRONGTYPE``), a transport ``TimeoutError`` and a bare ``OSError`` are
    never rewritten.
    """
    if not graph_abort_family(exc):
        return None
    text = str(exc)
    if _LEGIBLE_OPENER in text:
        return None
    calls = max(1, int(attempts))
    if graph_present is True:
        explains = (
            "the graph key is still present, so the graph was not left "
            "deleted: the instance swapped the graph under the running "
            "query (a restart/reload, a rebuild, or the engine evicting it "
            "from its cache). A rebuild REPLACES the graph's contents, so "
            "data written before the swap may be gone — verify, do not "
            "assume. This statement did not land, and the engine aborts "
            "before mutating, so retrying is safe — though presence is not "
            "writability, so a graph that refuses every write still refuses "
            f"it. {calls} attempt(s) were made in this call."
        )
    elif graph_present is False:
        explains = (
            "the graph key is ABSENT — either a deletion, or a rebuild still "
            "inside its delete window; the probe cannot tell those apart — "
            "so a retry would write into a fresh EMPTY graph, which is why "
            f"it is refused. {calls} attempt(s) were made in this call. "
            "Restore the graph, or wait for a rebuild to finish replaying, "
            "before writing."
        )
    else:
        explains = (
            "the graph key could not be probed, so whether the graph "
            f"survived is UNKNOWN — {calls} attempt(s) were made in this "
            "call. A probe answers nothing for three different reasons (the "
            "connection did not answer, the graph name did not resolve, or no "
            "projection was available), and the answer does not say which — "
            "check the connection, the graph name and the projection before "
            "concluding anything about the data."
        )
    return text + _LEGIBLE_OPENER + explains


# The instance-side failures a graph client sees when the CONTAINER goes away
# or comes back — the second family #7357 names, and the one
# `explain_graph_abort` deliberately leaves alone: nothing was aborted by the
# engine (so its anchored family does not match), and its ``EXISTS`` probe would
# answer about a graph nobody is asking after.

#: The strings a read on a TORN-DOWN connection parser raises (#6977).
#: ``redis/_parsers/socket.py``'s ``SocketBuffer.close()`` closes the buffer and
#: THEN rebinds the reference to ``None``, so a read landing in either window
#: raises a plain ``ValueError`` (``I/O operation on closed file.``, from the
#: closed buffer) or ``AttributeError`` (``'NoneType' object has no attribute
#: 'readline'`` / ``'read'`` — the two accessors ``resp2`` reads through, header
#: and bulk string). The vendor offers NO class for this state and there is no
#: server answer to inspect: these strings are the whole signal. They are not
#: UNIQUE to the vendor in the abstract (``I/O operation on closed file.`` is the
#: stdlib ``io`` message), so the arm is scoped to the two types the vendor
#: raises and is only one of the producers the note names: `hosted_api`
#: ``_data_plane_db`` (#3750) records the same string when a client pool is
#: disconnected under a live handle, which is why the note says CONNECTION
#: lifetime rather than container. Matching on message text is refused everywhere
#: else in this module (``hosted_backup._is_client_read_timeout`` states the
#: rule, and this arm stays off the CLASS-based families for the same reason) — it
#: is used here only because there is no alternative discriminator for a state
#: that has no class.
_DEAD_READER_MESSAGES = (
    "I/O operation on closed file.",
    "'NoneType' object has no attribute 'readline'",
    "'NoneType' object has no attribute 'read'",
)


def is_dead_reader(exc: BaseException) -> bool:
    """True when *exc* is a read racing a connection parser's teardown (#6977).

    The measured shape, not an inference: the intermittent
    ``test_sweep_enumerates_supabase_control_plane`` red carries exactly these
    strings (nothing in this tree raises them but the vendor parser — the graph
    side never sees a parser at all). Both accessors are admitted because
    ``SocketBuffer`` reads through both: ``readline()`` for the reply header and
    ``read()`` for every bulk string, i.e. the dump path.

    **The HEAD of the message, never the whole message.** ``cypher_guard``'s
    ``_note_instance_outage`` appends :data:`_LEGIBLE_OPENER` + a diagnosis IN
    PLACE before the error reaches its caller, so an equality test on the whole
    string answers False on the production path — measured on the guarded
    handle, where this predicate is the thing that decides whether a teardown is
    re-issued at all. Matching the head is also what the guard's own contract
    promises (``tests/test_7357_transport_legibility.py``: *"The driver sentence
    stays at position 0: every caller that matches on it (and every retry
    predicate) reads from there"*).

    Whether the vendor also CHAINS an originating transport error is not
    observable in the reported signatures (they arrive as ``str(e)``), so this
    arm covers the case where there is none; :func:`is_instance_unavailable`
    walks the chain as well, for the shape
    ``hosted_backup._is_client_read_timeout`` records.
    """
    if not isinstance(exc, (ValueError, AttributeError)):
        return False
    return str(exc).partition(_LEGIBLE_OPENER)[0].strip() in _DEAD_READER_MESSAGES


def is_instance_unavailable(exc: BaseException) -> bool:
    """True when *exc* is the INSTANCE going away, not the engine refusing.

    Two admissions, three exclusions and one reuse, all deliberate — and the
    exclusion is BY TYPE, never a ``ConnectionError`` subclass walk:

    - a read racing a connection parser's teardown IS a transport outcome — the
      instance may have gone away, or the client's own read bound expired and
      redis-py tore the connection down mid-parse (``hosted_backup`` records
      that reading of the same ``ValueError``) — observed at the window where
      the parser has already closed or cleared its socket buffer (#6977):
      ``is_dead_reader`` admits those states, which carry no transport class and
      therefore cannot be admitted below. This arm runs FIRST because the
      answered-refusal checks below have no class to match (a
      ``ValueError``/``AttributeError`` is never a server answer) and the
      transport fallback has no class to recognise.
    - an ANSWERED refusal is never an instance outage. On the pinned ``redis``
      (7.4.1, measured) ``AuthenticationError``, ``AuthorizationError`` and
      ``MaxConnectionsError`` all SUBCLASS ``ConnectionError``, so a plain
      membership test against the transport family would smuggle a
      ``WRONGPASS`` / ``NOPERM`` / pool-exhaustion into "the box died" and send
      the reader after a container restart instead of their credentials — the
      exact mirror of the defect this module exists to fix, and the trap
      ``graph_key_restore._instrument_failures`` records from its own incident
      (#8827: *"a subclass test would smuggle an auth failure into the very
      transport set it must never join"*). ``ResponseError`` covers the
      ``MISCONF`` / ``Can't persist`` refusal, which
      :func:`retryable_transient` calls retryable for a different reason (disk
      pressure, not a dead container).
    - everything else is decided by :func:`retryable_transient`, the module's
      ONE transport classifier, so this predicate cannot drift from the arm
      that decides whether such a failure may be re-issued. That also keeps
      the errno narrowing (``_NETWORK_ERRNOS``) and the socket-origin rule for
      a builtin ``TimeoutError`` in a single place.

    ``BusyLoadingError`` is the one family member the instance ANSWERED, and its
    early return above is ORDER-CRITICAL, not merely arm selection: the vendor
    parser maps ``LOADING`` in its ``EXCEPTION_CLASSES``, so
    :func:`_answered_by_server` would classify it as an answered refusal and drop
    it from the family — taking the LOADING arm (and every test that pins it) with
    it. :func:`retryable_transient` would have admitted it; that is NOT the gate
    that would reject it.
    """
    import redis.exceptions as _re

    busy_loading = getattr(_re, "BusyLoadingError", None)
    if busy_loading is not None and isinstance(exc, busy_loading):
        # ORDER-CRITICAL: admitted BEFORE the answered-refusal checks below, which
        # would otherwise exclude it (the parser maps "LOADING" to this class).
        return True
    if _answered_refusal(exc):
        return False
    # A legibility predicate must never REPLACE the exception it explains, so a
    # classifier that cannot run (a broken optional import inside
    # ``retryable_transient``) answers "not this family" rather than raising
    # from inside an ``except`` block. The dead-reader arm lives INSIDE this belt
    # for the same reason (#6977 review): it calls ``str(exc)``, and a hostile
    # ``__str__`` must not raise out of an ``except`` block.
    try:
        if is_dead_reader(exc):
            return True
        # #6977: a masked teardown can still CARRY its originating transport
        # error in the chain — the shape ``hosted_backup._is_client_read_timeout``
        # records (``ValueError`` chained behind ``TimeoutError``). ``retryable_transient``
        # is the module's one transport classifier, so walk the chain through it
        # rather than writing a second test next to the dead-reader arm. Bounded,
        # and deduplicated by identity: a self-referential chain terminates.
        #
        # #6977 review P1: the exclusions above are per-EXCEPTION, not per-family,
        # so they are re-applied to EVERY link. Without that, a data failure whose
        # ``__context__`` happens to be `-ERR max number of clients reached` (which
        # the vendor parser maps to the plain `ConnectionError`) or a `MISCONF`
        # refusal was admitted as an outage — the exact smuggling (#8827) the
        # answered-refusal arm exists to stop, and it would relabel a data failure
        # as `transport` on a public incident.
        seen: set[int] = set()
        link: BaseException | None = exc
        while link is not None and id(link) not in seen:
            seen.add(id(link))
            # The top link is already exclusion-checked above, and it MUST still be
            # classified here — this loop is the transport fallback, not only the
            # chain walk.
            if (link is exc or not _answered_refusal(link)) and retryable_transient(link):
                return True
            # Follow the chain the way Python reports it: an explicit
            # ``raise ... from`` (``__cause__``) wins; ``raise ... from None``
            # SUPPRESSES the context the author deliberately detached, so the walk
            # stops there; only then is the implicit ``__context__`` followed.
            # Ignoring ``__suppress_context__`` was measured to admit a data
            # failure the author had explicitly detached from a transport error.
            if link.__cause__ is not None:
                link = link.__cause__
            elif link.__suppress_context__:
                link = None
            else:
                link = link.__context__
        return False
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("transport classification unavailable", exc_info=True)
        return False


def _answered_refusal(exc: BaseException) -> bool:
    """True when *exc* is a refusal the SERVER produced, never an instance outage.

    The single definition of the answered-refusal exclusion, shared by the
    top-level arm and by every link the chain walk visits: a link that carries
    an answered refusal must not smuggle one into the family any more than the
    top-level exception may. ``BusyLoadingError`` answers False — LOADING is the
    one answer this module treats as an instance event (see
    :func:`is_instance_unavailable`), and that reading does not change because
    the error arrived chained.
    """
    import redis.exceptions as _re

    busy_loading = getattr(_re, "BusyLoadingError", None)
    if busy_loading is not None and isinstance(exc, busy_loading):
        return False
    for name in ("ResponseError", "AuthenticationError", "AuthorizationError",
                 "MaxConnectionsError", "ExternalAuthProviderError"):
        answered = getattr(_re, name, None)
        if answered is not None and isinstance(exc, answered):
            return True
    return _answered_by_server(exc)


def _answered_by_server(exc: BaseException) -> bool:
    """Whether the vendor PARSER produced *exc* from a server's own ANSWER.

    The type list above cannot be complete on its own, and this closes the gap
    the measured way: ``redis``'s reply parser maps some *answered* refusals
    into the ``ConnectionError`` family, including the PLAIN class —
    ``-ERR max number of clients reached`` → ``ConnectionError`` and
    ``-ERR problem with LDAP service`` → ``ExternalAuthProviderError`` (both
    read from the pinned parser's own table, not restated from memory). A
    caller told "the instance quit answering" when the instance answered "too
    many clients" or "your LDAP backend is broken" is the same defect as
    telling it the graph was deleted.

    The mapping is read from the parser's own ``EXCEPTION_CLASSES`` — the
    vendor table that PRODUCED the exception — so this cannot drift from the
    pin the way a copied list of literals would. The parser raises
    ``ResponseError`` for anything it does not map, and that is excluded above.
    Any failure to read the table (a private module, a future redis) answers
    False, i.e. falls back to the type list and the transport family.
    """
    try:
        from redis._parsers.base import BaseParser
    except Exception:  # noqa: BLE001, RUF100 — a private module; never fail the error path
        return False
    table = getattr(BaseParser, "EXCEPTION_CLASSES", None) or {}
    text = str(exc).strip()
    # TWO lookups, because redis-py STRIPS a leading ``ERR `` from a wire error
    # (measured; ``_ERR_PREFIX_RE`` records the same fact from the other side),
    # so a parsed refusal arrives as ``max number of clients reached`` while a
    # directly constructed one may keep the ``ERR ``. The bare form is matched
    # FIRST against the parser's own nested table — that is where the served
    # ``-ERR <text>`` answers live.
    mapped = None
    err_table = table.get("ERR")
    if isinstance(err_table, dict):
        mapped = err_table.get(text[4:] if text.upper().startswith("ERR ") else text)
    if mapped is None:
        head, _, rest = text.partition(" ")
        mapped = table.get(head)
        if isinstance(mapped, dict):
            mapped = mapped.get(rest)
    return isinstance(mapped, type) and isinstance(exc, mapped)


def explain_instance_unavailable(
    exc: BaseException,
    *,
    mutating: bool | None = None,
) -> str | None:
    """Return *exc*'s message with an instance-outage diagnosis appended (#7357).

    **The defect this closes.** The issue's symptom block lists TWO errors, and
    only one of them was made legible. ``graph was deleted or replaced while
    the query was running`` is explained by :func:`explain_graph_abort` on the
    write retry's exhaustion arm (#8954); the other —
    ``TimeoutError: Timeout reading from socket`` — still arrives as the bare
    driver sentence, in every verb, on reads as well as writes. Measured cost
    (the issue's 2026-10-13 comment): a read probe died with a bare transport
    error inside a test run and the lane spent a full cycle reading it as the
    test's own defect, because nothing in the message named the instance.

    **What it says, and what it refuses to claim.** The note names the
    mechanism CLASS — the instance quit answering mid-request, which is what a
    container restart / RDB reload / OOM-kill looks like from here, and equally
    what a query slower than the client's timeout looks like — and states
    outright that this is NOT the engine's graph-swap abort and NOT evidence
    that the graph's data was lost. It never says "your data is intact": a
    transport failure proves nothing about DATA either way, and that overclaim
    is the mirror of the defect (#7357's ``explain_graph_abort`` records the
    same rule).

    **Why the branch is on ``mutating``, not on a graph probe.** A transport
    failure means the reply was lost, and a lost reply is NOT a lost write
    (see :func:`retryable_aborted_write`, which refuses this whole family for a
    bare ``CREATE`` for exactly that reason). So the only actionable fact is
    the statement's shape:

    - ``mutating is False`` — a read changes nothing, so re-issuing it is safe.
    - ``mutating is True`` — the OUTCOME IS UNKNOWN. Never told "retry": it may
      have landed with only the reply lost, and re-issuing a non-idempotent
      statement can DUPLICATE it. The caller is told to read back first.
    - ``mutating is None`` — the command carries no classifiable statement
      (``PING``, ``GRAPH.LIST``, a raw command): the outcome is UNKNOWN and the
      caller is told which way each shape falls.

    **``LOADING`` is its own arm, and it is the one that ignores ``mutating``.**
    A ``BusyLoadingError`` is the instance ANSWERING (it replied LOADING while
    reloading its dataset), and the engine rejects the command BEFORE it runs —
    so nothing was applied whether the statement reads or writes, and re-issuing
    after the load is safe for both. Telling a LOADING caller the instance
    "quit answering" and its write's outcome is UNKNOWN would be two false
    statements, which is why this family does not share the arm above.

    **Contract preserved.** The original sentence stays at position 0 (every
    driver/``redis`` text match and the ``in``-style assertions in callers keep
    working), the exception TYPE is untouched, and the note is appended at most
    once — the shared :data:`_LEGIBLE_OPENER` is the idempotency marker, so a
    failure explained by ``explain_graph_abort`` (or already explained here) is
    never given a second suffix. Returns ``None`` for anything outside
    :func:`is_instance_unavailable`'s family.
    """
    if not is_instance_unavailable(exc):
        return None
    text = str(exc)
    if _LEGIBLE_OPENER in text:
        return None
    import redis.exceptions as _re

    loading = isinstance(exc, getattr(_re, "BusyLoadingError", ()))
    if loading:
        explains = (
            "the instance ANSWERED, and what it answered was LOADING: it is "
            "restarting and loading its dataset from disk, so it is up but not "
            "serving yet. This is not the engine's 'graph was deleted or "
            "replaced' abort, and it is not evidence that the graph or its "
            "data was lost. The command was rejected BEFORE it ran, so nothing "
            "was applied and re-issuing it once the load completes is safe — "
            "a large RDB can take minutes to load, so check again rather than "
            "assuming the instance is gone."
        )
        return text + _LEGIBLE_OPENER + explains
    if is_dead_reader(exc):
        # #6977: the recorded cause of THIS shape is connection lifetime, not a
        # container restart — `hosted_api._data_plane_db` (#3750) records the
        # same ``ValueError`` when a pool is disconnected out from under a live
        # handle, and `hosted_backup` records it when the CLIENT's read bound
        # expires and redis-py tears the connection down mid-parse. Saying "the
        # instance quit answering" here would send the operator after the wrong
        # thing, so this arm names the mechanism and leaves the container as one
        # possibility among three. The shared `mutating` arm below still applies:
        # whether the statement took effect does not depend on who closed the
        # socket.
        explains = (
            "the connection this command was reading through was torn down "
            "mid-read: redis-py closed the parser's socket buffer while a "
            "reply was being parsed. That is a CONNECTION-LIFETIME event, not "
            "a verdict about the graph — a client pool disconnecting, the "
            "client's read bound expiring, or the instance going away all "
            "surface this way, and this message does not say which. This is "
            "NOT the engine's 'graph was deleted or replaced' abort, and it "
            "is not evidence that the graph or its data was lost. "
        )
    else:
        explains = (
            "the instance quit answering in the middle of this request (a closed "
            "connection, or the client's read timeout expiring) — which is what a "
            "container restart/reload or an OOM-kill of the instance looks like "
            "from the client, and equally what a query slower than the client's "
            "timeout looks like. This is NOT the engine's 'graph was deleted or "
            "replaced' abort, and it is not evidence that the graph or its data "
            "was lost. "
        )
    if mutating is True:
        explains += (
            "The statement's OUTCOME IS UNKNOWN: it may have landed with only "
            "its reply lost. Do not conclude that it failed — read back what "
            "you wrote before re-issuing, because re-issuing a "
            "non-idempotent statement (a bare CREATE) can duplicate it."
        )
    elif mutating is False:
        explains += (
            "No result was returned, and a read changes nothing, so "
            "re-issuing this statement is safe."
        )
    else:
        explains += (
            "Whether the command took effect is UNKNOWN — re-issuing a "
            "read-only command is safe; anything that mutates must be read "
            "back before it is re-issued."
        )
    return text + _LEGIBLE_OPENER + explains


#: The engine's own MEMORY-CEILING sentence, as served on the wire (#7357).
#:
#: MEASURED on the pinned engine (falkordb 4.20.6, `CONFIG SET maxmemory 1mb`
#: + `noeviction`): a `GRAPH.QUERY` is answered
#: ``-OOM command not allowed when used memory > 'maxmemory'.`` and the vendor
#: parser maps the ``OOM `` half to ``redis.exceptions.OutOfMemoryError`` — so
#: the note below normally arrives with that CLASS, and the text arm exists for
#: the two shapes where it does not: a `ResponseError` rebuilt by hand, and the
#: engine's variant spelling without the `OOM` prefix. Only the distinctive
#: clause is matched (never a bare `maxmemory`, which a `CONFIG GET` reply or a
#: caller's own message can carry).
#:
#: ⚠️ THIS REGEX HAS TWO JOBS, and the second one is load-bearing (#7357): it
#: decides family MEMBERSHIP (the text arm of
#: :func:`is_memory_ceiling_refusal`), and it is the CEILING EVIDENCE for
#: :func:`_chain_names_the_ceiling` — the only thing that licenses a note to
#: assert `maxmemory` at all. Loosening it (e.g. matching a bare `maxmemory`)
#: silently re-arms the exact over-claim this issue closed; narrowing it
#: demotes a genuinely-named ceiling to the hedge arm. Change it for both
#: readers or not at all.
_MEMORY_CEILING_RE = re.compile(
    r"command not allowed when used memory\s*>\s*['\"]?maxmemory", re.IGNORECASE)


def is_memory_ceiling_refusal(exc: BaseException) -> bool:
    """True when *exc* is the instance REFUSING at its dataset ceiling (#7357).

    The mirror of :func:`is_instance_unavailable`, and deliberately exclusive of
    it. A caller that confused them would either chase a container restart that
    never happened or retry a capacity refusal forever.

    ⚠️ The exclusivity is by CLASS, not by "an outage is the absence of a
    reply" — that phrasing is FALSE and was corrected under review (#9532
    cycle 2): the outage family also owns the ANSWERED ``BusyLoadingError``
    (LOADING). The real discriminator is :func:`_answered_refusal`, which
    excludes every ``ResponseError`` from the outage family: ``OutOfMemoryError``
    IS a ``ResponseError``, and LOADING is not an ``OutOfMemoryError``. So the
    two predicates cannot both answer True for the SAME exception object on its
    OWN class/text.

    ⚠️ That is a statement about one link, not a universal property of the pair
    (#9532 cycle 3 P3). This function WALKS the chain below, so the two
    predicates CAN both answer True for one exception — a transport failure
    whose ``__cause__`` is a ceiling refusal is admitted by
    :func:`is_instance_unavailable` (on the top link's ``TimeoutError``) and by
    this one (on the cause). What keeps a caller-visible message to ONE suffix
    is the ORDER in ``cypher_guard._note_instance_outage`` — the outage arm
    first, the ceiling arm only as its fallback — plus the shared
    ``_LEGIBLE_OPENER`` idempotency marker. Not the disjointness. Pinned by
    ``test_the_ordering_not_the_disjointness_keeps_one_suffix`` in
    ``tests/test_7357_memory_ceiling_legibility.py``, which reddens if the two
    arms are ever swapped (the ceiling note would then claim "nothing was
    applied" about a failure whose REPLY was LOST).

    Class first, text second: ``OutOfMemoryError`` is the class the vendor
    parser mints for the engine's own ``OOM …`` reply on the pinned redis, and
    the text arm catches a `ResponseError` that reached the seam without passing
    through that table. The text test reads the message HEAD (`.partition`), the
    same rule :func:`is_dead_reader` follows, so an already-explained message is
    still classified and never double-noted.

    The chain is walked the way :func:`is_instance_unavailable` walks it
    (``__cause__``, then ``__context__`` unless suppressed), because the driver
    and this repo's own wrappers re-raise WITH a cause: measured, a
    ``ResponseError("write failed")`` whose ``__cause__`` is the ceiling refusal
    answered False here before the walk existed, so the illegible sentence
    reached the caller unchanged — the exact defect #7357 closes. Every link is
    tested with the SAME class/text rule, so a chained answer cannot smuggle a
    non-ceiling failure into the family, and the walk is id-deduplicated so a
    cyclic chain cannot spin.
    """
    import redis.exceptions as _re

    def _is_ceiling(link: BaseException) -> bool:
        oom = getattr(_re, "OutOfMemoryError", None)
        if oom is not None and isinstance(link, oom):
            return True
        if isinstance(link, _re.ResponseError):
            return bool(_MEMORY_CEILING_RE.search(
                str(link).partition(_LEGIBLE_OPENER)[0]))
        return False

    seen: set[int] = set()
    link: BaseException | None = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        if _is_ceiling(link):
            return True
        # As Python reports it: an explicit ``raise ... from`` wins, a
        # ``raise ... from None`` SUPPRESSES the context the author deliberately
        # detached, and only then is the implicit ``__context__`` followed — the
        # same order :func:`is_instance_unavailable` uses (#6977).
        if link.__cause__ is not None:
            link = link.__cause__
        elif link.__suppress_context__:
            link = None
        else:
            link = link.__context__
    return False


def _chain_names_the_ceiling(exc: BaseException) -> bool:
    """True when some link of *exc* NAMES the dataset ceiling in its own text.

    #7357: :func:`is_memory_ceiling_refusal` admits the exception CLASS, and
    redis-py mints ``OutOfMemoryError`` for **every** reply whose text starts
    with ``OOM``. So the class establishes that the instance ANSWERED with a
    memory refusal — NOT that a ``maxmemory`` exists, was read, or was reached.
    ``OOM failed to allocate 1GB`` is an allocation failure; an instance with
    ``maxmemory 0`` has no dataset ceiling at all. Only this predicate may
    license the ceiling sentence, which is why it is separate from the family
    predicate: the family is what the caller must be told about, and the
    ceiling is a claim that needs evidence the family does not carry.

    Walks the EXPLICIT chain — the top link, then ``__cause__``. It does NOT
    follow an implicit ``__context__``: a context link is not ATTRIBUTABLE to the
    failure being explained. Measured shape (#7357 cycle 4): an
    ``OutOfMemoryError("OOM failed to allocate 1GB")`` raised inside an
    ``except ResponseError("command not allowed when used memory > 'maxmemory'")``
    block inherits that sentence implicitly, and asserting the ceiling from it
    would be the same over-claim one level of indirection out. ``raise ... from``
    is the author stating causality, so ``__cause__`` is evidence and an implicit
    context is not. (The FAMILY predicate, :func:`is_memory_ceiling_refusal`,
    keeps walking ``__context__``: there the question is "is this the family",
    where a wider walk is the safe direction. Here the question is "may I assert
    a ``maxmemory``", where it is not.)

    ⚠️ RESIDUAL, recorded so it is not re-derived: ``__context__`` is ALSO the
    shape a wrapper takes when it re-raises WITHOUT ``raise ... from`` — the
    common no-``B904`` wrapping — and that half is the half production travels
    for the transport seam (#6977). So a wrapper placed between the wire and this
    seam that dropped the inner text and omitted ``from`` would demote a named
    ceiling to the hedge. That is the SAFE direction (the hedge asserts no
    ceiling and still names the read and the conditional remedy), and it is not
    reachable on today's paths: redis-py mints the class straight from the parsed
    reply, falkordb re-raises the same object, and ``cypher_guard`` re-raises
    bare. Matches ``_MEMORY_CEILING_RE`` against the message HEAD only, so an
    already-explained message never names the ceiling a second time.
    """
    seen: set[int] = set()
    link: BaseException | None = exc
    try:
        while link is not None and id(link) not in seen:
            seen.add(id(link))
            if _MEMORY_CEILING_RE.search(
                    str(link).partition(_LEGIBLE_OPENER)[0]):
                return True
            link = link.__cause__
    except Exception:  # noqa: BLE001, RUF100 — never replace the error being explained
        # ⚠️ THIS WALKS *EVERY* LINK, so unlike `is_memory_ceiling_refusal` (which
        # only stringifies `ResponseError` links) it can read a caller-supplied
        # `__str__` that raises. Same rule as `is_instance_unavailable`'s P1: a
        # legibility predicate must never REPLACE the exception it explains.
        # An unreadable link establishes nothing, and "not established" is the
        # HEDGE arm — the fail-safe direction, because the hedge asserts no
        # ceiling (measured shape: `OutOfMemoryError("OOM …")` whose
        # `__cause__.__str__` raises). Without this belt the raise reaches
        # `cypher_guard._note_instance_outage`, whose own belt swallows it and
        # hands the caller the raw illegible message with NO note at all — the
        # #7357 defect, for exactly that exception.
        return False
    return False


#: How far BELOW a non-zero `maxmemory` a reading may sit and still be the
#: ceiling — the ONE authority for "the ceiling was reached" (#7357 / #7392).
#:
#: The reading is taken AFTER the refusal, and `used_memory` includes transient
#: client buffers that are released when the refused reply is written. MEASURED
#: on falkordb 4.20.6 at a 16 MiB `noeviction` ceiling: immediately after the
#: server's own `used memory > 'maxmemory'` answer the reading sits **3.5-56 KiB
#: BELOW** the cap while the instance is still refusing at it. So a 1-byte or
#: 4 KiB shortfall is the reading, not a different cause, and only a shortfall
#: WIDER than this tolerance is evidence that the ceiling is not the explanation.
#:
#: ⚠️ ONE IMPLEMENTATION, ONE DESCRIPTION (#7357 review P2/P3):
#: `_write_refusal_message` CALLS this function; the SDK's hedge note DESCRIBES
#: the same rule, because it holds an exception and not a reading. They must move
#: together when these numbers do, and the note's wording is pinned where it is
#: ASSERTED — `test_a_class_only_refusal_is_not_told_it_reached_a_ceiling_it_never_read`
#: in `tests/test_7357_memory_ceiling_legibility.py` (a test that never reads the
#: note cannot pin its prose; the drift-value test cannot either).
#:
#: The 1 % arm is a HEURISTIC and is capped at 1 MiB so it cannot scale
#: unbounded: at 1 TiB it would swallow 10 GiB of genuine headroom and print the
#: ceiling sentence for a reading far from it — the over-claim direction, which is
#: the unsafe one (its remedy is `--maxmemory`). The false-negative direction is
#: safe, because the below-ceiling branch withholds nothing and asks for a
#: re-read.
#:
#: A tolerance larger than the ceiling's own scale is meaningless (`0 B of 4 KiB`
#: would print "reached" for every reading); callers that compare against a tiny
#: cap must use their own scale guard, as `_write_refusal_message` does.
def ceiling_reading_drift(cap: int) -> int:
    """Bytes of measured shortfall below *cap* that still count as REACHED."""
    return max(64 << 10, min(cap // 100, 1 << 20))


#: #2981's ownership constraint for a memory-ceiling refusal, as ONE authority.
#:
#: `tortoise/projection/__init__.py::_write_refusal_message` states this policy
#: for the SAME refusal, and two messages for one refusal must not drift into
#: opposite advice (#9532 review P2). The sentence therefore lives HERE — a
#: stdlib-only leaf that the projection already reaches through
#: `tortoise.cypher_guard` — and the projection composes its operator message
#: from this constant instead of carrying a second prose copy, so a change is
#: made once and both readers move together. It is deliberately plain text with
#: no code-span backticks: the projection's message is plain, and a
#: byte-identical string is what makes the binding real rather than a
#: normalisation that silently tolerates a rewrite.
#:
#: The constraint is a PROHIBITION, not a remedy: `GRAPH.DELETE` cannot be
#: undone, a name the caller cannot attribute is not theirs to delete, the
#: production graph and its snapshots are out of scope, and `FLUSHALL` is never
#: an option. See #2981.
MEMORY_CEILING_OWNERSHIP_CONSTRAINT = (
    "Do NOT hand-pick names from GRAPH.LIST to free memory instead: "
    "GRAPH.DELETE cannot be undone, and a name you cannot attribute is not "
    "yours to delete. Never delete the production graph tortoise or a "
    "tortoise_restored* snapshot, and do NOT FLUSHALL."
)


#: Where the ceiling knob lives, per instance class — the ONE copy both arms of
#: :func:`explain_memory_ceiling` use (#7357). #9532 cycle 2/3 P1 is why it names
#: BOTH homes: the SDK emits the note on managed instances too (FalkorDB Cloud —
#: no container, no `REDIS_ARGS`), so a container-only lever would prescribe an
#: action that cannot be taken. Two arms of one note advising the same knob in
#: different words is the drift #2981 rules out, one function apart.
_CEILING_REMEDY_HOMES = (
    "Where that setting lives depends on the "
    "instance: on a container this repo starts it is the `--maxmemory` in "
    "its `REDIS_ARGS`, and on a MANAGED instance (FalkorDB Cloud) it is the "
    "provider's plan/console setting — there is no container there to pass "
    "a flag to."
)


def explain_memory_ceiling(exc: BaseException) -> str | None:
    """Return *exc*'s message with a memory-ceiling diagnosis appended (#7357).

    **The defect this closes.** #7357 is about a memory failure that
    MISDESCRIBES itself. Its original shape was a container SIGKILLed by an
    uncapped host, whose death reached the caller as ``Timeout reading from
    socket`` (#9352) or ``graph was deleted or replaced`` (#8954). #9204 then
    capped every container this repo starts and #9506 gave the lane's own
    instance a Redis-side ``--maxmemory`` BELOW that cap with
    ``--maxmemory-policy noeviction``, so an overshoot is an ANSWERED refusal
    instead of a kill. That conversion is right — and it created the third
    shape of the same misdescription:

        redis.exceptions.OutOfMemoryError:
            command not allowed when used memory > 'maxmemory'.

    MEASURED on this checkout 2026-10-21 (lane container, `maxmemory 1mb`): that
    sentence is what a caller receives, through the guarded channel as well as
    raw. It names no cause, no next action and no owner of the limit; read at
    face value it is indistinguishable from a broken query, a bad write or
    graph damage — the exact failure mode this issue was filed about. Nothing
    in the tree said "memory" (the grep for a ceiling diagnosis was empty).

    **What this says, and what it refuses to claim.** Three facts, all measured:

    - it is a CAPACITY refusal, answered by a HEALTHY instance — not the
      engine's graph-swap abort, not a dead container, not data loss (the
      ceiling and `noeviction` exist precisely so the dataset survives);
    - the command was refused BEFORE it ran, so nothing was applied. The
      measured command is a ``GRAPH.QUERY`` WRITE, which carries ``denyoom``
      (measured: a refused `CREATE` left the graph at 0 nodes). That is the
      claim the note makes — the verbs actually measured — and not the
      absolute "every ``GRAPH.*`` verb", which no measurement in this change
      establishes. It is why the note does not hand the caller the transport
      arm's "OUTCOME IS UNKNOWN, read back first";
    - re-issuing is NOT the next action. The refusal is deterministic while the
      instance is at its ceiling, so the note names the real remedy — RAISE the
      ceiling — instead of inviting a retry loop. It deliberately does NOT
      advise freeing memory by deleting graphs: this seam runs on every
      instance the SDK builds a client for (shared and production included),
      not just a lane's own container, where "a graph this instance no longer
      needs" is not something the caller can know. That is the same ruling the
      repo already recorded for this refusal in
      ``tortoise/projection/__init__.py::_write_refusal_message`` (#2981), and
      two messages for one refusal must not advise opposite actions — a name
      you cannot attribute is not yours to delete, ``GRAPH.DELETE`` cannot be
      undone, and the production graph and its snapshots are out of scope. The
      note carries that constraint VERBATIM by composing
      :data:`MEMORY_CEILING_OWNERSHIP_CONSTRAINT`, the one constant the
      projection's own message is built from too (#9532 review P2: a second
      prose copy in a second module is exactly the drift #2981 forbids), and
      names #2981 so the next reader finds the policy instead of re-deciding it.

    It deliberately does NOT say "your data is at risk" or "raise `--memory`":
    the kernel cap is the backstop, and moving it does not move the refusal —
    only ``maxmemory`` does. Returns ``None`` for anything outside the family,
    keeps the engine sentence at position 0, leaves the exception TYPE alone (a
    wrapper would re-bucket it in ``_classify_db_failure``), and is idempotent
    through :data:`_LEGIBLE_OPENER`.

    ⚠️ **THE CEILING IS A CLAIM, AND IT NEEDS EVIDENCE THE CLASS DOES NOT
    CARRY** (#7357, measured 2026-10-22 on `origin/main` 02e18e752). The note
    used to assert "it has reached its configured dataset limit (`maxmemory`)"
    for every ``OutOfMemoryError``, while the classifier admits the class and
    redis-py mints that class for **every** reply starting with ``OOM`` — so
    ``OutOfMemoryError("OOM failed to allocate 1GB")``, and even
    ``OutOfMemoryError("")``, were told they had reached a ceiling that nothing
    in the exception had read. On an instance with ``maxmemory 0`` (measured on
    the shared container: no ceiling at all; the CI provisioner passes none)
    that sentence prescribes raising a setting that does not exist — the same
    "advice that cannot be acted on" defect this issue is about, pointing at the
    diagnosis instead of the symptom.

    So the note BRANCHES on :func:`_chain_names_the_ceiling`, i.e. on whether
    the exception's own text carries the engine's ceiling sentence. When it
    does, the ceiling note below is unchanged (the sentence is the evidence).
    When it does not, the note states what the message DOES establish — an
    answered memory refusal, so not a dead instance, not graph damage, not data
    loss — names the ceiling only as a CONDITIONAL to be read from the
    instance, and does NOT claim the refused command was rejected before it ran,
    because that was measured for the ``denyoom``/``maxmemory`` refusal and not
    for the allocation-failure shape.

    ⚠️ **IT CANNOT RAISE OVER THE CALLER'S ERROR.** Both the family predicate and
    ``str(exc)`` read caller-supplied data: ``is_memory_ceiling_refusal``
    stringifies ``ResponseError`` links, and a caller-defined ``OutOfMemoryError``
    subclass can carry a ``__str__`` that raises. A legibility helper that
    replaced the exception it explains would hand the caller a traceback instead
    of the failure (the module's P1 rule on :func:`is_instance_unavailable`), so
    the whole body is belted: an unreadable MESSAGE degrades to ``None`` (the raw
    engine sentence, unannotated) because there is nothing to append to. An
    unreadable CHAIN degrades further along — to the hedge, which still tells the
    caller something — because :func:`_chain_names_the_ceiling` establishes no
    ceiling and therefore has a safe answer.
    """
    try:
        return _explain_memory_ceiling(exc)
    except Exception:  # noqa: BLE001, RUF100 — never replace the error being explained
        logger.debug("memory-ceiling note failed", exc_info=True)
        return None


def _explain_memory_ceiling(exc: BaseException) -> str | None:
    """The body of :func:`explain_memory_ceiling`, belted by its caller."""
    if not is_memory_ceiling_refusal(exc):
        return None
    text = str(exc)
    if _LEGIBLE_OPENER in text:
        return None
    if not _chain_names_the_ceiling(exc):
        explains = (
            "the INSTANCE ANSWERED, and what it answered was a MEMORY "
            "refusal whose reply names no dataset ceiling — so a `maxmemory` "
            "ceiling must not be read into it. What IS established: this is "
            "an ANSWERED capacity refusal, not the engine's 'graph was "
            "deleted or replaced' abort, not a dead or restarting instance, "
            "and not evidence that the graph or its data was lost. What is "
            "NOT established: that this instance has a configured "
            "`maxmemory`, that it has reached one, or that the refusal "
            "preceded execution. The driver mints `OutOfMemoryError` for "
            "EVERY reply beginning with `OOM` — a policy refusal and an "
            "allocation failure alike — and an instance reporting "
            "`maxmemory 0` has no dataset ceiling at all: the pressure MAY "
            "come from host/kernel memory, an in-instance per-query cap, this "
            "command's own allocation, or elsewhere — that list is not closed. "
            "So read the instance's `INFO memory` "
            "before acting, and re-read while the refusal is repeating. If it "
            "reports `maxmemory 0`, there is no ceiling to raise — and "
            "re-issuing the same command unchanged will likely be refused the "
            "same way. If it reports a non-zero `maxmemory` and `used_memory` "
            "is at it, ABOVE it, or within a small drift below it, THAT is the "
            "ceiling "
            "and the next action is to raise it — a reading taken after the "
            "refusal sits slightly under the cap (measured: 3.5-56 KiB), so a "
            "near-miss is the reading and not a different cause, and a reading "
            "above the cap (the ordinary shape after the ceiling is lowered on "
            "a busy instance) is the ceiling too. "
            + _CEILING_REMEDY_HOMES
            + " Read back before re-issuing anything that mutates — this "
            "message does not establish that the refusal preceded execution. "
            + MEMORY_CEILING_OWNERSHIP_CONSTRAINT
            + " See #2981."
        )
        return text + _LEGIBLE_OPENER + explains
    explains = (
        "the INSTANCE ANSWERED, and what it answered was a MEMORY-CEILING "
        "refusal: it has reached its configured dataset limit (`maxmemory`) and "
        "refused the command instead of running it. This is a CAPACITY "
        "refusal, not the engine's 'graph was deleted or replaced' abort, not a "
        "dead or restarting instance, and not evidence that the graph or its "
        "data was lost — the ceiling with `noeviction` exists so an overshoot "
        "is refused while the instance and its data stay present. Nothing was "
        "applied: the refused command was rejected BEFORE it ran (measured on a "
        "write: a refused GRAPH.QUERY `CREATE` left the graph at 0 nodes). "
        "Re-issuing it now will be refused the same way — the next action is to "
        "raise the instance's `maxmemory` ceiling (the Redis-side bound on the "
        "DATASET), then re-issue. "
        + _CEILING_REMEDY_HOMES
        + " ONLY a `tools/test_lane.py` container derives that ceiling "
        "from `FALKORDB_CONTAINER_MEMORY` (3/4 of it); the others set it "
        "themselves — the dev container pins 512mb and the CI provisioner "
        "passes none — so raising `FALKORDB_CONTAINER_MEMORY` alone moves the "
        "kernel cap and NOT this refusal. The kernel cap is the backstop, "
        "never the ceiling. "
        + MEMORY_CEILING_OWNERSHIP_CONSTRAINT
        + " See #2981."
    )
    return text + _LEGIBLE_OPENER + explains


class WriteStageRetriesExhausted(Exception):
    """Write-stage retry sentinel (R1, #1786/#1806).

    Raised by :func:`call_with_predicate` ONLY when the predicate-true
    retries exhaust — the inner exception is exposed via ``.original`` and
    ``__cause__`` so the caller can unwrap FIRST and derive error class /
    retryable from the INNER exception (never from the sentinel itself —
    evaluating the predicate on the sentinel would persist
    ``retryable=False`` and permanently lose the write).

    Never constructed for a predicate-FALSE exception: the loop re-raises
    the ORIGINAL exception unchanged, unwrapped (no sentinel, no marker),
    so a fatal-class inner can never reach the caller sentinel-wrapped.
    """

    def __init__(self, original: BaseException):
        self.original = original
        super().__init__(f"write-stage retries exhausted: {original!r}")


def retryable_transient(exc: BaseException) -> bool:
    """True ONLY for transport-class transients a write/retry path may
    retry — never for parse/structural/fatal-class errors.

    Pinned matrix (#1786 Task 1 Step 2 / #1806 indicator 1):
    - redis ``TimeoutError`` / ``ConnectionError`` (redis-py 8.x: NOT the
      builtin classes, reprs ``network:TimeoutError`` / ``network:ConnectionError``)
      → True — the verified write-path loss mechanism.
    - redis ``ResponseError`` matching ``/MISCONF|Can't persist/`` (AOF
      fsync / disk-full write refusal) → True; unrelated ResponseErrors → False.
      **Deliberately NOT here: the replaced-graph / write-lock aborts** (#7405).
      They ARE retryable, but only on the write path
      (:func:`retryable_aborted_write`) — and the graph-abort family only when
      the graph still exists (#7685). Adding them to this predicate as well
      made ONE error retryable at two nested layers — the eval's outer phase
      loop (``tools/longmem_eval/ingest_v2.py``) wrapping the SDK's inner write
      retry — which multiplies the budget with no benefit, because the SDK
      retry already covers the write. So this predicate returns False for them.
    - ``requests``/``urllib`` provider-network errors (LLM provider
      transients) → True.
    - ``OSError`` narrowed to transport errnos (ECONNRESET/ETIMEDOUT/
      EHOSTUNREACH/ENETUNREACH/EPIPE/ECONNREFUSED/ECONNABORTED/ENETDOWN)
      or a socket.timeout cause → True; a deterministic-bug OSError
      (ENOENT/EACCES/...) → False.
    - builtin ``TimeoutError`` (≡ ``socket.timeout`` on 3.10+, where it
      subclasses ``OSError`` — the OSError branch below consumes it) → True
      ONLY with a socket-origin cause (``isinstance(exc.__cause__,
      socket.timeout)``) or a network errno; a BARE local timeout (no
      cause, no errno) → False — a direct ``socket.timeout`` without a
      cause/errno is deliberately NOT retried; drivers wrap socket
      timeouts into the classes above, and the conservative rule protects
      local concurrent.futures/asyncio deadline timeouts.
    - ``urllib.error.HTTPError`` / ``requests.HTTPError`` are EXCLUDED
      FIRST (HTTPError IS-A URLError IS-A OSError on 3.12) — deterministic
      status responses are never retryable.
    """
    import urllib.error

    import redis.exceptions as _re
    import requests

    # HTTPError classes first — they subclass URLError which subclasses
    # OSError (Python 3.12), so they must be excluded before either branch.
    if isinstance(exc, (urllib.error.HTTPError, requests.HTTPError)):
        return False
    if isinstance(exc, _re.TimeoutError):
        return True
    if isinstance(exc, _re.ConnectionError):
        return True
    if isinstance(exc, _re.ResponseError) and _MISCONF_RE.search(str(exc)):
        return True
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, OSError):
        if exc.errno in _NETWORK_ERRNOS:
            return True
        # socket-origin context (on 3.10+ builtin TimeoutError IS-A OSError,
        # so every TimeoutError lands here — bare local timeouts have no
        # cause/errno and correctly resolve False).
        return isinstance(exc.__cause__, socket.timeout)
    return False


def call_with_predicate(fn: Callable[[], Any], *, predicate: Callable[[BaseException], bool],
                        retries: int, what: str, base: float = 2.0,
                        cap: float = 30.0, marker_armed: bool = True,
                        on_retry: Callable[[BaseException], None] | None = None) -> Any:
    """Bounded jittered retry of ``fn`` gated by ``predicate`` (R1, #1786).

    Shared single-source retry helper — any caller that must NEVER retry a
    parse/structural/fatal-class exception uses this (the alternative of a
    retry-everything loop would violate "only transients are retried").

    - predicate-FALSE exception → re-raised IMMEDIATELY, unchanged,
      unwrapped (never a sentinel).
    - predicate-true transients → retried with half-jitter
      ``(0.5 + rand/2) * 2**attempt``; when the budget exhausts the loop
      raises ``WriteStageRetriesExhausted(inner) from inner`` (the R2
      whole-question marker).
    - ``marker_armed=False`` (e.g. a resume re-attempt): the exhausted
      re-raise is the ORIGINAL exception, unwrapped — no sentinel, no R2
      marker (no resume-internal whole-question retry gets a second budget).
    - ``on_retry`` (optional): called with the exception before each
      retry sleep (the caller's per-write retry counter).
    """
    for attempt in range(1, retries + 2):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001, RUF100
            if not predicate(e):
                raise
            if attempt > retries:
                if marker_armed:
                    raise WriteStageRetriesExhausted(e) from e
                raise
            wait = min(base ** attempt, cap) * (0.5 + random.random() / 2)
            if on_retry is not None:
                on_retry(e)
            logger.warning("%s failed (attempt %d/%d): %s; retrying in ~%.1fs",
                           what, attempt, retries, e, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover
