"""The declared SDK surface is a GATE, and these are the verdicts it must give.

#10034 / #4282 phase 1.1. `tortoise/__all__.py` declares the **40 approved SDK methods** —
the owner's ruling (`docs/product/beta-sdk-surface.md`) — and nothing else.
`tools/sdk_surface_declaration.py` holds the line.

What each test is for, and why the naive version of it would prove nothing:

  1. **The declaration is the owner's list.** A literal 40, and set-equality with the
     document in BOTH directions. `len(__all__) == len(parse(doc))` would pass on two
     different sets of 40.
  2. **A second ledger cannot come back.** The previous draft folded the whole live-method
     inventory into the same module (`OFF_SURFACE` + `APPROVED_PENDING`), which made "there is
     a method I never approved" and "the migration has not reached it" the same observation
     and let the gate stay green over the entire off-surface backlog. Loading such a module
     is now a REFUSAL, asserted directly.
  3. **An added public method fails, and the failure is NAMED.** Plus a non-vacuity test: the
     same live set passes once the declaration accounts for the name — so the red is caused
     by the declaration's content, not by the test's other machinery.
  4. **An approved method that does not exist fails, and is NAMED** — the mirror direction,
     with the same non-vacuity pattern. A gate tested in one direction permits the other.
  5. **Today's state is RED, and pinned with the real numbers** (40 approved, 124 live, 112
     off the surface, 27 approved-but-absent). This is the honest reading of #4282; it goes
     green when Phase 2 lands, not before — which is why the three live numbers carry an
     explicit "update this premise" note instead of being restated from the declaration.
  6. **The CONTRACT half is green and is the required-CI invariant** — the declaration is
     still the owner's 40 — and it reds when the declaration is padded or a binding dropped.
  7. **The baseline document is generated, fresh, and says it is not the target.** The gap is
     tracked, never blessed.

Nothing here types a name list: the declaration, the document and the class are all read.
The only literal is the approved COUNT, because that number is the owner's ruling.
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
BASELINE_DOC = ROOT / "docs" / "product" / "sdk-surface-baseline.md"

#: A LITERAL on purpose: `len(declaration["__all__"]) == len(doc_names)` would be satisfied
#: by any two lists of the same length, and the whole point is that this number is the
#: owner's ruling and not a coincidence of the file.
APPROVED_COUNT = 40


def _declaration() -> dict:
    return surface.load_declaration(DECLARATION)


def _live() -> set[str]:
    return surface.measure_live()


def _problems(live: set[str] | None = None, declared: list[str] | None = None) -> list[str]:
    return surface.problems(
        declared=_declaration()["__all__"] if declared is None else declared,
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


def test_the_declaration_declares_nothing_but_the_approved_set():
    """The module's ONLY surface-bearing export is `__all__` — the 40, not a 210-name ledger."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_decl_probe", DECLARATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    exported = {n for n in vars(module) if n == "__all__" or not n.startswith("_")}
    assert exported == {"__all__"}, exported


# ── 2. a second ledger cannot come back ─────────────────────────────────────────


@pytest.mark.parametrize("banned", ["OFF_SURFACE", "APPROVED_PENDING"])
def test_a_second_ledger_in_the_declaration_is_refused(tmp_path, banned):
    """The exact regression from #10034: the surplus blessed by being listed beside the 40."""
    names = tuple(_declaration()["__all__"])
    doctored = tmp_path / "__all__.py"
    doctored.write_text(f"__all__ = {names!r}\n{banned} = ()\n", encoding="utf-8")

    with pytest.raises(surface.DeclarationUnreadable) as exc:
        surface.load_declaration(doctored)

    assert banned in str(exc.value)
    assert "baseline" in str(exc.value)


# ── 3. an added public method fails (and the failure is not vacuous) ─────────────


def test_an_added_public_method_fails_and_is_named():
    added = "probe_undeclared_method"

    problems = _problems(live=_live() | {added})

    assert any(added in p and "NOT on the approved surface" in p for p in problems), problems


def test_the_addition_failure_is_NOT_vacuous():
    """The same added name stops being reported once the declaration includes it.

    This is what separates "the gate reported an addition" from "the gate reds on anything
    that moves": the ONLY difference between this case and the failing one above is the
    declaration's own content.
    """
    added = "probe_undeclared_method"
    declaration = _declaration()["__all__"]
    live = _live() | {added}

    assert surface.gap_problems(declaration, live)  # absent from the declaration -> red
    problems = surface.gap_problems([*declaration, added], live)  # declared -> not reported
    assert not any(added in p for p in problems), problems


def test_a_real_public_attribute_on_the_class_is_caught(monkeypatch):
    """Non-vacuity against the REAL class: add a public callable, the gate sees it.

    `measure_live` reflects on `TortoiseSDK`, so this exercises the same path production
    uses — not a hand-built set — and it passes again once the attribute is removed.
    """
    from tortoise.sdk import TortoiseSDK

    assert not any("probe_real_added_method" in p for p in _problems())

    monkeypatch.setattr(TortoiseSDK, "probe_real_added_method", lambda self: None, raising=False)
    problems = _problems()
    assert any("probe_real_added_method" in p for p in problems), problems

    monkeypatch.delattr(TortoiseSDK, "probe_real_added_method", raising=False)
    assert not any("probe_real_added_method" in p for p in _problems())


# ── 4. an approved method that does not exist fails (and is not vacuous) ────────


def test_a_declared_method_that_does_not_exist_fails_and_is_named():
    declaration = _declaration()
    live = _live()
    absent = sorted(n for n in declaration["__all__"] if not surface.resolves(n, live))
    assert absent, "the gap is open: the declaration has approved-but-unimplemented methods"
    gone = absent[0]

    problems = _problems(live=live)

    assert any(gone in p and "does NOT exist on TortoiseSDK" in p for p in problems), problems


def test_the_absence_failure_is_NOT_vacuous():
    """The same name stops being reported once the class has it — declaration unchanged."""
    declaration = _declaration()["__all__"]
    absent = sorted(n for n in declaration if not surface.resolves(n, _live()))
    gone = absent[0]

    assert any(gone in p for p in _problems())
    problems = _problems(live=_live() | {gone})
    assert not any(gone in p for p in problems), problems


# ── 5. today's state is RED, with the real numbers ──────────────────────────────


def test_the_gate_is_RED_on_todays_gap_and_names_every_offender():
    """#4282's true state: 40 approved, 124 live. Not green, and not vague.

    The approved side has not moved: 12 of the 40 exist, 28 do not. The LIVE side moved
    150 → 124 on 2026-10-27, when the owner's ruling that the surplus methods nothing calls
    may be deleted (PR #10187) removed the 26 with no caller under `tortoise/**` — so the
    off-surface gap falls 138 → 112 while `absent` stays 27. Nothing approved was removed:
    the 26 are all off-surface surplus, which is why this premise and the 40 are separate
    numbers.
    """
    declared = _declaration()["__all__"]
    live = _live()
    problems = _problems()

    off_surface = sorted(live - set(declared))
    absent = sorted(n for n in declared if not surface.resolves(n, live))

    # The numbers are LITERALS, not restatements of the same set algebra: `|live − declared|`
    # equals `|live| − |live ∩ declared|` by definition, so asserting that identity would
    # verify nothing at all. Each literal carries its own premise note —
    assert len(declared) == APPROVED_COUNT, "the approved list was re-cut; that is an owner decision"
    assert len(live) == 124, "the live surface moved — update this premise (it pins today's gap)"
    assert len(off_surface) == 112, "the gap moved — update this premise (it pins today's gap)"
    assert len(absent) == 27, "the gap moved — update this premise (it pins today's gap)"
    assert problems, "the gate must be RED while the gap is open"
    # EVERY offender is named, and nothing else is: the count is the gap, exactly.
    assert sum(1 for p in problems if "NOT on the approved surface" in p) == len(off_surface)
    assert sum(1 for p in problems if "does NOT exist on TortoiseSDK" in p) == len(absent)
    assert len(problems) == len(off_surface) + len(absent)


def test_the_declaration_docstring_states_the_measured_split_not_the_delta():
    """#10044 review F2: the prose must not misstate the gap the module is the authority for.

    The docstring is what a maintainer reads before touching a name, and it said "149 public
    methods are live on `TortoiseSDK` today; 40 are approved. The other 109 are the migration
    gap — Phase 2 of #4282 removes them" (149 was the live count when that text was written).
    `109` is the DELTA to the target COUNT — not a set — and the set it named as REMOVED is
    the wrong one: the approved methods already implemented stay, so the removals are
    `live − overlap`, while the approved-but-absent names are what Phase 2 IMPLEMENTS (a
    disjoint set, the opposite operation). Both of this module's own artifacts already said
    so — the baseline document tabulates the same split and the gate prints `N live method(s)
    off the approved surface, M approved method(s) not implemented` — so the declaration
    disagreed with the gate it exists to feed.

    Checked on the CLAIM, not the wording: every line mentioning 109 must also label it a
    delta, the two measured set sizes must appear, and any count attached to the implement
    set must BE the measured one (round-6 ISSUE 1: the first rewrite folded the row-1
    `Tortoise(...)` into the set Phase 2 implements and claimed `31` declared names for it,
    while both the gate and this file measure 30 — and the count pin was satisfied by the
    `30` echoed in the very sentence that claimed 31). RED on the pre-fix docstring (its
    `The other 109 are the migration gap` line carries no delta label and the text never
    states the two set sizes), so this is not vacuous.
    """
    import ast
    import re

    doc = ast.get_docstring(ast.parse(DECLARATION.read_text(encoding="utf-8"))) or ""
    assert doc, "the declaration module lost its docstring, so this pin protects nothing"
    declared, live = _declaration()["__all__"], _live()
    off_surface = live - set(declared)
    absent = [n for n in declared if not surface.resolves(n, live)]

    assert str(len(off_surface)) in doc, (
        f"the docstring never states the {len(off_surface)} live methods that are off the "
        "approved surface — the set Phase 2 removes, by measurement"
    )
    assert str(len(absent)) in doc, (
        f"the docstring never states the {len(absent)} approved-but-absent methods — the set "
        "Phase 2 implements, by measurement"
    )

    # ROUND-6 ISSUE 1: the count pin above is satisfied by a bare `30` ANYWHERE, which is how
    # a sentence claiming `31 declared names` for the implement set passed. Attach the count
    # to the CLAIM: a line that says what Phase 2 IMPLEMENTS must carry the MEASURED size,
    # and must not attach any other count to methods/names.
    implements = [ln.replace("**", "").replace("`", "") for ln in doc.splitlines()
                  if "IMPLEMENTS" in ln]
    assert implements, (
        "the docstring no longer says what Phase 2 does with the approved-but-absent names, "
        "so this pin would be checking nothing"
    )
    assert any(str(len(absent)) in ln for ln in implements), (
        f"no line carrying `IMPLEMENTS` states the measured implement set ({len(absent)}): "
        "the count must be attached to the claim, not echoed a sentence away"
    )
    claimed = {
        int(n) for ln in implements for n in re.findall(r"(\d+)\s+(?:declared\s+)?(?:methods|names)", ln)
    }
    overlap = len(set(declared) & set(live))
    assert claimed <= {len(absent)}, (
        f"a line carrying `IMPLEMENTS` attaches the count(s) {sorted(claimed)} to the "
        f"implement set; the measured set is {len(absent)}. "
        f"`{len(declared) - overlap}` is the complement of the {overlap}-method overlap — a "
        "DELTA against the declared list, not a set of things to implement: the row-1 "
        f"`Tortoise(...)` already resolves, and the {len(absent)} absent names exclude it."
    )

    unlabelled = [ln.strip() for ln in doc.splitlines()
                  if "109" in ln and "delta" not in ln.lower()]
    assert unlabelled == [], (
        "the docstring states the target-count DELTA (live − approved) as if it were the set "
        f"of methods the migration removes: {unlabelled}. The removals are the "
        f"{len(off_surface)}, and the {len(absent)} absent approved names are IMPLEMENTED, "
        "not removed — say `delta` on any line that quotes 109."
    )


def test_the_gate_is_NOT_always_red():
    """A surface that matches the declaration produces NO problems.

    Without this, every failure above is satisfiable by a gate that always reds.
    """
    doc_names = surface.approved_from_doc()
    # The surface #4282 targets, i.e. the shape Phase 2 produces: the owner's 40 are all
    # implemented and NOTHING else is public. It MUST be green — without this, every failure
    # above is satisfiable by a gate that always reds.
    completed = set(doc_names)

    assert surface.problems(doc_names, completed, doc_names) == []
    # ... and the same call on TODAY's live set is red, so the difference is the surface.
    assert surface.problems(doc_names, _live(), doc_names) != []


# ── 6. the contract half: the required-CI invariant ─────────────────────────────


def test_the_contract_holds_on_the_checked_in_declaration():
    assert surface.check(contract_only=True) == []


def test_a_padded_declaration_breaks_the_contract(tmp_path):
    text = DECLARATION.read_text(encoding="utf-8")
    doctored = tmp_path / "__all__.py"
    doctored.write_text(text.replace('    "close",\n', '    "close",\n    "probe_padding",\n', 1),
                        encoding="utf-8")

    problems = surface.check(declaration_path=doctored, contract_only=True)

    assert any("probe_padding" in p and "cannot add" in p for p in problems), problems


def test_a_dropped_approved_method_breaks_the_contract(tmp_path):
    text = DECLARATION.read_text(encoding="utf-8")
    doctored = tmp_path / "__all__.py"
    doctored.write_text(text.replace('    "close",\n', "", 1), encoding="utf-8")

    problems = surface.check(declaration_path=doctored, contract_only=True)

    assert any("close" in p and "MISSING from __all__" in p for p in problems), problems


def test_contract_mode_ignores_the_gap_but_still_reports_the_contract():
    """The required job is green on the contract WHILE the gap is open — and says so."""
    assert surface.check(contract_only=True) == []
    assert surface.check() != []


# ── the CLI, on the real evidence ────────────────────────────────────────────────


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True, cwd=ROOT
    )


def test_the_cli_full_check_is_RED_today_and_names_the_gap():
    result = _run("check")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL" in result.stdout
    assert "NOT on the approved surface" in result.stdout


def test_the_cli_contract_mode_is_green_and_prints_the_gap():
    result = _run("check", "--contract-only")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK CONTRACT" in result.stdout
    # A green required job must never read as "on target": the gap is printed beside it.
    assert "GAP" in result.stdout


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


# ── 7. the baseline of what exists: tracked, generated, never the target ────────


def test_the_baseline_document_is_not_stale():
    """Rendered output must equal the committed file — a drifting baseline tracks nothing."""
    committed = BASELINE_DOC.read_text(encoding="utf-8")

    assert committed == surface.rendered_baseline()


def test_the_baseline_document_records_today_and_says_it_is_not_the_target():
    declared = _declaration()["__all__"]
    live = _live()
    text = BASELINE_DOC.read_text(encoding="utf-8")

    assert "NOT the target" in text
    assert "may only SHRINK" in text
    assert f"| approved methods (the target) | {len(declared)} |" in text
    assert f"| live public methods on `TortoiseSDK` | {len(live)} |" in text
    # Every off-surface live method is listed, and NO approved-live method is listed as
    # backlog (the two sets are disjoint by construction — asserted, not assumed).
    backlog = live - set(declared)
    for name in sorted(backlog):
        assert f"- `{name}`" in text
    assert not backlog & set(declared)


def test_the_baseline_cli_checks_the_committed_file(tmp_path):
    result = _run("baseline", "--check")
    assert result.returncode == 0, result.stdout + result.stderr

    stale = tmp_path / "baseline.md"
    stale.write_text("stale\n", encoding="utf-8")
    result = _run("baseline", "--check", "--doc-file", str(stale))
    assert result.returncode == 1
    assert "STALE" in result.stdout


# ── and the other surface tools read the same declaration (single source) ───────


def test_the_manifest_records_the_approval_per_row_and_keeps_the_baseline_row_set():
    """The frozen baseline still describes what EXISTS; `approved` marks the owner's 40.

    The row set cannot shrink before Phase 2 (the required, base-ref-pinned `surface-guard`
    compares it against the live class), so the approval is the per-row flag — and the flag
    is cut from the declaration, not from a count.
    """
    import yaml

    manifest = yaml.safe_load(
        (ROOT / "config" / "surface-manifest.yml").read_text(encoding="utf-8")
    )
    declared = set(_declaration()["__all__"])
    live = _live()
    approved_rows = {row["method"] for row in manifest["rows"] if row.get("approved") is True}
    sdk_rows = {
        str(row["name"])[len("sdk:"):]
        for row in manifest["rows"]
        if str(row.get("name", "")).startswith("sdk:")
    }

    assert sdk_rows == live
    assert approved_rows == declared & live
    assert manifest["counts"]["sdk_public_methods"] == len(live)


def test_the_target_check_reads_the_sdk_target_from_the_declaration():
    """#4282 requirement: `40` comes from the declaration, not from parsing prose."""
    import importlib.util

    path = ROOT / "tools" / "surface-target-check.py"
    spec = importlib.util.spec_from_file_location("surface_target_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    targets = module.read_targets()

    assert targets["sdk"] == len(_declaration()["__all__"]) == APPROVED_COUNT
    assert Path(targets["sources"]["sdk"]) == DECLARATION


def test_the_constructor_row_resolves_to_the_class():
    """Row 1 of the approved table is `Tortoise(...)` — the class a caller constructs.

    This pins the DOCUMENTED MAPPING, not a measurement: there is no `Tortoise` binding in the
    package, and `resolves()` says so. The constructible entry point is `TortoiseSDK`, asserted
    here so the mapping cannot drift into a claim about a symbol that does not exist.
    """
    from tortoise.sdk import TortoiseSDK

    assert surface.resolves(surface.CONSTRUCTOR, _live()) is True
    assert callable(TortoiseSDK)
    # ... and NOT because a `Tortoise` symbol exists on the class or in the package.
    assert not hasattr(TortoiseSDK, surface.CONSTRUCTOR)
    assert surface.CONSTRUCTOR not in _live()


def test_loading_the_declaration_by_path_does_not_shadow_star_import():
    """`tortoise/__all__.py` is a MODULE; importing it as `tortoise.__all__` would break
    `from tortoise import *` for the rest of the process. The tools load it by PATH, so the
    package attribute must stay absent — pinned here, because a future dotted import would
    turn a working star-import into `TypeError: 'module' object is not iterable`.
    """
    import importlib

    import tortoise

    assert "__all__" not in vars(tortoise)
    surface.load_declaration()  # the supported way to read it
    assert "__all__" not in vars(tortoise)
    # And the trap is real, not hypothetical — it is why the loader avoids the dotted name.
    importlib.import_module("tortoise.__all__")
    with pytest.raises(TypeError):
        exec("from tortoise import *", {})  # proving the trap exists
    delattr(tortoise, "__all__")
