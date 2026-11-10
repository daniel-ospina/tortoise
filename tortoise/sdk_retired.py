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
``tools/sdk_rename_table.py`` is the migration map: all 124 current names and their
approved destinations, most of whose renames have **not** landed yet. Two reasons this
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
for the 26-name surplus block it holds the RECORDED DESTINATION too:
:data:`SURPLUS_DELETION_DESTINATIONS` is transcribed from the inventory's own column and
every reason below is pinned to it, because a hand-typed destination is how TEN of them
drifted off the record (#10226 review, 2026-11-10).
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

#: The surplus names whose recorded destination HAS a `def` on `TortoiseSDK` today but which
#: must stay UNHINTED, with the reason. A hint exists to be FOLLOWED (#3836 (c)), so a live
#: destination should always produce one; this cell is the exception, and it is a DECLARATION
#: rather than an omission — `tests/test_sdk_retirement.py` reds both on a live destination
#: that is neither hinted nor declared here, and on an entry here that has stopped being live.
UNHINTED_LIVE_DESTINATIONS: dict[str, str] = {
    "create_or_update_point": (
        "two records name two different destinations, so a hint would pick a winner nobody "
        "recorded: the canonical inventory's column says `create_entity`, while the owner's "
        "own ledger row for this name says `create_point` — and `create_point` is not one of "
        "the 40 numbered targets, so an ordinal for it cannot be recorded. Reconciling the "
        "two records is #4282 phase 2 work; until then this departure names nothing."
    ),
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
    #     exactly the set it declares.
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
