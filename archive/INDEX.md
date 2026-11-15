# Archive index — code removed from the product surface

> **Generated** by `uv run python tools/archive_surface.py index`. Do not edit by hand.
> The machine-readable record is [`MANIFEST.json`](MANIFEST.json); this file is its
> readable view, and `tools/archive_surface.py check` fails when the two disagree.

An archived symbol is **out of the product**: it is not importable from `tortoise`, it
is not served on the MCP surface, and no retired-forward name points at it. The code
is kept here so a removal can be inspected and — if it was a mistake — read back.
The mechanism and its rules: [`README.md`](README.md).

**4 archived MCP tools · 5 archived SDK methods · 1 archived modules.**

## MCP tools (4)

| archived tool | batch | reason | archived from |
|---|---|---|---|
| `tortoise_analyze` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `b85194d6bcb1` |
| `tortoise_backfill_v25` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |
| `tortoise_pack_install` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |
| `tortoise_packs_list` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |

## SDK methods (5)

| archived method | batch | reason | archived from |
|---|---|---|---|
| `checkpoint` | 11609-phase2-sources-containers | name retired into mine_knowledge_from_session(items=...) (#4282 Phase 2, #11609): row 16 keeps this batch filer as its items arm, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `create_source` | 11609-phase2-sources-containers | name retired into register_source (#4282 Phase 2, #11609): the approved row 14 keeps this body under its own name, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `index_directory` | 11609-phase2-sources-containers | name retired into index_sources_from_directory (#4282 Phase 2, #11609): row 15 keeps this walker under its own name, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `index_sessions` | 11609-phase2-sources-containers | name retired into index_sources_from_directory(file_type='agent_session') (#4282 Phase 2, #11609); it was a single delegation, frozen as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `mine_corpus` | 11609-phase2-sources-containers | name retired into mine_knowledge_from_directory (#4282 Phase 2, #11609): row 17 keeps this miner under its own name, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |

## Modules (1)

| archived module | batch | reason | archived from |
|---|---|---|---|
| `tortoise.archived.search_legacy` | pre-mechanism-legacy-search | pre-mechanism archive: shipped importable inside the package; moved out of the product (#11609 batch 0) | `tortoise/archived/search_legacy.py` @ `4abff72cbb65` |
