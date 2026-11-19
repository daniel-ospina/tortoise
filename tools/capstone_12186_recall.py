#!/usr/bin/env python
"""The #12186 capstone: are the merged search levers visible to a USER, on ONE fixed query set?

WHAT THIS ANSWERS, AND WHY IT IS NOT ANOTHER #11901 RUN
-------------------------------------------------------
The epic's own closing rule is *"the capstone proves behaviour, not code"*. Six
levers merged under it — `#12368` `_`-preserving OR terms, `#12343`/`#12297` the
cross-encoder rerank, `#12348` the verdict step, `#12304` the fold + multi-key
blocking, `#12349` M3/SPLADE measured-and-closed — and each was proven **where it
was built**. None was proven **where a user meets it**, on a query set that does
not move between runs.

This tool is that measurement. On ONE pinned query set, in one run, it reports:

  recall@3 / @5 / @10 / @15   the canonical's rank in the window, at four widths
  duplicate rate IN WINDOW    of the k rows a caller is handed, how many are
                              near-duplicates of ANOTHER ROW IN THE SAME WINDOW
                              (`neardup-v1`, declared below)
  duplicate MINT rate         a real surface VARIANT of a stored name that does
                              not attach to its canonical — the write path mints
                              a second node (`#11901`'s definition, on this pin)
  the surface's own identity  which door was measured, and the registry binding
                              that makes it the door a user hits

⛔ WHY A PINNED QUERY SET AND NOT THE `#11901` HARNESS'S LIVE HARVEST
--------------------------------------------------------------------
`#11901` — and `tools/candidate_recall_11901.py`, whose harvest rules this tool
IMPORTS rather than re-implements — harvests its population from the live capture
corpus at run time, so two runs on two days measure two corpora. That is correct
for a mechanism measurement and fatal for a capstone: a number that cannot be
re-derived from the commit is a claim, not evidence. So `build-pin` freezes the
harvest into `docs/evidence/12186-capstone/queries.json` (population, the 300-name
stratified query sample, class quotas, corpus accounting, a digest), and `run`
reads only that file — a re-run measures the SAME names.

⚠️ **Corpus identity is declared, never assumed.** This population is `#11901`'s
RULES on this box's corpus, frozen on one date. It is NOT the population `#11901`
published 61.3 %/65.0 %/69.0 %/73.0 % on (that artifact predates the pin and its
population is not re-provable — see that brief's §2.2), and it is NOT the
`#12298`/`#12360` pin whose pre-lever baseline was recall@3 0.7133. The artifact
refuses those comparisons BY NAME (`comparisons`), so a reader cannot mistake one
population's figure for another's.

⛔ AND THE LEVERS ARE MEASURED AS A BEFORE/AFTER *ON THIS PIN*
--------------------------------------------------------------
A cross-corpus comparison cannot show a lever working — the two sides differ in
corpus as well as in code. So every shipped lever that has a switch is measured
TWICE on the SAME pin, same graph, same process, one env flag apart:

  ``*_pre_lever``  ``TORTOISE_UNDERSCORE_TERMS=0`` — the shipped query path with
                   `#12368`'s composite terms OFF (its documented before/after
                   switch, `tortoise.sparse.UNDERSCORE_ENV`)
  ``capture_prior``/``user_search``
                   the shipped default (composite terms ON)
  ``user_search_rerank``
                   the user door with ``TORTOISE_FUSED_RERANK=1`` — NOT the
                   shipped default, so it answers "what would the user get if the
                   merged cross-encoder were armed", which is a different question
                   from "what the user gets today"

THE ARMS, AND WHICH DOOR EACH ONE IS
------------------------------------
``capture_prior``      ``extractor_v2._fts_rows`` — the link-before-create prior
                       that runs on every capture. The INTERNAL candidate path,
                       kept because "the user surface is fine" is not a statement
                       about the write path (a duplicate minted there is permanent).
``user_search``        ``sdk.tortoise_fts_query`` — the method the MCP tool
                       ``tortoise_search`` is registered to and REST
                       ``GET /v1/search`` uses. That VERB is what makes this the
                       door a user hits; the ARGUMENTS are this fixture's, NOT the
                       doors' defaults — the arms pass ``entity_type='object'``
                       (the population IS ``:Object`` names) at ``limit=KMAX``,
                       while both doors default to ``'point'`` and ``10``
                       (``tortoise/mcp_server.py::tortoise_search`` and
                       ``tortoise/selfhost_api.py::search``). See ``DOOR_NOTES``.
                       The binding is re-read at run time from
                       ``tortoise.tool_registry`` and recorded, so "we measured
                       the user surface" is a checked fact, not prose.

Each arm queries every name ONCE at ``KMAX``; recall@k for k <= KMAX is a prefix
of the same ranking, so no k is a separate read path.

PAIRED COMPARISONS, RESTRICTED TO CLEAN WINDOWS
-----------------------------------------------
On a loaded box a leg occasionally degrades with ``timeout`` — measured here, not
imagined. A raw arm-to-arm difference then mixes the lever with the degradation,
so every pair is also reported **paired over the queries whose windows were clean
in BOTH arms** (``paired``), with the excluded count stated. That is the number a
lever claim may rest on; the raw arm figures are kept beside it so a reader can
see what was excluded.

HOW TO RUN
----------
    # the structural guards (no graph, ~40 s)
    uv run python tools/test_lane.py run -- tests/test_capstone_12186_recall.py -q

    # one-time: freeze the query set (writes the committed pin; refuses to
    # overwrite an existing pin without --force, because a fixed set that moves
    # is not fixed)
    uv run python tools/capstone_12186_recall.py build-pin

    # the measurement: one command, one artifact
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    HF_HUB_OFFLINE=1 uv run python tools/capstone_12186_recall.py run \\
        --graph cap12186-$RANDOM \\
        --out docs/evidence/12186-capstone/measurement.json

EXIT CODES: 0 ok · 2 usage · 3 no reachable graph / a guard refused
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before any real work, so the refusal is
# ATTRIBUTED rather than a traceback from inside a function. `--help`, not the
# bare path: `main()` requires a subcommand, so the bare form would raise a
# SECOND unattributed error (#11507/#11744).
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    raise SystemExit(
        f"tools/capstone_12186_recall.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/capstone_12186_recall.py --help`"
    )

import argparse
import collections
import hashlib
import itertools
import json
import math
import os
import re
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence" / "12186-capstone"
DEFAULT_PIN = EVIDENCE_DIR / "queries.json"

#: The widths the capstone quotes. 3 is the capture prior's production window;
#: 15 is the widest window #11901 measured (Zep/Graphiti's candidate count).
KS: tuple[int, ...] = (3, 5, 10, 15)
KMAX = max(KS)

#: How many ids a paired block lists per side. The COUNTS (`gains`/`losses`) are never
#: capped; only the id lists are, and `ids_truncated_at` publishes the cap and the
#: `*_truncated` flags say whether it actually bit, so a reader re-deriving the count
#: from the ids cannot silently get the cap instead of the total.
IDS_LIMIT = 20

#: The capture prior's own window. Quoted separately because a rate at another
#: width is not the rate the write path pays.
PRIOR_WINDOW = 3

#: The k the paired comparisons are taken at: the capture prior's production
#: window, i.e. the width every capture actually pays.
PAIRED_K = 3

#: Frozen at pin-build time; a run against a pin that reports another version is
#: a REFUSAL, because the query set's identity is the whole point.
PIN_SCHEMA = "12186-capstone-queries-v1"

#: ``(arm name, door, env overrides)``. The arm NAME is the artifact key; the DOOR
#: selects the call; the env is the one thing that may differ between two arms of
#: the same door — which is what makes those pairs a measurement of a lever rather
#: than of two corpora.
ARMS: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("capture_prior_pre_lever", "capture_prior",
     {"TORTOISE_UNDERSCORE_TERMS": "0"}),
    ("capture_prior", "capture_prior", {}),
    ("user_search_pre_lever", "user_search",
     {"TORTOISE_UNDERSCORE_TERMS": "0"}),
    ("user_search", "user_search", {}),
    ("user_search_rerank", "user_search", {"TORTOISE_FUSED_RERANK": "1"}),
)

DOORS = ("capture_prior", "user_search")

DOOR_NOTES = {
    "capture_prior": "extractor_v2._fts_rows(entity_type='object') — the "
                     "capture-time link-before-create prior (INTERNAL path); "
                     "production window is 3",
    # ⛔ The VERB is the user door's; the ARGUMENTS are this fixture's, and the two are
    # not the same claim. `tortoise_search` (MCP) and `GET /v1/search` (REST) both
    # resolve to `sdk.tortoise_fts_query` — that is what makes this the user's door.
    # But the doors' own defaults are `entity_type='point'` and `limit=10`
    # (`tortoise/mcp_server.py::tortoise_search`), while the arms call
    # `entity_type='object'` (the population IS `:Object` names) at `limit=KMAX`=15.
    # A note that called those "the tool's own defaults" would be false about two
    # default values while presenting itself as a checked fact — so it states the
    # divergence instead. `recall@k` is a shipped-window figure only for k <= 10.
    "user_search": "sdk.tortoise_fts_query(entity_type='object', limit=15) — the "
                   "USER door's VERB (MCP `tortoise_search` and REST "
                   "`GET /v1/search` both resolve to it). The ARGUMENTS are this "
                   "fixture's, NOT the doors' defaults: `entity_type='object'` "
                   "because the population is `:Object` names (both doors default "
                   "to 'point'), and `limit=15` (both doors default to 10 — so "
                   "recall@k is a shipped-window figure only for k <= 10)",
}

ARM_NOTES = {
    "capture_prior_pre_lever": "capture prior with #12368's composite terms OFF "
                               "(TORTOISE_UNDERSCORE_TERMS=0) — the before of the "
                               "same-corpus before/after",
    "capture_prior": "capture prior, shipped default (composite terms ON)",
    "user_search_pre_lever": "user door with #12368's composite terms OFF — the "
                             "before of the same-corpus before/after",
    "user_search": "user door at the SHIPPED default: composite terms ON, fused "
                   "rerank gate OFF (what a user gets today)",
    "user_search_rerank": "the same user door with TORTOISE_FUSED_RERANK=1 — NOT "
                          "the shipped default; what the merged cross-encoder "
                          "would give a user if armed",
}

#: The pairs whose difference IS a lever, and how to read each. Every pair is
#: within one door, one pin, one graph, one process.
PAIRS: tuple[tuple[str, str, str], ...] = (
    ("capture_prior_pre_lever", "capture_prior",
     "#12368 composite `_`-terms, on the CAPTURE PRIOR (write path)"),
    ("user_search_pre_lever", "user_search",
     "#12368 composite `_`-terms, on the USER door"),
    ("user_search", "user_search_rerank",
     "#12297/#12343 fused cross-encoder rerank, on the USER door — the AFTER is "
     "the ARMED arm (NOT the shipped default)"),
    ("capture_prior", "user_search",
     "the DOOR itself — internal candidate path vs the surface a user hits"),
)

# ── the near-duplicate rule, FROZEN ───────────────────────────────────────────
#: The rule `duplicate_rate_in_window` is computed under. Versioned and fixed
#: because a duplicate rate without its rule is a number nobody can re-derive.
#: A pair is a near-duplicate when their keys are EQUAL, or when the token
#: Jaccard of their keys is >= ``NEARDUP_JACCARD``. The key folds case,
#: whitespace and the separator class (`-`/`_`/`.`/`/`) in ONE pass — reusing
#: `candidate_recall_11901._fold_punct`, so this tool cannot disagree with the
#: matcher measurement about what a separator fold is — and then drops a LEADING
#: article, because "the diff" and "diff" are the same noun written twice and
#: are exactly the class the epic calls the duplicate-noun rate.
#:
#: ⛔ It is a DESCRIPTIVE rule, never a binding decision: `_fold_punct`'s own
#: docstring records that the fold is not merge-free (an `a-b` mention can bind a
#: stored `b`), which is why production matching never uses it unguarded. Counting
#: two rows as duplicates is not linking them.
NEARDUP_RULE = "neardup-v1"
NEARDUP_JACCARD = 0.8
#: The machine-readable DESCRIPTION of the key the rule computes, written into the
#: artifact beside the version and the threshold. Named here rather than inlined at
#: the writer so the pin can derive it from the tool instead of copying the record's
#: own string back (round 27: `measurement.json -> neardup_rule.key` was read by
#: nothing, so it could become "exact string only" with the suite green at 55
#: passed, while §3a's rates are the one section a reader cannot re-derive).
NEARDUP_KEY_NOTE = "case+ws+separator fold, leading article dropped"
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_TOK = re.compile(r"[a-z0-9]+")


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ── the pin ──────────────────────────────────────────────────────────────────

def build_pin(args: argparse.Namespace) -> int:
    """Freeze `#11901`'s harvest into a committed query set.

    ⛔ The rules are IMPORTED from `tools/candidate_recall_11901.py`
    (`harvest` / `build_population` / `_allocate_shares` / `_interleave`), never
    re-implemented: a second copy of the harvest rules would drift and the two
    populations would stop meaning the same thing. This function adds only what
    that instrument never wrote out — the ROWS, so a later run can read them
    instead of re-harvesting a live, non-monotone directory.
    """
    from tools.candidate_recall_11901 import (
        _allocate_shares,
        _interleave,
        build_population,
        harvest,
    )

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"REFUSED: {out} already exists — the query set is FIXED, so "
              f"re-freezing it is how a 'fixed' set stops being one. Pass "
              f"--force only to replace the pin deliberately (and say so in the "
              f"artifact's corpus block).", file=sys.stderr)
        return 3

    sessions = Path(os.path.expanduser(args.sessions_dir))
    stats: dict = {}
    t0 = time.time()
    harvested = harvest(sessions, stats=stats)
    pop = build_population(harvested, args.population)
    if not pop:
        print("REFUSED: the harvest produced no names", file=sys.stderr)
        return 3
    shares = _allocate_shares(args.sample, pop)
    by_cls: dict[str, list[dict]] = collections.defaultdict(list)
    for row in pop:
        by_cls[row["cls"]].append(row)
    sample = _interleave(by_cls, shares)

    rows = [{"id": f"cap-{i:06d}", "name": r["name"], "cls": r["cls"],
             "records": r.get("records")} for i, r in enumerate(pop)]
    by_id = {r["id"]: r for r in rows}
    # The sample is carried as ids into the population, so the two lists cannot
    # disagree about a name (a second copy of the name is a second owner of it).
    query_ids = [r["id"] for r in rows
                 if any(s["name"] == r["name"] and s["cls"] == r["cls"]
                        for s in sample)]
    if len(query_ids) != len(sample):
        print(f"REFUSED: {len(query_ids)} query ids resolved for a "
              f"{len(sample)}-name sample — a duplicate name would alias two "
              f"rows", file=sys.stderr)
        return 3

    doc = {
        "schema": PIN_SCHEMA,
        "issue": "12186",
        "purpose": ("FIXED query set for the #12186 capstone — one population, "
                    "one 300-name stratified sample, frozen so a re-run measures "
                    "the same names"),
        "rules": {
            "harness": "tools/candidate_recall_11901.py",
            "imported": ["harvest", "build_population", "_allocate_shares",
                         "_interleave"],
            "population": args.population,
            "sample": args.sample,
            "shares": dict(sorted(shares.items())),
        },
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus": {"dir": str(sessions), **stats,
                   "harvested_distinct": {c: len(v) for c, v in harvested.items()},
                   "elapsed_s": round(time.time() - t0, 1)},
        "digest": hashlib.sha256(
            "\n".join(r["name"] for r in rows).encode()).hexdigest()[:16],
        "query_digest": hashlib.sha256(
            "\n".join(by_id[i]["name"] for i in query_ids).encode()).hexdigest()[:16],
        "population_rows": rows,
        "query_ids": query_ids,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"pin written: {out} — {len(rows)} population rows, "
          f"{len(query_ids)} queries, digest {doc['digest']}")
    return 0


def load_pin(path: Path) -> tuple[dict, str]:
    """Read the pin and REFUSE a shape this tool cannot honestly measure."""
    raw = path.read_bytes()
    doc = json.loads(raw)
    if doc.get("schema") != PIN_SCHEMA:
        raise SystemExit(f"REFUSED: {path} is schema {doc.get('schema')!r}, "
                         f"expected {PIN_SCHEMA!r}")
    rows, qids = doc.get("population_rows"), doc.get("query_ids")
    if not rows or not qids:
        raise SystemExit(f"REFUSED: {path} carries no population/query rows")
    if not all({"id", "name", "cls"} <= set(r) for r in rows):
        raise SystemExit(f"REFUSED: {path} rows need id/name/cls")
    known = {r["id"] for r in rows}
    missing = [q for q in qids if q not in known]
    if missing:
        raise SystemExit(f"REFUSED: {len(missing)} query ids are absent from "
                         f"the population ({missing[:3]})")
    return doc, hashlib.sha256(raw).hexdigest()


# ── the near-duplicate rule ──────────────────────────────────────────────────

def near_dup_key(name: str) -> str:
    from tools.candidate_recall_11901 import _fold_punct

    key = _LEADING_ARTICLE.sub("", _fold_punct(name or "").strip())
    return re.sub(r"\s+", " ", key).strip()


def _tokens(key: str) -> frozenset[str]:
    return frozenset(_TOK.findall(key))


def is_near_duplicate(a: str, b: str) -> bool:
    ka, kb = near_dup_key(a), near_dup_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = _tokens(ka), _tokens(kb)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= NEARDUP_JACCARD


def duplicate_in_window(names: list[str]) -> dict:
    """How many of these rows are near-duplicates of ANOTHER ROW IN THE WINDOW.

    Two numbers, because they answer different questions and one hides the
    other: ``rows`` (how much of what the caller is handed is redundant) and
    ``queries_with_any`` (how often the caller sees the problem at all — a
    single duplicate pair in a wide window moves the first and not the second).
    """
    dupe_rows = set()
    pairs = 0
    for i, j in itertools.combinations(range(len(names)), 2):
        if is_near_duplicate(names[i], names[j]):
            pairs += 1
            dupe_rows.add(i)
            dupe_rows.add(j)
    return {"rows": len(names), "duplicate_rows": len(dupe_rows), "pairs": pairs}


# ── the measurement ──────────────────────────────────────────────────────────

def _door(modes: dict) -> str:
    """The retrieval-mode label, derived from what the legs DID (never from
    embedder availability — a model that loads and whose leg never answers is
    not a hybrid read; `battery/runner/retrieval_preflight.py` exists for this).

    ⛔ ``untraced`` is its own answer, never folded into ``keyword_only``: a mode
    read off an empty trace would be an INFERENCE from the absence of a trace —
    the false label #2952/#3005 exist to prevent, in the direction that hides a
    degraded dense leg.
    """
    hybrid, keyword = modes.get("hybrid", 0), modes.get("keyword_only", 0)
    if not (hybrid or keyword):
        return "untraced (no leg_trace on this door)"
    if hybrid and keyword:
        return "MIXED"
    return "hybrid" if hybrid else "keyword_only"


class _TracedSdk:
    """A forwarding view of the SDK that attaches its own ``leg_trace`` sink.

    Used ONLY for the capture prior, whose function (``_fts_rows``) takes no trace
    kwarg. It forwards exactly one method and adds exactly one keyword the SDK
    already accepts, so the read it performs is the read ``_fts_rows`` performs.
    """

    def __init__(self, sdk, sink: list[dict]):
        self._sdk = sdk
        self._sink = sink

    def tortoise_fts_query(self, *args, **kwargs):
        kwargs.setdefault("leg_trace", self._sink)
        return self._sdk.tortoise_fts_query(*args, **kwargs)


def _query_rows(door: str, ctx: dict, query: str, limit: int) -> tuple[list[dict], dict]:
    """One read of one door. Returns ``(rows, facts)``; ``facts["degraded"]``
    says whether ANY leg failed to carry its share on THIS window."""
    trace: list[dict] = []
    if door == "capture_prior":
        rows = ctx["fts_rows"](_TracedSdk(ctx["sdk"], trace), "object",
                               query, limit=limit)
    else:
        # ⛔ The literal `_rerank_candidates=True` is NOT passed, and the reason is
        # the SHAPE OF THE CALL, not the value: the MCP tool passes no such
        # private kwarg either, so omitting it is what makes this arm the tool's
        # call. The kwarg's SDK default is already `True`
        # (`tortoise/sdk.py::tortoise_fts_query`), so the SHIPPED/ARMED split is
        # decided by the arm's own `TORTOISE_FUSED_RERANK` gate, not by this line —
        # which is why `user_search` is the shipped OFF arm and
        # `user_search_rerank` is the armed one from one env flag apart.
        rows = ctx["sdk"].tortoise_fts_query(query, entity_type="object",
                                             limit=limit, leg_trace=trace)
    from tortoise.exceptions import HybridReadUnavailableError
    from tortoise.search_engine import require_hybrid_read

    degraded = False
    if trace:
        try:
            require_hybrid_read(trace)
        except HybridReadUnavailableError:
            ctx["modes"]["keyword_only"] = ctx["modes"].get("keyword_only", 0) + 1
        else:
            ctx["modes"]["hybrid"] = ctx["modes"].get("hybrid", 0) + 1
    for entry in trace or []:
        leg = str(entry.get("leg") or entry.get("strategy") or "?")
        ran = bool(entry.get("ran"))
        ctx["legs"][leg] = ctx["legs"].get(leg, False) or ran
        # WHY a leg did not carry its share, counted rather than summarised: a
        # MIXED mode is otherwise an unexplained label, and "degraded" and "never
        # submitted" have different fixes.
        if not ran or entry.get("degraded"):
            degraded = True
            reason = str(entry.get("reason") or entry.get("absent_reason")
                         or "unknown")
            key = f"{leg}:{'degraded' if ran else 'absent'}:{reason}"
            ctx["leg_reasons"][key] = ctx["leg_reasons"].get(key, 0) + 1
        if leg == "rerank" and ran:
            ctx["rerank_ran"] = ctx["rerank_ran"] + 1
    return list(rows or []), {"degraded": degraded}


def _reset_ctx(ctx: dict) -> None:
    ctx["modes"] = {}
    ctx["legs"] = {}
    ctx["leg_reasons"] = {}
    ctx["rerank_ran"] = 0


def _attach_ctx(arm: dict, ctx: dict) -> None:
    """What the legs DID, attached to the arm that measured them."""
    arm["legs_seen"] = dict(sorted(ctx["legs"].items()))
    arm["leg_degrade_reasons"] = dict(sorted(ctx["leg_reasons"].items()))
    arm["retrieval_mode"] = _door(ctx["modes"])
    arm["window_modes"] = dict(sorted(ctx["modes"].items()))


def measure_arm(arm: str, door: str, rows: list[dict], ctx: dict) -> dict:
    per_k = {k: {"in_window": 0, "rank_sum": 0, "rows_zero": 0} for k in KS}
    dup_k = {k: {"rows": 0, "duplicate_rows": 0, "pairs": 0,
                 "queries_with_any": 0} for k in KS}
    by_class: dict[str, dict] = collections.defaultdict(
        lambda: {"n": 0, **{str(k): 0 for k in KS}})
    per_query: dict[str, dict] = {}

    n = 0
    t0 = time.time()
    for row in rows:
        out, facts = _query_rows(door, ctx, row["name"], KMAX)
        ids = [_rid(r) for r in out]
        names = [_rname(r) for r in out]
        n += 1
        by_class[row["cls"]]["n"] += 1
        rank = ids.index(row["id"]) + 1 if row["id"] in ids else None
        # ONE entry per query, carrying the degradation flag: the paired
        # comparisons need to exclude a window that was degraded on either side,
        # and a rank without that flag cannot be excluded honestly.
        per_query[row["id"]] = {"rank": rank, "degraded": facts["degraded"],
                                "cls": row["cls"]}
        for k in KS:
            if not ids:
                per_k[k]["rows_zero"] += 1
            if rank is not None and rank <= k:
                per_k[k]["in_window"] += 1
                per_k[k]["rank_sum"] += rank
                by_class[row["cls"]][str(k)] += 1
            d = duplicate_in_window(names[:k])
            dup_k[k]["rows"] += d["rows"]
            dup_k[k]["duplicate_rows"] += d["duplicate_rows"]
            dup_k[k]["pairs"] += d["pairs"]
            if d["duplicate_rows"]:
                dup_k[k]["queries_with_any"] += 1

    return {
        "arm": arm,
        "door": door,
        "n": n,
        "recall": {str(k): round(per_k[k]["in_window"] / n, 4) for k in KS},
        "rows_zero": {str(k): per_k[k]["rows_zero"] for k in KS},
        "mean_rank": {str(k): (round(per_k[k]["rank_sum"] / per_k[k]["in_window"], 3)
                               if per_k[k]["in_window"] else None) for k in KS},
        "by_class": {c: {"n": v["n"],
                         **{str(k): (round(v[str(k)] / v["n"], 4) if v["n"] else None)
                            for k in KS}}
                     for c, v in sorted(by_class.items())},
        "duplicate_rate_in_window": {
            str(k): {
                "rows": dup_k[k]["rows"],
                "duplicate_rows": dup_k[k]["duplicate_rows"],
                "rate": (round(dup_k[k]["duplicate_rows"] / dup_k[k]["rows"], 4)
                         if dup_k[k]["rows"] else None),
                "pairs": dup_k[k]["pairs"],
                "queries_with_any": dup_k[k]["queries_with_any"],
                "queries": n,
            } for k in KS},
        "per_query": per_query,
        "elapsed_s": round(time.time() - t0, 2),
        "note": ARM_NOTES[arm],
        "door_note": DOOR_NOTES[door],
    }


def _rid(row) -> str:
    return str((row or {}).get("id") or "") if isinstance(row, dict) else str(row)


def _rname(row) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("content") or row.get("name") or "")


def paired(before: dict, after: dict, k: int) -> dict:
    """Exact paired comparison over the queries CLEAN IN BOTH ARMS.

    ⛔ A raw arm-to-arm difference on a loaded box mixes the lever with a leg
    ``timeout``. Excluding a window that was degraded on EITHER side is what
    makes the remaining difference attributable — and the excluded count is
    published, so the exclusion cannot become a silent filter.
    """
    from tools.underscore_or_terms_12360 import mcnemar_exact

    b_q, a_q = before["per_query"], after["per_query"]
    shared = [i for i in b_q if i in a_q]
    clean = [i for i in shared
             if not b_q[i]["degraded"] and not a_q[i]["degraded"]]
    gains, losses = [], []
    for i in clean:
        rb, ra = b_q[i]["rank"], a_q[i]["rank"]
        hit_b = rb is not None and rb <= k
        hit_a = ra is not None and ra <= k
        if hit_a and not hit_b:
            gains.append(i)
        elif hit_b and not hit_a:
            losses.append(i)
    n = len(clean)
    rb_recall = (sum(1 for i in clean
                     if b_q[i]["rank"] is not None and b_q[i]["rank"] <= k) / n
                 if n else None)
    ra_recall = (sum(1 for i in clean
                     if a_q[i]["rank"] is not None and a_q[i]["rank"] <= k) / n
                 if n else None)
    p = mcnemar_exact(len(gains), len(losses))
    return {
        "k": k,
        "queries_compared": n,
        "excluded_degraded_on_either_side": len(shared) - n,
        "before_recall_clean": None if rb_recall is None else round(rb_recall, 4),
        "after_recall_clean": None if ra_recall is None else round(ra_recall, 4),
        "delta_pts_clean": (None if rb_recall is None or ra_recall is None
                            else round((ra_recall - rb_recall) * 100, 2)),
        "gains": len(gains),
        "losses": len(losses),
        "p": p,
        "p_display": (f"<1e-{int(-math.log10(p))}" if 0 < p < 1e-12 else f"{p:.4g}"),
        "gain_ids": gains[:IDS_LIMIT],
        "loss_ids": losses[:IDS_LIMIT],
        # ⛔ A capped list beside an uncapped COUNT is a silent subset unless the cap is
        # published: the artifact printed `gains: 43` next to 20 ids and said nothing,
        # so a reader re-deriving the gain count from the published ids got 20 — the
        # `tortoise/truncation.py` class (`mark_truncation`) this repo forbids. `gains`
        # and `losses` above already carry the true totals, so the only missing fact was
        # that the lists are capped, and where.
        "ids_truncated_at": IDS_LIMIT,
        "gain_ids_truncated": len(gains) > IDS_LIMIT,
        "loss_ids_truncated": len(losses) > IDS_LIMIT,
    }


def mint_rate(pin: dict, by_id: dict, ctx: dict) -> dict:
    """The write path's duplicate-noun rate, on the SAME pinned population.

    `#11901`'s definition, unchanged: a real surface VARIANT of a stored name is
    issued as the mention; if phase 1 does not bind it onto its own canonical,
    that capture MINTS a second node. Measured through the capture prior at the
    production window, against the FROZEN pre-`#12295` matcher
    (`candidate_recall_11901._norm_only_match`), so the before/after is one run
    on one corpus and the "before" cannot move when the "after" changes.

    ⛔ ``in_window`` is recorded BESIDE the rate, because a variant whose canonical
    never entered the window cannot be attached by ANY matcher: without it,
    "the matcher failed" and "retrieval failed" are one number, and only one of
    them is a matching defect.
    """
    from tools.candidate_recall_11901 import _norm_only_match, variants
    from tortoise.extractor_v2 import _find_existing_entity

    rows: dict[str, dict] = {
        "pairs": 0, "in_window": 0, "in_window_not_attached": 0,
        "shipped_attached": 0, "norm_only_attached": 0,
    }
    by_class: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for qid in pin["query_ids"]:
        row = by_id[qid]
        for vclass, surface in variants(row["name"], row["cls"]):
            cand_rows, _ = _query_rows("capture_prior", ctx, surface, PRIOR_WINDOW)
            rows["pairs"] += 1
            by_class[vclass]["pairs"] += 1
            in_win = any(_rid(r) == row["id"] for r in cand_rows)
            if in_win:
                rows["in_window"] += 1
                by_class[vclass]["in_window"] += 1
            legacy = _norm_only_match(cand_rows, surface, "core:other")
            if legacy is not None and legacy.get("id") == row["id"]:
                rows["norm_only_attached"] += 1
                by_class[vclass]["norm_only"] += 1
            cand, _mode = _find_existing_entity(cand_rows, surface, "core:other")
            if cand is not None and cand.get("id") == row["id"]:
                rows["shipped_attached"] += 1
                by_class[vclass]["shipped"] += 1
            elif in_win:
                rows["in_window_not_attached"] += 1
                by_class[vclass]["in_window_not_attached"] += 1

    pairs = rows["pairs"]

    def _rate(attached: int) -> dict:
        return {"attached": attached, "minted": pairs - attached,
                "rate": (round((pairs - attached) / pairs, 4) if pairs else None)}

    out = {
        "window_k": PRIOR_WINDOW,
        "pairs": pairs,
        "retrieval_in_window": rows["in_window"],
        "retrieval_in_window_rate": (round(rows["in_window"] / pairs, 4)
                                     if pairs else None),
        "in_window_not_attached": rows["in_window_not_attached"],
        "shipped": _rate(rows["shipped_attached"]),
        "norm_only": _rate(rows["norm_only_attached"]),
        "by_class": {
            c: {"pairs": v["pairs"],
                "retrieval_in_window": v["in_window"],
                "shipped_attached": v["shipped"],
                "norm_only_attached": v["norm_only"],
                "in_window_not_attached": v["in_window_not_attached"],
                "shipped_mint_rate": (round((v["pairs"] - v["shipped"]) / v["pairs"], 4)
                                      if v["pairs"] else None),
                "norm_only_mint_rate": (round((v["pairs"] - v["norm_only"]) / v["pairs"], 4)
                                        if v["pairs"] else None)}
            for c, v in sorted(by_class.items())},
    }
    return out


def _user_door_binding() -> dict:
    """The registry binding that makes the measured surface the USER's surface.

    Read, never assumed: `tortoise_search` is the MCP tool and the REST route, and
    `sdk_method` is the verb it resolves to. If that binding ever changes, this
    field changes with it and the artifact cannot keep claiming the old door.
    """
    from tortoise.tool_registry import get_tool_by_name

    tool = get_tool_by_name().get("tortoise_search")
    if tool is None:
        return {"tool": "tortoise_search", "present": False}
    rest = getattr(tool, "rest_spec", None)
    sdk_method = getattr(tool, "sdk_method", None)
    return {
        "tool": "tortoise_search",
        "present": True,
        "sdk_method": sdk_method,
        "id": getattr(tool, "id", None),
        "rest": (f"{rest.method} {rest.path}" if rest is not None else None),
        # The arms call `sdk.tortoise_fts_query`, so this is the checked fact
        # behind "we measured the user's door".
        "matches_measured_call": sdk_method == "tortoise_fts_query",
    }


#: The one sentence the artifact makes about the door, DERIVED from the binding —
#: never a constant. A hand-written "sdk.tortoise_fts_query = MCP tortoise_search /
#: REST GET /v1/search" asserts the door was measured even when the binding this
#: same run read says it was not (`present: False`), which is the difference
#: between a checked fact and prose. `run()` refuses to write an artifact at all
#: when `matches_measured_call` is not True (see its guard), and this builder is
#: what makes the recorded sentence carry the binding it was derived from.
_USER_DOOR_MEASURED_TEMPLATE = "sdk.{sdk_method} = MCP {tool} / REST {rest}"


def _user_door_measured(binding: dict) -> str:
    """The artifact's `user_door_measured` sentence, built from the live binding.

    With today's registry (`tool_registry.py`: `sdk_method="tortoise_fts_query"`,
    `RestSpec(GET, "/v1/search")`) this renders exactly the string the committed
    artifact carries, so deriving it changes no evidence — it removes the only
    way the sentence could survive a binding that no longer matches.
    """
    if not (binding.get("present") and binding.get("matches_measured_call")):
        return ("NOT-MEASURED: the registry binding for `tortoise_search` does "
                "not resolve to the call the arms make (`sdk.tortoise_fts_query`)")
    return _USER_DOOR_MEASURED_TEMPLATE.format(
        sdk_method=binding["sdk_method"], tool=binding["tool"],
        rest=binding["rest"])


def _cleanup(graph: str) -> dict:
    """The residue this run leaves behind, DECLARED rather than assumed.

    The instrument's only teardown is `sdk.close()`: it does not drop the graph
    it populated. The same-epic sibling (`tools/window_quality_m2.py`) records a
    `cleanup` block for exactly this reason — a shared FalkorDB accumulates one
    full population per run (#5084), and a reader sizing the lane's footprint has
    no other way to learn that the graph is still there. The convention is
    `graphs_dropped`, not "cleaned up": claiming a cleanup that did not happen is
    how residue becomes invisible.
    """
    return {
        "graphs_created": 1,
        "graphs_dropped": 0,
        "left_in_place": [graph],
        "note": ("this run does NOT drop the graph it populated (`sdk.close()` "
                 "is the only teardown), so a run leaves ONE POPULATED graph "
                 "behind on whatever FalkorDB `TORTOISE_DB_URI` names. The "
                 "block declares the residue instead of claiming a cleanup "
                 "that does not happen (`tools/window_quality_m2.py` records "
                 "the same shape). Drop it with `redis-cli -h <host> -p <port> "
                 "GRAPH.DELETE <name>`, or run the lane on its own container "
                 "(`tools/test_lane.py down` disposes of it) — a shared server "
                 "accumulates one full population per run (#5084)."),
    }


def _git_head() -> str | None:
    """The HEAD the artifact records as ``code_head``, or ``None``.

    ⛔ A FAILURE HERE IS ANNOUNCED, NEVER SWALLOWED (round 27, measured). Every
    other provenance guard in :func:`run` REFUSES rather than degrading quietly (the
    door binding, the URI check, the stale-name scan, the non-fixture rows); this one
    returned ``None`` on any exception, so a run whose ``git rev-parse`` failed — no
    git on PATH, a wrong ``REPO_ROOT``, the 10 s timeout, a permissions error — wrote
    an artifact with ``"code_head": null`` and the ``code_head`` <unknown> note, then
    exited 0. The absence was indistinguishable, in the operator's terminal, from a
    normal run. The return contract is kept (``None`` is a shape `_code_head_note`
    handles), but the condition is now printed to stderr so a null `code_head` is an
    announced state rather than a silent one.
    """
    import subprocess
    import sys
    try:
        done = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        head = done.stdout.strip()
        if done.returncode == 0 and head:
            return head
        # ⛔ A NON-ZERO EXIT IS THE COMMON CASE, NOT THE RARE ONE (round 27). `git -C
        # <bad root> rev-parse HEAD` does not RAISE — it exits 128 — so guarding only
        # `except` left the real path silent: `stdout.strip() or None` returned `None`
        # with no diagnostic, which is exactly the swallow this guard is for.
        print(f"capstone: could not resolve HEAD in {REPO_ROOT} (git exit "
              f"{done.returncode}): {(done.stderr or done.stdout).strip()[:300]} — the "
              f"artifact will record `code_head: null`, which is a LABEL and not a "
              f"reproduction anchor", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"capstone: could not resolve HEAD in {REPO_ROOT}: {exc!r} — the "
              f"artifact will record `code_head: null`, which is a LABEL and not a "
              f"reproduction anchor", file=sys.stderr)
        return None


def _code_head_note(run_head: str | None) -> str:
    """The record's statement that ``code_head`` is a LABEL, not a runnable anchor.

    ⛔ A REBASE WOULD REWRITE THE RUN HEAD, AND THIS NOTE MUST NOT ASSERT THAT ONE
    ALREADY DID IT TO THE HEAD IT IS DESCRIBING (round-14 P2, measured). This
    measurement runs on a branch that is rebased onto ``main`` before it lands, so the
    sha a run records stops being reachable once that happens — but the note is BUILT AT
    WRITE TIME, when the head it names is still the branch tip, resolvable, and an
    ancestor of itself. The previous wording stated flatly that the branch's rebase
    "REWROTE" the sha and that it was "an ancestor of no published ref": a prediction
    dressed as a record, and false for every run whose note is written before the rebase
    (i.e. all of them, since the note is written by the run). The claim is therefore
    stated as the RULE — a rebase rewrites the label, and a rewritten label resolves
    nowhere — which is true whether or not the rebase has happened yet, rather than as a
    past event the writer cannot observe.

    ⛔ AND IT NAMES NO SHA-ANCHORED COMMAND. Spelling ``git show <label>:`` here, even
    as a warning, is how the record hands a reader a command that cannot resolve: the
    guard in ``tests/test_capstone_12186_recall.py`` pins every ``git show <hex>:``
    target in the document, and prose is not exempt.
    ``code_head`` answers *which run this is*, which is what it was named for.

    It is BUILT here rather than hand-written, for the reason the ``post_run_edits``
    mechanism itself exists: an edit nobody's builder emits is an edit the ledger
    cannot see, and a free-text reconciliation field is exactly what that mechanism
    replaced. A run therefore writes this note, and a hand-written one is declared in
    ``post_run_edits`` like every other.
    """
    label = (run_head or "<unknown>")[:12]
    return (
        f"`code_head` {label} is the RUN head and is a LABEL, not a reproduction "
        f"anchor. This branch is rebased onto `origin/main` before it lands, and a "
        f"rebase rewrites {label}, leaving it an ancestor of no published ref — a "
        f"`git show` against it then fails with `fatal: invalid object name` for a "
        f"reader without the pre-rebase object. The CONTENT is verifiable "
        f"even where the sha is not: reproduce at the branch's own head — the two "
        f"INPUTS this record names (`tools/capstone_12186_recall.py`'s query/recall "
        f"code and `docs/evidence/12186-capstone/queries.json`, whose sha256 the `pin` "
        f"block pins) are what a reproduction needs, and the tool's later changes are "
        f"comments and new builder functions, never the recall computation. Read "
        f"`code_head` for WHICH RUN this is; run the commands against `HEAD`. In a "
        f"shallow clone, `git fetch --unshallow` first."
    )


# ── the live-backend guard ───────────────────────────────────────────────────
#
# ⛔ THE ACCEPTED SET IS THE PRODUCT'S, NOT THIS INSTRUMENT'S OPINION (round-22
# P3, review F1). `tortoise/config.py:50` declares
# `SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")`, and
# `projection._validate_uri_scheme` REUSES that tuple so URI-routing and the
# connection layer cannot drift (#715 exists for exactly this mistake: `rediss://`
# was documented-supported but unrecognised). `rediss://` is FalkorDB Cloud —
# `FalkorProjection.from_uri` accepts it (`ssl=(parsed.scheme == "rediss")`) —
# so the pre-fix `("docker://", "redis://")` REFUSED a backend the product
# ships support for, and the refusal stated the opposite ("a backend the product
# cannot use"). This line was inherited verbatim from
# `tools/candidate_recall_11901.py:1400`; the repo already carries the corrected
# tuple in `tools/ax_write_eval.py:363`.
URI_PREFIXES = ("docker://", "redis://", "rediss://")
URI_REFUSAL = (
    "REFUSED: no live FalkorDB in TORTOISE_DB_URI — a recall figure "
    "measured on a backend the product cannot use is not a figure.\n"
    "  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\""
)


def _uri_refusal(uri: str) -> str | None:
    """The refusal for a backend the PRODUCT cannot use, else ``None``.

    A function rather than an inline `if` because the guard shipped with NO pin
    at all (round-22, review F1): the only test that reached `run()` asserted the
    DOOR arm's reason and that the URI arm did *not* answer, so no mutant of this
    predicate reddened anything — this file's own rule 3 ("an assertion whose
    value cannot vary is a FINDING: make it reachable") applied to a guard with
    no assertion. One call site, one pin, in both directions.
    """
    if uri.startswith(URI_PREFIXES):
        return None
    return URI_REFUSAL


def run(args: argparse.Namespace) -> int:
    from tools.underscore_or_terms_12360 import wait_indexed
    from tortoise.embeddings import EmbeddingModel
    from tortoise.extractor_v2 import _fts_rows
    from tortoise.projection import FalkorProjection
    from tortoise.sdk import TortoiseSDK

    # ⛔ THE RUN HEAD IS SAMPLED ONCE (round-14 P2). `code_head` and `code_head_note`
    # are both derived from it, ~21 minutes apart in a real run; two `_git_head()`
    # calls let a commit landing between them write a note that describes a head the
    # record does not name.
    head = _git_head()

    pin_path = Path(args.pin)
    pin, pin_sha = load_pin(pin_path)
    rows = pin["population_rows"]
    by_id = {r["id"]: r for r in rows}

    # ⛔ REFUSE to measure a door that is not the door the arms call. The
    # artifact's `user_door_measured` sentence is derived from this binding (never
    # a constant), so a run whose binding does not resolve to
    # `sdk.tortoise_fts_query` would record "NOT-MEASURED" beside 21 minutes of
    # arm figures — a record that cannot say which surface it measured. Checking it
    # BEFORE the writes also means a registry change costs no graph, no embedder
    # and no query budget.
    user_door = _user_door_binding()
    if not user_door.get("matches_measured_call"):
        print("REFUSED: the registry binding for `tortoise_search` does not "
              "resolve to the call the arms make — this run cannot claim it "
              "measured the USER's door.", file=sys.stderr)
        print(f"  binding={user_door!r}", file=sys.stderr)
        print("  expected: sdk_method='tortoise_fts_query' "
              "(tortoise/tool_registry.py)", file=sys.stderr)
        return 3

    uri = os.environ.get("TORTOISE_DB_URI") or ""
    uri_refusal = _uri_refusal(uri)
    if uri_refusal is not None:
        print(uri_refusal, file=sys.stderr)
        return 3

    graph = args.graph or f"cap12186_{int(time.time())}_{os.getpid()}"
    t_mark = time.time()
    proj = FalkorProjection.from_uri(uri, graph_name=graph)
    sdk = TortoiseSDK(namespace=args.namespace, graph_name=graph)
    try:
        existing = proj.g.query(
            "MATCH (o:Object) WHERE NOT o.id STARTS WITH 'cap-' "
            "RETURN count(o)").result_set
        if existing and int(existing[0][0] or 0) > 0:
            print(f"REFUSED: graph {graph!r} already holds {existing[0][0]} "
                  f"non-fixture Object rows — pass a FRESH --graph", file=sys.stderr)
            return 3

        # ⛔ WARM THE EMBEDDER *BEFORE* THE WRITES, and record whether it was
        # there. `_upsert_object` embeds through the lazy singleton, and a COLD
        # model stores the row WITHOUT a vector (``#13224``: "this DERIVED entity
        # row was stored WITHOUT a vector") — the row is then invisible to the
        # dense leg forever. A hybrid arm measured over such a store is an
        # FTS-only figure wearing a hybrid label, which is the exact false label
        # ``battery/runner/retrieval_preflight.py`` exists to prevent. Measured
        # as a real incident on this instrument's first run: the population was
        # written before the model load finished.
        embedder = EmbeddingModel.get()
        embedder_at_write = type(embedder).__name__ if embedder else None
        timings: dict[str, float] = {"embedder_warm_s": round(time.time() - t_mark, 1)}
        t_mark = time.time()
        if embedder is None:
            print("WARNING: no dense leg (EmbeddingModel.get() -> None) — the "
                  "Object rows are stored WITHOUT vectors and every arm will "
                  "measure the LEXICAL leg. The artifact records this; do not "
                  "quote a cell as a fused production read.", file=sys.stderr)

        for row in rows:
            proj._upsert_object({"id": row["id"], "name": row["name"],
                                 "object_kind": "core:other"})
        timings["writes_s"] = round(time.time() - t_mark, 1)
        t_mark = time.time()
        # `o.name` is an ON CREATE write (#8919): a reused graph silently
        # measures the previous population, and the failure direction is
        # UNDERSTATED recall — i.e. the headline. Check, never assume.
        stored = {r[0]: r[1] for r in proj.g.query(
            "MATCH (o:Object) WHERE o.id STARTS WITH 'cap-' "
            "RETURN o.id, o.name", params={}).result_set}
        stale = [r["name"] for r in rows if stored.get(r["id"]) != r["name"]]
        if stale:
            print(f"REFUSED: {len(stale)} of {len(rows)} names did not land "
                  f"(first: {stale[:3]!r}) — `o.name` is ON CREATE (#8919); "
                  f"pass a fresh --graph.", file=sys.stderr)
            return 3

        # Dense COVERAGE, counted rather than assumed: a leg that "ran" over a
        # store whose rows carry no vector is a lexical read with a hybrid label.
        with_vector = proj.g.query(
            "MATCH (o:Object) WHERE o.id STARTS WITH 'cap-' "
            "AND o.embedding IS NOT NULL RETURN count(o)").result_set
        vector_rows = int(with_vector[0][0] or 0) if with_vector else 0

        ctx = {
            "sdk": sdk, "fts_rows": _fts_rows, "by_id": by_id,
            "modes": {}, "legs": {}, "leg_reasons": {}, "rerank_ran": 0,
        }
        # Warm the leg, then wait for the BACKGROUND index cycle: a query issued
        # straight after the writes books a present name as a miss.
        sdk.tortoise_fts_query("warm", entity_type="object", limit=1)
        indexed, waited = wait_indexed(proj, len(rows))
        timings["index_wait_s"] = round(time.time() - t_mark, 1)
        t_mark = time.time()

        arms: dict[str, dict] = {}
        # The QUERIES are population ROWS (id + name + class), resolved once from
        # the pin: a bare id string is not a query, and resolving it here (rather
        # than inside every arm) keeps the arms on one object.
        queries = [by_id[q] for q in pin["query_ids"]]

        # Every arm's env override is applied for that arm ONLY and restored
        # afterwards, and the SHIPPED state is asserted before the shipped arms
        # run: an ambient `TORTOISE_UNDERSCORE_TERMS=0` (or an armed rerank) would
        # silently turn the "shipped" arm into a second "pre_lever" arm — a
        # comparison of two identical measurements reported as a lever.
        saved = {k: os.environ.get(k) for k in
                 ("TORTOISE_UNDERSCORE_TERMS", "TORTOISE_FUSED_RERANK")}
        try:
            for arm, door, env in ARMS:
                for key in saved:
                    os.environ.pop(key, None)
                os.environ.update(env)
                _reset_ctx(ctx)
                arms[arm] = measure_arm(arm, door, queries, ctx)
                _attach_ctx(arms[arm], ctx)
                arms[arm]["rerank_entries_ran"] = ctx["rerank_ran"]
                arms[arm]["env"] = dict(env)
            timings["arms_s"] = round(time.time() - t_mark, 1)
            t_mark = time.time()

            # ⛔ THE MINT ARM IS THE **SHIPPED** MATCHER AND MUST BE MEASURED INSIDE THIS
            # GUARD (round 20, measured). It used to run AFTER the `finally` that
            # restores the ambient environment, while recording no `env` field at all —
            # so on a box exporting `TORTOISE_UNDERSCORE_TERMS=0` (the documented bisect
            # switch, and exactly how the `*_pre_lever` arms are produced) the arm
            # published as "shipped matcher" was measured on the PRE-LEVER retrieval
            # path, and §3b/§4's `1.0000 -> 0.5328` came out of a window nobody declared.
            # The comment above the loop states this hazard for every arm and left the
            # last one out of it. The shipped state is set here explicitly (both vars
            # popped) rather than inherited from the loop's last iteration.
            for key in saved:
                os.environ.pop(key, None)
            _reset_ctx(ctx)
            arms["mint_rate"] = mint_rate(pin, by_id, ctx)
            _attach_ctx(arms["mint_rate"], ctx)
            arms["mint_rate"]["env"] = {}
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value
        timings["mint_rate_s"] = round(time.time() - t_mark, 1)

        pairs = {}
        for before, after, lever in PAIRS:
            pairs[f"{before} -> {after}"] = {
                "lever": lever,
                "paired": paired(arms[before], arms[after], PAIRED_K),
            }

        merged_lever_visibility = {
            "user_door_measured": _user_door_measured(user_door),
            "user_search_retrieval_mode": arms["user_search"]["retrieval_mode"],
            "underscore_terms_shipped_default": "ON",
            "underscore_terms_visible_on_user_door":
                pairs["user_search_pre_lever -> user_search"]["paired"],
            "fused_rerank_shipped_default": "OFF",
            "fused_rerank_ran_in_user_search": bool(
                arms["user_search"].get("legs_seen", {}).get("rerank")),
            "fused_rerank_effect_on_user_door_if_armed":
                pairs["user_search -> user_search_rerank"]["paired"],
        }

        report = {
            "issue": "12186",
            "capstone": True,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "code_head": head,
            #: The record's own statement that `code_head` is a LABEL (a rebase rewrites
            #: it) and where a reproduction should actually run. BUILT, not typed — see
            #: `_code_head_note`.
            #: ⛔ ONE SAMPLE, TWO FIELDS (round-14 P2). These were two separate
            #: `_git_head()` calls ~21 minutes into the run, so a commit landing between
            #: them wrote a `code_head` its own `code_head_note` did not describe — and
            #: the artifact's own equality pin reds on the artifact the tool just wrote.
            "code_head_note": _code_head_note(head),
            #: The edits made to THIS record AFTER the run — `[]` when the record
            #: is exactly what the run wrote. It exists because `code_head` is the
            #: record's reproduction anchor: a block inserted by hand (`§4`'s
            #: committed `cleanup`, whose tool at that head has no such builder)
            #: makes the record NOT reproducible from the head it names, and a
            #: reader has no other way to learn that. The block is a declaration,
            #: not a fig leaf: every figure named here must still be run output.
            "post_run_edits": [],
            "embedder_at_write": embedder_at_write,
            "pin": {
                "path": _repo_relative(pin_path),
                "sha256": pin_sha,
                "schema": pin.get("schema"),
                "digest": pin.get("digest"),
                "query_digest": pin.get("query_digest"),
                "built_at": pin.get("built_at"),
                "population": len(rows),
                "queries": len(pin["query_ids"]),
                "shares": pin.get("rules", {}).get("shares"),
                "corpus": pin.get("corpus"),
            },
            "ks": list(KS),
            "neardup_rule": {"version": NEARDUP_RULE,
                             "jaccard_threshold": NEARDUP_JACCARD,
                             "key": NEARDUP_KEY_NOTE},
            "user_door": user_door,
            "arms": arms,
            "paired": pairs,
            "merged_lever_visibility": merged_lever_visibility,
            "cleanup": _cleanup(graph),
            "graph": {"name": graph, "indexed_docs": indexed,
                      "index_wait_s": waited,
                      "rows_with_vector": vector_rows,
                      "vector_coverage": (round(vector_rows / len(rows), 4)
                                          if rows else None)},
            "timings": timings,
            "comparisons": _comparisons(arms),
        }
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(report, indent=1,
                                                 sort_keys=True) + "\n")
        for arm, result in arms.items():
            if "recall" in result:
                print(f"{arm:24s} recall={result['recall']} "
                      f"dup@3={result['duplicate_rate_in_window']['3']['rate']} "
                      f"mode={result['retrieval_mode']}")
            else:
                print(f"{arm:24s} pairs={result['pairs']} "
                      f"in_window={result['retrieval_in_window']} "
                      f"shipped_attached={result['shipped']['attached']} "
                      f"norm_only_attached={result['norm_only']['attached']}")
        for name, block in pairs.items():
            p = block["paired"]
            print(f"PAIRED {name}: clean={p['queries_compared']} "
                  f"(excluded {p['excluded_degraded_on_either_side']}) "
                  f"{p['before_recall_clean']} -> {p['after_recall_clean']} "
                  f"({p['delta_pts_clean']:+} pts, +{p['gains']}/-{p['losses']}, "
                  f"p={p['p_display']})")
        return 0
    finally:
        import contextlib
        with contextlib.suppress(Exception):
            sdk.close()


def _comparisons(arms: dict) -> dict:
    """What each number may and may not be compared against — stated, not implied.

    A capstone that prints `recall@3 = 0.98` invites a reader to put it beside the
    0.7133 baseline in the epic's body. These are DIFFERENT populations, so the
    artifact refuses the comparison by name instead of leaving a reader to make
    it. Every entry says which side of the line it is on and why.
    """
    return {
        "same_corpus_as": [],
        "different_corpus_from": {
            "0.7133":
                "the pre-lever baseline quoted in #12186, measured on the "
                "#12298/#12360 pin (300 names, sha256 095d3aa5…, a 2026-11-13 "
                "harvest). This run's population is a different freeze of the "
                "same RULES on a later corpus, so the two are NOT the same "
                "measurement and must not be differenced: the names, their "
                "frequencies and the near-duplicate structure all differ. The "
                "SAME-CORPUS before/after is `paired`, which is why it exists.",
            "#11901 (k=3 61.3 %, k=5 65.0 %, k=10 69.0 %, k=15 73.0 %)":
                "measured on #11901's live harvest with HYBRID retrieval and no "
                "pin (its own brief §2.2 records that its population is not "
                "re-provable). This run's numbers are on the frozen pin, and the "
                "retrieval mode is recorded in each arm — read it before "
                "comparing any cell.",
        },
        "within_run_comparisons_that_ARE_valid": [
            "Any pair in `paired` — one door, one pin, one graph, one process, "
            "one env flag apart, restricted to windows clean in BOTH arms.",
            "capture_prior_pre_lever -> capture_prior — the same-corpus "
            "before/after of #12368's composite terms on the write path.",
            "user_search_pre_lever -> user_search — the same, on the user door.",
            "user_search -> user_search_rerank — same door, one env flag; the "
            "difference is the merged cross-encoder lever (armed vs shipped "
            "OFF). Read the paired cell, not the raw one: the raw arm figure "
            "carries the box's leg timeouts.",
            "capture_prior -> user_search — the DOOR (internal path vs the "
            "surface a user hits) on one corpus.",
            "mint_rate norm_only vs shipped — same pairs, frozen control; the "
            "difference is the merged #12304 fold.",
        ],
        "read_the_retrieval_mode_beside_every_cell":
            "a keyword_only arm is a real figure about the LEXICAL leg and is "
            "not a figure about the fused production read (#2952/#3005). "
            "`window_modes` gives the per-window split behind a MIXED label, and "
            "`leg_degrade_reasons` names why.",
        "note_on_arms": {name: arms[name].get("retrieval_mode", "n/a")
                         for name in arms},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build-pin", help="freeze the fixed query set (committed)")
    bp.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    bp.add_argument("--population", type=int, default=1000)
    bp.add_argument("--sample", type=int, default=300)
    bp.add_argument("--out", default=str(DEFAULT_PIN))
    bp.add_argument("--force", action="store_true",
                    help="replace an existing pin — a deliberate act, not a default")
    bp.set_defaults(func=build_pin)

    rp = sub.add_parser("run", help="measure the arms on the pinned query set")
    rp.add_argument("--pin", default=str(DEFAULT_PIN))
    rp.add_argument("--namespace", default="capstone12186")
    rp.add_argument("--graph", default=None,
                    help="graph to write the population to; MUST be fresh "
                         "(names are ON CREATE, #8919)")
    rp.add_argument("--out", default=str(EVIDENCE_DIR / "measurement.json"))
    rp.set_defaults(func=run)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
