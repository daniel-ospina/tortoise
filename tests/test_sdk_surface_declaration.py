"""The declared SDK surface is a GATE, and these are the three verdicts it must give.

#10034 / #4282 phase 1.1. `tortoise/__all__.py` declares the 40 approved SDK methods (the
owner's ruling, `docs/product/beta-sdk-surface.md`), the tracked gaps, and the off-surface
backlog. `tools/sdk_surface_declaration.py` holds the line.

What each test is for, and why the naive version of it would prove nothing:

  1. **The declaration is the owner's list.** A literal 40, and set-equality with the
     document in BOTH directions. `len(__all__) == len(parse(doc))` would pass on two
     different sets of 40.
  2. **An ADDED public method fails.** The live set is the real one plus one name that is on
     neither `__all__` nor the backlog — the exact shape of "somebody added a method".
  3. **A DECLARED method that does not exist fails.** One approved, implemented method is
     removed from the live set. This is the mirror defect, and a gate tested only in one
     direction is a gate that permits the other.
  4. **A no-op passes.** Without this every failure above is satisfiable by a gate that
     always reds.
  5. **The verdict is not vacuous.** The SAME added name passes once it is in the backlog and
     fails only because it is absent — so the failure is caused by the declaration, not by the
     test's other machinery. This is the in-code half of the requester's non-vacuity check;
     the other half (a real `def` in `tortoise/sdk.py`) is in the PR body.

Nothing here types a name list: the declaration, the document and the class are all read.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import sdk_surface_declaration as surface  # noqa: E402

DECLARATION = ROOT / "tortoise" / "__all__.py"
GATE = ROOT / "tools" / "sdk_surface_declaration.py"

#: A LITERAL on purpose: `len(declaration["__all__"]) == len(doc_names)` would be satisfied
#: by any two lists of the same length, and the whole point is that this number is the
#: owner's ruling and not a coincidence of the file.
APPROVED_COUNT = 40


def _declaration() -> dict:
    return surface.load_declaration(DECLARATION)


def _live() -> set[str]:
    return surface.measure_live()


def _problems(live: set[str] | None = None, **overrides) -> list[str]:
    declaration = _declaration()
    fields = {
        "declared": declaration["__all__"],
        "pending": declaration["APPROVED_PENDING"],
        "off_surface": declaration["OFF_SURFACE"],
        "pending_high_water": declaration["PENDING_HIGH_WATER"],
        "off_surface_high_water": declaration["OFF_SURFACE_HIGH_WATER"],
    }
    fields.update(overrides)
    return surface.problems(
        **fields,
        live=_live() if live is None else live,
        doc_names=surface.approved_from_doc(),
    )


# ── 1. the declaration IS the owner's list ────────────────────────────────────────


def test_the_declaration_carries_the_owners_40_methods():
    declaration = _declaration()
    doc_names = surface.approved_from_doc()

    assert len(declaration["__all__"]) == APPROVED_COUNT
    assert len(doc_names) == APPROVED_COUNT
    # Both directions, not a count: a swap keeps the count and changes the contract.
    assert set(declaration["__all__"]) == set(doc_names)
    # The document's order is the reviewable order; a reorder is not a surface change, but a
    # declaration that reorders silently makes the diff of a real change unreadable.
    assert list(declaration["__all__"]) == list(doc_names)


def test_the_gap_list_and_the_backlog_partition_the_live_class():
    declaration = _declaration()
    live = _live()
    approved_live = {n for n in declaration["__all__"] if surface.resolves(n, live)}

    # Every live method is either approved or on the removal backlog; nothing is uncounted.
    # (`Tortoise` is the constructor row: declared and resolved, but not a live METHOD name.)
    assert (approved_live & live) | set(declaration["OFF_SURFACE"]) == live
    assert not approved_live & set(declaration["OFF_SURFACE"])
    # The gaps are exactly the approved names that do not exist yet.
    assert set(declaration["APPROVED_PENDING"]) == set(declaration["__all__"]) - approved_live


# ── 2. an added public method fails ──────────────────────────────────────────────


def test_an_added_public_method_that_is_not_declared_fails():
    live = _live() | {"probe_undeclared_method"}

    problems = _problems(live=live)

    assert any("probe_undeclared_method" in p and "addition" in p for p in problems), problems


def test_the_addition_failure_is_NOT_vacuous():
    """The same added name passes once the declaration accounts for it.

    This is what separates "the gate reported an addition" from "the gate reds on anything
    that moves": the ONLY difference between this case and the failing one above is the
    declaration's own content.
    """
    added = "probe_undeclared_method"
    declaration = _declaration()

    assert _problems(live=_live() | {added})  # absent from the declaration -> red
    assert not _problems(  # listed in the backlog -> green, on the same live set
        live=_live() | {added},
        off_surface=declaration["OFF_SURFACE"] + [added],
        off_surface_high_water=len(declaration["OFF_SURFACE"]) + 1,
    )


def test_an_addition_cannot_be_absorbed_into_the_backlog():
    """Listing a new method in the backlog trips the pin — the cheapest bypass, closed."""
    added = "probe_undeclared_method"
    declaration = _declaration()

    problems = _problems(
        live=_live() | {added},
        off_surface=declaration["OFF_SURFACE"] + [added],
    )

    assert any("OFF_SURFACE grew" in p for p in problems), problems


# ── 3. a declared method that does not exist fails ───────────────────────────────


def test_a_declared_method_that_does_not_exist_fails():
    declaration = _declaration()
    live = _live()
    implemented = sorted(
        n for n in declaration["__all__"] if n != surface.CONSTRUCTOR and n in live
    )
    assert implemented, "the declaration must have at least one implemented approved method"
    gone = implemented[0]

    problems = _problems(live=live - {gone})

    assert any(gone in p and "may not vanish" in p for p in problems), problems


def test_an_approved_method_cannot_be_reclassified_as_a_gap():
    """Deleting an approved method and marking it 'pending' would satisfy the set equality."""
    declaration = _declaration()
    live = _live()
    implemented = sorted(
        n for n in declaration["__all__"] if n != surface.CONSTRUCTOR and n in live
    )
    gone = implemented[0]

    problems = _problems(
        live=live - {gone},
        pending=declaration["APPROVED_PENDING"] + [gone],
        off_surface=[n for n in declaration["OFF_SURFACE"] if n != gone],
    )

    assert any("APPROVED_PENDING grew" in p for p in problems), problems


def test_a_gap_that_was_implemented_must_leave_the_gap_list():
    declaration = _declaration()
    gap = declaration["APPROVED_PENDING"][0]

    problems = _problems(live=_live() | {gap})

    assert any(gap in p and "DOES exist" in p for p in problems), problems


# ── 4. a no-op passes ────────────────────────────────────────────────────────────


def test_an_unchanged_surface_passes():
    assert _problems() == []


def test_a_removal_from_the_backlog_passes_WITHOUT_touching_the_declaration():
    """The migration direction: a method leaves the class and the record needs no edit.

    #10034's complaint about the old workflow was that every removal was a hand edit. The
    frozen record removes that: the record says what WAS there, and the gate accounts for
    the live class as `record & live`.
    """
    declaration = _declaration()
    dropped = declaration["OFF_SURFACE"][0]

    assert not _problems(live=_live() - {dropped})


def test_a_RENAME_or_swap_is_caught():
    """The defect a `record == live − approved` rule CANNOT see (found in review).

    One method leaves the class and one unapproved method takes its place: the set lengths
    are unchanged, so a cardinality rule and an equality rule both stay green. With a frozen
    record the new name is in neither declared set and is NAMED.
    """
    declaration = _declaration()
    gone = declaration["OFF_SURFACE"][0]
    renamed_in = "probe_renamed_method"

    problems = _problems(live=(_live() - {gone}) | {renamed_in})

    assert any(renamed_in in p and "rename" in p for p in problems), problems


# ── the CLI, on the real evidence ────────────────────────────────────────────────


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True, cwd=ROOT
    )


def test_the_cli_is_green_on_the_checked_in_declaration():
    result = _run("check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_the_cli_reds_on_a_doctored_declaration(tmp_path):
    """A declaration that drops a live method from its backlog is an ADDITION, and reds."""
    text = DECLARATION.read_text(encoding="utf-8")
    declaration = _declaration()
    dropped = declaration["OFF_SURFACE"][0]
    doctored = tmp_path / "__all__.py"
    doctored.write_text(
        text.replace(f'    "{dropped}",\n', "", 1), encoding="utf-8"
    )

    result = _run("check", "--declaration", str(doctored))

    assert result.returncode == 1, result.stdout + result.stderr
    assert dropped in result.stdout
    assert "addition" in result.stdout


def test_the_cli_refuses_when_the_declaration_is_missing(tmp_path):
    result = _run("check", "--declaration", str(tmp_path / "absent.py"))

    assert result.returncode == 1
    assert "REFUSED" in result.stdout


def test_the_cli_refuses_when_the_document_no_longer_yields_the_table(tmp_path):
    truncated = tmp_path / "beta-sdk-surface.md"
    truncated.write_text("# no table here\n", encoding="utf-8")

    result = _run("check", "--doc", str(truncated))

    assert result.returncode == 1
    assert "REFUSED" in result.stdout


# ── and the other surface tools read the same declaration (the single-source claim) ──


def test_build_doc_REFUSES_when_the_class_disagrees_with_the_declaration(monkeypatch):
    """The refusal that guards every future re-cut (`cut` cannot run while they disagree).

    Review finding: the committed manifest comparison below passes even if the refusal is
    deleted, so the behaviour change that can break `cut` had no test. This adds one, by
    attaching a public method in-process and asserting `build_doc` refuses and NAMES it.
    """
    from tortoise.sdk import TortoiseSDK

    from tools import surface_manifest

    monkeypatch.setattr(TortoiseSDK, "probe_undeclared_review_method", lambda self: None, raising=False)

    with pytest.raises(surface_manifest.SurfaceEvidenceUnreadable, match="probe_undeclared_review_method"):
        surface_manifest.build_doc()


def test_the_manifest_is_cut_from_the_declaration():
    """The committed manifest's `sdk:` rows are exactly the declared live set.

    Asserted by reading the declaration the manifest was cut from, not by trusting the
    comment beside the code. (The REFUSAL half is
    `test_build_doc_REFUSES_when_the_class_disagrees_with_the_declaration`.)
    """
    import yaml

    manifest = yaml.safe_load((ROOT / "config" / "surface-manifest.yml").read_text(encoding="utf-8"))
    sdk_rows = {
        str(row["name"])[len("sdk:"):]
        for row in manifest["rows"]
        if str(row.get("name", "")).startswith("sdk:")
    }

    assert sdk_rows == surface.declared_live_methods()
    assert manifest["counts"]["sdk_public_methods"] == len(surface.declared_live_methods())


def test_the_target_check_reads_the_sdk_target_from_the_declaration():
    """#10034 requirement 3: `40` comes from the declaration, not from prose parsing."""
    import importlib.util

    path = ROOT / "tools" / "surface-target-check.py"
    spec = importlib.util.spec_from_file_location("surface_target_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    targets = module.read_targets()

    assert targets["sdk"] == len(_declaration()["__all__"]) == APPROVED_COUNT
    assert Path(targets["sources"]["sdk"]) == DECLARATION


def test_the_constructor_row_resolves_to_the_class_by_evidence():
    """Row 1 is written `Tortoise(...)` — the constructor, not a method on the class.

    The earlier version asserted `resolves("Tortoise", live) is True`, which is tautological:
    it passes on any tree, including one with no constructor at all (review finding). This
    asserts the OBJECT the row resolves to, so a tree without the class fails the row.
    """
    from tortoise.sdk import TortoiseSDK

    assert surface.resolve("Tortoise", _live()) is surface.CONSTRUCTOR
    assert surface.resolve("Tortoise", _live()) != ""
    assert callable(TortoiseSDK)
    # A name in neither the declaration nor the class resolves to nothing.
    assert surface.resolve("no_such_method_at_all", _live()) == ""


# ── the re-cut must not re-assert an OBSERVATION it cannot make (#10034 review P1) ──


def test_a_recut_carries_usage_observations_forward(tmp_path, monkeypatch):
    """`used_by` / `reason` are OBSERVATIONS (telemetry + corpora), not derivations.

    Measured on the review host: `skills/` did not resolve and the telemetry log differed,
    so a re-cut rewrote 103 rows — 58 flipping `agents, …` to `never called, …` — while no
    tool, registration or caller had moved. `check` cannot see it (`NON_DERIVABLE_ROW_KEYS`),
    so the carry is the only thing standing between a re-cut on a bare host and a frozen
    baseline that misreports what the owner's customers use.
    """
    import yaml

    from tools import surface_manifest

    previous = tmp_path / "surface-manifest.yml"
    previous.write_text(
        yaml.safe_dump(
            {
                "manifest_version": 1,
                "issue": 3863,
                "cut_at_commit": "deadbeef",
                "allowed_transforms": [],
                "response_fields": [],
                "approval_status": "approved",
                "approval_principal": "daniel-ospina",
                "approval_pr": "4120",
                "approval_date": "2026-09-18",
                "counts": {"tools": 1, "sdk_public_methods": 0, "retired": 0},
                "high_water_counts": {"tools": 1, "sdk_public_methods": 0},
                "retired": [],
                "rows": [
                    {
                        "name": "tortoise_query",
                        "used_by": "agents, tooling, skill-docs, docs",
                        "reason": "called by agents",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(surface_manifest, "MANIFEST_FILE", previous)
    rows = [
        {"name": "tortoise_query", "used_by": "never called, tooling, docs", "reason": "not called over MCP"},
        {"name": "tortoise_brand_new", "used_by": "never called", "reason": "new row, nothing to carry"},
    ]

    carried = surface_manifest._carried_observations(rows, carry=True)

    assert carried == ["tortoise_query"]
    assert rows[0]["used_by"] == "agents, tooling, skill-docs, docs"
    assert rows[0]["reason"] == "called by agents"
    # A name with no previous row keeps what was derived: carrying is not a blanket freeze.
    assert rows[1]["used_by"] == "never called"

    rewritten = [
        {"name": "tortoise_query", "used_by": "never called, tooling, docs", "reason": "not called over MCP"}
    ]
    assert surface_manifest._carried_observations(rewritten, carry=False) == []
    assert rewritten[0]["used_by"] == "never called, tooling, docs"
