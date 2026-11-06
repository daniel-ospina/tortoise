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
#: 37,914 chars — 74.6% of the row — and reported PASS over a reopening written
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
    # `the exception it carved` were here for a revision and had to go — the row's
    # `the remaining gap` names the SURVIVING OPEN residual (it says "What SURVIVES
    # of the old (d) is the SPELLING case"), so anchoring it withheld the honest
    # `spelling` waiver and red `the remaining gap is the SPELLING case, which is
    # open by design` (#11258 review round 5 `P2`, measured). `the remaining gap is
    # open` is therefore an undisclosed-arm shape and is named in
    # `WHAT THIS CANNOT CATCH` instead of being claimed covered.
    r"\bthe gap (?:that|those)\b[^.;]{0,40}\bclosed\b|"
    + _UNIT_TOKEN.pattern,
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
    r"\b(?:has|have|had|is|are|was|were)\s+yet\s+to\s+be\s+closed\b",
    r"\b(?:far\s+from|anything\s+but)\s+closed\b",
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
_RESIDUAL_OPENNESS = (
    # `not closed`, allowing an adverb inside the negation (`is not yet / not
    # fully / not really closed`) — the adverb-free form was the whole class
    # one word away (review cycle 6, MEASURED: all three PASSED before this).
    # Scanned PER SENTENCE rather than per clause: `the ADJACENCY half is not, and
    # never was, closed` splits into clauses that each carry only half of it
    # (#11258 review `P2`, measured).
    r"\bnot\b[^.;]{0,30}\bclosed\b",
    r"\bisn'?t\b[^.;]{0,30}\bclosed\b",
    # ⛔ `never … closed`, `<aux> yet to be closed`, `far from closed` and
    # `anything but closed` assert the gap is open for the same reason `not …
    # closed` does — but they are in the ANCHORED arm and NOT here, because unlike
    # `not closed` they are ordinary prose about ANY residual: this row's list is
    # literally "WHAT IS STILL NOT ENFORCED", so `(b2) is far from closed` and
    # `residual (a) is never closed by any rule` are honest and both RED when these
    # forms are unanchored (#11258 review round 5 `P1`, measured). See
    # `_RESIDUAL_CLOSED_VERDICT`.
    r"\bno\s+longer\s+(closed|fuses|judged|caught|compared|enforced|retained)\b",
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
    # RED on the class-word side and GREEN on the unit side once it carried the
    # row's own `, after the widen, ` register (MEASURED: `\`minute\` is, after the
    # widen landed, no longer fused` → GREEN at 20, RED at 80).
    r"\b(?:adjacency|the\s+half|carve-?out|value\s+class|numeric\s+side|"
    r"time[- ]unit)\b[^.;]{0,80}?\bno\s+longer\s+(?:fused|fusing)\b",
    r"\b(?:units?|unit\s+word|second|seconds|minute|minutes|hour|hours|day|days|"
    r"week|weeks|month|months|mos|year|years|mile|miles|mi|millisecond|"
    r"milliseconds|millisec|millisecs|min|mins|ms|sec|secs|hr|hrs|km|kg|m|s)"
    r"\b[^.;]{0,80}?\b(?:is|are|remains?|stays?|was|were)\b[^.;]{0,80}?"
    r"\bno\s+longer\s+(?:fused|fusing)\b",
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
    r"(?<![n']t\s)(?<![n']t\sbeen\s)\bopen\s+again\b",
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
    r"completely|actually|wholly|truly|strictly)\s+)?(?:fail-)?open\b)"
    r"(?!\s*(?:\w+\s+)?n't\s+"
    r"(?:(?:yet|fully|really|still|entirely|always|genuinely|currently|quite|"
    r"completely|actually|wholly|truly|strictly)\s+)?(?:fail-)?open\b)")

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
#: OVER-BLOCK, kept because the alternative is the leak it closes, and recorded
#: here rather than left to be discovered. ⛔ STILL LEAKING, stated rather than implied: the coordinator form
#: *“the ADJACENCY half is unjudged and fails open”* stays GREEN, because the
#: clause split leaves the anchor in the first clause and the phrase in the
#: second — the same shape as the row's honest sentence, so no clause-scoped rule
#: can separate them (#11258 review `P2`).
_RESIDUAL_FAIL_OPEN = re.compile(r"\bfails?\s+open\b", re.I)

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
    rf"\b({_RESIDUAL_OPEN_VERBS})\b"
    + _RESIDUAL_NEGATED_OPEN
    + r"(?:(?!\b(?:and|but)\s+(?:\w+\s+){0,4}?"
    r"(?:is|are|was|were|fails?|stays?|remains?|keeps?|holds?)\b)[^.;]){0,30}?"
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
    r"(?<!\bnot\s)(?<!\bnever\s)(?<!\bnot\sbeen\s)(?<!\bnever\sbeen\s)"
    r"(?<!\bnot\syet\s)(?<!\bnever\sbefore\s)"
    r"(?<!\bnot\syet\sbeen\s)(?<!\bnever\sbefore\sbeen\s)"
    r"(?<![n']t\s)(?<![n']t\sbeen\s)\bre-?open",
    *_RESIDUAL_CLOSED_VERDICT,
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
    r"\bno\s+longer\s+(closed|fuses|judged|caught|compared|enforced|retained)\b",
    r"\bunclosed\b",
    # ⛔ THE WAIVED SENTENCE IS STILL READ FOR `open again`, which is why it is here
    # and not only in `_RESIDUAL_OPENNESS`: the honest path runs ONLY this tuple, so
    # `the SPELLING gap is open again` was GREEN while the same phrase without the
    # word `spelling` red — the waiver was skipping the whole openness arm
    # (#11258 review round 3 `P1`, measured). Its negation window matches the
    # opening verb's lookbehind run, so an honest denial still passes.
    r"(?<!\bnot\s)(?<!\bnever\s)(?<!\bnot\sbeen\s)(?<!\bnever\sbeen\s)"
    r"(?<!\bnot\syet\s)(?<!\bnever\sbefore\s)"
    r"(?<!\bnot\syet\sbeen\s)(?<!\bnever\sbefore\sbeen\s)"
    r"(?<![n']t\s)(?<![n']t\sbeen\s)\bopen\s+again\b",
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
    the residual marker (37,914 of 50,850 chars = 74.6% of the row). A reopening
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
#: `although`/`though`/`yet` belong here for the same measured reason `and`/`but`
#: do: they join two claims too, and a joiner the splitter does not know leaves an
#: honest `spelling` disclosure and a reopening in ONE segment — so the honest
#: waiver swallowed the second claim (`the SPELLING gap is open while the
#: remaining gap is not closed` GREEN; the same claim unwrapped RED — #11258 review
#: round 4 `P2`, measured). ⚠️ This is a CLOSED list standing in for a clause
#: boundary; a joiner not named here still joins. That residual is disclosed in
#: `WHAT THIS CANNOT CATCH` rather than implied to be covered.
_RESIDUAL_CLAUSE_SPLIT = (
    r";|,?\s+(?:and|but|while|whereas|although|though|yet)\s+"
    # ⛔ THE COORDINATOR MUST NOT BITE INSIDE A VERDICT'S OWN TEXT. `has yet to be
    # closed` splits at `yet` and `anything but closed` at `but`; severing them put
    # the two halves of `_RESIDUAL_CLOSED_VERDICT` in different segments, so after an
    # honest `spelling` clause was removed and the survivors rejoined they never
    # matched — a fail-open reachable by clause ORDER: `the SPELLING gap is open, and
    # the half has yet to be closed, per the ADJACENCY row` was GREEN while the same
    # claim with the anchor first was RED (#11258 review round 7 `P1`, measured).
    r"(?!(?:to\s+be\s+closed|closed)\b)")

def _match_is_honest_spelling_disclosure(sentence: str, match) -> bool:
    """Is `match` a closed verdict INSIDE an honest SPELLING disclosure?

    ⛔ CLAUSE CONTAINMENT, and that is only possible because the splitter no longer
    bites inside these verdicts' own text (`has yet to be closed` used to split at
    `yet`) — see `_RESIDUAL_CLAUSE_SPLIT`. The clause whose SPAN holds the match must
    itself be an honest `spelling` disclosure: `the SPELLING gap has yet to be
    closed, while the carve-out's ADJACENCY half is CLOSED` passes, and
    `the SPELLING gap is open, and the half has yet to be closed, per the ADJACENCY
    row` reds.

    ⛔ IT WAS ONE-SIDED FOR A REVISION, and that was a FAIL-OPEN: the old test looked
    BACKWARDS for `spelling` and only for an anchor before the match, so a verdict
    whose anchor sat AFTER it read as an honest disclosure — one clause re-ordered,
    `the half has yet to be closed, per the ADJACENCY row`, escaped the only guard
    on "the row must not describe a CLOSED gap as open" (#11258 review round 7
    `P1`, measured).
    """
    start = 0
    for part in re.split(f"({_RESIDUAL_CLAUSE_SPLIT})", sentence):
        end = start + len(part)
        if start <= match.start() < end or (start <= match.end() <= end):
            return _is_honest_spelling(part)
        start = end
    return False


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
        # ⛔ THE NEGATED CLOSED VERDICTS ARE SCANNED ON THE WHOLE SENTENCE, before any
        # clause surgery, because their own text CONTAINS splitter words:
        # `has yet to be closed` splits at `yet`, `anything but closed` at `but`,
        # and removing an honest `spelling` clause then rejoins the pieces — so the
        # pattern's two halves land in different segments and it never matches
        # (`the SPELLING gap is open, and the time-unit half has yet to be closed`
        # was GREEN, #11258 review round 5 `P2`, measured).
        # ⚠️ AND THAT SENTENCE SCOPE OVER-BLOCKS, measured: it judges the union of
        # both clauses, so `the SPELLING gap has never been closed, and the
        # ADJACENCY half is closed by the tables` red even though the closed-
        # verdict phrase sits inside the HONEST clause — and both halves are true
        # by this row's own text (#11258 review round 6 `P2`). A match whose span
        # is contained in an honest clause's own match is a disclosure, not a
        # reopening, so it is dropped.
        if _RESIDUAL_CLASS_ANCHOR.search(sentence):
            for pattern in _RESIDUAL_CLOSED_VERDICT:
                m = re.search(pattern, sentence)
                if m is None:
                    continue
                if _match_is_honest_spelling_disclosure(sentence, m):
                    continue
                claims.append(
                    f"{m.group(0)!r} in sentence {sentence.strip()!r}")
                break
        if honest:
            if len(honest) == len(clauses):
                for pattern in _SPELLING_FLOOR:
                    m = re.search(pattern, sentence)
                    if m is not None:
                        claims.append(
                            f"{m.group(0)!r} in sentence {sentence.strip()!r}")
                        break
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

    ⛔ The withholding set is `_RESIDUAL_CLASS_ANCHOR` in full — `adjacency`, a
    unit token or its abbreviation, `time-unit`, the carve-out, the retention
    guarantee, the value class, the numeric side. It is that WHOLE set and not a
    hand-picked subset because a subset is what let one word defeat the gate: with
    `carve-out` dropped, `the SPELLING carve-out is open again` was waived and the
    same sentence without `spelling` red (#11258 review round 3 `P1`, measured).

    ⛔ The cost, named rather than implied: a clause naming BOTH `spelling` and the
    carve-out gets no waiver, so honest prose of that shape is refused. That is an
    over-block, which this artifact prefers to the fail-open it replaces.
    """
    return ("spelling" in text
            and not _RESIDUAL_CLASS_ANCHOR.search(text))


def test_the_registry_row_names_the_residual_that_still_stands():
    """`config/v4-mechanisms.yml` must not describe a CLOSED gap as open.

    The row's (d) is the artifact a lane reads ("⛔ WHAT IS STILL NOT ENFORCED —
    read this before improving a number"). It named the ADJACENCY gap
    (`seconds`, `minute`, `hour`, `day`, `mile`) as unjuged; that gap is closed
    by the tables above, so the row was left FALSE and nothing red on it. This
    pins both halves: the closed class must not come back into the row, and the
    still-open RATE class must stay named there.
    """
    row = (pathlib.Path(__file__).resolve().parent.parent
           / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
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
    # actually appends in). The scan is now REGION-WIDE, over the residual list's
    # marker → the end of the row; the class anchor is what keeps a truthful
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
    assert adjacency_at, (
        "the row no longer makes any ADJACENCY claim in residual (d) — this "
        "table closed that gap; if it was reopened, say so AND delete this pin")
    last = adjacency_at[-1]
    tail = sentences[last + 1:]
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
    # carries the whole rule (clause-scoped, class-anchored, spelling floor) and
    # its own proof list is `test_the_residual_region_is_scanned_WHERE_a_lane_
    # appends` below — this call is the pin, that test is the pin's own proof.
    claims = _residual_reopening_claims(row)
    assert claims == [], (
        "the residual region REOPENS a gap this row says is CLOSED: "
        + "; ".join(claims))
    assert not tail or any("spelling" in s for s in tail), (
        "a sentence after the adjacency claim says something other than the "
        f"disclosed SPELLING residual — inspect it: {tail!r}")
    if tail:
        assert any("spelling" in s and "open" in s for s in tail), (
            "the row no longer discloses that the SPELLING class is open: "
            f"{tail!r}")
    # positive control: the RATE class IS still open, and it must stay NAMED, or
    # the assertions above could be satisfied by a row that says nothing at all.
    assert "not judged" in flat, block
    assert "per day" in flat or "per\n      day" in block, block


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
    "the ADJACENCY result is unclosed",
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
    # round 5 (#11258 review round 2 `P1`): EMPHATIC negations ASSERT the gap is
    # open — a negation veto wide enough to refuse these is a coverage regression,
    # and one was shipped for a round before these probes caught it.
    "the ADJACENCY half is not only open, it is unfused.",
    "the ADJACENCY half is not merely open.",
    "the ADJACENCY half is not just open, it is unjudged.",
    "the ADJACENCY half is never far from open.",
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
    # a time: the 6 class words, `units?`, `minute`, `km`, and (round 4) `kg`,
    # `mi`, `mos`, `millisec`, `m` each lose a RED; the remaining plain-English
    # spellings (`second(s)`, `hour(s)`, `day(s)`, `week(s)`, `month(s)`,
    # `year(s)`, `mile(s)`, `millisecond(s)`, `min`, `ms`) do NOT, so a tidying
    # pass that collapses `second|seconds` still gets no signal. Said here rather
    # than implied (#11258 review round 4 `P2`).
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
    # ... and the row's own `, after the widen, ` register inside the window,
    # which the 20-char bound read as GREEN on the unit side and RED on the
    # class-word side.
    "`minute` is, after the widen landed, no longer fused",
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
    "(d2) does not reopen (d)'s ADJACENCY result — the unit word IS fused.",
    "an unknown unit still fails open on purpose.",
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
    assert len(_RESIDUAL_REOPENING_MUTATIONS) >= 78, (
        "the reopening proof list SHRANK below its floor — re-derive the count "
        "in the docstrings/comments rather than deleting shapes. ⚠️ The floor is "
        "kept within ONE arm's proof set of the current length on purpose: at a "
        "#11258 round-6 reviewer-measured 49, twenty-one deletions were permitted "
        "while `_RESIDUAL_CLOSED_VERDICT` is six shapes, so the whole arm plus every "
        "proof of it could be deleted with both tests GREEN.")
    assert len(_RESIDUAL_TRUTHFUL_STATEMENTS) >= 30, (
        "the truthful-statement proof list SHRANK below its floor — a false-block "
        "list that can be deleted silently is not coverage. ⚠️ Within two of the "
        "current length on purpose: at `>= 22` against a 35-entry list THIRTEEN "
        "deletions were free, so the honest closures this branch added could be "
        "deleted with both tests GREEN (#11258 review round 7 `P2`, measured).")
    for required in (
            # the issue's own mutation, and the append-point arm
            "the ADJACENCY half is open again",
            # the `no longer` participle, subject-gated (#11258 round 3)
            "the ADJACENCY half is no longer fused",
            # the clause-anchored `fails open` arm
            "the ADJACENCY half fails open.",
            # the emphatic negations the veto must NOT swallow
            "the ADJACENCY half is not only open, it is unfused.",
            # ⛔ THE CLOSED-VERDICT ARM (round 4/5, #11258 round-6 review `P1`
            # MEASURED): with the floor's old slack this arm had NO membership
            # entry — emptying `_RESIDUAL_CLOSED_VERDICT` AND deleting its six
            # exclusively-covered shapes left BOTH tests GREEN, because the floor
            # permitted 21 deletions and the arm is 6.
            "the ADJACENCY gap was never closed.",
            "the time-unit half has yet to be closed",
            "the ADJACENCY half is anything but closed.",
            # ⛔ AND ONE SHAPE PER ARM/PATTERN THAT HAS FEW EXCLUSIVE PROOFS. The
            # round-6 fix covered `_RESIDUAL_CLOSED_VERDICT` and left the rest:
            # `_SPELLING_FLOOR` had NO membership entry and exactly ONE exclusive
            # proof, `\bunclosed\b` and the bare opening verb one each, so each
            # could be deleted WITH its proof inside the floor's slack — and the
            # first of those silently restores the round-3 `P1` (the honest waiver
            # skipping the whole openness arm). All three measured GREEN/GREEN
            # before these three lines (#11258 review round 7 `P1`).
            "the SPELLING gap is open again.",
            "the ADJACENCY result is unclosed",
            "the carve-out reopens.",
            # ⛔ THE OTHER DIRECTION NEEDS MEMBERSHIP TOO: without it every proof of
            # this branch's own false-block fixes was deletable with zero red.
            "the ADJACENCY half is not open.",
            "the SPELLING gap has never been closed, and the ADJACENCY half is closed by the tables.",):
        assert (required in _RESIDUAL_REOPENING_MUTATIONS
                or required in _RESIDUAL_TRUTHFUL_STATEMENTS), (
            f"the proof lists no longer carry {required!r} — that shape is the "
            "reason its arm exists; a lane reading this list must be able to run "
            "the shape, so deleting it is a silent coverage regression")
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "config" / "v4-mechanisms.yml").read_text(encoding="utf-8")
    assert _residual_reopening_claims(text) == [], (
        "the row itself reds before any mutation is applied: "
        + "; ".join(_residual_reopening_claims(text)))
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
        if not _residual_reopening_claims(mutated):
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
    assert _residual_reopening_claims(prefixed), (
        "a reopening inserted into the row's narrative ABOVE the residual marker "
        "stayed GREEN — the scanned region still starts at "
        f"{_RESIDUAL_MARKER!r}, so it reads only "
        f"{len(region) - region.find(_RESIDUAL_MARKER)} of the row's "
        f"{len(region)} chars and publishes PASS over the rest (#11223's own "
        "defect, one bound further in)")
    false_alarms = [
        sentence for sentence in _RESIDUAL_TRUTHFUL_STATEMENTS
        if _residual_reopening_claims(_with_appended(text, sentence))]
    assert false_alarms == [], (
        "these TRUTHFUL statements red the scan — a false block is how a guard "
        f"gets deleted: {false_alarms}")


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
