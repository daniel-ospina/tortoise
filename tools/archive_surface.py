#!/usr/bin/env python3
"""The surface archive — where code removed from the product lives (#11609).

THE RULE THIS IMPLEMENTS
    IN THE TARGET SET  →  kept, as the product surface
    EVERYTHING ELSE    →  REMOVED from the product, and the CODE ARCHIVED so it is
                          recoverable and inspectable (kept in the repo, NOT importable
                          FROM THE SHIPPED PACKAGE, NOT exposed)

Owner ruling, 2026-11-12 verbatim in substance (comment `6831894340` on #11609):
*"we simply ended with the target ones and nothing more. anything outside the target gets
removed (archived code so not available, we archive it in case we made a mistake and want
to check what we had but ultimately outside the product because we don't want it)."*

WHY A TOP-LEVEL `archive/` AND NOT A SUBPACKAGE
    `tortoise/` IS the shipped product package (`[tool.setuptools.packages.find]
    include = ["tortoise*", …]`). Code under it is
    importable as `tortoise.<mod>` and lands in the wheel. An archive that lives there
    would be a symbol that still resolves — the opposite of "not available". A top-level
    `archive/` is outside the package namespace, outside `packages.find`, outside
    `mypy tortoise/`, and it is where a reader looks first. That is the whole design.

HOW THIS COMPOSES WITH THE TWO EXISTING RETIREMENT MECHANISMS — AND WHAT IT REPLACES
    Three departures exist, and they are DIFFERENT, so this does not collapse them:

    1. `tortoise/tool_registry.py::RETIRED_USE_INSTEAD` — MCP **retired-forward**. The
       name leaves the *advertised* surface but is STILL SERVED: a caller of a retired
       name resolves and is handed the replacement call (#3883). It is a service to a
       caller, so it is *inside* the product and is **not** archived.
    2. `tortoise/sdk_retired.py::RETIRED_SDK_METHODS` / `REMOVED_SDK_METHODS` — the SDK's
       fail-closed record (#3836 (c)). There is no alias layer: a retired name raises
       `AttributeError` naming its replacement; a REMOVED name raises with no hint
       because no replacement exists. This map stays authoritative for *the failure*.
    3. **The archive (this file).** The code itself, frozen and readable, with no name
       left on the surface at all. The archive is what makes a removal *recoverable*
       rather than *lost*.

    So the archive **COMPOSES**; it replaces neither:
      · an archived MCP tool must NOT be in `RETIRED_USE_INSTEAD` (archived means the
        name does not resolve; a forward entry means it does) — enforced below;
      · an archived SDK method MUST be recorded in `RETIRED_SDK_METHODS` or
        `REMOVED_SDK_METHODS`, so a caller of it still fails CLEANLY and the departure is
        accounted for — enforced below. The archive adds the code; the map keeps the
        failure.
    The `RETIRED_USE_INSTEAD` entries are therefore NOT in this archive's scope. (23 of them when
    this note was written, 73 today, measured — the pair of counts that stood here, "23" as a
    present state and "68" as the live advertised count, were both stale by the time round 21
    checked them; the live tool count is 39.) They are off the advertised count already, and
    `docs/product/canonical-mcp-tools.md` records (2026-10-27 amendment) that removing
    the forward entries "is a separate decision" — so archiving them here would be a
    different ruling, not this one.

WHAT IS ENFORCED (both `check` and `tests/test_archive.py`)
    · every archived symbol is GONE from the live surface — no live registry tool, no
      public `TortoiseSDK` method (the "archive must not be a slow leak back" rule);
    · every manifest entry has a file and every file under `archive/` is in the manifest
      (no unindexed code, no dangling index row);
    · each archived module's `ARCHIVED_*` constants match its manifest row, and every
      frozen block's **sha256** matches the manifest (the copy is the source that was
      removed — not a stub, and not something a later edit drifted);
    · the block is the source its anchor CITES: `verify-anchors` re-extracts the symbol from
      the recorded commit and compares byte-for-byte (exactness, not containment — see
      `_anchor_violations`). It is a separate verb because it cannot answer on a
      fetch-depth-1 clone, and a proof that only runs where it cannot answer is no proof;
    · `archive/` is not reachable through the PRODUCT package: no `tortoise/archive` path,
      no `__init__.py` anywhere under `archive/`, and `packages.find` does not match it (so
      it is not in the wheel). There is deliberately no `find_spec` arm: it would answer
      about the AMBIENT interpreter rather than `root` (see `package_violations`), and
      `live_surface(root)` already refuses when `import tortoise` is not inside `root`;
    · `archive/INDEX.md` is a regenerated view of `MANIFEST.json` — a stale index is a
      violation;
    · THE SHRINK DIRECTION, when a base ref is given (`check --base-ref`, #12402/#12291): a
      MODULE that left `tortoise/` since the merge base must carry a `module` entry, and an
      entry that left — or was rewritten in — `archive/MANIFEST.json` must be restored or
      waived with a stated reason. This is the half every other arm here is blind to, because
      they all iterate the entries the manifest FINDS; it needs the base ref because the
      reference has to be outside the diff (see the block comment above `CLAIM_FIELDS`);
    · NO product module imports the archive, which is the archive's promise turned around
      (removed BEHAVIOUR must not re-enter the product): a static `import archive…` /
      `from archive… import` and a dynamic `import_module("archive…")` / `__import__("archive…")`
      are both caught. ⚠ RESIDUAL: a module name built at runtime cannot be seen by any static
      scan, so that form is not caught and is stated rather than implied.

    ⛔ WHAT IS — AND IS NOT — ENFORCED ABOUT IMPORTABILITY. The enforced property is
    "not importable from the PUBLIC PACKAGE": nothing under `tortoise/`, nothing in the
    wheel. ⚠️ From a repo checkout, `import archive` DOES resolve as a PEP 420 namespace
    package (verified: `find_spec("archive").submodule_search_locations` is the tree), and
    asserting otherwise would be false — so that is stated rather than implied. Reading a
    frozen file in place is fine; IMPORTING it is not a supported path (the files are
    frozen source, not maintained modules) and nothing in the product ever does.

Usage
    uv run python tools/archive_surface.py check
    uv run python tools/archive_surface.py check --base-ref          # + the shrink arms
    uv run python tools/archive_surface.py index              # regenerate INDEX.md
    uv run python tools/archive_surface.py index --check      # assert it is current
    uv run python tools/archive_surface.py archive mcp-tool <tool> --batch <slug> --reason <text>
    uv run python tools/archive_surface.py archive sdk-method <method> --batch <slug> --reason <text>
    uv run python tools/archive_surface.py archive module <dotted.module> --batch <slug> --reason <text>

    Run `archive` BEFORE deleting the symbol from its source file: it reads the source from
    the working tree, requires it to match the commit on the DEFAULT BRANCH that last held it,
    and records THAT commit — so the restore recipe is exact AND the citation survives the
    squash merge that lands the batch.

Exit 0 = clean. Exit 1 = violations (printed one per line). Exit 2 = usage/refusal.
"""

from __future__ import annotations

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE, before the rest of the
# imports, so a stale local `python3` fails LOUDLY with a remedy instead of with whichever 3.12-only
# construct happens to trip first — this module imports `tomllib` (3.11+), so an unguarded import
# block raises ImportError before this guard could ever run and the operator gets an UNATTRIBUTED
# traceback. `tests/test_entry_point_python_guard.py` asserts structurally that this guard precedes
# every other import and behaviourally that it names the floor, the interpreter it got, and the
# invocation that works.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/archive_surface.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/archive_surface.py check`"
    )

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import tomllib
import unicodedata
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
MANIFEST_PATH = ARCHIVE_DIR / "MANIFEST.json"
INDEX_PATH = ARCHIVE_DIR / "INDEX.md"

SCHEMA = 1
MCP_TOOL = "mcp-tool"
SDK_METHOD = "sdk-method"
MODULE = "module"
KINDS = (MCP_TOOL, SDK_METHOD, MODULE)

SUBDIR = {MCP_TOOL: "mcp-tools", SDK_METHOD: "sdk-methods", MODULE: "modules"}
#: Where each kind is frozen FROM. `module` is the one kind whose source path is not
#: fixed — it is the module's own file — so it carries its source in the symbol.
SOURCE_OF = {
    MCP_TOOL: "tortoise/tool_registry.py",
    SDK_METHOD: "tortoise/sdk.py",
    MODULE: "tortoise/archived/",
}
#: The `ARCHIVED_*` constants, each paired with the manifest field it must equal.
#: ⚠ `ARCHIVED_AT` belongs here: written into every archived file but left out of this table
#: once, so the file's own record of its source commit could be hand-edited while `check`
#: stayed green (measured in review — the file and the manifest disagreed about the commit).
METADATA_FIELDS = (
    ("ARCHIVED_KIND", "kind"),
    ("ARCHIVED_SYMBOL", "symbol"),
    ("ARCHIVED_BATCH", "batch"),
    ("ARCHIVED_FROM", "archived_from"),
    ("ARCHIVED_AT", "archived_at"),
)
METADATA_KEYS = tuple(key for key, _field in METADATA_FIELDS)

#: A batch slug becomes a DIRECTORY name under `archive/`, so it is validated rather than
#: trusted: a `--batch ../../tortoise` would otherwise write archived code back INSIDE the
#: shipped package (measured: it did), where it is importable and ships in the wheel — the
#: exact outcome the archive exists to prevent.
_BATCH_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: One segment of a mirrored archive path. A symbol is a dotted path of these, so
#: `..`, `-x`, `.hidden` and `` are all refused before a path is ever built.
#: Kept as a name for the traversal guard's readability; the test is `str.isidentifier()`, so a
#: legal non-ASCII module name (`tortoise.café`) can still be archived — a regex over ASCII
#: refused it, leaving that module with no archive path in a mechanism that has exactly one.
_NAME_RE = re.compile(r"^[^\W\d]\w*$")
#: The header banner every archived module must carry for each frozen source block. Its
#: presence is what distinguishes a real archive from a file that merely repeats the
#: metadata (a body lost in a merge reads green otherwise — measured).
VERBATIM_BANNER = "# ── verbatim from "

#: The index's section titles and summary words per kind.
INDEX_LABEL = {
    MCP_TOOL: ("MCP tools", "tool", "MCP tools"),
    SDK_METHOD: ("SDK methods", "method", "SDK methods"),
    MODULE: ("Modules", "module", "modules"),
}


# ── the manifest ────────────────────────────────────────────────────────────────

class ArchiveRefused(Exception):
    """The archive cannot be read or written — a refusal, never a silent skip."""


def load_manifest(path: Path | None = None) -> dict:
    """Read `archive/MANIFEST.json`. Missing file is EMPTY, not an error (first run)."""
    path = Path(path or MANIFEST_PATH)
    if not path.exists():
        return {"schema": SCHEMA, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveRefused(f"could not read {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ArchiveRefused(f"{path} is not an archive manifest (expected an `entries` list)")
    return data


def _sorted_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: (str(e.get("kind")), str(e.get("symbol"))))


def save_manifest(data: dict, path: Path | None = None) -> None:
    path = Path(path or MANIFEST_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**data, "schema": SCHEMA, "entries": _sorted_entries(list(data["entries"]))}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── the live surface (executed, never parsed) ───────────────────────────────────

def live_surface(root: Path | None = None) -> dict:
    """The live surface and the two retirement maps, imported rather than read as text.

    `root` is the checkout this call is GRADING, and it is required in effect: the surface is
    whatever `import tortoise` resolves to, so a call that reads one tree's manifest while
    grading another's package is a silent false CLEAN (#11609 review). When the imported
    package is not inside `root`, this refuses rather than answering about someone else's
    code.
    """
    root = Path(root or ROOT).resolve()
    try:
        import tortoise
        from tortoise.sdk import TortoiseSDK
        from tortoise.sdk_retired import REMOVED_SDK_METHODS, RETIRED_SDK_METHODS
        from tortoise.tool_registry import RETIRED_USE_INSTEAD, TOOL_REGISTRY
    except Exception as exc:  # pragma: no cover - import failure is an environment fault
        raise ArchiveRefused(
            f"could not import the live surface: {type(exc).__name__}: {exc}"
        ) from exc
    imported = Path(tortoise.__file__ or "").resolve()
    if not imported.is_relative_to(root):
        raise ArchiveRefused(
            f"`import tortoise` resolves to {imported}, which is NOT inside {root} — the gate "
            "would grade a different package than the manifest it read, and report CLEAN. "
            "Install this checkout (editable) or fix PYTHONPATH first"
        )
    package_files = _package_modules(root)
    return {
        "tools": {entry.name for entry in TOOL_REGISTRY},
        "methods": {
            name
            for name in dir(TortoiseSDK)
            if not name.startswith("_") and callable(getattr(TortoiseSDK, name))
        },
        "forward": set(RETIRED_USE_INSTEAD),
        "sdk_retired": set(RETIRED_SDK_METHODS) | set(REMOVED_SDK_METHODS),
        # Every module the package tree carries (`tortoise/**/*.py`), repo-relative. An
        # archived `module` symbol must not appear here: a file under `tortoise/` is
        # importable and ships in the wheel.
        "modules": package_files,
        # …and the subset of those dotted names whose OWN file carries a BODY. `live_return`
        # reads this so "the module shipped" asks the same question arm 1 asks when it decides
        # what LEFT (`_modules_with_a_body`): a scaffolding-only `__init__.py` is importable
        # but carries nothing, and counting it as the module's return made the archive route
        # arm 1 prints unusable for a module→package split (review C-F2).
        "modules_with_a_body": _modules_with_a_body(
            package_files, lambda path: _file_text(root, path)
        ),
    }


def _literal_text(node: ast.AST) -> str:
    """The text of a string literal, a concatenation of literals, or "" when not static."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_text(node.left) + _literal_text(node.right)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _imports_of_archive(root: Path) -> list[tuple[str, str]]:
    """Every product module that imports `archive` (or a submodule of it).

    Returns (repo-relative path, the imported module name). `import archive.x` /
    `from archive.x import y` / `from archive import x` are all hits; `import tortoise.archive`
    is a different (and separately reported) problem.
    """
    out: list[tuple[str, str]] = []
    for path in sorted((root / "tortoise").rglob("*.py*")):
        if path.suffix not in (".py", ".pyi"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):  # pragma: no cover - reported elsewhere
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out += [(rel, alias.name) for alias in node.names
                        if alias.name == "archive" or alias.name.startswith("archive.")]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module == "archive" or module.startswith("archive."):
                    out.append((rel, module))
            elif isinstance(node, ast.Call):
                # The DYNAMIC forms whose argument is READABLE: `importlib.import_module("x")`,
                # `__import__("x")`, or a name assembled from literals. A name built at runtime
                # cannot be seen by any static scan — that is the declared residual, stated in
                # `archive/README.md` and in the WHAT IS ENFORCED list — but the one-line
                # `import_module("archive.…")` bypass is not worth leaving open.
                callee = node.func
                name = (
                    callee.attr if isinstance(callee, ast.Attribute)
                    else callee.id if isinstance(callee, ast.Name) else ""
                )
                if name not in ("import_module", "__import__"):
                    continue
                # The SAME separator test as the static arms: `archive` or `archive.<x>`, never
                # a mere prefix. `archive_utils.tidy` is a different module that a product file
                # may import legitimately — flagging it reds a required check on a PR that
                # touched nothing archived, and the message would assert a claim the gate cannot
                # support (measured, round 11).
                # The keyword form is the DOCUMENTED spelling of `import_module(name=…)`, so
                # reading only positional arguments left the idiomatic form unseen while the
                # comment below claimed the readable class was covered (measured, round 12).
                arg = node.args[0] if node.args else next(
                    (kw.value for kw in node.keywords if kw.arg == "name"), None
                )
                text = _literal_text(arg) if arg is not None else ""
                if text == "archive" or text.startswith("archive."):
                    out.append((rel, text))
    return out


def _forward_names(root: Path) -> set[str]:
    """The `RETIRED_USE_INSTEAD` keys, read from the tree rather than imported.

    Parsed, not executed: the writer must be able to reason about ANY tree (`check(root)` already
    grades one it does not import), and an import would tie the answer to whichever `tortoise`
    the interpreter resolves.
    """
    path = root / "tortoise" / "tool_registry.py"
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a registry that does not parse is reported elsewhere
        return set()
    for node in tree.body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "RETIRED_USE_INSTEAD" for t in targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):  # pragma: no cover - non-literal map
                return set()
            return set(value) if isinstance(value, dict) else set()
    return set()


def _package_modules(root: Path) -> set[str]:
    """Every module file the shipped package tree carries, repo-relative.

    `*.py*` and not `*.py`: a `.pyi` stub under `tortoise/` is importable surface too, and the
    `module` detector's `.pyi` candidate could never match a `*.py` scan (measured in review —
    the arm was unreachable by construction).
    """
    return {
        path.relative_to(root).as_posix()
        for path in (root / "tortoise").rglob("*.py*")
        if path.suffix in (".py", ".pyi")
    }


def _anchor_violations(
    root: Path,
    symbol: str,
    rel: str,
    entry: dict,
    source_path: str,
    body: str,
    node_key: str | None = None,
) -> list[str]:
    """Does the recorded commit actually hold this frozen block?

    The sha256 arm proves file ↔ manifest; both come from the same renderer, so it cannot tell
    "the source that was removed" from "a body re-rendered under a different anchor". This arm
    reads the anchored commit and re-extracts the symbol from it with the SAME extractor, then
    compares byte-for-byte.

    ⚠ EXACT, not containment. Containment ("this text appears somewhere in the blob") is
    satisfied by a different symbol's text in the same file — a module constant, a docstring,
    a neighbouring method — so an entry whose body was replaced with any substring of the
    source passed the arm it exists for (measured in review). Only a byte comparison binds a
    body to its symbol. Where the extractor cannot name the symbol (MCP handlers are found by
    a name scan, and a dispatch-style helper's segment is not keyed by the tool), the arm falls
    back to containment — the weaker question, on the blocks that have no exact key.

    ⚠ THREE-VALUED ON PURPOSE. A clone that does not hold the object cannot distinguish a
    forged anchor from an un-fetched one, and turning that absence into a refusal would red the
    gate on a HEALTHY shallow clone (`actions/checkout` defaults to `fetch-depth: 1`, and the
    anchor is an ANCESTOR of the head, not the head). So:
      · object readable here → the block must be in it, or this is a violation;
      · object absent        → `check --anchors` reports it and refuses; plain `check` prints
        nothing (see `anchor_unverified()`, which the strict verb uses).
    That is the same shape `tools/surface_manifest.py::cut_commit_verdict` uses for provenance,
    and for the same reason.
    """
    sha = str(entry.get("archived_at") or "")
    if not _git_has_commit(root, sha):
        return []
    # ⛔ READ THROUGH THE LINK, LIKE THE WRITER DOES (round-24 review, P1 — measured). The writer
    # freezes `path.read_text()`, which FOLLOWS a symlink; reading the raw blob here returns the LINK
    # TEXT, so `expected_block` could never match the frozen body. Measured: `archive module` on a
    # symlinked carrier SUCCEEDED (`archived_from=tortoise/x/__init__.py`, body = the real code),
    # and `check` then red the row it had just written — "the recorded commit does not describe this
    # body" — with no remedy that clears it, since re-archiving reproduces it byte-identically. This
    # is the same class the `block_holds` docstring names, fixed at the writer and not at the gate.
    blob = _blob_following_links(root, sha, source_path)
    if blob is None:
        return [
            f"{symbol}: {rel}'s block cites {sha}:{source_path}, which the anchored commit does "
            "NOT hold — the commit IS present in this clone, so its absence is not a fetch gap: "
            "the anchor names a commit that never contained this code"
        ]
    expected = expected_block(
        str(entry.get("kind") or ""), symbol, source_path, blob, node_key
    )
    if expected is None and node_key:
        # ⛔ Do NOT fall through to containment here. A key that names no node in the anchored
        # commit is either a hand-edited row or a row written before block keys existed, and the
        # containment test accepts any fragment of the file — so falling through demotes the
        # strongest arm to the weakest exactly when the record is least trustworthy.
        return [
            f"{symbol}: {rel}'s block records node {node_key!r}, which does not exist in "
            f"{sha}:{source_path} — the record cannot name the code it froze"
        ]
    if expected is not None:
        if expected.rstrip() != body.strip("\n"):
            return [
                f"{symbol}: {rel}'s block from {source_path} is not what {sha}:{source_path} "
                "holds at that symbol — the anchored commit does not describe this body, so "
                "restoring it would not restore what the manifest cites"
            ]
        return []
    if body.strip("\n") not in blob:
        return [
            f"{symbol}: {rel}'s block from {source_path} is NOT in {sha}:{source_path} — the "
            "recorded commit does not describe this body, so restoring it would not restore "
            "what the manifest cites"
        ]
    return []


def expected_block(
    kind: str, symbol: str, source_path: str, source: str, node_key: str | None = None
) -> str | None:
    """The block *symbol* WOULD extract from *source*, or None when it has no exact key.

    The same extractors the writer uses, applied to a blob instead of the working tree — one
    rule for "what this symbol is", so the gate cannot drift from the writer. Returning None
    is deliberate and narrow (see `_anchor_violations`): a block the extractor cannot key has
    only the containment question available.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a blob that does not parse is reported elsewhere
        return None
    if kind == MODULE:
        return source.rstrip()
    if node_key:
        # The node the writer sliced FROM, named explicitly: `FunctionDef:<name>` for a function
        # (including a shared helper, whose name is NOT the entry symbol) or
        # `ToolDefinition:<name>` for a registry literal.
        want_kind, _, want_name = node_key.partition(":")
        for node in ast.walk(tree):
            if want_kind == "FunctionDef" and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name == want_name:
                return _segment(source, node)
            if want_kind == "ToolDefinition" and isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "ToolDefinition"
            ):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == want_name
                    ):
                        return _segment(source, node)
        return None
    if kind == SDK_METHOD:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        child.name == symbol
                    ):
                        return _segment(source, child)
        return None
    if kind == MCP_TOOL:
        if source_path == SOURCE_OF[MCP_TOOL]:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Name) and func.id == "ToolDefinition"):
                    continue
                for keyword in node.keywords:
                    if (
                        keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == symbol
                    ):
                        return _segment(source, node)
            return None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                return _segment(source, node)
        return None
    return None


@cache
def _git_has_commit(root: Path, sha: str) -> bool:
    """Is the anchored COMMIT object readable here? (Distinct from "does it hold the path".)

    Without this split, "the commit does not hold that file at all" and "this clone never
    fetched that commit" collapse into one failed `git show` — and the first, which is what a
    re-anchored body looks like, would be silently skipped.
    """
    if not _SHA_RE.match(sha or ""):
        return False
    try:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError:  # pragma: no cover - env
        return False
    return proc.returncode == 0


@cache
def _git_blob(root: Path, sha: str, path: str) -> str | None:
    """`git show <sha>:<path>`, or None when this clone cannot answer (see the docstring above)."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError:  # pragma: no cover - env
        return None
    return proc.stdout if proc.returncode == 0 else None


def anchor_unverified(root: Path | None = None) -> list[str]:
    """Entries whose anchor this clone cannot read — the strict verb's question.

    Kept separate from `file_violations` so the ordinary `check` stays honest on a shallow
    clone while `check --anchors` (full history, or a fetch) still refuses an unprovable one.
    """
    root = Path(root or ROOT)
    entries = load_manifest(root / "archive" / "MANIFEST.json")["entries"]
    out: list[str] = []
    for entry in entries:
        sha = str(entry.get("archived_at") or "")
        ref = default_branch_ref(root)
        if ref is None:
            # Answering nothing here would be the shape this module condemns: a durability arm
            # that is skipped exactly where it cannot answer. The strict verb fails closed.
            out.append(
                f"{entry.get('symbol')}: cannot name a default-branch ref in this clone — the "
                "durability of the anchor is UNPROVEN (fetch the remote, or run in a checkout "
                "that has one)"
            )
        elif _git_has_commit(root, sha):
            code, _out = _git(root, "merge-base", "--is-ancestor", sha, ref)
            if code != 0:
                out.append(
                    f"{entry.get('symbol')}: `archived_at`={sha} is not an ancestor of {ref} — "
                    "a squash merge would orphan this citation, so the anchor must name a "
                    "commit on the default branch (see `anchor_sha`)"
                )
        for row in entry.get("blocks") or []:
            path = str(row.get("path") or "")
            if not _git_has_commit(root, sha) or _git_blob(root, sha, path) is None:
                out.append(
                    f"{entry.get('symbol')}: cannot read {sha}:{path} — this clone does not "
                    "hold the anchored commit, so the frozen body's provenance is UNPROVEN "
                    "(fetch the history, or treat the entry as unjustified)"
                )
    return out


# ── violations: the surface half (pure — no filesystem) ─────────────────────────

def live_return(entry: dict, surface: dict, root: Path | None = None) -> str | None:
    """WHERE this entry's symbol is LIVE again, or `None` — the ONE definition of "came back".

    Both directions read this, because the two arms grade the same event from opposite sides:
    `surface_violations` reds an archived row whose symbol is live again, and
    `shrink_violations` (arm 2) treats a DROPPED row whose symbol is live again as the RESTORE
    it is rather than the loss it otherwise is. Two definitions of "live" would let the two
    arms disagree about the same tree — one reding a restore, the other calling it clean.
    """
    kind = entry.get("kind")
    symbol = str(entry.get("symbol") or "")
    if kind == MCP_TOOL:
        return symbol if symbol in surface["tools"] else None
    if kind == SDK_METHOD:
        return symbol if symbol in surface["methods"] else None
    if kind == MODULE:
        # A whole module archived from INSIDE the package. Its file must be GONE from the
        # package tree: `tortoise/archived/x.py` is importable as `tortoise.archived.x` and
        # ships in the wheel, which is the opposite of "not available".
        #
        # ⛔ CONTENT, not the path set — the same rule arm 1 uses to decide what LEFT. A module
        # split into a package (`x.py` → `x/__init__.py` carrying only a docstring, the body in
        # `x/sub.py`) leaves `x/__init__.py` importable while `x`'s own body moved; grading that
        # as "the module came back" made the archive route arm 1 PRESCRIBES (archive the old
        # path, then delete it again) refused by this arm after it was carried out — one
        # legitimate refactor, two arms, no in-CI waiver, so the red had no route out (review
        # C-F2). `modules_with_a_body` is absent only on a synthetic surface built without it,
        # where the old path-set answer is the only one available.
        base = symbol.replace(".", "/")
        carriers = surface.get("modules_with_a_body")
        # ⛔ A `.pyi` IS SURFACE, AND THIS IS THE RECORDED RULE (round-15 review, P2 — DECLINED).
        # A round-15 reviewer proposed skipping the `.pyi` candidate unless the row itself froze a
        # stub, on the ground that arm 1b's `_head_carries_runtime_body` calls a `.pyi` "not
        # runtime statements". The two questions are DIFFERENT and both answers are recorded:
        #   * "does the module's RUNTIME BODY still exist?" -> no, a stub is not a body (arm 1b);
        #   * "is the symbol still declared in the shipped package?" -> YES, and that is what this
        #     function answers: `_package_modules` scans `*.py*` on purpose, and
        #     `test_a_tortoise_archive_stub_module_is_reported` plus
        #     `test_the_package_module_scan_sees_pyi_stubs` pin it ("a `.pyi` stub under the shipped
        #     package is importable surface, so the scan must see it"). Adopting the finding would
        #     reverse that rule and un-red a shape the operator is meant to fix by deleting the
        #     stub — which IS the green route `archive/README.md` gives, so the finding's "no green
        #     route" does not hold either.
        for candidate in (
            f"{base}.py",
            f"{base}/__init__.py",
            f"{base}.pyi",
            f"{base}/__init__.pyi",
        ):
            if candidate not in surface.get("modules", set()):
                continue
            if carriers is not None and symbol not in carriers:
                continue  # scaffolding only: the name resolves, the module's BODY is not here
            if root is not None:
                text = _file_text(root, candidate)
                if text is None:
                    # ⛔ A CARRIER THAT EXISTS AND READS AS NOTHING IS NOT "COMING BACK"
                    # (round-23 review, P2 — measured). `read_text` follows a symlink, so a
                    # DANGLING link answers `None` here — and the old `text is not None and ...`
                    # let it fall through to `return candidate`, i.e. "REAPPEARED on the live
                    # surface" for a file holding no code, while arm 1b graded that same file
                    # `MODULE BODY REMOVED` in the same run. A correct verdict beside a false
                    # stated fact, the class this file keeps having to remove.
                    # ⛔ AND A PATH THAT IS ABSENT ALTOGETHER KEEPS THE OLD ANSWER (measured on
                    # the round-18 positive control): the surface set is then the only evidence
                    # there is, and "cannot read it" must not become "it is not live" — that
                    # direction would let a real REAPPEARED row go green. Only an entry that is
                    # ON DISK and holds nothing is demoted.
                    if not os.path.lexists(root / candidate):
                        return candidate
                    continue
                if not _init_carries_a_body(text):
                    # A leftover EMPTY / docstring-only file is not the module coming back: arm
                    # 1b just called it a removal, and crediting it here would red the very
                    # remedy arm 1b prints (round-8 review).
                    continue
            return candidate
    return None


def surface_violations(
    entries: list[dict], surface: dict, root: Path | None = None
) -> list[str]:
    """Every way an archived symbol can be back on the live surface, or mis-recorded.

    Pure on purpose: `tests/test_archive.py` calls it with a synthetic surface to prove
    the detector FIRES. A gate nobody has seen fire is indistinguishable from one that
    cannot.
    """
    # `tools`/`methods`/`modules` are read through `live_return` below — ONE definition of
    # "came back" for both directions, so the two arms cannot disagree about the same tree.
    forward = surface["forward"]
    sdk_retired = surface["sdk_retired"]
    out: list[str] = []
    for entry in entries:
        kind = entry.get("kind")
        symbol = str(entry.get("symbol") or "")
        if kind not in KINDS:
            out.append(f"{symbol or '<no symbol>'}: unknown kind {kind!r} (expected one of {KINDS})")
            continue
        back = live_return(entry, surface, root)
        if kind == MCP_TOOL:
            if back is not None:
                out.append(
                    f"{symbol}: REAPPEARED on the live MCP surface — an archived tool must not "
                    "be in TOOL_REGISTRY (archive/README.md, 'not available')"
                )
            if symbol in forward:
                out.append(
                    f"{symbol}: is a RETIRED_USE_INSTEAD forward entry — a forward entry is "
                    "STILL SERVED, so it is inside the product and must not be archived; "
                    "these are different mechanisms and must not hold the same name"
                )
        elif kind == SDK_METHOD:
            if back is not None:
                out.append(
                    f"{symbol}: REAPPEARED as a public TortoiseSDK method — an archived method "
                    "must not be importable from the product package"
                )
            if symbol not in sdk_retired:
                out.append(
                    f"{symbol}: archived but absent from RETIRED_SDK_METHODS / "
                    "REMOVED_SDK_METHODS — a caller would then hit a bare AttributeError "
                    "instead of the recorded departure, so the removal is not accounted for"
                )
        elif back is not None:
            out.append(
                f"{symbol}: REAPPEARED as an importable module in the shipped package "
                f"({back}) — archived code must not ship"
            )
    return out


# ── violations: the files half ─────────────────────────────────────────────────

def _module_metadata(path: Path) -> dict:
    """The `ARCHIVED_*` module-level constants of an archived module.

    Parses only the HEADER — everything before the first verbatim banner. The frozen blocks
    below it are verbatim source in their ORIGINAL indentation (a class method keeps its four
    spaces, decorators included), so parsing the file as one module would raise
    `IndentationError` on the very thing the archive exists to preserve.
    """
    try:
        text = path.read_text(encoding="utf-8")
        header = text.split(VERBATIM_BANNER, 1)[0]
        tree = ast.parse(header, filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ArchiveRefused(
            f"{path} does not parse as Python — the ARCHIVED_* header must be valid: {exc}"
        ) from exc
    found: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in METADATA_KEYS:
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except ValueError as exc:
                    raise ArchiveRefused(
                        f"{path}: {target.id} is not a literal — the archive metadata is read "
                        "by the gate and must be a constant"
                    ) from exc
    return found


def frozen_blocks(text: str) -> list[tuple[str, str]]:
    """Split an archived file into `(source path, frozen body)` pairs.

    The writer emits one `VERBATIM_BANNER` line per frozen block, a blank line, the block
    verbatim, then a blank line. Parsing the SAME shape back is what makes the body
    verifiable: a file that keeps only its banner and its metadata would otherwise read as
    an archive while holding no code (measured — a banner-presence check alone passed).
    """
    blocks: list[tuple[str, str]] = []
    path: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith(VERBATIM_BANNER):
            if path is not None:
                blocks.append((path, "\n".join(body).strip("\n")))
            path = line[len(VERBATIM_BANNER):].removesuffix(" ──").strip()
            body = []
            continue
        if path is not None:
            body.append(line)
    if path is not None:
        blocks.append((path, "\n".join(body).strip("\n")))
    return blocks


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Code points that pass every category test above and still render as nothing (round-19 P3).
_BLANK_RENDERING = frozenset("\u115f\u1160\u3164\uffa0\u2800")


def _is_a_stated_reason(text: str) -> bool:
    """Does *text* actually SAY something a reader can read? (round-18 review, P2 — measured)

    `str.strip()` answers a different question. ZWSP (`\u200b`), LRM (`\u200e`) and BOM
    (`\ufeff`) are not `isspace()`, so each survived `.strip()` and was ACCEPTED as a removal's
    WHY while rendering as nothing — a record that reads as "a departure somebody checked" and
    says nothing. A reason counts only when one of its characters is in a category that renders:
    neither a separator (`Zs`/`Zl`/`Zp`), a control (`Cc`) nor a format/zero-width character
    (`Cf`).

    ⛔ WHAT THIS IS, EXACTLY (round-19 review, P3 — measured): it refuses blank, whitespace,
    control and zero-width/format characters. It does NOT test whether a character "renders" —
    the Hangul fillers (`U+115F`, `U+3164`, `U+FFA0`) are `Lo` and `U+2800` is `So`, and they
    render as nothing in every common font — so those are DENIED explicitly rather than described
    away. An emoji is accepted: it renders, and `"x"` passes too. This is a floor against the
    shapes that were measured, not a claim about legibility, and the next caller wiring it into a
    new site should know that.
    """
    return any(
        not ch.isspace()
        and unicodedata.category(ch) not in {"Cc", "Cf", "Zs", "Zl", "Zp"}
        and ch not in _BLANK_RENDERING
        for ch in text
    )


def file_violations(entries: list[dict], archive_dir: Path | None = None,
                    root: Path | None = None) -> list[str]:
    """Every way the files on disk and the manifest index disagree."""
    root = Path(root or ROOT)
    archive_dir = Path(archive_dir or root / "archive")
    out: list[str] = []
    indexed: set[str] = set()
    for entry in entries:
        symbol = str(entry.get("symbol") or "")
        rel = str(entry.get("file") or "")
        if not rel:
            out.append(f"{symbol}: manifest row carries no `file`")
            continue
        path = root / rel
        indexed.add(rel)
        # Containment: a manifest row must name a file INSIDE `archive/`. A `..` component
        # (`archive/mcp-tools/../../tortoise/x.py`) resolves into the shipped package, where
        # the file is importable, and the orphan scan below cannot see it — measured.
        if not rel.startswith("archive/") or ".." in Path(rel).parts:
            out.append(
                f"{symbol}: `file`={rel!r} is not a path inside archive/ — archived code must "
                "live under archive/, never inside the shipped package"
            )
            continue
        if not path.exists():
            out.append(f"{symbol}: manifest names {rel}, which does not exist")
            continue
        text = path.read_text(encoding="utf-8")
        meta = _module_metadata(path)
        for key, field in METADATA_FIELDS:
            if meta.get(key) != entry.get(field):
                out.append(
                    f"{symbol}: {rel} says {key}={meta.get(key)!r} but the manifest says "
                    f"{field}={entry.get(field)!r} — the index and the archived code drifted"
                )
        # The file must actually CARRY the frozen source, and it must be the SAME source:
        # a body lost in a merge, or hand-edited after the fact, would otherwise read green
        # and "recoverable" would be false. So the blocks are re-parsed and hashed against
        # the manifest, not merely counted.
        blocks = frozen_blocks(text)
        if not blocks:
            out.append(
                f"{symbol}: {rel} carries no `{VERBATIM_BANNER.strip()}` block — the frozen "
                "source is missing, so this is metadata without an archive"
            )
        else:
            empty = [path for path, body in blocks if not body.strip()]
            if empty:
                out.append(
                    f"{symbol}: {rel} has an EMPTY frozen block for {', '.join(empty)} — the "
                    "banner is there but the code is not"
                )
            recorded = entry.get("blocks")
            if not isinstance(recorded, list) or len(recorded) != len(blocks):
                out.append(
                    f"{symbol}: {rel} carries {len(blocks)} frozen block(s) but the manifest "
                    f"records {len(recorded) if isinstance(recorded, list) else 'none'} — the "
                    "index cannot say what this file holds"
                )
            else:
                for (path, body), row in zip(blocks, recorded, strict=True):
                    if row.get("path") != path or row.get("sha256") != _sha256(body):
                        out.append(
                            f"{symbol}: {rel}'s block from {path} does not match the manifest's "
                            "sha256 — the archived code drifted away from the source that was "
                            "removed, so restoring it would restore something else"
                        )
                        continue
                    # The digests prove FILE ↔ MANIFEST agreement, and both are produced by the
                    # same renderer — so on their own they cannot tell "the source that was
                    # removed" from "a body re-rendered under a changed anchor". Only the
                    # recorded commit can. This is the check that makes `archived_at` a
                    # citation rather than a number (measured in review: an entry re-anchored
                    # to a commit that does not hold its body passed every other arm).
                    out += _anchor_violations(
                        root, symbol, rel, entry, path, body, row.get("node")
                    )
        anchor = str(entry.get("archived_at") or "")
        if not _SHA_RE.match(anchor) or anchor == "0" * 40:
            out.append(
                f"{symbol}: `archived_at`={entry.get('archived_at')!r} is not a full commit "
                "sha (or is the all-zero placeholder) — the restore anchor was not recorded "
                "(a guess is not a record)"
            )
        if not _is_a_stated_reason(str(entry.get("reason") or "")):
            out.append(
                f"{symbol}: no `reason` — a reader cannot tell why it was removed (a blank, "
                "whitespace-only or invisible-character reason is not one)"
            )

    if archive_dir.exists():
        for path in sorted(archive_dir.rglob("*.py")):
            rel = str(path.relative_to(root))
            if rel not in indexed:
                out.append(f"{rel}: archived code that no manifest row indexes — orphan")
        for path in sorted(archive_dir.rglob("__init__.py")):
            rel = str(path.relative_to(root))
            out.append(
                f"{rel}: archive/ must carry no `__init__.py` — the archive is a READABLE "
                "SOURCE TREE, not an importable package"
            )
    return out


def package_violations(root: Path | None = None) -> list[str]:
    """Every way `archive/` could become reachable through the product package."""
    root = Path(root or ROOT)
    out: list[str] = []
    if (root / "tortoise" / "archive").exists():
        out.append(
            "tortoise/archive exists — code under the shipped package is importable as "
            "`tortoise.<mod>`; archived code must live outside it"
        )
    if (root / "tortoise" / "archived").exists():
        out.append(
            "tortoise/archived exists — this is the PRE-MECHANISM archive location, inside the "
            "shipped package and therefore importable. Archived code belongs in the top-level "
            "`archive/` tree (`archive/README.md`); migrate the module and delete the directory"
        )
    # ⛔ The OTHER direction: removed BEHAVIOUR must not re-enter the product. `archive` is a
    # PEP 420 namespace package reachable from a repo checkout, so `from archive.… import x` in a
    # live module WORKS at runtime — and the suite runs from the repo root, so every other gate
    # stayed green while a live method called archived code (measured, review round 9). Parsed,
    # not grepped: a mention in prose is not an import (the rule `_mcp_handlers` already applies
    # to docstrings).
    for rel, name in sorted(_imports_of_archive(root)):
        out.append(
            f"{rel} imports `{name}` — archived code must not be reachable from the product; "
            "that is the difference between 'removed' and 'still running'"
        )
    # ⛔ NO `find_spec("tortoise.archive")` ARM HERE, deliberately. Every other arm of this
    # function reads a path under `root`; an import probe answers about whatever the AMBIENT
    # interpreter resolves, so with any other tree it grades a different package than the one it
    # was asked about — the same mistake `live_surface(root)` was fixed for, one layer down
    # (review cycle 4). It is also redundant once that fix is in place: `live_surface(root)`
    # refuses unless `import tortoise` lives inside `root`, so a resolvable `tortoise.archive`
    # implies `tortoise/archive` exists, which the first arm already reports. The real-repo
    # property ("no importable tortoise.archive") is asserted directly by
    # `tests/test_archive.py::test_the_archive_is_not_importable_from_the_product_package`.
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:  # pragma: no cover - config fault
        raise ArchiveRefused(f"could not read pyproject.toml: {exc}") from exc
    include = (pyproject.get("tool", {}).get("setuptools", {}).get("packages", {})
               .get("find", {}).get("include", []))
    # A GLOB, not a prefix: `arch*` and `a*` both match `archive`, and the prefix test let them
    # through while claiming the opposite (measured, review cycle 5).
    if any(fnmatch.fnmatchcase("archive", pat) or pat == "*" for pat in include):
        out.append(
            f"pyproject.toml `packages.find.include` matches the archive ({include}) — an "
            "archived module would ship in the wheel, so it would be importable after install"
        )
    return out


def manifest_violations(manifest: dict, archive_dir: Path | None = None) -> list[str]:
    """The manifest file itself: present, the right schema, and no symbol archived twice."""
    archive_dir = Path(archive_dir or MANIFEST_PATH.parent)
    path = archive_dir / "MANIFEST.json"
    if not path.exists():
        return [f"{path.name}: no `archive/MANIFEST.json` — the archive has no record of itself"]
    out: list[str] = []
    if manifest.get("schema") != SCHEMA:
        out.append(
            f"archive manifest schema {manifest.get('schema')!r} != {SCHEMA} — a reader cannot "
            "know how to read the rows"
        )
    seen: set[tuple[str, str]] = set()
    for entry in manifest.get("entries") or []:
        key = (str(entry.get("kind")), str(entry.get("symbol")))
        if key in seen:
            out.append(f"{key[1]}: indexed twice ({key[0]}) — a symbol is archived once")
        seen.add(key)
    return out


def index_violations(entries: list[dict], root: Path | None = None) -> list[str]:
    """The committed `INDEX.md` must equal the regenerated view of the manifest."""
    root = Path(root or ROOT)
    path = root / "archive" / "INDEX.md"
    expected = render_index({"schema": SCHEMA, "entries": entries})
    if not path.exists():
        return [f"{path.relative_to(root)}: missing — run `tools/archive_surface.py index`"]
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        return [
            f"{path.relative_to(root)}: STALE — it is a generated view of MANIFEST.json; "
            "run `uv run python tools/archive_surface.py index`"
        ]
    return []


# ── the SHRINK direction: the removals nothing else can see (#12402, #12291) ────
#
# Every arm above iterates the MANIFEST's own entries, so it answers one direction: an
# archived symbol that came BACK is red. That leaves the other direction ungraded, and it is
# the one that loses code:
#
#   #12402  a WHOLE MODULE deleted from `tortoise/` with no `module` entry. `module` is a
#           first-class kind (`KINDS`) and the one committed archive entry IS a module, but
#           the surface baseline carries no module rows, so `surface_manifest.cut`'s removal
#           gate compares nothing for it and `removal_report` refuses the class by name
#           rather than crash (`ARCHIVE_KIND_BY_CLASS`). A module is the largest removable
#           unit and it was the one unit no gate could see.
#   #12291  an entry DROPPED from `archive/MANIFEST.json` (or rewritten in place). `check`
#           grades the entries PRESENT; an entry that is gone is not a row it can compare,
#           so deleting the index row turned removed code into code that exists nowhere —
#           green archive gate, green surface gate, green cut.
#
# WHY THE BASE REF AND NOT A SAME-TREE CHECK. Both artifacts are authored by the PR under
# test, so a check over them can only ask "is this consistent with itself" — and a splicer
# edits both sides consistently. The reference has to be outside the diff: the merge base.
# This is the same property supply-chain transparency logs get from being append-only
# (Sigstore's Rekor: "an immutable, tamper-resistant ledger of metadata generated within a
# software project's supply chain", https://docs.sigstore.dev/logging/overview/), and the
# same reason TUF makes a version counter MONOTONIC: a client that accepts a lower version
# than the one it already trusts has been rolled back, so the update is aborted and the
# rollback attack reported (TUF 1.0 §5.3, and §1.5.2's "Rollback attacks. An attacker
# cannot trick clients into installing software that is older than that which the client
# previously knew to be available"). Git history is this repo's append-only log; the merge
# base is the last version this tree is allowed to disagree with, and a removal that lives
# only in the new version must carry its own record (an archive row, or a stated waiver).
#
# ⛔ WHY MERGE-BASE AND NOT THE BASE TIP: on `refs/pull/N/merge` the merge base IS the base
# tip, while on a feature branch it is the fork point — and using the base TIP on a stale
# branch attributes MAIN's own removals to the PR (the #7628 class measured in MEMORY.md's
# `[ci-diff-base]` note: every commit the base branch gained after the fork was attributed to
# the PR). Merge-base is the one ref that means "what THIS change removed" in both shapes.

#: The fields that carry a row's CLAIM: which code, from where, at which commit, with which
#: bytes. `batch`/`reason` are deliberately NOT here — a prose correction is not a rewrite of
#: the record's evidence, and gating it would refuse an honest typo fix for no gain.
CLAIM_FIELDS = ("kind", "symbol", "file", "archived_from", "archived_at", "sources", "blocks")

#: What to do about a module that left the tree — printed INSIDE the violation, because a gate
#: whose remedy is elsewhere is a gate whose remedy is not read.
MODULE_ROUTE = (
    "REMEDY (archive/README.md, 'Archive a whole module'): restore it from the base FIRST "
    "(`git checkout <base-sha> -- <path>`) — the writer freezes the source it reads from the "
    "WORKING TREE and refuses without it, so archiving AFTER the deletion cannot work — then "
    "`uv run python tools/archive_surface.py archive module <dotted.path> --batch <slug> "
    "--reason <text>`, then delete it again in the same batch. A file that was RENAMED or "
    "MOVED is the same case: the old path is the symbol that left the product, so archive the "
    "old path and treat the new one as new code. If the removal is already recorded somewhere "
    "this gate cannot read, state it on the command line "
    "(`--allow-archive-shrink module:<dotted.path> --allow-archive-shrink-reason <why>`) and "
    "say why in the PR body."
)


def module_name(path: str) -> str:
    """`tortoise/a/b.pyi` → `tortoise.a.b` — the dotted name an archive row records.

    `__init__` collapses onto its package (`tortoise/a/__init__.py` → `tortoise.a`), which is
    what `archive module tortoise.a` names and what `tortoise.archived.search_legacy` shows in
    the committed entry.
    """
    for suffix in (".pyi", ".py"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    parts = path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _init_carries_a_body(text: str) -> bool:
    """Does this `__init__.py` carry anything of its OWN? (review F2, round 3)

    A module body is CONTENT. `tortoise/__init__.py` is 406 lines of the product's public API,
    so deleting it while `tortoise/sdk.py` survives removes everything a caller reaches through
    `import tortoise` — while the dotted name still resolves, because the directory still holds
    importable siblings. A comparison over PATHS therefore cancels that deletion, which is why
    "the package still imports" is the wrong question to ask of `__init__.py`.

    Only an `__init__.py` with no statements of its own — empty, whitespace, a docstring
    alone, or a bare no-op (any inert expression statement: `pass`, `...`, the name `Ellipsis`,
    a bare `None`/`0`) — is pure scaffolding, where deleting it while siblings survive removes
    nothing. Any statement (an import, a re-export, `__all__`, an assignment, a function) is
    content.
    Unparseable text is treated as carrying NOTHING: a body that no longer parses holds no
    statements a caller can reach, so the arm reports the removal rather than reading the
    garbage as content and cancelling it. (The previous version returned True here and called
    that "fail CLOSED toward the violation" — it was the GREEN direction: a 40-function module
    rewritten to `this is not python {{{` graded green, measured in round-5 review.)
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    body = list(tree.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # a docstring alone is prose about scaffolding, not content
    # ⛔ A NO-OP STATEMENT IS NOT CONTENT EITHER (round-16 P2, widened in round-17 P1 — measured
    # in BOTH directions, then measured again ONE SPELLING OVER). A file reduced to `pass` or `...`
    # carries nothing a caller can reach, which is exactly the README's test ("the name no longer
    # resolves to any code"). Counting them as a body was a false PASS one branch over from the
    # docstring hole: the WRITER accepted `pass` as the module's source of record
    # (`archived_from = tortoise/x.py`) and `shrink_violations` then credited the row on its
    # EXISTENCE — every arm green, the body in no file under `archive/`. The SAME predicate
    # over-blocked the mirror shape: a `pass`-only `__init__.py` read as CONTENT, so deleting a
    # scaffolding package went RED while deleting a docstring-only one was green.
    #
    # ⛔ ROUND-16 FIXED TWO SPELLINGS AND LEFT THE CLASS OPEN (round-17 P1, measured): `Ellipsis`
    # is the same object spelled as a NAME, and `None` / `0` / `NotImplemented` / `frozenset()` are
    # equally bare — every one of them still answered "carries a body", so `check --base-ref`
    # printed OK while the base's four functions were in no file under `archive/`. An enumeration
    # of spellings IS the defect. So ask the language rule instead, which closes the class:
    # a top-level expression STATEMENT has no effect unless it calls, awaits, yields, or binds a
    # named expression. Everything else — a bare constant (the docstring above included), a bare
    # name, an arithmetic expression — is dead code and cannot be the source of record.
    # Still only TOP-LEVEL: a `def`/`class`/`if` whose own body is `pass` is a statement, and a
    # `.pyi` stub's `def f() -> int: ...` is a FunctionDef.
    _effectful = (ast.Call, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)
    # ⛔ THE RULE STOPS AT EXPRESSION STATEMENTS — a BINDING is content, by decision (round-20
    # review, P2-4). An earlier draft of this comment claimed the binding rule as adopted; it is
    # DECLINED, and the reason is in `_inert` below. Do not read this block as a closed hole.
    def _inert(node: ast.stmt) -> bool:
        if isinstance(node, ast.Pass):
            return True
        if isinstance(node, ast.Expr):
            return not any(isinstance(sub, _effectful) for sub in ast.walk(node.value))
        # ⛔ A BINDING IS CONTENT, EVEN A CONSTANT ONE (round-19 P2, DECLINED with evidence).
        # The review proposed treating `X = 1` as inert so a module truncated to one assignment is
        # a removal. Measured, that rule contradicts the repo's own pinned rule and breaks 16 rows
        # (treating `Assign`/`AnnAssign`/`Import`/`ImportFrom` as inert reds 16 in `tests/test_archive.py`):
        # a module whose whole body is `VALUE = 1` EXPORTS a name a caller reaches
        # (`from mod import VALUE`), so "the name no longer resolves to any code" — the archive's
        # stated test — is false for it, and `test_a_module_split_into_a_package_can_be_archived`
        # plus fifteen other rows (sixteen in all, measured) depend on it being content. A module that keeps ONE binding is a
        # module that still resolves; the 40 functions are lost, and the shrink gate grades
        # SYMBOLS, not proportions. Widening it here would false-block every constants-only module.
        # Not adopted. ⚠️ The residual is REAL and NOT closed: a module reduced to `X = 1`,
        # `X: int`, `import os` or `class X: pass` — and the newer variant where the emptied
        # module's only surviving carrier is a file created in the same commit holding an unrelated
        # statement — is graded green with the body in no file under `archive/`. The rule this
        # function implements is a per-SYMBOL existence test, not a proportion test, so closing it
        # needs a different arm (e.g. "the surviving carrier must carry the base's top-level
        # names"), not another inertness spelling. Recommended over adopting it blind here.
        return False

    body = [node for node in body if not _inert(node)]
    return bool(body)


def _modules_with_a_body(paths, read) -> set[str]:
    """The dotted names whose OWN module file carries a body on this side.

    Deliberately NOT `_dotted_modules`' ancestor expansion: adding every ancestor on both sides
    is what made a 406-line `__init__.py` body disappear (review F2). A name is carried HERE if
    this side holds a file for it, and a scaffolding-only `__init__.py` carries nothing at all,
    so it cancels the way a namespace-package cleanup should. `read` returns `None` when the
    side cannot answer, and an unreadable `__init__.py` is counted as content (fail closed).
    """
    out: set[str] = set()
    for path in paths:
        if path.endswith("/__init__.py"):
            text = read(path)
            if text is not None and not _init_carries_a_body(text):
                continue
        out.add(module_name(path))
    return out


#: How many SYMLINK hops `_blob_following_links` will follow before answering "unreadable". No
#: shape in this repo comes near it; the bound exists so a malformed chain cannot be walked forever
#: and so a chain that IS truncated answers `None` rather than a plausible-looking link text.
_LINK_HOPS = 8

#: Two failures a link read can have, and they are NOT the same answer:
#: `_DANGLING` — the target does not exist at that commit **and the ask reached the tree**, so the
#: carrier provably holds no code;
#: `_UNREADABLE` — an exhausted hop bound, a target that is ABSOLUTE or leaves the repo root, or a
#: mode that cannot be read at all: nothing is known, so the caller must fail CLOSED (never cancel a
#: removal). ⛔ A link LOOP is neither — it answers the peer's LINK TEXT, because a loop holds no
#: code (measured; an earlier draft of this comment called it `_UNREADABLE`, which the docstring
#: below contradicts — round-25 review, P2). Returning `None` for `_DANGLING` and `_UNREADABLE`
#: alike is what let a 7-link chain to real code grade GREEN (round-24 review, measured).
_DANGLING = ""
_UNREADABLE: str | None = None


def _blob_following_links(root: Path, sha: str, path: str) -> str | None:
    """The commit's text for *path*, following a SYMLINK to its target in the same commit.

    ⛔ THE CARRIER SETS READ RAW BLOBS (round-20 review, P1 — measured). `_content_or_link` fixed the
    per-carrier question, but `_modules_with_a_body` still called `_git_blob`, which returns the
    LINK TEXT for mode `120000`. So a package whose `__init__.py` was a symlink to the module's real
    code never entered `base_modules` at all — and deleting the package AND the link target was
    GREEN in every arm with the body in no file under `archive/`, i.e. the central promise broken by
    choosing a FILENAME (`alias.py` → RED, `x/__init__.py` → GREEN).

    ⛔ FOLLOW THE WHOLE CHAIN, NOT ONE HOP (round-23 review, P1 — measured). One hop was not
    enough, and the excuse this docstring used to make for it was FALSE: for
    `x/__init__.py → ../../shared/alias.py → impl.py` the read returned the SECOND LINK'S TEXT
    (`"impl.py"`), which parses as an inert expression, so `tortoise.x` never entered
    `base_modules` — and because the final target lived OUTSIDE `tortoise/`, `_package_paths_at`
    never scanned it either. Deleting the package, the alias AND the target was GREEN in every arm
    (measured on a probe commit: `[]`), while the same code whose final target sat under
    `tortoise/` gave two `MODULE REMOVED` rows. The old sentence — "the body is never silently
    lost; only the package name stops being blamed" — held only for chains ending INSIDE the
    package, which is the shape least likely to be met.

    ⛔ A CYCLE ANSWERS THE PEER'S LINK TEXT — not `None`, which an earlier draft of this docstring
    claimed before it was measured (round-20 VGATE). A self-loop resolves
    to the file's own link text and a two-cycle to the other link's, both of which parse as inert
    expressions: the module simply does not enter the carrier set, exactly as it did before this
    function existed. That is the SAFE direction — the set answers "did this carrier hold a body",
    and a link loop holds no code. A chain longer than `_LINK_HOPS` links answers `_UNREADABLE`,
    and a link that DANGLES answers `_DANGLING` — a KNOWN reading ("holds no code"), not the
    unknown one. The sentence that stood here said BOTH answered `None`, which contradicted the
    fix (round-25 review, P3 — measured: a dangling link reads `''`, `is _DANGLING` True).

    ⛔ AND `_UNREADABLE` IS WIDER THAN THAT BOUND (round-25 review, P2/P3 — measured). A target
    that is ABSOLUTE or escapes the repo root, and a mode that cannot be read at all, are ALSO
    `_UNREADABLE`: nothing is known about either, so neither may cancel a removal. Only a target
    git resolved nothing for, INSIDE the tree, is `_DANGLING`.

    No depth bound is reached by any shape this repo has, and a bound that IS reached must not
    invent a reading by returning the last link's text.
    """
    text = _git_blob(root, sha, path)
    if text is None:
        return None
    seen = {path}
    for _ in range(_LINK_HOPS + 1):  # +1 so the FINAL target's mode is tested, not assumed
        mode = _blob_mode(root, sha, path)
        # ⛔ AN UNANSWERABLE MODE IS `_UNREADABLE`, NOT "NOT A LINK" (round-25 review, P3 —
        # measured). `_blob_mode_at` answers `""` when `ls-tree` fails, and testing
        # `"".startswith("120")` read that as "regular file" and returned the LINK TEXT — the very
        # "plausible-looking link text" this docstring forbids inventing — while `_content_or_link`
        # reads the same `""` the other way (`if not mode: return True`, fail closed toward the
        # removal). One question, one polarity at both sites.
        if not mode:
            return _UNREADABLE  # could not ask: nothing is known, so never cancel a removal
        if not mode.startswith("120"):
            return text
        # ⛔ NORMALISE BEFORE THE ASK (round-21 review, P1 — measured). A relative link target
        # (`../../shared/impl.py`) concatenated onto the carrier's directory yields
        # `tortoise/x/../../shared/impl.py`, which `git show <sha>:<path>` REFUSES with
        # `fatal: path … exists on disk` — so the read answered `None`, which the carrier tests
        # treat as "unreadable, never cancel a removal". `posixpath.normpath` collapses the `..`
        # segments before the ask.
        target = posixpath.normpath((Path(path).parent / text.strip()).as_posix())
        # ⛔ A TARGET THAT LEAVES THE REPO IS NOT A FACT ABOUT THE CODE (round-25 review, P2 —
        # measured). The comment above used to end "…the result never escapes the repo root because
        # `git show` refuses an absolute or outside path anyway", and that refusal is exactly the
        # problem: this function then mapped it to `_DANGLING`, i.e. "provably holds no code", which
        # is a GUESS where it claims a reading. Measured: `tortoise/x/__init__.py ->
        # /home/…/shared/impl.py` dropped the module out of `base_body_modules`, so deleting the
        # package AND its real target graded `[]` (the byte-identical RELATIVE link reds — the
        # verdict decided by the link's spelling again), and the anchor then refused the commit that
        # really holds the code. Only "git resolved nothing for a path inside the tree" is
        # `_DANGLING`; anything the read cannot address at all is `_UNREADABLE`.
        if target.startswith("/") or target == ".." or target.startswith("../"):
            return _UNREADABLE
        if target in seen:
            return text  # a loop holds no code: the peer's link text, as measured
        seen.add(target)
        resolved = _git_blob(root, sha, target)
        if resolved is None:
            return _DANGLING  # dangles: it holds no code, and that is a KNOWN reading
        path, text = target, resolved
    # ⛔ A CHAIN AT THE BOUND IS UNKNOWN, NOT EMPTY (round-24 review, P1 — measured). This used to
    # answer `None`, which `_content_or_link` read as "not the module's code" while
    # `_modules_with_a_body` read the SAME `None` as content — two polarities on one datum, and the
    # cancelling one decided the verdict. Measured: a 6-link chain deleted was RED, a 7-link chain
    # to real code was GREEN with the body in no file under `archive/`. `_UNREADABLE` is the
    # fail-closed reading for both askers; `_DANGLING` is the one case where "holds no code" is a
    # fact rather than a guess.
    return _UNREADABLE


#: A FULL object name is immutable, which is the whole reason it is a legal cache key; anything
#: else (a ref, an abbreviation) can move underneath the cache.
_IMMUTABLE_OBJECT = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _commit_sha(root: Path, rev: str) -> str:
    """The full sha *rev* names, so a cache key can never be a MOVING ref.

    ⛔ A REF IS NOT AN IMMUTABLE TREE (round-23 review, P2 — measured). `_blob_mode_at` caches on
    `(root, sha, path)` on the strength of "a commit's tree is immutable" — true of a SHA, FALSE of
    `HEAD`: commit a mode change in the same process and the cached answer still describes the old
    tree (measured: `100644` returned for a path `ls-tree HEAD` had since made `120000`). No caller
    in THIS tool passes a ref (`resolve_base` returns a merge-base sha), which is exactly why the
    miss must be structural instead of a property every future caller has to remember. A full sha
    costs no spawn. ⛔ NOT `@cache`d ITSELF (measured while writing this): memoising `"HEAD"` here
    only moves the staleness one level up — the ref would resolve once and answer that sha forever.
    The hot path is 307 calls with a FULL sha, which the regex answers without spawning git.
    """
    if _IMMUTABLE_OBJECT.match(rev):
        return rev
    code, out = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return out.strip() if code == 0 and out.strip() else rev


def _blob_mode(root: Path, sha: str, path: str) -> str:
    """The commit's file mode for *path* (`""` when the tree has no such entry).

    Resolves the rev first (`_commit_sha`) so the cache below is keyed on an IMMUTABLE object, not
    on whatever spelling the caller happened to hold.
    """
    return _blob_mode_at(root, _commit_sha(root, sha), path)


@cache
def _blob_mode_at(root: Path, sha: str, path: str) -> str:
    """The cached read behind `_blob_mode` — `sha` MUST be a full object name.

    ⛔ CACHED BECAUSE THE SAME QUESTION IS ASKED THREE TIMES (round-22 VGATE, measured). Reading
    the mode before the text asked for it 924 times per run to answer for 307 carriers —
    `_carrier_carries_a_body` is re-asked from `base_body_modules`, `base_runtime_body_modules` and
    the per-module carrier lookups — and on a loaded box each git spawn costs hundreds of
    milliseconds. A commit's tree is immutable, so the answer is cacheable on `(root, sha, path)`,
    which turns that into 307 calls plus 617 hits (measured, 3.01 asks per key).
    """
    code, out = _git(root, "ls-tree", sha, "--", path)
    if code != 0 or not out.strip():
        return ""
    return out.split(None, 1)[0]


def _content_or_link(root: Path, sha: str, path: str, source: str | None) -> bool:
    """Is this base carrier the module's code — counting a SYMLINK as code? (round-19 P2, measured)

    `_git_blob` on a symlink returns the LINK TEXT (mode `120000`), so the body test parsed
    `real_impl.py` as Python — an `Expr(Attribute)`, i.e. "dead code" — and answered "no body":
    deleting a symlinked module was GREEN while the identical regular file was RED. The docstring
    above promises that an UNREADABLE carrier never cancels a removal; this is the harder case, a
    reader that answers plausibly and wrongly, so the mode is asked directly.

    ⛔ ASK THE MODE **FIRST** (round-22 review, P1 — measured). The content test used to run above the
    `ls-tree` read, so any LINK TEXT that happens to parse as a non-inert statement short-circuited
    it: `x_init.py -> impl()` is `ast.Expr(Call)` and `y_init.py -> impl = 1` is `ast.Assign`, both
    "content", and the target was never consulted — an UNCHANGED dangling link graded `MODULE BODY
    REMOVED`, with the message's two stated facts both false. The mode is the only thing that says
    "this blob is a target, not source", so it is read before anything parses the blob.
    Cost: one `ls-tree` per carrier, cached in `_blob_mode` (three askers share it: measured 924
    asks before the cache, 307 calls after), which is the price of not answering the question from
    the wrong datum.
    """
    if source is None:
        return True  # unreadable at this commit: never cancel a removal
    mode = _blob_mode(root, sha, path)
    if not mode:
        return True  # could not ask: fail closed toward the removal, like the `None` case
    if mode.startswith("120"):
        # ⛔ ASK THE TARGET'S CONTENT, LIKE THE HEAD SIDE DOES (round-21 review, P1 — a regression
        # this branch introduced, measured). The blanket `not mode.startswith("100")` called every
        # symlink code WITHOUT asking what it points at, while `_head_carries_runtime_body` read
        # through the link (`Path.read_text` follows it). So an UNCHANGED `x_init.py` → a docstring
        # `impl.py`, and an UNCHANGED dangling link, graded `MODULE BODY REMOVED` — a false block on
        # a tree where nothing changed, whose message asserted two false things about the file
        # ("carries statements" / "this tree's module file carries none"). A link to real code still
        # counts (that is what the round-19 fix was for); a link to prose or to nothing holds no
        # code, and deleting nothing is not a removal.
        resolved = _blob_following_links(root, sha, path)
        if resolved is _UNREADABLE:
            return True  # cannot tell: fail CLOSED toward the removal
        return _init_carries_a_body(resolved)  # a dangling link's "" holds no code
    if mode.startswith("100"):
        return _init_carries_a_body(source)  # a regular file: ask its text, as before
    # `160000` (submodule) holds a TARGET, not source, so the body question cannot be asked of it —
    # and the name still resolving to code elsewhere in the package is exactly what must not
    # cancel a removal.
    return True


def _carrier_carries_a_body(root: Path, sha: str, path: str) -> bool:
    """Does THIS base carrier hold the module's code? — half of ONE quantifier.

    ⛔ A MODULE HAS MORE THAN ONE POSSIBLE CARRIER, and asking one of them is the defect this
    helper exists to remove (`module_name` collapses `<dotted>.py`, `<dotted>/__init__.py` and
    `<dotted>.pyi` onto the SAME symbol). A body-less `.py` beside a body-carrying
    `__init__.py` is a dead leftover, not proof that the base carried nothing: `next(...)`-ing
    the lexicographically first non-stub carrier cancelled the deletion of the body that left
    with it (measured: 23,899 lines for `tortoise/projection/__init__.py`, the one shipped module
    with two carriers), and every other arm was blind to it (round-23 review, F1 — a removal graded
    green, which is the one direction this whole function exists to prevent).

    A `.pyi` COUNTS as the module's code. That is a RECORDED rule, not a preference: a stub
    under the shipped package is importable surface (`live_return`'s DECLINED note), it is why
    `_package_modules` reads `*.py*` on purpose, and it is the module's ONLY code when nothing
    else carries it (`test_the_package_module_scan_sees_pyi_stubs`). It is exactly why the
    stub-only case must keep being graded as a removal.

    An UNREADABLE carrier counts too, deliberately: `_git_blob` returns `None` when this clone
    cannot answer (a shallow base), and "I could not read it" must never cancel a removal —
    the same fail-closed reading `_modules_with_a_body` documents.
    """
    # BY CONTENT — for `.py` AND `.pyi` alike (round-18 P2). A suffix test made a 0-byte or
    # comment-only `.pyi` count as the module's code, so deleting it was `MODULE REMOVED`, the
    # prescribed remedy froze an EMPTY block, and `file_violations` then red that block for holding
    # no code — a false block with no way out. A real stub is a `FunctionDef` and still counts.
    return _content_or_link(root, sha, path, _git_blob(root, sha, path))


def _head_carries_runtime_body(
    root: Path,
    symbol: str,
    head_paths_by_module: dict[str, str],
) -> bool:
    """Does the HEAD side still carry the module's RUNTIME body? — one answer, three call sites.

    ⛔ There is ONE predicate here on purpose: a second, body-only copy of this question ("the
    file exists and parses") answers a DIFFERENT thing, and the difference is not cosmetic. A
    body-only test counts a `.pyi` STUB as a body (it is an AST question, and a stub IS
    statements) and counts an UNREADABLE file as content (the safe reading on the BASE side,
    where a shallow clone explains the absence). Arm 1b grades BOTH of those as REMOVED on the
    HEAD side — a stub holds no runtime statements, and a file that exists but cannot be read is
    positive evidence about THIS tree. A second copy of the test is exactly how the unused-waiver
    diagnostic came to print "this tree still carries it, so nothing was removed" about the very
    removal the same run had just graded and credited (review P2 of PR #14039) — a correct
    verdict beside a false stated fact, the class this file keeps having to remove.

    ⛔ Unreadable counts as CONTENT on the BASE side and as REMOVED on the HEAD side, which is
    why the answer is only ever about THIS tree: the helper reads the working tree, never the
    base commit, so it takes no base map.

    ⛔ WHAT IT DOES *NOT* ANSWER, AND THE CALLERS THAT FORGOT IT (round-24 review, F1/F3 —
    measured). This is the RUNTIME question, so it answers `False` for a `.pyi` stub and for a
    bodyless `.py`. "Does this tree still carry the module AT ALL" is a DIFFERENT question, and
    the two unused-waiver sites (`_still_carried_why` callers) borrowed this one to mean it:
    a module whose only carrier is an unchanged stub — nothing removed, the stub IS its code
    (#12402) — therefore took their removal branch and printed "this tree does not … the
    removal is UNRECORDED" over a tree that carries the module with a `def` in it. Arm 1b is
    the caller this answer is FOR (it grades a body that left); the diagnostic must ask its own
    question, which is why that one now has its own helper and its own clause.
    """

    head_carrier = head_paths_by_module.get(symbol)
    if head_carrier is None:
        return False
    # ⛔ THE HEAD SIDE ASKS THE WHOLE PATH SET TOO (round-19 review, P1 — a false block, measured).
    # The base side was fixed to ask every carrier; this side still asked the ONE carrier
    # `head_paths_by_module` collapsed to, and `.` (46) sorts before `/` (47) — so a docstring
    # `y.py` beat a body-carrying `y/__init__.py`, and a body MOVED INTO the package (`y.py` reduced
    # to a deprecation docstring, the same statements now in `y/__init__.py`) was graded
    # `MODULE BODY REMOVED`: a legitimate refactor RED in a required job, whose printed route
    # (`archive module tortoise.y`) freezes the new file and then `surface_violations` reds it as
    # `REAPPEARED as an importable module` — no green state at all, and CI passes no waiver. The
    # identical move in the OPPOSITE direction was GREEN, which is what made the asymmetry visible.
    # `projection.py` ("DEPRECATED — This flat file is dead code") beside `projection/__init__.py`
    # is the CLOSEST shipped thing, but it is NOT this shape — round-21 review measured that the
    # shim carries a body — one `importlib.import_module` call plus eight `X = _pkg.Y` re-exports
    # (measured: 1 and 8) — so a first draft's claim that
    # this shape is "shipped in this repo" was false and is withdrawn. The shape below is
    # CONSTRUCTED in the test, which is what pins the defect.
    base = symbol.replace(".", "/")
    for candidate in (head_carrier, f"{base}.py", f"{base}/__init__.py"):
        if candidate.endswith(".pyi"):
            continue  # a stub holds no RUNTIME body, which is this function's question
        text = _file_text(root, candidate)
        if text is not None and _init_carries_a_body(text):
            return True
    return False


def _still_carried_why(prefix: str, head_carrier: str, *, base_carried_code: bool) -> str:
    """Why a waiver over a module this tree STILL CARRIES waives nothing.

    ⛔ THE QUESTION BELONGS TO THE CALLER, NOT TO `_head_carries_runtime_body` (round-24 review,
    F1 — measured). The two unused-waiver sites borrowed that helper to mean "does this tree
    still carry the module AT ALL", and the two questions differ on exactly one shape: a module
    whose only carrier is an UNCHANGED `.pyi` stub. Nothing was removed there — a stub-only
    module's only code IS the stub (#12402) — but the runtime answer is `False`, so the
    diagnostic took its removal branch and printed "this tree does not … so the removal is
    UNRECORDED" over a tree that carries the module with a `def` in it. Following that line
    means `archive module <x>`, which freezes a LIVE stub that `surface_violations` then reds as
    `REAPPEARED`: a false stated fact with no green route, the class this file keeps removing.
    Measured pre-fix on `tortoise/s.pyi` = `def api() -> int: ...`, unchanged between the base
    and the head, with `--allow-archive-shrink module:tortoise.s`: `… carries `tortoise.s`
    (module) and this tree does not — and NO `module` entry records it, so the removal is
    UNRECORDED`. (No verdict moved — both are `exit 1`.)

    ⛔ ONE CONSTRUCTION, TWO CALL SITES — the same reason `_stub_waiver_why` exists. The sites
    are the `key in base_entries` row branch and the `symbol in base_modules` package branch, and
    two earlier rounds fixed one copy while the other stood.

    ⛔ THE CALLER OWNS THE PREMISES, because only it holds the sets: it must have established
    that this tree still holds a carrier for the module (`symbol in head_modules`, which keeps a
    `.pyi` and a bodyless `.py` and so IS the path set) and that the base carried no RUNTIME body
    for it (`symbol not in base_runtime_body_modules`). Both are passed in as `head_carrier` /
    `base_carried_code` so this sentence can only make a claim the caller measured.
    """

    held = (
        "its only code was a `.pyi` stub and this tree still holds that carrier"
        if base_carried_code
        else "no file the base held for it carried a statement"
    )
    # ⛔ NOT "drop the flag" — the caller already appends that sentence to every `waives nothing`
    # row, and a second copy read as two instructions at the end of one line.
    return (
        f"{prefix} and this tree STILL CARRIES it (`{head_carrier or 'a stub carrier'}`), which "
        f"holds no runtime statements — {held}, so nothing was removed for the waiver to waive "
        "and no record is missing (#12402)"
    )


def _file_text(root: Path, path: str) -> str | None:
    """The working tree's copy of a repo-relative file, or None when it cannot be read."""

    try:
        return (root / path).read_text(encoding="utf-8")
    except OSError:
        return None


def _package_paths_at(root: Path, sha: str) -> list[str]:
    """The package's module files AS THE GIVEN COMMIT HOLDS THEM (repo-relative)."""
    code, out = _git(root, "ls-tree", "-r", "--name-only", sha, "--", "tortoise")
    if code != 0:
        raise ArchiveRefused(
            f"could not list tortoise/ at {sha[:12]} (`git ls-tree` exited {code}) — the shrink "
            "check cannot tell what the tree held before this change, and it must not answer "
            "'nothing was removed' from an unreadable base"
        )
    return [
        line for line in out.splitlines()
        if line.strip() and line.endswith((".py", ".pyi"))
    ]


def resolve_base(root: Path | None = None, ref: str | None = None) -> tuple[str, str]:
    """`(sha, label)` of the commit this tree is graded AGAINST, or a refusal.

    `ref` empty/None means "the default branch, as this clone names it". The answer is the
    MERGE BASE of HEAD and that ref (see the block comment above for why not the tip).
    """
    root = Path(root or ROOT)
    base = (ref or "").strip() or default_branch_ref(root)
    if not base:
        raise ArchiveRefused(
            "the shrink check needs a base ref and this clone names none (no "
            "refs/remotes/origin/HEAD, origin/main, main, origin/master or master). Pass "
            "`--base-ref <ref>`; a gate that cannot say what it compared cannot verify a "
            "removal, and must not report one as absent"
        )
    code, out = _git(root, "merge-base", "HEAD", base)
    if code != 0 or not out.strip():
        raise ArchiveRefused(
            f"could not resolve the merge base of HEAD and {base} (`git merge-base` exited "
            f"{code}) — a shallow or unrelated-history clone cannot answer, and answering "
            "'no removals' from a base it cannot read is the false green this arm exists to "
            "prevent. Fetch full history (`git fetch --unshallow`, or a `--depth=0` checkout) "
            "or pass `--base-ref <ref>`"
        )
    return out.strip(), f"{base} (merge base {out.strip()[:12]})"


def _entries_at(root: Path, sha: str) -> list[dict]:
    """The archive entries AS THE GIVEN COMMIT RECORDS THEM (empty when it has no archive)."""
    present, _ = _git(root, "cat-file", "-e", f"{sha}:archive/MANIFEST.json")
    if present != 0:
        # ABSENT is an honest answer here (the commit predates the archive), and it is a
        # different fact from "unreadable" — which is why the question is asked of the tree
        # rather than inferred from `_git_blob` returning None for both.
        return []
    blob = _git_blob(root, sha, "archive/MANIFEST.json")
    if blob is None:
        raise ArchiveRefused(
            f"could not read archive/MANIFEST.json at {sha[:12]} even though that commit holds "
            "it — the archive record at the base cannot be graded from here"
        )
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ArchiveRefused(
            f"archive/MANIFEST.json at {sha[:12]} is not JSON ({exc}) — a base record this gate "
            "cannot read must not read as 'no entries were dropped'"
        ) from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ArchiveRefused(
            f"archive/MANIFEST.json at {sha[:12]} carries no `entries` list — the base record "
            "is unreadable, so a drop cannot be ruled out"
        )
    return [entry for entry in entries if isinstance(entry, dict)]


def _entry_key(entry: dict) -> tuple[str, str]:
    return str(entry.get("kind")), str(entry.get("symbol"))


def _entry_claim(entry: dict) -> str:
    """A canonical digest of the fields that carry this row's CLAIM (see `CLAIM_FIELDS`)."""
    return json.dumps(
        {field: entry.get(field) for field in CLAIM_FIELDS}, sort_keys=True, default=str
    )


def parse_shrink_waiver(text: str) -> tuple[str, str]:
    """`kind:symbol` → `(kind, symbol)`, or a refusal. A waiver must name what it waives."""
    kind, sep, symbol = str(text).partition(":")
    if not sep or not symbol.strip() or kind not in KINDS:
        raise ArchiveRefused(
            f"--allow-archive-shrink {text!r} is not `<kind>:<symbol>` with a kind from "
            f"{KINDS} — e.g. `module:tortoise.legacy_thing` or `mcp-tool:tortoise_old_tool`. "
            "A waiver that does not name the record it waives cannot be audited"
        )
    return kind, symbol.strip()


def _live_body_is_the_recorded_one(root: Path, entry: dict, carrier: str) -> bool | None:
    """Does the LIVE carrier still hold the body this row recorded? None = cannot tell.

    ⛔ A live NAME is not a restore. The restore credit is granted because "the code came back
    to the product", so the question is about the CODE, not the path: a three-line stub dropped
    at the dotted path used to earn the credit, which destroyed the record of what was removed
    while nothing came back (round-7 review, measured on a 30-function module: frozen file
    deleted, row dropped, `X = 1  # nothing of the module` written at the path → every arm
    green). This asks the writer's own extractor what the symbol WOULD freeze from the live
    file and compares its sha256 to the row — a verbatim restore is green. A restore that also
    CHANGES the code is not green in either direction: the row cannot stay (direction one reds a
    live symbol) and the drop cannot be credited (the body is not the recorded one), so the route
    is to restore the block VERBATIM in the PR that lands and change the code after, when the row
    is gone from the merge base.

    `None` is returned in exactly two shapes, both of which mean "this row recorded nothing to
    compare against" (no `blocks`, or `blocks` is not a list of dicts) — that can only come from
    the BASE row, which a PR cannot author. An unreadable live file and a block the extractor
    cannot key both return `False`: the code the row recorded is not in the tree, which is the
    question this function exists to answer.
    """
    rows = entry.get("blocks")
    if not isinstance(rows, list) or not rows:
        return None  # nothing recorded to compare: absence is not evidence of a loss
    kind = str(entry.get("kind") or "")
    symbol = str(entry.get("symbol") or "")
    targets: list[tuple[dict, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        path = str(row.get("path") or "")
        if kind == MODULE and len(rows) == 1 and carrier and _file_text(root, carrier) is not None:
            path = carrier  # a module may live at a different path after a split
        targets.append((row, path))
    for row, path in targets:
        text = _file_text(root, path)
        if text is None:
            # ⛔ The row's OWN source file is not in the tree, so the code cannot be back. This
            # is `False`, not `None`: for `mcp-tool` / `sdk-method` the carrier `live_return`
            # returns is a SYMBOL, not a path, so the old code tried to read a file named
            # `get_entity` and every one of those 110 rows fell into "cannot tell" and kept the
            # credit — the body check was unreachable for all but the single `module` row
            # (round-8 review).
            return False
        expected = expected_block(kind, symbol, str(row.get("path") or ""), text, row.get("node"))
        if expected is None:
            # ⛔ NOT a "cannot tell". The live file does not yield this block (the node key is not
            # in it, or the kind branch finds no such symbol), so the code the row recorded is
            # not in the tree — which is the whole question. The earlier `None` here read as
            # "cannot tell" and the credit branch treats anything but False as credit, so a
            # multi-block `mcp-tool` row whose handler was nowhere in the tree still earned the
            # restore (round-8 review, measured).
            return False
        if _sha256(expected.strip("\n")) != row.get("sha256"):
            return False
    return True


def _reanchor_is_verified(
    root: Path, symbol: str, rel: str, entry: dict, base_entry: dict
) -> bool:
    """Is this REWRITE an EVIDENCED re-anchor, rather than a faithless edit? (#12291)

    A re-anchor happens when the frozen body is refreshed from the commit that LAST held the
    code (`main` edited the file while a batch was in flight — `archive/mcp-tools/b1-removed-no-
    destination/tortoise_analyze.py` records one), and the anchor AND often the blocks must then
    move together. The printed remedy for a rewrite is `--allow-archive-shrink`, a LOCAL-SHELL
    waiver that the workflow deliberately never passes, so before this arm a legitimate
    correction had no route a PR could take (review P2 of PR #14039) — and `verify-anchors`, run
    immediately before this step in the same job, is what REQUIRES the correction. That is a
    gate-against-gate deadlock, not a protection.

    So the question asked here is the one the anchor arm already knows how to answer: does the
    frozen file still match the recorded blocks, and does the NEW anchor hold each recorded body
    BYTE-FOR-BYTE? When both hold, the record still proves exactly what it claims — the removed
    code is what it says it is — which is the property #12291 exists to protect. When either
    fails, it is the faithless edit and stays a violation.

    ⛔ The anchor must be READABLE in this clone. `_anchor_violations` is deliberately
    three-valued and returns [] when the commit object is absent (a shallow clone cannot tell a
    forged anchor from an un-fetched one), so accepting on an unreadable anchor would turn this
    arm into a rubber stamp. A clone that cannot read the anchor keeps the violation — fail
    closed, and the archive's own step (`fetch-depth: 0`) is what makes it answerable in CI.

    ⛔ AND IT IS NOT A LICENCE TO SHRINK THE EVIDENCE. The first version of this check was
    handed only the NEW entry, so "the new record is internally consistent with the new file"
    was the whole question — and a rewrite that DELETED a frozen block while deleting its row
    from the manifest passed every arm (measured in round-6 review: a 2-block `mcp-tool` entry
    reduced to 1 block, `_reanchor_is_verified: True`, `shrink_violations: []`,
    `file_violations: []`, and `verify-anchors` iterates only the blocks that remain). That is
    the "code that exists nowhere" loss #12291 was filed for, reached one level down: keep the
    row, shrink its evidence. A re-anchor re-HASHES bodies; it does not delete them. So the
    BASE entry is required too: the same number of blocks, the same set of block KEYs, and
    `sources` still the writer's own derivation (`[path for path, … in blocks]`).
    """
    if not rel:
        return False
    recorded = entry.get("blocks")
    base_recorded = base_entry.get("blocks")
    if not isinstance(recorded, list) or not isinstance(base_recorded, list):
        return False
    if len(recorded) != len(base_recorded):
        return False  # a refresh re-hashes bodies; dropping one is the loss this arm exists for
    if sorted(str(row.get("node")) for row in base_recorded) != sorted(
        str(row.get("node")) for row in recorded
    ):
        return False  # same blocks, same keys — a re-anchor does not swap the evidence
    # ⛔ AS A MULTISET. The writer derives `sources` in block order, but the committed manifest
    # already carries one row whose order differs from its blocks
    # (`tortoise_analyze`: sources `[mcp_server.py, tool_registry.py]` over blocks in the
    # opposite order), so an ORDER-SENSITIVE comparison refused the one legitimate re-anchor for
    # that row with a remedy ("restore the recorded field(s)") that `verify-anchors` reds — a
    # false block with no route, for 1 of 111 rows (round-9 review). Order carries no claim
    # here: the block tuples are bound to their paths by the per-row comparison below.
    if sorted(entry.get("sources") or []) != sorted(str(row.get("path")) for row in recorded):
        return False  # a `sources` edit cannot ride along on a re-anchor
    # ⛔ EVERY OTHER CLAIM FIELD IS FIXED. A re-anchor moves the anchor and re-hashes bodies;
    # `archived_from` is a CLAIM_FIELD bound to nothing else on this path (`_anchor_violations`
    # reads the block rows' `path`, and `file_violations` only makes the FILE's metadata agree
    # with the manifest), so a consistent lie passed both (round-7 review: `archived_from`
    # rewritten to a file that never held it, every block byte-identical → `file_violations: []`,
    # `shrink_violations: []`).
    for field in CLAIM_FIELDS:
        if field in ("archived_at", "blocks", "sources"):
            continue
        if json.dumps(entry.get(field), sort_keys=True, default=str) != json.dumps(
            base_entry.get(field), sort_keys=True, default=str
        ):
            return False
    text = _file_text(root, rel)
    if text is None:
        return False
    blocks = frozen_blocks(text)
    if not blocks or len(recorded) != len(blocks):
        return False
    sha = str(entry.get("archived_at") or "")
    if not _SHA_RE.match(sha) or not _git_has_commit(root, sha):
        return False
    # ⛔ THE ANCHOR MUST MOVE FORWARD, ON THE DEFAULT BRANCH. A readable anchor is not enough:
    # re-rendering the frozen body from an OLDER revision of the same symbol and re-pointing
    # `archived_at` at it satisfies every byte check above and silently ROLLS THE EVIDENCE BACK
    # (round-7 review — the TUF rollback shape this module cites at the top of this file: "a
    # client that accepts a lower version than the one it already trusts has been rolled
    # back"). A side-branch anchor is refused for the same reason `anchor_sha` refuses to WRITE
    # one: a squash merge orphans it.
    previous = str(base_entry.get("archived_at") or "")
    if _SHA_RE.match(previous) and _git_has_commit(root, previous):
        code, _out = _git(root, "merge-base", "--is-ancestor", previous, sha)
        if code != 0:
            return False  # the anchor went BACKWARDS: a rollback, not a refresh
    ref = default_branch_ref(root)
    if ref is None:
        return False  # durability unprovable here: keep the violation (fail closed)
    code, _out = _git(root, "merge-base", "--is-ancestor", sha, ref)
    if code != 0:
        return False  # not on the default branch — this anchor is not a citation
    for (path, body), row in zip(blocks, recorded, strict=True):
        if not isinstance(row, dict):
            return False
        if row.get("path") != path or row.get("sha256") != _sha256(body):
            return False
        if _anchor_violations(root, symbol, rel, entry, path, body, row.get("node")):
            return False
    return True


def _row_froze_a_stub(entry: dict) -> bool:
    """Did this `module` row freeze FROM a `.pyi` stub? (fresh-context review, P0)

    `_module_source`'s `.pyi` fallback resolves the stub when the only file left is `x.pyi`, so
    the writer can freeze `def f1() -> int: ...` as the SOURCE OF RECORD for a module whose
    statements this same run reports as removed. Arm 1b's own rule is that a `.pyi` is NEVER
    runtime code, so such a row holds none of the module's body — and crediting it on the row's
    EXISTENCE reads "the removal has its record" over a record holding no code.

    ⛔ ASK `blocks[].path` — THE FIELD BOUND TO THE BYTES (round-3 correction of this helper).
    Three successive cuts of this guard were each wrong in a different way, and only mutation
    separated them:

      * the archived FILE's suffix — DEAD CODE. `archive_rel` always emits
        `archive/modules/<batch>/<mirror>.py` ("one symbol, one path, always"),
        so no row the writer produces could satisfy it, and the fixtures that "covered" it
        hand-wrote a `.pyi` path `archive_rel` cannot produce.
      * `archived_from` — SPOOFABLE. It is bound only to the frozen file's own `ARCHIVED_FROM`
        header, i.e. to a string the same hand-splicing author writes; this file already records
        that limit in `_reanchor_is_verified`'s "EVERY OTHER CLAIM FIELD IS FIXED" note ("bound
        to nothing else on this path … so a consistent lie passed both"), and hand-splicing is
        what `MEMORY.md` recommends. Measured: a spliced row saying `tortoise/x.py` over frozen
        STUB bytes was credited, with `shrink_violations` AND `file_violations` both empty.
      * `blocks[0].path` — the field `file_violations` ties to the path baked into the frozen
        file's verbatim banner AND to that body's sha256, so it cannot be re-pointed without the
        bytes following. This is the one asked below.

    ⛔ THESE CITATIONS NAME SYMBOLS, NOT LINE NUMBERS, ON PURPOSE. Each of the three cuts above
    was once described by a line-number anchor and every one of them rotted: the `archive_rel`
    cite said line 2177, then 2224-2235 at a head where the definition sat at 2314, and the
    re-anchor note's 1357-1361 was correct when written and was 15 lines off after a merge that
    INSERTED above it. `MEMORY.md` `[pin-defensive-claim]` states the rule ("re-derive from the
    SYMBOL … never key a needle to a loop variable"); comments are held to it here too.

    ⛔ AND FAIL CLOSED ON AN ABSENT SOURCE. An entry recording no block path proves nothing
    about what it holds, so it is refused WITH the stub, never credited: hence
    `not src.endswith(".py")`, not `src.endswith(".pyi")` — the latter answers False for an
    absent field and silently credits it.

    ⛔ `_init_carries_a_body` is deliberately NOT the test: it is an AST question and a stub IS
    statements (`def f1() -> int: ...`).
    """
    rows = entry.get("blocks")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        path = str(rows[0].get("path") or "")
        if path:
            # Authoritative: bound to the frozen banner AND the body's sha256, so it answers the
            # question in BOTH directions (a real `.py` credits, anything else refuses).
            return not path.endswith(".py")
    src = str(entry.get("archived_from") or "")
    if not src:
        # Records NOTHING about what it froze. Unprovable, so it is refused rather than credited
        # — but only in this shape: a row that does name a source falls through to the claim-field
        # test, because `SOURCE_OF` is not a path and refusing it would red the README's own
        # restore route (measured: two legitimate rows redded when absence alone refused).
        return True
    return src.endswith(".pyi")


def _row_frozen_source(entry: dict) -> str:
    """What the row says it froze FROM, preferring the field bound to the bytes."""

    rows = entry.get("blocks")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        path = str(rows[0].get("path") or "")
        if path:
            return path
    return str(entry.get("archived_from") or "")


def _stub_waiver_why(
    prefix: str, base_carrier: str, head_carrier: str, *, base_has_runtime_body: bool
) -> str:
    """Why an unconsumed waiver over a stub-frozen `module` row waives nothing.

    ⛔ ONE CONSTRUCTION, TWO CALL SITES (review F1 of round 21). This sentence existed in two
    copies — the `key in base_entries` branch and the `elif symbol in base_modules` branch — and
    two consecutive rounds fixed ONE copy, leaving the same falsity standing one branch below,
    contradicting its sibling for the same state depending only on whether the row was committed
    at the base. Reusing one helper is what makes a third copy impossible.

    ⛔ AND IT NAMES WHAT IS TRUE OF THIS TREE (review F2 of round 21). "No `.py` on either side"
    was not provable from `base_paths_by_module` alone — that map is built from the BASE paths
    only, and the head can hold a `.py` (an emptied one, or a docstring `__init__.py`) which
    `module_name` collapses onto the same symbol. So the head carrier is named explicitly, and
    the claim is only ever about what the two maps can prove.

    `base_paths_by_module` PREFERS the runtime carrier (the sorted builder skips a `.pyi` once a
    non-`.pyi` is stored), so a `.pyi` there is positive proof the base carried nothing else.

    ⛔ SPLIT ON THE ASSERTION THE TEXT MAKES (review F1 of round 22). The polarity is "does the
    base carry a RUNTIME source" — NOT "is the carrier a stub". An EMPTY
    carrier (the base holds no file for this module at all, the strongest proof there was nothing
    to remove) does not end with `.pyi`, so keying on `.pyi` sent it to the had-runtime arm and
    re-emitted both removed falsities: "it is not a record", and "restore the source from a commit
    that still holds it" over a repo where `git log --all -- <path>.py` is empty. Fail toward the
    no-runtime text, and describe an absent carrier as such rather than printing `() `.

    ⛔ AND ASK IT ABOUT THE CARRIER'S CONTENT, NOT ITS SUFFIX (round-23 review, F1).
    `endswith(".py")` is a statement about a FILENAME, and a `.py` whose body is gone is not a
    runtime source — the same repo establishes that one function up. With the suffix test, a base
    whose only `.py` was a leftover scaffold took the had-runtime arm and printed "restore the
    source from a commit that still holds it" over a module with no body-carrying `.py` in ANY
    commit: a route that cannot be followed, which is the class rounds 19-22 removed here. The
    caller now answers the question from the base's whole carrier set
    (`base_runtime_body_modules`), and the wording follows the carrier it actually found.
    """

    if base_has_runtime_body:
        return (
            f"{prefix} — but the `module` entry you also passed FROZE ONLY A STUB, so it is not "
            "a record: restore the source from a commit that still holds it, archive it, and "
            "delete it again"
        )
    if base_carrier.endswith(".pyi"):
        where = f"only as a stub (`{base_carrier}`)"
        held = "the base held no `.py` for this module"
    elif base_carrier:
        where = f"only as scaffolding (`{base_carrier}`, no statements of its own)"
        held = "the base's `.py` for this module carried nothing"
    else:
        where = "on neither side"
        held = "the base held no `.py` for this module"
    return (
        f"{prefix} — {where}: {held} "
        f"and this tree carries {head_carrier or 'no module file'}, so there was no runtime "
        "source to record. The `module` entry you also passed is the writer's own output for a "
        "stub-only module (a stub-only module's only code IS the stub), and nothing was removed "
        "for the waiver to waive: drop the flag"
    )


def _record_froze_a_stub(
    module: str, base_label: str, base_carrier: str, head_desc: str, entry: dict
) -> str:
    """The violation for a `module` row that credits a removal while holding only the stub."""
    return (
        f"{module}: THE RECORD FROZE A STUB — {base_label} carried the module's statements in "
        f"`{base_carrier}`, this tree carries {head_desc}, and the `module` row present froze "
        f"only `{_row_frozen_source(entry) or 'NO recorded source'}`, which is not the removed "
        "runtime source. This run reports the module "
        "body removed and then credits a record that holds none of it, which is the false PASS "
        "`archive/` exists to prevent (#12402). "
        f"{MODULE_ROUTE}"
    )


def shrink_violations(
    root: Path | None = None,
    *,
    base_ref: str | None = None,
    allowed: tuple[str, ...] | list[str] = (),
    surface: dict | None = None,
) -> list[str]:
    """Every removal this tree made that left NO trace, graded against the base ref.

    TWO arms, because the two artifacts fail differently (#12402, #12291):
      · a MODULE that left `tortoise/` must carry a `module` archive entry — nothing else in
        the repo can see a whole-module deletion;
      · an archive entry that left (or was rewritten in) `archive/MANIFEST.json` must be
        restored, or waived with a stated reason, because that manifest is the only index of
        what was removed.

    A DROPPED row is not always a loss. `archive/README.md`'s restore procedure ends by
    deleting the row AND its frozen file, because the code came back to the product — and a
    restore CANNOT keep the row, since direction one reds an archived symbol that is live
    again. So a drop whose symbol `live_return` finds live AND whose live body is the one the
    row recorded (`_live_body_is_the_recorded_one`; `False` on an unreadable or unkeyable file)
    is a RESTORE: green, no waiver,
    nothing lost (the code is in the product; the frozen bytes stay readable in git history at
    the deletion commit). `surface` is what answers that question, and the same answer
    `surface_violations` reds from its own side. `surface=None` means "liveness UNKNOWN" and
    gives no restore credit — fail-closed toward the violation, so a caller that has not read
    the surface can never turn a loss into a green.

    `allowed` is the deliberate-departure escape hatch, applied to a NAMED record only — there
    is no blanket flag, for the same reason `cut --allow-removal` has none: a blanket flag is
    how a gate becomes decoration. A waiver that matches no violation is itself reported, so a
    stale waiver cannot sit in a PR body looking like a record of something.
    """
    root = Path(root or ROOT)
    sha, base_label = resolve_base(root, base_ref)
    entries = load_manifest(root / "archive" / "MANIFEST.json")["entries"]
    working = {_entry_key(entry): entry for entry in entries}
    base_entries = {_entry_key(entry): entry for entry in _entries_at(root, sha)}
    waivers: dict[tuple[str, str], str] = {parse_shrink_waiver(t): t for t in allowed}
    used: set[tuple[str, str]] = set()
    out: list[str] = []

    # ── arm 1: a MODULE that disappeared from the package tree (#12402) ──────────
    head_paths = _package_modules(root)
    base_paths = _package_paths_at(root, sha)
    # CONTENT decides, not the path set: an `__init__.py` with a body at the base is a module
    # that left even though its package still resolves through a surviving sibling (review F2 —
    # `tortoise/__init__.py` is 406 lines of the public API, and the ancestor expansion that
    # used to cancel it graded that deletion green).
    # ⛔ AND THE SET THE ARMS ITERATE USES THE SAME READER (round-20 P1, measured): a symlinked
    # carrier must be graded by its TARGET's text, or the module never enters `base_modules`.
    base_modules = _modules_with_a_body(
        base_paths, lambda path: _blob_following_links(root, sha, path)
    )
    # ⛔ ONE CARRIER SET PER MODULE, asked ONCE and about the WHOLE SET (round-23 review, F1).
    # "Did the base carry this module's code?" is a question about the module's carriers, and a
    # module can have three of them; answering it from one path cancelled a real removal. The two
    # sets below are that question asked twice — "any carrier holds code" (the scaffolding skip)
    # and "any carrier is a `.py` that holds code" (the stub-waiver diagnosis, which must not
    # print a restore route for a runtime source that never existed).
    base_carriers: dict[str, list[str]] = {}
    for path in sorted(base_paths):
        base_carriers.setdefault(module_name(path), []).append(path)
    base_body_modules = {
        module
        for module, paths in base_carriers.items()
        if any(_carrier_carries_a_body(root, sha, path) for path in paths)
    }
    base_runtime_body_modules = {
        module
        for module, paths in base_carriers.items()
        if any(
            path.endswith(".py") and _carrier_carries_a_body(root, sha, path) for path in paths
        )
    }
    head_modules = _modules_with_a_body(head_paths, lambda path: _file_text(root, path))
    gone_modules = base_modules - head_modules
    for module in sorted(gone_modules):
        key = (MODULE, module)
        # `candidates` is the BASE's carrier set for this module, so it answers both "did the base
        # hold RUNTIME code (not a `.pyi` stub only)?" and the `where` in the violation below.
        candidates = base_carriers.get(module, [])
        # ⛔ THE CARRIER WHOSE CODE WAS LOST, asked by CONTENT — not the first non-`.pyi` path
        # (round-23 residual, measured). Both readers below make a claim about THIS file: the
        # `_record_froze_a_stub` verdict that the row credited the wrong source, and the
        # `where` that tells the operator which file the removed statements were in. The suffix
        # test answered with a leftover docstring `tortoise/x.py`, so a base of `x.py` (scaffold)
        # + `x.pyi` (the module's only code) with a row that correctly froze the `.pyi` was
        # graded RED ("THE RECORD FROZE A STUB … carried the module's statements in
        # `tortoise/x.py`"): a false violation AND a false stated fact about the same file, in
        # the arm whose job is to catch a row that holds nothing — which is why the answer must
        # be the carrier that actually holds code, and `None` when no non-stub one does (the row
        # that froze the stub IS the record there).
        runtime_carrier = next(
            (
                path
                for path in candidates
                if not path.endswith(".pyi") and _carrier_carries_a_body(root, sha, path)
            ),
            None,
        )
        # …and the file named in a `MODULE REMOVED` is the carrier the code was IN: with a
        # scaffolding `.py` beside a body-carrying `__init__.py`, `candidates[0]` pointed the
        # reader at the file that held nothing.
        lost_carrier = runtime_carrier or next(
            (path for path in candidates if _carrier_carries_a_body(root, sha, path)),
            candidates[0] if candidates else f"{module.replace('.', '/')}.py",
        )
        # ⛔ SCAFFOLDING AT THE BASE IS NOT A REMOVAL (round-17 P2, measured). Arm 1b already
        # grades the IN-PLACE shape by content, but this arm graded the DELETION shape by PATH: a
        # base `tortoise/doc.py` that was a docstring alone (or 0 bytes) landed in `base_modules`,
        # so deleting it was `MODULE REMOVED` — and the remedy the violation printed
        # (`archive module tortoise.doc`) is one this tool REFUSES for a body-less source, so the
        # red had no route at all: not the waiver (CI passes no `--allow-archive-shrink`), not the
        # writer. Nothing left the product, so the arm must cancel the way an emptied package does.
        #
        # ⛔ AND THE CANCELLATION IS ABOUT THE MODULE, NOT ONE CARRIER (round-23 review, F1). The
        # first cut asked `next(...)`-picked carrier — the lexicographically first non-`.pyi` path
        # — so a bodyless leftover `<dotted>.py` cancelled the deletion of a code-carrying
        # `<dotted>/__init__.py`, and a docstring `.py` cancelled a deleted `.pyi` beside it:
        # (round-21 review: the figures that stood here — "0-byte `tortoise/projection.py`" beside
        # a "406-line `projection/__init__.py`" — were BOTH false; measured, `projection.py` is
        # 1360 bytes carrying a body and its `__init__.py` is 23,899 lines, while 406 is
        # `tortoise/__init__.py`. Named shapes, no numbers, because a fabricated count is exactly
        # the class this file keeps removing.)
        # both were `MODULE REMOVED` before this block existed, and both went green with it, with
        # no record, no waiver and no diagnostic. Nothing left the product only when NO base
        # carrier carries code, which is what `base_body_modules` answers. The `.pyi`-only case
        # is untouched: a stub IS a module's code when nothing else carries it.
        if module not in base_body_modules:
            continue
        if key in working:
            # ⛔ A row that froze the stub is not a record — and it is credited HERE, not only in
            # arm 1b: when the stub is deleted as the writer's own output asks, the module has NO
            # head carrier at all, so it is `base - head` that lands in this arm while the row's
            # existence silences it. `_row_froze_a_stub` asks `blocks[].path` — the field
            # `file_violations` binds to the frozen banner AND the body's sha256 — and falls back
            # to `archived_from` only for a row carrying no block list. Never the archived file's
            # suffix (the archived file is always `.py`; see its docstring for the measured false
            # PASS that made the suffix-keyed first cut dead code).
            if runtime_carrier is not None and _row_froze_a_stub(working[key]):
                out.append(
                    _record_froze_a_stub(
                        module,
                        base_label,
                        runtime_carrier,
                        "no module file at all",
                        working[key],
                    )
                )
            continue  # an entry of the right kind names it: the removal has its record
        if key in waivers:
            used.add(key)
            continue
        where = lost_carrier
        out.append(
            f"{module}: MODULE REMOVED — {base_label} carries it and this tree does not, and "
            "`archive/MANIFEST.json` has no `module` entry for it. A module is the largest "
            "removable unit and the surface baseline carries no `module` rows, so neither "
            "`surface_manifest.cut` nor the rest of this check can see the deletion (#12402). "
            f"{MODULE_ROUTE} (the file was {where})"
        )

    # ── arm 1b: a module EMPTIED IN PLACE — the file is still there, its body is not (#12402) ──
    # `_modules_with_a_body` grades an `__init__.py` by CONTENT because the PATH set cannot tell
    # scaffolding from an API (review F2). The SAME reasoning holds for every module, and the
    # version that applied it to `__init__.py` alone was a live fail-open (review P2): `>
    # tortoise/x.py` — or `git rm` of the body while the file stays — left the name in BOTH sets,
    # so the arm reported green while the module's code was gone, and no sibling arm sees it
    # (`file_violations` walks manifest rows, `package_violations` only checks the archive
    # locations). Reading is narrowed to the names present on BOTH sides and to the head file
    # first, so the arm costs one working-tree read per shared module and no git call unless the
    # head file is body-less.
    base_paths_by_module: dict[str, str] = {}
    head_paths_by_module: dict[str, str] = {}
    for targets, paths in ((base_paths_by_module, base_paths), (head_paths_by_module, head_paths)):
        # SORTED so the carrier choice is a property of the paths, not of a set's hash order:
        # the preference below is what makes "add a stub beside the module" deterministic, and a
        # hash-ordered map made that guarantee untestable (measured in round-7 review).
        for path in sorted(paths):
            name = module_name(path)
            # ⛔ PREFER the RUNTIME carrier: a `.pyi` stub has no runtime statements, so it must
            # never stand in for a module's code on the SHRINK side. Replacing `x.py` with
            # `x.pyi` deletes the module from the product, and the stub used to satisfy both the
            # name set AND (being non-empty) the content rule — graded green (round-5 review).
            if name not in targets or targets[name].endswith(".pyi"):
                targets[name] = path
    for module in sorted(base_modules & head_modules):
        # ⛔ NAME THE CARRIER THAT HELD THE BODY, NOT THE FIRST PATH (round-21 review, P2 —
        # measured, PRE-EXISTING, fixed here only because the message is this arm's).
        # `base_paths_by_module[module]` is the lexicographically first non-`.pyi` path and `.`
        # (46) sorts before `/` (47), so a docstring `p.py` shim beside a code-carrying
        # `p/__init__.py` (the shape this repo's `tortoise/__init__.py` fallback was built for,
        # CONSTRUCTED in the test rather than copied from a shipped module — measured) made the
        # violation read "carries statements in `tortoise/p.py`" — false — and the restore route
        # it printed brought back the docstring and re-refused identically: a
        # permanent red whose stated fact and remedy both pointed at the wrong file. Arm 1 already
        # answers this by CONTENT, so the same preference order is reused here and the named file
        # is one that carries code.
        base_carrier = next(
            (
                path
                for path in base_carriers.get(module, [])
                if _carrier_carries_a_body(root, sha, path)
            ),
            base_paths_by_module[module],
        )
        head_carrier = head_paths_by_module[module]
        stubbed = head_carrier.endswith(".pyi") and not base_carrier.endswith(".pyi")
        # ⛔ A head carrier that EXISTS but cannot be read is the removed body, not a reason
        # to stay silent: a dangling symlink, or a DIRECTORY named `x.py`, passes `exists()`
        # while holding no code (round-7 review). This is the OPPOSITE polarity from
        # `_modules_with_a_body`, and deliberately so: there a `None` answer means the BASE
        # side could not read the commit (a shallow clone), where "counts as content" is the
        # only safe reading — here the file is on disk in the working tree, so an unreadable
        # one is evidence about THIS tree.
        #
        # ⛔ THE DECISION IS `_head_carries_runtime_body`, not a second copy of this test: arm 1b
        # grades a RUNTIME body that left, which is what this arm is about. (The unused-waiver
        # diagnostic below needed a DIFFERENT question — "does this tree carry the module at all"
        # — and borrowing this answer made it claim a removal over an unchanged stub; round-24
        # review F1 fixed that at ITS call site, in `_still_carried_why`, rather than by relaxing
        # this helper.)
        if _head_carries_runtime_body(root, module, head_paths_by_module):
            continue
        # ⛔ THE BASE SIDE ASKS THE WHOLE CARRIER SET, LIKE ARM 1 (round-18 review, P0 — a removal
        # graded GREEN). This gate read ONE carrier — `base_paths_by_module[module]`, the
        # lexicographically first non-`.pyi` path — and `.` (46) sorts before `/` (47), so a
        # body-less leftover `tortoise/x.py` beat the real `tortoise/x/__init__.py`: arm 1b called
        # the BASE "scaffolding" and `continue`d, so emptying the `__init__.py` IN PLACE was GREEN
        # in every arm with the body in no file under `archive/` (measured, three shapes; the
        # pairing is CONSTRUCTED in the test — round-21 review measured that no module in this repo
        # ships a BODYLESS shim beside another carrier, `tortoise/projection.py` being a real
        # 37-line re-export shim that carries a body).
        # Arm 1 was fixed for exactly this in round 23 (`base_body_modules`); the fix went to the
        # sibling gate and not to the shared question, so the arm one gate over kept the defect.
        # ⛔ ASK THE SAME KIND OF BODY ON BOTH SIDES (round-20 P1 — a regression this branch
        # introduced, fixed properly here). The first cut gated on `base_body_modules` while the
        # head test skips `.pyi` by design, so an UNCHANGED stub-only module graded `MODULE BODY
        # REMOVED` (a false block, whose route then reds as `REAPPEARED`); gating on
        # `base_runtime_body_modules` then silenced the MIXED shape (a bodyless `w.py` shim keeping
        # the name alive while the module's only code — a `w/__init__.pyi` — is deleted), because a
        # stub-only base never supplies a runtime body. So: whatever KIND carried it at the base is
        # the kind the head must still carry (the stub half and the runtime half are each measured
        # by a row; the "runtime appeared" case is answered once, above, not twice).
        #
        # ⚠️ MEASURED RESIDUAL OF THIS ARM (round-21 review, not closed): the head test asks "does
        # ANY surviving carrier hold a runtime body", so a RE-EXPORT SHIM that survives credits the
        # module no matter how much of the real body left. Emptying the 23,899-line
        # `tortoise/projection/__init__.py` to a docstring while the 37-line `tortoise/projection.py`
        # shim (one `importlib.import_module` call, eight `X = _pkg.Y` re-exports) stays put grades
        # GREEN. It is the same
        # family as the declined `X = 1` case: this arm asks per-symbol EXISTENCE across a path set,
        # not a proportion, so a shim that still resolves the same names is a legitimate answer and
        # a decoy is indistinguishable from it without symbol-level comparison. Closing it needs an
        # arm of its own (compare the base's and head's top-level name sets per carrier), not
        # another body spelling. Filed as #14298.
        if module not in base_body_modules:
            continue  # nothing left the product
        if module not in base_runtime_body_modules:
            base = module.replace(".", "/")
            still_stubbed = any(
                (text := _file_text(root, candidate)) is not None
                and _init_carries_a_body(text)
                for candidate in (f"{base}.pyi", f"{base}/__init__.pyi")
            )
            if still_stubbed:
                continue  # a stub-only module whose stub is still there: nothing left
            # No "a runtime body appeared at the head" test here: if one had, the call at the top
            # of this arm would already have continued (`_head_carries_runtime_body` is the same
            # question with the same arguments), so a branch for it can never be reached — measured
            # by the round-20 VGATE, which removed it and stayed green.
            key = (MODULE, module)
            if key in working or key in waivers:
                if key in waivers:
                    used.add(key)
                continue
            out.append(
                f"{module}: MODULE REMOVED — {base_label} carries it as a `.pyi` STUB and this "
                "tree does not, and `archive/MANIFEST.json` has no `module` entry for it. A stub "
                "under the shipped package is importable surface and is the module's only code "
                f"when nothing else carries it (#12402). {MODULE_ROUTE} (the file was "
                f"{base}.pyi or {base}/__init__.pyi)"
            )
            continue
        key = (MODULE, module)
        # HOW this tree's module file stopped carrying code: the stub spelling, or the body-less
        # `.py` this branch was written for. Computed BEFORE the `key in working` branch so the
        # row-credits-a-stub message names the same fact the violation below names — the two
        # used to disagree about the same file when only the latter was asked.
        if stubbed:
            how = f"replaced by a `.pyi` STUB (`{head_carrier}`), which holds no runtime statements"
        else:
            head_body_text = _file_text(root, head_carrier) or ""
            try:
                ast.parse(head_body_text)
            except SyntaxError:
                how = (
                    f"`{head_carrier}` no longer parses — text that is not Python holds no "
                    "statements a caller can reach"
                )
            else:
                how = (
                    f"`{head_carrier}` carries none (empty, whitespace, comments or a bare "
                    "docstring)"
                )
        if key in working:
            # ⛔ A ROW THAT FROZE THE STUB IS NOT A RECORD (fresh-context review of PR #14039,
            # P0). `_row_froze_a_stub` asks `blocks[].path` (falling back to `archived_from` for a
            # row with no block list) — see its docstring for why the archived file's suffix is a
            # dead question.
            #
            # ⛔ AND ASK THE BASE, NOT THE HEAD'S CARRIER SUFFIX (round-23 review, residual). The
            # conjunct used to be `stubbed`, i.e. "the head carrier is a `.pyi` and the base's was
            # not" — a statement about the HEAD. Its blind spot is the shape the writer resolves
            # to the stub (round-23 review, F1): base `x.py` carries the body, head `x.py` is a
            # docstring, head `x.pyi` is a stub, and a `module` row froze the `.pyi`. There the
            # head carrier is the `.py`, so `stubbed` is False, the guard never fires, and the
            # row's EXISTENCE credits the removal: measured green, with the base's `real()` in no
            # file under `archive/` — the exact false PASS this branch exists to refuse.
            # `base_runtime_body_modules` is the true premise (a runtime body left), so ask that.
            if module in base_runtime_body_modules and _row_froze_a_stub(working[key]):
                out.append(
                    _record_froze_a_stub(
                        module,
                        base_label,
                        base_carrier,
                        f"only the `.pyi` stub `{head_carrier}`" if stubbed else how,
                        working[key],
                    )
                )
            continue  # an entry of the right kind names it: the removal has its record
        if key in waivers:
            used.add(key)
            continue
        out.append(
            f"{module}: MODULE BODY REMOVED — {base_label} carries statements in "
            f"`{base_carrier}` and this tree's module file {how}. The NAME survived, "
            "so a set comparison over module names cannot see this, and the surface baseline "
            "carries no `module` rows: the code left the product and nothing records it "
            "(#12402). "
            f"{MODULE_ROUTE}"
        )

    # ── arm 2: an entry that left, or was rewritten in, MANIFEST.json (#12291) ───
    for key, base_entry in sorted(base_entries.items()):
        kind, symbol = key
        if key not in working:
            if key in waivers:
                used.add(key)
                continue
            # A RESTORE, not a loss: the row left because the code came BACK (direction
            # one reds the row if it stays — archived ⇔ absent), so there is nothing the
            # archive promised that the product no longer carries. Recognising it here is
            # what keeps `archive/README.md`'s own restore procedure green in CI, which
            # runs this arm with `--base-ref` on every PR.
            #
            # ⛔ The BODY decides, not the name: a stub dropped at the dotted path is not
            # the code returning (see `_live_body_is_the_recorded_one`).
            #
            # ONE `and` chain, not two nested `if`s: the inner test only ever means
            # "skip the report", so nesting it bought nothing and cost a ruff SIM102 on
            # the required `surface-guard`/lint path.
            if (
                surface is not None
                and (carrier := live_return(base_entry, surface, root)) is not None
                and _live_body_is_the_recorded_one(root, base_entry, carrier) is not False
            ):
                continue
            out.append(
                f"{symbol} ({kind}): REMOVED from archive/MANIFEST.json — {base_label} records "
                "it and this tree does not, so the only index of what was removed from the "
                "product no longer names it. The frozen file under archive/ may still be on "
                "disk — that sub-case is the one the file arm DOES see (`archived code that no "
                "manifest row indexes — orphan`); what no other arm sees is dropping the row AND "
                "the file together, because every other arm here iterates the rows it FINDS "
                "(#12291). "
                "REMEDY: restore the entry — or, if the code came back to the PRODUCT "
                "BYTE-FOR-BYTE, delete its frozen file in the same commit (that is the restore "
                "procedure in archive/README.md, and this arm credits a live symbol whose BODY "
                "matches the record, so no waiver is needed). A restore that also CHANGES the "
                "code has no green state here: putting the block back VERBATIM in this PR and "
                "changing the code afterwards is the route. If the drop is neither, state it: "
                f"`--allow-archive-shrink {kind}:{symbol} --allow-archive-shrink-reason <why>`. "
                "A waiver is a local-shell record: CI passes none, deliberately."
            )
        elif _entry_claim(working[key]) != _entry_claim(base_entry):
            if key in waivers:
                used.add(key)
                continue
            if _reanchor_is_verified(
                root, symbol, str(working[key].get("file") or ""), working[key], base_entry
            ):
                # Not a faithless edit: the frozen file still matches the record and the new
                # anchor holds the recorded body byte-for-byte (see `_reanchor_is_verified`).
                continue
            changed = [
                field for field in CLAIM_FIELDS
                if json.dumps(base_entry.get(field), sort_keys=True, default=str)
                != json.dumps(working[key].get(field), sort_keys=True, default=str)
            ]
            out.append(
                f"{symbol} ({kind}): REWRITTEN in archive/MANIFEST.json — {base_label} records "
                f"a different {', '.join(changed)} for it. A recorded entry is the evidence "
                "that the removed code is what it says it is; editing it in place leaves the "
                "same faithless record as deleting it, with the frozen file still on disk to "
                "make the drop look complete (#12291). REMEDY: restore the recorded "
                f"field(s), or state the correction: `--allow-archive-shrink {kind}:{symbol} "
                "--allow-archive-shrink-reason <why>`."
            )

    # A waiver is a RECORD. One that waived nothing is a false record, and it is the kind of
    # false record that makes a later reader believe a departure was checked (#12237's rule).
    for key in sorted(set(waivers) - used):
        kind, symbol = key
        if kind == MODULE:
            # ⛔ THE ARCHIVE IS NOT THE ARM-1 RECORD, so `base_entries` alone is the wrong
            # question and its answer was false in the ORDINARY case: a module present at the
            # base AND here printed "does not record `tortoise.x` (module) at all" while the
            # same run listed it on both sides. A `module` removal is answered by the PACKAGE
            # TREE (arm 1 above), so ask that first; the round-3 wording split just never
            # reached this branch (review F2 of PR #14039, the same "the verdict was right, the
            # stated fact was false" class the sibling branch was fixed for).
            #
            # ⛔ AND NOT THE TREE *INSTEAD* OF THE ROW: an ARCHIVED module is a row at the base
            # and a module on NEITHER side, so answering only from the package tree flipped the
            # same falsity one branch over — "carries no `tortoise.gone` (module) at all" over a
            # base manifest that records it (measured: `test_a_waiver_for_a_row_that_never_LEFT_
            # says_so_without_claiming_the_base_lacks_it` reds). The three answers are distinct
            # facts and each states its own: the ROW is here, the LIVE module is here, neither.
            if key in base_entries:
                # ⛔ THE ROW AND THE LIVE MODULE ARE TWO FACTS (review F1 of the P8b landing).
                # An ARCHIVED module is a row at the base and a module on NEITHER side — the
                # ordinary case for `archive/`, not an edge case — so "the base records it"
                # does NOT imply "this tree still carries it". Asking only the row printed
                # "this tree still carries it, so nothing was removed" over a module the base
                # had already frozen and this tree does not carry at all: the verdict (the
                # waiver waives nothing) was right and the stated fact was false, which is the
                # class this loop exists to remove, on the one branch still unasked. Measured
                # on the committed row (`module:tortoise.archived.search_legacy`): a waiver
                # for it claimed the tree carries a module present in neither tree.
                # ⛔ ASK THE LIVE BODY, exactly as the branch below does — and ask "AT ALL"
                # through the helper that answers THAT question (round-24 review, F1). The
                # runtime answer is `False` for a module this tree carries as an UNCHANGED
                # `.pyi` stub, so the borrowed predicate took the removal branch and printed
                # "this tree does not … so the removal is UNRECORDED" over a module that is
                # still here. Arm 1b `continue`s on that shape before it can consume the
                # waiver, which is exactly how it reaches this loop.
                if _head_carries_runtime_body(root, symbol, head_paths_by_module):
                    why = (
                        f"{base_label} records `{symbol}` ({kind}) and this tree still carries "
                        "it, so nothing was removed for it to waive"
                    )
                else:
                    if _row_froze_a_stub(base_entries[key]):
                        why = _stub_waiver_why(
                            f"{base_label} already records `{symbol}` ({kind}) and this tree "
                            "does not carry it",
                            base_paths_by_module.get(symbol) or "",
                            head_paths_by_module.get(symbol) or "",
                            base_has_runtime_body=symbol in base_runtime_body_modules,
                        )
                    elif symbol in head_modules and symbol not in base_runtime_body_modules:
                        # ⛔ ASK "AT ALL" WHERE THAT IS THE QUESTION (round-24 review, F1 —
                        # measured). A module carried as an UNCHANGED `.pyi` stub is `False` to
                        # the runtime predicate on BOTH sides, so the branch below — written for
                        # a body that LEFT — said "this tree does not carry it — the removal IS
                        # recorded" over a tree whose only carrier still holds the module's
                        # `def`. Nothing left; say that. The `base_runtime_body_modules`
                        # conjunct keeps the real removal shapes (the base's runtime body gone)
                        # on the branch below, which states the truth for a removal.
                        why = _still_carried_why(
                            f"{base_label} already records `{symbol}` ({kind})",
                            head_paths_by_module.get(symbol) or "",
                            base_carried_code=symbol in base_body_modules,
                        )
                    else:
                        why = (
                            f"{base_label} already records `{symbol}` ({kind}) and this tree does "
                            "not carry it — the removal IS recorded by that `module` entry, so the "
                        "waiver waives nothing"
                    )
            elif symbol in base_modules:
                # ⛔ ASK THE HEAD SIDE, and ask it about the BODY (review P2 of PR #14039). Set
                # membership is a PATH set, so it was `True` in BOTH shapes that DID remove a
                # module: arm 1's (the file left — `base_modules - head_modules` reaches here
                # only when the removal is already recorded, since an unrecorded one is reported
                # by arm 1 and would have CONSUMED the waiver) and arm 1b's (the file stayed and
                # its body left, so the name is still in `head_modules`). In both, the old
                # wording — "this tree still carries it, so nothing was removed" — pointed the
                # operator at a removal that never happened while the run had just demanded a
                # record for the one that did. Undoing an over-statement with an under-statement
                # is not the fix either: the two answers are distinct facts and each states its
                # own, which is this loop's stated rule.
                if _head_carries_runtime_body(root, symbol, head_paths_by_module):
                    why = (
                        f"{base_label} carries `{symbol}` ({kind}) and this tree still carries "
                        "it, so nothing was removed for it to waive"
                    )
                else:
                    # ⛔ SPLIT ON THE ROW, NOT ON THE MODULE (review P2 of PR #14039). The body
                    # being gone does NOT imply a row records it: arm 1b `continue`s on
                    # scaffolding-on-BOTH-sides BEFORE it can consume the waiver, so a module
                    # whose base file was docstring-only arrives here with NO `module` entry
                    # anywhere in the tree — and the one-sentence version told the operator "the
                    # removal IS recorded by the `module` entry you also passed" over a tree that
                    # has no such entry. Verdict right, stated fact false: this loop's own
                    # recurring class.
                    #
                    # ⛔ AND ASK WHETHER ANYTHING WAS REMOVED AT ALL (round-23 review, F2). Arm
                    # 1's scaffolding skip `continue`s BEFORE it can consume a waiver, so a
                    # waiver over a deleted body-less module arrives here with no entry and no
                    # removal — and the fallback below announced "NO `module` entry records it,
                    # so the removal is UNRECORDED" about a deletion that removed no code. The
                    # verdict (the waiver waives nothing) was right; the stated fact was false,
                    # and it sent the operator looking for a record of a removal that never
                    # happened. `base_body_modules` is the same answer arm 1 cancelled on, so the
                    # two cannot disagree about the same file.
                    if symbol not in base_body_modules:
                        why = (
                            f"{base_label} carries `{symbol}` ({kind}) and NOTHING was removed: "
                            "no file the base held for it carries a statement (a docstring alone, "
                            "or nothing), so there is no removal for a record to name"
                        )
                    elif symbol in head_modules and symbol not in base_runtime_body_modules:
                        # ⛔ THE OTHER "STILL CARRIES IT" SHAPE, AND IT IS NOT THE RUNTIME ONE
                        # (round-24 review, F1 — measured). A module whose only carrier is a
                        # `.pyi` stub is `False` to `_head_carries_runtime_body` on BOTH sides,
                        # so the branches below — written for a body that LEFT — announced a
                        # record for a removal that never happened while the very same file held
                        # the module's `def`. Nothing left; say so.
                        #
                        # ⛔ AND THE `base_runtime_body_modules` CONJUNCT IS LOAD-BEARING: on a
                        # real arm-1b shape (base `.py` body gone, head `.pyi` stub) arm 1b does
                        # NOT consume the waiver when the row exists — it `continue`s past it —
                        # so that shape reaches here too, and "nothing was removed" would be the
                        # false half of the split. Only a base with no runtime body is this
                        # branch's.
                        why = _still_carried_why(
                            f"{base_label} carries `{symbol}` ({kind})",
                            head_paths_by_module.get(symbol) or "",
                            base_carried_code=True,
                        )
                    elif key in working and not _row_froze_a_stub(working[key]):
                        why = (
                            f"{base_label} carries `{symbol}` ({kind}) and this tree does not "
                            "— the removal IS recorded by the `module` entry you also passed, "
                            "so the waiver waives nothing"
                        )
                    elif key in working:
                        # ⛔ ASK THE RECORD, NOT THE KEY (review F1 of round 17). Arm 1b appends
                        # `_record_froze_a_stub` and `continue`s BEFORE it consumes the waiver, so
                        # on the shape the anti-stub rule was added for this loop re-derives the
                        # same key, takes `key in working`, and printed "the removal IS recorded"
                        # ONE RUN after printing "THE RECORD FROZE A STUB" — two contradicting
                        # statements about the same entry, the false one pointing away from the
                        # only route that clears it.
                        # ⛔ AND NAME A SOURCE THAT ACTUALLY EXISTS (review F1 of round 19).
                        # `_modules_with_a_body` admits a `.pyi` module (it filters only
                        # `__init__.py`), so membership here does NOT mean the base holds the `.py`.
                        # An earlier round cleared this site on the opposite assumption.
                        #
                        # ⛔ AND SPLIT ON WHETHER A RUNTIME SOURCE EVER EXISTED (review F1 of
                        # round 20). "Restore the source from a commit that still holds it" is
                        # unfollowable for a module whose ONLY carrier is a stub: no commit holds a
                        # `.py`, and re-running the writer just reproduces this row — the writer's
                        # own rule says a stub-only module's only code IS the stub, so that row IS
                        # a record and there was no runtime source to lose. Telling that operator
                        # to hunt history for a file that never existed, AND that their correct row
                        # is "not a record", is false on both counts. `base_paths_by_module`
                        # PREFERS the runtime carrier (the sorted builder replaces a stored `.pyi`
                        # as soon as any non-`.pyi` is seen), so a `.pyi` there is positive
                        # proof the base had nothing else. The split lives in ONE helper
                        # (`_stub_waiver_why`), shared with the `key in base_entries` copy, so the
                        # two can no longer drift apart — two rounds fixed one and left the other.
                        why = _stub_waiver_why(
                            f"{base_label} carries `{symbol}` ({kind}) and this tree does not",
                            base_paths_by_module.get(symbol) or "",
                            head_paths_by_module.get(symbol) or "",
                            base_has_runtime_body=symbol in base_runtime_body_modules,
                        )
                    else:
                        why = (
                            f"{base_label} carries `{symbol}` ({kind}) and this tree does not "
                            "— and NO `module` entry records it, so the removal is UNRECORDED: "
                            "the entry is what is missing, not what this waiver waives"
                        )
            elif symbol in head_modules:
                why = (
                    f"{base_label} carries no `{symbol}` ({kind}) and this tree does — that is "
                    "an ADDITION, so nothing was removed for it to waive"
                )
            else:
                why = f"{base_label} carries no `{symbol}` ({kind}) at all"
        elif key in base_entries:
            # The base DOES record it and it is still here: the stated fact must not claim
            # otherwise (review round 3 — "does not record `symbol` at all" was false on this
            # path, and misdirects the operator to look for a removal that never happened).
            why = (
                f"{base_label} records `{symbol}` ({kind}) and this tree still carries it, so "
                "nothing was removed for it to waive"
            )
        else:
            why = f"{base_label} does not record `{symbol}` ({kind}) at all"
        out.append(
            f"--allow-archive-shrink {waivers[key]!r} waives nothing: {why}. Drop the "
            "flag (an override that replaces a real record is the state this arm exists to "
            "make visible)"
        )
    return out


def check(
    root: Path | None = None,
    *,
    require_entries: bool = False,
    base_ref: str | None = None,
    allowed_shrink: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Every violation, or an empty list when the archive is consistent.

    `require_entries` is OFF by default because the documented restore path ends with a LEGAL
    empty archive: restoring the last entry deletes its row and file, and `check` must then be
    clean (`archive/README.md` step 4). The `surface-guard` job passes the flag, where an empty
    archive means the archived code was deleted rather than restored.

    `base_ref` is OFF by default and turns on the SHRINK arms (`shrink_violations`), which need
    a ref to compare against: every other arm here iterates the entries the manifest FINDS, so
    none of them can see an entry that is gone or a module that was deleted. `None` = the arm
    does not run; a string (possibly empty → the default branch) = it runs, and an unresolvable
    ref is a REFUSAL rather than a silent pass. Arm 2 recognises a RESTORE (the row left, the
    symbol is live again, AND the live body is the one this row recorded —
    `_live_body_is_the_recorded_one`; a live NAME alone is not a restore, as `archive/README.md`
    says) and stays green, so the restore procedure in `archive/README.md`
    is untouched by turning the arm ON — the live surface read for `surface_violations` below is
    the same answer it reads, passed through rather than derived twice.
    """
    root = Path(root or ROOT)
    manifest = load_manifest(root / "archive" / "MANIFEST.json")
    entries = manifest["entries"]
    problems: list[str] = []
    surface = live_surface(root)
    problems += manifest_violations(manifest, root / "archive")
    if require_entries and not entries:
        # A VACUOUS PASS. With no entries every arm below is silent, so a PR that empties the
        # manifest and deletes the archived files would be green — in the job whose whole point
        # is that removed code stays recoverable. Off by default (see the docstring): the
        # restore path legitimately ends at zero.
        problems.append(
            "the archive is EMPTY — batch 0 landed one entry and every removal batch ADDS one; "
            "an empty manifest means the archived code was deleted, not that there is none"
        )
    problems += file_violations(entries, root / "archive", root)
    problems += surface_violations(entries, surface, root)
    problems += package_violations(root)
    problems += index_violations(entries, root)
    if base_ref is not None:
        problems += shrink_violations(
            root, base_ref=base_ref, allowed=allowed_shrink, surface=surface
        )
    return problems


# ── the rendered index ────────────────────────────────────────────────────────

def render_index(manifest: dict) -> str:
    entries = _sorted_entries(list(manifest.get("entries") or []))
    by_kind = {kind: [e for e in entries if e.get("kind") == kind] for kind in KINDS}
    summary = " · ".join(
        f"{len(by_kind[kind])} archived {INDEX_LABEL[kind][2]}" for kind in KINDS
    )
    lines = [
        "# Archive index — code removed from the product surface",
        "",
        "> **Generated** by `uv run python tools/archive_surface.py index`. Do not edit by hand.",
        "> The machine-readable record is [`MANIFEST.json`](MANIFEST.json); this file is its",
        "> readable view, and `tools/archive_surface.py check` fails when the two disagree.",
        "",
        "An archived symbol is **out of the product**: it is not importable from `tortoise`, it",
        "is not served on the MCP surface, and no retired-forward name points at it. The code",
        "is kept here so a removal can be inspected and — if it was a mistake — read back.",
        "The mechanism and its rules: [`README.md`](README.md).",
        "",
        f"**{summary}.**",
        "",
    ]
    for kind in KINDS:
        title, label, _plural = INDEX_LABEL[kind]
        rows = by_kind[kind]
        lines += [f"## {title} ({len(rows)})", ""]
        if not rows:
            lines += ["_None archived yet._", ""]
            continue
        lines += [
            f"| archived {label} | batch | reason | archived from |",
            "|---|---|---|---|",
        ]
        for entry in rows:
            lines.append(
                "| `{symbol}` | {batch} | {reason} | `{src}` @ `{sha}` |".format(
                    symbol=entry.get("symbol"),
                    batch=entry.get("batch"),
                    reason=str(entry.get("reason", "")).replace("|", "\\|"),
                    src=entry.get("archived_from"),
                    sha=str(entry.get("archived_at") or "")[:12],
                )
            )
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def write_index(root: Path | None = None) -> Path:
    root = Path(root or ROOT)
    manifest = load_manifest(root / "archive" / "MANIFEST.json")
    path = root / "archive" / "INDEX.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_index(manifest), encoding="utf-8")
    return path


# ── writing an archive entry ──────────────────────────────────────────────────

def _git(root: Path, *args: str) -> tuple[int, str]:
    """(exit status, stdout) for one git command, or (-1, "") when git cannot run."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:  # pragma: no cover - env
        return -1, ""
    return proc.returncode, proc.stdout


def default_branch_ref(root: Path) -> str | None:
    """The ref that names this repo's default branch, as this clone knows it.

    git's own `refs/remotes/origin/HEAD` first, then the conventional names (`origin/main`,
    `main`, `origin/master`, `master`) for a clone or a test fixture that has no remote HEAD.
    """
    # git's OWN answer first: a repo whose default branch is `develop` but which still carries a
    # `main` ref (mirror, legacy branch, tooling) would otherwise be anchored to `main`, which
    # need not be an ancestor of the default branch at all.
    code, out = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if code == 0 and out.strip():
        return out.strip().removeprefix("refs/remotes/")
    for ref in ("origin/main", "main", "origin/master", "master"):
        code, _ = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if code == 0:
            return ref
    return None


def block_holds(
    root: Path, kind: str, symbol: str, blocks: list[tuple[str, str, str | None]]
):
    """A candidate predicate: does this commit hold EVERY block, byte for byte?

    The block comparison the GATE uses (`expected_block`), so "the anchor holds the code" means
    the same thing on the way in and on the way out.

    ⛔ READ THROUGH THE LINK, LIKE THE WRITER DOES (round-23 review, P1 — measured). The writer
    freezes `path.read_text()`, which FOLLOWS a symlink, so comparing that frozen block against
    `_git_blob` — the LINK TEXT for mode `120000` — could never hold: `archive module tortoise.x`
    refused with "no commit on main holds this code" at the very commit that holds it (measured:
    `holds(base) = False` while `_git_blob(base, path) == '../../shared/alias.py'`). That is a red
    with no green route, the class rounds 19–21 closed twice elsewhere. `None` still reads as
    "does not hold", so an unreadable candidate is unaffected.
    """

    def holds(candidate: str) -> bool | None:
        """True / False / **None for "could not be evaluated"**.

        ⛔ THREE ANSWERS, NOT TWO (round-25 review, P3 — measured). A link chain past `_LINK_HOPS`
        answers `_UNREADABLE`, and calling that "this commit does not hold the code" made the
        writer refuse with two false facts — "no commit on main holds this code … the working tree
        differs from every commit there" — on a CLEAN tree whose commit holds the code through the
        chain, printing a remedy that cannot clear the red. The caller now names the bound.
        """
        unreadable = False
        for path, segment, node_key in blocks:
            blob = _blob_following_links(root, candidate, path)
            if blob is _UNREADABLE:
                unreadable = True
                continue
            if blob is _DANGLING:
                return False  # dangles: this commit provably holds no code here
            expected = expected_block(kind, symbol, path, blob, node_key)
            if expected is None or expected.rstrip() != segment.strip("\n"):
                return False
        if unreadable:
            return None
        return True

    return holds


def anchor_sha(root: Path, paths: list[str], holds=None) -> tuple[str, str]:
    """(commit on the default branch that last HELD *paths*, the ref it came from).

    ⛔ NOT `git rev-parse HEAD`. This repo lands by squash, so a batch PR's own commit never
    reaches `main`; an archive that cites it is unreadable from `main` the moment it merges,
    and `verify-anchors` (which the required `surface-guard` job runs on a depth-0 clone) would
    red every later run. The durable citation is the commit on the default branch that held the
    source, which is also the state a restore would want.

    ⛔ AND NOT `git rev-list -1 <ref> -- <paths>`, which answers "the newest commit that
    MODIFIED these paths" — a commit that DELETED them answers too, and holds nothing. That is
    not hypothetical: the batch PR itself deletes the source, so the squash-merge commit touches
    every archived path and would become the anchor, naming a commit that never contained the
    code (measured in review cycle 6). The candidate list is walked newest-first and the first
    commit that holds EVERY path wins; a deletion commit simply is not a candidate.
    """
    ref = default_branch_ref(root)
    if ref is None:
        raise ArchiveRefused(
            f"cannot find a default-branch ref in {root} (tried origin/main, main, "
            "origin/master, master, origin/HEAD) — the archive records the commit on the "
            "default branch that held the code, so it must be able to name one"
        )
    code, out = _git(root, "rev-list", ref, "--", *paths)
    candidates = [c for c in (out.split() if code == 0 else []) if _SHA_RE.match(c)]
    held_a_path = False
    unreadable_here = False
    # ⛔ A DEFINITE `False` OUTRANKS AN UNREADABLE CANDIDATE (round-25 review, P2 — measured).
    # `_UNREADABLE` used to mean exactly "hop bound exhausted", so the refusal below asserted that
    # cause. Widening it (absolute/escaping target, unreadable mode) made the message state a fact
    # the code no longer implies — measured: a ONE-hop absolute link was refused with "exceeds
    # `_LINK_HOPS` (8) hops" — and worse, `unreadable_here` then MASKED the genuine working-tree
    # refusal: with a readable `False` candidate present the message claimed "This is NOT a
    # working-tree difference, and committing the source will not change it", both halves false,
    # while the real remedy (commit the source) was suppressed. A candidate that was READ and did
    # not hold the code is hard evidence; it decides.
    false_here = False
    for candidate in candidates:
        if not all(_git_blob(root, candidate, path) is not None for path in paths):
            continue
        held_a_path = True
        verdict = True if holds is None else holds(candidate)
        if verdict:
            return candidate, ref
        if verdict is None:
            unreadable_here = True
        else:
            false_here = True
    if held_a_path and unreadable_here and not false_here:
        raise ArchiveRefused(
            f"the carrier at {', '.join(paths)} cannot be READ at any commit on {ref}: the link "
            "read returned UNKNOWN for it, which happens when a chain exceeds "
            f"`_LINK_HOPS` ({_LINK_HOPS}) hops, when the link target is ABSOLUTE or leaves the "
            "repo, or when the blob mode cannot be read. Point the carrier at the file by a "
            "repo-relative path of at most "
            f"{_LINK_HOPS} hops and archive again. ⛔ This is NOT a working-tree difference (no "
            "candidate was readable and disagreeing), and committing the source will not "
            "change it."
        )
    if held_a_path:
        # The paths exist on the default branch but no commit holds THIS code: the working tree
        # carries an edit (or a newer body) that never landed there. ⛔ Not a "never landed"
        # refusal — that message would describe a state the caller is not in.
        raise ArchiveRefused(
            f"no commit on {ref} holds this code at {', '.join(paths)} — the working tree "
            "differs from every commit there, so the archived body would not be what the "
            "citation says. Commit the source (or rebase onto the default branch) first"
        )
    raise ArchiveRefused(
        f"{', '.join(paths)} is not on {ref} — this code never landed on the default branch, "
        "so there is no released state to restore. Delete it; do not archive it"
    )


def _segment(source: str, node: ast.AST) -> str:
    """The VERBATIM source of *node*, decorators included, in its ORIGINAL indentation.

    `ast.get_source_segment` starts at `node.lineno`, which for a decorated function is the
    `def` line — the decorators above it are silently dropped — and it dedents that first line
    while leaving the rest at its original column. Both are wrong for an archive whose whole
    promise is "copying this back restores what was removed": a decorated SDK method lost its
    `@_declares_apply_first_write_lane(...)` declaration, and copying the dedented body into
    the class ended it at column 0 (measured in review, P0 — the gate could not see it, because
    the digest is computed over the same truncated text).

    Hand-rolled slicing rather than `padded=True` for the same reason: `padded` fixes the
    first line's indentation but still starts at the `def`.
    """
    start = node.lineno
    for decorator in getattr(node, "decorator_list", ()) or ():
        start = min(start, decorator.lineno)
    return "\n".join(source.splitlines()[start - 1:node.end_lineno])


def _mcp_source(root: Path, tool: str) -> list[tuple[str, str, str | None]]:
    """The `ToolDefinition` literal for *tool*, plus any handler that names it.

    Handlers are found by AST rather than by a naming convention: an MCP handler is a
    function in `mcp_server.py` whose body mentions the tool name, and that is the code a
    reader needs in order to see what a removed tool DID.
    """
    registry = (root / "tortoise" / "tool_registry.py").read_text(encoding="utf-8")
    tree = ast.parse(registry)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "ToolDefinition"):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == tool
            ):
                return [
                    ("tortoise/tool_registry.py", _segment(registry, node),
                     f"ToolDefinition:{tool}")
                ]
    raise ArchiveRefused(
        f"{tool} has no `ToolDefinition` in tortoise/tool_registry.py — nothing to archive. "
        "Retired-forward entries live in RETIRED_USE_INSTEAD and are NOT archived."
    )


def _code_without_docstring(node: ast.AST) -> list[ast.stmt]:
    """A function body with its docstring dropped — prose is not behaviour."""
    body = list(getattr(node, "body", []))
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _mcp_handlers(root: Path, tool: str) -> list[tuple[str, str, str | None]]:
    """The `mcp_server.py` function(s) that IMPLEMENT *tool*.

    Two shapes exist in this repo and both must be captured, because the archive's promise
    is that a reader can see what a removed tool DID:

    * the handler is named after the tool — `def tortoise_create_point(...)` (101 such
      top-level defs at #11609);
    * the handler is a shared helper whose body mentions the tool name as a string.

    ⛔ Matching only the second shape captured **8 of 72** live tools, and for those 8 it
    captured an unrelated helper (`tortoise_delete` -> `_arguments_say_preview`) while
    missing `def tortoise_delete` entirely (measured, review cycle 1). A name match is the
    primary rule; the string scan stays for the dispatch-style handlers.

    ⛔ And the string scan reads CODE, not prose: a DOCSTRING naming a tool is a comment about
    it, not a dispatch of it. `_arguments_say_preview` names `tortoise_create_point` in its
    docstring and dispatches nothing, so it was captured into `tortoise_delete`'s archive
    alongside a live helper that would be duplicated by "copy the block back" (measured in
    review cycle 4). The docstring is the one `Expr/Constant` a function starts with, and it is
    skipped here.

    ⚠ A SHARED helper that carries a tool-specific branch IS captured, and that is deliberate:
    `_arguments_say_preview` contains `if name == "tortoise_delete": return …` — the delete's
    preview contract — so dropping it would archive the registry row and lose what the tool
    actually DID. The consequence belongs to the restore recipe, not to the extraction: such a
    block is merged back INTO the existing function, never pasted as a second definition
    (`archive/README.md`).
    """
    path = root / "tortoise" / "mcp_server.py"
    if not path.exists():  # pragma: no cover - layout invariant
        return []
    source = path.read_text(encoding="utf-8")
    found: list[tuple[str, str, str | None]] = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == tool or any(
            isinstance(child, ast.Constant) and child.value == tool
            for statement in _code_without_docstring(node)
            for child in ast.walk(statement)
        ):
            # ⛔ The key is the FUNCTION's own name, not the entry symbol: a shared helper
            # captured because a branch of it serves this tool must be re-extractable as
            # ITSELF, or the anchor arm compares it to the handler and calls a correct archive
            # forged (measured: 8 of the 72 live tools).
            found.append(
                ("tortoise/mcp_server.py", _segment(source, node), f"FunctionDef:{node.name}")
            )
    return found


def _module_source(root: Path, symbol: str) -> list[tuple[str, str, str | None]]:
    """A whole module frozen verbatim. *symbol* is the dotted module path.

    This is the kind for code that was archived INSIDE the package before this mechanism
    existed (`tortoise/archived/search_legacy.py`) — an importable, shipped module, which is
    the opposite of "not available".
    """
    rel = symbol.replace(".", "/") + ".py"
    package_rel = symbol.replace(".", "/") + "/__init__.py"
    stub_rel = symbol.replace(".", "/") + ".pyi"
    package_stub_rel = symbol.replace(".", "/") + "/__init__.pyi"
    path = root / rel
    if not path.exists():
        # ⛔ A PACKAGE is archived by its DOTTED path and its source is `<dotted>/__init__.py`.
        # Without this fallback the rule arm 1 grades was UNRUNNABLE for the very shape it was
        # built for (review P1 of PR #14039): `_modules_with_a_body` grades an `__init__.py` by
        # CONTENT, so deleting a 406-line `tortoise/__init__.py` reports `tortoise: MODULE
        # REMOVED`, whose printed remedy is `archive module tortoise` — and this function refused
        # it with "no file at tortoise.py" while CI passes no waiver, so the operator had a
        # correct red and NO route. The archived FILE stays `<dotted>.py` (`archive_rel`), so no
        # `__init__.py` ever lands under `archive/` (`package_violations` refuses that), and
        # `archived_from` names the init file the body actually came from.
        if (root / package_rel).exists():
            rel, path = package_rel, root / package_rel
        else:
            # …and a `.pyi`-only module is graded by every other arm (`_package_modules` reads
            # `*.py*`, `module_name` strips `.pyi`, arm 1b's stub rule), so the remedy must be
            # able to freeze one — otherwise the red the gate prints has no route, one suffix
            # over from the package fallback above (round-7 review).
            if (root / stub_rel).exists():
                rel, path = stub_rel, root / stub_rel
            elif (root / package_stub_rel).exists():
                # ⛔ THE FOURTH SPELLING (round-18 review, P2 — measured). `module_name` strips
                # `.pyi`, so `<dotted>/__init__.pyi` IS a carrier of the module to arm 1 and arm
                # 1b — both red it — but this resolution knew only three spellings, so the
                # printed remedy was REFUSED ("no stub at tortoise/pp.pyi") and the red had no
                # route at all. One suffix over from the package fallback above, and the same
                # class: the gate reds a shape the writer cannot freeze.
                rel, path = package_stub_rel, root / package_stub_rel
            else:
                raise ArchiveRefused(
                    f"module {symbol} has no file at {rel}, no package at {package_rel}, no "
                    f"stub at {stub_rel} and no package stub at {package_stub_rel} — nothing to "
                    "archive"
                )
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ArchiveRefused(f"could not read {rel}: {exc}") from exc
    # ⛔ A LEFTOVER SCAFFOLD IS NOT THE MODULE (fresh-context review of PR #14039, P2).
    # The package fallback above exists so arm 1's printed remedy RUNS for the shape arm 1
    # grades — but it resolves `<dotted>/__init__.py` whether or not that file still carries the
    # module's code. Deleting `tortoise/x.py` (the body) and leaving `tortoise/x/__init__.py` as a
    # docstring made this writer freeze `"""Scaffolding."""` as the SOURCE OF RECORD for the
    # whole module, and `shrink_violations` then credits the removal on the row's EXISTENCE
    # (`if key in working: continue`): every arm green, the removed body in NO file under
    # `archive/` — the exact loss `archive/` exists to prevent, reading as a record. Measured on
    # the pre-fix bytes: the CLI said "archived module tortoise.x", `check --base-ref` said
    # "archive consistent", `verify-anchors` said OK, and the removed `f39` was in no archived
    # file.
    #
    # The legitimate package shape is unaffected (the `tortoise/__init__.py` this fallback was
    # built for carries 406 lines (12 top-level statements)), and so is the `.pyi` fallback above/below (a stub-only
    # module's only code IS the stub). The route this refusal names is the one
    # `archive/README.md` already gives: restore the source from the base, archive it, then
    # delete it again in the same batch.
    #
    # ⛔ AND THE SAME HOLE EXISTS ON THE ORDINARY `<dotted>.py` PATH (round-15 review, P2 — the
    # gate was scoped to `package_fallback` and therefore exempted the commonest shape). Arm 1b
    # grades a module whose file is still there but whose BODY is gone as `MODULE BODY REMOVED`,
    # and the remedy it prints is `archive module <dotted>` — so emptying `tortoise/x.py` in
    # place and archiving it froze `"""Scaffolding."""` as the source of record, after which
    # `shrink_violations` credited the row on its EXISTENCE and the whole run went green with the
    # module's code in no file under `archive/`. Measured: `archived_from = tortoise/x.py`, the
    # frozen block was the docstring, `shrink_violations` -> `[]`, `check --base-ref` -> OK.
    # The rule is therefore about the RESOLVED SOURCE, not about which fallback found it: the only
    # file shape exempt is a `.pyi`, because a stub's annotations ARE its statements.
    # ⛔ THE EXEMPTION WAS THE SAME SUFFIX TEST, ONE SIDE OVER (round-19 review, P3 — measured).
    # A suffix-keyed exemption let the WRITER freeze an EMPTY `.pyi` block (`sha256` of `b""`), and
    # `file_violations` then red the row it had just written ("has an EMPTY frozen block") — a red
    # whose only clear is deleting the record, i.e. no route that keeps it. A real stub is a
    # `FunctionDef`, so the content rule refuses only a file that declares nothing.
    if not _init_carries_a_body(source):
        # ⛔ ASK THE MODULE'S OTHER CARRIERS BEFORE REFUSING (round-23 review, F1). The resolution
        # above picks a carrier by PATH EXISTENCE, and `module_name` collapses `<dotted>.py`,
        # `<dotted>/__init__.py` and `<dotted>.pyi` onto ONE symbol — so a body-less leftover
        # `.py` hid the module's real code behind it, and the route this refusal printed
        # (`git checkout <base-sha> -- <dotted>.py`) restored the same body-less file and was
        # refused again: a red with no route, the shape the package fallback above was added for,
        # one carrier over. A body-carrying carrier anywhere in the module's set is the thing to
        # freeze, so walk the set in the same preference order and refuse only when NONE carries a
        # body. A `.pyi` carries (a stub under the shipped package IS importable surface, and it is
        # the module's only code when nothing else carries it), which the CONTENT rule makes on its
        # own: a real stub is a `FunctionDef`, so `_init_carries_a_body` accepts it and no suffix
        # test is needed. ⛔ NOT "the exemption the suffix test above already makes" — round 19
        # REMOVED that exemption (`if not rel.endswith(".pyi") and not _init_carries_a_body(...)`)
        # because a suffix-keyed one let the writer freeze an EMPTY `.pyi`; naming a mechanism this
        # same file no longer has (round-23 review, P2) sends the next reader looking for it.
        for other in (package_rel, stub_rel, package_stub_rel):
            other_path = root / other
            if other == rel or not other_path.exists():
                continue
            try:
                other_source = other_path.read_text(encoding="utf-8")
                if not other.endswith(".pyi"):
                    ast.parse(other_source, filename=str(other_path))
            except (OSError, SyntaxError):
                continue
            # ⛔ CONTENT FOR EVERY SPELLING, INCLUDING `.pyi` (round-20 review, P2 — measured).
            # The suffix arm accepted an EMPTY stub here, so `archive module` froze a 0-char block
            # that `file_violations` then red while `shrink_violations` stayed green — the state the
            # resolved-carrier fix closes, one walk over. A real stub is a `FunctionDef`, so the
            # content rule keeps it.
            if _init_carries_a_body(other_source):
                rel, path, source = other, other_path, other_source
                break
        else:
            # ⛔ NAME EVERY CARRIER, NOT ONE GUESS (round-21 review, P2 — measured, pre-existing).
            # `<dotted>.py` is only ONE of the four paths a module can live at, so with a bodyless
            # `<dotted>.py` beside a code-carrying `<dotted>/__init__.py` (a shape CONSTRUCTED in
            # the test: no module in this repo ships it — `tortoise/projection.py`, the nearest,
            # carries a body) the single path named here was the empty one: restoring it satisfied the
            # instruction and the writer refused again, identically, forever. The set is cheap to
            # name and each member is a file a human can look for.
            plain = [
                symbol.replace(".", "/") + suffix
                for suffix in (".py", "/__init__.py", ".pyi", "/__init__.pyi")
            ]
            raise ArchiveRefused(
                f"module {symbol} resolves to {rel}, which carries no body of its own — a "
                "docstring alone, or nothing. Freezing it would record SCAFFOLDING as the source "
                "of record for code that is gone, and `check --base-ref` credits a row on its "
                "EXISTENCE, so the removal would read as recorded while no file under `archive/` "
                "held it. Restore the carrier that HELD the code from the base revision first "
                f"(`git checkout <base-sha> -- <one of: {', '.join(plain)}>`), archive it, and "
                "delete it again in the same batch."
            )
    return [(rel, source, None)]


def _sdk_source(root: Path, method: str) -> list[tuple[str, str, str | None]]:
    path = root / "tortoise" / "sdk.py"
    source = path.read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if not (isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK"):
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method:
                return [
                    (str(path.relative_to(root)), _segment(source, child),
                     f"FunctionDef:{method}")
                ]
    raise ArchiveRefused(
        f"{method} is not a method of TortoiseSDK in tortoise/sdk.py — nothing to archive"
    )


def render_archive_file(
    kind: str,
    symbol: str,
    batch: str,
    reason: str,
    archived_from: str,
    sha: str,
    blocks: list[tuple[str, str, str | None]],
) -> tuple[str, list[dict]]:
    """Render one archived file, and the `blocks` digest list for its manifest row.

    One function for both, so a repair produces a byte-identical file: `check` re-parses
    and re-hashes the blocks, so a render that differed by a byte would red for no reason.
    """
    header = [
        f"# Archived {kind} `{symbol}` — REMOVED from the product surface (#11609).",
        "#",
        f"# batch:   {batch}",
        f"# reason:  {reason}",
        f"# source:  {archived_from} @ {sha}",
        "#",
        "# This file IS the source of record. To restore: copy each block below back into the",
        "# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than",
        "# one file), then run `uv run python tools/archive_surface.py check` (which fails",
        "# while `archive/` and the live surface disagree).",
        "#",
        "# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and",
        "#    not pointed at by any retired-forward name. Do not import this file.",
        "",
        f'ARCHIVED_KIND = {kind!r}',
        f'ARCHIVED_SYMBOL = {symbol!r}',
        f'ARCHIVED_BATCH = {batch!r}',
        f'ARCHIVED_FROM = {archived_from!r}',
        f'ARCHIVED_AT = {sha!r}',
        "",
    ]
    body: list[str] = []
    frozen: list[dict] = []
    for source_path, segment, node_key in blocks:
        text_block = segment.rstrip()
        body += [
            f"{VERBATIM_BANNER}{source_path} ──",
            "",
            text_block,
            "",
        ]
        # The digest is what lets `check` tell "the frozen source" from "a file that used to
        # hold it": a banner-presence check alone passed on a body that had been emptied.
        frozen.append({
            "path": source_path,
            "node": node_key,
            "sha256": _sha256(text_block.strip("\n")),
        })
    return "\n".join(header + body), frozen


def archive_rel(kind: str, batch: str, symbol: str) -> str:
    """`archive/<subdir>/<batch>/<mirrored symbol>.py` — one symbol, one path, always.

    A `module` symbol is a DOTTED path, so its file mirrors that path
    (`tortoise.archived.legacy` → `archive/modules/<batch>/tortoise/archived/legacy.py`).
    Flattening it to its basename made two distinct modules collide on one file, and the
    second write silently destroyed the first frozen body — a loss, from a command that
    exited 0 (measured in review). A bare name (`tortoise_search`) mirrors to itself, so the
    MCP/SDK layout is unchanged.
    """
    mirror = symbol.replace(".", "/") if kind == MODULE else symbol
    return f"archive/{SUBDIR[kind]}/{batch}/{mirror}.py"


def archive_symbol(
    kind: str,
    symbol: str,
    batch: str,
    reason: str,
    root: Path | None = None,
) -> dict:
    """Freeze *symbol* into `archive/` and append its manifest row. Does NOT delete it.

    The caller deletes the symbol from its source file in the same batch; running this
    first is what makes the recorded commit and the extracted source exact.
    """
    root = Path(root or ROOT)
    if kind not in KINDS:
        raise ArchiveRefused(f"unknown kind {kind!r} — expected one of {KINDS}")
    # ⛔ WHITESPACE IS NOT A REASON (round-17 P3, measured). Raw truthiness let `--reason '   '`
    # through: the row was written carrying `"reason": "   "` and `file_violations` accepted it,
    # so the one field a future reader consults for WHY read as checked while saying nothing —
    # the same hole the pairing guards name for `--flag` ("an unexplained removal could be waived
    # by pressing space"). `str.strip()` was not enough either (round-18 review, P2 — measured):
    # ZWSP `\u200b`, LRM `\u200e` and BOM `\ufeff` are not `isspace()`, so each was ACCEPTED and
    # rendered as nothing, and a non-`str` reason raised a bare `AttributeError` out of a function
    # whose other refusals are `ArchiveRefused`. The reason is NORMALISED in place, so the padding
    # that made it pass cannot survive into the record.
    if not isinstance(reason, str):
        raise ArchiveRefused(
            f"reason must be text, not {type(reason).__name__} — a record's WHY is what a future "
            "reader consults"
        )
    reason = reason.strip()
    if not batch or not _is_a_stated_reason(reason):
        raise ArchiveRefused("a batch slug and a reason are required: they are what a reader has")
    if not _BATCH_RE.match(batch):
        raise ArchiveRefused(
            f"batch {batch!r} is not a slug — a batch becomes a DIRECTORY name under archive/, "
            "so it must match [a-z0-9][a-z0-9._-]* (no path separators, no `..`)"
        )
    if any(ch in reason for ch in "\n\r\x00") or any(ord(ch) < 32 for ch in reason):
        raise ArchiveRefused(
            "`reason` must be ONE line: it is written into the archived file's header and into "
            "the generated index table, so a newline escapes the comment block"
        )
    if not symbol or "/" in symbol or ".." in symbol:
        raise ArchiveRefused(f"symbol {symbol!r} is not a bare symbol/module name")
    # The real traversal guard: every segment of the mirrored path must be a plain identifier.
    # (The previous check compared `part == ".."`, already excluded one line above, so it could
    # never fire while reading as the guard — the containment check below is what actually ran.)
    if any(
        not (part.isidentifier() and _NAME_RE.match(part))
        for part in symbol.replace(".", "/").split("/")
    ):
        raise ArchiveRefused(
            f"symbol {symbol!r} is not a dotted path of plain identifiers — a segment becomes a "
            "path component under archive/"
        )

    manifest_path = root / "archive" / "MANIFEST.json"
    manifest = load_manifest(manifest_path)
    if any(e.get("kind") == kind and e.get("symbol") == symbol for e in manifest["entries"]):
        raise ArchiveRefused(f"{symbol} is already archived ({kind})")

    if kind == MCP_TOOL and symbol in _forward_names(root):
        # ⛔ A `RETIRED_USE_INSTEAD` name leaves the ADVERTISED surface but is STILL SERVED — it
        # is inside the product, and the gate refuses the row. Writing it and printing "now
        # delete it" told an operator to delete a live, served handler, and the archive went red
        # only after the source was gone (measured, round 8). The writer must not write a row its
        # own gate rejects.
        raise ArchiveRefused(
            f"{symbol} is a RETIRED_USE_INSTEAD forward entry — still SERVED, so it is inside "
            "the product and is NOT archived. Removing the forward entry is a separate decision "
            "(`docs/product/canonical-mcp-tools.md`)"
        )
    if kind == MCP_TOOL:
        blocks = _mcp_source(root, symbol) + _mcp_handlers(root, symbol)
    elif kind == SDK_METHOD:
        blocks = _sdk_source(root, symbol)
    else:
        blocks = _module_source(root, symbol)

    archived_from = blocks[0][0] if kind == MODULE else SOURCE_OF[kind]
    # ⛔ THE ANCHOR IS A COMMIT ON THE DEFAULT BRANCH, not this branch's HEAD (see
    # `anchor_sha`): a squash merge would otherwise orphan the citation the archive rests on.
    # ⛔ The anchor must hold the CODE, not merely the path: for `mcp-tool` / `sdk-method` the
    # source FILE survives the removal, so the merge commit is a candidate and would become the
    # anchor — the row would then cite a commit that does not hold the block (measured, round 8).
    sha, _ref = anchor_sha(
        root,
        sorted({p for p, _s, _n in blocks}),
        block_holds(root, kind, symbol, blocks),
    )
    rel = archive_rel(kind, batch, symbol)
    path = root / rel
    # Containment, belt-and-braces after the slug/symbol validation: resolve and refuse
    # anything that escapes `archive/` (a traversal here writes into the shipped package).
    resolved = path.resolve()
    if not resolved.is_relative_to((root / "archive").resolve()):
        raise ArchiveRefused(f"{rel} resolves outside archive/ — refusing to write it")
    # A path collision is a DESTRUCTION, not a naming preference: `archive module tortoise.a.foo`
    # then `archive module tortoise.b.foo` used to overwrite the first frozen body under exit 0
    # (measured in review), leaving two manifest rows pointing at one file. `archive_rel` mirrors
    # the dotted path so distinct symbols cannot collide, and this refuses the remaining case —
    # a file already on disk that belongs to someone else.
    if path.exists():
        existing = _module_metadata(path).get("ARCHIVED_SYMBOL")
        raise ArchiveRefused(
            f"{rel} already holds archived code ({existing!r}) — refusing to overwrite a frozen "
            "body; archive it under a different batch, or move it deliberately"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    text, frozen = render_archive_file(
        kind, symbol, batch, reason, archived_from, sha, blocks
    )
    path.write_text(text, encoding="utf-8")

    entry = {
        "kind": kind,
        "symbol": symbol,
        "batch": batch,
        "reason": reason,
        "file": rel,
        "archived_from": archived_from,
        "archived_at": sha,
        "sources": [p for p, _s, _n in blocks],
        "blocks": frozen,
    }
    manifest["entries"].append(entry)
    save_manifest(manifest, manifest_path)
    write_index(root)
    return entry


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="archive_surface.py", description="The surface archive (#11609)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    check_parser = sub.add_parser(
        "check", help="assert the archive is consistent (exit 1 on violations)"
    )
    check_parser.add_argument(
        "--anchors",
        action="store_true",
        help=(
            "also require every `archived_at` commit to be readable in this clone AND to "
            "hold the frozen body (needs real history: a fetch-depth-1 clone cannot "
            "answer, and will refuse)"
        ),
    )

    sub.add_parser(
        "verify-anchors",
        help=(
            "PROVE every restore anchor: each `archived_at` commit must be readable in this "
            "clone AND hold its frozen block exactly (no `tortoise` import — usable in a "
            "full-history CI job that installs nothing)"
        ),
    )

    check_parser.add_argument(
        "--require-entries",
        action="store_true",
        help=(
            "fail when the manifest has NO entries (CI passes this: an empty archive means the "
            "archived code was deleted, not restored — the plain form stays green so the "
            "documented restore of the last entry can finish)"
        ),
    )
    check_parser.add_argument(
        "--base-ref",
        nargs="?",
        const="",
        default=None,
        metavar="REF",
        help=(
            "ALSO grade the SHRINK direction (#12402, #12291) against REF (default: the "
            "default branch), compared at its MERGE BASE with HEAD: a module that left "
            "tortoise/ must carry a `module` entry, and an entry that left or was rewritten "
            "in archive/MANIFEST.json must be restored or waived. Off unless given; an "
            "unresolvable ref is a REFUSAL, never a pass"
        ),
    )
    check_parser.add_argument(
        "--allow-archive-shrink",
        action="append",
        default=[],
        metavar="KIND:SYMBOL",
        help=(
            "waive ONE deliberate removal (repeatable): `module:<dotted.path>`, "
            "`mcp-tool:<tool>` or `sdk-method:<method>`. Needs --base-ref and "
            "--allow-archive-shrink-reason, and a waiver that waives nothing is reported"
        ),
    )
    check_parser.add_argument(
        "--allow-archive-shrink-reason",
        default=None,
        metavar="TEXT",
        help="WHY the waived removal is legitimate — it belongs in the PR body, not only here",
    )

    index_parser = sub.add_parser("index", help="regenerate archive/INDEX.md")
    index_parser.add_argument("--check", action="store_true", help="assert it is current")

    archive_parser = sub.add_parser("archive", help="freeze a symbol into the archive")
    archive_parser.add_argument("kind", choices=KINDS)
    archive_parser.add_argument("symbol")
    archive_parser.add_argument("--batch", required=True)
    archive_parser.add_argument("--reason", required=True)

    args = parser.parse_args(argv[1:])

    try:
        if args.cmd == "check":
            # An override is a RECORD: it needs the arm it waives, and a WHY. Both refusals are
            # exit 2 (a defect in the invocation), never exit 1 — an unwaivable invocation is
            # not a finding about the tree, and reading it as one would send the operator
            # looking for a removal that does not exist.
            # `.strip()` BEFORE the pairing test, the same normalisation `cmd_cut` applies to
            # `--allow-removal-reason` and the surface tool applies to its own reason: `""` and
            # `"   "` are not STATED reasons. Raw truthiness accepted both (measured: a whitespace
            # reason satisfied the pairing test beside a real waiver), so an unexplained removal
            # could be waived by pressing space. Normalised in place so the green line echoes the
            # string that was graded. `reason_given` keeps a blank-but-PRESENT flag distinct from
            # no flag at all, the same distinction the surface tool makes.
            reason_given = args.allow_archive_shrink_reason is not None
            # ⛔ AND "STATED" IS THE PREDICATE, NOT `.strip()` (round-19 review, P2 — measured).
            # The record path was taught to refuse ZWSP/LRM/BOM; the WAIVER path still graded with
            # `.strip() or None`, so `--allow-archive-shrink-reason $'\u200b'` printed
            # `reason: ` (nothing) on a green run — the same hole, three sites over, and a waiver
            # is the only thing that replaces a record for a removal.
            _waiver_reason = (args.allow_archive_shrink_reason or "").strip()
            args.allow_archive_shrink_reason = (
                _waiver_reason if _is_a_stated_reason(_waiver_reason) else None
            )
            if bool(args.allow_archive_shrink) != bool(args.allow_archive_shrink_reason) or (
                reason_given and not args.allow_archive_shrink
            ):
                print(
                    "REFUSED --allow-archive-shrink and --allow-archive-shrink-reason go "
                    "together: an override with no stated reason is not a record, and a "
                    "reason with nothing waived waives nothing",
                    file=sys.stderr,
                )
                return 2
            if args.allow_archive_shrink and args.base_ref is None:
                print(
                    "REFUSED --allow-archive-shrink needs --base-ref: it waives a removal "
                    "only the shrink arm can see, and that arm is off without a base ref",
                    file=sys.stderr,
                )
                return 2
            problems = check(
                require_entries=args.require_entries,
                base_ref=args.base_ref,
                allowed_shrink=args.allow_archive_shrink,
            )
            if args.anchors:
                problems += anchor_unverified()
            if problems:
                print(f"ARCHIVE NOT CONSISTENT — {len(problems)} violation(s):")
                for problem in problems:
                    print(f"  ✗ {problem}")
                return 1
            manifest = load_manifest()
            entries = manifest["entries"]
            counts = ", ".join(
                f"{sum(1 for e in entries if e['kind'] == kind)} {INDEX_LABEL[kind][2]}"
                for kind in KINDS
            )
            shrink = ""
            if args.base_ref is not None:
                # Say WHAT was compared, in the green line: a shrink pass that does not name
                # its base reads identically to a run where the arm silently did nothing.
                _sha, label = resolve_base(ROOT, args.base_ref)
                shrink = f" Shrink checked against {label}"
                if args.allow_archive_shrink:
                    # ⛔ THE REASON IS REQUIRED AND WAS THEN DISCARDED (review N1 of PR #14039):
                    # a WHY that is validated and never printed is a one-flag decoration, and
                    # the green line is the only surviving record that a departure was checked.
                    shrink += (
                        f" (waived: {', '.join(args.allow_archive_shrink)}"
                        f" — reason: {args.allow_archive_shrink_reason})"
                    )
            print(f"OK — archive consistent: {len(entries)} entries ({counts}).{shrink}")
            return 0
        if args.cmd == "verify-anchors":
            # The strict verb, and the only one that turns "this clone cannot answer" into a
            # failure. It runs in the `surface-guard` job, which checks out at depth 0 — the
            # same job and the same reason as `surface_manifest.py check --provenance-strict`
            # (#11057): a gate that runs only where it cannot answer is a NO-OP gate, and
            # before this step nothing in the repo invoked `--anchors` at all (review cycle 4).
            entries = load_manifest()["entries"]
            if not entries:
                # Every other arm is silent over nothing, so an empty archive would print OK —
                # a vacuous green in the only archive gate that runs on every PR.
                problems = [
                    "the archive is EMPTY — every entry was removed; if that was a restore, "
                    "say so in the PR; if it was a deletion, the code is gone"
                ]
            else:
                problems = file_violations(entries) + anchor_unverified()
            if problems:
                print(f"ANCHORS NOT PROVEN — {len(problems)} violation(s):")
                for problem in problems:
                    print(f"  ✗ {problem}")
                return 1
            print(f"OK — {len(entries)} entr(ies): every restore anchor resolves and holds its block.")
            return 0
        if args.cmd == "index":
            if args.check:
                problems = index_violations(load_manifest()["entries"])
                if problems:
                    for problem in problems:
                        print(f"  ✗ {problem}")
                    return 1
                print("OK — archive/INDEX.md is current.")
                return 0
            path = write_index()
            print(f"wrote {path.relative_to(ROOT)}")
            return 0
        if args.cmd == "archive":
            entry = archive_symbol(args.kind, args.symbol, args.batch, args.reason)
            print(f"archived {entry['kind']} {entry['symbol']} -> {entry['file']}")
            print(f"  now delete it from {entry['archived_from']} in this batch")
            return 0
    except ArchiveRefused as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover - argparse makes this unreachable


if __name__ == "__main__":
    raise SystemExit(main())
