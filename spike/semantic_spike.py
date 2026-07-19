#!/usr/bin/env python3
"""SS2 Semantic Extractor Spike — validate LLM entity extraction precision.

Single-file spike. Hardcoded doc paths. No argparse.
Exits: 0 = gate passed, 1 = gate failed, 2 = extraction error.
"""

import datetime, json, math, os, re, sys, time
from pathlib import Path

from openai import OpenAI

# ── config ──
MODEL = "deepseek/deepseek-chat"
BASE_URL = "https://openrouter.ai/api/v1"
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
SPIKE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path("/Users/home/eldato")

DOCS = [
    ("D1", "docs/epics/2026-07-14-memory-system/02-research-brief.md"),
    ("D2", "docs/teams/organisation-design-team/domains (S1)/data/ONTOLOGY.md"),
    ("D4", "docs/epics/2026-07-14-memory-system/04-plan.md"),
]

SYSTEM_PROMPT = """\
You are an entity extraction system. Extract Subjects and Objects from the markdown content below.
Documents are Objects with objectKind=document — include them in the objects array.

Return ONLY valid JSON with this exact schema:
{"subjects":[{"name":"...","subjectKind":"...","evidence":"..."}],"objects":[{"name":"...","objectKind":"...","evidence":"..."}]}

## Kind Vocabularies
- subjectKind: organization | team | role | legalPerson | naturalPerson | other
- objectKind: document | product | customer | competitor | user | skill | workflow | tool | agent | indicator | database | api | code | software | infrastructure | agreement | standard | epic | project | task | other

## Rules
- name: use the canonical name as it appears in the text (not normalized)
- subjectKind: classify by structural position
- objectKind: classify by nature (Documents are objectKind: document)
- evidence: verbatim snippet from the text justifying the extraction
- Skip generic terms that aren't named entities (e.g., "the team", "a document")
- Do not invent entities not mentioned in the text"""

VALID_SUBJECT_KINDS = {"organization", "team", "role", "legalPerson", "naturalPerson", "other"}
VALID_OBJECT_KINDS = {
    "document", "product", "customer", "competitor", "user", "skill", "workflow",
    "tool", "agent", "indicator", "database", "api", "code", "software",
    "infrastructure", "agreement", "standard", "epic", "project", "task", "other",
}


# ── name normalization for matching ──
def _normalize(name: str) -> str:
    """Case-insensitive, whitespace-normalized, strip backtick fences + punctuation."""
    s = name.strip().lower()
    s = " ".join(s.split())  # collapse multiple spaces
    s = s.strip("`\"'.,;:!?()[]{}")  # strip backticks and punctuation
    return s


# ── credential scan (Plan risk R7) ──
_CREDENTIAL_RE = re.compile(
    r'(sk-[a-zA-Z0-9]{20,}|AIza[0-9A-Za-z\-_]{35}|ghp_[a-zA-Z0-9]{36}|'
    r'sk-or-[a-zA-Z0-9]{20,}|hf_[a-zA-Z0-9]{34}|xox[bpras]-[a-zA-Z0-9-]+)',
    re.IGNORECASE,
)


def _scan_credentials(text: str) -> list[str]:
    """Return list of matched credential patterns found in text."""
    matches = _CREDENTIAL_RE.findall(text)
    return [m[:8] + "..." if len(m) > 11 else m for m in matches]  # redact in log


# ── extraction ──
def extract_entities(doc_text: str, model: str = MODEL) -> dict:
    """Single LLM call. Retry once on API failure or malformed JSON."""
    if not API_KEY:
        print("  ⚠️  OPENROUTER_API_KEY not set — skipping extraction", file=sys.stderr)
        return {"subjects": [], "objects": [], "_warnings": ["API key missing"]}

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": doc_text},
                ],
                temperature=0.1,
                max_tokens=8192,
                timeout=45,
            )
            raw = response.choices[0].message.content or ""
            parsed = _parse_json(raw)
            return _validate(parsed)

        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(2)
                continue
            print(f"  ⚠️  Malformed JSON after retry. Raw (first 200 chars): {raw[:200]}", file=sys.stderr)
            return {"subjects": [], "objects": [], "_warnings": ["malformed JSON"]}
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            print(f"  ⚠️  API failure: {e}", file=sys.stderr)
            return {"subjects": [], "objects": [], "_warnings": [str(e)]}

    return {"subjects": [], "objects": [], "_warnings": ["unknown"]}


def _parse_json(raw: str) -> dict:
    """Parse LLM response, handling markdown code fences."""
    raw = raw.strip()
    # Strip ```json ... ``` fences
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Remove opening fence (```json or ```)
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines)
    return json.loads(raw)


def _validate(parsed: dict) -> dict:
    """Strip invalid kind values, count in warnings."""
    warnings: list[str] = []
    subjects = []
    for s in parsed.get("subjects", []):
        if not isinstance(s, dict) or "name" not in s:
            continue
        kind = s.get("subjectKind", "other")
        if kind not in VALID_SUBJECT_KINDS:
            warnings.append(f"invalid subjectKind '{kind}' for '{s['name']}' — stripped")
            continue
        subjects.append({"name": s["name"], "subjectKind": kind, "evidence": s.get("evidence", "")})

    objects = []
    for o in parsed.get("objects", []):
        if not isinstance(o, dict) or "name" not in o:
            continue
        kind = o.get("objectKind", "other")
        if kind not in VALID_OBJECT_KINDS:
            warnings.append(f"invalid objectKind '{kind}' for '{o['name']}' — stripped")
            continue
        objects.append({"name": o["name"], "objectKind": kind, "evidence": o.get("evidence", "")})

    return {"subjects": subjects, "objects": objects, "_warnings": warnings}


# ── precision measurement ──
def wilson_ci(tp: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% confidence interval for proportion."""
    if total == 0:
        return (0.0, 1.0 if tp == 0 else 0.0)
    p = tp / total
    n = total
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def measure_precision(extracted: dict, ground_truth: dict) -> dict:
    """Strict entity matching: case-insensitive name normalize + exact kind match."""
    gt_subjects = {(s["name"], s["subjectKind"]) for s in ground_truth.get("entities", {}).get("subjects", [])}
    gt_objects = {(o["name"], o["objectKind"]) for o in ground_truth.get("entities", {}).get("objects", [])}
    gt_all = gt_subjects | gt_objects

    ext_subjects = {(_normalize(s["name"]), s["subjectKind"]) for s in extracted.get("subjects", [])}
    ext_objects = {(_normalize(o["name"]), o["objectKind"]) for o in extracted.get("objects", [])}
    ext_all = ext_subjects | ext_objects

    # GT set also normalized for matching
    gt_normalized = {(_normalize(name), kind) for name, kind in gt_all}
    tp = len(ext_all & gt_normalized)
    fp = len(ext_all - gt_normalized)
    total_gt = len(gt_normalized)

    # edge cases
    if tp + fp == 0:
        precision = 1.0 if total_gt == 0 else 0.0
    else:
        precision = tp / (tp + fp)

    lb, ub = wilson_ci(tp, tp + fp)
    recall = tp / total_gt if total_gt > 0 else 0.0

    fp_examples = []
    for name, kind in ext_all - gt_normalized:
        reason = "not in ground truth"
        fp_examples.append({"extracted": {"name": name, "kind": kind}, "reason": reason})

    return {
        "sourceFile": ground_truth.get("sourceFile", ""),
        "precision": round(precision, 4),
        "wilsonLowerBound": round(lb, 4),
        "wilsonUpperBound": round(ub, 4),
        "recall": round(recall, 4),
        "truePositives": tp,
        "falsePositives": fp,
        "totalGroundTruth": total_gt,
        "falsePositiveExamples": fp_examples[:5],
    }


def aggregate(results: list[dict]) -> dict:
    total_tp = sum(r["truePositives"] for r in results)
    total_fp = sum(r["falsePositives"] for r in results)
    total = total_tp + total_fp
    precision = total_tp / total if total > 0 else 0.0
    lb, ub = wilson_ci(total_tp, total)
    return {
        "precision": round(precision, 4),
        "wilsonLowerBound": round(lb, 4),
        "wilsonUpperBound": round(ub, 4),
        "totalTP": total_tp,
        "totalFP": total_fp,
        "perDoc": results,
        "gatePassed": lb >= 0.80,
    }


# ── main ──
def main() -> int:
    if not API_KEY:
        print("❌ OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    results = []
    extraction_failed = False

    for label, rel_path in DOCS:
        doc_path = PROJECT_ROOT / rel_path
        if not doc_path.exists():
            print(f"  ⚠️  {label}: not found at {doc_path}", file=sys.stderr)
            extraction_failed = True
            continue

        gt_path = SPIKE_DIR / "ground_truth" / f"{label}-research-brief.json" if label == "D1" else \
                  SPIKE_DIR / "ground_truth" / f"{label}-ontology.json" if label == "D2" else \
                  SPIKE_DIR / "ground_truth" / f"{label}-plan.json"

        doc_text = doc_path.read_text()
        gt = json.loads(gt_path.read_text())

        # Plan §5.1 step 2: token estimation guard
        est_tokens = len(doc_text) / 4
        if est_tokens > 55000:
            print(f"  ⚠️  {label}: estimated {est_tokens:.0f} tokens > 55K limit — skipping", file=sys.stderr)
            extraction_failed = True
            continue

        # Plan R7: credential scan before sending to external LLM
        cred_hits = _scan_credentials(doc_text)
        if cred_hits:
            print(f"  ⚠️  {label}: credential patterns found ({cred_hits}) — skipping", file=sys.stderr)
            extraction_failed = True
            continue

        print(f"  Extracting {label} ({len(doc_text)} chars)...", file=sys.stderr)

        extracted = extract_entities(doc_text, MODEL)
        warnings = extracted.pop("_warnings", [])

        # Plan R11: extraction failure → exit 2, don't aggregate
        if any("API key missing" in w or "malformed JSON" in w or "API failure" in str(w)
               for w in warnings):
            print(f"  ❌ {label}: extraction failed — gate inconclusive", file=sys.stderr)
            extraction_failed = True
            continue

        # Plan §4.1: wrap in ExtractionOutput schema for audit trail
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        extraction_output = {
            "sourceFile": rel_path,
            "extractedAt": now,
            "modelUsed": MODEL,
            "entities": {"subjects": extracted.get("subjects", []),
                         "objects": extracted.get("objects", [])},
            "validationWarnings": warnings,
        }

        out_path = SPIKE_DIR / "output" / f"{label}-extraction.json"
        out_path.write_text(json.dumps(extraction_output, indent=2, ensure_ascii=False))

        result = measure_precision(extracted, gt)
        results.append(result)

        print(f"    Precision: {result['precision']:.1%} (TP={result['truePositives']}, "
              f"FP={result['falsePositives']}, GT={result['totalGroundTruth']})", file=sys.stderr)
        if warnings:
            print(f"    Warnings: {warnings}", file=sys.stderr)

    agg = aggregate(results)

    print("\n" + "=" * 60)
    print(f"Aggregated Precision: {agg['precision']:.1%}")
    print(f"Wilson 95% CI: [{agg['wilsonLowerBound']:.1%}, {agg['wilsonUpperBound']:.1%}]")
    print(f"Total TP: {agg['totalTP']}, Total FP: {agg['totalFP']}")
    print(f"Gate: {'✅ PASS' if agg['gatePassed'] else '❌ FAIL'} (lower-bound ≥ 80%)")
    print("=" * 60)

    for r in agg["perDoc"]:
        print(f"\n  {r['sourceFile']}")
        print(f"    Precision: {r['precision']:.1%}  Recall: {r['recall']:.1%}  "
              f"TP: {r['truePositives']}  FP: {r['falsePositives']}  GT: {r['totalGroundTruth']}")

    agg_path = SPIKE_DIR / "output" / "aggregated-precision.json"
    agg_path.write_text(json.dumps(agg, indent=2, ensure_ascii=False))

    # Plan R11: gate requires all 3 docs processed. Any skip or extraction failure → exit 2.
    if extraction_failed or len(results) < len(DOCS):
        print(f"❌ Only {len(results)}/{len(DOCS)} docs extracted — gate inconclusive", file=sys.stderr)
        return 2
    return 0 if agg["gatePassed"] else 1


if __name__ == "__main__":
    sys.exit(main())
