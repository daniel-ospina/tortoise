#!/usr/bin/env python3
"""Fixed pipeline: solar clean (v6, with durable_memo) -> regex gate -> flash.
Shows FULL outputs inline."""
from __future__ import annotations
import json, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.model_adapters import MODELS

S1_RAW = """You are the STORY SUMMARIZER for the company/product epistemic memory.
Read the input. Produce a NARRATIVE that captures what CHANGED about the world
we operate in — the state of the product, the team, the domain — and WHY, at
the level of durable meaning, not mechanics.
Focus on TWO layers, in this order:
1. STATE (primary): subjects and objects and how they changed.
2. EPISTEMIC (primary): the LOGIC — points that support (IMPL), attack (NAND),
   or mitigate the relevance (MITIGATES) between points and objects.
EVENTS (secondary): only as context for why state changed.
De-emphasize process — no commit hashes, no test counts, no PR numbers, no
review findings, no tool calls, no build steps — unless they DIRECTLY change
state or reveal durable belief.
RESTATE, DON'T REINVENT: if the input states a root cause or a fact, preserve
it exactly. Do NOT invent a "We believed X" opening unless the input supports
it. Do NOT replace a stated root cause with your own thesis.
The narrative should read like: "We believed X. The session revealed Y, which
changed our approach to Z. The reasoning: A supports it, B undermines it, C
tempers how much it matters."
Granularity: the level of a decision (its resulting change in state, the
tradeoffs and reasons behind) worth remembering in six months.
The input includes a 'durable_memo' with named facts (root_cause_chain,
chosen_fix_and_why, residual_defects, independent_resolution,
environment_beliefs). These facts MUST appear in your narrative — restate
them in context; do not omit them."""

# Deterministic mechanics gate — strip/flag banned token classes.
BANNED = [
    (re.compile(r"#\d+"), "an issue/PR reference"),
    (re.compile(r"PR\s+#?\d+", re.I), "a PR reference"),
    (re.compile(r"\b\d+/\d+\s+(tests|passed|failed)\b", re.I), "a test count"),
    (re.compile(r"\b[0-9a-f]{7,}\b"), "a commit hash"),
    (re.compile(r"\bload\s+\d+[\s,.-]*\d*\b", re.I), "a load number"),
    (re.compile(r"\b\d+s\b"), "an elapsed time"),
    (re.compile(r"\bVGATE\b", re.I), "a gate name"),
    (re.compile(r"\bworktree\b", re.I), "the word worktree"),
    (re.compile(r"cid\[:8\]|line\s+\d+|\.py\b", re.I), "a code identifier"),
]

def gate_clean(text: str) -> list[str]:
    """Return the banned-token classes present in text (empty = clean)."""
    hits = set()
    for pat, label in BANNED:
        if pat.search(text):
            hits.add(label)
    return sorted(hits)


# Deterministic sanitizer: replace banned tokens with their MEANING so the
# model's mechanics never reach flash (belt-and-suspenders over retries).
_SANITIZE_RULES = [
    (re.compile(r"#\d+"), "the-issue"),
    (re.compile(r"PR\s+#?\d+", re.I), "a-concurrent-pr"),
    (re.compile(r"\b\d+/\d+\s+(tests|passed|failed)\b", re.I), "verified"),
    (re.compile(r"\b[0-9a-f]{7,}\b"), "a-hash"),
    (re.compile(r"\bload\s+\d+([.,]\d+)?\b", re.I), "heavy-load"),
    (re.compile(r"\b\d+s\b"), "a-duration"),
    (re.compile(r"\bVGATE\b", re.I), "a-gate"),
    (re.compile(r"\bworktree\b", re.I), "isolated-work"),
    (re.compile(r"cid\[:8\]|line\s+\d+|\.py\b|pytest-timeout|setsid", re.I), "tooling-detail"),
    (re.compile(r"\btest_\w+\b"), "a-test"),
]

def sanitize(text: str) -> str:
    for pat, repl in _SANITIZE_RULES:
        text = pat.sub(repl, text)
    return text


def sanitize_memo(memo: dict) -> dict:
    """Recursively sanitize the durable_memo fields."""
    out = {}
    for k, v in memo.items():
        if isinstance(v, str):
            out[k] = sanitize(v)
        elif isinstance(v, list):
            out[k] = [sanitize(x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out

def _complete(model, system, user):
    import threading
    box = {}
    def _run(): box["resp"] = model.complete(system=system, user=user)
    t = threading.Thread(target=_run, daemon=True); t.start()
    t.join(timeout=600)
    if t.is_alive(): raise TimeoutError("600s")
    return box.get("resp")

def _parse_json(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m: raise ValueError("no JSON")
    block = m.group(0)
    for cut in (None,-1,-2,-3,-5,-10):
        try: return json.loads(block if cut is None else block[:cut])
        except json.JSONDecodeError: continue
    raise ValueError("unparseable")

def main():
    transcript = (Path(__file__).resolve().parents[3] / "tests/eval/w-1272/w-design-bounded.txt").read_text()
    solar = MODELS["solar-pro4"](); solar.max_tokens = 8000; solar.temperature = 0.0
    flash = MODELS["deepseek-flash"](); flash.max_tokens = 8000; flash.temperature = 0.0
    CLEANER_TMPL = (Path(__file__).resolve().parent / "cleaner-v7.md").read_text()
    from tortoise.value_extractor import compile_value_brief
    _g = compile_value_brief().get("memory_granularity", {})
    _g_text = "\n".join(f"- {ns}: {txt}" for ns, txt in _g.items())
    CLEANER = CLEANER_TMPL.replace("{memory_granularity}", _g_text)

    print("=== SOLAR CLEAN (v6) ===", flush=True)
    t0 = time.time()
    raw_clean = None
    for attempt in range(3):
        raw_clean = _complete(solar, CLEANER, "CONVERSATION:\n" + transcript)
        try:
            cleaned = _parse_json(raw_clean)
            clean_text = cleaned.get("cleaned", "")
            hits = gate_clean(clean_text)
            if hits and attempt < 2:
                print(f"  attempt {attempt}: gate caught {hits} — retrying", flush=True)
                continue
            break
        except Exception:
            if attempt == 2: raise
            continue
    print(f"  solar: {time.time()-t0:.0f}s, cleaned={len(clean_text)} chars, "
          f"gate_hits={hits or 'CLEAN'}", flush=True)
    memo = cleaned.get("durable_memo", {})
    print(f"  durable_memo keys: {list(memo.keys())}", flush=True)
    print("\n--- CLEANED ---\n" + clean_text, flush=True)
    print("\n--- DURABLE MEMO ---\n" + json.dumps(memo, indent=1), flush=True)

    print("\n=== FLASH (with memo anchors, sanitized) ===", flush=True)
    t1 = time.time()
    clean_safe = sanitize(clean_text)
    memo_safe = sanitize_memo(memo)
    user = json.dumps({"cleaned": clean_safe, "durable_memo": memo_safe}, indent=1)
    b = _complete(flash, S1_RAW, user)
    print(f"  flash: {time.time()-t1:.0f}s, {len(b)} chars", flush=True)
    print("\n--- FLASH OUTPUT ---\n" + b, flush=True)

if __name__ == "__main__":
    main()
