"""The referentiality test — a mention is not an entity (#11021).

What this file pins, and why each row is load-bearing:

- **T1 (the false-minting case)** — a candidate whose phrase refers to nothing
  (`it`, `the problem`, `the former`) is held as a **mention** on the claim that
  used it, carries the rule that held it, and **never reaches the entity loop**.
  Proven at the MINT boundary (`execute_embed` → payload), not at the predicate:
  a predicate that returns the right answer while the mint loop still sees the
  candidate has changed nothing.
- **T2 (the positive control)** — a phrase that names a referent still mints.
  A rule that holds everything is not a referentiality test.
- **The 12-row must-keep arm** — the most-referenced Objects in the graph, every
  one a row a *name-pattern* rule would have deleted. This is the arm that can
  come back with a POSITIVE answer; the 25-row label set is all-negative and its
  precision number is `#10996`'s to own (`#11021` consumes it, and the two issues
  must not both be credited for one measurement).
- **Zero mints from the mention channel** — a `mentions[]` entry the model emitted
  itself is *never* promoted, even when its surface looks fully referential.
- **Layer-1 stays satisfied** — the held surface is stripped from
  `about_entities`, so `commit_schema`'s `about_entities ⊆ entities` invariant
  does not 422 the whole session. A hold that breaks the contract is a worse
  outcome than the phantom node it removed.
- **The flag** — **OFF at the default** (plan R1: *fail-open to entity until
  measured*), and `=1` arms it. ⚠️ This is deliberately NOT `#10996`'s ON default:
  that flip was legitimate because its rate had been measured and committed, and R1
  makes arming conditional on exactly the measurement this issue still owes.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import referentiality as ref
from tortoise.commit_schema import validate_payload_dict
from tortoise.extractor_v2 import execute_embed, route_mentions

REPO = Path(__file__).resolve().parent.parent


# ── helpers ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _arm_the_cue(monkeypatch):
    """Arm the cue for this file's behavioural rows.

    The artifact's DEFAULT is OFF (plan R1 — fail-open to entity until measured), so
    a test that drove the default would be testing that nothing happens. These rows
    exercise the ARMED arm; the flag rows below override this fixture in their own
    bodies, which is why the arming is a fixture and not a marker.
    """
    monkeypatch.setenv(ref.ENV_FLAG, "1")


def _embed_list(*surfaces: str, point_about: list[str] | None = None) -> dict:
    """An embed list shaped like S2.1's output: one entity per surface, and ONE
    claim that names the first surface."""
    first = surfaces[0] if surfaces else "x"
    about = point_about if point_about is not None else [first]
    return {
        "entities": [{"name": s, "kind": "core:other"} for s in surfaces],
        "events": [],
        "points": [{
            "content": f"a claim that uses {first} in its own sentence",
            "pointKind": "statement",
            "about_entities": about,
            "quote": f"a claim that uses {first}",
        }],
        "operators": [],
    }


def _mint(el: dict) -> tuple[dict, dict]:
    """Run the referentiality selection, then the REAL mint boundary."""
    held = route_mentions(copy.deepcopy(el), "")
    res = execute_embed(held, {}, session_id="ref-11021-test")
    return res["payload"], {"held": held, "res": res}


def _entity_names(payload: dict) -> set[str]:
    return {str(e.get("name")) for e in payload.get("entities") or []}


def _mentions_of(payload: dict) -> list[dict]:
    out: list[dict] = []
    for point in payload.get("points") or []:
        out.extend(point.get("mentions") or [])
    return out


# ── the predicate, directly ────────────────────────────────────────────────

@pytest.mark.parametrize("surface,rule", [
    ("it", ref.RULE_PRONOUN),
    ("They", ref.RULE_PRONOUN),
    ("those", ref.RULE_PRONOUN),
    ("something", ref.RULE_PRONOUN),
    ("the former", ref.RULE_DEICTIC),
    ("the above", ref.RULE_DEICTIC),
    ("the same", ref.RULE_DEICTIC),
    ("the thing", ref.RULE_GENERIC_HEAD),
    ("the whole thing", ref.RULE_GENERIC_HEAD),
    ("the problem", ref.RULE_GENERIC_HEAD),
    ("the rest", ref.RULE_GENERIC_HEAD),
])
def test_a_phrase_whose_referent_is_not_in_the_phrase_is_a_mention(surface, rule):
    got = ref.judge(surface)
    assert got is not None, f"{surface!r} must be held as a mention"
    assert got[0] == rule
    assert got[0] in ref.RULES


@pytest.mark.parametrize("surface", [
    # the MUST-KEEP arm — every one of these is a referent, and every one is a
    # row a name-pattern rule would have deleted (#11021 Indicators).
    "config/ci-surfaces.yml",
    "the admin-merge rail",
    "the plan doc",
    "the parity harness",          # a `not_an_entity` label — the ARBITER's call, not this cue's
    "the vocabulary-pinned test",   # ditto
    "the staging-only constraint",  # ditto
    "the agent-infra workflow-lock issue",
    "Alice",
    "guard 2",
    # pointers: #2453/#4899's identifier-only DISCARD owns these, not this cue
    "PR #465",
    "issue #3775",
    "_TOOL_BY_NAME",
    "tests/test_frontmatter_validator.py",
    "daniel-ospina/agent-infra",
    "feat/2779-slice1-display-name",
])
def test_a_phrase_that_names_a_referent_is_not_held(surface):
    """POSITIVE CONTROL. A cue that holds these is not a referentiality test —
    it is the name-pattern rule `#1026` refused, wearing a new name."""
    assert ref.judge(surface) is None, f"{surface!r} names a referent and must mint"


def test_an_empty_or_tokenless_surface_is_never_held():
    for surface in ("", "   ", "---", None, 12):
        assert ref.judge(surface) is None


# ── T1: the false-minting case, proven at the MINT boundary ────────────────

def test_T1_a_mention_is_recorded_on_its_claim_and_mints_no_object():
    payload, ctx = _mint(_embed_list("it", "the admin-merge rail"))

    # 0. STRUCTURAL, not behavioural: the candidate is already gone from
    #    `entities[]` before the mint loop runs, so the no-mint guarantee does not
    #    depend on the mint loop remembering to ask.
    assert "it" not in {e["name"] for e in ctx["held"]["entities"]}
    assert ctx["held"]["mention_stats"]["held_surfaces"] == 1

    # 1. the mention never became an entity
    assert "it" not in _entity_names(payload), (
        "a pronoun minted an :Object — the class #11021 exists to stop")
    mentions = _mentions_of(payload)
    assert [m["surface"] for m in mentions] == ["it"]
    # 2. it is recorded on the CLAIM, with the rule that held it and the quote
    assert mentions[0]["rule_id"] == ref.RULE_PRONOUN
    assert mentions[0]["claim_ref"], "a mention with no claim is not a record"
    assert mentions[0]["kind_guess"] == "core:other"
    # 3. the positive control in the SAME list still minted
    assert "the admin-merge rail" in _entity_names(payload)
    # 4. and the contract still validates (Layer-1 about_entities ⊆ entities)
    verdict, _parsed = validate_payload_dict(payload)
    assert verdict.ok is True, (
        f"the held surface broke the Layer-1 contract: {verdict.errors}")


def test_T1_the_held_surface_is_stripped_from_about_entities():
    payload, _ = _mint(_embed_list("it"))
    point = payload["points"][0]
    assert "it" not in point["about_entities"], (
        "the claim still claims to be ABOUT a mention — Layer-1 would 422 here")
    assert [m["surface"] for m in point["mentions"]] == ["it"]


def test_T1_a_generic_head_is_held_too():
    payload, _ = _mint(_embed_list("the problem", "config/ci-surfaces.yml"))
    assert "the problem" not in _entity_names(payload)
    assert {m["surface"] for m in _mentions_of(payload)} == {"the problem"}
    assert "config/ci-surfaces.yml" in _entity_names(payload)


def test_T1_every_claim_that_used_the_mention_carries_it():
    el = _embed_list("it")
    el["points"].append({"content": "a second claim, same pronoun",
                         "pointKind": "statement", "about_entities": ["it"]})
    payload, _ = _mint(el)
    carrying = [p for p in payload["points"] if p.get("mentions")]
    assert len(carrying) == 2, "the mention must ride EVERY claim that used it"
    assert all([m["surface"] for m in p["mentions"]] == ["it"] for p in carrying)


# ── zero mints from the channel itself ─────────────────────────────────────

def test_the_mention_channel_never_mints_even_for_a_referential_surface():
    """A mention the MODEL emitted is a mention by construction: the channel's
    contract is `this phrase is not a node`. Promotion would make the channel a
    decoration on `entities[]`."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "the admin-merge rail",
                       # ⚠️ claim_ref must RESOLVE to a real claim (contract row:
                       # "a mention with no resolvable claim is refused") — so it
                       # is the claim's own content, not a prefix of it.
                       "claim_ref": el["points"][0]["content"],
                       "quote": "the admin-merge rail"}]
    payload, _ = _mint(el)
    assert "the admin-merge rail" not in _entity_names(payload)
    assert "the admin-merge rail" in {m["surface"] for m in _mentions_of(payload)}


def test_a_mention_is_idempotent_under_two_passes():
    """The S2 and union passes both run the selection; a second pass must not
    duplicate the record or resurrect the entity."""
    el = _embed_list("it")
    once = route_mentions(copy.deepcopy(el), "")
    twice = route_mentions(copy.deepcopy(once), "")
    assert twice["entities"] == once["entities"]
    assert len(twice["mentions"]) == len(once["mentions"]) == 1


def test_a_second_pass_keeps_the_record_s_span_provenance():
    """The union pass re-records every existing entry THROUGH the channel branch,
    which hard-set `span_source = "model"` — so by the end of a session the field
    that says WHICH text the offsets address claimed the model supplied offsets
    this module derived, and the `reason` described a derived record as a
    model-emitted one (review cycle 3, P2)."""
    el = _embed_list("it")
    once = route_mentions(copy.deepcopy(el), "")
    twice = route_mentions(copy.deepcopy(once), "")
    first, second = once["mentions"][0], twice["mentions"][0]
    assert first["span_source"] == "quote" and second["span_source"] == "quote"
    assert (first["span_start"], first["span_end"]) == (
        second["span_start"], second["span_end"])
    assert second["reason"] == first["reason"], (
        "pass 2 re-described a derived record as a model-emitted one")


def test_a_span_never_addresses_bytes_the_record_does_not_store():
    """The span was located in the carrier's UNCLAMPED quote and then the stored
    quote was cut to 200: a surface past offset 200 shipped `span_source: "quote"`
    with offsets that resolve to nothing in the quote the record names (review
    cycle 3, P2)."""
    tail = "a claim that uses it at the very end"
    quote = ("lead " * 45) + tail          # > 200 chars, `it` lands past the cap
    el = _embed_list("it")
    el["points"] = [{"content": "a claim that uses it in its own sentence",
                     "pointKind": "statement", "about_entities": ["it"],
                     "quote": quote}]
    held = route_mentions(copy.deepcopy(el), "")
    rec = held["mentions"][0]
    assert len(rec["quote"]) <= 200
    if rec["span_source"] == "quote":
        assert rec["quote"][rec["span_start"]:rec["span_end"]] == "it", rec


def test_a_model_supplied_span_source_must_VERIFY():
    """Honouring a declared `span_source` (so a re-entering record keeps its
    provenance) also honoured one a MODEL supplied: an entry could assert `quote`
    for offsets it made up and the payload shipped an address whose provenance field
    names a text it does not index (review cycle 4, P2)."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{
        "surface": "the problem", "quote": "the quick brown fox",
        "claim_ref": el["points"][0]["content"],
        "span_start": 3, "span_end": 9, "span_source": "quote"}]
    payload, _ = _mint(el)
    rec = _mentions_of(payload)[0]
    assert rec["span_source"] == "model", rec
    assert rec["quote"][rec["span_start"]:rec["span_end"]] != "the problem"
    # …and a VERIFYING declared space is still honoured, or a re-entering record
    # loses the provenance this branch exists to keep
    ok = _embed_list("config/ci-surfaces.yml")
    ok["mentions"] = [{
        "surface": "the problem", "quote": "the problem is real",
        "claim_ref": ok["points"][0]["content"],
        "span_start": 0, "span_end": 11, "span_source": "quote"}]
    payload_ok, _ = _mint(ok)
    assert _mentions_of(payload_ok)[0]["span_source"] == "quote"


def test_a_carried_record_is_constructed_not_copied():
    """The `seeded` carry copied a channel entry VERBATIM, so an entry that guessed
    the cue's own `rule_id` (the S4 prompt renders the whole embed list, so the shape
    is visible) shipped a `span_source` naming a text that does not index it, and
    skipped the `_MENTION_CAPS["quote"]` clamp on the way (review cycle 5, P2)."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{
        "surface": "the problem", "quote": "the quick brown fox",
        "claim_ref": el["points"][0]["content"],
        "span_start": 3, "span_end": 9, "span_source": "quote",
        "rule_id": ref.RULE_GENERIC_HEAD}]
    payload, _ = _mint(el)
    rec = _mentions_of(payload)[0]
    assert rec["span_source"] == "model", rec
    assert rec["quote"][rec["span_start"]:rec["span_end"]] != "the problem"
    # …and the cue's own rule id is still carried, which is what the branch exists for
    assert rec["rule_id"] == ref.RULE_GENERIC_HEAD
    # a long quote is clamped before the span is judged, so no address outlives its text
    long_el = _embed_list("config/ci-surfaces.yml")
    tail = "the problem is real"
    long_quote = ("pad " * 60) + tail          # `the problem` sits past the 200 cap
    long_el["mentions"] = [{
        "surface": "the problem",
        "quote": long_quote,
        "claim_ref": long_el["points"][0]["content"],
        "span_start": long_quote.index(tail), "span_end": len(long_quote),
        "span_source": "quote",
        "rule_id": ref.RULE_GENERIC_HEAD}]
    payload_long, _ = _mint(long_el)
    rec_long = _mentions_of(payload_long)[0]
    # the quote is CLAMPED before the declaration is judged, so offsets that index
    # past the stored text cannot keep claiming `quote`
    assert len(rec_long["quote"]) <= 200
    assert rec_long.get("span_source") == "model", rec_long


# ── the flag ───────────────────────────────────────────────────────────────

def test_the_predicate_is_off_at_the_default(monkeypatch):
    """Plan R1 — *fail-open to entity until measured*. Arming an unmeasured cue
    reverses that ruling; `#10996`'s ON default does not transfer, because its rate
    had been measured and committed first."""
    monkeypatch.delenv(ref.ENV_FLAG, raising=False)
    assert ref.enabled() is False, (
        "the unmeasured default must be fail-open to entity (plan R1)")


def test_the_flag_and_garbage_read_off_and_one_arms_it(monkeypatch):
    for value in ("0", "false", "", "  "):
        monkeypatch.setenv(ref.ENV_FLAG, value)
        assert ref.enabled() is False, f"{value!r} must read OFF"
    for value in ("1", "true", "yes"):
        monkeypatch.setenv(ref.ENV_FLAG, value)
        assert ref.enabled() is True, f"{value!r} must ARM the cue"


def test_the_unarmed_default_turns_the_selection_off(monkeypatch):
    """Unarmed = the referentiality MACHINERY is off-path, payload included.

    The first draft of this row asserted only `entities`, so it passed against any
    implementation that left the entity list alone while still attaching mentions
    to the payload (review P2). It now checks the payload, and that the caller's
    own list was not mutated.
    """
    monkeypatch.delenv(ref.ENV_FLAG, raising=False)
    el = _embed_list("it")
    before = copy.deepcopy(el)
    routed = route_mentions(el, "")
    assert [e["name"] for e in routed["entities"]] == ["it"], (
        "unarmed, the entity list must be untouched (plan R1)")
    assert "mention_stats" not in routed, (
        "an unarmed run must add NOTHING to the list — even a stats key made an "
        "S2-empty session look non-empty and zeroed stats['s4_full_rescues']")
    payload = execute_embed(routed, {}, session_id="unarmed")["payload"]
    assert "it" in _entity_names(payload)
    assert _mentions_of(payload) == [], (
        "an unarmed run must not attach the channel to the payload")
    assert el == before, "route_mentions mutated the caller's embed list"


# ── the four doors a hold must not leak through (review P0/P1) ─────────────

def test_a_hold_also_strips_an_EVENT_about_list():
    """Layer-1 validates `events[i].about_entities` as well as points'. The first
    draft stripped only points, so a held pronoun named by an event 422'd the
    WHOLE session on the very run that armed the cue (review P0)."""
    el = _embed_list("it", "the admin-merge rail")
    el["events"] = [{"content": "an event about the pronoun", "eventKind": "core:event",
                     "about_entities": ["it", "the admin-merge rail"]}]
    payload, ctx = _mint(el)
    assert ctx["held"]["mention_stats"]["events_stripped"] == 1
    assert ctx["held"]["mention_stats"]["mention_carrier_is_event"] == 1
    assert payload["events"][0]["about_entities"] == ["the admin-merge rail"]
    verdict, _ = validate_payload_dict(payload)
    assert verdict.ok is True, verdict.errors


def test_a_second_sighting_of_a_held_surface_cannot_resurrect_it():
    """The union pass (and a duplicate name with another kind) re-enters with NO
    live claim reference, because pass 1 removed it. The first draft refused the
    hold as `hold_abandoned_no_claim` and MINTED — the selection undoing itself on
    the pass that exists to make it hold (review P0)."""
    el = _embed_list("it")
    once = route_mentions(copy.deepcopy(el), "")
    # S4 re-emits the surface as an entity with no about_entities reference
    once["entities"] = once["entities"] + [{"name": "it", "kind": "core:event"}]
    twice = route_mentions(once, "")
    assert [e["name"] for e in twice["entities"]] == ["the admin-merge rail"] or \
        [e["name"] for e in twice["entities"]] == [], twice["entities"]
    assert "it" not in {e["name"] for e in twice["entities"]}
    payload = execute_embed(twice, {}, session_id="resurrect")["payload"]
    assert "it" not in _entity_names(payload)


def test_an_operator_naming_a_held_surface_is_dropped():
    """`execute_embed`'s mint-before-wire pass (#2552) turns an unresolvable
    operator endpoint into a fabricated statement Point — so a held pronoun left
    in an operator came back as a CLAIM node (#11021 review P1)."""
    el = _embed_list("it", "the admin-merge rail")
    el["points"].append({"content": "a second claim", "pointKind": "statement",
                         "about_entities": ["the admin-merge rail"]})
    # ⚠️ The endpoint must be one ONLY the hold can prune: if `dst` were the held
    # entity the operator would drop anyway (an entity endpoint is unresolvable),
    # and the row would pass with the prune disabled (review cycle 2, P3).
    el["operators"] = [{"src": "it",
                        "dst": "a claim that uses it in its own sentence",
                        "op_type": "IMPL"}]
    payload, ctx = _mint(el)
    assert ctx["held"]["mention_stats"]["operators_dropped"] == 1
    assert payload["operators"] == []
    assert "it" not in {p["content"] for p in payload["points"]}, (
        "mint-before-wire fabricated a claim Point from a pronoun endpoint")


def test_an_endpoint_only_pronoun_does_not_abort_the_selection():
    """The endpoint pass put the surface in `held_surfaces` but not in `hold`, so
    `by_rule` raised KeyError and the caller's fail-open guard voided the WHOLE
    selection — the pronoun minted and the operator survived (review cycle 3, P1)."""
    el = _embed_list("the admin-merge rail")
    el["operators"] = [{"src": "it",           # `it` is NOT in entities[]
                        "dst": "the admin-merge rail", "op_type": "IMPL"}]
    routed = route_mentions(el, "")
    assert routed["mention_stats"]["operators_dropped"] == 1
    assert routed["mention_stats"]["held_surfaces"] == 1
    assert routed["operators"] == []


def test_a_model_supplied_cue_rule_cannot_delete_a_referent():
    """A channel entry tagged `referentiality.pronoun` for a surface `judge()` says
    is a REFERENT must not re-arm a hold — the tag is a claim, not a fact (review
    cycle 3, P1)."""
    el = _embed_list("the admin-merge rail")
    el["mentions"] = [{"surface": "the admin-merge rail",
                       "claim_ref": el["points"][0]["content"],
                       "rule_id": ref.RULE_PRONOUN, "quote": "the admin-merge rail"}]
    routed = route_mentions(el, "")
    assert [e["name"] for e in routed["entities"]] == ["the admin-merge rail"]
    assert routed["mentions"] == []


def test_an_abandoned_hold_neither_strips_nor_prunes_nor_counts():
    """`held_surfaces` must say what its name says: a surface whose hold was
    REFUSED (no claim to record it against) keeps its entity, keeps its operators,
    and is not counted (review cycle 3, P2)."""
    el = {"entities": [{"name": "the problem", "kind": "core:other"},
                       {"name": "the admin-merge rail", "kind": "core:other"}],
          "points": [{"content": "a claim about the rail", "pointKind": "statement",
                      "about_entities": ["the admin-merge rail"]}],
          "events": [], "mentions": [],
          "operators": [{"src": "the problem", "dst": "the admin-merge rail",
                         "op_type": "IMPL"}]}
    routed = route_mentions(el, "")
    stats = routed["mention_stats"]
    assert stats["hold_abandoned_no_claim"] == 1
    assert stats["held_surfaces"] == 0
    assert stats["operators_dropped"] == 0
    assert routed["operators"] == el["operators"]


def test_a_long_claim_s_mention_still_lands():
    """`execute_embed` truncates a claim to `_MAX_CONTENT`; a `claim_ref` recorded
    from the RAW text never resolved, so the mention was lost and reported as a
    fold that never happened (review cycle 3, P2)."""
    el = _embed_list("it")
    el["points"][0]["content"] = "x" * 1300 + " it"
    payload, ctx = _mint(el)
    assert ctx["res"]["stats"]["mentions_attached"] == 1, (
        f"the mention did not land: {ctx['res']['stats']}")
    assert ctx["res"]["stats"]["mentions_unresolved"] == 0
    assert _mentions_of(payload)[0]["surface"] == "it"


def test_S4_cannot_overwrite_a_held_record_s_rule_and_span():
    """S4's contract-shaped re-statement carries no `rule_id` and no span, so
    "S4 wins on collision" replaced the derived record with a provenance-free one
    (review P1). The two sides are complementary and are merged field-wise."""
    from tortoise.extractor_v2 import merge_embed_lists
    derived = {"surface": "it", "claim_ref": "the claim",
               "quote": "the claim that used it", "span_start": 3,
               "span_end": 5, "span_source": "quote",
               "kind_guess": "core:other", "rule_id": ref.RULE_PRONOUN,
               "reason": "pronoun"}
    s2 = {"entities": [], "mentions": [derived]}
    s4 = {"entities": [], "mentions": [{"surface": "it", "claim_ref": "the claim",
                                        "quote": "the claim that used it"}]}
    merged = merge_embed_lists(s2, s4)["mentions"]
    assert len(merged) == 1
    assert merged[0]["rule_id"] == ref.RULE_PRONOUN
    assert merged[0]["span_start"] == 3 and merged[0]["span_end"] == 5


def test_a_hallucinated_mention_field_cannot_422_the_payload():
    """`commit_schema.Mention` caps surface/quote at 200 and rejects a half span.
    The model path copied those fields verbatim, so one bad field lost the whole
    payload (review P1). Both are now clamped/dropped on the way in."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "X" * 250,
                       "claim_ref": el["points"][0]["content"],
                       "quote": "Y" * 300,
                       "span_start": 4}]          # a half span
    payload, _ = _mint(el)
    mentions = _mentions_of(payload)
    assert len(mentions) == 1
    assert len(mentions[0]["surface"]) == 200
    assert len(mentions[0]["quote"]) == 200
    assert "span_start" not in mentions[0] and "span_end" not in mentions[0]
    verdict, _ = validate_payload_dict(payload)
    assert verdict.ok is True, verdict.errors


def test_a_span_is_a_whole_word_address_and_says_which_text():
    """Substring arithmetic put a span on the `us` inside `usual`, and bare
    offsets did not say whether they address the quote or the narrative (review
    P2). Whole-word, unambiguous, and labelled."""
    from tortoise.extractor_v2 import _mention_span
    assert _mention_span("the usual thing", "us") is None
    assert _mention_span("we said it twice: it", "it") is None     # ambiguous
    assert _mention_span("we said it", "it") == (8, 10)
    # a non-ASCII letter or a digit blocks the match: `it` inside `itération`
    # names different bytes than the surface does (review cycle 2, P3)
    assert _mention_span("itération", "it") is None
    assert _mention_span("it2", "it") is None

    el = _embed_list("it")
    payload, _ = _mint(el)
    rec = _mentions_of(payload)[0]
    assert rec["span_source"] in ("quote", "narrative")
    if rec["span_source"] == "quote":
        quote = payload["points"][0]["quote"]
        assert quote[rec["span_start"]:rec["span_end"]] == "it"


def test_the_counters_do_not_conflate_opposite_outcomes():
    """`hold_abandoned_no_claim` (fail-open: the entity stays) and
    `channel_refused_no_claim` (the model's mention is dropped) lose opposite
    things, so one counter for both reads as a rate nobody can act on (#11021
    review P2)."""
    el = {"entities": [{"name": "it", "kind": "core:other"}],   # no claim names it
          "points": [{"content": "an unrelated claim",
                      "pointKind": "statement", "about_entities": []}],
          "events": [], "operators": [],
          "mentions": [{"surface": "the thing", "claim_ref": "no such claim"}]}
    routed = route_mentions(el, "")
    stats = routed["mention_stats"]
    assert stats["hold_abandoned_no_claim"] == 1
    assert stats["channel_refused_no_claim"] == 1
    assert stats["channel_refused_surfaces"] == ["the thing"]
    assert stats["held_surfaces"] == 0
    assert [e["name"] for e in routed["entities"]] == ["it"], "fail-open: kept"


def test_a_mention_whose_claim_a_duplicate_folded_away_is_REPORTED():
    """"every claim_ref resolves to an emitted point" (DM2) is only true if the
    rows a NOOP/duplicate/MERGE folded away are NAMED rather than silently
    dropped (#11021 review P2)."""
    el = _embed_list("it")
    # two identical claims: the second is an intra-batch duplicate and never
    # reaches the payload, so its mention record cannot attach
    el["points"].append(dict(el["points"][0]))
    _payload, ctx = _mint(el)
    stats = ctx["res"]["stats"]
    assert stats["mentions_recorded"] == 1        # deduped to one (surface, claim)
    assert stats["mentions_attached"] == 1
    assert stats["mentions_unresolved"] == 0

    # the losing case, forced: a record whose claim never reaches the payload
    routed = route_mentions(_embed_list("it"), "")
    routed["mentions"].append({"surface": "the former",
                               "claim_ref": "a claim this run never emits",
                               "quote": "the former"})
    res = execute_embed(routed, {}, session_id="unresolved")
    assert res["stats"]["mentions_recorded"] == 2
    assert res["stats"]["mentions_attached"] == 1
    assert res["stats"]["mentions_unresolved"] == 1
    assert any("did not emit" in w for w in res["warnings"]), res["warnings"]


def test_route_mentions_never_mutates_the_callers_lists():
    """The first draft mutated the caller's point dicts before the exception
    guard, so a later failure left claims stripped of their aboutness with no
    record to show for it (review P2)."""
    el = _embed_list("it")
    before = copy.deepcopy(el)
    route_mentions(el, "")
    assert el == before


def test_the_contract_carries_the_channel():
    """Decomposition row J's contract pin: `mentions[]` must be IN the prompt
    contract and IN the shape gate, or the channel is a decoration on
    `entities[]` and the model is never asked for it.

    ⚠️ ARMED contract, not `OUTPUT_CONTRACT`: the channel is injected into the
    prompt only when the cue is armed (see the row below), because an unarmed run
    discards it — and a model asked for a channel nobody reads routes referents
    into it, neither minted nor recorded (review cycle 3, P1)."""
    from tortoise.extractor_v2 import (
        _OUTPUT_SCHEMA,
        _prompt_contract,
        render_s4_prompt,
    )
    assert '"mentions":' in _prompt_contract()
    # BOTH prompt builders read the same owner, so S4 asks for the channel too
    assert '"mentions":' in render_s4_prompt(
        "a story", {}, {"entities": [], "points": []})
    assert "mentions" in _OUTPUT_SCHEMA
    assert _OUTPUT_SCHEMA["mentions"] == (), (
        "a required key here turns a salvageable partial response into a "
        "_ParseError on the default path")


def test_an_unarmed_prompt_does_not_ask_for_the_mention_channel(monkeypatch):
    """`OUTPUT_CONTRACT` carried the channel line unconditionally, so an UNARMED
    run — the production default — moved a referential phrase OUT of `entities[]`
    into a channel that is then discarded: the phrase was neither minted nor
    recorded, the opposite of plan R1's *fail-open to entity until measured*
    (review cycle 3, P1)."""
    from tortoise.extractor_v2 import (
        OUTPUT_CONTRACT,
        OUTPUT_CONTRACT_CORE_ONLY,
        _prompt_contract,
        render_s2_prompt,
    )

    monkeypatch.delenv(ref.ENV_FLAG, raising=False)
    assert '"mentions":' not in render_s2_prompt(), (
        "the default (unarmed) prompt still asks for a channel the run discards")
    # …and the unarmed contract is the constant it always was, so the flag-off
    # prompt stays byte-identical to pre-#11021
    assert _prompt_contract() == OUTPUT_CONTRACT
    assert _prompt_contract(core_only=True) == OUTPUT_CONTRACT_CORE_ONLY
    monkeypatch.setenv(ref.ENV_FLAG, "1")
    assert '"mentions":' in render_s2_prompt()


def test_an_unarmed_run_reports_no_record_it_did_not_make(monkeypatch):
    """With the flag OFF the channel is ignored, so the accounting must not claim
    records it never wrote, and must not warn about a fold that never happened
    (review cycle 2, P2)."""
    monkeypatch.delenv(ref.ENV_FLAG, raising=False)   # the autouse fixture ARMS it
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "the problem", "claim_ref": None}]
    res = execute_embed(route_mentions(el, ""), {}, session_id="unarmed-accounting")
    assert res["stats"]["mentions_recorded"] == 0
    assert res["stats"]["mentions_unresolved"] == 0
    assert not any("did not emit" in w for w in res["warnings"])


def test_the_span_ceiling_mirrors_the_schema():
    """A mirrored constant with no drift pin is how the address ceiling rots."""
    from tortoise.commit_schema import SPAN_OFFSET_MAX
    from tortoise.extractor_v2 import _SPAN_OFFSET_MAX
    assert _SPAN_OFFSET_MAX == SPAN_OFFSET_MAX


def test_an_over_cap_or_junk_span_cannot_422_the_payload():
    """`commit_schema.Mention` caps offsets at the int64 ceiling and pins
    `span_source` to a Literal; the model path copied both verbatim, so one bad
    pair lost the whole payload (review cycle 2, P1)."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "a thing", "claim_ref": el["points"][0]["content"],
                       "span_start": 0, "span_end": 10**19,
                       "span_source": "paragraph"}]
    payload, _ = _mint(el)
    rec = _mentions_of(payload)[0]
    assert "span_start" not in rec and "span_end" not in rec
    assert "span_source" not in rec
    verdict, _ = validate_payload_dict(payload)
    assert verdict.ok is True, verdict.errors


def test_an_entity_and_a_channel_entry_for_one_surface_do_not_disagree_across_passes():
    """The re-arm must never let the module's OWN channel marker remove an entity:
    a surface the model called both an entity and a mention is an entity in every
    pass, or a 70-reference must-keep referent is deleted one pass later (review
    cycle 2, P1)."""
    el = _embed_list("the admin-merge rail")
    el["mentions"] = [{"surface": "the admin-merge rail",
                       "claim_ref": el["points"][0]["content"],
                       "quote": "the admin-merge rail"}]
    once = route_mentions(copy.deepcopy(el), "")
    twice = route_mentions(copy.deepcopy(once), "")
    assert [e["name"] for e in once["entities"]] == ["the admin-merge rail"]
    assert [e["name"] for e in twice["entities"]] == ["the admin-merge rail"], (
        "the second pass removed a referent the first pass kept")
    assert once["mention_stats"]["channel_entry_is_entity"] == 1
    # the conflict is resolved on pass 1 (no record is written), so pass 2 has no
    # entry to disagree with — the point being that neither pass removes the entity
    assert twice["mention_stats"]["channel_entry_is_entity"] == 0
    assert twice["mention_stats"]["held_surfaces"] == 0


def test_the_domain_nouns_a_corpus_measurement_excluded_are_excluded():
    """The first GENERIC_HEADS draft carried this repo's own node types, and
    `the point` alone occurs 893 times in `docs/` + `tortoise/` (measured at
    277a559b1). Each row below is a referent the cue must not hold (#11021
    review P1)."""
    for surface in ("the point", "the points", "the question", "the number",
                    "the step", "the steps", "the result", "the case", "the time",
                    "the part", "the instance", "the instance of", "the detail"):
        assert ref.judge(surface) is None, (
            f"{surface!r} is a referent in this corpus and must mint")


# ── the 12-row must-keep arm — this issue's own measurement ────────────────

#: The three rows of the 12-row must-keep arm that #11021's Indicators name
#: VERBATIM (with their reference counts): every one is a row a NAME-PATTERN rule
#: would have deleted. ⚠️ The other nine are #10995's census and are not committed as
#: a readable list at this head, so this lane pins the three it can source rather
#: than inventing a population — the arm is measured in full by the run that lands
#: the 12-row number, not by a hand-copied tuple here.
MUST_KEEP: tuple[str, ...] = (
    "config/ci-surfaces.yml",   # 123 references
    "the admin-merge rail",     # 70 references
    "the plan doc",
)

#: The SHAPE CLASS the arm is made of — `the <content-noun>` with a compound
#: modifier. Held here so the cue's declared boundary is tested as a class and not
#: only as three strings: `the parity harness` is a `not_an_entity` row of this same
#: shape, which is exactly why no surface rule may decide it.
MUST_KEEP_SHAPE: tuple[str, ...] = (
    *MUST_KEEP,
    "the parity harness",
    "the vocabulary-pinned test",
    "the staging-only constraint",
    "the agent-infra workflow-lock issue",
    "the reaper probe sys.modules.pop nit",
)


def test_the_COMMITTED_capstone_corpus_is_not_touched():
    """The arm that HAS positive examples, measured on the repo's own committed
    corpus (`docs/evidence/7949-capstone/corpus/e1e2_corpus.json`: the 25
    owner-confirmed non-entity Objects plus every `about` surface the corpus's
    claims name). This is the reproducible number the lane reports as
    `entities-minted`; the cue must hold ZERO of them.

    ⚠️ It is also the honest answer to "how many mentions does a real run hold":
    **none**, because no surface in this corpus is in the cue's class — the
    production mention rate is UNMEASURED, which is exactly why the flag ships
    OFF (plan R1).
    """
    path = REPO / "docs" / "evidence" / "7949-capstone" / "corpus" / "e1e2_corpus.json"
    corpus = json.loads(path.read_text())
    surfaces = sorted({o["name"] for o in corpus["objects"]}
                      | {n for p in corpus["points"] for n in (p.get("about") or [])})
    assert len(surfaces) >= 25, f"the corpus moved: {len(surfaces)} surfaces"
    held = [(n, ref.judge(n)) for n in surfaces if ref.judge(n) is not None]
    print(f"\n[#11021] capstone corpus: {len(surfaces)} surfaces, "
          f"{len(held)} held, {len(surfaces) - len(held)} still mint")
    assert held == [], f"the cue held corpus referents: {held}"


def test_the_mention_arm_A_declared_sample_is_held():
    """The positive arm: the cue must hold its OWN class. These are lane-authored
    (a pronoun, a bare demonstrative, a relational head, a generic head) — there is
    no owner-labelled pronoun population in the repo, and saying so is more useful
    than an unmeasured hit rate presented as a result."""
    sample = ("it", "They", "those", "something", "the former", "the above",
              "the same", "the thing", "the whole thing", "the problem", "the rest")
    missed = [s for s in sample if ref.judge(s) is None]
    assert missed == [], f"the cue missed its own class: {missed}"


def test_the_must_keep_arm_is_not_touched_by_the_cue():
    """Specificity on the arm that HAS positive examples. Holding these would make
    the cue the name-pattern rule `#1026` refused, wearing a new name."""
    held = [(s, ref.judge(s)) for s in MUST_KEEP_SHAPE if ref.judge(s) is not None]
    assert held == [], f"the cue held must-keep referents: {held}"


def test_the_25_row_label_set_is_the_ARBITERS_class_not_this_cue_s():
    """The 25 owner-confirmed `not_an_entity` rows are all definite descriptions,
    identifiers and pointers — none is a pronoun, deictic or generic head, so this
    cue's hit count on them is 0 BY CONSTRUCTION. What this row proves is the
    boundary (the cue claims none of the arbiter's class), NOT a precision figure;
    claiming otherwise is how a lane reports a measurement it never made.
    """
    path = REPO / "tests" / "eval" / "write_path" / "salience_labels.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) == 25, f"the label set moved: {len(rows)} rows"
    assert all(r["verdict"] == "not_an_entity" for r in rows)

    held = [r["object_name"] for r in rows
            if ref.judge(r["object_name"]) is not None]
    # ⛔ THIS SET CANNOT MEASURE THE CUE. It has 15 definite-description rows and
    # 10 identifier/pointer rows and **no pronoun, deictic or generic-head row at
    # all**, which are the only classes the cue fires on — so a hit count here is
    # a tautology, not a number (review P2). It is asserted as a BOUNDARY: the
    # cue must decline every row of the arbiter's class, and the arbiter's
    # precision number is #10996's to own.
    print(f"\n[#11021] boundary: the cue holds {len(held)}/{len(rows)} of the 25 "
          f"not_an_entity rows (0 is CORRECT here — no row is in its class)")
    # ⛔ THE HONEST BOUNDARY, PINNED IN BOTH DIRECTIONS. The cue declines every
    # definite description on purpose: `the admin-merge rail` (a 70-reference
    # referent) and `the parity harness` (a not_an_entity row) are the SAME
    # surface shape, so telling them apart is meaning — the arbiter's `ref-v1`,
    # never a surface rule. A future lane that widens this cue to catch the
    # definite descriptions reddens here and has to argue for the change.
    definite = [r["object_name"] for r in rows
                if r["class"] == "definite_description"]
    assert len(definite) == 15, f"the definite-description arm moved: {len(definite)}"
    assert [n for n in definite if ref.judge(n) is not None] == [], (
        "the cue claimed a definite description — that class is the arbiter's, "
        "and the must-keep arm contains rows of the identical shape")
    assert len(held) == 0


# ── review cycle 5: the UNION pass carries decisions, it does not re-derive ─
#
# Every P1 in cycles 1–5 lived in the same corner: the interaction between pass
# 1, the S4 union, the `mentions[]` channel and the fail-open guard. Pass 2
# re-enters with a list pass 1 already rewrote, so anything it REBUILDS from that
# list disagrees with pass 1 — a re-emitted surface has no reference left, a
# re-read record loses its `span_source`, a long claim no longer resolves. These
# rows drive TWO passes, which is the only configuration production runs and the
# one the single-pass rows could not see.

def _union_pass(el: dict, **s4_changes) -> dict:
    """Pass 1, then the S4 union pass on its OUTPUT — what `extract_session_v2`
    does (S2 → S4 merge → union), with S4's edits supplied by the caller."""
    once = route_mentions(copy.deepcopy(el), "")
    for key, value in s4_changes.items():
        if key == "add_entity":
            once["entities"] = [*once["entities"], value]
        elif key == "add_point":
            once["points"] = [*once["points"], value]
        else:                                     # pragma: no cover - test helper
            raise AssertionError(f"unknown s4 change {key!r}")
    return route_mentions(once, "")


def test_an_EVENT_only_hold_is_not_minted_by_the_union_pass():
    """⛔ THE REGRESSION (review cycle 5, P1). `execute_embed`'s ref set is
    points-only, so a surface whose only carrier is an EVENT writes no record —
    and with no surface-level memory of the decision, pass 2 found no carrier,
    abandoned the hold and MINTED the entity pass 1 had held. That is #11021's
    central guarantee inverted on the ordinary two-pass pipeline, silently."""
    el = {"entities": [{"name": "it", "kind": "core:other"},
                       {"name": "the admin-merge rail", "kind": "core:policy"}],
          "events": [{"content": "it broke the deploy", "eventKind": "core:event",
                      "about_entities": ["it"]}],
          "points": [{"content": "a claim about the admin merge rail",
                      "pointKind": "statement",
                      "about_entities": ["the admin-merge rail"],
                      "quote": "a claim about the admin merge rail"}],
          "operators": []}
    once = route_mentions(copy.deepcopy(el), "")
    assert once["mention_stats"]["mention_carrier_is_event"] == 1
    assert once["mention_stats"]["held_surfaces"] == 1
    # S4 reviews gaps against the S3 index, where the entity was still visible.
    twice = _union_pass(el, add_entity={"name": "it", "kind": "core:object"})
    assert "it" not in {e["name"] for e in twice["entities"]}, (
        "the union pass minted the surface pass 1 held")
    assert twice["mention_stats"]["hold_abandoned_no_claim"] == 0
    payload = execute_embed(copy.deepcopy(twice), {}, session_id="ev-union")["payload"]
    assert "it" not in _entity_names(payload), (
        "held-then-minted: the phantom node #11021 exists to delete")


def test_a_held_record_survives_the_union_pass_when_S4_re_emits_the_entity():
    """Pass 2 used to read this module's OWN record as "the model said entity"
    (`candidate_surfaces` was built from the pre-hold list), rebuild `mentions[]`
    from scratch and drop the record — the surface then lived in no entity, no
    `about_entities` and no mention, with no warning (review cycle 4, P1-b)."""
    twice = _union_pass(_embed_list("it"),
                        add_entity={"name": "it", "kind": "core:object"})
    assert [m["surface"] for m in twice["mentions"]] == ["it"]
    assert twice["mention_stats"]["channel_entry_is_entity"] == 0, (
        "the module's own carried record was counted as a model contradiction")


def test_a_held_record_is_BYTE_IDENTICAL_after_the_second_pass():
    """The idempotence row asserted only `entities` and a COUNT, so it passed
    while pass 2 rewrote every derived record's `span_source` to `"model"` (the
    offsets address the claim's QUOTE, not a model emission) and replaced the
    cue's `reason` with the channel's boilerplate (review at 41fb0c65e, P1)."""
    once = route_mentions(_embed_list("it"), "")
    twice = route_mentions(copy.deepcopy(once), "")
    assert twice["mentions"] == once["mentions"]
    assert once["mentions"][0]["span_source"] in ("quote", "narrative")


def test_a_bare_about_reference_S4_added_is_still_stripped():
    """`held_surfaces` was rebuilt from what the entity loop kept, so a claim S4
    added whose `about_entities` names an already-held surface (with no entity
    behind it) kept the reference — and Layer-1 422'd the whole session, where
    the fail-open `try/except` could not see it (review cycle 4, P1-a)."""
    once = route_mentions(_embed_list("it"), "")
    once["points"] = [*once["points"],
                      {"content": "S4's new claim", "pointKind": "statement",
                       "about_entities": ["it"]}]
    twice = route_mentions(once, "")
    assert twice["points"][-1]["about_entities"] == [], (
        "a bare reference to a held surface survived the union pass")
    payload = execute_embed(copy.deepcopy(twice), {}, session_id="bare-ref")["payload"]
    verdict, _ = validate_payload_dict(payload)
    assert verdict.ok is True, verdict.errors


def test_a_mention_on_a_CLAIM_over_the_cap_survives_the_union_pass():
    """`claim_ref` is the WRITE's window (`_write_span(...strip(), _MAX_CONTENT)`),
    so pass 2 must key claims the same way — keyed on the raw content, a record
    over the cap resolved against nothing and was refused as
    `channel_refused_no_claim` (review cycle 4, P1-c)."""
    el = _embed_list("it")
    el["points"][0]["content"] = "x" * 1300 + " it"
    twice = _union_pass(el)
    assert [m["surface"] for m in twice["mentions"]] == ["it"]
    assert twice["mention_stats"]["channel_refused_no_claim"] == 0
    res = execute_embed(copy.deepcopy(twice), {}, session_id="long-union")
    assert res["stats"]["mentions_attached"] == 1, res["stats"]


def test_a_carried_hold_cannot_hold_a_referent():
    """`mention_held` rides the merged embed list, and S4 is rendered that whole
    list — so the model can emit one. It must re-verify against `judge()` like
    every other model-supplied hold, or it is a one-field delete of a referent
    (review cycle 3, P1, one door over)."""
    el = _embed_list("the admin-merge rail")
    el["mention_held"] = [{"surface": "the admin-merge rail",
                           "rule_id": ref.RULE_PRONOUN}]
    routed = route_mentions(el, "")
    assert [e["name"] for e in routed["entities"]] == ["the admin-merge rail"]
    assert routed["mention_stats"]["held_surfaces"] == 0
    assert "mention_held" not in routed, (
        "a refused carried decision must not be re-published as held")


def test_an_event_only_hold_names_the_record_it_cannot_write():
    """An event is a carrier (its `about_entities` is stripped), but a record
    against it can never resolve — so the hold writes NO record. That used to be
    silent while the docstring promised "a mention no claim can carry is
    refused" (review cycle 4, P2). The surface is still held; the gap is named."""
    el = {"entities": [{"name": "it", "kind": "core:other"}],
          "events": [{"content": "it broke the deploy", "eventKind": "core:event",
                      "about_entities": ["it"]}],
          "points": [{"content": "an unrelated claim", "pointKind": "statement",
                      "about_entities": []}],
          "operators": []}
    routed = route_mentions(el, "")
    stats = routed["mention_stats"]
    assert stats["held_surfaces"] == 1
    assert stats["mention_carrier_is_event"] == 1
    assert stats["mention_unrecorded_surfaces"] == ["it"]
    assert routed["mentions"] == []
    assert routed["events"][0]["about_entities"] == []


def test_a_MITIGATES_target_alias_cannot_mint_a_held_surface():
    """`execute_embed`'s mint pre-pass reads `target` OR `target_edge`, so a
    pronoun named in the `target` spelling was materialised as a fabricated
    statement Point while the `target_edge` spelling was pruned correctly
    (review cycle 4, P2)."""
    el = _embed_list("it", "the admin-merge rail")
    el["operators"] = [{"src": "the admin-merge rail",
                        "dst": "the admin-merge rail", "op_type": "MITIGATES",
                        "target": {"src": "it",
                                   "dst": "the admin-merge rail"}}]
    payload, ctx = _mint(el)
    assert ctx["held"]["mention_stats"]["operators_dropped"] == 1
    assert payload["operators"] == []


def test_a_model_rule_id_cannot_be_stored_as_a_hold_that_never_happened():
    """The model's `rule_id` was copied verbatim into `Mention.rule_id`, so an
    entry tagged `referentiality.pronoun` on a surface the cue calls a REFERENT
    was stored as a cue attribution for a rule that never ran — on the evidence
    surface plan R1's arming gate reads (review cycle 4, P3)."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "the admin-merge rail",
                       "claim_ref": el["points"][0]["content"],
                       "quote": "the admin-merge rail",
                       "rule_id": ref.RULE_PRONOUN}]
    routed = route_mentions(el, "")
    assert routed["mentions"][0]["rule_id"] == ref.RULE_CHANNEL
    assert routed["mention_stats"]["held_surfaces"] == 0


def test_a_boolean_offset_is_not_an_address():
    """`isinstance(True, int)` is `True`: `span_start: true` was accepted, stored
    as `span_source: "model"` and coerced to `1` by the model — an address naming
    bytes the surface never occupied (review P2)."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "the thing",
                       "claim_ref": el["points"][0]["content"],
                       "span_start": True, "span_end": 2}]
    payload, _ = _mint(el)
    rec = _mentions_of(payload)[0]
    assert "span_start" not in rec and "span_end" not in rec
    assert "span_source" not in rec


def test_an_unarmed_run_publishes_no_held_record(monkeypatch):
    """`result["mentions"]["records"]` was flag-agnostic, so a run with the cue
    OFF published the model's raw `mentions[]` entries as "the mentions held"
    when nothing was held (review cycle 4, P3). Driven through
    `extract_session_v2`, the only place that block is assembled."""
    from tortoise.extractor_v2 import extract_session_v2

    monkeypatch.delenv(ref.ENV_FLAG, raising=False)   # the fixture ARMS it

    class _M:
        last_finish_reason = None

        def complete(self, *, system, user, max_tokens=None):
            self.last_finish_reason = "stop"
            if "STORY SUMMARIZER" in system:
                return "A narrative."
            if "GAP REVIEWER" in system:
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [], "chain_notes": [], '
                        '"link_before_create": []}')
            return ('{"entities": [{"name": "ACME Corp", "kind": "core:org"}], '
                    '"events": [], "operators": [], '
                    '"points": [{"content": "the budget is 40k", '
                    '"pointKind": "statement"}], "chain_notes": [], '
                    '"link_before_create": [], '
                    '"mentions": [{"surface": "the plan doc", '
                    '"claim_ref": "the budget is 40k"}]}')

    out = extract_session_v2(_M(), [{"role": "user",
                                     "content": "the budget is 40k"}],
                             session_id="unarmed-records")
    assert out["mentions"]["records"] == [], (
        "an unarmed run reported records it never made")


# ── the SECOND reference set: typed participant SLOTS ──────────────────────
# Layer-1 resolves two reference sets against the emitted entities:
# `about_entities` (`points[i]`/`events[i]`) AND the typed `subject`/`object`
# slots (`_resolve_slot_refs`: "does not resolve to an emitted entity"). The
# selection read only the first, so a mention referenced ONLY from a slot had
# no carrier: the hold was abandoned, the phantom was MINTED (fail-open) and
# the slot survived to the payload. #11021's guarantee is that a mention is
# never promoted to an :Object — and a slot reference IS a promotion path.

def _slot_only_embed_list(surface: str = "it") -> dict:
    """One entity, and ONE claim whose ONLY reference to it is a typed slot."""
    return {
        "entities": [{"name": surface, "kind": "core:other"}],
        "events": [],
        "points": [{
            "content": f"a claim that uses {surface} in its own sentence",
            "pointKind": "statement",
            "about_entities": [],
            "quote": f"a claim that uses {surface}",
            "slots": {"subject": [{"name": surface, "kind": "core:other",
                                   "confidence": 0.93}]},
        }],
        "operators": [],
    }


def test_a_reference_that_is_ONLY_a_SLOT_is_held_and_recorded_not_minted():
    """⛔ THE CARRIER SET WAS INCOMPLETE (review cycle 5, P1). A surface the model
    emitted as an entity and referenced only from `slots.subject` had no carrier
    in `_claims_naming`, so the hold was abandoned and the phantom was MINTED —
    the exact promotion #11021 exists to prevent, through the second reference
    set Layer-1 resolves."""
    el = _slot_only_embed_list()
    routed = route_mentions(copy.deepcopy(el), "")
    stats = routed["mention_stats"]
    assert stats["hold_abandoned_no_claim"] == 0, (
        "a slot reference IS a claim to record the mention on")
    assert stats["held_surfaces"] == 1
    assert [m["surface"] for m in routed["mentions"]] == ["it"]
    payload, _ = _mint(el)
    assert "it" not in _entity_names(payload), (
        "the phantom node minted through the slot reference")
    assert [m["surface"] for m in _mentions_of(payload)] == ["it"], (
        "the mention was not recorded on the claim that used it")


def test_a_held_surface_is_stripped_from_the_slots_that_reference_it():
    """Stripping `about_entities` alone left the slot dangling: Layer-1 then drops
    it with `slot subject 'it' … does not resolve to an emitted entity — dropped`,
    a warning that reads as an EXTRACTION error for a decision the run made on
    purpose. Both reference sets are stripped, and `slots.event` (event-as-content,
    never resolved against entities) is left alone."""
    el = _slot_only_embed_list()
    el["points"][0]["slots"]["event"] = [
        {"name": "it", "kind": "core:event", "confidence": 0.8}]
    routed = route_mentions(copy.deepcopy(el), "")
    # ⛔ Asserted on the ROUTED list, not on the payload: Layer-1 drops a
    # dangling slot too, so a payload-only assertion passes whether WE stripped
    # it or Layer-1 reported it as an extraction error (measured: the mutant that
    # makes `_strip_held_slot_refs` a no-op left a payload-only assertion green).
    assert not (routed["points"][0].get("slots") or {}).get("subject"), (
        f"the dangling slot survived the selection: {routed['points'][0].get('slots')}")
    res = execute_embed(copy.deepcopy(routed), {}, session_id="slot-strip")
    payload = res["payload"]
    point = payload["points"][0]
    assert not (point.get("slots") or {}).get("subject"), (
        f"the dangling slot survived: {point.get('slots')}")
    assert not any("does not resolve to an emitted entity" in str(w)
                   for w in res.get("warnings") or []), (
        f"Layer-1 reported our own decision as an extraction error: {res.get('warnings')}")
    assert [r["name"] for r in point["slots"]["event"]] == ["it"], (
        "slots.event is event-as-content and must pass through untouched "
        "(its kind may be repaired by _clean_slots; its REFERENCE must survive)")


def test_a_reference_that_is_ONLY_an_OBJECT_slot_is_held_too():
    """The carrier set must cover BOTH participant roles, not only `subject`.

    Measured: narrowing `_SLOT_ENTITY_ROLES` to `("subject",)` left the whole
    suite green — the `object` half of the second reference set was pinned by
    nothing, so a regression that stopped judging it would mint a phantom with no
    red row (review cycle 6, P3)."""
    el = _slot_only_embed_list()
    slots = el["points"][0]["slots"]
    slots["object"] = slots.pop("subject")
    routed = route_mentions(copy.deepcopy(el), "")
    stats = routed["mention_stats"]
    assert stats["hold_abandoned_no_claim"] == 0, (
        "an `object` slot reference IS a claim to record the mention on")
    assert stats["held_surfaces"] == 1
    assert [m["surface"] for m in routed["mentions"]] == ["it"]
    assert not (routed["points"][0].get("slots") or {}).get("object"), (
        f"the dangling object slot survived: {routed['points'][0].get('slots')}")
    payload, _ = _mint(el)
    assert "it" not in _entity_names(payload), (
        "the phantom minted through the `object` slot reference")


def test_an_EVENT_whose_only_carrier_is_a_slot_is_held_and_its_slot_stripped():
    """An EVENT is a carrier in BOTH reference sets, so its typed slots must be
    stripped like a point's.

    Measured: skipping the slot branch for `eventKind` carriers left the whole
    suite green while the event's `slots.subject` survived the hold — exactly the
    dangling slot Layer-1 reports as `slot subject 'it' … does not resolve to an
    emitted entity`, an extraction-error warning for a decision the run made on
    purpose (review cycle 6, P3). Asserted on the ROUTED list for the same reason
    the point row is: Layer-1 also drops a dangling slot, so a payload-only
    assertion stays green whether WE stripped it or Layer-1 reported it.

    ⚠️ This strips ``events[]`` reference sets, which #11021's decision **C4**
    declined ("no stripping of `events[]`"). The departure is not introduced
    here — #11211 already shipped it and pinned the ``about_entities`` half at
    ``test_an_event_only_hold_names_the_record_it_cannot_write`` — and it is
    relayed to #11021 as an ``OVERRIDES:`` record: C4's refusal would fail open
    and MINT the very object #11021 exists to delete, whereas holding and
    stripping keeps the no-mint guarantee and counts the unrecorded surface in
    ``mention_carrier_is_event`` / ``mention_unrecorded_surfaces``."""
    el = {
        "entities": [{"name": "it", "kind": "core:other"}],
        "points": [],
        "events": [{
            "content": "an event that uses it in its own sentence",
            "eventKind": "occurrence",
            "about_entities": [],
            "startedAt": None,
            "slots": {"subject": [{"name": "it", "kind": "core:other",
                                   "confidence": 0.9}]},
        }],
        "operators": [],
    }
    routed = route_mentions(copy.deepcopy(el), "")
    stats = routed["mention_stats"]
    assert stats["hold_abandoned_no_claim"] == 0, (
        "an event slot reference IS a carrier, so the hold is not abandoned — "
        "it simply cannot carry the DM1 record (see mention_unrecorded_surfaces)")
    assert "it" not in {e["name"] for e in routed["entities"]}
    assert not (routed["events"][0].get("slots") or {}).get("subject"), (
        f"the event's dangling slot survived: {routed['events'][0].get('slots')}")
    assert stats["events_stripped"] == 1
    assert stats["mention_carrier_is_event"] == 1
    assert stats.get("mention_unrecorded_surfaces") == ["it"], (
        "an event-only hold writes no DM1 record and must SAY so")


def test_a_slot_only_hold_survives_the_union_pass():
    """The carried decision must cover the second reference set too: pass 2 sees
    the payload pass 1 rewrote (entity gone, slot gone), so re-deriving would
    abandon the hold and mint the phantom on the ordinary two-pass pipeline."""
    el = _slot_only_embed_list()
    twice = route_mentions(copy.deepcopy(route_mentions(el, "")), "")
    assert twice["mention_stats"]["hold_abandoned_no_claim"] == 0
    assert "it" not in {e["name"] for e in twice["entities"]}
    assert [m["surface"] for m in twice["mentions"]] == ["it"]
    payload = execute_embed(copy.deepcopy(twice), {}, session_id="slot-union")["payload"]
    assert "it" not in _entity_names(payload)


# ── the mention-only response: the gate must not sit behind `entities[]` ──

def test_a_mention_only_response_cannot_smuggle_a_rule_that_never_ran():
    """⛔ `route_mentions` returned early whenever `entities` was not a list, so a
    section the parsed output OMITS (`_validate_output_shape`: `if section not in
    parsed: continue`) skipped the cue re-verification entirely — and
    `_mentions_for_claim` then copied the model's `rule_id` verbatim, storing
    `referentiality.pronoun` as a cue attribution on a surface `judge()` calls a
    REFERENT (review at `591906397`, P2). Reachable in ONE model output: the armed
    prompt asks for `mentions[]`, and a model that believes the session has no
    entities can emit the channel without `entities[]`.

    Proven at the MINT boundary, which is where the record is actually stored."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "the admin-merge rail",
                       "claim_ref": el["points"][0]["content"],
                       "quote": "the admin-merge rail",
                       "rule_id": ref.RULE_PRONOUN}]
    del el["entities"]                     # the mention-only response
    payload, _ = _mint(el)
    records = _mentions_of(payload)
    assert [m["surface"] for m in records] == ["the admin-merge rail"], (
        "the mention-only response dropped the record it was asked for")
    assert records[0]["rule_id"] == ref.RULE_CHANNEL, (
        "a cue rule that never ran on this surface was stored as evidence")


def test_a_mention_only_response_keeps_a_rule_the_cue_AGREES_with():
    """POSITIVE CONTROL for the row above: the clamp must not flatten every
    `rule_id` to `referentiality.channel` — a cue rule the cue re-derives for the
    same surface is the one value the record exists to carry."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{"surface": "it",
                       "claim_ref": el["points"][0]["content"],
                       "quote": "it",
                       "rule_id": ref.RULE_PRONOUN}]
    del el["entities"]
    payload, _ = _mint(el)
    records = _mentions_of(payload)
    assert [m["surface"] for m in records] == ["it"]
    assert records[0]["rule_id"] == ref.RULE_PRONOUN


# ── the record's boundary: it reaches the claim Point, and the tree says so ──

def test_the_mention_record_reaches_the_claim_point_and_says_so():
    """⛔ DM1's HOME, ASSERTED FROM BOTH SIDES (persisted by `#11239`). DM1
    names the claim Point as the record's home and `Point.mentions` exists;
    between `#11211` and `#11239` the record stopped at the payload, so an armed
    run's `mentions_recorded` counted records the write DISCARDED and a reader of
    the graph saw the claim and no mention on it.

    This row pins the boundary in BOTH directions, because a one-sided pin is how
    the deferral survived: it REDs if persistence is REMOVED (the doors stop
    carrying it, the encoder stops running, the declaration disappears) and it
    REDs if the record is over-advertised (a REST `CreatePointRequest` field, or
    `mentions` added to `_CAPTURE_PASSTHROUGH_PROPS`, which is `#2949`'s public
    capture RESPONSE surface — `validFrom`'s standing note).
    """
    import inspect

    from tortoise import hosted_api, selfhost_api
    from tortoise.commit_schema import Mention
    from tortoise.consistency import _POINT_JSON_RECORD_PROPS as _CONSISTENCY_SET
    from tortoise.projection.entities import (
        _POINT_JSON_RECORD_PROPS,
        _EntityHandlers,
        encode_json_record_props,
    )
    from tortoise.sdk import _CAPTURE_PASSTHROUGH_PROPS, TortoiseSDK

    # the record EXISTS at the payload — "payload-only" was never "nothing"
    payload, _ = _mint(_embed_list("it"))
    assert [m["surface"] for m in _mentions_of(payload)] == ["it"]

    # ── the PERSISTENCE half, door by door ────────────────────────────────
    # 1. the hosted commit door — the one the v2 payload actually rides. It adds
    #    the key ONLY when the payload carries a record: `mentions=_mention_props
    #    or None` on a dedup hit would REMOVE a stored record (a `None` in a
    #    generic prop clears the property), so the empty case must not speak.
    _commit_src = inspect.getsource(hosted_api._execute_commit_writes)
    assert '_mention_props = [' in _commit_src, (
        "the commit route no longer reads the payload point's `mentions`: "
        "DM1's home is empty again — see #11239")
    assert _commit_src.count('point_props["mentions"] = _mention_props') == 2, (
        "both create legs (supersede and new) must carry `mentions`, and ONLY "
        "when the payload has one — a leg that passes None clears a stored "
        "record on the ordinary re-commit path")
    assert "mentions=_mention_props" not in _commit_src, (
        "a `mentions=` kwarg on the dedup write reintroduces the clearing bug: "
        "`create_point(dedup=True, mentions=None)` folds `mentions=None` onto "
        "the existing point, and `SET n += $props` carrying a None REMOVES it")
    # 2. the SDK capture seam: a create-only carrier, read off the payload point
    #    beside the other passthrough read (the `validFrom` shape — stored,
    #    never advertised).
    _capture_src = inspect.getsource(TortoiseSDK._extract_session_v2)
    assert '_mention_records = pt.get("mentions")' in _capture_src, (
        "the SDK capture seam stopped reading the payload point's `mentions`")
    assert '"mentions": _mention_records' in _capture_src, (
        "the SDK capture seam reads `mentions` and no longer carries it to the "
        "create — the record would be dropped at the write boundary")
    # 3. the ONE encoder, called from every Point door (live AND replay), so
    #    the engine is never handed an array of maps. `update_entity` delegates
    #    to `_update_entity`, which is where the Point branch writes caller
    #    props straight through.
    for door in ("create_point", "update_point", "_update_entity"):
        assert "_encode_json_record_props" in inspect.getsource(
            getattr(TortoiseSDK, door)), (
            f"{door} no longer encodes `mentions`, so the engine is handed an "
            "array of maps and the live write diverges from every rebuild")
    # 4. the declaration the writer reads — and the checker reads FROM it.
    assert "mentions" in _POINT_JSON_RECORD_PROPS
    assert "mentions" in _EntityHandlers._POINT_DECLARED_PROPS, (
        "`mentions` is not declared, so every capture that records a mention "
        "logs a FALSE \"Point prop 'mentions' is not declared\" drift warning")
    assert _CONSISTENCY_SET is _POINT_JSON_RECORD_PROPS, (
        "consistency must READ the writer's declaration rather than re-list it")

    # ── the encode itself: deterministic, lossless, canonical on re-entry ──
    once = {"mentions": [{"surface": "it", "span_start": 3, "span_end": 5},
                         {"surface": "the problem"}]}
    twice = {"mentions": [{"span_end": 5, "surface": "it", "span_start": 3},
                          {"surface": "the problem"}]}
    encode_json_record_props(once)
    encode_json_record_props(twice)
    assert once["mentions"] == twice["mentions"], (
        "the encode is not deterministic across field order, so live and "
        "rebuild would diverge on the same record")
    assert isinstance(once["mentions"], str), (
        "the encoded value must be ONE scalar — the engine cannot hold an array "
        "of maps (`_is_persistable_prop_value`)")
    assert json.loads(once["mentions"])[0]["surface"] == "it"
    encode_json_record_props(once)          # idempotent for a canonical string
    assert isinstance(once["mentions"], str)
    blank = {"mentions": []}
    encode_json_record_props(blank)
    assert "mentions" not in blank, (
        "an empty list must POP the key, never store `[]` and clear a value")
    model_form = {"mentions": [Mention(surface="it", span_start=3, span_end=5)]}
    encode_json_record_props(model_form)
    assert json.loads(model_form["mentions"])[0]["span_end"] == 5, (
        "a pydantic Mention must encode through its own model_dump")

    # ── the OTHER direction: nothing over-advertises the record ───────────
    assert "mentions" not in _CAPTURE_PASSTHROUGH_PROPS, (
        "`mentions` joined the capture RESPONSE surface: that is #2949's "
        "public create/dedup read-back — a stored-and-not-advertised field "
        "(the `validFrom` shape) must not become part of the response")
    assert "mentions" not in hosted_api.CreatePointRequest.model_fields, (
        "CreatePointRequest now accepts `mentions`: it is a writer-produced "
        "record, not a client field")
    assert "mentions" not in selfhost_api.CreatePointRequest.model_fields, (
        "the self-host REST door now accepts `mentions` — #4032's parity "
        "mirror must move with the hosted pair, and this field is neither's")

    # …and both docstrings NAME the doors, so a reader can check the claim.
    for doc in (Mention.__doc__ or "", route_mentions.__doc__ or ""):
        assert "selfhost_api.CreatePointRequest" in doc, (
            "a mention docstring does not name the self-host REST door, so its "
            "enumeration of which doors carry `mentions` is incomplete: "
            f"{doc[:200]!r}")
    assert "11239" in (Mention.__doc__ or ""), (
        "Mention's docstring no longer names the slice that persisted it, so a "
        "reader cannot tell what put the record on the node")
    assert "PAYLOAD-ONLY" not in (Mention.__doc__ or ""), (
        "Mention's docstring still says PAYLOAD-ONLY — the record IS on the "
        "claim Point since #11239, and a stale boundary is worse than none")


# ── the channel-only response must reach the LAST BUILDER, not just the door ──
# The `rule_id` clamp lives in `_mentions_for_claim` and runs on every record, so
# it kept the row above green even when `route_mentions` still returned early —
# which meant the OTHER half (`_mention_record`: span verification, dedupe, the
# quote clamp) was pinned by nothing. These two rows pin that half: they red only
# when a channel-only response bypasses the builder.

def test_a_mention_only_response_is_verified_by_the_last_builder():
    """⛔ The security half of the channel-only fix is `_mentions_for_claim`'s
    `rule_id` re-verification, which is the LAST DOOR every record passes. The
    other half — `route_mentions` processing the channel through `_mention_record`
    — is what VERIFIES a declared span against the haystack. Without it a
    mention-only response ships `span_source: "quote"` on offsets nothing supports:
    the exact provenance forgery `de036f2e` closed for the `entities[]` path.

    ⚠️ WHAT THIS ROW ACTUALLY PINS (corrected at `5da3575b7a`, P3): the LAST DOOR,
    not `route_mentions`' span half. `_clamped_mention` verifies the span too, so
    reverting the `route_mentions` early-return fix leaves THIS row green — measured
    (mutant A: 2 failed / 85 passed, and neither red is this row). Its sibling
    `test_a_mention_only_response_is_deduped_like_any_other` is what reds for that
    half. `route_mentions`' container half is pinned by
    `test_a_mention_only_response_is_HELD_at_the_predicate_and_stripped`."""
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{
        "surface": "the admin-merge rail",
        "claim_ref": el["points"][0]["content"],
        "quote": "a totally unrelated sentence with no rail in it",
        "span_source": "quote",          # a claim that must be CHECKED
        "span_start": 0,
        "span_end": 5,
        "rule_id": ref.RULE_CHANNEL,
    }]
    del el["entities"]
    payload, _ = _mint(el)
    records = _mentions_of(payload)
    assert [m["surface"] for m in records] == ["the admin-merge rail"], (
        "the channel-only response dropped the record it was asked for")
    assert records[0]["span_source"] == "model", (
        "a `quote`-declared span shipped UNVERIFIED on a channel-only response — "
        f"`route_mentions` handed the model's raw entry to the payload instead of "
        f"running `_mention_record`: {records[0]!r}")


def test_a_mention_only_response_is_deduped_like_any_other():
    """The second thing only `_mention_record`/`_dedupe_mentions` do. A duplicated
    channel entry would otherwise be stored twice — one hold, two records, and a
    `mentions_recorded` count that double-counts the evidence plan R1's arming gate
    reads."""
    el = _embed_list("config/ci-surfaces.yml")
    entry = {
        "surface": "the admin-merge rail",
        "claim_ref": el["points"][0]["content"],
        "quote": "the admin-merge rail",
        "rule_id": ref.RULE_CHANNEL,
    }
    el["mentions"] = [dict(entry), dict(entry)]
    del el["entities"]
    payload, _ = _mint(el)
    records = _mentions_of(payload)
    assert [m["surface"] for m in records] == ["the admin-merge rail"], (
        f"a duplicated channel entry was stored twice: {records!r}")


def test_a_response_that_omits_entities_cannot_resurrect_a_carried_hold():
    """The OTHER reachable shape of an omitted `entities[]`: a re-emitted response
    that carries `mention_held` and no entities. Returning early there discarded
    the decision, so a surface pass 1 held came back as a MINT on the next render —
    the phantom `#11021` exists to delete, reached without the model doing anything
    wrong (`_validate_output_shape` accepts a section the parsed output omits).

    An earlier `entities[]` that is not a list is a legitimate state; a *lost
    decision* is not, which is why the early return is now conditional on there
    being nothing at all to process."""
    el = _embed_list("it")
    el["mention_held"] = [{"surface": "it", "rule_id": ref.RULE_PRONOUN}]
    del el["entities"]
    held = route_mentions(copy.deepcopy(el), "")
    payload, _ = _mint(el)
    assert held["points"][0]["about_entities"] == [], (
        "a carried hold was dropped because the response omitted `entities[]`, "
        "so the held surface is still named by the claim and will MINT")
    assert _entity_names(payload) == set(), (
        f"the carried hold was resurrected as an entity: {_entity_names(payload)!r}")
    assert [m["surface"] for m in _mentions_of(payload)] == ["it"], (
        f"the hold lost its record: {_mentions_of(payload)!r}")


def test_the_last_door_clamps_a_rule_when_the_selection_FAILED_OPEN(monkeypatch):
    """⛔ `_mentions_for_claim` is the last door, and the fail-open path is the one
    place it stands alone: `execute_embed` catches ANY `route_mentions` exception
    and keeps the model's RAW `mentions[]` (`vet_warnings: "candidates kept
    (fail-open)"`). Nothing has verified those entries — so the attach step must
    re-derive `rule_id` itself, or a crashed selection ships the model's cue
    attribution verbatim on the evidence surface plan R1's arming gate reads.

    Measured before this row existed: removing the `rule_id` branch from
    `_mentions_for_claim` left the suite GREEN (84 passed), because every other
    row's record is sanitised earlier by `_mention_record`. The defence in depth
    was therefore a claim, not a behaviour."""
    import tortoise.extractor_v2 as ex

    def _boom(*_a, **_k):
        raise RuntimeError("selection exploded — the fail-open path")

    monkeypatch.setattr(ex, "route_mentions", _boom)
    el = _embed_list("config/ci-surfaces.yml")
    el["mentions"] = [{
        "surface": "the admin-merge rail",       # judge() calls this a REFERENT
        "claim_ref": el["points"][0]["content"],
        "quote": "the admin-merge rail",
        "rule_id": ref.RULE_PRONOUN,             # a rule that never ran on it
    }]
    payload = ex.execute_embed(copy.deepcopy(el), {}, session_id="fail-open-last-door")["payload"]
    records = _mentions_of(payload)
    assert [m["surface"] for m in records] == ["the admin-merge rail"], (
        "the fail-open path dropped the record")
    assert records[0]["rule_id"] == ref.RULE_CHANNEL, (
        "the fail-open path shipped the model's unverified cue rule as evidence: "
        f"{records[0]!r}")


# ── the EVIDENCE SURFACE, not just the payload ─────────────────────────────
# `result["mentions"]["records"]` is built from the merged embed list, not from
# `_mentions_for_claim`, so every clamp the last door performs had to be applied
# twice or the run's own evidence surface kept the model's raw claim. Driven
# through `extract_session_v2` (the only place that block is assembled) on the
# FAIL-OPEN path, where the raw channel is all `execute_embed` ever sees.

def _fail_open_session(monkeypatch, mentions: list[dict]) -> dict:
    """One session whose `route_mentions` RAISES, with `mentions` as the model's
    raw channel. The fail-open handler keeps the list unchanged, so nothing has
    verified the entries — which is exactly the state the last door defends."""
    from tortoise.extractor_v2 import extract_session_v2

    def _boom(*_a, **_k):
        raise RuntimeError("selection exploded — the fail-open path")

    monkeypatch.setattr("tortoise.extractor_v2.route_mentions", _boom)
    payload_json = json.dumps({
        "entities": [], "events": [], "operators": [],
        "points": [{"content": "the budget is 40k", "pointKind": "statement"}],
        "chain_notes": [], "link_before_create": [], "mentions": mentions,
    })

    class _M:
        last_finish_reason = None

        def complete(self, *, system, user, max_tokens=None):
            self.last_finish_reason = "stop"
            if "STORY SUMMARIZER" in system:
                return "A narrative."
            if "GAP REVIEWER" in system:
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": [], "chain_notes": [], '
                        '"link_before_create": []}')
            return payload_json

    return extract_session_v2(_M(), [{"role": "user", "content": "the budget is 40k"}],
                              session_id="fail-open-evidence")


def test_the_evidence_surface_does_not_carry_an_unverified_cue_rule(monkeypatch):
    """⛔ `result["mentions"]["records"]` is the surface a lane reads to count
    what the run held (plan R1's arming evidence). It was built straight from the
    merged `mentions[]`, so on the fail-open path — `execute_embed` catches ANY
    `route_mentions` exception and keeps the model's raw channel — it published
    `referentiality.pronoun` for a surface `judge()` calls a REFERENT, while the
    payload (clamped by the last door) said `referentiality.channel`. One run,
    two answers, and the unverified one was the one being read."""
    out = _fail_open_session(monkeypatch, [{
        "surface": "the admin-merge rail",
        "claim_ref": "the budget is 40k",
        "quote": "the admin-merge rail",
        "rule_id": ref.RULE_PRONOUN,      # a rule that never ran on this surface
    }])
    records = out["mentions"]["records"]
    assert [m["surface"] for m in records] == ["the admin-merge rail"], (
        f"the fail-open evidence surface dropped the record: {records!r}")
    assert records[0]["rule_id"] == ref.RULE_CHANNEL, (
        "the evidence surface published a cue rule that never ran, on the one "
        f"path where nothing verified it: {records[0]!r}")


def test_the_evidence_surface_does_not_carry_an_unverified_span(monkeypatch):
    """The same door, the neighbouring field: a `quote`-declared span is a claim
    that the offsets index THE QUOTE. Allow-listing the value is not verification
    — `span_source` is in `_MENTION_SPAN_SOURCES` either way — so an unverified
    declaration has to be demoted to the space that actually holds it (`model`,
    i.e. "the model asserted these bytes")."""
    out = _fail_open_session(monkeypatch, [{
        "surface": "the admin-merge rail",
        "claim_ref": "the budget is 40k",
        "quote": "a totally unrelated sentence with no rail in it",
        "span_source": "quote",
        "span_start": 0,
        "span_end": 5,
        "rule_id": ref.RULE_CHANNEL,
    }])
    records = out["mentions"]["records"]
    assert records, "the fail-open evidence surface dropped the record"
    assert records[0]["span_source"] == "model", (
        "the evidence surface shipped an address whose declared provenance names "
        f"a text it does not index: {records[0]!r}")


# ── the channel's decision has to be ENACTED, not just written ─────────────
# The three rows above prove the RECORD is clamped. None of them proves the
# surface is HELD: `hold` is a decision, and every consumer of it
# (`_strip_reference_sets`, the operator prune, `by_rule`, `mention_held`, the
# record loop) keys on `held_surfaces`. Arming `hold` without adding to
# `held_surfaces` is a decision nothing reads — measured: deleting that arm left
# this whole file GREEN (87 passed), because the clamp lives downstream at the
# last door and the channel record loop.

def test_a_mention_only_response_is_HELD_at_the_predicate_and_stripped():
    """⛔ The PREDICATE, not a downstream filter: the model said "this phrase is
    not a node", the cue was re-run on that surface and AGREED, so the hold is
    the selection's own decision. If it is only written to `hold`, the surface
    stays in `about_entities` (`validate_payload_dict`: "about_entities reference
    unknown entities" — Layer-1 rejects the session), no `mention_held` reaches
    the union pass, and `held_surfaces` reports 0 while a record names the cue.

    Mutating the arm to `pass` reddens this row and nothing else in the file."""
    el = _embed_list("it")
    el["mentions"] = [{"surface": "it",
                       "claim_ref": el["points"][0]["content"],
                       "quote": "it", "rule_id": ref.RULE_PRONOUN}]
    del el["entities"]                      # the mention-only response
    held = route_mentions(copy.deepcopy(el), "")
    assert held["mention_stats"]["held_surfaces"] == 1, (
        "the channel decided a hold and reported none held: "
        f"{held['mention_stats']!r}")
    assert held["points"][0]["about_entities"] == [], (
        "the held surface is still named by the claim — Layer-1 rejects that")
    assert held["mention_held"] == [{"surface": "it",
                                     "rule_id": ref.RULE_PRONOUN}], (
        f"the decision was not carried to the union pass: {held.get('mention_held')!r}")

    payload, _ = _mint(el)
    verdict, _parsed = validate_payload_dict(payload)
    assert verdict.ok is True, (
        f"the channel-only payload broke Layer-1: {verdict.errors}")
    assert [m["surface"] for m in _mentions_of(payload)] == ["it"]

    # …and the carry is what stops pass 2 minting it once an entity reappears
    back = copy.deepcopy(held)
    back["entities"] = [{"name": "it", "kind": "core:other"}]
    twice = route_mentions(back, "")
    assert "it" not in {e.get("name") for e in twice["entities"]}, (
        "the carried channel hold did not survive the union pass: the phantom "
        "node #11021 exists to delete came back")


# ── the clamp must not manufacture a label it cannot check ────────────────

def test_a_narrative_address_is_not_demoted_when_no_haystack_was_supplied():
    """⛔ NO HAYSTACK is not a FAILED verification (review at `5da3575b7a`, P2).
    `execute_embed`'s `story_arc` defaults to `""`, so any caller that omits it —
    every test, and any seam — passed an empty haystack, `_mention_span` returned
    `None`, and a genuinely cue-derived `narrative` address was relabelled
    `"model"`: a consumer that resolves the span per `span_source` was told the
    model invented offsets the cue had derived. Measured: the same held list
    through `execute_embed(..., story_arc=story)` said `narrative`, without it
    `model`."""
    from tortoise.extractor_v2 import _clamped_mention

    entry = {"surface": "it", "claim_ref": "c", "quote": "no pronoun here",
             "span_source": "narrative", "span_start": 6, "span_end": 8}
    assert _clamped_mention(dict(entry), narrative="")["span_source"] == "narrative", (
        "an absent haystack demoted a cue-derived address to `model`")
    # …and the guard is not a blanket amnesty: a haystack that DISAGREES still
    # demotes, which is the whole point of the verification.
    assert _clamped_mention(dict(entry), narrative="alpha it beta")["span_source"] == "narrative"
    assert _clamped_mention(dict(entry), narrative="no pronoun here or there")["span_source"] == "model"


def test_the_narrative_amnesty_does_not_extend_to_an_absent_QUOTE():
    """⛔ An absent NARRATIVE is an unknowable haystack; an absent QUOTE is a
    definite mismatch (`"quote": null` is IN the output contract and
    `Mention.quote` defaults to `""`). The first cut of the guard above exempted
    both, so a record declaring `span_source: "quote"` with no quote shipped its
    offsets VERIFIED-LABELLED — a consumer resolving `quote[start:end]` reads
    bytes that do not exist (review at `1a7072e22`, P2)."""
    from tortoise.extractor_v2 import _clamped_mention

    for quote in (None, ""):
        entry = {"surface": "it", "claim_ref": "c", "quote": quote,
                 "span_source": "quote", "span_start": 0, "span_end": 2}
        got = _clamped_mention(entry, narrative="whatever")
        assert got["span_source"] == "model", (
            f"a quote-space address with quote={quote!r} shipped as verified: {got!r}")
    # …and a quote that DOES contain the surface at the declared offsets keeps it
    ok = {"surface": "it", "claim_ref": "c", "quote": "x it y",
          "span_source": "quote", "span_start": 2, "span_end": 4}
    assert _clamped_mention(ok, narrative="")["span_source"] == "quote"


# ── the hold may not wait for the model to echo an internal rule id ────────
# `_MENTION_CONTRACT_LINE` asks for `{surface, claim_ref, quote, kind_guess}` —
# NO `rule_id` — and `merge_embed_lists` says the same of S4's contract-shaped
# entry ("it carries NO rule_id and NO span"). Requiring the model's string to
# equal the cue's was therefore a gate on an input the prompt never requests.

def test_a_mention_only_response_is_held_at_the_CONTRACT_shape():
    """⛔ The arm required `verdict[0] == recorded_rule`, so it was INERT for the
    only shape the prompt asks the model to emit: measured at `d785f3da8`,
    `{surface: "the thing", claim_ref: <a live claim>}` produced
    `held_surfaces: 0`, the surface stayed in `about_entities`, and Layer-1
    rejected the whole session ("about_entities reference unknown entities") —
    the exact failure the channel-only fix exists to close, still open for a
    model that follows the contract.

    The CUE decides. `judge()` returning a verdict IS the cue's decision to hold;
    the model's `rule_id` string cannot add or remove it (`verdict is None` still
    never holds — the row below — which is the cycle-3 P1 protection)."""
    el = _embed_list("it")
    el["mentions"] = [{"surface": "it",
                       "claim_ref": el["points"][0]["content"],
                       "quote": "it"}]          # the prompt shape: no rule_id
    del el["entities"]
    held = route_mentions(copy.deepcopy(el), "")
    assert held["mention_stats"]["held_surfaces"] == 1, held["mention_stats"]
    assert held["points"][0]["about_entities"] == [], (
        "the held surface is still named by the claim — Layer-1 rejects that")
    assert held["mention_held"] == [{"surface": "it",
                                     "rule_id": ref.RULE_PRONOUN}], (
        "a model-supplied surface with no rule string carried no cue rule: "
        f"{held.get('mention_held')!r}")

    payload, _ = _mint(el)
    verdict, _parsed = validate_payload_dict(payload)
    assert verdict.ok is True, (
        f"the contract-shaped mention-only payload broke Layer-1: {verdict.errors}")


def test_the_contract_shape_still_cannot_hold_a_REFERENT():
    """The negative control for the row above: the relaxation is the CUE's, not
    the model's. An entry with NO `rule_id` naming a must-keep referent is still
    not held, so a model cannot delete a 70-reference surface by omitting a
    field (`the admin-merge rail` is this repo's measured must-keep)."""
    el = _embed_list("the admin-merge rail")
    el["mentions"] = [{"surface": "the admin-merge rail",
                       "claim_ref": el["points"][0]["content"],
                       "quote": None}]
    del el["entities"]
    held = route_mentions(copy.deepcopy(el), "")
    assert held["mention_stats"]["held_surfaces"] == 0, held["mention_stats"]
    assert held["points"][0]["about_entities"] == ["the admin-merge rail"]


def test_a_mention_only_response_is_REFUSED_without_a_carrier():
    """POSITIVE CONTROL for the counter above: a channel entry that no claim names
    and whose `claim_ref` resolves to nothing is still REFUSED, counted, and named —
    the gate above must not be a blanket silence. (The hold gate refuses it because
    `_claims_naming` is empty; the record loop then has no claim and no carrier.)"""
    el = _embed_list("the admin-merge rail", point_about=[])
    el["mentions"] = [{"surface": "it", "claim_ref": "a claim this run never emits"}]
    del el["entities"]
    held = route_mentions(copy.deepcopy(el), "")
    assert held["mention_stats"]["held_surfaces"] == 0, held["mention_stats"]
    assert held["mention_stats"]["channel_refused_no_claim"] == 1, held["mention_stats"]
    assert held["mention_stats"].get("channel_refused_surfaces") == ["it"]
    assert held["mentions"] == []


def test_a_mention_the_model_cannot_TIE_to_a_claim_is_still_held():
    """⛔ The hold is a fact about the PAYLOAD, not about the model's
    `claim_ref` string — which the prompt allows to be `null`
    (`_MENTION_CONTRACT_LINE`: `str|null`) and which a model routinely
    paraphrases. A first cut also required that string to resolve
    `claims_by_ref`, so a surface a claim DID carry was not held, stayed in
    `about_entities`, and Layer-1 rejected the session — the 422 this whole fix
    exists to close, on an input the contract explicitly permits (review at
    `40a0a81e6`, P1). The record loop is CARRIER-driven, so the record is written
    against the carrier either way; only the HOLD was at stake."""
    for claim_ref in (None, "a paraphrase of the claim",
                      "a claim this run never emits"):
        el = _embed_list("it")
        el["mentions"] = [{"surface": "it", "claim_ref": claim_ref}]
        del el["entities"]
        held = route_mentions(copy.deepcopy(el), "")
        assert held["mention_stats"]["held_surfaces"] == 1, (
            claim_ref, held["mention_stats"])
        assert held["points"][0]["about_entities"] == [], claim_ref
        # ⛔ NOT a refusal: the hold is real and the carrier-driven loop wrote the
        # record, so counting the unresolvable ref here made ONE run report "held"
        # and "lost for lack of a claim" at once, naming that same surface (review
        # at `ea790070d`, P3). The refusal still fires when nothing can carry the
        # entry — covered by `test_a_mention_only_response_is_REFUSED_without_a_carrier`.
        assert held["mention_stats"]["channel_refused_no_claim"] == 0, claim_ref
        assert not held["mention_stats"].get("channel_refused_surfaces"), claim_ref
        assert [m["surface"] for m in held["mentions"]] == ["it"], claim_ref
        payload, _ = _mint(el)
        verdict, _parsed = validate_payload_dict(payload)
        assert verdict.ok is True, (claim_ref, verdict.errors)


# ── the clamp may not publish HALF an address ─────────────────────────────

def test_the_clamp_never_publishes_half_an_address():
    """⛔ A space label and its offset pair are ONE address. `_mention_record`
    can emit neither half alone (its label exists only inside the valid-pair
    branch, and an undeclared pair becomes `"model"`); `_clamped_mention` — which
    the evidence surface now goes through, so this is what
    `result["mentions"]["records"]` publishes — produced both halves (review at
    `d785f3da8`, P2), and `Mention._span_is_an_address` only checks
    completeness/order/range, so Layer-1 accepts either."""
    from tortoise.extractor_v2 import _clamped_mention

    # (a) the pair survived and nothing declared its space → the honest label
    undeclared = _clamped_mention({"surface": "it", "claim_ref": "c", "quote": "it",
                                   "span_start": 0, "span_end": 2}, narrative="")
    assert undeclared["span_source"] == "model", (
        f"a bare offset shipped with no space label: {undeclared!r}")

    # (b) a declared space whose offsets did NOT survive → the label goes with it
    for bad in ({"span_source": "quote", "span_start": 5},          # half a pair
                {"span_source": "quote", "span_start": 5, "span_end": 2},  # inverted
                {"span_source": "quote", "span_start": True, "span_end": 2}):  # bool
        half = _clamped_mention({"surface": "it", "claim_ref": "c", "quote": "it",
                                 **bad}, narrative="")
        assert "span_start" not in half and "span_end" not in half, half
        assert "span_source" not in half, (
            f"a space label outlived the address it describes: {half!r}")


# ── a surface needs a name, and the fail-open path counts one hold once ──────
# `_clamped_mention` is the ONLY verifier on the fail-open path: `execute_embed`
# catches any `route_mentions` exception and keeps the model's raw `mentions[]`,
# and both publishing surfaces (the payload and `result["mentions"]["records"]`)
# are built from it. Two shapes it still answered wrongly, plus the count.

def test_a_whitespace_surface_is_not_a_surface():
    """⛔ `Mention.surface` is `min_length=1`, so `"   "` passed validation and
    became a record with no name to resolve, and `" it "` matched nothing.
    `_dedupe_mentions` drops the whitespace-only shape on the normal path
    (`_norm` strips), which is why only the fail-open path and the direct clamps
    could see it. The strip must come BEFORE the 200-character cap, the order
    `_mention_record` uses: capping first collapsed a padded 202-character
    surface to the one-character `"i"` here while the sibling builder kept
    `"itx"` (review at `d785f3da8`, P3)."""
    from tortoise.extractor_v2 import _clamped_mention, _mention_record

    assert _clamped_mention({"surface": "   ", "claim_ref": "c"}) is None, (
        "a whitespace-only surface became a record")
    padded = _clamped_mention({"surface": " it ", "claim_ref": "c",
                               "quote": "x it y", "span_source": "quote",
                               "span_start": 2, "span_end": 4})
    assert padded["surface"] == "it", f"the surface was not stripped: {padded!r}"
    assert padded["span_source"] == "quote", (
        "stripping the surface must keep its verified address")

    long_padded = " " * 199 + "itx"
    assert _clamped_mention({"surface": long_padded, "claim_ref": "c"})["surface"] == "itx", (
        "a padded surface past the cap was truncated before it was stripped")
    assert _mention_record(surface=long_padded, claim={"content": "c"},
                           kind_guess=None, rule_id=ref.RULE_CHANNEL,
                           reason="t", narrative="", quote="")["surface"] == "itx"

    # …and a PADDED declared space is the same declaration (`_mention_record`
    # strips it, so leaving it raw demoted a verifiable address on one builder
    # only). It must be written back stripped, or the payload 422s.
    from tortoise.commit_schema import Mention
    padded_space = _clamped_mention({"surface": "it", "claim_ref": "c",
                                     "quote": "x it y", "span_source": " quote ",
                                     "span_start": 2, "span_end": 4})
    assert padded_space["span_source"] == "quote", (
        f"a padded declared space was demoted: {padded_space!r}")
    assert Mention(**padded_space).span_source == "quote"


def test_the_fail_open_path_dedupes_BOTH_publishing_surfaces(monkeypatch):
    """⛔ `_dedupe_mentions` runs INSIDE `route_mentions` — the call that may have
    raised. On the fail-open path nothing deduped, so one hold was counted twice
    on BOTH published lists AND in the accounting. The accounting matters most:
    `_records` is read straight from `embed_list`, so deduping only the published
    lists left `stats["mentions_recorded"] == 2` for one hold — the number plan
    R1's arming gate reads — while `records` said 1, i.e. the run disagreed with
    itself, and the "did not emit" warning counted two records while naming one
    surface (review at `d785f3da8`, P2)."""
    from tortoise.extractor_v2 import _mentions_for_claim

    entry = {"surface": "the admin-merge rail", "claim_ref": "the budget is 40k",
             "quote": "the admin-merge rail", "rule_id": ref.RULE_CHANNEL}
    direct = _mentions_for_claim({"mentions": [dict(entry), dict(entry)]},
                                 "the budget is 40k", armed=True, narrative="")
    assert [m["surface"] for m in direct] == ["the admin-merge rail"], (
        f"the payload clamp forwarded one hold twice: {direct!r}")

    out = _fail_open_session(monkeypatch, [dict(entry), dict(entry)])
    evidence = [m["surface"] for m in out["mentions"]["records"]]
    assert evidence == ["the admin-merge rail"], (
        f"the evidence surface double-counted one hold: {evidence!r}")
    payload = [m["surface"] for m in _mentions_of(out["payload"])]
    assert payload == ["the admin-merge rail"], (
        f"the fail-open payload double-counted one hold: {payload!r}")
    stats = out["stats"]
    assert stats["mentions_recorded"] == 1, (
        f"one hold was counted {stats['mentions_recorded']} times by the arming "
        f"number: {stats!r}")
    assert stats["mentions_unresolved"] == 0, (
        f"the emitted claim was reported unresolved: {stats!r}")
    assert not [w for w in out.get("warnings") or [] if "did not emit" in w], (
        f"the fail-open warning counted a hold twice: {out.get('warnings')!r}")


# ── ONE RUN, ONE ANSWER: the record may not credit the MODEL for the CUE's hold ──
# The rows below close the two-answers class at its last two sources. A run
# publishes the same hold on `stats["by_rule"]`, `mention_held[]` and the
# `mentions[]` records; plan R1's arming measurement reads the RECORDS, so a
# record that names a different rule is not a cosmetic mismatch — it counts a cue
# hold as a model assertion. Both were reported at `40a0a81e6` and re-verified at
# `0c15ad04d` (a peer lane's CLEAN verdict did not cover them).

def test_a_cue_held_mention_records_the_CUE_rule_not_the_model_silence():
    """⛔ The model-channel record loop stamped `entry.rule_id or RULE_CHANNEL`,
    ignoring the `(rule_id, reason)` the CHANNEL had just written into `hold` for
    that same surface. On the shape the armed prompt actually asks for — a mention
    whose claim does not also NAME it in `about_entities` — no hold-driven record
    exists, so the only record the run publishes said `referentiality.channel`
    ("the model said so") while `by_rule` and `mention_held` said
    `referentiality.pronoun`. One run, two answers to "what did this hold", on the
    surface plan R1's arming gate READS (review at `40a0a81e6`, P2; still present
    at `0c15ad04d`).

    Mutant: restoring `rule_id=str(entry.get("rule_id") or RULE_CHANNEL)` reddens
    this row and its payload half, and nothing else."""
    el = _embed_list("the admin-merge rail", point_about=[])
    el["mentions"] = [{
        "surface": "it",
        "claim_ref": el["points"][0]["content"],
        "quote": "it",
    }]
    del el["entities"]                      # the mention-only response

    held = route_mentions(copy.deepcopy(el), "")
    assert held["mention_stats"]["by_rule"] == {ref.RULE_PRONOUN: 1}, (
        f"the cue did not hold the surface: {held['mention_stats']!r}")
    assert held["mention_held"] == [{"surface": "it",
                                     "rule_id": ref.RULE_PRONOUN}], (
        f"the carried decision disagrees: {held.get('mention_held')!r}")

    records = held["mentions"]
    assert [m["surface"] for m in records] == ["it"], (
        f"the mention-only response dropped its record: {records!r}")
    assert records[0]["rule_id"] == ref.RULE_PRONOUN, (
        "the record credits the MODEL for a hold the CUE decided — the same run "
        f"reports by_rule={held['mention_stats']['by_rule']!r} but the record "
        f"plan R1 reads says {records[0]['rule_id']!r}: {records[0]!r}")

    # …and the payload's copy of the same record says the same thing.
    payload, _ = _mint(el)
    stored = _mentions_of(payload)
    assert [m["surface"] for m in stored] == ["it"], (
        f"the payload dropped its record: {stored!r}")
    assert stored[0]["rule_id"] == ref.RULE_PRONOUN, (
        f"the payload copy disagrees with the evidence copy: {stored[0]!r}")


def test_the_clamp_always_publishes_a_RULE_for_a_record():
    """⛔ `_clamped_mention`'s field loop SKIPS empty values before the `rule_id`
    branch, so an entry that OMITS the key shipped with no `rule_id` at all and
    `Mention.rule_id` defaulted to `""` — a value outside `referentiality.RULES`,
    i.e. the "no silent hold" field saying nothing. Reachable on the one path that
    matters: `_MENTION_CONTRACT_LINE` does not ask the model for a `rule_id`, and
    the fail-open path (the only route into this clamp that never passes
    `_mention_record`) is precisely where such an entry arrives (review at
    `40a0a81e6`, P2; still present at `0c15ad04d`).

    The value an ABSENT rule must become is `referentiality.channel` — "the model
    asserted it", nothing verified — NOT a cue rule: on the fail-open path the
    selection raised, so no cue verdict was reached for this record."""
    from tortoise.commit_schema import Mention
    from tortoise.extractor_v2 import _clamped_mention

    rec = _clamped_mention({"surface": "it", "claim_ref": "c"}, narrative="")
    assert rec is not None
    assert rec.get("rule_id") == ref.RULE_CHANNEL, (
        "an entry that omits `rule_id` — the shape the prompt asks for — was "
        f"published with rule_id={rec.get('rule_id')!r}, which is outside "
        f"{sorted(ref.RULES)}: {rec!r}")
    assert Mention(**rec).rule_id in ref.RULES, (
        "the record reaches the model with a rule the vocabulary does not hold")

    # …and the unconditional write is not a flattening: a cue rule the cue AGREES
    # with still survives, which is the whole point of the field.
    cue = _clamped_mention({"surface": "it", "claim_ref": "c",
                            "rule_id": ref.RULE_PRONOUN}, narrative="")
    assert cue.get("rule_id") == ref.RULE_PRONOUN, (
        f"the clamp flattened a cue rule it could verify: {cue!r}")


def test_the_record_builder_agrees_with_the_clamp_on_an_ABSENT_narrative():
    """⛔ TWO BUILDERS, ONE RECORD. `_clamped_mention` states — and pins — that an
    absent `narrative` is an UNKNOWABLE haystack, so a declared `narrative` space
    stands unchecked. `_mention_record` still ran `_mention_span("")` and demoted
    the same declaration to `"model"`, so the record published on
    `result["mentions"]["records"]` and the record published on the payload
    disagreed about WHICH TEXT the offsets address — for the same hold, in the same
    run (review at `40a0a81e6`, P2; still present at `0c15ad04d`). The clamp's rule
    is the correct side: relabelling to `model` asserts the model invented offsets
    the cue derived."""
    from tortoise.extractor_v2 import _mention_record

    span = {"span_start": 6, "span_end": 8, "span_source": "narrative"}
    absent = _mention_record(surface="it", claim={"content": "c"}, kind_guess=None,
                             rule_id=ref.RULE_PRONOUN, reason="r", narrative="",
                             quote="", span=dict(span))
    assert absent["span_source"] == "narrative", (
        "the record builder demoted a declared space on an ABSENT haystack, which "
        f"`_clamped_mention` keeps: {absent!r}")

    # …and the guard is not a blanket amnesty: a supplied haystack that DISAGREES
    # still demotes, exactly as the clamp does.
    disagree = _mention_record(surface="it", claim={"content": "c"},
                              kind_guess=None, rule_id=ref.RULE_PRONOUN,
                              reason="r", narrative="no pronoun here or there",
                              quote="", span=dict(span))
    assert disagree["span_source"] == "model", (
        f"an unverifiable declaration shipped as verified: {disagree!r}")
    agree = _mention_record(surface="it", claim={"content": "c"}, kind_guess=None,
                            rule_id=ref.RULE_PRONOUN, reason="r",
                            narrative="alpha it beta", quote="", span=dict(span))
    assert agree["span_source"] == "narrative", (
        f"a verifiable declaration was demoted: {agree!r}")


def test_the_fail_open_path_keys_a_record_to_the_claim_the_write_KEEPS(monkeypatch):
    """⛔ `_clamped_mention` re-derives "every model CLAIM" — except the one that
    decides ATTACHMENT. A `claim_ref` copied raw keys apart from its own point for
    any claim over `_MAX_CONTENT` (the write stores `_write_span(stripped, 1000)`),
    so the mention never attached and the run reported a claim it HAD emitted as
    folded away by a NOOP/duplicate/MERGE that never happened — the defect
    `_mention_record` fixed at its own call site (review cycle 4, P1-c), left open
    at the last door on the fail-open path (review at `b791a3eb2`, P2). Both sides
    of the match must use the same window, so the clamp cuts the ref AND
    `_mentions_for_claim` keys on the cut."""
    import tortoise.extractor_v2 as ex

    def _boom(*_a, **_k):
        raise RuntimeError("selection exploded — the fail-open path")

    monkeypatch.setattr(ex, "route_mentions", _boom)
    long_claim = ("the budget is 40k and " * 90).strip()
    assert len(long_claim) > ex._MAX_CONTENT
    el = _embed_list("it")
    el["points"][0]["content"] = long_claim
    el["mentions"] = [{"surface": "it", "claim_ref": long_claim,
                       "quote": "it", "rule_id": ref.RULE_PRONOUN}]
    res = ex.execute_embed(copy.deepcopy(el), {}, session_id="fail-open-long-claim")
    payload = res["payload"]
    attached = [m for p in payload["points"] for m in (p.get("mentions") or [])]
    assert [m["surface"] for m in attached] == ["it"], (
        "the mention did not attach to the claim the write kept: "
        f"{attached!r}")
    ref_len = len(attached[0]["claim_ref"])
    assert ref_len <= ex._MAX_CONTENT, (
        f"the record's claim_ref is longer than the write's window: {ref_len}")
    assert res["stats"]["mentions_attached"] == 1, (
        f"an attached record was not counted: {res['stats']!r}")
    assert res["stats"]["mentions_unresolved"] == 0, (
        f"a claim the run DID emit was reported unresolved: {res['stats']!r}")


def test_the_accounting_counts_what_the_run_PUBLISHED(monkeypatch):
    """⛔ Two raw entries whose surfaces agree only AFTER the 200-character cap were
    counted twice by `stats["mentions_recorded"]` while both published lists said
    one — the raw dedupe keys on the untruncated surface. That number is what plan
    R1's arming gate reads, so it has to be the records the run actually published
    (review at `b791a3eb2`, P3)."""
    long_a = "a" * 201 + "X"
    long_b = "a" * 201 + "Y"
    out = _fail_open_session(monkeypatch, [
        {"surface": long_a, "claim_ref": "the budget is 40k", "rule_id": ref.RULE_CHANNEL},
        {"surface": long_b, "claim_ref": "the budget is 40k", "rule_id": ref.RULE_CHANNEL},
    ])
    published = len(out["mentions"]["records"])
    assert out["stats"]["mentions_recorded"] == published, (
        "the arming number disagrees with the surface it describes: "
        f"{out['stats']['mentions_recorded']} recorded vs {published} published")
    assert published == 1, (
        f"two entries that converge after the cap were published twice: {published}")


def test_a_deduped_pair_keeps_the_entry_that_carries_the_ADDRESS():
    """⛔ `_dedupe_mentions` kept the FIRST entry, so a spanless duplicate listed
    first replaced a VERIFIED address listed second — the shipped record had no
    address while one was available, and the address is the record's whole point
    (review at `b791a3eb2`, P3)."""
    from tortoise.extractor_v2 import _clamped_mention, _dedupe_mentions

    account = "the budget is 40k"
    spanless = _clamped_mention({"surface": "it", "claim_ref": account})
    verified = _clamped_mention({"surface": "it", "claim_ref": account,
                                 "quote": "x it y", "span_source": "quote",
                                 "span_start": 2, "span_end": 4})
    assert "span_start" not in spanless and verified.get("span_start") == 2
    kept = _dedupe_mentions([spanless, verified])
    assert len(kept) == 1
    assert kept[0].get("span_start") == 2, (
        f"the spanless duplicate won the dedupe: {kept!r}")
    # …and the preference does not reorder anything else: the first entry still
    # wins when neither (or both) carry a pair.
    assert _dedupe_mentions([spanless, dict(spanless)]) == [spanless]

    # ⛔ THE SHAPE THAT WAS ACTUALLY BROKEN: `_mention_record` ALWAYS emits both
    # keys (`None` when it could not locate the surface) while `_clamped_mention`
    # POPS them, so a presence test was inert on the builder the defect was
    # observed on — the row above used only `_clamped_mention` products and was
    # green over a no-op rule (review at `9561959ed7`, P2). And the ADDRESS is
    # merged into the first record, not swapped with it: the cue's `rule_id` must
    # survive taking the duplicate's address.
    from tortoise.extractor_v2 import _mention_record

    cue = _mention_record(surface="it", claim={"content": account},
                          kind_guess=None, rule_id=ref.RULE_PRONOUN,
                          reason="cue derived", narrative="", quote="")
    assert cue.get("span_start") is None and "span_start" in cue
    addr = _mention_record(surface="it", claim={"content": account},
                           kind_guess=None, rule_id=ref.RULE_CHANNEL,
                           reason="carried", narrative="", quote="x it y",
                           span={"span_start": 2, "span_end": 4,
                                 "span_source": "quote"})
    assert addr["span_start"] == 2
    merged = _dedupe_mentions([cue, addr])
    assert len(merged) == 1
    assert merged[0]["span_start"] == 2 and merged[0]["span_source"] == "quote", (
        f"the address was dropped on a `_mention_record` pair: {merged!r}")
    assert merged[0]["rule_id"] == ref.RULE_PRONOUN and merged[0]["reason"] == "cue derived", (
        f"the cue's provenance was replaced by the duplicate's: {merged!r}")

    # ⛔ THE ADDRESS TRAVELS WITH ITS QUOTE. The pair was verified against the
    # DUPLICATE's quote, so filling the quote only "when absent" shipped
    # `span_source: "quote"` beside the survivor's different quote — offsets that
    # index bytes the record does not name, i.e. the forgery the clamp exists to
    # refuse, and no later clamp can catch it because every published surface
    # clamps BEFORE deduping (review at `2a1153519`, P2).
    survivor = _mention_record(surface="it", claim={"content": account},
                               kind_guess=None, rule_id=ref.RULE_PRONOUN,
                               reason="cue derived", narrative="",
                               # ⛔ no whole-word `it` here, or the survivor gets its
                               # OWN quote span and the merge never runs (first cut of
                               # this row used "nobody mentioned it here", measured green
                               # over the broken rule: `_mention_span` found `it` at 17)
                               quote="nobody mentioned that here")
    with_addr = _mention_record(surface="it", claim={"content": account},
                                kind_guess=None, rule_id=ref.RULE_CHANNEL,
                                reason="carried", narrative="", quote="he said it loudly",
                                span={"span_start": 8, "span_end": 10,
                                      "span_source": "quote"})
    assert with_addr["quote"][8:10] == "it"
    merged2 = _dedupe_mentions([survivor, with_addr])
    assert len(merged2) == 1
    rec = merged2[0]
    assert rec["span_source"] == "quote" and rec["quote"][rec["span_start"]:rec["span_end"]] == "it", (
        "the adopted quote-space address indexes the SURVIVOR's quote, not the "
        f"text it was verified against: {rec!r}")


def test_an_over_cap_claim_is_not_reported_as_CLAIMLESS():
    """⛔ `claims_by_ref` is keyed on the CUT identity (`_mention_ref`), but the
    channel loop compared the model's RAW `claim_ref` against it, so an entry whose
    claim is present over `_MAX_CONTENT` was counted `channel_refused_no_claim` and
    named in `channel_refused_surfaces` — an evidence surface whose own comment
    reads "a mention with no resolvable claim is not a record" — while the run held
    it, recorded it and attached it. The same window must be used on every lookup
    (review at `9561959ed7`, P3)."""
    long_claim = ("the budget is 40k and " * 90).strip()
    el = {"entities": [], "events": [],
          # ⛔ NO carrier names the surface: the claim link is the ONLY thing that can
          # hold it, so this row sees the claim window itself. With a carrier the
          # `_claims_naming` fallback masks a raw-vs-cut mismatch (measured: the
          # gate mutant stays green with one).
          "points": [{"content": long_claim, "pointKind": "statement"}],
          "operators": [],
          "mentions": [{"surface": "it", "claim_ref": long_claim,
                        "quote": "it", "rule_id": ref.RULE_PRONOUN}]}
    held = route_mentions(copy.deepcopy(el), "")
    assert held["mention_stats"]["held_surfaces"] == 1, (
        f"the mention-only shape was not held: {held['mention_stats']!r}")
    assert held["mention_stats"].get("channel_refused_no_claim", 0) == 0, (
        "a claim the run HELD and recorded was reported claimless: "
        f"{held['mention_stats']!r}")
    assert not held["mention_stats"].get("channel_refused_surfaces"), (
        f"a held surface was named refused: {held['mention_stats']!r}")
    payload, _ = _mint(copy.deepcopy(el))
    assert [m["surface"] for m in _mentions_of(payload)] == ["it"], (
        f"the record was lost: {_mentions_of(payload)!r}")


def test_a_deduped_pair_does_not_lend_its_address_to_a_DIFFERENT_SPELLING():
    """⛔ The dedupe key folds case and whitespace runs (`_norm`), but
    `_mention_span` is case-SENSITIVE — so a `quote`-space pair verified for `it`
    was adopted by a survivor named `It`, shipping offsets that index a VARIANT of
    the surface the record names. The published surfaces then demoted it to
    `"model"` (losing cue provenance) while the producer channel kept `"quote"`:
    the two-answers class, on the surface axis (review at `1bc5847b4`, P3)."""
    from tortoise.extractor_v2 import _dedupe_mentions, _mention_record

    account = "the server was down and it broke"
    upper = _mention_record(surface="It", claim={"content": account},
                            kind_guess=None, rule_id=ref.RULE_PRONOUN,
                            reason="cue derived", narrative="",
                            quote="nobody mentioned that here")
    lower = _mention_record(surface="it", claim={"content": account},
                            kind_guess=None, rule_id=ref.RULE_CHANNEL,
                            reason="carried", narrative="", quote="he said it loudly",
                            span={"span_start": 8, "span_end": 10,
                                  "span_source": "quote"})
    assert upper.get("span_start") is None and lower["span_start"] == 8
    merged = _dedupe_mentions([upper, lower])
    assert len(merged) == 1, f"case variants were not deduped: {merged!r}"
    rec = merged[0]
    assert rec["surface"] == "It", f"the survivor's spelling was replaced: {rec!r}"
    assert rec.get("span_start") is None, (
        "an address verified for `it` was adopted by a record named `It`: "
        f"{rec!r}")


def test_a_deduped_address_must_INDEX_the_text_the_record_carries():
    """⛔ ONE ADDRESS = LABEL + OFFSETS + THE TEXT THEY INDEX. The merge that made
    a verified address win the dedupe copied `span_start`/`span_end`/
    `span_source` from the winner while keeping the FIRST record's `quote` — unless
    that quote was EMPTY. So for two duplicates carrying DIFFERENT non-empty
    quotes, the record shipped a `"quote"`-labelled address into text it does not
    index: `quote[start:end]` reads bytes the surface never occupied, and
    `Mention._span_is_an_address` cannot see it (it checks completeness, order and
    range only). Measured on the fail-open path, where the clamp runs BEFORE the
    dedupe: `{'quote': 'the budget is 40k', 'span_start': 2, 'span_end': 4,
    'span_source': 'quote'}` → `quote[2:4] == 'e '` for surface `it` (review at
    `d59000f10`, P2 — the fabricated-address class #5007 and this PR's own
    `test_a_mention_only_response_is_verified_by_the_last_builder` exist to close).

    The invariant, asserted as a property so any variant is caught: a shipped
    `"quote"` address MUST index its own surface in its OWN quote. The positive
    half below proves the fix is not "stop merging": an address that DOES verify
    against the kept quote is still adopted."""
    from tortoise.extractor_v2 import _mentions_for_claim

    claim = "the budget is 40k"
    spanless = {"surface": "it", "claim_ref": claim, "quote": claim}
    carrying = {"surface": "it", "claim_ref": claim, "quote": "x it y",
                "span_source": "quote", "span_start": 2, "span_end": 4}

    for armed_entries in ([spanless, carrying], [carrying, spanless]):
        out = _mentions_for_claim({"mentions": [dict(e) for e in armed_entries]},
                                  claim, armed=True, narrative="")
        assert len(out) == 1, f"the pair was not deduped: {out!r}"
        rec = out[0]
        if rec.get("span_source") == "quote" and rec.get("span_start") is not None:
            q = str(rec.get("quote") or "")
            got = q[rec["span_start"]:rec["span_end"]]
            assert got == rec["surface"], (
                "a `quote`-labelled address does not index the text the record "
                f"carries: quote[{rec['span_start']}:{rec['span_end']}] == {got!r} "
                f"for surface {rec['surface']!r} — {rec!r}")

    # ⛔ AND THE ADDRESS MUST NAME THIS SURFACE, character for character. The dedupe
    # key is `_norm(surface)` (case-folded), so two entries for one claim can differ
    # in spelling; adopting the duplicate's pair then shipped offsets landing on the
    # right bytes of a DIFFERENT spelling than the record's own `surface` (review at
    # `ea790070d`, P3).
    mixed = _mentions_for_claim(
        {"mentions": [{"surface": "The Thing", "claim_ref": claim},
                      {"surface": "the thing", "claim_ref": claim,
                       "quote": "the thing is late", "span_source": "quote",
                       "span_start": 0, "span_end": 9}]},
        claim, armed=True, narrative="")
    assert len(mixed) == 1, mixed
    rec = mixed[0]
    if rec.get("span_start") is not None and rec.get("span_source") == "quote":
        q = str(rec.get("quote") or "")
        assert q[rec["span_start"]:rec["span_end"]] == rec["surface"], (
            "a `quote` address indexes a different SPELLING than the record's own "
            f"surface: {q[rec['span_start']:rec['span_end']]!r} vs "
            f"{rec['surface']!r} — {rec!r}")

    # POSITIVE CONTROL: the same duplicate, but the address verifies against the
    # KEPT record's quote — the address must still be adopted (the dedupe's job).
    ok = {"surface": "it", "claim_ref": claim, "quote": "x it y",
          "span_source": "quote", "span_start": 2, "span_end": 4}
    kept_ok = {"surface": "it", "claim_ref": claim, "quote": "x it y"}
    out = _mentions_for_claim({"mentions": [dict(kept_ok), dict(ok)]}, claim,
                              armed=True, narrative="")
    assert len(out) == 1
    assert out[0].get("span_start") == 2 and out[0].get("span_source") == "quote", (
        f"a VERIFIED address was dropped by the dedupe: {out[0]!r}")

# ── #11368: the two mention builders must be ONE rule, not two agreeing copies ──
#
# `_mention_record` (the cue's channel) and `_clamped_mention` (the last door) each
# carried their own copy of the declared-space verification AND their own spelling of
# the cap constants (`[:200]`, `_MAX_QUOTE`, `_MENTION_CAPS["quote"]`; `[:60]` beside
# `_MENTION_CAPS["kind_guess"]`). The copies agreed only while the spellings happened
# to agree, so changing one left the two builders publishing different quotes and
# different `span_source` labels for the SAME entry with a GREEN suite. The failure
# mode is a false PASS on provenance: a `span_source` asserting a verification the
# sibling builder would not perform (#11368). ⛔ The caps were EQUAL at `ad8022205` and
# again at `23d227c53` (`_mention_record`'s own `[:200]`/`_MAX_QUOTE`/`[:60]` happened
# to match `_MENTION_CAPS` 200/200/60), so nothing reddened — that is LUCK, not a pin,
# and the cap row below is what separates the two: with `_MENTION_CAPS` patched to
# `quote=100, surface=100, kind_guess=10` on `23d227c53`'s tree, the record builder
# still stored 200/200/60 while the clamp stored 100/100/10 — one failed row, no other
# test in the file moving.


def _build_pair(*, surface, quote, kind_guess=None):
    """One input, both builders, unrestricted fields (for the cap rows)."""
    from tortoise.extractor_v2 import _clamped_mention, _mention_record

    claim = {"content": "c", "quote": "c"}
    rec = _mention_record(surface=surface, claim=claim, kind_guess=kind_guess,
                          rule_id="coreference", reason="r", narrative="",
                          quote=quote, span=None)
    cl = _clamped_mention({"surface": surface, "quote": quote,
                           "kind_guess": kind_guess, "rule_id": "coreference",
                           "reason": "r"}, narrative="")
    return rec, cl


def _span_fields(d):
    """The address a record carries — the fields a consumer resolves.

    ``quote`` is compared as a VALUE, not as a key: `_clamped_mention` skips empty
    fields (`if v is None or v == ""`) while `_mention_record` always emits the key,
    and `commit_schema.Mention.quote` defaults to ``""`` — so an absent key and
    ``""`` are the same contract value. The verdict (`span_source` + the pair) is
    what must never differ.
    """
    assert d is not None
    return (d.get("quote") or "", d.get("span_start"), d.get("span_end"),
            d.get("span_source"))


def _declared_both(surface, quote, *, span, narrative=""):
    """The SAME DECLARED span through both builders, compared field by field.

    Only a DECLARED span is compared: without one the two builders do different jobs
    by design — `_mention_record` SEARCHES the text for an address while
    `_clamped_mention` re-derives a claim the model made — so parity is owed on the
    declaration verdict, which is the rule #11368 is about.
    """
    from tortoise.extractor_v2 import _clamped_mention, _mention_record

    claim = {"content": "the problem is elsewhere",
             "quote": "the problem is elsewhere"}
    rec = _mention_record(surface=surface, claim=claim, kind_guess=None,
                          rule_id="coreference", reason="cue reason",
                          narrative=narrative, quote=quote, span=span)
    m = {"surface": surface, "claim_ref": claim["content"], "quote": quote,
         "kind_guess": None, "rule_id": "coreference", "reason": "cue reason"}
    m.update({k: v for k, v in span.items() if v is not None})
    return _span_fields(rec), _span_fields(_clamped_mention(m, narrative=narrative))


@pytest.mark.parametrize("surface,quote,span,narrative,expect_source", [
    # a declaration that VERIFIES — both must honour it
    ("the problem", "the problem is elsewhere",
     {"span_start": 0, "span_end": 11, "span_source": "quote"}, "", "quote"),
    # a declaration that does NOT verify — both must demote to "model"
    ("the problem", "the problem is elsewhere",
     {"span_start": 3, "span_end": 9, "span_source": "quote"}, "", "model"),
    # an absent QUOTE is a definite MISMATCH, never an amnesty
    ("the problem", "",
     {"span_start": 0, "span_end": 11, "span_source": "quote"}, "", "model"),
    # an absent NARRATIVE is an UNKNOWABLE haystack — the declaration STANDS
    ("the problem", "the problem is elsewhere",
     {"span_start": 0, "span_end": 11, "span_source": "narrative"}, "",
     "narrative"),
    # …and a supplied narrative that does not verify demotes
    ("the problem", "the problem is elsewhere",
     {"span_start": 0, "span_end": 11, "span_source": "narrative"},
     "nothing about it here", "model"),
    # a padded declaration is the SAME declaration
    ("the problem", "the problem is elsewhere",
     {"span_start": 0, "span_end": 11, "span_source": " quote "}, "", "quote"),
    # a haystack longer than the cap, with the address inside it
    ("the problem", "the problem " + ("x" * 300),
     {"span_start": 0, "span_end": 11, "span_source": "quote"}, "", "quote"),
    # the amnesty must not leak: no narrative AND wrong offsets is still unknown
    ("the problem", "the problem is elsewhere",
     {"span_start": 2, "span_end": 13, "span_source": "narrative"}, "",
     "narrative"),
    # an AMBIGUOUS surface is not an address: the locator refuses it, so the
    # declaration demotes. ⛔ This is the row that pins the VERDICT rather than
    # parity: a copy that located the span with `find`/substring arithmetic — in
    # ONE builder or in BOTH — returns the first occurrence, keeps "quote", and
    # reds here, while every single-occurrence row above would still pass. Parity
    # alone cannot see that: two identical `find` copies agree with each other
    # (#11368 R1).
    ("it", "it was there and it was here",
     {"span_start": 0, "span_end": 2, "span_source": "quote"}, "", "model"),
])
def test_the_two_mention_builders_give_the_SAME_verdict_on_a_declared_span(
        surface, quote, span, narrative, expect_source):
    rec, cl = _declared_both(surface, quote, span=span, narrative=narrative)
    assert rec == cl, (
        f"the two mention builders disagree on ONE declared span — record={rec} "
        f"vs clamp={cl} for surface={surface!r} quote={quote[:40]!r} span={span} "
        f"narrative={narrative[:40]!r}. A `span_source` may only assert a "
        "verification BOTH builders would perform (#11368).")
    # ⛔ AND the agreed verdict must be the RIGHT one. Parity is satisfied by two
    # identical copies of a wrong rule, so each row names the verdict it wants:
    # this is the half that reds a symmetric re-split.
    assert rec[3] == expect_source, (
        f"both builders agreed on span_source={rec[3]!r} where the rule gives "
        f"{expect_source!r} for surface={surface!r} quote={quote[:40]!r} "
        f"span={span} narrative={narrative[:40]!r} — agreement between two copies "
        "of one locator is not evidence that the locator is right (#11368 R1).")


def test_both_builders_read_every_cap_from_ONE_source(monkeypatch):
    """⛔ The drift pin: a cap is only a cap if BOTH builders honour it.

    Moving one cap is exactly what #11368 measured — and what a builder-local
    literal makes invisible: the record builder carried `[:200]` and `[:60]` beside
    `_MAX_QUOTE`, so `_MENTION_CAPS` was a cap the OTHER builder obeyed and this one
    ignored.

    ⛔ AND the ceilings are the SCHEMA's, not the Point's. `Mention.quote`'s 200 is
    an independent literal, so a cap that follows `_MAX_QUOTE` can be dragged past
    it by a legitimate raise of the POINT's cap — and a Quote over the schema's cap
    422s the whole payload. So the two relations pinned here are the ones the
    payload and the offsets actually need (review at `d02ed2ccc`, P3; corrected
    after review — see below): a mention never stores MORE of the quote than the
    point does (`<= _MAX_QUOTE`), and no cap exceeds the schema's ceiling
    (`<= Mention.<field>.max_length`).

    ⛔ NOT equality with `_MAX_QUOTE`. It held on the day it was written and the
    direction it fires in is the wrong one: `test_point_quote_cap_matches_its_mirrors`
    tells a lane raising the POINT's quote cap to "raise both" (`_MAX_QUOTE` and
    `Point.quote.max_length`) — a change that has nothing to do with mentions — and
    an equality pin reds that legitimate, instructed change, while the remediation
    its own message gives ("move them together") trips the schema ceiling in the
    very next assertion. Two caps that measure different things must not be pinned
    equal; the `<=` in the mention-never-outgrows-the-point direction is the
    invariant the offset argument actually rests on.
    """
    from tortoise.commit_schema import Mention
    from tortoise.extractor_v2 import _MAX_QUOTE, _MENTION_CAPS

    # A mention's quote cut must not be LARGER than the point's, or the same
    # offsets index two different strings. NOT equality: raising the point's cap is
    # a legitimate change of an unrelated record (`Point.quote`), and the mention's
    # ceiling is the schema's, so it must not have to move with it.
    assert _MENTION_CAPS["quote"] <= _MAX_QUOTE, (
        "`_MENTION_CAPS['quote']` is larger than `_MAX_QUOTE`, the cut a point's "
        "quote is stored with — the mention would then hold more of the quote than "
        "the point it is anchored to, and the same offsets index two different "
        "strings; lower the mention's cap or raise `_MAX_QUOTE` (with "
        "`Point.quote.max_length`, per "
        "`test_point_quote_cap_matches_its_mirrors`). ⛔ Do NOT widen "
        "`commit_schema.Mention.quote.max_length` to make them equal — the "
        "mention's ceiling is the schema's own, not the point's")

    # ⛔ The real ceiling: the schema owns it, and exceeding it 422s the payload.
    for name in ("surface", "quote", "kind_guess"):
        field = Mention.model_fields[name]
        ceiling = [m.max_length for m in field.metadata if hasattr(m, "max_length")]
        assert ceiling, f"`Mention.{name}` carries no max_length to mirror"
        assert _MENTION_CAPS[name] <= ceiling[0], (
            f"`_MENTION_CAPS['{name}']` = {_MENTION_CAPS[name]} exceeds "
            f"`commit_schema.Mention.{name}.max_length` = {ceiling[0]} — a record "
            "built at that cap is REJECTED by the schema and takes the whole "
            "payload's validation down with it")

    cases = [
        ("quote", 100, {"surface": "the problem",
                        "quote": "the problem " + ("x" * 300)}),
        ("surface", 100, {"surface": "s" * 300, "quote": "q"}),
        ("kind_guess", 10, {"surface": "s", "quote": "q",
                            "kind_guess": "k" * 100}),
    ]
    for name, value, kwargs in cases:
        with monkeypatch.context() as patch:
            patch.setitem(_MENTION_CAPS, name, value)
            rec, cl = _build_pair(**kwargs)
        assert rec[name] is not None and cl[name] is not None, (name, rec, cl)
        assert len(rec[name]) == len(cl[name]) == value, (
            f"`{name}` cap {value} is not honoured by BOTH builders: "
            f"record={len(rec[name])} clamp={len(cl[name])} — one of them is reading "
            "its own literal instead of `_MENTION_CAPS` (#11368)")
