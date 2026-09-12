"""#3011 Track E — the 4-arm context-assembly runner + metrics.

Spec (frozen, authoritative):
``docs/experiments/2026-09-11-abc-context-assembly-experiment.md`` (the
"spec"). Where the spec and the implementation plan disagree, the spec wins.

This module is the **driver** for the pre-registered A/B/C/D experiment:

=============  ====================================================
arm            context slot handed to the one pinned reader
=============  ====================================================
``A``          the **GOLD** sessions rendered **verbatim** through
               ``render_context`` (turn text intact), resolved from
               ``answer_session_ids`` — the oracle ceiling, not a retrieval arm
``B``          the epistemic subgraph (``build_subgraph`` → ``render_arm_b``),
               typed relation lines, **no raw turn text**
``C``          arm B plus each anchor claim's verbatim source turns
               (``render_arm_c``)
``D``          no context — the ``Current Date:`` header only
               (``render_context([], question_date=...)``)
=============  ====================================================

Controls (§5) enforced here
---------------------------
* **One pinned reader** for all arms (the caller passes one object); the
  driver calls the identical ``answer_with_evidence`` path for every arm.
* **One prompt scaffolding** used byte-identically across arms — the
  pre-rendered-evidence seam of ``tortoise/reader.py``
  (``build_reader_user_message`` + ``system_prompt_for``); only the
  ``{context}`` slot differs. :func:`reader_prompt_hash` records the one
  scaffolding identity in every arm's methodology block.
* **One judge** for all arms (the caller passes one object).
* **Fresh graph namespace per question** — the driver builds one SDK per
  question through ``sdk_factory`` and records the namespace.
* **Matched context budget** — every arm is capped at the same
  ``max_words`` and the per-arm word *distribution* is reported (metric 2).
* ``measure_temporal.assert_reader_constancy`` is called over the four
  methodology blocks before any report is written.

Two dates convention (resolved ambiguity, recorded in the run manifest)
-----------------------------------------------------------------------
``render_context`` embeds the ``Current Date:`` header for arms A and D,
but ``render_arm_b`` / ``render_arm_c`` do **not** emit one. Because §5 makes
the header part of the byte-identical scaffolding and temporal-reasoning
questions are structurally unanswerable without it, the driver prepends the
same header to the B/C context slots (:func:`_with_date_header`). The slot
content is otherwise untouched.

Metrics
-------
Metric 5 (answer-bearing-claim presence), metric 8 (typed-relation presence)
and metric 9 (gold-provenance presence) are **content/graph measurements**.
They are computed in the dedicated "metrics layer" below, which reads the
pre-registered gold-evidence claim artifact
(``docs/experiments/artifacts/2026-09-11-abc-context-assembly/
gold-evidence-claims.json``) and a graph scan. **The seed / traversal /
ranking / B-C render code paths never open that artifact** (§3 static
reference assertion, enforced by ``tools/longmem_eval/leakage_guard.py``).
The metrics layer is never called from :func:`build_context_arm`.

Report output matches the existing lane's shape::

    {"arm": ..., "methodology": {...}, "n_outcomes": N, "outcomes": [...]}

with a non-empty ``reader_model_spec`` / ``reader_prompt_hash`` /
``judge_model`` in every methodology block, so
``tools.longmem_eval.measure_temporal`` can load it (its ``gate_output``
still validates arm ids against the #2578 ``ARM_TABLE``; the four
context-assembly arms are a new experiment, so the file is *shape*-consumable
— see the run manifest).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.longmem_eval.ep_activation import activate_beliefs
from tools.longmem_eval.gold_evidence_claims import (
    ARTIFACT_PATH as GOLD_ARTIFACT_PATH,
)
from tools.longmem_eval.gold_evidence_claims import (
    STOPWORDS,
    tokenize,
)
from tools.longmem_eval.judge import build_judge, is_abstention
from tools.longmem_eval.reader import build_reader
from tortoise.reader import (
    _looks_abstained,
    build_reader_user_message,
    reader_prompt_constants,
)
from tortoise.retrieval import render_context
from tortoise.subgraph import SEED_COUNT, SEED_LIMIT, Subgraph, build_subgraph
from tortoise.subgraph_render import (
    render_arm_b,
    render_arm_c,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ANSWER_BEARING_THRESHOLD",
    "ARMS",
    "CENSUS_DEFAULT",
    "DEFAULT_GOLD_ARTIFACT",
    "DEFAULT_MAX_WORDS",
    "ArmContext",
    "GraphPoint",
    "GraphScan",
    "QuestionContext",
    "answer_bearing_point_ids",
    "build_arg_parser",
    "build_context_arm",
    "build_report",
    "claim_matches_point",
    "embedder_model_id",
    "graph_metrics",
    "load_census_classes",
    "load_gold_claims",
    "load_question_context",
    "main",
    "reader_prompt_hash",
    "reader_prompt_sha256",
    "repo_git_sha",
    "run_experiment",
    "scan_eval_graph",
]

# ── frozen constants ──────────────────────────────────────────────────────

#: The four pre-registered arms (§3).
ARMS: tuple[str, ...] = ("A", "B", "C", "D")

#: Metric-5 match threshold (§4): ``|tokens(g) ∩ tokens(p)| / |tokens(g)| ≥ .80``.
ANSWER_BEARING_THRESHOLD = 0.80

#: The §5 matched context budget (whitespace words, via ``int(len*1.1)``).
DEFAULT_MAX_WORDS = 8000

#: The pre-registered gold-evidence claim artifact (spec §4 Storage).
DEFAULT_GOLD_ARTIFACT = GOLD_ARTIFACT_PATH

#: The #2578 census (qid → temporal class) used for metric 4.
CENSUS_DEFAULT = "tests/_assembly_census.json"

#: The metric-4 analysis classes (exact match; §4 + #2578 ANALYSIS_CLASSES).
ANALYSIS_CLASSES: tuple[str, ...] = ("ordering/compare", "interval",
                                     "current-state")

#: Edge classes metric 8 counts as typed relations (§4).
TYPED_RELATIONS: tuple[str, ...] = ("IMPL", "NAND", "supersession")

#: Budget-unit factor (§5): ``int(len(text.split()) * 1.1)``.
_WORD_TOKEN_FACTOR = 1.1

#: Repo root — ``<root>/tools/longmem_eval/context_assembly_arms.py``.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The frozen serializer module whose path + git sha the manifest records (§3).
SERIALIZER_MODULE_PATH = "tortoise/subgraph_render.py"

#: The fixed §10.1 ``lme_session_index`` permutation seed. Mirrors
#: ``leakage_guard.fixed_session_index_permutation``'s ``seed`` default, so the
#: perturbation the manifest records is the one the leakage test applies.
SESSION_INDEX_PERMUTATION_SEED = 0xD1CE

#: Turn-node id suffix ``…:s<si>:t<ti>`` (ti is 0-based).
_TURN_ID_RE = re.compile(r":s(\d+):t(\d+)$")

_EP_UNSET = object()


# ── data shapes ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QuestionContext:
    """Everything an arm needs from a dataset row (no gold fields used)."""

    qid: str
    question_text: str
    question_type: str
    question_date: str | None
    answer: str
    answer_session_ids: tuple[str, ...]
    haystack_session_ids: tuple[str, ...]
    haystack_sessions: tuple[tuple[Mapping[str, Any], ...], ...]
    haystack_dates: tuple[str, ...]
    raw: Mapping[str, Any] = field(repr=False, default_factory=dict)

    @property
    def session_dates(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for sid, date in zip(self.haystack_session_ids, self.haystack_dates,
                             strict=False):
            if date:
                out[str(sid)] = str(date)
        return out


@dataclass(frozen=True)
class ArmContext:
    """One arm's rendered ``{context}`` slot plus its size accounting."""

    arm: str
    text: str
    #: True whitespace words — metric 2's unit.
    word_count: int
    #: ``int(len(text.split()) * 1.1)`` — the §5 budget unit.
    budget_words: int
    context_source: str
    zero_seed: bool | None
    seed_fn: str | None
    #: All points admitted by traversal+ranking (anchors + candidates) —
    #: metric 6's "claims admitted".
    claims_admitted: int
    #: Anchor blocks rendered (claim lines) — the rendered subset.
    claims_rendered: int
    relations_rendered: int
    #: Count of reserved relation lines dropped by budget truncation (§3
    #: ``reserved_overflow``; B/C only — 0 for A/D).
    reserved_overflow: int = 0
    #: Selected seeds in **rank order** (§3 seed policy; B/C only).
    seed_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphPoint:
    """One non-operator Point read for metrics 5/8/9 (content + provenance)."""

    point_id: str
    content: str
    session_id: str | None


@dataclass(frozen=True)
class GraphScan:
    """A content-only snapshot of the eval graph for metrics 5/8/9.

    ``typed_relation_endpoint_ids`` is the set of every Point id that is an
    endpoint of an ``IMPL`` / ``NAND`` / supersession (``CORRECTS``) relation.
    The snapshot is deliberately separate from the subgraph so the metrics
    cannot be influenced by what the render path selected.
    """

    points: tuple[GraphPoint, ...]
    typed_relation_endpoint_ids: frozenset[str]


# ── small helpers ─────────────────────────────────────────────────────────


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _budget_words(text: str) -> int:
    return int(len(text.split()) * _WORD_TOKEN_FACTOR)


def _words(text: str) -> int:
    return len(text.split())


def _with_date_header(text: str, question_date: str | None) -> str:
    """Prepend the shared ``Current Date:`` header (B/C only — see module doc).

    Arms A/D get theirs from ``render_context``; arms B/C render none, so the
    driver supplies the identical header here. A falsy date leaves the text
    byte-identical (mirrors ``render_context``).
    """
    if not question_date:
        return text
    return f"Current Date: {question_date}\n\n{text}"


def _reader_prompt_source_text() -> str:
    """Canonical text of the ONE reader scaffolding (§5), context slot blank."""
    generic, fragments = reader_prompt_constants()
    parts = [generic, build_reader_user_message("{context}", "{question}")]
    for key in sorted(fragments):
        parts.append(f"{key}={fragments[key]}")
    return "\n".join(parts)


def reader_prompt_hash() -> str:
    """The ONE reader-prompt scaffolding identity recorded per arm (§5).

    Hashes the actual scaffolding the pre-rendered lane uses — the generic
    system prompt, the universal abstention clause, every type fragment and
    the user-message template with its ``{context}`` / ``{question}`` slots
    blanked — so prompt drift is human-visible and every arm carries the same
    value. The ``{context}`` slot itself is intentionally excluded: it is the
    only thing that differs between arms.
    """
    return _sha16(_reader_prompt_source_text())


def reader_prompt_sha256() -> str:
    """Full sha256 of the scaffolding (the manifest's §5 prompt digest)."""
    return hashlib.sha256(_reader_prompt_source_text().encode("utf-8")).hexdigest()


def stopwords_hash() -> str:
    """sha256 of the frozen stopword list (recorded in the run manifest)."""
    return _sha16("\n".join(sorted(STOPWORDS)))


# ── context builders (the RENDER path — never touches the gold artifact) ──


def load_question_context(row: Mapping[str, Any]) -> QuestionContext:
    """Derive the render inputs from a dataset row (no gold fields read)."""
    qid = str(row["question_id"])
    sessions = tuple(
        tuple(session or ()) for session in (row.get("haystack_sessions") or ())
    )
    return QuestionContext(
        qid=qid,
        question_text=str(row.get("question") or ""),
        question_type=str(row.get("question_type") or ""),
        question_date=(str(row.get("question_date") or "") or None),
        answer="" if row.get("answer") is None else str(row.get("answer")),
        answer_session_ids=tuple(str(s) for s in (row.get("answer_session_ids") or ())),
        haystack_session_ids=tuple(str(s) for s in (row.get("haystack_session_ids") or ())),
        haystack_sessions=sessions,
        haystack_dates=tuple(str(d) for d in (row.get("haystack_dates") or ())),
        raw=dict(row),
    )


def _turn_ref(point_id: Any, props: Mapping[str, Any]) -> str | None:
    """The point's stored source-turn id, else its own ``…:t<ti>`` id."""
    ref = props.get("source_turn_id")
    if ref:
        return str(ref)
    if _TURN_ID_RE.search(str(point_id)):
        return str(point_id)
    return None


def _resolve_turn(
    turn_ref: str | None,
    haystack_sessions: Sequence[Sequence[Mapping[str, Any]]],
) -> dict[str, str] | None:
    """Resolve ``lme:<qid>:s<si>:t<ti>`` against the dataset's sessions."""
    if not turn_ref:
        return None
    match = _TURN_ID_RE.search(str(turn_ref))
    if match is None:
        return None
    si, ti = int(match.group(1)), int(match.group(2))
    if si >= len(haystack_sessions):
        return None
    session = haystack_sessions[si]
    if ti >= len(session):
        return None
    turn = session[ti]
    return {
        "role": str(turn.get("role") or "unknown"),
        "content": str(turn.get("content") or ""),
    }


def turns_by_point(
    sg: Subgraph, haystack_sessions: Sequence[Sequence[Mapping[str, Any]]]
) -> dict[str, list[dict[str, str]]]:
    """Arm-C verbatim turns keyed by admitted point id.

    A point's source turn is resolved from its own stored ``source_turn_id``
    (extracted claims) or its turn-node id (turn points) against the
    question's frozen ``haystack_sessions`` — never from a gold field.
    """
    admitted = list(sg.anchors) + [c.point_id for c in sg.candidates]
    out: dict[str, list[dict[str, str]]] = {}
    for point_id in admitted:
        key = str(point_id)
        props = sg.point_props.get(key, {}) or {}
        turn = _resolve_turn(_turn_ref(point_id, props), haystack_sessions)
        if turn is not None:
            out[key] = [turn]
    return out


def build_context_arm(
    arm: str,
    qctx: QuestionContext,
    sdk: Any,
    *,
    namespace: str | None = None,
    max_words: int = DEFAULT_MAX_WORDS,
) -> ArmContext:
    """Render one arm's ``{context}`` slot.

    The B/C render path is gold-free (spec §3 labeling rule); arm A is the
    gold-verbatim oracle ceiling and reads the question's gold sessions by
    design, inside its own branch.
    """
    if arm == "A":
        # ── the gold-verbatim ORACLE CEILING (spec §3) ────────────────────
        # Arm A is NOT a retrieval arm. Spec §3 defines it as the question's
        # GOLD sessions rendered verbatim through ``render_context`` with the
        # turn text intact — resolved from ``answer_session_ids``. Every
        # F/H statistic is computed relative to arm A, and §7 F3's frozen
        # ``A_ref = 42/52`` exists only for the gold render, so a
        # retrieval-based A would invalidate every reported delta.
        #
        # This is the ONLY gold read in the driver and it is legitimate: the
        # §3/§10 leakage rule governs the seed / traversal / ranking / B-C
        # render paths, never the arm-A oracle. ``leakage_guard`` carves this
        # ``arm == "A"`` branch out of the §10.2 static assertion by name.
        position_by_sid: dict[str, list[int]] = {}
        for i, sid in enumerate(qctx.haystack_session_ids):
            position_by_sid.setdefault(str(sid), []).append(i)
        selected: list[int] = []
        for sid in qctx.answer_session_ids:
            positions = position_by_sid.get(str(sid))
            if not positions:
                raise ValueError(
                    f"{qctx.qid}: gold session id {sid!r} is not resolvable "
                    "against haystack_session_ids — arm A cannot be built")
            for index in positions:
                if index >= len(qctx.haystack_sessions):
                    raise ValueError(
                        f"{qctx.qid}: gold session {sid!r} maps to index "
                        f"{index} but haystack_sessions has "
                        f"{len(qctx.haystack_sessions)} entries")
                selected.append(index)
        hits: list[dict[str, Any]] = []
        for index in sorted(set(selected)):
            session = qctx.haystack_sessions[index]
            transcript = "\n".join(
                f"{str(turn.get('role') or 'unknown').title()}: "
                f"{turn.get('content') or ''}"
                for turn in session
            )
            sid = qctx.haystack_session_ids[index]
            hits.append({
                "id": f"lme:{qctx.qid}:s{index}",
                "content": transcript,
                "session_id": str(sid),
                "lme_session_index": index,
                "session_date": (
                    qctx.haystack_dates[index]
                    if index < len(qctx.haystack_dates) else ""),
            })
        text = render_context(hits, question_date=qctx.question_date)
        return ArmContext(
            arm=arm, text=text, word_count=_words(text),
            budget_words=_budget_words(text), context_source="gold-verbatim",
            zero_seed=None, seed_fn=None, claims_admitted=0,
            claims_rendered=0,
            relations_rendered=0,
        )

    if arm == "D":
        text = render_context([], question_date=qctx.question_date)
        return ArmContext(
            arm=arm, text=text, word_count=_words(text),
            budget_words=_budget_words(text), context_source="no-context",
            zero_seed=None, seed_fn=None, claims_admitted=0,
            claims_rendered=0,
            relations_rendered=0,
        )

    if arm in ("B", "C"):
        sg = build_subgraph(sdk, qctx.question_text, namespace=namespace)
        common: dict[str, Any] = {
            "haystack_session_ids": qctx.haystack_session_ids,
            "session_dates": qctx.session_dates,
            "max_words": max_words,
            "points_by_id": sg.point_props,
        }
        if arm == "B":
            result = render_arm_b(sg, **common)
            source = "subgraph"
        else:
            result = render_arm_c(
                sg, turns_by_point=turns_by_point(sg, qctx.haystack_sessions),
                **common)
            source = "union"
        text = _with_date_header(result.text, qctx.question_date)
        return ArmContext(
            arm=arm, text=text, word_count=_words(text),
            budget_words=_budget_words(text), context_source=source,
            zero_seed=bool(sg.zero_seed), seed_fn=sg.seed_fn,
            claims_admitted=len(sg.anchors) + len(sg.candidates),
            claims_rendered=result.claims_rendered,
            relations_rendered=result.relations_rendered,
            reserved_overflow=result.reserved_overflow,
            seed_ids=tuple(str(point_id) for point_id, _score in sg.seeds),
        )

    raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")


# ══════════════════════════════════════════════════════════════════════════
# METRICS LAYER — reads the gold-evidence artifact + a graph scan.
# Never called from ``build_context_arm`` / the seed / traversal / render
# paths (§3 static reference assertion).
# ══════════════════════════════════════════════════════════════════════════


def load_gold_claims(path: str | Path = DEFAULT_GOLD_ARTIFACT) -> dict[str, list[dict]]:
    """Load the pre-registered gold-evidence claim artifact (§4 Storage)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"gold-evidence artifact must be a JSON object: {path}")
    out: dict[str, list[dict]] = {}
    for qid, claims in raw.items():
        if not isinstance(claims, list):
            raise ValueError(f"artifact[{qid!r}] must be a list of claims")
        out[str(qid)] = [dict(c) for c in claims]
    return out


def gold_artifact_sha256(path: str | Path = DEFAULT_GOLD_ARTIFACT) -> str:
    """sha256 of the artifact bytes (recorded in the run manifest, §9.5)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def claim_matches_point(
    claim: Mapping[str, Any],
    point_content: str,
    *,
    threshold: float = ANSWER_BEARING_THRESHOLD,
) -> bool:
    """The frozen metric-5 content rule (§4).

    A claim flagged ``trivial: true`` is **never** matched. Otherwise the
    frozen :func:`~tools.longmem_eval.gold_evidence_claims.tokenize` is
    applied to both texts and the content-token **sets** are compared:
    ``len(tokens(g) ∩ tokens(p)) / len(tokens(g)) >= threshold``.
    """
    if claim.get("trivial"):
        return False
    gold = set(tokenize(str(claim.get("claim") or "")))
    if not gold:
        return False
    point = set(tokenize(str(point_content or "")))
    return (len(gold & point) / len(gold)) >= threshold


def answer_bearing_point_ids(
    scan: GraphScan,
    gold_claims: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Metric 5's per-point primitive: ids of answer-bearing Points."""
    return {
        p.point_id
        for p in scan.points
        if any(claim_matches_point(g, p.content) for g in gold_claims)
    }


def metric5_present(scan: GraphScan, gold_claims: Sequence[Mapping[str, Any]]) -> bool:
    """Metric 5 — ≥1 Point in the eval graph is answer-bearing (§4)."""
    return bool(answer_bearing_point_ids(scan, gold_claims))


def metric8_present(scan: GraphScan, answer_bearing_ids: Iterable[str]) -> bool:
    """Metric 8 — ≥1 typed relation has an answer-bearing endpoint (§4)."""
    return bool(set(scan.typed_relation_endpoint_ids) & set(answer_bearing_ids))


def metric9_present(scan: GraphScan, answer_session_ids: Iterable[str]) -> bool:
    """Metric 9 — ≥1 Point's provenance resolves to a gold session (§4).

    Content-free by construction: an unrelated Point from a gold session
    satisfies it. Never used for F6/H5 or the §9.4 gate.
    """
    gold = {str(s) for s in answer_session_ids if s}
    if not gold:
        return False
    return any(
        p.session_id is not None and str(p.session_id) in gold
        for p in scan.points
    )


def graph_metrics(
    scan: GraphScan,
    gold_claims: Sequence[Mapping[str, Any]],
    answer_session_ids: Iterable[str],
) -> dict[str, Any]:
    """Per-question metrics 5/8/9 computed from the artifact + graph scan."""
    bearing = answer_bearing_point_ids(scan, gold_claims)
    return {
        "answer_bearing_claim_present": int(bool(bearing)),
        "typed_relation_present": int(metric8_present(scan, bearing)),
        "gold_provenance_present": int(metric9_present(scan, answer_session_ids)),
        "answer_bearing_point_ids": sorted(bearing),
    }


# ── live graph scan (the only DB-touching helper in the metrics layer) ────

_POINTS_CYPHER = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
    "RETURN n.id, n.content, n.session_id"
)
_TYPED_ENDPOINT_CYPHER = (
    "MATCH (o:Point {is_operator:true})-[:IMPL|NAND]->(c:Point) "
    "RETURN DISTINCT c.id"
)
_SUPERSESSION_CYPHER = (
    "MATCH (a:Point)-[:CORRECTS]->(b:Point) RETURN a.id, b.id"
)


def scan_eval_graph(sdk: Any) -> GraphScan:
    """Read every non-operator Point + every typed-relation endpoint.

    The SDK must already be scoped to the question's namespace (the driver's
    fresh-per-question SDK). This reads only content and provenance — no gold
    field, no artifact.
    """
    graph = sdk._get_proj().g
    points: list[GraphPoint] = []
    for row in graph.query(_POINTS_CYPHER).result_set or ():
        pid = row[0] if len(row) > 0 else None
        if pid is None:
            continue
        content = row[1] if len(row) > 1 else ""
        session_id = row[2] if len(row) > 2 else None
        points.append(
            GraphPoint(
                point_id=str(pid),
                content="" if content is None else str(content),
                session_id=None if session_id is None else str(session_id),
            )
        )
    endpoints: set[str] = set()
    for row in graph.query(_TYPED_ENDPOINT_CYPHER).result_set or ():
        if row and row[0] is not None:
            endpoints.add(str(row[0]))
    for row in graph.query(_SUPERSESSION_CYPHER).result_set or ():
        for cell in row:
            if cell is not None:
                endpoints.add(str(cell))
    return GraphScan(points=tuple(points),
                     typed_relation_endpoint_ids=frozenset(endpoints))


# ── aggregate metrics ─────────────────────────────────────────────────────


def word_stats(words: Sequence[int]) -> dict[str, Any]:
    """Metric 2 — mean, median, IQR, max and distinct-value count (§4)."""
    values = [int(w) for w in words]
    if not values:
        return {"n": 0, "mean": None, "median": None, "iqr": None,
                "q1": None, "q3": None, "max": None, "distinct": 0}
    q1 = q3 = float(values[0])
    if len(values) >= 2:
        quartiles = statistics.quantiles(values, n=4, method="inclusive")
        q1, q3 = float(quartiles[0]), float(quartiles[2])
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": round(statistics.median(values), 1),
        "iqr": round(q3 - q1, 1),
        "q1": round(q1, 1),
        "q3": round(q3, 1),
        "max": max(values),
        "distinct": len(set(values)),
    }


def per_class_accuracy(
    outcomes: Sequence[Mapping[str, Any]],
    qid_to_class: Mapping[str, str],
) -> dict[str, Any]:
    """Metric 4 — per temporal class correct/n (§4).

    ``current-state`` (n=1) is reported for completeness and flagged
    ``excluded_from_inference``: never used for per-class inference.
    """
    out: dict[str, Any] = {}
    for cls in ANALYSIS_CLASSES:
        rows = [o for o in outcomes
                if qid_to_class.get(str(o.get("question_id"))) == cls]
        graded = [o for o in rows if isinstance(o.get("label"), bool)]
        correct = sum(1 for o in graded if o["label"] is True)
        out[cls] = {
            "n": len(rows),
            "graded": len(graded),
            "correct": correct,
            "accuracy": round(correct / len(graded), 4) if graded else None,
            "excluded_from_inference": cls == "current-state",
        }
    return out


def arm_metrics(
    outcomes: Sequence[Mapping[str, Any]],
    qid_to_class: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Per-arm secondary metrics 1/2/3/4/6/7 and the ``zero_seed`` rate (§4)."""
    graded = [o for o in outcomes if isinstance(o.get("label"), bool)]
    refusals = sum(1 for o in graded if o.get("reader_refusal") is True)
    correct = sum(1 for o in graded if o["label"] is True)
    # The primary is answerable-correct among the 52 (spec §4).
    answerable = [o for o in graded if _is_answerable(str(o.get("question_id")))]
    correct_answerable = sum(1 for o in answerable if o["label"] is True)
    words = [int(o.get("context_words") or 0) for o in outcomes]
    total_words = sum(words)
    zero_seed_flags = [o.get("zero_seed") for o in outcomes
                       if o.get("zero_seed") is not None]

    metrics: dict[str, Any] = {
        # metric 1
        "refusal_rate": round(refusals / len(graded), 4) if graded else None,
        "graded": len(graded),
        "correct": correct,
        "correct_answerable": correct_answerable,
        "answerable_n": len(answerable),
        # metric 2
        "context_words": word_stats(words),
        # metric 3 (answerable-correct per 1,000 context words)
        "correct_per_1k_words": (round(1000 * correct_answerable / total_words, 3)
                                 if total_words else None),
        # metric 4
        "per_class": (per_class_accuracy(outcomes, qid_to_class)
                      if qid_to_class else None),
        # zero_seed (B/C only)
        "zero_seed_rate": (
            round(sum(1 for f in zero_seed_flags if f) / len(zero_seed_flags), 4)
            if zero_seed_flags else None),
        "zero_seed_n": len(zero_seed_flags),
    }
    # metric 6 — subgraph size (the B/C arms only).
    if any(o.get("claims_admitted") is not None for o in outcomes):
        metrics["subgraph_size"] = {
            "mean_claims_admitted_per_question": round(
                statistics.fmean([int(o.get("claims_admitted") or 0)
                                  for o in outcomes]), 2) if outcomes else None,
            "mean_claims_rendered_per_question": round(
                statistics.fmean([int(o.get("claims_rendered") or 0)
                                  for o in outcomes]), 2) if outcomes else None,
            "mean_relations_rendered_per_question": round(
                statistics.fmean([int(o.get("relations_rendered") or 0)
                                  for o in outcomes]), 2) if outcomes else None,
            "total_claims_admitted": sum(int(o.get("claims_admitted") or 0)
                                         for o in outcomes),
            "total_claims_rendered": sum(int(o.get("claims_rendered") or 0)
                                         for o in outcomes),
            "total_relations_rendered": sum(
                int(o.get("relations_rendered") or 0) for o in outcomes),
        }
    return metrics


def graph_metric_rates(
    per_question: Mapping[str, Mapping[str, Any]],
    answerable_qids: Iterable[str],
) -> dict[str, Any]:
    """Aggregate metrics 5/8/9 + the §9.4 conjunction over the 52 (§4)."""
    qids = [q for q in answerable_qids]
    n = len(qids)
    if n == 0:
        return {"n": 0, "metric5_rate": None, "metric8_rate": None,
                "metric9_rate": None, "conjunction_rate": None}

    def _rate(key: str) -> float:
        return round(sum(int(per_question.get(q, {}).get(key, 0))
                         for q in qids) / n, 4)

    conjunction = sum(
        1 for q in qids
        if per_question.get(q, {}).get("answer_bearing_claim_present")
        and per_question.get(q, {}).get("typed_relation_present")
    )
    return {
        "n": n,
        "metric5_rate": _rate("answer_bearing_claim_present"),
        "metric8_rate": _rate("typed_relation_present"),
        "metric9_rate": _rate("gold_provenance_present"),
        "conjunction_rate": round(conjunction / n, 4),
    }


# ── report building ───────────────────────────────────────────────────────


def build_methodology(
    *,
    arm: str,
    context_source: str,
    reader: Any,
    judge: Any,
    max_words: int,
    gold_artifact_sha: str | None,
    ep_engine: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The per-arm methodology block (must carry the three constancy keys)."""
    methodology: dict[str, Any] = {
        # §5 constancy keys — non-empty and identical across arms.
        "reader_model_spec": getattr(reader, "model_spec", None)
        or getattr(reader, "model_id", "") or "unknown",
        "reader_prompt_hash": reader_prompt_hash(),
        "judge_model": getattr(judge, "model_spec", None)
        or getattr(judge, "model_id", "") or "unknown",
        # provenance
        "reader_model": getattr(reader, "model_id", "") or "unknown",
        "reader_provider": getattr(reader, "provider", None),
        "reader_pinned": getattr(reader, "pinned", None),
        "judge_rubric_id_hash": _sha16("longmemeval-official"),
        "arm": arm,
        "context_source": context_source,
        "max_context_tokens": max_words,
        "gold_evidence_artifact_sha256": gold_artifact_sha,
        "stopwords_sha256": stopwords_hash(),
    }
    if ep_engine is not None:
        methodology["ep_engine"] = ep_engine
    if extra:
        methodology.update(dict(extra))
    return methodology


def build_report(
    *,
    arm: str,
    outcomes: Sequence[Mapping[str, Any]],
    methodology: Mapping[str, Any],
    metrics: Mapping[str, Any] | None = None,
    integrity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The per-arm report — the existing lane's exact top-level shape."""
    return {
        "arm": arm,
        "methodology": dict(methodology),
        "n_outcomes": len(outcomes),
        "outcomes": [dict(o) for o in outcomes],
        "metrics": dict(metrics or {}),
        "integrity": dict(integrity or {}),
    }


def write_report(report: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{report['arm']}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


# ── retry helper (thin; the reader/judge surfaces are flaky by nature) ────


def _call_with_backoff(
    fn: Callable[[], Any],
    *,
    what: str,
    retries: int = 2,
    base: float = 2.0,
    cap: float = 30.0,
) -> Any:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt >= retries:
                break
            delay = min(base * (2 ** attempt), cap)
            logger.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs",
                           what, attempt + 1, retries + 1, exc, delay)
            time.sleep(delay)
    assert last is not None
    raise last


# ── orchestration ─────────────────────────────────────────────────────────


def _default_sdk_factory(qid: str) -> tuple[Any, str]:
    """Fresh per-question SDK scoped to the eval graph namespace (§5).

    The namespace mirrors the #2578 run's convention
    (``question_graph_namespace``): the env ``TORTOISE_LME_NAMESPACE_MODEL``
    (default ``default``) and ``TORTOISE_LME_QUERY_PROMPT`` are recorded so a
    boundary difference between runs is visible.
    """
    from tools.longmem_eval.run import question_graph_namespace
    from tortoise.sdk import TortoiseSDK

    model = os.environ.get("TORTOISE_LME_NAMESPACE_MODEL", "default")
    prompt = os.environ.get("TORTOISE_LME_QUERY_PROMPT") or None
    namespace = question_graph_namespace(model, prompt, qid)
    return TortoiseSDK(namespace=namespace), namespace


class MockEvidenceReader:
    """Offline mock reader for the pre-rendered lane (``--mock``).

    Mirrors the eval ``MockReader`` posture (deterministic, no keys): the
    hypothesis is the context text itself, so the judge scores retrieval
    quality rather than model capability.
    """

    model_id = "mock-reader"
    model_spec = "mock-reader"
    provider = "mock"
    pinned = None

    def answer_with_evidence(self, *, evidence: str, question: str,
                             question_date: str | None = None,
                             question_type: str | None = None) -> str:
        del question, question_date, question_type
        return (evidence or "").strip()

    def answer(self, *, context_hits: list[dict], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str:
        return self.answer_with_evidence(
            evidence=render_context(context_hits, question_date=question_date),
            question=question, question_date=question_date,
            question_type=question_type)

    def ping(self, probe: str) -> str:
        del probe
        return "mock ping ok"


def load_census_classes(path: str | Path = CENSUS_DEFAULT) -> dict[str, str]:
    """``qid → class`` for the #2578 analysis subset (metric 4)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["rows"]
    return {str(r["qid"]): str(r["cls"]) for r in rows
            if r.get("cls") in ANALYSIS_CLASSES}


def _is_answerable(qid: str) -> bool:
    return not str(qid).endswith("_abs")


def run_experiment(
    instances: Sequence[Mapping[str, Any]],
    *,
    reader: Any,
    judge: Any,
    sdk_factory: Callable[[str], tuple[Any, str]] | None = None,
    arms: Sequence[str] = ARMS,
    gold_claims: Mapping[str, list[dict]] | None = None,
    qid_to_class: Mapping[str, str] | None = None,
    limit: int | None = None,
    max_words: int = DEFAULT_MAX_WORDS,
    scan_fn: Callable[[Any], GraphScan] | None = None,
    ep_fn: Any = _EP_UNSET,
    max_retries: int = 2,
    gold_artifact_sha: str | None = None,
    method_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the four arms over ``instances`` and return per-arm reports.

    Seams (all injectable for hermetic tests): ``sdk_factory``,
    ``scan_fn``, ``ep_fn``. The metrics layer (5/8/9) runs **after** the
    context slot is rendered. The B/C render path never receives gold data;
    arm A is the gold-verbatim oracle ceiling by construction (spec §3) and
    its gold read is confined to ``build_context_arm``'s arm-A branch.
    """
    if sdk_factory is None:
        sdk_factory = _default_sdk_factory
    if scan_fn is None:
        scan_fn = scan_eval_graph
    activation = activate_beliefs if ep_fn is _EP_UNSET else ep_fn
    gold_by_qid: Mapping[str, list[dict]] = gold_claims or {}
    rows = list(instances)
    if limit is not None:
        rows = rows[:limit]

    per_arm: dict[str, list[dict]] = {arm: [] for arm in arms}
    per_question_graph_metrics: dict[str, dict[str, Any]] = {}
    ep_summary: dict[str, Any] = {"ran": 0, "failed": 0, "engine": None}
    #: §3 reserved-overflow flag, per question → arm → dropped reserved lines.
    reserved_overflow: dict[str, dict[str, int]] = {}
    #: §3 seed policy provenance, per question → arm → {seed_fn, seed_ids}.
    seed_selection: dict[str, dict[str, dict[str, Any]]] = {}

    for row in rows:
        qctx = load_question_context(row)
        sdk, namespace = sdk_factory(qctx.qid)

        ep_manifest: dict[str, Any] | None = None
        if activation is not None and any(a in ("B", "C") for a in arms):
            ep_manifest = _call_with_backoff(
                lambda _sdk=sdk, _ns=namespace: activation(_sdk,
                                                          namespace=_ns),
                what=f"activate_beliefs for {qctx.qid}", retries=max_retries)
            if ep_manifest.get("ep_ran"):
                ep_summary["ran"] += 1
                ep_summary["engine"] = ep_manifest.get("ep_engine")
            else:
                ep_summary["failed"] += 1

        # ── render path: every arm's context slot (no gold data) ──────────
        contexts: dict[str, ArmContext] = {}
        for arm in arms:
            contexts[arm] = build_context_arm(
                arm, qctx, sdk, namespace=namespace, max_words=max_words)

        # ── metrics layer: graph scan + gold artifact (outside render) ────
        try:
            scan = scan_fn(sdk)
        except Exception as exc:
            # silently fabricate a metric; record and re-raise for the run.
            logger.error("graph scan failed for %s: %s", qctx.qid, exc)
            raise
        gm = graph_metrics(
            scan, gold_by_qid.get(qctx.qid, []), qctx.answer_session_ids)
        per_question_graph_metrics[qctx.qid] = gm

        for arm in arms:
            ctx = contexts[arm]
            hypothesis = _call_with_backoff(
                lambda _c=ctx, _q=qctx: reader.answer_with_evidence(
                    evidence=_c.text, question=_q.question_text,
                    question_date=_q.question_date,
                    question_type=_q.question_type or None),
                what=f"reader({arm}) for {qctx.qid}", retries=max_retries)
            label = _call_with_backoff(
                lambda _h=hypothesis, _q=qctx: judge.judge(
                    question_type=_q.question_type or "",
                    question=_q.question_text,
                    answer=_q.answer,
                    hypothesis=_h,
                    abstention=is_abstention(_q.qid)),
                what=f"judge({arm}) for {qctx.qid}", retries=max_retries)
            outcome: dict[str, Any] = {
                "question_id": qctx.qid,
                "question_type": qctx.question_type,
                "question_date": qctx.question_date or "",
                "label": bool(label),
                "hypothesis": hypothesis,
                # shape parity with the existing lane (rebuild consumer).
                "context_tokens": ctx.budget_words,
                # metric-2 unit (true whitespace words).
                "context_words": ctx.word_count,
                "context_source": ctx.context_source,
                "namespace": namespace,
                "zero_seed": ctx.zero_seed,
                "seed_fn": ctx.seed_fn,
                "claims_admitted": (ctx.claims_admitted
                                     if arm in ("B", "C") else None),
                "claims_rendered": (ctx.claims_rendered
                                    if arm in ("B", "C") else None),
                "relations_rendered": (ctx.relations_rendered
                                       if arm in ("B", "C") else None),
                "reserved_overflow": ctx.reserved_overflow,
                "seed_ids": list(ctx.seed_ids),
                "reader_refusal": bool(_looks_abstained(hypothesis)),
                "measure_facts": {
                    "reader_refusal": bool(_looks_abstained(hypothesis)),
                    "gold_admitted_ids": [],
                    "pool_depth": {},
                },
                "metrics": gm,
                "ep": ep_manifest,
            }
            # Aggregate the §3 provenance the manifest must carry: a question
            # whose reserved claims alone exceed the budget is flagged with the
            # count of dropped reserved lines; the eight selected seed ids are
            # recorded in rank order with the seed function per question.
            if ctx.reserved_overflow:
                reserved_overflow.setdefault(qctx.qid, {})[arm] = (
                    ctx.reserved_overflow)
            if ctx.seed_ids or (arm in ("B", "C") and ctx.seed_fn):
                seed_selection.setdefault(qctx.qid, {})[arm] = {
                    "seed_fn": ctx.seed_fn,
                    "seed_ids": list(ctx.seed_ids),
                }
            per_arm[arm].append(outcome)

    # ── methodology + reports ─────────────────────────────────────────────
    gold_sha: str | None = gold_artifact_sha
    if (gold_sha is None and gold_claims is None
            and Path(DEFAULT_GOLD_ARTIFACT).exists()):
        from tools.longmem_eval.gold_evidence_claims import artifact_sha256
        gold_sha = artifact_sha256(DEFAULT_GOLD_ARTIFACT)

    methodologies = {
        arm: build_methodology(
            arm=arm,
            context_source=", ".join(
                sorted({o["context_source"] for o in per_arm[arm]})) or "unknown",
            reader=reader, judge=judge, max_words=max_words,
            gold_artifact_sha=gold_sha, ep_engine=ep_summary.get("engine"),
            extra=method_extra)
        for arm in arms
    }
    assert_reader_constancy(methodologies)

    graph_rates = graph_metric_rates(
        per_question_graph_metrics,
        [q for q in per_question_graph_metrics if _is_answerable(q)])

    d_floor = None
    if "D" in per_arm:
        d_floor = {
            "correct": sum(1 for o in per_arm["D"]
                           if o["label"] is True and _is_answerable(o["question_id"])),
            "n": sum(1 for o in per_arm["D"] if _is_answerable(o["question_id"])),
        }

    reports: dict[str, dict] = {}
    for arm in arms:
        metrics = arm_metrics(per_arm[arm], qid_to_class)
        metrics["graph"] = graph_rates
        if d_floor is not None:
            metrics["arm_d_floor"] = d_floor
        integrity = {
            "n_outcomes": len(per_arm[arm]),
            "n_answerable": sum(1 for o in per_arm[arm]
                                if _is_answerable(o["question_id"])),
            "reader_constancy": "pass",
            "graph_metric_denominator": graph_rates["n"],
        }
        reports[arm] = build_report(
            arm=arm, outcomes=per_arm[arm], methodology=methodologies[arm],
            metrics=metrics, integrity=integrity)

    return {
        "reports": reports,
        "methodologies": methodologies,
        "graph_metrics": graph_rates,
        "per_question_graph_metrics": per_question_graph_metrics,
        "ep_summary": ep_summary,
        "reserved_overflow": reserved_overflow,
        "seed_selection": seed_selection,
    }


def assert_reader_constancy(arms_meta: Mapping[str, Mapping[str, Any]]) -> None:
    """Delegate to the #2578 pre-registered constancy check (§5)."""
    from tools.longmem_eval.measure_temporal import assert_reader_constancy as _check
    _check({arm: dict(meta) for arm, meta in arms_meta.items()})


# ── run manifest ──────────────────────────────────────────────────────────


def repo_git_sha() -> str | None:
    """``git rev-parse HEAD`` captured at run time (§3 "Frozen artifact").

    The serializer's git sha is part of the manifest, so a silent re-render
    after a serializer change is detectable. Returns ``None`` when git is
    unavailable (a non-repo export) rather than inventing a value.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True, timeout=10)
    except Exception:  # pragma: no cover - env without git
        return None
    sha = proc.stdout.strip()
    return sha or None


def embedder_model_id() -> str:
    """The embedder identity ``vector_search`` seeds with (§3 seed policy)."""
    from tortoise.embeddings import EMBEDDING_MODEL
    return str(EMBEDDING_MODEL)


def _seed_selection_manifest(
    seed_selection: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
    embedder: str | None,
) -> dict[str, Any]:
    """Normalize the per-question seed provenance recorded by the run (§3).

    Records the **eight selected seed ids in rank order** plus the seed
    function per question, the fetch width/normalization constants, and the
    embedder model id — so a boundary difference between two runs is visible.
    ``mixed_seed_functions`` flags a run that mixed vector and BM25 seeding
    across arms, which §3 forbids.
    """
    per_question = {
        str(qid): {
            str(arm): {
                "seed_fn": entry.get("seed_fn"),
                "seed_ids": [str(s) for s in (entry.get("seed_ids") or ())],
            }
            for arm, entry in per_arm.items()
        }
        for qid, per_arm in (seed_selection or {}).items()
    }
    seed_fns = sorted({
        str(entry["seed_fn"]) for per_arm in per_question.values()
        for entry in per_arm.values() if entry.get("seed_fn")
    })
    return {
        "seed_fn": (seed_fns[0] if len(seed_fns) == 1
                    else (seed_fns or None)),
        "mixed_seed_functions": len(seed_fns) > 1,
        "seed_limit": SEED_LIMIT,
        "seed_count": SEED_COUNT,
        "embedder_model_id": (embedder if embedder is not None
                              else embedder_model_id()),
        "per_question": per_question,
    }


def _reserved_overflow_manifest(
    per_question: Mapping[str, Mapping[str, int]] | None,
) -> dict[str, Any]:
    """The §3 ``reserved_overflow`` block: per question + the dropped count.

    A question whose reserved claims alone exceed the §5 budget is flagged
    here with the count of dropped reserved lines.
    """
    normalized = {
        str(qid): {str(arm): int(count) for arm, count in per_arm.items()}
        for qid, per_arm in (per_question or {}).items()
    }
    total = sum(count for per_arm in normalized.values()
                for count in per_arm.values())
    return {
        "unit": "dropped reserved relation lines (§3)",
        "flagged_question_ids": sorted(normalized),
        "n_flagged_questions": len(normalized),
        "total_dropped_reserved_lines": total,
        "per_question": normalized,
    }


def build_manifest(
    *, arms: Sequence[str], gold_path: str, gold_sha: str | None,
    max_words: int, reader: Any, judge: Any,
    seed_selection: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    reserved_overflow: Mapping[str, Mapping[str, int]] | None = None,
    embedder_model: str | None = None,
    serializer_path: str = SERIALIZER_MODULE_PATH,
    serializer_git_sha: str | None = None,
    session_index_permutation_seed: int = SESSION_INDEX_PERMUTATION_SEED,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The run manifest: provenance + every resolved ambiguity (spec §3/§5/§9).

    Provenance the spec requires beyond the artifact hashes:

    * ``seed_selection`` — the eight selected seed ids in rank order, the seed
      function, and the embedder model id (§3 seed policy);
    * ``serializer_module_path`` + ``serializer_git_sha`` — the frozen
      serializer and the commit it was rendered from (§3 Frozen artifact);
    * ``session_index_permutation_seed`` — the §10.1 perturbation seed;
    * ``reserved_overflow`` — per question, the dropped reserved line count.
    """
    manifest: dict[str, Any] = {
        "schema": "context-assembly-run-manifest/v1",
        "issue": "3011",
        "spec": "docs/experiments/2026-09-11-abc-context-assembly-experiment.md",
        "arms": list(arms),
        "reader_model_spec": getattr(reader, "model_spec", None)
        or getattr(reader, "model_id", ""),
        "reader_prompt_hash": reader_prompt_hash(),
        "reader_prompt_sha256": reader_prompt_sha256(),
        "reader_prompt_source": (
            "tortoise/reader.py:build_reader_user_message + "
            "system_prompt_for + reader_prompt_constants, consumed via "
            "LLMReader.answer_with_evidence"),
        "judge_model": getattr(judge, "model_spec", None)
        or getattr(judge, "model_id", ""),
        "max_context_tokens": max_words,
        "max_context_unit": "whitespace words via int(len(text.split()) * 1.1)",
        "gold_evidence_artifact_path": gold_path,
        "gold_evidence_artifact_sha256": gold_sha,
        "stopwords": "tortoise.sparse.SPARSE_STOPWORDS",
        "stopwords_sha256": stopwords_hash(),
        "gold_evidence_tokenizer": "tools.longmem_eval.gold_evidence_claims.tokenize",
        "gold_evidence_min_tokens": 3,
        # §3 Frozen artifact — the serializer module path + the commit it ran at.
        "serializer_module_path": serializer_path,
        "serializer_git_sha": (serializer_git_sha if serializer_git_sha is not None
                               else repo_git_sha()),
        "serializer_engine_path": "tortoise/subgraph.py",
        # §10.1 — the one fixed permutation applied to lme_session_index.
        "session_index_permutation_seed": int(session_index_permutation_seed),
        # §3 seed policy provenance + the §3 reserved-overflow flag.
        "seed_selection": _seed_selection_manifest(seed_selection,
                                                   embedder_model),
        "reserved_overflow": _reserved_overflow_manifest(reserved_overflow),
        "resolved_ambiguities": {
            "arm_a_source": (
                "Spec §3 wins: arm A is the GOLD-verbatim oracle ceiling — "
                "the question's answer_session_ids resolved positionally "
                "against haystack_session_ids, those sessions taken from "
                "haystack_sessions and rendered verbatim through "
                "render_context (the frozen Current Date: header preserved). "
                "It is NOT a retrieval arm: every F/H statistic is relative "
                "to it and §7 F3's A_ref = 42/52 is a property of the gold "
                "render only. This is the sole legitimate gold read in the "
                "driver, confined to build_context_arm's arm-A branch, which "
                "leakage_guard excludes from the §10.2 static assertion."),
            "date_header_for_b_c": (
                "render_arm_b/render_arm_c emit no 'Current Date:' header; "
                "render_context embeds it for A/D. Because §5 makes the "
                "header part of the byte-identical scaffolding and TR "
                "questions need it, the driver prepends the identical header "
                "to B/C (see _with_date_header)."),
            "metric5_denominator": (
                "|tokens(g)| is the cardinality of the content-token SET "
                "(spec §4: 'the resulting content-token sets are compared'); "
                "trivial claims never match."),
            "metric2_unit": (
                "context words = len(text.split()); context_tokens = "
                "int(len*1.1) is the §5 budget unit (both recorded)."),
            "arm_c_turn_resolution": (
                "A claim's verbatim source turn is resolved from its stored "
                "source_turn_id (extracted claims) or its own turn-node id, "
                "against the question's frozen haystack_sessions — never a "
                "gold field."),
            "answerable_subset": (
                "52 = the census ANALYSIS_CLASSES subset minus the '_abs' "
                "abstention controls; metrics 5/8/9 and metric 4 use it."),
            "arm_ids_and_rebuild": (
                "Reports carry the task-mandated arm ids A/B/C/D and the "
                "existing lane's report SHAPE. measure_temporal."
                "rebuild_from_reports additionally validates arm ids against "
                "the #2578 ARM_TABLE via gate_output, so it cannot compare "
                "these new arms until they are registered; the loader, "
                "methodology-constancy and classify_outcome paths are "
                "shape-compatible."),
            "ep_activation": (
                "Draft points are promoted and EP is run once per question "
                "before B/C render, so confidence is measured (never a fake "
                "0.50); unmeasured points render 'confidence: unmeasured'."),
            "null_source_turns": (
                "A point whose source_turn_id/session_id/turn id does not "
                "resolve renders the frozen fallbacks ('session ?', 'turn ?', "
                "'(date unknown)') and contributes no arm-C turn."),
        },
    }
    if extra:
        manifest["extra"] = dict(extra)
    return manifest


# ── CLI ───────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context_assembly_arms",
        description="Run the #3011 A/B/C/D context-assembly arms + metrics.")
    parser.add_argument("--instances", required=True,
                        help="LongMemEval instances JSON (a list of rows)")
    parser.add_argument("--work-dir", required=True,
                        help="run working directory (manifests/scratch)")
    parser.add_argument("--out-dir", required=True,
                        help="directory for one <arm>.json report per arm")
    parser.add_argument("--arm", choices=list(ARMS), default=None,
                        help="run a single arm (default: all four)")
    parser.add_argument("--limit", type=int, default=None,
                        help="process only the first N instances")
    parser.add_argument("--mock", action="store_true",
                        help="offline mock reader + mock judge")
    parser.add_argument("--gold-artifact", default=DEFAULT_GOLD_ARTIFACT,
                        help="gold-evidence claim artifact path")
    parser.add_argument("--census", default=CENSUS_DEFAULT,
                        help="qid → class census for metric 4")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    rows = json.loads(Path(args.instances).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("error: --instances must be a JSON list", file=sys.stderr)
        return 2

    arms = (args.arm,) if args.arm else ARMS
    gold_path = Path(args.gold_artifact)
    if not gold_path.exists():
        print(f"error: gold-evidence artifact not found: {gold_path} "
              "(§9.5 — build it before the first render)", file=sys.stderr)
        return 2
    gold_claims = load_gold_claims(gold_path)
    gold_sha = gold_artifact_sha256(gold_path)

    try:
        qid_to_class = load_census_classes(args.census)
    except Exception as exc:
        logger.warning("census unavailable (%s) — metric 4 omitted", exc)
        qid_to_class = None

    reader = build_reader(mock=args.mock)
    judge = build_judge(mock=args.mock)
    if args.mock:
        reader = MockEvidenceReader()

    out_dir = Path(args.out_dir)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    result = run_experiment(
        rows, reader=reader, judge=judge, arms=arms,
        gold_claims=gold_claims, qid_to_class=qid_to_class,
        limit=args.limit, gold_artifact_sha=gold_sha)

    for arm in arms:
        path = write_report(result["reports"][arm], out_dir)
        print(f"wrote {path}")

    manifest = build_manifest(
        arms=arms, gold_path=str(gold_path), gold_sha=gold_sha,
        max_words=DEFAULT_MAX_WORDS, reader=reader, judge=judge,
        seed_selection=result["seed_selection"],
        reserved_overflow=result["reserved_overflow"],
        extra={"graph_metrics": result["graph_metrics"],
               "ep_summary": result["ep_summary"]})
    manifest_path = work_dir / "context-assembly-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover — thin CLI shim
    raise SystemExit(main())
