"""#11070 — repair the PRE-#10852 ``:Source`` prose population, and a census
that can actually reach zero.

WHAT IS BROKEN (measured, not reasoned)
=======================================
A capture that ran BEFORE #10852 wrote the transcript's first substantive
utterance into ``Source.summary`` and derived ``_searchText`` from that same
value. #10852 stopped the writer doing it again; it deliberately did not heal
the nodes already holding the pair, and no route reaches them:

* a re-capture is the #1727 no-op replay (the mint gate), pinned in
  ``tests/test_10000_source_prose.py``;
* a record-sourced re-projection DECLINES the pair for an episodic Source
  (``cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS``), and a decline means
  "do not SET", so the existing value stays;
* ``record_reconcile`` — the module the tracker points at — is the COMPARISON.
  Its ``declined = episodic_source_prose_properties()`` marks the pair
  CACHE-OWNED so it lands in ``graph_only`` instead of as a permanent
  ``field_mismatch``. It has no write path to the cache and should not get one.

⛔ THE SECOND HALF OF THIS FILE IS THE CENSUS, AND IT IS NOT BOOKKEEPING.
#11070's own indicator is

    MATCH (s:Source) WHERE s.is_episodic = true
      AND (s.summary <> '' OR s._searchText IS NOT NULL) RETURN count(s)

and that count CANNOT REACH ZERO on a healthy graph, for two independent
reasons, each with a leg below:

1. **``is_episodic`` is not the session-Source identity.** The capture commit
   stamps it on every EXTERNAL source of ``payload.sources``, and
   ``backfill_is_episodic.py`` stamps it on every Source reachable from a
   session point — so a DOCUMENT Source carries it. A document legitimately has
   ``summary`` and ``_searchText`` (its FTS field is declared on the latter and
   ``backfill_document_search_text`` writes it), so the number is structurally
   pinioned above zero by the document population. The identity is the repo's
   ONE declaration, :func:`tortoise.cache_projection.is_episodic_source_node`
   (two arms, never a flag).
2. **Non-empty is not prose.** A POST-#10852 node carries a topic LEXICON:
   ``summary`` is ``SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)`` and
   ``_searchText`` is the same lexicon bare. Both are non-empty by design, so
   ``summary <> ''`` counts every healthy node. The WITNESS that separates the
   two derivations is the prefix: a pre-#10000 row's prose carries no prefix
   (``session_source_display``).

A predicate that cannot reach zero cannot verify a fix, so the census in this
module is built from those two declarations and the legs below pin both.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path

import pytest

from tortoise.session_source_display import SESSION_SOURCE_SUMMARY_PREFIX

#: The transcript slice a pre-fix capture left on the node.
PROSE = "We decided the weekly rebuild happens on Fridays."


# ── fakes ──────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _FakeClient:
    """The CLIENT handle — where ``config_get`` actually lives."""

    def __init__(self, graph):
        self._graph = graph

    def config_get(self, name):
        if self._graph.config_get_raises:
            raise RuntimeError("this server cannot be asked")
        assert name == "TIMEOUT_MAX"
        return self._graph.timeout_cap


class _RecordingGraph:
    """A graph that RECORDS every write, so ``dry_run`` can be proven.

    Reads answer from ``rows``; a write returns one node. Every statement (and
    its params) is kept in ``statements`` so a test can assert that a dry run
    issued none, and that a repair wrote the values it claims.
    """

    WRITE_MARKERS = ("SET ", "REMOVE ", "DELETE ", "MERGE ")

    #: The columns the census read projects (it must NOT pull `embedding`, so the
    #: graph is asked for these by name and the fake answers in the same order).
    COLUMNS = ("url", "sourceKind", "summary", "_searchText", "topics",
               "sessionId", "turn_max_index")

    #: The server's ``TIMEOUT_MAX`` for this fake, in ms. ``0`` = uncapped, which
    #: is FalkorDB's own default. A NON-ZERO value makes the fake REFUSE an
    #: over-cap request exactly as the server does — the canonical docker test
    #: lane runs ``TIMEOUT_MAX 30000`` (``tools/test_lane.py``), and a fake that
    #: silently ignored ``timeout`` let a 60 000 ms request ship that could never
    #: execute there (#11152 review P1: the census raised on every run).
    TIMEOUT_CAP_ERR = ("The query TIMEOUT parameter value cannot exceed the "
                       "TIMEOUT_MAX configuration parameter value")

    def __init__(self, rows, *, timeout_cap=0, config_get_raises=False):
        self._rows = rows
        self.timeout_cap = timeout_cap
        self.config_get_raises = config_get_raises
        self.statements: list[tuple[str, dict]] = []
        self.requested_timeouts: list[int | None] = []
        # ⛔ THE VERB LIVES ON THE CLIENT, NOT THE GRAPH. A real
        # `falkordb.Graph` has NO `config_get` and NO `connection` — measured
        # `hasattr(Graph,'config_get') is False`, `hasattr(Graph,'client') is
        # True` — and the projection's `_GuardedGraph` forwards to the same
        # object. An earlier version of this fake put `config_get` ON THE GRAPH,
        # which made a dead clamp look alive: the module never found the cap on a
        # real handle, so it never clamped (#11152 review round 2, P1).
        self.client = _FakeClient(self)

    #: Deliberately ABSENT: `config_get` is not an attribute of this object, and
    #: neither is `connection` — a regression that goes back to probing the bare
    #: graph handle now fails here instead of passing quietly.
    @property
    def connection(self):
        raise AttributeError("a real falkordb Graph has no `.connection`")

    def query(self, cypher, params=None, timeout=None):
        if any(m in cypher for m in self.WRITE_MARKERS):
            self.statements.append((cypher, dict(params or {})))
            return _Result([("session:whatever",)])
        self.requested_timeouts.append(timeout)
        if self.timeout_cap and timeout and int(timeout) > int(self.timeout_cap):
            raise RuntimeError(self.TIMEOUT_CAP_ERR)
        return _Result([tuple(props.get(c) for c in self.COLUMNS)
                        for (props,) in self._rows])

    @property
    def writes(self):
        return [s for s in self.statements]


def _session_node(**overrides):
    props = {
        "url": "session:s-11070",
        "sourceKind": "agentSession",
        "sessionId": "s-11070",
        "is_episodic": True,
        "summary": PROSE,
        "_searchText": PROSE,
    }
    props.update(overrides)
    return props


def _document_node(**overrides):
    """A DOCUMENT Source that ``is_episodic`` misclassifies as a session Source."""
    props = {
        "url": "https://example.com/q3-pricing",
        "sourceKind": "document",
        "is_episodic": True,
        "summary": "Q3 pricing changes",
        "_searchText": "Q3 pricing changes",
    }
    props.update(overrides)
    return props


def _post_fix_node(**overrides):
    topics = ["migration", "plan", "friday"]
    props = {
        "url": "session:s-post",
        "sourceKind": "agentSession",
        "sessionId": "s-post",
        "is_episodic": True,
        "summary": SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics),
        "_searchText": ", ".join(topics),
    }
    props.update(overrides)
    return props


# ── the census: it must be able to reach 0 ─────────────────────────────────


def test_the_census_finds_a_session_source_carrying_pre_fix_prose():
    from tortoise import session_source_prose_migration as m

    found = m.find_session_sources_carrying_prose(_RecordingGraph([(_session_node(),)]))
    assert [n["url"] for n in found] == ["session:s-11070"]
    assert found[0]["summary_bytes"] == len(PROSE)
    assert found[0]["search_text_bytes"] == len(PROSE)


def test_a_document_source_is_not_in_the_census_even_with_is_episodic_true():
    """Leg 1: ``is_episodic`` is not the identity — the tracker's predicate is wrong.

    This node is a healthy document. Counting it pins the census above zero
    forever, which is exactly what makes a fixed graph look unfixed.
    """
    from tortoise import session_source_prose_migration as m

    assert m.find_session_sources_carrying_prose(_RecordingGraph([(_document_node(),)])) == []
    assert m.census(_RecordingGraph([(_document_node(),)])) == 0


def test_a_post_fix_lexicon_source_is_not_in_the_census():
    """Leg 2: non-empty is not prose — the prefix is the witness.

    A post-#10852 node carries a non-empty ``summary`` and ``_searchText`` by
    design. ``summary <> ''`` counts it; the prefix test does not.
    """
    from tortoise import session_source_prose_migration as m

    assert m.find_session_sources_carrying_prose(_RecordingGraph([(_post_fix_node(),)])) == []


def test_the_census_reaches_zero_on_a_graph_holding_only_healthy_sources():
    """The whole point: 0 is REACHABLE, so 0 is evidence."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([
        (_post_fix_node(),),
        (_document_node(),),
        (_document_node(url="corpus://c/transcript.md", sourceKind="agentSession"),),
        ({"url": "session:s-stub", "sourceKind": "agentSession", "summary": "",
          "_searchText": ""},),
    ])
    assert m.census(graph) == 0


# ── the repair ─────────────────────────────────────────────────────────────


def _reader(transcript):
    """A record reader that answers with ``transcript`` for any session."""
    def read(session_id, turn_max_index=None):
        return transcript
    return read


def test_the_repair_re_derives_the_lexicon_from_the_RECORD():
    """The constraint: derive from the record, never from the node's own prose.

    The repair must not read ``summary`` off the node and call that a repair —
    the narrative's authoritative copy is the record.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph,
        read_transcript=_reader("User: migrate the friday rebuild plan\n"
                                "Assistant: the plan is the migration"),
        dry_run=False,
    )

    assert report["repaired"] == ["session:s-11070"]
    assert report["no_record"] == []
    assert len(graph.writes) == 1
    params = graph.writes[0][1]
    assert params["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX)
    assert PROSE not in params["sum"], "the prose survived into the re-derived summary"
    assert PROSE not in params["st"]
    assert params["st"], "the fixture transcript must derive a lexicon"
    assert not params["st"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the indexed column must be the BARE lexicon — the display prefix in "
        "_searchText makes every session match a query for 'session'/'capture'")
    assert params["st"] == params["sum"][len(SESSION_SOURCE_SUMMARY_PREFIX):], (
        "the display column and the indexed column must be one datum")


def test_the_clear_arm_leaves_topics_alone():
    """`topics` is NOT the prose datum — the pre-fix writer derived it separately.

    The pre-#10000 capture took `topics` from `Counter.most_common(6)` (word
    frequencies) while `summary` was the first utterance, so the two were
    independent on this very population. Clearing it would destroy the only
    non-prose descriptor the node has, and would take a third field on a route
    whose declared bound is the pair.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([
        (_session_node(topics=["rebuild", "friday", "weekly"]),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False,
        clear_without_record=True)

    statement, params = graph.writes[0]
    assert "topics" not in statement, (
        "the clear-only arm wrote `topics` — it is not part of the pair")
    assert "topics" not in params


def test_neither_arm_writes_topics():
    """`topics` is NOT the pair, and `record-reconcile` compares it EXACTLY.

    The pre-fix writer took `topics` from `Counter.most_common(6)` while `summary`
    was the first utterance, so the record's own `topics` column is an independent
    value that the reconciler compares field-by-field (it is a projected property,
    NOT one of the declined pair). Writing a re-derived `topics` here would make
    `record-reconcile` permanently divergent on exactly the population this repair
    heals — the "signal indistinguishable from noise" class, one door over.
    """
    from tortoise import session_source_prose_migration as m

    for transcript in (None, "User: friday migration plan"):
        graph = _RecordingGraph([(_session_node(),)])
        m.reconcile_session_source_prose(
            graph, read_transcript=_reader(transcript), dry_run=False,
            clear_without_record=True)
        statement, params = graph.writes[0]
        assert "topics" not in statement, f"wrote `topics` (transcript={transcript!r})"
        assert "topics" not in params


def test_a_record_restored_node_is_not_a_candidate():
    """P0 (#11070 review): the recovery door's OWN node shape must not be prose.

    `node_from_row` DECLINES `summary` for an episodic Source while projecting the
    lexicon `_searchText` (#10965), so a `record-restore`d graph holds exactly
    `{_searchText: <lexicon>, topics: [...]}` with NO `summary`. Keying the
    blank-summary arm on emptiness alone made every restored session a candidate,
    so `--repair` cleared the whole tenant's Source index — the same unreachable-
    zero defect the tracker's own predicate had, one door over.
    """
    from tortoise import session_source_prose_migration as m

    topics = ["migration", "plan", "friday"]
    restored = {
        "url": "session:s-post", "sourceKind": "agentSession", "sessionId": "s-post",
        "_searchText": ", ".join(topics), "topics": topics,
    }
    assert m.carries_pre_lexicon_prose(restored) is False
    graph = _RecordingGraph([(restored,)])
    report = m.reconcile_session_source_prose(graph, read_transcript=_reader(None))
    assert report["candidates"] == []
    assert graph.writes == []


def test_a_restored_node_whose_topics_do_not_prove_the_lexicon_IS_a_candidate():
    """The proof is EXACT equality, and its polarity is fail-toward-candidate.

    `record-restore` always projects the pair together, so a blank `summary` with
    a `_searchText` its `topics` do not explain is the blanked-summary pre-fix
    hazard — prose with no prefix to witness it — and must be repaired.
    """
    from tortoise import session_source_prose_migration as m

    blanked = _session_node(
        summary="", _searchText=PROSE, topics=["rebuild", "friday", "weekly"])
    assert m.carries_pre_lexicon_prose(blanked) is True


def test_record_turn_reader_maps_the_records_three_answers(monkeypatch):
    """Fix pins the PRODUCTION reader, not just an injected lambda.

    The injected-lambda leg below passed while `record_turn_reader` collapsed
    `TurnAnswer.EMPTY` (an answer) into `None` (no answer) — so the `empty_sessions`
    bucket was dead in the only path that runs in production.
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    def fake(sid):
        return {
            "complete": record_read.TurnAnswer(
                answered=True, turns=(_turn("User: hi"),), turn_count=1,
                kind=record_read.TurnAnswer.COMPLETE),
            "empty": record_read.TurnAnswer(
                answered=True, turns=(), turn_count=0,
                kind=record_read.TurnAnswer.EMPTY),
            "none": record_read.TurnAnswer(
                answered=False, turns=(), turn_count=None,
                kind=record_read.TurnAnswer.NO_ACCOUNT),
            "partial": record_read.TurnAnswer(
                answered=False, turns=(_turn("User: hi"),), turn_count=9,
                kind=record_read.TurnAnswer.PARTIAL),
        }[sid]

    monkeypatch.setattr(record_read, "session_turns", lambda *a, **k: fake(k["session_id"]))
    read = m.record_turn_reader(object(), org_id="o", graph_id="g")

    assert read("complete") == "User: hi"
    assert read("empty") == "", "EMPTY is an ANSWER (record spoke, 0 turns)"
    assert read("none") is None
    assert read("partial") is None, (
        "a PARTIAL set must not build a lexicon from half a transcript")


class _Line:
    def __init__(self, line):
        self.line = line


def _turn(line):
    return _Line(line)


def test_a_record_that_spoke_and_states_zero_turns_is_not_reported_as_no_record():
    """`TurnAnswer.EMPTY` is an ANSWER; `NO_ACCOUNT` is not, and they differ.

    Collapsing them sent an operator hunting a DSN or row-level-security problem
    for a session whose record had spoken.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(""), dry_run=False)

    assert report["empty_sessions"] == ["session:s-11070"]
    assert report["no_record"] == []
    assert len(graph.writes) == 1  # the prose still goes (§9.1/D1)


def test_the_repair_clears_the_pair_when_the_record_does_not_answer():
    """No record → no invented lexicon. The CLEAR itself must be asked for.

    Clearing is not a new state — the writer itself produces ``summary=''`` and a
    bare ``_searchText`` for a text-less capture — but it is NOT self-correcting:
    the cleared node stops matching the census, so a later run cannot find it to
    re-derive the lexicon. It is therefore taken only when
    ``clear_without_record`` is set (#11152 review P2: the CLI gated this arm on
    "a DSN was supplied", which a wrong or empty record scope satisfies while
    every session reads as "no answer" — one bad DSN from un-indexing a
    tenant's whole Source population).
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False,
        clear_without_record=True)

    assert report["repaired"] == []
    assert report["no_record"] == ["session:s-11070"]
    assert len(graph.writes) == 1
    params = graph.writes[0][1]
    assert params["sum"] == ""
    # The writer's text-less shape: `summary=''` and NO `_searchText` (its SET is
    # `coalesce($st, s._searchText)` with `$st` None, and FalkorDB drops a null
    # property). `''` would be a state no writer reaches.
    assert params["st"] is None


def test_the_default_arm_leaves_an_unanswered_node_ALONE():
    """⛔ The default must not destroy what the record cannot replace.

    Same graph and same reader as the test above; only the flag differs. Without
    it the node is REPORTED and NOT written, so the operator sees the count the
    default refused instead of discovering it after the fact.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False)

    assert report["no_record"] == ["session:s-11070"]
    assert report["left_alone"] == ["session:s-11070"]
    assert graph.writes == [], "the default cleared a node it could not re-derive"


def test_a_run_with_no_reader_says_SO_and_not_that_the_record_was_silent():
    """⛔ "nobody asked" is not "the record did not answer" (#11152 review P2).

    The CLI's documented step 1 supplies no reader, so folding the two together
    told the operator the record had failed for N sessions they had never opened
    — sending them hunting a DSN or row-level-security problem that does not
    exist, and pointing at the destructive arm as the only remaining option.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(graph, dry_run=True)

    assert report["no_reader"] == ["session:s-11070"]
    assert report["no_record"] == [], (
        "a record that was never opened was reported as having not answered")
    assert report["left_alone"] == ["session:s-11070"]
    assert graph.writes == []


def test_the_repair_is_dry_by_default_and_writes_nothing():
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"))

    assert report["dry_run"] is True
    assert report["candidates"] == ["session:s-11070"]
    assert graph.writes == [], "dry_run wrote to the graph"


def test_dry_run_reports_the_same_candidate_set_as_a_real_run():
    """`dry_run=True` must return the SAME report shape (#4125's contract)."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    plan = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=True)
    done = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)

    assert plan["candidates"] == done["candidates"]
    assert set(plan) == set(done)


def test_the_candidate_read_is_never_conflated_with_a_healthy_node():
    """A repair that re-derived over a good node would rewrite it every run."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_post_fix_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: anything"))
    assert report["candidates"] == []
    assert graph.writes == []


def test_a_second_run_finds_nothing_left_to_repair():
    """Idempotent: the repair's own output must not be a candidate.

    A reconciler whose output re-enters its input is a rewrite loop, and the
    census would then never reach zero on the repaired graph — the failure this
    whole lane exists to prevent.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)
    params = graph.writes[0][1]

    healed = _session_node(summary=params["sum"], _searchText=params["st"])
    after = _RecordingGraph([(healed,)])
    report = m.reconcile_session_source_prose(
        after, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)

    assert report["candidates"] == [], (
        "the repair's own output is still a candidate — the census can never "
        "reach zero on a repaired graph")
    assert after.writes == []


def test_the_repaired_node_is_still_findable_through_the_source_fts_field():
    """#3518: a repair that only BLANKS the pair un-indexes every healed session."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday migration plan"),
        dry_run=False)
    st = graph.writes[0][1]["st"]
    assert st and "migration" in st, (
        "the repaired node carries no searchable text, so the Source FTS probe "
        "can never find the session again")


class _Proj:
    def __init__(self, graph):
        self.g = graph


def test_the_replay_hook_is_PLAN_ONLY_without_a_record():
    """Cycle-3 P1: the hook must never take the destructive arm unasked.

    A replay engine holds no record handle, and the clear arm is destructive AND
    not self-correcting (its output is no longer a candidate), so clearing here
    would un-index the pre-#10852 population irreversibly and then report
    `census == 0` — a false PASS of the exact kind this issue exists to close.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_after_replay(_Proj(graph), engine="rebuild")

    assert report is not None
    assert report["dry_run"] is True
    assert report["candidates"] == ["session:s-11070"]
    assert graph.writes == [], "the rebuild hook wrote without a record handle"


def test_the_replay_hook_writes_when_it_HAS_the_record():
    """The guard is the RECORD HANDLE, not a blanket refusal to act."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_after_replay(
        _Proj(graph), engine="rebuild",
        read_transcript=_reader("User: friday migration plan"))

    assert len(graph.writes) == 1
    assert graph.writes[0][1]["st"], "with the record, the lexicon is re-derived"


def test_the_replay_hook_is_a_no_op_without_a_graph_handle():
    from tortoise import session_source_prose_migration as m

    class _NoGraph:
        pass

    assert m.reconcile_after_replay(_NoGraph(), engine="rebuild") is None


def test_the_prose_never_reaches_the_re_derived_summary():
    """A one-word-prose session must still be repaired, not skipped.

    ``summary`` prose shorter than a lexicon is the shape an over-eager "is
    this really prose?" heuristic would skip.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(summary="ok", _searchText="ok"),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: ok go"), dry_run=False)
    assert report["candidates"] == ["session:s-11070"]
    healed = graph.writes[0][1]
    assert healed["sum"] != "ok" or healed["st"] != "ok"


# ── the read the census makes must SURVIVE a capped server ──────────────────
#
# #11152 review P1, reproduced on a real FalkorDB: the module requested a 60 000
# ms timeout, and a server whose TIMEOUT_MAX is 30 000 REFUSES the statement
# outright, so `census()` raised on every run against the canonical docker test
# lane — the module could not execute at all. The suite was green over it because
# the fake ignored `timeout`, which is the one parameter that decides whether the
# module runs. These three tests make the fake behave like the server.


def test_a_requested_timeout_above_the_servers_cap_is_CLAMPED_not_refused():
    """The census must run on a server that caps TIMEOUT (the lane runs 30 000)."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=30_000)
    assert m.CACHE_READ_TIMEOUT_MS > 30_000, (
        "this test is vacuous unless the module's own request exceeds the cap")

    found = m.find_session_sources_carrying_prose(graph)

    assert [n["url"] for n in found] == ["session:s-11070"]
    assert graph.requested_timeouts == [30_000], (
        "the request must be clamped to the server's own TIMEOUT_MAX")


def test_a_request_under_the_cap_is_honoured_as_written():
    """Clamped, never flattened: an uncapped/capable server gets what was asked."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=0)  # 0 = uncapped
    m.find_session_sources_carrying_prose(graph)
    assert graph.requested_timeouts == [m.CACHE_READ_TIMEOUT_MS]


def test_a_server_that_cannot_be_asked_still_reads_the_census():
    """A server that fails `config_get` must not turn the census into an error.

    Second line of defence: on the cap error the read is retried with NO explicit
    timeout, which the server's own policy bounds. Any OTHER exception is the
    query's and still propagates — asserted here so a bare `except: retry` cannot
    hide a real failure.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=30_000,
                            config_get_raises=True)
    found = m.find_session_sources_carrying_prose(graph)

    assert [n["url"] for n in found] == ["session:s-11070"]
    assert graph.requested_timeouts == [m.CACHE_READ_TIMEOUT_MS, None], (
        "the retry must carry NO timeout, not the refused one again")

    class _Broken(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            raise RuntimeError("some other server failure")

    with pytest.raises(RuntimeError, match="some other server failure"):
        m.find_session_sources_carrying_prose(_Broken([]))


def test_the_census_read_carries_the_sessions_live_turn_end():
    """⛔ The tail witness must ride the SAME scan (#11152 review P2).

    `record_read.session_turns`'s third PARTIAL shape — a transcript that stops
    BELOW the session's live id-space end — is invisible to the row set and to
    `turn_count`; the mark is its ONLY witness. A reader built without it returns
    that shape as COMPLETE, so the repair would derive a lexicon from half a
    transcript and call it a repair.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(turn_max_index=7),)])
    found = m.find_session_sources_carrying_prose(graph)
    assert found[0]["turn_max_index"] == 7

    seen = {}

    def read(session_id, turn_max_index=None):
        seen["tmi"] = turn_max_index
        return "User: friday migration plan"

    m.reconcile_session_source_prose(graph, read_transcript=read, dry_run=True)
    assert seen["tmi"] == 7, (
        "the reconciler called the reader without the session's turn_max_index")


def test_a_reader_that_takes_one_argument_is_tolerated_and_SAID_SO():
    """The degraded shape must not crash the run — and must not pass silently."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(turn_max_index=7),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid: "User: friday migration plan")

    assert report["repaired"] == ["session:s-11070"]
    assert "turn_max_index" in report["notes"]["session:s-11070"], (
        "a one-argument reader silently drops the tail witness")


def test_the_clamp_finds_the_cap_on_the_CLIENT_not_the_graph():
    """⛔ The verb is on the CLIENT — probing the graph alone was dead code.

    A real `falkordb.Graph` has no `config_get` and no `connection`, so an
    implementation that probes the graph handle never finds the cap and never
    clamps: the 60 000 ms request goes out, the server refuses it, and the read
    only survives on the retry. The fake above models that shape (verb on
    `.client`, and `.connection` raises), so this test fails if the probe regresses
    (#11152 review round 2, P1).
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=30_000)
    assert m._server_timeout_cap(graph) == 30_000
    assert m._effective_timeout_ms(graph, 60_000) == 30_000
    assert m._effective_timeout_ms(graph, 5_000) == 5_000


def test_the_clear_arm_says_CLEARED_and_the_default_says_LEFT_ALONE():
    """The note must state what HAPPENED, not what the default would have done.

    Emitting "left alone unless clear_without_record is set" on the very run that
    cleared the node is a report contradicting its own writes (#11152 review
    round 2, P2).
    """
    from tortoise import session_source_prose_migration as m

    # The dry run is asserted separately (a dry run writes NOTHING, so it must not
    # claim a clear); this test is about the WRITING arm's two outcomes.
    kept_graph = _RecordingGraph([(_session_node(),)])
    kept = m.reconcile_session_source_prose(
        kept_graph, read_transcript=_reader(None), dry_run=False)
    assert "left alone unless clear_without_record is set" in kept["notes"]["session:s-11070"]
    assert "CLEARED" not in kept["notes"]["session:s-11070"]
    assert kept_graph.writes == []

    cleared_graph = _RecordingGraph([(_session_node(),)])
    cleared = m.reconcile_session_source_prose(
        cleared_graph, read_transcript=_reader(None), dry_run=False,
        clear_without_record=True)
    assert "CLEARED" in cleared["notes"]["session:s-11070"]
    assert len(cleared_graph.writes) == 1


def test_a_node_with_no_session_id_is_COUNTED_when_a_reader_is_supplied():
    """⛔ The fourth state must have a counter on the production path.

    With a reader supplied, a pre-#4005 bare-filename node satisfies neither
    `no_reader` (a reader WAS supplied) nor `no_record` (the record was never
    asked), so it landed in NO bucket — and on the clear arm that made a
    destructive write reportable as nothing (#11152 review round 2, P2).
    """
    from tortoise import session_source_prose_migration as m

    legacy = {"url": "session.md", "sourceKind": "agentSession",
              "summary": PROSE, "_searchText": PROSE}
    graph = _RecordingGraph([(legacy,)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday plan"))

    assert report["candidates"] == ["session.md"]
    assert report["no_session_id"] == ["session.md"]
    assert report["left_alone"] == ["session.md"]
    assert graph.writes == []


def test_a_PARTIAL_answer_caused_by_the_CACHE_mark_is_named_as_such():
    """The tail witness is the CACHE's, so `no_record` may be the wrong diagnosis.

    `record_turn_reader` forwards the `:Session.turn_max_index` read off the
    cache; a mark ahead of the record's live end makes a fully-answered session
    read as PARTIAL. Refusing to write is the safe direction and stays — but the
    note must name the suspect instead of blaming the record (#11152 review
    round 2, P2).
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # ⛔ A REASON THAT DOES **NOT** CONTAIN THE FALLBACK WORDING, so only the
    # STRUCTURAL condition can produce the warning. An earlier version of this
    # test used the real tail wording, which also satisfied the string fallback —
    # deleting the structural computation left the suite green
    # (#11152 review round 4).
    tails = (record_read.Turn(turn_id="t1", index=0, content="[user] hi"),
             record_read.Turn(turn_id="t2", index=1, content="[user] plan"))

    def fake_session_turns(conn, *, org_id, graph_id, session_id,
                           turn_max_index=None):
        return record_read.TurnAnswer(
            answered=False, turns=tails, turn_count=2,
            reason="the record answered for part of this session",
            kind=record_read.TurnAnswer.PARTIAL)

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(self_inner):
            self_inner._real = record_read.session_turns
            record_read.session_turns = fake_session_turns

        def __exit__(self_inner, *exc):
            record_read.session_turns = self_inner._real

    graph = _RecordingGraph([(_session_node(turn_max_index=99),)])
    with _Patch():
        assert reader("s-11070", 99) is None
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "record did not ANSWER" in note
    assert "MAY BE THE CACHE'S FAULT" in note, (
        "a bare `no_record` tells the operator to hunt a record problem that may "
        "not exist")
# ══════════════════════════════════════════════════════════════════════════
# The operator CLI's SCOPE contract (#11152 review: presence is not proof, and
# the requirement belongs on the arm that can destroy something)
# ══════════════════════════════════════════════════════════════════════════
#
# Two independent defects, both only observable at argv level:
#
# 1. The scope guard was keyed on `record_dsn` ALONE, so on the box this repair
#    targets — `TORTOISE_RECORD_DSN` exported, no scope flags typed, which the
#    script's own comment calls "most of them" — the runbook's step 1
#    (`--dry-run`) and step 3 (`--dry-run --assert-clean`) exited 2 before
#    opening the graph. A verification step that cannot run is a verification
#    that cannot fail, and the only escape the error offered was
#    `--clear-without-record` — the flag that authorises clearing.
# 2. Non-empty `--org-id`/`--graph-id` prove PRESENCE, never that the scope
#    ANSWERED. Row-level security returns zero rows (never an error) for a scope
#    that does not match, so a typo'd `--graph-id` reads exactly like "the record
#    did not answer" for every session. One session cannot tell the two apart;
#    the population can, and the run must say which failure it saw.

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_PATH = _REPO_ROOT / "graph-scripts" / "11070_repair_session_source_prose.py"
_spec = _ilu.spec_from_file_location("repair_session_source_prose_11070",
                                     str(_CLI_PATH))
_cli = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cli)

_DSN = "postgresql://u@h/db"


class _Reached(Exception):
    """Raised where the run got PAST the argument guards."""


class _FakeRecordConn:
    def close(self):
        pass


class _NotAnswered:
    answered = False
    turns: tuple = ()


class _OneTurn:
    """A session whose record DOES answer — the proof the scope works."""

    answered = True
    turns = (type("_T", (), {"line": "User: friday rebuild plan"})(),)


def _no_account(conn, *, org_id, graph_id, session_id, turn_max_index=None):
    """The record as a WRONG SCOPE presents it: silence for every session."""
    return _NotAnswered()


class _Proj:
    """The projection the CLI opens — just the graph handle it needs."""

    def __init__(self, graph):
        self.g = graph

    def close(self):
        pass


def _export_a_record_dsn(monkeypatch):
    """The box this repair targets: a DSN exported, no scope flag typed."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@127.0.0.1:1/none")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", _DSN)
    monkeypatch.delenv("TORTOISE_ORG_ID", raising=False)


def _install_cli(monkeypatch, graph, argv):
    monkeypatch.setattr(_cli, "open_projection", lambda uri, name: _Proj(graph))
    monkeypatch.setattr("tortoise.record_serve.open_record",
                        lambda dsn: _FakeRecordConn())
    monkeypatch.setattr("tortoise.record_read.session_turns", _no_account)
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, *argv])


def test_a_census_runs_on_a_box_that_exports_a_record_dsn(monkeypatch, capsys):
    """Finding (1): steps 1 and 3 must be runnable where they are needed.

    (1) FAILS on the pre-fix code: the guard was keyed on `record_dsn`, so this
        argv exited 2 before `open_projection` was reached — proving the census
        refused to run and hiding the reason behind the clearing flag.
    (2) REACHABLE: the same argv now reaches the cache read, and the run SAYS
        the record was not consulted instead of silently reporting silence.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])

    def _boom(uri, name):
        raise _Reached

    monkeypatch.setattr(_cli, "open_projection", _boom)
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--dry-run"])

    with pytest.raises(_Reached):
        _cli.main()
    # …and with a real projection the census completes, exit 0, no scope asked.
    monkeypatch.setattr(_cli, "open_projection", lambda uri, name: _Proj(graph))
    assert _cli.main() == 0
    captured = capsys.readouterr()
    assert "census BEFORE: 1" in captured.out
    assert "NOT consulted" in captured.err


def test_the_writing_arm_still_refuses_a_missing_record_scope(monkeypatch,
                                                              capsys):
    """The guard is not relaxed where it protects a destructive write."""
    _export_a_record_dsn(monkeypatch)
    monkeypatch.setattr(_cli, "open_projection", lambda uri, name: _Reached)
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--repair"])

    assert _cli.main() == 2
    err = capsys.readouterr().err
    assert "WRITING arm" in err
    assert "NOTE:" not in err, (
        "the census path's note fired on a writing arm — the two arms are "
        "being conflated")


def test_the_writing_arm_names_an_UNPROVEN_scope(monkeypatch, capsys):
    """Finding (2): a typed-but-wrong scope exits 3 and destroys nothing.

    (1) FAILS on the pre-fix code: the run reported `census_after != 0` and
        exited 1 with "re-run --dry-run and read the report" — the same exit a
        half-finished repair produces, so a wrong scope sent the operator to
        re-read a census instead of checking the scope it typed.
    (2) REACHABLE: a record that answers for NOBODY while the operator supplied a
        scope — exactly what row-level security does for a scope that does not
        match.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([
        (_session_node(sessionId="s-a", url="session:s-a"),),
        (_session_node(sessionId="s-b", url="session:s-b"),),
    ])
    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "WRONG"])

    assert _cli.main() == 3
    assert graph.writes == []
    err = capsys.readouterr().err
    assert "UNPROVEN" in err
    assert "--clear-without-record" in err, (
        "the refusal names no escape hatch for the deliberate clear-only arm")


def test_a_json_run_cannot_report_success_over_the_unproven_scope(monkeypatch,
                                                                  capsys):
    """`--json` must not defeat the refusal (the earlier fix-7 defect class)."""
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "WRONG", "--json"])

    assert _cli.main() == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope_unproven"] is True
    assert graph.writes == []


def test_ONE_answered_session_clears_the_scope(monkeypatch, capsys):
    """The proof is population-level, so a real repair is never refused.

    (1) FAILS if the refusal keys on any `no_record` at all — the ordinary case,
        where older sessions have aged out of the record: the operator would be
        told their scope is wrong on a scope that works.
    (2) REACHABLE: one session answers, one does not.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([
        (_session_node(sessionId="s-a", url="session:s-a"),),
        (_session_node(sessionId="s-b", url="session:s-b"),),
    ])

    def answer_one(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return _OneTurn() if session_id == "s-a" else _NotAnswered()

    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "gx"])
    monkeypatch.setattr("tortoise.record_read.session_turns", answer_one)

    code = _cli.main()
    assert code != 3
    assert len(graph.writes) == 1, "the answered session must be re-derived"
    assert "UNPROVEN" not in capsys.readouterr().err


def test_the_clear_without_record_arm_is_never_second_guessed(monkeypatch,
                                                              capsys):
    """The operator asked for the destructive arm BY NAME — run it.

    (1) FAILS if the scope diagnosis also fires on this arm: `no_record` is empty
        here (the record is never opened), so a naive key on `left_alone` or on
        "no candidate was repaired" would refuse the one arm the flag exists for.
    (2) REACHABLE: `--repair --clear-without-record` with a DSN exported.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record"])

    code = _cli.main()
    assert code != 3
    assert len(graph.writes) == 1, "the by-name clear arm did not write"
    err = capsys.readouterr().err
    assert "UNPROVEN" not in err
    assert "NOT written" not in err


def test_an_EXPLICIT_record_dsn_survives_the_by_name_clear_flag(monkeypatch,
                                                                capsys):
    """⛔ The safe combination must be reachable FROM THE CLI (#11152 r3, P2).

    `--clear-without-record` exists so the clear arm is reachable on a box that
    EXPORTS a `TORTOISE_RECORD_DSN` the operator did not mean to use. It must
    never discard a `--record-dsn` the operator TYPED — doing so made
    "re-derive where the record answers, clear where it does not" inexpressible,
    and cleared nodes the record could have healed.

    ⛔ This test exists because the fix had NO test: reverting the guard to
    `if args.clear_without_record: record_dsn = ""` left every test green while
    turning the same argv from "re-derive" into "destroy" (review round 3).
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])

    def answers(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return _OneTurn()

    _install_cli(monkeypatch, graph, [
        "--repair", "--record-dsn", "postgresql://u@h/db",
        "--org-id", "org_x", "--graph-id", "gx",
        "--clear-without-record"])
    monkeypatch.setattr("tortoise.record_read.session_turns", answers)

    code = _cli.main()
    err = capsys.readouterr().err
    # ⚠️ This harness's graph does not replay its own writes, so `census_after`
    # stays above 0 and the run legitimately exits 1 ("did not reach 0"). The
    # subject here is WHICH arm ran, and exit 3 would mean the scope refusal fired.
    assert code != 3, err
    assert "UNPROVEN" not in err
    assert len(graph.writes) == 1
    written = graph.writes[0][1]
    assert written["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the explicitly supplied record was ignored and the pair was CLEARED "
        "instead of re-derived")
    assert written["st"], "the indexed column was cleared instead of re-derived"


def test_an_unproven_scope_is_not_reported_over_the_by_name_clear_arm(
        monkeypatch, capsys):
    """The refusal must never claim "nothing was destroyed" after destroying.

    `--record-dsn` + `--clear-without-record` with a record that answers for
    nobody is a DELIBERATE clear: the operator already passed the flag the old
    message advised, and `no_record` there means CLEARED. Firing the refusal
    printed "nothing was destroyed (0 candidates left alone) … pass
    --clear-without-record" over a run that had cleared the population
    (#11152 review round 3, P2).
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, [
        "--repair", "--record-dsn", "postgresql://u@h/db",
        "--org-id", "org_x", "--graph-id", "gx",
        "--clear-without-record"])

    code = _cli.main()
    err = capsys.readouterr().err
    assert "UNPROVEN" not in err
    assert "nothing was destroyed" not in err, (
        "the refusal claimed nothing was destroyed over a run that cleared")
    assert code != 3, err
    assert len(graph.writes) == 1
    assert graph.writes[0][1]["sum"] == "", "the by-name arm did not clear"


def test_a_SHORT_COUNT_PARTIAL_is_not_blamed_on_the_cache_mark():
    """⛔ The tail arm runs LAST; the record's own arms must win the diagnosis.

    `record_read._shortfall` refuses a short-count (`len(turns) < turn_count`) or
    a gap BEFORE it ever compares against `turn_max_index`, so a bare
    `max(held) < turn_max_index` test is a SUPERSET: it blamed the cache's mark
    for refusals the record itself declared, sending the operator to compare a
    datum that is irrelevant (#11152 review round 4).
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # The record declares 2 turns and holds 1 — the SHORT-COUNT arm, which
    # `_shortfall` returns before the tail is examined.
    held = (record_read.Turn(turn_id="t1", index=0, content="[user] hi"),)
    answer = record_read.TurnAnswer(
        answered=False, turns=held, turn_count=2,
        reason=record_read._shortfall(held, 2, 99),
        kind=record_read.TurnAnswer.PARTIAL)
    assert "turn(s) but holds only" in answer.reason, (
        "this test is vacuous unless the reason is the SHORT-COUNT arm")

    def fake(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return answer

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(s):
            s._real = record_read.session_turns
            record_read.session_turns = fake

        def __exit__(s, *exc):
            record_read.session_turns = s._real

    graph = _RecordingGraph([(_session_node(turn_max_index=99),)])
    with _Patch():
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "record did not ANSWER" in note
    assert "MAY BE THE CACHE'S FAULT" not in note, (
        "the record declared this shortfall itself — the cache's mark is "
        "irrelevant and naming it sends the operator to the wrong datum")


def test_a_dry_run_never_says_the_pair_WAS_cleared():
    """A report must not assert a write the run did not make."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=True,
        clear_without_record=True)

    note = report["notes"]["session:s-11070"]
    assert "writes NOTHING" in note
    assert "the pair is CLEARED" not in note, (
        "a dry run claimed the pair was cleared while writing nothing")
    assert graph.writes == []


def test_a_one_argument_reader_keeps_its_warning_when_the_read_also_fails():
    """Two defects on one node must both be reported — notes ACCUMULATE."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(turn_max_index=7),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid: None)

    note = report["notes"]["session:s-11070"]
    assert "takes one argument" in note, (
        "the degraded-read warning was overwritten exactly when the read also "
        "failed, i.e. when the diagnosis is least reliable")
    assert "did not ANSWER" in note


def test_a_candidate_with_no_usable_url_is_REPORTED_not_silently_dropped():
    """Otherwise it pins the census above zero forever with no reason given.

    `is_episodic_source_node`'s bare-filename arm accepts an empty url, so such a
    node counts as a candidate; it cannot be written, so `census_after` can never
    reach 0 — an unreachable zero with no reported cause, the exact class #11070
    exists to close (#11152 review round 4).
    """
    from tortoise import session_source_prose_migration as m

    empty = {"url": "", "sourceKind": "agentSession", "is_episodic": True,
             "summary": PROSE, "_searchText": PROSE}
    graph = _RecordingGraph([(empty,)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday plan"), dry_run=True)

    assert report["candidates"] == [""]
    assert report["unaddressable"] == [""]
    assert any("no usable url" in n for n in report["notes"].values()), (
        "the run exits 1 forever with no explanation of the pinning candidate")
    assert graph.writes == []


def test_a_LEADING_GAP_partial_is_not_blamed_on_the_cache_mark():
    """⛔ `_shortfall`'s gap arm measures from ZERO, not from `min(held)`.

    Its `span = max(held) + 1` / `missing_count = span - len(held)` treats a run
    whose EARLIEST rows are missing as a gap, while a `range(min(held), …)`
    comparison calls it contiguous — so that case quoted the record's own "no turn
    row for index 0" and then blamed the cache's mark in the next sentence
    (#11152 review round 5, P2).
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # Indices [1, 2]: contiguous to a min-based check, a GAP to `_shortfall`.
    held = (record_read.Turn(turn_id="t1", index=1, content="[user] a"),
            record_read.Turn(turn_id="t2", index=2, content="[user] b"))
    answer = record_read.TurnAnswer(
        answered=False, turns=held, turn_count=None,
        reason=record_read._shortfall(held, None, 5),
        kind=record_read.TurnAnswer.PARTIAL)
    assert "no turn row for index 0" in answer.reason, (
        "this test is vacuous unless the reason is the LEADING-GAP arm")

    def fake(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return answer

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(s):
            s._real = record_read.session_turns
            record_read.session_turns = fake

        def __exit__(s, *exc):
            record_read.session_turns = s._real

    graph = _RecordingGraph([(_session_node(turn_max_index=5),)])
    with _Patch():
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "MAY BE THE CACHE'S FAULT" not in note, (
        "the record reported a LEADING gap itself; the cache's mark is irrelevant")


def test_the_repair_count_comes_from_the_WRITES_not_from_bucket_arithmetic():
    """`left_alone` overlaps the other buckets, so subtracting mis-counts.

    `left_alone` also holds `no_session_id`/`no_reader` nodes, which are not in
    `no_record` — the old count reported 1 repaired on a run that wrote 2.
    """
    from tortoise import session_source_prose_migration as m

    legacy = {"url": "legacy.md", "sourceKind": "agentSession",
              "summary": PROSE, "_searchText": PROSE}
    graph = _RecordingGraph([
        (_session_node(sessionId="s1", url="session:s1"),),
        (_session_node(sessionId="s2", url="session:s2"),),
        (legacy,),
    ])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"),
        dry_run=False)

    assert len(report["repaired"]) == 2
    assert report["left_alone"] == ["legacy.md"]
    assert report["written"] == ["session:s1", "session:s2"], (
        "the report's own write log is the only honest repair count")
    assert len(graph.writes) == len(report["written"])


def test_a_dry_run_writes_nothing_and_reports_no_writes():
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday plan"))
    assert report["written"] == []
    assert graph.writes == []


def test_the_embedded_arm_honours_graph_and_the_report_names_what_it_opened(
        monkeypatch, capsys):
    """⛔ A clean verdict must never describe a graph the run did not open.

    `open_projection` dropped `graph_name` on the EMBEDDED branch, so
    `--db <path> --graph X` opened the default `tortoise` graph while printing
    `graph: X` — and `--dry-run --assert-clean` then passed over a graph the run
    never touched. And `--json` printed no graph field at all, so a
    machine-readable record of a DESTRUCTIVE run could not say which cache graph
    it wrote (#11152 review round 6).
    """
    seen = {}

    class _Proj:
        def __init__(self, graph_name):
            self.graph_name = graph_name
            self.g = _RecordingGraph([(_session_node(),)])

        def close(self):
            pass

    class _Capturing:
        def __init__(self, *a, **kw):
            seen["graph_name"] = kw.get("graph_name")
            # ⛔ NO `or "tortoise"` HERE. That normalisation is what let the
            # empty-name regression through round 6: the fake reinstated the
            # default the real constructor no longer receives (#11152 r7).
            self._inner = _Proj(kw.get("graph_name"))

        def __getattr__(self, item):
            return getattr(self._inner, item)

    monkeypatch.setattr(_cli, "FalkorProjection", _Capturing)
    monkeypatch.setattr(sys, "argv", [
        _CLI_PATH.name, "--db", "/tmp/embedded-11070", "--graph",
        "org_acme_tortoise", "--dry-run", "--json"])

    _cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert seen["graph_name"] == "org_acme_tortoise", (
        "the embedded branch dropped --graph and opened the default graph")
    assert payload["graph"] == "org_acme_tortoise", (
        "--json could not name the cache graph the run addressed")


def test_the_embedded_DEFAULT_graph_is_tortoise_not_the_empty_name(
        monkeypatch, capsys):
    """⛔ `--db <path>` with NO `--graph` must open `tortoise`, not `""`.

    The empty string is a REAL, distinct, empty FalkorDB graph, so a default of
    `""` made `--dry-run --assert-clean` exit 0 over an unrepaired graph the run
    never opened — a no-op verification gate, on the arm round 6 touched
    (#11152 review round 7, MATERIAL). `sdk._derive_graph_name` returns `tortoise`
    for an embedded checkout, and so must this.
    """

    seen = {}

    class _Inner:
        def __init__(self):
            self.graph_name = "tortoise"
            self.g = _RecordingGraph([(_session_node(),)])

        def close(self):
            pass

    class _Capturing:
        def __init__(self, *a, **kw):
            seen["graph_name"] = kw.get("graph_name")
            self._inner = _Inner()

        def __getattr__(self, item):
            return getattr(self._inner, item)

    monkeypatch.setattr(_cli, "FalkorProjection", _Capturing)
    monkeypatch.setattr(sys, "argv", [
        _CLI_PATH.name, "--db", "/tmp/embedded-11070", "--dry-run", "--json"])

    _cli.main()
    assert seen["graph_name"] == "tortoise", (
        "the embedded default reached the projection as an empty graph name")
    assert json.loads(capsys.readouterr().out)["graph"] == "tortoise"


def test_the_runbook_commands_that_touch_the_cache_all_name_the_graph():
    """One operator instruction must not contradict the next.

    Step 1 (`--dry-run`) omitted `--graph` while the docstring ten lines below
    warns that omitting it counts a different graph; the same defect appeared in
    the replay hook's FAILED-path message. Both are the command an operator
    copies, so both are pinned here against the source that emits them.
    """
    from pathlib import Path

    import tortoise.session_source_prose_migration as mod

    cli = Path(_CLI_PATH).read_text()
    module = Path(mod.__file__).read_text()

    for label, text in (("the CLI runbook", cli), ("the module", module)):
        for line in text.splitlines():
            if "11070_repair_session_source_prose.py" not in line:
                continue
            if "--dry-run" in line or "--repair" in line or "re-run" in line:
                following = text[text.index(line):text.index(line) + 400]
                assert "--graph" in following or line.rstrip().endswith(
                    "11070_repair_session_source_prose.py"), (
                    f"{label} hands the operator a cache-touching command that "
                    f"does not name the graph: {line.strip()}")


def test_the_flag_still_USES_the_record_when_a_scope_is_typed(monkeypatch,
                                                              capsys):
    """⛔ `--clear-without-record` must not throw away a record you asked for.

    The flag exists so the clear arm is REACHABLE on a box that exports
    `TORTOISE_RECORD_DSN`. Passing it WITH a typed scope is an explicit statement
    that the record is to be used, so the run must keep the inherited DSN and
    re-derive where the record answers — not open no record at all and clear every
    candidate, including ones the record could have healed (#11152 round 8, P1).

    Before the fix this argv wrote `{'sum': '', 'st': None}` over an answerable
    session.
    """
    _export_a_record_dsn(monkeypatch)          # inherited DSN only
    graph = _RecordingGraph([(_session_node(),)])

    def answers(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return _OneTurn()

    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "gx",
        "--clear-without-record"])
    monkeypatch.setattr("tortoise.record_read.session_turns", answers)

    _cli.main()
    assert len(graph.writes) == 1
    written = graph.writes[0][1]
    assert written["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the inherited record was discarded although a scope was typed — the "
        "answerable session was CLEARED instead of re-derived")
    assert written["st"]


def test_the_flag_still_drops_the_inherited_dsn_when_NO_scope_is_typed(
        monkeypatch):
    """The flag's original purpose must survive: reachable where a DSN is exported."""
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record"])

    _cli.main()
    assert len(graph.writes) == 1
    assert graph.writes[0][1]["sum"] == "", (
        "the by-name clear arm did not clear where no scope was typed")


def test_an_EMPTY_graph_name_is_refused_at_the_boundary(monkeypatch, capsys):
    """`--graph ""` is not "the default": it is a real graph, or a driver crash.

    On the served arm it reached `select_graph("")` and raised
    `TypeError: Expected a string parameter` from inside the driver, and
    `report["graph"]` would print "(URI default)" over a graph it never opened.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@127.0.0.1:1/none")
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--graph", "", "--dry-run"])

    assert _cli.main() == 2
    assert "--graph may not be empty" in capsys.readouterr().err


# ── the four replay CALL SITES ────────────────────────────
#
# #11152 round 8: every hook test above calls `reconcile_after_replay` DIRECTLY,
# so deleting one of the four wiring blocks — or copying the wrong `engine=`
# literal — reded nothing. The sibling migration (#4125) shipped exactly that
# defect: its `rebuild()` site passed `engine="rebuild_all"` and misdirected a
# failure investigation. These two tests close the gap the cheap way: one drives a
# REAL `rebuild()` and asserts the hook is invoked with the right label (the defect
# 4125 shipped), and one asserts all FOUR sites exist with their own literals (a
# deleted call site would otherwise be invisible).

def test_the_REAL_rebuild_engine_invokes_the_prose_hook_with_its_own_label(
        monkeypatch, tmp_path):
    """(1) FAILS if `projection.rebuild` drops the hook or mislabels the engine.

    (2) REACHABLE: a real `rebuild()` over a real journal. The population is empty
    here on purpose — the hook runs even when it reports nothing, and the LABEL is
    what this test is about.
    """
    from tortoise import session_source_prose_migration as mod
    from tortoise.log import EventLog
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "g.db"), namespace="test_11070_hook",
                    event_log_path=str(events / "events.jsonl"))
    calls: list[str] = []
    real = mod.reconcile_after_replay

    def spy(projection, *, engine, **kw):
        calls.append(engine)
        return real(projection, engine=engine, **kw)

    monkeypatch.setattr(mod, "reconcile_after_replay", spy)
    try:
        s._get_proj().rebuild(EventLog(str(events / "events.jsonl")),
                             confirm_destructive=True)
    finally:
        s.close()
    assert calls == ["rebuild"], (
        f"rebuild() invoked the hook as {calls!r}; #4125's defect was exactly a "
        f"wrong engine label here")


def test_EACH_replay_file_calls_THIS_hook_exactly_once_with_its_own_label():
    """⛔ AST, not a substring scan — the substring version could not fail.

    The first version of this test scanned each file for `reconcile_after_replay`
    plus an engine literal. Both ALREADY appear in those files for the #4125
    sibling hook, so deleting a #11070 call site (or mislabelling it) left the
    suite green — measured on three separate mutations (#11152 review round 9,
    MATERIAL: a gate that cannot fail).

    This walks the AST instead and keys on the #11070 IMPORT ALIAS
    (`sspm.reconcile_after_replay`), which #4125's bare `reconcile_after_replay`
    calls cannot satisfy. It therefore reds on a deleted site (count 0) and on a
    wrong label (the kwarg differs).

    Declared limit: it proves the call EXISTS with the right label, not that the
    engine reaches it at runtime. `rebuild` is driven for real by
    `test_the_REAL_rebuild_engine_invokes_the_prose_hook_with_its_own_label`; the
    other three are pinned here because each needs a distinct hostile setup
    (`rebuild_all`'s string-dir form, `recover_from_log`'s 0-node discriminator,
    `backup.restore`'s backup dir) and #4125 drives those in its own file.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    expected = {
        "tortoise/projection/__init__.py": ["rebuild", "rebuild_all"],
        "tortoise/consistency.py": ["recover_from_log"],
        "tortoise/backup.py": ["backup.restore"],
    }
    for rel, literals in expected.items():
        tree = ast.parse((root / rel).read_text())
        found: list[str | None] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == "reconcile_after_replay"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "sspm"):
                continue
            label = None
            for kw in node.keywords:
                if kw.arg == "engine" and isinstance(kw.value, ast.Constant):
                    label = kw.value.value
            found.append(label)
        assert sorted(found) == sorted(literals), (
            f"{rel}: the #11070 hook is called as {found!r}, expected {literals!r} "
            f"— a deleted call site or a wrong engine label")
