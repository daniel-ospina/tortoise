"""#2976 — TR temporal admission arms over ONE ingest (measurement instrument).

WHY THIS EXISTS. `tools/longmem_eval/run.py` measures ONE configuration per
invocation, and its ingest-reuse seam (`--cache-ingest`) is armed only for
``--ingest-mode v2``. On the deterministic lane the harness therefore wipes and
re-ingests every question per run, which makes a 3-arm before/after measurement
~3x the ingest cost for no methodological gain — the arms differ only in how
the SAME retrieved pool is admitted and rendered.

This driver runs N admission arms over ONE idempotent ingest of each question:

    baseline             tr_top_k=12 item cap, the shipped TR window (R5 #1544)
    admission            token-budgeted, session-granular admission (#2976)
    admission+completion admission + the session-completion pass (whole session)

Everything the reader sees goes through the SAME code the product uses:
``retrieve.retrieve_for_question`` (which calls ``tortoise.retrieval`` for the
pool, the admission and ``render_context``), the pinned reader prompt
(``reader.build_reader``) and one judge (``judge.build_judge``). The adaptive
differences between arms are ONLY the two #2976 switches — the reader prompt is
never touched, which is what makes the delta attributable to retrieval.

Method (the #2578 contract, restated so a row is self-describing):

* same pinned analysis subset (``measure_temporal.deterministic_subset`` of the
  committed census → 55 questions, 52 answerable + 3 abstention controls);
* same reader prompt and rendering (``tortoise.retrieval.render_context``);
* ingest once per question, reusing an existing graph when its points are
  already present (``ingest_haystack``'s per-session existence probe is
  idempotent, so a warm graph costs seconds, not minutes);
* per-question, per-arm row: judge verdict, reader refusal, context tokens,
  context items, sessions in the window, gold-marked hits admitted, wall time.

The judge is whatever ``--judge-model`` names: for a keyless, deterministic and
directly comparable number use ``variant-containment`` (the #2976 grader).

Usage::

    uv run python -m tools.longmem_eval.temporal_admission_arms \
        --split s --db 'docker://:pw@localhost:6379/db' \
        --reader-model deepseek:deepseek-v4-flash \
        --judge-model variant-containment \
        --out /tmp/2976-admission-arms.jsonl

    # a shard of the subset (parallel lanes share no question graph)
    ... --shard 0 --shards 6
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — the eval
# lane is 3.12-only, so a bare ImportError would be the first thing a
# 3.11 user sees.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #5128's contract: the refusal names the file, the interpreter it GOT, and
    # the invocation that WORKS. Without the `(got X.Y)` half a reader on 3.9 is
    # told the requirement and left to guess whether they met it, and without
    # the invocation they are left to guess how to run it —
    # `tests/test_entry_point_python_guard.py` pins all three repo-wide.
    raise SystemExit(
        "tools/longmem_eval/temporal_admission_arms.py requires Python >= 3.12 "
        f"(got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python -m tools.longmem_eval.temporal_admission_arms`"
    )

import argparse
import contextlib
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from tortoise.reader import _looks_abstained
from tortoise.sdk import TortoiseSDK

from . import dataset as ds
from . import measure_temporal as mt
from .ingest import ingest_haystack
from .judge import build_judge, is_abstention
from .reader import build_reader
from .retrieve import (
    DEFAULT_TR_SESSION_COMPLETION_PER_SESSION,
    DEFAULT_TR_SESSION_COMPLETION_SESSIONS,
    DEFAULT_TR_SESSION_COMPLETION_TOTAL,
    DEFAULT_TR_SESSION_COMPLETION_WINDOW,
    retrieve_for_question,
)
from .run import question_graph_namespace

#: The arms. Only the two #2976 switches differ — every other knob is the
#: shipped default, so a delta cannot come from an unrelated config change.
ARMS: tuple[dict[str, Any], ...] = (
    # EXPLICIT off switches, not an empty dict: `retrieve_for_question` resolves
    # a None switch from the ambient env, so an operator shell carrying
    # TORTOISE_LME_TR_SESSION_ADMISSION=1 (the very thing this PR documents
    # arming) would silently run all three arms as the SAME configuration and
    # report a ~0 delta that reads as "admission does not help".
    {"id": "baseline",
     "knobs": {"tr_session_admission": False,
               "tr_session_completion": False}},
    {"id": "admission",
     # BOTH switches explicit, for the same reason as `baseline`: an arm that
     # names only the switch under test leaves the OTHER one to the ambient
     # env, so an operator shell carrying
     # TORTOISE_LME_TR_SESSION_COMPLETION=1 (which this PR's own runbook
     # documents arming) would make `admission` byte-identical to
     # `admission+completion` and publish the completion number under the
     # `admission` label, with a ~0 delta between the two arms.
     "knobs": {"tr_session_admission": True,
               "tr_session_completion": False}},
    {"id": "admission+completion",
     # The four BUDGETS are pinned too, not just the switches: they are ordinary
     # `retrieve_for_question` env seams
     # (TORTOISE_LME_TR_SESSION_COMPLETION_TOTAL/_WINDOW/_SESSIONS/_PER_SESSION),
     # and they are measurement-relevant configuration — the run's fingerprint
     # keys on them (`test_fingerprint_stamps_the_arm_and_its_budgets_only_when_on`),
     # so a shell carrying one of them would produce this arm's published column
     # through a completion pass the runbook does not describe. The values are
     # the product defaults, so pinning them changes no measurement.
     "knobs": {"tr_session_admission": True,
               "tr_session_completion": True,
               "tr_session_completion_total":
                   DEFAULT_TR_SESSION_COMPLETION_TOTAL,
               "tr_session_completion_window":
                   DEFAULT_TR_SESSION_COMPLETION_WINDOW,
               "tr_session_completion_sessions":
                   DEFAULT_TR_SESSION_COMPLETION_SESSIONS,
               "tr_session_completion_per_session":
                   DEFAULT_TR_SESSION_COMPLETION_PER_SESSION}},
)

#: The pinned knobs every arm shares (the #2578 A-default configuration).
SHARED_KNOBS: dict[str, Any] = {
    "ks": (5, 10, 20),
    "top_k": 20,
    "max_context_tokens": 8000,
    "max_chunks_per_session": 3,
    "tr_top_k": 12,
    "retrieval_budget_ms": 1500,
}


def arm_by_id(arm_id: str) -> dict[str, Any]:
    for arm in ARMS:
        if arm["id"] == arm_id:
            return arm
    raise KeyError(f"unknown arm {arm_id!r}; known: {[a['id'] for a in ARMS]}")


def shard_of(qid: str, shards: int) -> int:
    """Stable shard assignment (by question id — order-independent)."""
    return sum(qid.encode()) % max(shards, 1)


def gold_census(context_points: list[dict], pool: list[dict]) -> dict[str, int]:
    """Marked-hit census for one arm's window (the #2578 measure_facts shape).

    ``admitted`` counts evidence-marked hits in the reader's window; ``pool``
    counts them in the fetched candidate pool — gold-in-pool but not admitted
    is the admission failure this issue measures.
    """
    return {
        "gold_admitted": sum(1 for h in context_points if h.get("has_answer")),
        "gold_in_pool": sum(1 for h in pool if h.get("has_answer")),
        "context_items": len(context_points),
        "context_sessions": len({h.get("session_id") for h in context_points}),
    }


def run_question(sdk: TortoiseSDK, question: dict, *, reader, judge,
                 arms: tuple[dict[str, Any], ...]) -> list[dict]:
    """Retrieve + read + judge ONE question under every arm.

    Every row carries the READER and JUDGE identities that produced it. That is
    not decoration: #2976 runs under a DECLARED substitution (the pinned
    OpenRouter route and the official gpt-4o judge were both blocked), so a row
    that did not name its grader could not be distinguished from one measured
    under the official judge — the exact silent-inheritance the substitution
    must never allow.
    """
    rows: list[dict] = []
    for arm in arms:
        t0 = time.monotonic()
        ret = retrieve_for_question(
            sdk, question, **SHARED_KNOBS, **arm["knobs"])
        context_points = ret["context_points"]
        hypothesis = reader.answer(
            context_hits=context_points,
            question=question["question"],
            question_date=question.get("question_date"),
            question_type=question.get("question_type"))
        label = judge.judge(
            question_type=question.get("question_type", ""),
            question=question["question"],
            answer=question.get("answer", ""),
            hypothesis=hypothesis,
            abstention=is_abstention(question["question_id"]))
        seconds = time.monotonic() - t0
        census = gold_census(context_points, ret["hits"])
        rows.append({
            "qid": question["question_id"],
            "arm": arm["id"],
            "reader_model": getattr(reader, "model_id", None),
            "reader_model_spec": getattr(reader, "model_spec", None),
            "reader_pinned": getattr(reader, "pinned", None),
            "judge_model": getattr(judge, "model_id", None),
            "judge_model_spec": getattr(judge, "model_spec", None),
            "answerable": not is_abstention(question["question_id"]),
            "label": bool(label),
            "hypothesis": hypothesis,
            "reader_refusal": bool(_looks_abstained(hypothesis)),
            "context_tokens": ret.get("context_tokens"),
            "seconds": round(seconds, 2),
            **census,
            "admission_stats": ret.get("tr_session_admission_stats"),
            "completion_stats": ret.get("tr_session_completion_stats"),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.temporal_admission_arms",
        description="#2976: run the TR admission arms over one ingest per "
                    "question (variance-free before/after on the pinned "
                    "55-question temporal subset).")
    ap.add_argument("--split", default=ds.DEFAULT_SPLIT,
                    choices=sorted(ds.SPLIT_FILES))
    ap.add_argument("--data", default=None,
                    help="local dataset JSON/JSONL (skips the download)")
    ap.add_argument("--census", default=mt.CENSUS_DEFAULT)
    ap.add_argument("--db", required=True,
                    help="FalkorDB URI (the per-question graph lane; required "
                         "so a warm graph is reused instead of re-ingested)")
    ap.add_argument("--arms", default=",".join(a["id"] for a in ARMS),
                    help="comma-separated arm ids to run")
    ap.add_argument("--reader-model", default=None)
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--shard", type=int, default=None,
                    help="run only this shard of the subset (parallel lanes)")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", required=True, help="per-row JSONL output path")
    ap.add_argument("--sum-out", default=None,
                    help="optional JSON summary output path")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--keep-graph", action="store_true",
                    help="keep each question's graph after its arms run "
                         "(default: RELEASE it — the driver needs the graph "
                         "only for its own arms, and a 55-question subset left "
                         "resident OOM-killed a 2 GB eval container at q40)")
    ap.add_argument("--resume", action="store_true",
                    help="skip questions already present in --out (a killed "
                         "run's rows are complete per question: rows are "
                         "written only after ALL arms for that question ran)")
    args = ap.parse_args(argv)

    arms = tuple(arm_by_id(a.strip()) for a in args.arms.split(",") if a.strip())
    # ⛔ A non-resume, non-shard run appends. Appending to an EXISTING output
    # silently DOUBLES the rows, and `summarize` does not dedupe — every
    # absolute number in a published artifact would change (the ratios survive,
    # so the file still looks valid). Refuse rather than corrupt a record.
    if Path(args.out).exists() and not args.resume and args.shard is None \
            and Path(args.out).stat().st_size > 0:
        raise SystemExit(
            f"refusing to APPEND to the existing {Path(args.out)} — pass --resume "
            f"(skip the qids already measured) or a fresh --out path")

    instances = {i["question_id"]: i for i in ds.load_dataset(
        split=args.split, data_path=args.data, download=args.data is None)}
    rows = mt.deterministic_subset(mt.load_census(args.census)["rows"])
    if args.shard is not None:
        rows = [r for r in rows if shard_of(r["qid"], args.shards) == args.shard]
    if args.limit:
        rows = rows[:args.limit]

    reader = build_reader(args.reader_model)
    judge = build_judge(args.judge_model)
    reader.ping("ping")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if args.resume and out_path.exists():
        done = {json.loads(ln)["qid"]
                for ln in out_path.read_text().splitlines() if ln.strip()}
        print(f"[2976] resume: {len(done)} question(s) already measured",
              file=sys.stderr)
    print(f"[2976] arms={[a['id'] for a in arms]} questions={len(rows)} "
          f"reader={reader.model_id} judge={judge.model_id}", file=sys.stderr)

    with out_path.open("a", encoding="utf-8") as fh, \
            tempfile.TemporaryDirectory(dir=args.work_dir):
        for i, row in enumerate(rows, 1):
            qid = row["qid"]
            if qid in done:
                continue
            question = instances.get(qid)
            if question is None:
                print(f"[2976] {qid}: absent from the split — skipped",
                      file=sys.stderr)
                continue
            # The SAME per-question namespace the harness uses, so a graph a
            # prior harness run already ingested is reused instead of
            # re-ingested (the ingest path's existence probe makes the top-up
            # a no-op). The driver never wipes.
            sdk = TortoiseSDK(namespace=question_graph_namespace(
                None, None, qid))
            try:
                t0 = time.monotonic()
                ingest_haystack(sdk, question)
                ingest_s = time.monotonic() - t0
                q_rows = run_question(sdk, question, reader=reader,
                                      judge=judge, arms=arms)
                if not args.keep_graph:
                    # RELEASE the question graph: the driver already holds
                    # every arm's rows for it, so keeping 55 resident graphs
                    # only spends the lane's memory budget — measured: a 2 GB
                    # eval container was OOM-killed at q40 with ~40 graphs
                    # resident. Scoped exactly like the harness's fresh-run
                    # wipe, so a peer question's graph is never touched.
                    with contextlib.suppress(Exception):
                        sdk._get_proj().g.query(
                            "MATCH (n) WHERE n.lme_question_id = $q "
                            "DETACH DELETE n", params={"q": qid})
            finally:
                sdk.close()
            for r in q_rows:
                r["cls"] = row["cls"]
                r["ingest_seconds"] = round(ingest_s, 1)
                fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(f"[2976] [{i}/{len(rows)}] {qid} {row['cls']} "
                  f"ingest={ingest_s:.0f}s " + " ".join(
                      f"{r['arm']}={'OK' if r['label'] else 'miss'}"
                      f"({r['context_tokens']}t)" for r in q_rows),
                  file=sys.stderr)
    if args.sum_out:
        summarize(out_path, args.sum_out)
    return 0


def summarize(rows_path: str | Path, out_path: str | Path) -> dict:
    """Aggregate the JSONL into the issue's metric set (per arm, per class)."""
    rows = [json.loads(ln) for ln in Path(rows_path).read_text().splitlines()
            if ln.strip()]
    summary: dict[str, Any] = {"arms": {}}
    # run-level provenance: the identities come off the rows themselves, so the
    # summary can never describe a different grader than the rows carry.
    for key in ("reader_model", "reader_model_spec", "reader_pinned",
                "judge_model", "judge_model_spec"):
        values = {r.get(key) for r in rows if r.get(key) is not None}
        if values:
            summary.setdefault("provenance", {})[key] = (
                values.pop() if len(values) == 1 else sorted(values))
    for arm in dict.fromkeys(r["arm"] for r in rows):
        sub = [r for r in rows if r["arm"] == arm and r["answerable"]]
        by_cls: dict[str, list[int]] = {}
        for r in sub:
            slot = by_cls.setdefault(r["cls"], [0, 0])
            slot[0] += 1
            slot[1] += 1 if r["label"] else 0
        summary["arms"][arm] = {
            "n": len(sub),
            "correct": sum(1 for r in sub if r["label"]),
            "refusals": sum(1 for r in sub if r["reader_refusal"]),
            "gold_admitted": sum(1 for r in sub if r["gold_admitted"]),
            "context_tokens_median": _median(
                [r["context_tokens"] or 0 for r in sub]),
            "seconds_mean": round(
                sum(r["seconds"] for r in sub) / max(len(sub), 1), 2),
            "by_class": {k: {"n": v[0], "correct": v[1]}
                         for k, v in sorted(by_cls.items())},
        }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(summary, indent=2, sort_keys=True)
                              + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


if __name__ == "__main__":
    raise SystemExit(main())
