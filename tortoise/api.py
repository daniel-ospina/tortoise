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

# #7951 §17.1: the live-write scope the journal-first doors open around the fold.
from .projection import _live_write_scope, fold

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
#: POSITIONALLY (rendered onto the envelope as `point`) after stamping it
#: themselves (with `creating=True`, so the origin attestation rides a creating
#: record's own basis). A CALLER that builds one bypasses both gates, and the
#: funnel must not "just stamp it" to close that: `creating=False`
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
#: `{"event_id", "ts", "type", "initiated_by", "agent_id", **payload}`, so a
#: caller `type=` re-TYPES the record (`add_object(..., type="PointAdded")`
#: journalled a `PointAdded` no record door built, and the replay folds by
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
    named = sorted(set(keys) & _NESTED_RECORD_KEYS)
    if named:
        raise ValueError(
            f"{named} is a RECORD-SHAPED journal payload that {door} does not "
            "build, and a caller-built one is folded by the replay without the "
            "vector rule the record doors apply (width declared, caller marker "
            "refused). Use the door that owns that record (`add_point`, "
            "`add_operator`) instead of naming it here.")


def _refuse_caller_named_envelope_keys(keys, door: str) -> None:
    """Refuse a caller key that collides with the journal ENVELOPE.

    #11948: the envelope is the FUNNEL's to build. A payload key that shares a
    name with it does not add a field — `_emit` spreads the payload AFTER the
    envelope, so the caller's value WINS, and the record is re-typed
    (`type`), re-identified (`event_id`) or re-attributed (`ts`,
    `initiated_by`, `agent_id`, `actor_user_id`, `corrects`). Nothing else in
    the write path re-checks the record's type, so a caller-typed record is
    folded by the replay as whatever it claims to be.
    """
    named = sorted(set(keys) & _ENVELOPE_KEYS_AT_PASSTHROUGH)
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
    two placements: at the door, and at the funnel every door funnels through.
    """
    named = sorted(set(keys) & _SERVER_MANAGED_EMBEDDING_KEYS)
    if named:
        raise ValueError(
            f"{named} are server-managed journal fields (embedding width / "
            f"storage form / origin) and cannot be set via {door}'s payload")


def _refuse_caller_named_funnel_keys(keys, door: str) -> None:
    """Refuse EVERY name the journal funnel owns, at one passthrough door.

    The three families and their reasons live above; this is the ONE call a
    door makes, so a fourth family (or a fourth door) cannot be half-wired —
    the failure mode #11835 review round 4 and #11948 both measured: a rule
    stated at two doors and silently absent at the third.
    """
    _refuse_caller_named_record_keys(keys, door)
    _refuse_caller_named_envelope_keys(keys, door)
    _refuse_caller_named_markers(keys, door)


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
        # `95b98e6cf1`). So the record is checked against TWO of the three
        # families — `point` and the envelope names — and ALL THREE refusals run
        # before the append below (the commit point), which therefore leaves
        # nothing to replay.
        #
        # ⛔ THE MARKER FAMILY IS DELIBERATELY NOT CHECKED HERE, and that is not
        # a gap to tidy away: `_point`'s own `stamp_journal_embedding` writes
        # `embedding_width` into this very record, so a funnel-wide marker
        # refusal would refuse every `add_point`. The caller-marker refusal
        # lives at that door (`_forged_keys`) instead, and `add_operator` takes
        # no `**kwargs` — so no public door can reach this channel with one.
        _refuse_caller_named_record_keys(payload, f"the `{type_}` envelope")
        if record:
            if len(record) > 1:
                raise TypeError(
                    f"_emit takes at most ONE record payload, got {len(record)}")
            _refuse_caller_named_record_keys(
                record[0], f"the `{type_}` record door")
            # ⛔ NOT a payload field on this channel either: see
            # `_ENVELOPE_KEYS`'s positional note. `_norm` splices the record
            # over the envelope on replay, so a caller keyed `type=` / `ts=` /
            # `event_id=` here re-types or re-attributes the replayed record.
            _refuse_caller_named_envelope_keys(
                record[0], f"the `{type_}` record door")
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
        _forged_markers = sorted(set(payload) & _SERVER_MANAGED_EMBEDDING_KEYS)
        if _forged_markers:
            raise ValueError(
                f"{_forged_markers} are server-managed journal fields (embedding "
                f"width / storage form / origin) and cannot be set via "
                f"{type_}'s payload")
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
        run here: the record-shaped, envelope, marker and money families, and
        the invisible-character half of the kind gate.

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
            ev: dict = {"id": sid, "name": name, "createdAt": now_iso()}
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
        here AS WELL AS at the funnel, the same set in two placements
        (`_refuse_caller_named_markers`), so the message names the door the
        caller actually used and a newly added family cannot be wired at one
        placement and silently missing at the other. It must NOT accept a
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
