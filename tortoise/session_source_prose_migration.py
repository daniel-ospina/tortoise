"""#11070 — the repair for the PRE-#10852 session ``:Source`` prose population.

WHAT THIS EXISTS FOR
====================
#10852 stopped the capture writing the transcript's first substantive utterance
into ``Source.summary`` (and deriving ``_searchText`` from it). It deliberately
did NOT heal the nodes that already hold the pair, and no OTHER route reaches them:

* a **re-capture** of the same ``session_id`` is the #1727 no-op replay — the
  only production caller of ``_materialize_session_source`` sits inside the
  mint gate (``if not session_existed or (retry_failed_capture and …)``);
* a **record-sourced re-projection** DECLINES the pair for an episodic Source
  (``cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS``), and a decline means
  "do not SET", so an existing value stays;
* **``record_reconcile``** — the module the tracker names — is the COMPARISON,
  not a purge. Its ``declined = episodic_source_prose_properties()`` (and the
  ``placed[declared] = placeable - declined`` branch it feeds) SUBTRACTS the
  pair from what the record is believed to place, so a prose-carrying node is
  reported under ``graph_only`` instead of as a permanent ``field_mismatch``.
  That is bookkeeping for the defect, and it must stay that: an instrument that
  repairs the thing it measures can never report a persistent divergence. It has
  no write path to the cache, and adding one would destroy the observability it
  exists for. So this module is a SEPARATE reconciler, in the shape #4125
  established (``session_source_migration``) — not a second purger bolted onto
  the comparison.

THE CENSUS — AND WHY THE TRACKER'S PREDICATE CANNOT REACH ZERO
==============================================================
#11070's indicator is

    MATCH (s:Source) WHERE s.is_episodic = true
      AND (s.summary <> '' OR s._searchText IS NOT NULL) RETURN count(s)

Two independent defects make that count structurally non-zero on a HEALTHY
graph, so a fixed graph would still read as unfixed:

1. **``is_episodic`` is not the session-Source identity.** The capture commit
   stamps it on every EXTERNAL source of ``payload.sources`` and
   ``backfill_is_episodic.py`` stamps it on every Source reachable from a
   session point, so a DOCUMENT Source carries it — and a document legitimately
   has a ``summary`` and a ``_searchText`` (the Source FTS index is declared on
   the latter). The identity is the repo's ONE declaration,
   :func:`tortoise.cache_projection.is_episodic_source_node` — two arms, never a
   flag — and it is imported here, never re-implemented.
2. **Non-empty is not prose.** A POST-#10852 node carries a topic LEXICON:
   ``summary`` is ``SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)`` and
   ``_searchText`` is the same lexicon bare, so both are non-empty BY DESIGN.
   The WITNESS is the prefix (:mod:`tortoise.session_source_display`): a
   pre-#10000 row's prose carries no prefix. ``summary <> ''`` counts every
   healthy node; :func:`carries_pre_lexicon_prose` does not.

⚠️ One residual is stated rather than implied: a node whose ``summary`` IS
prefixed but whose ``_searchText`` holds something else is NOT a candidate. No
writer can produce that shape (``_source_search_text`` returns the lexicon
whenever ``topics`` is non-empty, and ``_searchText`` is written under the same
guard as ``summary``), and treating it as prose would make a re-derivation loop
out of a node the repair cannot improve.

THE DERIVATION — the RECORD's, through the WRITER's own functions
=================================================================
⛔ The narrative's authoritative copy is the record. A reconciler that reads the
prose off the node and rewrites it is a NO-OP that looks like a repair, so the
turn text is read with :func:`tortoise.record_read.session_turns` and the pair is
re-derived with the capture's OWN functions — ``sdk._session_source_metadata``
and ``sdk._source_search_text``. They are imported LAZILY, inside the call:
``sdk`` imports ``cache_projection``, so a module-level import here would be a
layering inversion the day anything on that path imports this module.

A node whose record DOES NOT ANSWER is reported under ``no_record``, and its
pair is cleared ONLY when the caller asks for that by name
(``clear_without_record=True``) — because §9.1/D1 says the prose does not belong
on a graph node whether or not a lexicon can replace it, while a CLEAR cannot be
undone by a second run (the cleared node stops matching the census, so nothing
re-derives it later). The cleared shape is the writer's own: ``summary=''`` and
an ABSENT ``_searchText``, which is exactly what a text-less capture leaves
(``s._searchText=coalesce($st, s._searchText)`` with ``$st`` None). It never
INVENTS a lexicon — that is the constraint, and it is why the arms are reported
separately.

⛔ ``topics`` IS NOT PART OF THE PROSE PAIR AND IS NEVER WRITTEN — not in either
arm. The pre-fix writer proves it: it derived ``topics`` from
``Counter.most_common(6)`` — the transcript's word frequencies — while
``summary`` was the first utterance, so the two were already INDEPENDENT on this
population (which is why ``cache_projection``'s declined set never contained
``topics``). Writing it would also make ``record-reconcile`` permanently
divergent, because ``topics`` is a projected property the reconciler compares
EXACTLY — so a re-derived value would disagree with the record's own column on
exactly the nodes this repair heals. See :data:`_REPAIR_PAIR`.

REBUILD DETECTION, NOT REBUILD REPAIR
=====================================
The journal is append-only, so a pre-fix capture's prose is still in the replayed
``SourceCreated`` record: ``rebuild_all`` re-mints it. Each whole-journal replay
engine therefore runs :func:`reconcile_after_replay` at the end of its trailing
sweep — and there it is **PLAN-ONLY**, because a replay engine holds no record
connection and the clear arm is destructive and NOT self-correcting (its output
is no longer a candidate). So a replay never heals the graph; it MEASURES the
resurrected population, logs how many nodes carry the pair, and writes nothing.

⛔ Consequence, stated plainly because it is the operator's instruction: after any
whole-journal replay (``rebuild``, ``rebuild_all``, ``recover_from_log``,
``backup.restore``) the repair must be RE-RUN with ``--record-dsn`` AND with
``--graph`` naming the CACHE graph the replay just wrote — without it the CLI
addresses ``derive_uri_graph($TORTOISE_DB_URI)``, which on a hosted or multi-graph
box is not the graph the record scope names, so it finds 0 candidates and prints
``Repair complete.`` over the unrepaired graph. A replay does not restore it. The alternative — clearing at replay — would un-index every
pre-#10852 session irreversibly (the record's row cannot prove the lexicon on the
recovery door, so nothing can re-derive it there) and would then report
``census == 0``, a false PASS; that trade is refused here.

⚠️ Stated residual: the single-record ``apply()`` path does not run the hook at
all (it has no journal-wide view), so a differential replay of only a pre-fix
``SourceCreated`` re-materialises the pair with no report from this module until
the next whole-journal replay or a CLI census.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from tortoise.cache_projection import is_episodic_source_node
from tortoise.session_source_display import SESSION_SOURCE_SUMMARY_PREFIX

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_READ_TIMEOUT_MS",
    "FTS_PROBE_ENTITY_TYPE",
    "carries_pre_lexicon_prose",
    "census",
    "find_session_sources_carrying_prose",
    "lexicon_control_token",
    "probe_fts",
    "probe_phrase_rejection",
    "reconcile_after_replay",
    "reconcile_session_source_prose",
    "record_turn_reader",
    "source_fts_probe",
]

#: Wall-clock bound on the cache-wide Source read. Bounded, never truncated —
#: the same rule ``record_reconcile.scan_cache_view`` states: a ``LIMIT`` would
#: need its own truncation detection to stay honest, and a silently truncated
#: read UNDERCOUNTS the population this module exists to drive to zero.
#:
#: ⛔ THIS IS A REQUEST, NOT A GUARANTEE. FalkorDB refuses a query whose
#: ``TIMEOUT`` parameter exceeds the server's ``TIMEOUT_MAX``, and that cap is a
#: DEPLOYMENT setting — the canonical docker test lane runs
#: ``TIMEOUT_MAX 30000`` (``tools/test_lane.py:LANE_FALKORDB_ARGS``), so a bare
#: 60 000 here raised ``ResponseError: The query TIMEOUT parameter value cannot
#: exceed the TIMEOUT_MAX configuration parameter value`` on every run against
#: it: the census could never execute, and ``--repair`` wrote the pair and then
#: died in ``census_after``. Measured, not inferred (#11152 review P1).
#: :func:`_effective_timeout_ms` clamps the request to the server's own cap.
CACHE_READ_TIMEOUT_MS = 60_000

#: The subset of ``FalkorDB``'s error text that means "your requested timeout is
#: above MY cap" — not "the query ran long". Matched as a substring because the
#: message is the server's, and it is the only signal that separates the two.
_TIMEOUT_CAP_ERR = "TIMEOUT parameter value cannot exceed"

#: A cut of ``record_read.session_turns``'s PARTIAL reason that names the TAIL
#: witness. Kept ONLY as a fallback for an answer object that carries no
#: ``Turn.index`` — the production reasons DO contain this wording
#: (``… — the tail is missing, so this is a partial transcript``), which is
#: exactly why it cannot be the primary test: a diagnostic keyed on wording is
#: what became dead code in round 3 (the constant then held the NAME
#: ``turn_max_index``, which no reason ever contains) and its test passed over it.
#: The REAL test is structural, in :func:`record_turn_reader`.
_TAIL_WITNESS_HINT = "the tail is missing"

#: OFF-SWITCH for the census's NODE-IDENTITY dedupe (#11176). `=1` restores the
#: PRE-FIX path EXACTLY: one record per ROW ``_SOURCE_ROWS`` returns, carrying
#: that row's ``turn_max_index`` — so a ``:Source`` reached by two ``:Session``
#: rows is counted TWICE, the tail witness is whatever row arrived first, and
#: ``duplicate_session_rows`` is ABSENT (no count was taken).
#:
#: ⛔ NARROW BY DESIGN — only the exact string ``1`` disables it, and the site is
#: declared in ``tests/test_env_truthy.py``'s ``_KNOWN_NARROW_READS`` ledger with
#: its ``OVERRIDES`` reason (#4097/#4128). A `=true` written for legibility would
#: silently leave the FIXED census running, i.e. an operator believing they
#: re-measured with the old instrument would get a number taken by the new one.
#:
#: ⛔ WHY A SWITCH EXISTS ON A BUG FIX. This dedupe changes the NUMBER the whole
#: repair is verified BY (``census_after``, i.e. #10000 criterion 2), so an
#: operator holding a census recorded with the old instrument must be able to
#: re-measure with the OLD instrument — without reverting the module — before
#: concluding that a population grew. It is a kill switch, not a preference:
#: the default is the fixed path, and only the exact string ``1`` turns it off.
_CENSUS_DEDUPE_DISABLED = "TORTOISE_11070_CENSUS_DEDUPE_DISABLED"

#: The property names this repair reads. ⛔ It WRITES only the pair — see
#: :data:`_REPAIR_PAIR`.
_KIND = "sourceKind"
_SUMMARY = "summary"
_SEARCH_TEXT = "_searchText"
_TOPICS = "topics"
#: The census's per-row node identity (`id(s)`). The write addresses ONE node by
#: it; see `_REPAIR_PAIR`'s ⛔.
_NODE_ID = "node_id"

#: Only the properties the predicate READS. `properties(s)` would drag every
#: Source's dense `embedding` across the wire — one vector per document on a
#: corpus — and this read runs on every replay engine's trailing sweep.
#:
#: ⛔ ONE ROW PER MATCHING `:Session`, NOT ONE PER `:Source` (#11176). `:Session.id`
#: carries a plain INDEX and no UNIQUE constraint (``CREATE CONSTRAINT`` count is
#: 0 in this repo), so two `:Session` nodes can carry one ``sessionId`` and this
#: OPTIONAL MATCH then fans one `:Source` out into one row per session. The
#: census dedupes those rows on ``id(s)`` — see
#: :func:`find_session_sources_carrying_prose`.
_SOURCE_ROWS = (
    "MATCH (s:Source) "
    "OPTIONAL MATCH (sess:Session {id: s.sessionId}) "
    "RETURN s.url, s.sourceKind, s.summary, s._searchText, s.topics, "
    "       s.sessionId, sess.turn_max_index, id(s)"
)
#: THE ONE WRITE — the pair, and nothing else, in both arms.
#:
#: ⛔ ``topics`` IS DELIBERATELY NOT WRITTEN. It is not part of the prose datum:
#: the pre-#10000 writer took it from ``Counter.most_common(6)`` (word
#: frequencies) while ``summary`` was the first utterance, so the two were
#: already INDEPENDENT on this population — which is why ``cache_projection``'s
#: declined set never contained it. And the record's own ``topics`` column is
#: what ``record-reconcile`` compares EXACTLY (``topics`` is a projected
#: property, so it is not cache-owned): writing a re-derived value here would
#: make that reconciliation permanently divergent on exactly the population this
#: repair heals — one door over from the "signal indistinguishable from noise"
#: failure ``record_reconcile``'s docstring grades P1. Leaving it untouched also
#: keeps this route inside its declared bound, which is the pair.

# ⛔ THE MATCH IS A NODE MATCH — by the census's own `id(s)` — AND a
# COMPARE-AND-SET, and BOTH are load-bearing.
#
# * `:Source` declares NO uniqueness constraint on `url` — the repo's own
#   reconciler names that state as REACHABLE (``record_reconcile.py``: "the
#   graph declares no uniqueness constraint, so a second writer's ``CREATE``
#   can leave TWO nodes at one (label, key)"). A bare
#   ``MATCH (s:Source {url:$url})`` is therefore a POPULATION match, not a node
#   match: with a HEALTHY sibling at the same url (a ``record-restore``d node —
#   no ``summary``, ``_searchText`` = the lexicon) BOTH arms wrote that sibling,
#   and the CLEAR arm removed its only index entry while no bucket named it.
#   Measured on a real FalkorDB: the census selected ONE node, the write landed
#   on two (PR #11152 review F1).
# * The PAIR ALONE IS NOT AN IDENTITY. Two url-mates can carry an IDENTICAL pair
#   — the most likely duplicate shape, and the only one that matters: the
#   blank-`summary` candidate arm exists for a residue whose ``_searchText`` is
#   the lexicon while its ``topics`` do not prove it, and its HEALTHY twin (same
#   url, same absent ``summary``, same lexicon, topics that DO prove it) has a
#   byte-identical pair. So a value-only guard leaves the F1 symptom intact for
#   exactly that class, and two identical CANDIDATES make the second row's write
#   match nothing and report "NOTHING was written" about a url the same report
#   lists as written (PR #11152 review round 2, P2). The census therefore
#   carries ``id(s)`` and this write addresses ONE node.
# * The pair stays as the SECOND guard: it is what the census decided on, so a
#   node that changed between the read and the write is NOT written, and
#   ``census_after`` (RE-MEASURED, never decremented) shows the miss.
#
# ``{url:$url}`` is kept in the ``MATCH`` on purpose: it is the indexed seek,
# and ``id(s)`` is the filter over the handful of nodes it returns.
#
# ⚠️ The pair comparison is STRING-NORMALIZED (``coalesce(...)=''``), because a
# raw non-string param can trip ``_guard_numeric_params`` mid-run. A candidate
# holding a non-string pair value therefore cannot be compared at all and is
# reported as ``unaddressable`` BY NAME (a pinned census, never a silent one).


_REPAIR_PAIR = (
    "MATCH (s:Source {url:$url}) "
    "WHERE id(s)=$nid "
    "AND coalesce(s.summary,'')=$old_sum "
    "AND coalesce(s._searchText,'')=$old_st "
    "SET s.summary=$sum, s._searchText=$st "
    "RETURN id(s)"
)


# ── the census predicate ──────────────────────────────────────────────────


def carries_pre_lexicon_prose(props: Mapping[str, Any]) -> bool:
    """True when this node holds the PRE-#10852 derivation of the pair.

    Two clauses, and both are load-bearing:

    * **the identity** — :func:`~tortoise.cache_projection.is_episodic_source_node`
      (the repo's ONE declaration: a ``session:`` url, or an ``agentSession``
      Source on a separator-free bare filename). ``is_episodic=True`` is NOT a
      signal: the capture commit stamps it on every external source and
      ``backfill_is_episodic`` on every Source reachable from a session point,
      so keying on it selects DOCUMENTS — whose ``summary``/``_searchText`` are
      legitimate, and which is why the tracker's count was pinioned above zero.
    * **the witness** — the node carries text, and its ``summary`` does NOT
      start with ``SESSION_SOURCE_SUMMARY_PREFIX``. Every post-#10852 node does,
      so this is what tells a lexicon from prose: a legacy row's
      ``summary == _searchText == <utterance>`` and the utterance can itself be
      a comma-space list that coincides with ``", ".join(topics)`` exactly. The
      prefix cannot be produced by prose (#10852 review P2).

    ⛔ A ``summary`` that is empty while ``_searchText`` carries text is the
    shape the RECORD-RESTORE door writes TODAY, and it is HEALTHY: the projection
    DECLINES ``summary`` for an episodic Source while projecting the lexicon
    ``_searchText`` (#10965), so ``node_from_row`` yields exactly
    ``{..., _searchText: <lexicon>, topics: [...]}`` with no ``summary``. Keying
    the blank-summary arm on emptiness alone made every restored session a
    candidate, so a ``--repair`` on a ``record-restore``d graph cleared the whole
    tenant's Source index. The arm therefore requires that the node NOT prove the
    lexicon: ``topics`` a non-empty list of non-empty strings AND
    ``_searchText == ", ".join(topics)`` — the same proof
    ``cache_projection._episodic_source_search_text_is_the_lexicon`` uses, applied
    to the node's property names. A pre-fix row's ``_searchText`` is the utterance
    and its ``topics`` are the word frequencies, so the join does not hold.
    ⛔ WHAT THIS ARM CAN AND CANNOT DECIDE. It is a NO-OP on the true pre-fix
    population: that writer could not emit a blank ``summary`` with a non-empty
    ``_searchText`` (its text-less branch returns ``("", [])`` and its other arm
    always sets ``summary``), so every genuine pre-fix node carries a
    non-prefixed, non-blank ``summary`` and is caught by the clause above. What
    this arm actually decides is the RESIDUE of the record-restore shape: a node
    with no ``summary`` whose ``topics`` do not prove its ``_searchText``. It
    fails toward CANDIDATE — which is the DESTRUCTIVE direction here, the
    opposite of the projection's polarity, and it is deliberate only because
    :func:`_proves_the_lexicon` is an exact two-term proof rather than a shape
    test: the arm exists for a node whose blank ``summary`` plus unexplained
    ``_searchText`` cannot be the record door's output. It is reported, never
    silent.
    """
    if not isinstance(props, Mapping) or not is_episodic_source_node(props):
        return False
    summary = props.get(_SUMMARY)
    search_text = props.get(_SEARCH_TEXT)
    summary = summary if isinstance(summary, str) else ""
    search_text = search_text if isinstance(search_text, str) else ""
    if not summary.strip() and not search_text.strip():
        return False
    if summary.startswith(SESSION_SOURCE_SUMMARY_PREFIX):
        return False
    # The blank-`summary` arm: a record-RESTORED node has no `summary` (the
    # projection declines it for an episodic Source) but a lexicon `_searchText`
    # with the `topics` it was joined from. Healthy — never a candidate.
    return bool(summary.strip()) or not _proves_the_lexicon(props)


def _proves_the_lexicon(props: Mapping[str, Any]) -> bool:
    """True when the node's own ``topics`` PROVE ``_searchText`` is the lexicon.

    The node-property twin of
    :func:`tortoise.cache_projection._episodic_source_search_text_is_the_lexicon`
    (which answers for a RECORD ROW): EXACT equality against the join, never a
    shape test, and a ``topics`` that is not a list of non-empty strings proves
    nothing. ⚠️ The POLARITY is the opposite of that function's, and deliberately:
    there an unproven row keeps the pair DECLINED (a false positive would put
    prose back in the index), while here an unproven node stays a CANDIDATE (a
    false negative leaves prose on a node, which is visible in the census and
    recoverable by the record-re-derived run — a false positive would destroy a
    healthy node's index entry).
    """
    search_text = props.get(_SEARCH_TEXT)
    if not isinstance(search_text, str) or not search_text:
        return False
    topics = props.get(_TOPICS)
    if not isinstance(topics, (list, tuple)) or not topics:
        return False
    tokens = [t for t in topics if isinstance(t, str) and t]
    if len(tokens) != len(topics):
        return False
    return search_text == ", ".join(tokens)


def _session_id(props: Mapping[str, Any]) -> str | None:
    """The session this node belongs to — derived, never invented.

    ``s.sessionId`` is the capture's own property; a legacy bare-filename node
    has none, and its url carries no id (:func:`derive_session_source_url` is
    the inverse direction), so there is nothing to read the record BY. That
    node is reported (``no_record``, with a note) and left alone unless
    ``clear_without_record`` is set — it is never attributed to a guess
    (#4125's attribution rule).
    """
    sid = props.get("sessionId")
    if isinstance(sid, str) and sid.strip():
        return sid
    url = props.get("url")
    if isinstance(url, str) and url.startswith("session:"):
        tail = url[len("session:"):]
        return tail if tail.strip() else None
    return None


def _server_timeout_cap(g) -> int | None:
    """The server's ``TIMEOUT_MAX``, or ``None`` when it cannot be asked.

    ``0`` means UNCAPPED, which FalkorDB reports as such — it is not "a cap of
    zero milliseconds", so it is folded into ``None`` here.

    ⛔ ASKING BEATS GUESSING, AND THE VERB IS ON THE **CLIENT**, NOT THE GRAPH.
    A ``falkordb.Graph`` **instance** has NO ``config_get`` and NO ``connection``
    attribute (measured: ``hasattr(g, 'config_get') is False``,
    ``hasattr(g, 'connection') is False``, ``hasattr(g, 'client') is True`` —
    ``client`` is an INSTANCE attribute set in ``Graph.__init__``, not a class
    attribute, so ``hasattr(Graph, 'client')`` is False and quoting that as the
    measurement is itself misleading); the projection's ``_GuardedGraph``
    forwards to the same thing. Probing the graph handle itself therefore found
    nothing and the clamp silently never ran: the 60 000 ms request went out
    unclamped and every read took the ResponseError path below. That is how a
    "fix" can be dead code on every real deployment while its unit test, which
    put ``config_get`` on the fake graph, passes (#11152 review round 2, P1).
    The handles are tried CLIENT-FIRST for that reason, and the test fake models
    the client shape rather than this one.

    A server that cannot answer is NOT an error — the retry in
    :func:`_read_source_rows` is the last resort.
    """
    candidates = (getattr(g, "client", None),
                  getattr(g, "connection", None),
                  getattr(getattr(g, "_g", None), "client", None),
                  g)
    for handle in candidates:
        getter = getattr(handle, "config_get", None)
        if getter is None:
            continue
        try:
            cap = getter("TIMEOUT_MAX")
        except Exception:  # an unaskable server is not a failure
            continue
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            continue
        return cap if cap > 0 else None
    return None


def _effective_timeout_ms(g, requested_ms: int) -> int:
    """*requested_ms*, clamped DOWN to the server's own ``TIMEOUT_MAX``.

    Clamped, never dropped: the bound this module promises stays a bound on a
    server that will state its cap. On an uncapped server (``TIMEOUT_MAX 0``, the
    FalkorDB default) the request is honoured as written.

    ⚠️ A server that will NOT state its cap may still refuse the request (see
    :func:`_read_source_rows`); this function cannot know that, so it returns the
    request it would issue — it is the REQUEST, not a claim about what the read
    carried.
    """
    cap = _server_timeout_cap(g)
    if cap is None or requested_ms <= cap:
        return int(requested_ms)
    return int(cap)


def _read_source_rows(g, timeout_ms: int) -> list[dict]:
    requested = int(timeout_ms)
    effective = _effective_timeout_ms(g, requested)
    try:
        rows = g.query(_SOURCE_ROWS, timeout=effective).result_set
    except Exception as exc:  # discriminated immediately below
        # ⛔ LAST RESORT, and only for the CAP class. A server that will not
        # answer `config_get` (an older build, a proxy, a seam in a test) can
        # still REFUSE an over-cap request, and "the census cannot run anywhere"
        # is the defect being fixed — so on exactly that message the read is
        # retried with NO explicit timeout, which is bounded by the server's own
        # policy instead of by ours. ⚠️ Declared cost: on a server whose default
        # `TIMEOUT` is non-zero (FalkorDB's own default is 1000 ms) and whose cap
        # is below our request, that retry can itself time out — and a timeout is
        # NOT the cap class, so it PROPAGATES rather than being retried again. The
        # caller sees a hard failure, never a short read reported as a census.
        # Any other exception is the query's, and belongs to the caller.
        if _TIMEOUT_CAP_ERR not in str(exc):
            raise
        rows = g.query(_SOURCE_ROWS).result_set
    out: list[dict] = []
    for row in rows or ():
        props = _row_props(row)
        if props is not None:
            out.append(props)
    return out


def _row_props(row) -> dict | None:
    """One ``_SOURCE_ROWS`` row as a property map, or ``None`` for an empty row.

    Split out so the FTS leg's hit-classifier parses a Source row the SAME way
    the census does — one row layout, one decoder (see :data:`_HITS_ROWS`).
    """
    if not row:
        return None
    tmi = row[6] if len(row) > 6 else None
    # `id(s)` — the node identity the write addresses by (`_REPAIR_PAIR`'s
    # ⛔). `None` when the row is short (a seam/fake that projects seven
    # columns): such a row cannot be addressed and is reported, never
    # guessed at.
    nid = row[7] if len(row) > 7 else None
    return {"url": row[0], _KIND: row[1], _SUMMARY: row[2],
            _SEARCH_TEXT: row[3], _TOPICS: row[4], "sessionId": row[5],
            "turn_max_index": tmi if isinstance(tmi, int) else None,
            _NODE_ID: nid if isinstance(nid, int) else None}


def _census_dedupe_enabled() -> bool:
    """Read the #11176 off-switch AT CALL TIME.

    Read per call, never cached at import: the switch exists so one measurement
    can be taken with the old instrument and the next with the new one, and a
    module-level cache would make the second the same as the first.
    """
    return os.environ.get(_CENSUS_DEDUPE_DISABLED) != "1"


def _strongest_mark(first: Any, second: Any) -> int | None:
    """The STRONGER (higher) of two ``turn_max_index`` marks, ``None`` if neither.

    ⛔ THE STRONGEST, NOT THE FIRST — this is the safety-relevant half of
    #11176. The mark is handed to ``record_read.session_turns`` as the tail
    witness: a mark BELOW the session's true id-space end serves a truncated
    transcript as COMPLETE, and the lexicon derived from half a transcript is
    the fabricated descriptor this module refuses to write everywhere else. A
    mark ABOVE the end has its own named cause (:func:`_no_record_note` names
    the cache as the suspect), so taking the maximum loses nothing — while
    taking the first row's value makes REFUSE-vs-WRITE depend on FalkorDB's row
    order. A non-``int`` mark is not a mark: the other side wins.
    """
    marks = [value for value in (first, second)
             if isinstance(value, int) and not isinstance(value, bool)]
    return max(marks) if marks else None


def find_session_sources_carrying_prose(
        g, *, timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> list[dict]:
    """READ-ONLY: every NODE this repair would touch, with its byte sizes.

    The dry-run/audit surface. Writes nothing of any kind.

    ⛔ ONE RECORD PER ``id(s)``, NOT ONE PER ROW (#11176). ``_SOURCE_ROWS`` fans
    a `:Source` out into one row per `:Session` at its ``sessionId``, and
    ``:Session.id`` has no uniqueness constraint — so without this dedupe a
    single node is counted TWICE, which inflates the very number #10000
    criterion 2 is measured by, and hands the tail witness to whichever row
    arrived first. The dedupe keeps the FIRST position (so the report's order
    stays the ``url`` sort) and the STRONGEST mark among the node's rows (:func:
    `_strongest_mark`). The duplicate ROW count is kept on the record as
    ``duplicate_session_rows`` — a census that silently collapses rows hides the
    duplicate-`:Session` signal, and that is a graph-health fact its caller is
    entitled to see.

    A row with no ``int`` node identity is appended AS-IS: there is nothing to
    dedupe on, and it cannot be addressed by the write either — it is reported
    under ``unaddressable`` by the reconciler, never silently merged into a peer.

    The record carries ``duplicate_session_rows`` (rows beyond the first) ONLY
    where the dedupe MEASURED it: under the off-switch the key is ABSENT, because
    no count was taken and ``0`` would read as a measurement.

    ``TORTOISE_11070_CENSUS_DEDUPE_DISABLED=1`` restores the pre-fix path
    exactly (one record per row, first-row mark, no duplicate count); see
    :data:`_CENSUS_DEDUPE_DISABLED`.
    """
    out: list[dict] = []
    #: ``id(s)`` -> the record already emitted for that node, so the second row
    #: of a duplicated `:Session` updates a mark instead of appending a record.
    seen: dict[int, dict] = {}
    dedupe = _census_dedupe_enabled()
    for props in _read_source_rows(g, timeout_ms):
        if not carries_pre_lexicon_prose(props):
            continue
        summary = props.get(_SUMMARY)
        search_text = props.get(_SEARCH_TEXT)
        record = {
            "url": props.get("url"),
            "session_id": _session_id(props),
            # The session's LIVE id-space end, read in the SAME scan. It is the
            # only witness for the one PARTIAL shape `record_read` cannot see
            # from the row set (a refused HIGHEST turn), so a reader built
            # without it serves a truncated transcript as COMPLETE — and a
            # lexicon derived from half a transcript is the fabricated
            # descriptor this module refuses to write (#11152 review P2).
            "turn_max_index": props.get("turn_max_index"),
            "summary_bytes": len(summary.encode("utf-8")) if isinstance(summary, str) else 0,
            "search_text_bytes": (len(search_text.encode("utf-8"))
                                  if isinstance(search_text, str) else 0),
            # ⛔ THE NODE IDENTITY (`id(s)`) AND THE PAIR THE CENSUS DECIDED ON.
            # Carried because the write must addres ONE node (`_REPAIR_PAIR`'s
            # ⛔): the pair alone is not an identity — two url-mates can carry a
            # byte-identical pair, and then a value-only match writes a node
            # this row never selected. A re-read at write time would compare
            # against values no census vouched for; omitting them made every
            # write match nothing (measured).
            # ⚠️ `summary`/`_searchText` are the transcript's own TEXT. They
            # exist for the write, not for the report: the CLI REDACTES them
            # (with `node_id`) out of `candidate_detail`, which is what gets
            # printed and serialised (`--json`), because this population's
            # `summary` IS a user utterance. Deleting them here — instead of
            # redacting there — is what would silently disarm the write.
            _NODE_ID: props.get(_NODE_ID),
            _SUMMARY: summary,
            _SEARCH_TEXT: search_text,
        }
        node_id = record.get(_NODE_ID)
        if dedupe and isinstance(node_id, int) and not isinstance(node_id, bool):
            #: Rows this NODE contributed beyond the first. ⛔ SET ONLY WHERE IT WAS
            #: MEASURED — inside the dedupe branch, never on the record's initial
            #: shape. Under the off-switch no de-duplication happens, so the count is
            #: not a `0`, it was never taken; emitting `0` there would report a graph
            #: with two `:Session` rows at one `sessionId` as "no duplicates" — the
            #: exact signal this field exists to carry, hidden as a measurement
            #: (#11176 review P2).
            record["duplicate_session_rows"] = 0
            already = seen.get(node_id)
            if already is not None:
                already["turn_max_index"] = _strongest_mark(
                    already.get("turn_max_index"), record.get("turn_max_index"))
                already["duplicate_session_rows"] += 1
                continue
            seen[node_id] = record
        out.append(record)
    out.sort(key=lambda n: str(n["url"]))
    return out


def census(g, *, timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> int:
    """The population size — the number this repair must be able to drive to 0."""
    return len(find_session_sources_carrying_prose(g, timeout_ms=timeout_ms))


# ── the record side ───────────────────────────────────────────────────────


def record_turn_reader(conn: Any, *, org_id: str, graph_id: str
                       ) -> Callable[..., str | None]:
    """A transcript reader over the RECORD, for :func:`reconcile_session_source_prose`.

    Takes ``(session_id, turn_max_index)``. ⛔ ``turn_max_index`` is NOT optional
    decoration: the record's own read names it as the ONLY witness for one of
    the three PARTIAL shapes — a transcript that stops BELOW the session's live
    id-space end, which is invisible to the row set itself (a refused HIGHEST
    turn; ``record_read.session_turns``'s third PARTIAL bullet). Passing
    ``None`` there makes that shape come back ``COMPLETE``, so the caller would
    write a lexicon derived from half a transcript — the fabricated descriptor
    this whole reader exists to refuse. The census reads the mark off the
    ``:Session`` node in the same scan it already makes; a caller that truly
    cannot name it passes ``None`` and accepts the degraded answer knowingly.

    ⚠️ Stated residual, WIDENED because the narrow form under-claimed it: the
    mark is ``None`` — and the tail arm is then unreachable — for THREE shapes,
    not one. (a) the candidate's ``:Session`` node is missing (`OPTIONAL MATCH`
    yields NULL); (b) its ``sessionId`` is null, so the `OPTIONAL MATCH` cannot
    bind; and (c) the node EXISTS but its ``turn_max_index`` is absent or
    non-int, which :func:`_read_source_rows` also maps to ``None``. Each one
    silently degrades the tail witness; none of them is a record that declined
    to answer.

    ``None`` means THE RECORD DID NOT ANSWER for those turns — which is not the
    same as "the session has no turns" (``TurnAnswer.NO_ACCOUNT`` and
    ``EMPTY`` are different answers, and #10000 review P0-3 exists because
    conflating them serves an empty transcript over a store that still holds the
    text). Both are ``None`` here on purpose: this repair never derives a
    lexicon from a partial or absent answer, and the node is cleared + reported
    instead. A PARTIAL answer is refused for the same reason — a lexicon built
    from half a transcript is a fabricated descriptor.
    """
    def read(session_id: str, turn_max_index: int | None = None) -> str | None:
        from tortoise.record_read import session_turns

        answer = session_turns(conn, org_id=org_id, graph_id=graph_id,
                              session_id=session_id,
                              turn_max_index=turn_max_index)
        # ⛔ The REASON travels with the answer. A bare ``None`` forces the caller
        # to guess WHY, and the guesses are not interchangeable: a PARTIAL caused
        # by the CACHE's ``turn_max_index`` being ahead of the record is not a
        # missing or mis-scoped record at all (see :func:`_no_record_note`).
        # Recorded on the closure so the caller can name it without changing the
        # ``str | None`` contract.
        #
        # ``getattr``, not attribute access: this module needs only ``answered``
        # and ``turns`` from the answer, and a seam (a test double, an older
        # ``TurnAnswer``) that omits the diagnostic fields must not turn a
        # correct read into an AttributeError.
        #
        # ⛔ THE TAIL SUSPECT IS COMPUTED STRUCTURALLY, NOT MATCHED FROM THE
        # WORDING. An earlier version tested the reason string for
        # ``"turn_max_index"``, which NO production reason contains (the message
        # interpolates the VALUE, never the name) — so the whole diagnostic was
        # dead code that a hand-written test could not distinguish from live code
        # (#11152 review round 3, P2). ``Turn.index`` is on the answer already, so
        # the exact condition `_shortfall` refuses on is recomputed here.
        turns = getattr(answer, "turns", ()) or ()
        # ⛔ A **SET**, because `record_read._shortfall` uses one
        # (`held = {turn.index for turn in turns}`). A LIST disagrees with it
        # exactly when an index REPEATS — reachable: `_turn_index` accepts any
        # `int()`-parsable suffix, so a backfilled `s_t01` and `s_t1` both yield
        # 1 — and `[0, 1, 3, 3]` is contiguous to a list-based check while it is a
        # GAP to `_shortfall`. That re-opened the round-5 misdiagnosis: the note
        # named the record's own hole and then blamed the cache mark in the next
        # sentence (#11152 review P2).
        held = {t.index for t in turns
                if isinstance(getattr(t, "index", None), int)}
        # ⛔ THE TAIL ARM IS EVALUATED **LAST** BY `_shortfall`, so only claim it
        # when the two arms that run BEFORE it cannot explain the refusal. A bare
        # `max(held) < turn_max_index` is a SUPERSET: the record's own short-count
        # arm (`len(turns) < turn_count`) and gap arm (a hole in `0 .. max`) are
        # refused before the tail is ever looked at, and blaming the cache for
        # those sends the operator to compare a mark that is irrelevant
        # (#11152 review round 4).
        count = getattr(answer, "turn_count", None)
        short_count = isinstance(count, int) and len(turns) < count
        # ⛔ THE GAP ARM MEASURES FROM ZERO, NOT FROM `min(held)`.
        # `record_read._shortfall` computes `span = max(held) + 1` and
        # `missing_count = span - len(held)`, so a run whose EARLIEST rows are
        # missing is a gap there and contiguous to a `range(min(held), max+1)`
        # comparison — which misdiagnosed exactly that case as the cache mark's
        # fault, quoting the record's own "no turn row for index 0" and then
        # blaming the cache in the next sentence (#11152 review round 5, P2).
        gap = bool(held) and (max(held) + 1) != len(held)
        tail_suspect = bool(
            not getattr(answer, "answered", False) and turn_max_index is not None
            and held and max(held) < int(turn_max_index)
            and not short_count and not gap)
        read.reasons[session_id] = (getattr(answer, "kind", ""),  # type: ignore[attr-defined]
                                    getattr(answer, "reason", ""), tail_suspect)
        if not answer.answered:
            # NO_ACCOUNT, CONTRADICTION, or a PARTIAL set — the record did not
            # answer for the WHOLE session, and a lexicon built from half a
            # transcript would be a fabricated descriptor.
            return None
        if not answer.turns:
            # EMPTY: the record SPOKE and states the session has no turns. That
            # is an answer, and it is not a DSN/scope problem.
            return ""
        # The writer's transcript is ONE ``Speaker: body`` line per turn, joined
        # by newlines (``sdk._session_llm_transcript``). ``Turn.line`` is the
        # record's declaration of that same rendering rule, so re-deriving
        # through it cannot drift from the writer's frame.
        return "\n".join(t.line for t in answer.turns)

    read.reasons = {}  # type: ignore[attr-defined]
    return read


def _no_record_note(read_transcript, session_id: str | None) -> str:
    """WHY the record did not answer — named, because the guesses differ.

    ⛔ THE TAIL WITNESS IS THE CACHE'S. ``record_turn_reader`` forwards the
    ``:Session.turn_max_index`` it was handed, and this repair exists because the
    CACHE has diverged from the record — so a mark that is AHEAD of the record's
    live end makes ``record_read``'s ``_shortfall`` refuse a session the record
    answered COMPLETELY, and the node is then reported under ``no_record``, whose
    own name says the record was at fault. Measured shape (#11152 review round 2,
    P2): the diagnosis is wrong and the repair under-heals silently. Refusing is
    still the SAFE direction (a lexicon from a possibly-truncated transcript is
    the fabricated descriptor this module refuses), so the behaviour stays and
    the note names the suspect — and the operator can compare the mark with the
    record's live end before concluding the record is missing data.
    """
    reasons = getattr(read_transcript, "reasons", None)
    kind, reason, tail_suspect = ("", "", False)
    if isinstance(reasons, dict):
        entry = reasons.get(session_id) or ("", "", False)
        kind, reason, tail_suspect = (*tuple(entry), "", "", False)[:3]
    note = ("the record did not ANSWER for this session, so no lexicon is "
            "invented")
    if reason:
        note += f" ({kind}: {reason})"
    if tail_suspect or _TAIL_WITNESS_HINT in str(reason or ""):
        note += (". ⚠️ THIS MAY BE THE CACHE'S FAULT, NOT THE RECORD'S: the tail "
                 "witness handed to the record read is the CACHE's "
                 "`:Session.turn_max_index`, and a mark AHEAD of the record's "
                 "live end makes a complete answer read as PARTIAL — compare the "
                 "mark with the record before concluding the record is short")
    return note


def _add_note(report: dict, url: str, text: str) -> None:
    """ACCUMULATE a note — never replace one.

    ⛔ A node can have more than one thing wrong with it, and the second write
    used to erase the first: a one-argument reader's "the tail witness was
    dropped" note was destroyed by the not-answered note written right after it —
    exactly when the operator most needs to know the diagnosis is unreliable
    (#11152 review round 4).
    """
    previous = report["notes"].get(url)
    report["notes"][url] = f"{previous}; {text}" if previous else text


def _derive(transcript: str | None) -> dict:
    """The capture's OWN derivation — imported lazily, never re-implemented."""
    if transcript is None:
        # The writer's text-less shape: `summary=''` (its clause is
        # `coalesce(s.summary,'')`) and NO `_searchText` at all (`$st` is None and
        # FalkorDB drops a null property). Passing `''` here would mint a state no
        # writer reaches.
        return {"sum": "", "st": None}
    # LAZY: `sdk` imports `cache_projection`, so a module-level import here is a
    # layering inversion the day anything on that path imports this module.
    from tortoise.sdk import _session_source_metadata, _source_search_text

    summary, topics = _session_source_metadata(transcript)
    search_text = _source_search_text(topics=topics)
    return {"sum": summary, "st": search_text}


# ── the repair ────────────────────────────────────────────────────────────


def reconcile_session_source_prose(
        g, *,
        read_transcript: Callable[..., str | None] | None = None,
        dry_run: bool = True,
        timeout_ms: int = CACHE_READ_TIMEOUT_MS,
        found: list[dict] | None = None,
        clear_without_record: bool = False) -> dict:
    """Clear the pre-#10852 prose pair on every episodic session ``:Source``.

    Idempotent and re-runnable: the output is not a candidate (:func:`carries_
    pre_lexicon_prose` rejects a prefixed ``summary``), so a second run finds
    nothing and the census reaches 0 on the repaired graph.

    ``dry_run=True`` (the DEFAULT) writes nothing and returns the same report
    shape. ⚠️ The default DIVERGES from #4125's reconciler, which defaults to
    writing: this one destroys a value that has no graph-side copy, so the plan
    is the thing an operator approves, and a bare call can never be the run that
    did it.

    Returns (the SAME keys in both modes):
      ``candidates``  urls carrying the pair,
      ``repaired``    urls whose pair was re-derived FROM THE RECORD,
      ``empty_sessions``  urls whose record SPOKE and states the session has 0
                      turns — cleared, and NOT a record/scope problem,
      ``no_record``   urls whose pair was CLEARED because the record did not
                      ANSWER (never a fabricated lexicon),
      ``no_reader``   urls this run never asked the record about at all — a
                      reader was not supplied. ⛔ NOT folded into
                      ``no_record``: collapsing "nobody asked" into "the record
                      was silent" sends an operator hunting a DSN or a
                      row-level-security problem that does not exist, which is
                      the inverse of the conflation #10000 review fixed one
                      bucket over,
      ``left_alone``  urls NOT written at all because the record could not
                      supply a replacement and ``clear_without_record`` is
                      False. Clearing is the arm that cannot be undone by a
                      second run (the second run finds no candidate), so it is
                      taken only when ASKED FOR BY NAME — the same rule #4125's
                      operator CLI states for its own destructive arm,
      ``unaddressable``  urls a CANDIDATE that the write cannot address, and
                      therefore urls that pin ``census_after`` above zero while
                      they exist: no usable url, or no census node id / a
                      non-string pair value (the write compares the pair as
                      text). Named on every arm, never silent — repair the node
                      by hand or delete it,
      ``notes``       url -> why a candidate was handled the way it was,
      ``census_before``  the population, measured,
      ``census_after``   the population re-MEASURED after the writes; equal to
                      ``census_before`` on a dry run, because nothing was
                      written. Re-measured rather than decremented: a write that
                      silently missed (a node deleted mid-run) must show up as a
                      census that did not move, which `before - healed` hides,
      ``dry_run``     the flag this call carried.
    """
    # `found` lets a caller that has ALREADY taken the census (the CLI's dry run,
    # which wants the byte sizes) hand it in instead of paying for a second
    # whole-`Source` scan.
    if found is None:
        found = find_session_sources_carrying_prose(g, timeout_ms=timeout_ms)
    report: dict = {
        "candidates": [n["url"] for n in found],
        "repaired": [],
        "empty_sessions": [],
        "no_record": [],
        "no_reader": [],
        "no_session_id": [],
        "unaddressable": [],
        "left_alone": [],
        "written": [],
        "notes": {},
        "census_before": len(found),
        "census_after": len(found),
        "dry_run": bool(dry_run),
        "timeout_ms": _effective_timeout_ms(g, timeout_ms),
    }
    for node in found:
        url = node["url"]
        if not isinstance(url, str) or not url:
            # ⛔ A CANDIDATE THAT CANNOT BE ADDRESSED. `carries_pre_lexicon_prose`
            # accepts an empty-`url` `agentSession` Source (the bare-filename arm
            # needs only "no `://` and no `/`"), and the census counts it — but it
            # cannot be written, so it pinned `census_after` above zero FOREVER
            # with `--repair` exiting 1 and no reason given anywhere in the
            # report: an unreachable zero, which is the class #11070 exists to
            # close (#11152 review round 4).
            report["unaddressable"].append(node.get("url"))
            _add_note(report, "<empty url>",
                      "a candidate with no usable url: it carries the prose pair "
                      "and counts in the census, but cannot be addressed by a "
                      "write, so `census_after` cannot reach 0 while it exists — "
                      "repair the node by hand or delete it (#11070)")
            continue
        sid = node["session_id"]
        # FOUR answers, and they are not three. "" is the record SAYING the
        # session has no turns (TurnAnswer.EMPTY); None is the record NOT
        # ANSWERING (NO_ACCOUNT / CONTRADICTION / a PARTIAL set); and NO READER
        # is a third thing — nobody asked — which must never be reported as the
        # record's silence (#11152 review P2). Collapsing the first two sent an
        # operator hunting a DSN or row-level-security problem for a session
        # whose record had spoken; collapsing the third does the same for a
        # record that was never opened.
        asked = read_transcript is not None and sid is not None
        transcript: str | None = None
        if asked:
            try:
                transcript = read_transcript(sid, node.get("turn_max_index"))
            except TypeError:
                # A reader declared as ``read(session_id)`` — the one-argument
                # shape. Degraded (no turn-end witness), and said so in the
                # notes rather than crashing the run.
                transcript = read_transcript(sid)  # type: ignore[call-arg]
                _add_note(report, url,
                          "the reader takes one argument, so the session's "
                          "turn_max_index was not supplied — a PARTIAL set that "
                          "stops below the id-space end cannot be detected by it")
        elif read_transcript is None:
            report["no_reader"].append(url)
        else:
            # ⛔ A FOURTH BUCKET, and it needs its own counter: with a reader
            # supplied, a node carrying no derivable session id satisfies
            # neither `no_reader` (a reader WAS supplied) nor `no_record` (the
            # record was never asked), so it landed in no bucket at all — and on
            # the clear arm that made a DESTRUCTIVE write reported as nothing
            # (#11152 review round 2, P2).
            report["no_session_id"].append(url)
        if asked and transcript is None:
            report["no_record"].append(url)
            why = _no_record_note(read_transcript, sid)
        elif transcript is None:
            why = ("no session id is derivable from this node (a pre-#4005 "
                   "bare-filename url, or none), so the record cannot be read "
                   "BY session" if sid is None else
                   "no transcript reader was supplied, so the record was never "
                   "asked")
        if transcript is None:
            # ⛔ The note must state what HAPPENED, not what the arm would have
            # done: emitting "left alone" on the run that cleared the node — or
            # "the pair is CLEARED" on a DRY RUN that wrote nothing — is a report
            # contradicting its own writes (#11152 review rounds 2 and 4).
            if dry_run:
                outcome = (" — a dry run writes NOTHING; --repair with "
                           "clear_without_record set would CLEAR it"
                           if clear_without_record else
                           " — a dry run writes NOTHING; --repair would leave it "
                           "alone unless clear_without_record is set")
            else:
                outcome = (" — the pair is CLEARED, because clear_without_record "
                           "is set" if clear_without_record else
                           " — the pair is left alone unless clear_without_record "
                           "is set")
            _add_note(report, url, why + outcome)
        if transcript is None:
            # ⛔ THE ARM THAT CANNOT BE UNDONE BY A SECOND RUN. A clear is not
            # self-correcting: the damaged node no longer matches the census, so
            # the next run cannot find it to re-derive. It is therefore taken
            # ONLY when asked for by name — an empty or wrong record scope reads
            # as "no answer" for EVERY session (#10000 review P0-1), and a
            # default that destroys on that answer is one bad DSN away from
            # un-indexing a tenant's whole Source population.
            if not clear_without_record:
                report["left_alone"].append(url)
                continue
            values = _derive(None)
        elif transcript == "":
            report["empty_sessions"].append(url)
            values = _derive("")
        else:
            report["repaired"].append(url)
            values = _derive(transcript)
        if dry_run:
            continue
        # ⛔ `updatedAt` is deliberately NOT written: the reconciler must not
        # invent state a writer did not produce (#4125's rule). The pair is a
        # DERIVATION being corrected, not a new capture.
        #
        # The PAIR only, in both arms — `topics` is left exactly as it was (see
        # `_REPAIR_PAIR`'s ⛔).
        # ⛔ THE WRITE ADDRESSES ONE NODE: the census's own `id(s)`, with the
        # pair it READ on that row as the second guard (see `_REPAIR_PAIR`'s ⛔).
        # The pair alone is not an identity — two url-mates can carry a
        # byte-identical pair — and a MATCH on `{url:$url}` alone writes
        # whatever else lives at that url.
        #
        # The pair comparison is string-normalized on the Cypher side, so a row
        # whose pair value is not a string cannot be compared AT ALL. Passing the
        # raw value would let a float into `_guard_numeric_params` mid-run, and
        # normalizing it to "" would compare `12 = ''` — a write that can never
        # match, silently pinning `census_after` above zero under a note that
        # blames a concurrent change (PR #11152 review round 2, P2). Such a node
        # is NAMED in `unaddressable` instead: a pinned census, never a silent
        # one.
        old_summary = node.get(_SUMMARY)
        old_search_text = node.get(_SEARCH_TEXT)
        node_id = node.get(_NODE_ID)
        if (not isinstance(node_id, int)
                or not isinstance(old_summary, (str, type(None)))
                or not isinstance(old_search_text, (str, type(None)))):
            report["unaddressable"].append(url)
            _add_note(report, url,
                      "this candidate cannot be addressed by the write: its "
                      "census row carries no node id, or a pair value that is "
                      "not a string (the write compares the pair as text). It "
                      "counts in the census and cannot be repaired here — "
                      "repair the node by hand or delete it (#11070)")
            continue
        wrote = g.query(_REPAIR_PAIR, params={
            "url": url,
            "nid": node_id,
            "old_sum": old_summary if isinstance(old_summary, str) else "",
            "old_st": (old_search_text if isinstance(old_search_text, str)
                       else ""),
            "sum": values["sum"], "st": values["st"]})
        # ⛔ The count the hook reports must come from the WRITE, not from
        # arithmetic over the buckets: `no_record` and `left_alone` overlap
        # (`left_alone` also holds `no_session_id`/`no_reader` nodes), so
        # subtracting one from the other reported fewer repairs than were made
        # and contradicted its own parenthetical (#11152 review round 5, P2).
        # And a write that matched NOTHING is not a write: since the match is a
        # compare-and-set, an empty result is a node that changed under the run
        # (or whose only url-mates are healthy siblings). Counting it as
        # repaired is the same contradiction one layer down.
        if getattr(wrote, "result_set", None):
            report["written"].append(url)
        else:
            _add_note(report, url,
                      "the write matched NO :Source at this url whose current "
                      "pair is the one the census read — the node changed "
                      "between the census and the write (or every node at this "
                      "url is a different, healthy one), so NOTHING was "
                      "written; re-run")
    if not dry_run and found:
        # Re-MEASURED, never decremented — see the docstring's `census_after`.
        # Only when something was written: on an empty candidate set the value is
        # provably 0, and every replay engine runs this hook, so a second
        # whole-`Source` property scan for a known answer is a real cost on the
        # recovery path.
        report["census_after"] = census(g, timeout_ms=timeout_ms)
    return report


def reconcile_after_replay(projection, *, engine: str,
                           read_transcript: Callable[..., str | None] | None = None
                           ) -> dict | None:
    """End-of-replay-sweep entry point for the whole-journal replay engines.

    ⛔ WITHOUT A RECORD HANDLE THIS IS PLAN-ONLY, and that is a correctness
    requirement, not caution. The clear arm is destructive and NOT
    self-correcting (its output is no longer a candidate, so a later
    ``--repair --record-dsn`` finds nothing and writes nothing), and a replay
    engine holds no record connection. A hook that cleared here would un-index
    the whole pre-#10852 population on any rebuild performed before the
    operator's repair — irreversibly, because the record's row cannot prove the
    lexicon on this door — and would then report ``census == 0``, a FALSE PASS of
    exactly the kind this issue exists to close. So a replay with no reader
    reports the population and writes nothing; the operator's run, which has the
    record, is what heals it.

    NEVER raises: this runs AFTER the wipe, where an exception would leave an
    unproven graph (#2943 "no loss without proof"). A projection without a graph
    handle (an injected fake in a test) is a silent no-op.
    """
    g = getattr(projection, "g", None)
    if g is None:
        return None
    try:
        report = reconcile_session_source_prose(
            g, read_transcript=read_transcript,
            dry_run=read_transcript is None)
    except Exception:  # noqa: BLE001, RUF100
        logger.exception(
            "%s: pre-#10852 session :Source prose reconciliation FAILED — the "
            "graph is rebuilt but nodes captured before the fix still carry the "
            "transcript's utterance in `summary`/`_searchText`; re-run "
            "graph-scripts/11070_repair_session_source_prose.py --repair "
            "--graph <THE CACHE GRAPH THIS REPLAY JUST WROTE> --record-dsn … "
            "--org-id … --graph-id … (#11070). ⛔ NAME THE CACHE GRAPH: "
            "without --graph the CLI reads derive_uri_graph($TORTOISE_DB_URI), "
            "which on a hosted or multi-graph box is NOT the graph --graph-id "
            "names (#11152 review round 6).",
            engine)
        return None
    if report["candidates"]:
        if report["dry_run"]:
            # LOUD, because the graph is now in a state the operator must finish:
            # the replayed journal carried the pre-fix prose back in.
            logger.warning(
                "%s: %d session Source(s) carry the pre-#10852 prose pair and "
                "this replay engine has no record handle, so NOTHING was "
                "written (a clear here would be irreversible and would hide the "
                "population). Run graph-scripts/11070_repair_session_source_prose"
                ".py --repair --graph <THE CACHE GRAPH THIS REBUILD JUST WROTE> "
                "--record-dsn … --org-id … --graph-id … (#11070). "
                "⛔ NAME THE CACHE GRAPH: without --graph the CLI reads "
                "derive_uri_graph($TORTOISE_DB_URI), which on a hosted or "
                "multi-graph box is NOT the graph --graph-id names — it finds 0 "
                "candidates and prints 'Repair complete.' over the unrepaired "
                "graph (#11152 review round 5, P1).",
                engine, len(report["candidates"]))
        else:
            logger.info(
                "%s: repaired the pre-#10852 prose pair on %d of %d session "
                "Source(s) (%d re-derived from the record, %d the record did "
                "not answer, %d left alone) (#11070)", engine,
                len(report["written"]), len(report["candidates"]),
                len(report["repaired"]), len(report["no_record"]),
                len(report.get("left_alone") or []))
    return report



# ── the FTS leg — the SECOND half of the verification (#11161) ────────────
#
# ⛔ THE CENSUS IS NOT THE TARGET. #11070's own indicator says why in one
# sentence: "the whole point is that the sentence is also in the FTS index, so
# the verification is a search that stops finding it". The census reads NODE
# PROPERTIES; the FTS index is a DIFFERENT artifact built from them
# (``projection._REQUIRED_FULLTEXT_INDEXES`` declares ``("Source", "_searchText",
# "FULLTEXT")``). A repair — or a later regression — that cleared the pair on the
# node while the indexed tokens stayed findable passes every census and leaves
# the leak live in the only way a user experiences it: the sentence is still
# retrievable. So the verification needs a query that STOPS RETURNING THE
# SENTENCE, and that is what this section provides.
#
# ⛔ AND A HIT IS NOT ALWAYS A LEAK — the distinguishing is what makes the leg
# usable rather than a red light nobody believes. FalkorDB's FTS tokenises and
# STEMS, so a probe token can come back from a node that is already repaired:
# measured on a real FalkorDB, the probe ``fridays`` still returns a node whose
# ``_searchText`` is the lexicon ``migration, plan, friday`` — the stem
# ``friday`` matches. That node's indexed text is NOT the sentence; it is the
# descriptor the post-#10852 writer produces. A leg that called that a leak would
# report a CORRECT repair as unfinished, and the way an operator silences it is
# by clearing the lexicon — the #3518 descriptor. So every hit is CLASSIFIED
# against the module's own predicate: a hit whose node still carries
# pre-lexicon prose is RESIDUE (a real leak); a hit whose node carries the
# prefix is a stem collision, reported separately and never as a failure.
#
# ⛔ AND A ZERO FROM A DEAD INSTRUMENT IS NOT A ZERO. Three ways the leg can read
# ``0`` while measuring nothing:
#
# 1. **The probe never matched anything** — an operator-invented phrase that is
#    not in the corpus hits 0 BEFORE the repair too, so its 0 after proves
#    nothing. That is what the BEFORE/AFTER pair is for: a probe with
#    ``hits_before > 0`` that now reads 0 is the discriminating evidence, and a
#    probe whose before was ALSO 0 is reported as undiscriminating rather than
#    counted as a pass.
# 2. **The index read itself returned nothing** — an FTS circuit breaker that is
#    open, an index dropped, or a query that failed all surface to
#    ``run_fts_query`` as ``[]``. ``leg_trace`` is therefore read on EVERY probe
#    and a leg that did not run (or ran degraded) is reported as UNMEASURABLE,
#    never as a clean zero. The positive CONTROL is the second half of the same
#    guard: one token taken from a POST-fix lexicon node's own ``_searchText``
#    must still be findable, because that proves the index answers at all.
# 3. **The read was TRUNCATED** — a hit count that reaches the read's ``LIMIT``
#    means the probe matched more nodes than were classified, so residue beyond
#    the window would be invisible. A probe that common is not a probe: it is
#    reported as ``truncated`` and it cannot certify.
#
# ⚠️ Why the leg goes through :func:`tortoise.search_engine.run_fts_query` and
# not a raw ``CALL db.idx.fulltext.queryNodes``: that function is the product's
# ONE source-FTS read, and it carries the ``documentKind IS NULL`` discriminator
# (a document IS a ``:Source``, D10/#5026) plus the escaping and the measured
# breaker. A second query here would be a second derivation of the same read.

#: The label whose FULLTEXT index the sentence leaks through. A document is a
#: ``:Source`` too, so ``run_fts_query``'s own ``documentKind IS NULL`` filter is
#: what keeps this from measuring the document corpus.
FTS_PROBE_ENTITY_TYPE = "source"

#: The read's window. Every returned hit is CLASSIFIED (see the section header),
#: so this is a classification budget, not a count: a probe that fills it is
#: common enough that its residue could hide beyond the window, and
#: :func:`probe_fts` marks that as ``truncated`` instead of certifying.
_FTS_PROBE_LIMIT = 50

#: The SAME columns as :data:`_SOURCE_ROWS` (so :func:`_row_props` decodes both),
#: filtered to the urls the FTS read returned — one read for the whole hit set,
#: so the classifier costs one query per probe, not one per hit.
_HITS_ROWS = (
    "MATCH (s:Source) "
    "WHERE s.url IN $urls "
    "RETURN s.url, s.sourceKind, s.summary, s._searchText, s.topics, "
    "       s.sessionId, s.turn_max_index, id(s)"
)

#: One post-fix lexicon node's ``_searchText``, for the positive control. Read by
#: the WITNESS (the ``summary`` prefix), which is the same discriminator
#: :func:`carries_pre_lexicon_prose` uses — so the control queries a node the
#: lexicon writer produced, not one this repair left behind.
_CONTROL_LEXICON_ROWS = (
    "MATCH (s:Source) "
    "WHERE s.summary STARTS WITH $prefix "
    "AND s._searchText IS NOT NULL "
    "AND s.documentKind IS NULL "
    "RETURN s._searchText LIMIT 1"
)


def probe_phrase_rejection(phrase: str) -> str | None:
    """Why *phrase* cannot serve as an FTS probe, or ``None`` when it can.

    ⛔ A PROBE MUST BE ONE TOKEN, AND THE REASON IS THE READ PATH'S OWN
    SEMANTICS, not tidiness. ``run_fts_query`` routes the query through
    :func:`tortoise.sparse.build_or_query`, which OR-joins a multi-token input
    (``'the second-latest PR comment'`` → ``comment|second|latest|pr``). A probe
    measured that way returns rows for ANY of its tokens — and a post-fix
    lexicon's words are ordinary words — so a CORRECT repair would still read
    "leaked", and the obvious way to silence it is to clear the lexicon, i.e.
    destroy the descriptor #3518 exists to provide. Measured on the live hosted
    graph: the single token ``the`` returns the pre-fix fixture at rank 1, and a
    phrase that occurs nowhere still returns rows once it tokenises.

    The identifier mechanism (#8933) is the second way one typed token becomes
    several (``zx-99`` → ``zx|zx99|"zx 99"``), so that shape is refused too.

    ⚠️ What this CANNOT decide is RARITY — whether the token exists in the
    corpus. That is measured, not declared: the before/after pair on the writing
    arm, and the ``truncated`` flag on a probe that matches too much.
    """
    token = (phrase or "").strip()
    if not token:
        return "it is empty"
    # Lazy: `sparse` pulls the tokenizer + identifier machinery, and this module
    # is imported by every whole-journal replay engine's trailing sweep.
    from tortoise.sparse import build_or_query, tokenize_sparse_query

    tokens = tokenize_sparse_query(token)
    if len(tokens) != 1:
        return (f"it tokenises to {len(tokens)} term(s) {tokens!r}; the FTS read "
                f"OR-joins multi-term input, so the probe would match on any "
                f"one of them — pass a SINGLE RARE token that appears only in "
                f"the pre-fix prose (a stopword-only phrase is not a probe)")
    built = build_or_query(token)
    if len(built.split("|")) != 1:
        return (f"the identifier mechanism expands it into several terms "
                f"({built!r}); pass the plain token without an identifier shape "
                f"(no digit/dash runs)")
    return None


def source_fts_probe(g, phrase: str, *,
                     timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> dict:
    """Ask the Source FTS index whether *phrase* is still findable.

    Returns ``{"phrase", "hits", "urls", "truncated", "ran", "degraded",
    "reason"}``. ``hits`` counts the rows the read returned (capped at
    :data:`_FTS_PROBE_LIMIT`), so ``0`` reads as "not findable"; ``urls`` is what
    the classifier resolves, and ``truncated`` says the window filled — i.e. the
    count is a FLOOR and residue could be hiding past it.

    ``ran``/``degraded``/``reason`` come from the read path's own ``leg_trace``:
    a breaker-open or index-missing leg returns ``[]`` exactly as a clean miss
    does, and folding those together is the false PASS this leg exists to
    prevent.

    ``timeout_ms`` is CLAMPED to the server's own cap: the canonical docker lane
    runs ``TIMEOUT_MAX 30000``, and an unclamped 60 000 raised ``ResponseError``
    on every run of the census before #11152's review found it — the same
    deployment setting applies to this query.
    """
    from tortoise.search_engine import run_fts_query  # lazy (see module docs)

    trace: list[dict] = []
    rows = run_fts_query(
        g, phrase, entity_type=FTS_PROBE_ENTITY_TYPE, limit=_FTS_PROBE_LIMIT,
        timeout_ms=_effective_timeout_ms(g, timeout_ms), leg_trace=trace)
    leg = next((e for e in trace if e.get("leg") == "fts"), {})
    urls = [row[0] for row in rows or () if row and isinstance(row[0], str)]
    return {
        "phrase": phrase,
        "hits": len(rows or ()),
        "urls": urls,
        "truncated": len(rows or ()) >= _FTS_PROBE_LIMIT,
        "ran": bool(leg.get("ran")),
        "degraded": bool(leg.get("degraded")),
        "reason": leg.get("reason"),
    }


def classify_fts_hits(g, urls, *,
                      timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> dict:
    """Split *urls* into RESIDUE (still pre-fix prose) and LEXICON (repaired).

    ⛔ THE SPLIT IS THE WHOLE POINT OF THE LEG (see the section header): the index
    stems, so a probe token can come back from a node whose indexed text is
    already the topic lexicon — a correct repair, not a leak. The classifier
    applies the module's own :func:`carries_pre_lexicon_prose`, so the two halves
    cannot drift from the census.

    Returns ``{"residue": [...], "lexicon": [...], "unread": [...]}``. A url the
    read did not return is ``unread`` — a hit that could not be classified is
    NOT evidence either way, and the caller must not fold it into ``lexicon``.
    """
    wanted = [u for u in (urls or ()) if isinstance(u, str) and u]
    out: dict = {"residue": [], "lexicon": [], "unread": []}
    if not wanted:
        return out
    try:
        rows = g.query(_HITS_ROWS, params={"urls": wanted},
                       timeout=_effective_timeout_ms(g, timeout_ms)).result_set
    except Exception:  # noqa: BLE001, RUF100 — an unreadable classification is
        # reported as `unread`, never raised: this runs inside the verification
        # step, where an exception would be a crash instead of a verdict.
        logger.debug("#11070: the FTS hit classification read failed",
                     exc_info=True)
        out["unread"] = list(wanted)
        return out
    props_by_url = {}
    for row in rows or ():
        props = _row_props(row)
        if props is not None and isinstance(props.get("url"), str):
            props_by_url[props["url"]] = props
    for url in wanted:
        props = props_by_url.get(url)
        if props is None:
            out["unread"].append(url)
        elif carries_pre_lexicon_prose(props):
            out["residue"].append(url)
        else:
            out["lexicon"].append(url)
    return out


def lexicon_control_token(g, *,
                          timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> str | None:
    """One token from a POST-fix lexicon node, or ``None`` when there is none.

    The positive control: a token the lexicon writer put on a node must still be
    findable, which is what makes the probes' zero evidence that the index
    ANSWERS rather than evidence that the read is dead.
    """
    try:
        rows = g.query(_CONTROL_LEXICON_ROWS,
                       params={"prefix": SESSION_SOURCE_SUMMARY_PREFIX},
                       timeout=_effective_timeout_ms(g, timeout_ms)).result_set
    except Exception:  # noqa: BLE001, RUF100 — a control that cannot be read is
        # reported as ABSENT by the caller, never raised: the probe result is the
        # finding, and an exception here would turn "unmeasurable" into a crash.
        logger.debug("#11070: the FTS control read failed", exc_info=True)
        return None
    for row in rows or []:
        text = row[0] if row else None
        if not isinstance(text, str):
            continue
        for token in text.split(","):
            candidate = token.strip()
            if candidate and probe_phrase_rejection(candidate) is None:
                return candidate
    return None


def probe_fts(g, phrases, *, control: bool = True,
              timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> dict:
    """Measure the Source FTS index for every probe phrase, plus the control.

    Returns::

        {"ran": bool,                # at least one probe was measured
         "entity_type": "source", "limit": 50,
         "probes": [{phrase, hits, urls, truncated, ran, degraded, reason,
                     residue, lexicon, unread}, ...],
         "leaked": [phrase, ...],          # residue still findable — a LEAK
         "stem_only": [phrase, ...],       # hits, but every one is a lexicon node
         "unmeasurable": [phrase, ...],    # the leg did not run / is degraded
         "truncated": [phrase, ...],       # the window filled — a FLOOR, not a 0
         "control": {"ok", "hits", "note"} | None}

    ⚠️ ``control`` is only meaningful AFTER a repair has written a lexicon: on a
    graph with no post-fix node it is ``{"ok": False, "hits": None, "note": …}``
    and the caller reports why rather than treating the absence as a failure of
    the repair itself.
    """
    measured: list[dict] = []
    for phrase in phrases:
        got = source_fts_probe(g, phrase, timeout_ms=timeout_ms)
        split = classify_fts_hits(g, got["urls"], timeout_ms=timeout_ms)
        got.update(split)
        measured.append(got)
    report: dict = {
        "ran": bool(measured),
        "entity_type": FTS_PROBE_ENTITY_TYPE,
        "limit": _FTS_PROBE_LIMIT,
        "probes": measured,
        "leaked": [p["phrase"] for p in measured if p["residue"]],
        "stem_only": [p["phrase"] for p in measured
                      if p["hits"] and not p["residue"] and not p["unread"]],
        "unmeasurable": [p["phrase"] for p in measured
                         if not p["ran"] or p["degraded"]],
        "truncated": [p["phrase"] for p in measured if p["truncated"]],
        "control": None,
    }
    if control:
        token = lexicon_control_token(g, timeout_ms=timeout_ms)
        if token is None:
            report["control"] = {
                "ok": False, "hits": None,
                "note": "no POST-fix lexicon node on this graph, so the index "
                        "read cannot be corroborated by a known-present token "
                        "(a graph that has only ever been CLEARED has none)"}
        else:
            # ⛔ The TOKEN IS NOT ECHOED. It is a transcript-derived word (the
            # capture's own topic lexicon), and the dry run's report is
            # deliberately redacted by whitelist so no piece of the utterance
            # reaches stdout or `--json`; only the measured COUNT is reported.
            got = source_fts_probe(g, token, timeout_ms=timeout_ms)
            report["control"] = {
                "ok": bool(got["hits"]), "hits": got["hits"],
                "ran": got["ran"], "degraded": got["degraded"],
                "reason": got["reason"],
                "note": "one token of a post-fix lexicon node's `_searchText`",
            }
    return report
