#!/usr/bin/env python
"""Candidate recall@k for the capture-time link-before-create prior (#11901).

WHAT THIS MEASURES, AND WHY THE THREE ARMS ARE MEASURED SEPARATELY
---------------------------------------------------
Every capture derives one ``object`` query per extracted entity name and feeds
it to the hybrid read at ``limit=3`` (``extractor_v2._derive_queries`` ->
``_fts_rows`` -> ``sdk.tortoise_fts_query`` -> ``search_engine.degradation_chain``).
The candidate set that window produces is the whole input to identity: if the
right existing entity is not in it, no adjudicator downstream can pick it
(``docs/research/2026-09-24-2730-entity-attachment/research-brief.md`` §6.2, X1).

The window is only half of the pipeline. ``resolve_entities`` phase 1 attaches a
mention with ``_find_existing_entity``, which is **exact normalised-name
equality** (``_norm`` = whitespace collapse + lowercase, nothing else) plus a
kind-compatibility check. So a candidate can be *inside* the window and still be
unusable, and the two failures have different fixes:

  * RETRIEVAL failure — the right node never entered the window.
    Widening ``limit`` is the response, and it costs adjudication quality
    (research-brief X7: selection accuracy falls as the candidate list grows).
  * MATCHING failure — the right node IS in the window and the shipped matcher
    cannot bind it, because the mention is a punctuation/article variant of the
    stored name. Widening ``limit`` cannot help at any k; the normalisation is
    the constraint.

This tool measures both, on a real entity-name population, through the real
code path, so "does the window earn its keep" is answered by a number instead of
by an argument. Three arms, because the failures have different fixes:

  ARM 1  RETRIEVAL     a mention that IS the stored name — how often is the node
                       in the window at each k? (widening can help)
  ARM 2  MATCHING      a mention that is a real surface VARIANT of the stored
                       name — how often does phase 1 bind it? This arm ALSO
                       measures `attached_folded`, what a candidate punctuation
                       fold would recover, and that fix is NOT shipped here: the
                       point of #11901 is to decide on evidence, not to patch.
  ARM 3  THE UNION     how large is the candidate set a capture actually hands
                       `resolve_entities`? (not `limit` — see below)

POPULATION — WHAT IS REAL AND WHAT IS NOT (read before quoting a number)
-----------------------------------------------------------------------
REAL: the names. They are harvested from the capture corpus on this box
(``~/.tortoise/session-events/*.jsonl`` — thousands of real agent sessions) by
the declared deterministic rules in :func:`harvest`, and the graph is a real
FalkorDB built through the real projection (``_upsert_object``), queried through
the real ``sdk.tortoise_fts_query`` leg with the same flags ``_fts_rows`` passes.

NOT REAL, declared: the population is a *sample* of the strings those captures
contain, not the ``:Object`` rows a production graph actually holds. The
production population is not reachable from a lane (no live ``:Object`` graph on
this box, and the hosted graph carries the fleet's memory Points rather than
product entities), so a lane cannot census it. §12.1a/§14.3 record the same gap
for the vector population. Treat every recall figure below as a measurement of
**the retrieval and matching mechanism on a real name distribution**, never as a
census of production.

THE HARVEST RULES, stated so the population is reproducible and criticisable
--------------------------------------------------------------------------------
``definite``  — ``the <1-4 tokens>``, which is the class 62.3% of ``:Object``
                names belong to (research-brief §3.2). Frequency-ranked, because
                a name that recurs across captures is the one link-before-create
                exists to re-identify.
``ident``     — ``#<n>`` references (bare ``#<n>`` 0.5 % and ``PR #<n>`` 1.7 % of
                real ``:Object`` names — two of the audit's rows, one class here)
                and snake_case identifiers, which sit inside the audit's 9.0 %
                "names containing a code token" row (a SUPERSET: it also counts
                ``.py``, ``()``, ``--``). One class because both fail by lookup,
                not by description.
``path``      — repo-relative file paths. ⛔ §3.2 records NO share for this shape
                (its 1.7 % is ``PR #<n>``/``issue #<n>``, not paths), so the path
                class is justified by the shape being real, not by an audit
                proportion — and it is measured apart (``_add("path",`` below,
                ``variant_by_class``) because a path's variants are basenames,
                not articles.

USAGE
    # 1. start a lane container and export its four seams
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"                      # two steps on purpose — see test_lane's USAGE
    # 2. run the measurement (NOT via `test_lane.py run`, which executes pytest)
    uv run python tools/candidate_recall_11901.py \
        --population 1000 --sample 300 --captures 20 --out /tmp/recall-11901.json

⚠️ **The dense leg matters and is recorded, not assumed.** `EmbeddingModel.get()`
is ``None`` on a venv without the ``embeddings`` extra, and the fused read then
silently degrades to keyword-only — ``battery/runner/retrieval_preflight.py``
names a published FTS-only run as the class it exists to prevent. Install it
(`uv sync --extra embeddings --extra parity`) before measuring anything you
intend to quote, and read ``retrieval`` in the artifact: it states which mode
ran. A keyword-only figure is a real figure about the LEXICAL leg and is never
a figure about the fused production read.

Requires a live FalkorDB in ``TORTOISE_DB_URI``. It REFUSES to run without one
rather than falling back to the embedded engine — S3 itself refuses embedded
backends for the same reason.

EXIT CODES: 0 ok · 2 usage · 3 no reachable graph (never a silent skip)
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The window widths #11901 names: today's 3, and the 5/10/15 the issue asks for
# (15 is Zep/Graphiti's candidate count — research-brief §6.2).
KS: tuple[int, ...] = (3, 5, 10, 15)

#: ``the <1-4 tokens>`` — the definite-description class. Bounded to 4 tokens:
#: longer spans are clauses, not names, and the audit found real names are short.
_DD = re.compile(r"\bthe\s+([a-z][a-z0-9]*(?:[ _-][a-z0-9]+){0,3})")
_ISSUE_REF = re.compile(r"#\d{3,5}\b")
_PATH = re.compile(r"\b(?:[\w.-]+/){1,3}[\w.-]+\.(?:py|md|yml|yaml|json|sh|ts|sql)\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){1,3}\b")

#: Stems that make a definite description a *clause fragment* rather than a
#: name. Excluded so the population is not dominated by "the same", "the two",
#: "the next" — those are function words the extractor discards, not entities.
_STOP_FIRST = frozenset("""
same two next other first second last whole only few many more most such
one ones thing things way ways one's rest above below following
""".split())
#: A definite description ending in one of these is a sentence fragment.
_STOP_LAST = frozenset("""
be been being is are was were do does did done have has had will would can
could should must may might not no yes and or but if then than that this
these those it its they them their we our you your he she his her
""".split())


def harvest(sessions_dir: Path) -> dict[str, collections.Counter]:
    """Real capture text -> candidate entity names, by the three declared rules.

    Returns ``{class: Counter(name -> record-frequency)}``. Frequency counts the
    number of **session records** a name appears in — NOT its turns and NOT its
    raw occurrences. Recurrence across sessions is what makes a mention a
    candidate for re-identification, and a session that repeats a phrase 40
    times in one turn is one mention. (Stated this narrowly because the first
    draft called it "turn-frequency" while the ``seen`` set was created per
    record, which is a different population — review round 2, F5.)
    """
    out: dict[str, collections.Counter] = {c: collections.Counter()
                                           for c in ("definite", "ident", "path")}
    files = sorted(sessions_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"no session events under {sessions_dir}")
    for f in files:
        for line in f.open(errors="replace"):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            parts = []
            for turn in rec.get("conversation") or []:
                c = turn.get("content")
                if isinstance(c, str):
                    parts.append(c)
            if not parts:
                continue
            blob = "\n".join(parts)
            seen: set[tuple[str, str]] = set()

            def _add(cls: str, name: str) -> None:
                name = name.strip()
                if not name or (cls, name) in seen:
                    return
                seen.add((cls, name))
                out[cls][name] += 1

            for m in _DD.finditer(blob):
                phrase = m.group(0)
                toks = re.split(r"[\s_-]+", phrase)
                if toks[1] in _STOP_FIRST or toks[-1] in _STOP_LAST:
                    continue
                if len(toks[1]) < 2:
                    continue
                _add("definite", phrase)
            for m in _ISSUE_REF.finditer(blob):
                _add("ident", m.group(0))
            for m in _SNAKE.finditer(blob):
                _add("ident", m.group(0))
            for m in _PATH.finditer(blob):
                _add("path", m.group(0))
    return out


def build_population(harvested: dict[str, collections.Counter], n: int) -> list[dict]:
    """Top-``n`` names by record-frequency, class-balanced, deterministically ordered.

    ⛔ The quotas are a DELIBERATE OVERSAMPLE of the minority shapes, not the
    audit's class shares: the audit measured identifiers at ~2.2% of real
    ``:Object`` names (0.5% bare ``#<n>`` + 1.7% ``PR #<n>``) and the definite
    description at 62.3%, and this population gives identifiers 30% and paths
    10% so each class carries enough names for a usable per-class rate. **The
    composite headline is therefore stratified, never class-share weighted** —
    it is not an estimate of the production mix, and the per-class table is the
    number to quote for any one class. Ties break on the name so two runs are
    byte-identical.
    """
    quotas = {"definite": int(n * 0.60), "ident": int(n * 0.30), "path": n - int(n * 0.60) - int(n * 0.30)}
    pop: list[dict] = []
    for cls, quota in quotas.items():
        ranked = sorted(harvested[cls].items(), key=lambda kv: (-kv[1], kv[0]))[:quota]
        pop.extend({"name": name, "cls": cls, "records": records}
               for name, records in ranked)
    return pop


# ── the transformations ``_norm`` does NOT fold ──────────────────────────────
# ``_norm`` is whitespace-collapse + lowercase. Each variant below is a surface
# form a later capture plausibly produces for the SAME referent, and each is
# invisible to the shipped matcher. These are the duplicate-manufacturing cases.

def variants(name: str, cls: str) -> list[tuple[str, str]]:
    """``[(variant_class, surface_form)]`` — the mention shapes that split a node."""
    out: list[tuple[str, str]] = []
    if cls == "definite" and name.startswith("the "):
        out.append(("article-drop", name[4:]))
    if "-" in name:
        out.append(("hyphen-to-space", name.replace("-", " ")))
    if " " in name and cls == "definite":
        out.append(("space-to-hyphen", name.replace(" ", "-")))
    if cls == "path":
        stem = name.rsplit("/", 1)
        if len(stem) == 2 and stem[1] not in ("", name):
            out.append(("basename-only", stem[1]))
    if cls == "ident" and name.startswith("#"):
        out.append(("hash-dropped", name[1:]))
    return out


def _has_embedder() -> bool:
    """Is the dense leg available? Recorded, because it changes the measurement."""
    try:
        from tortoise.embeddings import EmbeddingModel
        return EmbeddingModel.get() is not None
    except Exception:
        return False


def _mode_note(has_embedder: bool, legs_seen: dict[str, bool]) -> str:
    """What the fused read ACTUALLY submitted — from the traces, not a guess."""
    vector = bool(legs_seen.get("vector"))
    if vector:
        return "hybrid"
    if not has_embedder:
        return ("⚠️ KEYWORD-ONLY: no embedder on this box, so the dense leg was "
                "never submitted — the numbers describe the lexical leg only, "
                "not the fused production read")
    return ("⚠️ KEYWORD-ONLY DESPITE AN EMBEDDER: the model loaded but the vector "
            "leg never ran (encode failure, breaker open, or an empty trace) — "
            "read `legs_seen`, not `has_embedder`")


def _fold_punct(text: str) -> str:
    """The candidate fix, measured but NOT shipped: fold the separator class.

    Deliberately only the separator class (``-``/``_``/``.``/``/``) plus case and
    whitespace. It is not a general normaliser and it does not touch word order
    or morphology, so it cannot merge two genuinely different names by itself —
    which is the asymmetric-error rule this repo already carries (#1370: a false
    merge is invisible and a false split is cheap, so be conservative).
    """
    t = re.sub(r"[-_./]+", " ", (text or "").strip().lower())
    return re.sub(r"\s+", " ", t).strip()


def run(args: argparse.Namespace) -> int:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if not uri.startswith(("docker://", "redis://")):
        print("REFUSED: no live FalkorDB in TORTOISE_DB_URI — S3 itself refuses "
              "the embedded backend, and a recall figure measured on a backend "
              "the product cannot use is not a figure.\n"
              "  start one:  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\"\n"
              "  (test_lane.py `run` executes PYTEST, not this tool — see USAGE)",
              file=sys.stderr)
        return 3

    from tortoise.extractor_v2 import _find_existing_entity, _sanitise_query_text
    from tortoise.sdk import TortoiseSDK

    sessions = Path(os.path.expanduser(args.sessions_dir))
    t0 = time.time()
    harvested = harvest(sessions)
    pop = build_population(harvested, args.population)
    if not pop:
        print("REFUSED: the harvest produced no names", file=sys.stderr)
        return 3
    counts = {c: len(v) for c, v in harvested.items()}

    sdk = TortoiseSDK(namespace=args.namespace, graph_name=args.graph)
    try:
        proj = sdk._get_proj()
        # The population is written through the REAL projection: one Object per
        # name, ids minted here (the projection MERGEs on the id per #3590).
        for i, row in enumerate(pop):
            proj._upsert_object({"id": f"obj-11901{i:06d}", "name": row["name"],
                                 "object_kind": "core:other"})
        # One cache-warming query so the first measured query is not paying the
        # full-scan cold cost — the residual is the #4997 unindexed-label scan
        # (§14.3 V3), constant across every arm, so it cannot bias a comparison.
        sdk.tortoise_fts_query("warm", entity_type="object", limit=1,
                               include_terminal=True, _record_reads=False,
                               _valid_time_rerank=False)

        #: Which legs the fused read actually submitted. An FTS-only run is a
        #: DIFFERENT measurement from a hybrid one — the capture prior is the
        #: fused read, and a store with no embedder silently degrades to
        #: keyword-only (``battery/runner/retrieval_preflight.py`` records an
        #: FTS-only run published as a product measurement as the class this
        #: field exists to prevent). Recorded, never assumed.
        legs_seen: dict[str, bool] = {}

        def window(query: str, k: int) -> list[dict]:
            """EXACTLY the leg ``_fts_rows`` runs, at width ``k``.

            The query goes through ``_sanitise_query_text`` first because that
            is what production hands the leg: ``_derive_queries`` stores the
            sanitised form and ``_fts_rows`` forwards it verbatim. Skipping it
            here would measure a query shape the product never issues.
            """
            trace: list[dict] = []
            rows = sdk.tortoise_fts_query(
                _sanitise_query_text(query), entity_type="object", limit=k,
                include_terminal=True, _record_reads=False,
                _valid_time_rerank=False, leg_trace=trace)
            # ``_trace_entry`` emits {leg, ran, degraded, reason, count} — there
            # is no ``ok`` key, so reading one would make this predicate
            # ``count > 0`` ("returned a row somewhere") while the comment
            # claimed "was submitted". Read ``ran`` and nothing else: a leg that
            # was submitted and degraded still ran, and a leg that failed to
            # encode did not.
            for entry in trace or []:
                name = entry.get("leg") or entry.get("strategy") or "?"
                legs_seen[str(name)] = legs_seen.get(str(name), False) or bool(
                    entry.get("ran"))
            return [{"id": r.get("id", ""), "name": r.get("content", ""),
                     "kind": r.get("point_kind", "")} for r in (rows or [])]

        # STRATIFIED, not head-sliced: ``build_population`` emits class by class,
        # so ``pop[:n]`` would sample one class only and report its recall as the
        # population's. Each class contributes its own proportional share.
        _by_cls: dict[str, list[dict]] = collections.defaultdict(list)
        for row in pop:
            _by_cls[row["cls"]].append(row)
        sample: list[dict] = []
        for cls in sorted(_by_cls):
            share = round(args.sample * len(_by_cls[cls]) / len(pop))
            sample.extend(_by_cls[cls][:share])
        sample = sample[: args.sample] or pop[: args.sample]
        arms: dict[str, dict] = {}

        # ── ARM 1: the mention IS the stored name (the exact rung of the ladder)
        # A miss is split into the two states that have DIFFERENT fixes:
        #   ``miss_past_k``          the canonical is in the k=15 window and fell
        #                            past THIS k cut → width is the fix.
        #   ``miss_absent_at_max_k`` the canonical is absent even at k=15 → no
        #                            width reaches it; the leg's own recall is
        #                            the constraint.
        # ``rows_zero`` counts the sub-case where the leg returned nothing at
        # all, which is a different defect again and is counted separately so
        # "absent from a 15-row window" is never reported as "the leg answered
        # nothing".
        exact = {k: {"in_window": 0, "rank_sum": 0, "attached": 0,
                     "miss_absent_at_max_k": 0, "miss_past_k": 0,
                     "rows_zero": 0} for k in KS}
        exact_by_class: dict[str, dict[str, dict[str, int]]] = collections.defaultdict(
            lambda: {str(k): {"n": 0, "in_window": 0, "rows_zero": 0} for k in KS})
        exact_misses: list[dict] = []
        # Token-overlap siblings in the population: how crowded the window is
        # for a given name. Computed from the population, not the graph, so the
        # crowding claim is measured rather than asserted.
        _tok = {row["name"]: frozenset(re.findall(r"[a-z0-9]+", row["name"].lower()))
                for row in pop}
        _siblings = {n: sum(1 for m, t in _tok.items()
                            if m != n and len(t & _tok[n]) >= 2) for n in _tok}
        for row in sample:
            name = row["name"]
            wide = window(name, max(KS))
            for k in KS:
                rows = window(name, k)
                exact_by_class[row["cls"]][str(k)]["n"] += 1
                hit = next((i for i, r in enumerate(rows) if r["name"] == name), None)
                if hit is not None:
                    exact[k]["in_window"] += 1
                    exact_by_class[row["cls"]][str(k)]["in_window"] += 1
                    exact[k]["rank_sum"] += hit + 1
                else:
                    # one over-fetch tells "never returned" from "cut at k"
                    if any(r["name"] == name for r in wide):
                        exact[k]["miss_past_k"] += 1
                    else:
                        exact[k]["miss_absent_at_max_k"] += 1
                        if not rows:
                            exact[k]["rows_zero"] += 1
                            exact_by_class[row["cls"]][str(k)]["rows_zero"] += 1
                    if k == KS[0] and len(exact_misses) < 200:
                        toks = _sanitise_query_text(name).split()
                        exact_misses.append({
                            "name": name, "cls": row["cls"],
                            "returned": len(rows),
                            "wide_returned": len(wide),
                            "tokens": len(toks),
                            "siblings_2tok": _siblings.get(name, 0),
                            "tokens_kept_numeric": len(
                                [t for t in toks if not t.isdigit()]) != len(toks),
                            "wide_top": [r["name"] for r in wide[:5]],
                        })
                # phase 1 over the SAME window — the shipped matcher, real code
                cand, _mode = _find_existing_entity(rows, name, "core:other")
                if cand is not None:
                    exact[k]["attached"] += 1
        arms["exact"] = exact
        arms["exact_misses"] = exact_misses
        arms["exact_by_class"] = {c: dict(v) for c, v in sorted(exact_by_class.items())}

        # ── ARM 2: the mention is a surface VARIANT of the stored name.
        # The stored node exists; the capture says it a different (real) way, so
        # the question is whether the shipped matcher can bind it — which is the
        # question "does this capture mint a duplicate".
        var = {k: {"pairs": 0, "in_window": 0, "attached": 0,
                   "attached_folded": 0, "in_window_not_attached": 0}
               for k in KS}
        by_class: dict[str, dict[str, dict[str, int]]] = collections.defaultdict(
            lambda: {str(k): {"pairs": 0, "in_window": 0, "attached": 0,
                              "attached_folded": 0} for k in KS})
        for row in sample:
            name = row["name"]
            for vclass, surface in variants(name, row["cls"]):
                folded_surface = _fold_punct(surface)
                for k in KS:
                    var[k]["pairs"] += 1
                    by_class[vclass][str(k)]["pairs"] += 1
                    rows = window(surface, k)
                    in_win = any(r["name"] == name for r in rows)
                    if in_win:
                        var[k]["in_window"] += 1
                        by_class[vclass][str(k)]["in_window"] += 1
                    # the MENTION is the surface form, exactly as a capture's
                    # derived query would carry it — never the stored name
                    cand, _m = _find_existing_entity(rows, surface, "core:other")
                    hit_ok = cand is not None and cand.get("name") == name
                    if hit_ok:
                        var[k]["attached"] += 1
                        by_class[vclass][str(k)]["attached"] += 1
                    elif in_win:
                        var[k]["in_window_not_attached"] += 1
                    folded = [dict(r, name=_fold_punct(r["name"])) for r in rows]
                    cand2, _m2 = _find_existing_entity(folded, folded_surface,
                                                       "core:other")
                    if cand2 is not None and cand2.get("name") == _fold_punct(name):
                        var[k]["attached_folded"] += 1
                        by_class[vclass][str(k)]["attached_folded"] += 1
        arms["variant"] = var
        arms["variant_by_class"] = {c: dict(v) for c, v in sorted(by_class.items())}

        # ── ARM 3: what a capture actually hands ``resolve_entities``.
        # ``search_graph`` accumulates every query's rows into ONE id-keyed dict,
        # so the effective candidate set is the UNION over the capture's derived
        # queries — not ``limit``. Measured here at ``max_queries`` names per
        # capture, which is the production budget (``max_queries=15``).
        pooled = {k: {"captures": 0, "set_sum": 0, "set_min": 10**9, "set_max": 0}
                  for k in KS}
        budget = 15   # production ``max_queries``
        for start in range(0, min(len(sample), args.captures * budget), budget):
            group = sample[start:start + budget]
            if len(group) < 2:
                continue
            for k in KS:
                merged: dict[str, dict] = {}
                for row in group:
                    for r in window(row["name"], k):
                        if r["id"] and r["id"] not in merged:
                            merged[r["id"]] = r
                size = len(merged)
                pooled[k]["captures"] += 1
                pooled[k]["set_sum"] += size
                pooled[k]["set_min"] = min(pooled[k]["set_min"], size)
                pooled[k]["set_max"] = max(pooled[k]["set_max"], size)
        arms["pooled"] = pooled

        report = {
            "issue": 11901,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "corpus": {"dir": str(sessions), "files": len(list(sessions.glob("*.jsonl"))),
                       "harvested_distinct": counts, "population": len(pop),
                       "sample": len(sample), "elapsed_s": round(time.time() - t0, 1)},
            "ks": list(KS),
            "retrieval": {
                "has_embedder": _has_embedder(),
                "legs_seen": dict(sorted(legs_seen.items())),
                # ⛔ The note is derived from the LEGS, never from embedder
                # availability: a model that loads but whose leg never answers
                # (`ran=False, reason="encode_failed"`, or a breaker-open empty
                # trace) would otherwise be labelled "hybrid" — the exact false
                # label `retrieval_preflight` exists to prevent, in the first
                # field a reader checks.
                "note": _mode_note(_has_embedder(), legs_seen),
            },
            "pooled_budget_note": ("a pooled group is `max_queries` (15) names, "
                                   "which is the production BUDGET CAP, not the "
                                   "typical capture — a capture with 5 entities "
                                   "issues 5 object queries"),
            "arms": arms,
        }
        payload = json.dumps(report, indent=1, sort_keys=True)
        if args.out:
            Path(args.out).write_text(payload + "\n")
        print(payload)
    finally:
        try:
            sdk.close()
        except Exception:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    p.add_argument("--population", type=int, default=1000)
    p.add_argument("--sample", type=int, default=300)
    p.add_argument("--captures", type=int, default=20,
                   help="pooled-set groups to emulate (a capture = max_queries=15 names)")
    p.add_argument("--namespace", default="recall11901")
    p.add_argument("--graph", default=None)
    p.add_argument("--out", default=None)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
