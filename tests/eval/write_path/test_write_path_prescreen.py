"""#5106 — the deterministic first-stage pre-screen for the banded judge.

Hermetic unit/contract layer over ``prescreen`` (the seam + the two stages),
its wiring into ``judge.BandedSalienceJudge.judge_units``, the runner's
additive receipt block, and the ``prescreen-audit`` measurement.  No
DB/network/LLM and **no model download**: the NLI stage is always an injected
fake scorer, and the real loader is never exercised here.

The load-bearing properties asserted:

* the stage is OFF by default and, with it off, a unit record is byte-identical
  to the #5085 shape (no ``source`` / ``prescreen`` keys);
* it FAILS OPEN — abstain, a raising stage, and an unavailable model all leave
  the unit on the LLM path; nothing is ever dropped;
* the §J4 blindness guard runs over the FULL probe dict BEFORE the stage, so a
  pre-screen is not an exemption from judge-blindness;
* ``likely_not`` requires a CONTRADICTION verdict, never merely the absence of
  entailment (the #5106 design correction — NEUTRAL must not be laundered into
  a confident band);
* ``same_fact`` requires the deterministic claim-critical guard as well as the
  model verdict — a reworded number cannot be short-circuited;
* the save is counted with the judge's real BATCHING rule (0 calls for a
  partial screen; orders × samples for a fully screened session);
* no dependency was added to the package metadata.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.eval.write_path import judge, prescreen, runner

REPO_ROOT = Path(__file__).resolve().parents[3]


# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeNli:
    """Directional NLI double: only the note→probe direction can entail.

    ``note_entails`` is the premise/hypothesis direction that MEANS
    preservation (the memory states the claim).  A double that scores the
    reverse direction instead must not produce ``same_fact`` — that is the
    direction test.
    """

    def __init__(
        self,
        *,
        entail: float = 0.0,
        contradict: float = 0.0,
        note_entails: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self.entail = entail
        self.contradict = contradict
        self.note_entails = note_entails
        self.raises = raises
        self.pairs: list[tuple[str, str]] = []

    def probabilities(self, pairs):
        self.pairs.extend(pairs)
        if self.raises is not None:
            raise self.raises
        rows = []
        for premise, _hypothesis in pairs:
            is_note = premise.startswith("NOTE:")
            credited = is_note if self.note_entails else not is_note
            rows.append({
                "contradiction": self.contradict if credited else 0.0,
                "entailment": self.entail if credited else 0.0,
                "neutral": 0.0,
            })
        return rows


class _FakeScreen:
    """Stage double: a table of verdicts, with optional per-call explosion."""

    name = "fake"

    def __init__(self, verdicts: dict[str, str], *, raises: Exception | None = None):
        self.verdicts = verdicts
        self.raises = raises
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def available(self) -> bool:
        return True

    def audit(self) -> dict:
        return {"name": self.name}

    def screen(self, probe: str, notes) -> str:
        self.calls.append((probe, tuple(notes)))
        if self.raises is not None:
            raise self.raises
        return self.verdicts.get(probe, prescreen.ABSTAIN)


class _AlwaysPreserved:
    """LLM double: marks every probe PRESERVED, records its prompts."""

    def __init__(self, *, temperature: float = 0.0) -> None:
        self.prompts: list[str] = []
        self.temperature = temperature

    def complete(self, *, system: str, user: str) -> str:
        import re

        self.prompts.append(user)
        if "Write the paraphrase probe" in user:
            return '{"paraphrase": "a neutral paraphrase of the planted claim"}'
        ids = re.findall(r"^(\w+): ", user, flags=re.MULTILINE)
        return json.dumps({pid: "PRESERVED" for pid in sorted(set(ids))})


def _arm(model, **kwargs) -> judge.BandedSalienceJudge:
    return judge.BandedSalienceJudge(model_factory=lambda _name: model, **kwargs)


# ── The seam ───────────────────────────────────────────────────────────────


def test_off_resolves_to_no_stage_and_unknown_is_refused():
    assert prescreen.build_prescreen(prescreen.PRESCREEN_OFF) is None
    assert prescreen.PRESCREEN_OFF in prescreen.PRESCREEN_NAMES
    assert prescreen.PRESCREEN_LEXICAL in prescreen.PRESCREEN_NAMES
    assert prescreen.PRESCREEN_NLI in prescreen.PRESCREEN_NAMES
    with pytest.raises(ValueError):
        prescreen.build_prescreen("gpt-nli")


def test_screen_verdicts_reuse_the_judge_band_vocabulary():
    """One declaration of the owner bands — the screen must not fork them."""
    assert prescreen.SAME_FACT == judge.BAND_SAME_FACT
    assert prescreen.LIKELY_NOT == judge.BAND_LIKELY_NOT
    assert prescreen.ABSTAIN not in judge.BAND_ORDER


def test_prescreen_import_is_hermetic_and_needs_no_model_stack():
    """Importing the module must not pull torch/transformers — the whole point."""
    code = (
        "import sys;"
        "import tests.eval.write_path.prescreen as p;"
        "assert 'torch' not in sys.modules, 'torch imported';"
        "assert 'sentence_transformers' not in sys.modules, 'st imported';"
        "assert p.build_prescreen('lexical').available() is True;"
        "print('hermetic')"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert "hermetic" in done.stdout


def test_prescreen_is_registered_by_runner_and_cli():
    """The runner exposes the seam; the CLI name is where the operator meets it."""
    source = inspect.getsource(runner._main)
    assert "--prescreen" in source
    assert "prescreen-audit" in source


# ── The lexical stage ──────────────────────────────────────────────────────


def test_lexical_short_circuits_a_near_verbatim_restatement():
    stage = prescreen.LexicalPreScreen()
    probe = "the migration moved the audit table to the secondary cluster"
    note = "the migration moved the audit table to the secondary cluster twice"
    assert stage.screen(probe, [note]) == prescreen.SAME_FACT
    assert stage.audit()["same_fact"] == 1


def test_lexical_abstains_on_a_genuine_paraphrase():
    """Low overlap is not evidence of loss — the stage is not allowed to guess."""
    stage = prescreen.LexicalPreScreen()
    probe = "the quarry backfill stalled on a duplicate ingest batch"
    note = "two writers double-submitted the replay job and it wedged"
    assert stage.screen(probe, [note]) == prescreen.ABSTAIN
    assert stage.audit()["abstained"] == 1


def test_spelled_out_numerals_are_claim_critical():
    """The gold uses word numerals; a digit-only guard is vacuous on them.

    18 of the 90 verbatim anchors carry a spelled-out numeral and NO digit
    ("batches of five thousand events each", "a sixty second backoff"), so a
    guard that only looked for ``str.isdigit()`` was blind on exactly the
    number-critical class it exists to protect.
    """
    for text in (
        "batches of five thousand events each",
        "retry the batch after a sixty second backoff",
        "four workers on the wire queue",
    ):
        assert prescreen.claim_critical_tokens(text), text
    stage = prescreen.LexicalPreScreen()
    # a changed WORD numeral is blocked exactly like a changed digit
    assert stage.screen(
        "batches of five thousand events each",
        ["batches of fifty thousand events each"],
    ) == prescreen.ABSTAIN
    assert stage.screen(
        "batches of 5000 events each", ["batches of 50000 events each"]
    ) == prescreen.ABSTAIN


def test_lexical_abstains_when_the_note_appends_an_unrelated_negation():
    """A note that RESTATES the claim plus a negation is not a contradiction.

    ``negation_parity`` reads the whole note, so an appended unrelated negation
    flips the note-wide parity; without the same-claim check the note's own
    restatement of the fact would be recorded as LOST.
    """
    stage = prescreen.LexicalPreScreen()
    assert stage.screen(
        "the batch locked the lease rows for the rollout",
        ["the batch locked the lease rows for the rollout and no errors occurred"],
    ) == prescreen.ABSTAIN
    assert stage.screen(
        "the migration removed the stale rows from the cluster",
        ["the migration removed the stale rows from the cluster and disabled "
         "the index"],
    ) == prescreen.ABSTAIN
    # …while the real polarity flip of the SAME claim still fires
    assert stage.screen(
        "the batch locked the lease rows for the rollout",
        ["the batch did not lock the lease rows for the rollout"],
    ) == prescreen.LIKELY_NOT


def test_lexical_stem_collision_cannot_let_an_appended_negation_through():
    """A prefix rule excused ``rollback`` (shares ``roll`` with ``rollout``).

    That excusal put the failure back in the HARMFUL direction: the note
    restates the claim verbatim, appends an unrelated negation, and the screen
    recorded a preserved fact as LOST.  The allowance is now a real inflection
    test (same word + a known suffix), not a prefix test.
    """
    stage = prescreen.LexicalPreScreen()
    probe = "the batch locked the lease rows for the rollout"
    for appended in ("no rollback", "no release-blocker", "no incidentals",
                     "no testing", "no mission"):
        assert stage.screen(probe, [f"{probe}, {appended}"]) == prescreen.ABSTAIN, appended
    # derivational endings form a DIFFERENT lexeme — forgiving them re-opened
    # the hole ("mission" is not "miss")
    assert stage.screen(
        "the crew did miss the lease rows for the rollout",
        ["the crew did miss the lease rows for the rollout, no mission"],
    ) == prescreen.ABSTAIN
    # …and the legitimate inflection is still forgiven
    assert prescreen._is_inflection("lock", "locked")
    assert prescreen._is_inflection("rows", "rows")
    assert not prescreen._is_inflection("rollback", "rollout")
    assert not prescreen._is_inflection("release", "release-blocker")
    assert not prescreen._is_inflection("mission", "miss")
    assert not prescreen._is_inflection("action", "act")


def test_lexical_same_fact_is_blocked_when_critical_spans_are_permuted():
    """A set test cannot see a permutation, and a permutation reverses meaning.

    Swapping two entities (or two numbers) satisfies containment, the
    probe→note critical subset, the no-added-critical check and parity — while
    stating a different claim.  The comparison is therefore on the SEQUENCE.
    """
    stage = prescreen.LexicalPreScreen()
    assert stage.screen(
        "Maya handed the rollout to Priya after the freeze",
        ["Priya handed the rollout to Maya after the freeze"],
    ) == prescreen.ABSTAIN
    assert stage.screen(
        "Maya owns 20 percent and Priya owns 30 percent of the rollout",
        ["Maya owns 30 percent and Priya owns 20 percent of the rollout"],
    ) == prescreen.ABSTAIN
    # the un-permuted restatement is still confident
    assert stage.screen(
        "Maya handed the rollout to Priya after the freeze",
        ["Maya handed the rollout to Priya after the freeze"],
    ) == prescreen.SAME_FACT


def test_lexical_same_fact_is_blocked_when_the_note_extends_a_word_numeral():
    """A subset test is not enough for composed word numerals.

    ``twenty seconds`` → ``twenty five seconds`` adds a critical token without
    changing any probe token, so probe→note passes while the VALUE changed.
    """
    stage = prescreen.LexicalPreScreen()
    assert stage.screen(
        "the job slept twenty seconds before the check",
        ["the job slept twenty five seconds before the check"],
    ) == prescreen.ABSTAIN
    # the atomic-digit control behaves the same way
    assert stage.screen(
        "the job slept 20 seconds before the check",
        ["the job slept 25 seconds before the check"],
    ) == prescreen.ABSTAIN
    # an unchanged restatement is still confident
    assert stage.screen(
        "the job slept twenty seconds before the check",
        ["the job slept twenty seconds before the check"],
    ) == prescreen.SAME_FACT


def test_lexical_abstains_when_the_probe_is_too_short_to_screen():
    stage = prescreen.LexicalPreScreen()
    assert stage.screen("latency dropped", ["latency dropped"]) == prescreen.ABSTAIN


def test_lexical_short_circuits_an_explicit_polarity_contradiction():
    stage = prescreen.LexicalPreScreen()
    probe = "the duplicate batch locked the lease rows for the rollout"
    note = "the duplicate batch did not lock the lease rows for the rollout"
    assert stage.screen(probe, [note]) == prescreen.LIKELY_NOT
    assert stage.audit()["likely_not"] == 1


def test_lexical_same_fact_requires_the_claim_critical_guard():
    """Same words, CHANGED number: not confident — NLI-style misses live here."""
    stage = prescreen.LexicalPreScreen()
    probe = "the release was frozen for 14 days before the change window"
    note = "the release was frozen for 30 days before the change window"
    assert stage.screen(probe, [note]) == prescreen.ABSTAIN
    assert stage.audit()["same_fact"] == 0


def test_lexical_negation_parity_is_read_from_the_raw_note():
    """A set-collapsed note would invert a doubled negation's parity."""
    assert prescreen.negation_parity("not a not b") == 0
    assert prescreen.negation_parity("not a not b never c") == 1


def test_lexical_contracted_negation_is_a_contradiction_not_a_restatement():
    """A contraction must stay ONE token, or the polarity flip is invisible.

    If ``won't`` tokenized as ``won``+``t``, its cue could never match, both
    parities would read 0, containment would be 1.0, and the stage would return
    ``same_fact`` for a note that CONTRADICTS the probe — exactly the
    laundering the stage exists to prevent.
    """
    stage = prescreen.LexicalPreScreen()
    probe = "the batch will lock the lease rows for the rollout"
    assert stage.screen(
        probe, ["the batch won't lock the lease rows for the rollout"]
    ) == prescreen.LIKELY_NOT
    # the uncontracted control must read the same way
    assert stage.screen(
        probe, ["the batch will not lock the lease rows for the rollout"]
    ) == prescreen.LIKELY_NOT
    # and the curly apostrophe the corpus mixes in is normalized to one token
    assert prescreen.tokens("won\u2019t") == prescreen.tokens("won't") == ["won't"]


def test_claim_critical_guard_sees_a_sentence_initial_proper_noun():
    """A name that opens a sentence is still claim-critical.

    Dropping each sentence's first capitalized token made ``Maya`` look like
    sentence case, blinding the guard to an entity swap at the start of a
    sentence — the corruption class the guard exists to catch.
    """
    assert "maya" in prescreen.entity_tokens("Maya is the natural driver for the flip")
    assert "maya" in prescreen.entity_tokens("the flip is driven by Maya")
    # generic function/role nouns stay excluded, sentence-initial or not
    assert prescreen.entity_tokens("The release was frozen") == set()
    assert "note" not in prescreen.entity_tokens("Note the driver change")


def test_lexical_same_fact_is_blocked_when_a_sentence_initial_entity_changes():
    """The guard, not luck, is what stops this short-circuit."""
    stage = prescreen.LexicalPreScreen()
    probe = "Maya owns the rollout plan for the quarter"
    note = "Priya owns the rollout plan for the quarter"
    # the guard is the blocker: the swapped name is claim-critical and absent
    assert not (
        prescreen.claim_critical_tokens(probe) <= set(prescreen.tokens(note))
    )
    assert stage.screen(probe, [note]) == prescreen.ABSTAIN


def test_lexical_same_fact_fires_on_a_byte_identical_restatement_with_critical_spans():
    """The guard's vocabulary must match ``tokens()`` or it abstains on a COPY.

    Spans extracted with their own regex (``Priya.`` → ``priya``, ``3am.`` →
    ``3``) are not tokens, so the guard could never pass and the stage's only
    valuable end was unreachable for the 42 of 270 measured probes that carry
    such a span.  That bug was fail-OPEN — it forced abstains, never a wrong
    band — but it meant the deterministic consent the docstring promises was
    not actually running.
    """
    stage = prescreen.LexicalPreScreen()
    for probe in (
        "The registry service is owned by Priya.",
        "The batch locked the rows since 3am.",
        "Coverage reached the 95th percentile after the rebuild.",
        "Maya\u2019s plan froze the rollout for 14 days.",
    ):
        critical = prescreen.claim_critical_tokens(probe)
        assert critical, probe
        assert critical <= set(prescreen.tokens(probe)), probe
        assert stage.screen(probe, [probe]) == prescreen.SAME_FACT, probe
    # The invariant must hold BY CONSTRUCTION, including for the codepoints
    # whose ``str.lower()`` changes shape (U+0130 'İ' — the one realistic case
    # where a case-insensitive scan and a lowercase-first scan disagree, so a
    # critical token `tokens()` would never emit is dropped rather than forcing
    # a spurious abstain).
    for probe in ("The \u0130stanbul plan froze", "The \u0130zmir rollout stalled"):
        assert prescreen.claim_critical_tokens(probe) <= set(prescreen.tokens(probe)), probe
        assert stage.screen(probe, [probe]) == prescreen.SAME_FACT, probe


def test_lexical_screen_never_raises_on_a_malformed_note_list():
    stage = prescreen.LexicalPreScreen()
    # ``None`` entries are not notes; the stage must still abstain, not crash.
    assert stage.screen("a probe with enough content tokens", [None]) in (
        prescreen.ABSTAIN, prescreen.SAME_FACT, prescreen.LIKELY_NOT
    )


# ── The NLI stage (fake scorer — no model is ever downloaded) ──────────────


def _nli(**kwargs) -> prescreen.NliPreScreen:
    scorer = kwargs.pop("scorer")
    return prescreen.NliPreScreen(scorer=scorer, **kwargs)


def test_nli_short_circuits_same_fact_on_a_confident_entailment():
    scorer = _FakeNli(entail=0.99, contradict=0.0)
    stage = _nli(scorer=scorer)
    assert stage.available() is True
    assert stage.screen("the batch locked the rows", ["NOTE: the batch locked the rows"]) \
        == prescreen.SAME_FACT


def test_nli_reads_entailment_in_the_note_to_probe_direction_only():
    """A memory note that the PROBE entails is not a preserved claim."""
    reversed_only = _FakeNli(entail=0.99, contradict=0.0, note_entails=False)
    stage = _nli(scorer=reversed_only)
    assert stage.screen("the batch locked the rows", ["NOTE: some unrelated text"]) \
        == prescreen.ABSTAIN
    # and the forward pair really was asked premise=note, hypothesis=probe
    assert all(p[0].startswith("NOTE:") for p in reversed_only.pairs[::2])


def test_nli_likely_not_requires_a_contradiction_not_merely_low_entailment():
    """NEUTRAL (entail 0, contradict 0) must NOT be laundered into likely_not."""
    neutral = _FakeNli(entail=0.0, contradict=0.0)
    assert _nli(scorer=neutral).screen("probe", ["NOTE: a note"]) == prescreen.ABSTAIN
    contradicting = _FakeNli(entail=0.0, contradict=0.99)
    assert _nli(scorer=contradicting).screen("probe", ["NOTE: a note"]) \
        == prescreen.LIKELY_NOT


def test_nli_same_fact_still_requires_the_claim_critical_guard():
    """A confident entailment over a CHANGED number is not short-circuited."""
    scorer = _FakeNli(entail=0.99, contradict=0.0)
    stage = _nli(scorer=scorer)
    assert stage.screen(
        "the release froze for 14 days",
        ["NOTE: the release froze for 30 days"],
    ) == prescreen.ABSTAIN


def test_nli_abstains_when_there_are_no_notes():
    scorer = _FakeNli(entail=0.99, contradict=0.0)
    assert _nli(scorer=scorer).screen("probe words here", []) == prescreen.ABSTAIN


def test_nli_fails_open_when_the_extra_is_missing(monkeypatch):
    """The declared `embeddings` extra absent ⇒ unavailable, every unit abstains."""
    stage = prescreen.NliPreScreen()

    def _boom(_name):
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(prescreen, "_CrossEncoderScorer", _boom)
    assert stage.available() is False
    assert stage.screen("probe words here", ["a note"]) == prescreen.ABSTAIN
    audit = stage.audit()
    assert audit["available"] is False
    assert audit["unavailable"] >= 1
    assert "sentence_transformers" in audit["load_error"]
    assert "fail-open" in audit["note"]


def test_nli_fails_open_when_the_scorer_raises():
    stage = _nli(scorer=_FakeNli(raises=RuntimeError("cuda oom")))
    assert stage.screen("probe words here", ["a note"]) == prescreen.ABSTAIN
    assert stage.audit()["errors"] == 1


def test_nli_stage_declares_no_new_dependency():
    """The stage must name the ALREADY-declared extra, never a new one."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "nli" not in pyproject
    assert "onnx" not in pyproject
    source = inspect.getsource(prescreen)
    assert "from sentence_transformers import CrossEncoder" in source
    # torch/transformers arrive through that extra's own metadata — the module
    # must not declare (or import at module scope) either one.
    assert "import torch" in source  # lazy, inside the scorer's methods


def test_the_measured_verdict_travels_with_the_artifact():
    """The stage's own measurement must be visible wherever it could be enabled.

    A reader of a receipt or an audit report — not only of this module — must be
    able to see that the stage was measured and that enabling it is not
    recommended.  Uses an injected scorer so no model is ever loaded (the real
    loader would attempt a download in a dev env with the extra installed).
    """
    stage = _nli(scorer=_FakeNli(entail=0.0, contradict=0.0))
    audit = stage.audit()
    assert audit["recommended"] is False
    assert "NOT RECOMMENDED" in audit["measured"]
    assert "same_fact" in audit["measured"]
    # Stable markers only — the numeric measurement is deliberately NOT pinned
    # here: re-measuring (the whole point of this instrument) updates the
    # constant, and a pinned number would redden CI until this test was edited
    # too.  The verdict that travels is `recommended`, not a point-in-time stat.
    assert "measured" in audit
    # …and it rides into an audit report, not just the stage object.
    report = prescreen.audit_receipt(
        _semantic_block(
            [{"session_id": "wp01", "unit_id": "u1",
              "probe": "a probe", "probability": 1.0}],
            {"wp01": ["a note"]},
        ),
        stage,
    )
    assert report["screen_audit"]["recommended"] is False
    assert report["screen_audit"]["measured"] == audit["measured"]


# ── Wiring into judge_units ────────────────────────────────────────────────


def test_screen_off_keeps_the_unit_record_identical_to_5085():
    arm = _arm(_AlwaysPreserved(), samples=1, orders=("probes_first",))
    units = arm.judge_units({"u1": "a probe"}, ["a note"])
    assert set(units["u1"]) == {
        "probability", "band", "votes_yes", "votes_total", "orders",
    }
    assert arm.prescreen_audit() is None


def test_screen_off_prompt_is_byte_identical_to_the_5085_prompt():
    """The OFF path must not re-order the probe block it sends the LLM.

    ``build_banded_prompt`` renders the PROBES block with ``probes.items()``,
    and the gold's unit order is NOT sorted for 3 of the 7 corpus sessions.  So
    drawing the active set from the SORTED id list silently changed the judge
    prompt — and therefore the judged number — on the very path that is
    supposed to be untouched, breaking the #5085 comparability the pin
    discipline exists to protect.  This is the regression guard.
    """
    probes = {"u_10": "ten", "u_02": "two", "u_07": "seven"}
    memory = ["a note"]
    assert list(probes) != sorted(probes), "fixture must be unsorted to bite"
    for order in judge.PROMPT_ORDERS:
        model = _AlwaysPreserved()
        arm = _arm(model, samples=1, orders=(order,))
        arm.judge_units(probes, memory)          # screen OFF — the default
        assert model.prompts == [
            judge.build_banded_prompt(probes, memory, order=order)
        ], order

    # A PARTIAL screen must keep the SURVIVORS' relative order too.
    model = _AlwaysPreserved()
    arm = _arm(model, samples=1, orders=("probes_first",))
    arm.judge_units(
        probes, memory, prescreen=_FakeScreen({"two": prescreen.SAME_FACT})
    )
    assert model.prompts == [
        judge.build_banded_prompt(
            {"u_10": "ten", "u_07": "seven"}, memory, order="probes_first"
        )
    ]


def test_screened_unit_skips_the_llm_entirely_and_takes_the_owner_band():
    model = _AlwaysPreserved()
    screen = _FakeScreen({"u1 probe text": prescreen.SAME_FACT})
    arm = _arm(model, samples=2, orders=("probes_first",))
    units = arm.judge_units({"u1": "u1 probe text"}, ["a note"], prescreen=screen)
    record = units["u1"]
    assert record["probability"] == 1.0
    assert record["band"] == judge.BAND_SAME_FACT
    assert record["source"] == "prescreen"
    assert record["prescreen"] == {"name": "fake", "verdict": "same_fact"}
    assert record["votes_total"] == 0
    assert model.prompts == []           # the LLM was never asked
    assert arm.judge_call_count == 0
    assert arm.prescreen_audit()["judge_calls_avoided"] == 2   # 1 order × 2 samples


def test_likely_not_short_circuit_takes_the_bottom_band():
    screen = _FakeScreen({"a probe": prescreen.LIKELY_NOT})
    arm = _arm(_AlwaysPreserved(), samples=1, orders=("probes_first",))
    units = arm.judge_units({"u1": "a probe"}, ["note"], prescreen=screen)
    assert units["u1"]["probability"] == 0.0
    assert units["u1"]["band"] == judge.BAND_LIKELY_NOT


def test_abstain_is_judged_by_the_llm():
    screen = _FakeScreen({})           # every probe abstains
    model = _AlwaysPreserved()
    arm = _arm(model, samples=1, orders=("probes_first",))
    units = arm.judge_units({"u1": "a probe"}, ["note"], prescreen=screen)
    assert units["u1"]["band"] == judge.BAND_SAME_FACT
    assert units["u1"]["source"] == "judge"
    assert model.prompts, "an abstaining screen must still reach the LLM judge"


def test_a_raising_screen_fails_open_to_the_llm():
    screen = _FakeScreen({}, raises=RuntimeError("screen exploded"))
    model = _AlwaysPreserved()
    arm = _arm(model, samples=1, orders=("probes_first",))
    units = arm.judge_units({"u1": "a probe"}, ["note"], prescreen=screen)
    assert units["u1"]["source"] == "judge"
    assert units["u1"]["band"] == judge.BAND_SAME_FACT
    assert arm.prescreen_audit()["screen_errors"] == 1


def test_partial_screen_saves_no_calls_but_a_full_screen_does():
    """The judge batches one call per (order, sample) per session."""
    partial = _FakeScreen({"p1": prescreen.SAME_FACT})     # p2 abstains
    arm = _arm(_AlwaysPreserved(), samples=2, orders=judge.PROMPT_ORDERS)
    arm.judge_units({"u1": "p1", "u2": "p2"}, ["note"], prescreen=partial)
    assert arm.prescreen_audit()["judge_calls_avoided"] == 0
    assert arm.judge_call_count == 4                       # 2 orders × 2 samples

    full = _FakeScreen({"p1": prescreen.SAME_FACT, "p2": prescreen.SAME_FACT})
    arm2 = _arm(_AlwaysPreserved(), samples=2, orders=judge.PROMPT_ORDERS)
    arm2.judge_units({"u1": "p1", "u2": "p2"}, ["note"], prescreen=full)
    assert arm2.prescreen_audit()["judge_calls_avoided"] == 4
    assert arm2.judge_call_count == 0


def test_screened_units_are_banded_by_the_unchanged_owner_table():
    """A screened run still aggregates through the ONE band mapping."""
    screen = _FakeScreen({"a": prescreen.SAME_FACT, "b": prescreen.LIKELY_NOT})
    arm = _arm(_AlwaysPreserved(), samples=1, orders=("probes_first",))
    units = arm.judge_units({"u1": "a", "u2": "b"}, ["note"], prescreen=screen)
    aggregate = judge.aggregate_banded([{"unit_id": k, **v} for k, v in units.items()])
    assert aggregate["band_distribution"] == {
        "same_fact": 1, "likely": 0, "maybe": 0, "likely_not": 1,
    }
    assert aggregate["band_weighted_score"] == round((0.90 + 0.25) / 2, 6)


# ── Blindness is not exempted ──────────────────────────────────────────────


def test_blindness_guard_still_fires_through_the_screen():
    """The guard runs over the FULL probe dict BEFORE the stage is consulted."""
    anchor = "the quarry backfill stalled on a duplicate ingest batch"
    screen = _FakeScreen({anchor: prescreen.SAME_FACT})
    arm = _arm(
        _AlwaysPreserved(),
        samples=1,
        orders=("probes_first",),
        anchors=[anchor],
    )
    with pytest.raises(judge.JudgeBlindnessError):
        arm.judge_units({"u1": anchor}, ["some note"], prescreen=screen)
    assert screen.calls == [], "the screen must not run before the blindness guard"


# ── The measurement ────────────────────────────────────────────────────────


def _semantic_block(units, memory, *, samples=5, orders=("probes_first", "memory_first")):
    return {
        "units": units,
        "memory_by_session": memory,
        "samples": samples,
        "orders": list(orders),
    }


def test_audit_reports_reduction_and_agreement_against_the_judge():
    claim = "the duplicate batch will lock the lease rows for the rollout"
    negated = "the duplicate batch will not lock the lease rows for the rollout"
    block = _semantic_block(
        [
            {"session_id": "wp01", "unit_id": "u1",
             "probe": claim, "probability": 1.0},
            {"session_id": "wp02", "unit_id": "u2",
             "probe": claim, "probability": 0.0},
        ],
        # wp01's memory states the claim; wp02's memory contradicts it.
        {"wp01": [claim], "wp02": [negated]},
        samples=2,
        orders=("probes_first",),
    )
    report = prescreen.audit_receipt(block, prescreen.LexicalPreScreen())
    assert report["units_total"] == 2
    assert report["units_screened"] == 2
    assert report["screened_same_fact"] == 1
    assert report["screened_likely_not"] == 1
    assert report["judge_calls_per_session"] == 2           # 1 order × 2 samples
    assert report["sessions_total"] == 2
    assert report["sessions_fully_screened"] == 2
    assert report["judge_calls_saved"] == 4                 # 2 sessions × 2 calls
    assert report["judge_calls_total_without_screen"] == 4
    assert report["paraphrase_calls_saved"] == 0
    assert report["agreement"] == 1.0
    assert report["confusion"] == {"same_fact": {"same_fact": 1},
                                   "likely_not": {"likely_not": 1}}


def test_audit_reports_the_disagreement_that_is_the_accuracy_loss():
    """A screen that calls a fact preserved when the judge says gone is visible."""
    verbatim = "the duplicate batch will lock the lease rows for the rollout"
    paraphrase = "two writers double-submitted the replay job and it wedged"
    block = _semantic_block(
        [
            {"session_id": "wp01", "unit_id": "u1",
             "probe": verbatim, "probability": 0.0},
            {"session_id": "wp01", "unit_id": "u2",
             "probe": paraphrase, "probability": 1.0},
        ],
        {"wp01": [verbatim]},
        samples=2,
        orders=("probes_first",),
    )
    report = prescreen.audit_receipt(block, prescreen.LexicalPreScreen())
    assert report["units_screened"] == 1
    assert report["agreement"] == 0.0
    assert report["disagreements"][0]["screen_band"] == prescreen.SAME_FACT
    assert report["disagreements"][0]["judge_band"] == judge.BAND_LIKELY_NOT
    # A PARTIAL screen removes tokens, not calls — never invent a saving.
    assert report["judge_calls_saved"] == 0
    assert report["probe_chars_removed"] > 0


def test_audit_excludes_units_the_receipt_already_screened():
    """A screened receipt's band IS the screen's verdict — never a reference.

    Scoring the re-run against those units compares the screen WITH ITSELF and
    reports a false 100%, erasing exactly the accuracy loss this tool exists to
    measure.
    """
    claim = "the duplicate batch will lock the lease rows for the rollout"
    negated = "the duplicate batch will not lock the lease rows for the rollout"
    block = _semantic_block(
        [
            # Already short-circuited by a pre-screen in the receipt: excluded.
            {"session_id": "wp01", "unit_id": "u1", "probe": claim,
             "probability": 1.0, "source": "prescreen"},
            # Judged by the LLM: the screen disagrees with it, and that is the
            # accuracy loss the report must still show.
            {"session_id": "wp02", "unit_id": "u2", "probe": claim,
             "probability": 1.0},
        ],
        {"wp01": [claim], "wp02": [negated]},
        samples=2,
        orders=("probes_first",),
    )
    report = prescreen.audit_receipt(block, prescreen.LexicalPreScreen())
    assert report["units_screened"] == 2
    assert report["receipt_pre_screened_units"] == 1
    assert report["reference_screened_excluded"] == 1
    assert report["agreement_denominator"] == 1
    # NOT 1.0 — the self-comparison is removed and the real miss is reported.
    assert report["agreement"] == 0.0
    assert report["agreement_n"] == 0
    assert report["reference_note"] and "false 100%" in report["reference_note"]


def test_receipt_screen_errors_includes_the_stage_own_counter(monkeypatch):
    """The receipt must not report 0 errors while the stage errored internally.

    Both built-in stages fail OPEN *inside* ``screen()``, so the arm's escape
    counter stays 0 on a run where the stage errored on every unit.  Reporting
    only that counter makes the receipt's fail-open audit self-contradictory.
    """
    session_id = "wp01_quarry_debug"

    class _InternallyFailing:
        name = "internally-failing"

        def available(self):
            return True

        def screen(self, probe, notes):
            return prescreen.ABSTAIN

        def audit(self):
            return {"name": self.name, "available": True, "errors": 7,
                    "unavailable": 0, "abstained": 0, "screened": 0}

    monkeypatch.setattr(
        prescreen, "build_prescreen", lambda name, **kw: _InternallyFailing()
    )
    monkeypatch.setattr(
        runner, "snapshot_session",
        lambda _sdk, _sid: {"points": [{"point_id": "p1", "content": "a note"}],
                            "rephrase_edges": []},
    )
    block = runner._run_semantic_judge(
        None, [session_id], runner.corpus.WRITE_PATH_DIR,
        samples=2,
        judge_factory=lambda _name: _AlwaysPreserved(),
        prescreen=prescreen.PRESCREEN_LEXICAL,
    )
    # The arm's own escape counter really is 0 — the sum is what must be right.
    assert block["prescreen"]["screen_errors"] == 7
    assert block["prescreen"]["stage"]["errors"] == 7


def test_audit_of_an_unloadable_stage_reports_zero_coverage_not_a_pass():
    stage = prescreen.NliPreScreen()

    class _Boom(prescreen.NliPreScreen):
        def _load(self):
            self._load_error = "ImportError: no sentence_transformers"
            return None

    _ = stage
    report = prescreen.audit_receipt(
        _semantic_block(
            [{"session_id": "wp01", "unit_id": "u1", "probe": "a probe",
              "probability": 1.0}],
            {"wp01": ["a note"]},
        ),
        _Boom(),
    )
    assert report["units_screened"] == 0
    assert report["units_screened_fraction"] == 0.0
    assert report["agreement"] is None
    assert report["judge_calls_saved"] == 0


# ── Runner wiring ──────────────────────────────────────────────────────────


def test_run_benchmark_refuses_a_prescreen_without_the_judged_arm():
    with pytest.raises(ValueError, match="requires --judge semantic"):
        runner.run_benchmark(prescreen=prescreen.PRESCREEN_LEXICAL)
    with pytest.raises(ValueError, match="unknown prescreen"):
        runner.run_benchmark(prescreen="gpt-nli")


def test_run_semantic_judge_records_the_block_and_repins(monkeypatch):
    session_id = "wp01_quarry_debug"
    note = "a note retained by the write path"
    monkeypatch.setattr(
        runner, "snapshot_session",
        lambda _sdk, _sid: {"points": [{"point_id": "p1", "content": note}],
                            "rephrase_edges": []},
    )
    block = runner._run_semantic_judge(
        None, [session_id], runner.corpus.WRITE_PATH_DIR,
        samples=2,
        judge_factory=lambda _name: _AlwaysPreserved(),
        prescreen=prescreen.PRESCREEN_LEXICAL,
    )
    assert block["status"] == "completed"
    assert block["pin"] == judge.SEMANTIC_JUDGE_PIN_PRESCREEN
    assert block["pin"] != judge.SEMANTIC_JUDGE_PIN
    assert "prescreen" in block
    assert block["prescreen"]["name"] == prescreen.PRESCREEN_LEXICAL
    assert block["prescreen"]["experimental"] is True
    assert block["prescreen"]["units_total"] == len(block["units"])
    assert (
        block["prescreen"]["units_screened"] + block["prescreen"]["abstained"]
        == len(block["units"])
    )
    # Every unit is still accounted for and the aggregate is intact.
    assert block["units_total"] == len(block["units"])


def test_run_semantic_judge_off_keeps_the_5085_shape(monkeypatch):
    session_id = "wp01_quarry_debug"
    monkeypatch.setattr(
        runner, "snapshot_session",
        lambda _sdk, _sid: {"points": [{"point_id": "p1", "content": "a note"}],
                            "rephrase_edges": []},
    )
    block = runner._run_semantic_judge(
        None, [session_id], runner.corpus.WRITE_PATH_DIR,
        samples=2,
        judge_factory=lambda _name: _AlwaysPreserved(),
    )
    assert block["pin"] == judge.SEMANTIC_JUDGE_PIN
    assert "prescreen" not in block
    assert all("source" not in unit for unit in block["units"])


def test_prescreen_audit_cli_fails_closed_when_the_stage_cannot_load(
    tmp_path, monkeypatch, capsys
):
    """A vacuous report must NOT exit 0.

    When the stage cannot load, every unit abstains, so the report reads
    ``units_screened=0, agreement=None`` — indistinguishable by exit code from
    a genuine "this stage screens nothing" measurement.  Fail CLOSED on the
    measurement (the tool exists to make this number right).
    """
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"semantic_judge": {
        "status": "completed",
        "units": [{"session_id": "s", "unit_id": "u",
                   "probe": "a probe", "probability": 1.0}],
        "memory_by_session": {"s": ["a note"]},
        "samples": 2,
        "orders": ["probes_first"],
    }}), encoding="utf-8")

    class _Unavailable(prescreen.NliPreScreen):
        def _load(self):
            self._load_error = "ImportError: no module named 'sentence_transformers'"
            return None

    monkeypatch.setattr(
        prescreen, "build_prescreen", lambda name, **kw: _Unavailable()
    )
    code = runner._main([
        "prescreen-audit",
        "--receipt", str(receipt),
        "--prescreen", prescreen.PRESCREEN_NLI,
    ])
    assert code == runner.EXIT_RUNNER_ERROR
    assert "NOT a measurement" in capsys.readouterr().err


def test_prescreen_audit_cli_fails_closed_when_the_stage_errors_on_every_unit(
    tmp_path, monkeypatch, capsys
):
    """Loading is not enough: a stage that raises on every unit is as vacuous.

    ``--prescreen-model`` pointed at an incompatible checkpoint makes
    ``probabilities()`` raise on every call; the stage's own fail-open handler
    swallows it, so ``available`` is True and only the error counter reveals it.
    """
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"semantic_judge": {
        "status": "completed",
        "units": [{"session_id": "s", "unit_id": "u",
                   "probe": "a probe", "probability": 1.0}],
        "memory_by_session": {"s": ["a note"]},
        "samples": 2,
        "orders": ["probes_first"],
    }}), encoding="utf-8")

    class _AlwaysErroring:
        name = "always-erroring"

        def available(self):
            return True

        def screen(self, probe, notes):
            return prescreen.ABSTAIN

        def audit(self):
            return {"name": self.name, "available": True, "errors": 3}

    monkeypatch.setattr(
        prescreen, "build_prescreen", lambda name, **kw: _AlwaysErroring()
    )
    code = runner._main([
        "prescreen-audit",
        "--receipt", str(receipt),
        "--prescreen", prescreen.PRESCREEN_NLI,
        "--json",
    ])
    assert code == runner.EXIT_RUNNER_ERROR
    captured = capsys.readouterr()
    assert "NOT a measurement" in captured.err
    assert "{" not in captured.out          # no JSON on the failure path
    assert "screen_errors=3" in captured.out
