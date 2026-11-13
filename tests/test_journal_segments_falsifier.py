"""#9025 — the falsifier: prove the segment guard is the thing that refuses.

A pin that cannot fail is not a guard (``#11859``). So this file does not take
the guard's word for it: it removes a guard **from the module's source**, loads
the mutated module, and shows the refusal disappears — and, with the guards that
overlap removed together, that the dangerous outcome becomes reachable.

Both directions run from :data:`MUTANTS`, a module-level tuple, so "the guard
works" is a test rather than a docstring nobody can re-run.

⚠️ **The refusals overlap on purpose (defence in depth), and that is stated
rather than hidden.** A dropped segment is refused both by ``verify`` (a
manifest row says *present*, the directory disagrees) and by
``reconstruction_scope`` (a departed segment denies a full claim). So most
single-site mutants expose *one refusal arm*, not the lie — which is why there is
also :func:`test_all_guards_removed_makes_the_lie_reachable`: the end-to-end
falsifier that removes every overlapping guard at once and shows a restore would
report full reconstruction over a journal that cannot support it.

⚠️ **The mutation needles are exact source text, and a no-match is a FAILURE**
(``test_every_mutant_needle_is_present_in_the_shipped_source``), never a skip: a
renamed guard must not silently disable the whole file.

Measured at ``feat/9025-journal-bound``; the PR body records the run.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from tortoise import journal_segments as real
from tortoise.log import EventLog

MODULE_PATH = Path(real.__file__)
SOURCE = MODULE_PATH.read_text(encoding="utf-8")


@dataclass(frozen=True)
class Mutant:
    """One removed guard: the exact source it removes, and what it exposes."""

    name: str
    needle: str
    replacement: str
    kills: str          # the falsifier test in tests/test_journal_segments.py


MUTANTS = (
    Mutant(
        # The arm that keeps a manifest-recorded, directory-absent segment IN the
        # layout. Remove it and the segment simply vanishes from view — which is
        # how a dropped segment becomes "nothing to see here".
        name="discovery-of-a-departed-segment",
        needle="""    for name, row in declared.items():
        if name in on_disk:
            continue""",
        replacement="""    for name, row in ():  # MUTANT: a departed segment leaves the layout
        if name in on_disk:
            continue""",
        kills="test_FALSIFIER_a_segment_dropped_without_a_boundary_REFUSES",
    ),
    Mutant(
        # The scope's own refusal for a segment whose bytes are elsewhere.
        name="scope-denies-a-full-claim",
        needle="""    return ReconstructionScope(
        full=False,
        from_segment=first.name,""",
        replacement="""    return ReconstructionScope(
        full=True,  # MUTANT: a departed segment no longer denies the claim
        from_segment=first.name,""",
        kills="test_FALSIFIER_a_full_claim_across_a_recorded_boundary_REFUSES",
    ),
    Mutant(
        # The claim function itself.
        name="claim-refusal",
        needle="""    scope = reconstruction_scope(layout)
    if not scope.full:""",
        replacement="""    scope = reconstruction_scope(layout)
    if False:  # MUTANT: the full-reconstruction refusal removed""",
        kills="test_FALSIFIER_a_full_claim_across_a_recorded_boundary_REFUSES",
    ),
    Mutant(
        name="sequence-gap",
        needle="""        if sorted(indices) != expected:""",
        replacement="""        if False:  # MUTANT: the gap check removed""",
        kills="test_FALSIFIER_a_gap_in_the_sealed_sequence_REFUSES",
    ),
    Mutant(
        name="stale-disposition",
        needle="""        if seg.disposition != "present" and seg.on_disk:""",
        replacement="""        if False:  # MUTANT: a stale disposition accepted""",
        kills="test_FALSIFIER_a_stale_disposition_REFUSES",
    ),
    Mutant(
        # The switch. Removing it arms sealing for every deployment that never
        # opted in — the default path stops being what ships today.
        name="switch-defaults-on",
        needle="""    return os.environ.get(ENV_FLAG, "") == "1\"""",
        replacement="""    return True  # MUTANT: sealing armed without the opt-in""",
        kills="test_the_default_layout_is_byte_for_byte_todays",
    ),
)


def load_mutant(tmp_path: Path, mutants: Mutant | tuple[Mutant, ...]):
    """Load the module with the given guard(s) removed, under its own name."""
    picks = mutants if isinstance(mutants, tuple) else (mutants,)
    mutated = SOURCE
    for mutant in picks:
        assert mutant.needle in mutated, (
            f"mutation {mutant.name!r} no longer matches the source — the guard "
            f"was renamed or reworded, and a silent no-match would make this "
            f"falsifier vacuous. Re-derive the needle in the same commit that "
            f"moved the guard.")
        mutated = mutated.replace(mutant.needle, mutant.replacement, 1)

    label = "-and-".join(m.name for m in picks)
    path = tmp_path / f"{label.replace('-', '_')}.py"
    path.write_text(mutated, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"mutant_{label}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # the module's only relative import is `from .log import …` inside
    # `read_all_segments`; give the mutant the real package so it resolves.
    module.__package__ = "tortoise"
    sys.modules[spec.name] = module
    with warnings.catch_warnings():
        # CPython notes `__package__ != __spec__.parent` for a module loaded this
        # way. It is the price of exercising the REAL source with one guard
        # removed, and it is confined to this call.
        warnings.filterwarnings("ignore", category=DeprecationWarning,
                                message=r".*__package__ != __spec__\.parent.*")
        spec.loader.exec_module(module)
    return module


def _by_name(name: str) -> Mutant:
    for mutant in MUTANTS:
        if mutant.name == name:
            return mutant
    raise AssertionError(f"no mutant named {name!r}")


def _journal(graph: Path, module, ids: list[str]) -> None:
    log = EventLog(graph / module.ACTIVE_NAME)
    for i in ids:
        log.append({"type": "PointRevised", "id": i})


def _seal_dropped(tmp_path: Path, module) -> Path:
    """A graph dir whose manifest says a segment is present and disk disagrees."""
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, module, ["a", "b"])
    module.seal_active(graph)
    (graph / "events.0000.jsonl").unlink()
    return graph


def _seal_archived(tmp_path: Path, module) -> Path:
    """A graph dir whose boundary is honest: the segment is archived, and says so."""
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, module, ["a", "b"])
    module.seal_active(graph)
    (graph / "events.0000.jsonl").unlink()
    module.record_disposition(graph, "events.0000.jsonl", "archived")
    return graph


# ---------------------------------------------------------------------------
# the GREEN direction: the shipped module refuses
# ---------------------------------------------------------------------------


def test_the_shipped_module_refuses_a_segment_that_left_without_a_boundary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = _seal_dropped(tmp_path, real)
    with pytest.raises(real.SegmentMissingError):
        real.assert_full_reconstruction(real.discover(graph))
    assert real.summarise(graph)["full_reconstruction"] is False


def test_the_shipped_module_refuses_a_full_claim_across_an_archived_boundary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = _seal_archived(tmp_path, real)
    layout = real.discover(graph)
    real.verify(layout)
    assert real.reconstruction_scope(layout).full is False
    with pytest.raises(real.ReconstructionClaimError):
        real.assert_full_reconstruction(layout)


# ---------------------------------------------------------------------------
# the RED direction: one refusal site at a time
# ---------------------------------------------------------------------------


def test_mutant_discovery_dropped_segment_is_no_longer_seen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_FALSIFIER_a_segment_dropped_without_a_boundary_REFUSES``."""
    monkeypatch.setenv(real.ENV_FLAG, "1")
    mutant = load_mutant(tmp_path, _by_name("discovery-of-a-departed-segment"))
    graph = _seal_dropped(tmp_path, mutant)

    # ⛔ no SegmentMissingError, no ReconstructionClaimError — the journal reads
    # as complete because the segment left the layout entirely.
    mutant.assert_full_reconstruction(mutant.discover(graph))
    report = mutant.summarise(graph)
    assert report["verified"] is True
    assert report["full_reconstruction"] is True
    # the sealed segment is not merely mis-described — it is not described at
    # all, because the manifest row that recorded it left the layout with it
    assert [s["name"] for s in report["segments"]] == []
    assert report["total_bytes"] == 0


def test_mutant_scope_reports_full_reconstruction_over_an_archived_segment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_FALSIFIER_a_full_claim_across_a_recorded_boundary_REFUSES``."""
    monkeypatch.setenv(real.ENV_FLAG, "1")
    mutant = load_mutant(tmp_path, _by_name("scope-denies-a-full-claim"))
    graph = _seal_archived(tmp_path, mutant)

    assert mutant.reconstruction_scope(mutant.discover(graph)).full is True
    mutant.assert_full_reconstruction(mutant.discover(graph))


def test_mutant_claim_function_licenses_the_claim_it_exists_to_refuse(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The scope still says False; only the asserting call stops raising."""
    monkeypatch.setenv(real.ENV_FLAG, "1")
    mutant = load_mutant(tmp_path, _by_name("claim-refusal"))
    graph = _seal_archived(tmp_path, mutant)

    layout = mutant.discover(graph)
    assert mutant.reconstruction_scope(layout).full is False   # honest scope
    mutant.assert_full_reconstruction(layout)                  # ⛔ silent claim


def test_mutant_gap_guard_accepts_a_broken_sequence(tmp_path: Path):
    """Kills ``test_FALSIFIER_a_gap_in_the_sealed_sequence_REFUSES``."""
    mutant = load_mutant(tmp_path, _by_name("sequence-gap"))
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, mutant, ["a"])
    (graph / mutant.ACTIVE_NAME).rename(graph / "events.0000.jsonl")
    _journal(graph, mutant, ["b"])
    (graph / mutant.ACTIVE_NAME).rename(graph / "events.0002.jsonl")
    _journal(graph, mutant, ["c"])

    mutant.verify(mutant.discover(graph))           # no refusal at all
    # and the replay now reads a sequence it cannot know is complete, in order,
    # and reports nothing wrong — a plausible and wrong graph.
    assert [r["id"] for r in mutant.read_all_segments(graph)] == ["a", "b", "c"]


def test_mutant_stale_disposition_is_accepted(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_FALSIFIER_a_stale_disposition_REFUSES``."""
    monkeypatch.setenv(real.ENV_FLAG, "1")
    mutant = load_mutant(tmp_path, _by_name("stale-disposition"))
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, mutant, ["a"])
    mutant.seal_active(graph)
    mutant.record_disposition(graph, "events.0000.jsonl", "archived")  # bytes never left

    mutant.verify(mutant.discover(graph))           # accepted
    assert mutant.reconstruction_scope(mutant.discover(graph)).full is False


def test_mutant_switch_default_on_seals_without_the_opt_in(tmp_path: Path):
    """Kills ``test_the_default_layout_is_byte_for_byte_todays``."""
    mutant = load_mutant(tmp_path, _by_name("switch-defaults-on"))
    assert real.ENV_FLAG not in os.environ
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, mutant, ["a"])

    mutant.seal_active(graph)                       # ⛔ the opt-in was bypassed
    assert (graph / "events.0000.jsonl").exists()


# ---------------------------------------------------------------------------
# the END-TO-END falsifier: every overlapping guard removed together
# ---------------------------------------------------------------------------


def test_all_guards_removed_makes_the_lie_reachable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The dangerous property, with nothing left to prevent it.

    A restore over a journal missing a segment reports **full reconstruction**.
    This is the state the tested code exists to make impossible — so it is the
    state the falsifier has to be able to reach.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    mutant = load_mutant(tmp_path, tuple(
        _by_name(n) for n in ("discovery-of-a-departed-segment",
                              "scope-denies-a-full-claim",
                              "claim-refusal",
                              "stale-disposition")))
    graph = _seal_dropped(tmp_path, mutant)

    report = mutant.summarise(graph)
    assert report["verified"] is True
    assert report["full_reconstruction"] is True      # ⛔ THE LIE
    mutant.assert_full_reconstruction(mutant.discover(graph))

    # …and the shipped module, over the same bytes, refuses
    with pytest.raises(real.SegmentMissingError):
        real.assert_full_reconstruction(real.discover(graph))


def test_every_mutant_needle_is_present_in_the_shipped_source():
    """A renamed guard must not silently disable a mutation (see the docstring)."""
    missing = [m.name for m in MUTANTS if m.needle not in SOURCE]
    assert missing == [], (
        f"mutations with no match against {MODULE_PATH}: {missing} — re-derive "
        f"the needles in the same commit that moved the guard")
    assert len({m.name for m in MUTANTS}) == len(MUTANTS)
    # every mutant names the falsifier test it kills, and that test exists
    shipped = (Path(__file__).with_name("test_journal_segments.py")
               .read_text(encoding="utf-8"))
    for mutant in MUTANTS:
        assert f"def {mutant.kills}(" in shipped, (
            f"mutant {mutant.name!r} names {mutant.kills!r}, which is not in "
            f"tests/test_journal_segments.py")
