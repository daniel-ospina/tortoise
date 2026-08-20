#!/usr/bin/env python3
"""S1 flash story-summary test — show the owner the raw output."""
from __future__ import annotations

import json  # noqa: F401
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tortoise.value_extractor import compile_value_brief  # noqa: E402, I001, RUF100
from tests.model_adapters import MODELS  # noqa: E402, RUF100

# Subjects + points + events the current compile_value_brief lacks (owner: master list)
CORE_SUBJECTS = {
    "core:organization": "An organization",
    "core:team": "A team",
    "core:role": "A role",
    "core:legalPerson": "A legal person",
    "core:naturalPerson": "A natural person",
}
CORE_EVENTS = {
    "core:decision": "A decision made (timeline record)",
    "core:occurrence": "A generic occurrence",
    "core:deployment": "A deployment",
    "core:review": "A review",
    "core:meeting": "A meeting",
}
CORE_POINTS = {
    "core:statement": "A point — the logic layer (supports/attacks/mitigates)",
}

S1_SYSTEM_TEMPLATE = """You are the STORY SUMMARIZER for the company/product epistemic memory.

Read the whole conversation. Produce a NARRATIVE that captures what CHANGED
about the world we operate in — the state of the product, the team, the
domain — and WHY it changed, at the level of durable meaning, not mechanics.

Focus on TWO layers, in this order:

1. STATE (primary): the subjects and objects that exist and how they
   changed — e.g. customer X, feature W, Product K, Team Z, an approach
   adopted, a ruling made, an option chosen or discarded. State is what
   REMAINS TRUE after the conversation: what was created, changed,
   superseded, or confirmed.

2. EPISTEMIC (primary): the LOGIC behind the state — the points that
   support it (IMPL), attack it (NAND), or — most importantly — mitigate
   the relevance of the relationships between points and objects. What do
   we now believe, and on what reasoning?

EVENTS (secondary): mention only as CONTEXT for why state changed — a
decision, a discovery, a pivot. Not as the story itself.

De-emphasize process — do NOT narrate the mechanics: no commit hashes, no
test counts, no "PR opened", no "rebase", no "issue emerged", no "review
gate found N findings", no tool calls, no build steps. Those are noise
unless they DIRECTLY change state or reveal a durable belief.

The narrative should read like: "We believed X. The session revealed Y,
which changed our approach to Z. The reasoning: A supports it, B
undermines it, C tempers how much it matters."

Use the canonical Entity list (below) for the subjects/objects/points you
embed — this is the ontology's vocabulary. Note entities and their
connections as you go, but weave them into the narrative — do not emit a
separate list.

Granularity: the level of a decision (it's resulting change in state, the
tradeoffs and reasons (points IMPL, NAND, mitigations) behind) worth
remembering in six months, grounded in the objects/subjects it affects.
If a detail won't matter then, drop it.

Canonical entity list (kind: description):
{entities}"""


def _master_list() -> str:
    brief = compile_value_brief()
    merged = {**brief, **CORE_SUBJECTS, **CORE_EVENTS, **CORE_POINTS}
    return "\n".join(f"{k}: {v}" for k, v in sorted(merged.items()))


def main() -> None:
    transcript = (Path(__file__).resolve().parents[3] / "tests/eval/w-1272/w-design-bounded.txt").read_text()
    edus = [l.strip() for l in transcript.splitlines()  # noqa: E741
            if l.strip() and not l.startswith("#")]
    print(f"=== window: {len(edus)} EDUs ===", flush=True)

    flash = MODELS["deepseek-flash"]()
    flash.max_tokens = 8000  # no hard cap (owner); high enough to not truncate
    flash.temperature = 0.0

    user = "CONVERSATION:\n" + "\n".join(edus)
    system = S1_SYSTEM_TEMPLATE.format(entities=_master_list())

    print("=== calling flash (S1 story summary) ===", flush=True)
    t0 = time.time()
    import threading
    box = {}
    def _run():
        box["resp"] = flash.complete(system=system, user=user)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=600)
    if t.is_alive():
        print("TIMEOUT after 600s", flush=True)
        return
    resp = box.get("resp")
    print(f"=== flash returned in {time.time()-t0:.0f}s, {len(resp) if resp else 0} chars ===", flush=True)
    print("\n" + "=" * 70 + "\nRAW OUTPUT:\n" + "=" * 70, flush=True)
    print(resp, flush=True)


if __name__ == "__main__":
    main()
