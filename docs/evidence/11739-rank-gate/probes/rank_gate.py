"""#11739 GATE 2 — rank distribution / top-k displacement. Sharded encoder + scorer.

Why two modes: the pinned bge-small in this process runs SINGLE-THREADED (measured,
100 % of one core, 16 idle), so the population encode is sharded across processes and
the scorer runs once over the assembled vectors.

  --mode texts    write the composed-text list (+ids) once           -> comp_texts.json
  --mode encode   --shard I --of N  encode slice I                   -> comp_vec_I.npy
  --mode measure  score every arm + the band-conditioned counts      -> report json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

K = 10
BAND_LO, BAND_HI = 0.70, 0.85
CHUNK = 1024
WORK = "/tmp/g2-work"
PREFIX_SAMPLE = 1500


def say(m: str) -> None:
    sys.stderr.write(m + "\n")
    sys.stderr.flush()


def live(row: dict) -> bool:
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES

    status = row.get("status")
    if isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES:
        return False
    return not row.get("outdated")


def population(cache: str):
    rows = [json.loads(line) for line in open(cache, encoding="utf-8")]
    live_rows = [r for r in rows if live(r)]
    from tortoise.embed_compose import compose_embedding_text, quote_text

    pop = [r for r in live_rows if quote_text(r.get("quote")) and r.get("embedding")]
    return rows, live_rows, pop


def encoder():
    import torch
    torch.set_num_threads(1)
    from tortoise.embeddings import EmbeddingModel
    m = EmbeddingModel.get()
    if m is None:
        say("GATE-NO-EMBEDDER")
        raise SystemExit(4)
    return m


def unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def encode_slice(model, texts: list[str], batch: int = 96) -> np.ndarray:
    import torch
    out = []
    with torch.inference_mode():
        for i in range(0, len(texts), batch):
            out.append(np.asarray(model.encode(texts[i:i + batch], batch_size=batch,
                                               show_progress_bar=False), dtype=np.float32))
    return unit(np.vstack(out)) if out else np.zeros((0, 384), np.float32)


def ranks_and_crowding(qvec: np.ndarray, dvec: np.ndarray) -> dict:
    n = dvec.shape[0]
    ranks = np.zeros(n, np.int64)
    sims = np.zeros(n, np.float32)
    ties = np.zeros(n, np.int64)
    for start in range(0, n, CHUNK):
        stop = min(start + CHUNK, n)
        s = qvec[start:stop] @ dvec.T
        selfsim = s[np.arange(stop - start), np.arange(start, stop)]
        greater = (s > selfsim[:, None]).sum(axis=1)
        ranks[start:stop] = greater + 1
        ties[start:stop] = (s == selfsim[:, None]).sum(axis=1)
        sims[start:stop] = selfsim
    return {"ranks": ranks, "sims": sims, "ties": ties}


def item_displacement(raw: np.ndarray, comp: np.ndarray, band_only: bool) -> dict:
    n = raw.shape[0]
    displaced = queries = considered = 0
    for start in range(0, n, CHUNK):
        stop = min(start + CHUNK, n)
        rs = raw[start:stop] @ raw.T
        cs = raw[start:stop] @ comp.T
        idx = np.arange(stop - start)
        rs[idx, np.arange(start, stop)] = -1.0
        cs[idx, np.arange(start, stop)] = -1.0
        rtop = np.argpartition(-rs, K, axis=1)[:, :K]
        ctop = np.argpartition(-cs, K, axis=1)[:, :K]
        for i in range(stop - start):
            rt = rtop[i]
            if band_only:
                rt = rt[(rs[i, rt] >= BAND_LO) & (rs[i, rt] < BAND_HI)]
            considered += len(rt)
            gone = np.setdiff1d(rt, ctop[i], assume_unique=False)
            if len(gone):
                queries += 1
                displaced += len(gone)
    return {"items_considered": int(considered), "items_displaced": int(displaced),
            "queries_with_displacement": int(queries)}


def hist(ranks: np.ndarray, sims: np.ndarray) -> dict:
    inband = (sims >= BAND_LO) & (sims < BAND_HI)
    return {
        "n": int(ranks.size),
        "rank_1": int((ranks == 1).sum()),
        "rank_2": int((ranks == 2).sum()),
        "rank_3": int((ranks == 3).sum()),
        f"rank_4_to_{K}": int(((ranks >= 4) & (ranks <= K)).sum()),
        f"out_of_top{K}": int((ranks > K).sum()),
        "median_rank": float(np.median(ranks)),
        "mean_rank": round(float(ranks.mean()), 4),
        "sim_mean": round(float(sims.mean()), 4),
        "sim_in_band_070_085": int(inband.sum()),
        "sim_lt_070": int((sims < BAND_LO).sum()),
        "sim_ge_085": int((sims >= BAND_HI).sum()),
        f"IN_BAND_AND_OUT_OF_TOP{K}": int((inband & (ranks > K)).sum()),
        f"in_band_and_in_top{K}": int((inband & (ranks <= K)).sum()),
        f"ge_085_and_out_of_top{K}": int(((sims >= BAND_HI) & (ranks > K)).sum()),
        f"lt_070_and_out_of_top{K}": int(((sims < BAND_LO) & (ranks > K)).sum()),
        f"ANY_out_of_top{K}": int((ranks > K).sum()),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--cache", default="/tmp/11739-rows.jsonl")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--out", default="-")
    a = ap.parse_args(argv[1:])

    if a.mode == "texts":
        rows, live_rows, pop = population(a.cache)
        from tortoise.embed_compose import compose_embedding_text
        texts = [compose_embedding_text(r.get("quote"), []) for r in pop]
        json.dump({"ids": [r["id"] for r in pop], "texts": texts,
                   "rows": len(rows), "live": len(live_rows)},
                  open(f"{WORK}/comp_texts.json", "w"))
        say(f"pop {len(pop)} distinct {len(set(texts))} rows {len(rows)} live {len(live_rows)}")
        return 0

    if a.mode == "encode":
        d = json.load(open(f"{WORK}/comp_texts.json"))
        texts = d["texts"]
        n = len(texts)
        lo = n * a.shard // a.of
        hi = n * (a.shard + 1) // a.of
        t = time.time()
        v = encode_slice(encoder(), texts[lo:hi])
        np.save(f"{WORK}/comp_vec_{a.shard}.npy", v)
        say(f"shard {a.shard}/{a.of} [{lo}:{hi}] {v.shape} in {time.time()-t:.0f}s")
        return 0

    if a.mode == "measure":
        d = json.load(open(f"{WORK}/comp_texts.json"))
        ids, texts = d["ids"], d["texts"]
        # ⛔ NUMERIC, not lexicographic: at >=11 shards `comp_vec_10` sorts BEFORE `comp_vec_2` in
        # plain string order, which would re-pair the composed vectors with the wrong ids and silently
        # corrupt every composed figure with no error. (The published run used exactly 10 shards,
        # 0-9, where the two orders agree — so this fixes a re-run, not the numbers.)
        parts = sorted(
            (f for f in os.listdir(WORK) if f.startswith("comp_vec_")),
            key=lambda f: int(f[len("comp_vec_"):-len(".npy")]),
        )
        comp = unit(np.vstack([np.load(f"{WORK}/{f}") for f in parts]))
        rows, live_rows, pop = population(a.cache)
        src_rows = {r["id"]: r for r in pop}
        pop = [src_rows[i] for i in ids]
        raw = unit(np.asarray([r["embedding"] for r in pop], np.float32))
        n = len(pop)
        say(f"assembled comp {comp.shape} from {len(parts)} shards; n={n}")

        m = encoder()
        probe_idx = list(range(0, n, max(1, n // 256)))[:256]
        content_vec = encode_slice(m, [pop[i].get("content") or "" for i in probe_idx])
        prov = float((content_vec * raw[probe_idx]).sum(axis=1).mean())

        say("A content-query raw")
        A_raw = ranks_and_crowding(raw, raw)
        say("A content-query composed")
        A_cmp = ranks_and_crowding(raw, comp)

        rng = np.random.default_rng(11739)
        qi = np.sort(rng.choice(n, size=min(PREFIX_SAMPLE, n), replace=False))
        pf = [" ".join((pop[i].get("content") or "").split()[
            :max(6, int(len((pop[i].get("content") or "").split()) * 0.25))]) for i in qi]
        say(f"encoding {len(qi)} prefix queries")
        pv = encode_slice(m, pf)
        say("B prefix-query raw")
        def rank_subset(qv, dv, idx):
            r = np.zeros(len(idx), np.int64); s = np.zeros(len(idx), np.float32)
            for start in range(0, len(idx), 256):
                blk = idx[start:start + 256]
                sc = qv[start:start + len(blk)] @ dv.T
                selfsim = sc[np.arange(len(blk)), blk]
                r[start:start + len(blk)] = (sc > selfsim[:, None]).sum(axis=1) + 1
                s[start:start + len(blk)] = selfsim
            return {"ranks": r, "sims": s}
        B_raw = rank_subset(pv, raw, qi)
        say("B prefix-query composed")
        B_cmp = rank_subset(pv, comp, qi)

        say("item displacement unbanded")
        d_all = item_displacement(raw, comp, False)
        say("item displacement band 0.70-0.85")
        d_band = item_displacement(raw, comp, True)

        rep = {
            "gate": "2 of 3 — rank distribution / top-k displacement",
            "rank_metric_from_issue": "#11739: 'count of items displaced from top-k' in the "
                                      "0.70-0.85 band (band named in the issue, k=10 from the product)",
            "k": K, "band": [BAND_LO, BAND_HI],
            "population": {"rows_in_cache": d["rows"], "live_rows": d["live"],
                           "live_with_quote_and_vector": n,
                           "distinct_composed_texts": len(set(texts)),
                           "encoder_provenance_mean_cosine_vs_stored_content": round(prov, 6)},
            "queries": {
                "A_content_query_full": {
                    "definition": "query = the point's own content (the raw arm's own index text); "
                                  "effective fact = that point; n = full population",
                    "raw": hist(A_raw["ranks"], A_raw["sims"]),
                    "composed": hist(A_cmp["ranks"], A_cmp["sims"]),
                },
                "B_prefix_query_sample": {
                    "definition": f"query = first 25% of the point's content words (arm-neutral, "
                                  f"seed 11739, n={len(qi)} of {n} queries, docs = full population)",
                    "raw": hist(B_raw["ranks"], B_raw["sims"]),
                    "composed": hist(B_cmp["ranks"], B_cmp["sims"]),
                },
            },
            "item_displacement": {
                "reference": "items in a query's RAW top-k (self excluded) missing from the "
                             "COMPOSED top-k; queries = full population",
                "unbanded": d_all, "band_070_085_only": d_band,
            },
            "duration_s": None,
            "probe_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
            "cache_sha256": hashlib.sha256(open(a.cache, "rb").read()).hexdigest(),
        }
        text = json.dumps(rep, indent=2) + "\n"
        if a.out == "-":
            sys.stdout.write(text)
        else:
            open(a.out, "w", encoding="utf-8").write(text)
            say(f"wrote {a.out}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
