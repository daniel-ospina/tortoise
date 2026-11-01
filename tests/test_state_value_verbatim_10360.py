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


def test_the_one_classifier_is_the_production_value_engine():
    """No second value classifier: the rule reads ``_value_bindings``.

    The previous attempt at this issue introduced a private
    ``_REWRITE_QUANTITY_RE`` beside the engine's own vocabulary and was rejected
    for it (review P2, owner rule: "never create a parallel layer doing the same
    thing with redundant machinery"). Asserted structurally, because a comment
    cannot fail: a value the engine binds is a value the rule judges, and a
    token the engine does not bind is invisible to the rule.
    """
    assert v2._value_slots("the TTL is twenty minutes") == (("ttl", ("", "20", "min")),)
    assert v2._value_slots("the team prefers intervals") == ()
    # the rule's judged set IS the engine's, minus the excluded FORMS
    engine = [v for _a, v in v2._value_bindings("the freeze starts 03:00")]
    assert engine and v2._value_slots("the freeze starts 03:00") == ()


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
    anyway. Both keys are recorded now; dropping either reds this."""
    content = "the p95 hit 4.0 seconds " + ("z" * (v2._MAX_CONTENT + 200))
    quote = "the p95 hit 4.2 seconds " + ("z" * (v2._MAX_CONTENT + 200))
    embed = _embed(content, quote)
    embed["operators"] = [{"src": content, "dst": "the cache TTL is 20 minutes",
                           "op_type": "IMPL"}]
    result = v2.execute_embed(embed, {}, session_id="s_10360")
    assert all(p["content"] != content for p in result["payload"]["points"]), (
        [p["content"][:40] for p in result["payload"]["points"]])
