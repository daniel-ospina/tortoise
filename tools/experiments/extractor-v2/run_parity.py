#!/usr/bin/env python3
"""Parity experiment (#1272): does P1+P2 (cheap compression) → P3 (capable
selection) preserve the logic vs P3 direct on raw — at lower cost?

Path A (direct):  P3 = deepseek-flash SUMMARY on the raw conversation
Path B (staged):  P1 = solar-pro4 "extract everything per ontology" (recall)
                  P2 = solar-pro4 "drop obvious noise" (precision-lite)
                  P3 = deepseek-flash SUMMARY on the P2 output (same model)

Parity = set containment: every decision/state/logic item in A must appear
in B (no loss); B may find more. Cost = measured tokens both paths.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.model_adapters import MODELS  # noqa: E402

P1_SYSTEM = """You are the RECALL extractor for an epistemic memory system.

Your ONLY job: emit EVERYTHING in the conversation that could be a durable
claim, decision, event, or entity — do NOT judge relevance, do NOT filter.
Over-emit. False positives are recoverable downstream; false negatives are
permanent loss.

Output ONE JSON object:
{
  "candidates": [
    {"kind": "decision|event|claim|process|entity|question",
     "text": "the utterance or assertion, verbatim-ish",
     "about": "the entity/topic it concerns, if any"}
  ]
}
Rules:
- A decision is a commitment with epistemic weight ("we decided X", "the
  ruling is Y", "default to Z") — include it even if unsure.
- An event is a past-perfective occurrence ("merged", "shipped", "fixed").
- A claim is a stative assertion ("X implies Y", "Z costs W", "the cause is Q").
- An entity is a named thing (product, feature, issue, pack, module,
  approach) — include every mention.
- "should"/"let me"/"I'll fix now" → process (work chatter) — STILL INCLUDE
  as kind=process (do not drop; the filter stage decides).
- Include questions that reveal intent or direction.
When in doubt: INCLUDE."""

P2_SYSTEM = """You are the NOISE FILTER for an epistemic memory system.

The input is a recall-extraction (over-emitted, deliberately noisy). Drop ONLY
OBVIOUS noise — content with zero epistemic value by definition:
- boilerplate / greetings / meta-instructions
- tool dumps, command output, stack traces, HTTP codes
- file paths, git refs, branch names, worktree names
- rate-limit chatter, "retrying", "took N seconds"
- pure procedural narration with no durable content ("let me verify X")

KEEP anything that could be a durable claim, decision, event, entity, or
intent — even if uncertain. When in doubt: KEEP.

Output ONE JSON object:
{
  "signal": [
    {"kind": "decision|event|claim|process|entity|question",
     "text": "...",
     "about": "..."}
  ]
}
Do not lose the logic — the decisions, claims, and argument structure must
survive. You are compressing signal, not judging importance."""


def _load(path: Path) -> str:
    """The raw transcript in harness format."""
    return path.read_text()


def _complete(model, system: str, user: str) -> str:
    import threading
    box = {}

    def _run():
        box["resp"] = model.complete(system=system, user=user)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=600)
    if t.is_alive():
        raise TimeoutError("model call exceeded 600s")
    return box.get("resp")


def _parse_json(raw: str) -> dict:
    import re
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("no JSON block")
    block = m.group(0)
    for cut in (None, -1, -2, -3, -5, -10, -20):
        try:
            return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("unparseable JSON")


def _summarize_p3(model, input_text: str, *, chunk_size: int = 12) -> dict:
    """P3: the SUMMARY_SYSTEM-style selection pass. Chunked for long inputs."""
    from tortoise.value_extractor import SUMMARY_SYSTEM, _parse_json as vp
    edus = [{"index": i, "role": "assistant", "text": line.strip()}
            for i, line in enumerate(input_text.splitlines())
            if line.strip() and not line.startswith("#")]
    merged = {"session": {"summary": ""}, "state": [], "decisions": [],
              "logic": [], "issues": [], "failed_chunks": 0}
    for start in range(0, len(edus), chunk_size):
        chunk = edus[start:start + chunk_size]
        user = "TRANSCRIPT:\n" + "\n".join(
            f"{e['index']}: {e['role']}: {e['text']}" for e in chunk)
        try:
            part = _parse_json(_complete(model, SUMMARY_SYSTEM, user))
        except Exception:
            merged["failed_chunks"] += 1
            continue
        for k in ("state", "decisions", "logic", "issues"):
            merged[k].extend(part.get(k, []) or [])
    return merged


def _p1_extract(model, transcript: str) -> list[dict]:
    out = _parse_json(_complete(model, P1_SYSTEM, transcript))
    return out.get("candidates", []) or []


def _p2_filter(model, candidates: list[dict]) -> list[dict]:
    user = json.dumps({"candidates": candidates}, indent=1)
    out = _parse_json(_complete(model, P2_SYSTEM, user))
    return out.get("signal", []) or []


def _norm(items: list[dict]) -> set[str]:
    """Normalize item content for set comparison (lowercase, strip)."""
    norm = set()
    for it in items or []:
        for key in ("content", "point", "name", "text"):
            if it.get(key):
                norm.add(str(it[key]).strip().lower()[:120])
    return norm


def _tokens(model) -> dict:
    return {"in": getattr(model, "last_prompt_tokens", 0),
            "out": getattr(model, "last_completion_tokens", 0)}


def main() -> None:
    transcript = _load(Path("tests/eval/w-1272/w-design-bounded.txt"))
    solar = MODELS["solar-pro4"]()
    solar.max_tokens = 4000
    flash = MODELS["deepseek-flash"]()
    flash.max_tokens = 4000

    runs = 3
    report = {"runs": []}
    for i in range(runs):
        print(f"=== RUN {i} ===", flush=True)
        # Path A: direct — flash SUMMARY on raw
        t0 = time.time()
        a = _summarize_p3(flash, transcript)
        a_tok = _tokens(flash)
        a_secs = round(time.time() - t0)

        # Path B: staged — solar P1 → solar P2 → flash P3
        t0 = time.time()
        cands = _p1_extract(solar, transcript)
        p1_tok = _tokens(solar)
        sig = _p2_filter(solar, cands)
        p2_tok = _tokens(solar)
        p2_text = "\n".join(f"{s.get('kind','')}: {s.get('text','')}"
                            for s in sig)
        b = _summarize_p3(flash, p2_text)
        p3_tok = _tokens(flash)
        b_secs = round(time.time() - t0)

        a_dec, a_state, a_logic = (_norm(a.get("decisions")), _norm(a.get("state")),
                                   _norm(a.get("logic")))
        b_dec, b_state, b_logic = (_norm(b.get("decisions")), _norm(b.get("state")),
                                   _norm(b.get("logic")))
        loss_dec = a_dec - b_dec
        loss_state = a_state - b_state
        loss_logic = a_logic - b_logic
        gain_dec = b_dec - a_dec

        run = {
            "run": i,
            "A": {"decisions": len(a.get("decisions", [])), "state": len(a.get("state", [])),
                  "logic": len(a.get("logic", [])), "tokens_in": a_tok["in"],
                  "tokens_out": a_tok["out"], "seconds": a_secs},
            "B": {"decisions": len(b.get("decisions", [])), "state": len(b.get("state", [])),
                  "logic": len(b.get("logic", [])), "p1_in": p1_tok["in"], "p1_out": p1_tok["out"],
                  "p2_in": p2_tok["in"], "p2_out": p2_tok["out"], "p3_in": p3_tok["in"],
                  "p3_out": p3_tok["out"], "seconds": b_secs},
            "loss_decisions": sorted(loss_dec),
            "loss_state": sorted(loss_state),
            "loss_logic": sorted(loss_logic),
            "gain_decisions": sorted(gain_dec),
        }
        report["runs"].append(run)
        print(json.dumps({k: run[k] for k in ("run", "A", "B")}), flush=True)
        print(f"  loss: dec={len(loss_dec)} state={len(loss_state)} logic={len(loss_logic)} "
              f"| gain_dec={len(gain_dec)}", flush=True)

    Path("/tmp/parity-report.json").write_text(json.dumps(report, indent=2))
    print("\nreport -> /tmp/parity-report.json")


if __name__ == "__main__":
    main()
