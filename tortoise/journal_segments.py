"""#9025 — journal segments, a recorded boundary, and a reconstruction claim that cannot lie.

WHY THIS MODULE EXISTS, and what it deliberately does NOT do.

The hosted lane writes one JSONL rebuild journal per graph
(``<TORTOISE_EVENT_LOG_BASE_DIR>/<graph>/events.jsonl``). It grows without
bound, and ``#9025`` prices three ways to bound it: keep it all, compact it
behind a genesis snapshot, or archive the prefix to cheaper media. Picking one
is **an owner decision** — the journal is the rebuild source
(``derived = replay(journal)``), so a bound is a durability-posture change
before it is a disk change.

What a lane MAY build without that decision is the machinery every option needs
and none of them can do without: a way to say **which segments exist**, **what
boundary has been crossed**, and **whether a reader is entitled to claim full
reconstruction**. This module is that, and everything it does today is
reversible and removes no byte:

* :func:`discover`, :func:`verify`, :func:`reconstruction_scope`,
  :func:`assert_full_reconstruction`, :func:`read_all_segments`,
  :func:`plan_rotation` and :func:`summarise` are **READ-ONLY**.
* :func:`seal_active` and :func:`record_disposition` are the only writers. They
  are gated behind :data:`ENV_FLAG` (default OFF), they **rename** and **record**
  — they delete nothing — and with the flag off the deployment's on-disk layout
  and the default write path are byte-for-byte what ships today.

⛔ THE FLAG GATES WRITING, NEVER REFUSING. If ``TORTOISE_JOURNAL_SEGMENTS`` also
disarmed :func:`verify` and :func:`assert_full_reconstruction`, then switching
the flag off would disable the honesty guarantee on a journal that had already
crossed a boundary — the fail-open shape AGENTS.md classifies as category A. A
manifest, once present, is verified on **every** read whatever the flag says.

THE FOUR ANCHORS, and why a manifest alone is not enough. "What is on disk
matches the manifest's rows" is a weaker question than "what is on disk is what
the journal had", and the difference is where every false completeness claim
lives. So the module records and checks four things, not one:

1. **The segment set is the REPLAY's set.** ``rebuild_all`` replays *any*
   ``*.jsonl`` in the directory (``projection/__init__.py``), so a ``*.jsonl``
   this module cannot place in the sequence is REFUSED rather than ignored — an
   ignored file is one the replay folds and the claim does not know about.
2. **A present segment is the size it was sealed at.** Each row records
   ``bytes``/``lines``; a segment that no longer matches is REFUSED
   (:class:`SegmentModifiedError`). Without this, truncating a sealed segment to
   zero bytes certifies "full reconstruction" over an empty journal.
3. **A manifested journal has an active segment.** :func:`seal_active` creates
   the next ``events.jsonl`` (empty) as part of sealing, which turns "somebody
   deleted the live tail" into a detectable state rather than an ambiguous one.
4. **The lexicographic order IS the replay order.** ``rebuild_all`` uses
   ``sorted(os.listdir(...))``, so the names carry the sequence. A fixed width
   makes that true only up to its width — ``events.10000.jsonl`` sorts *before*
   ``events.9999.jsonl`` — so :func:`verify` compares the sorted name order
   against the numeric order and refuses when they disagree, instead of trusting
   the width.

THE DANGEROUS PROPERTY, and the falsifier that guards it. Once a segment's bytes
are not all present, a restore can no longer claim full reconstruction — and the
failure that matters is not "the restore was incomplete", it is **"the restore
said it was complete"**. So the claim is a separate, checkable object
(:class:`ReconstructionScope`) and one function asserts it
(:func:`assert_full_reconstruction`), which raises
:class:`ReconstructionClaimError` rather than returning a falsy value a caller
might ignore.

⛔ **DECLARED RESIDUAL — a manifest is evidence, not proof.** Everything here is
a record that a deployment *wrote*; a deployment that deletes the manifest AND
every sealed segment leaves this module with nothing to compare, and it will
report the remaining files as a complete journal. That is unavoidable for any
bookkeeping file (it is the same shape as a rescue-file version gate), and it is
stated rather than papered over: :func:`summarise` reports ``manifest_present``,
so an operator can see that nothing was ever sealed. Deleting the manifest while
sealed segments REMAIN is detectable and is refused (anchor 1).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

#: The switch. **OFF by default** — nothing in this module rotates a journal
#: unless a deployment opts in. See the module docstring: it gates WRITING only.
ENV_FLAG = "TORTOISE_JOURNAL_SEGMENTS"

#: The active journal's name — ``config._resolve_event_log_path`` and every
#: reader in the tree already use exactly this.
ACTIVE_NAME = "events.jsonl"

#: The manifest that records the sequence and each segment's disposition.
MANIFEST_NAME = "journal-manifest.json"

#: The lock the writers take around their read-modify-write of the manifest.
LOCK_NAME = ".journal-manifest.lock"

#: ``events.0007.jsonl`` — at least four digits, so `sorted()` is numeric for
#: indices below 10000. Above that the width is no longer fixed, which is why
#: :func:`verify` checks the ORDER rather than trusting the padding (anchor 4).
SEALED_RE = re.compile(r"^events\.(\d{4,})\.jsonl$")

#: Where a segment's bytes are. ``present`` is the only disposition that keeps a
#: full-reconstruction claim available; the other two are recorded *decisions*,
#: which is the difference between a bound and silent truncation.
Disposition = Literal["present", "archived", "compacted"]

MANIFEST_VERSION = 1


class JournalLayoutError(RuntimeError):
    """The journal's segment layout cannot be trusted."""


class SegmentSequenceError(JournalLayoutError):
    """The sealed indices have a gap, a duplicate, or an unplaceable ``*.jsonl``.

    A gap means a segment is gone and nothing recorded that it went. Replaying
    the survivors and reporting success is the failure this class exists for.
    """


class SegmentMissingError(JournalLayoutError):
    """A manifest says a segment is ``present`` and the directory does not hold it."""


class SegmentModifiedError(JournalLayoutError):
    """A segment is not the size the manifest recorded for it.

    Truncating or rewriting a sealed segment is the one edit that makes a
    journal look complete while holding less than it did — the exact event the
    ``compacted`` disposition exists to record honestly.
    """


class ReconstructionClaimError(JournalLayoutError):
    """A caller claimed full reconstruction across a boundary that forbids it."""


class SegmentationDisabled(RuntimeError):
    """A write was attempted while :data:`ENV_FLAG` is off."""


def enabled() -> bool:
    """Is sealing switched on for this process? (OFF unless explicitly ``1``.)

    Read at call time, never cached at import: a fleet sets it per lane and a
    cached read would make the switch depend on which process imported first.
    """
    return os.environ.get(ENV_FLAG, "") == "1"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Segment:
    """One journal file in a graph directory."""

    name: str
    index: int | None      # None for the ACTIVE segment; a sealed index otherwise
    bytes: int
    lines: int
    disposition: Disposition = "present"
    #: False for a segment the MANIFEST records while the directory does not
    #: hold it — the shape that must refuse, not disappear from the layout.
    on_disk: bool = True
    #: What the manifest recorded at seal time (``None`` for the active segment,
    #: which the manifest does not describe).
    recorded_bytes: int | None = None
    recorded_lines: int | None = None
    #: When the manifest last wrote this row — what makes a disposition an
    #: audit trail rather than an assertion with no date on it.
    recorded_at: str | None = None

    @property
    def sealed(self) -> bool:
        return self.index is not None


@dataclass(frozen=True)
class Boundary:
    """The recorded edge of what the journal still holds in full.

    ``through_index`` is the highest segment index the journal HAD when the
    boundary was recorded; ``first_missing`` names the lowest-index segment whose
    bytes this deployment no longer keeps, and is ``None`` while nothing has
    left. A claim about *reconstruction*, never about durability —
    ``docs/durability-posture.md`` keeps authority over the latter.
    """

    recorded_at: str
    through_index: int
    first_missing: str | None = None


@dataclass(frozen=True)
class Layout:
    """What a graph directory holds, and what may therefore be claimed of it."""

    directory: Path
    segments: tuple[Segment, ...]
    boundary: Boundary | None
    manifest_present: bool

    @property
    def total_bytes(self) -> int:
        return sum(s.bytes for s in self.segments)

    @property
    def active(self) -> Segment | None:
        for seg in self.segments:
            if not seg.sealed:
                return seg
        return None


@dataclass(frozen=True)
class ReconstructionScope:
    """The strongest honest claim a restore from this layout may make.

    ⛔ ``first_missing`` and ``complete_from`` point in OPPOSITE directions and
    are named separately because an earlier version conflated them: the missing
    segment is where the journal STOPS being reproducible, and the complete one
    is where it STARTS again.
    """

    full: bool
    first_missing: str | None
    complete_from: str | None
    reason: str


@dataclass(frozen=True)
class RotationPlan:
    """What :func:`seal_active` WOULD do — computed without touching anything."""

    active: Segment
    seal_to: str
    next_index: int
    boundary: Boundary


# --------------------------------------------------------------------------
# reading the layout (read-only; never writes, renames or deletes)
# --------------------------------------------------------------------------


def _count_lines(path: Path) -> int:
    """Non-empty lines in *path*. The journal's unit is the line."""
    n = 0
    with path.open("rb") as fh:
        for chunk in fh:
            if chunk.strip():
                n += 1
    return n


def _segment_from_path(path: Path, disposition: Disposition = "present",
                       recorded_bytes: int | None = None,
                       recorded_lines: int | None = None) -> Segment:
    match = SEALED_RE.match(path.name)
    return Segment(
        name=path.name,
        index=int(match.group(1)) if match else None,
        bytes=path.stat().st_size,
        lines=_count_lines(path),
        disposition=disposition,
        recorded_bytes=recorded_bytes,
        recorded_lines=recorded_lines,
    )


def manifest_path(graph_dir: str | Path) -> Path:
    return Path(graph_dir) / MANIFEST_NAME


def read_manifest(graph_dir: str | Path) -> dict | None:
    """The manifest as stored, or ``None`` when this graph has never sealed.

    ⛔ A manifest that exists but cannot be parsed is an ERROR, not an absent
    manifest: treating it as absent is how a recorded boundary gets laundered
    into a full-reconstruction claim.
    """
    path = manifest_path(graph_dir)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:                      # pragma: no cover - fs fault
        raise JournalLayoutError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise JournalLayoutError(
            f"{path} is not valid JSON — a recorded boundary cannot be "
            f"discounted just because it cannot be parsed ({exc})") from exc
    if not isinstance(data, dict) or "segments" not in data:
        raise JournalLayoutError(f"{path} does not carry a `segments` list")
    version = data.get("version")
    if version != MANIFEST_VERSION:
        # Same posture as the rescue-file version gate in durability-posture.md:
        # a reader that does not know the format must REFUSE, not guess.
        raise JournalLayoutError(
            f"{path} is manifest version {version!r}, this build writes "
            f"{MANIFEST_VERSION} — refusing rather than guessing")
    if not isinstance(data.get("boundary"), dict):
        # `record_disposition` reads it, and a bare `KeyError` from a writer whose
        # whole posture is named refusals is the wrong shape.
        raise JournalLayoutError(
            f"{path} carries no `boundary` object — the boundary is what makes "
            f"the sequence meaningful, so a manifest without one is refused")
    return data


def discover(graph_dir: str | Path) -> Layout:
    """Enumerate a graph directory's journal segments, in REPLAY ORDER.

    Read-only. Raises on a layout it cannot describe honestly — the caller does
    not get to decide what was ignored, because ``rebuild_all`` will replay every
    ``*.jsonl`` it finds whether this module described it or not.
    """
    directory = Path(graph_dir)
    if not directory.is_dir():
        raise JournalLayoutError(f"not a directory: {directory}")

    manifest = read_manifest(directory)
    declared: dict[str, dict] = {}
    if manifest is not None:
        for row in manifest["segments"]:
            if not isinstance(row, dict) or "name" not in row:
                raise JournalLayoutError(
                    f"{manifest_path(directory)} has a segment row without a name")
            declared[row["name"]] = row

    journalled: dict[str, Path] = {}
    unplaceable: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not path.name.endswith(".jsonl"):
            continue
        if path.name == ACTIVE_NAME or SEALED_RE.match(path.name):
            journalled[path.name] = path
            continue
        unplaceable.append(path.name)

    # ANCHOR 1: the replay folds every `*.jsonl`; a name this module cannot place
    # in the sequence is a file whose position in the log is unknown, so the
    # claim cannot be made over it. Refuse rather than ignore.
    if unplaceable:
        raise SegmentSequenceError(
            f"{sorted(unplaceable)} end in .jsonl, so rebuild_all replays them, "
            f"but they are neither {ACTIVE_NAME} nor a sealed "
            f"events.<NNNN>.jsonl — their position in the sequence is unknown. "
            f"Rename them into the sequence or move them out of "
            f"{directory}.")

    found: list[Segment] = []
    for name, path in journalled.items():
        row = declared.get(name) or {}
        disposition = row.get("disposition", "present")
        if disposition not in ("present", "archived", "compacted"):
            raise JournalLayoutError(
                f"{name} has unknown disposition {disposition!r}")
        found.append(_segment_from_path(
            path, disposition,
            recorded_bytes=row.get("bytes"),
            recorded_lines=row.get("lines")))
        if row.get("recorded_at"):
            found[-1] = dataclass_replace(found[-1],
                                          recorded_at=row["recorded_at"])

    # ANCHOR 1 (second half): sealed segments on disk with no manifest means the
    # record of the sequence was deleted while the sequence survived. Nothing
    # can say what order they belong in, and every check below would be skipped
    # because `manifest_present` is False.
    if manifest is None:
        sealed_names = sorted(s.name for s in found if s.sealed)
        if sealed_names:
            raise SegmentSequenceError(
                f"{directory} holds sealed segment(s) {sealed_names} but no "
                f"{MANIFEST_NAME} — the record of the sequence is gone, so "
                f"neither their order nor their completeness can be established")

    # ⛔ A segment the manifest RECORDS while the directory does not hold it
    # stays in the layout with `on_disk=False`. Dropping it would make the
    # boundary invisible to `reconstruction_scope` and turn "archived, so no
    # full claim" into "nothing to see" — the laundered claim this module
    # exists to refuse.
    for name, row in declared.items():
        if name in journalled:
            continue
        disposition = row.get("disposition", "present")
        if disposition not in ("present", "archived", "compacted"):
            raise JournalLayoutError(
                f"{name} has unknown disposition {disposition!r}")
        found.append(Segment(
            name=name,
            index=row.get("index"),
            bytes=int(row.get("bytes", 0)),
            lines=int(row.get("lines", 0)),
            disposition=disposition,  # type: ignore[arg-type]
            on_disk=False,
            recorded_bytes=int(row.get("bytes", 0)),
            recorded_lines=int(row.get("lines", 0)),
            recorded_at=row.get("recorded_at"),
        ))

    boundary = None
    if manifest is not None and manifest.get("boundary"):
        raw = manifest["boundary"]
        boundary = Boundary(
            recorded_at=str(raw.get("recorded_at", "")),
            through_index=int(raw.get("through_index", -1)),
            first_missing=raw.get("first_missing"),
        )

    found.sort(key=lambda s: s.name)          # replay order, as rebuild_all reads
    return Layout(directory=directory, segments=tuple(found),
                  boundary=boundary, manifest_present=manifest is not None)


def verify(layout: Layout) -> None:
    """Raise unless the layout can be trusted to be a complete sequence.

    Each condition is a way a journal stops being replayable while still looking
    fine to a directory listing.
    """
    sealed = [s for s in layout.segments if s.sealed]
    indices = [s.index for s in sealed if s.index is not None]
    if len(set(indices)) != len(indices):
        raise SegmentSequenceError(
            f"duplicate sealed segment indices in {layout.directory}: "
            f"{sorted(indices)}")
    if indices:
        expected = list(range(min(indices), min(indices) + len(indices)))
        if sorted(indices) != expected:
            missing = sorted(set(expected) - set(indices))
            raise SegmentSequenceError(
                f"sealed segments in {layout.directory} are not contiguous: "
                f"have {sorted(indices)}, missing {missing} — a segment is gone "
                f"and no disposition records where it went")

    # ANCHOR 4: the ORDER the replay will use, not the width this module writes.
    order = [s.index for s in layout.segments if s.index is not None]
    if order != sorted(order):
        raise SegmentSequenceError(
            f"the replay order of {layout.directory} is not the numeric order: "
            f"sorted by name gives indices {order}. rebuild_all enumerates with "
            f"sorted(os.listdir(...)), so this journal would fold out of "
            f"sequence (a 5-digit index sorts before a 4-digit one).")

    for seg in layout.segments:
        if seg.disposition != "present" and seg.on_disk:
            raise SegmentSequenceError(
                f"{seg.name} is marked {seg.disposition!r} in the manifest but "
                f"is still on disk — the manifest no longer describes this "
                f"directory, so no claim about it can be trusted")
        if seg.disposition == "present" and not seg.on_disk:
            raise SegmentMissingError(
                f"the manifest records {seg.name} as present, but "
                f"{layout.directory} does not hold it — a segment left without "
                f"a recorded boundary")
        # ANCHOR 2: the size it was sealed at. A sealed segment is immutable, so
        # any difference is an in-place rewrite the manifest did not record.
        if seg.on_disk and seg.recorded_bytes is not None \
                and seg.bytes != seg.recorded_bytes:
            raise SegmentModifiedError(
                f"{seg.name} is {seg.bytes} B on disk but the manifest recorded "
                f"{seg.recorded_bytes} B at seal time — the segment was "
                f"rewritten in place. If that was a compaction, record it with "
                f"record_disposition(..., 'compacted') so the claim stops "
                f"saying 'full'.")

    if layout.manifest_present:
        rows = (read_manifest(layout.directory) or {}).get("segments", [])
        declared = {row["name"] for row in rows}
        unregistered = [s.name for s in sealed if s.name not in declared]
        if unregistered:
            raise SegmentSequenceError(
                f"sealed segment(s) {unregistered} are in {layout.directory} but "
                f"not in {MANIFEST_NAME} — the record of the sequence is "
                f"incomplete, so where they sit in it cannot be known")

        # ANCHOR 3: a manifested journal has a live tail. `seal_active` creates
        # the next empty `events.jsonl` as part of sealing, so its absence is a
        # deleted tail rather than the normal just-sealed state.
        if layout.active is None:
            raise SegmentSequenceError(
                f"{layout.directory} has a {MANIFEST_NAME} but no "
                f"{ACTIVE_NAME} — the live tail is missing. (A journal that has "
                f"only ever been sealed and never appended to still has an "
                f"empty active segment: sealing creates it.)")

        # ANCHOR 2 (boundary form): the recorded first_missing must agree with
        # the rows. A journal whose segments all came back cannot stay marked
        # incomplete forever, and one that lost a segment cannot stay marked
        # complete.
        first_gone = next((s.name for s in layout.segments
                           if s.disposition != "present" or not s.on_disk), None)
        recorded = layout.boundary.first_missing if layout.boundary else None
        if first_gone != recorded:
            raise SegmentSequenceError(
                f"{MANIFEST_NAME} records first_missing={recorded!r} but the "
                f"segments say {first_gone!r} — the boundary and the segment "
                f"rows disagree, so neither can be trusted")

    if layout.boundary is not None and indices \
            and max(indices) > layout.boundary.through_index:
        raise SegmentSequenceError(
            f"sealed segment {max(indices):04d} is ahead of the recorded "
            f"boundary ({layout.boundary.through_index:04d})")


def reconstruction_scope(layout: Layout) -> ReconstructionScope:
    """The strongest claim this layout supports — full reconstruction iff nothing left.

    A layout with no manifest, or with every segment ``present`` and on disk,
    supports ``full=True``. As soon as one segment's bytes are no longer here —
    archived, compacted, or simply gone — the answer is ``full=False``, names the
    segment the replay stops at (``first_missing``) and the one it can pick up
    from (``complete_from``).
    """
    verify(layout)
    gone = [s for s in layout.segments
            if s.disposition != "present" or not s.on_disk]
    if not gone:
        return ReconstructionScope(
            full=True, first_missing=None, complete_from=None,
            reason="every segment the journal has is present")

    missing = sorted(gone, key=lambda s: (-1 if s.index is None else s.index))[0]
    # "complete from" is the first segment the replay can actually reproduce
    # AFTER the break — judged by REPLAY ORDER (the name), not by index, because
    # the active segment has no index and still sits after every sealed one.
    after = [s for s in layout.segments
             if s.on_disk and s.disposition == "present"
             and s.name > missing.name]
    complete_from = after[0].name if after else None
    dispositions = sorted({s.disposition if s.disposition != "present"
                           else "absent" for s in gone})
    return ReconstructionScope(
        full=False,
        first_missing=missing.name,
        complete_from=complete_from,
        reason=(f"{len(gone)} segment(s) are recorded as "
                f"{'/'.join(dispositions)}; the replay cannot reproduce what "
                f"they held"))


def assert_full_reconstruction(layout: Layout) -> None:
    """Raise :class:`ReconstructionClaimError` unless a full claim is honest.

    ⛔ THIS IS THE FALSIFIER'S TARGET. A function (not a property, not a bool)
    so a caller cannot "read a false value and carry on": the claim either
    stands or the call raises. Off-by-default does not reach this — see the
    module docstring — because a guard the switch can silence is a bypass.
    """
    scope = reconstruction_scope(layout)
    if not scope.full:
        honest = (f"'complete from {scope.complete_from} forward'"
                  if scope.complete_from
                  else "'nothing after the boundary is reproducible'")
        raise ReconstructionClaimError(
            f"refusing a full-reconstruction claim for {layout.directory}: "
            f"{scope.reason} (reproduction stops at {scope.first_missing}). "
            f"The honest claim is {honest}.")


def read_all_segments(graph_dir: str | Path, *, salvage: bool = False) -> list[dict]:
    """Every record of every PRESENT segment, in replay order.

    Verifies the layout first: reading the survivors of a sequence with a gap
    would produce a plausible and wrong graph.

    ⛔ **STRICT BY DEFAULT.** ``read_for_replay`` salvages — a mid-file damaged
    line is skipped and counted — and a reader that used it silently would
    certify "full reconstruction" over a journal that lost records to damage.
    So a skipped line is a REFUSAL here unless the caller passes
    ``salvage=True``, which is the deliberate "I know this journal is damaged,
    give me what parses" choice and explicitly not a completeness claim.
    """
    layout = discover(graph_dir)
    verify(layout)
    from .log import EventLog, read_for_replay

    records: list[dict] = []
    damaged: list[str] = []
    for seg in layout.segments:
        if seg.disposition != "present" or not seg.on_disk:
            continue
        log = EventLog(layout.directory / seg.name)
        records.extend(read_for_replay(log))
        skipped = log.skipped_line_count
        if skipped:
            damaged.append(f"{seg.name}: {skipped} line(s) skipped")
    if damaged and not salvage:
        raise JournalLayoutError(
            f"{layout.directory} has damaged records ({'; '.join(damaged)}) — "
            f"refusing to hand back a record stream that is silently short of "
            f"what the journal holds. Pass salvage=True to read what parses.")
    return records


# --------------------------------------------------------------------------
# the writers (gated; rename and record, never delete)
# --------------------------------------------------------------------------


@contextlib.contextmanager
def _manifest_lock(directory: Path) -> Iterator[None]:
    """Serialise the writers' read-modify-write of the manifest.

    ``_write_manifest`` is atomic per FILE, which is not the same as atomic per
    TRANSACTION: two writers that both read, then both write, lose one of the two
    updates — measured as a sealed segment's row vanishing while the segment
    stayed on disk, which leaves the journal in a state no API could repair.
    """
    lock_path = directory / LOCK_NAME
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_manifest(graph_dir: Path, payload: dict) -> Path:
    """Write the manifest atomically — a half-written one must never be readable."""
    path = manifest_path(graph_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def _next_index(layout: Layout) -> int:
    return max([s.index for s in layout.segments
                if s.index is not None], default=-1) + 1


def _rows(layout: Layout) -> list[dict]:
    """The manifest rows for the sealed segments, in sequence order."""
    rows = []
    for seg in sorted((s for s in layout.segments if s.sealed),
                      key=lambda s: s.index if s.index is not None else -1):
        rows.append({"name": seg.name, "index": seg.index,
                     "bytes": seg.bytes, "lines": seg.lines,
                     "disposition": seg.disposition,
                     "recorded_at": seg.recorded_at or _utcnow()})
    return rows


def _first_missing(rows: list[dict]) -> str | None:
    for row in sorted(rows, key=lambda r: r.get("index", -1)):
        if row.get("disposition", "present") != "present":
            return row["name"]
    return None


def plan_rotation(graph_dir: str | Path, max_bytes: int) -> RotationPlan | None:
    """What sealing would do, computed without writing — the pricing instrument.

    Returns ``None`` when the active segment is under *max_bytes* (nothing to
    do), which is the normal answer today because nothing calls
    :func:`seal_active` unless a deployment switches it on.
    """
    layout = discover(graph_dir)
    verify(layout)
    active = layout.active
    if active is None:
        return None
    if active.bytes < max_bytes:
        return None
    next_index = _next_index(layout)
    return RotationPlan(
        active=active,
        seal_to=f"events.{next_index:04d}.jsonl",
        next_index=next_index,
        boundary=Boundary(
            recorded_at=_utcnow(),
            through_index=next_index,
            first_missing=layout.boundary.first_missing
            if layout.boundary else None,
        ),
    )


def seal_active(graph_dir: str | Path) -> Path:
    """Seal the active segment: rename it, record the boundary, open the next one.

    ⛔ Gated on :data:`ENV_FLAG`. With the flag off this raises
    :class:`SegmentationDisabled` and the directory is untouched, so the default
    deployment's layout is byte-for-byte what it is today.

    The rename (not copy, not truncate) is the point: the bytes are still there
    afterwards, under a name that sorts into replay order, and the manifest now
    states a boundary. The fresh empty ``events.jsonl`` is created as part of the
    seal so that "a manifested journal has a live tail" is an invariant a reader
    can check (anchor 3) — otherwise a deleted tail is indistinguishable from the
    just-sealed state.

    Moving the sealed bytes off the box is a separate, policy-bearing step this
    module deliberately does not take: it only records the disposition a caller
    may then set.
    """
    if not enabled():
        raise SegmentationDisabled(
            f"{ENV_FLAG} is not '1' — refusing to seal {graph_dir}. Sealing is "
            f"the boundary step of a retention policy that has not been chosen; "
            f"the read-only helpers on this module need no switch.")

    directory = Path(graph_dir)
    with _manifest_lock(directory):
        layout = discover(directory)
        verify(layout)
        active = layout.active
        if active is None:                       # pragma: no cover - verify covers it
            raise JournalLayoutError(
                f"no active {ACTIVE_NAME} in {directory} — nothing to seal")

        next_index = _next_index(layout)
        sealed_name = f"events.{next_index:04d}.jsonl"
        source = directory / active.name
        target = directory / sealed_name
        if target.exists():
            raise SegmentSequenceError(
                f"{sealed_name} already exists in {directory} — refusing to "
                f"overwrite a sealed segment")

        source.rename(target)
        # ANCHOR 3: the live tail exists from the moment the journal is sealed.
        (directory / ACTIVE_NAME).touch()

        rows = _rows(layout)
        rows.append({"name": sealed_name, "index": next_index,
                     "bytes": target.stat().st_size,
                     "lines": _count_lines(target),
                     "disposition": "present", "recorded_at": _utcnow()})
        payload = {
            "version": MANIFEST_VERSION,
            "graph_dir": str(directory),
            "segments": rows,
            "boundary": {
                "recorded_at": _utcnow(),
                "through_index": next_index,
                "first_missing": _first_missing(rows),
            },
        }
        return _write_manifest(directory, payload)


def record_disposition(graph_dir: str | Path, name: str,
                       disposition: Disposition) -> Path:
    """Record where a segment's bytes went. **Changes no bytes, anywhere.**

    This is the call that turns "a segment is missing" into "a segment is
    archived", and it is what makes the difference between a bound and a silent
    truncation: the manifest keeps admitting that the bytes are not here, so
    :func:`reconstruction_scope` reports ``full=False`` for as long as that is
    true — and the boundary is recomputed from the rows on every call, so putting
    the bytes back restores the claim instead of pinning the journal incomplete
    forever.

    ⚠️ **DECLARED RESIDUAL:** this is also how an accidentally deleted segment
    becomes an "archived" one. The manifest records *when* the disposition was
    set (``recorded_at``) so the two are distinguishable in an audit, but nothing
    here can prove the bytes left deliberately — only that someone said so.

    Gated on the same flag as :func:`seal_active`: deciding that bytes have left
    is a policy act, not housekeeping.
    """
    if not enabled():
        raise SegmentationDisabled(
            f"{ENV_FLAG} is not '1' — refusing to record a disposition for "
            f"{name} in {graph_dir}")
    if disposition not in ("present", "archived", "compacted"):
        raise ValueError(f"unknown disposition {disposition!r}")

    directory = Path(graph_dir)
    with _manifest_lock(directory):
        # Refuse a layout that cannot be described BEFORE writing to its manifest:
        # `discover` is the check that catches an unplaceable `*.jsonl` or a
        # sealed sequence whose manifest has gone. Its value is the raise.
        discover(directory)
        payload = read_manifest(directory)
        if payload is None:
            raise JournalLayoutError(
                f"{directory} has no {MANIFEST_NAME} — seal a segment before "
                f"recording where one went")
        hit = False
        for row in payload["segments"]:
            if row.get("name") == name:
                row["disposition"] = disposition
                row["recorded_at"] = _utcnow()
                hit = True
        if not hit:
            raise JournalLayoutError(
                f"{name} is not in {MANIFEST_NAME} — refusing to record a "
                f"disposition for a segment the manifest has never seen")
        payload["boundary"]["first_missing"] = _first_missing(payload["segments"])
        return _write_manifest(directory, payload)


def summarise(graph_dir: str | Path) -> dict:
    """A read-only report: segments, their bytes, and the honest claim.

    Never raises on a layout it cannot verify — it REPORTS the refusal, because a
    summariser that raises cannot tell an operator why. That includes a manifest
    it cannot read: :func:`discover` is inside the guard, not above it.
    """
    try:
        layout = discover(graph_dir)
    except JournalLayoutError as exc:
        return {
            "directory": str(graph_dir),
            "manifest_present": manifest_path(graph_dir).exists(),
            "total_bytes": None,
            "segments": [],
            "verified": False,
            "full_reconstruction": False,
            "from_segment": None,
            "reason": str(exc),
        }

    report: dict = {
        "directory": str(layout.directory),
        "manifest_present": layout.manifest_present,
        "total_bytes": layout.total_bytes,
        "segments": [
            {"name": s.name, "index": s.index, "bytes": s.bytes,
             "lines": s.lines, "disposition": s.disposition,
             "on_disk": s.on_disk}
            for s in layout.segments
        ],
    }
    try:
        verify(layout)
        scope = reconstruction_scope(layout)
        report["verified"] = True
        report["full_reconstruction"] = scope.full
        report["first_missing"] = scope.first_missing
        report["from_segment"] = scope.complete_from
        report["reason"] = scope.reason
    except JournalLayoutError as exc:
        report["verified"] = False
        report["full_reconstruction"] = False
        report["first_missing"] = None
        report["from_segment"] = None
        report["reason"] = str(exc)
    return report


def segment_names(graph_dir: str | Path) -> Iterable[str]:
    """The replay-ordered names the journal consists of (read-only)."""
    return (s.name for s in discover(graph_dir).segments)
