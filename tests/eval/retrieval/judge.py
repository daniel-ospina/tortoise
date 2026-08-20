"""Retrieval relevance judges (issue #1144) — two cross-vendor LLM judges +
owner adjudication, the #946/#961 gate pattern.

The AUTHORED queries (50, over real internal graph data) have SUBJECTIVE
relevance: no deterministic oracle exists for them. They are labeled by two
cross-vendor LLM judges (temperature 0.0) over the pooled top-50 per
strategy per query (deduped — the runner emits the pool via --pool-out),
with the #946/#961 agreement gates:

    - κ ≥ 0.60 inter-judge agreement (tools/kappa.py — reuse)
    - owner adjudication on disagreements: ≥85% acceptance on the
      combined denominator, adjudicating a ≥10% sample (all disagreements
      plus a stratified agreement slice when disagreements < 10%)

The relevance rubric is NEW — the extraction rubric (#945) does not fit
retrieval. Three graded levels matching the oracle's:

    2 = relevant — directly answers / is about the query
    1 = partially — related context, adjacent concept, background
    0 = non-relevant — unrelated to the query

Oracle queries (synthetic core) are NOT judged — their labels are
deterministic (synthetic_corpus.oracle_grades_for_query).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable  # noqa: F401, UP035

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools import kappa as kappa_tool  # noqa: E402
from tools.judge_harness import Label  # noqa: E402

GRADE_VOCAB = ("non_relevant", "partially", "relevant")  # 0, 1, 2
GRADE_TO_INT = {"non_relevant": 0, "partially": 1, "relevant": 2}

POOL_SCHEMA_VERSION = 1


class JudgeError(ValueError):
    """Malformed pool, judge response, or adjudication input."""


# ── The relevance rubric (NEW — retrieval, not extraction) ─────────────────

RELEVANCE_RUBRIC_SYSTEM = (
    "You are a retrieval relevance judge for Tortoise, an epistemic memory "
    "graph engine (issue #1144 retrieval quality eval). For ONE search query "
    "you grade a POOL of retrieved graph points on how relevant each point "
    "is to the query's information need.\n"
    "\n"
    "## Graded relevance (3 levels — closed vocabulary)\n"
    "2 = relevant: the point directly answers the query or is centrally "
    "about its topic — a user would want it in the top results.\n"
    "1 = partially relevant: related background or adjacent context — "
    "mentions the topic or a directly connected concept, useful but not "
    "what the query is about.\n"
    "0 = non-relevant: unrelated to the query — incidental token overlap "
    "or a different topic entirely.\n"
    "\n"
    "## Rules\n"
    "- Grade the point's CONTENT against the QUERY's intent, not just token "
    "overlap: a point that merely shares a word with the query but is about "
    "a different subject is 0.\n"
    "- A point that is on-topic but tangential is 1, not 2.\n"
    "- Grade EVERY point in the pool exactly once — never omit one.\n"
    "\n"
    "## Output format\n"
    'Return ONLY a JSON object: {"query_id": "<id>", "verdicts": '
    '[{"id": "<point id>", "grade": 2|1|0}, ...]} with ONE verdict per '
    'point id in the pool. Do not wrap the JSON in markdown fences.'
)


# ── Pool format (runner emits via --pool-out) ───────────────────────────────

@dataclass
class PoolPoint:
    id: str
    content: str
    point_kind: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content, "point_kind": self.point_kind}


@dataclass
class PoolQuery:
    id: str
    query: str
    points: list[PoolPoint] = field(default_factory=list)


@dataclass
class Pool:
    queries: list[PoolQuery]
    corpus_fingerprint: dict = field(default_factory=dict)
    strategies: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Pool":  # noqa: UP037
        if data.get("schema_version") != POOL_SCHEMA_VERSION:
            raise JudgeError(
                f"unsupported pool schema_version {data.get('schema_version')!r}"
            )
        queries = []
        for q in data.get("queries", []):
            points = [
                PoolPoint(id=p["id"], content=p.get("content", ""),
                          point_kind=p.get("point_kind", ""))
                for p in q.get("points", [])
            ]
            queries.append(PoolQuery(id=q["id"], query=q["query"], points=points))
        return cls(
            queries=queries,
            corpus_fingerprint=data.get("corpus_fingerprint", {}),
            strategies=data.get("strategies", []),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": POOL_SCHEMA_VERSION,
            "corpus_fingerprint": self.corpus_fingerprint,
            "strategies": self.strategies,
            "queries": [
                {"id": q.id, "query": q.query,
                 "points": [p.to_dict() for p in q.points]}
                for q in self.queries
            ],
        }


def build_pool(per_query_results: dict[str, dict], corpus_fingerprint: dict,
               strategies: list[str]) -> Pool:
    """Pool the top-K per strategy per query, deduped per query.

    per_query_results: {query_id: {strategy: [point dicts]}} — the runner's
    per-query per-strategy top-50s. Point dicts must carry id/content/
    point_kind. Order within a strategy is preserved (rank), but the pool
    itself is an unordered set per query (judges grade relevance, not rank).
    """
    queries = []
    for qid in sorted(per_query_results):
        seen: dict[str, PoolPoint] = {}
        for strat in strategies:
            for p in per_query_results[qid].get(strat, []):
                pid = p["id"]
                if pid not in seen:
                    seen[pid] = PoolPoint(
                        id=pid, content=p.get("content", ""),
                        point_kind=p.get("point_kind", ""),
                    )
        queries.append(PoolQuery(id=qid, query=per_query_results[qid].get("_query", ""),
                                 points=list(seen.values())))
    return Pool(queries=queries, corpus_fingerprint=corpus_fingerprint,
                strategies=strategies)


# ── Judge response parsing ──────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def parse_judge_response(raw: str, query_id: str, point_ids: list[str]) -> dict[str, int]:
    """Parse one judge's JSON response into {point_id: grade 0|1|2}.

    Hard-validated: every pooled point must have exactly one verdict, and
    the grade must be in {0, 1, 2} — the gate must never silently drop or
    misread a point (judge_harness "never omit an EDU" convention).
    """
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise JudgeError(
            f"{query_id}: judge response is not JSON (near {exc.pos}): "
            f"{raw[:200]!r}"
        ) from exc
    if not isinstance(data, dict):
        raise JudgeError(f"{query_id}: judge response is not an object")
    if data.get("query_id") not in (None, query_id):
        raise JudgeError(
            f"{query_id}: judge response query_id {data.get('query_id')!r} "
            f"mismatch"
        )
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, list):
        raise JudgeError(f"{query_id}: missing 'verdicts' array")
    labels: dict[str, int] = {}
    emitted: set[str] = set()
    for i, item in enumerate(verdicts):
        if not isinstance(item, dict) or "id" not in item or "grade" not in item:
            raise JudgeError(f"{query_id}: verdicts[{i}] missing 'id'/'grade'")
        pid = str(item["id"])
        if pid not in point_ids:
            raise JudgeError(f"{query_id}: verdict id {pid!r} not in the pool")
        if pid in emitted:
            raise JudgeError(f"{query_id}: duplicate verdict for point {pid!r}")
        grade = item["grade"]
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in (0, 1, 2):
            raise JudgeError(
                f"{query_id}: verdict for {pid!r} has grade {grade!r} — "
                "must be 0|1|2"
            )
        emitted.add(pid)
        labels[pid] = grade
    missing = set(point_ids) - emitted
    if missing:
        raise JudgeError(
            f"{query_id}: judge omitted {len(missing)} pooled points "
            f"(e.g. {sorted(missing)[:3]}) — never omit a point"
        )
    return labels


def build_judge_user_prompt(pq: PoolQuery) -> str:
    """Numbered pool view handed to the judge (index is the point id)."""
    lines = [f"QUERY: {pq.query}", ""]
    lines.extend(f"{i + 1}. [{p.point_kind}] {p.content}" for i, p in enumerate(pq.points))
    return "\n".join(lines)


def _judge_query(model, pq: PoolQuery) -> dict[str, int]:
    response = model.complete(
        system=RELEVANCE_RUBRIC_SYSTEM,
        user=build_judge_user_prompt(pq),
    )
    return parse_judge_response(response, pq.id, [p.id for p in pq.points])


# ── Agreement (reuse tools/kappa.py) ────────────────────────────────────────

def _kappa_labels(labels: dict[str, int]) -> list[Label]:
    """Convert {pid: grade} → tools.judge_harness.Label for tools.kappa.

    The point id is carried in edu_index (kappa only compares class_
    equality — the vocab is relevance grades here, not extraction classes).
    """
    return [
        Label(edu_index=i, class_=GRADE_VOCAB[g])
        for i, (pid, g) in enumerate(sorted(labels.items()))
    ]


def agreement_report(labels_a: dict[str, dict[str, int]],
                     labels_b: dict[str, dict[str, int]],
                     query_meta: dict[str, str]) -> dict:
    """κ + per-query agreement over two judges' label sets.

    labels_a/b: {query_id: {point_id: grade}}. Returns the #946/#961 gate
    report: overall κ (pooled over all judged points, paired per query),
    per-query κ, and the GREEN/NOT_GREEN/REVISE verdict using the reused
    thresholds from tools/kappa.py (KAPPA_GREEN=0.60, KAPPA_REVISE=0.50).
    """
    a_all: dict[str, int] = {}
    b_all: dict[str, int] = {}
    per_query: dict[str, dict] = {}
    common_queries = sorted(set(labels_a) & set(labels_b))
    for qid in common_queries:
        a, b = labels_a[qid], labels_b[qid]
        ka = _kappa_labels(a)
        kb = _kappa_labels(b)
        per_query[qid] = {
            "query": query_meta.get(qid, ""),
            "kappa": kappa_tool.kappa(ka, kb),
            "n_points": len(set(a) & set(b)),
        }
        a_all.update(a)
        b_all.update(b)
    k = kappa_tool.kappa(_kappa_labels(a_all), _kappa_labels(b_all))
    if k is None:
        verdict, reason = "NOT_GREEN", "no overlapping judged points"
    elif k >= kappa_tool.KAPPA_GREEN:
        verdict, reason = "GREEN", (
            f"kappa {k:.3f} >= {kappa_tool.KAPPA_GREEN} — agreement gate "
            "satisfied (then owner adjudication on disagreements)"
        )
    elif k >= kappa_tool.KAPPA_REVISE:
        verdict, reason = "NOT_GREEN", (
            f"kappa {k:.3f} in [{kappa_tool.KAPPA_REVISE}, "
            f"{kappa_tool.KAPPA_GREEN}) — middle band: expand labeling or "
            "proceed to adjudication; the gate is not satisfied"
        )
    else:
        verdict, reason = "REVISE", (
            f"kappa {k:.3f} < {kappa_tool.KAPPA_REVISE} — rubric revision; "
            "the workflow stops"
        )
    return {
        "judged_queries": len(common_queries),
        "judged_points": len(a_all),
        "kappa": k,
        "per_query": per_query,
        "gate": {"verdict": verdict, "reason": reason,
                 "kappa_green": kappa_tool.KAPPA_GREEN,
                 "kappa_revise": kappa_tool.KAPPA_REVISE},
    }


# ── Adjudication (owner, file-driven) ──────────────────────────────────────

ADJUDICATION_ACCEPTANCE_FLOOR = 0.85   # combined-denominator acceptance ≥ 85%
ADJUDICATION_SAMPLE_FRACTION = 0.10    # owner must rule a ≥10% sample


def _pairs(labels: dict[str, dict[str, int]]) -> list[tuple[str, str, int]]:
    return [(qid, pid, g) for qid, labs in sorted(labels.items())
            for pid, g in sorted(labs.items())]


def _disagreements(labels_a, labels_b) -> list[dict]:
    b_lookup = {(qid, pid): g for qid, labs in labels_b.items()
                for pid, g in labs.items()}
    out = []
    for qid, pid, ga in _pairs(labels_a):
        gb = b_lookup.get((qid, pid))
        if gb is None or gb == ga:
            continue
        out.append({"query_id": qid, "point_id": pid,
                    "judge_a_grade": ga, "judge_b_grade": gb,
                    "owner_ruling": None})
    return out


def emit_rulings_template(
    labels_a, labels_b, query_meta: dict[str, str],
    points_content: dict[str, str] | None = None,
    seed: int = 1144,
) -> dict:
    """Template the owner fills: ALL disagreements + a stratified ≥10%
    agreement sample (so the combined-denominator acceptance is computable
    even when disagreements alone < 10% of the judged set)."""
    dis = _disagreements(labels_a, labels_b)
    agreed = [
        (qid, pid, g) for qid, pid, g in _pairs(labels_a)
        if not any(d["query_id"] == qid and d["point_id"] == pid for d in dis)
    ]
    n_judged = len(list(_pairs(labels_a)))
    need_sample = max(0, math.ceil(ADJUDICATION_SAMPLE_FRACTION * n_judged) - len(dis))
    rng = random.Random(seed)
    sample = rng.sample(agreed, min(need_sample, len(agreed))) if need_sample else []
    rulings = []
    for d in dis:
        d = dict(d)
        d["content"] = (points_content or {}).get(d["point_id"], "")
        d["sample_type"] = "disagreement"
        rulings.append(d)
    for qid, pid, g in sample:
        rulings.append({
            "query_id": qid, "point_id": pid,
            "judge_a_grade": g, "judge_b_grade": g,
            "owner_ruling": None, "sample_type": "agreement-slice",
            "content": (points_content or {}).get(pid, ""),
        })
    return {
        "judge_a": "judgeA",
        "judge_b": "judgeB",
        "n_judged": n_judged,
        "n_disagreements": len(dis),
        "sample_fraction_required": ADJUDICATION_SAMPLE_FRACTION,
        "acceptance_floor": ADJUDICATION_ACCEPTANCE_FLOOR,
        "note": (
            "owner_ruling must be an int 0|1|2. Acceptance (combined "
            "denominator) = (agreed + rulings matching a judge) / n_judged; "
            "gate: acceptance >= 85% AND sample coverage >= 10%."
        ),
        "rulings": rulings,
    }


def adjudication_stats(rulings: dict) -> dict:
    """Compute acceptance over the owner's filled template.

    Accepted ruling = owner's grade equals a judge's grade (disagreement)
    or equals the shared grade (agreement-slice). Combined-denominator
    acceptance counts ALL agreed pairs as accepted (both judges agreed),
    plus accepted rulings, over n_judged. Sample coverage = ruled pairs
    (disagreements + agreement-slice) / n_judged.
    """
    n_judged = rulings["n_judged"]
    ruled = rulings.get("rulings", [])
    accepted_rulings = 0
    coverage = 0
    for r in ruled:
        ruling = r.get("owner_ruling")
        if ruling is None:
            continue
        coverage += 1
        if ruling in (r.get("judge_a_grade"), r.get("judge_b_grade")):
            accepted_rulings += 1
    n_agreed = n_judged - rulings.get("n_disagreements", 0)
    acceptance = (n_agreed + accepted_rulings) / n_judged if n_judged else 0.0
    sample_fraction = coverage / n_judged if n_judged else 0.0
    passed = (
        acceptance >= ADJUDICATION_ACCEPTANCE_FLOOR
        and sample_fraction >= ADJUDICATION_SAMPLE_FRACTION
        and coverage >= rulings.get("n_disagreements", 0)
    )
    return {
        "n_judged": n_judged,
        "n_agreed": n_agreed,
        "n_disagreements": rulings.get("n_disagreements", 0),
        "n_ruled": coverage,
        "accepted_rulings": accepted_rulings,
        "acceptance": round(acceptance, 4),
        "acceptance_floor": ADJUDICATION_ACCEPTANCE_FLOOR,
        "sample_fraction": round(sample_fraction, 4),
        "sample_fraction_required": ADJUDICATION_SAMPLE_FRACTION,
        "passed": passed,
    }


def merge_labels(labels_a, labels_b, rulings: dict) -> dict[str, dict[str, int]]:
    """Merged adjudicated labels: owner ruling on disagreements (must match
    a judge — fresh classes are rejected by the acceptance gate), either
    judge's grade on agreements. Raises JudgeError on unruled disagreements
    or fresh (non-judge) rulings (fail-closed — the gate must never accept
    invented grades)."""
    by_key: dict[tuple[str, str], dict] = {}
    for r in rulings.get("rulings", []):
        by_key[(r["query_id"], r["point_id"])] = r
    merged: dict[str, dict[str, int]] = {}
    for qid, labs in labels_a.items():
        m: dict[str, int] = {}
        for pid, ga in labs.items():
            gb = labels_b.get(qid, {}).get(pid)
            if gb is None or gb == ga:
                m[pid] = ga
                continue
            r = by_key.get((qid, pid))
            if r is None or r.get("owner_ruling") is None:
                raise JudgeError(
                    f"unruled disagreement ({qid}, {pid}) — run adjudication "
                    "before merging"
                )
            ruling = r["owner_ruling"]
            if ruling not in (ga, gb):
                raise JudgeError(
                    f"fresh class {ruling} on ({qid}, {pid}) — owner ruling "
                    "must match a judge (fail-closed)"
                )
            m[pid] = ruling
        merged[qid] = m
    return merged


# ── Judge run ───────────────────────────────────────────────────────────────

def run_judges(
    pool: Pool,
    model_a,
    model_b,
    *,
    judge_names: tuple[str, str] = ("judgeA", "judgeB"),
    max_points_per_query: int | None = None,
) -> tuple[dict, dict, dict]:
    """Label the whole pool with both judges (temp 0.0 adapters).

    One LLM call per query per judge (all pooled points in one call — the
    judge_harness batching pattern). Returns (labels_a, labels_b, report)
    with the κ gate report.
    """
    labels_a: dict[str, dict[str, int]] = {}
    labels_b: dict[str, dict[str, int]] = {}
    for pq in pool.queries:
        pts = pq.points
        if max_points_per_query is not None and len(pts) > max_points_per_query:
            pts = pts[:max_points_per_query]
        pq_capped = PoolQuery(id=pq.id, query=pq.query, points=pts)
        labels_a[pq.id] = _judge_query(model_a, pq_capped)
        labels_b[pq.id] = _judge_query(model_b, pq_capped)
    report = agreement_report(labels_a, labels_b,
                              {q.id: q.query for q in pool.queries})
    return labels_a, labels_b, report


# ── CLI ─────────────────────────────────────────────────────────────────────

def _model_factory(model_name: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
    from tests.model_adapters import MODELS  # noqa: PLC0415, RUF100

    try:
        return MODELS[model_name]()
    except KeyError:
        raise SystemExit(
            f"unknown model {model_name!r} — choose from: {', '.join(sorted(MODELS))}"
        ) from None


def _apply_tuning(model, max_tokens: int, temperature: float) -> None:
    for attr, value in (("max_tokens", max_tokens), ("temperature", temperature)):
        if hasattr(model, attr):
            setattr(model, attr, value)


def _load_judge_json(path: str) -> dict[str, dict[str, int]]:
    data = json.loads(Path(path).read_text())
    out = {}
    for qid, labels in data.items():
        if not isinstance(labels, dict):
            raise JudgeError(f"{path}: {qid}: labels must be an object")
        out[qid] = {str(k): int(v) for k, v in labels.items()}
    return out


def _save(path: str, payload) -> None:
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.retrieval.judge",
        description="Relevance judges for the #1144 authored query set: two "
        "cross-vendor LLM judges (temp 0.0) + κ gate + owner adjudication.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="label a pool with two judges")
    p_run.add_argument("--pool", required=True, help="pool JSON (runner --pool-out)")
    p_run.add_argument("--model-a", default="deepseek-v4-pro-noreason")
    p_run.add_argument("--model-b", default="qwen3.8-max")
    p_run.add_argument("--judge-names", default="judgeA,judgeB")
    p_run.add_argument("--max-points-per-query", type=int, default=None,
                       help="cap pooled points per query (dry-run)")
    p_run.add_argument("--max-tokens", type=int, default=8000)
    p_run.add_argument("--out-dir", required=True)

    p_adj = sub.add_parser("adjudicate", help="owner adjudication on disagreements")
    p_adj.add_argument("judge_a", help="judgeA labels JSON")
    p_adj.add_argument("judge_b", help="judgeB labels JSON")
    p_adj.add_argument("--emit-template", help="write the owner rulings template")
    p_adj.add_argument("--apply-rulings", help="rulings JSON with owner_ruling filled")
    p_adj.add_argument("--out", help="merged adjudicated labels JSON")
    p_adj.add_argument("--pool", default=None,
                       help="pool JSON (optional: content in template)")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        pool = Pool.from_dict(json.loads(Path(args.pool).read_text()))
        a_name, b_name = args.judge_names.split(",")
        model_a, model_b = _model_factory(args.model_a), _model_factory(args.model_b)
        _apply_tuning(model_a, args.max_tokens, 0.0)
        _apply_tuning(model_b, args.max_tokens, 0.0)
        try:
            labels_a, labels_b, report = run_judges(
                pool, model_a, model_b, judge_names=(a_name, b_name),
                max_points_per_query=args.max_points_per_query,
            )
        except (JudgeError, Exception) as exc:  # noqa: BLE001, RUF100
            print(f"judge: error: {exc}", file=sys.stderr)
            return 1
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        _save(str(out_dir / "judgeA.json"), labels_a)
        _save(str(out_dir / "judgeB.json"), labels_b)
        _save(str(out_dir / "agreement.json"), report)
        print(json.dumps(report, indent=2))
        print(f"\njudge labels + agreement written to {out_dir}")
        return 0

    # adjudicate
    labels_a = _load_judge_json(args.judge_a)
    labels_b = _load_judge_json(args.judge_b)
    points_content: dict[str, str] = {}
    if args.pool:
        pool = Pool.from_dict(json.loads(Path(args.pool).read_text()))
        for q in pool.queries:
            for p in q.points:
                points_content.setdefault(p.id, p.content)
    try:
        if args.emit_template:
            tmpl = emit_rulings_template(labels_a, labels_b, {}, points_content)
            _save(args.emit_template, tmpl)
            stats = adjudication_stats(tmpl)
            print(json.dumps(stats, indent=2))
            print(f"\nrulings template: {args.emit_template} "
                  f"({tmpl['n_disagreements']} disagreements)")
            return 0
        if args.apply_rulings:
            rulings = json.loads(Path(args.apply_rulings).read_text())
            stats = adjudication_stats(rulings)
            merged = merge_labels(labels_a, labels_b, rulings)
            if args.out:
                _save(args.out, merged)
            print(json.dumps(stats, indent=2))
            print(f"\nadjudication passed={stats['passed']} — merged labels: "
                  f"{args.out or '(stdout above)'}")
            return 0
    except (JudgeError, OSError, ValueError) as exc:
        print(f"judge: error: {exc}", file=sys.stderr)
        return 1
    parser.error("adjudicate needs --emit-template or --apply-rulings")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
