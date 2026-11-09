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
   never happens without a record (I2). And both arms' VERDICTS ride
   `stats["renarrate"]["decisions"]`: the post-S4 re-vet only re-judges items
   that SHIP, so a first-arm discard is the one verdict nothing re-derives
   (#5005's no-silent-discard must not lapse on the retried path).
8. **The failure path does not spend the retry** — an S1 that failed (empty
   narrative) yields no batch to be poor about, and a **failed S2 call** is a
   transport-class failure, not a poor batch: its empty list is byte-identical
   to a genuinely empty one, so the trigger must be told which it was. Both
   arms' retries and escalation already happened INSIDE their own stage; the
   one content-level retry is never spent on them.
9. **The recorded numbers are ONE population, and the loss is the APPLIED one**
   (`test_the_arm_counts_what_vet_APPLIED_not_what_it_said`,
   `test_the_arm_denominator_is_the_list_vet_judged`) — the tie-break and
   §4.2's hand calibration both read `candidates`/`discarded`/`keep_rate`, so a
   rate divided across two different lists, or a loss counted from VERDICTS
   where `apply_vet` downgraded the discard to a keep, decides the run's
   narrative on a number that never happened.

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
        # S4's `user` prompt carries the NARRATIVE the run shipped, so it is the
        # one observable that separates `story` from `story_arc` (a handler that
        # restored only one of the pair leaves the other on arm B, and only S4
        # reads `story`).
        self.s4_prompts: list[str] = []

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
            # The rendered S4 prompt (with the narrative block) arrives in
            # `system`; `user` is the short instruction. Keep both.
            self.s4_prompts.append(system + "\n" + user)
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


def _raising_second_arm_model():
    """A model whose SECOND S2 call raises — arm B's transport failure.

    Shared by the two cases that pin what a RETRIED run records about a call
    that threw: the vet surface must not claim the gate did not run, and the
    arm must not publish a coverage verdict for a batch that never existed.
    The first S2 call answers `FirstOnly`, so arm A is a normal arm.
    """
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

    return _SecondArmS2Down(
        ["Alice and Bob met.", "Alice met Bob about the budget."],
        [_embed("FirstOnly"), _embed("SecondOnly")],
    )


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
    model = _raising_second_arm_model()
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


def test_a_raising_arm_records_NO_coverage_verdict(monkeypatch):
    """A failed S2 has NO batch verdict — the arm records `None`, not `True`.

    `check_batch` reads `(embed_list, story)`, and a RAISING S2 leaves the list
    empty *without saying anything about the narrative's yield*, so its batch is
    byte-identical to a genuinely empty one — `coverage_suspect=True`, "prose
    in, ZERO candidates out". That is the very conflation
    :func:`_renarrate_trigger`'s `s2_failed` exists to refuse, and until now the
    refusal stopped at the TRIGGER: the arm's published record still carried
    `True` for a call that threw.

    It is not cosmetic. §4.4 points the next lane at *this key*,
    `stats["renarrate"][<arm>]["coverage_suspect"]`, as F3's measurement
    surface, and F3's flag clause is already an INFERENCE rather than a
    measurement — a fabricated `True` there invents the datum that decides
    whether `run_s4` may be deleted.

    ⛔ NON-VACUITY: the flag is asserted to be `True` for the SAME batch when
    the failure is not declared, so the `None` is the qualifier's effect and not
    a batch that had nothing to report. On the pre-fix code the run's second arm
    records `True` here and this test is red.
    """
    monkeypatch.setattr(extractor_v2.time, "sleep", lambda _s: None)
    result = extract_session_v2(_raising_second_arm_model(), list(CONVERSATION),
                                sdk=None, session_id="11002-test",
                                session_date="2026-11-08",
                                vet_arbiter=_renarrate_arbiter)
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True and (
        renarrate["trigger"] == "arbiter_renarrate")
    first, second = renarrate["first"], renarrate["second"]
    assert second["candidates"] == 0, "arm B's S2 raised, so it yielded nothing"
    assert second["s2_failed"] is True, (
        "the record must say WHY the flag below is not a verdict"
    )
    assert second["coverage_suspect"] is None, (
        "a call that threw produced no batch, so it has no coverage verdict — "
        "publishing `True` records 'prose in, zero candidates out' about a call "
        "that never answered"
    )
    assert first["s2_failed"] is False and first["coverage_suspect"] is False, (
        "arm A answered and emitted candidates — its two-state verdict is "
        "untouched by the other arm's failure"
    )
    # ⛔ The gate itself is unchanged: the SAME batch, judged without the failure
    # declared, still reads `True`. The `None` above is a RECORD decision.
    batch = extractor_v2._vet_gate.check_batch(
        {}, "Alice set a budget. Bob ships Friday.")
    assert batch["coverage_suspect"] is True, (
        "`check_batch` must keep flagging the empty batch — the qualifier lives "
        "in the record, not in the gate"
    )
    declared = extractor_v2._arm_metrics(
        "Alice set a budget. Bob ships Friday.", batch, None, None)
    assert declared["coverage_suspect"] is True and declared["s2_failed"] is False
    failed = extractor_v2._arm_metrics(
        "Alice set a budget. Bob ships Friday.", batch, None, None, s2_failed=True)
    assert failed["coverage_suspect"] is None and failed["s2_failed"] is True


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


def _entity_victim(items, name: str) -> str | None:
    """The id of the ENTITY called ``name``, in the arbiter's own item view.

    The arbiter is handed a projected view (`{id, section, text, kind}`), NOT the
    embed dicts, so a helper that looked for `item["name"]` would read `None` and
    silently fail open — which is how a fixture stops testing what it claims to.
    """
    return next((i["id"] for i in items
                 if i.get("section") == "entities" and i.get("text") == name),
                None)


def _downgraded_discard_arbiter(items, narrative):
    """DISCARD a still-referenced entity (apply_vet downgrades it) + RENARRATE.

    The batch verdict is what fires the retry, so the arm's numbers get recorded
    on a run whose ONLY discard was downgraded to a keep.
    """
    victim = _entity_victim(items, "Alpha")
    return {"verdicts": ([{"id": victim, "outcome": "DISCARD",
                            "rule_id": "test.downgrade", "reason": "review"}]
                          if victim else []),
            "batch": {"outcome": "RENARRATE", "reason": "retry"}}


def _discarding_arbiter_without_batch_verdict(items, narrative):
    """The same downgraded discard, with NO batch token — only the rate can fire."""
    victim = _entity_victim(items, "Alpha")
    return {"verdicts": ([{"id": victim, "outcome": "DISCARD",
                            "rule_id": "test.downgrade", "reason": "review"}]
                          if victim else [])}


def _discard_beta_arbiter(items, narrative):
    """DISCARD the one entity nothing references — a removal that IS applied."""
    victim = _entity_victim(items, "Beta")
    return {"verdicts": ([{"id": victim, "outcome": "DISCARD",
                            "rule_id": "test.keep_rate", "reason": "poor"}]
                          if victim else [])}


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
                "second", "merged", "decisions"):
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


def test_the_arm_counts_what_vet_APPLIED_not_what_it_said(monkeypatch):
    """A DISCARD the Layer-1 guard downgraded is not a loss in the arm's rate.

    `vet_candidates`' `stats["discarded"]` counts **verdicts**; `apply_vet`
    downgrades a discard to KEEP when the entity is still referenced by a
    surviving candidate (Rule 2 — dropping it would fail `validate_layer1` and
    422 the whole session). Reading the verdict count into `keep_rate` reports a
    loss that never happened, and the tie-break compares exactly that number.

    Both halves are asserted on the same run, because the defect is the
    DISAGREEMENT: the arm says 0 discarded / 1.0, VET's own block says 1. A test
    that only checked \"the arm reports something\" would pass on the pre-fix
    code.
    """
    embedded = json.dumps({
        "entities": [{"name": "Alpha", "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [], "operators": [],
        "points": [{"content": "the Alpha rollout moves to Q3",
                    "pointKind": "statement",
                    "about_entities": ["Alpha"]}],
        "chain_notes": [], "link_before_create": [],
    })
    _model, result = _run(monkeypatch,
                          ["Alice and Bob met.", "Alice and Bob met again."],
                          [embedded], arbiter=_downgraded_discard_arbiter)
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True, (
        "the fixture must reach a recorded arm — an arbiter batch RENARRATE "
        "fires it, and the assertion below is about that arm's numbers")
    first = renarrate["first"]
    assert first["candidates"] == 2, (
        "one entity and one point — the list VET judged")
    assert first["discarded"] == 0, (
        "the discard was DOWNGRADED to a keep, so nothing was removed")
    assert first["kept"] == 2 and first["keep_rate"] == 1.0, (
        "a downgraded discard must not lower the recorded yield")
    # ⛔ NON-VACUITY: the verdict really was a DISCARD, and the guard really did
    # downgrade it. Without this pair the test would pass on a fixture where the
    # arbiter was never consulted.
    assert (result["vet"]["s2"]["stats"] or {}).get("discarded") == 1, (
        "the gate's OWN block must still report the verdict it issued")
    assert any("Layer-1 referential integrity" in w for w in result["warnings"])
    assert "Alpha" in {e.get("name") for e in
                       (result["embed_list"] or {}).get("entities", [])}


def test_a_phantom_discard_does_not_arm_the_keep_rate_threshold(monkeypatch):
    """The same fixture, armed: a downgraded discard must NOT spend the retry.

    The discriminator against the pre-fix code is exact — a verdict-based rate
    is `(2-1)/2 = 0.5`, which is below a 0.95 threshold and would fire a second
    S1+S2 band; the applied rate is `1.0`, which must not.
    """
    embedded = json.dumps({
        "entities": [{"name": "Alpha", "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [], "operators": [],
        "points": [{"content": "the Alpha rollout moves to Q3",
                    "pointKind": "statement",
                    "about_entities": ["Alpha"]}],
        "chain_notes": [], "link_before_create": [],
    })
    model, result = _run(monkeypatch, ["Alice and Bob met."], [embedded],
                         arbiter=_discarding_arbiter_without_batch_verdict,
                         keep_rate="0.95")
    assert result["stats"]["renarrate"] == {"fired": False}, (
        "no candidate left the arm, so no threshold can be under")
    assert len(model.s1_prompts) == 1, "the retry was never spent"


def test_the_arm_denominator_is_the_list_vet_judged(monkeypatch):
    """`candidates` is VET's population, never the pre-mentions batch count.

    `check_batch` is computed BEFORE :func:`route_mentions` (deliberately: the
    coverage signal must exist with every switch off), and `route_mentions`
    REMOVES a held mention from `entities[]` before VET sees the list. So on the
    referentiality arm the batch is one candidate larger than the population VET
    judged, and dividing VET's removals by the batch count over-states the
    denominator — the arm's rate, which the tie-break compares, comes out HIGHER
    than the truth.

    The two counts differ by construction here: 3 entities + 1 point is 4 at the
    batch, one entity is held as a mention, and VET judges 3.
    """
    monkeypatch.setenv("TORTOISE_REFERENTIALITY", "1")
    embedded = json.dumps({
        "entities": [{"name": "It", "kind": "core:org",
                      "lifecycle": "created"},
                     {"name": "Alpha", "kind": "core:org",
                      "lifecycle": "created"},
                     {"name": "Beta", "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [], "operators": [],
        "points": [{"content": "the Alpha rollout moves to Q3",
                    "pointKind": "statement",
                    "about_entities": ["Alpha", "It"]}],
        "chain_notes": [], "link_before_create": [],
    })
    _, result = _run(monkeypatch,
                     ["Alice and Bob met.", "Alice and Bob met again."],
                     [embedded], arbiter=_discard_beta_arbiter,
                     keep_rate="0.9")
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True and renarrate["trigger"] == "keep_rate"
    first = renarrate["first"]
    # ⛔ NON-VACUITY FIRST: the mention really was held, so the pre-mentions
    # batch really was 4. A fixture where nothing was held makes this leg
    # satisfied by both the old and the new denominator.
    mentions = (result.get("mentions") or {}).get("stats") or {}
    assert (mentions.get("first") or {}).get("held_surfaces") == 1, (
        "the fixture must actually hold one mention, or the two populations "
        "are the same list and this test proves nothing")
    assert first["candidates"] == 3, (
        "the population VET judged: 3 entities minus the held mention, plus "
        "the point — a batch-based denominator would report 4")
    assert first["candidates"] == (result["vet"]["s2"]["stats"] or {})["candidates"]
    assert first["discarded"] == 1 and first["kept"] == 2
    assert first["kept"] + first["discarded"] == first["candidates"], (
        "the two halves must partition the ONE population they are a rate of")


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

    ⛔ `partial_gaps` (#11534) is the ONE admitted addition, and it is admitted
    because it is a REPORT, not a remedy: it names WHICH clause span no candidate
    accounts for (with a char span into the narrative) and nothing in this module
    acts on it. The consumer is the `S2.2b-ADD` stage behind
    `TORTOISE_PARTIAL_GAP_FILL`, which is DEFAULT OFF — so the gate itself still
    selects and never fills, which is the property this arm pins. A key that a
    SELECTOR could act on (e.g. a pre-computed candidate to append) would not
    belong in this set.
    """
    from tortoise import vet_gate

    keys = set(vet_gate.check_batch({}, "one. two."))
    assert keys == {
        "narrative_statements", "candidates", "by_section",
        "coverage_ratio", "coverage_suspect", "partial_gaps",
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


def test_the_narrative_and_the_union_are_two_different_choices():
    """The ITEMS are the union; the NARRATIVE is ONE arm's. Both at once, bound.

    `run_s4` receives `picked["story"]` and `picked["embed_list"]` — so this
    function hands S4 a union of both arms' items completed against a narrative
    that may not mention the other arm's. That interaction was UNSTATED and
    UNTESTED (#11002 review, P3): the pool test next door covers the removals and
    the tie-break test covers the rate comparison, but nothing asserted the two
    outputs come from DIFFERENT arms.

    ⛔ NON-VACUITY: this dies if the pick stops unioning (then `first-only` is
    gone from `embed_list`), if the narrative stops following the strict `>`
    (then the tie case would flip), and if the pick returns one arm's bundle
    wholesale instead of merging the two choices (then one of the four assertions
    below inverts). It is asserted in BOTH directions — second strictly better,
    and an exact tie — because a rule that only kept the first would pass the
    tie half.
    """
    def _arm(story, item, rate):
        return {"story": story,
                "embed_list": {"entities": [{"name": item, "kind": "Thing"}]},
                "metrics": {"keep_rate": rate}, "vet_pool": {},
                "vet_stats": {}, "mention_stats": {}, "warnings": [],
                "batch": {}, "chunks": 1, "failed_chunks": 0, "s2_failed": False}

    def _names(picked):
        return {e["name"] for e in picked["embed_list"]["entities"]}

    first = _arm("first narrative", "first-only", 0.5)
    second = _arm("second narrative", "second-only", 0.9)
    picked = extractor_v2._renarrate_pick(first, second)
    assert picked["story"] == "second narrative", (
        "a STRICTLY better keep-rate takes the narrative")
    assert picked["narrative"] == "second" and picked["items"] == "union", (
        "both decisions are named separately, because they are different choices")
    _ids = _names(picked)
    assert _ids == {"first-only", "second-only"}, (
        f"the items are STILL the union of both arms — got {_ids}. The losing "
        "arm's narrative is dropped; its ITEMS are not")

    # The tie half: equal rates keep the FIRST narrative, and the union is
    # unchanged from the case above.
    tie = extractor_v2._renarrate_pick(first, _arm("second narrative", "second-only", 0.5))
    assert tie["story"] == "first narrative" and tie["narrative"] == "first", (
        "a tie keeps the FIRST — keeping the second would let a coin flip lower "
        "the accepted keep-rate")
    assert _names(tie) == {"first-only", "second-only"}, (
        "and the union does not depend on which narrative won")


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


# ── 10. the FIRST arm's audit record survives the retry ───────────────────

def _two_entities() -> str:
    """Alpha + Beta, referenced by nothing — so a DISCARD of either is APPLIED."""
    return json.dumps({
        "entities": [{"name": "Alpha", "kind": "core:org",
                      "lifecycle": "created"},
                     {"name": "Beta", "kind": "core:org",
                      "lifecycle": "created"}],
        "events": [], "operators": [], "points": [],
        "chain_notes": [], "link_before_create": [],
    })


def _first_arm_discard_and_renarrate(log: dict):
    """DISCARD one unreferenced entity on the FIRST arm, and ask for the retry.

    Every LATER pass (the retried arm, the post-S4 union pass) answers NO
    verdict, so every assertion this feeds is about arm A's own decisions —
    the ones the re-vet CANNOT re-derive, because an item arm A discarded is
    absent from the union that re-vet judges.
    """
    def _arbiter(items, narrative):
        log["passes"] = log.get("passes", 0) + 1
        if log["passes"] > 1:
            return {"verdicts": []}
        victim = _entity_victim(items, "Beta")
        return {"batch": {"outcome": "RENARRATE", "reason": "poor batch"},
                "verdicts": ([{"id": victim, "outcome": "DISCARD",
                               "rule_id": "test.first_arm",
                               "reason": "poor"}] if victim else [])}
    return _arbiter


def _first_arm_discard_run(monkeypatch):
    """One run whose FIRST arm discards and whose batch verdict fires the retry."""
    log: dict = {}
    _, result = _run(monkeypatch,
                     ["Alice and Bob met.", "Alice met Bob about the budget."],
                     [_two_entities(), _embed("SecondOnly")],
                     arbiter=_first_arm_discard_and_renarrate(log))
    assert log["passes"] >= 2, (
        "the fixture must actually reach the retry, or it tests the non-retry "
        "path #5005 already covered"
    )
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is True
    assert renarrate["trigger"] == "arbiter_renarrate"
    return result, renarrate


def test_the_first_arms_discard_audit_line_survives_the_retry(monkeypatch):
    """#5005's no-silent-discard property must not LAPSE on the retried path.

    The retry composes the shipped list as the union of two arms, so a
    candidate arm A removed is gone from the list that ships — and the
    `prior=vet_s2_pool` re-vet re-asks the arbiter only about items that ARE
    in that list. Arm A's removals are therefore the verdicts nothing
    re-derives, and the run's `warnings` is where they have to survive: the
    per-item `vet: discarded <section> '<text>'` line (and `vet: pruned N
    operator(s)`) is the only published record of WHAT arm A dropped.

    ⛔ Asserted EXACTLY ONCE, not merely present: the fix this guards is a
    `warnings` UNION across the two arms, and the run-level list is already
    extended with arm A's warnings at the call site before the retry is
    considered — so a union at the pick would DUPLICATE every first-arm line
    rather than restore it. Present-and-duplicated is the regression shape this
    pins, and it is the one a plausible reading of the pick produces.
    """
    result, _ = _first_arm_discard_run(monkeypatch)
    lines = [w for w in result["warnings"]
             if "vet: discarded" in w and "Beta" in w]
    assert len(lines) == 1, (
        "arm A's discard must appear in the run's warnings exactly once — "
        f"found {len(lines)}: {result['warnings']}"
    )


def test_the_first_arms_verdicts_ride_the_renarrate_record(monkeypatch):
    """Which rule dropped arm A's item rides `stats["renarrate"]["decisions"]`.

    The retry's own record carries both arms' NUMBERS (`_arm_metrics`) and the
    union accounting, but the numbers cannot say *under which rule* an arm A
    item was dropped — and `result["vet"]["s2"]["decisions"]` is the SECOND
    arm's block, published independently of the tie-break. So without an arm-A
    slot, "which rule dropped it, and what was its counterfactual" is
    unrecoverable for the only arm whose verdicts nothing re-derives.

    ⛔ NON-VACUITY: this fails on the pre-fix tree with a `KeyError` (no
    `decisions` key at all), and it fails against a record that publishes ONLY
    the second arm's block — the shape it exists to prevent.
    """
    _, renarrate = _first_arm_discard_run(monkeypatch)
    first = renarrate["decisions"]["first"]
    discarded = [v for v in first.values() if v.get("outcome") == "DISCARD"]
    assert discarded, (
        "arm A's per-item verdicts must be recoverable from the retry's own "
        f"record — got {first!r}"
    )
    assert [v.get("rule_id") for v in discarded] == ["test.first_arm"], (
        "the RULE that dropped the item is the part a bare count cannot carry"
    )
    assert "second" in renarrate["decisions"], (
        "ONE shape for both arms — a reader must not have to guess which arm "
        "the block belongs to"
    )


# ── 9. the retry is FAIL-OPEN (#11569) ────────────────────────────────────

def test_a_retry_that_raises_after_the_re_bind_keeps_the_first_arms_capture(
        monkeypatch):
    """A raise in the retry's tail must not take a COMPLETED capture with it.

    Every other stage in `extract_session_v2` fails open (`run_s1` per chunk,
    `run_s2`, `route_mentions`, `_run_vet_pass`, the classify pass). The retry
    did not: it runs *after* a complete first arm, and by the time its tail runs
    it has already re-bound `story`, `embed_list` and the vet records to arm B.
    So an exception there escaped `extract_session_v2` and destroyed a capture
    that had already succeeded — and a PARTIAL restore is worse than the raise,
    because it ships arm B's LIST beside arm A's NARRATIVE and reports success.

    ⛔ The seam is chosen deliberately: `_rate_text` is called from exactly ONE
    place — the retry's own final warning — and that place sits **after** every
    re-bind. A raise planted in `_renarrate_pick` (the other candidate) fires
    BEFORE the bindings move, so it would pass against a handler that restored
    nothing at all. Planting it here is what makes the restore load-bearing.

    ⛔ NON-VACUITY: on the pre-guard tree this test does not assert — the run
    raises out of `extract_session_v2`. Deleting a restore line reds on the
    assertion that names it — `story`, `story_arc`, `embed_list`,
    `mention_s2_stats`, `vet_s2_stats`, `vet_s2_pool`, `chunks`/`failed_chunks`,
    the warning truncation, `fired`, `trigger`, `retry_failed`, `emphasis`,
    `"first"`, `errors` and the census bump were each measured that way, one
    mutant at a time — which is the "restores only some bindings" mutant this
    exists to catch. ⛔ **No exception.** An earlier version of this docstring
    and an earlier version of the fixture both declared `vet_s2_pool`
    UNOBSERVABLE here; that declaration was WRONG and is falsified by
    measurement, not by argument — see the fixture's own comment below for the
    call log that settles it.

    ⛔ AND THE TWO ARM COUNTERS MUST DISAGREE (review round 2, P3). The first
    fixture chunked the transcript ONCE per arm and neither chunk failed, so
    arm A, arm B and their sum were all `0` and deleting
    `failed_chunks = arm["failed_chunks"]` left this test GREEN — the exact
    `0 + 0 == 0` trap `test_the_chunk_counters_cover_BOTH_arms` documents. Arm
    B's FIRST chunk now raises (caught by `_arm_extract`'s own handler) while
    its SECOND still yields a narrative, so the restored `failed_chunks` is the
    only way to read `0` instead of `0 + 1`.
    """
    def _boom(_metrics):
        # `_ParseError`, not `RuntimeError` (review round 2, P3): the census
        # class this raises is `parse_error`, which NOTHING ELSE in this run can
        # produce — `_classify_error` never returns it (its own docstring says
        # the stage callers produce it), and arm B's failed S1 chunk is a
        # `RuntimeError` landing in `transient_unknown`. With a `RuntimeError`
        # here, "some census bump happened" is satisfied BY THAT ARM, so a
        # census assertion would pass with the handler's own `_bump_census`
        # deleted — a false pass, measured.
        raise extractor_v2._ParseError("the retry's tail exploded")

    monkeypatch.setattr(extractor_v2, "_rate_text", _boom)
    monkeypatch.setattr(extractor_v2.time, "sleep", lambda _s: None)
    monkeypatch.delenv("TORTOISE_RENARRATE_KEEP_RATE", raising=False)
    # VET is armed so `vet_s2_stats` is arm-dependent: arm A's list is empty
    # (nothing to judge) while arm B's is not, so the restore is OBSERVABLE.
    # With VET off both arms record `{}` and the mutation is invisible.
    monkeypatch.setenv("TORTOISE_VET", "1")
    # ⛔ `vet_s2_pool` IS observable on this fixture — the residual below said
    # otherwise and was WRONG (measured at `a7f928cbd`: `_run_vet_pass` is called
    # THREE times and the third, the post-S4 union pass, is reached with a
    # non-None `prior`). What made it look unobservable is that BOTH arms'
    # pools are empty here (`{'removed_texts': set(), 'removed_entities': {}}`),
    # so no CONTENT can separate them — the fix is to stamp arm B's pool at the
    # seam the retry really goes through and read what the union pass is handed.
    # The line is load-bearing: `_run_vet_pass`'s `prior` docstring calls it
    # "required for correctness, not cosmetic", and `apply_vet` RULE 4 can
    # restore an entity out of the pool it is given.
    _real_pick = extractor_v2._renarrate_pick
    _real_vet = extractor_v2._run_vet_pass
    priors: list[object] = []

    def _pick_stamped(first, second):
        _picked = _real_pick(first, second)
        _picked["vet_pool"] = {"removed_texts": {"FROM-ARM-B"},
                              "removed_entities": {}}
        return _picked

    def _vet_recording(embed_list, narrative, **kw):
        priors.append(kw.get("prior"))
        return _real_vet(embed_list, narrative, **kw)

    monkeypatch.setattr(extractor_v2, "_renarrate_pick", _pick_stamped)
    monkeypatch.setattr(extractor_v2, "_run_vet_pass", _vet_recording)

    class _SecondArmFirstChunkFails(_Scripted):
        """Arm B's first chunk raises; its second chunk still yields a story.

        Keyed on the prompt, never on a call counter: `_complete` retries the
        SAME prompt, so a counter would take the retries — and arm B's second
        chunk — down with it, emptying the arm that must re-bind `embed_list`.
        """

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
                self._fail = key
            if key == self._fail:
                self.s1_prompts.append(system)
                raise RuntimeError("S1 chunk exploded on the retried arm")
            return super().complete(system=system, user=user,
                                    max_tokens=max_tokens)

    model = _SecondArmFirstChunkFails(
        # One answer per surviving chunk: arm A's two, then arm B's second (its
        # first raised). Arm A's narrative is "Alice"; arm B's is "Carol".
        ["Alice set a budget. Bob ships Friday.",
         "Alice set a budget. Bob ships Friday.",
         "Carol signs the contract. Dave reviews it."],
        # Arm A yields NOTHING (the coverage trigger), arm B yields a candidate
        # — so `embed_list` after the run says which arm shipped.
        [_EMPTY_EMBED, _embed("SecondOnly")],
    )
    result = extract_session_v2(model, list(CONVERSATION), sdk=None,
                                session_id="11002-test",
                                session_date="2026-11-08", chunk_size=1)
    assert len(model.s2_prompts) == 2, "the retry really did run and pay"

    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is False, (
        "`fired` describes what SHIPPED — the first arm's capture, unchanged"
    )
    assert renarrate["trigger"] == "coverage_suspect"
    assert "the retry's tail exploded" in renarrate["retry_failed"], (
        "the reason must be recorded, not swallowed"
    )
    assert renarrate["second"] is not None, (
        "arm B's metrics are the only record of the spend `stats['llm']['calls']` "
        "already counted — dropping them hides that a second band was paid"
    )
    # ⛔ EACH ARM'S OWN NUMBERS, named separately: the record's contract is that
    # a reader can reconcile EITHER path against `stats["llm"]["calls"]`, and
    # deleting `"first": arm["metrics"]` left this test green (measured). Arm A
    # is the empty, coverage-suspect arm — the trigger's own fact — and arm B is
    # the band that judged candidates; a record with the two collapsed cannot be
    # decomposed, and swapping them is caught here too.
    assert renarrate["first"]["candidates"] == 0, (
        "the record must carry ARM A's own metrics — it is the empty, "
        f"coverage-suspect arm: {renarrate['first']!r}"
    )
    assert renarrate["first"]["coverage_suspect"] is True, (
        f"arm A is the arm the trigger fired on: {renarrate['first']!r}"
    )
    assert renarrate["second"]["candidates"] > 0, (
        f"and ARM B's, the band that paid: {renarrate['second']!r}"
    )
    # ax-principles: the failure is on the caller's surface, not only in stats.
    assert any("re-narrate retry failed" in w
               for w in result.get("warnings") or []), (
        f"a fail-open path that says nothing is a silent swallow: "
        f"{result.get('warnings')}"
    )

    # ── the restore, binding by binding ───────────────────────────────────
    assert _entity_names(result) == set(), (
        "arm A's list is the one that must ship — a restore that skips "
        f"`embed_list` ships arm B's: {_entity_names(result)}"
    )
    story = result["story_arc"]
    assert "Alice set a budget" in story, "arm A's narrative must survive"
    assert "Carol signs the contract" not in story, (
        "a restore that skips `story` ships arm B's narrative beside arm A's "
        f"list — the half-restored shape: {story!r}"
    )
    # ⛔ `story` and `story_arc` are ONE line but TWO variables, and the pair is
    # only separable through S4's prompt: `story_arc` is published on the result,
    # `story` is what `run_s4` and `_clamped_mention(narrative=...)` actually read.
    # Restoring one and not the other SURVIVES every assertion above (measured),
    # so this is the leg that makes the split visible.
    assert model.s4_prompts, "S4 ran, so it saw a narrative"
    # ⛔ A phrase UNIQUE TO THE NARRATIVE, never the bare entity name (review round
    # 6, P3). `run_s4` appends `_render_source_transcript(edus)` to its prompt
    # (`extractor_v2.py:3627`, called with the run's `edus` at `:13260`), and this
    # fixture's transcript opens "Alice decided the budget is 40k" — so
    # `"Alice" in s4_prompts[0]` is satisfied by the TRANSCRIPT regardless of
    # `story`, and the positive leg pinned nothing. "Alice set a budget" is arm A's
    # own S1 answer and appears nowhere in the transcript.
    assert "Alice set a budget" in model.s4_prompts[0], (
        "S4 must be handed ARM A's narrative — `story_arc` can be right while the "
        f"`story` S4 reads is arm B's: {model.s4_prompts[0][:200]!r}"
    )
    assert "Carol" not in model.s4_prompts[0], (
        "arm B's narrative must not reach S4 on a fail-open run: "
        f"{model.s4_prompts[0][:200]!r}"
    )
    assert result["mentions"]["stats"]["second"] is None, (
        "a restore that skips `mention_s2_stats` leaves the retry's "
        f"`{{first, second}}` arm pair behind: {result['mentions']['stats']!r}"
    )
    # ⛔ AND THE ARM-A HALF, not only the `second: None` that clears arm B
    # (review round 5, P3). `mention_s2_stats` is a PAIR — the record's own
    # contract (`extractor_v2.py:13009-13016`) is "ONE SHAPE ALWAYS:
    # `{first, second}` on BOTH returns" — and the assertion above is satisfied
    # by a PARTIAL restore that drops arm A's half entirely (measured: replacing
    # the handler's dict with `{"second": None}` left 464 tests green). That is
    # the same half-restore this PR exists to forbid, one level down: the
    # shipped arm's mention stats vanish while `second: None` still reads right.
    assert set(result["mentions"]["stats"]) == {"first", "second"}, (
        "`{first, second}` is the contract on BOTH returns — a restore that "
        "keeps only `second: None` silently drops ARM A's statistics: "
        f"{result['mentions']['stats']!r}"
    )
    assert result["mentions"]["stats"]["first"] == {}, (
        "arm A shipped nothing, so its mention stats are the empty dict — an "
        "absent or arm-B value here means the restore read the wrong arm: "
        f"{result['mentions']['stats']!r}"
    )
    # ⛔ `vet_s2_stats` (the `batch` block) AND `vet_s2_pool` (the removal pool
    # the union pass prunes against) are two different bindings, and this
    # assertion pins the first. `vet_s2_stats` is arm-dependent even for an
    # EMPTY arm: VET records a `batch` block either way, so `candidates` is 0
    # for arm A and 2 for arm B.
    assert result["vet"]["s2"]["stats"]["candidates"] == 0, (
        "a restore that skips `vet_s2_stats` publishes arm B's judgement "
        "(2 candidates) about a list that did not ship — the vet surface "
        f"would describe an arm nobody chose: {result['vet']['s2']!r}"
    )
    # ⛔ AND `vet_s2_pool`, through its ONE consumer (`prior=`), which is why the
    # fixture stamps it. This is the gap the previous note DECLARED as an
    # unclosable residual; it is closed, and the declaration was wrong — the
    # union pass does run on this fixture (see the stamping block above).
    assert priors and priors[-1] is not None, (
        "the post-S4 union VET pass must have run and been handed a `prior` "
        f"pool on this fixture: {priors!r}"
    )
    assert "FROM-ARM-B" not in json.dumps(priors[-1], default=str), (
        "a restore that skips `vet_s2_pool` hands ARM B's removal pool to the "
        "post-S4 union pass, which prunes and restores against discards from a "
        f"list that did not ship: {priors[-1]!r}"
    )
    # The run-level chunk counters are the only observable that pins
    # `chunks`/`failed_chunks`: both arms ran, so a restore that skips them (or
    # doubles them) reads differently from arm A's own count.
    # ⛔ NON-VACUITY FIRST: the arms must DISAGREE, or `0 + 0 == 0` satisfies
    # the `failed_chunks` leg without the restore. Arm B really did fail a
    # chunk (see the fixture), so arm A's `0` and the sum `0 + 1` are separable.
    arms = renarrate["chunks"]
    assert arms["second"]["failed"] == 1, (
        "arm B must really have failed exactly one chunk — without this the "
        f"`failed_chunks` leg cannot discriminate: {arms}"
    )
    assert arms["first"]["failed"] == 0, f"arm A's chunks all returned: {arms}"
    assert arms["first"]["chunks"] > 0 and arms["second"]["chunks"] > 0, (
        f"both arms really chunked the transcript: {arms}"
    )
    assert result["stats"]["chunks"] == arms["first"]["chunks"], (
        "arm A's chunk count must ship alone — a mutant that keeps the sum "
        f"publishes {arms['first']['chunks'] + arms['second']['chunks']}: "
        f"{result['stats'].get('chunks')!r} vs {arms}"
    )
    assert result["stats"]["failed_chunks"] == 0, (
        "arm A had no failed chunk; a mutant that keeps arm B's count would "
        f"publish 0 + 1 = 1: {result['stats'].get('failed_chunks')!r}"
    )
    # ⛔ THE ASYMMETRY IS DELIBERATE, AND IT IS PINNED HERE RATHER THAN LEFT
    # IMPLICIT (review round 4, P3). The run-level counters describe the capture
    # that SHIPPED — arm A's alone — but `errors`/`error_census` are a LOG of
    # what happened and are deliberately NOT restored, so arm B's chunk failure
    # survives there. Publishing `failed_chunks == 0` beside an `errors` line
    # reading "1/2 S1 chunks failed" is therefore the intended shape, not a
    # contradiction, and it is readable through `renarrate["chunks"]`. Asserting
    # it keeps the shape from being "fixed" by someone who reads the pair as a
    # bug and restores the failure counters to match.
    assert any("S1 chunks failed" in e for e in result["errors"]), (
        "the second arm's chunk failure must stay on the error log even though "
        "its COUNTER is restored — `errors`/`error_census` are a record of what "
        f"happened, not a description of the shipped capture: {result['errors']}"
    )
    assert result["error_census"].get("s1_chunk_summary") == 1, (
        "the arm-B chunk failure's census class survives the restore for the "
        f"same reason: {result['error_census']}"
    )
    # P3 (review round 1): the failure must reach the ERROR surface and the
    # census, like every sibling fail-open arm in this function — not only the
    # warnings list, or the run reads clean on `result["errors"]`.
    assert any("re-narrate retry failed" in e for e in result["errors"]), (
        f"a fail-open path that is silent on `errors` hides the failure: "
        f"{result['errors']}"
    )
    # ⛔ AND THE CENSUS ITSELF (review round 2, P3). Asserting only `errors`
    # left `_bump_census(error_census, e)` unpinned — deleting it from the
    # handler left this whole suite GREEN (measured). The record's own comment
    # says the failure must reach "the ERROR surface and the census"; this is
    # the half that reads the census. `parse_error` is attributable because
    # nothing else in the run produces it (see `_boom`).
    assert result["error_census"].get("parse_error") == 1, (
        "a fail-open path that never reaches the census is invisible to the "
        f"report census, which is what triage reads: {result['error_census']}"
    )


def test_arm_bs_vet_warnings_do_not_survive_a_failed_retry(monkeypatch):
    """Arm B's per-item verdicts must not be published on an ARM A run.

    `vet_warnings.extend(picked["warnings"])` is additive and runs BEFORE the
    raise point, and `picked["warnings"]` is arm B's list alone. Without
    truncation the run publishes verdicts about a list that did not ship — the
    mirror of #5005's no-silent-discard, in the misleading direction.

    ⛔ NON-VACUITY: the marker is injected through `_renarrate_pick`, i.e. it
    enters the run the same way a real arm-B warning does (the pick's own
    `warnings` list), so deleting the handler's truncation line reds here.

    ⛔ AND ARM A MUST OWN A WARNING TOO (review round 5, P3). The truncation's
    POSITION was unpinned: `_vet_mark = len(vet_warnings)` sits after arm A's
    `extend` so the mark is A's line count, but BOTH fail-open fixtures observed
    `_vet_mark == 0`, where "truncate from the mark" and "clear everything" are
    indistinguishable. Replacing the mark with `0` therefore left 464 tests green
    while it would `del` the SHIPPED arm's warnings on every fail-open run. Arm
    A now carries a marker of its own, injected where arm A's warnings really
    come from (its `_arm_extract` bundle), and it must SURVIVE.
    """
    real_pick = extractor_v2._renarrate_pick
    real_arm = extractor_v2._arm_extract
    arm_calls = {"n": 0}

    def _pick_marked(first, second):
        picked = real_pick(first, second)
        picked["warnings"] = [*picked["warnings"],
                               "SECOND-ARM-ONLY: vet discarded entities 'SecondOnly'"]
        return picked

    def _arm_marked(*args, **kwargs):
        bundle = real_arm(*args, **kwargs)
        arm_calls["n"] += 1
        if arm_calls["n"] == 1:  # arm A — the capture that SHIPS
            bundle["warnings"] = [*bundle["warnings"],
                                  "FIRST-ARM-ONLY: arm A's own line"]
        return bundle

    def _boom(_metrics):
        raise RuntimeError("the retry's tail exploded")

    monkeypatch.setattr(extractor_v2, "_renarrate_pick", _pick_marked)
    monkeypatch.setattr(extractor_v2, "_arm_extract", _arm_marked)
    monkeypatch.setattr(extractor_v2, "_rate_text", _boom)
    result = extract_session_v2(
        _Scripted(["Alice set a budget. Bob ships Friday.",
                   "Carol signs the contract. Dave reviews it."],
                  [_EMPTY_EMBED, _embed("SecondOnly")]),
        list(CONVERSATION), sdk=None, session_id="11002-test",
        session_date="2026-11-08")
    warnings = result.get("warnings") or []
    assert arm_calls["n"] == 2, f"both arms ran: {arm_calls}"
    assert not any("SECOND-ARM-ONLY" in w for w in warnings), (
        "a run that ships arm A must not publish arm B's per-item verdicts: "
        f"{warnings}"
    )
    # ⛔ THE MARK'S POSITION: arm A's line sits BELOW `_vet_mark`, so it must
    # survive. A mark taken before arm A's own extend deletes it (measured).
    assert any("FIRST-ARM-ONLY" in w for w in warnings), (
        "truncation must remove ARM B's lines only, never the SHIPPED arm's — "
        "a `_vet_mark` taken before arm A's warnings deletes them: "
        f"{warnings}"
    )
    assert any("re-narrate retry failed" in w for w in warnings), (
        "the FAILURE line must still be published — truncation removes arm B's "
        f"lines, not the record of the failure: {warnings}"
    )


def test_a_raise_in_the_emphasis_lookup_still_fails_open(monkeypatch):
    """`_renarrate_emphasis` is the FIRST statement in the guarded block.

    If it raises and `emphasis` is not pre-bound, the handler itself raises
    `UnboundLocalError` and the capture is destroyed — the exact defect #11569
    removes, reintroduced by the guard's own first line.

    ⛔ NON-VACUITY: with `emphasis = None` removed, this reds with
    `UnboundLocalError`; with it present, the run returns and records
    `emphasis: None` (never computed).
    """
    def _boom(_trigger):
        raise RuntimeError("emphasis lookup exploded")

    monkeypatch.setattr(extractor_v2, "_renarrate_emphasis", _boom)
    result = extract_session_v2(
        _Scripted(["Alice set a budget. Bob ships Friday."],
                  [_EMPTY_EMBED, _embed("SecondOnly")]),
        list(CONVERSATION), sdk=None, session_id="11002-test",
        session_date="2026-11-08")
    renarrate = result["stats"]["renarrate"]
    assert renarrate["fired"] is False
    assert renarrate["emphasis"] is None, (
        "the record must say the emphasis was never computed"
    )
    assert "emphasis lookup exploded" in renarrate["retry_failed"]
    assert _entity_names(result) == set(), "arm A's capture survives"
