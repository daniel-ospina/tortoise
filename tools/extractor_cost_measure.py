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

    # the S4 embed-list saving, OFFLINE — no provider key, no spend (#14371):
    # per emission before -> after, per document before -> after with the
    # arithmetic, and what the saving buys at the given allowance levels
    uv run python tools/extractor_cost_measure.py embed-json \\
        --receipts tests/eval/write_path/receipts/7947-arms-2026-10-18 \\
        --evidence docs/evidence/14277-extractor-cost/measurements.jsonl

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
    # ⛔ A LIMIT OF 0 IS A LEGAL REQUEST ("measure no documents"), not a crash.
    # `rows[-1] = inv[-1]` below used to raise `IndexError: list assignment
    # index out of range` on an empty slice — a traceback instead of a result,
    # reached by `run --docs 0` (review cycle 13, P3). The landmark loop above
    # always fills `picked`, so the empty case is only ever this slice.
    if limit <= 0 or not rows:
        return []
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


_TOGGLE = "TORTOISE_S4_EMBED_JSON"

#: What each measurement ARM asks that lever to be. An arm absent from this map
#: is not a lever arm (``cold``/``warm`` size the cache ladder), so it inherits
#: the ambient value.
_ARM_LAYOUT = {"after": "compact", "before": "indent"}

#: The CLOSED set of arm names the tool understands: the two that move the lever
#: plus the two cache-ladder arms that deliberately inherit the ambient value.
#: `--arms` refuses anything else — see the refusal in `main()`.
_ARMS = frozenset(_ARM_LAYOUT) | {"cold", "warm"}


def _embed_mode() -> str:
    """The serialization `render_s4_prompt` will really use — production's own
    resolution, not a copy of its rule.

    ⛔ RECORDED PER ROW, AND RECORDED BY READING THE ENVIRONMENT — never by
    re-deriving it from the arm label. A value computed as
    ``"compact" if arm == "after" else "indent"`` is a function of the label, so
    it can never disagree with it and can never catch a mislabelled run — which
    is the one thing the field exists to make visible (review cycle 10, P2).

    The resolution itself is delegated to ``extractor_v2._embed_json_mode``,
    which derives it from the kwargs the render passes to ``json.dumps``. A
    hand-written copy of that rule here (``.strip().lower() == "compact"``) is a
    SECOND parser of the same predicate: it agrees today and is free to drift
    the first time the toggle grows a spelling, and the drift would show up as a
    row that records a layout the render did not take.
    """
    from tortoise.extractor_v2 import _embed_json_mode
    return _embed_json_mode()


def _install_arm(arm: str) -> None:
    """Arm the lever for a whole BATCH.

    ``after`` arms compact; ``before`` pins it off; any other arm name (the
    ``cold``/``warm`` cache arms) leaves the ambient value alone, because those
    arms size the cache ladder rather than this lever.
    """
    if arm == "after":
        os.environ[_TOGGLE] = "compact"
    elif arm == "before":
        os.environ.pop(_TOGGLE, None)


def _assert_the_arm_is_what_rendered(arm: str, observed: str, *,
                                     at_exit: str | None = None) -> None:
    """Refuse a row whose render did not carry its arm's setting.

    ⛔ Review cycle 10 called the corrupted row "indistinguishable from a good
    one": recording the OBSERVED layout makes it visible in the artifact, but a
    row that was still written as a good measurement of that arm is evidence a
    reader has to distrust rather than evidence the harness refused. So the two
    ways an observation can disagree — a lever that moved under the measurement,
    or a batch that did not install what its arm asked for — RAISE, and the
    caller's own failure path records the row as FAILED.
    """
    if at_exit is not None and at_exit != observed:
        raise RuntimeError(
            f"{_TOGGLE} resolved to {observed!r} at entry and {at_exit!r} at "
            f"exit — the lever moved mid-measurement, so neither layout "
            f"describes this item's whole pipeline run and the row cannot be "
            f"labelled")
    expected = _ARM_LAYOUT.get(arm)
    if expected is not None and observed != expected:
        raise RuntimeError(
            f"the {arm!r} arm requests {expected!r} but the render resolved "
            f"{observed!r} — the lever moved under the measurement, so this "
            f"row cannot be attributed to an arm")


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
        # ⛔ THE ARM IS A SETTING, NOT A LABEL — but the setting is installed by
        # the BATCH, not by this function (see the `by_arm` loop below). It was
        # a label only: nothing on the measurement path wrote `os.environ`, so
        # `--arms before,after` measured the SAME render twice in one process and
        # the before→after delta came back 0 — a false pass with no banner, in
        # the one command the bundle documents as finishing the job (review
        # cycle 9, P2). `TORTOISE_S4_EMBED_JSON` is the lever this harness exists
        # to size, so `after` ARMS it and `before` pins it off.
        model = _build_model()   # one per call: the adapter carries per-call
                                 # state (last_*), which races under workers>1
        if kind == "doc":
            conversation = [{"role": "user",
                             "content": item["text"]}]
        else:
            conversation = item["turns"]
        mode_before = _embed_mode()      # sampled BEFORE the pipeline: S4 renders
                                        # late, so the label must bracket the run
        rec = measure_one(model, conversation,
                          session_date="2026-11-21T00:00:00+00:00")
        rec["arm"] = arm
        #: the serialization the render ACTUALLY used — sampled at entry and at
        #: exit, so a mislabelled row is VISIBLE instead of self-consistent and a
        #: lever that moved under the measurement is REFUSED instead of written
        #: as a good row.
        rec["s4_embed_json"] = mode_before
        _assert_the_arm_is_what_rendered(arm, mode_before,
                                         at_exit=_embed_mode())
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
    first_batch = True
    # ⛔ ARMS RUN AS SEQUENTIAL BATCHES — NEVER INTERLEAVED (review cycle 10, P2).
    # `os.environ` is PROCESS-GLOBAL and every worker thread shares it, so the
    # first cut of this fix — installing the flag per JOB, around the single
    # `measure_one` call — let an `after` row and a `before` row overwrite each
    # other between a thread's install and its measure. At the documented
    # `--arms before,after` and the DEFAULT `--workers 6`, that is a race rather
    # than an arm, and it reproduces the very false pass the fix was meant to
    # remove (a before→after delta that is an artefact of thread scheduling).
    # Installing the value ONCE per batch, before any thread starts, and
    # restoring it only after the batch has joined, makes it constant for every
    # call in that batch.
    by_arm: dict[str, list[tuple[dict, str]]] = {}
    for item, kind, arm in jobs:
        by_arm.setdefault(arm, []).append((item, kind))
    for batch_arm, batch_jobs in by_arm.items():
        #: ⛔ `--arm-gap-s` IS HONOURED HERE (review cycle 11, P3). It was
        #: documented as "a pause between arms (a provider's prefix cache is
        #: time-bounded)" but was bound to a parameter nothing ever read — an
        #: option that promised a pause it never took, so a caller asking for
        #: 300 s to defeat prefix-cache carry-over silently got a back-to-back
        #: replay and a delta that included the cache warm-up. The pause belongs
        #: between BATCHES, which is exactly where the arms now meet.
        if not first_batch and arm_gap_s > 0:
            print(f"arm gap: pausing {arm_gap_s:g}s before {batch_arm!r}",
                  flush=True)
            time.sleep(arm_gap_s)
        first_batch = False
        saved_embed = os.environ.get(_TOGGLE)
        try:
            _install_arm(batch_arm)
            with cf.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_job, item, kind, batch_arm):
                           (item, kind, batch_arm)
                           for item, kind in batch_jobs}
                for fut in cf.as_completed(futures):
                    item, kind, arm = futures[fut]
                    finished += 1
                    try:
                        rec = fut.result()
                    except Exception as exc:
                        # a 360-call measurement; the failure is recorded as a
                        # row so the report can state how many items did not
                        # measure.
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
        finally:
            if saved_embed is None:
                os.environ.pop(_TOGGLE, None)
            else:
                os.environ[_TOGGLE] = saved_embed

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
        # Same lever, same contract as the batch loop in `run_measurement`: the
        # arm is a SETTING. This block is serial, so install/restore around the
        # single call is race-free — it is written this way so the two paths
        # cannot disagree about what an arm means.
        saved_embed = os.environ.get(_TOGGLE)
        try:
            _install_arm(arm)
            mode_before = _embed_mode()
            rec = measure_one(model,
                              [{"role": "user", "content": t} for t in turns],
                              session_date="2026-11-21T00:00:00+00:00")
            mode_at_exit = _embed_mode()
        finally:
            if saved_embed is None:
                os.environ.pop(_TOGGLE, None)
            else:
                os.environ[_TOGGLE] = saved_embed
        rec["arm"] = arm
        rec["s4_embed_json"] = mode_before
        #: the same refusal the batch loop makes — the two paths must not
        #: disagree about what an arm MEANS, only about how they install it.
        _assert_the_arm_is_what_rendered(arm, mode_before,
                                         at_exit=mode_at_exit)
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


def affordable_count(allowance: int, unit_cost: float) -> int:
    """How many ``unit_cost``-priced items a token ``allowance`` can BUY.

    ⛔ FLOOR, never ``round()`` — this is a capacity claim, not a ratio.
    `round()` overstated three of the six printed cells: solo-after
    ``3,000,000/60,201 = 49.83`` printed **50**, and ``50 × 60,201 =
    3,010,050`` is 10,050 tokens PAST the allowance; team-after printed 399
    for ``24,020,199 > 24,000,000``; pro-before printed 62 for
    ``4,017,538 > 4,000,000``; pro's session count printed 110 for
    ``4,005,650 > 4,000,000``. A table whose "what you get for your tier"
    column cannot be bought at that tier is wrong in the direction that
    flatters us, so the count is what FITS, not the nearest integer.

    The bundle was already inconsistent about its own convention — §5's
    ``45,000``/``32,000`` columns and the ``+43 %`` derived from them are
    floors, while the ``64,799`` column was ``round()`` — which is why the
    three overstating cells survived four review rounds (review round 4,
    P2: ``docs/evidence/14343-amplify/README.md:320-322`` and
    ``.env.example:1767``). One convention, and it is the one that cannot
    overpromise.
    """
    if not unit_cost or unit_cost <= 0:
        return 0
    return int(allowance // unit_cost)


def allowance_table(cost: dict, tokens_per_doc: int) -> list[dict]:
    """`#14275`'s four allowances, re-stated in the MEASURED unit.

    The allowances are in TOKENS, so the measurement cannot change them —
    what it changes is (a) how many documents a token allowance is, and (b)
    what that allowance costs us. Both are derived here rather than asserted:
    the in:out mix comes from the measured median document, and the cost per
    token from the measured cache behaviour at peak rates.

    The document/session counts are FLOORS (`affordable_count`) — round()
    printed counts the allowance cannot afford.
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
            "documents": affordable_count(allowance, tokens_per_doc),
            "sessions": (
                affordable_count(allowance, session_row["measured_in"]
                                 + session_row["measured_out"])
                if session_row else 0),
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
    # ⛔ Both warm-arm figures are guarded on the warm arm being PRESENT, because
    # `--arms` is free-form (`--arms cold` is supported, and `--arms warm` was
    # fixed above): `warm_hit_ratio` falls back to 0.0 when no warm row exists, so
    # an unguarded clause would report "0.0% (warm arm)" — a measurement attributed
    # to an arm the bundle never ran, which is the same defect one level down from
    # the double-naming this block was given a guard for.
    row_arm = cost.get("arm", "cold")
    has_warm = "warm" in (report.get("arms") or {})
    share = f"**{cost['hit_ratio']:.1%}** ({row_arm} arm)"
    if has_warm and row_arm != "warm":
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
        f"${cold:.4f}."
        # the warm-arm half needs a warm arm to point at; on a cold-only bundle
        # `warm_hit_ratio` is the 0.0 fallback, not a reading.
        + (f" The warm arm's measured {cost['warm_hit_ratio']:.1%} hit share "
           f"reaches "
           f"{_item_cost(median_row['measured_in'], median_row['measured_out'], cost['warm_hit_ratio'], ISSUE_RATES['peak']):.4f}."
           if has_warm else "")
        + " The lever is real but SECOND-ORDER, because it only discounts the "
        "input side and the output side is now the larger half of the bill.")
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


# ── #14343: WHERE the two constants come from ────────────────────────────
#
# `run` answers *how much*. It cannot answer *of what*: the fitted 45,503 /
# 22,802 are summed over stages, calls and regions, and a constant nobody can
# decompose cannot be shrunk without guessing. `compose` runs the SAME
# pipeline over ONE document and attributes every prompt token to the region
# that produced it, plus every output token to the records the model emitted.
#
# It is additive by construction: the region counts are computed on the exact
# strings `_fill_template` was handed (captured by wrapping it, so the table
# can never describe a prompt other than the one sent), and the difference
# between their sum and the prompt's own token count is reported as its own
# row. A table that does not sum is not evidence.

#: The stage → (system-prompt prefix, template constants) map. The prefix is
#: how a captured call is identified as a stage: the pipeline renders three
#: distinct system prompts and the first line of each is a fixed signature.
_COMPOSE_STAGE_SIGNATURES = (
    ("s1", "You are the STORY SUMMARIZER"),
    ("s2", "You are the GRAPH MAPPER"),
    ("s4", "You are the GAP REVIEWER"),
)


def _compose_stage(system: str) -> str | None:
    for stage, sig in _COMPOSE_STAGE_SIGNATURES:
        if (system or "").startswith(sig):
            return stage
    return None


def _output_structure(text: str) -> dict:
    """What a stage's OUTPUT is made of (#14343 step 2).

    The question the constant asks is not "how many tokens" but "tokens of
    WHAT, derived from WHAT". So a JSON emission is counted structurally:
    section → item count, item chars, and the kinds those items carry. A stage
    whose item COUNT does not move with the document is emitting something the
    document did not authorise, and the counts are what make that visible.
    """
    from tortoise.extractor_v2 import _parse_json_robust
    enc = _encoding()
    out: dict = {"chars": len(text or ""),
                 "tokens": len(enc.encode(text or "")),
                 "json": False, "sections": {}}
    if not text:
        return out
    try:
        parsed = _parse_json_robust(text)
    except Exception:
        # a narrative emission is not a defect — S1 emits prose, not JSON
        parsed = None
    if not isinstance(parsed, dict):
        # S1's narrative: report its shape rather than pretending it is JSON.
        out["paragraphs"] = len([p for p in (text or "").split("\n\n")
                                 if p.strip()])
        return out
    out["json"] = True
    for section, value in parsed.items():
        if isinstance(value, list):
            items = [v for v in value if isinstance(v, dict)]
            kinds: dict[str, int] = {}
            for item in items:
                kind = str(item.get("kind") or item.get("type") or "-")
                kinds[kind] = kinds.get(kind, 0) + 1
            payload_tokens = len(enc.encode(json.dumps(value, indent=1)))
            out["sections"][section] = {
                "items": len(value),
                # ⛔ ADDITIVE, not a mean. `item_chars` was a per-call mean, and
                # `_merge_sections` added it across a stage's calls — so the
                # published JSON carried the SUM OF TWO MEANS as if it were a
                # mean. `chars_sum` and `items` are the two additive halves and
                # both are published, so the mean is `chars_sum / items` at any
                # moment without storing a derived third key that could drift
                # from them (review cycle 6, P2: the previous wording promised
                # a mean "derived at publish time" that nothing derived).
                "chars_sum": sum(len(json.dumps(i)) for i in items),
                "tokens": payload_tokens,
                # ⛔ NOT truncated here. A per-call `[:12]` cap is invisible on
                # one call and LOSSY on two: 13 kinds in one call plus 5 more in
                # another published `distinct_kinds = 12` for a true union of
                # 18 — fewer kinds than either call knew (measured, review
                # cycle 6). Truncation belongs where a human reads the map, and
                # nothing renders `kinds`, so the full histogram is kept and a
                # merge is exact.
                "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
                "distinct_kinds": len(kinds),
            }
        elif isinstance(value, dict):
            out["sections"][section] = {
                "keys": len(value),
                "tokens": len(enc.encode(json.dumps(value, indent=1))),
            }
        else:
            out["sections"][section] = {
                "value_tokens": len(enc.encode(json.dumps(value))),
            }
    return out


def compose_one(*, doc: dict, master: dict | None = None,
                session_date: str = "2026-11-21T00:00:00+00:00",
                live: bool = True) -> dict:
    """Run the real pipeline over ONE document and decompose each prompt.

    ``live=False`` swaps the provider for a canned emission (the prompts are
    still the REAL rendered ones for S1/S2; S4's ``embed_list_json`` region is
    then a stub, which is why the offline arm is a shape check and the live
    arm is the measurement).
    """
    from tortoise import extractor_v2 as X

    calls: list[dict] = []
    renders: list[dict] = []
    slots: list[dict] = []

    orig_fill = X._fill_template
    orig_granularity = X._granularity_text
    orig_anchor = X._date_anchor

    def _fill(tmpl, values):
        rendered = orig_fill(tmpl, values)
        stage = ("s2" if tmpl in (X.S2_TMPL, X.S2_TMPL_CORE_ONLY)
                 else "s4" if tmpl in (X.S4_TMPL, X.S4_TMPL_CORE_ONLY,
                                       X.S4_TMPL_DELTA,
                                       X.S4_TMPL_CORE_ONLY_DELTA)
                 else None)
        renders.append({"stage": stage, "values": dict(values),
                        "prompt": rendered})
        return rendered

    def _granularity(master=None):
        text = orig_granularity(master)
        slots.append({"slot": "memory_granularity", "text": text})
        return text

    def _anchor(session_date, *, include_emission_rules=False):
        text = orig_anchor(session_date,
                           include_emission_rules=include_emission_rules)
        slots.append({"slot": "date_anchor", "text": text,
                      "emission": include_emission_rules})
        return text

    X._fill_template = _fill
    X._granularity_text = _granularity
    X._date_anchor = _anchor
    try:
        model = _compose_model(live=live, calls=calls)
        out = X.extract_session_v2(model, [{"role": "user",
                                            "content": doc["text"]}],
                                   sdk=None, session_date=session_date,
                                   master=master)
    finally:
        X._fill_template = orig_fill
        X._granularity_text = orig_granularity
        X._date_anchor = orig_anchor

    enc = _encoding()
    # Pair each provider call with the render that produced its system
    # prompt, by PREFIX and WITHOUT consuming it.
    #
    # ⛔ PREFIX, not equality: `render_s2_prompt` / `render_s4_prompt` append
    # the SOURCE TRANSCRIPT **outside** `_fill_template`
    # (`return rendered + "\n\n" + transcript`), and that tail is non-empty
    # whenever the EDUs fit `_SOURCE_TRANSCRIPT_CAP` (8,000 chars). An equality
    # test therefore fails for every SMALL document — and fails silently: the
    # region map comes back empty, the whole prompt is printed as one
    # `*template prose*` row, and the table still sums.
    #
    # ⛔ NOT consumed: a stage RENDERS ONCE and may CALL TWICE — the #1746 parse
    # re-prompt and the #2134 truncation escalation both re-send the IDENTICAL
    # system prompt. A one-shot consumption therefore leaves every retry
    # unmatched (`regions == {}`), which is the same silent misattribution in a
    # rarer disguise. The last render for the stage is the one, for all calls.
    for call in calls:
        stage = _compose_stage(call["system"])
        call["stage"] = stage
        match = None
        for render in reversed(renders):
            if render["stage"] != stage:
                continue
            if call["system"].startswith(render["prompt"]):
                match = render
                break
        call["regions"] = ({k: len(enc.encode(v or ""))
                            for k, v in (match["values"].items()
                                         if match else {})})
        tail = (call["system"][len(match["prompt"]):]
                if match and len(call["system"]) > len(match["prompt"]) else "")
        if tail:
            call["regions"]["source_transcript"] = len(enc.encode(tail))
        if match is None and stage == "s1":
            # S1 substitutes with .replace, not the fill map — its slots are
            # the granularity block and the date anchor, captured above.
            for slot in slots:
                if slot["slot"] == "memory_granularity" and "emission" not in slot:
                    call["regions"]["memory_granularity"] = len(
                        enc.encode(slot["text"] or ""))
                elif slot["slot"] == "date_anchor" and not slot["emission"]:
                    call["regions"]["date_anchor"] = len(
                        enc.encode(slot["text"] or ""))
        call["user_tokens"] = len(enc.encode(call["user"] or ""))
        call["system_tokens"] = len(enc.encode(call["system"] or ""))
        # ⛔ The USER message is a prompt region like any other, and it is the
        # BIGGEST one for S1 (the whole document) and a real one for S2 (the
        # framed story). Leaving it out of the region map did not lose it from
        # the total — it landed in the `*template prose*` residual, so `compose`
        # and `decompose` attributed the same tokens to different parties.
        call["regions"]["user_message"] = call["user_tokens"]
        call["output"] = _output_structure(call.get("response") or "")
        call.pop("response", None)
        call.pop("system", None)
        call.pop("user", None)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live": live,
        "doc": {"path": doc["path"], "item_tokens": doc["tokens"]},
        "session_date": session_date,
        "calls": calls,
        "pipeline": {"calls": out.get("stats", {}).get("llm", {}).get("calls"),
                     "retries": out.get("stats", {}).get("llm", {}).get(
                         "retries"),
                     "story_chars": len(out.get("story_arc") or ""),
                     "errors": list(out.get("errors") or [])[:5]},
    }


def _compose_model(*, live: bool, calls: list[dict]):
    """The measuring model: the production adapter, wrapped.

    The wrapper is the instrument, not a change to the pipeline — it records
    what was actually sent and returned, then delegates. ``live=False`` swaps
    only the transport for a canned emission, which is what makes the shape
    of the decomposition testable offline.
    """
    inner = _build_model() if live else _StubModel()

    class _Recording:
        """Delegating wrapper with a ``max_tokens`` KWARG in its signature.

        The kwarg is not decoration: ``extractor_v2._cap_kind`` picks the
        race-free per-call path only when the signature accepts it, and its
        fallback MUTATES ``model.max_tokens`` (an attribute this wrapper does
        not own). A ``**kwargs``-only signature therefore defers to a
        read-only property and every stage raises
        ``property 'max_tokens' has no setter`` before its first call.
        """

        def __init__(self, model):
            self._m = model

        def complete(self, *, system, user, max_tokens=None, **kwargs):
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            resp = self._m.complete(system=system, user=user, **kwargs)
            calls.append({"system": system, "user": user, "response": resp,
                          "prompt_tokens": int(
                              getattr(self._m, "last_prompt_tokens", 0) or 0),
                          "completion_tokens": int(
                              getattr(self._m, "last_completion_tokens", 0) or 0),
                          "finish_reason": getattr(
                              self._m, "last_finish_reason", None)})
            return resp

        def __getattr__(self, name):
            return getattr(self._m, name)

    return _Recording(inner)


class _StubModel:
    """A canned emission for the offline arm — never a measured result.

    Its only job is to let the pipeline reach S4 so the render path is
    exercised without a provider call. The S4 prompt's ``embed_list_json``
    region is therefore a STUB in this arm and the offline composition is a
    shape check: a table built from it describes the instructions, not the
    pipeline.
    """

    last_finish_reason = "stop"
    last_prompt_tokens = 0
    last_completion_tokens = 0
    last_cache_hit_tokens = 0
    last_cost_usd = 0.0
    last_route = "stub/stub"
    max_tokens = None

    _S2 = json.dumps({
        "entities": [{"name": "stub entity", "kind": "core:Project",
                      "lifecycle": "created"}],
        "events": [], "points": [], "operators": [],
        "link_before_create": [], "chain_notes": []})

    def complete(self, *, system, user, max_tokens=None, **kwargs):
        if (system or "").startswith("You are the STORY SUMMARIZER"):
            return "The session changed the state of the project."
        if (system or "").startswith("You are the GRAPH MAPPER"):
            return self._S2
        return json.dumps({"gaps": [], "corrections": [],
                           "link_before_create": [], "chain_notes": []})


def compose_report(artifacts: list[dict]) -> dict:
    """Fold one or more ``compose`` artifacts into the #14343 table.

    Two tables, because the constants have two audiences: the INPUT table
    attributes every prompt token to the region that produced it, and the
    OUTPUT table says what the model emitted and what it derived it from.
    """
    rows: list[dict] = []
    for art in artifacts:
        per_stage: dict[str, dict] = {}
        for call in art["calls"]:
            stage = call.get("stage") or "?"
            acc = per_stage.setdefault(stage, {
                "calls": 0, "system_tokens": 0, "user_tokens": 0,
                "completion_tokens": 0, "regions": {}, "output": {}})
            acc["calls"] += 1
            acc["system_tokens"] += call["system_tokens"]
            acc["user_tokens"] += call["user_tokens"]
            acc["completion_tokens"] += call["completion_tokens"]
            for name, tokens in (call.get("regions") or {}).items():
                acc["regions"][name] = acc["regions"].get(name, 0) + tokens
            out = call.get("output") or {}
            acc["output"]["tokens"] = (acc["output"].get("tokens", 0)
                                       + out.get("tokens", 0))
            acc["output"]["sections"] = _merge_sections(
                acc["output"].get("sections") or {}, out.get("sections") or {})
        rows.append({"doc": art["doc"], "stages": per_stage,
                     "pipeline": art.get("pipeline") or {}})
    # ⛔ `live` is propagated or the renderer cannot tell a stub run from a
    # measured one, and the report is the artifact a reader quotes.
    live = all(a.get("live", False) for a in artifacts)
    return {"rows": rows, "live": live,
            "complete": all(_row_complete(r) for r in rows) if rows else False}


#: The stages a complete measurement must have observed. A stage that RAISED is
#: not a stage that measured: `_Recording.complete` appends to `calls` only
#: after the adapter RETURNS, so a failing stage contributes no call at all and
#: the table would show the surviving stages as if they were the whole run.
#: Error signatures that mean a stage produced NO call — a lost call, and the
#: only pipeline errors that make a `compose` row incomplete. Everything else
#: `extractor_v2` records (a truncated tail it dropped, a malformed section it
#: kept past, a failed S5/chain/entity-resolution/classify step) is recorded on a
#: call that RETURNED and was measured, so it is a note and not a verdict.
#:
#: ⛔ `"S4 failed"` IS DELIBERATELY ABSENT FROM THIS SET (review cycle 15, P3).
#: `capture_session` appends it from ONE `except` that wraps `run_s4(...)`
#: (`tortoise/extractor_v2.py:15293`) *and* the post-call merge/shape work that
#: follows it (`:15292`–`:15340`), while `_Recording.complete` appends a call
#: only after the adapter RETURNS — so the marker is the error TYPE of a
#: failure, not the fact that no call happened. An S4 whose prompt was rendered,
#: sent and measured but whose output then failed to parse or merge carries this
#: marker on a call that WAS measured, and reading it as a lost call refused an
#: intact composition and exited 1: the same over-block class cycle 9 fixed for
#: `"S4 output partial"`, one marker over. The fail-open it used to close stays
#: closed STRUCTURALLY — `compose_report` builds `stages` only from recorded
#: calls, so an S4 that really produced no call leaves `"s4"` out of `stages`
#: and `_REQUIRED_STAGES` below already refuses the row. That split is also the
#: one OpenTelemetry's GenAI conventions draw: a failure AFTER a response was
#: received (schema validation, parsing) keeps the response content and its
#: token usage and records an `error.type`, while only a request failure means
#: nothing was measured.
#: ⛔ EVERY MEMBER MUST NAME A CALL THAT WAS NEVER RECORDED. `_Recording.complete`
#: appends a call only AFTER the adapter returns, so a marker appended by code
#: that runs *after* a stage returned describes a post-processing failure on a
#: call that WAS measured — a note, not a lost call. Review cycle 13 measured two
#: members that are exactly that, and refused a complete run for them:
#:   * `"no embed list produced (S2/S4 empty)"` is appended in the **S5** branch
#:     (`tortoise/extractor_v2.py:15625`), i.e. only after `run_s2` AND `run_s4`
#:     returned and merged. It cannot indicate a lost call by construction.
#:   * `"S2 failed"` is the SAME shape as the `"S4 failed"` this list already
#:     dropped in cycle 12 — one broad `except` around `run_s2(...)`, whose
#:     `_complete_parsed` raises `_ParseError` after the adapter already returned.
#: A genuinely call-less stage is refused structurally by `_REQUIRED_STAGES`.
_LOST_CALL_MARKERS = ("chunk failed",)

_REQUIRED_STAGES = ("s1", "s2", "s4")


def _stages_without_a_measured_call(row: dict) -> list[str]:
    """Which `_REQUIRED_STAGES` a MEASURED row has no recorded call for.

    The measurement-row half of `_row_complete`'s missing-stage test. The two
    row shapes are not the same: a `compose` row also carries `pipeline`
    (`errors`, attempt counts), while a row that has been through
    `measurements.jsonl` carries only `stages` — so the PRESENCE test is shared
    here and the error/attempt reads stay in `_row_complete`.
    """
    stages = row.get("stages") or {}
    return [s for s in _REQUIRED_STAGES
            if not (stages.get(s) or {}).get("calls")]


def _row_complete(row: dict) -> bool:
    """Whether a `compose` row is a measurement rather than a partial run.

    ⛔ THE FAIL-OPEN THIS CLOSES (review cycle 7, P1). The provenance gate above
    distinguishes a stub transport from a live one, and says nothing about a
    live run whose stages FAILED. Measured on a box with no provider key — the
    exact state the evidence README documents — every call raised
    `401 Unauthorized`, so `calls` was empty, `pipeline.errors` held three
    entries, and the rendered output was a header plus three empty tables with
    **no marker at all** and exit 0. A partial run is worse: S1+S2 rendering
    with S4 (the stage whose embed-list region is this bundle's subject) simply
    absent reads as a smaller, complete measurement.

    ⛔ AND THE GUARD IS SCOPED TO ERRORS THAT EXPLAIN A LOST CALL (review cycle 9,
    P1 — two independent reviewers). `extractor_v2` records errors on calls that
    RETURNED and were fully measured: rung-4 partial accept appends
    `"S4 output partial — truncated tail dropped"` (`tortoise/extractor_v2.py:15347`),
    `"S4 output malformed section … kept S2 output"` (`:15302`) and the S2 twin
    (`:18014`) are the same, as are the post-extraction `S5 failed` / `chain …` /
    `entity resolution failed` / `classify(union) failed` entries. Treating ANY
    error as "did not measure" refused a complete composition and exited 1 — and
    it is not clearable by re-running, because README §2's 13 of 50 documents
    averaging >6,000 S4 completion tokens (max 24,000) is exactly what rung 4
    exists for, so the documented `compose --out …` exits 1 DETERMINISTICALLY on
    such a `--doc`. The fail-open this block exists for is still closed by the
    missing-stage test and the retry-aware call count below; a content-level
    error is a NOTE, not an incompleteness.

    ⛔ AND THE LOST-CALL SET IS A NAMED SET, NOT "ANY ERROR" (review cycle 15,
    P3 — see `_LOST_CALL_MARKERS`'s `"S4 failed"` note). A marker that the
    pipeline can append AFTER the call RETURNED describes a failure of
    post-processing, so it is a note; the incompleteness of a genuinely
    call-less stage is decided by the recorded CALLS themselves, through
    `_REQUIRED_STAGES` below.
    """
    pipe = row.get("pipeline") or {}
    if any(marker in err for err in (pipe.get("errors") or [])
           for marker in _LOST_CALL_MARKERS):
        return False
    stages = row.get("stages") or {}
    if not all(stages.get(s) for s in _REQUIRED_STAGES):
        return False
    emitted = sum(int(v.get("calls") or 0) for v in stages.values())
    made = pipe.get("calls")
    # ⛔ `pipeline.calls` is the sum of ATTEMPTS, not of successful calls:
    # `_rollup_llm` does `llm_stats["calls"] += stage_stats.get("attempts", 0)`
    # (`tortoise/extractor_v2.py:16778`) and `_complete` sets `attempts=attempt`
    # only on the SUCCESSFUL attempt. So a transient 429/5xx that succeeds on
    # retry adds +1 here with no call recorded by `_Recording` — and comparing
    # the two WITHOUT the retry count flagged a fully-measured run as INCOMPLETE
    # and made `main` exit 1, refusing a valid artifact and sending the next
    # lane to re-spend until it got a retry-free run (review cycle 7, P2).
    return made is None or int(made) <= emitted + int(pipe.get("retries") or 0)


def _merge_sections(a: dict, b: dict) -> dict:
    """Fold two per-call section maps.

    Two keys are not sums and must not be added blindly, which is what the
    previous revision did (review cycle 5, P3): `distinct_kinds` is a per-CALL
    count of a call-local histogram, so it is read off the UNION of the kind
    maps, and the per-item character figure is accumulated as `chars_sum`
    (additive) with its denominator `items` alongside — `chars_sum / items` is
    the mean, and no derived mean is stored to disagree with them.

    The union is taken over the FULL histograms: truncating the merged map here
    would make `distinct_kinds` report the display cap, not the count (measured:
    13 kinds + 5 more published 12 for a union of 18 — FEWER than either call
    knew). `_output_structure` therefore publishes the untruncated histogram
    too, and nothing renders `kinds`.
    """
    merged = {k: dict(v) for k, v in a.items()}
    for section, val in b.items():
        cur = merged.setdefault(section, {})
        for key, value in val.items():
            if key == "kinds" and isinstance(value, dict):
                kinds = dict(cur.get("kinds") or {})
                for kind, count in value.items():
                    kinds[kind] = kinds.get(kind, 0) + count
                cur["kinds"] = dict(sorted(
                    kinds.items(), key=lambda kv: -kv[1]))
            elif isinstance(value, (int, float)) and isinstance(
                    cur.get(key), (int, float)):
                cur[key] = cur[key] + value
            else:
                cur.setdefault(key, value)
        if cur.get("kinds"):
            cur["distinct_kinds"] = len(cur["kinds"])
        # (no `item_chars` here: nothing renders it, and a derived key with no
        # reader is the dead field review cycle 5 caught as `contract_tokens`.
        # `chars_sum` and `items` are both published, so the mean is theirs to
        # divide — it is not stored, and cannot drift from them.)
    return merged


def render_compose(report: dict) -> str:
    """The #14343 deliverable: a table whose parts SUM to the prompt."""
    lines: list[str] = ["## Prompt composition (#14343)", ""]
    # ⛔ THE MARKER IS THE POINT. An `--offline` run swaps only the TRANSPORT for
    # a stub, so its region map and its emission shapes are real while its
    # per-stage token counts are the stub's. Without this line the rendered
    # table is structurally identical to a measured one, and a bundle whose
    # whole purpose is that a stub must never read as a measurement would print
    # exactly that (review cycle 5, P3).
    # ⛔ FAIL-CLOSED: `report.get("live") is not True` — NOT `.get("live", True)`.
    # A report that does not carry the key is a report whose provenance is
    # unknown, and unknown must render as a stub, never as a measurement: the
    # fail-open direction prints no marker and is byte-shape identical to a
    # measured run, which is the false pass this whole block exists to prevent
    # (review cycle 6, P2). `compose_report` already takes the fail-closed view
    # (`all(a.get("live", False))`), so the two halves agree.
    if report.get("live") is not True:
        lines.append(
            "⚠️ **OFFLINE / STUB TRANSPORT — NOT A MEASUREMENT.** The region map "
            "and the emission shapes below are real render output, but every "
            "`completion` figure is the stub's, not a model's. Re-run without "
            "`--offline` on a box with a provider key before quoting any of it.")
        lines.append("")
    elif report.get("complete") is not True:
        # ⛔ A LIVE run is not automatically a MEASUREMENT (review cycle 7, P1).
        # A stage that raised appends no call, keeps the surviving stages
        # looking measured, and — before this branch — printed no marker and
        # exited 0. Name the stages that are missing and the errors the
        # pipeline itself recorded, so the table cannot be quoted as partial.
        missing = sorted({s for row in report.get("rows") or []
                          for s in _REQUIRED_STAGES
                          if not (row.get("stages") or {}).get(s)})
        errs = [e for row in report.get("rows") or []
                for e in ((row.get("pipeline") or {}).get("errors") or [])]
        lines.append(
            "⚠️ **INCOMPLETE LIVE RUN — NOT A MEASUREMENT.** The table below is "
            "missing stage(s) or recorded pipeline errors, so it is a partial "
            "run, not a decomposition: "
            + (", ".join(f"`{s}`" for s in missing) if missing
               else "every stage reported, but the pipeline counted calls the "
                    "instrument never saw complete")
            + ".")
        for err in errs[:5]:
            lines.append(f"* pipeline error: `{err}`")
        lines.append("")
        lines.append("A stage that RAISES emits no call, so the surviving rows "
                     "read as if they were the whole run. Re-run to a "
                     "complete artifact before quoting any figure here.")
        lines.append("")
    else:
        # ⛔ A CONTENT-LEVEL note, not a verdict (review cycle 9, P1). These are
        # recorded on calls that RETURNED and were measured — a truncated tail
        # the rung-4 partial accept dropped, a malformed section the pipeline
        # kept past, a failed S5/chain/entity-resolution/classify step. They do
        # not cost a call and must not flip `complete`, but a reader still
        # deserves to know the emission was not clean.
        notes = [e for row in report.get("rows") or []
                 for e in ((row.get("pipeline") or {}).get("errors") or [])]
        if notes:
            lines.append("ℹ️ **Measured, with content-level notes.** Every stage "
                         "reported and every prompt was measured; the pipeline "
                         "recorded the following on calls that RETURNED, so they "
                         "do not affect the composition:")
            for note in notes[:5]:
                lines.append(f"* {note}")
            lines.append("")
    for row in report["rows"]:
        lines.append(f"### `{row['doc']['path']}` — "
                     f"{row['doc']['item_tokens']} tokens")
        lines.append("")
        lines.append("| stage | calls | system | user | **total prompt** "
                     "| completion |")
        lines.append("|---|---|---|---|---|---|")
        for stage, acc in sorted(row["stages"].items()):
            lines.append(
                f"| {stage} | {acc['calls']} | {acc['system_tokens']:,} | "
                f"{acc['user_tokens']:,} | "
                f"**{acc['system_tokens'] + acc['user_tokens']:,}** | "
                f"{acc['completion_tokens']:,} |")
        lines.append("")
        lines.append("| stage | region | tokens | share of that stage's "
                     "prompt |")
        lines.append("|---|---|---|---|")
        for stage, acc in sorted(row["stages"].items()):
            total = acc["system_tokens"] + acc["user_tokens"]
            # zero-token regions are omitted from the RENDER (a spliced empty
            # value spends nothing, so printing it at 0.0% would pad a table
            # that is trying to be an account); they stay in the JSON so a
            # consumer can tell "empty this run" from "never a slot".
            regions = {k: v for k, v in
                       sorted(acc["regions"].items(), key=lambda kv: -kv[1])
                       if v}
            named = sum(regions.values())
            for name, tokens in regions.items():
                share = (100.0 * tokens / total) if total else 0.0
                lines.append(f"| {stage} | `{name}` | {tokens:,} | {share:.1f}% |")
            # the unattributed remainder is a ROW, never a rounding note: it
            # is the template prose the slots sit in plus every tokenizer
            # boundary, and hiding it would make the table sum by assertion.
            rest = total - named
            share = (100.0 * rest / total) if total else 0.0
            lines.append(f"| {stage} | *template prose + boundaries* | "
                         f"{rest:,} | {share:.1f}% |")
        lines.append("")
        lines.append("| stage | output tokens | sections |")
        lines.append("|---|---|---|")
        for stage, acc in sorted(row["stages"].items()):
            out = acc.get("output") or {}
            # ⛔ RENDER THE PUBLISHED KEYS — do not leave a derived key readerless.
            # `chars_sum` and `distinct_kinds` were both published in `_output_
            # structure` and neither was printed by ANYTHING, which is the same
            # "derived key with no reader" this module's own comment criticises
            # (measured, review cycle 7 P3 — the reviewer had to open the JSON to
            # see them). Printing the two stored halves is the reader: no third
            # derived number is created here (a `chars_sum / items` mean is the
            # consumer's to compute, and the render does not). `distinct_kinds`
            # is the UNION across a stage's calls, kept whole in `_merge_sections`
            # so it can never report the display cap instead of the count.
            parts = []
            for name, val in sorted((out.get("sections") or {}).items()):
                n = val.get("items", val.get("keys", 0))
                label = "items" if "items" in val else "keys"
                bits = [f"{n} {label}", f"{val.get('tokens', 0):,} tok"]
                if "chars_sum" in val:
                    bits.append(f"{val['chars_sum']:,} chars")
                if "distinct_kinds" in val:
                    bits.append(f"{val['distinct_kinds']} kinds")
                parts.append(f"{name}: " + ", ".join(bits))
            secs = "; ".join(parts)
            lines.append(f"| {stage} | {out.get('tokens', 0):,} | {secs or '—'} |")
        lines.append("")
    return "\n".join(lines)


def _render_regions() -> dict[str, dict[str, int]]:
    """The STATIC region sizes of each stage prompt, offline and exact.

    A region is a value the renderer SPLICES into the template; everything
    else in the prompt is the template's own prose (the instructions the
    stage is written in). Both are computed from the real render — the fill
    map is captured by wrapping ``_fill_template`` — so the numbers describe
    the prompt the pipeline sends, not a reconstruction of it.

    ⛔ No provider call is made: this is the render path only. It is the one
    part of the decomposition that is exact — the dynamic regions (the story
    and the re-serialized embed list) are solved from the measured evidence
    in ``decompose``.
    """
    from tortoise import extractor_v2 as X
    enc = _encoding()
    n = lambda s: len(enc.encode(s or ""))  # noqa: E731
    master = X.build_master_list()
    session_date = "2026-11-21T00:00:00+00:00"

    out: dict[str, dict[str, int]] = {}

    # S1 substitutes with .replace, so its two slots are counted directly.
    granularity = X._granularity_text(master)
    anchor_s1 = X._date_anchor(session_date)
    system_s1 = (X.S1_TMPL
                 .replace("{memory_granularity}", granularity)
                 .replace("{date_anchor}", anchor_s1))
    out["s1"] = {"memory_granularity": n(granularity),
                 "date_anchor": n(anchor_s1),
                 "template_prose": n(system_s1) - n(granularity) - n(anchor_s1)}

    captured: list[dict] = []
    orig_fill = X._fill_template

    def _fill(tmpl, values):
        rendered = orig_fill(tmpl, values)
        captured.append({"values": dict(values), "prompt": rendered})
        return rendered

    X._fill_template = _fill
    try:
        # Blank dynamic slots: this is what isolates the static part. The
        # story is a LABEL-ORDER SEED in the verbose render, so the static
        # vocabulary is the same with it empty (compact mode differs — that
        # is the lever `decompose` reports the size of, not a static here).
        X.render_s2_prompt(master, session_date=session_date, story="",
                           edus=None)
        X.render_s4_prompt("", {}, {}, master, session_date=session_date,
                           edus=None)
    finally:
        X._fill_template = orig_fill
    for stage, rec in zip(("s2", "s4"), captured, strict=False):
        regions = {name: n(text) for name, text in rec["values"].items()}
        regions["template_prose"] = (n(rec["prompt"])
                                     - sum(regions.values()))
        out[stage] = regions
    # The envelope a stage pays to frame the untrusted region, measured on a
    # one-character body so the body itself is not counted.
    out["envelope"] = {"clause": n(X._render_untrusted_block("S1 STORY", "x")) - 1}
    return out


def _source_transcript_tokens(doc_id: str | None) -> int:
    """Tokens of the SOURCE TRANSCRIPT block S2/S4 append for this document.

    ⛔ MEASURED, NOT ASSUMED. The block is rendered from the conversation's
    EDUs and omitted when they exceed `_SOURCE_TRANSCRIPT_CAP`, so whether a
    document carries one — and how big it is — is a property of THAT document,
    not of the corpus. `decompose` subtracts it per document before taking the
    medians, and publishes the count separately, because a residual solve that
    ignores it books verbatim source text into "S1's story".

    Returns 0 when the document cannot be read (a row whose `id` no longer
    resolves — the artefact still reproduces, and the row is then a documented
    floor rather than a silent subtraction of nothing).
    """
    if not doc_id:
        return 0
    path = _REPO_ROOT / doc_id
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    from tortoise import extractor_v2 as X
    edus = X._edus_from_conversation([{"role": "user", "content": text}])
    block = X._render_source_transcript(edus)
    return len(_encoding().encode(block)) if block else 0


#: ⛔ THE STAGES THIS ARITHMETIC IS WRITTEN FOR, and a REFUSAL if the corpus
#: gate grows past them. `_REQUIRED_STAGES` is the corpus gate's list; the
#: arithmetic below names its three stages (`c1`/`c2`/`c4`, the region table,
#: the `calls_per_doc` / `per_call` maps). Two literals that agree by
#: coincidence is exactly the subset-median failure this file keeps removing: a
#: fourth stage added to the GATE alone would decompose three of four and
#: publish a total that silently omits it. `decompose` compares the two and
#: refuses, so the omission can never be published.
_DECOMPOSED_STAGES: tuple[str, ...] = ("s1", "s2", "s4")

#: The static render slots each `regions` row charges, as (stage, slot) pairs.
#: The row's value is ``Σ calls[stage] × statics[stage][slot]``, so the table IS
#: the row — one declaration, and `_static_slot_problems` below derives the
#: coverage invariant from it instead of trusting a second hand-written list.
#: Row order is the bundle's own (§4) and is kept: `regions` is published sorted
#: by magnitude, and a stable sort ties on insertion order.
_STATIC_ROW_SLOTS: dict[str, tuple[tuple[str, str], ...]] = {
    "kind vocabulary (`master_list`)": (
        ("s2", "master_list"), ("s4", "master_list")),
    # ⛔ `pack_namespaces` is S2-only and renders EMPTY under the production
    # default (`core_only` off), so this row is 0 today and non-zero in a
    # `TORTOISE_CLASSIFY_LATER=1` shell — exactly the posture nobody was
    # charging (review notes at `371fd0a22e96`, P3).
    "pack namespace list (`pack_namespaces`, S2 only)": (
        ("s2", "pack_namespaces"),),
    "mapping rules (`anti_routine`)": (
        ("s2", "anti_routine"), ("s4", "anti_routine")),
    "output schema (`output_contract`)": (
        ("s2", "output_contract"), ("s4", "output_contract")),
    "stage instructions (template prose)": (
        ("s1", "template_prose"), ("s2", "template_prose"),
        ("s4", "template_prose")),
    "memory granularity (S1 only)": (("s1", "memory_granularity"),),
    "chains": (("s2", "chains_text"), ("s4", "chains_text")),
    "date anchor": (("s1", "date_anchor"), ("s2", "date_anchor"),
                    ("s4", "date_anchor")),
    "graph search results (S4)": (("s4", "search_results"),),
}

#: The render slots NO `regions` row charges, each with what it is and why it is
#: not chargeable. These are NOT a silent residual — they are a DECLARED one, and
#: `decompose` refuses a render slot that is in neither this map nor
#: `_STATIC_ROW_SLOTS`, and refuses an entry here whose slot the render no longer
#: fills — so the exemptions cannot rot silently either (review notes at
#: `0c06906012f6`, B).
_UNCHARGED_STATIC_SLOTS: dict[str, dict[str, str]] = {
    "s4": {
        #: 1 token/call in the committed posture. The row that would charge the
        #: REAL list is `**S2's embed list**`, solved as
        #: `pc4 − s4_sys − story − clause` — i.e. NET of this empty `{}`
        #: render, so charging this token here as well would need the solve
        #: changed in lockstep. Left in `unattributed` (≈4 tokens/doc, 0.009 %)
        #: rather than perturbing a published bundle for 0.009 %.
        "embed_list_json": (
            "the EMPTY list render (`{}`, 1 token/call); the real list is the "
            "solved embed row, which subtracts this render"),
        #: 0 tokens/call: a blank body renders as the EMPTY STRING (#10311
        #: envelope measured separately), so the static render carries no story.
        "story": (
            "blank in the static render (0 tokens/call); the real story body "
            "is solved and charged on the S1-story row"),
    },
}


def _static_slot_problems(statics: dict[str, dict[str, int]]) -> list[str]:
    """Every fill slot is charged by EXACTLY ONE row, or declared exempt.

    DERIVED from ``statics`` — the real render — never from a second
    hand-written list of names. One defect per line, empty when the table covers
    the render. Five ways this can go wrong, all of them silent before this
    check existed (`0c06906012f6` review note B):

    * a slot the render fills that NO row charges — its tokens leave the
      composition and land in `unattributed` while the table still sums
      (the `pack_namespaces` shape, `371fd0a22e96` P3);
    * a slot charged by TWO rows — double counted, the opposite error;
    * a slot a row charges that the render does not fill — a typo, which
      `.get(slot, 0)` would otherwise charge as a silent zero;
    * a slot both charged and declared exempt — the two lists disagree;
    * an exemption the render no longer fills — a stale exemption, which would
      re-open the first hole for whatever takes its name.
    """
    charged: dict[tuple[str, str], list[str]] = {}
    for label, slots in _STATIC_ROW_SLOTS.items():
        for pair in slots:
            charged.setdefault(pair, []).append(label)
    exempt = {(stage, slot) for stage, slots in _UNCHARGED_STATIC_SLOTS.items()
              for slot in slots}
    problems: list[str] = []
    for pair in sorted(charged):
        stage, slot = pair
        labels = charged[pair]
        if slot not in statics.get(stage, {}):
            problems.append(
                f"`{stage}.{slot}` is charged by {labels} but the render fills "
                f"no such slot — a typo would have charged 0 silently")
        if len(labels) > 1:
            problems.append(
                f"`{stage}.{slot}` is charged by more than one row {labels} — "
                f"double counted")
        if pair in exempt:
            problems.append(
                f"`{stage}.{slot}` is BOTH charged and declared exempt")
    for stage, slot in sorted(exempt):
        if slot not in statics.get(stage, {}):
            problems.append(
                f"`{stage}.{slot}` is declared exempt but the render fills no "
                f"such slot — stale exemption")
    for stage in _DECOMPOSED_STAGES:
        for slot, tokens in sorted(statics.get(stage, {}).items()):
            if (stage, slot) not in charged and (stage, slot) not in exempt:
                problems.append(
                    f"`{stage}.{slot}` ({tokens} tokens/call) is filled by the "
                    f"render but charged by NO row — its tokens would leave "
                    f"the composition into `unattributed`")
    return problems


def _assert_static_slots_are_covered(
        statics: dict[str, dict[str, int]]) -> None:
    """Fail closed, naming every uncovered slot — before any `--out` write."""
    problems = _static_slot_problems(statics)
    if problems:
        raise SystemExit(
            "refusing to decompose: the stage prompts' static regions do not "
            "reconcile with `regions`.\n  " + "\n  ".join(problems) +
            "\nAdd the slot to `_STATIC_ROW_SLOTS` (a row that charges it), or "
            "declare it in `_UNCHARGED_STATIC_SLOTS` with what it is and why "
            "it is not chargeable.")


def decompose(rows: list[dict], *, arm: str = "cold",
              receipts: Path | None = None) -> dict:
    """Attribute the two fitted constants of #14295 to the regions that make
    them (#14343).


    Every number here is either (a) tiktoken on a real render of the stage
    prompts (the statics), or (b) a measured token count from the evidence
    rows (the per-call inputs, the emissions, the call counts). Nothing is
    assumed; the residual rows are what keeps the table honest.
    """
    #: ⛔ ONE STAGE LIST, or a refusal. A stage added to the corpus gate alone
    #: would be gated but never decomposed (see `_DECOMPOSED_STAGES`).
    if tuple(_REQUIRED_STAGES) != _DECOMPOSED_STAGES:
        raise SystemExit(
            f"refusing to decompose: the corpus gate requires "
            f"{list(_REQUIRED_STAGES)} but this arithmetic is written for "
            f"{list(_DECOMPOSED_STAGES)} — extend the per-stage terms (and "
            f"`_STATIC_ROW_SLOTS`) before adding a stage to the gate, or the "
            f"total would silently omit it.")
    docs = [r for r in rows
            if r.get("arm") == arm and r.get("kind") == "doc"
            and not r.get("failed") and r.get("stages")]
    if not docs:
        raise SystemExit(f"no usable `{arm}` document rows in the evidence")
    #: ⛔ A PARTIAL CORPUS IS NOT A DECOMPOSITION (review, P1). `compose` refuses
    #: an incomplete RUN through `_row_complete` + `_REQUIRED_STAGES`; this half
    #: had no such gate, and a corpus where only SOME documents measured a stage
    #: still published `documents: len(docs)` while every median for that stage
    #: was taken over the subset that carried it. The residual then COLLAPSED
    #: toward zero and the bundle's headline ("fit and composition agree to
    #: 6.6%") flattered itself — no marker, exit 0, both files written. Measured
    #: by dropping S4 from 10 of the 50 committed rows: the embed region moved
    #: 14,149 → 16,695 and the residual 2,994 → 71.5. The committed corpus is
    #: complete (all 50 rows carry s1/s2/s4 with calls), so this refuses a state
    #: the tool can be handed, not the one it is handed today — the shape a
    #: per-document `S4 failed` on a call that never recorded leaves behind.
    partial = {str(r.get("id") or f"row{i}"): missing
               for i, r in enumerate(docs)
               if (missing := _stages_without_a_measured_call(r))}
    if partial:
        per_stage = {s: sum(1 for r in docs
                            if (r["stages"].get(s) or {}).get("calls"))
                     for s in _REQUIRED_STAGES}
        raise SystemExit(
            f"refusing to decompose a PARTIAL corpus: {len(partial)} of "
            f"{len(docs)} `{arm}` document row(s) have no measured call for a "
            f"required stage ({partial}) — the medians would be taken over a "
            f"subset while `documents` counted the whole corpus, and the "
            f"residual would collapse toward zero. Per-stage document counts: "
            f"{per_stage}.")
    statics = _render_regions()
    #: ⛔ COVERAGE IS DERIVED, NOT ASSERTED IN PROSE. Before the rows are built:
    #: every slot the REAL render fills must be charged by exactly one row, or be
    #: a declared exemption. Refuses here, before `--out` is ever opened.
    _assert_static_slots_are_covered(statics)
    #: ⛔ THE SOURCE-TRANSCRIPT TAIL IS SUBTRACTED, NOT IGNORED. `render_s2_prompt`
    #: / `render_s4_prompt` append `_render_source_transcript(edus)` OUTSIDE
    #: `_fill_template` whenever the document's EDU text fits
    #: `_SOURCE_TRANSCRIPT_CAP` (8,000 chars), and `_render_regions` renders with
    #: `edus=None` — so the residual solve `story = pc2 − s2_sys − clause` booked
    #: that block into "S1's story, re-sent to S2 and S4". Same class as the
    #: `compose_one` misattribution (review cycle 8, P2), and it is measured per
    #: document here: 9 of the 50 corpus documents carry a tail.
    tails: dict[str, int] = {r.get("id") or "": _source_transcript_tokens(
        r.get("id")) for r in docs}

    def _per_call(stage: str) -> tuple[float, float, float]:
        """(calls/doc, prompt tokens per call, completion tokens per call).

        MEDIANS, not means: the longest document (127,914 tokens, 9.3% of the
        corpus's prompt tokens but 29.7% of its DOCUMENT tokens) is sent twice
        and carries more prompt tokens than 90 documents its size, so it drags
        every mean per-call figure upward — the mean S1 call is 11,673 tokens
        against a median of 7,138. A mean would describe that document's
        leverage; the issue is about the median document.
        """
        calls, pc, oc = [], [], []
        for r in docs:
            s = r["stages"].get(stage)
            if not s or not s.get("calls"):
                continue
            calls.append(s["calls"])
            # the tail rides on EVERY S2/S4 call for a document that carries one
            tail = tails.get(r.get("id") or "", 0) if stage in ("s2", "s4") else 0
            pc.append((s["prompt_tokens"] / s["calls"]) - tail)
            oc.append(s["completion_tokens"] / s["calls"])
        return (sum(calls) / len(calls), statistics.median(pc),
                statistics.median(oc))

    c1, pc1, oc1 = _per_call("s1")
    c2, pc2, oc2 = _per_call("s2")
    c4, pc4, oc4 = _per_call("s4")
    t_med = statistics.median([r["item_tokens"] for r in docs])

    s2_sys = sum(statics["s2"].values())
    s4_sys = sum(statics["s4"].values())
    clause = statics["envelope"]["clause"]
    #: how often S1 makes a SECOND call for the same document (the narrative
    #: cap is hit, so the escalation re-sends the identical prompt). ONE
    #: predicate, counted once: `document_term` publishes the COUNT and
    #: `levers_offline` the SHARE, and a second `sum(...)` could drift from it.
    n_two_call = sum(1 for r in docs
                     if (r["stages"].get("s1") or {}).get("calls", 0) > 1)
    two_call_share = n_two_call / len(docs)

    # The two dynamic regions, solved from the measured per-call prompts. The
    # story is S1's own emission seen by the next stage; the embed list is
    # S2's emission re-serialized into S4's prompt.
    # ⛔ BOTH STAGES PAY THE #10311 ENVELOPE, and it is charged to the STORY
    # row, not left inside `embed` (review cycle 7, P2). S2's story is a USER
    # message (`_render_untrusted_block("S1 STORY", story)`), and S4's story
    # slot is wrapped by the same call inside its template. The statics above
    # render with `story=""`, and an empty body renders as the EMPTY STRING
    # (`_render_untrusted_block("S1 STORY", "")` is 0 tokens, measured), so
    # `s4_sys` contains NO story envelope — while a real S4 prompt carries
    # envelope+body. Subtracting only the body therefore dumped each S4 call's
    # 93-token envelope into `**S2's embed list**`, ~106 tokens/doc on the
    # bundle's headline row.
    #
    # The two rows therefore get: the body from both stages, and the envelope
    # from both stages. S2's own envelope was previously in the UNATTRIBUTED
    # residual (it is known, so it does not belong there) — hence the
    # composition total rises by (c2 + c4) x clause and the residual falls by
    # the same, which is what makes §1's fit-vs-composition agreement tighter.
    story = max(0.0, pc2 - s2_sys - clause)
    embed = max(0.0, pc4 - s4_sys - story - clause)
    #: the median per-call tail (0 for a transcript-free corpus — which the nine
    #: affected documents above are not; see `source_transcript` in the result).
    tail_med = statistics.median(list(tails.values())) if tails else 0

    #: ⛔ THE ROWS ARE THE TABLE. `_STATIC_ROW_SLOTS` declares which (stage, slot)
    #: pairs each row charges and this comprehension is the only place the value
    #: is computed, so a row cannot silently stop charging a slot it names — or
    #: charge one the render does not fill (`_assert_static_slots_are_covered`
    #: has already refused both). The two solved rows below are the ones that are
    #: NOT a sum of static slots; they stay explicit because the provenance of
    #: their number is a solve, not a render.
    calls_by_stage = {"s1": c1, "s2": c2, "s4": c4}
    regions = {
        label: sum(calls_by_stage[stage] * statics[stage].get(slot, 0)
                   for stage, slot in slots)
        for label, slots in _STATIC_ROW_SLOTS.items()
    }
    regions["**S1's story, re-sent to S2 and S4**"] = (c2 + c4) * (story + clause)
    regions["**S2's embed list, re-sent to S4**"] = c4 * embed
    total = sum(regions.values())
    # ⛔ DERIVED, not hardcoded: the constant is the intercept of THIS arm's
    # per-document fit. A literal 45_503 is right for `cold` and wrong for any
    # other arm — the bundle's own completion recipe runs `--arm after`, where a
    # literal would report the BEFORE intercept as the AFTER one and absorb the
    # lever's whole saving into "unattributed".
    in_fit = _fit([(r["item_tokens"], r["prompt_tokens"]) for r in docs])
    fitted = round(in_fit["intercept"])
    out_fit = _fit([(r["item_tokens"], r["completion_tokens"]) for r in docs])
    composition_out = c1 * oc1 + c2 * oc2 + c4 * oc4
    return {
        "arm": arm,
        "documents": len(docs),
        "median_doc_tokens": t_med,
        "calls_per_doc": {"s1": round(c1, 3), "s2": round(c2, 3),
                          "s4": round(c4, 3),
                          "total": round(c1 + c2 + c4, 3)},
        "per_call": {"s1": {"prompt": pc1, "completion": oc1},
                     "s2": {"prompt": pc2, "completion": oc2},
                     "s4": {"prompt": pc4, "completion": oc4}},
        #: ⛔ TWO estimators, both reported, because they answer different
        #: questions and differ by 22% here. The COMPOSITION is per-call MEDIANS
        #: times the measured call counts (the median document's shape); the
        #: FITTED constant is the intercept of the arm's own per-document fit,
        #: and output's fit is nearly flat (R²≈0.02) so its intercept sits near
        #: the MEAN output per document. The mean is above the median because
        #: S4's output is bimodal (see `output.reemission_tail`), which is a
        #: finding, not a discrepancy to smooth away.
        "output": {
            "s1_tokens_per_doc": round(c1 * oc1, 1),
            "s2_tokens_per_doc": round(c2 * oc2, 1),
            "s4_tokens_per_doc": round(c4 * oc4, 1),
            "total_per_doc": round(composition_out, 1),
            "s2_share": round(100.0 * c2 * oc2 / max(1e-9, composition_out), 1),
            "fitted_constant": round(out_fit["intercept"]),
            "fitted_slope": round(out_fit["slope"], 3),
            "fitted_r2": round(out_fit["r2"], 3),
            "unattributed": round(out_fit["intercept"] - composition_out, 1),
            "mean_per_doc": round(sum(r["completion_tokens"] for r in docs)
                                  / len(docs), 1),
            "s2_share_of_fitted": round(100.0 * c2 * oc2
                                        / max(1e-9, out_fit["intercept"]), 1),
            "reemission_tail": _output_tail(docs),
        },
        "statics": statics,
        "source_transcript": {
            "documents": len(tails),
            "documents_with_a_tail": sum(1 for v in tails.values() if v),
            "tokens_per_call_median": tail_med,
            "tokens_per_call_max": max(tails.values()) if tails else 0,
        },
        "solved": {"story_tokens": round(story, 1),
                   "embed_list_tokens": round(embed, 1),
                   "embed_over_emission": round(embed / oc2, 2) if oc2 else None},
        "regions": {k: round(v, 1) for k, v in
                    sorted(regions.items(), key=lambda kv: -kv[1])},
        "composition_total": round(total, 1),
        "fitted_constant": fitted,
        "unattributed": round(fitted - total, 1),
        #: ⛔ BOTH terms are DERIVED from this arm's own fit. The intercept fix
        #: was half-applied in the first revision: `fitted` came from `_fit()`
        #: while the slope was a literal `2.159` — so the bundle's own
        #: completion recipe (`--arm after`) printed the BEFORE slope as the
        #: AFTER arm's, which is the same mislabel the intercept fix removed.
        "input_fit": {"slope": round(in_fit["slope"], 3),
                      "intercept": fitted,
                      "r2": round(in_fit["r2"], 3)},
        "document_term": {
            "s1_calls_x_median_doc": round(c1 * t_med, 1),
            "fitted_slope_x_median_doc": round(in_fit["slope"] * t_med, 1),
            "documents": len(docs),
            "documents_taking_two_s1_calls": n_two_call,
            "note": ("S1 sends the WHOLE document once per call, and a "
                     "majority of documents take a second S1 call, so the "
                     "document is paid for ~2x"),
        },
        "levers_offline": _offline_levers(statics, c1, c2, c4, t_med,
                                          two_call_share, oc2,
                                          n_two_call=n_two_call),
        "serialization": serialization_lever(receipts) if receipts else {},
    }


def _output_tail(docs: list[dict]) -> dict:
    """S4's output distribution: the delta contract's compliance tail.

    A median/mean gap in S4's completion tokens is not noise — `#1789` measured
    the delta contract at −93.8 % on 26-turn sessions, and on a DOCUMENT corpus
    a quarter of documents still emit a full list. Reported as a distribution
    because a mean alone would hide it and a median alone would deny it.
    """
    per_call = sorted(
        (r["stages"]["s4"]["completion_tokens"] / r["stages"]["s4"]["calls"])
        for r in docs
        if (r["stages"].get("s4") or {}).get("calls"))
    if not per_call:
        return {}
    #: ⛔ These are DOCUMENTS, not calls: each value is that document's
    #: `completion_tokens / calls`, so one entry per document. The first
    #: revision called them "S4 calls", which overstated the denominator by the
    #: call ratio (≈1.14 here) in the JSON, the rendered markdown and the README.
    return {"s4_docs_over_6000": sum(1 for v in per_call if v > 6000),
            "s4_docs": len(per_call),
            "share_docs_over_6000_pct": round(
                100.0 * sum(1 for v in per_call if v > 6000) / len(per_call), 1),
            "s4_max_per_doc_per_call": round(max(per_call)),
            "s4_median_per_doc_per_call": round(statistics.median(per_call))}


def _offline_levers(statics: dict, c1: float, c2: float, c4: float,
                    t_med: float, two_call_share: float, oc2: float,
                    *, n_two_call: int) -> dict:
    """The size of each candidate lever, measured WITHOUT a provider call.

    A lever that cannot be sized cannot be chosen between, and a lever's size
    is a property of the prompt — not of the model's answer. What this cannot
    do is establish a lever's EFFECT ON QUALITY, which only a run of the
    capstone corpus can; every entry below therefore carries that limit.
    """
    from tortoise import extractor_v2 as X
    enc = _encoding()
    n = lambda s: len(enc.encode(s or ""))  # noqa: E731
    master = X.build_master_list()
    out: dict = {}
    try:
        compact = X._render_master_compact(master, "")
        verbose = X._render_master(master, "")
        out["compact_master_render"] = {
            "verbose_tokens": n(verbose),
            "compact_tokens": n(compact),
            "saved_per_doc": round((c2 + c4) * (n(verbose) - n(compact)), 1),
            "quality_risk": ("compact mode story-filters the pack kinds, so "
                             "a document that misses every trigger word drops "
                             "to the all-packs fallback and a document that "
                             "hits one drops the others — not "
                             "quality-neutral"),
        }
    except Exception as exc:
        # the lever's absence is a RESULT, not a crash: report it as one
        out["compact_master_render"] = {"error": f"{type(exc).__name__}: {exc}"}
    out["s1_second_send"] = {
        "cause": (f"S1's narrative cap is {X._S1_MAX_TOKENS} tokens; a story "
                  "that hits it escalates and re-sends the SAME prompt, so "
                  "the document is paid for twice"),
        # ⛔ COUNT AND SHARE ARE SEPARATE, CORRECTLY-NAMED KEYS (review cycle
        # 11, P3). This was `documents_with_two_s1_calls: round(100.0 * share,
        # 1)` — a PERCENTAGE under a COUNT-shaped name, published in the same
        # artifact as `document_term.documents_taking_two_s1_calls`, which is
        # the count of the same quantity. So the bundle asserted that 68 of a
        # 50-document corpus took two S1 calls.
        "documents_taking_two_s1_calls": n_two_call,
        "share_pct": round(100.0 * two_call_share, 1),
        "saved_per_doc_if_one_call": round(
            two_call_share * (t_med + sum(statics["s1"].values())), 1),
        "quality_risk": ("a longer narrative is more content, not less — but "
                         "it also enlarges the story both later stages "
                         "re-read, so the net is a measurement, not "
                         "arithmetic; the story's own growth is NOT "
                         "sizeable offline"),
    }
    return out


def serialization_lever(receipts: Path) -> dict:
    """#14343: the `indent=1` tax on S4's embed-list region, measured on REAL
    emissions.

    The one place in the pipeline where a token count can be cut with the
    JSON's CONTENT unchanged: S4's prompt re-serializes S2's list, and
    ``indent=1`` writes a newline + a space per key/line. The write-path
    receipts (`tests/eval/write_path/receipts/*/*.arms.json`) carry the real
    S2 emissions, so both serializations are computed on the same data — this
    is a measurement, not an estimate from a synthetic list.
    """
    enc = _encoding()
    rows = []
    for path in sorted(receipts.glob("*.arms.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for arm, embed in (rec.get("embed_list") or {}).items():
            if not isinstance(embed, dict):
                continue
            items = sum(len(v) for v in embed.values() if isinstance(v, list))
            if not items:
                continue
            indented = len(enc.encode(json.dumps(embed, indent=1)))
            compact = len(enc.encode(json.dumps(embed, separators=(",", ":"))))
            stats = (rec.get("s2_stats") or {}).get(arm) or {}
            rows.append({"session": path.name.split(".")[0], "arm": arm,
                         "items": items,
                         "indented_tokens": indented,
                         "compact_tokens": compact,
                         "emission_tokens": stats.get("completion_tokens"),
                         "story_tokens": len(enc.encode(
                             (rec.get("stories") or {}).get(arm) or ""))})
    if not rows:
        return {}
    ti = sum(r["indented_tokens"] for r in rows)
    tc = sum(r["compact_tokens"] for r in rows)
    emissions = [r["emission_tokens"] for r in rows if r["emission_tokens"]]
    return {
        "receipts": len({r["session"] for r in rows}),
        "emissions": len(rows),
        "items": sum(r["items"] for r in rows),
        "indented_tokens": ti,
        "compact_tokens": tc,
        "saved_tokens": ti - tc,
        "saved_pct": round(100.0 * (ti - tc) / ti, 1) if ti else None,
        "saved_per_emission": round((ti - tc) / len(rows)),
        "tokens_per_item": (round(sum(emissions) / sum(r["items"] for r in rows), 1)
                            if emissions else None),
        "rows": rows,
    }


def render_serialization_lever(lev: dict) -> str:
    if not lev:
        return ""
    lines = ["### The one information-neutral lever: `indent=1` on S4's "
             "embed-list region", ""]
    lines.append(
        f"Measured on **{lev['receipts']} real sessions / {lev['emissions']} "
        f"emissions / {lev['items']} items** from "
        f"`tests/eval/write_path/receipts/`: the list S4 re-reads costs "
        f"**{lev['indented_tokens']:,}** tokens as today's `indent=1` dump and "
        f"**{lev['compact_tokens']:,}** with compact separators — "
        f"**{lev['saved_tokens']:,} tokens ({lev['saved_pct']}%) that are pure "
        f"whitespace**, {lev['saved_per_emission']:,} per emission. "
        f"S2's own emission is {lev['tokens_per_item']} completion tokens per "
        f"item, so the re-serialized copy is ~1.3x the emission it copies.")
    lines.append("")
    lines.append("| session | arm | items | emission | `indent=1` | compact | "
                 "saved |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in lev["rows"]:
        lines.append(f"| {r['session']} | {r['arm']} | {r['items']} | "
                     f"{r['emission_tokens'] or '—'} | {r['indented_tokens']:,} | "
                     f"{r['compact_tokens']:,} | "
                     f"{r['indented_tokens'] - r['compact_tokens']:,} |")
    lines.append("")
    return "\n".join(lines)


# ── #14371: the same lever, measured OFFLINE — no provider key, no run ──
#
# `decompose` already computes the serialization ratio, but as ONE section of a
# report whose other sections read the #14295 corpus, and the per-document
# figure was published only as prose in `docs/evidence/14343-amplify/`. The
# token half of the lever is arithmetic — `json.dumps(..., indent=1)` versus
# compact separators over the SAME objects — so it must be reproducible on a box
# with NO provider key. That is what this verb is: one command, the receipts and
# the corpus, no network beyond tiktoken's own local BPE cache.
#
# ⛔ It measures the TOKEN half ONLY. What the layout does to what the model
# EMITS is the `#7949` capstone's question and is not answered here; the verb
# says so in its own output rather than leaving a reader to infer a green bar.

#: The token allowances `#14275` CONFIRMED as a cost level. ⛔ They are INPUTS,
#: not outputs: this verb reports what a saving buys AT each level and never
#: derives, prices, or proposes one — the level is the owner's (`#14275`).
#: Counts are FLOORS via `affordable_count`, the repo's one capacity convention.
_ALLOWANCES: tuple[tuple[str, int], ...] = (
    ("solo", 3_000_000), ("pro", 4_000_000), ("team", 24_000_000))

#: The ambient switches that move the STATIC render the embed region is SOLVED
#: against (`docs/evidence/14343-amplify/README.md`'s reproduce block; all five
#: are pinned by `tests/test_extractor_cost_measure.py`). A measurement taken
#: with any of them set is a DIFFERENT decomposition, so `embed_json_measurement`
#: refuses instead of publishing a number that is not comparable to the recorded
#: 12,412 — the false pass this guard exists to prevent.
_POSTURE_SWITCHES: tuple[str, ...] = (
    "TORTOISE_S4_DELTA", "TORTOISE_EXTRACTOR_PROMPT",
    "TORTOISE_CLASSIFY_LATER", "TORTOISE_REFERENTIALITY")

#: A PATH knob rather than a flag, which is why reading the flag list missed it.
#: It is production-equivalent only when it resolves to the in-tree `packs/`.
_POSTURE_PACKS_DIR = "TORTOISE_PACKS_DIR"


def _posture_problems() -> list[str]:
    """Ambient switches that would move the render this solve is written for.

    ⛔ GRADED BY RESOLUTION, through the PIPELINE'S OWN predicates — never by
    "the variable is set". The two failures that symmetry avoids are opposite
    and both silent:

    * a FALSE BLOCK — `TORTOISE_S4_DELTA=1`, `TORTOISE_S4_DELTA=garbage`
      (which resolves to the shipped default), `TORTOISE_EXTRACTOR_PROMPT=
      verbose`, and `TORTOISE_PACKS_DIR` aimed at the in-tree `packs/` all
      render EXACTLY the recorded prompt, so refusing them would refuse a
      correct measurement. Measured in this repo's own suite: `tests/conftest.py`
      exports `TORTOISE_S4_DELTA=0` for determinism, and a presence test read
      that as a broken posture on every run.
    * a FALSE PASS — `TORTOISE_S4_DELTA=0` (the legacy complete-list contract)
      and `=no`/`=off` DO move the S4 template render, so a presence-with-any-
      value test would wave through a different decomposition.

    One normalization rule, borrowed from the code that consumes the value, so
    this guard cannot disagree with the render it is guarding.
    """
    from tortoise import extractor_v2 as X
    from tortoise import referentiality

    out: list[str] = []
    if not X._s4_delta_enabled():
        out.append("`TORTOISE_S4_DELTA=<falsy>` (the legacy complete-list "
                   "contract — the S4 template render differs; unset it, "
                   "which is the shipped delta default)")
    if X._classify_later_enabled():
        out.append("`TORTOISE_CLASSIFY_LATER` armed (S2/S4 switch to the "
                   "core-only master render)")
    if (os.environ.get("TORTOISE_EXTRACTOR_PROMPT", "").strip()
            == "compact"):
        out.append("`TORTOISE_EXTRACTOR_PROMPT=compact` (a different master "
                   "render)")
    if referentiality.enabled():
        out.append("`TORTOISE_REFERENTIALITY` armed (moves `output_contract` "
                   "and the S1 story)")
    packs = (os.environ.get(_POSTURE_PACKS_DIR) or "").strip()
    if packs:
        try:
            resolved = Path(packs).expanduser().resolve()
        except OSError:  # pragma: no cover - unreadable path is still a value
            resolved = Path(packs)
        if resolved != (_REPO_ROOT / "packs").resolve():
            out.append(f"`{_POSTURE_PACKS_DIR}={packs}` (resolves outside the "
                       "in-tree `packs/`, so the kind vocabulary differs)")
    return out


def _bpe_cache_files() -> set[Path]:
    """tiktoken's on-disk BPE cache — the OFFLINE PRECONDITION, observable.

    `tiktoken.get_encoding("cl100k_base")` fetches ~1.6 MB of BPE ranks over the
    network on a COLD cache (`openaipublic.blob.core.windows.net`), and reuses
    the copy on disk afterwards. The verb reports whether a fetch happened
    during the measurement, so "reproducible with no network" is a MEASUREMENT
    rather than a claim: the run that produced the recorded cells fetched
    nothing.
    """
    import tempfile
    base = (os.environ.get("TIKTOKEN_CACHE_DIR")
            or str(Path(tempfile.gettempdir()) / "data-gym-cache"))
    try:
        return {p for p in Path(base).iterdir() if p.is_file()}
    except OSError:
        return set()


def embed_json_measurement(receipts: Path, evidence: Path, *,
                           arm: str = "cold") -> dict:
    """#14371: tokens before → after for the S4 embed-list layout, OFFLINE.

    Two independent sources, and the arithmetic between them is printed rather
    than left implicit:

    * the RECEIPTS carry the real S2 emissions, so both serializations are
      counted on the same objects (`serialization_lever`, the instrument
      `#14343` used — not a second harness);
    * the CORPUS (`#14295`'s `measurements.jsonl`) supplies the call structure
      (S4 calls/document) and the median document's in/out columns, and
      `decompose` SOLVES the re-sent embed region per S4 call.

    No provider call is made and no key is read. `decompose` re-renders the
    static prompt regions, which is why a non-production ambient posture is
    refused: the solved region is only comparable to the recorded figure under
    the posture the corpus was scored in.
    """
    problems = _posture_problems()
    if problems:
        print(
            "refusing to measure: the ambient extractor posture is not the "
            "production one this corpus was scored under — "
            + ", ".join(problems)
            + ". Each of these moves the static render the embed region is "
            "SOLVED against, so the per-document figure would not be "
            "comparable to the recorded 12,412. Unset them and re-run "
            "(`docs/evidence/14343-amplify/README.md`, reproduce block).",
            file=sys.stderr)
        raise SystemExit(2)

    cache_before = _bpe_cache_files()
    lev = serialization_lever(receipts)
    if not lev:
        raise SystemExit(
            f"no usable write-path receipts in {receipts} — the verb's entire "
            "subject is the real S2 emissions they carry")
    rows = load_rows(evidence)
    dec = decompose(rows, arm=arm, receipts=None)
    costs = (report_from_rows(rows).get("costs") or {})
    median_row = (costs.get("rows") or [None])[0]
    if not median_row:
        raise SystemExit(f"no usable `{arm}` median document in {evidence}")
    fetched = _bpe_cache_files() - cache_before

    #: ⛔ EXACT ratio, not the rounded 32.5: the published bundle applies the
    #: rounded share and prints 4,598, this applies the exact one and prints
    #: 4,600. Same quantity, and the evidence file states the 2-token gap so a
    #: reader comparing the two does not read one of them as rot.
    ratio = lev["saved_tokens"] / lev["indented_tokens"]
    region_per_call = float(dec["solved"]["embed_list_tokens"])
    s4_calls = float(dec["calls_per_doc"]["s4"])
    region_per_doc = region_per_call * s4_calls
    saved_per_call = ratio * region_per_call
    saved_per_doc = ratio * region_per_doc
    before_in = float(median_row["measured_in"])
    before_out = float(median_row["measured_out"])
    before = before_in + before_out
    after = before - saved_per_doc
    buys = [{"tier": tier, "allowance": allowance,
             "documents_before": affordable_count(allowance, before),
             "documents_after": affordable_count(allowance, after)}
            for tier, allowance in _ALLOWANCES]
    shares = [(r["indented_tokens"] - r["compact_tokens"]) / r["indented_tokens"]
              for r in lev["rows"] if r["indented_tokens"]]
    return {
        "lever": "TORTOISE_S4_EMBED_JSON",
        "offline": True,
        "provider_calls": 0,
        "tokenizer": "cl100k_base",
        "encoder_cache": {
            "cache_dir": (os.environ.get("TIKTOKEN_CACHE_DIR")
                          or "<tmp>/data-gym-cache"),
            "fetched_during_measurement": sorted(str(p) for p in fetched),
            "network_used": bool(fetched),
        },
        "posture": "production (no ambient extractor switch set)",
        "receipts": str(receipts),
        "evidence": str(evidence),
        "arm": arm,
        #: the corpus's call structure and medians — the inputs to the solve
        "corpus": {
            "documents": dec["documents"],
            "median_doc_tokens": dec["median_doc_tokens"],
            "s4_calls_per_doc": s4_calls,
            "median_document_in": before_in,
            "median_document_out": before_out,
            "median_document_total": before,
        },
        "per_emission": lev["rows"],
        "aggregate": {
            "receipts": lev["receipts"], "emissions": lev["emissions"],
            "items": lev["items"],
            "indented_tokens": lev["indented_tokens"],
            "compact_tokens": lev["compact_tokens"],
            "saved_tokens": lev["saved_tokens"],
            "saved_pct": lev["saved_pct"],
            "saved_ratio_exact": ratio,
            "saved_per_emission": lev["saved_per_emission"],
            "tokens_per_item": lev["tokens_per_item"],
            "per_emission_share_min_pct": round(100.0 * min(shares), 2),
            "per_emission_share_max_pct": round(100.0 * max(shares), 2),
        },
        "per_document": {
            "embed_region_per_s4_call": region_per_call,
            "s4_calls_per_doc": s4_calls,
            "embed_region_per_doc": round(region_per_doc, 1),
            "saved_per_call": round(saved_per_call, 1),
            "saved_per_doc": round(saved_per_doc, 1),
            "before_in": before_in, "before_out": before_out,
            "before_total": before,
            "after_in": round(before_in - saved_per_doc, 1),
            "after_total": after,
            "saved_pct": round(100.0 * saved_per_doc / before, 2),
        },
        "documents_per_month": buys,
        "not_delivered": (
            "the #7949 quality re-run (E1 volume / E2 correctness / E3 "
            "provenance / E4 operator edges): what the layout does to what "
            "the model EMITS is unmeasured here, so the lever stays DISARMED"),
    }


def render_embed_json_measurement(m: dict) -> str:
    """The measurement as a document: cells, then the arithmetic behind them."""
    import datetime
    lev, agg = m["per_document"], m["aggregate"]
    corpus = m["corpus"]
    lines = [
        "---",
        'title: "#14371 — the S4 embed-list saving, measured offline"',
        "type: evidence",
        "domain: platform",
        "doc_status: final",
        # ⛔ GENERATED FILE, and the frontmatter says so: a reader must not
        # hand-edit a measurement (the test that pins these cells would then
        # pin prose). `created` is the REGENERATION date for that reason.
        f"created: {datetime.date.today().isoformat()}",
        "subjects.team: epistemic-team",
        'aboutSubjects: "extractor v2 S4 prompt embed-list serialization; '
        'TORTOISE_S4_EMBED_JSON"',
        'aboutObjects: "#14371, #14343, #14365, #14295, #14277, #14275, #7949"',
        "---",
        "",
        "<!-- GENERATED by `tools/extractor_cost_measure.py embed-json`. Do not "
        "hand-edit: regenerate with the command at the foot of this file, or "
        "the pinned cells red against the run they no longer describe. -->",
        "",
        "## The S4 embed-list saving, measured offline (#14371)", "",
        "⛔ **OFFLINE — zero provider calls, zero spend, no key read.** Both "
        f"layouts are `json.dumps` over the SAME objects, counted with "
        f"`{m['tokenizer']}`; the encoder cache was "
        + ("**FETCHED during this run** (network used)"
           if m["encoder_cache"]["network_used"]
           else "**warm — no fetch, no network**") + ".", "",
        f"Corpus: `{m['receipts']}` ({agg['receipts']} sessions / "
        f"{agg['emissions']} emissions / {agg['items']:,} items). Vertex: "
        f"`{m['evidence']}` arm `{m['arm']}`.", "",
        "### The tokens the lever removes, per emission", "",
        "| session | arm | items | `indent=1` | compact | saved | saved % |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in m["per_emission"]:
        saved = r["indented_tokens"] - r["compact_tokens"]
        share = 100.0 * saved / r["indented_tokens"] if r["indented_tokens"] else 0.0
        lines.append(f"| {r['session']} | {r['arm']} | {r['items']} | "
                     f"{r['indented_tokens']:,} | {r['compact_tokens']:,} | "
                     f"{saved:,} | {share:.1f} |")
    lines += [
        f"| **total** | {agg['emissions']} emissions | {agg['items']:,} | "
        f"**{agg['indented_tokens']:,}** | **{agg['compact_tokens']:,}** | "
        f"**{agg['saved_tokens']:,}** | **{agg['saved_pct']}** |", "",
        f"**{agg['saved_per_emission']:,} tokens per emission**, and the share "
        f"spans {agg['per_emission_share_min_pct']}%–"
        f"{agg['per_emission_share_max_pct']}% across them — a property of the "
        "serialization, not of one session. S2's own emission is "
        f"{agg['tokens_per_item']} completion tokens per item, so the copy S4 "
        "re-reads is the pipeline's own whitespace on top of it.", "",
        "### Tokens per document, before → after", "",
        "The arithmetic, in the order it is applied:", "",
        f"1. the re-sent embed region is **solved** from the corpus per S4 "
        f"call — provider `prompt_tokens` minus the `cl100k` static render: "
        f"**{lev['embed_region_per_s4_call']:,.1f}** tokens (the bundle prints "
        f"`12,412`: same solve, rendered to the whole token);",
        f"2. S4 takes **{lev['s4_calls_per_doc']}** calls per document "
        f"({corpus['documents']} documents), so the region is "
        f"`{lev['embed_region_per_s4_call']:,.1f} × {lev['s4_calls_per_doc']}` "
        f"= **{lev['embed_region_per_doc']:,.1f} tokens/document**;",
        f"3. the measured share — `{agg['saved_tokens']:,} / "
        f"{agg['indented_tokens']:,}` = {100.0 * agg['saved_ratio_exact']:.4f}% "
        f"— applied to that region: `{agg['saved_ratio_exact']:.6f} × "
        f"{lev['embed_region_per_doc']:,.1f}` = **{lev['saved_per_doc']:,.1f} "
        f"tokens/document** ({lev['saved_per_call']:,.1f} per call);",
        f"4. the median document is `{lev['before_in']:,.0f} in + "
        f"{lev['before_out']:,.0f} out` = **{lev['before_total']:,.0f} "
        f"tokens**, so after = `{lev['before_total']:,.0f} − "
        f"{lev['saved_per_doc']:,.1f}` = **{lev['after_total']:,.1f}** "
        f"(**−{lev['saved_pct']}%**).", "",
        "| | before | after (`compact`) | Δ |", "|---|---|---|---|",
        f"| input tokens / median document | {lev['before_in']:,.0f} | "
        f"{lev['after_in']:,.1f} | −{lev['saved_per_doc']:,.1f} |",
        f"| output tokens / median document | {lev['before_out']:,.0f} | "
        f"{lev['before_out']:,.0f} | 0 (not measured — see ⛔) |",
        f"| **tokens / median document** | **{lev['before_total']:,.0f}** | "
        f"**{lev['after_total']:,.1f}** | "
        f"**−{lev['saved_per_doc']:,.1f} (−{lev['saved_pct']}%)** |", "",
        "⚠️ The output column is unchanged **by construction** (the prompt's "
        "content is identical, its layout smaller) and it is **not measured**: "
        "what S4 emits given either layout is exactly the `#7949` capstone's "
        "question.", "",
        "### What the saving would buy — at the allowance levels as given", "",
        "⛔ The levels below are **inputs** (`#14275`, owner), not re-derived "
        "here; every count is a **floor** (`affordable_count`), because a "
        "capacity cell that cannot be bought at its tier is wrong in the "
        "direction that flatters us.", "",
        "| tier | allowance | documents/month before | after | the floor, shown |",
        "|---|---|---|---|---|",
    ]
    for b in m["documents_per_month"]:
        n = b["documents_after"]
        lines.append(
            f"| {b['tier']} | {b['allowance']:,} | {b['documents_before']} | "
            f"**{n}** | `{n} × {lev['after_total']:,.1f} = "
            f"{n * lev['after_total']:,.0f} ≤ {b['allowance']:,}`; "
            f"`{n + 1} × = {(n + 1) * lev['after_total']:,.0f}` overruns |")
    lines += [
        "",
        f"That is **{m['documents_per_month'][0]['documents_after'] - m['documents_per_month'][0]['documents_before']:+d} / "
        f"{m['documents_per_month'][1]['documents_after'] - m['documents_per_month'][1]['documents_before']:+d} / "
        f"{m['documents_per_month'][2]['documents_after'] - m['documents_per_month'][2]['documents_before']:+d}** "
        "documents a month (solo / pro / team) — the size of the lever, not a "
        "pricing proposal.", "",
        "### What this measurement does NOT deliver", "",
        "⛔ The quality re-run on `#7949` is **NOT delivered here**. What the "
        "layout does to what the model EMITS (E1 volume / E2 correctness / E3 "
        "provenance / E4 operator edges) is unmeasured, so "
        "`TORTOISE_S4_EMBED_JSON=compact` stays **OFF**. Arming takes the "
        "variable AND the `DOOR_PINNED_OFF` preregistration pin withdrawn in "
        "the same change (`#14343` §5.2) — a capstone scored with the variable "
        "exported still measures the `indent` render.", "",
        "### Reproduce", "",
        "```bash",
        "uv run python tools/extractor_cost_measure.py embed-json \\",
        "    --receipts tests/eval/write_path/receipts/7947-arms-2026-10-18 \\",
        "    --evidence docs/evidence/14277-extractor-cost/measurements.jsonl",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_decompose(dec: dict) -> str:
    lines = ["## The input constant, decomposed (#14343)", ""]
    lines.append(f"arm `{dec['arm']}` · {dec['documents']} documents · median "
                 f"document {dec['median_doc_tokens']:,} tokens · calls/doc "
                 f"{dec['calls_per_doc']['total']}"
                 f" (s1 {dec['calls_per_doc']['s1']}, "
                 f"s2 {dec['calls_per_doc']['s2']}, "
                 f"s4 {dec['calls_per_doc']['s4']})")
    lines.append("")
    lines.append("### Static part of each prompt (offline render, exact)")
    lines.append("")
    stages = sorted(dec["statics"])
    names = sorted({k for st in stages for k in dec["statics"][st]})
    lines.append("| region | " + " | ".join(stages) + " |")
    lines.append("|---" * (len(stages) + 1) + "|")
    for name in names:
        cells = [f"{dec['statics'][st].get(name, 0):,}" for st in stages]
        lines.append(f"| `{name}` | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("### Per document — what the constant is made of")
    lines.append("")
    lines.append("| region | tokens/doc | share |")
    lines.append("|---|---|---|")
    total = dec["composition_total"] or 1
    for name, tokens in dec["regions"].items():
        lines.append(f"| {name} | {tokens:,.0f} | {100.0 * tokens / total:.1f}% |")
    lines.append(f"| **composition total** | **{total:,.0f}** | 100.0% |")
    lines.append(f"| *fitted constant (this arm's own intercept)* | "
                 f"{dec['fitted_constant']:,} | — |")
    lines.append(f"| *unattributed (fit intercept − composition)* | "
                 f"{dec['unattributed']:,.0f} | — |")
    lines.append("")
    doc_term = dec["document_term"]
    lines.append(f"The document itself is NOT in that table: it is the fit's "
                 f"slope, {doc_term['s1_calls_x_median_doc']:,.0f} tokens/doc "
                 f"({dec['calls_per_doc']['s1']} × "
                 f"{dec['median_doc_tokens']:,}) against the fitted "
                 f"{doc_term['fitted_slope_x_median_doc']:,.0f}. "
                 f"{doc_term['note']} — "
                 f"{doc_term['documents_taking_two_s1_calls']} of "
                 f"{doc_term['documents']} documents here.")
    lines.append("")
    st = dec.get("source_transcript") or {}
    if st.get("documents_with_a_tail"):
        lines.append(
            f"⚠️ **{st['documents_with_a_tail']} of {st['documents']} documents "
            "carry a SOURCE TRANSCRIPT block** (S2 and S4 append it outside the "
            "template whenever the EDU text fits the 8,000-char cap; up to "
            f"{st['tokens_per_call_max']:,} tokens per call). Those tails are "
            "**subtracted per document before the medians**, so the *S1's story* "
            "row is the narrative alone — without the subtraction it books "
            "verbatim source text as narrative (measured: 2,656 → 2,110 tokens "
            "per S2 call). The median document carries none, which is why the "
            "correction moves the row and adds no row of its own.")
        lines.append("")
    solved = dec["solved"]
    lines.append(f"Solved dynamic regions: the S1 story is **{solved['story_tokens']:,.0f}** "
                 f"tokens as the later stages see it, and S2's embed list is "
                 f"re-serialized into S4's prompt at "
                 f"**{solved['embed_list_tokens']:,.0f}** tokens — "
                 f"{solved['embed_over_emission']}× S2's own emission.")
    lines.append("")
    # ⛔ THE TWO SOLVED ROWS ARE A UNIT DIFFERENCE, and leaving it unnamed is
    # what lets a reader take them for a single tokenizer's numbers. `_per_call`
    # returns the PROVIDER's own reported `prompt_tokens` (per call, minus the
    # SOURCE-TRANSCRIPT tail), while `s2_sys`/`s4_sys`/`clause` are
    # `tiktoken cl100k_base` on a local render — so `story` and `embed` mix two
    # tokenizers, and the residual they feed (`unattributed`) absorbs the
    # complement. The bundle measures that gap itself in §3
    # (provider / offline render = 1.063, 1.035, 1.017 on three S1 prompts), so
    # the disclosure names the rows that carry it rather than presenting the
    # fit-vs-composition agreement as tokenizer-free.
    lines.append(
        "⚠️ **The two solved rows span TWO tokenizers.** `story` and `embed` "
        "are `provider prompt_tokens − cl100k static render`: the per-call "
        "prompt count is the provider's own reported usage "
        "(`last_prompt_tokens`), while the static being removed is "
        "`tiktoken cl100k_base` on a local render. The bundle measures that "
        "gap directly in §3 (measured / offline render = 1.063, 1.035, 1.017 "
        "on three S1 prompts), so these two rows carry it and `unattributed` "
        "absorbs its complement — the fit-vs-composition agreement reported "
        "below is therefore not a tokenizer-free comparison.")
    lines.append("")
    out = dec.get("output") or {}
    if out:
        lines.append("### The output constant, decomposed")
        lines.append("")
        lines.append("| stage | completion tokens/doc | share of output |")
        lines.append("|---|---|---|")
        # ⛔ THE PRINTED ROWS AND THE PRINTED TOTAL MUST SUM. The total used to
        # be formatted from the UNROUNDED sum while each row was formatted from
        # its own unrounded value, so banker's rounding made the column read
        # 2,798 + 11,677 + 3,354 = 17,830 — an off-by-one in the one table whose
        # stated rule is "a table that does not sum is not evidence" (review
        # cycle 9, P3). The total is now the SUM OF THE PRINTED ROWS, so the
        # column reconciles by construction rather than by luck. The shares keep
        # the unrounded total as their denominator, which is why they still sum
        # to 100.0%.
        stage_rows = [(stage, out.get(f"{stage}_tokens_per_doc", 0))
                      for stage in ("s1", "s2", "s4")]
        printed_total = sum(round(tokens) for _, tokens in stage_rows)
        for stage, tokens in stage_rows:
            share = (100.0 * tokens / out["total_per_doc"]) \
                if out.get("total_per_doc") else 0.0
            lines.append(f"| {stage} | {round(tokens):,} | {share:.1f}% |")
        lines.append(f"| **composition total** | **{printed_total:,}** "
                     f"| 100.0% |")
        lines.append(f"| *fitted constant (this arm's own intercept)* | "
                     f"{out['fitted_constant']:,} | — |")
        # ⛔ THE RESIDUAL IS DERIVED FROM THE PRINTED TOTAL, so the column
        # RECONCILES when a reader adds it up (review cycle 13, P2). It used to
        # be formatted from the UNROUNDED `out['unattributed']`, so the printed
        # rows summed to 17,829 while the residual printed 4,972 and the fitted
        # row said 22,802 — 17,829 + 4,972 = 22,801, one short, in the table whose
        # stated rule is that a column which does not sum is not evidence. The
        # measured value stays in the JSON (`output.unattributed`); only the
        # printed residual is re-based so the DISPLAY sums.
        printed_unattributed = out["fitted_constant"] - printed_total
        lines.append(f"| **unattributed** | {printed_unattributed:,.0f} | — |")
        lines.append("")
        tail = out.get("reemission_tail") or {}
        lines.append(
            "⚠️ **Two estimators, both reported.** The rows are per-call "
            "MEDIANS × measured call counts (the median document's shape); the "
            "fitted row is the intercept of this arm's own per-document fit, and "
            "output's fit is nearly flat "
            f"(slope {out['fitted_slope']}, R²={out['fitted_r2']}) so its "
            "intercept sits near the MEAN output per document "
            f"({out['mean_per_doc']:,.0f}), not the median. The mean is above the "
            "median because **S4's output is bimodal**: "
            f"{tail.get('s4_docs_over_6000', 0)} of {tail.get('s4_docs', 0)} "
            "documents "
            f"({tail.get('share_docs_over_6000_pct', 0)}%) average more than "
            f"6,000 completion tokens per S4 call (max "
            f"{tail.get('s4_max_per_doc_per_call', 0):,} against a median of "
            f"{tail.get('s4_median_per_doc_per_call', 0):,}) — the "
            "delta contract complying on most documents and not on the tail. "
            "That tail is where the gap between the two estimators lives, so it "
            "is stated rather than smoothed.")
        lines.append("")
        lines.append(
            f"Against the fitted constant, S2 carries "
            f"**{out.get('s2_share_of_fitted')}%** of output (the composition "
            f"gives {out.get('s2_share')}% of the attributed part). Both are "
            "printed because the remainder is not attributable by construction.")
        lines.append("")
        lines.append(
            "⚠️ The per-SECTION token counts printed by `compose` are "
            "re-serializations of each section on their own, so they do not sum "
            "to the emission and carry no residual row. Stage totals are the "
            "measured `completion_tokens`; section sizes are shape, not an "
            "account.")
        lines.append("")
        lines.append(
            "Two objects are each paid more than once, and they are NOT the "
            "same object. **S2's embed list** is paid twice: once as S2's "
            "output and once as S4's largest input region. **S1's story** is "
            "paid three times: as S1's output, as S2's user message and as S4's "
            "`{story}` slot. Nothing in this table scales with the document: "
            "the item COUNT is what moves, and a document's length barely "
            "moves it (R²=0.000 on S2's input fit).")
        lines.append("")
    if dec.get("serialization"):
        lines.append(render_serialization_lever(dec["serialization"]))
    if dec.get("levers_offline"):
        lines.append("### Candidate levers, sized offline")
        lines.append("")
        lines.append("| lever | size (tokens/doc) | quality-neutral? |")
        lines.append("|---|---|---|")
        cm = dec["levers_offline"].get("compact_master_render") or {}
        if "saved_per_doc" in cm:
            lines.append(f"| compact master render ({cm['compact_tokens']:,} vs "
                         f"{cm['verbose_tokens']:,} tokens/call) | "
                         f"−{cm['saved_per_doc']:,.0f} | NO — {cm['quality_risk']} |")
        s1l = dec["levers_offline"].get("s1_second_send") or {}
        if "saved_per_doc_if_one_call" in s1l:
            lines.append(
                f"| S1 truncation double-send "
                f"({s1l['documents_taking_two_s1_calls']} documents, "
                f"{s1l['share_pct']}% of the corpus take 2 "
                f"S1 calls) | −{s1l['saved_per_doc_if_one_call']:,.0f} | NO — "
                f"{s1l['quality_risk']} |")
        lines.append("")
    return "\n".join(lines)


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

    comp = sub.add_parser(
        "compose", help="#14343 — decompose ONE document's prompts by region")
    comp.add_argument("--out", required=True, type=Path)
    comp.add_argument("--docs", type=int, default=50,
                      help="corpus size to select the median FROM")
    comp.add_argument("--seed", type=int, default=7)
    comp.add_argument("--doc", default="median",
                      help="'median' | 'p75' | 'max' | a repo path")
    comp.add_argument("--offline", action="store_true",
                      help="canned emission: a shape check, NOT a "
                           "measurement (S4's embed-list region is a stub)")

    dec = sub.add_parser(
        "decompose", help="#14343 — attribute the fitted constants to regions")
    dec.add_argument("--evidence", required=True, type=Path,
                     help="the measurements.jsonl a `run` produced")
    dec.add_argument("--out", required=True, type=Path)
    dec.add_argument("--json-out", type=Path)
    dec.add_argument("--arm", default="cold")
    dec.add_argument("--receipts", required=True, type=Path,
                     help="dir of write-path `*.arms.json` receipts carrying "
                          "real S2 emissions (sizes the serialization lever). "
                          "REQUIRED: it is the only source of the artifact's "
                          "serialization section (the 32.5%% finding), so a "
                          "decomposition without it is a false pass, not a "
                          "shorter report")

    ej = sub.add_parser(
        "embed-json",
        help="#14371 — the S4 embed-list serialization saving, measured "
             "OFFLINE (no provider call, no key, no spend)")
    ej.add_argument("--receipts", required=True, type=Path,
                    help="dir of write-path `*.arms.json` receipts carrying "
                         "the real S2 emissions both layouts are counted on")
    ej.add_argument("--evidence", required=True, type=Path,
                    help="the `measurements.jsonl` supplying the call "
                         "structure and the median document's in/out columns")
    ej.add_argument("--arm", default="cold")
    ej.add_argument("--out", type=Path, help="write the markdown here")
    ej.add_argument("--json-out", type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "embed-json":
        measured = embed_json_measurement(args.receipts, args.evidence,
                                          arm=args.arm)
        text = finalize_markdown(render_embed_json_measurement(measured))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(measured, indent=1, sort_keys=True),
                encoding="utf-8")
        sys.stdout.write(text)
        return 0
    if args.cmd == "run":
        if args.corpus_only:
            for row in pick_corpus(limit=args.docs, seed=args.seed):
                print(f"{row['tokens']:>7}  {row['path']}")
            for row in spool_sessions(limit=args.sessions):
                print(f"{row['tokens']:>7}  session {row['path']}")
            return 0
        arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
        # ⛔ REFUSE AN ARM THE TOOL DOES NOT UNDERSTAND. `_install_arm` acts only
        # on `after`/`before`; `cold`/`warm` deliberately inherit the ambient
        # value. ANY other token used to fall through with no arm installed and
        # no diagnostic — so `--arms before,After` (a capital) or
        # `--arms before,compact` reproduced exactly the failure `_install_arm`
        # exists to prevent: both batches render `indent`, the delta is 0, `run`
        # exits 0, and a full provider run (~360 calls) measures nothing. The
        # only trace was the per-row `s4_embed_json` inside measurements.jsonl
        # (review cycle 11, P3). A typo must cost an EXIT CODE, not a run.
        #
        # ⛔ THE EXIT CODE IS 2, AND THAT HAS TO BE SAID OUT LOUD.
        # `raise SystemExit("message")` exits **1** and prints the message to
        # stderr — the same code this tool uses for a real run failure, so a
        # wrapper or CI step could not tell "you typed the arm wrong" from
        # "the measurement failed". `SystemExit(int)` makes it argparse's own
        # usage code, which is what a caller already distinguishes (review
        # cycle 12, P2 — the comment used to CLAIM exit 2 while the code
        # produced 1, and the pin never read `exc.value.code`).
        if not arms:
            print("--arms named no arm (got an empty selection)",
                  file=sys.stderr)
            raise SystemExit(2)
        unknown = sorted(set(arms) - _ARMS)
        if unknown:
            print(
                f"unknown arm(s) {', '.join(repr(a) for a in unknown)}; the "
                f"closed set is {', '.join(sorted(_ARMS))} — `after`/`before` "
                f"set the S4 embed-list layout, `cold`/`warm` inherit the "
                f"ambient value, and anything else would silently measure the "
                f"same render twice",
                file=sys.stderr)
            raise SystemExit(2)
        return run_measurement(out=args.out, docs_limit=args.docs,
                               sessions_limit=args.sessions,
                               workers=args.workers, seed=args.seed, arms=arms,
                               arm_gap_s=args.arm_gap_s,
                               long_split=args.long_split,
                               split_max_turns=args.split_max_turns,
                               split_only=args.split_only)
    if args.cmd == "decompose":
        decomposition = decompose(load_rows(args.evidence), arm=args.arm,
                                  receipts=args.receipts)
        # A `--receipts` path that yielded NOTHING is a user error, and the
        # artifact it would produce is one whose serialization section (the
        # headline 32.5% finding) is silently absent — the exact false pass
        # this bundle exists to carry. Refuse instead of writing it. The flag
        # itself is `required=True` for the same reason: an OMITTED one used to
        # emit that same artifact at exit 0, and this bundle's two documented
        # commands disagreed about it (one passed it, one did not).
        if not decomposition.get("serialization"):
            raise SystemExit(
                f"no usable write-path receipts in {args.receipts} — refusing "
                "to emit an artifact whose serialization section is missing")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(finalize_markdown(render_decompose(decomposition)),
                            encoding="utf-8")
        if args.json_out:
            args.json_out.write_text(json.dumps(decomposition, indent=1,
                                               sort_keys=True),
                                     encoding="utf-8")
        sys.stdout.write(render_decompose(decomposition))
        return 0
    if args.cmd == "compose":
        corpus = pick_corpus(limit=args.docs, seed=args.seed)
        if args.doc in ("median", "p75", "max"):
            sizes = [r["tokens"] for r in corpus]
            target = {"median": _quantile(sizes, 0.5),
                      "p75": _quantile(sizes, 0.75),
                      "max": max(sizes)}[args.doc]
            pick = min(corpus, key=lambda r: abs(r["tokens"] - target))
        else:
            pick = next((r for r in corpus if r["path"] == args.doc), None)
            if pick is None:
                raise SystemExit(f"{args.doc!r} is not in the selected corpus")
        doc = {"path": pick["path"], "tokens": pick["tokens"],
               "text": (_REPO_ROOT / pick["path"]).read_text(
                   encoding="utf-8", errors="replace")}
        art = compose_one(doc=doc, live=not args.offline)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(art, indent=1, sort_keys=True),
                            encoding="utf-8")
        report = compose_report([art])
        table = render_compose(report)
        sys.stdout.write(table)
        sys.stderr.write(f"\ncompose artifact: {args.out}\n")
        # ⛔ A LIVE run that did not measure every stage must not exit 0: the
        # exit code is the only machine-readable signal a scripted re-runner
        # has, and returning 0 here is how a 401-only run wrote an artifact that
        # read as a measurement (review cycle 7, P1). `--offline` is exempt —
        # a stub is a documented SHAPE check and its banner says so.
        if not args.offline and report.get("complete") is not True:
            sys.stderr.write(
                "compose: INCOMPLETE — not every stage measured (see the "
                "INCOMPLETE banner above). The artifact is written for "
                "diagnosis, but it is NOT a measurement.\n")
            return 1
        return 0
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
