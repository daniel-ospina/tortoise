"""#5062 — one verified per-object backup ledger: coverage is READ BACK.

The DR path's invariant is that a backup's coverage is a value **read back from
the destination**, never a number the writing path produced about itself. Before
this module the write path uploaded ``dump.enc`` + ``manifest.json``, recorded a
``sha256`` computed from the **in-memory** blob, returned, and the sweep reported
``status: backed_up`` from that return value — so a store that dropped the write,
a dump that was truncated at FalkorDB's server-global ``RESULTSET_SIZE``, and a
corrupt object all read green (#4991, #4515: both folded into #5062).

This is the single home for:

* **the object set of one backup** (:data:`BACKUP_OBJECT_SUFFIXES`) — the write
  path, the read-back and the delete paths all use it, so no orphan ledger can
  survive a rolled-back backup and no suffix can be verified by one path and
  forgotten by another;
* **the ledger document** (:func:`build_ledger`, :func:`serialize_ledger`) —
  per object: key, bytes, sha256, written-at, plus the **store's** independent
  source counts and the dump's own counts;
* **the read-back** (:func:`verify_ledger`) — downloads every named object from
  the destination, recomputes its sha256 + length, and classifies it. Objects the
  destination holds but the ledger never named are ``unreported``: a **third
  state**, distinct from ``verified`` and from any failure, that can never be
  read as coverage.

``verify_ledger`` also re-reads the ledger document itself and confirms it is
byte-identical to the canonically serialized copy, so a store that corrupts the
ledger is caught too.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable  # noqa: UP035

logger = logging.getLogger(__name__)

#: Ledger document schema version.
LEDGER_FORMAT = "backup-ledger/1"

#: The COMPLETE object set of one backup. ONE home (#5062): the write path
#: writes exactly these, the read-back verifies exactly these, and the delete
#: paths (prune, rollback) remove exactly these. Adding an object to a backup
#: without adding it here would leave a suffix the read-back never covers and
#: the prune never collects.
BACKUP_OBJECT_SUFFIXES: tuple[str, ...] = ("dump.enc", "manifest.json", "ledger.json")

#: Suffix of the ledger document itself.
LEDGER_SUFFIX = "ledger.json"

# ── Per-object verification states ──────────────────────────────────────────
VERIFIED = "verified"
MISSING = "missing"
MISMATCH = "mismatch"
UNREADABLE = "unreadable"
#: A third state: the destination holds an object the ledger never named. It is
#: neither verified nor failed — it is coverage the writer did not record, and
#: it can never be counted as either.
UNREPORTED = "unreported"

#: Coverage states.
COVERAGE_VERIFIED = "verified"
COVERAGE_FAILED = "failed"


class BackupVerificationError(RuntimeError):
    """The destination contradicts the backup's own ledger (fail-closed)."""


@dataclass(frozen=True)
class LedgerObject:
    """One object the writing path claims to have written."""

    key: str
    bytes: int
    sha256: str
    written_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "written_at": self.written_at,
        }


@dataclass(frozen=True)
class ObjectVerification:
    """The destination's answer for one ledger object."""

    key: str
    state: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "state": self.state}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class LedgerVerification:
    """Coverage as computed from the destination, never from a writer counter."""

    ledger_key: str
    objects: tuple[ObjectVerification, ...]
    unreported: tuple[str, ...]
    source_node_count: int | None
    dump_node_count: int | None
    source_edge_count: int | None
    dump_edge_count: int | None
    read_edge_count: int | None
    shortfall: dict[str, Any] | None

    @property
    def state(self) -> str:
        if self.shortfall is not None:
            return COVERAGE_FAILED
        if self.unreported:
            return COVERAGE_FAILED
        if any(o.state != VERIFIED for o in self.objects):
            return COVERAGE_FAILED
        if not self.objects:  # a ledger naming nothing covers nothing
            return COVERAGE_FAILED
        return COVERAGE_VERIFIED

    @property
    def ok(self) -> bool:
        return self.state == COVERAGE_VERIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": LEDGER_FORMAT,
            "state": self.state,
            "ledger_key": self.ledger_key,
            "objects": [o.as_dict() for o in self.objects],
            "unreported": list(self.unreported),
            "source_node_count": self.source_node_count,
            "dump_node_count": self.dump_node_count,
            "source_edge_count": self.source_edge_count,
            "dump_edge_count": self.dump_edge_count,
            "read_edge_count": self.read_edge_count,
            "shortfall": self.shortfall,
        }

    def failures(self) -> list[ObjectVerification]:
        return [o for o in self.objects if o.state != VERIFIED]

    def summary(self) -> str:
        parts = [f"coverage={self.state}"]
        bad = self.failures()
        if bad:
            parts.append(
                "objects=" + ", ".join(f"{o.key}:{o.state}" for o in bad)
            )
        if self.unreported:
            parts.append("unreported=" + ", ".join(self.unreported))
        if self.shortfall:
            parts.append(f"shortfall={self.shortfall}")
        return "; ".join(parts)


def backup_prefix(backup_id: str) -> str:
    """The storage prefix holding one backup's objects."""
    return f"backups/{backup_id}/"


def build_ledger(
    *,
    backup_id: str,
    org_id: str,
    graph_name: str,
    self_key: str,
    created_at: str,
    written_at: str,
    objects: Iterable[LedgerObject],
    source_node_count: int | None,
    source_edge_count: int | None,
    dump_node_count: int | None,
    dump_edge_count: int | None,
    read_edge_count: int | None = None,
) -> dict[str, Any]:
    """Build the plaintext per-object ledger for one backup.

    ``source_*`` are the **store's** independent counts (a cap-immune aggregate
    read from the graph, #4515); ``dump_*`` are the artifact's own counts. Both
    are recorded so the read-back can prove the artifact is not short of the
    store — the artifact's own number is never trusted on its own.
    """
    return {
        "format": LEDGER_FORMAT,
        "backup_id": backup_id,
        "org_id": org_id,
        "graph_name": graph_name,
        "self_key": self_key,
        "created_at": created_at,
        "written_at": written_at,
        "source": {
            "node_count": source_node_count,
            "edge_count": source_edge_count,
        },
        "dump": {
            "node_count": dump_node_count,
            "edge_count": dump_edge_count,
            "read_edge_count": read_edge_count,
        },
        "objects": [o.as_dict() for o in objects],
    }


def serialize_ledger(ledger: dict[str, Any]) -> bytes:
    """Canonical serialization — ONE function, so the read-back comparison can
    never disagree with the write about how the ledger is encoded."""
    return json.dumps(ledger, indent=2, sort_keys=True).encode("utf-8")


def _verify_object(storage, obj: dict[str, Any]) -> ObjectVerification:
    key = str(obj.get("key") or "")
    if not key:
        return ObjectVerification(key, UNREADABLE, "ledger object has no key")
    try:
        data = storage.download(key)
    except KeyError as e:
        return ObjectVerification(key, MISSING, f"KeyError: {e}")
    except Exception as e:
        return ObjectVerification(key, UNREADABLE, f"{type(e).__name__}: {e}")
    try:
        expected_bytes = int(obj.get("bytes"))
    except (TypeError, ValueError):
        expected_bytes = None
    if expected_bytes is not None and len(data) != expected_bytes:
        return ObjectVerification(
            key, MISMATCH,
            f"byte length read back {len(data)} != ledger {expected_bytes}",
        )
    actual = hashlib.sha256(data).hexdigest()
    expected_sha = str(obj.get("sha256") or "")
    if not expected_sha or actual != expected_sha:
        return ObjectVerification(
            key, MISMATCH, f"sha256 read back {actual} != ledger {expected_sha or '<missing>'}",
        )
    return ObjectVerification(key, VERIFIED)


def verify_ledger(storage, ledger: dict[str, Any]) -> LedgerVerification:
    """Compute coverage by READING THE DESTINATION back.

    Every object the ledger names is downloaded and its bytes hashed. The
    ledger document itself is re-read and compared to the canonical
    serialization. Objects under the backup prefix that the ledger never named
    are reported ``unreported`` (a distinct third state). The artifact's counts
    are compared against the store-read source counts; a shortfall (no source
    count at all, or a dump smaller than the store's count) fails coverage.
    """
    self_key = str(ledger.get("self_key") or "")
    raw_objects = ledger.get("objects")
    objects: list[dict[str, Any]] = list(raw_objects) if isinstance(raw_objects, list) else []

    verifications: list[ObjectVerification] = []
    named: set[str] = set()
    for obj in objects:
        if not isinstance(obj, dict):
            verifications.append(ObjectVerification("", UNREADABLE, "ledger object is not a mapping"))
            continue
        v = _verify_object(storage, obj)
        verifications.append(v)
        named.add(v.key)

    # The ledger itself is read back too — a store that corrupts the index must
    # not be able to verify the objects it indexes.
    if self_key and self_key not in named:
        self_v = _verify_object(storage, {
            "key": self_key,
            "bytes": len(serialize_ledger(ledger)),
            "sha256": hashlib.sha256(serialize_ledger(ledger)).hexdigest(),
        })
        verifications.append(self_v)
        named.add(self_key)

    # Objects at the destination the ledger never named: coverage the writer did
    # not record. A distinct third state — never verified, never a plain failure.
    unreported: list[str] = []
    prefix = self_key.rsplit("/", 1)[0] + "/" if "/" in self_key else ""
    if prefix:
        try:
            listed = storage.list(prefix)
        except Exception as e:
            # A listing we could not complete cannot certify the absence of
            # unreported objects — fail closed rather than assume none.
            logger.warning("ledger read-back could not list %s: %s", prefix, e)
            listed = []
            unreported.append(f"<list-failed:{prefix}>")
        for key in listed:
            if key not in named:
                unreported.append(key)

    source = ledger.get("source") or {}
    dump_counts = ledger.get("dump") or {}
    source_nodes = _as_int(source.get("node_count"))
    source_edges = _as_int(source.get("edge_count"))
    dump_nodes = _as_int(dump_counts.get("node_count"))
    dump_edges = _as_int(dump_counts.get("edge_count"))
    # The edge READ total, not the restorable edge_count: edges incident to
    # export-skipped bookkeeping nodes are dropped and counted (#3895), so the
    # restorable count is legitimately below the store's edge count.
    read_edges = _as_int(dump_counts.get("read_edge_count"))

    shortfall: dict[str, Any] | None = None
    if source_nodes is None or source_edges is None:
        shortfall = {"reason": "source counts not reported — the store's own count was never read"}
    elif dump_nodes is None or read_edges is None:
        shortfall = {"reason": "dump counts missing from the ledger"}
    elif dump_nodes < source_nodes:
        shortfall = {
            "reason": "dump is short of the store's independent node count (#4515)",
            "source_node_count": source_nodes,
            "dump_node_count": dump_nodes,
        }
    elif read_edges < source_edges:
        shortfall = {
            "reason": "dump is short of the store's independent edge count",
            "source_edge_count": source_edges,
            "read_edge_count": read_edges,
        }

    return LedgerVerification(
        ledger_key=self_key,
        objects=tuple(verifications),
        unreported=tuple(unreported),
        source_node_count=source_nodes,
        dump_node_count=dump_nodes,
        source_edge_count=source_edges,
        dump_edge_count=dump_edges,
        read_edge_count=read_edges,
        shortfall=shortfall,
    )


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def delete_backup_objects(storage, backup_id: str, *, best_effort: bool = True) -> bool:
    """Delete the COMPLETE object set of one backup (all suffixes).

    Returns True when every delete succeeded. A failed delete is logged and, when
    ``best_effort``, never raised — the prune path is already best-effort and
    retries on a later run. The value of routing every caller through here is
    that a rolled-back backup can never leave an orphan ``ledger.json`` behind,
    which a later sweep could read as coverage.
    """
    ok = True
    for suffix in BACKUP_OBJECT_SUFFIXES:
        key = backup_prefix(backup_id) + suffix
        try:
            storage.delete(key)
        except Exception as e:
            ok = False
            logger.warning("delete of %s failed: %s", key, e)
            if not best_effort:
                raise
    return ok


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
