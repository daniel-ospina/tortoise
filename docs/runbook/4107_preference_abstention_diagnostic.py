#!/usr/bin/env python3
"""#4107 diagnostic — is the answer-bearing turn for ``d6233ab6`` present in
the reader's context, and does any prompt variant change the abstention?

⚠️ THIS IS A DIAGNOSTIC, NOT THE RULER. The ruler is
``tools/ask_shape_rate.py`` (the D3 answer-shape instrument, #4064). This
script changes nothing about the instrument's legs, thresholds, fixture,
reader pin or pre-registered rule — it reads the SAME retrieval and the SAME
lane the instrument reads and answers ONE question about one failure:

  ``d6233ab6`` ("…would it be a good idea to attend my high school
  reunion?") failed L1 (abstention) in the 2026-09-18 receipt while its
  answer-bearing turn was in the reader's context. #4107's discipline is
  CHARACTERISE BEFORE CHANGING THE PROMPT, so this script establishes, not
  assumes:

   1. PRESENCE — the fused rank of the gold turn ``answer_b0fac439_t2`` in
      the ask lane's pool, the distinct session ids the assembled evidence
      carries, and the turn's exact RENDERED BLOCK. Deterministic; NO paid
      reader call (the reader is a capture stub, so the exact evidence the
      lane would render is captured without spending a completion).
   2. THE EMITTED PROMPT — ``question_type`` as the lane resolved it
      (``detect_question_type``) and the system prompt that follows, with
      its sha256. This is what makes the claim "the preference fragment
      covers this" checkable rather than assumed: ``system_prompt_for(None)``
      carries the universal clause but NOT ``_PREFERENCE_FRAGMENT``.
   3. BEFORE/AFTER (``--replay``, PAID) — the shipped system prompt and each
      candidate variant are run through the SAME frozen evidence with the
      SAME pinned reader and the SAME call shape, so a prompt change is
      recorded as a read-path behaviour change with before/after output on
      one context.

Usage:
  python3 docs/runbook/4107_preference_abstention_diagnostic.py [--replay] [--out PATH]

``--replay`` spends real completions on the pinned reader (deepseek-direct /
deepseek-v4-flash). Without it the script is deterministic, free, and still
records presence + the emitted prompt.

The reader pin is the instrument's own (``ask_shape_rate.pinned_reader_env`` +
``assert_reader_pin``): the other provider keys are narrowed out and the built
reader is asserted to be the production ``RoutingModel`` on exactly
``deepseek-direct``. A run whose reader is not the pin is refused here too — a
diagnostic that silently measured a different reader would be worse than no
diagnostic. ``tree_head`` is recorded for provenance; this diagnostic does not
carry the instrument's own pre-registered tree pin (it is not the ruler).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools import ask_shape_rate as asr  # noqa: E402
from tools.ask_spotcheck import _seed_memory, _to_iso_date  # noqa: E402
from tortoise.retrieval import resolve_ask_retrieval_caps  # noqa: E402

QID = "d6233ab6"
GOLD_TURN = "answer_b0fac439_t2"
#: The verbatim head of the gold turn's content (fixture, has_answer=True) —
#: the same window ``_turn_present`` probes. A reworded fixture fails the
#: presence assertion rather than silently reporting a different turn.
GOLD_TURN_HEAD = (
    "I still remember the happy high school experiences such as being part of the debate team"
)

#: Candidate prompt variants for ``--replay``. Keyed by a stable label so the
#: receipt is auditable. ``shipped_generic`` is ``system_prompt_for(None)`` —
#: what the lane actually emitted for this question. The others are the two
#: hypotheses #4107 names: route to the preference fragment, or add a
#: targeted advice sentence (the option (b) candidate).
from tortoise.reader import system_prompt_for  # noqa: E402

#: The advice/opinion sentence. Deliberately NOT in the shipped prompt — this
#: diagnostic measures whether the gap is reachable, not whether it should be
#: landed (#4107: one sample does not justify a universal prompt change; a
#: battery does — filed separately).
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


def _hid(h: dict) -> str:
    return str(h.get("id") or h.get("point_id") or h.get("pointId") or h.get("node_id") or "")


def _capture_stub_factory(captured: dict):
    """A reader transport that answers with a placeholder and records the
    exact ``(system, user)`` the lane would have sent.

    Installed over ``tortoise.ask_lane._ask_reader_complete`` so the lane's
    OWN context assembly runs unchanged (retrieval → annotation → dedup →
    boost → rerank → assemble → render) while the paid completion is
    skipped. A stub that returns non-empty text also keeps the #2280
    escalation off the path (an empty output would trigger a second call)."""

    def _stub(model, *, system: str, user: str):
        captured.setdefault("calls", []).append(
            {"system": system, "user": user, "provider": getattr(model, "provider", None)}
        )
        return "[capture-stub]", 0

    return _stub


def measure(*, replay: bool) -> dict:
    questions, fixture_shape = asr.load_fixture_asserted()
    q = next(x for x in questions if x["question_id"] == QID)
    out: dict = {
        "question_id": QID,
        "question": q["question"],
        "question_type_fixture": q.get("question_type"),
        "gold_sessions": sorted(asr.gold_sessions(q)),
        "gold_turn": GOLD_TURN,
        "fixture": fixture_shape,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    # The gold turn is a fixture fact; assert it rather than trust the label.
    ids = q.get("haystack_session_ids") or []
    sessions = q.get("haystack_sessions") or []
    pos = {sid: i for i, sid in enumerate(ids)}
    gi = pos.get("answer_b0fac439")
    assert gi is not None, "gold session answer_b0fac439 not in the fixture"
    gold_turn = sessions[gi][int(GOLD_TURN.rsplit("_t", 1)[1])]
    assert gold_turn.get("has_answer") is True, f"{GOLD_TURN} is not the fixture's has_answer turn"
    assert GOLD_TURN_HEAD in " ".join((gold_turn.get("content") or "").split()), (
        "gold turn head drifted from the fixture"
    )
    out["gold_turn_content"] = gold_turn.get("content")

    with asr.pinned_reader_env() as penv:
        out["reader_env"] = penv
        out["embedder"] = asr.assert_embedder()
        # The pin is asserted, not assumed: a reader that is not the
        # production RoutingModel on deepseek-direct aborts the run.
        out["reader_pin"] = asr.assert_reader_pin()
        caps = resolve_ask_retrieval_caps()
        out["caps"] = caps

        db = asr._fresh_db("p4107")
        from tortoise.sdk import TortoiseSDK

        sdk = TortoiseSDK(db)
        try:
            _seed_memory(sdk, q)
            qdate = _to_iso_date(q.get("question_date") or "")

            # (1) deterministic fused rank of the gold turn in the ask pool.
            # The retrieval knobs are resolved exactly as the lane resolves
            # them, so the rank is measured on the SAME retrieval the lane
            # runs under — not on a hardcoded default that an ambient env
            # override would silently diverge from.
            from tortoise.retrieval import (
                ASK_FUSION_K_ENV,
                ASK_FUSION_WEIGHTS_ENV,
                ask_env_bool,
                ask_env_int,
                ask_env_weights,
            )

            leg_trace: list = []
            # The lane branches BEFORE retrieval on the connected-assembly flag;
            # when it fires, the lane never calls this retrieval and a fused
            # rank would describe a path the lane did not run. Assert it is
            # OFF and record the mode, so the rank's meaning is auditable.
            connected_assembly = ask_env_bool("TORTOISE_ASK_CONNECTED_ASSEMBLY", False)
            assert not connected_assembly, (
                "TORTOISE_ASK_CONNECTED_ASSEMBLY is ON — the lane would skip the "
                "legacy retrieval this rank measures; unset it and re-run"
            )
            out["lane_mode"] = {"connected_assembly": connected_assembly}
            hits = sdk.tortoise_fts_query(
                q["question"],
                limit=caps["limit"],
                pool_size=caps["pool_size"],
                include_terminal=True,
                leg_trace=leg_trace,
                keep_numeric=ask_env_bool("TORTOISE_ASK_NUMERIC_TOKENS", True),
                search_keys_prf=ask_env_bool("TORTOISE_ASK_SEARCH_KEYS_PRF", True),
                fusion_weights=ask_env_weights(ASK_FUSION_WEIGHTS_ENV, None),
                fusion_k=ask_env_int(ASK_FUSION_K_ENV, 60),
            )
            order = [_hid(h) for h in hits]
            out["pool"] = {
                "n_hits": len(order),
                "gold_turn_fused_rank": (
                    order.index(GOLD_TURN) + 1 if GOLD_TURN in order else "ABSENT"
                ),
                "top_12": order[:12],
            }

            # (2) the lane's own assembly + the emitted prompt — captured
            #     through a stub reader, so NO completion is spent.
            from tortoise import ask_lane as al

            captured: dict = {}
            saved_complete = al._ask_reader_complete
            al._ask_reader_complete = _capture_stub_factory(captured)
            al._reset_ask_reader_cache_for_tests()
            try:
                res = al.run_ask_lane(sdk, q["question"], question_date=qdate)
            finally:
                al._ask_reader_complete = saved_complete
                al._reset_ask_reader_cache_for_tests()
            evidence = res["evidence"]
            system = system_prompt_for(res["question_type"])
            # The stub captured the wire (system, user) the lane actually
            # sent. Re-derive it and assert equality, so "the emitted prompt"
            # is EVIDENCE rather than a parallel re-computation: if the lane
            # ever composes its prompt differently, this run fails loud
            # instead of reporting the old prompt with full confidence.
            wire = captured.get("calls") or []
            assert len(wire) == 1, f"expected exactly one reader call, captured {len(wire)}"
            assert wire[0]["system"] == system, (
                "the lane's wire system prompt differs from "
                "system_prompt_for(resolved question_type)"
            )
            out["emitted"] = {
                "question_type": res["question_type"],
                "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
                "system_prompt_captured_from_wire": True,
                "system_prompt_has_preference_fragment": "PREFERENCE INSTRUCTIONS" in system,
                "system_prompt_has_synthesis_sentence": "asks what the user prefers" in system,
                "system_prompt_has_derived_license": "asks for a derived value" in system,
                "context_tokens": res["context_tokens"],
                "retrieved_session_ids": res["retrieved_session_ids"],
                "evidence_len": len(evidence),
            }
            blocks = [b for b in evidence.split("\n\n") if GOLD_TURN_HEAD in b]
            out["presence"] = {
                "gold_turn_in_rendered_context": bool(blocks),
                "gold_turn_rendered_block": blocks[:1],
                "gold_answer_span": asr.longest_common_span(evidence, q["answer"], min_words=1),
            }
            out["presence"]["gold_answer_span_words"] = len(
                (out["presence"]["gold_answer_span"] or "").split()
            )

            if replay:
                out["replay"] = _replay(sdk, al, evidence, q, system)
        finally:
            sdk.close()
    return out


class _SpyReader:
    """A transparent proxy that records each ``complete()`` call's kwargs.

    The #2280 escalation retries an empty first call at a LARGER budget, so a
    variant that escalated would have run a different call shape than the
    default 500-token one — and the before/after comparison would be
    measuring a budget change, not the prompt change. ``_ask_reader_complete``
    never returns empty text, so its output cannot reveal that; the spy
    records the actual calls instead: ``escalated`` is true when any call
    carried an explicit ``max_tokens`` override."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def complete(self, *, system: str, user: str, max_tokens: int | None = None) -> str:
        self.calls.append({"explicit_max_tokens": max_tokens})
        return self._inner.complete(system=system, user=user, max_tokens=max_tokens)


def _replay(sdk, al, evidence, q, shipped_system) -> dict:
    """Run the shipped prompt and each candidate variant through the SAME
    frozen evidence, SAME pinned reader, SAME call shape. PAID."""
    from tortoise.reader import _looks_abstained, build_reader_user_message

    variants = {
        "shipped_generic": shipped_system,
        "with_preference_fragment": system_prompt_for("single-session-preference"),
        "with_advice_sentence": shipped_system + ADVICE_SENTENCE,
    }
    model = al._ask_reader_model(sdk)
    user = build_reader_user_message(evidence, q["question"])
    variants_out: dict = {}
    any_escalated = False
    try:
        for name, system in variants.items():
            spy = _SpyReader(model)
            raw, _ = al._ask_reader_complete(spy, system=system, user=user)
            ans = (raw or "").strip()
            escalated = any(c["explicit_max_tokens"] for c in spy.calls)
            any_escalated = any_escalated or escalated
            span = asr.longest_common_span(evidence, ans)
            variants_out[name] = {
                "answer": ans,
                "abstained": _looks_abstained(ans),
                "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
                "reader_calls": len(spy.calls),
                "escalated": escalated,
                "shared_span_with_evidence": span,
                "shared_span_words": len(span.split()) if span else 0,
            }
    finally:
        decr = getattr(model, "decr_inflight", None)
        if decr is not None:
            decr()
        # Close the cached reader client — the stub block resets too; a
        # surviving cache entry would leak the open client to process exit.
        al._reset_ask_reader_cache_for_tests()
    return {
        "provider": getattr(model, "provider", None),
        "model": getattr(model, "model", None),
        "note": (
            "every variant ran on ONE frozen rendered evidence with the "
            "SAME pinned reader and call shape (temperature 0, "
            "max_tokens 500) — a prompt change is a read-path behaviour "
            "change measured before/after on the same context"
        ),
        "any_variant_escalated_output_budget": any_escalated,
        "variants": variants_out,
    }


def main(argv: list[str] | None = None) -> int:
    asr._install_redacting_excepthook()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--replay",
        action="store_true",
        help="PAID: run the shipped prompt + candidate variants "
        "on the frozen evidence through the pinned reader",
    )
    ap.add_argument("--out", default=None, help="receipt JSON path (default: stdout)")
    args = ap.parse_args(argv)
    import subprocess

    head = subprocess.run(
        ["git", "-C", _REPO_ROOT, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    result = measure(replay=args.replay)
    # Provenance only. The instrument's pre-registered tree pin (`--pin-sha`)
    # belongs to the instrument; this diagnostic records the tree it measured
    # and does not claim that pin.
    result["tree_head"] = head
    text = json.dumps(result, indent=1, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print(f"receipt: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
