#!/usr/bin/env python3
"""Kind-classification measurement foundation (issue #1695, Task 2).

Implements the pre-registered experiment instruments BEFORE any classify-
later code (the D0-2 probe is the BUILD GATE for the classifier):

``--probe`` — D0-2 kind-separability/tail-rate probe (pennies, no LLM):
embeds the FULL kind vocabulary (54 kinds: core objects + subjects + points
+ events + pack kindDefs) with the production embedder (BAAI/bge-small-en-
v1.5, TF-IDF degrade), computes pairwise inter-kind similarity, the top-5
hit rate on the gold bits, and the adjudication-tail fraction. **BUILD
GATE: tail fraction < 40% AND top-5 hit >= 0.85** — the plan's in-domain
conversion of the transfer research before the build commits.

``--eval <gold.jsonl>`` — D0-3 bit-level eval surface: validates the gold
set metadata (provenance, calibrate/holdout split, closed-vocab gold kinds,
nearMiss references, sentinel bounded class, pack-stratum minimum) and,
when ``tortoise.kind_classifier`` is importable (Task 4), computes the
per-arm bit metrics (precision, top-5 hit, adjudication rate, nearMiss
demotion, sentinel rate, no-pack-stratum row). The classifier's own
``--eval`` CLI is the authoritative offline eval; this tool owns the probe
and the gold-set schema checks.

Usage:
    python tools/kind_eval.py --probe [--gold tests/fixtures/kinds_gold.mini.jsonl]
    python tools/kind_eval.py --eval tests/fixtures/kinds_gold.mini.jsonl --arm compact
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = REPO_ROOT / "tests" / "fixtures" / "kinds_gold.mini.jsonl"

# D0-3/D0-2 pre-registered thresholds (frozen before any run).
TAIL_FRACTION_GATE = 0.40  # adjudication-tail fraction must be < 40%
TOP5_HIT_GATE = 0.85  # gold in embedding-top-5 must be >= 0.85
SENTINEL_RATE_CAP = 0.05  # D0-3: sentinel rate <= 5% of bits (else block)
GOLD_SPLITS = ("calibrate", "holdout")
GOLD_TYPES = ("entity", "event", "point")


# ── Gold-set schema / metadata validation (D0-3) ───────────────────────────


def load_gold(path: Path | str) -> list[dict]:
    """Load + validate the bit-level gold set. Returns the bits (dicts).

    Raises ValueError on any schema violation: every line is a JSON object
    with id/content/type/gold_kind/split/provenance (source + author); ids
    are unique; gold_kind must be a closed-vocabulary kind (checked against
    the caller-supplied vocabulary via ``validate_gold_metadata``);
    gold_kind must never be the ``unclassified`` sentinel (it is a SEPARATE
    bounded class — the sentinel rate gate); split ∈ {calibrate, holdout};
    near_miss_of refs are kind names.
    """
    bits: list[dict] = []
    seen_ids: set[str] = set()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"gold set not found: {p}")
    for lineno, line in enumerate(p.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            bit = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{lineno} not a JSON object: {e}") from e
        if not isinstance(bit, dict):
            raise ValueError(f"{p}:{lineno} must be a JSON object")
        for key in ("id", "content", "type", "gold_kind", "split", "provenance"):
            if key not in bit:
                raise ValueError(f"{p}:{lineno} missing required key {key!r}")
        if str(bit["id"]).strip() in seen_ids:
            raise ValueError(f"{p}:{lineno} duplicate bit id {bit['id']!r}")
        seen_ids.add(str(bit["id"]).strip())
        if bit["type"] not in GOLD_TYPES:
            raise ValueError(f"{p}:{lineno} type {bit['type']!r} not in {GOLD_TYPES}")
        if bit["split"] not in GOLD_SPLITS:
            raise ValueError(f"{p}:{lineno} split {bit['split']!r} not in {GOLD_SPLITS}")
        if str(bit.get("gold_kind", "")).lower() == "unclassified":
            raise ValueError(
                f"{p}:{lineno} gold_kind must never be the 'unclassified' "
                "sentinel (separate bounded class — sentinel rate gate)"
            )
        prov = bit["provenance"]
        if not isinstance(prov, dict) or not prov.get("source"):
            raise ValueError(f"{p}:{lineno} provenance must be a dict with a source")
        if not prov.get("author"):
            raise ValueError(
                f"{p}:{lineno} provenance.author required (owner+adjudicator labeling discipline)"
            )
        for n in bit.get("near_miss_of", []) or []:
            if not isinstance(n, str) or not n:
                raise ValueError(f"{p}:{lineno} near_miss_of entries must be kind strings")
        bits.append(bit)
    if not bits:
        raise ValueError(f"{p}: empty gold set (no bits)")
    return bits


def validate_gold_metadata(bits: list[dict], vocab: set[str]) -> dict:
    """D0-3 metadata contract check. Returns a stats dict; raises ValueError
    on violations: closed-vocab gold kinds, nearMiss refs inside the vocab,
    both splits present, pack-stratum minimum (>=1 pack-kind bit), 10%
    agreement check placeholder (provenance.author present)."""
    full = {k.lower() for k in vocab}
    bare = {k.lower().rsplit(":", 1)[-1] for k in vocab}
    unknown = []
    for b in bits:
        k = str(b["gold_kind"]).lower()
        if k not in full and k not in bare:
            unknown.append(f"{b['id']}: {b['gold_kind']!r}")
    if unknown:
        raise ValueError("gold kinds outside the closed vocabulary: " + ", ".join(unknown[:5]))
    for b in bits:
        for n in b.get("near_miss_of", []) or []:
            low = n.lower()
            if low not in full and low not in bare:
                raise ValueError(f"{b['id']}: nearMiss ref {n!r} outside the vocabulary")
    splits = {b["split"] for b in bits}
    missing = set(GOLD_SPLITS) - splits
    if missing:
        raise ValueError(f"gold set missing split(s): {sorted(missing)}")
    pack_n = sum(
        1
        for b in bits
        if ":" in str(b["gold_kind"]) and not str(b["gold_kind"]).lower().startswith("core:")
    )
    if pack_n == 0:
        raise ValueError(
            "gold set has NO pack-stratum bits — the pack-stratum gate "
            "(per-pack n for a -8pt/10pp detection ~392 bits/pack, oversampled "
            "per the dataset audit) is unreachable; oversample pack-heavy "
            "haystacks or downgrade the stratum gate to warn (documented)"
        )
    authors = {b["provenance"].get("author") for b in bits}
    stats = {
        "bits": len(bits),
        "splits": {s: sum(1 for b in bits if b["split"] == s) for s in GOLD_SPLITS},
        "types": {t: sum(1 for b in bits if b["type"] == t) for t in GOLD_TYPES},
        "pack_stratum_bits": pack_n,
        "authors": sorted(a for a in authors if a),
        "near_miss_bits": sum(1 for b in bits if b.get("near_miss_of")),
    }
    return stats


# ── D0-2 separability probe ────────────────────────────────────────────────


def _kind_texts(master: dict) -> tuple[list[str], list[str]]:
    """(kind_names, kind_texts) — the classification surface per kind.

    The D0-2 refinement path (plan-mandated before the build commits): the
    surface is the ``compile_kind_index_spec`` text — description + pack
    kindDefs synonyms/examples re-weighted — NOT the bare master-list
    descriptions (the first probe run against bare descriptions FAILED the
    build gate: top-5 0.19 / tail 0.56 on the mini-gold).
    """
    from tortoise.value_extractor import compile_kind_index_spec

    spec = compile_kind_index_spec()
    names = sorted(spec)
    return names, [spec[k]["text"] for k in names]


def _cosine_sim(a, b):
    import numpy as np

    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(a @ b / (na * nb))


def run_probe(gold_path: Path | str = DEFAULT_GOLD, margin: float = 0.05) -> dict:
    """D0-2: embed the kind vocab, measure inter-kind similarity + the
    top-5 hit / adjudication-tail on the gold bits. No LLM. Returns the
    probe record (JSON-able); prints the BUILD GATE verdict."""
    from tortoise import extractor_v2 as v2
    from tortoise.embeddings import _encode

    master = v2.build_master_list()
    names, texts = _kind_texts(master)
    kind_vecs, degraded = _encode(texts)

    gold = load_gold(gold_path)
    vocab = v2.master_kind_forms(master)
    validate_gold_metadata(gold, vocab)

    # gold bit vectors — the SAME encoder family as the kinds so the cosine
    # spaces match (the TF-IDF degrade fits ONE vectorizer on the kind texts
    # and TRANSFORMS the bits — a per-call refit would mismatch dimensions)
    bit_texts = [f"{b['content']}" for b in gold]
    if degraded:
        from sklearn.feature_extraction.text import TfidfVectorizer

        tv = TfidfVectorizer()
        kind_vecs = tv.fit_transform(texts).toarray()
        bit_vecs = tv.transform(bit_texts).toarray()
    else:
        from tortoise.embeddings import EmbeddingModel as _EM

        model = _EM.get()
        if model is None:
            raise RuntimeError("embedder unavailable")
        import numpy as _np

        kind_vecs = _np.asarray(kind_vecs, dtype=_np.float64)
        bit_vecs = _np.asarray(model.encode(bit_texts, show_progress_bar=False), dtype=_np.float64)

    # pairwise inter-kind similarity (upper triangle)
    import numpy as np

    n = len(names)
    sims: list[float] = []
    max_neighbor: dict[str, tuple[str, float]] = {}
    for i in range(n):
        best = (None, -1.0)
        for j in range(i + 1, n):
            s = _cosine_sim(kind_vecs[i], kind_vecs[j])
            sims.append(s)
            if s > best[1]:
                best = (names[j], s)
        if best[0] is not None:
            max_neighbor[names[i]] = best
    kind_sims = np.array(sims) if sims else np.zeros(0)

    # gold top-5 hit + adjudication-tail (bit_vecs computed above with the
    # SAME encoder family as the kind vectors)
    top5_hits = 0
    tail = 0
    for i, b in enumerate(gold):
        sims_i = [_cosine_sim(bit_vecs[i], kind_vecs[j]) for j in range(n)]
        order = sorted(range(n), key=lambda j: sims_i[j], reverse=True)
        top5 = [names[j].lower() for j in order[:5]]
        gold_kind = str(b["gold_kind"]).lower()
        gold_form = gold_kind.rsplit(":", 1)[-1]
        if gold_kind in top5 or gold_form in top5:
            top5_hits += 1
        # margin = top1 - top2 (below-floor bits count toward the tail —
        # they need adjudication or the unclassified terminal)
        margin_i = sims_i[order[0]] - sims_i[order[1]] if n > 1 else 1.0
        if margin_i < margin:
            tail += 1

    record = {
        "probe": "D0-2",
        "embedder": "bge-small-en-v1.5 (TF-IDF degraded)" if degraded else "BAAI/bge-small-en-v1.5",
        "kinds": n,
        "gold_bits": len(gold),
        "inter_kind_sim_mean": round(float(kind_sims.mean()), 4) if kind_sims.size else None,
        "inter_kind_sim_max": round(float(kind_sims.max()), 4) if kind_sims.size else None,
        "most_confusable": sorted(max_neighbor.items(), key=lambda kv: -kv[1][1])[:5],
        "top5_hit_rate": round(top5_hits / len(gold), 4),
        "tail_fraction": round(tail / len(gold), 4),
        "gate": {
            "tail_lt_40pct": (tail / len(gold)) < TAIL_FRACTION_GATE,
            "top5_hit_ge_85pct": (top5_hits / len(gold)) >= TOP5_HIT_GATE,
        },
    }
    return record


# ── D0-3 eval surface (classifier-backed when available) ───────────────────


def run_eval(gold_path: Path | str, arm: str) -> dict:
    """D0-3: gold-set metadata validation + (when tortoise.kind_classifier
    is importable) the per-arm bit metrics. The classifier's own --eval CLI
    is authoritative; this wrapper owns the schema checks."""
    from tortoise import extractor_v2 as v2

    gold = load_gold(gold_path)
    vocab = v2.master_kind_forms(v2.build_master_list())
    stats = validate_gold_metadata(gold, vocab)
    result = {"arm": arm, "gold_metadata": stats}
    try:
        from tortoise.kind_classifier import evaluate_bits  # Task 4

        result["metrics"] = evaluate_bits(gold, arm=arm)
    except ImportError:
        result["metrics"] = {
            "note": "tortoise.kind_classifier not built yet (Task 4) — metadata validation only"
        }
    return result


VALID_ARMS = ("verbose", "compact", "flag-on")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--probe", action="store_true", help="D0-2 kind-separability/tail-rate probe (no LLM)"
    )
    ap.add_argument(
        "--eval", metavar="GOLD.jsonl", default=None, help="D0-3 bit-level eval on the gold set"
    )
    ap.add_argument("--arm", default="compact", help="eval arm label (verbose|compact|flag-on)")
    ap.add_argument("--gold", default=str(DEFAULT_GOLD), help="gold set path")
    ap.add_argument(
        "--margin",
        type=float,
        default=0.05,
        help="adjudication-tail margin threshold (default 0.05)",
    )
    args = ap.parse_args(argv)

    if args.probe:
        record = run_probe(args.gold, margin=args.margin)
        print(json.dumps(record, indent=2))
        gate = record["gate"]
        ok = gate["tail_lt_40pct"] and gate["top5_hit_ge_85pct"]
        print(
            f"\nBUILD GATE: {'PASS' if ok else 'FAIL'} "
            f"(tail < {TAIL_FRACTION_GATE}: {gate['tail_lt_40pct']}, "
            f"top-5 >= {TOP5_HIT_GATE}: {gate['top5_hit_ge_85pct']})"
        )
        return 0 if ok else 2
    if args.eval:
        if args.arm not in VALID_ARMS:
            print(
                f"invalid --arm {args.arm!r} — pre-registered arms: {', '.join(VALID_ARMS)}",
                file=sys.stderr,
            )
            return 2
        result = run_eval(args.eval, args.arm)
        print(json.dumps(result, indent=2))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
