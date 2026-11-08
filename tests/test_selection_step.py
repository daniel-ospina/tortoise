"""#11002 — the S2.2 RE-NARRATE: the one bounded retry, its trigger, its union.

THE DEFECT THIS PINS
--------------------
The eight-step design has always named an outcome-triggered re-narrate as the
remedy for a poor batch (`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.4:
*"re-narrate ONCE with the pack's `memory_granularity` emphasised, then
re-extract"* — **a retry, not a loop**). Nothing implemented it, and the two
signals that would drive it had no consumer anywhere in the tree:
`vet_gate.check_batch`'s `coverage_suspect` and the batch-level
`vet_gate.RENARRATE` outcome token. So the coverage check could **detect** a
batch that lost everything and **act on nothing** — which is why `run_s4`
(today's gap-filler) could not be replaced without deleting gap-filling.

WHAT THIS PINS
--------------
1. **The ADD proof** (`test_a_zero_candidate_arm_re_narrates_exactly_once`) — a
   candidate the coverage check found missing is emitted by the new path, and it
   reaches the payload. This is the issue's load-bearing claim: the successor is
   a stage that can ADD.
2. **The bound** — at most ONE retry per run, even when the second arm is just
   as poor. §4.4's "a retry, not a loop" is an invariant, not a preference: an
   unbounded loop on the model path can oscillate and doubles capture spend with
   every iteration.
3. **The retry is a different QUESTION, not a second ticket for the same one**
   (`test_the_second_narrative_prompt_differs_and_names_the_gap`) — S1 renders
   from the transcript plus the granularity slot at temperature 0.0, so an
   identical prompt cannot return a better narrative.
4. **E4's merges-not-replaces property survives into the successor**
   (`test_the_union_preserves_the_e4_merge_property`) — the owner's ruling on
   #11002 (2026-11-08): *"the retry must union both attempts with the same
   identity-key collision rules the existing merge uses — that is what makes it
   a successor rather than a replacement."*
5. **§4.4's tie-break, on the narrative** — a tie keeps the FIRST (the metric
   and the comparison are fixed before the retry fires; a rule that took the
   second on a tie would let a coin flip lower the accepted keep-rate).
6. **The signals are consumed** — an arbiter's batch `RENARRATE` fires the retry
   (`test_an_arbiter_batch_renarrate_is_consumed`); the keep-rate arm is
   **DISARMED** unless a threshold is set by hand (§4.2: thresholds are set on
   tens of real sessions with the owner correcting the rule — never chosen in
   advance).
7. **The record** — both arms' numbers ride `stats["renarrate"]`, so an action
   never happens without a record (I2).
8. **The failure path does not spend the retry** — an S1 that failed (empty
   narrative) yields no batch to be poor about, and a **failed S2 call** is a
   transport-class failure, not a poor batch: its empty list is byte-identical
   to a genuinely empty one, so the trigger must be told which it was. Both
   arms' retries and escalation already happened INSIDE their own stage; the
   one content-level retry is never spent on them.

Every case is hermetic: a scripted model (no network, no SDK, no DB, no graph),
the same shape `tests/test_5095_s3_identity_policy.py::_FakeModel` uses.
"""

from __future__ import annotations

import json

import pytest

from tortoise import extractor_v2
from tortoise.extractor_v2 import extract_session_v2, run_s1

_EMPTY_EMBED = json.dumps({
    "entities": [], "events": [], "operators": [], "points": [],
    "chain_notes": [], "link_before_create": [],
})

CONVERSATION = [
    {"role": "user",
     "content": "Alice decided the budget is 40k. Bob will ship on Friday."},
    {"role": "assistant", "content": "Noted — I will send the revised plan."},
]


def _embed(name: str) -> str:
    return json.dumps({
        "entities": [{"name": name, "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [],
        "operators": [],
        "points": [{"content": f"the {name} rollout moves to Q3",
                    "pointKind": "statement"}],
        "chain_notes": [], "link_before_create": [],
    })


class _Scripted:
    """A model whose S1/S2 answers are scripted per call, recorded in order."""

    last_finish_reason = None

    def __init__(self, s1_answers, s2_answers):
        self.s1_answers = list(s1_answers)
        self.s2_answers = list(s2_answers)
        self.s1_prompts: list[str] = []
        self.s2_prompts: list[str] = []
        self.other_stages: list[str] = []

    def complete(self, *, system, user, max_tokens=None):
        self.last_finish_reason = "stop"
        if "STORY SUMMARIZER" in system:
            self.s1_prompts.append(system)
            idx = min(len(self.s1_prompts), len(self.s1_answers)) - 1
            return self.s1_answers[idx]
        if "GAP REVIEWER" in system:
            # S4 — the live gap-filler, untouched by #11002. It answers "no
            # gaps" so this file measures the E4 arm union, not S4's merge.
            self.other_stages.append("s4")
            return _EMPTY_EMBED
        self.s2_prompts.append(system)
        idx = min(len(self.s2_prompts), len(self.s2_answers)) - 1
        return self.s2_answers[idx]


def _run(monkeypatch, s1_answers, s2_answers, *, arbiter=None,
         keep_rate: str | None = None):
    if keep_rate is None:
        monkeypatch.delenv("TORTOISE_RENARRATE_KEEP_RATE", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_RENARRATE_KEEP_RATE", keep_rate)
    model = _Scripted(s1_answers, s2_answers)
    result = extract_session_v2(model, list(CONVERSATION), sdk=None,
                                session_id="11002-test",
                                session_date="2026-11-08",
                                vet_arbiter=arbiter)
    return model, result


def _entity_names(result) -> set[str]:
    return {e.get("name") for e in (result.get("embed_list") or {}).get(
        "entities") or [] if isinstance(e, dict)}


# ── 1. the ADD proof, and the bound ───────────────────────────────────────

def test_a_zero_candidate_arm_re_narrates_exactly_once(monkeypatch):
    """Prose in, NOTHING out → one re-narrate → the missing candidate is emitted.

    This is the issue's load-bearing claim: the retried arm **adds** a candidate
    the coverage check found missing, and it reaches the payload. Before #11002
    the run committed nothing and reported `coverage_suspect` into a stats key
    nobody read.
    """
    model, result = _run(
        monkeypatch,
        ["Alice set a budget. Bob ships Friday."],
        [_EMPTY_EMBED, _embed("Northwind")],
    )
    assert len(model.s1_prompts) == 2, "exactly one re-narrate"
    assert len(model.s2_prompts) == 2, "one re-extraction, not a loop"
    assert _entity_names(result) == {"Northwind"}, (
        "the candidate the coverage check found missing must reach the list"
    )
    payload_entities = {e.get("name")
                        for e in (result.get("payload") or {}).get("entities") or []}
    assert "Northwind" in payload_entities, (
        "the ADD proof is about what the run COMMITS, not only about the list"
    )
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True
    assert renarrate["trigger"] == "coverage_suspect"
    assert renarrate["first"]["candidates"] == 0
    assert renarrate["first"]["keep_rate"] is None
    assert renarrate["second"]["candidates"] == 2
    # ax-principles: the action names what it did, in the run's own warnings.
    assert any("re-narrate fired once" in w for w in result.get("warnings") or [])


def test_the_retry_fires_at_most_once_per_run(monkeypatch):
    """A second poor verdict is ACCEPTED, never retried — §4.4's hard bound."""
    model, result = _run(
        monkeypatch,
        ["Alice set a budget.", "Alice set a budget, again."],
        [_EMPTY_EMBED, _EMPTY_EMBED],
    )
    assert len(model.s1_prompts) == 2, (
        "an unbounded retry loop is the failure §4.4 bounds to one; a third S1 "
        "call means the second poor verdict triggered another round"
    )
    assert len(model.s2_prompts) == 2
    assert result["stats"]["renarrate"]["fired"] is True
    # The poor outcome is recorded honestly: both arms are still in the record.
    assert result["stats"]["renarrate"]["second"]["candidates"] == 0
    assert _entity_names(result) == set()


def test_an_s1_failure_does_not_spend_the_retry(monkeypatch):
    """No narrative is a FAILED S1, not a poor narrative — the retry is not burnt.

    §4.4's S1 policy owns the failure path; a retry spent on it would pay twice
    for the same failure and would still commit nothing.
    """
    model, result = _run(monkeypatch, [""], [_EMPTY_EMBED])
    assert model.s1_prompts, "S1 must have been attempted"
    assert result["stats"]["renarrate"] == {"fired": False}
    assert len(model.s2_prompts) == 0, "no narrative → no S2 at all"


def test_an_s2_failure_does_not_spend_the_retry(monkeypatch):
    """An S2 that RAISED is a transport failure, not a poor batch.

    `check_batch` reads `(embed_list, story)`, and a call that raised leaves
    `embed_list` empty without asserting anything about the narrative's yield —
    byte-identical to "prose in, ZERO candidates out", which is precisely the
    signal armed by construction. Without the distinction the SAME failure is
    paid for twice (S1+S2 again) and still commits nothing, on every session for
    the duration of the outage, while the narrative may have been rich and only
    the instrument broken.

    Non-vacuity: on the pre-fix code the trigger reads `coverage_suspect` off
    the empty list, so `s1_prompts` is 2 and `renarrate` is `fired: True`.
    """
    monkeypatch.delenv("TORTOISE_RENARRATE_KEEP_RATE", raising=False)
    monkeypatch.setattr(extractor_v2.time, "sleep", lambda _s: None)

    class _S2Down(_Scripted):
        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" in system:
                return super().complete(system=system, user=user,
                                        max_tokens=max_tokens)
            self.other_stages.append("s2")
            raise RuntimeError("provider exploded")

    model = _S2Down(["Alice set a budget. Bob ships Friday."], [_EMPTY_EMBED])
    result = extract_session_v2(model, list(CONVERSATION), sdk=None,
                                session_id="11002-test",
                                session_date="2026-11-08")
    assert model.other_stages.count("s2") >= 1, "S2 must have been attempted"
    assert len(model.s1_prompts) == 1, (
        "a failed S2 must not re-narrate: the batch it produced carries no fact "
        "about the narrative's yield, and the retry would pay the same outage twice"
    )
    assert result["stats"]["renarrate"] == {"fired": False}
    assert any("S2 failed" in e for e in result["errors"]), (
        "the failure is still recorded loudly — refusing the retry is not hiding it"
    )
    assert result["embed_list"] == {}, "nothing invented on the failure path"


# ── 2. the retry is a different question ──────────────────────────────────

def test_the_second_narrative_prompt_differs_and_names_the_gap(monkeypatch):
    """Arm B's S1 prompt carries the emphasis block; arm A's does not.

    ⛔ This is the test that would otherwise pass a broken implementation: an
    identical prompt at temperature 0.0 is a pure cost double, so "the retry
    ran" is not the property — "the retry asked something ELSE" is.
    """
    model, _ = _run(monkeypatch, ["Alice set a budget."],
                    [_EMPTY_EMBED, _embed("Northwind")])
    first, second = model.s1_prompts
    assert first != second
    block = extractor_v2._renarrate_emphasis("coverage_suspect")
    assert block in second, "arm B must carry the emphasis block verbatim"
    assert block not in first, "arm A must not — its prompt is the baseline"
    assert "coverage_suspect" in second, (
        "the block names the signal that fired, or the retry is unexplained"
    )
    assert "RE-NARRATE" in second


def test_the_union_pass_may_add_the_mentions_union_key(monkeypatch):
    """The arm keys are a FLOOR, not the whole set: the post-S4 pass adds `union`.

    `route_mentions` runs AGAIN on the union after S4 and folds its counters into
    `mention_s2_stats["union"]`, so a set-equality assertion on `{first, second}`
    passes only because the fixture holds no mention — it would fail on correct
    code (review round 3, P2). This pins the floor and the documented extra key.
    """
    monkeypatch.setenv("TORTOISE_REFERENTIALITY", "1")
    s2 = json.dumps({
        "entities": [{"name": "Northwind", "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [], "operators": [],
        "points": [{"content": "the Northwind rollout moves to Q3",
                    "pointKind": "statement",
                    "mentions": [{"surface": "it",
                                  "quote": "it ships in Q3"}]}],
        "chain_notes": [], "link_before_create": [],
    })
    _, result = _run(monkeypatch, ["Alice set a budget."], [s2])
    stats = (result.get("mentions") or {}).get("stats") or {}
    assert {"first", "second"} <= set(stats), (
        "the arm keys are present regardless of what the union pass adds"
    )
    assert set(stats) <= {"first", "second", "union"}, (
        f"no key beyond the documented three — got {sorted(stats)!r}"
    )
    assert "union" in stats, (
        "non-vacuity: this fixture DOES hold a mention, so the post-S4 pass ran "
        "and the third key is the one the set-equality pin would have broken on"
    )


def test_a_failed_second_arm_does_not_empty_the_vet_surface(monkeypatch):
    """Arm B's S2 raising must not publish `vet.s2 = {}` about a pass that ran.

    The pick took the SECOND arm's `vet_stats` unconditionally, so a retry whose
    S2 raised published an EMPTY `result["vet"]["s2"]` while the shipped list was
    the first arm's and arm A's VET pass had in fact run and decided. That is
    the false "the gate did not run" the vet surface exists to avoid (review
    round 3, P2).

    Non-vacuity: on the pre-fix code `result["vet"]["s2"]` is `{}` here.
    """
    monkeypatch.setattr(extractor_v2.time, "sleep", lambda _s: None)
    triggers = {"n": 0}

    class _SecondArmS2Down(_Scripted):
        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" in system:
                return super().complete(system=system, user=user,
                                        max_tokens=max_tokens)
            if "GAP REVIEWER" in system:
                return super().complete(system=system, user=user,
                                        max_tokens=max_tokens)
            triggers["n"] += 1
            if triggers["n"] > 1:
                raise RuntimeError("provider exploded on the retried arm")
            return super().complete(system=system, user=user,
                                    max_tokens=max_tokens)

    model = _SecondArmS2Down(
        ["Alice and Bob met.", "Alice met Bob about the budget."],
        [_embed("FirstOnly"), _embed("SecondOnly")],
    )
    result = extract_session_v2(model, list(CONVERSATION), sdk=None,
                                session_id="11002-test",
                                session_date="2026-11-08",
                                vet_arbiter=_renarrate_arbiter)
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True, "the arbiter fired the retry"
    assert renarrate["trigger"] == "arbiter_renarrate"
    assert renarrate["second"]["candidates"] == 0, (
        "the retried arm produced nothing — its S2 raised"
    )
    assert result["vet"]["s2"] != {}, (
        "the vet surface must not claim the gate did not run: arm A's pass ran "
        "and decided, and the union IS arm A's list here"
    )
    assert _entity_names(result) >= {"FirstOnly"}, (
        "arm A's items ship, which is why its VET record is the accurate one"
    )
    assert any("S2 failed" in e for e in result["errors"]), (
        "the failed retried arm is still recorded loudly"
    )


def test_each_trigger_states_its_own_premise():
    """The emphasis must not tell the model a false premise about its input.

    Only `coverage_suspect` means the first story *yielded nothing*. The other
    two triggers fire on a QUALITY judgement about a batch that DID produce
    candidates, so reusing the "yielded nothing" sentence there hands the model
    a false statement about its own input (review at `86ed2e9b7`, P2). The three
    premises must therefore be DISTINCT, and only the coverage arm may claim an
    empty extraction.
    """
    coverage = extractor_v2._renarrate_emphasis("coverage_suspect")
    arbiter = extractor_v2._renarrate_emphasis("arbiter_renarrate")
    keep_rate = extractor_v2._renarrate_emphasis("keep_rate")
    assert len({coverage, arbiter, keep_rate}) == 3, (
        "one premise per trigger — a shared sentence is the false-premise defect"
    )
    assert "yielded nothing" in coverage
    assert "yielded nothing" not in arbiter
    assert "yielded nothing" not in keep_rate
    # The machine-readable trigger rides its own labelled line, never the prose.
    for trigger, block in (("coverage_suspect", coverage),
                           ("arbiter_renarrate", arbiter),
                           ("keep_rate", keep_rate)):
        assert f"Re-narrate trigger: {trigger}." in block
    # An unknown reason gets the NEUTRAL sentence, never a claim about the input.
    unknown = extractor_v2._renarrate_emphasis("something_new")
    assert "yielded nothing" not in unknown
    assert "Re-narrate trigger: something_new." in unknown


def test_run_s1_renders_byte_identically_without_emphasis(monkeypatch):
    """``emphasis=None`` must not move a single byte of S1's prompt.

    The retry is the only caller that passes it, so the render with no emphasis
    has to be the PRE-#11002 prompt exactly — otherwise every S1 call in the
    fleet moves the moment the seam exists.

    ⛔ The baseline is COMPUTED FROM THE TEMPLATE, not compared to a second
    ``emphasis=None`` call. Comparing two identically-parameterised calls is a
    vacuous guard: the two paths coincide by construction, so any render
    mutation (an unconditional slot rewrite, ``if emphasis is not None:`` +
    ``"\n\n"``, ``emphasis or "X"``) leaves both strings equal and the
    assertion green — it pins nothing about the pre-PR prompt, which is the
    property the name claims (agent-infra#3442 rule 3).
    """
    seen: list[str] = []

    class _M:
        last_finish_reason = "stop"

        def complete(self, *, system, user, max_tokens=None):
            seen.append(system)
            return "a story"

    run_s1(_M(), "CONVERSATION BODY", session_date="2026-11-08")
    run_s1(_M(), "CONVERSATION BODY", session_date="2026-11-08", emphasis=None)
    assert len(seen) == 2
    # The pre-#11002 render, reconstructed from the template itself: this is the
    # string every S1 call site produced before the seam existed.
    baseline = (extractor_v2.S1_TMPL
                .replace("{memory_granularity}",
                         extractor_v2._granularity_text(None))
                .replace("{date_anchor}",
                         extractor_v2._date_anchor("2026-11-08")))
    assert "{memory_granularity}" not in baseline
    assert extractor_v2._renarrate_emphasis("coverage_suspect") not in baseline
    assert seen[0] == baseline, "an absent emphasis must not move the render"
    assert seen[1] == baseline, "explicit None is the same request as absent"

    # …and the armed arm differs from that baseline by EXACTLY the appended
    # block: the emphasis rides the granularity slot, it does not rewrite the
    # template around it.
    seen.clear()
    run_s1(_M(), "CONVERSATION BODY", session_date="2026-11-08",
           emphasis="EMPHASIS BLOCK")
    armed = (extractor_v2.S1_TMPL
             .replace("{memory_granularity}",
                      extractor_v2._granularity_text(None)
                      + "\n\nEMPHASIS BLOCK")
             .replace("{date_anchor}",
                      extractor_v2._date_anchor("2026-11-08")))
    assert seen[0] == armed
    assert seen[0] != baseline
    assert seen[0].replace("\n\nEMPHASIS BLOCK", "", 1) == baseline, (
        "the armed prompt must be the baseline plus one appended block — a "
        "mutation anywhere else in the render would leave a remainder here"
    )

    # The falsy-but-present spelling is a no-op too (`if emphasis:`, not
    # `if emphasis is not None:`) — the retry never passes "", and a render that
    # treated it as armed would emit a stray block for a caller that did not ask.
    seen.clear()
    run_s1(_M(), "CONVERSATION BODY", session_date="2026-11-08", emphasis="")
    assert seen[0] == baseline, "an empty emphasis is not an emphasis"


# ── 3. E4's property survives; the narrative follows §4.4's tie-break ────

def _renarrate_arbiter(items, narrative):
    """An arbiter that says the batch is poor — the token's only producer."""
    return {"batch": {"outcome": "RENARRATE", "reason": "poor batch"},
            "verdicts": []}


def test_the_union_preserves_the_e4_merge_property(monkeypatch):
    """Both arms' items ship: merges-not-replaces, the owner's #11002 ruling.

    A winner-take-all pick would silently drop the losing arm's items — the
    exact shape E4 exists to forbid, and the reason a lane may not remove
    `run_s4` before its successor carries this property.
    """
    model, result = _run(
        monkeypatch,
        ["Alice and Bob met.", "Alice met Bob about the budget."],
        [_embed("FirstOnly"), _embed("SecondOnly")],
        arbiter=_renarrate_arbiter,
    )
    assert len(model.s1_prompts) == 2
    renarrate = result["stats"]["renarrate"]
    assert renarrate["trigger"] == "arbiter_renarrate"
    assert renarrate["items"] == "union"
    assert _entity_names(result) == {"FirstOnly", "SecondOnly"}, (
        "the union must keep the FIRST arm's item as well — a collision-free "
        "second arm cannot be allowed to displace the first"
    )
    assert renarrate["merged"]["kept_from_first"] == 2
    assert renarrate["merged"]["added_by_second"] == 2


def test_a_tie_keeps_the_first_narrative(monkeypatch):
    """Equal keep-rates → the FIRST narrative is the one the run carries.

    §4.4: *"keep the FIRST unless the second is STRICTLY better"*; the metric is
    fixed before the retry fires, and a tie is not an improvement.
    """
    _, result = _run(
        monkeypatch,
        ["Alice and Bob met.", "Alice met Bob about the budget."],
        [_embed("FirstOnly"), _embed("SecondOnly")],
        arbiter=_renarrate_arbiter,
    )
    renarrate = result["stats"]["renarrate"]
    assert renarrate["first"]["keep_rate"] == renarrate["second"]["keep_rate"]
    assert renarrate["narrative"] == "first"
    assert result["story_arc"] == "Alice and Bob met.", (
        "the tie-break decides the ONE narrative the run records"
    )


# ── 4. the trigger's arms ─────────────────────────────────────────────────

def _discarding_arbiter(items, narrative):
    """Discard exactly one candidate — a measurable keep-rate below 1.0."""
    victim = items[0]["id"]
    return {"verdicts": [{"id": victim, "outcome": "DISCARD",
                          "rule_id": "test.keep_rate", "reason": "poor"}]}


def test_the_keep_rate_arm_is_disarmed_by_default(monkeypatch):
    """Unset / garbage threshold → the keep-rate arm never fires; armed → once.

    §4.2: the threshold is set by hand on tens of real sessions, so this lane
    ships the SEAM and refuses to chose a number. The pair is the discriminator:
    the SAME run fires only when the threshold is configured.
    """
    two = json.dumps({
        "entities": [{"name": "Alpha", "kind": "core:org",
                      "lifecycle": "created"},
                     {"name": "Beta", "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [], "operators": [], "points": [],
        "chain_notes": [], "link_before_create": [],
    })

    # Disarmed (unset): a 0.5 keep-rate is not a trigger.
    model, result = _run(monkeypatch, ["Alice and Bob met."], [two],
                         arbiter=_discarding_arbiter)
    assert result["stats"]["renarrate"] == {"fired": False}
    assert len(model.s1_prompts) == 1

    # Disarmed (garbage): a value that is not a rate cannot arm it either.
    model, result = _run(monkeypatch, ["Alice and Bob met."], [two],
                         arbiter=_discarding_arbiter, keep_rate="not-a-rate")
    assert result["stats"]["renarrate"] == {"fired": False}
    assert len(model.s1_prompts) == 1

    # Armed by hand: the below-threshold rate fires the retry exactly once.
    model, result = _run(monkeypatch, ["Alice and Bob met."], [two, two],
                         arbiter=_discarding_arbiter, keep_rate="0.9")
    assert result["stats"]["renarrate"]["fired"] is True
    assert result["stats"]["renarrate"]["trigger"] == "keep_rate"
    assert len(model.s1_prompts) == 2


def test_coverage_suspect_is_armed_with_no_threshold(monkeypatch):
    """The unambiguous arm needs no configuration — it is armed by construction."""
    assert extractor_v2._renarrate_trigger(
        {"coverage_suspect": True, "candidates": 0}, {"keep_rate": None},
        None) == "coverage_suspect"
    # …and it is not reachable any other way without one.
    assert extractor_v2._renarrate_trigger(
        {"coverage_suspect": False, "candidates": 4}, {"keep_rate": 0.25},
        None) is None
    # …and a batch from a FAILED S2 is not "prose in, nothing out": the empty
    # list is the instrument's, not the narrative's (review F1).
    assert extractor_v2._renarrate_trigger(
        {"coverage_suspect": True, "candidates": 0}, {"keep_rate": None},
        None, s2_failed=True) is None, (
        "a raised S2 call must never arm the content-level retry"
    )


def test_an_arbiter_batch_renarrate_is_consumed(monkeypatch):
    """The batch-level token `BATCH_OUTCOMES` carries must actually do something."""
    model, result = _run(monkeypatch, ["Alice and Bob met."],
                         [_embed("FirstOnly"), _embed("SecondOnly")],
                         arbiter=_renarrate_arbiter)
    assert result["stats"]["renarrate"]["trigger"] == "arbiter_renarrate"
    assert len(model.s1_prompts) == 2


# ── 5. the record ─────────────────────────────────────────────────────────

def test_the_run_records_both_arms(monkeypatch):
    """`stats["renarrate"]` carries both arms and which extraction was kept."""
    _, result = _run(monkeypatch,
                     ["Alice set a budget.", "Alice set a budget, restated."],
                     [_EMPTY_EMBED, _embed("Northwind")])
    renarrate = result["stats"]["renarrate"]
    for key in ("fired", "trigger", "items", "narrative", "emphasis", "first",
                "second", "merged"):
        assert key in renarrate, f"the record must carry {key!r}"
    for arm in (renarrate["first"], renarrate["second"]):
        for key in ("narrative_chars", "candidates", "kept", "keep_rate"):
            assert key in arm, f"each arm must carry {key!r}"
    assert "RE-NARRATE" in renarrate["emphasis"], (
        "the emphasis block the retry used must be recoverable from the record "
        "itself — a run that re-narrated with an unrecorded prompt cannot be "
        "audited (I2: no action without a record)"
    )


def test_the_census_stays_absent_when_the_retry_does_not_fire(monkeypatch):
    """A healthy run reports `fired: False` — never a fabricated episode."""
    _, result = _run(monkeypatch, ["Alice set a budget."], [_embed("Northwind")])
    assert result["stats"]["renarrate"] == {"fired": False}


def test_the_mentions_surface_keeps_ONE_shape(monkeypatch):
    """`mentions.stats` is `{first, second}` on both returns, never flat.

    It used to be the arm's FLAT counter dict on a non-retry run and
    `{first, second}` on a retried one, so a consumer had to guess which shape
    it held — the conditional-shape failure this file and `relations`/`renarrate`
    exist to rule out (review at `86ed2e9b7`, P2).
    """
    _, healthy = _run(monkeypatch, ["Alice set a budget."], [_embed("Northwind")])
    _, retried = _run(monkeypatch,
                      ["Alice set a budget.", "Alice set a budget, re-stated."],
                      [_EMPTY_EMBED, _embed("Northwind")])
    assert healthy["stats"]["renarrate"] == {"fired": False}
    assert retried["stats"]["renarrate"]["fired"] is True
    for label, res, second_is_none in (("healthy", healthy, True),
                                       ("retried", retried, False)):
        stats = (res.get("mentions") or {}).get("stats")
        assert isinstance(stats, dict), f"{label}: mentions.stats must be a dict"
        # ⛔ MEMBERSHIP, not set equality: the post-S4 union pass adds a `union`
        # key on any run that holds a mention (`route_mentions` on
        # `complete_list`), so `== {"first", "second"}` would pass only on a
        # mention-free fixture and FAIL ON CORRECT CODE (review round 3, P2).
        assert {"first", "second"} <= set(stats), (
            f"{label}: the ARM keys must be present on both returns — got "
            f"{sorted(stats)!r}"
        )
        assert isinstance(stats["first"], dict), f"{label}: first arm is a dict"
        assert (stats["second"] is None) is second_is_none, (
            f"{label}: second is None exactly when no retry ran"
        )


def test_the_chunk_counters_cover_BOTH_arms(monkeypatch):
    """The run-level chunk counters are the two arms' SUM, never arm A's alone.

    Both S1 passes chunked the transcript, so a retry doubles the population.
    Bound from arm A only, a retry whose SHIPPED narrative came from a second
    arm with a failed chunk still reported `failed_chunks == 0` — the surface
    said "clean" about the arm that shipped (review at `86ed2e9b7`, P2).

    ⛔ THE FIXTURE IS THE PROOF (round-4 review, F3 — a finding about THIS test).
    The previous fixture could only ever satisfy the `failed` leg as
    `0 + 0 == 0`: `_Scripted` never raised, so arm B's `failed` was structurally
    0 and the mutant the test exists to catch — rebinding `failed_chunks =
    arm["failed_chunks"]` — PASSED it. Here arm B's FIRST chunk raises (a real
    per-chunk failure, caught by `_arm_extract`'s own handler) while its SECOND
    chunk still yields a narrative that WINS the tie-break, so the SHIPPED
    narrative is the arm with the failure and the sum is `0 + 1` — a number no
    single-arm rebind can produce.
    """
    monkeypatch.setattr(extractor_v2.time, "sleep", lambda _s: None)
    monkeypatch.delenv("TORTOISE_RENARRATE_KEEP_RATE", raising=False)

    class _SecondArmS1ChunkFails(_Scripted):
        """Arm B's first chunk raises; its second chunk still yields a story."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._seen: list[str] = []
            self._fail: str | None = None

        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" not in system:
                return super().complete(system=system, user=user,
                                        max_tokens=max_tokens)
            key = system + "\x00" + user
            if key not in self._seen:
                self._seen.append(key)
            if self._fail is None and len(self._seen) == 3:
                # Arm A's two chunk prompts are distinct; the third DISTINCT
                # prompt is arm B's first chunk. Keyed on the prompt, never on a
                # call counter: `_complete` retries the SAME prompt, and a
                # counter would take the retries — and arm B's second chunk —
                # down with it, emptying the arm that must win the tie-break.
                self._fail = key
            if key == self._fail:
                self.s1_prompts.append(system)
                raise RuntimeError("S1 chunk exploded on the retried arm")
            return super().complete(system=system, user=user,
                                    max_tokens=max_tokens)

    model = _SecondArmS1ChunkFails(
        ["Alice budget is 40k.", "Bob ships Friday.",
         "Emily writes the second narrative."],
        [_EMPTY_EMBED, _embed("Northwind")])
    result = extract_session_v2(model, list(CONVERSATION), sdk=None,
                                session_id="11002-test",
                                session_date="2026-11-08", chunk_size=1)
    renarrate = result["stats"]["renarrate"]
    arms = renarrate["chunks"]
    assert renarrate["fired"] is True, "the empty first arm fired the retry"
    assert renarrate["narrative"] == "second", (
        "the fixture must SHIP the arm whose chunk failed, or the assertion "
        "below is not about the surface the defect mis-reported"
    )
    assert set(arms) == {"first", "second"}
    for arm in arms.values():
        assert set(arm) == {"chunks", "failed"}
    # ⛔ NON-VACUITY FIRST: the sums below are only discriminating because the
    # two arms disagree. `0 + 0 == 0` is what let the pre-fix rebind pass.
    assert arms["first"]["failed"] == 0, "arm A's chunks all returned"
    assert arms["second"]["failed"] == 1, (
        "arm B really did fail exactly one chunk — without this the `failed` "
        "leg can be satisfied by 0 + 0 and the mutant goes green"
    )
    assert any("S1 chunk failed" in e for e in result["errors"]), (
        "the failed chunk is still recorded loudly in the run's errors"
    )
    assert result["stats"]["chunks"] == (arms["first"]["chunks"]
                                         + arms["second"]["chunks"]), (
        "stats['chunks'] must be the sum of both arms' chunk counts"
    )
    assert result["stats"]["failed_chunks"] == (arms["first"]["failed"]
                                                + arms["second"]["failed"]), (
        "stats['failed_chunks'] must be the sum of both arms' failures"
    )
    assert arms["second"]["chunks"] > 0, (
        "the second arm really did chunk the transcript — non-vacuity"
    )


def test_every_return_carries_the_renarrate_channel(monkeypatch):
    """`stats["renarrate"]` is on EVERY return, the empty-conversation one too.

    It is an evidence surface about whether a mechanism acted — the same class as
    `vet`/`classify_later`/`identity`, which this function already makes
    unconditional — so a consumer never has to tell "did not fire" from "this
    build has no retry". The empty-conversation return is hand-enumerated and
    was the one path that omitted it (review F2), so a consumer crashed with
    `KeyError` on exactly the session where nothing ran.
    """
    _, result = _run(monkeypatch, ["Alice set a budget."], [_embed("Northwind")])
    assert _entity_names(result) == {"Northwind"}, "the fixture really did extract"
    empty = extract_session_v2(_Scripted(["story"], [_EMPTY_EMBED]), [],
                               sdk=None, session_id="11002-empty")
    assert empty["stats"]["renarrate"] == {"fired": False}
    assert empty["stats"]["llm"]["calls"] == 0, "nothing ran, so nothing was spent"


def test_the_emphasis_block_is_bounded():
    """The block is a bounded paragraph — not a second prompt."""
    block = extractor_v2._renarrate_emphasis("coverage_suspect")
    assert 200 < len(block) < 1200, len(block)
    assert "{memory_granularity}" not in block, (
        "the block rides the granularity slot; a placeholder here would render "
        "literally, since run_s1 substitutes only at the template level"
    )


def test_check_batch_gained_no_actionable_key():
    """The gate stays a SELECTOR: the retry reads the existing signals only.

    `vet_gate`'s allow-list is the boundary the successor must not cross — a new
    key here would make the coverage detector a gap-filler.
    """
    from tortoise import vet_gate

    keys = set(vet_gate.check_batch({}, "one. two."))
    assert keys == {
        "narrative_statements", "candidates", "by_section",
        "coverage_ratio", "coverage_suspect",
    }


def test_the_tie_break_metric_is_none_over_no_candidates():
    """`_keep_rate` is None — never 0.0 or 1.0 — when nothing was considered.

    An unmeasurable arm must lose a strict comparison rather than look perfect,
    and this is the value the tie-break actually compares.
    """
    assert extractor_v2._keep_rate(0, 0) is None
    assert extractor_v2._keep_rate(4, 0) == 1.0
    assert extractor_v2._keep_rate(4, 1) == 0.75
    assert extractor_v2._keep_rate(4, 9) == 0.0


@pytest.mark.parametrize("raw", ["", " ", "banana", "0", "-1", "1.5"])
def test_a_threshold_that_is_not_a_rate_disarms(monkeypatch, raw):
    """Every non-rate spelling reads as DISARMED — the fail-safe direction."""
    monkeypatch.setenv("TORTOISE_RENARRATE_KEEP_RATE", raw)
    assert extractor_v2._renarrate_threshold() is None


def test_a_threshold_in_range_arms(monkeypatch):
    monkeypatch.setenv("TORTOISE_RENARRATE_KEEP_RATE", "0.85")
    assert extractor_v2._renarrate_threshold() == 0.85


# ── the two surfaces round 5 reviewed as UNASSERTED ────────────────────────
# Round 5's verdict was ISSUES 2, both test-gaps on new behaviour: the removal
# pool had NO assertion anywhere (emptying it redded no row) and the per-arm
# `coverage_suspect` — the flag §4.4's F3 removal-gate is defined over — was
# unasserted. Both are pinned here, with the mutation each one dies under named.

def test_the_removal_pools_of_both_arms_are_unioned():
    """`_merge_arm_pools` — the record that stops a resurrection and a 422.

    Both arms compose the SHIPPED list (the union), so both arms' removals must
    carry into the post-S4 pass: an item either arm DISCARDED must not be
    resurrected by that pass, and an entity either arm removed must still be
    restorable when the other arm's narrative references it (`apply_vet`'s Rule
    4, whose failure mode is a session-wide Layer-1 422).

    ⛔ NON-VACUITY, measured against the shapes this names: `return {}` fails
    every assertion; first-arm-only drops `beta` and `Bob`; second-arm-only
    drops `alpha`; swapping the collision order returns Alice's FIRST note.
    """
    first = {"removed_texts": {"alpha", "shared"},
             "removed_entities": {"Alice": {"note": "from-first"}}}
    second = {"removed_texts": {"beta", "shared"},
              "removed_entities": {"Bob": {"note": "from-second"},
                                   "Alice": {"note": "from-second"}}}
    merged = extractor_v2._merge_arm_pools(first, second)
    assert merged["removed_texts"] == {"alpha", "beta", "shared"}, (
        "the UNION, not one arm's set — a lost removal text can be resurrected"
    )
    assert set(merged["removed_entities"]) == {"Alice", "Bob"}, (
        "an entity removed by EITHER arm must stay restorable by Rule 4"
    )
    assert merged["removed_entities"]["Alice"] == {"note": "from-second"}, (
        "the SECOND arm wins a key collision — the same rule the item merge uses"
    )
    # Fail-open in the pool's own direction: an unreadable side contributes
    # nothing rather than raising, so one broken arm cannot fail the run.
    assert extractor_v2._merge_arm_pools(first, None)["removed_texts"] == {
        "alpha", "shared"}
    assert extractor_v2._merge_arm_pools(None, "not a pool") == {
        "removed_texts": set(), "removed_entities": {}}


def test_the_pick_hands_the_union_pool_to_the_post_s4_pass():
    """The CONSUMER edge: `_renarrate_pick` publishes the union as `vet_pool`.

    Round 5 measured that emptying the helper redded nothing. Asserting only the
    helper would leave the same hole one level up, where the pool is actually
    consumed: a pick that published the second arm's pool alone would drop the
    first arm's removals from a list that IS the union of both.

    ⛔ AND ASSERTING THE HELPER ALONE **WAS** THE SAME HOLE (round 4 of the
    rebase review, measured): reverting the consumption site
    (`vet_s2_pool = picked["vet_pool"]` -> the first arm's pool) left this file
    and the two extractor suites at `424 passed, 1 skipped`. So the row below
    drives the REAL pipeline through a retried keep-rate run and reads the
    `prior` the post-S4 pass is handed — which is the only carrier of the
    first arm's DISCARD protection when the first arm yields nothing.
    """
    def _arm(pool, rate):
        return {"story": "n", "embed_list": {"entities": []},
                "metrics": {"keep_rate": rate}, "vet_pool": pool,
                "vet_stats": {}, "mention_stats": {}, "warnings": [],
                "batch": {}, "chunks": 1, "failed_chunks": 0, "s2_failed": False}

    first = _arm({"removed_texts": {"from-first"},
                  "removed_entities": {"A": {}}}, 0.5)
    second = _arm({"removed_texts": {"from-second"},
                   "removed_entities": {"B": {}}}, 0.9)
    picked = extractor_v2._renarrate_pick(first, second)
    assert picked["vet_pool"]["removed_texts"] == {"from-first", "from-second"}, (
        "the post-S4 pass must receive BOTH arms' removal texts"
    )
    assert set(picked["vet_pool"]["removed_entities"]) == {"A", "B"}, (
        "and BOTH arms' restorable entities"
    )


def test_the_pipeline_hands_BOTH_arms_pools_to_the_post_s4_pass(monkeypatch):
    """The SAME claim, on the wire: what `extract_session_v2` actually passes.

    Two arms with DIFFERENT candidates, each discarding its own first item, so
    the two pools are distinguishable by name: `Alpha` (first arm) and `Gamma`
    (second arm). Each arm's own VET pass is handed no `prior`; the post-S4
    union pass must be handed BOTH — the first arm's `Alpha` is the one a
    second-arm-only pool would silently lose, and losing it re-opens
    `apply_vet`'s resurrection path for an item S2 discarded.
    """
    payload_a = json.dumps({
        "entities": [{"name": "Alpha", "kind": "core:org", "lifecycle": "created"},
                     {"name": "Beta", "kind": "core:org", "lifecycle": "created"}],
        "events": [], "operators": [], "points": [],
        "chain_notes": [], "link_before_create": [],
    })
    payload_b = json.dumps({
        "entities": [{"name": "Gamma", "kind": "core:org", "lifecycle": "created"},
                     {"name": "Delta", "kind": "core:org", "lifecycle": "created"}],
        "events": [], "operators": [], "points": [],
        "chain_notes": [], "link_before_create": [],
    })
    priors: list = []
    real = extractor_v2._run_vet_pass

    def _spy(embed_list, narrative, **kw):
        priors.append(kw.get("prior"))
        return real(embed_list, narrative, **kw)

    monkeypatch.setattr(extractor_v2, "_run_vet_pass", _spy)
    _, result = _run(monkeypatch, ["Alice and Bob met.", "Carol and Dan met."],
                     [payload_a, payload_b], arbiter=_discarding_arbiter,
                     keep_rate="0.9")
    assert result["stats"]["renarrate"]["fired"] is True, (
        "the keep-rate arm must fire, or there is no union pass to inspect"
    )
    assert len(priors) == 3, (
        f"one VET pass per arm plus the post-S4 union pass, got {len(priors)}")
    assert priors[0] is None and priors[1] is None, (
        "each ARM's own pass starts from no prior removals")
    pool = priors[2] or {}
    restored = set((pool.get("removed_entities") or {}))
    # The pool keys are NORMALISED (`_norm`), so `Alpha` arrives as `alpha`.
    assert {"alpha", "gamma"} <= restored, (
        "the post-S4 pass must be handed BOTH arms' removals — the first arm's "
        f"`Alpha` is what a second-arm-only pool drops; got {sorted(restored)}")


def test_each_arm_records_its_own_coverage_verdict(monkeypatch):
    """F3's predicate reads the PRE-retry arm, so each arm records its OWN flag.

    §4.4's F3 — the gate on removing `run_s4` — is defined over the pre-retry
    arm: "S4 added items while `coverage_suspect` is False". The pipeline's
    published `result["vet"]["s2"]` is the SECOND arm's block, and on a retried
    run that arm is an INDEPENDENT S2 run whose flag can be True again (measured
    on `_EMPTY_EMBED` twice: BOTH arms flag) — it is the `fired` flag, not any
    property of the second arm's verdict, that makes a retried run unreachable
    for F3. A reader counting F3 against the second arm's block would therefore
    attribute the wrong arm's verdict to the session.

    ⛔ NON-VACUITY: this dies if `_arm_metrics` stops recording the flag (the
    key is missing), if it records the PIPELINE's flag for both arms (then
    `first` is False), and if it negates it (both assertions invert) — which is
    why the two arms are asserted to DIFFER rather than to each be truthy.
    """
    _, result = _run(monkeypatch,
                     ["Alice set a budget. Bob ships Friday."],
                     [_EMPTY_EMBED, _embed("Northwind")])
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True
    assert renarrate["trigger"] == "coverage_suspect"
    assert renarrate["first"]["coverage_suspect"] is True, (
        "the arm that fired the retry lost everything — F3's pre-retry arm"
    )
    assert renarrate["second"]["coverage_suspect"] is False, (
        "this fixture's second arm emitted candidates, so ITS flag is False — a "
        "fixture property, NOT a mechanism one: an empty second yield flags too"
    )
