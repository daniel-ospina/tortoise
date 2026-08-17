#!/usr/bin/env python3
"""Single-flash S1 with the memory_granularity bar (uncapped model)."""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tortoise.value_extractor import compile_value_brief
from tests.model_adapters import MODELS

S1_TMPL = """You are the STORY SUMMARIZER for the company/product epistemic memory.
Read the whole conversation. Produce a NARRATIVE that captures what CHANGED
about the world we operate in — the state of the product, the team, the
domain — and WHY it changed, at the level of durable meaning, not mechanics.

Use the MEMORY GRANULARITY definitions below as the rule for what to keep
(durable) vs drop (ephemeral) — not a vague time heuristic:

{memory_granularity}

Apply these per domain. When a fact spans domains, keep it if ANY domain
considers it durable. When unsure: "is this a decision, a state change, a
durable belief, or a reason — or is it how the work was done this hour?"

Focus on TWO layers, in this order:
1. STATE (primary): subjects and objects and how they changed.
2. EPISTEMIC (primary): the LOGIC — points that support (IMPL), attack (NAND),
   or mitigate the relevance (MITIGATES) between points and objects.
EVENTS (secondary): only as context for why state changed.

De-emphasize process — no commit hashes, no test counts, no PR numbers, no
review findings, no tool calls, no build steps — unless they DIRECTLY change
state or reveal durable belief.

RESTATE, DON'T REINVENT: if the conversation states a root cause or a fact,
preserve it exactly. Do NOT invent a "We believed X" opening unless the input
supports it.

The narrative should read like: "We believed X. The session revealed Y, which
changed our approach to Z. The reasoning: A supports it, B undermines it, C
tempers how much it matters."

Granularity: the level of a decision (its resulting change in state, the
tradeoffs and reasons behind) worth remembering in six months, per the
memory-granularity rules above. If a detail won't matter then, drop it."""

def _complete(model, system, user):
    import threading
    box = {}
    def _run(): box["resp"] = model.complete(system=system, user=user)
    t = threading.Thread(target=_run, daemon=True); t.start()
    t.join(timeout=600)
    if t.is_alive(): raise TimeoutError("600s")
    return box.get("resp")

def main():
    transcript = (Path(__file__).resolve().parents[3] / "tests/eval/w-1272/w-design-bounded.txt").read_text()
    g = compile_value_brief().get("memory_granularity", {})
    g_text = "\n".join(f"- {ns}: {txt}" for ns, txt in g.items())
    S1 = S1_TMPL.replace("{memory_granularity}", g_text)
    flash = MODELS["deepseek-flash"]()  # max_tokens=None = uncapped
    print(f"flash max_tokens: {flash.max_tokens} (uncapped)", flush=True)
    t0 = time.time()
    out = _complete(flash, S1, "CONVERSATION:\n" + transcript)
    print(f"flash on raw: {time.time()-t0:.0f}s, {len(out)} chars, "
          f"in={flash.last_prompt_tokens} out={flash.last_completion_tokens} tokens", flush=True)
    print("\n--- OUTPUT ---\n" + out, flush=True)

if __name__ == "__main__":
    main()
