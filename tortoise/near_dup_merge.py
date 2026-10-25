"""#5006 / D12·O4 — near-duplicate MERGE: the FUSION half (union of attachments).

THE GAP THIS CLOSES, IN ONE SENTENCE
------------------------------------
Design of record `EXTRACTOR-V4-ARCHITECTURE.md` §4.2 puts the near-duplicate
concern at **three** sites and says so explicitly — *"so no operation has two
homes, and no passage in this document places a fusion before the gate"* —
and the third site had no implementation:

| operation | site | who | state before this module |
|---|---|---|---|
| **DETECT** the pair + apply the §16.4 never-across guard | `S2.2a-β` | mechanical | ✅ `extractor_v2._boundary` (`#5080`) |
| record the **VERDICT** `MERGE` for a pair that survives the guard | `S2.2b` VET | the Jev arbiter | ✅ `vet_gate.OUTCOMES` (`#5005`) — *"nothing consumes it yet"* |
| execute the **FUSION** — the union of both sides' attachments | `S3` | the resolve/consolidation tail | ✅ **this module** |

The verdict had no consumer and the action had no implementation; that is
exactly what `#5006` names and what `config/v4-mechanisms.yml` recorded as
`state: not-implemented`.

WHAT THE RULING SAYS (D12/O4 — §16.4, and `STORAGE-ARCHITECTURE.md` §14.1 O4)
------------------------------------------------------------------------------
*"yes we can merge near duplicate claims. maybe we can have jev with the
claim and the narrative/raw data/both arbiter that."* — and the two rules
that bound it:

- **`UNION THE ATTACHMENTS, NEVER FUSE THE CLAIM`** (§S2.2). The fusion keeps
  the PRIOR's claim verbatim and adds the candidate's attachments to it. It
  never writes a new claim built from both. `FUSE` is FORBIDDEN (§16.3·O4).
- **A merge never crosses a difference in a number, a name, a negation, a
  condition, a date, a scope, or a role inversion** — the §16.4 never-across
  list, which `_boundary` already enumerates mechanically.

WHY THE BAR IS HIGH (and it is evidence, not caution)
-----------------------------------------------------
*sic* — *"a false merge costs MORE than keeping near-duplicates"*; multiple
independent sources find over-aggressive consolidation *"destroys specific
details needed for factual QA"*; practical thresholds cluster at **~0.95**.
⇒ `MERGE_MIN_OVERLAP = 0.95`. ⚠️ **This is NOT a volume optimisation** — O2
withdraws every *target*, and the honest caveat in §16.4 is that *"if the
draft-and-run loop shows merges losing detail, THAT is the signal to raise the
bar"*. Volume reduction is a side effect, never the reason.

WHY THE VERDICT IS STILL REQUIRED (the `flagged` operand)
--------------------------------------------------------
The mechanical guard answers *"is anything distinguishing lost?"*. It cannot
answer *"are these the same claim?"* — that is the semantic question D12 gives
the arbiter. So a pair below the never-across guard is **eligible**, and only a
`MERGE` verdict from `S2.2b VET` makes it **admissible**. `flagged=False` is
the default and it means: no verdict, no fusion. A mechanical bar alone is not
authorised to merge, and this module does not let it.

WHAT THE FUSION WRITES, AND WHERE THE LOSING SIDE'S EVIDENCE GOES
-----------------------------------------------------------------
A `NOOP` fold emits no payload point at all, so it stamps nothing: that is why
`#9424` had to *refuse* a fold that would drop an entity the candidate names.
This module is the authorised alternative for exactly that case — the fold
becomes admissible because nothing is dropped:

1. **ONE node survives** — the prior's `Point`, its claim byte-identical. No
   second point is created and the prior's text is not rewritten.
2. **Its attachment set becomes the UNION** — every `Object` either claim
   named is linked to the survivor (`apply_merge_union`).
3. **The losing document's provenance survives as a `(Session)-[:CONTAINS]->
   (Point)` edge** — the second session's evidence is a member of the claim's
   memory, which is the same channel the exact-fold path (`#2104`) uses.
4. **The merge itself is recorded — at the EXTRACTOR boundary.** `execute_embed`
   returns it in `result["merges"]` (never inside the payload), with the
   losing candidate's `quote`, its content, and the union it wrote.
   ⚠️ **The capture path does NOT forward that record**: `_extract_session_v2`
   keeps only the failure warnings, so a `capture_session` caller can follow a
   merge through `stats["merges"]` and the `(Session)-[:CONTAINS]->(Point)`
   edge, but NOT the losing quote. Do not read this item as a promise about the
   capture receipt — and note a `NOOP` fold IS surfaced there (as a classified
   `dedup` entry, `sdk.py:12290-12302`), so the two are **not** alike on that
   path. Tracked on #9871.
   *A merge a reader cannot find is indistinguishable from a silent drop*, which
   is why that gap is filed rather than absorbed here.

FAIL-CLOSED HERE — the opposite direction to `_attachments_preserved`
---------------------------------------------------------------------
`extractor_v2._attachments_preserved` (the `#9424` guard) is fail-OPEN: an
absent attachment set on either side PERMITS a fold, because refusing on an
unknown set would silently become *"never fold"* wherever the S3 enrichment is
off — a volume regression with no safety gain. **This module is the opposite,
deliberately**, because it does not merely decline to act: it WRITES. An
unreadable attachment set on either side means the union cannot be computed,
so no union can be written, so the merge is refused and both claims survive
(`reason="unreadable_attachments"`). A wrong keep is noise; a wrong merge is
memory loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The verdict token. Spelled identically to `vet_gate.MERGE` — the S2.2b
#: vocabulary is the authority and this module consumes it, so a drift here is
#: a bug in this line. `extractor_v2` imports `vet_gate` (never the reverse), so
#: the constant is duplicated rather than imported to keep this module
#: importable on its own; `tests/test_near_dup_merge_5006.py` pins the two
#: spellings equal.
MERGE = "MERGE"

#: **The high bar** (§16.4): *"Practical thresholds cluster at ~0.95"*, and the
#: direction of error is asymmetric — a false merge costs more than a kept
#: near-duplicate. Lowering this is a decision, not a tuning knob: it re-opens
#: the "merging can lose information" caveat §16.4 records.
MERGE_MIN_OVERLAP = 0.95


@dataclass(frozen=True)
class MergePlan:
    """One pair's fusion verdict: whether to merge, and the union to write.

    ``merge=False`` is a REFUSAL and it is the common case — every operand of
    the ladder below can produce it, and a refused pair falls through to `ADD`
    in ``classify_consolidation`` (both claims survive).
    """

    merge: bool
    prior_id: str = ""
    overlap: float = 0.0
    #: The whole attachment set the survivor must carry after the fusion,
    #: **spelled the way the graph stores it** (`Object.name` is case-keeping;
    #: see ``_entity_names``). Ordered by the NORMALISED key so the tuple is
    #: deterministic without re-casing what the write must address.
    union_entities: tuple[str, ...] = ()
    #: What the candidate contributes that the prior does not already carry —
    #: the evidence the fold would have dropped, which is the reason this
    #: mechanism exists. Empty on a merge means `NOOP` was already lossless.
    #: Original spelling, same ordering rule as ``union_entities``.
    added_entities: tuple[str, ...] = ()
    #: Machine-readable why: one of the ladder's refusal reasons, or
    #: ``"near_identical"``.
    reason: str = ""
    #: Human-readable why, recorded on the merge record.
    evidence: str = ""


def _entity_names(value: object) -> dict[str, str] | None:
    """An ``about_entities``-shaped value as ``{normalised: original}``, or
    ``None`` when the value is not a usable attachment set.

    ``None`` — not an empty dict — for anything that is not a sequence of
    strings: *"this row names no entities"* and *"this row's entities could not
    be read"* are different facts, and only the FIRST may be merged across
    (see the module docstring's fail-closed note).

    ⚠️ **The two halves have two DIFFERENT jobs and must not be conflated.**
    The normalised key is for COMPARISON only (union identity, "what does the
    candidate add"): `Acme` and `acme` name one entity, because case is
    presentation, not identity. The ORIGINAL spelling is what the WRITE must
    spell, because `Object.name` is stored **case-preserved** everywhere —
    `projection/entities.py` MERGEs on the verbatim name, capture's own
    attachment pass resolves the SAME `about_entities` list with
    `name.strip()` (no lowering, `sdk.py:12123`), and the S3 enrichment that
    fills a prior row reads `collect(o.name)` verbatim. Emitting the
    normalised form here is what made the fusion's central promise fail on
    ordinary data: `MATCH (o:Object {name:'acme'})` matches nothing when the
    node is `Acme`, so the edge was never written, the candidate's exclusive
    attachment was reported `unresolved`, and the merge record claimed a union
    it had not landed.

    Within one side, the lexicographically-first spelling of a name wins, so
    the result does not depend on the iteration order of a ``set``/``frozenset``.
    """
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        original = item.strip()
        if original:
            items.append(original)
    names: dict[str, str] = {}
    for original in sorted(items):
        names.setdefault(original.lower(), original)
    return names


def near_dup_merge(candidate: dict, prior: dict, *, overlap: float,
                   difference: str | None,
                   identity: frozenset[str] | set[str],
                   flagged: bool) -> MergePlan:
    """Decide ONE near-duplicate fusion: merge, or refuse and say why.

    ``candidate`` / ``prior`` are S3 point rows (``{id, content,
    about_entities}``-shaped). ``overlap``, ``difference`` and ``identity`` are
    the values the caller ALREADY computed for this pair — `_boundary`'s two
    returns and the length-guarded token overlap — so the never-across guard is
    evaluated once and cannot disagree with itself here.

    The ladder, in order (each rung is a REFUSAL and each has its own reason):

    1. ``flagged`` — no `MERGE` verdict from `S2.2b`, no fusion (D12: the
       verdict is the arbiter's; a mechanical bar cannot stand in for it).
    2. ``identity`` non-empty — a distinguishing difference (negation,
       condition, scope, language, substituted content). Absolute.
    3. ``difference`` not None — a value difference (number/quantity or date).
    4. ``overlap < MERGE_MIN_OVERLAP`` — not near-identical enough.
    5. an unreadable attachment set on either side — the union is unknown, so
       nothing can be written (fail-CLOSED; see the module docstring).
    6. nothing to union — the prior already carries every entity the candidate
       names. That pair is `NOOP`'s, which is lossless and cheaper; merging it
       would do the same work on a riskier path for no gain.
    """
    if not flagged:
        return MergePlan(False, reason="not_flagged",
                         evidence="no MERGE verdict from S2.2b VET")

    differences = sorted(identity) if identity else []
    if differences:
        # The §16.4 never-across list. Absolute, and reported by its own
        # dimension so the refusal is auditable rather than a bare false.
        return MergePlan(
            False, reason="distinguishing_difference",
            evidence=f"refused across {', '.join(differences)} "
                     "(§16.4 never-across)")

    if difference is not None:
        return MergePlan(
            False, reason="value_difference",
            evidence=f"refused across a value difference ({difference}) "
                     "(§16.4 never-across)")

    try:
        ov = float(overlap)
    except (TypeError, ValueError):
        ov = 0.0
    if ov < MERGE_MIN_OVERLAP:
        return MergePlan(
            False, overlap=ov, reason="below_bar",
            evidence=f"overlap {ov:.3f} < {MERGE_MIN_OVERLAP} merge bar")

    # Fail-closed on an unknown set: `_attachments_preserved` is fail-OPEN
    # because it only DECLINES to fold; this writes, so unknown means refuse.
    prior_names = _entity_names(prior.get("about_entities"))
    cand_names = _entity_names(candidate.get("about_entities"))
    if prior_names is None or cand_names is None:
        return MergePlan(
            False, overlap=ov, reason="unreadable_attachments",
            evidence="an attachment set could not be read on this pair — "
                     "nothing can be unioned, so nothing is merged")

    # The PRIOR's spelling wins where both sides name the same entity: the
    # survivor is the prior, and the write must address the node it already
    # points at. The candidate contributes spelling only for names the prior
    # does not carry (see `_entity_names` on why the write keeps case).
    union: dict[str, str] = dict(prior_names)
    added: dict[str, str] = {}
    for key in sorted(cand_names):
        if key in union:
            continue
        union[key] = cand_names[key]
        added[key] = cand_names[key]
    if not added:
        return MergePlan(
            False, overlap=ov, reason="nothing_to_union",
            evidence="the prior already carries every entity the candidate "
                     "names — this pair is NOOP's (lossless, no fusion needed)")

    return MergePlan(
        True,
        prior_id=str(prior.get("id") or ""),
        overlap=round(ov, 3),
        union_entities=tuple(union[k] for k in sorted(union)),
        added_entities=tuple(added[k] for k in sorted(added)),
        reason="near_identical",
        evidence=(f"near-identical (overlap {ov:.3f} >= {MERGE_MIN_OVERLAP}) "
                  f"with no distinguishing difference; attachments unioned "
                  f"({len(prior_names)} -> {len(union)}, +"
                  f"{len(added)} from the candidate); the claim is the "
                  f"prior's, unchanged — never fused"),
    )


def apply_merge_union(proj: Any, record: dict, *, session_id: str,
                      sdk: Any = None,
                      subject_names: Any = frozenset()) -> dict:
    """Execute one fusion against a live graph: union the attachments, keep the
    losing document's provenance. Returns an evidence dict; NEVER raises.

    ``record`` is one entry of ``execute_embed``'s ``result["merges"]``:
    ``{point_id (the SURVIVOR), about_entities (the union), ...}``.

    ``subject_names`` is the SUBJECT-KIND names the caller's payload declared
    (capture's ``subject_entity_names``, ``sdk.py:11857``). They are SKIPPED —
    see "the subject channel" below — and reported in ``skipped_subject``.
    Omitting it is safe rather than silently wider: with no set there is
    nothing to skip, which is exactly what a capture whose payload declares no
    subject-kind entity has. It is a name SET; a bare string is read as ONE
    name, never iterated as characters.

    What is written:

    - ``(Session {session_id})-[:CONTAINS]->(Point {point_id})`` — the losing
      document is not dropped: its session now contains the claim it argued
      for. This is the exact channel the identical-fold path uses (#2104), so
      a merged claim and a folded claim have the same provenance shape.
    - one ``aboutObject`` edge per unioned entity name, resolved by name and
      routed through :func:`tortoise.session_link.link_entity` so it REPLAYS
      (`EntityLinked`) when the SDK carries a journal — the #2296 live-only
      hazard, and the reason `#3664` routes capture's edges the same way.

    **Never raises and never blocks capture** (§4.2's failure policy for the
    write path: a failed attachment is a warning, not a lost session). The
    returned dict is the merge's record of what actually landed, so a reader
    can tell a written union from an attempted one.

    ⚠️ **The lookup is EXACT and case-SENSITIVE on purpose**, and it is the
    contract `_entity_names` satisfies: the union carries each name spelled the
    way the graph stores it, so `MATCH (o:Object {name:$n})` addresses the same
    node capture would. A `WHERE toLower(o.name) = $n` form is deliberately
    NOT used — nothing else in the code base matches entity names that way, and
    it would silently conflate two `Object`s that differ only in case.
    `unresolved` therefore means one thing only: *no node in this graph has
    that name*.

    ⚠️ **THE SUBJECT CHANNEL IS SKIPPED, not mis-bound — and the skip is what
    makes the coincidence case safe.** `about_entities` is not object-only: the
    SUBJECT channel (team/role/person, the gated `aboutSubject` edge) also
    contributes names to it. `subject_names` carries the payload's subject-kind
    names, and a union name in that set is reported in `skipped_subject` and
    **never written**.

    Why the skip is by NAME and not by "resolution failed": an `:Object` may
    legitimately share a subject's EXACT name — that is why capture's own pass
    skips on the name (`sdk.py:12124`, and the F6 note at `sdk.py:11849-11857`
    explains why the comparison is exact and not case-folded). So
    `MATCH (o:Object {name:$n})` in that case RESOLVES, and a write without
    this skip would attach the claim to an Object under a subject's name — the
    un-gated `aboutObject` edge capture deliberately refuses to write there
    (`#1370` / the `#4934` label leak). Bounding the consequence to "lands in
    `unresolved`" was therefore FALSE for exactly that case.

    ⚠️ **The pre-existing gap this makes VISIBLE instead of silent.** A merge
    emits no payload point, so `bind_point_subjects` never runs for the
    candidate and its confidence-gated `aboutSubject` binding is dropped — the
    binder is the only gated `aboutSubject` producer and this write has no
    binder. `skipped_subject` names those names, so the drop is REPORTED rather
    than assumed — ⚠️ **but only for subject-kind names that appear in the
    union's `about_entities`.** A participant that reaches the claim through the
    point's SLOTS is dropped UNREPORTED: the ordinary path binds it via
    `bind_point_subjects` (`sdk.py:12165`), a merge emits no payload point so
    that call never runs for the candidate, and the slots are not carried onto
    the merge record. So "nothing it argued for is silently dropped" is true of
    OBJECT attachments, and for subject binding it holds only for the
    `about_entities` channel — tracked on #9871. Closing that needs a
    subject-resolution path here; it is not a wider `MATCH` (which is the defect
    above), and it is not introduced by this module. The same blind spot
    `extractor_v2._attachment_ids` already has.
    """
    out: dict = {"point_id": "", "contained": 0, "linked": 0,
                 "unresolved": [], "skipped_subject": [], "warnings": []}
    point_id = str((record or {}).get("point_id") or "")
    out["point_id"] = point_id
    if not point_id:
        out["warnings"].append("merge record names no surviving point")
        return out
    try:
        # ⚠️ COUNT the edge, do not count the attempt. A bare `MERGE` after a
        # `MATCH` that resolves nothing is a silent no-op — returning 1 for it
        # would report a containment that does not exist, which is the
        # false-PASS shape this whole mechanism has to avoid. `RETURN count(*)`
        # is 1 only when both endpoints matched and the edge is there.
        rows = proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p) RETURN count(*)",
            params={"sid": session_id, "pid": point_id}).result_set
        out["contained"] = int(rows[0][0]) if rows and rows[0] else 0
        if not out["contained"]:
            out["warnings"].append(
                f"merge survivor {point_id} or session {session_id} is "
                "absent — no provenance edge written")
    except Exception as e:  # noqa: BLE001, RUF100 — additive, never blocking
        out["warnings"].append(
            f"merge CONTAINS edge failed ({type(e).__name__}: {e})")

    # Lazy import: `session_link` pulls the projection package, and this module
    # is imported by `extractor_v2` (whose import cost sits on every capture).
    # A module-level import here would put that on every extractor import.
    try:
        from .session_link import link_entity
    except Exception as e:  # noqa: BLE001, RUF100
        out["warnings"].append(
            f"merge attachment skipped, link_entity unavailable "
            f"({type(e).__name__}: {e})")
        return out

    names = record.get("about_entities") or []
    if not isinstance(names, (list, tuple, set, frozenset)):
        out["warnings"].append("merge record's about_entities is unreadable")
        return out
    # Capture's own skip (`sdk.py:12124`), applied here so a merge cannot write
    # an edge the SAME capture refuses to write on the normal route. Exact
    # names, case-preserved: the resolution below is case-SENSITIVE too.
    # A bare string is ONE name — iterating it would skip single characters and
    # silently drop a one-letter Object attachment for the rest of the capture.
    if isinstance(subject_names, str):
        subject_names = (subject_names,)
    subject_skip = {str(n).strip() for n in (subject_names or ())
                    if isinstance(n, str)}
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        _n = name.strip()
        if _n in subject_skip:
            # Reported, never written: the #1370 binder owns this edge and a
            # merge runs no binder. See the docstring's subject-channel note.
            out["skipped_subject"].append(_n)
            continue
        try:
            rows = proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": _n}).result_set
        except Exception as e:  # noqa: BLE001, RUF100
            out["warnings"].append(
                f"merge attachment lookup failed for {_n!r} "
                f"({type(e).__name__}: {e})")
            continue
        idless = False
        for row in rows:
            oid = row[0] if row else None
            if isinstance(oid, str) and oid:
                try:
                    out["linked"] += link_entity(proj, "Point", point_id, oid,
                                                 sdk=sdk)
                except Exception as e:  # noqa: BLE001, RUF100
                    out["warnings"].append(
                        f"merge attachment failed for {_n!r} "
                        f"({type(e).__name__}: {e})")
            else:
                idless = True
        if idless:
            # An id-less name stub cannot be addressed by `link_entity` (it
            # MERGEs on {id:...} and would mint a DIFFERENT node). Same
            # fallback as capture's own attachment pass (#1727): keep the
            # edge, live-only, honestly unjournaled.
            #
            # ⚠️ COUNT the edge here too. A bare `MERGE` after a `MATCH` that
            # resolves nothing is a silent no-op, so a blind `linked += 1`
            # reports an attachment that does not exist — the same false-PASS
            # shape the CONTAINS branch above exists to avoid, and the one
            # place `linked` would stop being a measurement. Reachable: this
            # function does NOT return early when `contained == 0` (the
            # survivor or session is absent), and `linked` is documented as
            # what actually LANDED. `unresolved` keeps its one meaning — *no
            # node has this name* — so a resolved-but-unwritten edge is not
            # reported there; the `contained` warning already names the
            # absent-endpoint cause.
            try:
                rows = proj.g.query(
                    "MATCH (p:Point {id:$pid}), (o:Object {name:$n}) "
                    "WHERE o.id IS NULL OR o.id = '' "
                    "MERGE (p)-[:aboutObject]->(o) RETURN count(*)",
                    params={"pid": point_id, "n": _n}).result_set
                out["linked"] += int(rows[0][0]) if rows and rows[0] else 0
            except Exception as e:  # noqa: BLE001, RUF100
                out["warnings"].append(
                    f"merge id-less attachment failed for {_n!r} "
                    f"({type(e).__name__}: {e})")
        elif not rows:
            out["unresolved"].append(_n)
    return out


__all__ = [
    "MERGE",
    "MERGE_MIN_OVERLAP",
    "MergePlan",
    "apply_merge_union",
    "near_dup_merge",
]
