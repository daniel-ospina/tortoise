"""EventAPI — the shared append surface both producers write through.

Attribution (`initiated_by`, `agent_id`) is bound once at construction, so the
extractor gets initiated_by="extractor" and a user-agent gets "user" without
per-call bookkeeping (see README Interaction model).

Every mutation appends exactly one event to the log AND (if a projection is
attached) applies it incrementally, so the store never drifts from the log.
"""
from __future__ import annotations

import logging

from . import record_writer
from .idempotency import IngestKey, IngestResult
from .ids import now_iso, ulid

# #9572 review P2: "nothing was named" (absent OR blank) has ONE predicate,
# shared with the SDK doors and the hosted surfaces — see the leaf module.
from .kind_fallbacks import named_kind

# #2782: the money value's WRITE gate, at the one seam that bypasses
# `_sanitize_props`. `money` imports only `numeric_domain` (no cycle).
from .money import refuse_malformed_money_in_payload

# #11915: the percent shape's gate at the same seam — a payload walk with the
# same engagement rule, so a ratio typed as a percentage cannot land ungraded
# through a record-shaped door.
from .percent import refuse_malformed_percent_in_payload

# #7951 §17.1: the live-write scope the journal-first doors open around the fold.
from .projection import EVIDENCE_FOLD_KEY, _live_write_scope, fold

# #5140: the `documents` METER KEY's defender for THIS producer. `quota` is a
# leaf module (stdlib-only at import time), so this adds no cycle.
from .quota import refuse_document_writer_demotion

logger = logging.getLogger(__name__)

#: The journal's SERVER-MANAGED embedding fields, in ONE spelling.
#: #11835 review finding 1 (P2): `_emit` is a journal door in its own right, and
#: it used to enforce neither half of the vector contract its own sibling
#: `add_point` does — it did not DECLARE the width of a vector that reached the
#: envelope, and it did not refuse a caller-supplied marker.
#:
#: ⛔ Two harms, and the second is the one the seam's contract forbids. (1) A new
#: record built through `add_event(**extra)` / `emit_belief(**payload)` carried
#: an `embedding` with `embedding_width` ABSENT, and `embeddings.py::
#: JOURNAL_VECTOR_WIDTH_KEY` reserves *absent* for PRE-CHANGE / FOREIGN records
#: — so the marker could no longer be read as "this record predates #11821".
#: (2) A caller could pass `embedding_width="f32"` beside an off-grid float64
#: vector and the marker LIED, because no stamp ran here to re-decide it. It is
#: the door a later width/base64 migration would trust, which is exactly the
#: population #5004 round-7 says must never be narrowed silently.
#:
#: The keys are the VECTOR's description — width, storage form, and the origin
#: attestation — none of which a caller can mint: they are server-derived here
#: (`stamp_journal_embedding`) exactly as they are in `add_point` and in
#: `sdk._sanitize_props`. `embeddingText` is NOT in this set: it is the enriched
#: TEXT (a payload field, not a marker), and `add_point` refuses it with its own
#: reason.
#:
#: ⛔ AND THE RULE IS ONE NESTING LEVEL DEEP (#11835 review round 4, F1). The
#: two gates below read the ENVELOPE, which is safe only while a record-shaped
#: payload is DOOR-BUILT — `add_point`/`add_operator` hand `_emit` their record
#: POSITIONALLY (rendered onto the envelope as `point`). `add_point` also stamps
#: it (with `creating=True`, so the origin attestation rides a creating record's
#: own basis); `add_operator`'s record carries NO vector to attest — `_point`
#: builds one only for the operator-less, string-content path — so there is no
#: marker on it to lie about. A CALLER that builds one bypasses both gates, and
#: the funnel must not "just stamp it" to close that: `creating=False`
#: DROPS `embedding_model`/`embedding_revision`/`embedding_text_hash`, which would
#: strip the attestation off every ordinary `PointAdded` (R1). So the nested
#: shape is REFUSED, at `_emit` itself, via `_NESTED_RECORD_KEYS` below — a gate
#: at the individual doors was tried first and was measurably incomplete.
_SERVER_MANAGED_EMBEDDING_KEYS = frozenset({
    "embedding_width",
    "embedding_verbatim",
    "embedding_preserved",
    "embedding_model",
    "embedding_revision",
    "embedding_text_hash",
})

#: The record facts a CALLER must not set on `_emit`, on EITHER channel: no seam
#: call in this module ever MINTS them, so a value can only be a caller's, and
#: each one corrupts something of its own — which is why they are listed by harm
#: rather than as one "marker family". ("Of its own", not "a DIFFERENT thing":
#: the three source-ref keys below SHARE one harm, which is why they share one
#: bullet. An earlier revision said "a different thing" of all seven, and the
#: list under it contradicted that immediately.)
#:
#:   • `embedding_preserved` — payload-only metadata; the entry at
#:     ``#5004 round-3`` note on the ``"embedding_preserved"`` entry (its words:
#:     "Never a node property — the node's vector identity is not a fact about
#:     the node") says exactly that. ⛔ What it does NOT do on this door is suppress an attestation, and
#:     an earlier draft of this comment claimed exactly that (found independently
#:     by two reviewers, `#11948` round 2): NEITHER channel of ``_emit`` attests
#:     a record — the positional one stamps only a TOP-LEVEL ``embedding`` (so
#:     ``payload["point"]`` never reaches ``stamp_journal_embedding``), and the
#:     keyword one stamps with ``creating=False``, whose else-arm POPS the
#:     triple whatever this key says. The suppression it names is real, but it
#:     lives at ``sdk._write_capture_turns`` (``creating=True``), a different
#:     door. Here the key is refused because it is a SERVER-OWNED fact no seam
#:     call in this module mints: a caller's value asserts a precedent capture
#:     that never existed, nothing on this door can contradict it, and it becomes
#:     load-bearing the moment anything re-stamps the record.
#:   • `embedding_verbatim` — ⛔ NOT payload-only, and saying it was is the
#:     same over-claim this guard was added to fix. Its entry is
#:     ``projection/entities.py``'s ``"embedding_verbatim"``, under the
#:     ``#5004 round-3`` note that starts "a DECLARED node property (own SET
#:     clause below)" — the entry's OWN note, not the identity triple's above
#:     it: a DECLARED node property with its own SET clause, because a later re-emit
#:     (``promote_point`` &c.) reads the point back and must carry the storage
#:     form. A forged one therefore writes ``n.embedding_verbatim = true`` AND
#:     flips the journal's storage form — the node and the record both.
#:   • `evidence_fold` — the replay's recreate anchor, and it forges from BOTH
#:     channels (so neither is the safe one to drop): a flat record carries the
#:     key at the top level, and on the positional channel ``_norm`` splices the
#:     ``point`` record over the envelope on replay, so the nested flag is read
#:     identically — ``projection.record_is_evidence_fold`` returns True for
#:     ``{"type": ..., "evidence_fold": True}`` AND for
#:     ``{"type": ..., "point": {"evidence_fold": True}}`` (measured). Either way
#:     the decay plan and the terminalizer stamp diverge from the live tree.
#:     `sdk._sanitize_props` calls it a HARD-REJECT key for that reason (its own
#:     ``if "evidence_fold" in props:`` arm, beside the ``embedding_preserved``
#:     precedent it follows).
#:   • `embeddingText` — the enriched text the vector is computed from; a
#:     caller-set one forges the text↔vector identity the ``#8488`` hash attests.
#:   • `extractedFromAll`, `sourceVersion`, `sourceVersions` — the source-ref
#:     set and its anchors. Their harm is the one `add_point`'s own note records
#:     (``_forged_keys``): a caller-supplied value puts the payload's edges AHEAD
#:     of the ``:Source`` resolution this seam performs, so the record asserts
#:     provenance the graph does not have. ``extractedFromAll`` is
#:     ``_POINT_HANDLED`` (``projection/entities.py``) and is what
#:     ``_point_all_source_refs`` reads.
#:
#: ⛔ `sourceVersionTransit` is deliberately NOT in the set although it looks
#: like a sibling: `add_point` MINTS it (its ``_sv`` arm, guarded by
#: ``if _sv is not None``), from ``_source_version_transit``
#: (``projection/edges.py``), so it is a seam fact on the channel that carries it
#: rather than a caller's claim. (``_point`` does not mention the key at all — an
#: earlier draft of this comment cited a ``pairs`` arm there that does not
#: exist.) ⛔ Cited by SYMBOL, not by line: two review cycles in a row found this
#: reference stale because the code above it grows on every commit.
#:
#: `#11948`-review finding at `ceae1cfcf4`: the comment here said "the marker
#: family is verified", which was true of ONE key out of six — measured,
#: ``_emit("PointAdded", {..., "embedding_preserved": True, "embedding_model":
#: "not-a-real-model"})`` journalled the lie.
#:
#: ⛔ Scoped to ``_emit``'s OWN two channels, and NOT because these are harmless
#: elsewhere: the SDK's writers legitimately set `embedding_preserved` (the
#: evidence-fold snapshot), `embedding_verbatim` (`create_point(embedding=...)`
#: and every `get_point`-backed re-emit — `promote_point`, `DedupeRecorded`,
#: `DedupeRejected`, the mitigation `PointAdded`), `evidence_fold` (the fold
#: writer) and `embeddingText` (the capture snapshot). Those all go through
#: ``sdk._emit_event``'s own snapshot channels (``point=`` for the fold and the
#: graph lane, ``turns=`` for the record-first capture lane), which append to
#: their own log and NEVER call ``EventAPI._emit`` — a different DOOR, not a
#: different channel of this one.
#: Which is why this is a separate set rather than
#: `_SERVER_MANAGED_EMBEDDING_KEYS`. The overlap is two keys
#: (`embedding_verbatim`, `embedding_preserved`), and it is a HALF-overlap, not a
#: duplicate: the KEYWORD channel already refused those two through
#: `_SERVER_MANAGED_EMBEDDING_KEYS`, while on the POSITIONAL channel nothing did
#: — measured at `main` (`31b29c3c`), both were accepted and journalled there:
#: that arm's vector guard is `_verify_record_vector_width`, which at that head
#: returned early on any declaration other than `f32` (it did NOT check whether
#: the width was one the seam mints — that enumeration is added by this PR, see
#: its own note) and then compared a `f32` only to the values. So these two
#: entries ADD that half. The other four
#: members of the older set are VERIFIED (`embedding_width`) or deliberately left
#: to `add_point` (the identity triple) — ⛔ ON THE POSITIONAL CHANNEL ONLY: an
#: identity key in the KEYWORD payload is refused by `_forged_markers`
#: (`_SERVER_MANAGED_EMBEDDING_KEYS`), which is the guard that runs first there,
#: while the positional record is the one that cannot tell the seam's stamp from
#: a caller's. Which is why folding the sets together
#: would assert a treatment those four do not get.
#: ``EVIDENCE_FOLD_KEY`` is the READER's spelling (``projection/__init__.py``),
#: imported here rather than retyped: a retyped literal would leave this guard
#: refusing a name nobody reads after a rename at the consumer, and letting the
#: new one through — a fail-open drift, not a style point. (``add_point``'s
#: ``_forged_keys`` still spells it out; the arm that backstops a consumer-side
#: rename on THAT path is ``_emit``'s record-channel
#: ``_refuse_uncallerable_record_keys``, which reads this constant — ⛔ NOT the
#: funnel below, which ``add_point`` never calls (#11996 review round 16: this
#: note named the funnel, and the funnel's own docstring says the opposite).
#: That copy is the one to convert next, not a second authority.)
_UNCALLERABLE_RECORD_KEYS = frozenset({
    "embedding_verbatim",
    "embedding_preserved",
    EVIDENCE_FOLD_KEY,
    "embeddingText",
    "extractedFromAll",
    "sourceVersion",
    "sourceVersions",
})

#: The record facts a DOOR MINTS into the positional record it hands `_emit`
#: — the one EXEMPTION FROM `_UNCALLERABLE_RECORD_KEYS` above, and it is neither
#: a channel name nor a signal from the door (#11996 review rounds 9 and 15,
#: each measured). `_emit` cannot tell a record `add_point` built from one a
#: caller built: both arrive as `record[0]`. Scoping the exemption by CHANNEL
#: therefore made it forgeable by any direct `_emit` caller:
#:
#:     _emit("PointAdded", {"id": "p1", "content": "c",
#:                          "sourceVersionTransit": [["src:1", "h-forged"]]})
#:       -> LANDED; `projection.entities._point_source_transit` consumed it as the
#:          `:Source` read-version anchor, which is the harm the set gives for
#:          `sourceVersion` / `sourceVersions`
#:
#: ⛔ AND SO DID EVERY "MINT SIGNAL" IDEA — a flag on the call (`seam_mints=True`)
#: is a keyword the caller can just pass. What ships is not a signal at all: on
#: the RECORD channel the value is DERIVED and compared (see `_emit`'s derive
#: arm), so a caller can only supply the truth, and on EVERY OTHER channel the
#: key is refused outright. This set is the key-name half of that arm; there is
#: no door-supplied proof to look for.
_DERIVE_ON_RECORD = frozenset({"sourceVersionTransit"})

#: The envelope's RECORD-SHAPED payload keys — the shape a record door BUILDS
#: (`add_point` / `add_operator`), which is why a KEY of this name may never
#: arrive from a caller.
#:
#: ⛔ `_emit` therefore carries the record payload on a POSITIONAL channel
#: (`*record`, rendered onto the envelope as `point`), and refuses this key in
#: its keyword payload. That is a security property, not style — an earlier
#: attempt refused it at the doors that splat a caller's kwargs instead, and
#: asserted the enumeration was complete. It was not: FOUR doors reach `_emit`
#: with caller keys — `add_event(**extra)` and `emit_belief(**payload)` splat
#: onto the envelope, `add_object(**props)` does the same, and
#: `add_point(**fields)` splats INSIDE the record payload. Measured at
#: `6aea53bb6`, where the first two were refused and the last two were not:
#:
#:     add_object("n", "thing", point={"id": "p-hijack", ...off-grid f64,
#:                                       "f32" marker})
#:       -> journalled `ObjectRegistered`; the replay's `_norm` SPLICES a dict
#:          `point` into the record, so the fold MERGEs an OBJECT (not a point)
#:          on the CALLER's id — journal `Points []`,
#:          `Objects [['p-hijack','n',None,None]]`. The `f32` marker never
#:          becomes a node property: it stays on the journal line, and an
#:          Object's vector is derived from its NAME.
#:       ⛔ AND a second route, which is why the fix closes the CHANNEL rather
#:          than enumerating names: `_norm` splices from the NESTED dict, so a
#:          `point={"type": "PointAdded", ...}` SHADOWS the envelope's own
#:          `type` (see the same hazard at `FalkorProjection.apply`) — the fold
#:          dispatches on `PointAdded` and folds the caller's point, while the
#:          JOURNALLED type stays `ObjectRegistered`.
#:     add_point("real", prov, point={...})
#:       -> journalled `PointAdded` carrying the caller's record NESTED inside
#:          its own, and returned the id of a point that never folds
#:
#: ``(**kwargs)`` cannot be enumerated safely — a door added tomorrow re-opens
#: it. Moving the same hole one name over does not help either: a `record=` kwarg
#: (measured) cannot bind `*record`, so it merely lands on the envelope as an
#: inert key — the channel itself stays empty, which is the property being
#: bought. A positional argument cannot be reached by a kwargs splat at all, so
#: the gate below is total: whatever a caller names,
#: `point` in the keyword payload is refused, and `point` inside the positional
#: one is refused. `add_event` / `emit_belief` / `add_object` still refuse it
#: at their own doors, for a message that names the door the caller actually
#: called.
#:
#: ⛔ AND THE SAME HOLE, ONE NAME OVER (#11948): `**props` splats onto the
#: ENVELOPE as well, where `type=` RE-TYPES the record and `event_id=` /
#: `ts=` / `initiated_by=` / `agent_id=` rewrite its identity. That family is
#: refused at `_emit` on BOTH of its channels and at the passthrough doors —
#: see `_ENVELOPE_KEYS` below, and the POSITIONAL arm's own note for why one
#: channel was not enough.
_NESTED_RECORD_KEYS = frozenset({"point"})

#: The journal ENVELOPE names — the shape `_emit` itself builds, not a payload.
#: #11948: a payload key that collides with one of these does not ADD a field,
#: it REWRITES the record's identity. `_emit` builds
#: `{"event_id", "ts", "type", "initiated_by", "agent_id", "corrects",
#: **payload}`, so a caller `type=` re-TYPES the record
#: (`add_object(..., type="PointAdded")` journalled a `PointAdded` no record
#: door built, and the replay folds by
#: `ev["type"]`), and `ts` / `initiated_by` / `agent_id` / `actor_user_id`
#: forge who wrote the record and when. The collision — not `point=` — is what
#: lets a caller re-type a record, and it was open at every splatting door.
#:
#: ⛔ `event_id` is deliberately NOT in this set: `add_document(event_id=...)`
#: pins the envelope id on purpose (#125, with `#167`'s None default pinned by
#: `tests/test_api.py`), and `add_document` takes NAMED parameters rather than
#: splatting a caller's dict, so refusing it here would reverse a recorded
#: decision instead of closing a door. The passthrough doors refuse it below.
#:
#: ⛔ AND ON THE POSITIONAL RECORD TOO — `_emit` refuses this family on BOTH of
#: its channels. The first fix refused it in the KEYWORD payload only, on the
#: reasoning that the record belongs to `add_point` / `add_operator`. But the
#: replay's `_norm` is `{**ev, **ev["point"]}`: a caller keyed `type=` inside
#: the record is spliced OVER the envelope, so it is not a payload field there
#: either — it is the record's identity on every replay. Measured at
#: `95b98e6cf1`: `add_point("hello", prov, type="PointDeleted")` was ACCEPTED,
#: journalled `"type":"PointAdded"` with the caller's `"type":"PointDeleted"`
#: nested inside, returned a point id, and the replay dispatched on
#: `PointDeleted` — an arm that matches nothing, so the point was lost SILENTLY
#: (`ts=` / `initiated_by=` / `event_id=` correspondingly forged the replay's
#: when / by-whom / id). One channel is never enough: the money gate's own note
#: below states the rule this broke.
_ENVELOPE_KEYS = frozenset({
    "type",
    "ts",
    "initiated_by",
    "agent_id",
    "actor_user_id",
})

#: …and the envelope names a PASSTHROUGH door can set obliquely. `_emit` binds
#: `corrects` and `actor` as KEYWORDS, so a caller's `corrects=` filed under
#: `add_event(**extra)` never reaches the payload check — it lands in the
#: envelope's provenance edge directly. `add_object` can also name an
#: `event_id` (its `id=` is the OBJECT's id, not the record's).
_ENVELOPE_KEYS_AT_PASSTHROUGH = _ENVELOPE_KEYS | {
    "event_id",
    "corrects",
    "actor",
}

#: …and the envelope name a RECORD must never carry: `projection_version`.
#: The doors set it themselves as a KEYWORD (`add_point` / `add_operator` pass
#: `projection_version=2`), so a value inside the RECORD is always a caller's —
#: and it is the one envelope key `_norm`'s splice acts on semantically rather
#: than as identity: the fold reads the record's value and skips the `#49`
#: context strip a v2 stop-write exists to perform (#11835 review round 9, P3).
#: Kept OUT of `_ENVELOPE_KEYS` on purpose — the RECORD doors pass it as a
#: keyword (`add_point` / `add_operator` both pass `projection_version=2`), so a
#: shared presence refusal would refuse their own call. ⛔ TWO DIFFERENT GUARDS refuse
#: it, and naming the wrong one costs a reader 1,200 lines (#11996 review round 20,
#: measured): the RECORD channel's refusal is `_emit`'s OWN
#: `_refuse_caller_named_envelope_keys(record[0], …, record=True)` — called in `_emit`
#: itself, so this note cites the SYMBOL and not a line number, which rot: it said
#: `~1545` while the call sat at `1553` in the same revision. NOT
#: `_refuse_caller_named_funnel_keys` — that function has three call sites, all of
#: them a PASSTHROUGH DOOR's keyword payload (`emit_belief` / `add_object` /
#: `add_event`), and its own docstring says the fourth splatting door `add_point`
#: never calls it. So: the RECORD channel refuses it, the three doors refuse it on
#: their KEYWORD payload, and both were wrong in earlier wording — "only the RECORD
#: channel refuses it" was false, and attributing the record half to the funnel was
#: false too (#11996 review rounds 18 and 20, measured: `projection_version=1` is
#: refused by `add_event`, `add_object` and `emit_belief`, journalled=0, and
#: `_emit("PointAdded", {"id": "p", "content": "c", "projection_version": 0})` is
#: refused by the `_refuse_caller_named_envelope_keys(…, record=True)` call in
#: `_emit`). ⛔ `_emit`'s OWN
#: keyword channel cannot refuse PRESENCE for the reason above, so it refuses the
#: TYPE instead (see
#: the `projection_version` arm there): an exact `int` is the producers' own value,
#: and the `int`-1-bypasses-the-context-strip case stays the stated trade-off.
_RECORD_ENVELOPE_EXTRA = frozenset({"projection_version"})

def _refuse_non_str_keys(fields, channel: str) -> None:
    """Refuse a caller-supplied key that is not an EXACT `str`, with its own reason.

    ⛔ THE KEYS ARE WRITTEN BY VALUE, THE HASH-MEMBERSHIP GUARDS READ THEM BY
    HASH (#11996 review round 8, measured). A `str` SUBCLASS whose `__hash__`
    differs from `str`'s passes every hash-membership test
    (`k in frozenset(...)`, `set(keys) & forbidden`), and before this arm
    existed it was journalled under the name it claimed:

        class K(str):
            def __hash__(self): return 0x5f3a1
        k = K("evidence_fold")
        k == "evidence_fold"              # True
        k in frozenset({"evidence_fold"}) # False  ← what the guards asked
        json.dumps({k: True})             # '{"evidence_fold":true}'  ← what lands

    Measured at that head: `_emit("PointAdded", {"id": "p1", "content": "c", K("evidence_fold"): True})`
    LANDED and `projection.record_is_evidence_fold` read it as a fold; the same
    carrier re-typed a record (`K("type")`) and re-identified one (`K("event_id")`),
    and it works on the keyword payload too (Python keeps the subclass in `**`).
    This is refused BEFORE any family guard so each of those guards keeps keeping
    its own, true reason: a key that is not an exact `str` is not a name any of
    them can classify, and the encoder does not write it as one.
    ⛔ AND THIS ARM IS NOT THE LAST LINE OF DEFENCE — say what it actually buys
    (#11996 review round 17, measured). The `_named_from_caller` belt refuses a
    non-`str` key by type too, so deleting this arm does NOT let a carrier land
    (measured: 0 records written). What it buys is the DIAGNOSIS: the belt reports
    ANY non-`str` key as a member of its own family, so without this arm
    `K("myCustomProp")` is told it is a `RECORD-SHAPED journal payload` — a name it
    does not have — while with it the caller is told the true reason. That is the
    whole difference, and it is why the message names the hash-membership arms
    rather than claiming the key would otherwise land.
    """
    for key in fields:
        if type(key) is not str:
            raise ValueError(
                f"{channel} carries a key that is not an exact `str` "
                f"({type(key).__name__}): the journal writes a key by its STRING "
                f"VALUE, while the HASH-membership arms here (`_forged_envelope`, "
                f"`_forged_markers`, `add_point`'s `_forged_keys`) read it by hash "
                f"and so cannot classify it. It is refused HERE, before them, so "
                f"the reason given is the true one — deeper in it is still refused, "
                f"but by whichever family guard reaches it first, which reports ANY "
                f"non-`str` key as one of its own (measured: `myCustomProp` called a "
                f"RECORD-SHAPED journal payload), misnaming the problem (#11948). "
                f"Pass plain `str` keys.")


def _named_from_caller(keys, forbidden) -> list[str]:
    """The names in `keys` this funnel owns — by VALUE, or by a non-exact TYPE.

    ⛔ NOT A MEMBERSHIP TEST ALONE (#11996 review round 8, measured). Every guard
    here used `set(keys) & forbidden` or `k in frozenset(...)`, which is a HASH
    lookup, while the journal writes a key by its STRING VALUE (`json.dumps`). A
    `str` subclass whose `__hash__` differs from `str`'s therefore passes the
    guard and is journalled under the name it claims:

        class K(str):
            def __hash__(self): return 0x5f3a1
        k = K("evidence_fold")
        k == "evidence_fold"              # True
        k in frozenset({"evidence_fold"}) # False  ← what the guards asked
        json.dumps({k: True})             # '{"evidence_fold":true}'  ← what lands

    Measured: `_emit("PointAdded", {"id": "p1", "content": "c", K("evidence_fold"): True})`
    LANDED, and `projection.record_is_evidence_fold` read it as a fold; the same
    carrier re-typed a record (`K("type")`) and re-identified it (`K("event_id")`).
    So a non-`str` key is refused whatever it claims to equal, and a real `str`
    is refused by the value the encoder will write — the same rule this file
    already applies to the declared width ("exact type AND value").
    """
    return sorted({str(k) for k in keys
                   if type(k) is not str or k in forbidden})


def _refuse_uncallerable_record_keys(fields: dict, type_: str, channel: str) -> None:
    """Refuse the record facts a caller cannot supply to `_emit`, on either channel.

    ⛔ The WIDTH rules below describe `_emit`'s POSITIONAL RECORD ONLY, and the
    scope matters: on the keyword channel and at all three passthrough doors a
    width is REFUSED OUTRIGHT — any declared width, a truthful `f32` included, is
    a server-managed journal field there (`_forged_markers`), so no merged view is
    consulted and no value is checked. On the positional record the width is not
    refused but ENUMERATED AND VERIFIED (the seam mints it —
    see `_verify_record_vector_width`): a spelling outside the seam's own two
    widths (`f32`, `f64`) is refused; a declaration of EITHER width is refused
    when the merged view has no vector for it, and when the value is one the
    seam would DROP (with ONE measured residual, an implausibly LONG vector
    under `f32`, stated as the residual where it is implemented); and a declared
    `f32` is then checked against the values. A declared `f64` is truthful about
    the values it can DESCRIBE — it forgoes narrowing — which says nothing about
    whether one exists. The keys in
    `_UNCALLERABLE_RECORD_KEYS` are refused on BOTH channels — the positional
    record AND the keyword payload — and each for its OWN reason rather than
    under one "marker family" banner: stating them as one family is what made
    the previous comment false. See that set's comment for the harm of each, and
    the citation for each.

    ⛔ BOTH CHANNELS, because neither is the safer one: a flat record carrying
    ``evidence_fold`` at the top level is read as a fold by
    ``projection.record_is_evidence_fold``, and a positional record carrying it
    nested under ``point`` is read identically on replay (``_norm`` splices
    ``point`` over the envelope) — measured, one caller-settable key flipped the
    decay plan AND the terminalizer stamp, i.e. ``derived = replay(journal)``
    diverged from the live tree.

    Deliberately silent on the identity triple (`embedding_model`,
    `embedding_revision`, `embedding_text_hash`): a creating `_point` stamps
    those, so the positional channel cannot distinguish the seam's value from a
    caller's. That residual is `add_point`'s to refuse (its `_forged_keys`
    already does), and it is stated here rather than hidden behind a blanket
    claim.

    ⛔ And ONE key outside the set is DERIVED rather than refused on this
    channel (#11996 review rounds 6, 9 and 10, each measured): a record may carry
    `sourceVersionTransit` only if re-deriving it from the graph reproduces it.
    `add_point` mints it into the record it hands `_emit`, and `_emit` cannot tell
    that record from a caller-built one — every trust signal (`channel`, a
    `seam_mints` boolean) was measured to be caller-suppliable, so the check is a
    PREDICATE no caller can supply: recompute the `:Source` read-version and
    refuse a mismatch. On every other channel the key is refused outright.
    """
    forged = _named_from_caller(fields, _UNCALLERABLE_RECORD_KEYS)
    if forged:
        raise ValueError(
            f"the `{type_}` {channel} does not take "
            f"{' or '.join(f'`{k}`' for k in forged)} from a caller: "
            "server-minted record facts are not a caller's to set (a "
            "caller-set one forges what the record claims about its vector, its "
            "provenance, or its replay)")
    # ⛔ AND THE ONE KEY EXEMPTED FROM THAT SET IS EXEMPT ONLY HERE ON THE RECORD
    # (#11996 review round 6, measured). `sourceVersionTransit` is not in
    # `_UNCALLERABLE_RECORD_KEYS` because `add_point` MINTS it — into its
    # POSITIONAL record (`p["sourceVersionTransit"] = _sv`, from
    # `_source_version_transit`). THAT is the channel the mint makes a seam fact.
    # Applied to both channels, the exemption leaked: no seam call mints the key
    # on the KEYWORD payload, so a caller could name it there and the replay
    # consumes it as the `:Source` read-version anchor — the exact harm the set
    # gives for `sourceVersion` / `sourceVersions`, one key over:
    #
    #     _emit("PointAdded", id="p1", content="c", extractedFrom=["src:1"],
    #           sourceVersionTransit={"src:1": "h-forged"})   -> LANDED
    #     _emit("PointAdded", id="p1", content="c", sourceVersion="x")
    #                                                        -> REFUSED
    #
    # So the exemption is scoped to the RECORD channel that mints it. A door
    # (`add_event` / `add_object` / `emit_belief`) passes its own keyword payload
    # through here, so it is refused everywhere a caller can reach.
    if channel != "record door" and _named_from_caller(
            fields, frozenset({"sourceVersionTransit"})):
        raise ValueError(
            f"the `{type_}` {channel} does not take `sourceVersionTransit` from "
            "a caller: the only call that mints it is `add_point`, into its own "
            "RECORD, so on this channel a caller-set value is a forged "
            "`:Source` read-version anchor (the harm `sourceVersion` / "
            "`sourceVersions` are already refused for).")


def _int_is_float_exact(value: int) -> bool:
    """Is `value` EXACTLY the float the seam will read it as (and can it be one at all)?

    The element arm in `_verify_record_vector_width` needs this total: the seam
    reads a vector element with `float(x)` while the journal's JSON encoder
    writes the `int` verbatim, so a value the float conversion ROUNDS is a
    disagreement between two readers of one number. `float()` is undefined for
    the whole `int` range, though — above ~1.8e308 it raises `OverflowError` —
    and an unhandled exception is not the documented refusal (#11996 review
    round 12, measured: `embedding=[2 ** 2000]` under `f32` raised
    `OverflowError: int too large to convert to float`). A value with no float
    to be read as is not exactly representable, which is the same verdict the
    rounding case gets.
    """
    try:
        return float(value) == value
    except OverflowError:  # 2 ** 2000: no finite float, so not exact
        return False


def _verify_record_vector_width(record: dict, type_: str, payload: dict) -> None:
    """A width declared on `_emit`'s POSITIONAL record must be the SEAM's answer.

    ⛔ NOT A REFUSAL OF THE FAMILY — and NOT a no-op either: this function
    VERIFIES the WIDTH, and refuses only a WIDTH — a spelling the seam cannot
    mint, a non-`str` declaration, a declaration over no vector (either width),
    a declaration over a value the seam can only DROP, a declaration over a
    vector carrying a NUMERIC SUBCLASS (the seam and the JSON encoder read such
    an element by different paths — measured, `_is_shortest_f32_text`'s
    `[float(x) for x in vector]` vs the C encoder), or, for `f32` alone, one the
    values contradict. (The one DROP reason it does not reach under `f32`,
    an implausibly long sequence, is a stated residual where it is
    implemented.) Which keys that
    leaves alone is measured, not assumed: the record doors legitimately carry
    the WIDTH and the IDENTITY triple, because each stamps its own payload
    through `stamp_journal_embedding` (``_point`` with ``creating=True``;
    ``add_point`` with ``creating=False`` after popping a stale width). Refusing
    any of those four on this channel reds the width suite — the reviewer who
    found the gap proposed exactly that — and WHICH records it reds is not the
    same for all four, measured: the WIDTH rides EVERY vector-bearing
    ``PointAdded`` (`_point` stamps it on both arms), while the identity triple
    rides only the `_point`-COMPUTED vector (`stamp_journal_embedding`'s
    ``creating=False`` POPS all three, ``tortoise/embeddings.py``), so a caller
    vector's record carries none of them. (``add_operator``'s record is the one
    that carries neither: `_point` builds a vector only on the operator-less,
    non-empty-content path, so there is no marker on it to verify or to lie —
    see the header above.)

    ⛔ AND THE OTHER TWO ARE *REFUSED* — but NOT BY THIS FUNCTION, and not by the
    same guard on both channels. They are in `_UNCALLERABLE_RECORD_KEYS` because
    no seam call here mints either (measured: `_point` stamps the width and the
    identity triple only), and both are ownership claims no predicate on the
    values can derive. On the POSITIONAL record they are refused by
    `_refuse_uncallerable_record_keys`, which runs before this function. On the
    KEYWORD payload they are refused EARLIER still, by the vector-family arm
    (`_forged_markers` → "server-managed journal fields"), so
    `_refuse_uncallerable_record_keys` is unreachable for those two names there —
    this PR's own pins assert the two DIFFERENT messages per channel for exactly
    that reason. Stating which guard owns which key
    matters: the paragraph above, read alone, invites the reading that the whole
    marker family rides this channel — which is the over-claim this file has now
    had to correct three times, one level out.

    So this VERIFIES instead. The harm the marker rule exists to prevent is a
    marker that LIES — a declared ``f32`` over values it does not describe,
    which a later width/base64 migration trusts (#5004 round-7). The decision is
    a pure function of the values, so re-derive it by asking the SEAM'S OWN TWO
    QUESTIONS about ``f32`` (is the vector its shortest-round-trip text? is it on
    the float32 grid?) — see the re-stamp note further down. What is NOT done is
    the tidier-looking version of the same idea, copying the record WITHOUT its
    declared width and re-running `stamp_journal_embedding`: that OVER-BLOCKS,
    because the seam's ``f32`` idempotency arm reads the marker itself, so
    stripping it re-decides ``f64`` for every already-narrowed payload. The
    ``⛔ THE PREDICATE IS … NOT a re-stamp`` paragraph further down carries that
    measurement.

    Measured before this guard, through the private channel only:

        _emit("PointAdded", {"id": "p1", "content": "c",
                             "embedding": [0.1234567890123, 1/3],
                             "embedding_width": "f32"})
          -> journalled, values [0.1234567890123, 0.3333333333333333],
             declared `f32`   ← the marker LIE, on the one channel built to be
                                unreachable from any door

    ⛔ THE VALUES ARE READ FROM THE MERGED VIEW, NOT FROM THE RECORD ALONE, and
    that is the second half of this guard (#11835 review round 9, P1 — measured).
    The replay's `_norm` is ``{**envelope, **record}`` — the record WINS per
    key — so the fold reads the width from the record and the VECTOR from
    wherever it is. Reading both out of `record` therefore verifies the wrong
    pair: a record declaring ``f32`` while its `embedding` rides the keyword
    payload passed here as "no vector to check" and journalled a lying marker
    the fold then honoured, and the seam's own stamp wrote the truthful
    ``f64`` onto the ENVELOPE in the same line (two contradictory declarations
    in one record). Measured before this fix:

        _emit("PointAdded", {"id": "p", "content": "c",
                             "embedding_width": "f32"},
              embedding=[0.1234567890123, 1/3])
          -> journalled; envelope width `f64`, record width `f32`,
             fold reads `f32` over [0.1234567890123, 0.3333333333333333]

    So the merged view is what gets verified, and a declared width — EITHER of
    the seam's two — with NO decidable vector anywhere is a REFUSAL rather than
    the undecidable case: `_stamp_journal_vector_width` POPS the key when there
    is no vector (`tortoise/embeddings.py`), so "width over no vector" is a
    state the contract says cannot exist, and that reasoning does not depend on
    which width was named.

    ONE input abstains: an unimportable seam (#5148: an import must never fail a
    WRITE), treated as NO MISMATCH, i.e. exactly the behaviour before this guard.
    Otherwise the guard only ever turns a lie into a refusal, never a
    write into an I/O failure.

    ⛔ AND THE `if not isinstance(record, dict): return` BELOW IS DEAD CODE FROM
    `_emit`, not a residual — the opposite of what an earlier revision of this
    paragraph said (#11996 review round 9, measured). `_emit` now refuses a
    non-`dict` record outright (`_emit's record payload must be an exact dict`),
    and its second call site is behind `isinstance(event.get("point"), dict)`, so
    no caller reaches this function with a non-dict record: `_emit("PointAdded",
    "notadict")` raises and journals nothing. The guard is kept as a defensive
    early return for a duck-typed call, and it is named here so a reader does not
    mistake it for an open hole. The REAL residual on this channel is the `f32`
    arm's over-long vector, stated where that arm is implemented. ⛔ A non-numeric ``embedding`` is NOT undecidable
    input — an earlier revision of this paragraph said it was, and the code it
    describes refuses it: `_is_shortest_f32_text` answers ``False`` for a `str` /
    `bytes` / `dict` / scalar by design (`#12000` round-10, F1) and the grid
    arm's raise is a REFUSAL, so a declaration over such a value is refused
    rather than carried.

    ⛔ WHAT THIS VERIFIES, EXACTLY: the WIDTH — the one member of
    `_SERVER_MANAGED_EMBEDDING_KEYS` whose truth is (almost) a function of the
    values, which is why it is the one this channel can re-derive. "Almost",
    measured: the width `stamp_journal_embedding` mints is a function of the
    values AND the two storage-form flags — `[0.5, 0.25]` alone mints `f32`,
    the same values with `embedding_preserved` (or `embedding_verbatim`) mint
    `f64` — so the re-derivation here is faithful only because this channel also
    refuses those two flags outright. The
    identity triple (`embedding_model`, `embedding_revision`,
    `embedding_text_hash`) rides this channel UNCHECKED, and "unchecked" is the
    honest word rather than "uncheckable": a VALUE check would have to be a
    re-derivation like the one this function uses for the width. ⛔ BUT IT DOES
    NOT EXIST FOR THE TRIPLE, and an earlier revision of this paragraph claimed
    it did — "so a record carrying the triple carries the seam's own" — which is
    FALSE on this channel (#11996 review, measured): the two premises are about
    the DOORS (`add_point`'s `_forged_keys`, and `creating=False` POPS all
    three on a caller vector), while `_emit` stamps only a top-level
    `embedding` and a record rides as `payload["point"]`, so nothing refuses and
    nothing pops a caller's triple HERE. Measured at this head:
    `_emit("PointAdded", {… "embedding_model": "forged-model", "embedding_revision": "rev0",
    "embedding_text_hash": "deadbeef", KEY: "f32"})` LANDS with a truthful
    `f32` beside it, and the same key with NO vector lands too — a durable fact
    the replay reads as a model change that never happened. That is the residual:
    it is STATED here, this function does not close it, and the door that refuses
    a caller's version of it is `add_point`. The two
    OWNERSHIP markers (`embedding_verbatim`, `embedding_preserved`) are not
    unchecked either — but not by this function, and not by the same guard on
    both channels: on the POSITIONAL record `_refuse_uncallerable_record_keys`
    runs before this function, while on the KEYWORD payload the vector-family arm
    (`_forged_markers` → "server-managed journal fields") refuses them earlier
    still, so that guard is UNREACHABLE for those two names there. (Measured at
    this head: a record carrying `embedding_verbatim: True` raises, where before
    #11948 it was accepted and the fold then stored the vector RAW instead of
    `vecf32`.)

    Two different reasons, and the difference is measurable (#12000 re-review,
    F2' — the first draft of this paragraph cited the SDK's re-emit as the
    reason, which is the wrong producer because that path never reaches this
    funnel):

    * `embedding_model` / `embedding_revision` / `embedding_text_hash` ARE
      minted on this channel — `_point`'s ``creating=True`` stamp writes all
      three into the record — so refusing the family here would red every
      vector-bearing ``PointAdded``;
    * `embedding_verbatim` / `embedding_preserved` are never minted here and no
      predicate on the values can derive them: they are ownership claims about
      where a vector came from, not a function of its values.

    The width is the one member of the family that IS a pure function of the
    values, which is why it is the one this channel verifies. Anything claiming
    the whole family is verified here is wrong.

    ⛔ THE PREDICATE IS "would the seam also have said `f32`", NOT a re-stamp.
    An earlier draft copied the record WITHOUT the declared width and re-ran
    `stamp_journal_embedding` — and that OVER-BLOCKS, measured: the seam's
    ``f32`` idempotency arm reads the marker itself, so stripping it makes every
    already-narrowed payload (whose text is off-grid as a float64 by
    construction) re-decide ``f64``, and every real `add_point` with an embedder
    available would raise. So ask the seam's own two questions ABOUT ``f32`` in
    the one order that cannot over-block: a declared ``f32`` is truthful iff the
    values ARE its shortest-round-trip text (the idempotent case) or ARE on the
    float32 grid (the case it would narrow). A declared ``f64`` is truthful
    about the
    values it can DESCRIBE — it only forgoes narrowing, which the raw form is
    entitled to — and a value the seam would DROP is refused too (an `f64` over a
    non-vector is not "truthful", it is a width over nothing), with the `f32`
    arm's own measured residual (an over-long sequence) stated where that arm is
    implemented.

    ⛔ AND THE DECLARATION ITSELF IS ENUMERATED, not merely compared to ``f32``.
    Comparing to ``f32`` alone left every OTHER declaration worse than
    unverified — it was UNEXAMINED, and on the one channel built to be
    unreachable from any door that meant a caller could mint a durable journal
    fact the seam never produces (``f16``, ``"f32 "``, ``"F32"``, ``"garbage"``
    all landed at #11948's review). That is not "undecidable input": it is a
    definite claim, and a width/base64 migration trusts it. The seam mints
    exactly two widths (``stamp_journal_embedding`` writes ``f32`` or ``f64``),
    so the set is closed: a declaration outside it is refused, and a
    non-``str`` declaration cannot be one of them either. A declared width is
    therefore validated against the values the writer can emit, and refused
    fail-closed on everything else.

    ⛔ THE ENUMERATION IS SCOPED TO THIS CHANNEL. `sdk._emit_event` carries no
    `_verify_record_vector_width` call at all (the module holds exactly two call
    sites, both here), so no CALLER-DECLARED width reaches the SDK funnel to be
    verified — the width its records carry is minted THERE, by
    `stamp_journal_embedding` inside `sdk._emit_event` itself (it stamps the
    vector `update_point` splats onto `PointRevised`). The caller's marker is
    refused at the DOORS instead, and that half is CLOSED. #11858, merged as #12113
    (`18d43e0462`) and so present only AFTER this branch's first head, refuses
    the whole family over `create_source`'s ASSEMBLED `ev`
    (`sdk._SERVER_MANAGED_EMBEDDING_MARKERS`). Measured at THIS head, with its
    own control:

        TortoiseSDK.create_source("https://example.invalid/f16-probe", "web",
                                  embedding_width="f16")
          -> ValueError: ['embedding_width'] are server-managed embedding
             journal fields and cannot be set by hand
        TortoiseSDK.create_source(..., embedding=[0.1, 0.2],
                                  embedding_width="f32")
          -> ValueError  (the guard reads `ev`, so a width BESIDE A VECTOR —
             the state this funnel's `f32` arm exists for — is refused too)
        TortoiseSDK.create_source("https://example.invalid/control", "web")
          -> OK                                         (the control)

    `_emit_event` keeps no such guard BY DESIGN, and this note may not be read
    as licensing one: `update_point` rides `embedding_verbatim` INTO its
    `PointRevised` record legitimately, setting it from the vector it was given
    (#5004 round-8), so a blanket refusal over `_emit_event`'s props would
    false-block a sanctioned writer — the reason `_SERVER_MANAGED_EMBEDDING_MARKERS`
    is refused at INTAKE and at the one door that bypasses intake, never at the
    funnel. Recorded here so the enumeration above is not read as a claim about
    every writer in the package.
    """
    if not isinstance(record, dict):
        return
    try:
        from .embeddings import JOURNAL_VECTOR_WIDTH_F32 as _F32
        from .embeddings import JOURNAL_VECTOR_WIDTH_F64 as _F64
        from .embeddings import JOURNAL_VECTOR_WIDTH_KEY as _KEY
        from .embeddings import _is_shortest_f32_text, shortest_float32_vector
    except Exception:  # noqa: BLE001, RUF100 — #5148
        return
    # ⛔ THE MERGED VIEW DECLARES THE WIDTH, not the record alone (#11996 review
    # round 15, measured). The READER takes `{**ev, **ev["point"]}` (see the
    # "merged view" note below), so when the width rides the ENVELOPE — which is
    # what `stamp_journal_embedding` mints — and the vector rides the RECORD,
    # reading only `record.get(KEY)` abstained on the pair the fold actually
    # reads, and a caller could journal `width: f32` over an off-grid record
    # vector:
    #
    #     _emit("PointAdded", {"id": "h", "content": "c",
    #                          "embedding": [0.1234567890123, 1/3]},
    #           embedding=[0.5, 0.25])        # -> the envelope's width is `f32`
    #       -> LANDED, and `_norm` reads `f32` over [0.1234567890123, 0.3333…]
    #
    # ⛔ AND ABSENT IS NOT "PRESENT AND `None`" (#11996 review round 14):
    # `record.get(_KEY)` read an explicit `embedding_width: None` as no
    # declaration at all, so the one value that is neither of the two widths got
    # past the closed enumeration below and was journalled as
    # `"embedding_width": null` — a durable width the seam can never mint (it
    # POPS the key when there is no vector, and `stamp_journal_embedding` writes
    # only `f32` / `f64`). Only a MISSING key on BOTH channels is the legacy
    # shape this guard has to keep accepting.
    if _KEY not in record and _KEY not in payload:
        return
    declared = record.get(_KEY, payload.get(_KEY))
    # ⛔ THE SEAM'S OWN ENUMERATION (#11948 review): a width the seam cannot mint
    # is not a claim to verify — it is a claim nothing here can produce, so it
    # is refused rather than carried into the journal. The `isinstance` arm is a
    # TYPE refusal, not a `TypeError` guard — an earlier revision of this comment
    # said it stopped "an unhashable declaration" from raising out of the
    # membership test, which is false: the container is a TUPLE, so membership is
    # an `==` scan and nothing is hashed (`["f32"] in ("f32", "f64")` is simply
    # `False`). What it actually buys (#11996 review, measured): a non-`str` can
    # never BE one of the two spellings the seam mints, but tuple membership asks
    # the VALUE via `__eq__`, so an object whose `__eq__` answers `True` for
    # `"f32"` reads as that width and is then accepted by the ``f32`` arm — the
    # refusal below is what stops it. A non-`str` is therefore refused by type,
    # independent of what it claims to equal.
    # ⛔ THE TYPE ARM IS EXACT, `type(...) is not str`, NOT `isinstance`
    # (#11996 review round 4, measured): a `str` SUBCLASS whose `__eq__` answers
    # `True` for `"f32"` passes BOTH halves of an `isinstance` form — Python
    # gives the subclass's `__eq__` priority in the membership scan — and was
    # journalled as a width the seam never mints. A non-`str` liar is refused by
    # either form; only the exact-type test refuses the subclass one, and no
    # producer here passes a `str` subclass.
    if type(declared) is not str or declared not in (_F32, _F64):
        raise ValueError(
            f"{_KEY}={declared!r} is not a width this seam mints: "
            f"`stamp_journal_embedding` writes only {_F32!r} or {_F64!r}, so a "
            f"`{type_}` record may not declare {declared!r}. A declared width is "
            "a server-managed journal field, and a durable claim nothing here "
            "can produce is trusted by the next width/base64 migration (#5004 "
            "round-7).")
    # `_norm` splices the record OVER the envelope on replay (`{**ev,
    # **ev["point"]}`), so this is the pair the FOLD sees.
    #
    # ⛔ THE NO-VECTOR REFUSAL IS WIDTH-INDEPENDENT, so it runs BEFORE the `f64`
    # return below (#11996 review round 2, measured): inside the `f32` arm only,
    # `_emit("PointAdded", {… KEY: "f64"})` LANDED and journalled a width over
    # `embedding=None` — a fact the seam can never have minted (it POPS the key
    # when there is no vector), i.e. the same forged-fact class this guard
    # exists to close, allowed for one of the two widths. `f64` is truthful
    # about the VALUES it describes; it is not truthful about a vector that does
    # not exist.
    values = {**payload, **record}.get("embedding")
    if values is None:
        # ⛔ THIS REFUSAL IS INDIFFERENT TO WHICH WIDTH WAS DECLARED, so it runs
        # BEFORE the `f32` narrowing below (#12059). Its justification — the
        # seam POPS the key when there is no vector, so "width over no vector"
        # is a state the journal contract says cannot exist — never mentioned
        # `f32`, but it sat after the `declared != _F32` early return, so half
        # the declaration surface was unchecked: a declared `f64` over no vector
        # was ACCEPTED and journalled, a durable width no door can mint and the
        # next width/base64 migration would trust. Measured at `25127b61b6`:
        #
        #     _emit("PointAdded", {"id": "p", "content": "c",
        #                          KEY: "f64"})   -> journalled `f64`
        raise ValueError(
            f"{_KEY}={declared!r} is declared over NO vector: the MERGED view "
            f"(`{{**payload, **record}}`, which is what the fold reads) carries no "
            f"`embedding` for `{type_}`. A width without a vector is a state the "
            f"journal contract says cannot exist (the seam POPS the key when there "
            f"is no vector). ⛔ The merge matters when the two channels disagree: a "
            f"record-carried `embedding: None` WINS over a vector on the envelope, "
            f"so 'your envelope supplied one' is not a reason to expect this to land "
            f"(#11996 review round 18, measured).")
    # ⛔ A ONE-SHOT ITERABLE IS READ ONCE (#11996 review round 14, measured).
    # Every arm below ASKS THE SEAM, and the seam reads with `list(raw)`
    # (`embeddings.py`); the STAMP further down then asks it a SECOND time. A
    # generator is exhausted by the first read, so the second saw an empty
    # vector — a write that LANDED on `main` became a refusal:
    #
    #     _emit("PointAdded", {… KEY: "f64"}, embedding=(x for x in [0.1, 0.2]))
    #       -> on `main`: journalled `f64` over [0.1, 0.2] (the `f64` arm returned
    #          early, so nothing consumed it before the stamp)
    #       -> with the `f64` seam probe but no materialisation: a
    #          "dropping an unusable embedding … (empty)" log line, then
    #          "declared over NO vector" from the post-stamp belt
    #
    # So the seam's own reading is materialised ONCE here and handed to every
    # arm; when the ENVELOPE carries the vector it is written back onto it, since
    # the stamp covers the envelope's top-level `embedding` only and would
    # otherwise read the exhausted iterator.
    # ⛔ THE ELEMENT ARM KEEPS THE RAW OBJECT ON PURPOSE: materialising would put
    # a numpy array's `np.float64` elements in front of a check that refuses
    # everything but an exact `int`/`float`, turning the stated residual (a
    # non-`list` iterable rides the seam arms below) into an over-block.
    seam_values = values
    if not isinstance(values, (list, tuple, str, bytes, dict)):
        try:
            seam_values = list(values)
        except TypeError:  # not iterable at all: the seam's DROP still decides
            seam_values = values
        else:
            # ⛔ WRITTEN BACK ONLY WHEN THE ENVELOPE IS WHAT CARRIED IT: the
            # merged view lets the RECORD win, so a record-carried vector must
            # not overwrite the envelope's — the stamp covers the envelope's
            # top-level `embedding`, and replacing it would change what lands.
            if payload.get("embedding") is values:
                payload["embedding"] = seam_values
    # ⛔ EVERY ELEMENT IS READ BY TWO DIFFERENT PATHS, so neither arm below can
    # verify an element that is not an EXACT `int` / `float` (#11996 review
    # rounds 6 and 7, each measured). This guard asks the SEAM, and the seam
    # converts with `float(x)`; the journal's JSON encoder writes the element AS
    # IT IS and converts nothing. So a payload can answer every question below
    # truthfully and still journal a value the guard never saw:
    #
    #     class Sneaky(float):
    #         def __float__(self): return 0.5        # what the SEAM reads
    #     json.dumps([Sneaky(nan)])          -> [NaN]  # what the JOURNAL writes
    #     json.dumps([Sneaky(0.1234567890123)]) -> [0.1234567890123]
    #     json.dumps(["0.5", "0.25"])        -> ["0.5", "0.25"]
    #
    # Measured at the head before this arm existed (`f64` behaves the same):
    #
    #     _emit("PointAdded", {… "embedding": [Sneaky(nan)], KEY: "f32"})
    #       -> LANDED, journalled `f32` over [NaN]   (the plain [nan] is refused)
    #     _emit("PointAdded", {… "embedding": [Sneaky(0.1234567890123)], KEY: "f32"})
    #       -> LANDED, journalled `f32` over an OFF-GRID double ← #5004 round-7
    #     _emit("PointAdded", {… "embedding": ["0.5", "0.25"], KEY: "f32"})
    #       -> LANDED, journalled `f32` over STRINGS, and the FOLD stored them as
    #          the Point's embedding
    #
    # `float(0.5)` is on the grid and `_is_shortest_f32_text` answers truthfully
    # about it, so every arm below passes on the seam's reading while the journal
    # stores the other one. The principle of this guard is that a value two
    # readers disagree about cannot carry a width — so the vector must be made
    # of elements BOTH readers agree on, which is exactly `type(x) is int or
    # type(x) is float` (IDENTITY, not `type(x) in (int, float)`: a membership
    # scan runs the METACLASS's `__eq__`, #11996 review round 17). A subclass, a
    # numeric `str`, a `bool`, a `Decimal` or a nested
    # sequence is refused rather than verified; no producer here passes one, and
    # the cost of being wrong is a refused write, not a forged durable fact.
    #
    # ⛔ SCOPED TO `list` / `tuple` ON PURPOSE: an iterable of another type (a numpy
    # array, a generator) keeps the older path through the seam arms below, where
    # the seam's own reading still decides DROP — a stated residual, because this
    # arm cannot see those elements without materialising a caller's iterator.
    if isinstance(values, (list, tuple)):
        # ⛔ AN EXACT `list` / `tuple` first, for the same reason as the dict rule
        # above (#11996 review round 8, measured): a `list` SUBCLASS can answer
        # `__iter__` differently on each call, so the guard reads one vector and
        # the encoder writes another. ⛔ IDENTITY, NOT MEMBERSHIP (#11996 review
        # round 17, measured): `type(values) not in (list, tuple)` is
        # `not (list == type(values) or tuple == type(values))`, which runs the
        # reflected `type(values).__eq__(list)` — the METACLASS's `__eq__` — so a
        # carrier that answers `True` defeated the arm:
        #
        #     class M(type):
        #         def __eq__(cls, other): return True
        #         def __hash__(cls): return id(cls)
        #     class StagedList(list, metaclass=M):   # real contents [nan]
        #         ... __iter__ yields [0.5] for the first six reads
        #     _emit(… {"embedding": StagedList([nan]), KEY: "f32"})
        #       -> LANDED, journalled `f32` over [NaN]
        #
        # Every neighbouring arm in this module already compares by identity for
        # exactly this reason (`type(declared) is not str`, `type(record[0]) is
        # not dict`, `type(_pair) is list`); these two were the exceptions.
        if type(values) is not list and type(values) is not tuple:
            raise ValueError(
                f"{_KEY}={declared!r} is declared over a {type(values).__name__} "
                f"vector: only an exact `list` / `tuple` can be read the same way "
                f"by this guard and by the journal's encoder, and a sequence "
                f"SUBCLASS can change what it yields between the two reads "
                f"(#5004 round-7). Pass an exact `list`.")
        # ⛔ THE TYPE ARM: an element that is not an exact `int` / `float` is
        # refused rather than verified. The MAGNITUDE arm — an `int` the float
        # conversion cannot reproduce, and the totality of that comparison — is
        # directly below, because it has its own reason and its own message.
        # ⛔ AND THE ELEMENT TEST IS IDENTITY TOO (#11996 review round 17,
        # measured): `type(x) not in (int, float)` runs `int.__eq__(type(x))` and
        # then the METACLASS's `__eq__`, so a numeric subclass whose metaclass
        # answers `True` passed the arm while the seam read it through
        # `float(x)` and the C encoder wrote it raw:
        #
        #     class Sneaky(float, metaclass=M):   # M.__eq__ -> True
        #         def __float__(self): return 0.5
        #     _emit(… {"embedding": [Sneaky(nan)], KEY: "f32"})
        #       -> LANDED, journalled `f32` over [NaN]
        #
        # Same rule as the container above, and as `_int_is_float_exact`.
        bad = [x for x in values
               if type(x) is not int and type(x) is not float]
        if bad:
            raise ValueError(
                f"{_KEY}={declared!r} is declared over a vector carrying an "
                f"element that is not an exact `int` / `float` "
                f"({type(bad[0]).__name__}): the seam reads the vector through "
                f"`float(x)` while the journal's JSON encoder writes the element "
                f"as it is, so the two readers can disagree and the declared "
                f"width would describe a value the guard never saw (#5004 "
                f"round-7). Pass exact `int` / `float` elements.")
        # ⛔ AND AN `int` MUST BE EXACTLY REPRESENTABLE AS A FLOAT (#11996 review
        # round 8, measured): the seam reads elements as `float(x)` and the
        # encoder writes the integer exactly, so above 2**53 the two disagree —
        # `_emit(… "embedding": [2**53 + 1], KEY: "f32")` LANDED with the width
        # describing a value the guard had rounded.
        #
        # ⛔ AND THE TEST MUST BE TOTAL (#11996 review round 12, measured):
        # `float(x)` is not defined for every `int` — above ~1.8e308 it raises
        # `OverflowError`, so an inline `float(x) != x` would leave THIS arm the
        # only one in the guard raising a `TypeError`'s cousin instead of the
        # documented `ValueError`:
        #
        #     _emit("PointAdded", {"id": "p", "content": "c",
        #                          "embedding": [2 ** 2000], KEY: "f32"})
        #       -> OverflowError: int too large to convert to float
        #
        # Nothing is journalled either way, so the harm was a caller (and every
        # `except ValueError` around a door) reading an unhandled type as a
        # crash rather than a refusal. A value the float conversion cannot even
        # represent is not exactly representable, which is the same verdict.
        #
        # ⛔ AND IT HAS ITS OWN MESSAGE, because the element IS an `int` (#11996
        # review round 13, measured). Folding both reasons into one `bad` list
        # made the refusal for `2**53 + 1` say "an element that is not an exact
        # `int` / `float` (int)" — contradicting the clause it was appended to —
        # and told the caller to "Pass exact `int` / `float` elements", i.e. to
        # resend the same value and be refused identically. The reason here is
        # the MAGNITUDE: `float()` ROUNDS above 2**53 and has no value at all
        # above ~1.8e308, while the encoder writes the integer verbatim.
        inexact = [x for x in values
                   if type(x) is int and not _int_is_float_exact(x)]
        if inexact:
            raise ValueError(
                f"{_KEY}={declared!r} is declared over a vector carrying an "
                f"`int` that no float reproduces exactly: the seam reads the "
                f"vector through `float(x)` — which ROUNDS an integer above "
                f"2**53 and has no finite value at all above ~1.8e308 — while "
                f"the journal's JSON encoder writes the integer verbatim, so "
                f"the declared width would describe a value the guard never saw "
                f"(#5004 round-7). Pass a value a float holds exactly.")
    if declared != _F32:
        # ``f64`` is truthful about the VALUES it can describe — it only forgoes
        # narrowing, which the raw form is entitled to — but it is NOT a licence
        # to carry a value that is not a vector at all (#11996 review round 3,
        # measured):
        #
        #     _emit("PointAdded", {… "embedding": "123", KEY: "f64"})
        #       -> LANDED, journalled `embedding: "123"` with `width: "f64"`, and
        #          the FOLD stored the `str` as the point's embedding — while the
        #          same value under a declared `f32` is refused one arm below.
        #
        # `f64` is the answer for a vector float32 cannot HOLD, not for one the
        # seam would DROP: `stamp_journal_embedding` sets an unusable `embedding`
        # to an owned `None` (a `str` / `bytes` / `dict` / a scalar / a
        # non-finite element / an empty or implausibly long sequence — each
        # measured), so a width over one of those is a fact the seam can never
        # have minted. Rather than duplicate that normalisation here and let it
        # drift, ASK THE SEAM — the same "re-derive it" method the `f32` arm
        # uses — and refuse only what it would drop. A legitimate `f64` over a
        # vector float32 overflows (`[1e300, -1e300]`) still lands, measured; so
        # does an off-grid `[0.1234567890123, 1/3]`.
        try:
            from .embeddings import stamp_journal_embedding
        except Exception:  # noqa: BLE001, RUF100 — #5148: never fail a WRITE on an import
            return
        # ⛔ THE IMPORT IS THE ONLY ABSTENTION — A RAISE FROM THE CALL IS A
        # REFUSAL (#11996 review round 5, measured). One `try` around BOTH made a
        # value the seam cannot NORMALISE abstain this guard, so the lie landed:
        #
        #     class Evil(float):
        #         def __float__(self): raise RuntimeError("boom")
        #     _emit("PointAdded", {… "embedding": [Evil(nan)], KEY: "f64"})
        #       -> JOURNALLED `embedding: [NaN]` with `width: "f64"`, while the
        #          SAME payload spelled `[nan]` is refused one arm above.
        #
        # `stamp_journal_embedding` catches TypeError/ValueError/OverflowError
        # only, so an exception escaping it is a value it cannot describe at ANY
        # width — and no width describes a vector the seam cannot read.
        #
        # ⛔ THIS IS NOW A DEFENCE IN DEPTH, and it says so rather than implying a
        # pin it does not have (#11996 review round 7, measured): the ELEMENT arm
        # above refuses every LIST / TUPLE element that is not an exact
        # `int`/`float`, which is every shape that reaches this arm with a
        # JSON-serialisable carrier — so the forged-fact harm this split was
        # written for is closed elsewhere. What still reaches it is a vector this
        # arm cannot enumerate (a numpy object array, a generator); the row in
        # `test_a_forged_width_cannot_ride_the_funnels_positional_record` pins
        # THAT, and with the split reverted the same payload abstains and dies at
        # the encoder with `TypeError: not JSON serializable` — a failure, not the
        # forged record this arm exists to prevent. Falsifier: put the call back
        # inside the import's `try` and that row reads `TypeError`, not
        # `ValueError` (measured).
        try:
            probe = stamp_journal_embedding({"embedding": seam_values},
                                           creating=False)
            dropped = probe.get("embedding") is None
        except Exception:  # noqa: BLE001, RUF100 — the seam cannot describe it
            raise ValueError(
                f"{_KEY}={declared!r} is declared over a value the seam cannot "
                f"normalise: `stamp_journal_embedding` RAISED on it, so it can "
                f"never have minted this width. A width describes a vector the "
                f"seam can READ; one it cannot read is refused rather than "
                f"carried as a durable fact (#11948).") from None
        if dropped:
            raise ValueError(
                f"{_KEY}={declared!r} is declared over a value the seam can only "
                f"DROP: `stamp_journal_embedding` sets an unusable `embedding` "
                f"to an owned `None` (a `str`/`bytes`/`dict`, a scalar, a "
                f"non-finite element, an empty sequence), so `{type_}`'s record "
                f"would carry a width and a vector the journal contract says "
                f"cannot exist. `f64` forgoes narrowing for a REAL vector; it is "
                f"not a licence to carry a non-vector.")
        return
    try:
        truthful = _is_shortest_f32_text(seam_values)
    except Exception:  # noqa: BLE001, RUF100 — a raise is a REFUSAL, not an abstention
        # ⛔ THE SAME ABSTENTION HOLE AS THE `f64` ARM (#11996 review round 5,
        # measured): `_is_shortest_f32_text` reads its argument as
        # `[float(x) for x in vector]`, so a `float` subclass whose `__float__`
        # raises escaped it — and `except: return` read that as "the seam cannot
        # decide" over a value it can decide exactly: it will DROP it. Measured:
        # `_emit("PointAdded", {… "embedding": [Evil(nan)], KEY: "f32"})`
        # journalled `[NaN]` under a declared `f32`, where a plain `[nan]` is
        # refused. Deciding "cannot decide" in the PERMISSIVE direction is the
        # one answer this guard exists to make impossible.
        #
        # ⛔ ALSO A DEFENCE IN DEPTH SINCE THE ELEMENT ARM (#11996 review round 7):
        # `Evil(float)` is a numeric subclass and is refused above, so what still
        # reaches this arm is a non-`list`/`tuple` iterable (a numpy object
        # array). The row pins THAT; with this line reverted to `return`, the
        # payload abstains and dies at the encoder instead of being refused.
        truthful = False
    if not truthful:
        # ⛔ The grid question is its OWN `try`, and a raise here is a REFUSAL,
        # not an abstention (#12000 round-10 review, F1 — measured).
        # `_is_shortest_f32_text` is fail-CLOSED on non-numeric input (a `str`,
        # `bytes`, `dict`, a scalar, a NaN) and returns `False`; asking
        # `shortest_float32_vector` about the same input can RAISE, and a
        # shared `except: return` therefore read "the seam cannot decide" over
        # a value the seam can describe exactly: it will DROP it. Measured at
        # `bf1957fb9`:
        #
        #     _emit("PointAdded", {"id": "p", "content": "c",
        #                          "embedding_width": "f32"},
        #           embedding="abc")   -> journalled, fold reads f32 over 'abc'
        #
        # So: a vector the seam can only DROP is a vector no width describes,
        # and a declared `f32` over it is refused.
        #
        # ⛔ AND THE SAME ITERABLE GUARD THE FIRST ARM CARRIES, because the grid
        # helper does NOT have it (#12000 round-10 re-review, F1' — measured):
        # `shortest_float32_vector` reads its argument as
        # `[float(x) for x in vector]`, so a `str` is a sequence of CHARACTERS,
        # `bytes` a sequence of INTS and a `dict` a sequence of KEYS —
        # `"123"` becomes `[1.0, 2.0, 3.0]` and `{"0": 0.1}` becomes `[0.0]`,
        # both ON the grid, so the arm answered "truthful" about the wrong
        # object, in the PERMISSIVE direction. `_is_shortest_f32_text` refuses
        # exactly these three types for exactly this reason; the grid arm must
        # too. Measured at `0a86cb5a87`, with the junk on the RECORD — where the
        # post-stamp belt DID run and DID see it (it is handed `event["point"]`
        # as the record, and the merged view lets the record win), and let it
        # through because it ran the SAME permissive predicate this arm did:
        #
        #     _emit("PointAdded", {"id": "p", "content": "c",
        #                          "embedding": "123",
        #                          "embedding_width": "f32"})
        #       -> journalled, and the FOLD stored point.embedding = '123'
        #
        # ⛔ The belt is NOT a duplicate of this arm and must not be read as one
        # (#12000 cycle-3 review, P3): an empty vector passes HERE (it is
        # representable at every width) and only the belt refuses it once the
        # stamp has made it absent. Both arms need the guard, and neither covers
        # a different CHANNEL — they cover the same pair at two moments.
        if isinstance(values, (str, bytes, dict)):
            truthful = False
        else:
            try:
                truthful = shortest_float32_vector(seam_values) is not None
            except Exception:  # noqa: BLE001, RUF100
                truthful = False
    if not truthful:
        raise ValueError(
            f"{_KEY}={declared!r} is not what the values say: this payload's "
            f"`embedding` is neither the seam's shortest float32 text nor on "
            f"the float32 grid, so `f32` describes a width the values do not "
            f"have. A declared width is a server-managed journal field and "
            f"`{type_}`'s record may only carry a truthful one — a marker that "
            f"lies is trusted by the next width/base64 migration (#5004 "
            f"round-7).")

    # ⛔ AND "TRUTHFUL BY THE TWO QUESTIONS ABOVE" IS NOT YET "THE SEAM WOULD
    # KEEP IT" (#11996 review round 4, measured). `_is_shortest_f32_text([])` is
    # deliberately `True` ("representable at every width") and the grid arm finds
    # `[]` on the grid, yet `stamp_journal_embedding` DROPS an empty vector — so a
    # declared `f32` over a RECORD-carried `[]` was JOURNALLED:
    #
    #     _emit("PointAdded", {… "embedding": [], KEY: "f32"})  -> LANDED
    #     _emit("PointAdded", {… "embedding": [], KEY: "f64"})  -> refused
    #
    # `f64` refuses it one arm above; this is the same forged fact under the other
    # width. ⛔ GATED ON THE RECORD CARRYING THE VECTOR, because the ENVELOPE-carried
    # empty vector is the BELT's own row (`test_a_forged_width_cannot_ride_the_
    # funnels_positional_record`): refusing it here would refuse the shape before
    # the stamp and take that row's mutant with it. ⛔ NOT "the belt's only mutant":
    # the post-stamp call has an INDEPENDENT pin this gate cannot see — the
    # merged-view lie (`p-merged-lie`), where the pre-stamp check early-returns
    # because NEITHER channel declared a width, so the stamp mints `f32` and only
    # the belt re-checks it against the merged vector (measured, round 18). Only the two
    # DROP reasons the questions above cannot see can reach this point — an EMPTY
    # or an implausibly long sequence (the non-numeric and non-finite ones are
    # already refused above) — and this checks the EMPTY one, which is the
    # measured shape; an over-long one stays a stated residual.
    if record.get("embedding") is not None:
        try:
            empty = len(seam_values) == 0
        except TypeError:  # a vector with no length is not an empty one
            empty = False
        if empty:
            raise ValueError(
                f"{_KEY}={declared!r} is declared over an EMPTY vector: "
                f"`stamp_journal_embedding` DROPS an empty `embedding` (it "
                f"cannot describe a width), so `{type_}`'s record would carry a "
                f"width over a vector the journal contract says cannot exist.")


def _refuse_caller_named_record_keys(keys, door: str) -> None:
    """Refuse a caller-supplied record-shaped payload on a channel that is not
    `_emit`'s positional record argument.

    Called by `_emit` itself (once over the keyword payload, once over the
    positional record), by `add_event` / `emit_belief` (a message naming the
    door the caller actually called), and by `add_object` (`**props`). One
    predicate, one spelling: no call site can drift. Always BEFORE any write —
    a refusal here leaves nothing to replay, which is the same ordering
    argument `_emit`'s own gates make.
    """
    named = _named_from_caller(keys, _NESTED_RECORD_KEYS)
    if named:
        raise ValueError(
            f"{named} is a RECORD-SHAPED journal payload that {door} does not "
            "build, and a caller-built one is folded by the replay without the "
            "vector rule the record doors apply (width declared, caller marker "
            "refused). Use the door that owns that record (`add_point`, "
            "`add_operator`) instead of naming it here.")


def _refuse_caller_named_envelope_keys(keys, door: str, *, record: bool = False) -> None:
    """Refuse a caller key that collides with the journal ENVELOPE.

    #11948: the envelope is the FUNNEL's to build. A payload key that shares a
    name with it does not add a field — `_emit` spreads the payload AFTER the
    envelope, so the caller's value WINS, and the record is re-typed
    (`type`), re-identified (`event_id`) or re-attributed (`ts`,
    `initiated_by`, `agent_id`, `actor_user_id`, `corrects`). Nothing else in
    the write path re-checks the record's type, so a caller-typed record is
    folded by the replay as whatever it claims to be.

    `record=True` adds the one envelope name that is a CALLER value on this
    channel only (see `_RECORD_ENVELOPE_EXTRA`): the doors pass
    `projection_version` as a keyword, so seeing it INSIDE the record means the
    caller put it there and the replay will read it in preference to the
    door's — defeating the v2 stop-write's context strip.
    """
    forbidden = (_ENVELOPE_KEYS_AT_PASSTHROUGH | _RECORD_ENVELOPE_EXTRA
                 if record else _ENVELOPE_KEYS_AT_PASSTHROUGH)
    named = _named_from_caller(keys, forbidden)
    if named:
        raise ValueError(
            f"{named} are journal ENVELOPE fields, which {door} does not own: "
            "the funnel builds the envelope, so a payload key that collides "
            "with it REWRITES the record's identity rather than adding a "
            "field (`type` re-types it, `event_id` re-identifies it, `ts` / "
            "`initiated_by` / `agent_id` / `actor_user_id` forge when and by "
            "whom it was written, `corrects` forges its provenance edge). "
            "Name payload fields only.")


def _refuse_caller_named_markers(keys, door: str) -> None:
    """Refuse a caller-supplied SERVER-MANAGED vector marker at a door.

    `_emit` refuses the same keys before its commit point; this is the
    door-level copy so that a door with a best-effort arm (`emit_belief`) can
    raise BEFORE the arm that would otherwise swallow the refusal and drop the
    record (#11948 F2). One spelling of the set (`_SERVER_MANAGED_EMBEDDING_KEYS`),
    ONE placement: this function, called by every passthrough door
    (`_refuse_caller_named_funnel_keys`) BEFORE its own best-effort arm. There is
    no second copy to keep in step — an earlier version of this docstring said
    "two placements" and there is one call site, in
    `_refuse_caller_named_funnel_keys` (#11996 review round 14: the evidence
    offered here used to be a `grep` for `'_refuse_caller_named_markers('`, which
    returns this docstring as well as the call, so the command refuted itself).
    """
    named = _named_from_caller(keys, _SERVER_MANAGED_EMBEDDING_KEYS)
    if named:
        raise ValueError(
            f"{named} are server-managed journal fields (embedding width / "
            f"storage form / origin) and cannot be set via {door}'s payload")


def _refuse_caller_named_funnel_keys(keys, door: str) -> None:
    """Refuse EVERY name the journal funnel owns, at one passthrough door.

    The four families and their reasons live above; this is the ONE call every
    PASSTHROUGH door makes, so a FIFTH family (or a fourth passthrough door)
    cannot be half-wired —
    the failure mode #11835 review round 4 and #11948 both measured: a rule
    stated at two doors and silently absent at the third. ⛔ "Half-wired" is a
    claim about the THREE PASSTHROUGH DOORS only, and the next sentence is why:
    `add_point` is the
    FOURTH caller-splatting door and does NOT call this: its `**fields` is
    covered by its own `_forged_keys` plus `_emit`'s positional channel, so a
    family armed here alone WOULD be absent there — which is why the
    uncallerable family is armed in both places (see the next paragraph).
    (#11996 review round 16: "cannot be half-wired" read as a guarantee for all
    four doors, which this function cannot give and the next sentence withdraws.)

    ⛔ The UNCALLERABLE family is in here rather than only in `_emit`, because a
    door calls this BEFORE its best-effort arm: refusing deeper (inside `_emit`)
    let `emit_belief` DROP the record with a log line while `add_event` and
    `add_object` raised on the identical payload — measured at the third review
    of this fix, for `evidence_fold`/`embeddingText`/`extractedFromAll`/
    `sourceVersion`/`sourceVersions`, one family over from the money/percent/kind
    recurrences this door has already been fixed for.
    """
    _refuse_non_str_keys(keys, door)
    _refuse_caller_named_record_keys(keys, door)
    # ⛔ `record=True` HERE TOO, and it is not cosmetic (#11996 review round 10,
    # measured): `_RECORD_ENVELOPE_EXTRA` (`projection_version`) was refused only
    # INSIDE the positional record, so a caller put the same key one level out and
    # every door took it — measured on a public door, no private access needed:
    #
    #     api.emit_belief("PointRevised", id=pid, new_content="c2",
    #                     new_context={"secret": "leaked"}, projection_version=1)
    #       -> LANDED, and the fold KEPT `context` (pv=1) while the same payload at
    #          the seam's own pv=2 strips it — the #49 v2 stop-write defeated
    #
    # The doors do not pass this key themselves (`add_point`/`add_operator` do, and
    # neither calls this funnel), so adding it here closes all three doors without
    # touching the funnel's keyword channel.
    _refuse_caller_named_envelope_keys(keys, door, record=True)
    _refuse_caller_named_markers(keys, door)
    _refuse_uncallerable_record_keys(keys, door.split("`")[1] if "`" in door else door,
                                     "payload")


def provenance(source_id, span, quote, *, speaker=None, extracted_by="mock@0"):
    return {
        "source_id": source_id,
        "speaker": speaker,
        "span": span,
        "quote": quote,
        "extracted_by": extracted_by,
        "extracted_at": now_iso(),
    }


def _refuse_invisible_point_kinds_in_payload(payload: dict) -> None:
    """#9287: this seam persists ``pointKind`` verbatim — refuse padding.

    ``EventAPI`` is the write seam every pipeline uses (the v1 extractor,
    ``ingest``, ``docs_miner``, ``mining``, the CLI), and ``add_point`` splats
    the caller's ``fields`` into the journal record, which
    ``projection._upsert_point`` folds with
    ``n.pointKind = coalesce($pk, n.pointKind)``. It already hosts the #5475
    kind gate for exactly this reason, and it had no invisible-kind arm —
    measured at #9591's head: ``api.add_point(..., pointKind='statement
    \\u2003')`` stored the padded kind, the gate answered ``[]``, and a
    ``require_calibration=True`` run stamped it.

    The input here is not always a human: ``extractor.py`` builds the kind from
    the model's JSON and passes it straight to ``add_point``, so a stray
    em-space/NBSP in model output mints the fail-open shape with no caller
    intent — a refusal that names the record is the only thing that surfaces it.

    ONE predicate with the SDK doors (``_kind_invisible_char``), never a
    re-spelling of the categories. The payload walk is ``pack_state``'s
    (``kind_spellings_in_payload``) — the same miner the approval gate reads —
    filtered to the canonical ``pointKind``: entity kinds are a different gate
    with its own blast radius, and this issue is the calibration gate's.

    A MODULE-LEVEL function, deliberately not a method: its one caller
    (``EventAPI._refuse_unapproved_kinds``) is invoked UNBOUND by duck-typed
    API doubles (``mining.py``'s ``ConversationMiner``, the #7951 ordering
    test), so nothing here may touch ``self`` — the first cut did, and raised
    ``AttributeError`` on a double whose ``projection`` has no ``.g``
    (#9591 review cycle 2, P1).
    """
    from .pack_state import kind_spellings_in_payload
    from .sdk import _refuse_invisible_kind
    for kind_prop, value in kind_spellings_in_payload(payload):
        if kind_prop == "pointKind":
            # ⛔ NOT one door's name: this gate runs at the FUNNEL (`_emit`) for
            # every write door, and `emit_belief` ALSO runs its PURE half eagerly
            # (outside the arm that swallows I/O). Naming `add_point` — which
            # runs no eager gate at all — told a caller who came in anywhere else
            # to fix the wrong call, and on the belief path this message is the
            # caller's ONLY signal (nothing journalled, nothing logged).
            _refuse_invisible_kind("pointKind", value, door="EventAPI")


class EventAPI:
    def __init__(self, log, *, initiated_by: str, agent_id: str | None = None,
                 projection=None):
        assert initiated_by in ("extractor", "user")
        self.log = log
        self.initiated_by = initiated_by
        self.agent_id = agent_id
        self.projection = projection
        self.current_run: str | None = None
        self._ingest_cache: dict[tuple, str] | None = None
        # Gate 4: SVBP instance (lazy-init on first confidence query)
        self._svbp = None

    # -- internals -----------------------------------------------------------
    def _refuse_unapproved_kinds(self, payload: dict, *,
                                graph_reads: bool = True) -> None:
        """#5475 (residual): a journal-first write may not install a pack.

        ``graph_reads=False`` runs only the PURE half — the invisible-character
        refusal, a string predicate — and returns before the approval-set READ.
        One caller needs the split: ``emit_belief`` is best-effort by contract,
        so an I/O failure must never escape it, and the approval half performs a
        whole-graph read that RAISES when the handle is bound but unreadable (its
        own note, below). Calling the FULL gate before that arm re-opened the
        class the arm exists to swallow — measured at `3f5c61601`:
        `emit_belief(..., eventKind="ruleRevised")` against a projection whose
        graph read raises propagated `OSError` out of `ep.run()`, journal delta
        0. The pure half still runs EAGERLY there (a malformed payload is not an
        I/O failure and must be reported to the caller), and the approval half
        then runs once, inside `_emit`, inside the arm — which is where failing
        closed at the commit point is correct.

        ``EventAPI`` is the write seam every pipeline uses (the v1 extractor,
        ``ingest``, ``docs_miner``, ``mining``, the CLI): it appends to the
        journal and folds through ``projection._upsert_*``, which persists the
        kind verbatim — so ``graph_kind_namespaces`` mines the value straight
        back into the graph's APPROVAL set. Measured at HEAD: a graph recorded
        as ``['dev']`` became ``['dev','marketing']`` from one
        ``add_object(..., 'marketing:campaign')``. The create-side gate does not
        see this seam (it never touches the SDK), so the closure property #5475
        states — *a write may not widen the approved set* — was still false one
        transport over.

        ⛔ THE BOUND, stated so this is not read as "the write path is closed":
        this gates a write that HAS a graph handle. ``rebuild_all`` folds a
        journal directly and stays ungated BY DESIGN — the journal is the
        RECONSTRUCTION SOURCE (never a durability authority; that lives only in
        ``docs/durability-posture.md``), so a record already in it must
        reproduce faithfully; refusing at replay would make a committed record
        unfoldable, which is strictly worse than the widening. Two consequences
        follow, both recorded rather than papered over: a record already in a
        legacy journal still widens the graph when rebuilt, and a
        ``projection``-less lane
        (``EventAPI(log, projection=None)`` — no PRODUCTION caller; the only two
        sites that construct one are the ``__main__`` demo subcommand and
        ``m0.py``'s fold demo) journals a kind for that replay to fold. Neither
        is a caller-controlled escalation from a live tenant; both are why
        #8982's replay half is pinned as a STATEMENT by
        ``TestReplayIsDeliberatelyUngated5475`` rather than as a gate.

        It runs BEFORE ``self.log.append`` in ``_emit``, and that ordering is
        load-bearing: the append is this seam's COMMIT POINT (the journal IS the
        reconstruction source), so a refusal raised after it would leave a
        record that is
        faithfully replayed on the next ``rebuild_all`` — refused live, widened
        anyway on replay. Fail closed means fail BEFORE the commit, not after.

        One approval-set read per CALL (not per kind property): the read is a
        whole-graph scan (``graph_installed_namespaces``' own COST note), and a
        capture that names several kinds must not pay it once per spelling. It
        is not paid at all for a payload the cheap ``kind_named_packs``
        pre-check clears — every ``core:`` kind, and a bare kind no catalog pack
        declares (``eventKind='AgentSession'`` is one of those: it clears, so the
        extractor's default capture pays NO read). ⚠️ A bare kind a pack DOES
        declare (``eventKind='ruleRevised'``, agent-ops) does not clear it — that
        leg may attribute the bare kind to the declaring pack, so it pays the
        read. See #8977.

        A graph handle that is bound but UNREADABLE raises (an outage must never
        read as "no packs" and silently widen the gate) — so this adds a graph
        READ to writes that previously did none. That is fail-closed and the
        fold would fail on that outage anyway, but it is a new dependency, not
        the note above's "no read".

        A ``projection``-less lane (``EventAPI(log, projection=None)``) has NO
        graph in scope: this gate abstains there rather than reading an approval
        set from a graph it does not hold (see THE BOUND above — the record is
        gated by nothing on that lane, and the replay that folds it is
        deliberately ungated too).
        """
        # ⛔ #9287: the INVISIBLE-KIND refusal is NOT conditional on a graph
        # handle — it is a pure string predicate, and a ``projection``-less lane
        # folds the record through the same ``_upsert_point`` later (or replays
        # it), so it must be refused here too. Only the APPROVAL-set gate
        # abstains (it needs a read). It runs BEFORE the ``log.append`` commit
        # point in ``_emit``, like the approval gate below, so a refused payload
        # leaves nothing to replay.
        #
        # Called as a BARE MODULE FUNCTION, never ``self.…``: this method is
        # exercised UNBOUND by duck-typed API doubles (``mining.py``'s
        # ``EventAPI._refuse_unapproved_kinds(api, record)``, the #7951 ordering
        # test's bare ``_API``), and ``self`` there is not an ``EventAPI`` at all
        # — a ``self.``-routed helper raised ``AttributeError`` on that double
        # (#9591 review cycle 2, P1, measured: test_7951_write_doors.py red).
        _refuse_invisible_point_kinds_in_payload(payload)
        if not graph_reads:
            return
        g = getattr(getattr(self, "projection", None), "g", None)
        if g is None:
            return
        from .pack_state import (
            graph_approval_namespaces,
            payload_names_pack,
            refuse_unapproved_kinds_in_payload,
        )
        if not (spells := payload_names_pack(payload)):
            return
        refuse_unapproved_kinds_in_payload(
            payload, graph_approval_namespaces(g), spells=spells)

    def _emit(self, type_: str, *record: dict, corrects=None,
              actor: str | None = None, **payload) -> dict:
        # ⛔ THE RECORD PAYLOAD IS POSITIONAL (`*record`), and only
        # `add_point` / `add_operator` pass one. See `_NESTED_RECORD_KEYS` for
        # why: a kwargs splat cannot bind the positional SLOT, so a caller's
        # `record=` lands on the envelope as an inert key and this one stays
        # empty. ⚠️ THE CONTENTS ARE CALLER-REACHABLE ALL THE SAME — `add_point`
        # does `p.update(fields)` and hands `p` positionally, which is how the
        # envelope family got in one nesting level down (#11948 review at
        # `95b98e6cf1`). So all three families are handled on this channel
        # BEFORE the append below (the commit point), which therefore leaves
        # nothing to replay: `point` and the envelope names are REFUSED, and a
        # declared vector width is VERIFIED.
        #
        # ⛔ THE VECTOR/RECORD FACTS ARE THREE GROUPS ON THIS CHANNEL, NOT ONE, and
        # the comment that said "the marker family is verified, not refused"
        # described ONE key out of six (the review finding at `ceae1cfcf4`,
        # measured). Precisely:
        #   • the WIDTH is ENUMERATED, then VERIFIED — `_point`'s own
        #     `stamp_journal_embedding` writes it into this very record, so
        #     refusing the two widths it mints would red every vector-bearing
        #     `PointAdded` (⛔ `OperatorAdded` is not in that population: its
        #     record carries no vector at all, so there is no width on it to
        #     refuse — see the header above); the harm is a marker that LIES (a
        #     declared `f32` over values it does not describe), and
        #     `_verify_record_vector_width` asks the seam's own questions: a
        #     declaration outside {`f32`, `f64`} is refused because the seam can
        #     never have produced it; a declared `f32` is checked against the
        #     values (a truthful one lands, a lie is refused); a declared `f64`
        #     is truthful about the values it can DESCRIBE (it only forgoes
        #     narrowing) and refused over one the seam would DROP; EITHER width
        #     declared over NO vector is refused. Only an
        #     unimportable seam keeps the pre-guard posture (a WRITE never fails
        #     on an import); a declaration is never undecidable, because the
        #     seam's mint enumeration is closed, and a VALUE the seam cannot
        #     describe is refused rather than carried;
        #   • the UNCALLERABLE facts are REFUSED on BOTH channels —
        #     `_UNCALLERABLE_RECORD_KEYS`: no seam call here mints them, so a
        #     value can only be a caller's. They are not one family, and each
        #     corrupts something of its own (⛔ "of its own", not "a different
        #     thing": the three source-ref keys SHARE one harm, which is why
        #     they are grouped — see the set's header): `embedding_preserved` claims a
        #     precedent capture nothing here can contradict (⛔ NOT an
        #     attestation suppressor on this door — no channel of `_emit` attests
        #     a record; see that set's comment), `embedding_verbatim` ALSO writes
        #     the node's declared property, `evidence_fold` forges the replay's
        #     recreate anchor, `embeddingText` forges the text↔vector identity,
        #     and the three source-ref keys assert provenance ahead of the
        #     `:Source` resolution. See that set's comment for each harm and
        #     citation;
        #   • the IDENTITY triple is NEITHER — a creating `_point` legitimately
        #     stamps it, so this channel cannot tell the seam's value from a
        #     caller's. Stated as the residual it is rather than folded into a
        #     blanket claim: here an identity is undecidable, and the door that
        #     refuses a caller's version of it is `add_point`.
        _refuse_non_str_keys(payload, f"the `{type_}` envelope")
        _refuse_caller_named_record_keys(payload, f"the `{type_}` envelope")
        if record:
            if len(record) > 1:
                raise TypeError(
                    f"_emit takes at most ONE record payload, got {len(record)}")
            # ⛔ AN EXACT `dict`, because the guard and the ENCODER read it by
            # different paths (#11996 review round 8, measured): a `dict` SUBCLASS
            # can answer `__iter__` with one set of keys while `json.dumps` writes
            # the ones it actually holds, so every guard below inspects fewer keys
            # than the record carries:
            #
            #     _emit("PointAdded", Hidden({"id": "p1", "content": "c",
            #                                 "evidence_fold": True},
            #                                shown={"id", "content"}))
            #       -> LANDED, and `record_is_evidence_fold` read it as a fold
            #
            # A caller-built container whose iteration is not its contents is the
            # same "two readers disagree" class as a lying vector element, and no
            # producer here passes one, so it is refused rather than copied.
            if type(record[0]) is not dict:
                raise ValueError(
                    f"_emit's record payload must be an exact `dict`, got "
                    f"{type(record[0]).__name__}: a mapping SUBCLASS can show the "
                    f"guards one set of keys while the journal's encoder writes "
                    f"another, so no key refusal can cover it (#11948).")
            _refuse_non_str_keys(record[0], f"the `{type_}` record door")
            _refuse_caller_named_record_keys(
                record[0], f"the `{type_}` record door")
            # ⛔ NOT a payload field on this channel either: see
            # `_ENVELOPE_KEYS`'s positional note. `_norm` splices the record
            # over the envelope on replay, so a caller keyed `type=` / `ts=` /
            # `event_id=` here re-types or re-attributes the replayed record.
            _refuse_caller_named_envelope_keys(
                record[0], f"the `{type_}` record door", record=True)
            # ⛔ The UNCALLERABLE facts are REFUSED on this channel too — no seam
            # call here mints them, so a value can only be a caller's. This is
            # the channel an earlier comment left open by describing the family
            # as "verified, not refused": that was true of the WIDTH alone. None
            # of these seven is a function of the values, so no verification can
            # exist for them — `embedding_verbatim` / `embedding_preserved` are
            # ownership claims (the first ALSO writes a declared node property,
            # under the ``#5004 round-3`` note on ``projection/entities.py``'s
            # ``"embedding_verbatim"`` entry), `evidence_fold` is the replay's
            # recreate anchor, `embeddingText` the text↔vector identity, and the
            # three source-ref keys provenance the `:Source` resolution asserts.
            # ⛔ AND THE KEYWORD CHANNEL IS NOT THE SAFER ONE, which is why both
            # arms exist: a flat record's top-level `evidence_fold` is read by
            # `projection.record_is_evidence_fold`, and `_norm` splices a nested
            # record over the envelope so it is read identically on replay.
            _refuse_uncallerable_record_keys(
                record[0], type_, "record door")
            # ⛔ ONE KEY IS EXEMPT FROM THAT SET, AND IT IS DERIVED, NOT TRUSTED
            # (#11996 review rounds 6, 9, 10 — each measured). `add_point` mints
            # `sourceVersionTransit` into the record it hands `_emit`, so refusing
            # it outright would refuse the seam's own record — but EVERY trust
            # signal tried so far is caller-suppliable: scoping by CHANNEL NAME let
            # any direct `_emit` caller forge it (`_emit(… {"sourceVersionTransit":
            # [["src:1", "h-forged"]]})` LANDED and the replay stamped the forged
            # `:Source` read-version), and a `seam_mints` keyword was one the
            # caller could just pass (`_emit(…, _seam_mints=True)` LANDED the same
            # forgery). So the test is a PREDICATE instead: re-derive the value the
            # seam would mint and compare. A caller can only pass the TRUTH.
            #
            # ⛔ AND THE COST IS THE POINT: this runs `resolve_source_versions` a
            # SECOND time on the live write path, because `add_point` resolved it
            # to mint the value it hands this funnel. Measured at #11996 review
            # round 13 (5 `add_point` calls, one `contentHash`-bearing source
            # each): `resolve_source_versions` 10 calls vs 5 with the arm disabled,
            # `resolve_source_key` 15 vs 10, 47.8 ms vs 37.4 ms — ~2 ms per
            # sourced point, on the extraction hot path.
            #
            # It is NOT removable by reusing the caller's value, which is the
            # whole class this PR closes: every channel that would carry it
            # (`seam_mints`, the channel name, a module-private token a caller can
            # import) is caller-suppliable or caller-reachable, and a re-used value
            # is a TRUSTED one. A memoised resolution would have to be keyed by
            # `(graph, extractedFrom)` on the instance, i.e. a cache whose staleness
            # is a new correctness surface for a ~2 ms saving. Stated here instead
            # of left for the next reader to measure.
            #
            # ⛔ AND THE SECOND READ IS A STATED RESIDUAL, not a covered case
            # (#11996 review round 16, measured): `resolve_source_versions`
            # documents that a Source's `contentHash` is bumped IN PLACE and
            # UNJOURNALLED (`#5024`), so a concurrent re-index that lands between
            # `add_point`'s mint and this re-derivation makes the two readings
            # disagree and refuses a CORRECT write:
            #
            #     add_point(…, extractedFrom=[DOC])  -> ValueError "this seam did
            #       not mint for it" (measured by bumping the Source inside the
            #       window; the same call lands on retry, and landed on `main`)
            #
            # The window is one `resolve_source_versions` (~ms), the failure is
            # fail-CLOSED (nothing journalled) and retryable, and this arm is what
            # refuses a forge — so the trade is stated rather than closed. The
            # design that removes it (make `_emit` the single authority: derive
            # once, publish on a payload copy, delete `add_point`'s mint) is a
            # decision about the record contract, not a lane's initiative; it is
            # preserved on `fix/11996-r13-single-authority` (`45f26c31d2`) and
            # filed as #12115.
            if _named_from_caller(record[0], _DERIVE_ON_RECORD):
                _declared_sv = record[0]["sourceVersionTransit"]
                _g = getattr(self.projection, "g", None)
                _derived_sv = None
                if _g is not None and record[0].get("extractedFrom"):
                    from .projection.edges import _source_version_transit, resolve_source_versions
                    # ⛔ A NON-ITERABLE `extractedFrom` IS A REFUSAL, not a crash
                    # (#11996 review round 16, measured): `resolve_source_versions`
                    # starts with `list(source_ref)` for anything that is not a
                    # `str`, so `extractedFrom=123` escaped this arm as a bare
                    # `TypeError` while every other refusal in this file is a
                    # `ValueError`. Measured: `_emit(… "extractedFrom": 123,
                    # "sourceVersionTransit": [[doc, "h1"]])` -> TypeError. Only
                    # `TypeError` is caught: an I/O failure from the graph read is
                    # an outage, not a caller's bug, and must keep propagating.
                    try:
                        _derived_sv = _source_version_transit(
                            resolve_source_versions(_g, record[0]["extractedFrom"]))
                    except TypeError:
                        raise ValueError(
                            f"`{type_}`'s record carries a `sourceVersionTransit` "
                            f"and an `extractedFrom` this seam cannot resolve "
                            f"({type(record[0]['extractedFrom']).__name__}): the "
                            f"anchor is DERIVED from the `:Source` refs, so they "
                            f"must be a `str` or a sequence of them (#5038 / "
                            f"#11948).") from None
                # ⛔ THE SHAPE IS CHECKED BEFORE THE COMPARISON, and the comparison
                # is exact-typed (#11996 review round 11, measured). `!=` delegates
                # to the CALLER's `__eq__`, so a `str` subclass answering True
                # type-checked nothing and landed a forged anchor — the same
                # carrier every other arm in this module already refuses:
                #
                #     class _Eq(str):
                #         def __eq__(self, other): return True
                #     _emit("PointAdded", {… "extractedFrom": [DOC],
                #           "sourceVersionTransit": [[DOC, _Eq("h-FORGED")]]})
                #       -> LANDED, and the replay stamped `h-FORGED` as the
                #          `:Source` read-version
                #
                # `_source_version_transit` mints `[[str, str], …]` and nothing
                # else, so any other shape cannot be what the seam produced.
                # ⛔ EXACT `list` PAIRS, AND `tuple` IS *NOT* ACCEPTED (#11996
                # review round 16, measured): this arm used to admit
                # `type(_pair) in (list, tuple)`, which its own sentence above
                # contradicts — `edges.py`'s mint is
                # `[[ref, h] for ref, h in versions.items()]`. A `tuple` pair
                # therefore passed the SHAPE check and was refused one arm later by
                # the VALUE comparison ("did not mint for it"), i.e. the caller was
                # told the wrong reason for a shape this seam never produces.
                _shape_ok = (
                    type(_declared_sv) is list
                    and all(type(_pair) is list and len(_pair) == 2
                            and type(_pair[0]) is str and type(_pair[1]) is str
                            for _pair in _declared_sv))
                if not _shape_ok:
                    raise ValueError(
                        f"`{type_}`'s record carries a `sourceVersionTransit` that "
                        f"is not the shape `_source_version_transit` mints "
                        f"(`[[str, str], …]`, exact types): a subclass can answer "
                        f"`==` for itself, so the value is refused before it is "
                        f"compared (#5038 / #11948).")
                if _derived_sv is None or _derived_sv != _declared_sv:
                    raise ValueError(
                        f"`{type_}`'s record carries a `sourceVersionTransit` that "
                        f"this seam did not mint for it: the value is the "
                        f"`:Source` read-version anchor the replay stamps, so it is "
                        f"DERIVED here and a caller-set one is refused (#5038 / "
                        f"#5256 / #11948). Pass `extractedFrom` and let `add_point` "
                        f"mint it.")
            # …THEN the width is VERIFIED, because the seam DOES mint that one
            # (and refuses a declaration outside the two it can mint — see
            # `_verify_record_vector_width`). Its limit is stated where it is
            # implemented, and the IDENTITY triple (`embedding_model` /
            # `_revision` / `_text_hash`) is the residual this channel cannot
            # decide — a creating `_point` legitimately stamps it, so the door
            # that refuses a caller's version is `add_point` (its
            # `_forged_keys`).
            _verify_record_vector_width(record[0], type_, payload)
            payload["point"] = record[0]
        # #7951 — THE JOURNAL ORDERING CONTRACT: **journal first, then
        # project.** The full contract (and the failure-mode argument) lives at
        # the top of `tortoise/sdk.py`; read it before adding a writer. It is
        # `docs/architecture/STORAGE-ARCHITECTURE.md` §3's write protocol —
        # *"Writes go to the truth first, then project"* — and the live doors
        # that state it explicitly are this method and
        # `TortoiseSDK._apply_and_journal`. The append below is the COMMIT
        # POINT: it must precede the fold, and it is deliberately not
        # best-effort (a failed append must not let the fold run).
        event = {
            "event_id": ulid(),
            "ts": now_iso(),
            "type": type_,
            "initiated_by": self.initiated_by,
            "agent_id": self.agent_id,
            "corrects": corrects,
            **payload,
        }
        # #2600: optional human actor (default None = extraction lanes
        # byte-identical). `actor` is a keyword parameter on `_emit` — the
        # SDK emitter provides it; EventAPI callers never touch it. No
        # caller change required for existing lanes.
        if actor is not None:
            event["actor_user_id"] = actor
        # #11835 review finding 1 (P2): this funnel is the seam's OWN journal
        # door, and it enforced neither half of the vector contract. Its sibling
        # `add_point` does both — it stamps (`stamp_journal_embedding`) and it
        # refuses a forged marker (`_forged` → `ValueError`); `_emit` had
        # neither, so `add_event(**extra)` / `emit_belief(**payload)` minted a
        # NEW record with the marker absent (a state the contract reserves for
        # PRE-CHANGE / FOREIGN records) and accepted a caller's `f32` over an
        # off-grid float64 vector (a marker that LIES — measured, `evt-2`).
        #
        # ⛔ REFUSAL runs before the stamp and both run before the commit point
        # below (`self.log.append`), for `_refuse_unapproved_kinds`' own reason:
        # a refusal raised after the append would leave a record the replay
        # folds. The marker is not a caller's to set — it DESCRIBES the values,
        # so a caller that supplies one is asserting a width nothing verified.
        # #11948: the same argument one key family over — the ENVELOPE is this
        # funnel's to build. `event_id` is excluded here because `add_document`
        # deliberately pins it (#125/#167, tests/test_api.py) and takes named
        # parameters rather than a caller's dict; every splatting door refuses
        # it instead (`_refuse_caller_named_envelope_keys`).
        _forged_envelope = sorted(set(payload) & _ENVELOPE_KEYS)
        if _forged_envelope:
            raise ValueError(
                f"{_forged_envelope} are journal ENVELOPE fields and cannot be "
                f"set via {type_}'s payload: the envelope is built here, so a "
                f"colliding caller key re-types or re-attributes the record "
                f"instead of adding a field")
        # ⛔ `event_id` IS THE ONE ENVELOPE NAME `_ENVELOPE_KEYS` LEAVES OUT, and
        # leaving it out here was a hole big enough to re-identify any record
        # (#11996 review round 21, measured). The exclusion is justified by
        # `add_document`'s recorded `#125` pin, which passes `event_id=` as ITS OWN
        # named parameter — but this channel serves EVERY type, so the exclusion
        # applied far wider than the reason for it:
        #
        #     api._emit("PointAdded", id="p1", content="c", event_id="forged-id")
        #       -> JOURNALLED `{"event_id": "forged-id", …, "type": "PointAdded"}`
        #
        # `projection._warn_entity_mutation_fold_miss`'s docstring calls `event_id`
        # "the only handle that locates the diverging journal line", so a caller
        # could pick the identity of its own record — the same harm the record
        # channel refuses it for
        # (`_ENVELOPE_KEYS_AT_PASSTHROUGH`, which the three splatting doors use).
        # ⛔ Scoped to the ONE producer that legitimately passes it: reversing
        # `add_document`'s pin would be a reopen of #125/#167, not a fix, and
        # `tests/test_11821_journal_vector_width.py::test_the_envelope_collision_
        # guard_keeps_add_documents_event_id_pin` pins that it still lands.
        if "event_id" in payload and type_ != "DocumentCreated":
            raise ValueError(
                f"['event_id'] are journal ENVELOPE fields and cannot be set via "
                f"{type_}'s payload: the envelope id is minted here, and a "
                f"caller-chosen one re-identifies the record (#11996). Only "
                f"`add_document`'s own `#125` pin may pass it")
        _forged_markers = sorted(set(payload) & _SERVER_MANAGED_EMBEDDING_KEYS)
        if _forged_markers:
            raise ValueError(
                f"{_forged_markers} are server-managed journal fields (embedding "
                f"width / storage form / origin) and cannot be set via "
                f"{type_}'s payload")
        # ⛔ AND THE ONE ENVELOPE KEY PRESENCE CANNOT REFUSE IS TYPE-CHECKED HERE
        # (#11996 review round 18, measured). `projection_version` is kept out of
        # `_ENVELOPE_KEYS` because `add_point` / `add_operator` pass their OWN int
        # through this same channel, so a presence refusal would break them — but
        # NOTHING checked the type, and this append is the commit point:
        #
        #     _emit("PointAdded", id="p", content="c", projection_version="x")
        #       -> JOURNALLED with a `str` version, and the flat record is then
        #          folded AWAY: `fold([ev])` returns `{}` with NO exception, because
        #          a flat record carries no `point` and `_apply_one` returns at its
        #          non-dict-point guard — so the write survives and the fold loses
        #          the point (live/replay divergence). The LOUD shape is the
        #          record-positional one — `_emit("PointAdded", {"id": …, "content":
        #          …}, projection_version="x")` — which raises on every replay:
        #          TypeError: '>=' not supported between instances of 'str' and 'int'
        #          (both measured; `projection/__init__.py`'s fold).
        #
        # A record every replay refuses is worse than a refused write: it is the
        # durable-corruption shape this whole fix exists to prevent, and the three
        # passthrough doors already refuse the KEY outright.
        if "projection_version" in payload:
            _pv = payload["projection_version"]
            if type(_pv) is not int:
                raise ValueError(
                    f"projection_version={_pv!r} on {type_}'s payload must be an "
                    f"exact `int`: the fold compares it with `>= 2` and the "
                    f"journal append is the commit point, so a non-numeric value "
                    f"is a record every replay refuses (#11996)")
            # ⛔ Stated, not implied: the VALUE is the producers' (`2` from
            # `add_point` and `add_operator`, `tortoise/sdk.py`'s
            # `"projection_version": 2` elsewhere), so an `int` a
            # caller supplies still lands on this channel — a `1` suppresses the
            # `#49` v2 context strip. That is the SAME trade-off the
            # `_RECORD_ENVELOPE_EXTRA` note records, kept so the producers' own
            # call is not refused; a presence refusal here would be the fix for
            # both, and it needs their calls moved off this channel first.
        # ⛔ The same set on the OTHER channel. `_forged_markers` above covers the
        # vector family; these are the record facts no seam call here mints, and
        # this channel is not the SAFER one — `record_is_evidence_fold` reads a
        # flat record's top-level flag, and reads the positional record's nested
        # one identically on replay (`_norm` splices `point` over the envelope).
        # Both arms are here because dropping either leaves a forge.
        _refuse_uncallerable_record_keys(dict(payload), type_, "payload")
        # ⛔ THE STAMP covers the vector THIS door carries — top-level
        # `embedding` only. The record-shaped doors nest their payload
        # (`add_point` / `add_operator` pass their record positionally, rendered
        # onto the envelope as `point`), so their vector is stamped by the door
        # that built it and is not touched here.
        # `creating=False` because this door computed no vector from content
        # riding with it: the seam still decides every arm from the values —
        # ours narrows and declares `f32`, an off-grid / unrepresentable / a
        # verbatim-or-preserved vector keeps its raw form and declares `f64`.
        if "embedding" in event:
            try:
                from .embeddings import stamp_journal_embedding
            except Exception:  # noqa: BLE001, RUF100 — #5148: never fail a write on an import
                # Same posture as `add_point`: an unimportable seam must not
                # fail a WRITE. The key is present, so PRESENCE IS OWNERSHIP
                # still holds and the replay cannot invent a vector; only the
                # normalisation and the width declaration are lost.
                stamp_journal_embedding = None
            if stamp_journal_embedding is not None:
                event = stamp_journal_embedding(event, creating=False)
        # ⛔ RE-VERIFY AFTER THE STAMP: the stamp can CHANGE the pair the fold
        # reads. `_drop_journalled_embedding` sets the vector to an owned `None`
        # and POPS the width from the dict it was given, so a record declaring
        # `f32` beside a vector the seam drops would leave `f32` over `None` —
        # the exact state the no-vector refusal forbids, reached one call later
        # (#12000 round-10 review, F1b — measured with `embedding="abc"` on the
        # payload: journalled `env.embedding=None env.width=None
        # record.width='f32'`). The pre-stamp check now refuses the non-numeric
        # vector that produced that instance, so this is the belt to that brace:
        # any FUTURE drop path lands here too, before the commit point.
        if isinstance(event.get("point"), dict):
            _verify_record_vector_width(
                event["point"], type_,
                {k: v for k, v in event.items() if k != "point"})
        # #5475 (residual): the approved-pack gate, BEFORE the commit point.
        # Every EventAPI mutation funnels through here, so gating here is what
        # closes the seam for the doors it has today AND for a door added
        # tomorrow — the argument the SDK makes for its own funnel, one
        # transport over. See `_refuse_unapproved_kinds` for why it must run
        # before the append rather than after.
        self._refuse_unapproved_kinds(event)
        # #2782 (review Finding 2): the SAME seam, the same ordering, one field
        # family over. This is the one live props writer that never reaches
        # `_sanitize_props` (its own words: an internal ingest/mining/CLI
        # seam), so the SDK intake's money gate did not cover it — measured, an
        # `EventAPI(...).add_object(name, kind, amountMinor=25_000_000)` was
        # ACCEPTED and left `amountMinor=25000000` durable on the node with no
        # `currency` and no `minorUnitExponent` beside it: the exact shape #2782
        # exists to refuse, one transport over, through a producer the
        # extractor, `ingest`, `docs_miner`, `mining` and the CLI all use. (The
        # READER renders the two missing members as `None`; the STORE holds no
        # null-valued property — `SET n += {currency: null}` REMOVES the key —
        # so "`currency: None` on the node" is a readable state, not a stored
        # one. #11836 review Finding 2, measured.)
        #
        # It runs BEFORE the append below for `_refuse_unapproved_kinds`' own
        # reason: the append is this seam's COMMIT POINT, so a refusal raised
        # after it would leave a record the replay would fold.
        #
        # ⚠️ The bound is the same one #5475 states, and it is deliberate: this
        # gates a write that HAS a graph handle. `rebuild_all` folds a journal
        # directly and stays ungated, because the journal is the RECONSTRUCTION
        # SOURCE — refusing at replay would make an already-committed record
        # unfoldable, which is strictly worse than reproducing it.
        #
        # #11836 review Finding 1 (measured, fail-open): a gate over the ENVELOPE
        # only was INERT on the Point door. Two of the seam's doors do not splat
        # their props at the top level — `add_point` builds `p` and hands it to
        # `_emit` as the record payload (rendered onto the envelope as `point`),
        # `add_operator` nests its record the same way — so
        # `add_point(..., amountMinor=25_000_000)` was ACCEPTED and left
        # `amountMinor` durable with no currency beside it (the reader spells
        # that `currency: None`; the store holds no null key), which is the exact
        # shape this gate exists to refuse. The sibling kind gate two calls up
        # (`_refuse_unapproved_kinds` → `kind_spellings_in_payload`) already
        # states the rule: a gate that reads only the top level re-opens the
        # hole one nesting level down, so it WALKS the payload. This does the
        # same over the money group — which is why the seam's "gates the doors
        # it has today AND a door added tomorrow" argument now holds for the
        # record-shaped doors too.
        refuse_malformed_money_in_payload(event, door=f"EventAPI.{type_}")
        # #11915: the same walk for the percent shape — `add_point(...,
        # basisPoints=20000)` must not land a 200% ratio on a node, for the same
        # reason `amountMinor` without a currency must not land.
        refuse_malformed_percent_in_payload(event, door=f"EventAPI.{type_}")
        # #9190: the append REPORTS the record's journal position, and that
        # number is the record's `journal_event.seq`. Dropping it here is what
        # kept layer 1 empty on live writes (the row was deferred with a
        # `position=False` that attributed the gap to #9190, which has landed).
        seq = self.log.append(event)
        # #9188: the Postgres record, AFTER the append (the commit point, §17.1)
        # and before the fold. This door is a LIVE journal writer — the v1
        # extractor, `ingest`, `docs_miner`, `mining` and the CLI all append
        # through it — so without this call every one of their records would
        # reach the journal and never the record. Best-effort by construction
        # (`write_live_record` counts and logs, never raises): the append above
        # already committed.
        #
        # ⛔ but only when the log IS a journal. `durable` is the log's own
        # answer, defaulting True for every duck-typed double and for
        # `EventLog`; `TortoiseSDK._extract_session_llm` hands this door an
        # `_InMemoryEventLog` (no file I/O, by design) whose records no journal
        # ever carries, so a row from it could never be reproduced or healed by
        # a replay — the record is `replay(journal)`, and a scratch buffer is
        # not a journal. The seam DECLINES it (no row) and counts the decline on
        # `monitoring.metrics()["record_non_journal_skips"]`, NOT on
        # `record_write_failures`: a lane with no journal by design lost nothing a
        # replay could restore (#9231 review P2).
        record_writer.write_live_record(
            event, durable=getattr(self.log, "durable", True), seq=seq)
        if self.projection is not None:
            # #7951 §17.1: journal-first door — the append above is the COMMIT
            # POINT, so the fold runs inside the live-write scope.
            with _live_write_scope("event_api._emit"):
                self.projection.apply(event)
        return event

    def emit_belief(self, type_: str, **payload) -> None:
        """#2884 A6: best-effort journal append for the EP/dream belief write-back.

        The ingest CLI's lazy EP propagation passes its ``EventAPI`` here
        instead of hand-building a JSONL envelope, so this lane's belief
        records go through the SAME ``_emit`` shape as every other ingest
        event (one envelope, not three). ``_emit`` is guarded here because the
        graph write has already committed by the time EP returns: an
        ``OSError``/``ENOSPC`` must never propagate out of ``ep.run()`` and
        crash the public ingest CLI. Mirrors
        ``TortoiseSDK._emit_event``'s best-effort JSONL branch.

        ⛔ The best-effort arm covers I/O, not a malformed payload: a caller
        that names a RECORD-SHAPED key here (`point=`) is refused with
        ``ValueError`` BEFORE this door touches the log, because a caller-built
        record is folded by the replay without the vector rule the record doors
        apply (`_NESTED_RECORD_KEYS`). Refusing is the honest report; silently
        journalling a record whose `embedding_width` marker lies would be the
        false-FACT shape this module's keys exist to prevent. (`_emit` refuses
        the same payload again, which is what makes the rule total; the call
        here exists so the raise is not swallowed by the arm below.)

        #11948 F2: that reasoning covers the ENVELOPE and MARKER families too,
        and it did not. They were refused only inside ``_emit``, so the raise
        landed in the ``except`` below — the belief record was DROPPED with a
        log line while the identical ``point=`` refusal raised. One class of
        malformed payload, two answers; the caller of a best-effort seam is the
        only one who can react, so a payload-shape refusal is reported here,
        BEFORE the arm that swallows I/O, wherever the gate is PURE enough to
        run here: the record-shaped, envelope, marker, money, percent and
        uncallerable-record-fact families, and the invisible-character half of
        the kind gate. Every one of those arrived the same way — a gate `_emit`
        got and this door did not — EXCEPT the uncallerable record facts, which
        are a different shape: #11948 arms them in this very call AS WELL AS in
        `_emit`, so no released state has one armed without the other. (An
        intermediate revision of this fix armed only `_emit`, and that is the
        `emit_belief` drop the paragraph above records as measured — so "from the
        start" describes the SHIPPED pairing, not a history in which nothing was
        ever half-armed. #11996 review round 17 found these two sentences
        contradicting each other.) This
        paragraph used to count "FIVE" over its own list of seven; it is written
        without a count now, because both earlier counts disagreed with the list
        they sat under.)

        ⛔ THE APPROVAL half of the kind gate is the ONE exception, and it is
        not a hole: it needs a whole-graph READ, and that read RAISES on a
        bound-but-unreadable handle (an outage is not a caller's bug). Run
        before the arm, it would be an I/O failure escaping ``ep.run()`` — the
        thing this door's best-effort posture exists to prevent (measured at
        `3f5c61601`). So it runs inside ``_emit``, and the ``except`` below
        RE-RAISES its named refusal instead of logging it away: an outage stays
        swallowed, a refused pack is reported. Stated as the split it is,
        because the flat claim "every payload-shape refusal is raised here"
        stopped being true the moment the pure-only call landed.
        """
        _refuse_caller_named_funnel_keys(payload, "`emit_belief`")
        # …and the MONEY family, which is the other gate `_emit` runs and this
        # arm would otherwise swallow the same way. Found by the review of the
        # F2 fix itself, and measured at that head: `emit_belief(...,
        # amountMinor=25_000_000)` was dropped with a log line while
        # `add_object(..., amountMinor=25_000_000)` raised — the identical
        # divergence F2 was filed for, one family over. Same predicate the
        # funnel uses (`refuse_malformed_money_in_payload`), so the two cannot
        # disagree about which shapes are malformed.
        refuse_malformed_money_in_payload(payload, door="EventAPI.emit_belief")
        # …and the PERCENT family, the same divergence a THIRD time, found by
        # the verification pass on the merge that brought this gate in
        # (`927c24e233` / #11955 added it to `_emit` and no door): measured at
        # the merge head, `emit_belief(..., basisPoints=20000)` returned None
        # with the journal EMPTY while `add_point`/`add_object`/`add_event`
        # raised `PercentValueError` on the identical payload. Pure walk of the
        # same shape as the money one, so it belongs on this side of the arm.
        refuse_malformed_percent_in_payload(
            payload, door="EventAPI.emit_belief")
        # …and the KIND family, the last gate `_emit` runs before its commit
        # point. Found by the review of the money fix one family over AGAIN,
        # with the same measurement: at that head
        # `emit_belief(eventKind="marketing:campaign")` and
        # `emit_belief(pointKind="statement\u2003")` were dropped with a log
        # line while `add_object`/`add_event` raised on the identical payload.
        # The predicate is the funnel's own (a bare module call inside it, so a
        # duck-typed double still works), and it abstains identically when the
        # lane holds no graph — so calling it here cannot disagree with `_emit`.
        # ⛔ `graph_reads=False`, and that is load-bearing: this call sits
        # OUTSIDE the arm below, so it may run only the PURE half. The approval
        # half reads the whole graph and RAISES on an unreadable handle, which
        # would be an I/O failure escaping `ep.run()` — the one thing this
        # door's best-effort posture exists to prevent (measured at
        # `3f5c61601`: a bound-but-unreadable projection propagated `OSError`,
        # journal delta 0). `_emit` re-runs the full gate inside the arm, at its
        # commit point, so the graph is read once per record and not twice.
        self._refuse_unapproved_kinds(payload, graph_reads=False)
        try:
            self._emit(type_, **payload)
        except Exception as exc:  # noqa: BLE001, RUF100
            # ⛔ A DETERMINISTIC PAYLOAD REFUSAL IS NOT THE I/O FAILURE THIS ARM
            # EXISTS TO SWALLOW. The approval half of the kind gate is the one
            # payload-shape gate that still runs inside the arm (the eager call
            # above is pure by contract), so logging-and-dropping it would
            # restore exactly the F2 divergence one family over: the same
            # payload raises at `add_object` and vanishes here. Measured at
            # `8ee861cbaa`: `emit_belief("ConfidenceChanged", id=…,
            # eventKind="marketing:campaign")` returned normally with the
            # journal empty. `UnapprovedKindNamespace` is the type the SDK
            # publishes for this distinction — its own docstring: a transport
            # "can tell this DETERMINISTIC payload refusal from a transient
            # graph failure WITHOUT parsing the message". An UNREADABLE handle
            # raises OSError, not this, so an outage keeps its best-effort
            # posture and the graph is still read once, inside `_emit`.
            from .sdk import UnapprovedKindNamespace
            if isinstance(exc, UnapprovedKindNamespace):
                raise
            logger.warning(
                "failed to append %s event to ingest log: %s", type_, exc)

    def _point(self, content, provenance, operator=None) -> dict:
        prov = dict(provenance)
        prov.setdefault("run_id", self.current_run)
        # #49 Phase 2: context param removed — context is deprecated.
        # Context/domain is now tracked via pointKind + provenance extractedFrom.
        p = {
            "id": ulid(),
            "content": content,
            "operator": operator,
            "provenance": prov,
            # #432/#1088: EventAPI DELIBERATELY writes status="draft" — the
            # legacy journal-composer posture, NOT SDK parity (SDK default is
            # live). An explicit draft is never auto-promoted; it stays draft
            # until `promote_point` / `update_point(status="live")` — the #131
            # clause now fills only a never-set status.
            "status": "draft",
            "createdAt": now_iso(),
        }
        # #5004: journal the vector + its identity. `EventAPI` is the SECOND
        # journal producer (ingest / mining / CLI write through it), and the
        # invariant `derived = replay(journal)` is producer-agnostic: without
        # this, an ingest-created Point's replay re-encoded under whatever
        # embedder was configured and silently produced a DIFFERENT graph from
        # the same journal. The vector is computed with the SAME store-declared
        # width the projection will use, and stamped through the SAME shared
        # seam the SDK writer uses (`stamp_journal_embedding`) so the two
        # producers cannot drift. `apply()` then RESTORES it verbatim, so the
        # live node and the journal agree by construction (one compute, not
        # two).
        if operator is None and isinstance(content, str) and content:
            vec = None
            try:
                from .embeddings import encode_for_row, stamp_journal_embedding
            except Exception:  # noqa: BLE001, RUF100
                # #5148: the seam MODULE is unimportable AT THIS MOMENT. Note
                # what this does NOT claim: `tortoise.embeddings` is imported
                # transitively at package-import time (sdk -> cross_lens), and
                # `numpy` is a core dependency, so a plain missing-dependency
                # install never reaches here. What it covers is an import hook
                # or an import environment that fails at the call site — and
                # the principle that a WRITE must not depend on an import
                # succeeding, which is the rule this block already follows for
                # an unavailable EMBEDDER. PRESENCE IS OWNERSHIP holds
                # regardless, and NOTHING is lost by skipping the stamp: with
                # no vector, `stamp_journal_embedding` only re-sets `embedding`
                # to the `None` written below, and its attestation block
                # requires a vector to fire.
                stamp_journal_embedding = None
            if stamp_journal_embedding is not None:
                try:
                    vec = encode_for_row(
                        content,
                        getattr(self.projection, "required_embedding_dim", None))
                except Exception:  # noqa: BLE001, RUF100
                    vec = None
            # PRESENCE IS OWNERSHIP (#5004 round-3): the key is set EVEN when
            # the encode failed or the embedder is unavailable, so the replay
            # is told the journal owns this field for this id and must not
            # invent a vector the live node does not have.
            p["embedding"] = vec
            if stamp_journal_embedding is not None:
                stamp_journal_embedding(p, creating=True)
        return p

    # -- ingest lifecycle / idempotency gate --------------------------------
    def begin_ingest(self, source_id, extractor_version, key: IngestKey, *,
                     force: bool = False) -> IngestResult:
        # Lazy-cache IngestStarted events: scan the log once, then O(1) lookups.
        if self._ingest_cache is None:
            self._ingest_cache = {}
            for e in self.log.read_all():
                if e["type"] == "IngestStarted":
                    k = e["key"]
                    self._ingest_cache[
                        (k["kind"], k["value"], e["extractor_version"])
                    ] = e["run_id"]

        cache_key = (key.kind, key.value, extractor_version)
        if cache_key in self._ingest_cache and not force:
            self.current_run = None
            return IngestResult(run_id=self._ingest_cache[cache_key], skip=True,
                                reason="already processed (same key + extractor_version)")

        # Reprocess (new version or --force): supersede every prior run for this
        # key, whatever its version, so the latest run wins.
        superseded = [
            rid for ck, rid in self._ingest_cache.items()
            if ck[:2] == (key.kind, key.value)
        ]
        run_id = ulid()
        if superseded:  # reprocess: retract the superseded runs' points first
            points = fold(self.log.read_all())
            for p in list(points.values()):
                if p["provenance"].get("run_id") in superseded:
                    self._emit("PointRetracted", id=p["id"])
        self._emit("IngestStarted", run_id=run_id, source_id=source_id,
                   extractor_version=extractor_version, key=key.as_dict(),
                   supersedes=superseded or None)
        # Update cache with the new run.
        self._ingest_cache[cache_key] = run_id
        self.current_run = run_id
        return IngestResult(run_id=run_id, skip=False)

    # -- mutations ----------------------------------------------------------
    def add_point(self, content, provenance, *, corrects=None, **fields) -> str:
        p = self._point(content, provenance)
        p.update(fields)
        # #5004 review: `_point` stamped a SERVER-computed vector. A
        # caller-supplied `embedding` in `fields` replaces that vector while the
        # identity/text-hash still describe the server one — a false
        # attestation that the replay would then trust. Re-normalise the FINAL
        # payload: a foreign vector is carried (so it is restored verbatim), but
        # with NO attestation, because its origin is not ours to vouch for.
        #
        # (The SDK surface does NOT refuse a caller-supplied `embedding`: `create_point`
        # and `update_point` store one VERBATIM by a recorded decision (PR #3018 review
        # P2 — see `tortoise.sdk::_sanitize_props`, which rejects the embedding IDENTITY
        # props but deliberately not `embedding` itself). ⚠️ But a vector cannot arrive
        # HERE from those tools: their journal half lives in `tortoise.sdk`
        # (`_journal_snapshot_from_create_map` → `_emit_event`'s
        # `clean.setdefault("embedding", None)` for `create_point`, and the
        # `PointRevised` record's `emit_props` for `update_point`). This branch guards
        # the vector a DIRECT `EventAPI` caller hands to `add_point(fields=…)`. ⚠️ The
        # cited test pins the CARRY, not this branch: it runs under an unimportable
        # seam, where the branch body reduces to an assignment `p.update(fields)` has
        # already made — so it stays green if this block is deleted, as its own
        # docstring says ("This pins the GUARD, not the ASSIGNMENT ORDER"). What the
        # block buys when the seam IS importable is the re-normalisation below: the
        # caller's vector must not ride the SERVER's identity/text-hash attestation.
        # Two guards — not one — keep a foreign vector from minting a claim it
        # has not earned: the carry-with-no-attestation below (it cannot carry OUR
        # attestation) and the `_forged` refusal a few lines down (the identity/marker
        # keys that would let it FORGE one are rejected outright).)
        if "embedding" in fields:
            # The key stays PRESENT in both branches: an explicit None is the
            # journal saying "this id has no vector", which the replay must
            # honour by leaving it unset rather than re-encoding. Set it FIRST,
            # so presence holds even when the seam below is unreachable.
            p["embedding"] = fields["embedding"]
            try:
                from .embeddings import JOURNAL_VECTOR_WIDTH_KEY, stamp_journal_embedding
            except Exception:  # noqa: BLE001, RUF100
                # #5148 review: the same import-at-the-call-site lane as
                # `_point` — an unimportable seam must not fail a WRITE. Only
                # the stamp's normalisation safety net is lost; the key is
                # already present, so PRESENCE IS OWNERSHIP is intact.
                JOURNAL_VECTOR_WIDTH_KEY = None
                stamp_journal_embedding = None
            if stamp_journal_embedding is not None:
                # #11835 review F2 (P1): the width marker describes the vector
                # that `_point` stamped, and `p.update(fields)` has just
                # REPLACED that vector with the caller's. It must die with the
                # value it described: `_stamp_journal_vector_width`'s
                # idempotency guard reads an existing `f32` as "already this
                # seam's own output", so it would return early and never
                # re-decide — leaving a CALLER's off-grid values declared
                # `f32`, which is precisely the population #5004 round-7 says
                # a later width/base64 migration must not narrow. Measured, and
                # named by the shape it holds for: with a caller vector whose
                # text is ALREADY the seam's shortest float32 round-trip, delete
                # this pop and it is journalled `f32` (the seam's idempotency
                # guard reads that text as its own output); that shape is pinned
                # by test_add_point_does_not_keep_the_servers_width_over_an_
                # f32_text_caller_vector. An OFF-GRID caller vector is refused
                # `f32` with or without this pop since `61a684bf7f` (the seam
                # re-decides from the values), so that sibling pin no longer
                # discriminates the pop — it discriminates the decision rule.
                p.pop(JOURNAL_VECTOR_WIDTH_KEY, None)
                stamp_journal_embedding(p, creating=False)
        # #5004 round-7: the journal MARKERS are server-minted, and this is the
        # one producer that does not pass through `_sanitize_props` (an
        # internal ingest/mining/CLI seam). Allowing them through let a caller
        # (a) claim a server vector was "caller-owned, stored verbatim" —
        # flipping the replay's storage form — and (b) set
        # `embedding_preserved`, which makes `stamp_journal_embedding` skip the
        # model/text-hash attestation that R1 exists to keep. Reject here for
        # the same reason the SDK and MCP boundaries do.
        # (`_SERVER_MANAGED_EMBEDDING_KEYS` is the vector's own description —
        # width, storage form, origin attestation — and the ONE spelling of that
        # set: `_emit` refuses the same keys a few hundred lines up, so a caller
        # cannot reach the journal with a marker through the door that is not
        # `add_point`. #5004 round-8: the IDENTITY keys are in it because they
        # never become node properties (`_POINT_HANDLED`), so this is not a
        # parity break — but the attestation itself is forgeable: a
        # caller-written `embedding_model` rides the journal and makes the
        # replay report a model change that never happened. #11821 P0: the
        # vector-WIDTH marker is writer-minted too. A caller-set value would
        # describe a width the payload's text does not have, and it never
        # becomes a node property — so accepting it would be a forged journal
        # FACT with no node to correct it on.)
        _forged_keys = _SERVER_MANAGED_EMBEDDING_KEYS | {
            # #8488 scope 1: the enriched text is writer-minted
            # and no `PointRevised` arm replays it — a caller-set
            # value would be a live-only write a rebuild drops,
            # and would forge the #4194 text↔vector identity.
            "embeddingText",
            # #5256: the `extractedFrom` read-version anchor is
            # server-derived (resolved from the :Source below).
            # Accepting it would let this non-SDK seam forge the
            # provenance record, and the same reason the SDK and
            # MCP boundaries reject it applies here.
            # #7704: `extractedFromAll` joins them — it is the
            # FOLD's record of the ref set, and
            # `_upsert_point_edges`/`_point_all_source_refs`
            # re-derive the `extractedFrom` edges from it on
            # replay, so a caller-supplied value puts the
            # payload's edges AHEAD of the :Source resolution
            # this seam performs (live wires the resolved refs,
            # the replay wires the caller's array too). This
            # seam is the one producer that never reaches
            # `_sanitize_props`, so the entry is the ONLY guard
            # on the key here — the sibling keys are registered
            # at every home for the same reason.
            "sourceVersion", "sourceVersions",
            "sourceVersionTransit", "extractedFromAll",
            # #10010: the FOLD's record FLAG — `_fold_source_evidence`
            # mints it on the snapshot it re-emits, and the replay's
            # recreate anchor reads it to tell a fold from a
            # creation. Server-minted like the keys above, so a
            # caller value is refused at this seam too.
            "evidence_fold",
        }
        # ⛔ `_forged_keys` IS DELIBERATELY LEFT AS A HASH TEST (#11996 review
        # round 15). It was converted to `_named_from_caller` and REVERTED: a
        # carrier (a `str` SUBCLASS whose `__hash__` differs) is refused by
        # `_refuse_non_str_keys` on `_emit`'s record channel, name-independently
        # and before every family guard, so this arm cannot be the only line of
        # defence and does not need to be. Converting it bought nothing and COST
        # the diagnosis: `_named_from_caller` reports every non-`str` key as one
        # of THIS set's, so `add_point(**{K("myCustomProp"): 1})` was told that
        # `myCustomProp` is a server-managed journal field, which is false.
        _forged = sorted(set(fields) & _forged_keys)
        if _forged:
            raise ValueError(
                f"{_forged} are server-managed journal fields (embedding "
                "identity / provenance) and cannot be set via "
                "add_point(fields=...)")
        # #5256: resolve the extractedFrom read-version from the :Source on the
        # LIVE path and put it in the payload, so the Point's OWN journaled
        # snapshot carries it and the replay never re-reads the Source (whose
        # in-place contentHash bump is unjournalled, #5024). `getattr` — not
        # `self.projection.g` — is load-bearing: this producer runs in
        # projection-less extraction lanes (`projection=None`) and against the
        # in-memory double (no `.g`), and neither has a Source to read. There
        # the field simply stays absent; the point itself still journals.
        if p.get("extractedFrom"):
            _g = getattr(self.projection, "g", None)
            if _g is not None:
                from .projection.edges import _source_version_transit, resolve_source_versions
                _sv = _source_version_transit(
                    resolve_source_versions(_g, p["extractedFrom"]))
                if _sv is not None:
                    p["sourceVersionTransit"] = _sv
        # P1 #49: mark events with projection_version=2 so the projection gate
        # (Task 1.6) knows to strip context from v2 events.
        self._emit("PointAdded", p, corrects=corrects, projection_version=2)
        # Auto-compute grounding when a resolution event seeds the a-vector (#6704).
        # P1 #49: check pointKind, not context (context is deprecated).
        # ponytail: duck-typed; add a proper protocol if more backends gain grounding.
        pk = p.get("pointKind") or fields.get("pointKind")
        if pk == "resolution-event" and self.projection is not None:  # noqa: SIM102
            if hasattr(self.projection, "compute_grounding"):
                self.projection.compute_grounding()
        return p["id"]

    def add_operator(self, op_type, inputs, provenance, *,
                     content=None, corrects=None) -> str:
        # #331: explicit validation — a bare assert vanishes under python -O,
        # and non-string inputs crashed the label join below.
        if not isinstance(op_type, str) or op_type not in ("NAND", "IMPL"):
            raise ValueError(f"unknown gate {op_type!r}")
        if inputs is None:
            raise TypeError("add_operator: inputs must be a list of point ids")
        if isinstance(inputs, str):
            # #331 (review r2): a bare string is a single id, never char-split
            # (parity with merge_points).
            inputs = [inputs]
        _inputs = []
        for x in inputs:
            if isinstance(x, dict):
                if not isinstance(x.get("id"), str):
                    raise TypeError(
                        f"add_operator: input dict missing string 'id': {x!r}")
                _inputs.append(x["id"])
            elif hasattr(x, "id"):
                if not isinstance(x.id, str):
                    raise TypeError(
                        f"add_operator: input {x!r} has non-string .id "
                        f"{x.id!r}")
                _inputs.append(x.id)
            elif isinstance(x, str):
                _inputs.append(x)
            else:
                raise TypeError(
                    "add_operator: input must be a point id string, a dict "
                    f"with 'id', or an object with .id — got {type(x).__name__}: {x!r}")
        label = content or f"{op_type}({', '.join(_inputs)})"
        p = self._point(label, provenance,
                        operator={"op_type": op_type, "inputs": _inputs})
        self._emit("OperatorAdded", p, corrects=corrects, projection_version=2)

        # Gate 4: incrementally update SVBP on the new operator
        # ponytail: only FalkorProjection supports SVBP; InMemoryProjection doesn't.
        if self.projection is not None and hasattr(self.projection, 'get_svbp'):
            # Lazy-init SVBP if not yet created (handles operators added
            # before any get_confidence() call — Bug 5 fix).
            if self._svbp is None:
                svbp = self.projection.get_svbp(
                    n_particles=25, n_svgd_steps=15, svgd_lr=0.01,
                    damping=0.5, max_iter=40, tol=5e-3, seed=42,
                )
                if svbp is not None:
                    self._svbp = svbp
            if self._svbp is not None:
                # #1162: scope the warm-start to THIS operator — the old
                # global extract_svbp_factors() ran 2 batch graph queries
                # (O(graph)) on every operator write when the quadrature
                # extra (jax) is installed. The new operator's factor is
                # fully known here: same weight rule (3.0 NAND / 1.0 IMPL)
                # and >=2-input degenerate exclusion as extract_svbp_factors
                # (the EP local path's weights differ — compute_operator_weight
                # applies NAND_BASE_WEIGHT=8.0), so per-write cost drops to
                # O(1) with zero graph queries.
                if len(_inputs) >= 2:
                    weight = 3.0 if op_type == "NAND" else 1.0
                    factors = [(p["id"], op_type, list(_inputs), weight)]
                else:
                    factors = []
                if factors:
                    old_max = self._svbp.max_iter
                    self._svbp.max_iter = 5
                    try:
                        self._svbp.run(factors, warm_start=True)
                    finally:
                        self._svbp.max_iter = old_max  # Bug 4 fix

        return p["id"]

    # ── Gate 4: Confidence API ────────────────────────────────────

    def get_confidence(self, point_id: str) -> dict | None:
        """Compute confidence for a point via SVBP.

        Lazy-inits SVBP on first call. Returns {mean, variance, alpha, beta}
        or None if the projection doesn't support SVBP.
        """
        if self._svbp is None and self.projection is not None and hasattr(self.projection, 'get_svbp'):
            svbp = self.projection.get_svbp(
                n_particles=25, n_svgd_steps=15, svgd_lr=0.01,
                damping=0.5, max_iter=40, tol=5e-3, seed=42,
            )
            if svbp is not None:
                self._svbp = svbp
        if self._svbp is None:
            return None
        return self._svbp.compute_confidence(point_id)

    def revise_point(self, id, *, new_content=None, corrects) -> str:
        ev = self._emit("PointRevised", id=id, new_content=new_content,
                        corrects=corrects)
        return ev["event_id"]

    def retract_point(self, id, *, corrects) -> str:
        ev = self._emit("PointRetracted", id=id, corrects=corrects)
        return ev["event_id"]

    def merge_points(self, keep_id, merge_ids, *, corrects=None) -> str:
        # #331: None/empty merge input must not crash (list(None) raises
        # TypeError); a bare string is a single id, never char-split.
        if merge_ids is None:
            merge_ids = []
        elif isinstance(merge_ids, str):
            merge_ids = [merge_ids]
        ev = self._emit("PointsMerged", keep_id=keep_id, merge_ids=list(merge_ids),
                        corrects=corrects)
        return ev["event_id"]

    def _registration_id(self, label: str, name: str,
                         explicit_id: str | None = None) -> str:
        """The id a REGISTRATION emitter writes: the live holder's, or a MINTED one.

        #3590 (D1/R1/R2, and the review's finding 1): ``ObjectRegistered`` /
        ``SubjectAdded`` ARE the explicit registration doors — D1's own text
        (``identity-decision.md``) says the entity set grows only from them —
        but a registration still has to be IDEMPOTENT, the way every other
        create door is (``#452``: a re-mention of a live name resolves to the
        live holder).

        ⛔ What changed and why this is not optional: the fold's MERGE key is now
        ``{id}`` (``projection.entities._upsert_subject`` / ``_upsert_object``),
        so the *by-construction* duplicate suppression the old name-keyed MERGE
        provided is GONE — Cypher's ``MERGE`` creates whenever the exact pattern
        is absent (primary source: `neo4j.com/docs/cypher-manual/current/
        clauses/merge/`, "if the exact pattern is not found, MERGE acts like
        CREATE"), and this repo declares range indexes, not a uniqueness
        constraint on ``Object.id``/``Subject.id``. So the anti-duplicate
        property has to be carried by the CALLER, and a door that minted a
        fresh ULID per call would leave a routine re-run of the v1 extractor
        with TWO live carriers of one name — after which every by-name read
        refuses (``AmbiguousEntityName``), ``create_entity`` raises, and
        ``update_entity(name=…)`` raises ``NameAlreadyHeld``, for a name the
        producer registered legitimately.

        ``explicit_id`` wins outright (``mining``/``docs_miner`` pass their own
        canonical id): an explicit-id channel supplies the identity, which is
        exactly what an opaque-id model allows and what ``add_object(id=…)``
        promises. It is never a name derivation.

        ``AmbiguousEntityName`` is propagated, NOT swallowed into a third
        carrier: same-name coexistence is legal (D2) but a name-keyed door may
        not pick a carrier for the caller — route-then-refuse (R8), the same
        disposition ``create_entity`` takes.

        ⛔ The caller MUST hold :func:`~tortoise.entity_identity.entity_create_lock`
        across this resolve AND the ``_emit`` that makes the holder visible —
        otherwise the empty-holder check is a TOCTOU window rather than a guard
        (the same finding round 5 fixed for ``create_entity``).
        """
        if explicit_id:
            return str(explicit_id)
        # `getattr` — not `self.projection.g` — because projection-less lanes
        # (the `__main__` demo, `m0.py`) and in-memory doubles have no graph to
        # resolve against; they journal only, so there is no second carrier to
        # avoid and the plain mint is right.
        g = getattr(self.projection, "g", None)
        if g is None:
            return ulid()
        from .entity_identity import resolve_or_mint
        return resolve_or_mint(g, label, name)[0]

    def add_subject(self, name: str, subject_kind: str | None = None) -> str:
        """Emit SubjectAdded event. Returns the subject's id.

        ``subject_kind`` defaults to ``None`` (**absent**, not ``"other"**) since
        #9572: the fabricated default reached the fold's ON MATCH
        ``coalesce($sk, s.subjectKind)`` as a real value, so a bare re-mention
        reset a user-authored kind. The ``'other'`` default is the fold's ON
        CREATE arm.

        The id is resolved-or-minted (``_registration_id``), never a fresh ULID
        per call: the fold MERGEs Subject by ``id``, so re-minting would leave
        the name with two LIVE holders and poison it for every later by-name
        read (#3590 review, finding 1).
        """
        from .entity_identity import entity_create_lock
        with entity_create_lock("Subject", name):
            sid = self._registration_id("Subject", name)
            # #12047 review F6b: this door wrote no matching key at all, so a
            # Subject registered through EventAPI could never be resolved by the
            # deterministic rung — the resolver would mint a twin beside it.
            from .entity_resolver import normalize_entity_name
            ev: dict = {"id": sid, "name": name, "createdAt": now_iso(),
                        "canonical_name": normalize_entity_name(name)}
            subject_kind = named_kind(subject_kind)
            if subject_kind is not None:
                ev["subject_kind"] = subject_kind
            self._emit("SubjectAdded", **ev)
        return sid

    def add_object(self, name: str, object_kind: str | None = None, *,
                   id: str | None = None, **props) -> str:
        """Emit ObjectRegistered event. Returns the object's id.

        ``id``: explicit canonical id override (epic #264 plan §4.1 —
        obj_sha256 scheme), which wins outright. WITHOUT it the id is
        resolved-or-minted through ``entity_identity.resolve_or_mint`` — the
        live holder's id for an existing single live carrier, a fresh ULID
        otherwise (``_registration_id``).

        ⛔ The projection MERGEs Object by ``id`` since #3590, so this door's
        idempotency is carried HERE, by the caller, and no longer by the MERGE
        key: a re-run with the same name resolves to the same node instead of
        minting a duplicate (DE2E-1/DE2E-8), and a second live carrier of one
        name is what the resolve REFUSES rather than creates. The previous
        text claimed the MERGE did it — false at this head, which is exactly the
        poisoning this door now prevents (#3590 review, finding 1).

        Extra props (e.g. canonical_name, title) are persisted by the
        projection's extra-props handler.

        ``object_kind`` defaults to ``None`` (**absent**) since #9572 — the
        Object twin of ``add_subject``'s fabricated default.

        #11948: this is the THIRD door that splats a caller's dict onto the
        envelope (`**props`), and it enforced none of the funnel's rules —
        neither a record-shaped `point=` nor an envelope collision. Both are
        refused here, BEFORE the entity lock and the id resolution: a refusal
        that ran after `_registration_id` would have taken the lock for a write
        that cannot happen.
        """
        _refuse_caller_named_funnel_keys(props, "`add_object`")
        from .entity_identity import entity_create_lock
        with entity_create_lock("Object", name):
            oid = self._registration_id("Object", name, explicit_id=id)
            from .entity_resolver import normalize_entity_name
            props.setdefault("canonical_name", normalize_entity_name(name))
            ev: dict = {"id": oid, "name": name, "createdAt": now_iso(), **props}
            object_kind = named_kind(object_kind)
            if object_kind is not None:
                ev["object_kind"] = object_kind
            self._emit("ObjectRegistered", **ev)
        return oid

    def add_document(self, doc_id: str, title: str, *,
                     document_kind: str = "",
                     document_knowledge_domain: str = "",
                     about_entities: list[str] | None = None,
                     authored_by: str = "",
                     owned_by: str = "",
                     managed_by: str = "",
                     governing_agreement: str = "",
                     format: str = "markdown",
                     version: str = "",
                     content_hash: str | None = None,
                     createdAt: str | None = None,
                     updatedAt: str | None = None,
                     corrects: str | None = None,
                     topics: list[str] | None = None,
                     summary: str | None = None,
                     session_id: str | None = None,
                     event_id: str | None = None,
                     source_path: str | None = None,
                     needs_extraction: bool = False,
                     # epic #900 T3 (§4.1 route pin):
                     source_url: str | None = None,
                     domain: str | None = None,
                     suppress_embedding: bool = False) -> str:
        """Emit DocumentCreated event. Returns the document id (same as input).

        JSONL fields are snake_case per plan §4.3 convention.
        Projection normalizes to camelCase for the graph.
        #125: topics/summary/session_id/event_id capture metadata.
        #167: source_path → d.sourcePath for file resolution.
        #133: needs_extraction → d.needs_extraction for --upgrade-all discovery.
        D10 (ONTOLOGY v3.15 §4.4): ``doc_status`` is RETIRED — liveness is a
        read of the extracted entities, not a stored field. It is no longer a
        parameter and is never emitted.
        Epic #900 T3: ``source_url`` overrides the #205 auto-wire target (the
        indexer passes the real ``corpus://`` Source url so no phantom Source
        is merged — the override rides the JOURNALED event, so replay honors
        it); ``domain`` is persisted as ``d.domain`` via ``_persist_extra_props``
        (intentionally NOT in ``_DOC_PASSTHROUGH_HANDLED`` — the persistence IS the
        intent); ``suppress_embedding`` skips the unconditional embedding call
        (new-path docs; the legacy branch computes as today — SC4).
        #5422: ``content_hash`` is the document's **version anchor** on the
        extraction path — the SHA-256 of the ingested text (callers pass
        ``tortoise.ids.content_hash(text)``). It rides the JOURNALED event so
        the projection fold writes it replayably, and it is what gives a
        document-derived Point a version to anchor on (ONTOLOGY §4.6: the
        hash identifies a version; identity is ``url``). The JSONL field is
        snake_case (``content_hash``) like every sibling on this event — the
        projection normalizes it to ``contentHash`` on the node. Absent/None
        is the back-compat shape — the fold PRESERVES the stored hash rather
        than clearing it, so a metadata-only re-emit cannot wipe an anchor.
        """
        # #5140 P1 (review cycle 3): the doc id IS the caller's `:Source` MERGE
        # key, so this producer could rewrite an existing document's genre to
        # `transcript` and drop it out of the metered `documents` set (MEASURED,
        # 3 seeded documents: meter 3 -> 2 -> 1, one call each). The fold's CASE
        # clause drops that value to keep REPLAY total (a rebuild must not abort
        # on a pre-guard record), but this is a PRODUCER, not replay: a silent
        # no-op would return the doc id as if the genre had been written. Runs
        # BEFORE `_emit` (the commit point), so a refusal leaves no record.
        # `getattr` — not `self.projection.g` — because projection-less lanes
        # (the `__main__` demo, `m0.py`) and in-memory doubles have no graph to
        # demote; there the record still journals (`_refuse_unapproved_kinds`
        # states the same bound).
        _g = getattr(self.projection, "g", None)
        if _g is not None:
            refuse_document_writer_demotion(
                _g, doc_id, document_kind, surface="add_document")
        self._emit("DocumentCreated",
                   corrects=corrects,
                   id=doc_id,
                   title=title,
                   document_kind=document_kind,
                   document_knowledge_domain=document_knowledge_domain,
                   about_entities=about_entities or [],
                   authored_by=authored_by,
                   owned_by=owned_by,
                   managed_by=managed_by,
                   governing_agreement=governing_agreement,
                   format=format,
                   version=version,
                   content_hash=content_hash,
                   createdAt=createdAt or now_iso(),
                   updatedAt=updatedAt or now_iso(),
                   topics=topics or [],
                   summary=summary,
                   session_id=session_id,
                   event_id=event_id,
                   source_path=source_path,
                   needs_extraction=needs_extraction,
                   source_url=source_url,
                   domain=domain,
                   suppress_embedding=suppress_embedding)
        return doc_id

    def add_event(self, event_id: str, event_kind: str, *,
                  subject: str = "",
                  object_name: str = "",
                  object_type: str = "",
                  uses: list | None = None,
                  participants: list[str] | None = None,
                  started_at: str = "",
                  ended_at: str = "",
                  classification_level: str = "internal",
                  format: str = "jsonl",
                  **extra) -> str:
        """Emit an EventRecorded event (#125). Returns the event id.

        The event id is passed as ``id`` (not ``eventId``) to match the
        projection's ``_upsert_event`` lookup (``inner.get("id") or
        inner.get("eventId")``).

        ``**extra`` is a passthrough straight onto the ENVELOPE, so a caller can
        put a top-level ``embedding`` on the record here. That is allowed — a
        vector is not an origin attestation — and ``_emit`` stamps every vector
        that reaches it. A caller-set MARKER is a different matter: it is refused
        by `_refuse_caller_named_funnel_keys` — ONE function, not two placements
        (`_refuse_caller_named_markers` has a single call site inside it), so the
        message names the door the caller actually used and a newly added family
        cannot be wired at one placement and silently missing at the other. It
        must NOT accept a
        record-shaped payload (`point=`) either — a caller-built
        record reaches the replay's fold chain untested by that rule — so it is
        refused here for a message that names THIS door, and again at `_emit`
        (`_NESTED_RECORD_KEYS`), which is where the refusal is total. Nor may
        it name an ENVELOPE field in ``**extra`` (`type=`, `ts=`,
        `initiated_by=`, `corrects=`, `agent_id=`): those are the funnel's, and
        the collision re-types / re-attributes the record (#11948). ⚠️
        ``event_id=`` is NOT in that list HERE: it is this door's own first
        parameter, re-emitted as the payload's ``id``, so it never travels
        ``**extra`` and cannot collide — the passthrough set names it for the
        doors whose ``id=`` means something ELSE (``add_object``'s
        ``add_object(id=…, event_id=…)``).
        """
        _refuse_caller_named_funnel_keys(extra, "`add_event`")
        self._emit("EventRecorded",
                   id=event_id,
                   eventKind=event_kind,
                   subject=subject,
                   object=object_name,
                   objectType=object_type,
                   uses=uses or [],
                   participants=participants or [],
                   startedAt=started_at,
                   endedAt=ended_at,
                   classificationLevel=classification_level,
                   format=format,
                   **extra)
        return event_id

    # ── Cleanup ───────────────────────────────────────────────────

    def close(self):
        """Release SVBP particles and close projection."""
        if self._svbp is not None:
            self._svbp._particles.clear()
            self._svbp._summaries.clear()
            self._svbp = None
        if self.projection is not None and hasattr(self.projection, 'close'):
            self.projection.close()
