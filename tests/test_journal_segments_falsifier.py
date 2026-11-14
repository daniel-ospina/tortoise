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
        if name in journalled:
            continue""",
        replacement="""    for name, row in ():  # MUTANT: a departed segment leaves the layout
        if name in journalled:
            continue""",
        kills="test_FALSIFIER_a_segment_dropped_without_a_boundary_REFUSES",
    ),
    Mutant(
        # The scope's own refusal for a segment whose bytes are elsewhere.
        name="scope-denies-a-full-claim",
        needle="""    return ReconstructionScope(
        full=False,
        first_missing=missing.name,""",
        replacement="""    return ReconstructionScope(
        full=True,  # MUTANT: a departed segment no longer denies the claim
        first_missing=missing.name,""",
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
        # ANCHOR 2: the size the manifest recorded at seal time. Remove it and a
        # sealed segment truncated in place certifies full reconstruction.
        name="recorded-size-anchor",
        # ⛔ A triple-quoted needle cannot carry a backslash-newline: Python
        # folds the continuation away and the needle silently stops matching.
        # Built as an explicit concatenation instead.
        needle=("        if seg.on_disk and seg.recorded_bytes is not None \\"
                "\n"
                "                and seg.bytes != seg.recorded_bytes:"),
        replacement="""        if False:  # MUTANT: the size anchor no longer diagnoses a rewrite""",
        # ⛔ NOT a lie-exposing mutant, and it must not claim to be: after cycle 3
        # the IDENTITY anchor catches a size change too, so removing this one
        # leaves the refusal standing (with a different message). What it kills is
        # the test that pins the DIAGNOSIS. Claiming "full reconstruction becomes
        # reachable" here was vacuous — measured, and corrected.
        kills="test_ANCHOR2c_a_size_change_that_KEEPS_the_line_count_is_still_REFUSED",
    ),
    Mutant(
        # ANCHOR 5: the identity anchor. Remove it and two equal-length sealed
        # segments can be SWAPPED — size and line count both survive, so nothing
        # else sees it, and the replay order inverts with the claim still "full".
        name="recorded-identity-anchor",
        needle=("        if seg.on_disk and seg.recorded_sha256 is not None \\"
                "\n"
                "                and seg.sha256 != seg.recorded_sha256:"),
        replacement="""        if False:  # MUTANT: a same-size rewrite is accepted""",
        kills="test_ANCHOR5_two_sealed_segments_SWAPPED_is_REFUSED",
    ),
    Mutant(
        # REQUIRED ANCHOR FIELDS: dropping `bytes`/`lines` from a row used to
        # switch the anchor off, because its guard read `is not None`.
        name="anchor-fields-optional",
        needle="""        for field in ("bytes", "lines"):
            if not isinstance(row.get(field), int) or isinstance(row.get(field), bool):""",
        replacement="""        for field in ():  # MUTANT: an anchor field may be omitted
            if not isinstance(row.get(field), int) or isinstance(row.get(field), bool):""",
        kills="test_a_manifest_row_that_OMITS_an_anchor_field_is_REFUSED",
    ),
    Mutant(
        # ANCHOR 3: `active` must be ON DISK. Without the check, a departed row
        # with no index stands in for the live tail and a deleted tail reads as
        # present.
        name="active-must-be-on-disk",
        needle="""            if not seg.sealed and seg.on_disk:""",
        replacement="""            if not seg.sealed:  # MUTANT: a departed row counts as the tail""",
        kills="test_a_DELETED_live_tail_is_caught_even_if_a_row_claims_it_is_archived",
    ),
    Mutant(
        # `complete_from` after the FIRST gap rather than the LAST one overstates
        # what is reproducible when there are two holes.
        name="complete-from-after-last-gap",
        needle="""    last_gone = sorted(gone, key=lambda s: s.name)[-1]""",
        replacement="""    last_gone = missing  # MUTANT: the first gap, not the last""",
        kills="test_complete_from_starts_after_the_LAST_gap_not_the_first",
    ),
    Mutant(
        # ANCHOR 1: a `*.jsonl` the sequence cannot place. `rebuild_all` replays
        # it regardless, so ignoring it is a claim over bytes we did not describe.
        name="unplaceable-jsonl",
        needle="""    if unplaceable:""",
        replacement="""    if False:  # MUTANT: a file the replay folds is silently ignored""",
        kills="test_ANCHOR1_an_unplaceable_jsonl_is_REFUSED_not_ignored",
    ),
    Mutant(
        # `adopt_crashed_seal`'s index check. Without it the repair verb becomes
        # a way to record a LOSS as "present": a missing segment behind the
        # boundary is a hole in the middle of the sequence, not a crashed seal.
        name="adopt-index-must-be-next",
        needle="""        if index != highest + 1:""",
        replacement="""        if False:  # MUTANT: a loss behind the boundary is adopted as present""",
        kills="test_adopt_refuses_when_the_missing_segment_is_behind_the_boundary",
    ),
    Mutant(
        # The crash signature's third limb: `seal_active` creates the tail right
        # after the rename, so a present tail means this is NOT that crash.
        name="adopt-tail-must-be-absent",
        needle="""        if os.path.lexists(tail):""",
        replacement="""        if False:  # MUTANT: any unrecorded sealed segment is adopted""",
        kills="test_adopt_refuses_when_the_live_tail_is_still_there",
    ),
    Mutant(
        # The second limb of the same window: `_open_tail_exclusively` refuses to
        # settle a tail that already has bytes. Removing it is only dangerous
        # together with the check above — and that layering is asserted in the
        # test rather than assumed.
        # ⛔ The needle carries the `refuse_non_empty and` conjunct because the
        # SEAL caller passes `refuse_non_empty=False` (cycle 6: the repair verb's
        # size arm must not be applied to a post-rename tail — refusing it wedged
        # a first seal with no manifest). Dropping the conjunct from the needle
        # would make this mutant vacuous, so it is re-derived with the guard.
        name="tail-settle-must-be-empty",
        needle="""        if refuse_non_empty and path.stat().st_size:""",
        replacement="""        if False:  # MUTANT: a live tail is settled as a fresh one""",
        kills="test_adopt_refuses_when_the_live_tail_is_still_there",
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
    # ⛔ A SHORT digest, not the joined label: with twelve guards removed the
    # label exceeded NAME_MAX and the combined falsifier died on `OSError`
    # instead of running (measured at 12 mutants).
    import hashlib
    tag = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
    path = tmp_path / f"mutant_{tag}.py"
    path.write_text(mutated, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"mutant_{label}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # the module's only relative import is `from .log import …` inside
    # `read_all_segments`; give the mutant the real package so it resolves.
    module.__package__ = "tortoise"
    sys.modules[spec.name] = module
    # ⛔ The `__package__ != __spec__.parent` DeprecationWarning is raised when
    # `read_all_segments` performs its FUNCTION-LOCAL relative import, i.e. at
    # CALL time — not here. A `catch_warnings()` around `exec_module` is a no-op
    # for it (measured), so the suppression lives in `call_quietly` and the tests
    # that trigger it go through that.
    spec.loader.exec_module(module)
    return module


def call_quietly(fn, *args, **kwargs):
    """Call a mutant function with the loader's DeprecationWarning suppressed."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning,
                                message=r".*__package__ != __spec__\.parent.*")
        return fn(*args, **kwargs)


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
    # all, because the manifest row that recorded it left the layout with it.
    # Only the (empty) active tail remains, so the journal reads as new.
    assert [s["name"] for s in report["segments"]] == [mutant.ACTIVE_NAME]
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


def _real_lines(path: Path) -> int:
    """The manifest's `lines` is a REAL count — the module compares it to disk."""
    return len([line for line in path.read_bytes().split(b"\n") if line.strip()])


def _real_sha(path: Path) -> str:
    """The manifest's `sha256` must be the real digest — it is the identity anchor."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_for(graph: Path, names: list[str]) -> None:
    """A valid manifest for a hand-built directory (the mutant's own format)."""
    import json
    rows = [{"name": n, "index": int(n.split(".")[1]), "bytes": 1, "lines": 1,
             "sha256": "x", "disposition": "present",
             "recorded_at": "2026-11-13T00:00:00+00:00"}
            for n in names]
    for row in rows:                                  # sizes must match, or ANCHOR 2 fires
        row["bytes"] = (graph / row["name"]).stat().st_size
        row["lines"] = _real_lines(graph / row["name"])
        row["sha256"] = _real_sha(graph / row["name"])
    (graph / "journal-manifest.json").write_text(json.dumps({
        "version": 2, "graph_dir": str(graph), "segments": rows,
        "boundary": {"recorded_at": "2026-11-13T00:00:00+00:00",
                     "through_index": max(r["index"] for r in rows),
                     "first_missing": None}}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


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
    _manifest_for(graph, ["events.0000.jsonl", "events.0002.jsonl"])

    mutant.verify(mutant.discover(graph))           # no refusal at all
    # and the replay now reads a sequence it cannot know is complete, in order,
    # and reports nothing wrong — a plausible and wrong graph.
    assert [r["id"] for r in call_quietly(mutant.read_all_segments, graph)] == \
        ["a", "b", "c"]


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


def test_mutant_switch_default_on_seals_without_the_opt_in(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_the_default_layout_is_byte_for_byte_todays``."""
    mutant = load_mutant(tmp_path, _by_name("switch-defaults-on"))
    # ⛔ Deliberately unset on THIS process: asserting `ENV_FLAG not in os.environ`
    # pinned a property of the machine, and reds a correct tree on any cell that
    # exports the flag — which is exactly how it is meant to be used.
    monkeypatch.delenv(real.ENV_FLAG, raising=False)
    assert not real.enabled()
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
    mutant = load_mutant(tmp_path, MUTANTS)      # every guard, not a subset
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


# ---------------------------------------------------------------------------
# cycle-3 mutants — RED direction, one guard at a time
# ---------------------------------------------------------------------------


def _rewrite_same_size(graph: Path) -> None:
    """Change a sealed segment's CONTENT without changing its length."""
    segment = graph / "events.0000.jsonl"
    original = segment.read_bytes()
    segment.write_bytes(original.replace(b'"a0"', b'"z0"'))


def _swap_equal_length_segments(graph: Path) -> None:
    first = graph / "events.0000.jsonl"
    second = graph / "events.0001.jsonl"
    assert first.stat().st_size == second.stat().st_size
    a, b = first.read_bytes(), second.read_bytes()
    first.write_bytes(b)
    second.write_bytes(a)


def _two_sealable_segments(tmp_path: Path, module) -> Path:
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, module, ["a0", "a1"])
    module.seal_active(graph)
    _journal(graph, module, ["b0", "b1"])
    module.seal_active(graph)
    return graph


def test_mutant_identity_anchor_lets_a_same_size_rewrite_through(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_ANCHOR5b_a_SAME_LENGTH_substitution_is_REFUSED``.

    ⛔ THE LIE: with the identity anchor gone, a segment whose records were
    replaced in place — same bytes, same line count — still certifies
    `full=True, anchored=True`, so a restore replays content the journal never
    sealed and reports success.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = _two_sealable_segments(tmp_path, real)
    mutant = load_mutant(tmp_path, _by_name("recorded-identity-anchor"))
    _rewrite_same_size(graph)

    with pytest.raises(real.SegmentModifiedError):        # the shipped module
        real.verify(real.discover(graph))

    layout = mutant.discover(graph)
    mutant.verify(layout)                                 # ⛔ no refusal
    assert mutant.reconstruction_scope(layout).full is True


def test_mutant_identity_anchor_lets_two_segments_SWAP(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_ANCHOR5_two_sealed_segments_SWAPPED_is_REFUSED``.

    Size and line count are both preserved by the swap, so with the identity
    anchor removed nothing sees it and the replay order inverts silently.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = _two_sealable_segments(tmp_path, real)
    mutant = load_mutant(tmp_path, _by_name("recorded-identity-anchor"))
    _swap_equal_length_segments(graph)

    with pytest.raises(real.SegmentModifiedError):
        real.verify(real.discover(graph))

    layout = mutant.discover(graph)
    mutant.verify(layout)                                  # ⛔ no refusal
    ids = [r["id"] for r in call_quietly(mutant.read_all_segments, graph)]
    assert ids[:2] == ["b0", "b1"]                         # replay order inverted


def test_mutant_size_anchor_still_refuses_but_loses_the_DIAGNOSIS(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_ANCHOR2c_a_size_change_that_KEEPS_the_line_count_is_still_REFUSED``.

    ⛔ NOT a lie-exposing mutant — the identity anchor is the second line of
    defence here, and it still refuses. What is lost is the message that says
    WHERE to look ("rewritten in place"), which is the whole operational value of
    the size anchor once identity exists.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = _two_sealable_segments(tmp_path, real)
    segment = graph / "events.0000.jsonl"
    lines = [ln for ln in segment.read_bytes().split(b"\n") if ln.strip()]
    lines[0] = b'{"id":"a0"}'
    segment.write_bytes(b"\n".join(lines) + b"\n")

    mutant = load_mutant(tmp_path, _by_name("recorded-size-anchor"))
    with pytest.raises(mutant.SegmentModifiedError) as exc:
        mutant.verify(mutant.discover(graph))
    assert "rewritten in place" not in str(exc.value)      # ⛔ the diagnosis
    assert "does not hash" in str(exc.value)               # identity caught it


def test_mutant_anchor_fields_optional_lets_a_truncated_segment_certify_full(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_a_manifest_row_that_OMITS_an_anchor_field_is_REFUSED``.

    ⛔ THE LIE, measured before the fix: a row without `bytes` made anchor 2 skip
    itself, so a segment truncated to zero bytes still certified full.

    At this head, removing the field validation ALONE is no longer enough — the
    identity anchor catches the truncation (empty content hashes differently), so
    the lie needs BOTH this mutant and the identity one. That layering is the fix
    working, and it is asserted here rather than assumed.
    """
    import json
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, real, ["a0", "a1"])
    real.seal_active(graph)
    (graph / "events.0000.jsonl").write_bytes(b"")
    payload = json.loads((graph / "journal-manifest.json").read_text())
    del payload["segments"][0]["bytes"]
    del payload["segments"][0]["lines"]
    (graph / "journal-manifest.json").write_text(json.dumps(payload))

    with pytest.raises(real.JournalLayoutError):
        real.verify(real.discover(graph))

    # one guard removed: the identity anchor still refuses
    alone = load_mutant(tmp_path, _by_name("anchor-fields-optional"))
    with pytest.raises(alone.SegmentModifiedError):
        alone.verify(alone.discover(graph))

    # BOTH removed: nothing is left, and the lie is reachable
    mutant = load_mutant(tmp_path, (_by_name("anchor-fields-optional"),
                                    _by_name("recorded-identity-anchor")))
    layout = mutant.discover(graph)
    mutant.verify(layout)                                  # ⛔ no refusal
    assert mutant.reconstruction_scope(layout).full is True


def test_mutant_active_not_on_disk_reads_a_deleted_tail_as_present(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_a_DELETED_live_tail_is_caught_even_if_a_row_claims_it_is_archived``."""
    import json
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, real, ["a0"])
    real.seal_active(graph)
    _journal(graph, real, ["tail"])
    payload = json.loads((graph / "journal-manifest.json").read_text())
    payload["segments"].append({"name": "events.jsonl", "index": None,
                               "bytes": 10, "lines": 1, "sha256": "0" * 64,
                               "disposition": "archived",
                               "recorded_at": "2026-11-13T00:00:00+00:00"})
    payload["boundary"]["first_missing"] = "events.jsonl"
    (graph / "journal-manifest.json").write_text(json.dumps(payload))
    (graph / "events.jsonl").unlink()

    with pytest.raises(real.SegmentSequenceError):
        real.verify(real.discover(graph))

    mutant = load_mutant(tmp_path, _by_name("active-must-be-on-disk"))
    layout = mutant.discover(graph)
    assert layout.active is not None                       # ⛔ a departed row
    mutant.verify(layout)                                  # ⛔ anchor 3 bypassed


def test_mutant_complete_from_names_a_segment_followed_by_a_hole(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_complete_from_starts_after_the_LAST_gap_not_the_first``."""
    import json
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = tmp_path / "graph"
    graph.mkdir()
    for i in range(4):
        _journal(graph, real, [f"s{i}"])
        real.seal_active(graph)
    _journal(graph, real, ["live"])
    (graph / "events.0001.jsonl").unlink()
    (graph / "events.0003.jsonl").unlink()
    payload = json.loads((graph / "journal-manifest.json").read_text())
    for row in payload["segments"]:
        if row["name"] in ("events.0001.jsonl", "events.0003.jsonl"):
            row["disposition"] = "archived"
    payload["boundary"]["first_missing"] = "events.0001.jsonl"
    (graph / "journal-manifest.json").write_text(json.dumps(payload))

    scope = real.reconstruction_scope(real.discover(graph))
    assert scope.complete_from == "events.jsonl"

    mutant = load_mutant(tmp_path, _by_name("complete-from-after-last-gap"))
    mutated = mutant.reconstruction_scope(mutant.discover(graph))
    assert mutated.complete_from == "events.0002.jsonl"    # ⛔ a segment, then a hole


def test_mutant_adopt_index_check_launders_a_LOSS_as_present(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_adopt_refuses_when_the_missing_segment_is_behind_the_boundary``.

    ⛔ THE LIE: the repair verb is the one place that can turn "a segment is
    missing" into "present" without the bytes. With the index check removed it
    adopts ANY unrecorded sealed segment, so a hole in the middle becomes a
    complete sequence.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, real, ["a0"])
    real.seal_active(graph)
    _journal(graph, real, ["b0"])
    real.seal_active(graph)
    (graph / "events.jsonl").write_bytes(b'{"type":"PointRevised","id":"x"}\n')
    (graph / "events.jsonl").rename(graph / "events.0007.jsonl")

    with pytest.raises(real.SegmentSequenceError):
        real.adopt_crashed_seal(graph)

    mutant = load_mutant(tmp_path, _by_name("adopt-index-must-be-next"))
    mutant.adopt_crashed_seal(graph)
    names = [row["name"] for row in
             __import__("json").loads((graph / "journal-manifest.json").read_text())
             ["segments"]]
    assert "events.0007.jsonl" in names                  # ⛔ a hole adopted as present


def test_mutant_adopt_tail_check_adopts_a_segment_mid_seal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_adopt_refuses_when_the_live_tail_is_still_there``.

    A present tail means the writer got PAST the rename — the directory is
    mid-operation, not crashed, and adopting it records a seal that may be about
    to be recorded again.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, real, ["a0"])
    real.seal_active(graph)
    (graph / "events.jsonl").write_bytes(b'{"type":"PointRevised","id":"x"}\n')
    (graph / "events.0001.jsonl").write_bytes((graph / "events.jsonl").read_bytes())

    with pytest.raises(real.SegmentSequenceError):
        real.adopt_crashed_seal(graph)

    # one guard removed: the tail SETTLEMENT still refuses a non-empty file
    alone = load_mutant(tmp_path, _by_name("adopt-tail-must-be-absent"))
    with pytest.raises(alone.SegmentSequenceError):
        alone.adopt_crashed_seal(graph)

    # BOTH removed: the live journal is adopted as a finished seal
    mutant = load_mutant(tmp_path, (_by_name("adopt-tail-must-be-absent"),
                                    _by_name("tail-settle-must-be-empty")))
    mutant.adopt_crashed_seal(graph)                     # ⛔ adopted mid-seal


def test_mutant_unplaceable_jsonl_is_silently_ignored(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Kills ``test_ANCHOR1_an_unplaceable_jsonl_is_REFUSED_not_ignored``.

    Was the one mutant no test loaded (cycle 4). `rebuild_all` replays EVERY
    `*.jsonl`, so a name this module cannot place is replayed at a position
    nothing described — and with the check removed `discover` returns a clean
    layout over a journal whose replay folds a file of unknown origin.
    """
    monkeypatch.setenv(real.ENV_FLAG, "1")
    graph = tmp_path / "graph"
    graph.mkdir()
    _journal(graph, real, ["a0"])
    (graph / "stray.jsonl").write_bytes(b'{"type":"PointRevised","id":"ghost"}\n')

    with pytest.raises(real.SegmentSequenceError) as exc:
        real.discover(graph)
    assert "stray.jsonl" in str(exc.value)

    mutant = load_mutant(tmp_path, _by_name("unplaceable-jsonl"))
    layout = mutant.discover(graph)                       # ⛔ no refusal
    names = [s.name for s in layout.segments]
    assert "stray.jsonl" not in names                     # …and it is INVISIBLE
    # ⛔ the claim covers a set the REPLAY does not: `rebuild_all` folds every
    # `*.jsonl` by NAME, and `stray.jsonl` is one of them
    replayed = sorted(p.name for p in graph.iterdir()
                      if p.name.endswith(".jsonl"))
    assert "stray.jsonl" in replayed and "stray.jsonl" not in names
    assert layout.total_bytes < sum(p.stat().st_size for p in graph.iterdir()
                                    if p.name.endswith(".jsonl"))


def test_every_mutant_is_LOADED_by_some_test():
    """A mutant nothing loads is a falsifier entry that proves nothing.

    Cycle 4 (P2-G): `unplaceable-jsonl` was matched only as a SUBSTRING by the
    needle test, so the file claimed a kill it never performed.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    dead = [m.name for m in MUTANTS
            if f'_by_name("{m.name}")' not in source]
    assert dead == [], (
        f"mutants no test loads: {dead} — a mutant that is never loaded is a "
        f"claim, not a check")
