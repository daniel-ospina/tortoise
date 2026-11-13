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
from pathlib import Path

import pytest

from tortoise.journal_segments import (
    ACTIVE_NAME,
    ENV_FLAG,
    MANIFEST_NAME,
    JournalLayoutError,
    ReconstructionClaimError,
    SegmentationDisabled,
    SegmentMissingError,
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
    assert scope.from_segment is None
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
    assert scope.from_segment == "events.0000.jsonl"
    assert "archived" in scope.reason

    with pytest.raises(ReconstructionClaimError) as exc:
        assert_full_reconstruction(layout)
    assert "complete from events.0000.jsonl forward" in str(exc.value)

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

    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
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
    assert not (graph_dir / ACTIVE_NAME).exists()    # renamed, not deleted

    manifest = read_manifest(graph_dir)
    assert manifest["segments"] == [{
        "disposition": "present", "index": 0, "name": "events.0000.jsonl",
        "bytes": len(before_bytes), "lines": 6}]
    assert manifest["boundary"]["through_index"] == 0
    assert manifest["boundary"]["first_incomplete"] is None


def test_a_sealed_segment_sorts_into_replay_order_before_the_active_one(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """`rebuild_all` unions sorted(os.listdir) — so the NAME must carry the order."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(2, "b"))

    rebuilt_view = sorted(f for f in os.listdir(graph_dir)
                          if f.endswith(".jsonl"))
    assert rebuilt_view == ["events.0000.jsonl", ACTIVE_NAME]
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
