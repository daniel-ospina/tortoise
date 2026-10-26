"""S2.2/S2.3 typed-decision seam (#5087) — five discriminating tests.

**Why the first two.** This seam exists to prevent one defect class: *an answer
that does not fit the schema is trusted anyway*. Both are either "a valid typed
answer produces the expected verdict through the REAL consumers" (so the seam's
shape is the gate's shape, not a private one) or "an out-of-schema answer fails
closed" (the defect the seam prevents). Anything that merely pins the request's
byte layout is a test that cannot fail for a reason we care about — the one
request-shape assertion kept is the ``questions`` **map** (``#7866``): the
documented array form 400s, so that line is the 400 trap, not decoration.

**Why the other three.** Being right and being *measurable* are different
failures, and only one of them reddens a pure-logic test. Arming the seam puts a
billable call on the write path, so the record of it is asserted where a cost
gate reads (``vet_gate``'s ``stats["seam"]``), an injected decider's
unrepresentable spend is asserted not to abort a pass documented as
never-raising, and the per-item degradation map is asserted where the design
record tells an operator to read it. None of the three was reachable from the
first two tests.

**Every test here is mutation-checked**, and the mutant is named in its
docstring. A test that cannot be made red by breaking the guard it claims to
test is not a test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_kind_classifier import (  # noqa: E402, RUF100
    FIXTURE_SPEC,
    KeywordEncoder,
)
from tortoise import s2_decision_seam as seam  # noqa: E402, RUF100
from tortoise import vet_gate  # noqa: E402, RUF100
from tortoise.kind_classifier import KindClassifier  # noqa: E402, RUF100
from tortoise.kind_index import KindIndex, _clear_index_cache  # noqa: E402, RUF100

# ── stubs ──────────────────────────────────────────────────────────────────

def _envelope(answers: dict) -> dict:
    """The endpoint's response envelope — the pinned model and an answers map,
    which is what ``_decide`` validates before any answer is read."""
    return {"model": seam.MODEL, "answers": answers,
            "usage": {"input_tokens": 100, "cost_usd": 0.00004}}


def _transport(answer: dict, seen: list | None = None):
    """A transport stub: one response, and (optionally) the request it saw."""
    def call(payload: dict) -> dict:
        if seen is not None:
            seen.append(payload)
        return _envelope(answer)
    return call


def _vet_embed_list() -> dict:
    return {"points": [
        {"content": "Decided: the cache stays warm across restarts."},
        {"content": "See above."},
    ]}


def _outcomes(out: dict) -> dict:
    return {d["text"]: d for d in out["decisions"].values()}


def _classifier(tail_decider) -> KindClassifier:
    _clear_index_cache()
    return KindClassifier(
        encoder=KeywordEncoder(),
        index=KindIndex.build(FIXTURE_SPEC, encoder=KeywordEncoder(), persist=False),
        model=None,
        llm_tail=True,
        tail_decider=tail_decider,
    )


# ── 1. a valid typed answer produces the expected verdict ───────────────────

def test_a_valid_typed_answer_produces_the_expected_verdict():
    """A real noul + score pair decides KEEP/DISCARD through ``vet_gate``, and a
    real choice decides the kind through ``KindClassifier`` — both through the
    seams that already exist, with no private shape in between.

    Mutant that reddens the second half: make ``classify_tail`` assign the
    offered top-1 instead of the model's choice (the stub answers the kNN's
    SECOND candidate) — the ``core:workflow`` assertion reads ``core:plan``.
    """
    seen: list = []
    arbiter = seam.vet_arbiter(transport=_transport({
        # candidate c0: durable and atomic → KEEP
        "keep_c0": {"type": "noul", "noul": 0.93},
        "atomic_c0": {"type": "score", "score": 3.0},
        # candidate c1: a bare pointer, confidently not a claim, and atomic → DISCARD
        "keep_c1": {"type": "noul", "noul": 0.02},
        "atomic_c1": {"type": "score", "score": 2.7},
    }, seen))
    out = vet_gate.vet_candidates(_vet_embed_list(), narrative="one decision", arbiter=arbiter)
    decisions = _outcomes(out)

    assert decisions["Decided: the cache stays warm across restarts."]["outcome"] == "KEEP"
    assert decisions["See above."]["outcome"] == "DISCARD"
    assert decisions["See above."]["rule_id"] == seam.RULE_SEAM
    assert out["stats"]["by_outcome"] == {"KEEP": 1, "DISCARD": 1}

    # #7866: `questions` MUST be a map — the documented array form 400s — and a
    # score question MUST carry its levels. One line, because a caller that gets
    # this wrong is told by a 400 that blames its own code.
    payload = seen[0]
    assert payload["model"] == seam.MODEL
    assert isinstance(payload["questions"], dict)
    assert payload["questions"]["atomic_c0"]["criteria"] == list(seam.ATOMICITY_LEVELS)
    assert all("instructions" in q for q in payload["questions"].values())

    # S2.3: a `choice` over exactly the kinds the kNN offered. The stub answers
    # `core:workflow`, which is the kNN's SECOND candidate — so the assertion
    # below is the model's answer, not the fast path's, and a mutant that echoed
    # the top-1 would read `core:plan`.
    clf = _classifier(seam.classify_decider(transport=_transport({
        "kind_c0": {"type": "choice", "choice": "core:workflow", "confidence": 0.7},
    }, seen)))
    classified = clf.classify_items(
        [{"id": "i0", "type": "entity", "text": "the plan workflow"}])
    assignment = classified["assignments"]["i0"]
    assert (assignment["kind"], assignment["mode"]) == ("core:workflow", "seam")
    assert classified["stats"]["closed_vocab_rejects"] == 0
    assert classified["stats"]["seam"]["assignments"] == 1
    assert classified["stats"]["seam"]["calls"] == 1
    # The record that makes arming measurable: the VET half carries its spend and
    # its bands out through the gate's own ``stats`` (``stats["seam"]``), and each
    # half names the prompt version that decided, so a prompt change is visible in
    # the artifact rather than inferred from the rollout date.
    assert out["stats"]["seam"]["prompt"] == seam.VET_PROMPT
    assert out["stats"]["seam"]["calls"] == 1
    assert out["stats"]["seam"]["cost_usd"] > 0.0
    assert out["stats"]["seam"]["bands"] == seam.DEFAULT_BANDS.as_record()
    assert classified["stats"]["seam"]["prompt"] == seam.CLASSIFY_PROMPT
    offered = seen[-1]["questions"]["kind_c0"]["criteria"]
    assert set(offered) < set(FIXTURE_SPEC), (
        "the offered set is the kNN candidate list (top-5), not the whole vocabulary")
    assert {"core:plan", "core:workflow"} <= set(offered)


# ── 2. an out-of-schema answer FAILS CLOSED (the point of the seam) ─────────

def test_an_out_of_schema_answer_fails_closed_and_names_the_question(monkeypatch):
    """Three malformed answers, both steps: wrong primitive, a score off the
    declared scale, and a choice outside the offered set. None may become a
    verdict, and the record must name the question that failed. The two
    envelope-level floors ride along, one line each: the seam is not consulted
    unless it is ARMED, and an answer from an unpinned model is not an answer.

    Mutants this reddens, each breaking one guard:
      * ``_noul`` returning ``float(answer.get("noul") or 0.0)`` — a missing
        ``noul`` coerces to 0.0, i.e. a confident DISCARD from a ``choice``
        answer. The "nothing was discarded" assertion fails.
      * ``_score`` dropping its ``0..hi`` range check — 9.0 on a 0..3 scale reads
        as "very atomic" and licenses the DISCARD above. Same assertion.
      * ``_choice`` returning ``answer.get("choice")`` unvalidated — a minted
        ``core:invented`` kind reaches the classifier, ``closed_vocab_rejects``
        becomes 1, and the item is no longer kNN-fallen-back.
    """
    arbiter = seam.vet_arbiter(transport=_transport({
        # c0: answered with the WRONG PRIMITIVE (a choice where a noul was asked)
        "keep_c0": {"type": "choice", "choice": "yes"},
        "atomic_c0": {"type": "score", "score": 3.0},
        # c1: a confident "not a claim", but the score is off the declared scale
        "keep_c1": {"type": "noul", "noul": 0.01},
        "atomic_c1": {"type": "score", "score": 9.0},
    }))
    out = vet_gate.vet_candidates(_vet_embed_list(), narrative="one decision", arbiter=arbiter)
    decisions = _outcomes(out)

    assert "DISCARD" not in out["stats"]["by_outcome"], (
        "an unusable answer must never become a drop — a wrong drop is memory loss")
    degraded = out["stats"]["seam"]["degraded"]
    by_text = {d["text"]: iid for iid, d in out["decisions"].items()}
    for text, question in (("Decided: the cache stays warm across restarts.", "keep_c0"),
                           ("See above.", "atomic_c1")):
        decision = decisions[text]
        # NO verdict (not a NOOP verdict): a verdict word is something the gate
        # APPLIES, so a degraded NOOP would overwrite the mechanical #4899
        # DISCARD — see the composition test below. The item keeps its default.
        assert decision["outcome"] == "KEEP"
        assert decision["rule_id"] == vet_gate.RULE_FAIL_OPEN
        assert question in degraded[by_text[text]], (
            f"the failing question must be named, got {degraded!r}")
    assert out["stats"]["candidates"] == 2  # both candidates reached the gate
    assert out["stats"]["seam"]["schema_rejects"] == ["keep_c0", "atomic_c1"]
    assert out["stats"]["seam"]["by_outcome"] == {}, "a degraded item produced no verdict"

    # S2.3: the model answers with a kind nobody offered.
    clf = _classifier(seam.classify_decider(transport=_transport({
        "kind_c0": {"type": "choice", "choice": "core:invented"},
    })))
    classified = clf.classify_items(
        [{"id": "i0", "type": "entity", "text": "the plan workflow"}])
    assignment = classified["assignments"]["i0"]
    assert assignment["kind"] != "core:invented", "a kind is never minted"
    assert assignment["mode"] == "knn", "the caller's mechanical fast path, not a guess"
    assert classified["stats"]["closed_vocab_rejects"] == 0, (
        "the SEAM rejects it — the downstream closed-vocab guard must never be the "
        "first line of defence, or the seam is trusting whatever comes back")
    assert any("kind_c0" in r for r in classified["stats"]["seam"]["rejected"])

    # The two envelope-level floors: OFF is the production default, and an answer
    # from a model that is not the pin is not an answer.
    assert seam.seam_enabled() is False
    monkeypatch.setenv(seam.ENV_FLAG, "1")
    assert seam.seam_enabled() is True

    # …and the flag must actually REACH the two gates. An armed seam that no
    # call site consults is the inert-enforcer failure class: it reports itself
    # as protection and protects nothing. Both wires are one callable away from
    # the pipeline, so they are asserted here rather than in a pipeline run.
    from tortoise import extractor_v2 as v2

    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
    assert v2._s2_seam_arbiter(None) is None, "flag off ⇒ today's VET (no arbiter)"
    assert v2._s2_seam_tail_decider() is None, "flag off ⇒ the caller's own tail"
    monkeypatch.setenv(seam.ENV_FLAG, "1")
    assert callable(v2._s2_seam_arbiter(None)) and callable(v2._s2_seam_tail_decider())
    injected = object()
    assert v2._s2_seam_arbiter(injected) is injected, "an injected arbiter always wins"

    unpinned = seam.vet_verdicts(
        [{"id": "p0", "text": "a claim"}],
        transport=lambda _payload: {"model": "jev-2", "answers": {}},
    )[0]
    assert [(v["outcome"], v["rule_id"]) for v in unpinned] == [], (
        "an unverified model's answer is no verdict at all, not a NOOP one")

    # ⛔ THE COMPOSITION FLOOR (regression: an armed seam with no usable answer
    # must not RESCIND the mechanical #4899 predicate). `vet_candidates` applies
    # arbiter verdicts over the mechanical pass (``decisions[iid].update(...)``),
    # so a degraded item that emitted a NOOP *verdict* would turn a discard into a
    # keep on a key outage — an outage silently protecting what a working
    # predicate dropped. With the flag armed and the transport raising, the
    # identifier-only candidate must still be DISCARD, with its own rule id.
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    identifier_only = {"points": [{"content": "a1b2c3d4e5f6a7b8c9d0"}]}

    def _down(_payload):
        raise seam.Unavailable("no key")

    out = vet_gate.vet_candidates(identifier_only, narrative="", arbiter=seam.vet_arbiter(
        transport=_down))
    only = next(iter(out["decisions"].values()))
    assert (only["outcome"], only["rule_id"]) == ("DISCARD", "value.identifier_only.hash"), (
        "an unavailable seam must not override the mechanical predicate: "
        f"got {only['outcome']}/{only['rule_id']}")
    assert only["counterfactual"].startswith("would have shipped"), (
        "a discard that survives must stay explainable")
    seam_record = out["stats"]["seam"]
    assert seam_record["unavailable"] == 1 and seam_record["by_outcome"] == {}
    assert seam_record["prompt"] == seam.VET_PROMPT
    monkeypatch.delenv("TORTOISE_VALUE_GATE", raising=False)


# ── 3. the seam's own record survives the boundary into the artifact ────────

def test_the_seam_record_reaches_the_gate_stats():
    """An armed seam is a network call and a SPEND on the write path, so the
    record of it has to exist somewhere a human can read — otherwise the cost
    gate sees $0 for a billable call, which is the inert-enforcer class one
    layer out (it reports itself as measured and measures nothing).

    Both fields asserted here are the ones the design record promises:
    ``prompt`` (which question wording answered) and ``cost_usd`` / ``bands``
    (what the answer was read through).

    Mutant that reddens it: return ``{"verdicts": …, "batch": {}}`` from
    ``seam.vet_arbiter`` — i.e. drop the ``seam`` key — or drop the fold in
    ``vet_gate.vet_candidates``. Either leaves the spend recorded nowhere.
    """
    out = vet_gate.vet_candidates(
        _vet_embed_list(),
        arbiter=seam.vet_arbiter(transport=_transport({
            "keep_c0": {"type": "noul", "noul": 0.93},
            "atomic_c0": {"type": "score", "score": 3.0},
            "keep_c1": {"type": "noul", "noul": 0.10},
            "atomic_c1": {"type": "score", "score": 3.0},
        })))
    record = out["stats"]["seam"]
    assert record["prompt"] == seam.VET_PROMPT
    assert record["model"] == seam.MODEL
    assert record["bands"] == {"keep_min": seam.DEFAULT_BANDS.keep_min,
                               "discard_max": seam.DEFAULT_BANDS.discard_max,
                               "atomic_min": seam.DEFAULT_BANDS.atomic_min}
    assert record["cost_usd"] > 0.0, "the call's spend must be in the record"
    assert record["by_outcome"] == {"KEEP": 1, "DISCARD": 1}
    assert out["stats"]["by_outcome"] == record["by_outcome"], (
        "the seam's own tally and the gate's must describe the same 2 candidates")


# ── 4. a decider's reported spend cannot abort the fail-open pass ───────────

def test_an_absurd_reported_cost_cannot_abort_the_classify_pass():
    """``tail_decider`` accepts ANY callable, and the spend it reports is a JSON
    number — a JSON int can be arbitrarily large, and ``float(10**400)`` raises
    ``OverflowError``. That must degrade to "spend not counted", never escape
    ``classify_items`` (documented *never raises*): one absurd number from one
    batch would otherwise abort the whole pass instead of falling back to kNN.

    Mutant that reddens it: accumulate ``float(cost)`` directly (drop the
    ``_bounded_cost`` bound) — this raises ``OverflowError``.
    """
    clf = _classifier(lambda _rows: ({"i0": "core:workflow"}, {
        "calls": 1, "cost_usd": 10 ** 400, "prompt": seam.CLASSIFY_PROMPT,
        "model": seam.MODEL, "unavailable": 1, "schema_rejects": ["kind_c0"],
    }))
    classified = clf.classify_items(
        [{"id": "i0", "type": "entity", "text": "the plan workflow"}])
    seam_stats = classified["stats"]["seam"]
    assert classified["assignments"]["i0"]["kind"] == "core:workflow"
    assert seam_stats["cost_usd"] == 0.0, "an unrepresentable spend is not counted"
    assert seam_stats["prompt"] == seam.CLASSIFY_PROMPT, (
        "the armed-but-unavailable state has to be readable from the artifact")
    assert seam_stats["model"] == seam.MODEL, (
        "the pin that answered belongs in the record — it is the provenance the "
        "seam enforces but the classify artifact used to drop")
    assert seam_stats["unavailable"] == 1
    assert "kind_c0" in seam_stats["rejected"]


# ── 5. a degraded call names WHICH items it left unassigned ────────────────

def test_a_degraded_call_names_the_items_it_left_unassigned(monkeypatch):
    """The design record tells an operator on a degraded armed run to read
    *which* items degraded (``stats["seam"]["degraded"][item]``), not just how
    many — *"a count alone answers the wrong question"*. S2.2 recorded that map
    from the start; S2.3 counted the failure only, so on a half-armed run the
    classify artifact could say "1 batch unavailable" and never which items the
    outage left to the kNN. Both of the seam's S2.3 failure modes now name the
    item and the failing question, and the record is asserted on BOTH halves at
    the path the doc cites: ``stats["seam"]``, never a ``batch`` key.

    Mutants that redden it, each one line:
      * drop the per-item ``stats["degraded"]`` write in ``classify_tail``'s
        ``except Unavailable`` (the count-only shape) — the first ``assert``
        reads an empty map;
      * drop the ``schema_rejects`` arm's write — the second reads an empty map;
      * return the arbiter's record under ``batch`` instead of ``seam``
        (``{"verdicts": …, "batch": {"seam": stats}}``) — ``out["stats"]``
        then carries no ``seam``, which is the prose-vs-artifact mismatch the
        design record's §5 path exists to prevent.
    """
    def _down(_payload):
        raise seam.Unavailable("http 429")

    offered = [("i0", "the plan workflow", ["core:plan", "core:workflow"])]
    assignments, down_stats = seam.classify_tail(offered, transport=_down)
    assert assignments == {}
    assert down_stats["unavailable"] == 1
    assert set(down_stats["degraded"]) == {"i0"}, (
        f"the item the outage left unassigned must be NAMED, got {down_stats['degraded']!r}")
    assert seam.RULE_UNAVAILABLE in down_stats["degraded"]["i0"]
    assert "429" in down_stats["degraded"]["i0"]

    # …and a refused answer is recorded the same way, with the question named.
    _kept, rejected = seam.classify_tail(
        offered,
        transport=_transport({"kind_c0": {"type": "choice", "choice": "core:invented"}}))
    assert seam.RULE_SCHEMA_REJECT in rejected["degraded"]["i0"]
    assert "kind_c0" in rejected["degraded"]["i0"], (
        "the failing question belongs in the per-item record, not only in the tally")

    # The artifact half, classification: the item keeps the caller's kNN top-1
    # and the seam record says which item degraded.
    classified = _classifier(seam.classify_decider(transport=_down)).classify_items(
        [{"id": "i0", "type": "entity", "text": "the plan workflow"}])
    assert classified["assignments"]["i0"]["mode"] == "knn"
    assert "i0" in classified["stats"]["seam"]["degraded"]

    # The artifact half, VET — and the PATH, which is the point: ``batch`` is
    # ``vet_gate``'s own coverage record plus any arbiter BATCH verdict, and the
    # arbiter returns an empty ``batch`` (no ``RENARRATE``), so a record written
    # under ``batch["seam"]`` would be invisible to the operator §5 sends there.
    out = vet_gate.vet_candidates(_vet_embed_list(), arbiter=seam.vet_arbiter(
        transport=_down))
    assert set(out["batch"]) == {"by_section", "candidates", "coverage_ratio",
                                 "coverage_suspect", "narrative_statements"}, (
        f"``batch`` is the gate's coverage record — got {sorted(out['batch'])!r}")
    assert "seam" not in out["batch"], (
        "the seam's record does not live under ``batch``: the arbiter returns an "
        "empty batch, so a reader sent there finds nothing on a degraded run")
    assert out["stats"]["seam"]["degraded"], (
        "the gate's ``stats`` carry the per-item record")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
