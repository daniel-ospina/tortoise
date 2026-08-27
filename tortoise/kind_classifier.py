"""The classify-later kind classifier (issue #1695, Task 4).

The post-extraction classification layer: kNN top-5 over the kind index →
margin gate → nearMiss-aware rerank (tie-break only) → batched LLM
adjudication of the low-margin tail (25–50 items per call, object-wrapped),
with all error paths fail-open and census-counted.

## Pipeline (per item)

1. **Encode** the item's classification surface with the injectable encoder
   (production: the ``EmbeddingModel`` singleton + TF-IDF degrade; tests:
   stub fixtures).
2. **kNN top-5** over ``KindIndex.nearest``, restricted per type (entities →
   object+subject kinds; events → event kinds; points → point kinds +
   ``statement``).
3. **Margin gate**: ``top1_sim >= SIM_FLOOR`` and ``top1 - top2 >= MARGIN``
   → assign top-1 (mode ``knn``).
4. **Below floor** → the ``unclassified`` sentinel (terminal — the write
   path resolves it to the best core kind + census; never written to the
   graph as-is).
5. **NearMiss rerank (tie-break only)**: within ``LAMBDA`` of a tie AND the
   top-2 are a declared nearMiss pair → prefer the non-decoy member
   deterministically (mode ``rerank``) instead of burning an adjudication
   call on a pure nearMiss tie.
6. **Batched LLM adjudication** (the low-margin tail, 25–50/call): ONE
   ``_complete_parsed`` call per batch, payload object-wrapped
   (``{item_key: {content, candidates}}`` → ``{item_key: kind}`` —
   ``_parse_json`` handles objects, not arrays). The LLM's pick is
   closed-vocab gated against the index kinds; invalid picks fall back to
   kNN top-1 (never a minted kind).

## Fail-open + census

``embedding_error`` (encode failure), ``classify_error`` (adjudication
failure), and the ``unclassified`` terminal are all counted in ``stats``
and surfaced as warnings — the classifier NEVER raises (the pipeline's
capture path must never depend on classification).

## Determinism

kNN/rerank are fully deterministic (seeded fixtures; float ties broken by
index order). A′ shuffle invariance: the index is built from the SORTED
spec — label order in the render never reaches the classifier.

Thresholds (``SIM_FLOOR`` / ``MARGIN`` / ``LAMBDA``) are D0-3-calibratable
constants — frozen on the calibrate split before the A/B per the plan.
"""

from __future__ import annotations

import json

# ── D0-3-calibratable thresholds (frozen before the A/B) ───────────────────
SIM_FLOOR = 0.30  # below top-1 similarity → unclassified terminal
MARGIN = 0.05  # top1-top2 margin below → LLM adjudication tail
LAMBDA = 0.02  # nearMiss tie-break window (|top1-top2| <= LAMBDA)
ADJUDICATION_BATCH = 40  # plan: 25-50/call
ADJUDICATION_MAX_TOKENS = 1500

#: Per-type candidate sections (kind_index spec sections).
TYPE_SECTIONS = {
    "entity": ("objects", "subjects"),
    "event": ("events",),
    "point": ("points",),
}

#: The unclassified sentinel — resolved at write time (never a graph kind).
UNCLASSIFIED = "unclassified"


def _default_encoder():
    from tortoise.kind_index import _DefaultEncoder

    return _DefaultEncoder()


def _sections_for(item_type: str) -> tuple[str, ...]:
    return TYPE_SECTIONS.get(str(item_type or "").lower(), ("objects", "subjects"))


class KindClassifier:
    """Hybrid kNN + LLM-adjudication kind classifier (injectable seams)."""

    def __init__(
        self,
        *,
        encoder=None,
        index=None,
        model=None,
        sim_floor: float = SIM_FLOOR,
        margin: float = MARGIN,
        lam: float = LAMBDA,
        llm_tail: bool = True,
        batch_size: int = ADJUDICATION_BATCH,
    ) -> None:
        """``encoder``/``index``/``model`` are the injectable seams: the
        production defaults are the EmbeddingModel-singleton encoder, the
        content-addressed ``KindIndex``, and the session's LLM adapter for
        the adjudication tail. ``llm_tail=False`` runs the deterministic
        kNN/rerank path only (offline eval)."""
        self.encoder = encoder if encoder is not None else _default_encoder()
        # Pass the RAW constructor arg: None (the production default path)
        # → load-then-build with persist+memoize; an injected stub encoder
        # → stub build (never memoized — the vector space differs).
        self.index = index if index is not None else self._build_index(encoder)
        self.model = model
        self.sim_floor = sim_floor
        self.margin = margin
        self.lam = lam
        self.llm_tail = llm_tail
        self.batch_size = batch_size
        self._restrict_cache: dict[str, set[str]] = {}

    @staticmethod
    def _build_index(encoder=None):
        """The index for the classification candidate set.

        Production (``encoder is None``): the content-addressed npz is
        LOADED when present and BUILT+PERSISTED when missing (the Task 3
        deliverable — the 54-kind vocabulary is embedded once per content
        hash, never per session). Two degraded-persistence guards (cycle-3
        P2): a persisted index that was built DEGRADED (embedder down at
        build time) never loads — ``KindIndex.load`` returns None and the
        caller rebuilds; and when the CURRENT process's embedder is
        unavailable, a good npz is NOT loaded — the index is rebuilt
        in-process DEGRADED (persist=False, so the good npz survives for
        when the embedder recovers) so the degraded TF-IDF item lane stays
        dimension-matched. Injected stub encoders change the vector space:
        their builds are never memoized/persisted (a stub build must never
        shadow the production index)."""
        import tortoise.kind_index as ki
        from tortoise.value_extractor import compile_kind_index_spec

        spec = compile_kind_index_spec()
        if encoder is None:
            from tortoise.embeddings import EmbeddingModel
            if EmbeddingModel.get() is None:
                # Embedder down NOW: the persisted npz (real-embedder dims)
                # would dimension-mismatch the degraded TF-IDF item lane.
                # Rebuild in-process degraded and do NOT overwrite the good
                # npz. The memo is evicted for this key so a previously
                # loaded/built good-dim index can't shadow the rebuild.
                ki._evict_index(ki.cache_key_for(spec))
                return ki.KindIndex.build(spec, persist=False)
            idx = ki.KindIndex.load(spec)
            if idx is not None:
                return idx
            return ki.KindIndex.build(spec, persist=True)
        return ki.KindIndex.build(spec, encoder=encoder, persist=False)

    # ── candidate restriction ──────────────────────────────────────────────

    def _restrict(self, item_type: str) -> set[str] | None:
        """Per-type candidate restriction: the index kind names whose spec
        section matches the item type (entities → objects+subjects, events
        → events, points → points). None = no restriction."""
        sections = _sections_for(item_type)
        key = ",".join(sections)
        if key in self._restrict_cache:
            return self._restrict_cache[key]
        restrict = {k for k, md in self.index.metadata.items() if md.get("section") in sections}
        if len(restrict) == len(self.index.kind_names):
            restrict = set()
        self._restrict_cache[key] = restrict
        return restrict or None

    # ── classification ─────────────────────────────────────────────────────

    def classify_items(self, items: list[dict]) -> dict:
        """Classify a batch of items.

        ``items``: ``[{"id": key, "type": "entity|event|point",
        "text": <classification surface>}]``.

        Returns ``{"assignments": {id: {"kind", "margin", "mode"}},
        "stats": {...}, "warnings": [...]}`` — never raises (fail-open).
        """
        assignments: dict[str, dict] = {}
        stats = {
            "items": 0,
            "assigned_knn": 0,
            "assigned_rerank": 0,
            "assigned_llm": 0,
            "unclassified": 0,
            "below_floor": 0,
            "adjudication_tail": 0,
            "adjudication_calls": 0,
            "embedding_errors": 0,
            "classify_errors": 0,
            "closed_vocab_rejects": 0,
        }
        warnings: list[str] = []
        tail: list[tuple[str, dict, list[tuple[str, float]]]] = []
        valid = [
            i
            for i in items
            if isinstance(i, dict) and str(i.get("id") or "") and str(i.get("text") or "")
        ]
        stats["items"] = len(valid)
        for item in valid:
            iid = str(item["id"])
            iid = iid if iid not in assignments else f"{iid}#{len(assignments)}"
            try:
                vectors, _degraded = self.encoder.encode([str(item["text"])])
            except Exception as e:  # fail-open: encode must not raise
                warnings.append(
                    f"{iid}: embed failed ({type(e).__name__}) — best-core fallback"
                )
                stats["embedding_errors"] += 1
                assignments[iid] = self._fallback(item)
                continue
            try:
                top = self.index.nearest(
                    vectors[0], k=5, restrict=self._restrict(item.get("type"))
                )
            except Exception as e:  # fail-open: retrieval must not raise
                warnings.append(
                    f"{iid}: retrieval failed ({type(e).__name__}: {e}) — "
                    "best-core fallback"
                )
                stats["classify_errors"] += 1
                assignments[iid] = self._fallback(item)
                continue
            if not top:
                warnings.append(f"{iid}: no candidate kinds — best-core fallback")
                stats["classify_errors"] += 1
                assignments[iid] = self._fallback(item)
                continue
            top1, top2 = top[0], (top[1] if len(top) > 1 else (top[0][0], 0.0))
            top1_sim, top2_sim = top1[1], top2[1]
            if top1_sim < self.sim_floor:
                stats["below_floor"] += 1
                stats["unclassified"] += 1
                assignments[iid] = {
                    "kind": UNCLASSIFIED,
                    "margin": top1_sim,
                    "mode": "unclassified",
                }
                continue
            if top1_sim - top2_sim >= self.margin:
                stats["assigned_knn"] += 1
                assignments[iid] = {"kind": top1[0], "margin": top1_sim, "mode": "knn"}
                continue
            # low margin → nearMiss rerank (tie-break only) → LLM tail
            reranked = self._near_miss_rerank(top)
            if reranked is not None:
                stats["assigned_rerank"] += 1
                rerank_sim = next(s for k, s in top if k == reranked)
                assignments[iid] = {
                    "kind": reranked, "margin": rerank_sim, "mode": "rerank"
                }
                continue
            stats["adjudication_tail"] += 1
            tail.append((iid, item, top))
        if tail and self.llm_tail:
            calls, usage = self._adjudicate_batches(tail, assignments, stats, warnings)
            stats["adjudication_calls"] += calls
            # The batched LLM spend reaches the caller's stats so the A/B
            # cost gate sees the flag-on arm's adjudication calls (the
            # session rollup consumes this via _rollup_llm).
            if usage["attempts"]:
                stats["llm"] = usage
        elif tail:
            # llm_tail off (offline eval): deterministic kNN top-1 fallback
            for iid, _item, top in tail:
                stats["assigned_knn"] += 1
                assignments[iid] = {"kind": top[0][0], "margin": top[0][1], "mode": "knn"}
        return {"assignments": assignments, "stats": stats, "warnings": warnings}

    def _near_miss_rerank(self, top: list[tuple[str, float]]):
        """Tie-break only: when the top-2 sit within ``LAMBDA`` AND one is
        the other's declared nearMiss, prefer the non-decoy member (the
        nearMiss is the confusable; the primary is the real kind). Mutual
        nearMisses keep kNN order. Returns the preferred kind or None (→
        the LLM tail)."""
        if len(top) < 2:
            return None
        if abs(top[0][1] - top[1][1]) > self.lam:
            return None
        a, b = top[0][0], top[1][0]
        nm_a = self.index.near_misses(a)
        nm_b = self.index.near_misses(b)
        b_is_decoy = b in nm_a and a not in nm_b
        a_is_decoy = a in nm_b and b not in nm_a
        if b_is_decoy and not a_is_decoy:
            return a
        if a_is_decoy and not b_is_decoy:
            return b
        return None

    # ── LLM adjudication (batched, object-wrapped) ─────────────────────────

    def _adjudicate_batches(self, tail, assignments, stats, warnings) -> tuple[int, dict]:
        """Batched adjudication: 25–50 items/call, ONE ``_complete_parsed``
        per batch, object-wrapped payload → object response. Fail-open:
        any exception → kNN top-1 fallback + ``classify_error`` census.

        Returns ``(calls, usage)`` — ``usage`` is the accumulated LLM spend
        across batches (``{"calls", "attempts", "retries", "truncated"}``,
        rollable into the session llm_stats via ``_rollup_llm``), so the
        adjudication tail's cost is never invisible to the A/B gate.
        ``usage["calls"]`` is the BATCH count (one per adjudication call),
        ``usage["attempts"]`` the total adapter-call attempts across
        batches (the total-attempts proxy the session rollup consumes), and
        ``usage["retries"]`` sums BOTH the adapter-level retries (top-level
        ``batch_stats["retries"]``) and the parse-level re-prompts
        (nested ``batch_stats["llm"]["retries"]`` — cycle-3 P2: a
        parse-retry on the adjudication tail must reach llm_stats)."""
        if self.model is None:
            for iid, _item, top in tail:
                stats["assigned_knn"] += 1
                assignments[iid] = {"kind": top[0][0], "margin": top[0][1], "mode": "knn"}
            return 0, {"calls": 0, "attempts": 0, "retries": 0, "truncated": False}
        from tortoise.extractor_v2 import _complete_parsed

        calls = 0
        usage = {"calls": 0, "attempts": 0, "retries": 0, "truncated": False}
        try:
            batch_size = max(1, min(int(self.batch_size), 50))  # plan: 25-50/call
        except (TypeError, ValueError):  # caller passed junk — bounded default
            batch_size = ADJUDICATION_BATCH
        for start in range(0, len(tail), batch_size):
            batch = tail[start : start + batch_size]
            calls += 1
            # Per-batch stats: the LLM spend (attempts/retries/truncated)
            # rides out of _complete_parsed through this dict (previously
            # stats=None made the batched spend invisible to the cost gate).
            batch_stats: dict = {}
            try:
                payload = {}
                for iid, item, top in batch:
                    candidates = [{"kind": k, "similarity": round(s, 4)} for k, s in top]
                    payload[iid] = {
                        "content": str(item.get("text", ""))[:400],
                        "candidates": candidates,
                    }
                system = (
                    "You are the KIND ADJUDICATOR for the Tortoise epistemic "
                    "memory. Each item lists its top candidate kinds with "
                    "similarity scores. Choose the SINGLE best kind per item "
                    "from ITS candidate list — nothing else. Respond with ONE "
                    "JSON object mapping each item key to its chosen kind "
                    "(exact candidate string, case-folded). Never invent a kind."
                )
                user = (
                    "ADJUDICATE:\n"
                    + json.dumps(payload, indent=1)
                    + '\n\nReturn {"item_key": "chosen-kind"} for every item.'
                )
                parsed = _complete_parsed(
                    self.model, system, user, max_tokens=ADJUDICATION_MAX_TOKENS,
                    stats=batch_stats,
                )
            except Exception as e:  # fail-open
                warnings.append(
                    f"adjudication batch failed ({type(e).__name__}: {e}) — kNN top-1 fallback"
                )
                stats["classify_errors"] += 1
                for iid, _item, top in batch:
                    stats["assigned_knn"] += 1
                    assignments[iid] = {"kind": top[0][0], "margin": top[0][1], "mode": "knn"}
                continue
            finally:
                # The spend is accumulated even when the batch failed (the
                # calls were made — the A/B cost gate must see them).
                # Retries sum the adapter-level loop AND the parse-level
                # re-prompts (_complete_parsed records those nested under
                # stats["llm"]["retries"] — cycle-3 P2 undercount).
                usage["attempts"] += batch_stats.get("attempts", 0)
                usage["retries"] += batch_stats.get("retries", 0)
                usage["retries"] += (batch_stats.get("llm") or {}).get("retries", 0)
                usage["truncated"] = usage["truncated"] or bool(
                    batch_stats.get("truncated"))
            if not isinstance(parsed, dict):
                warnings.append("adjudication returned a non-object — kNN top-1 fallback")
                stats["classify_errors"] += 1
                for iid, _item, top in batch:
                    stats["assigned_knn"] += 1
                    assignments[iid] = {"kind": top[0][0], "margin": top[0][1], "mode": "knn"}
                continue
            for iid, _item, top in batch:
                chosen = parsed.get(iid)
                if not isinstance(chosen, str):
                    chosen = ""
                cand_low = {c[0].lower(): c[0] for c in top}
                low = chosen.lower()
                resolved = cand_low.get(low)
                if not resolved:
                    for ck, cn in cand_low.items():
                        if ck.rsplit(":", 1)[-1] == low:
                            resolved = cn
                            break
                if not resolved:
                    warnings.append(
                        f"{iid}: adjudicator picked {chosen!r} — "
                        "not a candidate; kNN top-1 fallback"
                    )
                    stats["closed_vocab_rejects"] += 1
                    stats["assigned_knn"] += 1
                    assignments[iid] = {"kind": top[0][0], "margin": top[0][1], "mode": "knn"}
                    continue
                stats["assigned_llm"] += 1
                chosen_sim = next((s for k, s in top if k == resolved), top[0][1])
                assignments[iid] = {"kind": resolved, "margin": chosen_sim,
                                    "mode": "llm"}
        # usage["calls"] is the BATCH count (aligns with
        # stats["adjudication_calls"]); attempts is the spend proxy.
        usage["calls"] = calls
        return calls, usage

    def _fallback(self, item: dict) -> dict:
        """Fail-open terminal: best core kind for the type (the write path's
        family fallback — never a minted kind, never the sentinel in the
        graph)."""
        fallback = {"entity": "core:other", "event": "core:occurrence", "point": "statement"}
        return {
            "kind": fallback.get(str(item.get("type", "")).lower(), "core:other"),
            "margin": 0.0,
            "mode": "fallback",
        }


# ── D0-3 offline eval (kind_classifier is the authoritative --eval CLI) ─────


def evaluate_bits(gold: list[dict], arm: str = "compact", *, model=None, encoder=None) -> dict:
    """D0-3 bit-level metrics: deterministic precision (kNN/rerank path —
    no LLM in the eval so the numbers are reproducible), top-5 hit,
    adjudication-tail rate, nearMiss demotion count, sentinel rate, and a
    no-pack-stratum row. ``gold`` = validated bits from tools.kind_eval."""
    clf = KindClassifier(model=model, encoder=encoder, llm_tail=False)
    items = [{"id": b["id"], "type": b["type"], "text": b["content"]} for b in gold]
    out = clf.classify_items(items)
    assignments = out["assignments"]
    exact = 0
    top5_hit = 0
    near_miss_demoted = 0
    sentinel = 0
    pack_misses = 0
    pack_total = 0
    for b in gold:
        a = assignments.get(b["id"], {})
        kind = str(a.get("kind", ""))
        gold_kind = str(b["gold_kind"])
        gold_bare = gold_kind.rsplit(":", 1)[-1].lower()
        kind_bare = kind.rsplit(":", 1)[-1].lower()
        if kind.lower() == gold_kind.lower() or kind_bare == gold_bare:
            exact += 1
        if a.get("mode") == "rerank":
            near_miss_demoted += 1
        if kind == UNCLASSIFIED:
            sentinel += 1
        if ":" in gold_kind and not gold_kind.lower().startswith("core:"):
            pack_total += 1
            if kind_bare != gold_bare:
                pack_misses += 1
        # top-5 hit: the gold kind must be among the restricted top-5
        try:
            vecs, _ = clf.encoder.encode([b["content"]])
            top5 = clf.index.nearest(vecs[0], k=5, restrict=clf._restrict(b["type"]))
            if any(
                gold_kind.lower() == t[0].lower() or gold_bare == t[0].rsplit(":", 1)[-1].lower()
                for t in top5
            ):
                top5_hit += 1
        except Exception:
            pass
    n = len(gold) or 1
    return {
        "arm": arm,
        "bits": len(gold),
        "precision": round(exact / n, 4),
        "top5_hit_rate": round(top5_hit / n, 4),
        "adjudication_tail_rate": round(out["stats"]["adjudication_tail"] / n, 4),
        "adjudication_calls": out["stats"]["adjudication_calls"],
        "near_miss_demotions": near_miss_demoted,
        "sentinel_rate": round(sentinel / n, 4),
        "unclassified": out["stats"]["unclassified"],
        "classify_errors": out["stats"]["classify_errors"],
        "embedding_errors": out["stats"]["embedding_errors"],
        "closed_vocab_rejects": out["stats"]["closed_vocab_rejects"],
        "pack_stratum": {"bits": pack_total, "misses": pack_misses},
        "mode_breakdown": {
            k: out["stats"].get(k, 0)
            for k in ("assigned_knn", "assigned_rerank", "assigned_llm", "unclassified")
        },
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Kind classifier: hybrid kNN + LLM-adjudication (issue #1695, Task 4)."
    )
    ap.add_argument(
        "--eval",
        metavar="GOLD.jsonl",
        default=None,
        help="run the D0-3 bit-level eval on the gold set",
    )
    ap.add_argument("--arm", default="compact", help="eval arm label (verbose|compact|flag-on)")
    args = ap.parse_args(argv)
    if not args.eval:
        ap.print_help()
        return 1
    from tools.kind_eval import load_gold, validate_gold_metadata
    from tortoise import extractor_v2 as v2

    gold = load_gold(args.eval)
    vocab = v2.master_kind_forms(v2.build_master_list())
    validate_gold_metadata(gold, vocab)
    print(json.dumps(evaluate_bits(gold, arm=args.arm), indent=2))
    return 0


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
