"""(#11609) The surface archive must be consistent, and its detectors must FIRE.

An archive is only a guarantee if a gate can fail on it. Two properties are pinned here and
they are different questions:

1. **the real archive is consistent** — `tools/archive_surface.py check` is clean on the
   tree as committed (manifest ↔ files ↔ index ↔ live surface);
2. **each detector is discriminating** — a synthetic surface that carries an archived
   symbol is REPORTED. A gate nobody has seen fire is indistinguishable from one that
   cannot fire, so every violation class this module reports has a positive control, AND the
   ones `check()` reaches through an integration point (the anchor arm, the surface arms) are
   driven THROUGH that point — a unit test of a helper leaves the call site deletable, which
   was measured in review (removing the anchor call from `file_violations` left the suite
   green).

The properties being protected are the brief's own (`archive/README.md`): an archived
symbol is not importable from the product package, is not served, and a test fails if it
reappears on the live surface — the archive must not be a slow leak back.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from tools import archive_surface as arch

ROOT = Path(__file__).resolve().parent.parent


# ── fixtures ────────────────────────────────────────────────────────────────────

def _surface(tools=(), methods=(), forward=(), sdk_retired=(), modules=()) -> dict:
    return {
        "tools": set(tools),
        "methods": set(methods),
        "forward": set(forward),
        "sdk_retired": set(sdk_retired),
        "modules": set(modules),
    }


def _entry(kind: str, symbol: str, *, batch: str = "batch-a", file: str | None = None) -> dict:
    return {
        "kind": kind,
        "symbol": symbol,
        "batch": batch,
        "reason": "no per-tool destination (#11609)",
        "file": file or f"archive/{arch.SUBDIR[kind]}/{batch}/{symbol}.py",
        "archived_from": arch.SOURCE_OF[kind],
        "archived_at": "a" * 40,
    }


def _commit(root: Path, *paths: str) -> None:
    """Commit sources, so HEAD holds what the writer is about to freeze.

    `archive_symbol` refuses to freeze source with uncommitted changes (the anchor is the
    commit, the body comes from the working tree, so a dirty file would archive something
    the recorded commit does not hold) — a fixture that never commits could not archive.
    """
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=r@example.com", "-c", "user.name=reviewer",
         "commit", "-q", "-m", "sources"],
        cwd=root,
        check=True,
    )


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A minimal repo root: a registry, an SDK module, pyproject, an EMPTY git repo, archive/."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=r@example.com", "-c", "user.name=reviewer",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "tool_registry.py").write_text(
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class ToolDefinition:\n"
        "    name: str\n"
        "\n"
        "TOOL_REGISTRY = [\n"
        "    ToolDefinition(\n"
        "        name='tortoise_search',\n"
        "    ),\n"
        "]\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "mcp_server.py").write_text(
        # The two handler shapes the repo actually carries: one NAMED after the tool
        # (`def tortoise_search`), one a shared helper that mentions it as a string.
        "def tortoise_search(kind=None, limit=10):\n"
        "    return {'kind': kind, 'limit': limit}\n"
        "\n"
        "def _arguments_say_preview(args):\n"
        "    return args\n"
        "\n"
        "async def _tool_other(args):\n"
        "    return args\n",
        encoding="utf-8",
    )
    (tmp_path / "tortoise" / "sdk.py").write_text(
        "def _declares_apply_first_write_lane(name):\n"
        "    return lambda fn: fn\n"
        "\n"
        "\n"
        "class TortoiseSDK:\n"
        "    def list_namespaces(self, limit: int = 10) -> list[str]:\n"
        "        return []\n"
        "\n"
        "    @_declares_apply_first_write_lane('get_entity')\n"
        "    def get_entity(self, entity_id: str):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\ninclude = ["tortoise*", "benchmarks*"]\n',
        encoding="utf-8",
    )
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "MANIFEST.json").write_text(
        json.dumps({"schema": arch.SCHEMA, "entries": []}) + "\n", encoding="utf-8"
    )
    _commit(tmp_path, ".")
    return tmp_path


# ── 1. the real archive ────────────────────────────────────────────────────────

def test_the_committed_archive_is_consistent() -> None:
    problems = arch.check(ROOT)
    assert problems == [], "tools/archive_surface.py check is red:\n" + "\n".join(problems)


def test_the_committed_index_is_current() -> None:
    entries = arch.load_manifest(ROOT / "archive" / "MANIFEST.json")["entries"]
    assert arch.index_violations(entries, ROOT) == []


# ── 2. the detectors fire (each violation class, with a positive control) ─────

def test_an_archived_mcp_tool_back_in_the_registry_is_reported() -> None:
    problems = arch.surface_violations(
        [_entry(arch.MCP_TOOL, "tortoise_search")],
        _surface(tools={"tortoise_search"}),
    )
    assert len(problems) == 1
    assert "REAPPEARED on the live MCP surface" in problems[0]


def test_an_archived_tool_that_is_also_a_forward_entry_is_reported() -> None:
    problems = arch.surface_violations(
        [_entry(arch.MCP_TOOL, "tortoise_search")],
        _surface(forward={"tortoise_search"}),
    )
    assert len(problems) == 1
    assert "RETIRED_USE_INSTEAD forward entry" in problems[0]


def test_an_archived_sdk_method_back_on_the_class_is_reported() -> None:
    problems = arch.surface_violations(
        [_entry(arch.SDK_METHOD, "list_namespaces")],
        _surface(methods={"list_namespaces"}, sdk_retired={"list_namespaces"}),
    )
    assert len(problems) == 1
    assert "REAPPEARED as a public TortoiseSDK method" in problems[0]


def test_an_archived_sdk_method_missing_from_the_retirement_map_is_reported() -> None:
    problems = arch.surface_violations(
        [_entry(arch.SDK_METHOD, "list_namespaces")],
        _surface(sdk_retired=set()),
    )
    assert len(problems) == 1
    assert "absent from RETIRED_SDK_METHODS" in problems[0]


def test_a_consistent_archive_is_not_reported() -> None:
    """The negative control: the detectors must not fire on a clean archive."""
    assert (
        arch.surface_violations(
            [
                _entry(arch.MCP_TOOL, "tortoise_search"),
                _entry(arch.SDK_METHOD, "list_namespaces"),
            ],
            _surface(sdk_retired={"list_namespaces"}),
        )
        == []
    )


def test_an_unknown_kind_is_reported() -> None:
    problems = arch.surface_violations([{**_entry(arch.MCP_TOOL, "x"), "kind": "rest-route"}], _surface())
    assert any("unknown kind" in problem for problem in problems)


# ── 3. the writer, and the file/manifest agreement it produces ────────────────

def test_archiving_an_mcp_tool_freezes_its_definition_and_handler(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "test reason", root=tree
    )

    assert entry["file"] == "archive/mcp-tools/batch-a/tortoise_search.py"
    text = (tree / entry["file"]).read_text(encoding="utf-8")
    # the verbatim definition AND the handler — what the tool DID
    assert "name='tortoise_search'" in text
    # ⛔ The handler in this repo is NAMED after the tool. Matching only functions whose
    # body mentions the tool as a string captured 8 of 72 on the real tree and never the
    # name-matching one (measured) — so the name arm is pinned here, on its own.
    assert "def tortoise_search(kind=None, limit=10)" in text
    assert "def _arguments_say_preview" not in text, (
        "a handler that neither is named after the tool nor mentions it must not be swept in"
    )
    # metadata the gate reads, and the restore recipe
    assert "ARCHIVED_KIND = 'mcp-tool'" in text
    assert "ARCHIVED_SYMBOL = 'tortoise_search'" in text
    assert "IS the source of record" in text
    assert arch.VERBATIM_BANNER in text

    manifest = json.loads((tree / "archive" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert [e["symbol"] for e in manifest["entries"]] == ["tortoise_search"]
    assert (tree / "archive" / "INDEX.md").exists()
    assert arch.file_violations([entry], tree / "archive", tree) == []


def test_a_shared_helper_that_names_the_tool_is_still_captured(tree: Path) -> None:
    """The second handler shape: a dispatch helper whose body names the tool."""
    (tree / "tortoise" / "mcp_server.py").write_text(
        "async def _dispatch(name, args):\n"
        "    if name == 'tortoise_search':\n"
        "        return args\n"
        "    return None\n",
        encoding="utf-8",
    )
    _commit(tree, "tortoise/mcp_server.py")
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    text = (tree / entry["file"]).read_text(encoding="utf-8")
    assert "async def _dispatch(name, args)" in text


def test_archiving_an_sdk_method_freezes_the_method_body(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.SDK_METHOD, "list_namespaces", "batch-a", "test reason", root=tree
    )
    text = (tree / entry["file"]).read_text(encoding="utf-8")
    assert "def list_namespaces(self, limit: int = 10)" in text
    assert "def get_entity" not in text
    assert arch.file_violations([entry], tree / "archive", tree) == []


def test_a_frozen_method_keeps_its_decorator_and_its_indentation(tree: Path) -> None:
    """The block must be the source that WAS there — decorators included, indentation kept.

    `ast.get_source_segment` starts at the `def` line, so a decorated method lost its decorator
    (a load-bearing `@_declares_apply_first_write_lane(...)` declaration) and its first line was
    dedented — which, copied back into the class, would have ended the class body. Computing the
    digest over the same truncated text meant no arm of the gate could see it (review cycle 4,
    P0). This pins both halves against the source file.
    """
    entry = arch.archive_symbol(
        arch.SDK_METHOD, "get_entity", "batch-a", "r", root=tree
    )
    frozen = (tree / entry["file"]).read_text(encoding="utf-8")
    assert "    @_declares_apply_first_write_lane('get_entity')" in frozen, (
        "the decorator is part of the source that was removed"
    )
    assert "\n    def get_entity(self, entity_id: str):" in frozen, (
        "the block keeps its original indentation, so copying it back lands inside the class"
    )
    # …and the archived file still parses: only the HEADER has to be a valid module, because the
    # blocks are verbatim source in their original indentation (a class method keeps its 4 spaces).
    assert arch.file_violations([entry], tree / "archive", tree) == []


def test_the_writer_refuses_a_symbol_that_is_not_on_the_surface(tree: Path) -> None:
    with pytest.raises(arch.ArchiveRefused):
        arch.archive_symbol(arch.MCP_TOOL, "tortoise_nope", "batch-a", "r", root=tree)
    with pytest.raises(arch.ArchiveRefused):
        arch.archive_symbol(arch.SDK_METHOD, "nope", "batch-a", "r", root=tree)


def test_the_writer_refuses_a_symbol_that_is_already_archived(tree: Path) -> None:
    arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    with pytest.raises(arch.ArchiveRefused):
        arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-b", "r", root=tree)


def test_editing_an_archived_module_away_from_its_manifest_row_is_reported(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    path = tree / entry["file"]
    path.write_text(
        path.read_text(encoding="utf-8").replace("ARCHIVED_BATCH = 'batch-a'", "ARCHIVED_BATCH = 'x'"),
        encoding="utf-8",
    )
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("drifted" in problem for problem in problems)


def test_archived_code_with_no_manifest_row_is_reported(tree: Path) -> None:
    orphan = tree / "archive" / "mcp-tools" / "batch-a" / "orphan.py"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("x = 1\n", encoding="utf-8")
    problems = arch.file_violations([], tree / "archive", tree)
    assert any("orphan" in problem for problem in problems)


def test_an_init_py_in_the_archive_is_reported(tree: Path) -> None:
    (tree / "archive" / "__init__.py").write_text("", encoding="utf-8")
    problems = arch.file_violations([], tree / "archive", tree)
    assert any("no `__init__.py`" in problem for problem in problems)


def test_a_stale_index_is_reported(tree: Path) -> None:
    arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    (tree / "archive" / "INDEX.md").write_text("stale\n", encoding="utf-8")
    problems = arch.index_violations(
        arch.load_manifest(tree / "archive" / "MANIFEST.json")["entries"], tree
    )
    assert any("STALE" in problem for problem in problems)


# ── 4. the archive is not part of the product package ─────────────────────────

def test_the_archive_is_not_importable_from_the_product_package() -> None:
    assert not (ROOT / "tortoise" / "archive").exists(), (
        "code under tortoise/ is importable as tortoise.<mod> — archived code must live "
        "outside the shipped package"
    )
    assert importlib.util.find_spec("tortoise.archive") is None
    assert not list((ROOT / "archive").rglob("__init__.py")), (
        "archive/ carries no __init__.py: it is a readable source tree, not a package"
    )


def test_the_archive_is_not_packaged() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = (
        pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    )
    assert not any(pattern.startswith("archive") for pattern in include), (
        f"packages.find.include={include} must not match the archive — an archived module "
        "would ship in the wheel and be importable after install"
    )
    assert arch.package_violations(ROOT) == []


def test_the_archive_readme_states_the_surface_rule() -> None:
    """The mechanism is only usable if a reader is told what 'archived' MEANS."""
    readme = (ROOT / "archive" / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "OUT of the product",
        "not importable from the product package",
        "RETIRED_USE_INSTEAD",
        "Restoring the code is not permission to re-expose it",
    ):
        assert phrase in readme, f"archive/README.md must state: {phrase}"


# ── 5. the writer's refusals (each one a way an archive goes silent) ───────────

def test_a_batch_slug_cannot_escape_the_archive(tree: Path) -> None:
    """`--batch` becomes a DIRECTORY name: `../../tortoise` writes into the package."""
    for batch in ("../../tortoise", "a/b", "..", ".", "Batch", ""):
        with pytest.raises(arch.ArchiveRefused):
            arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", batch, "r", root=tree)
    assert not (tree / "tortoise" / "tortoise_search.py").exists()
    assert not any((tree / "archive").rglob("*.py")), "nothing may be written by a refusal"


def test_a_multi_line_reason_is_refused(tree: Path) -> None:
    """The reason is interpolated into a `#` header and a markdown table row."""
    with pytest.raises(arch.ArchiveRefused):
        arch.archive_symbol(
            arch.MCP_TOOL, "tortoise_search", "batch-a", 'r\nimport os; os.system("id")', root=tree
        )
    assert not any((tree / "archive").rglob("*.py"))


def test_the_writer_refuses_outside_a_git_checkout(tmp_path: Path) -> None:
    """No commit sha means no restore anchor — refuse, never record a placeholder."""
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text(
        "class TortoiseSDK:\n    def m(self):\n        return 1\n", encoding="utf-8"
    )
    with pytest.raises(arch.ArchiveRefused):
        arch.archive_symbol(arch.SDK_METHOD, "m", "batch-a", "r", root=tmp_path)


def test_a_module_archived_from_the_package_freezes_the_whole_file(tree: Path) -> None:
    (tree / "tortoise" / "archived").mkdir()
    (tree / "tortoise" / "archived" / "legacy.py").write_text(
        '"""ARCHIVED: old thing."""\n\n\ndef old_search(q):\n    return [q]\n', encoding="utf-8"
    )
    _commit(tree, "tortoise/archived/legacy.py")
    entry = arch.archive_symbol(
        arch.MODULE, "tortoise.archived.legacy", "batch-a", "moved out of the package", root=tree
    )
    assert entry["archived_from"] == "tortoise/archived/legacy.py"
    text = (tree / entry["file"]).read_text(encoding="utf-8")
    assert "def old_search(q)" in text
    assert arch.file_violations([entry], tree / "archive", tree) == []


# ── 6. the archive's own failure modes ────────────────────────────────────────

def test_a_module_still_importable_from_the_package_is_reported() -> None:
    """A module archived from inside `tortoise/` is importable AND ships in the wheel."""
    problems = arch.surface_violations(
        [_entry(arch.MODULE, "tortoise.archived.legacy")],
        _surface(modules={"tortoise/archived/legacy.py"}),
    )
    assert len(problems) == 1
    assert "REAPPEARED as an importable module" in problems[0]

    clean = arch.surface_violations(
        [_entry(arch.MODULE, "tortoise.archived.legacy")], _surface()
    )
    assert clean == []


def test_metadata_without_the_frozen_source_is_reported(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    path = tree / entry["file"]
    header = path.read_text(encoding="utf-8").split(arch.VERBATIM_BANNER)[0]
    path.write_text(header, encoding="utf-8")
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("frozen" in problem for problem in problems)


def test_a_placeholder_restore_anchor_is_reported(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    entry["archived_at"] = "unknown"
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("not a full commit sha" in problem for problem in problems)

    # A well-formed all-zero sha is the other placeholder shape: it is not a commit either.
    entry["archived_at"] = "0" * 40
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("all-zero placeholder" in problem for problem in problems)


# ── 7. the frozen body is verified by DIGEST, not by the banner ───────────────

def test_an_emptied_frozen_block_is_reported(tree: Path) -> None:
    """A banner with nothing under it is metadata pretending to be an archive."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    path = tree / entry["file"]
    kept = path.read_text(encoding="utf-8").split(arch.VERBATIM_BANNER)[0]
    path.write_text(kept + f"{arch.VERBATIM_BANNER}tortoise/tool_registry.py ──\n\n",
                    encoding="utf-8")
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("EMPTY frozen block" in problem for problem in problems)


def test_an_edited_frozen_block_is_reported(tree: Path) -> None:
    """The digest is what makes 'restoring it restores what was removed' checkable."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    path = tree / entry["file"]
    path.write_text(
        path.read_text(encoding="utf-8").replace("limit=10", "limit=99"), encoding="utf-8"
    )
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("does not match the manifest's sha256" in problem for problem in problems)


def test_a_manifest_row_with_no_block_digests_is_reported(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    entry.pop("blocks")
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("records none" in problem for problem in problems)


def test_a_block_count_mismatch_is_reported(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    entry["blocks"] = entry["blocks"][:1]
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("the index cannot say what this file holds" in problem for problem in problems)


def test_the_writer_refuses_to_freeze_a_dirty_source(tree: Path) -> None:
    """The anchor is the commit; the body is read from the working tree. They must agree."""
    (tree / "tortoise" / "tool_registry.py").write_text(
        (tree / "tortoise" / "tool_registry.py").read_text(encoding="utf-8") + "# edit\n",
        encoding="utf-8",
    )
    with pytest.raises(arch.ArchiveRefused, match="uncommitted changes"):
        arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)


# ── 8. remaining manifest/file refusals ──────────────────────────────────────

def test_a_manifest_row_naming_a_missing_file_is_reported(tree: Path) -> None:
    entry = _entry(arch.MCP_TOOL, "tortoise_search")
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("which does not exist" in problem for problem in problems)


def test_a_manifest_row_with_no_reason_is_reported(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    entry["reason"] = ""
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("no `reason`" in problem for problem in problems)


def test_a_duplicate_symbol_in_the_manifest_is_reported(tree: Path) -> None:
    arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    duplicated = arch.load_manifest(tree / "archive" / "MANIFEST.json")
    duplicated["entries"] = duplicated["entries"] * 2
    problems = arch.manifest_violations(duplicated, tree / "archive")
    assert any("indexed twice" in problem for problem in problems)


def test_a_manifest_at_another_schema_version_is_reported(tree: Path) -> None:
    problems = arch.manifest_violations({"schema": 0, "entries": []}, tree / "archive")
    assert any("schema" in problem for problem in problems)


def test_a_missing_manifest_is_reported(tree: Path) -> None:
    (tree / "archive" / "MANIFEST.json").unlink()
    problems = arch.manifest_violations(arch.load_manifest(tree / "archive" / "MANIFEST.json"),
                                       tree / "archive")
    assert any("no `archive/MANIFEST.json`" in problem for problem in problems)


def test_a_missing_index_is_reported(tree: Path) -> None:
    problems = arch.index_violations([], tree)
    assert any("missing" in problem for problem in problems)


def test_a_manifest_file_outside_the_archive_is_reported(tree: Path) -> None:
    """Even a hand-edited manifest cannot point archived code into the package."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    entry["file"] = "tortoise/tortoise_search.py"
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("not a path inside archive/" in problem for problem in problems)


def test_an_in_package_archive_directory_is_reported(tree: Path) -> None:
    """`tortoise/archive` is importable as `tortoise.archive` and ships in the wheel."""
    (tree / "tortoise" / "archive").mkdir()
    problems = arch.package_violations(tree)
    assert any("tortoise/archive exists" in problem for problem in problems)


def test_a_package_find_pattern_that_matches_the_archive_is_reported(tree: Path) -> None:
    (tree / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\ninclude = ["tortoise*", "archive*"]\n',
        encoding="utf-8",
    )
    problems = arch.package_violations(tree)
    assert any("matches the archive" in problem for problem in problems)
    (tree / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\ninclude = ["*"]\n', encoding="utf-8"
    )
    assert any(
        "matches the archive" in problem for problem in arch.package_violations(tree)
    )


def test_a_tortoise_archive_stub_module_is_reported(tree: Path) -> None:
    """A `.pyi` stub under `tortoise/` is importable surface too (the `.py`-only scan missed it)."""
    (tree / "tortoise" / "archived").mkdir()
    (tree / "tortoise" / "archived" / "legacy.pyi").write_text("def old(q): ...\n", encoding="utf-8")
    problems = arch.surface_violations(
        [_entry(arch.MODULE, "tortoise.archived.legacy")],
        _surface(modules={"tortoise/archived/legacy.pyi"}),
    )
    assert any("REAPPEARED as an importable module" in problem for problem in problems)


def test_the_live_module_scan_sees_pyi_stubs() -> None:
    surf = arch.live_surface(arch.ROOT)
    assert isinstance(surf["modules"], set)
    assert all(p.endswith((".py", ".pyi")) for p in surf["modules"]), (
        "a `.py`-only scan cannot see a stub, so the `module` detector's `.pyi` candidate is "
        "unreachable by construction"
    )


def test_ci_proves_the_anchors_on_a_full_history_checkout() -> None:
    """The strict verb must be INVOKED by CI, or an un-fetched anchor is green forever.

    Measured in review cycle 4: nothing in the repo invoked `--anchors`, so the depth-1 `test`
    job reported UNPROVEN and proved nothing. A gate that runs only where it cannot answer is a
    no-op gate — deleting this step must red a test, not read as a cleanup.
    """
    workflow = (arch.ROOT / ".github" / "workflows" / "python-ci.yml").read_text(encoding="utf-8")
    assert "tools/archive_surface.py verify-anchors" in workflow, (
        "no workflow runs the strict anchor proof"
    )
    index = workflow.index("verify-anchors")
    job = workflow.rfind("\n  surface-guard:", 0, index)
    assert job != -1, "the strict anchor step must live in a job that checks out full history"
    assert "fetch-depth: 0" in workflow[job:index], (
        "the job running verify-anchors must check out at fetch-depth: 0, or the anchor is "
        "UNPROVEN by construction and the step proves nothing"
    )


# ── 9. the restore anchor is a CITATION, not a number ────────────────────────

def test_a_block_the_anchored_commit_does_not_hold_is_reported(tree: Path) -> None:
    """The digests prove file↔manifest; only the commit can prove what was removed."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    path, body = arch.frozen_blocks((tree / entry["file"]).read_text(encoding="utf-8"))[0]
    head = arch._head_sha(tree)
    # A commit that IS present here but never held this path: the repository's ROOT commit (the
    # fixture's `--allow-empty` init) is in the object store and contains no files at all.
    later = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=tree, check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    assert later != head
    assert arch._anchor_violations(tree, "tortoise_search", entry["file"], entry, path, body) == []
    problems = arch._anchor_violations(
        tree, "tortoise_search", entry["file"], dict(entry, archived_at=later), path, body
    )
    assert len(problems) == 1
    assert "does NOT hold" in problems[0]


def test_an_unreadable_anchor_is_UNPROVEN_not_silently_green(tree: Path) -> None:
    """A clone that cannot answer must say so — the strict verb's whole purpose."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    entry["archived_at"] = "e5a5611f4" + "0" * 31
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    assert arch.anchor_unverified(tree), "an anchor this clone cannot read must be reported"
    # ⛔ NOT `assert anchor_unverified(ROOT) == []`: `actions/checkout` defaults to fetch-depth 1,
    # and the anchor is an ANCESTOR of the head, so on CI the object is legitimately absent. The
    # real archive must be quiet only where the commit is actually readable (finding from review
    # cycle 4: the unconditional form reds the required `test` job on a correct archive).
    real = arch.load_manifest(arch.ROOT / "archive" / "MANIFEST.json")["entries"]
    if all(arch._git_has_commit(arch.ROOT, str(e.get("archived_at") or "")) for e in real):
        assert arch.anchor_unverified(arch.ROOT) == [], (
            "every anchor IS readable here, so the strict verb must be quiet"
        )


def test_a_re_anchored_entry_is_caught_by_check_not_just_by_the_helper(tree: Path) -> None:
    """The arm must be exercised through `file_violations` — the only place `check` calls it.

    Measured in review cycle 4: deleting the call site left the whole suite green, because the
    only test that covered the arm called `_anchor_violations` directly.
    """
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    root_commit = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=tree, check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    path, body = arch.frozen_blocks((tree / entry["file"]).read_text(encoding="utf-8"))[0]
    # Re-render the file AND the manifest under the wrong anchor, so every other arm agrees:
    # this is what a repair or a re-cut looks like, and only the anchor arm can see it.
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        entry["archived_from"], root_commit, [(path, body)],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    forged = dict(entry, archived_at=root_commit, blocks=frozen)
    problems = arch.file_violations([forged], tree / "archive", tree)
    assert any("NOT hold" in problem for problem in problems), problems


def test_a_manifest_row_with_no_file_is_reported(tree: Path) -> None:
    entry = _entry(arch.MCP_TOOL, "tortoise_search")
    entry.pop("file")
    problems = arch.file_violations([entry], tree / "archive", tree)
    assert any("carries no `file`" in problem for problem in problems)


def test_two_module_symbols_cannot_share_one_archived_file(tree: Path) -> None:
    """A basename-only path made the second write destroy the first frozen body (exited 0)."""
    (tree / "tortoise" / "a").mkdir()
    (tree / "tortoise" / "b").mkdir()
    (tree / "tortoise" / "a" / "foo.py").write_text("ALPHA = 1\n", encoding="utf-8")
    (tree / "tortoise" / "b" / "foo.py").write_text("BETA = 2\n", encoding="utf-8")
    _commit(tree, "tortoise/a/foo.py", "tortoise/b/foo.py")
    first = arch.archive_symbol(arch.MODULE, "tortoise.a.foo", "b1", "r", root=tree)
    second = arch.archive_symbol(arch.MODULE, "tortoise.b.foo", "b1", "r", root=tree)
    assert first["file"] != second["file"]
    assert "ALPHA = 1" in (tree / first["file"]).read_text(encoding="utf-8")
    assert "BETA = 2" in (tree / second["file"]).read_text(encoding="utf-8")


def test_the_writer_refuses_to_overwrite_an_archived_file(tree: Path) -> None:
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    target = tree / entry["file"]
    # Same path, a row the manifest does not know about: the file is somebody's frozen body.
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    with pytest.raises(arch.ArchiveRefused, match="refusing to overwrite"):
        arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    assert target.exists()


def test_a_symbol_segment_that_is_not_an_identifier_is_refused(tree: Path) -> None:
    for symbol in ("tortoise..foo", "tortoise.-x", "tortoise.1foo"):
        with pytest.raises(arch.ArchiveRefused):
            arch.archive_symbol(arch.MODULE, symbol, "b1", "r", root=tree)


def test_a_pre_mechanism_in_package_archive_location_is_reported(tree: Path) -> None:
    """`tortoise/archived/` is the pre-mechanism location and it ships — refuse it."""
    (tree / "tortoise" / "archived").mkdir()
    problems = arch.package_violations(tree)
    assert any("tortoise/archived exists" in problem for problem in problems)
