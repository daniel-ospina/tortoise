# `archive/` — code removed from the product surface

**Everything here is OUT of the product.** It is kept so a removal can be inspected and — if
it was a mistake — read back. It is not a "maybe later" holding area, and nothing here is a
candidate for restoration without a **new** surface decision.

Owner ruling, 2026-11-12 (comment [`6831894340`](https://github.com/daniel-ospina/tortoise/issues/11609#issuecomment-6831894340)):

> **"there is no per-tool destination. We simply end with the target ones and nothing more.
> Anything outside the target gets removed — archived code, so not available; we archive it in
> case we made a mistake and want to check what we had, but ultimately outside the product
> because we don't want it."**

⚠ The comment's own TITLE reads "2026-11-17"; GitHub's `created_at`/`updated_at` are
`2026-11-12T18:09:02Z`, and that is the date cited here. Four files said `2026-11-17` before
this was settled — do not "correct" them back from the title alone.

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
| `sdk_retired.RETIRED_SDK_METHODS` | raises `AttributeError` **naming** the replacement (#3836 (c)) | no (the map is the failure, not the code) | **yes for a REMOVAL** — a `cut` that drops the method requires the frozen entry too (one half of one record); a **stated rename** keeps no code by design and archives nothing |
| `sdk_retired.REMOVED_SDK_METHODS` | raises `AttributeError` with **no** hint (went internal or was deleted) | no | **yes for a removal** — same requirement; this map records no replacement, so it never takes the rename route |
| **this archive** | **nothing** — the name does not resolve | **no** | **yes — this is where the code goes** |

The `RETIRED_USE_INSTEAD` entries are therefore **not** in this archive — 23 of them when this
archive began, 73 today (measured), and none of them is among the 111 archived entries: they are
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
  prior to lose, so it is not gated) — **and for exactly the symbols that baseline carries: MCP
  tool rows, public `TortoiseSDK` methods, and the `retired:` forward names.** ⚠️ **A
  whole-`module` removal is NOT graded by `cut`**: the surface baseline has no module rows, so a
  module deletion moves nothing `cut` compares. It IS graded one seam over, by
  `tools/archive_surface.py check --base-ref` (the `surface-guard` job runs it on every PR): that
  compares the MERGE BASE's package set with this tree's, so a module that left `tortoise/` with
  no `module` entry is RED
  ([#12402](https://github.com/daniel-ospina/tortoise/issues/12402)); archive a module by hand
  from the batch procedure below ("Archive a whole module"). Do not read this bullet as
  covering every archive kind. ⚠️ **One hand-edit still defeats `cut`'s comparison, and it is
  not a defect of the gate — no comparison of two documents can see a removal both already
  agree on:** deleting `config/surface-manifest.yml` before the cut (a first cut has no prior).
  **Splicing** the baseline by hand — deleting the row, the derived row, and the four
  mechanical cells a splicer already edits (`counts.tools`,
  `counts.keyword_distribution`, the `response_fields` entry,
  `config/surface-order.yml`'s `baseline_counts`), which is what the `[surface-cut]` note in
  `MEMORY.md` prescribes for slice PRs because a raw cut rewrites the whole file — used to
  defeat it the same way, and no longer does: it still leaves `check` and the pinned
  `tools/surface-guard.py` green, because both compare the committed baseline with a
  derivation of THIS tree, so the only way to see the splice is a version the PR cannot
  author. (Name those two ARTIFACTS, not the `surface-guard` JOB — the job runs
  `tools/surface_manifest.py check --base-ref` below, so the job is RED on a splice.)
  A batch that
  takes the splice path leaves **no** archive entry and **no** retirement record — which is why
  `tools/surface_manifest.py check --base-ref` compares the committed baseline's symbol set with
  the MERGE BASE's, so a row deleted by hand is RED even though `cut` never ran
  ([`#12327`](https://github.com/daniel-ospina/tortoise/issues/12327));
  [`#12291`](https://github.com/daniel-ospina/tortoise/issues/12291) closed the same absence class
  for the archive, one artifact over. **Both halves of the baseline are compared** — unless the committed
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
  That code is **not** in this tree and this gate does not read it: none of the 26 appears in the
  archive (`INDEX.md` counted `0 archived SDK methods` when this note was written; it counts the
  106 archived since), and a reader looking for one of those 26 should read the tag. A later batch that removes SDK methods archives them **here**.

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
# hold its frozen block exactly. Plain `check` skips that arm when the object is absent (an
# ancestor is missing by construction on a shallow clone), so `check` alone never proves an
# anchor. ⛔ NO CI JOB RUNS A BARE `check` — the only invocations in `.github/workflows/` are
# this verb and `check --require-entries --base-ref`, both in the `surface-guard` job on a
# depth-0 clone — which is what makes the anchor a proof rather than a claim.
uv run python tools/archive_surface.py verify-anchors
```

### Archive a whole module

A `module` row is the only record that a whole module left the product (`#12402`): the surface
baseline carries no module rows, so `cut` and `surface-guard` compare nothing for it. Two things
make this the case that goes wrong:

1. **`archive module` reads the source from the WORKING TREE** and refuses without it, so
   restoring the file from the base is a PREREQUISITE, not an afterthought —
   `git checkout <base-sha> -- <path>` → `archive module <dotted.path> --batch <slug> --reason
   <text>` → delete it again in the same batch. Archiving AFTER the deletion cannot work.
2. **A renamed or moved file is the same case.** The old dotted path is the symbol that left the
   product; archive the OLD path and treat the new one as new code. Splitting `x.py` into a
   package (`x/__init__.py` carrying only a docstring, the body in `x/sub.py`) is the same case
   and the same route: archive `x` FIRST. The scaffolding-only `__init__.py` left behind does
   not read as the module coming back, so carrying out this route clears both arms.

The gate that reads this is `check --base-ref` (bare `--base-ref` = the default branch), which
compares the MERGE BASE's module set with this tree's and grades an `__init__.py` by its
**CONTENT**, not by its path: deleting one that carries only a docstring (or nothing) is
scaffolding, so the package still resolves and nothing left the product — while deleting one
that carries statements IS a removal even though the package name still resolves through a
surviving sibling, because the code that answered as that package's front door left. The SAME
rule grades every module file, not just an `__init__.py`: a module whose file survives with its
body gone — truncated to nothing, reduced to a docstring, reduced to a bare no-op (`pass`,
`...`, `None`, or any other top-level expression statement that neither calls, awaits, yields
nor binds a `:=`), replaced by a `.pyi` stub, or rewritten to text that no longer parses — is a
removal too, because the name no longer resolves to any code, and a comparison over paths cannot
see it.

> **OVERRIDES:** the "a statement is content only if it calls, awaits, yields or binds" default —
> for a BINDING, an `import` and a `def`/`class` the answer is CONTENT even when the binding's value
> is a bare constant. A module truncated to `X = 1` (or `X: int`, `import os`, `class X: pass`)
> still EXPORTS a name a caller reaches (`from mod import X`), so "the name no longer resolves to
> any code" is false for it; grading it a removal false-blocks every constants-only module (16 rows
> of `tests/test_archive.py` measure it: treating `Assign`/`AnnAssign`/`Import`/`ImportFrom` as
> inert reds all 16). The shrink gate grades per-symbol EXISTENCE, not proportions — so a body reduced to
> one binding is a residual this arm does not see, deliberately, and closing it needs a different
> check rather than another inertness spelling. This arm grades ONE file shape at a time; a body
> replaced by a file CREATED in the same commit holding an unrelated statement is the same residual
> by a different route, and it is likewise not closed here. So is a body emptied while another
> carrier of the same module survives as a re-export shim: arm 1b asks whether ANY carrier still
> holds a runtime body, so a shim that re-exports the same names answers yes (measured: emptying a
> 23,899-line package `__init__.py` to a docstring while a 37-line `import_module` shim survives is
> graded GREEN). The gate grades per-symbol EXISTENCE across a path set, not proportions, and a
> legitimate shim is indistinguishable from a decoy without comparing the two sides' name sets —
> which is a separate arm, not a tighter spelling rule (#14298).

This check runs as part of the `surface-guard` job on every PR, and an unresolvable base ref is a REFUSAL (exit 2),
never a quiet pass. If the deletion is already recorded somewhere this gate cannot read, state
it: `--allow-archive-shrink module:<dotted.path> --allow-archive-shrink-reason <why>` (a
local-shell record — CI passes no waiver, see "Restoring a removed symbol" below).

## Restoring a removed symbol

**The archived file is the source of record** — reading it needs no git history. Two things do
need history, and both are checked: `verify-anchors` proves each file's cited commit is readable
here, holds the block exactly, and is an ancestor of the default branch (that verb runs in CI on
a full-history clone), and it refuses an archive with **no entries at all** so an emptied
manifest cannot pass vacuously. `check` itself is green at zero entries on purpose, because the
restore path legitimately ends there; pass `--require-entries` when zero must be a failure.

⚠️ **ONE entry REMOVED from a non-empty manifest IS graded** — `check --base-ref`
([#12291](https://github.com/daniel-ospina/tortoise/issues/12291)) compares the manifest's rows
with the merge base's and reports a row that **left**, or one whose CLAIM was **rewritten** in
place (`kind`, `symbol`, `file`, `archived_from`, `archived_at`, `sources`, `blocks` — appending a
tombstone field is what an append-only record does, so it is not a rewrite). Passing `--base-ref`
needs no special handling for a restore, because all three legitimate shapes are green:

- **a restore** — the row and its frozen file go (steps 3-4 below) and the SYMBOL is live again
  **with the recorded body**: in `TOOL_REGISTRY`, on `TortoiseSDK`, or importable from `tortoise/`,
  and re-extracting it yields the block the row recorded. A live NAME alone is not enough — a stub
  dropped at the dotted path is not the code coming back. ⚠ A restore that also CHANGES the code
  has no green state in the PR that lands it: the row cannot stay (the direction above reds a live
  symbol) and the drop cannot be credited (the body is not the recorded one), so the route is to
  restore the block VERBATIM here and change the code afterwards, when the row is gone from the
  merge base. The code is back in the product, so nothing the archive
  promised is missing — and the row CANNOT stay, because the other direction reds an archived
  symbol that is live again. The frozen bytes remain readable in git history at the deletion
  commit (`git log -- archive/<kind>s/…`). This is why the arm is safe in CI: the CI job runs it
  with `--base-ref` on every PR, and a restore is green there.
- **a verified re-anchor** — `archived_at` moves FORWARD (the new commit is a descendant of the
  one it replaces) and onto the default branch, every block key and count is unchanged, every
  other claim field is unchanged, `sources` still derives from the blocks, and the new commit
  holds each recorded body byte-for-byte. That is the correction `verify-anchors` asks for when
  the frozen body is refreshed from the commit that last held the code; it is not a way to edit
  the record, and anything short of all of it stays the violation #12291 is about.
- **a stated waiver** — `--allow-archive-shrink <kind>:<symbol>
  --allow-archive-shrink-reason <why>`, for a drop that is neither a restore nor a mistake, and
  it goes in the PR body. It needs `--base-ref` (the arm it waives) and it is a LOCAL-SHELL
  record: **CI passes no waiver flag, deliberately** — every non-restore deletion has a record
  route (archive it), and a waiver committed to the workflow would be a standing excuse for
  every later PR while one that waives nothing is itself reported. A waiver is therefore never
  what a restore needs.

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
4. `tools/archive_surface.py check` must be clean — and it stays clean with `--base-ref` too,
   because the arm treats a dropped row whose symbol is live again as the restore it is. **Restoring the LAST entry leaves a legal
   empty archive** — the plain form is green at zero entries on purpose, so this step works;
   `check --require-entries` and `verify-anchors` are the forms that refuse an empty one, and
   CI runs the latter, where an empty archive means the code was deleted rather than restored.

⛔ **Restoring the code is not permission to re-expose it.** Putting a symbol back on the live
surface is a **new surface change** and needs the owner's approval again (`AGENTS.md` →
"MCP/SDK Surface Approval", recorded in `docs/product/canonical-mcp-tools.md`). The archive
exists so a mistake is *readable*, not so it is *reversible by default*.
