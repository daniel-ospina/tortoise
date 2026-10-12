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
``tools/sdk_rename_table.py`` is the migration map: all 150 current names and their
approved destinations, most of whose renames have **not** landed yet. Two reasons this
map is separate and deliberately tiny:

  * a product module must not import a build tool, and that table reads two product docs
    at import time;
  * the table maps names that still EXIST. This map holds only names that have **already
    left** the surface — the ones ``__getattr__`` will actually be asked about.

``tests/test_sdk_retirement.py`` pins every entry here to the owner-approved
``docs/product/beta-sdk-surface.md`` "Renamed …" table — the same source
``tools/sdk_rename_table.py`` draws its destinations from — so an entry cannot be added
here that the owner has not approved: an unapproved rename would otherwise fail every
caller of a live method with a hint pointing at a name the owner never approved. A green
run is a CONSISTENCY CHECK against that document, never consent to edit it.
"""

from __future__ import annotations

#: retired public method name -> the approved replacement to name in the failure.
RETIRED_SDK_METHODS: dict[str, str] = {
    # #4282 phase 2.1 — the event-log cursor poll. Approved target:
    # `docs/product/beta-sdk-surface.md` row 11 (`poll_events`); cited by
    # `tools/sdk_rename_table.py` region `r8`.
    "events_poll": "poll_events",
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
