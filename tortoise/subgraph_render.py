"""#3011 Track B — frozen serializer + union packer for arms B and C.

Renders a Track A :class:`~tortoise.subgraph.Subgraph` into the exact text
form frozen by ``docs/experiments/2026-09-11-abc-context-assembly-experiment.md``
§3 ("Serializer template", "Render order within a block", "Rendered session
number/turn ordinal/date", "Rendered confidence", the worked examples) and
budgeted by §5 (``max_context_tokens = 8000``, enforced in whitespace words).
Where the spec and the plan disagree, the spec wins.

One block per **anchor** (a selected seed), in ``Subgraph.anchors`` order,
each rendered as:

1. ``C1: <claim text>`` — the claim line.
2. Its **reserved** supersession/validity and NAND relation lines, at the
   head of the block's relation lines, immediately beneath the claim line.
3. Its remaining relation lines in admission-priority order
   (``aboutObject`` → ``supersession`` → ``NAND`` → ``IMPL``).
4. Its provenance line: ``C1 came from session 12 (2023-05-06), turn 4``.
5. (Arm C only) the verbatim source turns, each on its own two-space-indented
   line immediately beneath the provenance line.
6. ``confidence: 0.82`` — the block's last line.

Labels are assigned deterministically: anchors first (in ``anchors`` order),
then admitted candidates (in ``candidates`` order), then any relation
endpoint not yet labelled (defensive; Track A already drops dangling
relations). A relation line is attributed to an anchor block when the anchor
is one of its claim endpoints.

Frozen fallbacks: unresolvable session → ``session ?``; unresolvable turn →
``turn ?``; missing/sentinel/unparseable date → ``(date unknown)``. Zero
seeds → the literal sentinel ``[no context retrieved]``.

Confidence is read through ``tools.longmem_eval.ep_activation.read_confidence``
and renders ``confidence: unmeasured`` when the claim carries no EP state —
never a fabricated ``0.50`` (#2598).

Leakage rule (hard, §3/§10): the render path reads the point's stored
``createdAt`` / ``validFrom`` / ``session_id`` / ``source_turn_id`` and its
EP posteriors. It never reads the ingest session index property.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tools.longmem_eval.ep_activation import read_confidence
from tortoise.subgraph import EDGE_PRIORITY_WEIGHT, Subgraph

__all__ = [
    "EMPTY_CONTEXT_SENTINEL",
    "RenderResult",
    "render_arm_b",
    "render_arm_c",
]

#: Literal context slot rendered when a question produces zero seeds (§3).
EMPTY_CONTEXT_SENTINEL = "[no context retrieved]"

#: Default word budget — mirrors ``max_context_tokens`` (§5).
DEFAULT_MAX_WORDS = 8000

#: The pre-registered word→budget conversion factor (§5).
_WORD_TOKEN_FACTOR = 1.1

#: The ingest undated sentinel (``tools/longmem_eval/ingest.py``). Rendering
#: it as a date would fabricate a 1970 provenance, so it renders
#: ``(date unknown)``.
_UNDATED_SENTINEL = "1970-01-01T00:00:00Z"

#: Turn node id suffix, e.g. ``lme:<qid>:s<si>:t<ti>``; ``<ti>`` is 0-based.
_TURN_SUFFIX_RE = re.compile(r":t(\d+)$")

#: Leading calendar date in either the ingest producer's real format
#: (``YYYY/MM/DD``, e.g. ``2023/05/20 (Sat) 03:29``) or ISO 8601
#: (``YYYY-MM-DD``). Both are normalised to ``YYYY-MM-DD``.
_CALENDAR_DATE_RE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})")

#: Reserved edge classes — spec §3 admits these ahead of the per-anchor cap
#: and places them at the head of the anchor's rendered block.
_RESERVED_RELATIONS = frozenset({"supersession", "NAND"})

#: Edge-class wording (spec §3.1): IMPL → IMPLIES, NAND → CONTRADICTS.
_RELATION_VERB = {"IMPL": "IMPLIES", "NAND": "CONTRADICTS"}


@dataclass(frozen=True)
class RenderResult:
    """Rendered context text plus the §4 metric-6 counters.

    ``word_count`` is ``int(len(text.split()) * 1.1)`` (§5 — the budget unit).
    ``reserved_overflow`` is the count of reserved relation lines dropped by
    budget truncation (0 when nothing was dropped); the retained text is
    always a whole-line prefix of the untruncated render.
    """

    text: str
    word_count: int
    claims_rendered: int
    relations_rendered: int
    reserved_overflow: int
    zero_seed: bool


@dataclass(frozen=True)
class _Line:
    """One rendered line, tagged for post-truncation counting."""

    text: str
    kind: str  # claim | reserved | relation | provenance | turn | confidence


# ── per-field resolution ─────────────────────────────────────────────────


def _budget_words(text: str) -> int:
    """The §5 budget unit: ``int(len(text.split()) * 1.1)``."""
    return int(len(text.split()) * _WORD_TOKEN_FACTOR)


def _session_ordinal(session_id: Any, haystack_session_ids: Sequence[str]) -> int | None:
    """1-based position of ``session_id`` in the frozen haystack list (§3).

    The rendered session number is **not** the ingest session index property.
    ``None`` (→ ``session ?``) when the id is absent or not in the list.
    """
    if session_id is None:
        return None
    target = str(session_id)
    for position, sid in enumerate(haystack_session_ids or ()):
        if str(sid) == target:
            return position + 1
    return None


def _turn_suffix_ordinal(value: Any) -> int | None:
    """Parse a ``…:t<ti>`` turn id; ``ti`` is 0-based → return ``ti + 1``."""
    if value is None:
        return None
    match = _TURN_SUFFIX_RE.search(str(value))
    if match is None:
        return None
    return int(match.group(1)) + 1


def _turn_ordinal(point_id: Any, props: Mapping[str, Any]) -> int | None:
    """1-based turn ordinal from the point's own id, else ``source_turn_id``.

    A point whose id and ``source_turn_id`` both fail to resolve to a
    ``…:t<ti>`` suffix renders ``turn ?``.
    """
    ordinal = _turn_suffix_ordinal(point_id)
    if ordinal is not None:
        return ordinal
    return _turn_suffix_ordinal(props.get("source_turn_id"))


def _parse_date(value: Any) -> str | None:
    """Return the ``YYYY-MM-DD`` calendar date, or ``None`` when unparseable.

    Accepts the ingest producer's real ``createdAt`` format
    (``2023/05/20 (Sat) 03:29`` → ``2023-05-20``, #3011) alongside ISO 8601
    (``2023-05-20`` or ``2023-05-20T00:00:00Z`` → ``2023-05-20``). The
    undated sentinel, a missing value and an unparseable value are all
    ``None`` → the caller renders ``(date unknown)``; no date is ever
    fabricated.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == _UNDATED_SENTINEL:
        return None
    match = _CALENDAR_DATE_RE.match(text)
    if match is None:
        return None
    year, month, day = match.groups()
    return f"{year}-{month}-{day}"


def _render_date(props: Mapping[str, Any]) -> str:
    """``validFrom`` → ``createdAt`` → ``(date unknown)`` (spec §3, frozen).

    The frozen chain is the point's own stored ``validFrom`` then
    ``createdAt``. The spec defines no session-level fallback, so a point
    carrying neither renders ``(date unknown)`` — never a fabricated date
    borrowed from the point's session.
    """
    for key in ("validFrom", "createdAt"):
        parsed = _parse_date(props.get(key))
        if parsed is not None:
            return parsed
    return "date unknown"


def _confidence_text(props: Mapping[str, Any]) -> str:
    """``unmeasured`` when the point has no EP state, else ``f"{c:.2f}"``."""
    confidence = read_confidence(dict(props))
    if confidence is None:
        return "unmeasured"
    return f"{confidence:.2f}"


# ── labels + relation wiring ─────────────────────────────────────────────


def _label_map(sg: Subgraph) -> dict[str, str]:
    """Deterministic ``point_id → C<k>`` assignment (see module docstring)."""
    labels: dict[str, str] = {}

    def assign(point_id: Any) -> None:
        key = str(point_id)
        if key not in labels:
            labels[key] = f"C{len(labels) + 1}"

    for anchor in sg.anchors:
        assign(anchor)
    for candidate in sg.candidates:
        assign(candidate.point_id)
    for relation in sg.relations:
        assign(relation.source_id)
        if relation.relation != "aboutObject":
            assign(relation.target_id)
    return labels


def _is_reserved(relation: Any) -> bool:
    return relation.relation in _RESERVED_RELATIONS


def _block_relations(sg: Subgraph, anchor_id: str) -> list[Any]:
    """Relations rendered in ``anchor_id``'s block (the anchor is an endpoint)."""
    out = []
    for relation in sg.relations:
        if relation.relation == "aboutObject":
            if str(relation.source_id) == anchor_id:
                out.append(relation)
        elif relation.relation == "supersession":
            # ``(superseder) -[:CORRECTS]-> (superseded)``. Symmetric with
            # NAND: the anchor may be EITHER endpoint, and the line always
            # names the superseded claim (the relation's target) — otherwise
            # a supersession reached from its superseder is silently dropped
            # whenever the superseded claim is a non-seed candidate.
            if anchor_id in (str(relation.source_id), str(relation.target_id)):
                out.append(relation)
        elif anchor_id in (str(relation.source_id), str(relation.target_id)):
            out.append(relation)
    return out


def _order_relations(relations: Sequence[Any]) -> list[Any]:
    """Reserved lines lead, then admission-priority order (stable within a class).

    Reserved = supersession/validity and NAND (spec §3); among the reserved the
    same priority key orders them (supersession 0.9 before NAND 0.8).
    """
    indexed = list(enumerate(relations))
    reserved = [(i, r) for i, r in indexed if _is_reserved(r)]
    others = [(i, r) for i, r in indexed if not _is_reserved(r)]

    def key(item: tuple[int, Any]) -> tuple[float, int]:
        index, relation = item
        return (-EDGE_PRIORITY_WEIGHT.get(relation.relation, 0.0), index)

    reserved.sort(key=key)
    others.sort(key=key)
    return [r for _, r in reserved] + [r for _, r in others]


def _render_relation(relation: Any, labels: Mapping[str, str]) -> str:
    """One labeled, directed relation line (spec §3 serializer template)."""
    source = labels.get(str(relation.source_id), "?")
    if relation.relation == "aboutObject":
        name = relation.target_label or "?"
        return f"{source} is about {name}"
    if relation.relation == "supersession":
        target = labels.get(str(relation.target_id), "?")
        return f"{target} [SUPERSEDED BY {source}]"
    verb = _RELATION_VERB.get(relation.relation, relation.relation)
    target = labels.get(str(relation.target_id), "?")
    return f"{source} {verb} {target}"


# ── arm C turn lines ─────────────────────────────────────────────────────


def _turn_line(turn: Any) -> str:
    """Normalize one arm-C turn entry to a ``> role: text`` line.

    ``turns_by_point`` values are pre-rendered ``> role: text`` strings (used
    verbatim) or ``{"role": …, "content": …}`` mappings (rendered here). The
    caller's two-space indent is added by :func:`_render`.
    """
    if isinstance(turn, Mapping):
        role = turn.get("role", "assistant")
        content = turn.get("content", "")
        return f"> {role}: {content}"
    return str(turn)


# ── core renderer ────────────────────────────────────────────────────────


def _resolve_points_by_id(
    sg: Subgraph, points_by_id: Mapping[str, Mapping[str, Any]] | None
) -> Mapping[str, Mapping[str, Any]]:
    """Point-property map: explicit argument, else ``sg.point_props``, else {}.

    Track A's :class:`Subgraph` carries claim text plus a ``point_props``
    channel (populated during traversal) for provenance/date/confidence. The
    caller may pass that map explicitly (``points_by_id``); when omitted it
    falls back to ``sg.point_props``, so the default path needs no wiring.
    """
    if points_by_id is not None:
        return points_by_id
    return getattr(sg, "point_props", None) or {}


def _render(
    sg: Subgraph,
    *,
    haystack_session_ids: Sequence[str],
    session_dates: Mapping[str, str] | None,
    turns_by_point: Mapping[str, Sequence[Any]] | None,
    max_words: int,
    points_by_id: Mapping[str, Mapping[str, Any]] | None,
) -> RenderResult:
    # ``session_dates`` is accepted for caller compatibility only and is never
    # consulted: the spec's frozen date chain is ``validFrom`` → ``createdAt``
    # → ``(date unknown)``, with no session-level fallback.
    del session_dates
    if sg.zero_seed or not sg.anchors:
        return RenderResult(
            text=EMPTY_CONTEXT_SENTINEL,
            word_count=_budget_words(EMPTY_CONTEXT_SENTINEL),
            claims_rendered=0,
            relations_rendered=0,
            reserved_overflow=0,
            zero_seed=True,
        )

    props_by_id = _resolve_points_by_id(sg, points_by_id)
    labels = _label_map(sg)
    lines: list[_Line] = []

    for anchor in sg.anchors:
        anchor_id = str(anchor)
        label = labels.get(anchor_id, "?")
        content = sg.content_by_id.get(anchor_id, "")
        lines.append(_Line(f"{label}: {content}", "claim"))

        for relation in _order_relations(_block_relations(sg, anchor_id)):
            kind = "reserved" if _is_reserved(relation) else "relation"
            lines.append(_Line(_render_relation(relation, labels), kind))

        props = props_by_id.get(anchor_id, {}) or {}
        session_id = props.get("session_id")
        session_number = _session_ordinal(session_id, haystack_session_ids)
        turn_number = _turn_ordinal(anchor_id, props)
        session_phrase = f"session {session_number}" if session_number is not None else "session ?"
        turn_phrase = f"turn {turn_number}" if turn_number is not None else "turn ?"
        date = _render_date(props)
        lines.append(
            _Line(f"{label} came from {session_phrase} ({date}), {turn_phrase}", "provenance")
        )

        if turns_by_point:
            for turn in turns_by_point.get(anchor_id, ()) or ():
                lines.append(_Line(f"  {_turn_line(turn)}", "turn"))

        lines.append(_Line(f"confidence: {_confidence_text(props)}", "confidence"))

    # Budget: drop whole trailing lines. Reserved lines lead their block, so
    # they are the last to go; a reserved line that still cannot fit is
    # counted in ``reserved_overflow`` rather than silently lost.
    dropped_reserved = 0
    while lines and _budget_words("\n".join(line.text for line in lines)) > max_words:
        if lines.pop().kind == "reserved":
            dropped_reserved += 1

    text = "\n".join(line.text for line in lines)
    return RenderResult(
        text=text,
        word_count=_budget_words(text),
        claims_rendered=sum(1 for line in lines if line.kind == "claim"),
        relations_rendered=sum(1 for line in lines if line.kind in ("relation", "reserved")),
        reserved_overflow=dropped_reserved,
        zero_seed=False,
    )


# ── public API ───────────────────────────────────────────────────────────


def render_arm_b(
    sg: Subgraph,
    *,
    haystack_session_ids: Sequence[str],
    session_dates: Mapping[str, str] | None = None,
    max_words: int = DEFAULT_MAX_WORDS,
    points_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> RenderResult:
    """Arm B — claim + typed relations + provenance + confidence, no turn text.

    ``haystack_session_ids`` is the question's frozen session-id list; the
    rendered session number is the 1-based position of the point's stored
    ``session_id`` in it. ``session_dates`` is accepted for caller
    compatibility only and is **ignored**: the spec's frozen date chain is
    ``validFrom`` → ``createdAt`` → ``(date unknown)``, with no session-level
    fallback. ``points_by_id`` is the graph point-property map
    (``point_id → properties``) the serializer reads for provenance, dates
    and EP confidence; when omitted, ``sg.point_props`` is used if present.
    """
    return _render(
        sg,
        haystack_session_ids=haystack_session_ids,
        session_dates=session_dates,
        turns_by_point=None,
        max_words=max_words,
        points_by_id=points_by_id,
    )


def render_arm_c(
    sg: Subgraph,
    *,
    haystack_session_ids: Sequence[str],
    session_dates: Mapping[str, str] | None = None,
    turns_by_point: Mapping[str, Sequence[Any]] | None = None,
    max_words: int = DEFAULT_MAX_WORDS,
    points_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> RenderResult:
    """Arm C — arm B plus each anchor's verbatim source turns.

    ``turns_by_point`` maps a point id to its source turns; each turn is
    either a pre-rendered ``"> role: text"`` string or a
    ``{"role": …, "content": …}`` mapping. Turn lines are inserted directly
    beneath the provenance line of the anchor they belong to and therefore
    before that block's ``confidence:`` line.
    """
    return _render(
        sg,
        haystack_session_ids=haystack_session_ids,
        session_dates=session_dates,
        turns_by_point=turns_by_point,
        max_words=max_words,
        points_by_id=points_by_id,
    )
