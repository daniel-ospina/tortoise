"""Semantic change feed — fold raw ``:GraphEvent`` rows into per-claim deltas (#2353).

WHY THIS EXISTS
---------------
``poll_events`` returns the ``:GraphEvent`` stream verbatim: a flat, typed event
list. Answering *"what changed for claim X since cursor C?"* therefore costs the
consumer a client-side replay across event types — re-deriving intent the graph
already recorded. This module answers it in one call by AGGREGATING the events
the poll already returns into per-claim deltas, each with ``before``/``after``.

WHAT IT IS NOT
--------------
* It is **not** a second store and **not** a new read surface. It is a pure fold
  over the rows a poll already produced, computed in the SDK assembly layer, and
  emitted as an **off-by-default additive response field** (``changes``) on the
  existing ``poll_events`` response — the recorded ``response_fields`` carve-out
  in ``docs/product/mcp-sdk-surface.md`` (no new tool, no new SDK method).
* It does **not** add event TYPES to the ``:GraphEvent`` store by default. The
  store's contract is the *claim-transition* stream (``_GRAPH_EVENT_TYPES``), a
  decision recorded in ``docs/plans/2026-08-08-432-subscriptions-plan.md`` §Task 3
  and pinned by ``tests/test_event_store.py::test_content_edit_emits_nothing``.
  ``PointRevised`` reaches the store **only** when this feature is switched on
  (see ``_emit_event``). ONE payload KEY is an exception, and it is deliberate:
  the hard-delete ``PointRetracted`` row carries ``deleted: true``
  UNCONDITIONALLY, because that marker states what the write path did and a
  reader-gated marker mislabels the deletes of every unswitched writer.

``before`` IS OBSERVED; A PRECONDITION IS NOT A ``before``
-------------------------------------------------------
A delta's ``before`` carries ONLY values the SAME window already observed for
that field (the fold keeps the last observed ``after`` per claim). It is omitted
when the window observed none — a content revision reports content
before→after because the previous revision is in the window, and a claim's FIRST
event in the window reports no ``before`` rather than a fabricated one.

Observation is necessary but NOT sufficient: a hard-delete row (``change:
deleted``) ENDS the incarnation, so the fold drops the claim's observed state and
the next event for that id reports no ``before`` however much of the window was
observed before it. The destroyed node's fields are not the history of the node
that replaced it, and reporting them would describe a content transition that
never happened.

A transition whose write path enforces a precondition carries that precondition
in its OWN key, ``precondition``, which is a property of the EVENT TYPE and
never of the observation. Keeping the two apart is load-bearing: a consumer must
be able to tell "we saw the old value" (``before``) from "the write path
refuses this transition unless the old state held" (``precondition``).

ONE EVENT TYPE, TWO END-STATES
------------------------------
``PointRetracted`` has two producers (#3300): ``retract_point``'s tombstone
(``status='retracted'``, node kept, terminal-guarded) and ``delete_point`` /
the capture stale-turn sweep's HARD delete (node destroyed, no guard). The two
payloads were identical, so a fold keyed on the type alone reported the
tombstone reading for a destroyed node — a fabricated ``after`` whose
``precondition`` contradicted its own ``before``. The hard-delete producers
therefore mark their payload ``deleted: true`` **unconditionally**: the marker
records what the WRITE PATH did, so it cannot be a function of the reading
process's switch. Gating it on the switch made the distinction depend on the
emitting process's environment — a deployment whose writer has the feed off and
whose reader has it on folded every hard delete as a tombstone, a fake
``status: retracted`` plus a ``precondition`` the delete path never enforced.
The fold reports a marked row as ``change: "deleted"`` with NO ``status`` and
NO precondition.

⚠️ **Residual, stated rather than papered over:** a row written BEFORE the
marker existed carries nothing that separates the two producers, and the fold
reads it as the tombstone. The distinction is exact from the marker's
introduction onward.

``before`` IS SCOPED TO THE PAGE IT WAS OBSERVED IN
---------------------------------------------------
The fold sees ONE page — the rows ``read_after(..., limit=limit)`` returned —
so ``before`` is a property of the page, not of the claim's history. The same
event reports ``before`` when its predecessor is in the page (and no hard delete
intervened), and NO ``before``
when a page boundary falls between them or an intervening hard delete ended the
incarnation; a client that pages therefore loses
``before`` for those claims whose events straddle a boundary, and ANY client
loses it for a claim that was hard-deleted and re-created inside the page. It is
still never invented (an unobserved field is absent, never back-filled from a
``precondition``), but "the window observed no earlier value" and "the window
started after the earlier value" are not distinguishable from the reply. A
pagination-stable ``before`` is a deliberate non-goal here: it would need a
per-subject back-read, which is a new read surface rather than a fold.

Shape (``changes`` in the ``poll_events`` response)::

    [
      {"id": "<claim id>",
       "changes": [
         {"seq": 4, "ts": "...", "event_id": "...", "event": "PointSuperseded",
          "change": "superseded",
          "precondition": {"terminal": False},
          "after": {"status": "superseded", "supersededBy": "..."}},
         {"seq": 5, "ts": "...", "event_id": "...", "event": "PointRevised",
          "change": "revised",
          "before": {"content": "v1"}, "after": {"content": "v2"}}
       ]}
    ]

Groups are ordered by their first ``seq``; deltas inside a group are in ``seq``
order. An event naming several claims (``OperatorAdded`` names its operator, its
``source_id`` and each of its ``target_ids``) yields one delta per named claim —
"evidence added" is news for the point it supports, not only for the operator.
"""
from __future__ import annotations

import copy
import os
from typing import Any

from .env_truthy import is_truthy

#: The single switch for the whole feature. Unset/blank/garbage -> OFF, so the
#: default surface is the pre-change one: the `changes` key is absent and no
#: `PointRevised` reach is emitted. NOT byte-identical, because ONE payload key
#: is unconditional — the hard-delete `PointRetracted` row carries
#: `deleted: true` whether or not anything is switched on (see the module
#: docstring).
CHANGE_FEED_ENV = "TORTOISE_CHANGE_FEED"

#: Payload keys that NAME claims rather than describe a changed value.
_SUBJECT_KEYS = ("id", "new_id", "source_id", "target_ids")

#: Keys `_emit_event` merges into the payload for the STORE's benefit — envelope
#: metadata about who wrote the row, never state of the claim. On the hosted/MCP
#: surface the actor ContextVar is set, so a catch-all revision arm would report
#: `actor_user_id` as claim state AND change the delta's field set depending on
#: whether a human was attached — the one thing the `_after` derivation must
#: never do.
_ENVELOPE_KEYS = frozenset({"actor_user_id"})

#: The four EP dimensions `annotate_operator` writes. Named once because two
#: event types carry them under two spellings (see the `PointRevised` arm).
_EP_DIMENSIONS = ("bias", "precision", "consistency", "directness")

#: Keys a `PointRevised` record may carry that are PLUMBING, not news: the
#: vector-machinery keys `update_point` journals (`#4208`/`#5004` — derived from
#: the content this same delta already reports) and the derived `content_hash`.
#: Catching whether content changed is what the delta is for; carrying a vector
#: would both bloat the feed and read as a second, drifting statement of it.
_PLUMBING_KEYS = frozenset({
    "embedding", "embedding_verbatim", "embedding_model",
    "embedding_revision", "embedding_text_hash", "content_hash",
})

#: The write-path precondition a transition IMPLIES — a property of the event
#: type, reported under its own key (never merged into an observed ``before``).
#: ``terminal`` is the predicate the shared lifecycle guard enforces
#: (``_terminal_excluded``): retract/supersede/invalidate refuse an already-
#: terminal claim. ``status: draft`` is ``promote_point``'s CAS.
_PRECONDITIONS: dict[str, dict[str, Any]] = {
    "PointSuperseded": {"terminal": False},
    "PointRetracted": {"terminal": False},
    "PointInvalidated": {"terminal": False, "outdated": False},
    # `promote_point` CASes `status='draft'`, but capture auto-promotion admits
    # an UNSET status too (`n.status IS NULL OR n.status = 'draft'`, sdk.py) and
    # emits this same type with no distinguishing payload. A precondition is the
    # predicate the write path refuses WITHOUT, so it must be the UNION every
    # producer enforces: `draft` alone overstates the capture leg. Admitted
    # values are listed when there is more than one.
    "PointPromoted": {"status": ["draft", None]},
    # `_promote_incident_operators` CASes `status='draft'`; `_apply_capture_ingest_ep`
    # admits an UNSET status too. Same union argument as `PointPromoted` above —
    # `draft` alone would claim a prior status the capture leg never required.
    "OperatorPromoted": {"status": ["draft", None]},
}


def enabled() -> bool:
    """Is the semantic change feed switched on for this process?"""
    return is_truthy(os.environ.get(CHANGE_FEED_ENV))


def _subjects(payload: dict[str, Any]) -> list[str]:
    """The claim ids this event is news ABOUT (ordered, de-duplicated).

    ``id`` first, then the ids the event names as related — so an
    ``OperatorAdded`` is reported against the operator, the point it supports
    and every target it relates. Deliberately NOT ``new_id``: a supersede is news
    about the claim that was superseded, and reporting the SUCCESSOR as
    ``superseded`` would state the opposite of what happened. The successor is
    named in the delta's ``after.supersededBy`` instead.
    """
    ids: list[str] = []
    for key in ("id", "source_id"):
        val = payload.get(key)
        if isinstance(val, str) and val and val not in ids:
            ids.append(val)
    targets = payload.get("target_ids")
    if isinstance(targets, (list, tuple)):
        for t in targets:
            if isinstance(t, str) and t and t not in ids:
                ids.append(t)
    return ids


def _after(type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The semantic "after" state this event establishes.

    Derived from the event's own meaning — never a ``properties(n)`` snapshot
    (a snapshot would report unchanged fields as "changed").
    """
    after: dict[str, Any] = {}
    if type_ == "PointAdded":
        for k in ("kind", "pointKind", "content_hash"):
            if k in payload:
                after[k] = payload[k]
    elif type_ == "PointRevised":
        for k, v in payload.items():
            if (k in _SUBJECT_KEYS or k in _PLUMBING_KEYS
                    or k in _ENVELOPE_KEYS):
                continue
            if k == "new_content":
                # `update_point` ALWAYS passes `new_content=props.get("content")`
                # (sdk.py), so a validity-, status-, tag- or annotation-only
                # revision reaches the store carrying `new_content: None`.
                # `None` means "no content edit" — `update_point` pops a
                # `content=None` rather than clearing (#5240) — so reporting it
                # would assert a content CLEAR nobody performed, and observing
                # `content: None` would poison the next delta's `before` (the
                # fold advances on `after`). Report only a real new value.
                if v is None:
                    continue
                # `new_content` is the journal spelling; `content` is the
                # claim's canonical field name, so the delta speaks canonical.
                after["content"] = v
                continue
            # `annotate_operator` emits `OperatorAnnotated` (payload short
            # aliases) and THEN calls `update_point(..., annotator_bias=...)`,
            # which emits THIS type. One EP dimension must not reach the feed
            # under two names: a consumer folding `after` would hold both `bias`
            # and `annotator_bias` for one number, two answers that can drift.
            # Fold the journal spelling to the canonical short alias.
            short = k[len("annotator_"):] if k.startswith("annotator_") else ""
            if short in _EP_DIMENSIONS:
                after[short] = v
                continue
            after[k] = v
    elif type_ == "PointSuperseded":
        after["status"] = "superseded"
        # `supersede_point` writes the legacy `outdated` flag alongside the
        # status (sdk.py: "#432: status='superseded' alongside the legacy
        # outdated=true"), and `record_projection.RECORD_MUTATIONS` carries it
        # for this same type. The `PointInvalidated` arm below reports it; a
        # consumer folding `after` must not see `outdated` present for one
        # terminal transition and absent for the other, on the same write.
        after["outdated"] = True
        if payload.get("new_id"):
            after["supersededBy"] = payload["new_id"]
        for src, dst in (("valid_to", "validTo"), ("expired_at", "expiredAt")):
            if payload.get(src) is not None:
                after[dst] = payload[src]
    elif type_ == "PointRetracted":
        # Two producers, two end-states (#3300): the tombstone keeps the node
        # and sets `status='retracted'`; the HARD delete DESTROYS it, so there
        # is no status to report. The marker (written UNCONDITIONALLY by the
        # hard-delete emit) is what tells them apart.
        if not _is_hard_delete(type_, payload):
            after["status"] = "retracted"
    elif type_ == "PointInvalidated":
        after["outdated"] = True
        if payload.get("corrected_by"):
            after["correctedBy"] = payload["corrected_by"]
        for src, dst in (("valid_to", "validTo"), ("expired_at", "expiredAt")):
            if payload.get(src) is not None:
                after[dst] = payload[src]
    elif type_ in ("PointPromoted", "OperatorPromoted"):
        after["status"] = "live"
    elif type_ == "OperatorAdded":
        for k in ("op_type", "source_id", "target_ids"):
            if payload.get(k) is not None:
                after[k] = payload[k]
    elif type_ == "OperatorAnnotated":
        for k in _EP_DIMENSIONS:
            if payload.get(k) is not None:
                after[k] = payload[k]
    # `DedupeRecorded` / `DedupeRejected` deliberately have NO arm. Every
    # in-tree emit uses the `point=` form (sdk.py), so `_emit_event` stores the
    # bare `{"id": ...}` — the candidate's outcome fields (`candidate_type`,
    # `wired`, `deferred_to_promotion`, `target_id`, `temporal_target_id`) are
    # the CALLER's return value and never reach the `:GraphEvent` payload.
    # Mapping them here yielded only dead keys and an empty `after`; the honest
    # delta is "a dedupe event was recorded for this claim" (the `change` name)
    # with no invented fields. The mitigation-creation `PointAdded` (`point=`
    # form) is the same shape and is equally not reinterpreted: the fold never
    # invents a snapshot the store did not carry.
    elif type_ == "ObjectSuperseded":
        after["status"] = "superseded"
        for k in ("supersedes_by", "session_id"):
            if payload.get(k) is not None:
                after[k] = payload[k]
    # COPY. `after` is built from the event payload's OWN objects, and the same
    # response carries that payload back under `events`; without a copy, a
    # consumer normalising or redacting one delta's `after` would silently
    # mutate the raw page — and every sibling delta for the same event — in the
    # same reply (measured: `changes[...].after["target_ids"] is
    # events[...].payload["target_ids"]`).
    return copy.deepcopy(after)


#: event type -> the semantic change name the delta carries.
_CHANGE_NAME = {
    "PointAdded": "created",
    "PointRevised": "revised",
    "PointSuperseded": "superseded",
    "PointRetracted": "retracted",
    "PointInvalidated": "invalidated",
    "PointPromoted": "promoted",
    "OperatorPromoted": "promoted",
    "OperatorAdded": "evidence-added",
    "OperatorAnnotated": "annotated",
    "DedupeRecorded": "dedupe-recorded",
    "DedupeRejected": "dedupe-rejected",
    "ObjectSuperseded": "superseded",
}


def _is_promote_revision(type_: str, payload: dict[str, Any]) -> bool:
    """Is this `PointRevised` row the draft→live promote door?

    `update_point` is "non-status except the draft→live promote" (sdk.py),
    enforced by the same `n.status IS NULL OR n.status = 'draft'` CAS
    `promote_point` uses — but it emits `PointRevised`, not `PointPromoted`. A
    consumer counting promotions from the feed would therefore miss every
    promotion made through the ordinary update path, and the guarded route
    carried no precondition at all. The payload's `status` tells them apart;
    everything else about the row is an ordinary revision.
    """
    return type_ == "PointRevised" and payload.get("status") == "live"


def _is_hard_delete(type_: str, payload: dict[str, Any]) -> bool:
    """Is this `PointRetracted` row a HARD delete rather than a tombstone?

    `PointRetracted` has two producers with one shared payload shape (#3300):
    `retract_point` keeps the node (`status='retracted'`, terminal-guarded) and
    `delete_point` / the capture stale-turn sweep DESTROY it (no guard). The
    hard-delete emit marks its payload `deleted: true` — unconditionally, since
    the marker describes the write and not the reader's switch. Rows written
    before the marker existed cannot be told apart and read as tombstones.
    """
    return type_ == "PointRetracted" and payload.get("deleted") is True


def _change_name(type_: str, payload: dict[str, Any]) -> str | None:
    """The semantic change name for a row, producer-aware for `PointRetracted`.

    A hard delete is not a retraction — the destroy and the tombstone are
    different news — so the marker selects the `deleted` name. Every other type
    maps by name alone.
    """
    if _is_hard_delete(type_, payload):
        return "deleted"
    if _is_promote_revision(type_, payload):
        return "promoted"
    return _CHANGE_NAME.get(type_)


def _precondition(type_: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The write-path precondition to report for this row, if any.

    `PointRetracted` is special-cased: the hard-delete producer is the one
    transition with NO guard at all (`delete_point` checks only existence), so
    attaching the tombstone's `terminal: False` to it would assert a refusal
    that never happened — contradicting the row's own `before`.
    """
    if _is_hard_delete(type_, payload):
        return None
    if _is_promote_revision(type_, payload):
        return _PRECONDITIONS["PointPromoted"]
    return _PRECONDITIONS.get(type_)


def _observed_before(after: dict[str, Any],
                     observed: dict[str, Any]) -> dict[str, Any]:
    """The fields this delta changes FOR WHICH the window already saw a value.

    Observed-only by construction: an unobserved field is absent from the
    result, never back-filled from a precondition (that has its own key) and
    never invented.
    """
    return {field: observed[field] for field in after if field in observed}


def aggregate_changes(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Fold ``poll_events`` rows into per-claim semantic deltas.

    ``events`` is the list ``read_after`` returns: dicts carrying ``seq``,
    ``ts``, ``type``, ``event_id`` and a decoded ``payload`` mapping. An
    unknown/unshaped row is skipped rather than raising — the feed describes
    what the stream holds, and a stream is allowed to grow types this build
    does not know.
    """
    observed: dict[str, dict[str, Any]] = {}
    last_after: dict[str, dict[str, Any]] = {}
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for ev in sorted(events or [], key=lambda e: e.get("seq") or 0):
        type_ = ev.get("type")
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        name = _change_name(type_, payload)
        if name is None:
            continue
        after = _after(type_, payload)
        for subject in _subjects(payload):
            seen = observed.get(subject, {})
            delta: dict[str, Any] = {
                "seq": ev.get("seq"),
                "ts": ev.get("ts"),
                "event_id": ev.get("event_id"),
                "event": type_,
                "change": name,
            }
            before = _observed_before(after, seen)
            if before:
                delta["before"] = before
            precondition = _precondition(type_, payload)
            if precondition:
                # Deep copy: a shallow `dict(...)` leaves the value OBJECT
                # (`["draft", None]`) shared with the module constant, so a
                # consumer mutating what it was handed corrupts the
                # precondition every later call in this long-lived process
                # reports.
                delta["precondition"] = copy.deepcopy(precondition)
            delta["after"] = copy.deepcopy(after)
            # A delta that repeats the IMMEDIATELY PRECEDING one for this subject
            # is not news. `annotate_operator` emits `OperatorAnnotated` and then
            # a JSONL-parity `PointRevised` carrying the same numbers under the
            # same folded keys, so without this the feed reported an extra
            # `revised` whose `after` equalled its own `before`.
            #
            # IMMEDIATELY PRECEDING, not "we have seen these values before": a
            # hard delete carries `after == {}` and CLEARS this slot, which is
            # what keeps delete-then-re-create from being swallowed — the
            # re-created point's `after` repeats its pre-delete values, so a
            # seen-before rule would tell a consumer the claim is still gone.
            if after and after == last_after.get(subject):
                last_after[subject] = after
                continue
            group = grouped.get(subject)
            if group is None:
                group = {"id": subject, "changes": []}
                grouped[subject] = group
                order.append(subject)
            group["changes"].append(delta)
            # The fold advances on EVERY subject the event named: the same
            # observed state is what the next delta for that subject compares to.
            observed[subject] = {**seen, **after}
            last_after[subject] = after
            # A HARD delete destroys the incarnation, so it clears the subject's
            # OBSERVED state as well as the suppression slot: the next event for
            # this id is a BIRTH, and carrying the destroyed node's fields
            # forward would report a content transition that never happened —
            # and, on a same-content re-create, `change: "created"` with
            # `before == after`. This is the module contract ("a claim's FIRST
            # event in the window reports no `before` rather than a fabricated
            # one") applied to an incarnation boundary.
            #
            # Only the HARD delete: `DedupeRecorded` / `DedupeRejected` also
            # fold to an empty `after`, and the subject is still there.
            if _is_hard_delete(type_, payload):
                observed.pop(subject, None)

    return [grouped[s] for s in order]
