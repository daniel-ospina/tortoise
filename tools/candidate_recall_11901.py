#!/usr/bin/env python
"""Candidate recall@k for the capture-time link-before-create prior (#11901).

HOW TO RUN THE TESTS THAT GUARD THIS DOCUMENT
---------------------------------------------
``tests/test_candidate_recall_11901.py`` needs no graph, but every ``pytest`` run
in this repo needs the lane's DB seam (epic #1647 P4), so a bare
``uv run pytest`` reports one ERROR per collected test from
``tests/_embedded.py`` — that is the repo's gate, not these tests, and the count
is whatever this file currently collects, so it is not restated here. Use either
form:
::

    uv run python tools/test_lane.py run -- tests/test_candidate_recall_11901.py -q
    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_candidate_recall_11901.py -q

WHAT THIS MEASURES, AND WHY THE ARMS ARE MEASURED SEPARATELY
-----------------------------------------------------------
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
by an argument. The first three arms exist because the failures have different
fixes:

  ARM 1  RETRIEVAL     a mention that IS the stored name — how often is the node
                       in the window at each k? (widening can help)
  ARM 2  MATCHING      a mention that is a real surface VARIANT of the stored
                       name — how often does phase 1 bind it? This arm ALSO
                       measures `attached_folded`, what a candidate punctuation
                       fold would recover, and that fix is NOT shipped here: the
                       point of #11901 is to decide on evidence, not to patch.
  ARM 3  THE UNION     how large is the candidate set a capture actually hands
                       `resolve_entities`? (not `limit` — see below)

  ARM 4  THE GATE      `#12294`'s name-entropy gate: for each name, would the
                       ported predicate refuse its query, and — the number that
                       decides the lever — was its CANONICAL in the window the
                       gate would remove? Two predicates (`zep_full_name`, the
                       verbatim port, and `shipped`, the same rule applied to
                       the name's STOPWORD-REMOVED content tokens — the set
                       that would carry the query if the leg used it, see
                       `_signal_tokens`), plus
                       `gate_variant` (what it removes from the variant pairs)
                       and `pooled_gate` (whether the OTHER names' queries in a
                       real capture union rescue a gated name). Measured and
                       DECLINED — the full record is beside the predicate,
                       further down this file, and in
                       `tests/test_name_entropy_12294.py`.
  ARM 5  THE RERANK    `#12297`'s cross-encoder, over the SAME fused
                       candidates: recall@3 and nDCG@10 before vs after a
                       permuting rerank at each `--rerank-pool` width, paired
                       on one pool so `gained`/`lost` are counterfactual.
                       OPT-IN (`--rerank`) and DEFAULT OFF, so every artifact
                       committed before it is byte-identical. Its ceiling is
                       `in_window(P)`, never `miss_past_k` — see
                       `_rerank_arm`'s own note.

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

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE, before the rest of the
# imports, so a stale local `python3` fails LOUDLY with a remedy instead of with whichever 3.12-only
# construct happens to trip first. `tests/test_entry_point_python_guard.py` asserts structurally that
# this guard precedes every other import and behaviourally that it names the floor, the interpreter it
# got, and the invocation that works.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(
        f"tools/candidate_recall_11901.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/candidate_recall_11901.py`",
        file=sys.stderr,
    )
    raise SystemExit(70)

import argparse
import collections
import contextlib
import difflib
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The window widths #11901 names: today's 3, and the 5/10/15 the issue asks for
# (15 is Zep/Graphiti's candidate count — research-brief §6.2).
KS: tuple[int, ...] = (3, 5, 10, 15)

#: The windows the GATE arms account at (#12294). Two, not four: k=3 is what the
#: capture prior ships and k=15 is the widest window #11901 measured, so the pair
#: bounds the answer.
#:
#: ⛔ DERIVED from ``KS``, and asserted non-empty, because the three readers do
#: NOT read one thing: the arm's schema drops members outside ``KS``
#: (``if k in KS``), and the accounting loop iterates ``KS`` and gates on
#: ``if k in _GATE_KS`` — so a member of this set that is not in ``KS`` is
#: silently ignored by the loop AND dropped by the schema. If that intersection
#: were empty, ``_g_hit[p]`` would be ``{}`` for every gated name,
#: ``not any({})`` is ``True``, and every gated name would book as
#: ``gated_and_missed`` with ``suppressed_correct_by_k == {}`` — inverting the
#: headline from "the gate removes correct candidates" to "the gate is exactly
#: what we need". Deriving (rather than merely hoisting) makes that
#: unrepresentable; the assert catches an edit that empties it.
_GATE_KS: tuple[int, ...] = tuple(k for k in (3, 15) if k in KS)
assert _GATE_KS, f"the gate arms must measure at least one window in KS={KS}"

#: ``the <1-4 tokens>`` — the definite-description class. Bounded to 4 tokens:
#: longer spans are clauses, not names, and the audit found real names are short.
_DD = re.compile(r"\bthe\s+([a-z][a-z0-9]*(?:[ _-][a-z0-9]+){0,3})")
_ISSUE_REF = re.compile(r"#\d{3,5}\b")
_PATH = re.compile(r"\b(?:[\w.-]+/){1,3}[\w.-]+\.(?:py|md|yml|yaml|json|sh|ts|sql)\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){1,3}\b")

#: Stems that make a definite description a *clause fragment* rather than a
#: name. Excluded so the population is not dominated by "the same", "the two",
#: "the next" — those are function words the extractor discards, not entities.
_STOP_FIRST = frozenset({
    "same", "two", "next", "other", "first", "second", "last", "whole", "only",
    "few", "many", "more", "most", "such", "one", "ones", "thing", "things", "way",
    "ways", "one's", "rest", "above", "below", "following",
})
#: A definite description ending in one of these is a sentence fragment.
_STOP_LAST = frozenset({
    "be", "been", "being", "is", "are", "was", "were", "do", "does", "did", "done",
    "have", "has", "had", "will", "would", "can", "could", "should", "must", "may",
    "might", "not", "no", "yes", "and", "or", "but", "if", "then", "than", "that",
    "this", "these", "those", "it", "its", "they", "them", "their", "we", "our",
    "you", "your", "he", "she", "his", "her",
})


def harvest(sessions_dir: Path, stats: dict | None = None) -> dict[str, collections.Counter]:
    """Real capture text -> candidate entity names, by the three declared rules.

    Returns ``{class: Counter(name -> record-frequency)}``. Frequency counts the
    number of **session records** a name appears in — NOT its turns and NOT its
    raw occurrences. Recurrence across sessions is what makes a mention a
    candidate for re-identification, and a session that repeats a phrase 40
    times in one turn is one mention. (Stated this narrowly because the first
    draft called it "turn-frequency" while the ``seen`` set was created per
    record, which is a different population — review round 2, F5.)

    ``stats``, when given, is filled with this call's OWN input accounting
    (``lines``, ``parse_errors``, ``records_without_text``). Both ``continue``s
    below drop a record SILENTLY, and without a counter an artifact cannot
    distinguish "the corpus lost names" from "the harness stopped parsing
    records" — which is exactly the distinction a re-run has to make, so the
    instrument now reports how much of its own input it discarded.
    """
    if stats is not None:
        stats.update(lines=0, parse_errors=0, records_without_text=0)
    out: dict[str, collections.Counter] = {c: collections.Counter()
                                           for c in ("definite", "ident", "path")}
    files = sorted(sessions_dir.glob("*.jsonl"))
    if not files:
        raise SystemExit(f"no session events under {sessions_dir}")
    if stats is not None:
        #: The count of files this harvest ACTUALLY read. The report used to
        #: glob the live directory a second time, ~390 s later, so the number it
        #: published described an input the harvest may never have seen — on a
        #: directory the brief itself calls live and non-monotone.
        stats["files"] = len(files)
    for f in files:
        for line in f.open(errors="replace"):
            if stats is not None:
                stats["lines"] += 1
            try:
                rec = json.loads(line)
            except Exception:
                if stats is not None:
                    stats["parse_errors"] += 1
                continue
            parts = []
            for turn in rec.get("conversation") or []:
                c = turn.get("content")
                if isinstance(c, str):
                    parts.append(c)
            if not parts:
                if stats is not None:
                    stats["records_without_text"] += 1
                continue
            blob = "\n".join(parts)
            seen: set[tuple[str, str]] = set()

            # `seen` is re-created per record, and this closure is defined
            # INSIDE the loop — so it is passed in rather than captured:
            # a captured loop variable is ruff B023, and it is also the shape
            # where a later refactor (hoisting the def) silently lets every
            # record share the first record's dedupe set.
            def _add(cls: str, name: str, seen: set[tuple[str, str]]) -> None:
                name = name.strip()
                if not name or (cls, name) in seen:
                    return
                seen.add((cls, name))
                out[cls][name] += 1

            for m in _DD.finditer(blob):
                # ⛔ Collapse whitespace BEFORE the filters: `_DD`'s ``\s`` matches
                # a newline, so a line-wrapped phrase was harvested verbatim and
                # became a node whose NAME carries ``\n`` (`the\nverdict`,
                # `the\n   check-runs are stale`) — 4 of 1000 population names,
                # one of them colliding under a fold with a real name, and three
                # of them clause fragments rather than names. A name is a single
                # line; `_norm` would fold the whitespace for a COMPARISON but
                # the stored name keeps it.
                phrase = " ".join(m.group(0).split())
                toks = re.split(r"[\s_-]+", phrase)
                if toks[1] in _STOP_FIRST or toks[-1] in _STOP_LAST:
                    continue
                if len(toks[1]) < 2:
                    continue
                _add("definite", phrase, seen)
            for m in _ISSUE_REF.finditer(blob):
                _add("ident", m.group(0), seen)
            for m in _SNAKE.finditer(blob):
                _add("ident", m.group(0), seen)
            for m in _PATH.finditer(blob):
                _add("path", m.group(0), seen)
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
    """The ARTIFACT-level mode note, from ``legs_seen`` (the legacy rule).

    ⛔ This is the #3005 *submitted* predicate (``ran``, regardless of
    ``degraded``), OR-ed over every window — which is why :func:`run` now also
    classifies each window with ``search_engine.require_hybrid_read`` (the
    strict #2952 predicate, whose docstring says a lane that RECORDS A SCORE
    must delegate to it) and REFUSES to write an artifact whose windows are not
    uniformly one mode. The committed artifacts predate that guard; their note
    is this function's answer, and the brief declares the residual.
    """
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


#: #12297: the cut the reranked pool is reduced to. 3 is the production
#: per-query window ``_fts_rows`` passes, so "recall@3 after the rerank" is the
#: SAME quantity as arm 1's ``in_window`` at k=3 and the two are comparable.
TOP_K_RERANK_CUT = 3
#: #12297: the rank at which the rerank arm reports nDCG. 10 is the issue's
#: named metric; with one gold per mention nDCG@10 = 1/log2(rank+2)
#: for the 0-based ``rank`` this code carries (rank 0 is the top hit).
NDCG_K = 10
#: #12297: the pool widths the rerank arm fetches at. 15 is Zep's candidate
#: count — the width #11901 measured — and 40 is the mean effective
#: candidate-list length #11901 measured for a production capture at the
#: `max_queries=15` budget (research-brief §1, consequence 1). Both are
#: MEASURED; neither is adopted.
RERANK_POOLS: tuple[int, ...] = (15, 40)


def build_rerank_arm(args, sample, window, variants) -> dict:
    """The ``--rerank`` arm, built from the model the RESOLVER names.

    A seam rather than an inline call so the wiring is pinnable without a graph:
    a test that drives ``resolve_rerank_model`` alone stays green over a
    hard-coded model id at this call site (measured — that mutation passed all 43
    tests, which is exactly the regression this arm's provenance depends on).
    """
    return _rerank_arm(
        sample, window, variants,
        model=resolve_rerank_model(args),
        pools=tuple(args.rerank_pool or RERANK_POOLS),
        top_k=TOP_K_RERANK_CUT, ndcg_k=NDCG_K,
        threads=args.rerank_threads)


def resolve_rerank_model(args) -> str:
    """The cross-encoder this arm measures — resolved the way the PRODUCT does.

    ⛔ ``fused_rerank_model()``, not the constant: the shipped read passes no
    model to ``rerank_pool``, which resolves through ``load_scorer(model or
    fused_rerank_model())``, so ``TORTOISE_FUSED_RERANK_MODEL`` decides which
    cross-encoder the product actually loads. Hard-coding
    ``RERANK_MODEL_DEFAULT`` measured — and RECORDED, in the arm's own ``model``
    field — a model the product would not load whenever that knob was set; the
    sibling ``tools/rerank_calibration_12297.py`` resolved the same defect.

    A function rather than an inline expression so the pin on it is BEHAVIOURAL:
    a source-text check is satisfied by this docstring's own prose (measured —
    the first version of that test stayed green over a hard-coded model id).
    """
    if args.rerank_model:
        return args.rerank_model
    from tortoise.rerank import fused_rerank_model
    return fused_rerank_model()
#: #12297: torch threads for the rerank arm. PINNED to 1 and recorded in the
#: artifact: the unpinned default (one thread per core, 16 here) thrashes under
#: fleet load and measured ~20x slower per pair on identical input, so an
#: unpinned latency figure would describe the scheduler rather than the model.
RERANK_THREADS = 1


# ── #12297 residual 1 — the LEXICAL BASELINE CONTROL ─────────────────────
#
# The PR's own stated residual, and the decisive missing experiment: **does a
# 5 microsecond string compare already do what the cross-encoder is being paid
# 207 ms/mention for?** The noun-path arm asks "does `the credential` match
# `the credential`", which is token overlap over 2-4 word strings — so if a
# lexical scorer reproduces the arm's numbers, the lever's justification is
# gone and its cost is pure overhead.
#
# ⛔ This is a CONTROL, never a candidate matcher. It is not shipped, not wired
# into `rerank_pool`, and reaches no product path: it exists only to be measured
# against the cross-encoder over the SAME pools. Turning a string score into an
# attach decision is the refused "second similarity constant" of #11901 §7 and
# is deliberately out of scope here.
LEXICAL_MODES: tuple[str, ...] = ("difflib", "overlap")


class LexicalScorer:
    """A deterministic string-compare scorer with the cross-encoder's interface.

    ``score(query, contents) -> list[float]`` mirrors
    ``rerank.CrossEncoderScorer.score`` exactly, so the arm can swap it in and
    change NOTHING else — the same pool, the same cut, the same tie-break, the
    same paired before/after.

    Two modes, both declared rather than tuned:

    * ``difflib`` — ``SequenceMatcher(None, a, b).ratio()`` on lowercased text.
      Character-level, so it rewards the surface variants the noun path's
      character-only perturbations produce.
    * ``overlap`` — Jaccard over lowercased alphanumeric tokens. Token-level,
      so it is the strongest fair form of "a 5 us string compare". (An exact
      match needs no short-circuit: equal NON-EMPTY token sets make the Jaccard
      ratio 1.0 on its own, and a branch that returned early would have been a
      second code path to time. ⛔ Two EMPTY token sets are 0.0 — returned by the
      ``if not ta or not tb`` guard in ``_one``, not by the ratio — so the
      shortcut is safe to omit either way.)
    """

    def __init__(self, mode: str = "difflib") -> None:
        if mode not in LEXICAL_MODES:
            raise ValueError(f"unknown lexical mode {mode!r}")
        self.mode = mode

    @staticmethod
    def _tokens(text: str) -> frozenset[str]:
        return frozenset(re.findall(r"[a-z0-9]+", (text or "").lower()))

    def _one(self, a: str, b: str) -> float:
        a_l, b_l = (a or "").lower().strip(), (b or "").lower().strip()
        if not a_l or not b_l:
            return 0.0
        if self.mode == "difflib":
            return difflib.SequenceMatcher(None, a_l, b_l).ratio()
        ta, tb = self._tokens(a_l), self._tokens(b_l)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    def score(self, query: str, contents) -> list[float]:
        return [self._one(query, c) for c in contents]


def _ndcg_at(rank: int | None, k: int = NDCG_K) -> float:
    """nDCG@k for ONE gold per query (IDCG = 1, so DCG is the whole value).

    ``rank`` is 0-based; ``None`` means the gold never entered the pool, which
    scores 0 rather than being dropped from the denominator — dropping it would
    let a reranker raise nDCG by *losing* queries.
    """
    if rank is None or rank >= k:
        return 0.0
    return 1.0 / math.log2(rank + 2)


def _rerank_arm(
    sample: list[dict],
    window,
    variants,
    *,
    model: str,
    pools: tuple[int, ...],
    top_k: int = TOP_K_RERANK_CUT,
    ndcg_k: int = NDCG_K,
    threads: int = RERANK_THREADS,
    scorer=None,
    label: str = "rerank",
) -> dict:
    """#12297 arm 5 — the cross-encoder over the SAME fused candidates.

    For each mention we take the real fused read at pool width ``P`` (wider than
    production's 3) and hand it to the SHIPPED ``rerank.rerank_pool`` — one
    ``score(query, contents)`` call per mention, scoring only the slots whose text
    is non-empty and writing those back into their OWN slots, exactly as the
    product does (a textless row is never scored and never moves). The harness
    then scores that order at ``top_k`` — the cut is the measurement's, not this
    function's.

    ⛔ THREE things this arm deliberately does NOT do, each because doing it
    would make the number unreadable:

    1. **It never widens the production default.** ``pools`` are measured, not
       adopted. #11901 measured the window lever and declined it standalone
       ("do not widen `limit` on its own", §7-1); this arm measures the PAIRED
       configuration the issue asks about, and the shipped default stays 3.
    2. **It does not use MMR.** ``mmr_select`` exists for a context pool where
       near-duplicate passages should not fill the slots. A link-before-create
       prior wants the single best match, so MMR would be a different (and
       worse-conditioned) question — it is exercised by the ask lane and by
       ``tests/test_longmem_rerank.py``, not here.
    3. **It does not use the score as a MATCHER.** Turning the cross-encoder
       score into an attach decision needs a threshold, and #11901 records a
       SECOND similarity constant as a REFUSED row beside
       ``embeddings.DEFAULT_THRESHOLD``. This arm only reorders, so the phase-1
       attach rate is untouched BY CONSTRUCTION — the matcher is never called on
       the reranked order, and this artifact therefore makes **no** claim about
       the attach rate moving (an earlier draft said it computed one; it does
       not, and it no longer says so).
    """
    from tortoise.rerank import get_scorer, rerank_pool

    # ⛔ PIN THE THREADS BEFORE THE FIRST SCORE, and record the pin.
    # Measured on this box (`tools/rerank_calibration_12297.py` docstring): the
    # default (one thread per core, 16) thrashes under fleet load at ~100 ms/pair
    # where one thread reports ~5.3 ms/pair — a 20x difference on identical
    # input. A latency figure taken under the default would describe the
    # scheduler, not the model, and would be quoted as if it described the model.
    if threads and threads > 0:
        import torch  # local: arm 5 is this tool's only torch consumer
        torch.set_num_threads(threads)

    if scorer is None:
        scorer, reason = get_scorer(model)
        if scorer is None:
            return {"applied": False, "degrade_reason": reason, "model": model}

    out: dict = {
        "applied": True,
        "arm": label,
        "model": model,
        "top_k": top_k,
        "ndcg_k": ndcg_k,
        "torch_threads": threads,
        # ⛔ NO MEASURED FIGURE IN THIS STRING. It used to carry one run's
        # numbers ("277/300 at a 15-window, 300/300 at 40") as a literal, so a
        # re-run on a grown corpus (this file already records `population_digest`
        # moving between runs) would have shipped a note contradicting its OWN
        # `arms.<label>.<P>.exact`, and a hand-edit of either artifact's copy was
        # invisible (measured: rewriting 277 -> 299 in the noun-path artifact left
        # every test green). The figures now live only where they are COMPUTED —
        # `arms.<label>.<P>.exact.recall_3_after` / `mentions` — and the note says
        # which cell to read AND that it is the ACHIEVED count, not the ceiling
        # (`recall_3_before` minus `lost` plus `gained` is what separates them; the
        # note called the two identical, then called `lost` the discriminator, which
        # is wrong in ONE direction — `lost` 0 is NECESSARY but not sufficient: the
        # witness is a rerank with `lost` 0 and `gained` 0 that still leaves an
        # in-pool gold outside the top 3).
        # ⛔ LABEL-DERIVED, and it has to be: this same note is emitted onto the
        # lexical carriers too (`_rerank_arm(..., label=...)`), where "this arm" is
        # `lexical_difflib` / `lexical_overlap`. A hard-coded `arms.rerank` pointer
        # read the WRONG ARM there — the same defect the ceiling half of this note
        # was rewritten to remove, reintroduced by a constant string.
        "ceiling_note": (  # noqa: UP032 — .format keeps the literal segments the test parses
            "a reranker only reorders rows the legs already returned, so its "
            "CEILING is in_window(P) — the number of golds the legs put INSIDE the "
            "scored window at that width — while the figure this arm records as its "
            "HEADLINE is the ACHIEVED one: "
            "`arms.{label}.<P>.exact.recall_3_after` over its `mentions` "
            "denominator. ⛔ The two are not the same quantity: the achieved figure "
            "is the ceiling MINUS the in-pool golds the arm still leaves outside "
            "the top 3, i.e. `recall_3_before` minus `lost` plus `gained`, so read "
            "those three fields in the same cell rather than treating the achieved "
            "count as the ceiling — `lost` alone does not decide it (it can be 0 "
            "while the achieved figure sits well below the ceiling). WIDTH "
            "DEPENDENT throughout, and not quoted here. "
            "⛔ `arms.exact` is a DIFFERENT, width-by-width table: it holds arm 1's "
            "own in-window count for each of arm 1's widths and nothing else, so a "
            "pool width outside that set has no cell there "
            "at all. Read it only where its key matches the width you are asking "
            "about; for the exact population it is the only figure this artifact "
            "carries for this arm, and the VARIANT pairs have their own at-pool "
            "window in `variant.in_window_3_before`. miss_past_k is 0 BY "
            "CONSTRUCTION only "
            "at arm 1's WIDEST window (a response has at most that many rows, so "
            "'the rank is past it' cannot occur) and can only be non-zero at a "
            "narrower k, where a gold the legs returned further down is genuinely "
            "past the cut — so it is never a width-independent ceiling at any "
            "width; miss_absent_at_max_k is "
            "not a width-independent bound either — it counts golds absent within "
            "arm 1's MAXIMUM window").format(label=label),
    }
    # ⛔ THE ORDER IS PRODUCED BY THE SHIPPED FUNCTION, NOT BY A COPY OF IT.
    # This arm used to sort the scores itself and reorder EVERY row, including a
    # textless one — and an empty passage outranks an unrelated real one, so the
    # arm credited the reranker with a promotion `rerank_pool` structurally
    # refuses (measured: a textless gold moved to rank 3 in the arm where the
    # product leaves it in its slot), and counted a pair the product never
    # scores. Both arms below therefore call ``rerank_pool`` with the ``scorer``
    # seam, and the timing proxy wraps the scorer so ``ms_per_pair`` still
    # describes ONE ``score()`` call per mention.

    class _Timed:
        def __init__(self, inner) -> None:
            self.inner = inner
            self.seconds = 0.0
            self.pairs = 0

        def score(self, query, texts):
            t0 = time.perf_counter()
            out = self.inner.score(query, texts)
            self.seconds += time.perf_counter() - t0
            self.pairs += len(texts)
            return out

    timed = _Timed(scorer)

    def _shipped_order(query: str, rows: list[dict], width: int) -> list[str] | None:
        """Ids in the SHIPPED post-rerank order, or None when it degraded."""
        cands = [{"id": r["id"], "content": r.get("name") or ""} for r in rows]
        # ⛔ THE COUNTER IS CUMULATIVE ACROSS THE RUN, ``scored`` IS THIS CALL.
        # Comparing them totals-to-call passed on the FIRST query and raised on
        # every one after it (measured: "the timed scorer saw 4 pairs but
        # rerank_pool reported 2" on the second) — an instrument that crashed on
        # any real corpus. Delta, not total.
        pairs_before = timed.pairs
        out_rows, stats = rerank_pool(
            query, cands, text_key="content", pool=width,
            enabled=True, model=model, scorer=timed)
        if not stats.get("applied"):
            return None
        # The proxy's count and the product's own disclosure must agree; a
        # divergence means this instrument is measuring a different call.
        if timed.pairs - pairs_before != stats.get("scored"):
            raise AssertionError(
                f"the timed scorer saw {timed.pairs - pairs_before} pairs but "
                f"rerank_pool reported {stats.get('scored')}")
        return [r["id"] for r in out_rows]

    for pool in pools:
        key = str(pool)
        row_exact = {"mentions": 0, "pairs_scored": 0, "rerank_degraded": 0}
        row_variant = {"pairs": 0, "rerank_degraded": 0}
        for name_k in ("before", "after"):
            row_exact[f"recall_{top_k}_{name_k}"] = 0
            row_exact[f"ndcg{ndcg_k}_{name_k}"] = 0.0
        row_exact["gained"] = 0
        row_exact["lost"] = 0
        for name_k in ("before", "after"):
            row_variant[f"in_window_{top_k}_{name_k}"] = 0
        row_variant["gained"] = 0
        row_variant["lost"] = 0

        for row in sample:
            rows = window(row["name"], pool)
            if not rows:
                row_exact["mentions"] += 1
                continue
            before_pairs = timed.pairs
            after_ids = _shipped_order(row["name"], rows, pool)
            if after_ids is None:
                row_exact["rerank_degraded"] += 1
                continue

            row_exact["mentions"] += 1
            row_exact["pairs_scored"] += timed.pairs - before_pairs
            gold = row["id"]
            rank_before = next(
                (i for i, r in enumerate(rows) if r["id"] == gold), None)
            rank_after = (after_ids.index(gold) if gold in after_ids else None)
            for name_k, rank in (("before", rank_before), ("after", rank_after)):
                if rank is not None and rank < top_k:
                    row_exact[f"recall_{top_k}_{name_k}"] += 1
                row_exact[f"ndcg{ndcg_k}_{name_k}"] += _ndcg_at(rank, ndcg_k)
            in3_before = rank_before is not None and rank_before < top_k
            in3_after = rank_after is not None and rank_after < top_k
            row_exact["gained"] += int(in3_after and not in3_before)
            row_exact["lost"] += int(in3_before and not in3_after)

            # The VARIANT arm on the same reranked order: does the rerank put a
            # surface variant's canonical inside the top-k the verdict step
            # reads? Measured, not asserted — the rerank does not make phase 1
            # able to bind it (that is the fold's job, #11901 §7-2b).
            for _vclass, surface in variants(row["name"], row["cls"]):
                vrows = window(surface, pool)
                if not vrows:
                    row_variant["pairs"] += 1
                    continue
                vafter = _shipped_order(surface, vrows, pool)
                if vafter is None:
                    row_variant["rerank_degraded"] += 1
                    continue
                row_variant["pairs"] += 1
                vb = next((i for i, r in enumerate(vrows)
                           if r["id"] == gold), None)
                va = vafter.index(gold) if gold in vafter else None
                for name_k, rank in (("before", vb), ("after", va)):
                    if rank is not None and rank < top_k:
                        row_variant[f"in_window_{top_k}_{name_k}"] += 1
                inb = vb is not None and vb < top_k
                ina = va is not None and va < top_k
                row_variant["gained"] += int(ina and not inb)
                row_variant["lost"] += int(inb and not ina)

        n = row_exact["mentions"] or 1
        row_exact["recall_pct_before"] = round(
            100.0 * row_exact[f"recall_{top_k}_before"] / n, 1)
        row_exact["recall_pct_after"] = round(
            100.0 * row_exact[f"recall_{top_k}_after"] / n, 1)
        row_exact["ndcg_mean_before"] = round(
            row_exact[f"ndcg{ndcg_k}_before"] / n, 4)
        row_exact["ndcg_mean_after"] = round(
            row_exact[f"ndcg{ndcg_k}_after"] / n, 4)
        vn = row_variant["pairs"] or 1
        row_variant["in_window_pct_before"] = round(
            100.0 * row_variant[f"in_window_{top_k}_before"] / vn, 1)
        row_variant["in_window_pct_after"] = round(
            100.0 * row_variant[f"in_window_{top_k}_after"] / vn, 1)
        out[key] = {"exact": row_exact, "variant": row_variant}

    pairs_scored = timed.pairs
    score_s = timed.seconds
    out["latency"] = {
        "pairs_scored": pairs_scored,
        "score_s": round(score_s, 3),
        "ms_per_pair": (round(1000.0 * score_s / pairs_scored, 3)
                        if pairs_scored else None),
        "device": "cpu (this box has no GPU)",
        "torch_threads": threads,
        # ⛔ The note must describe the scorer that ACTUALLY ran. A lexical arm
        # has no lock and no torch sigmoid, and carrying the cross-encoder's
        # note onto it would label a 5 us string compare with the model's cost
        # story — the false-label class this repo refuses.
        "note": (
            "per-pair wall clock around ONE score() call per mention, "
            "excluding model load. "
            + ("This arm is a LEXICAL control: no torch, no lock, no sigmoid."
               if model.startswith("lexical:") else
               "Includes the scorer's lock and the torch sigmoid.")),
    }
    if not model.startswith("lexical:"):
        # ⛔ THE NOTE MUST MATCH WHAT HAPPENED TO TORCH. `--rerank-threads 0`
        # leaves the default pool in place, and the artifact said "PINNED to 0" —
        # a pin the run never made, on the one field a reader uses to decide
        # whether the figure describes the scheduler or the model.
        if threads > 0:
            out["latency"]["note"] += (
                f" torch threads are PINNED to {threads} and recorded here, "
                "because the unpinned default (one thread per core) measured "
                "~20x slower on this box under fleet load — an unpinned figure "
                "describes the scheduler, not the model")
        else:
            out["latency"]["note"] += (
                " torch threads are NOT pinned (torch's default pool, one thread "
                "per core): this figure describes the scheduler as much as the "
                "model")
    return out

# ── the NAME-ENTROPY GATE (#12294) — MEASURED HERE, DECLINED ────────────────
#
# Zep/Graphiti refuses its *fuzzy* (MinHash/LSH shingle) matcher for names too
# short or too repetitive to shingle reliably, and DEFERS those names to the LLM
# dedupe prompt — it never drops them. Read from
# `graphiti_core/utils/maintenance/dedup_helpers.py`:
#
#   * `_NAME_ENTROPY_THRESHOLD = 1.5` / `_MIN_NAME_LENGTH = 6` /
#     `_MIN_TOKEN_COUNT = 2` — the three constants at `:31-33`;
#   * `_has_high_entropy` (`:80-86`) refuses when `len(name) < 6 AND
#     tokens < 2` (an **AND**, so a long single-token or a short two-token name
#     passes THAT test) and otherwise requires the Shannon entropy of the name's
#     CHARACTERS (`_name_entropy`, `:53-77`; spaces stripped, bits/char) to
#     reach `1.5`;
#   * the call site (`_resolve_with_similarity`, `:336-354`) runs EXACT
#     normalised-name matching for **every** name first and gates only the block
#     below it; a gated name goes to `unresolved_indices`, which
#     `_resolve_with_llm` (`node_operations.py:649-670`) feeds to the LLM.
#
# ⛔ THE GATE IS DECLINED, AND THIS MODULE HOLDS THE EVIDENCE. #12294 proposed
# porting it to `extractor_v2._derive_queries` (no query for a gated name) and
# `resolve_entities` (hold, never assert new). Built, measured here, and
# falsified by the issue's own falsifier on the #11901 corpus:
#
#   * ⛔ THE PREMISE IS FALSE, and this is the finding that outlives the lane.
#     The gate assumes a bare definite description's query degenerates to its
#     bare content word and returns a near-synonymous cluster. It does not:
#     for a name carrying NO identifier span, `sparse.build_or_query` builds the
#     OR-union only when TWO OR MORE tokens survive stopword removal, and a
#     single surviving token takes the RAW-LITERAL fallback (`sparse.py:590`); a
#     name WITH an identifier span is the exception (`ERR-4032` keeps the OR
#     path through its aliases even with one plain token, `sparse.py:570-588`),
#     and none of these names is one — so `the fix` is queried as `the fix`,
#     which RediSearch evaluates as an implicit AND over both terms
#     (`sparse.py:12`). The article is not a wasted term, it is half of the
#     conjunction.
#   * the SHIPPED-shaped predicate gates **86 names of 300**, and **86 of 86 are
#     reachable by the leg at k=15** (74 already at k=3) — so the gate removes a
#     CORRECT candidate for almost every name it touches;
#   * the class it was proposed for — names unreachable at ANY k (**81/300**,
#     `#11901`'s 27 %) — has **zero** overlap with the gated set: the predicate
#     is anti-correlated with the population it was meant to protect;
#   * with the real capture UNION (15 names, `max_queries`), the OTHER names'
#     queries rescue **1 of 86** gated names at k=3 and **6 of 86** at k=15, so
#     the recall cost is not an artefact of measuring each name in isolation;
#   * the exact Zep port — including the FUZZY normaliser it actually receives
#     (`_zep_normalize_for_fuzzy`, `dedup_helpers.py:46-50`) — is narrower and no
#     better: it gates **15 names, all `#NNNN` identifiers, and 0 of the
#     bare-definite-description class** (62.3 % of `:Object` names) the issue
#     targeted, because `the suite` is 9 characters and 2 tokens as written;
#   * and no duplicate is prevented: gated VARIANT pairs that phase 1 could bind
#     today = **0** at every k (`would_attach_before_any_k`; `#11901`: variant
#     attach is 0.0 % before and after), so the gate trades measured recall for
#     zero duplicate reduction.
#
# ⚠️ THE CORPUS IS LIVE. `--sessions-dir` defaults to `~/.tortoise/session-events`,
# which grows while a lane measures, so two runs a few minutes apart differ by one
# or two names (measured: 85 vs 86 gated). The committed artifact
# `docs/research/2026-11-12-11901-candidate-recall/measurement-entropy-gate-12294.json`
# is the RECORD; quote its numbers, and treat a re-run that lands within a name or
# two as a reproduction rather than a contradiction. What is invariant across runs
# is the finding, not the count: 0 gated names are unreachable at any k, ~100 % are
# reachable at k=15, and 0 gated variant pairs attach.
#
# The predicate is kept HERE rather than in `tortoise/`, because it is live
# measurement code and would be dead product code:
# `tests/test_name_entropy_12294.py` pins both the port and the falsification, so
# a future lane re-attempting lever M-1 reds instead of re-deriving it.

#: Zep's three published constants, re-declared so a test can pin the port.
_NAME_ENTROPY_THRESHOLD = 1.5
_MIN_NAME_LENGTH = 6
_MIN_TOKEN_COUNT = 2


def _name_char_entropy(normalized_name: str) -> float:
    """Shannon entropy over the name's CHARACTERS, spaces stripped, bits/char.

    Zep's `_name_entropy` (`dedup_helpers.py:53-77`). Repetition, not length, is
    what it measures: ``aaaaaaaa`` scores 0.0, ``deadline`` ~2.5 (two chars at
    1/4 and four at 1/8: 1.0 + 1.5).
    """
    cleaned = (normalized_name or "").replace(" ", "")
    if not cleaned:
        return 0.0
    counts: dict[str, int] = {}
    for char in cleaned:
        counts[char] = counts.get(char, 0) + 1
    total = len(cleaned)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _zep_normalize_for_fuzzy(name: str) -> str:
    """Zep's `_normalize_name_for_fuzzy` (`dedup_helpers.py:46-50`).

    ⛔ THE CALLER'S ARGUMENT, not a detail: Zep passes the FUZZY-normalised name
    (`re.sub(r"[^a-z0-9' ]", " ", lowercase)`, whitespace collapsed) into
    `_has_high_entropy`, so its token count is computed on PUNCTUATION-STRIPPED
    text. Passing the raw lowercased name instead (this arm's first cut) changes
    the token count for punctuated names (`a-fix` → 1 token raw, 2 tokens fuzzy)
    and would report a gate Zep does not ship. Re-implemented here because the
    only way to call theirs is to depend on graphiti.
    """
    normalized = re.sub(r"\s+", " ", (name or "").lower()).strip()
    normalized = re.sub(r"[^a-z0-9' ]", " ", normalized).strip()
    return re.sub(r"\s+", " ", normalized)


def _has_high_entropy(normalized_name: str) -> bool:
    """Zep's predicate, verbatim in behaviour (`dedup_helpers.py:80-86`).

    ⛔ The conjunction is deliberate and is the part a later "simplification"
    would break: `len < 6 AND tokens < 2` refuses only names that are BOTH short
    and single-token, so `fix` is refused, `hotfix` (6 chars, 1 token) is NOT,
    and `the fix` (7 chars, 2 tokens) is not. As an `or` it would refuse every
    single-token name — wider than the source ships, and in the direction that
    removes correct candidates.
    """
    token_count = len((normalized_name or "").split())
    if len(normalized_name or "") < _MIN_NAME_LENGTH and token_count < _MIN_TOKEN_COUNT:
        return False
    return _name_char_entropy(normalized_name) >= _NAME_ENTROPY_THRESHOLD


def _signal_tokens(name: str) -> list[str]:
    """The STOPWORD-REMOVED tokens of this name — not the leg's surviving set.

    `sparse.build_or_query` drops `SPARSE_STOPWORDS` before it builds the
    OR-union (`sparse.py:34-52`), so these are the tokens that would carry the
    query IF the leg used them.

    ⛔ It is the SPLIT rule that is shared with the leg (lowercase,
    non-alphanumeric runs), NOT the surviving-token set: `tokenize_sparse_query`
    additionally drops every token shorter than 2 characters (`:429-430`) and —
    at `build_or_query`'s default `keep_numeric=False`, which is what the
    capture path passes — every all-digit token (`:431-432`), then appends
    identifier aliases (`:441-447`). So `_signal_tokens("#2258") == ["2258"]`
    while the leg tokenises `2258` to `[]` and takes the escaped raw literal: the
    "token" this function returns does not exist as one. Nothing measured changes
    (the 15 ident names are gated by BOTH predicates — `gate_gated_names` records
    them as the `both` set — so no share moves), but this is not a fidelity
    guarantee and must not be quoted as one.
    """
    from tortoise.sparse import SPARSE_STOPWORDS

    return [t for t in re.split(r"[^a-z0-9]+", (name or "").lower())
            if t and t not in SPARSE_STOPWORDS]


def _name_carries_signal(name: str) -> bool:
    """Would a gate on this name keep a query that can still discriminate?

    Two ways a name carries no signal, and the second is the one Zep's
    full-name predicate cannot see through our tokenizer:

    1. **No content token at all** — every token is a `SPARSE_STOPWORDS` member
       (`the and` — article + connective), so `build_or_query` falls back to the
       escaped raw string. ⛔ Not `the same` / `the one`: `same` and `one` are NOT
       `SPARSE_STOPWORDS` members, so those are case-2 names
       (`_signal_tokens("the same") == ["same"]`), which is what
       `tests/test_name_entropy_12294.py` pins. This class is EMPTY on the
       measured corpus (`gate_gated_names` has no entry with `signal_tokens: []`).
    2. **Zep's predicate on the CONTENT tokens** — refuse a name whose only
       surviving token is one Zep's rule would refuse on its own. `the fix` →
       `fix`, three characters and one token, so it is refused by the same rule
       that refuses `fix` itself. ⚠️ Measured: this is the wrong inference for
       our leg, because the leg does NOT search that token alone — it searches
       `the fix` as a two-term implicit AND (see the header).

    ⛔ MEASURED AND DECLINED: see this block's header. The predicate is correct
    as a port and wrong as a policy here — it selects names the leg CAN find.
    """
    tokens = _signal_tokens(name)
    if not tokens:
        return False
    return _has_high_entropy(" ".join(tokens))


def _pooled_coverage(sample_size: int, captures: int, budget: int) -> dict:
    """The population a ``pooled``/``pooled_gate`` arm ACTUALLY measures.

    ⛔ THE CAP IS RECORDED, NOT IMPLIED (#11823's defect class: a cap that
    truncates without saying so). Both pooled arms are built by one loop that
    reaches at most ``captures * budget`` names and skips a trailing singleton
    (``len(group) < 2``), so without this the artifact leaves a reader to
    reconstruct the denominator from ``captures`` and the ``budget`` literal —
    and a ``--sample``/``--captures`` edit could narrow the population silently
    while the arm still reports a share.

    ``truncated`` is the field to read: True means the pooled numbers are about
    ``names_measured`` names, not ``names_in_sample``. At the committed
    artifact's shape (300 names, 20 captures, budget 15) it is False.

    ⛔ The COMMITTED artifact ``measurement-entropy-gate-12294.json`` predates
    this field and does not carry it — its denominator is reconstructible from
    what it does state (``corpus.sample`` 300 = ``pooled.captures`` 20 x the
    15-name budget recorded in ``pooled_budget_note``), and back-filling a
    generated record is worse than the gap: a hand-added field turns the run's
    own evidence into a hybrid a re-run diff reads as drift. The established
    precedent in this repo is `test_the_harness_reports_how_much_of_its_own_input_it_discarded`
    (committed artifacts predate ``corpus.input``; the INSTRUMENT is what is
    asserted). A fresh run emits the field.
    """
    reached = min(sample_size, captures * budget)
    singleton = 1 if reached % budget == 1 else 0
    return {
        "names_in_sample": sample_size,
        "names_reached": reached,
        "names_measured": reached - singleton,
        "skipped_singleton": singleton,
        "truncated": (reached < sample_size) or bool(singleton),
    }


def _allocate_shares(sample: int, pop: list[dict]) -> dict[str, int]:
    """Largest-remainder allocation of ``sample`` across the harvested classes.

    Rounding each class's share independently (``round(sample*n_c/N)``) does not
    guarantee the shares SUM to ``sample``, and the previous code then truncated
    with ``sample[:sample]`` — which drops from the END of a class-blocked list,
    i.e. always ``path``. Measured over sample sizes 1..600 on the committed
    600/300/100 mix, 124 of them produced a short sum or a ZERO share for a
    class (``--sample 6`` → [4, 2, 1] = 7 → the path class disappears).
    """
    total = len(pop) or 1
    counts = collections.Counter(r["cls"] for r in pop)
    base = {c: sample * counts[c] // total for c in counts}
    rem = sample - sum(base.values())
    order = sorted(counts, key=lambda c: (-(sample * counts[c] % total), c))
    for c in order[:max(0, rem)]:
        base[c] += 1
    return base


def _interleave(by_cls: dict[str, list[dict]], shares: dict[str, int]) -> list[dict]:
    """Round-robin the class lists so a slice of the sample is not one class.

    ``build_population`` emits class by class, so the pooled arm's groups
    (``sample[start:start+budget]``) were class-HOMOGENEOUS — 12 all-definite, 6
    all-ident, 2 all-path of 20 — while a real capture mixes classes. A
    same-class group shares tokens far more, so the union deduplicates harder and
    the mean is biased LOW (conservative for "~40 vs 15", but not "a capture").
    """
    queues = {c: list(by_cls[c][:shares[c]]) for c in sorted(by_cls)}
    out: list[dict] = []
    while any(queues.values()):
        for c in sorted(queues):
            if queues[c]:
                out.append(queues[c].pop(0))
    return out


def run(args: argparse.Namespace) -> int:
    from tortoise.rerank import rerank_pool_enabled

    #: ⛔ BEFORE THE STORE CHECK, deliberately. The gate shapes what "before" IS;
    #: with TORTOISE_FUSED_RERANK armed the read reranks the pool first, so every
    #: arm's "before" is a rerank and the cells report ``gained 0 / lost 0`` — a
    #: false zero that looks exactly like a real one, on the instrument that
    #: produced the artifacts this PR's §3, §5 and §7 cite. Its sibling
    #: (``tools/rerank_calibration_12297.py``) has refused this since the gate
    #: landed; this instrument did not, and that asymmetry is a hole in the
    #: evidence chain the PR rests on. The committed artifacts are provably
    #: unarmed (``gained 56 / lost 0``), so this closes a hazard for the NEXT run.
    _ambient_gate = rerank_pool_enabled()
    if _ambient_gate and not args.allow_reranked_baseline:
        print("REFUSED: TORTOISE_FUSED_RERANK is armed, so the read's own order "
              "is ALREADY reranked and \"before\" is not the fused baseline — "
              "this run would report a rerank-vs-rerank zero. Unset it "
              "(TORTOISE_FUSED_RERANK=) or pass --allow-reranked-baseline to "
              "measure an armed read on purpose.", file=sys.stderr)
        return 3

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
    _input_stats: dict = {}
    harvested = harvest(sessions, stats=_input_stats)
    pop = build_population(harvested, args.population)
    if not pop:
        print("REFUSED: the harvest produced no names", file=sys.stderr)
        return 3
    counts = {c: len(v) for c, v in harvested.items()}

    # ⛔ RUN-SCOPED GRAPH. `_upsert_object` MERGEs on the id and writes `o.name`
    # ON CREATE only (projection/entities.py:5031-5050, the recorded #8919 gap),
    # so a second run on the SAME graph whose harvest shifted a name into a
    # different population position leaves the node at that id carrying the
    # FIRST run's name — the query then matches nothing and books as
    # `miss_absent_at_max_k`. Measured on a lane container: upsert id X as
    # "alpha original", upsert X as "beta shifted", and "beta shifted" returns
    # ZERO rows while "alpha original" still returns X. Nothing below is allowed
    # to assume the names landed; it is CHECKED after the write (see the refusal)
    # and the graph name is recorded so a reader can tell two runs apart.
    graph_name = args.graph or f"recall11901_{int(time.time())}_{os.getpid()}"
    pop_digest = hashlib.sha256(
        "\n".join(r["name"] for r in pop).encode()).hexdigest()[:16]

    sdk = TortoiseSDK(namespace=args.namespace, graph_name=graph_name)
    try:
        proj = sdk._get_proj()
        # The population is written through the REAL projection: one Object per
        # name, ids minted here (the projection MERGEs on the id per #3590), and
        # the id is CARRIED on the population row so every comparison below is
        # by identity — a fold-equality on NAME can credit the wrong node (the
        # corpus yields fold-equal groups such as 'the verdict' / 'the merge
        # base' + 'the merge-base').
        for i, row in enumerate(pop):
            row["id"] = f"obj-11901{i:06d}"
            proj._upsert_object({"id": row["id"], "name": row["name"],
                                 "object_kind": "core:other"})
        # ⛔ REFUSE a run whose names did not land: with `o.name` unwritten on
        # ON MATCH, a reused graph silently measures the PREVIOUS population.
        stored = {r[0]: r[1] for r in proj.g.query(
            "MATCH (o:Object) WHERE o.id STARTS WITH 'obj-11901' "
            "RETURN o.id, o.name", params={}).result_set}
        stale = [row["name"] for row in pop
                 if stored.get(row["id"]) != row["name"]]
        if stale:
            print(f"REFUSED: {len(stale)} of {len(pop)} population names did not "
                  f"land (first: {stale[:3]!r}) — `o.name` is an ON CREATE write "
                  f"(#8919), so this graph already held a different population. "
                  f"Pass a fresh --graph.", file=sys.stderr)
            return 3
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
        #: Per-window verdict from `search_engine.require_hybrid_read` — the
        #: STRICT #2952 predicate (an IN-SCOPE vector entry RAN **and was not
        #: degraded**), whose docstring says a lane that records a score must
        #: delegate to it. `legs_seen` above is the looser #3005 "was submitted"
        #: predicate and is OR-ed over thousands of windows, so on its own it
        #: cannot distinguish a hybrid run from a MIXED one (`ran=True,
        #: degraded=True` on `no_embeddings`/below-floor, or `ran=False` on
        #: `breaker_open`). The pair is recorded and a mixed run is REFUSED.
        window_modes: dict[str, int] = {"hybrid": 0, "keyword_only": 0}
        from tortoise.exceptions import HybridReadUnavailableError
        from tortoise.search_engine import require_hybrid_read

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
            try:
                require_hybrid_read(trace)
            except HybridReadUnavailableError:
                window_modes["keyword_only"] += 1
            else:
                window_modes["hybrid"] += 1
            return [{"id": r.get("id", ""), "name": r.get("content", ""),
                     "kind": r.get("point_kind", "")} for r in (rows or [])]

        # STRATIFIED, not head-sliced: ``build_population`` emits class by class,
        # so ``pop[:n]`` would sample one class only and report its recall as the
        # population's. Each class contributes its own proportional share — via
        # LARGEST REMAINDER, so the shares sum to ``args.sample`` (independent
        # rounding plus a `[:sample]` truncation silently dropped the LAST class,
        # which is `path`, at 124 of the 600 sample sizes measured), and a sample
        # too small to give every class a member is refused rather than silently
        # dropping one.
        _by_cls: dict[str, list[dict]] = collections.defaultdict(list)
        for row in pop:
            _by_cls[row["cls"]].append(row)
        if args.sample > len(pop):
            print(f"REFUSED: --sample {args.sample} exceeds the population "
                  f"({len(pop)}) — `sample_by_class` would sum to "
                  f"{args.sample} while the sample held {len(pop)}", file=sys.stderr)
            return 2
        shares = _allocate_shares(args.sample, pop)
        if any(shares.get(c, 0) == 0 for c in _by_cls):
            print(f"REFUSED: --sample {args.sample} starves a class ({shares}) — "
                  f"a per-class rate needs at least one name per class",
                  file=sys.stderr)
            return 2
        # INTERLEAVED, so the pooled arm's groups are not single-class slices
        # (a real capture mixes classes; a same-class group deduplicates harder
        # and biases the union LOW).
        sample: list[dict] = _interleave(_by_cls, shares)
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
        # ── ARM 4 (this change, #12294): the ENTROPY GATE, measured on the SAME
        # names, windows and graph as ARM 1 — folded into this loop so the arm
        # costs ZERO extra queries, which is the only way its "before" is the
        # "before" the other arms report.
        #
        # Two predicates, because the issue asks what the ported gate ACTUALLY
        # tests and whether it reaches our population:
        #   ``zep_full_name`` — `_has_high_entropy` applied to the whole name,
        #                       exactly as Zep does (`dedup_helpers.py:80-86`).
        #   ``shipped``       — `_name_carries_signal`, i.e. the same predicate
        #                       applied to the name's STOPWORD-REMOVED content
        #                       tokens — the set that WOULD carry the query if the
        #                       leg used it, which is not the leg's surviving set
        #                       (`_signal_tokens` says why: the leg also drops
        #                       <2-char and all-digit tokens and the query may
        #                       take the raw-literal path instead).
        # For each: how many names it gates, and — the number that decides the
        # issue — how many of them had the CANONICAL in the window, i.e. correct
        # candidates the gate would suppress.
        #: ``suppressed_correct_k_sum`` is a SUM OVER THE MEASURED WINDOWS — a
        #: (name × k) count, so a name found at both k=3 and k=15 counts twice.
        #: ``suppressed_correct_by_k`` carries the per-window number, which is
        #: the one to quote ("74 of 86 names at k=3, 86 of 86 at k=15"); the sum
        #: is kept only so the artifact totals reconcile. There is no separate
        #: ``in_window_before`` field: it was incremented in the SAME branch and
        #: could therefore never disagree with this one, i.e. it was not an
        #: independent check.
        gate: dict[str, dict] = {
            p: {"names": 0, "gated": 0, "gated_and_missed": 0,
                "suppressed_correct_k_sum": 0,
                "suppressed_correct_by_k": {str(k): 0 for k in _GATE_KS
                                            if k in KS},
                #: ⛔ A SUM OVER THE MEASURED WINDOWS, like
                #: ``suppressed_correct_k_sum`` above and for the same reason:
                #: a gated name missed at BOTH k=3 and k=15 contributes twice.
                #: There is no per-k sibling for this field (none is quoted
                #: anywhere), so read it as an event count, never as a count of
                #: names — the 184 %-ratio and 4×-over-report defects this
                #: module records twice are exactly this key's failure mode.
                "noise_rows_freed": 0}
            for p in ("zep_full_name", "shipped")}
        #: per gated name, the evidence the falsifier needs: ONE entry per name,
        #: listing which predicates gate it. ⛔ It was appended inside the
        #: per-predicate loop, so a name gated by BOTH predicates appeared TWICE
        #: (98 entries for 85 names) and every "N of 98 rows" figure read off it
        #: was inflated by the overlap. Capped only by the sample, never sliced.
        gate_gated: list[dict] = []
        gate_by_class: dict[str, dict[str, dict[str, int]]] = collections.defaultdict(
            lambda: {c: {"names": 0, "gated": 0, "suppressed_correct_k_sum": 0}
                   for c in ("zep_full_name", "shipped")})

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
            # ── identity is the MINTED ID, never the name: `_norm` folds
            # whitespace and case, so a name comparison credits a different node
            # whose normalised name happens to match (the corpus yields
            # fold-equal groups), and 'was the canonical returned' is a question
            # about a node, not a string.
            wide = window(name, max(KS))
            _g_pred = {"zep_full_name": not _has_high_entropy(_zep_normalize_for_fuzzy(name)),
                       "shipped": not _name_carries_signal(name)}
            _g_hit: dict[str, dict[int, bool]] = {
                p: {} for p in _g_pred if _g_pred[p]}
            for k in KS:
                rows = window(name, k)
                exact_by_class[row["cls"]][str(k)]["n"] += 1
                hit = next((i for i, r in enumerate(rows) if r["id"] == row["id"]), None)
                # #12294 gate accounting, on THIS window — no extra queries.
                if k in _GATE_KS:
                    for p, is_gated in _g_pred.items():
                        if not is_gated:
                            continue
                        _g_hit[p][k] = hit is not None
                        if hit is not None:
                            # per-k AND the reconciling sum: the per-class table
                            # must not read 0 beside a non-zero total (it did).
                            gate[p]["suppressed_correct_by_k"][str(k)] += 1
                            gate[p]["suppressed_correct_k_sum"] += 1
                            gate_by_class[row["cls"]][p][
                                "suppressed_correct_k_sum"] += 1
                        else:
                            gate[p]["noise_rows_freed"] += len(rows)
                if hit is not None:
                    exact[k]["in_window"] += 1
                    exact_by_class[row["cls"]][str(k)]["in_window"] += 1
                    exact[k]["rank_sum"] += hit + 1
                else:
                    # one over-fetch tells "never returned" from "cut at k"
                    if any(r["id"] == row["id"] for r in wide):
                        exact[k]["miss_past_k"] += 1
                    else:
                        exact[k]["miss_absent_at_max_k"] += 1
                        if not rows:
                            exact[k]["rows_zero"] += 1
                            exact_by_class[row["cls"]][str(k)]["rows_zero"] += 1
                    # NO CAP: a `len(exact_misses) < 200` guard took the HEAD of
                    # a class-blocked list, so once it bound, the crowding
                    # statistic and §3's "N of the misses" became
                    # definite-only while the denominator still said "all".
                    # 300 entries is nothing — and the artifact states
                    # `exact_misses_recorded` beside `exact_misses_total`
                    # precisely so a cap cannot return unnoticed: with no cap
                    # the two are EQUAL, so an equality that no longer holds is
                    # itself the report of a regression, not a silent skew.
                    if k == KS[0]:
                        toks = _sanitise_query_text(name).split()
                        exact_misses.append({
                            "name": name, "cls": row["cls"],
                            "returned": len(rows),
                            "wide_returned": len(wide),
                            "tokens": len(toks),
                            "signal_tokens": len(_signal_tokens(name)),
                            "gated": _name_carries_signal(name) is False,
                            "siblings_2tok": _siblings.get(name, 0),
                            "tokens_kept_numeric": len(
                                [t for t in toks if not t.isdigit()]) != len(toks),
                            "wide_top": [r["name"] for r in wide[:5]],
                        })
                # phase 1 over the SAME window — the shipped matcher, real code.
                # ⛔ The denominator is THIS ONE query's k-window, not the union
                # production hands `resolve_entities` (`search_graph`'s id-keyed
                # union, ~40 at limit=3), so this field is recall@k under another
                # name — `attached == in_window` at every k is the tell. It is
                # recorded for the arm-1 cross-check only; arm `variant` is where
                # the union does not matter (a variant is `_norm`-different from
                # every candidate).
                cand, _mode = _find_existing_entity(rows, name, "core:other")
                if cand is not None:
                    exact[k]["attached"] += 1
            # #12294: one row per name, so `names`/`gated` counts are names and
            # the per-class denominator is readable.
            for p, is_gated in _g_pred.items():
                gate[p]["names"] += 1
                gate_by_class[row["cls"]][p]["names"] += 1
                if is_gated:
                    gate[p]["gated"] += 1
                    gate_by_class[row["cls"]][p]["gated"] += 1
                    if not any(_g_hit[p].values()):
                        # gated AND the canonical was reachable at NO window —
                        # the class the gate is FOR (a query that cannot find it)
                        gate[p]["gated_and_missed"] += 1
            _g_names = [p for p, g in _g_pred.items() if g]
            if _g_names:
                gate_gated.append({
                    "name": name, "cls": row["cls"],
                    # which predicates gate this name — the field whose absence
                    # made the double-append above invisible
                    "predicates": sorted(_g_names),
                    "signal_tokens": _signal_tokens(name),
                    "in_window_by_k": {str(k): _g_hit[p].get(k)
                                       for p in _g_names for k in _GATE_KS
                                       if k in _g_hit[p]},
                    "wide_rows": len(wide),
                })
        arms["gate"] = gate
        #: `gated` and the `gate_gated_names` entries describe the SAME names now
        #: that the record is one-per-name; this cross-check makes a future
        #: double-count fail loudly instead of inflating a quoted share.
        for p in gate:
            _recorded = sum(1 for g in gate_gated if p in g["predicates"])
            assert _recorded == gate[p]["gated"], (p, _recorded, gate[p]["gated"])
        arms["gate_by_class"] = {c: dict(v) for c, v in sorted(gate_by_class.items())}
        arms["gate_gated_names"] = gate_gated
        arms["exact"] = exact
        arms["exact_misses"] = exact_misses
        arms["exact_misses_recorded"] = len(exact_misses)
        arms["exact_misses_total"] = len(sample) - exact[KS[0]]["in_window"]
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
        #: #12294, variant arm: what the gate removes from the pair population.
        #: ``gated_pairs`` counts the gated MENTIONS in this arm, at
        #: ``_GATE_KS[0]`` only (the denominator is ``pairs`` at the SAME window,
        #: so the share is readable); ``would_attach_before`` is the number that
        #: decides the issue — a gated pair that phase 1 CAN bind today becomes a
        #: guaranteed duplicate once its query is not issued.
        #: ``pairs``, ``gated_pairs``, ``in_window_before`` and
        #: ``would_attach_before`` are all counted at ONE window (``_GATE_KS[0]``)
        #: so the share is readable — the first cut counted the denominator once
        #: per surface and the numerator per (surface × k), a 184 % ratio.
        #: ``would_attach_before_any_k`` is the falsifier's number and is counted
        #: once per PAIR over every k (a pair phase 1 binds at ANY width is a
        #: duplicate the gate creates) — per pair, not per (pair × k), which is
        #: how it was first accumulated and would over-report up to 4×.
        vgate: dict[str, dict[str, int]] = {
            p: {"pairs": 0, "gated_pairs": 0, "in_window_before": 0,
                "would_attach_before": 0, "would_attach_before_any_k": 0}
            for p in ("shipped", "zep_full_name")}
        for row in sample:
            name = row["name"]
            for vclass, surface in variants(name, row["cls"]):
                folded_surface = _fold_punct(surface)
                # #12294: the gate evaluates the MENTION — a capture derives its
                # query from the surface form it extracted, never from the stored
                # name, which it does not know yet.
                _v_gated = {"shipped": not _name_carries_signal(surface),
                            "zep_full_name": not _has_high_entropy(
                                _zep_normalize_for_fuzzy(surface))}
                #: per predicate, did phase 1 bind THIS pair at ANY width — the
                #: accumulator that makes `would_attach_before_any_k` a PAIR
                #: count rather than a (pair × k) event count.
                _v_attach: dict[str, bool] = {p: False for p in _v_gated}
                for k in KS:
                    var[k]["pairs"] += 1
                    by_class[vclass][str(k)]["pairs"] += 1
                    if k == _GATE_KS[0]:
                        for _p, _g in _v_gated.items():
                            vgate[_p]["pairs"] += 1
                            if _g:
                                vgate[_p]["gated_pairs"] += 1
                    rows = window(surface, k)
                    in_win = any(r["id"] == row["id"] for r in rows)
                    if in_win:
                        var[k]["in_window"] += 1
                        by_class[vclass][str(k)]["in_window"] += 1
                    # the MENTION is the surface form, exactly as a capture's
                    # derived query would carry it — never the stored name
                    cand, _m = _find_existing_entity(rows, surface, "core:other")
                    hit_ok = cand is not None and cand.get("id") == row["id"]
                    # #12294 false-suppression: a gated mention's query is never
                    # issued, so a pair that WAS in-window loses its canonical
                    # from phase 1's window AND from phase 2's candidate table.
                    for _p, _g in _v_gated.items():
                        if not _g:
                            continue
                        if hit_ok:
                            _v_attach[_p] = True
                        if in_win and k == _GATE_KS[0]:
                            vgate[_p]["in_window_before"] += 1
                            if hit_ok:
                                vgate[_p]["would_attach_before"] += 1
                    if hit_ok:
                        var[k]["attached"] += 1
                        by_class[vclass][str(k)]["attached"] += 1
                    elif in_win:
                        var[k]["in_window_not_attached"] += 1
                    folded = [dict(r, name=_fold_punct(r["name"])) for r in rows]
                    cand2, _m2 = _find_existing_entity(folded, folded_surface,
                                                       "core:other")
                    if cand2 is not None and cand2.get("id") == row["id"]:
                        var[k]["attached_folded"] += 1
                        by_class[vclass][str(k)]["attached_folded"] += 1
                # once per PAIR, after every window is known
                for _p, _ok in _v_attach.items():
                    if _ok:
                        vgate[_p]["would_attach_before_any_k"] += 1
        arms["variant"] = var
        arms["variant_by_class"] = {c: dict(v) for c, v in sorted(by_class.items())}
        arms["gate_variant"] = vgate

        # ── ARM 3: what a capture actually hands ``resolve_entities``.
        # ``search_graph`` accumulates every query's rows into ONE id-keyed dict,
        # so the effective candidate set is the UNION over the capture's derived
        # queries — not ``limit``. Measured here at ``max_queries`` names per
        # capture, which is the production budget (``max_queries=15``).
        pooled = {k: {"captures": 0, "set_sum": 0, "set_min": 10**9, "set_max": 0}
                  for k in KS}
        #: #12294: the PRODUCTION-TRUTH cost of the gate, which the isolated arms
        #: cannot answer. `search_graph` unions every derived query into ONE
        #: id-keyed candidate set, so a gated name's canonical can still arrive
        #: through ANOTHER name's query in the same capture. This measures exactly
        #: that: build the union from the NON-gated names only (what the gate
        #: would leave in place), then ask whether each gated name's canonical is
        #: still in it.
        #: ⛔ ``lost`` is NOT "every gated name that is not rescued" — it is a
        #: recall cost, so it must be one the gate CAUSES: the canonical was in
        #: the UNGATED union (``merged``) and is not in the gated one. A gated
        #: name whose canonical neither union holds is
        #: ``unreachable_regardless`` — the gate costs nothing there, so counting
        #: it as loss would inflate the decline's own evidence (a defect that ran
        #: the flattering-for-the-conclusion way: the first cut booked all 12 k=3
        #: misses as ``lost``).
        #: ⛔ IT IS PER-WINDOW, AND THAT IS NOT #12294's TARGET CLASS. This branch
        #: books a name no query in THIS window can reach — at k=3 on the measured
        #: corpus that is ~12 names window 15 finds fine. The target class (a name
        #: unreachable at ANY measured window) is `gate[p]["gated_and_missed"]`,
        #: which is 0 here. Reading this field as "the class Zep's gate protects"
        #: inverts the finding; the label below says so because a lane quoting it
        #: under the arm's authority would be quoting the inversion.
        #: ⚠️ THE COMMITTED ARTIFACT PREDATES THE SPLIT: its `shipped` k=3 cell
        #: (`lost` 85) is therefore an UPPER bound — the honest value is
        #: ``86 - rescued - unreachable_regardless``, bounded below by
        #: ``86 - 1 - 12 = 73`` since every name whose own query found it is in
        #: ``merged``. Its k=15 cell is EXACT (all 86 gated names are in-window at
        #: k=15, so ``unreachable_regardless`` is 0 there), which is why the PR
        #: body quotes k=15's 80 lost and never k=3's.
        #: ⛔ Per WINDOW, and the key says which: the first cut measured k=3 only
        #: and recorded no `k`, so "1 of 85 rescued" was silently the NARROWEST
        #: union — the choice that maximises apparent loss. k=15 is recorded
        #: beside it, and the artifact now names both.
        pooled_gate: dict[str, dict[str, dict[str, int]]] = {
            p: {str(k): {"gated": 0, "rescued_by_union": 0, "lost": 0,
                        "unreachable_regardless": 0}
                for k in _GATE_KS if k in KS}
            for p in ("shipped", "zep_full_name")}
        budget = 15   # production ``max_queries``
        #: The pooled arms' own denominator, recorded beside their numbers — see
        #: `_pooled_coverage` for why a silent cap is the defect here.
        _pooled = _pooled_coverage(len(sample), args.captures, budget)
        arms["pooled_coverage"] = _pooled
        for start in range(0, _pooled["names_reached"], budget):
            group = sample[start:start + budget]
            if len(group) < 2:
                continue
            for k in KS:
                merged: dict[str, dict] = {}
                #: per PREDICATE, the ids the gate would leave in place — built
                #: from exactly the names that predicate keeps, so neither
                #: column is measured against the other's set.
                kept: dict[str, dict[str, dict]] = {
                    p: {} for p in ("shipped", "zep_full_name")}
                for row in group:
                    rows_here = window(row["name"], k)
                    for r in rows_here:
                        if r["id"] and r["id"] not in merged:
                            merged[r["id"]] = r
                    for p, _keeps in (
                            ("shipped", _name_carries_signal(row["name"])),
                            ("zep_full_name", _has_high_entropy(
                                _zep_normalize_for_fuzzy(row["name"])))):
                        if not _keeps:
                            continue
                        for r in rows_here:
                            if r["id"]:
                                kept[p][r["id"]] = r
                size = len(merged)
                pooled[k]["captures"] += 1
                pooled[k]["set_sum"] += size
                pooled[k]["set_min"] = min(pooled[k]["set_min"], size)
                pooled[k]["set_max"] = max(pooled[k]["set_max"], size)
                if k not in _GATE_KS:
                    continue
                for row in group:
                    for p, is_gated in (
                            ("shipped", not _name_carries_signal(row["name"])),
                            ("zep_full_name", not _has_high_entropy(
                                _zep_normalize_for_fuzzy(row["name"])))):
                        if not is_gated:
                            continue
                        cell = pooled_gate[p][str(k)]
                        cell["gated"] += 1
                        if row["id"] in kept[p]:
                            cell["rescued_by_union"] += 1
                        elif row["id"] in merged:
                            # reachable BEFORE the gate, gone after it: a real loss
                            cell["lost"] += 1
                        else:
                            # neither union holds it — the gate costs nothing here
                            cell["unreachable_regardless"] += 1
        arms["pooled_gate"] = pooled_gate
        arms["pooled"] = pooled

        # ── ARM 5 (#12297): the cross-encoder rerank over the SAME candidates.
        #
        # ⛔ A reranker REORDERS what retrieval returned; it cannot surface a row
        # the leg never produced. So this arm's ceiling is `in_window(P)` — the
        # golds the legs put INSIDE the scored window at that width — which is
        # WIDTH DEPENDENT, so the ACHIEVED figures live in `arms.rerank.<P>.exact`
        # (`recall_3_after` / `mentions`, computed) and NOT as a literal here: a
        # re-run moves them, and an earlier draft hardcoded one run's pair in the
        # emitted note. ⛔ `recall_3_after` is what the arm ACHIEVED, not the
        # ceiling: they coincide only where `lost` is 0, so the note names both the
        # ceiling (in_window) and the achieved cell — they coincide when
        # `recall_3_before - lost + gained` reaches `in_window`; `lost` 0 is
        # NECESSARY but not sufficient (a no-gain rerank has it 0 with the achieved
        # figure still below the ceiling). ⛔ Not `arms.exact.<P>`: that
        # table is arm 1's own width-by-width one, and a pool width outside its
        # keys has no cell there.
        #
        # ⛔ `miss_past_k` is NOT that ceiling, and this comment used to say it
        # was: it is 0 BY CONSTRUCTION only at arm 1's WIDEST window (a response
        # has at most that many rows, so "the rank is past it" cannot occur) and
        # only possibly non-zero at a narrower k, where a gold the legs returned
        # further down is genuinely past the cut — so it measures the counter, not
        # the legs, at any width. ⛔ "non-zero at every narrower k" was the previous
        # wording and it bakes a corpus fact into a generated string: 0 is reachable
        # whenever every in-window gold sits inside the top k. `miss_absent_at_max_k` is not a width-independent bound either —
        # it is "absent within arm 1's maximum window (15)", and the pool-40 arm
        # returns every one of those golds. The count is the artifact's own
        # field, deliberately not restated as a literal here (an earlier draft
        # said 81, which was a previous run's number). The bound is stated rather
        # than left to inference because a rerank arm reporting a recall figure
        # without it invites "the reranker fixed recall", which is the one thing
        # it cannot do (issue #12297).
        #
        # The measurement is per-mention and PAIRED against the unreranked order
        # of the SAME rows, so `gained` / `lost` are counterfactual on one pool
        # and cannot be explained by a population difference:
        #   gained  gold sat past the top-3 and the rerank moved it in.
        #   lost    gold sat inside the top-3 and the rerank moved it out —
        #           the failure a rerank-only story hides, and the reason the
        #           net is reported beside both components.
        if args.rerank:
            arms["rerank"] = build_rerank_arm(args, sample, window, variants)

        # ── #12297 residual 1: the LEXICAL BASELINE CONTROL ──────────────────
        # Same arms, same pools, same pairs, same cut — only the scorer changes.
        # It is emitted under its own arm name so a reader can never mistake a
        # string-compare figure for the cross-encoder's, and it records the
        # `lexical_control` note that says what it is for.
        if args.lexical_baseline:
            for mode in ([args.lexical_baseline]
                         if args.lexical_baseline != "both" else LEXICAL_MODES):
                key = f"lexical_{mode}"
                arms[key] = _rerank_arm(
                    sample, window, variants,
                    model=f"lexical:{mode}",
                    pools=tuple(args.rerank_pool or RERANK_POOLS),
                    top_k=TOP_K_RERANK_CUT, ndcg_k=NDCG_K,
                    threads=0, scorer=LexicalScorer(mode), label=key)
                arms[key]["lexical_control"] = (
                    "NOT a matcher and NOT shipped: the decisive control for "
                    "#12297 residual 1. If this reproduces the cross-encoder "
                    "arm over the same pools, the lever buys name matching a "
                    "string compare already gives.")

        # ⛔ A MIXED run is refused: `legs_seen` OR-s the looser #3005 predicate
        # over thousands of windows, so a run where the dense leg held for some
        # windows and degraded for others would carry the label "hybrid" for a
        # measurement that is part keyword-only. Every committed artifact must
        # come from ONE mode (the strict per-window verdict).
        uniform = (window_modes["keyword_only"] == 0
                   or window_modes["hybrid"] == 0)
        if not uniform and not args.allow_mixed_modes:
            print(f"REFUSED: MIXED retrieval modes across windows "
                  f"({window_modes}) — an artifact labelled from this run would "
                  f"report a mixture as one mode. Re-run on a box whose dense leg "
                  f"is stable, or pass --allow-mixed-modes to record it AS a "
                  f"mixture (the note then says so).", file=sys.stderr)
            return 3

        #: The harvest's OWN file count, resolved BEFORE the report dict is built:
        #: `.get(key, <expr>)` evaluates its default EAGERLY, so writing the glob
        #: as the fallback re-ran the very second glob of the live directory that
        #: `harvest()`'s `stats["files"]` exists to remove (and discarded it).
        _harvest_files = _input_stats.get("files")
        if _harvest_files is None:
            # Only a fake/stub harvest that reported no count reaches this.
            _harvest_files = len(list(sessions.glob("*.jsonl")))
        report = {
            "issue": 11901,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            #: Whether the read's own rerank gate was armed during this run. False
            #: is the only shape whose "before" is the fused baseline; True is
            #: only reachable with --allow-reranked-baseline, and a reader
            #: comparing cells needs to know which they are looking at.
            "config": {"fused_rerank_gate_armed": bool(_ambient_gate)},
            "corpus": {"dir": str(sessions),
                       "files": _harvest_files,
                       "input": dict(_input_stats),
                       "harvested_distinct": counts, "population": len(pop),
                       "sample": len(sample),
                       # The graph the population was written to, and a digest of
                       # the names — both absent from the first-generation
                       # artifacts, which is why those cannot be re-proved
                       # (a reused graph keeps the OLD name at a shifted id,
                       # #8919).
                       "graph": graph_name, "population_digest": pop_digest,
                       "sample_by_class": dict(sorted(shares.items())),
                       "elapsed_s": round(time.time() - t0, 1)},
            "ks": list(KS),
            "retrieval": {
                "has_embedder": _has_embedder(),
                "legs_seen": dict(sorted(legs_seen.items())),
                # The STRICT per-window verdict (`require_hybrid_read`); a mixed
                # run is refused above unless explicitly allowed, in which case
                # `uniform_windows` says so in the artifact itself.
                "window_modes": dict(sorted(window_modes.items())),
                "uniform_windows": uniform,
                # ⛔ The note is derived from the LEGS, never from embedder
                # availability: a model that loads but whose leg never answers
                # (`ran=False, reason="encode_failed"`, or a breaker-open empty
                # trace) would otherwise be labelled "hybrid" — the exact false
                # label `retrieval_preflight` exists to prevent, in the first
                # field a reader checks.
                "note": ("MIXED: see window_modes" if not uniform
                         else _mode_note(_has_embedder(), legs_seen)),
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
        with contextlib.suppress(Exception):
            sdk.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    p.add_argument("--population", type=int, default=1000)
    p.add_argument("--sample", type=int, default=300)
    p.add_argument("--captures", type=int, default=20,
                   help="pooled-set groups to emulate (a capture = max_queries=15 names)")
    p.add_argument("--namespace", default="recall11901")
    p.add_argument("--graph", default=None,
                   help="graph to write the population to; default is RUN-SCOPED "
                        "(a reused graph keeps the previous run's names at any "
                        "shifted id — `o.name` is an ON CREATE write, #8919)")
    p.add_argument("--allow-mixed-modes", action="store_true",
                   help="record a run whose windows did not all share one "
                        "retrieval mode (the artifact then says MIXED); refused "
                        "by default, because a mixture published as one mode is "
                        "the false-label class #2952/#3005 exist to prevent")
    p.add_argument("--out", default=None)
    # ── #12297: the cross-encoder rerank arm. DEFAULT OFF, so every existing
    # invocation — and every committed artifact — is byte-identical.
    p.add_argument("--rerank", action="store_true",
                   help="also measure arm 5: the cross-encoder rerank over the "
                        "same fused candidates at each --rerank-pool width. "
                        "Off by default so the committed artifacts' shape is "
                        "unchanged.")
    p.add_argument("--rerank-pool", type=int, action="append", default=None,
                   help="pool width(s) the rerank arm fetches at; repeatable. "
                        "Default 15 (Zep's candidate count, the width #11901 "
                        "measured) and 40 (the mean effective candidate-list "
                        "length #11901 measured for a production capture — the "
                        "figure `TORTOISE_FUSED_RERANK_POOL` now defaults to). "
                        "MEASURED HERE, never adopted HERE: this flag is not the "
                        "product's default. The `limit` this harness reads at stays "
                        "3 (the capture prior's own), a DIFFERENT quantity from the "
                        "rerank width.")
    p.add_argument("--rerank-model", default=None,
                   help="cross-encoder to measure; default is "
                        "`tortoise.rerank.fused_rerank_model()` — the resolver "
                        "the shipped read goes through, so the arm measures the "
                        "model the product would load (including a "
                        "TORTOISE_FUSED_RERANK_MODEL override)")
    p.add_argument("--rerank-threads", type=int, default=RERANK_THREADS,
                   help=f"torch threads for the rerank arm (default "
                        f"{RERANK_THREADS}, PINNED and recorded). The unpinned "
                        f"default measured ~20x slower per pair on this box "
                        f"under fleet load; 0 leaves torch's default in place "
                        f"and the artifact then says so")
    p.add_argument("--lexical-baseline", choices=(*LEXICAL_MODES, "both"),
                   default=None,
                   help="#12297 residual 1 — also measure a LEXICAL string "
                        "scorer over the SAME pools as the rerank arm, to test "
                        "whether a ~5us compare reproduces what the "
                        "cross-encoder is paid 207 ms/mention for. A control "
                        "only: not shipped, not a matcher, reaches no product "
                        "path.")
    p.add_argument("--allow-reranked-baseline", action="store_true",
                   help="#12297 — proceed even when TORTOISE_FUSED_RERANK is "
                        "armed. Without this the run REFUSES (exit 3), because "
                        "the read's own order is already reranked and every "
                        "arm's \"before\" would be a rerank, i.e. a "
                        "rerank-vs-rerank zero that looks exactly like a real "
                        "one. The artifact records `fused_rerank_gate_armed` "
                        "either way.")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
