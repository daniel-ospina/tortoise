"""The SDK public-surface declaration is derived from the code and cannot drift silently.

Phase 0.4 + 1.1 of tortoise #4282. `tools/sdk_surface.py` declares the public method set of
`TortoiseSDK`; these tests are the proof that the declaration is a *gate* and not a comment.

Three properties, and the reason each needs its own test:

  1. **It describes today.** The declaration equals the live class, asserted against a LITERAL
     count. A test whose expected value is imported from the thing under test asserts
     nothing: `len(derive()) == len(derive())` passes even when the surface has tripled.
  2. **It reds in BOTH directions.** An undeclared method and a declared-but-gone method are
     different defects, and #3863's guard was only ever tested in one of them. Both are
     exercised here by mutating a *copy* of the declaration, never the checked-in one.
  3. **The gate is not silently disarmed.** A `return 0` (or a dropped comparison) in
     `main()` must red these tests. `test_check_exits_nonzero_on_drift` mutates the artifact
     rather than asserting a clean run — the mistake PR #4177's review caught in the sibling
     `test_bridge_table.py`.

Everything here reads the declaration from disk or derives it from the AST. Nothing re-types
a name list, so these tests cannot drift from the artifact they guard.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GENERATOR = ROOT / "tools" / "sdk_surface.py"
DECLARATION = ROOT / "config" / "sdk-surface.json"
MANIFEST = ROOT / "config" / "surface-manifest.yml"
DOC = ROOT / "docs" / "product" / "sdk-surface-declaration.md"

# The frozen count for THIS phase. A literal on purpose (see the module docstring): asserting
# `len(live) == len(live)` would be satisfied by any surface at all. When a later phase of
# #4282 lands the 40-method target, this number is expected to change — in the same PR that
# re-cuts the approved baseline, so the change is a reviewed decision and not a silent drift.
DECLARED_COUNT = 150


def _declaration() -> dict:
    return json.loads(DECLARATION.read_text(encoding="utf-8"))


def _declared() -> set[str]:
    return set(_declaration()["methods"])


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def _candidate(tmp_path: Path, methods: set[str]) -> Path:
    """A declaration file with a deliberately wrong method set. Never the checked-in one."""
    doc = _declaration()
    doc["methods"] = sorted(methods)
    doc["count"] = len(methods)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


# --- 1. it describes today -----------------------------------------------------------------


def test_the_declaration_describes_the_live_class_exactly():
    """A hand-typed or stale declaration cannot survive contact with the class."""
    import tools.sdk_surface as sdk_surface

    live = sdk_surface.declared_from_ast()
    assert _declared() == live, (
        "config/sdk-surface.json and the `TortoiseSDK` class body disagree. "
        "Run: uv run python tools/sdk_surface.py"
    )
    assert len(live) == DECLARED_COUNT, (
        f"the public `TortoiseSDK` surface is {len(live)} methods, not the {DECLARED_COUNT} "
        "this phase declared. A surface change is a reviewed decision: re-cut the approved "
        "baseline (config/surface-manifest.yml) and update DECLARED_COUNT in the same PR."
    )


def test_the_declaration_matches_runtime_reflection():
    """The AST walk and `dir()`+callable agree TODAY — and nothing else checks that.

    They are different questions: the AST sees what is DEFINED in the class body, reflection
    sees what is REACHABLE on the class (including attributes attached outside it —
    `tortoise/sdk.py` already does that once, privately). If a public attribute is attached
    outside the class body, reflection grows and the AST walk does not; that divergence would
    leave one gate green and the other red with no test naming the cause.
    """
    import tools.sdk_surface as sdk_surface

    ast_view = sdk_surface.declared_from_ast()
    reflected = sdk_surface.reflected_from_runtime()

    assert ast_view == reflected, (
        "the AST class-body walk and runtime reflection disagree about the public surface: "
        f"defined-not-reachable={sorted(ast_view - reflected)}, "
        f"reachable-not-defined={sorted(reflected - ast_view)}. "
        "A public attribute attached outside the `TortoiseSDK` class body is invisible to "
        "the AST walk; declare it as a `def` in the class body (or make it private)."
    )


def test_the_declaration_matches_the_approved_baseline():
    """Identity and approval are two artifacts; if they disagree, one of them is wrong.

    `config/surface-manifest.yml` is the owner-approved set (#3863) and stays the merge gate.
    This declaration is not a second approval gate — it must simply agree with the first, so
    a re-cut of one without the other is caught here rather than in production.
    """
    import tools.sdk_surface as sdk_surface

    approved = sdk_surface.approved_from_manifest()
    assert _declared() == approved, (
        "config/sdk-surface.json and config/surface-manifest.yml's `sdk:` rows disagree: "
        f"declared-not-approved={sorted(_declared() - approved)}, "
        f"approved-not-declared={sorted(approved - _declared())}"
    )


# --- 1b. EACH comparison is isolated, so dropping one cannot go unnoticed -------------------
#
# All three views agree today, so a test that only shows "the three disagree" cannot tell
# which comparison produced the finding — and a guard that silently STOPPED comparing one of
# them would still exit 0 on every input. These tests each make exactly ONE view
# differ, which is what pins each comparison individually.


def test_a_declaration_disagreeing_only_with_the_APPROVED_baseline_reds(tmp_path):
    """Isolates the approved-baseline comparison: the code views are untouched."""
    import yaml

    import tools.sdk_surface as sdk_surface

    doc = yaml.safe_load(sdk_surface.MANIFEST_FILE.read_text(encoding="utf-8"))
    live = _declared()
    target = sorted(live)[0]
    for row in doc["rows"]:
        if str(row.get("name", "")).startswith("sdk:") and row.get("method") == target:
            row["method"] = None
            break
    else:  # pragma: no cover - the fixture row must exist
        raise AssertionError(f"no `sdk:` row for {target} in the approved baseline")
    modified = tmp_path / "baseline.yml"
    modified.write_text(yaml.safe_dump(doc, sort_keys=False, width=110), encoding="utf-8")

    result = _run("--check", "--manifest", str(modified))

    assert result.returncode == 1, (
        "a declaration that disagrees only with the approved baseline did not red — the "
        "baseline comparison is disarmed"
    )
    assert target in result.stdout


def test_a_declaration_disagreeing_only_with_REFLECTION_reds(monkeypatch):
    """Isolates the reflection comparison: AST and the baseline are both left correct."""
    import tools.sdk_surface as sdk_surface

    real_reflected = sdk_surface.reflected_from_runtime()
    # A public method attached to the class OUTSIDE the class body: reachable to `dir()`,
    # invisible to the AST walk, and absent from the approved baseline. This is the exact
    # divergence class the reconciliation exists for.
    phantom = "a_method_attached_outside_the_class_body"
    monkeypatch.setattr(
        sdk_surface, "reflected_from_runtime", lambda: real_reflected | {phantom}
    )

    problems = sdk_surface.reconcile_declaration(
        _declared(),
        sdk_surface.declared_from_ast(),
        sdk_surface.reflected_from_runtime(),
        sdk_surface.approved_from_manifest(),
    )

    assert problems, (
        "a public method reachable by reflection but defined outside the class body did not "
        "red — the reflection comparison is disarmed"
    )
    assert any(phantom in problem for problem in problems), (
        f"the failure did not name the reflection-only method: {problems}"
    )


# The AST comparison is isolated by `test_a_vanished_public_method_is_a_failure` above: it
# repoints `_sdk_targets` at a mutated copy of `sdk.py` while reflection and the approved
# baseline keep the real set, so a finding there can only come from the AST comparison.


# --- 2. it reds in BOTH directions ----------------------------------------------------------


def test_an_undeclared_public_method_is_a_failure(tmp_path):
    """Direction A — a method ON THE CLASS that the declaration does not name.

    This is the #4282 defect: a public method appearing without a declaration. Simulated by
    removing one name from a COPY of the declaration, which is the same set difference as
    adding a method to the class without declaring it.
    """
    live = _declared()
    target = sorted(live)[0]
    candidate = _candidate(tmp_path, live - {target})

    result = _run("--check", "--declaration", str(candidate))

    assert result.returncode == 1, (
        "a live public method missing from the declaration did not red the check — the gate "
        "is disarmed in the `undeclared` direction"
    )
    assert target in result.stdout, (
        f"the failure did not name the offending method {target!r}; an unnamed finding is not "
        "actionable"
    )


def test_a_declared_method_that_no_longer_exists_is_a_failure(tmp_path):
    """Direction B — a declaration for a method the class does not have.

    The REMOVAL direction. It is the half #3863's guard was never tested in: an
    `test_..._when_a_public_method_is_added` existed, a `..._when_removed` did not. Without
    this test, the `baseline - declared` comparison could be deleted (or short-circuited) and
    every other test would stay green.
    """
    live = _declared()
    phantom = "a_method_that_no_longer_exists"
    assert phantom not in live, "the phantom name must not already be declared"
    candidate = _candidate(tmp_path, live | {phantom})

    result = _run("--check", "--declaration", str(candidate))

    assert result.returncode == 1, (
        "a declared method that no longer exists did not red the check — the removal "
        "direction is disarmed"
    )
    assert phantom in result.stdout, (
        f"the failure did not name the phantom method {phantom!r}"
    )


def test_a_vanished_public_method_is_a_failure(tmp_path, monkeypatch):
    """Direction B, from the CODE side: the class loses a method the declaration names.

    The direction-A test mutates the declaration; this one mutates the *class body* the AST
    walk reads. Both are needed: a guard could compare the declaration against a fixture
    rather than against the code and pass the first test while being blind to the class.

    Hermetic by construction — the mutated source is a COPY in `tmp_path`, injected by
    repointing `tools.bridge_table.SDK_SRC`. No file in the repo is written, so a killed test
    cannot leak a mutation into the tree.
    """
    import tools.bridge_table as bridge_table
    import tools.sdk_surface as sdk_surface

    live = _declared()
    target = "create_point"
    assert target in live, "the fixture name must actually be on the surface"

    source = bridge_table.SDK_SRC.read_text(encoding="utf-8")
    mutant = _rename_in_class_body(source, sdk_surface.CLASS_NAME, target, "_" + target)
    assert mutant != source, "the mutation did not apply — the test would be vacuous"
    copy = tmp_path / "sdk.py"
    copy.write_text(mutant, encoding="utf-8")
    monkeypatch.setattr(bridge_table, "SDK_SRC", copy)

    live_after = sdk_surface.declared_from_ast()
    assert target not in live_after, "the AST walk still sees the renamed method"

    problems = sdk_surface.reconcile_declaration(
        _declared(),
        live_after,
        sdk_surface.reflected_from_runtime(),
        sdk_surface.approved_from_manifest(),
    )
    assert problems, "a method that vanished from the class body did not red the check"
    assert any(target in p for p in problems), (
        f"the failure did not name the vanished method {target!r}: {problems}"
    )


def _rename_in_class_body(source: str, class_name: str, old: str, new: str) -> str:
    """Rewrite `def old(` to `def new(` for a method of `class_name`, located via the AST.

    AST-driven so the edit lands on the real `def` and not on a call site, a comment, or a
    helper-class method that happens to share the name.
    """
    import ast

    tree = ast.parse(source)
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for sub in node.body:
            if isinstance(sub, ast.FunctionDef) and sub.name == old:
                lines = source.splitlines(keepends=True)
                index = sub.lineno - 1
                rest = lines[index][sub.col_offset + len(f"def {old}("):]
                lines[index] = lines[index][: sub.col_offset] + f"def {new}(" + rest
                return "".join(lines)
    raise AssertionError(f"no `def {old}` found in the {class_name} class body")


# --- 3. the gate cannot be silently disarmed ------------------------------------------------


def test_check_exits_nonzero_on_a_drifted_document():
    """`--check` must FAIL on drift, not merely succeed when clean.

    The sibling `test_bridge_table.py` carries this test for the same reason (found in the
    PR #4177 review): if every test of `--check` asserts a zero exit, a mutation making
    `main()` return 0 unconditionally disarms the gate and the suite stays green.
    """
    with tempfile.TemporaryDirectory() as td:
        backup = Path(td) / "sdk-surface-declaration.md"
        shutil.copy2(DOC, backup)
        try:
            DOC.write_text(DOC.read_text(encoding="utf-8") + "\nDRIFT\n", encoding="utf-8")
            result = _run("--check")
            assert result.returncode != 0, (
                "--check exited 0 on a deliberately drifted document; the gate is disarmed"
            )
        finally:
            shutil.copy2(backup, DOC)


def test_check_reds_on_a_hand_edited_metadata_field():
    """A hand-edit the NAME-SET comparison cannot see must still be caught.

    Found by mutation (M13): dropping the declaration-text comparison survived every test,
    because a corrupted `count`, `issue`, `class` or `derivation` leaves the `methods` list
    intact — so all three name-set comparisons stay clean and a machine consumer reads a
    wrong number. The text comparison is the only thing that catches it.

    This test uses the DEFAULT spelling. The same defect reached through a non-identical
    spelling used to fail OPEN (see
    `test_check_reds_on_a_hand_edited_metadata_field_at_a_non_identical_path`), because the
    text comparison was gated on `Path` equality rather than on the file that was read.

    The mutation is applied to the checked-in file and restored from a /tmp copy in
    `finally` — never `git checkout`.
    """
    original = DECLARATION.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        backup = Path(td) / "sdk-surface.json"
        shutil.copy2(DECLARATION, backup)
        try:
            doc = _declaration()
            doc["count"] = len(doc["methods"]) + 1  # wrong number, SAME name set
            DECLARATION.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            result = _run("--check")
            assert result.returncode == 1, (
                "a hand-edited `count` in the declaration did not red — the metadata is "
                "unguarded and a machine consumer would read a wrong number"
            )
        finally:
            shutil.copy2(backup, DECLARATION)
    assert DECLARATION.read_text(encoding="utf-8") == original, (
        "the declaration was not restored; the worktree now holds a mutation"
    )


@pytest.mark.parametrize("spelling", ["relative", "dot-prefixed", "symlink"])
def test_check_reds_on_a_hand_edited_metadata_field_at_a_non_identical_path(
    tmp_path, spelling
):
    """The text comparison must run on the path that was READ, not on a specific spelling.

    Found by review of PR #4516: both text comparisons were gated on `Path` equality with
    the absolute default. A relative path, a `./`-prefixed path, or a symlink that resolves
    to the SAME file compared unequal, so a corrupted `count` was silently accepted:

        python tools/sdk_surface.py --check                             # exit 1
        python tools/sdk_surface.py --check --declaration config/sdk-surface.json  # exit 0

    The `methods` list is left intact, so the three name-set comparisons stay clean and only
    the text comparison can catch the corruption. HERMETIC: the corrupted declaration is a
    temp copy (never the checked-in artifact), so a killed test cannot leak a mutation.
    """
    candidate = _corrupted_declaration(tmp_path)
    spelling_arg = _path_spelling(candidate, spelling, tmp_path, "linked-declaration.json")

    result = _run("--check", "--declaration", spelling_arg)

    assert result.returncode == 1, (
        f"a hand-edited `count` was accepted through the {spelling!r} spelling of the "
        "declaration path — the text comparison is gated on path equality and fails open. "
        f"stdout={result.stdout!r}"
    )
    assert "stale" in result.stdout


@pytest.mark.parametrize("spelling", ["relative", "dot-prefixed", "symlink"])
def test_check_reds_on_a_drifted_document_at_a_non_identical_path(tmp_path, spelling):
    """The DOC text comparison must also run on the path that was READ.

    The same fail-open as the declaration side (#4516 review), and the same hermetic shape:
    a drifted temp copy of the doc, reached via a relative, `./`-prefixed, or symlinked
    spelling. The declaration and the name sets stay clean, so only the doc-text comparison
    can catch the drift.
    """
    drifted = tmp_path / "drifted-declaration.md"
    drifted.write_text(DOC.read_text(encoding="utf-8") + "\nDRIFT\n", encoding="utf-8")
    spelling_arg = _path_spelling(drifted, spelling, tmp_path, "linked-declaration.md")

    result = _run("--check", "--doc", spelling_arg)

    assert result.returncode == 1, (
        f"a drifted doc was accepted through the {spelling!r} spelling of the doc path — "
        f"the doc-text comparison is gated on path equality and fails open. "
        f"stdout={result.stdout!r}"
    )
    assert "stale" in result.stdout


def test_check_reds_on_a_hand_edited_metadata_field_with_a_custom_manifest(tmp_path):
    """The text comparison must not be gated on the MANIFEST path either.

    The original fail-open was a THREE-part AND (`is_default_paths` required the declaration,
    the doc AND the manifest to be the default). The declaration and doc legs are pinned by
    the tests above; this pins the manifest leg, so a mutation re-gating the text comparison
    on `args.manifest == MANIFEST_FILE` cannot survive the suite.
    """
    candidate = _corrupted_declaration(tmp_path)
    manifest_copy = tmp_path / "surface-manifest.yml"
    shutil.copy2(MANIFEST, manifest_copy)

    result = _run(
        "--check", "--declaration", str(candidate), "--manifest", str(manifest_copy)
    )

    assert result.returncode == 1, (
        "a corrupted declaration `count` was accepted with a custom manifest path — the "
        f"text comparison is gated on path equality and fails open. stdout={result.stdout!r}"
    )
    assert "stale" in result.stdout


def _corrupted_declaration(tmp_path: Path) -> Path:
    """A temp declaration copy with a wrong `count` and an INTACT `methods` list."""
    doc = _declaration()
    doc["count"] = len(doc["methods"]) + 1  # wrong number, SAME name set
    path = tmp_path / "corrupted-declaration.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def _path_spelling(path: Path, spelling: str, tmp_path: Path, link_name: str) -> str:
    """A non-identical but equivalent spelling of `path`, relative to the repo root."""
    if spelling == "relative":
        return os.path.relpath(path, ROOT)
    if spelling == "dot-prefixed":
        return "./" + os.path.relpath(path, ROOT)
    link = tmp_path / link_name
    link.symlink_to(path)
    return str(link)


def test_the_rendered_doc_does_not_claim_agreement_when_the_views_disagree():
    """The doc's agreement sentence must be derived, not asserted.

    Found by mutation (M17): the sentence was only observable in a disagreement state, so an
    unconditional "all agree" survived every test — because in the clean state both branches
    render the same characters. A doc that claims three views agree while they do not is the
    silent-drift failure this whole artifact exists to prevent, so the claim is pinned
    directly.
    """
    import tools.sdk_surface as sdk_surface

    live = sdk_surface.declared_from_ast()
    disagreed = sdk_surface.render_doc(
        live, live, live | {"a_method_the_baseline_lost"}
    )

    assert "do NOT all agree" in disagreed, (
        "the rendered doc claimed agreement while the approved baseline disagreed"
    )
    assert "— all agree" not in disagreed


def test_the_render_refuses_when_the_code_views_disagree(tmp_path, monkeypatch):
    """`--render` must not freeze a disagreement into an artifact (bridge_table precedent).

    If the AST walk and reflection disagree about what the surface IS, writing the declaration
    would pick one of them silently and the next reader could not tell which.
    """
    import tools.sdk_surface as sdk_surface

    real = sdk_surface.reflected_from_runtime()
    monkeypatch.setattr(
        sdk_surface,
        "reflected_from_runtime",
        lambda: real | {"attached_outside_the_class_body"},
    )
    out_declaration = tmp_path / "sdk-surface.json"
    out_doc = tmp_path / "doc.md"

    rc = sdk_surface.main(
        ["--declaration", str(out_declaration), "--doc", str(out_doc)]
    )

    assert rc == 1, "`--render` wrote a declaration while the code views disagreed"
    assert not out_declaration.exists() and not out_doc.exists(), (
        "`--render` wrote an artifact despite refusing"
    )


def test_check_refuses_when_its_evidence_is_missing(tmp_path):
    """Fail closed (#1382 class): a gate that cannot read its evidence must not pass."""
    result = _run("--check", "--declaration", str(tmp_path / "nope.json"))
    assert result.returncode == 1
    assert "does not exist" in result.stdout + result.stderr
    assert "REFUSED" in result.stdout + result.stderr


def test_check_refuses_when_the_baseline_is_missing(tmp_path):
    """The baseline is evidence too, and a missing one must REFUSE.

    Found by mutation (M10): a version of `approved_from_manifest` that returned an empty set
    instead of raising still exited 1 — but with a *wrong* finding ("the declaration names 150
    methods the baseline does not have"), so the fail-open was masked by an accidental red.
    The exit code alone cannot tell the two apart; the message is what does.
    """
    result = _run("--check", "--manifest", str(tmp_path / "nope.yml"))
    assert result.returncode == 1
    assert "REFUSED" in result.stdout + result.stderr, (
        "a missing approved baseline produced findings rather than a refusal — the gate failed "
        "open and was only accidentally red"
    )
    assert "does not exist" in result.stdout + result.stderr


def test_check_refuses_on_a_malformed_declaration(tmp_path):
    """A file that parses as JSON but is not a declaration is not evidence."""
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a declaration"}', encoding="utf-8")
    result = _run("--check", "--declaration", str(bad))
    assert result.returncode == 1
    assert "malformed" in result.stdout + result.stderr


def test_check_refuses_on_a_malformed_baseline(tmp_path):
    """Same fail-closed rule for the approved baseline."""
    bad = tmp_path / "bad.yml"
    bad.write_text("rows: not-a-list\n", encoding="utf-8")
    result = _run("--check", "--manifest", str(bad))
    assert result.returncode == 1
    assert "malformed" in result.stdout + result.stderr


# --- 4. ALL FOUR evidence surfaces fail closed (#4516 review) ------------------------------
#
# The module claims "a gate that cannot read its evidence must refuse, never skip (#1382
# class)". Before the fix that held for only two of the four surfaces: the AST and reflection
# readers raised raw `FileNotFoundError` / `SyntaxError` / `ImportError` / `RuntimeError` past
# `main()`'s `except SurfaceEvidenceUnreadable` as an uncaught traceback. Each test drives the
# real `main()` so it proves the documented REFUSED message and exit code, not merely that a
# helper raises. Each test FAILS on the pre-fix code (the exception escapes `main()`).


def _refused(rc: int, output: str, view: str) -> None:
    assert rc == 1, f"unreadable {view} evidence did not refuse"
    assert "REFUSED" in output, (
        f"unreadable {view} evidence did not produce the documented REFUSED message; "
        f"output={output!r}"
    )


def test_check_refuses_when_the_ast_source_is_missing(monkeypatch, capsys):
    """Surface 1/4 — `sdk.py` missing: unreadable AST evidence must REFUSE, not traceback."""
    import tools.bridge_table as bridge_table
    import tools.sdk_surface as sdk_surface

    monkeypatch.setattr(bridge_table, "SDK_SRC", Path("/nonexistent/sdk.py"))
    rc = sdk_surface.main(["--check"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    _refused(rc, output, "AST")
    assert "AST view" in output


def test_check_refuses_when_the_ast_source_does_not_parse(monkeypatch, capsys, tmp_path):
    """Surface 2/4 — `sdk.py` unparseable: unreadable AST evidence must REFUSE."""
    import tools.bridge_table as bridge_table
    import tools.sdk_surface as sdk_surface

    bad = tmp_path / "sdk.py"
    bad.write_text("def (:\n", encoding="utf-8")
    monkeypatch.setattr(bridge_table, "SDK_SRC", bad)
    rc = sdk_surface.main(["--check"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    _refused(rc, output, "AST")
    assert "AST view" in output


def test_check_refuses_when_the_sdk_class_is_absent(monkeypatch, capsys):
    """Surface 3/4 — `tortoise.sdk` imports but has no `TortoiseSDK`: must REFUSE."""
    import sys
    import types

    import tools.sdk_surface as sdk_surface

    monkeypatch.setitem(sys.modules, "tortoise.sdk", types.ModuleType("tortoise.sdk"))
    rc = sdk_surface.main(["--check"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    _refused(rc, output, "reflection")
    assert "reflection view" in output


def test_check_refuses_when_the_sdk_import_raises(monkeypatch, capsys):
    """Surface 4/4 — the reflection import raises: must REFUSE, not leak the exception."""
    import sys
    import types

    import tools.sdk_surface as sdk_surface

    class _ExplodingSdk(types.ModuleType):
        def __getattr__(self, name):
            raise RuntimeError("boom importing sdk")

    monkeypatch.setitem(sys.modules, "tortoise.sdk", _ExplodingSdk("tortoise.sdk"))
    rc = sdk_surface.main(["--check"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    _refused(rc, output, "reflection")
    assert "reflection view" in output


def test_check_refuses_when_the_doc_is_unreadable(tmp_path):
    """The doc staleness read is EVIDENCE too: unreadable must REFUSE, not traceback.

    Found by independent review of the #4516 fix (P2, medium): the doc read sat outside the
    `SurfaceEvidenceUnreadable` contract, so `--check --doc <a directory>` raised
    `IsADirectoryError`. Exit was 1, but the documented refusal message was absent — the
    contract is only uniform if EVERY evidence read honours it.
    """
    result = _run("--check", "--doc", str(tmp_path))  # a directory, not a file
    assert result.returncode == 1, "an unreadable doc did not refuse"
    assert "REFUSED" in result.stdout + result.stderr, (
        "an unreadable doc produced a raw traceback rather than the documented refusal"
    )


@pytest.mark.parametrize("label", ["AST", "reflection", "approved"])
def test_the_clean_run_reports_all_three_views(label: str):
    """The gate states what it reconciled, so a silently dropped comparison is visible.

    A check that stops comparing one of its three views would still exit 0 — the report line
    is the only place the loss shows up without a mutation.
    """
    result = _run("--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert label in result.stdout, (
        f"`--check` no longer reports the {label} view; a dropped comparison would be "
        "invisible in the exit code alone"
    )
