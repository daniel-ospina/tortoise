"""ARIES benchmark harness — operator EXTRACTION via OpenRouter.

The model receives the FULL source text and extracts all support/attack/neutral
operator relationships. Extracted pairs are fuzzy-matched against ARIES ground truth.

Tests 4 prompt variants (extraction strategies):
  V0: zero-shot (generic extraction prompt)
  V1: domain-specific few-shot
  V2: structured IMPL/NAND prompt
  V3: context-included (discourse markers + structure hints)

Metrics per variant:
  - pair_recall: fraction of ARIES pairs found (fuzzy match)
  - relation_accuracy: on matched pairs, did the gate type match?

Usage: .venv/bin/python tests/aries_harness.py [--variant V0] [--dry-run]
"""
from __future__ import annotations  # noqa: I001

import csv, json, os, random, re, sys, time, urllib.request  # noqa: E401, F401
from collections import defaultdict
from dataclasses import dataclass  # noqa: F401
from typing import Optional  # noqa: F401

DATA = "/tmp/aries-benchmark/data/data_full.csv"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-chat"
LABEL_MAP = {"0": "support", "1": "attack", "2": "neutral"}

# ── Fuzzy matching ──

def _norm(text: str) -> str:
    """Normalize for fuzzy comparison: lowercase, strip punctuation, collapse whitespace."""
    t = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", t).strip()

def _overlap(a: str, b: str) -> float:
    """Word-level Jaccard overlap between two strings."""
    wa = set(_norm(a).split())
    wb = set(_norm(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)

def match_pair(extracted_a: str, extracted_b: str,
               truth_a: str, truth_b: str, threshold: float = 0.5) -> bool:
    """Check if extracted pair matches ARIES pair (bidirectional fuzzy Jaccard)."""
    score_ab = min(_overlap(extracted_a, truth_a), _overlap(extracted_b, truth_b))
    score_ba = min(_overlap(extracted_a, truth_b), _overlap(extracted_b, truth_a))
    return max(score_ab, score_ba) >= threshold


# ── Prompt variants ──

V0_SYSTEM = (
    "You are an argument relationship extractor. Given a text, identify ALL pairs of "
    "propositions that have a logical relationship. For each pair, output:\n"
    '- "A": the first proposition (verbatim from text)\n'
    '- "B": the second proposition (verbatim from text)\n'
    '- "relation": one of "support" (B supports/elaborates A), '
    '"attack" (B contradicts/refutes A), or "neutral" (no clear logical relation)\n\n'
    "Return a JSON object: {\"pairs\": [{\"A\": \"...\", \"B\": \"...\", \"relation\": \"support|attack|neutral\"}]}\n"
    "Include ONLY pairs where there IS a support or attack relationship. "
    "Do NOT include neutral pairs. Quote propositions VERBATIM from the text."
)

V1_SYSTEM = V0_SYSTEM + (
    "\n\nExamples of good extractions:\n"
    '- A: "geometric methods are in general much faster than physically-based ones"\n'
    '  B: "This approach reduces computation time significantly"\n'
    '  relation: support\n'
    '- A: "allergists are not doctors"\n'
    '  B: "I am a physician and allergists undergo full medical training"\n'
    '  relation: attack\n'
    "Only extract relations explicitly present in the text. Do not infer."
)

V2_SYSTEM = (
    "You are an operator extractor for an epistemic graph. Given a text, extract all "
    "logical operators (IMPL or NAND) that connect propositions.\n\n"
    "Gates:\n"
    "- IMPL: B implies/supports/elaborates A. B's truth strengthens A's claim.\n"
    "- NAND: B contradicts/refutes A. B and A cannot both hold.\n\n"
    "Rules:\n"
    "1. Extract ONLY explicitly asserted relations (cue words: because, therefore, but, however, etc.)\n"
    "2. Quote propositions VERBATIM from the text\n"
    "3. Do NOT infer relations from co-occurrence alone\n\n"
    "Return JSON: {\"operators\": [{\"A\": \"...\", \"B\": \"...\", \"gate\": \"IMPL|NAND\"}]}"
)

V3_SYSTEM = (
    "You are an argument structure analyst. Given a text with discourse markers "
    "([SEP] separates segments), identify ALL support and attack relationships "
    "between propositions.\n\n"
    "Use these signals:\n"
    "- Support: because, therefore, thus, so, since, given that, for example, specifically\n"
    "- Attack: but, however, although, on the contrary, not relevant, doesn't follow, except that\n"
    "- Contrast: while, whereas, in contrast (these are NOT attacks — only flag if there's genuine contradiction)\n\n"
    "For each pair found, quote the propositions VERBATIM from the text.\n"
    "Return JSON: {\"pairs\": [{\"A\": \"...\", \"B\": \"...\", \"relation\": \"support|attack\"}]}"
)

VARIANTS = {
    "V0": ("V0_zero-shot", V0_SYSTEM),
    "V1": ("V1_few-shot", V1_SYSTEM),
    "V2": ("V2_structured", V2_SYSTEM),
    "V3": ("V3_context", V3_SYSTEM),
}

# Gate mapping for V2 (IMPL→support, NAND→attack)
GATE_MAP = {"IMPL": "support", "NAND": "attack"}


# ── Dataset ──

def load_sample(csv_path: str, seed: int = 42, n_per_class: int = 30) -> list[dict]:
    """Stratified sample, grouped by source text for single-extraction evaluation."""
    by_label = {"0": [], "1": [], "2": []}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lbl = row["relations"]
            if lbl in by_label:
                by_label[lbl].append(row)
    rng = random.Random(seed)
    sample = []
    for lbl in ["0", "1", "2"]:
        sample.extend(rng.sample(by_label[lbl], n_per_class))
    rng.shuffle(sample)
    return sample


def group_by_text(sample: list[dict]) -> dict[str, list[dict]]:
    """Group ARIES pairs by their source argument text."""
    groups = defaultdict(list)
    for row in sample:
        # Use first 500 chars as key (full text would be too large; same text = same key in practice)
        key = row["argument"][:500]
        groups[key].append(row)
    return dict(groups)


# ── OpenRouter ──

def call_openrouter(system: str, user: str, model: str = MODEL,
                    timeout: int = 60, max_tokens: int = 4096,
                    max_retries: int = 2) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    body = json.dumps({
        "model": model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            return data["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: F841
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            else:
                raise


def parse_extraction(raw: str, variant_name: str) -> list[dict]:
    """Parse model output into list of {A, B, relation} dicts."""
    # Strip markdown fences, thinking blocks
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text

    # Find JSON object
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []

    # Support both "pairs" and "operators" keys
    items = data.get("pairs") or data.get("operators") or []
    results = []
    for item in items:
        a = str(item.get("A", "")).strip().strip('"')
        b = str(item.get("B", "")).strip().strip('"')
        if not a or not b:
            continue
        # Normalize relation/gate
        rel = item.get("relation") or item.get("gate") or ""
        rel = rel.lower().strip()
        if variant_name.startswith("V2"):
            rel = GATE_MAP.get(rel.upper(), rel)
        if rel in ("support", "attack"):
            results.append({"A": a, "B": b, "relation": rel})
    return results


# ── Evaluation ──

def evaluate_extraction(extracted: list[dict], aries_pairs: list[dict]) -> dict:
    """Match extracted pairs against ARIES ground truth. Returns recall + relation accuracy."""
    matched = 0
    relation_correct = 0
    used = set()  # track which ARIES pairs were matched (avoid double-counting)

    for ext in extracted:
        for i, aries in enumerate(aries_pairs):
            if i in used:
                continue
            truth_a = aries["proposition_1"]
            truth_b = aries["proposition_2"]
            truth_rel = LABEL_MAP[aries["relations"]]
            if match_pair(ext["A"], ext["B"], truth_a, truth_b):
                matched += 1
                used.add(i)
                if ext["relation"] == truth_rel:
                    relation_correct += 1
                break

    total = len(aries_pairs)
    recall = matched / total if total else 0
    rel_acc = relation_correct / matched if matched else 0
    return {"total_pairs": total, "matched": matched, "recall": recall,
            "relation_accuracy": rel_acc, "relation_correct": relation_correct,
            "extracted_count": len(extracted)}


# ── Main ──

def run_variant(variant_name: str, system_prompt: str,
                text_groups: dict[str, list[dict]],
                dry_run: bool = False, max_workers: int = 8) -> dict:
    """Run extraction on each unique source text, evaluate against its ARIES pairs."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    agg = {"total_pairs": 0, "matched": 0, "relation_correct": 0, "extracted_count": 0}
    n_texts = len(text_groups)
    items = list(text_groups.items())

    def process_one(item):
        text_key, aries_pairs = item  # noqa: RUF059
        full_text = aries_pairs[0]["argument"]
        clean_text = full_text.replace(" '[SEP]' ", "\n")
        if dry_run:
            extracted = [
                {"A": p["proposition_1"], "B": p["proposition_2"],
                 "relation": LABEL_MAP[p["relations"]]}
                for p in aries_pairs
            ]
        else:
            user_prompt = f"Text:\n\n{clean_text}\n\nExtract all support and attack relationships."
            raw = call_openrouter(system_prompt, user_prompt)
            extracted = parse_extraction(raw, variant_name)
        return evaluate_extraction(extracted, aries_pairs)

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(process_one, item): item for item in items}
        for f in as_completed(futures):
            result = f.result()
            for k in agg:
                agg[k] += result[k]
            done += 1
            if done % 10 == 0 or done <= 2:
                rec = agg["matched"] / agg["total_pairs"] if agg["total_pairs"] else 0
                print(f"  [{variant_name}] {done}/{n_texts} texts — "
                      f"recall={rec:.1%} ({agg['matched']}/{agg['total_pairs']})")

    total = agg["total_pairs"]
    return {
        "variant": variant_name,
        "texts": n_texts,
        "total_pairs": total,
        "recall": agg["matched"] / total if total else 0,
        "relation_accuracy": agg["relation_correct"] / agg["matched"] if agg["matched"] else 0,
        "matched": agg["matched"],
        "relation_correct": agg["relation_correct"],
        "extracted_count": agg["extracted_count"],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["V0", "V1", "V2", "V3"],
                    help="Run single variant only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--n", type=int, default=30, help="Pairs per class (default: 30)")
    args = ap.parse_args()

    print(f"Loading ARIES dataset ({args.n} per class, seed=42)...")
    sample = load_sample(DATA, seed=42, n_per_class=args.n)
    text_groups = group_by_text(sample)
    print(f"Sample: {len(sample)} pairs across {len(text_groups)} unique texts")
    print(f"  support={sum(1 for r in sample if LABEL_MAP[r['relations']]=='support')}, "
          f"attack={sum(1 for r in sample if LABEL_MAP[r['relations']]=='attack')}, "
          f"neutral={sum(1 for r in sample if LABEL_MAP[r['relations']]=='neutral')}")

    variants_to_run = [args.variant] if args.variant else ["V0", "V1", "V2", "V3"]
    results = []
    for vk in variants_to_run:
        name, sys_prompt = VARIANTS[vk]
        print(f"\n{'='*60}\nRunning {name} ({len(text_groups)} texts)...")
        if args.dry_run:
            print("  (dry run — no API calls)")
        r = run_variant(name, sys_prompt, text_groups, dry_run=args.dry_run)
        results.append(r)
        print(f"  Result: recall={r['recall']:.1%} ({r['matched']}/{r['total_pairs']}) "
              f"rel_acc={r['relation_accuracy']:.1%} "
              f"extracted={r['extracted_count']}")

    if len(results) > 1:
        print(f"\n{'='*60}\nSUMMARY:")
        for r in results:
            print(f"  {r['variant']}: recall={r['recall']:.1%} rel_acc={r['relation_accuracy']:.1%} "
                  f"({r['matched']}/{r['total_pairs']})")
        best = max(results, key=lambda x: x["recall"])
        print(f"\n  Best recall: {best['variant']} at {best['recall']:.1%}")


if __name__ == "__main__":
    main()
