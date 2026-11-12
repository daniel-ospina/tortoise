#!/usr/bin/env python3
"""The surface archive — where code removed from the product lives (#11609).

THE RULE THIS IMPLEMENTS
    IN THE TARGET SET  →  kept, as the product surface
    EVERYTHING ELSE    →  REMOVED from the product, and the CODE ARCHIVED so it is
                          recoverable and inspectable (kept in the repo, NOT importable
                          FROM THE SHIPPED PACKAGE, NOT exposed)

Owner ruling, 2026-11-17 verbatim in substance (comment `6831894340` on #11609):
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
    The existing 23 `RETIRED_USE_INSTEAD` entries are therefore NOT in this archive's
    scope. They are off the *advertised* count (72) already, and
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

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from functools import cache
from pathlib import Path

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/archive_surface.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/archive_surface.py check`"
    )

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
        "modules": _package_modules(root),
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
    blob = _git_blob(root, sha, source_path)
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

def surface_violations(entries: list[dict], surface: dict) -> list[str]:
    """Every way an archived symbol can be back on the live surface, or mis-recorded.

    Pure on purpose: `tests/test_archive.py` calls it with a synthetic surface to prove
    the detector FIRES. A gate nobody has seen fire is indistinguishable from one that
    cannot.
    """
    tools = surface["tools"]
    methods = surface["methods"]
    forward = surface["forward"]
    sdk_retired = surface["sdk_retired"]
    installed_modules = surface.get("modules", set())
    out: list[str] = []
    for entry in entries:
        kind = entry.get("kind")
        symbol = str(entry.get("symbol") or "")
        if kind not in KINDS:
            out.append(f"{symbol or '<no symbol>'}: unknown kind {kind!r} (expected one of {KINDS})")
            continue
        if kind == MCP_TOOL:
            if symbol in tools:
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
            if symbol in methods:
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
        else:
            # A whole module archived from INSIDE the package. Its file must be GONE from the
            # package tree: `tortoise/archived/x.py` is importable as `tortoise.archived.x` and
            # ships in the wheel, which is the opposite of "not available".
            base = symbol.replace(".", "/")
            for candidate in (f"{base}.py", f"{base}/__init__.py", f"{base}.pyi"):
                if candidate in installed_modules:
                    out.append(
                        f"{symbol}: REAPPEARED as an importable module in the shipped package "
                        f"({candidate}) — archived code must not ship"
                    )
                    break
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
        if not entry.get("reason"):
            out.append(f"{symbol}: no `reason` — a reader cannot tell why it was removed")

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


def check(root: Path | None = None, *, require_entries: bool = False) -> list[str]:
    """Every violation, or an empty list when the archive is consistent.

    `require_entries` is OFF by default because the documented restore path ends with a LEGAL
    empty archive: restoring the last entry deletes its row and file, and `check` must then be
    clean (`archive/README.md` step 4). The `surface-guard` job passes the flag, where an empty
    archive means the archived code was deleted rather than restored.
    """
    root = Path(root or ROOT)
    manifest = load_manifest(root / "archive" / "MANIFEST.json")
    entries = manifest["entries"]
    problems: list[str] = []
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
    problems += surface_violations(entries, live_surface(root))
    problems += package_violations(root)
    problems += index_violations(entries, root)
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
    """

    def holds(candidate: str) -> bool:
        for path, segment, node_key in blocks:
            blob = _git_blob(root, candidate, path)
            if blob is None:
                return False
            expected = expected_block(kind, symbol, path, blob, node_key)
            if expected is None or expected.rstrip() != segment.strip("\n"):
                return False
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
    for candidate in candidates:
        if not all(_git_blob(root, candidate, path) is not None for path in paths):
            continue
        held_a_path = True
        if holds is None or holds(candidate):
            return candidate, ref
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
    path = root / rel
    if not path.exists():
        raise ArchiveRefused(f"module {symbol} has no file at {rel} — nothing to archive")
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ArchiveRefused(f"could not read {rel}: {exc}") from exc
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
    if not batch or not reason:
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
            problems = check(require_entries=args.require_entries)
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
            print(f"OK — archive consistent: {len(entries)} entries ({counts}).")
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
