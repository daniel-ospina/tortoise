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

⛔ R1 — NO RETENTION WINDOW
    This module introduces **no retention window of any length**. Absence is
    *recorded*, never *scheduled*.

⛔ FAIL-CLOSED ON READ, STRICT ON WRITE
    A *writer* that passes a value outside the vocabulary is a caller bug, and
    is refused loudly (:func:`validate_raw_state`). A *reader* that meets one
    (an old row, a hand-edited node, a future value) must never **fail open**:
    an unrecognised recorded state resolves to :data:`RAW_UNRECOGNISED` —
    explicitly *not* "present" — because "we cannot interpret what was
    recorded" is not the same claim as "the raw is reachable". The safe
    default (no property written at all) is :data:`RAW_PRESENT`: no absence has
    been recorded, which is the shape of every source that has never been
    observed to be absent.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "RAW_ABSENT_STATES",
    "RAW_ACCESS_REVOKED",
    "RAW_DELETED",
    "RAW_OFFLINE",
    "RAW_PRESENT",
    "RAW_STATES",
    "RAW_STATE_AT_PROP",
    "RAW_STATE_PROP",
    "RAW_UNRECOGNISED",
    "WRITABLE_RAW_STATES",
    "RawState",
    "is_valid_raw_state",
    "raw_availability",
    "raw_entry",
    "validate_raw_state",
]

# ── the vocabulary ────────────────────────────────────────────────────────
#: No absence has been recorded — the default for every source.
RAW_PRESENT = "present"
#: The raw was deliberately and permanently removed by its owner.
RAW_DELETED = "deleted"
#: The raw's host is unreachable *now* — the raw is expected to be unchanged.
RAW_OFFLINE = "offline"
#: The credential/ACL that reached the raw no longer does. The bytes still
#: exist; our *permission* to read them does not. A permissions fact, not a
#: statement about the file.
RAW_ACCESS_REVOKED = "access_revoked"
#: A state IS recorded but this module does not recognise it. A READ-side
#: only value — never writable — so an unrecognised row can never read as
#: "present" (fail closed). See the module docstring.
RAW_UNRECOGNISED = "unrecognised"

#: The three causes the contract names — distinguishable on purpose.
RAW_ABSENT_STATES: tuple[str, ...] = (RAW_DELETED, RAW_OFFLINE, RAW_ACCESS_REVOKED)
#: Every state the resolver can return.
RAW_STATES: tuple[str, ...] = (RAW_PRESENT, *RAW_ABSENT_STATES, RAW_UNRECOGNISED)
#: Every state a WRITER may set (``RAW_UNRECOGNISED`` is read-side only).
WRITABLE_RAW_STATES: tuple[str, ...] = (RAW_PRESENT, *RAW_ABSENT_STATES)

#: Node property carrying the state on a ``:Source``.
RAW_STATE_PROP = "rawState"
#: Node property carrying when the state was recorded (ISO-8601).
RAW_STATE_AT_PROP = "rawStateAt"


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

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "absent": self.absent,
            "permanent": self.permanent,
            "retryable": self.retryable,
            "label": self.label,
            "message": self.message,
        }


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


def is_valid_raw_state(value: object) -> bool:
    """True when ``value`` is a state a WRITER may set."""
    return isinstance(value, str) and value in WRITABLE_RAW_STATES


def validate_raw_state(value: object) -> str:
    """Writer-side gate — strict, and loud on a bad value.

    A caller bug at write time is worth an exception: silently dropping a
    recorded absence would be the exact failure this module exists to prevent.
    """
    if not is_valid_raw_state(value):
        raise ValueError(
            f"Invalid raw_state {value!r} — must be one of "
            f"{', '.join(WRITABLE_RAW_STATES)}"
        )
    return value  # type: ignore[return-value]


def raw_availability(props: object) -> RawState:
    """Resolve the raw-availability state from anything a read path holds.

    **Never raises, and never fails open.** Accepts, in order:

    * a mapping of ``:Source`` node properties (reads :data:`RAW_STATE_PROP`);
    * a bare state string;
    * ``None`` / ``""`` ⇒ :data:`RAW_PRESENT` (nothing recorded);
    * **anything else — including a mapping whose lookup raises — ⇒
      :data:`RAW_UNRECOGNISED`**, which is ``absent``. We cannot tell what it
      says, so we do not claim the raw is reachable.

    A recorded value outside the vocabulary resolves to
    :data:`RAW_UNRECOGNISED` for the same reason.
    """
    if props is None:
        return _STATES[RAW_PRESENT]
    if isinstance(props, str):
        return _STATES.get(props, _STATES[RAW_UNRECOGNISED]) if props else _STATES[RAW_PRESENT]
    if isinstance(props, dict):
        try:
            value = props.get(RAW_STATE_PROP)
        except Exception:  # noqa: BLE001, RUF100 — a hostile mapping must not fail OPEN
            return _STATES[RAW_UNRECOGNISED]
        if value is None or value == "":
            return _STATES[RAW_PRESENT]
        if isinstance(value, str):
            return _STATES.get(value, _STATES[RAW_UNRECOGNISED])
        return _STATES[RAW_UNRECOGNISED]
    return _STATES[RAW_UNRECOGNISED]


def raw_entry(
    props: object,
    *,
    source_id: str | None = None,
    content_hash: str | None = None,
) -> dict:
    """The graph's **index entry** for a raw — a reference, never a copy.

    This is the load-bearing shape of D30: what the graph keeps about a raw is
    *identity + version + availability*, and never the bytes. ``content_hash``
    is a hash, so it is not a payload; there is no field here that a raw's
    bytes could be written into.
    """
    resolved = raw_availability(props)
    src = props if isinstance(props, dict) else {}
    return {
        "source_id": source_id if source_id is not None else src.get("url"),
        "content_hash": content_hash if content_hash is not None else src.get("contentHash"),
        "raw_state": resolved.state,
        "raw_state_at": src.get(RAW_STATE_AT_PROP),
        "available": not resolved.absent,
        "permanent": resolved.permanent,
        "retryable": resolved.retryable,
        "label": resolved.label,
        "message": resolved.message,
    }
