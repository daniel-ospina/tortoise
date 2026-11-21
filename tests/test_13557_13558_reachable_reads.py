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
        # ⛔ A moved or renamed seam — including an import of a moved module — is a
        #   PROGRAMMING error, not "no server": it must not read as "storage
        #   unavailable" (#14114 class 2, #14125). Without this arm an
        #   `AttributeError` on `proj.g.query` (or a `ModuleNotFoundError` on the
        #   import above) fell through to the broad arm and the family SKIPPED
        #   green over a broken tree.
        except (AttributeError, NameError, TypeError, ImportError):
            raise
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
    ("what are our commitments", "commitments", "determined_noun"),
    ("our commitments", "commitments", "determined_noun"),
    # …and the wh-word's own copula, which is the shape the noun anchor admits:
    # the noun phrase is still the WHOLE request, only introduced by "what are".
    # Pinned because the start anchor (round-5 review, P2) is what refuses
    # `what did the vendor say about our commitments` — a start anchor that also
    # refused the bare set question would be a recall loss with no finding.
    ("what are the current commitments", "commitments", "scoped_noun"),
    # #13558 — Q6, and the other shapes a customer uses for it.
    ("has anything we relied on been withdrawn", "ended", "existence"),
    ("has anything been retired", "ended", "existence"),
    ("have any claims been archived", "ended", "existence"),
    # …and the example the ``existence`` docstring gives, made executable so the
    # contract it states cannot drift from the words it cites (round-4 review P3).
    ("have any claims been retracted", "ended", "existence"),
    ("what ended", "ended", "wh_whole_question"),
    ("what ended?", "ended", "wh_whole_question"),
    ("what has been withdrawn", "ended", "wh_whole_question"),
    ("what was retracted", "ended", "wh_whole_question"),
    ("which commitments were withdrawn", "ended", "which_predicate"),
    ("which claims were archived", "ended", "which_predicate"),
    # A set question whose predicate IS the ending — the `which` shape with a
    # A set question whose predicate IS the ending — the `which` shape with a
    # plural-head noun that is one of ASKED-ABOUT records (`endpoints`). ⛔ THE
    # ROUND-5 REVIEW CLASSIFIED THIS STRING AS A `NOT_SELECTED` ROW AND THIS FILE
    # DELIBERATELY DOES NOT, so the disagreement is recorded here rather than
    # left for the next reader to rediscover (the review named it in its
    # mutation-attribution gap: *"`which of our endpoints is deprecated` … a
    # `NOT_SELECTED` entry waiting to be written"*, and left it as an open
    # judgement call for the next reviewer). It stays SELECTED. Two reasons, and
    # the second is the one that decides it:
    #
    #   1. The population read is not a state FILTER, it is the ended SET with
    #      each row's own how-it-ended. `which claims were archived` is pinned
    #      SELECTED above for exactly that reason, so `… are deprecated` cannot be
    #      split off from it on the tense: `which <noun> ARE <participle>` and
    #      `which <noun> WERE <participle>` are the same request — the present
    #      form is the one a caller uses for a CURRENT terminal state, and the
    #      relevance read HIDES terminal records, so refusing it would answer a
    #      question about archived/deprecated rows with `0 rows`, i.e. restore
    #      #13558's silent-empty defect for the present-tense spelling.
    #   2. The discriminator that would separate this string from
    #      `which claims were archived` is the NOUN's record-class status
    #      (`endpoints` is not a graph kind, `claims` is), and a noun allow-list
    #      is a noun→route assignment — the same sin as the noun→state assignment
    #      #13128 refuses (*"a phrase is not a kind"*) and that this table's own
    #      header refuses (*"the cue is a QUESTION SHAPE, never a noun-to-state
    #      assignment"*). The review's suggested remedy therefore contradicts a
    #      recorded decision of this change, so it is arguable only as a reopen,
    #      not adoptable as a fix.
    #
    # ⛔ AND THE PARTITIVE IS NOT THE DISCRIMINATOR EITHER — the WINDOW was. The
    # reviewer's remaining objection was that `of our endpoints` is the only
    # narrowing in the question and the population read cannot apply it; that is
    # equally true of the wh-noun `claims` in the row above. What was actually
    # wrong with this shape was its bounded-FREE 30-character window, which on the
    # pre-fix module admitted a whole subordinate clause (`which vendor said our
    # commitments were archived`). Bounding the window to head-noun adjacency
    # fixes the hijack and leaves every genuine set question working — the
    # narrower change, and the one the precision contract asks for. See
    # `test_the_partitive_is_not_the_discriminator`.
    #
    # The residual is stated rather than hidden: `which endpoints are deprecated`
    # routes and returns the ended set, whose rows carry their own status word, so
    # a caller reading it can tell a `deprecated` row from an `archived` one. What
    # it does NOT do is apply `deprecated` as a filter — the same recall-as-
    # population trade the other SELECTED rows make.
    ("which of our endpoints is deprecated", "ended", "which_predicate"),
    ("what did we archive last quarter", "ended", "do_support"),
    ("did we retire the onboarding flow", "ended", "do_support"),
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
    # ── the bare-VERB class (round-1 review, measured) ──────────────────────
    "what is the end date of the contract",
    "what front end framework do we use",
    "what is the back end stack",
    "what happens if I withdraw consent",
    "show me the retired onboarding flow",
    "what did we end up doing",
    # ── the bare-NOUN class (round-1 review, measured) ─────────────────────
    "commitments due this week",
    "commitment letter template",
    "our commitment to privacy",
    "sponsorship commitment review",
    # ── the ATTRIBUTIVE-participle class (round-2 review, measured) ────────
    "what is our abandoned cart recovery rate",
    "what's our refund policy for cancelled orders",
    "what did the customer say when we cancelled their order",
    "what is the definition of an abandoned cart",
    "which archived report has the Q3 numbers",
    "what is the archived pricing document",
    "what did we learn from the cancelled launch",
    # ── the COPULA-IN-A-SUBORDINATE-CLAUSE class (round-3 review, measured) ─
    # A passive in a clause the caller tacked on is not the question. Each of
    # these was answered with the whole ended population by the cut that accepted
    # "a copula anywhere within 60 characters of a wh-word".
    "what happens when an order is cancelled",
    "what is the policy when a booking is cancelled",
    "what is our refund policy for orders that were cancelled",
    "what documents are archived",
    "what data is archived in S3",
    "what features are deprecated in v3",
    "what do I do if my booking is cancelled",
    "what do we do when we cancel a subscription",
    "what is the payment terms clause given the contract was archived",
    "what is the payment terms clause, given the contract was archived",
    # ── the ATTRIBUTIVE existence / which_predicate class (round-4 review) ──
    # The participle here MODIFIES the noun that follows the quantifier (or the
    # noun after the auxiliary), so none of these is a question about endings —
    # each is a content lookup, and each was answered with the whole ended
    # population. The `existence` auxiliary is now mandatory and
    # `which_predicate` is end-anchored, so they keep the relevance read.
    "have any archived reports been published",
    "have any cancelled orders been refunded",
    "have any deprecated endpoints been removed from the API",
    "do we have any archived reports",
    "has any retired employee's data been deleted",
    "which products have archived documentation",
    "which units are cancelled in the export",
    # ── the fact-question class about commitments (round-2/3 review) ────────
    "what commitments did the vendor make in the SLA",
    "which commitments are in the contract",
    "how much have we committed to the SOC2 audit",
    "current commitments in EMEA",
    # ── the THIRD-PARTY SUBJECT and PREPOSITIONAL-TAIL class (round-4 review) ─
    # The subject rule is the docstring's own measured example —
    # `what commitments did the vendor make in the SLA` — one word different:
    # `our competitors` is not `we`, and it was answered with OUR commitments. The
    # tail rows are the constraint rule the shape already states for the NOUN
    # shapes (`current commitments in EMEA`) applied to the VERB shape, where a
    # free tail let `in EMEA` / `to the SOC2 audit` be dropped in silence.
    "what are our competitors committed to",
    "what are our vendors committed to",
    "what are we committed to in EMEA",
    "what have we committed to the SOC2 audit",
    # ── the APPENDED-CONSTRAINT class (P2-5 review, measured) ──────────────
    # The same questions with the constraint set off by a COMMA. The first cut
    # scanned only `text.split(",")[0]`, so these routed while their comma-less
    # twins above kept the relevance read — the constraint the caller appended
    # was silently dropped, which is the one thing the commitment shapes say
    # must never happen. Both spellings are pinned so the split cannot return.
    "current commitments, in EMEA",
    "all the commitments, by owner",
    # …and the recall the removal gives up, stated rather than discovered: only
    # the WHOLE question can be the question, so a compound question keeps the
    # relevance read even when its first clause is an ending.
    "what ended, and what replaced it",
    # ── STATEMENTS, third-party actions, and boundaries stated in the module ─
    "did the customer cancel their order",
    "what has ended recently",
    "what commitments do we have",
    "",
    "   ",
    # ── the PRESENT-HABITUAL class (round-5 review, P1) ──────────────────────
    # `do|does` + we + a base ending verb is the affirmative present-habitual
    # frame — a question about what we DO, answered with the whole ended
    # population on the default SDK / MCP / hosted `/search` surfaces, with no
    # way to switch the route off. `did` is the anti-signal; these four are the
    # class, not two instances.
    "do we archive customer emails",
    "do we cancel subscriptions automatically",
    "do we archive old reports",
    "do we retract statements when challenged",
    "does legal archive the register",
    # ── the APPENDED-CONSTRAINT class on `existence` (round-5 review, P1) ────
    # `existence` was the one ending shape with no tail anchor, so the caller's
    # narrowing was silently dropped and the WHOLE ended population was served
    # as if it had been applied. Both orders of the constraint are pinned.
    "have any claims been retracted in Q3",
    "have any of our bookings been cancelled by the venue",
    "has anything we promised been withdrawn by legal",
    # …the same class on the `which` shape, which is the row the pre-fix detector
    # also served (round-3 review, P1: *"`which claims were retracted by legal`
    # → `by legal` dropped"*). It is pinned HERE because the user-facing MCP
    # description now names this exact string as a relevance read
    # (`mcp_server.py::tortoise_search`), and a promise in a tool description that
    # no row reds on is a claim, not a contract.
    "which claims were retracted by legal",
    "which reports were superseded by the new one",
    # ── the THIRD-PARTY-CLAUSE and STATEMENT class on the NOUN shapes ───────
    # (round-5 review, P2) Ending at the noun was only half the rule: with no
    # subject guard the noun phrase matched inside somebody else's clause and
    # the caller's actual subject was thrown away, so a question this population
    # CANNOT answer came back as OUR commitments. The last row is a statement.
    "what did the vendor say about our commitments",
    "what did legal advise about our commitments",
    "what are the risks to our commitments",
    "the board discussed current commitments",
    # ── the SUBORDINATE-CLAUSE class on `which_predicate` (this lane, ruling on
    # the round-5 review's open judgement call) ─────────────────────────────
    # The `which` window was a free 30 characters, so an ending participle inside
    # SOMEBODY ELSE'S clause selected the whole ended population — the caller's
    # head noun, and with it the question, silently dropped. The shape now
    # requires the wh-phrase's head noun to be ADJACENT to the auxiliary, which
    # is what refuses these. Measured on the pre-fix module: all four routed.
    "which vendor said our commitments were archived",
    "which report says the product was deprecated",
    "which notice confirmed the account was cancelled",
    "which customer reported that our claims were retracted",
    # …and the ATTRIBUTIVE twin the adjacency guard also refuses, which is why
    # `which archived report has the Q3 numbers` (already pinned above) can never
    # come back through the window it was fixed for.
    "which archived reports were retracted",
    # …and the recall the adjacency guard gives up, stated rather than discovered:
    # a MODIFIED head noun sits in the same slot as the subordinate clause's verb,
    # so it keeps the relevance read. See the shape's docstring.
    "which customer claims were archived",
    # …and the recall the start anchor gives up, stated rather than discovered:
    # an IMPERATIVE set request keeps the relevance read, because the measured
    # hijack `show me the retired onboarding flow` is the same imperative frame
    # asking for ONE record.
    "list our commitments",
    "show me all the commitments",
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


def test_the_partitive_is_not_the_discriminator():
    """The `which` guard is HEAD-NOUN ADJACENCY, not the partitive marker.

    The round-5 review left `which of our endpoints is deprecated` as an open
    judgement call: keep it a SELECTED row (as the previous round pinned it, on
    the reading that the wh-noun is the thing the question asks about) or move it
    to NOT_SELECTED (the review's reading, that `of our endpoints` is a narrowing
    the population read cannot apply).

    ⛔ IT STAYS SELECTED, and the two rows below are why: the objection that the
    wh-noun is unapplied is equally true of `which claims were archived` — the
    capability's canonical Q6 spelling — so refusing the partitive would refuse
    both, and the shape would stop answering the question it exists for. What was
    actually wrong with this shape was its FREE WINDOW: the same 30 characters
    that admit `of our endpoints` admitted a whole subordinate clause. Bounding
    the window to the head noun fixes the hijack and leaves every genuine set
    question working — the narrower change, and the one the module's precision
    contract asks for.

    This test is the falsifier for that reasoning: the three rows differ only in
    where the wh-phrase ends and the auxiliary begins, so a guard that keyed on
    the partitive, or that let the window come back, reds here.
    """
    from tortoise.state_intent import ENDED, detect_state_question

    # A partitive wh-phrase whose predicate IS the ending — a real set question.
    partitive = detect_state_question("which of our endpoints is deprecated")
    assert partitive.population == ENDED, (
        "`which of our endpoints is deprecated` asks for the ended set of a "
        "named family; refusing it would cost the same recall as refusing "
        "`which claims were archived`")

    # …and its bare twin, which carries the same unapplied wh-noun. If the
    # partitive is refused, this must be refused too, or the ruling is arbitrary.
    assert detect_state_question("which claims were archived").population == ENDED

    # The clause twin: the ending participle belongs to a SUBORDINATE clause, so
    # the question is about the vendor, not about what ended.
    for clause in (
        "which vendor said our commitments were archived",
        "which report says the product was deprecated",
        "which notice confirmed the account was cancelled",
    ):
        got = detect_state_question(clause)
        assert got.population is None, (
            f"{clause!r} was answered with the {got.population!r} population "
            f"(shape {got.shape!r}) — the ending belongs to somebody else's "
            f"clause, and the caller's head noun was dropped")


def test_an_ending_question_outranks_a_commitment_noun():
    """A question BOTH tables match is answered by the ending.

    The reverse precedence would answer a withdrawal question with everything
    still standing — the exact failure #13558 was filed for.

    ⛔ THE OVERLAP IS THE POINT, and the first version of this test did not have
    one (P2-4 review): `which commitments were withdrawn` matches only
    ``which_predicate``, so swapping the two loops in ``detect_state_question``
    reddened NOTHING — the ordering was unobservable and the docstring's
    justification for it was not what the test measured. The compound question
    below is ``committed_to`` AND ``existence`` at once, which is what makes
    this a falsifier: reverse the loops and it answers ``commitments`` (measured:
    the mutant returns `StateQuestion('commitments', 'committed_to', …)`).
    """
    from tortoise.state_intent import ENDED, detect_state_question

    overlapping = "what are we committed to and has anything been withdrawn"
    # …both tables really do match it, or this row proves nothing about ordering.
    from tortoise import state_intent

    assert any(p.search(overlapping) for _, p in state_intent._COMMITMENT_SHAPES), (
        f"{overlapping!r} is no longer matched by a COMMITMENT shape, so the "
        f"precedence this test pins is unobservable again")
    assert any(p.search(overlapping) for _, p in state_intent._ENDING_SHAPES), (
        f"{overlapping!r} is no longer matched by an ENDING shape, so the "
        f"precedence this test pins is unobservable again")

    got = detect_state_question(overlapping)
    assert got.population == ENDED, (
        f"a question both cue tables match was answered by the {got.population!r} "
        f"population (shape {got.shape!r}) — ENDED must win, or a withdrawal "
        f"question is answered with everything still standing (#13558)")

    # The single-table case still routes, for the reason it always did.
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
    retired commitment (``core:goal``, ``deprecated``, nothing replacing it) and a
    plan that reached a terminal stage. Returns the handles by role.

    ⛔ THE RETIRED RECORD CARRIES A COMMITMENT KIND ON PURPOSE, and that is a
    review finding rather than a detail (round-5 review, P2, rule 3 of the
    attribution contract). It used to be a ``process`` — which the KIND narrowing
    alone excludes — so the assertion watching it stayed green when the currency
    predicate was reverted to ``"true"`` and when the draft conjunct was deleted:
    it read as a status pin while proving nothing about status. With a commitment
    kind, the only thing keeping it out is the predicate the test claims to pin.
    """
    from tortoise import pipeline

    pipeline.seed_pipeline(sdk._get_proj().g, "core:plan", PLAN_PIPELINE)
    plan = sdk._create_object("Launch event — 12 December at the beach club",
                             objectKind="core:plan")
    pipeline.advance_stage(sdk, plan["id"], "core:plan", "scheduled")
    goal = sdk._create_object("Q4 uptime", objectKind="core:goal",
                             props={"goalState": "unmet"})
    target = sdk._create_object("Uptime target — 99.9% or better",
                               objectKind="core:target",
                               props={"comparator": ">=", "threshold": 99.9})
    other = sdk._create_object("Office lease", objectKind="process")
    retired = sdk._create_object("Old onboarding goal", objectKind="core:goal")
    sdk.update_knowledge(retired["id"], status="deprecated")
    cancelled = sdk._create_object("Thursday partner call — 6 November",
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

    rows = sdk._tortoise_fts_query("current commitments", entity_type="object",
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
    """The other half of Q1: the retired goal and the cancelled plan stay out.

    A ``deprecated`` COMMITMENT-KIND goal has ended on the status axis, and a plan
    on a terminal stage has ended on the POSITION axis with no status word at all.
    Both are the currency rule (#13034), read in the keep direction — so this is
    the same determinant every other reader filters on, not a list written here.

    ⛔ THE FIRST ASSERTION IS ONLY A PIN BECAUSE THE RECORD IS A COMMITMENT KIND
    (round-5 review, P2, rule 3). While it was a ``process``, the kind narrowing
    excluded it on its own, so replacing ``_keep_expression`` with ``"true"`` —
    the revert this assertion exists to catch — left it green.
    """
    world = _commitment_world(sdk)

    rows = sdk._tortoise_fts_query("what are we committed to", entity_type="object",
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
    draft = sdk._create_object("Draft launch plan", objectKind="core:plan",
                              status="draft")

    rows = sdk._tortoise_fts_query("current commitments", entity_type="object",
                                  limit=50)
    assert draft["id"] not in _ids(rows)
    # …and it is still CURRENT, so the ended read must not claim it either.
    assert draft["id"] not in _ids(
        sdk._tortoise_fts_query(None, entity_type="object", ended_scope=True,
                               limit=50))


def test_an_empty_commitment_kind_set_is_refused_rather_than_answered_empty():
    """``[]`` is the factual claim "nothing is committed" — so it is refused.

    The refusal is a CONTRACT check that runs before any statement is issued, so
    ``graph`` is deliberately ``None`` here: reaching the store would be the bug
    (a bounded read needs a real kind set to narrow on, and there is none).

    ⛔ Hermetic on purpose — no ``sdk`` fixture, no docker. The SDK arm guards
    this with ``if _commit_kinds:`` and so never reaches it, which means nothing in
    the suite reddened when the refusal was deleted: the docstring argued for it
    and no assertion held it. It is a PUBLIC refusal on a public function, so it is
    pinned where it lives.
    """
    from tortoise.commitments import enumerate_live_commitments

    with pytest.raises(ValueError) as err:
        enumerate_live_commitments(None, kinds=[])
    assert "empty kind set" in str(err.value), (
        "an empty kind set was answered rather than refused — `[]` would read as "
        "'nothing is currently committed', which is a factual claim this call "
        "cannot make from an empty QUESTION")


def test_the_commitment_read_reads_no_relationship_edge():
    """The commitment population is a NODE FILTER, not a link read (§13).

    ⛔ THIS IS THE §13 CENSUS DECIDING A DESIGN QUESTION, and it is pinned here so
    the answer cannot be undone by a later "symmetry" edit. The first cut carried
    the Events attached to each commitment —
    ``OPTIONAL MATCH (e:Event)-[:aboutObject]->(n)`` — to mirror
    ``ended.build_cypher``, whose ``linked_events`` IS part of its answer ("how did
    it end" is often only in the event). In the commitment question it is not:
    "how far along" is the stage the row already carries, no test asserted the
    field, and the edge made this module a new customer-callable reader of
    ``aboutObject`` — which the §13 census (`docs/architecture/`
    `STORAGE-ARCHITECTURE.md`) requires to own a row, where a row cannot be keyed
    without §13.8 and the census guard's reader keying being redesigned for a field
    nothing consumed. So the read was removed rather than registered.

    ⛔ Hermetic on purpose (no ``sdk`` fixture, no docker): this is a property of
    the STATEMENT and the served row SHAPE, and both are pure functions. It reds
    on the pre-fix code in two places — the ``OPTIONAL MATCH`` in the query, and
    the ``linkedEvents`` key the row carried.
    """
    from tortoise.commitments import build_cypher

    statement = build_cypher(narrow_kinds=True)
    assert "aboutObject" not in statement, (
        f"the commitment read is walking the aboutObject edge again:\n  {statement}\n"
        "That makes this module a customer-callable READER of the edge, which §13 "
        "must enumerate and which nothing in the commitment answer consumes — "
        "register the row in §13 (and settle §13.8's base-name keying for a "
        "second `build_cypher`) or drop the edge")
    # the general shape, so a DIFFERENT edge is not a silent way around the pin
    assert "OPTIONAL MATCH" not in statement and "MATCH (n:Object) WHERE" in statement, (
        f"the commitment statement is no longer the single node filter it claims "
        f"to be:\n  {statement}")

    # …and the served row does not promise links either, which is the half a
    # caller sees. `linkedEvents` in a commitment row is the field the census
    # question was about.
    from tortoise.commitments import _commitment_row

    served = _commitment_row(
        ("p1", "launch", "core:plan", "live", None, "scheduled", "entry",
         "pending", "2026-01-01", False, "2026-01-02"))
    assert "linkedEvents" not in served, (
        f"a commitment row carries {sorted(served)} — `linkedEvents` is the ended "
        "read's field (its links ARE how it ended) and here it would put the read "
        "back on §13's census for an answer nobody asked for")


def test_the_keep_predicate_is_the_canonical_live_only_predicate():
    """The keep predicate is ``live._live_only`` — ONE home, not a re-spelling.

    ⛔ ROUND-5 REVIEW, P2: the module's own thesis is that the determinant lives in
    one place (``tortoise/commitments.py``: *"A word or a slot those predicates
    stop treating as an ending stops dividing these two reads, and nothing in this
    module needs editing"*). The first cut honoured that for the ENDING half
    (``_terminal_excluded``) and then hand-spelled the DRAFT half beside it
    (``AND coalesce(status, '') <> 'draft'``), so the guarantee silently covered
    only half the predicate. ``live._live_only`` IS "current and not a draft"
    (``include_draft=False`` is its default) and is what every other product
    reader calls.

    ⛔ Hermetic (no ``sdk``, no docker): both sides are pure string functions, and
    the assertion is identity, so it REDS on the pre-fix code — the revert this
    watches (re-spelling the draft conjunct) changes the string.
    """
    from tortoise import commitments
    from tortoise.live import _live_only

    assert commitments._keep_expression("n") == _live_only("n.status"), (
        "the commitment read no longer composes its keep predicate from "
        "`live._live_only` — a re-spelt draft or terminal conjunct is a second "
        "home for the determinant, so a change to what 'current and not a "
        "draft' means stops reaching this read in silence")

    statement = commitments.build_cypher(narrow_kinds=True)
    assert _live_only("n.status") in statement, (
        f"the statement does not carry the canonical predicate:\n  {statement}")
    # …and the specific re-spelling the finding was about cannot come back.
    assert "coalesce(n.status, '') <> " not in statement, (
        f"the hand-spelled draft conjunct is back:\n  {statement}")


@_docker_only
def test_the_commitment_question_honours_the_callers_kind_filter(sdk):
    """A narrowing the caller asked for is honoured, not overridden."""
    world = _commitment_world(sdk)

    rows = sdk._tortoise_fts_query("current commitments", kind="goal",
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

    rows = sdk._tortoise_fts_query("current commitments", entity_type="point",
                                  limit=50)
    assert world["plan"]["id"] not in _ids(rows)


# ══════════════════════════════════════════════════════════════════════════════
# #13558 — Q6: the withdrawal question, answered by the ended population
# ══════════════════════════════════════════════════════════════════════════════


@_docker_only
def test_an_ending_question_on_a_family_with_no_lifecycle_keeps_the_text_read(
        sdk, monkeypatch):
    """A family with no lifecycle is not GIVEN an ended population it cannot have.

    ``ended.enumerate_ended`` REFUSES a family it does not serve rather than
    answering it empty (an Event has no lifecycle — design §8, `ended.py`'s family
    table), and the two must not read the same. So the route must not derive a
    scope the population cannot serve: an Event-scoped ending question keeps the
    relevance read it had.

    Pinned by intercepting the population's own enumerator, not by inspecting rows
    — with the family guard deleted the arm IS reached, and it would raise the
    family refusal rather than serve anything, so a rows-only assertion would pass
    for the wrong reason. The `point` half is the POSITIVE CONTROL that keeps this
    from passing because the route is off altogether.
    """
    import tortoise.ended as ended_mod

    reached: list[str] = []

    def record(graph, *, entity_type, kind=None, kinds=None, limit=50,
               timeout_ms=None):
        reached.append(entity_type)
        return []

    monkeypatch.setattr(ended_mod, "enumerate_ended", record)

    sdk._tortoise_fts_query("has anything we relied on been withdrawn",
                           entity_type="event", limit=5)
    assert reached == [], (
        "an Event-scoped ending question was served the ended population, which "
        "`ended.py` refuses to have — the guard keeping the route off a family "
        "with no lifecycle is gone")

    # POSITIVE CONTROL — the same question, on a family that HAS a lifecycle.
    sdk._tortoise_fts_query("has anything we relied on been withdrawn",
                           entity_type="point", limit=5)
    assert reached == ["point"], (
        "the control did not reach the population, so the assertion above proves "
        "nothing about the guard")


@_docker_only
def test_a_withdrawal_question_returns_the_ended_records_and_how_each_ended(sdk):
    """Q6. The customer's sentence reaches the ended read with NO special scope.

    The pre-fix behaviour is the defect: two rows sharing no word with the
    question, at score 0.0, and the withdrawn claim reachable only when terminal
    scope was forced by name.
    """
    live = sdk._create_point(kind="statement", content="the venue is booked")
    retracted = sdk._create_point(
        kind="statement", content="The beach club confirmed our 12 December booking.")
    sdk._retract_point(retracted["id"])
    old = sdk._create_point(kind="decision", content="the entry tier is 29")
    new = sdk._create_point(kind="decision", content="the entry tier is 39")
    sdk._supersede_point(old["id"], new["id"])

    rows = sdk._tortoise_fts_query("has anything we relied on been withdrawn",
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
    live = sdk._create_point(kind="statement", content="the venue is booked")
    dead = sdk._create_point(kind="statement", content="an old claim we withdrew")
    sdk._retract_point(dead["id"])

    rows = sdk._tortoise_fts_query("has anything been withdrawn",
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
    live = sdk._create_point(kind="statement", content="the venue is booked")

    status: dict = {}
    rows = sdk._tortoise_fts_query("has anything we relied on been withdrawn",
                                  entity_type="point", limit=50,
                                  read_status_out=status)
    assert rows == [], (
        "nothing has ended, so the answer to 'has anything been withdrawn' must "
        "be empty — serving live rows is the defect")
    assert live["id"] not in _ids(rows)
    assert status.get("status") == "empty", status


@_docker_only
def test_a_policy_question_is_not_answered_with_the_ended_population(sdk):
    """The blast radius of a present-habitual cue, measured on served rows.

    ⛔ ROUND-5 REVIEW, P1, and the row demonstration the detector table cannot
    give on its own. `do we archive customer emails` is the present-habitual
    POLICY frame, and pre-fix it selected the ended population — this is the
    measurement that turns the mechanism into a served answer: on a store holding
    one live note and one retracted claim, the question came back as the WITHDRAWN
    claim (the whole ended population, no query applied) with the caller's words
    dropped, on the default SDK / MCP surface and on the hosted `/v1/search`.
    Post-fix the route does not fire, so the relevance read answers and no ending
    is asserted to the customer.
    """
    live = sdk._create_point(kind="statement",
                            content="we archive customer emails on request")
    dead = sdk._create_point(kind="statement",
                            content="an old claim we withdrew")
    sdk._retract_point(dead["id"])

    rows = sdk._tortoise_fts_query("do we archive customer emails",
                                  entity_type="point", limit=50)
    served = _ids(rows)
    assert dead["id"] not in served, (
        "a present-habitual policy question was answered with the ended "
        "population — the caller asked what we DO and was told what has ended, "
        "which is #13558's defect class on a default surface")
    # …and the READ kept is the relevance read the caller's words belong to, so
    # this is not satisfied by an empty answer: pre-fix the note matching the
    # question was NOT in the answer at all (`served == {dead}`).
    assert live["id"] in served, (
        "the policy question's own record was not served — the route either "
        "fired anyway or the relevance read it keeps did not answer")


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
    live_plan = sdk._create_object("Live plan", objectKind="core:plan")
    pipeline.advance_stage(sdk, live_plan["id"], "core:plan", "scheduled")
    cancelled = sdk._create_object("Cancelled plan", objectKind="core:plan")
    pipeline.advance_stage(sdk, cancelled["id"], "core:plan", "cancelled")
    retired = sdk._create_object("Retired goal", objectKind="core:goal")
    sdk.update_knowledge(retired["id"], status="deprecated")

    commitments = _ids(sdk._tortoise_fts_query(
        "current commitments", entity_type="object", limit=50))
    ended = _ids(sdk._tortoise_fts_query(
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
    legacy = sdk._create_object("Legacy outdated plan", objectKind="core:plan")
    pipeline.advance_stage(sdk, legacy["id"], "core:plan", "scheduled")
    sdk._get_proj().g.query(
        "MATCH (n {id:$id}) SET n.status = 'outdated'",
        params={"id": legacy["id"]})

    commitments = _ids(sdk._tortoise_fts_query(
        "current commitments", entity_type="object", limit=50))
    ended = _ids(sdk._tortoise_fts_query(
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
    rows = sdk._tortoise_fts_query("current commitments", entity_type="object",
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
    everything = sdk._tortoise_fts_query("current commitments", entity_type="object",
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
    rows = sdk._tortoise_fts_query("current commitments", entity_type="object",
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
        sdk._tortoise_fts_query("current commitments", entity_type="object",
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
    sdk._tortoise_fts_query("current commitments", entity_type="object",
                           min_confidence=0.99, limit=5)
    assert reached == [], (
        "the commitment population was served to a caller who asked for a "
        "relevance read with `min_confidence` — the floor would be silently "
        "dropped")

    # NO KNOB → the population, with no scope argument from the caller.
    sdk._tortoise_fts_query("current commitments", entity_type="object", limit=5)
    assert reached, "the plain commitment question did not reach the population"


def test_the_ended_scope_still_refuses_an_explicit_query(sdk):
    """The caller-facing contract is unchanged: an EXPLICIT scope + query reds.

    Only a query the read itself RESOLVED into the scope is exempt (it selected
    the population); a caller who sends one beside ``ended_scope=True`` is asking
    for two different reads at once.
    """
    with pytest.raises(ValueError) as err:
        sdk._tortoise_fts_query("anything withdrawn", entity_type="point",
                               ended_scope=True)
    assert "ended_scope" in str(err.value)
