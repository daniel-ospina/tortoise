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
    assert ctx["held"]["mention_stats"]["held"] == 1

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
    el["operators"] = [{"src": "it", "dst": "the admin-merge rail",
                        "op_type": "IMPL"}]
    payload, ctx = _mint(el)
    assert ctx["held"]["mention_stats"]["operators_dropped"] == 1
    assert payload["operators"] == []
    assert "it" not in {p["content"] for p in payload["points"]}


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
    assert stats["held"] == 0
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


def test_the_domain_nouns_a_corpus_measurement_excluded_are_excluded():
    """The first GENERIC_HEADS draft carried this repo's own node types, and
    `the point` alone occurs 893 times in `docs/` + `tortoise/` (measured at
    277a559b1). Each row below is a referent the cue must not hold (#11021
    review P1)."""
    for surface in ("the point", "the points", "the question", "the number",
                    "the step", "the steps", "the result", "the case", "the time",
                    "the part"):
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
    assert [n for n in held if n in definite] == []
