"""#10360 — the verbatim state-value guarantee, enforced in the WRITE PATH.

What this file pins, and why each pin is here rather than in a comment:

1. **The rule.** ``_value_slot_divergence`` fires exactly on a same-slot
   contradiction (the point states a DIFFERENT value than its own verbatim
   quote in the slot the quote binds) and fails open everywhere else. The
   tolerance battery below is the one the previous attempt at this issue was
   rejected for failing: string identity refused truthful points for
   ``4.0``/``4``, ``1200``/``1,200``, ``20%``/``20 percent``,
   ``800ms``/``800 milliseconds`` and ``03:00``/``3am`` (review P1s 1–2, #10360
   lane record §6). A guard that accuses a truthful point of a rewrite it did
   not commit is worse than no guard: it withholds a durable memory.
   ⛔ ``1200``/``1,200`` is no longer a TOLERANCE the rule applies: a
   separator-bearing number is outside the judged set entirely
   (``_separator_reads_ambiguously``, review cycle 12 P1) — the pair agrees
   because neither side is judged, not because the comma was decided.
2. **The enforcement, at the emit site.** ``execute_embed`` does not write a
   point whose value contradicts its own quote. This is the pin that fails if
   the rule is DELETED from the write path (mutation-checked in the lane
   record): asserting the pure function alone would be machinery, not proof.
3. **The invariant the guarantee cannot break.** A point whose value IS
   verbatim survives — untouched, with its span — so this rule can never be
   read as a filter that drops state values.
4. **The other invariant, which this rule must not disturb.** The selection
   predicate ``value_gate.identifier_only`` (#4899) still drops
   identifier-like residues. Retention must not re-admit the identifiers that
   filter was built to remove, so both halves are asserted together.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys
import traceback

import pytest

from tortoise import extractor_v2 as v2
from tortoise import value_gate


def _embed(content: str, quote: str) -> dict:
    """The minimal S2 payload the write path accepts, one point."""
    return {
        "entities": [], "points": [{
            "content": content, "pointKind": "statement",
            "about_entities": [], "quote": quote,
        }],
        "events": [], "operators": [], "chain_notes": [],
        "link_before_create": [],
    }


def _write(content: str, quote: str) -> dict:
    # #10997: a COMPLETE lookup. `#2552`'s mint is an identity assertion and is
    # licensed only by a lookup that resolved (`{}` = "the lookup did not run"
    # now HOLDS the edge), and the mint cases in this file must reach the code
    # they pin.
    return v2.execute_embed(_embed(content, quote), _RESOLVED_SEARCH,
                            session_id="s_10360")


_RESOLVED_SEARCH = {
    "mode": "real", "degraded": False, "reason": None,
    "entities": [], "points": [], "events": [], "queries_run": 5,
    "query_coverage": {"derived": {"object": 1, "event": 1, "point": 1},
                       "ran": {"object": 1, "event": 1, "point": 1},
                       "failed": {"object": 0, "event": 0, "point": 0}},
}


# ── 1. the rule + its tolerance bound ───────────────────────────────────────

#: ``(content, quote, must_flag, label)``. The ``False`` rows carry one of three
#: measured false-positive classes — a model-independent FORM difference, a
#: separator/locale-ambiguous numeral, or a quote that drops/reorders a value —
#: and the ``True`` rows are the class the carve-out exists for ("never round,
#: paraphrase, or label them"). ⛔ The rows are NOT counted here: a hand-written
#: total re-staled twice on #10513 (`four` FORM rows and `three` dropped/reordered
#: ones against a 33-row battery, review cycle 13 P2). The battery is the count;
#: a reader who needs one reads it from `len(BATTERY)`.
BATTERY = (
    ("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds", True, "rounding"),
    ("the invoice was $12", "the invoice was $60", True, "leading symbol"),
    ("the p95 hit 4 seconds", "the p95 hit 4.2 seconds", True, "precision dropped"),
    ("the TTL is ten minutes", "the TTL is twenty minutes", True, "ttl rewritten"),
    ("p95 went from 4.2 to 6.1", "p95 went from 4.2 to 6.0", True, "one of two"),
    ("we have 2 cats and 3 dogs", "we have 3 cats and 2 dogs", True, "quantities swapped"),
    ("the p95 hit 4.0 seconds", "the p95 hit 4.0 seconds", False, "identical"),
    ("latency was 1200 ms", "latency was 1,200 ms", False, "separator, unjudged"),
    # ⛔ A SEPARATOR-BEARING NUMBER IS UNJUDGED (#10513 review cycle 12, P1).
    # Which of ``,``/``.`` is the decimal point is a LOCALE property (CLDR UTS #35
    # carries `decimal` and `group` per locale; Babel's `format_decimal(1.2345,
    # locale='en_US')` is '1.234' while `format_decimal(12345, locale='de_DE')`
    # is '12.345'), and this path has no locale — so BOTH readings fail OPEN.
    # The comma pair is the one the previous revision REFUSED, because it had
    # decided the locale: it deleted the comma, so `4,2` read as `42` against
    # `4.2` and a truthful point in a comma-decimal locale was withheld.
    ("the p95 hit 4,2 seconds", "the p95 hit 4.2 seconds", False,
     "comma decimal, unjudged"),
    ("the drive was 1.234 km", "the drive was 1234 km", False,
     "dot thousands, unjudged"),
    ("the p95 hit 4.20 seconds", "the p95 hit 4.2 seconds", False, "trailing zero"),
    ("the share was 20 percent", "the share was 20%", False, "percent form"),
    ("budget 800 milliseconds", "budget 800ms", False, "unit spacing"),
    ("the freeze starts 03:00", "the freeze starts 3am", False, "clock form"),
    ("the freeze starts 2026-10-09", "the freeze starts yesterday", False, "date form"),
    ("12 million events", "twelve million events", False, "number word"),
    ("the session cache TTL is twenty minutes", "the TTL is twenty minutes",
     False, "quote is a fragment"),
    ("forty round-trips blew up the p95", "the calls blew up the p95",
     False, "quote omits a value"),
    # units are VALUE, not form: a minutes->hours rewrite is refused, while a
    # unit SPELLED differently is not (review P2 on the first round).
    ("the TTL is 20 minutes", "the TTL is 20 hours", True, "unit rewritten"),
    # `_value_bindings` fuses a unit the engine's `_UNIT_WORDS` carries, whether
    # it is ATTACHED (`800ms`) or written APART (`800 ms`) — so both forms are
    # unit identities and are compared. Only a spelling OUTSIDE `_UNIT_EQUIV`
    # (`800millis`) falls back to the unstated-unit tolerance.
    ("the timeout is 800ms", "the timeout is 800s", True, "unit rewritten 2"),
    ("the TTL is 20 minutes", "the TTL is 20 mins", False, "unit spelling"),
    ("the p95 is 0.5 seconds", "the p95 is .5 seconds", False, "leading dot"),
    ("the delta is -4 percent", "the delta is -4 percent", False, "identical sign"),
    ("the delta is 4 percent", "the delta is -4 percent", True, "sign dropped"),
    # units the ENGINE can fuse but the first table did not spell (review 2, P2)
    ("the drive is 20 km", "the drive is 20 mi", True, "unit rewritten 3"),
    ("the weight is 20 kg", "the weight is 20 lb", True, "unit rewritten 4"),
    ("the TTL is 20 min", "the TTL is 20 m", True, "unit rewritten 5"),
    # A BARE MAGNITUDE PREFIX is not a unit (#10513 review P1, MEASURED: both of
    # the first two rows REFUSED before `_MAGNITUDE_PREFIXES` existed). `k` is
    # SI's x1000 symbol and the table ALSO carries it as a unit identity, so a
    # pair that DISAGREES about which job it did is unjudgeable and fails OPEN.
    ("I ran 20k", "I ran 20km", False, "magnitude prefix vs unit"),
    ("we saw 5k QPS", "we saw 5000 QPS", False, "magnitude prefix vs scaled"),
    # ...and the tolerance is NARROW, which is what keeps it from being a waiver:
    # a bare-magnitude rewrite is still refused, because both sides agree the
    # suffix is `k` and only the number moved. ⛔ The mutation this row
    # discriminates is NOT "widen the arm's condition" — the `_magnitude_agrees`
    # gate still refuses this pair under any condition that keeps its gating
    # (measured: `a[2] in _MAGNITUDE_PREFIXES or b[2] in ...` leaves this row
    # green, because 5 x 1000 != 6). It reds when the SIGN/CORE comparison is
    # deleted, or when `_magnitude_agrees` is replaced by the unit-mismatch test
    # the previous revision shipped — which is exactly the regression it guards.
    ("the load was 5k QPS", "the load was 6k QPS", True, "magnitude rewritten"),
    # ⛔ THE TOLERANCE IS ABOUT THE UNIT WORD, NEVER ABOUT THE NUMBER OR THE SIGN
    # (#10513 review P2, MEASURED: an arm that fired on the unit mismatch ALONE
    # WROTE all three of these — `_value_slot_divergence` returned `()`).
    # `_magnitude_agrees` is the gate that keeps them refused, and these rows are
    # the only thing standing between that arm and a numeric waiver.
    ("we saw 5k QPS", "we saw 6000 QPS", True, "magnitude number moved"),
    ("we ran 20k", "we ran 21km", True, "magnitude number moved 2"),
    ("the delta is -20k", "the delta is 20km", True, "magnitude sign dropped"),
    # ⛔ A MAGNITUDE **WORD** IS NEITHER FUSED NOR JUDGED (#10513 review cycle-13
    # P2, measured). `_value_bindings` fuses no scale word, so `million` /
    # `billion` / `thousand` are ignored and both sides bind the SAME core — a
    # 10^3..10^9 scale rewrite is written as verbatim. It is residual (m) in the
    # registry row, NOT a tolerance this rule applies on purpose: closing it means
    # fusing scale words into the core in `_value_bindings`, which is a change to
    # the production engine, not to this seam. Pinned so closing it must move the
    # row.
    ("we have 12 million events", "we have 12 billion events", False,
     "scale word, unjudged"),
)


@pytest.mark.parametrize("content,quote,must_flag,label", BATTERY)
def test_the_rule_fires_on_a_same_slot_rewrite_and_fails_open_on_form(
        content, quote, must_flag, label):
    diverged = v2._value_slot_divergence(content, quote)
    assert bool(diverged) is must_flag, (
        f"{label}: expected flag={must_flag}, got {diverged}")
    if must_flag:
        # The report names BOTH values, so a rewrite is recoverable from the
        # run without the original session.
        _anchor, point_value, quote_value = diverged[0]
        assert point_value and quote_value and point_value != quote_value


def test_a_bare_magnitude_prefix_is_tolerated_only_against_a_DIFFERENT_unit():
    """The `k` arm is a tolerance on AMBIGUITY, not a waiver on `k` (#10513 P1/P2).

    `k` is SI's x1000 prefix AND a unit identity in `_UNIT_EQUIV` (it is a unit the
    engine fuses, so the table must carry the key). The two jobs are only
    confusable when the two sides DISAGREE about which one it was, and even then
    the arm may only tolerate the UNIT WORD — the number and the sign are never
    in dispute.

    ⛔ That second half is a review finding, not a nicety (#10513 P2, MEASURED):
    an earlier revision of this arm fired on the unit mismatch ALONE and returned
    before the sign/core check, so `5k QPS`/`6000 QPS`, `20k`/`21km` and
    `-20k`/`20km` were all WRITTEN. The assertions below are what reds that
    revision; the unit-only arm cannot satisfy them.

    ⛔ THE OTHER SIDE IS SPELLED UNGROUPED (``5000``) ON PURPOSE. The case was
    measured with ``5,000``, and a grouped numeral is now OUTSIDE the judged set
    (``_separator_reads_ambiguously``, review cycle 12 P1), so a grouped row
    would test the fail-open rather than this arm: it would stay green with
    ``_magnitude_agrees`` deleted. The arm's live evidence is the ungrouped form.
    """
    # Ambiguous unit word: unjudgeable, so it fails OPEN (the refusal direction
    # this rule must never take is the one that withholds a truthful memory).
    assert v2._value_slot_divergence("I ran 20k", "I ran 20km") == ()
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 5000 QPS") == ()
    # Same unit on both sides: still judged, and a moved number is a rewrite.
    assert v2._value_slot_divergence("the load was 5k QPS", "the load was 6k QPS")
    # ⛔ EVERY magnitude prefix needs a FACTOR, or `_magnitude_agrees` returns
    # False for everything and the arm becomes a silent no-op — the two false
    # refusals below would quietly come back with every test still green. One
    # line, because the containment is the whole mechanism.
    assert set(v2._MAGNITUDE_PREFIXES) <= set(v2._MAGNITUDE_FACTORS)
    # ⛔ The number and the sign are NOT the ambiguity, so they are still judged
    # even where the unit words disagree (this is the P2 the arm first shipped).
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 6000 QPS")
    assert v2._value_slot_divergence("we ran 20k", "we ran 21km")
    assert v2._value_slot_divergence("the delta is -20k", "the delta is 20km")
    # ⛔ The equal-core half is gated on the OTHER unit (#10513 review P1):
    # "the cores are equal" is "the same number" only while `k` stands for that
    # unit word. Against `m`/`mi` the same cores are a 1000x change — the class
    # `20 km`/`20 m` is refused for — and the first version of this arm WROTE
    # them: measured `20k`/`20m` and `20k`/`20mi` both passed with no warning.
    assert v2._value_slot_divergence("the drive is 20k", "the drive is 20m")
    assert v2._value_slot_divergence("the drive is 20k", "the drive is 20mi")
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 5000 mi QPS")
    # …and the two tolerance cases the arm exists for still hold, so the gate did
    # not buy the detection by re-introducing the false refusals.
    assert v2._value_slot_divergence("the drive is 20k", "the drive is 20kg") == ()
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 5000 QPS") == ()


def test_a_scale_WORD_rewrite_is_unjudged_because_the_engine_fuses_none():
    """Residual (m), PINNED so closing it has to move the registry row.

    #10513 review cycle-13 P2, measured: `_value_bindings` fuses no scale word,
    so `million`/`billion`/`thousand` are IGNORED and both sides bind the same
    core — a 10^3..10^9 rewrite is written as verbatim. This is a different class
    from residual (d) (a RATE unit with no number adjacent, or a unit SPELLING
    the tables lack): it is not a unit adjacency the engine never fuses and not a
    spelling, it is a MAGNITUDE the engine never multiplies, so the fix
    belongs in `_value_bindings` and would be its own change — a second scale
    reading here is the parallel-classifier shape that killed the previous
    attempt at this issue.
    """
    for a, b in (("we have 12 million events", "we have 12 billion events"),
                 ("5 million requests", "5 thousand requests"),
                 ("we have 12 million events", "we have 12 events")):
        assert v2._value_slot_divergence(a, b) == (), (a, b)
    # positive control: the NUMBER in the same slot is still judged, so this is a
    # scale-word gap and not a broken pair
    assert v2._value_slot_divergence("we have 12 million events",
                                     "we have 13 million events")


def test_a_separator_bearing_number_is_outside_the_judged_set():
    """NO SEPARATOR RULE SHIPS — the recorded decision, and the over-refusal.

    ``tortoise/value_normalizer.py`` carries the owner's #2817 ruling, point 1:
    "**No separator rule, at all.** … There is no 'PT-BR reads dots as
    thousands' rung and no 'EN reads commas as thousands' rung to add one —
    deciding that ``R$ 1.234,56`` is ``1234.56`` **is** the locale question the
    design excludes."

    ⛔ THE PREVIOUS REVISION SHIPPED THE RUNG ANYWAY (#10513 review cycle 12, P1)
    — ``str(token).replace(",", "")`` — so this test reds on it: pre-fix
    ``_value_form_outside_judged('1,200')`` was False and ``_value_core('1,200')``
    was ``('', '1200', None)``, i.e. the locale was decided, and the consequence
    was in the WITHHOLDING direction: ``4,2`` reduced to ``42`` so it diverged
    from ``4.2`` and the whole point was refused, losing a durable memory over a
    spelling. Both directions are asserted below, so neither half can be
    reverted silently.
    """
    # Every reading that needs a separator CONVENTION is outside the judged set,
    # in both conventions and in the mixed shapes.
    for token in ("1,200", "4,2", "1.234", "1.234,56", "1,234.56",
                  "$1,200,000", "1,200km"):
        assert v2._value_form_outside_judged(token), token
        assert v2._value_core(token) is None, token
    # …and a number that reads the same in EVERY locale is still judged, so the
    # exclusion did not swallow the class it sits beside: a 1-2 digit fraction
    # cannot be a thousands group, a leading `0.` cannot be one, and an ungrouped
    # integer carries no separator at all.
    for token in ("4.2", "4.20", "1200", "5000", "1.2345", "0.500", "800ms",
                  "1.5-inch", "-4percent", ".5"):
        assert not v2._value_form_outside_judged(token), token
        assert v2._value_core(token) is not None, token
    # The over-refusals the finding MEASURED, end to end: a truthful comma-decimal
    # quote and a truthful dot-thousands quote are no longer accusations.
    assert v2._value_slot_divergence("the p95 hit 4,2 seconds",
                                     "the p95 hit 4.2 seconds") == ()
    assert v2._value_slot_divergence("the drive was 1.234 km",
                                     "the drive was 1234 km") == ()
    # ⛔ THE MEASURED COST, stated rather than hidden (registry residual (l)): a
    # REAL thousands rewrite is unjudged too, because both sides carry the
    # ambiguity. Precision over recall, in the direction that keeps memories.
    assert v2._value_slot_divergence("we saw 5,000 QPS",
                                     "we saw 6,000 QPS") == ()


def test_the_one_classifier_is_the_production_value_engine(monkeypatch):
    """No second value classifier: the rule reads ``_value_bindings``.

    The previous attempt at this issue introduced a private
    ``_REWRITE_QUANTITY_RE`` beside the engine's own vocabulary and was rejected
    for it (review P2, owner rule: "never create a parallel layer doing the same
    thing with redundant machinery"). Asserted structurally, because a comment
    cannot fail: a value the engine binds is a value the rule judges, and a
    token the engine does not bind is invisible to the rule.

    ⛔ AND ASSERTED AS A CALL (#10513 review P2, measured). The three
    assertions below are all about ``_value_slots``' OUTPUT, so a lane that added
    a parallel regex classifier INSIDE ``_value_slots`` would satisfy every one of
    them — the exact P2 that killed the previous attempt, passing green. The
    counter is what makes this pin fail on that shape; it is the same device
    ``test_an_over_long_side_is_not_tokenised_at_all`` uses for the bound.
    """
    calls = {"n": 0}
    real = v2._value_bindings

    # ⛔ `*args, **kwargs`: the engine gained a `with_following` keyword
    # (#10513 review cycle 18) so the JUDGING layer can ask about a SCALE
    # word. The wrapper forwards it — a signature that dropped the keyword
    # would make this pin pass by refusing to call the engine at all, which
    # is the opposite of what it exists to assert.
    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(v2, "_value_bindings", counting)
    assert v2._value_slots("the TTL is twenty minutes") == (("ttl", ("", "20", "min")),)
    assert calls["n"] == 1, (
        f"_value_slots answered without calling the ONE engine: {calls['n']} "
        "calls — a second value classifier is the P2 this row forbids")
    assert v2._value_slots("the team prefers intervals") == ()
    assert calls["n"] == 2
    # the rule's judged set IS the engine's, minus the excluded FORMS
    engine = [v for _a, v in v2._value_bindings("the freeze starts 03:00")]
    assert engine and v2._value_slots("the freeze starts 03:00") == ()
    assert calls["n"] == 4, calls["n"]
    # ⛔ THE COUNTER ALONE IS NOT ENOUGH (#10513 review cycle 10 P2, measured):
    # it observes a REPLACEMENT or a second call, so a SUPPLEMENTARY classifier
    # added BESIDE the engine — the `_REWRITE_QUANTITY_RE` shape that killed the
    # previous attempt — left the whole file green (62 passed, mutated, measured).
    # The guard that catches it is DERIVABILITY: every slot the rule returns must
    # be re-derivable from a binding the engine produced. A parallel classifier's
    # slot has no binding behind it and this equality reds.
    for text in ("the TTL is twenty minutes",
                 "we waited 3 turns for the 5k rollout",
                 "the freeze starts 03:00",
                 "my personal best 5K time is 27:12",
                 "the team prefers intervals 7"):
        assert v2._value_slots(text) == tuple(
            (str(a), c)
            for a, v in v2._value_bindings(text)
            if not v2._value_form_outside_judged(v)
            and (c := v2._value_core(v)) is not None
        ), text


def test_a_clock_valid_colon_form_is_excluded_but_a_duration_is_judged():
    """The colon exclusion is CLOCK-VALID, not "any colon" (#10513 review P1).

    An any-colon exclusion swallowed every colon-bearing numeric token, so a
    race time — the ``STATE_VALUE_CARVE_OUT`` text's OWN example, "my personal
    best 5K time is 27:12" — was outside the judged set and ``27:00`` for it was
    written with no warning. The carve-out's headline case was the one case the
    rule could never see.

    Measured on the committed census, narrowing it changes NOTHING (62 judged /
    0 refused), so this pin buys the carve-out's own example at no precision
    cost on the corpus the residual cites.
    """
    # a valid clock stays outside the judged set — the measured false-positive
    # class (`03:00`/`3am`), which is why the arm exists at all.
    assert v2._value_form_outside_judged("9:30")
    assert v2._value_form_outside_judged("03:00")
    assert v2._value_form_outside_judged("23:59:59")
    assert not v2._value_slot_divergence("the freeze starts 03:00",
                                         "the freeze starts 3am")
    # a colon form that is NOT a clock is a value, and a moved one is a rewrite
    assert not v2._value_form_outside_judged("27:12")
    assert v2._value_slot_divergence("my personal best 5K time is 27:00",
                                     "my personal best 5K time is 27:12")
    # …and the write path refuses it, so the carve-out's own example is enforced
    result = _write("my personal best 5K time is 27:00",
                    "my personal best 5K time is 27:12")
    assert result["payload"]["points"] == [], result["warnings"]
    # ⛔ A ZONE-QUALIFIED clock is still a clock (#10513 review cycle 10 P2,
    # measured): a bare-shape-only arm judged `UTC+03:00`, whose core is the two
    # colon runs joined (`3/0`), so `UTC+3` against `UTC+03:00` — one offset
    # spelled two ways — was REFUSED. That is the over-refusal direction.
    assert v2._value_form_outside_judged("UTC+03:00")
    assert v2._value_form_outside_judged("UTC-03:00")
    assert not v2._value_slot_divergence("the offset is UTC+3",
                                         "the offset is UTC+03:00")
    # …and the boundary that keeps a duration judged is still there: `27` is no
    # hour, so `27:12` has NO clockable start.
    assert not v2._value_form_outside_judged("27:12")


def test_a_typographic_minus_is_a_sign_not_decoration():
    """U+2212 is a sign (#10513 review P1, measured).

    The ASCII-only read made ``−4`` and ``4`` one value, and let ``−20k`` through
    where the ASCII ``-20k`` is refused — so the registry's "a dropped sign …
    still refuse" was true of one spelling of the minus and false of the other.
    ``value_normalizer.py`` already reads U+2212 as a sign; this pins that the
    value rule joins it.

    ⛔ AND THE DASH FAMILY (#10513 review cycle 10 P2), which is the OVER-refusal
    direction and so the worse one: ``_strip_edges`` removed an en/em dash as
    decoration, so a quote writing its minus that way read as NO sign and a
    truthful ``-5`` point was DROPPED, with a warning that misreported the quote
    as ``5``. A LEADING dash is now part of the numeral in every spelling.
    """
    assert v2._value_slot_divergence("the delta is \u22124 percent",
                                     "the delta is 4 percent")
    assert v2._value_slot_divergence("the delta is \u221220k",
                                     "the delta is 20km")
    # positive control: both spellings of the SAME signed value agree
    assert not v2._value_slot_divergence("the delta is \u22124 percent",
                                         "the delta is -4 percent")
    # the dash family: one minus, three spellings, all equal to each other
    assert not v2._value_slot_divergence("the temperature was -5 degrees",
                                         "the temperature was \u20135 degrees")
    assert not v2._value_slot_divergence("the temperature was -5 degrees",
                                         "the temperature was \u20145 degrees")
    # …and a dash does not hide a real sign change
    assert v2._value_slot_divergence("the temperature was -5 degrees",
                                     "the temperature was 5 degrees")
    # the positive control for the OTHER direction: an INNER dash is a range or a
    # compound, never a sign, so `5–10` is still two numbers and not `5` minus
    # `10`. Keeping only the LEADING case is what makes this safe.
    assert v2._value_slots("we ran 5\u201310 km") == (("ran", ("", "5/10", "km")),)

# ── 2. the enforcement, in the write path ───────────────────────────────────

def test_the_write_path_refuses_a_point_that_rewrites_its_own_quote():
    """MUTATION CHECK TARGET: delete the emit-site rule and this goes red.

    The refusal is on the POINT, not on the offsets: withholding only the span
    would leave the rounded value in the graph with the correct value one
    ``find`` away — the P1 that rejected the previous attempt.
    """
    result = _write("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    assert result["payload"]["points"] == [], (
        "a point whose value contradicts its own verbatim quote reached the "
        "write path — the guarantee is prompt-only again")
    assert any("REWRITES its own verbatim quote" in w
               for w in result["warnings"]), result["warnings"]


def test_the_refusal_names_the_verbatim_value_it_would_have_lost():
    result = _write("the TTL is ten minutes", "the TTL is twenty minutes")
    joined = " ".join(result["warnings"])
    assert "'10min'" in joined and "'20min'" in joined, joined


def test_the_refusal_judges_the_SAME_text_the_write_uses_on_a_falsy_content():
    """#5060's trap, in the new gate (review P3 — it was a live bypass).

    The call site passed ``str(p.get("content") or "")``, which turns a writable
    falsy content (the integer ``0``) into ``""``. The judge then read an EMPTY
    side, ``_value_slots`` bound nothing, no divergence was found, and the
    rewritten value was WRITTEN with no warning — while ``content`` in the same
    iteration was ``"0"``. The file warns about exactly this 80 lines above the
    call (``#5060 made a falsy content WRITABLE as its coerced text``), so the
    fix passes ``_raw_content``: the coerced string the write clamps from.

    Measured at the pre-fix call site: ``str(0 or "")`` = ``""`` against the
    quote ``"20 minutes"`` is ``()``, while ``"0"`` yields
    ``(('', '0', '20min'),)``. The integration call below would have written the
    point; the predicate assertions pin the half that actually changed.
    """
    assert v2._value_slot_divergence(str(0 or ""), "20 minutes") == (), (
        "the pre-fix coercion is supposed to MISS this — if it no longer does, "
        "this pin has stopped discriminating and the bypass is untestable")
    assert v2._value_slot_divergence(str(0), "20 minutes"), (
        "the judged text must be the text the write uses; a falsy writable "
        "content must not be collapsed to an empty side")
    # And the shape the write path receives: content=0 is a dict value here, so
    # `or ""` is reached. It must be REFUSED, not written silently.
    result = _write(0, "20 minutes")
    assert result["payload"]["points"] == [], (
        "a falsy content collapsed the judged side to '' and the rewrite was "
        f"written anyway: {result['payload']['points']!r}")
    assert any("REWRITES its own verbatim quote" in w
               for w in result["warnings"]), result["warnings"]


# ── 2b. the enforcement's OWN off-switch (#10360, 2026-11-07) ────────────────

def test_the_off_switch_defaults_ON_and_restores_the_pre_10513_write(monkeypatch):
    """The rule WITHHOLDS a durable memory, so it needs a reversible door.

    MUTATION CHECK TARGETS, both arms: drop the switch from the emit site and
    the OFF block below reds; flip its default to ``False`` and the ON block
    reds. Neither arm is machinery — the ON arm asserts the guarantee is armed
    when nothing is set, and the OFF arm asserts the write path is the
    PRE-#10513 one, byte for byte (the point is written, carrying the rewritten
    value, with no refusal and no disclosure).
    """
    monkeypatch.delenv(v2.STATE_VALUE_VERBATIM_SWITCH, raising=False)
    assert v2._state_value_verbatim_enforced() is True
    assert _write("the p95 hit 4.0 seconds",
                  "the p95 hit 4.2 seconds")["payload"]["points"] == []
    # a TYPO is not a statement: an unparseable value resolves to the DEFAULT (ON),
    # which is the only safe direction for a gate that can withhold a memory.
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "ture")
    assert v2._state_value_verbatim_enforced() is True
    assert _write("the p95 hit 4.0 seconds",
                  "the p95 hit 4.2 seconds")["payload"]["points"] == []
    # …and each explicit falsy spelling opens the door.
    for spelling in ("0", "off", "false", "no"):
        monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, spelling)
        off = _write("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
        assert [p["content"] for p in off["payload"]["points"]] == [
            "the p95 hit 4.0 seconds"], spelling
        assert not any("REWRITES its own verbatim quote" in w
                       for w in off["warnings"]), (spelling, off["warnings"])
        # …and the WHOLE rule is off, not just its refusal: the hard-cut
        # disclosure is part of the judgment and must not leak into the OFF arm
        # (a half-disarmed switch reads as "judged and failed open"). ⚠️ The
        # plain pair above cannot discriminate this — both predicates are False
        # for it — so the shape that WOULD disclose is used too (review round 2,
        # P3: the assertion was unfalsifiable for `the p95 hit 4.0 seconds`).
        assert not any("NOT judged" in w for w in off["warnings"]), (
            spelling, off["warnings"])
        hard_cut_shape = "z" * 998 + "4.2 seconds"
        off_cut = _write(hard_cut_shape, "4.2 seconds")
        assert not any("NOT judged" in w for w in off_cut["warnings"]), (
            "the hard-cut disclosure leaked into the OFF arm — the switch "
            "disarmed the refusal but left the judgment's other output on",
            spelling, off_cut["warnings"])


def test_the_off_switch_leaves_the_MINT_guard_off_too(monkeypatch):
    """“Off” has to mean BOTH doors, or it is a half-armed guard.

    `refused_value_contents` / `refused_value_slots` are populated only on a
    refusal, so a switch that disarmed the refusal while leaving
    `_mint_endpoint`'s guard armed would suppress an operator edge for a point
    the run never refused. With the switch off the rewritten value is written
    AND an operator naming a variant of it is not suppressed — the pre-#10513
    behaviour on both doors.
    """
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "0")
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    embed["operators"] = [{
        "src": "the p95 hit 4.0 seconds.",
        "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}]
    result = v2.execute_embed(embed, _RESOLVED_SEARCH, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert "the p95 hit 4.0 seconds" in contents, contents
    assert not any("refused for rewriting its own verbatim quote" in w
                   for w in result["warnings"]), result["warnings"]


def test_the_off_switch_does_not_move_the_JUDGMENT(monkeypatch):
    """The switch moves the DOOR, never the classifier.

    `_value_slot_divergence` / `_value_slots` are the production value engine
    (shared with `value_gate`/#4899), so an operator can flip the switch,
    re-measure and flip it back without re-deriving a tolerance battery. Pinned
    because a switch that also silenced the classifier would make the OFF arm
    unable to report what it let through.
    """
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "0")
    assert v2._state_value_verbatim_enforced() is False
    assert v2._value_slot_divergence("the p95 hit 4.0 seconds",
                                     "the p95 hit 4.2 seconds")
    assert v2._value_slots("the p95 hit 4.0 seconds")


def test_the_off_switch_is_read_at_ONE_site():
    """One switch, one read (#4097's lesson, applied to a guarantee).

    Two things a rename or a second spelling would break silently, both pinned
    literally rather than against the constant itself (review round 2, P3: the
    first version compared the assignment to `v2.STATE_VALUE_VERBATIM_SWITCH`,
    i.e. the value against itself, so renaming the variable kept every test
    green while the documented rollback stopped working):

    * the module's string literal is **exactly** `TORTOISE_STATE_VALUE_VERBATIM`,
      and it appears as a literal **once** — a second spelling is a second read
      waiting to disagree;
    * the constant is referenced by NAME **only inside
      `_state_value_verbatim_enforced`**. That is what catches a second read in
      ANY spelling — `env_flag`, `is_truthy(os.environ.get(…))`,
      `os.environ.get(…)` — which a call-count pin alone cannot (review round 2,
      P3: `is_truthy(os.environ.get(STATE_VALUE_VERBATIM_SWITCH, "1"))` left the
      count at one and stayed green);
    * the single `env_flag` read of it defaults to ``True``:
      a default-OFF read would silently disarm the guarantee for every
      deployment that never sets the variable.
    """
    import ast
    source = pathlib.Path(v2.__file__).read_text(encoding="utf-8")
    assert v2.STATE_VALUE_VERBATIM_SWITCH == "TORTOISE_STATE_VALUE_VERBATIM", (
        "the switch's NAME is the operator's interface: it is quoted in the "
        "registry row and in `.env.example`, so a rename is a breaking change "
        "and must not ride on a test that compares the constant to itself")
    tree = ast.parse(source)
    # ONE definition of the name…
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name)
                       and t.id == "STATE_VALUE_VERBATIM_SWITCH"
                       for t in n.targets)]
    assert len(assigns) == 1, (
        f"expected ONE module-level definition, found {len(assigns)}")
    # …whose string literal is the operator-visible name, spelled ONCE. Docstrings
    # are excluded (they NAME the variable for readers; they are not reads).
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                    ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)
                  and isinstance(n.body[0].value.value, str)}
    literals = [n for n in ast.walk(tree)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and "TORTOISE_STATE_VALUE_VERBATIM" in n.value
                and id(n) not in docstrings]
    assert len(literals) == 1, (
        f"the switch's name is spelled in {len(literals)} string literals — any "
        "literal besides the constant's definition is a second, divergent read "
        "that this pin would otherwise not see")
    assert literals[0].value == "TORTOISE_STATE_VALUE_VERBATIM"
    # …and ONE read, through `env_flag`, defaulting True.
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "env_flag"
             and n.args and isinstance(n.args[0], ast.Name)
             and n.args[0].id == "STATE_VALUE_VERBATIM_SWITCH"]
    assert len(calls) == 1, f"expected ONE env_flag read, found {len(calls)}"
    assert isinstance(calls[0].args[1], ast.Constant) \
        and calls[0].args[1].value is True, (
            "the switch must default ON: a default-OFF read silently disarms "
            "the guarantee for every deployment that never sets the variable")
    # …and the name is referenced NOWHERE ELSE, in any spelling of a read.
    helper = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_state_value_verbatim_enforced"), None)
    assert helper is not None, "the helper is gone — the switch has no ONE read"
    inside = {id(n) for n in ast.walk(helper)
              if isinstance(n, ast.Name)
              and n.id == "STATE_VALUE_VERBATIM_SWITCH"}
    assert len(inside) == 1, (
        f"`_state_value_verbatim_enforced` references the switch {len(inside)} "
        "time(s) — the helper must resolve it ONCE, or two spellings inside it "
        "can disagree about what 'unset' means (measured: appending "
        "`return is_truthy(os.environ.get(STATE_VALUE_VERBATIM_SWITCH, \"1\"))` "
        "left this pin green until the count was added)")
    outside = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Name)
               and n.id == "STATE_VALUE_VERBATIM_SWITCH"
               and isinstance(n.ctx, ast.Load)
               and id(n) not in inside]
    assert not outside, (
        f"the switch is referenced at line(s) {outside} outside "
        "`_state_value_verbatim_enforced` — a read in any spelling there can "
        "disagree with the helper's (env_flag, is_truthy(os.environ.get(...)), "
        "os.environ.get(...)); route it through the helper")


def test_the_rule_HONOURS_the_switch_the_caller_handed_it(monkeypatch):
    """The `armed=` handoff is a guarantee, so it is pinned (review round 3, P3).

    Round 2's defect was exactly the shape this catches: the emit site resolved the
    switch for a point and the rule resolved it AGAIN, so a mid-batch flip judged
    one point under two answers and wrote a divergent value with no warning. With
    the handoff honoured, the caller's answer wins both ways.

    MUTATION CHECK TARGET: replace ``if armed is None: armed = …`` with an
    unconditional read and this reds (measured: the whole file stayed green under
    that mutant until this test existed).
    """
    content, quote = "the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds"
    # env ON, caller says OFF → the CALLER's answer wins: no refusal, no report.
    monkeypatch.delenv(v2.STATE_VALUE_VERBATIM_SWITCH, raising=False)
    warnings: list[str] = []
    assert v2._refuse_rewritten_value(content, quote, warnings=warnings,
                                     armed=False) is False
    assert warnings == [], warnings
    # env OFF, caller says ON → the CALLER's answer wins: refusal + report.
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "0")
    warnings = []
    assert v2._refuse_rewritten_value(content, quote, warnings=warnings,
                                     armed=True) is True
    assert any("REWRITES its own verbatim quote" in w for w in warnings), warnings


def test_the_emit_site_SUPPLIES_the_reading_the_rule_honours(monkeypatch):
    """The handoff's SUPPLY end, which `armed=`-honouring alone does not pin.

    Review round 5's mutation attribution measured an un-killed mutant (M9): dropping
    `armed=_value_fidelity_judged` at the emit site left the whole file green,
    because in a static environment the caller's read and the rule's own re-read
    cannot disagree — which is exactly the round-2 defect shape (one point judged
    under two answers) that the handoff exists to prevent.

    This pins the supply by making the two reads DISAGREE: the helper answers from a
    call counter, so the emit site's read says ON and any later re-read says OFF.
    With the handoff supplied the rule refuses (the point's own reading wins); with
    the handoff dropped the rule re-reads, gets OFF, and writes the divergent point.

    MUTATION CHECK TARGET: remove `armed=_value_fidelity_judged` from the call site
    and this reds on the call count (2 reads) and on the written point.
    """
    calls: list[int] = []

    def _one_reading_then_off() -> bool:
        calls.append(1)
        return len(calls) == 1

    monkeypatch.setattr(v2, "_state_value_verbatim_enforced",
                        _one_reading_then_off)
    result = _write("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    assert calls == [1], (
        "the emit site did not hand its own reading to the rule — the rule read "
        f"the switch again ({len(calls)} reads for one point), so a mid-batch "
        "flip can judge one point under two answers and write the divergent "
        "value with no warning")
    assert result["payload"]["points"] == [], (
        "the emit site's ON reading was not honoured by the rule: the divergent "
        f"point was written {result['payload']['points']!r}")


def test_the_off_switch_is_INTRINSIC_so_a_second_call_site_cannot_bypass_it(
        monkeypatch):
    """The switch is checked INSIDE the rule, not only at the emit site.

    Review round 2, P3: with the gate only at the caller, the next lane to wire
    `_refuse_rewritten_value` into a SECOND emit path restores a refusal nobody
    can turn off, and no test notices. Called directly with the switch OFF the
    rule must answer ``False`` and say nothing.
    """
    content, quote = "the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds"
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "0")
    warnings: list[str] = []
    assert v2._refuse_rewritten_value(content, quote, warnings=warnings) is False
    assert warnings == [], warnings
    # positive control: the switch is what answered — with it ON the SAME direct
    # call refuses and reports, so this cannot pass by the rule being dead.
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "1")
    assert v2._refuse_rewritten_value(content, quote, warnings=warnings) is True
    assert any("REWRITES its own verbatim quote" in w for w in warnings)


# ── 3. the invariant: a verbatim value is never dropped ─────────────────────

def test_a_point_that_carries_its_value_verbatim_is_written_untouched():
    """The rule cannot be read as a filter over state values.

    It fires only on a value that is NOT the verbatim one. A point whose value
    the quote also holds passes through byte-identical, still value-bearing —
    which is what makes this enforcement of the carve-out rather than a second
    way to lose state values.
    """
    result = _write("the p95 hit 4.2 seconds", "the p95 hit 4.2 seconds")
    points = result["payload"]["points"]
    assert len(points) == 1, result["warnings"]
    assert points[0]["content"] == "the p95 hit 4.2 seconds"


def test_every_STORED_value_bearing_point_satisfies_the_verbatim_identity(
        monkeypatch):
    """MECHANISM-LEVEL SURVIVAL — the value a stored point carries IS the value
    its own verbatim quote states.

    This is the provable form of “the value survives verbatim”, and it needs no
    LLM arm: whatever the model wrote, the write path's guarantee is that a
    stored point's value identity and its own verbatim quote's value identity
    AGREE. A reader can re-derive it over any payload — it is
    `_value_slot_divergence(stored.content, stored.quote) == ()` — over the FORM
    tolerances the rule applies on purpose (unit spelling, a leading dot, a
    magnitude prefix), none of which may be mistaken for a rewrite.

    ⛔ The ON arm contains a DIVERGENT pair as well, asserted ABSENT from the
    stored set (review round 2, P2: the first version's four cases were
    non-divergent BY CONSTRUCTION, so every assertion held whether or not the
    emit-site rule existed — deleting the rule left it green). MUTATION CHECK
    TARGETS: delete the emit-site rule → the divergent case reaches the payload
    and this reds; set the switch off → the falsifier's divergent pair is stored
    and the identity check flags it.
    """
    cases = (
        ("the p95 hit 4.2 seconds", "the p95 hit 4.2 seconds"),     # identical
        ("the TTL is 20 minutes", "the TTL is 20 mins"),           # unit spelling
        ("the p95 is 0.5 seconds", "the p95 is .5 seconds"),       # leading dot
        ("we saw 5k QPS", "we saw 5000 QPS"),                      # magnitude form
    )
    stored_values = []
    for content, quote in cases:
        points = _write(content, quote)["payload"]["points"]
        assert len(points) == 1, (content, quote)
        stored = points[0]
        # the value SURVIVED into what is written…
        assert content in stored["content"], (content, stored["content"])
        # …and the stored text still satisfies the identity against its own
        # verbatim quote — the two readings a consumer holds agree.
        assert v2._value_slot_divergence(
            stored["content"], stored["quote"]) == (), (content, quote, stored)
        stored_values.append(stored["content"])
    # THE DISCRIMINATING CASE: a DIVERGENT pair must not be stored at all, so the
    # identity above is a guarantee about the graph rather than a property of the
    # four pairs picked for it.
    divergent = _write("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    assert divergent["payload"]["points"] == [], (
        "a divergent pair was STORED, so the identity below is not enforced by "
        f"the write path: {divergent['payload']['points']!r}")
    assert "the p95 hit 4.0 seconds" not in stored_values
    # FALSIFIER — the identity check is not vacuous. With the door OPEN the same
    # check FLAGS the pair the invariant forbids, so a future lane cannot green
    # this test by neutering both sides.
    monkeypatch.setenv(v2.STATE_VALUE_VERBATIM_SWITCH, "0")
    leaked = _write("the p95 hit 4.0 seconds",
                    "the p95 hit 4.2 seconds")["payload"]["points"]
    assert len(leaked) == 1, leaked
    assert v2._value_slot_divergence(leaked[0]["content"],
                                     leaked[0]["quote"]), (
        "the identity check must FLAG a stored divergent pair, or the block "
        "above passes by vacancy")


def test_the_registry_row_declares_the_off_switch():
    """The row a lane reads must name the switch, its default and what OFF restores.

    A rollback nobody can find is not a rollback: the row is what an operator
    reads before flipping a guarantee off in production. The name is asserted
    against `v2.STATE_VALUE_VERBATIM_SWITCH` (review round 2, P3) so the row and
    the code cannot drift — renaming the variable without the row reds here.
    """
    row = (pathlib.Path(__file__).resolve().parent.parent
           / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    flattened = " ".join(row.split())
    assert v2.STATE_VALUE_VERBATIM_SWITCH in flattened, (
        f"the registry row does not name the enforcement's off-switch "
        f"({v2.STATE_VALUE_VERBATIM_SWITCH})")
    assert "_state_value_verbatim_enforced" in flattened, (
        "the row must name the ONE read the switch goes through")
    assert "STATE_VALUE_VERBATIM_SWITCH" in flattened, (
        "the row must name the constant as a `symbols:` anchor, or a rename "
        "leaves it pointing at a symbol that does not exist with every gate green")
    assert "defaults to **ON**" in flattened, (
        "the row must state the default, so a reader knows what unset means")
    assert "pre-#10513 REFUSAL semantics" in flattened, (
        "the row must state WHAT OFF restores — the rollback target is the "
        "whole point of the switch — and must not overclaim byte-identical "
        "pre-#10513 output (the #10513 mint-site cuts are not behind it)")


def test_the_off_switch_is_declared_in_the_env_contract():
    """`.env.example` IS this repo's env contract, and an operator rolling back an
    over-blocking refusal in production reads it — not the registry row.

    Review round 2, P2: the switch was named only in the module, the row and this
    test file, while the test's own message claimed it was in "deployment docs".
    Mirrors `tests/test_time_aware_2520.py::test_env_example_declares_the_valid_time_kill_switch`
    and `tests/test_bm25_lexical_8488.py::test_env_example_declares_the_bm25_arm_switch`:
    the name must be present AND shown as a commented assignment (the form an
    operator copies), not merely named in prose.
    """
    env = (pathlib.Path(__file__).resolve().parent.parent
           / ".env.example").read_text(encoding="utf-8")
    name = v2.STATE_VALUE_VERBATIM_SWITCH
    assert name in env, (
        f"{name} is the only way to turn the verbatim-value refusal off, and "
        ".env.example does not declare it")
    declared = [line for line in env.splitlines()
                if line.strip().startswith(f"# {name}=")
                or line.strip().startswith(f"{name}=")]
    assert declared, (
        f"{name} must be shown as a commented assignment (the example form an "
        "operator copies), not just named in prose")
    # ⚠️ Scoped to THIS block, not the whole file (review round 3, P3): a
    # file-wide substring search is green for a neighbouring knob's "default-on"
    # (the S4 delta block has one), so it could not fail for the declaration it
    # describes.
    lines = env.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip().startswith(f"# {name}=")
                 or line.strip().startswith(f"{name}="))
    block = " ".join("\n".join(lines[max(0, start - 14):start + 4]).split())
    assert "default-on" in block, (
        f"the {name} declaration must state the default, so 'unset' is "
        f"unambiguous — found in the block: {block[-200:]!r}")
    assert "ENFORCED" in block, (
        f"the {name} declaration must name what unset MEANS (enforced)")


def test_a_quote_that_only_fragments_the_point_is_not_a_contradiction():
    """Fail-open on a different slot layout: the quote may legitimately omit a
    value the point distils in (measured class, census receipts)."""
    result = _write("the session cache TTL is twenty minutes",
                    "the TTL is twenty minutes")
    assert len(result["payload"]["points"]) == 1, result["warnings"]


# ── 4. the invariant retention must NOT break: identifiers stay dropped ─────

@pytest.mark.parametrize("text,rule", [
    ("see #4118", value_gate.RULE_REFERENCE),
    ("merged deadbeef1", value_gate.RULE_HASH),
    ("see a/b/c.py", value_gate.RULE_PATH),
    ("commit 550e8400-e29b-41d4-a716-446655440000", value_gate.RULE_UUID),
])
def test_identifier_only_still_drops_identifier_residues(text, rule):
    """#4899's predicate is untouched by this change.

    Retention must never be bought by widening ``identifier_only`` — the exact
    failure #10360 names in reverse: a survival number improved by re-admitting
    the identifiers the filter exists to remove.
    """
    assert value_gate.identifier_only(text) == rule


@pytest.mark.parametrize("text", [
    "the TTL is twenty minutes",
    "the p95 hit 4.2 seconds",
    "back under 800 milliseconds",
    "a ten minute TTL",
])
def test_identifier_only_never_drops_a_state_value(text):
    """…and the two rules agree on the class they are both about."""
    assert value_gate.identifier_only(text) is None


# ── 5. the residual, PRODUCED rather than quoted ───────────────────────────

RECEIPTS = (pathlib.Path(__file__).parent / "eval" / "write_path" / "receipts"
            / "7947-arms-2026-10-18")
SESSIONS = ("wp01_quarry_debug", "wp02_lumen_refactor", "wp03_ember_design",
            "wp04_aurora_perf", "wp05_retro_writeup", "wp06_quarry_rollout",
            "wp07_bluepeak_followup")
ARMS = ("raw", "narrative")


def test_the_residual_is_measured_on_the_committed_census_corpus():
    """The registry row's residual, re-derived here instead of quoted.

    `#10360`'s second indicator: the survival/residual figure must be PRODUCED
    by a lane, not copied from a comment. Re-running this test reproduces the
    row's numbers from the same sealed receipts the span census reads — so a
    change to the rule's reach moves this test, and the row cannot cite a
    measurement nothing can reproduce.

    ⚠️ The first pair here is the HARD-clamped one (the caps production used to
    truncate the STORED point with) and the second the UNCLAMPED pair; the third
    reading — `_write_span`, the token-boundary cut the write now stores AND
    judges — is pinned below, because it is the shipped one. All three give the
    same `(62, 0)` on this corpus, which is what makes the numbers in the
    registry row safe to state once.
    """
    unclamped_judged = unclamped_refused = 0
    for session in SESSIONS:
        blob = json.loads((RECEIPTS / f"{session}.arms.json").read_text())
        for arm in ARMS:
            for point in blob["embed_list"][arm]["points"]:
                raw_content = str(point.get("content") or "").strip()
                raw_quote = str(point.get("quote") or "").strip()
                if not v2._carries_value(raw_content, raw_quote):
                    continue
                c = v2._value_slots(raw_content)
                q = v2._value_slots(raw_quote)
                if c and q and tuple(a for a, _ in c) == tuple(a for a, _ in q):
                    unclamped_judged += 1
                if v2._value_slot_divergence(raw_content, raw_quote):
                    unclamped_refused += 1

    points = value_bearing = judged = refused = 0
    for session in SESSIONS:
        blob = json.loads((RECEIPTS / f"{session}.arms.json").read_text())
        for arm in ARMS:
            for point in blob["embed_list"][arm]["points"]:
                content = str(point.get("content") or "").strip()[:v2._MAX_CONTENT]
                quote = str(point.get("quote") or "").strip()[:v2._MAX_QUOTE]
                points += 1
                if not v2._carries_value(content, quote):
                    continue
                value_bearing += 1
                # ⛔ THE SHIPPED PREDICATE, not an approximation of it (#10513
                # review cycle 18): `judged` must be exactly what
                # `_value_slot_divergence` will look at, which since the scale
                # rule is the anchors AND the per-slot SCALE — a pair whose
                # scales differ is outside the judged set. Counting anchors alone
                # overstated the figure and hid the fail-open cost of the new
                # exclusions.
                content_slots = v2._value_slots_scaled(content)
                quote_slots = v2._value_slots_scaled(quote)
                if (content_slots and quote_slots
                        and tuple((a, sc) for a, _, sc in content_slots)
                        == tuple((a, sc) for a, _, sc in quote_slots)):
                    judged += 1
                if v2._value_slot_divergence(content, quote):
                    refused += 1
    # A positive control, so the pin has a direction that can FAIL: `refused`
    # alone is self-derived (a weakened rule keeps it at 0), so the rule's
    # STRENGTH is pinned separately here.
    assert v2._value_slot_divergence("the TTL is 20 minutes",
                                     "the TTL is 20 hours")
    assert not v2._value_slot_divergence("the TTL is 20 minutes",
                                         "the TTL is 20 minutes")
    assert points == 217, points
    assert value_bearing == 112, value_bearing
    # The measured residual the registry row states. A refusal on this corpus
    # would be a false accusation against a sealed truthful extraction — the
    # exact failure the previous attempt at this issue was rejected for.
    assert (judged, refused) == (62, 0), (judged, refused)
    assert (unclamped_judged, unclamped_refused) == (62, 0), (
        unclamped_judged, unclamped_refused)
    # ⛔ AND THE READING THE WRITE PATH ACTUALLY USES (review cycle-14 P2): the
    # three arms above compared a HARD-clamped and an UNCLAMPED pair, and the
    # shipped judge is neither — it is `_write_span(...)`, the token-boundary cut
    # the write also STORES. Pinning only the two older readings let the shipped
    # one drift out of the covered set with every gate green (measured on the
    # cycle-13 cut: 61 judged / 51 fail open, while this file still asserted 62).
    span_judged = span_refused = 0
    for session in SESSIONS:
        blob = json.loads((RECEIPTS / f"{session}.arms.json").read_text())
        for arm in ARMS:
            for point in blob["embed_list"][arm]["points"]:
                c = v2._write_span(str(point.get("content") or "").strip(),
                                   v2._MAX_CONTENT)
                q = v2._write_span(str(point.get("quote") or "").strip(),
                                   v2._MAX_QUOTE)
                if not v2._carries_value(c, q):
                    continue
                content_slots = v2._value_slots_scaled(c)
                quote_slots = v2._value_slots_scaled(q)
                if (content_slots and quote_slots
                        and tuple((a, sc) for a, _, sc in content_slots)
                        == tuple((a, sc) for a, _, sc in quote_slots)):
                    span_judged += 1
                if v2._value_slot_divergence(c, q):
                    span_refused += 1
    assert (span_judged, span_refused) == (62, 0), (span_judged, span_refused)


# ── 6. the two review findings this rule must never repeat ─────────────────

def test_a_value_that_straddles_the_quote_clamp_is_not_a_rewrite():
    """Review P1: the module's own truncation must not accuse the model.

    The quote is cut to `_MAX_QUOTE` before the point is built, so a HARD cut of
    a value sitting across it reads as a SHORTER value ("1200" -> "1"). The cut
    is therefore made at a TOKEN boundary (`_write_span`), which drops the split
    token whole; a truthful point over a long quote is written.
    """
    # The cut must land INSIDE the digits: "1200" -> "12" with the anchor
    # ("ttl") surviving, so a clamped read is a DIVERGENT read. Putting the
    # whole value past the cut binds no value at all and fails open for an
    # unrelated reason — a test that cannot fail (review cycle 2, P2).
    content = "the ttl is 1200"
    quote = ("x" * (v2._MAX_QUOTE - 7)) + " ttl 1200 seconds"
    assert len(quote) > v2._MAX_QUOTE
    assert v2._value_slot_divergence(content, quote[:v2._MAX_QUOTE]), (
        "the clamped pair no longer diverges — this test would not pin the fix")
    assert not v2._value_slot_divergence(content, quote)
    result = _write(content, quote)
    assert len(result["payload"]["points"]) == 1, result["warnings"]


def test_the_judged_pair_IS_the_stored_pair():
    """Review cycle-14 P2: a judged pair that differs from the stored one is a hole.

    Two measured holes, both closed here and both pinned by the STORED text:

    * the write HARD-cut content/quote while the judge saw a token-cut span, so a
      value split at the cap was stored as its truncated prefix (`… hit 4.`) and
      was never judged — the graph then held a value the model never wrote, with
      no warning;
    * the judge saw the UNSTRIPPED quote while the write stores the stripped one,
      so two leading spaces shifted the cut far enough to drop a value the stored
      quote states (fail-open, always in that direction).

    ⛔ Pinned on `payload`, not on the helper: the defect was which STRING the two
    halves used, so a helper-only assertion cannot see it.
    """
    # (1) the value's token straddles `_MAX_CONTENT`, and the quote states a
    # DIFFERENT value in the same slot. Whatever is stored must therefore be
    # judged: either the point is refused, or the stored pair does not diverge.
    content = "the " * 247 + "a " + "p95 hit 4.0 seconds"
    quote = "the p95 hit 5.5 seconds"
    assert len(content) > v2._MAX_CONTENT
    span = v2._write_span(content, v2._MAX_CONTENT)
    assert content.startswith(span) and span.endswith(" "), span[-12:]
    assert not span.rstrip().endswith("4."), span[-12:]
    result = _write(content, quote)
    stored = result["payload"]["points"]
    if stored:  # written ⇒ the STORED pair must not diverge and must not be cut
        c = stored[0]["content"]
        q = stored[0].get("quote") or ""
        assert not v2._value_slot_divergence(c, q), (c, q)
        assert not c.endswith("4."), c
    # (2) leading whitespace on the quote must not move the judged span: the
    # stored quote states 5.5 against a point saying 4.0, so it is refused.
    quote_padded = "  " + ("the " * 47) + "p95 hit 5.5 seconds"
    span_stripped = v2._write_span(quote_padded.strip(), v2._MAX_QUOTE)
    # the padding must actually MOVE the cut, or this pin proves nothing: the
    # unstripped span loses `5.5` (whose token the shifted cut splits), while the
    # stripped span — the one the write stores — keeps it
    assert span_stripped != v2._write_span(quote_padded, v2._MAX_QUOTE)[2:]
    assert "5.5" in span_stripped, span_stripped[-20:]
    result = _write("the p95 hit 4.0 seconds", quote_padded)
    assert result["payload"]["points"] == [], result["warnings"]
    assert any("REWRITES its own verbatim" in w for w in result["warnings"])


def test_a_divergence_in_the_text_the_write_DISCARDS_is_not_a_rewrite():
    """Review cycle-13 P2: judge what is WRITTEN, not what is thrown away.

    Judging the UNCLAMPED pair (the straddle fix above) also judged text the
    write DISCARDS. Measured before this pin: a 1219-char content whose value
    sits past `_MAX_CONTENT` was REFUSED, while the stored
    `content[:_MAX_CONTENT]` binds no value at all — so a durable memory was
    withheld over a rewrite in a tail that never reaches the graph, which is the
    over-refusal direction this rule must never take.

    The cut is at a TOKEN boundary, so the straddle protection is kept: an
    incomplete trailing token is dropped whole and can never be read short.
    """
    content = "the " * 300 + "p95 hit 4.0 seconds"          # value past the cap
    quote = "the p95 hit 4.2 seconds"
    assert len(content) > v2._MAX_CONTENT
    assert v2._value_slots(content[:v2._MAX_CONTENT]) == (), (
        "the kept span must bind no value, or this pin proves nothing")
    # …while the unclamped pair DOES diverge, which is what used to refuse it
    assert v2._value_slot_divergence(content, quote)
    result = _write(content, quote)
    assert len(result["payload"]["points"]) == 1, result["warnings"]
    # ⛔ positive control: the SAME divergence inside the kept span still refuses,
    # so this cannot pass by the rule having gone blind to long content
    kept_content = "the p95 hit 4.0 seconds" + " the" * 300
    assert v2._value_slot_divergence(
        v2._write_span(kept_content, v2._MAX_CONTENT), quote)
    result = _write(kept_content, quote)
    assert result["payload"]["points"] == [], result["warnings"]
    assert any("REWRITES its own verbatim" in w for w in result["warnings"])


def test_a_refused_point_leaves_no_supersession_naming_it():
    """Review P2: the refusal must run BEFORE any supersede record is collected.

    Firing late left `payload.supersessions` naming a pid that is never written,
    and the commit layer reported the dangling target as a failed supersede — a
    write bug reported for an extraction refusal.
    """
    prior = {"id": "pt_prior", "content": "the p95 hit 4.2 seconds",
             "about_entities": []}
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    result = v2.execute_embed(
        embed, {"points": [prior], "entities": []}, session_id="s_10360")
    assert result["payload"]["points"] == []
    assert result["payload"].get("supersessions") in (None, []), (
        result["payload"].get("supersessions"))
    # …and the reversal the refused point carried is not dropped SILENTLY
    # (#9906: none of the drop doors is silent).
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    embed["points"][0]["supersedes"] = "pt_prior_claim"
    result = v2.execute_embed(embed, {"points": [], "entities": []},
                              session_id="s_10360")
    assert result["payload"]["points"] == []
    assert any("pt_prior_claim" in w for w in result["warnings"]), (
        result["warnings"])


def test_a_refused_value_cannot_re_enter_the_graph_through_an_operator():
    """Review cycle 2, P2: mint-before-wire must not resurrect a refused value.

    `_mint_endpoint` builds a `statement` Point out of the MODEL's text, so an
    operator naming the refused point's content used to write the rewritten value
    anyway — the refusal held for the payload point and was undone one step
    later. Measured before the fix: the payload held two points, one of them
    exactly `'the p95 hit 4.0 seconds'`.
    """
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    embed["operators"] = [{
        "src": "the p95 hit 4.0 seconds",
        "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}]
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert "the p95 hit 4.0 seconds" not in contents, contents
    assert any("refused for rewriting its own verbatim quote" in w
               for w in result["warnings"]), result["warnings"]


@pytest.mark.parametrize("variant", [
    "the p95 hit 4.0 seconds.",   # a trailing period
    "the p95 hit 4.0 second",     # a singular unit
])
def test_a_refused_value_cannot_re_enter_the_graph_under_a_VARIANT_name(variant):
    """#10513 review P2: the guard is the VALUE, not the byte string.

    The first version keyed only on ``_norm`` equality, which is EXACT — so an
    operator naming the refused ref with one character added re-minted the
    rewritten value as an endpoint Point, which is the resurrection 1(b) exists
    to stop. Measured before the fix: the payload held
    ``'the p95 hit 4.0 seconds.'`` and ``'the p95 hit 4.0 second'``.

    The second key is ``_value_slots``: a variant that states the SAME value in
    the SAME slot is the same resurrection even when its bytes differ. An
    endpoint carrying no value has no slots and is unaffected (positive control
    below), so this cannot be read as "any operator endpoint is refused".
    """
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    embed["operators"] = [{
        "src": variant,
        "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}]
    result = v2.execute_embed(embed, _RESOLVED_SEARCH, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert variant not in contents, contents
    # positive control: an endpoint with no value in it is still minted, so the
    # slot key is a value guard and not a blanket refusal of the operator path.
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    embed["operators"] = [{
        "src": "the nightly rebuild window",
        "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}]
    result = v2.execute_embed(embed, _RESOLVED_SEARCH, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert "the nightly rebuild window" in contents, contents


def test_the_mint_guard_matches_the_SLOT_TUPLE_exactly_so_a_WIDER_variant_mints():
    """Residual (1b-ii), PINNED so closing it has to move the registry row.

    #10513 review cycle-13 P2, measured: the mint guard's second key is
    ``_value_slots(content)`` compared for EQUALITY against the refused point's
    own slot tuple, so a variant that states the same value in a SUBSET, a
    SUPERSET, or a REORDERED slot layout is NOT matched and re-mints the
    rewritten value as an endpoint Point — the resurrection 1(b) exists to stop,
    one notch wider than the byte-exact and slot-exact variants above.

    ⛔ Why this is disclosed rather than "narrowed": a containment rule (match
    when any refused slot appears in the endpoint) does NOT close it either —
    measured, the reordered endpoint ``we saw 4.0 seconds on the p95 this time``
    binds ``(saw, 4)``/``(on, 95)``, so it matches NO refused slot — while it
    WOULD suppress every unrelated endpoint that merely mentions the same number
    in the same anchor slot, i.e. it buys the subset/superset shapes with a large
    expansion of residual (1b-i)'s over-suppression. Closing it properly means
    identifying the CLAIM, which is the parallel-classifier shape the review P2
    already rejected. The shape needs the model to emit an operator naming a
    WIDER variant of an already-refused ref, and the committed census produces 0
    refusals at all.
    """
    WIDER_VARIANTS = {
        "subset": "hit 4.0 seconds",
        "superset": "the p95 hit 4.0 seconds and the p99 is 9 seconds",
        "reordered": "we saw 4.0 seconds on the p95 this time",
    }
    for name, variant in WIDER_VARIANTS.items():
        embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
        embed["operators"] = [{
            "src": variant,
            "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}]
        result = v2.execute_embed(embed, _RESOLVED_SEARCH, session_id="s_10360")
        contents = [p["content"] for p in result["payload"]["points"]]
        assert variant in contents, (name, contents)


def test_a_DIFFERENT_claim_sharing_an_anchor_and_value_is_suppressed_too():
    """Residual (1b-i), PINNED so narrowing the key has to move the row.

    #10513 review cycle-11 P3, measured: the mint guard's slot key is
    ``(anchor, sign, core, unit)`` per slot, so it carries NO identity for the
    CLAIM. Refusing ``the TTL is 20 minutes`` (its quote says ``20 hours``)
    therefore also suppresses the operator endpoint ``the cache TTL is 20
    minutes`` — a DIFFERENT claim that merely shares the anchor word and the
    value. On ``main`` that endpoint is minted, so in this narrow shape one
    operator edge is lost; the loss is reported (``operator_endpoints_minted``
    and the edge's own dangling-edge warning), never silent, and it is reported
    as a VARIANT of the refusal rather than as an accusation that this claim
    rewrote its quote — which is false of it.

    The behaviour STAYS: separating that endpoint from a genuine variant means
    judging the claim's non-value words, i.e. a second value classifier beside
    the production engine — the P2 that killed the previous attempt at this
    issue. It is stated as residual (1b-i) in ``config/v4-mechanisms.yml``
    instead. The positive control below is the load-bearing half: with NO
    refusal in the batch the same endpoint IS minted, so the drop is caused by
    the refusal and not by the mint path.
    """
    refusal = _embed("the TTL is 20 minutes", "the TTL is 20 hours")
    refusal["operators"] = [{
        "src": "the cache TTL is 20 minutes",
        "dst": "the nightly rebuild window", "op_type": "IMPL"}]
    result = v2.execute_embed(refusal, {}, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert "the cache TTL is 20 minutes" not in contents, contents
    assert any("value matches a point refused" in w for w in result["warnings"]), (
        result["warnings"])
    # …and it does NOT inherit the exact-content arm's wording, which would be a
    # false accusation against a claim that never rewrote anything.
    assert not any("whose point was refused" in w for w in result["warnings"]), (
        result["warnings"])

    # positive control: the SAME endpoint, in a batch with no refusal, mints.
    control = _embed("the TTL is 20 minutes", "the TTL is 20 minutes")
    control["operators"] = [{
        "src": "the cache TTL is 20 minutes",
        "dst": "the nightly rebuild window", "op_type": "IMPL"}]
    result = v2.execute_embed(control, _RESOLVED_SEARCH, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert "the cache TTL is 20 minutes" in contents, contents


# ── 7. pins for the cycle-3 findings ──────────────────────────────────────

def test_the_unit_table_covers_every_unit_the_engine_can_fuse():
    """A fused unit must never be UNKNOWN here.

    Review cycle 2 closed the wildcard by returning an unmapped suffix as
    itself; review cycle 3 measured that this created a false-refusal class
    (`800millis`/`800ms`, `20kms`/`20km`, `32-byte`/`32-bytes` — the last shape
    occurs 4 times in the committed census), so an unknown unit now fails OPEN
    and this structural pin keeps a fusable unit from being unknown at all.
    Mutating any of the six `k/km/mi/m/kg/lb` rows reds this.
    """
    assert set(v2._UNIT_WORDS) <= set(v2._UNIT_EQUIV)


def test_the_unit_tables_carry_no_DUPLICATED_literal_row():
    """A merge union can add a row TWICE and nothing at runtime notices.

    `_UNIT_EQUIV` is a dict literal, so a duplicate key silently keeps the LAST
    value: the two sides of a forward-merge can each add `"mile": "mi"` in a
    disjoint hunk, git reports no conflict, and the tree reads as if it were
    hand-written once. That is exactly what happened folding #10804 onto this
    branch (the widen and the fold's `mile`/`miles` row), and a missing
    re-derivation is invisible in every behavioural test because the duplicate
    resolves to the same identity.

    ⛔ This must read the SOURCE, not the runtime dict: by the time the literal
    is evaluated the duplicate is gone. Non-vacuity — appending
    `"mile": "mi",` to `_UNIT_EQUIV` in `tortoise/extractor_v2.py` reds this
    with the key and both line numbers.
    """
    import ast
    from pathlib import Path as _P

    src = _P(v2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"_UNIT_EQUIV", "_UNIT_WORDS"}
    seen_nodes = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        value = node.value
        if isinstance(value, ast.Call):        # frozenset({...})
            value = value.args[0] if value.args else None
        if isinstance(value, ast.Set):
            literal = {(k.value, k.lineno) for k in value.elts
                       if isinstance(k, ast.Constant)}
        elif isinstance(value, ast.Dict):
            literal = {(k.value, k.lineno) for k in value.keys
                       if isinstance(k, ast.Constant)}
        else:                                  # pragma: no cover - shape guard
            raise AssertionError(
                f"{target.id} is assigned a {type(value).__name__}, not a literal "
                "— this pin reads the SOURCE, so its shape has to stay inspectable")
        seen_nodes += 1
        by_value: dict[object, list[int]] = {}
        for val, lineno in literal:
            by_value.setdefault(val, []).append(lineno)
        dupes = {v: lines for v, lines in by_value.items() if len(lines) > 1}
        assert not dupes, (
            f"{target.id} declares the same key twice: {dupes} — a dict literal "
            "keeps the last one silently, so this is a merge union that was never "
            "de-duplicated, not a harmless repetition")
    assert seen_nodes == 2, (
        f"found {seen_nodes} of the two unit-table literals this pin covers — the "
        "table was renamed or its shape changed, so this pin is now vacuous")


def test_the_carve_outs_own_ADJACENT_time_units_are_judged():
    """Residual (d)'s ADJACENCY half, closed: a unit beside a value is judged.

    ``STATE_VALUE_CARVE_OUT`` states its operational examples in time units —
    "back under 800 milliseconds", "a ten minute TTL". At ``a6e20f05b05d`` every
    one of those unit words was OUTSIDE ``_UNIT_WORDS``: ``_value_bindings``
    fuses a following token only when that table carries it, so ``800 seconds``
    bound a value with NO unit, ``_value_identity_matches`` read an absent unit
    as the deliberate "one side omits the unit" tolerance, and the rewrite was
    WRITTEN as verbatim. That is the silent-correctness class this issue exists
    to close, on the guarantee's own worked examples.

    ⛔ NOT the whole of residual (d): the carve-out's RATE example ("forty
    thousand requests per day") has no unit adjacent to its number, so it is
    still unjudged and is named as residual (d) in `config/v4-mechanisms.yml`.
    This test is scoped to what it actually closes.

    MUTATION CHECK TARGET: remove ``seconds``/``minute``/``days``/``miles``
    (or the ``d``/``wk``/``mo``/``y``/``mi`` identities) from the tables and
    this goes red.
    """
    for content, quote in (
            ("back under 800 seconds", "back under 800 milliseconds"),
            ("a ten minute TTL", "a ten hour TTL"),
            ("the lease is 3 hours", "the lease is 3 days"),
            ("the drive is 20 miles", "the drive is 20 km"),
            # The two abbreviations the abbreviation set itself omitted: a
            # table with a hole in it is how this gap survived review, so the
            # siblings the widen introduced are pinned, not just the words.
            ("the lease is 3 mos", "the lease is 3 years"),
            ("back under 800 millisec", "back under 800 seconds")):
        assert v2._value_slot_divergence(content, quote), (
            content, quote)
    # …and the WRITE refuses the point, not just the pure function
    result = _write("back under 800 seconds", "back under 800 milliseconds")
    assert result["payload"]["points"] == [], (
        "a time-unit rewrite of the carve-out's own example reached the write "
        "path — residual (d) is open again")
    assert any("REWRITES its own verbatim quote" in w
               for w in result["warnings"]), result["warnings"]
    # ⛔ THE FALSE-ACCUSATION DIRECTION, pinned for EVERY new identity: the SAME
    # value with its unit spelled differently (word vs symbol vs attached) must
    # stay EQUAL. Without these rows a typo in any new value (`miles` → `ml`,
    # `days` → `dd`) turns a truthful point into a refusal — the direction this
    # carve-out must never break — and nothing else in this file notices.
    for content, quote in (
            ("the drive is 20 miles", "the drive is 20 mi"),
            ("the lease is 3 days", "the lease is 3 d"),
            ("the sprint is 2 weeks", "the sprint is 2 wk"),
            ("the lease is 3 months", "the lease is 3 mo"),
            ("the SLA is 5 years", "the SLA is 5 yr"),
            ("the cache TTL is 900 milliseconds", "the cache TTL is 900 ms"),
            ("the cache TTL is 900 milliseconds", "the cache TTL is 900 millisec"),
            ("the lease is 3 months", "the lease is 3 mos"),
            ("the TTL is 20 minutes", "the TTL is 20 mins"),
            ("the share is 20 percent", "the share is 20%"),
            ("back under 800 milliseconds", "back under 800ms")):
        assert v2._value_slot_divergence(content, quote) == (), (
            content, quote)
    # …and the RATE form the carve-out also names is NOT judged: pinned so the
    # registry row's surviving (d) cannot be closed by accident, and so a future
    # lane closing it has to move that row.
    assert v2._value_slot_divergence(
        "forty thousand requests per day",
        "forty thousand requests per week") == ()


def test_a_magnitude_prefix_beside_a_unit_word_is_COMPOSED_not_refused():
    """#10804 review P1 — the widen must not REFUSE a truthful point.

    ``_value_bindings`` glues a value to the unit word written beside it with NO
    prefix arithmetic (``value + seq[i + 1]``), so ``20k`` + ``miles`` binds ONE
    token, ``20kmiles``, whose suffix is not a ``_UNIT_EQUIV`` key. It therefore
    reads as an UNKNOWN unit with the numeric core still ``20``, and against
    ``20000 miles`` (core ``20000``, unit ``mi``) the cores DIVERGE.

    ⛔ MEASURED, not predicted: every row below returned ``()`` at `origin/main`
    `525fde4a9` — ``miles``/``days``/``milliseconds``/``weeks``/``percent`` were
    not in ``_UNIT_WORDS``, so the pair fell open on the unknown-unit tolerance —
    and every one REFUSED with the widened tables and no composition arm. That
    is the FALSE-ACCUSATION direction this carve-out must never take, so the
    widen is only admissible WITH the composition (``_composed_identity``).

    MUTATION CHECK TARGET: delete the ``_composed_identity`` arm from
    ``_value_identity_matches`` and the first loop goes red; the second loop (the
    rewrites) must stay green either way — it is what stops the arm from being
    widened into a tolerance that lets a real rewrite through.
    """
    # A prefix followed by a SPACE-SEPARATED unit word: one value, two spellings.
    for content, quote in (
            ("the fleet drove 20000 miles", "the fleet drove 20k miles"),
            ("the lease is 3000 days", "the lease is 3k days"),
            ("back under 5000 milliseconds", "back under 5k milliseconds"),
            ("the sprint is 2000 weeks", "the sprint is 2k weeks")):
        assert v2._value_slot_divergence(content, quote) == (), (
            content, quote)
    # …and the WRITE does not withhold the point (the whole of the harm).
    result = _write("the fleet drove 20000 miles", "the fleet drove 20k miles")
    assert result["payload"]["points"], (
        "a truthful point was WITHHELD because `20k miles` and `20000 miles` "
        "are one value written two ways — the false-accusation direction")
    assert not any("REWRITES its own verbatim quote" in w
                   for w in result["warnings"]), result["warnings"]
    # ⛔ NOT VACUOUS IN THE OTHER DIRECTION: composing the prefix must not turn a
    # rewritten number or a changed unit into a match. `20k miles`/`21k miles`
    # composes to 20000 mi against 21000 mi, and `20k miles`/`20000 km` to
    # 20000 mi against 20000 km.
    for content, quote in (
            ("the fleet drove 20k miles", "the fleet drove 21000 miles"),
            ("the fleet drove 20k miles", "the fleet drove 21k miles"),
            ("the fleet drove 20k miles", "the fleet drove 20000 km"),
            ("the lease is 3k days", "the lease is 3000 hours")):
        assert v2._value_slot_divergence(content, quote), (content, quote)
    # ⛔ A unit the table ALREADY KNOWS is never composed, and a multi-run core
    # (one suffix for several numbers) is never scaled — pinned so a later widen
    # cannot quietly reinterpret `20km` as 20000 metres.
    assert v2._value_core("20km") == ("", "20", "km")
    assert v2._value_core("24h/7d/4w") == ("", "24/7/4", "w")
    assert v2._composed_identity(("", "20", "km")) is None
    # ⛔ `kdays`, not `kw`: `w` is not a `_UNIT_EQUIV` key, so a `kw` assertion
    # passes at the `rest is None` step and never reaches the multi-run guard it
    # claims to pin (measured — review P3).
    assert v2._composed_identity(("", "24/7/4", "kdays")) is None
    # …and the AMBIGUOUS colloquial spelling keeps today's fail-open rather than
    # becoming a refusal: `20kms` composes, but `20km` does not, so the pair is
    # still tolerated (reading "kms" as kilo-milliseconds would REFUSE).
    assert v2._value_slot_divergence("the ride was 20kms", "the ride was 20km") == ()
    # NOT FUSED, pinned so a later lane cannot quietly re-add percent/pct.
    assert "percent" not in v2._UNIT_WORDS and "pct" not in v2._UNIT_WORDS
    assert v2._value_slot_divergence(
        "the share is 20k%", "the share is 20000 percent") == ()
    # ⛔ THE percent family is EQUAL by the PRE-EXISTING factor arm, not by the
    # composition arm — it does not discriminate the arm under test, so it is
    # asserted here with its own gate rather than inside the loop above
    # (review P3).
    assert v2._value_slot_divergence(
        "the share is 20k percent", "the share is 20000 percent") == ()
    # ⛔ AND THE ONE NEW TOLERANCE IS DISCLOSED, not silently opened (review P3).
    # `20kms` is the ambiguous colloquial spelling; composing `k`+`ms` gives it a
    # kilo-MILLISECOND reading, so it now AGREES with `20000 ms` — REFUSED at
    # `origin/main` `525fde4a9`. That is the fail-open direction (a possible
    # rewrite tolerated), not a false refusal, and it is the price of the arm;
    # neither UCUM nor Pint permits a prefix on an already-prefixed atom, so the
    # unit tables do not sanction it — this pin exists so the trade is recorded.
    assert v2._value_slot_divergence("the ride was 20kms", "the ride was 20000 ms") == ()
    # …while the colloquially-correct reading stays REFUSED at both heads.
    assert v2._value_slot_divergence("the ride was 20kms", "the ride was 20000 m")
    # ⛔ THE SECOND case changed SHAPE in the merge of #10804's widen with the
    # fold's FUSION-TIME composition, and the change is a CORRECTION rather than a
    # new tolerance: a fused suffix outside `_UNIT_EQUIV` (`kseconds`) used to
    # leave the core UNSCALED at `5`, so it coincided with `5 minutes` and the
    # unknown-unit tolerance fired — `the batch took 5k seconds`/`… 5 minutes`
    # REFUSED at `origin/main` `525fde4a9` and TOLERATED with the widen alone.
    # That was a MISSED REWRITE (5k seconds is 5000 s, not 5 min) sitting in the
    # fail-open direction. With `_compose_magnitude_and_unit` in the fusion
    # branch the prefix is multiplied, so the pair binds `5000s` against `5min`
    # and is REFUSED — the truthful verdict. Pinned in BOTH directions so neither
    # half can be narrowed silently.
    assert v2._value_slot_divergence(
        "the batch took 5k seconds", "the batch took 5 minutes") == (
            ("took", "5000s", "5min"),)
    # …and the same value written two ways still AGREES (the composition is a
    # reading, not a refusal): `5k seconds` is 5000 seconds.
    assert v2._value_slot_divergence(
        "the batch took 5k seconds", "the batch took 5000 seconds") == ()
    # …and the false accusation the SAME mechanism REPAIRS, which is why the
    # trade was taken: `3k minutes`/`3000 minutes` REFUSED at base.
    assert v2._value_slot_divergence(
        "the job ran 3k minutes", "the job ran 3000 minutes") == ()
    # ⛔ THE OPPOSITE DIRECTION IS STILL REFUSED — a FALSE ACCUSATION recorded as
    # #10876, not fixed here. When the magnitude side's spelling is UNMAPPED
    # (`millis`, `dys`) the binding is a bare `5k`, and `_magnitude_agrees`'
    # `_K_ABBREVIATES` gate ({"km", "kg"}) refuses it against any KNOWN unit, so
    # a truthful point is withheld. Pre-existing (`5k millis`/`5000 hours`
    # refuses at `origin/main` `525fde4a9`); the widen extended which right-hand
    # units trigger it. Pinned so it cannot start agreeing silently either — the
    # gate's narrowness is a RECORDED DECISION (#10513 review P1: `20k`/`20m` is
    # 20 km against 20 metres), so #10876 must move before this changes.
    assert v2._value_slot_divergence("the batch took 5k millis", "the batch took 5000 ms")
    assert v2._value_slot_divergence("the lease is 5k dys", "the lease is 5000 days")
    # ⛔ AND THE MOST GENERAL INSTANCE: a BARE `5k`, with NO unit word at all on
    # that side. #10876's first scope clause used to require an unmapped
    # spelling, which excludes this shape entirely — yet it is the same refusal
    # and the widest one (every newly fused unit triggers it). TOLERATED at
    # `origin/main` `525fde4a9`, REFUSED here; pinned so #10876 cannot be closed
    # against the narrow shape alone.
    assert v2._value_slot_divergence("the took 5k", "the took 5000 ms")
    assert v2._value_slot_divergence("the took 5k", "the took 5000 days")
    assert v2._value_slot_divergence("the took 5k", "the took 5000 second")
    # …while the units `k` may abbreviate, and a bare number, still agree.
    assert v2._value_slot_divergence("the took 5k", "the took 5000 km") == ()
    assert v2._value_slot_divergence("the took 5k", "the took 5000") == ()
    assert v2._value_slot_divergence(
        "the batch took 5k millis", "the batch took 5000 millis") == ()


def test_a_composed_prefix_against_a_UNITLESS_side_keeps_the_omitted_unit_tolerance():
    """#10804 review (cycle 7) P2 — the widen newly WITHHELD a truthful point.

    The composition arm's match was `composed == other`, an EXACT identity
    equality, so it could only rescue a side that STATES a unit. A side with NO
    unit at all is ``('', '20000', None)``, which no composed identity can equal:
    so ``20k miles`` against ``20000`` fell through to the core comparison (20
    against 20000) and the write REFUSED the point.

    ⛔ MEASURED at `origin/main` `525fde4a9` (a scratch worktree, not a revert):
    every row in the first loop returned ``()`` there — `miles`/`days`/`seconds`/
    `milliseconds` were not in `_UNIT_WORDS`, so the value bound as a BARE `k`
    and `_magnitude_agrees` closed the gap — and every one REFUSED at the head
    before this fix. That is the false-accusation direction this carve-out must
    never take, introduced by the widen.

    MUTATION CHECK TARGET: drop the `other[2] is None` branch from
    `_composed_identities_agree` and the first loop goes red; the second loop (a
    rewritten number, a re-read unit, a stated spelling outside the tables) must
    stay green either way — it is what stops the tolerance from becoming one that
    lets a real rewrite through.
    """
    # The unit is written on ONE side only — the module's oldest tolerance.
    for content, quote in (
            ("the fleet drove 20000", "the fleet drove 20k miles"),
            ("the lease is 3000", "the lease is 3k days"),
            ("back under 5000", "back under 5k milliseconds"),
            ("the batch took 5000", "the batch took 5k seconds")):
        assert v2._value_slot_divergence(content, quote) == (), (
            content, quote)
    # ...and the WRITE does not withhold the point.
    result = _write("the fleet drove 20000", "the fleet drove 20k miles")
    assert result["payload"]["points"], (
        "a truthful point was WITHHELD because `20k miles` and `20000` are one "
        "value with the unit written on one side only")
    assert not any("REWRITES its own verbatim quote" in w
                   for w in result["warnings"]), result["warnings"]
    # The arm itself: the fold rescues a UNITLESS side, and this is the exact
    # equality branch still working beside it.
    assert v2._composed_identities_agree(("", "20", "kmiles"),
                                         ("", "20000", "mi"))
    assert v2._composed_identities_agree(("", "20", "kmiles"),
                                         ("", "20000", None))
    # ⛔ NOT VACUOUS IN THE OTHER DIRECTION — a rewritten NUMBER still refuses,
    # against a unitless side as much as against a stated one.
    for content, quote in (
            ("the fleet drove 20k miles", "the fleet drove 21000"),
            ("the lease is 3k days", "the lease is 3001"),
            ("the batch took 5k seconds", "the batch took 5001")):
        assert v2._value_slot_divergence(content, quote), (content, quote)
    # ...and a re-read UNIT does, including against a side that states its own.
    for content, quote in (
            ("the fleet drove 20k miles", "the fleet drove 20000 km"),
            ("the lease is 3k days", "the lease is 3000 hours"),
            ("the batch took 5k seconds", "the batch took 5000 minutes")):
        assert v2._value_slot_divergence(content, quote), (content, quote)
    # ⛔ THE TOLERANCE IS SCOPED TO `None`, and the scope is load-bearing: a side
    # stating an UNKNOWN SPELLING (`millis`) is NOT rescued this way — that
    # refusal is `_magnitude_agrees`' `_K_ABBREVIATES` gate, a recorded decision
    # (#10513 review P1) and #10876's open root, so this fix must not reach it.
    assert not v2._composed_identities_agree(("", "20", "kmiles"),
                                            ("", "20000", "millis"))
    assert v2._value_slot_divergence("the batch took 5k millis",
                                     "the batch took 5000 ms")


#: ⛔ THE REOPENING SCAN IS NO LONGER A BLOCK-SCOPED LIST (#11223). It lived here
#: as `_ADJACENCY_REOPENING` and was applied to the `(d)` block's sentences and
#: then to a narrow tail; BOTH bounds are gone, because a reopening appended
#: where a lane APPENDS it — after the last residual — was outside every one of
#: them (`"(d2)" in block` → False on a 10,019-char block). The list now lives
#: beside its two partners, `_RESIDUAL_OPENNESS` (what it became) and
#: `_RESIDUAL_CLASS_ANCHOR` (what the claim must be ABOUT), and the scan covers
#: the whole residual region. See `_residual_reopening_claims`.

#: The closure ASSERTION the adjacency sentence must carry. Not the bare word
#: `closed`: a sentence reading "the ADJACENCY half is now UNJUDGED, unlike the
#: closed spellings" contains `closed` while asserting the gap is open (review
#: cycle 7, MEASURED: it PASSED the `"closed" in sentence` check, at both the
#: previous revision and this one).
_ADJACENCY_CLOSED_CLAIM = (
    r"\b(is|are|was|were)\b[^.;]{0,20}\bclosed\b",
    # ⛔ ONLY THE FIRST ALTERNATIVE HAS A FALSIFIER (#11258 review round 23 `P2`,
    # MEASURED): delete any of the three below and the whole file stays GREEN —
    # they are exercised by no proof, because the row's own sentence happens to use
    # the `is … closed` form. They are carried as DECLARED SPECULATION, not as
    # measured coverage: the failure direction here is OVER-BLOCK (a narrowed list
    # refuses an honest row), which is a false block rather than a gate that passes
    # something wrong, so the cost of leaving them unpinned is bounded. Recorded
    # rather than repaired on purpose — a synthetic block per form would be
    # machinery to prove a list nobody has needed to widen.
    r"\b(has|have|had)\s+been\s+closed\b",
    r"\bwe\s+have\s+closed\b",
    r"\bclosed\s+by\s+this\s+table\b",
)


#: The exemption `_is_honest_spelling` grants: a sentence that discloses the
#: still-open SPELLING class AND names no class anchor at all. The anchor arm is
#: what keeps an appended reopening from hiding behind the word `spelling`
#: (review cycle 8, MEASURED: scanning the tail with the NARROW subset instead let
#: 7 end-appended reopenings PASS, e.g. "except `minute`, which is open again" —
#: the shape the block's own comment calls the likeliest edit).
#: ⛔ It is NOT scoped to a trailing region any more: since #11223 the scan covers
#: the whole residual region, so the waiver applies wherever the sentence sits.
_UNIT_TOKEN = re.compile(
    r"\b(minute|minutes|second|seconds|hour|hours|day|days|week|weeks|month|"
    r"months|year|years|mile|miles|percent|pct|millisecond|milliseconds|"
    r"time-unit)\b")


#: The marker that opens the residual list inside the `state_value_carve_out`
#: row — the artifact a lane reads ("read this before improving a number").
#: ⛔ IT IS A LOCATOR, NOT THE SCAN'S BOUND (#11258 review, round 3): the marker
#: sits 12,908 chars into a 50,850-char row, so a scan that STARTED here read
#: 37,942 chars (50,850 − 12,908; MEASURED) — 74.6% of the row — and reported
#: PASS over a reopening written
#: into the row's own narrative above it (MEASURED at `(2b) the ADJACENCY half is
#: open again.` inserted just above the marker: marker bound `[]` → GREEN, row
#: bound 1 claim → RED). That is #11223's own defect one bound further in
#: (`[:1200]` → the `(d)` block → the marker → the row), so `_residual_region`
#: starts at `_RESIDUAL_ROW_START`; the marker keeps two jobs — it names the
#: residual list, and it is one of the two anchors the region must still carry.
_RESIDUAL_MARKER = "⛔ WHAT IS STILL NOT ENFORCED"

#: The row's own entry start — where the scanned region BEGINS. Deliberately a
#: LITERAL, like `_RESIDUAL_ROW_END` below, so the region cannot move with the
#: function the proof test checks.
_RESIDUAL_ROW_START = "\n  - id: state_value_carve_out\n"

#: What marks a claim as being about THIS row's closed gap: the adjacency class
#: (the word itself, a time/length UNIT token or its abbreviation, `time-unit`),
#: the carve-out, or the retention guarantee.
#: ⛔ THE ANCHOR IS NOT REQUIRED OF EVERY PHRASE — that is the difference between
#: this revision and the one the #11258 review refused (`P1`, measured): requiring
#: it of ALL of them let `the gap those tables closed is open again` through,
#: which the PRE-change guard caught. It is required only of the OPENING VERB
#: (`re-?open` / `reopened`), because that verb is the one the row itself uses
#: honestly about OTHER subjects — a false refusal, an owner's decision, a
#: ruling. Every other phrase below can only describe a gap being reopened, so it
#: is matched wherever it appears.
#: ⛔ AND THE ANCHOR IS NOT THE WORD `adjacency` ALONE: review cycle 6 MEASURED
#: that gating the whole scan on it let five reopenings through ("`minute` no
#: longer fuses", "the time-unit half is no longer closed", "except `minute`,
#: which is open again"). The abbreviations are here because the row's own
#: vocabulary uses them (`min`, `ms`, `km`); `\b` keeps `800ms` (one token) from
#: matching.
_RESIDUAL_CLASS_ANCHOR = re.compile(
    r"adjacency|\btime[- ]unit\b|carve[- ]out|\bthe guarantee\b|\bretention\b|"
    r"\bvalue class\b|\bnumeric side\b|"
    r"\b(ms|min|mins|sec|secs|hr|hrs|km)\b|"
    # ⛔ ONE subject phrase, and only one: `the gap that/those … closed` names the
    # gap BY ITS CLOSURE, so it can only be about this one. `the remaining gap` and
    # `the exception it carved` were here for a revision and had to go — anchoring
    # `the remaining gap` reds the honest proof `the remaining gap is the SPELLING
    # case, which is open by design` (MEASURED: GREEN at this head, RED once the
    # phrase is added to this anchor), because it is an ordinary noun phrase a
    # truthful sentence about the SURVIVING residual can use. ⛔ NOT because the
    # row says that phrase: `rg -c 'the remaining gap' config/v4-mechanisms.yml`
    # is 0 (MEASURED), and the row's own wording is "What SURVIVES of the old (d)
    # is the SPELLING case". `the remaining gap is open` is therefore an
    # undisclosed-arm shape and is named in `WHAT THIS CANNOT CATCH` instead of
    # being claimed covered.
    r"\bthe gap (?:that|those)\b[^.;]{0,40}\bclosed\b|"
    + _UNIT_TOKEN.pattern,
    re.I)

#: ⛔ THE WAIVER withholds on ITS OWN SET, which is `_RESIDUAL_CLASS_ANCHOR` PLUS
#: every spelling the unit arm reads and the anchor does not name (#11258 review
#: round 5 `P1`, MEASURED). The unit arm was widened to the row's whole family;
#: the anchor it is withheld by was not, so 9 of the arm's spellings (`units?`,
#: `unit word`, `mos`, `mi`, `millisec(s)`, `kg`, `m`, `s`) never reached it when
#: the sentence also said `spelling`: `` the SPELLING gap is open because `kg` is
#: no longer fused `` was WAIVED (GREEN) while the same shape with `minute` RED —
#: the waiver is applied per sentence, so one word defeated the gate, the same
#: class as round 3's finding one layer in.
#: ⛔ WIDEN THIS SET, NOT `_RESIDUAL_CLASS_ANCHOR`: the anchor also arms
#: `_RESIDUAL_ANCHORED_OPENNESS`, and adding these spellings to it reds EVERY
#: truthful statement — no exception survives (MEASURED at the 33-statement
#: revision; the earlier "29 of 32" was stale on both operands, #11258 review
#: round 11 `P2`) — so the withholding set is the narrow surface. ⛔ THE COUNT IS
#: DELIBERATELY NOT RESTATED: it drifted once already, and the measurement is the
#: claim.
#: ⛔ THE UNIT SPELLINGS ARE ONE DECLARED TUPLE, and the pin DERIVES its sample
#: spellings from it, so an arm added or removed here is measured the moment it
#: is declared. Two of the alternatives an earlier revision carried were DEAD and
#: are gone: `unit\s+word` (`units?` already matches `unit`) and `millisec`
#: (`millisecs?` covers both forms) — each could be deleted at the previous head
#: with the whole file still `102 passed` (#11258 review round 9 `P2`, measured).
_RESIDUAL_UNIT_WAIVER_ARMS = (r"units?", r"kg", r"mi", r"mos", r"millisecs?")

#: ⛔ THE VOCABULARY THE WAIVER MUST WITHHOLD ON, named independently of the arms
#: so that DELETING an arm cannot delete the spelling that would have caught it
#: (#11258 review round 9 `P2`, MEASURED: with the samples derived from
#: `_RESIDUAL_UNIT_WAIVER_ARMS` alone, dropping `kg` also dropped `kg` from the
#: sample list and all `103 passed`). The test asserts BOTH directions: every
#: spelling here is withheld, and every arm declares a spelling that is exercised.
_RESIDUAL_WAIVER_REQUIRED_SPELLINGS = (
    "units", "unit", "kg", "mi", "mos", "millisec", "millisecs")

#: ⛔ THE `(?<!['’])` GUARDS ARE PINNED BY THEIR OWN FALSIFIERS (#11258 review
#: round 9 `P2`, MEASURED): `\b` treats `'` as a boundary, so without them the
#: bare `m`/`s` arms arm on a contraction. Each guard owns a sentence that flips:
#: `I'm confident the pair is no longer fused` is GREEN with the `m` guard and RED
#: without it; `the pair's refusal is no longer fused` is GREEN with the `s` guard
#: and RED without it (in `_RESIDUAL_OPENNESS`); and in the WITHHOLD set,
#: `the spelling list is unchanged; I'm confident it stays so` is WAIVED with the
#: guard and WITHHELD without. All three are asserted in
#: `test_every_waiver_withhold_arm_earns_its_place`.
#: ⚠️ A round-9 finding quoted `I'm confident the ADJACENCY half is no longer
#: fused` as proof the guards were unnecessary: that sentence carries a class
#: ANCHOR, so it reds in BOTH configurations and demonstrated nothing. The two
#: sentences above are the ones that flip, and they are the same two this guard
#: was originally justified with — the guard was right, its stated proof was
#: imprecise.
_RESIDUAL_WAIVER_WITHHOLD = re.compile(
    _RESIDUAL_CLASS_ANCHOR.pattern
    + r"|\b(?:" + "|".join(_RESIDUAL_UNIT_WAIVER_ARMS) + r")\b"
    + r"|(?<!['\u2019])\b[ms]\b",
    re.I)

#: The anchors that mark a claim about the CLOSED gap, and — because the
#: honest-SPELLING waiver reads THIS set — the words that withhold that waiver.
#: ⛔ `carve-out` IS IN IT, and that is a deliberate OVER-BLOCK: prefixing a
#: reopening with `the SPELLING ` used to waive it whole, so
#: `the SPELLING carve-out is open again` / `… stays open` / `… is not closed` all
#: went GREEN while the same sentences without the word `spelling` red — one word
#: defeated the gate (#11258 review round 3 `P1`, measured, and the pre-#11258
#: revision on `main` has the same hole). The cost of closing it is that an honest
#: clause naming BOTH `spelling` and the carve-out is refused; over-block is the
#: safe direction here, and it is stated rather than left to be discovered.
#: ⛔ ONE DEFINITION, ABOVE — this note deliberately does NOT re-declare the regex.
#: It did for one revision, without `carve-out`, and the second binding silently
#: shadowed the first: four mutations (`the carve-out is still open`, `… reopens`,
#: `… fails open`, and the two-clause spelling form) went GREEN because the
#: anchored arm stopped applying to any carve-out clause at all (#11258 review
#: round 3, measured). A note binds nothing; only the literal above does.

#: The other negated CLOSED verdicts: `never … closed`, `<aux> yet to be closed`,
#: `far from closed`, `anything but closed`. ⛔ ANCHORED, deliberately: `not …
#: closed` can only be about this gap, but these read naturally about ANY residual —
#: the row is a list of what is still not enforced, so `(b2) is far from closed` and
#: `residual (a) is never closed by any rule` are HONEST, and putting them in the
#: unanchored arm red eight such sentences (#11258 review round 5 `P1`, measured).
#: The round-4 defect they close is real: `the ADJACENCY gap was never closed` was
#: GREEN while `the ADJACENCY gap is not closed` RED.
_RESIDUAL_CLOSED_VERDICT = (
    r"\bnever\b[^.;]{0,30}\bclosed\b",
    # ⛔ `<aux> yet to be closed` AND ITS SYNONYMS (#11258 review round 9 `P2`,
    # MEASURED): the arm read only `yet`, so `the ADJACENCY half remains to be
    # closed` and `… is still to be closed` — the natural residual-list phrasing,
    # asserting the gap is still open — were GREEN. `remains?` is its own form
    # because it takes no auxiliary.
    # ⛔ AND THE FILLER WINDOW, on EVERY plain form in this tuple (#11258 review
    # round 13 `P1`, MEASURED). Round 12 unified the `no longer` arms and left
    # these requiring strict `\s+` adjacency, so one interpolated word or comma
    # turned a membership-pinned mutation GREEN:
    #   RED    the time-unit half has yet to be closed
    #   GREEN  the time-unit half has yet, in practice, to be closed.  / … fully …
    #   RED    the ADJACENCY half remains to be closed
    #   GREEN  the ADJACENCY half remains, for now, to be closed.
    # ⛔ AND AN EARLIER REVISION OF THIS NOTE WAS FALSE (#11258 round 21,
    # MEASURED): it said "the row's own register is exactly `, in practice, ` /
    # `, after the widen, ` / `, for now, `". The registry writes NONE of them —
    # `in practice`, `after the widen` and `for now` occur 0 times in
    # `config/v4-mechanisms.yml`, exact AND whitespace-flattened. The claim was
    # stated where a lane reasons from, which is the class this file polices
    # (rounds 16-17 corrected two of its members); a lane that looked for the
    # form in the row found nothing.
    # What IS measured: the row does interpolate parenthetically (`measured at
    # this head`, `, paraphrase, `), English supplies these adverbials freely,
    # and each form above is pinned RED in `_RESIDUAL_REOPENING_MUTATIONS`.
    # Filling the slot is the EASY edit because the LANGUAGE offers it — not
    # because the row contains it today. `never … closed` above already carried a
    # 30-char window; that asymmetry was unintended and is what hid these.
    r"\b(?:has|have|had|is|are|was|were)\b[^.;]{0,20}?\b(?:yet|still)\b[^.;]{0,20}?"
    r"to\s+be\s+(?:\w+\s+)?closed\b",
    r"\bremains?\b[^.;]{0,20}?\bto\s+be\s+(?:\w+\s+)?closed\b",
    # ⛔ THE PRESENT-TENSE VERB, not only the participle (#11258 review round 10,
    # MEASURED): `the ADJACENCY half doesn't close the gap` asserts the gap is
    # still open and was GREEN at every head before this one, because the whole
    # `closed` family reads only the PARTICIPLE. It sits in the ANCHORED arm and
    # NOT in `_RESIDUAL_OPENNESS` on purpose: `the tables do not close the
    # SPELLING gap` is HYPOTHETICAL honest prose — the row does NOT contain it
    # (`grep -ciF "tables do not close"` over the registry is 0; its real SPELLING
    # disclosure is "a unit outside `_UNIT_EQUIV` (`800millis`) still fails open on
    # purpose"), so an earlier version of this comment calling it "the row's own
    # honest disclosure" was WRONG (#11258 review round 16 `P2`, MEASURED). It is in
    # `_RESIDUAL_TRUTHFUL_STATEMENTS` now, so the anchoring has a REAL falsifier.
    # ⛔ THREE ALTERNATIVES, because English writes the negation three ways and
    # each round found one of them missing: the contraction on the auxiliary
    # (`doesn't`, `didnt`, `can't`), the spaced `not` (with a window, so
    # `does, in practice, not close` is read), and `cannot`.
    r"(?:\b(?:does|do|did|would|will|can|could|must|should|need|ai)"
    r"(?:n['\u2019]?t|['\u2019]t)"
    r"|\b(?:does|do|did|would|will|can|could|must|should|need)[^.;]{0,20}?\bnot\b"
    r"|\bcannot\b)[^.;]{0,20}?\bclose[sd]?\b",
    r"\b(?:far\s+from|anything\s+but)\b[^.;]{0,20}?\bclosed\b",
)

#: The affirmations that something IS open / STOPPED being closed. TWO ARMS,
#: and the split is the whole design — each arm was measured into place.
#:
#: 1. `_RESIDUAL_OPENNESS` — phrases that can only describe a gap being reopened.
#:    Applied to EVERY sentence in the region, with NO class anchor, because a
#:    reopening that names the gap in words outside the anchor vocabulary is still
#:    a reopening: requiring the anchor here let `the gap those tables closed is
#:    open again` through, which the PRE-change guard caught (#11258 review `P1`).
#: 2. `_RESIDUAL_ANCHORED_OPENNESS` — the polysemous arm (`<verb> … open`, and the
#:    opening verb `re-?open`). "is open" can be said of anything, and the row
#:    says it about a false refusal and about an owner's decision, so this arm
#:    requires `_RESIDUAL_CLASS_ANCHOR` somewhere in the sentence. Without that
#:    requirement the arm's filler window read the row's own honest
#:    "the pair is unjudgeable and fails open" as an openness claim (MEASURED).
#: ⛔ AND THE PLAIN `not … judged` / `not … fused` SHAPE IS NOT READ (#11258
#: review round 13 `P2`, MEASURED): only the `no longer …` form is —
#: `the ADJACENCY half is no longer judged.` REDS, but `the ADJACENCY half is not
#: judged.`, `… is not fused.` and `the units are still not fused.` are GREEN. That
#: is a DISCLOSURE, not a missing arm, and the reason is measured: an anchored
#: `not … (fused|judged)` arm false-blocks the row's OWN honest prose —
#: `(d) … is not judged: no unit is ADJACENT`, `(h) … is not judged by the PURE
#: function`, `(i) A k SUFFIX IS NOT JUDGED` and `miles was not fused there` all
#: sit in a clause segment that carries a class anchor. So the arm cannot separate
#: the honest denial from the reopening, and naming the shape here is the honest
#: alternative to claiming coverage.
#: ⛔ AND EVERY FILLER WINDOW IS BOUNDED, so a filler LONGER than the bound defeats
#: that arm (#11258 review round 15 `P1`, MEASURED): multi-token interpolations
#: are ordinary English (`, in practice, ` / `, at this head, ` — ⛔ NOT "the
#: row's own register": the row writes NEITHER comma form, 0 occurrences each, and
#: its one `at this head` is bare, inside `measured at this head`; #11258 round 21,
#: MEASURED), and two of them stacked exceed `[^.;]{0,25}?` / `{0,30}` /
#: `{0,20}`. GREEN at this revision:
#: `the ADJACENCY half is no longer, in practice and at this head, judged.`,
#: `the units are no longer, in practice and at this head, fused.`,
#: `the ADJACENCY half is not, in practice and at this head, closed.`,
#: `the ADJACENCY half has, in practice and at this head, yet to be closed.`,
#: `the ADJACENCY half remains, in practice and at this head, to be closed.`,
#: `the ADJACENCY half is never, in practice and at this head, closed.`
#: ⛔ THE BOUND IS NOT RAISED TO CHASE THESE, deliberately: the windows are bounded
#: BECAUSE an unbounded filler false-blocks honest prose (a `not … closed` reaching
#: across a clause, `is unjudged and never closed`), and each widening measured in
#: this file traded one leak for a false block. A scan with a length limit is the
#: artifact; the limit is stated here so a lane knows what it is buying when it
#: writes a long interpolation. The multi-token forms would need a parser, not a
#: regex — which is the honest boundary of this pin.
#: ⛔ WHAT THIS CANNOT CATCH, stated rather than implied: it is a pattern list, so
#: a reopening phrased with none of these words passes — MEASURED, still GREEN at
#: this revision: "the ADJACENCY half went back to being unjudged", "`minute` is
#: once more outside the closed set", "adjacency is unjudged again", and the
#: coordinator form of the row's own vocabulary, "the ADJACENCY half is unjudged
#: and fails open" (the clause split leaves the anchor in one clause and the
#: phrase in the next — see `_RESIDUAL_FAIL_OPEN`). ⛔ AND A JOINER THE SPLITTER
#: DOES NOT KNOW (`_RESIDUAL_CLAUSE_SPLIT` is a CLOSED list: `and`/`but`/`while`/
#: `whereas`/`although`/`though`/`yet`) puts two claims in one segment, and the
#: honest `spelling` waiver then covers both — the measured fix for `while` and
#: `although` is in that list, but a joiner it does not name still does this. ⛔ AND
#: THE GAP NAMED WITHOUT AN ANCHOR: `the remaining gap is open` is GREEN, because
#: `the remaining gap` names the SURVIVING OPEN residual and therefore cannot be a
#: class anchor — anchoring it would refuse `the remaining gap is the SPELLING
#: case, which is open by design`, which is true (#11258 review round 5 `P2`,
#: measured). The closed subject IS read when it names its own closure
#: (`the gap those tables closed is open`). ⛔ EVERY
#: ENTRY HERE IS A CLAIM LIKE ANY OTHER and must be re-measured when the arms
#: change: "`minute` is back in the open set" sat on this list for one revision
#: and is RED again (it is mutation #28), because the anchored arm reads an object
#: complement. A stale entry here is worse than no entry — a lane reads it as
#: uncovered and deletes the mutation as unfalsifiable, shrinking coverage
#: silently. The pin's job is
#: to catch the shapes a lane actually writes, not to be a semantic reader of
#: English; closing that needs a human or a different instrument, not a longer
#: regex.
#: ⛔ ONE WINDOW for every `no longer …` arm (#11258 review round 12, MEASURED):
#: three separate arms each required the verb to follow `no longer` IMMEDIATELY, so
#: `no longer BEING fused`, `no longer EVEN judged`, `no longer ACTIVELY fused` were
#: GREEN in whichever arm was supposed to read them, and the floor — the ONLY tuple
#: the honest-SPELLING path runs — had the same hole. `\bno\s+longer\b` (word
#: boundaries, not `\s+`) also stops a PUNCTUATED insertion from defeating all
#: three: `no longer, strictly speaking, closed` was GREEN.
#: ⛔ `fused`/`fusing` are NOT in the unanchored arm below; they stay in the
#: subject-gated ones. The falsifier is `` `percent`/`pct` is no longer fused ``
#: (a must-stay-GREEN proof), and `percent`/`pct` are absent from those gates'
#: vocabularies, which is what makes a window there free.
_NO_LONGER_WINDOW = r"\bno\s+longer\b[^.;]{0,25}?"

_RESIDUAL_OPENNESS = (
    # `not closed`, allowing an adverb inside the negation (`is not yet / not
    # fully / not really closed`) — the adverb-free form was the whole class
    # one word away (review cycle 6, MEASURED: all three PASSED before this).
    # Scanned PER SENTENCE rather than per clause: `the ADJACENCY half is not, and
    # never was, closed` splits into clauses that each carry only half of it
    # (#11258 review `P2`, measured).
    r"\bnot\b[^.;]{0,30}\bclosed\b",
    # ⛔ THE CONTRACTION ARM IS A PATTERN, NOT AN AUXILIARY LIST (#11258 review
    # rounds 8 and 9, each MEASURED). Round 8 replaced `isn'?t closed` with an
    # enumeration (`is|are|was|were|has|have|had|does|do|did|would|should|could|
    # must|need|ca|wo`) and round 9 found three holes in the enumeration itself:
    # `cannot` has NO word boundary after `can`, and `shan't` was never listed, so
    # `cannot be closed` / `shan't be closed` stayed GREEN while `can not be
    # closed` / `can't be closed` RED. An enumeration will keep acquiring holes
    # because the language has one, so the arm is now the SHAPE: any word whose
    # contraction ends the negation, plus `cannot`.
    # ⛔ THE APOSTROPHE IS OPTIONAL ONLY ON A KNOWN STEM (#11258 review round 10
    # `P1`, MEASURED). Round 9 made it MANDATORY to avoid `point`/`count`/
    # `account`, and that NARROWED the arm: `isnt closed`, `wasnt closed`,
    # `hasnt been closed`, `hadnt been closed` were RED at the previous head and
    # GREEN at round 9's — an undisclosed fail-open REGRESSION introduced by the
    # fix, in the same direction the last two rounds were closing. The collision
    # only exists on the UNBOUNDED stem (`\w+n['’]?t` matches `point`), so the
    # arm has two contraction branches:
    #   (a) apostrophe MANDATORY, any stem — every contraction spelling;
    #   (b) apostrophe OPTIONAL, KNOWN auxiliary stems only — the sloppy
    #       apostrophe-less spellings a lane actually types;
    #   (c) `cannot`, the one contraction written without either.
    r"\b(?:\w+n['\u2019]t|"
    r"(?:isn|aren|wasn|weren|hasn|haven|hadn|doesn|don|didn|wouldn|shouldn|"
    r"couldn|mustn|needn|can|shan|won|ain)['\u2019]?t|cannot)\b[^.;]{0,30}\bclosed\b",
    # ⛔ `never … closed`, `<aux> yet to be closed`, `far from closed` and
    # `anything but closed` assert the gap is open for the same reason `not …
    # closed` does — but they are in the ANCHORED arm and NOT here, because unlike
    # `not closed` they are ordinary prose about ANY residual: this row's list is
    # literally "WHAT IS STILL NOT ENFORCED", so `(b2) is far from closed` and
    # `residual (a) is never closed by any rule` are honest and both RED when these
    # forms are unanchored (#11258 review round 5 `P1`, measured). See
    # `_RESIDUAL_CLOSED_VERDICT`.
    # ⛔ AND THE FILLER WINDOW, which the verb list alone did not have (#11258
    # review round 11 `P1`, MEASURED): `no longer judged` RED but `no longer BEING
    # judged`, `no longer EVEN judged`, `no longer ACTIVELY judged` and `no longer
    # BEING closed` were all GREEN — the arm required the verb to follow `no
    # longer` immediately, while its sibling `not` arm deliberately carries a
    # window for exactly this reason. It is phrased with `no longer` + `closed`/
    # `judged`, i.e. the vocabulary this arm CLAIMS to read, so it is not the
    # disclosed residual.
    _NO_LONGER_WINDOW
    + r"(closed|fuses|judged|caught|compared|enforced|retained)\b",
    # ⛔ THE PARTICIPLE, SUBJECT-GATED. The `no longer` arm above carries only the
    # 3sg `fuses`, so "the ADJACENCY half is no longer fused" — the row's OWN
    # register (`the unit word IS fused`, `THE FUSION ITSELF MULTIPLIES`) — stayed
    # GREEN while "… is no longer judged" RED: the arm was reached and only the
    # inflection was missing (#11258 review round 3, MEASURED). It cannot simply
    # join the arm: this arm is UNANCHORED, so an ungated participle reds an honest
    # `no longer fused` about ANY subject — `percent`/`pct` is no longer fused: …`
    # RED (measured, and it is a must-stay-GREEN proof statement below, so the gate
    # has a falsifier).
    # ⛔ WHAT IS TRUE ABOUT THE ROW, stated because an earlier revision of this
    # comment claimed otherwise (#11258 review round 4 `P2`, MEASURED): the row's
    # real prose is "`miles` was not fused there" and "fusing `percent` REFUSED
    # the truthful pair" — `grep -c "no longer fus"` over the whole registry is
    # **0**, and `git log -S"no longer fused" -- config/v4-mechanisms.yml` is
    # empty. So the honest shapes below are HYPOTHETICAL (what a future lane might
    # write about the same graph), not the row's own sentences, and an ungated
    # participle leaves the real row GREEN. That is why the gate may be — and is —
    # widened to the past tense: it costs no real disclosure.
    # ⛔ TWO GATES because one cannot be both wide enough for the row's own
    # closed-class vocabulary and narrow enough for a generic subject:
    #   (a) the non-unit class words of the CLOSED gap — no subject gate can be
    #       narrower than the vocabulary the row itself names;
    #   (b) the unit vocabulary the row enumerates plus its conventional siblings,
    #       with an explicit copula so a bare unit token elsewhere cannot arm it.
    # ⛔ TENSE IS NOT A DISCRIMINATOR (#11258 review round 4 `P2`, MEASURED): the
    # first revision of (b) admitted only a PRESENT copula, which left the
    # dangerous direction open — `the units were no longer fused in this revision`
    # and `\`minute\` was no longer fused after the widen` were GREEN (a reopening
    # in the past tense) — while (a) refused honest past-tense prose one vocabulary
    # class over. Since the row writes no `no longer fused` at all, the honest
    # past-tense shapes are hypothetical, so BOTH arms admit `was|were` and the
    # leak closes on both sides.
    # ⛔ ONE WINDOW FOR BOTH ARMS: a 20-char bound on (b) made the same sentence
    # RED on the class-word side and GREEN on the unit side once it carried an
    # interpolated `after the widen landed` (MEASURED: `\`minute\` is, after the
    # widen landed, no longer fused` → GREEN at 20, RED at 80). ⛔ NOT "the row's
    # own register": `after the widen` occurs 0 times in the registry, and no
    # commit ever added it (`git log -S` is empty; #11258 round 21, MEASURED).
    # ⛔ IT READS THE WHOLE ANCHOR VOCABULARY, not a hand-copied six (#11258 review
    # round 16 `P1`, MEASURED): the literal here was narrower than
    # `_RESIDUAL_CLASS_ANCHOR` by exactly `the guarantee`, `retention` and
    # `the gap that/those … closed`, so the SAME predicate was RED for six of the
    # nine members and GREEN for the other three:
    #   GREEN  the gap those tables closed is no longer fused.
    #   GREEN  the retention guarantee is no longer fused.
    #   RED    the ADJACENCY half is no longer fused.
    # The asymmetry was specific to `fused|fusing` — those three subjects RED under
    # every other arm — and the row uses `retention` / `the guarantee` ABOUT THIS
    # CARVE-OUT, while `_RESIDUAL_CLASS_ANCHOR`'s own note says `the gap that/those …
    # closed` "can only be about this one". Widening false-blocks 0 of the truthful
    # statements and does not red the real row (measured) — the same argument this
    # comment already uses for admitting the past tense.
    # ⛔ AND `the gap that/those … closed`, the anchor member that names the gap BY
    # ITS CLOSURE — listed inside the group, not delegated to
    # `_RESIDUAL_CLASS_ANCHOR.pattern`, because that pattern also carries the UNIT
    # TOKENS and would false-block the must-stay-GREEN
    # `` `percent`/`pct` is no longer fused `` proof (measured). ⚠️ It must sit
    # INSIDE `(?:…)`: a top-level `|` here makes the anchor word alone a complete
    # match and reds every truthful statement (measured, twice).
    r"\b(?:adjacency|the\s+half|carve-?out|value\s+class|numeric\s+side|"
    r"time[- ]unit|retention|the\s+guarantee"
    r"|the\s+gap\s+(?:that|those)\b[^.;]{0,40}\bclosed\b)"
    r"[^.;]{0,80}?"
    + _NO_LONGER_WINDOW + r"(?:fused|fusing)\b",

    r"\b(?:units?|unit\s+word|second|seconds|minute|minutes|hour|hours|day|days|"
    r"week|weeks|month|months|mos|year|years|mile|miles|mi|millisecond|"
    r"milliseconds|millisec|millisecs|min|mins|ms|sec|secs|hr|hrs|km|kg|"
    r"(?<![\'\u2019])\bm\b|(?<![\'\u2019])\bs\b)"
    r"\b[^.;]{0,80}?\b(?:is|are|remains?|stays?|was|were)\b[^.;]{0,80}?"
    + _NO_LONGER_WINDOW + r"(?:fused|fusing)\b",
    r"\bunclosed\b",
    # ⛔ NEGATION IS NOT A REOPENING, and this arm is the UNANCHORED one — so it is
    # where a plain `not` would otherwise reach an `open` phrase: `the ADJACENCY
    # gap is not open again` DENIES the gap and it red before this pair was added
    # (#11258 review `P2`, measured). `not closed` is deliberately NOT guarded —
    # there the negation ASSERTS the gap is open, which is the whole arm.
    # ⛔ THE NEGATION WINDOW IS THE RUN OF FIXED-WIDTH LOOKBEHINDS BELOW, and the
    # RUN is the count — say `ten forms` and it is stale the next time one is added
    # (this comment said `SIX FORMS` while carrying ten, in the same commit whose
    # finding was "stale count comments", #11258 review round 4 `P2`, measured).
    # They match the opening verb's window, and for the same
    # measured reason: two forms read only `not open again` / `never open again`
    # and left `has not been open again`, `has never been open again` and
    # `hasn't been open again` as FALSE BLOCKS (#11258 review round 2 `P2`).
    r"(?<!\bnot\s)(?<!\bnever\s)(?<!\bnot\sbeen\s)(?<!\bnever\sbeen\s)"
    r"(?<!\bnot\syet\s)(?<!\bnever\sbefore\s)"
    r"(?<!\bnot\syet\sbeen\s)(?<!\bnever\sbefore\sbeen\s)"
    # ⛔ THE APOSTROPHE CLASS CARRIES U+2019 (#11258 review round 22 `P2`,
    # MEASURED): the contraction ARM already accepts the typographic apostrophe
    # (`n['\u2019]?t`), and so does every other spelling arm — but the negation
    # RUNS spelled only `'`, so `the ADJACENCY gap hasn’t been open again.` (the
    # smart-quote form macOS supplies for free) was REFUSED while its ASCII twin
    # stayed GREEN, and NO ledger entry re-measured the difference. That
    # breaks this artifact's own rule — an over-block is acceptable only if
    # RECORDED — and it is the false block that ends with the pin being deleted.
    # ⛔ MEASURED at `6c3c18b8280f`: SEVEN truthful denials were GREEN/ASCII and
    # RED/curly. Both characters therefore belong in the class at EVERY run site,
    # and the invariant that keeps them there is
    # `test_every_apostrophe_shape_gives_the_same_verdict_in_EITHER_spelling`,
    # which re-measures every proof-list shape in both spellings — so a site that
    # loses `’` reds for the shape that exercises it. Fixed width is unchanged:
    # `[n'’]t` is still three chars, so every lookbehind stays legal.
    r"(?<![n'\u2019]t\s)(?<![n'\u2019]t\sbeen\s)"
    # ⛔ THE ARM AND THIS RUN ARE ONE UNIT (#11258 review round 17 `P1`, MEASURED):
    # widening the ARM to `open(?:s|ed|ing)?` without widening the RUN turned five
    # honest closures into FALSE BLOCKS — `no longer opens again`, `is no longer
    # opened again`, `is no longer opening again`, `hasn't yet opened again`,
    # `hasn't ever opened again` were GREEN while the arm read only `open again`
    # (no match at all) and RED the moment it read the inflection. An inflection
    # added to the arm needs its own negation form HERE, and every form in this run
    # has a must-stay-GREEN partner in `_RESIDUAL_TRUTHFUL_STATEMENTS`.
    # ⛔ AND EACH SITE HAS ITS OWN PARTNERS, not a shared assumption (#11258 review
    # round 18 `P0`, MEASURED): the run is carried in BOTH sites because the two
    # drift independently, and only the `_RESIDUAL_OPENNESS` copy was falsifiable —
    # deleting this site's copy left the whole file GREEN while five honest waived
    # denials went RED. The `the ADJACENCY half …` partners pin occurrence #1; the
    # `the SPELLING gap …` partners pin THIS one.
    r"(?<!no longer\s)(?<![n'\u2019]t\syet\s)(?<![n'\u2019]t\sever\s)"
    r"\bopen(?:s|ed|ing)?\s+again\b",
)

#: The ANCHORED arm: `<verb> … open` and the opening verb. ⛔ NO HYPHEN EXCEPTION —
#: `fail-open` IS read, because failing open on the adjacency class is exactly the
#: reopening this scan refuses (`the ADJACENCY half is fail-open.` stays GREEN with
#: one, #11258 review `P2`).
_RESIDUAL_OPEN_VERBS = (
    r"is|are|was|were|remain|remains|remained|stay|stays|stayed|"
    r"keep|keeps|kept|hold|holds|held")

#: `<verb> open` with the verb NEGATED is a DENIAL of the gap, not a reopening:
#: `the ADJACENCY half is not open` closes it. The veto sits directly after the
#: verb so it can only ever speak about THIS predicate — `is unjudged and never
#: closed` is still read by the `not … closed` arm, where the negation asserts the
#: gap is open (#11258 review `P2`, measured: seven honest denials red before it).
#: ⛔ IT VETOES ONLY THE PREDICATE'S OWN NEGATOR, and it admits an adverb on BOTH
#: sides of it. Too tight, and honest denials are refused — `is not entirely open`,
#: `is clearly not open` were FALSE BLOCKS (#11258 review round 3 `P2`, measured).
#: Too wide (a bare `(?!…not…)`), and the EMPHATIC negations, which ASSERT the gap
#: is open, go green — `is not only open`, `is not merely open`, `is not just open,
#: it is unjudged`, `is never far from open` were a coverage REGRESSION against the
#: head the previous review approved (#11258 review round 2 `P1`, measured). The
#: adverb that makes the difference is the one AFTER `not`, so that slot is an
#: explicit allowlist — `only`/`merely`/`just`/`simply`/`far` flip the polarity and
#: are deliberately absent, while the ones here do not.
_RESIDUAL_NEGATED_OPEN = (
    r"(?!\s*(?:\w+\s+)?(?:not|never)\s+"
    r"(?:(?:yet|fully|really|still|entirely|always|genuinely|currently|quite|"
    r"completely|actually|wholly|truly|strictly)\s+)?"
    # ⛔ AND THE PARTICIPLE, the OTHER half of the round-17 `P2` fix — in ALL THREE
    # post-negator slots (`not`/`never`, `no longer`, `n't`), because a class fixed
    # in one of three places is the defect rounds 11-12 were spent on. It sits
    # AFTER the adverb slot, so every polarity flipper round 2's regression let
    # through (`not only kept open`, `not merely held open`) still cannot reach
    # `open` and stays RED — pinned as mutations, not asserted.
    r"(?:(?:kept|held)\s+)?(?:fail-)?open\b)"
    # ⛔ `no longer` IS ITS OWN LOOKAHEAD, not a member of the `not|never` group
    # (#11258 review round 13 `P2`, MEASURED): it hard-coded `no\s+longer\s+`, so
    # `the ADJACENCY half is no longer open.` was GREEN (truthful, pinned) while
    # `… is no longer, in practice, open.` and `… is no longer being open.` RED —
    # an UNRECORDED over-block, and the fourth `no longer` site not using the
    # shared primitive (three did). It takes a window and the SAME after-negator
    # adverb allowlist, so `no longer merely open` (which ASSERTS openness) stays
    # RED.
    r"(?!\s*(?:\w+\s+)?\bno\s+longer\b"
    # ⛔ THE WINDOW MUST NOT SWALLOW A POLARITY FLIPPER (#11258 review round 13,
    # MEASURED — my own first attempt did): `no longer merely open` / `no longer
    # only open` / `no longer far from open` ASSERT the gap is wide open, and with
    # a bare `[^.;]{0,25}?` the filler consumed `merely ` and the veto fired,
    # turning all three GREEN — round 2's coverage regression, re-created by the
    # fix for the over-block. So the filler is barred from containing one.
    r"(?![^.;]{0,25}?\b(?:merely|only|just|simply|far\s+from)\b)"
    r"[^.;]{0,25}?"
    r"(?:(?:yet|fully|really|still|entirely|always|genuinely|currently|quite|"
    r"completely|actually|wholly|truly|strictly)\s+)?"
    # ⛔ AND THE PARTICIPLE, the OTHER half of the round-17 `P2` fix — in ALL THREE
    # post-negator slots (`not`/`never`, `no longer`, `n't`), because a class fixed
    # in one of three places is the defect rounds 11-12 were spent on. It sits
    # AFTER the adverb slot, so every polarity flipper round 2's regression let
    # through (`not only kept open`, `not merely held open`) still cannot reach
    # `open` and stays RED — pinned as mutations, not asserted.
    r"(?:(?:kept|held)\s+)?(?:fail-)?open\b)"
    # ⛔ `’` IS IN THE CLASS HERE FOR PARITY, and this site is NOT a falsifier
    # (#11258 review round 22, MEASURED): a contraction attaches to its own
    # auxiliary (`isn’t` = `isn` + `’t`), and `\b(is|are|…)\b` cannot match the
    # `is` of `isn’t` for EITHER apostrophe — so this lookahead's `n't` branch
    # cannot fire on well-formed English. The guard that actually keeps a
    # contracted denial out of this arm is the `(?<![n'’]t )` LOOKBEHIND (it vetoes
    # the participle verbs `kept`/`held` when the contraction precedes them), which
    # is why `the ADJACENCY gap isn’t kept open.` is GREEN. The class is widened
    # anyway so that a lane reading any one run site cannot conclude the
    # typographic apostrophe is handled "somewhere else".
    r"(?!\s*(?:\w+\s+)?n['\u2019]t\s+"
    r"(?:(?:yet|fully|really|still|entirely|always|genuinely|currently|quite|"
    r"completely|actually|wholly|truly|strictly)\s+)?"
    # ⛔ AND THE PARTICIPLE, the OTHER half of the round-17 `P2` fix — in ALL THREE
    # post-negator slots (`not`/`never`, `no longer`, `n't`), because a class fixed
    # in one of three places is the defect rounds 11-12 were spent on. It sits
    # AFTER the adverb slot, so every polarity flipper round 2's regression let
    # through (`not only kept open`, `not merely held open`) still cannot reach
    # `open` and stays RED — pinned as mutations, not asserted.
    r"(?:(?:kept|held)\s+)?(?:fail-)?open\b)")

#: `fails open` (no hyphen) is the row's OWN vocabulary for the same thing, and it
#: is read CLAUSE-anchored rather than by adding `fails?` to the verb list above:
#: MEASURED, that addition reds the real row on its own honest `(d)` — *“`_value_
#: bindings` fuses nothing and the pair fails open”* — whose clause *“the pair
#: fails open”* names no class, while *“the ADJACENCY half fails open”* does. So
#: the anchor requirement is what separates the lane's reopening from the row's
#: disclosure. ⚠️ THAT SENTENCE IS ONLY HALF TRUE, MEASURED: when the anchor IS the
#: subject of an honest disclosure — `the numeric side fails open on an unknown
#: unit`, `the numeric side still fails open on purpose` — the arm REFUSES it, and
#: the row's own sentence escapes only because its subject (`a unit outside
#: _UNIT_EQUIV`) carries no anchor (#11258 review round 6 `P2`). That is an
#: OVER-BLOCK, kept because the alternative is the leak it closes, and
#: RECORDED IN THE LEDGER rather than left to be discovered — its two sentences
#: are entries in `_RESIDUAL_REFUSED_TRUTHFUL`, so the suite re-measures them;
#: until #11258 review round 19 `P3` they were recorded only in THIS comment,
#: which is a record that cannot red. ⛔ STILL LEAKING, stated rather than implied: the coordinator form
#: *“the ADJACENCY half is unjudged and fails open”* stays GREEN, because the
#: clause split leaves the anchor in the first clause and the phrase in the
#: second — the same shape as the row's honest sentence, so no clause-scoped rule
#: can separate them (#11258 review `P2`).
#: ⛔ THE DENIAL OF THIS PREDICATE IS NOT A REOPENING, and until #11258 review
#: round 25 `P2` this arm had NO negation veto at all while every other
#: `open`-predicate arm in this file carried one — so the plain, honest negation of
#: the very phrase it reads was REFUSED: `the ADJACENCY half no longer fails open.`,
#: `… never fails open.`, `… does not fail open.` and `the numeric side no longer
#: fails open.` each returned a claim, appeared in NO ledger entry, and got the
#: message that says the region "REOPENS a gap this row says is CLOSED" plus a
#: pointer at a ledger with no matching entry — the same double defect rounds
#: 19/20 were spent removing. MEASURED both directions with this module's own
#: functions at `9e6839bc4636` (waived=False, one claim each).
#:
#: ⛔ THE VETO IS A FIXED-WIDTH RUN, one form per must-stay-GREEN partner in
#: `_RESIDUAL_TRUTHFUL_STATEMENTS` (this file's own rule, round 17 `P1`: a form in
#: the run with no partner is a form nothing re-measures). `’` is in the apostrophe
#: class for parity with every other run site, and its falsifier is the ASCII-valued
#: `… doesn't fail open.` partner, which
#: `test_every_apostrophe_shape_gives_the_same_verdict_in_EITHER_spelling`
#: re-measures in both spellings.
#:
#: ⛔ AND EACH FORM IS A FIXED-WIDTH LOOKBEHIND SITTING IMMEDIATELY IN FRONT OF THE
#: VERB, which is what keeps the EMPHATIC negations RED: `not only fails open`
#: ASSERTS the predicate, and the four chars in front of the verb there are `nly `,
#: not `not `, so it is still read. That
#: narrowness is pinned rather than asserted — `the ADJACENCY half not only fails
#: open, it leaks.` is in `_RESIDUAL_REOPENING_MUTATIONS` and goes GREEN the moment
#: the veto is widened into a WINDOW scan (the trade round 2 `P1` and round 13 had
#: to correct one arm over). ⛔ DO NOT ADD AN EMPHATIC BRANCH: one was written in
#: this round's first cut, was measured INERT (dropping it left `108 passed`,
#: because no grammatical emphatic form puts `not` immediately before the verb),
#: and dead arms are the class rounds 18/23 `P0` filed.
#:
#: ⚠️ THE BOUNDED-LOOKBEHIND COST IS PAID OPENLY: an adverb between the negator and
#: the verb (`does not actually fail open`) sits outside every form the run can
#: carry, so it is refused. That shape is in `_RESIDUAL_REFUSED_TRUTHFUL`, which is
#: the route this artifact's own refusal message names.
_RESIDUAL_FAIL_OPEN = re.compile(
    r"(?<!\bnot\s)(?<!\bnever\s)(?<![n'\u2019]t\s)(?<!no longer\s)"
    r"\bfails?\s+open\b",
    re.I,
)

#: The gap between the verb and `open` may hold an OBJECT ("keeps the gap open"),
#: a participle ("is held open"), adverbs or a parenthetical ("remains, in
#: practice, open"), and it may cross a coordinator that JOINS ADJECTIVES inside
#: one predicate ("is unjudged and open") — but NOT one that introduces a NEW
#: PREDICATE, which is how the row's own honest "the pair is unjudgeable and fails
#: open" was read as an openness claim. Three attempts are recorded here because
#: each one traded a leak for a different leak, measured: a CLOSED ADVERB LIST
#: dropped every object (`keeps the gap open`, `is held open` GREEN — round 2
#: `P1`); a BLANKET coordinator bound dropped coordinator-joined adjectives
#: (`is unjudged and open` GREEN — round 3, measured); this bound blocks the
#: coordinator only when a subject and a verb follow it.
_RESIDUAL_ANCHORED_OPENNESS = (
    # ⛔ THE VERB MAY ITSELF BE NEGATED FROM THE LEFT (#11258 review round 14 `P2`,
    # MEASURED): `the ADJACENCY half is no longer kept open.` / `… no longer held
    # open.` are the negated twins of the pinned reopenings `… is kept open.` /
    # `… is held open.`, and the veto below sits AFTER the verb, so it could not
    # see a negator in front of it. Both were RED — an honest closure refused.
    # The lookbehind is a FIXED-WIDTH literal (`no longer ` is exactly 10 chars),
    # which a Python lookbehind requires — `\s+`/`\b` make the pattern
    # variable-width and `re` refuses it outright (measured).
    # ⛔ ROUND 17 `P2`: that lookbehind covered `no longer` ONLY, and the pair of
    # costs the ledger used to carry (`is not kept open.` / `is not held open.`)
    # was RECORDED as unfixable — which was WRONG and is corrected here by
    # measurement. The two halves are each INERT ALONE and ADDITIVE TOGETHER: the
    # participle in the veto slot alone changes nothing (the match arrives through
    # the PARTICIPLE as the verb, so the negator is on the wrong side of that
    # slot), and `not`/`never` in front of the verb alone is not enough either —
    # but the two together make both denials GREEN while `not only kept open` /
    # `not merely held open` (which ASSERT openness) stay RED. Measured: 0
    # mutations lose RED, 0 truthful statements go RED.
    r"(?<!no longer )"
    # ⛔ `’` IS IN THE CLASS HERE TOO — see the apostrophe note on the
    # `_RESIDUAL_OPENNESS` negation run.
    r"(?<!\bnot )(?<!\bnever )(?<![n'\u2019]t )\b(" + _RESIDUAL_OPEN_VERBS + r")\b"
    + _RESIDUAL_NEGATED_OPEN
    + r"(?:(?!\b(?:and|but)\s+(?:\w+\s+){0,4}?"
    r"(?:is|are|was|were|fails?|stays?|remains?|keeps?|holds?)\b)[^.;]){0,40}?"
    r"(?:fail-)?open(?!\w)",
    # ⛔ A NEGATED reopening is a DENIAL of one: `(d2) does not reopen (d)'s
    # ADJACENCY result` is the row's own truthful scope boundary (#11223 fix
    # direction 2), so the lookbehinds are what keep it out of the scan. They are
    # A FAMILY of fixed-width forms rather than the original pair, because a
    # one-token window read only `not reopen` / `never reopen` and refused every
    # honest denial that puts a word between them (`has not been re-opened`,
    # `has never been reopened`, `doesn't reopen` all red — #11258 review `P2`,
    # measured). ⚠️ The enumeration is
    # BOUNDED, not exact: an ADVERB inside the forms (`has not YET been re-opened`,
    # `has never BEFORE been reopened`) is still refused, so the `yet` and `before`
    # variants are carried below. A Python lookbehind must be fixed-width, so a
    # different adverb means another form — do NOT read the run as exhaustive
    # (#11258 review round 3 `P2`, measured on the word "exact").
    # ⛔ AND THE COST OF THAT BOUNDEDNESS IS EXECUTABLE, not a sentence here
    # (#11258 round-20 `P3`, MEASURED): three honest denials with an interpolated
    # adverb or comma — `has not to date been reopened.`, `has not, in practice,
    # been reopened.`, `has not yet, in practice, been re-opened.` — are REFUSED,
    # because a fixed-width lookbehind cannot cross the interpolation. Widening
    # the run is not the fix (the trades are rounds 2/9/10), so all three live in
    # `_RESIDUAL_REFUSED_TRUTHFUL` where the suite re-measures them; this note
    # pointing at prose was the defect the round-19 `fails open` fix had already
    # closed one arm over.
    r"(?<!\bnot\s)(?<!\bnever\s)(?<!\bnot\sbeen\s)(?<!\bnever\sbeen\s)"
    r"(?<!\bnot\syet\s)(?<!\bnever\sbefore\s)"
    r"(?<!\bnot\syet\sbeen\s)(?<!\bnever\sbefore\sbeen\s)"
    # ⛔ `’` IS IN THE CLASS HERE TOO — see the apostrophe note on the
    # `_RESIDUAL_OPENNESS` negation run.
    r"(?<![n'\u2019]t\s)(?<![n'\u2019]t\sbeen\s)\bre-?open",
    *_RESIDUAL_CLOSED_VERDICT,
)

#: The negation RUN shared by `_SPELLING_FLOOR`'s two verb arms.
#:
#: ⛔ ONE PRIMITIVE, NOT TWO COPIES — the drift this file already paid for once, one
#: tuple over (#11258 review rounds 11-12: the run was written out three times, only
#: one copy carried a filler window, and every copy used `\s+`, so a punctuated
#: insertion defeated all of them). Both arms below need the SAME run, so it lives
#: in one place and an arm that widens cannot leave the run behind.
_SPELLING_FLOOR_NEGATION_RUN = (
    r"(?<!\bnot\s)(?<!\bnever\s)(?<!\bnot\sbeen\s)(?<!\bnever\sbeen\s)"
    r"(?<!\bnot\syet\s)(?<!\bnever\sbefore\s)"
    r"(?<!\bnot\syet\sbeen\s)(?<!\bnever\sbefore\sbeen\s)"
    # ⛔ `\u2019` IS IN THE CLASS HERE, and the falsifier for it is the TRUTHFUL
    # PARTNER `the SPELLING gap hasn’t been open again.` — NOT
    # `test_every_apostrophe_shape_gives_the_same_verdict_in_EITHER_spelling`.
    # MEASURED (#11258 review round 24 `P2`): that test iterates a shape only when
    # it contains an ASCII `'` to convert, so it never reaches this site; an earlier
    # note here claimed it did. Reverting `’` here reds ONLY the truthful-list test.
    r"(?<![n'\u2019]t\s)(?<![n'\u2019]t\sbeen\s)"
    r"(?<!no longer\s)(?<![n'\u2019]t\syet\s)(?<![n'\u2019]t\sever\s)"
)

#: The floor for an EXEMPT sentence — one that discloses the still-open SPELLING
#: class and names no class anchor. It is not skipped: these say a unit stopped
#: being fused/judged, which reopens the adjacency gap whatever else the sentence
#: is about (review cycle 7, MEASURED: exempting that sentence wholesale let "the
#: SPELLING gap is not closed and `minute` no longer fuses" PASS; cycle 9:
#: skipping it entirely let "… the value class is no longer judged" PASS).
#: `not closed` / `is open` are deliberately absent — the SPELLING gap IS open and
#: saying so is the row's own honest disclosure.
_SPELLING_FLOOR = (
    _NO_LONGER_WINDOW
    + r"(closed|fuses|fused|fusing|judged|caught|compared|enforced|retained)\b",
    r"\bunclosed\b",
    # ⛔ THE WAIVED SENTENCE IS STILL READ FOR `open again`, which is why it is here
    # and not only in `_RESIDUAL_OPENNESS`: the honest path runs ONLY this tuple, so
    # `the SPELLING gap is open again` was GREEN while the same phrase without the
    # word `spelling` red — the waiver was skipping the whole openness arm
    # (#11258 review round 3 `P1`, measured). Its negation window matches the
    # opening verb's lookbehind run, so an honest denial still passes.
    # ⛔ THE ARM AND THIS RUN ARE ONE UNIT (#11258 review round 17 `P1`, MEASURED):
    # widening the ARM to `open(?:s|ed|ing)?` without widening the RUN turned five
    # honest closures into FALSE BLOCKS — `no longer opens again`, `is no longer
    # opened again`, `is no longer opening again`, `hasn't yet opened again`,
    # `hasn't ever opened again` were GREEN while the arm read only `open again`
    # (no match at all) and RED the moment it read the inflection. An inflection
    # added to the arm needs its own negation form in the run, and every form in the
    # run has a must-stay-GREEN partner in `_RESIDUAL_TRUTHFUL_STATEMENTS`.
    # ⛔ AND EACH SITE HAS ITS OWN PARTNERS, not a shared assumption (#11258 review
    # round 18 `P0`, MEASURED): the run is carried in BOTH sites because the two
    # drift independently, and only the `_RESIDUAL_OPENNESS` copy was falsifiable —
    # deleting this site's copy left the whole file GREEN while five honest waived
    # denials went RED. The `the ADJACENCY half …` partners pin occurrence #1; the
    # `the SPELLING gap …` partners pin THIS one.
    _SPELLING_FLOOR_NEGATION_RUN
    + r"\bopen(?:s|ed|ing)?\s+again\b",
    # ⛔ AND FOR THE `reopen` FAMILY (#11258 review round 24 `P1`, MEASURED): this
    # arm existed on `origin/main` as the third member of
    # `_SPELLING_SENTENCE_REOPENING` (`\bre-?open`) and the rewrite dropped it. A
    # WAIVED sentence runs ONLY this tuple, so `the SPELLING gap is reopened.` was
    # GREEN here while `origin/main`'s own pin REFUSED it — a coverage regression in
    # the shape this artifact exists to refuse, and NOTHING recorded it: the shape
    # appeared in no proof list and in neither ledger. Restored with the SAME run as
    # the arm above (one primitive, not a second copy), with five reopenings pinned
    # in `_RESIDUAL_REOPENING_MUTATIONS` and three honest denials in
    # `_RESIDUAL_TRUTHFUL_STATEMENTS` so the run reaches this arm too.
    _SPELLING_FLOOR_NEGATION_RUN
    + r"\bre-?open(?:s|ed|ing)?\b",
)

#: The row that FOLLOWS the residual list. The proof test anchors its append point
#: on this literal, deliberately NOT on `_residual_region` — anchoring it there is
#: what made the first version of that test self-referential: it inserted each
#: mutation inside whatever region the function returned, so narrowing the bound
#: moved the fixture with it and every mutation stayed GREEN (#11258 review `P1`,
#: measured).
_RESIDUAL_ROW_END = "\n  - id: atomic_point_write"


def _residual_region(text: str) -> str:
    """The `state_value_carve_out` row's own text: its `  - id: ` entry → the next.

    ⛔ THE BOUND IS THE ROW, NOT THE MARKER, and every earlier bound was defeated
    by writing OUTSIDE it (#11223): `[:1200]` → the `(d)` block (10,019 chars) →
    the residual marker (37,942 of 50,850 chars = 74.6% of the row). A reopening
    is a reopening wherever in the row it is written, so the region is the whole
    row and the marker is only a locator. MEASURED that covering the marker's
    12,908-char prefix costs nothing at this revision: the real row carries 0
    claims on the whole row and every entry of both proof lists below is
    unchanged, and a reopening inserted into the prefix
    (`(2b) the ADJACENCY half is open again.`) is GREEN under the marker bound
    and RED under this one.
    """
    assert text.count(_RESIDUAL_MARKER) == 1, (
        f"the registry must carry exactly ONE {_RESIDUAL_MARKER!r} residual list "
        f"(found {text.count(_RESIDUAL_MARKER)}) — this pin is the guard for "
        "that one, so a second copy is a second, UNGUARDED residual list")
    # ⛔ AND THE ROW ITSELF MUST BE UNIQUE: a second `  - id:
    # state_value_carve_out` entry leaves the scan reading whichever copy comes
    # first while the other publishes its own, unguarded residual list — measured
    # on a duplicate appended at the end of the file: both residual tests stayed
    # GREEN. This file family has exactly that history (`b1751f9d80` deduped a
    # three-row duplicate of another entry).
    # ⛔ The count is BRANCHED (#11258 review round 3, `P2`): `0` is not a
    # duplicate, and it is the case a lane most needs diagnosed — the row was
    # renamed, re-indented or wrapped, so the pin must be moved, not narrowed.
    row_copies = text.count(_RESIDUAL_ROW_START)
    assert row_copies == 1, (
        ("the `state_value_carve_out` row is GONE (0 matches for "
         f"{_RESIDUAL_ROW_START!r}) — it was renamed, re-indented or wrapped; move "
         "this pin with it rather than narrowing the scan to something it can "
         "still reach")
        if row_copies == 0 else
        (f"the registry must carry exactly ONE `state_value_carve_out` row "
         f"(found {row_copies}) — this pin scans that row, so a second copy is a "
         "second, UNGUARDED residual list"))
    start = text.find(_RESIDUAL_ROW_START)
    # ⛔ the terminator is searched AFTER the row's own entry start, or the start
    # line itself matches `\n  - id: ` and truncates the region to nothing.
    tail = text[start + len(_RESIDUAL_ROW_START):]
    nxt = re.search(r"\n  - id: ", tail)
    assert nxt is not None, (
        "could not find the END of the `state_value_carve_out` row (the next "
        "`  - id: ` entry) — without it this scan would read the REST OF THE "
        "FILE and red on other rows' prose, which is how a pin gets deleted "
        "rather than fixed")
    region = _RESIDUAL_ROW_START + tail[:nxt.start()]
    # ⛔ ANTI-SILENT-NARROWING: the region must still carry the row's load-bearing
    # literals, or a `  - id: ` written into the row's PROSE truncates this region
    # early while every assertion above still passed. MEASURED reach: a decoy at
    # row+200 / +8,000 / +12,900 / +20,000 / +24,000 / +24,300 REDs here, so this
    # assert covers truncation UP TO `(e) the` (row+24,386); a decoy AFTER it
    # (+30,000 / +40,000 / +50,000) passes this assert and is caught by the append
    # fixture in
    # `test_the_residual_region_is_scanned_WHERE_a_lane_appends` instead, which
    # inserts at the row's `_RESIDUAL_ROW_END` literal and asserts the sentence is
    # inside the region. Two anchors rather than one because the row is 50,850
    # chars and a single early literal leaves the tail unguarded here.
    assert (_RESIDUAL_MARKER in region and "(d) a RATE unit" in region
            and "(e) the" in region), (
        "the scanned region does not run from the row's start through residual "
        f"(e) — it truncated early at {len(region)} chars (prose inside "
        "the row containing `  - id: `?), so the scan would silently cover less "
        "than the row it claims to guard")
    return region


#: The clause boundary the scan splits on before matching. ⛔ `while`/`whereas`/
#: `although`/`though` belong here for the same measured reason `and`/`but`
#: do: they join two claims too, and a joiner the splitter does not know leaves an
#: honest `spelling` disclosure and a reopening in ONE segment — so the honest
#: waiver swallowed the second claim (`the SPELLING gap is open while the
#: remaining gap is not closed` GREEN; the same claim unwrapped RED — #11258 review
#: round 4 `P2`, measured).
#: ⛔ `yet` IS A JOINER TOO, GUARDED — and an earlier cut of this note said the
#: opposite (#11258 review round 23 re-review, MEASURED): `yet\s+` IS a branch of
#: the alternation, behind `(?<![n'\u2019]t\s)(?<!\bnot\s)(?<!\bnever\s)` so that
#: `hasn't yet opened again` is not severed. Those lookbehinds narrow WHEN it may
#: split; they do not remove it from the group. Deleting the branch greens
#: `the SPELLING gap is open yet the remaining gap is not closed.`, which is pinned
#: in `_RESIDUAL_REOPENING_MUTATIONS`.
#: ⚠️ This is a CLOSED list standing in for a clause
#: boundary; a joiner not named here still joins. That residual is disclosed in
#: `WHAT THIS CANNOT CATCH` rather than implied to be covered.
_RESIDUAL_CLAUSE_SPLIT = (
    # ⛔ `yet` IS BARRED BEHIND A NEGATOR (#11258 review round 18 `P1`, MEASURED).
    # The splitter bit at `yet` in `hasn't yet opened again`, dropped the honest
    # `spelling` clause and rejoined the survivors with ` , ` — so the negator and
    # the participle landed in different segments and `the SPELLING gap hasn't yet
    # opened again.` went RED while the same denial WITHOUT an honest clause was
    # GREEN (pinned). A verdict must not depend on unrelated content in the
    # sentence. The bar is fixed-width and applies to `yet` ONLY. ⛔ IT DOES NOT
    # REOPEN THE ROUND-7 HOLE: the closed-verdict lookahead below still refuses to
    # split `has yet to be closed` (measured at this head: the sentence stays ONE
    # segment, so `_RESIDUAL_CLOSED_VERDICT` reads it whole). The bar narrows WHO
    # may split at `yet`; the lookahead still decides WHERE it may not.
    r";|,?\s+(?:(?:and|but|while|whereas|although|though)\s+"
    # ⛔ `’` IS IN THE CLASS HERE TOO: the bar exists to keep a negator attached to
    # its `yet`, and it was the round-18 `P1` fix — with the ASCII-only class the
    # curly spelling severed it again, so the verdict depended on unrelated
    # content in the sentence, which is exactly what that round removed.
    r"|(?<![n'\u2019]t\s)(?<!\bnot\s)(?<!\bnever\s)yet\s+)"
    # ⛔ THE COORDINATOR MUST NOT BITE INSIDE A VERDICT'S OWN TEXT. `has yet to be
    # closed` splits at `yet` and `anything but closed` at `but`; severing them put
    # the two halves of `_RESIDUAL_CLOSED_VERDICT` in different segments, so after an
    # honest `spelling` clause was removed and the survivors rejoined they never
    # matched — a fail-open reachable by clause ORDER: `the SPELLING gap is open, and
    # the half has yet to be closed, per the ADJACENCY row` was GREEN while the same
    # claim with the anchor first was RED (#11258 review round 7 `P1`, measured).
    r"(?!(?:to\s+be\s+closed|closed)\b)")

def _residual_reopening_claims(text: str) -> list[str]:
    """Every claim in the residual region that REOPENS a gap the row closed.

    REGION-WIDE, SENTENCE-scoped, and anchored only where it has to be. Each of
    those three is a correction, measured — not a preference:

    * region-wide: the region used to stop at `(e) the`, so a reopening appended
      where a lane appends one was never read (#11223);
    * SENTENCE-scoped, not clause-scoped: splitting a sentence on `;`/`, and` puts
      the class anchor in one half and the openness phrase in the other, so
      `the ADJACENCY half is now CLOSED, but the exception it carved is open
      again.` and `the ADJACENCY half is not, and never was, closed.` both PASSED
      a clause-scoped scan (#11258 review `P2`, measured);
    * anchored ONLY for the opening verb, and for the SPELLING waiver: requiring
      the class anchor of EVERY phrase let `the gap those tables closed is open
      again` through — a shape the PRE-change guard caught (#11258 review `P1`).

    A sentence that discloses the still-open SPELLING class and names no class
    anchor is honest (that gap IS open) and gets only `_SPELLING_FLOOR`.
    Returns the offending phrase with its sentence, so a red names both.
    """
    claims: list[str] = []
    flat = " ".join(_residual_region(text).split()).lower()
    for sentence in re.split(r"(?<=[.!?;])\s+", flat):
        # ⛔ THE WAIVER IS CLAUSE-SCOPED EVEN THOUGH MATCHING IS PER SENTENCE. The
        # two scopes answer two different failures, measured in opposite
        # directions: matching must be per SENTENCE, or a `;`/`, and` split
        # separates a class anchor from its openness phrase (round 1 `P2`); the
        # WAIVER must be per CLAUSE, or one honest `spelling` clause waives a
        # reopening in the NEXT one — "The SPELLING gap is open on purpose, but
        # the remaining gap is not closed." stayed GREEN with a sentence-scoped
        # waiver (round 2 `P2`). So the honest clauses are REMOVED and what is
        # left is scanned.
        clauses = re.split(_RESIDUAL_CLAUSE_SPLIT, sentence)
        honest = [c for c in clauses if _is_honest_spelling(c)]
        # ⛔ THE NEGATED CLOSED VERDICTS DO NOT NEED A SENTENCE-SCOPED PASS, and the
        # one that sat HERE has been DELETED (#11258 review round 22 `P2`, MEASURED).
        # Its job was round 5's `P2` (`has yet to be closed` splits at `yet`,
        # `anything but closed` at `but`, and rejoining the pieces put the pattern's
        # two halves in different segments). That job is now done by the SPLITTER's
        # own lookahead, `(?!(?:to\s+be\s+closed|closed)\b)` in
        # `_RESIDUAL_CLAUSE_SPLIT` (round 7), so this pass supplied nothing — and a
        # review measured it DELETEABLE WHILE GREEN: removing the loop and its helper
        # left 135/135 mutations RED, every truthful statement GREEN, 15/15 ledger
        # entries refused and the row green. It is the class rounds 17/18 filed as
        # `P0` (an arm deletable while green), so it is gone rather than left looking
        # like coverage; its helper `_match_is_honest_spelling_disclosure` had no
        # other caller and went with it.
        # ⛔ WHY IT COULD NOT BE RESCUED WITH A FALSIFIER: `_RESIDUAL_CLOSED_VERDICT`
        # also travels in `_RESIDUAL_ANCHORED_OPENNESS`, which runs on `scan`, and
        # `_RESIDUAL_WAIVER_WITHHOLD` is a literal SUPERSET of
        # `_RESIDUAL_CLASS_ANCHOR` — so a clause carrying an anchor can never be an
        # honest one, therefore can never be the clause REMOVED, therefore the text
        # a sentence-scoped pass would judge always reaches `scan` too. That is an
        # argument, so it is also measured, both directions, at the head this note
        # was written at: round 5's
        # `the SPELLING gap is open, and the time-unit half has yet to be closed`
        # still REDS, and round 6's over-block statement
        # `the SPELLING gap has never been closed, and the ADJACENCY half is closed
        # by the tables` is still GREEN (it is in `_RESIDUAL_TRUTHFUL_STATEMENTS`,
        # which re-measures it on every run).
        if honest:
            if len(honest) == len(clauses):
                for pattern in _SPELLING_FLOOR:
                    m = re.search(pattern, sentence)
                    if m is not None:
                        claims.append(
                            f"{m.group(0)!r} in sentence {sentence.strip()!r}")
                        break
                continue
            # ⛔ THE FLOOR JUDGES EACH HONEST CLAUSE — it is what the waived clause
            # is judged BY, so it must not be reachable only when EVERY clause is
            # honest (#11258 review `P1`, MEASURED at `0f48bcd05db`). Removing the
            # honest clauses from `scan` and then never running the floor on them
            # left `the SPELLING gap is reopened, and the pair is fused.` GREEN: the
            # second clause is TRUE and carries no anchor, and it decided the
            # verdict for a reopening in the first — the class round 18's `P1` was
            # filed for. THIRTEEN of the 157 pinned mutations went GREEN once one
            # truthful clause was appended, so the list is non-vacuous alone and
            # vacuous under composition. `origin/main` ran this floor PER CLAUSE
            # (`_SPELLING_SENTENCE_REOPENING`), so the all-honest-only path was a
            # COVERAGE REGRESSION, not a widening — measure it against the same
            # site inside residual `(d)` and main REDS the three mixed shapes this
            # head passed.
            # ⚠️ The all-honest branch above searches the WHOLE SENTENCE and is
            # left alone: a per-clause search is a subset of it, so changing it
            # could only narrow the floor's reach there. The floor still omits
            # `not closed` / `is open` deliberately, so the honest disclosures it
            # exists to allow cannot be blocked by this pass.
            floor_hit = None
            for clause in honest:
                for pattern in _SPELLING_FLOOR:
                    m = re.search(pattern, clause)
                    if m is not None:
                        floor_hit = m.group(0)
                        break
                if floor_hit is not None:
                    break
            if floor_hit is not None:
                claims.append(f"{floor_hit!r} in sentence {sentence.strip()!r}")
                continue
            # ⛔ REJOINED WITH `, `, NOT `. ` — see the round-5 note below.
            scan = " , ".join(c for c in clauses if not _is_honest_spelling(c))
        else:
            scan = sentence
        patterns = list(_RESIDUAL_OPENNESS)
        if _RESIDUAL_CLASS_ANCHOR.search(scan):
            patterns += _RESIDUAL_ANCHORED_OPENNESS
        for pattern in patterns:
            m = re.search(pattern, scan)
            if m is not None:
                claims.append(f"{m.group(0)!r} in sentence {sentence.strip()!r}")
                break
        # ⛔ THE UNHYPHENATED `fails open` IS CLAUSE-ANCHORED (`_RESIDUAL_FAIL_OPEN`):
        # it can only be read when THIS clause names the class, which is what keeps
        # the row's own `the pair fails open` disclosure green while a lane's
        # `the ADJACENCY half fails open` reds (#11258 review `P2`).
        for clause in re.split(_RESIDUAL_CLAUSE_SPLIT, scan):
            m = _RESIDUAL_FAIL_OPEN.search(clause)
            if m is not None and _RESIDUAL_CLASS_ANCHOR.search(clause):
                claims.append(f"{m.group(0)!r} in clause {clause.strip()!r}")
                break
    return claims


def _is_honest_spelling(text: str) -> bool:
    """Does this TEXT disclose the still-open SPELLING class and nothing else?

    That gap IS open, so saying so is the row's own honest disclosure — but the
    waiver is only ever granted to a span that names NO class anchor, so it can
    never travel to a claim about the gap this row closed.

    ⛔ The withholding set is `_RESIDUAL_WAIVER_WITHHOLD` — NOT
    `_RESIDUAL_CLASS_ANCHOR` "in full", which is what this docstring said until
    #11258 review round 17 `P2` measured that false at the head. It is the anchor
    PLUS two arms the anchor does not carry: the unit-spelling family declared as
    `_RESIDUAL_UNIT_WAIVER_ARMS`, and the bare `(?<!['’])\b[ms]\b` abbreviation.
    ⛔ THE MEMBERS ARE DELIBERATELY NOT RE-ENUMERATED HERE, because two cuts of
    this paragraph drifted from the code precisely by re-listing them: it named
    `unit word`, which no declaration carries (`units?` already matches `unit` —
    DEAD, removed in round 9), and it dropped "a unit token or its abbreviation",
    which is where `hrs`/`minute` live, while claiming a shape (`… because `kg`
    is no longer fused`) that `_SPELLING_FLOOR` reds even under the narrower
    anchor-only withholding — the shape that actually flips is `… because `kg` is
    not closed` (#11258 review rounds 18-19 `P2`, MEASURED). The declarations
    above are the statement: `test_every_waiver_withhold_arm_earns_its_place`
    derives its sample spellings from them, so an arm added or removed there is
    measured the moment it is declared. WIDEN THAT SET, NOT
    `_RESIDUAL_CLASS_ANCHOR`: the anchor also arms `_RESIDUAL_ANCHORED_OPENNESS`,
    and widening it reds every truthful statement (MEASURED). What the anchor
    alone must keep out is unchanged: with `carve-out` dropped from it, `the
    SPELLING carve-out is open again` was waived and the same sentence without
    `spelling` red (round 3 `P1`, measured).

    ⛔ The cost, named rather than implied: a clause naming BOTH `spelling` and the
    carve-out gets no waiver, so honest prose of that shape is refused. That is an
    over-block, which this artifact prefers to the fail-open it replaces.
    """
    return ("spelling" in text
            and not _RESIDUAL_WAIVER_WITHHOLD.search(text))


def _assert_the_row_names_its_closure(row: str) -> None:
    """The pin's WHOLE assertion body, callable by its own proof (#11258).

    ⛔ A FUNCTION AND NOT INLINE, for a measured reason: with the body inline,
    deleting its two wiring lines (`claims = _residual_reopening_claims(row)`
    and `assert claims == []`) left BOTH the pin and its proof test GREEN, and
    the pin then passed over `⛔ (d3) the ADJACENCY half is open again.`
    appended at the residual list's end — the exact state #11223 exists to
    refuse (#11258 review round 7 `P2`, reproduced by the reviewer). The proof
    list called the helper directly, so it proved the HELPER and never that
    the pin used it; `test_the_residual_region_is_scanned_WHERE_a_lane_
    appends` now calls THIS function on the mutated row, so detaching the scan
    from the assertion it feeds reds the proof.

    Takes the row's TEXT rather than reading the file, so the proof can feed
    it a mutated copy. `test_the_registry_row_names_the_residual_that_still_
    stands` is the only caller that reads the artifact.
    """
    residual = row.split("(d) a RATE unit", 1)
    assert len(residual) == 2, (
        "the registry row no longer states residual (d) as the RATE-unit gap — "
        "if the adjacency gap was reopened, say so there; if (d) was closed, "
        "delete this pin and the row together")
    after = residual[1][:1200]
    assert "per\n      day" in after or "per day" in after, after
    assert "ADJACENCY" in after.upper(), after
    # ⛔ BOTH HALVES, or the pin is half-vacuous (review cycle 3, MEASURED: with
    # only the two assertions above, flipping `is CLOSED` to `is still OPEN`
    # stayed GREEN — the header and the word ADJACENCY survive any rewording of
    # the verdict).
    #
    # ⛔ AND A BARE `CLOSED` SUBSTRING IS STILL HALF-VACUOUS (review cycle 4,
    # MEASURED: `is not closed:` contains `CLOSED`).
    #
    # ⛔ AND A PHRASE MATCH PLUS A NEGATION ALLOWLIST IS STILL FAIL-OPEN, on the
    # exact shapes a lane writes (review cycle 5, MEASURED). Two leaks:
    #   (a) the two checks were DECOUPLED, so keeping the closed sentence and
    #       appending "the ADJACENCY half remains open" passed — the negation
    #       list is an unbounded-enemy allowlist and "remains open" / "open
    #       again" / "except `minute`, which is open again" are not on it;
    #   (b) the guard read `[:1200]` while residual (d) runs 1339 chars, so a
    #       reopening note appended where a lane APPENDS it was invisible EVEN
    #       with a phrase from the list ("no longer closed").
    # So: scan EVERY sentence for a reopening, and separately require the block
    # to still NAME the adjacency claim as closed — the scan is not limited to
    # sentences that say `adjacency`, because a reopening that names the gap
    # another way ("`minute` no longer fuses", "the time-unit half is no longer
    # closed", "except `minute`, which is open again") is the likeliest edit of
    # all (review cycle 6, MEASURED: with the scan gated on `adjacency`, all five
    # of those PASSED).
    # ⛔ AND `(d) THE` IS NOT THE BOUND EITHER (#11223, MEASURED: that block is
    # 10,019 chars and does not contain the row's own `(d2)` clause, so a
    # reopening appended after it was never read at all — the position a lane
    # actually appends in). The scan is now REGION-WIDE, from the row's own
    # `  - id: ` entry → the next entry; the class anchor is what keeps a truthful
    # statement about some OTHER subject from tripping it, and the closed-claim
    # rule stays scoped to `(d)` where that claim lives.
    # The block's own structural half is asserted here; the reopening half lives
    # in `_residual_reopening_claims`, whose proof list is `len()` of the two
    # tuples below (`_RESIDUAL_REOPENING_MUTATIONS` must all RED,
    # `_RESIDUAL_TRUTHFUL_STATEMENTS` must all stay GREEN) and is PINNED by shape
    # and floor in that test, and is
    # `test_the_residual_region_is_scanned_WHERE_a_lane_appends` below — that test
    # is what makes the list above a checked claim rather than a docstring.
    # — plus the structural rule that the still-open SPELLING disclosure must be
    # its OWN sentence, because one sentence carrying both claims must satisfy
    # both and cannot.
    block = row.split("(d) a RATE unit", 1)[1].split("(e) the", 1)[0]
    flat = " ".join(block.split()).lower()
    sentences = re.split(r"(?<=[.!?;])\s+", flat)
    adjacency_at = [i for i, s in enumerate(sentences) if "adjacency" in s]
    # ⛔ `assert adjacency_at` WAS DELETED HERE AND IT IS NOT TIDINESS (#11258
    # round 27 `P2`, MEASURED): it was DEAD, and it was the one structural rule
    # with no killing mutant — the exact class the record in `test_every_structural
    # _rule_in_the_pin_has_a_falsifier` claims is empty. `after` is
    # `residual[1][:1200]` and `block` is `residual[1]` up to `(e) the` (10,019
    # chars), so `after ⊆ block`; the assert 3 lines above
    # (`"ADJACENCY" in after.upper()`) therefore implies "adjacency" ∈ `flat`,
    # which implies this list is non-empty. MEASURED by deletion: patching this
    # assert to `pass` changed NO verdict — control PASS, all 10 falsifiers red at
    # the same rules — so it could never fire.
    # The loop below is therefore GUARDED BY AN IMPLICATION, not by an assert, and
    # that is sound only while `after ⊆ block` holds: if a future edit reads `block`
    # from a NARROWER window than `after`, the implication breaks and the loop
    # silently goes vacuous. `test_every_structural_rule_in_the_pin_has_a_falsifier`
    # re-measures the premise (`after in block` on the real row) and reds if it
    # stops holding, so the vacuity route is recorded rather than left to be
    # discovered — and the `adjacency-claim-exists` entry that used to stand in
    # here is gone with the assert, because its mutant reddened the WINDOW rule
    # above instead of this one (a label that no longer named its rule).
    # ⛔ THE CLAIM ITSELF must still say CLOSED, per SENTENCE (review cycle 7 P1,
    # MEASURED: collapsing this to a block-wide `"closed" in flat` let a row that
    # says "the ADJACENCY half is not currently judged; the closed set grows"
    # pass — any unrelated `closed` in the 1339-char block satisfied it).
    for i in adjacency_at:
        assert any(re.search(p, sentences[i])
                   for p in _ADJACENCY_CLOSED_CLAIM), (
            "the row does not ASSERT the ADJACENCY gap as CLOSED (a `closed` "
            f"elsewhere in the sentence is not enough): {sentences[i]!r}")
    # ⛔ AND THE REOPENING SCAN IS REGION-WIDE (#11223), which is the ONLY place it
    # can live: every earlier bound here — `[:1200]`, the `(d)` block, the tail
    # after the last adjacency sentence — was defeated by appending the reopening
    # OUTSIDE it, and the block a lane reads is 10,019 chars while the residual
    # list it appends to runs to the end of the row. `_residual_reopening_claims`
    # carries the whole rule (sentence-scoped, class-anchored, spelling floor) and
    # its own proof list is `test_the_residual_region_is_scanned_WHERE_a_lane_
    # appends` below — this call is the pin, that test is the pin's own proof.
    claims = _residual_reopening_claims(row)
    # ⛔ A REFUSAL WHOSE SENTENCE CONTAINS A LEDGER SHAPE SAYS SO, and it POINTS
    # rather than ruling (#11258 review round 11 `P2`/`P3`, MEASURED): the sentence
    # can carry BOTH an honest ledger shape and a genuine reopening —
    # `the ADJACENCY half is not closed, and (b2) is not closed.` matches the
    # `(b2)` entry inside a real reopening — so a message that calls it "a recorded
    # over-block, not a reopening" states something false about the artifact it
    # just refused. The comparison is LOWERCASED on both sides because `claims`
    # carries the sentence lower-cased while five of the seven ledger entries are
    # spelled in caps: a case-sensitive compare found 5 of 7 undetectable, the
    # `minute` cost included (MEASURED).
    # ⛔ AND THE ROUTE OUT IS NOT CONDITIONAL ON BEING IN THE LEDGER (#11258 review
    # round 19 `P2`, MEASURED). It used to be: the whole pointer paragraph hung off
    # `if ledger_hits else ""`, so an honest sentence about a still-open item that
    # is NOT one of the recorded entries got the bare accusation and nothing else —
    # `(a) is not closed.`, `(c) is not closed.`, `(e1) is not closed.` are items of
    # THIS row's own still-open list, so the statement is true, yet the message told
    # the lane its disclosure "REOPENS a gap this row says is CLOSED" with no
    # indication that `is still open` / `remains open` are accepted — the
    # false-block path that ends with the pin being deleted. `ledger_hits` is now
    # the SPECIFIC-MATCH detail line only; the pointer is emitted on every refusal.
    ledger_hits = [s for s in _RESIDUAL_REFUSED_TRUTHFUL
                   if any(s.rstrip(".").lower() in c.lower() for c in claims)]
    assert claims == [], (
        "the residual region REOPENS a gap this row says is CLOSED: "
        + "; ".join(claims)
        + ("\n⛔ A LEDGER SHAPE IS ALSO IN THIS SENTENCE: "
           f"{ledger_hits}." if ledger_hits else "")
        + "\n⛔ THE ROUTE OUT IS THE OVER-BLOCK LEDGER, ALWAYS: "
        "`_RESIDUAL_REFUSED_TRUTHFUL` records truthful statements this pin "
        "refuses, and its note gives the arm, why that arm cannot be narrowed, "
        "and — where one exists — the phrasing accepted in its place. Read it "
        "before rewriting: if the WHOLE sentence is honest and an entry records "
        "it, that entry is why this red is not yours (for `not … closed`: `is "
        "still open` / `remains open` are MEASURED GREEN, and a still-open item "
        "of this row's own list belongs in the ledger rather than outside it); "
        "if the sentence also reopens this gap, the rest of the sentence is the "
        "defect.")
    # ⛔ TWO TAIL ASSERTS WERE DELETED HERE, and the reason is measured rather than
    # tidy (#11258 review round 24 `P1`): `assert not tail or any("spelling" in s
    # for s in tail)` and the `spelling` + `open` variant over the SAME `tail` are
    # both IMPLIED by the record in `test_every_structural_rule_in_the_pin_has_a
    # _falsifier` (`sum("spelling" in s for s in tail) > 1` and
    # `sum("spelling" in s and "open" in s for s in tail) > 1`), so neither could
    # ever fire: MEASURED, deleting either alone leaves `108 passed`, and deleting
    # both leaves `108 passed`. An arm that cannot red is the rounds-17/18 `P0`
    # class — and this file already deleted one dead pass (`_RESIDUAL_CLOSED_VERDICT`'s
    # sentence-scoped half) on exactly that ground, so leaving these would be the
    # same defect one screen down. The RECORD is the guard, and it is strictly
    # stronger: it reds on the vacuity route too (a trailing `the ADJACENCY half is
    # CLOSED.` empties `tail`, and the record then asserts `0 > 1`).
    # positive control: the RATE class IS still open, and it must stay NAMED, or
    # the assertions above could be satisfied by a row that says nothing at all.
    assert "not judged" in flat, block
    # ⛔ AND THE BLOCK-WIDE `per day` ASSERT THAT STOOD HERE WAS DELETED (#11258
    # round 27 `P3`, MEASURED): unreachable, not merely late. `flat` is `block`
    # whitespace-flattened and `block ⊇ after`, so the opening-window assert above
    # (`"per\n      day" in after or "per day" in after`) already implies this one;
    # MEASURED by deletion: patching it to `pass` left the matrix BYTE-IDENTICAL
    # (control PASS, every falsifier red at the same line). Same class as the two
    # tail asserts deleted above, and the `rate-per-day` falsifier still reds on the
    # window assert, so the rule is not left unpinned.


def test_the_registry_row_names_the_residual_that_still_stands():
    """`config/v4-mechanisms.yml` must not describe a CLOSED gap as open.

    The row's (d) is the artifact a lane reads ("⛔ WHAT IS STILL NOT ENFORCED —
    read this before improving a number"). It named the ADJACENCY gap
    (`seconds`, `minute`, `hour`, `day`, `mile`) as unjuged; that gap is closed
    by the tables above, so the row was left FALSE and nothing red on it. This
    pins both halves: the closed class must not come back into the row, and the
    still-open RATE class must stay named there.
    """
    _assert_the_row_names_its_closure(
        (pathlib.Path(__file__).resolve().parent.parent
         / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8"))


#: Reopenings a lane actually writes, appended AT THE END OF THE RESIDUAL LIST —
#: the position all of them must RED from (#11223). Each names the class (a unit
#: token, `adjacency`, the `time-unit` half, the carve-out, the value class, the
#: numeric side), because that is what makes it a claim about THIS row's gap
#: rather than about a decision, a false refusal or a ruling.
_RESIDUAL_REOPENING_MUTATIONS = (
    # the issue's own required mutation, appended after the residual list
    "the ADJACENCY half is open again",
    "except `minute`, which is open again.",
    "`minute` no longer fuses",
    "the time-unit half is no longer closed",
    "the ADJACENCY half remains open",
    "the adjacency gap is not closed",
    "the ADJACENCY half has not yet been closed",
    # round 8 (#11258 review round 8 `P2`, MEASURED): the CONTRACTED auxiliary. The
    # uncontracted twins above and below RED; the contractions were GREEN because
    # the arm carried `isn't` and only `isn't`. Each is a plain reopening a lane
    # appends — `wasn't closed` says the gap is open in the past tense.
    "the ADJACENCY half wasn't closed.",
    "the ADJACENCY half hasn't been closed.",
    "the ADJACENCY half hadn't been closed.",
    "the ADJACENCY half weren't closed.",
    # ⛔ ROUND 9: the two spellings the AUXILIARY ENUMERATION could not reach, and
    # the `yet to be closed` synonyms. `cannot` has no word boundary after `can`,
    # `shan't` was simply never listed, and `remains to be closed` is the natural
    # residual-list phrasing of the same claim. Each was MEASURED GREEN at the head
    # that enumerated auxiliaries, which is why the arm is now a shape.
    "the ADJACENCY half cannot be closed.",
    "the ADJACENCY half shan't be closed.",
    "the ADJACENCY half remains to be closed.",
    "the ADJACENCY half is still to be closed.",
    # ⛔ THE `no longer …` MOVEMENT (#11258 review round 9 `P1`): with the floor's
    # old slack this pattern and its six exclusive proofs were deletable in one
    # edit, and this shape then measured GREEN. It is the one the membership loop
    # pins that pattern by.
    "the ADJACENCY half is no longer closed.",
    "the ADJACENCY half is no longer judged.",
    # ⛔ THE FILLER WINDOW (#11258 review round 11 `P1`, MEASURED): each of these
    # was GREEN while its window-free twin RED, because the arm required the verb
    # to follow `no longer` immediately.
    "the ADJACENCY half is no longer being closed.",
    "the ADJACENCY half is no longer being judged.",
    "the ADJACENCY half is no longer even judged.",
    "the ADJACENCY half is no longer actively judged.",
    "the ADJACENCY half is no longer being fused.",
    # round 16: gate (a) mirrored only six of the anchor vocabulary's nine
    # members, so the SAME predicate was RED for `adjacency` and GREEN for
    # `retention` / `the guarantee` / `the gap that/those … closed`.
    "the retention guarantee is no longer fused.",
    "the gap those tables closed is no longer fused.",
    # ⛔ ROUND 12 (#11258 review round 12, MEASURED): the window was fixed in ONE
    # arm and left out of the other three, and `\s+` after `longer` let a
    # punctuated insertion defeat all of them. One shared `_NO_LONGER_WINDOW`
    # primitive now serves every arm — these are its probes, one per arm.
    "`minute` is no longer being fused.",
    "the units are no longer being fused.",
    "the SPELLING gap is no longer being fused.",
    "the ADJACENCY half is no longer, strictly speaking, closed.",
    "the value class is no longer, once the widen lands, fused.",
    "the ADJACENCY half is no longer, at this head, judged.",
    "the ADJACENCY half is far from being closed.",
    "the ADJACENCY half is anything but being closed.",
    # ⛔ ROUND 13 (#11258 review round 13 `P1`, MEASURED): the same `\s+`
    # adjacency survived in `_RESIDUAL_CLOSED_VERDICT`, whose plain forms all
    # pinned RED while ONE interpolated word or comma — ordinary English, and
    # pinned RED here in the three forms below (⛔ NOT "the row's own register": the
    # row writes none of them, #11258 round 21, MEASURED) — turned them GREEN.
    "the time-unit half has yet, in practice, to be closed.",
    "the ADJACENCY half remains, for now, to be closed.",
    "the ADJACENCY half does, in practice, not close the gap.",
    "the ADJACENCY half cannot close the gap.",
    "the ADJACENCY half is anything but being fully closed.",
    # ⛔ THE APOSTROPHE-LESS CONTRACTIONS (#11258 review round 10 `P1`, MEASURED):
    # these four were RED, then went GREEN when round 9 made the apostrophe
    # mandatory to avoid `point`/`count` — a REGRESSION introduced by a fix. They
    # are the reason the arm's second branch is a KNOWN-STEM list rather than an
    # optional apostrophe on an unbounded stem.
    "the ADJACENCY half isnt closed.",
    "the ADJACENCY half wasnt closed.",
    "the ADJACENCY half hasnt been closed.",
    "the ADJACENCY half hadnt been closed.",
    # the present-tense verb: `closed` is not the only form that asserts openness
    "the ADJACENCY half doesn't close the gap.",
    "the ADJACENCY half does not close the gap.",
    "the ADJACENCY result is unclosed",
    # ⛔ THE EXCLUSIVE PROOF FOR THE FLOOR'S `\bunclosed\b` (#11258 review round 15,
    # MEASURED at `474435d658`): `the ADJACENCY result is unclosed` above is caught
    # by `_RESIDUAL_OPENNESS`'s own `\bunclosed\b` — a class-anchored sentence never
    # reaches `_SPELLING_FLOOR` — so deleting the floor's arm left `103 passed`.
    # The floor is the ONLY tuple a WAIVED sentence runs, so the shape that pins it
    # has to be the SPELLING-subject one; with this entry the same deletion is
    # `1 failed`.
    "the SPELLING gap is unclosed.",
    # the SPELLING clause is honest; a `;`/`, and`-joined second clause is not
    "the SPELLING gap is not closed and `minute` no longer fuses",
    "The SPELLING gap is open, and the carve-out is still open.",
    "the carve-out is still open",
    "the carve-out reopens.",
    "the value class is no longer judged.",
    "the numeric side is no longer compared.",
    "`min` stays open",
    # the strongest phrase is not weakened by the `fail-open` hyphen boundary
    "the time-unit half is fail-open again",
    "the ADJACENCY half is not closed yet",
    "the ADJACENCY gap is open",
    "the carve-out is no longer closed",
    # ⛔ THE #11258 REVIEW'S OWN CASES, each one a shape the pre-review revision
    # let through and the PRE-PR guard caught — the anchor is required of the
    # polysemous arm only, so a reopening that names the gap in other words still
    # reds (#11258 review P1/P2, measured: OLD=True / NEW=False before this).
    "the gap those tables closed is open again.",
    "the remaining gap is not closed.",
    "the ADJACENCY half is now CLOSED, but the exception it carved is open again.",
    "the ADJACENCY half is not, and never was, closed.",
    "the ADJACENCY half is fail-open.",
    # round 2: the object / participle / parenthetical complements the closed
    # adverb list had dropped, and the filler tokens it left unpinned
    "the ADJACENCY half keeps the gap open.",
    "the ADJACENCY gap is kept open.",
    "the ADJACENCY gap is held open.",
    "the ADJACENCY half remains, in practice, open.",
    "the ADJACENCY half is, in practice, open.",
    "the ADJACENCY gap is left open.",
    "the ADJACENCY gap is now open.",
    "`minute` is back in the open set.",
    # round 2: one honest `spelling` clause must not waive the NEXT clause
    "The SPELLING gap is open on purpose, but the remaining gap is not closed.",
    "The SPELLING gap is still open, and the remaining gap is open again.",
    # round 3: a coordinator that JOINS two adjectives inside ONE predicate — the
    # blanket coordinator bound missed these
    "the ADJACENCY gap is unjudged and open.",
    "the ADJACENCY half is unfused and open.",
    "the ADJACENCY gap is unjudged, and open.",
    "the ADJACENCY gap remains unjudged and open.",
    "the ADJACENCY half is unjudged but open.",
    # round 4 (#11258 review `P2`): the row's OWN vocabulary for this gap, used by
    # a lane instead of a disclosure. `is fail-open` was already read; these are the
    # UNHYPHENATED verb form, which `_RESIDUAL_FAIL_OPEN` reads CLAUSE-anchored so
    # the row's own `the pair fails open` stays green. ⛔ The coordinator form
    # (`… is unjudged and fails open`) is NOT here: it stays GREEN and is disclosed
    # as a residual on `_RESIDUAL_FAIL_OPEN` rather than asserted as covered.
    "the ADJACENCY half fails open.",
    "the time-unit half fails open.",
    "the carve-out fails open.",
    "the value class fails open.",
    # ⛔ THE EMPHATIC FORM OF THE SAME PREDICATE (#11258 review round 25 `P2`):
    # `not only fails open` ASSERTS it, so the `fails open` arm's new negation veto
    # must not swallow it. This pins the veto's NARROWNESS — it is four fixed-width
    # lookbehinds immediately in front of the verb, so `not only` never vetoes — and
    # it goes GREEN the moment the veto is widened into a window scan, the same
    # coverage regression round 2 `P1` recorded for the `open` arm (`is not only open`).
    "the ADJACENCY half not only fails open, it leaks.",
    # round 5 (#11258 review round 2 `P1`): EMPHATIC negations ASSERT the gap is
    # open — a negation veto wide enough to refuse these is a coverage regression,
    # and one was shipped for a round before these probes caught it.
    "the ADJACENCY half is not only open, it is unfused.",
    "the ADJACENCY half is not merely open.",
    "the ADJACENCY half is not just open, it is unjudged.",
    "the ADJACENCY half is never far from open.",
    # ⛔ THE `no longer` NEGATOR IS THE SAME TRADE, ONE WORD FAMILY OVER (#11258
    # review round 7 `P2`, MEASURED). `is no longer open` is the PLAINEST English
    # for `is closed` and it was a false block; the veto now admits `no\s+longer`
    # as a negator, keeping the same after-negator adverb allowlist. These three
    # are what keeps that widening honest: each is an EMPHATIC `no longer` that
    # still ASSERTS the gap is open, and each must stay RED. Without them the
    # widening could creep (an adverb allowlist that forgot `only`/`merely`/`far`
    # would turn all three GREEN, which is round 2's measured regression again).
    "the ADJACENCY half is no longer merely open.",
    "the ADJACENCY half is no longer only open.",
    # round 14: an interpolated `, in practice, ` pushes the filler past the arm's
    # 30-char bound, so the flipper bar never ran. ⛔ NOT "the row's OWN `, in
    # practice, ` interpolation" — the row writes no `in practice` at all (0
    # occurrences, exact AND flattened, and `git log -S` is empty; #11258 round
    # 21, MEASURED). The form is pinned here because the language offers it.
    "the ADJACENCY half is no longer, in practice, merely open.",
    "the ADJACENCY half is no longer, in practice, simply open.",
    "the ADJACENCY half is no longer far from open.",
    # round 6 (#11258 review round 3 `P1`): THE WAIVER IS NOT A PREFIX. Each of
    # these was GREEN while its counterpart without the word `spelling` RED — the
    # waiver skipped `_RESIDUAL_OPENNESS` whole, so one word defeated the gate.
    "the SPELLING gap is open again.",
    "the SPELLING carve-out is open again.",
    "the SPELLING carve-out stays open.",
    "the SPELLING carve-out is not closed.",
    "the SPELLING carve-out fails open.",
    # round 7 (#11258 review round 4): the CLOSED-verdict arm enumerated `not` and
    # missed `never`/`yet to be`/`far from` — the same claim in other words — and
    # the anchor vocabulary had no subject phrase for this gap, so
    # `<subject> is open` was blind unless it said `adjacency`.
    "the ADJACENCY gap was never closed.",
    "the ADJACENCY half is far from closed.",
    "the ADJACENCY half is anything but closed.",
    "the time-unit half has yet to be closed",
    "the gap those tables closed is open.",
    # round 9 (#11258 review round 5): the honest-`spelling` clause is REMOVED and
    # the survivors rejoined, so a join that used to be `and`/`but` was severing the
    # new patterns' own halves — `has` / `to be closed`, `anything` / `closed`. They
    # are rejoined with ` , ` now; these two pin it.
    "the SPELLING gap is open, and the time-unit half has yet to be closed.",
    "the SPELLING gap is open, and the ADJACENCY half is anything but closed.",
    # round 8 (#11258 review round 4): the clause splitter did not know
    # `while`/`although`, so an honest `spelling` disclosure and a reopening joined
    # by one of them shared a segment and the waiver swallowed the second claim.
    "the SPELLING gap is open while the remaining gap is not closed.",
    "the SPELLING gap is open although the remaining gap is not closed.",
    # ⛔ THE PARTICIPLE OF THE ROW'S OWN VERB, SUBJECT-GATED (#11258 review round
    # 3, MEASURED: `no longer fused` was GREEN because the arm carried only the
    # 3sg `fuses`, while `no longer judged` RED — the arm was reached and only
    # the inflection was missing). The second shape names the class in open words
    # rather than the anchor word, so it exercises the subject gate too.
    "the ADJACENCY half is no longer fused",
    "the units are no longer fused",
    # ⛔ ONE PARTICIPLE SHAPE PER GATE ALTERNATIVE (#11258 review round 3 `P2`,
    # MEASURED: only 2 of the gate's 7 alternatives were exercised by any
    # mutation, so an alternative could be deleted and its shape SUBSTITUTED for
    # one of the four membership-pinned ones — `len` and uniqueness both held and
    # the test stayed GREEN). These close the remaining five class-word
    # alternatives. ⛔ NOT EVERY alternative is pinned — measured by deleting one at
    # a time (#11258 review round 5 `P2`, MEASURED): 5 of the 6 class words lose a
    # RED — `time[- ]unit` does NOT, because its proof shape `the time-unit half is
    # no longer fused` is caught by the unit arm's `units?` matching inside the
    # token, so that alternative is pinned by nothing and its mutation pins nothing
    # exclusively — plus `units?`, `minute`, `km`, `kg`, `mi`, `mos`, `millisec`,
    # `m` in the unit arm. The remaining 27 spellings (including `second(s)`,
    # `hour(s)`, `min`, `ms`, `s`) do NOT lose a RED, so a tidying pass that
    # collapses `second|seconds` still gets no signal. Said here rather than
    # implied.
    "the half is no longer fused",
    "the carve-out is no longer fused",
    "the value class is no longer fused",
    "the numeric side is no longer fused",
    "the time-unit half is no longer fused",
    # ... and the unit VOCABULARY the row itself enumerates, which the first
    # revision of the gate missed entirely (`minute` is no longer fused was GREEN
    # while `` `minute` no longer fuses `` was RED — the same inflection asymmetry
    # this arm exists to close, still open for the ten spellings the row names).
    "`minute` is no longer fused",
    "`km` is no longer fused",
    # ⛔ THE REST OF THE ROW'S OWN UNIT FAMILY (#11258 review round 4 `P2`,
    # MEASURED: `km` was caught while its row-sibling `kg` was GREEN — the same
    # vocabulary asymmetry one spelling list narrower), and the PAST-TENSE
    # reopenings the present-copula-only revision let through.
    "`kg` is no longer fused",
    "`mi` is no longer fused",
    "`mos` is no longer fused",
    "`millisec` is no longer fused",
    "`m` is no longer fused",
    "the units were no longer fused in this revision",
    "`minute` was no longer fused after the widen",
    # ... and an interpolated `after the widen landed` inside the window, which
    # the 20-char bound read as GREEN on the unit side and RED on the class-word
    # side. ⛔ NOT "the row's own register" — `after the widen` occurs 0 times in
    # the registry (#11258 round 21, MEASURED); the shape is pinned here because
    # the language offers it, and that is the whole basis for the window.
    "`minute` is, after the widen landed, no longer fused",
    # ⛔ THE WAIVER'S OWN FALSIFIERS (#11258 review round 5 `P1`, MEASURED): the
    # unit arm was widened but the waiver's WITHHOLDING set was not, so prefixing
    # the shape with the row's own disclosure word made it invisible —
    # `` the SPELLING gap is open because `kg` is no longer fused `` was WAIVED
    # while the same shape with `minute` RED. These three run the waiver path.
    "the SPELLING gap is open because `kg` is no longer fused",
    "the SPELLING gap is open because `mi` is no longer fused",
    "the SPELLING gap is open because `mos` is no longer fused",
    # ⛔ AND THE FLOOR'S `no longer <participle>` ARM HAS ITS OWN FALSIFIER
    # (#11258 review round 9 `P2`, MEASURED): at `7b3bdd231d` replacing that
    # pattern with `(?!)` left the WHOLE FILE `102 passed`. ⚠️ AT `ff1ff27c74` IT
    # WAS ALREADY PINNED by the peer's `the SPELLING gap is no longer being
    # fused.`; these three cover `judged` and `retained` — endings that entry does
    # NOT reach — plus the filler-free twin of the `fused` it already exercises
    # (MEASURED: removing the `fused` alternative greens BOTH `… no longer being
    # fused.` and `… no longer fused.`, so that pair shares one alternative).
    # Each is FLOOR-EXCLUSIVE
    # for a different reason than an openness gap: `judged` and `retained` ARE in
    # the unanchored openness arm — the floor decides them because
    # `_residual_reopening_claims` runs ONLY `_SPELLING_FLOOR` when every clause
    # is an honest `spelling` disclosure and then `continue`s. (An earlier version
    # of this comment claimed "the unanchored openness arm requires a unit "
    # "SUBJECT"; measured, that arm matches both of these sentences — the
    # short-circuit, not the openness vocabulary, is what makes them floor-only.)
    # Verified by disarming the floor at runtime, one arm at a time.
    "the SPELLING gap is no longer fused.",
    "the SPELLING gap is no longer judged.",
    "the SPELLING gap is no longer retained.",
    # ⛔ THE `open again` ARM READ ONLY THE UNINFLECTED LEMMA (#11258 review round 16
    # `P0`, MEASURED). `\bopen\s+again\b` matched `is open again` and nothing else,
    # so every inflected spelling was GREEN — `opens`, `has opened`, `is opening`.
    # One character class wide, and NOT one of the disclosed residuals: the
    # disclosure names shapes phrased with NONE of this arm's words, while these
    # carry the arm's own word `open` (⛔ NOT "the row's own phrase" — round 17
    # `P2` corrected that claim: `grep -ci 'open[- ]again' config/v4-mechanisms.yml`
    # is **0**, the row writes `open`/`opens`/`fails open`/`open on purpose` and no
    # form of `open again`; the argument rests on the WORD, not on the row).
    # Fixed at both sites (`_RESIDUAL_OPENNESS` and `_SPELLING_FLOOR`, which is the
    # ONLY tuple the honest-`spelling` path runs), and pinned at BOTH — round 17
    # `P0` measured that reverting the second site alone left the whole file green.
    "the ADJACENCY gap opens again.",
    "the ADJACENCY gap has opened again.",
    "the ADJACENCY gap is opening again.",
    # ⛔ AND THE SAME THREE ON THE WAIVED PATH (#11258 review round 17 `P0`, MEASURED).
    # The three above are class-anchored, so they run `_RESIDUAL_OPENNESS`; a waived
    # sentence runs `_SPELLING_FLOOR` ONLY. Reverting the second site alone left the
    # WHOLE FILE green while `the SPELLING gap opens again.` went GREEN — the exact
    # "an arm is deletable while both tests pass" failure this proof list exists to
    # refuse, and the claim that the two sites were "pinned, one per inflection" was
    # false until these three existed.
    "the SPELLING gap opens again.",
    "the SPELLING gap has opened again.",
    "the SPELLING gap is opening again.",
    # ⛔ ROUND 17 `P2`: the participle in the negation veto must not swallow a
    # POLARITY FLIPPER. These are the round-2 regression's own shapes, one verb
    # over — `not only kept open` ASSERTS the gap is wide open, and the veto that
    # makes `not kept open` an honest closure must not reach it.
    "the ADJACENCY half is not only kept open.",
    "the ADJACENCY half is not merely held open.",
    "the ADJACENCY half isn't only kept open.",
    # ⛔ ONE FALSIFIER PER `_RESIDUAL_OPEN_VERBS` ALTERNATIVE (#11258 review round 23
    # `P1`, MEASURED at `6c3c18b828` by SOURCE-level mutation — delete the
    # alternative, re-import, re-scan): ELEVEN of the sixteen alternatives had NO
    # falsifier at all, so any one of them could be deleted with the whole file
    # GREEN. Only `is`/`remains`/`stays`/`keeps`/`kept` were pinned — five of
    # sixteen, NOT the seven an earlier cut of this comment claimed: `keep` and
    # `held` were named there as pinned and were not (round-23 re-review, MEASURED:
    # deleting either flips NO entry in this list). ⛔ EACH SHAPE BELOW IS MEASURED AS
    # A FALSIFIER, not merely as a RED: it REDs with the alternative present and
    # goes GREEN when that ONE alternative is removed from the tuple. A plain
    # `<verb> open` sentence is NOT usable here — `the ADJACENCY halves are open.`
    # stays RED with `are` deleted, because another arm catches it, so it would pin
    # nothing. These are the shapes only this arm reaches.
    "the carve-outs are still open.",
    "the carve-out was still open.",
    "the carve-outs were still open.",
    "the carve-outs remain open.",
    "the ADJACENCY half remained open.",
    "the carve-out may stay open.",
    "the carve-out stayed open.",
    "the ADJACENCY gap can hold open.",
    "the ADJACENCY gap holds open.",
    # ⛔ THE LAST TWO (#11258 round-23 RE-REVIEW `P1`, MEASURED): `keep` and `held`
    # were the two alternatives this round's first cut left unpinned while claiming
    # them pinned. `the ADJACENCY gap is held open.` above does NOT pin `held` —
    # `is` matches it with `held` as filler, so deleting `held` flips nothing.
    "the ADJACENCY half may keep the gap open.",
    "the ADJACENCY gap held open.",
    # ⛔ `whereas`, `though` AND `yet` ARE ALL LOAD-BEARING SPLITTER BRANCHES
    # (#11258 review round 23 `P1` + re-review, MEASURED by deleting each branch
    # from `_RESIDUAL_CLAUSE_SPLIT` and re-running): with the branch gone the whole
    # file stayed GREEN while these sentences flipped RED -> GREEN, i.e. the honest
    # `spelling` waiver swallowed the second claim again. ⛔ THE FIRST CUT OF THIS
    # COMMENT CLAIMED `yet` WAS NOT AN ALTERNATIVE — that was FALSE, and false
    # because the probe that "measured" it replaced the substring `|yet`, which
    # does not occur in the source (`yet` sits behind
    # `(?<![n'\u2019]t\s)(?<!\bnot\s)(?<!\bnever\s)`). A no-op edit was read as a
    # measurement. `yet` IS a branch of the same alternation; those lookbehinds
    # guard WHEN it may fire, they do not keep it out of the group.
    "the SPELLING gap is open whereas the remaining gap is not closed.",
    "the SPELLING gap is open though the remaining gap is not closed.",
    "the SPELLING gap is open yet the remaining gap is not closed.",
    # ⛔ `_RESIDUAL_CLASS_ANCHOR`'s `retention` / `the guarantee` / the SPACED
    # `time unit` had no falsifier either (same round, MEASURED): the fused arm's
    # own subject list does not reach these, and each sentence below goes GREEN the
    # moment its anchor member is deleted.
    "the retention rule has yet to be closed.",
    "the guarantee has yet to be closed.",
    "the time unit half has yet to be closed.",
    # ⛔ THE WAIVED `reopen` FAMILY (#11258 review round 24 `P1`, MEASURED): the
    # `reopen` arm was restored to `_SPELLING_FLOOR` because a WAIVED sentence runs
    # only that tuple, so these five were GREEN at the pre-fix head while
    # `origin/main`'s own `_SPELLING_SENTENCE_REOPENING` refused them.
    "the SPELLING gap is reopened.",
    "the SPELLING gap is re-opened.",
    "the SPELLING gap has been reopened by the widen.",
    "the SURVIVING SPELLING case is reopened.",
    # ⛔ NOT `the unit-spelling case reopens.` — MEASURED GREEN at the append point,
    # and it is a DISCLOSED residual of the restore rather than a fixture mistake:
    # a clause carrying `unit` is WITHHELD from the `spelling` waiver
    # (`_RESIDUAL_WAIVER_WITHHOLD`) and is not a class anchor, so neither
    # `_SPELLING_FLOOR` (the arm above) nor `_RESIDUAL_ANCHORED_OPENNESS` reads it,
    # and the unanchored arm cannot take `\bre-?open` without refusing the row's own
    # honest `…the owner's decision reopened there, not a rung bolted on here.`
    # (`_RESIDUAL_TRUTHFUL_STATEMENTS`). Same trade as every bounded arm in this
    # file, recorded here rather than left to be discovered.
    # ⛔ THE MIXED-CLAUSE FORM (#11258 review `P1`, MEASURED at `0f48bcd05db`): a
    # reopening written inside an honest `spelling` clause was REMOVED from `scan`
    # and then judged by NOTHING as soon as a second, truthful clause shared the
    # sentence — `_SPELLING_FLOOR`, the only tuple a waived clause is ever judged
    # by, ran only when EVERY clause was honest. All five shapes below were GREEN,
    # and THIRTEEN of the mutations above go GREEN the same way once
    # `, and the pair is fused.` is appended to them, so the list was non-vacuous
    # alone and vacuous under composition. `origin/main` ran the floor PER CLAUSE,
    # so this was a coverage REGRESSION rather than a widening, and each sentence
    # here is RED at the same insertion site under main's own
    # `_SPELLING_SENTENCE_REOPENING`. The second clause is the row's own `(d2)`
    # truth and carries no anchor, i.e. it is the framework of the must-stay-GREEN
    # `the SPELLING gap has never been closed, and the ADJACENCY half is closed by
    # the tables` below — an UNRELATED truthful clause decided the verdict, which
    # is the class round 18's `P1` was filed for.
    "the SPELLING gap is reopened, and the pair is fused.",
    "the SPELLING gap is unclosed, and the pair is fused.",
    "the SPELLING gap no longer fuses, and the pair is fused.",
    "the SPELLING gap is open again, and the pair is fused.",
    "the SPELLING gap opens again, and the ADJACENCY half is closed by the tables.",
)

#: Truthful statements that must stay GREEN. Each is a shape the row itself uses
#: or could use honestly: a closure in another tense, the SPELLING disclosure
#: (that class IS open), a negated reopening, and openness about some OTHER
#: subject — the four `re-open`/`reopened` phrases the real region already
#: carries, plus the row's own `fail-open` vocabulary.
_RESIDUAL_TRUTHFUL_STATEMENTS = (
    "the ADJACENCY half is now CLOSED.",
    "the ADJACENCY gap has been CLOSED.",
    "We have closed the ADJACENCY half.",
    "the ADJACENCY gap is closed by this table.",
    "the SPELLING case is still open on purpose.",
    "the SPELLING gap is not closed.",
    # round 16: the present-tense `not … close` arm's anchoring falsifier — honest
    # prose about the SPELLING class the anchor must not read (hypothetical: the
    # row writes no such sentence, measured).
    "the tables do not close the SPELLING gap.",
    # ⛔ THE WORKAROUND THE REFUSAL MESSAGE NAMES IS A PROOF, not prose: when a
    # lane's honest `not closed` sentence is refused, the message tells it to say
    # `is still open` / `remains open`, so both must stay GREEN or the advice
    # itself becomes a falsified measurement (#11258 review round 12 `P3`).
    "(b2) is still open.",
    "(e) remains open.",
    "(d2) does not reopen (d)'s ADJACENCY result — the unit word IS fused.",
    "an unknown unit still fails open on purpose.",
    # ⛔ THE DENIALS OF `fails open`, one per form in `_RESIDUAL_FAIL_OPEN`'s veto
    # run (#11258 review round 25 `P2`, MEASURED): every one of these was REFUSED at
    # `9e6839bc4636` — the arm had no negation veto at all — while `origin/main`
    # accepted all four (it had no `fails open` arm). A plain denial of this
    # predicate CLOSES the gap, so each must stay GREEN; `doesn't` (ASCII) is also
    # the `’` partner for the run's apostrophe class, re-measured in both spellings
    # by `test_every_apostrophe_shape_gives_the_same_verdict_in_EITHER_spelling`.
    "the ADJACENCY half no longer fails open.",
    "the ADJACENCY half never fails open.",
    "the ADJACENCY half does not fail open.",
    "the ADJACENCY half doesn't fail open.",
    "the numeric side no longer fails open.",
    # ⛔ HONEST DENIALS, and they are here because they were FALSE BLOCKS: each one
    # red the scan before the negation guards above (#11258 review `P2`, measured).
    # A false block is how a guard gets deleted, so the surface is executable
    # rather than argued — every entry below must stay GREEN.
    "the ADJACENCY half is not open.",
    "the ADJACENCY gap is not open again.",
    "the carve-out is not open to a second reading.",
    "the carve-out has never been reopened.",
    "the ADJACENCY half has not been re-opened.",
    "it doesn't reopen (d)'s ADJACENCY result.",
    # ⛔ ... AND THE SAME DENIALS ONE VERB OVER (`open again`). They were FALSE
    # BLOCKS at the previous head: the `open again` arm had two negation forms
    # where the opening verb had six (#11258 review round 2 `P2`, measured).
    "the ADJACENCY gap has not been open again since the tables landed.",
    "the ADJACENCY gap has never been open again.",
    "the ADJACENCY gap hasn't been open again.",
    "the ADJACENCY half is not entirely open.",
    "the ADJACENCY half is clearly not open.",
    "the ADJACENCY half has not yet been re-opened.",
    # ⛔ HONEST PROSE ABOUT THE ROW'S OTHER RESIDUALS. The row is a list of what is
    # still not enforced, so `(b2) is far from closed` is a disclosure, not a
    # reopening — these red when the closed-verdict forms are UNANCHORED (#11258
    # review round 5 `P1`, measured). The last two are the `spelling` waiver's cost
    # for `the remaining gap` / `the exception it carved`, which name the OPEN
    # residual and so must not anchor (#11258 review round 5 `P2`).
    "(b2) is far from closed at 49.4%.",
    "(e) has yet to be closed.",
    "residual (a) is never closed by any rule in this module.",
    "the remaining gap is the SPELLING case, which is open by design.",
    "the exception it carved is the SPELLING class, still open.",
    # ⛔ CLOSURE DOCUMENTATION: both halves are TRUE by the row's own text (the
    # adjacency half is CLOSED, the spelling half is not), and the sentence-level
    # closed-verdict pass red all four until it learned to recognise a verdict that
    # sits INSIDE the honest `spelling` disclosure (#11258 review round 6 `P2`).
    "the SPELLING gap has never been closed, and the ADJACENCY half is closed by the tables.",
    "the SPELLING gap was never closed (it is open by design), and the ADJACENCY half is closed.",
    "the SPELLING class is far from closed, and the ADJACENCY half is closed by this table.",
    "the SPELLING gap has yet to be closed, while the carve-out's ADJACENCY half is CLOSED.",
    # ⛔ ⚠️ AN HONEST CLAUSE THAT NAMES BOTH `spelling` AND THE CARVE-OUT IS NOT
    # HERE, because it is deliberately REFUSED: waiving it is what let one word
    # (`the SPELLING `) turn every reopening on the carve-out GREEN, so the two
    # sentences `the SPELLING carve-out is still open` / `the carve-out is still
    # open` disagreed with each other about a fact. Over-block is the direction
    # this artifact chooses — the shape is documented on `_RESIDUAL_CLASS_ANCHOR`
    # and in `_is_honest_spelling`, NOT asserted as passing here (#11258 review
    # round 3 `P1`, measured).
    "splitting it would re-open that false refusal on every two-group decimal.",
    "that needs the owner's decision reopened there, not a rung bolted on here.",
    # ⛔ THE GATE'S OWN FALSIFIERS (#11258 review round 3 `P2`, MEASURED): the
    # subject gate on the participle is justified by the claim "an UNGATED
    # `fused|fusing` false-blocks the row's own honest prose", and that claim had
    # no executable proof — the whole gate could be deleted and this test stayed
    # GREEN. These four are GREEN under the gated arm and RED under an ungated
    # one (both measured), so the must-stay-GREEN loop is now the gate's own
    # falsifier. The last two are HISTORICAL (`were` / `was`), which is what the
    # present-copula half of the gate exists to admit: a first revision gated
    # `units?` with no tense requirement and FALSE-BLOCKED both (measured).
    "`percent`/`pct` is no longer fused: fusing it REFUSED the truthful pair",
    # ⛔ THREE DELIBERATE OVER-BLOCKS, recorded rather than silently satisfied
    # (#11258 review round 4 `P2`, MEASURED): the past tense is admitted on BOTH
    # arms because leaving it out let a past-tense REOPENING through
    # (`the units were no longer fused in this revision` was GREEN — the dangerous
    # direction), and no tense rule can separate that from the honest historical
    # phrasing below without also admitting the reopening. Since the row writes
    # `no longer fused` NOWHERE (measured: `grep -c "no longer fus"` over the whole
    # registry is 0), these shapes are hypothetical — a lane that writes one in a
    # HISTORICAL sense will be asked to rephrase — so the over-block costs no real
    # disclosure and closes the leak:
    #   "`miles` was no longer fused at that revision"
    #   "the units were no longer fused at `origin/main` `525fde4a9`, …"
    #   "the unit word was no longer fused in the revision the widen replaced"
    # ⛔ AND `is no longer open` — the plainest closure English — is ACCEPTED, not
    # an over-block, since the veto took `no\s+longer` as a negator (#11258 review
    # round 7 `P2`). It is pinned here rather than merely claimed, and the three
    # emphatic `no longer` forms in `_RESIDUAL_REOPENING_MUTATIONS` are its
    # falsifier: `no longer merely/only/far from open` mean the OPPOSITE of closed.
    "the ADJACENCY half is no longer open.",
    # ⛔ ROUND 13 `P2`: the veto hard-coded `no\s+longer\s+`, so these two
    # HONEST closures were REFUSED while their window-free twin was accepted —
    # an unrecorded over-block, and the fourth `no longer` site not using the
    # shared primitive.
    "the ADJACENCY half is no longer, in practice, open.",
    "the ADJACENCY half is no longer being open.",
    "the ADJACENCY half is no longer an open question.",   # round 13: no longer refused
    # round 14: the negated twins of the pinned `… is kept open.` / `… is held
    # open.` reopenings — a true closure the arm used to refuse.
    "the ADJACENCY half is no longer kept open.",
    "the ADJACENCY half is no longer held open.",
    # \u26d4 THE TWO CONTRACTION SENTENCES (#11258 review round 14 `P2`, MEASURED):
    # each is an honest closure about a DIFFERENT subject that the unanchored
    # openness arm only reads when its lookbehind is dropped, so each is the
    # END-TO-END falsifier for one of the two `(?<!['\u2019])` guards. They are here
    # rather than only in `test_every_waiver_withhold_arm_earns_its_place` because
    # this list is the surface the append-the-row proof runs, and a guard pinned by
    # a proof shape is pinned against every future narrowing of the arms.
    "I'm confident the pair is no longer fused",
    "the pair's refusal is no longer fused",
    # round 16: the inflected `open again` arm must not swallow its own DENIALS.
    # These are the negation lookbehind run's falsifiers for the forms the fix
    # added — `not open again` / `not been opened again` are honest closures, and
    # the lookbehind family is a fixed-width enumeration, so each new inflection
    # needs its own measured partner or a later widening reds it silently.
    "the ADJACENCY half is not open again.",
    "the ADJACENCY half has not been opened again.",
    # ⛔ ROUND 17 `P1`: the widened `open(?:s|ed|ing)?` arm needs the negation RUN
    # widened with it, or these five honest closures are FALSE BLOCKS — they were
    # GREEN when the arm read only `open again` (no match) and RED the moment it
    # read the inflection. One partner per new negation form, because the run is a
    # fixed-width enumeration and a form without a partner drifts silently.
    "the ADJACENCY half no longer opens again.",
    "the ADJACENCY half is no longer opened again.",
    "the ADJACENCY half is no longer opening again.",
    "the ADJACENCY half hasn't yet opened again.",
    "the ADJACENCY half hasn't ever opened again.",
    # round 17 `P2`: the two `not kept open` / `not held open` costs REPAIRED and
    # moved here from `_RESIDUAL_REFUSED_TRUTHFUL` — the route that ledger's own
    # instruction prescribes when a cost stops being paid.
    "the ADJACENCY half is not kept open.",
    "the ADJACENCY gap is not held open.",
    "the ADJACENCY gap isn't kept open.",
    # ⛔ ROUND 18 `P0`: `(?<!\bnever )` was the ONE third of the round-17 verb guard
    # that no entry pinned — deleting exactly that term left the whole file green
    # while `the ADJACENCY half is never kept open.` went RED. Each term of a
    # lookbehind run needs its own partner, or the arm is deletable a term at a time.
    "the ADJACENCY half is never kept open.",
    # ⛔ ROUND 18 `P0`: the round-17 negation RUN is carried at TWO sites and only
    # one was falsifiable. These are the WAIVED-path partners — the only shape that
    # reaches `_SPELLING_FLOOR` — so deleting occurrence #2 of the run no longer
    # leaves the suite green.
    "the SPELLING gap no longer opens again.",
    "the SPELLING gap is no longer opened again.",
    "the SPELLING gap is no longer opening again.",
    "the SPELLING gap hasn't ever opened again.",
    # ⛔ ROUND 18 `P1`: the splitter no longer severs a negator from its participle,
    # so these two honest denials are GREEN — pinned because the bar is the only
    # thing that decides them, and the second is the shape whose verdict used to
    # depend on an unrelated honest clause elsewhere in the sentence.
    "the SPELLING gap hasn't yet opened again.",
    "the SPELLING gap is open by design, and the ADJACENCY half has not yet opened again.",
    # ⛔ ROUND 22 `P2`: THE SAME DENIALS SPELLED WITH THE TYPOGRAPHIC APOSTROPHE.
    # The arms accepted `n’t` and the negation RUNS did not, so every sentence here
    # was REFUSED while its ASCII twin above stayed GREEN and no ledger entry
    # re-measured the difference — an unrecorded over-block, and the shape macOS
    # supplies for free. One partner per run site, because the class is written at
    # six patterns in two independently-drifting copies: dropping `\u2019` from
    # any ONE of them reds one entry here.
    "the ADJACENCY gap hasn’t been open again.",
    "the ADJACENCY half isn’t yet opened again.",
    "the ADJACENCY half hasn’t ever opened again.",
    "the SPELLING gap hasn’t yet opened again.",
    "the ADJACENCY gap isn’t kept open.",
    "it doesn’t reopen (d)’s ADJACENCY result.",
    # ⛔ THE `never …` LOOKBEHINDS' MISSING PARTNERS (#11258 review round 23 `P2`,
    # MEASURED): the comment above the run asserts that "every form in this run has
    # a must-stay-GREEN partner" here, and that was FALSE for three of them —
    # `(?<!\bnever\s)`, `(?<!\bnever\sbefore\s)` and
    # `(?<!\bnever\sbefore\sbeen\s)` had none (deleting all three left the file
    # GREEN while the sentences below went RED). Adding the partners makes the
    # claim true; it was not true before them.
    "the carve-out never reopens.",
    "the carve-out has never before been reopened.",
    "the carve-out has never before reopened.",
    # ⛔ THE COORDINATOR GUARD'S FALSIFIER (#11258 review rounds 23 and 25 `P2`,
    # MEASURED): the `(?!\b(?:and|but)\s+…(?:is|are|was|were|fails?)\b)` guard
    # inside `_RESIDUAL_ANCHORED_OPENNESS` was deletable with the whole file
    # GREEN, so it needs a partner. ⛔ The partner MUST NOT be a reopening:
    # round 23 first pinned `the ADJACENCY half is unjudged and the pair is
    # unjudgeable and fails open`, which asserts the CLOSED gap is open — the row
    # at this head says *"the ADJACENCY half that used to stand here is CLOSED"*
    # and `_RESIDUAL_REOPENING_MUTATIONS` carries `the ADJACENCY half is no longer
    # judged.` as must-RED, so that entry required the scan to stay SILENT on a
    # reopening and told the next lane (in this list's own failure text) to treat
    # real coverage as a false block. Replaced with a shape that keeps the
    # falsifier and is actually truthful (round 25, MEASURED both ways).
    "the ADJACENCY half is closed and the pair fails open",
    # ⛔ AND THE `_SPELLING_FLOOR` COPY OF THE RUN NEEDS ITS OWN CURLY PARTNER
    # (#11258 review round 23 `P1`, MEASURED). Round 22 widened `’` at all six
    # patterns, but the only waived-path partners it added were for the `open again`
    # run and the `yet` bar — so reverting `’` at `_SPELLING_FLOOR`'s
    # `[n'’]t\sbeen` site left the file at `108 passed` while
    # `the SPELLING gap hasn’t been open again.` was REFUSED: an unrecorded
    # over-block, which this artifact says is never acceptable. This entry is that
    # site's falsifier — with it present, the revert reds.
    "the SPELLING gap hasn’t been open again.",
    # ⛔ AND THE `reopen` ARM'S HONEST DENIALS (#11258 review round 24 `P1`): the arm
    # restored to `_SPELLING_FLOOR` shares the same negation run, and these three
    # are what proves the run reaches THAT arm too — without them the arm could be
    # narrowed to `\bre-?open` with no lookbehind and nothing would red until an
    # honest waived denial was refused.
    "the SPELLING gap is not reopened.",
    "the SPELLING gap hasn't reopened.",
    "the SPELLING gap has never been reopened.",
)

#: ⛔ THE OVER-BLOCK LEDGER: truthful statements this pin REFUSES, each MEASURED
#: RED at the append point (#11258 review round 7 `P2`). This file's own rule is
#: that an over-block is the direction the artifact chooses — over fail-open — but
#: only if it is RECORDED rather than left to be discovered. So the cost is a
#: tuple and `test_the_residual_region_is_scanned_WHERE_a_lane_appends` re-measures
#: every entry, rather than a sentence in a comment that can drift.
#:
#: Each shape is honest, and each is refused for a reason that is NOT an oversight:
#: * `CLOSED, not open` / `judged, not open` — the `is … open` veto binds a
#:   negator DIRECTLY after the verb, and the comma puts a word in the slot. A
#:   veto wide enough to cross the comma is the veto that let the EMPHATIC
#:   reopenings through (`is not only open`, `is not merely open`, `is never far
#:   from open` — a measured coverage REGRESSION, #11258 review round 2 `P1`).
#: * `closed rather than open` — same slot, same trade.
#: * ⛔ `no longer an open question` WAS here and is now ACCEPTED (#11258 review
#:   round 13): the veto took a filler window, so a noun phrase between `no longer`
#:   and `open` no longer refuses an honest closure. It moved to
#:   `_RESIDUAL_TRUTHFUL_STATEMENTS` — the route this ledger's own comment
#:   prescribes when a cost stops being paid.
#: * `the SPELLING case for \`minute\`, which is open by design` — the unit token
#:   withholds the `spelling` waiver (that withholding is what stops one word
#:   `the SPELLING ` from excusing every reopening), so the honest sentence and
#:   its reopening twin are indistinguishable to a clause-scoped rule. This is
#:   the cost a lane is most likely to meet, because the row's surviving gap IS
#:   the unit-spelling class.
#: * `(b2) is not closed` / `(e) is not closed` — the `not … closed` arm is
#:   UNANCHORED on purpose, so it also refuses the honest statement that a
#:   DIFFERENT residual of this row is still not closed. The row is a list of what
#:   is still not enforced, and `(b2)` (`STRICT VERBATIM SURVIVAL stays SHORT of
#:   target`, 38/77 = 49.4%) and `(e)` (`the S5 minted-endpoint site is not judged
#:   against its own quote … a follow-up`) are items in it, so a lane writing an
#:   update about them meets this (#11258 review round 11 `P1`, MEASURED RED at
#:   the append point at `7b3bdd231d`). ⛔ THE WORKAROUND IS A DIFFERENT WORD, not
#:   a wider arm: `(b2) is still open.` / `(e) remains open.` are GREEN (measured).
#:   This is a CLASS, not two shapes — a third label meets it too, so ADD the
#:   sentence here rather than anchoring the arm. ⛔ WHAT ANCHORING ACTUALLY
#:   COSTS, because the first revision of this bullet cited the wrong sentence: it
#:   is NOT `the gap those tables closed is not closed` — that one matches
#:   `_RESIDUAL_CLASS_ANCHOR` and stays RED under the anchored arm (MEASURED both
#:   ways). The loss is the ANCHOR-FREE `the remaining gap is not closed` family,
#:   whose four exclusive proofs (`the remaining gap is not closed.` and its three
#:   `the SPELLING gap is open <but|while|although> the remaining gap is not
#:   closed` variants) go GREEN under the anchored arm — `the remaining gap` is
#:   deliberately NOT an anchor, and the reason is MEASURED rather than inferred:
#:   adding it reds the honest proof `the remaining gap is the SPELLING case,
#:   which is open by design` (GREEN at this head, RED with the phrase in the
#:   anchor). ⛔ AND NOT because the row says that phrase — `rg -c 'the remaining
#:   gap' config/v4-mechanisms.yml` is 0 (MEASURED); the row's wording is "What
#:   SURVIVES of the old (d) is the SPELLING case". Re-measure this set before
#:   quoting it again: a comment that names the wrong reason is how the next lane
#:   concludes the arm can be anchored.
#: * `the ADJACENCY half is no longer open, and the pair is fused.` — the
#:   UNANCHORED `no longer … (closed|fuses|judged|…)` arm's 25-char `[^.;]` window
#:   crosses a comma+coordinator, so `no longer OPEN, and the pair is FUSED` is
#:   read as `no longer … fused`. Both clauses are true (the gap is closed; the
#:   pair IS fused, the row's own `(d2)`). MEASURED, not repaired: the
#:   coordinator bound that `_RESIDUAL_ANCHORED_OPENNESS` already carries neither
#:   un-refuses this sentence nor keeps `the pair is no longer fused, and the
#:   tables are stale.` RED — it loses that catch. Recorded, per the rule below.
#:
#: ⛔ WHEN ONE OF THESE STOPS BEING REFUSED, THAT IS AN IMPROVEMENT — move it into
#: `_RESIDUAL_TRUTHFUL_STATEMENTS` in the same commit. Do NOT delete the test's
#: assertion to silence it: a ledger that cannot red is a comment.
_RESIDUAL_REFUSED_TRUTHFUL = (
    "the ADJACENCY half is CLOSED, not open.",
    "the ADJACENCY half is judged, not open.",
    "the ADJACENCY half is closed rather than open.",
    "the remaining gap is the SPELLING case for `minute`, which is open by design.",
    # the unanchored `not … closed` arm's over-block, on THIS row's own items
    "(b2) is not closed.",
    "(e) is not closed.",
    # ⛔ AN HONEST ASSERTION OF CLOSURE, refused (#11258 review round 15 `P2`,
    # MEASURED). The unanchored `not … closed` arm exists because `not closed`
    # normally ASSERTS the gap is open; `not merely closed` asserts the opposite,
    # and the arm cannot tell them apart without the parser this artifact does not
    # have. Recorded rather than left to be discovered — the rule this ledger
    # exists for — and it is the reason the arm is not widened to `not … fused`.
    "the ADJACENCY half is not merely closed, it is fused.",
    "the ADJACENCY half is not just closed but fused.",
    "the ADJACENCY half is not closed again since the tables landed.",
    # ⛔ THE PARTICIPLE SLOT SAID "NOT FIXED" AND THAT WAS WRONG (#11258 review
    # round 17 `P2`, MEASURED — this is the correction). Round 16 recorded
    # `… is not kept open.` / `… is not held open.` here as an unfixable cost on
    # the grounds that "the veto sits after the verb and the match arrives through
    # the participle". The DIAGNOSIS was right and the CONCLUSION was not: each
    # half alone is inert, and the two together fix both denials — a
    # `not`/`never` lookbehind in front of the VERB group (so the participle
    # cannot BE the verb when it is negated) plus the participle in the veto slot.
    # Both now live in `_RESIDUAL_TRUTHFUL_STATEMENTS`, which is the route this
    # ledger's own instruction prescribes when a cost stops being paid.
    # ⛔ `, so ` IS NOT A CLAUSE SPLIT (#11258 review round 16 `P1`, MEASURED). The
    # sentence names BOTH classes and closes the first, but `_RESIDUAL_CLAUSE_SPLIT`
    # does not break on `, so `, so the anchored `stays open` arm judges the union
    # and reds it. Adding `, so ` to the splitter is a widening, and a widening here
    # is what round 1's `P2` was: an honest closure that only LOOKS honest because
    # the clause it removes is the one carrying the anchor.
    "the ADJACENCY half is closed by the tables, so the SPELLING gap is what stays open.",
    # ⛔ THE `fails open` OVER-BLOCK, which was recorded in a COMMENT ONLY (#11258
    # review round 19 `P3`, MEASURED): the `_RESIDUAL_FAIL_OPEN` note states the cost
    # "recorded here rather than left to be discovered", and both sentences red at
    # the append point while appearing in NO ledger entry — prose the suite never
    # re-measures is the failure this ledger exists to prevent, one layer in. These
    # are the two shapes that note names, moved here so a change to the arm shows up
    # as a ledger failure (`now_accepted` in
    # `test_the_residual_region_is_scanned_WHERE_a_lane_appends`).
    "the numeric side fails open on an unknown unit",
    "the numeric side still fails open on purpose",
    # ⛔ THE SAME BOUNDED-LOOKBEHIND COST, MET BY THE NEW `fails open` VETO (#11258
    # review round 25 `P2`, MEASURED RED at the append point): the run admits a
    # negator only IMMEDIATELY before the verb, so an adverb between them falls
    # outside every fixed-width form and the sentence is refused although it is an
    # honest DENIAL. Recorded rather than left to be discovered — the rule this
    # ledger exists for — because the refusal message names this ledger as the
    # route out, so an entry must exist for that route to be true. ⛔ DO NOT "FIX" IT
    # BY WIDENING THE RUN: a variable-width `not … fail` lookbehind is impossible in
    # `re`, and an adverb allowlist is the closed-list trade rounds 2/9/10 measured
    # against.
    "the ADJACENCY half does not actually fail open.",
    # ⛔ THE BOUNDED-LOOKBEHIND OVER-BLOCK, recorded here rather than in the run's
    # own note (#11258 review round 20 `P3`, MEASURED RED at the append point):
    # these are honest DENIALS of a reopening — the direction the run exists to
    # accept — and each is refused because its adverb (or a parenthetical comma)
    # sits between `not` and `been`, which a FIXED-WIDTH lookbehind cannot cross.
    # `, in practice, ` is ORDINARY English, and the row does not write it today
    # (`in practice` occurs 0 times in `config/v4-mechanisms.yml`, #11258 round 21,
    # MEASURED) — so this cost is met by a lane EDITING the row, not by its current
    # text. ⛔ The round-20 finding's lede inherited the false "row's own register"
    # claim from the note above; the finding itself stands, because the refusal
    # message names this ledger as the route out, so the entry must exist for that
    # route to be true.
    # ⛔ DO NOT "FIX" THESE BY WIDENING THE RUN: a variable-width `not … been`
    # lookbehind is impossible in `re`, and the adverb-list alternative is the
    # closed-list trade rounds 2/9/10 measured against (a dropped adverb re-opens
    # the reopening). The over-block is the direction this artifact chooses; it is
    # paid openly, per entry, at zero slack.
    "the ADJACENCY half has not to date been reopened.",
    "the ADJACENCY half has not, in practice, been reopened.",
    "the ADJACENCY half has not yet, in practice, been re-opened.",
    # ⛔ THE `carve-out` + `spelling` OVER-BLOCK, which was recorded in a COMMENT
    # only (#11258 review `P3`, MEASURED RED at the append point): `_RESIDUAL_
    # CLASS_ANCHOR`'s note accepts the cost — an honest clause naming BOTH
    # `spelling` and the carve-out is refused — and the ledger's own rule is that an
    # over-block is acceptable "only if it is RECORDED rather than left to be
    # discovered". Nothing re-measured it, while the refusal it triggers says "THE
    # ROUTE OUT IS THE OVER-BLOCK LEDGER, ALWAYS" and pointed at a ledger with no
    # matching entry — the defect rounds 19/20/25 removed for other arms on that
    # same stated ground. The sentence is the row's OWN truthful claim ("What
    # SURVIVES of the old (d) is the SPELLING case"). ⛔ The fix is NOT to add
    # `spelling`+`carve-out` to the waiver's withhold set: that set is what keeps an
    # appended reopening from hiding behind the word `spelling`, so widening the
    # waiver is the round-1 `P2` direction.
    "what survives of the carve-out is the SPELLING case, which is open by design.",
    # ⛔ THE COMPOSED CLOSURE + `(d2)` TRUTH — measured while re-verifying the
    # round-26 fix at this head, and recorded rather than left to be discovered.
    # The UNANCHORED `no longer … (closed|fuses|judged|caught|compared|enforced|
    # retained)` arm takes a 25-char `[^.;]` window, so it crosses a
    # comma+coordinator and reads `no longer OPEN, and the pair is FUSED` as
    # `no longer … fused` — a claim the sentence does not make. BOTH clauses are
    # true: the ADJACENCY gap is no longer open (the row says it is CLOSED) and
    # the pair IS fused (the row's own `(d2)`), and the second clause alone is a
    # `_RESIDUAL_TRUTHFUL_STATEMENTS` entry, so an unrelated truthful clause is
    # deciding the verdict again — the class round 26's `P1` was filed for, in the
    # over-block direction instead of the fail-open one.
    # ⛔ NOT REPAIRED, and the reason is MEASURED, not assumed: bounding that
    # window with the same coordinator bound `_RESIDUAL_ANCHORED_OPENNESS` already
    # carries does NOT un-refuse this sentence (measured still RED) and DOES lose
    # a real catch — `the pair is no longer fused, and the tables are stale.` goes
    # RED → GREEN under it. A window that reaches the target verb only before the
    # coordinator is the trade this artifact refuses; the over-block is the
    # direction it chooses, so it is paid openly here, per entry, at zero slack.
    "the ADJACENCY half is no longer open, and the pair is fused.",
)


def _with_appended(text: str, sentence: str) -> str:
    """`text` with `sentence` appended at the END OF THE RESIDUAL LIST.

    That is the append point the issue is about: the shape is inserted where the
    region ends (just before the row's own terminator), NOT inside the `(d)`
    block, so a guard that stops reading at `(e) the` cannot see it.
    """
    assert text.count(_RESIDUAL_ROW_END) == 1, (
        f"the row that FOLLOWS the residual list ({_RESIDUAL_ROW_END!r}) is not "
        "in this registry exactly once, so the append point cannot be fixed "
        "independently of `_residual_region` — update this anchor deliberately, "
        "do NOT derive it from the function under test")
    at = text.index(_RESIDUAL_ROW_END)
    return text[:at] + "\n      " + sentence + text[at:]


def _reds(text: str) -> bool:
    """Does the PIN refuse this row text? — asked of the pin itself.

    ⛔ THIS ASKS `_assert_the_row_names_its_closure`, NOT `_residual_reopening_
    claims`, and the difference is the whole point (#11258 review round 7 `P2`,
    reproduced): with the proof list calling the HELPER, deleting the two wiring
    lines that connect the helper to the pin left this test GREEN **and** the pin
    GREEN over a real reopening. Driving the pin's own callable means the wiring
    cannot be removed without reddening the proof — the assertion a lane deletes
    is the one this test measures.
    """
    try:
        _assert_the_row_names_its_closure(text)
    except AssertionError:
        return True
    return False


def test_every_waiver_withhold_arm_earns_its_place():
    r"""Each guard the WAIVER carries is pinned by an assertion only IT can satisfy.

    ⛔ WHY THIS TEST EXISTS (#11258 review round 9 `P2`, MEASURED): at
    `7b3bdd231d` SIX guards in this family could be DELETED with the whole file
    still `102 passed` — the two subsumed unit spellings (`unit\s+word` and
    `millisec`), the withhold set's contraction lookbehind, `_RESIDUAL_OPENNESS`'s
    `m` lookbehind, its `s` lookbehind, and `_SPELLING_FLOOR`'s `no longer …` arm.
    ⚠️ Five of them still held at `ff1ff27c74`, and the FLOOR's no longer did
    (the peer's `the SPELLING gap is no longer being fused.` mutation already
    reds it there) — so the floor is listed for the head the finding was measured
    at, not as a claim about every head since.
    For the TWO SUBSUMED SPELLINGS and the WITHHOLD lookbehind, an END-TO-END
    verdict cannot pin them: the verdict is an OR over several arms (and a deleted
    withhold guard only WIDENS the set), so no proof shape's verdict flips. So those
    are pinned at the CONTRACT THEY OWN — `_is_honest_spelling` (does it withhold?)
    — where the disarmed guard is the only thing deciding the assertion.
    ⛔ THE TWO BARE-TOKEN LOOKBEHINDS ARE DIFFERENT, and an earlier version of
    this very paragraph got it wrong (#11258 review round 14 `P2`, MEASURED): they
    CAN be pinned end-to-end, because deleting them only ever ADDS a match, so an
    honest sentence flips GREEN→RED — no sibling can cover for them. Both are
    pinned at BOTH levels: by the assertions below, and by their honest sentences
    in `_RESIDUAL_TRUTHFUL_STATEMENTS`, so either deletion reds one of them.
    The floor's arm is pinned end-to-end too, by the floor-exclusive entries in
    `_RESIDUAL_REOPENING_MUTATIONS`.
    """
    # 1. THE UNIT SPELLINGS. The waiver must WITHHOLD on every spelling the unit
    #    arm reads. ⛔ TWO DECLARATIONS, AND BOTH DIRECTIONS ARE MEASURED: the
    #    REQUIRED spellings are named independently of the arms (so deleting an
    #    arm cannot delete the spelling that catches it — dropping `kg` from a
    #    derived-only list left all `103 passed`, MEASURED), and the samples are
    #    ALSO derived from the supplied code so an arm that is declared but not
    #    actually armed reds here (#11258 review round 9 `P2`, MEASURED: adding
    #    `fortnights?` to a hand-written sample list left `103 passed`).
    assert _RESIDUAL_UNIT_WAIVER_ARMS, "the withhold set carries no unit spelling"
    # ⛔ AND THE REQUIRED VOCABULARY IS PINNED BY MEMBERSHIP, not just read: it is
    # the list that makes deleting an arm visible, so deleting its `kg` entry in
    # the same edit restores the original fail-open. MEASURED (#11258 review round
    # 13 `P2`): `kg` removed from BOTH the arms and this list leaves `103 passed`,
    # and the waiver then swallows `the spelling gap is open because `kg` is not
    # closed` — the round-5 `P1` this list exists to close. Same idiom as the
    # proof-list membership loop below: a deleted entry fails to match a literal.
    assert set(_RESIDUAL_WAIVER_REQUIRED_SPELLINGS) >= {
        "units", "unit", "kg", "mi", "mos", "millisec", "millisecs"}, (
        "the required unit-spelling vocabulary SHRANK — every entry here is a "
        "spelling the unit arm reads and the class anchor does not name, so "
        "dropping one (with its arm) re-opens the fail-open that let a lane "
        "defeat the gate with a single word")
    samples = (*_RESIDUAL_WAIVER_REQUIRED_SPELLINGS,
               *[a.replace("?", "") for a in _RESIDUAL_UNIT_WAIVER_ARMS])
    waived = [s for s in samples
              if _is_honest_spelling(
                  f"the spelling gap is open because {s} is no longer fused")]
    assert waived == [], (
        "these unit spellings were WAIVED by the honest-`spelling` waiver, so a "
        "reopening that names the class by its own unit spelling passes the "
        f"gate: {waived}")
    # 2. THE CONTRACTION LOOKBEHINDS, at the contract each one owns. ⛔ TWO
    #    ASSERTIONS, ONE PER ALTERNATIVE: a single sentence supplying only `m`
    #    leaves the `s` guard unpinned (#11258 review round 9 `P2`, MEASURED:
    #    removing only the `s` lookbehind kept all `103 passed`).
    contraction = "the spelling list is unchanged; I'm confident it stays so"
    assert _is_honest_spelling(contraction), (
        "the waiver WITHHELD on a contraction — `\\b` treats `'` as a boundary, so "
        "the bare `m`/`s` arms need their `(?<!['’])` guards or the artifact's own "
        f"vocabulary is false-blocked: {contraction!r}")
    for opened in ("I'm confident the pair is no longer fused",
                   "the pair's refusal is no longer fused"):
        matched = [p for p in _RESIDUAL_OPENNESS if re.search(p, opened, re.I)]
        assert matched == [], (
            "the unanchored openness arm read a CONTRACTION as a unit token — "
            "`I'm` supplies the `m` and `pair's` supplies the `s`, so a sentence "
            f"about a DIFFERENT subject is refused (false block): {opened!r} "
            f"matched {matched}")
    # 3. THE BARE `m`/`s` ALTERNATIVE ITSELF, at the contract it owns. ⛔ THE
    #    CONTRACTION ASSERTIONS ABOVE PIN THE LOOKBEHIND, NOT THE ALTERNATIVE:
    #    they assert a contraction is WAIVED, which stays true when the whole
    #    bare-token alternative is disarmed (nothing matches, so the waiver is
    #    granted either way). MEASURED at `83e5613cf`: replacing
    #    `(?<!['’])\b[ms]\b` with a never-matching group left `103 passed` — so
    #    the row's own `s`/`m` unit abbreviations could stop withholding the
    #    waiver with no test noticing, and `the spelling gap is open and s is not
    #    closed` was then WAIVED (a reopening through the honest path, the
    #    round-5 `P1` one spelling over). Each token is asserted on its own: one
    #    sentence carrying `s` leaves `m` unpinned, the same way a single
    #    contraction left the second lookbehind unpinned (#11258 review round 9).
    for bare in ("s", "m"):
        bare_sentence = f"the spelling gap is open and {bare} is not closed"
        assert not _is_honest_spelling(bare_sentence), (
            "the waiver was GRANTED on a sentence carrying the row's own bare "
            f"`{bare}` unit abbreviation — the `[ms]` alternative no longer "
            "withholds, so a reopening written next to one letter passes the "
            f"gate: {bare_sentence!r}")


def test_the_residual_region_is_scanned_WHERE_a_lane_appends():
    """#11223: the pin's own proof list, executable — every mutation must RED.

    ⛔ NO COUNTS IN THIS DOCSTRING ON PURPOSE. Three revisions carried a count here
    and every one of them drifted the moment the next fix added a case (39/10 →
    43/16 → "fixed" → 47/22 in the very commit that re-created the drift,
    #11258 review round 3 `P2`). The two tuples ARE the count — the assertions
    below iterate them, so a stale number here could only ever mislead a reader.

    The pin above asserted a scan whose bounds were the `(d)` block; measured on
    the row at `af864a42a` that block is 10,019 chars and does NOT contain the
    row's own `(d2)` clause, so every mutation below appended after it stayed
    GREEN. This test is what makes "the scan is region-wide" a checked claim: it
    appends each shape where a lane appends and requires the scan to find it.
    """
    # ⛔ THE PROOF LISTS ARE PINNED, and by SHAPE rather than by size alone
    # (#11258 review round 3, MEASURED: gutting `_RESIDUAL_REOPENING_MUTATIONS` to
    # its first two entries left this test GREEN while both docstrings still
    # claimed the full count — a proof list that can be deleted without reddening
    # anything is the drift `[pin-defensive-claim]` names).
    # ⛔ AN EQUALITY-ONLY SIZE PIN IS NOT ENOUGH EITHER (round-2 reviewer, MEASURED:
    # reverting the participle and duplicating two existing entries keeps
    # `len == N` and stays GREEN — and this branch's lists are still GROWING, so an
    # equality pin also drops one side's proofs in a merge conflict). So: pin
    # UNIQUENESS rejects duplicate padding ONLY; MEMBERSHIP is the only thing
    # that blocks SUBSTITUTION, and it pins the shape each arm exists for.
    # shape each arm exists for, and use a `>=` FLOOR for size.
    assert len(set(_RESIDUAL_REOPENING_MUTATIONS)) == len(
        _RESIDUAL_REOPENING_MUTATIONS), (
        "the reopening proof list has duplicate entries — a duplicate pays for a "
        "deleted shape while the length stays constant, so the list can be "
        "gutted without reddening anything")
    assert len(set(_RESIDUAL_TRUTHFUL_STATEMENTS)) == len(
        _RESIDUAL_TRUTHFUL_STATEMENTS), (
        "the truthful-statement proof list has duplicate entries — see above")
    assert len(_RESIDUAL_REOPENING_MUTATIONS) >= 162, (
        "the reopening proof list SHRANK below its floor — re-derive the count "
        "in the docstrings/comments rather than deleting shapes. ⚠️ THE FLOOR IS "
        "THE CURRENT LENGTH (162 — 157 plus FIVE for the mixed `spelling`+truthful "
        "clause form the floor used to skip (#11258 review `P1`, MEASURED: thirteen "
        "of these mutations went GREEN under it); before that, "
        "156 raised by round 24's four falsifiers for the "
        "restored waived `reopen` arm, plus ONE this round adds for the "
        "`not only fails open` shape that pins the NARROWNESS of "
        "`_RESIDUAL_FAIL_OPEN`'s new negation veto (#11258 review round 25 `P2`); "
        "before that, 149 raised by round 23's fourteen falsifiers: one "
        "per `_RESIDUAL_OPEN_VERBS` alternative that had none, `whereas`/`though`, "
        "and the three `_RESIDUAL_CLASS_ANCHOR` members; plus four this round adds "
        "for the restored waived `reopen` arm. A FIFTH shape,"
        " `the unit-spelling case reopens.`, was measured GREEN and is recorded at "
        "that arm as a disclosed residual rather than pinned; plus three from the "
        "round-23 re-review, which found `keep` and `held` still unpinned and the "
        "`yet` splitter branch — the first cut of that round had CLAIMED those "
        "pinned, and its `yet` note was false), i.e. ZERO SLACK: at "
        "`>= 81` against an 88-entry "
        "list the slack was SEVEN mutations, which is more than the SIX exclusive "
        "proofs of the `no longer …` arm — so that pattern and all six of its "
        "proofs could be deleted with both tests GREEN, and "
        "`the ADJACENCY half is no longer closed.` then measured GREEN (#11258 "
        "review round 9 `P1`, measured). Earlier the same slack at 49 permitted "
        "twenty-one deletions while `_RESIDUAL_CLOSED_VERDICT` is three patterns. "
        "A `>=` at the current length costs nothing on growth and refuses any "
        "shrinkage; raise it in the same commit that adds proofs.")
    assert len(_RESIDUAL_TRUTHFUL_STATEMENTS) >= 79, (
        "the truthful-statement proof list SHRANK below its floor — a false-block "
        "list that can be deleted silently is not coverage. ⚠️ THE FLOOR IS THE "
        "CURRENT LENGTH (79 — 74 raised by round 24's four partners for the "
        "restored `reopen` arm, plus FIVE this round adds for `_RESIDUAL_FAIL_OPEN`'s "
        "negation run, one per form + its `’` partner (#11258 review round 25 `P2`); "
        "before that, 70 raised by round 23's four partners: the three "
        "`never …` lookbehind forms that had none, and the coordinator guard's "
        "honest shape, plus FOUR round 24 added: `_SPELLING_FLOOR`'s curly "
        "partner and the restored `reopen` arm's three honest denials. Before "
        "round 23 it was 66 — raised in the commit AFTER the two "
        "contraction "
        "falsifiers landed, which is the 2-entry slack this closed; then raised "
        "again with round 22's six typographic-apostrophe partners), i.e. ZERO "
        "SLACK, the same rule the ledger and the "
        "mutation list carry: it was `>= 30` against a 33-entry list, so three "
        "honest closures were deletable with both tests GREEN (the stale "
        "\"within two of the current length\" note is why it read as tight; "
        "#11258 review rounds 11-12, MEASURED)")
    # ⛔ AND THE OVER-BLOCK LEDGER IS PINNED BY SHAPE TOO (#11258 review round 8
    # `P2`, MEASURED): it had a fail-closed re-measurement (`now_accepted == []`)
    # and nothing else, so substituting entry 5 with a DUPLICATE of entry 1 left
    # five entries, all still refused, and the proof GREEN — the `minute` cost, the
    # one a lane is most likely to meet, silently dropped from the ledger. Same
    # defect the two lists above already carry a fix for; the ledger needed it too.
    assert len(set(_RESIDUAL_REFUSED_TRUTHFUL)) == len(
        _RESIDUAL_REFUSED_TRUTHFUL), (
        "the over-block ledger has duplicate entries — a duplicate pays for a "
        "dropped cost while the count stays constant")
    assert len(_RESIDUAL_REFUSED_TRUTHFUL) >= 18, (
        "the over-block ledger SHRANK below its floor — each entry is a measured "
        "cost of a rule that cannot be widened without a coverage regression; "
        "re-derive the ledger rather than deleting shapes. ⚠️ THE FLOOR IS THE "
        "CURRENT LENGTH (18 — 17 plus the composed closure + `(d2)` truth the "
        "unanchored `no longer …` window reads across a comma+coordinator, "
        "measured while re-verifying round 26, and the ONE repair attempt for it "
        "was measured to cost a real catch instead — see the entry's own note; "
        "before that, 16 plus the `carve-out` + `spelling` cost that was "
        "recorded in a COMMENT only until #11258 review `P3`; before that, 15 plus "
        "the bounded-lookbehind cost of the "
        "new `fails open` negation veto, #11258 review round 25 `P2`), i.e. ZERO "
        "SLACK: the round-11 entries are the "
        "`not … closed` cost, round 19 added the two `fails open` costs, round 20 "
        "added the three bounded-lookbehind denials, and slack above them is "
        "slack to delete one of those with")
    for required in (
            # the unit-token waiver cost — the one a lane actually meets, because
            # the row's surviving gap IS the unit-spelling class
            "the remaining gap is the SPELLING case for `minute`, which is open by design.",
            # the comma forms: the veto slot cannot cross a comma
            "the ADJACENCY half is CLOSED, not open.",
            "the ADJACENCY half is judged, not open.",
            # ⛔ AND THE OTHER TWO (#11258 review round 9 `P2`, MEASURED): pinning
            # three of five left the remaining two SUBSTITUTABLE — replacing either
            # with a duplicate of a pinned one kept uniqueness, length and all three
            # membership strings, and the proof stayed GREEN. A ledger where a cost
            # can be swapped out silently is the same defect one entry over.
            "the ADJACENCY half is closed rather than open.",
            # ⛔ AND THE UNANCHORED `not … closed` COST (#11258 review round 11
            # `P1`): without membership both entries are substitutable by
            # duplicates of the ones above — uniqueness and length survive and the
            # measured class is gone from the ledger, the same defect round 9
            # closed one entry over.
            "(b2) is not closed.",
            "(e) is not closed.",
            # ⛔ AND THE FOUR THAT WERE STILL SUBSTITUTABLE (#11258 review round 22
            # `P1`, MEASURED). Rounds 8 and 11 pinned the three comma forms and the
            # two `(x) is not closed` entries; these four were left with no
            # membership, so substituting
            # `the ADJACENCY half is not merely closed, it is fused.` with a
            # DIFFERENT, equally-refused sentence kept uniqueness, the 15-entry
            # floor, every membership string already present AND the whole file at
            # `106 passed` — while the round-15 `P2` cost (the honest assertion of
            # closure the unanchored `not … closed` arm cannot tell from a
            # reopening) left the ledger unrecorded. Same defect rounds 8 and 11
            # closed one entry over, and the rule this file already states:
            # membership is the only thing that blocks SUBSTITUTION.
            "the ADJACENCY half is not merely closed, it is fused.",
            "the ADJACENCY half is not just closed but fused.",
            # ⛔ this one also pins the `not … closed AGAIN` shape, which is the
            # only membership entry for it.
            "the ADJACENCY half is not closed again since the tables landed.",
            # ⛔ AND THIS ONE IS THE ONLY MEMBERSHIP PIN ON THE CLAUSE SPLITTER:
            # `_RESIDUAL_CLAUSE_SPLIT` does not break on `, so `, so the sentence is
            # refused as a UNION. Adding `, so ` to the splitter is what turns it
            # GREEN, and that widening is what round 16's `P1` refused.
            "the ADJACENCY half is closed by the tables, so the SPELLING gap is what stays open.",
            # ⛔ AND THE `fails open` PAIR (#11258 review round 19 `P3`): without
            # membership both are substitutable by duplicates of the entries above
            # — uniqueness and length survive and the measured class leaves the
            # ledger, the same defect round 9 closed one entry over. They are also
            # the ONLY entries that pin `_RESIDUAL_FAIL_OPEN`'s anchor requirement.
            "the numeric side fails open on an unknown unit",
            "the numeric side still fails open on purpose",
            # ⛔ AND THE BOUNDED-LOOKBEHIND TRIO (#11258 review round 20 `P3`):
            # without membership each is substitutable by a duplicate of an entry
            # above — uniqueness and length survive and the measured class leaves
            # the ledger, the same defect round 9 closed one entry over. They are
            # also the ONLY entries that pin the fixed-width limit of the
            # negation run, so deleting the note that explains it now reds here.
            "the ADJACENCY half has not to date been reopened.",
            "the ADJACENCY half has not, in practice, been reopened.",
            "the ADJACENCY half has not yet, in practice, been re-opened.",
            # ⛔ AND THE `fails open` VETO'S OWN BOUNDED-LOOKBEHIND COST (#11258
            # review round 25 `P2`): without membership it is substitutable by a
            # duplicate of an entry above — uniqueness and length survive and the
            # measured cost leaves the ledger, the same defect round 9 closed one
            # entry over.
            "the ADJACENCY half does not actually fail open.",
            # ⛔ AND THE `carve-out` + `spelling` COST (#11258 review `P3`): without
            # membership it is substitutable by a duplicate of an entry above —
            # uniqueness and length survive and the measured cost leaves the ledger,
            # the same defect round 9 closed one entry over.
            "what survives of the carve-out is the SPELLING case, which is open by design.",
            # ⛔ AND THE COMPOSED CLOSURE + `(d2)` COST: without membership it is
            # substitutable by a duplicate of an entry above — uniqueness and
            # length survive and the measured cost leaves the ledger, the same
            # defect round 9 closed one entry over. It is also the ONLY entry that
            # pins the 25-char window's cross-coordinator reach, so narrowing that
            # window reds here.
            "the ADJACENCY half is no longer open, and the pair is fused.",):
        assert required in _RESIDUAL_REFUSED_TRUTHFUL, (
            f"the over-block ledger no longer carries {required!r} — that shape's "
            "refusal is the measured price of a specific rule; if it is no longer "
            "refused, move it into `_RESIDUAL_TRUTHFUL_STATEMENTS` instead of "
            "dropping it silently")
    for required in (
            # the issue's own mutation, and the append-point arm
            "the ADJACENCY half is open again",
            # the `no longer` participle, subject-gated (#11258 round 3)
            "the ADJACENCY half is no longer fused",
            # the clause-anchored `fails open` arm
            "the ADJACENCY half fails open.",
            # ⛔ AND ITS EMPHATIC FORM (#11258 review round 25 `P2`): `not only
            # fails open` ASSERTS the predicate, so it is the falsifier for the
            # veto's narrowness — a veto widened into a WINDOW scan would turn this
            # GREEN and leave the arm reading denials as claims.
            "the ADJACENCY half not only fails open, it leaks.",
            # the emphatic negations the veto must NOT swallow
            "the ADJACENCY half is not only open, it is unfused.",
            # ⛔ THE CLOSED-VERDICT ARM (round 4/5, #11258 round-6 review `P1`
            # MEASURED): with the floor's old slack this arm had NO membership
            # entry — emptying `_RESIDUAL_CLOSED_VERDICT` AND deleting its TWO
            # exclusively-covered shapes left BOTH tests GREEN, because the floor
            # permitted 21 deletions and the arm is three patterns (two of them
            # exclusively covered).
            "the ADJACENCY gap was never closed.",
            "the time-unit half has yet to be closed",
            "the ADJACENCY half is anything but closed.",
            # ⛔ THE CONTRACTED AUXILIARY (#11258 review round 8 `P2`, MEASURED):
            # `isn't` was the ONLY contraction the arm read, so every other one was
            # GREEN while its uncontracted twin RED. This entry keeps the family
            # shape (not just the `is` member) from being narrowed back.
            "the ADJACENCY half hasn't been closed.",
            # ⛔ AND THE SHAPE ITSELF (#11258 review round 9 `P1`, MEASURED):
            # `cannot` and `shan't` were GREEN under the ENUMERATION that replaced
            # `isn't`, so the arm is a pattern now. These two keep the pattern's
            # two branches (apostrophe-contraction, `cannot`) from being re-listed.
            "the ADJACENCY half cannot be closed.",
            "the ADJACENCY half shan't be closed.",
            # ⛔ AND THE APOSTROPHE-LESS FORM (#11258 review round 10 `P1`): the
            # arm's KNOWN-STEM branch exists for these, so narrowing back to a
            # mandatory apostrophe has to red here, not just in the mutations.
            "the ADJACENCY half wasnt closed.",
            # ⛔ THE `no longer …` ARM (#11258 review round 9 `P1`, MEASURED): this
            # pattern loses SIX exclusive proofs and the membership loop pinned
            # none of them, so it could be deleted with its proofs inside the
            # floor's slack and `the ADJACENCY half is no longer closed.` went
            # GREEN with both tests GREEN.
            "the ADJACENCY half is no longer closed.",
            "the ADJACENCY half is no longer judged.",
            # ⛔ AND THE SHARED WINDOW (#11258 review round 12 `P1`): three arms
            # ran the same rule and only one had the window, and the floor — the
            # ONLY tuple the waived path runs — had none, so one filler word
            # defeated it. These two are the unit-vocabulary and floor arms.
            "`minute` is no longer being fused.",
            "the SPELLING gap is no longer being fused.",
            # ⛔ THE `yet to be closed` SYNONYM ARM (#11258 review round 9 `P2`).
            "the ADJACENCY half remains to be closed.",
            # ⛔ AND ONE SHAPE PER ARM/PATTERN THAT HAS FEW EXCLUSIVE PROOFS. The
            # round-6 fix covered `_RESIDUAL_CLOSED_VERDICT` and left the rest:
            # `_SPELLING_FLOOR` had NO membership entry and exactly ONE exclusive
            # proof, `\bunclosed\b` and the bare opening verb one each, so each
            # could be deleted WITH its proof inside the floor's slack — and the
            # first of those silently restores the round-3 `P1` (the honest waiver
            # skipping the whole openness arm). Measured GREEN/GREEN before these
            # lines (#11258 review round 7 `P1`).
            "the SPELLING gap is open again.",
            # ⛔ `the ADJACENCY result is unclosed` DOES NOT PIN THE FLOOR'S ARM —
            # measured at `474435d658`: delete the floor's `\bunclosed\b` and the
            # suite is `103 passed`, because that sentence is caught by
            # `_RESIDUAL_OPENNESS`'s OWN `\bunclosed\b` (a class-anchored sentence
            # never reaches the floor). The floor is the ONLY tuple a WAIVED sentence
            # runs, so its exclusive proof has to carry `spelling`. ⚠️ BUT IT IS NOT
            # FREE TO DROP: that entry is the only membership pin on `_RESIDUAL_OPENNESS`'s
            # arm, and `_RESIDUAL_OPENNESS` has NO length floor of its own, so it is
            # named here AND below — one entry per arm, not one entry per shape.
            "the SPELLING gap is unclosed.",
            "the ADJACENCY result is unclosed",
            "the carve-out reopens.",
            # ⛔ THE OTHER DIRECTION NEEDS MEMBERSHIP TOO: without it every proof of
            # this branch's own false-block fixes was deletable with zero red.
            "the ADJACENCY half is not open.",
            # ⛔ AND ONE PER FORM OF THE `fails open` VETO RUN (#11258 review round
            # 25 `P2`): the run's own members need membership, or a proof can be
            # swapped for a duplicate while uniqueness and the floor both survive.
            "the ADJACENCY half no longer fails open.",
            "the ADJACENCY half never fails open.",
            "the ADJACENCY half does not fail open.",
            # ⛔ AND THE MIXED-CLAUSE FLOOR'S OWN PROOFS (round 26, MEASURED): the
            # five mixed `spelling`+truthful shapes were the ONLY proofs that the
            # per-clause floor pass runs in the MIXED form, and they had no
            # membership entry — so replacing all five with five OTHER reopenings
            # (length 162, uniqueness 162, every membership string above intact)
            # and then DELETING the pass left the whole proof GREEN: the pass was
            # DELETABLE WHILE GREEN, the rounds-17/18 `P0` class, in the fix for
            # the round-26 `P1` itself. Membership is the only thing that blocks
            # SUBSTITUTION (see the round-9/11/19/20/22/25 entries), so each shape
            # is named here rather than left to the length floor.
            "the SPELLING gap is reopened, and the pair is fused.",
            "the SPELLING gap is unclosed, and the pair is fused.",
            "the SPELLING gap no longer fuses, and the pair is fused.",
            "the SPELLING gap is open again, and the pair is fused.",
            "the SPELLING gap opens again, and the ADJACENCY half is closed by the tables.",
            "the SPELLING gap has never been closed, and the ADJACENCY half is closed by the tables.",):
        assert (required in _RESIDUAL_REOPENING_MUTATIONS
                or required in _RESIDUAL_TRUTHFUL_STATEMENTS), (
            f"the proof lists no longer carry {required!r} — that shape is the "
            "reason its arm exists; a lane reading this list must be able to run "
            "the shape, so deleting it is a silent coverage regression")
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    assert not _reds(text), "the row itself reds before any mutation is applied"
    # ⛔ THE REGION BOUND IS THE POINT, so it is asserted rather than assumed — and
    # the append point is anchored on the LITERAL row that follows the list, never
    # on `_residual_region`. Deriving it from the scanner is how the first version
    # of this test became self-referential: the mutations moved with the bound, so
    # narrowing the bound kept the suite GREEN (#11258 review P1, measured — with
    # the literal anchor, narrowing it puts every mutation outside the region and
    # the fixture assertion below fires).
    region = _residual_region(text)
    old_bound = text.split("(d) a RATE unit", 1)[1].split("(e) the", 1)[0]
    # ⛔ AND IT MUST START AT THE ROW'S OWN ENTRY — the marker sits ~12.9k chars
    # in, and a scan that starts there reads 74.6% of the artifact and reports PASS
    # over a reopening written into the row's narrative above it (#11258 review
    # round 3, MEASURED). `region` here is the SCANNER's own output, deliberately:
    # a bound that moves back to the marker reds this assert (measured); the
    # assertion on the append point below is the one that stays on a FIXTURE
    # literal, so a narrowing bound cannot move it.
    assert region.startswith(_RESIDUAL_ROW_START), region[:60]
    assert len(region) > len(old_bound), (
        "the residual region must reach PAST the pre-#11223 bound (the `(d)` "
        "block): that bound is the defect this pin exists to remove")
    # ⛔ AND THE APPEND POINT MUST BE OUTSIDE THAT OLD BOUND, or every mutation
    # below would be inside the region the OLD guard read and would prove
    # nothing. This is asserted on the FIXTURE, not on the scanner.
    # The `(d2)` clause is the row's own text that the old bound could not see; its
    # absence there is what makes "appended after the block" mean "invisible to the
    # pre-#11223 guard" (a measured fact, not an inference).
    assert "(d2)" not in old_bound, (
        "the `(d2)` clause is now INSIDE the pre-#11223 bound, so this fixture no "
        "longer demonstrates the append point being outside it — re-measure the "
        "claim in the docstring before changing this assert")
    missed = []
    for sentence in _RESIDUAL_REOPENING_MUTATIONS:
        mutated = _with_appended(text, sentence)
        assert sentence in _residual_region(mutated), (
            f"the fixture appended {sentence!r} OUTSIDE the region it tests — "
            "the mutation would prove nothing")
        assert mutated.index(sentence) >= mutated.index(old_bound) + len(old_bound), (
            f"the fixture appended {sentence!r} INSIDE the pre-#11223 `(d)` block "
            "— a mutation there proves nothing about the append point")
        if not _reds(mutated):
            missed.append(sentence)
    assert missed == [], (
        "these reopenings appended after the residual list stayed GREEN — the "
        f"region-wide scan does not cover the append point: {missed}")
    # ⛔ AND THE PREFIX IS COVERED TOO, or "region-wide" still means "from the "
    # marker onward": a reopening written into the row's own narrative, ABOVE the
    # marker, is the shape #11223's bound would still miss one step further in.
    # Inserted at the row's start — before the marker — so the assertion fails if
    # the bound ever moves back to `_RESIDUAL_MARKER`.
    prefix_mutation = "the ADJACENCY half is open again"
    prefix_at = text.index(_RESIDUAL_ROW_START) + len(_RESIDUAL_ROW_START)
    assert prefix_at < text.index(_RESIDUAL_MARKER), (
        "the row's entry start is now AFTER its own residual marker, so this "
        "fixture no longer inserts above it — re-measure the prefix claim")
    prefixed = text[:prefix_at] + "      ⛔ (2b) " + prefix_mutation + ".\n" + text[prefix_at:]
    assert _reds(prefixed), (
        "a reopening inserted into the row's narrative ABOVE the residual marker "
        "stayed GREEN — the scanned region still starts at "
        f"{_RESIDUAL_MARKER!r}, so it reads only "
        f"{len(region) - region.find(_RESIDUAL_MARKER)} of the row's "
        f"{len(region)} chars and publishes PASS over the rest (#11223's own "
        "defect, one bound further in)")
    false_alarms = [
        sentence for sentence in _RESIDUAL_TRUTHFUL_STATEMENTS
        if _reds(_with_appended(text, sentence))]
    assert false_alarms == [], (
        "these TRUTHFUL statements red the scan — a false block is how a guard "
        f"gets deleted: {false_alarms}")
    # ⛔ AND THE OVER-BLOCK LEDGER IS RE-MEASURED, not asserted in prose (#11258
    # review round 7 `P2`). These are honest and refused; the pin states the
    # cost so it is a measured property of this revision rather than a discovery
    # left to the next lane. A shape that stops being refused is an IMPROVEMENT —
    # move it to `_RESIDUAL_TRUTHFUL_STATEMENTS` and drop it here.
    now_accepted = [s for s in _RESIDUAL_REFUSED_TRUTHFUL
                    if not _reds(_with_appended(text, s))]
    assert now_accepted == [], (
        "these over-blocked statements are no longer refused — that is an"
        " improvement, so move each one into `_RESIDUAL_TRUTHFUL_STATEMENTS`"
        " (with its measured verdict) rather than leaving two lists: "
        f"{now_accepted}"
    )


#: The PIN'S STRUCTURAL HALF, one falsifier per rule (#11258 review round 22 `P0`).
#:
#: ⛔ MEASURED BEFORE THIS EXISTED — every rule below was deletable with the file at
#: `106 passed`. The reopening half was never in that state: both proof tuples drive
#: `_residual_reopening_claims`, so an arm there cannot be removed silently. The
#: structural half is a straight-line run of `assert`s whose only caller feeds it the
#: REAL row, and a real row satisfies every one of them by definition.
#:
#: ⛔ AND THE LIST ITSELF IS PINNED — BY MEMBERSHIP, NOT BY SIZE (#11258 review
#: round 23 `P0`, MEASURED). A falsifier list that can be SHORTENED is the round-1
#: `P0` over again one layer up: deleting the per-sentence entry AND the loop it covers
#: left the whole file at `108 passed`, with the pin passing a row that says the
#: ADJACENCY half "was judged" — two edits, each individually green. So every entry
#: carries a stable `key` and the test body pins the KEY SET: an entry can neither be
#: deleted (its key is missing) nor substituted (a duplicate key, or a key that no
#: longer names its rule). That is the shape the three other proof tuples already use
#: — UNIQUENESS for padding, MEMBERSHIP for substitution, a `>=` floor for shrinkage —
#: and it is the shape this one did not have.
#:
#: Each entry is `(key, label, mutate, attribution, rule)`:
#:   * `key` — the stable, short name the test body pins membership of;
#:   * `label` — the rule, named the way its own assertion names it;
#:   * `mutate` — the registry text → a copy that breaks THAT rule;
#:   * `rule` — the SOURCE LINE of the assert that must actually, fire, as a
#:     fragment of its own text. ⛔ ADDED BECAUSE THE KEY WAS NOT THE RULE (#11258
#:     round 27 `P2`, MEASURED): `attribution` above checks WHICH KIND of failure
#:     arrived, never WHICH RULE refused it, so an entry whose mutant reddens a
#:     NEIGHBOURING structural rule passed every arm — the `adjacency-claim-exists`
#:     entry (`assert adjacency_at`) was reddened by the WINDOW assert two lines
#:     above it, and its own rule was dead: patching it to `pass` changed no
#:     verdict. Declaring the line turns "some structural assert refused it" into
#:     "THIS rule refused it", which is the only thing the label ever claimed.
#:     Declared for every entry, not just the ambiguous one: the gap was the whole
#:     registry's, and a field filled in on one row would let the next row in
#:     unfalsified. A probe for it (a synthetic entry that reddens a neighbour)
#:     lives in `test_a_falsifier_that_reddens_a_neighbouring_rule_is_refused`.
#:   * `attribution` — how the red is allowed to arrive. Named arms, not a fallback
#:     (#11258 review round 23 `P2`): an arm that accepts ANY non-reopening reason
#:     is the wrong-reason red this test's own docstring forbids.
#:       `"claims-empty"`    the reopening scan RETURNS `[]` on the mutant, so the
#:                          structural rule is provably the only thing that refused
#:                          it;
#:       `"claims-not-run"`  the scan RAISES — a guard inside `_residual_region`
#:                          refuses the mutant before the claim rule can run, so it
#:                          was never judged a reopening either. That is all this
#:                          arm needs and all it may claim;
#:       `"region-guard"`    additionally asserts `_residual_region(mutant)` raises,
#:                          because the rule under test IS that guard, and a mutant
#:                          can break a structural rule AND a region guard at once.
#:                          `rule` pins the guard's OWN assert line for these.


def _red_line(call, mutant: str, src_lines) -> tuple[str, int, str]:
    """The SOURCE LINE of the assert that actually refused `mutant` (#11258 round 27).

    Returns `(function_name, lineno, line_text)` for the innermost `AssertionError`
    frame, so a falsifier can be attributed to the assert it CLAIMS rather than to any
    structural rule that happens to be nearby. `src_lines` is this file's own source
    (read once by the caller); the lineno is 1-based, as `traceback` gives it.

    ⛔ WHY THIS EXISTS: `attribution` says which KIND of failure arrived, never which
    RULE refused it, so one entry could red a neighbour's assert and read as full
    coverage of its own dead one — MEASURED on `adjacency-claim-exists`, whose
    mutant reddened the 1,200-char WINDOW assert while its own `assert adjacency_at`
    could not fire at all.
    """
    try:
        call(mutant)
    except AssertionError:
        frame = traceback.extract_tb(sys.exc_info()[2])[-1]
        return frame.name, frame.lineno, src_lines[frame.lineno - 1].strip()
    raise AssertionError(
        "the mutant did not raise at all, so it cannot attribute a rule — the "
        "entry proves nothing about the assert it is labelled with")


def _mutate_row_end_unfindable(text: str) -> str:
    """The row becomes the LAST entry: the `  - id: ` terminator after it is gone.

    Without the terminator `_residual_region` would read the REST OF THE FILE and
    red on other rows' prose — the failure its own message names — so removing the
    declaration that follows it is the honest mutant for "the END is findable".
    """
    start = text.index(_RESIDUAL_ROW_START) + len(_RESIDUAL_ROW_START)
    nxt = re.search(r"\n  - id: ", text[start:])
    assert nxt is not None, (
        "the fixture expects a declaration AFTER the `state_value_carve_out` row to "
        "truncate at — the row is the last entry in the registry now, so this "
        "falsifier has nothing to remove")
    return text[:start + nxt.start()]


def _mutate_adjacency_past_the_window(text: str) -> str:
    """Push the ADJACENCY sentence PAST residual (d)'s 1,200-char opening window.

    ⛔ THE `adjacency-claim-exists` ENTRY THIS USED TO BE PAIRED WITH IS GONE, and
    so is its `assert adjacency_at` (#11258 round 27 `P2`, MEASURED). That entry
    claimed this rule was un-falsifiable while `adjacency_at` held — "no mutant can
    break the window assert without also emptying `adjacency_at`" — and that record
    was FALSE (#11258 review round 24 `P1`): the sentence sits ~700 chars into the
    window, so filler inserted BEFORE it moves the sentence past 1,200 while leaving
    it inside residual (d), and `adjacency_at` still finds it. This helper is that
    mutant; with it, the WINDOW rule is pinned like every other — and the dead
    `adjacency_at` assert it was wrongly paired with is deleted rather than left
    carrying a label this mutant does not red.
    """
    anchor = "is the carve-out's own fifth example."
    assert text.count(anchor) == 1, (
        f"the window fixture anchors on {anchor!r} and the row now carries it "
        f"{text.count(anchor)} times — re-anchor it rather than deleting the entry")
    return text.replace(anchor, anchor + " the row says so." * 60, 1)


_STRUCTURAL_FALSIFIERS = (
    (
        "d-header",
        "residual (d) is still stated as the RATE-unit gap",
        lambda t: t.replace("(d) a RATE unit", "(d) the RATE-unit gap", 1),
        r"assert len(residual) == 2",
        "claims-not-run",
    ),
    (
        "rate-per-day",
        # ⛔ ONE RULE, ONE ASSERT (#11258 round 27 `P3`). This entry used to say the
        # rule was "expressed TWICE" and that no single deletion was caught; the
        # SECOND assert (block-wide) was UNREACHABLE, not merely redundant — the
        # window assert implies it (`after ⊆ block`) — and it is deleted, see the
        # positive control in `_assert_the_row_names_its_closure`. So a single
        # deletion DOES red here now, and this mutant reds the one assert left.
        "the RATE class's own `per day` example is still in residual (d)'s opening",
        lambda t: t.replace("requests per\n      day", "requests per\n      week", 1),
        r'assert "per\n      day" in after or "per day" in after',
        "claims-empty",
    ),
    (
        "positive-control",
        "the positive control: the RATE class is still named as NOT JUDGED",
        lambda t: t.replace("is not judged: no unit is ADJACENT",
                            "is judged: no unit is ADJACENT", 1),
        'assert "not judged" in flat',
        "claims-empty",
    ),
    (
        # ⛔ THE `adjacency-claim-exists` ENTRY THAT STOOD HERE IS DELETED (#11258
        # round 27 `P2`, MEASURED), WITH its `assert adjacency_at`. Its label named
        # the row still making an ADJACENCY claim at all; its mutant walked the
        # ADJACENCY word out of the first 1,200 chars, so the red landed on the
        # WINDOW assert below — a rule that already has its own entry — while its
        # own assert could not fire for ANY mutant (`after ⊆ block`). Restoring the
        # note without a falsifier for it is the defect; the rule is now covered by
        # the window assert (a superset) and by the `after in block` premise check.
        #
        # ⛔ THE WINDOW ASSERT IS A SEPARATE RULE FROM `adjacency_at`, MEASURED
        # (#11258 review round 24 `P1`): this entry replaces a record that claimed
        # the opposite. See `_mutate_adjacency_past_the_window`.
        "adjacency-claim-in-window",
        "the ADJACENCY claim is still inside residual (d)'s opening window",
        _mutate_adjacency_past_the_window,
        'assert "ADJACENCY" in after.upper()',
        "claims-empty",
    ),
    (
        "adjacency-closed-per-sentence",
        "the ADJACENCY gap is asserted CLOSED, per SENTENCE",
        lambda t: t.replace("stand here is CLOSED:",
                            "stand here was judged, and the tables now cover it:", 1),
        "assert any(re.search(p, sentences[i])",
        "claims-empty",
    ),
    (
        "one-marker",
        "the registry carries exactly ONE residual-list marker",
        # ⛔ NOT `t.replace(marker, marker + " (2b)")` — MEASURED: that leaves the
        # count at ONE (the marker substring survives inside its own replacement),
        # so it mutates nothing the guard reads and would prove the guard missing
        # when it is present. A second OCCURRENCE is what the guard counts.
        lambda t: t + "\n      " + _RESIDUAL_MARKER + " — a second, unguarded list\n",
        "assert text.count(_RESIDUAL_MARKER) == 1",
        "region-guard",
    ),
    (
        "one-row",
        "the registry carries exactly ONE `state_value_carve_out` row",
        lambda t: t + "  - id: state_value_carve_out\n    name: \"a second, unguarded copy\"\n",
        "assert row_copies == 1",
        "region-guard",
    ),
    (
        "row-end-findable",
        "the row's END (the next `  - id: ` entry) is findable",
        _mutate_row_end_unfindable,
        "assert nxt is not None",
        "region-guard",
    ),
    (
        "region-spans-d-to-e",
        "the region runs from residual (d) through residual (e)",
        lambda t: t.replace("(e) the", "(e) that", 1),
        r'assert (_RESIDUAL_MARKER in region and "(d) a RATE unit" in region',
        "region-guard",
    ),
)


def _verify_structural_falsifier(entry, text: str, src_lines) -> None:
    """One `_STRUCTURAL_FALSIFIERS` entry, measured end to end (#11258 round 27).

    Extracted so the checks are exercised by a PROBE entry as well as by the real
    list: `test_a_falsifier_that_reddens_a_neighbouring_rule_is_refused` feeds it a
    deliberately mis-attributed entry, so the attribution check has a falsifier of
    its own instead of being trusted because it is written down.

    `entry` is `(key, label, mutate, rule, attribution)`; `src_lines` is this file's
    source, used to name the assert that actually fired.
    """
    key, label, mutate, rule, attribution = entry
    assert attribution in ("claims-empty", "claims-not-run", "region-guard"), (
        f"{key!r} declares an unknown attribution {attribution!r} — an unnamed "
        "arm is a fallback, and a fallback accepts any non-reopening reason")
    mutant = mutate(text)
    assert mutant != text, (
        f"the fixture for {key!r} ({label!r}) no longer CHANGES the registry — "
        "the row wording it targets has moved, so this falsifier proves nothing "
        "at all. Re-anchor it on the row's new text; do not delete the entry")
    try:
        _assert_the_row_names_its_closure(mutant)
    except AssertionError:
        pass
    else:
        raise AssertionError(
            f"the structural rule {label!r} has NO falsifier: a registry that "
            "breaks it still passes the pin, so the rule is prose the suite "
            "never re-measures — the class this artifact exists to refuse")
    # ⛔ ATTRIBUTION, asserted per named arm rather than as one permissive
    # fallback (#11258 review round 23 `P2`). `claims-empty` is the STRONG arm:
    # the claim rule ran and found nothing, so nothing but the structural rule
    # can have refused the mutant. `claims-not-run` records the weaker fact —
    # the mutant never reached the claim rule — instead of pretending to the
    # stronger one.
    if attribution == "claims-empty":
        assert _residual_reopening_claims(mutant) == [], (
            f"the falsifier for {key!r} reds through the REOPENING scan, so it "
            "proves nothing about the structural rule it is labelled with — "
            "re-derive the mutant, or declare `claims-not-run` if the scan "
            "cannot run on it at all")
    else:
        with pytest.raises(AssertionError):
            _residual_reopening_claims(mutant)
    if attribution == "region-guard":
        # asserted on the GUARD ITSELF, not inferred from the pin's failure above: a
        # mutant can break a structural rule AND the region guard, and then the pin's
        # red says nothing about which one refused it.
        frame, lineno, line = _red_line(_residual_region, mutant, src_lines)
    else:
        frame, lineno, line = _red_line(
            _assert_the_row_names_its_closure, mutant, src_lines)
    # ⛔ THE RED MUST ARRIVE AT THIS ENTRY'S OWN RULE (#11258 round 27 `P2`). The
    # kind arms above cannot see WHICH assert fired, so an entry whose mutant reddens
    # a NEIGHBOURING rule read as full coverage of a rule with no killing mutant at
    # all — measured on the deleted `adjacency-claim-exists`, whose
    # `assert adjacency_at` could not fire for any mutant. `rule` is a fragment of the
    # raising line's own text, so a re-anchored entry that drifts onto a neighbour's
    # assert reds HERE instead of passing quietly.
    assert rule in line, (
        f"the falsifier for {key!r} ({label!r}) reds {frame}:{lineno} — "
        f"`{line}` — which is NOT the rule it declares (`{rule}`). An entry "
        "that reds a neighbouring rule proves nothing about its own: "
        "re-derive the mutant, or delete the rule and the entry together")


def test_a_falsifier_that_reddens_a_neighbouring_rule_is_refused():
    """The attribution check has a falsifier of its own (#11258 round 27 `P2`).

    ⛔ MEASURED, and this is the finding: at `06cdf2a692e7` the falsifier table
    carried `adjacency-claim-exists` labelled "the row still makes an ADJACENCY claim
    at all", whose mutant walked the ADJACENCY word out of residual (d)'s 1,200-char
    window. Every arm the table had — the `mutant != text` guard, "the pin reds", the
    kind attribution — PASSED, because the red landed on the WINDOW assert three lines
    above, a rule that already had its own entry. The entry's own rule
    (`assert adjacency_at`) could not fire for ANY mutant (MEASURED by deletion:
    patching it to `pass` left the control PASS and all 10 falsifiers red at the same
    rules), so the table asserted coverage of a rule it never touched.

    This probe feeds `_verify_structural_falsifier` exactly that shape — a real
    mutant, a real red, `claims-empty` genuinely satisfied — with only the `rule`
    fragment pointing at a DIFFERENT assert. Before the `rule` check existed this
    entry passed; now it must be refused, and named as mis-attributed. A probe that
    hardcodes the expected message would pass on a stub, so what is asserted is the
    refusal AND that it names the declared rule.
    """
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    src_lines = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines()
    probe = (
        "probe-reddens-a-neighbour",
        "a rule whose mutant reds the NEXT rule's assert instead",
        lambda t: t.replace("requests per\n      day", "requests per\n      week", 1),
        'assert "ADJACENCY" in after.upper()',
        "claims-empty",
    )
    with pytest.raises(AssertionError) as refused:
        _verify_structural_falsifier(probe, text, src_lines)
    message = str(refused.value)
    assert "probe-reddens-a-neighbour" in message, (
        f"the probe was refused for some other reason: {message}")
    assert "is NOT the rule it declares" in message, (
        "the probe was refused without naming the mis-attribution — the check that "
        f"refused it is not the `rule` check this test exists to pin: {message}")


def test_every_structural_rule_in_the_pin_has_a_falsifier():
    """#11258: the pin's STRUCTURAL half, refused rule by rule.

    ⛔ WHY THIS EXISTS — MEASURED at the review head (`6c3c18b8280f`), and it is
    this artifact's own defect class one layer in. `_assert_the_row_names_its_
    closure` has two halves: the REOPENING half, whose arms are iterated by both
    proof tuples (`_RESIDUAL_REOPENING_MUTATIONS` must RED,
    `_RESIDUAL_TRUTHFUL_STATEMENTS` must stay GREEN), and the STRUCTURAL half — an
    unbroken run of `assert`s over the real row. A real row satisfies all of them
    by definition, so nothing re-measured any of them: neutralising the `(d)`
    header split, the `per day` example, the `not judged` positive control, the
    per-sentence CLOSED rule, the single-marker guard AND the single-row guard left
    this file at **106 passed**.

    ⛔ TWO OF THOSE ARE NOT COSMETIC, which is what makes this a `P0` rather than
    tidiness:
      * neutralising the per-sentence CLOSED loop restores review cycle 7's `P1` —
        a row saying the ADJACENCY half "was judged" (a retired claim: no CLOSED
        verdict in the sentence) passes, because the block-wide `"closed" in flat`
        control that cycle 7 replaced is satisfied elsewhere in the 10,019-char
        block;
      * neutralising the row guard restores the duplicate-row fail-open its own
        comment measures — a second `  - id: state_value_carve_out` row publishes
        its own residual list while the scan reads whichever copy comes first.

    ⛔ AND A MUTANT MUST NOT RED FOR THE WRONG REASON. Each entry declares its
    attribution — `claims-empty` (the scan RETURNS `[]`, so the structural rule is
    provably the only refuser), `claims-not-run` (the scan RAISES, so the mutant was
    never judged a reopening), or `region-guard` (that guard IS the rule, asserted
    directly) — so a mutant that is merely a reopening cannot stand in for a missing
    structural rule. Same shape as the over-block ledger: what is measured is
    recorded as measured, and what a rule CANNOT do is stated rather than implied.

    ⛔ AND A MUTANT MUST NOT RED FOR THE WRONG RULE EITHER (#11258 round 27 `P2`,
    MEASURED). Attribution by KIND was not enough: `adjacency-claim-exists`
    (`assert adjacency_at`) was reddened by the WINDOW assert three lines above it,
    so an entry whose own rule was DEAD read as full coverage — the exact shape
    cargo-mutants refuses when it reports the test that CAUGHT a mutant rather than
    "some test failed". Each entry now declares the source line of the assert that
    must fire (`rule`), and this loop re-reads the raised frame's own line.

    ⛔ AND THE LIST OF FALSIFIERS IS ITSELF PINNED — see its own note. A falsifier
    list that can be SHORTENED is this same `P0` one layer up, and it was measured:
    deleting the per-sentence entry plus the loop it covers left the file at `108
    passed` over a row that says the ADJACENCY half "was judged".

    ⛔ NOT FALSIFIED HERE, stated rather than implied: nothing — and the two rules
    that WERE here under a claimed falsifier are gone rather than excused. The two
    SPELLING-tail rules were DELETED (#11258 review round 24 `P1`, MEASURED): both
    are implied by this test's own `sum(…) > 1` records over the same `tail`, so
    neither could ever fire. `assert adjacency_at` and the block-wide `per day`
    assert were DELETED with their claims (#11258 round 27 `P2`/`P3`, MEASURED):
    each was UNREACHABLE given the assert above it, and patching either to `pass`
    changed no verdict. A DEAD assert is worse than an absent one — the absence is
    visible here, the dead assert reads as coverage — which is why the eliminations
    leave their premise re-measured below (`after in block`) instead of a note.
    """
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    src_lines = pathlib.Path(__file__).read_text(encoding="utf-8").splitlines()
    # control: the real row must PASS, or a mutant could red for a reason that has
    # nothing to do with the rule it is labelled with.
    _assert_the_row_names_its_closure(text)
    # ⛔ THE PREMISE under which the two dead asserts above were deleted (#11258
    # round 27). `assert adjacency_at` was dead and the block-wide `per day` assert
    # was dead ONLY while `after ⊆ block`; if a later edit reads `block` from a
    # narrower window than `after`, both deleted rules become reachable again and the
    # per-sentence CLOSED loop silently goes vacuous (a suppressed red, not a loud
    # one). So the premise is re-measured rather than assumed.
    _residual_after_block = text.split("(d) a RATE unit", 1)[1]
    assert _residual_after_block[:1200] in \
        _residual_after_block.split("(e) the", 1)[0], (
            "`after` (the 1,200-char opening window) is no longer a PREFIX of the "
            "(d) block, so `assert adjacency_at` and the block-wide `per day` assert "
            "that were deleted as unreachable are reachable again — re-derive a "
            "falsifier for the rule they covered (and for the now-vacuous "
            "per-sentence CLOSED loop) instead of reading the deletion as coverage")
    # ⛔ THE LIST IS PINNED (#11258 review round 23 `P0`): see its own note. Without
    # this, deleting a rule and the single entry covering it is TWO edits that are
    # each individually green — measured at `108 passed` over a row that says the
    # ADJACENCY half "was judged".
    keys = [entry[0] for entry in _STRUCTURAL_FALSIFIERS]
    assert len(set(keys)) == len(keys), (
        "the structural falsifier list has duplicate keys — a duplicate pays for a "
        "deleted rule while the length stays constant")
    assert set(keys) >= {
        "d-header",
        "rate-per-day",
        "positive-control",
        # ⛔ NOT `adjacency-claim-exists`: its assert was dead and its mutant reddened
        # the window rule, so the entry is DELETED with it (#11258 round 27 `P2`).
        # Re-adding the key without a rule that its own mutant reds is the defect.
        "adjacency-claim-in-window",
        "adjacency-closed-per-sentence",
        "one-marker",
        "one-row",
        "row-end-findable",
        "region-spans-d-to-e",
    }, (
        "the structural falsifier list no longer carries a declared rule — deleting "
        "a rule AND the falsifier that covers it is the `P0` this key set exists to "
        f"refuse (have: {sorted(set(keys))})")
    for entry in _STRUCTURAL_FALSIFIERS:
        _verify_structural_falsifier(entry, text, src_lines)
    # ⛔ THE RECORD behind "not falsified here": the tail rules cannot be isolated
    # while more than one tail sentence carries their evidence. If that ever drops
    # to ONE, they CAN be falsified a sentence at a time and the entries belong in
    # `_STRUCTURAL_FALSIFIERS` — so this reds and says so rather than leaving a
    # stale excuse in a docstring (the [pin-defensive-claim] class this file polices).
    block = text.split("(d) a RATE unit", 1)[1].split("(e) the", 1)[0]
    flat = " ".join(block.split()).lower()
    sentences = re.split(r"(?<=[.!?;])\s+", flat)
    tail = sentences[
        [i for i, s in enumerate(sentences) if "adjacency" in s][-1] + 1:]
    assert sum("spelling" in s for s in tail) > 1, (
        "the SPELLING disclosure is now expressed by a SINGLE tail sentence, so "
        "`_assert_the_row_names_its_closure`'s two tail rules CAN be falsified one "
        "sentence at a time — move them into `_STRUCTURAL_FALSIFIERS` and delete "
        "this record rather than leaving it stale")
    assert sum("spelling" in s and "open" in s for s in tail) > 1, (
        "only one tail sentence now carries `spelling` AND `open`, so the tail "
        "rules' evidence is isolable — falsify them in `_STRUCTURAL_FALSIFIERS` "
        "instead of recording them as unfalsifiable")


def test_the_filler_windows_are_justified_by_the_language_not_by_the_row():
    """The interpolation notes cite a MEASUREMENT, not "the row's own register" (#11258 round 21).

    ⛔ MEASURED, and this is the correction: SEVEN notes in this file said
    `, in practice, ` / `, after the widen, ` / `, for now, ` is "the row's own
    register" (or "the row's OWN interpolation") — and `config/v4-mechanisms.yml`
    writes NONE of them (0 occurrences each, exact AND whitespace-flattened; no
    commit ever added one, `git log -S` is empty for all three). The class is the
    one this file polices: a lane reading the note looks for the form in the row
    and does not find it. Round 20's `P3` severity argument inherited the false half
    verbatim ("the row writes it"), which is why the correction belongs on the
    artifact rather than in a comment.

    What the notes claim now is TWO-SIDED, and both sides are pinned here:
      * the row does not contain the comma-delimited forms — so the cost is met by
        a lane EDITING the row, not by the row's current text, and
      * each form is still pinned RED in `_RESIDUAL_REOPENING_MUTATIONS` — THAT
        list, not the row, is what justifies the filler windows. The pin is
        per-FORM, not per-mutation (`in practice` is carried by six mutations and
        the others by one each): deleting EVERY mutation carrying a form is what
        leaves the window justified by nothing a reader can re-measure.

    ⛔ PRE-FIX BEHAVIOUR: with the "row's own register" prose restored, the suite
    stayed green (105 passed at `7cfb51aa0d4`) — nothing re-measured the claim, so
    it could stay false indefinitely, and it did (written in round 13, still there
    at round 20). Both assertions below are measured, not asserted: revert either
    side and this test reds.
    """
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    row = _residual_region(text)
    flat_row = " ".join(row.split())
    for interpolation in (", in practice,", ", after the widen", ", for now,"):
        assert interpolation not in flat_row, (
            f"the registry now WRITES {interpolation!r} — that is fine, but the "
            "notes in `_RESIDUAL_CLOSED_VERDICT` / `_RESIDUAL_OPENNESS` / the "
            "over-block ledger state the cost as met by an EDIT; re-measure those "
            "notes against the row's new text rather than leaving a claim that is "
            "true for the wrong reason")
        assert any(interpolation in mutation
                   for mutation in _RESIDUAL_REOPENING_MUTATIONS), (
            f"no mutation pins {interpolation!r} RED — the filler windows are "
            "justified by the language, and the proof of that justification is "
            "this list; a window whose only red-required shape is gone is a window "
            "with no falsifier")


def test_every_refusal_names_the_over_block_ledger():
    """The route out of a refusal is offered on EVERY refusal (#11258 round 19 `P2`).

    ⛔ MEASURED, and this is why the test exists: the pointer paragraph hung off
    `if ledger_hits else ""`, so a refusal was told its disclosure "REOPENS a gap
    this row says is CLOSED" with NO route — the false-block path that ends with
    the pin being deleted. `(a) is not closed.` is the honest case that matters:
    `(a)` is an item of the row's OWN still-open list, so the statement is true,
    and it is not one of the recorded entries.

    ⛔ PRE-FIX BEHAVIOUR: every non-ledger shape in `not_in_ledger` below was
    refused with a message that did not contain `_RESIDUAL_REFUSED_TRUTHFUL` at
    all (104–105 chars at measurement TIME — the list is pinned by MEMBERSHIP, not
    by that width, so adding a shape does not stale this sentence), while `(b2)` —
    which IS in the ledger — carried the 634-char pointer. A
    message whose help depends on membership in a hardcoded list is exactly the
    false block the ledger's own instructions forbid.
    """
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    not_in_ledger = ("(a) is not closed.", "(c) is not closed.",
                     "(e1) is not closed.")
    for sentence in not_in_ledger:
        with pytest.raises(AssertionError) as refused:
            _assert_the_row_names_its_closure(_with_appended(text, sentence))
        message = str(refused.value)
        assert "_RESIDUAL_REFUSED_TRUTHFUL" in message, (
            f"the refusal of {sentence!r} offers no route out — it does not name "
            "the over-block ledger, so an honest disclosure of a still-open item "
            f"reads as an accusation with no remedy: {message!r}")
        assert "is still open" in message, (
            "the refusal names the ledger but not a phrasing it accepts — a lane "
            f"cannot rewrite its sentence from that: {message!r}")
        assert "A LEDGER SHAPE IS ALSO IN THIS SENTENCE" not in message, (
            f"{sentence!r} is NOT in the ledger, so claiming a ledger shape is "
            f"in the sentence states something false about the artifact: {message!r}")
    # and the SPECIFIC match is still reported when there is one — hoisting the
    # pointer must not flatten the two cases into one message (#11258 round 11
    # `P2`/`P3`: the sentence can carry BOTH an honest ledger shape and a real
    # reopening, and a message that calls that a recorded over-block is false).
    with pytest.raises(AssertionError) as refused:
        _assert_the_row_names_its_closure(_with_appended(text, "(b2) is not closed."))
    message = str(refused.value)
    assert "A LEDGER SHAPE IS ALSO IN THIS SENTENCE" in message, (
        f"the refusal of a DECLARED ledger shape no longer says so: {message!r}")
    assert "(b2) is not closed." in message, (
        f"the refusal does not name the entry that matched: {message!r}")


def test_the_fails_open_over_block_is_in_the_ledger_not_only_a_comment():
    """The `_RESIDUAL_FAIL_OPEN` cost is re-measured, not only written down.

    ⛔ MEASURED at the pre-fix head: the `_RESIDUAL_FAIL_OPEN` note states the
    over-block is "recorded here rather than left to be discovered", while both
    of its sentences red at the append point and appear in NO ledger entry — so
    changing that arm left the suite green and the record stale. The ledger is the
    only place a cost is re-measured (`now_accepted` in
    `test_the_residual_region_is_scanned_WHERE_a_lane_appends`), which is the
    failure this file's own note calls "a stale entry is worse than no entry".
    """
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    for sentence in ("the numeric side fails open on an unknown unit",
                     "the numeric side still fails open on purpose",
                     # ⛔ AND THE BOUNDED-LOOKBEHIND COST OF THE ARM'S NEW VETO
                     # (#11258 review round 25 `P2`): the run admits a negator only
                     # IMMEDIATELY before the verb, so an adverb between them is
                     # refused though the sentence denies the predicate. Recorded,
                     # so a widening of the run that stops refusing it reds here.
                     "the ADJACENCY half does not actually fail open."):
        assert sentence in _RESIDUAL_REFUSED_TRUTHFUL, (
            f"the `fails open` over-block {sentence!r} is not in the ledger, so "
            "nothing re-measures it — a cost recorded only in a comment can drift")
        assert _reds(_with_appended(text, sentence)), (
            f"{sentence!r} is no longer refused — that is an IMPROVEMENT, so move "
            "it into `_RESIDUAL_TRUTHFUL_STATEMENTS` rather than leaving a stale "
            "over-block entry behind")


def test_every_apostrophe_shape_gives_the_same_verdict_in_EITHER_spelling():
    """The negation class reads the typographic apostrophe too (#11258 round 22 `P2`).

    ⛔ MEASURED, and this is why the test exists: the contraction ARM and every
    spelling arm already accepted `’`, while the negation RUNS spelled only `'` —
    so SEVEN truthful denials in `_RESIDUAL_TRUTHFUL_STATEMENTS` were REFUSED in
    the spelling macOS supplies for free and GREEN in ASCII, and NO ledger entry
    re-measured the difference. An unrecorded over-block is the one this artifact
    forbids: it ends with the pin being deleted rather than with the guard fixed.

    ⛔ PRE-FIX BEHAVIOUR (measured at `6c3c18b8280f`): this test fails on exactly
    those seven — `the ADJACENCY gap hasn't been open again.`, `… half isn't yet
    opened again.`, `… hasn't yet opened again.`, `… hasn't ever opened again.`,
    the two `the SPELLING gap …` twins and `the ADJACENCY gap isn't kept open.`
    each flip GREEN → RED when ONLY the apostrophe changes.

    THE INVARIANT, rather than a second list: a spelling must never decide a
    verdict, so every shape in the three proof lists that contains `'` is
    re-measured with `’` substituted for EVERY apostrophe in it and has to keep
    its OWN verdict — mutations RED, truthful statements GREEN, ledger entries
    refused. A hand-picked partner list would cover the shapes someone thought
    of; this covers every apostrophe shape the proof surface already carries, and
    a shape added later is covered the moment it lands.
    """
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    converted = 0
    mismatches: list[tuple[str, str, str]] = []
    for name, sentences in (("mutation", _RESIDUAL_REOPENING_MUTATIONS),
                            ("truthful", _RESIDUAL_TRUTHFUL_STATEMENTS),
                            ("ledger", _RESIDUAL_REFUSED_TRUTHFUL)):
        for sentence in sentences:
            if "'" not in sentence:
                continue
            converted += 1
            curly = sentence.replace("'", "\u2019")
            if _reds(_with_appended(text, curly)) != _reds(_with_appended(text, sentence)):
                mismatches.append((name, sentence, curly))
    assert mismatches == [], (
        "the apostrophe SPELLING decided these verdicts — a lane writing one of "
        "them with a typographic apostrophe gets a different answer than its own "
        "proof list records, so one of the two is an UNRECORDED over-block: "
        f"{mismatches}")
    assert converted >= 21, (
        f"only {converted} proof-list shapes carry an apostrophe — the invariant "
        "above is measured on the shapes that exist, so a shrink here means the "
        "surface it pins shrank. ⚠️ FLOOR IS THE CURRENT COUNT (21: 7 mutations + "
        "14 truthful statements + 0 ledger entries — MEASURED at this head; the "
        "apostrophe partners rounds 22-25 added are what carried it here), ZERO "
        "SLACK, the "
        "same rule the two proof-list floors carry: raise it in the same commit "
        "that adds shapes.")


def test_the_suffix_comes_from_the_last_digit_run():
    """Review cycle 3, finding 3: `raw.find(...)` read the FIRST occurrence of
    the last run (`24h/7d/4w` → `hdw`). Reverting to it reds these two."""
    assert v2._value_core("24h/7d/4w") == ("", "24/7/4", "w")
    assert v2._value_core("12.3ms/1") == ("", "12.3/1", None)


def test_an_unknown_unit_is_a_spelling_not_a_rewrite():
    """The fail-open half of the same finding: same unit, different spelling."""
    for content, quote in (("800millis", "800ms"), ("20kms", "20km"),
                           ("1.5-inch", "1.5in"), ("32-byte", "32-bytes")):
        assert v2._value_slot_divergence(f"the value is {content}",
                                         f"the value is {quote}") == (), content


def test_a_refused_value_over_the_content_cap_cannot_be_minted_either():
    """Review cycle 3, P2: the set was keyed UNCLAMPED while `_mint_endpoint`
    compares the CLAMPED text, so a >`_MAX_CONTENT` refused content was minted
    anyway. The CLAMPED key reds THIS pin; the refusal records the UNCLAMPED key
    as well, because a whitespace-heavy >cap content collapses to a short norm
    that a short prose operator ref carries — see
    `test_a_whitespace_collapsed_over_cap_refusal_still_blocks_the_mint`."""
    content = "the p95 hit 4.0 seconds " + ("z" * (v2._MAX_CONTENT + 200))
    quote = "the p95 hit 4.2 seconds " + ("z" * (v2._MAX_CONTENT + 200))
    embed = _embed(content, quote)
    embed["operators"] = [{"src": content, "dst": "the cache TTL is 20 minutes",
                           "op_type": "IMPL"}]
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    # ⚠️ Compare against the CLAMPED text: the resurrection artifact is what
    # `_mint_endpoint` builds, i.e. `str(ref).strip()[:_MAX_CONTENT]`. Asserting
    # against the UNCLAMPED string is vacuous — it is true even when the
    # rewritten value did re-enter, which is exactly cycle 4's finding.
    clamped = content[:v2._MAX_CONTENT]
    assert all(p["content"] != clamped and "4.0 seconds" not in p["content"]
               for p in result["payload"]["points"]), (
        [p["content"][:40] for p in result["payload"]["points"]])


def test_a_whitespace_collapsed_over_cap_refusal_still_blocks_the_mint():
    """Review cycle 6, P2: the UNCLAMPED key is load-bearing, not redundant.

    A refused content that exceeds `_MAX_CONTENT` only in WHITESPACE collapses
    under `_norm`, so a SHORT prose operator ref carries the collapsed full text
    — and `_mint_endpoint`'s clamped key cannot produce it. With only the clamped
    key the refused value is re-minted (measured before this pin:
    `'the p95 hit 4.0 seconds b'` reappeared in `payload["points"]`).
    """
    content = "the p95 hit 4.0 seconds" + ("\n" * (v2._MAX_CONTENT + 200)) + "b"
    embed = {
        "entities": [],
        "points": [{"content": content, "pointKind": "statement",
                    "about_entities": [],
                    "quote": "the p95 hit 4.2 seconds"}],
        "events": [], "operators": [{
            "src": "the p95 hit 4.0 seconds b",
            "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}],
        "chain_notes": [], "link_before_create": [],
    }
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    assert result["payload"]["points"] == [], (
        [p["content"][:44] for p in result["payload"]["points"]])


# ── 7b. the MINT's own cut, which cycle 14 left on the old hard clamp ──────

#: A content whose `[:_MAX_CONTENT]` cut lands INSIDE the value and whose
#: discarded remainder starts with an ALPHANUMERIC (`0`). That is the branch
#: `_write_span` resolves by dropping the split token WHOLE, so the token-safe
#: span carries no value while the HARD clamp carries `… p95 hit 4`.
_SPLIT_VALUE_PAD = v2._MAX_CONTENT - 10
_SPLIT_VALUE_CONTENT = "z" * _SPLIT_VALUE_PAD + " p95 hit 4.0 seconds"


def _split_value_embed() -> dict:
    """One point plus an operator whose `src` is that point's OWN content."""
    return {
        "entities": [],
        "points": [{"content": _SPLIT_VALUE_CONTENT, "pointKind": "statement",
                    "about_entities": [], "quote": "p95 hit 4.2 seconds"}],
        "events": [], "operators": [{
            "src": _SPLIT_VALUE_CONTENT,
            "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}],
        "chain_notes": [], "link_before_create": [],
    }


def test_the_MINT_cuts_at_a_TOKEN_boundary_so_it_cannot_FABRICATE_a_value():
    """Review cycle 15, P2: the write was moved to `_write_span` and the MINT
    was left on the hard clamp, so the module still fabricated a value.

    Measured pre-fix on this shape: `_mint_endpoint` built a quote-less Point
    whose content ended `… p95 hit 4` — a value the model never wrote, out of a
    text whose own token-safe span carries no value at all (`… p95 hit `) — and
    the point was written to the graph. The helper is now the module's ONE
    truncation, so the mint cannot invent a value the write would have dropped.

    Non-vacuous: the assertion names the exact fabricated string, and the mint
    does produce it pre-fix.
    """
    hard = _SPLIT_VALUE_CONTENT.strip()[:v2._MAX_CONTENT]
    # the shape is only interesting because the two cuts DIFFER:
    assert hard.endswith("hit 4"), hard[-12:]
    assert v2._write_span(_SPLIT_VALUE_CONTENT, v2._MAX_CONTENT) != hard
    result = v2.execute_embed(_split_value_embed(), {}, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert hard not in contents, (
        "the mint stored the HARD-clamped text — a value the model never wrote "
        f"({hard[-12:]!r})")
    assert all(not c.endswith("hit 4") for c in contents), [c[-14:] for c in contents]


def test_a_ref_that_matches_the_stored_content_RESOLVES_instead_of_minting_a_DUPLICATE():
    """Review cycle 15, P2: ONE truncation helper, or the dedup breaks.

    `_mint_endpoint` keys the already-emitted check on `_norm(content)` of its
    OWN cut (`:8907`). While the write stored the hard clamp and the mint
    computed the same hard clamp, a byte-identical operator ref hit that dedup
    and minted nothing. Moving the WRITE to the token-safe span without moving
    the mint made the two strings differ, so the same ref missed the dedup and
    minted a second, quote-less Point beside the one it was naming — measured
    pre-fix: 3 points and an `operator endpoint minted` warning for a ref that
    is the point's own content, verbatim.

    Non-vacuous: pre-fix this reds on both the count and the mint warning.
    """
    result = v2.execute_embed(_split_value_embed(), {}, session_id="s_10360")
    points = result["payload"]["points"]
    # scoped to the SRC (the ref that IS the point's own content): the `dst`
    # endpoint is genuinely new, so its mint is correct and unrelated here.
    minted = [w for w in (result.get("warnings") or [])
              if "operator endpoint minted" in w and "src named" in w]
    assert not minted, (minted, [p["content"][-14:] for p in points])
    assert len(points) == 1, (len(points), [p["content"][-14:] for p in points])


def test_the_stored_content_IS_the_identity_key_a_verdict_and_a_prior_key_on():
    """Review cycle-18 P1: `_MAX_CONTENT` is an IDENTITY cap, not only a bound.

    `_norm(x.strip()[:_MAX_CONTENT])` is the key `_endpoint_keys`,
    `_merge_verdict_keys`, `classify_consolidation` and the write's own dedup
    compute — and every point already in the graph was stored under it. Cycle 14
    moved the write onto `_write_span`, which dropped the split token even when
    it carried no digit: measured, a 1639-char content stored 999 chars instead
    of the key's 1000 (`… team meets ` instead of `… team meets w`), so a pair
    the S2.2b arbiter had ruled identical fell through to ADD — `merges == 0`,
    one duplicate Point minted, silently (the #5006 F1 defect).

    The span now moves only for a truncated NUMERAL, which is the only thing the
    value rule is about (`_write_span`). ⛔ Non-vacuous: pre-fix `span` is 999
    chars at this shape, and the `core` end-to-end pin
    `tests/test_near_dup_merge_5006.py::
    TestTheWritePathEmitsOneEntityAndKeepsTheLosersEvidence::
    test_a_verdict_on_a_candidate_over_the_cap_still_merges` reds with
    `merges == 0`.
    """
    raw = ("the team meets weekly in the main office " * 40).strip()
    hard = raw[:v2._MAX_CONTENT]
    span = v2._write_span(raw, v2._MAX_CONTENT)
    assert len(raw) > v2._MAX_CONTENT
    # the cut lands INSIDE a LETTER token (`meets w|eekly`): the shape that must
    # NOT move the span
    assert hard.endswith("meets w") and not hard[-1].isdigit(), hard[-12:]
    assert span == hard, span[-14:]
    assert v2._norm(span) == v2._norm(hard)
    # …so what the write STORES is the string the write's matchers key on
    assert v2._norm(span) in v2._merge_verdict_keys(raw)
    assert v2._norm(span) in v2._endpoint_keys(raw)
    result = _write(raw, "the team meets weekly")
    assert [p["content"] for p in result["payload"]["points"]] == [hard], (
        [len(p["content"]) for p in result["payload"]["points"]],
        result["warnings"])
    # ⛔ positive control, so this cannot pass by the rule having gone blind: a
    # NUMERAL cut at the same cap DOES move the span (the value exception).
    hard_split = _SPLIT_VALUE_CONTENT.strip()[:v2._MAX_CONTENT]
    assert hard_split.endswith("hit 4"), hard_split[-12:]
    assert v2._write_span(_SPLIT_VALUE_CONTENT, v2._MAX_CONTENT) != hard_split


def test_the_MINT_stores_the_refs_OWN_identity_prefix_so_the_5069_loop_closes():
    """Review cycle-18 P1: `_endpoint_keys` keys on `ref[:1000]`; the mint stores it.

    Measured pre-fix: a ref of `"a"*500 + " "*50 + "b"*500 + "tail"` (1054
    chars) whose cut lands inside the `b` run was stored as 550 chars, because
    the split token was dropped whole although it carried no digit. The guard's
    key is the ref's OWN `_MAX_CONTENT` window, so every later comparison that
    re-derives an endpoint's identity from its STORED text (the #5069 closed
    loop, the closure/removal arms) compared two different strings, and ~45% of
    the ref never reached the graph.

    ⛔ Non-vacuous: pre-fix `span` is 550 chars and the `core` pin
    `tests/test_vet_gate_5005.py::test_content_only_closure_collision_does_not_over_prune`
    reds on `_norm(p[:1000]) == _norm(ref[:1000])`.
    """
    ref = "a" * 500 + " " * 50 + "b" * 500 + "tail"
    hard = ref[:v2._MAX_CONTENT]
    assert len(ref) > v2._MAX_CONTENT
    # the cut lands inside the `b` run — alphanumeric, but NO digit, so the span
    # must stay the identity window
    assert hard.endswith("b") and not hard[-1].isdigit(), hard[-12:]
    span = v2._write_span(ref.strip(), v2._MAX_CONTENT)
    assert span == hard, (len(span), len(hard))
    assert v2._norm(span) in v2._endpoint_keys(ref)
    # …and the mint END TO END stores that window, not a shorter span
    embed = {
        "entities": [], "points": [], "events": [], "chain_notes": [],
        "link_before_create": [],
        "operators": [{"src": ref, "dst": "the cache TTL is 20 minutes",
                       "op_type": "IMPL"}],
    }
    result = v2.execute_embed(embed, _RESOLVED_SEARCH, session_id="s_10360")
    minted = [p["content"] for p in result["payload"]["points"]]
    assert hard in minted, ([len(c) for c in minted], result["warnings"])


# ── 8. the rule's own BOUND, and the residual it still carries ───────────────────

def test_an_over_long_side_is_not_judged_so_the_write_path_stays_bounded():
    """UNVERIFIED item 2, settled: the judge must be BOUNDED.

    The pure function answers `()` for a side longer than `_MAX_JUDGED_TEXT`
    (2 x `_MAX_CONTENT`), because it is LINEAR in the text it is given —
    measured on this box: 0.45 ms at 67 chars, 2.5 ms at 1 KB, 23 ms at 10 KB,
    227 ms at 100 KB, 2.4 s at 1 MB. That bound is now a DEFENSIVE one for
    direct callers: the write path never reaches it, because it hands the rule
    the span the write KEEPS (`_write_span`, <= `_MAX_CONTENT`/`_MAX_QUOTE`).
    ⛔ That is also why an over-long quote no longer FAILS OPEN at the write
    (review cycle-13 P2): the stored quote keeps its first `_MAX_QUOTE` chars,
    and for this pair those chars DO carry `4.2` against a point saying `4.0` —
    failing open there recorded a rewritten value as verbatim to save work the
    write path no longer spends. Bounded AND judging the written pair, instead
    of bounded at the price of the guarantee.
    """
    content = "the p95 hit 4.0 seconds"
    quote = "the p95 hit 4.2 seconds " + ("padding z " * (v2._MAX_JUDGED_TEXT))
    assert len(quote) > v2._MAX_JUDGED_TEXT
    # within the bound the same pair IS a rewrite, so this fails open only on
    # length — the bound cannot pass by the rule being broken (positive control)
    assert v2._value_slot_divergence(content, "the p95 hit 4.2 seconds")
    assert v2._value_slot_divergence(content, quote) == ()
    # ⛔ the WRITE is judged on the span it keeps, so the kept prefix's rewrite
    # is refused rather than failed open on the discarded tail. The cut lands
    # INSIDE `paddin|g` — a LETTER token, so the span is the hard cut itself
    # (cycle-18: only a truncated NUMERAL moves it) — and it carries `4.2`.
    kept = v2._write_span(quote, v2._MAX_QUOTE)
    assert kept == quote[:v2._MAX_QUOTE], kept[-12:]
    assert v2._value_slot_divergence(content, kept), kept
    result = _write(content, quote)
    assert result["payload"]["points"] == [], result["warnings"]
    assert any("REWRITES its own verbatim" in w for w in result["warnings"])
    # …and the bound still holds at the write for a side that is over-long AND
    # whose kept prefix carries no value: nothing to judge, so nothing is spent
    result = _write("the p95 hit 4.0 seconds", "padding z " * (v2._MAX_JUDGED_TEXT))
    assert len(result["payload"]["points"]) == 1, result["warnings"]


def test_an_over_long_side_is_not_tokenised_at_all(monkeypatch):
    """The same bound, stated as COST: the engine is never called for it.

    This is the half a return-value pin cannot see. It fails on the pre-fix code
    (which tokenised the whole side first and answered `()` afterwards), so the
    bound cannot be deleted without this going red.
    """
    calls = {"n": 0}
    real = v2._value_bindings

    # ⛔ `*args, **kwargs`: the engine gained a `with_following` keyword
    # (#10513 review cycle 18) so the JUDGING layer can ask about a SCALE
    # word. The wrapper forwards it — a signature that dropped the keyword
    # would make this pin pass by refusing to call the engine at all, which
    # is the opposite of what it exists to assert.
    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(v2, "_value_bindings", counting)
    huge = "the p95 hit 4.2 seconds " + ("z " * v2._MAX_JUDGED_TEXT)
    assert v2._value_slot_divergence("the p95 hit 4.0 seconds", huge) == ()
    assert calls["n"] == 0, f"tokenised an over-long side: {calls['n']} calls"
    # …and a side AT the bound is still judged (the bound is a bound, not an off
    # switch): the engine is called again for a judgeable pair.
    assert v2._value_slot_divergence("the p95 hit 4.0 seconds",
                                     "the p95 hit 4.2 seconds")
    assert calls["n"] > 0


def test_a_leading_currency_symbol_is_FORM_today_and_that_is_stated():
    """The residual the review called out, PINNED so it cannot move silently.

    `_value_core` reads the numeric core and the unit AFTER it, so a LEADING
    symbol is discarded: `$12` and `€12` are one value. That is fail-open (the
    direction this rule always takes), and making a symbol a unit identity would
    add a false-refusal class — `$` / `USD` / `US$` are one currency spelled
    three ways, exactly the `800millis`/`800ms` trap cycle 3 measured. So the
    behaviour STAYS and the registry row names it in "WHAT IS STILL NOT
    ENFORCED" instead. If a future lane tightens it, this test reds and the row
    has to move with it.
    """
    assert not v2._value_slot_divergence("the invoice was $12",
                                         "the invoice was €12")
    # the positive control: a symbol does not hide a numeric rewrite
    assert v2._value_slot_divergence("the invoice was $12",
                                     "the invoice was $60")


def test_a_version_trailing_zero_is_FORM_today_and_that_is_stated():
    """The residual the cycle-10 review called out, PINNED so it cannot drift.

    `_value_core` drops a trailing fractional zero UNCONDITIONALLY, because that
    is what makes `4.20`/`4.2` one value — the false refusal that rejected the
    first attempt at this issue. The same strip also folds `2.10` into `2.1`,
    and for a VERSION that is a semantic change, not a form: `2.10` and `2.1`
    are different releases, and `versions` is a class `STATE_VALUE_CARVE_OUT`
    names explicitly.

    It stays, and the registry row names it as residual (j) instead: splitting
    the strip by "does this token look like a version" would re-open the
    `4.20`/`4.2` false refusal on any two-group decimal, which is the direction
    this rule must never take. If a future lane tightens it, this test reds and
    the row has to move with it.
    """
    assert v2._value_core("2.10") == ("", "2.1", None)
    assert not v2._value_slot_divergence("the release is 2.10",
                                         "the release is 2.1")
    # the positive control: a version's NON-zero groups are still judged, so this
    # residual is a trailing-zero tolerance and not a hole in version comparison.
    assert v2._value_slot_divergence("the release is 2.10",
                                     "the release is 2.11")
    assert v2._value_slot_divergence("the SDK version is 2026.10",
                                     "the SDK version is 2025.10")


def test_a_dash_attached_to_the_numeral_is_a_sign_and_the_bullet_glyph_shape_is_stated():
    """Residual (k), PINNED: the dash arm is shared and over-reads one glyph.

    An en/em dash is a MINUS in every spelling (`_keeps_numeric_edge` keeps a
    leading `Pd`), which is what stops a truthful `-5` point being dropped over
    a typographic minus — the over-refusal direction that is worse than no guard.

    ⚠️ The arm is NOT local to this rule: it feeds `_value_bindings`, which the
    against-store fold path also consumes (`test_never_across_fold_5080.py`).
    And it over-reads exactly one shape, measured here: an em dash written FLUSH
    against the numeral (`—5 minutes`, punctuation used as a bullet glyph) reads
    as minus, so a bulleted content line against an unbulleted quote diverges as
    a dropped sign and is refused. A SPACED dash and `•` stay decoration. The
    row states the cost as residual (k) instead of narrowing it, because telling
    a bullet from a minus means judging the glyph's context — the same
    form-vs-value guess that re-opened the `800millis`/`800ms` false refusal.
    If a future lane narrows it, this test reds and the row has to move with it.
    """
    # ATTACHED em dash: read as a sign, so the unmarked side diverges.
    assert v2._value_core("\u20145 minutes") == ("-", "5", "min")
    assert v2._value_slot_divergence("\u20145 minutes of downtime",
                                     "5 minutes of downtime")
    # SPACED dash and a bullet marker: decoration, so the two sides agree. Both
    # are the positive controls — the arm must not turn punctuation into a
    # quantity in the shapes where the glyph is unambiguously markup.
    assert not v2._value_slot_divergence("\u2014 5 minutes of downtime",
                                         "5 minutes of downtime")
    assert not v2._value_slot_divergence("\u2022 5 minutes of downtime",
                                         "5 minutes of downtime")
    # …and the sign the arm exists for is still kept: a dash does not hide a
    # real sign change, in every spelling.
    assert v2._value_slot_divergence("\u20145 minutes of downtime",
                                     "-5 minutes of downtime") == (), (
        "one minus, two spellings, must be ONE value — otherwise a truthful "
        "point is dropped over typography")
    assert v2._value_slot_divergence("-5 minutes of downtime",
                                     "5 minutes of downtime")


def test_the_changelog_records_the_flip_and_marks_the_old_claim_superseded():
    """The durable record a later lane reads INSTEAD of the row (#10513 P3).

    The 2026-10-30 `#9775` entry ends by asserting the row "keeps
    `not-implemented` because … nothing in the write path *enforces* retention".
    `#10360` put the enforcement in the write path, so that present-tense claim
    went false and NOTHING in the tree marked it superseded — the defect this
    test exists to keep closed. Two directions, both required:

    * the old claim is still WORDS in the history (deleting it is how a later
      lane loses the reason the residual list is read first), and it carries a
      supersession marker naming `#10360`;
    * a dated entry AFTER it records the flip with the row state the registry
      actually carries, so the page and `config/v4-mechanisms.yml` cannot drift
      apart in either direction.

    Non-vacuous: on the pre-fix tree there is no marker and no such entry, so
    both halves red.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    page = (root / "docs/architecture/EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md"
            ).read_text(encoding="utf-8")
    claim = ("keeps `not-implemented` because")
    assert claim in page, (
        "the #9775 history paragraph was DELETED — it is the record of why the "
        "residual list outranks the state word; mark it superseded instead")
    claim_at = page.index(claim)
    # the marker has to be bound to the claim it supersedes, not floating
    # somewhere else in a 1,500-line page.
    marker = "SUPERSEDED 2026-11-01 by `#10360`"
    assert marker in page, (
        f"the pre-#10360 claim is not marked superseded ({marker!r} absent) — a "
        "reader arriving at state_value_carve_out still gets the old story")
    assert claim_at < page.index(marker) < claim_at + 700, (
        "the supersession marker is not adjacent to the claim it supersedes")
    # …and the dated entry records the flip AFTER it, with the state the row
    # actually carries. Asserting only "implemented appears somewhere" would
    # pass against the row prose alone, so the entry's own heading is required.
    entry = "## 2026-11-01 — the carve-out's enforcement half exists in the write path"
    assert entry in page, (
        "no dated entry records that #10360 landed and the row moved to "
        "`implemented`")
    assert page.index(entry) > claim_at, (
        "the entry must come AFTER the history it supersedes (newest last)")
    tail = page[page.index(entry):]
    assert "`implemented`" in tail, tail[:400]
    assert "execute_embed" in tail, tail[:400]
    # the departure is pointed at where the decision lives, not asserted here
    assert "6563672665" in tail, (
        "the entry names the OVERRIDES departure but not the comment id on "
        "#10360 that records it")
    # and the residuals that are NOT enforced stay readable in the same entry
    assert "REFUSE" in tail and "REPAIR" in tail, tail[:400]


# ---------------------------------------------------------------------------
# Review cycle 16 — four false-accusation / cost defects found at `dede0d903`,
# each fixed in `tortoise/extractor_v2.py` and pinned here.
#
# The common shape: the rule's own docstring says a guard that accuses a
# truthful point of a rewrite "is worse than no guard: it withholds a durable
# memory". Three of these four were exactly that, reachable from ordinary
# extracted text. Every test below carries the positive control that keeps the
# fix from becoming an unjudged hole.
# ---------------------------------------------------------------------------


def test_a_unicode_decimal_digit_is_the_same_value_in_every_script():
    """Cycle 16 P2: `\\d` matches every Nd script, the COMPARISON did not.

    `_VALUE_DECIMALS` is `\\d+`, so a fullwidth, Arabic-Indic or Devanagari
    numeral was BOUND and then normalised as a core STRING — the same value
    spelled in another decimal script diverged and the point was REFUSED.
    Measured at `dede0d903`: `_value_slot_divergence("…\\uff14.\\uff12 …",
    "…4.2 …") == (('hit', '\\uff14.\\uff12', '4.2'),)` — a dropped durable
    memory over typography, and it was in no residual.

    NFKC does NOT fold Arabic-Indic (checked, not assumed), so the fold is per
    codepoint through `unicodedata.digit`, which is total on category Nd.
    """
    # Every script spells the ONE value `4.2`, so the pair AGREES.
    for token in ("\uff14.\uff12", "\u0664.\u0662", "\u096a.\u0968"):
        assert v2._value_core(token) == ("", "4.2", None), token
        assert not v2._value_slot_divergence(
            f"the p95 hit {token} seconds", "the p95 hit 4.2 seconds"), token

    # POSITIVE CONTROL — the fold must not flatten a DIFFERENT value into
    # agreement. `4.3` in fullwidth is still a different value from `4.2`.
    assert v2._value_slot_divergence(
        "the p95 hit \uff14.\uff13 seconds", "the p95 hit 4.2 seconds"), (
        "folding must make SCRIPT invisible, never VALUE")
    # …and a sign in another script is still a sign, not a digit.
    assert v2._value_core("-5") == ("-", "5", None)


def test_the_whole_Pd_dash_family_reads_as_one_minus_not_just_four_literals():
    """Cycle 16 P2: the edge predicate keeps every `Pd`; the sign read four.

    `_keeps_numeric_edge` admits a LEADING `Pd` (so the dash reaches
    `_value_core` at all) while the sign tuple named only `-`, U+2212, U+2013
    and U+2014 — so the comment's own invariant ("now that the edge is kept, the
    dash reaches here and has to be read as the sign it is") was false for
    U+2010, U+2011, U+2012, U+2015, U+FE63 and U+FF0D. Measured at `dede0d903`:
    a truthful `\\u20115` (non-breaking hyphen — what PDF and Word extraction
    emits) against `-5` was REFUSED, the same shape cycle 10 fixed for en/em
    dash and left open for the rest of the family. The fix reads the sign from
    the SAME predicate, so the two lists cannot drift again.
    """
    family = ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014",
              "\u2015", "\ufe63", "\uff0d", "\u2212")
    for ch in family:
        assert v2._value_core(f"{ch}5") == ("-", "5", None), hex(ord(ch))
        assert not v2._value_slot_divergence(
            f"the temperature was {ch}5 degrees",
            "the temperature was -5 degrees"), hex(ord(ch))

    # POSITIVE CONTROL — one minus in nine spellings must not make a REAL sign
    # change invisible. Every member still diverges from the unsigned value…
    for ch in family:
        assert v2._value_slot_divergence(
            f"the temperature was {ch}5 degrees",
            "the temperature was 5 degrees"), hex(ord(ch))
    # …and a leading SPACED dash stays decoration, so the widened sign read did
    # not turn markup into a quantity (residual (k)'s boundary).
    assert not v2._value_slot_divergence("\u2014 5 minutes of downtime",
                                         "5 minutes of downtime")


def test_the_S5_mint_span_is_token_safe_so_it_cannot_fabricate_a_value():
    """Cycle 16 P2: the last holdout on the HARD `[:cap]` cut.

    `_write_span`'s own docstring calls itself "the module's ONE truncation
    helper", and `_mint_endpoint` was moved onto it in cycle 15 — but the S5
    quote spans (`_probe`, `_anchor`, `pt_entry["quote"]`) still hard-cut with
    `content[:_MAX_QUOTE]`. Measured, an operator ref of
    `"a " * 99 + "12.5 seconds overall"` minted a point whose stored quote ended
    `a a a 12` against a content of `12.5 seconds overall`, and
    `_value_slot_divergence` reports `(('', '12.5', '12'),)` with NO warning:
    the fabricated-value class the row's residual (e) claimed this site could
    not reach "by construction", because the point's own CLAMP is the reachable
    divergence.

    Non-vacuous both ways: the hard cut MUST diverge (that is the defect) and
    the token-safe span MUST not (that is the fix).
    """
    content = "a " * 99 + "12.5 seconds overall"
    assert len(content) > v2._MAX_QUOTE

    hard = content[:v2._MAX_QUOTE]
    assert v2._value_slot_divergence(content, hard), (
        "the hard cut is supposed to be the defect this test pins — if it no "
        "longer diverges, the fixture stopped straddling the cap")

    safe = v2._write_span(content, v2._MAX_QUOTE)
    assert not v2._value_slot_divergence(content, safe), (
        "the token-safe span must never read a value SHORT out of the cap")

    # And the S5 sites use it. This is a SOURCE property by nature ("one
    # truncation helper"), so it is asserted as one rather than inferred.
    # Comments are stripped first: the fix's own note QUOTES the old cut to
    # explain it, and a pin that reds on its own explanation is not a pin.
    src = inspect.getsource(v2.execute_embed)
    mint = src[src.index("def _mint_endpoint"):]
    mint = "\n".join(
        line for line in mint.splitlines() if not line.lstrip().startswith("#"))
    assert "content[:_MAX_QUOTE]" not in mint, (
        "the S5 mint quotes are still hard-cut — the value can be fabricated")
    assert "_quote_span = _write_span(content, _MAX_QUOTE," in mint, (
        "the S5 quote spans must cut with the module's one truncation helper")
    assert "hard_cut_single_token=False" in mint, (
        "the S5 site must refuse the single-token HARD fallback — on a ref that "
        "is one whitespace-free token it would stamp a quote that misstates "
        "its own value, and this site has no divergence check to catch it")
    assert mint.count('pt_entry["quote"] = _quote_span') == 1, (
        "the stored quote must be the SAME span that was probed and anchored")


def test_a_refusal_over_the_judged_cap_never_tokenises_the_unclamped_text():
    """Cycle 16 P2: the cost bound was claimed by construction and not enforced.

    `_value_slot_divergence` refuses to judge past `_MAX_JUDGED_TEXT`, and the
    docstring said "the write path never reaches it, because it hands the rule
    the span the write keeps". That was FALSE on the refusal path: the
    `refused_value_slots` collector fed `_value_slots` the UNCLAMPED
    `_raw_content` with no guard, so cost was linear and unbounded in the
    model-authored content length — measured 7.53 s to refuse a 2 MB content and
    17.67 s for 5 MB.

    The pin asserts the CONTRACT on the shipped refusal path — an over-cap text
    contributes no slot key — plus the MEASUREMENT that the pure function's own
    bound still refuses to judge it. Wall-clock is deliberately not asserted, so
    the pin cannot flake on a loaded box; the bound is the property, the seconds
    were the symptom.
    """
    cap = v2._MAX_JUDGED_TEXT
    over = "the p95 hit 4.0 seconds " + ("z " * cap)
    assert len(over) > cap
    # The pure function's own bound still holds — unchanged by this fix.
    assert v2._value_slot_divergence(over, "the p95 hit 4.2 seconds") == ()

    # The COLLECTOR, which is where the bound was missing, now enforces the same
    # cap on both keys. Asserted against the shipped source (there is no
    # side-effect-free seam to count calls through `execute_embed`), together
    # with the behavioural half above that the skipped key loses nothing a
    # judge would have used.
    src = inspect.getsource(v2.execute_embed)
    guard = "for _key_text in (_raw_content, content):\n"
    assert guard in src, "the two-key refusal collector moved — re-read this pin"
    after = src[src.index(guard):]
    after = after[:after.index("refused_value_slots.add")]
    assert "_MAX_JUDGED_TEXT" in after, (
        "the refusal collector tokenises the UNCLAMPED text with no length "
        "bound again — cost is linear in the model-authored content length")
    assert "continue" in after, (
        "the bound must SKIP the over-cap key, not truncate it (a truncated key "
        "describes a string the mint never produces)")


def test_a_numeral_in_another_script_keeps_its_OWN_decimal_separator():
    """Cycle 17 P2: folding the digits left the numeral fragmented.

    Folding the `Nd` digits (cycle 16) made `４.２` agree with `4.2` — but only
    when the separator was ASCII. A decimal script brings its OWN separator, and
    `_VALUE_DECIMALS` only knows the ASCII dot, so the numeral split into two
    runs that `_value_core` joined with `/`: measured, `４．２` (U+FF0E) and
    `٤٫٢` (U+066B) both read `('', '4/2', None)` and DIVERGED from `4.2`,
    refusing a truthful point — the same false-accusation class the cycle-16
    fold claimed to close, and stated as closed in the row.

    ⛔ The fail-open half is asserted too: that same `/` join made an
    Arabic-locale decimal compare EQUAL to a genuine ASCII `4/2` ratio. One root
    cause, both directions.
    """
    for token in ("\uff14\uff0e\uff12", "\u0664\u066b\u0662"):
        assert v2._value_core(token) == ("", "4.2", None), token
        assert not v2._value_slot_divergence(
            f"the p95 hit {token} seconds", "the p95 hit 4.2 seconds"), token

    # POSITIVE CONTROL — the fold must not make a DIFFERENT value agree, and it
    # must not make a genuine ratio look like a decimal.
    assert v2._value_slot_divergence(
        "the p95 hit \uff14\uff0e\uff13 seconds", "the p95 hit 4.2 seconds")
    assert v2._value_slot_divergence(
        "the ratio is 4/2", "the ratio is \u0664\u066b\u0662"), (
        "an Arabic decimal separator is not a solidus — the join artifact must "
        "not survive as agreement")

    # A THOUSANDS separator folds to `,`, which `_separator_reads_ambiguously`
    # then excludes from the judged set (no locale-free identity, so no
    # judgement) — this module's own doctrine for an ambiguous numeral.
    assert v2._value_core("4\u066c200") is None


def test_the_S5_mint_leaves_a_single_token_ref_UNQUOTED_rather_than_misquoting_it():
    """Cycle 17 P2: `_write_span`'s single-token fallback is still a HARD cut.

    When the text is ONE whitespace-free token longer than the cap there is no
    token boundary to cut at, so `_write_span` falls back to `s[:cap]`. On the
    CONTENT path that is the pinned pre-existing shape
    (`test_quote_capped_at_200`). At the S5 MINT site it stamped a quote that
    misstates its own content — and S5 has NO `_value_slot_divergence` call to
    refuse it. Measured: an operator ref of
    `"storage/" + "d"*190 + "12.5" + "z"*40` (242 chars, one token) stored the
    quote tail `dddddddddddd12` against a content holding `12.5…`, silently.

    The site now asks for `hard_cut_single_token=False` and names the drop in
    its warnings, so an absent quote replaces a misstating one.
    """
    ref = "storage/" + "d" * 190 + "12.5" + "z" * 40
    assert len(ref) > v2._MAX_QUOTE and " " not in ref

    # The default is UNCHANGED — the content path keeps its pinned hard cut.
    assert v2._write_span(ref, v2._MAX_QUOTE) == ref[:v2._MAX_QUOTE]
    # The S5 mode refuses to fabricate.
    assert v2._write_span(ref, v2._MAX_QUOTE,
                          hard_cut_single_token=False) == ""
    # …and the divergence it dodged is real, not hypothetical: the hard cut
    # misstates the value, the empty span cannot.
    assert v2._value_slot_divergence(ref, ref[:v2._MAX_QUOTE])
    assert v2._value_slot_divergence(
        ref, v2._write_span(ref, v2._MAX_QUOTE, hard_cut_single_token=False)) == ()

    # A MULTI-token ref is unaffected — it has a boundary, so it still quotes,
    # and the span is that boundary cut. Cycle 18: the `x` run carries no digit,
    # so nothing value-bearing is truncated and the cap STANDS (the content cap
    # is the identity key — see
    # `test_the_stored_content_IS_the_identity_key_a_verdict_and_a_prior_key_on`).
    multi = "long endpoint claim " + "x" * 300
    span = v2._write_span(multi, v2._MAX_QUOTE, hard_cut_single_token=False)
    assert span == multi[:v2._MAX_QUOTE], repr(span[-12:])
    # …and the token-drop is not vacuous: a ref whose NUMERAL straddles the quote
    # cap still drops the split token, so the S5 site cannot quote `12` out of
    # `12.5` even though it has a whitespace boundary.
    num_multi = "storage tier " + "d" * 185 + "12.5" + "z" * 40
    assert v2._write_span(num_multi, v2._MAX_QUOTE) == "storage tier "

    # End to end: the minted point is UNQUOTED and the drop is NAMED, never
    # silent. An unannounced loss is the rule-#9906 defect. `edus` is supplied
    # because the S5 quote spans are only reached when a transcript exists.
    embed = {
        "entities": [], "points": [], "events": [], "chain_notes": [],
        "link_before_create": [],
        "operators": [{"src": ref, "dst": "the quota is 20 requests",
                       "op_type": "IMPL"}],
    }
    result = v2.execute_embed(embed, _RESOLVED_SEARCH, session_id="s_10360", edus=[
        {"index": 0, "role": "user", "text": "maybe try intervals for speed"}])
    minted = [p for p in result["payload"]["points"] if p["id"].startswith("pt")]
    single = [p for p in minted if "storage/" in p.get("content", "")]
    assert single, "the endpoint was not minted at all — this pin needs it to be"
    assert single[0]["quote"] == "", (
        "a single-token ref must not carry a quote that misstates its value")
    assert any("no token-safe quote" in w for w in result["warnings"]), (
        "the withheld quote must be NAMED, not silent")


# ── 22. the cycle-18 false-accusation classes (measured, then closed) ──────
#
# Every pin in this block has the same shape and the same reason: the rule
# WITHHELD a truthful point, which is the one direction its own contract says it
# must never take ("a guard that accuses a truthful point of a rewrite it did
# not commit is worse than no guard"). Each was reproduced against the primary
# source at the reviewed head before the fix, and each pin carries a POSITIVE
# CONTROL so it can fail in the detection direction too rather than only
# certifying that the refusal stopped happening.


def test_a_HARD_CUT_pair_is_not_judged_because_the_cut_can_read_a_value_short():
    """Review cycle-18 P1: the module's own truncation refused a truthful point.

    ``_write_span`` falls back to the hard ``s[:cap]`` when the text is ONE
    whitespace-free token longer than the cap — there is no token boundary to
    cut at — and the write path then fed that truncated span to the judge.

    Measured before the fix: ``content = "z"*998 + "4.2 seconds"`` against
    ``quote = "4.2 seconds"`` — a TRUTHFUL point — was REFUSED with
    ``point says '4', quote says '4.2'``, and the mirror shape refused ``4.25``
    against a ``4`` read out of the quote. Both directions are pinned here, plus
    the end-to-end write, plus the positive control that a REAL rewrite over
    ordinary text is still refused.
    """
    hard_content = "z" * 998 + "4.2 seconds"
    mirror_quote = "q" * 198 + "4.25 seconds"
    # the shape really is the fallback, or this pin tests nothing
    assert v2._write_span_is_hard_cut(hard_content, v2._MAX_CONTENT)
    assert v2._write_span_is_hard_cut(mirror_quote, v2._MAX_QUOTE)
    assert v2._write_span(hard_content, v2._MAX_CONTENT) != hard_content
    # …and the pair those cuts WOULD produce diverges, which is the false
    # accusation: 998 z's + `4.` reads as the value 4.
    assert v2._value_slot_divergence(
        v2._write_span(hard_content, v2._MAX_CONTENT), "4.2 seconds")

    # POSITIVE CONTROL: an ordinary (under-cap) pair is NOT the fallback, so the
    # guard cannot pass by declaring every pair unjudgeable.
    assert not v2._write_span_is_hard_cut(
        "the p95 hit 4.2 seconds", v2._MAX_CONTENT)
    assert v2._value_slot_divergence(
        "the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")

    # END TO END: the truthful over-cap point is WRITTEN, and it is WRITTEN with
    # a warning naming why it was not judged — an unannounced fail-open would be
    # the #9906 silent-drop defect one layer over.
    embed = {"entities": [], "events": [], "chain_notes": [],
             "link_before_create": [],
             "points": [{"content": hard_content, "quote": "4.2 seconds",
                         "pointKind": "statement", "search_keys": []}]}
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    kept = [p for p in result["payload"]["points"] if p["content"].endswith("4.")]
    assert kept, ("the truthful over-cap point must survive — this pin needs it "
                  "to be written, not refused")
    assert any("NOT judged on this pair" in w for w in result["warnings"]), (
        "the fail-open must be NAMED, not silent")


def test_a_currency_symbol_does_not_swallow_the_SIGN():
    """Review cycle-18 P2: `$-4` read as `4`, so one value was refused.

    ``_value_core`` read the sign from ``raw.lstrip()[0]``, and a leading
    currency symbol is decoration AROUND the sign. Measured before the fix:
    ``_value_core("$-4")`` returned ``('', '4', None)``, so ``$-4``/``-4`` — ONE
    value in two spellings — DIVERGED and a truthful point was refused, while
    ``$-4``/``$4`` — a sign the model DROPPED, a real rewrite — was WRITTEN with
    no warning. Both directions are pinned; the second is the positive control.
    """
    assert v2._value_core("$-4") == ("-", "4", None)
    assert v2._value_core("-$4") == ("-", "4", None)
    assert not v2._value_slot_divergence("the delta was $-4",
                                         "the delta was -4")
    # POSITIVE CONTROL: the sign the model dropped is still a rewrite.
    assert v2._value_slot_divergence("the delta was $-4",
                                     "the delta was $4")


def test_an_accounting_PAREN_numeral_is_outside_the_judged_set():
    """Review cycle-18 P2: `($4)` and `-4` are one value, and it refused them.

    ``($4)`` is ``-4`` in accounting and a prose aside in a sentence, and WHICH
    ONE APPLIES IS A READER PROPERTY this path does not have — the same reason a
    separator-bearing numeral is unjudged (#2817 point 1: never invent a second
    reading of one token). Reading it as POSITIVE was not neutral: it refused
    ``"the shortfall was ($4)"`` against ``"the shortfall was -4"``.

    ⛔ Asked of the RAW text, because ``_value_bindings`` strips the parens
    before the token reaches ``_value_core`` — the positive control below is that
    a prose parenthetical is NOT swallowed (the exclusion is the accounting
    SHAPE, not every bracket).
    """
    assert v2._value_core("($4)") is None
    assert not v2._value_slot_divergence("the shortfall was ($4)",
                                         "the shortfall was -4")
    # POSITIVE CONTROL: a prose parenthetical keeps its slot, so a real rewrite
    # in that text is still refused — the exclusion did not swallow the side.
    assert v2._value_slots("a loss of (see 2019) here")
    assert v2._value_slot_divergence("a loss of (see 2019) here",
                                     "a loss of (see 2020) here")


def test_a_SCALE_word_is_compared_across_the_pair_rather_than_dropped():
    """Review cycle-18 P2: `12 million` against `12000000` refused a truthful point.

    ``_value_bindings`` fuses no magnitude word, so ``12 million`` binds the core
    ``12`` while the other side's ``12000000`` binds ``12000000`` and the two
    DIVERGED — one value written two ways, refused. Reading a scale is
    ``value_normalizer``'s territory and #2817's "no magnitude table" ruling is
    NOT reopened here, so a pair whose SCALES DIFFER is outside the judged set.

    ⛔ The scale is COMPARED, not dropped: ``12 million`` against ``13 million``
    is a real rewrite and must stay refused (this is the same property
    ``test_a_scale_WORD_rewrite_is_unjudged_because_the_engine_fuses_none``
    pins from the other side). Dropping every scale-bearing slot — the first
    attempt at this fix — silently lost it, and this pin is what caught that.
    """
    assert not v2._value_slot_divergence("we have 12000000 events",
                                         "we have 12 million events")
    assert not v2._value_slot_divergence("we have 1.0 million events",
                                         "we have 1000000 events")
    # POSITIVE CONTROLS: equal scales are still judged on the digits, and a
    # non-scale neighbour does not make the pair unjudgeable.
    assert v2._value_slot_divergence("we have 12 million events",
                                     "we have 13 million events")
    assert v2._value_slot_divergence("we sold 12 events", "we sold 13 things")


def test_a_BARE_parenthetical_numeral_is_still_judged():
    """Review cycle-20 P2: the paren exclusion was WIDER than it claimed.

    The first cut of `_PAREN_NUMERAL_RE` made every element optional
    (``\\(\\s*[-$€£¥]?\\s*-?\\d``), so it matched a BARE parenthetical numeral as
    well as the accounting shape — and `_value_slots_scaled` returns ``()`` for
    a text carrying a match, so ``the audit (2019) says 12 events`` became
    WHOLLY unjudgeable: a real ``12`` -> ``13`` rewrite in that text was NOT
    refused, while the constant's own note, `_refuse_rewritten_value`'s
    docstring and the registry row all claimed "the exclusion is the accounting
    SHAPE only". A prose parenthetical year is a common shape, and the loss was
    in the DETECTION direction — the guarantee quietly not applying rather than
    a truthful point being withheld.

    ⛔ The accounting reading has to be AVAILABLE before the token can be
    ambiguous: a currency symbol, an explicit sign, or an inner separator is
    what makes it so. `(2019)`, `(1)` and `(see 2019)` carry none of the three,
    so they keep their slots.
    """
    # the accounting SHAPE is still excluded, every spelling of it
    for excluded in ("($4)", "(-4)", "($-4.2m)", "(4.2)", "(1,200)",
                     "(\u20ac4)", "(-\u00a350)"):
        assert v2._PAREN_NUMERAL_RE.search(excluded), excluded
    # …and a BARE parenthetical numeral is NOT the accounting shape
    for judged in ("(2019)", "(1)", "(see 2019)", "( 4 )", "(2019-2020)"):
        assert not v2._PAREN_NUMERAL_RE.search(judged), judged
    # the side keeps its slots, so the rewrite below can be caught at all
    assert v2._value_slots("the audit (2019) says 12 events")
    # DETECTION: a real rewrite in a text carrying a prose paren numeral
    assert v2._value_slot_divergence("the audit (2019) says 12 events",
                                     "the audit (2019) says 13 events")
    # …and the truthful pair still agrees (the exclusion did not simply move)
    assert not v2._value_slot_divergence("the audit (2019) says 12 events",
                                         "the audit (2019) says 12 events")
    # POSITIVE CONTROL, the class the exclusion EXISTS for: one value in two
    # spellings stays unjudged, so the narrowing did not re-open the false
    # accusation cycle 18 fixed.
    assert not v2._value_slot_divergence("the shortfall was ($4)",
                                         "the shortfall was -4")
    assert v2._value_core("($4)") is None
    # …and the SECOND arm agrees with the text scan (#10513's own "two lists
    # that must agree" pattern): a wrapped BARE numeral is not the accounting
    # shape, so `_value_core` must not swallow it either.
    assert v2._value_core("(2019)") == ("", "2019", None)
    # END TO END: the write path refuses the rewrite, not just the predicate.
    result = _write("the audit (2019) says 12 events",
                    "the audit (2019) says 13 events")
    assert result["payload"]["points"] == [], result["warnings"]
    assert any("REWRITES its own verbatim quote" in w
               for w in result["warnings"]), result["warnings"]


# ── 23. the engine's own unit VOCABULARY, which was declared but not fused ──

def test_a_declared_unit_WORD_is_fused_so_a_unit_rewrite_cannot_hide():
    """Residual (d) of the registry row, CLOSED — and the row said how.

    `config/v4-mechanisms.yml`'s `state_value_carve_out` row carried this as a
    limit rather than a tolerance:

    > (d) a unit WORD the engine's own `_UNIT_WORDS` vocabulary does not carry
    > (`seconds`, `minute`, `hour`, `day`, `mile`, …) is never fused into the
    > value token — apart or not — so a minutes→seconds rewrite is not judged:
    > an ENGINE VOCABULARY limit, NOT a tolerance of this rule.

    It was exactly that: `_UNIT_EQUIV` DECLARED those spellings while
    `_UNIT_WORDS` — the set `_value_bindings` can actually FUSE — did not carry
    them, so the value was emitted with NO unit, and a unit-less side reads as
    "unit not stated" and FAILS OPEN. Measured on the shipped tree:

        content "the TTL is 20 minutes"  quote "the TTL is 20 seconds"
          bindings      c=('ttl', '20minutes')  q=('ttl', '20')
          divergence    ()          <-- a minutes→seconds rewrite, unrefused

    while the SAME class was already refused for `20 minutes`/`40 minutes`
    (`('ttl', '20min', '40min')`). The verdict depended on the SPELLING of the
    unit, which is the false-negative direction the carve-out exists to stop.

    ⛔ The `True` rows are the re-opened class; the `False` rows are the
    tolerance the completion must NOT buy its detection with — a unit spelled
    differently is still FORM, not value, and refusing it is the
    false-accusation direction this rule must never take.
    """
    # the rewrite class, previously fail-open for every row below
    for content, quote in (
        ("the TTL is 20 minutes", "the TTL is 20 seconds"),      # plural → plural
        ("the timeout is 90 seconds", "the timeout is 90 minutes"),
        ("the wait is 2 hour", "the wait is 2 second"),          # singular forms
        ("the timeout is 800 ms", "the timeout is 800 s"),        # ABBREVIATION, APART
        ("the drive is 20 miles", "the drive is 20 km"),          # mile → mi identity
        ("the TTL is 20 milliseconds", "the TTL is 20 minutes"),
    ):
        assert v2._value_slot_divergence(content, quote), (content, quote)

    # …and the tolerance bound is unchanged: same unit, different spelling
    for content, quote in (
        ("the TTL is 20 minutes", "the TTL is 20 min"),
        ("the TTL is 20 minutes", "the TTL is 20 mins"),
        ("the timeout is 800 ms", "the timeout is 800 msec"),
        ("the timeout is 800 milliseconds", "the timeout is 800 msecs"),
        ("the drive is 20 mile", "the drive is 20 miles"),
        ("the drive is 20 miles", "the drive is 20 mi"),
        ("the drop was 20 percent", "the drop was 20 pct"),
        ("the drop was 20 percent", "the drop was 20 %"),
        ("the wait is 2 hours", "the wait is 2 hrs"),
        ("the wait is 2 hour", "the wait is 2hr"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # …and the rewrite is REFUSED at the write boundary, not merely detectable
    result = _write("the TTL is 20 minutes", "the TTL is 20 seconds")
    assert result["payload"]["points"] == [], result["warnings"]
    assert any("REWRITES its own verbatim quote" in w
               for w in result["warnings"]), result["warnings"]


def test_the_unit_vocabulary_is_complete_in_the_direction_that_matters():
    """The MECHANICAL invariant that keeps residual (d) from re-opening.

    `test_the_unit_table_covers_every_unit_the_engine_can_fuse` pins one
    direction — a FUSED unit is never UNKNOWN. The direction that produced
    #10360's gap is the other one: a unit the module KNOWS (a key of
    `_UNIT_EQUIV`, which is what `_value_identity_matches` compares) but cannot
    FUSE, so the pair is never judged at all. Nothing pinned that, which is why
    the two tables could disagree for as long as they did.

    `%`/`percent`/`pct` are the named exceptions, and they are not an escape
    hatch — they are the ONE place a DECLARED identity is left unfused on
    purpose, and the reason is measured (#10360 review cycles 3 and 6, P2).
    `%` reaches a pair by two routes that disagree: `20 %` is merged onto its
    numeral by the TOKENIZER, but `20k %` is not (`['20k', '%']`), and the
    attached `20k%` tokenises to `['20k']`, dropping the symbol. Fusing
    `percent` therefore composed one side and not the other and REFUSED a
    truthful point. ⚠️ Leaving them unfused refuses nothing HEAD did not already
    refuse across same-value percent pairs, and its cost is the missed detection
    of a `20 percent`→`20 seconds` rewrite — the fail-OPEN direction. Both
    halves are asserted below, so neither the exception nor its cost is implied.

    ⛔ This reds the moment a lane adds a spelling to `_UNIT_EQUIV` and forgets
    `_UNIT_WORDS` — the exact edit that would silently re-create residual (d).
    """
    assert set(v2._UNIT_WORDS) <= set(v2._UNIT_EQUIV)          # the other direction
    assert set(v2._UNIT_EQUIV) - set(v2._UNIT_WORDS) == {
        "%", "percent", "pct"}, sorted(
        set(v2._UNIT_EQUIV) - set(v2._UNIT_WORDS))
    # the two routes `%` arrives by, which is WHY it cannot be fused: a BARE
    # numeral is merged by the tokenizer...
    assert "20%" in v2._guard_token_seq(v2._canonicalise_clocks("the drop was 20 %"))
    assert "20%" in v2._guard_token_seq(v2._canonicalise_clocks("the drop was 20\t%"))
    assert v2._value_core("20 %") == ("", "20", "%")
    assert v2._value_core("20%") == ("", "20", "%")
    # ...but a PREFIXED one is NOT (the symbol stays a separate token, and the
    # attached form is dropped entirely), so fusing `percent` would compose one
    # side and not the other
    assert v2._guard_token_seq(v2._canonicalise_clocks("the drop was 20k %")) == [
        "the", "drop", "was", "20k", "%"]
    assert v2._guard_token_seq(v2._canonicalise_clocks("the drop was 20k%")) == [
        "the", "drop", "was", "20k"]
    # the fail-OPEN consequence, pinned so it is a stated choice not an accident
    for content, quote in (
        ("the drop was 20 percent", "the drop was 20 seconds"),
        ("the drop was 20k percent", "the drop was 20k %"),
        ("the value was 20k%", "the value was 20000 percent"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)
    # positive control: the invariant's set is not trivially satisfiable by an
    # inert table — the completions this issue added are all live
    for word in ("minute", "second", "seconds", "hour", "millisecond",
                 "milliseconds", "msec", "msecs", "mile", "miles", "ms"):
        assert word in v2._UNIT_WORDS, word
    # …and the three the row leaves UNFUSED on purpose are absent, so this pin
    # cannot be satisfied by quietly fusing them again
    for word in ("%", "percent", "pct"):
        assert word not in v2._UNIT_WORDS, word


def test_a_magnitude_prefix_COMPOSES_with_the_unit_word_that_follows_it():
    """#10360 review cycle 1, P2 — the false accusation completing the table reached.

    `_value_bindings` fuses a unit WORD that follows a value onto it. On a value
    that already carried a magnitude prefix that produced `20k` + `miles` →
    `20kmiles`, whose suffix maps to nothing: `_value_core` returned an UNKNOWN
    unit, the `k` reading was lost, and the pair diverged on the numeric CORE
    (20 vs 20000) — reaching the write boundary and withholding a truthful
    point, which is the false-accusation direction this rule must never take.

    Reproduce by reverting `_compose_magnitude_and_unit` in the fusion branch:
    the first row below REFUSES and the write path drops the point.

    ⚠️ The shape PRE-EXISTED for every already-fusable unit — `5k min` against
    `5000 min` was refused on the pre-change tree — so this is a repair of that
    class too, not only a hedge around the new vocabulary.
    """
    # a prefixed value and a plain one name the SAME number once composed
    for content, quote in (
        ("the fleet drove 20000 miles", "the fleet drove 20k miles"),
        ("the wait was 5000 min", "the wait was 5k min"),
        ("the wait was 20500 seconds", "the wait was 20.5k seconds"),
        ("the timeout was 3000 ms", "the timeout was 3k ms"),
        ("the retry took 2000 hours", "the retry took 2k hours"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # …and the composition did not buy that by going blind: a DIFFERENT number
    # behind the same prefix is still refused
    for content, quote in (
        ("the fleet drove 20000 miles", "the fleet drove 21k miles"),
        ("the wait was 5000 min", "the wait was 6k min"),
        ("we ran 20k", "we ran 21km"),
        ("the delta is -20k", "the delta is 20km"),
    ):
        assert v2._value_slot_divergence(content, quote), (content, quote)

    # the pre-existing FAIL-OPEN rows the composition must not disturb
    for content, quote in (
        ("we saw 5k QPS", "we saw 5000 QPS"),      # `QPS` is not a fused unit
        ("the drive is 20k", "the drive is 20km"),  # `k` as a bare unit word
        ("the value is 20kms", "the value is 20km"),  # ONE token: still kilometres
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # end to end: the truthful point is WRITTEN, not withheld
    result = _write("the fleet drove 20000 miles", "the fleet drove 20k miles")
    assert result["payload"]["points"], result["warnings"]


def test_the_composition_reads_a_SIGNED_or_SPACED_prefix_as_the_same_value():
    """#10360 review cycles 2 and 3 — the shapes a private regex kept missing.

    The first composition required a bare ``\\d+`` core, so spellings the module
    ALREADY reads as one value fell to the raw glue and became an unknown unit
    again — a truthful point withheld:

    * **the sign** (`_reads_as_sign`): `-20k` + `miles` → `-20kmiles` → the `k`
      reading is lost and the pair diverges on the CORE (-20 vs -20000);
    * **the leading dot** (`_LEADING_DOT`: "``.5`` is a legitimate written form of
      ``0.5``"): `.5k miles` → `.5kmiles`, same shape, against `500 miles`;
    * **a SPACED prefix**: `k` is itself a `_UNIT_WORDS` member, so `20 k min`
      fused `k` and STOPPED — the unit word after it was never reached, leaving
      `('20k')` against `('20000min')`. The cycle-2 reviewer measured 260 refused
      pairs of this single shape over its own spelling grid.

    ⛔ And a THIRD revision still had it wrong (#10360 review cycle 3, P1): it
    admitted only `-`/U+2212 while `_reads_as_sign` admits the whole `Pd` family
    (en/em dash, hyphen, U+2011) and `_value_core` skips every `Sc`/`Sm`
    decoration (`~ + > < $ ≈ ±`) — the sign/decoration reading now comes from
    `_value_core` itself, so it cannot drift again.
    """
    # the sign, in every glyph family the module treats as one sign
    for content, quote in (
        ("the delta is -20000 miles", "the delta is -20k miles"),
        ("the delta is \u221220000 miles", "the delta is \u221220k miles"),
        ("the delta is \u201320000 miles", "the delta is \u201320k miles"),
        ("the delta is \u201420000 miles", "the delta is \u201420k miles"),
        ("the delta is \u201120000 miles", "the delta is \u201120k miles"),
        ("the delta is -5000 min", "the delta is -5k min"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # …and the decorations `_value_core` ignores are ignored here too
    for content, quote in (
        ("the fleet drove ~20000 miles", "the fleet drove ~20k miles"),
        ("the fleet drove 20000 miles", "the fleet drove ~20000 miles"),
        ("the fleet drove +20000 miles", "the fleet drove +20k miles"),
        ("the fleet drove \u224820000 miles", "the fleet drove \u224820k miles"),
        ("the fleet drove \u00b120000 miles", "the fleet drove \u00b120k miles"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # the leading dot — the same value `_LEADING_DOT` already accepts
    for content, quote in (
        ("the drive was 500 miles", "the drive was .5k miles"),
        ("the drive was 500 miles", "the drive was 0.5k miles"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # a prefix written APART, and a prefix spelled as a number word
    for content, quote in (
        ("the wait was 20000 min", "the wait was 20 k min"),
        ("the fleet drove 20000 miles", "the fleet drove 20 k miles"),
        ("the wait was 20000 min", "the wait was twenty k min"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # ⛔ a NON-ASCII decimal script is the same value (#10513 cycle 16 pins the
    # fold; this pins that the composition reads through it — cycle 6, P2:
    # an ASCII-only head class refused `２０k` and withheld the truthful point)
    for content, quote in (
        ("the fleet drove 20000 miles", "the fleet drove \uff12\uff10k miles"),
        ("the fleet drove 20000 miles", "the fleet drove \u0662\u0660k miles"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # ⛔ the `_MAGNITUDE_TAIL` POSITIVE branch, pinned (#10360 review cycle 5,
    # P3): `20k k miles` already carries a prefix, so the standalone `k` must
    # NOT be consumed a second time. Forcing that guard to always fire yields
    # `20kkiles` here instead of `20000k`, and nothing else in this file noticed.
    assert v2._value_bindings("the value was 20k k miles") == (
        ("value", "20000k"),), v2._value_bindings("the value was 20k k miles")

    # …and the WHOLE same-value spelling grid, which is how the 260 were found:
    # no spelling of one number may be refused against another. 9 fused unit
    # SPELLINGS (7 identities — `miles`/`mi` and `min`/`minutes` share one each)
    # × 6 forms × 6 forms, 54 of them the trivial `a == b` diagonal.
    for unit in ("miles", "mi", "km", "seconds", "min", "minutes", "ms",
                 "hours", "kg"):
        for a in ("20000", "20k", "20 k", "twenty k", "20K", "20.0k"):
            for b in ("20000", "20k", "20 k", "twenty k", "20K", "20.0k"):
                assert v2._value_slot_divergence(
                    f"the value was {a} {unit}",
                    f"the value was {b} {unit}") == (), (a, b, unit)

    # the composition still is not blind: a different number behind the sign
    for content, quote in (
        ("the delta is -20000 miles", "the delta is -21k miles"),
        ("the drive was 500 miles", "the drive was .6k miles"),
    ):
        assert v2._value_slot_divergence(content, quote), (content, quote)


def test_the_composition_does_not_smuggle_the_SEPARATOR_decision_back_in():
    """#10360 review cycle 3 — a crash and a recorded decision, both measured.

    `_compose_magnitude_and_unit` reads the value through `_value_core`, which
    returns `None` for a token it deliberately does NOT read: a SEPARATOR-bearing
    numeral (`1,200`). Unguarded, the composition raised

        TypeError: cannot unpack non-iterable NoneType object

    on `latency was 1200 ms` against `latency was 1,200 ms` — a BATTERY row, so
    the suite caught it. ⛔ The guard must also not REFUSE those pairs: deciding
    that `1,200` is 1200 IS the locale question `tortoise/value_normalizer.py`
    excludes by the owner's #2817 ruling ("No separator rule, at all"), and a
    unit fused onto the numeral would smuggle that decision back in through the
    unit. So a `None` identity leaves the token unchanged, and the pair stays
    outside the judged set.
    """
    assert v2._value_core("1,200") is None
    assert v2._value_core("1.234") is None
    for content, quote in (
        ("latency was 1200 ms", "latency was 1,200 ms"),
        ("latency was 1200 minutes", "latency was 1,200 minutes"),
        ("the p95 hit 4,2 seconds", "the p95 hit 4.2 seconds"),
        ("the drive was 1.234 km", "the drive was 1234 km"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)
    # …while the SAME unit on an unseparated pair is still judged, so the guard
    # did not simply disable the composition for anything with a unit
    assert v2._value_slot_divergence("latency was 1200 ms", "latency was 1300 ms")


def test_an_IDENTIFIER_shaped_token_is_not_read_as_a_quantity():
    """#10360 review cycle 5, P3 — the composition multiplied an ARRAY INDEX.

    `_value_core` reads the LAST numeric run of ANY token, so the
    identifier-shaped `s4[k` also returns core `4` + suffix `k`. Composing it
    with a following unit word turned an array index into 4000 — measured on this
    repo's own docs corpus (`docs/plans/2026-08-27-1789-s4-delta-contract.md`),
    which is `_value_core`'s own doctrine about never inventing a second
    interpretation of one token (#2817). `_NUMERIC_HEAD` requires the token —
    after its leading decoration — to START with a digit or a dot, so an
    identifier keeps the pre-change reading.

    ⛔ This test exists because the guard was UNPINNED when it landed: mutation
    (`_NUMERIC_HEAD` replaced with an always-matching pattern) left the whole
    file green (#10360 review cycle 6, P3).
    """
    # an identifier-shaped token keeps the OLD reading — no 4000-style core
    ident = v2._value_bindings("any(_is_content_list(s4[k k")
    assert ident == (("", "any(_is_content_list(s4[kk"),), ident
    assert v2._value_core(v2._value_bindings("the see was x4k ms")[0][1])[1] == "4"
    # …while every genuine quantity shape still composes (a decorated value and a
    # leading-dot one), so the head guard did not simply disable the composition
    assert v2._value_core(
        v2._value_bindings("the value was $20k miles")[0][1]) == (
            "", "20000", "mi")
    assert v2._value_core(
        v2._value_bindings("the value was .5k miles")[0][1]) == (
            "", "500", "mi")


def test_the_registry_rows_measured_disclosures_are_pinned():
    """The four residual clauses named at the row's ⚠️ paragraph are pinned here.

    The `state_value_carve_out` row is the residual ledger an auditor reads
    ("⛔ WHAT IS STILL NOT ENFORCED — read this before improving a number"). A
    disclosure in that row with no pin can drift silently: closing the behaviour
    leaves the row asserting the old state, and re-breaking it leaves the row
    claiming a shape it no longer has — the "guarantee documented, mechanism
    absent" defect #10360 exists to remove.

    ⛔ WHAT THIS TEST DOES **NOT** CLAIM: that every clause of that row is now
    pinned. Residuals (a) and (b2) are unpinnable in a unit test by construction
    (a value the model never EMITS is upstream of selection; the survival target
    needs an E2 LLM arm run), and (e)–(o) are pinned where they live or are
    out of this test's scope. What it pins is the set the row's ⚠️ paragraph
    names as a battery: (c) the `4:32` non-clock, (d) `per <unit>` rates,
    (d2) the identifier fail-open, and `20k-mile`/`20000 miles`.

    ⛔ TWO PAIRS IN THAT BATTERY ARE **RE**-PINS, and that is deliberate rather
    than an oversight: `forty thousand requests per day`/`per week` is already
    asserted by `test_the_carve_outs_own_ADJACENT_time_units_are_judged`, and
    `UTC+03:00`/`UTC+3` by
    `test_a_clock_valid_colon_form_is_excluded_but_a_duration_is_judged`. The
    rest — `4:32` in both directions, `1:23 seconds`/`83 seconds`, `27:12`,
    `x4k ms`, `20k-mile` and the two range refusals — had no pin before this
    test. Repeating the two keeps ONE place that names the whole clause set, so
    a lane editing a clause finds its verdict beside its siblings.

    ⛔ EVERY VERDICT BELOW WAS MEASURED AT `origin/main` `277a559b1`, this file's
    own head, and each assert that holds a row clause names it. NINE of them are
    REFUSALS (`:MM` with a fused unit, its pre-existing `27:12` spelling, an
    identifier against a quantity, a different identifier, an identifier with a
    PLAIN unit tail against two different quantities — `x4 ms`/`5000 ms` and
    `arr4 ms`/`9000 ms` — an identifier whose TAIL spells a known unit
    (`arr4ms`/`4 seconds`), and the two range comparisons) and the TWELVE others
    are TOLERANCES — a tolerance is not a bug, but an UNDISCLOSED or
    UNPINNED tolerance is how a future narrowing ("why does this pair fail
    open?") quietly reverses a measured decision.
    """
    # (c) a colon form that is a valid CLOCK SHAPE without being a clock — the
    # row's own example, `4:32` read as a marathon time — is NOT caught, and
    # neither is the same value converted to seconds. The exclusion is by SHAPE,
    # so `4:32`/`3:59` is outside the judged set on both sides.
    assert v2._value_slot_divergence("the marathon was 4:32",
                                     "the marathon was 3:59") == ()
    assert v2._value_slot_divergence("the marathon was 4:32",
                                     "the marathon was 272 seconds") == ()
    # …and the clock-VALID exclusion is the only one that may fire: the row
    # records that a BARE-shape arm REFUSED this pair (one offset spelled two
    # ways), which is why the CLOCK-VALID rung is the one that landed.
    assert v2._value_slot_divergence("the offset was UTC+03:00",
                                     "the offset was UTC+3") == ()

    # (d) a RATE unit written as `per <unit>` — the carve-out's own fifth
    # example — is not judged: no unit is ADJACENT to the number, so nothing is
    # fused and the pair fails open. Pinned in both spellings, and the mechanism
    # is asserted directly so a future adjacency widen cannot silently "fix" the
    # row and leave it saying the opposite.
    assert v2._value_bindings("we served 40000 requests per day") == (
        ("served", "40000"),)
    for content, quote in (
        ("we served 40000 requests per day", "we served 40000 requests per week"),
        ("we served forty thousand requests per day",
         "we served forty thousand requests per week"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)

    # the REFUSALS the row discloses, kept adjacent to the tolerances so no row
    # edit can move one without the other:
    #   * `1:23 seconds`/`83 seconds` — `_CLOCK_FORM` anchors on `$`, so a colon
    #     token with a FUSED unit word after it is not a clock: the core is the
    #     RATIO `1/23` with unit `s` (a conversion is a different identity);
    #   * `27:12 minutes`/`27:12 seconds` — the pre-existing spelling of the same
    #     shape, which is what proves the rule was always there.
    assert v2._value_slot_divergence("the lap took 1:23 seconds",
                                     "the lap took 83 seconds") == (
        ("took", "1/23s", "83s"),)
    assert v2._value_slot_divergence("the lap took 27:12 minutes",
                                     "the lap took 27:12 seconds") == (
        ("took", "27/12min", "27/12s"),)

    # (d2) AN IDENTIFIER-SHAPED value beside a unit word fails OPEN (#10943, which
    # records this clause as `(q2)`), and it fails open by INVENTING A SCALE
    # rather than by declining to judge: `x4k ms` binds `x4kms` (core `4`, suffix
    # `kms`), `_composed_identity` folds `k`+`ms` and multiplies to `4000 ms`, and
    # the pair then AGREES. So the assert below does not hold because an array
    # index is outside the judged set — measured, both sides bind slots — and the
    # refusal against `5000 ms` shows the pair IS judged. The TOKEN-level reading
    # is held separately by
    # `test_an_IDENTIFIER_shaped_token_is_not_read_as_a_quantity`
    # (`_NUMERIC_HEAD` keeps `x4k` from being read as 4000 in place); the GLUED
    # token is not guarded by it, and that gap is the residual.
    # ⛔ THESE pairs are the mutation-sensitive ones: drop the `x1000`
    # multiplication — an OVER-BROAD stand-in for the #10943 fix, which scopes the
    # fold by the token's HEAD, so under that fix `20k-mile` (head `2`) still
    # composes and stays tolerated — and both the `x4k ms`/`4000 ms` pair below and
    # its `s4[k ms` splice flip to a refusal `('see', '4kms', '4000ms')`; the
    # `20k-mile`/`20000 miles` tolerance one block further down flips too, which is
    # why the mutation is NOT a fix: it withholds a truthful point. The `5000 ms`
    # refusal below does NOT move under that mutation — it is over-determined (core
    # `4` vs `5000` and unit `kms` vs `ms` already differ before the fold) — so it
    # is evidence that the pair is judged, never evidence about the multiplication.
    assert v2._value_slot_divergence("the see was x4k ms",
                                     "the see was 4000 ms") == ()
    assert v2._value_slot_divergence("the see was s4[k ms",
                                     "the see was 4000 ms") == ()
    assert v2._value_slot_divergence("the see was x4k ms",
                                     "the see was 5000 ms") == (
        ("see", "4kms", "5000ms"),)
    assert v2._value_slot_divergence("the see was x4k ms",
                                     "the see was x5k ms") == (
        ("see", "4kms", "5kms"),)
    # ⛔ SCOPE, pinned (#10943 review cycle 2): (d2) is the PREFIX-TAIL shape, not
    # a blanket identifier tolerance. An identifier followed by a PLAIN unit word
    # is judged normally — `x4 ms` against a different quantity REFUSES, and
    # `arr4 ms` against `9000 ms` REFUSES — while `x4 ms` against the SAME value
    # tolerates. A future fix that broadens (d2) into "identifiers are not judged"
    # has to move these.
    assert v2._value_slot_divergence("the see was x4 ms",
                                     "the see was 5000 ms") == (
        ("see", "4ms", "5000ms"),)
    assert v2._value_slot_divergence("the field was arr4 ms",
                                     "the field was 9000 ms") == (
        ("field", "4ms", "9000ms"),)
    assert v2._value_slot_divergence("the see was x4 ms",
                                     "the see was 4 ms") == ()

    # ⛔ AND THE SUFFIX IS READ AS A UNIT WITH NO IDENTIFIER/UNIT DISTINCTION,
    # which is what the row's THE DISCRIMINATOR paragraph states: the identifier's
    # own letters reach the identity through the SUFFIX slot, so a tail drives BOTH
    # directions. A tail that SPELLS a known unit is compared AS that unit —
    # `arr4ms` IS `4 ms`, so against `4 seconds` it REFUSES — while a tail that
    # spells NOTHING in `_UNIT_EQUIV` leaves the unit UNJUDGEABLE on equal cores,
    # the module's own unknown-unit tolerance, so `arr4zz` against `4 hours` fails
    # open. Both sides BIND in that tolerance (`_value_bindings` yields a token on
    # each side), which is what makes it a FAIL-OPEN rather than an absent
    # binding. Only the `k` tail is ADDITIONALLY read as a SCALE — that is (d2)
    # above and arm (i)'s bare shape — so the row must not be read as saying these
    # are the only tails that reach the identity: that is how a lane scoping the
    # #10943 fix to the `k` shape misses this class entirely.
    # ⛔ MUTATION, measured: narrowing the SUFFIX read by the token's HEAD (drop
    # the suffix when the token does not start with a numeral — the `_NUMERIC_HEAD`
    # scope applied one slot too far, an OVER-BROAD stand-in for #10943) reads
    # `('', '4', None)` and flips the `4 seconds` refusal to a tolerance, so the
    # two asserts below it red. The `arr4zz` fail-open does NOT move under that
    # narrowing, which is precisely why a `k`-scoped fix does not close it.
    assert v2._value_core("arr4ms") == ("", "4", "ms")
    assert v2._value_core("arr4zz") == ("", "4", "zz")
    assert v2._value_slot_divergence("the field was arr4ms",
                                     "the field was 4 seconds") == (
        ("field", "4ms", "4s"),)
    assert v2._value_slot_divergence("the field was arr4zz",
                                     "the field was 4 hours") == ()

    # a HYPHENATED compound is ONE token, so it never reaches the fusion arm and
    # the composition folds `kmile` into `k`+`mile`: `20k-mile`/`20000 miles` is
    # the SAME value, one spelling. It did DIVERGE before #10801 (core `20` +
    # unit `kmile` against core `20000`), which WITHHELD a truthful point.
    assert v2._value_slot_divergence("the drive was 20k-mile",
                                     "the drive was 20000 miles") == ()
    # ⛔ …and it did NOT buy that by reading a RANGE as one quantity: a
    # hyphenated range carries ONE suffix for several numbers, so the core stays
    # `20/30` and a different range still refuses. This is the hazard that keeps
    # a blanket prefix peel out of `_value_core`, and the two refusals below are
    # the second half of the `20k-mile` clause above.
    assert v2._value_core("20k-30kmiles") == ("", "20/30", "kmiles")
    assert v2._value_slot_divergence("the drive was 20k-30k miles",
                                     "the drive was 20000 miles")
    assert v2._value_slot_divergence("the drive was 20k-30k miles",
                                     "the drive was 20k-40k miles")

    # CONTROLS, holding row (i)'s `k`-as-unit identity: no clause above may be
    # satisfied by a rule that refuses every `Nk` — the `k`-abbreviation bridge
    # still lets `3k` name `3000 km`/`3000 kg`.
    for content, quote in (
        ("the load is 3000 kg", "the load is 3k"),
        ("the trip is 3000 km", "the trip is 3k"),
    ):
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)


# ── 24. the FAIL-OPEN half, measured: a detection gap is not a lost value ───
#
# `test_the_residual_is_measured_on_the_committed_census_corpus` (§5) pins the
# rule's REACH — 62 judged, 0 refused. This section pins what the other 50 are,
# because the registry row's (b) reads "50 of the 112 … fail open on slot
# layout", and a lane improving that number has to know whether it is looking at
# 50 LOST VALUES (a retention defect — #10360's Target 2) or at values the
# rule's own slot projection cannot ALIGN (a detection gap, nothing lost). The
# two readings license OPPOSITE work — the first a wider rule, the second a
# narrower one — and nothing measured the difference, so the row could only
# state the count.
#
# ⛔ THE ANSWER ON THIS CORPUS IS THE SECOND READING. Of the 50:
#    * 29 state EVERY value their own quote states — under a slot layout
#      `_value_slot_divergence` cannot align, so it fails open on a RETAINED
#      value. (This is what the row's "fail open on slot layout" means.)
#    * 2 differ only on the determiner `one` — the quote's slot is the word
#      "one", which the engine binds as a quantity. Neither is a state value;
#      both are the SAME sentence in the two arms.
#    * 7 state the value in a form the row EXCLUDES BY DESIGN (residual (c)):
#      4 carry a clock (`2am`, `02:00`, `9:40am`/`1:05pm`) and 3 an ISO date
#      (`2026-11-02`, `2026-11-09`, `2026-10-09`). ⛔ The class ALSO covers the
#      third excluded form — a separator-bearing numeral (`1,234`, `#2817`'s
#      "no separator rule at all") — which this corpus happens not to contain: a
#      row whose only value is separator-spelled would otherwise fall into
#      `CONTENT_NO_VALUE`, whose quote slot is counted as a DROPPED value, i.e.
#      a positive claim about text the projection never read. ⛔ The class is
#      assigned from
#      the CONTENT alone — it says the value is stated in a FORM the rule
#      excludes, NEVER that it was checked against the quote; residual (c) is
#      exactly that a clock/ISO REWRITE is not caught, so quote agreement is not
#      something this class may claim (6 of the 7 do restate the quote's value —
#      3 in a NORMALISED form (`02:00` for `2am`, `2026-11-02` for `November 2`,
#      `2026-11-09` for `November 9`) and 3 in the quote's OWN spelling; the
#      seventh's quote binds no value at all, `last night`).
#      This is why the row's STATED figure is 91 (62 ALIGNED + 29
#      LAYOUT_RETAINED) and NOT 98: the 7 are form-excluded, not checkable.
#    * 8 state no value at all, and 7 of those quote no STATE value either (a
#      determiner, or an empty slot).
#    * 4 have no slot on the QUOTE side — nothing to retain.
# ⇒ exactly ONE of the value-bearing points whose drop this projection can SEE
# drops a state value its own quote carries: the 8 `CONTENT_NO_VALUE` and the 2
# `LAYOUT_LOST` rows — the classes where the content binds no slot, or binds the
# wrong one. One, not fifty.
#
# ⛔ TWO DENOMINATORS, BOTH STATED, NEITHER GLOSSED (#11730 review, cycles 8–9):
#   * **10** — the rows where a dropped value is VISIBLE at all (8 + 2). "ONE"
#     is a count over these, not over 112.
#   * **105** — the rows this projection can CHECK at all (112 − the 7
#     `CONTENT_EXCLUDED_FORM`). A value dropped while the content keeps its
#     CLOCK is invisible to a projection that binds no slot for a clock token,
#     which is residual (c) itself; the ISO half of that class IS checkable and
#     is checked below (`iso_bound`, by month-and-day agreement). The blind
#     spot's size is pinned (`len(excluded) == 7`, `len(iso_bound) == 2`) so it
#     cannot grow silently.
#
# ⚠️ SCOPE, so this is not read as a survival figure: this is the COMMITTED
# census corpus (7 sealed sessions × 2 arms, real `deepseek-v4-flash`
# extractions) measured through the SHIPPED helpers. It is NOT the flags-on E2
# arm, so it neither confirms nor contradicts the 54.4% baseline in the row's
# (b2) — that number needs an E2 LLM arm run and stays `UNMEASURED`.
#
# The classifier is deliberately THIN and reuses the shipped predicates
# (`_carries_value`, `_value_slots_scaled`, `_write_span`) rather than minting a
# second value layer — the discipline `_value_slot_divergence` is held to. The
# clock/ISO detector is LOCAL to this section on purpose: it classifies a
# residual the row already declares, and is not a new value predicate.
#
# ⛔ WHAT IT DELIBERATELY CANNOT JUDGE: a MAGNITUDE difference. The engine has no
# magnitude table (`#2817` rules one out; residual (m) records that `12 million`
# against `12 billion` is unfused), so `12 million` and `12 thousand` bind the
# SAME core and differ only on the slot's scale — and `12000000` binds a
# different core from `12 million` with no scale at all. Neither pair is a value
# this classifier can call LOST or RETAINED without adding the very table the
# recorded ruling excludes, so both land in `LAYOUT_UNJUDGED`.

_CLOCK_FORM = re.compile(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b|\b\d{1,2}\s*(?:am|pm)\b",
                         re.IGNORECASE)
_ISO_DATE_FORM = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
#: A separator-bearing numeral (`1,234`, `1.234`) is the THIRD form the
#: engine puts outside the judged set (`_separator_reads_ambiguously`, the
#: recorded #2817 decision: no separator rule at all). Without it a row whose
#: only value is separator-spelled fell into `CONTENT_NO_VALUE`, whose quote
#: slot then counted as a DROPPED value — a positive claim about text the
#: projection never read (#11730 review, cycle 12). Census-inert: no row on
#: this corpus carries the form, so the split is unchanged.
_SEPARATOR_FORM = re.compile(r"\d[,.\u00a0 ]\d{3}(?!\d)")

#: The spelled month names the corpus's quotes use, for the one agreement check
#: the excluded-form class CAN carry: an ISO date in the content against the
#: month-and-day the quote spells out (`2026-11-02` against `November 2`).
_MONTH_NUMBERS = {name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), start=1)}
_SPELLED_DATE = re.compile(
    r"\b(" + "|".join(_MONTH_NUMBERS) + r")\s+(\d{1,2})\b", re.IGNORECASE)


def _stated_month_day(text: str) -> tuple[int, int] | None:
    """The month and day a text states, as numbers, in either form.

    `2026-11-02` and `November 2` both give ``(11, 2)``; the identity is what
    lets the excluded-form class CHECK agreement instead of asserting presence
    (review cycle 9, P1: `"2" in content` is a substring search over a
    one-digit core, so a content rewritten to `2026-12-25` stayed green).
    """
    iso = _ISO_DATE_FORM.search(text)
    if iso:
        _year, month, day = iso.group(0).split("-")
        return int(month), int(day)
    spelled = _SPELLED_DATE.search(text)
    if spelled:
        return _MONTH_NUMBERS[spelled.group(1).lower()], int(spelled.group(2))
    return None

#: A bare count of the sentence's own nouns ("one risk", "two workers") is not a
#: state value: the engine binds it as a quantity, which is why the determiner
#: rows are pinned by IDENTITY below rather than filtered on this shape alone —
#: a bare `1`/`2` that IS a state value ("the retry limit is 2") has the same
#: core, so a shape-only skip would hide its drop.
_DETERMINER_CORES = ("('', '1', None)", "('', '2', None)")

#: Every row on this census whose quote's ONLY value is a determiner, pinned by
#: identity. `_DETERMINER_CORES` skips these from the dropped-value count, so the
#: skip itself is pinned: a new bare `1`/`2` row that is a real state value
#: appears here and reds, instead of vanishing behind the shape filter.
DETERMINER_ROWS = frozenset({
    ("wp01_quarry_debug", "narrative", "claiming", "('', '1', None)"),
    ("wp01_quarry_debug", "narrative", "prevents", "('', '2', None)"),
    ("wp04_aurora_perf", "narrative", "point", "('', '1', None)"),
    ("wp04_aurora_perf", "raw", "", "('', '1', None)"),
    ("wp06_quarry_rollout", "narrative", "flag", "('', '1', None)"),
    ("wp07_bluepeak_followup", "narrative", "instead", "('', '1', None)"),
})


def _census_row(point: dict) -> tuple[str, str]:
    """The pair the WRITE stores for a committed point (the §5 inputs)."""
    return (v2._write_span(str(point.get("content") or "").strip(),
                           v2._MAX_CONTENT),
            v2._write_span(str(point.get("quote") or "").strip(),
                           v2._MAX_QUOTE))


def _value_pairs(text: str) -> list[tuple[str, str]]:
    """``(anchor, core)`` for every slot, as strings, for identity pins."""
    return [(anchor, str(core)) for anchor, core, _scale
            in v2._value_slots_scaled(text)]


def _digits(core: tuple) -> str:
    """A core's digit string, with the decimal point dropped.

    `1.2` and `1200000` are the same number written as a decimal scale and as
    digits; dropping the point makes them comparable by the padding relation
    below without any magnitude arithmetic.

    ⛔ THIS NEVER RETURNS AN EMPTY STRING. A core with no digits, and the literal
    `0`, both collapse to `"0"` rather than to `""` — so `_same_magnitude_number`
    tests `left == right` and NOT `not left or not right`: that guard was
    unreachable, because `_value_slots_scaled` yields a slot only when it binds
    digits, so every core that reaches here carries some. A guard no caller can
    reach is surface no mutant can red, which is the class this PR removes
    (review cycle 11, found while re-verifying cycles 8-10). The collapse is
    pinned directly in §24 item 2f, because the census exercises neither shape.
    """
    return str(core[1]).replace(".", "").lstrip("0") or "0"


def _same_magnitude_number(one: tuple, other: tuple) -> bool:
    """Are two cores the same number at a different MAGNITUDE?

    `12` (beside the scale word `million`) and `12000000` are one value written
    two ways: the longer digit string is the shorter one followed by nothing but
    zeros. That is a STRING relation, not a magnitude table — `#2817` rules out
    folding magnitudes (residual (m)), and this decider never folds one: it only
    separates "a magnitude is in play" from "a value is missing".

    ⛔ EQUAL digits are NOT this relation (review cycle 9): `3 batches` in a
    quote and `3 million events` in the content share the digits and are not the
    same value, so treating equality as a magnitude pair let one unrelated
    scaled slot excuse an outright drop. Equality is only a magnitude question
    when BOTH spellings carry a scale word, which the caller tests.
    """
    left, right = _digits(one), _digits(other)
    if left == right:
        return False
    longer, shorter = ((left, right) if len(left) > len(right) else (right, left))
    return longer.startswith(shorter) and set(longer[len(shorter):]) <= {"0"}


def _identity_agrees(content_core: tuple, content_scale: str | None,
                    quote_core: tuple, quote_scale: str | None) -> bool:
    """Does the engine's own tolerance accept these two spellings as one value?

    `20 percent` against `20%` is one value: the shipped
    `_value_identity_matches` allows one side to omit a UNIT, and the census
    must not accuse a pair the rule would pass (#11730 review, cycle 11 — exact
    core equality filed that truthful restatement as a LOSS once the anchor
    moved).

    ⛔ A SCALE WORD IS NOT A UNIT, so the tolerance is not applied where either
    side carries one: `3 million` and `3` are not "the same number with the unit
    omitted", and letting the identity predicate see them made the magnitude
    class unreachable — `12 thousand` against `12 million` came back RETAINED
    because both scales are unknown units to that table. Scale words are
    outside the judged set upstream (`_SCALE_WORDS`), and this decider keeps
    them outside too.
    """
    if content_scale or quote_scale:
        return False
    return v2._value_identity_matches(content_core, quote_core)


def _magnitude_in_play(missing: list[tuple], content_slots: list[tuple]) -> bool:
    """Is EVERY missing value a magnitude this projection cannot compare?

    `missing` are the quote slots no content slot accounts for; `content_slots`
    are the slots LEFT OVER after each quote slot has consumed one match, so a
    duplicate cannot satisfy two quote values (review cycle 9: `stated` was a
    SET, so `we shipped 2 bugs` against `… and 2 fixes` counted as retained).

    A magnitude is in play only where the missing slot has a COUNTERPART among
    the leftovers:
      * the two sides carry DIFFERENT scale words (`12 thousand` against
        `12 million`, and `2000 thousand` against `2 million`, where the
        mantissas differ too) — two spellings of one magnitude, no way to say
        which is right. The SAME scale word with a different mantissa is not
        this case: the scale cancels, the mantissa is what differs, and the
        shipped `_value_slot_divergence` JUDGES that pair — a classifier that
        called it unjudged would refuse a judgement the rule makes;
      * the digits are zero-extended (`12000000` against `12 million`) — one
        value, a numeric form and a scaled one, with a scale word on one side.

    ⛔ EVERY missing slot must be explained, not just one (review cycle 10):
    returning True on the first explained pair certified the whole row, so a
    row whose OTHER missing value had no counterpart at all — `we shipped 3000
    events` against `… 30 batches and 12 events` — was filed as a magnitude
    question although `12 events` is simply dropped. An unrelated slot's scale
    is not a counterpart whatever its anchor, and an unscaled quote value
    against a scaled content value is not one either (`3 batches` against
    `3 million events` is a DROP, not a magnitude).
    """
    for _anchor, quote_core, quote_scale in missing:
        explained = False
        for _ca, content_core, content_scale in content_slots:
            if quote_scale and content_scale:
                # ⛔ TWO DIFFERENT SCALE WORDS ARE A MAGNITUDE QUESTION whatever
                # the mantissas are (#11730 review, cycle 12): `2000 thousand`
                # against `2 million` is the SAME value, and requiring equal
                # digits filed it as a positive claim — a value ABSENT — which
                # this projection cannot support. The SAME scale word is not a
                # magnitude question: there the scale cancels, the mantissa is
                # what differs, and the shipped rule JUDGES it
                # (`1.2 million` against `12 million`).
                if quote_scale != content_scale:
                    explained = True
                    break
            elif ((quote_scale or content_scale)
                  and _same_magnitude_number(content_core, quote_core)):
                # ⛔ A SCALE WORD MUST BE ON ONE OF THE TWO SIDES (#11730 review,
                # cycle 11): `2000 batches` against `2 batches` is a 1000x
                # DIFFERENT number with no scale word anywhere, and the shipped
                # rule JUDGES that pair — the zero-extension relation only
                # bridges a numeric form and a scaled one.
                explained = True
                break
        if not explained:
            return False
    return True


def _retention_class(content: str, quote: str) -> str | None:
    """Which half of the census a value-bearing point falls in.

    ALIGNED               — the rule judges it (`_value_slots_scaled` agrees).
    LAYOUT_RETAINED       — the layouts differ, but every value the quote binds
                            is still stated in the content (a DETECTION gap).
    LAYOUT_LOST           — the layouts differ and a value the quote binds is
                            not stated in the content.
    LAYOUT_UNJUDGED       — the layouts differ and a MAGNITUDE is in play, which
                            this projection cannot compare (#2817 rules out a
                            magnitude table; residual (m)). Neither retained nor
                            lost: unjudged.
    CONTENT_EXCLUDED_FORM — the content binds no slot, but states the value in
                            a form the row excludes by design ((c)): a CLOCK, an
                            ISO date, or a SEPARATOR-BEARING numeral (`1,234`),
                            the three forms `_value_form_outside_judged` puts
                            outside the judged set.
                            ⛔ NOT counted as STATED: the class reads the
                            content's FORM, never its agreement with the quote.
    CONTENT_NO_VALUE      — the content binds no slot and carries no excluded form.
    QUOTE_NO_SLOT         — the quote binds no slot: nothing to retain.

    ``None`` for a value-free point — the census counts value-bearing points only.
    """
    if not v2._carries_value(content, quote):
        return None
    content_slots = v2._value_slots_scaled(content)
    quote_slots = v2._value_slots_scaled(quote)
    if not content_slots:
        # ⛔ THE EXEMPTION IS GATED ON `not content_slots`, so it covers a row whose
        # ONLY value is written in an excluded form. A MULTI-value row that mixes
        # one excluded form with a bound value (`we have 1,234 events and 5000
        # nodes`) is still judged, and its excluded-form value is counted as a
        # LOSS — the false-accusation direction, recorded rather than fixed
        # because it is not reachable on this corpus (all 7 excluded rows bind
        # no slot at all) and `len(dropped) == 1` would red if such a row were
        # added (#11730 review, cycle 13).
        if (_CLOCK_FORM.search(content) or _ISO_DATE_FORM.search(content)
                or _SEPARATOR_FORM.search(content)):
            return "CONTENT_EXCLUDED_FORM"
        return "CONTENT_NO_VALUE"
    if not quote_slots:
        return "QUOTE_NO_SLOT"
    if (tuple((a, s) for a, _, s in content_slots)
            == tuple((a, s) for a, _, s in quote_slots)):
        return "ALIGNED"
    missing = []
    leftovers = list(content_slots)
    for anchor, core, scale in quote_slots:
        # ⛔ THE MATCH USES THE ENGINE'S OWN TOLERANCE, not string equality
        # (#11730 review, cycle 11): `20 percent` and `20%` are one value the
        # shipped rule tolerates, so exact-core matching called a truthful
        # restatement a LOSS as soon as the anchor moved. `_value_identity_matches`
        # is the same predicate `_value_slot_divergence` applies, so the census
        # cannot accuse a pair the rule would pass.
        found = None
        for index, (_ca, content_core, content_scale) in enumerate(leftovers):
            if (str(content_core), content_scale) == (str(core), scale):
                found = index
                break
            if _identity_agrees(content_core, content_scale, core, scale):
                found = index
                break
        if found is None:
            missing.append((anchor, core, scale))
        else:
            # consume ONE content slot per quote slot, so a duplicate value in
            # the quote cannot be satisfied twice by one content value
            leftovers.pop(found)
    if missing:
        # ⛔ A MAGNITUDE is not comparable here: `12 million` against
        # `12 billion` binds the same core (residual (m)), and `12000000`
        # against `12 million` binds two, with no magnitude table to fold them
        # (#2817 rules one out). Calling either LOST would accuse a truthful
        # point; calling it RETAINED would certify a six-orders drop. Unjudged.
        #
        # ⛔ AND THE SCALE TEST IS KEYED ON THE MAGNITUDE, NOT ON AN ANCHOR, in
        # both directions (#11730 review, three cycles):
        #   * an unrelated scale word must not launder a dropped value into
        #     "unjudged" — `we shipped 3 million events` / `… and 4 batches`
        #     loses `4 batches` outright, and so does `the east shard holds 2
        #     million records` / `… the west shard holds 3 records`, where the
        #     unrelated `2 million` shares the missing slot's own anchor (an
        #     anchor is the NEIGHBOURING TOKEN and repeats in a parallel clause);
        #   * a magnitude pair must not be filed as a DROP just because the
        #     anchor was reworded — `12000000` against `we are INGESTING 12
        #     million` is the same value in two forms;
        #   * a missing SCALED value with no counterpart at all (`3 million
        #     events` dropped from a content that kept `4 batches`) is a DROP,
        #     not a magnitude question.
        # `_magnitude_in_play` is a STRING relation, never a magnitude table, so
        # it decides "unjudged" vs "lost" without folding anything.
        if _magnitude_in_play(missing, leftovers):
            return "LAYOUT_UNJUDGED"
        return "LAYOUT_LOST"
    return "LAYOUT_RETAINED"


def _census_rows() -> list[tuple[str, str, str, str, str]]:
    """``(session, arm, class, content, quote)`` over the committed census."""
    rows = []
    for session in SESSIONS:
        blob = json.loads((RECEIPTS / f"{session}.arms.json").read_text())
        for arm in ARMS:
            for point in blob["embed_list"][arm]["points"]:
                content, quote = _census_row(point)
                klass = _retention_class(content, quote)
                if klass is None:
                    continue
                rows.append((session, arm, klass, content, quote))
    return rows


#: The split, measured at this head through the shipped helpers. Re-run
#: `_census_rows()` before touching a number: a lower ALIGNED or a higher
#: LAYOUT_LOST is the finding, never a constant to loosen.
RETENTION_SPLIT = {
    "ALIGNED": 62,
    "LAYOUT_RETAINED": 29,
    "LAYOUT_LOST": 2,
    "LAYOUT_UNJUDGED": 0,
    "CONTENT_EXCLUDED_FORM": 7,
    "CONTENT_NO_VALUE": 8,
    "QUOTE_NO_SLOT": 4,
}

#: The only two LAYOUT_LOST rows, pinned by IDENTITY rather than counted: both
#: are the determiner `one` ("… the one that added …") read as a quantity, in
#: the two arms of the SAME session — nothing here is a state value. A third
#: entry is a real loss, and it reds here before it reaches the row.
LAYOUT_LOST_ROWS = frozenset({
    ("wp04_aurora_perf", "raw", ("release", "('', '1', None)")),
    ("wp04_aurora_perf", "narrative", ("release", "('', '1', None)")),
})

#: The ONE point on the committed census that drops a state value its own quote
#: carries: the quote sizes a lease grace to thirty seconds; the point states the
#: policy and never the duration. Named, because a residual of ONE is actionable
#: and a residual of "50" is not.
THE_ONE_CONTENT_MISSING_VALUE = (
    "wp07_bluepeak_followup", "narrative", "grace", "('', '30', 's')")


def test_the_fail_open_half_is_MEASURED_so_a_gap_is_not_read_as_a_LOSS():
    """50 fail open; 29 of them retain the value, and 1 of the 10 rows where a
    drop is VISIBLE drops one (1 of the 105 the projection can check at all).

    This is the difference the row could not state: the fail-open count is a
    DETECTION figure. Reading it as retention (62 of 112) leaves 29 rows of
    retention unstated — 25.9 points, 62/112 against the 91/112 STATED — and
    would license widening a rule that is already accused of over-refusal.
    """
    rows = _census_rows()
    assert len(rows) == 112, (
        f"the value-bearing population moved: {len(rows)} rows, pinned 112 by "
        "test_the_residual_is_measured_on_the_committed_census_corpus")
    split: dict[str, int] = {key: 0 for key in RETENTION_SPLIT}
    for _session, _arm, klass, _content, _quote in rows:
        assert klass in split, f"a class the constants do not know: {klass!r}"
        split[klass] += 1
    assert split == RETENTION_SPLIT, (
        f"the retention split moved: {split} != {RETENTION_SPLIT}")

    # The reading the row must not lose: the value is STATED — through the
    # shipped slot projection — in ALIGNED + LAYOUT_RETAINED. ⛔ The 7
    # CONTENT_EXCLUDED_FORM rows are NOT added here: their class reads the
    # content's FORM and never its agreement with the quote, so counting them
    # as stated would make the figure a claim nothing checks (that is residual
    # (c)'s own class, declared).
    stated = RETENTION_SPLIT["ALIGNED"] + RETENTION_SPLIT["LAYOUT_RETAINED"]
    # ⛔ PINNED TO THE ROW, not to a constant beside the split (a number the test
    # derives from its own constants asserts nothing — `split` above is already
    # pinned): the registry row publishes this figure, so the figure and the
    # measurement cannot drift apart.
    # ⛔ AND TO THE `state_value_carve_out` ROW SPECIFICALLY, not to the FILE.
    # The claim is "THIS row publishes the measured figure", so the assertion has
    # to read the text that claim is about: `in row_text` over all ~9,000 lines
    # stayed GREEN if another mechanism's row ever carried the same sentence
    # while this row drifted back to the reach-only reading it replaces — a
    # fail-open direction on a new guard, in the one pin whose whole job is that
    # the figure and the measurement cannot diverge (#11730 review finding 3;
    # MEASURED: a decoy row appended with the sentence, and the sentence deleted
    # from this row, was GREEN before this line and RED after it).
    row_text = (pathlib.Path(__file__).resolve().parent.parent
                / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    row = _residual_region(row_text)
    assert f"{stated} of 112 STATED" in row, (
        f"the `state_value_carve_out` row does not publish the measured "
        f"{stated} of 112 STATED")

    lost = set()
    retained = []
    for session, arm, klass, content, quote in rows:
        if klass == "LAYOUT_RETAINED":
            retained.append((content, quote))
        if klass != "LAYOUT_LOST":
            continue
        stated_cores = {(core, scale) for _a, core, scale in
                        v2._value_slots_scaled(content)}
        for anchor, core, scale in v2._value_slots_scaled(quote):
            if (str(core), scale) not in {(str(c), s) for c, s in stated_cores}:
                lost.add((session, arm, (anchor, str(core))))
    assert lost == LAYOUT_LOST_ROWS, (
        "the LAYOUT_LOST set moved — a state value was dropped, which is the "
        f"finding this pin exists to surface: {sorted(lost)}")

    # ⛔ AND THE RETAINED HALF IS CHECKED AGAINST THE OTHER ARTEFACT, not
    # re-derived from the classifier: the shipped rule fails open on EVERY row
    # this class calls retained — that is what "a detection gap, not a loss"
    # means. A rule widened to flag a layout mismatch reds here, which is the
    # direction the row's next lane would move it.
    # ⛔ THE LOOP'S POPULATION IS NOT ASSERTED HERE. `len(retained)` IS
    # `split["LAYOUT_RETAINED"]` by construction, and `split == RETENTION_SPLIT`
    # is asserted above — so a count here could not go red without that line
    # going red first, which makes it an assertion no edit can falsify rather
    # than a second opinion (#11730 review finding 1: the reachability step of
    # reachability–infection–propagation is never entered, so it is not a weak
    # guard, it is not a guard). The population is pinned where it can move: by
    # the split above, and as a literal in
    # `test_the_classifier_DISCRIMINATES_in_both_directions`.
    for content, quote in retained:
        assert v2._value_slot_divergence(content, quote) == (), (content, quote)


def test_the_ONE_dropped_state_value_on_the_census_is_NAMED():
    """A residual of ONE is actionable; "50 fail open" is not.

    No rule acting on an emitted point can recover a value the point never
    stated — but the points that DID drop one can be counted, and here there is
    exactly one: the lease grace the quote sizes as `thirty seconds`, whose
    point states the policy and not the duration. Its content carries no
    excluded form, so it is not residual (c) in disguise.
    """
    dropped = []
    skipped = set()
    for session, arm, klass, _content, quote in _census_rows():
        if klass != "CONTENT_NO_VALUE":
            continue
        for anchor, core in _value_pairs(quote):
            if core in _DETERMINER_CORES:
                skipped.add((session, arm, anchor, core))
                continue
            dropped.append((session, arm, anchor, core))
    assert skipped == DETERMINER_ROWS, (
        "the determiner skip moved — a bare 1/2 that is a real state value would "
        f"be hidden by the shape filter: {sorted(skipped)}")
    assert len(dropped) == 1, (
        "the dropped-state-value set moved: "
        f"{sorted(dropped)}")
    assert dropped[0] == THE_ONE_CONTENT_MISSING_VALUE, dropped[0]

    session, arm, _anchor, core = THE_ONE_CONTENT_MISSING_VALUE
    # Keyed on the identity pinned above, never on the (session, arm, class)
    # triple: two rows of this session share that triple.
    content, quote = next(
        (c, q) for s, a, _k, c, q in _census_rows()
        if (s, a) == (session, arm)
        and c.startswith("Any lease expiry landing inside the grace window"))
    # Selected by the point's OWN TEXT, which is independent of the class — so
    # this is a claim about the row, not a restatement of the branch that
    # assigned it. ⛔ And the SAME session's sibling point, which states the
    # same `thirty seconds` (`The lease grace period is sized to thirty
    # seconds…`), must NOT be in this class: that contrast is the pin.
    assert _retention_class(content, quote) == "CONTENT_NO_VALUE"
    sibling = next(
        (c, q) for s, a, _k, c, q in _census_rows()
        if (s, a) == (session, arm)
        and c.startswith("The lease grace period is sized to thirty seconds"))
    assert _retention_class(*sibling) == "LAYOUT_RETAINED", sibling[0][:80]
    assert "second" not in content.lower(), content
    # ⛔ NOT `_ISO_DATE_FORM.search(content) is None` / `_CLOCK_FORM…` beside the
    # class assert: `CONTENT_NO_VALUE` is returned only from the branch that
    # already tested both, so those two could never fail (#11730 review finding
    # 2, same class as the `CONTENT_EXCLUDED_FORM` block below).


def test_the_classifier_DISCRIMINATES_in_both_directions():
    """The split above is only a MEASUREMENT if it can move.

    Every assert is a direction the classifier must take, on inputs built from
    the carve-out's own examples — so a regression reds here rather than
    silently re-labelling the census.
    """
    # 1. A RETAINED value under a layout the rule cannot align is NOT a loss.
    #    Built from a REAL census row, so the class is not hypothetical, and the
    #    assertion that carries the weight is the OTHER artefact's: the shipped
    #    rule fails open on it. (Re-running `_retention_class` on rows it selected
    #    would be `f(x) == f(x)`.)
    retained = [
        (content, quote) for _s, _a, klass, content, quote in _census_rows()
        if klass == "LAYOUT_RETAINED"]
    assert len(retained) == 29, len(retained)
    for content, quote in retained:
        assert v2._value_slot_divergence(content, quote) == (), (
            content[:80], quote[:80])
    # 2. A DROPPED value, in both shapes the "content_missing" class takes: the
    #    content binds another anchor's value (a layout the rule cannot align, so
    #    `_value_slot_divergence` fails open on it), or it binds none at all.
    assert _retention_class("we set the limit to 30 minutes",
                            "the TTL is 20 minutes") == "LAYOUT_LOST"
    assert v2._value_slot_divergence("we set the limit to 30 minutes",
                                     "the TTL is 20 minutes") == ()
    assert _retention_class("the deploy uses a short TTL",
                            "the TTL is 20 minutes") == "CONTENT_NO_VALUE"
    # 2b. A MAGNITUDE difference is UNJUDGED, not LOST and not RETAINED — the
    #    engine has no magnitude table (`#2817` rules one out; residual (m)), so
    #    neither direction can be read without adding the table the ruling
    #    excludes. Both directions are pinned, or the class is unpinned.
    assert _retention_class("we have 12 thousand events",
                            "we have 12 million events") == "LAYOUT_UNJUDGED"
    assert _retention_class("we have 12000000 events",
                            "we have 12 million events") == "LAYOUT_UNJUDGED"
    # 2c. ⛔ AND THE SCALE TEST IS KEYED ON THE MAGNITUDE, NOT ON AN ANCHOR, in
    #    BOTH directions (#11730 review, two cycles — each direction is a real
    #    failure of the naive spellings, so both are pinned).
    #    (a) An unrelated scale word must not launder a drop: `4 batches` is lost
    #        outright here while ANOTHER slot says `million` …
    assert _retention_class("we shipped 3 million events",
                            "we shipped 3 million events and 4 batches") == (
        "LAYOUT_LOST"), "a scale word on ANOTHER anchor must not excuse a loss"
    #        … and the SAME anchor is not the same slot: `holds` repeats in the
    #        parallel clause, so an anchor-match test still launders the dropped
    #        `3` behind the unrelated `2 million`.
    assert _retention_class(
        "the east shard holds 2 million records",
        "the east shard holds 2 million records and the west shard holds 3 "
        "records") == "LAYOUT_LOST", (
            "a repeated ANCHOR is not the same slot: an unrelated magnitude on "
            "it must not excuse a dropped value")
    #    (b) … and a magnitude pair must not be filed as a DROP just because the
    #        anchor was REWORDED (`12000000` against `we are INGESTING 12
    #        million` is one value in two forms, not a loss).
    assert _retention_class("we have 12000000 events",
                            "we are ingesting 12 million events") == (
        "LAYOUT_UNJUDGED"), (
            "a reworded anchor must not turn a magnitude pair into a loss")
    assert v2._value_slot_divergence("we have 12 thousand events",
                                     "we have 12 million events") == ()
    # 2d. ⛔ THREE SHAPES THE CYCLE-9 REVIEW MEASURED, all of them the same
    #     defect one spelling further on, and all now pinned:
    #     (i) EQUAL digits are not a magnitude pair — `3 batches` against content
    #         `3 million events` shares the digits and is a DROP, not residual (m);
    assert _retention_class("we shipped 3 million events",
                            "we shipped 3 million events and 3 batches") == (
        "LAYOUT_LOST"), (
            "equal digits on an unscaled missing slot are not a magnitude pair")
    #     (ii) a missing SCALED value with NO counterpart is a drop — the old
    #          unconditional `quote_scale` arm filed the dropped `3 million` as
    #          unjudged because it carried a scale word;
    assert _retention_class("we shipped 4 batches",
                            "we shipped 3 million events and 4 batches") == (
        "LAYOUT_LOST"), "a dropped scaled value with no counterpart is a loss"
    #     (iii) one content value cannot satisfy TWO quote values: the match is a
    #           MULTISET, not a set (`stated` was a set, so `2 fixes` was
    #           certified retained by the `2 bugs` slot).
    assert _retention_class("we shipped 2 bugs",
                            "we shipped 2 bugs and 2 fixes") == (
        "LAYOUT_LOST"), "one content value cannot state two quote values"
    #     (iv) a DECIMAL spelling is the same number as its digit form (`1.2`
    #          million = 1200000, `0.5` million = 500000).
    assert _retention_class("we have 1.2 million events",
                            "we have 1200000 events") == "LAYOUT_UNJUDGED"
    assert _retention_class("we have 0.5 million events",
                            "we have 500000 events") == "LAYOUT_UNJUDGED"
    # 2e. ⛔ EVERY CLAUSE ABOVE IS PINNED BY A CASE THAT MOVED WHEN THAT CLAUSE WAS
    #     REVERTED, and the rows are grouped BY THE CLAUSE THEY PIN (#11730
    #     review, cycles 11–12). ⛔ Cycle 12 measured that the four (a) rows below
    #     have NO explained slot at all, so they pin the `(quote_scale or
    #     content_scale)` guard and NOT the every-slot rule — a revert of the
    #     every-slot rule kept all 122 tests green. The discriminating pairs are
    #     (a2) and (a3); the header said otherwise and was wrong.
    #     (a) the (quote_scale or content_scale) guard and the every-slot rule,
    #         TOGETHER: each row below has TWO missing quote slots and only one is
    #         a zero-extension candidate, so reverting EITHER clause alone leaves
    #         them LOST — measured (cycle 13). They flip to UNJUDGED only when
    #         both are reverted, so they do not pin either clause on its own:
    #         the guard's own pin is (b) below, the every-slot rule's is (a2).
    for content, quote in (("we shipped 3000 events",
                            "we shipped 30 batches and 12 events"),
                           ("we shipped 12000 events",
                            "we shipped 3 batches and 12 events"),
                           ("we shipped 100 events",
                            "we shipped 4 batches and 1000 events"),
                           ("the fleet holds 700 nodes",
                            "the fleet holds 7 regions and 40 nodes")):
        assert _retention_class(content, quote) == "LAYOUT_LOST", (content, quote)
    #     (a2) EVERY missing slot must be explained, not just one: `12 million`
    #         IS explained (`12000000`, zero-extended) while the `9 nodes` beside
    #         it is simply dropped, so the row is LOST. This is the only shape
    #         that distinguishes the every-slot rule from first-wins.
    assert _retention_class(
        "we shipped 12000000 events and 5 nodes",
        "we shipped 12 million events and 9 nodes") == "LAYOUT_LOST", (
            "one explained slot must not excuse another missing value")
    #     (a3) … and the compensated-scale pair is NOT a magnitude question when
    #         the scale words are the SAME: `1.2 million` against `12 million` is a
    #         10x rewrite the shipped rule JUDGES, so the classifier must not
    #         call it unjudged. This is the only shape that distinguishes the
    #         `quote_scale != content_scale` clause.
    assert _retention_class("we have 1.2 million events",
                            "we are ingesting 12 million events") == (
        "LAYOUT_LOST"), (
            "the same scale word cancels, so a different mantissa is a rewrite")
    #     (b) a zero extension with NO scale word on either side is a different
    #         number the shipped rule JUDGES, not a magnitude question;
    assert _retention_class("we ordered 2000 batches",
                            "we shipped 2 batches") == "LAYOUT_LOST"
    #     (c) an unscaled missing value is not rescued by a scaled content slot;
    assert _retention_class("we shipped 3 million events",
                            "we shipped 3 batches") == "LAYOUT_LOST"
    #     (d) a unit form the engine tolerates is a RESTATEMENT, not a loss —
    #         the direction that accuses a truthful point (`20 percent` against
    #         `20%`, the same tolerance the file's battery at the top pins).
    assert _retention_class("we ordered 20 percent",
                            "we shipped 20%") == "LAYOUT_RETAINED"
    #     (e) TWO DIFFERENT scale words are a magnitude question whatever the
    #         mantissas are — `2000 thousand` and `2 million` are the SAME value,
    #         so calling that pair LOST would be a positive claim the projection
    #         cannot support (cycle 12, the false-accusation direction).
    for content, quote in (("we shipped 2000 thousand units",
                            "we shipped 2 million units"),
                           ("we have 1200 thousand events",
                            "we have 1.2 million events"),
                           ("the fleet holds 20 hundred nodes",
                            "the fleet holds 2 thousand nodes")):
        assert _retention_class(content, quote) == "LAYOUT_UNJUDGED", (
            content, quote)
    # 2f. ⛔ `_digits` CANNOT RETURN AN EMPTY STRING, which is what lets
    #     `_same_magnitude_number` decide on equality alone (#11730 review, cycle
    #     11). The census never reaches either shape — `_value_slots_scaled`
    #     yields a slot only when it binds digits — so the CONTRACT is pinned
    #     here rather than left as surface no input can reach: a digitless core
    #     and the literal zero both collapse to `"0"`, and two of them are not a
    #     magnitude pair.
    assert _digits(("", "", None)) == "0", (
        "a digitless core must collapse to the sentinel, never to ''")
    assert _digits(("", "0", None)) == "0", "the literal zero is the sentinel"
    assert _digits(("", "1.2", None)) == "12", _digits(("", "1.2", None))
    assert _same_magnitude_number(("", "0", None), ("", "0", None)) is False
    # 3. An excluded FORM is its own class — residual (c) is not a loss.
    assert _retention_class("we cut over at 2am",
                            "we cut over at 2am") == "CONTENT_EXCLUDED_FORM"
    assert _retention_class("due 2026-11-09", "due November 9") == (
        "CONTENT_EXCLUDED_FORM")
    # 3a. … and so is the THIRD excluded form, a separator-bearing numeral. This
    #     corpus carries none, so it is pinned synthetically: without the arm the
    #     row would fall into `CONTENT_NO_VALUE` and its quote slot would be
    #     counted as a DROPPED value (#11730 review, cycle 12).
    assert _retention_class("we have 1,234 events",
                            "we have 1234 events") == "CONTENT_EXCLUDED_FORM"
    assert _retention_class("we have 1.234 events",
                            "we have 1234 events") == "CONTENT_EXCLUDED_FORM"
    #     … and it is still a FORM class, not a value class: a plain numeral that
    #     the projection CAN bind stays in the judged set.
    assert _retention_class("we have 1234 events",
                            "we have 1234 events") == "ALIGNED"
    # 3b. The excluded-form class is a MEASURED class on the census, not only a
    #     synthetic shape: those 7 rows really do carry a clock or ISO token and
    #     really are rows the shipped slot projection finds nothing to align in.
    #     ⛔ THAT IS STATED HERE AND NOT ASSERTED: `_retention_class` reaches
    #     `CONTENT_EXCLUDED_FORM` only when the content has no slot AND carries a
    #     clock/ISO token, so both predicates held over rows it selected are
    #     `f(x) == f(x)` — entailed by the branch that assigned the class, with no
    #     reachable edit whose change reaches them (#11730 review finding 2). The
    #     count below is the part that CAN move, and it is a literal: a corpus
    #     edit that re-labels a row reds here. The class's discriminating power is
    #     pinned synthetically above (3) and by the split in the test above.
    excluded = [
        content for _s, _a, klass, content, _quote in _census_rows()
        if klass == "CONTENT_EXCLUDED_FORM"]
    assert len(excluded) == 7, len(excluded)
    # 3c. ⛔ THE BLIND SPOT'S SIZE IS PINNED AND THE PART THAT IS CHECKABLE IS
    #     ACTUALLY CHECKED (#11730 review, cycles 8 and 9). A value dropped while
    #     the content keeps its CLOCK is invisible to this projection: it binds no
    #     slot for a clock token, so there is nothing to compare the quote
    #     against — that is residual (c) itself. But the ISO half IS checkable:
    #     of the 7 excluded rows exactly 2 bind ANY quote-side slot, and both bind
    #     an ISO date, which the content restates as a real date. So the check is
    #     an AGREEMENT between the two forms, not a substring search — cycle 9
    #     measured that `"2" in content` stayed green when the content was
    #     rewritten to `2026-12-25` (and to the sibling row's `2026-11-09`)
    #     against a quote that says `November 2`.
    iso_bound = [(c, q) for _s, _a, klass, c, q in _census_rows()
                 if klass == "CONTENT_EXCLUDED_FORM"
                 and v2._value_slots_scaled(q)]
    assert len(iso_bound) == 2, [q[:60] for _c, q in iso_bound]
    for content, quote in iso_bound:
        assert _stated_month_day(quote) is not None, quote[:80]
        assert _stated_month_day(content) == _stated_month_day(quote), (
            _stated_month_day(content), _stated_month_day(quote), content[:80])
    # ⛔ AND THE LOOP IS NOT VACUOUS: emptying its iterable left all of §24 green
    #     (cycle 11), so the count and the agreement are asserted together.
    assert len(iso_bound) == 2 and all(
        _stated_month_day(c) == _stated_month_day(q) for c, q in iso_bound)
    # 3d. ⛔ AND THE HELPER ITSELF IS DISCRIMINATED, because both census rows
    #     AGREE: an equality over two agreeing rows cannot tell "compares
    #     month-and-day" from "compares presence", which is the cycle-9 P1 one
    #     level down (a `_stated_month_day` reduced to `(0, 0)` when any date is
    #     present left all three §24 tests green). ⛔ ONE PAIR PER COMPONENT: a
    #     pin that varies the day AND the month is carried by the day alone, so
    #     the month needs a pair with the SAME day and a different month
    #     (cycle 13: a helper reduced to `(0, day)` was silent over every pin
    #     below until this one was added).
    assert _stated_month_day("due 2026-11-02") == _stated_month_day(
        "land by November 2")
    assert _stated_month_day("due 2026-12-25") != _stated_month_day(
        "land by November 2"), "the checker must compare month-and-day, not presence"
    # the DAY on its own — same month, different day;
    assert _stated_month_day("due 2026-11-19") != _stated_month_day(
        "land by November 2"), "the checker must compare the DAY too"
    # the MONTH on its own — same day, different month;
    assert _stated_month_day("due 2026-12-02") != _stated_month_day(
        "land by November 2"), "the checker must compare the MONTH too"
    assert _stated_month_day("nothing here") is None
    # 4. A value-free point is not a row at all.
    assert _retention_class("the team prefers intervals",
                            "the team prefers intervals") is None
    # 5. And the class the census calls ALIGNED is the class the SHIPPED rule
    #    judges — a same-slot pair, whose rewrite the rule REFUSES. So the
    #    classifier is not a second opinion about the rule: ALIGNED is where the
    #    rule looks, and the divergence both sides see is the same one.
    assert _retention_class("the TTL is 20 hours",
                            "the TTL is 20 minutes") == "ALIGNED"
    assert v2._value_slot_divergence("the TTL is 20 hours",
                                     "the TTL is 20 minutes")
    assert _retention_class("the TTL is 20 minutes",
                            "the TTL is 20 minutes") == "ALIGNED"
    assert v2._value_slot_divergence("the TTL is 20 minutes",
                                     "the TTL is 20 minutes") == ()
