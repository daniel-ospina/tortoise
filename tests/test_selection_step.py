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


def test_every_return_carries_the_renarrate_channel(monkeypatch):
    """`stats["renarrate"]` is on EVERY return, the empty-conversation one too.

    It is an evidence surface about whether a mechanism acted — the same class as
    `vet`/`classify_later`/`identity`, which this function already makes
    unconditional — so a consumer never has to tell "did not fire" from "this
    build has no retry". The empty-conversation return is hand-enumerated and
    was the one path that omitted it (review F2), so a consumer crashed with
    `KeyError` on exactly the session where nothing ran.
    """
    model, _ = _run(monkeypatch, ["Alice set a budget."], [_embed("Northwind")])
    assert _entity_names(_) == {"Northwind"}, "the fixture really did extract"
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
