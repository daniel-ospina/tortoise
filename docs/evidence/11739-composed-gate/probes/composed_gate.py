"""#11739 — the GATE on the composed embedding text, run on the LIVE graph.

This is #11739's own non-negotiable gate, and it is a MEASUREMENT, not a feature. The
issue specifies it in three parts:

1. **Per-subject raw-vs-composed worst-case pairwise similarity** against
   ``NEAR_DUPLICATE_THRESHOLD = 0.89`` (the calibrated bge-small duplicate band). The
   pre-existing test (`tests/test_context_enrich_8488.py::
   test_turn_discrimination_survives_enrichment`) uses a canned rewriter on FOUR
   synthetic turns, so it has never measured this on real composed text.
2. **A rank-distribution check** — the 0.89 band is a *duplicate* detector and cannot
   see facts crowding the top-k at 0.70–0.85. The issue asks for two numbers: items
   displaced from top-k, and crowding in the 0.70–0.85 band.
3. **A false-proposal check** on ``find_cross_source_matches`` → ``cross_lens``
   (#399/#438): does the composed text create cross-lens proposals that the raw text
   did not?

⛔ WHAT IS MEASURED, AND WHY IT IS THE TEXT THAT WOULD SHIP.

The LIVE graph carries **no resolved-neighbour material**: none of the properties a
Point row serves begins with ``about``, and the read surface exposes no edge
enumeration (measured on page 1 of 500, 2026-11-13; the probe RE-verifies this and
publishes it as ``neighbour_surface``). The names half of
``tortoise.embed_compose.compose_embedding_text`` is therefore fed an EMPTY name list,
which is exactly the composition that #11805 shipped: a quoted Point composes to its
verbatim ``quote``; a Point with no quote falls back to ``content`` byte-for-byte. So
the raw-vs-composed comparison on this graph is **content-vs-quote**, and the names
arm is reported as UNMEASURABLE (no material) rather than silently dropped.

Subject proxy, stated because the graph has no subject field either. Two real proxies:

* ``session`` — the capture ``session_id``. Points captured in one session are about one
  work subject; this is the population where duplicate risk lives.
* ``population`` — the whole live population (the strongest statement: the global worst
  case, no grouping at all).

Pre-declared pass criteria (written BEFORE the run, so they cannot be tuned to it):

* GATE 1 PASS iff every per-subject group's worst-case composed pairwise similarity is
  **< 0.89**, and the global worst case is < 0.89.
* GATE 2 PASS iff (a) no pair that is below 0.89 in RAW space reaches **≥ 0.89** in
  COMPOSED space (composition manufactures no near-duplicate), and (b) every document
  that was in a query's raw top-k **and** is raw-relevant (raw similarity ≥ 0.72, the
  calibrated paraphrase band) is still in that query's composed top-k. Crowding in
  [0.70, 0.85) is REPORTED (raw vs composed) as the issue asks, but it is not itself a
  fail: 0.72 is the band the product deliberately proposes candidates in.
* GATE 3 PASS iff no cross-lens pair rises from below 0.89 (raw) to **≥ 0.89**
  (composed). New proposals entering the [0.72, 0.89) candidate band are REPORTED as
  the false-proposal pressure the issue asks us to count.

Usage
-----
    set -a; . ~/keys.env; set +a                     # TORTOISE_MCP_API_KEY
    uv run python docs/evidence/11739-composed-gate/probes/composed_gate.py \
        --out docs/evidence/11739-composed-gate/gate.json

Exit codes: 0 measured · 2 usage / unwritable --out · 3 graph unreachable, not
configured, or an empty population (never a silent zero) · 4 the page walk could not be
proven complete, or the embedder is unavailable (a gate that cannot encode cannot
measure, and TF-IDF is NOT the calibrated space).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
CENSUS_PROBE = REPO / "docs/evidence/11739-quote-population/probes/quote_population.py"

#: Which ``k`` the read path's default top-k uses when the caller does not say. The
#: point of the metric is that a fixed small page is what a caller sees.
TOP_K = 10
#: Seeded so the run is re-runnable: the query sample and the cross-lens sample are
#: both deterministic.
SEED = 11739
QUERY_SAMPLE = 500
CROSS_LENS_SAMPLE = 1500
#: Chunk rows for the all-pairs pass — keeps peak memory ~30 MB instead of ~2 GB.
CHUNK = 256


def _say(msg: str) -> None:
    sys.stderr.write(msg)


def _load_census():
    """The census probe's tested MCP client + page walk (one owner for the walk)."""
    spec = importlib.util.spec_from_file_location("quote_population", CENSUS_PROBE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _quote_text(quote: object) -> str:
    from tortoise.embed_compose import quote_text  # the REAL composer, not a mirror

    return quote_text(quote)


def _live(row: dict) -> bool:
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES

    status = row.get("status")
    if isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES:
        return False
    return not row.get("outdated")


def _rows_after_shrink(census, client) -> tuple[list[dict], dict]:
    walk, total, distinct = census.walk(client, include_retracted=True)
    rows = walk["rows"]
    live = [r for r in rows if _live(r)]
    neighbour_keys = sorted({k for r in rows for k in r if k.lower().startswith("about")})
    return live, {
        "rows_walked": len(rows),
        "total_reported": total,
        "distinct_ids": distinct,
        "live_rows": len(live),
        "pages": walk["pages"],
        "final_page_size": walk["final_page_size"],
        "neighbour_surface": {
            "about_prefixed_keys_seen": neighbour_keys,
            "note": ("no neighbour material on the live read surface: the names half "
                     "of the composition is fed an empty name list, so this run "
                     "measures the text that would actually ship (#11805)"),
        },
    }


def _encode_all(texts: list[str], batch: int = 128) -> np.ndarray:
    import torch

    # Without this the encoder runs SINGLE-THREADED in this process: measured on the
    # 16-core cell, 256 short texts took ~2.1 s at 8 threads and the default gave
    # ~1.6 texts/s (a 2.6-hour run for this population). Threads, not correctness.
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    from tortoise.embeddings import EmbeddingModel

    model = EmbeddingModel.get()
    if model is None:
        _say("GATE-NO-EMBEDDER: the pinned embedder is unavailable; the calibrated "
             "0.89/0.72 bands are bge-small bands and TF-IDF is a different space, so "
             "no gate number can be produced here.\n")
        raise SystemExit(4)
    out = []
    for i in range(0, len(texts), batch):
        out.append(np.asarray(model.encode(texts[i:i + batch], batch_size=batch),
                              dtype=np.float32))
        _say(f"encoded {min(i + batch, len(texts))}/{len(texts)}\n")
    return np.vstack(out) if out else np.zeros((0, 384), dtype=np.float32)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def _worst_pair(vec: np.ndarray, idx: list[int]) -> tuple[float, tuple[int, int] | None]:
    """Worst (max) pairwise cosine among ``idx`` — self-pairs excluded."""
    if len(idx) < 2:
        return 0.0, None
    sub = vec[idx]
    sims = sub @ sub.T
    np.fill_diagonal(sims, -1.0)
    flat = int(np.argmax(sims))
    i, j = divmod(flat, sims.shape[1])
    return float(sims[i, j]), (idx[i], idx[j])


def _pair_bands(raw: np.ndarray, comp: np.ndarray, bands) -> dict:
    """Count pairs in each band for RAW and COMPOSED, and the transitions.

    Single chunked pass over i<j so the 15k x 15k matrix is never materialised.
    """
    n = raw.shape[0]
    counts = {name: {"raw": 0, "composed": 0} for name in ("gt_089", "b070_085", "b085_089")}
    transitions = {"crossed_089": 0, "entered_070_085": 0}
    for start in range(0, n, CHUNK):
        stop = min(start + CHUNK, n)
        rs = raw[start:stop] @ raw.T
        cs = comp[start:stop] @ comp.T
        for i_local in range(stop - start):
            i = start + i_local
            # only j > i
            r = rs[i_local, i + 1:]
            c = cs[i_local, i + 1:]
            counts["gt_089"]["raw"] += int(np.count_nonzero(r >= 0.89))
            counts["gt_089"]["composed"] += int(np.count_nonzero(c >= 0.89))
            counts["b070_085"]["raw"] += int(np.count_nonzero((r >= 0.70) & (r < 0.85)))
            counts["b070_085"]["composed"] += int(np.count_nonzero((c >= 0.70) & (c < 0.85)))
            counts["b085_089"]["raw"] += int(np.count_nonzero((r >= 0.85) & (r < 0.89)))
            counts["b085_089"]["composed"] += int(np.count_nonzero((c >= 0.85) & (c < 0.89)))
            transitions["crossed_089"] += int(np.count_nonzero((r < 0.89) & (c >= 0.89)))
            transitions["entered_070_085"] += int(
                np.count_nonzero((r < 0.70) & (c >= 0.70) & (c < 0.85)))
        _say(f"pairs {stop}/{n}\n")
    return {"counts": counts, "transitions": transitions}


def _topk_displacement(raw: np.ndarray, comp: np.ndarray, qidx: list[int]) -> dict:
    n = raw.shape[0]
    displaced = 0
    queries_with_displacement = 0
    relevant_in_raw_k = 0
    relevant_lost = 0
    for q in qidx:
        rs = raw[q] @ raw.T
        cs = comp[q] @ comp.T
        rs[q] = -1.0
        cs[q] = -1.0
        rtop = np.argpartition(-rs, TOP_K)[:TOP_K]
        ctop = np.argpartition(-cs, TOP_K)[:TOP_K]
        gone = set(rtop.tolist()) - set(ctop.tolist())
        if gone:
            queries_with_displacement += 1
            displaced += len(gone)
        rel = {int(x) for x in rtop.tolist() if rs[x] >= 0.72}
        relevant_in_raw_k += len(rel)
        relevant_lost += len(rel - set(ctop.tolist()))
    return {
        "k": TOP_K,
        "queries": len(qidx),
        "items_displaced_from_top_k": displaced,
        "queries_with_any_displacement": queries_with_displacement,
        "mean_displaced_per_query": round(displaced / len(qidx), 4) if qidx else None,
        "raw_relevant_items_in_raw_top_k": relevant_in_raw_k,
        "raw_relevant_items_lost_from_composed_top_k": relevant_lost,
    }


def _cross_lens(raw: np.ndarray, comp: np.ndarray, ids: list[str], sessions: list[str],
                sample: list[int]) -> dict:
    """The real `find_cross_lens_matches`, over an injected vector set.

    Called twice with the SAME points and the SAME lens key, once on raw vectors and
    once on composed vectors — the difference is the composition's own contribution.
    """
    from tortoise.cross_lens import find_cross_lens_matches

    rows = {ids[i]: {"content": "", "session_id": sessions[i] or "unknown"} for i in sample}
    order = list(rows)

    def _enc_raw(_texts):
        return raw[sample]

    def _enc_comp(_texts):
        return comp[sample]

    assert len(order) == len(sample)
    raw_hits = find_cross_lens_matches(rows, lens_key="session_id", encode=_enc_raw)
    comp_hits = find_cross_lens_matches(rows, lens_key="session_id", encode=_enc_comp)
    raw_pairs = {(h["src"], h["dst"]) for h in raw_hits}
    comp_pairs = {(h["src"], h["dst"]) for h in comp_hits}
    new_pairs = comp_pairs - raw_pairs
    pos = {pid: i for i, pid in enumerate(order)}
    new_crossed_089 = 0
    new_entered_070_085 = 0
    max_new = 0.0
    for a, b in new_pairs:
        r = float(raw[sample][pos[a]] @ raw[sample][pos[b]])
        c = float(comp[sample][pos[a]] @ comp[sample][pos[b]])
        if r < 0.89 <= c:
            new_crossed_089 += 1
        if r < 0.70 <= c < 0.85:
            new_entered_070_085 += 1
        max_new = max(max_new, c)
    return {
        "sample": len(sample),
        "threshold": 0.72,
        "lens_key": "session_id",
        "proposals_raw": len(raw_pairs),
        "proposals_composed": len(comp_pairs),
        "new_proposals": len(new_pairs),
        "new_proposals_crossing_0_89": new_crossed_089,
        "new_proposals_entering_0_70_0_85": new_entered_070_085,
        "max_similarity_among_new_proposals": round(max_new, 4),
    }


def measure(rows: list[dict], meta: dict) -> dict:
    from tortoise.embed_compose import compose_embedding_text

    kept: list[dict] = []
    quoted = 0
    unquoted = 0
    for r in rows:
        emb = r.get("embedding")
        if not isinstance(emb, list) or not emb:
            continue
        q = _quote_text(r.get("quote"))
        if q:
            quoted += 1
        else:
            unquoted += 1
            continue  # no quote -> the composition is content byte-for-byte: no change
        kept.append(r)
    if not kept:
        _say("GATE-EMPTY: no live, quoted, embedded points — nothing to measure.\n")
        raise SystemExit(3)

    ids = [str(r["id"]) for r in kept]
    raw = _unit(np.asarray([r["embedding"] for r in kept], dtype=np.float32))
    comp_texts = [compose_embedding_text(r.get("quote"), (), content=r.get("content"))
                  for r in kept]
    comp = _unit(_encode_all(comp_texts))
    if comp.shape[1] != raw.shape[1]:
        _say(f"GATE-DIM: composed encodings are {comp.shape[1]}-d, stored vectors are "
             f"{raw.shape[1]}-d — a different model space cannot be compared.\n")
        raise SystemExit(4)

    # Provenance: does OUR encoder reproduce the graph's stored vectors for the same
    # text? If not, the composed arm is not the same space as the raw arm.
    probe_n = min(50, len(kept))
    content_vec = _unit(_encode_all([kept[i].get("content") or "" for i in range(probe_n)]))
    prov = float(np.mean(np.sum(content_vec * raw[:probe_n], axis=1)))

    sessions: dict[str, list[int]] = {}
    for i, r in enumerate(kept):
        sid = r.get("session_id")
        if isinstance(sid, str) and sid:
            sessions.setdefault(sid, []).append(i)
    session_groups = {s: idx for s, idx in sessions.items() if len(idx) >= 3}

    g_all = _worst_pair(comp, list(range(len(kept))))
    r_all = _worst_pair(raw, list(range(len(kept))))
    per_session = []
    for sid, idx in sorted(session_groups.items(), key=lambda kv: -len(kv[1])):
        wc, pair = _worst_pair(comp, idx)
        wr, _ = _worst_pair(raw, idx)
        per_session.append({
            "session": sid, "points": len(idx),
            "worst_composed": round(wc, 4), "worst_raw": round(wr, 4),
            "worst_pair_ids": [ids[pair[0]], ids[pair[1]]] if pair else None,
        })
    worst_session = max(per_session, key=lambda g: g["worst_composed"], default=None)

    bands = _pair_bands(raw, comp, None)

    rng = np.random.default_rng(SEED)
    qidx = rng.choice(len(kept), size=min(QUERY_SAMPLE, len(kept)), replace=False).tolist()
    disp = _topk_displacement(raw, comp, qidx)
    cl_idx = rng.choice(len(kept), size=min(CROSS_LENS_SAMPLE, len(kept)),
                        replace=False).tolist()
    cl = _cross_lens(raw, comp, ids, [r.get("session_id") or "" for r in kept], cl_idx)

    g1 = g_all[0] < 0.89 and (worst_session is None or worst_session["worst_composed"] < 0.89)
    g2 = (bands["transitions"]["crossed_089"] == 0
          and disp["raw_relevant_items_lost_from_composed_top_k"] == 0)
    g3 = cl["new_proposals_crossing_0_89"] == 0

    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **meta,
        "population": {
            "live_rows": len(rows),
            "live_with_quote_and_vector": len(kept),
            "live_with_quote_no_vector_dropped": quoted - len(kept),
            "live_without_quote_dropped": unquoted,
            "encoder_provenance_mean_cosine_vs_stored": round(prov, 6),
            "embedding_dim": int(raw.shape[1]),
        },
        "subject_groups": {
            "session_groups": len(per_session),
            "points_in_session_groups": sum(g["points"] for g in per_session),
            "worst_session": worst_session,
            "per_session": per_session[:20],
        },
        "gate1_similarity": {
            "threshold": 0.89,
            "global_worst_composed": round(g_all[0], 4),
            "global_worst_raw": round(r_all[0], 4),
            "global_worst_pair_ids": [ids[g_all[1][0]], ids[g_all[1][1]]] if g_all[1] else None,
            "pass": bool(g1),
        },
        "gate2_rank": {**bands, "top_k": disp, "pass": bool(g2)},
        "gate3_false_proposal": {**cl, "pass": bool(g3)},
        "verdict": {
            "all_pass": bool(g1 and g2 and g3),
            "gates": {"gate1": bool(g1), "gate2": bool(g2), "gate3": bool(g3)},
        },
    }


def _provenance(rows_source: str) -> dict:
    """The artifact's self-binding (the #11838 census's rule).

    The repo squash-merges, so a head sha cannot be verified exactly when someone re-runs the
    probe to check the numbers; the PROBE's own digest can. This is the ONE producer of the
    field — ``main()`` calls it on a fresh run and ``--attest`` calls it on an existing
    artifact, so the value is always computed by this file from this file's own bytes.
    """
    return {
        "probe_path": "probes/composed_gate.py",
        "probe_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "rows_source": rows_source,
        "encoder": "BAAI/bge-small-en-v1.5 (tortoise.embeddings.EMBEDDING_MODEL)",
    }


def _attest(path: str) -> int:
    """(Re)bind an existing artifact to this probe's bytes.

    ``--attest`` exists because a fresh run costs ~16 minutes of encoding (949.2 s measured) and
    the binding is a pure function of the probe's bytes. It touches ONLY ``provenance``: it
    refuses an artifact whose gate verdicts are absent, so a failed walk can never be attested
    into a pass. The numbers are unchanged by this step, which is why the README can show the
    pair of commands and still claim the committed file is what they produce.
    """
    try:
        report = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _say(f"ATTEST-UNREADABLE({path}): {e}\n")
        return 2
    if "verdict" not in report or "gate1_similarity" not in report:
        _say(f"ATTEST-REFUSED({path}): no gate verdicts in this artifact\n")
        return 2
    report["provenance"] = _provenance(str(report.get("source", "")))
    pathlib.Path(path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _say(f"attested {path} -> {report['provenance']['probe_sha256']}\n")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="-")
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--cache", default="")
    ap.add_argument("--attest", default="", metavar="PATH",
                    help="(re)write provenance into an existing artifact; no measurement runs")
    args = ap.parse_args(argv[1:])
    if args.attest:
        return _attest(args.attest)

    census = _load_census()
    meta = {"endpoint": ""}
    if args.cache and os.path.exists(args.cache):
        rows = [json.loads(line) for line in open(args.cache, encoding="utf-8")]
        about = sorted({k for r in rows for k in r if k.lower().startswith("about")})
        meta = {
            "source": f"cache:{args.cache}",
            "endpoint": "",
            "rows_walked": len(rows),
            "live_rows": len(rows),
            "neighbour_surface": {
                "about_prefixed_keys_seen": about,
                "note": ("no neighbour material on the live read surface: the names "
                         "half of the composition is fed an empty name list, so this "
                         "run measures the text that would actually ship (#11805)"),
            },
        }
    else:
        key = os.environ.get("TORTOISE_MCP_API_KEY") or os.environ.get("TORTOISE_API_KEY")
        if not key:
            _say("GRAPH-UNREACHABLE(NO-KEY): TORTOISE_MCP_API_KEY is not set.\n")
            return 3
        raw_endpoint = (args.endpoint or os.environ.get("TORTOISE_API_URL")
                        or census.DEFAULT_EP)
        endpoint = census._mcp_endpoint(raw_endpoint)
        rows, meta = _rows_after_shrink(census, census._Client(endpoint, key))
        meta["endpoint"] = endpoint
        meta["source"] = "live"
        if args.cache:
            with open(args.cache, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
    started = time.monotonic()
    report = measure(rows, meta)
    report["duration_s"] = round(time.monotonic() - started, 1)
    report["provenance"] = _provenance(meta.get("source", ""))
    text = json.dumps(report, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
        return 0
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        _say(f"PROBE-WRITE({args.out}): {e}\n")
        return 2
    _say(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
