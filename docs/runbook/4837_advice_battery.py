#!/usr/bin/env python3
"""#4837 — the advice-shaped question battery: the class decision.

⚠️ THIS IS THE BATTERY (#4837), NOT THE RULER. The ruler is
``tools/ask_shape_rate.py`` (the D3 answer-shape instrument, #4064). This
harness exists because the D3 fixture is SHA-pinned and **a changed fixture is
a new instrument** (#4064), so the questions that decide a *universal
prompt-contract* change are measured here, on their own fixture, against the
SAME frozen reader pin.

What it decides (pre-registered below, before the run):

  #4107 characterised ONE advice-shaped L1 failure — ``d6233ab6``: the
  answer-bearing turn is in the reader's context, yet the pinned reader
  abstains, because the universal ``_ABSTRACTION_FRAGMENT`` holds a synthesis
  LICENSE and an asked-subject scoping GUARD in tension, and the model follows
  the guard. A candidate sentence flips that one sample. One sample is not a
  class, and a universal prompt append needs the class measured — plus the
  false-commit guard the #1775/#2027 clause exists to preserve.

  * ARM ``class`` (n=6) — advice-shaped question about the user's own life,
    where the asked EVENT is absent from the context and the user's relevant
    experiences / preferences / interests ARE present. #4107 says these
    should commit after the candidate and abstain before it.
  * ARM ``near_miss`` (n=4) — advice-shaped question where the asked subject
    is absent AND no relevant experience / preference / interest exists
    anywhere in the context. These must abstain BEFORE and AFTER. This is the
    guard arm the issue names as missing: the three ``_abs`` questions are
    factual, not advice-shaped, so they do not cover it.
  * ARM ``d3_abs`` (n=3) — the frozen D3 fixture's three ``_abs`` questions,
    READ from the fixture (never copied), as the issue's own guard.

Three variants are replayed on each item's ONE frozen rendered evidence, with
the SAME pinned reader and the SAME call shape (temperature 0, max_tokens
500), so a variant difference is a prompt difference, not a budget or
retrieval difference:

  V1 ``v1_shipped``          — ``system_prompt_for(resolved question_type)``.
  V2 ``v2_universal_advice`` — V1 + the #4107 ``ADVICE_SENTENCE`` verbatim.
  V3 ``v3_gated_fragment``   — a DETECTOR-GATED bidirectional advice fragment:
                                the fragment is emitted only when
                                ``is_advice_shaped(question)`` fires, and it
                                names BOTH directions (commit when the
                                context states the relevant material; abstain
                                when it does not). The "narrow" candidate.

PRE-REGISTERED DECISION RULE (frozen before the run — the fixture is
SHA-pinned in this file, so the questions cannot move after the answers are
seen):

  Validity: every ``class`` item must (a) render its ``relevant_needle`` in
  the assembled evidence and (b) render none of its ``forbidden_in_haystack``
  strings; every ``near_miss`` item must render none of its forbidden strings.
  An item failing either is INVALID and reported as such; if more than one
  ``class`` item is INVALID the run is **VOID** (the class denominator moved).

  C  = #{class items: V1 abstains AND V2 commits AND V2 shares >= 4
       contiguous verbatim words with the evidence}
  G2 = #{guard items (near_miss + d3_abs): V2 does NOT abstain}
  N3 = the same count for V3, G3 = the same guard count for V3.

  • **LAND** (universal append) iff C >= 5 and G2 == 0.
  • **NARROW** (detector-gated fragment) iff C >= 5 and G2 >= 1 and
    N3 >= 5 and G3 == 0.
  • **REJECT** otherwise — including C <= 4, which says the effect does not
    reproduce beyond the single #4107 sample and the universal append is not
    justified.

The reader is the production ``RoutingModel`` on exactly ``deepseek-direct`` /
``deepseek/deepseek-v4-flash``, asserted (``ask_shape_rate.assert_reader_pin``)
— never a mock: a MockReader answers from the evidence and would make the
commit legs GREEN by construction. The embedder is asserted too (#2985): a
keyword-only run cannot see the dense leg and is VOID, not low.

Usage:
  python3 docs/runbook/4837_advice_battery.py                 # free: evidence + emitted prompts
  python3 docs/runbook/4837_advice_battery.py --replay        # PAID: the variant comparison
  python3 docs/runbook/4837_advice_battery.py --replay --out docs/runbook/4837-advice-battery-receipt.json
  python3 docs/runbook/4837_advice_battery.py --relabel docs/runbook/4837-advice-battery-receipt.json

MEASURED (2026-10-13, tree ``a91179c50``, receipt
``docs/runbook/4837-advice-battery-receipt.json``; the class decision and its
full per-item before/after are in ``docs/runbook/4837-advice-battery.md``):

  • pre-registered metric (the product label): **REJECT** — C=2/6.
  • sensitivity (Phase-2 refusal vocabulary, ``--relabel``): **REJECT** —
    C=4/6, N3=3/6. The guard is clean in every reading (G1=G2=G3=0/7), so the
    verdict is not a label artefact and not a guard leak.

⚠️ The class arm surfaced a SECOND defect while measuring: the product's
``_looks_abstained`` (the field the D3 instrument's L1 leg reads) has no
Phase-2 refusal VERBS — "The context doesn't say whether X. It does show Y."
is scored as a COMMIT. Two battery items are that shape. That defect is filed
separately; ``--relabel`` exists so this battery's verdict is checked under a
corrected vocabulary rather than resting on the broken one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools import ask_shape_rate as asr  # noqa: E402
from tools.ask_spotcheck import _seed_memory, _to_iso_date  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

#: The battery fixture, and its SHA. The SHA is the PRE-REGISTRATION: the
#: questions were frozen (and this pin written) before the live run, so a
#: post-hoc question edit fails the run instead of silently redefining the
#: class.
FIXTURE_PATH = os.path.join(_REPO_ROOT, "docs", "runbook", "4837-advice-battery.json")
FIXTURE_SHA256 = "bd7768ddb32b27c2b1e352acd87572fde51574256cb8dc879aab2aea9e421045"

#: The frozen D3 fixture — READ for the ``d3_abs`` guard arm, whose content is
#: asserted against the instrument's own pin (``ask_shape_rate.FIXTURE_SHA256``)
#: rather than copied, so the guard measures the instrument's real questions.
D3_FIXTURE_PATH = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "ask_spotcheck_composition.json")

CLASS_ARM = "class"
NEAR_MISS_ARM = "near_miss"
D3_ABS_ARM = "d3_abs"
GUARD_ARMS = (NEAR_MISS_ARM, D3_ABS_ARM)

#: Pre-registered thresholds (see the module docstring). Frozen before the run.
CLASS_FLIP_MIN = 5       # C, of 6 class items
CLASS_SIZE = 6
BACKSTOP = 0             # G2 / G3 must be 0
GROUND_MIN_WORDS = 4     # the D3 instrument's L3 lexical floor (SPAN_MIN_WORDS)

#: The #4107 candidate sentence, VERBATIM (byte-identical to the value in
#: ``docs/runbook/4107_preference_abstention_diagnostic.py``, asserted by
#: ``tests/test_reader_4837_advice_battery.py`` so the battery can never
#: measure a paraphrased candidate).
ADVICE_SENTENCE = (
    "\n\nADVICE QUESTIONS: when the question asks for advice or an opinion "
    "about the user's own life, plans, or decisions (for example 'would it "
    "be a good idea to…' or 'should I…'), and the context states the user's "
    "relevant experiences, preferences, or interests, answer with advice "
    "grounded in those memories. The specific event or plan the question "
    "names need not be mentioned in the context; do not abstain, and do not "
    "say the context lacks information, merely because that event or plan "
    "is not mentioned."
)

#: V3 — the NARROW candidate: a bidirectional advice fragment, emitted only
#: through the detector gate below. Where V2 licenses a commit, V3 states both
#: directions (commit on relevant material, abstain on its absence), so the
#: guard arm is a fair test of it rather than a leak surface it never
#: addresses.
ADVICE_FRAGMENT_BIDIRECTIONAL = (
    "\n\nADVICE QUESTIONS: this question asks for advice or an opinion about "
    "the user's own life, plans, or decisions. First decide whether the "
    "context states the user's relevant experiences, preferences, or "
    "interests for the topic the question asks about.\n"
    "- If the context states them, the answer is present: answer with advice "
    "grounded in those memories. The specific event or plan the question "
    "names need not be mentioned in the context — do not abstain, and do not "
    "say the context lacks information, merely because that event or plan is "
    "not mentioned.\n"
    "- If the context states no relevant experience, preference, or interest "
    "for that topic, the asked information is absent: abstain (Phase 2)."
)

#: V3's gate. This is the #2009 advice-form candidate cue set that was
#: MEASURED but NOT added (+0.050 mapped agreement; ``tortoise/reader.py``
#: ``_SSP_PATTERNS``), plus the two forms the #4107 candidate sentence names
#: explicitly. The gate is deliberately SHAPE-ONLY: it cannot distinguish a
#: class item from a near-miss item (both are advice-shaped), which is exactly
#: why the guard arm is measured rather than assumed.
ADVICE_GATE_PATTERNS = (
    re.compile(r"\bwould (that|this|it) be a good idea\b"),
    re.compile(r"\b(would|could) it be a good idea\b"),
    re.compile(r"\b(should|shall) i\b"),
    re.compile(r"\b(can|could) you (recommend|suggest)\b"),
    re.compile(r"\bany (tips|suggestions|advice)\b"),
    re.compile(r"\b(do|would) you (think|have)\b"),
    re.compile(r"\bwhat should\b"),
    re.compile(r"\bwhich (option|one)\b"),
    re.compile(r"\bgood idea (for me )?to\b"),
)


#: SUPPLEMENTARY Phase-2 refusal vocabulary — the refusal VERBS. The
#: product's ``_ABSTAINED_PHRASES`` (``tortoise/reader.py``) carries the
#: containment family ("does not contain", "not mention", "no information",
#: …) but not the say/state/indicate family. ``_looks_abstained`` is the
#: field the D3 instrument's L1 leg reads, so a genuine Phase-2 abstention
#: written as "The context doesn't say whether X. It does show Y." is scored
#: there as a COMMIT.
#:
#: ⛔ This is NOT the decision metric. The pre-registered rule is defined on
#: the PRODUCT label (``_looks_abstained``) and must stay that way — the rule
#: was frozen before the run. This vocabulary exists only for the SENSITIVITY
#: readout (``--relabel``), so the recorded verdict can be checked for being
#: an artefact of a label this battery itself caught.
_PHASE2_REFUSAL_FORMS: tuple[re.Pattern, ...] = (
    re.compile(r"\b(does not|doesn't|doesnt|do not|don't|dont|cannot|can not|can't) "
               r"(say|state|indicate|specify|tell)\b"),
    re.compile(r"\b(says|states) nothing about\b"),
)


def strict_abstention_label(answer: str | None) -> bool:
    """The product label OR a Phase-2 refusal written with a refusal VERB.

    Sensitivity readout only — never the pre-registered decision metric.
    """
    from tortoise.reader import _looks_abstained

    if _looks_abstained(answer):
        return True
    low = " ".join(str(answer or "").split()).lower()
    return any(p.search(low) for p in _PHASE2_REFUSAL_FORMS)


def is_advice_shaped(question: str | None) -> bool:
    """V3's detector gate — shape only, no context and no expectations.

    ⛔ Not a claim that the question IS advice-shaped, only that the gate
    fires: the gate's job is to decide whether V3's fragment is emitted at
    all, and the guard arm measures whether that is safe.
    """
    if not question or not str(question).strip():
        return False
    q = str(question).lower()
    return any(p.search(q) for p in ADVICE_GATE_PATTERNS)


def variants_for(question: str, shipped_system: str) -> dict[str, dict]:
    """The three pre-registered variants for one item.

    ``v3_gated_fragment`` is the shipped prompt where the gate does not fire,
    so V3 never reaches a question the detector does not route — its coverage
    is exactly the gate.
    """
    gated = is_advice_shaped(question)
    return {
        "v1_shipped": {"system": shipped_system, "gate_fires": False},
        "v2_universal_advice": {
            "system": shipped_system + ADVICE_SENTENCE, "gate_fires": False},
        "v3_gated_fragment": {
            "system": (shipped_system + ADVICE_FRAGMENT_BIDIRECTIONAL
                       if gated else shipped_system),
            "gate_fires": gated,
        },
    }


def variant_record(*, answer: str, spec: dict, shipped: str, span: str,
                   reader_calls: int, escalated: bool) -> dict:
    """The per-variant receipt row. PURE, so the committed receipt's schema is
    pinned hermetically.

    ⛔ ``abstained_strict`` is deliberately NOT written here. The
    pre-registered metric is the PRODUCT label (``abstained``); the strict
    vocabulary exists only for the ``--relabel`` sensitivity readout, where
    :func:`relabel` recomputes it from the answer. A record-time
    ``abstained_strict`` made the committed instrument and the committed
    receipt disagree on schema (#8407 review).
    """
    from tortoise.reader import _looks_abstained

    return {
        "answer": answer,
        "abstained": _looks_abstained(answer),
        "system_prompt_sha256": hashlib.sha256(spec["system"].encode()).hexdigest(),
        "gate_fires": spec["gate_fires"],
        "fragment_emitted": spec["system"] != shipped,
        "reader_calls": reader_calls,
        "escalated": escalated,
        "shared_span_with_evidence": span,
        "shared_span_words": len(span.split()) if span else 0,
        "grounded": bool(span),
    }


def _hid(h: dict) -> str:
    return str(h.get("id") or h.get("point_id") or h.get("pointId") or h.get("node_id") or "")


def _capture_stub_factory(captured: dict):
    """A reader transport that records the exact ``(system, user)`` the lane
    would have sent and answers with a placeholder — the lane's OWN assembly
    runs unchanged (retrieval → annotation → dedup → boost → rerank →
    assemble → render) while the paid completion is skipped, and a non-empty
    stub keeps the #2280 escalation off the path."""

    def _stub(model, *, system: str, user: str):
        captured.setdefault("calls", []).append(
            {"system": system, "user": user, "provider": getattr(model, "provider", None)})
        return "[capture-stub]", 0

    return _stub


class _SpyReader:
    """A transparent proxy recording each ``complete()`` call's kwargs, so a
    variant that escalated its output budget (#2280) is visible: an escalated
    variant ran a different call shape, and a before/after on it would be
    measuring a budget change."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def complete(self, *, system: str, user: str, max_tokens: int | None = None) -> str:
        self.calls.append({"explicit_max_tokens": max_tokens})
        return self._inner.complete(system=system, user=user, max_tokens=max_tokens)


def load_battery() -> list[dict]:
    """The battery fixture, SHA-asserted, plus the D3 ``_abs`` questions read
    from the frozen instrument fixture (never copied)."""
    with open(FIXTURE_PATH, "rb") as fh:
        raw = fh.read()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != FIXTURE_SHA256:
        raise SystemExit(
            "4837_advice_battery: FIXTURE DRIFT — the battery was pre-registered "
            f"with SHA {FIXTURE_SHA256}\n  actual   {actual}\n"
            "A changed fixture is a new instrument: re-register it (update the "
            "pin in this file) and RE-MEASURE. The pre-registered decision "
            "rule cannot be applied to questions chosen after the answers.")
    items = json.loads(raw)["questions"]
    d3 = asr.load_fixture_asserted()[0]  # asserts ask_shape_rate.FIXTURE_SHA256
    for q in d3:
        if "_abs" not in (q.get("question_id") or ""):
            continue
        items.append({
            "question_id": q["question_id"],
            "question": q["question"],
            "question_type": q.get("question_type"),
            "question_date": q.get("question_date"),
            "battery_arm": D3_ABS_ARM,
            "relevant_needle": None,
            "forbidden_in_haystack": [],
            "haystack_dates": q.get("haystack_dates") or [],
            "haystack_session_ids": q.get("haystack_session_ids") or [],
            "haystack_sessions": q.get("haystack_sessions") or [],
            "note": "read from the frozen D3 fixture (ask_shape_rate.FIXTURE_SHA256)",
        })
    return items


def _run_item(sdk, al, item: dict, *, replay: bool) -> dict:
    from tortoise.reader import build_reader_user_message, system_prompt_for

    _seed_memory(sdk, item)
    qdate = _to_iso_date(item.get("question_date") or "")

    captured: dict = {}
    saved = al._ask_reader_complete
    al._ask_reader_complete = _capture_stub_factory(captured)
    al._reset_ask_reader_cache_for_tests()
    try:
        res = al.run_ask_lane(sdk, item["question"], question_date=qdate)
    finally:
        al._ask_reader_complete = saved
        al._reset_ask_reader_cache_for_tests()

    evidence = res["evidence"]
    qtype = res["question_type"]
    shipped = system_prompt_for(qtype)
    wire = captured.get("calls") or []
    if len(wire) != 1:
        raise SystemExit(f"{item['question_id']}: expected 1 reader call, captured {len(wire)}")
    if wire[0]["system"] != shipped:
        raise SystemExit(
            f"{item['question_id']}: the lane's wire system prompt differs from "
            "system_prompt_for(resolved question_type) — the emitted-prompt claim "
            "would be a parallel re-computation, not evidence")

    normalized = " ".join(evidence.split()).lower()
    needle = item.get("relevant_needle")

    def _present(s: str) -> bool:
        # WORD-BOUNDARY match: a substring test would flag "sea" inside
        # "season" and "run" inside another word, and an over-flagged
        # forbidden needle silently retires a guard control.
        return re.search(r"\b" + re.escape(" ".join(str(s).split()).lower()) + r"\b",
                         normalized) is not None

    needle_present = None if not needle else _present(needle)
    forbidden_found = [f for f in (item.get("forbidden_in_haystack") or []) if _present(f)]
    seeded = [s for s in (item.get("haystack_session_ids") or []) if s]
    rendered = set(res["retrieved_session_ids"] or [])
    all_rendered = all(s in rendered for s in seeded)
    rows = {
        "evidence_len": len(evidence),
        "context_tokens": res["context_tokens"],
        "retrieved_session_ids": res["retrieved_session_ids"],
        "question_type_resolved": qtype,
        "shipped_system_prompt_sha256": hashlib.sha256(shipped.encode()).hexdigest(),
        "relevant_needle": needle,
        "relevant_needle_in_evidence": needle_present,
        "forbidden_found_in_evidence": forbidden_found,
        "haystack_session_ids": seeded,
        "haystack_all_sessions_rendered": all_rendered,
        "gate_fires": is_advice_shaped(item["question"]),
    }
    invalid: list[str] = []
    if item["battery_arm"] == CLASS_ARM and needle_present is not True:
        invalid.append("relevant_needle_absent")
    if forbidden_found:
        invalid.append("forbidden_present:" + ",".join(forbidden_found))
    if item["battery_arm"] == NEAR_MISS_ARM and not all_rendered:
        # The guard control's claim is about the CONTEXT: "no relevant
        # experience or preference exists here". A partially-rendered
        # haystack makes that a claim about an unseen tail, so the item is
        # retired rather than counted.
        invalid.append("haystack_not_fully_rendered")
    rows["valid"] = not invalid
    rows["invalid_reason"] = "; ".join(invalid) if invalid else None

    if replay:
        model = al._ask_reader_model(sdk)
        user = build_reader_user_message(evidence, item["question"])
        variants: dict = {}
        any_escalated = False
        try:
            for name, spec in variants_for(item["question"], shipped).items():
                spy = _SpyReader(model)
                raw, _ = al._ask_reader_complete(spy, system=spec["system"], user=user)
                ans = (raw or "").strip()
                escalated = any(c["explicit_max_tokens"] for c in spy.calls)
                any_escalated = any_escalated or escalated
                span = asr.longest_common_span(evidence, ans)
                variants[name] = variant_record(
                    answer=ans, spec=spec, shipped=shipped, span=span,
                    reader_calls=len(spy.calls), escalated=escalated)
        finally:
            decr = getattr(model, "decr_inflight", None)
            if decr is not None:
                decr()
            al._reset_ask_reader_cache_for_tests()
        rows["replay"] = {
            "provider": getattr(model, "provider", None),
            "model": getattr(model, "model", None),
            "note": ("every variant ran on ONE frozen rendered evidence with the SAME "
                     "pinned reader and call shape (temperature 0, max_tokens 500)"),
            "any_variant_escalated_output_budget": any_escalated,
            "variants": variants,
        }
    return rows


def decide(items: list[dict], *, metric: str = "abstained") -> dict:
    """The pre-registered decision rule. Pure: reads the rows, applies the
    thresholds frozen in the module docstring.

    ``metric`` selects the abstention field. The PRE-REGISTERED metric is the
    product label (``"abstained"``); ``"abstained_strict"`` exists only for
    the ``--relabel`` sensitivity readout.
    """
    class_rows = [r for r in items if r["battery_arm"] == CLASS_ARM]
    guard_rows = [r for r in items if r["battery_arm"] in GUARD_ARMS]
    invalid_class = [r["question_id"] for r in class_rows if not r["valid"]]
    replays = [r for r in items if "replay" in r]
    if len(replays) != len(items):
        return {"verdict": "NOT_RUN",
                "reason": "--replay not run: the variant comparison is unmeasured"}

    def v(row, name):
        return row["replay"]["variants"][name]

    def did_abstain(row, name) -> bool:
        return bool(v(row, name)[metric])

    def flipped(row):
        return (did_abstain(row, "v1_shipped") is True
                and did_abstain(row, "v2_universal_advice") is False
                and v(row, "v2_universal_advice")["grounded"] is True)

    def flipped3(row):
        return (did_abstain(row, "v1_shipped") is True
                and did_abstain(row, "v3_gated_fragment") is False
                and v(row, "v3_gated_fragment")["grounded"] is True)

    C = sum(1 for r in class_rows if r["valid"] and flipped(r))
    N3 = sum(1 for r in class_rows if r["valid"] and flipped3(r))
    G2 = sum(1 for r in guard_rows if r["valid"]
             and did_abstain(r, "v2_universal_advice") is False)
    G3 = sum(1 for r in guard_rows if r["valid"]
             and did_abstain(r, "v3_gated_fragment") is False)
    G1 = sum(1 for r in guard_rows if r["valid"]
             and did_abstain(r, "v1_shipped") is False)

    out = {
        "metric": metric,
        "C_class_flips_v2": C,
        "N3_class_flips_v3": N3,
        "G1_shipped_guard_false_commits": G1,
        "G2_v2_guard_false_commits": G2,
        "G3_v3_guard_false_commits": G3,
        "class_size_valid": sum(1 for r in class_rows if r["valid"]),
        "class_size": len(class_rows),
        "invalid_class_items": invalid_class,
        "guard_size_valid": sum(1 for r in guard_rows if r["valid"]),
        "guard_size": len(guard_rows),
        "thresholds": {
            "CLASS_FLIP_MIN": CLASS_FLIP_MIN,
            "CLASS_SIZE": CLASS_SIZE,
            "BACKSTOP": BACKSTOP,
            "GROUND_MIN_WORDS": GROUND_MIN_WORDS,
        },
    }
    if len(invalid_class) > 1:
        out.update(verdict="VOID",
                   reason=(f"{len(invalid_class)} class items are INVALID — the "
                           "pre-registered class denominator moved (max 1 allowed)"))
        return out
    if C >= CLASS_FLIP_MIN and G2 == BACKSTOP:
        out.update(verdict="LAND_UNIVERSAL",
                   reason=(f"C={C}/{len(class_rows)} >= {CLASS_FLIP_MIN} and "
                           f"G2={G2} — the class reproduces AND the candidate does "
                           "not false-commit the guard: the #4107 ADVICE_SENTENCE "
                           "lands as a universal append."))
    elif C >= CLASS_FLIP_MIN and G2 > BACKSTOP and N3 >= CLASS_FLIP_MIN and G3 == BACKSTOP:
        out.update(verdict="NARROW_GATED",
                   reason=(f"C={C} >= {CLASS_FLIP_MIN} but G2={G2} > 0 — the universal "
                           f"append leaks the guard; the detector-gated bidirectional "
                           f"fragment holds (N3={N3}, G3={G3})."))
    else:
        out.update(verdict="REJECT",
                   reason=(f"C={C} (needs >= {CLASS_FLIP_MIN}), G2={G2}, "
                           f"N3={N3}, G3={G3} — no measured variant both reproduces "
                           "the class and survives the guard."))
    return out


def measure(*, replay: bool) -> dict:
    items = load_battery()
    out: dict = {
        "battery": "4837-advice-battery",
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture": {"path": os.path.relpath(FIXTURE_PATH, _REPO_ROOT),
                    "sha256": FIXTURE_SHA256},
        "d3_fixture": {"path": os.path.relpath(D3_FIXTURE_PATH, _REPO_ROOT),
                       "sha256": asr.FIXTURE_SHA256,
                       "questions": [i["question_id"] for i in items
                                     if i["battery_arm"] == D3_ABS_ARM]},
        "preregistered": {
            "advice_sentence_sha256": hashlib.sha256(ADVICE_SENTENCE.encode()).hexdigest(),
            "bidirectional_fragment_sha256": hashlib.sha256(
                ADVICE_FRAGMENT_BIDIRECTIONAL.encode()).hexdigest(),
            "rule": ("LAND iff C>=5 and G2==0; NARROW iff C>=5 and G2>0 and N3>=5 "
                     "and G3==0; else REJECT/VOID (see the module docstring)"),
        },
    }
    with asr.pinned_reader_env() as penv:
        out["reader_env"] = penv
        out["embedder"] = asr.assert_embedder()
        out["reader_pin"] = asr.assert_reader_pin()
        from tortoise import ask_lane as al

        rows = []
        for item in items:
            # ONE GRAPH PER ITEM. The battery seeds 13 haystacks; a shared
            # store would make item K's retrieval see items 1..K-1 too, so the
            # rendered evidence would not be the item's own context and both
            # the presence precondition and the near-miss guard would be
            # measuring the wrong thing (caught on the first run: the c3
            # evidence carried the c1 sessions).
            db = asr._fresh_db(f"p4837_{item['question_id']}")
            sdk = TortoiseSDK(db)
            try:
                row = {"question_id": item["question_id"],
                       "battery_arm": item["battery_arm"],
                       "question": item["question"],
                       "note": item.get("note"),
                       "expected_advice_basis": item.get("expected_advice_basis")}
                row.update(_run_item(sdk, al, item, replay=replay))
            finally:
                sdk.close()
            rows.append(row)
            mark = "VALID" if row["valid"] else f"INVALID({row['invalid_reason']})"
            print(f"[{row['battery_arm']:9s}] {row['question_id']:16s} {mark}",
                  file=sys.stderr)
            sys.stderr.flush()
    out["items"] = rows
    out["decision"] = decide(rows)
    return out


def relabel(receipt_path: str) -> dict:
    """SENSITIVITY readout on a COMMITTED receipt: recompute every variant's
    abstention with the Phase-2 refusal vocabulary and re-apply the SAME
    pre-registered rule under that metric.

    Why it exists: the battery's class arm produced items whose answer is a
    genuine Phase-2 refusal ("The context doesn't say whether X. It does show
    Y.") which the PRODUCT label scores as a commit. A verdict that holds
    under the product metric but flips under the corrected one would be an
    artefact; this readout is how that is checked instead of asserted. It
    does NOT change the recorded verdict — the receipt's own ``decision``
    block is the pre-registered result.
    """
    with open(receipt_path) as fh:
        data = json.loads(fh.read())
    items = data["items"]
    for row in items:
        for variant in row.get("replay", {}).get("variants", {}).values():
            variant["abstained_strict"] = strict_abstention_label(variant["answer"])
    return {
        "receipt": receipt_path,
        "note": ("SENSITIVITY ONLY — the receipt's own decision block is the "
                 "pre-registered result, measured with the product label."),
        "product_label_verdict": data.get("decision", {}).get("verdict"),
        "strict_label_decision": decide(items, metric="abstained_strict"),
    }


def main(argv: list[str] | None = None) -> int:
    asr._install_redacting_excepthook()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replay", action="store_true",
                    help="PAID: replay every variant on each item's frozen evidence "
                         "through the pinned reader")
    ap.add_argument("--out", default=None, help="receipt JSON path (default: stdout)")
    ap.add_argument("--relabel", default=None, metavar="RECEIPT",
                    help="SENSITIVITY: re-apply the pre-registered rule to a committed "
                         "receipt using the Phase-2 refusal vocabulary (does not re-run "
                         "the reader, does not change the recorded verdict)")
    args = ap.parse_args(argv)
    if args.relabel:
        print(json.dumps(relabel(args.relabel), indent=1, ensure_ascii=False))
        return 0
    import subprocess

    head = subprocess.run(["git", "-C", _REPO_ROOT, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    result = measure(replay=args.replay)
    result["tree_head"] = head
    text = json.dumps(result, indent=1, ensure_ascii=False, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print(f"receipt: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
