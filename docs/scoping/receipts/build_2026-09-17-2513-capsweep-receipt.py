"""Build the #2513 C4 total-cap-sweep receipt from the arm artifacts.

Reads /tmp/lme2513/ms71/cap{10,15}/<arm>.json (+ the cohort provenance) and
writes docs/scoping/receipts/<date>-2513-reinjection-capsweep-<sha>.json.

Per arm block it also emits the per-question (A)/(B) decider
`gold_sessions_in_pool` / `gold_session_best_rank` — the two fields without
which the "not-reachable (A) vs reachable-but-dropped (B)" split of the
residual multi-session misses cannot be re-derived from the receipt alone.

Overrides (the defaults are this machine's run-time provenance):
  LME_RAW           arm-artifact root (default /tmp/lme2513/ms71)
  LME_WT            worktree the measurement ran in; its HEAD branch is what
                    the receipt records as `branch`, so a re-run from another
                    checkout must point this at the measured branch
  LME_RECEIPT_DEST  output path (default <LME_WT>/docs/scoping/receipts/...)

`--rederive <receipt.json>` rebuilds an existing receipt from its own recorded
blocks (its `results`, `run_integrity`, `retrieval_mode` and `cohort`) instead
of from arm artifacts, and writes it back. Each falsifier `measured` block is
recomputed from `results`; `verified_at_seal` is re-emitted from this
generator's seal-time literal (it is NOT derivable from `results`). The raw
artifacts behind this receipt are gone, so that is the only way to keep it in
agreement with this generator.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
from pathlib import Path


def _read_json(path: Path):
    with open(path) as fh:
        return json.load(fh)

# The worktree the measurement ran in: the receipt records ITS HEAD branch, so
# re-running the generator from a different checkout must set LME_WT to a
# checkout of the measured branch or the `branch` field would silently change.
WT = Path(os.environ.get(
    "LME_WT",
    "/Users/danielospina/Documents/GitHub/tortoise/.worktrees/"
    "impl/2513-retrieval"))
RAW = Path(os.environ.get("LME_RAW", "/tmp/lme2513/ms71"))
COHORT = Path.home() / ".cache/tortoise-longmemeval/longmemeval_2517_ms_tail.json"
PROV = Path.home() / ".cache/tortoise-longmemeval/longmemeval_2517_ms_tail.provenance.json"
SHA = "412470cd3"
ARMS = ("off", "off_b", "inj_only", "on")

# Gold-session census support. The cohort is the ONLY source of gold labels;
# the arm artifact is the only source of the candidate pool. Loaded LAZILY:
# `--rederive` never needs it (it takes the run order from `results`), so the
# module must import even when the cohort cache file is absent.
_COHORT_CACHE = {}


def cohort_list():
    if "list" not in _COHORT_CACHE:
        _COHORT_CACHE["list"] = _read_json(COHORT)
    return _COHORT_CACHE["list"]


def cohort_data():
    if "data" not in _COHORT_CACHE:
        _COHORT_CACHE["data"] = {q["question_id"]: q for q in cohort_list()}
    return _COHORT_CACHE["data"]


POINT_SESSION_RE = re.compile(r"^lme:(?P<qid>[0-9A-Za-z_]+):s(?P<sid>\d+):")
GRADED_K = 5


def gold_pool_census(qid, ranked_ids):
    """The per-question (A)/(B) decider, keyed by gold-session HAYSTACK INDEX.

    (A) not-reachable  = some gold session contributes ZERO rows to this arm's
                         candidate pool (`ranked_ids`), so no ranking change can
                         ever reach it;
    (B) reachable-but-dropped = every gold session is IN the pool but the best
                         gold rank is still >= GRADED_K (dropped by the cut).

    Both maps are keyed by the cohort's `haystack_session_ids` index, so their
    KEY SET is the gold-session set and the split stays decidable from the
    receipt alone. `gold_session_best_rank` is 0-based; null == ABSENT.
    """
    out = {"gold_sessions_in_pool": {}, "gold_session_best_rank": {}}
    q = cohort_data().get(qid)
    if q is None:
        return out
    pos = {s: i for i, s in enumerate(q["haystack_session_ids"])}
    gold = sorted({pos[s] for s in q["answer_session_ids"] if s in pos})
    best = {}
    for i, p in enumerate(ranked_ids or []):
        m = POINT_SESSION_RE.match(p or "")
        if m:
            sid = int(m.group("sid"))
            if sid not in best:
                best[sid] = i          # first hit in rank order == best rank
    for sid in gold:
        out["gold_sessions_in_pool"][str(sid)] = sid in best
        out["gold_session_best_rank"][str(sid)] = best.get(sid)
    return out


def git(*a):
    """Run git in the measured worktree, FAILING LOUDLY on a non-zero exit.

    A silent empty stdout here would be written into the receipt's provenance
    (`branch`, `revision_measured.subject`) as if it were a real value."""
    r = subprocess.run(["git", "-C", str(WT), *a], capture_output=True,
                       text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(a)} in {WT} failed rc={r.returncode}: "
            f"{r.stderr.strip()}")
    return r.stdout.strip()


# SEAL-TIME FACTS — a LITERAL, recorded once when this receipt was sealed
# (2026-09-21), NOT derived from the measurement. It is deliberately NOT
# recomputed on a re-run: re-probing git here would silently re-seal a
# historical record and mutate it whenever origin/main moves. If this
# generator is reused for a different measured revision, update by hand.
SEAL_VERIFICATION = {
    "date": "2026-09-21",
    "origin_main": "a36fd686eec2b6cdb21fc21af45e365dd64974a2",
    "commits_in_origin_main_not_in_measured_revision": 306,
    "merge_base": "a079767f1655c2a7ba3a0daa78b83200e16d6c61",
    "measured_revision_is_ancestor_of_origin_main": False,
    "provenance": ("literal, captured at seal \u2014 NOT derived from the "
                   "measurement and NOT recomputed on a re-run"),
    "method": ("git merge-base --is-ancestor / git rev-list --count / "
               "git cat-file -e, run once at seal on 2026-09-21"),
}

# Methodology narrative. Qualitative only: the raw hit count it used to quote
# ("2503 hits") is NOT recorded in any surviving artifact, so it is omitted
# rather than repeated as an un-checkable number. Canonical here so a raw run
# and `--rederive` cannot disagree on it.
LEG_MIX_OBSERVED = ("every retrieved hit carries match_source 'rrf' — so this "
                    "is NOT a keyword/FTS-only run (the raw hit count is not "
                    "recorded in this receipt)")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load(cap, arm):
    p = RAW / f"cap{cap}" / f"{arm}.json"
    return json.load(open(p)) if p.exists() else None


def qv(out, key, k="5"):
    return [float((o.get(key) or {})[k]) for o in out
            if (o.get(key) or {}).get(k) is not None]


def mn(xs):
    return round(statistics.mean(xs), 4) if xs else None


def census(out):
    a = {k: 0 for k in ("seeded", "injected_total", "injected_merged",
                        "dropped_by_cap", "fetch_ok_questions",
                        "total_cap_hit_questions", "questions_with_seeds",
                        "seed_sessions_total")}
    for o in out:
        st = o.get("session_reinjection_stats") or {}
        a["questions_with_seeds"] += 1 if st.get("seeded") else 0
        a["seed_sessions_total"] += len(st.get("seed_sessions") or [])
        a["fetch_ok_questions"] += 1 if st.get("fetch_ok") else 0
        a["total_cap_hit_questions"] += 1 if st.get("total_cap_hit") else 0
        for k in ("seeded", "injected_total", "injected_merged",
                  "dropped_by_cap"):
            a[k] += int(st.get(k) or 0)
    return a


def arm_block(d):
    out = d.get("outcomes") or []
    r = d.get("retrieval") or {}
    gp = {o["question_id"]: gold_pool_census(o["question_id"], o.get("ranked_ids"))
          for o in out}
    lat = sorted(o["retrieval_latency_ms"] for o in out
                 if o.get("retrieval_latency_ms") is not None)
    hist = {}
    for o in out:
        hist[o["question_type"]] = hist.get(o["question_type"], 0) + 1
    return {
        "n_questions": len(out),
        "question_type_histogram": hist,
        "recall_all@5": [mn([1.0 if (o.get("session_recall@k") or {}).get("5") == 1.0
                             else 0.0 for o in out
                             if (o.get("session_recall@k") or {}).get("5")
                             is not None]), len(qv(out, "session_recall@k"))],
        "session_recall@5": [mn(qv(out, "session_recall@k")),
                             len(qv(out, "session_recall@k"))],
        "evidence_recall@5": [mn(qv(out, "evidence_recall@k")),
                              len(qv(out, "evidence_recall@k"))],
        "reader_evidence@5": [mn(qv(out, "reader_evidence@k")),
                              len(qv(out, "reader_evidence@k"))],
        "turn_recall@5": [mn(qv(out, "turn_recall@k")),
                          len(qv(out, "turn_recall@k"))],
        "reader_surface@5": (r.get("reader_surface@k") or {}).get("5"),
        "reader_surface@5_per_q": mn(qv(out, "reader_surface@k")),
        "chunk_evidence_recall@5": r.get("chunk_evidence_recall@k"),
        "context_tokens_mean": mn([o["context_tokens"] for o in out
                                   if o.get("context_tokens") is not None]),
        "context_point_count_mean": r.get("context_point_count_mean"),
        "retrieval_latency_ms_mean_p95": [
            round(statistics.mean(lat), 1),
            lat[max(0, int(0.95 * len(lat)) - 1)]] if lat else None,
        "sr_census": census(out),
        "per_question": {
            "session_recall@5": {o["question_id"]: (o.get("session_recall@k") or {}).get("5") for o in out},
            "evidence_recall@5": {o["question_id"]: (o.get("evidence_recall@k") or {}).get("5") for o in out},
            "reader_surface@5": {o["question_id"]: (o.get("reader_surface@k") or {}).get("5") for o in out},
            "context_tokens": {o["question_id"]: o.get("context_tokens") for o in out},
            "injected_merged": {o["question_id"]: (o.get("session_reinjection_stats") or {}).get("injected_merged") for o in out},
            "total_cap_hit": {o["question_id"]: (o.get("session_reinjection_stats") or {}).get("total_cap_hit") for o in out},
            "pool_size": {o["question_id"]: o.get("pool_size") for o in out},
            "ranked_ids_top5": {o["question_id"]: (o.get("ranked_ids") or [])[:5] for o in out},
            "gold_sessions_in_pool": {o["question_id"]: gp[o["question_id"]]["gold_sessions_in_pool"] for o in out},
            "gold_session_best_rank": {o["question_id"]: gp[o["question_id"]]["gold_session_best_rank"] for o in out},
        },
    }


def flips(map_a, map_b):
    """Per-question flips of ONE metric: {qid: value} -> {qid: value}."""
    imp, reg, same = [], [], 0
    for q in sorted(map_a):
        a, b = map_a[q], map_b.get(q)
        if a is None or b is None:
            continue
        if b > a + 1e-9:
            imp.append([q, a, b])
        elif b < a - 1e-9:
            reg.append([q, a, b])
        else:
            same += 1
    return {"n_improved": len(imp), "n_regressed": len(reg),
            "unchanged": same, "improvements": imp, "regressions": reg}


def _closure(caps, per_q):
    """Does the arm CLOSE the multi-session miss class? Count fully-recalled
    questions (session_recall@5 == 1.0) per arm, the OFF-missed set, how many
    of those the arm converts, and any regressions."""
    out = {}
    for cap in sorted(caps, key=int):
        if "off" not in caps[cap]:
            continue
        off_miss = sorted(q for q, v in per_q[f"{cap}_off"]["session_recall@5"].items()
                          if v is not None and v < 1.0)
        n = len(per_q[f"{cap}_off"]["session_recall@5"])
        block = {"n": n, "off_missed": len(off_miss), "off_missed_ids": off_miss,
                 "arms": {}}
        for a in ("inj_only", "on"):
            if a not in caps[cap]:
                continue
            m = per_q[f"{cap}_{a}"]["session_recall@5"]
            miss = sorted(q for q, v in m.items() if v is not None and v < 1.0)
            closed = [q for q in off_miss if q not in miss]
            newly = [q for q in miss if q not in off_miss]
            block["arms"][a] = {
                "missed": len(miss), "missed_ids": miss,
                "fully_recalled": n - len(miss),
                "recall_all@5": round((n - len(miss)) / n, 4),
                "misses_closed": len(closed), "misses_closed_ids": closed,
                "misses_closed_share_of_off_misses": round(len(closed) / len(off_miss), 4),
                "newly_missed": len(newly), "newly_missed_ids": newly,
                "classes_remaining": len(miss),
            }
        out[str(cap)] = block
    return out


def _arm(caps, cap, arm):
    return ((caps.get(str(cap)) or {}).get(arm)) or {}


def _mean0(block, key="recall_all@5"):
    v = block.get(key)
    return v[0] if isinstance(v, list) and v else None


def _pq(per_q, cap, arm):
    block = per_q.get(f"{cap}_{arm}")
    return block if isinstance(block, dict) else None


def _flips_of(per_q, cap_a, arm_a, cap_b, arm_b, metric):
    a, b = _pq(per_q, cap_a, arm_a), _pq(per_q, cap_b, arm_b)
    if a is None or b is None:
        return None
    ma, mb = a.get(metric), b.get(metric)
    if not isinstance(ma, dict) or not isinstance(mb, dict):
        return None
    return flips(ma, mb)


def _flip_str(flip):
    return None if flip is None else f"+{flip['n_improved']}/-{flip['n_regressed']}"


def _ratio(num, den):
    return None if num is None or den is None else f"{num}/{den}"


def _r1(x):
    return None if x is None else round(x, 1)


def _subset_recall_all(per_question_block, qids):
    """recall_all@5 over an ordered SUBSET of questions (the n=20 prefix),
    computed from that arm's per-question session_recall@5 map."""
    m = (per_question_block or {}).get("session_recall@5") or {}
    vals = [1.0 if m[q] == 1.0 else 0.0 for q in qids if m.get(q) is not None]
    return round(statistics.mean(vals), 4) if vals else None


def derive_falsifier_measured(caps, per_q, reader_token_cap):
    """Every value returned here is COMPUTED from `caps`/`per_q` (the receipt's
    own `results`), so a `measured` block cannot disagree with the data it
    summarises.

    Nothing here may be a literal: a figure that cannot be derived from
    `results` is either dropped or moved out to the verdict's
    `cited_not_measured` with its provenance. `reader_token_cap` is the one
    input taken from `retrieval_mode` (methodology metadata) rather than from
    the per-arm results.
    """
    closure = _closure(caps, per_q)
    c10 = closure.get("10") or {}
    c10_on = (c10.get("arms") or {}).get("on") or {}
    c10_inj = (c10.get("arms") or {}).get("inj_only") or {}

    off10 = _pq(per_q, 10, "off") or {}
    # The n=20 prefix is "the first 20 questions of THIS RUN", so it is taken
    # from the run's own outcome order (the key order of the arm's per-question
    # map) — not from the cohort cache, which `--rederive` does not read.
    n20 = list((off10.get("session_recall@5")) or {})[:20]

    def top5(cap_a, arm_a, cap_b, arm_b):
        a, b = _pq(per_q, cap_a, arm_a), _pq(per_q, cap_b, arm_b)
        if a is None or b is None:
            return None
        r1, r2 = a.get("ranked_ids_top5") or {}, b.get("ranked_ids_top5") or {}
        if not r1 or not r2:
            return None
        return [sum(1 for q in r1 if r1.get(q) == r2.get(q)), len(r1)]

    def graded_flips(cap_a, arm_a, cap_b, arm_b):
        got = [_flips_of(per_q, cap_a, arm_a, cap_b, arm_b, m)
               for m in ("session_recall@5", "evidence_recall@5",
                         "reader_surface@5")]
        if any(f is None for f in got):
            return None
        imp = sum(f["n_improved"] for f in got)
        reg = sum(f["n_regressed"] for f in got)
        return f"{imp}/{reg}" + (" on every metric" if imp == 0 and reg == 0 else "")

    def merged_at_cap10(census):
        if census.get("injected_merged") is None or census.get("injected_total") is None:
            return None
        return f"{census['injected_merged']}/{census['injected_total']} at cap10"

    on10, on15 = _arm(caps, 10, "on"), _arm(caps, 15, "on")
    on_census10, on_census15 = on10.get("sr_census") or {}, on15.get("sr_census") or {}
    inj_census15 = _arm(caps, 15, "inj_only").get("sr_census") or {}
    sweep_on = _flips_of(per_q, 10, "on", 15, "on", "session_recall@5")
    off_off_b, off_cap15 = top5(10, "off", 10, "off_b"), top5(10, "off", 15, "off")

    return {
        "1_miss_class_closure": {
            "off_missed": c10.get("off_missed"),
            "on_missed": c10_on.get("missed"),
            "inj_only_missed": c10_inj.get("missed"),
            "closed": c10_on.get("misses_closed"),
            "closed_share_of_off_misses": c10_on.get(
                "misses_closed_share_of_off_misses"),
            "newly_missed": c10_on.get("newly_missed"),
            "session_recall@5_flips": _flip_str(
                _flips_of(per_q, 10, "off", 10, "on", "session_recall@5")),
            "recall_all@5": [_mean0(_arm(caps, 10, "off")),
                             _mean0(_arm(caps, 10, "on"))],
            "session_recall@5": [
                _mean0(_arm(caps, 10, "off"), "session_recall@5"),
                _mean0(_arm(caps, 10, "on"), "session_recall@5")],
            "n": on10.get("n_questions"),
        },
        "2_cap_sweep_truncation": {
            "total_cap_hit_questions": {
                "cap10": on_census10.get("total_cap_hit_questions"),
                "cap15": on_census15.get("total_cap_hit_questions")},
            "injected_total": {
                "cap10": on_census10.get("injected_total"),
                "cap15_on": on_census15.get("injected_total"),
                "cap15_inj_only": inj_census15.get("injected_total")},
            "dropped_by_cap": {
                "cap10": on_census10.get("dropped_by_cap"),
                "cap15_on": on_census15.get("dropped_by_cap"),
                "cap15_inj_only": inj_census15.get("dropped_by_cap")},
            "recall_all@5_on": {"cap10": _mean0(on10), "cap15": _mean0(on15)},
            "session_recall@5_on": {
                "cap10": _mean0(on10, "session_recall@5"),
                "cap15": _mean0(on15, "session_recall@5")},
            "per_question_movement": None if sweep_on is None else {
                "improved": sweep_on["improvements"],
                "regressed": sweep_on["regressions"],
                "regressed_total_cap_hit_at_cap15": {
                    q: ((_pq(per_q, 15, "on") or {}).get("total_cap_hit")
                        or {}).get(q)
                    for q, *_ in sweep_on["regressions"]}},
        },
        "3_cost_guard": {
            "context_tokens_mean": {
                "cap10_off": _r1(_arm(caps, 10, "off").get("context_tokens_mean")),
                "cap10_on": _r1(_arm(caps, 10, "on").get("context_tokens_mean")),
                "cap15_on": _r1(_arm(caps, 15, "on").get("context_tokens_mean"))},
            "reader_token_cap": reader_token_cap,
        },
        "4_inertness_and_isolation": {
            "fetch_ok": _ratio(on_census10.get("fetch_ok_questions"),
                               on10.get("n_questions")),
            "seeded_sessions": on_census10.get("seed_sessions_total"),
            "injected_total_merged": merged_at_cap10(on_census10),
            "off_vs_off_b_ranked_ids_identical": (
                _ratio(*off_off_b) if off_off_b else None),
            "off_vs_off_b_graded_metric_flips": graded_flips(10, "off", 10, "off_b"),
            "cap10_off_vs_cap15_off_top5_identical": (
                _ratio(*off_cap15) if off_cap15 else None),
            "cap10_off_vs_cap15_off_graded_metric_flips": graded_flips(10, "off", 15, "off"),
        },
        "5_sample_vs_class": {
            "n20_subset_of_this_run": {
                "off": _subset_recall_all(_pq(per_q, 10, "off"), n20),
                "on": _subset_recall_all(_pq(per_q, 10, "on"), n20)},
            "n71_full_class": {"off": _mean0(_arm(caps, 10, "off")),
                               "on": _mean0(_arm(caps, 10, "on"))},
        },
        "6_evidence_surface_guardrail": {
            "evidence_recall@5": {
                "off": _mean0(_arm(caps, 10, "off"), "evidence_recall@5"),
                "on_cap10": _mean0(on10, "evidence_recall@5"),
                "on_cap15": _mean0(on15, "evidence_recall@5")},
            "evidence_recall@5_flips": _flip_str(
                _flips_of(per_q, 10, "off", 10, "on", "evidence_recall@5")),
            "reader_surface@5": {"off": _arm(caps, 10, "off").get("reader_surface@5"),
                                 "on_cap10": on10.get("reader_surface@5")},
            "reader_surface@5_flips": _flip_str(
                _flips_of(per_q, 10, "off", 10, "on", "reader_surface@5")),
        },
    }


# Figures a verdict cites that are NOT derivable from THIS receipt's `results`.
# Kept as explicit literals with their provenance so they cannot be mistaken for
# measured values (and so `main()` and `--rederive` cannot drift apart).
CITED_NOT_MEASURED = {
    "5_sample_vs_class": {
        "prior_receipt_n20": {
            "value": {"off": 0.65, "on": 0.90},
            "provenance": ("literal, read at seal from the superseded receipt "
                           "(see `supersedes`); that artifact is NOT at HEAD "
                           "or on origin/main, so it cannot be re-derived "
                           "here"),
        },
    },
}


def _attach_measured(verdicts, measured):
    """Put the DERIVED `measured` block first in each verdict, replacing any
    literal that was there, and attach the shared `cited_not_measured`
    provenance for figures that cannot be derived from `results`."""
    out = {}
    for key, block in verdicts.items():
        block = {k: v for k, v in block.items() if k != "measured"}
        if key in measured:
            block = {"measured": measured[key], **block}
        if key in CITED_NOT_MEASURED:
            block["cited_not_measured"] = CITED_NOT_MEASURED[key]
        out[key] = block
    return out


def rederive(src, dest):
    """Rebuild an EXISTING receipt canonically from its own recorded blocks.

    The raw arm artifacts are gone, so a normal run cannot reproduce the
    census. `results` is the surviving measurement record, though, so the
    receipt is rebuilt by the SAME `build_receipt` a raw run uses — every
    derived block from `results`, every prose/static field (and the
    branch/revision provenance) from the receipt itself or this generator's
    literals. That keeps the artifact and the generator from drifting: one
    producer.
    """
    receipt = json.loads(Path(src).read_text())
    caps = receipt.get("results") or {}
    per_q = {f"{cap}_{arm}": block.get("per_question") or {}
             for cap, arms in caps.items() for arm, block in arms.items()}
    retrieval_mode = receipt.get("retrieval_mode") or {}
    out = build_receipt(
        caps, per_q, receipt.get("run_integrity") or {}, retrieval_mode,
        retrieval_mode.get("reader_token_cap"), receipt.get("cohort") or {},
        {"branch": receipt.get("branch"),
         "raw_root": (((receipt.get("staleness") or {})
                       .get("raw_artifacts") or {}).get("declared_root")),
         "revision_measured": receipt.get("revision_measured")})
    write_receipt(out, dest)


def load_raw_measurement():
    """Read the arm artifacts once and return (caps, per_q, run_integrity,
    retrieval_mode, token_cap). This is the ONLY path that needs the raw
    artifacts; `--rederive` fills the same five values in from an existing
    receipt's own blocks, so both paths feed the SAME `build_receipt`."""
    caps = {}
    for cap in (10, 15, 20):
        arms = {}
        for a in ARMS:
            d = load(cap, a)
            if d:
                arms[a] = arm_block(d)
        if arms:
            caps[str(cap)] = arms
    per_q = {f"{cap}_{a}": arm_block(d)["per_question"]
             for cap in (10, 15, 20)
             for a in ARMS if (d := load(cap, a))}
    off10 = load(10, "off") or {}
    method = off10.get("methodology", {})
    run_integrity = {
        f"cap{cap}_{a}": {
            "n_questions": len(load(cap, a).get("outcomes") or []),
            "n_failed": load(cap, a).get("n_failed"),
            "n_excluded": load(cap, a).get("n_excluded"),
            "integrity_valid": (load(cap, a).get("integrity") or {}).get("valid"),
            "ingest_latency_ms_mean": round(statistics.mean(
                [o["ingest_latency_ms"] for o in load(cap, a)["outcomes"]
                 if o.get("ingest_latency_ms") is not None]), 1),
            "ingest_cached_field_present": any(
                "ingest_cached" in o for o in load(cap, a)["outcomes"]),
        }
        for cap in (10, 15, 20) for a in ARMS if load(cap, a)}
    retrieval_mode = {
        "retriever": "hybrid (fts + vector, RRF-fused)",
        "checkpoint_key": method.get("checkpoint_key"),
        "leg_mix_observed": LEG_MIX_OBSERVED,
        "embedder": method.get("embedder") or "BAAI/bge-small-en-v1.5 (384-dim)",
        "reader_item_cap": method.get("context_item_cap"),
        "reader_token_cap": method.get("context_token_cap"),
    }
    return (caps, per_q, run_integrity, retrieval_mode,
            method.get("context_token_cap"))


def provenance_from_environment():
    """The branch/revision/raw-root provenance a RAW run records, read from the
    measured worktree (WT) and the environment. `--rederive` carries the
    receipt's recorded values through instead, so it never needs WT to exist,
    never depends on `$LME_RAW`, and can never rewrite provenance."""
    return {
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "raw_root": str(RAW),
        "revision_measured": {
            "sha": SHA,
            "subject": git("log", "-1", "--format=%s", SHA),
            "tree": "clean at launch",
            "verified_by": ("tools/longmem_eval/guard_measured_revision.py "
                            f"--rev {SHA} --paths tortoise/ tools/ -> rc=0 after "
                            "removing 88 pre-existing __pycache__ byte-caches "
                            "(guard class 16, #3712); all runs launched with "
                            "-B + PYTHONDONTWRITEBYTECODE=1"),
        },
    }


def build_receipt(caps, per_q, run_integrity, retrieval_mode, token_cap,
                  cohort_block, provenance):
    """Assemble the WHOLE receipt from the measurement-derived `caps`/`per_q`
    plus the (raw- or receipt-sourced) integrity/methodology/cohort blocks.

    Every data block AND every prose field is produced HERE, so a `--rederive`
    and a raw run of the same measurement are byte-identical — there is only
    one producer, and no prose field can be stranded by a partial refresh."""
    measured = derive_falsifier_measured(caps, per_q, token_cap)
    out = {}
    # arm-vs-arm flips per cap
    cap_flips = {}
    for cap in caps:
        if "off" in caps[cap]:
            for a in ("inj_only", "on"):
                if a in caps[cap]:
                    cap_flips[f"cap{cap}_{a}_vs_off"] = {
                        m: flips(per_q[f"{cap}_off"][m], per_q[f"{cap}_{a}"][m])
                        for m in ("session_recall@5", "evidence_recall@5",
                                  "reader_surface@5")}
    # cap-to-cap flips per injected arm
    cap_sweep = {}
    for a in ("inj_only", "on"):
        if f"10_{a}" in per_q and f"15_{a}" in per_q:
            cap_sweep[f"{a}_cap15_vs_cap10"] = {
                m: flips(per_q[f"10_{a}"][m], per_q[f"15_{a}"][m])
                for m in ("session_recall@5", "evidence_recall@5",
                          "reader_surface@5", "context_tokens")}
            c10, c15 = caps["10"][a]["sr_census"], caps["15"][a]["sr_census"]
            cap_sweep[f"{a}_cap15_vs_cap10"]["census_delta"] = {
                k: c15[k] - c10[k] for k in
                ("injected_total", "injected_merged", "dropped_by_cap",
                 "total_cap_hit_questions")}
            cap_sweep[f"{a}_cap15_vs_cap10"]["metrics_delta"] = {
                m: [round(caps["15"][a][m][0] - caps["10"][a][m][0], 4)]
                for m in ("recall_all@5", "session_recall@5",
                          "evidence_recall@5")}
    repro = {}
    for pair, (k1, k2) in {"cap10_off_vs_off_b": ("10_off", "10_off_b"),
                           "cap10_off_vs_cap15_off": ("10_off", "15_off")}.items():
        if k1 in per_q and k2 in per_q:
            r1, r2 = per_q[k1]["ranked_ids_top5"], per_q[k2]["ranked_ids_top5"]
            repro[pair] = {
                "top5_identical": [sum(1 for q in r1 if r1.get(q) == r2.get(q)),
                                   len(r1)],
                **{m: flips(per_q[k1][m], per_q[k2][m])
                   for m in ("session_recall@5", "evidence_recall@5",
                             "reader_surface@5", "context_tokens")},
                "has_off_b": k2.endswith("off_b"),
            }
    out.update({
        "epic": 2513,
        "issue": 2513,
        "kind": ("retrieval-only re-measurement of the C4 re-injection arm at "
                 "POINTKIND='event' turn grain over the WHOLE multi-session "
                 "class of the s[150:250] tail, plus a TOTAL-BUDGET cap sweep"),
        "branch": provenance.get("branch"),
        "revision_measured": provenance.get("revision_measured"),
        # ⛔ STALENESS. The census below is a HISTORICAL record of ONE revision,
        # never a claim about current main. 412470cd3 is not on origin/main and
        # never was, the module the arm measures is absent from main entirely,
        # and the raw arm artifacts are gone — so the receipt is NOT current and
        # its CENSUS is not regenerable (only the falsifier `measured` blocks
        # are, from `results`; `--rederive`). Recorded in the artifact itself so the
        # next reader cannot quote these numbers as current.
        "staleness": {
            "status": ("STALE-BY-DESIGN \u2014 a sealed historical measurement of ONE "
                       "revision, not a statement about current main"),
            "caveat": (
                "EVERY NUMBER IN THIS RECEIPT DESCRIBES REVISION 412470cd3 AND "
                "NOTHING ELSE; it is NOT a current-main result and must not be "
                "quoted as one. 412470cd3 is the measurement tip of branch "
                "fix/2513-retrieval-evidence, it is NOT an ancestor of "
                "origin/main (`git merge-base --is-ancestor 412470cd3 "
                "origin/main` exits non-zero), and the module this arm measures "
                "\u2014 tortoise/session_reinjection.py (472 lines at 412470cd3) "
                "\u2014 is ABSENT from origin/main entirely. The headline figures "
                "(session_recall@5 OFF 0.831 / ON-cap10 0.9225; evidence_recall@5 "
                "OFF 0.527 / ON-cap10 0.4706) are therefore the record of what "
                "an UNMERGED arm did at 412470cd3 on 2026-09-16, not a "
                "measurement of the tree anyone can check out from main today."),
            "measured_at": "2026-09-16T22:44:49-05:00",
            "measured_revision": SHA,
            "measured_branch": "fix/2513-retrieval-evidence",
            "open_pr_carrying_the_measured_revision": 3577,
            # LITERAL seal-time facts, captured at seal (2026-09-21) — NOT
            # derived from the measurement and NOT recomputed on a re-run.
            "verified_at_seal": SEAL_VERIFICATION,
            "measured_surface_absent_from_origin_main": [
                "tortoise/session_reinjection.py",
                "tools/longmem_eval/guard_measured_revision.py",
                "tools/longmem_eval/build_cohorts.py",
            ],
            "raw_artifacts": {
                "declared_root": provenance.get("raw_root"),
                "state_at_seal": (
                    "GONE \u2014 the directory does not exist, so the generator "
                    "can no longer read cap{10,15}/<arm>.json and THE CENSUS "
                    "BELOW CANNOT BE REGENERATED. It is the only surviving "
                    "record of this census; only the derived blocks remain "
                    "recomputable from `results` itself (`--rederive`): each "
                    "falsifier's `measured`. `verified_at_seal` is NOT "
                    "derivable from `results` — it is a seal-time literal "
                    "re-emitted verbatim. /tmp/lme-v2-p100.json (the 100-Q profile "
                    "whose '53% of misses' figure titles issue #2513) is also "
                    "gone, so that issue's cited evidence no longer exists on "
                    "disk either."),
            },
            "cohort_anchor": (
                "STILL VALID: the cohort file named in `cohort.file` is present "
                "and its sha256 still equals `cohort.sha256`, so the gold labels "
                "behind the (A)/(B) split remain checkable even though the arm "
                "artifacts do not."),
            "still_derivable_from_this_artifact": [
                "The (A)/(B)/(C) split of the cap10 ON residual re-derives from "
                "`per_question` alone: A=1 (ba358f49 \u2014 gold session 28 "
                "contributes 0 rows to the ranked pool), B=9, C=0.",
                "The OFF-arm identity across servers (off / off_b) and across "
                "caps, as recorded in `reproducibility`.",
            ],
        },
        "substrate": {
            "embedder": "BAAI/bge-small-en-v1.5 (384-dim, local "
                        "SentenceTransformer) — the real embedder; retrieval "
                        "mode is HYBRID (fts + vector + rrf), NOT keyword-only",
            "graph": ("one DEDICATED docker FalkorDB per arm "
                      "(falkordb/falkordb@sha256:adbddd418916c25618564ff859"
                      "7a919b08bc76452ebeb74eb985c38d7281df62, "
                      "REDIS_ARGS='--requirepass falkordb --save \"\" "
                      "--maxmemory 6gb --maxmemory-policy noeviction "
                      "--activedefrag yes'), 127.0.0.1:6391-6397"),
            "ingest": ("ingest_mode=deterministic (turn points pointKind="
                       "'event' + [role] content + has_answer, plus raw "
                       "session-transcript chunks); NO LLM extraction. The "
                       "#2080 ingest cache is INERT in this lane by "
                       "construction (run.py: _cache_armed requires "
                       "ingest_mode=='v2'), so the premise 'ingest_cached=true"
                       " pays ingest once' does NOT hold here and was NOT "
                       "used; every arm pays its own deterministic ingest "
                       "(measured below)."),
            "reader": "mock (--retrieval-only --mock): no reader LLM is called",
        },
        "cohort": cohort_block,
        "arm_flags": {
            "off": "(none)",
            "off_b": "(none) — a SECOND OFF run on a second dedicated server, "
                     "the cross-server ingest/reproducibility control",
            "inj_only": "--session-reinjection --no-session-reinjection-guard",
            "on": "--session-reinjection",
            "confounded_arms_pinned_off": ("coverage_loop (C3-1), entity_key_"
                                           "expansion (#2518), rerank (R6), "
                                           "evidence_boost (C2), aggregative_"
                                           "flag (C5), evidence_assembly (A6) "
                                           "— all OFF (defaults)"),
        },
        "caps": {
            "knob": ("TORTOISE_LME_REINJECTION_TOTAL_CAP (eval-side, "
                     f"committed in {SHA})"),
            "product_constant": 10,
            "per_session_cap": 3,
            "seed_sessions": 5,
            "structural_fan_out": 15,
            "settings_run": sorted(int(c) for c in caps),
            "reachability": ("total_cap_hit is REACHABLE only below the "
                             "structural fan-out (5 seeds x 3 per session = "
                             "15); at 15 the total budget stops being the "
                             "binding guard and total_cap_hit fires only if a "
                             "16th distinct CANDIDATE row exists"),
        },
        "run_integrity": run_integrity,
        "retrieval_mode": {**retrieval_mode, "leg_mix_observed": LEG_MIX_OBSERVED},
        "miss_class_closure": _closure(caps, per_q),
        "results": caps,
        "arm_vs_arm_flips": cap_flips,
        "cap_sweep": cap_sweep,
        "reproducibility": repro,
        "derivation": {
            "recall_all@5": "mean over questions of (session_recall@5 == 1.0)",
            "gold_sessions_in_pool": "per-question map {gold-session haystack index: did that session contribute >=1 row to THIS arm's RANKED candidate pool (the arm's ranked_ids, whose head is ranked_ids_top5 — NOT the ingested point count recorded as pool_size)?}; a False entry is the (A) not-reachable class",
            "gold_session_best_rank": "per-question map {gold-session haystack index: best (smallest, 0-based) rank of any of that session's pool rows, null == ABSENT from the pool}; >= 5 with every gold session in-pool is the (B) reachable-but-dropped class",
            "session_recall@5": "per-question mean of outcome session_recall@k['5']",
            "evidence_recall@5": "per-question mean of outcome evidence_recall@k['5']",
            "reader_surface@5": "per-question mean of outcome reader_surface@k['5']",
            "context_tokens": "per-question mean of outcome context_tokens",
            "chunk_evidence_recall@5": "report.retrieval aggregate (null under --retrieval-only --mock)",
            "sr_census": "sum over questions of session_reinjection_stats",
        },
        "command": ("bash /tmp/lme2513/arm.sh <port> <arm> <cap> ms71 "
                    "<cohort.json>  (one dedicated FalkorDB server per arm; "
                    "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m "
                    "tools.longmem_eval.run --data <cohort.json> --split s "
                    "--retrieval-only --mock --output <arm>.json --checkpoint "
                    "<arm>.cp.json [arm flags])"),
        "falsifier_verdicts": {
            "1_miss_class_closure": {
                "verdict": "PARTIAL CLOSURE, AND THE GUARD IS THE ENTIRE MOVER. "
                           "At the shipped cap the arm converts 13 of the 23 "
                           "multi-session questions OFF misses (56.5%) into "
                           "FULL session recall, with ZERO session_recall@5 "
                           "regressions (+13/-0); recall_all@5 0.6761 -> "
                           "0.8592 (+0.1831). It does NOT close the class: 10 "
                           "questions remain missed. The guard-ablated arm "
                           "closes NOTHING (23 missed, session_recall@5 "
                           "identical to OFF on all 71 questions), so the "
                           "closure is attributable to the session-diverse "
                           "re-order, not to the added material.",
            },
            "2_cap_sweep_truncation": {
                "verdict": "0.8592 IS NOT A TRUNCATION LOWER BOUND. Raising the "
                           "total cap 10 -> 15 cuts total_cap_hit from 30/71 "
                           "(42.3%) to 6/71 (8.5%) and admits ~80 more "
                           "injected-and-merged items, yet the delta does NOT "
                           "move UP — it moves marginally DOWN (recall_all@5 "
                           "0.8592 -> 0.8451). The truncation was not what "
                           "limited the arm; the shipped cap is not "
                           "suppressing it, and on this cohort the extra "
                           "injected volume is (weakly, on one question) "
                           "harmful — consistent with rank displacement / "
                           "context dilution at a saturated reader window.",
            },
            "3_cost_guard": {
                "verdict": "NO CONTEXT-TOKEN REGRESSION at the shipped cap "
                           "(7795.5 -> 7799.2, +3.7 tokens; the window is "
                           "already 97.4% of the 8000-token cap in EVERY arm, "
                           "so the reader is saturated before any injection). "
                           "At cap=15 the token mean DROPS (7768.6) because "
                           "the extra injected items displace base items. The "
                           "arm's own block latency was measured at run time "
                           "but is NOT in any surviving artifact and is not "
                           "re-derivable from this receipt, so it is "
                           "deliberately not cited here — process-level "
                           "retrieval latency is NOT a cost proxy in this "
                           "campaign (host contention).",
            },
            "4_inertness_and_isolation": {
                "verdict": "NOT INERT: the fetch fires on every question with "
                           "seeds (71/71 fetch_ok), seeds 354 sessions and "
                           "injects-and-merges 617 items (617/617) at cap=10, "
                           "both injected arms. ISOLATION IS PROVEN, NOT "
                           "ASSUMED: two OFF runs on two dedicated servers "
                           "produce byte-identical ranked_ids on 71/71 "
                           "questions (the deterministic ingest is "
                           "reproducible across servers), and the cap=10 and "
                           "cap=15 OFF runs are metric-identical on all 71. "
                           "This run does NOT reproduce the harness-side "
                           "pool-order instability the superseded receipt "
                           "documented.",
            },
            "5_sample_vs_class": {
                "verdict": "THE n=20 SAMPLE OVERSTATED THE CLASS RATE BY 4.1 "
                           "POINTS. On the first 20 questions of this cohort "
                           "the run reproduces the superseded receipt EXACTLY "
                           "(OFF 0.65 -> ON 0.90); on the full 71-question "
                           "class the same arm gives OFF 0.6761 -> ON 0.8592. "
                           "A sample fixes the point estimate it measured; it "
                           "does not establish the class rate, and here it did "
                           "not.",
            },
            "6_evidence_surface_guardrail": {
                "verdict": "PARTIALLY FALSIFIED (declared structural "
                           "guardrail, confirmed). evidence_recall@5 does NOT "
                           "rise: 0.527 -> 0.4706 (+4/-11 questions), and "
                           "reader_surface@5 does not rise either "
                           "(0.9436 -> 0.9289, +0/-2). By construction the "
                           "guard caps a session at 2 inside the window, so a "
                           "top-5 holding 3+ marked points from one session "
                           "loses marked points — the session-level closure "
                           "is bought partly with item-level evidence. Net "
                           "recall_all@5 still gains, so the trade is "
                           "favourable at the graded metric.",
            },
        },
        "acknowledged_limitations": [
            "COHORT IS THE WHOLE MULTI-SESSION CLASS OF THE DESIGNATED TAIL "
            "SLICE, NOT OF THE BENCHMARK. n=71 is every multi-session question "
            "in s[150:250]; the S split holds 133 multi-session questions, so "
            "62 lie outside the designated tail and were NOT measured. The "
            "n=20 sample this supersedes is a strict prefix (ms10 == "
            "ms_tail[0:10], ms20 == ms_tail[10:20]).",
            "A SAMPLE DOES NOT ESTABLISH A CLASS RATE. The superseded n=20 "
            "sample's ON 0.90 becomes 0.8592 on the full class (measured here "
            "on the same revision); the +0.25 sample delta becomes +0.1831 on "
            "the class. Neither number is a confidence interval — no CI is "
            "computed here.",
            "THE READER IS MOCK (--retrieval-only --mock): no reader LLM is "
            "called, so chunk_evidence_recall@5 is null, the raw-chunk reader "
            "surface is UNMEASURED, and NO end-to-end answer accuracy is "
            "measured. reader_surface@5 scores the retrieval-assembled "
            "context, not a reader answer.",
            "THE 'ingest_cached=true PAYS INGEST ONCE' PREMISE DOES NOT HOLD IN "
            "THIS LANE. run.py's _cache_armed requires ingest_mode == 'v2'; "
            "this campaign is ingest_mode='deterministic' (as the superseded "
            "campaign was), so the #2080 ingest cache is INERT: no outcome "
            "carries ingest_cached and EVERY arm paid its own ~141 s/question "
            "deterministic ingest. Ingest was NOT paid once. Switching to v2 "
            "would have changed the substrate (LLM-extracted graph) and broken "
            "comparability with the superseded receipt.",
            "'ONE DB PER ARM' IS IMPLEMENTED AS ONE SERVER PER ARM, because it "
            "is the only mechanism that actually isolates: the graph NAME is "
            "namespace-derived (team_default__default__<qid>) and the "
            "docker:// URI path is DECORATIVE, so three different DB names on "
            "one server share graph names and are isolated only by a "
            "per-question wipe. Each arm here ran against its own dedicated "
            "container (same image digest/config), which is why the "
            "off-vs-off_b identity is meaningful.",
            "cap=20 — a plateau control ABOVE the structural fan-out "
            "(5 seeds x 3 per session = 15), where total_cap_hit must be "
            "False by construction — was NOT run (budget). The sweep has "
            "exactly two settings, 10 (shipped) and 15 (max reachable).",
            "PROCESS-LEVEL LATENCY IS NOT A COST PROXY IN THIS CAMPAIGN. The "
            "host was under extreme fleet contention (1-min load 110-230 on 10 "
            "CPUs; 5+ concurrent pi sessions plus Chrome/OrbStack); ingest "
            "measured 141 s/question versus 28 s for the same work in a "
            "low-load 3-question smoke. The isolated SR-block latency was "
            "recorded only in this receipt's original narrative; it is NOT in "
            "any surviving artifact and cannot be re-derived, so it is not "
            "cited as evidence here.",
            "THE CAP-15 REGRESSION RESTS ON ONE QUESTION (d3ab962e, "
            "session_recall@5 1.0 -> 0.5). The defensible claim is 'the delta "
            "does not improve when the cap is raised', NOT 'raising the cap "
            "hurts' — one question cannot carry an effect size.",
            "--retrieval-only --mock exits rc=1 in _print_summary "
            "(pre-existing bug, TypeError on acc['overall']); the report JSON "
            "is written before that, so every artifact here is complete "
            "(n=71, n_failed=0, integrity.valid=true in all 7 runs).",
            "A pre-existing FLAKY test was observed while verifying this "
            "change: tests/test_eval_retrieval_budget.py::"
            "test_deadline_degradation_records_timeout_reason failed once "
            "(redislite socket in a tmpdir) and passed on rerun; it does not "
            "touch the re-injection arm. Recorded, not filed (category B: "
            "test flakiness of the machinery, nothing but time lost).",
            "The 88 pre-existing __pycache__ byte-caches under tortoise/ and "
            "tools/ were removed before the guard would pass (guard class 16, "
            "#3712). All runs were launched with -B + "
            "PYTHONDONTWRITEBYTECODE=1 so the measured surface stayed "
            "byte-code-free.",
            "R2 (RESIDUAL, NOT VERDICT-CHANGING): the recorded `command` uses `--checkpoint <arm>.cp.json` with NO cap component in the filename. Under the PRE-fix revision the cap was not part of the checkpoint fingerprint, so the cap-15 arms were RESUME-ELIGIBLE against the cap-10 checkpoint file \u2014 a resume would have blended two injection volumes into one artifact while it declared one config. It is refuted in fact, not by construction: the recorded census still separates the arms (cap10 on dropped_by_cap 879 / total_cap_hit_questions 30 vs cap15 on 823 / 6, i.e. the cap-15 arms really did admit MORE volume and trip the total budget LESS often, which a wholesale resume of cap-10 outcomes could not produce), and every arm paid its own ~141 s/question deterministic ingest (the #2080 ingest cache is inert in this lane), so a full resume was not what happened. The numbers are therefore NOT wholesale-blended. The delta review's fix DOES close this class going forward: the resolved cap now rides the checkpoint fingerprint, so the pre-fix resume this limitation describes is now REFUSED by `CheckpointStaleError`. CAVEAT: the seam test that pins that end to end, `tests/test_eval_reinjection_cap_resume.py`, is NOT at HEAD or on origin/main either — it exists only on the unmerged commit 195c4186a9db95dff976360892f1f9ea8613e8f5 (branch fix/2513-retrieval-evidence, PR #3577), so read it by commit.",
        ],
        "supersedes": ("the sample this run supersedes: docs/scoping/receipts/"
                       "2026-09-16-2517-reinjection-turn-c1e6f7f2d.json "
                       "(n=20 = ms_tail[0:20], pooled recall_all@5 OFF 0.65 -> "
                       "ON 0.90, total_cap_hit 7/20, cap=10 only). CAVEAT: "
                       "that path is NOT at HEAD or on origin/main — it exists "
                       "only on the unmerged branch fix/2513-retrieval-evidence "
                       "(PR #3577), which first added it at commit "
                       "8f1af7f62a922c321f49c774e5f7dfba70e1c9a6 (blob "
                       "e673d855e02b7db2483d813802d42e99b348d4df at that "
                       "commit; blob 74b14069306e2d634ad6df9a75c17f681742eedd "
                       "at the branch tip). Read it by commit, not by path."),
    })
    # `measured` is DERIVED here from `caps`/`per_q`, never a literal: this is
    # what keeps every falsifier's `measured` block in agreement with `results`.
    out["falsifier_verdicts"] = _attach_measured(
        out["falsifier_verdicts"], measured)
    return out


def cohort_block():
    """The cohort provenance block. Read from the cohort file only when a raw
    run builds the receipt; `--rederive` passes the recorded block straight
    through so it needs no cohort cache."""
    n = len(cohort_list())
    return {
        "file": str(COHORT),
        "sha256": sha256(COHORT),
        "selector": ("instances[150:250], question_type == 'multi-session',"
                     " in source order (the WHOLE class; 71 of the 100 tail"
                     " questions)"),
        "source": "longmemeval_s_cleaned.json sha256="
                  "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
                  " (verified against SPLIT_DIGESTS['s'])",
        "provenance_sidecar": str(PROV),
        "n": n,
        "composition": {"multi-session": n},
        "built_by": "tools/longmem_eval/build_cohorts.py --cohort ms_tail",
        "superset_of_prior_sample": ("ms10 == ms_tail[0:10] and ms20 == "
                                     "ms_tail[10:20] — the superseded "
                                     "receipt's n=20 sample is a strict "
                                     "PREFIX of this cohort"),
    }


def write_receipt(receipt, dest):
    Path(dest).write_text(json.dumps(receipt, indent=1, default=str) + "\n")
    print("wrote", dest)


def print_summary(receipt):
    caps = receipt["results"]
    cap_sweep = receipt["cap_sweep"]
    repro = receipt["reproducibility"]
    for cap, arms in sorted(caps.items(), key=lambda kv: int(kv[0])):
        print(f"\n=== cap {cap} ===")
        print(f"{'arm':<9}{'ra@5':>7}{'sr@5':>7}{'er@5':>7}{'rs@5':>7}"
              f"{'tok':>8}{'caphit':>7}{'inj':>5}{'drop':>6}{'q':>4}")
        for a, s in arms.items():
            print(f"{a:<9}{s['recall_all@5'][0]!s:>7}{s['session_recall@5'][0]!s:>7}"
                  f"{s['evidence_recall@5'][0]!s:>7}{s['reader_surface@5']!s:>7}"
                  f"{s['context_tokens_mean']!s:>8}"
                  f"{s['sr_census']['total_cap_hit_questions']:>7}"
                  f"{s['sr_census']['injected_total']:>5}"
                  f"{s['sr_census']['dropped_by_cap']:>6}"
                  f"{s['n_questions']:>4}")
    print("\n=== cap sweep (delta cap15 - cap10) ===")
    for k, v in cap_sweep.items():
        if "metrics_delta" in v:
            print(f"  {k}: recall_all@5 {v['metrics_delta']['recall_all@5']}, "
                  f"session_recall@5 {v['metrics_delta']['session_recall@5']}, "
                  f"evidence_recall@5 {v['metrics_delta']['evidence_recall@5']}, "
                  f"cap_hits {v['census_delta']['total_cap_hit_questions']}, "
                  f"improved {v['session_recall@5']['n_improved']}, "
                  f"regressed {v['session_recall@5']['n_regressed']}")
    print("\n=== reproducibility ===")
    for k, v in repro.items():
        print(f"  {k}: top5_identical {v['top5_identical']}, "
              f"sr@5 +{v['session_recall@5']['n_improved']}/-{v['session_recall@5']['n_regressed']}")


def main():
    caps, per_q, run_integrity, retrieval_mode, token_cap = load_raw_measurement()
    receipt = build_receipt(caps, per_q, run_integrity, retrieval_mode,
                            token_cap, cohort_block(),
                            provenance_from_environment())
    dest = Path(os.environ.get(
        "LME_RECEIPT_DEST",
        str(WT / "docs/scoping/receipts" / (
            f"2026-09-17-2513-reinjection-capsweep-{SHA}.json"))))
    write_receipt(receipt, dest)
    print_summary(receipt)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rederive", metavar="RECEIPT",
        help=("rebuild an EXISTING receipt from its own recorded blocks "
              "instead of from arm artifacts, then write it back; each "
              "falsifier `measured` is recomputed from `results`, "
              "`verified_at_seal` is re-emitted from the seal-time literal"))
    parser.add_argument("--dest", metavar="PATH",
                        help="output path for --rederive (default: in place)")
    args = parser.parse_args()
    if args.rederive:
        rederive(args.rederive, args.dest or args.rederive)
    else:
        main()
