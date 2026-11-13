# `archive/` — code removed from the product surface

**Everything here is OUT of the product.** It is kept so a removal can be inspected and — if
it was a mistake — read back. It is not a "maybe later" holding area, and nothing here is a
candidate for restoration without a **new** surface decision.

Owner ruling, 2026-11-17 (comment [`6831894340`](https://github.com/daniel-ospina/tortoise/issues/11609#issuecomment-6831894340)):

> **"there is no per-tool destination. We simply end with the target ones and nothing more.
> Anything outside the target gets removed — archived code, so not available; we archive it in
> case we made a mistake and want to check what we had, but ultimately outside the product
> because we don't want it."**

The target itself is owner-approved in
[`docs/product/canonical-mcp-tools.md`](../docs/product/canonical-mcp-tools.md) — **27 MCP
tools** and **40 public SDK methods** (`tortoise/__all__.py`).

## The two directions, and why both matter

```text
IN THE TARGET SET   →  kept, as the product surface
EVERYTHING ELSE     →  removed from the product, code archived here (readable, not importable
                       *as part of the product*)
```

Removing into a directory that does not exist is how a removal becomes unrecoverable — so the
mechanism landed **before** the first batch, and every batch goes through it.

## What "not available" means, concretely

An archived symbol is:

- **not importable from the product package** — `archive/` is outside `tortoise/`, outside
  `pyproject.toml`'s `packages.find`, and carries no `__init__.py`. `import tortoise.archive`
  does not resolve, and no archived module ships in the wheel;
- **not served** — an archived MCP tool is not in `TOOL_REGISTRY`, and is **not** a
  `RETIRED_USE_INSTEAD` forward entry either (that mechanism still *serves* the name);
- **not callable** — an archived SDK method is not a public `TortoiseSDK` attribute. It **is**
  recorded in `tortoise/sdk_retired.py` (`RETIRED_SDK_METHODS` when it has an approved
  replacement, `REMOVED_SDK_METHODS` when it has none), so a caller gets a recorded, fail-closed
  `AttributeError` instead of a silent break;
- **readable** — the file holds the **verbatim source** that was removed, with the batch, the
  reason, and the commit it came from.

⚠️ **What is and is not enforced about importability.** The enforced property is "not importable
**from the product package**": nothing under `tortoise/`, nothing in the wheel. From a repo
checkout, archived code **is importable**: `import archive` and `import archive.modules` resolve
as PEP 420 namespace packages, and `importlib.import_module("archive.modules.<batch>.<path>")`
resolves and EXECUTES any frozen module regardless of the batch slug (measured — a hyphen stops the
`import …` *statement*'s grammar, not the import machinery). **Reading** a frozen file is the
supported path; **importing** one is not supported, and `check` fails closed on the direction that matters: no
module under `tortoise/` may **statically** import `archive`. Parsed with `ast`, so a mention in
prose is not an import; a static `import archive…` / `from archive… import` AND a dynamic
`importlib.import_module("archive…")` / `__import__("archive…")` are caught.
⚠ **RESIDUAL, stated not implied:** a module name *assembled at runtime* cannot be seen by any
static scan, so that form is not caught. The product-facing property is the shipped package and the
wheel — nothing under `tortoise/`, nothing in `packages.find`, no `__init__.py` anywhere under
`archive/`.

`tools/archive_surface.py check` enforces every one of those, and
`tests/test_archive.py` proves the detectors **fire** (a gate nobody has seen fire is
indistinguishable from one that cannot).

## How this composes with the existing retirement mechanisms

It **composes** — it replaces neither, because they answer different questions:

| mechanism | what the name does | is it in the product? | archived? |
|---|---|---|---|
| `tool_registry.RETIRED_USE_INSTEAD` | **served**, with a warning naming the replacement (#3883) | **yes** — it still answers | **no** |
| `sdk_retired.RETIRED_SDK_METHODS` | raises `AttributeError` **naming** the replacement (#3836 (c)) | no (the map is the failure, not the code) | **yes** — a `cut` that drops the method requires the frozen entry too (one half of one record) |
| `sdk_retired.REMOVED_SDK_METHODS` | raises `AttributeError` with **no** hint (went internal or was deleted) | no | **yes** — same requirement |
| **this archive** | **nothing** — the name does not resolve | **no** | **yes — this is where the code goes** |

The 23 existing `RETIRED_USE_INSTEAD` entries are therefore **not** in this archive: they are
off the advertised count already, and `canonical-mcp-tools.md` records (2026-10-27 amendment)
that removing the forward entries "is a separate decision". Archiving them would be a different
ruling, not this one.

## The files

| path | what it is |
|---|---|
| `MANIFEST.json` | the machine-readable record — one row per archived symbol (kind, symbol, batch, reason, file, source, commit) |
| `INDEX.md` | **generated** view of the manifest, for a human reader. Regenerate with `tools/archive_surface.py index`; `check` fails when it drifts |
| `mcp-tools/<batch>/<tool>.py` | a frozen `ToolDefinition` literal plus every `mcp_server.py` function whose **code** names the tool — matched by NAME (`def tortoise_<tool>`) or by the name appearing as a string (a mention in a DOCSTRING is prose and is not captured). ⚠ A shared helper carrying a tool-specific branch (e.g. `if name == "tortoise_delete": …`) IS captured: that branch is the tool's behaviour |
| `sdk-methods/<batch>/<method>.py` | a frozen `TortoiseSDK` method, verbatim |
| `modules/<batch>/<dotted/path>.py` | a whole module frozen from `tortoise/`, verbatim, at the **mirrored** dotted path (`tortoise.archived.legacy` → `modules/<batch>/tortoise/archived/legacy.py`) so two module symbols can never collide on one file |

## Adding to the archive (the batch procedure)

An archived symbol is removed in the **same** batch that archives it. The two directions are
enforced at two different seams, deliberately:

- **an archive entry whose symbol is still live is RED** — `tools/archive_surface.py check`
  (still in `TOOL_REGISTRY`, still a public `TortoiseSDK` method, still importable from the
  package, or still a `RETIRED_USE_INSTEAD` forward entry). This gate grades the manifest
  against the surface;
- **a symbol that disappears with NO record is RED** — `tools/surface_manifest.py cut`
  ([#12237](https://github.com/daniel-ospina/tortoise/issues/12237)). This gate cannot live
  in `check`: it iterates the manifest's own entries, so a symbol gone from **both** is
  invisible to it. `cut` is the seam where a row is actually forgotten, so the refusal is
  there — **whenever a committed baseline exists to compare against** (a first cut has no
  prior to lose, so it is not gated). ⚠️ **Two hand-edits defeat the comparison, and neither is
  a defect of the gate — no comparison of two documents can see a removal both already agree
  on:** deleting `config/surface-manifest.yml` before the cut (a first cut has no prior), or
  **splicing** the baseline by hand — deleting the row, the derived row, and the four
  mechanical cells a splicer already edits (`counts.tools`,
  `counts.keyword_distribution`, the `response_fields` entry,
  `config/surface-order.yml`'s `baseline_counts`), which is what the `[surface-cut]` note in
  `MEMORY.md` prescribes for slice PRs because a raw cut rewrites the whole file. A batch that
  takes the splice path leaves **no** archive entry and **no** retirement record with every
  required gate green; a shrink pin on the committed counts is the only instrument that would
  see it ([`#12327`](https://github.com/daniel-ospina/tortoise/issues/12327)), and
  [`#12291`](https://github.com/daniel-ospina/tortoise/issues/12291) covers the same absence class
  for the archive. **Both halves of the baseline are compared** — unless the committed
  baseline carries no `retired:` half at all: `check` already reds that shape, so `cut`
  is repairing it, and the cut then says so and does NOT compare that half. With both
  halves present they are compared because a
  retired name is still SERVED: `RETIRED_TOOL_REGISTRY` is derived from
  `RETIRED_USE_INSTEAD`, so one edit to that map detaches both, and a gate reading only
  `rows:` would watch a live name leave the product and report nothing. A dropped symbol must
  be accounted for by an archive entry of the matching kind — and for an **SDK method** that
  entry is ONE HALF of the record: it must be paired with `RETIRED_SDK_METHODS` /
  `REMOVED_SDK_METHODS`. **The two halves are ONE record and BOTH are required** — the freeze
  keeps the code, the map records the departure, and neither alone is the record. A `RETIRED_USE_INSTEAD` entry is **not**
  one of these routes:
  that map is what makes a name a RETIREMENT rather than a removal — `TOOL_REGISTRY` excludes it
  and `RETIRED_TOOL_REGISTRY` includes it, so the name is still served and the baseline's
  `retired:` half names it — and a bare key whose declaration is gone dangles, which the cut
  refuses ("delete the stale forward key"). For a
  **tool**, an archive entry AND a `RETIRED_USE_INSTEAD` row is two records and is refused: a
  name that is both archived and still served is the inconsistency `check` reds from its own
  side. For an **SDK method** the freeze and the map entry are ONE record, and `cut` requires
  **both** halves: the pair apart is refused (counting it as two records refused this file's
  own procedure — freeze, then record), archive-only is refused by `check` (a frozen entry with
  no map entry leaves a caller with a bare `AttributeError`), and map-only is refused by `cut`
  (the map records the departure but keeps no code, and `check` iterates the ARCHIVE MANIFEST,
  so a method that was never frozen is invisible to it — map-only passed both gates while
  freezing nothing until review round 14 of #12237) unless it is a STATED RENAME, which is the
  recorded SDK rename shape (old name into `RETIRED_SDK_METHODS`, method gone from `sdk.py`) and
  keeps no code by design. A genuine rename passes `cut --allow-removal <symbol>
  --allow-removal-reason <why>`, which is logged in the cut output and belongs in the PR body;
  a reason with no symbol is refused, because it records an override that did not happen.
  **The refusal happens BEFORE the write**, so a refused cut never leaves a baseline that has
  already forgotten the symbol.

So the batch procedure below is no longer the only guarantee for direction two: the cut
refuses. Run step 1 **before** step 2 anyway — the gate proves the record EXISTS, not that it
holds the code you meant to keep (an `sdk-method` entry cannot account for an MCP tool, and no
static gate can tell a correct block from a careless one).

The `module` kind is how code that was archived **inside** the package before this mechanism
existed is moved out. `tortoise/archived/` is refused by the gate for that reason: a file under
`tortoise/` is importable and ships in the wheel, which is the opposite of "not available".

⚠️ Two **pre-mechanism** archives predate this tree, and one of them is not a directory at all:

- `tortoise/archived/search_legacy.py` — migrated here (`modules/pre-mechanism-legacy-search/`);
- the **git tag** `archive/4282-surplus-sdk-methods` — the 26 public SDK methods deleted by
  `#4282` under the 2026-10-27 ruling (owned by
  [`docs/product/archived-sdk-methods-2026-10.md`](../docs/product/archived-sdk-methods-2026-10.md)).
  That code is **not** in this tree and this gate does not read it: `0 archived SDK methods` in
  [`INDEX.md`](INDEX.md) is literally true, and a reader looking for one of those 26 should read
  the tag. A later batch that removes SDK methods archives them **here**.

```bash
# 1. Freeze the code. Run BEFORE deleting: it reads the source from the working tree and records
#    a commit on the DEFAULT BRANCH that holds the code — not this branch's HEAD, because a
#    squash merge never lands that commit on `main` and the citation would then be unreadable.
uv run python tools/archive_surface.py archive mcp-tool tortoise_search \
    --batch mcp-read-duplicates --reason 'no per-tool destination; removed (#11609)'

#    …or a whole module that currently ships inside the package:
uv run python tools/archive_surface.py archive module tortoise.archived.legacy_search \
    --batch pre-mechanism --reason 'shipped importable inside the package; moved out'

# 2. Delete it from the live surface (registry entry + any custom handler + call sites).
#    The batch PR's job — one batch, one purpose.

# 3. Re-cut the derived artifacts and make the gates agree.
uv run python tools/surface_manifest.py cut
uv run python tools/surface_manifest.py check
uv run python tools/surface-guard.py

# 4. Assert the archive and the live surface agree.
uv run python tools/archive_surface.py check
uv run python tools/archive_surface.py check --require-entries   # never "0 entries, OK"
uv run python tools/test_lane.py run -- tests/test_archive.py -q
#    (a bare `pytest` refuses without TORTOISE_DB_URI — epic #1647 P4 — so use the lane runner
#     even though this file is hermetic)

# STRICTER, and proven in CI: every `archived_at` commit must be readable in this clone AND
# hold its frozen block exactly. Plain `check` skips that arm when the object is absent (the
# `test` job checks out at fetch-depth: 1, where an ancestor is missing by construction), so
# `check` alone never proves an anchor. This verb is what the `surface-guard` job runs, on a
# depth-0 clone — that is what makes the anchor a proof rather than a claim.
uv run python tools/archive_surface.py verify-anchors
```

## Restoring a removed symbol

**The archived file is the source of record** — reading it needs no git history. Two things do
need history, and both are checked: `verify-anchors` proves each file's cited commit is readable
here, holds the block exactly, and is an ancestor of the default branch (that verb runs in CI on
a full-history clone), and it refuses an archive with **no entries at all** so an emptied
manifest cannot pass vacuously. `check` itself is green at zero entries on purpose, because the
restore path legitimately ends there; pass `--require-entries` when zero must be a failure.

⚠️ **What no gate sees: ONE entry REMOVED from a non-empty manifest.** A shrink guard is not
built. `cut`'s refusal covers the SURFACE direction (a live symbol disappearing with no
record); it does not cover a manifest that quietly loses a row which the surface also no
longer carries — the code would then be gone from the repo with every gate green. Tracked in
[#12291](https://github.com/daniel-ospina/tortoise/issues/12291), separately from #12237, which
closed the surface direction only.

1. open `archive/mcp-tools/<batch>/<tool>.py` (or `sdk-methods/…`, `modules/…`) and copy **each**
   verbatim block back into the file named in **its own** `verbatim from <path>` banner — one
   symbol can span more than one file (an MCP tool's `ToolDefinition` lives in
   `tool_registry.py` while its handler lives in `mcp_server.py`);
   ⚠ **A block whose function is still LIVE is a branch, not a paste.** When the archived block is
   a shared helper that survived the removal (only a tool-specific branch of it was archived),
   merge that branch back into the existing function — pasting the block adds a second definition
   of a live function;
2. re-add the registry/declaration row and re-cut the manifest (`tools/surface_manifest.py cut`);
3. delete the manifest row and the archived file, then `tools/archive_surface.py index`;
4. `tools/archive_surface.py check` must be clean. **Restoring the LAST entry leaves a legal
   empty archive** — the plain form is green at zero entries on purpose, so this step works;
   `check --require-entries` and `verify-anchors` are the forms that refuse an empty one, and
   CI runs the latter, where an empty archive means the code was deleted rather than restored.

⛔ **Restoring the code is not permission to re-expose it.** Putting a symbol back on the live
surface is a **new surface change** and needs the owner's approval again (`AGENTS.md` →
"MCP/SDK Surface Approval", recorded in `docs/product/canonical-mcp-tools.md`). The archive
exists so a mistake is *readable*, not so it is *reversible by default*.
