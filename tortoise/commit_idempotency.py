""":CommitRecord graph store — the L1 replay + PL3 adjudication record.

Slice 5a of epic #909: the :CommitRecord node (plan §4.1, §3.3) is the
idempotency/adjudication state for POST /v1/sessions/commit. One node per
commit attempt; MERGE on ``client_commit_id`` is the atomic concurrency
serialization point — the loser of a concurrent identical commit sees the
winner's record (created=False) → duplicate (DE2E-7 negative case a).

This module owns ONLY the record store (graph reads/writes via the tenant
SDK projection). The pure adjudication logic (L1 replay predicates, L2
reconciliation, budget) lives in ``commit_schema.py`` — the shared contract
module both the local extractor and the endpoint import. The endpoint
handler (#953, slice 5b) drives both together.

:CommitRecord fields (plan §4.1): client_commit_id (UNIQUE, MERGE key),
session_id, commit_id (= client_commit_id), status fully_written|held|
partial, written_at, write_ops_billed, value_nodes_declared. Division of
labor: the record carries per-commit adjudication state + billing; Session
counters carry the budget numerator (value_nodes_held lives on the Session,
NOT on the record — no held_point_ids[] on :CommitRecord).

``value_nodes_declared`` (#4704) is the record's OWN declaration, not a
second numerator: the write phase applies the payload's net-new to
``Session.value_nodes_created`` exactly once and stamps the amount here in
the SAME statement, so a retry of a partially-written payload neither
re-applies it nor re-counts a remainder that its own declaration already
covers (`plan_commit`). It is the counter-half twin of
``write_ops_billed`` (PL4: one logical payload billed exactly once).
NULL = not declared yet.
"""
from __future__ import annotations  # noqa: I001

import time
from typing import Any

from .commit_schema import (
    CommitRecordState,
    VALID_COMMIT_STATUSES,
)
from .ids import now_iso

__all__ = ["CommitRecordStore"]

_MERGE_CYPHER = """
MERGE (r:CommitRecord {client_commit_id: $cid})
ON CREATE SET r.session_id = $sid,
              r.status = $status,
              r.write_ops_billed = $wob,
              r.written_at = $ts,
              r._acquire_ts = $acquire
RETURN r._acquire_ts AS acquire_ts, r.status AS status,
       r.session_id AS sid, r.write_ops_billed AS wob,
       r.written_at AS ts, r.value_nodes_declared AS vnd
"""

_GET_CYPHER = """
MATCH (r:CommitRecord {client_commit_id: $cid})
RETURN r.session_id AS sid, r.status AS status,
       r.write_ops_billed AS wob, r.written_at AS ts,
       r.value_nodes_declared AS vnd
LIMIT 1
"""

_UPDATE_CYPHER = """
MATCH (r:CommitRecord {client_commit_id: $cid})
SET r.status = $status
RETURN r.session_id AS sid, r.status AS status,
       r.write_ops_billed AS wob, r.written_at AS ts,
       r.value_nodes_declared AS vnd
"""


class CommitRecordStore:
    """Graph-backed :CommitRecord store (tenant graph via the SDK).

    Args:
        sdk: a tenant ``TortoiseSDK`` (the endpoint passes the org-scoped
            SDK — the record lives in the existing tenant graph, §5.3).
    """

    def __init__(self, sdk: Any):
        self._proj = sdk._get_proj()

    # ── Reads ─────────────────────────────────────────────────────────

    def get(self, client_commit_id: str) -> CommitRecordState | None:
        """Look up a :CommitRecord by client_commit_id (None if absent)."""
        rows = self._proj.g.query(
            _GET_CYPHER, params={"cid": client_commit_id},
        ).result_set
        if not rows:
            return None
        sid, status, wob, ts, vnd = rows[0]
        return CommitRecordState(
            client_commit_id=client_commit_id,
            session_id=sid or "",
            status=status,
            write_ops_billed=wob or 0,
            written_at=ts,
            value_nodes_declared=vnd,
        )

    # ── Atomic serialization point ────────────────────────────────────

    def acquire(
        self,
        client_commit_id: str,
        *,
        session_id: str,
        status: str,
        write_ops_billed: int = 0,
    ) -> tuple[CommitRecordState, bool]:
        """Atomic MERGE on client_commit_id — the concurrency serialization
        point (W-3 [2]).

        Returns (record, created): created=True when THIS call created the
        record (it won the MERGE — the commit proceeds); created=False when
        the record already existed (a concurrent identical commit won — the
        loser sees the winner's record → duplicate:true, DE2E-7 neg a).

        Raises ValueError on an invalid status (fail-fast, never writes a
        corrupt record).
        """
        if status not in VALID_COMMIT_STATUSES:
            raise ValueError(
                f"invalid :CommitRecord status {status!r} — must be one of "
                f"{VALID_COMMIT_STATUSES}")
        ts = now_iso()
        acquire_ns = time.time_ns()
        rows = self._proj.g.query(
            _MERGE_CYPHER,
            params={
                "cid": client_commit_id,
                "sid": session_id,
                "status": status,
                "wob": write_ops_billed,
                "ts": ts,
                "acquire": acquire_ns,
            },
        ).result_set
        acquire_ts, rec_status, sid, wob, written_at, vnd = rows[0]
        created = (acquire_ts == acquire_ns)
        return (
            CommitRecordState(
                client_commit_id=client_commit_id,
                session_id=sid or session_id,
                status=rec_status,
                write_ops_billed=wob or 0,
                written_at=written_at,
                value_nodes_declared=vnd,
            ),
            created,
        )

    # ── Transitions (re-submission path, PL3) ─────────────────────────

    def discard_attempt(self, client_commit_id: str) -> bool:
        """Delete a record THIS attempt created, before any write (#4051).

        A refusal that happens after ``acquire`` leaves a ``status='partial'``
        record behind unless it cleans up after itself. That is NOT a neutral
        leftover: ``plan_commit``/``is_first_adjudication`` treat ANY existing
        record as a previous attempt, so the retry of the very same
        ``client_commit_id`` is re-adjudicated WITHOUT the first-adjudication
        hard-25 band and writes the items the original attempt would have HELD
        (measured: a 26-net-new payload 200s with ``held`` 26 on a clean graph,
        and WRITES them after a refusal left the record).

        The 429 ingest throttle calls this; the 402 org-limit gates still do
        not, which is #9826 — so a 402-then-retry still writes what a first
        adjudication would have held.

        Deleting restores the pre-attempt state exactly, which is what the
        refusal promised: nothing was written, so the client's documented
        backoff-then-retry (``docs/INGEST_CONTRACT.md`` §9) is a FIRST
        adjudication, not a second one.

        Refuses to delete anything carrying write work, so a genuine mid-write
        crash (the PL3 re-submission path) is untouched. The load-bearing limb
        is ``value_nodes_declared IS NULL``: the write phase stamps that on the
        record at its FIRST statement (``hosted_api`` step 1 — "the stamp and
        the arithmetic are one statement"), so a crash leaves it SET, while
        ``write_ops_billed`` is only written AFTER the phase completes and is
        therefore still 0 on a mid-write record (``acquire`` writes the column as
        ``0`` when it creates the record; the ``1`` lands only after the phase
        completes: ``store.update(... "fully_written")`` then
        ``SET r.write_ops_billed=1``). Both limbs are checked (belt
        and braces), but do NOT simplify the guard to ``status``/``wob`` alone —
        that would delete a live in-flight record.

        Returns True when a record was deleted.
        """
        rows = self._proj.g.query(
            "MATCH (r:CommitRecord {client_commit_id:$cid}) "
            "WHERE r.status = 'partial' "
            "  AND coalesce(r.write_ops_billed, 0) = 0 "
            "  AND r.value_nodes_declared IS NULL "
            "DELETE r RETURN count(*) AS n",
            params={"cid": client_commit_id},
        ).result_set
        return bool(rows and int(rows[0][0] or 0) > 0)

    def update(
        self, client_commit_id: str, *, status: str
    ) -> CommitRecordState | None:
        """Transition a record's status (held|partial → fully_written on
        re-submission, PL3; held → held on a ceiling-exceeded re-submission).

        Returns the updated record, or None if no record exists.
        """
        if status not in VALID_COMMIT_STATUSES:
            raise ValueError(
                f"invalid :CommitRecord status {status!r} — must be one of "
                f"{VALID_COMMIT_STATUSES}")
        rows = self._proj.g.query(
            _UPDATE_CYPHER,
            params={"cid": client_commit_id, "status": status},
        ).result_set
        if not rows:
            return None
        sid, rec_status, wob, ts, vnd = rows[0]
        return CommitRecordState(
            client_commit_id=client_commit_id,
            session_id=sid or "",
            status=rec_status,
            write_ops_billed=wob or 0,
            written_at=ts,
            value_nodes_declared=vnd,
        )
