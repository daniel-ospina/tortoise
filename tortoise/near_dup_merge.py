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
    (capture's ``subject_entity_names``; ``sdk.py::TortoiseSDK._extract_session_v2``
    seeds it at ``subject_entity_names.add(name)`` and skips on it where the
    ``aboutObject`` edge is written). They are SKIPPED —
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

    ⚠️ **ONE lookup over the writer's key space — there are no "arms".** The
    union carries each name spelled the way the caller's payload spells it, and
    the query below matches `name` · `canonical_name` · `aliases` over the raw and
    the folded spelling in a SINGLE `WHERE`, then attaches every row it matched.
    A raw-exact hit therefore does NOT suppress a fold-only hit: if
    `"Widget"` is one Object's spelling and another Object carries the folded key
    only, BOTH are attached — capture's own attach-to-every-carrier disposition
    (§"one `aboutObject` edge per unioned entity name"), not a precedence rule.
    A `WHERE toLower(o.name) = $n` form is deliberately NOT used — a bespoke
    lowering written into THIS query would be a second key space beside the
    writer's, and would equate spellings the fold does not.

    #12120 merge-attachment site: the pre-fix lookup was NARROWER than the write it
    mirrors. `_entity_names` carries the union of names the CLAIMS mention, while
    the writer resolves them on the FOLDED union (`name` · `canonical_name` ·
    `aliases`, over the raw and the folded spelling), so a claim naming
    `"the widget"` for an Object stored as `"Widget"` matched nothing here and
    the attachment was reported `unresolved` instead of being linked — a
    measurement that said "no node has that name" about a node the write door
    reaches. ⛔ The space that comes from
    `entity_identity.name_key_space_same_entity` is the **SPELLING** space only:
    unlike `resolve_or_mint`, this site is status-blind and does not collapse
    merged carriers (capture's recorded attach-to-every-carrier disposition), so a
    terminal-only holder IS linked and `unresolved` is about spelling, not about
    the writer's whole resolution (the collapse half is #12244).

    `unresolved` therefore means what it says: *no Object holds that name in the
    key space the write resolves on* (raw, folded, `canonical_name`, or a merge's
    `aliases`) — never "the raw spelling missed".

    ⚠️ **THE SUBJECT CHANNEL IS SKIPPED when the spelling is EXACT — and a
    FOLD-ONLY one is narrowed, not skipped.** `about_entities` is not object-only:
    the SUBJECT channel (team/role/person, the gated `aboutSubject` edge) also
    contributes names to it. `subject_names` carries the payload's subject-kind
    names, and the comparison is made in the writer's key space (raw or FOLDED,
    because the lookup below is fold-wide):

    * a name whose EXACT spelling is in that set is reported in `skipped_subject`
      and **never written** (F6's rule: capture's own pass skips on the exact name,
      `sdk.py::TortoiseSDK._extract_session_v2`'s `if _n in subject_entity_names`
      skip, and a write there would attach an Object under a subject's
      name — the un-gated `#4934` edge);
    * a name that only FOLD-equals one is NOT dropped: the lookup is narrowed to
      `AND o.name = $exact`, so an Object whose OWN name is the reference keeps its
      edge (F6's legitimately-cased carrier) and a FOLD-ONLY carrier does not get
      one. It is reported in `skipped_subject` only when no exact carrier exists
      (review round 16: the paragraph this replaces said such a name was "never
      written", which is the opposite of what the narrowing does, and the sibling
      sites all state the narrowed rule).

    Why the comparison is by NAME and not by "resolution failed": an `:Object` may
    legitimately share a subject's name, and the shared spelling is what makes the
    case ambiguous — so the lookup below RESOLVES there, and a write without this
    skip would attach the claim to an Object under a subject's name. Bounding the
    consequence to "lands in `unresolved`" was therefore FALSE for exactly that
    case (#12120 review round 3).

    ⚠️ **The pre-existing gap this makes VISIBLE instead of silent.** A merge
    emits no payload point, so `bind_point_subjects` never runs for the
    candidate and its confidence-gated `aboutSubject` binding is dropped — the
    binder is the only gated `aboutSubject` producer and this write has no
    binder. `skipped_subject` names those names, so the drop is REPORTED rather
    than assumed — ⚠️ **but only for subject-kind names that appear in the
    union's `about_entities`.** A participant that reaches the claim through the
    point's SLOTS is dropped UNREPORTED: the ordinary path binds it via
    `bind_point_subjects` (`sdk.py::TortoiseSDK._extract_session_v2`, the call site
    after the entity loop), a merge emits no payload point so
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
    # #12120 merge-attachment site: the writer's key space for an about-edge name. Lazy for
    # the same import-cost reason as `link_entity` above (`entity_identity`
    # pulls `entity_resolver`, and this module is on every extractor import).
    try:
        from .entity_identity import name_key_space_same_entity
    except Exception as e:  # noqa: BLE001, RUF100
        out["warnings"].append(
            f"merge attachment skipped, name key space unavailable "
            f"({type(e).__name__}: {e})")
        return out
    # The subject-channel skip below compares in the writer's key space, so it
    # folds. Local import: this module must stay importable without
    # `entity_resolver` being pulled in eagerly (see the block above).
    from .entity_resolver import normalize_entity_name as _ndm_fold
    # Capture's own skip (`sdk.py::TortoiseSDK._extract_session_v2`'s
    # `if _n in subject_entity_names`), applied here so a merge cannot write
    # an edge the SAME capture refuses to write on the normal route. The
    # comparison is made in the WRITER's key space (raw OR folded), because the
    # resolution below matches the folded union — an exact-only skip here would
    # let a fold-equivalent spelling of a subject-kind name reach a fold-equal
    # Object, which is the #1370/#4934 label leak this skip exists to prevent
    # (#12120 review round 3).
    # A bare string is ONE name — iterating it would skip single characters and
    # silently drop a one-letter Object attachment for the rest of the capture.
    if isinstance(subject_names, str):
        subject_names = (subject_names,)
    subject_skip = {str(n).strip() for n in (subject_names or ())
                    if isinstance(n, str)}
    subject_skip_folded = {_ndm_fold(n) for n in subject_skip}
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        _n = name.strip()
        if _n in subject_skip:
            # Reported, never written: the #1370 binder owns this edge and a
            # merge runs no binder. See the docstring's subject-channel note.
            out["skipped_subject"].append(_n)
            continue
        # #12120 review round 3: when this spelling only FOLD-equals a declared
        # subject-kind name, an EXACT-name Object may still be attributed (F6's
        # legitimately-cased carrier) but a fold-only one must not — that is the
        # #4934 leak this skip exists to prevent, on a spelling the pre-fix raw
        # lookup could not reach.
        _fold_only = _ndm_fold(_n) in subject_skip_folded
        try:
            # The WIDE space is kept beside the narrowed one: the reporting branch
            # below has to tell "a fold-equal carrier exists but was not narrowed in"
            # from "nothing holds this name in any spelling" (review round 13).
            _wide_space, _wide_params = name_key_space_same_entity("o", _n)
            _space, _space_params = _wide_space, _wide_params
            if _fold_only and _space:
                _space = f"({_space}) AND o.name = $exact"
                _space_params = {**_space_params, "exact": _n}
            rows = (proj.g.query(
                f"MATCH (o:Object) WHERE {_space} "
                "RETURN o.id",
                params=_space_params).result_set if _space else [])
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
                    f"MATCH (p:Point {{id:$pid}}), (o:Object) "
                    f"WHERE ({_space}) "
                    "AND (o.id IS NULL OR o.id = '') "
                    "MERGE (p)-[:aboutObject]->(o) RETURN count(*)",
                    params={"pid": point_id, **_space_params}).result_set
                out["linked"] += int(rows[0][0]) if rows and rows[0] else 0
            except Exception as e:  # noqa: BLE001, RUF100
                out["warnings"].append(
                    f"merge id-less attachment failed for {_n!r} "
                    f"({type(e).__name__}: {e})")
        elif not rows:
            if _fold_only:
                # #12120 review round 4: a fold-only match of a declared
                # subject-kind name is the SUBJECT channel's, not "no node has
                # that name" — `unresolved` promises exactly that narrower
                # meaning (and sdk forwards it as "a later backfill can link
                # it", which can never happen here). The narrowing dropped it on
                # purpose, so report it where the exact skip reports: as a
                # subject-channel skip.
                #
                # ⛔ `_fold_only` is a property of the REFERENCE, not evidence that
                # a fold-equal carrier exists (review round 13): `rows` is empty
                # both when the narrowing suppressed a real carrier AND when no
                # Object holds the name in ANY spelling, and only the first is a
                # subject-channel skip. Reporting the second as one told the
                # caller's own message (`"the binder owns the gated edge"`) a
                # claim no binder can act on, while this module's docstring
                # records that a merge runs no binder — and it dropped the name
                # from `unresolved`, the one channel documented to carry it. Ask
                # the WIDE space; a failed read asserts neither.
                try:
                    _carrier_exists = bool(
                        _wide_space and proj.g.query(
                            f"MATCH (o:Object) WHERE {_wide_space} "
                            "RETURN o.id LIMIT 1",
                            params=_wide_params).result_set)
                except Exception as e:  # noqa: BLE001, RUF100
                    out["warnings"].append(
                        f"merge attachment key-space recheck failed for {_n!r} "
                        f"({type(e).__name__}: {e})")
                    continue
                if _carrier_exists:
                    out["skipped_subject"].append(_n)
                else:
                    out["unresolved"].append(_n)
            else:
                out["unresolved"].append(_n)
    return out


__all__ = [
    "MERGE",
    "MERGE_MIN_OVERLAP",
    "MergePlan",
    "apply_merge_union",
    "near_dup_merge",
]
