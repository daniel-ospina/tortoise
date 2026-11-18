# SDK surface baseline — what exists today, NOT the target

**GENERATED** by `tools/sdk_surface_declaration.py baseline` (tortoise #4282 Phase
0.4/1.1). Never edit by hand — `baseline --check` reds if this file is stale.

> ⚠️ **This is a baseline, not an approval, and not a second declaration.** It records
> the public SDK surface as it exists today so the gap to the owner's target can be
> tracked while #4282 Phase 2 removes it. The **approved target is
> `tortoise/__all__.py`** — 40 methods, owner ruling 2026-09-21
> (`docs/product/beta-sdk-surface.md`, approval record in
> `docs/product/canonical-mcp-tools.md`). Nothing is approved by being listed here.
> **This list may only SHRINK.**

| measured | count |
|---|---|
| approved methods (the target) | 40 |
| live public methods on `TortoiseSDK` | 39 |
| approved and implemented (row 1 `Tortoise(...)` resolves to the `TortoiseSDK` class) | 40 |
| approved but not implemented yet | 0 |
| live and NOT approved (migration backlog) | 0 |

Row 1 of the owner's table is `Tortoise(...)` — the class a caller constructs. It resolves
to `TortoiseSDK` (the only constructible entry point); it is not a method on it.

`docs/product/beta-sdk-surface.md` states "only four of the 40 exist in the current SDK".
That sentence is STALE: 39 exist, plus the constructor. It is recorded here rather than
edited there, because the approved document is the owner's ruling and this baseline is not
the place to amend it (#10044).

## Approved but not implemented yet

The owner approved these; no `def` exists on `TortoiseSDK` under the canonical name.
Phase 2 implements them.

- (none)

## Live and NOT approved — the migration backlog

Public on `TortoiseSDK` today, absent from the approved 40. Phase 2 removes them;
the count above is the honest measure of the gap.

- (none)
