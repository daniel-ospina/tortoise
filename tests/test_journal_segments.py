"""#9025 — the journal's segment boundary must refuse to lie.

THE PROPERTY UNDER TEST, in one sentence: **a restore may not claim full
reconstruction across a boundary it cannot cross, and a segment may not vanish
without a recorded disposition.**

Every test here drives the mechanism that makes that property true. The ones
marked ``# FALSIFIER`` are the ones that must go RED when the guard is removed —
the file header of ``tools/journal_bound_falsifier.py`` (below) shows how to run
that check, and the PR body records the red run.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from tortoise.journal_segments import (
    ACTIVE_NAME,
    ENV_FLAG,
    MANIFEST_NAME,
    SEALED_RE,
    JournalLayoutError,
    ReconstructionClaimError,
    SegmentationDisabled,
    SegmentMissingError,
    SegmentModifiedError,
    SegmentSequenceError,
    assert_full_reconstruction,
    discover,
    plan_rotation,
    read_all_segments,
    read_manifest,
    reconstruction_scope,
    record_disposition,
    seal_active,
    summarise,
    verify,
)
from tortoise.log import EventLog


def write_journal(directory: Path, records: list[dict]) -> Path:
    """Append *records* to ``<directory>/events.jsonl`` through the real writer."""
    path = directory / ACTIVE_NAME
    log = EventLog(path)
    for rec in records:
        log.append(rec)
    return path


def sealed(name: str, directory: Path) -> Path:
    """Rename the active journal to a sealed segment name, as sealing does."""
    src = directory / ACTIVE_NAME
    dst = directory / name
    src.rename(dst)
    return dst


def regraph(directory: Path, records: list[dict]) -> None:
    """Start a fresh active segment with *records* in it (post-seal appends)."""
    write_journal(directory, records)


def records(n: int, prefix: str = "r") -> list[dict]:
    return [{"type": "PointRevised", "id": f"{prefix}{i}"} for i in range(n)]


def write_manifest(directory: Path, names: list[str],
                   dispositions: dict[str, str] | None = None) -> None:
    """A valid manifest for a HAND-BUILT directory (no seal involved).

    Used by the tests that construct a broken sequence directly — going through
    `seal_active` could not produce a gap or a de-padded name, and those are the
    shapes the guard has to refuse.
    """
    dispositions = dispositions or {}
    rows = []
    for name in names:
        index = int(SEALED_RE.match(name).group(1)) if SEALED_RE.match(name) else None
        rows.append({"name": name, "index": index,
                     "bytes": (directory / name).stat().st_size,
                     "lines": 1,
                     "disposition": dispositions.get(name, "present"),
                     "recorded_at": "2026-11-13T00:00:00+00:00"})
    first_missing = next((r["name"] for r in sorted(rows, key=lambda r: r["index"])
                          if r["disposition"] != "present"), None)
    (directory / MANIFEST_NAME).write_text(json.dumps({
        "version": 1,
        "graph_dir": str(directory),
        "segments": rows,
        "boundary": {"recorded_at": "2026-11-13T00:00:00+00:00",
                     "through_index": max((r["index"] for r in rows
                                           if r["index"] is not None), default=0),
                     "first_missing": first_missing},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def graph_dir(tmp_path: Path) -> Path:
    d = tmp_path / "graph"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# the happy path — a journal that never sealed anything still supports a full claim
# ---------------------------------------------------------------------------


def test_an_untouched_journal_allows_a_full_reconstruction_claim(graph_dir: Path):
    write_journal(graph_dir, records(3))
    layout = discover(graph_dir)
    assert [s.name for s in layout.segments] == [ACTIVE_NAME]
    assert layout.manifest_present is False

    scope = reconstruction_scope(layout)
    assert scope.full is True
    assert scope.first_missing is None
    assert scope.complete_from is None
    assert_full_reconstruction(layout)          # does not raise


def test_a_sealed_and_resealed_journal_still_supports_a_full_claim(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(2, "b"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(2, "c"))

    layout = discover(graph_dir)
    verify(layout)
    assert [s.name for s in layout.segments] == [
        "events.0000.jsonl", "events.0001.jsonl", ACTIVE_NAME]
    assert reconstruction_scope(layout).full is True
    assert_full_reconstruction(layout)


# ---------------------------------------------------------------------------
# FALSIFIER 1 — a segment dropped WITHOUT a recorded boundary
# ---------------------------------------------------------------------------


def test_FALSIFIER_a_segment_dropped_without_a_boundary_REFUSES(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The dangerous property: replay the survivors and report success.

    A manifest that says a segment is present, and a directory that no longer
    has it, is not "one segment short" — it is a journal whose completeness
    claim is false. It must REFUSE, not return what it found.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(4, "a"))
    seal_active(graph_dir)
    manifest = read_manifest(graph_dir)
    assert manifest is not None
    assert manifest["segments"][0]["disposition"] == "present"

    (graph_dir / "events.0000.jsonl").unlink()      # the segment is simply gone

    with pytest.raises(SegmentMissingError) as exc:
        assert_full_reconstruction(discover(graph_dir))
    assert "without a recorded boundary" in str(exc.value)

    with pytest.raises(SegmentMissingError):
        verify(discover(graph_dir))

    with pytest.raises(SegmentMissingError):
        read_all_segments(graph_dir)

    report = summarise(graph_dir)
    assert report["verified"] is False
    assert report["full_reconstruction"] is False
    assert "does not hold it" in report["reason"]


# ---------------------------------------------------------------------------
# FALSIFIER 2 — a full claim across a RECORDED boundary
# ---------------------------------------------------------------------------


def test_FALSIFIER_a_full_claim_across_a_recorded_boundary_REFUSES(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Archiving keeps the bytes elsewhere — it does not license "complete"."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(4, "a"))
    seal_active(graph_dir)
    (graph_dir / "events.0000.jsonl").unlink()      # archived off-box
    record_disposition(graph_dir, "events.0000.jsonl", "archived")
    write_journal(graph_dir, records(2, "b"))       # the journal keeps running

    layout = discover(graph_dir)
    verify(layout)                                  # the layout itself is sound
    scope = reconstruction_scope(layout)
    assert scope.full is False
    assert scope.first_missing == "events.0000.jsonl"
    assert scope.complete_from == ACTIVE_NAME
    assert "archived" in scope.reason

    with pytest.raises(ReconstructionClaimError) as exc:
        assert_full_reconstruction(layout)
    assert "complete from events.jsonl forward" in str(exc.value)

    # …and the replay still refuses to pretend the survivors are the whole story
    rows = read_all_segments(graph_dir)
    assert [r["id"] for r in rows] == ["b0", "b1"]


def test_a_compacted_segment_denies_a_full_claim_the_same_way(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    (graph_dir / "events.0000.jsonl").unlink()
    record_disposition(graph_dir, "events.0000.jsonl", "compacted")

    scope = reconstruction_scope(discover(graph_dir))
    assert scope.full is False
    assert "compacted" in scope.reason
    with pytest.raises(ReconstructionClaimError):
        assert_full_reconstruction(discover(graph_dir))


# ---------------------------------------------------------------------------
# FALSIFIER 3 — the sequence itself is broken
# ---------------------------------------------------------------------------


def test_FALSIFIER_a_gap_in_the_sealed_sequence_REFUSES(graph_dir: Path):
    """0000 and 0002 present: 0001 vanished and nothing says where it went."""
    write_journal(graph_dir, records(2, "a"))
    sealed("events.0000.jsonl", graph_dir)
    write_journal(graph_dir, records(2, "b"))
    sealed("events.0002.jsonl", graph_dir)
    write_journal(graph_dir, records(1, "c"))
    write_manifest(graph_dir, ["events.0000.jsonl", "events.0002.jsonl"])

    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "not contiguous" in str(exc.value)
    assert "missing [1]" in str(exc.value)


def test_FALSIFIER_a_stale_disposition_REFUSES(graph_dir: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    """A segment marked archived that is still on disk means the manifest is stale."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    record_disposition(graph_dir, "events.0000.jsonl", "archived")
    # the bytes never left — so the disposition is a claim about a state that
    # is not the state, and no read may proceed on it
    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "still on disk" in str(exc.value)


def test_an_unregistered_sealed_segment_REFUSES(graph_dir: Path,
                                                monkeypatch: pytest.MonkeyPatch):
    """A sealed file dropped into a manifested directory is not in the sequence."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(2, "b"))
    (graph_dir / ACTIVE_NAME).rename(graph_dir / "events.0001.jsonl")

    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "not in journal-manifest.json" in str(exc.value)


# ---------------------------------------------------------------------------
# the switch gates WRITING, never REFUSING (category-A fail-open if reversed)
# ---------------------------------------------------------------------------


def test_turning_the_flag_off_does_not_disarm_the_guard(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """A boundary already recorded stays enforced after the flag is switched off.

    If the flag also silenced the refusal, a deployment could cross a boundary
    and then hide it by unsetting a variable — the guard would be a suggestion.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    (graph_dir / "events.0000.jsonl").unlink()
    record_disposition(graph_dir, "events.0000.jsonl", "archived")

    monkeypatch.delenv(ENV_FLAG)
    with pytest.raises(ReconstructionClaimError):
        assert_full_reconstruction(discover(graph_dir))
    # …while a new seal is refused outright
    with pytest.raises(SegmentationDisabled):
        seal_active(graph_dir)
    with pytest.raises(SegmentationDisabled):
        record_disposition(graph_dir, "events.0000.jsonl", "present")


def test_the_default_layout_is_byte_for_byte_todays(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """With the flag off, a journal directory holds exactly what it holds today."""
    monkeypatch.delenv(ENV_FLAG, raising=False)
    path = write_journal(graph_dir, records(5))
    before = {p.name: p.read_bytes() for p in graph_dir.iterdir()}

    with pytest.raises(SegmentationDisabled):
        seal_active(graph_dir)

    after = {p.name: p.read_bytes() for p in graph_dir.iterdir()}
    assert after == before
    # ⛔ The claim is "nothing was ADDED", not "one file exists": `EventLog`
    # already keeps its own `events.jsonl.position` sidecar today, so an exact
    # name set here would pin the sidecar's absence and red on a correct tree.
    assert not [n for n in after if n != ACTIVE_NAME
                and not n.startswith(ACTIVE_NAME + ".")]
    assert path.stat().st_size > 0


def test_plan_rotation_writes_nothing(graph_dir: Path):
    """The planner is the pricing instrument: it must not change the directory."""
    write_journal(graph_dir, records(5))
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in graph_dir.iterdir()}

    assert plan_rotation(graph_dir, max_bytes=10 ** 9) is None      # under the bound

    plan = plan_rotation(graph_dir, max_bytes=1)                    # over it
    assert plan is not None
    assert plan.next_index == 0
    assert plan.seal_to == "events.0000.jsonl"
    assert plan.boundary.through_index == 0

    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in graph_dir.iterdir()}
    assert after == before


# ---------------------------------------------------------------------------
# sealing: renames and records, deletes nothing
# ---------------------------------------------------------------------------


def test_sealing_renames_and_records_and_deletes_nothing(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(6, "a"))
    before_bytes = (graph_dir / ACTIVE_NAME).read_bytes()

    sealed_path = seal_active(graph_dir)

    assert sealed_path.name == MANIFEST_NAME
    segment = graph_dir / "events.0000.jsonl"
    assert segment.exists()
    assert segment.read_bytes() == before_bytes      # every byte still here
    # ANCHOR 3: the live tail exists from the moment the journal is sealed, so a
    # DELETED tail is a detectable state rather than the normal just-sealed one.
    assert (graph_dir / ACTIVE_NAME).exists()
    assert (graph_dir / ACTIVE_NAME).read_bytes() == b""

    manifest = read_manifest(graph_dir)
    assert [row["name"] for row in manifest["segments"]] == ["events.0000.jsonl"]
    row = manifest["segments"][0]
    assert row["disposition"] == "present"
    assert row["index"] == 0
    assert row["bytes"] == len(before_bytes)
    assert row["lines"] == 6
    assert row["recorded_at"]          # the audit trail, not an undated assertion
    assert manifest["boundary"]["through_index"] == 0
    assert manifest["boundary"]["first_missing"] is None


def test_the_segment_names_carry_the_replay_order_at_a_discriminating_index(
        graph_dir: Path):
    """`rebuild_all` unions `sorted(os.listdir)` — so the NAME must carry the order.

    ⛔ TWO segments would pass with NO padding at all (`events.0.jsonl` and
    `events.1.jsonl` also sort before `events.jsonl`), so the fixture uses the
    first index where padding is load-bearing: **9 vs 10**, where an unpadded
    name sorts `events.10.jsonl` BEFORE `events.9.jsonl`.
    """
    for i in range(10):
        (graph_dir / f"events.{i:04d}.jsonl").write_bytes(b'{"type":"X"}\n')
    (graph_dir / ACTIVE_NAME).write_bytes(b'{"type":"Y"}\n')
    write_manifest(graph_dir, [f"events.{i:04d}.jsonl" for i in range(10)])

    rebuilt_view = sorted(f for f in os.listdir(graph_dir)
                          if f.endswith(".jsonl"))
    assert rebuilt_view == [f"events.{i:04d}.jsonl" for i in range(10)] + [ACTIVE_NAME]
    verify(discover(graph_dir))
    assert [s.name for s in discover(graph_dir).segments] == rebuilt_view


def test_a_replay_across_segments_is_the_same_record_stream_as_one_file(
        graph_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sealing must not reorder, duplicate or drop a record."""
    monkeypatch.setenv(ENV_FLAG, "1")
    whole = tmp_path / "whole.jsonl"
    log = EventLog(whole)
    for rec in records(3, "a"):
        log.append(rec)

    write_journal(graph_dir, records(3, "a"))
    seal_active(graph_dir)
    for rec in records(3, "b"):
        log.append(rec)
        EventLog(graph_dir / ACTIVE_NAME).append(rec)

    assert read_all_segments(graph_dir) == \
        EventLog(whole).read_all(salvage=True)


# ---------------------------------------------------------------------------
# a manifest that cannot be read is an ERROR, not an absence
# ---------------------------------------------------------------------------


def test_an_unparseable_manifest_REFUSES_rather_than_reading_as_absent(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Treating a corrupt boundary record as "no boundary" is the laundered claim."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2))
    seal_active(graph_dir)
    (graph_dir / MANIFEST_NAME).write_text("{ not json", encoding="utf-8")

    with pytest.raises(JournalLayoutError) as exc:
        discover(graph_dir)
    assert "not valid JSON" in str(exc.value)


def test_an_unknown_manifest_version_REFUSES(graph_dir: Path,
                                             monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2))
    seal_active(graph_dir)
    payload = json.loads((graph_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload["version"] = 99
    (graph_dir / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(JournalLayoutError) as exc:
        discover(graph_dir)
    assert "refusing rather than guessing" in str(exc.value)


def test_sealing_repeatedly_yields_a_contiguous_sequence_and_a_matching_manifest(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Each seal takes the NEXT index. The manifest must describe exactly that."""
    monkeypatch.setenv(ENV_FLAG, "1")
    for i in range(3):
        write_journal(graph_dir, records(2, f"s{i}"))
        seal_active(graph_dir)
    write_journal(graph_dir, records(2, "live"))

    layout = discover(graph_dir)
    verify(layout)
    assert [s.name for s in layout.segments] == [
        "events.0000.jsonl", "events.0001.jsonl", "events.0002.jsonl",
        ACTIVE_NAME]
    manifest = read_manifest(graph_dir)
    assert [row["name"] for row in manifest["segments"]] == [
        "events.0000.jsonl", "events.0001.jsonl", "events.0002.jsonl"]
    assert manifest["boundary"]["through_index"] == 2
    assert reconstruction_scope(layout).full is True


# ---------------------------------------------------------------------------
# the four anchors the first review round found MISSING
# (each of these certified full reconstruction before the anchor existed)
# ---------------------------------------------------------------------------


def test_ANCHOR1_an_unplaceable_jsonl_is_REFUSED_not_ignored(graph_dir: Path):
    """`rebuild_all` folds every `*.jsonl`; a name we cannot place is not 'absent'.

    Measured before the anchor: `events.7.jsonl` (a hand-typed seal — the name an
    operator produces, since 4-digit padding is this module's internal
    convention) was replayed by `rebuild_all` and invisible to `discover`, so the
    journal was certified complete while the replay folded a file the claim did
    not know about.
    """
    write_journal(graph_dir, records(2, "a"))
    (graph_dir / "events.7.jsonl").write_bytes(
        (graph_dir / ACTIVE_NAME).read_bytes())

    with pytest.raises(SegmentSequenceError) as exc:
        discover(graph_dir)
    assert "events.7.jsonl" in str(exc.value)
    assert "rebuild_all replays them" in str(exc.value)


def test_ANCHOR1b_sealed_segments_without_a_manifest_are_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Deleting the manifest must not launder an archived journal into 'complete'.

    Measured before the anchor: seal → archive → `rm journal-manifest.json` →
    `full_reconstruction: True` over a journal missing a segment.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    (graph_dir / MANIFEST_NAME).unlink()

    with pytest.raises(SegmentSequenceError) as exc:
        discover(graph_dir)
    assert "no journal-manifest.json" in str(exc.value)
    assert recon_false(graph_dir)["full_reconstruction"] is False


def test_ANCHOR3_a_deleted_active_segment_is_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The live tail is part of the claim.

    Measured before the anchor: seal → append b1,b2 → `rm events.jsonl` →
    `verified: True, full_reconstruction: True`, and the two newest records were
    simply gone from the answer.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(2, "b"))
    (graph_dir / ACTIVE_NAME).unlink()

    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "the live tail is missing" in str(exc.value)


def test_ANCHOR2_a_truncated_sealed_segment_is_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The recorded size is the anchor; the evidence was in the manifest already.

    Measured before the anchor: seal (manifest records 3 lines) → truncate the
    sealed segment to 0 bytes → `verified: True, full_reconstruction: True`, and
    the replay returned one record for a journal that had three.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(3, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(1, "b"))
    (graph_dir / "events.0000.jsonl").write_bytes(b"")

    with pytest.raises(SegmentModifiedError) as exc:
        verify(discover(graph_dir))
    assert "rewritten in place" in str(exc.value)
    assert recon_false(graph_dir)["full_reconstruction"] is False


def test_ANCHOR4_a_five_digit_index_is_REFUSED_because_it_sorts_wrong(
        graph_dir: Path):
    """`events.10000` sorts BEFORE `events.9999` — the width is a floor, not a fix.

    The module must check the ORDER it will actually get, not the width it writes:
    at index 10000 the padding stops being fixed and `sorted()` silently folds
    the journal out of sequence.
    """
    for name in ("events.9998.jsonl", "events.9999.jsonl",
                 "events.10000.jsonl", "events.10001.jsonl"):
        (graph_dir / name).write_bytes(b'{"type":"X"}\n')
    (graph_dir / ACTIVE_NAME).write_bytes(b'{"type":"Y"}\n')
    write_manifest(graph_dir, ["events.9998.jsonl", "events.9999.jsonl",
                               "events.10000.jsonl", "events.10001.jsonl"])

    assert sorted(p.name for p in graph_dir.glob("events.*.jsonl"))[:2] == [
        "events.10000.jsonl", "events.10001.jsonl"]

    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "not the numeric order" in str(exc.value)


def recon_false(graph_dir: Path) -> dict:
    """`summarise` on a layout the module refuses — the report, not a raise."""
    return summarise(graph_dir)


# ---------------------------------------------------------------------------
# the claim's two directions, and the boundary invariant
# ---------------------------------------------------------------------------


def test_complete_from_names_the_first_REPRODUCIBLE_segment(graph_dir: Path,
                                                            monkeypatch):
    """`first_missing` and `complete_from` point in opposite directions."""
    monkeypatch.setenv(ENV_FLAG, "1")
    for i in range(3):
        write_journal(graph_dir, records(2, f"s{i}"))
        seal_active(graph_dir)
    write_journal(graph_dir, records(1, "live"))
    (graph_dir / "events.0000.jsonl").unlink()
    record_disposition(graph_dir, "events.0000.jsonl", "archived")

    scope = reconstruction_scope(discover(graph_dir))
    assert scope.full is False
    assert scope.first_missing == "events.0000.jsonl"
    assert scope.complete_from == "events.0001.jsonl"
    with pytest.raises(ReconstructionClaimError) as exc:
        assert_full_reconstruction(discover(graph_dir))
    assert "'complete from events.0001.jsonl forward'" in str(exc.value)


def test_putting_the_bytes_back_RESTORES_the_claim(graph_dir: Path, monkeypatch):
    """A boundary must be recomputed from the rows, not pinned once.

    Before the fix `first_missing` was only ever SET, so a journal that was
    archived and then restored reported `full=False` forever with no API to clear
    it — the module permanently disabled the claim it exists to make.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    bytes_back = (graph_dir / "events.0000.jsonl").read_bytes()

    (graph_dir / "events.0000.jsonl").unlink()
    record_disposition(graph_dir, "events.0000.jsonl", "archived")
    assert reconstruction_scope(discover(graph_dir)).full is False

    (graph_dir / "events.0000.jsonl").write_bytes(bytes_back)
    record_disposition(graph_dir, "events.0000.jsonl", "present")
    scope = reconstruction_scope(discover(graph_dir))
    assert scope.full is True
    assert scope.first_missing is None
    assert_full_reconstruction(discover(graph_dir))


def test_a_boundary_that_disagrees_with_the_rows_is_REFUSED(graph_dir: Path):
    """Two truth sources, one invariant: they must agree."""
    write_journal(graph_dir, records(2, "a"))
    sealed("events.0000.jsonl", graph_dir)
    write_journal(graph_dir, records(1, "b"))
    write_manifest(graph_dir, ["events.0000.jsonl"])
    payload = json.loads((graph_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload["boundary"]["first_missing"] = "events.0000.jsonl"   # a lie: it is present
    (graph_dir / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "the boundary and the segment rows disagree" in str(exc.value)


# ---------------------------------------------------------------------------
# damaged records are a refusal, not a silent short read
# ---------------------------------------------------------------------------


def test_a_damaged_record_makes_the_strict_read_REFUSE(graph_dir: Path):
    """`read_for_replay` salvages; a completeness claim may not borrow that.

    Before the fix a malformed line was skipped and counted on a throwaway
    `EventLog`, so `read_all_segments` returned a record stream short by one
    while `assert_full_reconstruction` passed.
    """
    write_journal(graph_dir, records(3, "a"))
    path = graph_dir / ACTIVE_NAME
    lines = path.read_bytes().split(b"\n")
    lines[1] = b'{"type": "PointRev'                    # torn mid-file
    path.write_bytes(b"\n".join(lines))

    with pytest.raises(JournalLayoutError) as exc:
        read_all_segments(graph_dir)
    assert "damaged records" in str(exc.value)

    salvaged = read_all_segments(graph_dir, salvage=True)   # explicit choice
    assert [r["id"] for r in salvaged] == ["a0", "a2"]


# ---------------------------------------------------------------------------
# the manifest itself: refusals in the right shape
# ---------------------------------------------------------------------------


def test_a_manifest_without_a_boundary_REFUSES_instead_of_a_bare_KeyError(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2))
    seal_active(graph_dir)
    payload = json.loads((graph_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload.pop("boundary")
    (graph_dir / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(JournalLayoutError) as exc:
        record_disposition(graph_dir, "events.0000.jsonl", "archived")
    assert "carries no `boundary` object" in str(exc.value)


def test_summarise_REPORTS_a_manifest_it_cannot_parse(graph_dir: Path):
    """An operator tool must not raise on the most alarming state there is."""
    write_journal(graph_dir, records(2))
    (graph_dir / MANIFEST_NAME).write_text("{ not json", encoding="utf-8")

    report = summarise(graph_dir)
    assert report["verified"] is False
    assert report["full_reconstruction"] is False
    assert "not valid JSON" in report["reason"]
    assert report["segments"] == []


def test_concurrent_manifest_writers_do_not_lose_a_row(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The two writers serialise their read-modify-write.

    Before the lock, interleaving `seal_active` with `record_disposition` lost
    the newly sealed row while the segment stayed on disk — and NOTHING could
    repair it (`record_disposition` only mutates existing rows).
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    for i in range(6):
        write_journal(graph_dir, records(2, f"s{i}"))
        seal_active(graph_dir)
    write_journal(graph_dir, records(1, "live"))
    names = [f"events.0{i:03d}.jsonl" for i in range(6)]
    for name in names:
        (graph_dir / name).unlink()

    def archive(name: str) -> None:
        record_disposition(graph_dir, name, "archived")

    threads = [threading.Thread(target=archive, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    layout = discover(graph_dir)
    verify(layout)                                  # no orphaned / lost row
    manifest = read_manifest(graph_dir)
    assert sorted(row["name"] for row in manifest["segments"]) == names
    assert all(row["disposition"] == "archived"
               for row in manifest["segments"])
    assert reconstruction_scope(layout).first_missing == names[0]


def test_a_stale_append_handle_follows_the_rename_and_the_journal_stays_consistent(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """A writer that held the pre-seal fd does not write into the sealed segment.

    `EventLog` revalidates its cached fd against the path's identity before an
    append (`log.py::_records_before` / `_identity`), so a SEQUENTIAL stale handle
    reopens onto the new active segment and the sealed segment stays exactly the
    size the manifest recorded.

    ⚠️ **The window this does not close is a TOCTOU one**, and it is a stated
    residual rather than a hidden one: a writer that revalidates, is pre-empted,
    and then writes, lands in the sealed inode — measured while writing this test
    as a sealed segment growing from 35 B to 70 B under two threads. That is why
    sealing is a **maintenance operation, not a hot-path one**, and why ANCHOR 2
    (the recorded size) exists: when it happens, every read refuses instead of
    certifying a sealed segment that is still growing.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1, "a"))
    held = EventLog(graph_dir / ACTIVE_NAME)     # opens/caches on the active file
    held.append({"type": "PointRevised", "id": "pre"})
    before = (graph_dir / ACTIVE_NAME).stat().st_size

    seal_active(graph_dir)
    sealed_size = (graph_dir / "events.0000.jsonl").stat().st_size
    assert sealed_size == before

    held.append({"type": "PointRevised", "id": "post"})
    assert (graph_dir / "events.0000.jsonl").stat().st_size == sealed_size
    assert (graph_dir / ACTIVE_NAME).stat().st_size > 0

    layout = discover(graph_dir)
    verify(layout)                               # the manifest still describes it
    # `pre` was appended BEFORE the seal, so it belongs to the sealed segment;
    # `post` went to the NEW active one — which is the property under test.
    assert [r["id"] for r in read_all_segments(graph_dir)] == ["a0", "pre", "post"]
