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
``tools/sdk_rename_table.py`` is the migration map: every name on the LIVE surface (112
at this head — re-derive with ``tools/sdk_surface.py --check``; the numeral moves with
every wave) and its approved destination, most of whose
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
:data:`SURPLUS_DELETION_DESTINATIONS` (the 26-name `#4282` block) and
:data:`WAVE1_DELETION_DESTINATIONS` (the `#12901` wave-1 block) are both transcribed from
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
    # ── #11609 Phase 2 (read/query family) ────────────────────────────────────
    # Every name below left the surface in the SAME PR that landed the container
    # covering it, which is what makes each hint true rather than aspirational: a
    # retired name FAILS and names a replacement that RESOLVES (#3836 (c)).
    # `stale_points` is deliberately NOT here: the owner's narrow-alias row names
    # `stale` (the MCP tool), not this method name, so no approving region of
    # `docs/product/beta-sdk-surface.md` authorises ITS retirement — it stays
    # public and is reached through `graph_overview(section='stale')`.
    "taxonomy": "graph_overview",
    "list_pointkinds": "graph_overview",
    "list_tags": "graph_overview",
    "status": "graph_overview",
    "list_topics": "graph_overview",
    "list_namespaces": "graph_overview",
    "validate_domain": "graph_overview",
    "list_sources": "list_knowledge",
    "diary_read": "list_knowledge",
    "search_sessions": "search_knowledge",
    "suggest_entry_points": "search_knowledge",
    "provenance": "check_confidence",
    "expand_relationships": "explore_connections",
    "list_dedup_candidates": "review_link_candidates",
    # #11609 Phase 2 completes two departures that had nowhere to point until now:
    # both left the surface in #4282's surplus block with `explore_connections`
    # recorded as their destination, and that destination had NO `def` — so they
    # carried a record and no hint. The container lands here, so the hint is free
    # and #3836 (c)'s "fail and name the replacement" finally holds for them.
    "traverse": "explore_connections",
    "get_org_structure": "explore_connections",
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
    # #11609 Phase 2: two more recorded destinations became LIVE when the read
    # containers landed, and neither can be awarded a hint by the approving-region
    # parser. Declared here rather than quietly skipped (#3836 (c)).
    # #12901 wave 1: two REMOVED names whose recorded destination went LIVE with #11609
    # Phase 2's containers. A removed name cannot emit a hint at all —
    # `test_every_removal_is_recorded_and_documented` asserts none does, because a hint
    # names an owner-approved replacement and these have none (they were recorded against
    # the 40-method TARGET, not approved as its replacements). So the live destination is
    # DECLARED here rather than left as an omission the census cannot see.
    "calibration_passed": (
        "Recorded destination `check_confidence` is LIVE since #11609 Phase 2, and this "
        "name was REMOVED in #12901 wave 1 (its body went private as "
        "`_calibration_marker_recorded()`), so it can carry no retirement hint: a hint "
        "would name a replacement the owner never approved for it. The capability it "
        "reads is reachable at `check_confidence`; the ledger row is the 40-method "
        "target's, which is not an approval of the rename."
    ),
    "list_relations": (
        "Recorded destination `graph_overview` is LIVE since #11609 Phase 2, and this "
        "name was REMOVED in #12901 wave 1 with no approved replacement — its "
        "`callers: engine` cell was a documented receiver-blind NAME COLLISION "
        "(`registry.list_relations()`, a different object), not a call on the SDK, so no "
        "approving region authorises a rename and a hint cannot be minted. The "
        "declarations are served by `graph_overview`."
    ),
    "retrieval_legs": (
        "Recorded destination `check_confidence` is LIVE since #11609 Phase 2, but no "
        "region of `docs/product/beta-sdk-surface.md` names `retrieval_legs` — the "
        "owner's ledger row says `recall_legs` — so no approving region authorises a "
        "rename and a hint cannot be minted. The capability is reachable at "
        "`check_confidence`; reconciling the two spellings is #4282 review work."
    ),
    "list_drafts": (
        "Recorded destination `list_knowledge` is LIVE since #11609 Phase 2, but the "
        "ledger row that names this method (`promote_point`, `set_point_baseline`, "
        "`list_drafts`, `quarantine_batch`) names TWO targets and no row ordinal — "
        "`update_knowledge` for the lifecycle FIELD and `list_knowledge` for the draft "
        "QUEUE — so the parser cannot award a single approved replacement. The queue is "
        "`list_knowledge(kind=…, status=…)`; choosing between the two records is the "
        "owner's, not a build step."
    ),
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
    # The other twelve recorded destinations are the Phase-2 targets
    # `mine_knowledge_from_session`, `check_confidence`, `explore_connections`,
    # `index_sources_from_directory`, `write_knowledge_batch`, `list_knowledge`, `list_keys`,
    # `list_members`, `remove_member` and `adjust_relationship` — no `def` today, so a hint
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
    "capture_session": (
        "#4282 (2026-10-27): RETIRED from the PUBLIC surface. Recorded destination "
        "`mine_knowledge_from_session`, an approved target with no `def` today, so there "
        "is no surface name to point a hint at. The capability is not lost: the body "
        "lives on as the PRIVATE `TortoiseSDK._capture_session` (surplus-and-referenced, "
        "so it went private rather than out — it drives the journal-first capture "
        "provenance stamp and the aboutObject pass), and the hosted lane keeps its own "
        "`_capture_session_impl`."
    ),
    "commit_session": (
        "#4282 (2026-10-27): DELETED. Recorded destination "
        "`mine_knowledge_from_session` — approved, not yet implemented, so no hint."
    ),
    "retrieval_legs": (
        "#4282 (2026-10-27): DELETED. Recorded destination `check_confidence`; the "
        "retrieval-leg breakdown is not a customer verb."
    ),
    # `traverse` moved OUT of this table in #11609 Phase 2: `explore_connections`
    # now HAS a `def`, so the departure is a #3836 (c) retirement with a real hint
    # and it lives in RETIRED_SDK_METHODS. A departure has ONE shape.
    # `get_org_structure` likewise moved to RETIRED_SDK_METHODS in #11609 Phase 2.
    "backfill_about_entities": (
        "#4282 (2026-10-27): DELETED. **No** recorded destination: the ledger rules the "
        "four backfills (this, `backfill_sources`, `backfill_v25`, `reconcile_sessions`) "
        "`One-shot migrations. Run once, then dead code carrying a public promise`, and "
        "the inventory's destination column is `—`. This is a JUDGEMENT (delete, ship "
        "nothing) rather than a mapping: back-filling edges over history is not the "
        "indexer's question, so no successor is named."
    ),
    "index_file": (
        "#4282 (2026-10-27): DELETED. Recorded destination "
        "`index_sources_from_directory`; the single-file form is the directory form of "
        "one path."
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
#: The approving regions shared by more than one #11609 retirement — each quoted
#: VERBATIM from `docs/product/beta-sdk-surface.md`, one cell so a group of names that
#: the owner's ledger retires in ONE row does not carry N hand-typed copies of it.
_GRAPH_OVERVIEW_ALIAS_ROW = (
    "| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, "
    "`list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, "
    "`check_structure`, `list_topics` | **Deleted, not folded.** The approved list "
    "contains the container and not the aliases; shipping both is the merge failing at "
    "its own goal. |"
)
_VALIDATE_DOMAIN_ROW = (
    "| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, "
    "`dream_health_state` | ~5 | → `graph_overview` where they are orientation. The "
    "diagnostics are the held question above. |"
)
_LIST_SOURCES_ROW = (
    "| `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool "
    "and a CLI command (`tortoise/__main__.py`), and it is covered by "
    "`tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds "
    "into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays "
    "first-class and gains the credibility tier; it no longer needs its own method. |"
)
#: The journal paragraph (owner ruling **#4667**) states each of the three names retires
#: into a NAMED replacement; this is the `diary_read` clause of that sentence. It names no
#: row NUMBER, so the derived arm resolves it against the single approved target it names.
_DIARY_READ_CLAUSE = "`diary_read` into `list_knowledge`"
_SEARCH_GROUP_ROW = (
    "| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, "
    "`annotate_ask_hits` | 5 | → `search_knowledge`. |"
)
_EXPAND_RELATIONSHIPS_ROW = (
    "| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → "
    "`explore_connections`. |"
)
_DEDUP_ROW = (
    "| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → "
    "`review_link_candidates`. |"
)


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
    # ── #11609 Phase 2 (read/query family) ────────────────────────────────────
    # ONE region per retirement, quoted from the owner's ledger. The seven
    # `graph_overview` aliases share the ledger's own group row: it names no target
    # row NUMBER, so the approved set is the single target it names — which is why
    # `_approved_replacements`' derived-ordinal arm reads them, and why the ordinal
    # cell below records row 9 for each.
    "taxonomy": _GRAPH_OVERVIEW_ALIAS_ROW,
    "list_pointkinds": _GRAPH_OVERVIEW_ALIAS_ROW,
    "list_tags": _GRAPH_OVERVIEW_ALIAS_ROW,
    "status": _GRAPH_OVERVIEW_ALIAS_ROW,
    "list_topics": _GRAPH_OVERVIEW_ALIAS_ROW,
    "list_namespaces": _GRAPH_OVERVIEW_ALIAS_ROW,
    "validate_domain": _VALIDATE_DOMAIN_ROW,
    "list_sources": _LIST_SOURCES_ROW,
    "diary_read": _DIARY_READ_CLAUSE,
    # `expand_relationships`' region names one target and no ordinal, so it derives.
    "search_sessions": _SEARCH_GROUP_ROW,
    "suggest_entry_points": _SEARCH_GROUP_ROW,
    # ⚠️ The provenance/confidence region has TWO clauses and names a target in each
    # (`check_confidence` for the confidence view, `poll_events` for the timeline). The
    # quote below is the CONFIDENCE clause only, and the clause it stops before is
    # stated here rather than dropped: `poll_events` is the TIMELINE destination, and
    # `provenance` is confidence context, which is the arm `check_confidence` answers.
    # Both owner records agree on that (this ledger's clause and
    # `docs/product/bridge-table.md` §D1's citation for the same row), so nothing is
    # edited to agree with the destination — the destination is what both record.
    "provenance": (
        "| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | "
        "→ `check_confidence` where they are confidence context;"
    ),
    "expand_relationships": _EXPAND_RELATIONSHIPS_ROW,
    "list_dedup_candidates": _DEDUP_ROW,
    # Same region: it retires `traverse` and `get_org_structure` into the same target
    # in the same row, which is why the three share one quoted region.
    "traverse": _EXPAND_RELATIONSHIPS_ROW,
    "get_org_structure": _EXPAND_RELATIONSHIPS_ROW,
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
    # ── #11609 Phase 2 — the read/query family ───────────────────────────────
    # Every ordinal below is DERIVED (the approving region names exactly one approved
    # target), except `provenance`, whose region names two clauses and therefore two
    # targets — there the clause recorded in :data:`RETIRED_SDK_APPROVALS` IS the choice
    # (the confidence clause), and row 5 is the row that clause names.
    "taxonomy": 9,
    "list_pointkinds": 9,
    "list_tags": 9,
    "status": 9,
    "list_topics": 9,
    "list_namespaces": 9,
    "validate_domain": 9,
    "list_sources": 4,
    "diary_read": 4,
    "search_sessions": 3,
    "suggest_entry_points": 3,
    "provenance": 5,
    "expand_relationships": 8,
    "list_dedup_candidates": 10,
    "traverse": 8,
    "get_org_structure": 8,
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
