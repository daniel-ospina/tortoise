"""Retired public SDK method names and their replacements (#4282 phase 2).

The owner ruled (#3836, option (c), 2026-09-23) that a retired SDK name must **fail
and name its replacement** — there is deliberately NO alias layer and NO warning shim.
``TortoiseSDK.__getattr__`` consults :data:`RETIRED_SDK_METHODS` and raises an
``AttributeError`` naming where the caller should go instead.

**OVERRIDES:** the industry default and our own earlier ruling (#3836 (b)) — *old names
keep working and warn through the beta* — is overridden: a retired name FAILS with a
migration hint rather than resolving. Reason: the warn-and-continue behaviour existed
solely to harvest telemetry on which retired names were still called, and with zero
users there is no telemetry to harvest and no caller to protect.

WHY THIS IS A SMALL HAND-KEPT MAP, NOT A READ OF THE MIGRATION TABLE
--------------------------------------------------------------------

``tools/sdk_rename_table.py`` is the migration map: every name on the LIVE surface
(re-derive the count with ``tools/sdk_surface.py --check``; the numeral is not typed here
because it moves with every wave) and its approved destination, most of whose
renames have **not** landed yet. Two reasons this
map is separate and deliberately tiny:

  * a product module must not import a build tool, and that table reads two product docs
    at import time;
  * the table maps names that still EXIST. This module holds only names that have
    **already left** the surface, in the two shapes a departure takes:

      - :data:`RETIRED_SDK_METHODS` — a RENAME. The name left and a customer-visible
        replacement exists, so ``__getattr__`` FAILS and NAMES it (#3836 (c)). These
        are the names ``__getattr__`` will actually be asked about.
      - :data:`REMOVED_SDK_METHODS` — a departure with NO replacement to name: the
        method went INTERNAL (``graph_count`` → ``_graph_count``, kept for the plan
        quota) or was DELETED (``org_list`` — it returned every tenant and had zero
        callers). No hint is emitted for these, and that is deliberate rather than an
        omission: this is not a ``#3836 (c)`` retirement, the rename hint's wording
        ("use X instead") would be false for a method that is deliberately not coming
        back in any form, and ``retirement_hint`` has no way to say "no replacement".
        They are recorded all the same — the point of this module is that every
        departure is ACCOUNTED FOR, not that every departure has a hint.

``tests/test_sdk_retirement.py`` pins every entry here to the owner-approved
``docs/product/beta-sdk-surface.md`` — each RETIRED name carries the ledger region that
authorises it (``RETIRED_SDK_APPROVALS``), and the gate checks that the region is
present VERBATIM in that document, that it names the retired name, and that the
replacement is one of the numbered target rows the region points at. An entry cannot
be added there that the owner has not approved: an unapproved rename would otherwise
fail every caller of a live method with a hint pointing at a name the owner never
approved. A green run is a CONSISTENCY CHECK against that document, never consent to
edit it.

The REMOVAL record has no such owner document to quote (the ruling deletes the name and
names no replacement), so it is pinned differently, in the direction that was missing
before #4282 phase 2.3: the gate requires each entry to name a method that has really
left the surface, to be accounted for in the inventory, and to keep the recorded-
universe count whole — so a departure cannot go unrecorded the way these two did. And
for the recorded-destination cells it holds the RECORDED DESTINATION too:
:data:`SURPLUS_DELETION_DESTINATIONS` (the 26-name `#4282` block),
:data:`WAVE1_DELETION_DESTINATIONS` (the `#12901` wave-1 block) and
:data:`WAVE2_DELETION_DESTINATIONS` (the `#12901` wave-2 control-plane block) are all
transcribed from
the inventory's own column and every reason below is pinned to them, because a hand-typed
destination is how TEN of them
drifted off the record (#10226 review, 2026-11-10) — and how one wave-1 destination
(`quarantine_batch` → a LIVE `update_knowledge`) sat outside the pin entirely until
2026-11-16.
"""

from __future__ import annotations

#: retired public method name -> the approved replacement to name in the failure.
RETIRED_SDK_METHODS: dict[str, str] = {
    # #4282 phase 2.1 — the event-log cursor poll. Approved target:
    # `docs/product/beta-sdk-surface.md` row 11 (`poll_events`); cited by
    # `tools/sdk_rename_table.py` region `r8`.
    "events_poll": "poll_events",
    # #4282 phase 2.1 — the point-in-time read. Approved target: the beta doc's own
    # rename ledger (`| `restore_point_at` | → row 7 **`get_historical_knowledge`** |`);
    # cited by `tools/sdk_rename_table.py` region `r3_restore`.
    "restore_point_at": "get_historical_knowledge",
    # #4282 phase 2.3 — the memory-graph read. Approved by the beta doc's rename
    # ledger group row (`graph_delete`, `graph_restore`, `graph_list`,
    # `graph_set_name` → rows 30–33 `*_memory_graph*`).
    #
    # `graph_count` is NOT here, and it is no longer "deferred": the owner's
    # 2026-10-25 ruling settled it — the counter is kept INTERNAL as
    # `_graph_count` rather than folded into `list_memory_graphs`, because folding
    # is a BEHAVIOUR change (the meter excludes tombstoned graphs while the
    # selfhost `list_memory_graphs` returns them), not a rename. It is recorded in
    # REMOVED_SDK_METHODS below, with no hint.
    "graph_list": "list_memory_graphs",
    # #4282 phase 2.3 — trash restore.
    "graph_restore": "restore_memory_graph",
    # #4282 phase 2.3 — the two per-field setters fold into ONE partial update
    # (row 30). `graph_set_name` is authorised by the group row above;
    # `graph_set_recording` by its own discarded-row entry, which names row 30.
    "graph_set_name": "update_memory_graph",
    "graph_set_recording": "update_memory_graph",
    # #4282 (2026-10-27) — the provenance read, one of the 26 surplus names. Its recorded
    # destination is `get_entity` (the owner ledger's "narrow readers" collapse, and the
    # canonical inventory's own column), and `get_entity` HAS a `def` today — so this name
    # is a real #3836 (c) retirement with a hint rather than a bare `AttributeError`.
    # The approving region names no target-row ORDINAL; the ordinal is DERIVED (the region
    # names exactly one approved target), which is why nothing is invented here — see
    # `tests/test_sdk_retirement.py::_approved_replacements`' derived-ordinal arm.
    "get_provenance_chain": "get_entity",
    # ── #4282 Phase 2 — the SOURCES / MINE / SESSION containers land ─────────
    #
    # Option B, taken literally: "retire each legacy name in the same release that
    # lands its replacement". Four of these five were already RECORDED as removed
    # (`index_file`, `capture_session`, `commit_session`) and two of them cited a
    # destination with "no `def` today, so there is no surface name to point a hint
    # at" — that condition is now FALSE, so the bare `AttributeError` they carried
    # becomes a real migration hint. The state that changes a record's SHAPE is the
    # one a reader must be told about, so each reason below says it moved.
    "assess_source": "manage_source_trust",
    "set_source_tier": "manage_source_trust",
    "index_file": "index_sources_from_directory",
    "capture_session": "mine_knowledge_from_session",
    "commit_session": "mine_knowledge_from_session",

    # ── #4282 Phase 2, the WRITE/MUTATE containers (#11609 lane B) ────────────
    # Each of these six left the surface in the release that landed its
    # replacement, which is the owner's Option B. The replacements are the four
    # approved target rows the beta doc's own "Removed" table names for them, and
    # every pair is quoted VERBATIM in `RETIRED_SDK_APPROVALS` below.
    #
    # ⛔ WHAT IS DELIBERATELY NOT HERE, and why (stated so a later lane does not
    # "finish" the job by retiring a capability):
    #   * `update_point` and `supersede_point` — both carry a §17.1 write-lane
    #     DECLARATION keyed by the door's NAME (`_declares_apply_first_write_lane` /
    #     `_declares_live_write_lane`), and three gate suites pin that census by name
    #     (`tests/test_9124_declared_live_doors.py`, `test_9397_...`,
    #     `test_10047_record_every_lane.py`). Renaming them is a write-lane change with
    #     its own review, not a rename this batch can carry — so they stay public and
    #     the container's `def` delegates to them, which is the same behaviour with the
    #     old name still available.
    #   * `invalidate_point` — its `valid_to_precision='unknown'` window state
    #     (#2350: "this ended, but I cannot date it") has NO home on
    #     `update_knowledge`, so retiring it would DELETE the state rather than
    #     rename it. `supersede_knowledge(transfer_edges=False)` covers its
    #     everyday arm; the precision arm keeps the door.
    #   * `retract_point` — same class: `update_knowledge`'s temporal arm retracts
    #     WITH an instant, and the door's instant-free retraction is the shape its
    #     callers use. Retiring it is a signature change to the approved verb, not
    #     a rename.
    #   * `promote_point`, `set_point_baseline` — their approving row names TWO
    #     destinations ("a field on `update_knowledge`" AND a filter on
    #     `list_knowledge`), so `tests/test_sdk_retirement.py` cannot derive a
    #     single approved replacement for them. The MCP tools DO retire (into
    #     `tortoise_refresh_confidence`); the SDK names wait on the owner settling
    #     §D2c of `docs/product/bridge-table.md`.
    #   * `annotate_operator`, `mitigate_operator` — same two-destination row
    #     ("`adjust_relationship` for strength, `update_knowledge` for annotation").
    #     The MCP tools retire into `tortoise_adjust_relationship`; the SDK names
    #     wait on the same ruling.
    "supersede": "supersede_knowledge",
    "update_entity": "update_knowledge",
    "file_decision": "write_question",
    "file_human_approval": "record_decision",
}

#: The RECORDED destination of every name in the #4282 surplus block — one machine-readable
#: cell, transcribed from the NON-OWNER inventory's own column
#: (`docs/product/canonical-sdk-methods.md`, "The 40-method target it was recorded
#: against"). An EMPTY tuple means the record names NO destination (`—` in that column):
#: the name was deleted with nothing to migrate to.
#:
#: WHY A CELL AND NOT JUST THE PROSE. The reason strings below used to carry the destination
#: on their own, and a free-text copy of a machine record is a copy that rots. Measured
#: 2026-11-10 (#10226 review, P2) against the inventory as it stood: FOUR of the then-fifteen
#: "recorded destination" entries named a target the record never recorded
#: (`batch_create_points` → `create_entity` where the record says `write_knowledge_batch`;
#: `get_provenance_chain` → `check_confidence` where it says `get_entity`; `list_drafts` →
#: `update_knowledge` where it says `list_knowledge`; `record_calibration` →
#: `refresh_confidence` where it records NONE), THREE named a destination where the
#: record names NONE (`backfill_about_entities`, `backfill_sources`, `complete_source`), and
#: THREE claimed "no recorded destination" for names the record DOES give one (`apikey_list`
#: → `list_keys`, `membership_list` → `list_members`, `membership_delete` →
#: `remove_member`). Both counters above the block were wrong by one. The destinations now
#: live HERE once, and `tests/test_sdk_retirement.py` re-reads that inventory column and
#: holds every entry below to it — in both directions, so neither a wrong destination nor a
#: "none" can be re-typed into a reason.
#:
#: The owner's ledger states three things this cell deliberately does NOT: that `list_drafts`'
#: lifecycle is a FIELD on `update_knowledge`; that `create_or_update_point` is named
#: `create_point` by that ledger's own row (see :data:`UNHINTED_LIVE_DESTINATIONS`); and that
#: `record_calibration`'s delete verdict is UNSTAKED. Those are recorded in the reasons.
SURPLUS_DELETION_DESTINATIONS: dict[str, tuple[str, ...]] = {
    "apikey_list": ("list_keys",),
    "backfill_about_entities": (),
    "backfill_sources": (),
    "batch_create_points": ("write_knowledge_batch",),
    "capture_session": ("mine_knowledge_from_session",),
    "cleanup_expired_invitations": (),
    "commit_session": ("mine_knowledge_from_session",),
    "complete_source": (),
    "create_or_update_point": ("create_entity",),
    "get_org_structure": ("explore_connections",),
    # The ONE surplus name that is a real #3836 (c) retirement: its recorded destination is
    # `get_entity`, which HAS a `def` today — so it lives in `RETIRED_SDK_METHODS` above.
    "get_provenance_chain": ("get_entity",),
    "index_file": ("index_sources_from_directory",),
    "invitation_accept": (),
    "invitation_create": (),
    "invitation_get_by_token": (),
    "list_drafts": ("list_knowledge",),
    "membership_delete": ("remove_member",),
    "membership_list": ("list_members",),
    "membership_update_role": (),
    "migrate_orgs_to_registry": (),
    "operator_action": ("adjust_relationship",),
    "org_delete": (),
    "org_update": (),
    "record_calibration": (),
    "retrieval_legs": ("check_confidence",),
    "traverse": ("explore_connections",),
}

#: The recorded destinations in BOTH deletion cells that HAVE a `def` on `TortoiseSDK`
#: today but which must stay UNHINTED, with the reason. A hint exists to be FOLLOWED
#: (#3836 (c)), so a live destination should always produce one; this cell is the
#: exception, and it is a DECLARATION rather than an omission —
#: `tests/test_sdk_retirement.py` reds on a live destination in either cell that is
#: neither hinted nor declared here, and on an entry here that has stopped being live.
UNHINTED_LIVE_DESTINATIONS: dict[str, str] = {
    # ⛔ #12915 review: an `operator_action` entry used to sit here, asserting that
    # `adjust_relationship` "now HAS a `def`, which makes operator_action's recorded
    # destination LIVE". That premise was FALSE — `adjust_relationship` is an MCP TOOL
    # container (`tool_registry`'s own row says "handler-served: no declared SDK link")
    # and has never been a `TortoiseSDK` method, so the destination is not live and the
    # declaration was a stale excuse. `test_sdk_retirement.py` derived exactly that
    # (`live_unhinted` = {create_or_update_point} vs the declared pair). The name is
    # still accounted for where it belongs: `REMOVED_SDK_METHODS["operator_action"]`
    # records the removal and names the surviving writes, and an SDK caller gets no
    # hint because the retirement record names no replacement for THAT name.
    "create_or_update_point": (
        "two records name two different destinations, so a hint would pick a winner nobody "
        "recorded: the canonical inventory's column says `create_entity`, while the owner's "
        "own ledger row for this name says `create_point` — and `create_point` is not one of "
        "the 40 numbered targets, so an ordinal for it cannot be recorded. Reconciling the "
        "two records is #4282 phase 2 work; until then this departure names nothing."
    ),
    "quarantine_batch": (
        "two records name two different destinations, so a hint would pick a winner nobody "
        "recorded: the canonical inventory's column says `update_knowledge` (quarantine is "
        "a FIELD on the partial update, not a verb), while the beta `w11_lifecycle` region "
        "the approval quotes names TWO targets, `update_knowledge` and `list_knowledge`, and "
        "the derived-ordinal arm refuses to choose between them. The capability is not lost "
        "either way — `tortoise.mining.quarantine_batch` survives and is what the delegate "
        "called — so until the records are reconciled this departure names nothing."
    ),
    # ⛔ #12901 wave 2: the one wave-2 name whose recorded destination is LIVE.
    # `get_organisation_account` has a `def` AND is an approved row, and the departure
    # STILL gets no hint — the beta ledger never names `org_get`, so the region a
    # #3836 (c) retirement requires (one naming BOTH names) does not exist. Declaring the
    # divergence is what keeps a live recorded destination from silently escaping the pin.
    "org_get": (
        "a live recorded destination with no approval to point a hint at: "
        "`get_organisation_account` IS on the surface and IS the destination "
        "`WAVE2_DELETION_DESTINATIONS` records, but `beta-sdk-surface.md` never names "
        "`org_get` — so the region a #3836 (c) hint requires does not exist and a hint "
        "would record an approval nobody wrote. The capability is not lost: the body "
        "survives as `_org_get`, which the hosted control plane reads."
    ),
}

#: The #12901 wave-1 names' recorded destination, transcribed from the inventory's own
#: column exactly as :data:`SURPLUS_DELETION_DESTINATIONS` is, and pinned to it in both
#: directions by `tests/test_sdk_retirement.py`.
#:
#: ⛔ WHY IT IS A CELL AND NOT PROSE. The surplus block learned this the expensive way: a
#: hand-typed destination in a reason is a copy of a machine record, and the copy rots. The
#: wave-1 reasons below carried their destinations in prose only, OUTSIDE the pin, so the
#: gate that keeps `UNHINTED_LIVE_DESTINATIONS` honest could not see them — measured
#: 2026-11-16, a fresh-context reviewer: `quarantine_batch` records the LIVE
#: `update_knowledge` and no arm could have noticed. This cell is the population that arm
#: now reads (the 26-name `#4282` block stays CLOSED — its own counter is untouched).
WAVE1_DELETION_DESTINATIONS: dict[str, tuple[str, ...]] = {
    "backfill_v25": (),
    "calibration_passed": ("check_confidence",),
    "list_relations": ("graph_overview",),
    "membership_get": (),
    "quarantine_batch": ("update_knowledge",),
}

#: The #12901 wave-2 names' recorded destination — the same shape, the same pin.
#:
#: Wave 2 is the canonical inventory's control-plane partition (N1–N6): the console /
#: hosted plumbing that the owner-approved beta surface hands to the TENANCY block
#: (`get_organisation_account`, `create_key`, `list_keys`, `revoke_key`, `add_member`,
#: `delete_memory_graph`) or to nobody at all. Every destination below is the one
#: `tools/sdk_rename_table.py` records for the name (its `GROUP_TARGET` / `OVERRIDE`
#: row, whose own evidence is the beta ledger region quoted in its `CITES` cell) — none
#: is invented here, and `tests/test_sdk_retirement.py` re-reads the inventory column and
#: holds every entry to it in both directions, exactly as it holds the two cells above.
WAVE2_DELETION_DESTINATIONS: dict[str, tuple[str, ...]] = {
    "apikey_create": ("create_key",),
    "apikey_revoke": ("revoke_key",),
    "apikey_verify": (),
    "graph_active_key_count": ("list_keys",),
    "graph_delete": ("delete_memory_graph",),
    "graph_key_ids": ("list_keys",),
    "invitation_get_by_id": (),
    "invitation_list": (),
    "invitation_revoke": (),
    "membership_create": ("add_member",),
    "org_get": ("get_organisation_account",),
    "signup_token_lookup": (),
    "signup_token_recover": (),
    "signup_token_revoke": (),
    "sweep_invite_ghost_memberships": (),
    "trash_graphs": (),
}

#: removed public name -> why it is gone, and what a caller does instead (prose only).
#:
#: These left the surface WITHOUT a replacement to name, so they are NOT
#: :data:`RETIRED_SDK_METHODS` entries and ``retirement_hint`` must keep returning
#: ``None`` for them (a hint would claim an approved rename that does not exist).
#: What they owe the next reader is a RECORD, so that a name disappearing from
#: ``TortoiseSDK`` is never silent — `tests/test_sdk_retirement.py` pins every entry
#: here to the non-owner inventory (`docs/product/canonical-sdk-methods.md`) and pins
#: the accounted universe, so a future departure cannot skip this table either.
REMOVED_SDK_METHODS: dict[str, str] = {
    "graph_count": (
        "#4282 phase 2.3 (owner ruling 2026-10-25): kept INTERNAL as `_graph_count` "
        "for the plan-limit check. A customer counts by calling `list_memory_graphs` — "
        "which is why this is not a rename: the counter excludes tombstoned graphs "
        "while `list_memory_graphs` returns them, so folding would change the number."
    ),
    "org_list": (
        "#4282 phase 2.3 (owner ruling 2026-10-25): DELETED. It returned every tenant "
        "on the account and had zero callers; the ruling ships no account-list call "
        "in beta, so there is deliberately no replacement to name."
    ),
    # ── #4282, the 26 surplus names (owner amendment 2026-10-27, PR #10187) ──────
    #
    # These are DELETIONS, not renames, for a reason that is about the TARGET surface and
    # not about the old name. The disposition recorded for each of them is a
    # MACHINE-READABLE cell — :data:`SURPLUS_DELETION_DESTINATIONS` — because the prose
    # below used to carry it alone and it drifted (see that cell's own note for the 2026-11-10
    # measurement; `tests/test_sdk_retirement.py` now re-reads the inventory column and holds
    # every reason to it). 14 of the 26 have a recorded destination and 12 record none.
    #
    # A hint must NAME something a caller can call, so a recorded destination can only reach
    # `RETIRED_SDK_METHODS` when it HAS a `def` on `TortoiseSDK` today. Measured at this head,
    # exactly TWO of the 14 qualify:
    #
    #   * `get_provenance_chain` → `get_entity` — LIVE, so it IS a `RETIRED_SDK_METHODS`
    #     entry above, with a real migration hint.
    #   * `create_or_update_point` → `create_entity` — LIVE, but deliberately UNHINTED: the
    #     owner's ledger row for that name points at `create_point`, so two records disagree.
    #     :data:`UNHINTED_LIVE_DESTINATIONS` carries that reason, and the pin holds the cell to
    #     exactly the set it declares — which is now THREE names, the third being
    #     `quarantine_batch` → `update_knowledge` from the `#12901` wave-1 cell below (it is
    #     outside this 26-name block, so it does not move the counters here).
    #
    # ⚠️ THREE MORE QUALIFY AS OF #4282 PHASE 2's SOURCES CONTAINERS (#11609): `capture_session`
    # and `commit_session` (→ `mine_knowledge_from_session`) and `index_file` (→
    # `index_sources_from_directory`) had a recorded destination whose `def` did not exist, so
    # they were recorded HERE with the "no surface name to point a hint at" reason. Those three
    # containers landed, the condition is now FALSE, and keeping them here would be a record
    # that says a hint is impossible when it is not — they MOVED to `RETIRED_SDK_METHODS` above.
    # Their destinations stay in `SURPLUS_DELETION_DESTINATIONS` (that cell is the RECORD, and it
    # is pinned at 26 names); only the shape of the departure changed. This is the direction the
    # test `test_a_live_recorded_destination_is_hinted_or_declared` pins, in both directions.
    #
    # The other nine recorded destinations are the Phase-2 targets
    # `check_confidence`, `explore_connections`, `write_knowledge_batch`, `list_knowledge`,
    # `list_keys`, `list_members`, `remove_member` and `adjust_relationship` — no `def`
    # today, so a hint
    # naming one would be false and
    # `tests/test_sdk_retirement.py::test_every_replacement_exists` would red on it. The
    # destination is still recorded per name, which is the whole job of this table — an
    # unrecorded departure is the failure it exists to prevent.
    #
    # The archive is tagged `archive/4282-surplus-sdk-methods` (17f604b054), so every
    # body below is recoverable; the owner's ruling ships no alias layer and no
    # deprecation warning (#3836 (c)).
    #
    # :data:`SURPLUS_DELETION_DESTINATIONS` is the authority for each entry's destination;
    # the reasons below carry it once, in prose:
    "retrieval_legs": (
        "#4282 (2026-10-27): DELETED. Recorded destination `check_confidence`; the "
        "retrieval-leg breakdown is not a customer verb."
    ),
    "traverse": (
        "#4282 (2026-10-27): DELETED. Recorded destination `explore_connections`, "
        "approved with no `def` today — so the graph walk is unreachable from the SDK "
        "until Phase 2 lands that target."
    ),
    "get_org_structure": (
        "#4282 (2026-10-27): DELETED. Recorded destination `explore_connections`."
    ),
    "backfill_about_entities": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: the ledger rules the "
        "four backfills (this, `backfill_sources`, `backfill_v25`, `reconcile_sessions`) "
        "`One-shot migrations. Run once, then dead code carrying a public promise`, and "
        "the inventory's destination column is `—`. This is a JUDGEMENT (delete, ship "
        "nothing) rather than a mapping: back-filling edges over history is not the "
        "indexer's question, so no successor is named."
    ),
    "backfill_sources": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination — same ledger region and "
        "same judgement as `backfill_about_entities`: a one-shot migration, deleted rather "
        "than migrated."
    ),
    "complete_source": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: the ledger's verdict is "
        "**Cut.** — its entire body populates `contentHash`, `version`, `externalId`, which "
        "`register_source` already writes, and it had **zero callers in the repo**. "
        "`register_source` is the REASON it was cut, not a target to migrate to."
    ),
    "batch_create_points": (
        "#4282 (2026-10-27): DELETED. Recorded destination `write_knowledge_batch` — batch "
        "creation is ONE call, not its own verb, and the recorded fold is the batch writer "
        "rather than a per-point `create_entity` loop."
    ),
    "create_or_update_point": (
        "#4282 (2026-10-27): DELETED. Recorded destination `create_entity`; upsert "
        "semantics are a flag on the create target, not a second verb. ⚠️ UNHINTED even "
        "though that destination has a `def`: the owner's own ledger row for this name "
        "points at `create_point` instead, so the two records disagree — see "
        "`UNHINTED_LIVE_DESTINATIONS`."
    ),
    "list_drafts": (
        "#4282 (2026-10-27): DELETED. Recorded destination `list_knowledge` — the draft "
        "QUEUE is the filter `list_knowledge(kind=…, status=…)`, while the lifecycle status "
        "itself is a FIELD on `update_knowledge` (the C6 fold record's finding, kept in "
        "`docs/product/canonical-sdk-methods.md` after the name left). `update_knowledge` "
        "is the field's host, not a second draft verb."
    ),
    "operator_action": (
        "#4282 (2026-10-27): DELETED. Recorded destination `adjust_relationship`; the "
        "operator-action vocabulary was never in beta."
    ),
    "record_calibration": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: the ledger's delete "
        "verdict is UNSTAKED, and the W13 group rename (`stabilize_beliefs` → "
        "`refresh_confidence`) is a GROUP target, not a per-name destination. The name was "
        "UNSTAKEABLE rather than merely unbacked: recomputing confidence is not recording "
        "that a calibration milestone passed, so naming `refresh_confidence` here would be "
        "a judgement masquerading as a record."
    ),
    "apikey_list": (
        "#4282 (2026-10-27): DELETED. Recorded destination `list_keys`, which has no `def` "
        "yet, so no hint is emitted: the approved key surface is "
        "`create_key`/`list_keys`/`revoke_key`."
    ),
    "membership_list": (
        "#4282 (2026-10-27): DELETED. Recorded destination `list_members`, which has no "
        "`def` yet, so no hint is emitted: the member surface is "
        "`add_member`/`list_members`/`remove_member`."
    ),
    "membership_delete": (
        "#4282 (2026-10-27): DELETED. Recorded destination `remove_member`, which has no "
        "`def` yet, so no hint is emitted; the member surface replaces the triplet "
        "wholesale."
    ),
    "membership_update_role": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: a role change is not in "
        "the approved member surface."
    ),
    "invitation_create": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: invitations are not in "
        "the beta surface."
    ),
    "invitation_accept": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: invitations are not in "
        "the beta surface."
    ),
    "invitation_get_by_token": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: invitations are not in "
        "the beta surface."
    ),
    "cleanup_expired_invitations": (
        "#4282 (2026-10-27): DELETED. A scheduled maintenance sweep, not a customer "
        "verb; **no** recorded destination."
    ),
    "migrate_orgs_to_registry": (
        "#4282 (2026-10-27): DELETED. A one-off platform migration, not a customer "
        "verb; **no** recorded destination."
    ),
    "org_update": (
        "#4282 (2026-10-27): DELETED. Tenancy administration is a console operation in "
        "the approved tenancy block; **no** recorded destination."
    ),
    "org_delete": (
        "#4282 (2026-10-27): DELETED. Account closure is a console operation in the "
        "approved tenancy block; **no** recorded destination."
    ),
    # ── #4282 Phase 2 — the SOURCES / MINE / SESSION containers (#11609) ──────
    #
    # These five are the LIVE off-surface methods the containers REPLACE. They are
    # REMOVALS rather than renames because `RETIRED_SDK_APPROVALS` demands an
    # owner-approved region of `beta-sdk-surface.md` that names BOTH the retired
    # name and its numbered successor, and for these pairs that region does not
    # exist. The collapse IS recorded elsewhere and is not in doubt — the approved
    # menu (`canonical-mcp-tools.md`) and the non-owner inventory
    # (`canonical-sdk-methods.md`) both name the destination — so this is a DOC GAP
    # being recorded honestly, not a judgement that no successor exists. Writing a
    # region that the owner never wrote would be inventing an approval, which is the
    # one thing this map may never do.
    "create_source": (
        "#4282 Phase 2 (#11609): RETIRED from the public surface. The approved row 14 "
        "`register_source` carries the same signature and delegates to the same body "
        "(`_create_source`), so no capability moved. NO HINT, and the reason is the doc:"
        " `beta-sdk-surface.md` has no region naming both names — its only "
        "`create_source` row is `create_source` ↛ `create_entity` (the row that says a "
        "source is NOT an entity), which authorises no replacement at all."
    ),
    "index_directory": (
        "#4282 Phase 2 (#11609): RETIRED from the public surface; the unified walker is "
        "now `index_sources_from_directory` (row 15), which this delegates to. NO HINT: "
        "every beta region that names `index_directory` names it as a DESTINATION "
        "(`index_sessions`/`ingest_corpus` → `index_directory`), never as a source with a "
        "numbered successor — so the region that would authorise a hint is the reverse "
        "direction of this departure."
    ),
    "index_sessions": (
        "#4282 Phase 2 (#11609): RETIRED from the public surface; its body was a single "
        "delegation to `ingest_corpus` (`eventKind='AgentSession'`) — the "
        "`index_directory` successor its DEPRECATED docstring named was never the "
        "mechanism — and the operation is now spelled "
        "`index_sources_from_directory(file_type='agent_session')`. NO HINT: beta's row "
        "for this name is in its *Removed* table (`Body is a single delegation to another "
        "public method, no added contract`), and the one region naming both it and a "
        "successor names `index_directory` — which is ITSELF retired by this change, so a "
        "hint would point a caller at a dead name."
    ),
    "mine_corpus": (
        "#4282 Phase 2 (#11609): RETIRED from the public surface; `mine_knowledge_from_directory` "
        "(row 17) is the same operation under its approved name. NO HINT: the beta region "
        "that records the destination ALSO names `mine_knowledge_from_session` (\"it is the "
        "batch form of …\"), so it names two approved targets — and picking one is the "
        "owner's choice, not this map's."
    ),
    "checkpoint": (
        "#4282 Phase 2 (#11609): RETIRED from the public surface; `mine_knowledge_from_session` "
        "(row 16) files the same batch under its approved name, as its `items=` arm. NO HINT: "
        "the beta region for the journal family retires it \"into `capture_knowledge`\" (the "
        "MCP row, not a numbered SDK target) and names `create_entity` and `list_knowledge` "
        "as its siblings' destinations, so it authorises no single numbered replacement."
    ),
    # ── #12901 wave 1 (2026-11-15) — the retirement wave ─────────────────
    #
    # The MEMBERSHIP RULE for this block, so a later reader can re-derive it rather
    # than trust it: a name qualifies only when it is (a) NOT on `tortoise/__all__.py`
    # (the owner's approved list), (b) bound by NO entry in
    # `tortoise/tool_registry.py` — live OR retired, since a retired tool still answers
    # through the #3883 shim — and (c) called by NO production code in `tortoise/`.
    # (c) is the one that does the work, and it is MEASURED, not asserted: a receiver-
    # aware AST walk over `tortoise/`, `tests/` and `tools/` for `sdk.<name>` /
    # `self.<name>` / a bare attribute reference (`asyncio.to_thread(sdk.<name>, …)`
    # is such a reference, and a `(`-anchored grep misses it) / `getattr(sdk, "<name>")`.
    # ⛔ AND THE SCAN MUST MATCH EVERY RECEIVER SHAPE. Measured the hard way: a scan keyed
    # on `sdk.`/`self.` MISSED `TortoiseSDK().calibration_passed()` — its receiver is a
    # CONSTRUCTOR CALL, not a name — i.e. it missed the one production caller in this
    # block. Match on the ATTRIBUTE, then judge the receiver, or a real caller hides.
    #
    # WHY (c) IS A CONDITION AND NOT A COURTESY. The owner's #3836 (c) ruling retires a
    # name by FAILING and naming its replacement — no alias layer, no warning shim — and
    # its stated reason is that with zero users there is no caller to protect. A
    # deprecation window is what protects callers (PEP 387: an incompatible change only
    # after a documented deprecation; SemVer 2.0.0 rule 8: a MAJOR bump). So the ruling is
    # only legitimate over names that HAVE no EXTERNAL caller. That is the gate.
    #
    # ⚠️ ONE REFINEMENT, and it is the repo's own precedent rather than an exception made
    # here: an INTERNAL caller does not disqualify a name if the body can go PRIVATE and
    # that caller re-points at it. That is exactly the `capture_session` -> 
    # `_capture_session` shape already recorded above, and it is the shape
    # `calibration_passed` took (one caller in `tortoise/gates.py`, re-pointed at
    # `_calibration_marker_recorded`). What the ruling forbids is leaving a caller BROKEN;
    # it does not forbid closing a door whose only key-holder is our own code.
    #
    # DEFERRED, with the reason, so the next wave does not re-derive it: `recall_gaps` and
    # `recall_subgraph` have zero production callers BUT are invoked dynamically by name
    # from `tests/test_time_aware_sdk_2520.py`'s `_RE_OWNING_LANES` (a string tuple, so a
    # receiver-based scan cannot see it), and both wrap live `ranking` machinery
    # (`GapsRanker`, `SubgraphExpander`). `test_guard` has zero production callers but the
    # beta ledger's `r6_test_guard` region rules it **Kept and relocated** — it guards the
    # production-wipe incident, so the CODE must survive and move to test infrastructure
    # rather than be deleted. It needs its own wave.
    "backfill_v25": (
        "#12901 wave 1 (2026-11-15): DELETED. A one-shot ONTOLOGY v2.5 migration "
        "(status/pointKind backfill) written against a schema several versions old, "
        "with zero callers in `tortoise/`, `tests/` or `tools/`. The beta ledger's "
        "`w8_backfill` region rules the class `One-shot migrations. Run once, then "
        "dead code carrying a public promise.`, and the rename table's own disposition "
        "is ARCHIVE -> DISCARDED. **No** recorded destination: nothing migrates to it, "
        "and it migrates to nothing."
    ),
    "list_relations": (
        "#12901 wave 1 (2026-11-15): DELETED. It listed the pack-declared relation "
        "declarations and had no caller ON THE SDK — the same-named methods elsewhere "
        "(`pack_registry.list_relations`, reached as `registry.list_relations()`) are "
        "DIFFERENT objects and survive untouched. Recorded destination `graph_overview` — "
        "the CANONICAL `r6_canon` region (row R6) — which has no `def` today, so no hint is "
        "emitted, because a hint must name something a caller can actually call. The beta "
        "tables never recorded a target for this name, so the canonical row is the whole of "
        "the destination record. "
        "`TortoiseSDK._resolve_traversal_path` still reads the registry call."
    ),
    "quarantine_batch": (
        "#12901 wave 1 (2026-11-15): DELETED. A thin delegate to "
        "`tortoise.mining.quarantine_batch`, which STAYS — batch lifecycle state lives "
        "on :Batch marker nodes, so the capability was never the delegate. It had no "
        "PRODUCTION caller; its only call sites were tests, and they were re-pointed at "
        "`tortoise.mining.quarantine_batch`, which is exactly what the delegate called. "
        "Recorded destination `update_knowledge` (quarantine is a FIELD on the partial "
        "update, not a verb) — the beta `w11_lifecycle` region's column. The recorded "
        "destination lives in "
        "`WAVE1_DELETION_DESTINATIONS` (NOT in `SURPLUS_DELETION_DESTINATIONS`, which is "
        "the CLOSED #4282 block). It is "
        "UNHINTED even though `update_knowledge` is live: the approving region names "
        "TWO targets (`update_knowledge` and `list_knowledge`), so the derived-ordinal "
        "arm refuses to pick a winner between them, and a hint would record a judgement "
        "no source makes. That divergence is DECLARED in "
        "`UNHINTED_LIVE_DESTINATIONS`, and the pin reds on a divergence that is neither "
        "hinted nor declared."
    ),
    "calibration_passed": (
        "#12901 wave 1 (2026-11-15): DELETED FROM THE PUBLIC SURFACE. The Gate B "
        "calibration-milestone reader. ⚠️ It had ONE production caller — "
        "`tortoise/gates.py`'s CLI read `TortoiseSDK().calibration_passed()` inside a "
        "try/except — so this is NOT a zero-caller deletion and the departure is the "
        "`capture_session` -> `_capture_session` shape instead: the body went PRIVATE as "
        "`_calibration_marker_recorded`, gates.py reads that, and the unapproved PUBLIC "
        "door closes with the capability intact. (A bare `sdk.<name>` scan MISSES this "
        "caller — its receiver is a constructor call, not a name.) Recorded destination "
        "`check_confidence` — the beta `r3` region's column — which has no `def` today, so "
        "no hint is emitted. `_CALIBRATION_MARKER_KEY` deliberately STAYS on the class: the "
        "projection's config-reset preservation reads it too."
    ),
    "membership_get": (
        "#12901 wave 1 (2026-11-15): DELETED. It read one membership row by id and had "
        "no PRODUCTION caller — the only call sites were two tests "
        "(`test_membership_get_returns_membership` / `..._returns_none_for_missing`), "
        "which were DELETED with it because the empty branch they exercised went too. "
        "The console reads memberships through the approved "
        "`list_members` surface instead. The beta `n1_console` region rules this class "
        "`Console plumbing` and puts no tenancy administration on the SDK, so there is "
        "**no** recorded destination to name."
    ),

    # ── #12901 wave 2 (2026-11-16) — the CONTROL PLANE leaves the SDK ─────────
    #
    # The MEMBERSHIP RULE, so a later reader re-derives it rather than trusting it: a
    # name qualifies when it is (a) NOT on `tortoise/__all__.py`, (b) bound by NO entry
    # in `tortoise/tool_registry.py` — live OR retired, since a retired tool still answers
    # through the #3883 shim — and (c) a member of the canonical inventory's control-plane
    # partition (`N1`–`N6`). (c) is what makes this wave a PARTITION rather than a
    # selection: the whole console/hosted surface leaves together, and the five N-members
    # that stay do so by the same rule (they are approved target rows —
    # `check_connection`, `get_organisation_account`, `list_memory_graphs`,
    # `restore_memory_graph`, `update_memory_graph` — or bound by a live MCP tool:
    # `org_create`).
    #
    # ⚠️ NONE of these is a zero-caller deletion: each body is called from
    # `tortoise/hosted_api.py`, `tortoise/__main__.py`, `tortoise/mcp_auth.py` or one
    # `graph-scripts/` entry point, so each took the `capture_session` ->
    # `_capture_session` shape — the body goes PRIVATE, that caller re-points, and the
    # unapproved PUBLIC door closes with the capability intact. Closing a door whose only
    # key-holder is our own code is not what #3836 (c) forbids; leaving a caller BROKEN is.
    #
    # Each name's destination cell is in `WAVE2_DELETION_DESTINATIONS` above (7 record a
    # destination, 9 record none), and each reason states it once in the claim sentence
    # `Recorded destination \`x\`` the pin parses. The destinations are the approved
    # TENANCY rows — `create_key` (34), `list_keys` (35), `revoke_key` (36), `add_member`
    # (37), `get_organisation_account` (27), `delete_memory_graph` (32) — most of which
    # have no `def` yet, so no hint is emitted: a hint must name something a caller can
    # CALL.
    "org_get": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_org_get`. Read one org row from the "
        "control-plane registry; `tortoise/hosted_api.py` and the SDK's own control-plane "
        "methods are its callers. Recorded destination "
        "`get_organisation_account` — the approved tenancy row that answers the customer's "
        "question, which `org_get` never did (it is the console's internal read and returns "
        "raw registry properties). ⚠️ UNHINTED although the destination is LIVE: the beta "
        "ledger never names `org_get`, so the region a #3836 (c) hint requires does not "
        "exist — see `UNHINTED_LIVE_DESTINATIONS`."
    ),
    "graph_delete": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_graph_delete`. Soft-deletes a Graph "
        "node (tombstone); the hosted graph routes are its only callers. Recorded "
        "destination `delete_memory_graph` (row 32). It is recorded as a REMOVAL rather "
        "than a rename because the beta row authorising the collapse names a RANGE — "
        "`graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` → rows 30–33 — "
        "and this map may not pick row 32 on the owner's behalf."
    ),
    "trash_graphs": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_trash_graphs`. Lists an org's "
        "tombstoned custom graphs (the trash view behind graph restore), read by the "
        "hosted graph routes. **No** recorded destination: the beta ledger's maintenance "
        "row — `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, "
        "`sweep_invite_ghost_memberships` | **Our maintenance.** Never product surface — is "
        "a verdict that it never belongs on the surface, with nothing to migrate to."
    ),
    "graph_key_ids": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_graph_key_ids`. The APIKey node ids "
        "bound to a graph — the delete-cascade source. Recorded destination "
        "`list_keys`: the beta row is the 'Console diagnostics' line that folds both key "
        "counters into `list_keys`. `list_keys` (row 35) has no `def` yet, so no hint is "
        "emitted."
    ),
    "graph_active_key_count": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_graph_active_key_count`. The "
        "non-revoked key count per graph for the hosted graphs list. Recorded destination "
        "`list_keys` — the same beta 'Console diagnostics' row as `graph_key_ids`, and the "
        "same reason there is no hint (`list_keys` has no `def`)."
    ),
    "membership_create": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_membership_create`. Adds a user to "
        "an org with a role; the hosted invite/organisation routes and the SDK's own org "
        "bootstrap call it. Recorded destination `add_member` (row 37), whose `def` does "
        "not exist yet — so no hint. This closes the membership family that the #4282 "
        "surplus block half-retired (`membership_list`, `membership_delete`, "
        "`membership_update_role`) and wave 1 finished (`membership_get`)."
    ),
    "apikey_create": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_apikey_create`. Mints a control-plane "
        "API key; the hosted key routes and the `apikey` CLI are its callers. Recorded "
        "destination `create_key` (row 34) — 'the credential carries the tenant' — which "
        "has no `def` yet, so no hint. It completes the keys family with `apikey_revoke`, "
        "`apikey_verify` and the already-deleted `apikey_list`."
    ),
    "apikey_revoke": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_apikey_revoke`. Revokes a key (soft "
        "delete); the hosted graph-delete cascade, the offline-key sweep and the key "
        "routes call it. Recorded destination `revoke_key` (row 36), no `def` yet — no "
        "hint."
    ),
    "apikey_verify": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_apikey_verify`. Verifies a presented "
        "key against the stored hashes — the MCP auth path and the hosted routes read it. "
        "**No** recorded destination: the beta ledger files it under the 'Console "
        "plumbing' row whose verdict is that the builder's account surface is a console "
        "operation and not in the SDK at all."
    ),
    "invitation_list": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_invitation_list`. Lists an org's "
        "pending invitations for the console. **No** recorded destination: the beta ledger "
        "rules `invitation_*` (six names) as 'The invite **UX** belongs to the console, "
        "where a human clicks it' — the console owns that surface, so nothing migrates "
        "into the SDK."
    ),
    "invitation_get_by_id": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_invitation_get_by_id`. Reads one "
        "invitation row; the hosted revoke route and `_invitation_revoke` are its "
        "callers. **No** recorded destination — the same `invitation_*` console row as "
        "`invitation_list`."
    ),
    "invitation_revoke": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_invitation_revoke`. Revokes a pending "
        "invitation from the console. **No** recorded destination — the same "
        "`invitation_*` console row."
    ),
    "sweep_invite_ghost_memberships": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_sweep_invite_ghost_memberships`. The "
        "scheduled repair that removes membership rows whose invite never completed; the "
        "hosted scheduler and one `graph-scripts/` entry point call it. **No** recorded "
        "destination: the beta maintenance row ('**Our maintenance.** Never product "
        "surface.') covers it, and that row names nothing to migrate to."
    ),
    "signup_token_lookup": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_signup_token_lookup`. Resolves an "
        "agent self-signup token; `_signup_token_recover` and the hosted signup routes "
        "call it. **No** recorded destination: the beta ledger rules the three "
        "`signup_token_*` names as 'Operator-side agent self-signup — our provisioning, "
        "not product surface.'"
    ),
    "signup_token_recover": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_signup_token_recover`. Re-mints the "
        "bootstrap key for a signup token (the process-local lock above it stays for the "
        "same reason); the hosted signup route calls it. **No** recorded destination — the "
        "same `signup_token_*` provisioning row."
    ),
    "signup_token_revoke": (
        "#12901 wave 2 (2026-11-16): PRIVATISED as `_signup_token_revoke`. Revokes a "
        "signup token from operator tooling. **No** recorded destination — the same "
        "`signup_token_*` provisioning row."
    ),
}

#: retired name -> the owner-approved region of `docs/product/beta-sdk-surface.md`
#: that authorises the retirement, quoted VERBATIM (whitespace-normalised).
#:
#: Why a separate cell and not a parse of one fixed sentence shape: the owner's
#: ledger states an approval in three different shapes — a single-name row
#: (`| `events_poll` | → row 11 `poll_events`. |`), a GROUP row
#: (`| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | →
#: rows 30–33 `*_memory_graph*`. |`), and a discarded-row entry that names the
#: destination inline. A parser that accepts only the first shape can never
#: authorise the other 112 renames the same ledger records, so it would block
#: phase 2 permanently. The quote makes each entry's authority explicit and
#: checkable instead of implied by a shape.
#:
#: ⚠️ The residual, stated rather than papered over: a group row names a RANGE
#: of approved targets, so this map's job is to prove the replacement is one of
#: those rows and that the old name is in the row authorising it. WHICH row of
#: the range a given old name collapses into is the author's design decision —
#: no machine can decide that `graph_list` is row 31 rather than row 30 — so the
#: decision is RECORDED in :data:`RETIRED_SDK_APPROVAL_ROWS` and pinned by
#: `tests/test_sdk_retirement.py`, instead of being re-decided at runtime. That
#: cell is not decoration: a range alone would admit a DESTRUCTIVE mis-target
#: (`"graph_list": "delete_memory_graph"` sits inside rows 30–33), whose hint
#: would send every caller to the eraser with a green run.
RETIRED_SDK_APPROVALS: dict[str, str] = {
    "events_poll": "| `events_poll` | → row 11 `poll_events`. |",
    "restore_point_at": (
        "| `restore_point_at` | → row 7 **`get_historical_knowledge`**. A **read**, not a "
        "write — it returns the version of a claim valid on a date and mutates nothing. |"
    ),
    "graph_list": (
        "| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 "
        "`*_memory_graph*`. |"
    ),
    "graph_restore": (
        "| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 "
        "`*_memory_graph*`. |"
    ),
    "graph_set_name": (
        "| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 "
        "`*_memory_graph*`. |"
    ),
    "graph_set_recording": (
        "| ~~`graph_set_recording`~~ (SDK method) | 1 | **Discarded as an SDK method, KEPT "
        "as an MCP tool.** It is a per-field setter, the same shape as "
        "`set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that "
        "fields go on create plus one partial update. The override therefore folds into "
        "**`update_memory_graph`** (row 30) — while the **MCP tool** "
        "`graph_set_recording` survives, because it is an agent's only in-MCP recovery "
        "from the capture 409. |"
    ),
    # #4282 (2026-10-27) — the provenance read. The owner's ledger authorises the collapse
    # into `get_entity` for the "narrow readers" group, and states NO target-row ordinal;
    # the ordinal is derived from the single target the region names (see the ordinal cell).
    "get_provenance_chain": (
        "| narrow readers (`get_session`, `get_events`, `get_owned_entities`, "
        "`get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely "
        "different shape is returned. |"
    ),
    # #4282 Phase 2 — one region covers the pair: the beta row names the SETTER's
    # target first and the reader's second, so the approved destination set this
    # region authorises is exactly {`manage_source_trust`} (the reader is not a
    # numbered target).
    "assess_source": (
        "| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → "
        "`manage_source_trust` for the setter; reads via `list_sources`. |"
    ),
    "set_source_tier": (
        "| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → "
        "`manage_source_trust` for the setter; reads via `list_sources`. |"
    ),
    "index_file": (
        "| `ingest_corpus`, `index_file`, `session_index_health` | 3 | → "
        "`index_sources_from_directory`. |"
    ),
    # The row states the ordinal itself (`row 16`), so no author choice is being
    # made here — `test_each_retirement_records_the_row_it_chose` derives it.
    "capture_session": (
        "| `capture_session` / `commit_session` | → row 16 `mine_knowledge_from_session`, "
        "one method. The backend is the target graph's configuration. |"
    ),
    "commit_session": (
        "| `capture_session` / `commit_session` | → row 16 `mine_knowledge_from_session`, "
        "one method. The backend is the target graph's configuration. |"
    ),

    # #11609 lane B (#4282 Phase 2) — the WRITE/MUTATE containers. Each quote is the
    # row's own text, copied whole from the doc's "Removed" table.
    "supersede": (
        "| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also "
        "**disagree** — `supersede_point` carries a `valid_from` the other silently "
        "drops. |"
    ),
    "update_entity": "| `update_point`, `update_entity` | 2 | → `update_knowledge`. |",
    "file_decision": (
        "| `file_decision` | → rows 20/21 **`write_question`** + "
        "**`record_decision`**. It was filing a *question* and calling it a "
        "decision. |"
    ),
    "file_human_approval": "| `file_human_approval` | 1 | → `record_decision`. |",
}

#: retired name -> the ONE target-row ordinal its replacement was chosen from.
#:
#: The approvals above authorise a REGION (a group row names rows 30–33). The CHOICE
#: inside that region is an author decision, and this cell is where that decision is
#: RECORDED rather than re-decided at runtime. It is a gate, not documentation: with
#: only the region, `"graph_list": "delete_memory_graph"` would satisfy every check and
#: send each `graph_list` caller to the eraser — so `tests/test_sdk_retirement.py`
#: resolves this ordinal against `beta-sdk-surface.md` and requires it to BE the
#: declared replacement, and requires the ordinal to lie inside the approved region.
RETIRED_SDK_APPROVAL_ROWS: dict[str, int] = {
    "events_poll": 11,
    "restore_point_at": 7,
    "graph_list": 31,
    "graph_restore": 33,
    "graph_set_name": 30,
    "graph_set_recording": 30,
    # Derived, not chosen: the approving region names exactly one approved target
    # (`get_entity`), and that name occupies exactly one numbered target row.
    "get_provenance_chain": 6,
    "assess_source": 18,
    "set_source_tier": 18,
    "index_file": 15,
    "capture_session": 16,
    "commit_session": 16,

    # #11609 lane B. `supersede_knowledge` is row 23, `update_knowledge` is row 22,
    # `write_question` is row 20 and `record_decision` is row 21 — read from the
    # beta doc's target table, never typed from memory (the gate re-derives them).
    #
    # `file_decision`'s approving row is "rows 20/21 **`write_question`** +
    # **`record_decision`**": the doc says that question/decision split explicitly,
    # and `write_question` is the arm this name's BODY became (it files the
    # question), so row 20 is the recorded choice rather than a coin flip.
    "supersede": 23,
    "update_entity": 22,
    "file_decision": 20,
    "file_human_approval": 21,
}


def retirement_hint(name: str) -> str | None:
    """The migration hint for a retired name, or ``None`` when it was never public.

    Kept as a function so the one place that owns the wording is testable without
    constructing an SDK (the caller is ``TortoiseSDK.__getattr__``).
    """
    replacement = RETIRED_SDK_METHODS.get(name)
    if replacement is None:
        return None
    return (
        f"TortoiseSDK.{name} was retired (#4282) — use TortoiseSDK.{replacement} "
        f"instead. There is no alias layer: the old name does not resolve "
        f"(owner ruling #3836 (c))."
    )
