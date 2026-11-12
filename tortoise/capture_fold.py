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
* **Attachment links** (`about*` — the seven predicates the capture cascade and
  the shared writer can put on a Point; see `_FOLD_ATTACHMENT_RELS`) — the
  claim's *support* — are UNIONED onto the canonical before the copy is removed, via
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
  (`uncarried_source_ref`) — a Point holds ONE scalar `extractedFrom`, so a
  difference there is refused rather than overwritten (writing the duplicate's
  source over the canonical's would clobber provenance to save provenance).
  §4.3 does NOT foreclose the write: `update_point` is a lifecycle door, and the
  free-slot carry below uses it on this very row. ⚠️ This refuses the
  DIFFERENCE, not the possibility: an additive home exists (`extractedFromAll`,
  a LIST on the same Point), and carrying the difference there is the upgrade
  path that would retire this token. Until that union lands, refusing is the
  fail-closed answer — the fold loses a merge, not an edge;
* a link that does not LAND when re-written (`link_not_carried`) — counted,
  never assumed, the same rule `apply_merge_union` follows;
* evidence that cannot be READ at all (`unreadable`). Fail-closed: this
  module is about to authorise a delete, and "could not tell" is not "nothing
  there" (the distinction `extractor_v2._attachment_ids` draws with `None`);
* an address the duplicate itself does not hold VALIDLY (`malformed_address`) —
  a capture Point can carry `span_start` without `span_end`, an INVERTED pair, a
  stringly-typed offset or an out-of-range one (the span validator guards the SDK
  doors, not this seam). Writing it would raise on the canonical and blame the
  wrong node forever, so the duplicate's own `validate_span` refusal is named for
  what it is;
* an INBOUND `about*` edge — another node pointing AT the duplicate
  (`inbound_attachment`): the carry writes FROM the canonical, so such a link
  cannot be moved and the delete would orphan it;
* a link whose PREDICATE has no permitted writer for a Point
  (`uncarriable_attachment`) — the cascade can mint `aboutDocument`/
  `aboutPoint` on a Point, and neither is a §3.2 triple `link_entity` may
  write;
* a scalar slot write that did not LAND (`slot_write_failed`) — read back,
  because `update_point` on an absent id SUCCEEDS silently, and reporting the
  attempt there would delete a duplicate whose evidence never arrived;
* more than one node carrying the duplicate's id (`ambiguous_duplicate`):
  `read_fold_evidence` reads `rows[0]` while the caller's `DETACH DELETE`
  removes EVERY row with that id, so a second copy's evidence would die without
  ever having been read — "could not tell" is not "nothing there";
* an edge class this module has no way to carry (`unexpected_edge`) — the reads
  are UNFILTERED and partitioned in Python, so a session/`TAGGED`/future edge
  refuses the fold instead of being orphaned by the delete. The same posture as
  a writer-less `about*` predicate, and the reason the reads carry no rel-type
  filter (`type(r) IN [...]` answered only for the subset this module knew).
  `_PROVENANCE_RELS` (`extractedFrom`) is the one class handled on another axis:
  its content is verified as a FIELD (the ref check) rather than carried;
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
ONE `link_entity` per attachment (2 graph statements, journaled as
`EntityLinked` when the SDK has a journal), plus — only when a free slot is
actually filled — ONE `update_point`. So a fold writes N+1 times where N is the
duplicate's attachment count, and a duplicate with nothing to carry issues ZERO
writes (`test_a_duplicate_with_nothing_to_carry_issues_no_writes` pins that at
the write door, not at the receipt).

Reads per fold: three unconditional (the duplicate's props, its outbound edges,
its inbound edges) plus one per canonical view and one read-back per
`link_entity`/`_carried` pair. The widened rel set costs NO extra read — one
query covers them all.

⚠️ `update_point` is the SDK's lifecycle door, and it is used deliberately:
§4.3 forbids writing a committed row's field outside an appended lifecycle
Event, and this door appends `PointRevised`. Two consequences a later reader
must know rather than discover:

* on a journal-armed SDK TWO records are appended per fold — a `PointRevised`
  (from `update_point`, and only when a free slot was filled) and one
  `EntityLinked` per link this fold CREATED — and the capture's own Points live
  in `_InMemoryEventLog` (`durable = False`, `sdk.py`), so the journal holds
  records for ids whose `PointAdded` it never had. They are inert
  on the graph fold (the revision matches no row, the link descriptor's
  endpoint is absent) and no replay can reach them; the seam is journal-free by
  design for these points, and these are the records that step outside that. Tracked as a declared residual, not fixed
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

#: The about* predicates a Point can carry, as ONE tuple the reads derive from.
#:
#: ⚠️ SEVEN — five the capture cascade can mint plus two only the shared writer
#: can. `projection._create_about_edges` is the rel-IGNORING
#: Subject→Object→Event→Source→Point cascade (`_ABOUT_NAME_CASCADE`,
#: `projection/edges.py`), so an `aboutEntities` entry resolves to whichever
#: label owns that name — a release day named as an `:Event` mints `aboutEvent`,
#: a document name mints `aboutDocument`. Reading only two of them left the
#: others to be destroyed by the delete while the receipt said the duplicate had
#: "no evidence of its own" (measured). `aboutSource`/`aboutAction` are not in
#: that cascade at all — `session_link.link_entity` is their writer.
#: The outbound, inbound and carriable reads are all pinned against this tuple,
#: so adding an eighth predicate here cannot silently skip a read.
_FOLD_ATTACHMENT_RELS = ("aboutObject", "aboutSubject", "aboutEvent",
                         "aboutSource", "aboutAction",
                         "aboutDocument", "aboutPoint")

#: Which of them a fold is allowed to RE-WRITE. `session_link.ENTITY_LINKED_TRIPLES`
#: permits FIVE Point-source triples — `aboutSubject` (→Subject), `aboutObject`
#: (→Object), `aboutEvent` (→Event), `aboutSource` (→Source) and `aboutAction`
#: (→Point).
#:
#: ⚠️ Permitted is not the same as POSSIBLE for one of them: a `:Source` is
#: `url`-keyed and carries NO `id` (`projection._mint_source_stub`), while the
#: writer matches its target by `{id:$tid}` — so an `aboutSource` link reaches the
#: writer, finds nothing, and the fold refuses as `link_not_carried` ("did not
#: land"), NOT as `unaddressable` (an earlier version of this comment said the
#: latter; the outbound read coalesces `t.url` into the address column, so a real
#: Source is addressable and simply not addressable the way `link_entity` asks).
#: Either way it is fail-closed — nothing is lost — and it is reported, never
#: silently dropped; the module must not claim it as carried. `aboutDocument` (Point→Source) and
#: `aboutPoint` (whose only permitted direction is Event→Point) are NOT among
#: them: there is no legal writer for the duplicate's copy of those, so the fold
#: REFUSES rather than dropping it.
_CARRIABLE_ATTACHMENT_RELS = ("aboutSubject", "aboutObject", "aboutEvent",
                              "aboutSource", "aboutAction")

#: The label each carriable rel must target (the §3.2 triple's third member).
_CARRIABLE_ATTACHMENT_LABELS = {
    "aboutSubject": "Subject",
    "aboutObject": "Object",
    "aboutEvent": "Event",
    "aboutSource": "Source",
    "aboutAction": "Point",
}

#: Edges whose content the fold verifies as a FIELD rather than carrying as an
#: edge: `(Point)-[:extractedFrom]->(Source)` is the claim's PROVENANCE, and a
#: Point holds it as `extractedFrom`/`extractedFromAll` too, so the ref check
#: (`uncarried_source_ref`) is what stops the field from being lost.
#:
#: ⚠️ THE EDGE IS STILL CHECKED SEPARATELY, and the exemption's real ground is
#: the CANONICAL's own edge, not the row's field:
#:
#: * the outbound read COLLECTS every `extractedFrom` target into
#:   `FoldEvidence.provenance_edges`, and the carry refuses `unexpected_edge`
#:   when the canonical has no `extractedFrom` edge to that same target — because
#:   the field check does NOT imply the edge exists. An earlier revision granted
#:   the exemption on the FIELD and skipped the edge before the edge check could
#:   see it: an edge pointing at a source the row's field does not name was
#:   destroyed on an `ok=True` fold.
#: * the exemption is OUTBOUND-ONLY. The same line was copied onto the inbound
#:   axis, where the ground cannot hold at all — an inbound edge is not the
#:   projection of any field on the duplicate. It is an `unexpected_edge` refusal.
#:
#: So an outbound `extractedFrom` edge whose target the row's FIELD does not name
#: still proceeds when the canonical already holds that same edge, and the delete
#: then removes a link whose meaning the canonical demonstrably has. That is the
#: implemented rule, and a test distinguishes it from the field-ref mechanism.
_PROVENANCE_RELS = ("extractedFrom",)

# (There is deliberately no writer-less PREDICATE tuple here. An earlier
# revision carried one — hand-listed, then derived — while the operative test at
# the refusal site is `rel not in _CARRIABLE_ATTACHMENT_RELS`; a second spelling
# of that fact is the "constant nothing reads" shape this module criticises
# twice, and its only reader was a test asserting the partition was consistent
# with itself. The refusal site names every writer-less rel it found.)

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
#:
#: ⚠️ The DECLARED vocabulary (`_DECLARED_PROPS`) covers the capture contract
#: plus the provenance pointers; THIS set is the smaller one the fold reports
#: rather than writes. A Point's payload is OPEN-SET (`api.add_point(**fields)` persists
#: undeclared props too, #2795) — so no list can cover every field a producer
#: might attach. That bound is on what the fold can CARRY, not on what it
#: discloses: a prop outside the declared vocabulary is never carried, and it
#: is now REPORTED by name (`FoldEvidence.undeclared`, `out["undeclared"]`, and
#: the caller's receipt) rather than silently deleted with the node. Before that
#: report existed the receipt said "no per-occurrence evidence of its own to
#: carry" over a duplicate whose `author`/`source` prop was about to be
#: destroyed (measured), which is the same silence this module exists to close.
_REPORTED_FIELDS = ("confidence", "pointKind", "eventId", "status",
                    "is_operator")

#: The props the fold neither carries nor reports because they are the ROW's
#: identity or the projection's own bookkeeping rather than the occurrence's
#: evidence: `id` addresses the row, `content`/`content_hash` are the dedup key
#: the FOLD ITSELF is about, `createdAt` is first-writer history and `updatedAt`
#: moves on any later write.
#:
#: ⚠️ `is_operator` and `status` used to sit here on the ground that "`sdk`
#: refuses an operator-wired duplicate before this module is reached" — which is
#: a different fact: that probe refuses a duplicate WIRED TO an operator, and says
#: nothing about a duplicate whose OWN flag is set (`sdk`'s capture statements
#: never set it, so a producer would have to). A retracted duplicate's `status`
#: was the same hole: crossed in silence because the field was declared
#: structural. Both are now in `_REPORTED_FIELDS`, so a difference is a residual —
#: reported, never written (they are the canonical's own state).
#:
#: ⚠️ `content` is NOT "equal by construction" — that ground would be false on the
#: `rephrase_linked` arm, where the two texts DIFFER by design (`rephrase_hit`
#: links a paraphrase, which is the whole point of folding it). The ground is that
#: the difference IS the fold's premise and is reported as that dedup verdict
#: (`dedup=rephrase_linked` on the capture receipt), not as a loss: the claim's
#: occurrence TEXT is not its support, and a canonical keeps one wording.
#:
#: ⚠️ This list is the OPEN SET's boundary, so it is the one place where an
#: unknown prop becomes visible instead of lost: a prop NOT named here is
#: reported as undeclared. A system prop added upstream therefore shows up as
#: noise on the receipt (loud, harmless) rather than as a silent omission, and
#: `test_a_clean_capture_point_reports_no_undeclared_props` is what reds when
#: this list has drifted behind the projection's own writes.
#: ⚠️ `sourceVersionTransit` is the projection's own bookkeeping (`projection`
#: stamps it beside `provenanceSource` on a captured Point) — it is NOT evidence,
#: and it stays structural. `provenanceSource` is the OTHER half: it is a
#: PROVENANCE POINTER (`graph_census.PROVENANCE_POINTER_PROPS` names exactly
#: `("provenanceSource", "extractedFrom")`), so it is read as a REF — a
#: differing one refuses the fold like any other source reference, and an equal
#: one is silent.
_STRUCTURAL_PROPS = ("id", "content", "content_hash", "createdAt",
                     "updatedAt", "sourceVersionTransit",
                     # EP's own recompute bookkeeping, written by the projection on
                     # a Point the epistemic engine has touched (`ep_dirty`,
                     # `ep_dirty_at`). Not evidence: the flag says "recompute me",
                     # and its value is derived from the graph, not asserted.
                     "ep_dirty", "ep_dirty_at")

#: Everything the duplicate read DECLARES it knows: the occurrence address, the
#: single slots in BOTH spellings (a producer stores `when` or `validFrom`, and
#: the fold WRITES `validFrom` — declaring only the read spelling reported a
#: value the fold had just carried as an undeclared loss), the reported support
#: fields, the source refs, and the structural identity above. A prop outside
#: this union is the open set.
_DECLARED_PROPS = frozenset(
    set(_OCCURRENCE_ADDRESS)
    | {key for key, _ in _SINGLE_SLOTS}
    | {write_key for _, write_key in _SINGLE_SLOTS}
    | set(_REPORTED_FIELDS)
    | set(_STRUCTURAL_PROPS)
    #: `provenanceSource` is the OTHER provenance pointer
    #: (`graph_census.PROVENANCE_POINTER_PROPS` names exactly
    #: `("provenanceSource", "extractedFrom")`): it is DECLARED here because it is
    #: READ — as a ref, through `_refs_from` — not reported as an unknown.
    #: ⚠️ Omitting it from this union put it straight into `undeclared`, so every
    #: Point the m2 capture path mints (the projection stamps it) got a receipt
    #: saying the fold "could not carry: provenanceSource" over a value it had
    #: compared and allowed. The VGATE caught it; the stub-based drift test could
    #: not, because that producer never writes the prop.
    | {"extractedFrom", "extractedFromAll", "provenanceSource"}
)

#: The edge ATTRIBUTES the fold carries — and the ONLY attribute it READS.
#: `confidence` is carried by `link_entity` and read back by `_carried`.
#:
#: ⚠️ DECLARED BOUND, not a closed set: the graph's edge-attribute vocabulary is
#: wider (`projection/edges.py`'s `DIRECT_EDGE_ATTR_KEYS` — direction, weight,
#: label, batch_id — plus the record projection's `idx`), and an attribute outside
#: this tuple is neither carried nor REPORTED. `link_entity` accepts only
#: `confidence`, so carrying another key needs a writer change (#12146).
#:
#: ⚠️ NOR IS THE BOUND LIMITED TO `about*` LINKS — an earlier version of this
#: comment said "the only one a capture Point's links carry today" and grounded
#: that on `projection._create_about_edges` minting them bare. That is true of the
#: `about*` edges only: the PROVENANCE writer (`projection/edges.py::
#: _EdgeHandlers._link_source`) stamps `r.sourceVersion` via
#: `_anchor_on_create`, so a Point's `extractedFrom` link can carry an attribute
#: this fold does not read — and that is the one edge the fold LETS DIE whenever
#: the canonical already holds an edge to the same target. The provenance axis is
#: verified as a FIELD, which says nothing about the edge's attributes.
#: `#12146` now names this axis too; a lane reading this must not assume the
#: attribute class is closed.
_CARRIED_EDGE_ATTRS = ("confidence",)

#: The duplicate's outbound walk. ⚠️ UNFILTERED: the caller's `DETACH DELETE`
#: takes every edge, so a rel-type filter here would answer for the subset this
#: module knows and say nothing about the rest — the shape the typed-pattern
#: census was blind to. `type(r) IN [...]` was that filter; the partition is in
#: PYTHON and anything outside `_FOLD_ATTACHMENT_RELS` becomes an
#: `unexpected_edge` refusal instead of an orphaning delete.
#:
#: It is a CONSTANT because `_CARRIED_EDGE_ATTRS` is asserted against it by
#: `_assert_carried_edge_attrs`, called from `read_fold_evidence` (NOT from
#: `assert_evidence_vocabulary`, which covers a different vocabulary). The
#: attribute bound was declared and read by NOTHING — the "second constant
#: nothing reads" shape this module calls out elsewhere — so adding a read
#: without declaring it could not drift-flag.
#: `url` as well as `id`: a provenance edge targets a `:Source`, which is
#: `url`-keyed and carries NO id (`projection._mint_source_stub`), so
#: `coalesce(t.id,'')` would hand the provenance check an empty string and
#: refuse every capture Point's own `extractedFrom` edge.
_OUTBOUND_EDGES_QUERY = (
    "MATCH (d:Point {id:$dup})-[r]->(t) "
    "RETURN type(r), head(labels(t)), coalesce(t.id, t.url, ''), r.confidence")


@dataclass(frozen=True)
class FoldEvidence:
    """What ONE duplicate node holds, read off the live graph.

    ``attachments`` is ``(rel, target_label, target_id, confidence)``. A target
    with NEITHER an id nor a url is reported in ``unaddressable`` instead — there
    is nothing to name it by. A URL-ONLY target is addressable here (the read
    coalesces ``t.id, t.url``) but not by the writer, which MATCHes ``{id:$id}``
    and finds nothing, so it REFUSES as ``link_not_carried`` rather than being
    reported as unaddressable. (An earlier version of this docstring said a
    target with no id lands in ``unaddressable`` and that the writer "would mint a
    different node" — false twice: the coalesce makes it addressable, and
    `link_entity` creates nothing when the MATCH finds no node.)
    """

    attachments: tuple[tuple[str, str, str, Any], ...] = ()
    unaddressable: tuple[tuple[str, str], ...] = ()
    #: The duplicate's own labels. `DETACH DELETE` removes the NODE, so a second
    #: label (`:Point:Object`) goes with it — and a label is not a property, so
    #: `undeclared` could never see it. Read, and REPORTED as a residual.
    labels: frozenset[str] = frozenset()
    #: Source-ref values that were present but BLANK (whitespace). Not refs, and
    #: not nothing: `extractedFrom` is declared, so an empty ref set passes the
    #: ref check and the value would otherwise be destroyed with the node.
    blank_refs: tuple[str, ...] = ()
    source_refs: frozenset[str] = frozenset()
    scalar: dict[str, Any] = field(default_factory=dict)
    #: The duplicate's own span evidence is NOT a valid address — the reason is
    #: `commit_schema.validate_span`'s, verbatim (a half pair, an inverted pair,
    #: a non-integer offset, out of range). A capture Point can hold any of them:
    #: `validate_span` guards the SDK write doors, not the EventAPI seam. Writing
    #: one would raise on the canonical and refuse the fold forever under a
    #: misleading token, so it is detected here and named for what it is.
    malformed_address: str | None = None
    #: ``(rel, source_label)`` pairs for `about*` edges pointing AT the
    #: duplicate. `link_entity` writes FROM the canonical, so an inbound edge
    #: cannot be carried — the delete would orphan it.
    inbound: tuple[tuple[str, str], ...] = ()
    #: `_REPORTED_FIELDS` the duplicate holds (``{field: value}``). The fold
    #: never writes these onto the canonical — they are its own state — but a
    #: difference is REPORTED, because crossing one in silence is how the
    #: receipt came to say "no evidence of its own" over a duplicate whose
    #: `confidence`/`pointKind` disagreed (measured).
    support: dict[str, Any] = field(default_factory=dict)
    #: Props the duplicate carries that this module DECLARES no knowledge of
    #: (`_DECLARED_PROPS`'s complement — a producer's `author`, a future field).
    #: They are not carried (there is no declared slot to carry them into) and
    #: they are about to be destroyed with the node, so they are REPORTED by
    #: name: the open set #2795 permits is exactly the set a fold cannot see,
    #: and naming it is the difference between a loss and a silent loss.
    undeclared: tuple[str, ...] = ()
    #: Slots the DUPLICATE itself holds twice with different values (one slot,
    #: two spellings — `when`/`validFrom`). Reported as residuals by the carry:
    #: the fold cannot carry both and must not cross the difference in silence.
    slot_conflicts: tuple[str, ...] = ()
    #: The target identities of the duplicate's OUTBOUND `_PROVENANCE_RELS` edges
    #: (`id`, or `url` for a `:Source`). Whether one may die with the node is the
    #: CANONICAL's question — it is exempt only when the canonical carries the
    #: same edge — so the read collects them and the carry decides.
    provenance_edges: tuple[str, ...] = ()
    #: `(rel, direction)` for every edge the duplicate carries OUTSIDE
    #: `_FOLD_ATTACHMENT_RELS` (`direction` is `"out"` or `"in"`). The delete
    #: takes EVERY edge, so an unfiltered read is the only way to know nothing
    #: else was orphaned; the fold REFUSES rather than dropping one (a `TAGGED`
    #: or session edge is not reconstructible here), which is the same posture
    #: the writer-less predicates take.
    unexpected_edges: tuple[tuple[str, str], ...] = ()
    #: More than one node carries the duplicate's id. The reads below answer for
    #: `rows[0]`, while the caller's `DETACH DELETE` removes EVERY row with that
    #: id — so a second copy's evidence would be destroyed without being read.
    #: `sdk`'s capture ids are ULIDs (unique), but a point id is not unique in
    #: general (`_refuse_inverted_point_window` records the fan-out as a tested
    #: shape), so the carry refuses rather than trusting the read.
    ambiguous: bool = False


def _refs_from(scalar_ref: Any, all_refs: Any, prov_source: Any = None,
               blanks: list[str] | None = None) -> set[str] | None:
    """The provenance refs a row holds, from ONE normaliser.

    ⚠️ A string is a SEQUENCE, so `*(all_refs or ())` iterated a malformed
    `extractedFromAll="session:a#t2"` CHARACTER-WISE — the duplicate read refused
    the fold over the ref `':'`, and the canonical read (same loop, unguarded)
    made the receipt claim the canonical did not carry a ref that is exactly what
    it held. Fail-closed both times, and wrong both times. A str is the ONE ref it
    is; anything else that is not a list/tuple is not a ref list at all (``None``
    = the row cannot be read).

    ONE function because the two reads must agree: a normaliser applied to only
    one of them is how the second copy of a bug survives a fix.
    """
    if isinstance(all_refs, str):
        all_refs = [all_refs]
    elif all_refs is not None and not isinstance(all_refs, (list, tuple)):
        return None
    refs: set[str] = set()
    for ref in (scalar_ref, prov_source, *(all_refs or ())):
        if ref is None:
            continue
        # ⚠️ A non-string ELEMENT is the same malformed-evidence class as a
        # non-list container, and it used to be dropped in silence: the value is
        # in `_DECLARED_PROPS` (so it never reaches `undeclared`), it is not a
        # ref this set holds (so it never reaches a residual), and the duplicate
        # was deleted with it. Refuse instead — "could not read" is not "nothing
        # there".
        if not isinstance(ref, str):
            return None
        if not ref.strip():
            # ⚠️ A blank ref is not a ref, but it is not NOTHING either: the row
            # DECLARES `extractedFrom`, so the ref check passes (an empty set is
            # a subset of anything), the fold succeeds, and the value dies with
            # the node — neither carried, nor a residual, nor `undeclared`. The
            # caller collects it (`blanks`) and reports it.
            if blanks is not None:
                blanks.append(repr(ref))
            continue
        refs.add(ref.strip())
    return refs


#: The evidence columns, in ONE place: the duplicate's read and the canonical's
#: read must select the same list in the same order, or the occupancy checks
#: compare different columns.
_EVIDENCE_SELECT = (
    "MATCH ({alias}:Point {{id:$id}}) "
    "RETURN {alias}.extractedFrom, {alias}.extractedFromAll, {alias}.quote, "
    "       {alias}.span_start, {alias}.span_end, {alias}.source_turn_id, "
    "       {alias}.search_keys, {alias}.when, {alias}.validFrom, "
    "       {alias}.eventId, {alias}.status, {alias}.is_operator, "
    "       {alias}.provenanceSource")


def _assert_carried_edge_attrs(query: str) -> None:
    """The edge-attribute bound must be READ by something, or it cannot drift-flag.

    `_CARRIED_EDGE_ATTRS` was declared and read by NOTHING — the "second
    constant nothing reads" shape this module calls out elsewhere — so a new
    attribute read added to the query, or a carried attribute dropped from it,
    would leave the declaration drifting in silence.

    ⚠️ The query arrives as an ARGUMENT, never by naming `_OUTBOUND_EDGES_QUERY`
    here: the census derives an every-relationship-type SITE for any symbol whose
    statement references that constant, and this function only inspects the TEXT
    (no query is ever issued) — a shape the register has no honest slot for. The
    call site is `read_fold_evidence`, which owns §13 row 42 for that walk.
    """
    read = {name for name in _CARRIED_EDGE_ATTRS if f"r.{name}" in query}
    if read != set(_CARRIED_EDGE_ATTRS):
        raise AssertionError(
            "#9437: `_CARRIED_EDGE_ATTRS` declares attributes the outbound read "
            f"does not select: {sorted(set(_CARRIED_EDGE_ATTRS) - read)}")
    if "r.confidence" not in query:                   # non-vacuity
        raise AssertionError(
            "#9437: the outbound read selects no edge attribute at all, so the "
            "declaration above is unmeasurable")


def assert_evidence_vocabulary() -> None:
    """Assert this module reads every field the capture contract declares.

    The evidence slots are a hand-copy of `sdk._CAPTURE_PASSTHROUGH_ORDER` (the
    E3/E4 capture surface), and a copy with no guard silently DRIFTS: a field
    added to the capture contract would not be read here, so the `DETACH DELETE`
    would destroy it — precisely the failure this module exists to stop. The
    sibling read-back (`sdk._capture_passthrough_read_fields`) generates its
    field list from that tuple for the same reason; this asserts coverage
    instead, so the read SELECT can stay a literal Cypher string (a generated
    one would also have to be a generated `_EVIDENCE_SELECT`, and the canonical
    read must select the SAME list in the SAME order).

    ⚠️ The import is FUNCTION-LOCAL on purpose: `sdk` imports this module at its
    fold site, so a module-level `from .sdk import …` here would be the cycle.
    Deferring it to call time costs one dict lookup and keeps the direction one-
    way. (The earlier wording claimed the import was avoided altogether "because
    importing `sdk` here would be a cycle" — two lines above the import.)
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
    # ⚠️ DECLARED is not SELECTED. Declaring a field is what keeps it out of
    # `undeclared`; only the SELECT puts it in front of this module. A field
    # added to both `_CAPTURE_PASSTHROUGH_ORDER` and `_OCCURRENCE_ADDRESS` but not
    # to `_EVIDENCE_SELECT` therefore passed the check above, was never read, was
    # never carried and never reported — and the read itself would fail to unpack
    # a 13-column row. This half is the one that makes "reads every field the
    # contract declares" true.
    declared |= {write_key for _, write_key in _SINGLE_SLOTS}
    unselected = sorted(k for k in declared if f".{k}" not in _EVIDENCE_SELECT)
    if unselected:
        raise AssertionError(
            "#9437: this fold DECLARES evidence fields its read does not "
            f"SELECT, so they are neither carried nor reported: {unselected}. "
            "Add them to `_EVIDENCE_SELECT` (both reads share it).")


def read_fold_evidence(proj: Any, dup_id: str) -> FoldEvidence | None:
    """Read the duplicate's evidence, or ``None`` when it cannot be read.

    ``None`` (not an empty :class:`FoldEvidence`) is the fail-closed answer:
    "this node holds nothing" and "the read failed" are different facts, and
    only the first may authorise a delete. The caller REFUSES the fold on
    ``None`` rather than treating an unreadable node as an empty one.
    """
    _assert_carried_edge_attrs(_OUTBOUND_EDGES_QUERY)
    try:
        rows = proj.g.query(
            _EVIDENCE_SELECT.format(alias="d")
            + ", d.confidence, d.pointKind, properties(d), labels(d)",
            params={"id": dup_id}).result_set
    except Exception:
        return None
    if not rows:
        return None
    if len(rows) > 1:
        # `DETACH DELETE n` by id takes EVERY row with that id; this read answers
        # for one. Refuse rather than authorise a delete over rows it never saw.
        return FoldEvidence(ambiguous=True)
    (scalar_ref, all_refs, quote, span_start, span_end, source_turn_id,
     search_keys, when, validfrom, _event_id, _status, _is_operator,
     prov_source, _confidence, _point_kind, _props, _labels) = rows[0]
    blank_refs: list[str] = []
    refs = _refs_from(scalar_ref, all_refs, prov_source, blank_refs)
    if refs is None:
        return None
    # The open set: what the node carries that this module does not read. Read
    # from the node itself (`properties(d)`), never derived from the producer's
    # payload — the payload is the superset the projection SELECTS from. A read
    # that returns no map at all is `()`: an absent map is not an undeclared
    # field, and inventing one would put a phantom loss on every receipt.
    undeclared_props = tuple(sorted(
        str(key) for key in (_props or ()) if str(key) not in _DECLARED_PROPS))
    #: ⚠️ A label is neither a prop nor an edge: it is part of the NODE the delete
    #: removes, and `properties(d)` cannot see it. A map-less read is `()`.
    labels = frozenset(str(lab) for lab in (_labels or ()))
    scalar: dict[str, Any] = {}
    for key, value in (("quote", quote), ("span_start", span_start),
                       ("span_end", span_end), ("source_turn_id", source_turn_id),
                       ("search_keys", search_keys),
                       ("when", when if when is not None else validfrom)):
        # Presence is ownership (#5004's rule, applied to evidence): an empty
        # string is a value the producer wrote, not an absence to fill.
        if value is not None:
            scalar[key] = value
    # ⚠️ ONE slot, TWO spellings — on the DUPLICATE side too. `when` and
    # `validFrom` are one date, and a row holding BOTH with different values used
    # to collapse to `when` before any comparison, so the second value was
    # neither carried, nor a residual, nor undeclared (both spellings are
    # declared): the receipt then said the duplicate had "no per-occurrence
    # evidence of its own to carry" over a date that died with it. The canonical
    # side of this defect was closed earlier; this is the same rule applied to
    # the node the fold DELETES.
    slot_conflicts: list[str] = []
    if (when is not None and validfrom is not None and when != validfrom):
        # ⚠️ NAME the value that travels. Without it the receipt could print
        # "copied validFrom" (true of the spelling the write loop kept) and "did
        # not carry validFrom (as when)" (true of the other) in one sentence
        # about ONE slot, and a reader cannot tell which date went with the node.
        # ⚠️ The value that travels is `when`: `scalar["when"]` is built as
        # `when if when is not None else validfrom`, so on a row holding BOTH it
        # is `when` that reaches the writer and `validFrom` is the one the
        # collapse drops. Naming them the other way round is the same lie in
        # reverse — the first draft of this clause named `validFrom` as the
        # traveller and a test asserting the WRITTEN value caught it.
        # ⚠️ The SUBJECT is the SECOND SPELLING, not the field: the sinks prefix
        # every residual with "did not carry ", and a subject of `validFrom`
        # produced "copied validFrom; did not carry validFrom (as when)" — the
        # contradiction this clause exists to remove, one level up. `validFrom`
        # WAS carried (as `when`); what did not survive is the duplicate's second
        # spelling.
        slot_conflicts.append(
            f"the duplicate's second validFrom (as when) spelling — it held "
            f"both ({when!r} and {validfrom!r}) for one slot, so only "
            f"{when!r} travelled; {validfrom!r} went with the node")

    # The span is an ADDRESS, and "half an address" is only ONE of the ways a
    # duplicate's own evidence can be unwritable. `validate_span` is the ONE home
    # of the rule the SDK's write doors already apply (E4 / #5007), and the
    # EventAPI→projection passthrough does NOT run it — so an inverted pair, a
    # stringly-typed offset or an out-of-range one are all shapes a capture Point
    # can hold. Calling it here names them as the DUPLICATE's malformed evidence;
    # without it they surface as `slot_write_failed` and the refusal blames the
    # CANONICAL — the mis-attribution `malformed_address` exists to prevent.
    from .commit_schema import validate_span

    try:
        validate_span(span_start, span_end)
        malformed_address = None
    except ValueError as exc:
        malformed_address = str(exc)

    try:
        edges = proj.g.query(_OUTBOUND_EDGES_QUERY,
                             params={"dup": dup_id}).result_set
    except Exception:
        return None
    attachments: list[tuple[str, str, str, Any]] = []
    unaddressable: list[tuple[str, str]] = []
    unexpected: list[tuple[str, str]] = []
    provenance_targets: list[str] = []
    for rel, label, target_id, confidence in edges:
        if not isinstance(rel, str) or not isinstance(label, str):
            return None
        if rel in _PROVENANCE_RELS:
            # COLLECTED, not exempted: whether this edge may die depends on the
            # CANONICAL (does it have the same edge?), which this read cannot
            # answer. An exemption granted here on the FIELD alone was false twice
            # over — it skipped the edge before the ref check could see it, and
            # the field's presence does not imply the canonical's edge exists.
            provenance_targets.append(target_id.strip())
            continue
        if rel not in _FOLD_ATTACHMENT_RELS:
            unexpected.append((rel, "out"))
            continue
        # ⚠️ For a CARRIABLE predicate the label is a pure function of the PREDICATE
        # (§3.2's triple), not of `head(labels(t))` — which is order-dependent and
        # would hand the writer the wrong target label (or trip the writer-less
        # check) on a target that carries more than one label. The graph's own head
        # is kept only for the predicates that have no permitted writer, whose
        # message names what it found.
        label = _CARRIABLE_ATTACHMENT_LABELS.get(rel) or label
        if isinstance(target_id, str) and target_id.strip():
            # ⚠️ The RAW value: converting a non-numeric grading to `None` made
            # `_carried`'s comparison (`grading != confidence`) skip it, so the
            # duplicate's grading died on an `ok=True` fold with no clause. It is
            # reported as a residual instead, and the WRITE door still receives a
            # number (a string passed there would be a type error deep in a
            # writer).
            attachments.append((rel, label, target_id.strip(), confidence))
        else:
            unaddressable.append((rel, label))
    try:
        # Unfiltered for the same reason as the outbound read above. The type
        # slot used to be a `|`-joined alternation of `_FOLD_ATTACHMENT_RELS`.
        inbound_rows = proj.g.query(
            "MATCH (s)-[r]->(d:Point {id:$id}) "
            "RETURN type(r), head(labels(s))",
            params={"id": dup_id}).result_set
    except Exception:
        return None
    inbound: list[tuple[str, str]] = []
    for rel, source_label in inbound_rows:
        # ⚠️ FAIL CLOSED on a malformation, mirroring the outbound arm above.
        # The guard here used to be `if isinstance(rel, str) and isinstance(
        # source_label, str):` with NO else, and `head(labels(s))` is None for a
        # label-less source node — so that row was neither `inbound` nor
        # `unexpected`, and the edge died on an `ok=True` fold with no clause
        # naming it. The two arms disagreed about the same query shape.
        if not isinstance(rel, str) or not isinstance(source_label, str):
            return None
        # ⚠️ NO provenance exemption on this axis: an INBOUND edge is not the
        # projection of any field on the duplicate, so the "verified as a field"
        # ground cannot hold — and it was copied here anyway.
        if rel in _FOLD_ATTACHMENT_RELS:
            inbound.append((rel, source_label))
        else:
            unexpected.append((rel, "in"))

    _observed = {"confidence": _confidence, "pointKind": _point_kind,
                 "eventId": _event_id, "status": _status,
                 "is_operator": _is_operator}
    # ⚠️ `is_operator` False is the DEFAULT, not evidence of its own: reporting
    # it would put a residual on every fold where the canonical does not carry
    # the flag at all. Only a truthy flag is a fact worth comparing (a status,
    # by contrast, is reported whenever the duplicate holds one).
    support = {k: v for k, v in _observed.items()
               if k in _REPORTED_FIELDS
               and v is not None and (k != "is_operator" or v)}
    return FoldEvidence(
        attachments=tuple(attachments),
        unaddressable=tuple(unaddressable),
        source_refs=frozenset(refs),
        labels=labels,
        blank_refs=tuple(blank_refs),
        scalar=scalar,
        malformed_address=malformed_address,
        inbound=tuple(inbound),
        support={k: v for k, v in support.items() if v is not None},
        slot_conflicts=tuple(slot_conflicts),
        provenance_edges=tuple(provenance_targets),
        undeclared=undeclared_props,
        unexpected_edges=tuple(unexpected),
    )


def _canonical_view(proj: Any, canonical_id: str) -> dict | None:
    """The canonical's evidence-bearing slots + source refs, or ``None``.

    ``None`` covers BOTH "the row could not be read" and "the id is not
    unique" — the same fan-out the duplicate's `ambiguous_duplicate` arm refuses
    for. The asymmetry was a false PASS: the slot write addresses the ID
    (`update_point` is `MATCH (n:Point {id:$id}) SET n += $props`), so on a
    canonical id carried by two rows the occupancy check would read `rows[0]`
    while the write landed on both — a first-writer value overwritten on a row
    the fold never read. `_carried` addresses the id too, so its count could
    exceed the links written. A fan-out is therefore not a view.
    """
    try:
        rows = proj.g.query(
            _EVIDENCE_SELECT.format(alias="c") + ", c.confidence, c.pointKind",
            params={"id": canonical_id}).result_set
    except Exception:
        return None
    if not rows or len(rows) > 1:
        return None
    (scalar_ref, all_refs, quote, span_start, span_end, source_turn_id,
     search_keys, when, validfrom, _event_id, _status, _is_operator,
     _prov_source, _confidence, _point_kind) = rows[0]
    refs = _refs_from(scalar_ref, all_refs, _prov_source)
    if refs is None:
        return None
    try:
        # The canonical's OWN provenance edges. A provenance edge on the
        # duplicate may only die if the canonical holds the same one — the field
        # says which source the row names, not that an edge to it exists.
        prov_rows = proj.g.query(
            "MATCH (:Point {id:$id})-[:extractedFrom]->(s) "
            "RETURN coalesce(s.id, s.url, '')",
            params={"id": canonical_id}).result_set
    except Exception:
        return None
    prov_targets = {str(r[0]).strip() for r in prov_rows if r and r[0]}
    return {
        "refs": refs,
        "quote": quote, "span_start": span_start, "span_end": span_end,
        "source_turn_id": source_turn_id, "search_keys": search_keys,
        "when": when, "validFrom": validfrom,
        "confidence": _confidence, "pointKind": _point_kind,
        "eventId": _event_id,
        "status": _status, "is_operator": _is_operator,
        "prov_targets": prov_targets,
    }


def _already_written(out: dict) -> str:
    """Name what a REFUSED-AT-THE-END carry has already ESTABLISHED.

    A refusal is not always a no-op: the free-slot write is atomic and happens
    first, so a failure in the link loop refuses with those slots (and any
    earlier link) already on the canonical. Reporting that is the difference
    between "kept distinct" and "kept distinct — and the canonical gained one
    link" (measured: a reader treats the first as "nothing changed").

    The RESIDUALS are reported here too, for the same reason: they are computed
    before the write, so a late refusal would otherwise drop the differences the
    carry had already found — the one class of fact this module never crosses in
    silence.
    """
    added = []
    if out.get("copied"):
        added.append("copied " + ", ".join(out["copied"]))
    # ⚠️ `created`, NOT `carried`: `carried` counts links PRESENT (which includes
    # ones the canonical already held — `link_entity` short-circuits those), and
    # reporting them as "added" would assert a change this fold did not make.
    if out.get("created"):
        added.append(f"{out['created']} attachment link(s)")
    said = ""
    if added:
        said = ("; it had ALREADY added " + " and ".join(added)
                + " to the canonical — additive, nothing removed from either node")
    if out.get("residual"):
        # ⚠️ NOT "kept the canonical's own …": for a support field the canonical
        # may hold NOTHING (`confidence` absent), and then nothing was kept — the
        # duplicate's value went with the node. "not carried" is true in both
        # cases; the parenthetical says why (a conflicting value, or an absence).
        # Each residual item carries its own reason — see the caller's note.
        said += "; did not carry " + "; ".join(out["residual"])
    # ⚠️ The CALLER filters on the text of `blocked` rather than on a marker:
    # this line already prints the residual here, and the seam appends the
    # residual too (a refusal can be reached before any write, when this line is
    # not printed at all) — so two clauses about one fact must not both appear on
    # the arm whose whole purpose is telling an operator what changed. A private
    # marker key would break the wrapper's fixed key set, which is itself pinned.
    return said


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
    if _CARRIABLE_ATTACHMENT_LABELS.get(rel) != label:
        # Both halves are read from the graph itself; the allowlist is a
        # fail-closed backstop against interpolating an unvetted label into a
        # pattern (the same posture `session_link.link_entity` takes).
        return 0, None
    # ⚠️ Membership, not `head(labels(t))`: a target carrying more than one
    # label would not have the expected label FIRST, and the caller reads a 0 as
    # "the link did not land" — a refusal whose stated reason is false, over a
    # link that `link_entity` did create.
    rows = proj.g.query(
        f"MATCH (:Point {{id:$canon}})-[r:{rel}]->(t {{id:$target}}) "
        "WHERE $label IN labels(t) "
        "RETURN count(*), head(collect(r.confidence))",
        params={"canon": canonical_id, "target": target_id,
                "label": label}).result_set
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
        out = _carry_fold_evidence(proj, dup_id, canonical_id, sdk=sdk)
        # ⚠️ ONE place, so every arm is de-duplicated: two links of the same
        # predicate to different targets (or one residual reached by two paths)
        # must not print the same sentence twice on the receipt.
        out["residual"] = list(dict.fromkeys(out["residual"]))
        return out
    except Exception as e:
        # ⚠️ The SAME key set as every other arm — the docstring's invariant is
        # executable, not aspirational: the capture seam indexes these keys, so a
        # missing one would raise `KeyError` inside the loop this wrapper exists
        # to protect.
        return {"ok": False, "reason": "internal_error",
                "blocked": ("the evidence carry failed "
                            f"({type(e).__name__}: {e})"),
                "carried": 0, "created": 0, "copied": [], "residual": [],
                "undeclared": []}


def _carry_fold_evidence(proj: Any, dup_id: str, canonical_id: str, *,
                         sdk: Any = None) -> dict:
    """Move the duplicate's own evidence onto the canonical. NEVER raises.

    Call this BEFORE the copy is removed; a refusal means the caller must NOT
    delete. Returns::

        {"ok": bool,             # False ⇒ refuse the fold, keep both nodes
         "reason": str | None,   # machine-readable refusal token
         "blocked": str | None,  # the same refusal in words, for the receipt
         "carried": int,         # attachment links PRESENT after the write
         "created": int,         # …of which THIS fold wrote (rest pre-existed)
         "copied": [keys],       # free scalar slots filled from the duplicate
         "residual": [keys],     # slots/fields the canonical already filled
         "undeclared": [keys]}   # props the duplicate held and this fold does
                                 # not know (open set, #2795) — REPORTED, never
                                 # carried, so the caller says what it dropped

    ⚠️ BOTH the success and the never-raise refusal return this EXACT key set:
    the capture seam indexes `blocked` on every refusal and `carried`/`created`/
    `copied`/`residual` on every success, and the module's own guards index
    `created` while a link is still in flight — so a refusal missing a key would
    raise `KeyError` inside the capture loop, the very failure the wrapper exists
    to prevent.

    ``residual`` is a REPORT, never a refusal: two occurrences of one claim
    legitimately carry different quotes, and a Point holds one value per
    field. ``reason`` is the narrow set the module docstring enumerates.
    """
    out: dict = {"ok": False, "reason": None, "blocked": None,
                 "carried": 0, "created": 0, "copied": [], "residual": [],
                 "undeclared": []}
    evidence = read_fold_evidence(proj, dup_id)
    if evidence is None:
        out["reason"] = "unreadable"
        out["blocked"] = ("the duplicate's evidence could not be read, so a "
                          "fold cannot be shown to lose nothing")
        return out
    # Set on EVERY arm, refusals included: a refused fold also tells the caller
    # what the node holds that this module cannot carry, and a receipt that
    # reported it only on success would go quiet in exactly the case where the
    # operator is deciding whether to force the fold.
    out["undeclared"] = list(evidence.undeclared)
    # The duplicate's OWN one-slot-two-values conflicts: reported, never carried,
    # and set here so a refusal discloses them too.
    out["residual"].extend(evidence.slot_conflicts)
    # A second LABEL on the duplicate dies with the node and nothing carried it:
    # `update_point` writes properties, never labels. Reported, so the receipt
    # names what went with it.
    extra_labels = sorted(evidence.labels - {"Point"})
    if extra_labels:
        out["residual"].append("label(s) " + ", ".join(extra_labels))
    # A blank source ref: a declared prop whose VALUE cannot be carried anywhere
    # and whose presence in the set was the only thing the ref check could have
    # caught.
    if evidence.blank_refs:
        out["residual"].append(
            "source reference(s) that were blank: "
            + ", ".join(evidence.blank_refs))
    # 0) An edge class this module does not know. The delete takes EVERY edge, so
    #    an unfiltered read is the only way to know none was orphaned; the
    #    refusal is the same posture as a writer-less `about*` predicate.
    if evidence.unexpected_edges:
        named = ", ".join(sorted({f"{rel} ({direction}bound)"
                                   for rel, direction in evidence.unexpected_edges}))
        out["reason"] = "unexpected_edge"
        out["blocked"] = (f"it carries edge(s) this fold has no way to carry "
                          f"({named}), and the delete would orphan them")
        return out
    if evidence.ambiguous:
        out["reason"] = "ambiguous_duplicate"
        out["blocked"] = ("more than one node carries its id, and the fold's "
                          "delete would take the evidence of the copies this "
                          "carry never read")
        return out
    canonical = _canonical_view(proj, canonical_id)
    if canonical is None:
        out["reason"] = "unreadable"
        out["blocked"] = ("the canonical this claim resolved to could not be "
                          "read as a single row (absent, unreadable, or an id "
                          "carried by more than one node), so its evidence "
                          "could not be checked")
        return out

    # 1) A link whose target has neither an id nor a url cannot be re-pointed:
    #    the writer addresses its target by `{id:$id}` only, and `link_entity`
    #    MATCHes both endpoints (it MINTS nothing — an earlier version of this
    #    comment said it would create a different node, which the
    #    `FoldEvidence.attachments` docstring already corrects).
    if evidence.unaddressable:
        rels = ", ".join(sorted({rel for rel, _ in evidence.unaddressable}))
        out["reason"] = "unaddressable"
        out["blocked"] = (f"its {rels} link(s) name no addressable target, so "
                          "they cannot be carried")
        return out

    # 2) A source reference the canonical does not carry is a node FIELD
    #    (`extractedFrom`) holding the canonical's OWN provenance: writing the
    #    duplicate's ref there would CLOBBER the first source, not add the
    #    second, and there is no second slot for it. That is the operative
    #    reason (not "a fold may never write a field" — the free-slot carry below
    #    writes fields on this very row, through the lifecycle door).
    missing = sorted(evidence.source_refs - canonical["refs"])
    if missing:
        out["reason"] = "uncarried_source_ref"
        out["blocked"] = ("its source reference "
                          f"{missing[0]!r} is not one the canonical carries, "
                          "and `extractedFrom` is ONE slot holding the "
                          "canonical's own source — writing the duplicate's "
                          "would clobber it, not add it")
        return out

    # 3) The duplicate's own span evidence may be an address `validate_span`
    #    refuses — a HALF pair, an INVERTED pair, a non-integer offset, or one
    #    out of range (a capture Point can hold any of them: the span validator
    #    guards the SDK write doors, not the EventAPI seam). That is the
    #    duplicate's own malformed evidence, not a write failure on the canonical
    #    — named here, with the validator's own reason, so the receipt does not
    #    blame the wrong node, and refused before any write so a refused fold
    #    stays a no-op.
    if evidence.malformed_address:
        out["reason"] = "malformed_address"
        out["blocked"] = ("its span evidence is not a valid address "
                          f"({evidence.malformed_address}), so it is not a "
                          "value the fold can carry and not one it may drop")
        return out

    # 4) A link the ONTOLOGY gives a Point no writer for cannot be moved at all
    #    — refuse, before anything is written, rather than dropping it. (The
    #    cascade can mint `aboutDocument`/`aboutPoint` on a Point; neither is a
    #    permitted §3.2 triple for a Point source, so `link_entity` cannot
    #    re-create it.)
    # ⚠️ ONE list decides: the COMPLEMENT of `_CARRIABLE_ATTACHMENT_RELS`, tested
    # right here. A second, hand-listed or derived tuple of the same fact was the
    # "constant nothing reads" shape — its only reader was a test asserting it
    # matched its own input.
    writer_less = sorted({rel for rel, _label, _target, _conf
                          in evidence.attachments
                          if rel not in _CARRIABLE_ATTACHMENT_RELS})
    if writer_less:
        out["reason"] = "uncarriable_attachment"
        out["blocked"] = (f"its {', '.join(writer_less)} link(s) are predicates "
                          "a Point has no permitted writer for, so they cannot "
                          "be put on the canonical")
        return out
    # 4b) An INBOUND about* edge (another node pointing at the duplicate) is not
    #     carriable at all: `link_entity` writes FROM the canonical, so the
    #     delete would orphan a relation the duplicate is the TARGET of. Only a
    #     non-Point source can hold one (`aboutPoint` is Event→Point), so this is
    #     the shape `Event -[:aboutPoint]-> Point`.
    if evidence.inbound:
        rel, label = evidence.inbound[0]
        out["reason"] = "inbound_attachment"
        out["blocked"] = (f"a {label} points AT it with {rel!r}, and a fold can "
                          "only carry links the canonical is the SOURCE of, so "
                          f"that link would be orphaned ({len(evidence.inbound)} "
                          "inbound link(s))")
        return out

    # 2b) A provenance EDGE whose target the canonical has no edge to. The ref
    #     check above establishes a FIELD; this establishes the EDGE, which is
    #     what the delete actually removes. Exempting the edge on the field alone
    #     (the earlier shape) claimed "the canonical carries it" from a fact that
    #     does not imply it.
    # ⚠️ `if t` (an earlier shape) SKIPPED an address-less provenance target —
    # `coalesce(t.id, t.url, '')` is empty when a `:Source` is keyed some third
    # way — so that edge died on an `ok=True` fold with no clause naming it: the
    # ref check reads the FIELD, the edge check had nothing to compare, and the
    # delete took the edge. Unaddressable is a refusal here, exactly like an
    # unknown rel.
    orphaned = sorted({t for t in evidence.provenance_edges
                       if not t or t not in canonical["prov_targets"]})
    if orphaned:
        out["reason"] = "unexpected_edge"
        out["blocked"] = ("it carries a provenance edge to "
                          f"{orphaned[0]!r} that the canonical has no edge to, "
                          "and the delete would orphan it")
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
    #    or not at all. It runs before the links so that ITS failure leaves the
    #    graph untouched; a LATE failure (a link that will not land) can still
    #    leave this write behind, which is why the refusal discloses it.
    to_write: dict[str, Any] = {}
    # 6a) the occurrence ADDRESS — all or nothing (see `_OCCURRENCE_ADDRESS`).
    address_held = any(canonical.get(k) is not None for k in _OCCURRENCE_ADDRESS)
    for key in _OCCURRENCE_ADDRESS:
        if key not in evidence.scalar:
            continue
        if address_held:
            held_value = canonical.get(key)
            if held_value != evidence.scalar[key]:
                # ⚠️ "the canonical's value differs" was printed for a part the
                # canonical does not hold AT ALL: the address is all-or-nothing,
                # so a canonical holding only `quote` reports the duplicate's
                # `span_start` as dropped while its own `span_start` does not
                # exist. The occupancy is part of the fact.
                reason = ("the canonical's value differs"
                          if held_value is not None else
                          "the canonical holds no value for this part of the "
                          "address")
                out["residual"].append(f"{key} — {reason}")
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
                # Named as the SLOT, not the read spelling: `when` and `validFrom`
                # are one slot, and a receipt saying "when" over a row whose
                # value lives on `validFrom` sends the reader to a field the
                # node does not hold.
                out["residual"].append(
                    (key if key == write_key else f"{key} (as {write_key})")
                    + " — the canonical's value differs")
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
                f"({type(e).__name__}: {e}) — the raise does not say WHICH "
                "side refused it: a value the write door will not store is the "
                "DUPLICATE's own malformed evidence (its span has its own "
                "`malformed_address` refusal; the other slots have no "
                "per-slot validity door), while a refusal from the canonical's "
                "own write would be the other case")
            # Nothing landed (the write raised), but the RESIDUALS were already
            # computed — disclose them here too, or a raise after a differing
            # `pointKind` crosses that difference in silence, the class round 5
            # fixed on the other two arms.
            out["blocked"] += _already_written(out)
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
            # A PARTIAL landing is exactly the case this read-back exists to
            # catch, and a refusal must say what DID land — otherwise "kept
            # distinct" reads as "nothing changed" over a node that gained a
            # slot.
            out["copied"] = sorted(
                k for k, v in to_write.items()
                if after is not None and after.get(k) == v)
            out["blocked"] = (
                "the canonical could not be read back after the evidence "
                "write, so the carry is unproven" if after is None else
                "its " + ", ".join(unlanded)
                + " evidence did not land on the canonical")
            out["blocked"] += _already_written(out)
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
            created = link_entity(
                proj, "Point", canonical_id, target_id,
                edge_type=rel, target_label=label, sdk=sdk,
                # ⚠️ A malformed grading is DISCLOSED above and never handed to
                # the writer, whose parameter is typed as a number.
                confidence=(confidence
                            if isinstance(confidence, (int, float)) else None))
            # ⚠️ COUNTED HERE, before the read-back: `created` is what the WRITER
            # reported, and the read-back below can RAISE. Counting after it left
            # `created: 0` on a refusal that had already added the link, so the
            # disclosure said "nothing changed" over a canonical that gained one
            # (measured on a real graph with `_carried` patched to raise).
            if created:
                out["created"] += 1
            present, grading = _carried(proj, canonical_id, rel, label,
                                        target_id)
        except Exception as e:
            # ⚠️ The read-back is INSIDE the try on purpose: a raise here used to
            # escape to the never-raise wrapper, which builds a FRESH dict — so
            # the partial report (slots already copied, links already written)
            # was discarded and the receipt read as "nothing changed".
            failures.append(f"{rel}→{target_id} ({type(e).__name__}: {e})")
            continue
        if not present:
            # The writer said it created the edge and the read-back does not see
            # it: it did NOT land, so it must not be counted as created either.
            if created:
                out["created"] -= 1
            failures.append(f"{rel}→{target_id} (did not land)")
            continue
        out["carried"] += 1
        # ⚠️ The LINK is carried; its GRADING may not be. `link_entity`
        # short-circuits a pre-existing edge without re-grading it (#1370),
        # so when the canonical already held this link the duplicate's
        # `confidence` is still on the duplicate — about to be deleted. That
        # is reported, never claimed as carried.
        if confidence is not None:
            if not isinstance(confidence, (int, float)):
                # Named with the VALUE: "confidence of X→Y" over a string says
                # nothing an operator can act on.
                out["residual"].append(
                    f"confidence of {rel}→{target_id} (not a number: "
                    f"{confidence!r})")
            elif grading != confidence:
                # ⚠️ Names the TARGET, not just the label: two graded links to
                # different targets emitted the identical residual, so the
                # receipt could not say which link's grading died.
                out["residual"].append(f"confidence of {rel}→{target_id}")
    if failures:
        out["reason"] = "link_not_carried"
        # The COUNT first: naming three of seven is a partial fact, and the rest
        # of this module counts what it cannot name (`unexpected_edges` names
        # every one, `created`/`carried` are counted, never assumed).
        shown = "; ".join(failures[:3])
        more = "" if len(failures) <= 3 else f" (and {len(failures) - 3} more)"
        out["blocked"] = (f"its {len(failures)} attachment link(s) could not be "
                          f"carried onto the canonical: {shown}{more}"
                          + _already_written(out))
        return out

    out["ok"] = True
    return out


__all__ = ["FoldEvidence", "assert_evidence_vocabulary",
           "carry_fold_evidence", "read_fold_evidence"]
