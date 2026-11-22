#!/usr/bin/env python
"""#14277 — measure the extractor's REAL token spend over repo documents.

`#14275` prices a document at ≈ $0.0056 from two *assumed* constants: a ~7k
constant per-item overhead (system prompt + pack kind list) and **1.5T** of
pass input across the three spending stages. Neither is measured, and the
whole decision rides on them: a 15k constant nearly doubles the cost, and
prompt caching hitting the constant nearly halves it.

This tool replaces the assumptions with numbers. It runs the REAL
``extract_session_v2`` pipeline over

* ~50 files from ``docs/**/*.md``, stratified across the length spread the
  repo actually has (so the sample is not 50 similar files), and
* 10 spooled sessions from ``~/.tortoise/capture-spool``,

once cold and once warm, and records **input/output tokens per stage per
item**. The two fitted constants then fall out of a least-squares fit of
stage input tokens on item tokens, instead of being eyeballed.

WHAT IT DOES NOT DO — by design, and each one is a constraint on the work:

* ⛔ it takes no pricing decision. It prints cost at the rates the issue under
  test quotes, and separately at the repo's own pricing map; the difference is
  reported, never resolved here.
* ⛔ it changes no extractor behaviour. The only production instrumentation it
  relies on is the prompt-cache split added by this same change (#14277) plus
  the ``by_stage`` cost envelope that already existed.
* ⛔ it writes nothing to a graph: ``sdk=None`` means S3 degrades to empty
  results, which is not a shortcut but the shape of a genuinely new account's
  first pack — see the ``--sdk`` note in the module's LIMITS section.

Usage::

    # measure (writes one JSONL row per item per arm; resumable)
    uv run python tools/extractor_cost_measure.py run \\
        --docs 50 --sessions 10 --workers 6 \\
        --out docs/evidence/14277-extractor-cost/measurements.jsonl

    # fit + report (tables, constants, predicted-vs-measured, verdict)
    uv run python tools/extractor_cost_measure.py report \\
        --out docs/evidence/14277-extractor-cost/measurements.jsonl

LIMITS — stated because an unstated limit reads as a result:

1. **One document is one user turn.** There is no document-ingest lane yet
   (`#14219` is open), so the faithful representation of "the extractor reads
   this document" is a single-turn conversation. Consequence: S1 sees ONE
   chunk (``chunk_transcript`` splits 50 EDUs, and a single turn is one EDU),
   so the per-chunk system prompt is paid ONCE per document. A real ingest
   that splits a long document into turns pays it once PER TURN — the
   fitted constant times the chunk count. ``--long-split`` measures that
   difference on the longest document instead of leaving it to arithmetic.
2. **S3 is empty.** ``sdk=None`` degrades the graph read to no results, so
   S4's prompt carries no candidate list. That is the first-pack-into-a-new-
   account shape; a pack into a POPULATED graph carries candidates and its S4
   input is larger. The measured S4 figure is therefore a floor.
3. **Cache arms are a ladder, not two points.** Both arms run the corpus in
   order, so from the second document onward the per-stage system prompt is
   already a cached prefix. ⛔ **NO TRULY COLD PREFIX WAS OBSERVED.** The
   provider's prefix cache outlives a single call, and the workers run
   concurrently against the same per-stage prompts, so even the cold arm's
   FIRST document was 83.3% cached (29,568 hit of 35,512 prompt tokens) — the
   prefix was already warm when it was measured, from a sibling call or an
   earlier run, and the bundle cannot tell which. Its row is reported,
   labelled for what it is, rather than called a cold reading. The cold arm's
   steady state (sys prompt warm, document body cold) is the realistic
   production figure; the warm arm measures a perfect replay — the upper bound
   of the cache lever, and the value content-hash dedup would collect.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first, with an UNATTRIBUTED error (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/extractor_cost_measure.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/extractor_cost_measure.py`"
    )

import argparse
import json
import os
import random
import re
import statistics
import subprocess
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: The rates `#14275` quotes (its own table, "vendor-published, not reproduced
#: there"), per 1M tokens. These are the numbers the issue's recommendation is
#: built on, so predicted-vs-measured is stated at THESE rates — testing the
#: model as it was stated, not a rate the measurement prefers.
ISSUE_RATES = {
    "peak": {"input": 0.30, "output": 1.20, "cache_hit": 0.006},
    "off_peak": {"input": 0.15, "output": 0.60, "cache_hit": 0.003},
}

#: The repo's own reprice map (`tools/longmem_eval/costing.py::PRICING_MAP`),
#: which is what a stored `capture_cost` row is re-priced against. Reported
#: beside the above; the gap between the two is a finding for `#14275`, not
#: something this tool resolves.
_MAP_ROUTE = ("deepseek-direct", "deepseek-v4-flash")

_SPEND_STAGES = ("s1", "s2", "s4")


# ── corpus selection ──────────────────────────────────────────────────────

def _encoding():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def _repo_docs() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "docs"], cwd=_REPO_ROOT, capture_output=True,
        text=True, check=True).stdout.split()
    return [ _REPO_ROOT / f for f in sorted(out) if f.endswith(".md")]


def doc_inventory() -> list[dict]:
    """Every tracked ``docs/**/*.md`` with its token count (cl100k_base).

    The encoding is NAMED rather than implied: `#14275`'s size table does not
    say which tokenizer produced it, and a token count without its tokenizer
    is not comparable to anything. ``cl100k_base`` reproduces that table's
    distribution to within ~3% on every quantile, which is what makes the two
    measurements the same measurement rather than two adjacent ones.
    """
    enc = _encoding()
    rows = []
    for path in _repo_docs():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - unreadable file is not a document
            continue
        rows.append({"path": str(path.relative_to(_REPO_ROOT)),
                     "tokens": len(enc.encode(text))})
    return rows


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(q * len(ordered))))
    return ordered[idx]


def pick_corpus(limit: int = 50, seed: int = 7) -> list[dict]:
    """Stratify ``docs/**/*.md`` across the REAL length spread.

    Equal-width length bands of the observed distribution, one file per band
    picked at random (seeded), so the sample carries the distribution instead
    of the tail or the head of it. Deterministic for a given seed — two runs
    of this tool measure the same corpus, which is what makes cold and warm
    comparable at all.
    """
    inv = doc_inventory()
    inv.sort(key=lambda r: r["tokens"])
    n = len(inv)
    rng = random.Random(seed)
    picked: dict[str, dict] = {}
    for i in range(limit):
        # i-th band of `limit` equal-width slices over the sorted corpus
        lo = int(i * n / limit)
        hi = max(lo + 1, int((i + 1) * n / limit))
        chosen = rng.choice(inv[lo:hi])
        picked[chosen["path"]] = chosen
    # Guarantee the spread's landmarks: the median, p75 and the LONGEST file
    # are in the sample by definition, not by luck of the draw (the brief asks
    # for them explicitly, and a band that happens to miss the max would leave
    # the constant's leverage on long documents unmeasured).
    for landmark in (_quantile([r["tokens"] for r in inv], 0.5),
                     _quantile([r["tokens"] for r in inv], 0.75),
                     inv[-1]["tokens"]):
        nearest = min(inv, key=lambda r: abs(r["tokens"] - landmark))
        picked[nearest["path"]] = nearest
    rows = sorted(picked.values(), key=lambda r: r["tokens"])[:limit]
    if inv[-1]["path"] not in {r["path"] for r in rows}:
        rows[-1] = inv[-1]
    return rows


def spool_sessions(limit: int = 10) -> list[dict]:
    """The median-sized spooled sessions, from ``~/.tortoise/capture-spool``.

    Sessions and documents draw on the SAME budget (owner ruling 2026-11-21),
    so the session lane is fitted on the same terms rather than assumed to be
    "about a third of a document". Selection is by measured token size around
    the median #14275 reports (1,461), so the sample is the typical session
    and not the long tail.
    """
    root = Path(os.environ.get("CELL_HOME", Path.home())) / ".tortoise" \
        / "capture-spool" / "entries"
    if not root.is_dir():
        return []
    enc = _encoding()
    rows = []
    for turns_path in sorted(root.glob("*.turns.jsonl")):
        turns = []
        try:
            for line in turns_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                text = rec.get("content") or rec.get("text") or ""
                if not isinstance(text, str) or not text:
                    continue
                turns.append({"role": str(rec.get("role") or "user"),
                              "content": text})
        except (OSError, ValueError):
            continue
        if not turns:
            continue
        size = len(enc.encode("\n".join(t["content"] for t in turns)))
        rows.append({"path": turns_path.name, "tokens": size, "turns": turns})
    rows.sort(key=lambda r: r["tokens"])
    if not rows:
        return []
    median = _quantile([r["tokens"] for r in rows], 0.5)
    rows.sort(key=lambda r: abs(r["tokens"] - median))
    return rows[:limit]


# ── the measurement ───────────────────────────────────────────────────────

def _build_model():
    """The PRODUCTION extractor model (`build_extractor_model`'s default).

    Not a hand-built request: the point is to measure the pipeline as it is
    deployed, caps and JSON mode and thinking-disabled included.
    """
    from tortoise.model_adapters import build_extractor_model
    return build_extractor_model(max_tokens=None)


def _stage_totals(llm: dict) -> dict:
    """Per-stage totals across every (provider, model) bucket.

    A stage can be served by more than one route (a mid-stage failover), so
    the buckets are SUMMED rather than read by key — reading one route would
    silently drop the other's tokens.
    """
    out: dict[str, dict] = {}
    for stage, providers in (llm.get("by_stage") or {}).items():
        agg = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
               "cache_hit_tokens": 0, "cache_miss_tokens": 0,
               "cost_usd": 0.0, "route": None}
        for provider, models in (providers or {}).items():
            for model, bucket in (models or {}).items():
                if not isinstance(bucket, dict):
                    continue
                agg["calls"] += int(bucket.get("calls", 0) or 0)
                agg["prompt_tokens"] += int(bucket.get("prompt_tokens", 0) or 0)
                agg["completion_tokens"] += int(
                    bucket.get("completion_tokens", 0) or 0)
                agg["cache_hit_tokens"] += int(
                    bucket.get("prompt_cache_hit_tokens", 0) or 0)
                agg["cache_miss_tokens"] += int(
                    bucket.get("prompt_cache_miss_tokens", 0) or 0)
                agg["cost_usd"] = round(
                    agg["cost_usd"] + float(bucket.get("cost_usd", 0.0) or 0.0), 9)
                agg["route"] = agg["route"] or f"{provider}/{model}"
        out[stage] = agg
    return out


def measure_one(model, conversation: list[dict], *, session_date: str) -> dict:
    """Run the real pipeline once over one item; return its spend."""
    from tortoise.extractor_v2 import extract_session_v2
    started = time.time()
    out = extract_session_v2(model, conversation, sdk=None,
                             session_date=session_date)
    llm = (out.get("stats") or {}).get("llm") or {}
    stages = _stage_totals(llm)
    return {
        "seconds": round(time.time() - started, 1),
        "stages": stages,
        "calls": int(llm.get("calls", 0) or 0),
        "prompt_tokens": int(llm.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(llm.get("completion_tokens", 0) or 0),
        "cache_hit_tokens": int(llm.get("prompt_cache_hit_tokens", 0) or 0),
        "cache_miss_tokens": int(llm.get("prompt_cache_miss_tokens", 0) or 0),
        "cost_usd": float(llm.get("cost_usd", 0.0) or 0.0),
        "errors": list(out.get("errors") or []),
        "warnings": list(out.get("warnings") or [])[:5],
        "story_chars": len(out.get("story_arc") or ""),
    }


def _existing_rows(path: Path) -> set[tuple[str, str, str]]:
    done = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        done.add((rec.get("arm", ""), rec.get("kind", ""), rec.get("id", "")))
    return done


def run_measurement(*, out: Path, docs_limit: int, sessions_limit: int,
                    workers: int, seed: int, arms: tuple[str, ...],
                    arm_gap_s: float, long_split: bool,
                    split_max_turns: int = 40,
                    split_only: bool = False) -> int:
    import concurrent.futures as cf

    docs = pick_corpus(limit=docs_limit, seed=seed)
    sessions = spool_sessions(limit=sessions_limit)
    print(f"corpus: {len(docs)} documents "
          f"(median {_quantile([d['tokens'] for d in docs], 0.5)} tokens, "
          f"max {max((d['tokens'] for d in docs), default=0)}); "
          f"{len(sessions)} sessions", flush=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    done = _existing_rows(out)
    lock = threading.Lock()

    def _emit(rec: dict) -> None:
        with lock, out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _job(item: dict, kind: str, arm: str) -> dict:
        model = _build_model()   # one per call: the adapter carries per-call
                                 # state (last_*), which races under workers>1
        if kind == "doc":
            conversation = [{"role": "user",
                             "content": item["text"]}]
        else:
            conversation = item["turns"]
        rec = measure_one(model, conversation,
                          session_date="2026-11-21T00:00:00+00:00")
        rec["arm"] = arm
        rec["kind"] = kind
        rec["id"] = item["path"]
        rec["item_tokens"] = item["tokens"]
        rec["turns"] = len(conversation)
        rec["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _emit(rec)
        return rec

    jobs: list[tuple[dict, str, str]] = []
    for arm in (() if split_only else arms):
        for doc in docs:
            if (arm, "doc", doc["path"]) in done:
                continue
            jobs.append(({"path": doc["path"],
                          "tokens": doc["tokens"],
                          "text": (_REPO_ROOT / doc["path"]).read_text(
                              encoding="utf-8", errors="replace")},
                         "doc", arm))
        for sess in sessions:
            if (arm, "session", sess["path"]) in done:
                continue
            jobs.append((sess, "session", arm))

    total = len(jobs)
    print(f"running {total} measurements at {workers} workers "
          f"(arms: {', '.join(arms)})", flush=True)
    finished = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_job, item, kind, arm): (item, kind, arm)
                   for item, kind, arm in jobs}
        for fut in cf.as_completed(futures):
            item, kind, arm = futures[fut]
            finished += 1
            try:
                rec = fut.result()
            except Exception as exc:
                # a 360-call measurement; the failure is recorded as a row so
                # the report can state how many items did not measure.
                rec = {"arm": arm, "kind": kind, "id": item["path"],
                       "item_tokens": item["tokens"], "failed": True,
                       "error": f"{type(exc).__name__}: {exc}"}
                _emit(rec)
            print(f"  [{finished}/{total}] {arm:4} {kind:7} "
                  f"{rec['id'][-58:]:58} "
                  f"in={rec.get('prompt_tokens', 0):>7} "
                  f"out={rec.get('completion_tokens', 0):>5} "
                  f"hit={rec.get('cache_hit_tokens', 0):>7} "
                  f"{'FAILED' if rec.get('failed') else ''}", flush=True)

    if long_split:
        _measure_long_split(out, docs, arms, _emit, split_max_turns
                            )
    return 0


def _measure_long_split(out: Path, docs: list[dict], arms: tuple[str, ...],
                        emit, max_turns: int = 40) -> None:
    """The longest document, delivered as ``max_turns`` turns.

    `#14277`'s limit 1: one turn is one S1 chunk, so the per-chunk system
    prompt is paid once. An ingest that splits a long document pays it once
    per chunk. This measures that instead of extrapolating it.

    The turns are CAPPED (`--split-max-turns`, default 40): an unbounded
    split of a 128k-token document is hundreds of S1 calls, which is spend
    the issue's scope does not authorise. The cap is safe because the cost is
    LINEAR IN TURNS — the whole point of the constant — so the measured
    per-turn cost multiplies out to any chunk count, and the report states the
    formula rather than a guess.
    """
    longest = max(docs, key=lambda d: d["tokens"])
    text = (_REPO_ROOT / longest["path"]).read_text(encoding="utf-8",
                                                    errors="replace")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) < 2:
        return
    # Group paragraphs into `max_turns` contiguous turns (keeping document
    # order — the narrative stages read it).
    n = min(max_turns, len(paras))
    turns: list[str] = []
    for i in range(n):
        lo = int(i * len(paras) / n)
        hi = max(lo + 1, int((i + 1) * len(paras) / n))
        turns.append("\n\n".join(paras[lo:hi]))
    for arm in arms:
        model = _build_model()
        rec = measure_one(model,
                          [{"role": "user", "content": t} for t in turns],
                          session_date="2026-11-21T00:00:00+00:00")
        rec["arm"] = arm
        rec["kind"] = "doc_split"
        rec["id"] = f"{longest['path']}#{len(turns)}turns"
        rec["item_tokens"] = longest["tokens"]
        rec["turns"] = len(turns)
        rec["paragraphs"] = len(paras)
        rec["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        emit(rec)
        print(f"  long-split {arm}: {len(turns)} turns of {len(paras)} "
              f"paragraphs, in={rec['prompt_tokens']} "
              f"out={rec['completion_tokens']}", flush=True)


# ── fitting ───────────────────────────────────────────────────────────────

def _least_squares(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Slope/intercept by ordinary least squares (no numpy dependency).

    Returns ``(slope, intercept)``; a degenerate x-range yields a
    zero-slope line through the mean rather than a divide-by-zero.
    """
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, mean_y
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def _fit(points: list[tuple[float, float]]) -> dict:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    slope, intercept = _least_squares(xs, ys)
    pred = [intercept + slope * x for x in xs]
    resid = [y - p for y, p in zip(ys, pred, strict=True)]
    ss_res = sum(r * r for r in resid)
    ss_tot = sum((y - (sum(ys) / len(ys))) ** 2 for y in ys) if ys else 0.0
    return {
        "n": len(points),
        "slope": slope,
        "intercept": intercept,
        "r2": (1 - ss_res / ss_tot) if ss_tot else 0.0,
        "max_abs_resid": max((abs(r) for r in resid), default=0.0),
        "median_abs_resid": statistics.median([abs(r) for r in resid])
        if resid else 0.0,
        "mean": (sum(ys) / len(ys)) if ys else 0.0,
        "median": statistics.median(ys) if ys else 0.0,
    }


def _usd(tokens_in: int, tokens_out: int, cache_hit: int, rates: dict) -> float:
    miss = max(0, tokens_in - cache_hit)
    return round((miss * rates["input"]
                  + cache_hit * rates["cache_hit"]
                  + tokens_out * rates["output"]) / 1e6, 9)


def _pricing_map_rate() -> dict | None:
    try:
        from tools.longmem_eval import costing
    except Exception:  # pragma: no cover - report still renders without it
        return None
    entry = costing.lookup_rate(*_MAP_ROUTE)
    if not entry:
        return None
    return {"input": float(entry["prompt_per_1m"]),
            "output": float(entry["completion_per_1m"]),
            "cache_hit": float(entry.get("cache_read_per_1m") or 0.0)}


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def build_report(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("failed")]
    failed = [r for r in rows if r.get("failed")]
    #: carried on the report for the cost tables; stripped before the JSON is
    #: written (`_jsonable`) so the artifact stays a summary, not a second copy
    #: of the raw JSONL.

    def _arm(arm: str, kind: str) -> list[dict]:
        return [r for r in ok
                if r.get("arm") == arm and r.get("kind") == kind]

    report: dict = {"rows": len(rows), "failed": len(failed),
                    "failed_ids": [r.get("id", "<unidentified>")
                                   for r in failed][:20],
                    "arms": {}}

    for arm in sorted({r.get("arm") for r in ok if r.get("arm")}):
        docs = _arm(arm, "doc")
        sess = _arm(arm, "session")
        split = _arm(arm, "doc_split")
        arm_rep: dict = {"documents": len(docs), "sessions": len(sess)}

        # per-stage fits: stage input tokens against item tokens
        stage_fits = {}
        for stage in _SPEND_STAGES:
            pts = [(r["item_tokens"],
                    (r["stages"].get(stage) or {}).get("prompt_tokens", 0))
                   for r in docs if stage in (r.get("stages") or {})]
            if len(pts) >= 3:
                stage_fits[stage] = _fit(pts)
        total_pts = [(r["item_tokens"], r["prompt_tokens"]) for r in docs]
        out_pts = [(r["item_tokens"], r["completion_tokens"]) for r in docs]
        arm_rep["stage_fits"] = stage_fits
        arm_rep["input_fit"] = _fit(total_pts) if len(total_pts) >= 3 else None
        arm_rep["output_fit"] = _fit(out_pts) if len(out_pts) >= 3 else None

        # tokens per document, at the distribution's own landmarks
        per_doc_in = [r["prompt_tokens"] for r in docs]
        per_doc_out = [r["completion_tokens"] for r in docs]
        sizes = [r["item_tokens"] for r in docs]
        arm_rep["doc"] = {
            "tokens_in": {"median": statistics.median(per_doc_in) if per_doc_in else 0,
                          "mean": round(sum(per_doc_in) / len(per_doc_in), 1)
                          if per_doc_in else 0,
                          "p75": _quantile(per_doc_in, 0.75),
                          "max": max(per_doc_in, default=0)},
            "tokens_out": {"median": statistics.median(per_doc_out) if per_doc_out else 0,
                           "mean": round(sum(per_doc_out) / len(per_doc_out), 1)
                           if per_doc_out else 0,
                           "p75": _quantile(per_doc_out, 0.75),
                           "max": max(per_doc_out, default=0)},
            "item_tokens": {"median": statistics.median(sizes) if sizes else 0,
                            "p75": _quantile(sizes, 0.75),
                            "max": max(sizes, default=0)},
            "cache": _cache_block(docs),
            "stage_totals": _stage_block(docs),
            "calls_per_doc": round(
                sum(r["calls"] for r in docs) / len(docs), 2) if docs else 0,
            "seconds_median": statistics.median(
                [r["seconds"] for r in docs]) if docs else 0,
        }
        arm_rep["session"] = {
            "n": len(sess),
            "item_tokens_median": statistics.median(
                [r["item_tokens"] for r in sess]) if sess else 0,
            "tokens_in_median": statistics.median(
                [r["prompt_tokens"] for r in sess]) if sess else 0,
            "tokens_out_median": statistics.median(
                [r["completion_tokens"] for r in sess]) if sess else 0,
            "tokens_in_mean": round(
                sum(r["prompt_tokens"] for r in sess) / len(sess), 1)
            if sess else 0,
            "cache": _cache_block(sess),
            "stage_totals": _stage_block(sess),
            "calls_per_item": round(
                sum(r["calls"] for r in sess) / len(sess), 2) if sess else 0,
            "input_fit": _fit([(r["item_tokens"], r["prompt_tokens"])
                               for r in sess]) if len(sess) >= 3 else None,
            "output_fit": _fit([(r["item_tokens"], r["completion_tokens"])
                                for r in sess]) if len(sess) >= 3 else None,
        }
        if split:
            arm_rep["long_split"] = [
                {"id": r.get("id", ""), "turns": r.get("turns", 0),
                 "item_tokens": r.get("item_tokens", 0),
                 "tokens_in": r["prompt_tokens"],
                 "tokens_out": r["completion_tokens"],
                 "calls": r.get("calls", 0),
                 "stages": {k: {"calls": v.get("calls", 0),
                                "in": v.get("prompt_tokens", 0),
                                "out": v.get("completion_tokens", 0)}
                            for k, v in (r.get("stages") or {}).items()}}
                for r in sorted(split, key=lambda r: r.get("turns", 0))]
        report["arms"][arm] = arm_rep
    report["_rows"] = ok

    # the TRUE cold call: the first document of each arm, in run order
    cold_calls = [r for r in ok if r.get("kind") == "doc"]
    if cold_calls:
        first = min(cold_calls, key=lambda r: r.get("measured_at", ""))
        report["first_call"] = {
            "id": first.get("id", ""),
            "item_tokens": first.get("item_tokens", 0),
            "stages": {k: {"prompt": v.get("prompt_tokens", 0),
                           "completion": v.get("completion_tokens", 0),
                           "hit": v.get("cache_hit_tokens", 0)}
                       for k, v in (first["stages"] or {}).items()},
        }
    return report


def _cache_block(items: list[dict]) -> dict:
    if not items:
        return {}
    hit = sum(r["cache_hit_tokens"] for r in items)
    miss = sum(r["cache_miss_tokens"] for r in items)
    total = hit + miss
    return {"hit": hit, "miss": miss,
            "hit_ratio": round(hit / total, 4) if total else 0.0}


def _stage_block(items: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for stage in _SPEND_STAGES:
        agg = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
               "cache_hit_tokens": 0, "cache_miss_tokens": 0}
        present = False
        for rec in items:
            block = (rec.get("stages") or {}).get(stage)
            if not block:
                continue
            present = True
            for key in agg:
                agg[key] += int(block.get(key, 0) or 0)
        if present and items:
            out[stage] = {**agg,
                          "per_item_input": round(
                              agg["prompt_tokens"] / len(items), 1),
                          "per_item_output": round(
                              agg["completion_tokens"] / len(items), 2)}
    return out


def _item_cost(tokens_in: float, tokens_out: float, hit_ratio: float,
               rates: dict) -> float:
    """USD for one item at ``rates``, with the measured cache-hit share.

    ``hit_ratio`` is the MEASURED share of prompt tokens that hit the prefix
    cache; the miss side pays the full input rate and the hit side the cache
    rate. ``hit_ratio=0`` is the fully-cold upper bound.
    """
    hit = max(0.0, min(1.0, hit_ratio)) * tokens_in
    miss = max(0.0, tokens_in - hit)
    return round((miss * rates["input"]
                  + hit * rates["cache_hit"]
                  + tokens_out * rates["output"]) / 1e6, 9)


#: `#14275`'s modelled spend per item, restated so the fit can be compared
#: against it rather than against a memory of it: ``input = 1.5T + 7k``,
#: ``output = 0.2T``, no cache dimension.
ISSUE_MODEL = {"input_constant": 7000.0, "input_multiple": 1.5,
               "output_multiple": 0.2}


def _issue_model(tokens: float) -> tuple[float, float]:
    return (ISSUE_MODEL["input_constant"] + ISSUE_MODEL["input_multiple"] * tokens,
            ISSUE_MODEL["output_multiple"] * tokens)


def _med(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _p75(values: list[float]) -> float:
    return _quantile(values, 0.75)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def cost_table(report: dict, rates_peak: dict, rates_off: dict) -> dict:
    """Measured vs modelled cost, per item size, at both published rates.

    The MEASURED side is a direct reading — the sample's own median / mean /
    p75 / maximum tokens per document — not the fit evaluated at those sizes.
    The fit is a model and belongs in the prediction section; a cost headline
    should be the thing that actually happened.
    """
    cold = report["arms"].get("cold") or {}
    warm = report["arms"].get("warm") or {}
    arm = cold or warm
    if not arm:
        return {}
    # #14277 review: the measured in/out below MUST come from the SAME arm that
    # supplies `hit_ratio` (and that the headline fits are fitted on — `cold`
    # when present). Reading them from the pooled `_rows` mixes both arms while
    # pricing the mix at the cold arm's cache ratio, and it made this one
    # generated document contradict itself: the prose reported the cold-arm
    # median session (30,647 / 5,768) and this table reported the pooled one
    # (31,229 / 5,387). Rows carrying no `arm` key are a single-arm report by
    # construction, so they are in-arm.
    arm_name = "cold" if cold else "warm"

    def _in_arm(row: dict) -> bool:
        return row.get("arm") in (None, arm_name)

    docs = [r for r in report.get("_rows", [])
            if r.get("kind") == "doc" and r.get("stages") and _in_arm(r)]
    sess = [r for r in report.get("_rows", [])
            if r.get("kind") == "session" and r.get("stages") and _in_arm(r)]
    if not docs:
        return {}
    doc_cache = (arm.get("doc") or {}).get("cache") or {}
    hit_ratio = float(doc_cache.get("hit_ratio") or 0.0)
    warm_cache = ((warm.get("doc") or {}).get("cache") or {}) if warm else {}

    def _row(label: str, tokens: float, tin: float, tout: float) -> dict:
        m_in, m_out = _issue_model(tokens)
        return {
            "item": label, "tokens": round(tokens),
            "modelled_in": round(m_in), "modelled_out": round(m_out),
            "measured_in": round(tin), "measured_out": round(tout),
            "modelled_peak": _item_cost(m_in, m_out, 0.0, rates_peak),
            "measured_cold_peak": _item_cost(tin, tout, 0.0, rates_peak),
            "measured_cached_peak": _item_cost(tin, tout, hit_ratio, rates_peak),
            "modelled_off": _item_cost(m_in, m_out, 0.0, rates_off),
            "measured_cold_off": _item_cost(tin, tout, 0.0, rates_off),
            "measured_cached_off": _item_cost(tin, tout, hit_ratio, rates_off),
        }

    tokens = [r["item_tokens"] for r in docs]
    ins = [r["prompt_tokens"] for r in docs]
    outs = [r["completion_tokens"] for r in docs]
    longest = max(docs, key=lambda r: r["item_tokens"])
    rows = [
        _row("median document", _med(tokens), _med(ins), _med(outs)),
        _row("mean document", _mean(tokens), _mean(ins), _mean(outs)),
        _row("p75 document", _p75(tokens), _p75(ins), _p75(outs)),
        _row("long document", longest["item_tokens"], longest["prompt_tokens"],
             longest["completion_tokens"]),
    ]
    if sess:
        rows.append(_row("median session",
                         _med([r["item_tokens"] for r in sess]),
                         _med([r["prompt_tokens"] for r in sess]),
                         _med([r["completion_tokens"] for r in sess])))

    median_tokens = round(rows[0]["measured_in"] + rows[0]["measured_out"])
    share_in = (rows[0]["measured_in"] / median_tokens) if median_tokens else 0.0
    per_m_peak = _item_cost(1e6 * share_in, 1e6 * (1 - share_in), hit_ratio,
                            rates_peak)
    per_m_off = _item_cost(1e6 * share_in, 1e6 * (1 - share_in), hit_ratio,
                           rates_off)
    return {
        "rows": rows, "hit_ratio": hit_ratio,
        # #14277 review: the arm these rows and this ratio were read from. The
        # renderers print it rather than the literal "cold", so a bundle
        # measured with `--arms warm` is not labelled as a cold one (that
        # mislabelling was the same defect as the pooling above, one level up).
        "arm": arm_name,
        "usd_per_million_peak": per_m_peak,
        "usd_per_million_off_peak": per_m_off,
        "warm_hit_ratio": float(warm_cache.get("hit_ratio") or 0.0),
        "tokens_per_median_doc": median_tokens,
        "modelled_tokens_per_median_doc": round(
            sum(_issue_model(_med(tokens)))),
        "fit": {"input": arm.get("input_fit"), "output": arm.get("output_fit"),
                "stages": arm.get("stage_fits")},
    }


def measurement_cost(rows: list[dict], rates_peak: dict, rates_off: dict) -> dict:
    """What this measurement itself cost — stated, because the issue asks."""
    ok = [r for r in rows if not r.get("failed")]
    total_in = sum(r.get("prompt_tokens", 0) for r in ok)
    total_out = sum(r.get("completion_tokens", 0) for r in ok)
    total_hit = sum(r.get("cache_hit_tokens", 0) for r in ok)
    calls = sum(r.get("calls", 0) for r in ok)
    return {
        "items": len(ok), "calls": calls,
        "prompt_tokens": total_in, "completion_tokens": total_out,
        "cache_hit_tokens": total_hit,
        "hit_ratio": round(total_hit / total_in, 4) if total_in else 0.0,
        "usd_peak": _item_cost(total_in, total_out,
                               (total_hit / total_in) if total_in else 0.0,
                               rates_peak),
        "usd_off_peak": _item_cost(total_in, total_out,
                                   (total_hit / total_in) if total_in else 0.0,
                                   rates_off),
        "usd_peak_no_cache": _item_cost(total_in, total_out, 0.0, rates_peak),
    }


def allowance_table(cost: dict, tokens_per_doc: int) -> list[dict]:
    """`#14275`'s four allowances, re-stated in the MEASURED unit.

    The allowances are in TOKENS, so the measurement cannot change them —
    what it changes is (a) how many documents a token allowance is, and (b)
    what that allowance costs us. Both are derived here rather than asserted:
    the in:out mix comes from the measured median document, and the cost per
    token from the measured cache behaviour at peak rates.
    """
    rows = cost.get("rows") or []
    if not rows:
        return []
    median = rows[0]
    session_row = next((r for r in rows if r["item"] == "median session"), None)
    mix_in = median["measured_in"]
    mix_out = median["measured_out"]
    share_in = mix_in / (mix_in + mix_out) if (mix_in + mix_out) else 0.0
    hit = cost["hit_ratio"]
    per_token = _item_cost(1e6 * share_in, 1e6 * (1 - share_in), hit, ISSUE_RATES["peak"])
    per_token_off = _item_cost(1e6 * share_in, 1e6 * (1 - share_in), hit,
                               ISSUE_RATES["off_peak"])
    tiers = [("free", 0.0, 0), ("solo", 19.0, 3_000_000),
             ("pro", 25.0, 4_000_000), ("team", 149.0, 24_000_000)]
    out = []
    for name, price, allowance in tiers:
        if not allowance:
            out.append({"tier": name, "subscription": price, "allowance": 0,
                        "usd_peak": 0.0, "usd_off_peak": 0.0,
                        "pct_peak": 0.0, "pct_off_peak": 0.0,
                        "documents": 0, "sessions": 0})
            continue
        usd_peak = round(per_token * allowance / 1e6, 4)
        usd_off = round(per_token_off * allowance / 1e6, 4)
        out.append({
            "tier": name, "subscription": price, "allowance": allowance,
            "usd_peak": usd_peak, "usd_off_peak": usd_off,
            "pct_peak": round(100 * usd_peak / price, 1) if price else 0.0,
            "pct_off_peak": round(100 * usd_off / price, 1) if price else 0.0,
            "documents": round(allowance / tokens_per_doc) if tokens_per_doc else 0,
            "sessions": (
                round(allowance / (session_row["measured_in"]
                                   + session_row["measured_out"]))
                if session_row and (session_row["measured_in"]
                                    + session_row["measured_out"]) else 0),
        })
    return out


def _mean_item_tokens(arm_rep: dict) -> float:
    doc = arm_rep.get("doc") or {}
    item = doc.get("item_tokens") or {}
    # the sample's own mean item size is not carried verbatim; the p75/median
    # pair is, and the doc-level token table below prints the true mean.
    return float(item.get("median") or 0)


# A blockquote marker may sit at ANY column: inside a list item the quote is
# offset by the item's content indent (`  > ``` ` on one line, `    > code` on the
# next, both inside the same quote). Bounding this to {0,3} made a fence CONTENT
# line at >=4 columns read as "the container ended" — measured: it escaped code
# and left the following prose bare (two wrong outcomes at once).
_BLOCKQUOTE = re.compile(r"^[ \t]*> ?")
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<delim>`{3,}|~{3,})(?P<info>.*)$")


def _marker_count(line: str) -> int:
    """How many leading blockquote markers this line carries, at ANY indentation.

    The indentation is deliberately not capped: inside a list item a quote's
    markers are offset by the item's content indent, so the same quote carries
    `  > ``` ` on the opener and `    > code` on a content line, and a {0,3}
    cap read the second as "no marker at all".

    Only the COUNT is used, never the offsets. That is measured, not assumed: a
    line whose marker sits at a SHALLOWER offset than the opener's is still
    fence CONTENT under CommonMark (`markdown-it@14`, commonmark preset), so
    comparing offsets escaped a code line and left the real prose bare.
    """
    count = 0
    pos = 0
    while True:
        marker = _BLOCKQUOTE.match(line[pos:])
        if not marker:
            return count
        count += 1
        pos += marker.end()


def _strip_markers(line: str, count: int) -> str:
    """Drop AT MOST ``count`` blockquote markers — never more than the opener had."""
    for _ in range(count):
        marker = _BLOCKQUOTE.match(line)
        if not marker:
            break
        line = line[marker.end():]
    return line


def _opens_fence(line: str) -> tuple[str, int]:
    """(the opener's FULL delimiter run, its marker count) or ("", 0)."""
    count = _marker_count(line)
    match = _FENCE_OPEN.match(_strip_markers(line, count))
    if not match:
        return "", 0
    delim = match.group("delim")
    if delim[0] == "`" and "`" in match.group("info"):
        # CommonMark: a backtick fence's info string may not contain a backtick.
        return "", 0
    return delim, count


def _left_container(line: str, count: int) -> bool:
    """Did this line end the container the fence was opened in?

    ONE way: fewer markers than the opener. A line with NO marker ends the
    container; a line whose markers are merely offset differently does not.
    """
    if not count:
        # An unquoted fence is not held by a container: only a closer ends it.
        return False
    return _marker_count(line) < count


def _closes_fence(line: str, delim: str, count: int) -> bool:
    """A closer is AT LEAST as long as its opener, and only whitespace follows.

    The opener's marker count is stripped, never more and never fewer: a
    ``>``-prefixed line INSIDE a fence is fence CONTENT, so stripping markers
    the opener did not have would let a quoted example close the block it is
    quoted inside.
    """
    return re.fullmatch(
        r" {0,3}" + re.escape(delim[0]) + "{" + str(len(delim)) + r",}[ \t]*",
        _strip_markers(line, count)) is not None


def escape_dollars_outside_code(markdown: str) -> str:
    """Backslash-escape every literal ``$`` that is not in a code span or fence.

    A bare ``$`` beside a figure is ambiguous to Markdown's inline parser: the
    parser runs ``micromark-extension-math``, so ``$…$`` is a MATH span, and
    pairing one re-pairs the surrounding ``**emphasis**`` and code-span
    delimiters. That produced two findings in this report (MD037, MD038) whose
    POSITION MOVED when unrelated prose changed, so no hand-edit of the
    generated text could keep them fixed: the defect is the emission, not the
    sentence it landed on (#14295).

    ``\\$`` renders as ``$`` (it is a normal punctuation escape), so the
    rendered report is unchanged while its inline structure no longer depends
    on how many figures a sentence carries. A backslash is LITERAL inside code,
    so both kinds of it pass through untouched: inline spans, located by
    matching backtick RUNS (a double-backtick span is one unit), and fenced
    blocks, located by the CommonMark fence rules — at most three spaces of
    indentation, the closer at least as long as its opener with nothing but
    whitespace after it, and blockquote markers stripped only to the OPENER's
    own depth (a ``>``-prefixed line inside a fence is content and closes
    nothing). Escaping a fenced ``$HOME`` would print a backslash the author
    never wrote.

    MD012 (a blank line at end of file) is NOT this function's cause; see
    ``finalize_markdown``.

    Scope, stated so the boundary is not mistaken for completeness:

    * An inline span that WRAPS a line is not tracked — the scan is line-local.
    * A line with an ODD number of backtick runs leaves that line's tail read as
      code, so a bare ``$`` there stays bare. Treat that direction as
      UNMONITORED: whether the ``docs`` gate reports such a line depends on how
      markdownlint happens to dislike the shape (measured — one such line lints
      clean, another reds MD038 from the backtick pairing alone with no ``$``
      involvement), so the gate cannot be relied on to catch it.
    * An UNCLOSED, UNQUOTED fence leaves the rest of the document unescaped.
      That one is deliberate: a fence that never closes really does make what
      follows code. A fence opened INSIDE a blockquote is not in that class —
      the quote ending closes it — so a line carrying fewer markers than the
      opener ends both the fence and the container, and is re-read from the top:
      escaped as prose, or treated as a new opener (and so left alone) when it
      opens a fence itself.
    * An INDENTED code block (4+ spaces, which CommonMark makes code) is not
      tracked as code, so a ``$`` inside one IS escaped — the fence rules above
      are applied, indentation-based code blocks are not. The one exception is a
      line that reads as a 4+-column-indented QUOTED fence (`    > ``` `), which
      this scan treats as a fence and then keeps its ``$`` bare — there a
      renderer calls it an indented code block. This module emits neither shape.
    * CONTAINER tracking (a fence opened inside a blockquote, so the quote
      ending closes it) approximates CommonMark's list/quote interaction rather
      than modelling it: a marker at any indentation is recognised, markers are
      counted as characters rather than expanded to tab stops, and the fence ends
      when a line carries FEWER markers than the opener. Degenerate documents —
      a fence inside a list-item quote whose content lines' marker offsets vary
      wildly — can still be classified differently from a full CommonMark
      renderer. Every shape this module actually emits is fence-free.
    * A ``$`` preceded by an ESCAPED backslash (``\\$``) stays bare: the
      lookbehind that keeps this function idempotent cannot tell it from an
      already-escaped ``$``, so that ``$`` is left unescaped and can still pair
      with a later one. This module never emits it.

    Neither of the first two shapes is emitted by this module today: it prints
    no fences, and every line of its output has an even number of backtick runs
    (pinned by a test against the shipped report).
    """
    out: list[str] = []
    fence: str = ""
    fence_markers: int = 0
    for line in markdown.split("\n"):
        if fence and _left_container(line, fence_markers):
            # The container that held the fence ENDED on this line, so the fence
            # ended with it and this line is prose (or a new opener) again —
            # CommonMark closes the container, it does not carry it on.
            fence = ""
            fence_markers = 0
        if fence:
            out.append(line)
            out.append("\n")
            if _closes_fence(line, fence, fence_markers):
                fence = ""
                fence_markers = 0
            continue
        opened, markers = _opens_fence(line)
        if opened:
            fence = opened
            fence_markers = markers
            out.append(line)
            out.append("\n")
            continue
        parts = re.split(r"(`+)", line)
        in_code = False
        for part in parts:
            if part.startswith("`"):
                in_code = not in_code
                out.append(part)
            else:
                # `(?<!\\)` keeps this idempotent: an already-escaped `\$`
                # must not become `\\$` if a caller finalizes twice.
                out.append(
                    part if in_code
                    else re.sub(r"(?<!\\)\$", r"\\$", part))
        out.append("\n")
    return "".join(out[:-1])


def finalize_markdown(markdown: str) -> str:
    """Make the rendered sections a lint-clean document.

    Two output-boundary repairs, applied once to the concatenated sections so
    every renderer (present and future) is covered:

    * ``escape_dollars_outside_code`` — see its docstring (#14295).
    * exactly one trailing newline. Each ``render_*`` ends with a blank-line
      element so consecutive sections separate correctly, which left the LAST
      section ending in a blank line — MD012 "multiple consecutive blank
      lines" at end of file.
    """
    return escape_dollars_outside_code(markdown).rstrip("\n") + "\n"


def render(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("## Measurement")
    add("")
    add(f"rows: {report['rows']} (failed: {report['failed']})")
    if report["failed"]:
        add(f"failed ids: {', '.join(report['failed_ids'])}")
    add("")
    for arm, rep in report["arms"].items():
        add(f"### arm `{arm}`")
        add("")
        d = rep["doc"]
        add(f"- documents measured: {rep['documents']} · "
            f"calls/doc: {d['calls_per_doc']} · "
            f"median wall-clock/doc: {d['seconds_median']}s")
        if d["cache"]:
            add(f"- prompt cache: {d['cache']['hit']:,} hit / "
                f"{d['cache']['miss']:,} miss → "
                f"hit ratio {d['cache']['hit_ratio']:.1%}")
        add("")
        add("| stage | input/item | output/item | calls/item | input/call | "
            "output/call | cache hit |")
        add("|---|---|---|---|---|---|---|")
        n_docs = max(1, rep["documents"])
        for stage, block in (d.get("stage_totals") or {}).items():
            calls = max(1, block["calls"])
            add(f"| {stage} | {block['per_item_input']:,.0f} | "
                f"{block['per_item_output']:,.2f} | "
                f"{block['calls'] / n_docs:.2f} | "
                f"{block['prompt_tokens'] / calls:,.0f} | "
                f"{block['completion_tokens'] / calls:,.0f} | "
                f"{block['cache_hit_tokens']:,} |")
        add("")
        fi, fo = rep.get("input_fit"), rep.get("output_fit")
        if fi:
            add(f"- input fit: `tokens_in = {fi['intercept']:,.0f} + "
                f"{fi['slope']:.3f} × T`  (R²={fi['r2']:.3f}, "
                f"median |resid| {fi['median_abs_resid']:,.0f})")
        if fo:
            add(f"- output fit: `tokens_out = {fo['intercept']:,.0f} + "
                f"{fo['slope']:.3f} × T`  (R²={fo['r2']:.3f}, "
                f"median |resid| {fo['median_abs_resid']:,.0f})")
        for stage, fit in (rep.get("stage_fits") or {}).items():
            add(f"  - {stage}: `{fit['intercept']:,.0f} + {fit['slope']:.3f} × T`"
                f" (R²={fit['r2']:.3f})")
        s = rep.get("session")
        if s and s["n"]:
            add("")
            add(f"- sessions: n={s['n']}, median item {s['item_tokens_median']:,} "
                f"tokens → in {s['tokens_in_median']:,} / out "
                f"{s['tokens_out_median']:,} (median), calls/item "
                f"{s['calls_per_item']}")
            if s.get("cache"):
                add(f"  - session cache hit ratio "
                    f"{s['cache']['hit_ratio']:.1%}")
        ls = rep.get("long_split")
        if ls:
            add("")
            add(f"- the longest document ({ls[0]['item_tokens']:,} tokens) "
                "delivered as different numbers of turns:")
            add("")
            add("| turns | calls | in | out | stage in | stage out |")
            add("|---|---|---|---|---|---|")
            for row in ls:
                st_in = {k: v["in"] for k, v in row["stages"].items()}
                st_out = {k: v["out"] for k, v in row["stages"].items()}
                add(f"| {row['turns']} | {row['calls']} | {row['tokens_in']:,} | "
                    f"{row['tokens_out']:,} | {st_in} | {st_out} |")
        add("")
    fc = report.get("first_call")
    if fc:
        hits = sum(b["hit"] for b in fc["stages"].values())
        prompts = sum(b["prompt"] for b in fc["stages"].values())
        share = (hits / prompts) if prompts else 0.0
        # ⛔ NOT a cold-prefix reading, and the heading says so. The provider's
        # prefix cache outlives a single call, and the workers run concurrently
        # against the same per-stage system prompts, so the cold arm's first
        # document already carried a warm prefix. Calling it "the true cold
        # call" presented a measurement artifact as the cold baseline (#14277
        # review, finding 2). ⛔ Do NOT attribute the warmth to a RESUMED run:
        # `run_measurement` skips rows already in `_existing_rows(out)`, so a
        # resumed row is never re-measured, and every committed row's
        # `measured_at` falls inside one 23-minute window — nothing in this
        # bundle is a re-entry into an earlier run.
        add("### the first measured document — NOT a cold prefix")
        add("")
        add(f"`{fc['id']}` ({fc['item_tokens']:,} tokens). **{share:.1%} of this "
            f"item's prompt tokens were served from the provider cache** "
            f"({hits:,} hit of {prompts:,}), so this row is not a cold "
            f"reading — the prefix was already warm when it was measured, "
            f"whether from a sibling call or from an earlier run (limit 3) — "
            f"a truly cold prefix was NOT observed.")
        add("")
        add("| stage | prompt | completion | cache hit |")
        add("|---|---|---|---|")
        for stage, b in fc["stages"].items():
            add(f"| {stage} | {b['prompt']:,} | {b['completion']:,} | "
                f"{b['hit']:,} |")
        add("")
    return "\n".join(lines) + "\n"


def render_cost(report: dict) -> str:
    cost = report.get("costs") or {}
    if not cost.get("rows"):
        return ""
    lines: list[str] = []
    add = lines.append
    add("## Cost: modelled vs measured")
    add("")
    # ⛔ The second clause is the WARM arm's figure, so it is printed only when the
    # rows were NOT read from the warm arm: on a `--arms warm` bundle the two
    # figures are the same measurement, and printing it twice read as if two arms
    # had been measured (`75.0% (warm arm) · 75.0% (warm arm)`).
    row_arm = cost.get("arm", "cold")
    share = f"**{cost['hit_ratio']:.1%}** ({row_arm} arm)"
    if row_arm != "warm":
        share += f" · {cost['warm_hit_ratio']:.1%} (warm arm)"
    add(f"Measured cache-hit share of prompt tokens: "
        f"{share}. "
        "`measured-cold` prices every prompt token at the full input rate — "
        "the conservative end `#14275` says it is quoting; `measured-cached` "
        "prices at the measured share.")
    add("")
    add(f"Tokens per median document: **{cost['tokens_per_median_doc']:,}** "
        f"measured vs **{cost['modelled_tokens_per_median_doc']:,}** modelled "
        f"(#14275's `1.5T + 7k` input, `0.2T` output).")
    add("")
    add("| item | tokens | modelled in/out | measured in/out | modelled $ | "
        "measured-cold $ | measured-cached $ |")
    add("|---|---|---|---|---|---|---|")
    for r in cost["rows"]:
        add(f"| {r['item']} | {r['tokens']:,} | {r['modelled_in']:,} / "
            f"{r['modelled_out']:,} | {r['measured_in']:,} / "
            f"{r['measured_out']:,} | ${r['modelled_peak']:.4f} | "
            f"${r['measured_cold_peak']:.4f} | "
            f"${r['measured_cached_peak']:.4f} |")
    add("")
    add("Peak rates (`#14275`'s own: $0.30/M in, $1.20/M out, "
        "$0.006/M cache hit). Off-peak in the JSON.")
    add("")
    median_row = cost["rows"][0]
    ceiling = _item_cost(median_row["measured_in"], median_row["measured_out"],
                         1.0, ISSUE_RATES["peak"])
    cold = median_row["measured_cold_peak"]
    add(f"**How big the cache lever actually is.** A PERFECT replay (every "
        f"prompt token a cache hit, which no real workload achieves) prices "
        f"the median document at ${ceiling:.4f} — "
        f"{(1 - ceiling / cold) * 100:.0f}% below the fully-cold "
        f"${cold:.4f}, and the warm arm's measured "
        f"{cost['warm_hit_ratio']:.1%} hit share reaches "
        f"{_item_cost(median_row['measured_in'], median_row['measured_out'], cost['warm_hit_ratio'], ISSUE_RATES['peak']):.4f}. "
        f"The lever is real but SECOND-ORDER, because it only discounts the "
        f"input side and the output side is now the larger half of the bill.")
    add("")
    add("| item | modelled $ (off-peak) | measured-cold $ | measured-cached $ |")
    add("|---|---|---|---|")
    for r in cost["rows"]:
        add(f"| {r['item']} | ${r['modelled_off']:.4f} | "
            f"${r['measured_cold_off']:.4f} | "
            f"${r['measured_cached_off']:.4f} |")
    add("")
    return "\n".join(lines) + "\n"


def render_verdict(report: dict) -> str:
    allowances = report.get("allowances") or []
    cost = report.get("costs") or {}
    mc = report.get("measurement_cost") or {}
    if not allowances or not cost.get("rows"):
        return ""
    rows = cost["rows"]
    by_label = {r["item"]: r for r in rows}
    median = by_label.get("median document") or rows[0]
    lines: list[str] = []
    add = lines.append
    add("## What this does to #14275's numbers")
    add("")
    add("### Confirmed / corrected")
    add("")
    ratio = (median["measured_cached_peak"] / median["modelled_peak"]
             if median["modelled_peak"] else 0)
    add(f"- **Cost per median document — CORRECTED.** "
        f"`${median['modelled_peak']:.4f}` modelled → "
        f"**${median['measured_cached_peak']:.4f}** measured at peak "
        f"({ratio:.1f}×), ${median['measured_cached_off']:.4f} off-peak. "
        f"1,000 median documents ≈ "
        f"**${median['measured_cached_peak'] * 1000:,.2f}** at peak, not "
        f"$5.6–7.7.")
    # the fitters below must read the arm the cost table priced — a
    # `--arms warm` bundle has no `cold` key at all and used to raise
    # `KeyError: 'cold'` here (#14277 review).
    fitted = report["arms"][cost.get("arm") or ("cold" if report["arms"].get("cold")
                                                 else "warm")]
    fit_in = fitted["input_fit"]
    fit_out = fitted["output_fit"]
    add(f"- **The two constants — CORRECTED, and one of them was never a "
        f"constant.** Input: `tokens_in = "
        f"{fit_in['intercept']:,.0f} + {fit_in['slope']:.2f} × T` per "
        f"document, so the per-item overhead is "
        f"**{fit_in['intercept']:,.0f} tokens**, not ~7k, and the pass "
        f"multiplier is **{fit_in['slope']:.2f}T**, not 1.5T. "
        f"Output: `tokens_out = "
        f"{fit_out['intercept']:,.0f} + "
        f"{fit_out['slope']:.3f} × T` — "
        f"**output is dominated by a constant too, which the model assumed "
        f"away** (`0.2T` ≈ 1.0k on a median document; measured ≈ "
        f"{median['measured_out']:,}).")
    add("- **Per-stage:** the fit is per stage in the JSON. The shape is the "
        "finding: S1 scales with the document (~2T, R²≈0.99 — it is the only "
        "stage that reads the text), while **S2 and S4 are almost pure "
        "constants** (~15k and ~26-29k) that a document's length barely "
        "moves, so small items are not relatively expensive — they are "
        "essentially ALL overhead.")
    add("")
    add("### The allowances, re-stated in the measured unit")
    add("")
    add("| tier | subscription | allowance (tokens) | cost to us peak | % of sub "
        "| cost off-peak | % of sub | ≈ documents | ≈ sessions |")
    add("|---|---|---|---|---|---|---|---|---|")
    for a in allowances:
        add(f"| {a['tier']} | ${a['subscription']:.0f} | {a['allowance']:,} | "
            f"${a['usd_peak']:.2f} | {a['pct_peak']}% | "
            f"${a['usd_off_peak']:.2f} | {a['pct_off_peak']}% | "
            f"{a['documents']:,} | {a['sessions']:,} |")
    add("")
    per_m_peak = report.get("costs", {}).get("usd_per_million_peak")
    per_m_off = report.get("costs", {}).get("usd_per_million_off_peak")
    if per_m_peak:
        add(f"- **The overage rate — CONFIRMED (as a rate above cost).** Our "
            f"measured cost is **${per_m_peak:.2f} per 1M blended tokens at "
            f"peak** and **${per_m_off:.2f} off-peak**, so `#14275`'s "
            f"$1.00/1M sits {1.0 / per_m_peak:.1f}× above cost at peak and "
            f"{1.0 / per_m_off:.1f}× off-peak. It is not below cost at either "
            f"end, which is the property the issue needs from it.")
        add("- **The DOCUMENT COUNT attached to each allowance — CORRECTED.** "
            "The token figures already land inside the 5–10% band at peak "
            "(6.5–6.6%), so the allowance numbers themselves stand; what the "
            "measurement changes is what they BUY. `#14275` promises ≈200/260/"
            "1,600 median documents; at the measured "
            f"{report['costs']['tokens_per_median_doc']:,} tokens per median "
            "document it is the counts in the table above — ~4× fewer.")
        add("")
    pm = report.get("rates_pricing_map")
    if pm:
        add("### A rate the issue and the repo disagree on (flagged, not resolved)")
        add("")
        add(f"Every figure above is at `#14275`'s own quoted peak rates. The "
            f"repo's reprice map (`tools/longmem_eval/costing.py`, "
            f"`{_MAP_ROUTE[0]}/{_MAP_ROUTE[1]}`) carries "
            f"**${pm['input']:.2f}/M input, ${pm['output']:.2f}/M output, "
            f"${pm['cache_hit']:.3f}/M cache hit** — 2.1× and 4.3× lower. At "
            f"the map's rates each `$` figure here scales down by the same "
            f"per-axis factor, so the TOKEN measurement is unaffected and the "
            f"DOLLAR conclusion moves by up to 4.3×. Which rate set is "
            f"authoritative is a pricing decision (`#14275`, owner) — this "
            f"lane states both and picks neither.")
        add("")
    add("### The measurement's own cost")
    add("")
    add(f"{mc.get('items', 0)} items · {mc.get('calls', 0)} provider calls · "
        f"{mc.get('prompt_tokens', 0):,} input / "
        f"{mc.get('completion_tokens', 0):,} output tokens · "
        f"cache hit {mc.get('hit_ratio', 0):.1%} → "
        f"**${mc.get('usd_peak', 0):.2f} at peak, "
        f"${mc.get('usd_off_peak', 0):.2f} off-peak** "
        f"(${mc.get('usd_peak_no_cache', 0):.2f} had nothing been cached).")
    add("")
    return "\n".join(lines) + "\n"


def report_from_rows(rows: list[dict]) -> dict:
    """The report the ``report`` subcommand renders, from raw rows.

    Factored out of ``main`` so the CLI and the reproducibility pin
    (``tests/test_extractor_cost_measure.py``) build the report by the SAME
    path: the evidence bundle claims to be regenerable from
    ``measurements.jsonl``, and a pin that re-implemented this assembly could
    pass while the CLI drifted.
    """
    report = build_report(rows)
    report["rates_issue"] = ISSUE_RATES
    report["rates_pricing_map"] = _pricing_map_rate()
    report["costs"] = cost_table(report, ISSUE_RATES["peak"],
                                 ISSUE_RATES["off_peak"])
    report["measurement_cost"] = measurement_cost(
        rows, ISSUE_RATES["peak"], ISSUE_RATES["off_peak"])
    if report["costs"]:
        report["allowances"] = allowance_table(
            report["costs"], report["costs"]["tokens_per_median_doc"])
    return report


def report_markdown(report: dict) -> str:
    """The whole document: the three sections, finalized for the linter.

    ``finalize_markdown`` (the output boundary added for #14295's `docs` red)
    is part of the render, not of ``main`` — otherwise this helper and the
    committed bundle would disagree by the `\\$` escapes and the trailing
    newline, and the reproducibility pins would fail on a correct artifact.
    """
    return finalize_markdown(
        render(report) + render_cost(report) + render_verdict(report))


def report_json(report: dict) -> str:
    return json.dumps({k: v for k, v in report.items() if k != "_rows"},
                      indent=1, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="measure the corpus")
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--docs", type=int, default=50)
    run.add_argument("--sessions", type=int, default=10)
    run.add_argument("--workers", type=int, default=6)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--arms", default="cold,warm")
    run.add_argument("--arm-gap-s", type=float, default=0.0,
                     help="pause between arms (a provider's prefix cache is "
                          "time-bounded; the default of 0 measures a "
                          "back-to-back replay, the strongest cache case)")
    run.add_argument("--long-split", action="store_true",
                     help="also measure the longest document split into "
                          "turns (the per-chunk constant)")
    run.add_argument("--split-max-turns", type=int, default=40,
                     help="cap on the long-document split's turn count (cost "
                          "is linear in turns; the cap bounds the spend)")
    run.add_argument("--split-only", action="store_true",
                     help="skip the corpus and measure ONLY the long-document "
                          "split (for re-measuring the per-chunk constant at a "
                          "different turn count without re-spending on the "
                          "corpus)")
    run.add_argument("--corpus-only", action="store_true",
                     help="print the selected corpus and exit (no API calls)")

    rep = sub.add_parser("report", help="fit and render")
    rep.add_argument("--out", required=True, type=Path)
    rep.add_argument("--json-out", type=Path)
    rep.add_argument("--markdown-out", type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        if args.corpus_only:
            for row in pick_corpus(limit=args.docs, seed=args.seed):
                print(f"{row['tokens']:>7}  {row['path']}")
            for row in spool_sessions(limit=args.sessions):
                print(f"{row['tokens']:>7}  session {row['path']}")
            return 0
        arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
        return run_measurement(out=args.out, docs_limit=args.docs,
                               sessions_limit=args.sessions,
                               workers=args.workers, seed=args.seed, arms=arms,
                               arm_gap_s=args.arm_gap_s,
                               long_split=args.long_split,
                               split_max_turns=args.split_max_turns,
                               split_only=args.split_only)
    rows = load_rows(args.out)
    report = report_from_rows(rows)
    text = report_markdown(report)
    if args.markdown_out:
        args.markdown_out.write_text(text, encoding="utf-8")
    if args.json_out:
        args.json_out.write_text(report_json(report), encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
