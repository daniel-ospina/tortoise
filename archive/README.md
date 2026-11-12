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
checkout, `import archive.<batch>.<file>` *does* resolve as a PEP 420 namespace package — that is a
fact about Python, not a hole in the gate, and asserting otherwise would be a false claim.
**Reading** a frozen file is the supported path; **importing** one is not supported (the files are
frozen source, not maintained modules) and nothing in the product ever does it.

`tools/archive_surface.py check` enforces every one of those, and
`tests/test_archive.py` proves the detectors **fire** (a gate nobody has seen fire is
indistinguishable from one that cannot).

## How this composes with the existing retirement mechanisms

It **composes** — it replaces neither, because they answer different questions:

| mechanism | what the name does | is it in the product? | archived? |
|---|---|---|---|
| `tool_registry.RETIRED_USE_INSTEAD` | **served**, with a warning naming the replacement (#3883) | **yes** — it still answers | **no** |
| `sdk_retired.RETIRED_SDK_METHODS` | raises `AttributeError` **naming** the replacement (#3836 (c)) | no (the map is the failure, not the code) | the code may be |
| `sdk_retired.REMOVED_SDK_METHODS` | raises `AttributeError` with **no** hint (went internal or was deleted) | no | the code may be |
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

An archived symbol is removed in the **same** batch that archives it. Of the two directions,
`check` enforces the one that protects the archive — **an archive entry whose symbol is still
live is RED** (still in `TOOL_REGISTRY`, still a public `TortoiseSDK` method, still importable
from the package, or still a `RETIRED_USE_INSTEAD` forward entry). The other direction — code
deleted with no archive entry — is **not** enforced by this gate: it grades the manifest against
the surface, and a symbol that is simply gone from both is invisible to it. That direction is
[#12237](https://github.com/daniel-ospina/tortoise/issues/12237) (`cut`-time enforcement),
**not** a property of this mechanism — so the batch procedure below is the only guarantee for
it: run step 1 **before** step 2.

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
#    the commit on the DEFAULT BRANCH that last held it — not this branch's HEAD, because a
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
uv run python tools/archive_surface.py check --require-entries   # CI's form: never "0 entries, OK"
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
here AND holds the block exactly (it runs on a full-history clone in CI), and `check` refuses an
archive with **no entries at all** so an emptied manifest cannot pass vacuously. What no gate
sees is an entry **removed** from a non-empty manifest — a shrink guard is not built; that
direction is part of [#12237](https://github.com/daniel-ospina/tortoise/issues/12237).

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
