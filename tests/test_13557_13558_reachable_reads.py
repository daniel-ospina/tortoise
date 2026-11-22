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

import ast
import asyncio
import inspect
import os
import sys
import textwrap
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
    # ⛔ …and the PASSIVE PERFECT WITH AN ADVERB between the auxiliary and the participle
    # (re-cut review, P1, measured). All four routed on `main` (`ended`) and were SILENT on this
    # branch — `population=None, signal=None` — which is #14033's own defect reintroduced by a
    # guard fixing a different spelling. The window now carries the adverb slot; the round-7
    # ACTIVE reading (`have any customers ever cancelled`, no `been`) stays refused, and the
    # `already`-as-AUXILIARY spelling is pinned in NOT_SELECTED as a stated loss.
    ("have any claims been already retracted", "ended", "existence"),
    ("have any claims been ever retracted", "ended", "existence"),
    ("has anything been already withdrawn", "ended", "existence"),
    ("have any of our bookings been already cancelled", "ended", "existence"),
    # …and the RELATIVE CLAUSE on the head, which the round-14 relativizer guard must
    # NOT refuse: `that` directly after the quantifier modifies it, so this is the
    # window's DOCUMENTED legitimate content (`state_intent.py:176`) and the question
    # really does ask about the ended population. Pinned HERE because the round-14 fix
    # added a relativizer lookahead, and a blanket widening of it (adding
    # `that|which|…` to the subordinator alternation instead of requiring a preceding
    # word) costs this row — measured: widened that way the row returns
    # `population=None, signal='existence_unguarded'`.
    ("has anything that the board approved been superseded", "ended", "existence"),
    # …and a relative clause whose OWN verb is `<aux> <adverb> been <other participle>`
    # must keep routing (round-24 review, P2): the adverb slot without a `been <ENDING>`
    # anchor refused `has anything the board has recently been briefed on been withdrawn`,
    # which routed before it. The anchor is the same one the pronoun branch uses.
    ("has anything the board has recently been briefed on been withdrawn", "ended", "existence"),
    # ⛔ …AND A MODAL `do` IS NOT A REASON TO REFUSE (round-5 review, P3, measured, and it is the
    # COVERAGE half of a round-26 fix): `do|does|did` were removed from the two lookaheads because
    # they cannot precede `been` and their only effect was to let an ordinary verb fill the noun
    # slot (`has anything we said we would do been cancelled` refused as `noun='would' aux='do'`).
    # The removal was right and NOTHING PINNED IT: restoring `did|does|do` left the whole suite
    # green while these six rows flipped to refused. Pinned here so the deletion is asserted, which
    # is the same defect class the relativizer lookahead already has a dedicated row for.
    ("has anything we said we would do been cancelled", "ended", "existence"),
    ("have any of the things we said we would do been withdrawn", "ended", "existence"),
    # …AND THE PRONOUN BRANCH OF THAT SAME DELETION NEEDS ITS OWN ROWS (round-6 review, P3,
    # measured): the two rows above are refused by the THIRD lookahead, so restoring
    # `did|does|do` in the FOURTH (pronoun) lookahead alone left the suite GREEN — a coverage
    # hole with reachable consequences (`has anything they did, in fact, been archived` and its
    # twins refuse under that mutant). These rows have a pronoun subject, so only the fourth
    # lookahead can refuse them, and they red when `do|does|did` is restored there.
    ("has anything they did, in fact, been archived", "ended", "existence"),
    ("has anything we did of late been withdrawn", "ended", "existence"),
    ("has anything you did to date been cancelled", "ended", "existence"),
    # ⛔ …AND THE OVER-REFUSAL THE `{0,8}` BOUND CAUSED, PINNED AS ROUTING (round-6 review, P2,
    # measured — this lane's own regression): a RELATIVE clause whose own participle is an ENDING
    # word was refused at the widened bound while it routed on `main` and on the pre-change head,
    # and only the TOKEN COUNT separated it from the asyndetic hijack the widening exists for.
    # The two lookaheads now carry `(?!\s*(?:been|by)\b)` — the ending belongs to the relative
    # clause when the clause continues or takes an agent — so these three route again while their
    # asyndetic twins stay refused (NOT_SELECTED). Pin BOTH directions: this list is the half that
    # reds if the tail-negative is dropped.
    ("has anything that has been only just very recently deprecated been withdrawn",
     "ended", "existence"),
    ("has anything that has been in fact only just superseded been withdrawn", "ended", "existence"),
    # ⛔ …AND THE AGENT ARM IS WITHDRAWN, WITH ITS COST PINNED (round-7 review, P2, measured): the
    # negative below tolerates ONLY `been`, because tolerating `by` made the guard blind to the
    # asyndetic hijack it exists for — `have any users reported it has been deprecated by legal been
    # withdrawn` returned `ended` with `signal=None`. So a relative clause whose own participle takes
    # an agent is REFUSED AND REPORTED (see NOT_SELECTED / NOTICE_ONLY) rather than routed: a stated
    # recall loss in the module's preferred direction, not a silent drop.
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
    # ── #14033: the phrasings a customer actually uses, which this table used to
    # name as RECALL LOSSES in the module docstring. Each one is still a
    # PREDICATE anchored to its grammatical role — that rule is what the hijack
    # fix bought and it is not what widened.
    #
    # A trailing RECENCY adverb, which names no window the population would have
    # to apply: every returned row carries its own `endedAt`, so nothing is
    # dropped (`in Q3`, `by legal`, `last quarter` still are, and now REPORT
    # themselves — see `test_a_strong_cue_that_does_not_fire_reports_itself`).
    ("what has ended recently", "ended", "wh_whole_question"),
    ("what ended lately", "ended", "wh_whole_question"),
    ("what has been withdrawn so far", "ended", "wh_whole_question"),
    # A YES/NO question whose own SUBJECT is the record asked about: the copula
    # opens the question and the participle ends it, so the subject cannot be a
    # subordinate clause's (that is what refuses `what happens when an order is
    # cancelled`) and the ending word cannot be attributive (that is what refuses
    # `is the archived pricing document live`).
    ("is the onboarding flow retired?", "ended", "is_copula"),
    ("is the onboarding flow retired", "ended", "is_copula"),
    # …and a subject word that carries DIGITS. The first subject class was `[a-z]…`,
    # so `v3` / `SOC2` / `2024` were unrepresentable and this question was both
    # unrouted and unreported (the module's own example of a version-qualified name).
    ("is the v3 export deprecated?", "ended", "is_copula"),
    ("is our onboarding flow already retired", "ended", "is_copula"),
    # ── the BARE-PRONOUN subject, and the recall the round-18 P1 guard KEEPS ──
    # (round-18 review, P1.) Making a pronoun legal only as the WHOLE subject is the
    # fix that closes the reduced-relative and extraposed classes; these three rows are
    # what that rule must NOT cost. `is it archived` is the pronoun branch itself,
    # `is it really retired` proves the prefix's ADVERB SLOT still carries the time /
    # degree adverbs a caller actually writes, and `is the export still archived` proves
    # the same slot applies to a noun-phrase subject. Measured at the pre-fix head: all
    # three routed, and all three still route — a guard that bought the P1 class by
    # refusing these would be a recall loss with no finding behind it.
    ("is it archived", "ended", "is_copula"),
    ("is it really retired", "ended", "is_copula"),
    ("is the export still archived", "ended", "is_copula"),
    ("has it been archived", "ended", "is_copula"),
    ("has the partner call been cancelled", "ended", "is_copula"),
    # An IMPERATIVE over a FREE RELATIVE CLAUSE — a request for a SET. ⛔ The
    # discriminator against `show me the retired onboarding flow` (which stays a
    # NOT_SELECTED row, because it asks for ONE record and its object is a noun
    # phrase) is the clause, not the imperative frame.
    ("list what we retired", "ended", "imperative_what"),
    ("show me what we archived", "ended", "imperative_what"),
    ("enumerate what we have cancelled", "ended", "imperative_what"),
    # ── DETERMINER STACKING and COMPOUND-WORD subjects on `is_copula` (round-18
    # review, P2 — the first cut of round-17's determiner lookahead broke these) ──
    # `all|both|half|such` are PRE-determiners, so `all the` is ONE noun phrase; and a
    # hyphen/slash is not a word boundary for this rule, so `how-to`, `what-if`,
    # `all-hands` and `a/b` are single words. Both were measured SILENT regressions
    # (routed before round-17, `population=None, signal=None` after it), and each row
    # here is the spelling that broke. The perfect branch is pinned separately because
    # it takes the same subject expression.
    ("is all the data archived", "ended", "is_copula"),
    ("is all our old plans retired", "ended", "is_copula"),
    ("is half the exports archived", "ended", "is_copula"),
    ("is such a report archived", "ended", "is_copula"),
    ("is both the reports archived", "ended", "is_copula"),
    ("has all the data been archived", "ended", "is_copula"),
    ("is the how-to guide archived", "ended", "is_copula"),
    ("is the what-if scenario archived", "ended", "is_copula"),
    ("is our all-hands meeting cancelled", "ended", "is_copula"),
    ("is the a/b test cancelled", "ended", "is_copula"),
    # …and the PRE-EXISTING twin the same `(?![-/])` repairs on an older token, which
    # is why it is pinned here rather than left as a comment: it was refused BEFORE
    # round 17 and nothing reddened then.
    ("is the when-in-doubt policy archived", "ended", "is_copula"),
    ("has any how-to guide been archived", "ended", "existence"),
    # ── the `of`-PARTITIVE (round-19 review, P2) ─────────────────────────────
    # One noun phrase (`one of the exports`), routed before round 17, refused with no
    # notice after it. The determiner guard now fires only when the word DIRECTLY before
    # the determiner is neither a pre-determiner nor `of`, which is what distinguishes
    # `is one of the exports archived` from `is the report the board archived`.
    ("is one of the exports archived", "ended", "is_copula"),
    ("is one of our plans retired", "ended", "is_copula"),
    ("is none of the reports archived", "ended", "is_copula"),
    ("is neither of the reports archived", "ended", "is_copula"),
    ("is either of the plans retired", "ended", "is_copula"),
    ("is part of the data archived", "ended", "is_copula"),
    ("has none of the data been archived", "ended", "is_copula"),
    ("is all of the data archived", "ended", "is_copula"),
    ("is most of our reports archived", "ended", "is_copula"),
    # …and the `-ed` ADJECTIVE modifier the new determiner-less anchor must NOT refuse:
    # the guard fires only when the `-ed` word is IMMEDIATELY followed by the ending
    # participle, so an adjective modifier of the subject's own head noun still routes.
    # This is the recall the anchor costs, kept and pinned rather than assumed.
    ("is the deprecated export archived", "ended", "is_copula"),
    # ── the STATED boundary: a determiner-less second NP ROUTES (round-21 review, P2) ──
    # Pinned as a ROUTING row on purpose. The `-ed` anchor that refused it also silently
    # refused every `-ed`-ending head noun, so it was withdrawn; this row is what the
    # module now does, and the prose on `_COPULA_SUBJECT` states it. A later lane that
    # closes this class changes these rows deliberately, not by accident.
    ("is the export customers requested archived", "ended", "is_copula"),
    # ── …and the class the withdrawn anchor had silently broken (round-21 review, P1) ──
    # `-ed`-ending HEAD NOUNS and DEGREE WORDS inside the subject all routed at
    # `e84612d43f` (the revision BEFORE round 20) and returned `population=None,
    # signal=None` after round 20. Both are pinned here so the next widening reds instead
    # of costing them quietly. ⛔ THE BASELINE IS `e84612d43f` — ABSOLUTE, NEVER RELATIVE: a
    # relative rev is stable only in the working copy it was drafted in, and the first cut of
    # this note said "HEAD~1", which by the time it was read had become round 21 (round-23
    # review, P3). Round 20 is `6a301c4647` (the commit that refuses them), round 21 is
    # `a6225cbad1`, and the rows route at `e84612d43f`.
    ("is the feed archived", "ended", "is_copula"),
    ("is the seed archived", "ended", "is_copula"),
    ("is the migration seed archived", "ended", "is_copula"),
    ("is the deprecated feed archived", "ended", "is_copula"),
    ("is the most recent export archived", "ended", "is_copula"),
    ("is the most recent report deprecated", "ended", "is_copula"),
    ("is our most recent plan retired", "ended", "is_copula"),
    ("is the data all archived", "ended", "is_copula"),
    ("is our report all archived", "ended", "is_copula"),
    # ── and the DEGREE/NEGATION-adverb tail (round-22 review, P2) ────────────────
    # A determiner-vocabulary word followed by `no longer` / `any longer` / `all but` /
    # `most recent` is still ONE noun phrase, not a second NP. Without position rule (c)
    # these were SILENT while the semantically identical `is the export not archived`
    # ROUTED — an internally inconsistent pair with no notice on the refused half.
    ("is the export no longer archived", "ended", "is_copula"),
    ("is our plan no longer retired", "ended", "is_copula"),
    ("is the report any longer archived", "ended", "is_copula"),
    ("is the invoice no longer cancelled", "ended", "is_copula"),
    ("is the export all but archived", "ended", "is_copula"),
    ("is the export most recent archived", "ended", "is_copula"),
    # ── …and the PERFECT branch's post-nominal quantifier (round-23 review, P2) ────
    # `has the data all BEEN archived` puts `been` between the quantifier and the
    # participle, so rule (b) never reached it: 77/77 focused probes were SILENT while both
    # flat twins (`has all the data been archived`, `has the data been archived`) ROUTED and
    # are pinned as routing — and BOTH really are, round-24 review's P3: this comment
    # promised two pins while only `has all the data been archived` had one, so the bare
    # twin below is pinned HERE, as the row the `(?:been\s+)?` widening exists for.
    # The lookahead now accepts `been` + the participle too — the same position one
    # auxiliary later.
    ("has the data been archived", "ended", "is_copula"),
    ("has the data all been archived", "ended", "is_copula"),
    ("has the reports both been archived", "ended", "is_copula"),
    ("have the claims each been withdrawn", "ended", "is_copula"),
    # ── a PREPOSITIONAL `to` IS NOT AN INFINITIVE (review P1 at `42e0c1a662`) ───────
    # The infinitive exclusion (`_COPULA_INFINITIVE_REFUSAL`) names `to be` — a PHRASE, not
    # the word `to` — so a `to` that introduces a noun keeps routing. Pinned because the
    # cheap wrong fix is `to`, which would have killed every `is the export to Europe
    # archived` question on this shape.
    ("is the export to Europe archived", "ended", "is_copula"),
    # ── …and a CONTRACTED head auxiliary (round-4 review, P2, measured) ─────────────
    # `isn't the export archived` / `hasn't the export been archived` matched NO shape and NO
    # signal — the head was spelled `is|are|was|were` with nothing between the verb and the
    # space, and `\b` cannot match after `is` inside `isn't` — while their uncontracted twins
    # `is the export not archived` / `is the export archived` route. A negative question is
    # still a question about a state, so the two spellings must not differ.
    ("isn't the export archived", "ended", "is_copula"),
    ("hasn't the export been archived", "ended", "is_copula"),
    ("isn’t the export archived", "ended", "is_copula"),
    ("aren't the exports archived", "ended", "is_copula"),
    # ── the INFINITIVE exclusion belongs to the COPULA BRANCH ONLY (review P1, measured) ──
    # It sits in `_COPULA_INFINITIVE_REFUSAL`, applied to the `is|are|was|were` alternative
    # and NOT to `_COPULA_SUBJECT`, because that guard is shared with the perfect branch.
    # In `has … been <participle>` the predicate is already anchored, so a `to be <participle>`
    # inside the subject is a POST-NOMINAL MODIFIER and the question is legitimate. When the
    # exclusion lived in the shared guard all four rows below went `None / None` — routed at
    # the previous head, then SILENT, with `missed_route_payload` None because
    # `_COPULA_LONG_SUBJECT` is anchored `^(?:is|are|was|were)`. That is the routed->silent
    # regression this PR exists to remove, so each spelling is pinned as a ROUTE.
    ("has the contract to be signed been archived", "ended", "is_copula"),
    ("has the report to be reviewed been archived", "ended", "is_copula"),
    ("has the decision to be taken been archived", "ended", "is_copula"),
    ("has the report being reviewed been archived", "ended", "is_copula"),
    # ── A POSSESSIVE SUBJECT IS NOT AN AUXILIARY CLITIC (round-3 review, P1, measured) ──
    # The clitic exclusion in `_COPULA_SUBJECT` first refused ANY subject word ending in
    # `'s|'ve|'d`, which cannot tell the auxiliary clitic (`billing's been deprecated`) from a
    # POSSESSIVE DETERMINER (`the client's contract archived`) — so every possessive-subject
    # question was refused with NO notice, against this branch's own base:
    #   base `21cc92e90c`: copula 96/96 routed → head 0/96 (all silent); perfect 72/72 → 0/72.
    # It was not even self-consistent: the PLURAL possessive still routed, because there the
    # apostrophe is word-final.
    # ⛔ THE MECHANISM, DESCRIBED AS THE CODE IMPLEMENTS IT: the exclusion is anchored on the
    # FOLLOWING `been` — `(?!…['’](?:s|ve|d)\s+been\b)` — i.e. it refuses only the AUXILIARY
    # reading (`billing's been deprecated`) and ADMITS every other `'s|'ve|'d` as a possessive.
    # An earlier cut of THIS comment claimed a broader discriminator (a noun after a possessive,
    # a predicate list `been|not|already|ever|…|ENDING` after the auxiliary) — that was the
    # design I had built and then DROPPED in favour of the branch's own fix, so the comment was
    # describing a mechanism that is not in the file. Measured against the code: `is the plan's
    # not archived` and `is the plan's already archived` still ROUTE, because only `been` is
    # anchored. These rows are pinned as ROUTES so a future widening cannot silently re-drop them.
    ("is the client's contract archived", "ended", "is_copula"),
    ("is the client’s contract archived", "ended", "is_copula"),
    ("is the clients' contract archived", "ended", "is_copula"),
    ("has the client's contract been archived", "ended", "is_copula"),
    ("has the vendor's invoice been retired", "ended", "is_copula"),
    # !! THE CLITIC ANCHOR'S HYPHEN TRAP (round-10 review, P3, measured): the exclusion is
    # anchored on `been\b`, and `\b` matches BEFORE a hyphen -- so a genuine possessive
    # followed by a `been-...` compound was refused with NO notice, while its plain twin
    # routed. The module's own idiom for a token that must END its word is `(?![-/])\b`,
    # used by `_EXISTENCE_GUARD` for exactly this reason. These two route.
    ("is the vendor's been-signed contract archived", "ended", "is_copula"),
    ("is the client's been-archived contract archived", "ended", "is_copula"),
    # ⛔ AN ATTRIBUTIVE `-ing` WORD IS A NOUN-MODIFIER, NOT A PREDICATE (round-11 review, P2,
    # measured). The `-ing` widening refused the non-finite word at ANY position in the
    # subject, so `is the remaining invoice archived` went `ended` -> `None`/`signal=None` —
    # no route, no notice — for 120 of 120 rows (and 30/30 each for `keeping`/`staying`/
    # `becoming`). A route -> silence move is the one direction that widening must not take.
    # The refusal is frame-aware now: the non-finite verb must be FOLLOWED (within two
    # adverbs) by an ending word. These four rows FAIL on `8cbe45d646` (all `None`/silent)
    # and ROUTE here.
    ("is the remaining invoice archived", "ended", "is_copula"),
    ("is our remaining budget archived", "ended", "is_copula"),
    ("is the keeping invoice archived", "ended", "is_copula"),
    ("is the staying contract retired", "ended", "is_copula"),
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
    "have any users reported the vendor hasn’t been deprecated",
    # ⛔ THE REFUSAL-SIDE SLOT'S ROWS (round-3 review, P1, measured): members of the guard's own
    # adverb class (`just`, `only`, and the multi-word `no longer`/`to date`) were not in the
    # lookaheads' slot, so they blinded both and these ROUTED the whole ended population with the
    # caller's predicate dropped. They live here because this list is what the parametrization
    # READS; `NOTICE_ONLY` re-states the ones that carry a notice.
    "have any users reported it has been just deprecated and been withdrawn",
    "have any users reported it has been only deprecated and been withdrawn",
    "have any users reported it has been no longer deprecated and been withdrawn",
    "have any users reported it has been to date deprecated and been withdrawn",
    # …added by the `NOTICE_ONLY ⊆ NOT_SELECTED` invariant: these three report the
    # notice, so they belong in BOTH lists — a `NOTICE_ONLY` row that no
    # parametrization reads asserts nothing at all.
    "has anyone complained billing has been recently deprecated",
    "have any customers noticed they have been already cancelled",
    "have any users reported it has been already deprecated",
    # ⛔ THE BOUND, NOT THE CLASS (round-5 review, P2, measured): the refusal slot carried the whole
    # adverb class but was bounded to THREE tokens while every multi-word member (`in fact`,
    # `just about`, `no longer`, `to date`) is TWO — so two of them in a row needed four and slipped
    # through, routing the whole ended population with the caller's predicate dropped. Both rows
    # below are the same sentence as the four above, only longer, and their 3-token twin
    # (`only just very`) was already refused — the length was the only discriminator. They report
    # through `existence_unguarded`, so they belong in BOTH lists.
    "have any reported it has been in fact only just deprecated and been withdrawn",
    "have any reported it has been in fact just about deprecated and been withdrawn",
    # ⛔ AND THE NOTICE MUST SEE THE SAME CLASS THE GUARD REFUSES ON (round-5 review, P3,
    # measured): `existence_unguarded` still matched the WINDOW's narrow recall slot, so the
    # `recently` spelling reported while its `just`/`only` twins — refused by the SAME guard clause
    # — were SILENT. The rows above did not catch it because a trailing `been <ending>` clause
    # supplied their notice; these are the bare frames, and they red without this pin.
    "has anyone complained billing has been just deprecated",
    "has anyone complained billing has been only deprecated",
    # ⛔ …AND THE CLITIC-SUBJECT COPULA FRAME, WHICH WAS SILENT AT BOTH ENDS (round-8 review, P2,
    # measured): the copula/perfect shape refuses a subject carrying an auxiliary clitic on
    # purpose, and the comment above says the row then "falls through to `_ENDING_SIGNALS` … and
    # reports" — true only when the question ALSO carries an existential head. With a bare copula
    # head there was nothing to fall through to, so `has the billing's been deprecated` returned
    # NOTHING AT ALL while its parent routed it `ended`. All 8 determiners x 4 nouns were silent;
    # these two rows pin the class in both directions (population None AND a notice).
    "has the billing's been deprecated",
    "has v3's been deprecated",
    # …and the ADVERB twins, which the narrow slot blinded (round-9 review, P2, measured): the
    # first cut of that arm used the WINDOW's narrow set, so these were still refused SILENTLY.
    "has the billing's been just deprecated",
    "has the billing's been only just deprecated",
    # ⛔ …AND THE AGENT-PHRASE HIJACK, WHICH THE `by` TOLERANCE RE-OPENED (round-7 review, P2,
    # measured — this lane's regression, found by a fresh reviewer within the hour): with `by`
    # tolerated, both lookaheads went blind whenever the reported clause's participle took an
    # agent, and these ROUTED the whole ended population with the caller's predicate dropped and
    # `signal=None`. Refused and reported now; both subject classes and the clitic twin are pinned.
    "have any users reported it has been deprecated by legal been withdrawn",
    "have any users reported the vendor has been deprecated by legal been withdrawn",
    "have any users reported the vendor has been deprecated by legal and been withdrawn",
    "has anyone complained billing has been deprecated by legal been withdrawn",
    # …and the RELATIVE row the withdrawn `by` arm used to route — refused AND REPORTED (a stated
    # recall loss: a token-level slot cannot see the relativizer that separates it from the
    # asyndetic twin above).
    "has anything that has been in fact only just superseded by legal been withdrawn",
    # ⛔ THE `<preposition> the <noun>` QUALIFIER class on `is_copula`, GIVEN UP by the re-cut
    # review's P1 fix (measured): `as`/`than`/`during`/`until` also head a CLAUSE, so exempting
    # them from the second-NP refusal routed `is the report as the board archived` to the WHOLE
    # ended population with no notice. The exemption therefore carries only prepositions that do
    # not head a clause, and these four spellings are silent — `main`'s behaviour, stated and
    # pinned rather than claimed as coverage. Open on #14033 with the rest of the class.
    "is the export archived during the migration",
    "is the export archived until the merger",
    "is the export archived as the old policy",
    "is the export archived than the old sharepoint",
    # …and the spelling the adverb slot above still gives up: `already` as the AUXILIARY with
    # no `been` leaves the participle ATTRIBUTIVE (`reports already archived` = a noun phrase),
    # and admitting that adverb back is the round-7 ACTIVE reading two lines down. `main` routed
    # it, so this is a stated recall loss — and it REPORTS (`existence_unguarded`, turn-3 P2),
    # because a stated recall loss that is silent is #14033 duty (b) itself. See `NOTICE_ONLY`.
    "have any reports already archived",
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
    # ── the CLAUSE-IN-THE-EXISTENCE-WINDOW class (#14033 round-8 review, P1) ─
    # `existence`'s window is `[^?]{0,60}?`, which admits any clause, not only a
    # relative one — so an ending participle in a SUBORDINATE clause routed and the
    # caller's own predicate was dropped. Each of these was measured ROUTED (and, on
    # the read, served the whole ended population). They now report instead.
    "have any customers complained since the old plan has been cancelled",
    "have any users complained because the feature has been deprecated",
    "has anything changed since the old pricing has been deprecated",
    "have any customers churned after the tier has been deprecated",
    # …and the ASYNDETIC class that lookahead DOES protect (#14033 round-9 review,
    # P2): a second clause with its own subject and its own `has been`, no
    # subordinator to catch it. Measured: each ROUTED (whole ended population, the
    # caller's observation dropped) once the lookahead was removed wholesale, which
    # is why the remedy narrows it rather than deleting it. Pinned so a later
    # "simplification" cannot let them route again.
    "have any users reported the vendor has been deprecated",
    "have any customers said the feature has been deprecated",
    # …and the MODAL PASSIVE of the same class, which the first cut of the narrowing still
    # let through (measured on `0bde9536`: whole ended population served, the caller's own
    # `noticed` dropped). The guard now requires `been` after the finite verb rather than a
    # closed list of auxiliaries, so `will have been` is caught like `has been` — the same
    # class one auxiliary deeper, and no verb list to be one spelling short of.
    "have any users noticed the vendor will have been deprecated",
    # ── the VERB-AND-OBJECT class on the attributive `which` row ─────────────
    # `which cancelled the deal` puts a finite past-tense verb with its own object
    # where a MODIFIER should be; the row now refuses a determiner after the
    # participle, so these are neither routed nor reported.
    "which cancelled the deal",
    "which ended the argument",
    "which retired the old process",
    # …and a coordinated SUBJECT over the copula bound: reported (it is a plain long
    # noun phrase), never routed.
    "is the SOC2 audit and its evidence package archived",
    "is the onboarding flow and its partner variants retired",
    # ── the ACTIVE-VOICE class on the perfect branch (#14033 round-7 review, P1)
    # `already` / `ever` in place of `been` is an ACTIVE third-party action, not a
    # state of the record: each of these routed to the whole ended population,
    # measured on the read (the ended row served, the live row not). The `existence`
    # shape had the same hole one quantifier away — pinned too, because it is the
    # same active-versus-passive discriminator.
    "has the partner already cancelled",
    "has legal already retracted",
    "has the customer ever cancelled",
    "have any customers ever cancelled",
    # ── the PRONOUN SUBJECT on the asyndetic class (#14033 round-14 review, P2) ──
    # The guard's subject slot was `<determiner> <noun>`, which a pronoun has neither
    # of, so each of these ROUTED to the whole ended population and dropped the
    # caller's own predicate (`reported`, `noticed`, `said`) — measured at
    # `248713d6f4` on the default surfaces. The guard now has a pronoun alternative,
    # so they are refused (and reported — see NOTICE_ONLY).
    "have any users reported it has been deprecated",
    "have any customers noticed they have been cancelled",
    "have any users said it will have been deprecated",
    # ── and the VERB-WITH-OBJECT class on the attributive `which` row ───────────
    # The determiner lookahead is a CLOSED list that stopped at `the|a|an|this|…`, so
    # `all|both|each|one|two|first|last` … still carried the notice this row exists to
    # refuse. Measured at `248713d6f4`; the class is extended, so these are silent.
    "which retired all the processes",
    "which cancelled both deals",
    # ── the YES/NO CO-PREDICATE class (#14033 round-6 review, P0) ──────────
    # The `is_copula` shape's first cut reused `existence`'s free 60-character
    # window for its SUBJECT, so an ending participle belonging to a SUBORDINATE
    # clause routed — the round-3 class, left open because the question shape was
    # `is …`. Every string below was measured ROUTING to the ended population, on
    # the default SDK / MCP / hosted surfaces, before the subject was bounded to a
    # contiguous noun phrase (optional determiner + ONE to FOUR words).
    "is the export broken because the schema was deprecated",
    "is the revenue down because the launch was cancelled",
    "is it a problem that the endpoint is deprecated",
    "is it true that our claims were archived",
    "is the report accurate given the data was archived",
    "is our pricing page up to date since the tier was retired",
    "is the vendor still engaged now the contract is archived",
    "is there any chance the release was cancelled",
    # …and the recall the bounded subject gives up, stated rather than discovered:
    # a modifier clause INSIDE the subject keeps the relevance read (the module
    # docstring names this one as its example).
    "is the onboarding flow, as agreed in March, retired",
    # …a subject LONGER than the four-word bound: unrouted (the bound is where a
    # clause can hide) and REPORTED — silence here was round-7's P1-B.
    "is the onboarding flow for enterprise partners retired",
    # …a `which` head noun carrying a MODIFIER: the stated recall loss, now reported.
    "which customer claims were archived",
    # …and boundary-free adverbs outside the admitted recency list: REPORTED, because
    # their first word is a `_BLOCKED_TAIL` member (`_RECENCY_TAIL` admits four
    # spellings; the closed tail list reports the ones it names a leading word for —
    # `most recently` stands outside it and is silent, which that note states).
    "what ended previously",
    "which claims were retracted earlier",
    "list what we retired previously",
    "is the onboarding flow retired anymore",
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
    # …`what has ended recently` USED TO SIT HERE as a stated recall loss and is
    # now a SELECTED row (#14033): a recency adverb names no window, and the rows
    # carry `endedAt`. The CONSTRAINT rows below still do not route — they report
    # themselves instead (see `test_a_strong_cue_that_does_not_fire_reports_itself`).
    "what has ended last quarter",
    # …and the ATTRIBUTIVE use in the copula frame: the ending word modifies the
    # subject noun and the question's predicate is the word after it, so it is a
    # content lookup and neither routes nor reports a cue (`_BLOCKED_TAIL`).
    "is the archived pricing document live",
    "is our deprecated endpoint still served",
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
    # ── the COMPLEMENTIZER class on `existence` (round-14 review, P1) ────────
    # The subordinator list named `because`/`since`/`when`/… but not `whether`,
    # `once`, `whereas`, and guarded the relativizers only via the asyndetic
    # lookahead — so these three ROUTED to the whole ended population with the
    # caller's own predicate (`asked`, `said`, `noted`) silently dropped. Measured
    # at the pre-fix head: all three `population='ended', shape='existence'`. They are
    # pinned in BOTH lists — NOT_SELECTED asserts the population stayed None, and
    # NOTICE_ONLY (below) asserts the notice is emitted — because the cue is real
    # (`has … been <ended>`) and only the clause guard refuses it, so the read must
    # report rather than go silent.
    "has anyone asked whether the old plan has been archived",
    "has anyone said that our launch plan has been withdrawn",
    "has anyone noted which endpoint has been deprecated",
    # …and the FREE-RELATIVE spellings the list still omitted (round-17 review, P2):
    # `why`/`how` are complements exactly like `whether`, and neither was a member, so
    # both ROUTED. The one-word-NP sibling (`… why the data has been archived`) was
    # caught by the determiner+noun+active-verb+`been` lookahead instead, which is why
    # nothing reddened — the hole was invisible to every pinned row and open only on a
    # MULTI-word noun phrase. Pinned in BOTH lists: the cue is real, the clause guard
    # refuses it, so it reports as `existence_unguarded`.
    "has anyone asked why the old plan has been archived",
    "has anyone asked how the old plan has been archived",
    # …and the `is_copula` twin of the same defect (round-14 review, P3): a
    # subordinator INSIDE the four-word subject bound, which the stated justification
    # claimed could not fit. The question's predicate is the relative clause's
    # `archived`, not a state the caller asked about. It is pinned in NOT_SELECTED and
    # NOT in NOTICE_ONLY on purpose: the copula signal row's own lookahead already
    # refuses to report a subject that carries a subordinator (`_COPULA_LONG_SUBJECT`),
    # so this row's remedy is the unchanged relevance read and nothing else.
    "is the plan that the board archived",
    # ── the FREE-RELATIVE and REDUCED-RELATIVE class on `is_copula` (round-17
    # review, P1) ──────────────────────────────────────────────────────────
    # The subordinator list did not name `what`, and no member is needed for a REDUCED
    # relative, so a subject holding either routed. Measured at the pre-fix head: all
    # four `population='ended', shape='is_copula', signal=None` — the caller's own
    # predicate dropped AND no notice, which is the #14033 defect itself. The guard now
    # adds the free-relative pronouns to the list and refuses a DETERMINER AFTER A WORD
    # (a second noun phrase, which is what a reduced relative needs). Pinned in
    # NOT_SELECTED and NOT in NOTICE_ONLY on purpose: measured `signal is None` for all
    # four, so the remedy is the unchanged relevance read — the recall given up is
    # stated on `_COPULA_SUBJECT` rather than discovered here.
    "is that what the board archived",
    "is it what we archived",
    "is that the endpoint we deprecated",
    "is the report the board archived",
    # ── the BARE-NOUN SUBJECT class (round-16 review, P1) ────────────────────
    # The asyndetic lookahead required `<determiner> <noun>` before the finite verb, so
    # every subject spelled without an article still ROUTED and dropped the caller's own
    # predicate — the pinned twin `have any users reported the vendor has been
    # deprecated` was refused and these were not, differing only by `the`. The
    # determiner is optional now and the PRONOUNS are excluded instead (a pronoun
    # subject is the relative clause's, which is why
    # `has anything we have been tracking been withdrawn` still routes). The last row is
    # the same clause reaching the `is_copula` perfect branch, whose subject carried its
    # own auxiliary; that branch's guard now refuses a nested auxiliary too.
    "have any users reported v3 has been deprecated",
    "has anyone complained billing has been deprecated",
    "have any users reported the search index has been deprecated",
    # ── the ADVERB insertion between the second clause's auxiliary and `been`
    # (round-23 review, P1, measured) ─────────────────────────────────────────
    # The asyndetic lookahead required `aux been` ADJACENT, so every
    # `<verb> <subject> has <adverb> been <participle>` spelling slipped through and
    # the window fired on the second clause's participle: measured 294 of 294 over
    # `{reported,noticed,said,heard,complained,claimed,confirmed} x <subject> x
    # {recently,just,already,finally,quietly,officially} has __ been deprecated` —
    # the whole ended population, the caller's predicate dropped, and NO notice.
    # English inserts adverbs exactly there, so the guard now carries an adverb slot
    # before and after an optional `have` (modal passives, `only just`).
    "have any users reported the vendor has recently been deprecated",
    "have any users reported it has recently been deprecated",
    "have any users reported the vendor will have recently been deprecated",
    "have any users reported the vendor has only just been deprecated",
    # …and the NON-`-ly` adverbials and the 4-deep stack (round-24 review, P1): the
    # `-ly` class is not the adverb class, so `very recently` / `just about` / `in fact`
    # slipped the first slot and `only just very recently` exceeded its {0,3} bound.
    # Measured: 291 of 588 rounded off the 7x7x12 matrix before the widening.
    "have any users reported the vendor has very recently been deprecated",
    "have any users reported the vendor has only just very recently been deprecated",
    # …and the non-`-ly` / positional-negation / two-word spellings (round-25 review, P1):
    # 12 of 12 adverbials, `still not` and `recently not` all routed before this widening.
    "have any users reported the vendor has often been deprecated",
    "have any users reported the vendor has still not been deprecated",
    "have any users reported the vendor has no longer been deprecated",
    "have any users reported the vendor has, in fact, recently been deprecated",
    # ── and the SILENT direction of the notice's tail (round-16 review, P2) ──
    # `existence_unguarded` carried no tail guard, so ANY tail reported — falsifying the
    # two paragraphs that say a qualifier outside the closed tail list stays silent. The
    # signal now carries the same `_BLOCKED_TAIL` the shapes do, and this row is pinned
    # HERE and NOT in `NOTICE_ONLY` because "silent" is the claim being made.
    "has anything we relied on been withdrawn most recently",
    # ── the PRONOUN-SUBJECT class on `is_copula` (round-18 review, P1) ─────────
    # Round 17's two guards (a subordinator inside the subject; a second determiner
    # after a word) miss two shapes: a REDUCED RELATIVE whose subject is a pronoun
    # needs no second determiner, and an EXTRAPOSED frame (`is it <adj> <subject>
    # <participle>`) has no determiner at all. Measured at the pre-fix head: every row
    # below returned `population='ended', shape='is_copula', signal=None` — the whole
    # ended population served with the caller's own predicate dropped and no notice.
    # `is_copula` is NEW in this PR (`git grep -c is_copula <merge-base> --
    # tortoise/state_intent.py` → 0), so on `main` these are ordinary relevance reads:
    # the class is this PR's own regression.
    # ⛔ THE GUARD IS A ROLE, NOT A WORD LIST: a subject pronoun may only BE the
    # subject (a bare-pronoun branch), never sit inside a longer noun phrase — which is
    # why the SELECTED rows above (`is it archived`, `is it really retired`) survive.
    # Pinned here and NOT in NOTICE_ONLY on purpose, measured: `signal is None` for
    # every one, so the remedy is the unchanged relevance read.
    "is the plan we archived",
    "is the flow we retired",
    "is the report she archived",
    "is the export we deprecated",
    "is the report they archived",
    "is the plan you archived",
    "is it clear legal retracted",
    "is it true legal retracted",
    "is it obvious legal retracted",
    "is it likely legal retracted",
    "is it certain legal retracted",
    "is it accurate legal retracted",
    "is it possible legal retracted",
    "is it fact legal retracted",
    "is it news legal retracted",
    "is there evidence legal retracted",
    # ── the SAME reduced-relative shape whose subject OPENS with a pre-determiner
    # (round-19 review, P1) ────────────────────────────────────────────────
    # Round-18's exclusion sat inside EVERY tick of the word run, so a subject that
    # opened with `every|all|most|both|some|no` could not start the run and the WHOLE
    # guard was skipped — round-17's class re-opened by its own fix. These route as
    # `ended` on the pre-round-17 module and were measured routing at round 18; they
    # are refused now, and `is all the data the board archived` reports
    # `is_copula_long_subject` while the short ones are silent (the stated recall).
    "is every report the board archived",
    "is all the data the board archived",
    "is most data the board archived",
    "is both reports the board archived",
    "is some report the board archived",
    "is no report the board archived",
    # ── the same class with a POSSESSIVE / PRE-DETERMINER / NO determiner (round-20
    # review, P2) ────────────────────────────────────────────────────────────
    # The prose claimed "a subject whose tail is a second noun phrase is refused", and
    # the closed determiner list was narrower than that claim: `his|her` and the
    # pre-determiners were absent, and a second NP with NO determiner needed a different
    # anchor. All five routed to the whole ended population with `signal=None` before this
    # round. The three long ones report `is_copula_long_subject` (pinned in NOTICE_ONLY
    # below); the other two are SILENT, which is the stated notice-scope limit.
    # ⛔ THE DETERMINER-LESS ROW IS WITHDRAWN FROM THIS LIST (round-21 review, P1,
    # measured): the `-ed` anchor that refused it also refused every subject whose HEAD
    # NOUN ends in `ed` (`feed`, `seed`, `need`, `bed`, `shed`) and was SILENT about it, so
    # the anchor is gone and `is the export customers requested archived` ROUTES — pinned
    # in `SELECTED` below as the STATED boundary, not left to be discovered. Telling a
    # bare noun from a bare verb needs a lexicon this detector does not have.
    "is the report his team archived",
    "is the contract every employee signed archived",
    "is the report each manager reviewed archived",
    "is the dashboard all customers use deprecated",
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
    # …and the recall the start anchor gives up, stated rather than discovered:
    # an IMPERATIVE set request keeps the relevance read, because the measured
    # hijack `show me the retired onboarding flow` is the same imperative frame
    # asking for ONE record.
    "list our commitments",
    "show me all the commitments",
    # …and the INFINITIVE / PROGRESSIVE copula subjects (review P1 at `42e0c1a662`):
    # refused by the subject's second-verb lookahead, never routed. They also carry the
    # missed-cue notice, so they stand in `NOTICE_ONLY` as well — a reporting row pinned
    # in only one of the two lists is asserted by nothing (see the collection-time
    # assertion under `NOTICE_ONLY`). `is the export yet to be archived` is the row that
    # makes the defect obvious: it asks for the OPPOSITE of archived.
    "is the export to be archived",
    "is the data being archived",
    "is the export yet to be archived",
    "is the export supposed to be archived",
    "is the export going to be archived",
    "is the export due to be archived",
    "is the report to be retired",
    "is the claim being withdrawn",
    "is the export to be archived by the vendor",
    "is the export to be archived in Q3",
    # ── …and the REST OF THE CLASS the literal spellings missed (round-28 review, P1) ──
    # A role, not two spellings: `getting` is the colloquial progressive, ONE adverb splits
    # `to be`, and `to get`/`to become` are the same scheduled-state frame. Every one of
    # these ROUTED the whole ended population with the qualifier dropped and `signal=None`
    # before the guard was re-bound to `_NONFINITE_COPULA`.
    "is the export getting archived",
    "is the export to eventually be archived",
    "is the export to get archived",
    "is the export to become archived",
    "is the export becoming archived",
    "is the export becoming deprecated",
    "is the report staying archived",
    "is the export ending up archived",
    "is the export winding up archived",
    "is the claim turning out archived",
    "is the export remaining archived",
    "is the export keeping archived",
    # ⛔ THE ADVERB BOUND IS THE PREFIX'S BOUND, NOT A GUESS (round-12 review, P1, measured).
    # `_COPULA_PREFIX` absorbs `{0,3}` subject words PLUS one adverb slot, so a refusal
    # bounded at two let THREE adverbs fall through and the prefix carried the row to the
    # ending: `is the export being really actually already archived` ROUTED the whole ended
    # population with `signal=None` — 216 of 216 such rows — the fail-open direction, one
    # round after the same widening fixed the same direction. The bound is `{0,4}` now.
    # ⛔ AND `now` USED TO DISABLE THE NOTICE (round-12 review, P3): arm A's second-clause
    # list carried `now`, so the same row with a trailing ` now` reported NOTHING. Removed —
    # an adverb of time introduces no finite verb.
    "is the export being really actually already archived",
    "is the export being really actually definitely archived now",
    "is the export getting really actually already deprecated",
    # ⚠️ THE DECLARED RESIDUAL, PINNED RATHER THAN CLAIMED AWAY (round-12 review, P3): the
    # frame rule discriminates on POSITION, and position cannot tell a predicate participle
    # from a modifying one — so an attributive `-ing` followed by an ATTRIBUTIVE ENDING WORD
    # is refused with NO notice. Not a regression (identical before the widening), but it sits
    # inside the class the attributive pins declare fixed, so it is pinned as silent.
    "is the remaining archived report archived",
    "is the keeping withdrawn invoice archived",
    # ── …and the FOUR PHRASAL / RESULTATIVE verbs the class was missing (fresh review,
    # P2, measured). `to wind up`, `to end up`, `to turn out` and `to stay` are the same
    # scheduled or continuing frame as `to be`, and each ROUTED the whole ended population
    # with the qualifier dropped and `signal=None`. Refused now, and REPORTED, because
    # refusing without a notice is the other half of #14033.
    "is the export to wind up archived",
    "is the export to end up archived",
    "is the export to turn out archived",
    "is the export to stay archived",
    # ── …and the CONTRACTED asyndetic twin this shape swallowed (round-28 review, P1) ──
    # `has anyone reported v3 hasn't been deprecated` ROUTED through the perfect branch and
    # dropped the caller's own verb (`reported`) with `signal=None`, while its UN-contracted
    # twin was refused. Refused now, and reported through `existence_unguarded`.
    "has anyone reported v3 hasn't been deprecated",
    "has anyone complained billing hasn't been deprecated",
    # …and the ADVERB-inside-the-non-finite-phrase rows (round-4 review, P2, measured):
    # refused AND reported, so they stand in `NOTICE_ONLY` as well (see the collection-time
    # assertion there).
    "is the export being quietly archived",
    "is the data being constantly archived",
    "is the export getting officially retired",
    # ── …and the ADVERB-CLASS hole in the FRAME-AWARE guard (round-12 review, P1,
    # measured — the module's OWN round-24 lesson, one frame over) ─────────────────
    # The frame's gap was spelled as a hand-typed list (`[a-z]+ly|already|ever|really|
    # still|actually|just|only|never|not`), not as the adverb class `_EXISTENCE_ADVERB`
    # already consolidated for exactly this failure. Round 12 bounded the COUNT to four and
    # left the CLASS untouched, so any adverbial outside the list still re-opened the
    # fail-open the guard exists to close: measured at `9dedcf60e7`, each of these ROUTED
    # the whole ended population with the caller's qualifier dropped and `signal=None`.
    # The slot is now the shared class with `_COPULA_PREFIX`'s own bound (four), and the
    # comma is part of the gap (`\s+` cannot match `, `, so `is the export being, in fact,
    # archived` was refused with NO notice at BOTH this head and its parent — a cue reaching
    # no answer and saying nothing). Non-vacuity: revert the slot to the hand-typed pair and
    # all seven rows red; narrow arm B alone and five red (silence); drop the comma and one
    # reds. Measured, not asserted.
    "is the export being very archived",
    # ⛔ ONE TOKEN DECIDED WHETHER THE CALLER WAS TOLD (round-13 review, P2, measured). The
    # copula branch's ONLY notice arm was anchored `^(?:is|are|was|were)\s+`, while the
    # refusal accepts a CONTRACTED head (`(?:n['’]t)?`) — so `isn't the export being archived`
    # answered `(None, None, None)`: refused, `signal=None`, no payload — SILENCE, on the very
    # defect #14033 exists to close, while its uncontracted twin reported
    # `is_copula_long_subject`. These rows FAIL at `3088fd8d6a` (silent there) and report here.
    "isn't the export being archived",
    "isn't the export being very very very archived",
    "aren't the claims being withdrawn",
    "is the export to be very archived",
    "is the export being no longer archived",
    "is the export being almost archived",
    "is the export being often archived",
    "is the export becoming soon archived",
    "is the export being, in fact, archived",
    "hasn't the billing's been deprecated",
    "has somebody reported the vendor has been deprecated",
    # …and the two spellings the round-13 widening of the quantifier enumeration still left
    # out (that enumeration now lives in `_EXISTENCE_QUANTIFIERS`, shared by both heads — the
    # round-33 split moved it out of `_EXISTENCE_HEAD`, which no longer carries it)
    # (round-14 review, P2, measured): `anybody` is the exact synonym of the
    # `anyone`/`somebody` pair that widening had just made report, and the gap consumes ONE
    # token so a two-word `no one` matched neither half. All three were SILENT at
    # `cb30c1a6b6` while `has somebody reported …` reported `existence_unguarded`.
    "has anybody reported the vendor has been deprecated",
    "has no one reported the vendor has been deprecated",
    "has no-one reported the vendor has been deprecated",
    # …and the separator is a CODEPOINT CLASS, not the two spellings that were measured
    # (follow-up review, P3): U+2011/U+2013/U+2014 and the un-hyphenated `noone` were all
    # SILENT while the ASCII twin reported — the module already treats apostrophes as an
    # inventory for exactly this reason, so the dash is one too.
    "has no‑one reported the vendor has been deprecated",
    # U+2013 is pinned as well, not just named in prose: a class the body claims but no row
    # asserts is a claim, not a contract (review, P3 — dropping U+2013 from the range reddened
    # NOTHING before this row existed).
    "has no–one reported the vendor has been deprecated",
    "has no—one reported the vendor has been deprecated",
    "has noone reported the vendor has been deprecated",
    # ⛔ THE ZERO-WORD SUBJECT OF A NON-FINITE COPULA (round-3 review, P3, measured): the
    # refusal lookahead allows `{0,6}` words before `_NONFINITE_COPULA` (`to be|to get|to
    # become|being|getting`, plus up to two modifiers — NOT the three-spelling literal an
    # earlier cut of this comment named), so a SUBJECT-LESS question is refused; but arm B's
    # reporter required `{1,5}`, so these four came back `population=None, signal=None,
    # payload=None` (SILENT). ⛔ THE HISTORY, WITH THE COMMITS NAMED: they ROUTED at
    # `0e52593c91` (before `_COPULA_INFINITIVE_REFUSAL` landed) and were ALREADY SILENT at
    # `21cc92e90c` — the base of the commit that fixed them — so "routed at the delta's base"
    # would be false. Refusing is right; going silent is not. Pinned in BOTH lists.
    "is being archived",
    "is to be archived",
    "is getting archived",
    "was being retired",
    # …the BARE-`been` COPULA SUBJECT class, reported through `existence_unguarded` (the
    # reasoning and the measurements live beside its `NOTICE_ONLY` pins).
    "is the export been archived",
    "is the report been reviewed archived",
    "is the export still been archived",
    "isn't the export been archived",
    "aren't the claims been archived",
    "wasn't the report been archived",
    # …the CONTRACTED QUANTIFIER FRAME (round-33 review, P2, measured): the notice arm's head was
    # `_EXISTENCE_HEAD`, whose auxiliary is uncontracted — and it is uncontracted ON PURPOSE
    # (that constant is the head of `_EXISTENCE_PREFIX`, i.e. it ROUTES). So the 30-row class
    # `{hasn't,haven't,hadn't} x {anyone,anybody,someone,nobody,no one} x {reported the vendor has
    # been deprecated, reported it has been archived}` was SILENT while every uncontracted twin
    # reported. The notice arm now takes a head that admits the contraction, derived from the
    # SAME `_EXISTENCE_AUX` + `_EXISTENCE_QUANTIFIERS`, so the quantifier list still has one home
    # and the shape-side pattern is byte-identical (asserted, not hoped).
    "hasn't anyone reported the vendor has been deprecated",
    "haven't any users said it has been archived",
    "hadn't anybody reported it has been archived",
]


#: The pinned content rows that DO carry a missed-cue notice (#14033) — every one a
#: state question the cue table refused, and each named with the reason. ⛔ The list
#: is EXHAUSTIVE on purpose: `test_an_ordinary_query_keeps_the_relevance_read` asserts
#: a notice here and NO notice anywhere else in ``NOT_SELECTED``, so an ending cue
#: that starts firing on ordinary content (`which` as a signal, the first cut's
#: `^which\s+[^?]{0,60}?<ENDING>`) reds instead of quietly annotating 16 content
#: rows — which is what the round-6 review measured the first version doing.
NOTICE_ONLY = [
    # ⛔ `already`/`ever` AS THE AUXILIARY — THE REFUSAL THAT USED TO BE SILENT (turn-3, P2,
    # measured at `34b339c75d` vs `main` `70fbd1a27d`). The round-7 decision stands (this class
    # does NOT route: the adverb admits the ACTIVE reading), but the refusal emitted no notice,
    # so `have any reports already archived` and its siblings were `population=None,
    # signal=None` while `main` answered every one of them with `ended`/`existence`. A caller
    # cannot distinguish that from "nothing has ended", which is duty (b). `_ALREADY_EVER_AUX`'s
    # arm in `existence_unguarded` reports the cue instead, and the arm is a SIGNAL, so the
    # routing table is untouched — 0 population/shape moves over the pinned corpus (`SELECTED`
    # + `NOT_SELECTED` + `NOTICE_ONLY`, 321 DISTINCT queries; ⛔ not the 624 a naive walk of the
    # three lists yields — `SELECTED` holds `(query, population, shape)` tuples, so a recursive
    # walk counts 204 population/shape TOKENS as rows, and `NOTICE_ONLY` duplicates `NOT_SELECTED`.
    # A corpus size that cannot be reproduced from the lists is exactly the kind of stale claim
    # this module's own cost test exists to prevent, so the reproducible number is the one stated).
    # ⛔ AND THE ACTIVE-VOICE SIBLING IS STILL SILENT ON PURPOSE — `has the partner already
    # cancelled` / `has legal already retracted` (no existential quantifier, so no ending CUE is
    # recognised, and they were silent on `main` too). Pinned in `NOT_SELECTED` only, so an arm
    # widened to "any auxiliary + adverb" would red here instead of annotating a content query.
    "have any reports already archived",
    "have any customers ever cancelled",
    # ⛔ THE ONE ROW WHOSE `cue` IS NOT A PREFIX OF THE CALLER'S OWN STRING (round-29
    # review, P3, measured): `cue` is the matched text AFTER normalisation, so this
    # row's cue spells the apostrophe as ASCII. Without it the pin's apostrophe loop
    # was exercised by zero inputs — reverting the normalisation left it green.
    "have any users reported the vendor hasn’t been deprecated",
    # an appended CONSTRAINT the population cannot apply
    "have any claims been retracted in Q3",
    "have any of our bookings been cancelled by the venue",
    "has anything we promised been withdrawn by legal",
    "which claims were retracted by legal",
    "which reports were superseded by the new one",
    "which units are cancelled in the export",
    "what has ended last quarter",
    # a COMPOUND question: only the first clause is an ending
    "what ended, and what replaced it",
    # an ATTRIBUTIVE ending word in a `which` phrase — the fourth row of #14033's
    # table is exactly this class, so the notice is the row's whole remedy
    "which archived report has the Q3 numbers",
    "which archived reports were retracted",
    # a stated RECALL LOSS the shapes refuse, reported rather than silent: a modified
    # `which` head noun, an over-long copula subject, and boundary-free adverbs the
    # recency list does not route.
    "which customer claims were archived",
    "is the onboarding flow for enterprise partners retired",
    "what ended previously",
    "which claims were retracted earlier",
    "list what we retired previously",
    "is the onboarding flow retired anymore",
    # …and the `existence` clause class: the CUE is really there (`has … been
    # <ended>`), only the clause guard refused it, so it is reported rather than
    # silenced — the notice's hint is conditional on what the caller is asking, and
    # their rows are unchanged either way.
    "have any customers complained since the old plan has been cancelled",
    "have any users complained because the feature has been deprecated",
    "has anything changed since the old pricing has been deprecated",
    "have any customers churned after the tier has been deprecated",
    # …including the asyndetic second clause the narrowed guard still refuses
    # (`<det> <noun> <finite verb> been`): reported, never routed, though it reads like
    # the relative-clause form — the two are separated by which clause supplies `been`.
    "have any users reported the vendor has been deprecated",
    "have any customers said the feature has been deprecated",
    "have any users noticed the vendor will have been deprecated",
    # …and the PRONOUN-SUBJECT twin of that class (#14033 round-14 review, P2): there the
    # subject slot needs no noun, so the guard needs its own alternative — refused by the
    # guard, reported through `existence_unguarded`, never routed.
    "have any users reported it has been deprecated",
    "have any customers noticed they have been cancelled",
    # …and the ROUND-5 pairs, added with their NOT_SELECTED rows: the 4-token adverbial run the
    # slot's old `{0,3}` bound let through, and the bare `just`/`only` frames the notice was blind
    # to. Reported, never routed — and each is asserted in BOTH lists, so deleting either half reds.
    "have any reported it has been in fact only just deprecated and been withdrawn",
    "have any reported it has been in fact just about deprecated and been withdrawn",
    "has anyone complained billing has been just deprecated",
    "has anyone complained billing has been only deprecated",
    "has the billing's been deprecated",
    "has v3's been deprecated",
    "has the billing's been just deprecated",
    "has the billing's been only just deprecated",
    "have any users reported it has been deprecated by legal been withdrawn",
    "have any users reported the vendor has been deprecated by legal been withdrawn",
    "have any users reported the vendor has been deprecated by legal and been withdrawn",
    "has anyone complained billing has been deprecated by legal been withdrawn",
    "has anything that has been in fact only just superseded by legal been withdrawn",
    # ⛔ …AND THEIR ADVERB TWINS (review of this fix, P1, measured): the adverb slot this lane adds
    # to the `been`-anchored window is matched by the SAME slot in the two lookaheads that decide
    # whether a `been` belongs to a second CLAUSE. Without that symmetry these three ROUTED the whole
    # ended population with the caller's own predicate (`reported`, `noticed`, `complained`) dropped
    # and `signal=None`. They are refused and REPORTED exactly like the two rows above them, and are
    # pinned so a one-sided widening reds instead of shipping.
    "have any users reported it has been already deprecated",
    "have any customers noticed they have been already cancelled",
    "has anyone complained billing has been recently deprecated",
    # ⛔ THE REFUSAL-SIDE SLOT'S ROWS (round-3 review, P1, measured): the WINDOW's slot is the narrow
    # `already|ever|recently` (recall), while the two clause LOOKAHEADS are refusal-only and carry
    # the WHOLE `_EXISTENCE_ADVERB` class — including the multi-word adverbials, which need the
    # repetition the slot carries). Giving the lookaheads the narrow slot was a measured hole:
    # `just`/`only`/`no longer`/`to date` are class members but were not in it, so they blinded both
    # lookaheads and these ROUTED the whole ended population with the caller's predicate dropped and
    # `signal=None`, while the `already` spelling of the same string was refused.
    "have any users reported it has been just deprecated and been withdrawn",
    "have any users reported it has been only deprecated and been withdrawn",
    "have any users reported it has been no longer deprecated and been withdrawn",
    "have any users reported it has been to date deprecated and been withdrawn",
    "have any users said it will have been deprecated",
    "is the SOC2 audit and its evidence package archived",
    "is the onboarding flow and its partner variants retired",
    # …and the COMPLEMENTIZER spellings of the class above (round-14 review, P1).
    # `whether`/`that`/`which` name the subordinate clause, the ending participle is
    # that clause's, and the population cannot answer a question about `asked`/`said`/
    # `noted` — so the cue is REPORTED and never routed, which keeps the precision
    # contract while the caller still learns a cue was seen.
    "has anyone asked whether the old plan has been archived",
    "has anyone said that our launch plan has been withdrawn",
    "has anyone noted which endpoint has been deprecated",
    # …and the FREE-RELATIVE (round-17 review, P2) rows pinned in NOT_SELECTED above.
    "has anyone asked why the old plan has been archived",
    "has anyone asked how the old plan has been archived",
    # …and the BARE-NOUN spellings of the asyndetic class (round-16 review, P1): the
    # clause guard refused them for lack of a determiner, the cue is real, so they are
    # reported rather than routed.
    "have any users reported v3 has been deprecated",
    "has anyone complained billing has been deprecated",
    "have any users reported the search index has been deprecated",
    "have any users reported the vendor has recently been deprecated",
    "have any users reported it has recently been deprecated",
    "have any users reported the vendor will have recently been deprecated",
    "have any users reported the vendor has only just been deprecated",
    # …and the NON-`-ly` adverbials and the 4-deep stack (round-24 review, P1): the
    # `-ly` class is not the adverb class, so `very recently` / `just about` / `in fact`
    # slipped the first slot and `only just very recently` exceeded its {0,3} bound.
    # Measured: 291 of 588 rounded off the 7x7x12 matrix before the widening.
    "have any users reported the vendor has very recently been deprecated",
    "have any users reported the vendor has only just very recently been deprecated",
    # …and the non-`-ly` / positional-negation / two-word spellings (round-25 review, P1):
    # 12 of 12 adverbials, `still not` and `recently not` all routed before this widening.
    "have any users reported the vendor has often been deprecated",
    "have any users reported the vendor has still not been deprecated",
    "have any users reported the vendor has no longer been deprecated",
    "have any users reported the vendor has, in fact, recently been deprecated",
    # …and the ONE row of round-19's pre-determiner-opener class that REPORTS rather than
    # going silent: it is five words long, so the subject bound exceeded is what the
    # notice names (`is_copula_long_subject`). Its short siblings stay in NOT_SELECTED
    # alone, which is the stated silent part of the recall loss.
    "is all the data the board archived",
    # …and the three LONG rows of the possessive / pre-determiner / determiner-less
    # class (round-20 review, P2): each reports `is_copula_long_subject`.
    "is the contract every employee signed archived",
    "is the report each manager reviewed archived",
    "is the dashboard all customers use deprecated",
    # ── the INFINITIVE / PROGRESSIVE class (review P1 at `42e0c1a662`, measured) ────
    # `to be` and `being` inside the subject are a SECOND VERB, not noun-phrase words, so
    # the shape must not route: `is the export to be archived` asks about a scheduled /
    # future state, `is the data being archived` about one in progress, and `is the export
    # yet to be archived` is the OPPOSITE of archived. Measured before the fix: all three
    # served the WHOLE ended population with the qualifier dropped and `signal=None` —
    # #14033's own defect on the shape this PR adds. They are REPORTED now, through
    # `is_copula_long_subject` (arm B), because a refused state question that says nothing
    # is the other half of the same defect.
    "is the export to be archived",
    "is the data being archived",
    "is the export yet to be archived",
    "is the export supposed to be archived",
    "is the export going to be archived",
    "is the export due to be archived",
    "is the report to be retired",
    "is the claim being withdrawn",
    "is the export to be archived by the vendor",
    "is the export to be archived in Q3",
    # …the rest of that class, measured in round 28: the non-finite VERB PHRASE is a role
    # (`getting`, `to <adverb> be`, `to get`, `to become`), and the contracted auxiliary is
    # the same second-verb class as `has`.
    "is the export getting archived",
    "is the export to eventually be archived",
    "is the export to get archived",
    "is the export to become archived",
    # -- THE `-ing` PROGRESSIVE FRAME, EVERY SPELLING (round-10 review, P1, measured) --
    # !! THE CLASS SHIPPED WITH `being|getting` ONLY, so `is the export becoming archived`
    # ROUTED the whole ended population with the caller's qualifier dropped and
    # `signal=None` -- fail-open, the direction #14033 exists to close -- while its `being`
    # twin refused correctly. One spelling over, the same frame: measured at `cdfc3b1957`,
    # 20/20 rows routed for EACH of these, and `main` (`70fbd1a27d`) REFUSED them, so this
    # was introduced by this PR, not inherited. Each `-ing` form is now pinned beside its
    # `to <verb>` twin so a widening of one cannot leave the other behind. These rows FAIL
    # on `cdfc3b1957` (all route) and are refused AND reported here.
    "is the export becoming archived",
    "is the export becoming deprecated",
    "is the report staying archived",
    "is the export ending up archived",
    "is the export winding up archived",
    "is the claim turning out archived",
    "is the export remaining archived",
    "is the export keeping archived",
    # ⛔ THE ADVERB BOUND IS THE PREFIX'S BOUND, NOT A GUESS (round-12 review, P1, measured).
    # `_COPULA_PREFIX` absorbs `{0,3}` subject words PLUS one adverb slot, so a refusal
    # bounded at two let THREE adverbs fall through and the prefix carried the row to the
    # ending: `is the export being really actually already archived` ROUTED the whole ended
    # population with `signal=None` — 216 of 216 such rows — the fail-open direction, one
    # round after the same widening fixed the same direction. The bound is `{0,4}` now.
    # ⛔ AND `now` USED TO DISABLE THE NOTICE (round-12 review, P3): arm A's second-clause
    # list carried `now`, so the same row with a trailing ` now` reported NOTHING. Removed —
    # an adverb of time introduces no finite verb.
    "is the export being really actually already archived",
    "is the export being really actually definitely archived now",
    "is the export getting really actually already deprecated",
    "has anyone reported v3 hasn't been deprecated",
    "has anyone complained billing hasn't been deprecated",
    # …and the ADVERB between the non-finite verb and the participle (round-4 review, P2,
    # measured): `is the export being quietly archived` and its twins were SILENT while the
    # no-adverb spelling reports — the round-22 class one slot over. A signal cannot route, so
    # the widened slot can only add a report.
    "is the export being quietly archived",
    "is the data being constantly archived",
    "is the export getting officially retired",
    # …the ADVERB-CLASS rows from the frame-aware guard's shared slot (round-12 review,
    # P1): refused AND reported, so they stand in both lists.
    "is the export being very archived",
    # ⛔ ONE TOKEN DECIDED WHETHER THE CALLER WAS TOLD (round-13 review, P2, measured). The
    # copula branch's ONLY notice arm was anchored `^(?:is|are|was|were)\s+`, while the
    # refusal accepts a CONTRACTED head (`(?:n['’]t)?`) — so `isn't the export being archived`
    # answered `(None, None, None)`: refused, `signal=None`, no payload — SILENCE, on the very
    # defect #14033 exists to close, while its uncontracted twin reported
    # `is_copula_long_subject`. These rows FAIL at `3088fd8d6a` (silent there) and report here.
    "isn't the export being archived",
    "isn't the export being very very very archived",
    "aren't the claims being withdrawn",
    "is the export to be very archived",
    "is the export being no longer archived",
    "is the export being almost archived",
    "is the export being often archived",
    "is the export becoming soon archived",
    "is the export being, in fact, archived",
    "hasn't the billing's been deprecated",
    "has somebody reported the vendor has been deprecated",
    # …and the two spellings the round-13 widening of the quantifier enumeration still left
    # out (that enumeration now lives in `_EXISTENCE_QUANTIFIERS`, shared by both heads — the
    # round-33 split moved it out of `_EXISTENCE_HEAD`, which no longer carries it)
    # (round-14 review, P2, measured): `anybody` is the exact synonym of the
    # `anyone`/`somebody` pair that widening had just made report, and the gap consumes ONE
    # token so a two-word `no one` matched neither half. All three were SILENT at
    # `cb30c1a6b6` while `has somebody reported …` reported `existence_unguarded`.
    "has anybody reported the vendor has been deprecated",
    "has no one reported the vendor has been deprecated",
    "has no-one reported the vendor has been deprecated",
    # …and the separator is a CODEPOINT CLASS, not the two spellings that were measured
    # (follow-up review, P3): U+2011/U+2013/U+2014 and the un-hyphenated `noone` were all
    # SILENT while the ASCII twin reported — the module already treats apostrophes as an
    # inventory for exactly this reason, so the dash is one too.
    "has no‑one reported the vendor has been deprecated",
    # U+2013 is pinned as well, not just named in prose: a class the body claims but no row
    # asserts is a claim, not a contract (review, P3 — dropping U+2013 from the range reddened
    # NOTHING before this row existed).
    "has no–one reported the vendor has been deprecated",
    "has no—one reported the vendor has been deprecated",
    "has noone reported the vendor has been deprecated",
    # ── the `<function word> the <noun>` TAIL class — reported, or silent for a REASON
    # that is now stated and machine-checked (review at `5b82bcdf03`, P2, measured) ──
    # The PREPOSITIONS `_BLOCKED_TAIL` names are REPORTED on this shape (see the
    # reported list in `test_a_strong_cue_that_does_not_fire_reports_itself_and_never_routes`)
    # UNLESS the subordinator lookahead already refuses the shape. These three are
    # prepositions AND subordinators, so the exclusion list cannot rescue them: measured,
    # adding `after|before|since` there flips 0 of 277 probe outcomes. The UD tags split
    # the two readings only after the fact (`ADP` before a nominal, `SCONJ` before a
    # clause), which is why this is a stated class rather than a longer word list.
    # (`test_every_preposition_the_module_declares_reports_the_qualifier_it_introduces`
    # pins the same three as a deliberate omission from `_PREPOSITIONS`.)
    # …and the CONJUNCTIONS are silent for a DIFFERENT reason: the second-NP test reads a
    # coordination as one, and none of these five is in the subordinator lookahead, so
    # this is a LIST decision rather than that guard's participle bound. Excluding them
    # WOULD report them (measured: 5 probes flip, 0 pinned rows move) — a recall widening
    # with its own review, deliberately not made inside a claim correction.
    # ⛔ the zero-word subject of a non-finite copula — refused AND reported (see the note
    # in `NOT_SELECTED`); arm B's lower bound is zero for exactly these four.
    "is being archived",
    "is to be archived",
    "is getting archived",
    "was being retired",
    # ⛔ …and the four PHRASAL / RESULTATIVE non-finite verbs (fresh review, P2, measured):
    # `to wind up|end up|turn out|stay` were outside `_NONFINITE_COPULA`, so the shape ROUTED
    # them and served a scheduled/continuing state as the ended population. Refused AND
    # reported now — pinned in BOTH lists, so deleting either half reds.
    "is the export to wind up archived",
    "is the export to end up archived",
    "is the export to turn out archived",
    "is the export to stay archived",
    # ⛔ THE BARE-`been` COPULA SUBJECT CLASS (round-31 review, P3, measured): round-30 forbids
    # `been` as a SUBJECT word in `_COPULA_SUBJECT` — rightly, that is what stops the subject
    # slot from swallowing the auxiliary — but on a copula head there is then nothing to fall
    # through to, so `<is|are|was|were> <subject> been <ending>` came back with NOTHING while
    # the base `42e0c1a662` served `ended/is_copula`: measured, 45 of 45 `is <det> <noun> been
    # <ending>` rows silent, and `signal=None`/`payload=None` on every one. Ungrammatical input,
    # but a cue reaching no answer and saying nothing is the mechanism this module forbids, so
    # they report `existence_unguarded` now. ⛔ SCOPED TO THE COPULA HEADS: letting the same
    # alternative take `has|have|had` made it PREEMPT `which_modified_noun` on
    # `which customer claims has been archived` (this row is evaluated BEFORE that one,
    # index 6 of 9 vs 7 of 9), which the suite caught — so `<aux> … been` is deliberately
    # NOT in that arm. The CONTRACTED copulas (`isn't`, `aren't`, `wasn't`, `weren't`) ARE:
    # they are the same head, and their absence left 108 of 108 contracted rows silent
    # (round-32 review, P2), pinned below.
    "is the export been archived",
    "is the report been reviewed archived",
    "is the export still been archived",
    "isn't the export been archived",
    "aren't the claims been archived",
    "wasn't the report been archived",
    # …and the CONTRACTED QUANTIFIER FRAME rows (round-33 review, P2 — see the block beside
    # their `NOT_SELECTED` pins).
    "hasn't anyone reported the vendor has been deprecated",
    "haven't any users said it has been archived",
    "hadn't anybody reported it has been archived",
]

assert set(NOTICE_ONLY) <= set(NOT_SELECTED), (
    f"`NOTICE_ONLY` rows are only ever READ through the `NOT_SELECTED` parametrization, "
    f"so a row here that is not also there is asserted by NOTHING — measured: 8 such "
    f"orphans accumulated, and they encoded the OPPOSITE of the implemented behaviour "
    f"(`signal is None`), so promoting one into `NOT_SELECTED` would have reddened the "
    f"suite on a correct answer. Difference: {sorted(set(NOTICE_ONLY) - set(NOT_SELECTED))}"
)


def test_the_contraction_is_admitted_by_the_NOTICE_head_and_never_by_the_SHAPE_head():
    """One class, two heads, and the split is the claim (round-33 review, P2).

    `_EXISTENCE_HEAD` is the head of `_EXISTENCE_PREFIX`, i.e. it ROUTES, so admitting a
    contracted auxiliary there would move the routing surface — which is why the contracted
    quantifier frame (`hasn't anyone reported the vendor has been deprecated`) was silent for
    all 30 measured rows instead of being fixed by widening that constant. The NOTICE arm takes
    `_EXISTENCE_HEAD_NOTICE`, the same two classes with `n['\u2019]t` admitted; it is a signal
    head and cannot route.

    This pins the SPLIT itself, because the behaviour rows below can only show that the notice
    fires — not that the shape-side pattern stayed as it was.
    """
    import re

    from tortoise.state_intent import _EXISTENCE_HEAD, _EXISTENCE_HEAD_NOTICE

    contracted = "hasn't anyone reported the vendor has been deprecated"
    assert not re.search(_EXISTENCE_HEAD, contracted), (
        "the SHAPE head now admits a contracted auxiliary — that is a routing change, not a "
        "notice widening; it needs its own measurement against `which_modified_noun` "
        "preemption and its own pins")
    assert re.search(_EXISTENCE_HEAD_NOTICE, contracted), (
        "the NOTICE head no longer admits the contracted auxiliary — the 30-row silent class "
        "this exists for is open again")


@pytest.mark.parametrize("query", NOT_SELECTED)
def test_an_ordinary_query_keeps_the_relevance_read(query):
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population is None, (
        f"{query!r} was routed to the {got.population!r} population "
        f"(shape {got.shape!r}) — a detector that hijacks a content lookup "
        f"answers a question nobody asked")
    # ⛔ AND `signal is None` UNLESS THE ROW IS PINNED IN `NOTICE_ONLY`. Without
    # this half the whole notice mechanism could widen to arbitrary content: the
    # first cut's `^which\s+[^?]{0,60}?<ENDING>` signal annotated 16 pinned content
    # rows, and every test in this file stayed green.
    if query in NOTICE_ONLY:
        assert got.signal, (
            f"{query!r} is pinned as a state question that DECLINES to route, so it "
            f"must report the cue it saw — otherwise its relevance reply is "
            f"indistinguishable from 'nothing has ended' (#14033)")
    else:
        assert got.signal is None, (
            f"{query!r} carries the missed-cue notice {got.signal!r} — it names no "
            f"ending state, so this is noise on an ordinary content query, and the "
            f"notice's own contract is that it appears only where a state cue was "
            f"recognised")


def test_none_is_a_full_scan_not_a_question():
    from tortoise.state_intent import detect_state_question

    assert detect_state_question(None).population is None


def test_an_INFINITIVE_or_PROGRESSIVE_subject_is_REFUSED_and_REPORTED():
    """Review P1 at `42e0c1a662` — the shape this PR adds was routing a FUTURE state.

    `to be` and `being` inside the subject are a second verb. The subject slot read them
    as noun-phrase words, so `is the export to be archived` (a SCHEDULED state), `is the
    data being archived` (one in progress) and — worst — `is the export yet to be
    archived` (the OPPOSITE of archived) each served the WHOLE ended population with the
    qualifier dropped and `signal=None`. The ended population cannot contain a row that
    is pending-but-not-yet-ended, so a caller reads a full answer as "no" and never
    learns the cue was refused. Measured before the fix: 240 of 240 templated
    `<is|are> <subject> <infinitive> <ending>` probes routed with no signal.

    ⛔ THE FIX IS A GUARD PLUS A NOTICE, and both halves are asserted here: refusing
    without reporting trades one #14033 defect (a route with the qualifier dropped) for
    the other one (an unrouted state question that looks like an honest empty answer).
    ⛔ AND IT IS `to be`, NOT `to`: the prepositional spelling must keep routing, which is
    the class the cheap wrong fix (`to`) would have killed.
    """
    from tortoise.state_intent import detect_state_question

    refused = [
        "is the export to be archived",
        "is the data being archived",
        "is the export yet to be archived",
        "is the export supposed to be archived",
        "is the export going to be archived",
        "is the export due to be archived",
        "is the export scheduled to be archived",
        "is the report to be retired",
        "is the claim being withdrawn",
        # ⛔ THE PHRASAL / RESULTATIVE VERBS ARE THE SAME ROLE (fresh review, P2,
        # measured). `to wind up`, `to end up`, `to turn out` and `to stay` are the same
        # scheduled or continuing frame as `to be`, and each ROUTED the whole ended
        # population with the qualifier dropped and `signal=None` — `is the export to
        # wind up archived` among them. Listed here AND in `NOT_SELECTED`/`NOTICE_ONLY`,
        # with an adverb and a `supposed to` frame so the two consumers are driven
        # through the widened class rather than one spelling of it.
        "is the export to wind up archived",
        "is the export to end up archived",
        "is the export to turn out archived",
        "is the export to stay archived",
        "is the export to eventually wind up archived",
        "is the export supposed to stay archived",
        "is the report to end up retired",
    ]
    for query in refused:
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} routed to the {got.population!r} population — the infinitive or "
            f"progressive verb phrase inside the subject was read as a noun phrase, so "
            f"the question's own tense and qualifier were dropped (#14033 class)")
        assert got.signal == "is_copula_long_subject", (
            f"{query!r} was refused but reports {got.signal!r} — a silent refusal is the "
            f"other half of the #14033 defect: an unrouted state question must be "
            f"distinguishable from an empty answer")

    # a prepositional `to` introduces a NOUN, not an infinitive: it must keep routing
    for query in ("is the export to Europe archived", "is the export archived",
                  "is the export to the vendor archived",
                  "is the export to legal archived"):
        got = detect_state_question(query)
        assert got.population == "ended", (
            f"{query!r} is a plain state question but no longer routes ({got!r}) — the "
            f"infinitive guard was widened to the word `to`")

    # ⛔ AND THE CLOSED LIST'S RESIDUAL IS A MEASUREMENT, NOT A SENTENCE (fresh review,
    # P2). `_NONFINITE_COPULA` names a closed set of non-finite verbs; a non-finite verb
    # OUTSIDE it still routes, which the constant's own note says. Pinning one keeps that
    # claim falsifiable: a later widening that swallows them reds here, and a reader can
    # see the size of what is left rather than inferring zero from silence.
    # ⛔ THE PROXY MOVED, THE PROPERTY DID NOT (round-10 review, P1). This pin used to name
    # `to remain` / `to keep` as verbs OUTSIDE the closed list — and they were, until that
    # round measured `remaining`/`keeping` ROUTING the whole ended population while
    # `being` refused, one spelling over: the same progressive frame. Both pairs are in
    # `_NONFINITE_COPULA` now, so they refuse AND report, and the residual is re-pinned on
    # verbs that are still genuinely outside it. The claim being kept falsifiable is the
    # SAME one: a non-finite verb the list does not name still routes, and a widening that
    # swallows the residual reds here rather than silently overstating the class covered.
    for query in ("is the export to remain archived", "is the export to keep archived"):
        got = detect_state_question(query)
        assert got.population is None and got.signal == "is_copula_long_subject", (
            f"{query!r} went to {got!r} — `remain`/`keep` are IN the class as of the round "
            f"that closed the `-ing` progressive frame, so this row must refuse and report")
    for query in ("is the export to appear archived", "is the export to seem archived"):
        got = detect_state_question(query)
        assert got.population == "ended" and got.signal is None, (
            f"{query!r} moved to {got!r} — `appear`/`seem` are the residual the note on "
            f"`_NONFINITE_COPULA` declares outside the closed list, so a change here means "
            f"that note now understates what the class carries")

    # ⛔ THE TWO SILENCES THIS FIX LEAVES, STATED AND PINNED (a report that claimed the
    # class closed would be the drift this branch has already paid for twice):
    #   * a subordinate clause SUPPLIES the predicate (`is it true that the export is to
    #     be archived`) — the same content class the existing subordinator guard refuses;
    #   * a CAUSAL qualifier (`is the plan being retired because of the migration`) is the
    #     class this module refuses deliberately (see the guard-B note).
    # Both stay silent, and this test makes that a decision on the record rather than a
    # discovery for the next reviewer.
    for query in (
        "is it true that the export is to be archived",
        "is the plan being retired because of the migration",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal is None, (
            f"{query!r} moved to {got!r} — the stated silences of the infinitive class "
            f"are not silences any more, so the prose on `_COPULA_SUBJECT` and the "
            f"`_COPULA_LONG_SUBJECT` arm-B note are now wrong")


def test_the_ALREADY_EVER_auxiliary_is_REFUSED_and_REPORTED():
    """Turn-3 P2 at `34b339c75d` — the class this branch refuses was refused SILENTLY.

    Making the existence window's auxiliary the literal `been` (round 7, the active-reading
    decision) left `already`/`ever` AS THE AUXILIARY matching no shape at all. Measured on
    both ends in one call: `main` `70fbd1a27d` answers `have any reports already archived`
    with `ended`/`existence`; the head answered `population=None, signal=None`. `main`'s
    answer was the WRONG one (the active reading) and at least visible — silence is
    indistinguishable from "nothing has ended", which is #14033 duty (b).

    ⛔ THE FIX IS A NOTICE, NOT A ROUTE, and this test holds BOTH directions:
      * the refused class reports `existence_unguarded` (the cue is recognised, no
        population is served);
      * the routing table does not move — the three spellings that routed on the head keep
        routing, and `has anything ever been retracted` (a `been` frame) still ROUTES, so
        the arm did not turn a served population into a notice;
      * the ACTIVE-VOICE sibling stays SILENT on purpose (it carries no existential
        quantifier, so no ending cue is recognised — and it was silent on `main` too), and
        so does the ATTRIBUTIVE content lookup whose ending word modifies the quantifier's
        own noun.
    """
    from tortoise.state_intent import detect_state_question

    refused = [
        "have any reports already archived",
        "have any reports ever archived",
        "have any claims already retracted",
        "have any claims ever retracted",
        "have any reports already retracted",
        "have any reports ever withdrawn",
        "have any customers ever cancelled",
        "have any customers already cancelled",
        "have anything already archived",
    ]
    for query in refused:
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} routed to the {got.population!r} population — `already`/`ever` in the "
            f"auxiliary slot admits the ACTIVE reading, which the round-7 decision refuses")
        assert got.signal == "existence_unguarded", (
            f"{query!r} was refused but reports {got.signal!r} — `main` ROUTED this class, so "
            f"a silent refusal is #14033 duty (b): the caller cannot tell it from 'nothing "
            f"has ended'")

    # the routing table is untouched: a `been` frame, a copula and a `which` row still route
    for query, population, shape in (
        ("has anything ever been retracted", "ended", "existence"),
        ("is the export already archived", "ended", "is_copula"),
        ("which commitments were withdrawn", "ended", "which_predicate"),
    ):
        got = detect_state_question(query)
        assert (got.population, got.shape) == (population, shape), (
            f"{query!r} moved to {(got.population, got.shape)!r} — the notice arm cannot route, "
            f"so a move here means it was wired into the SHAPE table")

    # the two silences the arm must NOT reach, each for its own measured reason
    for query in (
        # no existential quantifier -> no ending CUE is recognised, and silent on `main` too
        "has the partner already cancelled",
        "has legal already retracted",
        # the ending word MODIFIES the quantifier's noun; the predicate is `published`
        "have any reports already archived been published",
        "have any archived reports been published",
        # `recently`/`just` are the same refusal class but were silent on `main` as well: the
        # arm covers `already|ever` ONLY, and widening it is a separate decision with its own
        # measurement, not a free extrapolation from this one
        "have any reports recently archived",
        "have any reports just archived",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal is None, (
            f"{query!r} moved to {got!r} — it is one of the silences this arm deliberately does "
            f"not reach, so the note on `_ALREADY_EVER_AUX` is now wrong")

    # ⛔ THE WINDOW BOUND IS A GUARD NOTHING ELSE HOLDS (fresh review, P2, measured): with
    # `[^?]{0,60}?` mutated to `*?` the whole suite stayed GREEN while 48 of 56 long-gap probes
    # changed behaviour, i.e. the bound was a claim with no test behind it. Both sides are pinned
    # here, so a widening reds instead of silently annotating every long question.
    for query, expected in (
        # window ~52 chars — inside the bound, so the cue is the question's and it is reported
        ("have any reports that the finance team reviewed last year already archived",
         "existence_unguarded"),
        # window ~69 chars — past the bound; the cue is not the question's own predicate window
        # any more, so it is NOT reported (and `main` was silent too)
        ("have any reports that the finance team archived last year before the merger "
         "already archived", None),
    ):
        got = detect_state_question(query)
        assert got.signal == expected, (
            f"{query!r} reports {got.signal!r}, not {expected!r} — the `{0,60}` window bound in "
            f"ARM C is unpinned again")

    # ⛔ THE REFUSAL SLOT'S BOUND IS `{0,10}`, NOT `{0,8}` (fresh review, P2, measured): the prose
    # claimed the slot WAS the helper's `{0,10}` while the code spelled eight, and the gap was
    # observable from the REPORT side — six and seven adverbials before the ending word were
    # `population=None, signal=None` (a refused cue with no notice, duty (b)). Both report now, and
    # the widening is refusal-side: measured over 78 probes (runs 0-12 x six sentence shapes),
    # 10 diffs, all silence -> `existence_unguarded`, 0 population/shape moves. At eight and beyond
    # the 60-character window dominates and both bounds answer identically — pinned below, because
    # "the window dominates" is the reason the bound cannot be measured there and a reader should
    # not infer coverage from it.
    for query in (
        "have any it has been just only already no longer in fact very recently deprecated and "
        "been withdrawn",
        "have any it has been just only already no longer in fact very recently still "
        "deprecated and been withdrawn",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal == "existence_unguarded", (
            f"{query!r} reports {got.signal!r} — a six- or seven-adverbial refusal must REPORT; if "
            f"this went silent, the refusal-side slot is back inside the bound the prose claims")

    # ⛔ THE ARM IS NOT PURELY ADDITIVE, AND THE DIRECTION IT IS NOT IS A NAMING ONE (fresh
    # review, P2, measured). The signal rows are ordered and this one sits before
    # `which_modified_noun`, so a COMPOUND question reports the existence notice instead:
    # `which_modified_noun` -> `existence_unguarded`, cue replaced. The POPULATION must not move —
    # that is the claim a signal arm can make — so that is the part pinned.
    compound = "which customer claims has been archived and have any reports already archived"
    got = detect_state_question(compound)
    assert got.population is None, (
        f"{compound!r} routed to the {got.population!r} population — ARM C is a signal arm and "
        f"cannot serve a population")
    assert got.signal == "existence_unguarded", (
        f"{compound!r} reports {got.signal!r} — a compound question with an `already|ever` frame "
        f"gets the existence notice, which is EARLIER in `_ENDING_SIGNALS` than "
        f"`which_modified_noun`; if this changed, the ordering note on ARM C is wrong")

    # ⛔ THE ATTRIBUTIVE RESIDUAL, STATED: silent only when the ending word is IMMEDIATELY
    # followed by `been`. A qualifier in between puts a `_BLOCKED_TAIL` member there and the
    # notice fires on a content query — the safe direction, and the same shape the pre-existing
    # `been` arm already reports (`have any reports been archived in Q3 been published`).
    for query in ("have any reports already archived in Q3 been published",
                  "have any reports already archived by legal been published"):
        got = detect_state_question(query)
        assert got.population is None and got.signal == "existence_unguarded", (
            f"{query!r} moved to {got!r} — the qualifier residual on ARM C is stated as a NOTICE "
            f"on a content query; if it changed, that note is wrong")


def test_a_contracted_auxiliary_does_not_silence_the_which_modified_noun_row():
    r"""The fourth copy of the finite-auxiliary class (fresh review, P2, measured).

    ``_WHICH_MODIFIED_NOUN`` insists the ending participle be preceded by the wh-phrase's
    OWN auxiliary, and that slot was hand-typed (``is|are|was|were|has|have|had``). A
    CONTRACTED auxiliary therefore failed to match, and because this row reports rather
    than routes, the failure was the module's worst direction — silence, not a wrong
    route:

        `which customer claims has been archived`     -> `which_modified_noun`
        `which customer claims hasn't been archived`  -> `population=None, signal=None`

    The slot is now interpolated from :data:`_CONTRACTED_AUX`, so the contraction is the
    same class as its un-contracted twin. The typographic apostrophe is exercised too:
    the module normalises the sender's inventory, and a spelling that accepted only ASCII
    would leave the class open for every phone keyboard that types `’`.
    """
    from tortoise.state_intent import detect_state_question

    pairs = [
        # (un-contracted twin, contracted twin) — the FIRST must not change, the second
        # must agree with it.
        ("which customer claims has been archived",
         "which customer claims hasn't been archived"),
        ("which vendor reports have been retracted",
         "which vendor reports haven't been retracted"),
        ("which board requests had been withdrawn",
         "which board requests hadn't been withdrawn"),
    ]
    for plain, contracted in pairs:
        want = detect_state_question(plain)
        assert want.population is None and want.signal == "which_modified_noun", (
            f"{plain!r} is the row's pinned baseline and now reads {want!r} — the "
            f"comparison below would be measuring the wrong thing")
        for spelling in (contracted, contracted.replace("'", "’")):
            got = detect_state_question(spelling)
            assert got.population is None and got.signal == "which_modified_noun", (
                f"{spelling!r} returned {got!r} while its un-contracted twin reported "
                f"`which_modified_noun` — the auxiliary slot is not the "
                f"`_CONTRACTED_AUX` class, so a contracted state question is SILENT, "
                f"which is #14033's own defect one guard over")
    # …and the discriminator the row exists for still holds: a subordinate clause puts a
    # LEXICAL verb where the auxiliary must be, contraction or not.
    for query in ("which vendor said our commitments were archived",
                  "which vendor said our commitments weren't archived"):
        got = detect_state_question(query)
        assert got.population is None and got.signal is None, (
            f"{query!r} reports {got.signal!r} — the two-word window that keeps the "
            f"subordinate-clause class out was widened along with the auxiliary slot")


def test_the_contraction_in_a_LONG_subject_stays_on_the_safe_side():
    r"""`_COPULA_LONG_SUBJECT` arm A is the ONE copy that deliberately omits contractions.

    Everywhere else the contraction class is interpolated from :data:`_CONTRACTED_AUX`
    because leaving it out costs a NOTICE. In arm A's negative lookahead the effect is
    the opposite — a second finite verb inside the subject SUPPRESSES the report — so
    interpolating the contractions was measured converting 1 180 rows from
    ``signal='is_copula_long_subject'`` to ``signal=None``, e.g.
    ``is the team the report hasn't been filed by archived``. A silent decline is the
    direction #14033 exists to remove, so the un-contracted run stays and this test
    makes the asymmetry a measurement rather than a sentence in a comment.
    """
    from tortoise.state_intent import detect_state_question

    # The un-contracted spelling is SUPPRESSED by the hand-typed run (`has` is a member).
    quiet = "is the team the report has not been filed by archived"
    got = detect_state_question(quiet)
    assert got.population is None and got.signal is None, (
        f"{quiet!r} reports {got.signal!r} — arm A's second-verb suppression no longer "
        f"holds for `has`, so the asymmetry this test pins has moved")
    # The CONTRACTED twin must stay on the SAFE side: a report, never a silence. If a
    # future round interpolates `_CONTRACTED_AUX` here "for consistency", this reds and
    # names the measured cost instead of quietly converting notices into silence.
    for query in (
        "is the team the report hasn't been filed by archived",
        "is the team the report hasn’t been filed by archived",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal == "is_copula_long_subject", (
            f"{query!r} returned {got!r} — `_COPULA_LONG_SUBJECT` arm A gained "
            f"`_CONTRACTED_AUX`, which was measured converting 1 180 reports into "
            f"SILENCE. That is the module's worst direction; if the change is wanted, "
            f"update the notice-scope paragraph and this test together")


# ══════════════════════════════════════════════════════════════════════════════
# #14033 — THE SIX PHRASINGS, the cue rule that widened, and the notice
# ══════════════════════════════════════════════════════════════════════════════

#: #14033's own table, measured on `origin/main` `e93f5df464`. The first five
#: ROUTE after this change (the top two already did); the sixth does not, because
#: its ending word modifies `claims` and the question's own predicate is
#: `affected pricing`. The sixth is exactly the row the missed-cue notice exists
#: for — it names a withdrawn record and the default currency filter hides it, so
#: a plain relevance reply there is an answer that hides the thing asked about.
THE_SIX = [
    ("has anything we relied on been withdrawn", "ended", "existence"),
    ("what ended", "ended", "wh_whole_question"),
    ("what has ended recently", "ended", "wh_whole_question"),
    ("is the onboarding flow retired?", "ended", "is_copula"),
    ("list what we retired", "ended", "imperative_what"),
    ("which of our withdrawn claims affected pricing", None, None),
]


@pytest.mark.parametrize(("query", "population", "shape"), THE_SIX)
def test_the_six_phrasings_of_14033_route_or_decline_as_measured(query,
                                                                population, shape):
    """#14033's table, executable: five route, the attributive one does not.

    ⛔ THE RULE THAT WIDENED AND THE RULE THAT DID NOT. Four new rows were added
    to the cue table and every one of them is still a PREDICATE anchored to its
    grammatical role — `is <record> <participle>?` (auxiliary opens, participle
    ends), `list what we <participle>` (a free relative clause, i.e. a set
    request), and `what <participle> recently` (the participle is the whole
    predicate; a RECENCY adverb names no window). What did NOT widen is the
    anchoring: the four content questions that must not route are pinned in
    `NOT_SELECTED` and re-asserted on the READ below.
    """
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population == population, (
        f"{query!r} selects {got.population!r}, not {population!r} — #14033")
    assert got.shape == shape
    # The sixth row is the one that DECLINES, and it must say so rather than be
    # silent: a state cue was seen and no shape could apply it.
    if population is None:
        assert got.signal == "which_names_an_ending", (
            "the attributive row declined WITHOUT reporting the cue it saw, so "
            "its relevance reply is indistinguishable from 'nothing ended'")


def test_a_strong_cue_that_does_not_fire_reports_itself_and_never_routes():
    """The NOTICE's trigger: a cue the recogniser SAW and could not apply.

    #14033's third ask. A state question that misses the cue table keeps the
    relevance read (the population cannot apply the caller's narrowing), but it
    must not be SILENT — an empty or unrelated relevance reply is otherwise
    byte-identical to the factual claim "nothing has ended".

    ⛔ `signal` IS A REPORT, NEVER A ROUTE, and that is the property this test
    pins: every query below carries a cue and NONE of them selects a population.
    Routing them would drop the qualifier (`in Q3`, `by legal`) or the caller's
    own predicate (`affected pricing`) — the hijack three review rounds removed.
    """
    from tortoise.state_intent import (
        MISSED_ROUTE_HINT,
        STATE_NOTICE_KEYS,
        detect_state_question,
        missed_route_payload,
    )

    blocked = [
        # an appended CONSTRAINT, on each shape that has a tail anchor
        ("have any claims been retracted in Q3", "existence"),
        ("has anything we promised been withdrawn by legal", "existence"),
        ("which claims were retracted by legal", "which_predicate"),
        ("is the onboarding flow retired by legal", "is_copula"),
        # ⛔ …AND THE `<prep> the <noun>` SPELLING OF THE SAME QUALIFIER, which the row
        # above did NOT cover and which review P1 measured as SILENT (not routed and not
        # reported): the subject guard read the qualifier's own noun phrase as a second NP
        # inside the subject, so `is the export archived by the vendor` came back
        # `signal=None` while `… by legal` (a BARE noun) was reported. A closed-list
        # qualifier that produces no notice is #14033's own defect on the shape this PR
        # adds, so the PREPOSITION CLASS is excluded here — it is not typed here any
        # more, it is interpolated from `_PREPOSITIONS` — one noun phrase, not two, and
        # the cue is REPORTED. ⛔ `during|until|as|than` were missing from the first
        # draft's hand-typed run (round-2 review, P2, measured), which is why the four
        # rows below exist. ⛔ AND THE CLASS HAS TWO REASONS FOR WHAT IT STILL MISSES
        # (review at `5b82bcdf03`, P2, measured); the guard's note states both:
        # `after|before|since` are PREPOSITIONS AND SUBORDINATORS, so the subordinator
        # lookahead refuses the shape first and excluding them from this list is a
        # NO-OP (measured: 0 of 277 probes move), while the conjunctions
        # `and|or|but|so|yet` are silent because the second-NP test reads a coordination
        # as one — a LIST decision (excluding them DOES report them: 5 probes flip, 0
        # pinned rows move). Both groups are pinned in `NOT_SELECTED`, the first by the
        # module-level preposition test and the second by
        # `test_the_conjunction_silence_is_a_LIST_decision_not_a_participle_bound`.
        ("is the export archived by the vendor", "is_copula"),
        ("is the billing service deprecated for the new plans", "is_copula"),
        ("is the Q3 export archived in the old sharepoint", "is_copula"),
        ("is the data archived on the old server", "is_copula"),
        # ⛔ THE FOUR `<prep> the <noun>` SPELLINGS MOVED TO `NOT_SELECTED` (re-cut review, P1,
        # measured): they report `is_copula` only while `during|until|as|than` are exempt from the
        # second-NP refusal — and that exemption is exactly what routed
        # `is the report as the board archived` to the whole ended population with no notice. A
        # silence that is a DELIBERATE trade is pinned where the silences live, not in the table
        # that asserts the cue is REPORTED.
        ("list what we retired in Q3", "imperative_what"),
        # …and the compound question the table has always refused
        ("what ended, and what replaced it", "wh_whole_question"),
        # …and a NAMED window: the recency adverbs the `wh_whole_question` shape
        # admits name NO boundary, so one that does (`yesterday`, `since
        # January`, `this month`) is still refused — and reported.
        ("what ended yesterday", "wh_whole_question"),
        ("what has been retired this month", "wh_whole_question"),
        # ⛔ …and a recency adverb PLUS a qualifier, which the round-6 review measured
        # as BOTH unrouted and unreported (the hand-written signal table dropped the
        # recency tail). The description advertises `what has ended recently` as a
        # route, so this is the exact string a caller would extend.
        ("what has ended recently in Q3", "wh_whole_question"),
        ("what ended recently by legal", "wh_whole_question"),
        ("which claims were archived recently", "which_predicate"),
        # …and a preposition the first function-word list omitted.
        ("has anything been withdrawn about our commitments", "existence"),
        # an ATTRIBUTIVE ending word inside a `which` question — #14033's row 6
        ("which of our withdrawn claims affected pricing",
         "which_names_an_ending"),
    ]
    for query, signal in blocked:
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} was ROUTED to {got.population!r} (shape {got.shape!r}) — "
            f"the notice must never become a route")
        assert got.signal == signal, (query, got.signal)

        payload = missed_route_payload(got)
        assert payload is not None
        assert payload["routed"] is False
        assert payload["population"] == "ended"
        assert payload["reason"] == "shape"
        assert payload["cue"], "the notice must name the words it saw"
        assert payload["hint"] == MISSED_ROUTE_HINT
        # ⛔ THE CLEARED KEY SET IS DERIVED FROM THIS PAYLOAD, so it is pinned HERE:
        # `sdk._tortoise_fts_query` clears a reused caller sink by KEY (never with
        # `dict.clear()` — the sink may carry the caller's own bookkeeping), using
        # `STATE_NOTICE_KEYS`. A key added to the payload without being added to that
        # constant would leave a STALE notice on a reused sink, which is the one thing
        # the clear exists to stop.
        assert tuple(payload) == STATE_NOTICE_KEYS, (
            f"payload keys {tuple(payload)} != STATE_NOTICE_KEYS {STATE_NOTICE_KEYS} — "
            f"the sink clear in `sdk._tortoise_fts_query` is key-scoped and would leak "
            f"the difference as a stale notice")

    # …and the notice is NOT emitted where there is nothing to report: an
    # ordinary content query (the four that must not route, and a record lookup)
    # produces no payload at all, so a content read's reply is unchanged.
    for quiet in (
        "have any customers cancelled this month",
        "do we archive customer emails",
        "launch event",
        "the meeting was cancelled",
        "is the archived pricing document live",
        # …`ended up` is not an ending: the participle must END the question, and
        # the word after it is neither punctuation nor a qualifier.
        "what ended up happening",
        # …and the YES/NO subordinate-clause class, which the first `is_copula`
        # window routed (round-6 review, P0). Each is a content question and none
        # of them now sees a state cue at all.
        "is the export broken because the schema was deprecated",
        "is it a problem that the endpoint is deprecated",
        "is it true that our claims were archived",
        "is there any chance the release was cancelled",
    ):
        got = detect_state_question(quiet)
        assert got.population is None and got.signal is None, (quiet, got)
        assert missed_route_payload(got) is None, (
            f"{quiet!r} carries no state cue, so a notice on it would be noise on "
            f"a content search")


    # …and a routed question reports nothing, because it WAS applied.
    for routed in ("what ended", "has anything been withdrawn"):
        got = detect_state_question(routed)
        assert got.population == "ended" and got.signal is None
        assert missed_route_payload(got) is None
    # …the other reason a cue can fail to be served is a family with no
    # lifecycle, and it is a distinct value on the same payload.
    from tortoise.state_intent import ENDED, StateQuestion

    family = missed_route_payload(StateQuestion(ENDED, "existence", "what ended"),
                                  reason="family")
    assert family["reason"] == "family" and family["routed"] is False
    with pytest.raises(ValueError):
        missed_route_payload(StateQuestion(ENDED, "existence", "what ended"),
                             reason="invented")


#: `_BLOCKED_TAIL`'s CONJUNCTION members — the SECOND of the two silent groups the
#: guard's note names, and the one whose cause is that guard itself rather than the
#: subordinator lookahead. Named here so the class is a class, not a set difference.
_CONJUNCTION_TAIL_MEMBERS = frozenset(("and", "or", "but", "so", "yet"))

#: `_BLOCKED_TAIL`'s TIME ADVERBS and DETERMINERS — the members whose `<member> the
#: <noun>` spelling is not an appended qualifier at all (`recently`, `this`). Named so
#: the partition asserted below can be read instead of inferred.
_NON_NOMINAL_TAIL_MEMBERS = frozenset((
    "last", "next", "this", "these", "those", "yesterday", "today", "tomorrow",
    "recently", "lately", "already", "ever", "now", "still", "previously",
    "earlier", "afterwards", "once", "then", "anymore", "ago",
))


def _the_two_guards():
    """`is_copula`'s subject guard, split into the two alternations it is built from.

    Read OUT of :data:`state_intent._COPULA_SUBJECT` rather than re-typed: the claim in
    the guard's note is a statement ABOUT those two lists, so a copy of them would only
    test the copy. It raises loudly when the pattern's shape changes, because that is the
    moment the note and this test must be re-derived together.
    """
    import re

    from tortoise import state_intent

    pattern = state_intent._COPULA_SUBJECT
    # ⛔ READ BY POSITION, NOT BY A CHARACTER CLASS (re-derived twice, and this is why):
    # the member class has stopped being a flat `[a-z|]+` list twice over — first the
    # INFINITIVE / PROGRESSIVE member carried a regex escape (review P1 at `42e0c1a662`),
    # then the CONTRACTED-AUXILIARY class arrived as its own nested group AND the
    # non-finite member grew a SECOND level of nesting (`to\s+(?:[a-z]+\s+){0,2}…`), which
    # no `\(\?:[^()]*\)` capture can span. So the reader slices the group out between its
    # own delimiters and splits it with a paren-DEPTH-AWARE splitter, which keeps ONE home
    # for the class: the drift between copies of this closed list is the failure this
    # branch has already paid for three times.
    head = pattern.index("{1,5}(?:") + len("{1,5}(?:")
    tail = pattern.index(")(?![-/])", head)
    subordinators = _split_alternation(pattern[head:tail])
    second_np = re.search(r"\{0,4\}\(\?!\(\?:([a-z|]+)\)", pattern)
    assert subordinators and second_np, (
        "`_COPULA_SUBJECT` no longer parses into its two alternations — re-derive this "
        "test AND the note it checks against the new pattern, rather than deleting the "
        "check: the note is a claim about exactly these two lists")
    # `to\s+be` is read back as `to be` so the set holds what a reader would write.
    return (frozenset(tok.replace("\\s+", " ") for tok in subordinators),
            frozenset(second_np.group(1).split("|")))


def _split_alternation(inner: str) -> list[str]:
    """Split a regex alternation on the `|`s at DEPTH 0, flattening `(?:…)` groups.

    A nested group can itself be an alternation of words (`(?:hasn't|…|shouldn't)`), so its
    members are flattened into the same list — that is what makes the resulting set the
    CLASS the guard actually refuses rather than the spelling the pattern happens to use.
    """
    tokens: list[str] = []
    depth = 0
    current = ""
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "|" and depth == 0:
            tokens.append(current)
            current = ""
        else:
            current += ch
    tokens.append(current)
    out: list[str] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("(?:") and tok.endswith(")"):
            out.extend(_split_alternation(tok[len("(?:"):-1]))
        else:
            out.append(tok)
    return out


def _blocked_tail_members():
    """`_BLOCKED_TAIL`'s wording, out of the regex fragment that defines it."""
    import re

    from tortoise import state_intent

    inner = re.search(r"\\b\(\?:([a-z|]+)\)\\b", state_intent._BLOCKED_TAIL)
    assert inner, (
        "`_BLOCKED_TAIL` no longer exposes its closed word list as one alternation — "
        "re-derive this test rather than skipping it")
    return frozenset(inner.group(1).split("|"))


def test_the_conjunction_silence_is_a_LIST_decision_not_a_participle_bound():
    """The OTHER silent tail group — refused HERE, not by the subordinator lookahead.

    ⛔ THIS PIN EXISTS BECAUSE THE GUARD'S NOTE GAVE BOTH SILENT GROUPS ONE REASON, AND
    FOR THIS GROUP THE REASON IS FALSE (review at `5b82bcdf03`, P2, measured). The note
    said a CONJUNCTION or SUBORDINATOR member still silences this shape "because guard A
    refuses inside the subject". For `and|or|but|so|yet` that is not what happens:

    * not one of the five is in the subordinator lookahead (asserted below, read out of
      the pattern, not restated);
    * what refuses them is the second-NP test reading `and the migration` as the start
      of a coordinated noun phrase;
    * so their silence is a LIST DECISION, not the participle bound that the three
      preposition/subordinator overlaps (`after|before|since`) are stuck behind —
      excluding the five from that one word list DOES report them.

    That distinction is not pedantry, it is the note's whole content and it decides what
    a later round can fix cheaply: routing these five is one list entry, whereas routing
    `after|before|since` needs the lookahead bounded at the participle, which is measured
    to re-route the round-16 hijack. Measured when the change was tried and reverted:
    **5 probes flip, 0 pinned rows move**, so the widening is safe — it is simply a
    recall decision with its own review, not a repair of a false claim.

    The partition assert is the other half: `_BLOCKED_TAIL` must be fully classified by
    the four sets named here, so a member added later cannot skip this question.
    """
    from tortoise import state_intent
    from tortoise.state_intent import detect_state_question, missed_route_payload

    subordinators, second_np = _the_two_guards()
    members = _blocked_tail_members()

    # ── the PARTITION: every table member belongs to exactly one of the declared groups.
    declared = (set(state_intent._PREPOSITIONS)
                | _CONJUNCTION_TAIL_MEMBERS
                | (subordinators & members)
                | _NON_NOMINAL_TAIL_MEMBERS)
    assert declared == members, (
        f"`_BLOCKED_TAIL` has unclassified {sorted(members - declared)} and stale "
        f"{sorted(declared - members)} — classify the new member here (is it a "
        f"preposition, a conjunction, a subordinator, or a time adverb?) so the guard's "
        f"note keeps describing the table that exists")

    # ── the CAUSE, read out of the guard rather than restated: this group is NOT
    # refused by the subordinator lookahead...
    assert not (_CONJUNCTION_TAIL_MEMBERS & subordinators), (
        f"{sorted(_CONJUNCTION_TAIL_MEMBERS & subordinators)} reached the subordinator "
        f"lookahead — the note's two-group split has to be re-derived: it states these "
        f"five are refused by the SECOND-NP test, not by that lookahead")
    # …and none of them is excluded from the second-NP test either, which is what makes
    # the silence a list decision rather than a property of English.
    assert not (_CONJUNCTION_TAIL_MEMBERS & second_np), (
        f"{sorted(_CONJUNCTION_TAIL_MEMBERS & second_np)} is now excluded from the "
        f"second-NP test, so the shape should REPORT it — if a round made that widening "
        f"deliberately, correct the guard's note (it states all five are still silent "
        f"and why) and these pins together")

    for member in sorted(_CONJUNCTION_TAIL_MEMBERS):
        # A FIXED FRAME, so the probe is mechanical rather than a claim about which
        # sentences people say. ⛔ NOT ALSO PINNED IN `NOT_SELECTED` — the earlier cut of
        # this comment said the natural spellings were, and they were not: the same commit
        # that added the `NOTICE_ONLY` subset invariant deleted them, because they measure
        # `signal is None` and that is what THIS loop asserts. A comment naming a list
        # that does not contain the rows is a claim nothing holds.
        query = f"is the export archived {member} the migration"
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} was ROUTED to {got.population!r} (shape {got.shape!r}) — this "
            f"shape REPORTS a tail, it never routes one: the population cannot apply "
            f"the caller's coordination (#14033's hijack direction)")
        assert got.signal is None and missed_route_payload(got) is None, (
            f"{query!r} now REPORTS {got.signal!r}: if a round deliberately routed the "
            f"conjunction class, update the guard's note (it states these five are still "
            f"silent) and `NOT_SELECTED` in the same commit — the point is that the note "
            f"and the behaviour never disagree")




def test_the_repetition_bound_is_not_NARROWER_than_the_bound_it_replaced():
    """The count bound must not re-open the hole it says it closes (review P3, measured).

    The helper this bound replaced was TWO `{0,5}` runs, i.e. effectively ten adverbial
    tokens. Shipping `{0,8}` while the comment called it a widening from five was a
    NARROWING of the guard by two tokens, and the direction that matters is the one that
    matters everywhere in this module: a question the guard no longer matches is ROUTED
    to the whole ended population with the caller's predicate dropped and `signal=None`.

    Asserted at the GUARD-REGEX level on purpose, and the consequence is stated honestly
    rather than overstated: the full detector's 60-character window dominates for a sentence
    this long, so END-TO-END the bound is UNOBSERVABLE at both `{0,8}` and `{0,10}` — measured
    across 5 heads x 6 adverbials x n=0..13 (420 probes), 0 differences in `detect_state_question`.
    What this pin holds is the FRAGMENT's property: the guard must not be narrowed below the
    bound it replaced, because the bound is what keeps a long adverb run from opening the hole
    the guard exists to close, and a future shorter head or a wider window would make it
    reachable. It does NOT claim a routing change, and neither does the module comment this
    mirrors — an earlier cut of both said "ROUTED", which the measurement did not support.
    """
    import re

    from tortoise import state_intent

    guard = re.compile(state_intent._EXISTENCE_GUARD)
    for n in (8, 9, 10):
        probe = "the vendor has " + "very " * n + "been deprecated"
        assert guard.match(probe) is None, (
            f"{n} adverbials did NOT match the refusal guard ({probe!r}) — the bound is "
            f"narrower than the ten-token bound it replaced. This is the guard FRAGMENT, not "
            f"a route claim: end-to-end the 60-char window dominates at this length, so the "
            f"narrowing is unobservable today and becomes a route the moment that changes")
    assert guard.match("the vendor has " + "very " * 12 + "been deprecated") is not None, (
        "the bound is unbounded — a count bound still has to keep the window from "
        "swallowing a whole clause")


def test_the_two_silences_the_comments_name_are_measured_not_hoped():
    r"""The recall the guards give up is STATED — so the stated examples must hold.

    Two comments in ``state_intent`` each justify a narrowing by naming what it is
    silent about (#14033 review round 10, P3, both measured):

    * an over-long subject REPORTS itself through ``_COPULA_LONG_SUBJECT`` on the
      ``is|are|was|were`` branch and is SILENT on the ``has|have|had … been``
      branch — the earlier wording promised the report for both, which the
      notice-scope paragraph contradicted;
    * a qualifier outside ``_BLOCKED_TAIL`` stays silent, and the examples named
      are genuinely outside it (``most recently``, ``only after the audit`` — a
      qualifier whose FIRST word is a member, like ``as yet`` via ``as``, is
      REPORTED, so naming it would invert the claim).

    A narrowing that silently loses recall it does not admit is the defect this
    module exists to remove, so the claim is pinned against the detector rather
    than trusted.
    """
    from tortoise.state_intent import detect_state_question

    long_subject = "the onboarding flow for enterprise partners"
    copula = detect_state_question(f"is {long_subject} retired")
    assert copula.population is None and copula.signal == "is_copula_long_subject", (
        f"the copula twin no longer reports itself: {copula!r} — the comment's "
        "'it REPORTS rather than going silent' would then be false")
    perfect = detect_state_question(f"has {long_subject} been retired")
    assert perfect.population is None and perfect.signal is None, (
        f"the over-long PERFECT subject now reports {perfect.signal!r} — if this is "
        "a widening, say so in the notice-scope paragraph and in the is_copula "
        "comment, which state that this branch stays silent")

    for silent in ("what ended most recently",
                   "which claims were retracted only after the audit"):
        got = detect_state_question(silent)
        assert got.population is None and got.signal is None, (
            f"{silent!r} is named as SILENT in `_BLOCKED_TAIL`'s note but reports "
            f"{got.signal!r} — `most recently` / `only after …` are the examples "
            "that stand outside the closed list")
    for reported in ("what ended as yet", "which claims were retracted as yet"):
        got = detect_state_question(reported)
        assert got.population is None and got.signal is not None, (
            f"{reported!r} is a refused constraint and must be REPORTED (its first "
            "word `as` is a `_BLOCKED_TAIL` member) — if the list lost it, the "
            "comment's '`as yet` is outside the list' example becomes true again")


def test_every_preposition_the_module_declares_reports_the_qualifier_it_introduces():
    r"""The PREPOSITION class has ONE home, and BOTH consumers are measured (round-25).

    #14033's defect is a state cue that does not fire and says nothing, and the
    ``is_copula`` subject guard and the notice's :data:`_BLOCKED_TAIL` each decide
    whether ``<participle> <prep> the <noun>`` is a refused constraint. Each carried
    its OWN hand-typed preposition run, and the two copies drifted twice — both
    caught by review, both after the fact:

    * round-1 P1 — ``is the export archived by the vendor`` was SILENT while
      ``… retired by legal`` (a bare-noun qualifier) was reported;
    * round-2 P2 — ``during``/``until``/``as``/``than`` were still silent after
      that fix, and the prose above the guard claimed the class was closed.

    A third hand-typed copy drifts a third time, so both sites now interpolate
    :data:`state_intent._PREPOSITIONS` and this test drives EVERY member through the
    detector. Drop one member from EITHER site and its row goes back to
    ``signal=None`` — which is why the pin is a loop over the declared class rather
    than a handful of examples (the earlier pins were examples, and that is how the
    first two omissions got through).

    ⛔ The DELIBERATE omissions are pinned too, so a later reader sees a decision
    rather than an oversight: ``after``/``before``/``since`` are prepositions AND
    subordinators, and their silence is the guard-A trade the module records.
    """
    from tortoise import state_intent
    from tortoise.state_intent import _CLAUSE_HEADING_PREPOSITIONS, detect_state_question

    declared = state_intent._PREPOSITIONS
    # ⛔ THE CLASS ITSELF IS PINNED, NOT JUST EACH MEMBER'S WIRING (round-26 review, P2,
    # measured). The loop below drives every member the tuple CURRENTLY holds, so it is a
    # self-reference: delete a member and the loop simply stops asking about it. Measured
    # before this block existed — removing `above` left `signal=None` (exactly #14033's
    # defect, restored) and this file stayed GREEN, because only the members that happen
    # to have their own row elsewhere (`by|for|in|on|during|until|as|than`) were pinned by
    # anything but their own presence. Narrowing the class is a DECISION; it must red
    # here and be argued, never quietly un-cover a spelling. Widening is pinned by the
    # same equality: a new member is new behaviour and needs its own review.
    assert declared == (
        "about", "above", "across", "against", "along", "alongside", "amid",
        "amidst", "among", "amongst", "around", "as", "at", "behind", "below",
        "beneath", "beside", "besides", "between", "beyond", "by", "concerning",
        "despite", "during", "except", "for", "from", "in", "inside", "into",
        "near", "notwithstanding", "of", "on", "onto", "outside", "over", "past",
        "per", "regarding", "than", "through", "throughout", "till", "to",
        "toward", "towards", "under", "underneath", "unlike", "until", "upon",
        "via", "with", "within", "without",
    ), (
        f"the declared preposition class changed to {len(declared)} members "
        f"({declared!r}). A member REMOVED un-covers the spelling it names and restores "
        f"#14033's silent cue; a member ADDED is new behaviour. Both are decisions: "
        f"change this tuple in the same commit and say why")
    assert len(set(declared)) == len(declared), (
        "a duplicated member is a copy-paste artefact, not a class")
    # ⛔ AND THE ROLE LIST ITSELF IS PINNED EXACTLY, in BOTH directions (review of this fix, P2,
    # measured). The block below asserts SILENCE for every member of
    # `_CLAUSE_HEADING_PREPOSITIONS`, which is exactly what a WRONG WIDENING produces — so a
    # member added to it traded a real notice for a silence with the whole file green (measured:
    # adding `along` kept this suite at 279 passed / 19 skipped while
    # `is the export archived along the migration` went from `is_copula` to `None`). The tuple
    # equality is what makes an addition a decision instead of an edit, the same rule
    # `_PREPOSITIONS` is held to above.
    assert tuple(_CLAUSE_HEADING_PREPOSITIONS) == ("as", "during", "than", "until"), (
        f"the clause-heading role list changed to {tuple(_CLAUSE_HEADING_PREPOSITIONS)!r}. A member "
        f"ADDED moves a preposition out of the qualifier exemption — its `<prep> the <noun>` "
        f"spelling stops being REPORTED as `is_copula` and becomes a silence, which is #14033's own "
        f"defect (measured: `along` did exactly that with this suite green). A member REMOVED "
        f"re-opens the clause route this pin exists to close. Both are decisions: change this "
        f"tuple in the same commit and say why")

    # ⛔ THE EXEMPTION CLASS IS THE DECLARED CLASS MINUS THE MEMBERS THAT CAN HEAD A CLAUSE
    # (re-cut review, P1, measured): `as`, `during`, `than` and `until` introduce a second CLAUSE
    # as readily as a qualifier, so exempting them let `is the report as the board archived`
    # become ONE copula subject and ROUTE the whole ended population with the caller's predicate
    # dropped and no notice — #14033's own defect, in the worst direction. The guard derives
    # its exemption from `_PREPOSITIONS` minus `_CLAUSE_HEADING_PREPOSITIONS`; those four are
    # graded by the block BELOW instead of this loop, and the `<prep> the <noun>` spelling they
    # cost is pinned in `NOT_SELECTED` rather than claimed as covered.
    for prep in declared:
        if prep in _CLAUSE_HEADING_PREPOSITIONS:
            continue
        # …the shape this PR adds, whose guard read a prepositional qualifier as a
        # second noun phrase and swallowed the cue.
        got = detect_state_question(f"is the export archived {prep} the migration")
        assert got.population is None, (
            f"{prep!r}: the qualifier was ROUTED to {got.population!r} — a narrowing "
            f"the read cannot apply is a silently-dropped constraint. If the class is "
            f"deliberately wider than the notice, it must not be: the notice is the "
            f"only channel that tells the caller the cue did not fire")
        assert got.signal == "is_copula", (
            f"{prep!r}: `is the export archived {prep} the migration` reports "
            f"{got.signal!r}, not 'is_copula' — that is #14033's own defect (a state "
            f"cue that does not fire and says nothing) on the shape this PR adds. "
            f"Both the `is_copula` subject guard and `_BLOCKED_TAIL` interpolate "
            f"`_PREPOSITIONS`, so this word was dropped from one of them")

        # …and the notice is not `is_copula`-only: `_BLOCKED_TAIL` is the tail every
        # tail-anchored shape swaps its end anchor for, so the SAME word must reach the
        # notice on a different shape — the second consumer, measured separately. (The
        # `has <subject> been <participle>` spelling reports `is_copula`, whose perfect
        # branch claims it first; the relative-clause window is what makes it `existence`.)
        other = detect_state_question(
            f"has anything we promised been withdrawn {prep} legal")
        assert other.population is None, (prep, other)
        # WHICH existence row reports is the window guard's business (`as` trips the
        # guarded shape and its unguarded twin reports) — the property this pin owns is
        # the one the class fixes: the word reaches the notice instead of going silent.
        assert other.signal in ("existence", "existence_unguarded"), (
            f"{prep!r}: `has anything we promised been withdrawn {prep} legal` "
            f"reports {other.signal!r} — the shape's own notice lost this preposition, "
            f"which `_BLOCKED_TAIL` inherits from `_PREPOSITIONS`")

    # ⛔ THE CLAUSE-HEADING MEMBERS: BOTH directions pinned, because the failure is directional
    # (re-cut review, P1, measured). (a) On the CLAUSE shape the shape must REFUSE — routing it
    # serves the whole ended population with the caller's predicate dropped and no notice
    # (measured: all four routed with `signal=None` before this fix) — and it refuses AND
    # reports `is_copula_long_subject`, which is what a reader can act on. (b) On the QUALIFIER
    # shape (`is the export archived as the migration`) the answer is `main`'s — SILENT — and
    # that is declared as a stated recall loss in `NOT_SELECTED`, never as coverage: telling "as
    # the migration" from "as the board archived" needs a predicate, not another word list.
    for prep in _CLAUSE_HEADING_PREPOSITIONS:
        assert prep in declared, (
            f"{prep!r} is in `_CLAUSE_HEADING_PREPOSITIONS` but not in `_PREPOSITIONS` — the "
            f"exemption is derived by subtraction, so a name that is not a member subtracts "
            f"nothing and the role list silently stops describing the class")
        clause = detect_state_question(f"is the report {prep} the board archived")
        assert clause.population is None, (
            f"{prep!r}: `is the report {prep} the board archived` was ROUTED to "
            f"{clause.population!r} — a second CLAUSE was read as one copula subject, which "
            f"serves the whole ended population with the caller's own predicate dropped")
        assert clause.signal == "is_copula_long_subject", (
            f"{prep!r}: the clause shape reports {clause.signal!r}, not "
            f"'is_copula_long_subject' — a refusal the caller cannot see is the same silence "
            f"this class exists to remove")
        qualifier = detect_state_question(f"is the export archived {prep} the migration")
        assert qualifier.population is None and qualifier.signal is None, (
            f"{prep!r}: `is the export archived {prep} the migration` now reports "
            f"{qualifier.signal!r}. It is pinned as a STATED silence in `NOT_SELECTED`; if that "
            f"is deliberately closed, remove the pin and say so there")

    # ⛔ THE DEPARTURE, PINNED SO IT READS AS A DECISION: these three are prepositions
    # in this position and still SILENCE the shape, because guard A refuses a
    # subordinator inside the subject and cannot tell a tail from a subject without
    # bounding itself at the participle (measured to re-route the round-16 hijack).
    # The notice is genuinely still owed for them — the module says so — and the day
    # that is closed, this block is what has to change.
    for overlap in ("after", "before", "since"):
        assert overlap not in declared, (
            f"{overlap!r} is a subordinator overlap — admitting it to the class is a "
            f"behaviour decision (it changes what `is the export archived "
            f"{overlap} the merger` reports), not a list completion")
        got = detect_state_question(f"is the export archived {overlap} the merger")
        assert got.population is None and got.signal is None, (
            f"{overlap!r} now reports {got.signal!r}: if the subordinator silence was "
            f"deliberately closed, say so in the guard-B note and in `_BLOCKED_TAIL`'s "
            f"limit paragraph, which both state it as still owed")

    # …and the particles must NOT be in it: `what ended up happening` is a content
    # query and `up` is a particle, not a preposition in that position.
    for particle in ("up", "down", "off"):
        assert particle not in declared, (
            f"{particle!r} is a particle (`what ended up happening`), not a "
            f"preposition introducing a narrowing — its admission reports a content "
            f"query as a state cue")


def test_a_relative_clause_in_the_perfect_still_routes():
    r"""The pronoun lookahead must not swallow the RELATIVE clause (#14033 round-15).

    The guard's pronoun alternative was added to refuse the ASYNDETIC hijack
    (`have any users reported it has been deprecated`, where the ending belongs to
    the relative clause and the caller's own predicate is dropped). Its first cut
    matched `we have been` in the RELATIVE form too — where the ending still belongs
    to the question — so the capstone question in the present perfect became a
    relevance read AND a notice whose remedy ("ask it as a whole question") is a
    runaround, because re-asking in the same legitimate form is refused again.

    The discriminator is what the pronoun clause's own `been` introduces: an ENDING
    participle means asyndetic (refuse), anything else means the relative clause is
    just describing the subject (route). Both directions are pinned here, because a
    one-sided pin is what let the regression in.
    """
    from tortoise.state_intent import detect_state_question, missed_route_payload

    for routed in (
        "has anything we have been tracking been withdrawn",
        "have any features we have been using been deprecated",
        "has anything they have been told about been withdrawn",
    ):
        got = detect_state_question(routed)
        assert got.population == "ended" and got.signal is None, (
            f"{routed!r} no longer routes ({got.population!r}/{got.signal!r}) — the "
            "pronoun lookahead is refusing a RELATIVE clause, whose participle is "
            "still the question's own predicate")
        assert missed_route_payload(got) is None, (
            f"{routed!r} now carries a notice on top of a relevance read — the "
            "remedy it names is a runaround, since the question is already in the "
            "form the ROUTES list asks for")
    for refused in (
        "have any users reported it has been deprecated",
        "have any customers noticed they have been cancelled",
    ):
        got = detect_state_question(refused)
        assert got.population is None and got.signal is not None, (
            f"{refused!r} ROUTES to the ended population again ({got!r}) — its "
            "ending belongs to the relative clause, and the caller's own predicate "
            "is dropped (#14033 round-14 review, P2)")


def test_a_missed_cue_names_words_it_saw_whole():
    r"""The notice's `cue` ends on a WORD boundary (#14033 review, P3).

    `signal_cue` is copied verbatim into `missed_route_payload`'s `cue`, which a
    caller reads in `read_status_out["state_question"]["cue"]` and in the hosted
    body's `state_question.cue`. Its contract is *"``cue`` names the words it
    saw"* — so a match ending inside the next word is a diagnostic that misnames
    what it saw. `which_names_an_ending` was the one signal that carried its
    trailing context INSIDE the match (`\s+[a-z]`), so `group(0)` stopped on the
    first letter of the following noun. Every other signal ends on a `\b`.
    """
    from tortoise.state_intent import (
        _APOSTROPHES,
        detect_state_question,
        missed_route_payload,
    )

    # The rows the review measured through the module's own functions.
    measured = {
        "which of our withdrawn claims affected pricing": "which of our withdrawn",
        "which archived report has the Q3 numbers": "which archived",
        "which archived reports were retracted": "which archived",
    }
    for query, want in measured.items():
        got = detect_state_question(query)
        assert got.signal == "which_names_an_ending", (query, got.signal)
        assert got.signal_cue == want, (query, got.signal_cue)

    # …and the PROPERTY, over every query that reports a notice — not only these
    # three, so a new notice row cannot reintroduce the truncation.
    for query in sorted(set(NOTICE_ONLY) | set(measured) | set(NOT_SELECTED)):
        got = detect_state_question(query)
        if not got.signal:
            continue
        cue = missed_route_payload(got)["cue"]
        assert cue, query
        # the detector lower-cases and collapses whitespace before it matches, so
        # the cue is a prefix of the NORMALISED query (a `SOC2` in the caller's
        # spelling arrives as `soc2`).
        # ⛔ AND IT IS NORMALISED ALL THE WAY, APOSTROPHES INCLUDED (round-28 review, P3,
        # measured): the detector translates the apostrophe inventory to ASCII before it
        # matches, so the cue for `hasn’t been deprecated` is `hasn't been deprecated` and
        # a bare `lower()+split()` comparison fails on a CORRECT cue — it passed only
        # because no pinned row carried a non-ASCII apostrophe. Compare through the
        # module's own table so a new row cannot slip past on the same technicality.
        normalised = " ".join(query.lower().split())
        for apostrophe in _APOSTROPHES:
            normalised = normalised.replace(apostrophe, "'")
        assert normalised.startswith(cue), (query, cue)
        rest = normalised[len(cue):]
        assert not rest or not (rest[0].isalnum() or rest[0] == "_"), (
            f"the cue {cue!r} for {query!r} is truncated MID-WORD ({rest[0]!r}) — a "
            f"diagnostic whose contract is 'cue names the words it saw' cannot "
            f"stop inside one")


def test_every_signal_is_its_shape_without_the_end_anchor():
    """The anti-drift falsifier for the notice: ONE expression, two tails.

    Each tail-anchored shape's signal is DERIVED from that shape — its own pattern
    with :data:`state_intent._TAIL_END` swapped for
    :data:`state_intent._BLOCKED_TAIL` — so a signal cannot describe a different
    cue than the shape it is named after. This test asserts the IDENTITY at the
    pattern level (not a handful of examples), because that is what the derivation
    buys and what a hand-written second list silently loses: the round-6 review
    measured the earlier hand-written table DROPPING the recency tail, so
    `what has ended recently in Q3` was neither routed nor reported while the
    description advertised `what has ended recently` as a route.
    """
    from tortoise import state_intent
    from tortoise.state_intent import detect_state_question

    shapes = dict(state_intent._ENDING_SHAPES)
    signals = dict(state_intent._ENDING_SIGNALS)
    tail_anchored = [n for n, p in state_intent._ENDING_SHAPES
                     if state_intent._TAIL_END in p.pattern]
    assert tail_anchored, "no shape carries the end anchor — this test proves nothing"
    for name in tail_anchored:
        want = shapes[name].pattern.replace(state_intent._TAIL_END,
                                           state_intent._BLOCKED_TAIL)
        assert signals[name].pattern == want, (
            f"the {name!r} signal is no longer that shape's own expression with its "
            f"tail swapped — a second, hand-written cue list is back:\n"
            f"  shape : {shapes[name].pattern}\n  signal: {signals[name].pattern}")
    # …and the rows that are NOT derived are named, so a new one cannot appear
    # silently: `do_support` has no end anchor to swap (it routes with a trailing
    # constraint, a pre-existing deliberate trade), and `which_names_an_ending` is
    # the attributive row.
    assert set(signals) - set(tail_anchored) == {
        "which_names_an_ending", "which_modified_noun", "is_copula_long_subject",
        # the UNGUARDED `existence` expression: the same cue, matched without the
        # clause guard, so a question the guard refused is reported, not silenced.
        "existence_unguarded"}

    # The BEHAVIOUR the identity buys, driven through the detector: each shape's own
    # routed example, given a qualifier, declines and REPORTS — including the
    # recency form that the hand-written table lost.
    for shape, query in {
        "existence": "has anything we relied on been withdrawn",
        "wh_whole_question": "what has ended recently",
        "which_predicate": "which claims were archived",
        "is_copula": "is the onboarding flow retired",
        "imperative_what": "list what we retired",
    }.items():
        assert detect_state_question(query).shape == shape, query
        blocked = detect_state_question(f"{query} in Q3")
        assert blocked.population is None, (shape, query, blocked)
        assert blocked.signal == shape, (
            f"the {shape!r} shape's own example, given a qualifier, reported "
            f"{blocked.signal!r} — that question is now BOTH unrouted and unreported")

    # ⛔ `do_support` IS THE ONE SHAPE WITH NO TAIL ANCHOR, so it has no blocked
    # state: a constraint after it is still served with the population. That is a
    # PRE-EXISTING, deliberately pinned trade (`what did we archive last quarter`
    # is a SELECTED row, and every returned row carries `endedAt`), recorded here
    # so the asymmetry is a decision rather than a gap the next reader "fixes".
    assert detect_state_question("what did we archive last quarter").shape == (
        "do_support")
    assert "do_support" not in signals


def test_the_mcp_description_predicts_which_of_the_six_route():
    """A caller reading ONLY the tool description can predict all six (#14033).

    The defect #14033 names is that the user-facing surface said only "an ending
    question reads the ended records", which invited exactly the failures its
    table measures. So the description now carries the phrasings in two groups —
    ROUTES and DOES NOT ROUTE — and this test reads those groups back out of the
    description and checks each row against the recogniser. A description that
    stops naming a phrasing, or files it under the wrong heading, reds here.

    ⛔ IT READS THE **SERVED** STRING, NOT `tortoise_search.__doc__` (#14033
    review, P1). `FastMCPAdapter.register_all` hands FastMCP
    `ToolDefinition.description`, and an explicit `description=` OVERRIDES the
    docstring (gofastmcp.com *Tools*: "when provided, FastMCP ignores the function
    docstring for the tool description") — so a docstring copy is invisible to
    every MCP caller. The first cut of this change put the block in the docstring
    and pinned it here; the row then passed no matter what the served text said,
    which is a false PASS over the exact deliverable the issue asks for. The
    docstring no longer carries the block at all — one home, checked below.
    """
    from tortoise.mcp_server import mcp as _mcp
    from tortoise.mcp_server import tortoise_search
    from tortoise.state_intent import ROUTING_NOTE, detect_state_question
    from tortoise.tool_registry import TOOL_REGISTRY

    # The artifact an MCP caller actually receives: the registry row's description,
    # which `FastMCPAdapter.register_all` passes to `FunctionTool.from_function(...)`.
    doc = next(t for t in TOOL_REGISTRY if t.name == "tortoise_search").description
    assert ROUTING_NOTE in doc, (
        "the served tortoise_search description does not carry the routing block — "
        "the recogniser owns it, the registry must serve it (#14033)")

    # ⛔ AND THE HOP THAT SERVES IT IS PINNED TOO (#14033 review, P2). The registry row
    # is only the INPUT: `register_all` passes `description=entry.description`, and with
    # that argument dropped FastMCP derives the text from the DOCSTRING instead — the
    # caller then receives a description with no routing block while the assertion above
    # stays green, because the row it reads never changed. Measured on this head: the
    # mutant `description=entry.description` → `description=None` serves 5190 chars with
    # no `WHICH PHRASINGS ROUTE`, and every assertion in this test still passed. So the
    # object FastMCP would hand a client is read directly.
    served = asyncio.run(_mcp.get_tool("tortoise_search")).description
    assert served and ROUTING_NOTE in served, (
        "the tool FastMCP serves does not carry the routing block: the registry row has "
        "it but the serving hop lost it (a `description=` that stopped being passed "
        "falls back to the docstring, which is not served text — #14033 review, P2)")

    # …and the docstring must NOT carry a second copy: prose there reaches no caller,
    # so a re-added copy reads like the contract while the served text drifts from it.
    # ⛔ THE ABSENCE PIN COVERS THE SECTION HEADS, THE RESTATED ROWS AND THE QUALIFIER
    # RULE — not one head string (#14033 review round 9, P2, measured; scope corrected
    # round 13, P2): the docstring used to restate the qualifier rule WITH both of its
    # examples, on the lines immediately above the paragraph claiming the contract "is
    # not written here", and the single-head check stayed green through it. ⛔ IT IS NOT
    # THE WHOLE TABLE, and the claim is now scoped because the docstring legitimately
    # NAMES two phrasings as illustrations of the ending question ("has anything been
    # withdrawn", "what ended"). ⛔ AND THE TWO ARE NOT THE SAME KIND OF ROW (round-13
    # delta review, P3 — this comment first said a whole-table pin "would red on those",
    # which is true of ONE of them): `what ended` IS a quoted row of `ROUTING_NOTE`
    # (measured: `re.findall(r"`([^`]+)`", ROUTING_NOTE)` contains it), while `has
    # anything been withdrawn` is NOT — the note's row is `has anything we relied on
    # been withdrawn` — so such a pin would red on the first and miss the second.
    # What is pinned is what must not come back: the two headings,
    # the specific rows that were restated here, and the qualifier rule.
    _docstring = tortoise_search.__doc__ or ""
    for _copied in (
        "ROUTES — answered from the ended population",
        "DOES NOT ROUTE — you get the relevance rows",
        "have any claims been retracted in Q3",
        "which claims were retracted by legal",
        "ask the question in its unqualified form",
        # …and the QUALIFIER RULE itself, which used to be restated in a parenthetical
        # on the line that claimed the contract is "not written here" (#14033 review
        # round 11, P2): the rule is served by `MISSED_ROUTE_HINT`, so a copy here is
        # the same drift the paragraph warns about.
        "carrying a narrowing the population cannot apply",
    ):
        assert _copied not in _docstring, (
            f"the phrase table is back in `tortoise_search.__doc__` ({_copied!r}), which "
            "FastMCP does not serve when the registry supplies `description=` — one "
            "home only (#14033 review, P2)")
    # ⛔ AND THE SERVED REMEDY NAMES THE SELF-HOSTED OMISSION (#14033 review round-9,
    # P3, measured). `selfhost_api`'s `GET /v1/search` takes the SAME path and routes
    # the cue too (the recogniser runs inside `_tortoise_fts_query`), but it passes no
    # sink, so a self-hoster reading "use the hosted `GET /v1/search` route" follows it
    # to a route that cannot report anything. The claim is pinned on BOTH sides: the
    # served text must say so, and the self-host route must still be the sinkless
    # caller that makes it true — wire a sink there and this reds, because the
    # sentence would then be false.
    assert "SELF-HOSTED `GET /v1/search`" in ROUTING_NOTE, (
        "`ROUTING_NOTE` no longer names the self-hosted route, so a self-hosted caller "
        "is told to use a remedy their own deployment cannot serve — and the omission "
        "is silent again (#14033 review, P3)")
    from tortoise import selfhost_api as _selfhost_api

    # …measured on the CALL, not on the text: the docstring above names the sink to say
    # it is NOT passed, so a source-substring check would red on its own explanation.
    _selfhost_calls = {
        kw.arg
        for node in ast.walk(ast.parse(textwrap.dedent(
            inspect.getsource(_selfhost_api.search))))
        if isinstance(node, ast.Call)
        for kw in node.keywords
    }
    assert "_state_route_out" not in _selfhost_calls, (
        "`selfhost_api.search` now passes `_state_route_out`, so the served sentence "
        "saying the self-hosted route carries no `state_question` is FALSE — update "
        "`ROUTING_NOTE` and the manifest row in the same change (#14033 review, P3)")
    routes_head = "ROUTES — answered from the ended population"
    declines_head = "DOES NOT ROUTE — you get the relevance rows"
    boundary_head = "SO A STATE QUESTION THAT DOES NOT ROUTE"
    # ⛔ THE THREE HEADS ARE PRESENT **AND IN THIS ORDER**. A bare `head in doc` loop is
    # tautological as a CLAIM — each head is a verbatim substring of `ROUTING_NOTE`, which
    # the assertion above already requires `in doc`, so it holds under any mutation of the
    # recogniser (#14033 review, P3). The ORDER is the claim that carries weight: the
    # slicing below reads ROUTES, then DOES NOT ROUTE, then the boundary. ⛔ NOT NEW
    # COVERAGE — a better DIAGNOSIS: a reorder also reds the per-phrasing loop below (the
    # slice collapses to `""` and every routed row reads as mis-filed; measured at
    # `6b5e46c7`, at the row assertion rather than here). What this buys is the CAUSE on
    # the first failing line instead of a row-by-row mis-filing report.
    _heads = [doc.index(head) for head in
              (routes_head, declines_head, boundary_head)]
    assert _heads == sorted(_heads) and len(set(_heads)) == 3, (
        f"the routing block's three sections are not in ROUTES → DOES NOT ROUTE → "
        f"boundary order (at {_heads}) — a caller reading them in order can no longer "
        f"predict which phrasings route (#14033)")
    routes = doc[doc.index(routes_head):doc.index(declines_head)]
    declines = doc[doc.index(declines_head):doc.index(boundary_head)]

    for query, population, _shape in THE_SIX:
        routed = population is not None
        # …the description files it under the right heading.
        region = routes if routed else declines
        assert query in region, (
            f"{query!r} is filed under the WRONG heading in the MCP description "
            f"(it {'does' if routed else 'does not'} route)")
        # …and the heading agrees with what the recogniser actually does.
        actually = detect_state_question(query).population is not None
        assert actually is routed, (
            f"the description says {query!r} "
            f"{'routes' if routed else 'does not route'}, but the recogniser "
            f"disagrees — the user-facing contract has drifted from the code")

    # ⛔ AND EVERY QUESTION THE DECLINES BLOCK NAMES IS CHECKED, not only the six:
    # a row filed as "an ordinary relevance search" while the recogniser routes it
    # is the dangerous direction (the description would tell a caller to expect a
    # relevance answer and hand them a population).
    import re as _re

    # ⛔ A SPAN THAT WRAPS ACROSS LINES IS PROSE, NOT A PHRASING (#14033 review, P2). The
    # block's explanatory parenthetical — "the question's own predicate is `affected
    # pricing`" — is backticked too, and a multi-line span is not a question: fed to the
    # recogniser it answers `population is None` under EVERY mutant, so an iteration over
    # it asserts nothing while the comment above claims every phrasing is checked. A real
    # phrasing in this block is always on one line, so the newline IS the discriminator.
    named = [
        span
        for span in _re.findall(r"`([^`]+)`", declines)
        if " " in span and "\n" not in span
    ]
    assert named, "the DOES NOT ROUTE block names no phrasings — nothing is pinned"
    for query in named:
        assert detect_state_question(query).population is None, (
            f"the MCP description files {query!r} under DOES NOT ROUTE, but it "
            f"does route — the description contradicts the code")

    # ⛔ AND THE CHANNEL IT NAMES MUST EXIST (#14033 review, P1b). The block used to
    # promise "the SDK, whose read-status sink carries the same payload" — no PUBLIC
    # SDK method accepts that sink, so the served text sent every agent after an
    # entry point that does not exist. Pin the corrected claim both ways.
    import tortoise.sdk as _sdk_mod

    assert "read_status_out" in doc and "_tortoise_fts_query" in doc, (
        "the served description must name the PRIVATE SDK entry point that carries "
        "the notice, or it promises a channel a customer cannot reach")
    assert "read_status_out" in inspect.signature(
        _sdk_mod.TortoiseSDK._tortoise_fts_query).parameters, (
        "the served description names `_tortoise_fts_query(read_status_out=…)` but "
        "that sink is gone from the method — the description is now false")
    public_with_sink = [
        n for n, m in inspect.getmembers(_sdk_mod.TortoiseSDK, inspect.isfunction)
        if not n.startswith("_")
        and "read_status_out" in inspect.signature(m).parameters
    ]
    assert not public_with_sink, (
        f"a public SDK method now accepts `read_status_out` ({public_with_sink}) — the "
        f"served description says no PUBLIC method exposes the notice; update the text "
        f"to name it rather than leaving the one channel that exists unadvertised")

    # ⛔ AND THE FILTER CONVERSE IS SERVED, NOT MERELY TRUE (#14033 review round-12, P2).
    # Routing is gated on NO retrieval knob being supplied (`sdk.py`
    # `_retrieval_knobs()`, the closed list), and the cue report is raised inside that
    # SAME guard — so a filtered state question gets neither the population NOR the
    # notice, while the served block listed the routes with no clause saying so. A
    # caller reading only this description therefore could not predict the outcome of
    # a filtered call, which is #14033's third acceptance criterion. The five names
    # below are the retrieval arguments that SILENCE the report — ⛔ EXAMPLES of the
    # closed list, never the set (round-18 review, P2: the served sentence called them
    # the set and named "the other three" as the remainder, while `_retrieval_knobs()`
    # classifies **23** parameters — so a caller who passed `pool_size` or `time_aware`
    # was told the report survived when it was silenced). The `_knob_names` set below
    # is DECLARED FROM `sdk.py` rather than re-typed, so the examples cannot drift off
    # the list they are examples of. The signature assertion is what keeps the named
    # five true: an argument that is renamed or dropped leaves the served clause
    # describing an argument nobody can pass.
    _filter_args = {"threshold", "min_confidence", "order_by",
                    "relationship_filter", "traversal_path"}
    assert _filter_args <= set(inspect.signature(tortoise_search).parameters), (
        "the served filter clause names arguments `tortoise_search` no longer takes — "
        f"the description now names a knob the caller cannot pass (params: "
        f"{sorted(inspect.signature(tortoise_search).parameters)})")
    for _name in sorted(_filter_args):
        assert _name in doc, (
            f"the SERVED description does not name `{_name}`, so a caller who passes it "
            "is not told that the cue report is suppressed — the filter/population "
            "boundary is over-promised again (#14033 review round-12, P2)")

    # ⛔ THE EXAMPLES MUST BE MEMBERS OF A BIGGER LIST, DERIVED FROM `sdk.py`
    # (#14033 round-18 review, P2 — the review's own measurement, made mechanical). The
    # condition this pins is not "the five are named" (they were, and the sentence was
    # still wrong): it is that the guard classifies MORE than the examples, so a served
    # sentence presenting them as the set is describing a different guard from the one
    # that runs. `_retrieval_knobs()` is a nested function of the SDK read, and each of
    # its rows is `("<name>", <condition>)`, so the names are read off the source and
    # never re-typed here — the same rule the module applies to its own tables.
    _knob_src = inspect.getsource(_sdk_mod.TortoiseSDK._tortoise_fts_query)
    _knob_names = set(_re.findall(r'\(\s*"([a-z_]+)",', _knob_src))
    assert _filter_args <= _knob_names, (
        "the served filter examples are no longer members of `_retrieval_knobs()` — "
        f"the description names a knob the guard does not classify (knobs: "
        f"{sorted(_knob_names)})")
    assert len(_knob_names) > len(_filter_args), (
        "`_retrieval_knobs()` now classifies no more names than the served description "
        "shows as examples — the round-18 P2 defect was that the text presented five "
        "of twenty-three as the set; if the list really is down to five, re-cut the "
        "text to say so instead of deleting this arm")

    # ⛔ AND THE CLAUSE ITSELF MUST BE TRUE, NOT MERELY NAME THE ARGUMENTS (#14033
    # round-13 review, P3). It first read "ANY RETRIEVAL ARGUMENT SILENCES THE
    # REPORT", which is FALSE at a value equal to the default — `_retrieval_knobs()`
    # classifies `threshold not in (0.0, 0)`, `min_confidence not in (0.0, 0)` and
    # `order_by != "relevance"`, so `threshold=0.0` passed EXPLICITLY is not a knob
    # and the report still fires (measured in
    # `test_a_retrieval_knob_suppresses_the_notice_too`). And a names-only pin could
    # not see an INVERSION of the sentence: flipping "AND ANY … SILENCES" to "AND NO
    # … SILENCES" left every assertion above green (reviewer-measured mutant).
    # So the qualifier and its carve-out are pinned as the CLAIMS they are, and the
    # behaviour behind them is pinned in the SDK test that drives the guard.
    # ⛔ NORMALISED FIRST: the served string is line-wrapped, and the phrase this pins
    # spans a wrap (`... THAT CHANGES THE\nREAD SILENCES ...`), so a raw `in doc` would
    # report a false failure on the very wording it is checking (measured).
    # ⛔ AND THE PIN IS THE WHOLE CLAIM, INCLUDING ITS POLARITY (#14033 round-13 DELTA
    # review, P2). A presence pin on the qualifier alone ("THAT CHANGES THE READ" +
    # "NON-DEFAULT") was measured BLIND to a negation: `AND NO RETRIEVAL ARGUMENT THAT
    # CHANGES THE …` left the whole file green (164 passed), i.e. a served sentence
    # saying the guard does NOT silence the report shipped. The regex below requires
    # the affirmative claim end to end — the qualifier, its verb and its object — so a
    # negation, a dropped "SILENCES THE REPORT", or a swap to "ANY" all red.
    _doc_flat = " ".join(doc.split())
    assert _re.search(
        r"AND A RETRIEVAL ARGUMENT THAT CHANGES THE READ SILENCES THE REPORT",
        _doc_flat), (
        "the SERVED description no longer states the affirmative claim \"a retrieval "
        "argument that CHANGES THE READ SILENCES THE REPORT\" — a negation, an \"ANY "
        "ARGUMENT\" wording or a dropped consequence all describe the guard "
        "differently from how it behaves (default-equal values do NOT silence it; "
        "non-default ones do) — #14033 round-13 review P3, delta review P2")
    assert "NON-DEFAULT" in _doc_flat, (
        "the SERVED description dropped the NON-DEFAULT qualifier, so it reads as if "
        "an explicit default value is a filter (#14033 round-13 review, P3)")
    assert "is not a filter and does not silence it" in _doc_flat, (
        "the SERVED description dropped the NEUTRAL-VALUE carve-out: a caller who "
        "passed `threshold=0` explicitly is then told they silenced a report that in "
        "fact still fires (#14033 round-13 review, P3)")

    # ⛔ AND THE SENTENCE MUST POINT AT THE CLOSED LIST RATHER THAN CLAIM TO BE IT
    # (#14033 round-18 review, P2 — the served text said the five were the set and
    # `kind`/`entity_type`/`limit` were "the other three", while `_retrieval_knobs()`
    # classifies 23: eighteen beyond the five also silence the notice and were named
    # nowhere, so `pool_size=` or `time_aware=` produced exactly the silence #14033
    # exists to remove while the description said the report survived). ⛔ THE PIN IS
    # THE AUTHORITY CLAIM, not a new subset: naming a different five would reproduce
    # the defect with a different spelling, and the arm above already requires the
    # examples to be members of a BIGGER list.
    assert _re.search(
        r"THE CLOSED LIST IS THE AUTHORITY, NOT THIS SENTENCE",
        _doc_flat), (
        "the SERVED description no longer says the closed list is the authority — that "
        "sentence is what stops a reader from treating the examples it names as the set "
        "(#14033 round-18 review, P2)")
    assert _re.search(r"`_retrieval_knobs` in `tortoise/sdk\.py`", _doc_flat), (
        "the SERVED description stopped naming `_retrieval_knobs` as the list — a reader "
        "is then back to a subset with no pointer to the guard that actually runs")
    assert _re.search(r"is an EXAMPLE of it, never the set", _doc_flat), (
        "the SERVED description lost the EXAMPLE-not-the-SET clause: the five names are "
        "then readable as the whole list again, which is the round-18 P2 defect")

    # ⛔ AND THE OTHER SIDE OF THE BOUNDARY — the three the POPULATION applies
    # (#14033 round-16 review, third residual; re-worded in round 18). "A RETRIEVAL
    # ARGUMENT THAT CHANGES THE READ SILENCES THE REPORT" is not true of `kind`,
    # `entity_type` or `limit`: the population applies them, so they change WHICH rows
    # a read returns and never whether the report fires (measured —
    # `test_a_retrieval_knob_suppresses_the_notice_too` drives `kind=` and reads the
    # sink). Without this carve-out the sentence tells a caller who narrowed by `kind`
    # that they silenced a report that in fact still fires.
    assert _re.search(r"Only THREE parameters sit OUTSIDE it", _doc_flat), (
        "the SERVED description dropped the `kind`/`entity_type`/`limit` carve-out, so "
        "the general \"A RETRIEVAL ARGUMENT THAT CHANGES THE READ SILENCES THE "
        "REPORT\" reads as covering arguments that do NOT silence it (#14033 "
        "round-16 review, third residual)")
    for _name in ("kind", "entity_type", "limit"):
        assert _re.search(rf"`{_name}`", _doc_flat), (
            f"the carve-out no longer names `{_name}`, so a caller who narrowed by it "
            "cannot tell whether the cue report still fires")
    assert _re.search(
        r"they change WHICH rows a read returns rather than asking for a filtered read",
        _doc_flat,
    ), (
        "the carve-out lost its AFFIRMATIVE claim — the names alone do not say what "
        "happens to the report, and a negation of this sentence would ship green"
    )


def test_the_bounded_cost_claims_name_every_scan():
    """The cost claims this module makes are PINNED to the tables they describe.

    #14033 review round-12, P3: the widening this branch shipped took
    `detect_state_question` from one shape table to three passes (the 6 ending
    shapes, then the 9 missed-cue signals derived from them, then the 3
    commitment shapes) on the DEFAULT read path of every SDK/MCP/hosted search —
    and two comments in the module still claimed otherwise: ``two linear regex
    scans`` in the module docstring and ``a scan of six compiled patterns`` on the
    function. Both were true on ``origin/main`` and false at the head, and nothing
    read either, which is why they drifted. A stale count in the module whose job
    is to be the ONE home for the routing contract is the drift #14033 is about,
    one level down.

    The numbers are DERIVED here from the tables themselves, so adding a row reds
    this instead of leaving the prose describing a smaller scan than the code
    performs. The module docstring deliberately carries NO count — the size has
    one home (the function that owns the scan), so there is nothing there to
    regress into a second, staler claim.
    """
    from tortoise import state_intent as si

    shapes = len(si._ENDING_SHAPES)
    signals = len(si._ENDING_SIGNALS)
    commitments = len(si._COMMITMENT_SHAPES)
    doc = " ".join((si.detect_state_question.__doc__ or "").split())
    for count, noun in ((shapes, "ending shapes"), (signals, "missed-cue signals"),
                        (commitments, "commitment shapes")):
        assert f"{count} {noun}" in doc, (
            f"`detect_state_question` no longer states `{count} {noun}`, so its cost "
            f"claim describes a scan the code does not perform (tables: "
            f"{shapes}/{signals}/{commitments} — #14033 review round-12, P3)")
    assert f"AT MOST {shapes + signals + commitments} compiled" in doc, (
        "`detect_state_question` no longer states the total it can scan per query "
        f"({shapes + signals + commitments}) — the bound is unstated again")
    module_doc = " ".join((si.__doc__ or "").split())
    assert "two linear regex scans" not in module_doc, (
        "the module docstring is back to a hard-coded scan count that no table backs "
        "— the size belongs to `detect_state_question`, the one home for the scan")
    assert "detect_state_question" in module_doc, (
        "the module docstring no longer points at the function that owns the scan "
        "order and its count, so the reader has no home to check the claim against")

    # ⛔ AND THE DERIVATION SENTENCE IS PINNED TO THE SPLIT IT DESCRIBES (#14033
    # round-13 review, P3; the derivation hardened by the round-13 DELTA review, P3).
    # The function said the 9 missed-cue signals were "derived from those shapes"
    # while 4 of them are HAND-WRITTEN recall rows with no shape at all — the count
    # was right and the derivation claim was not, and a count-only pin cannot see
    # that.
    #
    # ⛔ THE SPLIT IS READ OFF THE CODE'S OWN PREDICATE, never off the NAMES. It was
    # first written as `name in shape_names`, which certified a false sentence while
    # green: `do_support` IS an ending-shape name but is NOT tail-anchored, so a
    # hand-written recall row reusing that name was counted as DERIVED, and a
    # docstring claiming "6 derived" passed (measured: the tables hold 5 derived /
    # 4 hand-written). The construction in `_ENDING_SIGNALS` filters on
    # `_TAIL_END in shape.pattern` — the same test this pin now uses — so a name
    # collision cannot move the count, and the per-shape membership check below
    # reds if the derivation ever drops or duplicates a row.
    tail_anchored = [name for name, shape in si._ENDING_SHAPES
                     if si._TAIL_END in shape.pattern]
    signal_names = [name for name, _ in si._ENDING_SIGNALS]
    derived = [name for name in signal_names if name in set(tail_anchored)]
    hand_written = [name for name in signal_names if name not in set(tail_anchored)]
    for name in tail_anchored:
        assert signal_names.count(name) == 1, (
            f"the tail-anchored shape `{name}` produced {signal_names.count(name)} "
            f"missed-cue signals — the mechanical derivation is not one-to-one "
            f"(signals: {signal_names})")
    assert f"{len(derived)} derived" in doc and f"{signals} missed-cue signals" in doc, (
        f"`detect_state_question` no longer states that {len(derived)} of the "
        f"{signals} missed-cue signals are DERIVED from the ending shapes (derived: "
        f"{derived} — #14033 round-13 review, P3)")
    assert f"{len(hand_written)} hand-written" in doc, (
        f"`detect_state_question` no longer states that {len(hand_written)} of the "
        f"{signals} missed-cue signals are HAND-WRITTEN recall rows with no shape "
        f"(hand-written: {hand_written}) — the sentence would again claim every "
        f"signal is derived from a shape that does not exist for it")


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

    # ⛔ #14033: the refusal is REPORTED, not silent — a shape fired and the
    # population does not exist for this family, which is the second way a state
    # question ends up answered by the relevance read. Deleting the notice leaves
    # the rows unchanged, so a rows-only assertion cannot pin it (round-6 review,
    # P2): the sentinel here is the sink.
    notice: dict = {}
    sdk._tortoise_fts_query("has anything we relied on been withdrawn",
                           entity_type="event", limit=5,
                           _state_route_out=notice)
    assert reached == [], (
        "an Event-scoped ending question was served the ended population, which "
        "`ended.py` refuses to have — the guard keeping the route off a family "
        "with no lifecycle is gone")
    assert notice.get("reason") == "family", (
        f"the family refusal was not reported ({notice!r}) — the caller is told "
        f"nothing, which is #14033's silence in the one place a shape DID fire")
    assert notice["routed"] is False and notice["cue"]
    # ⛔ the family arm gets its OWN hint: a shape DID apply, so "no state-question
    # shape applied to it" would be false, and its remedy ("ask it as a whole
    # question") is the string the caller already asked. The assertions are on those
    # two claims, not on whether the word "event" appears.
    from tortoise.state_intent import MISSED_ROUTE_FAMILY_HINT, MISSED_ROUTE_HINT

    # ⛔ THE IDENTITY **AND** THE CONTENT, because they pin different things (#14033
    # review, P3): the identity pins WHICH constant the family arm is handed; it says
    # nothing about what that constant says. Turning `MISSED_ROUTE_FAMILY_HINT` back into
    # the shape remedy — the round-7 runaround, "ask it as a whole question", the string
    # the caller already asked — keeps the identity green (measured at `0f5bfbe6`: the
    # whole file passed), so the arm must be pinned on its content too. A review at that
    # head falsified an earlier revision of this comment, which claimed the probes below
    # "could not fail independently" of the identity; they can.
    assert notice["hint"] == MISSED_ROUTE_FAMILY_HINT != MISSED_ROUTE_HINT, (
        f"the family refusal was answered with the wrong hint ({notice['hint']!r}) — "
        f"the shape arm's remedy tells a caller to re-ask a form they already used")
    assert "no state-question shape applied" not in notice["hint"].lower(), (
        f"the family arm is serving the SHAPE remedy ({notice['hint']!r}): a shape DID "
        f"apply here, so \"no state-question shape applied\" is false and its remedy is "
        f"the runaround the caller already tried (#14033 round 7)")
    assert "lifecycle" in notice["hint"].lower(), (
        f"the family remedy no longer names WHY the population was refused "
        f"({notice['hint']!r}) — the caller cannot tell this from a shape miss")

    # POSITIVE CONTROL — the same question, on a family that HAS a lifecycle: the
    # population IS reached, and there is no notice to report.
    control: dict = {}
    sdk._tortoise_fts_query("has anything we relied on been withdrawn",
                           entity_type="point", limit=5, _state_route_out=control)
    assert reached == ["point"], (
        "the control did not reach the population, so the assertion above proves "
        "nothing about the guard")
    assert control == {}, (
        f"a ROUTED question carried a notice ({sorted(control)}) — the notice must "
        f"only appear where the population was not served")


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
# #14033 — the widened cues ON THE READ, both directions, and the notice
# ══════════════════════════════════════════════════════════════════════════════


#: The ended world #14033's read-level tests are measured against: one live note,
#: one retracted claim and one superseded decision, i.e. two endings of different
#: kinds (a status word, and a status word plus a named successor).
def _ended_world(sdk):
    live = sdk._create_point(kind="statement", content="the venue is booked")
    retracted = sdk._create_point(
        kind="statement",
        content="The beach club confirmed our 12 December booking.")
    sdk._retract_point(retracted["id"])
    old = sdk._create_point(kind="decision", content="the entry tier is 29")
    new = sdk._create_point(kind="decision", content="the entry tier is 39")
    sdk._supersede_point(old["id"], new["id"])
    return {"live": live, "retracted": retracted, "old": old, "new": new}


@_docker_only
def test_the_three_unfired_phrasings_of_14033_reach_the_ended_read(sdk):
    """#14033, acceptance 1 — on the READ, with how each record ended.

    Measured on `origin/main` `e93f5df464`, all three of these were ORDINARY
    searches: `what has ended recently` and `is the onboarding flow retired?` and
    `list what we retired` returned nothing useful, and the reply was
    indistinguishable from an honest "nothing has ended". Each now reaches the
    ended population, so the ended records arrive WITH their status / successor /
    `endedAt`, and the live record does not arrive at all.
    """
    world = _ended_world(sdk)

    for question in ("what has ended recently",
                     "is the onboarding flow retired?",
                     "list what we retired"):
        rows = sdk._tortoise_fts_query(question, entity_type="point", limit=50)
        by_id = {r["id"]: r for r in rows}

        assert world["retracted"]["id"] in by_id, (
            f"{question!r} did not reach the ended population — #14033's defect "
            f"is exactly this silence")
        assert by_id[world["retracted"]["id"]]["status"] == "retracted"
        assert by_id[world["retracted"]["id"]]["endedAt"], (
            "the ending time must be served where the record carries one")

        assert world["old"]["id"] in by_id
        assert by_id[world["old"]["id"]]["status"] == "superseded"
        assert by_id[world["old"]["id"]]["successorId"] == world["new"]["id"]

        assert world["live"]["id"] not in by_id, (
            f"{question!r} served a LIVE record — the ended population is the "
            f"answer, not the corpus")


@_docker_only
def test_the_four_content_questions_of_14033_do_not_route_on_the_read(sdk):
    """#14033, acceptance 2 — asserted on the READ, not on the helper.

    The four questions the table pins as MUST-NOT-ROUTE. The falsifier is the
    ended record: with the route fired, the ended population is served wholesale
    and the ended record is necessarily in the reply; with the relevance read,
    the shared currency predicate excludes it from the BASE retrieval, so it
    cannot appear at all. Each question's own live record is also asserted served
    where one exists, so "not routed" is not satisfied by an empty answer.

    ⛔ `which features are deprecated in v3` and `which claims were retracted by
    legal` DO carry a missed-cue notice (they name an ending state with a
    qualifier the population cannot apply). A notice is not a route: the rows are
    the relevance rows this test pins.
    """
    dead = sdk._create_point(
        kind="statement",
        content=("customers cancelled this month: an old claim we withdrew; "
                 "v3 features deprecated; we archive customer emails; "
                 "legal retracted the claims"))
    sdk._retract_point(dead["id"])
    live_archive = sdk._create_point(
        kind="statement", content="we archive customer emails on request")
    live_v3 = sdk._create_point(
        kind="statement", content="v3 features deprecated: the legacy export")
    live_cancel = sdk._create_point(
        kind="statement", content="customers cancelled this month: 12 subscriptions")

    content_questions = [
        ("have any customers cancelled this month", live_cancel),
        ("which features are deprecated in v3", live_v3),
        ("do we archive customer emails", live_archive),
        ("which claims were retracted by legal", None),
    ]
    for question, live in content_questions:
        rows = sdk._tortoise_fts_query(question, entity_type="point", limit=50)
        served = _ids(rows)
        assert dead["id"] not in served, (
            f"{question!r} was ROUTED to the ended population — the caller asked "
            f"a content question and was handed the whole ended set (#13558's "
            f"defect class, measured on four surfaces)")
        if live is not None:
            assert live["id"] in served, (
                f"{question!r} did not route AND its own record was not served — "
                f"the relevance read it keeps did not answer either")


@_docker_only
def test_a_missed_state_cue_is_distinguishable_from_an_honest_empty(sdk):
    """#14033, acceptance 4 — the notice's WHOLE point, on the read.

    Two replies that used to be identical: a state question the cue table could
    not apply, and a state question answered honestly empty. The first now carries
    a `state_question` notice naming the cue it saw; the second carries none, and
    its empty is the factual claim it always was.

    ⛔ The notice rides the caller-owned sinks and never a row: the rows are the
    relevance rows the caller would have received anyway, so nothing about the
    ANSWER changes — which is what keeps this from becoming the hijack.
    """
    world = _ended_world(sdk)

    # (1) a state cue the table SAW and could not apply (an appended qualifier).
    notice: dict = {}
    status: dict = {}
    rows = sdk._tortoise_fts_query("which claims were retracted by legal",
                                   entity_type="point", limit=50,
                                   _state_route_out=notice,
                                   read_status_out=status)
    assert notice, (
        "a state question that did not route came back with NO notice — its "
        "reply is indistinguishable from 'nothing has ended' (#14033)")
    assert notice["routed"] is False
    assert notice["reason"] == "shape"
    assert "retracted" in notice["cue"]
    assert notice["hint"]
    # …the READ is the relevance read, unchanged: the ended record is not served.
    assert world["retracted"]["id"] not in _ids(rows)
    # …and the same payload reaches the caller who asked for the read status.
    assert status["state_question"]["cue"] == notice["cue"]

    # (2) the honest empty: a question that DOES route, read on a graph where
    # nothing of the family has ended (a fresh namespace, so no record was
    # retired there). Same empty reply, NO notice — the two are distinguishable,
    # which is the whole acceptance.
    from tortoise.sdk import TortoiseSDK

    clean = TortoiseSDK(namespace=f"test13557_{uuid.uuid4().hex[:10]}")
    quiet: dict = {}
    quiet_status: dict = {}
    rows = clean._tortoise_fts_query(
        "has anything we relied on been withdrawn", entity_type="point", limit=50,
        _state_route_out=quiet, read_status_out=quiet_status)
    assert rows == [], (
        "the routed question must answer honestly empty on a graph where nothing "
        "has ended — a notice is not a substitute for an answer")
    assert quiet == {}, (
        f"an honestly empty ROUTED answer carried {sorted(quiet)} — the notice "
        f"must not appear where the recogniser applied the question")
    assert "state_question" not in quiet_status
    assert quiet_status.get("status") == "empty", (
        "the honest empty must say the store ANSWERED and found nothing, which is "
        "the status the notice-less reply carries")
    clean.close()

    # (3) and a CONTENT query carries nothing at all, in either sink — INCLUDING
    # a sink a previous call already wrote a notice into (a sink is caller-owned
    # and may be reused, so a stale notice would claim a cue nobody saw).
    plain_status: dict = {}
    plain: dict = {}
    sdk._tortoise_fts_query("launch event", entity_type="point", limit=5,
                            _state_route_out=plain, read_status_out=plain_status)
    assert plain == {} and "state_question" not in plain_status, (
        "a content query's reply changed — the notice may only appear where a "
        "state cue was seen")
    reused_status = dict(status)          # carries this test's earlier notice
    reused: dict = dict(notice)
    # ⛔ …and the CLEAR IS KEY-SCOPED (review P2, measured). The sink belongs to the
    # CALLER, so a reused dict may also carry that caller's own bookkeeping; the
    # hygiene this block performs must not destroy it. `dict.clear()` did, which is
    # what the code shipped before this row: the sibling `truncation.mark_truncation`
    # (the pattern the comment cites) writes its own keys and `pop`s its own stale one,
    # and never wipes the mapping.
    reused["caller_own_key"] = "kept"
    sdk._tortoise_fts_query("launch event", entity_type="point", limit=5,
                            _state_route_out=reused,
                            read_status_out=reused_status)
    assert reused == {"caller_own_key": "kept"}, (
        f"the clear took something it does not own: {sorted(reused)} (a stale notice "
        f"must go, a caller's own key must not)")
    assert "state_question" not in reused_status, (
        "a REUSED read-status sink kept a stale `state_question` on a content "
        "read — the cleared-then-decided rule is what stops that")


@_docker_only
def test_a_retrieval_knob_suppresses_the_notice_too(sdk):
    """A caller who supplied a knob asked for a relevance read — and is not second-guessed.

    #14033 routes, and NOTICES, only for a PLAIN question. The knob guard is the
    same closed list the ended arm refuses by, so a caller who typed
    `min_confidence=`/`threshold=` has said what they want; telling them their
    question looked like a state question would be the read arguing with its caller.

    ⛔ Pinned on the SINK, not on the rows: with the guard intact the rows are the
    relevance rows either way, so a rows-only assertion passes with the notice arm
    deleted (round-6 review, P2-4).
    """
    sink: dict = {}
    sdk._tortoise_fts_query("which claims were retracted by legal",
                            entity_type="point", limit=5, threshold=0.01,
                            _state_route_out=sink, read_status_out={})
    assert sink == {}, (
        f"a call that supplied `threshold` still carried the notice {sorted(sink)} — "
        f"the customer's own retrieval request was overridden by a guess")

    # …and the same question with NO knob does carry it, so the assertion above is
    # not satisfied by a notice that never fires.
    plain: dict = {}
    sdk._tortoise_fts_query("which claims were retracted by legal",
                            entity_type="point", limit=5, _state_route_out=plain)
    assert plain.get("reason") == "shape", plain

    # ⛔ AND `kind` IS NOT ONE OF THE SILENCING KNOBS (#14033 round-16 review, third
    # residual). `_retrieval_knobs()` classifies five arguments and NOT `kind`,
    # `entity_type` or `limit` — the population applies those, so they change which
    # rows come back and never whether the report fires. The served block states
    # that only because it was measured here: adding `("kind", kind is not None)`
    # to that list reds this arm.
    narrowed: dict = {}
    sdk._tortoise_fts_query("which claims were retracted by legal",
                            entity_type="point", kind="claim", limit=5,
                            _state_route_out=narrowed)
    assert narrowed.get("reason") == "shape", (
        f"`kind=` silenced the cue report ({narrowed!r}) — the SERVED description "
        f"says `kind`/`entity_type`/`limit` leave the report intact (#14033 "
        f"round-16 review, third residual)")

    # ⛔ AND THE SERVED CLAUSE'S CARVE-OUT IS THE MEASURED BEHAVIOUR, not a hope
    # (#14033 round-13 review, P3). The served text now says a retrieval argument
    # "THAT CHANGES THE READ" silences the report and that a default-equal value is
    # "not a filter and does not silence it" — this is the arm that makes that true.
    # `_retrieval_knobs()` classifies by VALUE (`threshold not in (0.0, 0)`), so a
    # caller who passes the default explicitly is still served the report.
    # ⛔ WHAT REDS IT, MEASURED RATHER THAN ASSUMED (#14033 round-13 delta review, P3):
    # `("threshold", threshold is not None)` reds this TEST, but at the PLAIN arm above
    # (`assert plain.get("reason") == "shape"`), not here — that arm passes the default
    # `0.0` too, so a value-based variant that treats an explicit `0.0` as a knob also
    # treats the default as one, and the plain arm fails first. Isolating THIS arm
    # needs a sentinel default in the signature (`threshold: float | None = None`, knob
    # `is not None`) — a signature change this PR does not make. The assertion is still
    # the one that would catch a "default-equal values are filters" reading in the
    # served text; it is simply not the first arm to red under that mutant.
    neutral: dict = {}
    sdk._tortoise_fts_query("which claims were retracted by legal",
                            entity_type="point", limit=5, threshold=0.0,
                            min_confidence=0.0, order_by="relevance",
                            _state_route_out=neutral)
    assert neutral.get("reason") == "shape", (
        f"a DEFAULT-equal value silenced the cue report ({neutral!r}) — the served "
        f"description says such a value \"is not a filter and does not silence it\", "
        f"so the text and the guard now disagree (#14033 round-13 review, P3)")


def test_the_missed_route_hint_states_only_what_the_read_did():
    """The served notice's REMEDY is content-pinned, not identity-pinned.

    #14033 round-13 review, P3. The `reason="shape"` arm was pinned only by
    `payload["hint"] == MISSED_ROUTE_HINT` — a constant compared with itself — so
    replacing the whole remedy with a FALSE statement ("this question was ROUTED
    to the ended population and is complete") shipped green, measured: 163 passed.
    The sibling `reason="family"` hint had been content-pinned for rounds; this
    arm had not, and it is the one that reaches the ATTRIBUTIVE rows whose whole
    remedy is the sentence.

    ⛔ AND THE FIRST CLAUSE IS THE CLAIM THE SERVED ROUTING BLOCK CONTRADICTED.
    `which archived report has the Q3 numbers` fires the `which_names_an_ending`
    SIGNAL (cue `which archived`) while `ROUTING_NOTE` files that phrasing under
    DOES NOT ROUTE — the ending word MODIFIES its noun and the question's own
    predicate is `has the Q3 numbers`. So the hint may report what was SEEN ("a
    strong ENDING cue was seen"), it may say no shape applied, and it MUST carry
    the QUALIFIER branch that tells that caller the relevance read is the right
    one. It may not tell them their question named an ending STATE.
    """
    from tortoise.state_intent import (
        MISSED_ROUTE_HINT,
        detect_state_question,
        missed_route_payload,
    )

    hint = MISSED_ROUTE_HINT
    flat = " ".join(hint.split()).lower()

    # The notice really does reach the attributive rows, with the cue it reports.
    for phrasing, cue in (
        ("which archived report has the Q3 numbers", "which archived"),
        ("which of our withdrawn claims affected pricing",
         "which of our withdrawn"),
    ):
        payload = missed_route_payload(detect_state_question(phrasing))
        assert payload is not None, (
            f"`{phrasing}` is an attributive row whose ending cue was seen and not "
            f"routed — the notice that IS its remedy no longer fires")
        assert payload["reason"] == "shape" and payload["cue"] == cue, (
            f"`{phrasing}` reported {payload!r}, not the attributive cue it saw")
        assert payload["hint"] == hint

    # WHAT IT MUST SAY — each clause is the caller's only remedy on this arm.
    for needle, why in (
        ("no state-question shape applied",
         "the caller is not told WHY they got the relevance rows"),
        ("relevance read",
         "the caller is not told which read answered"),
        ("not evidence that nothing has ended",
         "an empty reply is again indistinguishable from an honest \"nothing has "
         "ended\", which is the silence #14033 exists to remove"),
        ("qualifier",
         "the ATTRIBUTIVE branch is gone — the caller whose ending word MODIFIES "
         "its noun is not told the relevance read is the right one"),
        ("successorid",
         "the caller is not told what the population would have given them"),
    ):
        assert needle in flat, (
            f"MISSED_ROUTE_HINT no longer states `{needle}`: {why} "
            f"(hint: {hint!r})")

    # WHAT IT MUST NOT SAY — the round-13 false clause, and the round-13 mutant.
    assert "names an ending state" not in flat, (
        "MISSED_ROUTE_HINT is back to telling an ATTRIBUTIVE content question ("
        "`which archived report has the Q3 numbers`) that it \"names an ENDING "
        "state\", which the served ROUTING_NOTE contradicts for exactly that "
        "phrasing (it is under DOES NOT ROUTE) — the notice and the routing block "
        "would describe the boundary differently, which is the drift this module "
        "is the one home against")
    for forbidden in ("was routed", "routed to the ended population", "is complete"):
        assert forbidden not in flat, (
            f"MISSED_ROUTE_HINT claims `{forbidden}` — this arm fires precisely "
            f"because the population was NOT served, so that sentence is the "
            f"false reassurance the notice exists to prevent")


def test_the_hosted_search_body_carries_the_missed_cue(monkeypatch):
    """#14033 on the HOSTED surface — the only place the notice reaches a caller.

    ⛔ Hermetic, and pinned on the BODY. The SDK test above drives
    `_tortoise_fts_query` directly, so deleting the hosted emission left every test
    green while the one caller-facing surface went silent (round-6 review, P2-4).
    """
    import asyncio

    import tortoise.hosted_api as hosted_api

    payload = {"routed": False, "population": "ended", "reason": "shape",
               "cue": "which claims were retracted", "hint": "hint"}

    def _patch(monkeypatch, *, cue: bool):
        class _FakeSDK:
            def _get_proj(self):
                return object()

            def _tortoise_fts_query(self, q, limit=10, read_status_out=None,
                                    _truncation_out=None, _state_route_out=None,
                                    **_ignored):
                if _truncation_out is not None:
                    _truncation_out.update({"truncated": False})
                if cue and _state_route_out is not None:
                    _state_route_out.update(payload)
                return [{"id": "p1", "content": "alpha", "pointKind": "statement"}]

            def close(self):
                pass

        monkeypatch.setattr(hosted_api, "_data_sdk", lambda org: _FakeSDK())
        monkeypatch.setattr(hosted_api, "_require_scope",
                            lambda org, scope, name: None)
        return hosted_api

    hosted = _patch(monkeypatch, cue=True)
    body = asyncio.run(hosted.search(q="which claims were retracted by legal",
                                     limit=5, org={}))
    assert body["state_question"] == payload, (
        f"the hosted body carries {sorted(body)} — a state question that did not "
        f"route is indistinguishable from an honest empty on the one surface an "
        f"HTTP caller reads")
    assert body["count"] == 1 and body["truncated"] is False
    assert body["results"][0]["id"] == "p1"

    # …and a read with NO state cue is byte-identical: no field, nothing new.
    quiet = _patch(monkeypatch, cue=False)
    body2 = asyncio.run(quiet.search(q="launch event", limit=5, org={}))
    assert "state_question" not in body2, (
        f"a content search gained {sorted(set(body2))} — the notice may only "
        f"appear where a state cue was recognised")

    # ⛔ AND ``family`` IS NOT REACHABLE ON THIS ROUTE (#14033 review round 10, P3).
    # The body's own docstring listed it beside ``shape`` while the manifest row this
    # same change adds says it cannot occur — the route takes no ``entity_type``, so the
    # recogniser always sees the SDK's ``point`` default and ``point`` HAS a lifecycle.
    # Pinned on both halves the claim rests on: the signature (no ``entity_type``) and
    # the docstring (does not offer ``family`` as a possible value).
    _params = inspect.signature(hosted.search).parameters
    assert "entity_type" not in _params, (
        "`hosted.search` now takes `entity_type`, so `reason: family` MAY be reachable "
        "here — the docstring, the manifest row and its rendered doc all say it cannot "
        "(#14033 review, P3)")
    assert "or ``family``" not in (hosted.search.__doc__ or ""), (
        "the hosted body's docstring offers `reason: family` again, which this route "
        "cannot produce — a caller would branch on a value that never arrives "
        "(#14033 review, P3)")


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


#: The CONTRACTIONS that must defeat the asyndetic guard, one per member.
#: ⛔ THIS CLASS IS CLOSED AND ITS CLOSURE WAS MEASURED (round-26 review, P1): every
#: member below routed the whole ended population with the caller's predicate dropped
#: and `signal=None` — `have any users reported the vendor hasn't been deprecated` —
#: because the negation slot accepts a `not` TOKEN and a contraction carries its
#: negation INSIDE the auxiliary. All 17 un-contracted twins were already refused, so
#: the two spellings of one sentence disagreed.
_CONTRACTED_AUXILIARIES = (
    "hasn't", "hadn't", "haven't", "isn't", "aren't", "wasn't", "weren't",
    "doesn't", "didn't", "don't", "won't", "wouldn't", "can't", "couldn't",
    "mightn't", "mustn't", "shouldn't",
)

#: The adverbials round-24/round-25 measured, one per spelling. Kept as a LIST so the
#: set-equality pin below is a statement about the module's own table, not about these
#: rows: a token deleted from `_EXISTENCE_ADVERB` reds the pin even if some other
#: alternative happens to catch the row.
_MEASURED_ADVERBIALS = (
    "very recently", "quite recently", "almost", "just about", "in fact", "only just",
    "very", "too", "much", "all",
    "often", "always", "sometimes", "seldom", "rarely", "soon", "again", "twice", "then",
    "longer", "no longer", "to date", "of late", "at last",
)


def _existence_adverb_tokens():
    """`_EXISTENCE_ADVERB`'s alternatives, out of the regex fragment that defines it.

    ⛔ A WIDENING WHOSE ONLY JUSTIFICATION IS A COMMENT IS A RULE NOTHING HOLDS
    (round-26 review, P2, measured): the module's note says twelve spellings were
    "measured" and were added, but removing the round-25 tokens flipped only TWO pinned
    rows, so ten of the twelve could have been deleted silently. This returns the table
    itself so the assertion below is about the CLASS.
    """

    from tortoise import state_intent

    fragment = state_intent._EXISTENCE_ADVERB
    assert fragment.startswith("(?:") and fragment.endswith(")"), (
        f"`_EXISTENCE_ADVERB` no longer exposes itself as one alternation — re-derive "
        f"this test rather than deleting it: {fragment[:60]!r}")
    return frozenset(fragment[3:-1].split("|"))


def test_every_measured_adverbial_is_a_member_of_the_table_and_cannot_be_dropped():
    """The adverbial table is pinned as a CLASS, not as prose in a comment."""
    tokens = _existence_adverb_tokens()
    # The `-ly` pattern is the ONE member that is not a spelling; everything else is a
    # literal token, so the table can be compared exactly.
    expected_literals = {
        "just", "already", "only", "still", "even", "also", "never", "ever", "now", "yet",
        "very", "quite", "rather", "almost", "nearly", "too", "much", "all", "as", "so",
        "about", "in", "fact", "practice", "effect", "good", "long", "since", "far",
        "often", "always", "sometimes", "seldom", "rarely", "soon", "again", "twice",
        "then", "longer", "date", "late", "last", "first", "no", "to", "of", "at",
    }
    assert tokens - {"[a-z]+ly"} == expected_literals, (
        f"the adverbial table moved. Added: {sorted(tokens - {'[a-z]+ly'} - expected_literals)}"
        f" — Removed: {sorted(expected_literals - tokens)}. A token that leaves this set "
        f"is a spelling the guard goes blind to, which is the defect this table exists to "
        f"refuse; a token that joins it must come with the row that measured it.")
    assert "[a-z]+ly" in tokens, (
        "the `-ly` pattern left the table — every `-ly` adverbial (`quietly`, "
        "`officially`, `hitherto` is NOT one) would then need its own literal")


def test_the_asyndetic_gap_is_blind_to_neither_a_contraction_nor_a_measured_adverbial():
    """One row per spelled member, both classes, in the hijack frame.

    The frame matters: the caller's OWN predicate (`reported`) is what gets dropped when
    this guard fails, so every row here is a question whose answer would silently be the
    whole ended population.
    """
    from tortoise.state_intent import (
        _APOSTROPHES,
        detect_state_question,
        missed_route_payload,
    )

    for member in _CONTRACTED_AUXILIARIES:
        query = f"have any users reported the vendor {member} been deprecated"
        got = detect_state_question(query)
        assert got.population is None and got.signal, (
            f"{query!r} ROUTED the whole ended population with the caller's predicate "
            f"dropped (population={got.population!r}, signal={got.signal!r}) — a "
            f"contraction carries its negation inside the auxiliary, so it is invisible "
            f"to a gap that looks for the `not` TOKEN")
        assert missed_route_payload(got) is not None, (
            f"{query!r} is refused but reports nothing — a silent drop is the same defect "
            f"one layer down")
    # ⛔ THE SAME SENTENCE IN THE APOSTROPHE THE PRODUCT ACTUALLY RECEIVES (round-27
    # review, P1, measured): 384 of 384 rows in a 6-spelling × 8-subject × 8-verb
    # population routed on U+2019 while the ASCII twin was refused. `tortoise/
    # extractor_v2.py` normalises the same inventory before tokenising, for the same
    # reason; this module is the consumer that sees raw caller text.
    # ⛔ EVERY CODEPOINT THE NORMALISER TRANSLATES, asserted against the module's OWN
    # inventory rather than a hand-typed list: the round-27 fix shipped exercising four of
    # seven, so three could have been deleted from `_APOSTROPHES` with the suite green. A
    # pin derived from the constant cannot drift off it.
    for _ap in _APOSTROPHES:
        member = f"hasn{_ap}t"
        query = f"have any users reported the vendor {member} been deprecated"
        got = detect_state_question(query)
        assert got.population is None and got.signal, (
            f"{query!r} ROUTED (population={got.population!r}, signal={got.signal!r}) — "
            f"the apostrophe {_ap!r} is not U+0027, and a class closed over one codepoint "
            f"is not closed over the family the caller sends")
    # ⛔ AND THE INVENTORY IS PINNED AGAINST THE SENDER'S TABLE, NOT AGAINST ITSELF (fresh
    # review, P1, measured). The first cut asserted `len(_APOSTROPHES) >= 7` and looped over
    # that same tuple, so it was TAUTOLOGICAL about coverage: the seven handsome codepoints
    # passed while the EIGHTEEN `tortoise/extractor_v2.py` also folds (`\u201a \u201b \u02b9
    # \u2032 \u2035 \u00b4 \u02c8 \u05f3 \uff02 \ua78c …`) each still routed the whole
    # ended population with the caller's predicate dropped and `signal=None`. A pin that reads
    # the tuple it pins cannot see that the tuple is incomplete — the only failure this class
    # has. So the assertion is PARITY with the table the sender normalises with: a codepoint
    # added upstream reds HERE rather than quietly re-opening the hijack.
    from tortoise.extractor_v2 import _APOSTROPHES as _SENDER_INVENTORY

    _sender = {chr(codepoint) for codepoint in _SENDER_INVENTORY}
    _missing = _sender - set(_APOSTROPHES)
    assert not _missing, (
        f"the sender folds {len(_sender)} apostrophe codepoints and this normaliser covers "
        f"{len(_APOSTROPHES)} — missing {sorted(hex(ord(c)) for c in _missing)}. Each of "
        f"those still ROUTES the whole ended population with the caller's predicate dropped "
        f"and `signal=None`, which is the defect the normaliser exists to close")
    for _codepoint in _sender:
        _probe = f"have any users reported the vendor hasn{_codepoint}t been deprecated"
        _got = detect_state_question(_probe)
        assert _got.population is None and _got.signal, (
            f"{_probe!r} ROUTED (population={_got.population!r}, signal={_got.signal!r}) — "
            f"this codepoint IS in the sender's inventory, so it reaches this module")
    # ⛔ AND THE CLITIC SPELLINGS (`'s`, `'ve`, `'d`) — the commonest English form of
    # exactly these auxiliaries, and all of them routed before the clitic pattern.
    for query in (
        "have any users reported the vendor's been deprecated",
        "have any users said the feature's been deprecated",
        "have any customers said they've been cancelled",
        "has anyone noticed the plan's been withdrawn",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal, (
            f"{query!r} ROUTED (population={got.population!r}, signal={got.signal!r}) — a "
            f"clitic attaches to the SUBJECT, not to a separable auxiliary")
    for member in _MEASURED_ADVERBIALS:
        query = f"have any users reported the vendor has {member} been deprecated"
        got = detect_state_question(query)
        assert got.population is None and got.signal, (
            f"{query!r} ROUTED (population={got.population!r}, signal={got.signal!r}) — "
            f"this spelling was measured and added to the table, so it must stay closed")


def test_the_relativizer_lookahead_is_the_only_refusal_for_its_own_row():
    """The SECOND top-level lookahead, pinned by a row only IT can refuse.

    ⛔ A RULE NOTHING HOLDS IS NOT A RULE (round-26 review, P3, measured): deleting this
    lookahead flipped ZERO verdicts across the pinned corpus, because the asyndetic
    lookahead happens to cover every relativized row the corpus carried. The row below
    is the counter-example that gives it a job: its clause verb (`withdrew`) cannot be
    read as `<aux> … been`, so lookahead #3 cannot see it.

    ⚠️ STATED RESIDUAL, not a claim of correctness: this lookahead refuses on the
    PRESENCE of a relativizer in the window, so a relativized head —
    `have any users reported the vendor that has been deprecated` — is refused too,
    though its ending belongs to the relative clause and the question is legitimate.
    That over-refusal is pre-existing, silent and measured; closing it means telling a
    restrictive from a non-restrictive relative clause, which needs the lexicon this
    detector does not have.
    """
    from tortoise.state_intent import detect_state_question

    got = detect_state_question("has anyone asked that the vendor withdrew been archived")
    assert got.population is None and got.signal == "existence_unguarded", (
        f"the relativizer lookahead stopped refusing its own row: {got.population!r}, "
        f"{got.signal!r} — either restore it or delete it and this pin together")


def test_the_apostrophe_table_mirrors_the_extractors_inventory():
    """The apostrophe table is the EXTRACTOR'S inventory, not a re-derivation.

    ⛔ THE FIRST CUT TRANSLATED SEVEN SPELLINGS AND EIGHTEEN ROUTED (round-28 review, P2,
    measured): `extractor_v2._APOSTROPHES` normalises twenty-five codepoints before it
    tokenises, because the product actually receives them; this module is the other
    consumer that sees raw caller text, and it translated seven. `hasn‚t been deprecated`
    (U+201A) and `hasn‛t been deprecated` (U+201B) routed the whole ended population with
    the caller's predicate dropped and `signal=None` — spellings the module's own note
    claimed were covered.
    ⛔ NOT IMPORTED AT IMPORT TIME on purpose: `state_intent` is the hot read path and
    `extractor_v2` is a heavy module, so the two tables are kept equal by this assertion
    rather than by one importing the other.
    """
    from tortoise.extractor_v2 import _APOSTROPHES as _EXTRACTOR_APOSTROPHES
    from tortoise.state_intent import _APOSTROPHES as _STATE_INTENT_APOSTROPHES

    ours = {ord(c) for c in _STATE_INTENT_APOSTROPHES}
    # the extractor's table is a dict keyed by codepoint
    theirs = set(_EXTRACTOR_APOSTROPHES)
    assert ours == theirs, (
        f"the apostrophe inventories drifted apart. Only in `state_intent`: "
        f"{sorted(hex(c) for c in ours - theirs)} — only in `extractor_v2`: "
        f"{sorted(hex(c) for c in theirs - ours)}. A codepoint the extractor normalises "
        f"and this module does not is a spelling the asyndetic guard is blind to, which is "
        f"the hijack #14033 exists to refuse; add it to `_APOSTROPHES` rather "
        f"than trimming this assertion")


def test_a_bare_noun_clitic_reports_instead_of_being_served_by_the_copula_shape():
    """The copula shape must not swallow a subject that carries the auxiliary clitic.

    ⛔ ONE ARTICLE APART, OPPOSITE VERDICTS (round-28 review, P1, measured):
    `_COPULA_SUBJECT`'s word class admits `'`, so it ate `billing's` whole and the copula
    shape matched the FULL string on the first pass — `have any users reported billing's
    been deprecated` returned the ended population with the caller's predicate dropped and
    `signal=None`, while `have any users reported the billing's been deprecated` was
    refused with the notice. The guard could not help: the copula shape is matched BEFORE
    it and never consults it. Measured over a 2,400-row cross-product (5 heads x 8 bare
    nouns x 5 participles x 6 clitic spellings x det/non-det): 1,200 routed, ALL of them
    the bare-noun half.
    """
    from tortoise.state_intent import detect_state_question, missed_route_payload

    for query in (
        "have any users reported billing's been deprecated",
        "have any users said v3's been deprecated",
        "have any users reported the search index's been deprecated",
        "has anyone complained billing's been withdrawn",
        "have any users reported billing’s been deprecated",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal, (
            f"{query!r} ROUTED (population={got.population!r}, shape={got.shape!r}, "
            f"signal={got.signal!r}) — its determiner twin is refused, so the pair "
            f"disagreeing is the defect, not the spelling")
        assert missed_route_payload(got) is not None, (
            f"{query!r} is refused silently — a cue that reaches no answer must say so")
    # …and the plain copula questions this exclusion could have broken still route.
    for query in ("is the export archived", "is all the data archived",
                  "is it archived", "is the export no longer archived"):
        assert detect_state_question(query).population == "ended", query


def test_a_possessive_subject_is_not_read_as_an_auxiliary_clitic():
    """The copula subject may carry a POSSESSIVE `'s`; only `'s been` is the auxiliary.

    ⛔ THE EXCLUSION THAT CLOSED THE BARE-NOUN CLITIC BROKE THIS CLASS (round-29 review,
    P1, measured): applying `(?![a-z0-9'’._/-]*['’](?:s|ve|d)\b)` to EVERY subject word
    refused `is the board's decision archived`, `is Q3's report archived` and
    `is O'Brien's claim retracted` — 800 of 800 rows in a 4-head x 2-det x 20-subject x
    5-participle population — and did it SILENTLY (`signal=None`, so not even a notice).
    Those are the multi-word subjects the slot was widened to admit. The exclusion is now
    anchored on the following `been`, which is what distinguishes the auxiliary reading
    (`billing's been deprecated`) from a possessive (`the board's decision is archived`).
    """
    from tortoise.state_intent import detect_state_question

    for query in (
        "is the board's decision archived",
        "is the vendor’s team archived",
        "is Q3's report archived",
        "is O'Brien's claim retracted",
        "is last year's report superseded",
        "is the team's plan archived",
        "is the search index's owner retracted",
    ):
        got = detect_state_question(query)
        assert got.population == "ended", (
            f"{query!r} is NOT served ({got.population!r}, shape={got.shape!r}) — its "
            f"subject carries a POSSESSIVE, not an auxiliary clitic, and refusing it "
            f"silently is the same defect this file pins in the other direction")
    # …and the auxiliary reading stays refused, so this pin cannot be satisfied by
    # widening the slot back.
    for query in (
        "have any users reported billing's been deprecated",
        "have any users said v3's been deprecated",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal, query


def test_an_adverb_between_the_clitic_and_been_does_not_reopen_the_hijack():
    """`billing's JUST been deprecated` — the interruption must not restore the hijack.

    ⛔ THE ADJACENCY ANCHOR WAS DEFEATED BY ONE ADVERB (round-30 review, P1, measured):
    `\\s+been\\b` only excluded a clitic IMMEDIATELY followed by `been`, so
    `has billing's just been deprecated` was admitted to the copula subject slot — whose
    word class accepts the literal `been` — and the row served the whole ended population
    with the caller's predicate dropped and `signal=None`. Over a 37,800-row clitic
    population, 17,568 rows newly routed and 1,296 that had been refused WITH a notice
    (`is_copula_long_subject`) became silent. The gap now tolerates up to three adverbial
    words, and `been` is forbidden as a subject word.

    ⛔ AND THE PLAIN INTERROGATIVE SPELLING IS NO LONGER SILENT (round-31 review, P2, measured):
    this paragraph used to say that spelling (`has billing's just been deprecated`, with no
    caller predicate) is "now SILENT rather than served". The same round widened the NOTICE
    arm's gap to the guard's own `{0,3}` words, so it is refused AND REPORTED — `population=None`,
    `signal='existence_unguarded'`, payload present — and the pins beside
    `test_a_refused_clitic_subject_REPORTS_with_a_word_between_it_and_been` assert that report.
    It must still never be SERVED by re-widening the subject slot, which is what this paragraph
    is about.

    ⚠️ ON THE ISSUE POINTER, CORRECTED (round-31 review, P2, measured): this docstring also said
    the spelling was "Recorded in `daniel-ospina/tortoise#14292` beside the other contracted-head
    shapes". #14292 records four shapes and NONE of them is this adverb-bearing clitic spelling
    (its clitic row is the ADJACENT `billing's been withdrawn`), and two of its four rows — the
    contracted head and the bare-noun clitic — are FIXED at this head while its body still says
    all four reproduce on `origin/main`. Round 31 posted the measured status there rather than
    leave a pointer to a document that no longer describes the code.
    """
    from tortoise.state_intent import detect_state_question, missed_route_payload

    # THE HIJACK FRAME: must be refused AND report, never served.
    for query in (
        "have any users reported billing's just been deprecated",
        "has anyone complained the vendor's recently been deprecated",
        "have any users reported the team's just been retired",
        "have any users said the plan's very recently been superseded",
        "have any users reported billing's never been deprecated",
    ):
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} served {got.population!r} — an ADVERB between the clitic and "
            f"`been` re-opened the asyndetic hijack")
        assert got.signal, (
            f"{query!r} is refused but reports nothing (signal={got.signal!r}) — a silent "
            f"drop is the direction this module rates worst")
        assert missed_route_payload(got) is not None, query
    # …and the STATEMENTS that must not be swallowed by the same slot stay unrouted.
    for query in ("has billing's just been deprecated",
                  "has the vendor's recently been deprecated"):
        assert detect_state_question(query).population is None, (
            f"{query!r} is now served — that direction re-opens the hijack above")


def test_a_refused_clitic_subject_REPORTS_with_a_word_between_it_and_been():
    """A refusal that reports nothing is invisible — the cue must survive one word.

    ⛔ THE NOTICE ARM REQUIRED ADJACENCY (round-31 review, P1/P2, measured). It matched
    `['’](?:s|ve|d)\\s+been`, so ONE word between them blinded it and the row was refused
    with NOTHING reported — no population, no signal, no payload:
      * the POSSESSIVE-SUBJECT class whose noun collides with the adverb table or ends in
        `-ly` — `has the client's supply been archived` (2,400 of 5,600 rows silent);
      * the HIJACK frame when its head is not `any`/`anything` — `has support confirmed
        billing's just been retired` (240 of 400 silent).
    ⛔ AND NOTHING PINNED IT: reverting the arm to the adjacent-only form left the suite
    GREEN, because the neighbouring pin asserts only `population is None` on its statement
    rows. This pin asserts the REPORT — which is what the arm exists for. A notice arm can
    only add a report (a signal is computed only after every shape has declined), so
    pinning it cannot hide a routing change.
    """
    from tortoise.state_intent import detect_state_question, missed_route_payload

    rows = (
        # the possessive-collision class (noun looks like an adverb-table member)
        "has the client's supply been archived",
        "has the vendor's practice been archived",
        "has the invoice's date been archived",
        "has the board's last been archived",
        "has the client's reply been retired",
        # …and the non-`any` hijack frames
        "has support confirmed billing's just been retired",
        "have we heard billing's just been retired",
        "has the board been told billing's just been retired",
        "has support said the vendor's recently been retired",
        # …and the statement spelling the neighbouring pin only checks for silence
        "has billing's just been deprecated",
        "has the vendor's recently been deprecated",
    )
    for query in rows:
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} served {got.population!r} — a notice arm cannot route; a "
            f"population here means a shape matched first")
        assert got.signal, (
            f"{query!r} is refused with NO notice (signal={got.signal!r}) — the caller "
            f"cannot tell an empty answer from a cue that was seen and declined")
        assert missed_route_payload(got) is not None, (
            f"{query!r} reports a signal but no payload — a notice a caller cannot read "
            f"is the same silence one layer down")


def test_the_clitic_notice_gap_is_the_guard_s_separator_and_covers_modal_heads():
    """The notice arm's gap must be as wide as the GUARD's, or the silence survives.

    ⛔ ONE GAP SPELLED IN TWO PLACES DRIFTS (round-32 review, P3, measured). The guard
    (`_COPULA_SUBJECT`) treats `,` as the same gap the clitic rule uses, but the notice
    arm's token class was `[a-z0-9'’._/-]+` and its separator was `\\s+`, so a comma, a
    non-ASCII letter, `#` or `$` in the gap blinded the arm and the row was refused with
    NO report: `has the vendor's recently, been deprecated`, `has the client's café been
    archived`, `has the vendor's report #12 been archived`, `has the client's $5 fee been
    archived`, `has billing's, just, been deprecated`.
    ⛔ AND A MODAL PERFECT IS THE SAME CUE: the head class was
    `has|have|had|is|are|was|were`, so `could the vendor's supply have been archived` and
    `will the client's report have been archived` reported NOTHING.
    Both widenings are on a SIGNAL arm, which is computed only after every shape has
    declined — so they can only add a report, never a population.
    """
    from tortoise.state_intent import detect_state_question, missed_route_payload

    for query in (
        "has billing's, just, been deprecated",
        "has the vendor's recently, been deprecated",
        "has the client's supply, been archived",
        "has the client's café been archived",
        "has the vendor's report #12 been archived",
        "has the client's $5 fee been archived",
        "has the client's supply been archived",
        "has billing's just been deprecated",
    ):
        got = detect_state_question(query)
        assert got.population is None, (
            f"{query!r} served {got.population!r} — a SIGNAL arm cannot route; a "
            f"population means a shape matched first and this assertion is moot")
        assert got.signal, (
            f"{query!r} is refused with NO notice (signal={got.signal!r}) — the gap "
            f"between the clitic and `been` is wider in the GUARD than in this arm, so "
            f"the spellings the guard admits keep the silence the arm exists to remove")
        assert missed_route_payload(got) is not None, query
    # …and the modal perfects.
    for query in (
        "could the vendor's supply have been archived",
        "will the client's report have been archived",
        "should the invoice's date have been archived",
        "will billing's just been deprecated",
    ):
        got = detect_state_question(query)
        assert got.population is None and got.signal, (
            f"{query!r} reports nothing ({got.population!r}, {got.signal!r}) — a modal "
            f"perfect carrying `been <ending>` is the same state cue")
    # …while every served row stays served, so the widening bought no serving.
    for query in ("has the client's contract been archived", "is the export archived",
                  "has anything we have been tracking been withdrawn", "what ended",
                  "which claims were archived"):
        assert detect_state_question(query).population == "ended", query
