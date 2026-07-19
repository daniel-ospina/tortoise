"""ARIES extraction via tortoise LLMExtractor pipeline.

Converts ARIES [SEP]-separated texts to tortoise-compatible transcripts,
runs the two-stage extractor (point cleaning + relation finding), then
fuzzy-matches extracted operators against ARIES ground truth pairs.

Tests 4 relation-stage prompt variants:
  V0: generic (no gate terminology)
  V1: few-shot (examples in prompt)
  V2: structured IMPL/NAND (current tortoise default — baseline)
  V3: context-included (discourse markers + structure hints)

Usage: .venv/bin/python tests/aries_extraction.py [--variant V2] [--dry-run] [--n 10]
"""
from __future__ import annotations

import csv, json, os, random, re, sys, time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance
from tortoise.extractor import (
    LLMExtractor, _PointStage, _RelationStage, _overlap, _json,
    _POINTS_SYS, _RELATIONS_SYS,
)
from tortoise.idempotency import document_key
from tortoise.log import EventLog
from tortoise.models import OpenAICompatModel

DATA = "/tmp/aries-benchmark/data/data_full.csv"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL_ID = "deepseek/deepseek-chat"

LABEL_MAP = {"0": "support", "1": "attack", "2": "neutral"}
GATE_MAP = {"IMPL": "support", "NAND": "attack"}


# ── Prompt variants (relation stage only — point stage stays constant) ──

V0_RELATIONS_SYS = (
    "TASK: extract_relations\n"
    "Identify relationships between the given points. For each pair of points "
    "that have a logical connection, classify as:\n"
    '- "support": the second point supports, elaborates, or provides evidence for the first\n'
    '- "attack": the second point contradicts, refutes, or argues against the first\n'
    "Only include pairs where a relationship is explicitly present. "
    "Do NOT infer unstated relations.\n"
    "Return JSON: "
    '{"relations":[{"op_type":"support|attack","src":<point index>,"dst":<point index>}]}.'
)

V1_RELATIONS_SYS = V0_RELATIONS_SYS + (
    "\n\nExamples:\n"
    '- {"op_type":"support","src":0,"dst":1} — point 1 gives a reason for point 0\n'
    '- {"op_type":"attack","src":2,"dst":3} — point 3 contradicts point 2\n'
    '- {"op_type":"support","src":4,"dst":5} — point 5 elaborates on point 4\n'
)

V2_RELATIONS_SYS = _RELATIONS_SYS  # current tortoise default: IMPL/NAND + explicit cues

V3_RELATIONS_SYS = (
    "TASK: extract_relations\n"
    "The text uses [SEP] markers to separate discourse segments. "
    "Identify logical relationships between the given points using these signals:\n"
    "- Support cues: because, therefore, thus, so, since, given that, for example, specifically\n"
    "- Attack cues: but, however, although, on the contrary, not relevant, doesn't follow\n"
    "- Contrast markers (while, whereas, in contrast) are NOT attacks — only flag genuine contradiction\n"
    "Gates: IMPL (supports) or NAND (refutes). "
    "Do NOT infer unstated relations.\n"
    "Return JSON: "
    '{"relations":[{"op_type":"IMPL|NAND","src":<point index>,"dst":<point index>}]}.'
)

VARIANTS = {
    "V0": ("V0_generic", V0_RELATIONS_SYS),
    "V1": ("V1_few-shot", V1_RELATIONS_SYS),
    "V2": ("V2_structured", V2_RELATIONS_SYS),
    "V3": ("V3_context", V3_RELATIONS_SYS),
}


# ── ARIES → tortoise transcript conversion ──

def aries_to_transcript(argument: str) -> str:
    """Convert ARIES [SEP]-separated text to tortoise Speaker: text format."""
    segments = argument.split(" '[SEP]' ")
    lines = []
    for i, seg in enumerate(segments):
        seg = seg.strip()
        if seg:
            lines.append(f"S: {seg}")
    return "\n".join(lines)


# ── Fuzzy matching (segment-level → proposition-level) ──

def _norm(text: str) -> str:
    t = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", t).strip()

def _contains_proposition(segment_content: str, proposition: str) -> bool:
    """Check if proposition is contained within segment (fuzzy)."""
    seg_norm = _norm(segment_content)
    prop_norm = _norm(proposition)
    if not prop_norm:
        return False
    # Direct substring match
    if prop_norm in seg_norm:
        return True
    # Word overlap: ≥60% of proposition words appear in segment
    pw = set(prop_norm.split())
    sw = set(seg_norm.split())
    if not pw:
        return False
    return len(pw & sw) / len(pw) >= 0.6

def match_aries_pair(extracted_a: str, extracted_b: str,
                     truth_a: str, truth_b: str) -> bool:
    """Match extracted segment pair against ARIES proposition pair."""
    match_ab = _contains_proposition(extracted_a, truth_a) and _contains_proposition(extracted_b, truth_b)
    match_ba = _contains_proposition(extracted_a, truth_b) and _contains_proposition(extracted_b, truth_a)
    return match_ab or match_ba


# ── Model factory ──

def make_model() -> OpenAICompatModel:
    return OpenAICompatModel(
        id=MODEL_ID,
        base_url=OPENROUTER_BASE,
        api_key_env="OPENROUTER_API_KEY",
    )


# ── Custom extractor that accepts variant prompt ──

class VariantExtractor:
    """LLMExtractor with configurable relation prompt."""

    def __init__(self, point_model, relation_model, relation_system_prompt: str,
                 prompt_version: str = "v3"):
        self.points = _PointStage(point_model)
        self.relations = _RelationStage(relation_model)
        # Monkey-patch: swap the system prompt for relation stage
        self._orig_relations_sys = _RELATIONS_SYS
        self.relations.model._variant_sys = relation_system_prompt
        self.version = f"{point_model.id}/{relation_model.id}@{prompt_version}"

    def run(self, transcript: str, source_id: str, api: EventAPI, *,
            max_utterances: int = 40) -> list[dict]:
        """Run extraction, returning list of {A, B, op_type} for evaluation."""
        # This mirrors LLMExtractor.run but captures extracted relations for eval
        segs = list(self._utterances(transcript))
        if max_utterances:
            segs = segs[:max_utterances]
        context = f"conversation:{source_id}"

        point_model = self.points.model
        # Override relation model's complete to use variant prompt
        rel_model = self.relations.model
        orig_complete = rel_model.complete

        def variant_complete(*, system, user):
            return orig_complete(system=self.relations.model._variant_sys, user=user)

        rel_model.complete = variant_complete

        try:
            cleaned = self.points.run([s[1] for s in segs], context)
            ids, contents = [], []
            for i, (speaker, text, span) in enumerate(segs):
                c = cleaned.get(i)
                content = c if (c and _overlap(c, text) >= 0.5) else text
                contents.append(content)
                prov = provenance(source_id, span, quote=text, speaker=speaker,
                                  extracted_by=self.version)
                ids.append(api.add_point(content, context, prov))

            relations = self.relations.run(contents, context)
        finally:
            rel_model.complete = orig_complete

        extracted = []
        for r in relations:
            s, d = r.get("src"), r.get("dst")
            if s is None or d is None or not (0 <= s < len(ids)) or not (0 <= d < len(ids)):
                continue
            extracted.append({
                "A": contents[s],
                "B": contents[d],
                "op_type": r["op_type"],
            })
        return extracted

    @staticmethod
    def _utterances(transcript: str):
        """Parse Speaker: text lines — same as tortoise's _utterances."""
        import re as _re
        _RE = _re.compile(r"^\s*([A-Z][\w .'-]{0,40}):\s*(.*)$")
        base = 0
        for line in transcript.splitlines(keepends=True):
            stripped = line.rstrip("\n")
            m = _RE.match(stripped)
            if not m:
                base += len(line)
                continue
            speaker, body = m.group(1), m.group(2)
            body_off = m.start(2)
            start = base + body_off
            yield speaker, body.strip(), [start, start + len(body)]
            base += len(line)


# ── Evaluation ──

def evaluate(extracted: list[dict], aries_pairs: list[dict]) -> dict:
    """Match extracted operators against ARIES ground truth pairs."""
    matched = 0
    rel_correct = 0
    used = set()

    for ext in extracted:
        ext_rel = GATE_MAP.get(ext["op_type"].upper(), ext["op_type"].lower())
        for i, aries in enumerate(aries_pairs):
            if i in used:
                continue
            truth_rel = LABEL_MAP[aries["relations"]]
            if match_aries_pair(ext["A"], ext["B"],
                                aries["proposition_1"], aries["proposition_2"]):
                matched += 1
                used.add(i)
                if ext_rel == truth_rel:
                    rel_correct += 1
                break

    total = len(aries_pairs)
    return {
        "total_pairs": total,
        "matched": matched,
        "recall": matched / total if total else 0,
        "relation_accuracy": rel_correct / matched if matched else 0,
        "relation_correct": rel_correct,
        "extracted_count": len(extracted),
    }


# ── Dataset ──

def load_sample(csv_path: str, seed: int = 42, n_per_class: int = 30) -> list[dict]:
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
    groups = defaultdict(list)
    for row in sample:
        key = row["argument"][:500]
        groups[key].append(row)
    return dict(groups)


# ── Main ──

def run_variant(variant_name: str, relation_prompt: str,
                text_groups: dict[str, list[dict]],
                dry_run: bool = False) -> dict:
    """Run extractor on each unique source text."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    agg = {"total_pairs": 0, "matched": 0, "relation_correct": 0, "extracted_count": 0}
    items = list(text_groups.items())

    def process_one(item):
        text_key, aries_pairs = item
        full_text = aries_pairs[0]["argument"]
        transcript = aries_to_transcript(full_text)
        if dry_run:
            # Simulate: extract the exact ARIES pairs
            extracted = [
                {"A": p["proposition_1"], "B": p["proposition_2"],
                 "op_type": "IMPL" if LABEL_MAP[p["relations"]] == "support" else "NAND"}
                for p in aries_pairs
            ]
        else:
            point_model = make_model()
            rel_model = make_model()
            ext = VariantExtractor(point_model, rel_model, relation_prompt)
            import tempfile
            log_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_aries_"), "events.jsonl")
            log = EventLog(log_path)
            api = EventAPI(log, initiated_by="extractor")
            api.begin_ingest("aries_text", ext.version, document_key(full_text))
            extracted = ext.run(transcript, "aries_text", api)
        return evaluate(extracted, aries_pairs)

    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(process_one, item): item for item in items}
        for f in as_completed(futures):
            result = f.result()
            for k in agg:
                agg[k] += result[k]
            done += 1
            if done % 5 == 0 or done <= 2:
                rec = agg["matched"] / agg["total_pairs"] if agg["total_pairs"] else 0
                print(f"  [{variant_name}] {done}/{len(items)} texts — "
                      f"recall={rec:.1%} ({agg['matched']}/{agg['total_pairs']})")

    total = agg["total_pairs"]
    return {
        "variant": variant_name,
        "texts": len(items),
        "total_pairs": total,
        "recall": agg["matched"] / total if total else 0,
        "relation_accuracy": agg["relation_correct"] / agg["matched"] if agg["matched"] else 0,
        "matched": agg["matched"],
        "extracted_count": agg["extracted_count"],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["V0", "V1", "V2", "V3"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--n", type=int, default=10, help="Pairs per class")
    args = ap.parse_args()

    print(f"Loading ARIES ({args.n}/class, seed=42)...")
    sample = load_sample(DATA, seed=42, n_per_class=args.n)
    text_groups = group_by_text(sample)
    print(f"  {len(sample)} pairs across {len(text_groups)} texts")

    variants = [args.variant] if args.variant else ["V0", "V1", "V2", "V3"]
    results = []
    for vk in variants:
        name, prompt = VARIANTS[vk]
        print(f"\n{'='*60}\n{name} ({len(text_groups)} texts)...")
        r = run_variant(name, prompt, text_groups, dry_run=args.dry_run)
        results.append(r)
        print(f"  recall={r['recall']:.1%} ({r['matched']}/{r['total_pairs']}) "
              f"rel_acc={r['relation_accuracy']:.1%} extracted={r['extracted_count']}")

    if len(results) > 1:
        print(f"\n{'='*60}\nSUMMARY:")
        for r in results:
            print(f"  {r['variant']}: recall={r['recall']:.1%} rel_acc={r['relation_accuracy']:.1%}")

if __name__ == "__main__":
    main()
