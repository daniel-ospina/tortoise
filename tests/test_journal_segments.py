"""#9025 — the journal's segment boundary must refuse to lie.

THE PROPERTY UNDER TEST, in one sentence: **a restore may not claim full
reconstruction across a boundary it cannot cross, and a segment may not vanish
without a recorded disposition.**

Every test here drives the mechanism that makes that property true. The ones
named ``test_FALSIFIER_*`` are the ones that must go RED when the guard is
removed — ``tests/test_journal_segments_falsifier.py`` is what removes it (a
mutation per guard, plus one combined mutant), and the PR body records the run.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from tortoise import journal_segments
from tortoise.journal_segments import (
    ACTIVE_NAME,
    ENV_FLAG,
    MANIFEST_NAME,
    MANIFEST_VERSION,
    SEALED_RE,
    JournalLayoutError,
    ReconstructionClaimError,
    SegmentationDisabled,
    SegmentMissingError,
    SegmentModifiedError,
    SegmentSequenceError,
    adopt_crashed_seal,
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


def _real_lines(path: Path) -> int:
    """The manifest's `lines` is a REAL count — the module compares it to disk."""
    return len([line for line in path.read_bytes().split(b"\n") if line.strip()])


def _real_sha(path: Path) -> str:
    """The manifest's `sha256` must be the real digest — it is the identity anchor."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        path = directory / name
        # A DEPARTED row still carries the anchors it was sealed with; the module
        # does not compare them (nothing is on disk to compare against).
        rows.append({"name": name, "index": index,
                     "bytes": path.stat().st_size if path.exists() else 1,
                     "lines": _real_lines(path) if path.exists() else 1,
                     "sha256": _real_sha(path) if path.exists() else "0" * 64,
                     "disposition": dispositions.get(name, "present"),
                     "recorded_at": "2026-11-13T00:00:00+00:00"})
    first_missing = next((r["name"] for r in sorted(
        rows, key=lambda r: (r["index"] is None, r["index"] or -1))
        if r["disposition"] != "present"), None)
    (directory / MANIFEST_NAME).write_text(json.dumps({
        "version": MANIFEST_VERSION,
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


# ---------------------------------------------------------------------------
# cycle-2 findings
# ---------------------------------------------------------------------------


def test_ANCHOR2b_a_record_dropped_and_the_bytes_PADDED_BACK_is_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Bytes alone is not enough: the same length can hold one record fewer.

    Cycle-2 measured this: drop a record line from a sealed segment and pad the
    file back to the recorded byte count with trailing blanks (which `read_all`
    skips WITHOUT counting, so no damage counter fires) → `full_reconstruction:
    True` over a stream short by one. The recorded LINE count is the datum that
    catches it, and it was already in the manifest.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(3, "a"))
    seal_active(graph_dir)
    segment = graph_dir / "events.0000.jsonl"
    recorded_bytes = segment.stat().st_size
    lines = segment.read_bytes().split(b"\n")
    lines = [line for line in lines if line.strip()][:-1]        # drop the last record
    body = b"\n".join(lines) + b"\n"
    segment.write_bytes(body + b"\n" * (recorded_bytes - len(body)))   # same size

    assert segment.stat().st_size == recorded_bytes
    with pytest.raises(SegmentModifiedError) as exc:
        verify(discover(graph_dir))
    assert "lines on disk" in str(exc.value)


def test_the_claim_says_when_it_is_UNANCHORED(graph_dir: Path):
    """An unsealed journal's claim rests on the files present, and says so.

    Cycle-2's second P1: nothing can anchor a file that is still being appended
    to, so truncating the ACTIVE segment is undetected. That is declared — and
    the declaration is visible in the answer rather than only in a docstring,
    because `anchored=False` is the state EVERY deployment is in today.
    """
    write_journal(graph_dir, records(2))
    scope = reconstruction_scope(discover(graph_dir))
    assert scope.full is True
    assert scope.anchored is False
    assert "UNANCHORED" in scope.reason
    report = summarise(graph_dir)
    assert report["full_reconstruction"] is True
    assert report["anchored"] is False

    # …and it becomes anchored once something is sealed
    monkeypatch_env = pytest.MonkeyPatch()
    monkeypatch_env.setenv(ENV_FLAG, "1")
    try:
        seal_active(graph_dir)
    finally:
        monkeypatch_env.undo()
    assert reconstruction_scope(discover(graph_dir)).anchored is True


def test_a_directory_named_jsonl_is_REFUSED(graph_dir: Path):
    """`rebuild_all` filters on the NAME, so a dict named `sub.jsonl` is replayed.

    Cycle-2: `is_file()` hid it from `discover` while the replay would raise
    `IsADirectoryError` opening it — a claim over a journal nothing can fold.
    """
    write_journal(graph_dir, records(1))
    (graph_dir / "sub.jsonl").mkdir()

    with pytest.raises(SegmentSequenceError) as exc:
        discover(graph_dir)
    assert "sub.jsonl" in str(exc.value)


def test_the_writer_refuses_an_index_its_own_verifier_would_reject(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Never EMIT a broken layout: 10000 sorts before 9999.

    Cycle-2: `seal_active` renamed to `events.10000.jsonl` and wrote the row,
    after which every read and every write refused — the writer created the state
    anchor 4 exists to detect, and the repair was renaming ten thousand files.
    """
    (graph_dir / "events.9999.jsonl").write_bytes(b'{"type":"X"}\n')
    (graph_dir / ACTIVE_NAME).write_bytes(b'{"type":"Y"}\n')
    write_manifest(graph_dir, ["events.9999.jsonl"])

    monkeypatch.setenv(ENV_FLAG, "1")
    # ⛔ The WRITER, not its private helper: a guard reachable only through
    # `_next_index` would leave `seal_active` free to emit the broken name.
    with pytest.raises(SegmentSequenceError) as exc:
        seal_active(graph_dir)
    assert "5-digit name sorts BEFORE a 4-digit one" in str(exc.value)
    # …and nothing was renamed on the way to the refusal
    assert (graph_dir / "events.9999.jsonl").exists()
    assert not (graph_dir / "events.10000.jsonl").exists()


def test_summarise_reports_a_MISTYPED_manifest_instead_of_raising(graph_dir: Path,
                                                                 monkeypatch):
    """Parseable-but-wrong JSON must not crash the one verb that reports.

    Cycle-2: `int(raw.get('through_index'))` escaped as a bare `ValueError`, and a
    null `bytes` on a departed row as `TypeError`.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1))
    (graph_dir / MANIFEST_NAME).write_text(json.dumps({
        "version": MANIFEST_VERSION, "segments": [],
        "boundary": {"through_index": "nope"},
    }), encoding="utf-8")
    report = summarise(graph_dir)
    assert report["verified"] is False
    assert "non-numeric through_index" in report["reason"]

    # ⛔ `bytes: None` is now refused at PARSE, not skipped at the anchor: an
    # absent anchor field used to disable the check (cycle-3 finding P2-1).
    (graph_dir / MANIFEST_NAME).write_text(json.dumps({
        "version": MANIFEST_VERSION,
        "segments": [{"name": "events.0000.jsonl", "index": 0, "bytes": None,
                      "lines": 1, "sha256": "x", "disposition": "archived"}],
        "boundary": {"through_index": 0, "first_missing": "events.0000.jsonl"},
    }), encoding="utf-8")
    report = summarise(graph_dir)
    assert report["verified"] is False
    assert "no integer `bytes`" in report["reason"]

    # …and the same for a row that is not an object at all, which used to raise
    # a bare AttributeError from `record_disposition`.
    (graph_dir / MANIFEST_NAME).write_text(json.dumps({
        "version": MANIFEST_VERSION, "segments": ["events.0000.jsonl"],
        "boundary": {"through_index": 0, "first_missing": None},
    }), encoding="utf-8")
    assert summarise(graph_dir)["verified"] is False
    with pytest.raises(JournalLayoutError) as exc:
        record_disposition(graph_dir, "events.0000.jsonl", "archived")
    assert "not a named object" in str(exc.value)


def test_a_writer_on_a_missing_directory_raises_a_named_refusal(tmp_path: Path,
                                                               monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    with pytest.raises(JournalLayoutError) as exc:
        seal_active(tmp_path / "nope")
    assert "not a directory" in str(exc.value)


def test_a_TORN_TAIL_is_tolerated_while_MID_FILE_damage_is_not(graph_dir: Path):
    """`log.py` distinguishes them on purpose; this reader must not conflate them.

    A trailing fragment was never a complete record (SIGKILL on the last append),
    where a skipped mid-file line was a record the journal held and lost.
    Cycle-2: both raised, which would make the reader unusable after any killed
    process.
    """
    write_journal(graph_dir, records(2, "a"))
    path = graph_dir / ACTIVE_NAME
    path.write_bytes(path.read_bytes() + b'{"type": "PointRev')   # torn tail

    assert [r["id"] for r in read_all_segments(graph_dir)] == ["a0", "a1"]


def test_plan_rotation_refuses_a_nonsense_threshold(graph_dir: Path):
    """`max_bytes=0` would mint an empty sealed segment per call."""
    write_journal(graph_dir, records(1))
    with pytest.raises(ValueError) as exc:
        plan_rotation(graph_dir, max_bytes=0)
    assert "must be positive" in str(exc.value)


def test_the_manifest_lock_is_reentrant_within_one_thread(graph_dir: Path,
                                                          monkeypatch):
    """A nested writer must not deadlock (flock is per open-file-description)."""
    from tortoise.journal_segments import _manifest_lock
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1))

    # ⛔ TWO contexts on purpose: a naive lock blocks forever on the second
    # (flock is per open-file-description), so combining them would defeat the
    # test. `noqa` because the linter's simplify is exactly the change under test.
    with _manifest_lock(graph_dir), _manifest_lock(graph_dir):
        seal_active(graph_dir)
    assert (graph_dir / "events.0000.jsonl").exists()


def test_the_replay_enumeration_this_module_assumes_has_not_changed():
    """CANARY: anchors 1 and 4 rest on `rebuild_all`'s own enumeration.

    Cycle-2 (P2): the order test rebuilt `sorted(os.listdir(db_dir))` +
    `endswith('.jsonl')` locally, so if the real enumerator changed (a glob, a
    recursion, another suffix) the test would stay green over a false premise.
    This pins the predicate in the source it comes from.
    """
    source = (Path(__file__).resolve().parent.parent
              / "tortoise/projection/__init__.py").read_text(encoding="utf-8")
    assert "sorted(os.listdir(log_dir))" in source
    assert "fname.endswith('.jsonl')" in source


# ---------------------------------------------------------------------------
# cycle-3 findings
# ---------------------------------------------------------------------------


def test_ANCHOR2c_a_size_change_that_KEEPS_the_line_count_is_still_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The size anchor still refuses, and now names the SIZE as the cause.

    After cycle 3 the digest would catch this too — so this test pins the
    DIAGNOSIS, which is what an operator acts on ("rewritten in place" says where
    to look; "does not hash" says the content changed but not how). The falsifier
    mutant for the size anchor asserts this message specifically, rather than
    claiming a lie becomes reachable — it does not, and saying otherwise would be
    a vacuous kill.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    segment = graph_dir / "events.0000.jsonl"
    lines = [line for line in segment.read_bytes().split(b"\n") if line.strip()]
    lines[0] = b'{"id":"a0"}'                        # shorter, same line count
    segment.write_bytes(b"\n".join(lines) + b"\n")
    assert _real_lines(segment) == 2

    with pytest.raises(SegmentModifiedError) as exc:
        verify(discover(graph_dir))
    assert "rewritten in place" in str(exc.value)


def test_ANCHOR5_two_sealed_segments_SWAPPED_is_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Size and line count both survive a swap; identity does not.

    Cycle-3's P1, measured: seal two equal-length segments, exchange their bytes
    → `verify` PASSED, `full=True`, and the replay returned them INVERTED. The
    claim said complete over a journal that replays in the wrong order, because
    nothing tied a NAME to a CONTENT.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "aaaa"))     # equal-length lines
    seal_active(graph_dir)
    write_journal(graph_dir, records(2, "bbbb"))
    seal_active(graph_dir)
    first = graph_dir / "events.0000.jsonl"
    second = graph_dir / "events.0001.jsonl"
    assert first.stat().st_size == second.stat().st_size

    first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
    first.write_bytes(second_bytes)
    second.write_bytes(first_bytes)

    with pytest.raises(SegmentModifiedError) as exc:
        verify(discover(graph_dir))
    assert "does not hash to what the manifest recorded" in str(exc.value)


def test_ANCHOR5b_a_SAME_LENGTH_substitution_is_REFUSED(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """One record rewritten in place, same length — the digest is the only guard."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    segment = graph_dir / "events.0000.jsonl"
    original = segment.read_bytes()
    segment.write_bytes(original.replace(b'"a0"', b'"z0"'))
    assert segment.stat().st_size == len(original)

    with pytest.raises(SegmentModifiedError) as exc:
        verify(discover(graph_dir))
    assert "CONTENT changed while its size and line count did not" in str(exc.value)


def test_a_manifest_row_that_OMITS_an_anchor_field_is_REFUSED(graph_dir: Path):
    """A field whose ABSENCE disables a check is a fail-open.

    Cycle-3 (P2-1), measured: a valid manifest row with no `bytes`/`lines` made
    both anchors skip themselves (`is not None`), so a segment truncated to 0 B
    certified `full=True, anchored=True` with the tail's records still "present".
    """
    write_journal(graph_dir, records(3))
    (graph_dir / "events.0000.jsonl").write_bytes(b"")
    for dropped in ("bytes", "lines", "sha256"):
        row = {"name": "events.0000.jsonl", "index": 0,
               "bytes": 102, "lines": 3, "sha256": "x" * 64,
               "disposition": "present",
               "recorded_at": "2026-11-13T00:00:00+00:00"}
        del row[dropped]
        (graph_dir / MANIFEST_NAME).write_text(json.dumps({
            "version": MANIFEST_VERSION, "segments": [row, {
                "name": ACTIVE_NAME, "index": None, "bytes": 1, "lines": 1,
                "sha256": "y" * 64, "disposition": "present"}],
            "boundary": {"recorded_at": "2026-11-13T00:00:00+00:00",
                         "through_index": 0, "first_missing": None},
        }), encoding="utf-8")
        with pytest.raises(JournalLayoutError) as exc:
            verify(discover(graph_dir))
        assert "cannot omit" in str(exc.value) or "no `sha256`" in str(exc.value)
        assert summarise(graph_dir)["verified"] is False


def test_complete_from_starts_after_the_LAST_gap_not_the_first(
        graph_dir: Path):
    """Two holes: "complete from 0002 forward" names a segment followed by a hole.

    Cycle-3 (P2-2), measured on 0000..0003 with 0001 and 0003 archived.
    """
    for i in range(4):
        write_journal(graph_dir, records(1, f"s{i}"))
        (graph_dir / f"events.{i:04d}.jsonl").write_bytes(
            (graph_dir / ACTIVE_NAME).read_bytes())
        (graph_dir / ACTIVE_NAME).unlink()
        (graph_dir / ACTIVE_NAME).touch()
    write_journal(graph_dir, records(1, "live"))
    # the archived ones must actually be GONE, or the manifest does not describe
    # the directory (a `archived` row whose bytes are still here is its own refusal)
    (graph_dir / "events.0001.jsonl").unlink()
    (graph_dir / "events.0003.jsonl").unlink()
    write_manifest(graph_dir,
                   [f"events.{i:04d}.jsonl" for i in range(4)] + [ACTIVE_NAME],
                   {"events.0001.jsonl": "archived",
                    "events.0003.jsonl": "archived"})
    scope = reconstruction_scope(discover(graph_dir))
    assert scope.full is False
    assert scope.first_missing == "events.0001.jsonl"     # where it STOPS
    # ⛔ NOT `events.0002.jsonl`, which the pre-fix code named: that segment is
    # followed immediately by another hole (0003), so "complete from 0002
    # forward" was false. The only thing reproducible after the LAST gap is the
    # live tail.
    assert scope.complete_from == ACTIVE_NAME
    assert scope.complete_from != "events.0002.jsonl"


def test_a_DELETED_live_tail_is_caught_even_if_a_row_claims_it_is_archived(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Anchor 3 must not be satisfied by a DEPARTED row's absence of index.

    Cycle-3 (P2-3), measured: a row `{"name":"events.jsonl","index":null,
    "disposition":"archived"}` made `Layout.active` return a segment that was not
    on disk, so `layout.active is None` was false and the deleted tail read as
    present.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(1, "tail"))

    payload = read_manifest(graph_dir)
    payload["segments"].append({"name": ACTIVE_NAME, "index": None, "bytes": 10,
                                "lines": 1, "sha256": "z" * 64,
                                "disposition": "archived",
                                "recorded_at": "2026-11-13T00:00:00+00:00"})
    payload["boundary"]["first_missing"] = ACTIVE_NAME
    (graph_dir / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
    (graph_dir / ACTIVE_NAME).unlink()

    assert discover(graph_dir).active is None
    with pytest.raises(SegmentSequenceError) as exc:
        verify(discover(graph_dir))
    assert "the live tail is missing" in str(exc.value)


def test_seal_records_the_size_BEFORE_the_rename(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """An append in the rename→stat window must be a mismatch, not blessed.

    Cycle-3 (P2-4): the recorded size was read AFTER the rename, so a record that
    landed in that window was folded into the number anchor 2 compares against.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    before = (graph_dir / ACTIVE_NAME).stat().st_size

    real_digest = journal_segments._digest

    calls = {"n": 0}

    def growing_digest(path: Path):
        """Simulate a writer appending inside the seal's stat→rename window.

        `seal_active` calls this ONCE for the active segment after taking its
        size, so the append below lands after the measurement and before the
        rename — exactly the window cycle-3 found.
        """
        lines, sha = real_digest(path)
        if path.name == ACTIVE_NAME:
            calls["n"] += 1
            if calls["n"] == 2:                  # call 1 is `discover`; this is the seal
                with path.open("ab") as fh:
                    fh.write(b'{"type":"PointRevised","id":"late"}\n')
        return lines, sha

    monkeypatch.setattr(journal_segments, "_digest", growing_digest)
    seal_active(graph_dir)
    monkeypatch.setattr(journal_segments, "_digest", real_digest)

    row = read_manifest(graph_dir)["segments"][0]
    assert row["bytes"] == before                    # the size at SEAL time
    with pytest.raises(SegmentModifiedError):
        verify(discover(graph_dir))                  # the extra record is caught


def test_the_lock_closes_its_descriptor_exactly_once(graph_dir: Path,
                                                     monkeypatch):
    """A double close can land on another thread's fd.

    Cycle-3 (P2-5): both retry paths closed the fd and then the `finally` closed
    it again — the second close is EBADF *unless* the number was reused.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1))

    import tortoise.journal_segments as module
    closes: list[int] = []
    real_close = os.close
    real_stat = os.stat

    def stat_that_moves_the_file(path, *args, **kwargs):
        if str(path).endswith(module.LOCK_NAME):
            raise FileNotFoundError(path)            # force the retry path
        return real_stat(path, *args, **kwargs)

    def counting_close(fd):
        closes.append(fd)
        return real_close(fd)

    monkeypatch.setattr(module.os, "stat", stat_that_moves_the_file)
    monkeypatch.setattr(module.os, "close", counting_close)
    with pytest.raises(JournalLayoutError) as exc:
        seal_active(graph_dir)
    monkeypatch.undo()
    assert "after 5 attempts" in str(exc.value)
    assert len(closes) == len(set(closes)) or len(closes) == 5, \
        f"a descriptor was closed twice: {closes}"


def test_the_report_has_the_SAME_KEYS_on_every_path(graph_dir: Path):
    """A dashboard must not `KeyError` on the most alarming state.

    Cycle-3 (P2-7): the discover-error branch omitted `first_missing`.
    """
    write_journal(graph_dir, records(1))
    ok_keys = set(summarise(graph_dir))
    (graph_dir / "stray.jsonl").write_text("{}", encoding="utf-8")
    bad = summarise(graph_dir)
    assert bad["verified"] is False
    assert set(bad) == ok_keys


def test_tail_anchored_is_False_and_says_why(graph_dir: Path):
    """`anchored` covers the sealed prefix; the tail can never be anchored.

    Cycle-3 (P2-8): five tail records blanked to zero bytes left
    `full=True, anchored=True` — and `anchored`'s docstring told the caller to
    check it for exactly that. The tail is a separate, honest `False`.
    """
    write_journal(graph_dir, records(1, "a"))
    scope = reconstruction_scope(discover(graph_dir))
    assert scope.anchored is False
    assert scope.tail_anchored is False
    report = summarise(graph_dir)
    assert report["tail_anchored"] is False


def test_a_CRASH_between_the_rename_and_the_manifest_write_is_repairable(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The one recoverable-but-unreadable state, and its narrow verb.

    Cycle-3 (P2-11), measured: after a crash in that window EVERY read and BOTH
    writers refuse (correctly — anchor 1 cannot tell a crashed seal from a
    dropped segment), so the graph directory stayed unreadable until someone
    hand-edited the manifest, which is the thing this module exists to prevent.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(2, "a"))
    seal_active(graph_dir)
    # the crash: the rename happened, the manifest write did not, the tail is absent
    (graph_dir / ACTIVE_NAME).rename(graph_dir / "events.0001.jsonl")
    payload = read_manifest(graph_dir)
    (graph_dir / MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SegmentSequenceError):
        verify(discover(graph_dir))          # unreadable, and rightly so

    adopt_crashed_seal(graph_dir)
    verify(discover(graph_dir))              # repaired
    report = summarise(graph_dir)
    assert report["verified"] is True
    assert [s["name"] for s in report["segments"]] == [
        "events.0000.jsonl", "events.0001.jsonl", ACTIVE_NAME]
    assert report["full_reconstruction"] is True
    # it restored the tail the interrupted seal was about to create — EMPTY, so
    # anchor 3 holds without inventing a record
    assert (graph_dir / ACTIVE_NAME).exists()
    assert (graph_dir / ACTIVE_NAME).stat().st_size == 0


def test_adopt_refuses_when_the_missing_segment_is_behind_the_boundary(
        graph_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """The crash verb must not launder a LOSS into "present".

    A missing index that is not `max+1` is a hole in the middle of the sequence:
    adopting it would record a segment nobody sealed and deny the loss.
    """
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1, "a"))
    seal_active(graph_dir)
    write_journal(graph_dir, records(1, "b"))
    seal_active(graph_dir)
    # a crash-shaped extra segment at an index the manifest does not expect
    (graph_dir / ACTIVE_NAME).write_bytes(b'{"type":"PointRevised","id":"x"}\n')
    (graph_dir / ACTIVE_NAME).rename(graph_dir / "events.0007.jsonl")

    with pytest.raises(SegmentSequenceError) as exc:
        adopt_crashed_seal(graph_dir)
    assert "is a LOSS, not a crash" in str(exc.value)


def test_adopt_refuses_when_the_live_tail_is_still_there(graph_dir: Path,
                                                         monkeypatch):
    """A present tail means the writer got past the rename — not the crash shape."""
    monkeypatch.setenv(ENV_FLAG, "1")
    write_journal(graph_dir, records(1, "a"))
    seal_active(graph_dir)
    (graph_dir / ACTIVE_NAME).write_bytes(b'{"type":"PointRevised","id":"x"}\n')
    (graph_dir / "events.0001.jsonl").write_bytes(
        (graph_dir / ACTIVE_NAME).read_bytes())

    with pytest.raises(SegmentSequenceError) as exc:
        adopt_crashed_seal(graph_dir)
    assert "still holds" in str(exc.value)


def test_adopt_is_gated_on_the_flag(graph_dir: Path):
    """Adopting a seal decides a boundary, so it is behind the same switch."""
    write_journal(graph_dir, records(1))
    with pytest.raises(SegmentationDisabled):
        adopt_crashed_seal(graph_dir)
