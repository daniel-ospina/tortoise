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

import ast
import builtins
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tools import archive_surface as arch

ROOT = Path(__file__).resolve().parent.parent


# ── fixtures ────────────────────────────────────────────────────────────────────

def _surface(
    tools=(), methods=(), forward=(), sdk_retired=(), modules=(), modules_with_a_body=None
) -> dict:
    surface = {
        "tools": set(tools),
        "methods": set(methods),
        "forward": set(forward),
        "sdk_retired": set(sdk_retired),
        "modules": set(modules),
    }
    if modules_with_a_body is not None:
        surface["modules_with_a_body"] = set(modules_with_a_body)
    return surface


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
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
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


def test_the_committed_rows_cite_the_commit_the_writer_would_record() -> None:
    """The real manifest, not a fixture: the row is what today's writer produces.

    The committed row cited a commit that merely HELD the file (main's HEAD at archive time), so
    the README's "the commit on the default branch that last held it" was not what it said and
    nothing noticed — the writer↔row equality was only ever asserted on a tmp tree (review
    cycle 5). Guards on the ref being resolvable, because a depth-1 CI clone legitimately cannot
    answer and must not be turned into a false red.
    """
    ref = arch.default_branch_ref(arch.ROOT)
    entries = arch.load_manifest(arch.ROOT / "archive" / "MANIFEST.json")["entries"]
    # ⛔ THREE-VALUED, like every other anchor arm: `actions/checkout` defaults to
    # `fetch-depth: 1`, and on the post-merge push the archived path is DELETED at the tip — so
    # `rev-list <ref> -- <path>` is empty and a correct archive would raise here (measured in
    # review cycle 7). An absence this clone cannot interpret is a SKIP, never an assertion:
    # `verify-anchors` runs in the depth-0 job and is where the proof belongs.
    if ref is None or not all(
        arch._git_has_commit(arch.ROOT, str(e.get("archived_at") or "")) for e in entries
    ):
        pytest.skip("this clone cannot resolve the anchored commits (shallow) — see verify-anchors")
    for entry in entries:
        # The predicate is the GATE's block comparison, threaded the same way the writer threads
        # it: "the anchor holds the code", not merely the path. Without it this row-equality test
        # tells a maintainer to re-anchor onto a commit that does not hold the block — which the
        # gate then rejects (measured, round 8: the source FILE survives an `mcp-tool`/`sdk-method`
        # removal, so the merge commit is a candidate).
        body = (arch.ROOT / entry["file"]).read_text(encoding="utf-8")
        blocks = [
            (path, segment, row.get("node"))
            for (path, segment), row in zip(
                arch.frozen_blocks(body), entry["blocks"], strict=True
            )
        ]
        # ⛔ The GATE's question, not the writer's newest-commit rule: `_anchor_violations`
        # accepts ANY commit that holds the blocks, while `anchor_sha` returns the NEWEST such
        # commit — so a commit landing on main that touches the file without touching this block
        # makes a strictly-newer answer "correct", and asserting equality called a still-valid
        # row drift, with a remedy that refuses to run (`already archived`) and no procedure to
        # follow (measured, review round 9).
        assert arch.block_holds(
            arch.ROOT, entry["kind"], entry["symbol"], blocks
        )(entry["archived_at"]), (
            f"{entry['symbol']} cites {entry['archived_at']}, which does not hold its frozen "
            "block — the citation does not describe the code"
        )


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


def test_the_writer_refuses_to_freeze_an_EDITED_block(tree: Path) -> None:
    """The block is the body that gets archived, so THAT is what must match the anchor."""
    path = tree / "tortoise" / "tool_registry.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "name='tortoise_search',", "name='tortoise_search',\n        id='surface.x',"
        ),
        encoding="utf-8",
    )
    with pytest.raises(arch.ArchiveRefused, match="differs from"):
        arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)


def test_an_unrelated_edit_elsewhere_in_the_file_does_not_block_the_archive(tree: Path) -> None:
    """The normal batch flow: the lane's file is its merge-base state, main moved on.

    Comparing the WHOLE file refused this with "land the source on the default branch first",
    describing an edit the lane had not made — every archive from a lane that had not rebased
    (measured in review cycle 6). The block is identical, so the body is exactly what the
    citation says and there is nothing to refuse.
    """
    path = tree / "tortoise" / "tool_registry.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n# a comment landed by another lane\n",
        encoding="utf-8",
    )
    entry = arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    assert arch.file_violations([entry], tree / "archive", tree) == []


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


def test_the_package_module_scan_sees_pyi_stubs(tree: Path) -> None:
    """The PRODUCER, on a tree that actually carries a stub.

    The previous form asserted over the real repo's modules, which are all `.py` (`rg --files -g
    '*.pyi'` → 0), so a `*.py`-only scan satisfied it and reverting the scan left every test
    green (review cycle 6). A `.pyi` file has to exist for the arm to be exercised.
    """
    (tree / "tortoise" / "archived").mkdir()
    (tree / "tortoise" / "archived" / "legacy.pyi").write_text("def old(q): ...\n", encoding="utf-8")
    (tree / "tortoise" / "plain.py").write_text("X = 1\n", encoding="utf-8")
    modules = arch._package_modules(tree)
    assert "tortoise/archived/legacy.pyi" in modules, (
        "a `.pyi` stub under the shipped package is importable surface, so the scan must see it"
    )
    assert "tortoise/plain.py" in modules
    assert arch.live_surface(arch.ROOT)["modules"] == arch._package_modules(arch.ROOT)


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
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
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


def _clone_can_answer_ancestry(root: Path) -> bool:
    """Can this clone walk history to a default-branch ref at all?

    ⛔ A SHALLOW CHECKOUT CANNOT, and `_git_has_commit` does NOT reveal it. Measured on
    this repo's own self-hosted runner (`pi-runner-c5-x64-4`, `actions/checkout` at
    `fetch-depth: 1`, checked out at `refs/remotes/pull/13728/merge`): the object store
    still held ANCIENT anchored commits from an earlier job on the same box, so all 45
    entries were `cat-file -e` READABLE, while `merge-base --is-ancestor <anchor>
    origin/main` answered non-zero for every one of them — a shallow graft is a history
    boundary, so nothing older than it is reachable. The strict verb's answer there is
    UNKNOWN, not UNPROVEN, and demanding a quiet answer is demanding a falsehood (the
    same class as the `git diff` arm repaired at `5f935f3b69`).

    So the test asserts the real archive is quiet only where the clone can actually
    answer the question. A full clone still reds on a genuinely orphaned anchor.
    """
    code, out = arch._git(root, "rev-parse", "--is-shallow-repository")
    if code != 0:
        return False        # cannot even ask: assert nothing rather than something false
    return out.strip() != "true"


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
    # real archive must be quiet only where the clip can actually answer — the commit readable
    # AND the ancestry walkable (see `_clone_can_answer_ancestry`: a shallow graft makes every
    # anchor look orphaned while `cat-file -e` still finds the object; measured, 45/45, on the
    # required `test (b)` shard at `decb96fe8a`).
    real = arch.load_manifest(arch.ROOT / "archive" / "MANIFEST.json")["entries"]
    if _clone_can_answer_ancestry(arch.ROOT) and all(
            arch._git_has_commit(arch.ROOT, str(e.get("archived_at") or "")) for e in real):
        assert arch.anchor_unverified(arch.ROOT) == [], (
            "every anchor IS readable here and this clone can walk the ancestry, so the "
            "strict verb must be quiet"
        )


def test_the_shallow_clone_discriminator_answers_both_ways(tmp_path: Path) -> None:
    """The skip above is only honest if it can TELL a shallow clone from a full one.

    NON-VACUITY: a discriminator stuck on `True` would re-red the required `test` job on
    every shallow checkout (the measured failure), and one stuck on `False` would silently
    retire this arm on every box. Both directions are asserted against real clones of a
    two-commit repo, so neither polarity can pass by accident.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
    for n in (1, 2):
        (origin / f"f{n}.txt").write_text(f"{n}\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", f"f{n}.txt"], cwd=origin, check=True)
        subprocess.run(
            ["git", "-c", "user.email=r@example.com", "-c", "user.name=reviewer",
             "commit", "-q", "-m", f"c{n}"],
            cwd=origin,
            check=True,
        )

    full = tmp_path / "full"
    shallow = tmp_path / "shallow"
    # `file://` on purpose: `git clone --depth` ignores the flag for a plain local path.
    subprocess.run(["git", "clone", "-q", f"file://{origin}", str(full)], check=True)
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(shallow)],
        check=True,
    )

    assert _clone_can_answer_ancestry(full) is True
    assert _clone_can_answer_ancestry(shallow) is False

    # ⛔ THE MEASURED CI SHAPE, REPRODUCED: an object present in a shallow clone. The old
    # gate asked only "is the commit readable", so it answered YES here and then demanded
    # `anchor_unverified == []` — a falsehood, because the ancestry walk cannot cross the
    # graft. `git fetch <sha>` puts the object in the store without unshallowing.
    first = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=origin, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[0]
    subprocess.run(
        ["git", "fetch", "-q", "--depth", "1", f"file://{origin}", first],
        cwd=shallow, check=True,
    )
    assert arch._git_has_commit(shallow, first) is True
    manifest = {"entries": [{
        "kind": arch.MCP_TOOL, "symbol": "tortoise_search", "batch": "batch-a",
        "reason": "fixture", "file": "archive/mcp_tools/batch-a/tortoise_search.py",
        "archived_from": arch.SOURCE_OF[arch.MCP_TOOL], "archived_at": first,
        "blocks": [],
    }]}
    arch.save_manifest(manifest, shallow / "archive" / "MANIFEST.json")
    reported = arch.anchor_unverified(shallow)
    assert reported and "is not an ancestor of" in reported[0], (
        "the shallow-clone red must reproduce as an ANCESTRY complaint, not a readability "
        f"one — got {reported!r}"
    )


def test_the_restore_anchor_is_a_commit_on_the_default_branch(tree: Path) -> None:
    """A batch PR's own HEAD never reaches `main` (this repo lands by squash)."""
    ref = arch.default_branch_ref(tree)
    assert ref is not None
    entry = arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    code, _ = arch._git(tree, "merge-base", "--is-ancestor", entry["archived_at"], ref)
    assert code == 0, (
        f"{entry['archived_at']} is not an ancestor of {ref} — after a squash merge the anchor "
        "would be unreachable and `verify-anchors` would red on every later run"
    )
    assert entry["archived_at"] == arch.anchor_sha(tree, ["tortoise/tool_registry.py"])[0]


def test_source_that_never_landed_on_the_default_branch_is_refused(tree: Path) -> None:
    """Code that never shipped has no released state to restore — delete it, do not archive."""
    (tree / "tortoise" / "branchonly.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=tree, check=True)
    _commit(tree, "tortoise/branchonly.py")
    with pytest.raises(arch.ArchiveRefused, match="never landed on the default branch"):
        arch.archive_symbol(arch.MODULE, "tortoise.branchonly", "b1", "r", root=tree)


def test_segment_is_byte_exact(tree: Path) -> None:
    """Writer and gate share `_segment`, so a substring assertion cannot see a bad slice.

    A one-line mutation (appending the next line) leaves every other assertion in this file
    passing, and `file_violations` re-runs the SAME function, so the blindness is mutual.
    """
    source = (
        "class C:\n"
        "    @dec(1)\n"
        "    @other\n"
        "    def f(self):\n"
        "        return 1\n"
        "\n"
        "    def g(self):\n"
        "        return 2\n"
    )
    tree_ast = ast.parse(source)
    method = tree_ast.body[0].body[0]
    assert arch._segment(source, method) == (
        "    @dec(1)\n    @other\n    def f(self):\n        return 1"
    ), "decorators included, indentation preserved, nothing from the next method"


def test_a_surface_violation_reaches_check_through_its_call_site(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check()` must aggregate the SURFACE arm, or that call site is deletable green."""
    arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    monkeypatch.setattr(
        arch, "live_surface", lambda root=None: _surface(tools={"tortoise_search"})
    )
    monkeypatch.setattr(arch, "package_violations", lambda root=None: [])
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])
    problems = arch.check(tree)
    assert any("REAPPEARED on the live MCP surface" in problem for problem in problems), (
        "an archived tool back in TOOL_REGISTRY must fail `check`, not only the helper"
    )


def test_an_empty_archive_is_reported(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vacuous PASS: with no entries every other arm is silent."""
    arch.write_index(tree)
    # `check` refuses a foreign tree by design (`live_surface(root)`), so the surface half is
    # stubbed here — an empty manifest is a defect regardless of what the surface says.
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    # Off by default, because the documented restore of the LAST entry legitimately ends at zero
    # (finding from review cycle 5: the arm made step 4 of the restore procedure unreachable).
    assert not any("EMPTY" in problem for problem in arch.check(tree))
    assert any("EMPTY" in problem for problem in arch.check(tree, require_entries=True))


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
        entry["archived_from"], root_commit, [(path, body, None)],
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


def test_a_node_key_that_names_no_node_is_reported(tree: Path) -> None:
    """A key that resolves to nothing must RED, not demote the arm to containment."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    path, body = arch.frozen_blocks((tree / entry["file"]).read_text(encoding="utf-8"))[0]
    problems = arch._anchor_violations(
        tree, "tortoise_search", entry["file"], entry, path, body, "FunctionDef:not_a_function"
    )
    assert any("does not exist in" in problem for problem in problems)


def test_an_anchor_that_is_not_on_the_default_branch_is_reported(tree: Path) -> None:
    """The durability the writer enforces must also be CHECKED, or a hand-edited row slips by."""
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    (tree / "tortoise" / "side.py").write_text("Y = 2\n", encoding="utf-8")
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tree, check=True)
    _commit(tree, "tortoise/side.py")
    branch_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=branch_sha)]},
        tree / "archive" / "MANIFEST.json",
    )
    problems = arch.anchor_unverified(tree)
    assert any("is not an ancestor of" in problem for problem in problems), problems


def test_verify_anchors_exits_non_zero_on_a_forged_entry(tree: Path) -> None:
    """The CI step's ONE line of proof: gutting the verb must red, not print OK.

    Nothing called `arch.main(...)` before this, so `problems = []` inside `verify-anchors`
    would still have satisfied the workflow assertion (review cycle 5).
    """
    entry = arch.archive_symbol(
        arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree
    )
    root_commit = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=tree, check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[0]
    path, body = arch.frozen_blocks((tree / entry["file"]).read_text(encoding="utf-8"))[0]
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        entry["archived_from"], root_commit, [(path, body, None)],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=root_commit, blocks=frozen)]},
        tree / "archive" / "MANIFEST.json",
    )
    monkey_monkey = arch.ROOT
    arch.ROOT = tree
    try:
        assert arch.main(["archive_surface.py", "verify-anchors"]) == 1
        # …and an empty manifest must not print OK over nothing.
        arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
        for f in (tree / "archive").rglob("*.py"):
            f.unlink()
        assert arch.main(["archive_surface.py", "verify-anchors"]) == 1
    finally:
        arch.ROOT = monkey_monkey


def test_a_packages_find_glob_that_matches_the_archive_is_reported(tree: Path) -> None:
    """`arch*` matches `archive`; a prefix test said otherwise (review cycle 5)."""
    (tree / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\ninclude = ["tortoise*", "arch*"]\n',
        encoding="utf-8",
    )
    assert any("matches the archive" in problem for problem in arch.package_violations(tree))


def test_the_anchor_is_the_newest_commit_that_HOLDS_the_path(tree: Path) -> None:
    """`rev-list -1 -- <path>` also returns the commit that DELETED it, and holds nothing.

    The batch PR deletes the source, so the squash merge touches every archived path — with
    `-1`, the anchor would name a commit that never contained the code. No fixture distinguished
    the two until this one, so reverting the walk kept the suite green (review cycle 7).
    """
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    adding = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    subprocess.run(["git", "rm", "-q", "tortoise/gone.py"], cwd=tree, check=True)
    _commit(tree)
    deleting = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    assert adding != deleting
    # The naive answer is the DELETION commit — the one the walk must not choose.
    assert arch._git(tree, "rev-list", "-1", "main", "--", "tortoise/gone.py")[1].strip() == deleting
    sha, _ref = arch.anchor_sha(tree, ["tortoise/gone.py"])
    assert sha == adding, "the anchor must be the commit that still HOLDS the code"
    assert arch._git_blob(tree, sha, "tortoise/gone.py") is not None


def test_a_body_that_exists_in_the_file_but_is_not_the_symbol_is_reported(tree: Path) -> None:
    """The exactness arm's positive control: a SUBSTRING of the anchored file is not the symbol.

    Both existing anchor tests anchor at a commit that holds no blob, so they are carried by the
    "commit does not hold the path" arm; weakening the comparison back to containment stayed
    green (measured in review cycle 7). This forges a body that IS in the file.
    """
    entry = arch.archive_symbol(
        arch.SDK_METHOD, "get_entity", "batch-a", "r", root=tree
    )
    # `_declares_apply_first_write_lane(...)`'s body is in sdk.py at the anchor, and it is NOT
    # `get_entity` — a containment test accepts it, the exact test must not.
    sdk = (tree / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    other = next(line for line in sdk.splitlines() if line.startswith("def _declares"))
    forged = other + "\n    return lambda fn: fn"
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        entry["archived_from"], entry["archived_at"], [("tortoise/sdk.py", forged, "FunctionDef:get_entity")],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    problems = arch.file_violations(
        [dict(entry, blocks=frozen)], tree / "archive", tree
    )
    assert any("does not exist in" in problem or "not what" in problem for problem in problems), (
        "a body that is merely PRESENT in the anchored file is not the symbol"
    )


def test_check_reports_every_arm_it_aggregates(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One forced violation per call site, or three of the four arms are deletable green.

    Round 8 measured exactly that: deleting the `file_violations`, `package_violations` or
    `index_violations` line from `check` left all 66 tests passing, because each helper has its
    own test and a clean archive makes every arm silent.
    """
    arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    (tree / "archive" / "mcp-tools" / "batch-a" / "orphan.py").write_text("x = 1\n", encoding="utf-8")
    (tree / "tortoise" / "archive").mkdir()
    (tree / "archive" / "INDEX.md").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    problems = arch.check(tree)
    assert any("orphan" in problem for problem in problems), problems          # file arm
    assert any("tortoise/archive exists" in problem for problem in problems), problems  # package arm
    assert any("STALE" in problem for problem in problems), problems           # index arm


def test_the_writer_refuses_a_still_served_retired_forward_name(tree: Path) -> None:
    """A `RETIRED_USE_INSTEAD` name is STILL SERVED — archiving it writes a row its gate rejects."""
    (tree / "tortoise" / "tool_registry.py").write_text(
        (tree / "tortoise" / "tool_registry.py").read_text(encoding="utf-8")
        + "\nRETIRED_USE_INSTEAD = {'tortoise_search': 'tortoise_new'}\n",
        encoding="utf-8",
    )
    _commit(tree, "tortoise/tool_registry.py")
    with pytest.raises(arch.ArchiveRefused, match="still SERVED"):
        arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)


def test_a_product_module_that_imports_the_archive_is_reported(tree: Path) -> None:
    """The archive's promise, reversed: removed behaviour must not re-enter the product.

    `archive` is a namespace package reachable from a checkout, so this import RUNS — and the
    suite runs from the repo root, so every other gate stayed green while a live method called
    archived code (measured, review round 9). Nothing read the product's import graph before.
    """
    (tree / "tortoise" / "live.py").write_text(
        "from archive.modules.b1.tortoise.memory_scope import MemoryScope\n"
        "\n"
        "def f():\n"
        "    return MemoryScope\n",
        encoding="utf-8",
    )
    problems = arch.package_violations(tree)
    assert any("imports `archive.modules.b1.tortoise.memory_scope`" in problem for problem in problems), (
        problems
    )

    # …and the one-line DYNAMIC bypass is caught too.
    (tree / "tortoise" / "live.py").write_text(
        "import importlib\n"
        "_m = importlib.import_module(\"archive.modules.b1.tortoise.memory_scope\")\n",
        encoding="utf-8",
    )
    assert any(
        "imports `archive.modules.b1.tortoise.memory_scope`" in problem
        for problem in arch.package_violations(tree)
    )

    # …the KEYWORD form is readable too — it is the documented spelling of `import_module`.
    (tree / "tortoise" / "live.py").write_text(
        "import importlib\n"
        "_m = importlib.import_module(name=\"archive.modules.b1.tortoise.memory_scope\")\n",
        encoding="utf-8",
    )
    assert any(
        "imports `archive.modules.b1.tortoise.memory_scope`" in problem
        for problem in arch.package_violations(tree)
    ), "import_module(name=...) is a static literal, not a runtime-built name"

    # …but a module that merely STARTS with the letters (a legitimate `archive_utils` package)
    # is not the archive: the separator test is what keeps this arm from a false red.
    (tree / "tortoise" / "live.py").write_text(
        "import importlib\n_m = importlib.import_module('archived_helpers.core')\n",
        encoding="utf-8",
    )
    assert not any("imports `archive" in problem for problem in arch.package_violations(tree)), (
        "`archived_helpers` is not `archive` — a prefix match would false-red a live PR"
    )

    # …and a MENTION in prose is not an import: the scan parses, it does not grep.
    (tree / "tortoise" / "live.py").write_text(
        'def f():\n    """Restored from archive.modules.b1.tortoise.memory_scope."""\n    return 1\n',
        encoding="utf-8",
    )
    assert not any("imports `archive" in problem for problem in arch.package_violations(tree))


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


# ── the restore path's RUNTIME dependencies ─────────────────────────────────────

class TestRestoreDependenciesResolve:
    """A restore recipe is "copy the block back into the module it names".

    That works only if every MODULE-LEVEL name the frozen handler reads still exists in
    its target module. `archive_surface.py` cannot see this: `file_violations` digests the
    archived file against the manifest and `surface_violations` proves the symbol is not
    live — neither reads the target module. So deleting a name the archived handler needs
    leaves every gate green while a restore raises `NameError` on its FIRST call.

    Measured (#11609 batch 1 review, P2): `ERR_UNAUTHORIZED` was used only by the
    archived `tortoise_packs_list` handler, so it left `mcp_server`'s import block with
    the tool — `'ERR_UNAUTHORIZED' in dir(tortoise.mcp_server)` was False, and
    `verify-anchors` was still green.

    The check is DERIVED, not a hand-kept list: it walks the frozen handler's AST and
    collects the free names (loads minus parameters, assignments, comprehension targets,
    handlers, and imports), so a dependency added to the archive later is covered without
    anyone remembering to list it.
    """

    BANNER = re.compile(r"^# ── verbatim from (\S+) ──$", re.M)

    def _free_names(self, handler: ast.FunctionDef) -> set[str]:
        local: set[str] = {a.arg for a in (*handler.args.posonlyargs,
                                           *handler.args.args,
                                           *handler.args.kwonlyargs)}
        for extra in (handler.args.vararg, handler.args.kwarg):
            if extra is not None:
                local.add(extra.arg)
        for node in ast.walk(handler):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                local.add(node.id)
            elif isinstance(node, ast.comprehension):
                local.update(t.id for t in ast.walk(node.target)
                             if isinstance(t, ast.Name))
            elif isinstance(node, ast.ExceptHandler) and node.name:
                local.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                local.update(a.asname or a.name.split(".")[0] for a in node.names)
        loads = {n.id for n in ast.walk(handler)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        return loads - local - set(dir(builtins))

    def test_every_free_name_in_a_frozen_handler_resolves_in_its_module(self) -> None:
        # Population from the MANIFEST, not a batch path literal: `archive_rel()`
        # writes one directory per batch, so a hardcoded
        # `mcp-tools/b1-removed-no-destination/` would silently exempt every future
        # batch — the "covers one instance, calls it a class" shape this arm exists to
        # avoid. Every MCP-tool record is in scope, whichever batch wrote it.
        records = sorted(arch.ROOT / e["file"]
                         for e in arch.load_manifest()["entries"]
                         if e["kind"] == arch.MCP_TOOL)
        assert records, ("no mcp-tool rows in archive/MANIFEST.json — this test would "
                         "be vacuous (the population is the manifest, not a batch path)")
        missing: list[str] = []
        checked = 0
        for record in records:
            text = record.read_text(encoding="utf-8")
            symbol = record.stem
            at = text.index(f"def {symbol}(")
            tail = text[at:]
            nxt = tail.find("# ── verbatim from ")
            segment = tail[:nxt] if nxt != -1 else tail
            handler = next(n for n in ast.parse(segment).body
                           if isinstance(n, ast.FunctionDef))
            assert handler.name == symbol, handler.name

            banner = None
            for match in self.BANNER.finditer(text):
                if match.start() < at:
                    banner = match.group(1)
            assert banner is not None, f"{record.name}: no `verbatim from` banner"
            module = importlib.import_module(
                banner.removesuffix(".py").replace("/", "."))

            # Non-vacuity: the handler must actually read module-level names, or this
            # arm proves nothing about the restore path.
            free = self._free_names(handler)
            assert free, f"{symbol}: no module-level names found — parser drift"
            checked += 1
            missing.extend(f"{symbol} -> {banner}: {name}"
                           for name in sorted(free) if not hasattr(module, name))
        assert checked == len(records)
        assert missing == [], (
            "a frozen handler reads a name its target module no longer defines, so the "
            "archive's own restore recipe raises NameError on the first call while "
            "`archive_surface.py verify-anchors` stays green (it proves byte-identity "
            f"only): {missing}")

    def test_the_arm_reds_when_a_restore_dependency_is_deleted(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Positive control: delete the dependency and the arm above must fail."""
        import tortoise.mcp_server as mcp_mod
        monkeypatch.delattr(mcp_mod, "ERR_UNAUTHORIZED", raising=True)
        with pytest.raises(AssertionError, match="NameError on the first call"):
            self.test_every_free_name_in_a_frozen_handler_resolves_in_its_module()


# ── 10. the SHRINK direction (#12402 + #12291) ────────────────────────────────
#
# Every arm above iterates the entries the manifest FINDS. These two questions are the ones
# that shape cannot ask, and each is proven BOTH ways here: the guard must red on the
# violation (performed, in a scratch repo, not simulated) and stay green on the legitimate
# case. A guard nobody has seen fire is the defect #12402/#12291 were filed about.

def _commit_drop(root: Path, path: str) -> None:
    """Delete a tracked path and commit — the violation, performed for real."""
    subprocess.run(["git", "rm", "-q", path], cwd=root, check=True)
    _commit(root, ".")


def _archive_module(tree: Path, module: str) -> dict:
    """Record a `module` entry AND write its frozen file — what `archive` actually lands.

    Both halves matter: the row alone makes the "row **and** its file" scenario unreachable,
    and a test that claims to delete a file it never created proves nothing about the guard
    (review F4).
    """
    entry = _entry(arch.MODULE, module, batch="b-shrink", file=f"archive/modules/b-shrink/{module}.py")
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(f"# frozen {module}\n", encoding="utf-8")
    return entry


def test_a_module_removal_with_no_archive_entry_is_reported(tree: Path) -> None:
    """#12402: a whole module leaves `tortoise/` and every other gate is blind to it."""
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    _commit_drop(tree, "tortoise/gone.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("tortoise.gone" in p and "MODULE REMOVED" in p for p in problems), problems
    joined = " ".join(problems)
    assert "archive module" in joined and "WORKING TREE" in joined, (
        "the violation must print the runnable route, including the order trap (the writer "
        f"reads the WORKING TREE, so archiving after the deletion refuses): {problems}"
    )


def test_a_module_removal_with_an_archive_entry_is_green(tree: Path) -> None:
    """The legitimate case: the module was frozen, so the deletion has its record."""
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    _commit_drop(tree, "tortoise/gone.py")
    _archive_module(tree, "tortoise.gone")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == []


def test_a_renamed_module_is_a_removal_of_the_old_path(tree: Path) -> None:
    """#12402's acceptance names renaming: the old DOTTED PATH stops existing either way."""
    (tree / "tortoise" / "old_name.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/old_name.py")
    subprocess.run(["git", "mv", "tortoise/old_name.py", "tortoise/new_name.py"], cwd=tree, check=True)
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    removed = [p for p in problems if "MODULE REMOVED" in p]
    assert [p.split(":", 1)[0] for p in removed] == ["tortoise.old_name"], (
        "exactly the OLD dotted path is a removal. Asserting only that `new_name` is absent is "
        "dead weight (an ADDED path cannot enter `base - head`); asserting the SET is what reds "
        f"a head-side or symmetric-difference mutant that calls new code a removal: {problems}"
    )


def test_deleting_an_init_py_whose_package_still_imports_is_not_a_removal(tree: Path) -> None:
    """A directory that still holds submodules is an importable namespace package."""
    (tree / "tortoise" / "pkg").mkdir()
    (tree / "tortoise" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "tortoise" / "pkg" / "sub.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/pkg")
    _commit_drop(tree, "tortoise/pkg/__init__.py")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "`tortoise.pkg` still imports through `pkg/sub.py`, so nothing a caller could import "
        "was removed — reding this is the false block that makes an operator disable the gate"
    )


def test_a_module_that_was_scaffolding_on_both_sides_is_not_a_removal(tree: Path) -> None:
    """Non-vacuity for the emptied-in-place arm: a placeholder that stays one removed nothing."""
    (tree / "tortoise" / "stub.py").write_text('"""Placeholder."""\n', encoding="utf-8")
    _commit(tree, "tortoise/stub.py")
    (tree / "tortoise" / "stub.py").write_text("", encoding="utf-8")
    _commit(tree, "tortoise/stub.py")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "a module that carried no statements on EITHER side is scaffolding on both sides; "
        "reding it is the false block that makes an operator disable the gate"
    )


def test_a_module_replaced_by_a_pyi_STUB_is_a_removal(tree: Path) -> None:
    """A `.pyi` has no runtime statements, so it cannot stand in for a module (round-6 review).

    `module_name` collapses `.py` and `.pyi` onto one dotted name and the content rule only read
    `/__init__.py`, so `git rm x.py` + a stub `x.pyi` satisfied the name set AND the content test
    — arm 1 saw the name on both sides and arm 1b read the STUB as a body. Graded green, with the
    module's runtime code gone (measured in review; `main` carries zero `.pyi` files under
    `tortoise/`, so nothing had ever exercised the shape).
    Mutation: `stubbed = False` -> RED here (the arm falls back to the content read, and a
    non-empty stub reads as a body). The carrier-map determinism is `test_a_pyi_stub_beside_a_
    live_module_is_NOT_a_removal`'s question, not this one.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py")
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.py"], cwd=tree, check=True)
    (tree / "tortoise" / "stubby.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    removed = [p for p in problems if "MODULE BODY REMOVED" in p]
    assert [p.split(":", 1)[0] for p in removed] == ["tortoise.stubby"], (
        "a stub is not the module's runtime code: the name resolves, the code is gone — "
        f"{problems}"
    )
    assert ".pyi" in removed[0], f"the violation must name the stub it graded: {removed[0]}"


def test_a_module_row_that_froze_the_STUB_is_not_a_record(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose frozen SOURCE was the stub records nothing (fresh-context review, P0).

    `_module_source`'s `.pyi` fallback resolves the stub when the only file left is `x.pyi`, so
    the writer freezes `def value() -> int: ...` as the SOURCE OF RECORD for a module whose body
    this same run reports as removed — and the credit rule above (`if key in working:
    continue`) then reads "the removal has its record" over a record holding no code. Every arm
    green, the removed body in no file under `archive/`: the false PASS `archive/` exists to
    prevent.

    ⛔ This row drives the WRITER on purpose. The first version hand-built a row with
    `file=".../tortoise.stubby.pyi"` and the guard keyed on that suffix — a shape `archive_rel`
    cannot produce (every module row is rendered `.py`), so the guard was dead code on the
    product's own path and this test could not see that. The row's `archived_from` DOES name
    `tortoise/stubby.pyi` (and `file_violations` binds it to the frozen header), which is the
    fact the guard now reads.

    ⛔ `_init_carries_a_body` cannot be the test here even though it guards the sibling scaffold
    shape: it is an AST question, and a stub IS statements. Arm 1b's own rule is that a `.pyi` is
    never runtime code, so the credit rule asks THAT question instead.

    THIS row is the stub-LEFT shape (`base ∩ head`, arm 1b — `stubbed`); the row below is the
    deleted-stub shape (`base - head`, arm 1), because both credit sites can silence the same
    false PASS and a guard at only one of them is a guard at neither.

    Discriminating: removing the `stubbed and _row_froze_a_stub(...)` arm leaves this row with no
    violation, and the negative control below (same base, same stub, a row that froze the REAL
    body) stays green either way — measured, both directions.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py")
    base = _head_sha(tree)
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.py"], cwd=tree, check=True)
    (tree / "tortoise" / "stubby.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, ".")

    entry = arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review", root=tree)
    assert entry["archived_from"] == "tortoise/stubby.pyi", (
        "the row must record WHICH source it froze — that is the fact the guard reads: " f"{entry}"
    )
    assert entry["file"].endswith(".py") and not entry["file"].endswith(".pyi"), (
        "the writer renders a module row as `.py` whatever it froze from — which is exactly why "
        f"the archived-path suffix cannot be the test: {entry['file']}"
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref=base)
    froze = [p for p in problems if "THE RECORD FROZE A STUB" in p]
    assert froze, f"a row that froze the stub must not credit the removal: {problems}"
    assert "tortoise.stubby" in froze[0], froze[0]
    assert "tortoise/stubby.pyi" in froze[0], f"the violation must name the stub source: {froze[0]}"

    # …and the GATE, which is where the false PASS was measured.
    monkeypatch.setattr(arch, "ROOT", tree)
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])
    rc = arch.main(["archive_surface.py", "check", "--base-ref", base])
    assert rc == 1, "the gate itself must red, not only the helper"


def test_a_stub_row_credits_nothing_after_the_stub_is_deleted(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same false PASS one step later: the stub is deleted, as the writer's own output says.

    `archive module tortoise.x` prints "now delete it from tortoise/x.pyi in this batch", so the
    operator does — and the module is then in `base - head` (arm 1), NOT in `base ∩ head`
    (arm 1b, where the P0 guard was first written). Measured on the pre-fix bytes through the
    writer and the real CLI: `check --base-ref` answered

        OK — archive consistent: 1 entries (… 1 modules). Shrink checked against …

    with the removed body in NO file under `archive/`, and `shrink_violations` returned `[]`.
    A guard placed only in arm 1b could not see that, which is why the rule lives in one helper
    and both credit sites call it.

    Discriminating: delete the arm-1 call site (`if runtime_carrier is not None and
    _row_froze_a_stub(...)`) -> RED here, and the row above stays green.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py")
    base = _head_sha(tree)
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.py"], cwd=tree, check=True)
    (tree / "tortoise" / "stubby.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, ".")
    arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review", root=tree)
    _commit_drop(tree, "tortoise/stubby.pyi")  # the deletion the writer printed

    assert not (tree / "tortoise" / "stubby.py").exists()
    assert not (tree / "tortoise" / "stubby.pyi").exists()

    problems = arch.shrink_violations(tree, base_ref=base)
    froze = [p for p in problems if "THE RECORD FROZE A STUB" in p]
    assert froze, f"the deleted-stub shape credits the same empty record: {problems}"
    assert "no module file at all" in froze[0], froze[0]

    monkeypatch.setattr(arch, "ROOT", tree)
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])
    assert arch.main(["archive_surface.py", "check", "--base-ref", base]) == 1, (
        "the measured false PASS was `check --base-ref` answering OK on this exact tree"
    )


def test_a_module_row_that_LIES_about_archived_from_cannot_credit_a_stub(
    tree: Path,
) -> None:
    """The row's claim field is writable by the splicer; `blocks[].path` is not (review P1).

    `archived_from` is bound only to the frozen file's `ARCHIVED_FROM` header — a string the same
    hand-splicing author writes. `tools/archive_surface.py` records that limit in
    `_reanchor_is_verified`'s "EVERY OTHER CLAIM FIELD IS FIXED" note ("bound to nothing else on
    this path … so a consistent lie passed both"), and hand-splicing is the procedure `MEMORY.md`
    recommends, so this is a REACHABLE shape, not a contrived one. Measured before the fix: a spliced row saying `tortoise/stubby.py` over frozen
    STUB bytes was credited, with `shrink_violations` AND `file_violations` BOTH empty.

    The field tied to the bytes is `blocks[].path`: `file_violations` requires it to equal the path
    baked into the frozen file's verbatim banner and `blocks[].sha256` to equal that body's digest,
    so it cannot be re-pointed without the bytes following.

    Discriminating: an arm keyed on `archived_from` credits this row (measured) — which is why the
    writer-driven row above cannot catch it on its own; there `archived_from` and the block path
    agree, so only this row separates the two answers.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py")
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.py"], cwd=tree, check=True)
    stub = "def value() -> int: ...\n"
    (tree / "tortoise" / "stubby.pyi").write_text(stub, encoding="utf-8")

    entry = _entry(arch.MODULE, "tortoise.stubby")
    entry["archived_from"] = "tortoise/stubby.py"  # the lie
    entry["blocks"] = [{"path": "tortoise/stubby.pyi", "sha256": "a" * 64}]  # the truth
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(stub, encoding="utf-8")
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    _commit(tree, ".")

    assert arch._row_froze_a_stub(entry), (
        "the block path is bound to the frozen banner and digest, so it decides even when the "
        f"claim field says otherwise: {entry}"
    )
    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    froze = [p for p in problems if "THE RECORD FROZE A STUB" in p]
    assert froze, f"a spliced row that froze the stub must not be credited: {problems}"
    assert "tortoise/stubby.pyi" in froze[0], froze[0]


def test_a_module_row_that_froze_the_REAL_body_is_still_credited(tree: Path) -> None:
    """The negative control for both rows above, through the writer's restore-then-freeze route.

    Same base, same stub left in the tree, same `stubbed` — only `archived_from` differs (the
    real `.py` body, frozen BEFORE the file was deleted, which is `archive/README.md`'s documented
    route). If the arm keyed on `stubbed` alone, or on the row merely existing, this legitimate
    record would red and that route would stop working. Measured: collapsing the condition to
    `if stubbed:` reds this row.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py")
    base = _head_sha(tree)
    entry = arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review", root=tree)
    assert entry["archived_from"] == "tortoise/stubby.py"
    _commit(tree, ".")
    _commit_drop(tree, "tortoise/stubby.py")
    (tree / "tortoise" / "stubby.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.pyi")

    problems = arch.shrink_violations(tree, base_ref=base)
    assert not [p for p in problems if "THE RECORD FROZE A STUB" in p], (
        f"a row holding the real body is the legitimate record and must not red: {problems}"
    )


def test_a_module_waiver_does_not_claim_the_tree_carries_a_module_it_only_STUBBED(
    tree: Path,
) -> None:
    """The waiver diagnostic must not disagree with arm 1b about a `.pyi` stub (review P2).

    Base carries `tortoise/stubby.py` (+ a stub, so the anchor resolves); the tree keeps only the
    stub; the `module` row freezes the REAL body, so arm 1b credits the row and the waiver this
    run was given is left UNCONSUMED. The diagnostic then has to answer "does this tree still
    carry the module?" — and the body-only answer it used to carry counts a stub as a body,
    printing "this tree still carries it, so nothing was removed" over the very removal the same
    run graded and credited. It now asks `_head_carries_runtime_body`, the single predicate arm
    1b also decides on.

    Discriminating: answering that diagnostic with a body-only test ("the file exists and
    parses") reds the `still carries it` assertion below, because a `.pyi` stub passes it.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "stubby.pyi").write_text(
        "def value() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, ".")
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.py"], cwd=tree, check=True)
    entry = _entry(arch.MODULE, "tortoise.stubby")
    entry["archived_from"] = "tortoise/stubby.py"
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.stubby"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"the unconsumed waiver must still be reported: {problems}"
    assert "still carries it" not in waived[0], (
        "a `.pyi` stub is not the module's runtime body, and arm 1b already graded its removal "
        f"in this same run: {waived[0]}"
    )
    assert "this tree does not" in waived[0], waived[0]


def test_a_waiver_for_a_row_that_FROZE_A_STUB_does_not_say_the_removal_IS_recorded(
    tree: Path,
) -> None:
    """One run must not print two contradicting statements about the same entry (review F1).

    **Writer-driven, nothing spliced.** Base carries `tortoise/stubby.py`; the tree deletes it and
    leaves `tortoise/stubby.pyi`; `archive_symbol` then freezes the STUB — measured, that row's
    `archived_from` and `blocks[0].path` are BOTH `tortoise/stubby.pyi` (`archive_symbol` sets
    `archived_from = blocks[0][0] if kind == MODULE`), so `_row_froze_a_stub` is True. Arm 1b then
    appends "THE RECORD FROZE A STUB" and `continue`s BEFORE consuming the waiver — so the
    diagnostic loop re-derives the same key and, asking only `key in working`, printed "the
    removal IS recorded by the `module` entry you also passed" in the same run.

    ⛔ Built through `archive_symbol`, not by hand: a hand-built row here would advertise a
    writer-side property it never calls, and a later change to what the writer freezes would
    leave it green. This also pins `archived_from` to the `.pyi`, which is the fact that makes
    the anti-stub rule fire on a module that still has a stub on disk.

    Discriminating: the sibling test above covers the opposite arm — its row froze the REAL body,
    so `_row_froze_a_stub` is False there and a key-presence-only check is right. Only this row
    separates "an entry exists" from "an entry that records the source", which is the whole
    point of the anti-stub rule it sits beside.
    """
    (tree / "tortoise" / "stubby.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "stubby.pyi").write_text(
        "def value() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, ".")
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.py"], cwd=tree, check=True)
    entry = arch.archive_symbol(
        arch.MODULE, "tortoise.stubby", "batch-a", "review F1", root=tree
    )
    _commit(tree, ".")

    assert entry["archived_from"] == "tortoise/stubby.pyi", (
        "the writer froze a stub, so the row itself must say so — this is what makes the "
        f"anti-stub rule fire: {entry}"
    )
    assert arch._row_froze_a_stub(entry), "this fixture must freeze the stub, not the body"
    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.stubby"]
    )
    assert [p for p in problems if "THE RECORD FROZE A STUB" in p], (
        f"the stub row must not credit the removal: {problems}"
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"the unconsumed waiver must still be reported: {problems}"
    assert "IS recorded" not in waived[0], (
        "the same run just graded this entry as having frozen a stub — saying the removal IS "
        f"recorded by it contradicts that and points away from the fix: {waived[0]}"
    )
    assert "FROZE ONLY A STUB" in waived[0], waived[0]


def test_a_waiver_over_a_STUB_ONLY_base_does_not_send_the_reader_to_the_base(
    tree: Path,
) -> None:
    """The base may hold only a `.pyi`, so "restore from the base" can be impossible (review F1).

    `_modules_with_a_body` filters only `__init__.py`, so a `.pyi`-only module IS in
    `base_modules` — membership there does NOT mean the base holds the `.py`. An earlier round
    cleared this second copy of the sentence on the opposite assumption; the shape below is the
    counterexample: base carries only `tortoise/stubby.pyi`, this run archives that stub (the
    writer sets `archived_from` to the `.pyi`, so `_row_froze_a_stub` is True and arm 1 refuses to
    credit it), deletes it as the writer instructs, and passes an unnecessary waiver. The
    diagnostic then runs and must not print a `git checkout <base> -- tortoise/stubby.py` route.

    Discriminating: the old wording prints "restore the source from the base" here, and that base
    has no `tortoise/stubby.py` for the route to resolve against. The round-20 wording that
    followed it ("a commit that still holds it") is unfollowable for the same reason — NO commit
    holds a `.py` for a module whose only carrier is a stub — so this row also pins that the
    diagnosis names the true fact instead: there was no runtime source to record, and the stub row
    is the writer's own correct output for a stub-only module (see the writer's rule at
    `_module_source`, "a stub-only module's only code IS the stub").
    """
    (tree / "tortoise").mkdir(exist_ok=True)
    (tree / "tortoise" / "stubby.pyi").write_text(
        "def value() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, ".")
    entry = arch.archive_symbol(
        arch.MODULE, "tortoise.stubby", "batch-a", "review F1", root=tree
    )
    assert entry["archived_from"] == "tortoise/stubby.pyi", entry
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.pyi"], cwd=tree, check=True)
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.stubby"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"the unconsumed waiver must still be reported: {problems}"
    assert "from the base" not in waived[0], (
        "the base holds only the stub, so a checkout from it cannot yield the source: "
        f"{waived[0]}"
    )
    assert "not a record" not in waived[0], (
        "a stub-only module's stub IS its code, so this row is a record — calling it 'not a "
        f"record' is false and sends the reader to redo a correct freeze: {waived[0]}"
    )
    assert "no runtime source to record" in waived[0], waived[0]
    assert "drop the flag" in waived[0], waived[0]


def test_a_waiver_over_a_STUB_ONLY_base_whose_row_is_at_the_BASE_says_it_too(
    tree: Path,
) -> None:
    """The OTHER copy of the sentence, reachable when the stub row was committed at the base.

    Two rounds in a row fixed the `elif symbol in base_modules` copy and left this one — the
    `key in base_entries` branch — emitting "restore the source from a commit that still holds
    it" over a repo in which `git log --all -- tortoise/stubby.py` is empty, and calling a correct
    stub-only row "not a record of the source". The two copies contradicted each other for the
    same state depending only on WHERE the row was committed.

    Discriminating: the had-runtime wording contains "FROZE ONLY A STUB" and "restore the source"
    and neither is asserted absent, so this row is what pins the split at this site.
    """
    (tree / "tortoise").mkdir(exist_ok=True)
    (tree / "tortoise" / "stubby.pyi").write_text(
        "def value() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, ".")
    arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review F1", root=tree)
    _commit(tree, ".")  # the BASE carries the row
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.pyi"], cwd=tree, check=True)
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.stubby"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"the unconsumed waiver must still be reported: {problems}"
    assert "no runtime source to record" in waived[0], waived[0]
    assert "drop the flag" in waived[0], waived[0]
    assert "FROZE ONLY A STUB" not in waived[0], (
        "the base held only a stub, so the row is the writer's correct output — not a stub "
        f"frozen over a deleted body: {waived[0]}"
    )
    assert "from the base" not in waived[0], waived[0]


def test_a_waiver_over_a_STUB_ONLY_base_with_NO_carrier_says_it_too(tree: Path) -> None:
    """An ABSENT base carrier is the strongest proof there was no `.py` — and it was the one shape
    that fell through (review F1 of round 22).

    The split keyed on `endswith(".pyi")`, so an empty carrier — the base holding the row and NO
    file at all, i.e. an earlier PR already archived and deleted a stub-only module — took the
    had-runtime arm and re-emitted both removed falsities: "it is not a record", and "restore the
    source from a commit that still holds it" over a repo where `git log --all -- tortoise/
    stubby.py` is empty. The polarity is now "does the base carry a RUNTIME source".

    Discriminating: keying on `.pyi` reds this row (measured), because `` does not end with it.
    """
    (tree / "tortoise").mkdir(exist_ok=True)
    (tree / "tortoise" / "stubby.pyi").write_text(
        "def value() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, ".")
    arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review F1", root=tree)
    subprocess.run(["git", "rm", "-q", "tortoise/stubby.pyi"], cwd=tree, check=True)
    _commit(tree, ".")  # the BASE: the row exists, NO carrier on either side
    (tree / "unrelated.txt").write_text("x\n", encoding="utf-8")
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.stubby"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"the unconsumed waiver must still be reported: {problems}"
    assert "on neither side" in waived[0], waived[0]
    assert "FROZE ONLY A STUB" not in waived[0], (
        f"no commit holds a `.py` for this module, so nothing runtime was removed: {waived[0]}"
    )
    assert "not a record" not in waived[0], waived[0]


def test_the_stub_waiver_text_names_the_head_carrier_it_can_see() -> None:
    """`base_paths_by_module` proves the BASE side only (review F2).

    The sentence said "and no `.py` on either side", which that map cannot support: it is built
    from `base_paths` alone, and the head can hold a `.py` — an emptied one, or a docstring
    `__init__.py` that `module_name` collapses onto the same symbol. The head carrier is now named
    explicitly and never claimed absent.

    Discriminating: the previous wording asserted "no `.py` on either side" while holding a head
    `.py`, and this assertion rejects exactly that string.
    """
    said = arch._stub_waiver_why(
        "P", "tortoise/x.pyi", "tortoise/x.py", base_has_runtime_body=False
    )
    assert "tortoise/x.py" in said, said
    assert "no `.py` on either side" not in said, said
    assert arch._stub_waiver_why(
        "P", "tortoise/x.pyi", "", base_has_runtime_body=False
    ).endswith(
        "this tree carries no module file, so there was no runtime source to record. The "
        "`module` entry you also passed is the writer's own output for a stub-only module (a "
        "stub-only module's only code IS the stub), and nothing was removed for the waiver to "
        "waive: drop the flag"
    )


def test_a_module_body_replaced_by_UNPARSEABLE_text_is_a_removal(tree: Path) -> None:
    """A body that no longer parses holds no statements a caller can reach (round-6 review).

    `_init_carries_a_body` returned True on `SyntaxError` while its docstring called that "fail
    CLOSED toward the violation" — it was the GREEN direction: a 40-function module rewritten to
    `this is not python {{{` graded green on every arm. The arm now reports the removal.
    Mutation: `except SyntaxError: return True` -> RED here.
    """
    (tree / "tortoise" / "victim.py").write_text(
        "".join(f"def f{i}():\n    return {i}\n" for i in range(40)), encoding="utf-8"
    )
    _commit(tree, "tortoise/victim.py")
    (tree / "tortoise" / "victim.py").write_text("this is not python {{{\n", encoding="utf-8")
    _commit(tree, "tortoise/victim.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(
        p.startswith("tortoise.victim: MODULE BODY REMOVED") for p in problems
    ), f"unparseable is not content — the code left: {problems}"
    hit = next(p for p in problems if p.startswith("tortoise.victim: MODULE BODY REMOVED"))
    assert "no longer parses" in hit, (
        "the verdict is right but the stated fact must not send the operator looking for an "
        f"EMPTIED file: {hit}"
    )


def test_emptying_a_module_in_place_is_a_removal(tree: Path) -> None:
    """`> tortoise/x.py` kept every gate green while the code left (review P2 of #14039).

    The content rule was applied to `/__init__.py` only, so a file truncated to zero bytes (or
    to a bare docstring) stayed in BOTH module-name sets and arm 1 reported nothing — the same
    fail-open round-3 F2 closed for the package init, one path shape over. Mutation: restrict
    the read back to `endswith('/__init__.py')` -> RED (green on a module whose body is gone).
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "stays.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise")
    (tree / "tortoise" / "gone.py").write_text("", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    removed = [p for p in problems if "MODULE BODY REMOVED" in p]
    assert [p.split(":", 1)[0] for p in removed] == ["tortoise.gone"], (
        "the emptied module must be reported, and as the BODY arm — a name-set comparison "
        f"cannot see it (the path survived). Problems: {problems}"
    )
    assert not any("tortoise.stays" in p for p in problems), problems
    assert "archive module" in " ".join(problems), (
        "the same runnable route as a deletion, or the operator has a red with no way out"
    )


def test_a_package_is_archivable_by_its_dotted_path(tree: Path) -> None:
    """The route arm 1 PRINTS must run for the shape arm 1 grades (review P1 of #14039).

    Arm 1 grades an `__init__.py` by CONTENT (round-3 F2), so deleting `tortoise/__init__.py`
    reports `tortoise: MODULE REMOVED` and prints `archive module tortoise`. The writer resolved
    only `<dotted>.py`, so that command refused with "no file at tortoise.py" and the only other
    route is a waiver the workflow never passes — a correct red with no way out.
    Mutation: drop the `__init__.py` fallback -> RED (ArchiveRefused).
    """
    (tree / "tortoise" / "__init__.py").write_text(
        "from tortoise.core import X\n\n__all__ = ['X']\n", encoding="utf-8"
    )
    (tree / "tortoise" / "core.py").write_text("X = 1\n", encoding="utf-8")
    _commit(tree, "tortoise")

    entry = arch.archive_symbol(arch.MODULE, "tortoise", "batch-a", "r", root=tree)
    assert entry["archived_from"] == "tortoise/__init__.py", (
        "the frozen body came from the package __init__, so that is what the record must cite"
    )
    assert entry["file"] == "archive/modules/batch-a/tortoise.py", (
        "the archived FILE must not be an `__init__.py`: `archive/` is a readable source tree, "
        "not an importable package, and `package_violations` refuses one — a writer that wrote "
        "one would create a file its own gate reds"
    )
    assert arch.package_violations(tree) == [], arch.package_violations(tree)

    _commit(tree, ".")
    _commit_drop(tree, "tortoise/__init__.py")
    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "the archive row this writer just wrote must be the record that clears the removal — "
        "otherwise the printed remedy still leaves the operator red"
    )


def test_archiving_a_module_must_not_freeze_a_LEFTOVER_scaffold(tree: Path) -> None:
    """The package fallback must not record a SCAFFOLD as the module's source of record.

    Fresh-context review of PR #14039 (P2). The fallback above resolves `<dotted>/__init__.py`
    whatever it holds, so `archive module <dotted>` on a DELETED body whose package was left as a
    bare docstring froze that docstring — and `shrink_violations` credits the removal on the
    row's EXISTENCE, so `check --base-ref` then reported "archive consistent", `verify-anchors`
    said OK, and the removed code was in NO file under `archive/`. Measured on the pre-fix bytes
    (scratch repo, `tortoise/x.py` = `def f39`): the CLI printed "archived module tortoise.x" /
    "now delete it from tortoise/x/__init__.py", the frozen file held only the docstring, and
    `f39` was in no archived file. A record that names code it does not hold is the one thing
    `archive/` exists to make impossible.

    Mutation: delete `if package_fallback and not _init_carries_a_body(source)` from
    `_module_source` -> RED here (the writer succeeds and writes the scaffold). The positive
    control is `test_a_package_is_archivable_by_its_dotted_path` above, whose `__init__.py`
    carries statements and must STAY archivable.
    """
    (tree / "tortoise" / "x.py").write_text("def f39():\n    return 39\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py")
    subprocess.run(["git", "rm", "-q", "tortoise/x.py"], cwd=tree, check=True)
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").write_text('"""Scaffolding."""\n', encoding="utf-8")
    (tree / "tortoise" / "x" / "sub.py").write_text("Y = 1\n", encoding="utf-8")
    _commit(tree, ".")

    with pytest.raises(arch.ArchiveRefused, match="SCAFFOLDING"):
        arch.archive_symbol(arch.MODULE, "tortoise.x", "batch-a", "r", root=tree)
    assert not list((tree / "archive").rglob("*.py")), (
        "the refusal must not leave a record behind: a row frozen from a scaffold is the false "
        "record this guard exists to prevent"
    )


def test_archiving_a_PLAIN_py_whose_body_is_now_a_DOCSTRING_is_refused(tree: Path) -> None:
    """The scaffold refusal was gated on the PACKAGE fallback, so the plain `.py` shape was open.

    Round-15 review (P2), measured by the reviewer and re-measured here: arm 1b grades a module
    whose file is still there but whose BODY is gone as `MODULE BODY REMOVED`, and the remedy it
    prints is `archive module <dotted>` — but the writer refused a scaffold only on the
    `<dotted>/__init__.py` fallback. So emptying `tortoise/gone.py` IN PLACE and archiving it
    froze `\"\"\"Scaffolding.\"\"\"` with `archived_from = tortoise/gone.py`, `shrink_violations`
    then credited the row on its EXISTENCE, and the whole run went green with the module's body
    in no file under `archive/` — the false PASS the package-shaped row above closed one branch
    over. The rule is about the RESOLVED SOURCE, not about which fallback found it.

    Mutation: drop the `not rel.endswith(".pyi")` body test -> RED (the writer accepts the
    docstring and writes a record holding no code).
    """
    (tree / "tortoise" / "gone.py").write_text(
        "def f1():\n    return 1\n\ndef f2():\n    return 2\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/gone.py")
    # The body is removed IN PLACE: the file survives, which is how this shape slips past any
    # reader that asks whether a path exists.
    (tree / "tortoise" / "gone.py").write_text('"""Scaffolding."""\n', encoding="utf-8")
    _commit(tree, ".")

    with pytest.raises(arch.ArchiveRefused, match=r"no body of its own"):
        arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    assert not list((tree / "archive").rglob("*.py")), (
        "the refusal must not leave a record behind: a row frozen from a docstring is the false "
        "record this guard exists to prevent"
    )
    assert any(
        p.startswith("tortoise.gone: MODULE") and "REMOVED" in p
        for p in arch.shrink_violations(tree, base_ref="HEAD~1")
    ), "and the removal stays a red — declining to write the record must not read as written"


def test_a_module_reduced_to_a_NO_OP_statement_is_not_a_body(tree: Path) -> None:
    """`pass` / `...` are not code a caller can reach — in BOTH directions (round-16 review, P2).

    Measured before the fix: `_init_carries_a_body("pass\\n")` -> True. Two consequences, and
    this row pins both:
      (a) the WRITER accepted `pass` as a module's source of record (`archived_from =
          tortoise/gone.py`), and `shrink_violations` then credited the row on its EXISTENCE —
          every arm green while the deleted body was in no file under `archive/` (the same false
          PASS the docstring row above closed, one shape over);
      (b) the mirror: a `pass`-only `__init__.py` counted as CONTENT, so deleting a scaffolding
          package went RED — a correct-looking red that blocked a legitimate cleanup.
    One predicate answers both, which is why the fix is in `_init_carries_a_body` and not at the
    call site. Only TOP-LEVEL bare no-ops are dropped: a `def`/`class`/`if` whose body is `pass`
    is still a statement, and a `.pyi` stub's `def f() -> int: ...` is a FunctionDef.

    Mutation: drop the `ast.Pass`/Ellipsis filter -> RED (both halves).
    """
    # (a) the writer must refuse a no-op body, and the removal must stay a RED without a record.
    (tree / "tortoise" / "gone.py").write_text("def f1():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    (tree / "tortoise" / "gone.py").write_text("pass\n", encoding="utf-8")
    _commit(tree, ".")
    with pytest.raises(arch.ArchiveRefused, match=r"no body of its own"):
        arch.archive_symbol(arch.MODULE, "tortoise.gone", "b1", "r", root=tree)
    assert any(
        p.startswith("tortoise.gone: MODULE") and "REMOVED" in p
        for p in arch.shrink_violations(tree, base_ref="HEAD~1")
    ), "a body reduced to `pass` is a removal, and declining to write the record must not green it"

    # (b) …and a package whose `__init__.py` is ONLY `pass` is scaffolding: deleting it removes
    # nothing, because the body lives in its siblings.
    (tree / "tortoise" / "pkg").mkdir()
    (tree / "tortoise" / "pkg" / "__init__.py").write_text("pass\n", encoding="utf-8")
    (tree / "tortoise" / "pkg" / "sub.py").write_text("Y = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/pkg")
    _commit_drop(tree, "tortoise/pkg/__init__.py")
    assert not [
        p for p in arch.shrink_violations(tree, base_ref="HEAD~1") if "tortoise.pkg" in p
    ], (
        "a `pass`-only `__init__.py` carries no statement of its own, so deleting it removes "
        "nothing and must not red the scaffolding cleanup"
    )


@pytest.mark.parametrize(
    "spelling", ["pass", "...", "Ellipsis", "None", "0", "NotImplemented", "1 + 1"]
)
def test_every_SPELLING_of_a_bare_no_op_is_not_a_body(tree: Path, spelling: str) -> None:
    """The CLASS of no-op, not two of its spellings (round-17 review, P1 — a false PASS).

    Round-16 dropped `ast.Pass` and the `...` CONSTANT only, so the SAME no-op spelled `Ellipsis`
    (a Name bound to that object) or `None` / `0` / `NotImplemented` (bare constants) still
    answered "carries a body": the writer froze it as the module's source of record and
    `check --base-ref` printed `OK — archive consistent` while the base's four functions were in
    NO file under `archive/` (measured per spelling: `shrink_violations -> 0`). An enumeration of
    spellings was the defect; the fix asks the language rule instead, so this row tests the rule
    rather than the list it used to be.

    Mutation: narrow the filter back to `Constant(value is Ellipsis)` -> RED for every other
    spelling here.
    """
    (tree / "tortoise" / "gone.py").write_text("def f1():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    (tree / "tortoise" / "gone.py").write_text(f"{spelling}\n", encoding="utf-8")
    _commit(tree, ".")
    assert not arch._init_carries_a_body(f"{spelling}\n")
    with pytest.raises(arch.ArchiveRefused, match=r"no body of its own"):
        arch.archive_symbol(arch.MODULE, "tortoise.gone", "b1", "r", root=tree)
    assert any(
        p.startswith("tortoise.gone: MODULE") and "REMOVED" in p
        for p in arch.shrink_violations(tree, base_ref="HEAD~1")
    ), f"a body reduced to `{spelling}` is a removal, and no record may green it"


def test_deleting_a_SCAFFOLDING_py_module_is_not_a_removal(tree: Path) -> None:
    """Arm 1 grades a DELETION by content too, not only an in-place emptying (round-17, P2).

    Measured before the fix: `_modules_with_a_body` content-graded ONLY `__init__.py`, so a base
    `tortoise/doc.py` that was a docstring alone (or 0 bytes) was "carried", its deletion was
    `MODULE REMOVED` — and the remedy the violation printed (`archive module tortoise.doc`) is
    REFUSED by this very tool for a body-less source: the red had NO route (CI passes no
    `--allow-archive-shrink`). Nothing left the product, so the arm must cancel exactly as it
    does for an emptied package.

    The `.pyi`-only case is deliberately NOT cancelled: a stub IS the module's code when nothing
    else carries it (pinned by `test_a_tortoise_archive_stub_module_is_reported`).

    Mutation: drop the base-body test in arm 1 -> RED here.
    """
    (tree / "tortoise" / "doc.py").write_text('"""Placeholder module."""\n', encoding="utf-8")
    (tree / "tortoise" / "empty.py").write_text("", encoding="utf-8")
    _commit(tree, "tortoise/doc.py", "tortoise/empty.py")
    _commit_drop(tree, "tortoise/doc.py")
    _commit_drop(tree, "tortoise/empty.py")

    assert arch.shrink_violations(tree, base_ref="HEAD~2") == [], (
        "a docstring-only or 0-byte module carries nothing, so deleting it removes nothing"
    )


def test_deleting_a_module_whose_SIBLING_carrier_held_the_code_is_a_removal(tree: Path) -> None:
    """The scaffolding skip asks the module, not one of its carriers (round-23 review, F1).

    `module_name` collapses `tortoise/projection.py` and `tortoise/projection/__init__.py` onto
    the SAME dotted name, so `candidates` holds BOTH paths — and `sorted()` puts the `.py` first
    (`'.'` = 46 < `'/'` = 47). The first cut of the skip asked that one carrier, found a
    docstring, and cancelled the MODULE: deleting both graded green with the `__init__.py`'s
    functions gone, no record, no waiver and no diagnostic — a REMOVAL turned into a PASS by the
    guard whose whole job is to prevent exactly that. Both were `MODULE REMOVED` before the skip
    existed (measured by review on this shape); a body-less leftover `.py` is a dead shim, not
    proof the base carried nothing.

    Mutation: cancel on the `next(...)`-picked runtime carrier (`if runtime_carrier is not None
    and not _init_carries_a_body(...)`) -> RED here (green, with the code in no file).
    """
    (tree / "tortoise" / "projection").mkdir()
    (tree / "tortoise" / "projection.py").write_text(
        '"""Placeholder shim."""\n', encoding="utf-8"
    )
    (tree / "tortoise" / "projection" / "__init__.py").write_text(
        "def real():\n    return 1\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/projection.py", "tortoise/projection/__init__.py")
    _commit_drop(tree, "tortoise/projection.py")
    _commit_drop(tree, "tortoise/projection/__init__.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~2")
    assert any(p.startswith("tortoise.projection: MODULE REMOVED") for p in problems), (
        "the base's `__init__.py` carried the module's code, so its deletion is a removal however "
        f"empty the `.py` beside it was: {problems}"
    )


def test_a_docstring_py_does_not_cancel_the_STUB_carrier_beside_it(tree: Path) -> None:
    """A `.pyi` sibling IS the module's code — the skip must not cancel its deletion.

    The block's own rule is "a stub IS a module's code when nothing else carries it", and here
    nothing else carries it: the `x.py` beside the stub holds a docstring, not statements. Asking
    ONE carrier made `x.py` win and cancelled the module, so the deleted stub — importable
    surface by this file's recorded rule (`live_return`'s DECLINED note, `_package_modules`'
    `*.py*`) — left the product with nothing recording it.

    Mutation: cancel on the `next(...)`-picked runtime carrier -> RED here.
    """
    (tree / "tortoise" / "x.py").write_text('"""Placeholder."""\n', encoding="utf-8")
    (tree / "tortoise" / "x.pyi").write_text("def api() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py", "tortoise/x.pyi")
    _commit_drop(tree, "tortoise/x.py")
    _commit_drop(tree, "tortoise/x.pyi")

    problems = arch.shrink_violations(tree, base_ref="HEAD~2")
    assert any(p.startswith("tortoise.x: MODULE REMOVED") for p in problems), (
        "the stub was the module's only code, and it is gone: "
        f"{problems}"
    )


def test_a_waiver_over_a_deleted_SCAFFOLDING_module_does_not_claim_the_removal_is_void(
    tree: Path,
) -> None:
    """The scaffolding skip runs BEFORE the waiver is consumed — so the diagnosis must be true.

    Arm 1 `continue`s on a body-less base before it can `used.add(key)`, so a waiver over a
    deleted scaffolding module reaches the unused-waiver loop with no entry and no removal — and
    the fallback told the operator "NO `module` entry records it, so the removal is UNRECORDED",
    about a deletion that removed no code at all (round-23 review, F2). The verdict (the waiver
    waives nothing) was right; the stated fact was false, and it pointed at a record that had no
    business existing.

    Mutation: drop the `symbol not in base_body_modules` branch -> RED here ("the removal is
    UNRECORDED" comes back), while `test_deleting_a_SCAFFOLDING_py_module_is_not_a_removal`
    stays green — that row passes no waiver, so it cannot see this.
    """
    (tree / "tortoise" / "doc.py").write_text('"""Placeholder module."""\n', encoding="utf-8")
    _commit(tree, "tortoise/doc.py")
    _commit_drop(tree, "tortoise/doc.py")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.doc"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"an unused waiver is still a false record and must be reported: {problems}"
    assert "UNRECORDED" not in waived[0], (
        "nothing was removed, so no record is missing — that sentence sends the operator after a "
        f"`module` entry for a deletion that removed no code: {waived[0]}"
    )
    assert "NOTHING was removed" in waived[0], waived[0]


def test_a_waiver_over_a_BODY_LESS_py_base_does_not_send_the_reader_to_history(
    tree: Path,
) -> None:
    """Two carriers, one symbol: the writer freezes the code, the diagnosis names what is true.

    Base holds `tortoise/stubby.py` = a docstring AND `tortoise/stubby.pyi` = the stub. Both
    halves of this row were wrong before round 23:

    · `_module_source` resolved `<dotted>.py` by PATH EXISTENCE, so the body-less `.py` won and
      the writer REFUSED ("carries no body of its own") with a route — `git checkout <base> --
      tortoise/stubby.py` — that restores the same body-less file and is refused again; the
      `.pyi` fallback behind it was unreachable. A red with no route, one carrier over from the
      package fallback that fallback exists for.
    · `_stub_waiver_why` split on `endswith(".py")`, so a base whose only `.py` carried nothing
      took the had-runtime arm and printed "restore the source from a commit that still holds
      it" over a module with no body-carrying `.py` in ANY commit.

    Mutations: resolve the source by path existence alone -> RED at the `archived_from` assert
    (`ArchiveRefused`); split `_stub_waiver_why` on the suffix -> RED at the "FROZE ONLY A STUB"
    assert below.
    """
    (tree / "tortoise" / "stubby.py").write_text(
        '"""Placeholder shim."""\n', encoding="utf-8"
    )
    (tree / "tortoise" / "stubby.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py", "tortoise/stubby.pyi")

    entry = arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review F1", root=tree)
    assert entry["archived_from"] == "tortoise/stubby.pyi", (
        "the `.py` beside the stub carries no statements, so the stub is the module's only code "
        f"— freezing the shim would record scaffolding as the source of record: {entry}"
    )
    _commit_drop(tree, "tortoise/stubby.py")
    _commit_drop(tree, "tortoise/stubby.pyi")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~2", allowed=["module:tortoise.stubby"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, f"the unconsumed waiver must still be reported: {problems}"
    assert "FROZE ONLY A STUB" not in waived[0], (
        "no commit holds a body-carrying `.py` for this module, so there is no source to restore "
        f"and no stub frozen over a body: {waived[0]}"
    )
    assert "restore the source from a commit that still holds it" not in waived[0], waived[0]
    assert "no runtime source to record" in waived[0], waived[0]
    assert "only as scaffolding" in waived[0], (
        f"the base's `.py` is what was found, and it must be described as what it is: {waived[0]}"
    )


def test_a_row_that_froze_the_STUB_beside_a_SCAFFOLD_py_is_a_RECORD(tree: Path) -> None:
    """The mirror of the fail-open: a CORRECT stub row must not be redded (round-23 residual).

    Same carrier set as the row above — base `stubby.py` = a docstring, `stubby.pyi` = the stub —
    but taken to the end of the writer's own route: freeze the stub, then delete BOTH files. The
    module is now `base - head`, so it lands in arm 1, and arm 1's stub check asked
    `next(non-.pyi carrier)` — the SUFFIX question, one carrier over from the one this branch
    just fixed. It answered `tortoise/stubby.py`, so the row that records the only code the
    module ever had was reported as "THE RECORD FROZE A STUB … carried the module's statements in
    `tortoise/stubby.py`" — a false violation AND a false stated fact about the same file (the
    statements were in the `.pyi`), which is why the residual is worth a row rather than a note.

    Mutation: `runtime_carrier = next((p for p in candidates if not p.endswith(".pyi")), None)`
    -> RED here (the false `THE RECORD FROZE A STUB` returns).
    """
    (tree / "tortoise" / "stubby.py").write_text('"""Placeholder shim."""\n', encoding="utf-8")
    (tree / "tortoise" / "stubby.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/stubby.py", "tortoise/stubby.pyi")

    entry = arch.archive_symbol(arch.MODULE, "tortoise.stubby", "batch-a", "review", root=tree)
    assert entry["archived_from"] == "tortoise/stubby.pyi", entry
    _commit(tree, ".")
    _commit_drop(tree, "tortoise/stubby.py")
    _commit_drop(tree, "tortoise/stubby.pyi")

    problems = arch.shrink_violations(tree, base_ref="HEAD~3")
    assert not [p for p in problems if "THE RECORD FROZE A STUB" in p], (
        "the row froze the module's ONLY code, so it is the record — redding it sends the "
        f"operator to re-freeze what is already frozen: {problems}"
    )
    assert problems == [], problems


def test_a_row_that_froze_the_STUB_over_a_REMOVED_py_BODY_is_not_a_record(tree: Path) -> None:
    """The head is a body-less `.py`, not a stub — and the row still froze the stub (round-23).

    The FAIL-OPEN mirror of the row above, reached through the writer's own route. Base:
    `tortoise/x.py` carries the module's statements and `tortoise/x.pyi` is a stub. Head: `x.py`
    reduced to a docstring, `x.pyi` untouched — arm 1b's `MODULE BODY REMOVED`, correctly.
    Following that violation, `archive module tortoise.x` now walks the module's carrier set
    (round-23 review, F1) and freezes the `.pyi`, because a body-less `.py` cannot be a source of
    record. But the anti-stub guard was gated on `stubbed` — "the HEAD carrier is a `.pyi` and the
    base's was not" — which is FALSE here (the head carrier is the `.py`), so the row's EXISTENCE
    credited the removal: measured `shrink_violations(...) -> []` with base's `real()` in no file
    under `archive/`, the one direction this whole function exists to prevent.

    Mutation: gate the arm-1b stub check on `stubbed` again -> `[]` and this row reds.
    """
    (tree / "tortoise" / "x.py").write_text("def real():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "x.pyi").write_text("def api() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py", "tortoise/x.pyi")
    (tree / "tortoise" / "x.py").write_text('"""Scaffolding."""\n', encoding="utf-8")
    _commit(tree, ".")

    entry = arch.archive_symbol(arch.MODULE, "tortoise.x", "batch-a", "review", root=tree)
    assert entry["archived_from"] == "tortoise/x.pyi", (
        "the writer refuses a body-less `.py` and freezes the carrier that holds code: "
        f"{entry}"
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~2")
    frozen = [p for p in problems if "THE RECORD FROZE A STUB" in p]
    assert frozen, (
        "the base's `real()` was in `tortoise/x.py` and no file under `archive/` holds it, so "
        f"the row that froze the stub must not credit the removal: {problems}"
    )
    assert "tortoise/x.py`" in frozen[0] and "carried the module's statements" in frozen[0], (
        f"the violation must name the carrier the statements were in: {frozen[0]}"
    )
    assert "carries none" in frozen[0], (
        f"the head carrier is a body-less `.py`, not a stub, and the text must say so: {frozen[0]}"
    )


def test_a_MODULE_REMOVED_names_the_carrier_that_HELD_the_code(tree: Path) -> None:
    """Two carriers, one symbol: the route must name the file the statements were in.

    `module_name` collapses `tortoise/projection.py` and `tortoise/projection/__init__.py`, and the
    violation ended with `(the file was {candidates[0]})` — the lexicographically first path, so
    with a scaffolding `.py` beside the body-carrying `__init__.py` it pointed the operator at the
    file that held nothing (and at a `git checkout <base> -- tortoise/projection.py` that restores
    the scaffold). The message is the only guide the operator has here, and a wrong one is the
    class rounds 19-23 removed from the sibling sentences.

    Mutation: `where = candidates[0]` -> RED here (`the file was tortoise/projection.py`).
    """
    (tree / "tortoise" / "projection.py").write_text('"""Dead shim."""\n', encoding="utf-8")
    (tree / "tortoise" / "projection").mkdir()
    (tree / "tortoise" / "projection" / "__init__.py").write_text(
        "def real():\n    return 1\n", encoding="utf-8"
    )
    _commit(tree, ".")
    _commit_drop(tree, "tortoise/projection.py")
    _commit_drop(tree, "tortoise/projection/__init__.py")

    problems = [
        p for p in arch.shrink_violations(tree, base_ref="HEAD~2")
        if p.startswith("tortoise.projection: MODULE REMOVED")
    ]
    assert problems, "a deleted body with a body-carrying sibling carrier is a removal"
    assert "the file was tortoise/projection/__init__.py" in problems[0], problems[0]


def test_a_WHITESPACE_only_reason_is_not_a_reason(tree: Path) -> None:
    """A record's `reason` is what a future reader consults — pressing space is not a reason.

    Round-17 review, P3: raw truthiness at BOTH the writer (`archive_symbol`) and the checker
    (`file_violations`) let `--reason '   '` through, so a row was written carrying
    `"reason": "   "` and graded clean. This is the hole the PR's own pairing guard names for
    `--flag` ("an unexplained removal could be waived by pressing space"); the reason is now
    normalised in place, so the padding cannot survive into the record.

    Mutation: `reason = (reason or "").strip()` -> `reason = reason or ""` -> RED here.
    """
    (tree / "tortoise" / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py")

    with pytest.raises(arch.ArchiveRefused, match="reason"):
        arch.archive_symbol(arch.MODULE, "tortoise.x", "b1", "   ", root=tree)

    # …and the checker must not accept a row that a hand-edit put on disk with padding either:
    # build the row with the real writer (a valid reason) and then corrupt only the reason, so
    # the row reaches the reason arm with a file the checker can actually read.
    entry = _archive_module(tree, "tortoise.x")
    entry["reason"] = "   "
    violations = arch.file_violations([entry], archive_dir=tree / "archive", root=tree)
    assert any("no `reason`" in v for v in violations), (
        f"a whitespace-only reason must not satisfy the reason rule: {violations}"
    )


def test_the_CLI_refuses_to_freeze_a_leftover_scaffold(
    tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The row above is API-level; the OPERATOR sees `main()`'s exit code and stderr.

    The review asked for a CLI-level row, and the arm it pins is a different one: `main()`'s
    `except ArchiveRefused -> print("REFUSED …", stderr); return 2`. That mapping is pinned
    NOWHERE else for the `archive` verb, so a refusal that exited 0 would read as a successful
    archive that wrote nothing — the fail-open direction, on the writer's only refusal path.

    Mutation: `return 0` in `main()`'s `except ArchiveRefused` arm -> RED here (rc is 0 and the
    operator reads success), while every API-level assertion above stays green.
    """
    (tree / "tortoise" / "x.py").write_text("def f39():\n    return 39\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py")
    _commit_drop(tree, "tortoise/x.py")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").write_text(
        '"""Scaffolding."""\n', encoding="utf-8"
    )
    (tree / "tortoise" / "x" / "sub.py").write_text("Y = 1\n", encoding="utf-8")
    _commit(tree, ".")
    monkeypatch.setattr(arch, "ROOT", tree)

    rc = arch.main(
        ["archive_surface.py", "archive", "module", "tortoise.x", "--batch", "b1",
         "--reason", "split"]
    )
    err = capsys.readouterr().err
    assert rc == 2, f"a refusal must not read as a successful archive (exit {rc})"
    assert "REFUSED" in err and "git checkout <base-sha> -- <one of: tortoise/x.py" in err, (
        f"the refusal must name the route that clears the red it came from: {err}"
    )
    assert not (tree / "archive" / "modules").exists(), (
        "and it must write nothing — a row frozen from a scaffold IS the false record"
    )
    assert any(
        p.startswith("tortoise.x: MODULE") and "REMOVED" in p
        for p in arch.shrink_violations(tree, base_ref="HEAD~2")
    ), (
        "and the removal stays a red (arm 1: the module left the product) — declining to write "
        "the record must not read as having written it"
    )


def test_an_archive_entry_dropped_from_the_manifest_is_reported(tree: Path) -> None:
    """#12291: the index of what was removed loses a row, and every gate stays green."""
    _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("tortoise.gone" in p and "REMOVED from archive/MANIFEST.json" in p for p in problems), (
        problems
    )


def test_dropping_the_row_and_its_file_is_STILL_reported(tree: Path) -> None:
    """The tidy deletion — row AND frozen file — is an unexplained drop, not a restore.

    The file is CREATED and then deleted, so this is the scenario the PR body's mutation table
    claims to perform (review F4: it used to be byte-identical to the row-only test, and the
    file never existed, so no mutation could tell the two apart).
    """
    entry = _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    assert (tree / entry["file"]).exists(), "the frozen file must exist to be dropped"
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])
    assert not (tree / entry["file"]).exists(), "the row is not the only thing that must go"
    assert arch.load_manifest(tree / "archive" / "MANIFEST.json")["entries"] == []

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("tortoise.gone" in p and "REMOVED from archive/MANIFEST.json" in p for p in problems), (
        "an entry whose file is gone is already a `manifest_violations` finding in the OTHER "
        f"direction; dropping BOTH is still a record that vanished: {problems}"
    )


def test_a_row_dropped_for_a_RESTORED_module_is_green(tree: Path) -> None:
    """The documented restore path, WITH the shrink arm ON — the false block of review F1.

    `archive/README.md` step 3 deletes the row and the frozen file, because the code came back;
    direction one reds the row if it stays, so dropping it is the only consistent shape. The
    surface-guard job runs this arm on every PR, so a restore that reds here reds the repo's own
    documented procedure with no waiver reachable from CI.
    """
    entry = _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])

    restored = [p for p in arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(modules={"tortoise/gone.py"})
    ) if "tortoise.gone" in p]
    assert restored == [], f"a restore is not a loss: {restored}"

    # NON-VACUITY: the SAME drop with the symbol NOT back is still the loss #12291 is about.
    lost = arch.shrink_violations(tree, base_ref="HEAD~1", surface=_surface())
    assert any("REMOVED from archive/MANIFEST.json" in p for p in lost), lost


def _sdk_entry_with_a_real_body(tree: Path, name: str = "old_method") -> dict:
    """An `sdk-method` row whose recorded block is the body that actually lives in `sdk.py`.

    ⛔ The earlier fixture built the row with `_entry(...)`, which records NO blocks and wrote no
    live `sdk.py` at all — so the restore credit was granted by the NAME and the body question
    was unreachable (round-8 review). A restore fixture has to contain the code that came back.
    """
    text = f"class TortoiseSDK:\n    def {name}(self):\n        return 1\n"
    (tree / "tortoise" / "sdk.py").write_text(text, encoding="utf-8")
    _commit(tree, "tortoise/sdk.py")
    segment = arch.expected_block(arch.SDK_METHOD, name, "tortoise/sdk.py", text)
    assert segment is not None, "the fixture must hold the shape the extractor keys"
    row = {
        "path": "tortoise/sdk.py",
        "node": None,
        "sha256": arch._sha256(segment.strip("\n")),
    }
    return dict(
        _entry(arch.SDK_METHOD, name),
        blocks=[row],
        sources=["tortoise/sdk.py"],
    )


def test_a_row_dropped_for_a_RESTORED_sdk_method_is_green(tree: Path) -> None:
    """The same rule for the other two kinds: liveness is per-kind, not module-only.

    And the credit is the BODY, not the name — `test_a_row_dropped_for_a_STUBBED_sdk_method`
    is the negative control (round-8 review: the body check was unreachable for `sdk-method`
    and `mcp-tool`, because `live_return` returns a SYMBOL for those kinds and the old code
    tried to read a file named after it).
    """
    entry = _sdk_entry_with_a_real_body(tree)
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text("def old_method():\n    return 1\n", encoding="utf-8")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])

    assert arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(methods={"old_method"})
    ) == [], "a verbatim restore of the recorded body IS the restore"
    assert arch.shrink_violations(tree, base_ref="HEAD~1", surface=_surface()), (
        "without the method live again the row drop is unrecorded code loss"
    )


def test_a_row_dropped_for_a_STUBBED_sdk_method_is_a_loss(tree: Path) -> None:
    """A live NAME whose body is not the recorded one is a STUB, not the code coming back.

    Measured in round-8 review: the credit was name-only for `sdk-method` / `mcp-tool` (the
    carrier `live_return` returns is a SYMBOL, so the body check read a file named `old_method`
    and every one of those rows fell into "cannot tell"), so dropping the row and the frozen
    file while leaving a three-line stub destroyed the record with every arm green.
    Mutation: make `_live_body_is_the_recorded_one` return None instead of False for an
    unreadable path -> RED here.
    """
    entry = _sdk_entry_with_a_real_body(tree)
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text("def old_method():\n    return 1\n", encoding="utf-8")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    subprocess.run(["git", "rm", "-q", entry["file"]], cwd=tree, check=True)
    (tree / "tortoise" / "sdk.py").write_text(
        "class TortoiseSDK:\n    def old_method(self):  # re-added as a stub\n        ...\n",
        encoding="utf-8",
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(methods={"old_method"})
    )
    assert any("REMOVED from archive/MANIFEST.json" in p for p in problems), (
        "the name is back and the BODY is not — the frozen record is the only copy of what was "
        f"removed: {problems}"
    )


def test_a_MULTI_block_restore_needs_EVERY_block_back(tree: Path) -> None:
    """A two-block row restored verbatim in ONE file is not a restore (round-8 review).

    Only `rows[0]` was compared, and all four committed `mcp-tool` rows are two-block
    (`tool_registry.py` `ToolDefinition` + `mcp_server.py` handler). Measured: registry block
    restored verbatim, the handler nowhere in the tree, `_live_body_is_the_recorded_one: True`.
    Mutation: `for row in rows` -> `for row in rows[:1]` -> RED here.
    """
    registry_text = 'TOOL = ToolDefinition(name="old_tool", description="x")\n'
    handler = "def old_tool():\n    return 1"
    (tree / "tortoise" / "tool_registry.py").write_text(registry_text, encoding="utf-8")
    _commit(tree, "tortoise/tool_registry.py")
    registry_segment = arch.expected_block(
        arch.MCP_TOOL, "old_tool", "tortoise/tool_registry.py", registry_text,
        "ToolDefinition:old_tool",
    )
    assert registry_segment is not None, "the fixture must hold the shape the extractor keys"
    rows = [
        {"path": "tortoise/tool_registry.py", "node": "ToolDefinition:old_tool",
         "sha256": arch._sha256(registry_segment.strip("\n"))},
        {"path": "tortoise/mcp_server.py", "node": "FunctionDef:old_tool",
         "sha256": arch._sha256(handler)},
    ]
    entry = dict(
        _entry(arch.MCP_TOOL, "old_tool"),
        blocks=rows,
        sources=["tortoise/tool_registry.py", "tortoise/mcp_server.py"],
    )
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text("# frozen\n", encoding="utf-8")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(tools={"old_tool"})
    )
    assert any("REMOVED from archive/MANIFEST.json" in p for p in problems), (
        "the handler block is not in the tree at all, so this is not a restore: " f"{problems}"
    )


def test_a_restore_whose_recorded_FILE_is_gone_is_not_a_restore(tree: Path) -> None:
    """A row whose own source file is not in the tree is not back (round-8 review).

    The credit branch reads each row's `path`; an absent file is `False`, not "cannot tell" —
    `None` there was credit, and for `sdk-method` / `mcp-tool` it was ALWAYS `None` because
    `live_return` returns a SYMBOL for those kinds, so the body check was unreachable for 110
    of the archive's 111 entries.
    Mutation: `if text is None: return None` -> RED here.
    """
    entry = dict(
        _entry(arch.SDK_METHOD, "old_method"),
        blocks=[{"path": "tortoise/gone_sdk.py", "node": None, "sha256": "0" * 64}],
        sources=["tortoise/gone_sdk.py"],
    )
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text("# frozen\n", encoding="utf-8")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(methods={"old_method"})
    )
    assert any("REMOVED from archive/MANIFEST.json" in p for p in problems), (
        "the recorded source file is not in the tree, so nothing came back: " f"{problems}"
    )


def test_a_module_left_behind_as_an_EMPTY_file_is_not_a_return(tree: Path) -> None:
    """Direction one must agree with arm 1b: a leftover emptied file is not the code (round-8).

    Arm 1b calls an emptied module a removal; `live_return` used to credit it as "the module
    came back", so carrying out arm 1b's own printed remedy (archive it, delete the body again)
    RED the sibling arm on the leftover file — a red with no CI-passable route.
    Mutation: drop the `root is not None` content check in `live_return` -> RED here.
    """
    (tree / "tortoise" / "splitmod.py").write_text("def body():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/splitmod.py")
    (tree / "tortoise" / "splitmod.py").write_text("", encoding="utf-8")
    _commit(tree, "tortoise/splitmod.py")
    entry = dict(_entry(arch.MODULE, "tortoise.splitmod"), blocks=[], sources=[])

    # The body-carrier set production would build for THIS tree: `_modules_with_a_body` filters
    # only a scaffolding `__init__.py`, so a plain empty `.py` is still a CARRIER. Omitting the
    # name here (an empty set, or the misspelt key that made this argument inert until review 10)
    # short-circuits on the carriers guard ABOVE and the test could then no longer pin the on-disk
    # content check its docstring names.
    surface = _surface(
        modules={"tortoise/splitmod.py"}, modules_with_a_body={"tortoise.splitmod"}
    )
    assert arch.live_return(entry, surface, tree) is None, (
        "an emptied leftover file is not the module coming back"
    )
    assert arch.surface_violations([entry], surface, tree) == [], (
        "and direction one must not red the file arm 1b's remedy leaves behind"
    )


def test_the_body_carrier_set_decides_when_there_is_no_tree_to_read() -> None:
    """`root=None` is the ONE place `modules_with_a_body` decides the answer (round-11 review).

    Both other consumers pass `root`, and there `_init_carries_a_body` re-derives the answer from
    the file, so the carriers guard never changes it — which meant nothing pinned the KEY the
    fixture writes, and round-10's defect (the fixture writing `modules_with_body`, a key no
    production code reads) could return silently with every other test still green. With no tree
    to read, a scaffolding-only carrier is "not the module coming back" and a body-carrying one
    is.

    Mutation: revert `_surface`'s key to `modules_with_body` -> the FIRST assertion REDs, because
    `carriers` becomes `None` and `live_return` credits the scaffolding path.
    """
    entry = dict(_entry(arch.MODULE, "tortoise.splitmod"), blocks=[], sources=[])
    scaffolding = {"tortoise/splitmod/__init__.py"}

    assert (
        arch.live_return(entry, _surface(modules=scaffolding, modules_with_a_body=set()), None)
        is None
    ), "the surface says the name resolves and carries no body, so nothing came back"

    assert (
        arch.live_return(
            entry, _surface(modules=scaffolding, modules_with_a_body={"tortoise.splitmod"}), None
        )
        == "tortoise/splitmod/__init__.py"
    ), "a carrier the surface names IS the module coming back"


def test_the_restore_credit_needs_a_KNOWN_surface(tree: Path) -> None:
    """`surface=None` means liveness UNKNOWN, and that must fail CLOSED (still a violation).

    A caller that has not read the surface can never turn a loss into a green; only `check`
    (and a test that supplies one) earns the restore credit.
    """
    entry = _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])

    assert arch.shrink_violations(tree, base_ref="HEAD~1"), (
        "an unknown surface was read as 'the symbol is live' — that is the fail-open shape"
    )


def test_check_hands_the_live_surface_to_the_shrink_arm(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CALL SITE, not just the helper: `check` must pass the surface it already read.

    Deleting `surface=surface` from `check`'s call would red every restore in CI while every
    helper-level test above stayed green — the call-site shape this section exists for.
    """
    entry = _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])
    monkeypatch.setattr(
        arch, "live_surface", lambda root=None: _surface(modules={"tortoise/gone.py"})
    )
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])

    problems = arch.check(tree, base_ref="HEAD~1")
    assert not any("tortoise.gone" in p for p in problems), (
        "check() did not hand the live surface to the shrink arm, so a documented restore reds: "
        f"{problems}"
    )


def test_a_rewritten_manifest_entry_is_reported(tree: Path) -> None:
    """A recorded entry edited in place leaves the same faithless record as a deleted one."""
    entry = _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    rewired = dict(entry, archived_at="b" * 40)
    arch.save_manifest({"entries": [rewired]}, tree / "archive" / "MANIFEST.json")
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p and "archived_at" in p for p in problems), problems


def test_a_VERIFIED_reanchor_is_not_a_rewrite(tree: Path) -> None:
    """A correction the SIBLING arm requires must not be refused here (review P2, #14039).

    `verify-anchors` — the step that runs immediately BEFORE this arm in the same
    `surface-guard` job — requires an entry to cite a commit that still HOLDS the frozen body,
    so when `main` edits an archived file the anchor must move. The only route this arm printed
    was `--allow-archive-shrink`, a local-shell waiver the workflow deliberately never passes:
    gate against gate, with no way through a PR. The correction is accepted ONLY on the evidence
    the anchor arm already uses — the frozen file still matches the record AND the new anchor
    holds each recorded body byte-for-byte. Mutation: delete the `_reanchor_is_verified` call ->
    RED here; drop its `_anchor_violations` loop -> the negative control below goes green.
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    # A LATER commit that still holds this module's body unchanged — a real, readable anchor.
    (tree / "tortoise" / "other.py").write_text("Y = 2\n", encoding="utf-8")
    _commit(tree, "tortoise/other.py")
    newer = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    assert newer != entry["archived_at"], "the fixture must actually move the anchor"
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=newer)]}, tree / "archive" / "MANIFEST.json"
    )
    _commit(tree, ".")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "a re-anchor whose new commit provably holds the recorded body is the correction "
        "`verify-anchors` asks for, not a faithless edit"
    )


def test_a_reanchor_to_a_commit_that_does_not_hold_the_body_is_still_reported(
    tree: Path,
) -> None:
    """The negative control: the evidence is the BODY, not the presence of a real sha.

    Without this, `_reanchor_is_verified` would accept any rewrite that merely names a commit
    this clone happens to hold — which is the forged-anchor shape #12291 exists to catch.

    ⚠️ WHAT ACTUALLY ANSWERS THIS FIXTURE (measured, round-12 mutation pass): it anchors at an
    ANCESTOR (`before`), so the ROLLBACK guard refuses before the body read is reached — deleting
    the per-row `_anchor_violations` body read alone leaves this row GREEN. The row that
    discriminates the BODY guard is
    `test_a_reanchor_to_a_LATER_commit_that_CHANGED_the_body_is_still_reported`, which moves the
    anchor FORWARD on the default branch and changes the body, so rollback and the
    default-branch check both pass and only the body evidence can refuse.
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    before = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=before)]}, tree / "archive" / "MANIFEST.json"
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p and "archived_at" in p for p in problems), (
        "a commit that exists but does not carry the frozen body is not a re-anchor: "
        f"{problems}"
    )


def test_a_reanchor_to_a_LATER_commit_that_CHANGED_the_body_is_still_reported(tree: Path) -> None:
    """The BODY guard on its own — the existing row is not discriminating about it (round-12).

    `test_a_reanchor_to_a_commit_that_does_not_hold_the_body_is_still_reported` anchors at an
    ANCESTOR, so the ROLLBACK guard answers it as well. Measured in the round-12 mutation pass:
    removing the per-row `_anchor_violations` body read alone left that row GREEN, and removing
    the rollback check alone left it GREEN too — only all three together reddened it. So it never
    discriminated the guard it names.

    This fixture is built the other way round: the anchor MOVES FORWARD on the default branch and
    simply does not hold the frozen body, so rollback and default-branch both pass and the body
    evidence is the only guard left that can refuse.

    Mutation: delete the per-row `_anchor_violations(...)` call -> RED here, and only here.
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    # A LATER commit on the default branch edits the file: a descendant of the archived revision
    # (so no rollback) and on the branch (so a valid citation) — but it no longer holds the body.
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 2\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    later = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    assert later != entry["archived_at"], (
        "the fixture needs a FORWARD anchor, or the rollback guard answers instead of the body"
    )
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=later)]}, tree / "archive" / "MANIFEST.json"
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p and "archived_at" in p for p in problems), (
        "a FORWARD anchor that does not hold the frozen body is not a re-anchor — the evidence "
        f"is the BODY, not the direction of the sha: {problems}"
    )


def test_a_reanchor_cannot_DELETE_a_frozen_block(tree: Path) -> None:
    """The escape is a RE-ANCHOR, not a licence to shrink the evidence (round-6 review).

    The first version was handed only the NEW entry, so "the new record is internally consistent
    with the new file" was the whole question — and a rewrite that DELETED a frozen block while
    deleting its row from the manifest passed EVERY arm: the row stays, its evidence shrinks, and
    the removed code now exists nowhere, which is the loss #12291 was filed for. A re-anchor
    RE-HASHES bodies; it does not delete them, so the base entry's block count and block keys
    are required to match.
    Mutation: drop the BASE-entry comparisons (block count / block keys / `sources`) -> RED
    here. ⚠️ THE THREE ARE MUTUALLY SUBSUMING, and the round-12 mutation pass measured it:
    deleting the block-COUNT comparison alone leaves this row GREEN, because the block-KEY
    multiset then refuses the same fixture, and vice versa. Only removing all three (or the
    `sources` check on the rewrite that edits `sources`) reds it. So this row pins the
    PROPERTY, not one comparison — an attribution table cannot name a single load-bearing
    mutant for it, and should not pretend to. A per-guard mutant is meaningful only for a guard
    something else does not already answer.
    """
    entry = arch.archive_symbol(arch.MCP_TOOL, "tortoise_search", "batch-a", "r", root=tree)
    _commit(tree, ".")
    assert len(entry["blocks"]) >= 2, "the fixture needs a MULTI-block entry to be able to shrink"
    kept = entry["blocks"][:1]
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        entry["archived_from"], entry["archived_at"],
        [(row["path"], arch.frozen_blocks((tree / entry["file"]).read_text(encoding="utf-8"))[i][1], row["node"])
         for i, row in enumerate(kept)],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    shrunk = dict(entry, blocks=frozen, sources=[row["path"] for row in frozen])
    arch.save_manifest({"entries": [shrunk]}, tree / "archive" / "MANIFEST.json")
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p and "blocks" in p for p in problems), (
        "dropping a frozen block while keeping the row is exactly the faithless record this arm "
        f"grades — the code it held now exists nowhere: {problems}"
    )


def test_a_reanchor_to_an_ANCESTOR_commit_is_a_ROLLBACK(tree: Path) -> None:
    """A readable anchor is not enough: the evidence must not roll BACKWARDS (round-7 review).

    Re-rendering the frozen body from an OLDER revision of the same symbol and re-pointing
    `archived_at` at it satisfies every byte check — the file matches the record, and the commit
    really holds that body — so the escape accepted it and the record silently named an earlier,
    weaker revision. That is the TUF rollback shape this module cites at its top. A side-branch
    anchor is refused for the same reason `anchor_sha` refuses to WRITE one.
    Mutation: drop the `merge-base --is-ancestor previous sha` check -> RED here.
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    older = None
    _commit(tree, "tortoise/gone.py")
    older = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    (tree / "tortoise" / "gone.py").write_text(
        "def secret():\n    return 2\n\ndef more():\n    return 3\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    assert entry["archived_at"] != older
    # ROLL BACK: the frozen body from the OLDER revision, anchored at the OLDER commit.
    old_body = arch._git_blob(tree, older, "tortoise/gone.py")
    assert old_body is not None
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        entry["archived_from"], older, [("tortoise/gone.py", old_body, None)],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=older, blocks=frozen, sources=["tortoise/gone.py"])]},
        tree / "archive" / "MANIFEST.json",
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p for p in problems), (
        "a re-anchor to an ANCESTOR of the recorded anchor is a ROLLBACK, not a refresh — the "
        f"record would name an earlier revision than the one it already trusted: {problems}"
    )


def test_a_reanchor_accepts_sources_in_ANOTHER_order(tree: Path) -> None:
    """`sources` is compared as a MULTISET, because a committed row carries a different order.

    The writer derives `sources` in block order, but `archive/MANIFEST.json`'s `tortoise_analyze`
    row lists `[mcp_server.py, tool_registry.py]` over blocks in the opposite order — so an
    order-sensitive comparison refused the ONE legitimate re-anchor for that row, with a remedy
    ("restore the recorded field(s)") that `verify-anchors` reds: a false block with no route, for
    1 of 111 rows (round-9 review). Order carries no claim; every block tuple is still bound to
    its own path by the per-row comparison.
    Mutation: compare `entry["sources"]` to the derived list with order-sensitive `!=` -> RED.
    """
    (tree / "tortoise" / "tool_registry.py").write_text(
        'TOOL = ToolDefinition(name="old_tool", description="x")\n', encoding="utf-8"
    )
    (tree / "tortoise" / "mcp_server.py").write_text(
        "def old_tool():\n    return 1\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/tool_registry.py", "tortoise/mcp_server.py")
    entry = arch.archive_symbol(arch.MCP_TOOL, "old_tool", "batch-a", "r", root=tree)
    _commit(tree, ".")
    assert len(entry["blocks"]) >= 2, "the fixture needs a MULTI-block row to reorder"
    (tree / "tortoise" / "note.py").write_text("Z = 5\n", encoding="utf-8")
    _commit(tree, "tortoise/note.py")
    newer = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    reversed_sources = list(reversed([str(row["path"]) for row in entry["blocks"]]))
    assert reversed_sources != [str(row["path"]) for row in entry["blocks"]]
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=newer, sources=reversed_sources)]},
        tree / "archive" / "MANIFEST.json",
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert not [p for p in problems if "REWRITTEN" in p], (
        "a faithful re-anchor whose `sources` merely lists the same paths in another order is "
        f"not a rewrite — and the remedy for calling it one cannot be carried out: {problems}"
    )


def test_a_reanchor_off_the_default_branch_is_a_violation(tree: Path) -> None:
    """The new anchor must be a CITATION, i.e. on the default branch (round-7 review).

    `anchor_unverified` already refuses to read such an entry; accepting it here made the two
    arms disagree about the same record, and a squash merge orphans a side-branch anchor.
    Mutation: drop the `--is-ancestor sha <default ref>` check -> RED here.
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    # A real commit that holds the same body, but on a branch that is not `main`.
    arch._git(tree, "checkout", "-q", "-b", "side")
    (tree / "tortoise" / "side_note.py").write_text("Z = 9\n", encoding="utf-8")
    _commit(tree, "tortoise/side_note.py")
    side = arch._git(tree, "rev-parse", "HEAD")[1].strip()
    arch._git(tree, "checkout", "-q", "main")
    body = "def secret():\n    return 1"
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        entry["archived_from"], side, [("tortoise/gone.py", body, None)],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    arch.save_manifest(
        {"entries": [dict(entry, archived_at=side, blocks=frozen, sources=["tortoise/gone.py"])]},
        tree / "archive" / "MANIFEST.json",
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p for p in problems), (
        "an anchor that is not an ancestor of the default branch is not a citation: "
        f"{problems}"
    )


def test_a_reanchor_cannot_REWRITE_archived_from(tree: Path) -> None:
    """`archived_from` is a CLAIM_FIELD bound to nothing else on the rewrite path (round-7).

    `_anchor_violations` reads the block rows' `path`, and `file_violations` only makes the
    FILE's metadata agree with the manifest — so a consistent lie passed both (measured: the
    field rewritten to a file that never held the body, every block byte-identical).
    Mutation: drop the `CLAIM_FIELDS` equality loop -> RED here.
    """
    (tree / "tortoise" / "gone.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    bogus = "tortoise/a/file/that/never/held/it.py"
    text, frozen = arch.render_archive_file(
        entry["kind"], entry["symbol"], entry["batch"], entry["reason"],
        bogus, entry["archived_at"],
        [(row["path"],
          arch.frozen_blocks((tree / entry["file"]).read_text(encoding="utf-8"))[i][1],
          row["node"]) for i, row in enumerate(entry["blocks"])],
    )
    (tree / entry["file"]).write_text(text, encoding="utf-8")
    arch.save_manifest(
        {"entries": [dict(entry, archived_from=bogus, blocks=frozen)]},
        tree / "archive" / "MANIFEST.json",
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("REWRITTEN" in p for p in problems), (
        "a re-anchor moves the anchor and re-hashes bodies — it does not restate where the "
        f"body came from: {problems}"
    )


def test_a_dropped_row_reattached_to_a_STUB_is_not_a_restore(tree: Path) -> None:
    """A live NAME is not a restore: the BODY has to come back (round-7 review).

    Dropping the row and the frozen file earned the restore credit whenever the name was live
    again, so writing a three-line stub at the dotted path destroyed the record of what was
    removed while nothing came back (measured on a 30-function module: every arm green, the
    frozen bytes gone).
    Mutation: drop the `_live_body_is_the_recorded_one` call -> RED here.
    """
    body = "".join(f"def f{i}():\n    return {i}\n" for i in range(30))
    (tree / "tortoise" / "gone.py").write_text(body, encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.gone", "batch-a", "r", root=tree)
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    subprocess.run(["git", "rm", "-q", entry["file"]], cwd=tree, check=True)
    (tree / "tortoise" / "gone.py").write_text("X = 1  # nothing of the module\n", encoding="utf-8")
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(modules={"tortoise/gone.py"})
    )
    assert any("REMOVED from archive/MANIFEST.json" in p for p in problems), (
        "the row's frozen body is gone and a stub stands at the path — that is a loss, not a "
        f"restore: {problems}"
    )


def test_a_pyi_only_module_is_archivable_by_its_dotted_path(tree: Path) -> None:
    """The remedy must reach a shape the gate grades (round-7 review).

    `.pyi` carriers are graded everywhere (`_package_modules`, `module_name`, arm 1b's stub
    rule), so deleting a stub-only module reported `MODULE REMOVED` and printed a route that
    resolved neither `<dotted>.py` nor `<dotted>/__init__.py` — a correct red with no route,
    one suffix over from the package fallback.
    Mutation: drop the `.pyi` fallback in `_module_source` -> RED (ArchiveRefused).
    """
    (tree / "tortoise" / "stubonly.pyi").write_text("def f() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/stubonly.pyi")

    entry = arch.archive_symbol(arch.MODULE, "tortoise.stubonly", "batch-a", "r", root=tree)
    assert entry["archived_from"] == "tortoise/stubonly.pyi"
    assert entry["file"] == "archive/modules/batch-a/tortoise/stubonly.py"
    _commit(tree, ".")
    _commit_drop(tree, "tortoise/stubonly.pyi")
    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "the row the remedy wrote must clear the removal it was printed for"
    )


def test_an_UNREADABLE_head_carrier_counts_as_the_body_gone(tree: Path) -> None:
    """A carrier that exists but cannot be read holds no code (round-7 review).

    A dangling symlink (or a DIRECTORY named `x.py`) passes `exists()` while holding nothing; the
    arm used to skip it as "not evidence", which is the green direction. The opposite polarity
    to `_modules_with_a_body` is deliberate — there a `None` means the BASE commit could not be
    read (a shallow clone), where "counts as content" is the only safe reading.
    """
    (tree / "tortoise" / "sym.py").write_text("def secret():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "dir.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    _commit(tree, "tortoise/sym.py", "tortoise/dir.py")
    subprocess.run(["git", "rm", "-q", "tortoise/sym.py"], cwd=tree, check=True)
    (tree / "tortoise" / "sym.py").symlink_to("/nonexistent/target.py")
    subprocess.run(["git", "rm", "-q", "tortoise/dir.py"], cwd=tree, check=True)
    (tree / "tortoise" / "dir.py").mkdir()
    (tree / "tortoise" / "dir.py" / "inner.txt").write_text("x\n", encoding="utf-8")
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    removed = sorted(p.split(":", 1)[0] for p in problems if "MODULE BODY REMOVED" in p)
    assert removed == ["tortoise.dir", "tortoise.sym"], (
        "an unreadable carrier holds no code: a dangling symlink and a directory named `.py` "
        f"are both the body gone — {problems}"
    )


def test_a_pyi_stub_beside_a_live_module_is_NOT_a_removal(tree: Path) -> None:
    """The `.pyi` preference's real load: a stub ADDED beside the module must stay green.

    A name that carries BOTH `x.py` and `x.pyi` must resolve to the RUNTIME file, or a `.pyi`
    winning the carrier map would turn "add a stub beside the module" into a false
    `MODULE BODY REMOVED`. The map is built from a SORTED path list, so the choice is a property
    of the paths and not of a set's hash order — the determinism is the guard, and this test is
    its non-vacuity (the replacement case is `test_a_module_replaced_by_a_pyi_STUB_is_a_removal`,
    whose guard is the `stubbed` flag).
    """
    (tree / "tortoise" / "both.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/both.py")
    (tree / "tortoise" / "both.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/both.pyi")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "adding a stub beside a live module removes nothing"
    )


def test_a_tombstone_field_added_to_an_entry_is_not_a_rewrite(tree: Path) -> None:
    """Appending to the record is what an append-only ledger DOES; editing it is not."""
    entry = _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    arch.save_manifest(
        {"entries": [dict(entry, restored_in="PR #1")]}, tree / "archive" / "MANIFEST.json"
    )
    _commit(tree, ".")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == []


def test_a_recorded_archive_shrink_is_green(tree: Path) -> None:
    """A DROPPED row is green when the drop's REASON is on the record (the waiver route).

    ⛔ NOT the restore path: a restore needs no waiver at all, because the symbol is live again and
    `live_return` credits it (`test_a_row_dropped_for_a_RESTORED_module_is_green`, the README's
    step-3 procedure). This one is `--allow-archive-shrink-reason <why>` — a drop that is neither a
    restore nor a mistake — so the row pins the waiver route, not the restore route.
    """
    _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit(tree, ".")

    assert arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.gone"]
    ) == []


def test_a_waiver_that_waives_nothing_is_reported(tree: Path) -> None:
    """A false record is worse than no record: it reads as a departure that was checked."""
    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.never_existed"]
    )
    assert any("waives nothing" in p for p in problems), problems


def test_a_MODULE_waiver_that_waives_nothing_does_not_claim_the_base_lacks_it(tree: Path) -> None:
    """Review F2 of PR #14039: the round-3 wording split never reached the MODULE branch.

    `base_entries` is the ARCHIVE's rows, and arm 1's question — "does the base carry this
    module?" — is answered by the PACKAGE TREE, so a module present at BOTH ends printed
    "does not record `tortoise.sdk` (module) at all" while the same run compared it on both
    sides. A verdict that is right and a stated fact that is false is the exact defect the
    archive-row branch was fixed for, and it points the operator at a removal that never
    happened.
    """
    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.sdk"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "does not record" not in waived[0], waived[0]
    assert "still carries it" in waived[0], waived[0]


def test_the_green_line_echoes_the_waiver_reason(
    tree: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Review N1 of PR #14039: the reason is REQUIRED and was then discarded.

    A WHY that is validated and never printed is a one-flag decoration, and this line is the
    only surviving record that the departure was checked (`cut` already prints its reason).
    """
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    _commit_drop(tree, "tortoise/gone.py")
    monkeypatch.setattr(arch, "ROOT", tree)
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])

    rc = arch.main(
        [
            "archive_surface.py",
            "check",
            "--base-ref",
            "HEAD~1",
            "--allow-archive-shrink",
            "module:tortoise.gone",
            "--allow-archive-shrink-reason",
            "deliberate: measured no callers",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "waived: module:tortoise.gone" in out, out
    assert "reason: deliberate: measured no callers" in out, out
    # ⛔ THE POSITIVE SIDE OF THE GREEN LINE (review note of PR #14039, "no red"): every row
    # over this string asserted its ABSENCE (`test_the_shrink_arm_is_OFF_unless_a_base_ref_is
    # _given` checks `check()`, not `main()`'s print), so deleting the `shrink = f" Shrink
    # checked against {label}"` assignment kept the suite green and made a green shrink pass
    # read identically to a run where the arm silently did nothing. Assert the RESOLVED label,
    # not just the prefix — `resolve_base` is the one definition of what was compared.
    assert "Shrink checked against HEAD~1 (merge base " in out, out


def test_a_waiver_must_name_a_real_kind_and_symbol(tree: Path) -> None:
    with pytest.raises(arch.ArchiveRefused, match="not `<kind>:<symbol>`"):
        arch.shrink_violations(tree, base_ref="HEAD", allowed=["tortoise.gone"])


def test_an_unchanged_tree_is_green(tree: Path) -> None:
    """Non-vacuity: an ADDITION over the base must not red — and the arm must COMPARE two commits.

    ⛔ The first cut asserted `shrink_violations(tree, base_ref="HEAD") == []` and
    `shrink_violations(tree) == []` on a fixture whose default branch IS its HEAD: both calls
    compared the tree with ITSELF, so `resolve_base` could answer HEAD unconditionally — the whole
    shrink arm made vacuous — and this row stayed green (round-17 review, P3, measured as
    mutation M10: `resolve_base` -> HEAD left `1 passed, 143 deselected`). The row committed the
    addition on a SIDE branch instead, so the two sides genuinely differ, and PINS that premise
    so the vacuous resolver reds HERE rather than nowhere.
    """
    subprocess.run(
        ["git", "switch", "-q", "-c", "adds-a-module"], cwd=tree, check=True
    )
    (tree / "tortoise" / "added.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/added.py")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip()
    # ⛔ THE PREMISE IS COMPARED AGAINST THE SHA, NOT THE TUPLE. `resolve_base` answers
    # `(sha, label)`, so `resolve_base(...) != head` was a tuple-vs-str comparison that is ALWAYS
    # true — it pinned nothing and let a `resolve_base` answering HEAD stay green (measured on my
    # own first cut of this row, mutation M4). Index the sha.
    assert arch.resolve_base(tree, "main")[0] != head, (
        "premise: the base must NOT be HEAD, or this row asserts a self-comparison and a vacuous "
        "`resolve_base` passes it"
    )
    assert arch.shrink_violations(tree, base_ref="main") == []


def test_the_shrink_arm_is_OFF_unless_a_base_ref_is_given(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check(tmp)` keeps its old meaning: an opt-in arm cannot red the depth-1 `test` job."""
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    # ⛔ The fixture's DEFAULT branch is `main` and HEAD starts ON it, so a removal committed
    # there makes `resolve_base(root, None)` return HEAD — the arm then compares HEAD with
    # ITSELF and returns the same empty list as "the arm did not run". Measured (round-14
    # review, P3-2): with the arm forced ON (`if base_ref is not None:` -> `if True:`) the whole
    # of `tests/test_archive.py` stayed green at 128 passed, so this row could not red the gate
    # it names. Commit the removal on a SIDE branch: the default branch then still holds the
    # file, so a forced-on arm has a real removal to report.
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tree, check=True)
    _commit_drop(tree, "tortoise/gone.py")
    # Pin the premise, so the test cannot silently drift back to the vacuous shape: the ref the
    # arm would use without a base ref must still carry the removed file.
    default_sha = arch.resolve_base(tree, None)[0]
    still_holds = subprocess.run(
        ["git", "cat-file", "-e", f"{default_sha}:tortoise/gone.py"], cwd=tree
    ).returncode
    assert still_holds == 0, (
        "the default branch no longer holds the removed file, so a forced-on arm would report "
        "nothing and this row would be vacuous again"
    )
    assert arch.shrink_violations(tree, base_ref="HEAD~1"), "positive control: the arm fires"
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])
    # `check` reaches the arm only when asked — through the SAME call site `main` uses.
    off = arch.check(tree)
    on = arch.check(tree, base_ref="HEAD~1")
    assert not any("MODULE REMOVED" in problem for problem in off), (
        f"check() ran the base-ref arm without being given a base ref: {off}"
    )
    assert any("MODULE REMOVED" in problem for problem in on), (
        "the call site does not pass the base ref through, so the arm is unreachable from the "
        f"CLI: {on}"
    )


def test_a_BLANK_or_whitespace_archive_reason_is_not_a_reason() -> None:
    """P3-1 of the round-14 review: the pairing guard compared RAW truthiness.

    Measured before the fix: `check --allow-archive-shrink-reason ""` with no waiver exited 0
    silently, and a whitespace reason satisfied the pairing test beside a real waiver — an
    unexplained removal waived by pressing space. `cmd_cut` normalises its own reason with
    `.strip()`; this guard did not.
    Mutation: drop the `.strip()`/`reason_given` normalisation -> the first case exits 0 again.
    """

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "archive_surface.py"), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    blank = run("check", "--allow-archive-shrink-reason", "")
    assert blank.returncode == 2, blank.stdout + blank.stderr
    assert "go together" in (blank.stdout + blank.stderr), (
        "a reason flag with no waiver names an override that did not happen — including when "
        f"the value is empty: {blank.stdout}{blank.stderr}"
    )

    spaces = run(
        "check",
        "--base-ref",
        "HEAD",
        "--allow-archive-shrink",
        "module:tortoise.sdk",
        "--allow-archive-shrink-reason",
        "   ",
    )
    assert spaces.returncode == 2, spaces.stdout + spaces.stderr
    assert "go together" in (spaces.stdout + spaces.stderr), (
        "refused for the wrong reason: the PAIRING guard is what must answer here, not the "
        f"needs--base-ref guard: {spaces.stdout}{spaces.stderr}"
    )

    # Positive control: a REAL reason beside a REAL waiver passes the pairing guard and is
    # answered by the base-ref arm instead.
    real = run(
        "check",
        "--base-ref",
        "no-such-ref-14039",
        "--allow-archive-shrink",
        "module:tortoise.sdk",
        "--allow-archive-shrink-reason",
        "deliberate: measured",
    )
    assert real.returncode == 2, real.stdout + real.stderr
    assert "go together" not in (real.stdout + real.stderr), (
        "a stated reason must pass the pairing guard and fail on the base ref instead: "
        f"{real.stdout}{real.stderr}"
    )


def test_an_unreadable_base_ref_is_a_refusal_not_a_pass(tree: Path) -> None:
    """A gate that cannot answer must not answer 'nothing was removed'."""
    with pytest.raises(arch.ArchiveRefused, match=r"merge base|base ref"):
        arch.shrink_violations(tree, base_ref="no-such-ref")


def test_the_cli_reds_on_a_module_removal_and_names_it(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance's own shape: the GATE exits non-zero, not just a helper returning text."""
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    _commit_drop(tree, "tortoise/gone.py")
    monkeypatch.setattr(arch, "ROOT", tree)
    monkeypatch.setattr(arch, "live_surface", lambda root=None: _surface())
    monkeypatch.setattr(arch, "index_violations", lambda entries, root=None: [])

    rc = arch.main(["archive_surface.py", "check", "--base-ref", "HEAD~1"])
    assert rc == 1, "the shrink arm did not reach the CLI's exit code"


def test_the_cli_refuses_a_waiver_without_a_reason(tree: Path) -> None:
    """An override with no stated reason is not a record (same rule as `cut --allow-removal`)."""
    assert arch.main(
        ["archive_surface.py", "check", "--base-ref", "HEAD", "--allow-archive-shrink", "module:x"]
    ) == 2
    assert arch.main(
        ["archive_surface.py", "check", "--allow-archive-shrink-reason", "because"]
    ) == 2


def test_the_cli_refuses_a_shrink_waiver_without_a_base_ref(capsys: pytest.CaptureFixture) -> None:
    """Review F5: the pairing guard needs its own assertion, or deleting it reds nothing.

    A waiver names a removal only the shrink arm can see, so without `--base-ref` it waives
    nothing while reading as a departure that was checked. Per agent-infra#3442 rule 1, every
    new fail-closed guard gets a mutant: delete this block and this test is the only red.
    """
    rc = arch.main(
        [
            "archive_surface.py",
            "check",
            "--allow-archive-shrink",
            "module:tortoise.gone",
            "--allow-archive-shrink-reason",
            "because",
        ]
    )
    assert rc == 2, "a waiver with no arm to waive must be refused, not run against no base"
    assert "--base-ref" in capsys.readouterr().err, (
        "the refusal must name the flag the operator has to add"
    )


def test_every_readme_section_a_violation_cites_exists() -> None:
    """Review F3: a printed REMEDY that cites a missing section is a remedy nobody can read.

    The citation is the whole justification the message gives for existing, so it is pinned to
    the README's own headings: adding a citation to a section that does not exist reds here.
    """
    source = (ROOT / "tools" / "archive_surface.py").read_text(encoding="utf-8")
    readme = (ROOT / "archive" / "README.md").read_text(encoding="utf-8")
    headings = "\n".join(line for line in readme.splitlines() if line.startswith("#"))
    cited = set(re.findall(r"archive/README\.md, '([^']+)'", source))

    assert cited, "positive control: the violations cite the procedure doc at all"
    missing = sorted(section for section in cited if section not in headings)
    assert missing == [], (
        f"a violation cites archive/README.md section(s) that do not exist: {missing}; the "
        "operator following the gate's own remedy would find nothing there"
    )


def test_the_ci_job_runs_the_shrink_arm_on_a_base_ref() -> None:
    """Wiring: an arm no job invokes is a no-op gate (the measured #9075/#11057 shape)."""
    workflow = (ROOT / ".github" / "workflows" / "python-ci.yml").read_text(encoding="utf-8")
    assert "tools/archive_surface.py check --require-entries --base-ref" in workflow, (
        "no workflow runs the shrink arm; deleting the flag from the step must red here, not "
        "read as a cleanup"
    )
    index = workflow.index("tools/archive_surface.py check --require-entries --base-ref")
    job = workflow.rfind("\n  surface-guard:", 0, index)
    assert job != -1, "the shrink arm needs the job whose clone has real history"
    assert "fetch-depth: 0" in workflow[job:index], (
        "the job running the shrink arm must check out full history, or the merge base is "
        "absent by construction and the gate cannot answer"
    )


# ── 11. review rounds 2-3: content, not paths (#12402, #12291) ────────────────

def test_an_init_py_WITH_A_BODY_is_a_removal_even_when_the_package_still_imports(
    tree: Path,
) -> None:
    """Round 3 F2: `tortoise/__init__.py` is 406 lines of the public API.

    The dotted name still resolves through a surviving sibling, so a PATH-set comparison
    cancels the deletion — measured: the pre-fix arm graded `git rm tortoise/__init__.py`
    green on a tree that kept `tortoise/sdk.py`, with no `module` entry required. A module
    body is content; only scaffolding may cancel.
    """
    (tree / "tortoise" / "core.py").write_text("X = 1\n", encoding="utf-8")
    (tree / "tortoise" / "__init__.py").write_text(
        "from tortoise.core import X\n\n__all__ = ['X']\n", encoding="utf-8"
    )
    _commit(tree, "tortoise")
    _commit_drop(tree, "tortoise/__init__.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any("tortoise: MODULE REMOVED" in p for p in problems), (
        "the package name still resolves through `core.py`, but its __init__ BODY left the "
        f"product and no other arm can see that: {problems}"
    )


def test_a_docstring_only_init_py_is_scaffolding_and_still_cancels(tree: Path) -> None:
    """The other direction: prose carries no API, so deleting it removes nothing."""
    (tree / "tortoise" / "pkg2").mkdir()
    (tree / "tortoise" / "pkg2" / "__init__.py").write_text(
        '"""Package marker."""\n', encoding="utf-8"
    )
    (tree / "tortoise" / "pkg2" / "sub.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/pkg2")
    _commit_drop(tree, "tortoise/pkg2/__init__.py")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == []


def test_a_waiver_must_name_a_real_kind(tree: Path) -> None:
    """The `kind not in KINDS` half of the parser was unpinned (round 3 note)."""
    with pytest.raises(arch.ArchiveRefused, match="not `<kind>:<symbol>`"):
        arch.shrink_violations(tree, base_ref="HEAD", allowed=["widget:tortoise.gone"])


def test_a_waiver_for_a_row_that_never_LEFT_says_so_without_claiming_the_base_lacks_it(
    tree: Path,
) -> None:
    """Round 3 F1's sibling: the verdict was right, the stated fact was false.

    The base RECORDS the row, so "does not record `x` at all" would send the operator looking
    for a removal that never happened. That is what this row pins.

    ⛔ AND `_archive_module` WRITES THE ROW, THE FROZEN FILE AND NO LIVE MODULE, so this shape
    is an ARCHIVED module — a row at the base and a module on NEITHER side, the ordinary case
    for `archive/`. The old assertion here demanded "still carries it" over a module this tree
    does not carry at all, i.e. the test PINNED the falsity instead of catching it (review F1 of
    the P8b landing). It now asserts the true pair: the base already records the removal, and
    this tree does not carry the module.

    ⛔ This is the ROW branch. The branch that answers from the package tree instead has its own
    fixture below, because the two are distinct facts and a single fixture can only exercise one
    (measured: the row-branch fixture never creates the module file, so it cannot tell "the tree
    carries it" from "the base's MANIFEST records it").
    """
    _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.gone"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "still carries it" not in waived[0], waived[0]
    assert "already records" in waived[0], waived[0]
    assert "does not carry it" in waived[0], waived[0]
    assert "does not record" not in waived[0], waived[0]


def test_a_MODULE_waiver_for_a_RECORDED_row_over_a_LIVE_module_still_says_the_tree_carries_it(
    tree: Path,
) -> None:
    """The OTHER half of the row branch's split (review F1 of the P8b landing).

    The base records the `module` row AND the module is still live here, so nothing left and
    "this tree still carries it" is the TRUE answer. Without this row the branch could be
    "fixed" into always printing the archived wording and only the sibling above would run,
    which is the shape that turns a fact into a constant. Discriminating: hardcoding the
    "already records / does not carry" text reds here.
    """
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.gone"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "still carries it" in waived[0], waived[0]
    assert "does not carry it" not in waived[0], waived[0]


def test_a_waiver_for_a_LIVE_module_the_base_never_recorded_says_so(
    tree: Path,
) -> None:
    """The package-tree branch: a module the base CARRIES with NO archive row for it.

    A `module` removal is answered by the package tree, so this shape must not claim the base
    "does not record" it — the base never recorded it, but the MODULE was there, and a reader
    told otherwise looks for a removal that never happened. Discriminating: on the row-branch
    fix alone the arm prints "does not record `tortoise.gone` (module) at all" and this reds.
    """
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    assert arch.load_manifest(tree / "archive" / "MANIFEST.json")["entries"] == [], (
        "the discriminating shape is a module the base carries with no archive row for it"
    )

    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.gone"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "still carries it" in waived[0], waived[0]
    assert "does not record" not in waived[0], waived[0]


def test_a_MODULE_waiver_for_a_RECORDED_deletion_does_not_claim_the_tree_still_carries_it(
    tree: Path,
) -> None:
    """Review P2 of PR #14039: the SAME falsity, one branch over, on the removal path.

    The reviewer's trace, performed: `tortoise/gone.py` had a body at the base, is deleted here,
    and the deletion carries its `module` row — so arm 1 stays silent (it does NOT mark the
    waiver used, because the record is what silenced it) and the waiver falls through as UNUSED.
    The old `elif symbol in base_modules:` answered from the BASE alone and printed "this tree
    still carries it, so nothing was removed for it to waive" over a module the same run had
    just required a record for. Discriminating: that exact sentence, in the same run that
    reports the record the operator passed to satisfy it.
    """
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    _commit_drop(tree, "tortoise/gone.py")
    _archive_module(tree, "tortoise.gone")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.gone"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "still carries it" not in waived[0], waived[0]
    assert "nothing was removed" not in waived[0], waived[0]
    assert "this tree does not" in waived[0], waived[0]
    assert "IS recorded" in waived[0], waived[0]


def test_a_MODULE_waiver_for_a_RECORDED_body_removal_does_not_claim_the_tree_still_carries_it(
    tree: Path,
) -> None:
    """The second shape the PATH set cannot see: the file survives, its body does not (arm 1b).

    A fix that asked only `symbol in head_modules` would still print "this tree still carries
    it" here, because arm 1b exists precisely because the NAME stays in that set while the code
    is gone — so the diagnostic has to ask the FILE. Discriminating: the membership-only
    wording reds this row, and the pre-fix wording reds both.
    """
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    (tree / "tortoise" / "gone.py").write_text("", encoding="utf-8")
    _archive_module(tree, "tortoise.gone")
    _commit(tree, "tortoise/gone.py")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.gone"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "still carries it" not in waived[0], waived[0]
    assert "this tree does not" in waived[0], waived[0]
    assert "IS recorded" in waived[0], waived[0]


# ── 12. the module→package split: arm 1's remedy must clear arm 2 (review C-F2) ─────

def _head_sha(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def _surface_with_bodies(tree: Path) -> dict:
    """The live surface `check` reads, built from a scratch tree — paths AND body-carriers.

    `modules_with_a_body` is the half that makes "the module came back" ask the same question
    arm 1 asks when it decides what LEFT, so a surface built without it exercises the old
    path-set rule and cannot prove this one.
    """
    paths = arch._package_modules(tree)
    return _surface(
        modules=paths,
        modules_with_a_body=arch._modules_with_a_body(
            paths, lambda path: arch._file_text(tree, path)
        ),
    )


def test_a_module_split_into_a_package_can_be_archived(tree: Path) -> None:
    """Review C-F2: carrying out arm 1's own REMEDY must clear the other arm.

    The split is ordinary code: `tortoise/x.py` becomes a package whose `__init__.py` is a
    docstring and whose body lives in `x/sub.py`. That `__init__.py` is importable, so
    `live_return`'s module branch used to call the archived module "shipped again" — one
    legitimate refactor met two arms that disagreed, and the route arm 1 PRINTS could never be
    completed: archiving was refused after it was prescribed, with no in-CI waiver.
    """
    (tree / "tortoise" / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py")
    base = _head_sha(tree)

    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").write_text(
        '"""The x package."""\n', encoding="utf-8"
    )
    (tree / "tortoise" / "x" / "sub.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "rm", "-q", "tortoise/x.py"], cwd=tree, check=True)
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref=base)
    assert any("tortoise.x: MODULE REMOVED" in p for p in problems), (
        f"the module's own body left with `x.py` and no other arm can see it: {problems}"
    )

    # …now carry out the remedy the violation printed: record the module, then grade again.
    entry = _archive_module(tree, "tortoise.x")
    _commit(tree, "archive")

    assert arch.shrink_violations(tree, base_ref=base) == [], (
        "the row arm 1 demands reds arm 2: the archive row exists and arm 1 is satisfied"
    )
    assert arch.surface_violations([entry], _surface_with_bodies(tree)) == [], (
        "the archive arm refuses the row the shrink arm PRESCRIBED — a scaffolding-only "
        "`__init__.py` is not the module coming back, and grading it as one left a "
        "module→package split with no route out of the red"
    )


def test_a_module_reappearing_as_a_PACKAGE_WITH_A_BODY_is_still_a_return(tree: Path) -> None:
    """The contrast the scaffolding rule needs: a re-exporting `__init__.py` IS the module.

    Without this direction "skip every `__init__.py`" would pass the test above while archived
    code shipped again through a package's own front door.
    """
    entry = _archive_module(tree, "tortoise.y")
    (tree / "tortoise" / "y").mkdir()
    (tree / "tortoise" / "y" / "__init__.py").write_text(
        "from tortoise.y.sub import VALUE\n\n__all__ = ['VALUE']\n", encoding="utf-8"
    )
    (tree / "tortoise" / "y" / "sub.py").write_text("VALUE = 1\n", encoding="utf-8")

    problems = arch.surface_violations([entry], _surface_with_bodies(tree))
    assert any("tortoise.y" in p and "REAPPEARED" in p for p in problems), (
        f"the package's `__init__.py` carries statements, so the module shipped again: {problems}"
    )


# ── round-18 review: the shapes a fresh-context pass measured as still open ──────────────


def test_emptying_the_PACKAGE_init_while_a_leftover_shim_exists_is_a_removal(tree: Path) -> None:
    """P0: arm 1b asked ONE base carrier, and the map picks by PATH, not by CONTENT.

    Measured before the fix: `base_paths_by_module` keeps the lexicographically first non-`.pyi`
    path per dotted name, and `.` (46) sorts before `/` (47) — so a body-less leftover
    `tortoise/x.py` beat the real `tortoise/x/__init__.py`. Arm 1b read the shim, called the BASE
    "scaffolding", and `continue`d: emptying the `__init__.py` IN PLACE was GREEN in every arm
    (`shrink_violations -> []`, `check -> OK`) with the body in no file under `archive/`. The
    pairing is CONSTRUCTED here, not copied from a shipped module: measured at round 21, no module
    in this repo has a BODYLESS shim beside another carrier (`tortoise/projection.py` is a real
    37-line re-export shim, which carries a body).

    Mutation: gate arm 1b on the single preferred carrier again -> RED here (and NOTHING else in
    this suite reds — measured: 158 rows green either way, so this row is the only pin).
    """
    (tree / "tortoise" / "x").mkdir(parents=True, exist_ok=True)
    (tree / "tortoise" / "x.py").write_text('"""stale shim."""\n', encoding="utf-8")
    (tree / "tortoise" / "x" / "__init__.py").write_text(
        "def real():\n    return 1\n\ndef other():\n    return 2\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/x.py", "tortoise/x/__init__.py")
    (tree / "tortoise" / "x" / "__init__.py").write_text('"""gone."""\n', encoding="utf-8")
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.x:") and "REMOVED" in p for p in problems), (
        "the base's code lived in tortoise/x/__init__.py; a body-less shim beside it must not "
        f"cancel the removal: {problems}"
    )


def test_deleting_an_EMPTY_pyi_stub_is_not_a_removal(tree: Path) -> None:
    """P2: a 0-byte or comment-only `.pyi` is not the module's code.

    The carrier test was by SUFFIX (`if path.endswith(".pyi"): return True`), so an empty stub
    counted as code: deleting it was `MODULE REMOVED`, the prescribed remedy froze an EMPTY block,
    and `file_violations` then red that block for holding no code — a false block with no exit
    (CI passes no waiver). A REAL stub is a `FunctionDef` and still counts.

    Mutation: `return True` for any `.pyi` -> RED here.
    """
    (tree / "tortoise" / "b.pyi").write_text("", encoding="utf-8")
    (tree / "tortoise" / "c.pyi").write_text("# annotations only, no declarations\n", encoding="utf-8")
    _commit(tree, "tortoise/b.pyi", "tortoise/c.pyi")
    _commit_drop(tree, "tortoise/b.pyi")
    _commit_drop(tree, "tortoise/c.pyi")

    assert arch.shrink_violations(tree, base_ref="HEAD~2") == [], (
        "a `.pyi` that declares nothing carries nothing, so deleting it removes nothing"
    )


def test_a_package_stub_module_is_reported_AND_has_a_runnable_route(tree: Path) -> None:
    """P2: `<dotted>/__init__.pyi` IS a carrier to the grading arms but was not to the writer.

    Measured before the fix: the deletion was `MODULE REMOVED` (correct — a stub under the shipped
    package is importable surface), and the remedy it printed, `archive module tortoise.pp`, was
    REFUSED: "no stub at tortoise/pp.pyi — nothing to archive". The fourth spelling was missing
    from the resolution chain, so the red had no route — the class rounds 7 and 15 each fixed one
    suffix over.

    Mutation: drop the package-stub fallback -> the archive raises instead of returning a row.
    """
    (tree / "tortoise" / "pp").mkdir(parents=True, exist_ok=True)
    (tree / "tortoise" / "pp" / "__init__.pyi").write_text(
        "def api() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/pp/__init__.pyi")
    _commit_drop(tree, "tortoise/pp/__init__.pyi")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.pp:") and "REMOVED" in p for p in problems), problems
    # …and the route the violation prints must actually run. The route's FIRST step is the
    # documented one (restore the source from the base, archive it, delete it again in the same
    # batch) — `archive_symbol` reads the working tree — so restore it here rather than pretending
    # the writer can freeze a file that is gone.
    (tree / "tortoise" / "pp").mkdir(parents=True, exist_ok=True)
    (tree / "tortoise" / "pp" / "__init__.pyi").write_text(
        "def api() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/pp/__init__.pyi")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.pp", "b1", "r", root=tree)
    assert entry["archived_from"] == "tortoise/pp/__init__.pyi", entry


# ⛔ THE BLANK-RENDERING SET IS NOW IN BOTH COPIES, SO IT IS PINNED IN BOTH (round-20 P3). The
# Hangul fillers and `U+2800` pass every `unicodedata` CATEGORY test and still render as nothing;
# `surface_manifest._stated_reason` lacked them while this copy had them, so one rule graded two
# ways by which tool asked. Mutation: drop `_BLANK_RENDERING` from either copy -> RED here.
@pytest.mark.parametrize(
    "reason",
    ["\u200b", "\u200e", "\ufeff", "\u00a0", "", "   ", "\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"],
)
def test_an_INVISIBLE_character_is_not_a_stated_reason(tree: Path, reason: str) -> None:
    """P2: `str.strip()` is not the question "does this say something a reader can read?".

    Measured before the fix: ZWSP, LRM and BOM are not `isspace()`, so each was ACCEPTED as a
    removal's WHY and rendered as nothing — a record that reads as "a departure somebody checked"
    and says nothing. The docstring of this gate's own round-17 comment claimed the hole was
    closed by `.strip()`; it was not.

    Mutation: back to `reason.strip()` alone -> RED for the three zero-width cases.
    """
    (tree / "tortoise" / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py")

    with pytest.raises(arch.ArchiveRefused, match="reason"):
        arch.archive_symbol(arch.MODULE, "tortoise.x", "b1", reason, root=tree)


def test_a_NON_TEXT_reason_is_a_REFUSAL_not_a_traceback(tree: Path) -> None:
    """P2: a library caller must get this tool's refusal, not `AttributeError`.

    Measured before the fix: `archive_symbol(..., reason=123)` raised
    `AttributeError: 'int' object has no attribute 'strip'` — every other refusal in the function
    is an `ArchiveRefused`, and `archive_symbol` is called by other tools.
    """
    (tree / "tortoise" / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _commit(tree, "tortoise/x.py")

    with pytest.raises(arch.ArchiveRefused, match="reason must be text"):
        arch.archive_symbol(arch.MODULE, "tortoise.x", "b1", 123, root=tree)  # type: ignore[arg-type]


@pytest.mark.parametrize("spelling", ['f"{register()}"', "[register(x) for x in y]"])
def test_a_top_level_statement_that_CALLS_inside_an_expression_is_a_body(
    tree: Path, spelling: str
) -> None:
    """P3: the no-op test graded the OUTERMOST node, so a nested call read as dead code.

    Measured before the fix: `_init_carries_a_body('f"{register()}"')` and
    `('[register(x) for x in y]')` both answered False — the predicate implemented "the outer node
    is a Call", not the stated rule — so a module whose only statement was one of them had its
    deletion CANCELLED as scaffolding while code with a side effect left the product. That is the
    fail-open direction, and both this file's comment and `archive/README.md` claimed the opposite.

    Mutation: `isinstance(node.value, _effectful)` (outermost only) -> RED here.
    """
    assert arch._init_carries_a_body(f"{spelling}\n"), (
        f"`{spelling}` calls, so it is a statement a caller can reach"
    )


def test_resolve_base_answers_the_MERGE_BASE_not_the_tip(tree: Path) -> None:
    """P3: the docstring says "the MERGE BASE of HEAD and that ref (not the tip)", unpinned.

    Measured: a `resolve_base` that answered the base ref's TIP left 29 selected rows green, so
    the documented semantics were ungraded. On a long-lived branch the tip reports OTHER PRs'
    removals against this one, or misses this PR's against the fork point.

    Mutation: return the ref's tip instead of `merge-base` -> RED here.
    """
    subprocess.run(["git", "switch", "-q", "-c", "side"], cwd=tree, check=True)
    (tree / "tortoise" / "side_only.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/side_only.py")
    tip = subprocess.run(
        ["git", "rev-parse", "side"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "switch", "-q", "main"], cwd=tree, check=True)
    (tree / "tortoise" / "main_only.py").write_text("Y = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/main_only.py")

    sha, label = arch.resolve_base(tree, "side")
    assert sha == subprocess.run(
        ["git", "merge-base", "HEAD", "side"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip(), f"resolve_base must answer the merge base, not the tip: {label}"
    assert sha != tip, "the fixture must place the tip past the fork point, or this row is vacuous"


# ── round-19 review: the shapes a fifth fresh-context pass measured as still open ───────


def test_a_body_MOVED_INTO_the_package_is_not_a_removal(tree: Path) -> None:
    """P1: the HEAD side asked ONE carrier too, so a legitimate refactor was RED with no route.

    `head_paths_by_module` collapses `<dotted>.py` and `<dotted>/__init__.py` onto one symbol by
    PATH, and `.` (46) sorts before `/` (47). So `y.py` reduced to a deprecation docstring with the
    same statements moved into `y/__init__.py` was graded `MODULE BODY REMOVED`: a correct-looking
    red in a required job whose printed route (`archive module tortoise.y`) freezes the NEW file and
    is then red again as `REAPPEARED as an importable module` — no green state, CI passes no waiver.
    The identical move in the OPPOSITE direction was GREEN, which is what made it visible.

    Mutation: ask only the collapsed `head_carrier` -> RED here.
    """
    body = "def real():\n    return 1\n\ndef other():\n    return 2\n"
    (tree / "tortoise" / "y.py").write_text(body, encoding="utf-8")
    _commit(tree, "tortoise/y.py")
    (tree / "tortoise" / "y.py").write_text(
        '"""DEPRECATED — the code moved into the package."""\n', encoding="utf-8"
    )
    (tree / "tortoise" / "y").mkdir()
    (tree / "tortoise" / "y" / "__init__.py").write_text(body, encoding="utf-8")
    _commit(tree, ".")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "the statements are byte-identical and the dotted name still resolves to them, so nothing "
        "left the product"
    )


def test_a_SYMLINKED_module_carrier_is_not_read_as_its_link_text(tree: Path) -> None:
    """P2: `_git_blob` on a symlink returns the TARGET PATH, which parsed as inert Python.

    Measured: `git ls-tree` mode `120000`, blob `real_impl.py` -> `Expr(Attribute)` -> "dead code",
    so deleting a symlinked module carrier was GREEN while the identical regular file was RED. The
    helper's own docstring promises an unreadable carrier never cancels a removal; this is the
    harder case, a reader that answers plausibly and wrongly, so the MODE is asked.

    Mutation: drop the `ls-tree` mode test -> RED here.
    """
    (tree / "tortoise" / "real_impl.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "alias.py").symlink_to("real_impl.py")
    _commit(tree, "tortoise/real_impl.py", "tortoise/alias.py")
    _commit_drop(tree, "tortoise/alias.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.alias:") and "REMOVED" in p for p in problems), (
        f"the module's name left the package, whatever the file's mode was: {problems}"
    )


def test_a_module_truncated_to_a_bare_ASSIGNMENT_is_CONTENT_by_DECISION(
    tree: Path,
) -> None:
    """DECLINED finding, pinned so the next lane does not adopt it blind (round-19 P2).

    The old name said "by the stated rule", which was FALSE for the README as it then read — round-20
    review measured the contradiction and the README now states this rule explicitly, with an
    `OVERRIDES:` line naming the default it departs from.

    The review proposed grading `X = 1` (and `X: int`) as inert, so a module truncated to one
    assignment is a removal. Measured, that contradicts the archive's own stated test — "the name
    no longer resolves to any code" — because a module whose whole body is `VALUE = 1` EXPORTS a
    name a caller reaches (`from mod import VALUE`). Adopting the rule red
    `test_a_module_split_into_a_package_can_be_archived` and fifteen other rows (sixteen in all,
    measured by the mutation that reds them) whose fixtures are
    constants-only modules.

    So the behaviour is pinned in the direction the rule requires: a surviving binding is content.
    The shape the review found (40 functions reduced to `X = 1`, no record, every arm green) is
    REAL and is NOT fixed here — the shrink gate grades symbols, not proportions, and a
    proportion rule would false-block every constants-only module.
    """
    (tree / "tortoise" / "x.py").write_text(
        "".join(f"def f{i}():\n    return {i}\n\n" for i in range(5)), encoding="utf-8"
    )
    _commit(tree, "tortoise/x.py")
    (tree / "tortoise" / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit(tree, ".")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "a module that still binds a name still resolves to code, so the shrink gate does not "
        "grade it — this row exists to keep the declined finding from being re-adopted"
    )


def test_the_writer_REFUSES_to_freeze_an_empty_pyi(tree: Path) -> None:
    """P3: the `.pyi` exemption on the WRITER was the same suffix test, one side over.

    Measured: `archive module tortoise.b` on an EMPTY `.pyi` was ACCEPTED and froze a block whose
    sha256 is the digest of `b""`; `file_violations` then red the row it had just written ("has an
    EMPTY frozen block") — a red whose only clear is deleting the record. A real stub is a
    `FunctionDef`, so the content rule refuses only a file that declares nothing.

    Mutation: `if not rel.endswith(".pyi") and not _init_carries_a_body(source):` -> RED here.
    """
    (tree / "tortoise" / "b.pyi").write_text("", encoding="utf-8")
    _commit(tree, "tortoise/b.pyi")

    with pytest.raises(arch.ArchiveRefused, match=r"no body of its own"):
        arch.archive_symbol(arch.MODULE, "tortoise.b", "b1", "r", root=tree)


def test_the_ROUTE_runs_when_a_BODYLESS_py_survives_beside_a_package_stub(
    tree: Path,
) -> None:
    """P2: the "ask the module's other carriers" WALK still enumerated three spellings.

    Round 18 taught the first resolution chain about `<dotted>/__init__.pyi` and left the sibling
    walk inside the same function at three, so a bodyless `w.py` leftover beside the real
    `w/__init__.pyi` produced a RED whose printed remedy named `w.py` — a file that provably holds
    no statements — and refused again no matter how often it was restored.

    Mutation: `for other in (package_rel, stub_rel):` -> RED here.
    """
    (tree / "tortoise" / "w.py").write_text('"""leftover."""\n', encoding="utf-8")
    (tree / "tortoise" / "w").mkdir()
    (tree / "tortoise" / "w" / "__init__.pyi").write_text(
        "def api() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/w.py", "tortoise/w/__init__.pyi")
    _commit_drop(tree, "tortoise/w/__init__.pyi")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.w:") and "REMOVED" in p for p in problems), problems
    # The route's first step is the documented one: restore the source from the base. The WALK is
    # what this row is about — with a bodyless `w.py` still on disk, the resolution chain must find
    # the restored `.pyi` instead of refusing over the shim it met first.
    (tree / "tortoise" / "w").mkdir(parents=True, exist_ok=True)
    (tree / "tortoise" / "w" / "__init__.pyi").write_text(
        "def api() -> int: ...\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/w/__init__.pyi")
    entry = arch.archive_symbol(arch.MODULE, "tortoise.w", "b1", "r", root=tree)
    assert entry["archived_from"] == "tortoise/w/__init__.pyi", entry


def test_live_return_sees_a_PACKAGE_STUB_carrier(tree: Path) -> None:
    """P3: the only GREEN→RED half of the round-18 fix had no positive control at all.

    Measured: removing the `f"{base}/__init__.pyi"` candidate from `live_return`'s tuple left 26
    related rows green while `live_return` flipped to `None` and `surface_violations` stopped
    reporting the module as live — so the sole red this diff can produce could be deleted silently.
    The file's own doctrine: "every violation class this module reports has a positive control".

    Mutation: drop the package-stub candidate -> RED here.
    """
    entry = _entry(arch.MODULE, "tortoise.pp", batch="b1", file="archive/modules/b1/tortoise.pp.py")
    surface = {
        "tools": set(),
        "sdk": set(),
        "modules": {"tortoise/pp/__init__.pyi"},
        "modules_with_a_body": {"tortoise.pp"},
    }

    assert arch.live_return(entry, surface, tree) == "tortoise/pp/__init__.pyi"


@pytest.mark.parametrize("reason", ["\u200b", "\u200e", "\ufeff"])
def test_a_ZWSP_waiver_reason_is_refused_by_the_CLI(tree: Path, reason: str) -> None:
    """P2: the invisible-character rule reached the RECORD path and not the WAIVER path.

    Measured before the fix: `check --base-ref HEAD --allow-archive-shrink module:x
    --allow-archive-shrink-reason $'\\u200b'` exited 0 and printed `reason: ` (nothing) on a green
    run — and a waiver is the only thing that replaces a record for a removal.

    Mutation: `.strip() or None` on the waiver reason -> RED here.
    """
    rc = arch.main(
        [
            "archive_surface.py",
            "check",
            "--base-ref",
            "HEAD",
            "--allow-archive-shrink",
            "module:tortoise.ghost",
            "--allow-archive-shrink-reason",
            reason,
        ]
    )
    assert rc == 2, f"a waiver whose reason renders as nothing is not a stated reason (rc={rc})"


def test_an_UNCHANGED_pyi_only_module_is_not_a_removal(tree: Path) -> None:
    """P1 REGRESSION (round-20): the head-side carrier set skipped `.pyi`, so a stub-only module
    that did not change graded `MODULE BODY REMOVED`.

    Measured before the fix: base == head == `{"tortoise/s.pyi": "def api() -> int: ...\\n"}` ->
    `['tortoise.s: MODULE BODY REMOVED — … carries none (empty, whitespace, comments or a bare
    docstring)']` — a false block on a tree where nothing changed, asserting a false fact about a
    file holding a `def`, and its printed route (`archive module tortoise.s`) freezes the live stub
    so `surface_violations` then reds it as `REAPPEARED`: no green state.

    The arm asks for a RUNTIME body at the base (`base_runtime_body_modules`), which a stub-only
    module never supplies.

    Mutation: gate arm 1b on `base_body_modules` -> RED here.
    """
    (tree / "tortoise" / "s.pyi").write_text("def api() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/s.pyi")
    # a SECOND commit that does not touch the stub at all, so base and head differ by a commit and
    # the module is byte-identical on both sides (the shape the regression graded RED)
    (tree / "tortoise" / "unrelated.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/unrelated.py")

    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "a stub-only module that did not change is not a removal"
    )


def test_a_MODULE_waiver_over_an_UNCHANGED_STUB_does_not_state_a_removal(
    tree: Path,
) -> None:
    """Round-24 review, F1 (MEASURED): the unused-waiver diagnostic borrowed the RUNTIME answer.

    `_head_carries_runtime_body` answers "does this tree hold the module's runtime body", and this
    caller used it as "does this tree carry the module AT ALL". The two differ on exactly one
    shape: a module whose only carrier is an UNCHANGED `.pyi` stub. Nothing was removed there (a
    stub-only module's only code IS the stub, #12402), but the runtime answer is `False`, so the
    diagnostic took its removal branch. Measured pre-fix on this fixture, verbatim:

        … carries `tortoise.s` (module) and this tree does not — and NO `module` entry records
        it, so the removal is UNRECORDED: the entry is what is missing

    over a tree that carries `tortoise/s.pyi` with a `def` in it. Following that line means
    `archive module tortoise.s`, which freezes a LIVE stub that `surface_violations` then reds as
    `REAPPEARED as an importable module` — a correct verdict beside a false stated fact with no
    green route, the class this file keeps removing.

    Mutation: drop the `symbol in head_modules and symbol not in base_runtime_body_modules`
    branch at the `symbol in base_modules` site -> RED here (the UNRECORDED wording returns).
    """
    (tree / "tortoise" / "s.pyi").write_text("def api() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/s.pyi")
    # a SECOND commit that does not touch the stub, so it is byte-identical on both sides
    (tree / "tortoise" / "unrelated.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/unrelated.py")
    assert arch.shrink_violations(tree, base_ref="HEAD~1") == [], (
        "the premise is a tree where NOTHING was removed"
    )

    problems = arch.shrink_violations(
        tree, base_ref="HEAD~1", allowed=["module:tortoise.s"]
    )

    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "UNRECORDED" not in waived[0], waived[0]
    assert "this tree does not" not in waived[0], waived[0]
    assert "STILL CARRIES" in waived[0], waived[0]
    assert "nothing was removed" in waived[0], waived[0]


def test_a_MODULE_waiver_over_an_UNCHANGED_STUB_WITH_A_ROW_does_not_state_a_removal(
    tree: Path,
) -> None:
    """Round-24 review, F1 (MEASURED): the same falsity on the ROW branch, one `if` away.

    The row branch asked the runtime predicate too, so a committed `module` row over a module this
    tree still carries as an unchanged stub printed "this tree does not carry it — the removal IS
    recorded by that `module` entry". The row's existence is true; `this tree does not carry it`
    is not, and it is the half an operator acts on. Both branches share `_still_carried_why`, so
    the two can no longer drift (the shape `_stub_waiver_why` already guards one branch over).

    Mutation: drop that branch at the `key in base_entries` site -> RED here.
    """
    (tree / "tortoise" / "s.pyi").write_text("def api() -> int: ...\n", encoding="utf-8")
    _commit(tree, "tortoise/s.pyi")
    arch.save_manifest(
        {
            "entries": [
                {
                    "kind": "module",
                    "symbol": "tortoise.s",
                    "batch": "b-shrink",
                    "reason": "no per-tool destination (#11609)",
                    "file": "archive/modules/b-shrink/tortoise.s.py",
                    "archived_from": "tortoise/s.py",
                    "archived_at": "a" * 32,
                }
            ]
        },
        tree / "archive" / "MANIFEST.json",
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.s"]
    )

    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "this tree does not carry it" not in waived[0], waived[0]
    assert "STILL CARRIES" in waived[0], waived[0]


def test_a_SYMLINKED_package_init_whose_TARGET_is_deleted_is_a_removal(tree: Path) -> None:
    """P1 (round-20): the CARRIER SETS read raw blobs, so a symlinked `__init__.py` was invisible.

    Measured: `tortoise/x/__init__.py` -> symlink to `../../shared/impl.py`, then one commit deletes
    the package AND the target. `_git_blob` returned the LINK TEXT for the base carrier,
    `_modules_with_a_body` parsed it as an inert expression, so the module never entered
    `base_modules` — every arm green, the body in no file under `archive/` and none at head. The
    answer depended on the FILENAME (`alias.py` -> RED, `x/__init__.py` -> GREEN).

    Mutation: read the base carriers with `_git_blob` -> RED here.
    """
    (tree / "shared").mkdir()
    (tree / "shared" / "impl.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../../shared/impl.py")
    _commit(tree, "shared/impl.py", "tortoise/x/__init__.py")
    subprocess.run(["git", "rm", "-r", "-q", "tortoise/x", "shared/impl.py"], cwd=tree, check=True)
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.x:") and "REMOVED" in p for p in problems), (
        f"the module's code is in no file under archive/ and none at head: {problems}"
    )


def test_a_TWO_HOP_link_chain_whose_FINAL_target_is_OUTSIDE_the_package_is_a_removal(
    tree: Path,
) -> None:
    """P1 (round-23): one hop was not enough, and the docstring claimed the loss was impossible.

    Measured: `tortoise/x/__init__.py -> ../../shared/alias.py -> impl.py`, one commit deleting
    all three. `_blob_following_links` returned the SECOND link's TEXT (`"impl.py"`), which parses
    as an inert expression, so `tortoise.x` never entered `base_modules`; the final target sat
    outside `tortoise/`, which `_package_paths_at` does not enumerate — so no arm red it (`[]`).
    The same code whose final target sat under `tortoise/` gave two `MODULE REMOVED` rows, i.e.
    the answer depended on where the real file happened to live, which is the class this gate
    exists to remove. The old docstring asserted "the body is never silently lost; only the
    package name stops being blamed" — false for exactly the outside-package shape.

    Mutation: follow ONE hop only -> RED here.
    """
    (tree / "shared").mkdir()
    (tree / "shared" / "impl.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (tree / "shared" / "alias.py").symlink_to("impl.py")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../../shared/alias.py")
    _commit(tree, "shared/impl.py", "shared/alias.py", "tortoise/x/__init__.py")
    subprocess.run(
        ["git", "rm", "-r", "-q", "tortoise/x", "shared/alias.py", "shared/impl.py"],
        cwd=tree,
        check=True,
    )
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.x:") and "REMOVED" in p for p in problems), (
        f"a two-hop chain holds code like any other carrier: {problems}"
    )


def test_the_route_printed_for_a_SYMLINKED_carrier_ANCHORS_at_the_commit_that_holds_it(
    tree: Path,
) -> None:
    """P1 (round-23): the writer froze the TARGET's text while the anchor compared LINK TEXT.

    `_module_source` freezes `path.read_text()`, which FOLLOWS the link; `block_holds` compared
    that block against `_git_blob` — the LINK TEXT for mode `120000` — so it could never hold.
    Measured on a probe commit: `holds(base) = False` while `_git_blob(base, path)` was
    `'../../shared/alias.py'`, so `archive module tortoise.x` — the remedy the gate PRINTS for
    this carrier — refused with "no commit on main holds this code" at the very commit that holds
    it: a red with no green route, the class rounds 19–21 closed twice elsewhere.

    Mutation: read the candidate with `_git_blob` in `block_holds` -> RED here.
    """
    (tree / "shared").mkdir()
    (tree / "shared" / "impl.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../../shared/impl.py")
    _commit(tree, "shared/impl.py", "tortoise/x/__init__.py")
    base = _head_sha(tree)

    blocks = arch._module_source(tree, "tortoise.x")
    holds = arch.block_holds(tree, "module", "tortoise.x", blocks)
    assert holds(base) is True, (
        "the anchor must read the body the writer froze, not the link text: the printed remedy "
        "is otherwise refused at the commit that holds the code"
    )
    anchored, ref = arch.anchor_sha(tree, [block[0] for block in blocks], holds)
    assert anchored == base
    assert ref, "anchor_sha must name the ref it read"


def test_a_DANGLING_symlink_is_not_the_archived_module_COMING_BACK(tree: Path) -> None:
    """P2 (round-23): one file, two arms, contradictory statements about the same tree.

    `_file_text` returns `None` for a dangling link, and the old `text is not None and ...` let
    it fall through to `return candidate` — i.e. "REAPPEARED on the live surface" for a file
    holding no code — while arm 1b graded that same file `MODULE BODY REMOVED` in the same run.
    A file that holds nothing is not the module coming back; the removal arm is the true verdict.

    Mutation: fold the `text is None` case back into the `and` -> RED here.
    """
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../../shared/impl.py")
    # ⛔ `modules` HOLDS PATHS, `modules_with_a_body` HOLDS DOTTED NAMES (round-25 review, P2 —
    # measured). This fixture put the DOTTED name in `modules`, which no candidate path can ever
    # equal, so the loop fell through to `return None` WITHOUT reading the file — the row passed
    # with the defect present, the behaviour it exists to pin was ungraded, and its stated
    # `Mutation:` did not hold. Production builds `modules` from `_package_modules` (repo-relative
    # paths) — see `_surface` and the sibling at `surface={"tortoise/pp/__init__.pyi"}`.
    surface = {
        "modules": {"tortoise/x/__init__.py"},
        "modules_with_a_body": {"tortoise.x"},
    }
    entry = {"kind": arch.MODULE, "symbol": "tortoise.x"}
    # POSITIVE CONTROL, so an empty candidate set can never make this row vacuous again: the same
    # surface with a carrier that holds real code IS the module coming back.
    (tree / "tortoise" / "x" / "__init__.py").unlink()
    (tree / "tortoise" / "x" / "__init__.py").write_text(
        "def body():\n    return 1\n", encoding="utf-8"
    )
    assert arch.live_return(entry, surface, tree) == "tortoise/x/__init__.py", (
        "positive control: a candidate that matches the surface AND holds code is a return"
    )
    # …and the row under test: the same candidate, now a DANGLING link.
    (tree / "tortoise" / "x" / "__init__.py").unlink()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../../shared/impl.py")
    assert arch.live_return(entry, surface, tree) is None, (
        "a dangling link holds no code: counting it LIVE contradicts arm 1b about one file"
    )


def test_an_ABSOLUTE_link_target_is_UNREADABLE_not_a_known_empty(tree: Path) -> None:
    """P2 (round-25): `git show` refusing a path is NOT a reading that the carrier holds no code.

    `_blob_following_links` used to map every refused read to `_DANGLING` ("provably holds no
    code"), and an absolute target is always refused (`git show HEAD:/abs/…` -> rc 128). So a
    package symlinked by ABSOLUTE path to its real code dropped out of the carrier sets and
    deleting it graded GREEN — while the byte-identical RELATIVE link reds. The verdict was
    decided by the link's SPELLING, the class rounds 19 and 23 removed one spelling over.

    Mutation: return `_DANGLING` for a target that leaves the repo -> RED here.
    """
    (tree / "tortoise" / "real.py").write_text("def real(x):\n    return x * 2\n", encoding="utf-8")
    (tree / "tortoise" / "x").mkdir()
    # ABSOLUTE on purpose: `symlink_to(str(...))` stores the absolute path verbatim.
    (tree / "tortoise" / "x" / "__init__.py").symlink_to(str(tree / "tortoise" / "real.py"))
    _commit(tree, "tortoise/real.py", "tortoise/x/__init__.py")

    assert arch._blob_following_links(
        tree, "HEAD", "tortoise/x/__init__.py"
    ) is arch._UNREADABLE, (
        "an absolute target is not addressable by `git show`, so nothing is known about it: "
        f"_DANGLING ({arch._DANGLING!r}) would claim a reading that was never taken"
    )


def test_an_UNREADABLE_blob_mode_is_not_read_as_a_regular_file(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P3 (round-25): `"".startswith("120")` is False, so a failed `ls-tree` returned the LINK TEXT.

    `_blob_mode_at` answers `""` when `ls-tree` fails or prints nothing, and the caller read that as
    "regular file" and handed back the link text — the "plausible-looking link text" the docstring
    forbids inventing, and the opposite polarity to `_content_or_link`, which reads the same `""` as
    fail-closed. One question, two answers, on the one datum the whole chain read turns on.

    Mutation: test `not _blob_mode(...).startswith("120")` without the empty guard -> RED here.
    """
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("real.py")
    (tree / "tortoise" / "real.py").write_text("def real(x):\n    return x * 2\n", encoding="utf-8")
    _commit(tree, "tortoise/real.py", "tortoise/x/__init__.py")
    arch._blob_mode_at.cache_clear()

    monkeypatch.setattr(arch, "_blob_mode", lambda *a, **k: "")
    assert arch._blob_following_links(
        tree, "HEAD", "tortoise/x/__init__.py"
    ) is arch._UNREADABLE, (
        "a mode that could not be read is not a regular file: returning the link text here is the "
        "plausible-but-wrong reading the bound exists to refuse"
    )
    arch._blob_mode_at.cache_clear()


def test_the_blob_MODE_cache_is_keyed_on_a_SHA_not_a_ref(tree: Path) -> None:
    """P2 (round-23): "a commit's tree is immutable" is true of a SHA and FALSE of `HEAD`.

    Measured in one process: `_blob_mode(root, "HEAD", path)` answered `100644` after the path
    had been committed as a symlink (`ls-tree HEAD` reports `120000`). No caller in the tool
    passes a ref today — which is why the miss has to be structural, not a habit.

    Mutation: drop `_commit_sha` from `_blob_mode` -> RED here.
    """
    (tree / "tortoise" / "q.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/q.py")
    arch._blob_mode_at.cache_clear()
    assert arch._blob_mode(tree, "HEAD", "tortoise/q.py").startswith("100")

    (tree / "tortoise" / "q.py").unlink()
    (tree / "tortoise" / "real.py").write_text("y = 2\n", encoding="utf-8")
    (tree / "tortoise" / "q.py").symlink_to("real.py")
    _commit(tree, "tortoise/q.py", "tortoise/real.py")

    assert arch._blob_mode(tree, "HEAD", "tortoise/q.py").startswith("120"), (
        "the cache answered a MOVED question: a ref is not an immutable tree"
    )
    arch._blob_mode_at.cache_clear()


def test_an_EMPTY_pyi_sibling_does_not_rescue_a_bodyless_module(tree: Path) -> None:
    """P2 (round-20): the writer's OTHER-CARRIER walk exempted `.pyi` from the content test by
    SUFFIX, so an EMPTY stub let the writer freeze a 0-char block.

    Measured before the fix: `tortoise/c.py` holding `\"\"\"Scaffolding.\"\"\"` plus an empty
    `tortoise/c.pyi` -> `archive module tortoise.c` SUCCEEDED, recording
    `archived_from = tortoise/c.pyi` and `sha256` of `b""`; `shrink_violations` was `[]` (the row
    is credited on EXISTENCE) while only `file_violations` red the "EMPTY frozen block" — a record
    the writer itself refuses to accept, whose only clear is deleting it, i.e. no route that keeps
    it. The same walk already applies the content rule to `.py`, so the suffix was the whole bug.

    The module's only body-carrier here is the scaffolding `.py`, so the correct answer is the
    refusal that names the restore route; the empty stub must not stand in as the source of record.

    Mutation: reinstate the `other.endswith(".pyi") or` arm -> RED here.
    """
    (tree / "tortoise" / "c.py").write_text('"""Scaffolding."""\n', encoding="utf-8")
    (tree / "tortoise" / "c.pyi").write_text("", encoding="utf-8")
    _commit(tree, "tortoise/c.py")

    with pytest.raises(arch.ArchiveRefused) as refused:
        arch._module_source(tree, "tortoise.c")
    assert "carries no body" in str(refused.value)
    assert "git checkout" in str(refused.value), "the refusal must name a route that works"


def test_an_UNCHANGED_symlinked_carrier_is_not_a_body_removal(tree: Path) -> None:
    """REGRESSION (round-21): a symlink carrier was called "code" without asking its target.

    `_content_or_link` answered `not mode.startswith("100")` for mode `120000`, while the HEAD side
    reads THROUGH the link (`Path.read_text` follows it). So an UNCHANGED `x_init.py` pointing at a
    docstring `impl.py` — and an UNCHANGED dangling link — graded `MODULE BODY REMOVED` on a tree
    where nothing changed, and the message asserted two false things about the file ("carries
    statements in `tortoise/x_init.py`" / "this tree's module file carries none").

    Two shapes here, because they fail for different reasons: a resolvable link to PROSE (the
    blanket mode test), and a DANGLING link whose target `git show` cannot read at all.

    Mutation: restore `return not mode.startswith("100")` -> RED here.
    """
    (tree / "tortoise" / "impl.py").write_text('"""just prose."""\n', encoding="utf-8")
    (tree / "tortoise" / "x_init.py").symlink_to("impl.py")
    (tree / "tortoise" / "gen.py").symlink_to("../build/gen.py")  # dangling on purpose
    _commit(tree, "tortoise/impl.py", "tortoise/x_init.py", "tortoise/gen.py")

    (tree / "tortoise" / "unrelated.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/unrelated.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert problems == [], (
        f"a link to prose, and a dangling link, hold no code — nothing changed: {problems}"
    )


def test_the_MODULE_BODY_REMOVED_names_the_carrier_that_HELD_the_body(tree: Path) -> None:
    """P2 (round-21): the message named the lexicographically FIRST path, not the carrier.

    `base_paths_by_module[module]` is the first non-`.pyi` path and `.` (46) sorts before `/` (47),
    so with a docstring `p.py` beside a code-carrying `p/__init__.py` the violation read "carries
    statements in `tortoise/p.py`" — false — and the route it printed restored the docstring and
    re-refused identically: a permanent red whose stated fact and remedy both pointed at the wrong
    file.

    The shape is CONSTRUCTED here, not copied from a shipped module: measured at round 21, no
    module in this repo has a bodyless `.py` shim beside another carrier (`tortoise/projection.py`
    is a real 37-line re-export shim, which counts as a body). A first draft of this docstring
    called it "the pairing this repo SHIPS" — a false claim the VGATE refused.

    Mutation: build the label from `base_paths_by_module[module]` -> RED here.
    """
    (tree / "tortoise" / "p").mkdir()
    (tree / "tortoise" / "p" / "__init__.py").write_text(
        "def real():\n    return 1\n", encoding="utf-8"
    )
    (tree / "tortoise" / "p.py").write_text('"""Deprecated shim."""\n', encoding="utf-8")
    _commit(tree, "tortoise/p.py", "tortoise/p/__init__.py")
    (tree / "tortoise" / "p" / "__init__.py").write_text('"""Gone."""\n', encoding="utf-8")
    _commit(tree, "tortoise/p/__init__.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    row = next(p for p in problems if p.startswith("tortoise.p:") and "REMOVED" in p)
    assert "tortoise/p/__init__.py" in row, (
        f"the statements were in the package's __init__, not the docstring shim: {row}"
    )
    assert "carries statements in `tortoise/p.py`" not in row, (
        f"the message must not assert a false fact about the shim: {row}"
    )


def test_a_symlink_whose_TARGET_TEXT_parses_as_a_STATEMENT_is_not_code(tree: Path) -> None:
    """P1 (round-22): the mode was read AFTER the content test, so a link's TEXT could short-circuit.

    `x_init.py -> impl()` is `ast.Expr(Call)` and `y_init.py -> impl = 1` is `ast.Assign` — both
    "content" to `_init_carries_a_body` — so the test passed above the `ls-tree` read and the TARGET
    was never consulted. Measured: an UNCHANGED dangling link of either spelling graded
    `MODULE BODY REMOVED … carries statements in `tortoise/x_init.py` and this tree's module file
    `tortoise/x_init.py` carries none` — a false block on a tree where nothing changed, asserting
    two false things about a file that is a symlink holding no statements at all.

    The mode is the only datum that says "this blob is a target, not source", so it is asked first.

    Mutation: move the content test back above the `ls-tree` read -> RED here.
    """
    (tree / "tortoise" / "x_init.py").symlink_to("impl()")  # dangling, and the text parses
    (tree / "tortoise" / "y_init.py").symlink_to("impl = 1")  # an Assign, also non-inert
    _commit(tree, "tortoise/x_init.py", "tortoise/y_init.py")

    (tree / "tortoise" / "unrelated.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/unrelated.py")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert problems == [], (
        f"link TEXT is not source — neither link resolves to anything: {problems}"
    )


def test_a_link_CHAIN_AT_the_HOP_BOUND_whose_TARGET_holds_code_is_a_removal(tree: Path) -> None:
    """P1 (round-24): the hop loop stepped onto the final target and exited WITHOUT testing it.

    `for _ in range(_LINK_HOPS)` walks `_LINK_HOPS` links and then returns `None` — never asking the
    mode of the node it is standing on. So a chain of exactly `_LINK_HOPS` links to a real file
    answered `None`, and `None` meant two different things to the two askers: `_content_or_link`
    read it as "not the module's code" (dropping the module from `base_body_modules`, so arm 1
    `continue`d) while `_modules_with_a_body` read it as content. Measured: a 6-link chain deleted
    was RED; a 7-link chain to `def real(...)` was GREEN with the body in no file under `archive/`.

    The bound is now `_LINK_HOPS + 1` so the final target's mode is ASKED, and an exhausted bound
    answers `_UNREADABLE` (fail closed) instead of "no code".

    Mutation: `range(_LINK_HOPS)` (drop the +1) -> RED here.
    """
    # the CARRIER link counts too: `x/__init__.py -> link0 -> ... -> real.py` must be exactly
    # `_LINK_HOPS` links in total, so the loop needs `_LINK_HOPS` steps plus one mode test.
    links = arch._LINK_HOPS - 1
    for i in range(links):
        (tree / "tortoise" / f"link{i}.py").symlink_to(
            "real.py" if i == links - 1 else f"link{i + 1}.py"
        )
    (tree / "tortoise" / "real.py").write_text(
        "def real(x):\n    return x * 2\n\nCONST = 7\n", encoding="utf-8"
    )
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../link0.py")
    _commit(tree, *(f"tortoise/link{i}.py" for i in range(links)), "tortoise/real.py",
            "tortoise/x/__init__.py")
    subprocess.run(
        ["git", "rm", "-r", "-q", "tortoise/x", "tortoise/real.py"]
        + [f"tortoise/link{i}.py" for i in range(links)],
        cwd=tree, check=True,
    )
    _commit(tree, ".")

    # ⛔ AND THE READ ITSELF MUST RESOLVE, not merely fail closed (round-24 P1, measured). With the
    # polarity fix alone a bounded read answers `_UNREADABLE`, which keeps this row RED for the wrong
    # reason — so the resolution is asserted directly, and THAT is what the `+1` is for.
    resolved = arch._blob_following_links(tree, "HEAD~1", "tortoise/x/__init__.py")
    assert "def real(x)" in (resolved or ""), (
        f"a chain of exactly _LINK_HOPS links must resolve to its target, got {resolved!r}"
    )

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.x:") and "REMOVED" in p for p in problems), (
        f"a chain at the bound still reaches real code: {problems}"
    )


def test_a_link_CHAIN_PAST_the_HOP_BOUND_that_reaches_code_is_a_removal(tree: Path) -> None:
    """Round-24 P1, the OTHER half: an EXHAUSTED bound is UNKNOWN, never "no code".

    The sibling row above pins the `+1` (a chain of exactly `_LINK_HOPS` links must RESOLVE, so it
    never reaches the bound). That leaves the bound itself unmeasured: nothing in this file built a
    chain the loop cannot resolve, so the fail-closed polarity could be reverted silently. It was —
    the bound used to return `None`, which the two askers read in OPPOSITE directions: `_content_or_link`
    took it as "not the module's code" (so the module never entered `base_body_modules` and arm 1
    `continue`d) while `_modules_with_a_body` took the SAME `None` as content. The cancelling reader
    decided the verdict, so deleting a 9-link chain that ends at real code was GREEN with the body in
    no file under `archive/` — a false PASS in the one direction this gate exists to prevent.

    Mutation: `return _UNREADABLE` at the bound -> `return _DANGLING` -> RED here (no `tortoise.x`
    row: the module drops out of `base_body_modules` and arm 1 cancels the removal).
    """
    # ONE PAST what the loop can resolve: the carrier link PLUS `_LINK_HOPS` intermediates is
    # `_LINK_HOPS + 1` links in total, one more than `range(_LINK_HOPS + 1)` can walk to the end.
    links = arch._LINK_HOPS
    for i in range(links):
        (tree / "tortoise" / f"link{i}.py").symlink_to(
            "real.py" if i == links - 1 else f"link{i + 1}.py"
        )
    (tree / "tortoise" / "real.py").write_text(
        "def real(x):\n    return x * 2\n", encoding="utf-8"
    )
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../link0.py")
    _commit(tree,
            *(f"tortoise/link{i}.py" for i in range(links)),
            "tortoise/real.py", "tortoise/x/__init__.py")
    subprocess.run(
        ["git", "rm", "-r", "-q", "tortoise/x", "tortoise/real.py"]
        + [f"tortoise/link{i}.py" for i in range(links)],
        cwd=tree, check=True,
    )
    _commit(tree, ".")

    # the premise: this chain is genuinely UNRESOLVED, not merely a shorter one that resolves
    assert arch._blob_following_links(tree, "HEAD~1", "tortoise/x/__init__.py") is None, (
        "the fixture must exceed the bound, or it measures the sibling row instead"
    )

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.x:") and "REMOVED" in p for p in problems), (
        "an unresolvable chain to real code is UNKNOWN, and unknown must not cancel a removal "
        f"(fail closed): {problems}"
    )


def test_an_archived_module_from_a_SYMLINKED_carrier_is_CLEAN(tree: Path) -> None:
    """P1 (round-24): the anchor read the RAW blob, so the gate red a record the writer had just made.

    The writer freezes `path.read_text()`, which FOLLOWS a symlink; `_anchor_violations` compared
    that frozen body against `_git_blob`, the LINK TEXT for mode `120000`. Measured: `archive module`
    on a symlinked carrier SUCCEEDED (`archived_from=tortoise/x/__init__.py`, frozen body = the real
    code), and `check` then reported "the recorded commit does not describe this body" — a red with
    no route, since re-archiving reproduces it byte-identically.

    Mutation: read the anchor with `_git_blob` -> RED here.
    """
    (tree / "tortoise" / "impl.py").write_text("def real():\n    return 1\n", encoding="utf-8")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../impl.py")
    _commit(tree, "tortoise/impl.py", "tortoise/x/__init__.py")

    entry = arch.archive_symbol(arch.MODULE, "tortoise.x", "batch-a", "r", root=tree)
    frozen = (tree / entry["file"]).read_text(encoding="utf-8")
    assert "def real()" in frozen, "the frozen body must be the TARGET's code"
    assert arch.file_violations([entry], tree / "archive", tree) == []


def _chain(tree: Path, links: int) -> None:
    """`tortoise/x/__init__.py -> lc0 -> ... -> real.py`, *links* links in total."""
    for i in range(links - 1):
        (tree / "tortoise" / f"lc{i}.py").symlink_to(
            "real.py" if i == links - 2 else f"lc{i + 1}.py"
        )
    (tree / "tortoise" / "real.py").write_text("def real(x):\n    return x * 2\n", encoding="utf-8")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to("../lc0.py")
    _commit(
        tree,
        *(f"tortoise/lc{i}.py" for i in range(links - 1)),
        "tortoise/real.py",
        "tortoise/x/__init__.py",
    )


def test_a_link_CHAIN_BEYOND_the_HOP_BOUND_fails_CLOSED(tree: Path) -> None:
    """P2 (round-25): the fail-closed polarity was load-bearing and pinned by NOTHING.

    Measured pre-fix: returning `_DANGLING` instead of `_UNREADABLE` at the bound turned a
    beyond-bound chain's deletion from RED to GREEN — code in no file under `archive/` — while ALL
    101 targeted `tests/test_archive.py` rows stayed green. The reviewer's mutation was the only
    thing that could see it: a bound that cannot tell "unknown" from "empty" is fail-OPEN.

    Both readings are asserted, so the polarity cannot drift again: the read says UNKNOWN, the
    cancelling asker treats UNKNOWN as content, and deleting the whole chain is a removal.

    Mutation: `return _UNREADABLE` -> `return _DANGLING` at the bound -> RED here.

    The SIBLING row `test_a_link_CHAIN_PAST_the_HOP_BOUND_that_reaches_code_is_a_removal` measures
    the same deletion; this one adds the two datum identities beside it, so the polarity is stated
    in the same place as the verdict it produces.
    """
    _chain(tree, arch._LINK_HOPS + 1)
    datum = arch._blob_following_links(tree, "HEAD", "tortoise/x/__init__.py")
    assert datum is arch._UNREADABLE, f"beyond the bound is UNKNOWN, not empty: {datum!r}"
    assert arch._content_or_link(tree, "HEAD", "tortoise/x/__init__.py", datum) is True, (
        "an unknown carrier must not cancel a removal"
    )

    for extra in ("tortoise/x", "tortoise/real.py"):
        subprocess.run(["git", "rm", "-r", "-q", extra], cwd=tree, check=True)
    for i in range(arch._LINK_HOPS):
        subprocess.run(["git", "rm", "-q", f"tortoise/lc{i}.py"], cwd=tree, check=True)
    _commit(tree, ".")

    problems = arch.shrink_violations(tree, base_ref="HEAD~1")
    assert any(p.startswith("tortoise.x:") and "REMOVED" in p for p in problems), (
        f"a chain past the bound is UNREADABLE, never 'nothing was here': {problems}"
    )


def test_a_chain_BEYOND_the_HOP_BOUND_names_the_BOUND_not_a_working_tree_diff(tree: Path) -> None:
    """P3 (round-25): the writer refused an over-bound chain by asserting two false facts.

    Measured pre-fix on a CLEAN tree whose commit holds the code through the chain:
    "no commit on main holds this code at tortoise/x/__init__.py — the working tree differs from
    every commit there" — both halves false, and the remedy (commit the source) cannot clear it.
    `holds` now answers None for "could not be evaluated", and the refusal names the bound.

    Mutation: make `holds` return False for `_UNREADABLE` -> RED here.
    """
    _chain(tree, arch._LINK_HOPS + 1)
    with pytest.raises(arch.ArchiveRefused) as refused:
        arch.archive_symbol(arch.MODULE, "tortoise.x", "batch-a", "r", root=tree)
    message = str(refused.value)
    assert "_LINK_HOPS" in message, f"the refusal must name the bound it hit: {message}"
    assert "the working tree differs from every commit" not in message, (
        f"a clean tree must not be told it differs from every commit: {message}"
    )


def test_an_UNREADABLE_candidate_does_not_MASK_the_working_tree_refusal(tree: Path) -> None:
    """P2 (round-25): widening `_UNREADABLE` made `anchor_sha` suppress the true refusal.

    `_UNREADABLE` used to mean exactly "hop bound exhausted", and the refusal that names it was
    written then. Once it also covered an ABSOLUTE/escaping target and an unreadable mode, an
    unreadable candidate could out-shout a candidate that was READ and did not hold the code —
    printing "This is NOT a working-tree difference, and committing the source will not change
    it", both halves false, while the real remedy (commit the source) was never shown. A definite
    `False` is hard evidence and decides.

    ⛔ `holds` IS INJECTED HERE, SO THIS ROW PINS THE BRANCH SELECTION, NOT THE TREE (round-25
    review, P3 — the fixture's own tree is clean; only the stub makes the newest candidate
    disagree). The mirrored direction — a CLEAN tree whose NEWEST carrier is genuinely unreadable
    while an older commit disagrees — is the shape an order-blind gate got wrong, and it has its
    own row below (`test_a_CLEAN_tree_whose_newest_carrier_is_unreadable_names_the_READ`).

    Mutation: key the arm on "any candidate returned False" instead of the NEWEST evaluated one
    -> the mirrored row below REDs (this one stays green, which is exactly why it needed a peer).
    """
    for body in ("def real(x):\n    return x * 2\n", "def real(x):\n    return x + 1\n"):
        (tree / "tortoise" / "real.py").write_text(body, encoding="utf-8")
        _commit(tree, "tortoise/real.py")
    shas = subprocess.run(
        ["git", "rev-list", "main", "--", "tortoise/real.py"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(shas) >= 2, f"the fixture needs two commits that touched the path: {shas}"
    unreadable, readable_disagreement = shas[-1], shas[0]

    def holds(candidate: str) -> bool | None:
        # The OLDEST commit cannot be read; the NEWEST was read and does not hold this body.
        return None if candidate == unreadable else False

    with pytest.raises(arch.ArchiveRefused) as refused:
        arch.anchor_sha(tree, ["tortoise/real.py"], holds=holds)
    message = str(refused.value)
    assert "the working tree differs from every commit" in message, (
        f"a readable candidate that disagreed must decide the refusal: {message}"
    )
    assert "cannot be READ" not in message, (
        f"the unreadable arm must not out-shout a readable disagreement: {message}"
    )
    assert readable_disagreement, "the readable candidate is the one that must decide"


def test_the_unREADABLE_refusal_names_its_CAUSES_not_only_the_hop_bound(tree: Path) -> None:
    """P2 (round-25): the refusal asserted "exceeds `_LINK_HOPS`" for a ONE-hop absolute link.

    Measured pre-fix: a carrier whose single link target was ABSOLUTE was refused with "the
    carrier's link chain … exceeds `_LINK_HOPS` (8) hops" — a fact about a chain that is one hop
    long, and a remedy ("shorten the chain") that cannot apply. The read now returns
    `_UNREADABLE` for three distinct causes, so the message names all three.

    Mutation: restore the "exceeds `_LINK_HOPS` … shorten the chain" wording -> RED here.
    """
    (tree / "tortoise" / "real.py").write_text("def real(x):\n    return x * 2\n", encoding="utf-8")
    _commit(tree, "tortoise/real.py")
    sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=tree, capture_output=True, text=True, check=True
    ).stdout.strip()

    def holds(candidate: str) -> bool | None:
        return None  # one candidate, unreadable

    with pytest.raises(arch.ArchiveRefused) as refused:
        arch.anchor_sha(tree, ["tortoise/real.py"], holds=holds)
    message = str(refused.value)
    assert sha, "the fixture must have a commit for the carrier"
    assert "cannot be READ" in message, f"the refusal must say the read failed: {message}"
    assert "ABSOLUTE" in message, f"the refusal must name the target shape it can hit: {message}"
    assert "mode cannot be read" in message, f"the refusal must name the unreadable-mode cause: {message}"


def test_a_CLEAN_tree_whose_newest_carrier_is_unreadable_names_the_READ(tree: Path) -> None:
    """P2 (round-25): an order-BLIND gate blamed the working tree on a CLEAN tree.

    Measured pre-fix, `git status --short` and `git diff HEAD` both empty: the newest carrier is a
    one-hop ABSOLUTE link (unreadable) and an OLDER commit holds a regular file with a different
    body. Keying the arm on "any candidate returned False" made the older commit's ordinary
    HISTORY decide, and the caller was told "the working tree differs from every commit there …
    Commit the source" — both halves false, and the printed remedy cannot clear it, because
    committing the identical absolute link keeps it unreadable. This is a REAL fixture, not a
    stubbed predicate: the newest commit's carrier IS the working tree's.

    Mutation: gate on `false_here` (any readable disagreement) instead of the newest evaluated
    candidate -> RED here.
    """
    (tree / "tortoise" / "real.py").write_text("def real(x):\n    return x * 2\n", encoding="utf-8")
    (tree / "tortoise" / "x").mkdir()
    (tree / "tortoise" / "x" / "__init__.py").write_text(
        "def real(x):\n    return x + 1\n", encoding="utf-8"
    )
    _commit(tree, "tortoise/real.py", "tortoise/x/__init__.py")

    # the NEWEST carrier: ABSOLUTE, so `git show` cannot address it at all
    (tree / "tortoise" / "x" / "__init__.py").unlink()
    (tree / "tortoise" / "x" / "__init__.py").symlink_to(str(tree / "tortoise" / "real.py"))
    _commit(tree, "tortoise/x/__init__.py")

    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tree, capture_output=True, text=True, check=True
    ).stdout
    assert dirty == "", f"the fixture must be CLEAN, or it measures the other arm: {dirty!r}"

    body = "def real(x):\n    return x * 2\n"
    holds = arch.block_holds(
        tree, arch.MODULE, "tortoise.x", [("tortoise/x/__init__.py", body, None)]
    )
    with pytest.raises(arch.ArchiveRefused) as refused:
        arch.anchor_sha(tree, ["tortoise/x/__init__.py"], holds=holds)
    message = str(refused.value)
    assert "cannot be READ" in message, (
        f"the newest carrier is unreadable, so the READ is what failed: {message}"
    )
    assert "the working tree differs from every commit" not in message, (
        f"a clean tree must not be told it differs from every commit: {message}"
    )
