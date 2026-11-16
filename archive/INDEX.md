# Archive index — code removed from the product surface

> **Generated** by `uv run python tools/archive_surface.py index`. Do not edit by hand.
> The machine-readable record is [`MANIFEST.json`](MANIFEST.json); this file is its
> readable view, and `tools/archive_surface.py check` fails when the two disagree.

An archived symbol is **out of the product**: it is not importable from `tortoise`, it
is not served on the MCP surface, and no retired-forward name points at it. The code
is kept here so a removal can be inspected and — if it was a mistake — read back.
The mechanism and its rules: [`README.md`](README.md).

**4 archived MCP tools · 14 archived SDK methods · 1 archived modules.**

## MCP tools (4)

| archived tool | batch | reason | archived from |
|---|---|---|---|
| `tortoise_analyze` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `b85194d6bcb1` |
| `tortoise_backfill_v25` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |
| `tortoise_pack_install` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |
| `tortoise_packs_list` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |

## SDK methods (14)

| archived method | batch | reason | archived from |
|---|---|---|---|
| `diary_read` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `expand_relationships` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_dedup_candidates` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_namespaces` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_pointkinds` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_sources` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_tags` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_topics` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `provenance` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `search_sessions` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `status` | 11609-a-read-containers | retired into graph_overview(section='status') (#11609 Phase 2, #4282 row 9): the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `suggest_entry_points` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `taxonomy` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `validate_domain` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |

## Modules (1)

| archived module | batch | reason | archived from |
|---|---|---|---|
| `tortoise.archived.search_legacy` | pre-mechanism-legacy-search | pre-mechanism archive: shipped importable inside the package; moved out of the product (#11609 batch 0) | `tortoise/archived/search_legacy.py` @ `4abff72cbb65` |
