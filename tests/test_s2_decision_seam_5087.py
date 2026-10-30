"""S2.2/S2.3 typed-decision seam (#5087) — the tests, named by what they pin.

No count is stated, here or in `docs/architecture/S2-DECISION-SEAM.md` (§8): the file grew
every review round, and every stale count written down was itself a review finding.

**Why the first two.** This seam exists to prevent one defect class: *an answer
that does not fit the schema is trusted anyway*. Both are either "a valid typed
answer produces the expected verdict through the REAL consumers" (so the seam's
shape is the gate's shape, not a private one) or "an out-of-schema answer fails
closed" (the defect the seam prevents). Anything that merely pins the request's
byte layout is a test that cannot fail for a reason we care about — the one
request-shape assertion kept is the ``questions`` **map** (``#7866``): the
documented array form 400s, so that line is the 400 trap, not decoration.

**Why the other two.** Being right and being *measurable* are different
failures, and only one of them reddens a pure-logic test. Arming the seam puts a
billable call on the write path, so the record of it is asserted where a cost
gate reads (``vet_gate``'s ``stats["seam"]``), and an injected decider's
unrepresentable spend is asserted not to abort a pass documented as
never-raising. Neither was reachable from the first two tests.

**Every test here is mutation-checked**, and the mutant is named in its
docstring. A test that cannot be made red by breaking the guard it claims to
test is not a test.

**Why the fifth and sixth.** The first four proved the parsers on their PASSING
side and left guards deletable with the suite green: ``_noul``'s ``[0, 1]`` band
on the out-of-band side (including a JSON ``bool``, which ``isinstance(x, int)``
accepts), the atomicity veto on a fused candidate, and — see the model-echo
assertions — an envelope check asserted against an empty answer map, which fails
at ``_answer`` rather than at the envelope it claims to test. The fifth and sixth
tests supply the input each guard exists to refuse: the parsers' guards, and then
``Bands``' ordering, ``_add_cost``'s non-negativity, ``classify_tail``'s
empty-offer filter, and — added by the round-7 review — the three guards that are
not about answers at all: the request never goes through a redirect-following
``urlopen`` (which would replay the bearer key to a 3xx target), the arming read is
**total** so an unloadable seam module cannot break the OFF path, and an
``Unavailable`` call ends the pass instead of retrying every chunk serially.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
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

def _envelope(answers: dict, *, model: str | None = "jev-1.14.0") -> dict:
    """The endpoint's response envelope — the model ECHO and an answers map.

    The echo is a CONCRETE version, deliberately not :data:`seam.MODEL`: the
    fleet sends the vendor ALIAS (owner ruling 2026-10-27), so the alias is what a
    request carries and the resolved version is what a response names. A stub
    that echoed our own constant back would make the recording self-referential
    and could never red a seam that refused a legitimate version echo.
    """
    return {"model": model, "answers": answers,
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


def _classifier(tail_decider, *, batch_size=None) -> KindClassifier:
    _clear_index_cache()
    extra = {} if batch_size is None else {"batch_size": batch_size}
    return KindClassifier(
        encoder=KeywordEncoder(),
        index=KindIndex.build(FIXTURE_SPEC, encoder=KeywordEncoder(), persist=False),
        model=None,
        llm_tail=True,
        tail_decider=tail_decider,
        **extra,
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
    verdict, and the record must name the question that failed. Two envelope-level
    facts ride along, one line each: the seam is not consulted unless it is ARMED,
    and the response's model echo is RECORDED rather than asserted (the fleet
    sends the vendor alias, so the echo is a concrete version; a refusal here
    would make an armed seam inert on the vendor's next release).

    Mutants this reddens, each breaking one guard:
      * ``_score`` dropping its ``0..hi`` range check — 9.0 on a 0..3 scale reads
        as "very atomic" and licenses the **DISCARD** of c1 (keep 0.01), so the
        "nothing was discarded" assertion reads ``{'KEEP': 1, 'DISCARD': 1}``.
      * ``_choice`` returning ``answer.get("choice")`` unvalidated — the minted
        ``core:invented`` never REACHES the classifier (``_decide_tail_seam``
        re-validates at the write site), so the kind assertion and ``mode: knn``
        still hold and the red is the seam's own refusal:
        ``closed_vocab_rejects`` becomes 1 and the seam's ``rejected`` list no
        longer names ``kind_c0``. Claiming the mint *arrives* would be the
        opposite of what the guard does.

    NOT in this list on purpose: ``_noul``'s coercion and its ``[0, 1]`` band.
    c0's wrong-primitive answer deliberately carries a ``noul``, so
    ``_answer``'s echoed-``type`` gate refuses it *before* any value is read and
    no ``_noul`` mutant can change this test's fate — measured, not assumed.
    Those guards are reddened in
    ``test_the_two_numeric_bounds_and_the_fused_veto_redden_when_removed``.
    """
    arbiter = seam.vet_arbiter(transport=_transport({
        # c0: answered with the WRONG PRIMITIVE — but carrying the field that WAS
        # asked for (`noul`), so the echoed-`type` gate is what refuses it. A
        # stub that omits `noul` fails at the probability guard instead, and the
        # type check would then be deletable with this test still green.
        "keep_c0": {"type": "choice", "noul": 0.93},
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
    # The prompt version is the ONLY provenance a degraded artifact carries, and
    # both this module's docstring and S2-DECISION-SEAM.md §prompt promise it is
    # on each degraded line. Without these the stamp is deletable and the promise
    # is decoration.
    assert all(seam.VET_PROMPT in v for v in degraded.values()), (
        f"every degraded line must name the prompt version, got {degraded!r}")

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

    # The two envelope-level facts: OFF is the production default, and the
    # model a response ECHOES is recorded, never compared to the alias we sent
    # (the fleet sends `jev-latest` by owner ruling 2026-10-27 — agent-infra#2915,
    # restated in `.env.example` and AGENTS.md). The flag is DELETED first — an
    # operator shell with it exported is the documented armed state, and reading
    # the ambient environment here would red the suite for it.
    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
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

    # The ALIAS is a recorded owner decision (2026-10-27, "let's use jev
    # latest"), and the literal is pinned the way ENDPOINT is: reintroducing a
    # version pin here silently voids every armed pass, because the vendor's alias
    # resolves to a version that is not the one we would name. No stub that echoes
    # `seam.MODEL` back can red that, so the constant itself is asserted.
    assert seam.MODEL == "jev-latest", (
        "the fleet sends the vendor ALIAS by owner ruling 2026-10-27 "
        f"(agent-infra#2915) — got {seam.MODEL!r}")

    # A FULLY VALID, in-schema answer pair served under a CONCRETE version the
    # alias resolved to — which is the NORMAL case, not an anomaly. It must
    # produce a verdict AND record the version that answered: a seam that refused
    # it would report itself armed while keeping everything and falling back to
    # kNN forever (the inert-enforcer class this module argues against).
    served_verdicts, served_stats = seam.vet_verdicts(
        [{"id": "p0", "text": "a claim"}],
        transport=lambda _payload: _envelope({
            "keep_c0": {"type": "noul", "noul": 0.93},
            "atomic_c0": {"type": "score", "score": 3.0},
        }),
    )
    assert [(v["outcome"], v["rule_id"]) for v in served_verdicts] == [
        ("KEEP", seam.RULE_SEAM)], (
        "a version echo other than the alias is the NORMAL case and must be "
        "decided, not refused")
    assert served_stats["unavailable"] == 0
    assert served_stats["model"] == seam.MODEL, "the record names what was ASKED"
    assert served_stats["model_served"] == "jev-1.14.0", (
        "the record must name the CONCRETE version that ANSWERED — that echo is "
        "what makes a shifted threshold visible instead of inferred")

    # …and an OMITTED echo answers too, recorded as None ("unknown version"),
    # never as "the pin": the name is provenance, not the decision's premise.
    absent_verdicts, absent_stats = seam.vet_verdicts(
        [{"id": "p0", "text": "a claim"}],
        transport=lambda _payload: _envelope({
            "keep_c0": {"type": "noul", "noul": 0.93},
            "atomic_c0": {"type": "score", "score": 3.0},
        }, model=None),
    )
    assert len(absent_verdicts) == 1 and absent_stats["unavailable"] == 0
    assert absent_stats["model_served"] is None

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
    assert all(seam.VET_PROMPT in v for v in seam_record["degraded"].values()), (
        "the UNAVAILABLE path must stamp the version too, not just schema_reject")
    assert seam_record["prompt"] == seam.VET_PROMPT
    monkeypatch.delenv("TORTOISE_VALUE_GATE", raising=False)


def test_a_NOOP_verdict_does_not_rescind_a_mechanical_discard(monkeypatch):
    """``NOOP`` is DEFINED as "a decision was made and it changes nothing"
    (``vet_gate``'s own vocabulary) — so it must not FLIP an outcome.

    ⛔ MEASURED BEFORE THE GUARD EXISTED (#10072 review, P2): the seam emits
    ``NOOP`` for its uncertain band and for a fused candidate, and
    ``vet_candidates`` applied every arbiter verdict with
    ``decisions[iid].update(verdict)``. So an armed seam turned
    ``DISCARD/value.identifier_only.hash`` into ``NOOP/seam.arbiter`` — the item
    surviving and the ``#4899`` predicate's rule id erased. That is the SAME harm
    the UNAVAILABLE composition test above guards against ("an outage must not
    protect what a working predicate dropped"); an emitted no-opinion is just
    another way to arrive at it, and it was the unguarded one.

    ``KEEP`` must still override — overriding the heuristic is the seam's job.
    """
    from tortoise import vet_gate

    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    identifier_only = {"points": [{"content": "a1b2c3d4e5f6a7b8c9d0"}]}

    base = vet_gate.vet_candidates(identifier_only, narrative="")["decisions"]
    iid = next(iter(base))
    assert (base[iid]["outcome"], base[iid]["rule_id"]) == (
        "DISCARD", "value.identifier_only.hash"), (
        f"precondition: the mechanical #4899 discard — got {base[iid]}")

    def _noop_arbiter(_candidates, _narrative):
        return {"verdicts": [{"id": iid, "outcome": "NOOP",
                              "reason": "uncertain band",
                              "rule_id": "seam.arbiter"}],
                "batch": {}, "seam": {"prompt": seam.VET_PROMPT}}

    got = vet_gate.vet_candidates(
        identifier_only, narrative="", arbiter=_noop_arbiter)["decisions"][iid]
    assert (got["outcome"], got["rule_id"]) == (
        "DISCARD", "value.identifier_only.hash"), (
        "an emitted NOOP must leave the gate's own decision standing — got "
        f"{got['outcome']}/{got['rule_id']}")

    def _keep_arbiter(_candidates, _narrative):
        return {"verdicts": [{"id": iid, "outcome": "KEEP",
                              "reason": "durable claim",
                              "rule_id": "seam.arbiter"}],
                "batch": {}, "seam": {"prompt": seam.VET_PROMPT}}

    kept = vet_gate.vet_candidates(
        identifier_only, narrative="", arbiter=_keep_arbiter)["decisions"][iid]
    assert (kept["outcome"], kept["rule_id"]) == ("KEEP", "seam.arbiter"), (
        "a real KEEP verdict must still override the heuristic — got "
        f"{kept['outcome']}/{kept['rule_id']}")

    # ⛔ AGREEMENT is not a new decision. A seam DISCARD that merely AGREES with
    # the mechanical one must not rewrite ``rule_id``: the record then said
    # ``DISCARD/seam.arbiter`` while the census said the predicate fired
    # (``by_rule {value.identifier_only.hash: 1}, overridden: 0``), so "which
    # rule dropped this?" had two answers for the same drop (#10072 review).
    def _agreeing_arbiter(_candidates, _narrative):
        return {"verdicts": [{"id": iid, "outcome": "DISCARD",
                              "reason": "not durable",
                              "rule_id": "seam.arbiter"}],
                "batch": {}, "seam": {"prompt": seam.VET_PROMPT}}

    agreed = vet_gate.vet_candidates(
        identifier_only, narrative="", arbiter=_agreeing_arbiter)
    got_agree = agreed["decisions"][iid]
    assert got_agree["rule_id"] == "value.identifier_only.hash", (
        "on AGREEMENT the predicate's rule id is the evidence for the drop and "
        f"must not be replaced — got {got_agree['rule_id']}")
    assert got_agree["agreed_by"] == "seam.arbiter", (
        "the seam's agreement is recorded beside it, additively — got "
        f"{got_agree.get('agreed_by')}")
    assert agreed["stats"]["mechanical"]["by_rule"][
        "value.identifier_only.hash"] == 1, (
        "the census must still name the predicate that fired")
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
    assert record["model_served"] == "jev-1.14.0", (
        "the gate record carries the version that ANSWERED beside the alias that "
        "was asked for — without it a rolled model is invisible in the artifact")
    assert record["bands"] == {"keep_min": seam.DEFAULT_BANDS.keep_min,
                               "discard_max": seam.DEFAULT_BANDS.discard_max,
                               "atomic_min": seam.DEFAULT_BANDS.atomic_min}
    assert record["cost_usd"] > 0.0, "the call's spend must be in the record"
    assert record["by_outcome"] == {"KEEP": 1, "DISCARD": 1}
    assert out["stats"]["by_outcome"] == record["by_outcome"], (
        "the seam's own tally and the gate's must describe the same 2 candidates")


def test_an_armed_tail_reports_its_own_calls_beside_the_llm_counter():
    """The seam's batches have to be countable in the artifact the arm
    comparison is read from.

    ``adjudication_calls`` is the LLM tail's call counter and stays 0 on a seam
    run — but a census that showed ONLY that reads an armed arm as INERT
    (non-zero ``adjudication_tail_rate`` beside ``adjudication_calls: 0``,
    which is the false reading the A/B gate exists to avoid). The seam's own
    count rides beside it: ``assigned_seam`` (the same assignments
    ``assigned_llm`` already folds in, narrowed) and ``stats["seam"]["calls"]``.

    Spend stays SPLIT — this counts calls and assignments, never money: the
    LLM's spend is ``stats["llm"]``, the seam's is ``stats["seam"]``.

    Mutant that reddens it: drop the ``assigned_seam`` bump at the seam's write
    site (or the key from the stat dict) and that lane is invisible again.

    The second half reddens on a different mutant: **replace**
    ``seam["model_served"]`` with the batch's own value instead of folding it
    through ``_record_served`` (the pre-fix code). One batch cannot see that —
    the two-batch phase pins it, because the alias resolving to a new version
    between batches then reads as *that* version having decided the whole tail.
    """
    clf = _classifier(lambda _rows: ({"i0": "core:workflow"}, {
        "calls": 1, "cost_usd": 0.001, "input_tokens": 6112,
        "prompt": seam.CLASSIFY_PROMPT, "model": seam.MODEL}))
    out = clf.classify_items(
        [{"id": "i0", "type": "entity", "text": "the plan workflow"}])
    stats = out["stats"]
    assert out["assignments"]["i0"]["mode"] == "seam"
    assert stats["adjudication_calls"] == 0, (
        "the LLM's call counter stays the LLM's — the seam does not blend into it")
    assert stats["assigned_seam"] == 1, (
        "the seam's assignments must be countable on their own lane")
    assert stats["seam"]["calls"] == 1
    # The receipt. On the VENDOR endpoint a response's ``usage`` is
    # ``{input_tokens, output_tokens}`` and carries NO ``cost_usd`` at all, so a
    # classify half that folded only ``cost_usd`` published the seeded "0.0" — a
    # number the service never reported — and dropped the only receipt there was,
    # while the VET half (which folds the seam's whole stats dict) kept it. The
    # two halves disagreed about the same call.
    assert stats["seam"]["input_tokens"] == 6112, (
        "the vendor reports tokens, not money — the token receipt must survive "
        f"the fold into stats['seam'] — got {stats['seam'].get('input_tokens')!r}")
    assert "llm" not in stats, (
        "no LLM spend was made, so no LLM spend block is reported")

    # ── the served-version receipt ACCUMULATES across batches ────────────────
    # `_decide_tail_seam` runs once per classifier batch and folds THAT batch's
    # own seam stats, so an assignment published only the last batch's version
    # and claimed it decided the whole tail — the reading a roll BETWEEN
    # batches must be able to refuse (`.env.example`: "record it"). The seam's
    # own `_record_served` keeps a single version as a string and merges
    # differing ones into a sorted list; this half now folds through the same
    # helper instead of replacing. Two batches, the vendor's alias resolving to
    # a new version between them:
    versions = iter(["jev-1.14.0", "jev-1.15.0"])
    rolled = _classifier(
        lambda rows: ({rows[0][0]: "core:workflow"}, {
            "calls": 1, "prompt": seam.CLASSIFY_PROMPT, "model": seam.MODEL,
            "model_served": next(versions)}),
        batch_size=1)
    two = rolled.classify_items([
        {"id": "i0", "type": "entity", "text": "the plan workflow"},
        {"id": "i1", "type": "entity", "text": "the plan workflow"},
    ])
    assert two["stats"]["seam"]["calls"] == 2, (
        "one item per batch is two batches, so two seam calls")
    assert [a["mode"] for a in two["assignments"].values()] == ["seam", "seam"]
    assert two["stats"]["seam"]["model_served"] == ["jev-1.14.0", "jev-1.15.0"], (
        "a roll between classifier batches must be a RECEIPT, not a last-write: "
        "a replaced field claims one version decided the entire tail — got "
        f"{two['stats']['seam'].get('model_served')!r}")


def test_identical_answers_across_a_chunk_are_not_attributable():
    """ELEMENT ATTRIBUTION is the one request shape the vendor's reference page
    does not document (design record §2), and the only one of the three whose
    failure is SILENT: ``state`` addresses elements by an ``id`` field, each
    question names its element in TEXT, and the answers are read back
    POSITIONALLY — so a model that ignores the ids still returns in-schema,
    in-band answers about the wrong element. One ``keep=0.02, atomic=3.0`` pair
    would then ``DISCARD`` a whole chunk of 40 candidates in a single round
    trip, which is memory loss with a plausible-looking ``rule_id``.

    The design record's answer was a procedural 2-item probe. A probe is a step
    nobody is required to run, so the same signature is a RUNTIME check: answers
    that do not differ across THREE OR MORE elements yield no verdict at all
    (the fail-open direction — a wrong keep is noise), and every candidate is
    named in ``stats["degraded"]``. The control below shows the guard is not a
    blanket refusal: differing answers still decide.

    Mutant that reddens it: return ``False`` from
    ``_answers_are_identical_across_chunk`` — the identical chunk then yields
    DISCARD verdicts.
    """
    items = [{"id": f"p{i}", "text": text} for i, text in enumerate([
        "Decided: the cache stays warm across restarts.",
        "Decided: the retry budget is two attempts.",
        "Decided: the index is rebuilt nightly.",
    ])]

    identical: dict = {}
    for pos in range(3):
        identical[f"keep_c{pos}"] = {"type": "noul", "noul": 0.02}
        identical[f"atomic_c{pos}"] = {"type": "score", "score": 3.0}
    verdicts, stats = seam.vet_verdicts(items, transport=_transport(identical))
    assert verdicts == [], (
        "an unattributable chunk must produce NO verdict — not a DISCARD")
    assert stats["attribution_rejects"] == 1
    assert stats["calls"] == 1, "the call WAS made; the count is not a lie"
    assert stats["by_outcome"] == {}
    for item in items:
        assert stats["degraded"][item["id"]].startswith(seam.RULE_ATTRIBUTION), (
            f"{item['id']} must be named as degraded, with the prompt version")

    # Control: differing answers are attributable, so the same three candidates
    # DO get verdicts. (Otherwise the guard would be a blanket refusal dressed
    # as a check.)
    differing: dict = {}
    for pos, (keep, atomic) in enumerate(((0.02, 3.0), (0.93, 3.0), (0.50, 1.0))):
        differing[f"keep_c{pos}"] = {"type": "noul", "noul": keep}
        differing[f"atomic_c{pos}"] = {"type": "score", "score": atomic}
    verdicts, stats = seam.vet_verdicts(items, transport=_transport(differing))
    assert stats["attribution_rejects"] == 0
    assert [v["outcome"] for v in verdicts] == ["DISCARD", "KEEP", "NOOP"]


def test_an_unattributable_chunk_cannot_discard_through_the_real_gate():
    """The composition, not the unit: an armed seam answering identically for a
    chunk of candidates must leave EVERY candidate exactly where the gate put
    it — here, kept.

    Mutant that reddens it: drop the identical-answers check in
    ``vet_verdicts`` (the seam then emits ``DISCARD`` for all three through the
    real ``vet_gate``), which is the whole point of the guard.
    """
    answers: dict = {}
    for pos in range(3):
        answers[f"keep_c{pos}"] = {"type": "noul", "noul": 0.02}
        answers[f"atomic_c{pos}"] = {"type": "score", "score": 3.0}
    out = vet_gate.vet_candidates(
        {"points": [
            {"content": "Decided: the cache stays warm across restarts."},
            {"content": "Decided: the retry budget is two attempts."},
            {"content": "Decided: the index is rebuilt nightly."},
        ]},
        arbiter=seam.vet_arbiter(transport=_transport(answers)))
    assert out["stats"]["seam"]["attribution_rejects"] == 1
    assert out["stats"]["by_outcome"] == {"KEEP": 3}, (
        "an unattributable answer must not drop anything")


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
        "model": seam.MODEL, "model_served": "jev-1.14.0",
        "unavailable": 1, "schema_rejects": ["kind_c0"],
    }))
    classified = clf.classify_items(
        [{"id": "i0", "type": "entity", "text": "the plan workflow"}])
    seam_stats = classified["stats"]["seam"]
    assert classified["assignments"]["i0"]["kind"] == "core:workflow"
    assert seam_stats["cost_usd"] == 0.0, "an unrepresentable spend is not counted"
    assert seam_stats["prompt"] == seam.CLASSIFY_PROMPT, (
        "the armed-but-unavailable state has to be readable from the artifact")
    assert seam_stats["model"] == seam.MODEL, (
        "the alias that was ASKED FOR belongs in the record — the classify "
        "artifact used to drop it")
    assert seam_stats["model_served"] == "jev-1.14.0", (
        "the concrete version that ANSWERED belongs beside it — with a vendor "
        "alias that moves, this is the only receipt of which model decided")
    assert seam_stats["unavailable"] == 1
    assert "kind_c0" in seam_stats["rejected"]


def test_the_two_numeric_bounds_and_the_fused_veto_redden_when_removed():
    """The three guards the earlier tests exercised only on their PASSING side.

    Mutation-checked, and this is why it exists: each of the three could be
    deleted and the other four tests stayed green, so the guard was unproven.

    Mutants this reddens, each breaking exactly one guard:
      * ``_noul`` dropping ``not (0.0 <= value <= 1.0)`` — ``noul=1.5`` becomes a
        confident KEEP and ``noul=-0.2`` becomes a **DISCARD**. ``_finite``
        accepts both, so nothing else catches them and a malformed answer
        silently drops a claim: a wrong drop is memory loss.
      * ``vet_verdict`` dropping ``if atomic < bands.atomic_min`` — a low-validity
        **fused** candidate becomes ``DISCARD``, so dropping one candidate loses
        the several claims fused into it. ``SPLIT`` is not in O4's vocabulary, so
        the veto is the only thing standing between the two.
    """
    # ── _noul's [0, 1] band, on BOTH sides of it ──────────────────────────────
    for bad, why in ((1.5, "above the band"), (-0.2, "below the band"),
                     (True, "a JSON bool")):
        verdicts, stats = seam.vet_verdicts(
            [{"id": "b0", "text": "a claim"}],
            transport=lambda _p, _v=bad: {"model": seam.MODEL, "answers": {
                "keep_c0": {"type": "noul", "noul": _v},
                "atomic_c0": {"type": "score", "score": 3.0},
            }},
        )
        assert verdicts == [], f"a noul {why} ({bad!r}) must produce no verdict"
        assert stats["schema_rejects"] == ["keep_c0"], (
            f"the probability guard must be what refused {bad!r} ({why}) — it is "
            "the only check between a malformed answer and a verdict")

    # ── the atomicity veto: keep < discard_max AND atomic < atomic_min ────────
    fused, _ = seam.vet_verdicts(
        [{"id": "f0", "text": "a fused candidate"}],
        transport=lambda _p: {"model": seam.MODEL, "answers": {
            "keep_c0": {"type": "noul", "noul": 0.02},
            "atomic_c0": {"type": "score", "score": 1.0},
        }},
    )
    assert [(v["outcome"], "fused candidate is not dropped" in v["reason"])
            for v in fused] == [("NOOP", True)], (
        "a low-validity FUSED candidate must be vetoed out of the drop — "
        f"got {fused!r}")


def test_the_module_guards_the_suite_never_proved_redden_when_removed(monkeypatch, tmp_path):
    """The non-parser guards, each documented as load-bearing in this module and
    each deletable with the whole suite green before this test existed.

    Mutants this reddens, one guard each:
      * ``Bands.__post_init__`` dropping ``0 <= discard_max < keep_min <= 1`` —
        ``Bands(keep_min=0.30, discard_max=0.70)`` is then accepted, and because
        ``vet_verdict`` tests ``keep >= keep_min`` FIRST, every probability in
        ``[0.30, 1]`` becomes a KEEP: the configured ``discard_max`` stops
        meaning what its own docstring says it means.
      * ``_add_cost`` dropping ``cost >= 0.0`` — a negative reported spend is
        then SUBTRACTED from the recorded total, so an artifact can under-report
        what it spent and the cost gate reads a number that cannot exist.
      * ``classify_tail`` dropping its empty-offered-set filter — an item with
        nothing offered then ships an empty ``criteria`` map, which this module's
        own docstring says the endpoint 400s, so the WHOLE chunk loses its
        assignments while ``skipped_no_offer`` reports 0 for the gap.
      * the wire is ``urllib.request.urlopen`` instead of the redirect-refusing
        ``_OPENER`` — the default opener FOLLOWS a 3xx and copies request headers
        to the new origin, so the bearer spend key is replayed to whatever host
        answered. ``_RefuseRedirects`` raising is the guard; the ``urlopen``
        tripwire here is what proves the call actually goes through it.
      * ``extractor_v2._s2_seam_armed`` importing the seam module OUTSIDE its
        guard — with the module unloadable, ``_vet_enabled()`` then raises out of
        a call site that is outside any ``try``, aborting a whole capture for code
        the session never asked for. The OFF path must be TOTAL.
      * the chunk loops continuing after an ``Unavailable`` — ``Unavailable`` is
        the step-level state the docstring's table names, so continuing is a
        serial retry ladder at up to ``TIMEOUT`` per chunk on the synchronous
        write path. One call per pass is the guard; the VET pass still names
        every remaining candidate, so nothing is lost by stopping.
      * ``KindClassifier._adjudicate_batches`` re-consulting the tail decider for
        every batch after the first one reported the capability unavailable —
        the same ladder one level up, and the worse one: the S2.3 consumer calls
        the seam per CLASSIFIER batch, so a 5,000-item tail was 125 dead round
        trips (~62 min at the 30 s budget). The latch is the guard; the items it
        skips take the caller's declared kNN top-1 fallback and are counted.
    """
    # A band that inverts its own thresholds is not a policy, it is a bug: with
    # the ordering unchecked, discard_max above keep_min makes every value a KEEP.
    with pytest.raises(ValueError):
        seam.Bands(keep_min=0.30, discard_max=0.70)

    # A reported spend is display-only, but it may not be negative.
    _, negative = seam.vet_verdicts(
        [{"id": "n0", "text": "a claim"}],
        transport=lambda _p: {"model": seam.MODEL,
                              "usage": {"cost_usd": -5.0},
                              "answers": {
                                  "keep_c0": {"type": "noul", "noul": 0.93},
                                  "atomic_c0": {"type": "score", "score": 3.0},
                              }},
    )
    assert negative["cost_usd"] == 0.0, (
        "a negative reported spend must not be subtracted from the record")

    # Nothing offered ⇒ nothing asked: the item is COUNTED as skipped and no
    # round trip is made at all. The transport proves the second half.
    def _must_not_be_called(_payload):
        raise AssertionError("an item with an empty offered set must not be sent")

    assignments, no_offer = seam.classify_tail(
        [("i0", "the plan workflow", [])], transport=_must_not_be_called)
    assert assignments == {}
    assert (no_offer["items"], no_offer["skipped_no_offer"]) == (0, 1), (
        "an unaskable item must be counted as skipped, not silently dropped")

    # ── the wire is the redirect-refusing opener, not ``urlopen`` ────────────
    # ``urlopen``'s default opener follows 301/302/303/307/308 and CPython's
    # redirect handler copies every request header to the new origin, so the
    # bearer key would be replayed to whatever host answered. Two assertions:
    # the handler refuses, and the POST goes through the opener that uses it.
    with pytest.raises(urllib.error.HTTPError):
        seam._RefuseRedirects().redirect_request(
            urllib.request.Request(seam.ENDPOINT), None, 302, "Found", {},
            "https://not-the-endpoint.example/steal")

    # …and the production opener must BE redirect-refusing, not merely be used:
    # ``build_opener()`` installs the DEFAULT ``HTTPRedirectHandler`` unless the
    # refusing subclass is what was passed, so rebuilding ``_OPENER`` as
    # ``build_opener()`` would leave the two facts above true and the key still
    # replayable. This is the assertion that reddens on that mutant.
    assert any(isinstance(handler, seam._RefuseRedirects)
               for handler in seam._OPENER.handlers), (
        "the production opener must install _RefuseRedirects — a default "
        "build_opener() follows 3xx and replays the bearer key to the target")
    # …and presence is not precedence: a FOLLOWER installed alongside the refuser
    # is the same defect in another composition, and `any()` above cannot see it.
    assert not any(type(handler) is urllib.request.HTTPRedirectHandler
                   for handler in seam._OPENER.handlers), (
        "a default redirect FOLLOWER must not be installed beside _RefuseRedirects")

    seen: dict = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, amt=None):
            # Mirror ``http.client.HTTPResponse.read(amt=None)`` — the seam
            # bounds the body (#10072 review), so a no-arg fake would test a
            # call the production path does not make.
            body = json.dumps({"model": seam.MODEL, "answers": {}}).encode()
            return body if amt is None else body[:amt]

    class _Opener:
        def open(self, request, timeout=None):
            seen["request"] = request
            seen["timeout"] = timeout
            return _Response()

    def _no_bare_urlopen(*_a, **_k):
        raise AssertionError(
            "the seam must post through _OPENER (redirect-refusing), not urlopen")

    monkeypatch.setenv("JEV_API_KEY", "jv_live_not_a_real_key")
    monkeypatch.setattr(seam, "_OPENER", _Opener())
    monkeypatch.setattr(urllib.request, "urlopen", _no_bare_urlopen)
    assert seam._http_post({"model": seam.MODEL, "state": "s", "questions": {}},
                           timeout=2.5) == {"model": seam.MODEL, "answers": {}}
    assert seen["request"].get_header("Authorization") == "Bearer jv_live_not_a_real_key"
    assert seen["timeout"] == 2.5, "the per-call budget must reach the wire"

    # ── the flag read is TOTAL: an unloadable seam can never arm, never raise ─
    import tortoise as tortoise_pkg
    from tortoise import extractor_v2 as v2

    monkeypatch.delenv("TORTOISE_VET", raising=False)
    monkeypatch.setitem(sys.modules, "tortoise.s2_decision_seam", None)
    monkeypatch.delattr(tortoise_pkg, "s2_decision_seam", raising=False)
    # The state the guard exists for is "the flag says ARM, and the module is
    # broken" — so the flag is SET here, not deleted. Both halves of the answer
    # matter: it must not raise, and it must not arm.
    monkeypatch.setenv(seam.ENV_FLAG, "1")
    assert v2._s2_seam_armed() is False, (
        "an unloadable seam module cannot be armed even when the flag asks — "
        "and it must not raise into _vet_enabled, whose call site is outside "
        "any try")
    assert v2._vet_enabled() is False, (
        "the OFF path stays total AND off: a broken seam never arms VET")
    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
    # Put the module back (``monkeypatch`` would only restore at teardown) before
    # asserting the ON direction — otherwise the rest of this test would be
    # measuring the simulated breakage, not the flag's reach.
    monkeypatch.setitem(sys.modules, "tortoise.s2_decision_seam", seam)
    monkeypatch.setattr(tortoise_pkg, "s2_decision_seam", seam, raising=False)

    # …and the flag DOES reach the gate when the module is intact: this is the
    # documented override of an explicit TORTOISE_VET=0 (the seam IS S2.2's
    # arbiter, so an armed seam no gate runs is the inert-enforcer class).
    monkeypatch.setenv("TORTOISE_VET", "0")
    assert v2._vet_enabled() is False, "TORTOISE_VET=0 alone keeps the gate off"
    monkeypatch.setenv(seam.ENV_FLAG, "1")
    assert v2._vet_enabled() is True, (
        "an armed seam must arm VET even against an explicit TORTOISE_VET=0")
    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
    monkeypatch.delenv("TORTOISE_VET", raising=False)

    # ── an unavailable call ends the PASS, not just the chunk ────────────────
    # 100 candidates is 3 chunks at the default batch size, and the chunks are
    # DISJOINT — so before this guard a hung endpoint did not re-send anything; it
    # spent 3 serial timeouts on candidates nothing had yet judged. Now it costs
    # one, and the items the pass would have judged are all named degraded rather
    # than left silent.
    attempts: list = []

    def _down(_payload):
        attempts.append(1)
        raise seam.Unavailable("429")

    many = [{"id": f"d{i}", "text": f"claim {i}"} for i in range(100)]
    down_verdicts, down_stats = seam.vet_verdicts(many, narrative="", transport=_down)
    assert len(attempts) == 1, (
        f"an unavailable call must end the pass, not retry per chunk — {len(attempts)} calls")
    assert down_verdicts == [] and down_stats["unavailable"] == 1, (
        f"a stopped pass still produces no verdicts — got {down_verdicts!r} / "
        f"unavailable={down_stats['unavailable']}")
    assert sorted(down_stats["degraded"]) == sorted(c["id"] for c in many), (
        "stopping early must still name every candidate it would have judged")

    tail_attempts: list = []

    def _down_tail(_payload):
        tail_attempts.append(1)
        raise seam.Unavailable("timeout")

    tail = [(f"t{i}", f"text {i}", ["core:plan"]) for i in range(100)]
    tail_out, tail_stats = seam.classify_tail(tail, transport=_down_tail)
    assert len(tail_attempts) == 1 and tail_out == {}, (
        f"the classify pass stops too — {len(tail_attempts)} calls")
    assert tail_stats["unavailable"] == 1, (
        f"one unavailable batch must be counted once — got {tail_stats['unavailable']}")

    # …and the CONSUMER does not re-ask it per classifier batch, which is the
    # same ladder one level up: the tail decider is invoked once per classifier
    # batch, so without the latch a dead endpoint costs one round trip per batch
    # (125 of them on a 5,000-item tail), while `classify_tail`'s own break only
    # bounds the calls INSIDE one of them.
    consumer_calls: list = []

    def _down_consumer(rows):
        consumer_calls.append(len(rows))
        return {}, {"calls": 0, "unavailable": 1}

    clf = _classifier(_down_consumer, batch_size=2)
    consumer_stats = {"assigned_knn": 0, "assigned_llm": 0,
                      "closed_vocab_rejects": 0, "classify_errors": 0}
    consumer_assignments: dict = {}
    consumer_tail = [(f"k{i}", {"text": f"claim {i}"},
                      [("core:plan", 0.5), ("core:workflow", 0.4)])
                     for i in range(6)]
    clf._adjudicate_batches(consumer_tail, consumer_assignments, consumer_stats, [])
    assert consumer_calls == [2], (
        "an unavailable capability must be asked ONCE per pass, not once per "
        f"classifier batch — got {len(consumer_calls)} calls")
    assert consumer_stats["assigned_knn"] == 6, (
        f"every remaining item takes the declared fallback — got {consumer_stats}")
    assert {a["mode"] for a in consumer_assignments.values()} == {"knn"}, (
        f"no kind may be minted by a down capability — got {consumer_assignments}")

    # …and the OTHER half of the same latch: a decider that RAISES must stop the
    # pass exactly like one that reports `unavailable`. The latch is set at two
    # distinct sites (`_decide_tail_seam`'s `except` for a raise, the
    # `unavailable` count for a reported outage), and only the reported half was
    # pinned — deleting the raise-branch assignment left this suite GREEN, so the
    # ladder the latch exists to prevent (one real round trip per classifier
    # batch) was unpinned on the path a transport blow-up takes (#10072 review).
    raising_calls: list = []

    def _raising_consumer(rows):
        raising_calls.append(len(rows))
        raise RuntimeError("decider down")

    raising_stats = {"assigned_knn": 0, "assigned_llm": 0,
                     "closed_vocab_rejects": 0, "classify_errors": 0}
    raising_assignments: dict = {}
    raising_clf = _classifier(_raising_consumer, batch_size=2)
    raising_tail = [(f"k{i}", {"text": f"claim {i}"},
                     [("core:plan", 0.5), ("core:workflow", 0.4)])
                    for i in range(6)]
    raising_clf._adjudicate_batches(raising_tail, raising_assignments,
                                    raising_stats, [])
    assert raising_calls == [2], (
        "a RAISING decider must latch for the pass too, not be re-asked once "
        f"per classifier batch — got {len(raising_calls)} calls")
    assert raising_stats["assigned_knn"] == 6, (
        f"every remaining item takes the declared fallback — got {raising_stats}")
    assert {a["mode"] for a in raising_assignments.values()} == {"knn"}, (
        f"no kind may be minted by a raising decider — got {raising_assignments}")

    # ── the ENDPOINT is the vendor's host, and the KEY resolution order ──────
    # Reintroducing the lookalike host is not hypothetical: this branch carried
    # `https://jevtypesafeai.com/api/v1/decide` while `main` was moving every
    # caller to `api.typesafe.ai` (tortoise#7864 paid that host; #10095/#10128
    # did this repo's leg), so the rebase conflict was resolved by hand and the
    # constant is what no doc line can protect. `jv_live_…` credentials are worth
    # nothing to a host nobody named: an armed seam would hand a billable key to
    # whoever answered.
    assert seam.ENDPOINT == "https://api.typesafe.ai/v1/systemone", (
        "the seam must post to the VENDOR endpoint, with the fleet's model "
        f"alias — got {seam.ENDPOINT!r}")
    assert "jevtypesafeai" not in seam.ENDPOINT, (
        "the lookalike host is forbidden in this repo (tortoise#7864/#10095) — "
        f"got {seam.ENDPOINT!r}")

    # The key resolves env -> 0600 store -> repo-root `.env`, with the vendor's
    # name first. Each term is load-bearing: the store is the key's documented
    # HOME, so skipping it leaves the documented arming recipe unkeyed; reading
    # `.env` before it would let a local convenience outrank the fleet's store;
    # and a whitespace-only env value is TRUTHY, so `if value:` (no `.strip()`)
    # sends `Bearer ` and the 401 blames the caller's code. `HOME` is
    # monkeypatched so this never reads the real store.
    empty_home = tmp_path / "home"
    (empty_home / ".pi" / "agent").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(empty_home))
    repo_env = tmp_path.resolve() / "repo" / ".env"
    repo_env.parent.mkdir(parents=True, exist_ok=True)
    repo_env.write_text("JEV_API_KEY=jv_live_in_a_dotenv\n", encoding="utf-8")
    monkeypatch.setattr(seam, "__file__", str(repo_env.parent / "tortoise" / "seam.py"))
    for name in seam.KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert seam._api_key() == "jv_live_in_a_dotenv", (
        "the repo-root `.env` is the LAST source, and it must still be read")
    (empty_home / ".pi" / "agent" / "jev-config.json").write_text(
        '{"apiKey": "apikey_from_the_store"}', encoding="utf-8")
    assert seam._api_key() == "apikey_from_the_store", (
        "the 0600 store is the key's documented HOME and must outrank `.env`")
    monkeypatch.setenv("JEV_API_KEY", "jv_live_old_name")
    assert seam._api_key() == "jv_live_old_name", (
        "the older env name is still read (agent-infra#2642)")
    monkeypatch.setenv("TYPESAFE_API_KEY", "apikey_vendor_name")
    assert seam._api_key() == "apikey_vendor_name", (
        "the vendor's own name wins over the legacy one")
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    assert seam._api_key() == "jv_live_old_name", (
        "a whitespace-only value is truthy — it must not win and send `Bearer `")
    # …and the FILE term honours the same order: an EMPTY `TYPESAFE_API_KEY=` line
    # (the line `.env.example` shows you how to uncomment) must not shadow a
    # populated `JEV_API_KEY=` line below it and report no key at all while a key
    # sits in the file. A single file-order scan that returns on the first name it
    # matches does exactly that.
    for name in seam.KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    repo_env.write_text("TYPESAFE_API_KEY=\nJEV_API_KEY=jv_live_from_legacy_line\n",
                        encoding="utf-8")
    (empty_home / ".pi" / "agent" / "jev-config.json").unlink()
    assert seam._api_key() == "jv_live_from_legacy_line", (
        "an empty vendor-name line must not shadow a populated legacy-name line "
        f"in the same .env — got {seam._api_key()!r}")


def test_the_arming_flag_reaches_the_PRODUCTION_call_sites(monkeypatch):
    """The flag must reach the two gates *through the production call sites*,
    not merely yield a callable at the helper.

    ⛔ MEASURED BEFORE THIS TEST EXISTED (#10072 review, P1): deleting
    ``tail_decider=_s2_seam_tail_decider()`` from ``_default_kind_classifier``
    left ``tests/test_s2_decision_seam_5087.py`` + ``tests/test_vet_gate_5005.py``
    fully GREEN (66 passed), and replacing ``arbiter=_s2_seam_arbiter(arbiter)``
    with ``arbiter=arbiter`` in ``_run_vet_pass`` left those PLUS
    ``tests/test_e1_e2_measurement.py`` green (182 passed). One deleted keyword
    therefore disables S2.2 or S2.3 for the whole pipeline with CI green —
    precisely the inert-enforcer class the neighbouring test's own comment claims
    is covered, while asserting only the two helpers.

    These assertions go through the real call sites, so each deleted wire reds.
    """
    from tortoise import extractor_v2 as v2

    # ── S2.3 — the classifier the EXTRACTOR actually builds bears the decider.
    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
    assert getattr(v2._default_kind_classifier(None), "tail_decider", None) is None, (
        "flag off ⇒ the classifier must be constructed exactly as pre-#5087")

    # ⛔ ``is not None`` is too loose: ANY callable would satisfy it, including a
    # stub that returns no assignments and defeats S2.3 with the suite green.
    # Pin THE SEAM'S decider by substituting a sentinel through the production
    # helper and asserting it arrives at the production classifier.
    sentinel = object()
    monkeypatch.setenv(seam.ENV_FLAG, "1")
    monkeypatch.setattr(seam, "classify_decider", lambda **kw: sentinel)
    assert getattr(v2._default_kind_classifier(None), "tail_decider", None) is sentinel, (
        "flag on ⇒ the PRODUCTION classifier must carry the seam's own tail "
        "decider (a stand-in callable must not pass); deleting the keyword in "
        "_default_kind_classifier reds this")
    # …and unarmed it still builds pre-#5087, with the sentinel in place.
    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
    assert getattr(v2._default_kind_classifier(None), "tail_decider", None) is None

    # ── S2.2 — the VET pass actually hands ``vet_candidates`` an arbiter.
    seen: dict = {}

    def _capture(candidates, *, narrative="", arbiter=None, **kw):
        seen["arbiter"] = arbiter
        return {"decisions": {}, "stats": {}, "batch": {}}

    monkeypatch.setattr(v2._vet_gate, "vet_candidates", _capture)

    monkeypatch.delenv(seam.ENV_FLAG, raising=False)
    v2._run_vet_pass({"points": [{"id": "p0", "content": "a claim"}]}, "")
    assert seen.get("arbiter") is None, "flag off ⇒ no arbiter (today's VET)"

    monkeypatch.setenv(seam.ENV_FLAG, "1")
    v2._run_vet_pass({"points": [{"id": "p0", "content": "a claim"}]}, "")
    assert seen.get("arbiter") is not None, (
        "flag on ⇒ _run_vet_pass must pass a NON-None arbiter to the gate; "
        "replacing the keyword with `arbiter=arbiter` reds this")


def test_the_batch_bound_the_read_cap_and_the_offer_bind_are_pinned(monkeypatch):
    """Three guards this commit added, each reversible with the suite green.

    A guard whose deletion cannot red a test is not pinned — the standard this
    file's own docstring states, and the exact root of the P1 one cycle earlier.
    """
    # ── ``_step`` actually CLAMPS. ``batch_size=500`` used to build a request
    # past the vendor's 64k-token budget and 400 the whole chunk.
    assert seam._step(500) == seam.BATCH_MAX
    assert seam._step(0) == 1
    assert seam._step(7) == 7
    # An unbounded knob must land on the ceiling, not escape as a traceback:
    # ``int(float('inf'))`` raises OverflowError.
    assert seam._step(float("inf")) == seam.BATCH_MAX
    assert seam._step(object()) == seam.BATCH_MAX

    # ── the BODY CAP degrades instead of parsing an oversized/unbounded read.
    # ⛔ The body must be VALID JSON and the assertion must pin the CAUSE.
    # Measuring it the obvious way (a junk blob + ``pytest.raises(Unavailable)``)
    # is NOT discriminating: ``read(MAX_BODY + 1)`` truncates the body, so the
    # JSON parse fails and the SAME exception is raised with the cap deleted —
    # the assertion then passes for the wrong reason (verified: that mutant
    # survived). A valid-JSON body past the cap, plus a check on the reason,
    # reds the moment the cap guard is removed.
    oversized = json.dumps({
        "model": seam.MODEL, "answers": {},
        "pad": "x" * seam.MAX_BODY,
    }).encode()
    assert len(oversized) > seam.MAX_BODY

    class _HugeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, amt=None):
            return oversized if amt is None else oversized[:amt]

    class _HugeOpener:
        def open(self, request, timeout=None):
            return _HugeResponse()

    monkeypatch.setenv("JEV_API_KEY", "jv_live_not_a_real_key")
    monkeypatch.setattr(seam, "_OPENER", _HugeOpener())
    with pytest.raises(seam.Unavailable) as over_cap:
        seam._http_post({"model": seam.MODEL, "state": "s", "questions": {}},
                        timeout=2.5)
    assert "exceeds" in str(over_cap.value), (
        "the over-cap body must degrade for the CAP reason, not incidentally "
        f"through a JSON parse error — got {over_cap.value!r}")

    # ── a GENERATOR-valued ``offered`` must not defeat the empty-offer guard.
    # Materialising it twice consumed it in the filter, so the row survived with
    # an EMPTY criteria map — the 400 the guard exists to prevent — while
    # ``skipped_no_offer`` reported 0.
    captured: dict = {}

    class _NoopTransport:
        def __call__(self, payload):
            captured["payload"] = payload
            return {"model": seam.MODEL, "answers": {},
                    "usage": {"input_tokens": 1, "cost_usd": 0.0}}

    _verdicts, gen_stats = seam.classify_tail(
        [("i0", "some text", (k for k in ["a", "b"]))],
        transport=_NoopTransport())
    criteria = captured["payload"]["questions"]["kind_c0"]["criteria"]
    assert criteria, (
        "a generator-valued offered set must still populate criteria — an empty "
        "map is the 400 the empty-offer guard exists to prevent")
    assert list(criteria) == ["a", "b"]
    assert gen_stats["skipped_no_offer"] == 0


def test_a_standalone_KEEP_verdict_keeps_its_own_attribution(monkeypatch):
    """⚠️ REGRESSION THIS TEST EXISTS FOR (#10072 review, cycle 3):

    The agreement rule ("the incoming outcome equals the standing one ⇒ keep the
    standing rule id") was first written as ``incoming == standing``. For a KEPT
    candidate the STANDING row is the gate's fail-open DEFAULT
    (``KEEP``/``vet.fail_open``/``no verdict``) — which is the *absence* of a
    decision, not one — so a real ``KEEP`` verdict at noul 0.93 was recorded as
    ``rule_id: vet.fail_open, reason: "no verdict", agreed_by: seam.arbiter``.
    The artifact then claimed no verdict was given when one was, and "why was
    this kept?" became unanswerable per item.

    Agreement must therefore be scoped to a standing DECISION
    (``rule_id != RULE_FAIL_OPEN``).
    """
    from tortoise import vet_gate

    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    real_claim = {"points": [{"id": "p1",
                              "content": "The board approved the Series A on 2026-03-04."}]}
    iid = next(iter(vet_gate.vet_candidates(real_claim, narrative="")["decisions"]))
    assert vet_gate.vet_candidates(real_claim, narrative="")["decisions"][iid][
        "rule_id"] == vet_gate.RULE_FAIL_OPEN, "precondition: the default row is fail-open"

    def _keep(_candidates, _narrative):
        return {"verdicts": [{"id": iid, "outcome": "KEEP",
                              "reason": "validity 0.93 >= 0.70",
                              "rule_id": "seam.arbiter"}],
                "batch": {}, "seam": {"prompt": seam.VET_PROMPT}}

    kept = vet_gate.vet_candidates(
        real_claim, narrative="", arbiter=_keep)["decisions"][iid]
    assert kept["outcome"] == "KEEP"
    assert kept["rule_id"] == "seam.arbiter", (
        "a standalone KEEP must carry the seam's attribution, not fall back to "
        f"the fail-open default row — got {kept['rule_id']}")
    assert kept["reason"] == "validity 0.93 >= 0.70", (
        f"the band reason must survive — got {kept['reason']!r}")
    assert "agreed_by" not in kept, (
        "there was no standing DECISION to agree with — got "
        f"agreed_by={kept.get('agreed_by')!r}")
    monkeypatch.delenv("TORTOISE_VALUE_GATE", raising=False)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
