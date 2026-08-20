#!/usr/bin/env python3
"""Test: solar-pro4 pre-processing -> flash S1, vs flash S1 direct.

The idea: a cheap model cleans the raw conversation first (keeps the story
arc + entities + logic, strips process noise), then flash narrates from the
clean signal. Compare quality + cost.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.model_adapters import MODELS  # noqa: E402, RUF100

CLEAN_SYSTEM = """You are the SIGNAL CLEANER for an epistemic memory system.

Read the whole conversation. Produce a CLEANED version that keeps the story
arc and the logic, and removes process noise.

KEEP:
- the narrative of what happened, in order, and why (the story arc)
- the objects/subjects/events/points and how they connect (the 3 layers:
  State = subjects+objects, Epistemic = the logic/points, Events = decisions/
  discoveries)
- durable claims, decisions, reasoning, tradeoffs, mitigations

REMOVE (process noise):
- commit hashes, test counts, "PR #N opened/merged", "review gate found N
  findings", "rebase", "issue emerged", "build step", tool calls, load
  averages, elapsed times, any mechanical work detail

Do NOT lose the logic: the decisions and the reasoning behind them must
survive intact. You are compressing signal, not summarizing away meaning.

Output ONE JSON object:
{
  "cleaned": "the cleaned narrative (prose, story arc preserved)",
  "entities": [{"name": "...", "kind": "<ontology kind>", "role": "state|epistemic|event"}],
  "points": [{"text": "...", "supports": "IMPL|NAND|MITIGATES", "about": "<entity name>"}]
}"""

S1_SYSTEM = """You are the STORY SUMMARIZER for the company/product epistemic memory.

Read the input. Produce a NARRATIVE that captures what CHANGED about the
world we operate in — the state of the product, the team, the domain — and
WHY it changed, at the level of durable meaning, not mechanics.

Focus on TWO layers, in this order:
1. STATE (primary): subjects and objects and how they changed — an approach
   adopted, a ruling made, an option chosen or discarded. What REMAINS TRUE.
2. EPISTEMIC (primary): the LOGIC — points that support (IMPL), attack
   (NAND), or mitigate the relevance (MITIGATES) between points and objects.

EVENTS (secondary): only as context for why state changed.

De-emphasize process — no commit hashes, no test counts, no PR numbers, no
review findings, no tool calls, no build steps — unless they DIRECTLY change
state or reveal durable belief.

The narrative should read like: "We believed X. The session revealed Y,
which changed our approach to Z. The reasoning: A supports it, B undermines
it, C tempers how much it matters."

Granularity: the level of a decision (its resulting change in state, the
tradeoffs and reasons behind) worth remembering in six months. If a detail
won't matter then, drop it."""


def _complete(model, system: str, user: str) -> str:
    import threading
    box = {}
    def _run():
        box["resp"] = model.complete(system=system, user=user)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=600)
    if t.is_alive():
        raise TimeoutError("600s")
    return box.get("resp")


def _parse_json(raw: str) -> dict:
    import re
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("no JSON")
    block = m.group(0)
    for cut in (None, -1, -2, -3, -5, -10):
        try:
            return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("unparseable")


def _tokens(model) -> dict:
    return {"in": getattr(model, "last_prompt_tokens", 0),
            "out": getattr(model, "last_completion_tokens", 0)}


def main() -> None:
    transcript = (Path(__file__).resolve().parents[3] / "tests/eval/w-1272/w-design-bounded.txt").read_text()
    solar = MODELS["solar-pro4"](); solar.max_tokens = 8000; solar.temperature = 0.0  # noqa: E702
    flash = MODELS["deepseek-flash"](); flash.max_tokens = 8000; flash.temperature = 0.0  # noqa: E702

    print("=== PATH A: flash S1 DIRECT on raw ===", flush=True)
    t0 = time.time()
    a = _complete(flash, S1_SYSTEM, "CONVERSATION:\n" + transcript)
    a_tok = _tokens(flash)
    print(f"A took {time.time()-t0:.0f}s, {len(a)} chars", flush=True)

    print("\n=== PATH B: solar CLEAN -> flash S1 ===", flush=True)
    t0 = time.time()
    cleaned = _parse_json(_complete(solar, CLEAN_SYSTEM, "CONVERSATION:\n" + transcript))
    s_tok = _tokens(solar)
    print(f"cleaner took {time.time()-t0:.0f}s, entities={len(cleaned.get('entities',[]))} "
          f"points={len(cleaned.get('points',[]))}", flush=True)
    clean_text = cleaned.get("cleaned", "")
    t1 = time.time()
    b = _complete(flash, S1_SYSTEM, "CLEANED SIGNAL:\n" + clean_text)
    f_tok = _tokens(flash)
    print(f"flash-on-clean took {time.time()-t1:.0f}s, {len(b)} chars", flush=True)

    print("\n=== COST ===")
    print(f"Path A (flash direct): in={a_tok['in']} out={a_tok['out']} tokens", flush=True)
    print(f"Path B (solar+flash): solar in={s_tok['in']} out={s_tok['out']} + flash in={f_tok['in']} out={f_tok['out']}", flush=True)
    a_in_cost = a_tok['in'] * 0.06146e-6 + a_tok['out'] * 0.12292e-6
    s_in_cost = s_tok['in'] * 0.03e-6 + s_tok['out'] * 0.12e-6
    f_in_cost = f_tok['in'] * 0.06146e-6 + f_tok['out'] * 0.12292e-6
    print(f"est cost: A=${a_in_cost:.4f}  B=${s_in_cost+f_in_cost:.4f}", flush=True)

    print("\n" + "=" * 70 + "\nCLEANED SIGNAL (solar output):\n" + "=" * 70, flush=True)
    print(clean_text[:2000], flush=True)
    print("\n" + "=" * 70 + "\nPATH B — FLASH S1 ON CLEANED:\n" + "=" * 70, flush=True)
    print(b, flush=True)


if __name__ == "__main__":
    main()
