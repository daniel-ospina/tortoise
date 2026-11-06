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

    kept = m.reconcile_session_source_prose(
        _RecordingGraph([(_session_node(),)]), read_transcript=_reader(None))
    assert "left alone unless clear_without_record is set" in kept["notes"]["session:s-11070"]
    assert "CLEARED" not in kept["notes"]["session:s-11070"]

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
    from tortoise import session_source_prose_migration as m

    def read(session_id, turn_max_index=None):
        # The production reader (`record_turn_reader`) records WHY it refused; a
        # bare None would force the caller to guess, and the guesses differ.
        read.reasons[session_id] = (
            "PARTIAL", "the record stops below the session's turn_max_index=99")
        return None

    read.reasons = {}
    graph = _RecordingGraph([(_session_node(turn_max_index=99),)])
    report = m.reconcile_session_source_prose(graph, read_transcript=read)

    note = report["notes"]["session:s-11070"]
    assert "record did not ANSWER" in note
    assert "MAY BE THE CACHE'S FAULT" in note, (
        "a bare `no_record` tells the operator to hunt a record problem that may "
        "not exist")
    assert "turn_max_index=99" in note, "the record's own reason must survive"
