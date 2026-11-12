"""#9437 — the in-capture near-duplicate fold may not destroy the duplicate's evidence.

THE DEFECT THIS CLOSES, IN ONE SENTENCE
---------------------------------------
The M2 capture seam (`sdk._extract_session_llm`) resolves a repeated claim to
an earlier claim of the SAME capture and then runs

    MATCH (n:Point {id:$id}) DETACH DELETE n
    pid = resolved

with ONE guard — the operator-wiring probe (`engaged`). Nothing looks at the
node's own attachments, so the duplicate's `aboutObject` / `aboutSubject`
edges go with the node, and the surviving canonical is left carrying LESS
support than the two claims together held. The loss is silent: the receipt
reports a `dedup` verdict, which is a statement about the CLAIM, never about
what the delete took with it.

The sibling path (`extractor_v2.classify_consolidation`, guarded by PR
`#9424`) does not have this shape: a NOOP fold that would drop an entity the
candidate names is REFUSED. That guard cannot run at this seam — it compares
two payload rows and this seam has a live graph — so this module applies the
same ruling on the evidence the graph actually holds.

WHAT IS CARRIED, AND HOW (the ruling, not a preference)
------------------------------------------------------
* **Attachment links** (`aboutObject` / `aboutSubject`) — the claim's
  *support* — are UNIONED onto the canonical before the copy is removed, via
  `session_link.link_entity`, the shared journaled about*-edge writer (the
  same writer `near_dup_merge.apply_merge_union` uses for the authorised
  fusion). “UNION THE ATTACHMENTS, NEVER FUSE THE CLAIM” (§S2.2): the
  canonical's own text is untouched, and the duplicate's link is added beside
  it. ⚠️ The link's GRADING is carried only when the link is NEW on the
  canonical: `link_entity` short-circuits an edge that already exists and does
  not re-grade it (`#1370`), so a duplicate whose `confidence` differs from an
  edge the canonical already holds keeps that difference as a REPORTED
  residual — it is not silently claimed as carried.
* **Per-occurrence scalar evidence** is copied onto the canonical ONLY where
  that evidence is EMPTY. A Point holds one value per field, so a slot the
  canonical already fills keeps the canonical's value (first-writer — the
  same rule `_extract_session_v2` states for its dedup hits), and the
  difference is REPORTED as a residual instead of being silently dropped.
  Two shapes need care, and both were measured wrong before this wording:
  the occurrence ADDRESS (`quote` + `source_turn_id` + `span_start`/`span_end`)
  is ONE group — copy it whole or not at all, or the survivor presents
  turn-2's offsets as turn-1's — and `when`/`validFrom` are ONE date slot,
  so occupancy must be read from BOTH spellings.
* **A PHYSICAL DELETE IS FORECLOSED** where any of the above cannot be
  carried. `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.3's *"G5's
  destructive half"* (PR `#10069`, 2026-10-26 — ⚠️ a DIFFERENT document from
  `docs/ONTOLOGY.md` §4.3, which is the Object vocabulary) forecloses a
  physical delete to every pass, and this fold is not a pass — which is why this issue exists
  rather than being covered by `#5333`. But the delete's authority comes from
  "the row is a redundant copy"; the moment the copy holds something the
  canonical does not, it is not redundant, and the fold must keep it and say
  so. That is the `engaged` branch's shape (`kept distinct — …`), extended
  from "operator-wired" to "carrying evidence that cannot be moved".

WHY REFUSALS ARE NARROW ON PURPOSE
----------------------------------
A guard that refuses whenever the duplicate holds *anything* would silently
become "never fold": every paraphrase of a claim restated in a second turn
carries a different `quote`/`source_turn_id`, and that is the normal case.
So a conflicting scalar slot is a RESIDUAL, not a refusal — the claim is
still one claim, and the canonical keeps the occurrence it was first minted
from. A refusal is reserved for the cases where the SUPPORT ITSELF would be
lost and cannot be re-created:

* an attachment link whose target cannot be addressed by id (`unaddressable`);
* a source reference the canonical does not already carry
  (`uncarried_source_ref`) — `extractedFrom` is a node FIELD, and §4.3
  forecloses a field write on a committed row outside a lifecycle event;
* a link that does not LAND when re-written (`link_not_carried`) — counted,
  never assumed, the same rule `apply_merge_union` follows;
* evidence that cannot be READ at all (`unreadable`). Fail-closed: this
  module is about to authorise a delete, and "could not tell" is not "nothing
  there" (the distinction `extractor_v2._attachment_ids` draws with `None`);
* an address the duplicate itself only HALF holds (`malformed_address`) — a
  capture Point can carry `span_start` without `span_end` (the span validator
  guards the SDK doors, not this seam), and writing it would raise on the
  canonical and blame the wrong node forever;
* a link whose PREDICATE has no permitted writer for a Point
  (`uncarriable_attachment`) — the cascade can mint `aboutDocument`/
  `aboutPoint` on a Point, and neither is a §3.2 triple `link_entity` may
  write;
* a scalar slot write that did not LAND (`slot_write_failed`) — read back,
  because `update_point` on an absent id SUCCEEDS silently, and reporting the
  attempt there would delete a duplicate whose evidence never arrived;
* anything else that raised (`internal_error`). The public entry point never
  raises: its caller sits inside the capture's single per-loop `try`, so a
  propagated error would abandon every remaining statement's resolution, and a
  refusal is the only correct answer in front of a destructive act.

WHY A CONFLICTING SLOT IS A RESIDUAL BUT AN UNCURRIED SOURCE REF IS A REFUSAL
----------------------------------------------------------------------------
They are not the same kind of fact. `quote`/`span_*`/`source_turn_id`/`when`/
`search_keys` DESCRIBE one occurrence of the claim, and a Point models one
claim — the canonical's own occurrence is a truthful record, so keeping it and
reporting the difference loses a description, not support. `extractedFrom` is
the claim's PROVENANCE — which document asserted it — and a Point holds ONE
scalar ref, so the second source would have to overwrite the first: a
destructive field write on a committed row, which §4.3 forecloses outside a
lifecycle event. Refusing keeps both claims and both attributions.

MEASURED BEFORE THIS MODULE (2026-11-12, this worktree)
-------------------------------------------------------
Scope the following to the UTTERANCE lane: `extractor.LLMExtractor.run` →
`_PointStage` emits cleaned content only (`_POINTS_SYS`, `extractor.py:565`), so
there the duplicate's node carries provenance identical to the canonical's
(same `session:<id>` source, same `sourceVersionTransit`) and no attachments.
That is a statement about one producer, not about the code base — the DOCUMENT
lane (`extractor.LLMExtractor._run_document`, `extractor.py:1146-1156`) passes
`aboutEntities` into `api.add_point`, which mints `aboutSubject`/`aboutObject`
edges, so the attachment class this module carries is live in the tree; that
lane does not route through this seam.
The hole is structural, and it is reachable the moment a producer attaches
evidence: `tests/test_capture_session.py` already drives this fold with a
stub whose duplicate carries `quote`/`when`/`source_turn_id`, and the
`#3778` read-back pin at the same seam exists precisely because a fold's
reply used to describe a payload the surviving node never held.
`config/v4-mechanisms.yml` records the same split — on the **`exact_dedup`**
row (not `near_dup_detect`, where the three-site split lives): *"The FOLD and
the `DETACH DELETE` of the folded node's `aboutObject` happen in the SDK's
capture seam (`sdk.py`) … Attributing that write to the pure helper would
point a lane at a module with no write in it."* — and this change extends
that note with what the carry does now.
WHAT THE CARRY COSTS, DECLARED (measured on this worktree)
----------------------------------------------------------
Two writes per folded duplicate, never more: `link_entity` per attachment
(a MERGE plus one pre-check read, journaled as `EntityLinked` when the SDK has
a journal) and — only when a free slot is actually filled — ONE `update_point`.

⚠️ `update_point` is the SDK's lifecycle door, and it is used deliberately:
§4.3 forbids writing a committed row's field outside an appended lifecycle
Event, and this door appends `PointRevised`. Two consequences a later reader
must know rather than discover:

* on a journal-armed SDK the record is appended, and the capture's own Points
  live in `_InMemoryEventLog` (`durable = False`, `sdk.py`) — so the journal
  holds a `PointRevised` for an id whose `PointAdded` it never had. It is inert
  on the graph fold (the revision matches no row) and no replay can reach it;
  the seam is journal-free by design for these points, and this is the one
  record that steps outside that. Tracked as a declared residual, not fixed
  here: routing the carry through the capture's own event stream would need a
  prop-carrying revision door `EventAPI` does not have;
* it marks the canonical as an EP-dirty root, so the fold can schedule one
  belief re-calibration for it. Content and edges are unchanged; this is cost,
  not corruption. It is why the carry is skipped entirely when there is
  nothing free to fill.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The about* predicates the capture's own attachment pass can put on a Point.
#:
#: ⚠️ FIVE, not two. `projection._create_about_edges` is the rel-IGNORING
#: Subject→Object→Event→Source→Point cascade (`_ABOUT_NAME_CASCADE`,
#: `projection/edges.py`), so an `aboutEntities` entry resolves to whichever
#: label owns that name — a release day named as an `:Event` mints `aboutEvent`,
#: a document name mints `aboutDocument`. Reading only two of the five left the
#: other three to be destroyed by the delete while the receipt said the
#: duplicate had "no evidence of its own" (measured).
_FOLD_ATTACHMENT_RELS = ("aboutObject", "aboutSubject", "aboutEvent",
                         "aboutDocument", "aboutPoint")

#: Which of them a fold can actually RE-WRITE. The permitted ONTOLOGY §3.2
#: triples for a Point source are `aboutSubject`/`aboutObject`/`aboutEvent`
#: (`session_link.ENTITY_LINKED_TRIPLES`); `aboutDocument` (Point→Source) and
#: `aboutPoint` (only Event→Point) are not among them, so there is no legal
#: writer for the duplicate's copy — the fold REFUSES rather than dropping it.
_CARRIABLE_ATTACHMENT_RELS = ("aboutSubject", "aboutObject", "aboutEvent")

#: ONE OCCURRENCE'S ADDRESS — an all-or-nothing group.
#:
#: ``quote`` + ``source_turn_id`` + ``span_start``/``span_end`` address the
#: same thing: a region of one turn's text (the E3 and E4 shapes the capture
#: contract writes). They are NOT independent slots. Copying a span from the
#: duplicate onto a canonical that kept its OWN quote and turn composes an
#: address no occurrence ever had — offsets computed against turn-2's text,
#: presented as turn-1's — and nothing downstream can catch it (`validate_span`
#: checks order and range, not which text the range indexes). So the group is
#: carried WHOLE when the canonical holds none of it, and left alone (with the
#: difference reported) the moment the canonical holds any part of it.
_OCCURRENCE_ADDRESS = ("quote", "span_start", "span_end", "source_turn_id")

#: Single-valued FACTS about the occurrence: ``(read key, write key)``.
#:
#: ``when`` maps onto ``validFrom`` — the ONTOLOGY §4.7 date slot, and the same
#: mapping the v2 capture seam's create branch applies. ⚠️ The two spellings are
#: ONE slot on the canonical side too: a producer that stored the raw ``when``
#: (the E3 passthrough does) fills ``validFrom`` as much as a ``validFrom``
#: does, so the occupancy check reads BOTH — reading only ``validFrom`` wrote a
#: second value for one date onto the node (measured, then fixed here).
_SINGLE_SLOTS = (
    ("when", "validFrom"),
    ("search_keys", "search_keys"),
)

#: Fields that are EVIDENCE OF SUPPORT rather than per-occurrence addresses.
#: The fold never writes them onto the canonical (they are the canonical's own
#: state — first-writer, and `confidence` is EP's to move, not a capture
#: writer's), but a difference is REPORTED: before this, a duplicate whose
#: `confidence`/`pointKind` differed was crossed in silence while the receipt
#: claimed it had "no evidence of its own to carry" (measured).
_REPORTED_FIELDS = ("confidence", "pointKind")


@dataclass(frozen=True)
class FoldEvidence:
    """What ONE duplicate node holds, read off the live graph.

    ``attachments`` is ``(rel, target_label, target_id, confidence)``; a
    target with no id is reported in ``unaddressable`` instead, because
    `link_entity` MERGEs on ``{id:$id}`` and would mint a different node.
    """

    attachments: tuple[tuple[str, str, str, float | None], ...] = ()
    unaddressable: tuple[tuple[str, str], ...] = ()
    source_refs: frozenset[str] = frozenset()
    scalar: dict[str, Any] = field(default_factory=dict)
    #: ``span_start`` and ``span_end`` are an address PAIR; a duplicate holding
    #: one without the other has an address that is already half-written
    #: (a capture Point can hold it — `validate_span` guards the SDK write
    #: doors, not the EventAPI seam). Writing it would raise on the canonical
    #: and refuse the fold forever under a misleading token, so it is detected
    #: here and named for what it is.
    malformed_address: bool = False
    #: `_REPORTED_FIELDS` the duplicate holds (``{field: value}``). The fold
    #: never writes these onto the canonical — they are its own state — but a
    #: difference is REPORTED, because crossing one in silence is how the
    #: receipt came to say "no evidence of its own" over a duplicate whose
    #: `confidence`/`pointKind` disagreed (measured).
    support: dict[str, Any] = field(default_factory=dict)


#: The evidence columns, in ONE place: the duplicate's read and the canonical's
#: read must select the same list in the same order, or the occupancy checks
#: compare different columns.
_EVIDENCE_SELECT = (
    "MATCH ({alias}:Point {{id:$id}}) "
    "RETURN {alias}.extractedFrom, {alias}.extractedFromAll, {alias}.quote, "
    "       {alias}.span_start, {alias}.span_end, {alias}.source_turn_id, "
    "       {alias}.search_keys, {alias}.when, {alias}.validFrom")


def assert_evidence_vocabulary() -> None:
    """Assert this module reads every field the capture contract declares.

    The evidence slots are a hand-copy of `sdk._CAPTURE_PASSTHROUGH_ORDER` (the
    E3/E4 capture surface), and a copy with no guard silently DRIFTS: a field
    added to the capture contract would not be read here, so the `DETACH DELETE`
    would destroy it — precisely the failure this module exists to stop. The
    sibling read-back (`sdk._capture_passthrough_read_fields`) generates its
    field list from that tuple for the same reason; this asserts coverage
    instead of importing it, because importing `sdk` here would be a cycle
    (`sdk` imports this module at its fold site).
    """
    from .sdk import _CAPTURE_PASSTHROUGH_ORDER

    declared = set(_OCCURRENCE_ADDRESS) | {key for key, _ in _SINGLE_SLOTS}
    missing = sorted(set(_CAPTURE_PASSTHROUGH_ORDER) - declared)
    if missing:
        raise AssertionError(
            "#9437: the capture contract declares evidence fields this fold "
            f"does not read (so a fold would DESTROY them): {missing}. Add "
            "them to `_OCCURRENCE_ADDRESS`/`_SINGLE_SLOTS` (and to the "
            "`_EVIDENCE_SELECT` list).")


def read_fold_evidence(proj: Any, dup_id: str) -> FoldEvidence | None:
    """Read the duplicate's evidence, or ``None`` when it cannot be read.

    ``None`` (not an empty :class:`FoldEvidence`) is the fail-closed answer:
    "this node holds nothing" and "the read failed" are different facts, and
    only the first may authorise a delete. The caller REFUSES the fold on
    ``None`` rather than treating an unreadable node as an empty one.
    """
    try:
        rows = proj.g.query(
            _EVIDENCE_SELECT.format(alias="d") + ", d.confidence, d.pointKind",
            params={"id": dup_id}).result_set
    except Exception:
        return None
    if not rows:
        return None
    (scalar_ref, all_refs, quote, span_start, span_end, source_turn_id,
     search_keys, when, validfrom, _confidence, _point_kind) = rows[0]
    refs: set[str] = set()
    for ref in (scalar_ref, *(all_refs or ())):
        if isinstance(ref, str) and ref.strip():
            refs.add(ref.strip())
    scalar: dict[str, Any] = {}
    for key, value in (("quote", quote), ("span_start", span_start),
                       ("span_end", span_end), ("source_turn_id", source_turn_id),
                       ("search_keys", search_keys),
                       ("when", when if when is not None else validfrom)):
        # Presence is ownership (#5004's rule, applied to evidence): an empty
        # string is a value the producer wrote, not an absence to fill.
        if value is not None:
            scalar[key] = value

    try:
        edges = proj.g.query(
            "MATCH (d:Point {id:$dup})-[r]->(t) "
            f"WHERE type(r) IN {list(_FOLD_ATTACHMENT_RELS)!r} "
            "RETURN type(r), head(labels(t)), coalesce(t.id, ''), r.confidence",
            params={"dup": dup_id}).result_set
    except Exception:
        return None
    attachments: list[tuple[str, str, str, float | None]] = []
    unaddressable: list[tuple[str, str]] = []
    for rel, label, target_id, confidence in edges:
        if not isinstance(rel, str) or not isinstance(label, str):
            return None
        if isinstance(target_id, str) and target_id.strip():
            conf = confidence if isinstance(confidence, (int, float)) else None
            attachments.append((rel, label, target_id.strip(), conf))
        else:
            unaddressable.append((rel, label))
    support = {"confidence": _confidence, "pointKind": _point_kind}
    return FoldEvidence(
        attachments=tuple(attachments),
        unaddressable=tuple(unaddressable),
        source_refs=frozenset(refs),
        scalar=scalar,
        malformed_address=((span_start is None) != (span_end is None)),
        support={k: v for k, v in support.items() if v is not None},
    )


def _canonical_view(proj: Any, canonical_id: str) -> dict | None:
    """The canonical's evidence-bearing slots + source refs, or ``None``."""
    try:
        rows = proj.g.query(
            _EVIDENCE_SELECT.format(alias="c") + ", c.confidence, c.pointKind",
            params={"id": canonical_id}).result_set
    except Exception:
        return None
    if not rows:
        return None
    (scalar_ref, all_refs, quote, span_start, span_end, source_turn_id,
     search_keys, when, validfrom, _confidence, _point_kind) = rows[0]
    refs: set[str] = set()
    for ref in (scalar_ref, *(all_refs or ())):
        if isinstance(ref, str) and ref.strip():
            refs.add(ref.strip())
    return {
        "refs": refs,
        "quote": quote, "span_start": span_start, "span_end": span_end,
        "source_turn_id": source_turn_id, "search_keys": search_keys,
        "when": when, "validFrom": validfrom,
        "confidence": _confidence, "pointKind": _point_kind,
    }


def _already_written(out: dict) -> str:
    """Name what a REFUSED-AT-THE-END carry has already added to the canonical.

    A refusal is not always a no-op: the free-slot write is atomic and happens
    first, so a failure in the link loop refuses with those slots (and any
    earlier link) already on the canonical. Reporting that is the difference
    between "kept distinct" and "kept distinct — and the canonical gained one
    link" (measured: a reader treats the first as "nothing changed").
    """
    added = []
    if out.get("copied"):
        added.append("copied " + ", ".join(out["copied"]))
    if out.get("carried"):
        added.append(f"{out['carried']} attachment link(s)")
    if not added:
        return ""
    return ("; it had ALREADY added " + " and ".join(added)
            + " to the canonical — additive, nothing removed from either node")


def _carried(proj: Any, canonical_id: str, rel: str, label: str,
             target_id: str) -> tuple[int, float | None]:
    """``(does the edge exist, the grading it carries)`` — measured, not assumed.

    Existence is COUNTED, never inferred from the write having been issued:
    `link_entity`'s MERGE creates nothing when a endpoint is absent. The
    grading is read back for the same reason — `link_entity` short-circuits a
    pre-existing edge WITHOUT re-grading it (#1370: "a later no-confidence link
    therefore never clears an earlier confident one"), so the duplicate's
    `confidence` is NOT carried onto an edge the canonical already holds. That
    is a fact the caller must report rather than assume.
    """
    if rel not in _CARRIABLE_ATTACHMENT_RELS or label not in (
            "Object", "Subject", "Event"):
        # Both halves are read from the graph itself; the allowlist is a
        # fail-closed backstop against interpolating an unvetted label into a
        # pattern (the same posture `session_link.link_entity` takes).
        return 0, None
    rows = proj.g.query(
        f"MATCH (:Point {{id:$canon}})-[r:{rel}]->(t:{label} {{id:$target}}) "
        "RETURN count(*), head(collect(r.confidence))",
        params={"canon": canonical_id, "target": target_id}).result_set
    if not rows or not rows[0] or not rows[0][0]:
        return 0, None
    grading = rows[0][1]
    return int(rows[0][0]), (grading if isinstance(grading, (int, float))
                             else None)


def carry_fold_evidence(proj: Any, dup_id: str, canonical_id: str, *,
                        sdk: Any = None) -> dict:
    """NEVER raises — any internal failure is a REFUSAL, never a propagated error.

    The caller sits inside the capture's per-statement loop whose surrounding
    ``try`` is the WHOLE loop, so a raise here would abort the remaining
    statements' resolution (they would not fold, not be registered as
    canonicals, and not be wired into the Session) and surface as one generic
    error. A refusal is the correct answer for a destructive act.
    """
    try:
        assert_evidence_vocabulary()
        return _carry_fold_evidence(proj, dup_id, canonical_id, sdk=sdk)
    except Exception as e:
        return {"ok": False, "reason": "internal_error",
                "blocked": ("the evidence carry failed "
                            f"({type(e).__name__}: {e})"),
                "carried": 0, "copied": [], "residual": []}


def _carry_fold_evidence(proj: Any, dup_id: str, canonical_id: str, *,
                         sdk: Any = None) -> dict:
    """Move the duplicate's own evidence onto the canonical. NEVER raises.

    Call this BEFORE the copy is removed; a refusal means the caller must NOT
    delete. Returns::

        {"ok": bool,             # False ⇒ refuse the fold, keep both nodes
         "reason": str | None,   # machine-readable refusal token
         "blocked": str | None,  # the same refusal in words, for the receipt
         "carried": int,         # attachment links PRESENT after the write
         "copied": [keys],       # free scalar slots filled from the duplicate
         "residual": [keys]}     # slots the canonical already filled (first-writer)

    ``residual`` is a REPORT, never a refusal: two occurrences of one claim
    legitimately carry different quotes, and a Point holds one value per
    field. ``reason`` is the narrow set the module docstring enumerates.
    """
    out: dict = {"ok": False, "reason": None, "blocked": None,
                 "carried": 0, "created": 0, "copied": [], "residual": []}
    evidence = read_fold_evidence(proj, dup_id)
    if evidence is None:
        out["reason"] = "unreadable"
        out["blocked"] = ("the duplicate's evidence could not be read, so a "
                          "fold cannot be shown to lose nothing")
        return out
    canonical = _canonical_view(proj, canonical_id)
    if canonical is None:
        out["reason"] = "unreadable"
        out["blocked"] = ("the canonical this claim resolved to could not be "
                          "read, so its evidence could not be checked")
        return out

    # 1) A link whose target has no id cannot be re-pointed: `link_entity`
    #    MERGEs on `{id:$id}`, so it would mint a DIFFERENT node.
    if evidence.unaddressable:
        rels = ", ".join(sorted({rel for rel, _ in evidence.unaddressable}))
        out["reason"] = "unaddressable"
        out["blocked"] = (f"its {rels} link(s) name no addressable target, so "
                          "they cannot be carried")
        return out

    # 2) A source reference the canonical does not carry is a node FIELD
    #    (`extractedFrom`), which a PASS may not write outside a lifecycle
    #    event (EXTRACTOR-V4-ARCHITECTURE.md §4.3, "G5's destructive half",
    #    PR #10069) — so it cannot be moved, and the fold must not drop it.
    missing = sorted(evidence.source_refs - canonical["refs"])
    if missing:
        out["reason"] = "uncarried_source_ref"
        out["blocked"] = ("its source reference "
                          f"{missing[0]!r} is not one the canonical carries, "
                          "and a source field cannot be written onto a "
                          "committed row by a fold")
        return out

    # 3) The duplicate's own address may be HALF-written (a capture Point can
    #    hold `span_start` without `span_end`: the span validator guards the SDK
    #    write doors, not the EventAPI seam). That is the duplicate's own
    #    malformed evidence, not a write failure on the canonical — named here
    #    so the receipt does not blame the wrong node, and refused before any
    #    write so a refused fold stays a no-op.
    if evidence.malformed_address:
        out["reason"] = "malformed_address"
        out["blocked"] = ("its span evidence is half an address (one of "
                          "`span_start`/`span_end` is missing), so it is not a "
                          "value the fold can carry and not one it may drop")
        return out

    # 4) A link the ONTOLOGY gives a Point no writer for cannot be moved at all
    #    — refuse, before anything is written, rather than dropping it. (The
    #    cascade can mint `aboutDocument`/`aboutPoint` on a Point; neither is a
    #    permitted §3.2 triple for a Point source, so `link_entity` cannot
    #    re-create it.)
    writer_less = sorted({rel for rel, _label, _target, _conf
                          in evidence.attachments
                          if rel not in _CARRIABLE_ATTACHMENT_RELS})
    if writer_less:
        out["reason"] = "uncarriable_attachment"
        out["blocked"] = (f"its {', '.join(writer_less)} link(s) are predicates "
                          "a Point has no permitted writer for, so they cannot "
                          "be put on the canonical")
        return out

    # 5) Evidence of SUPPORT the fold must not write but must not cross in
    #    silence: the canonical's own value stands (first-writer; `confidence`
    #    is EP's field, not a capture writer's) and the difference is reported.
    for name, value in sorted(evidence.support.items()):
        held = canonical.get(name)
        if held != value:
            out["residual"].append(
                f"{name} (duplicate {value!r}, canonical {held!r})")

    # 6) Fill FREE evidence slots. ONE write, because the write must be
    #    ATOMIC: `update_point` refuses a half span, so per-slot writes left a
    #    partially copied node behind on a refused fold (measured: `quote`
    #    landed and the span did not). One `SET n += $props` either lands whole
    #    or not at all — and it runs BEFORE the links, so its failure leaves the
    #    graph untouched and a refused fold stays a no-op.
    to_write: dict[str, Any] = {}
    # 6a) the occurrence ADDRESS — all or nothing (see `_OCCURRENCE_ADDRESS`).
    address_held = any(canonical.get(k) is not None for k in _OCCURRENCE_ADDRESS)
    for key in _OCCURRENCE_ADDRESS:
        if key not in evidence.scalar:
            continue
        if address_held:
            if canonical.get(key) != evidence.scalar[key]:
                out["residual"].append(key)
        else:
            to_write[key] = evidence.scalar[key]
    # 6b) single-slot facts. `when`/`validFrom` are ONE slot: the occupancy
    #     check reads both spellings, or a producer that stored the raw `when`
    #     would receive a second value for one date.
    for key, write_key in _SINGLE_SLOTS:
        if key not in evidence.scalar:
            continue
        value = evidence.scalar[key]
        held = canonical.get(write_key)
        if held is None:
            held = canonical.get(key)
        if held is not None:
            if held != value:
                out["residual"].append(key)
            continue
        to_write[write_key] = value

    if to_write:
        try:
            if sdk is None:
                raise RuntimeError("no SDK write door available")
            sdk.update_point(canonical_id, **to_write)
        except Exception as e:
            out["reason"] = "slot_write_failed"
            out["blocked"] = (
                "its " + ", ".join(sorted(to_write))
                + " evidence could not be written onto the canonical "
                f"({type(e).__name__}: {e})")
            return out
        # COUNT what landed, do not report the attempt: `update_point` on an
        # absent id is a SILENT success (it returns {} and raises nothing), so
        # a canonical that vanished between the read and the write would
        # otherwise be reported as carrying evidence it never received — and
        # the caller would then delete the duplicate. The same rule the links
        # follow ("counted, never assumed").
        after = _canonical_view(proj, canonical_id)
        unlanded = [] if after is None else sorted(
            k for k, v in to_write.items() if after.get(k) != v)
        if after is None or unlanded:
            out["reason"] = "slot_write_failed"
            out["blocked"] = (
                "the canonical could not be read back after the evidence "
                "write, so the carry is unproven" if after is None else
                "its " + ", ".join(unlanded)
                + " evidence did not land on the canonical")
            return out
        out["copied"] = sorted(to_write)

    # 7) Carry the attachment links (additive, journaled when the SDK has a
    #    journal), then COUNT what landed. Last, because a link write is the
    #    only step that can fail AFTER mutating the graph — and when it does,
    #    the refusal says what was already written instead of presenting
    #    "kept distinct" as "nothing changed".
    from .session_link import link_entity
    failures: list[str] = []
    for rel, label, target_id, confidence in evidence.attachments:
        try:
            created = link_entity(proj, "Point", canonical_id, target_id,
                                  edge_type=rel, target_label=label, sdk=sdk,
                                  confidence=confidence)
        except Exception as e:
            failures.append(f"{rel}→{target_id} ({type(e).__name__}: {e})")
            continue
        present, grading = _carried(proj, canonical_id, rel, label, target_id)
        if not present:
            failures.append(f"{rel}→{target_id} (did not land)")
            continue
        out["carried"] += 1
        if created:
            out["created"] += 1
        # ⚠️ The LINK is carried; its GRADING may not be. `link_entity`
        # short-circuits a pre-existing edge without re-grading it (#1370),
        # so when the canonical already held this link the duplicate's
        # `confidence` is still on the duplicate — about to be deleted. That
        # is reported, never claimed as carried.
        if confidence is not None and grading != confidence:
            out["residual"].append(f"confidence of {rel}→{label}")
    if failures:
        out["reason"] = "link_not_carried"
        out["blocked"] = ("its attachment link(s) could not be carried onto "
                          f"the canonical: {'; '.join(failures[:3])}"
                          + _already_written(out))
        return out

    out["ok"] = True
    return out


__all__ = ["FoldEvidence", "assert_evidence_vocabulary",
           "carry_fold_evidence", "read_fold_evidence"]
