"""#3998 — the ABSENT-RAW state: a first-class value on the source record.

D30 (#3919) makes provenance **raw-optional**: the graph *indexes* raw
conversations it may not be able to reach.

    *"we have two storages: raw data and then the graph. the graph indexes the
    files of raw data and extracts from them into our ontology. Raw data can be
    hosted by us (hosted service), in the user machine, or in their own hosting
    preference."*

So the source record carries **three** values, not two:

| value | property | what it answers |
|---|---|---|
| identity | ``url`` / ``canonicalUrl`` | *which* source is this? |
| version | ``contentHash`` | *which version* of it did we read? |
| **availability** | ``rawState`` (this module) | *can we still reach the raw?* |

``STORAGE-ARCHITECTURE.md`` §9.4 ③ names this placement: the absent-raw state
is **a third value on that record, not a fourth kind of source**.

⛔ A DELETION IS AN ACT; OFFLINE / ACCESS-REVOKED ARE CONDITIONS
    The owner ruled on #3998 (2026-10-14, **Option A** — his 2026-09-18 wording
    reinstated): *deleting a memory is an act, recorded durably as a marker
    bounded by an expiry policy; ``offline`` / ``access_revoked`` are
    conditions, observed live, never stored.* The record therefore holds exactly
    two states (:data:`RAW_DURABLE_STATES`): ``present`` and ``deleted``.
    ``offline`` and ``access_revoked`` live in :data:`RAW_OBSERVED_STATES` — they
    are **derived at read time** through :func:`resolve_raw_availability`, and a
    value of either found ON A STORED ROW is a legacy/foreign row and reads
    fail-closed as :data:`RAW_UNRECOGNISED`. This is what makes the storage-layer
    canon applicable rather than decorative: Cassandra writes a tombstone because
    a replica may be *unreachable* — and it does **not** record the replica's
    unreachability.

⛔ WHY A VALUE AND NOT AN EXCEPTION
    Absence is **normal, not an error**. A user whose own bucket goes offline,
    or who deletes the raw, or whose credential is revoked, still has a valid
    memory with valid provenance — the memory and its provenance must stay
    readable and **sayable**. So nothing in the read path here raises on
    absence, and the resolver returns a value for *every* input.

⛔ THE THREE CAUSES ARE NOT INTERCHANGEABLE
    ``deleted`` is permanent and intended · ``offline`` is temporary ·
    ``access revoked`` is a permissions fact. Collapsing them into "missing"
    makes the product say something false, so each carries its own permanence,
    its own label and its own user-facing message.

⛔ R1 — NO RETENTION WINDOW ON USER CONTENT; THE MARKER IS BOUNDED METADATA
    This module introduces **no retention window of any length on the raw or on
    any user content.** What it bounds is the **deletion marker** — metadata
    about the link back to the raw, never a copy of anything and never a period
    on user content. The ruling requires that bound (*"record durably, do NOT
    record forever"*), and the convergent practice fixes its shape and its
    number: Cassandra's ``gc_grace_seconds`` tombstone grace (default **10
    days**), Elasticsearch's soft-delete retention lease (~12 h), S3's
    lifecycle-expired delete markers. The window has the same job here as there:
    it must outlast the slowest path that could still hold a pre-deletion copy —
    here, a ``rebuild_all`` from an older journal — or dropping the marker would
    let that path resurrect a deleted memory. It is
    :data:`RAW_DELETION_MARKER_TTL` = **10 days**.

    ⛔ THE WINDOW AUTHORISES A FUTURE DROP — IT DOES NOT RESURRECT.
    :func:`is_deletion_marker_expired` reports when the marker MAY be dropped.
    It does **not** change what a read reports: :func:`raw_availability` keeps
    returning ``deleted`` after expiry, because dropping the marker is a
    separate, **journalled** act. Removing ``rawState`` on the node with no
    record would make a rebuild replay the marker straight back (``live !=
    replay`` — the divergence lane L6 exists to remove); treating "expired" as
    "the raw is back" is exactly the resurrection the ruling forbids.

⛔ FAIL-CLOSED ON READ, STRICT AT THE WRITE BOUNDARY
    A *caller* that passes a value outside the vocabulary is a caller bug, and
    is refused loudly (:func:`validate_raw_state`) — AT THE TENANT BOUNDARY.
    That is the only route a caller has to the state (`rawState`/`rawStateAt`
    are refused as props), and it is where strictness actually lives: the
    projection's replay fold writes the journalled value verbatim and does NOT
    re-validate. The journalled value is trusted because it was validated
    before it was journalled, so this is a claim about the write BOUNDARY, not
    about every writer — stated this way because the earlier wording ("STRICT
    ON WRITE") read as covering the fold too, which it does not.
    A *reader* that meets a bad value anyway
    (an old row, a hand-edited node, a future value) must never **fail open**:
    an unrecognised recorded state resolves to :data:`RAW_UNRECOGNISED` —
    explicitly *not* "present" — because "we cannot interpret what was
    recorded" is not the same claim as "the raw is reachable". The safe
    default (no property written at all) is :data:`RAW_PRESENT`: no absence has
    been recorded, which is the shape of every source that has never been
    observed to be absent. An empty string is a *recorded value*, not the
    absence of one, so it resolves to :data:`RAW_UNRECOGNISED` as well.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = [
    "RAW_ABSENT_STATES",
    "RAW_ACCESS_REVOKED",
    "RAW_DELETED",
    "RAW_DELETION_MARKER_TTL",
    "RAW_DURABLE_STATES",
    "RAW_OBSERVED_STATES",
    "RAW_OFFLINE",
    "RAW_PRESENT",
    "RAW_STATE_AT_PROP",
    "RAW_STATE_PROP",
    "RAW_UNRECOGNISED",
    "WRITABLE_RAW_STATES",
    "RawState",
    "deletion_marker_expires_at",
    "is_deletion_marker_expired",
    "is_valid_raw_state",
    "raw_availability",
    "raw_entry",
    "raw_state_for",
    "resolve_raw_availability",
    "validate_raw_state",
]

# ── the vocabulary ────────────────────────────────────────────────────────
#: No absence has been recorded — the default for every source.
RAW_PRESENT = "present"
#: The raw was deliberately and permanently removed by its owner. A durable
#: ACT, recorded as a marker and bounded by :data:`RAW_DELETION_MARKER_TTL`.
RAW_DELETED = "deleted"
#: The raw's host is unreachable *now* — the raw is expected to be unchanged.
#: A CONDITION: observed live, never recorded (see the module docstring).
RAW_OFFLINE = "offline"
#: The credential/ACL that reached the raw no longer does. The bytes still
#: exist; our *permission* to read them does not. A permissions fact, not a
#: statement about the file. A CONDITION: observed live, never recorded.
RAW_ACCESS_REVOKED = "access_revoked"
#: A state IS recorded but this module does not recognise it. A READ-side
#: only value — never writable — so an unrecognised row can never read as
#: "present" (fail closed). See the module docstring.
RAW_UNRECOGNISED = "unrecognised"

#: The states a WRITER may RECORD on the source record — the durable acts.
#: ``deleted`` is the marker the ruling requires; ``present`` is the documented
#: way back (a raw that returned). NOTHING A CONDITION REPORTS is here.
RAW_DURABLE_STATES: tuple[str, ...] = (RAW_PRESENT, RAW_DELETED)
#: The states that are CONDITIONS — observed live, never stored. They are
#: returned by :func:`raw_state_for` (and therefore by
#: :func:`resolve_raw_availability`), and recorded by nobody.
RAW_OBSERVED_STATES: tuple[str, ...] = (RAW_OFFLINE, RAW_ACCESS_REVOKED)

#: The three causes the contract names — distinguishable on purpose. The
#: RECORDED absent cause (``deleted``) plus the two OBSERVED ones.
RAW_ABSENT_STATES: tuple[str, ...] = (RAW_DELETED, *RAW_OBSERVED_STATES)
#: Every state a WRITER may set (``RAW_UNRECOGNISED`` is read-side only).
#: The name is kept because the write boundary and its refusal message use it;
#: under the #3998 ruling it is exactly :data:`RAW_DURABLE_STATES`.
WRITABLE_RAW_STATES: tuple[str, ...] = RAW_DURABLE_STATES

#: Node property carrying the state on a ``:Source``.
RAW_STATE_PROP = "rawState"
#: Node property carrying when the state was recorded (ISO-8601).
RAW_STATE_AT_PROP = "rawStateAt"

#: How long a ``deleted`` marker is **guaranteed** for, after which it is
#: eligible to be dropped by a (future, journalled) collection pass.
#:
#: ``RAW_DELETION_MARKER_TTL`` is 10 days — Cassandra's documented
#: ``gc_grace_seconds`` default — the same
#: shape and the same number as the storage-layer canon the ruling rests on.
#: The window must outlast the slowest path that could still hold a
#: pre-deletion copy (here: a `rebuild_all` replaying an older journal), or
#: dropping the marker would let that path resurrect a deleted memory. It is
#: metadata about the LINK back to the raw — never a copy of the raw, and never
#: a retention period on user content (R1).
RAW_DELETION_MARKER_TTL: timedelta = timedelta(days=10)


@dataclass(frozen=True)
class RawState:
    """A resolved raw-availability state — a value, never an exception.

    ``permanent`` is the distinction that makes the three causes usable: it is
    what lets a caller say *"this is gone for good"* rather than *"try again
    later"*, and it is the only reason ``deleted`` and ``offline`` are not one
    value.
    """

    state: str
    permanent: bool
    label: str
    message: str

    @property
    def absent(self) -> bool:
        """True when the raw is NOT known to be reachable.

        ``RAW_UNRECOGNISED`` is deliberately ``absent=True``: an
        uninterpretable recorded state must never resolve as reachable.
        """
        return self.state != RAW_PRESENT

    @property
    def retryable(self) -> bool:
        """True when reaching the raw might succeed later (offline only)."""
        return self.state == RAW_OFFLINE


#: state → the claim it makes. The message is the user-facing sentence: it is
#: a *claim*, which is why ``deleted`` and ``access revoked`` do not share one
#: ("gone" vs "we may not read it" are different statements about the world).
_STATES: dict[str, RawState] = {
    RAW_PRESENT: RawState(
        state=RAW_PRESENT, permanent=False,
        label="present",
        message="The raw is expected to be reachable; no absence has been recorded.",
    ),
    RAW_DELETED: RawState(
        state=RAW_DELETED, permanent=True,
        label="raw deleted",
        message=(
            "The raw was deleted by its owner. This is permanent — the memory "
            "and its provenance remain valid, but the original cannot be "
            "fetched again."
        ),
    ),
    RAW_OFFLINE: RawState(
        state=RAW_OFFLINE, permanent=False,
        label="raw offline",
        message=(
            "The raw's host is unreachable right now. The raw itself is "
            "expected to be unchanged — this is temporary and may resolve on "
            "its own."
        ),
    ),
    RAW_ACCESS_REVOKED: RawState(
        state=RAW_ACCESS_REVOKED, permanent=False,
        label="raw access revoked",
        message=(
            "Access to the raw was revoked. The raw still exists; the "
            "credential that reached it no longer may read it. This is a "
            "permissions fact, not a statement about the file."
        ),
    ),
    RAW_UNRECOGNISED: RawState(
        state=RAW_UNRECOGNISED, permanent=False,
        label="raw state unrecognised",
        message=(
            "A raw state is recorded that this build does not recognise. It is "
            "NOT treated as reachable — the raw's availability is unknown."
        ),
    ),
}

#: The lookup a READ of the RECORD may use. Deliberately NARROWER than
#: :data:`_STATES`: only the durable acts are recordable, so a stored
#: ``offline`` / ``access_revoked`` (written by an earlier head) is not
#: interpreted as a condition — it reads fail-closed as
#: :data:`RAW_UNRECOGNISED`. The observed causes are reached through
#: :func:`raw_state_for`, which is the live-observation route and never a
#: record lookup.
_RECORDED_STATES: dict[str, RawState] = {
    RAW_PRESENT: _STATES[RAW_PRESENT],
    RAW_DELETED: _STATES[RAW_DELETED],
}


def is_valid_raw_state(value: object) -> bool:
    """True when ``value`` is a state a WRITER may set."""
    return isinstance(value, str) and value in WRITABLE_RAW_STATES


def validate_raw_state(value: object) -> str:
    """Writer-side gate — strict, and loud on a bad value.

    A caller bug at write time is worth an exception: silently dropping a
    recorded absence would be the exact failure this module exists to prevent.

    ``offline`` / ``access_revoked`` are REFUSED, and the message says why:
    they are conditions, observed live, never recorded (#3998 ruling, Option A).
    """
    if not is_valid_raw_state(value):
        if isinstance(value, str) and value in RAW_OBSERVED_STATES:
            raise ValueError(
                f"Invalid raw_state {value!r} — {value!r} is a CONDITION, "
                f"observed live and never stored (#3998). A writer may record "
                f"only {', '.join(WRITABLE_RAW_STATES)}."
            )
        raise ValueError(
            f"Invalid raw_state {value!r} — must be one of "
            f"{', '.join(WRITABLE_RAW_STATES)}"
        )
    return value  # type: ignore[return-value]


def raw_state_for(state: object) -> RawState:
    """The :class:`RawState` for a LIVE observation.

    This is the resolver for the OBSERVED conditions (``offline`` /
    ``access_revoked``) — and for ``present`` / ``deleted`` supplied by a caller
    that has just determined them. It does **not** read a record;
    :func:`raw_availability` does that.

    **Never raises, and never fails open.** An unrecognised observation resolves
    to :data:`RAW_UNRECOGNISED` — "we cannot interpret what was observed" is not
    the same claim as "the raw is reachable".
    """
    if isinstance(state, str):
        return _STATES.get(state, _STATES[RAW_UNRECOGNISED])
    return _STATES[RAW_UNRECOGNISED]


def raw_availability(props: object) -> RawState:
    """Resolve the raw-availability state from **the RECORD** a read path holds.

    ⛔ This reads the durable record ONLY. ``offline`` / ``access_revoked`` are
    never recorded, so a stored one is not honoured: it resolves to
    :data:`RAW_UNRECOGNISED` (fail closed). The live route is
    :func:`resolve_raw_availability` / :func:`raw_state_for`.

    **Never raises, and never fails open.** Accepts, in order:

    * a mapping of ``:Source`` node properties (reads :data:`RAW_STATE_PROP`);
    * a bare state string;
    * ``None`` ⇒ :data:`RAW_PRESENT` — NOTHING is recorded, which is the shape
      of every source that has never been observed to be absent. This conflates
      THREE cases, deliberately: a source that has a raw, a source that was
      never fetched, and a source whose state was never recorded all resolve
      ``available: True``. The record holds the ACT (and an act is only ever a
      deletion), so it cannot
      distinguish "the raw is there and we looked" from "we never looked" — and a
      url-only stub with no raw reads as available. Stated here because the
      consequence is not obvious from the constant's name; the conflation is
      NOT resolved by this module (#3998: absence is only ever RECORDED as an
      act, never inferred, and never scheduled).
    * **anything else — a recorded ``""``, a value outside the recordable
      vocabulary (including a legacy stored ``offline`` / ``access_revoked``),
      a non-string, or a mapping whose lookup raises — ⇒
      :data:`RAW_UNRECOGNISED`**, which is ``absent``. ``""`` is deliberate:
      it is a *recorded value*, so it is NOT "nothing recorded", and we cannot
      tell what it says, so we do not claim the raw is reachable.
    """
    if props is None:
        return _STATES[RAW_PRESENT]
    if isinstance(props, str):
        return _RECORDED_STATES.get(props, _STATES[RAW_UNRECOGNISED])
    if isinstance(props, dict):
        try:
            value = props.get(RAW_STATE_PROP)
        except Exception:  # noqa: BLE001, RUF100 — a hostile mapping must not fail OPEN
            return _STATES[RAW_UNRECOGNISED]
        if value is None:
            return _STATES[RAW_PRESENT]
        if isinstance(value, str):
            return _RECORDED_STATES.get(value, _STATES[RAW_UNRECOGNISED])
        return _STATES[RAW_UNRECOGNISED]
    return _STATES[RAW_UNRECOGNISED]


def resolve_raw_availability(
    props: object,
    *,
    observed: object = None,
) -> RawState:
    """The READ resolver: the RECORDED act first, then the LIVE condition.

    Precedence is deliberate, and it is the ruling expressed in code:

    1. **A recorded state that is not ``present`` WINS.** A recorded deletion is
       an ACT — a live observation must never override it, and an unrecognised
       record also wins (fail closed). This is what keeps a replay (and a probe)
       from un-deleting a memory.
    2. **With no observation** (``observed is None``) the record is returned
       as-is. A reader that cannot reach the raw says only what it recorded: it
       does not invent a condition, and it does not go silent — the entry is
       always produced (:func:`raw_entry`), never an empty result standing in
       for "not there".
    3. **Otherwise the LIVE observation is returned** (:func:`raw_state_for`),
       so ``offline`` / ``access_revoked`` are DERIVED at read time and never
       read from the record. A caller that has just determined the raw is
       unreachable gets the explicit, labelled, user-facing outcome — never a
       silently-empty read.
    """
    recorded = raw_availability(props)
    if recorded.state != RAW_PRESENT:
        return recorded
    if observed is None:
        return recorded
    return raw_state_for(observed)


def _marker_instant(value: object) -> datetime | None:
    """Parse a recorded ``rawStateAt``; ``None`` when it is unfit to reason on."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A naive stamp is read as UTC (the writer always mints UTC ISO-8601);
        # refusing it outright would make the policy silently inert on a
        # hand-written or foreign row, which is the direction that resurrects.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def deletion_marker_expires_at(props: object) -> str | None:
    """When the ``deleted`` marker's guarantee ends — the marker's expiry policy.

    ``None`` when there is no deletion marker on the record (the state is not
    ``deleted``, or ``rawStateAt`` is absent/unparseable).

    Derived from the RECORDED instant, never from a clock read, so the same
    record yields the same expiry on every machine and on every replay — the
    policy obeys ``derived = replay(journal)`` like every other recorded fact.
    """
    if raw_availability(props).state != RAW_DELETED:
        return None
    stamp = _marker_instant(props.get(RAW_STATE_AT_PROP)) if isinstance(props, dict) else None
    if stamp is None:
        # A `deleted` marker with no recorded instant cannot be bounded. That is
        # a malformed marker, not a licence to drop it: expiry is UNKNOWN, which
        # this reports as `None` — never as "already expired".
        return None
    try:
        return (stamp + RAW_DELETION_MARKER_TTL).isoformat()
    except OverflowError:
        # A stamp at the representable maximum (e.g. `datetime.max`) overflows
        # the window arithmetic. That marker is UNFIT to reason on, so expiry is
        # UNKNOWN — the same `None` as an unparseable stamp, and never a raise
        # on a foreign/legacy row. `is_deletion_marker_expired` then reports
        # `False`, the safe direction: an unboundable marker is never dropped.
        return None


def is_deletion_marker_expired(
    props: object,
    *,
    now: datetime | None = None,
) -> bool:
    """True when the deletion marker has passed its guarantee window.

    ⛔ EXPIRED IS NOT RESURRECTED. This predicate authorises a LATER, JOURNALLED
    drop of the marker; it does **not** change what a read reports.
    :func:`raw_availability` keeps returning ``deleted`` after expiry (pinned by
    the `test_a_deletion_marker_is_still_honoured_past_its_window` guard),
    because dropping the marker with no record makes a rebuild replay it back —
    the ``live != replay`` divergence lane L6 exists to remove. A marker with no
    recorded instant never reports as expired (the safe direction).

    ``now`` is the fake-clock seam: pass an instant to evaluate the policy at
    that instant. Nothing in this module sleeps.
    """
    expires = deletion_marker_expires_at(props)
    if expires is None:
        return False
    when = now if now is not None else datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when > datetime.fromisoformat(expires)


def raw_entry(
    props: object,
    *,
    source_id: str | None = None,
    observed: object = None,
) -> dict:
    """The graph's **index entry** for a raw — a reference, never a copy.

    This is the load-bearing shape of D30: what the graph keeps about a raw is
    *identity + version + availability*, and never the bytes. There is no field
    here a raw's bytes could be written into — **provided the caller passed a
    real digest for ``content_hash``**. That field is a hash BY CONVENTION, not
    by enforcement: ``contentHash`` is caller-supplied and is not
    length-checked, so a caller who passes 2 KB of body as the ``contentHash``
    stores it (measured, and returned here verbatim). This entry is a reference
    **by construction** — the entry's own keys are fixed and none of them is a
    payload field — but it cannot make a caller's *value* be a digest. Digest
    enforcement is a separate policy decision (what algorithm? what length?)
    that this change does NOT take; it is part of the stated value-level
    residual alongside `title`. Named here rather than left as a claim the
    previous revision made and the code did not support.

    ``observed`` is the LIVE determination seam
    (:func:`resolve_raw_availability`): a caller that HAS reached a conclusion
    about the raw's reachability passes it, and the entry reports that explicit
    outcome. A caller that has not (the SDK's record readers do not probe)
    passes nothing and gets the record. The key set is UNCHANGED by this seam —
    no new response field is added, so the SDK/MCP response surface is stable.

    Never raises, for the same reason the resolver does not: a read path calls
    this unconditionally, so a hostile or malformed ``props`` must degrade to
    ``None`` fields rather than propagate.
    """
    resolved = resolve_raw_availability(props, observed=observed)

    def _lookup(key: str):
        if not isinstance(props, dict):
            return None
        try:
            return props.get(key)
        except Exception:  # noqa: BLE001, RUF100 — mirrors raw_availability's guard
            return None

    return {
        "source_id": source_id if source_id is not None else _lookup("url"),
        "content_hash": _lookup("contentHash"),
        "raw_state": resolved.state,
        "raw_state_at": _lookup(RAW_STATE_AT_PROP),
        "available": not resolved.absent,
        "permanent": resolved.permanent,
        "retryable": resolved.retryable,
        "label": resolved.label,
        "message": resolved.message,
    }
