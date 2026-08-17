#!/usr/bin/env python3
"""A/B: single flash pass on raw vs solar-clean -> flash. Shows FULL outputs inline."""
from __future__ import annotations
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.model_adapters import MODELS

S1_RAW = """You are the STORY SUMMARIZER for the company/product epistemic memory.
Read the whole conversation. Produce a NARRATIVE that captures what CHANGED
about the world we operate in — the state of the product, the team, the
domain — and WHY it changed, at the level of durable meaning, not mechanics.
Focus on TWO layers, in this order:
1. STATE (primary): subjects and objects and how they changed — an approach
   adopted, a ruling made, an option chosen or discarded. What REMAINS TRUE.
2. EPISTEMIC (primary): the LOGIC — points that support (IMPL), attack (NAND),
   or mitigate the relevance (MITIGATES) between points and objects.
EVENTS (secondary): only as context for why state changed.
De-emphasize process — no commit hashes, no test counts, no PR numbers, no
review findings, no tool calls, no build steps — unless they DIRECTLY change
state or reveal durable belief.
The narrative should read like: "We believed X. The session revealed Y, which
changed our approach to Z. The reasoning: A supports it, B undermines it, C
tempers how much it matters."
Granularity: the level of a decision (its resulting change in state, the
tradeoffs and reasons behind) worth remembering in six months. If a detail
won't matter then, drop it."""

CLEANER = (Path(__file__).resolve().parent / "cleaner-v5.md").read_text()

def _complete(model, system, user):
    import threading
    box = {}
    def _run(): box["resp"] = model.complete(system=system, user=user)
    t = threading.Thread(target=_run, daemon=True); t.start()
    t.join(timeout=600)
    if t.is_alive(): raise TimeoutError("600s")
    return box.get("resp")

def _parse_json(raw):
    import re
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m: raise ValueError("no JSON")
    block = m.group(0)
    for cut in (None,-1,-2,-3,-5,-10):
        try: return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError: continue
    raise ValueError("unparseable")

def main():
    transcript = (Path(__file__).resolve().parents[3] / "tests/eval/w-1272/w-design-bounded.txt").read_text()
    flash = MODELS["deepseek-flash"](); flash.max_tokens = 8000; flash.temperature = 0.0
    solar = MODELS["solar-pro4"](); solar.max_tokens = 8000; solar.temperature = 0.0

    print("=" * 70)
    print("PATH A — SINGLE FLASH PASS ON RAW")
    print("=" * 70)
    t0 = time.time()
    a = _complete(flash, S1_RAW, "CONVERSATION:\n" + transcript)
    print(f"(flash on raw: {time.time()-t0:.0f}s, {len(a)} chars)")
    print(a)

    print()
    print("=" * 70)
    print("PATH B — SOLAR CLEAN -> FLASH")
    print("=" * 70)
    t0 = time.time()
    raw_clean = None
    for attempt in range(3):
        raw_clean = _complete(solar, CLEANER, "CONVERSATION:\n" + transcript)
        try:
            cleaned = _parse_json(raw_clean); break
        except Exception:
            if attempt == 2: raise
            continue
    clean_text = cleaned.get("cleaned", "")
    print(f"(solar clean: {time.time()-t0:.0f}s, {len(clean_text)} chars)")
    print("--- SOLAR CLEANED OUTPUT ---")
    print(clean_text)
    t1 = time.time()
    b = _complete(flash, S1_RAW, "CLEANED SIGNAL:\n" + clean_text)
    print(f"\n--- FLASH ON CLEANED ({time.time()-t1:.0f}s, {len(b)} chars) ---")
    print(b)

if __name__ == "__main__":
    main()
