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
reconstruction**. This module is that, and it is built so that everything it
does today is reversible and nothing it does today removes a byte:

* :func:`discover`, :func:`verify`, :func:`reconstruction_scope`,
  :func:`read_all_segments` and :func:`plan_rotation` are **READ-ONLY**. They
  never write, rename or delete.
* :func:`seal_active` is the ONE writer. It is gated behind
  :data:`ENV_FLAG` (default OFF), it **renames** the active segment and writes a
  manifest — it deletes nothing, and with the flag off the deployment's on-disk
  layout and the default write path are byte-for-byte what ships today.

⛔ THE FLAG GATES WRITING, NEVER REFUSING. If ``TORTOISE_JOURNAL_SEGMENTS`` also
disarmed :func:`verify` and :func:`assert_full_reconstruction`, then switching
the flag off would disable the honesty guarantee on a journal that already
crossed a boundary — the exact fail-open shape AGENTS.md classifies as category
A. So a manifest, once present, is verified on **every** read whatever the flag
says; the flag only decides whether a new boundary may be recorded.

THE DANGEROUS PROPERTY, and the falsifier that guards it. Once a segment's bytes
are not all present, a restore can no longer claim full reconstruction — and the
failure that matters is not "the restore was incomplete", it is **"the restore
said it was complete"**. So the claim is a separate, checkable object
(:class:`ReconstructionScope`) and there is one function that asserts it
(:func:`assert_full_reconstruction`), which raises
:class:`ReconstructionClaimError` rather than returning a falsy value a caller
might ignore. :class:`SegmentMissingError` covers the other half: a manifest
that says a segment is present while the directory does not have it (a segment
dropped WITHOUT a recorded disposition) refuses instead of replaying the
survivors and calling it done.

SEGMENT NAMING, and why it is zero-padded. Sealed segments are
``events.<NNNN>.jsonl`` with at least four digits, and the active one stays
``events.jsonl``. This matters because ``rebuild_all`` enumerates a per-graph
directory with ``sorted(os.listdir(...))`` and ``endswith('.jsonl')``
(``tortoise/projection/__init__.py``) and ``consistency.py`` does the same — so
under a fixed width, lexicographic order IS numeric order, and
``events.0000.jsonl`` sorts before ``events.jsonl`` (``'0'`` < ``'j'``). A
variable-width or fully-lexical name would replay out of order and the replay
order is the whole reason the journal is a log.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
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

#: ``events.0007.jsonl`` — at least four digits, so `sorted()` is numeric.
SEALED_RE = re.compile(r"^events\.(\d{4,})\.jsonl$")

#: Where a segment's bytes are. ``present`` is the only disposition that keeps a
#: full-reconstruction claim available; the other two are recorded *decisions*,
#: which is the difference between a bound and silent truncation.
Disposition = Literal["present", "archived", "compacted"]

MANIFEST_VERSION = 1


class JournalLayoutError(RuntimeError):
    """The journal's segment layout cannot be trusted."""


class SegmentSequenceError(JournalLayoutError):
    """The sealed indices have a gap, a duplicate, or an unparseable name.

    A gap means a segment is gone and nothing recorded that it went. Replaying
    the survivors and reporting success is the failure this class exists for.
    """


class SegmentMissingError(JournalLayoutError):
    """A manifest says a segment is ``present`` and the directory does not hold it."""


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

    @property
    def sealed(self) -> bool:
        return self.index is not None


@dataclass(frozen=True)
class Boundary:
    """The recorded edge of what the journal still holds in full.

    ``through_index`` is the highest segment index the journal HAD when the
    boundary was recorded; ``first_incomplete`` names the first segment whose
    bytes this deployment no longer keeps, and is ``None`` while nothing has
    left. This is a claim about *reconstruction*, never about durability —
    ``docs/durability-posture.md`` keeps authority over the latter.
    """

    recorded_at: str
    through_index: int
    first_incomplete: str | None = None


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


@dataclass(frozen=True)
class ReconstructionScope:
    """The strongest honest claim a restore from this layout may make."""

    full: bool
    from_segment: str | None
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


def _segment_from_path(path: Path, disposition: Disposition = "present") -> Segment:
    match = SEALED_RE.match(path.name)
    return Segment(
        name=path.name,
        index=int(match.group(1)) if match else None,
        bytes=path.stat().st_size,
        lines=_count_lines(path),
        disposition=disposition,
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
    return data


def discover(graph_dir: str | Path) -> Layout:
    """Enumerate a graph directory's journal segments, in REPLAY ORDER.

    Read-only. Raises only on a layout it cannot describe honestly; the caller
    decides what to do with the answer (:func:`verify` is the strict pass).
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

    on_disk: dict[str, Path] = {}
    for path in sorted(directory.iterdir()):
        if path.name == MANIFEST_NAME or not path.is_file():
            continue
        if path.name != ACTIVE_NAME and not SEALED_RE.match(path.name):
            continue
        on_disk[path.name] = path

    found: list[Segment] = []
    for name, path in on_disk.items():
        disposition = (declared.get(name) or {}).get("disposition", "present")
        if disposition not in ("present", "archived", "compacted"):
            raise JournalLayoutError(
                f"{name} has unknown disposition {disposition!r}")
        found.append(_segment_from_path(path, disposition))  # type: ignore[arg-type]

    # ⛔ A segment the manifest RECORDS while the directory does not hold it
    # stays in the layout with `on_disk=False`. Dropping it from the layout
    # would make the boundary invisible to `reconstruction_scope` and turn
    # "archived, so no full claim" into "nothing to see" — the laundered claim
    # this module exists to refuse.
    for name, row in declared.items():
        if name in on_disk:
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
        ))

    boundary = None
    if manifest is not None and manifest.get("boundary"):
        raw = manifest["boundary"]
        boundary = Boundary(
            recorded_at=str(raw.get("recorded_at", "")),
            through_index=int(raw.get("through_index", -1)),
            first_incomplete=raw.get("first_incomplete"),
        )

    return Layout(directory=directory, segments=tuple(found),
                  boundary=boundary, manifest_present=manifest is not None)


def verify(layout: Layout) -> None:
    """Raise unless the layout can be trusted to be a complete sequence.

    Four conditions, each a way a journal stops being replayable while still
    looking fine to a directory listing:

    1. **No gap and no duplicate** among sealed indices — a gap is a segment
       that vanished without a recorded disposition.
    2. **Every ``present`` manifest segment exists on disk** — otherwise the
       manifest's own record of what is here is false.
    3. **No segment is marked ``archived``/``compacted`` while still on disk** —
       a stale disposition means the boundary no longer describes reality.
    4. **The highest sealed index is not ahead of the boundary** — you cannot
       have sealed past what the boundary admits to.
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

    if layout.manifest_present:
        rows = (read_manifest(layout.directory) or {}).get("segments", [])
        declared = {row["name"] for row in rows}
        unregistered = [s.name for s in sealed if s.name not in declared]
        if unregistered:
            raise SegmentSequenceError(
                f"sealed segment(s) {unregistered} are in {layout.directory} but "
                f"not in {MANIFEST_NAME} — the record of the sequence is "
                f"incomplete, so where they sit in it cannot be known")

    if layout.boundary is not None and indices \
            and max(indices) > layout.boundary.through_index:
        raise SegmentSequenceError(
            f"sealed segment {max(indices):04d} is ahead of the recorded "
            f"boundary ({layout.boundary.through_index:04d})")


def reconstruction_scope(layout: Layout) -> ReconstructionScope:
    """The strongest claim this layout supports — full reconstruction iff nothing left.

    A layout with no manifest, or with every segment ``present``, supports
    ``full=True``. As soon as one segment's bytes are no longer here — archived
    or compacted, recorded or not — the answer is ``full=False`` and names the
    first segment the replay cannot cross.
    """
    verify(layout)
    gone = [s for s in layout.segments
            if s.disposition != "present" or not s.on_disk]
    if not gone:
        if layout.boundary and layout.boundary.first_incomplete:
            return ReconstructionScope(
                full=False,
                from_segment=layout.boundary.first_incomplete,
                reason=(f"the recorded boundary names "
                        f"{layout.boundary.first_incomplete} as no longer kept"))
        return ReconstructionScope(
            full=True, from_segment=None,
            reason="every segment the journal has is present")

    first = sorted(gone, key=lambda s: (-1 if s.index is None else s.index))[0]
    dispositions = sorted({s.disposition if s.disposition != "present"
                           else "absent" for s in gone})
    return ReconstructionScope(
        full=False,
        from_segment=first.name,
        reason=(f"{len(gone)} segment(s) are recorded as "
                f"{'/'.join(dispositions)}; the replay cannot reproduce what "
                f"they held"))


def assert_full_reconstruction(layout: Layout) -> None:
    """Raise :class:`ReconstructionClaimError` unless a full claim is honest.

    ⛔ THIS IS THE FALSIFIER'S TARGET. A function (not a property, not a bool)
    so a caller cannot "read a false value and carry on": the claim either
    stands or the call raises. Off-by-default does not reach this — see the
    module docstring — because a guard that the switch can silence is a bypass.
    """
    scope = reconstruction_scope(layout)
    if not scope.full:
        raise ReconstructionClaimError(
            f"refusing a full-reconstruction claim for {layout.directory}: "
            f"{scope.reason} (from {scope.from_segment}). The honest claim is "
            f"'complete from {scope.from_segment} forward'.")


def read_all_segments(graph_dir: str | Path) -> list[dict]:
    """Every record of every PRESENT segment, in replay order.

    Verifies the layout first: reading the survivors of a sequence with a gap
    would produce a plausible and wrong graph. A segment whose disposition says
    its bytes are elsewhere is skipped — and that skip is only safe because
    :func:`verify` already refused any layout that is missing a ``present`` one.
    """
    layout = discover(graph_dir)
    verify(layout)
    from .log import EventLog, read_for_replay

    records: list[dict] = []
    for seg in layout.segments:
        if seg.disposition != "present" or not seg.on_disk:
            continue
        records.extend(read_for_replay(EventLog(layout.directory / seg.name)))
    return records


# --------------------------------------------------------------------------
# the one writer (gated; renames and records, never deletes)
# --------------------------------------------------------------------------


def plan_rotation(graph_dir: str | Path, max_bytes: int) -> RotationPlan | None:
    """What sealing would do, computed without writing — the pricing instrument.

    Returns ``None`` when the active segment is under *max_bytes* (nothing to
    do), which is the normal answer today because nothing calls
    :func:`seal_active` unless a deployment switches it on.
    """
    layout = discover(graph_dir)
    verify(layout)
    active = [s for s in layout.segments if not s.sealed]
    if not active:
        return None
    active_seg = active[0]
    if active_seg.bytes < max_bytes:
        return None
    next_index = max([s.index for s in layout.segments
                      if s.index is not None], default=-1) + 1
    return RotationPlan(
        active=active_seg,
        seal_to=f"events.{next_index:04d}.jsonl",
        next_index=next_index,
        boundary=Boundary(
            recorded_at=_utcnow(),
            through_index=next_index,
            first_incomplete=layout.boundary.first_incomplete
            if layout.boundary else None,
        ),
    )


def _utcnow() -> str:
    from datetime import datetime
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_manifest(graph_dir: Path, payload: dict) -> Path:
    """Write the manifest atomically — a half-written one must never be readable."""
    path = manifest_path(graph_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def seal_active(graph_dir: str | Path) -> Path:
    """Seal the active segment: rename it, record the boundary. DELETES NOTHING.

    ⛔ Gated on :data:`ENV_FLAG`. With the flag off this raises
    :class:`SegmentationDisabled` and the directory is untouched, so the default
    deployment's layout is byte-for-byte what it is today.

    The rename (not copy, not truncate) is the whole point: the bytes are still
    there afterwards, under a name that sorts into replay order, and the
    manifest now states a boundary. Moving those bytes off the box is a
    separate, policy-bearing step that this module deliberately does not take —
    it only records the disposition a caller may then set.
    """
    if not enabled():
        raise SegmentationDisabled(
            f"{ENV_FLAG} is not '1' — refusing to seal {graph_dir}. Sealing is "
            f"the boundary step of a retention policy that has not been chosen; "
            f"the read-only helpers on this module need no switch.")

    layout = discover(graph_dir)
    verify(layout)
    active = [s for s in layout.segments if not s.sealed]
    if not active:
        raise JournalLayoutError(
            f"no active {ACTIVE_NAME} in {graph_dir} — nothing to seal "
            f"(an empty directory has no journal yet)")

    next_index = max([s.index for s in layout.segments
                      if s.index is not None], default=-1) + 1
    sealed_name = f"events.{next_index:04d}.jsonl"
    source = layout.directory / active[0].name
    target = layout.directory / sealed_name
    if target.exists():
        raise SegmentSequenceError(
            f"{sealed_name} already exists in {layout.directory} — refusing to "
            f"overwrite a sealed segment")

    source.rename(target)

    segments = []
    for seg in layout.segments:
        if not seg.sealed:
            continue
        segments.append({"name": seg.name, "index": seg.index,
                         "bytes": seg.bytes, "lines": seg.lines,
                         "disposition": seg.disposition})
    segments.append({"name": sealed_name, "index": next_index,
                     "bytes": target.stat().st_size,
                     "lines": _count_lines(target),
                     "disposition": "present"})
    payload = {
        "version": MANIFEST_VERSION,
        "graph_dir": str(layout.directory),
        "segments": segments,
        "boundary": {
            "recorded_at": _utcnow(),
            "through_index": next_index,
            "first_incomplete": (layout.boundary.first_incomplete
                                 if layout.boundary else None),
        },
    }
    return _write_manifest(layout.directory, payload)


def record_disposition(graph_dir: str | Path, name: str,
                       disposition: Disposition) -> Path:
    """Record where a segment's bytes went. **Changes no bytes, anywhere.**

    This is the call that turns "a segment is missing" into "a segment is
    archived", and it is what makes the difference between a bound and a silent
    truncation: the manifest keeps admitting that the bytes are not here, so
    :func:`reconstruction_scope` reports ``full=False`` for as long as that is
    true. Gated on the same flag as :func:`seal_active` — deciding that bytes
    have left is a policy act, not housekeeping.
    """
    if not enabled():
        raise SegmentationDisabled(
            f"{ENV_FLAG} is not '1' — refusing to record a disposition for "
            f"{name} in {graph_dir}")
    if disposition not in ("present", "archived", "compacted"):
        raise ValueError(f"unknown disposition {disposition!r}")

    layout = discover(graph_dir)
    payload = read_manifest(layout.directory)
    if payload is None:
        raise JournalLayoutError(
            f"{layout.directory} has no {MANIFEST_NAME} — seal a segment before "
            f"recording where one went")
    hit = False
    for row in payload["segments"]:
        if row.get("name") == name:
            row["disposition"] = disposition
            hit = True
    if not hit:
        raise JournalLayoutError(
            f"{name} is not in {MANIFEST_NAME} — refusing to record a "
            f"disposition for a segment the manifest has never seen")
    if any(row.get("disposition") != "present"
           for row in payload["segments"]):
        first = next(row["name"] for row in payload["segments"]
                     if row.get("disposition") != "present")
        payload["boundary"]["first_incomplete"] = first
    return _write_manifest(layout.directory, payload)


def summarise(graph_dir: str | Path) -> dict:
    """A read-only report: segments, their bytes, and the honest claim.

    Used by the measurement and by an operator asking "what does this journal
    actually hold". Never raises on a layout it cannot verify — it REPORTS the
    refusal, because a summariser that raises cannot tell an operator why.
    """
    layout = discover(graph_dir)
    report: dict = {
        "directory": str(layout.directory),
        "manifest_present": layout.manifest_present,
        "total_bytes": layout.total_bytes,
        "segments": [
            {"name": s.name, "index": s.index, "bytes": s.bytes,
             "lines": s.lines, "disposition": s.disposition}
            for s in layout.segments
        ],
    }
    try:
        verify(layout)
        scope = reconstruction_scope(layout)
        report["verified"] = True
        report["full_reconstruction"] = scope.full
        report["from_segment"] = scope.from_segment
        report["reason"] = scope.reason
    except JournalLayoutError as exc:
        report["verified"] = False
        report["full_reconstruction"] = False
        report["reason"] = str(exc)
    return report


def segment_names(graph_dir: str | Path) -> Iterable[str]:
    """The replay-ordered names the journal consists of (read-only)."""
    return (s.name for s in discover(graph_dir).segments)
