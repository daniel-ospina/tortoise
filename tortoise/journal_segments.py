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
* :func:`seal_active`, :func:`record_disposition` and :func:`adopt_crashed_seal`
  are the only writers. They are gated behind :data:`ENV_FLAG` (default OFF),
  they **rename**, **record** and (the repair verb) **append one row** — they
  delete nothing — and with the flag off the deployment's on-disk layout and the
  default write path are byte-for-byte what ships today.

⛔ THE FLAG GATES WRITING, NEVER REFUSING. If ``TORTOISE_JOURNAL_SEGMENTS`` also
disarmed :func:`verify` and :func:`assert_full_reconstruction`, then switching
the flag off would disable the honesty guarantee on a journal that had already
crossed a boundary — the fail-open shape AGENTS.md classifies as category A. A
manifest, once present, is verified on **every** read whatever the flag says.

THE SIX ANCHORS, and why a manifest alone is not enough. "What is on disk
matches the manifest's rows" is a weaker question than "what is on disk is what
the journal had", and the difference is where every false completeness claim
lives. So the module records and checks six things, not one — and each of the
last three exists because a review cycle MEASURED a way past the ones before it:

1. **The segment set is the REPLAY's set.** ``rebuild_all`` replays *any*
   ``*.jsonl`` in the directory (``projection/__init__.py``), so a ``*.jsonl``
   this module cannot place in the sequence is REFUSED rather than ignored — an
   ignored file is one the replay folds and the claim does not know about.
2. **A present segment is the size AND line count it was sealed at.** Each row
   records ``bytes``/``lines``; a segment that no longer matches is REFUSED
   (:class:`SegmentModifiedError`). Without this, truncating a sealed segment to
   zero bytes certifies "full reconstruction" over an empty journal — and a
   dropped record whose bytes were padded back is invisible to ``bytes`` alone.
3. **A manifested journal has an active segment.** :func:`seal_active` creates
   the next ``events.jsonl`` (empty) as part of sealing, which turns "somebody
   deleted the live tail" into a detectable state rather than an ambiguous one.
4. **A present segment has the IDENTITY it was sealed at.** Each row also records
   a ``sha256``. Size and line count are BOTH preserved by a same-length in-place
   rewrite, and by two equal-length segments SWAPPED during a migration — which
   was measured certifying ``full=True`` with the replay order inverted.
5. **The sequence's ORIGIN is accounted for.** Contiguity is checked from the
   lowest index present, so deleting ``events.0000.jsonl`` *and* its row left a
   contiguous sequence certifying "full"; the lowest sealed index must be 0
   unless the boundary records that 0000 left.
6. **The lexicographic order IS the replay order.** ``rebuild_all`` uses
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

⛔ **DECLARED RESIDUAL 2 — THE LIVE TAIL IS UNANCHORED.** No manifest can record
the size of a file that is still being appended to, so truncating or blanking
``events.jsonl`` — the ACTIVE segment — is detected by nothing here: the sealed
prefix still matches its recorded sizes and :func:`reconstruction_scope` reports
``full=True``. This is the shape **every deployment is in today** (the flag is
off, nothing seals, so there is no sealed prefix at all), which is why
:attr:`ReconstructionScope.anchored` exists: it is ``False`` until a manifest
exists, and a caller that needs *evidence* rather than an *assumption* must check
it. Closing this needs a per-append anchor (a length trail), which is a write-path
change and therefore part of the policy decision, not of this plumbing.

⚠️ **THE ONE RECOVERABLE-BUT-UNREADABLE STATE, AND ITS OWN VERB.** A crash
between `seal_active`'s rename and its manifest write leaves a sealed segment
that the manifest has never seen: the bytes are intact and the state is fully
described by the directory, but EVERY read and BOTH writers refuse (anchor 1) —
by design, because a sealed segment nobody recorded could otherwise be adopted
silently. `adopt_crashed_seal` is the narrow verb for exactly that state: it
REFUSES unless the directory matches the crash signature and nothing else
(release the check and it becomes a way to launder a dropped segment into
"present"), and it appends a row — it never renames, deletes or rewrites bytes.
Hand-editing the manifest is the state this module exists to prevent, so the
repair is a tested verb rather than a recipe in a docstring.

⛔ **DECLARED RESIDUAL 1 — a manifest is evidence, not proof.** Everything here is
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
import hashlib
import json
import logging
import os
import re
import stat
import threading
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

MANIFEST_VERSION = 2       # v2 added the per-segment sha256 (cycle 3)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
#: ⛔ A v1 manifest is REFUSED, not read with the digest check skipped: a missing
#: digest must not mean "no identity anchor", because that is the hole v2 closes.
OLD_MANIFEST_VERSIONS: frozenset[int] = frozenset({1})


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
    """A segment is not what the manifest recorded for it (size, lines, digest).

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
    #: The sha256 the manifest recorded at seal time. Size and line count both
    #: survive a rewrite that preserves them (two equal-length segments SWAPPED,
    #: a same-length substitution); this does not.
    recorded_sha256: str | None = None
    #: The digest on disk (``None`` for a segment the directory does not hold).
    sha256: str | None = None
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
        """Bytes ACTUALLY ON DISK. A departed segment's recorded size is not disk.

        Cycle 4: summing every segment made a graph whose only segment was
        archived report 37 bytes while `du` said 0 — and a rotation threshold
        coded against the total would never fire once segments started leaving.
        """
        return sum(s.bytes for s in self.segments if s.on_disk)

    @property
    def departed_bytes(self) -> int:
        """Recorded size of segments whose bytes this deployment no longer holds."""
        return sum(s.bytes for s in self.segments if not s.on_disk)

    @property
    def active(self) -> Segment | None:
        """The live tail — only if the directory actually still holds it.

        ⛔ ``on_disk`` matters: anchor 3 asks "is the live tail present?", and a
        segment object for a file that is GONE answering that question is how a
        deleted tail read as present.
        """
        for seg in self.segments:
            if not seg.sealed and seg.on_disk:
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
    #: ⛔ What HAS recorded evidence: the sealed prefix, and only it. ``False``
    #: means no manifest exists — nothing has ever been sealed — so "every
    #: segment the journal has is present" describes the files that are here, not
    #: proof that nothing was lost. **That is the shape every deployment is in
    #: today** (the flag is off and nothing seals).
    anchored: bool = False
    #: ⛔ THE LIVE TAIL IS **NEVER** ANCHORED, and this field says so rather than
    #: leaving a caller to infer it from `anchored`. No manifest can record the
    #: size of a file that is still being appended to, so truncating or blanking
    #: ``events.jsonl`` is invisible here — including blanking it to ZERO bytes,
    #: which makes the tail *look* like a freshly sealed, empty one. Measured: five
    #: records removed this way left `full=True, anchored=True`. Closing this
    #: needs a per-append length trail (a write-path change, i.e. the policy
    #: decision). **A caller that needs a claim over tail bytes therefore cannot
    #: have one from this module**, and that is the honest answer, not `False` in
    #: a comment.
    tail_anchored: bool = False


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


def _measure(path: Path, *, lock: bool = False,
             strict: bool = False) -> tuple[int, int, str]:
    """``(bytes, non-empty lines, sha256)`` from ONE read of ONE descriptor.

    ⛔ ALL THREE COME FROM THE SAME READ. Cycle 4 measured the alternative:
    `stat()` for the size and a SEPARATE open for the digest let an appender land
    in between, producing a row that no byte string can satisfy — recorded 74 B
    with the digest of 113 B's content — after which EVERY read and EVERY writer
    refused forever, and the module's own repair advice ("restore the segment's
    bytes") was impossible.

    ⛔ AND ``strict`` IS FOR WRITERS ONLY. Cycle 5 measured the first version,
    which applied the length re-check to EVERY file — including the live tail,
    which is BY DEFINITION being appended to. A 200-call read loop against a
    journal under a live appender refused **142 times**, reporting a routine
    append as "the segment was rewritten in place". On the read path the triple
    describes the bytes actually read, which is the strongest true statement
    available; only a WRITER needs the refusal, because only a writer mints a row
    that could become unsatisfiable.

    ``lock=True`` (writers only) takes the journal's own append lock
    (``log._lock_exclusive``, the same key `EventLog.append` uses) so a compliant
    appender is serialised out of the measurement window. ⚠️ That lock is
    BEST-EFFORT and its failure is not local: on its 5 s timeout the appender
    proceeds unlocked and the journal path is marked unhealthy for 30 s
    (``log._FLOCK_UNHEALTHY_FOR_S``), so a measurement that outlasts the deadline
    degrades the WRITE path. Local throughput is ~484 MB/s in this read loop
    (measured), i.e. the deadline is ~2.4 GB on a local disk and a few hundred MB
    on a networked volume — inside this module's working range. That window is
    the price of never minting an unsatisfiable row, and it is declared rather
    than hidden.
    """
    digest = hashlib.sha256()
    n = 0
    total = 0
    with path.open("rb") as fh:
        key = os.path.realpath(path)
        if lock:
            from .log import _lock_exclusive
            _lock_exclusive(fh.fileno(), key)      # best effort; see above
        before = os.fstat(fh.fileno()).st_size
        for chunk in fh:
            if chunk.strip():
                n += 1
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(fh.fileno()).st_size
    if strict and not (before == after == total):
        # The file changed under us. Refusing is the point ON THE WRITE PATH: a
        # row built from mixed reads is unsatisfiable, and an unsatisfiable row
        # is unrepairable. A READER must not refuse — see the docstring.
        raise SegmentModifiedError(
            f"{path.name} changed while it was being measured ({before} B at "
            f"open, {total} B read, {after} B at close) — another writer holds "
            f"it open. Re-run the seal once the journal is quiet; a row built "
            f"from two different reads can never be satisfied.")
    return total, n, digest.hexdigest()


def _digest(path: Path) -> tuple[int, str]:
    """``(non-empty line count, sha256)`` — see :func:`_measure`."""
    _, lines, sha = _measure(path)
    return lines, sha


def _count_lines(path: Path) -> int:
    """Non-empty lines in *path*. The journal's unit is the line."""
    return _measure(path)[1]


def _segment_from_path(path: Path, disposition: Disposition = "present",
                       recorded_bytes: int | None = None,
                       recorded_lines: int | None = None) -> Segment:
    match = SEALED_RE.match(path.name)
    # ⛔ all three from ONE read — `stat()` afterwards could describe a different
    # read than the digest beside it (cycle 5).
    size, lines, sha = _measure(path)
    return Segment(
        name=path.name,
        index=int(match.group(1)) if match else None,
        bytes=size,
        lines=lines,
        sha256=sha,
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
    except (OSError, UnicodeDecodeError) as exc:
        # ⛔ `UnicodeDecodeError` is a ValueError, NOT an OSError — so it escaped
        # every `except JournalLayoutError` in this module and made `summarise`
        # raise, which is the one thing that verb promises not to do (cycle 5,
        # measured with a single `\xff\xfe` in the file). Same class as the
        # recorded `[unicode-decode-is-not-oserror]` gotcha.
        raise JournalLayoutError(
            f"cannot read {path} as UTF-8: {exc} — a manifest that cannot be "
            f"decoded is still a recorded boundary, so it is refused by name "
            f"rather than escaping as a decode error") from exc
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
        # ⛔ A v1 manifest is refused like any other unknown version. It has no
        # per-segment digest, and a reader that accepted it "with the identity
        # anchor skipped" would re-open exactly the hole v2 closes — a missing
        # field must never mean "no check". No v1 manifest exists in the wild:
        # this module ships in the same PR that wrote v1, and both writers are
        # behind a flag that defaults OFF.
        hint = (" (v1 has no per-segment sha256, so a same-size rewrite of a "
                "sealed segment would be undetectable — re-mint the manifest "
                "from the segments on disk)"
                if version in OLD_MANIFEST_VERSIONS else "")
        raise JournalLayoutError(
            f"{path} is manifest version {version!r}, this build writes "
            f"{MANIFEST_VERSION} — refusing rather than guessing{hint}")
    # ⛔ ROWS ARE VALIDATED HERE, ONCE. Three consumers walk them (`discover`,
    # `verify`, `record_disposition`); validating only in `discover` left the
    # writer raising a bare `AttributeError` on `"segments": ["name"]` — the same
    # wrong shape the boundary check above exists to prevent.
    #
    # AND: `bytes`/`lines`/`sha256` are REQUIRED on every row. They used to be
    # optional, and anchor 2 skipped whatever was absent — so a row that simply
    # OMITTED them turned the anchor off and certified a truncated segment as
    # full. A field whose absence disables a check is a fail-open.
    for row in data["segments"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise JournalLayoutError(
                f"{path} has a segment row that is not a named object: {row!r}")
        for field in ("bytes", "lines"):
            if not isinstance(row.get(field), int) or isinstance(row.get(field), bool):
                raise JournalLayoutError(
                    f"{path} row {row['name']!r} has no integer `{field}` — "
                    f"the size and line count are anchors, so a row cannot "
                    f"omit them (an absent field would disable the check)")
        # ⛔ not just "is a string": `""` is a string, passed this check, and then
        # made the anchor's falsy guard skip itself — so the ONE anchor R5b
        # exists to make un-skippable was skipped by an empty value, and the next
        # seal rewrote the row's digest FROM DISK, laundering a same-size
        # substitution permanently (cycle 4, measured). A digest is 64 hex chars
        # or it is not a digest.
        sha = row.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            raise JournalLayoutError(
                f"{path} row {row['name']!r} has no valid `sha256` "
                f"({sha!r}) — the identity anchor is what catches a same-size "
                f"rewrite, so a row cannot omit it or leave it blank")
        if row.get("disposition", "present") not in ("present", "archived",
                                                    "compacted"):
            raise JournalLayoutError(
                f"{row['name']!r} has unknown disposition "
                f"{row.get('disposition')!r}")

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
            if row["name"] in declared:
                # Last-wins is how a `[archived, present]` pair reproduces a
                # segment the manifest says left: the set collapses, `verify`
                # cross-checks the surviving row, and `full=True` comes back over
                # a laundered boundary (cycle 4, measured).
                raise JournalLayoutError(
                    f"{manifest_path(directory)} declares {row['name']!r} more "
                    f"than once — the second row would shadow the first, so a "
                    f"recorded departure could be overwritten with `present`")
            declared[row["name"]] = row

    journalled: dict[str, Path] = {}
    unplaceable: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.name.endswith(".jsonl"):
            continue
        # ⛔ NOT `is_file()`. `rebuild_all` filters on the NAME alone
        # (`endswith('.jsonl')`), so a DIRECTORY or FIFO called `sub.jsonl` is
        # something the replay will try to open — and a claim over a journal
        # whose replay raises `IsADirectoryError` is not a claim about anything.
        if (path.name == ACTIVE_NAME or SEALED_RE.match(path.name)) \
                and path.is_file():
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
        seg = _segment_from_path(
            path, disposition,
            recorded_bytes=row.get("bytes"),
            recorded_lines=row.get("lines"))
        found.append(dataclass_replace(seg, recorded_sha256=row.get("sha256")))
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
        # ⛔ The index comes from the NAME, never from the row. A row claiming
        # `"index": null` for `events.jsonl` used to be adopted as the ACTIVE
        # segment by name regardless of the row, so a DEPARTED row could stand in
        # for the live tail and anchor 3 never fired on a deleted tail.
        # Sealed-ness is a property of the name everywhere else in this module.
        index = int(SEALED_RE.match(name).group(1)) if SEALED_RE.match(name) else None
        try:
            rec_bytes = int(row["bytes"])
            rec_lines = int(row["lines"])
        except (TypeError, ValueError) as exc:
            raise JournalLayoutError(
                f"{manifest_path(directory)} has a non-numeric field on the "
                f"{name} row — refusing rather than guessing what it meant"
            ) from exc
        found.append(Segment(
            name=name,
            index=index,
            bytes=rec_bytes,
            lines=rec_lines,
            disposition=disposition,  # type: ignore[arg-type]
            on_disk=False,
            recorded_bytes=rec_bytes,
            recorded_lines=rec_lines,
            recorded_sha256=row.get("sha256"),
            recorded_at=row.get("recorded_at"),
        ))

    boundary = None
    if manifest is not None and manifest.get("boundary"):
        raw = manifest["boundary"]
        # A hand edit or a writer from another build can leave a field the
        # wrong TYPE. Coerce, and refuse with a named error rather than letting a
        # bare ValueError/TypeError escape from `summarise` — the one verb that
        # promises a report instead of a traceback.
        try:
            through = int(raw.get("through_index", -1))
        except (TypeError, ValueError) as exc:
            raise JournalLayoutError(
                f"{manifest_path(directory)} has a non-numeric "
                f"through_index ({raw.get('through_index')!r})") from exc
        boundary = Boundary(
            recorded_at=str(raw.get("recorded_at", "")),
            through_index=through,
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
        # (`recorded_*` is None only for the ACTIVE segment, which the manifest
        # does not describe — a DECLARED row is required to carry all three
        # anchors, so these guards can no longer be reached by a row that simply
        # omitted a field.)
        if seg.on_disk and seg.recorded_bytes is not None \
                and seg.bytes != seg.recorded_bytes:
            raise SegmentModifiedError(
                f"{seg.name} is {seg.bytes} B on disk but the manifest recorded "
                f"{seg.recorded_bytes} B at seal time — the segment was "
                f"rewritten in place. `record_disposition` cannot clear this (it "
                f"changes a disposition, never a size), and re-sealing is not an "
                f"operation this module has: the repair is to RESTORE the "
                f"segment's bytes. If the rewrite was deliberate, move the "
                f"segment out of {layout.directory} FIRST and then call "
                f"record_disposition({seg.name!r}, 'compacted'). A sealed segment "
                f"that GREW means a writer held an fd across the seal.")
        # ⛔ BYTES ALONE IS NOT ENOUGH. A record can be dropped and the file
        # padded back to the same length (trailing blanks are skipped by
        # `read_all` WITHOUT counting, so no damage counter fires): the claim
        # then reads "full" over a record stream short by one. Measured.
        if seg.on_disk and seg.recorded_lines is not None \
                and seg.lines != seg.recorded_lines:
            raise SegmentModifiedError(
                f"{seg.name} holds {seg.lines} lines on disk but the manifest "
                f"recorded {seg.recorded_lines} at seal time — records were "
                f"dropped or added in place, even if the byte count was padded "
                f"back to match.")
        # ⛔ IDENTITY. Size and line count are both preserved by a rewrite that
        # keeps them, so neither can see two equal-length sealed segments SWAPPED
        # (the replay order then inverts with the claim still "full") or a
        # same-length substitution. The digest can, and it costs one pass the
        # manifest already makes.
        # `is not None`, not truthiness: an empty string must FAIL the anchor,
        # not skip it (cycle 4).
        if seg.on_disk and seg.recorded_sha256 is not None \
                and seg.sha256 != seg.recorded_sha256:
            raise SegmentModifiedError(
                f"{seg.name} does not hash to what the manifest recorded at seal "
                f"time (on disk {str(seg.sha256)[:12]}…, recorded "
                f"{seg.recorded_sha256[:12]}…) — its CONTENT changed while its "
                f"size and line count did not. That is a same-length rewrite, or "
                f"two sealed segments swapped during a move. The bytes this "
                f"journal replays are not the bytes it sealed. Repair: restore "
                f"the segment's bytes from a copy taken at seal time.")

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
    if layout.boundary is not None and indices \
            and max(indices) != layout.boundary.through_index:
        # ⛔ AND THE OTHER SIDE (cycle 5, measured): a boundary BEYOND every row
        # was accepted, so a manifest whose rows for 0002…0009 were deleted
        # together with those segments certified `full=True, anchored=True` —
        # `through_index` is the only field recording how far the sequence went,
        # and it was never compared to the rows. All three writers maintain
        # `through_index == max(row index)`, so an inequality is a refusal.
        raise SegmentSequenceError(
            f"{MANIFEST_NAME} records through_index="
            f"{layout.boundary.through_index} but the highest row is "
            f"{max(indices)} — the boundary went further than the rows describe, "
            f"so rows (and their segments) were removed without a disposition")
    if layout.boundary is not None and indices \
            and min(indices) > 0 \
            and layout.boundary.first_missing != "events.0000.jsonl":
        # ⛔ ANCHOR 6 — THE ORIGIN. Contiguity is checked from the LOWEST index
        # present, so deleting `events.0000.jsonl` AND its row left a sequence
        # that was contiguous, had `first_missing=None`, and certified
        # `full=True, anchored=True` (cycle 4, measured). Nothing recorded that
        # the journal STARTED at zero. A journal whose first segment is not
        # `events.0000` must have a boundary that says 0000 left.
        raise SegmentSequenceError(
            f"the lowest sealed segment in {layout.directory} is "
            f"{min(indices):04d}, but {MANIFEST_NAME} does not record "
            f"events.0000.jsonl as the first missing segment — the journal's "
            f"ORIGIN is unaccounted for, so its earliest records may simply be "
            f"gone. Record the departure with "
            f"record_disposition('events.0000.jsonl', ...)")


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
        if not layout.manifest_present:
            return ReconstructionScope(
                full=True, first_missing=None, complete_from=None,
                anchored=False,
                reason=("no journal-manifest.json: nothing has been sealed, so "
                        "this is an UNANCHORED claim — it describes the files "
                        "present, and cannot show that nothing was lost; the "
                        "live tail is unanchored either way"))
        return ReconstructionScope(
            full=True, first_missing=None, complete_from=None,
            anchored=True,
            reason=("every SEALED segment the manifest records is present at its "
                    "recorded size, line count and digest. The live tail's bytes "
                    "are NOT anchored (no per-append length trail exists) — see "
                    "`tail_anchored`"))

    # `first_missing` is the LOWEST gap (where reproduction stops).
    missing = sorted(gone, key=lambda s: (-1 if s.index is None else s.index))[0]
    # ⛔ `complete_from` must start after the LAST gap, not after the first.
    # Measured: with 0001 and 0003 gone, a claim "complete from 0002 forward"
    # names a segment followed immediately by another hole — the honest claim is
    # the first PRESENT segment after the highest missing one, or nothing.
    last_gone = sorted(gone, key=lambda s: s.name)[-1]
    after = [s for s in layout.segments
             if s.on_disk and s.disposition == "present"
             and s.name > last_gone.name]
    complete_from = after[0].name if after else None
    dispositions = sorted({s.disposition if s.disposition != "present"
                           else "absent" for s in gone})
    return ReconstructionScope(
        full=False,
        first_missing=missing.name,
        complete_from=complete_from,
        anchored=True,
        reason=(f"{len(gone)} segment(s) are recorded as "
                f"{'/'.join(dispositions)}; the replay cannot reproduce what "
                f"they held; the live tail is unanchored in any case"))


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
    torn: list[str] = []
    for seg in layout.segments:
        if seg.disposition != "present" or not seg.on_disk:
            continue
        log = EventLog(layout.directory / seg.name)
        records.extend(read_for_replay(log))
        # ⛔ A TORN TAIL is not the same defect as MID-FILE damage, and `log.py`
        # treats them differently on purpose: a trailing fragment was never a
        # complete record (SIGKILL on the last append), where a skipped MID-FILE
        # line was a record the journal held and lost. Refusing the first would
        # make the reader unusable after any killed process — the contract
        # `tortoise/log.py::read_for_replay` documents.
        if getattr(log, "corrupt_line_count", 0):
            damaged.append(f"{seg.name}: {log.corrupt_line_count} mid-file line(s)")
        if getattr(log, "torn_trailing_count", 0):
            torn.append(f"{seg.name}: {log.torn_trailing_count} torn tail fragment(s)")
    if damaged and not salvage:
        raise JournalLayoutError(
            f"{layout.directory} has damaged records ({'; '.join(damaged)}) — "
            f"refusing to hand back a record stream that is silently short of "
            f"what the journal holds. Pass salvage=True to read what parses.")
    if torn and not salvage:
        _LOGGER.warning(
            "journal %s: reading across a torn tail (%s) — those fragments were "
            "never complete records", layout.directory, "; ".join(torn))
    return records


# --------------------------------------------------------------------------
# the writers (gated; rename and record, never delete)
# --------------------------------------------------------------------------


#: Paths THIS THREAD already holds the manifest lock on. `flock` is per
#: open-file-description, so a second `os.open` in the same process would not be
#: granted and a nested writer would BLOCK FOREVER — so the lock is made
#: re-entrant. ⛔ It is **per THREAD, not per process**: a process-global set made
#: a SECOND THREAD look like a re-entrant caller and skip the lock entirely,
#: which silently destroyed exclusion between threads (caught by
#: `test_concurrent_manifest_writers_do_not_lose_a_row`, which then lost a row).
_HELD_LOCKS = threading.local()

_LOGGER = logging.getLogger(__name__)


@contextlib.contextmanager
def _manifest_lock(directory: Path) -> Iterator[None]:
    """Serialise the writers' read-modify-write of the manifest.

    ``_write_manifest`` is atomic per FILE, which is not the same as atomic per
    TRANSACTION: two writers that both read, then both write, lose one of the two
    updates — measured as a sealed segment's row vanishing while the segment
    stayed on disk, which leaves the journal in a state no API could repair.

    ⚠️ **DECLARED RESIDUAL:** deleting ``.journal-manifest.lock`` while a writer
    holds it gives the next writer a different inode, so exclusion is lost. The
    dev/ino is re-validated after acquiring (a mismatch retries), which closes
    the common case but cannot close a deletion that happens after the check.
    """
    import fcntl

    if not directory.is_dir():
        raise JournalLayoutError(f"not a directory: {directory}")
    key = str(directory.resolve())
    held_here = getattr(_HELD_LOCKS, "paths", None)
    if held_here is None:
        held_here = _HELD_LOCKS.paths = set()
    if key in held_here:                        # re-entrant within this THREAD
        yield
        return

    lock_path = directory / LOCK_NAME
    for _ in range(5):
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                on_path = os.stat(lock_path)
            except FileNotFoundError:           # deleted while we waited
                continue                        # the `finally` closes fd — ONCE
            held = os.fstat(fd)
            if (on_path.st_dev, on_path.st_ino) != (held.st_dev, held.st_ino):
                continue                        # the lock file was replaced
            held_here.add(key)
            try:
                yield
            finally:
                held_here.discard(key)
                fcntl.flock(fd, fcntl.LOCK_UN)
            return
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
    raise JournalLayoutError(
        f"could not acquire {lock_path} after 5 attempts — the lock file keeps "
        f"being replaced while we wait")


def _write_manifest(graph_dir: Path, payload: dict) -> Path:
    """Write the manifest atomically — a half-written one must never be readable."""
    path = manifest_path(graph_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


#: The largest index a FOUR-digit name can carry. Beyond it the name grows a
#: digit and `sorted()` stops agreeing with the numeric order (anchor 4), so the
#: WRITER must refuse rather than emit a layout its own verifier rejects.
MAX_SEALABLE_INDEX = 9999


def _next_index(layout: Layout) -> int:
    next_index = max([s.index for s in layout.segments
                      if s.index is not None], default=-1) + 1
    if next_index > MAX_SEALABLE_INDEX:
        raise SegmentSequenceError(
            f"cannot seal {layout.directory}: the next index is {next_index}, "
            f"and a 5-digit name sorts BEFORE a 4-digit one, so the replay "
            f"would fold out of sequence. A journal that has sealed "
            f"{MAX_SEALABLE_INDEX + 1} segments has reached the end of this "
            f"naming scheme — that is a decision, not a rename.")
    return next_index


def _rows(layout: Layout) -> list[dict]:
    """The manifest rows for the sealed segments, in sequence order."""
    rows = []
    for seg in sorted((s for s in layout.segments if s.sealed),
                      key=lambda s: s.index if s.index is not None else -1):
        rows.append({"name": seg.name, "index": seg.index,
                     "bytes": seg.recorded_bytes if seg.recorded_bytes is not None
                     else seg.bytes,
                     "lines": seg.recorded_lines if seg.recorded_lines is not None
                     else seg.lines,
                     # ⛔ Keep the digest that was RECORDED at seal time, so a
                     # rewrite of the row does not launder a rewrite of the file.
                     # A fallback to the DISK digest here is how a tampered
                     # segment got re-blessed (cycle 4), so a missing recorded
                     # digest on a segment we are re-writing is a refusal.
                     "sha256": _recorded_sha(seg),
                     "disposition": seg.disposition,
                     "recorded_at": seg.recorded_at or _utcnow()})
    return rows


def _recorded_sha(seg: Segment) -> str:
    if seg.recorded_sha256 is not None:
        return seg.recorded_sha256
    if seg.on_disk and seg.sealed and seg.sha256 is not None:
        return seg.sha256                       # first seal of this segment
    raise JournalLayoutError(
        f"{seg.name} has no recorded sha256 and is not on disk — refusing to "
        f"write a row whose identity anchor is missing")


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
    if max_bytes <= 0:
        # `max_bytes=0` would seal an EMPTY active segment on every call: an
        # index consumed per iteration and a sealed segment holding nothing.
        raise ValueError(
            f"max_bytes must be positive (got {max_bytes}) — a zero threshold "
            f"mints an empty sealed segment per call")
    layout = discover(graph_dir)
    verify(layout)
    active = layout.active
    if active is None:
        return None
    if active.bytes < max_bytes or active.bytes == 0:
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

        # ⛔ MEASURE BEFORE THE RENAME. Cycle-3 finding: reading the size AFTER
        # the rename folds an append that landed in the rename→stat window into
        # the recorded number, so anchor 2 compares the grown file to the grown
        # size and BLESSES the extra record (which then replays before anything
        # written to the new active segment). Taken before the rename, any such
        # append is a genuine mismatch — which is what the stale-append-handle
        # residual promises.
        # ⛔ ONE read, one anchor set, under the journal's own append lock — see
        # `_measure`. This used to be `stat()` + a separate `_digest()`, which a
        # concurrent appender could split into an unsatisfiable row.
        sealed_bytes, sealed_lines, sealed_sha = _measure(
            source, lock=True, strict=True)

        source.rename(target)
        # ANCHOR 3: the live tail exists from the moment the journal is sealed.
        (directory / ACTIVE_NAME).touch()

        rows = _rows(layout)
        rows.append({"name": sealed_name, "index": next_index,
                     "bytes": sealed_bytes,
                     "lines": sealed_lines,
                     "sha256": sealed_sha,
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
        # ⛔ NOT `discover()` here. The intended order is MOVE THE BYTES, THEN
        # RECORD — so at the moment this runs the manifest is stale by design (a
        # `present` row whose file has gone), and a full verify would refuse the
        # one state this call exists to make recordable. `read_manifest`
        # validates the format, the version and the boundary object, which is
        # what a writer needs.
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


def adopt_crashed_seal(graph_dir: str | Path) -> Path:
    """Record a segment that a crash left sealed but unmanifested. **Nothing else.**

    The state: `seal_active` renamed `events.jsonl` → `events.<N>.jsonl` and died
    before writing the manifest. The bytes are intact, but anchor 1 refuses the
    directory — correctly, because a sealed segment nobody recorded is ALSO what
    a dropped segment looks like. This call accepts it only when the directory
    matches the crash signature and NOTHING else:

    * a manifest exists (a journal that never sealed has no crash to repair);
    * exactly ONE on-disk sealed segment is missing from the manifest, and its
      index is ``max(recorded index) + 1`` — a gap further back is a real loss,
      not a crash;
    * the live tail is ABSENT — `seal_active` creates it immediately after the
      rename, so its presence means the writer got past the point this repairs.

    ⛔ Each condition narrows a way this could launder a loss into "present". It
    appends one row (bytes, lines, digest, measured from disk) and advances the
    boundary; it renames nothing, deletes nothing, and touches no journal byte.
    Gated on :data:`ENV_FLAG` like the other writers: adopting a seal is deciding
    a boundary, which is the policy act the flag guards.
    """
    if not enabled():
        raise SegmentationDisabled(
            f"{ENV_FLAG} is not '1' — refusing to adopt a crashed seal in "
            f"{graph_dir}")
    directory = Path(graph_dir)
    with _manifest_lock(directory):
        payload = read_manifest(directory)
        if payload is None:
            raise JournalLayoutError(
                f"{directory} has no {MANIFEST_NAME} — a crash between a rename "
                f"and a manifest write needs a manifest to have been mid-write")
        recorded = {row["name"] for row in payload["segments"]}
        # ⛔ `lstat`/`S_ISREG`, not `is_file()`: `is_file()` FOLLOWS symlinks, so
        # `events.0001.jsonl -> ../other-graph/events.0000.jsonl` was adopted and
        # certified `full=True` over ANOTHER graph's bytes (cycle 4, measured).
        # Where a segment's name points is part of what the manifest describes.
        on_disk_sealed = sorted(
            p.name for p in directory.iterdir()
            if SEALED_RE.match(p.name) and stat.S_ISREG(p.lstat().st_mode))
        unrecorded = [n for n in on_disk_sealed if n not in recorded]
        if len(unrecorded) != 1:
            raise SegmentSequenceError(
                f"expected exactly ONE sealed segment missing from "
                f"{MANIFEST_NAME} (the crash signature), found {unrecorded} — "
                f"this is not a crashed seal, and adopting it here would record "
                f"a segment nobody sealed")
        name = unrecorded[0]
        index = int(SEALED_RE.match(name).group(1))
        highest = max((int(SEALED_RE.match(r).group(1))
                       for r in recorded if SEALED_RE.match(r)), default=-1)
        if index != highest + 1:
            raise SegmentSequenceError(
                f"{name} is index {index} but the manifest's highest is "
                f"{highest} — the missing segment is behind the boundary, which "
                f"is a LOSS, not a crash, and cannot be adopted")
        # ⛔ TWO windows, and they differ by the TAIL'S SIZE. `seal_active` is
        # rename → touch(tail) → write(manifest). A crash before the touch leaves
        # NO tail; a crash after it leaves an EMPTY one. Treating any tail as
        # "past the repair" (cycle 4) meant the second window — the one a failing
        # `_write_manifest` actually produces — had no repair verb at all. A tail
        # with BYTES is a different thing: an appender got in.
        tail = directory / ACTIVE_NAME
        if os.path.lexists(tail):
            if tail.is_symlink():
                raise SegmentSequenceError(
                    f"{tail} is a SYMLINK — refusing to adopt around it, because "
                    f"settling the tail would create a file wherever it points")
            if tail.stat().st_size:
                raise SegmentSequenceError(
                    f"{directory} holds a NON-EMPTY {ACTIVE_NAME} — records were "
                    f"appended after the seal, so this is a live journal, not "
                    f"the crash signature")

        path = directory / name
        # ⛔ One read, all three anchors (see `_measure`), and the bytes must be
        # REPLAYABLE: a segment of garbage adopted as `present` would pass
        # `verify` and certify `full=True` while `read_all_segments` refuses the
        # same layout (cycle 4, measured).
        sealed_bytes, lines, sha = _measure(path, strict=True)
        _assert_adoptable_records(path)
        payload["segments"].append({
            "name": name, "index": index, "bytes": sealed_bytes,
            "lines": lines, "sha256": sha, "disposition": "present",
            "recorded_at": _utcnow()})
        for row in payload["segments"]:
            if "index" not in row:
                raise JournalLayoutError(
                    f"{MANIFEST_NAME} row {row.get('name')!r} has no `index` — "
                    f"refusing to guess where it sits in the sequence")
            payload["boundary"].setdefault("through_index", 0)
        payload["segments"].sort(key=lambda r: r["index"] if r["index"] is not None
                                 else -1)
        payload["boundary"]["through_index"] = max(
            int(payload["boundary"]["through_index"]), index)
        payload["boundary"]["first_missing"] = _first_missing(payload["segments"])
        # ⛔ EVERY REFUSAL BEFORE EVERY WRITE. Cycle 4 measured the alternative:
        # the row was appended and the manifest written, and THEN the tail
        # settlement refused — leaving a half-repaired directory whose signature
        # (one unrecorded segment) was gone, so the repair could not be retried.
        # The tail is settled first: it can only refuse, and a crash after it is
        # the window this verb already handles.
        _open_tail_exclusively(directory / ACTIVE_NAME)
        return _write_manifest(directory, payload)


def _assert_adoptable_records(path: Path) -> None:
    """Refuse a segment whose records do not replay (a TORN tail stays fine).

    `log.py` distinguishes a trailing fragment — never a complete record, the
    SIGKILL-during-append signature — from a skipped MID-FILE line, which is a
    record the journal held and lost. Adoption must make the same distinction:
    admitting the second certifies a full claim over a layout its own reader
    refuses.
    """
    data = path.read_bytes()
    body = data if data.endswith(b"\n") else data[: data.rfind(b"\n") + 1]
    for number, raw in enumerate(body.split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            json.loads(raw)
        except ValueError as exc:
            raise JournalLayoutError(
                f"{path.name} line {number} is not a record ({exc}) — refusing "
                f"to adopt a segment whose bytes cannot be replayed") from exc


def _open_tail_exclusively(path: Path) -> None:
    """Create the empty tail, REFUSING a symlink at that name.

    `Path.touch()` follows a symlink and would create the file wherever it
    points — outside the graph directory — while this module promises to write
    nothing but the manifest (cycle 4, measured with a dangling link).
    """
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except FileExistsError:
        if path.is_symlink():
            raise SegmentSequenceError(
                f"{path} is a symlink — refusing to treat it as the live tail"
            ) from None
        if path.stat().st_size:
            raise SegmentSequenceError(
                f"{path} is not empty — refusing to settle it as a fresh tail"
            ) from None


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
            "departed_bytes": None,
            "segments": [],
            "verified": False,
            "full_reconstruction": False,
            "anchored": False,
            "tail_anchored": False,
            "first_missing": None,
            "from_segment": None,
            "reason": str(exc),
        }

    report: dict = {
        "directory": str(layout.directory),
        "manifest_present": layout.manifest_present,
        "total_bytes": layout.total_bytes,
        "departed_bytes": layout.departed_bytes,
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
        report["anchored"] = scope.anchored
        report["tail_anchored"] = scope.tail_anchored
        report["first_missing"] = scope.first_missing
        report["from_segment"] = scope.complete_from
        report["reason"] = scope.reason
    except JournalLayoutError as exc:
        report["verified"] = False
        report["full_reconstruction"] = False
        report["anchored"] = False
        report["tail_anchored"] = False
        report["first_missing"] = None
        report["from_segment"] = None
        report["reason"] = str(exc)
    return report


def segment_names(graph_dir: str | Path) -> Iterable[str]:
    """The replay-ordered names the journal consists of (read-only)."""
    return (s.name for s in discover(graph_dir).segments)
