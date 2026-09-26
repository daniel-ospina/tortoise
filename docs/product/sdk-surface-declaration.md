# The declared public surface of `TortoiseSDK`

**GENERATED** by `tools/sdk_surface.py` (tortoise #4282, Phase 0.4 + 1.1). Never edit by
hand — edit the generator, or the declaration it derives from. `--check` reds if this file
is stale.

**150 public methods**, declared in `config/sdk-surface.json`.

## What is declared, and where it comes from

The declaration is machine-readable (`config/sdk-surface.json`) and is **derived from the
code, never typed**: every `def`/`async def` in the AST class body of `TortoiseSDK` whose name does not begin with `_`.

The walk itself lives in `tools.bridge_table._sdk_targets` (Phase 0.1) and is *imported*
here, not re-implemented. A second walk would be a second opinion about what the SDK surface
is, and the two would eventually disagree.

The declaration holds the **name set only** — no `def` line numbers. That is deliberate: this
epic already removed `co_firstlineno` from the MCP fingerprint (Phase 3.3) because a pure code
move shifted every handler below it and reddened the gate on an unchanged surface. A line
number here would red this gate on a comment insertion.

### Why not `tortoise/__all__`

#4282's Phase 0.4 asked for `__all__` in `tortoise/__init__.py` as *"the root cause of the
150"*. It is not, and cannot be:

| Claim | Measured |
|---|---|
| "the public surface is declared by convention" | `tortoise/__init__.py` binds six names (`__version__`, `os`, `RELATIVE_PATH_ERROR`, `enforce_embedded_fork_safety`, `fork_safe_serverconfig`, `atexit_fast_close`) and has no `__all__`. `tortoise/sdk.py` has none either. |
| "`__all__` lists the 41 approved methods" | The methods are **class members**. A module-level `__all__` physically cannot name them. |
| "the surface is the module's names" | `tests/test_surface_resolution.py` resolves `getattr(TortoiseSDK, entry.sdk_method)` — the **class body** is the surface production consumes. |

## Why three comparisons, not one

A declaration checked only against itself proves nothing. `--check` compares the frozen
declaration against three views derived by genuinely different means, in both directions each:

| view | derivation | sees |
|---|---|---|
| **AST** | `_sdk_targets()` class-body walk of `tortoise/sdk.py` | what is **defined** in the class |
| **REFLECTION** | `dir(TortoiseSDK)` + `callable` | what is **reachable**, incl. attributes attached outside the class body |
| **APPROVED** | `config/surface-manifest.yml` `sdk:` rows | what an **owner approved** (#3863) |

At the render that produced this file: AST **150** · reflection **150**
· approved **150** — all agree.

AST and reflection are *not* the same question, and nothing reconciled them before this file.
`tortoise/sdk.py` already attaches one attribute outside the class body
(`TortoiseSDK._EVENT_PURGE_LAST = now`); it is private, so it does not surface today, but a
**public** attach would be visible to reflection and invisible to the AST walk. That is
exactly the divergence this reconciliation makes loud. A public attach would land here, in the
gap between the two counts — which is why both are reported.

## How to add a method to the surface

1. **Define it** in the `TortoiseSDK` class body in `tortoise/sdk.py` (a `def`/`async def`
   with no leading `_`). That alone makes it public — which is the point: it is now *caught*,
   not absorbed.
2. **Re-render**: `uv run python tools/sdk_surface.py`.
3. **Get it approved — from Daniel, FIRST.** You may not add or remove a public SDK method
   without human approval (the #4282 mandate): the surface is the contract every agent and
   customer integration is built on, so a change materially affects customer outcomes. Ask
   Daniel (repo `AGENTS.md` → "USER QUESTIONS" / "DECISION RELAY") before you write the code
   or re-cut anything. Only then add the method's `sdk:` row to `config/surface-manifest.yml`
   and set `approval` on that row to the PR number and Daniel's handle. This declaration
   records *identity*; the manifest records *approval*. `uv run python tools/surface-guard.py`
   is RED until that row exists — but a green guard is a CONSISTENCY result, not consent: a
   change that updates the class and the manifest together passes it, so it cannot tell an
   approved addition from an unapproved one. Daniel's review is what carries the approval.
4. **Verify**: `uv run python tools/surface-guard.py` and
   `uv run pytest tests/test_sdk_surface.py tests/test_surface_manifest.py -q`.

Adding a method without steps 2–3 reds `--check`. Adding it without step 3 reds the guard.
Neither reds silently.

**Removing or renaming** a method is the mirror image, and it carries the same approval gate:
you may not remove or rename a public SDK method without Daniel's approval (the #4282 mandate).
The retired name then **fails and names its replacement** (#3836 (c) ruling — there is no SDK
alias layer, no warning shim, and no call telemetry). Do not delete a `def` from the class body
until the replacement it points a caller at exists.

## Related

- `docs/product/mcp-sdk-surface.md` — the curated list the owner reads, and the approved
  baseline's human form (#3863).
- `docs/product/bridge-table.md` — Phase 0.1: every tool and its single SDK destination.
- `docs/product/canonical-sdk-methods.md` — all 150 methods in groups (#1521).
- `docs/product/beta-sdk-surface.md` — the **target** set. A target is not a description of
  today; this declaration describes **today**.
