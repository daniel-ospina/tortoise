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


def test_a_row_dropped_for_a_RESTORED_sdk_method_is_green(tree: Path) -> None:
    """The same rule for the other two kinds: liveness is per-kind, not module-only."""
    entry = _entry(arch.SDK_METHOD, "old_method")
    arch.save_manifest({"entries": [entry]}, tree / "archive" / "MANIFEST.json")
    frozen = tree / entry["file"]
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text("def old_method(self):\n    return 1\n", encoding="utf-8")
    _commit(tree, ".")
    arch.save_manifest({"entries": []}, tree / "archive" / "MANIFEST.json")
    _commit_drop(tree, entry["file"])

    assert arch.shrink_violations(
        tree, base_ref="HEAD~1", surface=_surface(methods={"old_method"})
    ) == []
    assert arch.shrink_violations(tree, base_ref="HEAD~1", surface=_surface()), (
        "without the method live again the row drop is unrecorded code loss"
    )


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
    """The documented restore path: the row leaves, and the reason is on the record."""
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


def test_a_waiver_must_name_a_real_kind_and_symbol(tree: Path) -> None:
    with pytest.raises(arch.ArchiveRefused, match="not `<kind>:<symbol>`"):
        arch.shrink_violations(tree, base_ref="HEAD", allowed=["tortoise.gone"])


def test_an_unchanged_tree_is_green(tree: Path) -> None:
    """Non-vacuity: the arm must not red an ordinary merge."""
    assert arch.shrink_violations(tree, base_ref="HEAD") == []
    assert arch.shrink_violations(tree) == []  # default branch of the fixture


def test_the_shrink_arm_is_OFF_unless_a_base_ref_is_given(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check(tmp)` keeps its old meaning: an opt-in arm cannot red the depth-1 `test` job."""
    (tree / "tortoise" / "gone.py").write_text("Z = 1\n", encoding="utf-8")
    _commit(tree, "tortoise/gone.py")
    _commit_drop(tree, "tortoise/gone.py")
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

    The base records the row AND this tree still carries it, so "does not record `x` at all"
    would send the operator looking for a removal that never happened.
    """
    _archive_module(tree, "tortoise.gone")
    _commit(tree, ".")

    problems = arch.shrink_violations(
        tree, base_ref="HEAD", allowed=["module:tortoise.gone"]
    )
    waived = [p for p in problems if "waives nothing" in p]
    assert waived, problems
    assert "still carries it" in waived[0], waived[0]
    assert "does not record" not in waived[0], waived[0]


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
    return {
        "tools": set(),
        "methods": set(),
        "forward": set(),
        "sdk_retired": set(),
        "modules": paths,
        "modules_with_a_body": arch._modules_with_a_body(
            paths, lambda path: arch._file_text(tree, path)
        ),
    }


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
