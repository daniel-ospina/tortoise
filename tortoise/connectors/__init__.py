"""P1-5: External connectors.

Connectors poll external services and derive EventRecorded events
for the Tortoise event log. Uses `gh` CLI for GitHub — no PyGithub needed.
"""
from __future__ import annotations

#: "the caller did not hand us an already-resolved approval set" — a private
#: sentinel, NOT ``None``: ``None`` is the RESOLVED answer "this graph has no
#: ``:PackInstall`` records" (the catalog union ⇒ no gate). A default that
#: doubled as both would either skip the gate on the second batch or pay the
#: whole-graph read twice.
_READ_THE_SET: object = object()


def _gate_kinds(proj, records, *, approved=_READ_THE_SET) -> object:
    """#9068: the approved-pack gate for a connector's fold batch.

    Connectors fold records with a bare ``proj.apply(...)`` instead of going
    through ``TortoiseSDK``'s write funnels, so they never met the #5475 door.
    ``FalkorProjection.apply`` refuses a direct fold only on a JOURNAL-BACKED
    projection (§17.1's single-writer guard) — and there the cache is a derived
    image of the journal. On a journal-less projection the cache IS the store,
    so the replay-fidelity argument (a record already appended reproduces
    faithfully, which is why refusing at replay is worse than the widening —
    durability authority lives only in ``docs/durability-posture.md``) does not
    apply, and the fold is a real write with nothing in front of it. Measured before this
    gate: ``github``'s ``object_kind='pm:issue'`` put ``pm`` into the approved
    set of a graph whose only ``:PackInstall`` record is ``dev``.

    ⛔ ONE PREDICATE, NOT A CONNECTOR COPY. The decision, the key set and the
    refusal's text are ``pack_state``'s: ``payload_names_pack`` walks each
    record once and returns the ``(prop, value)`` pairs that name a pack,
    ``graph_approval_namespaces`` reads the target graph's set, and
    ``refuse_unapproved_kinds_in_payload`` raises. A connector that screened
    its own key list would be a second rule wearing the gate's name — the
    shape that let the widening survive the first closure.

    One approval-set READ per BATCH, before the first ``apply``: a refusal
    therefore leaves the graph untouched instead of half-imported.

    ``approved=`` hands a set forward, so a connector that gates two batches in
    ONE pass (github: the entity chains, then the poll events) pays one read.
    A batch whose kinds name NO pack namespace returns ``_READ_THE_SET``
    unchanged — it never read the set, so it must not tell the next batch that
    it did.

    ⚠️ WHY THE PRE-CHECK MATTERS HERE: the read is ``_read_installs`` +
    ``_read_selection`` + ``graph_kind_namespaces`` (8 whole-graph property
    scans, O(nodes)). The WEBHOOK arms call this with ONE record per delivered
    message, where "one read per batch" saves nothing — so a batch that names
    no pack (the overwhelming majority: connector records carry BARE
    ``sourceKind``/``eventKind`` spellings) must pay nothing at all.
    """
    from tortoise.pack_state import (
        graph_approval_namespaces,
        payload_names_pack,
        refuse_unapproved_kinds_in_payload,
    )

    # ⛔ ONE DICT IS ONE RECORD, NOT a sequence of its keys (#9068 review P2).
    # ``records`` iterated a bare dict would yield its STRING keys, every one
    # of which fails the ``isinstance(r, dict)`` filter below — so a caller
    # that handed the gate a single record would have it gate NOTHING, on a
    # security door, in the shape the singular predicate invites. Both live
    # call sites pass lists; this makes the quiet fail-open impossible rather
    # than merely unused.
    if isinstance(records, dict):
        records = [records]
    # Walk each record ONCE; the pairs the pre-check computed are handed to the
    # gate, so the payload is not walked a second time.
    gated = [(r, spells) for r in records
             if isinstance(r, dict) and (spells := payload_names_pack(r))]
    if not gated:
        return _READ_THE_SET
    if approved is _READ_THE_SET:
        approved = graph_approval_namespaces(proj.g)
    for record, spells in gated:
        refuse_unapproved_kinds_in_payload(record, approved, spells=spells)
    return approved
