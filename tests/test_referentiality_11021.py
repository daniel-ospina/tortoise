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
    skipped the `_MAX_QUOTE` clamp on the way (review cycle 5, P2)."""
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


# ── the record's boundary: it is payload-only, and the tree says so ───────

def test_the_mention_record_is_payload_only_and_says_so():
    """⛔ THE DECLARED DEFERRAL, PINNED (review at `591906397`, Issue 1). DM1
    names the claim Point as the record's home and `Point.mentions` exists, but
    BOTH declared write doors drop it — `hosted_api.create_point` enumerates its
    props and the SDK capture whitelist is `#2949`'s response-parity surface. So
    an armed run's `mentions_recorded` counts records the write discards, and a
    reader of the graph sees the claim and no mention on it.

    Persisting it is `#11239`, deliberately not this slice. This row exists so the
    boundary cannot become a silent claim: adding `mentions` to the whitelist (or
    removing the disclosure) REDs here and forces the docstring, the PR body and
    `#11239` to be updated together."""
    from tortoise.commit_schema import Mention
    from tortoise.sdk import _CAPTURE_PASSTHROUGH_PROPS

    # the record EXISTS at the payload — "payload-only" is not "nothing"
    payload, _ = _mint(_embed_list("it"))
    assert [m["surface"] for m in _mentions_of(payload)] == ["it"]

    # …and the write door does not take it, which is the whole deferral
    assert "mentions" not in _CAPTURE_PASSTHROUGH_PROPS, (
        "the capture whitelist now carries mentions: the payload-only deferral "
        "is over — update Mention's docstring and #11239, and turn this row into "
        "a persistence assertion")
    assert "11239" in (Mention.__doc__ or ""), (
        "Mention's docstring no longer names the slice that owns persistence, so "
        "the model reads as graph state")
