"""#13557 + #13558 — the state answers, reachable from a QUESTION.

Both defects come from the same capstone run (#13038, 2026-11-18), and both are
the same failure: **the right answer existed and no question reached it.**

* **#13557 — a commitment is a KIND, not a phrase.** ``search('current
  commitments', object)`` returned **0 rows** while ``search('launch event',
  object)`` returned the live 12 December plan. The plan carries
  ``pipelineStageId=scheduled`` and ``isClosed=false``; it is invisible to the
  commitment question only because the word "commitment" does not occur in its
  text.
* **#13558 — the ended read exists but nothing reaches it.**
  ``search(None, point, ended_scope=True)`` returned the ended records;
  ``search('has anything we relied on been withdrawn', point)`` returned two rows
  that share no word with the question, at score 0.0. A capability that must be
  invoked with no text at all is not a read surface — it is a second tool the
  caller has to know about.

WHAT IS PINNED HERE, and each one is a returned SET, never "a detector matched":

* **Q1** — a commitment question returns every live commitment (a ``plan``, a
  ``goal``, a ``target``) without the caller naming any of their nouns, and a
  retired one (``deprecated``) and a plan ended on a ``terminal`` stage are not
  in the population;
* **Q6** — a withdrawal question returns the ended records **with how each
  ended** (``status``, the successor, the terminal ``stage`` + ``outcome``), and
  a live unrelated record is NOT served as the answer; if nothing has ended the
  answer is plainly empty rather than unrelated rows;
* **both directions of the disjointness** — no live commitment in the ended
  read, no ended record in the commitments read (they are one determinant,
  ``live._terminal_expression`` and its keep dual, so this is the property that
  makes the two reads one model rather than two lists);
* **the default surface is unchanged for a content query** — a query that names
  a record keeps the relevance read it had (the routing is a question shape, not
  a keyword hijack), and the commitment population is not reachable from a
  ``point``-scoped question, because the commitment kinds are Object kinds;
* **the degradation is carried, never served as an empty answer** — a scan that
  could not answer marks the leg and the truncation when a sink exists, and
  raises when none does (``[]`` would otherwise claim "nothing is committed").

The selector is the model's own: ``tortoise/commitments.py`` reads the
commitment kinds of ONTOLOGY §5 through the shared currency predicate, and
``tortoise/ended.py`` reads the same predicate in the positive direction. The
edge from the customer's sentence to either population is
``tortoise/state_intent.py`` — pure text, no model, no graph.

Lane: docker (a live FalkorDB) — both reads are Cypher over a real store, so an
embedded fake would test nothing. Skips when no store is reachable (the shipped
convention, ``tests/_live_utils.py``).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import _live_utils  # noqa: E402, RUF100


def _probe_graph_name() -> str:
    """A PER-RUN name for the availability probe's own graph.

    ``from_uri`` defaults to ``ensure_indexes=True``, whose schema DDL CREATES the
    graph it names — so an unnamed probe graph is a graph nobody owns. Minted per
    run, inside the session sweep's prefix family, and in the ``_<8+ hex>`` shape
    ``drop_test_graph`` will accept. Same policy as ``test_13302_ended_read.py``.
    """
    return f"tortoise_test_13557_probe_{uuid.uuid4().hex[:10]}"


def _probe(candidates):
    env_uri = os.environ.get("TORTOISE_DB_URI")
    probe_graph = _probe_graph_name()
    for uri in candidates:
        if not uri:
            continue
        proj = None
        try:
            from tortoise.projection import FalkorProjection

            proj = FalkorProjection.from_uri(uri, graph_name=probe_graph)
            proj.g.query("RETURN 1")
            return True, uri
        except Exception:
            if uri == env_uri:
                break
            continue
        finally:
            if proj is not None:
                try:  # noqa: SIM105
                    proj.close()
                except Exception:
                    pass
    return False, None


_AVAILABLE, _WORKING_URI = _probe([
    os.environ.get("TORTOISE_DB_URI"),
    _live_utils.docker_uri("tortoise_test_13557"),
    _live_utils.legacy_uri("tortoise_test_13557"),
])

_docker_only = pytest.mark.skipif(
    not _AVAILABLE,
    reason="live FalkorDB not available (both reads are Cypher over a real store)")


def _uri() -> str:
    return (os.environ.get("TORTOISE_DB_URI")
            or (_WORKING_URI
                or _live_utils.docker_uri("tortoise_test_13557", password=None)))


@pytest.fixture()
def sdk(monkeypatch):
    """A fresh graph namespace and an SDK pointed at the live store."""
    monkeypatch.setenv("TORTOISE_DB_URI", _uri())
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK(namespace=f"test13557_{uuid.uuid4().hex[:10]}")


#: A pack-style pipeline a plan kind declares. The stage NAMES are deliberately
#: not read by either population (only the slot is, design §4.1), so the terminal
#: ones are named with words the model never treats as an ending.
PLAN_PIPELINE = {
    "defaultStage": "scheduled",
    "stages": [
        {"id": "scheduled", "label": "Scheduled", "order": 0, "slot": "entry"},
        {"id": "cancelled", "label": "Cancelled", "order": 1, "slot": "terminal",
         "outcome": "withdrawn"},
        {"id": "held", "label": "Held", "order": 2, "slot": "terminal",
         "outcome": "completed"},
    ],
}


def _ids(rows) -> set[str]:
    return {r["id"] for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
# The edge itself — hermetic, no graph, no model (the routing table)
# ══════════════════════════════════════════════════════════════════════════════


#: The questions the two issues are about, and the population each must select.
#: The cue is a QUESTION SHAPE, never a noun-to-state assignment.
SELECTED = [
    # #13557 — Q1, in the words a customer uses for it.
    ("current commitments", "commitments", "scoped_noun"),
    ("what are we currently committed to — pricing, onboarding, launch and uptime",
     "commitments", "committed_to"),
    ("our commitments", "commitments", "determined_noun"),
    # #13558 — Q6, and the other shapes a customer uses for it.
    ("has anything we relied on been withdrawn", "ended", "anything"),
    ("has anything been retired", "ended", "anything"),
    ("has anything been archived", "ended", "anything"),
    ("what has been withdrawn", "ended", "what"),
    ("what ended", "ended", "what"),
    ("which commitments were withdrawn", "ended", "which"),
    ("list what we retired", "ended", "what"),
    ("what did we archive last quarter", "ended", "did_we"),
]


@pytest.mark.parametrize(("query", "population", "shape"), SELECTED)
def test_a_state_question_selects_its_population(query, population, shape):
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population == population, (
        f"{query!r} selects {got.population!r}, not {population!r} — the question "
        f"that could not reach its answer is the whole of #13557/#13558")
    assert got.shape == shape
    assert got.cue, "the routed decision must say WHICH words selected it"


#: The NEGATIVE control, and the reason the detector is a question shape rather
#: than a keyword list. Each of these is a query the read had before and must
#: keep: the capstone's own content queries (they name records), a note lookup
#: that happens to contain an ending word, and the plan-versus-occurrence
#: question that the ended population CANNOT answer (a `held` plan ended AND
#: happened, so serving the ended set as "never happened" would be a wrong answer
#: that looks right — see the module docstring's boundary).
#:
#: ⛔ THE SECOND HALF OF THIS LIST IS A REVIEW FINDING, NOT A THOUGHT
#: EXPERIMENT (#13557/#13558 review, P0/P1). The first cut of the cue table
#: matched the BARE verb ("end", "withdraw", "retract") and the BARE noun
#: ("commitment"), and every query below then returned a whole state population
#: instead of matching rows — `what is the end date of the contract` came back as
#: the ended population of the family, `commitments due this week` as every live
#: commitment with "due this week" dropped. Each one is pinned here so that
#: regression cannot come back quietly.
NOT_SELECTED = [
    "launch event",
    "onboarding flow",
    "beach club confirmed booking",
    "pricing decision Q3 Q4",
    "thursday partner call",
    "the meeting was cancelled",
    "what was planned and never happened",
    "onboarding flow retired",
    # the bare-verb class the review measured
    "what is the end date of the contract",
    "what front end framework do we use",
    "what is the back end stack",
    "what happens if I withdraw consent",
    "show me the retired onboarding flow",
    "what did we end up doing",
    # the bare-noun class the review measured
    "commitments due this week",
    "commitment letter template",
    "our commitment to privacy",
    "sponsorship commitment review",
    "",
    "   ",
]


@pytest.mark.parametrize("query", NOT_SELECTED)
def test_an_ordinary_query_keeps_the_relevance_read(query):
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population is None, (
        f"{query!r} was routed to the {got.population!r} population "
        f"(shape {got.shape!r}) — a detector that hijacks a content lookup "
        f"answers a question nobody asked")


def test_none_is_a_full_scan_not_a_question():
    from tortoise.state_intent import detect_state_question

    assert detect_state_question(None).population is None


def test_an_ending_question_outranks_a_commitment_noun():
    """'which commitments were withdrawn' is an ENDING question.

    The reverse precedence would answer a withdrawal question with everything
    still standing — the exact failure #13558 was filed for.
    """
    from tortoise.state_intent import ENDED, detect_state_question

    assert detect_state_question("which commitments were withdrawn").population == ENDED


def test_every_lifecycle_word_the_readers_hide_has_an_ended_cue():
    """The cue words and `live.TERMINAL_EXCLUDED_STATUSES` cannot drift apart.

    A review finding (#13557/#13558 review, P2): the first cut's docstring claimed
    the cue set was "the same words ``live.TERMINAL_EXCLUDED_STATUSES`` renders"
    while ``archived`` was missing from it — so "which claims were archived" never
    reached the ended read, which is #13558's whole shape for one of the model's
    own words. This pins the direction that matters: a word that HIDES a record
    from the current reads must be a word a customer can ask about. (The reverse
    does not hold — "withdrawn"/"retired"/"ended" need no status word, because the
    model spells those endings `deprecated`/`superseded`.)

    ``outdated`` is the one deliberate exception and it is asserted as such: the
    write door REFUSES it (`#11824` S0), so no customer can reach a row by asking
    about it, and the rows carrying it are answered by `deprecated`, its recorded
    meaning. If that changes, this test says so instead of quietly missing a word.
    """
    import re

    from tortoise.live import TERMINAL_EXCLUDED_STATUSES
    from tortoise.state_intent import _ENDING_WORDS

    cue = re.compile(rf"^(?:{_ENDING_WORDS})$")
    uncued = sorted(w for w in TERMINAL_EXCLUDED_STATUSES if not cue.match(w))
    assert uncued == ["outdated"], (
        f"lifecycle words the readers HIDE with no cue of their own: {uncued} — "
        f"'which claims were <word>' would reach nothing, which is #13558's "
        f"failure for one of the model's own words")
    # …and the cue set is used, not merely declared: one query per word.
    from tortoise.state_intent import ENDED, detect_state_question

    for word in sorted(TERMINAL_EXCLUDED_STATUSES - {"outdated"}):
        assert detect_state_question(f"which claims were {word}").population == ENDED, word


def test_the_population_register_names_a_module_for_every_member():
    """The routing table and the reads cannot drift apart."""
    import importlib

    from tortoise.state_intent import POPULATIONS

    assert set(POPULATIONS) == {"commitments", "ended"}
    for population, module in POPULATIONS.items():
        assert importlib.import_module(module), population


# ══════════════════════════════════════════════════════════════════════════════
# #13557 — Q1: the commitment question, answered by KIND + STAGE
# ══════════════════════════════════════════════════════════════════════════════


def _commitment_world(sdk):
    """The capstone's Q1 narrative, through the product's own write doors.

    A live plan (a ``core:plan`` on a non-terminal stage), a live goal and a live
    target (the commitment-state family of §5), an unrelated live Object, a
    retired Object (``deprecated``, nothing replacing it) and a plan that reached a
    terminal stage. Returns the handles by role.
    """
    from tortoise import pipeline

    pipeline.seed_pipeline(sdk._get_proj().g, "core:plan", PLAN_PIPELINE)
    plan = sdk.create_object("Launch event — 12 December at the beach club",
                             objectKind="core:plan")
    pipeline.advance_stage(sdk, plan["id"], "core:plan", "scheduled")
    goal = sdk.create_object("Q4 uptime", objectKind="core:goal",
                             props={"goalState": "unmet"})
    target = sdk.create_object("Uptime target — 99.9% or better",
                               objectKind="core:target",
                               props={"comparator": ">=", "threshold": 99.9})
    other = sdk.create_object("Office lease", objectKind="process")
    retired = sdk.create_object("Old onboarding flow", objectKind="process")
    sdk.update_knowledge(retired["id"], status="deprecated")
    cancelled = sdk.create_object("Thursday partner call — 6 November",
                                  objectKind="core:plan")
    pipeline.advance_stage(sdk, cancelled["id"], "core:plan", "cancelled")
    return {"plan": plan, "goal": goal, "target": target, "other": other,
            "retired": retired, "cancelled": cancelled}


@_docker_only
def test_a_commitment_question_returns_every_live_commitment_by_kind(sdk):
    """Q1. The question names no commitment's own noun, and every one comes back.

    This is the defect: ``search('current commitments', object)`` returned 0 rows
    while the plan sat in the graph, live and staged. Nothing here supplies a
    query term that occurs in the plan's text — the population is its KIND and its
    STAGE.
    """
    world = _commitment_world(sdk)

    rows = sdk.tortoise_fts_query("current commitments", entity_type="object",
                                  limit=50)
    by_id = {r["id"]: r for r in rows}

    for role in ("plan", "goal", "target"):
        assert world[role]["id"] in by_id, (
            f"the live {role} is missing from the commitment question's answer — "
            f"the commitment set is incomplete (#13557)")

    # The plan answers with the position it stands on, so "outstanding" is
    # visible rather than inferred from the absence of an ending.
    stage = by_id[world["plan"]["id"]]["stage"]
    assert stage["slot"] == "entry"
    assert stage["id"] == "scheduled"
    assert by_id[world["plan"]["id"]]["isClosed"] in (None, False)

    # …and the kinds are the family's, so an unrelated live Object is not a
    # commitment (otherwise the read would just be "everything current").
    assert world["other"]["id"] not in by_id
    assert {r["objectKind"] for r in rows} <= {
        "core:plan", "core:goal", "core:target"}


@_docker_only
def test_a_retired_or_ended_commitment_is_not_in_the_commitment_question(sdk):
    """The other half of Q1: the retired flow and the cancelled plan stay out.

    A ``deprecated`` process is not a commitment kind *and* has ended; a plan on a
    terminal stage has ended on the POSITION axis with no status word at all. Both
    are the currency rule (#13034), read in the keep direction — so this is the
    same determinant every other reader filters on, not a list written here.
    """
    world = _commitment_world(sdk)

    rows = sdk.tortoise_fts_query("what are we committed to", entity_type="object",
                                  limit=50)
    served = _ids(rows)

    assert world["retired"]["id"] not in served, (
        "a retired commitment was served as a current one — the trust failure "
        "this model exists to remove")
    assert world["cancelled"]["id"] not in served, (
        "a plan that ended on a terminal stage was served as a live commitment "
        "(#13034's position axis)")


@_docker_only
def test_a_draft_is_current_but_is_not_a_commitment(sdk):
    """design §4: a draft is *"created, not yet a commitment … not in force"*.

    The one place where "current" and "a commitment" differ. Pinned because the
    keep predicate (rightly) KEEPS drafts — they are not dead — so a read that
    simply reused it would report a not-yet-committed thing as committed.
    """
    draft = sdk.create_object("Draft launch plan", objectKind="core:plan",
                              status="draft")

    rows = sdk.tortoise_fts_query("current commitments", entity_type="object",
                                  limit=50)
    assert draft["id"] not in _ids(rows)
    # …and it is still CURRENT, so the ended read must not claim it either.
    assert draft["id"] not in _ids(
        sdk.tortoise_fts_query(None, entity_type="object", ended_scope=True,
                               limit=50))


@_docker_only
def test_the_commitment_question_honours_the_callers_kind_filter(sdk):
    """A narrowing the caller asked for is honoured, not overridden."""
    world = _commitment_world(sdk)

    rows = sdk.tortoise_fts_query("current commitments", kind="goal",
                                  entity_type="object", limit=50)
    served = _ids(rows)

    assert world["goal"]["id"] in served
    assert world["plan"]["id"] not in served, (
        "the caller narrowed to goals and got a plan — a kind filter dropped in "
        "silence is the defect class this read names")
    assert world["target"]["id"] not in served


@_docker_only
def test_a_point_scoped_commitment_question_is_not_answered_with_objects(sdk):
    """The commitment kinds are OBJECT kinds (§5) — the family contract holds.

    A caller asking for ``point`` keeps the relevance read it had (on the
    capstone's Q1 that half already returned the live pricing/uptime claims), and
    never receives Objects it did not ask for.
    """
    world = _commitment_world(sdk)

    rows = sdk.tortoise_fts_query("current commitments", entity_type="point",
                                  limit=50)
    assert world["plan"]["id"] not in _ids(rows)
    assert all(r.get("id") for r in rows)  # no crash, an ordinary read


# ══════════════════════════════════════════════════════════════════════════════
# #13558 — Q6: the withdrawal question, answered by the ended population
# ══════════════════════════════════════════════════════════════════════════════


@_docker_only
def test_a_withdrawal_question_returns_the_ended_records_and_how_each_ended(sdk):
    """Q6. The customer's sentence reaches the ended read with NO special scope.

    The pre-fix behaviour is the defect: two rows sharing no word with the
    question, at score 0.0, and the withdrawn claim reachable only when terminal
    scope was forced by name.
    """
    live = sdk.create_point(kind="statement", content="the venue is booked")
    retracted = sdk.create_point(
        kind="statement", content="The beach club confirmed our 12 December booking.")
    sdk.retract_point(retracted["id"])
    old = sdk.create_point(kind="decision", content="the entry tier is 29")
    new = sdk.create_point(kind="decision", content="the entry tier is 39")
    sdk.supersede_point(old["id"], new["id"])

    rows = sdk.tortoise_fts_query("has anything we relied on been withdrawn",
                                  entity_type="point", limit=50)
    by_id = {r["id"]: r for r in rows}

    assert live["id"] not in by_id, (
        "a live claim was served as the answer to a withdrawal question")
    assert retracted["id"] in by_id, (
        "the withdrawal question did not reach the withdrawn claim — that is "
        "#13558 exactly")
    assert by_id[retracted["id"]]["status"] == "retracted"

    # "how each ended" is on the row, in the record's own fields.
    assert old["id"] in by_id
    assert by_id[old["id"]]["status"] == "superseded"
    assert by_id[old["id"]]["successorId"] == new["id"]
    assert by_id[retracted["id"]]["endedAt"], (
        "the ending time must be served where the record carries one")


@_docker_only
def test_a_live_unrelated_record_is_not_served_as_the_answer(sdk):
    """The other half of #13558: the question is not answered with unrelated rows."""
    live = sdk.create_point(kind="statement", content="the venue is booked")
    dead = sdk.create_point(kind="statement", content="an old claim we withdrew")
    sdk.retract_point(dead["id"])

    rows = sdk.tortoise_fts_query("has anything been withdrawn",
                                  entity_type="point", limit=50)
    assert dead["id"] in _ids(rows)
    assert live["id"] not in _ids(rows), (
        "a live record was served as the answer to 'has anything been "
        "withdrawn' — the customer is told about something that never ended")


@_docker_only
def test_a_withdrawal_question_on_a_clean_graph_says_so_plainly(sdk):
    """Nothing has ended → an empty answer, not unrelated content.

    A graph with live records only: the pre-fix default scope answered the same
    question with those live rows at score 0.0 (that is what "unrelated rows"
    means here). The answer must be empty, and the read status must say the store
    ANSWERED — ``empty`` is a fact about the graph, ``degraded`` is not.
    """
    live = sdk.create_point(kind="statement", content="the venue is booked")

    status: dict = {}
    rows = sdk.tortoise_fts_query("has anything we relied on been withdrawn",
                                  entity_type="point", limit=50,
                                  read_status_out=status)
    assert rows == [], (
        "nothing has ended, so the answer to 'has anything been withdrawn' must "
        "be empty — serving live rows is the defect")
    assert live["id"] not in _ids(rows)
    assert status.get("status") == "empty", status


# ══════════════════════════════════════════════════════════════════════════════
# Both directions of the one determinant
# ══════════════════════════════════════════════════════════════════════════════


@_docker_only
def test_the_two_populations_are_disjoint_in_both_directions(sdk):
    """A live commitment is never in the ended read; an ended one never here.

    This is what makes the two reads ONE model (design §6's single determinant
    read in both directions) rather than two lists that can drift: if either
    predicate is edited without the other, this reds.
    """
    from tortoise import pipeline

    pipeline.seed_pipeline(sdk._get_proj().g, "core:plan", PLAN_PIPELINE)
    live_plan = sdk.create_object("Live plan", objectKind="core:plan")
    pipeline.advance_stage(sdk, live_plan["id"], "core:plan", "scheduled")
    cancelled = sdk.create_object("Cancelled plan", objectKind="core:plan")
    pipeline.advance_stage(sdk, cancelled["id"], "core:plan", "cancelled")
    retired = sdk.create_object("Retired goal", objectKind="core:goal")
    sdk.update_knowledge(retired["id"], status="deprecated")

    commitments = _ids(sdk.tortoise_fts_query(
        "current commitments", entity_type="object", limit=50))
    ended = _ids(sdk.tortoise_fts_query(
        None, entity_type="object", ended_scope=True, limit=50))

    assert live_plan["id"] in commitments
    assert live_plan["id"] not in ended
    assert cancelled["id"] in ended
    assert cancelled["id"] not in commitments
    assert retired["id"] in ended
    assert retired["id"] not in commitments
    assert not (commitments & ended), (
        "a record is in BOTH populations — the two reads are no longer the two "
        "directions of one determinant")


@_docker_only
def test_a_LEGACY_outdated_row_is_in_the_ended_read_and_not_a_commitment(sdk):
    """The duality holds on the row shape that broke it (#13557/#13558 review P1).

    The first cut composed the commitments read with the OBJECT read-leg
    vocabulary (`OBJECT_TERMINAL_STATUSES`, which has no `outdated`) while the
    ended read selects `live._terminal_expression` (which HAS `outdated`, as a
    status and as a flag). A commitment-kind Object carrying `status='outdated'`
    was therefore CURRENT to one read and ENDED to the other — the same record in
    both populations, in one session.

    The write door REFUSES `outdated` now (#11824 S0), so the row is written RAW:
    the population being modelled is the pre-S0 one, measured as present in the
    canonical instance (`assembly.py`'s `_RECALL_OBJECT_EXCLUDED_STATUSES` keeps
    the word for exactly this shape). Same precedent as
    `tests/test_11926_derived_invalidation.py`.
    """
    from tortoise import pipeline

    pipeline.seed_pipeline(sdk._get_proj().g, "core:plan", PLAN_PIPELINE)
    legacy = sdk.create_object("Legacy outdated plan", objectKind="core:plan")
    pipeline.advance_stage(sdk, legacy["id"], "core:plan", "scheduled")
    sdk._get_proj().g.query(
        "MATCH (n {id:$id}) SET n.status = 'outdated'",
        params={"id": legacy["id"]})

    commitments = _ids(sdk.tortoise_fts_query(
        "current commitments", entity_type="object", limit=50))
    ended = _ids(sdk.tortoise_fts_query(
        None, entity_type="object", ended_scope=True, limit=50))

    assert legacy["id"] in ended, (
        "the ended read selects by live._terminal_expression, which treats "
        "`outdated` as an ending")
    assert legacy["id"] not in commitments, (
        "a legacy `outdated` commitment was served as CURRENT — the keep side is "
        "not the dual of the selector the ended read uses")


# ══════════════════════════════════════════════════════════════════════════════
# The answer is BOUNDED, and a scan that could not answer says so
# ══════════════════════════════════════════════════════════════════════════════


@_docker_only
def test_a_capped_commitment_answer_is_marked_truncated(sdk):
    """A page is never served as "these are all the commitments"."""
    _commitment_world(sdk)

    trunc: dict = {}
    rows = sdk.tortoise_fts_query("current commitments", entity_type="object",
                                  limit=2, _truncation_out=trunc)
    assert len(rows) == 2
    from tortoise.truncation import EXACT_KEY, truncation_field

    marker = truncation_field(trunc)
    assert marker["truncated"] is True, marker
    # ⛔ `exact` is the producer's PROOF that the page is the whole population, and
    # a capped page must never carry it — a full page is not a complete answer.
    assert trunc[EXACT_KEY] is False, (
        "a full page must never be certified as the whole population")
    # …and the proof is real: the population is larger than the page served.
    everything = sdk.tortoise_fts_query("current commitments", entity_type="object",
                                        limit=50)
    assert len(everything) > len(rows), (len(everything), len(rows))


@_docker_only
def test_a_commitments_scan_that_could_not_answer_is_carried_not_swallowed(
        sdk, monkeypatch):
    """Contract: zero rows + a degraded leg + `incomplete` when a sink exists.

    ``[]`` from a store that could not answer is byte-identical to the factual
    claim "nothing is committed", so the marking must reach the caller — and with
    NO sink it must raise instead (#13302 round 2's contract, applied to the new
    population).
    """
    import tortoise.commitments as commitments_mod

    def boom(*_a, **_kw):
        raise RuntimeError("store said no")

    monkeypatch.setattr(commitments_mod, "enumerate_live_commitments", boom)

    status: dict = {}
    trace: list = []
    trunc: dict = {}
    rows = sdk.tortoise_fts_query("current commitments", entity_type="object",
                                  limit=10, read_status_out=status,
                                  leg_trace=trace, _truncation_out=trunc)
    assert rows == []
    assert any(t.get("leg") == "commitments" and t.get("degraded") is True
               for t in trace), trace
    assert trunc.get("truncated") is True, trunc
    assert status.get("status") == "degraded", status

    # …and with no sink there is nowhere for the marking to go, so the store's
    # error rises rather than an unmarked empty being served.
    with pytest.raises(RuntimeError):
        sdk.tortoise_fts_query("current commitments", entity_type="object",
                               limit=10)


@_docker_only
def test_no_retrieval_knob_is_served_beside_a_state_question(sdk, monkeypatch):
    """A knob is a relevance-read request: the call keeps the text read.

    ``min_confidence`` cannot be applied to a population that carries no belief,
    and silently dropping it would serve rows the caller asked to filter. So the
    state route is taken only for a PLAIN question — and this pins the DECISION by
    intercepting the population's own enumerator, not by inspecting rows: with a
    knob set the arm must never be reached, and without one it must be.

    ⛔ The first cut asserted `isinstance(rows, list)` and was caught in review as
    a test that cannot fail — deleting the guard would have left it green. A
    sentinel is what makes the property observable.
    """
    import tortoise.commitments as commitments_mod

    reached: list[list[str]] = []

    def record(graph, *, kinds, limit=50, timeout_ms=None):
        reached.append(list(kinds))
        return []

    monkeypatch.setattr(commitments_mod, "enumerate_live_commitments", record)

    # A KNOB → the relevance read (which cannot report a belief above 0.99 here,
    # so the rows are empty either way: the ASSERTION is on `reached`).
    sdk.tortoise_fts_query("current commitments", entity_type="object",
                           min_confidence=0.99, limit=5)
    assert reached == [], (
        "the commitment population was served to a caller who asked for a "
        "relevance read with `min_confidence` — the floor would be silently "
        "dropped")

    # NO KNOB → the population, with no scope argument from the caller.
    sdk.tortoise_fts_query("current commitments", entity_type="object", limit=5)
    assert reached, "the plain commitment question did not reach the population"


def test_the_ended_scope_still_refuses_an_explicit_query(sdk):
    """The caller-facing contract is unchanged: an EXPLICIT scope + query reds.

    Only a query the read itself RESOLVED into the scope is exempt (it selected
    the population); a caller who sends one beside ``ended_scope=True`` is asking
    for two different reads at once.
    """
    with pytest.raises(ValueError) as err:
        sdk.tortoise_fts_query("anything withdrawn", entity_type="point",
                               ended_scope=True)
    assert "ended_scope" in str(err.value)
