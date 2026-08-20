#!/usr/bin/env python3
"""Optimization loop: cleaner prompt (from file) -> solar clean -> flash S1.
Reports cost + the cleaner's compression ratio + flash output."""
from __future__ import annotations  # noqa: I001
import json, sys, time  # noqa: E401
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.model_adapters import MODELS

S1_SYSTEM = """You are the STORY SUMMARIZER for the company/product epistemic memory.
Read the input. Produce a NARRATIVE that captures what CHANGED about the world
we operate in — the state of the product, the team, the domain — and WHY, at
the level of durable meaning, not mechanics.
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

def _complete(model, system, user):
    import threading
    box = {}
    def _run(): box["resp"] = model.complete(system=system, user=user)
    t = threading.Thread(target=_run, daemon=True); t.start()  # noqa: E702
    t.join(timeout=600)
    if t.is_alive(): raise TimeoutError("600s")  # noqa: E701
    return box.get("resp")

def _parse_json(raw):
    import re
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m: raise ValueError("no JSON")  # noqa: E701
    block = m.group(0)
    for cut in (None,-1,-2,-3,-5,-10):
        try: return json.loads(block if cut is None else block[:cut])  # noqa: E701
        except json.JSONDecodeError: continue  # noqa: E701
    raise ValueError("unparseable")

def main():
    prompt_file = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else prompt_file.replace(".md", "-result.json")
    CLEAN_SYSTEM = Path(prompt_file).read_text()
    transcript = (Path(__file__).resolve().parents[3] / "tests/eval/w-1272/w-design-bounded.txt").read_text()
    raw_tokens = len(transcript) // 4
    solar = MODELS["solar-pro4"](); solar.max_tokens = 8000; solar.temperature = 0.0  # noqa: E702
    flash = MODELS["deepseek-flash"](); flash.max_tokens = 8000; flash.temperature = 0.0  # noqa: E702

    t0 = time.time()
    raw_clean = None
    for attempt in range(3):
        raw_clean = _complete(solar, CLEAN_SYSTEM, "CONVERSATION:\n" + transcript)
        try:
            cleaned = _parse_json(raw_clean)
            break
        except Exception:
            if attempt == 2:
                Path("/tmp/cleaner-raw-fail.txt").write_text(raw_clean or "")
                raise
            continue
    s_in, s_out = solar.last_prompt_tokens, solar.last_completion_tokens
    clean_text = cleaned.get("cleaned", "")
    ratio = len(clean_text) / len(transcript)
    b = _complete(flash, S1_SYSTEM, "CLEANED SIGNAL:\n" + clean_text)
    f_in, f_out = flash.last_prompt_tokens, flash.last_completion_tokens
    cost = s_in*0.03e-6 + s_out*0.12e-6 + f_in*0.06146e-6 + f_out*0.12292e-6

    result = {
        "iter": prompt_file,
        "seconds": round(time.time()-t0),
        "raw_tokens": raw_tokens,
        "solar": {"in": s_in, "out": s_out},
        "flash": {"in": f_in, "out": f_out},
        "cost_usd": round(cost, 5),
        "compression": round(ratio, 2),
        "entities": len(cleaned.get("entities", [])),
        "points": len(cleaned.get("points", [])),
        "cleaned_head": clean_text[:600],
        "flash_output": b,
    }
    Path(out_path).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: result[k] for k in ("seconds","solar","flash","cost_usd","compression","entities","points")}))
    print("=== CLEANED (head) ===")
    print(clean_text[:600])
    print("=== FLASH OUTPUT ===")
    print(b[:1500])

if __name__ == "__main__":
    main()
