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

import json
import pathlib

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
    return v2.execute_embed(_embed(content, quote), {}, session_id="s_10360")


# ── 1. the rule + its tolerance bound ───────────────────────────────────────

#: ``(content, quote, must_flag, label)``. The four ``False`` rows that carry a
#: FORM difference and the three that carry a dropped/reordered value are the
#: measured false-positive classes; the ``True`` rows are the class the carve-out
#: exists for ("never round, paraphrase, or label them").
BATTERY = (
    ("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds", True, "rounding"),
    ("the invoice was $12", "the invoice was $60", True, "leading symbol"),
    ("the p95 hit 4 seconds", "the p95 hit 4.2 seconds", True, "precision dropped"),
    ("the TTL is ten minutes", "the TTL is twenty minutes", True, "ttl rewritten"),
    ("p95 went from 4.2 to 6.1", "p95 went from 4.2 to 6.0", True, "one of two"),
    ("we have 2 cats and 3 dogs", "we have 3 cats and 2 dogs", True, "quantities swapped"),
    ("the p95 hit 4.0 seconds", "the p95 hit 4.0 seconds", False, "identical"),
    ("latency was 1200 ms", "latency was 1,200 ms", False, "thousands separator"),
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
    # `_value_bindings` fuses only the units in its own `_UNIT_WORDS`, so the
    # judged form is the fused one (`800ms`); a unit written APART is not
    # fused by the engine and therefore not a unit identity here either.
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
    ("we saw 5k QPS", "we saw 5,000 QPS", False, "magnitude prefix vs scaled"),
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
    ("we saw 5k QPS", "we saw 6,000 QPS", True, "magnitude number moved"),
    ("we ran 20k", "we ran 21km", True, "magnitude number moved 2"),
    ("the delta is -20k", "the delta is 20km", True, "magnitude sign dropped"),
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
    before the sign/core check, so `5k QPS`/`6,000 QPS`, `20k`/`21km` and
    `-20k`/`20km` were all WRITTEN. The assertions below are what reds that
    revision; the unit-only arm cannot satisfy them.
    """
    # Ambiguous unit word: unjudgeable, so it fails OPEN (the refusal direction
    # this rule must never take is the one that withholds a truthful memory).
    assert v2._value_slot_divergence("I ran 20k", "I ran 20km") == ()
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 5,000 QPS") == ()
    # Same unit on both sides: still judged, and a moved number is a rewrite.
    assert v2._value_slot_divergence("the load was 5k QPS", "the load was 6k QPS")
    # ⛔ EVERY magnitude prefix needs a FACTOR, or `_magnitude_agrees` returns
    # False for everything and the arm becomes a silent no-op — the two false
    # refusals below would quietly come back with every test still green. One
    # line, because the containment is the whole mechanism.
    assert set(v2._MAGNITUDE_PREFIXES) <= set(v2._MAGNITUDE_FACTORS)
    # ⛔ The number and the sign are NOT the ambiguity, so they are still judged
    # even where the unit words disagree (this is the P2 the arm first shipped).
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 6,000 QPS")
    assert v2._value_slot_divergence("we ran 20k", "we ran 21km")
    assert v2._value_slot_divergence("the delta is -20k", "the delta is 20km")
    # ⛔ The equal-core half is gated on the OTHER unit (#10513 review P1):
    # "the cores are equal" is "the same number" only while `k` stands for that
    # unit word. Against `m`/`mi` the same cores are a 1000x change — the class
    # `20 km`/`20 m` is refused for — and the first version of this arm WROTE
    # them: measured `20k`/`20m` and `20k`/`20mi` both passed with no warning.
    assert v2._value_slot_divergence("the drive is 20k", "the drive is 20m")
    assert v2._value_slot_divergence("the drive is 20k", "the drive is 20mi")
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 5,000 mi QPS")
    # …and the two tolerance cases the arm exists for still hold, so the gate did
    # not buy the detection by re-introducing the false refusals.
    assert v2._value_slot_divergence("the drive is 20k", "the drive is 20kg") == ()
    assert v2._value_slot_divergence("we saw 5k QPS", "we saw 5,000 QPS") == ()


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

    def counting(text):
        calls["n"] += 1
        return real(text)

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

    ⚠️ The inputs here are the CLAMPED pair (the caps production truncates the
    STORED point with), while the write path now judges the UNCLAMPED pair — a
    deliberate difference, since a value straddling the clamp must not read as a
    rewrite. Both readings give the same `(62, 0)` on this corpus (asserted
    below), so the pin is not sensitive to which one it feeds; if they ever
    diverge, the clamp is cutting a value and the write-path reading is the
    correct one.
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
                content_slots = v2._value_slots(content)
                quote_slots = v2._value_slots(quote)
                if (content_slots and quote_slots
                        and tuple(a for a, _ in content_slots)
                        == tuple(a for a, _ in quote_slots)):
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


# ── 6. the two review findings this rule must never repeat ─────────────────

def test_a_value_that_straddles_the_quote_clamp_is_not_a_rewrite():
    """Review P1: the module's own truncation must not accuse the model.

    `quote` is clamped to `_MAX_QUOTE` before the point is built, so a value
    sitting across the cut reads as a SHORTER value ("1200" -> "1"). The rule is
    therefore fed the UNCLAMPED texts; a truthful point over a long quote is
    written.
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
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert variant not in contents, contents
    # positive control: an endpoint with no value in it is still minted, so the
    # slot key is a value guard and not a blanket refusal of the operator path.
    embed = _embed("the p95 hit 4.0 seconds", "the p95 hit 4.2 seconds")
    embed["operators"] = [{
        "src": "the nightly rebuild window",
        "dst": "the cache TTL is 20 minutes", "op_type": "IMPL"}]
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    contents = [p["content"] for p in result["payload"]["points"]]
    assert "the nightly rebuild window" in contents, contents


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
    result = v2.execute_embed(control, {}, session_id="s_10360")
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


# ── 8. the rule's own BOUND, and the residual it still carries ─────────────

def test_an_over_long_side_is_not_judged_so_the_write_path_stays_bounded():
    """UNVERIFIED item 2, settled: the unclamped judge must be BOUNDED.

    `_refuse_rewritten_value` is fed the UNCLAMPED texts (a value straddling the
    clamp must not read as a rewrite), so a model-authored over-long side used to
    be tokenised in full on EVERY point of the single Point write path. Measured
    linear on this box: 0.45 ms at 67 chars, 2.5 ms at 1 KB, 23 ms at 10 KB,
    227 ms at 100 KB, 2.4 s at 1 MB — one hallucinated mega-quote costs the
    write path minutes. An over-long side now FAILS OPEN (never clamped: a cut
    value reads as a different value, the false-accusation direction).

    ⛔ Pinned by the WRITE, not by the return value: `()` is the answer both
    before and after the fix — the defect was the WORK, not the verdict.
    """
    content = "the p95 hit 4.0 seconds"
    quote = "the p95 hit 4.2 seconds " + ("padding z " * (v2._MAX_JUDGED_TEXT))
    assert len(quote) > v2._MAX_JUDGED_TEXT
    # within the bound the same pair IS a rewrite, so this fails open only on
    # length — the bound cannot pass by the rule being broken (positive control)
    assert v2._value_slot_divergence(content, "the p95 hit 4.2 seconds")
    assert v2._value_slot_divergence(content, quote) == ()
    result = _write(content, quote)
    assert len(result["payload"]["points"]) == 1, result["warnings"]
    assert not any("REWRITES its own verbatim" in w for w in result["warnings"])


def test_an_over_long_side_is_not_tokenised_at_all(monkeypatch):
    """The same bound, stated as COST: the engine is never called for it.

    This is the half a return-value pin cannot see. It fails on the pre-fix code
    (which tokenised the whole side first and answered `()` afterwards), so the
    bound cannot be deleted without this going red.
    """
    calls = {"n": 0}
    real = v2._value_bindings

    def counting(text):
        calls["n"] += 1
        return real(text)

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
