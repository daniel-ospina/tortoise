"""#11739 gate 3 of 3 — the FALSE-PROPOSAL check on `find_cross_source_matches` -> `cross_lens`.

This probe answers ONE question and stops: **does composing the embedding text from the
verbatim `quote` cause FALSE cross-source proposals?**

What a "false proposal" is (criterion, declared BEFORE the run — #399 / #438 wording):

  #399 defines the candidate surface as "point pairs from DIFFERENT lenses that MAY describe
  the same concept", and defines its similarity floor by measurement: the 0.72 band is the
  "cross-vocabulary paraphrase band" (same concept, said differently) and below it is the
  "unrelated / noise floor". #399 also states the motivating low-similarity pair is
  "topically similar but logically independent — it must surface as a *candidate* for
  verification". #438 measures *precision* of candidates and calls below-threshold noise the
  thing a verifier must reject.

  So: a proposal is FALSE when the two Points do NOT describe the same concept/claim — i.e.
  it is a similarity artifact. Two Points that describe the same concept but are worded
  cross-vocabularily are a TRUE proposal (that is the recall #399 exists for).

  The classification uses the product's OWN calibrated floor, not a new one:

  T1  contents identical (whitespace-collapsed)                     -> TRUE   (same claim)
  T2  composed texts byte-identical AND contents differ             -> FALSE  (one span, two
      different facts: cosine 1.000 is the quote's identity, not the claim's)
  T3  raw (content-space) similarity < DEFAULT_THRESHOLD 0.72       -> FALSE  (the pair does
      not even reach the calibrated same-concept band; only composition can propose it)
  T4  otherwise (raw >= 0.72, texts differ)                         -> LABELLED (dumped for a
      hand/LLM audit; this is the only tier that needs judgement)

T1/T2/T3 are mechanical and exhaustively applied. T4 is audited on a stated sample and the
rate carries a Wilson interval.

Arms: RAW = the vectors the graph stores (bge-small encoding of `content`); COMPOSED =
`embed_compose.compose_embedding_text(quote, ())` encoded with the same pinned model. Same
rows, same lens key, same threshold — the only difference is the embedding text.

Population: the LIVE, quoted, embedded Points the default read serves. ⛔ The hosted graph is
under wait-budget pressure (504 -32009), so this probe reads a bounded page PREFIX of the read
order at limit 100 (the rung that answers) and DECLARES that frame: it is a prefix, not a
random sample, so its composition is a statement about that prefix.

Usage:
    set -a; . ~/keys.env; set +a
    uv run python docs/evidence/11739-composed-gate/probes/gate3_false_proposals.py \
        --out /tmp/11739-g3.json --pairs-out /tmp/11739-g3-pairs.jsonl

    # provenance-only rebind of an EXISTING artifact (no measurement, no encoding)
    uv run python docs/evidence/11739-composed-gate/probes/gate3_false_proposals.py \
        --attest docs/evidence/11739-composed-gate/gate3_false_proposals.json

The AUDIT samples are dumped, not labelled: `--pairs-out` carries the seeded samples of the
only tier that needs judgement — `shared_T4` (proposed by BOTH arms, raw >= 0.72) and
`raw_only_T4` (proposed by RAW only). The true/false labels on those rows are a LANE action
recorded in the issue and the bundle README; this probe records the sample frame and its size
so the labels have a denominator, and it never invents a label of its own.

Exit codes: 0 measured · 2 usage / unwritable --out / an artifact `--attest` refused
(`ATTEST-REFUSED`: no `source` field to attribute the numbers to) · 3 graph unreachable /
empty population · 4 embedder unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
CENSUS_PROBE = REPO / "docs/evidence/11739-quote-population/probes/quote_population.py"

THRESHOLD = 0.72          # DEFAULT_THRESHOLD, imported by cross_lens (bge-small)
NEAR_DUP = 0.89           # NEAR_DUPLICATE_THRESHOLD
PAGE = 100                # the rung the wait budget answers at (500/250 missed)
ROWS_TARGET = 900         # declared population prefix
PAGE_ATTEMPTS = 6         # the wait budget is intermittent; retry with linear backoff
PAGE_BACKOFF_S = 8
AUDIT_MAX = 100           # shared-T4 pairs dumped for the audit (seeded random sample)
AUDIT_RAW_ONLY_MAX = 40   # raw-only T4 pairs dumped (the counter-movement's own sample)


def _say(msg: str) -> None:
    sys.stderr.write(msg)


def _payload_digest(report: dict, rows_source: str) -> str:
    """sha256 over the artifact's EVIDENCE — every field except ``provenance``, PLUS the row
    cache those fields are attributed to.

    Same construction, and the same reason, as ``composed_gate._payload_digest``:
    ``probe_sha256`` binds the artifact to the CODE that produced it and says nothing about the
    NUMBERS, so a hand-edit of a count would leave both digests intact. ``rows_source`` is
    INSIDE the digest because re-pointing it at a different cache is an edit to the evidence.

    Canonical form: ``json.dumps({"artifact": <the fields>, "rows_source": …}, indent=2,
    sort_keys=True)`` — a pure function of the values, not of the file's formatting.
    """
    body = {k: v for k, v in report.items() if k != "provenance"}
    canon = json.dumps({"artifact": body, "rows_source": rows_source},
                       indent=2, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _provenance(report: dict, rows_source: str) -> dict:
    """The artifact's self-binding (the bundle's convention, third artifact).

    ``main()`` writes it on a fresh run and ``--attest`` rewrites it on an existing artifact, so
    the value is always computed by this file from this file's own bytes — a lane cannot
    hand-author a digest that matches. What the bind buys is *"nothing edited this artifact
    after the attest"*, not *"these numbers came from a run"*; the attest is a lane action
    recorded in the commit diff, and this docstring says so rather than implying more.
    """
    return {
        "probe_path": "probes/gate3_false_proposals.py",
        "probe_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "payload_sha256": _payload_digest(report, rows_source),
        "rows_source": rows_source,
        "encoder": "BAAI/bge-small-en-v1.5 (tortoise.embeddings.EMBEDDING_MODEL)",
    }


def _attest(path: str) -> int:
    """(Re)write ``provenance`` into an EXISTING artifact from this probe's current bytes.

    Exists because a fresh run needs the LIVE/cached rows plus ~1 min of encoding, while a
    deliberate non-logic edit (a docstring, the audit-sample frame) changes only the code
    digest. ⛔ It refuses an artifact with no ``source`` field: the numbers must stay
    attributable to ONE row cache, or `rows_source` could be moved without touching a number.
    """
    try:
        report = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _say(f"ATTEST-UNREADABLE({path}): {e}\n")
        return 2
    if not isinstance(report, dict):
        # a JSON ARRAY parses fine, so this is the ONE shape ATTEST-UNREADABLE cannot catch and a
        # bare `.get` would turn into an AttributeError out of main() — exit 1, the code a lane
        # script cannot tell from a crash. Every documented refusal is 2.
        _say(f"ATTEST-UNREADABLE({path}): root is not an object\n")
        return 2
    source = report.get("source")
    if not isinstance(source, str) or not source:
        _say(f"ATTEST-REFUSED({path}): no `source` field, so `rows_source` cannot be recorded\n")
        return 2
    report["provenance"] = _provenance(report, source)
    try:
        pathlib.Path(path).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    except OSError as e:
        _say(f"ATTEST-WRITE({path}): {e}\n")
        return 2
    _say(f"attested {path} -> {report['provenance']['probe_sha256']}\n")
    return 0


def _census():
    spec = importlib.util.spec_from_file_location("quote_population", CENSUS_PROBE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _live(row: dict) -> bool:
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES

    status = row.get("status")
    if isinstance(status, str) and status in TERMINAL_EXCLUDED_STATUSES:
        return False
    return not row.get("outdated")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def _encode_all(texts: list[str], batch: int = 128) -> np.ndarray:
    import torch

    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    from tortoise.embeddings import EmbeddingModel

    model = EmbeddingModel.get()
    if model is None:
        _say("GATE-NO-EMBEDDER: pinned embedder unavailable (TF-IDF is a different space).\n")
        raise SystemExit(4)
    out = []
    for i in range(0, len(texts), batch):
        out.append(np.asarray(model.encode(texts[i:i + batch], batch_size=batch),
                              dtype=np.float32))
        _say(f"encoded {min(i + batch, len(texts))}/{len(texts)}\n")
    return np.vstack(out) if out else np.zeros((0, 384), dtype=np.float32)


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def read_rows(limit_rows: int) -> tuple[list[dict], dict]:
    census = _census()
    key = os.environ.get("TORTOISE_MCP_API_KEY") or os.environ.get("TORTOISE_API_KEY")
    endpoint = os.environ.get("TORTOISE_ENDPOINT") or "https://api.premiselabs.co/mcp/"
    if not key:
        _say("GATE-NO-KEY: TORTOISE_MCP_API_KEY is not set.\n")
        raise SystemExit(3)
    client = census._Client(endpoint, key)
    rows: list[dict] = []
    offset = 0
    pages = 0
    refused_at = None
    while len(rows) < limit_rows:
        page = None
        for attempt in range(1, PAGE_ATTEMPTS + 1):
            try:
                page, _used = census._page(client, offset, PAGE, True, page_no=pages + 1)
                break
            except SystemExit as e:
                refused_at = {"offset": offset, "exit": e.code, "attempt": attempt}
                _say(f"page {pages + 1} refused (exit {e.code}), attempt {attempt}\n")
                time.sleep(PAGE_BACKOFF_S * attempt)
        if page is None:
            break
        batch = page.get("results") or []
        if not batch:
            break
        rows.extend(batch)
        pages += 1
        offset += len(batch)
        _say(f"read page {pages} (offset {offset}) total_rows={len(rows)}\n")
    return rows, {
        "endpoint": endpoint,
        "pages": pages,
        "offset_reached": offset,
        "refused_at": refused_at,
        "rows_walked": len(rows),
    }


def proposals(raw: np.ndarray, comp: np.ndarray, ids: list[str], sessions: list[str],
              which: str) -> dict[tuple[str, str], float]:
    from tortoise.cross_lens import find_cross_lens_matches

    rows = {ids[i]: {"content": "", "session_id": sessions[i] or "unknown"}
            for i in range(len(ids))}
    enc = (lambda _t: raw) if which == "raw" else (lambda _t: comp)
    hits = find_cross_lens_matches(rows, lens_key="session_id", encode=enc,
                                   threshold=THRESHOLD)
    return {(h["src"], h["dst"]): float(h["similarity"]) for h in hits}


def classify(pairs: list[tuple[str, str]], raw: np.ndarray, comp: np.ndarray,
             ids: list[str], contents: dict[str, str], quotes: dict[str, str],
             pos: dict[str, int]) -> dict:
    tiers = {"T1_same_content": [], "T2_identical_composed_text": [],
             "T3_below_calibrated_floor": [], "T4_needs_label": []}
    for a, b in pairs:
        ia, ib = pos[a], pos[b]
        r = float(raw[ia] @ raw[ib])
        c = float(comp[ia] @ comp[ib])
        rec = {"a": a, "b": b, "raw": round(r, 4), "composed": round(c, 4)}
        if _norm(contents[a]) == _norm(contents[b]):
            tiers["T1_same_content"].append(rec)
        elif _norm(quotes[a]) == _norm(quotes[b]):
            tiers["T2_identical_composed_text"].append(rec)
        elif r < THRESHOLD:
            tiers["T3_below_calibrated_floor"].append(rec)
        else:
            tiers["T4_needs_label"].append(rec)
    return tiers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/11739-g3.json")
    ap.add_argument("--pairs-out", default="/tmp/11739-g3-pairs.jsonl")
    ap.add_argument("--attest", default="", metavar="PATH",
                    help="(re)write provenance into an existing artifact; no measurement runs")
    args = ap.parse_args()
    if args.attest:
        return _attest(args.attest)
    t0 = time.time()

    from tortoise.embed_compose import compose_embedding_text, quote_text

    cache = pathlib.Path(os.environ.get("G3_ROWS_CACHE", "/tmp/11739-g3-rows.jsonl"))
    if cache.exists():
        rows = [json.loads(line) for line in cache.read_text(encoding="utf-8").splitlines()]
        meta = {"endpoint": "cache:" + str(cache), "rows_walked": len(rows),
                "pages": None, "offset_reached": None, "refused_at": None}
        _say(f"rows from cache: {len(rows)}\n")
    else:
        rows, meta = read_rows(ROWS_TARGET)
        with cache.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if not rows:
        _say("GATE-EMPTY: the read returned no rows.\n")
        return 3
    live = [r for r in rows if _live(r)]
    kept = []
    for r in live:
        emb = r.get("embedding")
        if not isinstance(emb, list) or not emb:
            continue
        if not quote_text(r.get("quote")):
            continue
        kept.append(r)
    _seen: set[str] = set()
    _uniq = []
    for r in kept:
        pid = str(r["id"])
        if pid in _seen:
            continue
        _seen.add(pid)
        _uniq.append(r)
    kept = _uniq

    if len(kept) < 50:
        _say(f"GATE-SMALL: only {len(kept)} live quoted+embedded rows.\n")
        return 3

    ids = [str(r["id"]) for r in kept]
    pos = {pid: i for i, pid in enumerate(ids)}
    contents = {str(r["id"]): (r.get("content") or "") for r in kept}
    quotes = {str(r["id"]): quote_text(r.get("quote")) for r in kept}
    sessions = [str(r.get("session_id") or "unknown") for r in kept]

    raw = _unit(np.asarray([r["embedding"] for r in kept], dtype=np.float32))
    comp_texts = [compose_embedding_text(r.get("quote"), (), content=r.get("content"))
                  for r in kept]
    comp = _unit(_encode_all(comp_texts))

    raw_pairs = proposals(raw, comp, ids, sessions, "raw")
    comp_pairs = proposals(raw, comp, ids, sessions, "composed")
    new_pairs = sorted(set(comp_pairs) - set(raw_pairs))
    lost_pairs = sorted(set(raw_pairs) - set(comp_pairs))
    shared = sorted(set(comp_pairs) & set(raw_pairs))

    tiers_comp = classify(sorted(comp_pairs), raw, comp, ids, contents, quotes, pos)
    tiers_new = classify(new_pairs, raw, comp, ids, contents, quotes, pos)
    tiers_raw = classify(sorted(raw_pairs), raw, comp, ids, contents, quotes, pos)

    import random

    # ONE SEED PER SAMPLE, both 11739: each sample is a function of its own tier and seed, so
    # adding, resizing or reordering the OTHER sample cannot renumber the rows a hand audit was
    # performed on (a single shared stream does exactly that — measured: 40/40 different pairs).
    dump = []
    shared_t4 = [r for r in tiers_comp["T4_needs_label"]]
    sample = random.Random(11739).sample(shared_t4, min(AUDIT_MAX, len(shared_t4)))
    for rec in sample:
        dump.append({**rec, "set": "shared_T4",
                     "in_raw": [rec["a"], rec["b"]] in
                     [[x["a"], x["b"]] for x in tiers_raw["T4_needs_label"]],
                     "content_a": contents[rec["a"]][:300],
                     "content_b": contents[rec["b"]][:300],
                     "quote_a": quotes[rec["a"]][:100],
                     "quote_b": quotes[rec["b"]][:100]})

    # The COUNTER-MOVEMENT's own tier: raw proposed it, composition dropped it. Same judgement
    # question (raw >= 0.72, texts differ), different set — without it the composed arm's
    # false count is quoted against a denominator whose raw half was never audited.
    # Sorted by pair id, so the sample is a function of the PAIR SET and not of the order the
    # raw classifier happened to visit them in — the same seed must name the same rows on every
    # re-run, or a hand audit of "sample n=40" would silently describe a different 40.
    lost_set = set(lost_pairs)
    raw_only_t4 = sorted((r for r in tiers_raw["T4_needs_label"]
                          if (r["a"], r["b"]) in lost_set),
                         key=lambda r: (r["a"], r["b"]))
    raw_only_sample = random.Random(11739).sample(raw_only_t4,
                                                 min(AUDIT_RAW_ONLY_MAX, len(raw_only_t4)))
    for rec in raw_only_sample:
        dump.append({**rec, "set": "raw_only_T4",
                     "content_a": contents[rec["a"]][:300],
                     "content_b": contents[rec["b"]][:300],
                     "quote_a": quotes[rec["a"]][:100],
                     "quote_b": quotes[rec["b"]][:100]})
    with open(args.pairs_out, "w", encoding="utf-8") as fh:
        for rec in dump:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def summarize(tiers: dict, total: int) -> dict:
        t1, t2, t3 = (len(tiers["T1_same_content"]),
                      len(tiers["T2_identical_composed_text"]),
                      len(tiers["T3_below_calibrated_floor"]))
        t4 = len(tiers["T4_needs_label"])
        return {"proposals": total, "true_exact": t1, "false_exact": t2 + t3,
                "false_by_tier": {"T2_identical_composed_text": t2,
                                  "T3_below_calibrated_floor": t3},
                "needs_label": t4}

    source = str(meta.get("endpoint") or "")
    out = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "criterion_source": "#399 (candidate surface + calibrated 0.72 paraphrase floor) / #438 (precision of candidates)",
        "frame": {**meta, "live_rows": len(live),
                  "population_used": len(kept),
                  "frame_note": ("page PREFIX of the default read order at limit "
                                 f"{PAGE}; not a random sample — a prefix, declared.")},
        "arms": {"threshold": THRESHOLD, "lens_key": "session_id",
                 "encoder": "BAAI/bge-small-en-v1.5"},
        "audit_samples": {"shared_T4": len(sample), "raw_only_T4": len(raw_only_sample),
                          "shared_T4_tier": len(shared_t4),
                          "raw_only_T4_tier": len(raw_only_t4),
                          "note": ("seeded random samples (seed 11739) of the only tier that "
                                   "needs judgement; the true/false LABELS are recorded in "
                                   "the bundle README and #11739, not here")},
        "proposals": {
            "raw": len(raw_pairs), "composed": len(comp_pairs),
            "shared": len(shared), "composed_only": len(new_pairs),
            "raw_only_lost": len(lost_pairs),
            "composed_only_crossing_0_89": sum(
                1 for a, b in new_pairs
                if float(raw[pos[a]] @ raw[pos[b]]) < NEAR_DUP <= float(comp[pos[a]] @ comp[pos[b]])),
        },
        "composed": summarize(tiers_comp, len(comp_pairs)),
        "composed_only": summarize(tiers_new, len(new_pairs)),
        "raw": summarize(tiers_raw, len(raw_pairs)),
        "duration_s": round(time.time() - t0, 1),
    }
    out["provenance"] = _provenance(out, source)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    _say(json.dumps({k: out[k] for k in ("proposals", "composed", "composed_only", "raw")},
                    indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
